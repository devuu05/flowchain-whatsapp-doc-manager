"""Login, server-side sessions, and login throttling."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotAuthenticated, TooManyRequests
from app.db.base import utcnow
from app.models import LoginAttempt, User, UserSession
from app.security.crypto import hmac_hex, new_token, sha256_hex
from app.security.passwords import hash_password, needs_rehash, verify_password
from app.services import audit


@dataclass
class IssuedSession:
    token: str
    session: UserSession
    csrf_token: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def csrf_for(session: UserSession) -> str:
    return hmac_hex("csrf", session.token_hash)


def _check_throttle(db: Session, email: str) -> None:
    s = get_settings()
    since = utcnow() - timedelta(minutes=s.login_lockout_minutes)
    failures = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.email == email,
            LoginAttempt.success.is_(False),
            LoginAttempt.created_at >= since,
        )
    )
    if (failures or 0) >= s.login_max_failures:
        raise TooManyRequests("Too many failed sign-in attempts. Try again later.")


def authenticate(
    db: Session, email: str, password: str, *, ip: str | None, user_agent: str | None
) -> IssuedSession:
    email = normalize_email(email)
    _check_throttle(db, email)
    user = db.scalar(select(User).where(User.email == email))
    ok = verify_password(user.password_hash if user else None, password)
    if not ok or user is None or not user.is_active:
        db.add(LoginAttempt(email=email, ip=ip, success=False, created_at=utcnow()))
        audit.record(db, "auth.login_failed", actor_type="user", ip=ip, details={"email": email})
        db.commit()
        raise NotAuthenticated("Invalid email or password")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = utcnow()
    db.add(LoginAttempt(email=email, ip=ip, success=True, created_at=utcnow()))
    issued = create_session(db, user, ip=ip, user_agent=user_agent)
    audit.record(db, "auth.login", actor_type="user", actor_id=user.id, ip=ip)
    db.commit()
    return issued


def create_session(
    db: Session, user: User, *, ip: str | None, user_agent: str | None
) -> IssuedSession:
    s = get_settings()
    token = new_token(32)
    now = utcnow()
    sess = UserSession(
        user_id=user.id,
        token_hash=sha256_hex(token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=s.session_absolute_hours),
        ip=ip,
        user_agent=(user_agent or "")[:300],
    )
    db.add(sess)
    db.flush()
    return IssuedSession(token=token, session=sess, csrf_token=csrf_for(sess))


def resolve_session(db: Session, token: str | None) -> tuple[User, UserSession]:
    if not token or len(token) > 200:
        raise NotAuthenticated()
    s = get_settings()
    now = utcnow()
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == sha256_hex(token)))
    if (
        sess is None
        or sess.revoked_at is not None
        or sess.expires_at <= now
        or sess.last_seen_at + timedelta(minutes=s.session_idle_minutes) <= now
    ):
        raise NotAuthenticated("Session expired")
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise NotAuthenticated()
    # Throttle last_seen writes to once a minute.
    if now - sess.last_seen_at > timedelta(minutes=1):
        sess.last_seen_at = now
        db.commit()
    return user, sess


def revoke_session(db: Session, sess: UserSession) -> None:
    sess.revoked_at = utcnow()
    db.commit()


def revoke_all_sessions(db: Session, user_id: uuid.UUID) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )

"""Dashboard authentication: login, logout, current user, password change."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.api.deps import DB, Auth, client_ip
from app.core.config import get_settings
from app.core.errors import AppError, NotAuthenticated
from app.models import Tenant, TenantMembership
from app.schemas.api import LoginIn, MembershipOut, MeOut, PasswordChangeIn
from app.security.passwords import hash_password, validate_password_strength, verify_password
from app.services import audit
from app.services.auth import authenticate, create_session, csrf_for, revoke_all_sessions

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.session_cookie_name,
        token,
        max_age=s.session_absolute_hours * 3600,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )


def _me(db: DB, auth_user_id: object, csrf: str) -> MeOut:
    from app.models import User

    user = db.get(User, auth_user_id)
    assert user is not None
    rows = db.execute(
        select(TenantMembership, Tenant)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(TenantMembership.user_id == user.id)
        .order_by(Tenant.name)
    ).all()
    memberships = [MembershipOut(tenant_id=t.id, tenant_name=t.name, role=m.role) for m, t in rows]
    if user.is_platform_admin:
        member_ids = {m.tenant_id for m in memberships}
        for t in db.scalars(select(Tenant).order_by(Tenant.name)):
            if t.id not in member_ids:
                memberships.append(MembershipOut(tenant_id=t.id, tenant_name=t.name, role="owner"))
    return MeOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_platform_admin=user.is_platform_admin,
        csrf_token=csrf,
        memberships=memberships,
    )


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, db: DB) -> MeOut:
    issued = authenticate(
        db,
        body.email,
        body.password,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_cookie(response, issued.token)
    return _me(db, issued.session.user_id, issued.csrf_token)


@router.post("/logout", status_code=204)
def logout(auth: Auth, response: Response, db: DB) -> Response:
    from app.services.auth import revoke_session

    sess = db.merge(auth.session)
    revoke_session(db, sess)
    audit.record(db, "auth.logout", actor_type="user", actor_id=auth.user.id, ip=auth.ip)
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return response


@router.get("/me", response_model=MeOut)
def me(auth: Auth, db: DB) -> MeOut:
    return _me(db, auth.user.id, csrf_for(auth.session))


@router.post("/password", response_model=MeOut)
def change_password(
    body: PasswordChangeIn, auth: Auth, request: Request, response: Response, db: DB
) -> MeOut:
    user = db.merge(auth.user)
    if not verify_password(user.password_hash, body.current_password):
        raise NotAuthenticated("Current password is incorrect")
    if problem := validate_password_strength(body.new_password):
        raise AppError(problem, code="weak_password")
    user.password_hash = hash_password(body.new_password)
    revoke_all_sessions(db, user.id)
    issued = create_session(
        db, user, ip=client_ip(request), user_agent=request.headers.get("user-agent")
    )
    audit.record(db, "auth.password_changed", actor_type="user", actor_id=user.id, ip=auth.ip)
    db.commit()
    _set_cookie(response, issued.token)
    return _me(db, user.id, issued.csrf_token)

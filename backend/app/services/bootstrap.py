"""One-time, idempotent bootstrap from environment variables.

For hosts without shell access (e.g. Render free). On API start, if BOOTSTRAP_ADMIN_EMAIL is set:
- creates that platform admin if NO users exist yet (password from BOOTSTRAP_ADMIN_PASSWORD),
- creates BOOTSTRAP_TENANT_NAME owned by the admin if NO tenants exist yet,
- authorizes BOOTSTRAP_SENDER_PHONES (comma separated) on that tenant.
It never modifies existing users or tenants. Remove the variables after the first sign-in.
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuthorizedSender, Tenant, User
from app.security.passwords import hash_password, validate_password_strength
from app.services import audit
from app.services.auth import normalize_email
from app.services.tenancy import add_authorized_sender, create_tenant, normalize_phone

log = logging.getLogger(__name__)


def bootstrap_from_env(db: Session, env: dict[str, str] | None = None) -> dict[str, object]:
    e = env if env is not None else dict(os.environ)
    email = e.get("BOOTSTRAP_ADMIN_EMAIL", "").strip()
    result: dict[str, object] = {
        "admin_created": False,
        "tenant_created": False,
        "senders_added": 0,
    }
    if not email:
        return result

    admin: User | None
    if (db.scalar(select(func.count()).select_from(User)) or 0) == 0:
        password = e.get("BOOTSTRAP_ADMIN_PASSWORD", "")
        if problem := validate_password_strength(password):
            log.error("bootstrap_skipped_weak_password", extra={"reason": problem})
            return result
        admin = User(
            email=normalize_email(email),
            full_name=e.get("BOOTSTRAP_ADMIN_NAME", ""),
            password_hash=hash_password(password),
            is_platform_admin=True,
        )
        db.add(admin)
        db.flush()
        audit.record(
            db,
            "bootstrap.admin_created",
            actor_type="system",
            target_type="user",
            target_id=admin.id,
        )
        result["admin_created"] = True
    else:
        admin = db.scalar(select(User).where(User.email == normalize_email(email)))

    tenant_name = e.get("BOOTSTRAP_TENANT_NAME", "").strip()
    tenant = None
    if tenant_name and admin is not None:
        if (db.scalar(select(func.count()).select_from(Tenant)) or 0) == 0:
            tenant = create_tenant(db, tenant_name, admin)
            audit.record(db, "bootstrap.tenant_created", tenant_id=tenant.id, actor_type="system")
            result["tenant_created"] = True
        else:
            tenant = db.scalar(select(Tenant).where(Tenant.name == tenant_name))

    if tenant is not None:
        for raw in e.get("BOOTSTRAP_SENDER_PHONES", "").split(","):
            if not raw.strip():
                continue
            try:
                phone = normalize_phone(raw)
            except ValueError:
                log.warning("bootstrap_invalid_phone")
                continue
            exists = db.scalar(
                select(AuthorizedSender.id).where(
                    AuthorizedSender.tenant_id == tenant.id, AuthorizedSender.phone == phone
                )
            )
            if exists is None:
                add_authorized_sender(db, tenant.id, phone, "bootstrap")
                result["senders_added"] = int(result["senders_added"]) + 1  # type: ignore[call-overload]
    db.commit()
    if any(result.values()):
        log.info("bootstrap_applied", extra={k: v for k, v in result.items()})
    return result

"""Environment-driven first-run bootstrap (for hosts without a shell)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuthorizedSender, Tenant, TenantMembership, User
from app.services.bootstrap import bootstrap_from_env
from tests.conftest import make_user

ENV = {
    "BOOTSTRAP_ADMIN_EMAIL": "Owner@Example.com",
    "BOOTSTRAP_ADMIN_PASSWORD": "First-Run-Pass-2026",
    "BOOTSTRAP_TENANT_NAME": "Pilot Client",
    "BOOTSTRAP_SENDER_PHONES": "+91 98765 43210, 91 99887 76655, bad",
}


def test_creates_admin_tenant_and_senders_once(db: Session) -> None:
    r = bootstrap_from_env(db, ENV)
    assert r == {"admin_created": True, "tenant_created": True, "senders_added": 2}
    admin = db.scalar(select(User))
    assert admin is not None and admin.email == "owner@example.com" and admin.is_platform_admin
    assert admin.password_hash != ENV["BOOTSTRAP_ADMIN_PASSWORD"]
    tenant = db.scalar(select(Tenant))
    assert tenant is not None and tenant.name == "Pilot Client"
    assert db.scalar(select(TenantMembership.role)) == "owner"
    # Second run (every restart) changes nothing.
    assert bootstrap_from_env(db, ENV) == {
        "admin_created": False,
        "tenant_created": False,
        "senders_added": 0,
    }
    assert db.query(User).count() == 1 and db.query(AuthorizedSender).count() == 2


def test_never_touches_existing_users(db: Session) -> None:
    make_user(db, "someone@example.com")
    db.commit()
    r = bootstrap_from_env(db, ENV)
    assert r["admin_created"] is False and r["tenant_created"] is False
    assert db.scalar(select(User).where(User.email == "owner@example.com")) is None


def test_weak_password_and_missing_email_do_nothing(db: Session) -> None:
    assert bootstrap_from_env(db, {}) == {
        "admin_created": False,
        "tenant_created": False,
        "senders_added": 0,
    }
    assert (
        bootstrap_from_env(db, {**ENV, "BOOTSTRAP_ADMIN_PASSWORD": "short"})["admin_created"]
        is False
    )
    assert db.query(User).count() == 0

"""Tenant provisioning, default categories and authorized senders."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict
from app.models import (
    AuthorizedSender,
    Category,
    Role,
    SenderRole,
    Tenant,
    TenantMembership,
    User,
)

DEFAULT_CATEGORIES: list[tuple[str, str]] = [
    ("Cheque", "Cheques"),
    ("Invoice", "Invoices"),
    ("Receipt", "Receipts"),
    ("Contract", "Contracts"),
    ("Bill", "Bills"),
    ("Purchase Order", "Purchase Orders"),
    ("Bank Statement", "Bank Statements"),
    ("ID/KYC", "ID-KYC"),
    ("Report", "Reports"),
    ("Certificate", "Certificates"),
    ("Other", "Other"),
]
# Sensitive by default: never sent to AI unless an admin enables it per category.
SENSITIVE_DEFAULTS = {"ID/KYC", "Bank Statement", "Cheque"}


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or uuid.uuid4().hex[:8]


def normalize_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if not 8 <= len(digits) <= 15:
        raise ValueError("Phone number must include the country code (8-15 digits)")
    return digits


def create_tenant(db: Session, name: str, owner: User | None = None) -> Tenant:
    slug = slugify(name)
    if db.scalar(select(Tenant).where(Tenant.slug == slug)):
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"
    tenant = Tenant(name=name.strip()[:200], slug=slug, settings={})
    db.add(tenant)
    db.flush()
    for cat_name, folder in DEFAULT_CATEGORIES:
        db.add(
            Category(
                tenant_id=tenant.id,
                name=cat_name,
                folder_name=folder,
                ai_allowed=cat_name not in SENSITIVE_DEFAULTS,
                keywords=[],
            )
        )
    if owner is not None:
        db.add(TenantMembership(tenant_id=tenant.id, user_id=owner.id, role=Role.owner))
    db.flush()
    return tenant


def active_categories(db: Session, tenant_id: uuid.UUID) -> list[Category]:
    return list(
        db.scalars(
            select(Category)
            .where(Category.tenant_id == tenant_id, Category.is_active.is_(True))
            .order_by(Category.name)
        )
    )


def add_authorized_sender(
    db: Session,
    tenant_id: uuid.UUID,
    phone: str,
    display_name: str = "",
    role: str = SenderRole.member,
) -> AuthorizedSender:
    digits = normalize_phone(phone)
    existing = db.scalar(
        select(AuthorizedSender).where(
            AuthorizedSender.tenant_id == tenant_id, AuthorizedSender.phone == digits
        )
    )
    if existing:
        if existing.is_active:
            raise Conflict("This number is already authorized")
        existing.is_active = True
        existing.display_name = display_name or existing.display_name
        existing.role = role
        return existing
    sender = AuthorizedSender(
        tenant_id=tenant_id, phone=digits, display_name=display_name, role=role
    )
    db.add(sender)
    db.flush()
    return sender


def sender_role(db: Session, tenant_id: uuid.UUID, phone: str | None) -> str | None:
    """Role of an active authorized sender ("member"/"admin"), or None if not authorized."""
    if not phone:
        return None
    return db.scalar(
        select(AuthorizedSender.role).where(
            AuthorizedSender.tenant_id == tenant_id,
            AuthorizedSender.phone == phone,
            AuthorizedSender.is_active.is_(True),
        )
    )


def is_authorized_sender(db: Session, tenant_id: uuid.UUID, phone: str | None) -> bool:
    if not phone:
        return False
    return (
        db.scalar(
            select(AuthorizedSender.id).where(
                AuthorizedSender.tenant_id == tenant_id,
                AuthorizedSender.phone == phone,
                AuthorizedSender.is_active.is_(True),
            )
        )
        is not None
    )

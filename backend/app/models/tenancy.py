"""Tenants, users, memberships, sessions and login throttling."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPk


class TenantStatus(StrEnum):
    active = "active"
    suspended = "suspended"


class Role(StrEnum):
    owner = "owner"
    admin = "admin"
    member = "member"


ROLE_RANK = {Role.member: 1, Role.admin: 2, Role.owner: 3}


class Tenant(UUIDPk, Timestamps, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(20), default=TenantStatus.active)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    # AI processing is OFF until the client authorizes it (privacy).
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_consent_note: Mapped[str | None] = mapped_column(String(500))
    max_file_bytes: Mapped[int | None] = mapped_column(Integer)
    monthly_document_quota: Mapped[int] = mapped_column(Integer, default=1000)
    monthly_ai_quota: Mapped[int] = mapped_column(Integer, default=500)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    memberships: Mapped[list[TenantMembership]] = relationship(back_populates="tenant")


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None]

    memberships: Mapped[list[TenantMembership]] = relationship(back_populates="user")


class TenantMembership(UUIDPk, Timestamps, Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), default=Role.member)

    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class UserSession(UUIDPk, Base):
    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class LoginAttempt(UUIDPk, Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_email_created", "email", "created_at"),)

    email: Mapped[str] = mapped_column(String(320))
    ip: Mapped[str | None] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime]

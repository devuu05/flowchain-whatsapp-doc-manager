"""Integration configurations (WhatsApp, Google Drive, AI), senders and categories."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class IntegrationKind(StrEnum):
    whatsapp = "whatsapp"
    google_drive = "google_drive"  # also used by the FAKE dev storage
    onedrive = "onedrive"


# Kinds that provide document storage; a workspace has at most one active at a time.
STORAGE_KINDS = (IntegrationKind.google_drive, IntegrationKind.onedrive)


class IntegrationStatus(StrEnum):
    pending = "pending"
    connected = "connected"
    error = "error"
    disconnected = "disconnected"


class IntegrationConfig(UUIDPk, Timestamps, Base):
    __tablename__ = "integration_configs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(20), default=IntegrationStatus.pending)
    # Unguessable key embedded in the webhook URL (WhatsApp connections only).
    connection_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    # Fernet-encrypted JSON of secrets (API keys, OAuth tokens). Never returned by the API.
    encrypted_secrets: Mapped[str | None] = mapped_column(Text)
    # Non-secret settings (phone number, Drive root folder id, ...).
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_error: Mapped[str | None] = mapped_column(String(1000))
    last_checked_at: Mapped[datetime | None]


class SenderRole(StrEnum):
    member = "member"  # save, search, receive documents
    admin = "admin"  # additionally rename and delete documents from WhatsApp


class AuthorizedSender(UUIDPk, Timestamps, Base):
    __tablename__ = "authorized_senders"
    __table_args__ = (UniqueConstraint("tenant_id", "phone"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    phone: Mapped[str] = mapped_column(String(32))  # digits only, with country code
    display_name: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[str] = mapped_column(String(20), default=SenderRole.member)


class Category(UUIDPk, Timestamps, Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(60))
    folder_name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # When false, documents the user describes as this category are never sent to AI.
    ai_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    keywords: Mapped[list[str]] = mapped_column(JSONB, default=list)

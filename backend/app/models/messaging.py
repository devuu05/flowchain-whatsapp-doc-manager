"""Inbound webhook events and the WhatsApp message log (incl. idempotent outbound sends)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class InboundStatus(StrEnum):
    received = "received"
    processed = "processed"
    ignored = "ignored"
    failed = "failed"


class InboundEvent(UUIDPk, Timestamps, Base):
    __tablename__ = "inbound_events"
    __table_args__ = (UniqueConstraint("connection_id", "provider_event_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_configs.id", ondelete="CASCADE")
    )
    provider_event_id: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(64))
    sender: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default=InboundStatus.received)
    ignore_reason: Mapped[str | None] = mapped_column(String(200))
    # Normalized event (see schemas.events.NormalizedEvent). Raw payload kept redacted.
    normalized: Mapped[dict[str, Any]] = mapped_column(JSONB)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Direction(StrEnum):
    inbound = "inbound"
    outbound = "outbound"


class OutboundStatus(StrEnum):
    pending = "pending"
    sent = "sent"
    failed = "failed"


class Message(UUIDPk, Timestamps, Base):
    """Conversation log. Outbound rows double as the idempotent send outbox."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_tenant_created", "tenant_id", "created_at"),
        UniqueConstraint("idempotency_key"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_configs.id", ondelete="CASCADE")
    )
    direction: Mapped[str] = mapped_column(String(10))
    phone: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(20))  # text | media | document
    body: Mapped[str | None] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    inbound_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inbound_events.id", ondelete="SET NULL")
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str | None] = mapped_column(String(20))
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1000))
    sent_at: Mapped[datetime | None]

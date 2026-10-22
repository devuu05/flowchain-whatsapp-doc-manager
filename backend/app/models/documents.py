"""Documents, transient media blobs and pending-description state."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class DocumentStatus(StrEnum):
    received = "received"
    awaiting_description = "awaiting_description"
    queued = "queued"
    processing = "processing"
    stored = "stored"
    failed = "failed"
    rejected = "rejected"


class MediaBlob(UUIDPk, Base):
    __tablename__ = "media_blobs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(20), default="intake")  # intake | delivery
    filename: Mapped[str] = mapped_column(String(255), default="document")
    mime_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime] = mapped_column(index=True)


class Document(UUIDPk, Timestamps, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "inbound_event_id"),
        Index("ix_documents_tenant_created", "tenant_id", "created_at"),
        Index("ix_documents_tenant_type", "tenant_id", "document_type"),
        Index("ix_documents_tenant_period", "tenant_id", "period_start", "period_end"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("integration_configs.id", ondelete="SET NULL")
    )
    inbound_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inbound_events.id", ondelete="SET NULL")
    )
    sender: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default=DocumentStatus.received)

    original_filename: Mapped[str] = mapped_column(String(255))
    extension: Mapped[str] = mapped_column(String(16))
    mime_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str | None] = mapped_column(Text)

    # Extraction
    extraction_status: Mapped[str | None] = mapped_column(String(20))
    extraction_warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    page_count: Mapped[int | None] = mapped_column(Integer)
    char_count: Mapped[int | None] = mapped_column(Integer)

    # Classification
    document_type: Mapped[str | None] = mapped_column(String(60))
    company_or_person: Mapped[str | None] = mapped_column(String(200))
    doc_date: Mapped[date | None] = mapped_column(Date)
    date_or_period: Mapped[str | None] = mapped_column(String(60))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    summary: Mapped[str | None] = mapped_column(String(600))
    folder: Mapped[str | None] = mapped_column(String(200))
    standardized_filename: Mapped[str | None] = mapped_column(String(255))
    confidence: Mapped[float | None] = mapped_column(Float)
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    classifier: Mapped[str | None] = mapped_column(String(40))

    # Storage
    storage_provider: Mapped[str | None] = mapped_column(String(32))
    storage_file_id: Mapped[str | None] = mapped_column(String(200))
    storage_folder_id: Mapped[str | None] = mapped_column(String(200))
    storage_md5: Mapped[str | None] = mapped_column(String(64))
    stored_at: Mapped[datetime | None]

    blob_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_blobs.id", ondelete="SET NULL")
    )
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class PendingStatus(StrEnum):
    awaiting = "awaiting"
    resolved = "resolved"
    expired = "expired"
    cancelled = "cancelled"


class PendingMedia(UUIDPk, Timestamps, Base):
    __tablename__ = "pending_media"
    __table_args__ = (Index("ix_pending_lookup", "tenant_id", "connection_id", "sender", "status"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_configs.id", ondelete="CASCADE")
    )
    sender: Mapped[str] = mapped_column(String(32))
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[str] = mapped_column(String(20), default=PendingStatus.awaiting)
    expires_at: Mapped[datetime]

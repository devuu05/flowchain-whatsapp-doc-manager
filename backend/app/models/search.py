"""Persisted WhatsApp search sessions and their numbered results."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class SearchStatus(StrEnum):
    active = "active"
    superseded = "superseded"
    expired = "expired"


class SearchSession(UUIDPk, Timestamps, Base):
    __tablename__ = "search_sessions"
    __table_args__ = (Index("ix_search_lookup", "tenant_id", "connection_id", "sender", "status"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_configs.id", ondelete="CASCADE")
    )
    sender: Mapped[str] = mapped_column(String(32))
    query_text: Mapped[str] = mapped_column(String(500))
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    page: Mapped[int] = mapped_column(Integer, default=1)
    total: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=SearchStatus.active)
    expires_at: Mapped[datetime]


class SearchResult(UUIDPk, Base):
    __tablename__ = "search_results"
    __table_args__ = (UniqueConstraint("search_session_id", "position"),)

    search_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("search_sessions.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(300))

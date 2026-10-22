"""Audit trail for sensitive operations."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import AuditEvent
from app.security.redaction import redact


def record(
    db: Session,
    action: str,
    *,
    tenant_id: uuid.UUID | None = None,
    actor_type: str = "system",
    actor_id: str | uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_id=str(actor_id) if actor_id else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id else None,
        ip=ip,
        details=redact(details or {}),
        created_at=utcnow(),
    )
    db.add(event)
    return event

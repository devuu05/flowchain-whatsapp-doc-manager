"""Idempotent outbound WhatsApp messages (outbox rows + send jobs)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import Direction, Message, OutboundStatus
from app.workers import queue

MAX_TEXT = 4000


def queue_text(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    to: str,
    text: str,
    idempotency_key: str,
    document_id: uuid.UUID | None = None,
    inbound_event_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Create the outbox row and its send job. Re-queuing the same key is a no-op."""
    now = utcnow()
    msg_id = db.execute(
        insert(Message)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            connection_id=connection_id,
            direction=Direction.outbound,
            phone=to,
            kind="text",
            body=text[:MAX_TEXT],
            document_id=document_id,
            inbound_event_id=inbound_event_id,
            idempotency_key=idempotency_key,
            status=OutboundStatus.pending,
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(Message.id)
    ).scalar_one_or_none()
    if msg_id is None:
        return None
    queue.enqueue(
        db,
        "send_message",
        {"message_id": str(msg_id)},
        tenant_id=tenant_id,
        idempotency_key=f"send:{idempotency_key}",
        max_attempts=5,
    )
    return msg_id


def log_inbound(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    phone: str,
    kind: str,
    body: str | None,
    inbound_event_id: uuid.UUID,
    document_id: uuid.UUID | None = None,
) -> None:
    exists = db.scalar(
        select(Message.id).where(
            Message.inbound_event_id == inbound_event_id, Message.direction == Direction.inbound
        )
    )
    if exists:
        return
    db.add(
        Message(
            tenant_id=tenant_id,
            connection_id=connection_id,
            direction=Direction.inbound,
            phone=phone,
            kind=kind,
            # Captions/requests are short user text; document contents are never logged here.
            body=(body or "")[:1000] or None,
            inbound_event_id=inbound_event_id,
            document_id=document_id,
        )
    )

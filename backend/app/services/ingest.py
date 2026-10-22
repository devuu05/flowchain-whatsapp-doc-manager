"""Fast webhook ingestion: dedupe + persist + enqueue in a single transaction."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import InboundEvent, InboundStatus, IntegrationConfig
from app.schemas.events import NormalizedEvent
from app.security.redaction import mask_phone, redact
from app.workers import queue

log = logging.getLogger(__name__)
RAW_PAYLOAD_LIMIT = 20_000


def _ignore_reason(ev: NormalizedEvent) -> str | None:
    if ev.event_type != "message":
        return f"event type {ev.event_type}"
    if ev.from_me:
        return "message sent by this account"
    if ev.is_group:
        return "group messages are not supported"
    if not ev.sender:
        return "sender phone number unavailable"
    if not ev.text and not ev.media:
        return "empty message"
    return None


def ingest_events(
    db: Session,
    conn: IntegrationConfig,
    events: list[NormalizedEvent],
    raw_payload: dict[str, Any] | None,
) -> dict[str, int]:
    """Persist each event once (unique per connection+event id) and enqueue processing."""
    counts = {"accepted": 0, "duplicate": 0, "ignored": 0}
    raw = redact(raw_payload) if raw_payload else None
    if raw is not None and len(str(raw)) > RAW_PAYLOAD_LIMIT:
        raw = {"truncated": True}
    now = utcnow()
    for ev in events:
        reason = _ignore_reason(ev)
        stmt = (
            insert(InboundEvent)
            .values(
                tenant_id=conn.tenant_id,
                connection_id=conn.id,
                provider_event_id=ev.event_id,
                event_type=ev.event_type,
                sender=ev.sender,
                status=InboundStatus.ignored if reason else InboundStatus.received,
                ignore_reason=reason,
                normalized=ev.model_dump(mode="json"),
                raw_payload=raw,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["connection_id", "provider_event_id"])
            .returning(InboundEvent.id)
        )
        event_id = db.execute(stmt).scalar_one_or_none()
        if event_id is None:
            counts["duplicate"] += 1
            continue
        if reason:
            counts["ignored"] += 1
            continue
        queue.enqueue(
            db,
            "handle_inbound",
            {"inbound_event_id": str(event_id)},
            tenant_id=conn.tenant_id,
            idempotency_key=f"inbound:{event_id}",
        )
        counts["accepted"] += 1
        log.info(
            "inbound_accepted",
            extra={"sender": mask_phone(ev.sender), "has_media": ev.media is not None},
        )
    db.commit()
    return counts

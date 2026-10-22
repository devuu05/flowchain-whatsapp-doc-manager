"""Periodic housekeeping: expire pending state and search lists, purge transient blobs."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import (
    Document,
    DocumentStatus,
    InboundEvent,
    LoginAttempt,
    MediaBlob,
    PendingMedia,
    PendingStatus,
    SearchSession,
    SearchStatus,
    UserSession,
)
from app.services import outbox
from app.services.conversation import enqueue_processing

log = logging.getLogger(__name__)
RAW_PAYLOAD_RETENTION_DAYS = 14
BLOB_HARD_LIMIT_DAYS = 14


def expire_pending(db: Session) -> int:
    """Expired pending files are saved without a description rather than lost."""
    now = utcnow()
    rows = list(
        db.scalars(
            select(PendingMedia)
            .where(PendingMedia.status == PendingStatus.awaiting, PendingMedia.expires_at <= now)
            .with_for_update(skip_locked=True)
            .limit(200)
        )
    )
    for p in rows:
        p.status = PendingStatus.expired
        doc = db.get(Document, p.document_id)
        if doc is None or doc.status != DocumentStatus.awaiting_description:
            continue
        if doc.blob_id is None:
            doc.status = DocumentStatus.failed
            doc.failure_reason = "original expired before a description was received"
            continue
        doc.extraction_warnings = [*doc.extraction_warnings, "no description received in time"]
        enqueue_processing(db, doc)
        outbox.queue_text(
            db,
            tenant_id=doc.tenant_id,
            connection_id=p.connection_id,
            to=p.sender,
            text=(
                f"⌛ No description received for *{doc.original_filename}*, so I'm saving it "
                "without one. It will be marked for review."
            ),
            idempotency_key=f"pending-expired:{p.id}",
            document_id=doc.id,
        )
    return len(rows)


def sweep(db: Session) -> dict[str, int]:
    now = utcnow()
    stats = {"pending_expired": expire_pending(db)}
    stats["searches_expired"] = db.execute(
        update(SearchSession)
        .where(SearchSession.status == SearchStatus.active, SearchSession.expires_at <= now)
        .values(status=SearchStatus.expired)
    ).rowcount  # type: ignore[attr-defined]
    # Delivery blobs and expired intake blobs that are not referenced by an unfinished document.
    referenced = select(Document.blob_id).where(Document.blob_id.is_not(None))
    stats["blobs_deleted"] = db.execute(
        delete(MediaBlob).where(
            MediaBlob.expires_at <= now,
            MediaBlob.id.not_in(referenced.where(Document.status.not_in([DocumentStatus.stored]))),
        )
    ).rowcount  # type: ignore[attr-defined]
    stats["sessions_deleted"] = db.execute(
        delete(UserSession).where(UserSession.expires_at <= now - timedelta(days=7))
    ).rowcount  # type: ignore[attr-defined]
    stats["login_attempts_deleted"] = db.execute(
        delete(LoginAttempt).where(LoginAttempt.created_at <= now - timedelta(days=30))
    ).rowcount  # type: ignore[attr-defined]
    stats["raw_payloads_pruned"] = db.execute(
        update(InboundEvent)
        .where(
            InboundEvent.raw_payload.is_not(None),
            InboundEvent.created_at <= now - timedelta(days=RAW_PAYLOAD_RETENTION_DAYS),
        )
        .values(raw_payload=None)
    ).rowcount  # type: ignore[attr-defined]
    stats["provider_refs_pruned"] = db.execute(
        text(
            "UPDATE inbound_events SET normalized = jsonb_set(normalized, '{media,provider_ref}', "
            "'{}'::jsonb) WHERE (status IN ('processed', 'ignored') OR (status = 'failed' AND "
            "updated_at < now() - interval '3 days')) AND normalized->'media'->'provider_ref' "
            "IS NOT NULL AND normalized->'media'->'provider_ref' <> '{}'::jsonb"
        )
    ).rowcount  # type: ignore[attr-defined]
    # Hard cap: no transient blob outlives this, even for failed documents (FK sets NULL).
    stats["stale_blobs_deleted"] = db.execute(
        delete(MediaBlob).where(MediaBlob.created_at <= now - timedelta(days=BLOB_HARD_LIMIT_DAYS))
    ).rowcount  # type: ignore[attr-defined]
    if any(stats.values()):
        log.info("maintenance_sweep", extra=stats)
    return stats

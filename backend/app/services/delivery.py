"""Authorized document delivery over WhatsApp via short-lived signed URLs."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utcnow
from app.models import (
    Direction,
    Document,
    DocumentStatus,
    IntegrationConfig,
    Job,
    MediaBlob,
    Message,
    OutboundStatus,
)
from app.providers import registry
from app.providers.errors import ProviderError
from app.security.signed_urls import make_media_token
from app.services import audit, outbox
from app.services.tenancy import is_authorized_sender
from app.workers import queue
from app.workers.errors import PermanentJobError, TransientJobError

WHATSAPP_DOCUMENT_LIMIT = 100 * 1024 * 1024


def deliver_document(db: Session, job: Job) -> None:
    s = get_settings()
    p = job.payload
    tenant_id = job.tenant_id
    doc = db.scalar(
        select(Document).where(
            Document.id == uuid.UUID(p["document_id"]), Document.tenant_id == tenant_id
        )
    )
    conn = db.scalar(
        select(IntegrationConfig).where(
            IntegrationConfig.id == uuid.UUID(p["connection_id"]),
            IntegrationConfig.tenant_id == tenant_id,
        )
    )
    if doc is None or conn is None or tenant_id is None:
        raise PermanentJobError("document or connection not found for tenant")
    to = str(p["to"])
    if not is_authorized_sender(db, tenant_id, to):
        raise PermanentJobError("recipient is no longer authorized")
    if doc.status != DocumentStatus.stored or not doc.storage_file_id:
        raise PermanentJobError("document is not available")

    key = f"deliver:{p['inbound_event_id']}"
    msg = db.scalar(select(Message).where(Message.idempotency_key == key).with_for_update())
    if msg is not None and msg.status == OutboundStatus.sent:
        return
    filename = doc.standardized_filename or doc.original_filename
    if msg is None:
        msg = Message(
            tenant_id=tenant_id,
            connection_id=conn.id,
            direction=Direction.outbound,
            phone=to,
            kind="document",
            body=filename,
            document_id=doc.id,
            idempotency_key=key,
            status=OutboundStatus.pending,
            attempts=0,
        )
        db.add(msg)
        db.flush()

    limit = min(s.max_file_bytes * 2, WHATSAPP_DOCUMENT_LIMIT)
    try:
        storage, _ = registry.storage_for(db, tenant_id)
        data = storage.download(doc.storage_file_id, limit)
    except ProviderError as exc:
        if exc.transient:
            raise TransientJobError(str(exc)) from exc
        raise PermanentJobError(str(exc)) from exc

    now = utcnow()
    blob = MediaBlob(
        tenant_id=tenant_id,
        purpose="delivery",
        filename=filename,
        mime_type=doc.mime_type,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        data=data,
        created_at=now,
        expires_at=now + timedelta(seconds=s.signed_media_url_ttl_seconds + 300),
    )
    db.add(blob)
    db.flush()
    token = make_media_token(tenant_id, blob.id, s.signed_media_url_ttl_seconds)
    url = f"{s.public_base_url}/api/v1/media/{token}"
    # The blob must be committed before the provider fetches the URL.
    db.commit()

    provider = registry.whatsapp_for(conn)
    msg = db.get(Message, msg.id, with_for_update=True, populate_existing=True)
    assert msg is not None
    if msg.status == OutboundStatus.sent:
        return
    msg.attempts += 1
    queue.ensure_lease(job)
    try:
        result = provider.send_document(to, document_url=url, filename=filename, caption=None)
    except ProviderError as exc:
        msg.last_error = str(exc)[:1000]
        db.commit()
        if exc.transient:
            raise TransientJobError(str(exc), retry_after=exc.retry_after) from exc
        raise PermanentJobError(str(exc)) from exc
    msg.status = OutboundStatus.sent
    msg.provider_message_id = result.provider_message_id
    msg.sent_at = utcnow()
    audit.record(
        db,
        "document.delivered_whatsapp",
        tenant_id=tenant_id,
        actor_type="system",
        target_type="document",
        target_id=doc.id,
    )


def on_deliver_dead(db: Session, job: Job) -> None:
    p = job.payload
    if job.tenant_id is None:
        return
    msg = db.scalar(
        select(Message).where(Message.idempotency_key == f"deliver:{p.get('inbound_event_id')}")
    )
    if msg is not None and msg.status != OutboundStatus.sent:
        msg.status = OutboundStatus.failed
        msg.last_error = (job.last_error or "")[:1000]
    outbox.queue_text(
        db,
        tenant_id=job.tenant_id,
        connection_id=uuid.UUID(p["connection_id"]),
        to=str(p["to"]),
        text="❌ Sorry, I couldn't send that document. Please try again later.",
        idempotency_key=f"deliver-failed:{p.get('inbound_event_id')}",
    )

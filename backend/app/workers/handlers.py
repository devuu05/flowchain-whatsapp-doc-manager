"""Job handlers. Importing this module registers them with the worker registry."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import (
    InboundEvent,
    InboundStatus,
    IntegrationConfig,
    IntegrationStatus,
    Job,
    Message,
    OutboundStatus,
    Tenant,
    TenantStatus,
)
from app.providers import registry
from app.providers.errors import ProviderError
from app.schemas.events import NormalizedEvent
from app.security.redaction import mask_phone
from app.services import audit, conversation, delivery, outbox, pipeline
from app.services.tenancy import is_authorized_sender
from app.workers import queue
from app.workers.errors import PermanentJobError, TransientJobError
from app.workers.registry import job_handler

log = logging.getLogger(__name__)


def _on_inbound_dead(db: Session, job: Job) -> None:
    ev = db.get(InboundEvent, uuid.UUID(job.payload["inbound_event_id"]))
    if ev is None or ev.status != InboundStatus.received:
        return
    # Keep the provider reference so an admin retry can still download the media; the
    # maintenance sweep prunes it from failed events after a grace period.
    ev.status = InboundStatus.failed
    if ev.sender and is_authorized_sender(db, ev.tenant_id, ev.sender):
        outbox.queue_text(
            db,
            tenant_id=ev.tenant_id,
            connection_id=ev.connection_id,
            to=ev.sender,
            text=(
                "❌ Sorry, I couldn't process your last message"
                f"{' (file)' if ev.normalized.get('media') else ''}. Nothing was saved. "
                "Please try sending it again in a few minutes."
            ),
            idempotency_key=f"inbound-failed:{ev.id}",
            inbound_event_id=ev.id,
        )


def _strip_provider_ref(ev: InboundEvent) -> None:
    """Drop the provider download handle (media keys, thumbnails) once it is not needed."""
    media = ev.normalized.get("media") if isinstance(ev.normalized, dict) else None
    if isinstance(media, dict) and media.get("provider_ref"):
        ev.normalized = {**ev.normalized, "media": {**media, "provider_ref": {}}}


@job_handler("handle_inbound", on_dead=_on_inbound_dead)
def handle_inbound(db: Session, job: Job) -> None:
    ev_row = db.get(InboundEvent, uuid.UUID(job.payload["inbound_event_id"]), with_for_update=True)
    if ev_row is None or ev_row.tenant_id != job.tenant_id:
        raise PermanentJobError("inbound event not found for tenant")
    if ev_row.status != InboundStatus.received:
        return  # already handled
    tenant = db.get(Tenant, ev_row.tenant_id)
    conn = db.get(IntegrationConfig, ev_row.connection_id)
    if tenant is None or conn is None or conn.tenant_id != tenant.id:
        raise PermanentJobError("tenant/connection missing")
    if tenant.status != TenantStatus.active or conn.status == IntegrationStatus.disconnected:
        ev_row.status = InboundStatus.ignored
        ev_row.ignore_reason = "workspace or connection inactive"
        _strip_provider_ref(ev_row)
        return
    ev = NormalizedEvent.model_validate(ev_row.normalized)
    if not is_authorized_sender(db, tenant.id, ev.sender):
        ev_row.status = InboundStatus.ignored
        ev_row.ignore_reason = "sender not authorized"
        _strip_provider_ref(ev_row)
        audit.record(
            db,
            "whatsapp.unauthorized_sender",
            tenant_id=tenant.id,
            actor_type="whatsapp",
            details={"sender": mask_phone(ev.sender)},
        )
        return
    assert ev.sender is not None
    # Serialize processing per (tenant, sender) so concurrent workers cannot race on the same
    # pending file or result list. Released automatically at transaction end.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"conv:{tenant.id}:{ev.sender}"},
    )
    db.refresh(ev_row)
    if ev_row.status != InboundStatus.received:
        return
    ctx = conversation.Ctx(db=db, tenant=tenant, conn=conn, sender=ev.sender, event=ev_row)
    if ev.media is not None:
        conversation.handle_media(ctx, ev)
        _strip_provider_ref(ev_row)
    else:
        conversation.handle_text(ctx, ev)
    ev_row.status = InboundStatus.processed


def _on_send_dead(db: Session, job: Job) -> None:
    msg = db.get(Message, uuid.UUID(job.payload["message_id"]))
    if msg is not None and msg.status != OutboundStatus.sent:
        msg.status = OutboundStatus.failed
        msg.last_error = (job.last_error or "")[:1000]


@job_handler("send_message", on_dead=_on_send_dead)
def send_message(db: Session, job: Job) -> None:
    msg = db.get(Message, uuid.UUID(job.payload["message_id"]), with_for_update=True)
    if msg is None or msg.tenant_id != job.tenant_id:
        raise PermanentJobError("message not found for tenant")
    if msg.status == OutboundStatus.sent:
        return
    conn = db.scalar(
        select(IntegrationConfig).where(
            IntegrationConfig.id == msg.connection_id, IntegrationConfig.tenant_id == msg.tenant_id
        )
    )
    if conn is None:
        raise PermanentJobError("connection missing")
    provider = registry.whatsapp_for(conn)
    msg.attempts += 1
    queue.ensure_lease(job)
    try:
        result = provider.send_text(msg.phone, msg.body or "")
    except ProviderError as exc:
        msg.last_error = str(exc)[:1000]
        if exc.transient:
            raise TransientJobError(str(exc), retry_after=exc.retry_after) from exc
        raise PermanentJobError(str(exc)) from exc
    msg.status = OutboundStatus.sent
    msg.provider_message_id = result.provider_message_id
    msg.sent_at = utcnow()


job_handler("process_document", on_dead=pipeline.on_process_dead)(pipeline.process_document)
job_handler("deliver_document", on_dead=delivery.on_deliver_dead)(delivery.deliver_document)

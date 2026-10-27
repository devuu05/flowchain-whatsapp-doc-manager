"""Admin document actions requested over WhatsApp: rename and delete.

Both run as jobs (never inside the webhook) and re-check that the requester is still an
active WhatsApp admin. The confirmation reply is queued only after the storage provider has
confirmed the change, in the same transaction as the database update.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import Document, DocumentStatus, Job, SenderRole
from app.providers import registry
from app.providers.errors import ProviderError
from app.services import audit, outbox
from app.services.tenancy import sender_role
from app.workers import queue
from app.workers.errors import PermanentJobError, TransientJobError


def _load(db: Session, job: Job) -> tuple[Document, str, uuid.UUID]:
    p = job.payload
    tenant_id = job.tenant_id
    if tenant_id is None:
        raise PermanentJobError("job has no tenant")
    to = str(p["to"])
    if sender_role(db, tenant_id, to) != SenderRole.admin:
        raise PermanentJobError("requester is no longer a WhatsApp admin")
    doc = db.scalar(
        select(Document)
        .where(Document.id == uuid.UUID(p["document_id"]), Document.tenant_id == tenant_id)
        .with_for_update()
    )
    if doc is None:
        raise PermanentJobError("document not found for tenant")
    return doc, to, uuid.UUID(p["connection_id"])


def _reply(
    db: Session, job: Job, to: str, conn_id: uuid.UUID, text: str, doc_id: uuid.UUID | None
) -> None:
    assert job.tenant_id is not None
    outbox.queue_text(
        db,
        tenant_id=job.tenant_id,
        connection_id=conn_id,
        to=to,
        text=text,
        idempotency_key=f"{job.kind}-done:{job.payload['inbound_event_id']}",
        document_id=doc_id,
    )


def rename_document(db: Session, job: Job) -> None:
    doc, to, conn_id = _load(db, job)
    old_name = doc.standardized_filename or doc.original_filename
    if doc.status != DocumentStatus.stored or not doc.storage_file_id:
        raise PermanentJobError("document is not stored")
    stem = str(job.payload["new_stem"])
    new_name = f"{stem}.{doc.extension}" if doc.extension else stem
    if new_name == old_name:
        _reply(db, job, to, conn_id, f"ℹ️ *{old_name}* already has that name.", doc.id)
        return
    queue.ensure_lease(job)
    try:
        storage, _ = registry.storage_for(db, job.tenant_id)  # type: ignore[arg-type]
        stored = storage.rename(doc.storage_file_id, new_name)
    except ProviderError as exc:
        if exc.transient:
            raise TransientJobError(str(exc), retry_after=exc.retry_after) from exc
        if exc.status_code == 409:
            _reply(
                db,
                job,
                to,
                conn_id,
                f"⚠️ A file named *{new_name}* already exists in 📁 {doc.folder}. "
                "Please choose another name.",
                doc.id,
            )
            return
        raise PermanentJobError(str(exc)) from exc
    doc.standardized_filename = stored.name or new_name
    doc.updated_at = utcnow()
    audit.record(
        db,
        "document.renamed_whatsapp",
        tenant_id=job.tenant_id,
        actor_type="whatsapp",
        target_type="document",
        target_id=doc.id,
        details={"from": old_name, "to": doc.standardized_filename},
    )
    _reply(db, job, to, conn_id, f"✏️ Renamed *{old_name}* → *{doc.standardized_filename}*", doc.id)


def delete_document(db: Session, job: Job) -> None:
    doc, to, conn_id = _load(db, job)
    name = doc.standardized_filename or doc.original_filename
    if doc.storage_file_id:
        queue.ensure_lease(job)
        try:
            storage, _ = registry.storage_for(db, job.tenant_id)  # type: ignore[arg-type]
            storage.trash(doc.storage_file_id)
        except ProviderError as exc:
            if exc.transient:
                raise TransientJobError(str(exc), retry_after=exc.retry_after) from exc
            raise PermanentJobError(str(exc)) from exc
    audit.record(
        db,
        "document.deleted_whatsapp",
        tenant_id=job.tenant_id,
        actor_type="whatsapp",
        target_type="document",
        target_id=doc.id,
        details={"filename": name, "folder": doc.folder},
    )
    db.delete(doc)
    _reply(
        db,
        job,
        to,
        conn_id,
        f"🗑️ Deleted *{name}*. It is in the storage recycle bin if you need it back.",
        None,
    )


def on_admin_job_dead(db: Session, job: Job) -> None:
    if job.tenant_id is None:
        return
    action = "rename" if job.kind == "rename_document" else "delete"
    outbox.queue_text(
        db,
        tenant_id=job.tenant_id,
        connection_id=uuid.UUID(job.payload["connection_id"]),
        to=str(job.payload["to"]),
        text=f"❌ Sorry, I couldn't {action} that document. Nothing was changed.",
        idempotency_key=f"{job.kind}-failed:{job.payload.get('inbound_event_id')}",
    )

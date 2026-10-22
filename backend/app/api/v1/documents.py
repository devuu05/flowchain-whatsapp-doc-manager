"""Document search, details, corrections, retry, download and deletion (dashboard)."""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, or_, select

from app.api.deps import DB, Admin, Member
from app.core.config import get_settings
from app.core.errors import AppError, Conflict, NotFound
from app.models import Category, Document, DocumentStatus, InboundEvent, Job, JobStatus
from app.providers import registry
from app.providers.errors import ProviderError
from app.schemas.api import (
    DocumentDetailOut,
    DocumentOut,
    DocumentUpdateIn,
    JobOut,
    Page,
)
from app.services import audit
from app.services.conversation import enqueue_processing
from app.services.dates import parse_date_or_period
from app.services.search import _like
from app.workers.queue import retry_dead_job

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["documents"])


def _get_doc(db: DB, tenant_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    doc = db.scalar(
        select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id)
    )
    if doc is None:
        raise NotFound()
    return doc


@router.get("/documents", response_model=Page[DocumentOut])
def list_documents(
    ctx: Member,
    db: DB,
    q: Annotated[str | None, Query(max_length=200)] = None,
    document_type: Annotated[str | None, Query(max_length=60)] = None,
    status: Annotated[str | None, Query(max_length=32)] = None,
    company: Annotated[str | None, Query(max_length=200)] = None,
    period: Annotated[str | None, Query(max_length=40)] = None,
    review_required: bool | None = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[DocumentOut]:
    stmt = select(Document).where(Document.tenant_id == ctx.tenant_id)
    if q:
        pat = _like(q)
        stmt = stmt.where(
            or_(
                Document.original_filename.ilike(pat, escape="\\"),
                Document.standardized_filename.ilike(pat, escape="\\"),
                Document.company_or_person.ilike(pat, escape="\\"),
                Document.description.ilike(pat, escape="\\"),
            )
        )
    if document_type:
        stmt = stmt.where(Document.document_type == document_type)
    if status:
        stmt = stmt.where(Document.status == status)
    if company:
        stmt = stmt.where(Document.company_or_person.ilike(_like(company), escape="\\"))
    if period:
        p = parse_date_or_period(period)
        if p is None or p.start is None or p.end is None:
            raise AppError(
                "Unrecognized period; use YYYY, YYYY-MM or YYYY-MM-DD", code="invalid_period"
            )
        stmt = stmt.where(Document.period_start <= p.end, Document.period_end >= p.start)
    if review_required is not None:
        stmt = stmt.where(Document.review_required.is_(review_required))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Document.created_at.desc(), Document.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page(
        items=[DocumentOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/documents/{document_id}", response_model=DocumentDetailOut)
def get_document(document_id: uuid.UUID, ctx: Member, db: DB) -> DocumentDetailOut:
    doc = _get_doc(db, ctx.tenant_id, document_id)
    jobs = db.scalars(
        select(Job)
        .where(Job.tenant_id == ctx.tenant_id, Job.document_id == doc.id)
        .order_by(Job.created_at)
    )
    out = DocumentDetailOut.model_validate(doc)
    out.jobs = [JobOut.model_validate(j) for j in jobs]
    return out


@router.patch("/documents/{document_id}", response_model=DocumentDetailOut)
def correct_document(
    document_id: uuid.UUID, body: DocumentUpdateIn, ctx: Admin, db: DB
) -> DocumentDetailOut:
    """Manual metadata correction (does not move/rename the Drive file)."""
    doc = _get_doc(db, ctx.tenant_id, document_id)
    changes = body.model_dump(exclude_unset=True)
    if "document_type" in changes and changes["document_type"] is not None:
        valid = db.scalar(
            select(Category.name).where(
                Category.tenant_id == ctx.tenant_id, Category.name == changes["document_type"]
            )
        )
        if valid is None:
            raise AppError("Unknown category", code="invalid_category")
        doc.document_type = changes["document_type"]
    if "company_or_person" in changes:
        doc.company_or_person = (changes["company_or_person"] or "").strip() or None
    if "date_or_period" in changes:
        raw = changes["date_or_period"]
        p = parse_date_or_period(raw) if raw else None
        if raw and p is None:
            raise AppError("Unrecognized date/period", code="invalid_period")
        doc.date_or_period = p.period if p else None
        doc.doc_date = p.date if p else None
        doc.period_start = p.start if p else None
        doc.period_end = p.end if p else None
    if "review_required" in changes and changes["review_required"] is not None:
        doc.review_required = changes["review_required"]
    audit.record(
        db,
        "document.corrected",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        target_type="document",
        target_id=doc.id,
        ip=ctx.auth.ip,
        details={"fields": sorted(changes)},
    )
    db.commit()
    return get_document(document_id, ctx, db)


@router.post("/documents/{document_id}/retry", response_model=DocumentDetailOut)
def retry_document(document_id: uuid.UUID, ctx: Admin, db: DB) -> DocumentDetailOut:
    doc = _get_doc(db, ctx.tenant_id, document_id)
    if doc.status != DocumentStatus.failed:
        raise Conflict("Only failed documents can be retried")
    if doc.blob_id is None:
        raise Conflict("The original file is no longer available; ask the sender to resend it")
    job = db.scalar(select(Job).where(Job.idempotency_key == f"process:{doc.id}"))
    if job is not None:
        retry_dead_job(db, job)
        doc.status = DocumentStatus.queued
    else:
        enqueue_processing(db, doc)
    audit.record(
        db,
        "document.retry",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        target_type="document",
        target_id=doc.id,
        ip=ctx.auth.ip,
    )
    db.commit()
    return get_document(document_id, ctx, db)


@router.get("/documents/{document_id}/download")
def download_document(document_id: uuid.UUID, ctx: Member, db: DB) -> Response:
    doc = _get_doc(db, ctx.tenant_id, document_id)
    if doc.status != DocumentStatus.stored or not doc.storage_file_id:
        raise Conflict("Document is not stored")
    try:
        storage, _ = registry.storage_for(db, ctx.tenant_id)
        data = storage.download(doc.storage_file_id, get_settings().max_file_bytes * 4)
    except ProviderError as exc:
        raise AppError(f"Storage unavailable: {exc}", code="storage_unavailable") from exc
    audit.record(
        db,
        "document.downloaded",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        target_type="document",
        target_id=doc.id,
        ip=ctx.auth.ip,
    )
    db.commit()
    name = doc.standardized_filename or doc.original_filename
    return Response(
        content=data,
        media_type=doc.mime_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document_id: uuid.UUID, ctx: Admin, db: DB, trash_in_drive: bool = False
) -> Response:
    doc = _get_doc(db, ctx.tenant_id, document_id)
    if trash_in_drive and doc.storage_file_id:
        try:
            storage, _ = registry.storage_for(db, ctx.tenant_id)
            storage.trash(doc.storage_file_id)
        except ProviderError as exc:
            raise AppError(
                f"Could not trash Drive file: {exc}", code="storage_unavailable"
            ) from exc
    audit.record(
        db,
        "document.deleted",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        target_type="document",
        target_id=doc.id,
        ip=ctx.auth.ip,
        details={"trash_in_drive": trash_in_drive},
    )
    db.delete(doc)
    db.commit()
    return Response(status_code=204)


# ---- jobs / failures --------------------------------------------------------------------
@router.get("/jobs", response_model=Page[JobOut])
def list_jobs(
    ctx: Member,
    db: DB,
    status: Annotated[str | None, Query(max_length=20)] = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[JobOut]:
    stmt = select(Job).where(Job.tenant_id == ctx.tenant_id)
    if status:
        stmt = stmt.where(Job.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Job.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return Page(
        items=[JobOut.model_validate(j) for j in rows], total=total, page=page, page_size=page_size
    )


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: uuid.UUID, ctx: Admin, db: DB) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.tenant_id == ctx.tenant_id))
    if job is None:
        raise NotFound()
    if job.status != JobStatus.dead:
        raise Conflict("Only dead jobs can be retried")
    retry_dead_job(db, job)
    if job.kind == "handle_inbound":
        ev = db.scalar(
            select(InboundEvent).where(
                InboundEvent.id == uuid.UUID(job.payload["inbound_event_id"]),
                InboundEvent.tenant_id == ctx.tenant_id,
            )
        )
        if ev is not None and ev.status == "failed":
            ev.status = "received"
    if job.document_id:
        doc = db.get(Document, job.document_id)
        if (
            doc is not None
            and doc.status == DocumentStatus.failed
            and job.kind == "process_document"
        ):
            doc.status = DocumentStatus.queued
    audit.record(
        db,
        "job.retry",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        target_type="job",
        target_id=job.id,
        ip=ctx.auth.ip,
        details={"kind": job.kind},
    )
    db.commit()
    return job

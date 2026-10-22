"""Document processing pipeline: extract -> classify -> validate -> upload -> persist -> confirm.

Never confirms success before the storage provider has confirmed the upload. Uploads are
idempotent: the Drive file carries the document id in appProperties and is looked up before
uploading, so a retry after "uploaded but DB commit failed" reconciles instead of duplicating.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utcnow
from app.models import Category, Document, DocumentStatus, Job, MediaBlob, Tenant
from app.providers import registry
from app.providers.ai.base import ClassificationInput, ClassifierError, RawClassification
from app.providers.ai.rules import RulesClassifier
from app.providers.errors import ProviderError
from app.services import audit, outbox
from app.services.dates import parse_date_or_period
from app.services.extraction.base import ExtractionLimits, ExtractionResult
from app.services.extraction.runner import run_extraction
from app.services.naming import build_standard_filename, sanitize_component
from app.services.tenancy import active_categories
from app.workers.errors import PermanentJobError, TransientJobError

log = logging.getLogger(__name__)
REVIEW_CONFIDENCE = 0.6
AI_TRANSIENT_RETRIES = 3
# Toggle for tests: running extractors in a child process is slow under pytest.
ISOLATE_EXTRACTION = True


@dataclass
class Classified:
    document_type: str
    category: Category
    company: str | None
    period_label: str | None
    period: object | None
    summary: str | None
    confidence: float
    review_required: bool
    classifier: str
    warnings: list[str]


def extract(doc: Document, data: bytes) -> ExtractionResult:
    s = get_settings()
    kind = str(doc.meta.get("file_kind") or doc.extension)
    return run_extraction(
        kind,
        data,
        ExtractionLimits(
            max_pages=s.max_pdf_pages,
            max_sheets=s.max_sheets,
            max_slides=s.max_slides,
            max_chars=s.max_extracted_chars,
        ),
        timeout=s.extraction_timeout_seconds,
        legacy_converter=s.legacy_converter,
        isolate=ISOLATE_EXTRACTION,
    )


def _monthly_ai_count(db: Session, tenant_id: uuid.UUID) -> int:
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.tenant_id == tenant_id,
                Document.classifier == "gemini",
                Document.created_at >= start,
            )
        )
        or 0
    )


def _ai_permitted(
    db: Session, tenant: Tenant, doc: Document, categories: list[Category], text: str | None
) -> tuple[bool, str | None]:
    if not tenant.ai_enabled:
        return False, None
    if _monthly_ai_count(db, tenant.id) >= tenant.monthly_ai_quota:
        return False, "monthly AI quota reached; used rule-based classification"
    # Pre-classify locally: if the user's description points at a sensitive category,
    # the content must not leave our servers.
    names = [c.name for c in categories]
    pre = RulesClassifier().classify(
        ClassificationInput(
            doc.description, None, doc.original_filename, doc.mime_type, doc.extension, names
        )
    )
    blocked = {c.name for c in categories if not c.ai_allowed}
    if pre.document_type in blocked:
        return False, f"AI disabled for category {pre.document_type}"
    if text:
        pre_text = RulesClassifier().classify(
            ClassificationInput(None, text[:5000], None, doc.mime_type, doc.extension, names)
        )
        if pre_text.document_type in blocked and not doc.description:
            return False, f"AI disabled for category {pre_text.document_type}"
    return True, None


def classify(
    db: Session, tenant: Tenant, doc: Document, extraction: ExtractionResult, data: bytes, job: Job
) -> Classified:
    categories = active_categories(db, tenant.id)
    if not categories:
        raise PermanentJobError("workspace has no active categories")
    by_name = {c.name.lower(): c for c in categories}
    names = [c.name for c in categories]
    warnings: list[str] = []
    text = extraction.text or None

    use_ai, why_not = _ai_permitted(db, tenant, doc, categories, text)
    if why_not:
        warnings.append(why_not)
    inp = ClassificationInput(
        description=doc.description,
        extracted_text=text,
        original_filename=doc.original_filename,
        mime_type=doc.mime_type,
        extension=doc.extension,
        allowed_categories=names,
        media_bytes=data if (use_ai and not text) else None,
    )
    raw: RawClassification
    classifier_name = "rules"
    if use_ai:
        classifier = registry.classifier_for(tenant)
        classifier_name = classifier.name
        try:
            raw = classifier.classify(inp)
        except ClassifierError as exc:
            if exc.transient and job.attempts < AI_TRANSIENT_RETRIES:
                raise TransientJobError(f"classifier: {exc}") from exc
            warnings.append(f"AI classification unavailable ({exc}); used rules")
            raw = RulesClassifier().classify(inp)
            classifier_name = "rules"
            raw.confidence = min(raw.confidence, 0.5)
    else:
        raw = RulesClassifier().classify(inp)

    category = by_name.get(raw.document_type.strip().lower())
    review = False
    if category is None:
        warnings.append(f"classifier proposed unknown category {raw.document_type!r}")
        category = by_name.get("other") or categories[0]
        review = True
    company = sanitize_component(raw.company_or_person, 120) or None
    period = parse_date_or_period(raw.date_or_period) if raw.date_or_period else None
    if raw.date_or_period and period is None:
        warnings.append("classifier date was not a valid date/period; ignored")
    review = (
        review
        or raw.confidence < REVIEW_CONFIDENCE
        or category.name == "Other"
        or not raw.evidence_found
    )
    return Classified(
        document_type=category.name,
        category=category,
        company=company,
        period_label=period.period if period else None,
        period=period,
        summary=raw.summary,
        confidence=raw.confidence,
        review_required=review,
        classifier=classifier_name,
        warnings=warnings,
    )


def process_document(db: Session, job: Job) -> None:
    doc_id = uuid.UUID(job.payload["document_id"])
    doc = db.get(Document, doc_id, with_for_update=True)
    if doc is None or doc.tenant_id != job.tenant_id:
        raise PermanentJobError("document not found for tenant")
    if doc.status == DocumentStatus.stored:
        return  # already done (duplicate job execution)
    if doc.status not in (DocumentStatus.queued, DocumentStatus.processing, DocumentStatus.failed):
        raise PermanentJobError(f"document in unexpected state {doc.status}")
    tenant = db.get(Tenant, doc.tenant_id)
    assert tenant is not None
    blob = db.get(MediaBlob, doc.blob_id) if doc.blob_id else None
    if blob is None or blob.tenant_id != doc.tenant_id:
        raise PermanentJobError("original file is no longer available for processing")
    doc.status = DocumentStatus.processing

    extraction = extract(doc, blob.data)
    classified = classify(db, tenant, doc, extraction, blob.data, job)

    year = None
    period = classified.period
    if period is not None and getattr(period, "start", None):
        year = str(period.start.year)  # type: ignore[attr-defined]
    folder_path = [classified.category.folder_name, year or "Undated"]
    filename = build_standard_filename(
        document_type=classified.document_type,
        company=classified.company,
        period=classified.period_label,
        extension=doc.extension,
        short_id=doc.id.hex,
    )

    try:
        storage, _cfg = registry.storage_for(db, tenant.id)
        stored = storage.find_by_document_id(str(doc.id))
        reconciled = stored is not None
        if stored is None:
            folder_id = storage.ensure_folder_path(folder_path)
            stored = storage.upload(
                data=blob.data,
                filename=filename,
                mime_type=doc.mime_type,
                folder_id=folder_id,
                document_id=str(doc.id),
            )
    except ProviderError as exc:
        if exc.transient:
            raise TransientJobError(str(exc), retry_after=exc.retry_after) from exc
        raise PermanentJobError(str(exc)) from exc

    if stored.size_bytes is not None and stored.size_bytes != doc.size_bytes:
        raise TransientJobError("uploaded size mismatch; will verify again")

    doc.extraction_status = extraction.status
    doc.extraction_warnings = [
        *doc.extraction_warnings,
        *extraction.warnings,
        *classified.warnings,
    ][:50]
    doc.page_count = extraction.page_count
    doc.char_count = len(extraction.text or "")
    doc.document_type = classified.document_type
    doc.company_or_person = classified.company
    doc.date_or_period = classified.period_label
    if period is not None:
        doc.doc_date = getattr(period, "date", None)
        doc.period_start = getattr(period, "start", None)
        doc.period_end = getattr(period, "end", None)
    doc.summary = classified.summary
    doc.confidence = classified.confidence
    doc.review_required = classified.review_required
    doc.classifier = classified.classifier
    doc.folder = "/".join(folder_path)
    doc.standardized_filename = stored.name or filename
    doc.storage_provider = storage.name
    doc.storage_file_id = stored.file_id
    doc.storage_folder_id = stored.folder_id
    doc.storage_md5 = stored.md5
    doc.stored_at = utcnow()
    doc.status = DocumentStatus.stored
    doc.failure_reason = None
    doc.blob_id = None
    db.delete(blob)
    audit.record(
        db,
        "document.stored",
        tenant_id=tenant.id,
        actor_type="system",
        target_type="document",
        target_id=doc.id,
        details={"reconciled": reconciled, "classifier": classified.classifier},
    )

    if doc.connection_id and doc.sender:
        bits = [f"✅ Saved *{doc.standardized_filename}*", f"📁 {doc.folder}"]
        if classified.company:
            bits.append(f"🏢 {classified.company}")
        if classified.period_label:
            bits.append(f"📅 {classified.period_label}")
        if classified.review_required:
            bits.append("⚠️ Marked for review — some details could not be confirmed.")
        outbox.queue_text(
            db,
            tenant_id=tenant.id,
            connection_id=doc.connection_id,
            to=doc.sender,
            text="\n".join(bits),
            idempotency_key=f"confirm:{doc.id}",
            document_id=doc.id,
        )


def on_process_dead(db: Session, job: Job) -> None:
    doc_id = uuid.UUID(job.payload["document_id"])
    doc = db.get(Document, doc_id)
    if doc is None or doc.status == DocumentStatus.stored:
        return
    doc.status = DocumentStatus.failed
    doc.failure_reason = (job.last_error or "processing failed")[:500]
    audit.record(
        db,
        "document.failed",
        tenant_id=doc.tenant_id,
        target_type="document",
        target_id=doc.id,
        details={"error": doc.failure_reason},
    )
    if doc.connection_id and doc.sender:
        outbox.queue_text(
            db,
            tenant_id=doc.tenant_id,
            connection_id=doc.connection_id,
            to=doc.sender,
            text=(
                f"❌ I couldn't save *{doc.original_filename}*. It has NOT been stored yet. "
                "Your admin has been notified and can retry it from the dashboard."
            ),
            idempotency_key=f"failed:{doc.id}:{job.id}:{job.attempts}",
            document_id=doc.id,
        )

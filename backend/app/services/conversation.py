"""WhatsApp conversation handling: intake, pending descriptions, search and selection.

Routing order for a text message (documented in docs/architecture/conversation.md):
 1. commands: help / cancel / skip / more
 2. explicit search request (starts with find/search/show/...)
 3. pending files awaiting a description
 4. bare number -> selection from an *active* (unexpired) search list
 5. implicit search ("invoices from ABC")
 6. help
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utcnow
from app.models import (
    Document,
    DocumentStatus,
    InboundEvent,
    IntegrationConfig,
    MediaBlob,
    PendingMedia,
    PendingStatus,
    Tenant,
)
from app.providers import registry
from app.providers.errors import ProviderError
from app.schemas.events import NormalizedEvent
from app.services import audit, outbox, search
from app.services.file_validation import detect_and_validate
from app.services.naming import sanitize_filename, split_extension
from app.services.tenancy import active_categories
from app.workers import queue
from app.workers.errors import TransientJobError

log = logging.getLogger(__name__)

HELP_TEXT = (
    "👋 *Flow Chain Documents*\n"
    "• Send a photo, PDF or Office file with a short caption (e.g. _invoice from ABC Traders_).\n"
    "• Find files: _find invoices from ABC Traders_, _find documents from September 2026_, "
    "_show my latest documents_.\n"
    "• Reply with a number from a result list to receive that file.\n"
    "• _cancel_ discards files waiting for a description, _skip_ saves them without one."
)
NUMBER_ONLY = re.compile(r"^\s*#?(\d{1,3})\s*$")
PENDING_ASSIGN = re.compile(r"^\s*#?(\d{1,2})\s*[:\-.)]\s*(.{2,})$", re.S)
SKIP = re.compile(r"^\s*skip(?:\s+(all|\d{1,2}))?\s*$", re.I)
CANCEL = re.compile(r"^\s*cancel(?:\s+(all|\d{1,2}))?\s*$", re.I)
HELP = re.compile(r"^\s*(help|menu|hi|hello|hey|start|\?)\s*[!.]?\s*$", re.I)
MORE = re.compile(r"^\s*(more|next)\s*$", re.I)
UNSUPPORTED_MEDIA = {"video", "audio", "sticker"}


@dataclass
class Ctx:
    db: Session
    tenant: Tenant
    conn: IntegrationConfig
    sender: str
    event: InboundEvent

    def reply(
        self, text: str, key_suffix: str = "reply", document_id: uuid.UUID | None = None
    ) -> None:
        outbox.queue_text(
            self.db,
            tenant_id=self.tenant.id,
            connection_id=self.conn.id,
            to=self.sender,
            text=text,
            idempotency_key=f"{key_suffix}:{self.event.id}",
            document_id=document_id,
            inbound_event_id=self.event.id,
        )


def caption_is_usable(text: str | None) -> bool:
    if not text:
        return False
    letters = sum(ch.isalnum() for ch in text)
    return letters >= 3


# ---------------------------------------------------------------------------------------------
# Media intake
# ---------------------------------------------------------------------------------------------


def _monthly_document_count(db: Session, tenant_id: uuid.UUID) -> int:
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.tenant_id == tenant_id, Document.created_at >= start)
        )
        or 0
    )


def awaiting(db: Session, ctx: Ctx) -> list[PendingMedia]:
    return list(
        db.scalars(
            select(PendingMedia)
            .where(
                PendingMedia.tenant_id == ctx.tenant.id,
                PendingMedia.connection_id == ctx.conn.id,
                PendingMedia.sender == ctx.sender,
                PendingMedia.status == PendingStatus.awaiting,
                PendingMedia.expires_at > utcnow(),
            )
            .order_by(PendingMedia.created_at, PendingMedia.id)
        )
    )


def _pending_list_text(db: Session, pendings: list[PendingMedia]) -> str:
    lines = []
    for i, p in enumerate(pendings, start=1):
        doc = db.get(Document, p.document_id)
        lines.append(f"{i}. {doc.original_filename if doc else 'file'}")
    return "\n".join(lines)


def handle_media(ctx: Ctx, ev: NormalizedEvent) -> None:
    db = ctx.db
    s = get_settings()
    media = ev.media
    assert media is not None
    if media.kind in UNSUPPORTED_MEDIA:
        ctx.reply(
            f"⚠️ {media.kind.capitalize()} messages aren't supported. Please send photos, PDFs, "
            "Word, Excel, PowerPoint, TXT or CSV files."
        )
        return
    if _monthly_document_count(db, ctx.tenant.id) >= ctx.tenant.monthly_document_quota:
        ctx.reply(
            "⚠️ This workspace has reached its monthly document limit. Please contact your admin."
        )
        audit.record(db, "quota.documents_exceeded", tenant_id=ctx.tenant.id, actor_type="whatsapp")
        return

    max_bytes = min(ctx.tenant.max_file_bytes or s.max_file_bytes, s.max_file_bytes)
    if media.size_bytes and media.size_bytes > max_bytes:
        _reject(ctx, ev, f"the file is larger than {max_bytes // (1024 * 1024)} MB")
        return

    provider = registry.whatsapp_for(ctx.conn)
    try:
        downloaded = provider.download_media(media, max_bytes)
    except ProviderError as exc:
        if exc.transient:
            raise TransientJobError(str(exc), retry_after=exc.retry_after) from exc
        reason = (
            "the file is too large" if "too large" in str(exc) else "it could not be downloaded"
        )
        log.warning("media_download_failed", extra={"error": str(exc)})
        _reject(ctx, ev, reason)
        return

    claimed_name = media.filename or downloaded.filename
    check = detect_and_validate(
        downloaded.data,
        claimed_filename=claimed_name,
        claimed_mime=media.mime_type or downloaded.mime_type,
        max_bytes=max_bytes,
    )
    if not check.ok or not check.extension or not check.mime_type:
        _reject(
            ctx,
            ev,
            check.reason or "the file type is not supported",
            data=downloaded.data,
            filename=claimed_name,
        )
        return

    sha = hashlib.sha256(downloaded.data).hexdigest()
    original = sanitize_filename(
        claimed_name or f"whatsapp-{media.kind}-{utcnow():%Y%m%d-%H%M%S}",
        check.extension,
        fallback=f"whatsapp-{media.kind}",
    )
    existing = db.scalar(
        select(Document).where(
            Document.tenant_id == ctx.tenant.id,
            Document.sha256 == sha,
            Document.status == DocumentStatus.stored,
        )
    )
    if existing is not None:
        ctx.reply(
            f"ℹ️ This file was already saved as *{existing.standardized_filename}* "
            f"on {existing.created_at:%d %b %Y}. I didn't store a second copy."
        )
        return

    now = utcnow()
    blob = MediaBlob(
        tenant_id=ctx.tenant.id,
        purpose="intake",
        filename=original,
        mime_type=check.mime_type,
        sha256=sha,
        size_bytes=len(downloaded.data),
        data=downloaded.data,
        created_at=now,
        expires_at=now + timedelta(hours=s.media_blob_ttl_hours),
    )
    db.add(blob)
    db.flush()
    _, ext = split_extension(original)
    doc = Document(
        tenant_id=ctx.tenant.id,
        connection_id=ctx.conn.id,
        inbound_event_id=ctx.event.id,
        sender=ctx.sender,
        status=DocumentStatus.received,
        original_filename=original,
        extension=ext or check.extension,
        mime_type=check.mime_type,
        size_bytes=len(downloaded.data),
        sha256=sha,
        blob_id=blob.id,
        extraction_warnings=list(check.warnings),
        meta={"file_kind": check.kind},
    )
    db.add(doc)
    db.flush()
    outbox.log_inbound(
        db,
        tenant_id=ctx.tenant.id,
        connection_id=ctx.conn.id,
        phone=ctx.sender,
        kind="media",
        body=ev.text,
        inbound_event_id=ctx.event.id,
        document_id=doc.id,
    )

    if caption_is_usable(ev.text):
        doc.description = (ev.text or "")[:1000]
        enqueue_processing(db, doc)
        ctx.reply(f"📥 Received *{original}*. Processing it now…", "ack", doc.id)
        return

    db.add(
        PendingMedia(
            tenant_id=ctx.tenant.id,
            connection_id=ctx.conn.id,
            sender=ctx.sender,
            document_id=doc.id,
            status=PendingStatus.awaiting,
            expires_at=now + timedelta(hours=s.pending_media_ttl_hours),
        )
    )
    doc.status = DocumentStatus.awaiting_description
    db.flush()
    pendings = awaiting(db, ctx)
    if len(pendings) <= 1:
        ctx.reply(
            f"📎 Got *{original}*. What is it? Reply with a short description, e.g. "
            "_invoice from ABC Traders, Sept 2026_. Reply *skip* to save it without one.",
            "ask",
            doc.id,
        )
    else:
        ctx.reply(
            "📎 You have several files waiting for a description:\n"
            f"{_pending_list_text(db, pendings)}\n"
            "Reply like *2: receipt from Dev Construction* to describe a specific file, "
            "or *skip all*.",
            "ask",
            doc.id,
        )


def _reject(
    ctx: Ctx,
    ev: NormalizedEvent,
    reason: str,
    *,
    data: bytes | None = None,
    filename: str | None = None,
) -> None:
    media = ev.media
    assert media is not None
    name = sanitize_filename(filename or media.filename or "file", "", fallback="file")
    ctx.db.add(
        Document(
            tenant_id=ctx.tenant.id,
            connection_id=ctx.conn.id,
            inbound_event_id=ctx.event.id,
            sender=ctx.sender,
            status=DocumentStatus.rejected,
            original_filename=name[:255],
            extension="",
            mime_type=(media.mime_type or "application/octet-stream")[:128],
            size_bytes=len(data) if data is not None else (media.size_bytes or 0),
            sha256=hashlib.sha256(data).hexdigest() if data else "",
            failure_reason=reason[:500],
            meta={"rejected": True},
        )
    )
    audit.record(
        ctx.db,
        "document.rejected",
        tenant_id=ctx.tenant.id,
        actor_type="whatsapp",
        details={"reason": reason},
    )
    ctx.reply(f"❌ I couldn't accept that file: {reason}.", "reject")


def enqueue_processing(db: Session, doc: Document) -> None:
    doc.status = DocumentStatus.queued
    queue.enqueue(
        db,
        "process_document",
        {"document_id": str(doc.id)},
        tenant_id=doc.tenant_id,
        idempotency_key=f"process:{doc.id}",
        document_id=doc.id,
    )


# ---------------------------------------------------------------------------------------------
# Text handling
# ---------------------------------------------------------------------------------------------


def handle_text(ctx: Ctx, ev: NormalizedEvent) -> None:
    db = ctx.db
    text = (ev.text or "").strip()
    outbox.log_inbound(
        db,
        tenant_id=ctx.tenant.id,
        connection_id=ctx.conn.id,
        phone=ctx.sender,
        kind="text",
        body=text,
        inbound_event_id=ctx.event.id,
    )
    categories = [c.name for c in active_categories(db, ctx.tenant.id)]

    if HELP.match(text):
        ctx.reply(HELP_TEXT)
        return
    if m := CANCEL.match(text):
        _cancel_or_skip(ctx, m.group(1), skip=False)
        return
    if m := SKIP.match(text):
        _cancel_or_skip(ctx, m.group(1), skip=True)
        return
    if MORE.match(text):
        session = search.active_session(db, ctx.tenant.id, ctx.conn.id, ctx.sender)
        if session is None:
            ctx.reply("There's no active result list. Send a new search, e.g. _find all invoices_.")
            return
        page = search.next_page(db, session)
        ctx.reply(search.format_page(page, search.SearchFilters.from_json(session.filters)))
        return

    explicit = search.parse_query(text, categories, require_intent=True)
    if explicit is not None:
        _do_search(ctx, text, explicit)
        return

    pendings = awaiting(db, ctx)
    if pendings and not NUMBER_ONLY.match(text):
        _describe_pending(ctx, pendings, text)
        return

    if m := NUMBER_ONLY.match(text):
        _select(ctx, int(m.group(1)), has_pending=bool(pendings))
        return

    implicit = search.parse_query(text, categories)
    if implicit is not None:
        _do_search(ctx, text, implicit)
        return
    ctx.reply("🤔 I didn't understand that.\n\n" + HELP_TEXT)


def _do_search(ctx: Ctx, text: str, filters: search.SearchFilters) -> None:
    page = search.create_search_session(
        ctx.db,
        tenant_id=ctx.tenant.id,
        connection_id=ctx.conn.id,
        sender=ctx.sender,
        query_text=text,
        filters=filters,
    )
    audit.record(
        ctx.db,
        "search.whatsapp",
        tenant_id=ctx.tenant.id,
        actor_type="whatsapp",
        target_type="search_session",
        target_id=page.session.id,
        details={"filters": filters.to_json(), "total": page.total},
    )
    ctx.reply(search.format_page(page, filters))


def _select(ctx: Ctx, number: int, *, has_pending: bool) -> None:
    db = ctx.db
    session = search.active_session(db, ctx.tenant.id, ctx.conn.id, ctx.sender)
    if session is None:
        if has_pending:
            ctx.reply("Please describe the file in words (e.g. _receipt from Dev Construction_).")
        elif search.recently_expired_session(db, ctx.tenant.id, ctx.conn.id, ctx.sender):
            ctx.reply(
                "⌛ That result list has expired. Please search again, e.g. _find all invoices_."
            )
        else:
            ctx.reply("There's no result list to choose from. Try _find all documents_.")
        return
    shown = search.shown_count(db, session)
    result = search.select_result(db, session, number) if number >= 1 else None
    if result is None:
        if shown == 0:
            ctx.reply("That search had no results. Try a different search.")
        else:
            ctx.reply(f"Please reply with a number between 1 and {shown} from the list.")
        return
    doc = db.scalar(
        select(Document).where(
            Document.id == result.document_id, Document.tenant_id == ctx.tenant.id
        )
    )
    if doc is None or doc.status != DocumentStatus.stored or not doc.storage_file_id:
        ctx.reply("Sorry, that document is no longer available.")
        return
    queue.enqueue(
        db,
        "deliver_document",
        {
            "document_id": str(doc.id),
            "connection_id": str(ctx.conn.id),
            "to": ctx.sender,
            "inbound_event_id": str(ctx.event.id),
        },
        tenant_id=ctx.tenant.id,
        idempotency_key=f"deliver:{ctx.event.id}",
        document_id=doc.id,
        max_attempts=4,
    )
    audit.record(
        db,
        "document.requested_whatsapp",
        tenant_id=ctx.tenant.id,
        actor_type="whatsapp",
        target_type="document",
        target_id=doc.id,
    )
    ctx.reply(
        f"📤 Sending *{doc.standardized_filename or doc.original_filename}*…", "sending", doc.id
    )


def _describe_pending(ctx: Ctx, pendings: list[PendingMedia], text: str) -> None:
    db = ctx.db
    if len(pendings) == 1:
        target, description = pendings[0], text
    else:
        m = PENDING_ASSIGN.match(text)
        if not m:
            ctx.reply(
                "You have several files waiting for a description:\n"
                f"{_pending_list_text(db, pendings)}\n"
                "Reply like *1: invoice from ABC Traders* so I know which file you mean."
            )
            return
        idx = int(m.group(1))
        if not 1 <= idx <= len(pendings):
            ctx.reply(f"Please pick a file number between 1 and {len(pendings)}.")
            return
        target, description = pendings[idx - 1], m.group(2)
    if not caption_is_usable(description):
        ctx.reply("Please send a few words describing the file.")
        return
    doc = db.get(Document, target.document_id)
    if doc is None or doc.tenant_id != ctx.tenant.id:
        target.status = PendingStatus.cancelled
        return
    target.status = PendingStatus.resolved
    doc.description = description.strip()[:1000]
    enqueue_processing(db, doc)
    remaining = len(pendings) - 1
    extra = f" {remaining} file(s) still need a description." if remaining else ""
    ctx.reply(f"👍 Thanks! Processing *{doc.original_filename}* now.{extra}", "described", doc.id)


def _cancel_or_skip(ctx: Ctx, which: str | None, *, skip: bool) -> None:
    db = ctx.db
    pendings = awaiting(db, ctx)
    if not pendings:
        ctx.reply("There are no files waiting for a description.")
        return
    if which is None and len(pendings) > 1:
        verb = "skip" if skip else "cancel"
        ctx.reply(
            f"Which file? Reply *{verb} N* or *{verb} all*:\n{_pending_list_text(db, pendings)}"
        )
        return
    if which is None or which.lower() == "all":
        targets = pendings
    else:
        idx = int(which)
        if not 1 <= idx <= len(pendings):
            ctx.reply(f"Please pick a file number between 1 and {len(pendings)}.")
            return
        targets = [pendings[idx - 1]]
    for p in targets:
        doc = db.get(Document, p.document_id)
        if doc is None:
            continue
        if skip:
            p.status = PendingStatus.resolved
            enqueue_processing(db, doc)
        else:
            p.status = PendingStatus.cancelled
            doc.status = DocumentStatus.rejected
            doc.failure_reason = "cancelled by sender"
            if doc.blob_id:
                blob = db.get(MediaBlob, doc.blob_id)
                doc.blob_id = None
                if blob:
                    db.delete(blob)
    n = len(targets)
    ctx.reply(
        f"👍 Saving {n} file(s) without a description." if skip else f"🗑️ Discarded {n} file(s)."
    )

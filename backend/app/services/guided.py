"""Interactive WhatsApp features: numbered menu, folder choice, and admin rename/delete.

Rules (see docs/architecture/conversation.md):
- Guided steps live in `conversation_states` (one per sender, short TTL), never in memory.
- Rename and delete are admin-only; the role is checked here and again when the job runs.
- Delete always needs an explicit "YES" reply; the storage call happens in a job, and the
  "done" reply is only queued after the storage provider has confirmed it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.db.base import utcnow
from app.models import ConversationState, Document, DocumentStatus, SenderRole
from app.services import audit, search
from app.services.naming import sanitize_component
from app.workers import queue

if TYPE_CHECKING:
    from app.services.conversation import Ctx

STATE_TTL = timedelta(minutes=10)
CONFIRM_TTL = timedelta(minutes=5)
MAX_FOLDER_DEPTH = 3

MENU = re.compile(r"^\s*(menu|hi|hello|hey|hii|start|namaste|options)\s*[!.]?\s*$", re.I)
FOLDERS = re.compile(r"^\s*(folders?|list folders|show folders)\s*$", re.I)
RENAME = re.compile(r"^\s*rename\s+#?(\d{1,3})\s*(?:(?:to|as)\b|->|:)?\s*(.+)$", re.I | re.S)
RENAME_BARE = re.compile(r"^\s*rename\s*$", re.I)
DELETE = re.compile(r"^\s*(?:delete|remove)\s+#?(\d{1,3})\s*$", re.I)
DELETE_BARE = re.compile(r"^\s*(?:delete|remove)\s*$", re.I)
YES = re.compile(r"^\s*(yes|y|confirm|haan|ha|ji haan|delete it)\s*[!.]?\s*$", re.I)
NO = re.compile(r"^\s*(no|n|cancel|stop|nahi|na|keep it)\s*[!.]?\s*$", re.I)
NUMBER = re.compile(r"^\s*#?(\d{1,3})\s*$")
# "#Bank Statements/2026: rest" (folder names may contain spaces when ended by a colon)
FOLDER_TAG_COLON = re.compile(r"^\s*#([^\s#:][^#:\n]*):\s*(.*)$", re.S)
# "#Invoices/2026 rest of the description" (folder ends at the first space)
FOLDER_TAG = re.compile(r"^\s*#([^\s#][^#\n]*?)(?:\s+(?=\S)|$)(.*)$", re.S)

ADMIN_ONLY = "⛔ Only WhatsApp admins of this workspace can rename or delete documents."


# ---- state ----------------------------------------------------------------------------------
def get_state(ctx: Ctx) -> ConversationState | None:
    st = ctx.db.scalar(
        select(ConversationState).where(
            ConversationState.tenant_id == ctx.tenant.id,
            ConversationState.connection_id == ctx.conn.id,
            ConversationState.sender == ctx.sender,
        )
    )
    if st is not None and st.expires_at <= utcnow():
        ctx.db.delete(st)
        ctx.db.flush()
        return None
    return st


def set_state(
    ctx: Ctx, kind: str, data: dict[str, Any] | None = None, ttl: timedelta = STATE_TTL
) -> None:
    st = ctx.db.scalar(
        select(ConversationState).where(
            ConversationState.tenant_id == ctx.tenant.id,
            ConversationState.connection_id == ctx.conn.id,
            ConversationState.sender == ctx.sender,
        )
    )
    if st is None:
        st = ConversationState(
            tenant_id=ctx.tenant.id, connection_id=ctx.conn.id, sender=ctx.sender, kind=kind
        )
        ctx.db.add(st)
    st.kind = kind
    st.data = data or {}
    st.expires_at = utcnow() + ttl
    ctx.db.flush()


def clear_state(ctx: Ctx) -> None:
    st = get_state(ctx)
    if st is not None:
        ctx.db.delete(st)
        ctx.db.flush()


# ---- folders --------------------------------------------------------------------------------
@dataclass
class FolderCaption:
    folder: list[str] | None
    text: str


def parse_folder_caption(text: str | None) -> FolderCaption:
    """Split "#Invoices/2026 receipt from ABC" into (["Invoices", "2026"], "receipt from ABC").

    "#Bank Statements/2026: March" keeps spaces in folder names (colon ends the folder).
    """
    if not text:
        return FolderCaption(None, "")
    m = FOLDER_TAG_COLON.match(text) or FOLDER_TAG.match(text)
    if not m:
        return FolderCaption(None, text)
    parts = [sanitize_component(p, 60) for p in m.group(1).split("/")]
    parts = [p for p in parts if p][:MAX_FOLDER_DEPTH]
    if not parts:
        return FolderCaption(None, text)
    return FolderCaption(parts, m.group(2).strip())


def folders_text(ctx: Ctx) -> str:
    rows = ctx.db.execute(
        select(Document.folder, func.count())
        .where(
            Document.tenant_id == ctx.tenant.id,
            Document.status == DocumentStatus.stored,
            Document.folder.is_not(None),
        )
        .group_by(Document.folder)
        .order_by(Document.folder)
        .limit(40)
    ).all()
    if not rows:
        return (
            "📁 No folders yet. Send a file with a caption like *#Invoices* to save it into "
            "a folder of your choice."
        )
    lines = [f"• {folder} ({n})" for folder, n in rows]
    return (
        "📁 *Folders*\n"
        + "\n".join(lines)
        + "\n\nTo save into a folder, send the file with a caption like *#Invoices/2026*."
    )


# ---- menu -----------------------------------------------------------------------------------
def menu_text(ctx: Ctx) -> str:
    lines = [
        "👋 *Flow Chain Documents* – what would you like to do?",
        "",
        "1️⃣ Show my latest documents",
        "2️⃣ Find a document",
        "3️⃣ Folders",
        "4️⃣ How to save a file",
    ]
    if ctx.role == SenderRole.admin:
        lines += ["5️⃣ Rename a document", "6️⃣ Delete a document"]
    lines += ["", "Reply with a number. You can also just send a file at any time."]
    return "\n".join(lines)


SAVE_HELP = (
    "📤 *Saving files*\n"
    "• Send a photo, PDF or Office file with a short caption, e.g. _invoice from ABC Traders_.\n"
    "• To choose the folder, start the caption with *#*: _#Invoices/2026 bill from ABC_.\n"
    "  Folder names with spaces end with a colon: _#Bank Statements: March_.\n"
    "• Without a caption I'll ask what the file is (reply *skip* to save it anyway)."
)


def show_menu(ctx: Ctx) -> None:
    set_state(ctx, "menu")
    ctx.reply(menu_text(ctx), "menu")


def _latest(ctx: Ctx, key: str = "latest") -> search.SearchPage:
    categories: list[str] = []
    filters = search.parse_query("show my latest documents", categories) or search.SearchFilters()
    page = search.create_search_session(
        ctx.db,
        tenant_id=ctx.tenant.id,
        connection_id=ctx.conn.id,
        sender=ctx.sender,
        query_text="latest documents",
        filters=filters,
    )
    ctx.reply(search.format_page(page, filters), key)
    return page


def _start_pick(ctx: Ctx, action: str) -> None:
    if ctx.role != SenderRole.admin:
        ctx.reply(ADMIN_ONLY, "denied")
        return
    session = search.active_session(ctx.db, ctx.tenant.id, ctx.conn.id, ctx.sender)
    if session is None or session.total == 0:
        page = _latest(ctx, "pick-list")
        if page.total == 0:
            clear_state(ctx)
            return
    set_state(ctx, f"pick_{action}")
    verb = "rename" if action == "rename" else "delete"
    ctx.reply(f"Which document should I {verb}? Reply with its number from the list.", "pick")


def _menu_choice(ctx: Ctx, n: int) -> bool:
    if n == 1:
        clear_state(ctx)
        _latest(ctx)
    elif n == 2:
        set_state(ctx, "ask_search")
        ctx.reply(
            "🔍 What are you looking for? e.g. _invoices from ABC Traders_ or "
            "_documents from September 2026_.",
            "ask-search",
        )
    elif n == 3:
        clear_state(ctx)
        ctx.reply(folders_text(ctx), "folders")
    elif n == 4:
        clear_state(ctx)
        ctx.reply(SAVE_HELP, "save-help")
    elif n in (5, 6) and ctx.role == SenderRole.admin:
        _start_pick(ctx, "rename" if n == 5 else "delete")
    else:
        return False
    return True


# ---- rename / delete ------------------------------------------------------------------------
def _picked_document(ctx: Ctx, number: int) -> Document | None:
    session = search.active_session(ctx.db, ctx.tenant.id, ctx.conn.id, ctx.sender)
    if session is None:
        ctx.reply("⌛ That list has expired. Please search again, e.g. _show my latest documents_.")
        return None
    result = search.select_result(ctx.db, session, number) if number >= 1 else None
    if result is None:
        shown = search.shown_count(ctx.db, session)
        ctx.reply(f"Please reply with a number between 1 and {shown} from the list.")
        return None
    doc = ctx.db.scalar(
        select(Document).where(
            Document.id == result.document_id, Document.tenant_id == ctx.tenant.id
        )
    )
    if doc is None or doc.status != DocumentStatus.stored or not doc.storage_file_id:
        ctx.reply("Sorry, that document is no longer available.")
        return None
    return doc


def _doc_name(doc: Document) -> str:
    return doc.standardized_filename or doc.original_filename


def _ask_new_name(ctx: Ctx, doc: Document) -> None:
    set_state(ctx, "rename_name", {"document_id": str(doc.id)})
    ctx.reply(
        f"✏️ New name for *{_doc_name(doc)}*? (the .{doc.extension} extension is kept)",
        "ask-name",
    )


def _ask_delete(ctx: Ctx, doc: Document) -> None:
    set_state(ctx, "confirm_delete", {"document_id": str(doc.id)}, CONFIRM_TTL)
    ctx.reply(
        f"⚠️ Delete *{_doc_name(doc)}*"
        + (f" from 📁 {doc.folder}" if doc.folder else "")
        + "?\nIt goes to the storage recycle bin. Reply *YES* to delete or *NO* to keep it.",
        "confirm-delete",
    )


def _enqueue_admin_job(ctx: Ctx, kind: str, doc: Document, extra: dict[str, Any]) -> None:
    queue.enqueue(
        ctx.db,
        kind,
        {
            "document_id": str(doc.id),
            "connection_id": str(ctx.conn.id),
            "to": ctx.sender,
            "inbound_event_id": str(ctx.event.id),
            **extra,
        },
        tenant_id=ctx.tenant.id,
        idempotency_key=f"{kind}:{ctx.event.id}",
        document_id=doc.id,
        max_attempts=5,
    )
    audit.record(
        ctx.db,
        f"document.{kind}_requested_whatsapp",
        tenant_id=ctx.tenant.id,
        actor_type="whatsapp",
        target_type="document",
        target_id=doc.id,
    )


def _rename(ctx: Ctx, doc: Document, new_name: str) -> None:
    stem = sanitize_component(new_name.strip(), 120)
    if len(stem) < 2:
        ctx.reply("Please send a name with at least 2 letters or digits.")
        return
    clear_state(ctx)
    _enqueue_admin_job(ctx, "rename_document", doc, {"new_stem": stem})
    ctx.reply(f"✏️ Renaming *{_doc_name(doc)}*…", "renaming", doc.id)


def _doc_by_id(ctx: Ctx, raw_id: Any) -> Document | None:
    try:
        doc_id = uuid.UUID(str(raw_id))
    except ValueError:
        return None
    return ctx.db.scalar(
        select(Document).where(Document.id == doc_id, Document.tenant_id == ctx.tenant.id)
    )


# ---- routing --------------------------------------------------------------------------------
def handle(ctx: Ctx, text: str) -> bool:
    """Handle guided-chat input. Returns True if the message was consumed."""
    st = get_state(ctx)

    if st is not None and st.kind == "confirm_delete":
        if YES.match(text):
            doc = _doc_by_id(ctx, st.data.get("document_id"))
            clear_state(ctx)
            if ctx.role != SenderRole.admin:
                ctx.reply(ADMIN_ONLY, "denied")
            elif doc is None or doc.status != DocumentStatus.stored:
                ctx.reply("Sorry, that document is no longer available.")
            else:
                _enqueue_admin_job(ctx, "delete_document", doc, {})
                ctx.reply(f"🗑️ Deleting *{_doc_name(doc)}*…", "deleting", doc.id)
            return True
        if NO.match(text):
            clear_state(ctx)
            ctx.reply("👍 Kept it. Nothing was deleted.", "kept")
            return True
        clear_state(ctx)  # moved on to something else: the delete is not confirmed

    if st is not None and st.kind == "rename_name" and not (MENU.match(text) or NO.match(text)):
        doc = _doc_by_id(ctx, st.data.get("document_id"))
        if doc is None or doc.status != DocumentStatus.stored:
            clear_state(ctx)
            ctx.reply("Sorry, that document is no longer available.")
        elif ctx.role != SenderRole.admin:
            clear_state(ctx)
            ctx.reply(ADMIN_ONLY, "denied")
        else:
            _rename(ctx, doc, text)
        return True
    if st is not None and st.kind == "rename_name" and NO.match(text):
        clear_state(ctx)
        ctx.reply("👍 Rename cancelled.", "kept")
        return True

    if MENU.match(text):
        show_menu(ctx)
        return True
    if FOLDERS.match(text):
        ctx.reply(folders_text(ctx), "folders")
        return True

    if m := RENAME.match(text):
        if ctx.role != SenderRole.admin:
            ctx.reply(ADMIN_ONLY, "denied")
            return True
        doc = _picked_document(ctx, int(m.group(1)))
        if doc is not None:
            _rename(ctx, doc, m.group(2))
        return True
    if m := DELETE.match(text):
        if ctx.role != SenderRole.admin:
            ctx.reply(ADMIN_ONLY, "denied")
            return True
        doc = _picked_document(ctx, int(m.group(1)))
        if doc is not None:
            _ask_delete(ctx, doc)
        return True
    if RENAME_BARE.match(text) or DELETE_BARE.match(text):
        _start_pick(ctx, "rename" if RENAME_BARE.match(text) else "delete")
        return True

    if st is None:
        return False
    if st.kind == "menu" and (m := NUMBER.match(text)):
        if _menu_choice(ctx, int(m.group(1))):
            return True
        ctx.reply("Please reply with one of the numbers from the menu.", "menu-invalid")
        return True
    if st.kind in ("pick_rename", "pick_delete") and (m := NUMBER.match(text)):
        if ctx.role != SenderRole.admin:
            clear_state(ctx)
            ctx.reply(ADMIN_ONLY, "denied")
            return True
        doc = _picked_document(ctx, int(m.group(1)))
        if doc is not None:
            (_ask_new_name if st.kind == "pick_rename" else _ask_delete)(ctx, doc)
        return True
    if st.kind == "ask_search":
        clear_state(ctx)
        from app.services.conversation import run_free_text_search  # avoid import cycle

        run_free_text_search(ctx, text)
        return True
    return False

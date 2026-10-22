"""Deterministic natural-language document search over PostgreSQL metadata.

The parser turns a WhatsApp message into a fixed `SearchFilters` structure; the query builder
only ever uses bound parameters and always constrains `tenant_id`. No AI-generated SQL.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utcnow
from app.models import Document, DocumentStatus, SearchResult, SearchSession, SearchStatus
from app.services.dates import parse_date_or_period

SEARCH_VERBS = re.compile(
    r"^\s*(?:please\s+)?(?:find|search(?:\s+for)?|show(?:\s+me)?|list|get|send(?:\s+me)?|"
    r"give(?:\s+me)?|fetch|look\s+for|where\s+is|retrieve)\b",
    re.I,
)
LATEST = re.compile(r"\b(latest|recent|newest|last\s+(\d{1,2})|most\s+recent)\b", re.I)
ALL_DOCS = re.compile(r"\b(all|every|my)\s+(documents?|docs?|files?|uploads?)\b", re.I)
GENERIC_NOUN = re.compile(r"\b(documents?|docs?|files?|uploads?)\b", re.I)
FILENAME = re.compile(r"(?:named|called|filename|file\s+name)\s+[\"']?([^\"']{2,80})[\"']?", re.I)
QUOTED = re.compile(r"[\"“]([^\"”]{2,80})[\"”]")
COMPANY = re.compile(
    r"\b(?:from|by|of|for|issued\s+by|with)\s+(?:the\s+)?([A-Za-z0-9&.'\- ]{2,80})", re.I
)
RELATIVE = re.compile(r"\b(this|last|previous)\s+(month|year)\b|\b(today|yesterday)\b", re.I)

CATEGORY_ALIASES: dict[str, list[str]] = {
    "Cheque": ["cheques", "cheque", "checks", "chq"],
    "Invoice": ["invoices", "invoice", "bills of sale", "inv"],
    "Receipt": ["receipts", "receipt"],
    "Contract": ["contracts", "contract", "agreements", "agreement"],
    "Bill": ["bills", "bill"],
    "Purchase Order": ["purchase orders", "purchase order", "pos", "po"],
    "Bank Statement": ["bank statements", "bank statement", "statements", "statement"],
    "ID/KYC": ["kyc", "id proofs", "id proof", "ids", "id"],
    "Report": ["reports", "report"],
    "Certificate": ["certificates", "certificate", "certs"],
    "Other": ["other documents", "others"],
}


@dataclass
class SearchFilters:
    category: str | None = None
    company: str | None = None
    start: date | None = None
    end: date | None = None
    period_label: str | None = None
    filename: str | None = None
    latest: bool = False
    limit: int | None = None

    def is_empty(self) -> bool:
        return not any([self.category, self.company, self.start, self.filename])

    def to_json(self) -> dict[str, object]:
        d = asdict(self)
        d["start"] = self.start.isoformat() if self.start else None
        d["end"] = self.end.isoformat() if self.end else None
        return d

    @classmethod
    def from_json(cls, d: dict[str, object]) -> SearchFilters:
        data = dict(d)
        for k in ("start", "end"):
            if data.get(k):
                data[k] = date.fromisoformat(str(data[k]))
        return cls(**data)  # type: ignore[arg-type]

    def describe(self) -> str:
        bits = []
        if self.category:
            bits.append(self.category)
        if self.company:
            bits.append(f"from “{self.company}”")
        if self.period_label:
            bits.append(f"for {self.period_label}")
        if self.filename:
            bits.append(f"named “{self.filename}”")
        return " ".join(bits) or "all documents"


def _relative_period(m: re.Match[str], today: date) -> tuple[date, date, str]:
    if m.group(3):
        d = today if m.group(3).lower() == "today" else today - timedelta(days=1)
        return d, d, d.isoformat()
    which, unit = m.group(1).lower(), m.group(2).lower()
    if unit == "month":
        first = today.replace(day=1)
        if which != "this":
            first = (first - timedelta(days=1)).replace(day=1)
        nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        return first, nxt - timedelta(days=1), first.strftime("%Y-%m")
    year = today.year if which == "this" else today.year - 1
    return date(year, 1, 1), date(year, 12, 31), str(year)


def parse_query(
    text: str, categories: list[str], *, today: date | None = None, require_intent: bool = False
) -> SearchFilters | None:
    """Return filters if the message is a search request, else None."""
    raw = text.strip()
    if not raw or len(raw) > 300:
        return None
    has_verb = bool(SEARCH_VERBS.match(raw))
    working = SEARCH_VERBS.sub(" ", raw, count=1)
    f = SearchFilters()

    m = FILENAME.search(working) or QUOTED.search(working)
    if m:
        f.filename = m.group(1).strip()
        working = working.replace(m.group(0), " ")

    m_lat = LATEST.search(working)
    if m_lat:
        f.latest = True
        if m_lat.group(2):
            f.limit = max(1, min(int(m_lat.group(2)), 50))
        working = working.replace(m_lat.group(0), " ")

    rel = RELATIVE.search(working)
    if rel:
        f.start, f.end, f.period_label = _relative_period(rel, today or date.today())
        working = working.replace(rel.group(0), " ")
    else:
        period = parse_date_or_period(working)
        if period and period.start:
            f.start, f.end, f.period_label = period.start, period.end, period.period
            working = _strip_date_words(working)

    lowered = f" {working.lower()} "
    for cat in categories:
        aliases = [*CATEGORY_ALIASES.get(cat, []), cat.lower() + "s", cat.lower()]
        for alias in sorted(aliases, key=len, reverse=True):
            if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", lowered):
                f.category = cat
                working = re.sub(
                    rf"(?i)(?<![a-z]){re.escape(alias)}(?![a-z])", " ", working, count=1
                )
                break
        if f.category:
            break

    m = COMPANY.search(working)
    if m:
        company = re.sub(r"\s+", " ", m.group(1)).strip(" .,-'")
        company = re.sub(r"(?i)^(the|my|our)\s+", "", company)
        company = GENERIC_NOUN.sub("", company).strip(" .,-'")
        if len(company) >= 2 and company.lower() not in {"me", "us", "all", "my"}:
            f.company = company[:80]

    is_all = bool(ALL_DOCS.search(raw)) or bool(GENERIC_NOUN.search(raw))
    if f.is_empty() and not f.latest and not (has_verb and is_all):
        return None
    if require_intent and not has_verb:
        return None
    if not has_verb and f.category is None and not f.latest and not is_all:
        # e.g. "from ABC" alone is too ambiguous without a verb or a document noun.
        return None
    return f


def _strip_date_words(text: str) -> str:
    from app.services.dates import _DMY, _FY, _ISO, _MON_Y, _YEAR, _YM

    for regex in (_ISO, _DMY, _FY, _MON_Y, _YM, _YEAR):
        text = regex.sub(" ", text)
    text = re.sub(r"(?i)\b(in|on|during|dated|for|from)\s*$", " ", text.strip())
    return re.sub(r"(?i)\b(in|on|during|dated)\s+(?=\s|$)", " ", text)


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def build_query(tenant_id: uuid.UUID, f: SearchFilters) -> Select[Document]:
    q = select(Document).where(
        Document.tenant_id == tenant_id, Document.status == DocumentStatus.stored
    )
    if f.category:
        q = q.where(func.lower(Document.document_type) == f.category.lower())
    if f.company:
        q = q.where(Document.company_or_person.ilike(_like(f.company), escape="\\"))
    if f.filename:
        pattern = _like(f.filename)
        q = q.where(
            or_(
                Document.standardized_filename.ilike(pattern, escape="\\"),
                Document.original_filename.ilike(pattern, escape="\\"),
                Document.description.ilike(pattern, escape="\\"),
            )
        )
    if f.start and f.end:
        q = q.where(Document.period_start <= f.end, Document.period_end >= f.start)
    return q


def run_search(
    db: Session, tenant_id: uuid.UUID, f: SearchFilters, *, offset: int, limit: int
) -> tuple[int, list[Document]]:
    base = build_query(tenant_id, f)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    if f.limit:
        total = min(total, f.limit)
        limit = max(0, min(limit, f.limit - offset))
    docs = list(
        db.scalars(
            base.order_by(Document.created_at.desc(), Document.id).offset(offset).limit(limit)
        )
    )
    return total, docs


def result_label(doc: Document) -> str:
    parts = [doc.document_type or "Document"]
    if doc.company_or_person:
        parts.append(doc.company_or_person)
    if doc.date_or_period:
        parts.append(doc.date_or_period)
    name = doc.standardized_filename or doc.original_filename
    return f"{' · '.join(parts)} ({name})"[:300]


@dataclass
class SearchPage:
    session: SearchSession
    total: int
    items: list[tuple[int, str]]
    page: int
    page_size: int


def create_search_session(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    sender: str,
    query_text: str,
    filters: SearchFilters,
) -> SearchPage:
    s = get_settings()
    # Only one active list per sender: older lists can never be selected from again.
    db.execute(
        update(SearchSession)
        .where(
            SearchSession.tenant_id == tenant_id,
            SearchSession.connection_id == connection_id,
            SearchSession.sender == sender,
            SearchSession.status == SearchStatus.active,
        )
        .values(status=SearchStatus.superseded)
    )
    session = SearchSession(
        tenant_id=tenant_id,
        connection_id=connection_id,
        sender=sender,
        query_text=query_text[:500],
        filters=filters.to_json(),
        page=0,
        total=0,
        status=SearchStatus.active,
        expires_at=utcnow() + timedelta(minutes=s.search_session_ttl_minutes),
    )
    db.add(session)
    db.flush()
    return next_page(db, session)


def next_page(db: Session, session: SearchSession) -> SearchPage:
    s = get_settings()
    size = s.search_page_size
    filters = SearchFilters.from_json(session.filters)
    offset = session.page * size
    total, docs = run_search(db, session.tenant_id, filters, offset=offset, limit=size)
    items: list[tuple[int, str]] = []
    for i, doc in enumerate(docs, start=offset + 1):
        label = result_label(doc)
        db.add(
            SearchResult(
                search_session_id=session.id,
                tenant_id=session.tenant_id,
                position=i,
                document_id=doc.id,
                label=label,
            )
        )
        items.append((i, label))
    session.page += 1
    session.total = total
    session.expires_at = utcnow() + timedelta(minutes=s.search_session_ttl_minutes)
    db.flush()
    return SearchPage(session=session, total=total, items=items, page=session.page, page_size=size)


def active_session(
    db: Session, tenant_id: uuid.UUID, connection_id: uuid.UUID, sender: str
) -> SearchSession | None:
    return db.scalar(
        select(SearchSession)
        .where(
            SearchSession.tenant_id == tenant_id,
            SearchSession.connection_id == connection_id,
            SearchSession.sender == sender,
            SearchSession.status == SearchStatus.active,
            SearchSession.expires_at > utcnow(),
        )
        .order_by(SearchSession.created_at.desc())
        .limit(1)
    )


def recently_expired_session(
    db: Session, tenant_id: uuid.UUID, connection_id: uuid.UUID, sender: str
) -> SearchSession | None:
    return db.scalar(
        select(SearchSession)
        .where(
            SearchSession.tenant_id == tenant_id,
            SearchSession.connection_id == connection_id,
            SearchSession.sender == sender,
            SearchSession.status.in_([SearchStatus.active, SearchStatus.expired]),
            SearchSession.expires_at <= utcnow(),
            SearchSession.expires_at > utcnow() - timedelta(hours=24),
        )
        .order_by(SearchSession.created_at.desc())
        .limit(1)
    )


def select_result(db: Session, session: SearchSession, position: int) -> SearchResult | None:
    return db.scalar(
        select(SearchResult).where(
            SearchResult.search_session_id == session.id,
            SearchResult.tenant_id == session.tenant_id,
            SearchResult.position == position,
        )
    )


def shown_count(db: Session, session: SearchSession) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(SearchResult)
            .where(SearchResult.search_session_id == session.id)
        )
        or 0
    )


def format_page(page: SearchPage, filters: SearchFilters) -> str:
    if page.total == 0:
        return (
            f"🔎 No documents found for {filters.describe()}.\n"
            "Try a different name, type or month — e.g. *find invoices from ABC Traders*."
        )
    if not page.items:
        return "That's the end of the list. Send a new search any time."
    first, last = page.items[0][0], page.items[-1][0]
    lines = [f"🔎 {page.total} result(s) for {filters.describe()} — showing {first}-{last}:"]
    lines += [f"{n}. {label}" for n, label in page.items]
    footer = "\nReply with a number to get the file"
    if last < page.total:
        footer += ", or *more* for the next page"
    footer += f". This list expires in {get_settings().search_session_ttl_minutes} min."
    return "\n".join(lines) + footer

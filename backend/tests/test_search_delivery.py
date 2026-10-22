"""WhatsApp search, numbered selection, expiry, delivery via signed URLs, and isolation."""

from __future__ import annotations

import re
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import Document, DocumentStatus, Message, SearchSession
from app.services.search import SearchFilters, run_search
from tests.conftest import World, drain, make_world, post_events, sent_texts, text_event

DOCS = [
    ("Invoice", "ABC Traders", date(2026, 9, 15), "INV-ABC-0915.pdf"),
    ("Invoice", "ABC Traders", date(2026, 8, 2), "INV-ABC-0802.pdf"),
    ("Invoice", "XYZ Corp", date(2026, 9, 20), "INV-XYZ.pdf"),
    ("Receipt", "Dev Construction", date(2026, 9, 1), "RCPT-DEV.pdf"),
    ("Bill", "Power Co", date(2025, 12, 31), "BILL-PWR.pdf"),
]


def seed(db: Session, world: World, extra: int = 0) -> list[Document]:
    out = []
    rows = DOCS + [("Report", f"Vendor {i}", date(2026, 1, 1), f"R{i}.pdf") for i in range(extra)]
    for i, (typ, company, d, name) in enumerate(rows):
        data = f"content-{name}".encode()
        file = world.fake_storage.upload(
            data=data,
            filename=name,
            mime_type="application/pdf",
            folder_id="f",
            document_id=f"seed-{i}",
        )
        doc = Document(
            tenant_id=world.tenant.id,
            connection_id=world.conn.id,
            status=DocumentStatus.stored,
            original_filename=name,
            extension="pdf",
            mime_type="application/pdf",
            size_bytes=len(data),
            sha256=f"{i:064d}",
            document_type=typ,
            company_or_person=company,
            doc_date=d,
            period_start=d,
            period_end=d,
            date_or_period=d.isoformat(),
            standardized_filename=name,
            storage_provider="fake",
            storage_file_id=file.file_id,
            created_at=utcnow() - timedelta(minutes=len(rows) - i),
        )
        db.add(doc)
        out.append(doc)
    db.commit()
    return out


def ask(client: TestClient, world: World, text: str) -> str:
    post_events(client, world, text_event(world.sender, text))
    drain()
    return sent_texts(world)[-1]


def test_find_all_and_by_type_company_date(client: TestClient, db: Session, world: World) -> None:
    seed(db, world)
    assert "5 result(s)" in ask(client, world, "Find all documents")
    r = ask(client, world, "Find all invoices")
    assert "3 result(s)" in r and "RCPT" not in r
    r = ask(client, world, "Find invoices from ABC Traders")
    assert "2 result(s)" in r and "XYZ" not in r
    r = ask(client, world, "Find documents from September 2026")
    assert "3 result(s)" in r and "INV-ABC-0802" not in r and "BILL" not in r
    r = ask(client, world, "Find the receipt from Dev Construction")
    assert "1 result(s)" in r and "RCPT-DEV" in r
    r = ask(client, world, "find invoices from ABC Traders September 2026")
    assert "1 result(s)" in r and "INV-ABC-0915" in r
    r = ask(client, world, 'find invoice named "XYZ"')
    assert "1 result(s)" in r


def test_latest_and_no_results(client: TestClient, db: Session, world: World) -> None:
    seed(db, world)
    r = ask(client, world, "Show my latest documents")
    lines = [ln for ln in r.splitlines() if re.match(r"^\d+\. ", ln)]
    assert lines[0].endswith("(BILL-PWR.pdf)")  # newest first
    assert "No documents found" in ask(client, world, "find contracts from Nobody Ltd")
    r = ask(client, world, "show last 2 invoices")
    assert "2 result(s)" in r


def test_pagination_with_more(client: TestClient, db: Session, world: World) -> None:
    seed(db, world, extra=20)
    r = ask(client, world, "find all documents")
    assert "25 result(s)" in r and "showing 1-10" in r and "*more*" in r
    assert "showing 11-20" in ask(client, world, "more")
    r = ask(client, world, "more")
    assert "showing 21-25" in r and "*more*" not in r
    assert "end of the list" in ask(client, world, "more")
    # numbering is global: item 15 is selectable after paging
    assert "Sending" in ask(client, world, "15")


def test_valid_selection_delivers_via_signed_url(
    client: TestClient, db: Session, world: World
) -> None:
    seed(db, world)
    ask(client, world, "find invoices from ABC Traders")
    # Results are newest-upload first; INV-ABC-0802 was seeded after INV-ABC-0915.
    assert "Sending *INV-ABC-0802.pdf*" in ask(client, world, "1")
    [sent_doc] = [m for m in world.fake_wa.sent if m["type"] == "document"]
    assert sent_doc["filename"] == "INV-ABC-0802.pdf" and sent_doc["to"] == world.sender
    url = sent_doc["url"]
    assert url.startswith("https://api.example.test/api/v1/media/")
    assert "drive.google" not in url
    r = client.get(url.replace("https://api.example.test", ""))
    assert r.status_code == 200 and r.content == b"content-INV-ABC-0802.pdf"
    assert r.headers["content-type"] == "application/pdf"
    assert "INV-ABC-0802.pdf" in r.headers["content-disposition"]
    msg = db.scalar(select(Message).where(Message.kind == "document"))
    assert msg is not None and msg.status == "sent"


def test_signed_url_tampering_and_expiry(client: TestClient, db: Session, world: World) -> None:
    seed(db, world)
    ask(client, world, "find all invoices")
    ask(client, world, "1")
    url = next(m["url"] for m in world.fake_wa.sent if m["type"] == "document").replace(
        "https://api.example.test", ""
    )
    payload, sig = url.rsplit("/", 1)[1].split(".")
    assert client.get(f"/api/v1/media/{payload}.{'0' * len(sig)}").status_code == 404
    assert client.get(f"/api/v1/media/{payload[:-2]}xx.{sig}").status_code == 404
    from app.models import MediaBlob

    db.execute(update(MediaBlob).values(expires_at=utcnow() - timedelta(seconds=1)))
    db.commit()
    assert client.get(url).status_code == 404


def test_invalid_selection_numbers(client: TestClient, db: Session, world: World) -> None:
    seed(db, world)
    ask(client, world, "find all invoices")
    assert "between 1 and 3" in ask(client, world, "7")
    assert "between 1 and 3" in ask(client, world, "0")
    assert not [m for m in world.fake_wa.sent if m["type"] == "document"]


def test_number_without_any_search_is_not_a_selection(
    client: TestClient, db: Session, world: World
) -> None:
    seed(db, world)
    assert "no result list" in ask(client, world, "2")
    assert not [m for m in world.fake_wa.sent if m["type"] == "document"]


def test_expired_selection_not_used(client: TestClient, db: Session, world: World) -> None:
    seed(db, world)
    ask(client, world, "find all invoices")
    db.execute(update(SearchSession).values(expires_at=utcnow() - timedelta(seconds=1)))
    db.commit()
    assert "expired" in ask(client, world, "1")
    assert not [m for m in world.fake_wa.sent if m["type"] == "document"]
    # unrelated text after expiry goes to normal handling
    assert "didn't understand" in ask(client, world, "thanks a lot")


def test_new_search_supersedes_old_list(client: TestClient, db: Session, world: World) -> None:
    seed(db, world)
    ask(client, world, "find all invoices")  # 3 results
    ask(client, world, "find receipts")  # 1 result
    assert "between 1 and 1" in ask(client, world, "3")


def test_duplicate_filenames_resolved_by_position(
    client: TestClient, db: Session, world: World
) -> None:
    docs = seed(db, world)
    for d in docs[:2]:
        d.standardized_filename = "same-name.pdf"
    db.commit()
    ask(client, world, "find invoices from ABC Traders")
    ask(client, world, "2")
    sent = [m for m in world.fake_wa.sent if m["type"] == "document"]
    r = client.get(sent[0]["url"].replace("https://api.example.test", ""))
    assert r.content == b"content-INV-ABC-0915.pdf"  # position 2 = older upload


def test_search_isolated_between_senders_and_tenants(client: TestClient, db: Session) -> None:
    a = make_world(db, "Search A", sender="919822220001")
    b = make_world(db, "Search B", sender="919822220002")
    seed(db, b)
    assert "No documents found" in ask(client, a, "find all invoices") or "0 result" in ask(
        client, a, "find all invoices"
    )
    # Another authorized sender in tenant B cannot select from the first sender's list.
    from app.services.tenancy import add_authorized_sender

    add_authorized_sender(db, b.tenant.id, "919822220003")
    db.commit()
    ask(client, b, "find all invoices")
    post_events(client, b, text_event("919822220003", "1"))
    drain()
    assert "no result list" in sent_texts(b)[-1]
    assert not [m for m in b.fake_wa.sent if m["type"] == "document" and m["to"] == "919822220003"]


def test_run_search_tenant_scoped_and_like_escaped(db: Session, world: World) -> None:
    seed(db, world)
    other = make_world(db, "Other T", sender="919833330000")
    seed(db, other)
    total, docs = run_search(db, world.tenant.id, SearchFilters(company="%"), offset=0, limit=50)
    assert total == 0
    total, docs = run_search(db, world.tenant.id, SearchFilters(), offset=0, limit=50)
    assert total == 5 and all(d.tenant_id == world.tenant.id for d in docs)


def test_delivery_failure_reports_and_does_not_lie(
    client: TestClient, db: Session, world: World
) -> None:
    from app.providers.errors import ProviderError

    seed(db, world)
    ask(client, world, "find all invoices")
    for _ in range(5):
        world.fake_wa.fail_next_sends.append(
            ProviderError("HTTP 400 invalid url", provider="fake", transient=False)
        )
    post_events(client, world, text_event(world.sender, "1"))
    drain()
    msg = db.scalar(select(Message).where(Message.kind == "document"))
    assert msg is not None and msg.status == "failed"


def test_revoked_sender_cannot_receive(client: TestClient, db: Session, world: World) -> None:
    from app.models import AuthorizedSender

    seed(db, world)
    ask(client, world, "find all invoices")
    post_events(client, world, text_event(world.sender, "1"))
    db.execute(update(AuthorizedSender).values(is_active=False))
    db.commit()
    drain()
    assert not [m for m in world.fake_wa.sent if m["type"] == "document"]

"""End-to-end document intake pipeline: webhook -> worker -> extraction -> classification ->
storage -> confirmation. External services are the labelled FAKE providers."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import (
    AuditEvent,
    Document,
    DocumentStatus,
    InboundEvent,
    Job,
    JobStatus,
    MediaBlob,
    PendingMedia,
    Tenant,
)
from app.providers import registry
from app.providers.ai.base import (
    ClassificationInput,
    ClassifierError,
    DocumentClassifier,
    RawClassification,
)
from app.providers.errors import ProviderError
from app.services.maintenance import sweep
from tests import synthetic as syn
from tests.conftest import (
    World,
    drain,
    make_world,
    media_event,
    post_events,
    sent_texts,
    text_event,
)

INVOICE_PDF = syn.pdf_with_text(["TAX INVOICE", "ABC Traders", "Invoice date 15/09/2026"])


def docs(db: Session, world: World) -> list[Document]:
    db.expire_all()
    return list(
        db.scalars(
            select(Document)
            .where(Document.tenant_id == world.tenant.id)
            .order_by(Document.created_at)
        )
    )


def test_pdf_with_caption_is_stored_and_confirmed(
    client: TestClient, db: Session, world: World
) -> None:
    ack = post_events(
        client,
        world,
        media_event(
            world.sender,
            INVOICE_PDF,
            filename="scan 001.pdf",
            mime="application/pdf",
            caption="invoice from ABC Traders",
        ),
    )
    assert ack["accepted"] == 1
    # Nothing slow happened inside the request: only a queued job exists.
    assert (
        db.query(Job).filter(Job.kind == "handle_inbound", Job.status == JobStatus.queued).count()
        == 1
    )
    drain()
    [doc] = docs(db, world)
    assert doc.status == DocumentStatus.stored
    assert doc.document_type == "Invoice" and doc.company_or_person == "ABC Traders"
    assert doc.date_or_period == "2026-09-15" and doc.folder == "Invoices/2026"
    assert doc.standardized_filename and doc.standardized_filename.endswith(".pdf")
    assert doc.standardized_filename.startswith("Invoice_ABC-Traders_2026-09-15_")
    assert doc.blob_id is None and db.query(MediaBlob).count() == 0  # transient copy purged
    stored = world.fake_storage.get_metadata(doc.storage_file_id)
    assert stored.size_bytes == len(INVOICE_PDF)
    texts = sent_texts(world)
    assert texts[0].startswith("📥 Received") and texts[-1].startswith("✅ Saved")
    assert "Invoices/2026" in texts[-1]


def test_image_without_caption_asks_then_uses_reply(
    client: TestClient, db: Session, world: World
) -> None:
    post_events(
        client,
        world,
        media_event(world.sender, syn.png(), filename=None, mime="image/png", kind="image"),
    )
    drain()
    [doc] = docs(db, world)
    assert doc.status == DocumentStatus.awaiting_description
    assert "What is it?" in sent_texts(world)[-1]
    post_events(client, world, text_event(world.sender, "receipt from Dev Construction Aug 2026"))
    drain()
    [doc] = docs(db, world)
    assert doc.status == DocumentStatus.stored and doc.document_type == "Receipt"
    assert doc.company_or_person == "Dev Construction" and doc.date_or_period == "2026-08"
    assert doc.extension == "png"
    assert doc.review_required is False


def test_multiple_pending_files_require_explicit_choice(
    client: TestClient, db: Session, world: World
) -> None:
    for name in ("a.pdf", "b.pdf"):
        post_events(
            client,
            world,
            media_event(
                world.sender, syn.pdf_with_text([name * 10]), filename=name, mime="application/pdf"
            ),
        )
    drain()
    assert "several files" in sent_texts(world)[-1]
    post_events(client, world, text_event(world.sender, "invoice from ABC Traders"))
    drain()
    assert "1: invoice" in sent_texts(world)[-1]  # did not guess
    assert all(d.status == DocumentStatus.awaiting_description for d in docs(db, world))
    post_events(client, world, text_event(world.sender, "2: bill from Power Company"))
    drain()
    a, b = docs(db, world)
    assert a.status == DocumentStatus.awaiting_description
    assert b.status == DocumentStatus.stored and b.document_type == "Bill"
    post_events(client, world, text_event(world.sender, "9: nothing"))
    drain()
    assert (
        "between 1 and 1" in sent_texts(world)[-1]
        or a.status == DocumentStatus.awaiting_description
    )


def test_skip_and_cancel(client: TestClient, db: Session, world: World) -> None:
    post_events(
        client,
        world,
        media_event(world.sender, INVOICE_PDF, filename="x.pdf", mime="application/pdf"),
    )
    drain()
    post_events(client, world, text_event(world.sender, "skip"))
    drain()
    [doc] = docs(db, world)
    assert doc.status == DocumentStatus.stored and doc.document_type == "Invoice"  # from content
    post_events(
        client,
        world,
        media_event(
            world.sender,
            syn.pdf_with_text(["another one entirely"]),
            filename="y.pdf",
            mime="application/pdf",
        ),
    )
    drain()
    post_events(client, world, text_event(world.sender, "cancel"))
    drain()
    cancelled = docs(db, world)[1]
    assert cancelled.status == DocumentStatus.rejected and cancelled.blob_id is None
    assert "Discarded" in sent_texts(world)[-1]


def test_duplicate_webhook_delivery_is_processed_once(
    client: TestClient, db: Session, world: World
) -> None:
    ev = media_event(
        world.sender,
        INVOICE_PDF,
        filename="a.pdf",
        mime="application/pdf",
        caption="invoice ABC",
        event_id="EVT-1",
    )
    assert post_events(client, world, ev)["accepted"] == 1
    assert post_events(client, world, ev)["duplicate"] == 1
    drain()
    assert post_events(client, world, ev)["duplicate"] == 1
    drain()
    assert len(docs(db, world)) == 1
    assert world.fake_storage.upload_calls == 1
    assert len([t for t in sent_texts(world) if t.startswith("✅")]) == 1


def test_same_file_sent_twice_is_not_stored_twice(
    client: TestClient, db: Session, world: World
) -> None:
    post_events(
        client,
        world,
        media_event(
            world.sender,
            INVOICE_PDF,
            filename="a.pdf",
            mime="application/pdf",
            caption="invoice ABC",
        ),
    )
    drain()
    post_events(
        client,
        world,
        media_event(
            world.sender,
            INVOICE_PDF,
            filename="a-copy.pdf",
            mime="application/pdf",
            caption="invoice ABC",
        ),
    )
    drain()
    assert world.fake_storage.upload_calls == 1
    assert "already saved" in sent_texts(world)[-1]


@pytest.mark.parametrize(
    ("data", "name", "mime", "kind", "expect"),
    [
        (b"MZ\x90\x00binary", "invoice.pdf", "application/pdf", "document", "not supported"),
        (b"", "empty.pdf", "application/pdf", "document", "empty"),
        (
            syn.with_zip_member(syn.docx(["x"]), "word/vbaProject.bin", b"x"),
            "m.docx",
            None,
            "document",
            "macro",
        ),
        (b"\x00\x00", "clip.mp4", "video/mp4", "video", "aren't supported"),
    ],
)
def test_invalid_files_rejected_with_clear_reply(
    client: TestClient,
    db: Session,
    world: World,
    data: bytes,
    name: str,
    mime: str | None,
    kind: str,
    expect: str,
) -> None:
    post_events(
        client,
        world,
        media_event(world.sender, data, filename=name, mime=mime, kind=kind, caption="invoice"),
    )
    drain()
    assert expect in sent_texts(world)[-1]
    assert world.fake_storage.upload_calls == 0


def test_oversized_file_rejected(client: TestClient, db: Session, world: World) -> None:
    db.execute(update(Tenant).values(max_file_bytes=1024))
    db.commit()
    post_events(
        client,
        world,
        media_event(
            world.sender,
            INVOICE_PDF + b"0" * 2000,
            filename="big.pdf",
            mime="application/pdf",
            caption="invoice",
        ),
    )
    drain()
    assert "larger than" in sent_texts(world)[-1]
    [doc] = docs(db, world)
    assert doc.status == DocumentStatus.rejected


def test_unauthorized_sender_ignored_without_reply(
    client: TestClient, db: Session, world: World
) -> None:
    post_events(client, world, text_event("919999999999", "find all documents"))
    drain()
    assert world.fake_wa.sent == []
    ev = db.scalar(select(InboundEvent))
    assert ev is not None and ev.status == "ignored" and ev.ignore_reason == "sender not authorized"
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "whatsapp.unauthorized_sender"))


def test_invalid_signature_rejected(client: TestClient, world: World) -> None:
    r = client.post(
        f"/api/v1/webhooks/fake/{world.conn.connection_key}",
        json={"events": []},
        headers={"x-webhook-signature": "wrong"},
    )
    assert r.status_code == 403
    r = client.post(
        f"/api/v1/webhooks/wasender/{world.conn.connection_key}",
        json={"events": []},
        headers={"x-webhook-signature": "fake-secret"},
    )
    assert r.status_code == 404  # provider mismatch
    r = client.post(
        "/api/v1/webhooks/fake/unknown-key-unknown-key",
        json={},
        headers={"x-webhook-signature": "fake-secret"},
    )
    assert r.status_code == 404


def test_from_me_and_group_messages_ignored(client: TestClient, db: Session, world: World) -> None:
    ev1 = {**text_event(world.sender, "hi"), "from_me": True}
    ev2 = {**text_event(world.sender, "hi"), "is_group": True}
    assert post_events(client, world, ev1, ev2)["ignored"] == 2
    assert db.query(Job).count() == 0


def test_transient_drive_failure_retries_then_succeeds(
    client: TestClient, db: Session, world: World
) -> None:
    world.fake_storage.fail_uploads.append(
        ProviderError("timeout", provider="fake", transient=True)
    )
    post_events(
        client,
        world,
        media_event(
            world.sender,
            INVOICE_PDF,
            filename="a.pdf",
            mime="application/pdf",
            caption="invoice ABC",
        ),
    )
    drain()
    [doc] = docs(db, world)
    assert doc.status != DocumentStatus.stored
    assert not any(t.startswith("✅") for t in sent_texts(world))  # never confirm before storage
    drain(include_future=True)
    [doc] = docs(db, world)
    assert doc.status == DocumentStatus.stored
    assert len([t for t in sent_texts(world) if t.startswith("✅")]) == 1


def test_permanent_drive_failure_marks_failed_and_tells_user(
    client: TestClient, db: Session, world: World
) -> None:
    world.fake_storage.fail_uploads.append(
        ProviderError("HTTP 403 insufficientPermissions", provider="fake", transient=False)
    )
    post_events(
        client,
        world,
        media_event(
            world.sender,
            INVOICE_PDF,
            filename="a.pdf",
            mime="application/pdf",
            caption="invoice ABC",
        ),
    )
    drain()
    [doc] = docs(db, world)
    assert doc.status == DocumentStatus.failed and "insufficientPermissions" in (
        doc.failure_reason or ""
    )
    assert doc.blob_id is not None  # original preserved for retry
    assert "has NOT been stored" in sent_texts(world)[-1]
    assert not any(t.startswith("✅") for t in sent_texts(world))


def test_retry_exhaustion_dead_letters(client: TestClient, db: Session, world: World) -> None:
    for _ in range(10):
        world.fake_storage.fail_uploads.append(
            ProviderError("503", provider="fake", transient=True)
        )
    post_events(
        client,
        world,
        media_event(
            world.sender,
            INVOICE_PDF,
            filename="a.pdf",
            mime="application/pdf",
            caption="invoice ABC",
        ),
    )
    for _ in range(8):
        drain(include_future=True)
    job = db.scalar(select(Job).where(Job.kind == "process_document"))
    assert job is not None and job.status == JobStatus.dead and job.attempts == job.max_attempts
    assert docs(db, world)[0].status == DocumentStatus.failed


def test_reconciliation_when_commit_fails_after_upload(
    client: TestClient, db: Session, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive upload succeeds but the DB transaction fails: the retry finds the existing file."""
    from app.workers import queue as q

    post_events(
        client,
        world,
        media_event(
            world.sender,
            INVOICE_PDF,
            filename="a.pdf",
            mime="application/pdf",
            caption="invoice ABC",
        ),
    )
    drain(max_jobs=1)  # handle_inbound only
    original = q.mark_succeeded
    calls = {"n": 0}

    def flaky(db_: Session, job_id: object, worker_id: str) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            from sqlalchemy.exc import OperationalError

            raise OperationalError("COMMIT", {}, Exception("connection lost"))
        return original(db_, job_id, worker_id)  # type: ignore[arg-type]

    monkeypatch.setattr(q, "mark_succeeded", flaky)
    drain(include_future=True)
    [doc] = docs(db, world)
    assert doc.status == DocumentStatus.stored
    assert world.fake_storage.upload_calls == 1 and len(world.fake_storage.files) == 1
    reconciled = db.scalar(select(AuditEvent).where(AuditEvent.action == "document.stored"))
    assert reconciled is not None and reconciled.details["reconciled"] is True


def test_pending_state_survives_worker_restart_and_expires_safely(
    client: TestClient, db: Session, world: World
) -> None:
    post_events(
        client,
        world,
        media_event(world.sender, INVOICE_PDF, filename="a.pdf", mime="application/pdf"),
    )
    drain()
    # "restart": nothing is in memory; state is only in Postgres.
    registry.reset_fakes()
    assert db.query(PendingMedia).count() == 1
    db.execute(update(PendingMedia).values(expires_at=utcnow() - timedelta(minutes=1)))
    db.commit()
    sweep(db)
    db.commit()
    drain()
    [doc] = docs(db, world)
    assert doc.status == DocumentStatus.stored and doc.document_type == "Invoice"
    assert any("no description received" in w for w in doc.extraction_warnings)
    # Text after expiry is not attached to the old file.
    post_events(client, world, text_event(world.sender, "receipt from someone"))
    drain()
    assert docs(db, world)[0].document_type == "Invoice"


def test_monthly_quota_enforced(client: TestClient, db: Session, world: World) -> None:
    db.execute(update(Tenant).values(monthly_document_quota=0))
    db.commit()
    post_events(
        client,
        world,
        media_event(
            world.sender, INVOICE_PDF, filename="a.pdf", mime="application/pdf", caption="invoice"
        ),
    )
    drain()
    assert "monthly document limit" in sent_texts(world)[-1]


class RecordingClassifier(DocumentClassifier):
    name = "gemini"

    def __init__(self, result: RawClassification | Exception) -> None:
        self.result = result
        self.calls: list[ClassificationInput] = []

    def classify(self, inp: ClassificationInput) -> RawClassification:
        self.calls.append(inp)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _enable_ai(db: Session, world: World) -> None:
    db.execute(update(Tenant).where(Tenant.id == world.tenant.id).values(ai_enabled=True))
    db.commit()


def test_ai_classification_validated_and_clamped(
    client: TestClient, db: Session, world: World
) -> None:
    _enable_ai(db, world)
    fake = RecordingClassifier(
        RawClassification(
            document_type="Spaceship",
            company_or_person="ABC/../Traders",
            date_or_period="sometime",
            confidence=0.99,
        )
    )
    registry.set_override("classifier", lambda t: fake)
    post_events(
        client,
        world,
        media_event(
            world.sender,
            INVOICE_PDF,
            filename="a.pdf",
            mime="application/pdf",
            caption="see attached",
        ),
    )
    drain()
    [doc] = docs(db, world)
    assert doc.status == DocumentStatus.stored
    assert doc.document_type == "Other" and doc.review_required  # invalid category clamped
    assert doc.date_or_period is None  # invalid date dropped, not invented
    assert "/" not in (doc.company_or_person or "") and ".." not in (
        doc.standardized_filename or ""
    )
    assert fake.calls and "ABC Traders" in (fake.calls[0].extracted_text or "")


def test_ai_not_called_for_sensitive_category(
    client: TestClient, db: Session, world: World
) -> None:
    _enable_ai(db, world)
    fake = RecordingClassifier(RawClassification(document_type="ID/KYC", confidence=0.9))
    registry.set_override("classifier", lambda t: fake)
    post_events(
        client,
        world,
        media_event(
            world.sender,
            syn.png(),
            filename="pan.png",
            mime="image/png",
            kind="image",
            caption="PAN card of director",
        ),
    )
    drain()
    assert fake.calls == []
    [doc] = docs(db, world)
    assert doc.document_type == "ID/KYC" and doc.classifier == "rules"


def test_ai_disabled_by_default_never_calls_ai(
    client: TestClient, db: Session, world: World
) -> None:
    fake = RecordingClassifier(RawClassification(document_type="Invoice", confidence=0.9))
    registry.set_override("classifier", lambda t: fake)
    post_events(
        client,
        world,
        media_event(
            world.sender,
            INVOICE_PDF,
            filename="a.pdf",
            mime="application/pdf",
            caption="invoice ABC",
        ),
    )
    drain()
    assert fake.calls == []


def test_ai_failure_falls_back_to_rules_after_retries(
    client: TestClient, db: Session, world: World
) -> None:
    _enable_ai(db, world)
    fake = RecordingClassifier(ClassifierError("429 RESOURCE_EXHAUSTED", transient=True))
    registry.set_override("classifier", lambda t: fake)
    post_events(
        client,
        world,
        media_event(
            world.sender,
            INVOICE_PDF,
            filename="a.pdf",
            mime="application/pdf",
            caption="invoice from ABC Traders",
        ),
    )
    for _ in range(4):
        drain(include_future=True)
    [doc] = docs(db, world)
    assert (
        doc.status == DocumentStatus.stored
        and doc.classifier == "rules"
        and doc.document_type == "Invoice"
    )
    assert any("AI classification unavailable" in w for w in doc.extraction_warnings)
    assert len(fake.calls) == 3


def test_malformed_ai_output_falls_back_immediately(
    client: TestClient, db: Session, world: World
) -> None:
    _enable_ai(db, world)
    fake = RecordingClassifier(ClassifierError("malformed JSON", transient=False))
    registry.set_override("classifier", lambda t: fake)
    post_events(
        client,
        world,
        media_event(
            world.sender,
            INVOICE_PDF,
            filename="a.pdf",
            mime="application/pdf",
            caption="invoice from ABC Traders",
        ),
    )
    drain()
    [doc] = docs(db, world)
    assert (
        doc.status == DocumentStatus.stored and doc.classifier == "rules" and len(fake.calls) == 1
    )


def test_office_formats_end_to_end(client: TestClient, db: Session, world: World) -> None:
    files = [
        (
            syn.docx(["Contract agreement between Flow Chain and Sharma Enterprises 2026"]),
            "agreement.docx",
            "contract with Sharma Enterprises",
        ),
        (
            syn.xlsx([["Bank statement", "September 2026"], ["Opening", 1000]]),
            "stmt.xlsx",
            "bank statement September 2026",
        ),
        (syn.pptx(["Quarterly report Q3"]), "q3.pptx", "report Q3 2026"),
        (b"Receipt no 44, received with thanks from Dev Construction", "r.txt", "receipt"),
        (b"po_number,vendor\n77,ABC Traders\n", "po.csv", "purchase order ABC Traders"),
    ]
    for data, name, caption in files:
        post_events(
            client,
            world,
            media_event(world.sender, data, filename=name, mime=None, caption=caption),
        )
    drain()
    got = {d.original_filename: d for d in docs(db, world)}
    assert all(d.status == DocumentStatus.stored for d in got.values()), {
        k: (v.status, v.failure_reason) for k, v in got.items()
    }
    assert got["agreement.docx"].document_type == "Contract"
    assert (
        got["stmt.xlsx"].document_type == "Bank Statement"
        and got["stmt.xlsx"].date_or_period == "2026-09"
    )
    assert got["q3.pptx"].document_type == "Report"
    assert got["r.txt"].document_type == "Receipt"
    assert got["po.csv"].document_type == "Purchase Order"
    assert {d.extension for d in got.values()} == {"docx", "xlsx", "pptx", "txt", "csv"}


def test_tenants_with_separate_connections_are_isolated(client: TestClient, db: Session) -> None:
    a = make_world(db, "Iso A", sender="919811110001")
    b = make_world(db, "Iso B", sender="919811110002")
    # B's sender writing to A's number is not authorized there.
    post_events(client, a, text_event(b.sender, "find all documents"))
    drain()
    assert a.fake_wa.sent == []


def test_concurrent_messages_from_same_sender_are_serialized(
    client: TestClient, db: Session, world: World
) -> None:
    """Two descriptions racing for one pending file: exactly one wins, the other sees no pending."""
    import threading

    from app.workers import runner

    post_events(
        client,
        world,
        media_event(world.sender, INVOICE_PDF, filename="a.pdf", mime="application/pdf"),
    )
    drain()
    post_events(
        client,
        world,
        text_event(world.sender, "invoice from ABC Traders"),
        text_event(world.sender, "receipt from Dev Construction"),
    )
    barrier = threading.Barrier(2)

    def work(n: int) -> None:
        barrier.wait()
        runner.run_one(f"w{n}")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    drain()
    [doc] = docs(db, world)
    assert doc.status == DocumentStatus.stored
    process_jobs = db.query(Job).filter(Job.kind == "process_document").count()
    assert process_jobs == 1
    assert any(
        "no files waiting" in t
        or "didn't understand" in t
        or "No documents" in t
        or "result(s)" in t
        for t in sent_texts(world)
    )

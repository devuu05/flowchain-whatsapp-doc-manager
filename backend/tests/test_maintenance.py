"""Retention/housekeeping: transient blobs, expired search lists, raw payloads, sessions."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import Document, DocumentStatus, InboundEvent, MediaBlob, SearchSession
from app.services.maintenance import sweep
from tests.conftest import World, drain, post_events, text_event


def _blob(db: Session, world: World, *, age_hours: float, purpose: str = "intake") -> MediaBlob:
    now = utcnow()
    b = MediaBlob(
        tenant_id=world.tenant.id,
        purpose=purpose,
        filename="x.pdf",
        mime_type="application/pdf",
        sha256=hashlib.sha256(b"x").hexdigest(),
        size_bytes=1,
        data=b"x",
        created_at=now - timedelta(hours=age_hours),
        expires_at=now - timedelta(hours=age_hours) + timedelta(hours=72),
    )
    db.add(b)
    db.flush()
    return b


def test_expired_unreferenced_blobs_deleted_referenced_kept(db: Session, world: World) -> None:
    orphan = _blob(db, world, age_hours=100)
    delivery = _blob(db, world, age_hours=100, purpose="delivery")
    kept = _blob(db, world, age_hours=100)
    fresh = _blob(db, world, age_hours=1)
    db.add(
        Document(
            tenant_id=world.tenant.id,
            status=DocumentStatus.failed,
            original_filename="k.pdf",
            extension="pdf",
            mime_type="application/pdf",
            size_bytes=1,
            sha256="0" * 64,
            blob_id=kept.id,
        )
    )
    db.commit()
    stats = sweep(db)
    db.commit()
    remaining = set(db.scalars(select(MediaBlob.id)))
    assert orphan.id not in remaining and delivery.id not in remaining
    assert kept.id in remaining and fresh.id in remaining
    assert stats["blobs_deleted"] == 2


def test_hard_cap_removes_even_referenced_blobs(db: Session, world: World) -> None:
    old = _blob(db, world, age_hours=24 * 15)
    doc = Document(
        tenant_id=world.tenant.id,
        status=DocumentStatus.failed,
        original_filename="k.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size_bytes=1,
        sha256="0" * 64,
        blob_id=old.id,
    )
    db.add(doc)
    db.commit()
    sweep(db)
    db.commit()
    db.expire_all()
    assert db.get(MediaBlob, old.id) is None
    assert db.get(Document, doc.id).blob_id is None  # type: ignore[union-attr]


def test_search_sessions_expire_and_raw_payloads_pruned(
    client: TestClient, db: Session, world: World
) -> None:
    post_events(client, world, text_event(world.sender, "find all documents"))
    drain()
    db.execute(update(SearchSession).values(expires_at=utcnow() - timedelta(minutes=1)))
    db.execute(
        update(InboundEvent).values(raw_payload={"x": 1}, created_at=utcnow() - timedelta(days=15))
    )
    db.commit()
    stats = sweep(db)
    db.commit()
    assert stats["searches_expired"] == 1 and stats["raw_payloads_pruned"] == 1
    db.expire_all()
    assert db.scalar(select(SearchSession.status)) == "expired"
    assert db.scalar(select(InboundEvent.raw_payload)) is None

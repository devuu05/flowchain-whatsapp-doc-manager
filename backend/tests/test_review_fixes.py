"""Regression tests for findings of the independent security/correctness review."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import Document, InboundEvent, Job, JobStatus, Message, Role, Tenant
from app.providers import registry
from app.providers.ai.base import RawClassification
from app.providers.errors import ProviderError
from app.providers.storage.google_drive import API
from app.providers.storage.google_oauth import TOKEN_URL
from app.workers import queue, runner
from tests import synthetic as syn
from tests.conftest import (
    PASSWORD,
    World,
    drain,
    login,
    make_user,
    make_world,
    media_event,
    post_events,
    sent_texts,
    text_event,
)


def test_lost_lease_prevents_second_send(client: TestClient, db: Session, world: World) -> None:
    """Worker A stalls past its lease; worker B reclaims. A must not send after losing the lease."""
    post_events(client, world, text_event(world.sender, "hi"))
    drain(max_jobs=1)  # handle_inbound -> queues send_message
    job = db.scalar(select(Job).where(Job.kind == "send_message"))
    assert job is not None
    claimed_a = queue.claim(db, "worker-A", lease_seconds=60)
    assert claimed_a is not None and claimed_a.id == job.id
    db.execute(update(Job).values(locked_until=utcnow() - timedelta(seconds=1)))
    db.commit()
    assert runner.run_one("worker-B")  # B reclaims and sends
    # A now resumes with its stale job object: the fenced check aborts before sending.
    runner._execute(claimed_a, "worker-A")
    assert len(sent_texts(world)) == 1
    db.expire_all()
    assert db.get(Job, job.id).status == JobStatus.succeeded  # type: ignore[union-attr]


def test_heartbeat_renews_lease(db: Session, world: World) -> None:
    jid = queue.enqueue(db, "noop_kind")
    db.commit()
    job = queue.claim(db, "w", lease_seconds=5)
    assert job is not None and jid == job.id
    before = job.locked_until
    assert queue.renew_lease(job.id, "w", lease_seconds=600)
    db.expire_all()
    assert db.get(Job, job.id).locked_until > before  # type: ignore[union-attr,operator]
    assert not queue.renew_lease(job.id, "someone-else")


def test_retrying_dead_inbound_job_reprocesses_and_sender_was_told(
    client: TestClient, db: Session, world: World
) -> None:
    calls = {"n": 0}
    provider = world.fake_wa
    original = provider.download_media

    def download(media, max_bytes):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] <= 6:
            raise ProviderError("media server down", provider="fake", transient=True)
        return original(media, max_bytes)

    provider.download_media = download  # type: ignore[method-assign]
    post_events(
        client,
        world,
        media_event(
            world.sender,
            syn.pdf_with_text(["invoice ABC Traders"]),
            filename="a.pdf",
            mime="application/pdf",
            caption="invoice ABC",
        ),
    )
    for _ in range(8):
        drain(include_future=True)
    job = db.scalar(select(Job).where(Job.kind == "handle_inbound"))
    assert job is not None and job.status == JobStatus.dead
    assert "couldn't process your last message" in sent_texts(world)[-1]
    csrf = login(client, world.user.email)
    r = client.post(
        f"/api/v1/tenants/{world.tenant.id}/jobs/{job.id}/retry", headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 200
    drain()
    db.expire_all()
    assert db.scalar(select(Document.status)) == "stored"
    assert db.scalar(select(InboundEvent.status)) == "processed"


@respx.mock
def test_oauth_callback_requires_same_session(client: TestClient, db: Session) -> None:
    from app.main import app

    w = make_world(db, "Bind Co")
    csrf = login(client, w.user.email)
    state = parse_qs(
        urlparse(
            client.post(
                f"/api/v1/tenants/{w.tenant.id}/integrations/google/start",
                headers={"x-csrf-token": csrf},
            ).json()["authorization_url"]
        ).query
    )["state"][0]
    token = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ya29.x", "expires_in": 3600, "refresh_token": "1//x"}
        )
    )
    victim = TestClient(app)  # another browser, not signed in
    r = victim.get(
        "/api/v1/integrations/google/callback",
        params={"state": state, "code": "c"},
        follow_redirects=False,
    )
    assert r.headers["location"].endswith("result=session_mismatch") and not token.called
    other_user = make_user(db, "victim@example.com")
    db.commit()
    login(victim, other_user.email)
    r = victim.get(
        "/api/v1/integrations/google/callback",
        params={"state": state, "code": "c"},
        follow_redirects=False,
    )
    assert r.headers["location"].endswith("result=session_mismatch") and not token.called


@respx.mock
def test_reconnect_with_other_account_creates_new_root(client: TestClient, db: Session) -> None:
    w = make_world(db, "Reconnect Co")
    from app.models import IntegrationConfig

    cfg = db.get(IntegrationConfig, w.storage_cfg.id)
    assert cfg is not None
    cfg.provider = "google_drive"
    cfg.settings = {"root_folder_id": "OLDROOT"}
    db.commit()
    csrf = login(client, w.user.email)
    state = parse_qs(
        urlparse(
            client.post(
                f"/api/v1/tenants/{w.tenant.id}/integrations/google/start",
                headers={"x-csrf-token": csrf},
            ).json()["authorization_url"]
        ).query
    )["state"][0]
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ya29.new", "expires_in": 3600, "refresh_token": "1//new"}
        )
    )
    respx.get(f"{API}/files/OLDROOT").mock(
        return_value=httpx.Response(404, json={"error": {"code": 404}})
    )
    respx.post(f"{API}/files").mock(return_value=httpx.Response(200, json={"id": "NEWROOT"}))
    r = client.get(
        "/api/v1/integrations/google/callback",
        params={"state": state, "code": "c"},
        follow_redirects=False,
    )
    assert r.headers["location"].endswith("result=connected")
    db.expire_all()
    assert db.get(IntegrationConfig, w.storage_cfg.id).settings["root_folder_id"] == "NEWROOT"  # type: ignore[union-attr]


def test_forwarded_for_uses_rightmost_trusted_hop(
    client: TestClient, world: World, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from app.api.deps import client_ip
    from app.core.config import get_settings

    class Req:
        headers = {"x-forwarded-for": "6.6.6.6, 203.0.113.9"}
        client = type("C", (), {"host": "10.0.0.1"})()

    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 1)
    assert client_ip(Req()) == "203.0.113.9"  # type: ignore[arg-type]
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 0)
    assert client_ip(Req()) == "10.0.0.1"  # type: ignore[arg-type]


def test_member_cannot_see_webhook_url_or_audit_activity(client: TestClient, db: Session) -> None:
    w = make_world(db, "Member View", role=Role.member)
    login(client, w.user.email)
    integ = client.get(f"/api/v1/tenants/{w.tenant.id}/integrations").json()
    assert all(i["webhook_url"] is None for i in integ)
    assert client.get(f"/api/v1/tenants/{w.tenant.id}/overview").json()["recent_activity"] == []


def test_adding_existing_user_discloses_minimal_profile(client: TestClient, db: Session) -> None:
    a = make_world(db, "Ws A", sender="919800000011")
    b = make_world(db, "Ws B", sender="919800000012")
    login(client, b.user.email)  # gives B's owner a last_login_at
    csrf = login(client, a.user.email)
    r = client.post(
        f"/api/v1/tenants/{a.tenant.id}/members",
        json={"email": b.user.email, "role": "member"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 201
    assert r.json()["full_name"] == "" and r.json()["last_login_at"] is None


def test_image_without_description_not_sent_to_ai(
    client: TestClient, db: Session, world: World
) -> None:
    from tests.test_pipeline_e2e import RecordingClassifier

    db.execute(update(Tenant).values(ai_enabled=True))
    db.commit()
    fake = RecordingClassifier(RawClassification(document_type="Invoice", confidence=0.9))
    registry.set_override("classifier", lambda t: fake)
    post_events(
        client,
        world,
        media_event(world.sender, syn.png(), filename="IMG_1.png", mime="image/png", kind="image"),
    )
    drain()
    post_events(client, world, text_event(world.sender, "skip"))
    drain()
    assert all(c.media_bytes is None for c in fake.calls)
    doc = db.scalar(select(Document))
    assert doc is not None and any("not sent to AI" in w for w in doc.extraction_warnings)


def test_lockout_is_per_ip_before_per_account(
    client: TestClient, db: Session, world: World
) -> None:
    from app.models import LoginAttempt

    for _ in range(5):
        db.add(
            LoginAttempt(
                email=world.user.email, ip="198.51.100.7", success=False, created_at=utcnow()
            )
        )
    db.commit()
    # The attacker's IP is locked out, but the account holder (different IP) can still sign in.
    r = client.post("/api/v1/auth/login", json={"email": world.user.email, "password": PASSWORD})
    assert r.status_code == 200
    for _ in range(15):
        db.add(
            LoginAttempt(
                email=world.user.email,
                ip=f"198.51.100.{_ + 20}",
                success=False,
                created_at=utcnow(),
            )
        )
    db.commit()
    r = client.post("/api/v1/auth/login", json={"email": world.user.email, "password": PASSWORD})
    assert r.status_code == 429


def test_webhook_connection_key_masked_in_request_log(
    client: TestClient, world: World, caplog
) -> None:  # type: ignore[no-untyped-def]
    import logging

    with caplog.at_level(logging.INFO, logger="app.request"):
        client.post(
            f"/api/v1/webhooks/fake/{world.conn.connection_key}",
            json={"events": []},
            headers={"x-webhook-signature": "fake-secret"},
        )
    paths = [getattr(r, "path", "") for r in caplog.records if r.name == "app.request"]
    assert paths and all(world.conn.connection_key not in p for p in paths)
    assert any(p.endswith("/[connection-key]") for p in paths)


def test_ignored_media_event_keeps_no_provider_reference(
    client: TestClient, db: Session, world: World
) -> None:
    ev = media_event("919999999999", b"%PDF-1.4", filename="a.pdf", mime="application/pdf")
    ev["from_me"] = True
    post_events(client, world, ev)
    row = db.scalar(select(InboundEvent))
    assert row is not None and row.normalized["media"]["provider_ref"] == {}
    assert db.scalar(select(Message)) is None


def test_dashboard_proxy_relayed_ip_trusted_only_with_secret(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    from app.api.deps import client_ip
    from app.core.config import get_settings

    def req(headers: dict[str, str]):  # type: ignore[no-untyped-def]
        return type(
            "R", (), {"headers": headers, "client": type("C", (), {"host": "10.0.0.1"})()}
        )()

    monkeypatch.setattr(get_settings(), "proxy_shared_secret", SecretStr("proxy-secret-123"))
    good = {"x-proxy-auth": "proxy-secret-123", "x-client-ip": "203.0.113.5"}
    assert client_ip(req(good)) == "203.0.113.5"
    assert client_ip(req({**good, "x-proxy-auth": "wrong"})) == "10.0.0.1"
    assert client_ip(req({**good, "x-client-ip": "not-an-ip"})) == "10.0.0.1"
    monkeypatch.setattr(get_settings(), "proxy_shared_secret", SecretStr(""))
    assert client_ip(req(good)) == "10.0.0.1"  # no secret configured -> never trusted

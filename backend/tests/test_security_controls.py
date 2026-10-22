"""Security controls added in the production-readiness review."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InboundEvent
from app.security.rate_limit import LIMITERS
from app.security.redaction import redact
from tests import synthetic as syn
from tests.conftest import World, drain, media_event, post_events


def test_webhook_rate_limited(client: TestClient, world: World) -> None:
    LIMITERS["webhook"].limit = 3
    try:
        url = f"/api/v1/webhooks/fake/{world.conn.connection_key}"
        codes = [
            client.post(
                url, json={"events": []}, headers={"x-webhook-signature": "fake-secret"}
            ).status_code
            for _ in range(5)
        ]
    finally:
        LIMITERS["webhook"].limit = 600
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]


def test_media_and_login_rate_limited(client: TestClient) -> None:
    LIMITERS["media"].limit = 2
    LIMITERS["login"].limit = 2
    try:
        media = [client.get("/api/v1/media/x.y").status_code for _ in range(3)]
        login = [
            client.post("/api/v1/auth/login", json={"email": "a@b.co", "password": "x"}).status_code
            for _ in range(3)
        ]
    finally:
        LIMITERS["media"].limit = 120
        LIMITERS["login"].limit = 30
    assert media == [404, 404, 429]
    assert login[-1] == 429


def test_forwarded_for_header_cannot_spoof_audit_ip(client: TestClient, world: World) -> None:
    from app.models import AuditEvent

    client.post(
        "/api/v1/auth/login",
        json={"email": world.user.email, "password": "wrong-Pass-1"},
        headers={"x-forwarded-for": "6.6.6.6"},
    )
    from app.db.session import get_sessionmaker

    with get_sessionmaker()() as db:
        ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.login_failed"))
        assert ev is not None and ev.ip != "6.6.6.6"


def test_provider_media_reference_pruned_after_processing(
    client: TestClient, db: Session, world: World
) -> None:
    post_events(
        client,
        world,
        media_event(
            world.sender,
            syn.pdf_with_text(["invoice ABC Traders"]),
            filename="a.pdf",
            mime="application/pdf",
            caption="invoice",
        ),
    )
    drain()
    ev = db.scalar(select(InboundEvent))
    assert ev is not None and ev.status == "processed"
    assert ev.normalized["media"]["provider_ref"] == {}


def test_whatsapp_thumbnails_and_media_keys_redacted() -> None:
    raw = {
        "data": {
            "messages": {
                "message": {
                    "documentMessage": {
                        "mediaKey": "c2VjcmV0",
                        "jpegThumbnail": "/9j/4AAQ...",
                        "fileName": "a.pdf",
                    }
                }
            }
        }
    }
    out = redact(raw)
    doc = out["data"]["messages"]["message"]["documentMessage"]
    assert doc["mediaKey"] == "[REDACTED]" and doc["jpegThumbnail"] == "[REDACTED]"
    assert doc["fileName"] == "a.pdf"


def test_production_settings_refuse_insecure_defaults() -> None:
    import pytest
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(app_env="production")
    ok = Settings(
        app_env="production",
        secret_key="x" * 48,
        field_encryption_keys="k",
        cookie_secure=True,
        allow_fake_providers=False,
    )
    assert ok.app_env == "production"


def test_fake_providers_refused_when_disabled(world: World, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import pytest

    from app.core.config import get_settings
    from app.providers import registry
    from app.providers.errors import ProviderError

    monkeypatch.setattr(get_settings(), "allow_fake_providers", False)
    with pytest.raises(ProviderError):
        registry.whatsapp_for(world.conn)

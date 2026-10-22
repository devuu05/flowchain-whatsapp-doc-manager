"""Phase 2: authentication, sessions, CSRF, throttling, secret hygiene."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import UserSession
from tests.conftest import PASSWORD, World, login


def test_login_me_logout(client: TestClient, world: World) -> None:
    csrf = login(client, world.user.email)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == world.user.email
    assert me.json()["memberships"][0]["tenant_id"] == str(world.tenant.id)
    cookie = client.cookies.get("fc_session")
    assert cookie and cookie not in me.text
    r = client.post("/api/v1/auth/logout", headers={"x-csrf-token": csrf})
    assert r.status_code == 204
    client.cookies.set("fc_session", cookie)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_session_cookie_flags(client: TestClient, world: World) -> None:
    r = client.post("/api/v1/auth/login", json={"email": world.user.email, "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_wrong_password_and_unknown_user_look_identical(client: TestClient, world: World) -> None:
    a = client.post(
        "/api/v1/auth/login", json={"email": world.user.email, "password": "wrong-Password-1"}
    )
    b = client.post(
        "/api/v1/auth/login", json={"email": "ghost@example.com", "password": "wrong-Password-1"}
    )
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["message"] == b.json()["error"]["message"]


def test_login_lockout_after_repeated_failures(client: TestClient, world: World) -> None:
    for _ in range(5):
        client.post(
            "/api/v1/auth/login", json={"email": world.user.email, "password": "nope-Nope-123"}
        )
    r = client.post("/api/v1/auth/login", json={"email": world.user.email, "password": PASSWORD})
    assert r.status_code == 429


def test_unauthenticated_requests_rejected(client: TestClient, world: World) -> None:
    for path in [
        "/api/v1/auth/me",
        "/api/v1/tenants",
        f"/api/v1/tenants/{world.tenant.id}/documents",
    ]:
        assert client.get(path).status_code == 401


def test_csrf_required_for_state_changes(client: TestClient, world: World) -> None:
    csrf = login(client, world.user.email)
    path = f"/api/v1/tenants/{world.tenant.id}/senders"
    assert client.post(path, json={"phone": "+91 99999 00000"}).status_code == 403
    assert (
        client.post(
            path, json={"phone": "+91 99999 00000"}, headers={"x-csrf-token": "bad"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            path, json={"phone": "+91 99999 00000"}, headers={"x-csrf-token": csrf}
        ).status_code
        == 201
    )


def test_expired_and_idle_sessions_rejected(client: TestClient, world: World, db: Session) -> None:
    login(client, world.user.email)
    db.execute(
        update(UserSession).values(last_seen_at=UserSession.last_seen_at - timedelta(days=2))
    )
    db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_password_change_revokes_other_sessions(client: TestClient, world: World) -> None:
    from app.main import app

    other = TestClient(app)
    login(other, world.user.email)
    csrf = login(client, world.user.email)
    r = client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "New-Password-2026x"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401


def test_weak_password_rejected(client: TestClient, world: World) -> None:
    csrf = login(client, world.user.email)
    r = client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "alllowercase1"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 400


def test_integration_secrets_never_returned(client: TestClient, world: World) -> None:
    csrf = login(client, world.user.email)
    r = client.post(
        f"/api/v1/tenants/{world.tenant.id}/integrations/whatsapp",
        json={
            "provider": "wasender",
            "phone_number": "+91 88888 77777",
            "api_key": "wasender-api-key-SECRET-1",
            "webhook_secret": "webhook-secret-SECRET-2",
        },
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 201, r.text
    listing = client.get(f"/api/v1/tenants/{world.tenant.id}/integrations")
    for resp in (r, listing):
        assert "SECRET-1" not in resp.text and "SECRET-2" not in resp.text
        assert "encrypted_secrets" not in resp.text
    assert r.json()["has_secrets"] is True
    assert r.json()["webhook_url"].startswith("https://api.example.test/api/v1/webhooks/wasender/")


def test_secrets_redacted_from_logs(caplog) -> None:  # type: ignore[no-untyped-def]
    import logging

    from app.core.logging import RedactingJsonFormatter

    rec = logging.LogRecord(
        "t",
        logging.INFO,
        "f",
        1,
        "calling with Bearer abc.def.ghi and key=AIzaSyA1234567890abcdefghijklmnopqrstu",
        None,
        None,
    )
    rec.api_key = "plain-api-key"
    rec.payload = {"refresh_token": "1//0gabcdefghijklmnopqrstuvwxyz", "ok": "fine"}
    out = RedactingJsonFormatter().format(rec)
    assert "abc.def.ghi" not in out and "AIzaSy" not in out
    assert "plain-api-key" not in out and "1//0g" not in out
    assert "fine" in out

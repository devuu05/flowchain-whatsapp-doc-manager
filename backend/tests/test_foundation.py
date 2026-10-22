"""Phase 1: app foundation — health, errors, request ids, limits, internal webhook schema."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import World, text_event


def test_health_liveness(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers["x-request-id"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_readiness_reports_database_and_queue(client: TestClient) -> None:
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["database"] == "ok"
    assert "queued" in body["checks"]["queue"]


def test_request_id_propagates_valid_and_replaces_invalid(client: TestClient) -> None:
    assert (
        client.get("/health", headers={"x-request-id": "abc12345-req"}).headers["x-request-id"]
        == "abc12345-req"
    )
    assert (
        client.get("/health", headers={"x-request-id": "bad id\n"}).headers["x-request-id"]
        != "bad id\n"
    )


def test_unknown_route_returns_structured_404(client: TestClient) -> None:
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert r.json()["error"]["request_id"]


def test_validation_errors_do_not_echo_input(client: TestClient) -> None:
    r = client.post("/api/v1/auth/login", json={"email": "x", "password": "s3cret-value"})
    assert r.status_code == 422
    assert "s3cret-value" not in r.text
    assert r.json()["error"]["code"] == "validation_error"


def test_body_size_limit(client: TestClient) -> None:
    r = client.post(
        "/api/v1/auth/login", content=b"x" * 1_100_000, headers={"content-type": "application/json"}
    )
    assert r.status_code == 413


def test_internal_webhook_validates_schema(client: TestClient, world: World) -> None:
    headers = {"x-internal-token": "internal-test-token-123"}
    bad = client.post(
        "/api/v1/webhooks/whatsapp",
        json={"connection_key": world.conn.connection_key, "event": {"event_type": "message"}},
        headers=headers,
    )
    assert bad.status_code == 422
    extra = client.post(
        "/api/v1/webhooks/whatsapp",
        json={
            "connection_key": world.conn.connection_key,
            "event": {**text_event(world.sender, "hi"), "unexpected": 1},
        },
        headers=headers,
    )
    assert extra.status_code == 422
    ok = client.post(
        "/api/v1/webhooks/whatsapp",
        json={"connection_key": world.conn.connection_key, "event": text_event(world.sender, "hi")},
        headers=headers,
    )
    assert ok.status_code == 200 and ok.json()["accepted"] == 1


def test_internal_webhook_requires_token(client: TestClient, world: World) -> None:
    r = client.post(
        "/api/v1/webhooks/whatsapp",
        json={"connection_key": world.conn.connection_key, "event": text_event(world.sender, "hi")},
    )
    assert r.status_code == 403

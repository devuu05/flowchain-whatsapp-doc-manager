"""Dashboard API workflows: documents, failures/retry, members, categories, integrations,
usage, audit, export and deletion."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Document, DocumentStatus, IntegrationConfig, Job
from app.providers.errors import ProviderError
from tests import synthetic as syn
from tests.conftest import World, drain, login, media_event, post_events

PDF = syn.pdf_with_text(["TAX INVOICE", "ABC Traders", "15/09/2026"])


def _store(
    client: TestClient, world: World, caption: str = "invoice from ABC Traders", data: bytes = PDF
) -> None:
    post_events(
        client,
        world,
        media_event(world.sender, data, filename="a.pdf", mime="application/pdf", caption=caption),
    )
    drain()


def test_documents_list_filter_detail_download(client: TestClient, world: World) -> None:
    _store(client, world)
    login(client, world.user.email)
    t = world.tenant.id
    page = client.get(f"/api/v1/tenants/{t}/documents", params={"document_type": "Invoice"}).json()
    assert page["total"] == 1
    doc = page["items"][0]
    assert (
        client.get(f"/api/v1/tenants/{t}/documents", params={"company": "abc"}).json()["total"] == 1
    )
    assert (
        client.get(f"/api/v1/tenants/{t}/documents", params={"period": "2026-09"}).json()["total"]
        == 1
    )
    assert (
        client.get(f"/api/v1/tenants/{t}/documents", params={"period": "2025"}).json()["total"] == 0
    )
    assert (
        client.get(f"/api/v1/tenants/{t}/documents", params={"period": "garbage"}).status_code
        == 400
    )
    assert client.get(f"/api/v1/tenants/{t}/documents", params={"q": "100%_"}).json()["total"] == 0
    detail = client.get(f"/api/v1/tenants/{t}/documents/{doc['id']}").json()
    assert detail["extraction_status"] == "success" and detail["jobs"]
    dl = client.get(f"/api/v1/tenants/{t}/documents/{doc['id']}/download")
    assert dl.status_code == 200 and dl.content == PDF


def test_correct_metadata(client: TestClient, world: World) -> None:
    _store(client, world)
    csrf = login(client, world.user.email)
    t = world.tenant.id
    doc_id = client.get(f"/api/v1/tenants/{t}/documents").json()["items"][0]["id"]
    h = {"x-csrf-token": csrf}
    r = client.patch(
        f"/api/v1/tenants/{t}/documents/{doc_id}",
        json={
            "document_type": "Receipt",
            "date_or_period": "Aug 2026",
            "company_or_person": "ABC Pvt",
        },
        headers=h,
    )
    assert (
        r.status_code == 200
        and r.json()["document_type"] == "Receipt"
        and r.json()["date_or_period"] == "2026-08"
    )
    assert (
        client.patch(
            f"/api/v1/tenants/{t}/documents/{doc_id}", json={"document_type": "Nope"}, headers=h
        ).status_code
        == 400
    )


def test_failed_document_visible_and_retryable(
    client: TestClient, db: Session, world: World
) -> None:
    world.fake_storage.fail_uploads.append(
        ProviderError("HTTP 403", provider="fake", transient=False)
    )
    _store(client, world)
    csrf = login(client, world.user.email)
    t = world.tenant.id
    failed = client.get(f"/api/v1/tenants/{t}/documents", params={"status": "failed"}).json()[
        "items"
    ]
    assert len(failed) == 1 and "403" in failed[0]["failure_reason"]
    dead = client.get(f"/api/v1/tenants/{t}/jobs", params={"status": "dead"}).json()
    assert dead["total"] == 1 and dead["items"][0]["error_category"] == "permanent"
    overview = client.get(f"/api/v1/tenants/{t}/overview").json()
    assert overview["failed"] == 1 and overview["jobs_dead"] == 1
    r = client.post(
        f"/api/v1/tenants/{t}/documents/{failed[0]['id']}/retry", headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 200
    drain()
    db.expire_all()
    assert db.scalar(select(Document.status)) == DocumentStatus.stored


def test_members_management(client: TestClient, world: World) -> None:
    csrf = login(client, world.user.email)
    t, h = world.tenant.id, {"x-csrf-token": csrf}
    r = client.post(
        f"/api/v1/tenants/{t}/members",
        json={
            "email": "Clerk@Example.com",
            "role": "member",
            "initial_password": "Clerk-Password-123",
        },
        headers=h,
    )
    assert r.status_code == 201 and r.json()["email"] == "clerk@example.com"
    assert "password" not in r.text.lower()
    assert (
        client.post(
            f"/api/v1/tenants/{t}/members", json={"email": "clerk@example.com"}, headers=h
        ).status_code
        == 409
    )
    uid = r.json()["user_id"]
    assert (
        client.patch(
            f"/api/v1/tenants/{t}/members/{uid}", json={"role": "admin"}, headers=h
        ).json()["role"]
        == "admin"
    )
    assert client.delete(f"/api/v1/tenants/{t}/members/{uid}", headers=h).status_code == 204
    assert len(client.get(f"/api/v1/tenants/{t}/members").json()) == 1


def test_categories_and_senders(client: TestClient, world: World) -> None:
    csrf = login(client, world.user.email)
    t, h = world.tenant.id, {"x-csrf-token": csrf}
    r = client.post(
        f"/api/v1/tenants/{t}/categories",
        json={"name": "Delivery Challan", "keywords": ["challan"]},
        headers=h,
    )
    assert r.status_code == 201
    assert (
        client.post(
            f"/api/v1/tenants/{t}/categories", json={"name": "delivery challan"}, headers=h
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/tenants/{t}/categories", json={"name": "<script>"}, headers=h
        ).status_code
        == 422
    )
    other = next(
        c for c in client.get(f"/api/v1/tenants/{t}/categories").json() if c["name"] == "Other"
    )
    assert (
        client.patch(
            f"/api/v1/tenants/{t}/categories/{other['id']}", json={"is_active": False}, headers=h
        ).status_code
        == 409
    )
    s = client.post(
        f"/api/v1/tenants/{t}/senders",
        json={"phone": "+91 98111 22233", "display_name": "Accounts"},
        headers=h,
    )
    assert s.status_code == 201 and s.json()["phone"] == "919811122233"
    assert (
        client.post(f"/api/v1/tenants/{t}/senders", json={"phone": "12"}, headers=h).status_code
        == 422
    )
    assert (
        client.delete(f"/api/v1/tenants/{t}/senders/{s.json()['id']}", headers=h).status_code == 204
    )


def test_whatsapp_connection_check_and_disconnect(
    client: TestClient, db: Session, world: World
) -> None:
    csrf = login(client, world.user.email)
    t, h = world.tenant.id, {"x-csrf-token": csrf}
    r = client.post(
        "/api/v1/integrations/check".replace(
            "/integrations", f"/tenants/{t}/integrations/{world.conn.id}"
        ),
        headers=h,
    )
    assert r.status_code == 200 and r.json()["status"] == "connected"
    dup = client.post(
        f"/api/v1/tenants/{t}/integrations/whatsapp",
        json={"provider": "fake", "phone_number": "910000000000"},
        headers=h,
    )
    assert dup.status_code == 409
    assert (
        client.delete(f"/api/v1/tenants/{t}/integrations/{world.conn.id}", headers=h).status_code
        == 204
    )
    db.expire_all()
    cfg = db.get(IntegrationConfig, world.conn.id)
    assert cfg is not None and cfg.status == "disconnected" and cfg.encrypted_secrets is None
    # webhooks for a disconnected connection are refused
    r = client.post(
        f"/api/v1/webhooks/fake/{world.conn.connection_key}",
        json={"events": []},
        headers={"x-webhook-signature": "fake-secret"},
    )
    assert r.status_code == 404


def test_usage_and_audit(client: TestClient, world: World) -> None:
    _store(client, world)
    login(client, world.user.email)
    t = world.tenant.id
    u = client.get(f"/api/v1/tenants/{t}/usage").json()
    assert u["documents_this_month"] == 1 and u["messages_sent_this_month"] >= 2
    assert u["storage_bytes_this_month"] == len(PDF) and u["ai_enabled"] is False
    a = client.get(f"/api/v1/tenants/{t}/audit").json()
    actions = {i["action"] for i in a["items"]}
    assert {"document.stored", "auth.login"} & actions


def test_export_and_delete_tenant_data(client: TestClient, db: Session, world: World) -> None:
    _store(client, world)
    csrf = login(client, world.user.email)
    t, h = world.tenant.id, {"x-csrf-token": csrf}
    exp = client.get(f"/api/v1/tenants/{t}/export")
    assert exp.status_code == 200 and "attachment" in exp.headers["content-disposition"]
    body = exp.json()
    assert len(body["documents"]) == 1 and "encrypted_secrets" not in exp.text
    assert (
        client.post(
            f"/api/v1/tenants/{t}/delete-data", json={"confirm_slug": "wrong"}, headers=h
        ).status_code
        == 400
    )
    r = client.post(
        f"/api/v1/tenants/{t}/delete-data",
        json={"confirm_slug": world.tenant.slug, "trash_drive_files": True},
        headers=h,
    )
    assert r.status_code == 200 and r.json()["drive_trashed"] == 1
    db.expire_all()
    assert db.query(Document).count() == 0 and db.query(Job).count() == 0
    assert world.fake_storage.files == {}
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "tenant.data_deleted"))

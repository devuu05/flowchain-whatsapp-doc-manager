"""Tenant isolation at the API level and authorization by role."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Document, DocumentStatus, Role
from tests.conftest import World, login, make_user, make_world


def _doc(db: Session, world: World, name: str) -> Document:
    d = Document(
        tenant_id=world.tenant.id,
        status=DocumentStatus.stored,
        original_filename=name,
        extension="pdf",
        mime_type="application/pdf",
        size_bytes=10,
        sha256="0" * 64,
        document_type="Invoice",
        company_or_person="ABC Traders",
        standardized_filename=name,
        storage_provider="fake",
        storage_file_id="file-x",
    )
    db.add(d)
    db.commit()
    return d


def test_tenant_a_cannot_access_tenant_b(client: TestClient, db: Session) -> None:
    a = make_world(db, "Tenant A", sender="919800000001")
    b = make_world(db, "Tenant B", sender="919800000002")
    b_doc = _doc(db, b, "b-secret.pdf")
    csrf = login(client, a.user.email)
    tb = b.tenant.id
    for path in [
        f"/api/v1/tenants/{tb}",
        f"/api/v1/tenants/{tb}/documents",
        f"/api/v1/tenants/{tb}/documents/{b_doc.id}",
        f"/api/v1/tenants/{tb}/integrations",
        f"/api/v1/tenants/{tb}/audit",
        f"/api/v1/tenants/{tb}/members",
        f"/api/v1/tenants/{tb}/senders",
        f"/api/v1/tenants/{tb}/export",
        f"/api/v1/tenants/{tb}/overview",
        f"/api/v1/tenants/{tb}/jobs",
    ]:
        assert client.get(path).status_code == 404, path
    # Using tenant A's path with tenant B's document id must not leak it either.
    assert client.get(f"/api/v1/tenants/{a.tenant.id}/documents/{b_doc.id}").status_code == 404
    r = client.delete(
        f"/api/v1/tenants/{a.tenant.id}/documents/{b_doc.id}", headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 404
    listing = client.get(f"/api/v1/tenants/{a.tenant.id}/documents").json()
    assert listing["total"] == 0
    assert [t["id"] for t in client.get("/api/v1/tenants").json()] == [str(a.tenant.id)]


def test_member_cannot_perform_admin_actions(client: TestClient, db: Session) -> None:
    w = make_world(db, "Members Only", role=Role.member)
    csrf = login(client, w.user.email)
    h = {"x-csrf-token": csrf}
    t = w.tenant.id
    assert client.get(f"/api/v1/tenants/{t}/documents").status_code == 200
    assert (
        client.post(
            f"/api/v1/tenants/{t}/senders", json={"phone": "919811111111"}, headers=h
        ).status_code
        == 403
    )
    assert client.get(f"/api/v1/tenants/{t}/members").status_code == 403
    assert client.get(f"/api/v1/tenants/{t}/export").status_code == 403
    assert (
        client.patch(f"/api/v1/tenants/{t}", json={"ai_enabled": True}, headers=h).status_code
        == 403
    )


def test_only_platform_admin_creates_tenants(client: TestClient, db: Session, world: World) -> None:
    csrf = login(client, world.user.email)
    assert (
        client.post(
            "/api/v1/tenants", json={"name": "New Co"}, headers={"x-csrf-token": csrf}
        ).status_code
        == 403
    )
    admin = make_user(db, "ops@example.com", admin=True)
    db.commit()
    csrf = login(client, admin.email)
    r = client.post("/api/v1/tenants", json={"name": "New Co"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 201
    cats = client.get(f"/api/v1/tenants/{r.json()['id']}/categories").json()
    assert {c["name"] for c in cats} >= {"Invoice", "Receipt", "ID/KYC", "Other"}
    assert next(c for c in cats if c["name"] == "ID/KYC")["ai_allowed"] is False


def test_ai_enable_requires_owner_and_consent_note(client: TestClient, world: World) -> None:
    csrf = login(client, world.user.email)
    h = {"x-csrf-token": csrf}
    t = world.tenant.id
    assert (
        client.patch(f"/api/v1/tenants/{t}", json={"ai_enabled": True}, headers=h).status_code
        == 400
    )
    r = client.patch(
        f"/api/v1/tenants/{t}",
        json={"ai_enabled": True, "ai_consent_note": "Approved by client director on 2026-10-20"},
        headers=h,
    )
    assert r.status_code == 200 and r.json()["ai_enabled"] is True


def test_last_owner_cannot_be_removed(client: TestClient, world: World) -> None:
    csrf = login(client, world.user.email)
    r = client.delete(
        f"/api/v1/tenants/{world.tenant.id}/members/{world.user.id}", headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 409

"""wasender.dev adapter — MOCKED HTTP (respx). Signature scheme, endpoints and error format are
from wasender.dev's llms-full.txt; webhook/document field names follow the whapi.cloud format the
service declares compatibility with and must be confirmed live."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, IntegrationConfig, IntegrationKind, IntegrationStatus
from app.providers.errors import ProviderError
from app.providers.whatsapp.wasender_dev import WasenderDevProvider
from app.schemas.events import MediaRef
from app.security.crypto import encrypt_json, new_token
from tests import synthetic as syn
from tests.conftest import drain, login, make_world

BASE = "https://api.wasender.dev"
SECRET = "whsec_test_123"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def wa() -> WasenderDevProvider:
    return WasenderDevProvider(token="wsk_sg_test", webhook_secret=SECRET, base_url=BASE)


def text_payload(
    body: str = "find all invoices", mid: str = "MSG1", sender: str = "919876543210"
) -> dict:
    return {
        "messages": [
            {
                "id": mid,
                "from_me": False,
                "type": "text",
                "chat_id": f"{sender}@s.whatsapp.net",
                "timestamp": 1761100000,
                "from": sender,
                "from_name": "Ravi",
                "text": {"body": body},
            }
        ],
        "event": {"type": "messages", "event": "post"},
        "channel_id": "ch_1",
    }


def test_hmac_signature_over_raw_body(wa: WasenderDevProvider) -> None:
    raw = json.dumps(text_payload()).encode()
    assert wa.verify_webhook({"X-Wasender-Signature": sign(raw)}, raw)
    assert not wa.verify_webhook({"x-wasender-signature": sign(raw, "wrong")}, raw)
    assert not wa.verify_webhook({"x-wasender-signature": sign(raw)}, raw + b" ")  # body changed
    assert not wa.verify_webhook(
        {"x-wasender-signature": hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()}, raw
    )
    assert not wa.verify_webhook({}, raw)


def test_parse_text_image_document_and_ignored(wa: WasenderDevProvider) -> None:
    ev = wa.parse_webhook(text_payload())[0]
    assert (ev.event_id, ev.sender, ev.text, ev.sender_name) == (
        "MSG1",
        "919876543210",
        "find all invoices",
        "Ravi",
    )
    doc = {
        "messages": [
            {
                "id": "M2",
                "type": "document",
                "chat_id": "919876543210@s.whatsapp.net",
                "from": "919876543210",
                "document": {
                    "id": "med_1",
                    "mime_type": "application/pdf",
                    "file_size": 2048,
                    "file_name": "INV-42.pdf",
                    "caption": "invoice ABC Traders",
                },
            }
        ]
    }
    d = wa.parse_webhook(doc)[0]
    assert d.media is not None and d.media.kind == "document" and d.media.filename == "INV-42.pdf"
    assert (
        d.media.size_bytes == 2048
        and d.text == "invoice ABC Traders"
        and d.media.provider_ref["media_id"] == "med_1"
    )
    img = {
        "messages": [
            {
                "id": "M3",
                "type": "image",
                "chat_id": "919876543210@s.whatsapp.net",
                "image": {"id": "med_2", "mime_type": "image/jpeg"},
            }
        ]
    }
    i = wa.parse_webhook(img)[0]
    assert (
        i.media is not None
        and i.media.kind == "image"
        and i.text is None
        and i.sender == "919876543210"
    )
    grp = {
        "messages": [
            {
                "id": "G",
                "type": "text",
                "from_me": True,
                "chat_id": "123@g.us",
                "from": "9198",
                "text": {"body": "x"},
            }
        ]
    }
    g = wa.parse_webhook(grp)[0]
    assert g.is_group and g.from_me and g.sender is None  # malformed sender -> ignored, not a crash
    assert (
        wa.parse_webhook(
            {"statuses": [{"id": "x", "status": "read"}], "event": {"type": "statuses"}}
        )[0].event_type
        == "status"
    )


@respx.mock
def test_send_text_and_document(wa: WasenderDevProvider) -> None:
    route = respx.post(f"{BASE}/messages/text").mock(
        return_value=httpx.Response(
            200, json={"sent": True, "message": {"id": "out1", "type": "text", "status": "pending"}}
        )
    )
    assert wa.send_text("919876543210", "hello").provider_message_id == "out1"
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer wsk_sg_test"
    assert json.loads(req.read()) == {"to": "919876543210", "body": "hello"}  # no '+'
    doc = respx.post(f"{BASE}/messages/document").mock(
        return_value=httpx.Response(200, json={"sent": True, "message": {"id": "out2"}})
    )
    wa.send_document(
        "919876543210",
        document_url="https://api.example.com/api/v1/media/t",
        filename="a.pdf",
        caption=None,
    )
    assert json.loads(doc.calls[0].request.read()) == {
        "to": "919876543210",
        "media": "https://api.example.com/api/v1/media/t",
        "filename": "a.pdf",
    }


@respx.mock
@pytest.mark.parametrize(
    ("status", "transient"),
    [(429, True), (503, True), (500, True), (401, False), (402, False), (403, False)],
)
def test_error_classification(wa: WasenderDevProvider, status: int, transient: bool) -> None:
    respx.post(f"{BASE}/messages/text").mock(
        return_value=httpx.Response(
            status,
            json={"error": {"code": status, "message": "nope"}},
            headers={"retry-after": "9"},
        )
    )
    with pytest.raises(ProviderError) as exc:
        wa.send_text("1", "x")
    assert exc.value.transient is transient and exc.value.status_code == status
    assert exc.value.retry_after == 9


@respx.mock
def test_media_download_with_size_cap(wa: WasenderDevProvider) -> None:
    respx.get(f"{BASE}/media/med_1").mock(return_value=httpx.Response(200, content=b"%PDF-1.4 x"))
    ref = MediaRef(
        kind="document",
        mime_type="application/pdf",
        filename="a.pdf",
        provider_ref={"media_id": "med_1"},
    )
    assert wa.download_media(ref, 1000).data == b"%PDF-1.4 x"
    respx.get(f"{BASE}/media/big").mock(return_value=httpx.Response(200, content=b"x" * 5000))
    with pytest.raises(ProviderError, match="too large"):
        wa.download_media(MediaRef(kind="document", provider_ref={"media_id": "big"}), 100)
    respx.get(f"{BASE}/media/gone").mock(
        return_value=httpx.Response(404, json={"error": {"code": 404, "message": "not found"}})
    )
    with pytest.raises(ProviderError) as exc:
        wa.download_media(MediaRef(kind="document", provider_ref={"media_id": "gone"}), 100)
    assert not exc.value.transient


@respx.mock
def test_health(wa: WasenderDevProvider) -> None:
    respx.get(f"{BASE}/health").mock(
        return_value=httpx.Response(200, json={"status": {"code": 4, "text": "connected"}})
    )
    assert wa.connection_status().connected
    respx.get(f"{BASE}/health").mock(
        return_value=httpx.Response(200, json={"status": {"text": "hibernated"}})
    )
    assert wa.connection_status().connected
    respx.get(f"{BASE}/health").mock(
        return_value=httpx.Response(401, json={"error": {"code": 401}})
    )
    assert wa.connection_status().status == "unauthorized"


@respx.mock
def test_end_to_end_through_webhook(client: TestClient, db: Session) -> None:
    """Signed wasender.dev webhook -> download -> stored in (fake) Drive -> confirmation sent."""
    w = make_world(db, "WDev Co")
    conn = IntegrationConfig(
        tenant_id=w.tenant.id,
        kind=IntegrationKind.whatsapp,
        provider="wasender_dev",
        name="wasender.dev",
        status=IntegrationStatus.connected,
        connection_key=new_token(24),
        encrypted_secrets=encrypt_json({"api_key": "wsk_sg_test", "webhook_secret": SECRET}),
        settings={"phone_number": "919686071617"},
    )
    db.add(conn)
    db.commit()
    pdf = syn.pdf_with_text(["TAX INVOICE", "ABC Traders", "15/09/2026"])
    respx.get(f"{BASE}/media/med_9").mock(return_value=httpx.Response(200, content=pdf))
    sends = respx.post(f"{BASE}/messages/text").mock(
        return_value=httpx.Response(200, json={"sent": True, "message": {"id": "o"}})
    )
    payload = {
        "messages": [
            {
                "id": "IN1",
                "type": "document",
                "chat_id": f"{w.sender}@s.whatsapp.net",
                "from": w.sender,
                "document": {
                    "id": "med_9",
                    "mime_type": "application/pdf",
                    "file_size": len(pdf),
                    "file_name": "scan.pdf",
                    "caption": "invoice from ABC Traders",
                },
            }
        ]
    }
    raw = json.dumps(payload).encode()
    url = f"/api/v1/webhooks/wasender_dev/{conn.connection_key}"
    assert (
        client.post(
            url,
            content=raw,
            headers={"x-wasender-signature": "sha256=bad", "content-type": "application/json"},
        ).status_code
        == 403
    )
    r = client.post(
        url,
        content=raw,
        headers={"x-wasender-signature": sign(raw), "content-type": "application/json"},
    )
    assert r.status_code == 200 and r.json()["accepted"] == 1
    drain()
    doc = db.scalar(select(Document).where(Document.tenant_id == w.tenant.id))
    assert doc is not None and doc.status == "stored" and doc.document_type == "Invoice"
    bodies = [json.loads(c.request.read())["body"] for c in sends.calls]
    assert any(b.startswith("✅ Saved") for b in bodies)


def test_connect_form_requires_both_secrets(client: TestClient, db: Session) -> None:
    w = make_world(db, "Form Co")
    csrf = login(client, w.user.email)
    h = {"x-csrf-token": csrf}
    r = client.post(
        f"/api/v1/tenants/{w.tenant.id}/integrations/whatsapp",
        json={
            "provider": "wasender_dev",
            "phone_number": "+91 96860 71617",
            "api_key": "wsk_sg_abcdef12",
        },
        headers=h,
    )
    assert r.status_code == 400
    r = client.post(
        f"/api/v1/tenants/{w.tenant.id}/integrations/whatsapp",
        json={
            "provider": "wasender_dev",
            "phone_number": "+91 96860 71617",
            "api_key": "wsk_sg_abcdef12",
            "webhook_secret": "whsec_abcdefgh",
        },
        headers=h,
    )
    assert r.status_code == 201 and "/api/v1/webhooks/wasender_dev/" in r.json()["webhook_url"]

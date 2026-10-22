"""WasenderAPI adapter — MOCKED HTTP (respx). Payload shapes follow the SDK-derived notes in
docs/architecture/provider-notes.md and must be re-verified against a live account."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.providers.errors import ProviderError
from app.providers.whatsapp.wasender import WasenderProvider
from app.schemas.events import MediaRef

BASE = "https://www.wasenderapi.com/api"


@pytest.fixture
def wa() -> WasenderProvider:
    return WasenderProvider(
        api_key="session-key-123", webhook_secret="hook-secret-abc", base_url=BASE
    )


def _msg(**message: object) -> dict[str, object]:
    return {
        "key": {"id": "3EB0ABC", "fromMe": False, "remoteJid": "919876543210@s.whatsapp.net"},
        "pushName": "Ravi",
        "messageTimestamp": 1761100000,
        "message": message,
    }


def test_signature_verification(wa: WasenderProvider) -> None:
    assert wa.verify_webhook({"X-Webhook-Signature": "hook-secret-abc"}, b"{}")
    assert not wa.verify_webhook({"x-webhook-signature": "wrong"}, b"{}")
    assert not wa.verify_webhook({}, b"{}")


@pytest.mark.parametrize("shape", ["messages_obj", "messages_list", "flat"])
def test_parse_text_message_shapes(wa: WasenderProvider, shape: str) -> None:
    m = _msg(conversation="find all invoices")
    data = (
        {"messages": m}
        if shape == "messages_obj"
        else {"messages": [m]}
        if shape == "messages_list"
        else m
    )
    events = wa.parse_webhook({"event": "messages.received", "data": data})
    assert len(events) == 1
    ev = events[0]
    assert (ev.event_id, ev.sender, ev.text, ev.event_type) == (
        "3EB0ABC",
        "919876543210",
        "find all invoices",
        "message",
    )
    assert ev.sender_name == "Ravi" and ev.timestamp is not None


def test_parse_extended_text_and_clean_sender(wa: WasenderProvider) -> None:
    m = _msg(extendedTextMessage={"text": "hello"})
    m["key"] = {
        "id": "X1",
        "fromMe": False,
        "remoteJid": "12345@lid",
        "cleanedSenderPn": "919811112222",
    }
    ev = wa.parse_webhook({"event": "messages-personal.received", "data": {"messages": m}})[0]
    assert ev.sender == "919811112222" and ev.text == "hello"


def test_parse_image_with_caption(wa: WasenderProvider) -> None:
    m = _msg(
        imageMessage={
            "url": "https://mmg.whatsapp.net/x",
            "mimetype": "image/jpeg",
            "caption": "receipt from Dev Construction",
            "fileLength": "2048",
        }
    )
    ev = wa.parse_webhook({"event": "messages.upsert", "data": {"messages": m}})[0]
    assert ev.media is not None and ev.media.kind == "image"
    assert ev.media.mime_type == "image/jpeg" and ev.media.size_bytes == 2048
    assert ev.text == "receipt from Dev Construction"
    assert ev.media.provider_ref["envelope"] == "messages"


def test_parse_document_with_caption_wrapper(wa: WasenderProvider) -> None:
    m = _msg(
        documentWithCaptionMessage={
            "message": {
                "documentMessage": {
                    "mimetype": "application/pdf",
                    "fileName": "INV-42.pdf",
                    "caption": "invoice ABC",
                }
            }
        }
    )
    ev = wa.parse_webhook({"event": "messages.received", "data": {"messages": m}})[0]
    assert ev.media is not None and ev.media.filename == "INV-42.pdf" and ev.text == "invoice ABC"


def test_from_me_group_and_other_events(wa: WasenderProvider) -> None:
    m = _msg(conversation="x")
    m["key"] = {
        "id": "G1",
        "fromMe": True,
        "remoteJid": "120363@g.us",
        "participantPn": "919800000000",
    }
    ev = wa.parse_webhook({"event": "messages.upsert", "data": {"messages": m}})[0]
    assert ev.from_me and ev.is_group
    other = wa.parse_webhook(
        {"event": "session.status", "timestamp": 1, "data": {"status": "connected"}}
    )
    assert other[0].event_type == "status"
    assert (
        wa.parse_webhook({"event": "messages.received", "data": {"messages": {"nokey": 1}}}) == []
    )


@respx.mock
def test_send_text_payload_and_message_id(wa: WasenderProvider) -> None:
    route = respx.post(f"{BASE}/send-message").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "msgId": 98765,
                    "jid": "919876543210@s.whatsapp.net",
                    "status": "in_progress",
                },
            },
        )
    )
    res = wa.send_text("919876543210", "hello")
    assert res.provider_message_id == "98765"
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer session-key-123"
    assert req.read() == b'{"to":"+919876543210","text":"hello"}'


@respx.mock
def test_send_document_by_url(wa: WasenderProvider) -> None:
    import json

    route = respx.post(f"{BASE}/send-message").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"msgId": 1}})
    )
    wa.send_document(
        "919876543210",
        document_url="https://api.example.com/api/v1/media/tok",
        filename="a.pdf",
        caption=None,
    )
    body = json.loads(route.calls[0].request.read())
    assert body == {
        "to": "+919876543210",
        "documentUrl": "https://api.example.com/api/v1/media/tok",
        "fileName": "a.pdf",
    }


@respx.mock
@pytest.mark.parametrize(
    ("status", "transient"), [(429, True), (500, True), (503, True), (401, False), (422, False)]
)
def test_error_classification(wa: WasenderProvider, status: int, transient: bool) -> None:
    respx.post(f"{BASE}/send-message").mock(
        return_value=httpx.Response(status, json={"message": "nope", "retry_after": 7})
    )
    with pytest.raises(ProviderError) as exc:
        wa.send_text("919876543210", "x")
    assert exc.value.transient is transient and exc.value.status_code == status
    if status == 429:
        assert exc.value.retry_after == 7


@respx.mock
def test_timeout_is_transient(wa: WasenderProvider) -> None:
    respx.post(f"{BASE}/send-message").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderError) as exc:
        wa.send_text("919876543210", "x")
    assert exc.value.transient


@respx.mock
def test_success_false_is_permanent(wa: WasenderProvider) -> None:
    respx.post(f"{BASE}/send-message").mock(
        return_value=httpx.Response(200, json={"success": False, "message": "invalid number"})
    )
    with pytest.raises(ProviderError) as exc:
        wa.send_text("919876543210", "x")
    assert not exc.value.transient


@respx.mock
def test_decrypt_and_download_media(wa: WasenderProvider) -> None:
    import json

    msg = _msg(documentMessage={"mimetype": "application/pdf", "fileName": "a.pdf"})
    route = respx.post(f"{BASE}/decrypt-media").mock(
        return_value=httpx.Response(
            200, json={"success": True, "publicUrl": "https://files.wasenderapi.com/tmp/a.pdf"}
        )
    )
    respx.get("https://files.wasenderapi.com/tmp/a.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 data")
    )
    ref = MediaRef(
        kind="document",
        mime_type="application/pdf",
        filename="a.pdf",
        provider_ref={"message": msg, "envelope": "messages"},
    )
    out = wa.download_media(ref, max_bytes=1000)
    assert out.data == b"%PDF-1.4 data"
    assert json.loads(route.calls[0].request.read()) == {"data": {"messages": msg}}


@respx.mock
def test_download_rejects_oversize_and_non_https(wa: WasenderProvider) -> None:
    msg = _msg(documentMessage={})
    ref = MediaRef(kind="document", provider_ref={"message": msg, "envelope": "flat"})
    respx.post(f"{BASE}/decrypt-media").mock(
        return_value=httpx.Response(200, json={"data": {"url": "https://f.example.com/x"}})
    )
    respx.get("https://f.example.com/x").mock(return_value=httpx.Response(200, content=b"x" * 5000))
    with pytest.raises(ProviderError, match="too large"):
        wa.download_media(ref, max_bytes=100)
    respx.post(f"{BASE}/decrypt-media").mock(
        return_value=httpx.Response(200, json={"publicUrl": "http://insecure/x"})
    )
    with pytest.raises(ProviderError, match="https"):
        wa.download_media(ref, max_bytes=100)


@respx.mock
def test_connection_status(wa: WasenderProvider) -> None:
    respx.get(f"{BASE}/status").mock(return_value=httpx.Response(200, json={"status": "connected"}))
    assert wa.connection_status().connected
    respx.get(f"{BASE}/status").mock(return_value=httpx.Response(401, json={"message": "bad key"}))
    st = wa.connection_status()
    assert not st.connected and st.status == "unauthorized"

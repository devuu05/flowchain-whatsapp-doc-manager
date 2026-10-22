"""WasenderAPI adapter.

Details come from WasenderAPI's own SDKs; see docs/architecture/provider-notes.md for the
verification status of every field. Parsing is defensive because verbatim webhook payloads
could not be verified from the live docs during development.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.providers.errors import ProviderError, is_transient_status
from app.providers.whatsapp.base import (
    ConnectionStatus,
    DownloadedMedia,
    SendResult,
    WhatsAppProvider,
)
from app.schemas.events import MediaRef, NormalizedEvent
from app.security.crypto import constant_time_equals

log = logging.getLogger(__name__)

MESSAGE_EVENTS = {
    "messages.received",
    "messages.upsert",
    "messages-personal.received",
}
SIGNATURE_HEADER = "x-webhook-signature"
PROVIDER = "wasender"


def _jid_to_phone(jid: str | None) -> str | None:
    if not jid or not isinstance(jid, str):
        return None
    user = jid.split("@", 1)[0].split(":", 1)[0]
    digits = "".join(c for c in user if c.isdigit())
    return digits or None


def _unwrap_message(message: dict[str, Any]) -> dict[str, Any]:
    """WhatsApp wraps some messages (captioned documents, ephemeral, view-once)."""
    for wrapper in (
        "documentWithCaptionMessage",
        "ephemeralMessage",
        "viewOnceMessage",
        "viewOnceMessageV2",
    ):
        inner = message.get(wrapper)
        if isinstance(inner, dict) and isinstance(inner.get("message"), dict):
            return _unwrap_message(inner["message"])
    return message


def _message_objects(data: Any) -> list[tuple[dict[str, Any], str]]:
    """Accept `data` as the message itself ("flat"), or `data.messages` as object or list.

    Returns (message, envelope) so the media download can echo the original shape back.
    """
    if not isinstance(data, dict):
        return []
    msgs = data.get("messages")
    if isinstance(msgs, list):
        return [(m, "messages") for m in msgs if isinstance(m, dict)]
    if isinstance(msgs, dict):
        return [(msgs, "messages")]
    if "key" in data:
        return [(data, "flat")]
    return []


class WasenderProvider(WhatsAppProvider):
    name = PROVIDER
    sends_documents_by_url = True

    def __init__(
        self,
        *,
        api_key: str,
        webhook_secret: str,
        base_url: str,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._webhook_secret = webhook_secret
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            transport=transport,
        )
        self._download_client = httpx.Client(timeout=timeout, transport=transport)

    # ---- webhooks -------------------------------------------------------------------------
    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        provided = {k.lower(): v for k, v in headers.items()}.get(SIGNATURE_HEADER)
        return constant_time_equals(provided, self._webhook_secret)

    def parse_webhook(self, payload: dict[str, Any]) -> list[NormalizedEvent]:
        event_name = str(payload.get("event", ""))
        if event_name not in MESSAGE_EVENTS:
            ts = payload.get("timestamp")
            return [
                NormalizedEvent(
                    provider=PROVIDER,
                    event_id=f"{event_name or 'unknown'}:{ts or datetime.now(UTC).timestamp()}",
                    event_type="status" if event_name.startswith("session") else "other",
                )
            ]
        events: list[NormalizedEvent] = []
        for msg, envelope in _message_objects(payload.get("data")):
            ev = self._parse_message(msg, envelope)
            if ev is not None:
                events.append(ev)
        return events

    def _parse_message(self, msg: dict[str, Any], envelope: str) -> NormalizedEvent | None:
        key = msg.get("key") if isinstance(msg.get("key"), dict) else {}
        assert isinstance(key, dict)
        msg_id = key.get("id") or msg.get("id")
        if not msg_id:
            return None
        remote = str(key.get("remoteJid") or "")
        is_group = remote.endswith("@g.us")
        sender = (
            _jid_to_phone(key.get("cleanedSenderPn"))
            or _jid_to_phone(key.get("cleanSenderPn"))
            or _jid_to_phone(key.get("senderPn"))
            or (_jid_to_phone(key.get("participantPn")) if is_group else None)
            or (_jid_to_phone(remote) if remote.endswith("@s.whatsapp.net") else None)
        )
        message = msg.get("message") if isinstance(msg.get("message"), dict) else {}
        assert isinstance(message, dict)
        message = _unwrap_message(message)

        text: str | None = None
        media: MediaRef | None = None
        if isinstance(message.get("conversation"), str):
            text = message["conversation"]
        elif isinstance(message.get("extendedTextMessage"), dict):
            text = message["extendedTextMessage"].get("text")
        for kind, field in (
            ("image", "imageMessage"),
            ("document", "documentMessage"),
            ("video", "videoMessage"),
            ("audio", "audioMessage"),
            ("sticker", "stickerMessage"),
        ):
            part = message.get(field)
            if isinstance(part, dict):
                size = part.get("fileLength")
                try:
                    size_int = int(size) if size is not None else None
                except (TypeError, ValueError):
                    size_int = None
                media = MediaRef(
                    kind=kind,
                    mime_type=(part.get("mimetype") or None),
                    filename=(part.get("fileName") or part.get("title") or None),
                    size_bytes=size_int,
                    provider_ref={"message": msg, "envelope": envelope},
                )
                text = part.get("caption") or text
                break
        if text is None and isinstance(msg.get("messageBody"), str) and media is None:
            text = msg["messageBody"]

        ts = msg.get("messageTimestamp")
        try:
            timestamp = datetime.fromtimestamp(int(ts), UTC) if ts else None
        except (TypeError, ValueError, OverflowError):
            timestamp = None
        if text is not None:
            text = text[:4096]
        return NormalizedEvent(
            provider=PROVIDER,
            event_id=str(msg_id)[:200],
            event_type="message",
            sender=sender,
            sender_name=(str(msg.get("pushName"))[:120] if msg.get("pushName") else None),
            from_me=bool(key.get("fromMe")),
            is_group=is_group,
            timestamp=timestamp,
            text=text,
            media=media,
        )

    # ---- HTTP helpers ---------------------------------------------------------------------
    def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._client.post(path, json=json)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", provider=PROVIDER, transient=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError(
                f"connection error: {type(exc).__name__}", provider=PROVIDER, transient=True
            ) from exc
        return self._handle(resp)

    def _handle(self, resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code >= 400:
            retry_after: float | None = None
            message = f"HTTP {resp.status_code}"
            try:
                body = resp.json()
                if isinstance(body, dict):
                    message = str(body.get("message") or body.get("error") or message)[:300]
                    if body.get("retry_after") is not None:
                        retry_after = float(body["retry_after"])
            except (ValueError, TypeError):
                pass
            if retry_after is None and resp.headers.get("retry-after", "").isdigit():
                retry_after = float(resp.headers["retry-after"])
            raise ProviderError(
                message,
                provider=PROVIDER,
                transient=is_transient_status(resp.status_code),
                status_code=resp.status_code,
                retry_after=retry_after,
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError("invalid JSON response", provider=PROVIDER, transient=True) from exc
        if isinstance(body, dict) and body.get("success") is False:
            raise ProviderError(
                str(body.get("message", "request rejected"))[:300],
                provider=PROVIDER,
                transient=False,
                status_code=resp.status_code,
            )
        return body if isinstance(body, dict) else {"data": body}

    @staticmethod
    def _msg_id(body: dict[str, Any]) -> SendResult:
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        assert isinstance(data, dict)
        mid = data.get("msgId") or data.get("id")
        return SendResult(
            provider_message_id=str(mid) if mid is not None else None,
            raw_status=data.get("status"),
        )

    # ---- sending --------------------------------------------------------------------------
    def send_text(self, to: str, text: str) -> SendResult:
        return self._msg_id(self._post("/send-message", {"to": f"+{to}", "text": text}))

    def send_document(
        self, to: str, *, document_url: str, filename: str, caption: str | None
    ) -> SendResult:
        payload: dict[str, Any] = {
            "to": f"+{to}",
            "documentUrl": document_url,
            "fileName": filename,
        }
        if caption:
            payload["text"] = caption
        return self._msg_id(self._post("/send-message", payload))

    # ---- media ----------------------------------------------------------------------------
    def download_media(self, media: MediaRef, max_bytes: int) -> DownloadedMedia:
        message = media.provider_ref.get("message")
        if not isinstance(message, dict):
            raise ProviderError("missing media reference", provider=PROVIDER, transient=False)
        # SDKs post {"data": <webhook message object>}; echo the envelope shape we received.
        data_obj = (
            message if media.provider_ref.get("envelope") == "flat" else {"messages": message}
        )
        body = self._post("/decrypt-media", {"data": data_obj})
        url = None
        for container in (body, body.get("data") if isinstance(body.get("data"), dict) else {}):
            assert isinstance(container, dict)
            url = url or container.get("publicUrl") or container.get("url")
        if not url or not isinstance(url, str) or not url.startswith("https://"):
            raise ProviderError(
                "decrypt-media response did not include an https URL",
                provider=PROVIDER,
                transient=False,
            )
        data = self._stream_download(url, max_bytes)
        return DownloadedMedia(data=data, mime_type=media.mime_type, filename=media.filename)

    def _stream_download(self, url: str, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        try:
            with self._download_client.stream("GET", url, follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    raise ProviderError(
                        f"media download HTTP {resp.status_code}",
                        provider=PROVIDER,
                        transient=is_transient_status(resp.status_code),
                        status_code=resp.status_code,
                    )
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise ProviderError("media too large", provider=PROVIDER, transient=False)
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ProviderError("media too large", provider=PROVIDER, transient=False)
                    chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "media download timeout", provider=PROVIDER, transient=True
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderError(
                "media download connection error", provider=PROVIDER, transient=True
            ) from exc
        return b"".join(chunks)

    # ---- status ---------------------------------------------------------------------------
    def connection_status(self) -> ConnectionStatus:
        try:
            resp = self._client.get("/status")
        except httpx.HTTPError as exc:
            return ConnectionStatus(False, "unreachable", type(exc).__name__)
        if resp.status_code in (401, 403):
            return ConnectionStatus(False, "unauthorized", "API key rejected")
        try:
            body = self._handle(resp)
        except ProviderError as exc:
            return ConnectionStatus(False, "error", str(exc))
        status = str(body.get("status") or (body.get("data") or {}).get("status") or "unknown")
        return ConnectionStatus(status == "connected", status)

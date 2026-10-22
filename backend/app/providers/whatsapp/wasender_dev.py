"""wasender.dev adapter (https://api.wasender.dev).

Verified from wasender.dev's published llms-full.txt (2026-10-22):
- Bearer channel token (`wsk_...`) per connected number; base URL https://api.wasender.dev
- POST /messages/text {"to": "<digits, no +>", "body"} -> {"sent": true, "message": {"id", ...}}
- POST /messages/document, GET /media/{MediaID} (download), GET /health
- Webhooks signed: header `x-wasender-signature: sha256=<hex>`, HMAC-SHA256 of the RAW body
- Errors: {"error": {"code", "message", ...}}; 429 (Retry-After) / 503 are retryable
- The API is declared request/response compatible with whapi.cloud.

UNVERIFIED (field names taken from whapi.cloud's documented format because the endpoint list did
not include them): webhook payload shape (`messages[]` with `type`, `text.body`,
`image|document.{id, mime_type, file_size, file_name, caption, link}`, `from`, `chat_id`,
`from_me`) and the document-send body (`media` URL, `filename`, `caption`). Confirm with the
first live test; see docs/testing/manual-provider-tests.md.
"""

from __future__ import annotations

import hashlib
import hmac
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

log = logging.getLogger(__name__)

PROVIDER = "wasender_dev"
SIGNATURE_HEADER = "x-wasender-signature"
MEDIA_TYPES = ("image", "document", "video", "audio", "voice", "sticker")
HEALTHY = {"connected", "hibernated"}


def _digits(value: Any) -> str | None:
    if not isinstance(value, str | int):
        return None
    user = str(value).split("@", 1)[0].split(":", 1)[0]
    digits = "".join(c for c in user if c.isdigit())
    # Malformed ids must not break parsing of the whole webhook batch.
    return digits if 6 <= len(digits) <= 20 else None


class WasenderDevProvider(WhatsAppProvider):
    name = PROVIDER
    sends_documents_by_url = True

    def __init__(
        self,
        *,
        token: str,
        webhook_secret: str,
        base_url: str = "https://api.wasender.dev",
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._webhook_secret = webhook_secret
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            transport=transport,
        )

    # ---- webhooks -------------------------------------------------------------------------
    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        provided = {k.lower(): v for k, v in headers.items()}.get(SIGNATURE_HEADER, "")
        if not self._webhook_secret or not provided.startswith("sha256="):
            return False
        expected = hmac.new(self._webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(provided[len("sha256=") :].strip().lower(), expected)

    def parse_webhook(self, payload: dict[str, Any]) -> list[NormalizedEvent]:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
            assert isinstance(event, dict)
            kind = str(event.get("type") or "other")
            marker = f"{kind}:{datetime.now(UTC).timestamp()}"
            return [
                NormalizedEvent(
                    provider=PROVIDER,
                    event_id=marker[:200],
                    event_type="status" if kind in ("statuses", "channel", "health") else "other",
                )
            ]
        events = []
        for msg in messages:
            if isinstance(msg, dict):
                ev = self._parse_message(msg)
                if ev is not None:
                    events.append(ev)
        return events

    def _parse_message(self, msg: dict[str, Any]) -> NormalizedEvent | None:
        msg_id = msg.get("id")
        if not msg_id:
            return None
        chat_id = str(msg.get("chat_id") or "")
        is_group = chat_id.endswith("@g.us")
        sender = _digits(msg.get("from")) or (None if is_group else _digits(chat_id))
        mtype = str(msg.get("type") or "")

        text: str | None = None
        media: MediaRef | None = None
        if mtype == "text" and isinstance(msg.get("text"), dict):
            text = msg["text"].get("body")
        elif mtype == "link_preview" and isinstance(msg.get("link_preview"), dict):
            text = msg["link_preview"].get("body")
        elif mtype in MEDIA_TYPES and isinstance(msg.get(mtype), dict):
            part = msg[mtype]
            try:
                size = int(part["file_size"]) if part.get("file_size") is not None else None
            except (TypeError, ValueError):
                size = None
            kind = "audio" if mtype == "voice" else mtype
            media = MediaRef(
                kind=kind,
                mime_type=part.get("mime_type") or None,
                filename=part.get("file_name") or part.get("filename") or None,
                size_bytes=size,
                provider_ref={"media_id": part.get("id"), "link": part.get("link")},
            )
            text = part.get("caption") or None

        ts = msg.get("timestamp")
        try:
            timestamp = datetime.fromtimestamp(int(ts), UTC) if ts else None
        except (TypeError, ValueError, OverflowError):
            timestamp = None
        return NormalizedEvent(
            provider=PROVIDER,
            event_id=str(msg_id)[:200],
            event_type="message",
            sender=sender,
            sender_name=str(msg["from_name"])[:120] if msg.get("from_name") else None,
            from_me=bool(msg.get("from_me")),
            is_group=is_group,
            timestamp=timestamp,
            text=text[:4096] if isinstance(text, str) else None,
            media=media,
        )

    # ---- HTTP -----------------------------------------------------------------------------
    @staticmethod
    def _raise_for(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        message = f"HTTP {resp.status_code}"
        try:
            err = resp.json().get("error", {})
            if isinstance(err, dict) and err.get("message"):
                message = f"{message} {str(err['message'])[:200]}"
        except (ValueError, AttributeError):
            pass
        retry_after = resp.headers.get("retry-after", "")
        raise ProviderError(
            message,
            provider=PROVIDER,
            transient=is_transient_status(resp.status_code),
            status_code=resp.status_code,
            retry_after=float(retry_after) if retry_after.isdigit() else None,
        )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._client.post(path, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", provider=PROVIDER, transient=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError("connection error", provider=PROVIDER, transient=True) from exc
        self._raise_for(resp)
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError("invalid JSON response", provider=PROVIDER, transient=True) from exc
        if not isinstance(data, dict):
            raise ProviderError("unexpected response", provider=PROVIDER, transient=True)
        if data.get("sent") is False:
            raise ProviderError("message not accepted", provider=PROVIDER, transient=False)
        return data

    @staticmethod
    def _result(data: dict[str, Any]) -> SendResult:
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        assert isinstance(message, dict)
        mid = message.get("id")
        return SendResult(
            provider_message_id=str(mid) if mid else None, raw_status=message.get("status")
        )

    # ---- sending --------------------------------------------------------------------------
    def send_text(self, to: str, text: str) -> SendResult:
        return self._result(self._post("/messages/text", {"to": to, "body": text}))

    def send_document(
        self, to: str, *, document_url: str, filename: str, caption: str | None
    ) -> SendResult:
        body: dict[str, Any] = {"to": to, "media": document_url, "filename": filename}
        if caption:
            body["caption"] = caption
        return self._result(self._post("/messages/document", body))

    # ---- media ----------------------------------------------------------------------------
    def download_media(self, media: MediaRef, max_bytes: int) -> DownloadedMedia:
        media_id = media.provider_ref.get("media_id")
        if not media_id:
            raise ProviderError("missing media id", provider=PROVIDER, transient=False)
        chunks: list[bytes] = []
        total = 0
        try:
            with self._client.stream("GET", f"/media/{media_id}", follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    self._raise_for(resp)
                declared = resp.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_bytes:
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
            raise ProviderError("media download failed", provider=PROVIDER, transient=True) from exc
        return DownloadedMedia(
            data=b"".join(chunks), mime_type=media.mime_type, filename=media.filename
        )

    # ---- status ---------------------------------------------------------------------------
    def connection_status(self) -> ConnectionStatus:
        try:
            resp = self._client.get("/health")
        except httpx.HTTPError as exc:
            return ConnectionStatus(False, "unreachable", type(exc).__name__)
        if resp.status_code == 401:
            return ConnectionStatus(False, "unauthorized", "channel token rejected")
        if resp.status_code >= 400:
            return ConnectionStatus(False, "error", f"HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError:
            return ConnectionStatus(False, "error", "invalid JSON")
        status = body.get("status") if isinstance(body, dict) else None
        text = str(status.get("text") if isinstance(status, dict) else status or "unknown")
        return ConnectionStatus(text.lower() in HEALTHY, text.lower())

"""FAKE WhatsApp provider for tests and local development. Never used in production.

Webhook payloads are already-normalized events: {"events": [NormalizedEvent, ...]}.
Media bytes are supplied inline as base64 in `media.provider_ref.b64`.
"""

from __future__ import annotations

import base64
import itertools
from typing import Any

from app.providers.errors import ProviderError
from app.providers.whatsapp.base import (
    ConnectionStatus,
    DownloadedMedia,
    SendResult,
    WhatsAppProvider,
)
from app.schemas.events import MediaRef, NormalizedEvent
from app.security.crypto import constant_time_equals

_ids = itertools.count(1)


class FakeWhatsAppProvider(WhatsAppProvider):
    name = "fake"
    sends_documents_by_url = True

    def __init__(self, webhook_secret: str = "fake-secret") -> None:  # noqa: S107
        self.webhook_secret = webhook_secret
        self.sent: list[dict[str, Any]] = []
        self.fail_next_sends: list[ProviderError] = []

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        provided = {k.lower(): v for k, v in headers.items()}.get("x-webhook-signature")
        return constant_time_equals(provided, self.webhook_secret)

    def parse_webhook(self, payload: dict[str, Any]) -> list[NormalizedEvent]:
        return [NormalizedEvent.model_validate(e) for e in payload.get("events", [])]

    def _maybe_fail(self) -> None:
        if self.fail_next_sends:
            raise self.fail_next_sends.pop(0)

    def send_text(self, to: str, text: str) -> SendResult:
        self._maybe_fail()
        mid = f"fake-{next(_ids)}"
        self.sent.append({"type": "text", "to": to, "text": text, "id": mid})
        return SendResult(provider_message_id=mid, raw_status="sent")

    def send_document(
        self, to: str, *, document_url: str, filename: str, caption: str | None
    ) -> SendResult:
        self._maybe_fail()
        mid = f"fake-{next(_ids)}"
        self.sent.append(
            {
                "type": "document",
                "to": to,
                "url": document_url,
                "filename": filename,
                "caption": caption,
                "id": mid,
            }
        )
        return SendResult(provider_message_id=mid, raw_status="sent")

    def download_media(self, media: MediaRef, max_bytes: int) -> DownloadedMedia:
        b64 = media.provider_ref.get("b64")
        if not isinstance(b64, str):
            raise ProviderError("no media bytes", provider="fake", transient=False)
        data = base64.b64decode(b64)
        if len(data) > max_bytes:
            raise ProviderError("media too large", provider="fake", transient=False)
        return DownloadedMedia(data=data, mime_type=media.mime_type, filename=media.filename)

    def connection_status(self) -> ConnectionStatus:
        return ConnectionStatus(True, "connected", "fake provider")

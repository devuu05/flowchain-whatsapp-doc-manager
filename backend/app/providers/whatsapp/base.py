"""WhatsAppProvider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.schemas.events import MediaRef, NormalizedEvent


@dataclass
class SendResult:
    provider_message_id: str | None
    raw_status: str | None = None


@dataclass
class DownloadedMedia:
    data: bytes
    mime_type: str | None
    filename: str | None


@dataclass
class ConnectionStatus:
    connected: bool
    status: str
    detail: str | None = None


class WhatsAppProvider(ABC):
    name: str = "abstract"
    # Whether documents must be sent by publicly reachable URL (vs. direct upload).
    sends_documents_by_url: bool = True

    @abstractmethod
    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool: ...

    @abstractmethod
    def parse_webhook(self, payload: dict[str, Any]) -> list[NormalizedEvent]: ...

    @abstractmethod
    def send_text(self, to: str, text: str) -> SendResult: ...

    @abstractmethod
    def send_document(
        self, to: str, *, document_url: str, filename: str, caption: str | None
    ) -> SendResult: ...

    @abstractmethod
    def download_media(self, media: MediaRef, max_bytes: int) -> DownloadedMedia: ...

    @abstractmethod
    def connection_status(self) -> ConnectionStatus: ...

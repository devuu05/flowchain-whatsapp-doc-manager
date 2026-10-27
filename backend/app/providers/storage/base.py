"""DocumentStorage interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class StoredFile:
    file_id: str
    name: str
    folder_id: str | None
    size_bytes: int | None
    md5: str | None
    mime_type: str | None


class DocumentStorage(ABC):
    name: str = "abstract"

    @abstractmethod
    def ensure_folder_path(self, path: list[str]) -> str:
        """Return the id of the folder at `path` below the tenant root, creating as needed."""

    @abstractmethod
    def find_by_document_id(self, document_id: str) -> StoredFile | None:
        """Find a previously uploaded file for this document (idempotency/reconciliation)."""

    @abstractmethod
    def upload(
        self, *, data: bytes, filename: str, mime_type: str, folder_id: str, document_id: str
    ) -> StoredFile: ...

    @abstractmethod
    def download(self, file_id: str, max_bytes: int) -> bytes: ...

    @abstractmethod
    def get_metadata(self, file_id: str) -> StoredFile: ...

    @abstractmethod
    def rename(self, file_id: str, new_name: str) -> StoredFile:
        """Rename a stored file in place (same folder)."""

    @abstractmethod
    def trash(self, file_id: str) -> None: ...

    @abstractmethod
    def check(self) -> tuple[bool, str]:
        """Lightweight connectivity check: (ok, detail)."""

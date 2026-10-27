"""FAKE in-memory DocumentStorage for tests/local development. Never used in production."""

from __future__ import annotations

import hashlib
import itertools

from app.providers.errors import ProviderError
from app.providers.storage.base import DocumentStorage, StoredFile

_ids = itertools.count(1)


class FakeStorage(DocumentStorage):
    name = "fake"

    def __init__(self) -> None:
        self.folders: dict[tuple[str, str], str] = {}
        self.files: dict[str, tuple[StoredFile, bytes, str]] = {}
        self.fail_uploads: list[ProviderError] = []
        self.upload_calls = 0

    def ensure_folder_path(self, path: list[str]) -> str:
        parent = "root"
        for name in path:
            parent = self.folders.setdefault((parent, name), f"folder-{next(_ids)}")
        return parent

    def find_by_document_id(self, document_id: str) -> StoredFile | None:
        for stored, _, doc_id in self.files.values():
            if doc_id == document_id:
                return stored
        return None

    def upload(
        self, *, data: bytes, filename: str, mime_type: str, folder_id: str, document_id: str
    ) -> StoredFile:
        self.upload_calls += 1
        if self.fail_uploads:
            raise self.fail_uploads.pop(0)
        fid = f"file-{next(_ids)}"
        stored = StoredFile(
            file_id=fid,
            name=filename,
            folder_id=folder_id,
            size_bytes=len(data),
            md5=hashlib.md5(data, usedforsecurity=False).hexdigest(),
            mime_type=mime_type,
        )
        self.files[fid] = (stored, data, document_id)
        return stored

    def download(self, file_id: str, max_bytes: int) -> bytes:
        if file_id not in self.files:
            raise ProviderError("not found", provider="fake", transient=False, status_code=404)
        data = self.files[file_id][1]
        if len(data) > max_bytes:
            raise ProviderError("too large", provider="fake", transient=False)
        return data

    def get_metadata(self, file_id: str) -> StoredFile:
        if file_id not in self.files:
            raise ProviderError("not found", provider="fake", transient=False, status_code=404)
        return self.files[file_id][0]

    def rename(self, file_id: str, new_name: str) -> StoredFile:
        if file_id not in self.files:
            raise ProviderError("not found", provider="fake", transient=False, status_code=404)
        stored, data, doc_id = self.files[file_id]
        renamed = StoredFile(
            file_id=stored.file_id,
            name=new_name,
            folder_id=stored.folder_id,
            size_bytes=stored.size_bytes,
            md5=stored.md5,
            mime_type=stored.mime_type,
        )
        self.files[file_id] = (renamed, data, doc_id)
        return renamed

    def trash(self, file_id: str) -> None:
        self.files.pop(file_id, None)

    def check(self) -> tuple[bool, str]:
        return True, "fake storage"

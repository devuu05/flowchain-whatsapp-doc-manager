"""Google Drive v3 REST adapter (scope: drive.file). Files are private by default."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from app.providers.errors import ProviderError, is_transient_status
from app.providers.storage import google_oauth
from app.providers.storage.base import DocumentStorage, StoredFile

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id,name,mimeType,size,md5Checksum,parents,appProperties"
PROVIDER = "google_drive"
APP_PROP = "flowchainDocumentId"


def _q_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class GoogleDriveStorage(DocumentStorage):
    name = PROVIDER

    def __init__(
        self,
        *,
        secrets: dict[str, Any],
        root_folder_id: str,
        on_tokens_refreshed: Callable[[dict[str, Any]], None] | None = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._secrets = dict(secrets)
        self.root_folder_id = root_folder_id
        self._on_refresh = on_tokens_refreshed
        self._transport = transport
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._folder_memo: dict[tuple[str, str], str] = {}

    # ---- auth -----------------------------------------------------------------------------
    def _access_token(self, force_refresh: bool = False) -> str:
        expires_raw = self._secrets.get("expires_at")
        expires = (
            datetime.fromisoformat(expires_raw) if expires_raw else datetime.min.replace(tzinfo=UTC)
        )
        if force_refresh or not self._secrets.get("access_token") or expires <= datetime.now(UTC):
            refresh_token = self._secrets.get("refresh_token")
            if not refresh_token:
                raise ProviderError(
                    "Drive not authorized (no refresh token); reconnect Google Drive",
                    provider=PROVIDER,
                    transient=False,
                )
            tokens = google_oauth.refresh(refresh_token, transport=self._transport)
            self._secrets.update(tokens.to_secrets())
            if self._on_refresh:
                self._on_refresh(dict(self._secrets))
        return str(self._secrets["access_token"])

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in (1, 2):
            headers = dict(kwargs.pop("headers", {}) or {})
            headers["Authorization"] = f"Bearer {self._access_token(force_refresh=attempt == 2)}"
            try:
                resp = self._client.request(method, url, headers=headers, **kwargs)
            except httpx.TimeoutException as exc:
                raise ProviderError("timeout", provider=PROVIDER, transient=True) from exc
            except httpx.TransportError as exc:
                raise ProviderError("connection error", provider=PROVIDER, transient=True) from exc
            if resp.status_code == 401 and attempt == 1:
                kwargs["headers"] = {k: v for k, v in headers.items() if k != "Authorization"}
                continue
            if resp.status_code >= 400:
                reason = ""
                try:
                    err = resp.json().get("error", {})
                    reason = (err.get("errors") or [{}])[0].get("reason", "") or err.get(
                        "status", ""
                    )
                except (ValueError, AttributeError):
                    pass
                transient = is_transient_status(resp.status_code) or reason in (
                    "rateLimitExceeded",
                    "userRateLimitExceeded",
                    "backendError",
                )
                raise ProviderError(
                    f"HTTP {resp.status_code} {reason}".strip(),
                    provider=PROVIDER,
                    transient=transient,
                    status_code=resp.status_code,
                )
            return resp
        raise ProviderError("unauthorized", provider=PROVIDER, transient=False, status_code=401)

    @staticmethod
    def _stored(meta: dict[str, Any]) -> StoredFile:
        parents = meta.get("parents") or []
        return StoredFile(
            file_id=meta["id"],
            name=meta.get("name", ""),
            folder_id=parents[0] if parents else None,
            size_bytes=int(meta["size"]) if meta.get("size") else None,
            md5=meta.get("md5Checksum"),
            mime_type=meta.get("mimeType"),
        )

    # ---- folders --------------------------------------------------------------------------
    def _find_or_create_folder(self, name: str, parent_id: str) -> str:
        key = (parent_id, name)
        if key in self._folder_memo:
            return self._folder_memo[key]
        q = (
            f"mimeType='{FOLDER_MIME}' and name='{_q_escape(name)}' "
            f"and '{_q_escape(parent_id)}' in parents and trashed=false"
        )
        resp = self._request(
            "GET", f"{API}/files", params={"q": q, "fields": "files(id,name)", "pageSize": 10}
        )
        files = resp.json().get("files", [])
        if files:
            folder_id = str(files[0]["id"])
        else:
            created = self._request(
                "POST",
                f"{API}/files",
                params={"fields": "id"},
                json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
            )
            folder_id = str(created.json()["id"])
        self._folder_memo[key] = folder_id
        return folder_id

    def ensure_folder_path(self, path: list[str]) -> str:
        parent = self.root_folder_id
        for name in path:
            parent = self._find_or_create_folder(name, parent)
        return parent

    @classmethod
    def create_root_folder(cls, access_token: str, name: str, transport: Any = None) -> str:
        with httpx.Client(timeout=30, transport=transport) as client:
            resp = client.post(
                f"{API}/files",
                params={"fields": "id"},
                headers={"Authorization": f"Bearer {access_token}"},
                json={"name": name, "mimeType": FOLDER_MIME},
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"could not create root folder (HTTP {resp.status_code})",
                provider=PROVIDER,
                transient=is_transient_status(resp.status_code),
                status_code=resp.status_code,
            )
        return str(resp.json()["id"])

    @classmethod
    def folder_accessible(cls, access_token: str, folder_id: str, transport: Any = None) -> bool:
        with httpx.Client(timeout=30, transport=transport) as client:
            resp = client.get(
                f"{API}/files/{folder_id}",
                params={"fields": "id,trashed,mimeType"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code != 200:
            return False
        body = resp.json()
        return not body.get("trashed") and body.get("mimeType") == FOLDER_MIME

    # ---- files ----------------------------------------------------------------------------
    def find_by_document_id(self, document_id: str) -> StoredFile | None:
        q = (
            f"appProperties has {{ key='{APP_PROP}' and value='{_q_escape(document_id)}' }} "
            "and trashed=false"
        )
        resp = self._request(
            "GET", f"{API}/files", params={"q": q, "fields": f"files({FILE_FIELDS})"}
        )
        files = resp.json().get("files", [])
        return self._stored(files[0]) if files else None

    def upload(
        self, *, data: bytes, filename: str, mime_type: str, folder_id: str, document_id: str
    ) -> StoredFile:
        metadata = {
            "name": filename,
            "parents": [folder_id],
            "mimeType": mime_type,
            "appProperties": {APP_PROP: document_id},
        }
        # Resumable session, single PUT (works for any size up to our file limit).
        init = self._request(
            "POST",
            f"{UPLOAD_API}/files",
            params={"uploadType": "resumable", "fields": FILE_FIELDS},
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": mime_type,
                "X-Upload-Content-Length": str(len(data)),
            },
            content=json.dumps(metadata).encode(),
        )
        location = init.headers.get("location")
        if not location or not location.startswith("https://"):
            raise ProviderError("missing resumable upload URL", provider=PROVIDER, transient=True)
        resp = self._request("PUT", location, content=data, headers={"Content-Type": mime_type})
        return self._stored(resp.json())

    def download(self, file_id: str, max_bytes: int) -> bytes:
        meta = self.get_metadata(file_id)
        if meta.size_bytes and meta.size_bytes > max_bytes:
            raise ProviderError(
                "file exceeds delivery size limit", provider=PROVIDER, transient=False
            )
        resp = self._request("GET", f"{API}/files/{file_id}", params={"alt": "media"})
        if len(resp.content) > max_bytes:
            raise ProviderError(
                "file exceeds delivery size limit", provider=PROVIDER, transient=False
            )
        return resp.content

    def get_metadata(self, file_id: str) -> StoredFile:
        resp = self._request("GET", f"{API}/files/{file_id}", params={"fields": FILE_FIELDS})
        return self._stored(resp.json())

    def trash(self, file_id: str) -> None:
        self._request("PATCH", f"{API}/files/{file_id}", json={"trashed": True})

    def check(self) -> tuple[bool, str]:
        try:
            self._request(
                "GET", f"{API}/files/{self.root_folder_id}", params={"fields": "id,trashed"}
            )
            return True, "ok"
        except ProviderError as exc:
            return False, str(exc)

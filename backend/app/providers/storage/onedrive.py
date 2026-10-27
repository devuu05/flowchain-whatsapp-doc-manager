"""OneDrive adapter (Microsoft Graph v1.0, delegated `Files.ReadWrite`).

Works for personal OneDrive and OneDrive for Business. Everything lives below one root
folder the integration creates ("Flow Chain Documents"); files stay private to the account.

Graph has no equivalent of Drive's appProperties on driveItems, so `find_by_document_id`
cannot look files up by document id. Idempotency is instead enforced inside `upload`: if the
target folder already holds a file with the same name, size and QuickXorHash (i.e. the same
bytes, from an earlier attempt of the same job), that file is returned instead of uploading
a second copy.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.providers.errors import ProviderError, is_transient_status
from app.providers.storage import microsoft_oauth
from app.providers.storage.base import DocumentStorage, StoredFile

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
DRIVE = f"{GRAPH}/me/drive"
PROVIDER = "onedrive"
SIMPLE_UPLOAD_MAX = 4 * 1024 * 1024
CHUNK = 320 * 1024 * 16  # upload-session chunks must be multiples of 320 KiB


def quick_xor_hash(data: bytes) -> str:
    """OneDrive QuickXorHash (base64), as reported in driveItem.file.hashes.quickXorHash.

    Byte i is XORed into a 160-bit register rotated left by (11*i) mod 160, then the data
    length (64-bit little endian) is XORed into the last 8 bytes.
    """
    width, shift = 160, 11
    # Bytes at the same index mod 160 land on the same rotation: fold them together first.
    full = len(data) - len(data) % width
    view = memoryview(data)
    acc = 0
    for start in range(0, full, width):
        acc ^= int.from_bytes(view[start : start + width], "little")
    folded = bytearray(acc.to_bytes(width, "little"))
    for j, b in enumerate(view[full:]):
        folded[j] ^= b
    mask = (1 << width) - 1
    reg = 0
    for j, b in enumerate(folded):
        if b:
            pos = (j * shift) % width
            reg ^= ((b << pos) | (b >> (width - pos))) & mask
    out = bytearray(reg.to_bytes(width // 8, "little"))
    for k, lb in enumerate(len(data).to_bytes(8, "little")):
        out[width // 8 - 8 + k] ^= lb
    return base64.b64encode(bytes(out)).decode()


def _path(name: str) -> str:
    return quote(name, safe="")


class OneDriveStorage(DocumentStorage):
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
                    "OneDrive not authorized (no refresh token); reconnect OneDrive",
                    provider=PROVIDER,
                    transient=False,
                )
            tokens = microsoft_oauth.refresh(refresh_token, transport=self._transport)
            self._secrets.update(tokens.to_secrets())
            # Microsoft rotates refresh tokens, so always persist the new set.
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
                raise self._error(resp)
            return resp
        raise ProviderError("unauthorized", provider=PROVIDER, transient=False, status_code=401)

    @staticmethod
    def _error(resp: httpx.Response) -> ProviderError:
        code = ""
        try:
            code = str(resp.json().get("error", {}).get("code", ""))
        except (ValueError, AttributeError):
            pass
        retry_after = resp.headers.get("retry-after")
        return ProviderError(
            f"HTTP {resp.status_code} {code}".strip(),
            provider=PROVIDER,
            transient=is_transient_status(resp.status_code) or code == "activityLimitReached",
            status_code=resp.status_code,
            retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None,
        )

    @staticmethod
    def _stored(item: dict[str, Any]) -> StoredFile:
        return StoredFile(
            file_id=str(item["id"]),
            name=item.get("name", ""),
            folder_id=(item.get("parentReference") or {}).get("id"),
            size_bytes=int(item["size"]) if item.get("size") is not None else None,
            md5=None,  # OneDrive reports QuickXorHash/SHA, not MD5
            mime_type=(item.get("file") or {}).get("mimeType"),
        )

    def _child(self, parent_id: str, name: str) -> dict[str, Any] | None:
        try:
            resp = self._request("GET", f"{DRIVE}/items/{parent_id}:/{_path(name)}")
        except ProviderError as exc:
            if exc.status_code == 404:
                return None
            raise
        item: dict[str, Any] = resp.json()
        return item

    # ---- folders --------------------------------------------------------------------------
    def _find_or_create_folder(self, name: str, parent_id: str) -> str:
        key = (parent_id, name)
        if key in self._folder_memo:
            return self._folder_memo[key]
        existing = self._child(parent_id, name)
        if existing is None:
            try:
                created = self._request(
                    "POST",
                    f"{DRIVE}/items/{parent_id}/children",
                    json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
                )
                existing = created.json()
            except ProviderError as exc:
                if exc.status_code != 409:
                    raise
                existing = self._child(parent_id, name)  # created concurrently
                if existing is None:
                    raise
        if "folder" not in existing:
            raise ProviderError(
                f"{name!r} exists but is not a folder", provider=PROVIDER, transient=False
            )
        folder_id = str(existing["id"])
        self._folder_memo[key] = folder_id
        return folder_id

    def ensure_folder_path(self, path: list[str]) -> str:
        parent = self.root_folder_id
        for name in path:
            parent = self._find_or_create_folder(name, parent)
        return parent

    @classmethod
    def create_root_folder(cls, access_token: str, name: str, transport: Any = None) -> str:
        """Find or create the integration's root folder at the top of the user's OneDrive."""
        headers = {"Authorization": f"Bearer {access_token}"}
        with httpx.Client(timeout=30, transport=transport) as client:
            resp = client.get(f"{DRIVE}/root:/{_path(name)}", headers=headers)
            if resp.status_code == 200 and "folder" in resp.json():
                return str(resp.json()["id"])
            resp = client.post(
                f"{DRIVE}/root/children",
                headers=headers,
                json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "rename"},
            )
        if resp.status_code >= 400:
            raise cls._error(resp)
        return str(resp.json()["id"])

    @classmethod
    def folder_accessible(cls, access_token: str, folder_id: str, transport: Any = None) -> bool:
        with httpx.Client(timeout=30, transport=transport) as client:
            resp = client.get(
                f"{DRIVE}/items/{folder_id}", headers={"Authorization": f"Bearer {access_token}"}
            )
        if resp.status_code != 200:
            return False
        body = resp.json()
        return "folder" in body and not body.get("deleted")

    @classmethod
    def account_label(cls, access_token: str, transport: Any = None) -> str | None:
        with httpx.Client(timeout=30, transport=transport) as client:
            resp = client.get(
                f"{GRAPH}/me?$select=displayName,userPrincipalName,mail",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code != 200:
            return None
        me = resp.json()
        return me.get("mail") or me.get("userPrincipalName") or me.get("displayName")

    # ---- files ----------------------------------------------------------------------------
    def find_by_document_id(self, document_id: str) -> StoredFile | None:
        # See module docstring: idempotency is handled in upload() by content hash.
        return None

    def upload(
        self, *, data: bytes, filename: str, mime_type: str, folder_id: str, document_id: str
    ) -> StoredFile:
        existing = self._child(folder_id, filename)
        same_size = existing is not None and int(existing.get("size", -1)) == len(data)
        if existing is not None and "file" in existing and same_size:
            remote = ((existing.get("file") or {}).get("hashes") or {}).get("quickXorHash")
            if remote and remote == quick_xor_hash(data):
                log.info("onedrive_upload_reconciled", extra={"document_id": document_id})
                return self._stored(existing)

        target = f"{DRIVE}/items/{folder_id}:/{_path(filename)}:"
        if len(data) <= SIMPLE_UPLOAD_MAX:
            resp = self._request(
                "PUT",
                f"{target}/content",
                params={"@microsoft.graph.conflictBehavior": "rename"},
                content=data,
                headers={"Content-Type": mime_type},
            )
            return self._stored(resp.json())

        session = self._request(
            "POST",
            f"{target}/createUploadSession",
            json={"item": {"@microsoft.graph.conflictBehavior": "rename"}},
        ).json()
        upload_url = session.get("uploadUrl", "")
        if not upload_url.startswith("https://"):
            raise ProviderError("missing upload session URL", provider=PROVIDER, transient=True)
        item: dict[str, Any] | None = None
        for start in range(0, len(data), CHUNK):
            end = min(start + CHUNK, len(data))
            # The upload URL is pre-authenticated; Graph rejects an Authorization header here.
            try:
                resp = self._client.put(
                    upload_url,
                    content=data[start:end],
                    headers={"Content-Range": f"bytes {start}-{end - 1}/{len(data)}"},
                )
            except httpx.HTTPError as exc:
                raise ProviderError(
                    "upload chunk failed", provider=PROVIDER, transient=True
                ) from exc
            if resp.status_code >= 400:
                raise self._error(resp)
            if resp.status_code in (200, 201):
                item = resp.json()
        if item is None:
            raise ProviderError(
                "upload session did not complete", provider=PROVIDER, transient=True
            )
        return self._stored(item)

    def download(self, file_id: str, max_bytes: int) -> bytes:
        item = self._request("GET", f"{DRIVE}/items/{file_id}").json()
        if int(item.get("size") or 0) > max_bytes:
            raise ProviderError(
                "file exceeds delivery size limit", provider=PROVIDER, transient=False
            )
        url = item.get("@microsoft.graph.downloadUrl")
        if not url:
            raise ProviderError("no download URL for item", provider=PROVIDER, transient=False)
        # Short-lived pre-authenticated URL: fetched without our bearer token.
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as exc:
            raise ProviderError("download failed", provider=PROVIDER, transient=True) from exc
        if resp.status_code >= 400:
            raise self._error(resp)
        if len(resp.content) > max_bytes:
            raise ProviderError(
                "file exceeds delivery size limit", provider=PROVIDER, transient=False
            )
        return resp.content

    def get_metadata(self, file_id: str) -> StoredFile:
        return self._stored(self._request("GET", f"{DRIVE}/items/{file_id}").json())

    def rename(self, file_id: str, new_name: str) -> StoredFile:
        try:
            resp = self._request(
                "PATCH",
                f"{DRIVE}/items/{file_id}",
                json={"name": new_name, "@microsoft.graph.conflictBehavior": "fail"},
            )
        except ProviderError as exc:
            if exc.status_code == 409:
                raise ProviderError(
                    "a file with that name already exists in the folder",
                    provider=PROVIDER,
                    transient=False,
                    status_code=409,
                ) from exc
            raise
        return self._stored(resp.json())

    def trash(self, file_id: str) -> None:
        # DELETE moves the item to the OneDrive recycle bin (restorable).
        try:
            self._request("DELETE", f"{DRIVE}/items/{file_id}")
        except ProviderError as exc:
            if exc.status_code != 404:  # already gone
                raise

    def check(self) -> tuple[bool, str]:
        try:
            self._request("GET", f"{DRIVE}/items/{self.root_folder_id}")
            return True, "ok"
        except ProviderError as exc:
            return False, str(exc)

"""Google Drive adapter and OAuth — MOCKED HTTP (respx). Live test: docs/testing/manual-provider-tests.md."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IntegrationConfig, IntegrationKind
from app.providers.errors import ProviderError
from app.providers.storage.google_drive import API, UPLOAD_API, GoogleDriveStorage
from app.providers.storage.google_oauth import TOKEN_URL
from app.security.crypto import decrypt_json
from tests.conftest import World, login, make_world

FUTURE = (datetime.now(UTC) + timedelta(hours=1)).isoformat()


def _drive(**secrets: object) -> tuple[GoogleDriveStorage, list[dict[str, object]]]:
    refreshed: list[dict[str, object]] = []
    base = {
        "access_token": "ya29.valid",
        "expires_at": FUTURE,
        "refresh_token": "1//refresh-token-value",
    }
    base.update(secrets)
    return GoogleDriveStorage(
        secrets=base, root_folder_id="ROOT", on_tokens_refreshed=refreshed.append
    ), refreshed


@respx.mock
def test_refreshes_expired_token_and_persists() -> None:
    drive, refreshed = _drive(expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    token = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "ya29.new", "expires_in": 3600})
    )
    respx.get(f"{API}/files/ROOT").mock(return_value=httpx.Response(200, json={"id": "ROOT"}))
    assert drive.check() == (True, "ok")
    assert token.called
    assert refreshed and refreshed[0]["access_token"] == "ya29.new"
    assert refreshed[0]["refresh_token"] == "1//refresh-token-value"


@respx.mock
def test_401_triggers_single_refresh_retry() -> None:
    drive, _ = _drive()
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "ya29.new", "expires_in": 3600})
    )
    route = respx.get(f"{API}/files/F1").mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json={"id": "F1", "name": "a.pdf", "size": "3"}),
        ]
    )
    meta = drive.get_metadata("F1")
    assert meta.file_id == "F1" and route.call_count == 2
    assert route.calls[1].request.headers["authorization"] == "Bearer ya29.new"


@respx.mock
def test_revoked_refresh_token_is_permanent() -> None:
    drive, _ = _drive(expires_at=None)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(ProviderError) as exc:
        drive.get_metadata("F1")
    assert not exc.value.transient and "invalid_grant" in str(exc.value)


@respx.mock
def test_folder_path_find_or_create_and_upload_with_app_properties() -> None:
    drive, _ = _drive()
    lists = respx.get(f"{API}/files").mock(
        side_effect=[
            httpx.Response(200, json={"files": [{"id": "INV"}]}),  # Invoices exists
            httpx.Response(200, json={"files": []}),  # 2026 missing
        ]
    )
    create = respx.post(f"{API}/files").mock(return_value=httpx.Response(200, json={"id": "Y2026"}))
    folder = drive.ensure_folder_path(["Invoices", "2026"])
    assert folder == "Y2026"
    q0 = parse_qs(urlparse(str(lists.calls[0].request.url)).query)["q"][0]
    assert "name='Invoices'" in q0 and "'ROOT' in parents" in q0 and "trashed=false" in q0
    assert json.loads(create.calls[0].request.read())["parents"] == ["INV"]

    init = respx.post(f"{UPLOAD_API}/files").mock(
        return_value=httpx.Response(
            200,
            headers={"location": "https://www.googleapis.com/upload/drive/v3/files?upload_id=xyz"},
        )
    )
    put = respx.put("https://www.googleapis.com/upload/drive/v3/files?upload_id=xyz").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "FILE1",
                "name": "Invoice_ABC.pdf",
                "size": "4",
                "md5Checksum": "abc",
                "parents": ["Y2026"],
                "mimeType": "application/pdf",
            },
        )
    )
    stored = drive.upload(
        data=b"%PDF",
        filename="Invoice_ABC.pdf",
        mime_type="application/pdf",
        folder_id="Y2026",
        document_id="doc-1",
    )
    assert stored.file_id == "FILE1" and stored.folder_id == "Y2026" and stored.size_bytes == 4
    meta = json.loads(init.calls[0].request.read())
    assert meta["appProperties"] == {"flowchainDocumentId": "doc-1"}
    assert meta["parents"] == ["Y2026"]
    assert "permissions" not in meta  # private by default
    assert put.calls[0].request.read() == b"%PDF"


@respx.mock
def test_find_by_document_id_query_escapes() -> None:
    drive, _ = _drive()
    route = respx.get(f"{API}/files").mock(return_value=httpx.Response(200, json={"files": []}))
    assert drive.find_by_document_id("it's") is None
    q = parse_qs(urlparse(str(route.calls[0].request.url)).query)["q"][0]
    assert "value='it\\'s'" in q


@respx.mock
@pytest.mark.parametrize(
    ("status", "reason", "transient"),
    [
        (429, "rateLimitExceeded", True),
        (403, "userRateLimitExceeded", True),
        (403, "insufficientFilePermissions", False),
        (404, "notFound", False),
        (500, "backendError", True),
    ],
)
def test_drive_error_classification(status: int, reason: str, transient: bool) -> None:
    drive, _ = _drive()
    respx.get(f"{API}/files/F").mock(
        return_value=httpx.Response(status, json={"error": {"errors": [{"reason": reason}]}})
    )
    with pytest.raises(ProviderError) as exc:
        drive.get_metadata("F")
    assert exc.value.transient is transient


@respx.mock
def test_download_respects_size_limit() -> None:
    drive, _ = _drive()
    respx.get(f"{API}/files/F").mock(
        return_value=httpx.Response(200, json={"id": "F", "size": "999999"})
    )
    with pytest.raises(ProviderError, match="size limit"):
        drive.download("F", max_bytes=1000)


@respx.mock
def test_oauth_start_and_callback_flow(client: TestClient, db: Session) -> None:
    w: World = make_world(db, "OAuth Co")
    csrf = login(client, w.user.email)
    r = client.post(
        f"/api/v1/tenants/{w.tenant.id}/integrations/google/start", headers={"x-csrf-token": csrf}
    )
    assert r.status_code == 200
    url = urlparse(r.json()["authorization_url"])
    params = parse_qs(url.query)
    assert params["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    assert params["access_type"] == ["offline"]
    state = params["state"][0]

    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.fresh",
                "expires_in": 3600,
                "refresh_token": "1//fresh-refresh-token-abcdefgh",
                "scope": "https://www.googleapis.com/auth/drive.file",
            },
        )
    )
    respx.post(f"{API}/files").mock(return_value=httpx.Response(200, json={"id": "NEWROOT"}))
    # The fake storage config from make_world would satisfy drive_config(); remove it first.
    db.delete(db.get(IntegrationConfig, w.storage_cfg.id))
    db.commit()
    cb = client.get(
        "/api/v1/integrations/google/callback",
        params={"state": state, "code": "auth-code"},
        follow_redirects=False,
    )
    assert cb.status_code == 303 and cb.headers["location"].endswith("result=connected")
    cfg = db.scalar(
        select(IntegrationConfig).where(
            IntegrationConfig.tenant_id == w.tenant.id,
            IntegrationConfig.kind == IntegrationKind.google_drive,
        )
    )
    assert cfg is not None and cfg.settings["root_folder_id"] == "NEWROOT"
    assert decrypt_json(cfg.encrypted_secrets)["refresh_token"] == "1//fresh-refresh-token-abcdefgh"
    assert "1//fresh" not in (cfg.encrypted_secrets or "")
    listing = client.get(f"/api/v1/tenants/{w.tenant.id}/integrations").text
    assert "ya29" not in listing and "1//fresh" not in listing


def test_oauth_callback_rejects_tampered_state(client: TestClient) -> None:
    cb = client.get(
        "/api/v1/integrations/google/callback",
        params={"state": "abc.def", "code": "x"},
        follow_redirects=False,
    )
    assert cb.headers["location"].endswith("drive=invalid_state")

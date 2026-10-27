"""OneDrive adapter and Microsoft OAuth — MOCKED HTTP (respx).

These tests do not prove the live Graph API works; see docs/testing/manual-provider-tests.md.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IntegrationConfig, IntegrationKind, IntegrationStatus
from app.providers import registry
from app.providers.errors import ProviderError
from app.providers.storage.onedrive import CHUNK, DRIVE, GRAPH, OneDriveStorage, quick_xor_hash
from app.security.crypto import decrypt_json, encrypt_json
from tests.conftest import World, login, make_world

FUTURE = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"


def _od(**secrets: object) -> tuple[OneDriveStorage, list[dict[str, object]]]:
    refreshed: list[dict[str, object]] = []
    base = {"access_token": "eyJ.valid", "expires_at": FUTURE, "refresh_token": "M.R3_old"}
    base.update(secrets)
    return OneDriveStorage(
        secrets=base, root_folder_id="ROOT", on_tokens_refreshed=refreshed.append
    ), refreshed


def test_quick_xor_hash_known_values() -> None:
    # Empty input hashes to 20 zero bytes; one byte lands unrotated plus the length.
    assert quick_xor_hash(b"") == base64.b64encode(bytes(20)).decode()
    assert quick_xor_hash(b"\x83") == "gwAAAAAAAAAAAAAAAQAAAAAAAAA="


@respx.mock
def test_refresh_persists_rotated_refresh_token() -> None:
    od, refreshed = _od(expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    token = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "eyJ.new", "expires_in": 3600, "refresh_token": "M.R3_new"}
        )
    )
    respx.get(f"{DRIVE}/items/ROOT").mock(
        return_value=httpx.Response(200, json={"id": "ROOT", "folder": {}})
    )
    assert od.check() == (True, "ok")
    body = parse_qs(token.calls.last.request.content.decode())
    assert body["grant_type"] == ["refresh_token"] and body["refresh_token"] == ["M.R3_old"]
    assert refreshed[-1]["refresh_token"] == "M.R3_new"


@respx.mock
def test_ensure_folder_path_finds_or_creates() -> None:
    od, _ = _od()
    respx.get(f"{DRIVE}/items/ROOT:/Invoices").mock(
        return_value=httpx.Response(200, json={"id": "INV", "folder": {}})
    )
    respx.get(f"{DRIVE}/items/INV:/2026").mock(return_value=httpx.Response(404))
    create = respx.post(f"{DRIVE}/items/INV/children").mock(
        return_value=httpx.Response(201, json={"id": "Y2026", "folder": {}})
    )
    assert od.ensure_folder_path(["Invoices", "2026"]) == "Y2026"
    assert create.calls.last.request.content and b'"fail"' in create.calls.last.request.content
    # memoized: no further calls
    assert od.ensure_folder_path(["Invoices", "2026"]) == "Y2026"
    assert create.call_count == 1


@respx.mock
def test_ensure_folder_handles_concurrent_create() -> None:
    od, _ = _od()
    respx.get(f"{DRIVE}/items/ROOT:/Bills").mock(
        side_effect=[httpx.Response(404), httpx.Response(200, json={"id": "B", "folder": {}})]
    )
    respx.post(f"{DRIVE}/items/ROOT/children").mock(return_value=httpx.Response(409))
    assert od.ensure_folder_path(["Bills"]) == "B"


@respx.mock
def test_small_upload_uses_rename_on_conflict() -> None:
    od, _ = _od()
    respx.get(f"{DRIVE}/items/F:/a.pdf").mock(return_value=httpx.Response(404))
    put = respx.put(f"{DRIVE}/items/F:/a.pdf:/content").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "A1",
                "name": "a.pdf",
                "size": 3,
                "parentReference": {"id": "F"},
                "file": {},
            },
        )
    )
    stored = od.upload(
        data=b"pdf", filename="a.pdf", mime_type="application/pdf", folder_id="F", document_id="d"
    )
    assert stored.file_id == "A1" and stored.folder_id == "F" and stored.size_bytes == 3
    assert put.calls.last.request.url.params["@microsoft.graph.conflictBehavior"] == "rename"


@respx.mock
def test_upload_is_idempotent_for_identical_bytes() -> None:
    od, _ = _od()
    data = b"same bytes as the first attempt"
    respx.get(f"{DRIVE}/items/F:/a.pdf").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "A1",
                "name": "a.pdf",
                "size": len(data),
                "file": {"hashes": {"quickXorHash": quick_xor_hash(data)}},
            },
        )
    )
    put = respx.put(f"{DRIVE}/items/F:/a.pdf:/content")
    stored = od.upload(
        data=data, filename="a.pdf", mime_type="application/pdf", folder_id="F", document_id="d"
    )
    assert stored.file_id == "A1" and not put.called


@respx.mock
def test_same_name_different_bytes_uploads_new_copy() -> None:
    od, _ = _od()
    respx.get(f"{DRIVE}/items/F:/a.pdf").mock(
        return_value=httpx.Response(
            200, json={"id": "OLD", "size": 4, "file": {"hashes": {"quickXorHash": "x"}}}
        )
    )
    respx.put(f"{DRIVE}/items/F:/a.pdf:/content").mock(
        return_value=httpx.Response(
            201, json={"id": "NEW", "name": "a 1.pdf", "size": 4, "file": {}}
        )
    )
    stored = od.upload(
        data=b"abcd", filename="a.pdf", mime_type="application/pdf", folder_id="F", document_id="d"
    )
    assert stored.file_id == "NEW" and stored.name == "a 1.pdf"


@respx.mock
def test_large_upload_uses_session_without_auth_header() -> None:
    od, _ = _od()
    data = b"x" * (CHUNK + 1000) + b"y" * (4 * 1024 * 1024)
    respx.get(f"{DRIVE}/items/F:/big.pdf").mock(return_value=httpx.Response(404))
    respx.post(f"{DRIVE}/items/F:/big.pdf:/createUploadSession").mock(
        return_value=httpx.Response(200, json={"uploadUrl": "https://up.example/sess"})
    )
    chunks = respx.put("https://up.example/sess").mock(
        side_effect=[
            httpx.Response(202, json={"nextExpectedRanges": [f"{CHUNK}-"]}),
            httpx.Response(
                201, json={"id": "BIG", "name": "big.pdf", "size": len(data), "file": {}}
            ),
        ]
    )
    stored = od.upload(
        data=data, filename="big.pdf", mime_type="application/pdf", folder_id="F", document_id="d"
    )
    assert stored.file_id == "BIG" and chunks.call_count == 2
    first = chunks.calls[0].request
    assert "authorization" not in first.headers
    assert first.headers["content-range"] == f"bytes 0-{CHUNK - 1}/{len(data)}"


@respx.mock
def test_download_uses_preauthenticated_url_and_enforces_limit() -> None:
    od, _ = _od()
    respx.get(f"{DRIVE}/items/A1").mock(
        return_value=httpx.Response(
            200,
            json={"id": "A1", "size": 5, "@microsoft.graph.downloadUrl": "https://dl.example/a"},
        )
    )
    dl = respx.get("https://dl.example/a").mock(return_value=httpx.Response(200, content=b"hello"))
    assert od.download("A1", max_bytes=100) == b"hello"
    assert "authorization" not in dl.calls.last.request.headers
    with pytest.raises(ProviderError, match="size limit"):
        od.download("A1", max_bytes=2)


@respx.mock
def test_rename_conflict_and_trash() -> None:
    od, _ = _od()
    respx.patch(f"{DRIVE}/items/A1").mock(
        side_effect=[
            httpx.Response(200, json={"id": "A1", "name": "New.pdf", "file": {}}),
            httpx.Response(409, json={"error": {"code": "nameAlreadyExists"}}),
        ]
    )
    assert od.rename("A1", "New.pdf").name == "New.pdf"
    with pytest.raises(ProviderError) as exc:
        od.rename("A1", "Taken.pdf")
    assert exc.value.status_code == 409 and not exc.value.transient
    delete = respx.delete(f"{DRIVE}/items/A1").mock(return_value=httpx.Response(204))
    od.trash("A1")
    assert delete.called
    respx.delete(f"{DRIVE}/items/GONE").mock(return_value=httpx.Response(404))
    od.trash("GONE")  # already deleted: not an error


@respx.mock
def test_throttling_is_transient_with_retry_after() -> None:
    od, _ = _od()
    respx.get(f"{DRIVE}/items/ROOT").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "7"})
    )
    with pytest.raises(ProviderError) as exc:
        od.get_metadata("ROOT")
    assert exc.value.transient and exc.value.retry_after == 7


def _start(client: TestClient, w: World) -> str:
    csrf = login(client, w.user.email)
    r = client.post(
        f"/api/v1/tenants/{w.tenant.id}/integrations/microsoft/start",
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 200, r.text
    url = urlparse(r.json()["authorization_url"])
    assert url.netloc == "login.microsoftonline.com" and url.path == "/common/oauth2/v2.0/authorize"
    params = parse_qs(url.query)
    assert "offline_access" in params["scope"][0] and "Files.ReadWrite" in params["scope"][0]
    assert params["redirect_uri"] == [
        "http://localhost:3000/api/v1/integrations/microsoft/callback"
    ]
    return params["state"][0]


@respx.mock
def test_microsoft_oauth_connects_onedrive(client: TestClient, db: Session) -> None:
    w = make_world(db, "OneDrive Co")
    db.delete(db.get(IntegrationConfig, w.storage_cfg.id))
    db.commit()
    state = _start(client, w)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "eyJ.fresh",
                "expires_in": 3600,
                "refresh_token": "M.R3_fresh_refresh_token",
                "scope": "Files.ReadWrite User.Read",
            },
        )
    )
    respx.get(f"{DRIVE}/root:/Flow%20Chain%20Documents").mock(return_value=httpx.Response(404))
    respx.post(f"{DRIVE}/root/children").mock(
        return_value=httpx.Response(201, json={"id": "ODROOT", "folder": {}})
    )
    respx.get(url__startswith=f"{GRAPH}/me").mock(
        return_value=httpx.Response(200, json={"mail": "owner@outlook.com"})
    )
    cb = client.get(
        "/api/v1/integrations/microsoft/callback",
        params={"state": state, "code": "ms-code"},
        follow_redirects=False,
    )
    assert cb.status_code == 303
    assert cb.headers["location"].endswith("result=connected&provider=onedrive")
    cfg = db.scalar(
        select(IntegrationConfig).where(
            IntegrationConfig.tenant_id == w.tenant.id,
            IntegrationConfig.kind == IntegrationKind.onedrive,
        )
    )
    assert cfg is not None and cfg.status == IntegrationStatus.connected
    assert (
        cfg.settings["root_folder_id"] == "ODROOT"
        and cfg.settings["account"] == "owner@outlook.com"
    )
    assert decrypt_json(cfg.encrypted_secrets)["refresh_token"] == "M.R3_fresh_refresh_token"
    listing = client.get(f"/api/v1/tenants/{w.tenant.id}/integrations").text
    assert "M.R3" not in listing and "eyJ" not in listing
    storage, _ = registry.storage_for(db, w.tenant.id)
    assert isinstance(storage, OneDriveStorage)


def test_microsoft_start_refused_while_other_storage_connected(
    client: TestClient, db: Session
) -> None:
    w = make_world(db, "Has Drive Co")  # make_world connects (fake) storage
    csrf = login(client, w.user.email)
    r = client.post(
        f"/api/v1/tenants/{w.tenant.id}/integrations/microsoft/start",
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 409


def test_microsoft_callback_rejects_bad_state_and_other_session(
    client: TestClient, db: Session
) -> None:
    cb = client.get(
        "/api/v1/integrations/microsoft/callback",
        params={"state": "abc.def", "code": "x"},
        follow_redirects=False,
    )
    assert cb.headers["location"].endswith("drive=invalid_state")

    w = make_world(db, "Session Co")
    db.delete(db.get(IntegrationConfig, w.storage_cfg.id))
    db.commit()
    state = _start(client, w)
    client.cookies.clear()  # a different browser finishing the flow
    cb = client.get(
        "/api/v1/integrations/microsoft/callback",
        params={"state": state, "code": "x"},
        follow_redirects=False,
    )
    assert "result=session_mismatch" in cb.headers["location"]


def test_google_callback_does_not_replace_onedrive(client: TestClient, db: Session) -> None:
    w = make_world(db, "Mixed Co")
    cfg = db.get(IntegrationConfig, w.storage_cfg.id)
    assert cfg is not None
    cfg.kind, cfg.provider = IntegrationKind.onedrive, "onedrive"
    cfg.settings = {"root_folder_id": "R"}
    cfg.encrypted_secrets = encrypt_json({"refresh_token": "M.R3"})
    db.commit()
    csrf = login(client, w.user.email)
    r = client.post(
        f"/api/v1/tenants/{w.tenant.id}/integrations/google/start", headers={"x-csrf-token": csrf}
    )
    state = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]
    cb = client.get(
        "/api/v1/integrations/google/callback",
        params={"state": state, "code": "x"},
        follow_redirects=False,
    )
    assert cb.headers["location"].endswith("result=other_storage")

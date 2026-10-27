"""Microsoft identity platform OAuth 2.0 (authorization code flow, offline access).

Endpoints and parameters: https://learn.microsoft.com/entra/identity-platform/v2-oauth2-auth-code-flow
Refresh tokens rotate: every refresh may return a new one, which callers must persist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.core.config import get_settings
from app.providers.errors import ProviderError, is_transient_status
from app.providers.storage.google_oauth import TokenSet

LOGIN_BASE = "https://login.microsoftonline.com"
PROVIDER = "microsoft_oauth"


def _base() -> str:
    return f"{LOGIN_BASE}/{quote(get_settings().microsoft_tenant, safe='')}/oauth2/v2.0"


def build_authorization_url(state: str) -> str:
    s = get_settings()
    params = {
        "client_id": s.microsoft_client_id,
        "redirect_uri": s.microsoft_redirect_uri,
        "response_type": "code",
        "response_mode": "query",
        "scope": s.microsoft_oauth_scopes,
        "prompt": "select_account",
        "state": state,
    }
    return f"{_base()}/authorize?{urlencode(params)}"


def _token_request(data: dict[str, str], transport: httpx.BaseTransport | None) -> dict[str, Any]:
    s = get_settings()
    data = {
        **data,
        "client_id": s.microsoft_client_id,
        "client_secret": s.microsoft_client_secret.get_secret_value(),
        "scope": s.microsoft_oauth_scopes,
    }
    try:
        with httpx.Client(timeout=s.microsoft_timeout_seconds, transport=transport) as client:
            resp = client.post(f"{_base()}/token", data=data)
    except httpx.HTTPError as exc:
        raise ProviderError(
            "token endpoint unreachable", provider=PROVIDER, transient=True
        ) from exc
    if resp.status_code >= 400:
        error = ""
        try:
            error = str(resp.json().get("error", ""))
        except ValueError:
            pass
        # invalid_grant = refresh token expired/revoked -> needs reconnect (permanent).
        raise ProviderError(
            f"token request failed: {error or resp.status_code}",
            provider=PROVIDER,
            transient=is_transient_status(resp.status_code),
            status_code=resp.status_code,
        )
    body: dict[str, Any] = resp.json()
    return body


def _tokenset(body: dict[str, Any], refresh_token: str | None) -> TokenSet:
    return TokenSet(
        access_token=body["access_token"],
        expires_at=datetime.now(UTC) + timedelta(seconds=int(body.get("expires_in", 3600)) - 60),
        refresh_token=body.get("refresh_token") or refresh_token,
        scope=body.get("scope"),
    )


def exchange_code(code: str, transport: httpx.BaseTransport | None = None) -> TokenSet:
    body = _token_request(
        {
            "code": code,
            "redirect_uri": get_settings().microsoft_redirect_uri,
            "grant_type": "authorization_code",
        },
        transport,
    )
    return _tokenset(body, None)


def refresh(refresh_token: str, transport: httpx.BaseTransport | None = None) -> TokenSet:
    body = _token_request(
        {"refresh_token": refresh_token, "grant_type": "refresh_token"}, transport
    )
    return _tokenset(body, refresh_token)

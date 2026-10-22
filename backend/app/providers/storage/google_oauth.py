"""Google OAuth 2.0 (authorization code flow, offline access)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.providers.errors import ProviderError, is_transient_status

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (URL, not a secret)
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
PROVIDER = "google_oauth"


@dataclass
class TokenSet:
    access_token: str
    expires_at: datetime
    refresh_token: str | None
    scope: str | None

    def to_secrets(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "expires_at": self.expires_at.isoformat(),
            "refresh_token": self.refresh_token,
            "scope": self.scope,
        }


def build_authorization_url(state: str) -> str:
    s = get_settings()
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": s.google_redirect_uri,
        "response_type": "code",
        "scope": s.google_oauth_scopes,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _token_request(data: dict[str, str], transport: httpx.BaseTransport | None) -> dict[str, Any]:
    s = get_settings()
    try:
        with httpx.Client(timeout=s.google_timeout_seconds, transport=transport) as client:
            resp = client.post(TOKEN_URL, data=data)
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
        # invalid_grant = refresh token revoked/expired -> needs reconnect (permanent).
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
    s = get_settings()
    body = _token_request(
        {
            "code": code,
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret.get_secret_value(),
            "redirect_uri": s.google_redirect_uri,
            "grant_type": "authorization_code",
        },
        transport,
    )
    return _tokenset(body, None)


def refresh(refresh_token: str, transport: httpx.BaseTransport | None = None) -> TokenSet:
    s = get_settings()
    body = _token_request(
        {
            "refresh_token": refresh_token,
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret.get_secret_value(),
            "grant_type": "refresh_token",
        },
        transport,
    )
    return _tokenset(body, refresh_token)


def revoke(token: str, transport: httpx.BaseTransport | None = None) -> None:
    try:
        with httpx.Client(timeout=15, transport=transport) as client:
            client.post(REVOKE_URL, data={"token": token})
    except httpx.HTTPError:
        pass  # best effort; the integration is removed locally regardless

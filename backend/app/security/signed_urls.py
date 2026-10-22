"""Short-lived HMAC-signed tokens (media URLs for provider fetches, OAuth state)."""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

from app.security.crypto import constant_time_equals, hmac_hex, new_token


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign(purpose: str, data: dict[str, Any], ttl_seconds: int) -> str:
    body = {**data, "exp": int(time.time()) + ttl_seconds, "n": new_token(6)}
    payload = _b64(json.dumps(body, separators=(",", ":")).encode())
    return f"{payload}.{hmac_hex(purpose, payload)}"


def verify(purpose: str, token: str) -> dict[str, Any] | None:
    try:
        payload, sig = token.split(".", 1)
        if not constant_time_equals(sig, hmac_hex(purpose, payload)):
            return None
        data = json.loads(_unb64(payload))
        if not isinstance(data, dict) or int(data["exp"]) < int(time.time()):
            return None
        return data
    except (ValueError, KeyError, json.JSONDecodeError, TypeError):
        return None


def make_media_token(tenant_id: uuid.UUID, blob_id: uuid.UUID, ttl_seconds: int) -> str:
    return sign("media", {"t": str(tenant_id), "b": str(blob_id)}, ttl_seconds)


def verify_media_token(token: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    data = verify("media", token)
    if data is None:
        return None
    try:
        return uuid.UUID(data["t"]), uuid.UUID(data["b"])
    except (KeyError, ValueError):
        return None

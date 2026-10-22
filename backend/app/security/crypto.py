"""Field encryption (Fernet/MultiFernet) and HMAC helpers.

Key management: FIELD_ENCRYPTION_KEYS holds one or more urlsafe-base64 32-byte Fernet keys,
comma separated. The first key encrypts; all keys decrypt, enabling rotation:
prepend a new key, run `python -m app.cli rotate-keys`, then remove the old key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings


class EncryptionUnavailable(RuntimeError):
    pass


@lru_cache
def _fernet() -> MultiFernet:
    s = get_settings()
    raw = s.field_encryption_keys.get_secret_value()
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        if s.app_env == "production":
            raise EncryptionUnavailable("FIELD_ENCRYPTION_KEYS not configured")
        # Development/test only: derive a deterministic key from SECRET_KEY.
        digest = hashlib.sha256(b"fc-dev-field-key:" + s.secret_key.get_secret_value().encode())
        keys = [base64.urlsafe_b64encode(digest.digest()).decode()]
    return MultiFernet([Fernet(k) for k in keys])


def encrypt_json(data: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        value = json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise EncryptionUnavailable("Could not decrypt integration secrets") from exc
    return value if isinstance(value, dict) else {}


def rotate(token: str) -> str:
    return _fernet().rotate(token.encode()).decode()


def sha256_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def hmac_hex(purpose: str, value: str) -> str:
    key = get_settings().secret_key.get_secret_value().encode()
    return hmac.new(key, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode(), b.encode())


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def generate_fernet_key() -> str:
    return Fernet.generate_key().decode()

"""Redaction of secrets and personal data from logs and error payloads."""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "secret",
    "client_secret",
    "authorization",
    "cookie",
    "set-cookie",
    "x-webhook-signature",
    "webhook_secret",
    "session",
    "csrf_token",
    "x-csrf-token",
    "x-goog-api-key",
    "base64",
    "data_b64",
}

_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-~+/=]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(key=)[A-Za-z0-9_\-]{8,}"), r"\1[REDACTED]"),
    (re.compile(r"ya29\.[A-Za-z0-9._\-]+"), "[REDACTED_GOOGLE_TOKEN]"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"1//[0-9A-Za-z_\-]{20,}"), "[REDACTED_REFRESH_TOKEN]"),
    (re.compile(r"gAAAAA[A-Za-z0-9_\-=]{20,}"), "[REDACTED_CIPHERTEXT]"),
]

REDACTED = "[REDACTED]"


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    return k in SENSITIVE_KEYS or k.endswith(("_token", "_secret", "_password", "_key"))


def redact_text(value: str) -> str:
    for pattern, repl in _PATTERNS:
        value = pattern.sub(repl, value)
    return value


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively redact sensitive keys and token-like strings."""
    if _depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and _is_sensitive(k) else redact(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def mask_phone(phone: str | None) -> str:
    """Mask a phone number for logs: +919876543210 -> +91******3210."""
    if not phone:
        return ""
    digits = phone.strip()
    if len(digits) <= 6:
        return "***"
    return digits[:3] + "*" * (len(digits) - 7) + digits[-4:]

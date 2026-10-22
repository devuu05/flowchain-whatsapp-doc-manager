"""Filename sanitation and standardized naming."""

from __future__ import annotations

import re
import unicodedata

_UNSAFE = re.compile(r"[^A-Za-z0-9 ._()&\-]+")
_SPACES = re.compile(r"[\s_]+")
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}  # fmt: skip


def sanitize_component(value: str | None, max_len: int = 60) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = value.replace("/", "-").replace("\\", "-")
    value = _UNSAFE.sub("", value)
    value = _SPACES.sub(" ", value).strip(" .-")
    value = value.replace("..", ".")
    if value.upper() in _RESERVED:
        value = f"_{value}"
    return value[:max_len].strip(" .-")


def split_extension(filename: str | None) -> tuple[str, str]:
    name = (filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if "." in name[1:]:
        stem, ext = name.rsplit(".", 1)
        ext = re.sub(r"[^A-Za-z0-9]", "", ext).lower()[:10]
        return stem, ext
    return name, ""


def sanitize_filename(filename: str | None, extension: str, fallback: str = "document") -> str:
    stem, _ = split_extension(filename)
    stem = sanitize_component(stem, 120) or fallback
    return f"{stem}.{extension}" if extension else stem


def build_standard_filename(
    *, document_type: str, company: str | None, period: str | None, extension: str, short_id: str
) -> str:
    parts = [sanitize_component(document_type, 30) or "Document"]
    if company and company.lower() != "unknown":
        parts.append(sanitize_component(company, 50))
    if period and period.lower() != "unknown":
        parts.append(sanitize_component(period, 20))
    parts.append(short_id[:6])
    stem = "_".join(re.sub(r"-{2,}", "-", p.replace(" ", "-")) for p in parts if p)
    return f"{stem}.{extension}" if extension else stem

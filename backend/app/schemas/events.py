"""Provider-independent normalized WhatsApp event schema (internal contract).

Every provider adapter converts its webhook payloads into `NormalizedEvent`. The rest of the
system only understands this schema. Documented in docs/architecture/events.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MediaRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["image", "document", "video", "audio", "sticker"]
    mime_type: str | None = Field(default=None, max_length=200)
    filename: str | None = Field(default=None, max_length=255)
    size_bytes: int | None = Field(default=None, ge=0)
    # Opaque provider data needed to download the media later (e.g. the webhook message object).
    provider_ref: dict[str, Any] = Field(default_factory=dict)


class NormalizedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(max_length=32)
    event_id: str = Field(min_length=1, max_length=200)
    event_type: Literal["message", "status", "other"]
    sender: str | None = Field(default=None, max_length=32)  # digits only, country code
    sender_name: str | None = Field(default=None, max_length=120)
    from_me: bool = False
    is_group: bool = False
    timestamp: datetime | None = None
    text: str | None = Field(default=None, max_length=4096)
    media: MediaRef | None = None

    @field_validator("sender")
    @classmethod
    def _digits(cls, v: str | None) -> str | None:
        if v is None:
            return None
        digits = "".join(ch for ch in v if ch.isdigit())
        if not 6 <= len(digits) <= 20:
            raise ValueError("sender must be a phone number with country code")
        return digits

"""DocumentClassifier interface and the validated classification schema."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, field_validator


@dataclass
class ClassificationInput:
    description: str | None
    extracted_text: str | None
    original_filename: str | None
    mime_type: str
    extension: str
    allowed_categories: list[str]
    # Raw bytes for vision/OCR when local extraction yielded no text (images, scanned PDFs).
    media_bytes: bytes | None = None
    warnings: list[str] = field(default_factory=list)


class RawClassification(BaseModel):
    """What a classifier proposes. Untrusted until post-validated by the pipeline."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    document_type: str = Field(default="Other", max_length=60)
    company_or_person: str | None = Field(default=None, max_length=200)
    date_or_period: str | None = Field(default=None, max_length=60)
    summary: str | None = Field(default=None, max_length=600)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_found: bool = True

    @field_validator("company_or_person", "date_or_period", "summary", mode="before")
    @classmethod
    def _unknown_to_none(cls, v: object) -> object:
        if isinstance(v, str) and v.strip().lower() in {"", "unknown", "n/a", "na", "none", "null"}:
            return None
        return v

    @field_validator("summary", mode="before")
    @classmethod
    def _truncate_summary(cls, v: object) -> object:
        return v[:600] if isinstance(v, str) else v

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v: object) -> object:
        try:
            return min(1.0, max(0.0, float(v)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0


class ClassifierError(Exception):
    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


class DocumentClassifier(ABC):
    name: str = "abstract"

    @abstractmethod
    def classify(self, inp: ClassificationInput) -> RawClassification: ...

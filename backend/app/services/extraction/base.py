"""DocumentExtractor interface and result type."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

ExtractionStatus = Literal["success", "partial", "empty", "unsupported", "failed"]


@dataclass
class ExtractionLimits:
    max_pages: int = 200
    max_sheets: int = 50
    max_slides: int = 300
    max_chars: int = 200_000
    max_rows_per_sheet: int = 5000


@dataclass
class ExtractionResult:
    status: ExtractionStatus
    text: str = ""
    format: str = ""
    page_count: int | None = None  # pages, sheets or slides depending on format
    warnings: list[str] = field(default_factory=list)


class DocumentExtractor(ABC):
    kinds: tuple[str, ...] = ()

    @abstractmethod
    def extract(self, data: bytes, limits: ExtractionLimits) -> ExtractionResult: ...


class TextAccumulator:
    """Collects text up to a character cap and records truncation."""

    def __init__(self, max_chars: int) -> None:
        self.max_chars = max_chars
        self.parts: list[str] = []
        self.size = 0
        self.truncated = False

    def add(self, text: str | None) -> bool:
        if not text or self.truncated:
            return not self.truncated
        remaining = self.max_chars - self.size
        if len(text) > remaining:
            self.parts.append(text[:remaining])
            self.size = self.max_chars
            self.truncated = True
            return False
        self.parts.append(text)
        self.size += len(text)
        return True

    @property
    def text(self) -> str:
        return "\n".join(self.parts).strip()

"""Deterministic keyword-based classifier.

Used when AI processing is disabled for a tenant or category, or as a fallback. It only uses
the user's description, the original filename and locally extracted text — nothing leaves
the server. It never invents values: unknown fields stay None.
"""

from __future__ import annotations

import re

from app.providers.ai.base import ClassificationInput, DocumentClassifier, RawClassification
from app.services.dates import parse_date_or_period

DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "Cheque": ["cheque", "check no", "chq", "pay to the order", "cheque no"],
    "Invoice": ["invoice", "tax invoice", "inv no", "inv#", "gst invoice", "proforma"],
    "Receipt": ["receipt", "received with thanks", "payment received", "rcpt"],
    "Contract": ["contract", "agreement", "mou", "memorandum of understanding", "nda"],
    "Bill": ["bill", "electricity bill", "utility bill", "phone bill", "water bill"],
    "Purchase Order": ["purchase order", "p.o.", "po no", "po number", " po "],
    "Bank Statement": ["bank statement", "statement of account", "account statement"],
    "ID/KYC": ["aadhaar", "aadhar", "pan card", "passport", "kyc", "voter id", "driving licence"],
    "Report": ["report", "analysis", "summary report", "audit report"],
    "Certificate": ["certificate", "certified that", "certification"],
}

_COMPANY = re.compile(r"\b(?:from|by|of|to|for)\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,4})")
_COMPANY_SUFFIX = re.compile(
    r"\b([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,4}\s+"
    r"(?:Pvt\.?\s*Ltd\.?|Private Limited|Ltd\.?|LLP|Traders|Enterprises|Industries|"
    r"Constructions?|Co\.))"
)
_STOP = {"The", "This", "My", "Our", "A", "An", "Invoice", "Receipt", "Bill", "September"}


def _match_category(text: str, categories: list[str], keywords: dict[str, list[str]]) -> str | None:
    lowered = f" {text.lower()} "
    best, best_pos = None, len(lowered) + 1
    for cat in categories:
        for kw in [*keywords.get(cat, []), cat.lower()]:
            pos = lowered.find(kw)
            if pos != -1 and pos < best_pos:
                best, best_pos = cat, pos
    return best


def _company(text: str) -> str | None:
    for regex in (_COMPANY_SUFFIX, _COMPANY):
        m = regex.search(text)
        if m:
            words = [w for w in m.group(1).split() if w not in _STOP]
            if words:
                return " ".join(words)[:200]
    return None


class RulesClassifier(DocumentClassifier):
    name = "rules"

    def __init__(self, keywords: dict[str, list[str]] | None = None) -> None:
        self.keywords = {**DEFAULT_KEYWORDS, **(keywords or {})}

    def classify(self, inp: ClassificationInput) -> RawClassification:
        desc = inp.description or ""
        name = (inp.original_filename or "").replace("_", " ")
        body = (inp.extracted_text or "")[:20_000]
        categories = [c for c in inp.allowed_categories if c != "Other"]

        doc_type = None
        confidence = 0.0
        for source, weight in ((desc, 0.75), (name, 0.6), (body, 0.5)):
            doc_type = _match_category(source, categories, self.keywords)
            if doc_type:
                confidence = weight
                break

        company = _company(desc) or _company(body[:3000])
        period = parse_date_or_period(desc) or parse_date_or_period(body[:5000])
        return RawClassification(
            document_type=doc_type or "Other",
            company_or_person=company,
            date_or_period=period.period if period else None,
            summary=None,
            confidence=confidence,
            evidence_found=bool(doc_type),
        )

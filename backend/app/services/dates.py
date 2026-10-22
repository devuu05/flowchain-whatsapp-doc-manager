"""Deterministic date / period parsing (Indian day-first convention)."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from datetime import date, timedelta

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}  # fmt: skip
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

_ISO = re.compile(r"\b(20\d{2}|19\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_DMY = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b")
_D_MON_Y = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-]+({_MONTH_RE})[a-z]*[\s,\-]+(\d{{4}})\b", re.I
)
_MON_D_Y = re.compile(
    rf"\b({_MONTH_RE})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I
)
_MON_Y = re.compile(rf"\b({_MONTH_RE})[a-z]*[\s,\-']+(\d{{4}})\b", re.I)
_YM = re.compile(r"\b(20\d{2})-(\d{1,2})\b")
_FY = re.compile(r"\bFY\s?(\d{2,4})\s?[-/]\s?(\d{2,4})\b", re.I)
_YEAR = re.compile(r"\b(20\d{2})\b")


@dataclass
class ParsedPeriod:
    date: dt.date | None  # exact date if known
    period: str | None  # normalized label: YYYY-MM-DD, YYYY-MM, YYYY or FYxxxx-yy
    start: dt.date | None
    end: dt.date | None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _month_end(y: int, m: int) -> date:
    return date(y + 1, 1, 1).replace(day=1) if m == 12 else date(y, m + 1, 1)


def parse_date_or_period(text: str | None) -> ParsedPeriod | None:
    """Find the first unambiguous date or period in text. Returns None if nothing found."""
    if not text:
        return None

    for regex, order in ((_ISO, "ymd"), (_D_MON_Y, "dMy"), (_MON_D_Y, "Mdy"), (_DMY, "dmy")):
        m = regex.search(text)
        if not m:
            continue
        g = m.groups()
        if order == "ymd":
            d = _safe_date(int(g[0]), int(g[1]), int(g[2]))
        elif order == "dMy":
            d = _safe_date(int(g[2]), MONTHS[g[1].lower()], int(g[0]))
        elif order == "Mdy":
            d = _safe_date(int(g[2]), MONTHS[g[0].lower()], int(g[1]))
        else:
            year = int(g[2]) if len(g[2]) == 4 else 2000 + int(g[2])
            d = _safe_date(year, int(g[1]), int(g[0]))  # day-first
        if d:
            return ParsedPeriod(d, d.isoformat(), d, d)
    m = _FY.search(text)
    if m:
        a = int(m.group(1))
        a = a if a > 999 else 2000 + a
        return ParsedPeriod(None, f"FY{a}-{str(a + 1)[-2:]}", date(a, 4, 1), date(a + 1, 3, 31))
    m = _MON_Y.search(text)
    if m:
        month = MONTHS.get(m.group(1).lower())
        if month:
            y = int(m.group(2))
            return ParsedPeriod(
                None,
                f"{y}-{month:02d}",
                date(y, month, 1),
                _month_end(y, month) - timedelta(days=1),
            )
    m = _YM.search(text)
    if m and 1 <= int(m.group(2)) <= 12:
        y, mo = int(m.group(1)), int(m.group(2))
        return ParsedPeriod(
            None, f"{y}-{mo:02d}", date(y, mo, 1), _month_end(y, mo) - timedelta(days=1)
        )
    m = _YEAR.search(text)
    if m:
        y = int(m.group(1))
        return ParsedPeriod(None, str(y), date(y, 1, 1), date(y, 12, 31))
    return None

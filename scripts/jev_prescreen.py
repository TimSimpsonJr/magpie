"""ASCII only. PURE (this task): the Part A deterministic numeric/date + computed-value gate.

Part A (spec 2.3) lets a claim skip the per-claim extraction-verifier only when every number
and date in the claim also appears in its cited span, and the claim makes no computed-value
assertion (totals, averages, percentages, comparisons, rates). This module holds the local,
network-free gate; routing and the CLI build on it in later tasks.

Gate rules (Decision 9):

- Dates are read first with ``DATE_PATTERNS`` (longest-first) and blanked out of the text, so
  their parts are never re-read as bare numbers. A claim date agrees with a span date when the
  span date has every component the claim date has, with equal values (a claim may be less
  specific: "March 2026" agrees with "March 15, 2026").
- Bare numbers come from the date-blanked text and are normalized (thousands commas, leading
  zeros, trailing fractional zeros). The span number set is those numbers PLUS only the 4-digit
  year of each span date; a span date's month and day never join it.
- The gate is digit-only: spelled-out numbers ("fourteen") are not extracted or compared.
  Jev's entailment question and the verifier cover them.
"""
from __future__ import annotations

import re

# Month names and common abbreviations (with optional period, handled in the patterns).
_MONTH = (
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

# Longest-first: a full date must be consumed before a month-year pattern can take part of it.
DATE_PATTERNS = [
    ("ymd_iso", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    ("mdy_num", re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b")),
    ("mdy_name", re.compile(rf"(?i)\b{_MONTH}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b")),
    ("dmy_name", re.compile(rf"(?i)\b(\d{{1,2}})\s+{_MONTH}\.?,?\s+(\d{{4}})\b")),
    ("my_name", re.compile(rf"(?i)\b{_MONTH}\.?,?\s+(\d{{4}})\b")),
]

# Digits with optional thousands groups and an optional fractional part.
NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")

# A claim with any of these cues asserts a derived value the span may not state verbatim.
COMPUTED_CUE_RE = re.compile(
    r"(?i)%|\b(?:total(?:s|ed|ing)?|sum(?:s|med|ming)?|average(?:s|d)?|percent(?:age)?s?|per"
    r"|ratios?|more\s+than|less\s+than|fewer\s+than|increase(?:s|d)?|increasing"
    r"|decrease(?:s|d)?|decreasing|rates?)\b")

# Two-digit years at or below this pivot are 20xx; above it, 19xx.
_TWO_DIGIT_YEAR_PIVOT = 69

# Three-letter month prefix -> month number.
_MONTH_NUMBERS = {
    name: index + 1
    for index, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))
}

REASON_NUMERIC_MISMATCH = "numeric_mismatch"
REASON_COMPUTED_VALUE = "computed_value"


def _month_number(name: str) -> int:
    return _MONTH_NUMBERS[name[:3].lower()]


def _year(token: str) -> int:
    year = int(token)
    if len(token) == 2:
        year += 2000 if year <= _TWO_DIGIT_YEAR_PIVOT else 1900
    return year


def _normalize_match(kind: str, groups: tuple[str, ...]) -> tuple[int, ...]:
    if kind == "ymd_iso":
        year, month, day = groups
        return (int(year), int(month), int(day))
    if kind == "mdy_num":
        month, day, year = groups
        return (_year(year), int(month), int(day))
    if kind == "mdy_name":
        month, day, year = groups
        return (int(year), _month_number(month), int(day))
    if kind == "dmy_name":
        day, month, year = groups
        return (int(year), _month_number(month), int(day))
    if kind == "my_name":
        month, year = groups
        return (int(year), _month_number(month))
    raise ValueError(f"unknown date pattern: {kind}")


def extract_dates(text: str) -> tuple[list[tuple[int, ...]], str]:
    """Normalized ``(y, m, d)`` / ``(y, m)`` dates, and ``text`` with each date span blanked.

    Patterns run longest-first; each match is replaced by the same number of spaces before the
    next pattern runs, so offsets and word boundaries in the remaining text are preserved.
    Dates are returned in pattern order, then left-to-right within a pattern.
    """
    dates: list[tuple[int, ...]] = []
    rest = text
    for kind, pattern in DATE_PATTERNS:
        spans: list[tuple[int, int]] = []
        for match in pattern.finditer(rest):
            dates.append(_normalize_match(kind, match.groups()))
            spans.append(match.span())
        for start, end in reversed(spans):
            rest = rest[:start] + " " * (end - start) + rest[end:]
    return dates, rest


def normalize_number(tok: str) -> str:
    """Canonical form: no thousands commas, no leading zeros, no trailing fractional zeros."""
    plain = tok.replace(",", "")
    whole, _, frac = plain.partition(".")
    whole = whole.lstrip("0") or "0"
    frac = frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def _numbers(text: str) -> set[str]:
    return {normalize_number(tok) for tok in NUMBER_RE.findall(text)}


def _date_agrees(claim_date: tuple[int, ...], span_date: tuple[int, ...]) -> bool:
    return len(span_date) >= len(claim_date) and span_date[: len(claim_date)] == claim_date


def numeric_gate(claim_text: str, span: str) -> str | None:
    """``"numeric_mismatch"``, else ``"computed_value"``, else None (spec 2.3 reason order).

    numeric_mismatch: some claim date has no agreeing span date, or some bare claim number is
    not in the span number set (date-blanked span numbers plus each span date's 4-digit year).
    computed_value: the claim matches ``COMPUTED_CUE_RE``.
    """
    claim_dates, claim_rest = extract_dates(claim_text)
    span_dates, span_rest = extract_dates(span)

    for claim_date in claim_dates:
        if not any(_date_agrees(claim_date, span_date) for span_date in span_dates):
            return REASON_NUMERIC_MISMATCH

    span_numbers = _numbers(span_rest) | {str(date[0]) for date in span_dates}
    if not _numbers(claim_rest) <= span_numbers:
        return REASON_NUMERIC_MISMATCH

    if COMPUTED_CUE_RE.search(claim_text):
        return REASON_COMPUTED_VALUE
    return None

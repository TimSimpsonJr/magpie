"""Authoritative spaCy-NER + structured-regex PII-EXPOSURE tally over a FOIA
free-text column. NER runs over DISTINCT values then weights by row counts (the
pilot's ~7x efficiency lesson). Officials named for accountability are split from
PII that should have been sanitized. See the Phase 5 design doc for rationale.

Pure core, spaCy only at the edge: the distinct/weight/regex/tally logic imports
no heavy ML; the lazy SpacyPersonClassifier is the only spaCy touch-point, so the
tally is golden-testable with a fake classifier. Decoupled from recipe.check_pii.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import pandas as pd


def _is_blank(value: Any) -> bool:
    """True for None / NaN / empty-or-whitespace-only string."""
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(value, str) and value.strip() == ""


def distinct_texts(series: pd.Series) -> tuple[list[str], list[int]]:
    """Distinct non-blank texts + the row count each covers.

    Outer whitespace is STRIPPED before counting (so "John " and "John"
    collapse); case is PRESERVED (spaCy NER is case-sensitive). Null / blank /
    whitespace-only rows are dropped.
    """
    stripped = series.map(lambda v: v.strip() if isinstance(v, str) else v)
    nonblank = stripped[~stripped.map(_is_blank)]
    vc = nonblank.value_counts()
    return [str(t) for t in vc.index], [int(c) for c in vc.values]


def text_id(text: str) -> str:
    """Stable LOCAL join key for a distinct text: truncated sha256 hex of the
    STRIPPED text, so it matches distinct_texts (``text_id("John ") ==
    text_id("John")``). Case-preserved. redact-output joins on this id LOCALLY;
    it never crosses a published path (published notes carry the aggregate tally
    only -- design doc Section 7).
    """
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]

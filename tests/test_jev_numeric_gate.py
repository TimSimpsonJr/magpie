"""Offline tests for the Part A numeric/date + computed-value gate (plan Task 5, spec 2.3)."""
from __future__ import annotations

import pytest

from scripts.jev_prescreen import (
    COMPUTED_CUE_RE,
    DATE_PATTERNS,
    extract_dates,
    normalize_number,
    numeric_gate,
)

GATE_CASES = [
    # 1
    ("Officer Ramirez ran 482 searches in March 2026.",
     "Officer Ramirez ran 482 searches in March 2026, exceeding the quota.", None),
    # 2
    ("ran 1,482 searches", "ran 1482 searches", None),
    # 3
    ("ran 483 searches", "ran 482 searches", "numeric_mismatch"),
    # 4
    ("On 2026-03-15 the audit ran", "On March 15, 2026 the audit ran", None),
    # 5
    ("On 3/15/2026", "on 15 March 2026", None),
    # 6
    ("On March 16, 2026", "on March 15, 2026", "numeric_mismatch"),
    # 7
    ("In March 2026", "on March 15, 2026", None),
    # 8
    ("In April 2026", "on March 15, 2026", "numeric_mismatch"),
    # 9
    ("during 2026", "on March 15, 2026", None),
    # 10
    ("Sept. 3, 2025", "logged 2025-09-03", None),
    # 11
    ("The total was 482", "482 searches", "computed_value"),
    # 12
    ("Searches increased after the policy", "Searches rose after the policy", "computed_value"),
    # 13
    ("12% of searches were flagged", "12% of searches were flagged", "computed_value"),
    # 14
    ("three searches per day", "three searches per day", "computed_value"),
    # 15
    ("more than 400 searches", "482 searches", "numeric_mismatch"),
    # 16
    ("ran 482 searches", "ran 4820 searches", "numeric_mismatch"),
    # 17
    ("paid 482.50 dollars", "paid 482.5 dollars", None),
    # 18
    ("Case 2026-0042 was closed", "Case 2026-0042 was closed", None),
    # 19
    ("The unit operated separately", "The unit operated separately", None),
    # 20
    ("The officer ran searches.", "The officer ran searches in 2026.", None),
    # 21
    ("The rate was 5", "the rate was 5", "computed_value"),
    # 22: day 15 of a span date never joins the span number set
    ("ran 15 searches", "On March 15, 2026 the officer ran 14 searches", "numeric_mismatch"),
]


@pytest.mark.parametrize(
    "claim,span,expected",
    GATE_CASES,
    ids=[f"row{i + 1:02d}" for i in range(len(GATE_CASES))],
)
def test_numeric_gate_table(claim, span, expected):
    assert numeric_gate(claim, span) == expected


def test_extract_dates_two_digit_year_mdy():
    assert extract_dates("on 3/4/99")[0] == [(1999, 3, 4)]


def test_extract_dates_two_digit_year_pivot():
    assert extract_dates("on 3/4/69")[0] == [(2069, 3, 4)]
    assert extract_dates("on 3/4/70")[0] == [(1970, 3, 4)]


def test_extract_dates_month_year():
    assert extract_dates("May 2026")[0] == [(2026, 5)]


def test_extract_dates_modal_may_is_not_a_date():
    assert extract_dates("you may 2 go")[0] == []


def test_extract_dates_blanks_date_spans_with_spaces():
    dates, rest = extract_dates("on March 15, 2026 ran 14")
    assert dates == [(2026, 3, 15)]
    assert len(rest) == len("on March 15, 2026 ran 14")
    assert "15" not in rest and "2026" not in rest
    assert rest.endswith("ran 14")


def test_extract_dates_longest_first_consumes_full_date():
    # mdy_name must win over my_name, so the day is kept.
    assert extract_dates("March 15, 2026")[0] == [(2026, 3, 15)]


def test_extract_dates_formats_normalize_equal():
    iso = extract_dates("2026-03-15")[0]
    assert iso == [(2026, 3, 15)]
    assert extract_dates("3/15/2026")[0] == iso
    assert extract_dates("15 March 2026")[0] == iso
    assert extract_dates("Mar. 15th, 2026")[0] == iso


def test_normalize_number():
    assert normalize_number("007") == "7"
    assert normalize_number("482.50") == "482.5"
    assert normalize_number("5.0") == "5"
    assert normalize_number("1,482") == "1482"
    assert normalize_number("0") == "0"
    assert normalize_number("000") == "0"
    assert normalize_number("0.50") == "0.5"


def test_pattern_order_is_longest_first():
    assert [name for name, _ in DATE_PATTERNS] == [
        "ymd_iso", "mdy_num", "mdy_name", "dmy_name", "my_name"]


def test_numeric_mismatch_checked_before_computed_value():
    assert numeric_gate("The total was 483", "482 searches") == "numeric_mismatch"


@pytest.mark.parametrize("cue", [
    "total", "totals", "sum", "summed", "average", "averaged", "percent",
    "percentage", "per", "ratio", "more than", "less than", "fewer than",
    "increase", "decreasing", "rate", "rates", "%",
])
def test_computed_cues_match(cue):
    assert COMPUTED_CUE_RE.search(f"the {cue} of it")


@pytest.mark.parametrize("word", ["person", "separately", "summary", "totally", "operate"])
def test_computed_cues_respect_word_boundaries(word):
    assert COMPUTED_CUE_RE.search(f"the {word} was here") is None

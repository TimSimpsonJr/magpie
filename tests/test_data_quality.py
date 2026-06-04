"""Tests for the data-quality gate (``scripts/data_quality.py``), Phase 3.2.

FOIA exports are silently truncated at spreadsheet / export ceilings (Google
Sheets and many CSV exporters stop at 2**20 - 1 == 1,048,575 data rows), and
deliveries often cover a narrower window than was requested. This gate flags
both so an analyst never publishes a finding on incomplete data -- a core rigor
guardrail of the Magpie suite.

Every fixture here is built INLINE and SMALL. Critically, the truncation tests
never materialize a 1,048,575-row DataFrame: the row count is passed as an int
(the function accepts either an int or a DataFrame), and a separate tiny
DataFrame exercises the ``len(df)`` branch. The module under test is pure
(pandas/numpy, deterministic, no IO/clock/random/network) and decoupled from
``stats.py`` / ``load_table.py``.
"""

import numpy as np
import pandas as pd

from scripts.data_quality import (
    analyze_anomalies,
    check_date_window,
    check_truncation,
    data_quality_report,
)

# The Google-Sheets / CSV-export row ceiling: 2**20 rows minus one header row.
CEILING = 2**20 - 1  # 1,048,575


# ==========================================================================
# 3.2.a  check_truncation -- EXACT-match-at-ceiling is the high-confidence flag
# ==========================================================================

def test_ceiling_arithmetic_is_2_pow_20_minus_1():
    # Anchor the magic number so a future edit can't silently drift it. 2**20 is
    # 1,048,576 rows; minus one header leaves 1,048,575 data rows.
    assert CEILING == 1_048_575
    assert CEILING == 2**20 - 1


def test_truncation_flags_exactly_at_ceiling():
    # A row count landing EXACTLY on the export ceiling is the strong signal that
    # the upstream exporter clipped the data.
    result = check_truncation(CEILING)
    assert result["truncated"] is True
    assert result["n_rows"] == CEILING
    assert result["ceiling"] == CEILING


def test_truncation_message_recommends_request_the_gap():
    # When flagged, the message must steer the analyst to re-request a
    # weekly/native/database export that dodges the 2**20 row cap. Substring
    # check is case-insensitive so wording can evolve.
    msg = check_truncation(CEILING)["message"].lower()
    assert "request" in msg and "gap" in msg


def test_truncation_one_below_ceiling_is_not_flagged():
    # One row below the ceiling is NOT auto-flagged: only an EXACT match is the
    # high-confidence signal (near-but-below counts are intentionally left for a
    # human to judge, never auto-asserted here).
    result = check_truncation(CEILING - 1)  # 1,048,574
    assert result["truncated"] is False
    assert result["n_rows"] == CEILING - 1


def test_truncation_small_count_is_not_flagged():
    result = check_truncation(100)
    assert result["truncated"] is False
    assert result["n_rows"] == 100


def test_truncation_above_ceiling_is_not_flagged():
    # A count ABOVE the ceiling means the source clearly is not clipped at 2**20
    # (e.g. a native DB export), so the exact-match flag must not fire.
    result = check_truncation(CEILING + 5)
    assert result["truncated"] is False


def test_truncation_accepts_dataframe_via_len():
    # The DataFrame branch must use len(df). A tiny 3-row frame is nowhere near
    # the ceiling, so it must NOT flag -- proving the branch counts rows rather
    # than mis-reading the object. (We deliberately never build a 1M-row frame.)
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    result = check_truncation(df)
    assert result["truncated"] is False
    assert result["n_rows"] == 3


def test_truncation_custom_ceiling():
    # The ceiling is overridable (e.g. an Excel .xls 65,536 cap). Exact match on
    # the custom ceiling flags; off-by-one does not.
    assert check_truncation(50, ceiling=50)["truncated"] is True
    assert check_truncation(49, ceiling=50)["truncated"] is False
    assert check_truncation(50, ceiling=50)["ceiling"] == 50


# ==========================================================================
# 3.2.b  check_date_window -- delivery narrower than the requested window
# ==========================================================================

def test_date_window_flags_missing_head_and_tail():
    # Requested 2026-03-01..2026-04-30 but the delivery only spans
    # 2026-03-15..2026-04-20 -- the head (first two weeks) and tail (last ten
    # days) of the requested window are missing.
    df = pd.DataFrame(
        {"date": ["2026-03-15", "2026-03-20", "2026-04-01", "2026-04-20"]}
    )
    result = check_date_window(
        df,
        "date",
        requested_start="2026-03-01",
        requested_end="2026-04-30",
    )
    assert result["missing_head"] is True
    assert result["missing_tail"] is True
    assert pd.Timestamp(result["actual_start"]) == pd.Timestamp("2026-03-15")
    assert pd.Timestamp(result["actual_end"]) == pd.Timestamp("2026-04-20")
    assert result["n_undated"] == 0


def test_date_window_full_coverage_flags_nothing():
    # The delivery fully covers (indeed exceeds) the requested window on both
    # ends, so neither head nor tail is missing.
    df = pd.DataFrame(
        {"date": ["2026-02-15", "2026-03-10", "2026-04-10", "2026-05-15"]}
    )
    result = check_date_window(
        df,
        "date",
        requested_start="2026-03-01",
        requested_end="2026-04-30",
    )
    assert result["missing_head"] is False
    assert result["missing_tail"] is False


def test_date_window_exact_boundaries_are_not_missing():
    # When actual min/max land EXACTLY on the requested bounds, nothing is
    # missing (the test is strictly narrower-than, not "not wider than").
    df = pd.DataFrame({"date": ["2026-03-01", "2026-03-15", "2026-04-30"]})
    result = check_date_window(
        df, "date", requested_start="2026-03-01", requested_end="2026-04-30"
    )
    assert result["missing_head"] is False
    assert result["missing_tail"] is False


def test_date_window_only_head_missing():
    # Delivery starts late but runs past the requested end: head missing, tail
    # present.
    df = pd.DataFrame({"date": ["2026-03-15", "2026-05-10"]})
    result = check_date_window(
        df, "date", requested_start="2026-03-01", requested_end="2026-04-30"
    )
    assert result["missing_head"] is True
    assert result["missing_tail"] is False


def test_date_window_no_request_means_no_missing_flags():
    # With no requested window, there is nothing to be narrower-than: the flags
    # are False but the actual span is still reported (descriptive).
    df = pd.DataFrame({"date": ["2026-03-15", "2026-04-20"]})
    result = check_date_window(df, "date")
    assert result["missing_head"] is False
    assert result["missing_tail"] is False
    assert pd.Timestamp(result["actual_start"]) == pd.Timestamp("2026-03-15")
    assert result["requested_start"] is None
    assert result["requested_end"] is None


def test_date_window_counts_undated_rows_and_ignores_them():
    # NaT / unparseable dates are counted (n_undated) and excluded from the
    # min/max span -- a garbage date must not widen or narrow the actual window.
    df = pd.DataFrame(
        {"date": ["2026-03-15", "not-a-date", None, "2026-04-20"]}
    )
    result = check_date_window(
        df, "date", requested_start="2026-03-01", requested_end="2026-04-30"
    )
    assert result["n_undated"] == 2
    assert pd.Timestamp(result["actual_start"]) == pd.Timestamp("2026-03-15")
    assert pd.Timestamp(result["actual_end"]) == pd.Timestamp("2026-04-20")


def test_date_window_all_nat_does_not_crash():
    # An all-NaT / unparseable column must be handled gracefully: no exception,
    # actual span is None, and there is no false missing-head/tail claim (you
    # can't assert a gap when you have no dates at all).
    df = pd.DataFrame({"date": ["junk", None, "also-junk"]})
    result = check_date_window(
        df, "date", requested_start="2026-03-01", requested_end="2026-04-30"
    )
    assert result["actual_start"] is None
    assert result["actual_end"] is None
    assert result["missing_head"] is False
    assert result["missing_tail"] is False
    assert result["n_undated"] == 3


def test_date_window_empty_dataframe_does_not_crash():
    df = pd.DataFrame({"date": pd.Series([], dtype="object")})
    result = check_date_window(
        df, "date", requested_start="2026-03-01", requested_end="2026-04-30"
    )
    assert result["actual_start"] is None
    assert result["actual_end"] is None
    assert result["missing_head"] is False
    assert result["missing_tail"] is False
    assert result["n_undated"] == 0


def test_date_window_echoes_requested_bounds():
    # The requested bounds are echoed back (normalized to timestamps) so a report
    # consumer can render them without re-parsing.
    df = pd.DataFrame({"date": ["2026-03-15"]})
    result = check_date_window(
        df, "date", requested_start="2026-03-01", requested_end="2026-04-30"
    )
    assert pd.Timestamp(result["requested_start"]) == pd.Timestamp("2026-03-01")
    assert pd.Timestamp(result["requested_end"]) == pd.Timestamp("2026-04-30")


# ==========================================================================
# 3.2.c  analyze_anomalies -- per-column descriptive LEADS (not verdicts)
# ==========================================================================

def test_anomalies_flag_mostly_null_column():
    # A column that is >90% null is a lead worth surfacing (the field may be
    # unreliable). 1 value in 20 rows = 95% null.
    df = pd.DataFrame(
        {
            "mostly_empty": [1] + [None] * 19,
            "clean": list(range(20)),
        }
    )
    warnings = analyze_anomalies(df)
    hits = [w for w in warnings if w["column"] == "mostly_empty"]
    assert hits, f"expected a null-heavy warning for 'mostly_empty'; got {warnings}"
    assert any(w["kind"] == "high_null" for w in hits)
    # The detail carries the null fraction so a reader can judge severity.
    assert any("null_pct" in w["detail"] or "null" in w["detail"].lower() for w in hits)


def test_anomalies_clean_column_has_no_warning():
    # A fully-populated, well-typed column must produce no warning -- the gate
    # reports leads, not noise on clean data.
    df = pd.DataFrame({"clean": list(range(20))})
    warnings = analyze_anomalies(df)
    assert [w for w in warnings if w["column"] == "clean"] == []


def test_anomalies_flag_all_null_column():
    df = pd.DataFrame({"empty": [None] * 5, "ok": [1, 2, 3, 4, 5]})
    warnings = analyze_anomalies(df)
    hits = [w for w in warnings if w["column"] == "empty"]
    assert hits
    assert any(w["kind"] in ("all_null", "high_null") for w in hits)


def test_anomalies_flag_all_blank_column():
    # An all-blank (empty/whitespace strings) column reads as "present" to a
    # naive null check but carries no information -- surface it as a lead.
    df = pd.DataFrame({"blank": ["", " ", "   ", ""], "ok": [1, 2, 3, 4]})
    warnings = analyze_anomalies(df)
    hits = [w for w in warnings if w["column"] == "blank"]
    assert hits
    assert any(w["kind"] in ("all_blank", "all_null") for w in hits)


def test_anomalies_flag_dirty_numeric_coercion_mismatch():
    # An object column that is mostly numeric but has a few non-numeric junk
    # values is a likely dirty numeric column. >80% parseable with some junk
    # triggers a coercion-mismatch lead reporting the non-parseable count. Here
    # 10 of 12 non-null cells parse (~83%, clearing the >80% bar) and 2 do not.
    df = pd.DataFrame(
        {
            "amount": [
                "10", "20", "30", "40", "50",
                "60", "70", "80", "90", "100",
                "n/a", "oops",
            ],
        }
    )
    warnings = analyze_anomalies(df)
    hits = [w for w in warnings if w["column"] == "amount"]
    assert hits, f"expected a coercion-mismatch lead for 'amount'; got {warnings}"
    mismatch = [w for w in hits if w["kind"] == "type_coercion_mismatch"]
    assert mismatch
    # The detail names how many cells failed to parse (2 here: "n/a", "oops").
    assert "2" in mismatch[0]["detail"]


def test_anomalies_clean_numeric_object_column_is_not_flagged_as_mismatch():
    # A 100%-parseable string-of-numbers column has no MISMATCH (no non-parseable
    # cells), so it must not get a coercion-mismatch lead -- the flag is about
    # the dirty mixture, not about being object-dtyped.
    df = pd.DataFrame({"amount": ["10", "20", "30", "40"]})
    warnings = analyze_anomalies(df)
    assert [w for w in warnings if w["kind"] == "type_coercion_mismatch"] == []


def test_anomalies_text_column_is_not_flagged_as_numeric_mismatch():
    # A genuinely textual column (mostly NON-numeric) must not be mistaken for a
    # dirty numeric column: it's below the >80%-parseable bar.
    df = pd.DataFrame(
        {"city": ["Newark", "Trenton", "Camden", "Newark", "42"]}
    )
    warnings = analyze_anomalies(df)
    assert [w for w in warnings if w["kind"] == "type_coercion_mismatch"] == []


def test_anomalies_warnings_have_expected_shape():
    df = pd.DataFrame({"mostly_empty": [1] + [None] * 19})
    warnings = analyze_anomalies(df)
    assert isinstance(warnings, list)
    for w in warnings:
        assert set(w.keys()) >= {"column", "kind", "detail"}
        assert isinstance(w["detail"], str)


# ==========================================================================
# 3.2.d  data_quality_report -- the convenience aggregator
# ==========================================================================

def test_report_ties_all_checks_together():
    # One fixture exercising truncation (a small frame -> not truncated),
    # date-window (all dates within 2026-03-15..2026-04-20, narrower than the
    # requested 03-01..04-30 -> missing head & tail), and anomalies (a column
    # that is 11/12 null, ~92%, clearing the >90% high-null bar).
    df = pd.DataFrame(
        {
            "date": [
                "2026-03-15", "2026-03-18", "2026-03-22", "2026-03-25",
                "2026-03-28", "2026-04-01", "2026-04-05", "2026-04-08",
                "2026-04-11", "2026-04-14", "2026-04-17", "2026-04-20",
            ],
            "amount": list(range(10, 130, 10)),
            "mostly_empty": [1] + [None] * 11,
        }
    )
    report = data_quality_report(
        df,
        date_col="date",
        requested_start="2026-03-01",
        requested_end="2026-04-30",
    )
    # Truncation sub-report present and not flagged on this tiny frame.
    assert report["truncation"]["truncated"] is False
    assert report["truncation"]["n_rows"] == 12
    # Date-window sub-report present and flags the narrow delivery.
    assert report["date_window"]["missing_head"] is True
    assert report["date_window"]["missing_tail"] is True
    # Anomalies present as a list, with the null-heavy column called out.
    assert isinstance(report["anomalies"], list)
    assert any(w["column"] == "mostly_empty" for w in report["anomalies"])


def test_report_without_date_col_omits_window():
    # When no date_col is given, the date-window check is skipped (None), but the
    # other two still run.
    df = pd.DataFrame({"amount": [1, 2, 3]})
    report = data_quality_report(df)
    assert report["date_window"] is None
    assert report["truncation"]["truncated"] is False
    assert isinstance(report["anomalies"], list)


def test_report_is_json_friendly_scalars():
    # The aggregator's flag fields should be plain Python bools (not numpy
    # bool_), so a downstream JSON/markdown renderer behaves predictably.
    df = pd.DataFrame({"date": ["2026-03-15"], "amount": [1]})
    report = data_quality_report(
        df, date_col="date", requested_start="2026-03-01", requested_end="2026-04-30"
    )
    assert isinstance(report["truncation"]["truncated"], bool)
    assert isinstance(report["date_window"]["missing_head"], bool)
    assert isinstance(report["date_window"]["missing_tail"], bool)
    # And not the numpy variant specifically.
    assert not isinstance(report["truncation"]["truncated"], np.bool_)

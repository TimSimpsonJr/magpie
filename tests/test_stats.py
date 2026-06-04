"""Tests for the flagship statistics module (``scripts/stats.py``), Phase 2.

These functions back a FOIA-analysis tool, so they are pinned to the
documented Simpsonville pilot values (design doc 9): network-volume Gini
~0.805, top-1% of agencies ~27% of volume, bottom-50% ~2.5%, traffic-stop
median net-width above homicide, scheduled-job automation signatures, and
same-second burst batches.

All fixtures here are SYNTHETIC. No real corpus is read. The module under
test is generic: every function takes plain inputs (a sequence of numbers, or
a DataFrame + column names), never a Simpsonville-specific loader.
"""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.stats import (
    automation_signature,
    bottom_half_share,
    burstiness,
    category_pct,
    gini,
    median_by_category,
    presence_rate,
    top_k_share,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture_counts():
    """Load the synthetic per-agency search-count fixture.

    A heavy-tailed distribution (a mega-agency plus a long tail of
    one-search agencies) calibrated so its Gini lands on the pilot target
    ~0.805 (band [0.79, 0.82]) and its top-1% / bottom-50% volume shares
    reproduce the documented pilot concentration (~27% / ~2.5%). Actual
    fixture values: Gini 0.805, top-1% 0.271, bottom-50% 0.025.
    """
    return json.loads((FIXTURES / "agency_counts_sample.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 2.1 gini
# --------------------------------------------------------------------------

def test_gini_uniform_is_zero():
    assert gini([5, 5, 5, 5]) == 0.0


def test_gini_total_inequality_near_one():
    assert gini([0] * 99 + [100]) > 0.95


def test_gini_empty_is_zero():
    assert gini([]) == 0.0


def test_gini_all_zero_is_zero():
    assert gini([0, 0, 0]) == 0.0


def test_gini_ignores_none():
    # None entries are dropped, not treated as zero.
    assert gini([5, None, 5, None, 5, 5]) == 0.0


def test_gini_rejects_negative_values():
    # Negative magnitudes are nonsensical here and silently break the formula
    # (gini([-5, 0, 5, 10]) would be 1.25; [-10, -10, 10, 10] sums to zero and
    # the all-zero guard would mask real dispersion). Fail loudly instead.
    import pytest

    with pytest.raises(ValueError, match="non-negative"):
        gini([-5, 0, 5, 10])
    with pytest.raises(ValueError, match="non-negative"):
        gini([-10, -10, 10, 10])
    # None-filtering still runs before the negative check.
    with pytest.raises(ValueError, match="non-negative"):
        gini([None, -1, 2])
    # Existing non-negative cases are unaffected.
    assert gini([5, 5, 5, 5]) == 0.0
    assert gini([0] * 99 + [100]) > 0.95


def test_gini_matches_pilot_band():
    counts = load_fixture_counts()
    g = gini(counts)
    # Pinned to the pilot target ~0.805 (design doc 9), not a loose band.
    assert 0.79 <= g <= 0.82, f"fixture Gini {g} outside pilot band [0.79, 0.82]"


# --------------------------------------------------------------------------
# 2.2 top_k_share / bottom_half_share
# --------------------------------------------------------------------------

def test_top_k_share_reproduces_pilot():
    counts = load_fixture_counts()
    # Documented pilot: top 1% of agencies hold ~27% of all volume.
    assert top_k_share(counts, k_frac=0.01) == pytest_approx(0.27, abs=0.02)


def test_bottom_half_share_reproduces_pilot():
    counts = load_fixture_counts()
    # Documented pilot: bottom 50% of agencies hold ~2.5% of all volume.
    assert bottom_half_share(counts) == pytest_approx(0.025, abs=0.005)


def test_top_k_share_all_volume_in_one_agency():
    # One agency holds everything; even the smallest top fraction is ~all of it.
    counts = [1000] + [0] * 999
    assert top_k_share(counts, k_frac=0.001) == pytest_approx(1.0, abs=1e-9)


def test_top_k_share_uniform_is_proportional():
    # 100 equal agencies, top 1% => 1 agency => 1% of volume.
    counts = [10] * 100
    assert top_k_share(counts, k_frac=0.01) == pytest_approx(0.01, abs=1e-9)


def test_bottom_half_share_uniform_is_half():
    counts = [10] * 100
    assert bottom_half_share(counts) == pytest_approx(0.5, abs=1e-9)


def test_top_k_share_k_at_least_one():
    # Tiny k_frac on a small N still selects at least one agency.
    counts = [5, 4, 3, 2, 1]
    assert top_k_share(counts, k_frac=0.0001) == pytest_approx(5 / 15, abs=1e-9)


def test_shares_empty_and_zero_are_zero():
    assert top_k_share([]) == 0.0
    assert bottom_half_share([]) == 0.0
    assert top_k_share([0, 0, 0]) == 0.0
    assert bottom_half_share([0, 0, 0]) == 0.0


# --------------------------------------------------------------------------
# 2.3 median_by_category
# --------------------------------------------------------------------------

def _net_width_df():
    """Synthetic net-width-per-incident rows by reason.

    Encodes the real finding: a traffic stop casts a wider surveillance net
    (more cameras/plates pulled per incident) than a homicide investigation.
    """
    rows = []
    # Homicide: focused pulls, low net width.
    for v in [1, 1, 2, 2, 2, 3, 3, 4]:
        rows.append({"reason": "Homicide", "net_width": v})
    # Traffic: wide dragnet, high net width.
    for v in [8, 9, 10, 11, 12, 14, 18, 25]:
        rows.append({"reason": "Traffic", "net_width": v})
    # Theft: middle of the pack.
    for v in [4, 5, 5, 6, 7]:
        rows.append({"reason": "Theft", "net_width": v})
    return pd.DataFrame(rows)


def test_median_by_category_traffic_wider_than_homicide():
    result = median_by_category(_net_width_df(), "net_width", "reason")
    assert result["Traffic"] > result["Homicide"]


def test_median_by_category_sorted_descending():
    result = median_by_category(_net_width_df(), "net_width", "reason")
    values = list(result.values)
    assert values == sorted(values, reverse=True)
    # Traffic is the widest net, so it sorts first.
    assert result.index[0] == "Traffic"


def test_median_by_category_returns_series():
    result = median_by_category(_net_width_df(), "net_width", "reason")
    assert isinstance(result, pd.Series)


# --------------------------------------------------------------------------
# 2.4 automation_signature
# --------------------------------------------------------------------------

def _automation_df():
    rows = []
    # NIGHTBOT: every query overnight (hours 0-4) => scheduled-job signature.
    for h in [0, 1, 2, 3, 4, 0, 1, 2, 3, 4]:
        rows.append({"agency": "NIGHTBOT", "hour": h})
    # DAYDESK: every query midday (10-15) => human business hours.
    for h in [10, 11, 12, 13, 14, 15, 10, 11, 12, 15]:
        rows.append({"agency": "DAYDESK", "hour": h})
    return pd.DataFrame(rows)


def test_automation_flags_overnight_agency():
    result = automation_signature(_automation_df(), "hour", "agency")
    night = result.loc["NIGHTBOT"]
    assert night["overnight_pct"] == pytest_approx(1.0)
    assert bool(night["flagged"]) is True


def test_automation_does_not_flag_midday_agency():
    result = automation_signature(_automation_df(), "hour", "agency")
    day = result.loc["DAYDESK"]
    assert day["daytime_pct"] == pytest_approx(1.0)
    assert day["overnight_pct"] == pytest_approx(0.0)
    assert bool(day["flagged"]) is False


def test_automation_boundary_hours():
    # day_start=6 is daytime; day_end=18 is overnight (interval is [start, end)).
    df = pd.DataFrame(
        {"agency": ["A", "A"], "hour": [6, 18]}
    )
    result = automation_signature(df, "hour", "agency")
    a = result.loc["A"]
    assert a["daytime_pct"] == pytest_approx(0.5)
    assert a["overnight_pct"] == pytest_approx(0.5)


def test_automation_drops_invalid_hours_no_inflation():
    # A daytime agency with a NaN hour and an out-of-range hour (25). Those
    # dirty rows must be DROPPED, not bucketed as overnight: overnight_pct must
    # stay 0 and the agency must not be flagged. Without the guard, the two
    # invalid rows would land in the overnight bucket (2/6 -> 0.33 overnight).
    df = pd.DataFrame(
        {
            "agency": ["DAYDESK"] * 6,
            "hour": [10, 11, 12, 13, float("nan"), 25],
        }
    )
    result = automation_signature(df, "hour", "agency")
    day = result.loc["DAYDESK"]
    assert day["daytime_pct"] == pytest_approx(1.0)  # all 4 valid rows are daytime
    assert day["overnight_pct"] == pytest_approx(0.0)
    assert bool(day["flagged"]) is False


def test_automation_invalid_hours_do_not_trip_flag():
    # A mostly-daytime agency padded with invalid hours. If invalid hours were
    # counted as overnight, overnight_pct would exceed 0.5 and flag falsely.
    df = pd.DataFrame(
        {
            "agency": ["A"] * 5,
            "hour": [10, 11, float("nan"), 99, -3],
        }
    )
    result = automation_signature(df, "hour", "agency")
    a = result.loc["A"]
    # Only hours 10 and 11 are valid; both daytime.
    assert a["daytime_pct"] == pytest_approx(1.0)
    assert a["overnight_pct"] == pytest_approx(0.0)
    assert bool(a["flagged"]) is False


def test_automation_all_invalid_hours_not_flagged():
    # Every row for this agency has an invalid hour. After dropping them it has
    # zero valid rows: shares are 0.0/0.0 and it is NOT flagged.
    df = pd.DataFrame(
        {
            "agency": ["GHOST", "GHOST", "GHOST"],
            "hour": [float("nan"), 24, -1],
        }
    )
    result = automation_signature(df, "hour", "agency")
    ghost = result.loc["GHOST"]
    assert ghost["daytime_pct"] == pytest_approx(0.0)
    assert ghost["overnight_pct"] == pytest_approx(0.0)
    assert bool(ghost["flagged"]) is False


def test_automation_valid_hours_unchanged_with_drop_logic():
    # Sanity: with only valid hours, the drop logic changes nothing. A genuine
    # overnight agency is still flagged; a midday agency is still clean.
    result = automation_signature(_automation_df(), "hour", "agency")
    night = result.loc["NIGHTBOT"]
    day = result.loc["DAYDESK"]
    assert night["overnight_pct"] == pytest_approx(1.0)
    assert bool(night["flagged"]) is True
    assert day["daytime_pct"] == pytest_approx(1.0)
    assert bool(day["flagged"]) is False


# --------------------------------------------------------------------------
# 2.5 burstiness
# --------------------------------------------------------------------------

def test_burstiness_detects_ten_in_one_second():
    rows = [{"ts": "2024-01-01T03:00:00Z", "agency": "BOT"} for _ in range(10)]
    result = burstiness(pd.DataFrame(rows), "ts", "agency")
    assert result["max_same_second"] == 10
    # The size distribution records one group of size 10.
    assert result["size_distribution"][10] == 1


def test_burstiness_mostly_singletons():
    rows = [
        {"ts": f"2024-01-01T03:00:{s:02d}Z", "agency": "HUMAN"} for s in range(20)
    ]
    result = burstiness(pd.DataFrame(rows), "ts", "agency")
    assert result["max_same_second"] == 1
    assert result["size_distribution"][1] == 20


def test_burstiness_without_agency_groups_by_timestamp_only():
    # Same second, different agencies: collapsed when agency_col is omitted.
    rows = [
        {"ts": "2024-01-01T03:00:00Z", "agency": "A"},
        {"ts": "2024-01-01T03:00:00Z", "agency": "B"},
        {"ts": "2024-01-01T03:00:00Z", "agency": "C"},
        {"ts": "2024-01-01T03:00:05Z", "agency": "A"},
    ]
    df = pd.DataFrame(rows)
    assert burstiness(df, "ts")["max_same_second"] == 3
    # Splitting by agency drops the max to 1 (each agency once per second).
    assert burstiness(df, "ts", "agency")["max_same_second"] == 1


# --------------------------------------------------------------------------
# 2.6 presence_rate / category_pct
# --------------------------------------------------------------------------

def _non_empty(value):
    """A value is PRESENT when it is not None and not an empty string.

    Crucially, a redacted ``***`` is PRESENT: a value exists, it is merely
    withheld. Only blank / None / "" mean a value is genuinely ABSENT.
    """
    if value is None:
        return False
    return str(value).strip() != ""


def test_presence_rate_redaction_counts_as_present():
    series = pd.Series(["CASE123", "***", "", None, "C-9"])
    # CASE123, ***, C-9 are present (3); "" and None are absent (2).
    assert presence_rate(series, _non_empty) == pytest_approx(3 / 5)


def test_presence_rate_redaction_is_not_blank():
    # Explicit guardrail: *** must NOT be lumped in with blank/None.
    redacted = presence_rate(pd.Series(["***"]), _non_empty)
    blank = presence_rate(pd.Series([""]), _non_empty)
    assert redacted == 1.0
    assert blank == 0.0


def test_presence_rate_accepts_plain_list():
    assert presence_rate(["a", "b", "", None], _non_empty) == pytest_approx(0.5)


def test_category_pct_fraction_true():
    df = pd.DataFrame({"state": ["GA"] + ["FL"] * 9})
    mask = df["state"] != "GA"  # 9/10 out-of-state
    assert category_pct(df, mask) == pytest_approx(0.9)


def test_category_pct_all_false_is_zero():
    df = pd.DataFrame({"state": ["GA"] * 5})
    assert category_pct(df, df["state"] != "GA") == 0.0


# --------------------------------------------------------------------------
# minimal local approx helper (avoids importing pytest.approx at module top
# so the import-error surface during the red phase is the stats module only)
# --------------------------------------------------------------------------

class _Approx:
    def __init__(self, expected, abs=1e-6):
        self.expected = expected
        self.abs = abs

    def __eq__(self, other):
        return math.isclose(float(other), float(self.expected), abs_tol=self.abs)

    def __repr__(self):
        return f"approx({self.expected!r}, abs={self.abs})"


def pytest_approx(expected, abs=1e-6):
    return _Approx(expected, abs=abs)

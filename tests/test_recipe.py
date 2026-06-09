"""TDD for ``scripts/recipe.py`` -- the 13-point parameterized per-source pass.

All fixtures are SYNTHETIC and inline (no real corpus is read). The suite pins the
contract of each of the 13 checks plus the ``run_recipe`` orchestrator, and the
publish-critical rigor guardrails the checks inherit:

* keyword categorization (immigration / pretext / co-travel) is WORD-BOUNDARY
  matched via the shared ``derive`` guardrail, so the canonical trap -- "police"
  / "service" / "justice" flagged immigration off the substring "ice" -- MUST
  fail to match;
* presence is not value (a redaction sentinel is PRESENT) flows through the
  accountability check via ``stats.presence_rate``;
* a check whose required column is absent is SKIPPED-with-reason, never a crash
  and never a silently-zeroed metric;
* an unknown check name in the config is a loud ``ValueError`` (a typo must not
  drop a check), mirroring ``derive_columns``.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from scripts.recipe import (
    CHECKS,
    check_accountability,
    check_ai_moderation,
    check_blast_radius,
    check_co_travel,
    check_cross_agency,
    check_immigration,
    check_mega_users,
    check_operations,
    check_out_of_state,
    check_pii,
    check_pretext,
    check_statistical_patterns,
    check_truncation,
    run_recipe,
)


# --------------------------------------------------------------------------- #
# canonical synthetic audit-log frame (post-load / quality-gate / derive)
# --------------------------------------------------------------------------- #
#
# 12 rows. Hand-computed expectations are asserted below. The frame already
# carries the DERIVED columns the recipe consumes (geo / nets / has_case /
# base_type / hour_et / date_et) plus a raw agency + a seconds-resolution ts.
#
#  agency counts: Houston 5, Local SC 3, Atlanta 2, Miami 1, Denver 1  (5 users)
#  geo:           OOS 8, SC 3, UNK 1
#  has_case True: 5  (False 7)
#  nets present:  11 (one <NA>);  by base_type: Traffic median 1500 > Homicide 150
def sample_audit() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "agency": [
                "Houston TX PD", "Houston TX PD", "Houston TX PD",
                "Houston TX PD", "Houston TX PD",
                "Local SC PD", "Local SC PD", "Local SC PD",
                "Atlanta GA PD", "Atlanta GA PD",
                "Miami FL PD", "Denver CO PD",
            ],
            "geo": [
                "OOS", "OOS", "OOS", "OOS", "OOS",
                "SC", "SC", "SC",
                "OOS", "OOS",
                "OOS", "UNK",
            ],
            "has_case": [
                False, False, False, True, True,
                True, True, False,
                False, False,
                True, False,
            ],
            "base_type": [
                "Traffic", "Traffic", "Traffic", "Homicide", "Homicide",
                "Traffic", "Homicide", "Traffic",
                "Traffic", "Homicide",
                "Traffic", "Traffic",
            ],
            "date_et": [
                dt.date(2026, 3, 1), dt.date(2026, 3, 1), dt.date(2026, 3, 1),
                dt.date(2026, 3, 2), dt.date(2026, 3, 2),
                dt.date(2026, 3, 2), dt.date(2026, 3, 3), dt.date(2026, 3, 3),
                dt.date(2026, 3, 3), dt.date(2026, 3, 4),
                dt.date(2026, 3, 4), dt.date(2026, 3, 4),
            ],
            "ts": [
                "2026-03-01 02:00:01", "2026-03-01 02:00:01", "2026-03-01 03:00:00",
                "2026-03-02 14:00:00", "2026-03-02 15:00:00",
                "2026-03-02 10:00:00", "2026-03-03 11:00:00", "2026-03-03 12:00:00",
                "2026-03-03 01:00:00", "2026-03-04 23:00:00",
                "2026-03-04 13:00:00", "2026-03-04 16:00:00",
            ],
        }
    )
    df["nets"] = pd.array(
        [3000, 2900, 2800, 100, 200, 50, 60, pd.NA, 1500, 1600, 800, 900],
        dtype="Int64",
    )
    df["hour_et"] = pd.array(
        [2, 2, 3, 14, 15, 10, 11, 12, 1, 23, 13, 16], dtype="Int64"
    )
    return df


# --------------------------------------------------------------------------- #
# 1. truncation
# --------------------------------------------------------------------------- #

def test_truncation_clean_frame_not_flagged():
    out = check_truncation(sample_audit(), {})
    assert out["status"] == "ok"
    assert out["truncated"] is False
    assert out["n_rows"] == 12


def test_truncation_flags_exact_ceiling():
    # The DataFrame branch must not materialize a 1M-row frame: pass the count.
    out = check_truncation(pd.DataFrame({"x": range(5)}), {"ceiling": 5})
    assert out["truncated"] is True
    assert out["n_rows"] == 5
    assert "ceiling" in out["message"].lower() or "ceiling" in out["summary"].lower()


# --------------------------------------------------------------------------- #
# 2. out-of-state
# --------------------------------------------------------------------------- #

OOS_CONFIG = {"geo_col": "geo", "out_label": "OOS", "unknown_label": "UNK"}


def test_out_of_state_counts_and_honest_denominators():
    out = check_out_of_state(sample_audit(), OOS_CONFIG)
    assert out["status"] == "ok"
    assert out["total"] == 12
    assert out["out_of_state"] == 8
    assert out["in_state"] == 3
    assert out["unknown"] == 1
    # pct of TOTAL vs pct of KNOWN are reported separately (honest denominators).
    assert out["out_of_state_pct"] == pytest.approx(8 / 12)
    assert out["out_of_state_pct_of_known"] == pytest.approx(8 / 11)


def test_out_of_state_skipped_when_geo_absent():
    out = check_out_of_state(pd.DataFrame({"agency": ["x"]}), OOS_CONFIG)
    assert out["status"] == "skipped"
    assert "geo" in out["reason"]


# --------------------------------------------------------------------------- #
# 3. immigration  -- WORD-BOUNDARY guardrail (the ICE / polICE trap)
# --------------------------------------------------------------------------- #

IMM_KEYWORDS = ["ice", "immigration", "deportation", "cbp", "ero"]


def test_immigration_keyword_path_rejects_substring_false_positives():
    df = pd.DataFrame(
        {
            "reason_text": [
                "Assist local police",        # 'ice' substring -> NOT immigration
                "Community service request",  # 'ice' substring -> NOT immigration
                "Department of Justice ref",  # 'ice' substring -> NOT immigration
                "ICE detainer hold",          # genuine -> immigration
                "CBP border referral",        # genuine -> immigration
                "deportation order",          # genuine -> immigration
                "Routine traffic stop",       # unrelated -> NOT immigration
            ]
        }
    )
    out = check_immigration(df, {"text_col": "reason_text", "keywords": IMM_KEYWORDS})
    assert out["status"] == "ok"
    assert out["immigration"] == 3          # ICE, CBP, deportation only
    assert out["total"] == 7
    assert out["immigration_pct"] == pytest.approx(3 / 7)


def test_immigration_flag_col_fast_path():
    df = pd.DataFrame({"is_immigration": [True, False, True, False]})
    out = check_immigration(df, {"flag_col": "is_immigration"})
    assert out["status"] == "ok"
    assert out["immigration"] == 2
    assert out["immigration_pct"] == pytest.approx(0.5)


def test_immigration_subtype_breakdown():
    df = pd.DataFrame(
        {
            "reason_text": ["ICE hold", "deportation", "ICE detainer", "traffic"],
            "imm_type": ["criminal", "civil", "criminal", "n/a"],
        }
    )
    out = check_immigration(
        df,
        {"text_col": "reason_text", "keywords": IMM_KEYWORDS, "subtype_col": "imm_type"},
    )
    assert out["immigration"] == 3
    assert out["by_subtype"] == {"criminal": 2, "civil": 1}


def test_immigration_skipped_without_text_or_flag():
    out = check_immigration(pd.DataFrame({"x": [1]}), {})
    assert out["status"] == "skipped"


# --------------------------------------------------------------------------- #
# 4. pretext  (free-text categorization, same guardrail)
# --------------------------------------------------------------------------- #

def test_pretext_keyword_path_counts_and_respects_word_boundary():
    df = pd.DataFrame(
        {
            "reason_text": [
                "Routine traffic stop",     # traffic -> pretext
                "Expired registration",     # registration -> pretext
                "Homicide investigation",   # -> not pretext
                "Traffic-related inquiry",  # traffic (boundary at '-') -> pretext
                "Registration desk note",   # registration -> pretext
            ]
        }
    )
    out = check_pretext(
        df, {"text_col": "reason_text", "keywords": ["traffic", "registration"]}
    )
    assert out["status"] == "ok"
    assert out["pretext"] == 4
    assert out["total"] == 5


def test_pretext_category_path():
    df = pd.DataFrame({"reason_cat": ["Traffic", "Homicide", "Traffic", "Other"]})
    out = check_pretext(
        df, {"cat_col": "reason_cat", "pretext_cats": ["Traffic"]}
    )
    assert out["pretext"] == 2


# --------------------------------------------------------------------------- #
# 5. PII  (structured-regex presence indicator -- NOT the spaCy pii-sweep)
# --------------------------------------------------------------------------- #

def test_pii_structured_patterns_presence():
    df = pd.DataFrame(
        {
            "notes": [
                "Contact A12345678 for details",  # a_number
                "SSN 123-45-6789 on file",        # ssn
                "email john@example.com",         # email
                "call 555-123-4567",              # phone
                "no pii here",                    # none
            ]
        }
    )
    out = check_pii(df, {"text_cols": ["notes"]})
    assert out["status"] == "ok"
    # record-level: a row counts once even if it matches in multiple text_cols.
    assert out["records_with_pii"] == 4
    assert out["pii_pct"] == pytest.approx(4 / 5)
    assert out["records_by_pattern"]["a_number"] == 1
    assert out["records_by_pattern"]["ssn"] == 1
    assert out["records_by_pattern"]["email"] == 1
    assert out["records_by_pattern"]["phone"] == 1
    # honest about scope: defers semantic NER to pii-sweep (Phase 5).
    assert "pii-sweep" in out["note"].lower()


def test_pii_default_patterns_exclude_bare_dates():
    # A high-precision default set: a lone calendar date is NOT flagged as PII
    # (an incident date is not exposure); DOB detection is opt-in via `patterns`.
    df = pd.DataFrame({"notes": ["incident on 03/14/2026", "nothing here"]})
    out = check_pii(df, {"text_cols": ["notes"]})
    assert out["records_with_pii"] == 0
    assert "date" not in out["records_by_pattern"]


def test_pii_dedupes_record_across_multiple_text_cols():
    df = pd.DataFrame(
        {
            "reason": ["A12345678 noted", "clean"],
            "notes": ["see A12345678", "clean"],
        }
    )
    out = check_pii(df, {"text_cols": ["reason", "notes"]})
    # row 0 matches a_number in BOTH columns but is ONE record with PII.
    assert out["records_with_pii"] == 1
    assert out["records_by_pattern"]["a_number"] == 1


def test_pii_a_number_separator_variants_all_detected():
    # Real alien-registration numbers in the corpus carry separators / a leading
    # "A#", and span 8-9 digits. Every row below is one A-number in some form, so
    # the looser pattern must catch all 6 (the strict \bA\d{8,9}\b caught 0 of the
    # separator forms -- a false negative on the most sensitive identifier type).
    df = pd.DataFrame(
        {
            "notes": [
                "A123456789",               # 9 digits, no separator
                "A# 123456789",             # leading A#, space separator
                "A-123456789",              # hyphen separator
                "A 12345678",               # 8 digits, space separator
                "A-12345678",               # 8 digits, hyphen separator
                "subject A12345678 flagged",  # embedded, no separator
            ]
        }
    )
    out = check_pii(df, {"text_cols": ["notes"]})
    assert out["records_by_pattern"]["a_number"] == 6
    assert out["records_with_pii"] == 6


def test_pii_a_number_precision_guard():
    # Detection stays anchored on the A-then-digits SHAPE: a bare digit run, an
    # "A" not followed by digits, and a too-short (6-digit) run must NOT match.
    df = pd.DataFrame(
        {
            "notes": [
                "ref 123456789",     # bare 9-digit run, no A
                "graded A on test",  # A not followed by digits
                "A123456",           # only 6 digits, too short
            ]
        }
    )
    out = check_pii(df, {"text_cols": ["notes"]})
    assert out["records_by_pattern"]["a_number"] == 0


def test_pii_skipped_when_no_text_cols_present():
    out = check_pii(pd.DataFrame({"x": [1]}), {"text_cols": ["notes"]})
    assert out["status"] == "skipped"


# --------------------------------------------------------------------------- #
# 6. accountability  (case-number presence; presence != value)
# --------------------------------------------------------------------------- #

def test_accountability_no_case_rate():
    out = check_accountability(sample_audit(), {"case_col": "has_case"})
    assert out["status"] == "ok"
    assert out["with_case"] == 5
    assert out["without_case"] == 7
    assert out["no_case_pct"] == pytest.approx(7 / 12)


def test_accountability_grouped():
    out = check_accountability(
        sample_audit(), {"case_col": "has_case", "group_col": "geo"}
    )
    # SC rows 5,6,7: has_case True,True,False -> no_case_pct 1/3.
    assert out["by_group"]["SC"] == pytest.approx(1 / 3)
    # OOS rows: 0,1,2,3,4,8,9,10 has_case F,F,F,T,T,F,F,T -> 5 without / 8.
    assert out["by_group"]["OOS"] == pytest.approx(5 / 8)


# --------------------------------------------------------------------------- #
# 7. co-travel  (keyword / search-type, same guardrail; not a spatial join)
# --------------------------------------------------------------------------- #

def test_co_travel_keyword_with_word_boundary_trap():
    df = pd.DataFrame(
        {
            "reason_text": [
                "Convoy analysis",        # convoy -> co-travel
                "co-travel query",        # co-travel -> co-travel
                "Single vehicle lookup",  # -> not
                "travelogue review",      # 'travel' substring -> NOT (boundary)
            ]
        }
    )
    out = check_co_travel(
        df, {"text_col": "reason_text", "keywords": ["convoy", "co-travel", "travel"]}
    )
    assert out["status"] == "ok"
    assert out["co_travel"] == 2          # travelogue is NOT counted


# --------------------------------------------------------------------------- #
# 8. blast-radius  (net width + net-width-by-severity)
# --------------------------------------------------------------------------- #

def test_blast_radius_overall_and_by_severity():
    out = check_blast_radius(
        sample_audit(), {"nets_col": "nets", "severity_col": "base_type"}
    )
    assert out["status"] == "ok"
    assert out["n_with_nets"] == 11
    assert out["median"] == pytest.approx(900.0)
    assert out["max"] == 3000
    assert out["total_exposure"] == 13910
    # the headline inversion: routine Traffic casts a WIDER median net than Homicide.
    assert out["by_severity"]["Traffic"] == pytest.approx(1500.0)
    assert out["by_severity"]["Homicide"] == pytest.approx(150.0)
    assert list(out["by_severity"]) == ["Traffic", "Homicide"]  # sorted high -> low


def test_blast_radius_skipped_without_nets():
    out = check_blast_radius(pd.DataFrame({"x": [1]}), {"nets_col": "nets"})
    assert out["status"] == "skipped"


def test_blast_radius_partial_when_no_values_observed():
    # Column present but all-NA: undefined median/max must be None, NOT a real 0.
    df = pd.DataFrame(
        {
            "nets": pd.array([pd.NA, pd.NA], dtype="Int64"),
            "base_type": ["Traffic", "Homicide"],
        }
    )
    out = check_blast_radius(df, {"nets_col": "nets", "severity_col": "base_type"})
    assert out["status"] == "partial"
    assert out["n_with_nets"] == 0
    assert out["median"] is None
    assert out["max"] is None


# --------------------------------------------------------------------------- #
# 9. mega-users  (concentration: top-N + largest)
# --------------------------------------------------------------------------- #

def test_mega_users_top_and_largest():
    out = check_mega_users(sample_audit(), {"user_col": "agency", "top_frac": 0.01})
    assert out["status"] == "ok"
    assert out["n_users"] == 5
    assert out["largest_user"] == {"user": "Houston TX PD", "count": 5}
    assert out["top_users"][0] == {"user": "Houston TX PD", "count": 5}
    assert len(out["top_users"]) == 5
    # top 1% -> at least one actor -> Houston's 5 of 12.
    assert out["top_frac_share"] == pytest.approx(5 / 12)


def test_mega_users_partial_when_no_users_observed():
    out = check_mega_users(pd.DataFrame({"agency": []}), {"user_col": "agency"})
    assert out["status"] == "partial"
    assert out["n_users"] == 0
    assert out["largest_user"] is None
    assert out["top_frac_share"] is None


# --------------------------------------------------------------------------- #
# 10. operations  (tempo / volume overview)
# --------------------------------------------------------------------------- #

def test_operations_overview():
    out = check_operations(
        sample_audit(),
        {"user_col": "agency", "date_col": "date_et", "hour_col": "hour_et"},
    )
    assert out["status"] == "ok"
    assert out["total_records"] == 12
    assert out["n_users"] == 5
    assert out["date_start"] == "2026-03-01"
    assert out["date_end"] == "2026-03-04"
    assert out["span_days"] == 3
    assert out["n_active_days"] == 4
    assert out["records_per_active_day"] == pytest.approx(12 / 4)
    assert out["busiest_hour"]["hour"] == 2
    assert out["busiest_hour"]["count"] == 2
    # all four days tie at 3 records -> deterministic tie-break is the EARLIEST.
    assert out["busiest_day"] == {"date": "2026-03-01", "count": 3}


# --------------------------------------------------------------------------- #
# 11. AI / moderation  (automation signature + burstiness)
# --------------------------------------------------------------------------- #

def test_ai_moderation_overnight_heavy_actors_and_bursts():
    out = check_ai_moderation(
        sample_audit(),
        {"hour_col": "hour_et", "user_col": "agency", "timestamp_col": "ts"},
    )
    assert out["status"] == "ok"
    assert out["n_actors"] == 5
    # Houston (3/5 overnight) and Atlanta (2/2 overnight) cross the 0.5 threshold.
    assert out["n_overnight_heavy"] == 2
    assert set(out["overnight_heavy_actors"]) == {"Houston TX PD", "Atlanta GA PD"}
    # rows 0,1 share the same second for the same agency -> a batch of 2.
    assert out["max_same_second"] == 2


def test_ai_moderation_honest_lead_no_verdict():
    # Issue #18: the overnight half is a timezone-relative LEAD, not an
    # automation verdict. The result must carry a timezone caveat, and the
    # summary must not render an automation verdict ("automat...") while still
    # reporting the overnight_heavy count.
    out = check_ai_moderation(
        sample_audit(),
        {"hour_col": "hour_et", "user_col": "agency", "timestamp_col": "ts"},
    )
    assert out["overnight_caveat"]  # non-empty
    assert "timezone" in out["overnight_caveat"].lower()
    assert "automat" not in out["summary"].lower()
    assert out["n_overnight_heavy"] == 2


def test_ai_moderation_partial_without_timestamp():
    # The overnight signature still runs, but burstiness is undefined without a
    # second-resolution timestamp -> PARTIAL, with the reason recorded and the
    # undefined sub-metric null (never a fake 0). The caveat is present in this
    # branch too, and the summary still renders no automation verdict.
    out = check_ai_moderation(
        sample_audit(), {"hour_col": "hour_et", "user_col": "agency"}
    )
    assert out["status"] == "partial"
    assert out["n_overnight_heavy"] == 2          # signature half still computed
    assert out["max_same_second"] is None
    assert "timestamp" in out["reason"].lower()
    assert "timezone" in out["overnight_caveat"].lower()
    assert "automat" not in out["summary"].lower()


# --------------------------------------------------------------------------- #
# 12. cross-agency overlap  (per-source EXTERNAL actor set for the rollup)
#
# Consistent actor model (review C1): the check consumes `user_col` and emits
# `actor_counts` (the FULL machine field -- never truncated, so rollup can't
# silently miss a shared actor) plus `external_actor_counts` restricted to actors
# seen on external rows. Rollup keys recurrence off the EXTERNAL counts only, so
# an in-state actor can't manufacture a cross-source recurrence finding.
# --------------------------------------------------------------------------- #

def test_cross_agency_actor_sets():
    out = check_cross_agency(
        sample_audit(),
        {"user_col": "agency", "external_geo_col": "geo", "external_label": "OOS"},
    )
    assert out["status"] == "ok"
    assert out["n_actors"] == 5
    # FULL machine field (not bounded) so a shared actor can't be truncated away.
    assert out["actor_counts"]["Houston TX PD"] == 5
    # external = actors on any OOS row; in-state Local SC + UNK Denver excluded.
    assert out["external_actor_counts"] == {
        "Houston TX PD": 5, "Atlanta GA PD": 2, "Miami FL PD": 1
    }
    assert out["external_actors"] == [
        "Atlanta GA PD", "Houston TX PD", "Miami FL PD"
    ]
    assert out["n_external_actors"] == 3


def test_cross_agency_skipped_without_user_col():
    out = check_cross_agency(pd.DataFrame({"x": [1]}), {"user_col": "agency"})
    assert out["status"] == "skipped"


# --------------------------------------------------------------------------- #
# 13. statistical patterns  (Gini + concentration shares)
# --------------------------------------------------------------------------- #

def test_statistical_patterns_gini_and_shares():
    out = check_statistical_patterns(
        sample_audit(), {"user_col": "agency", "top_frac": 0.01}
    )
    assert out["status"] == "ok"
    assert out["n_actors"] == 5
    # counts [5,3,2,1,1] -> Gini 1/3.
    assert out["gini"] == pytest.approx(1 / 3, abs=1e-9)
    assert out["top_frac_share"] == pytest.approx(5 / 12)
    assert out["bottom_half_share"] == pytest.approx(2 / 12)


def test_statistical_patterns_partial_when_no_actors():
    # No actors -> Gini/shares are UNDEFINED (None), not a misleading real 0.0.
    out = check_statistical_patterns(
        pd.DataFrame({"agency": []}), {"user_col": "agency"}
    )
    assert out["status"] == "partial"
    assert out["n_actors"] == 0
    assert out["gini"] is None
    assert out["top_frac_share"] is None


# --------------------------------------------------------------------------- #
# orchestrator: run_recipe
# --------------------------------------------------------------------------- #

def test_run_recipe_assembles_findings_object():
    config = {
        "source_id": "simpsonville-network-audit",
        "checks": {
            "out_of_state": OOS_CONFIG,
            "mega_users": {"user_col": "agency"},
            "statistical_patterns": {"user_col": "agency"},
        },
    }
    findings = run_recipe(sample_audit(), config)
    assert findings["source_id"] == "simpsonville-network-audit"
    assert findings["n_records"] == 12
    assert set(findings["checks"]) == {
        "out_of_state", "mega_users", "statistical_patterns"
    }
    assert findings["checks"]["out_of_state"]["out_of_state"] == 8
    assert findings["checks"]["mega_users"]["largest_user"]["count"] == 5


def test_run_recipe_unknown_check_raises():
    with pytest.raises(ValueError, match="unknown check"):
        run_recipe(sample_audit(), {"source_id": "x", "checks": {"nope": {}}})


def test_run_recipe_records_skipped_check_without_crashing():
    # cross_agency needs a user_col that isn't present -> skipped, not a crash.
    findings = run_recipe(
        pd.DataFrame({"foo": [1, 2]}),
        {"source_id": "x", "checks": {"cross_agency": {"user_col": "agency"}}},
    )
    assert findings["checks"]["cross_agency"]["status"] == "skipped"


def test_checks_registry_covers_all_thirteen():
    expected = {
        "truncation", "out_of_state", "immigration", "pretext", "pii",
        "accountability", "co_travel", "blast_radius", "mega_users",
        "operations", "ai_moderation", "cross_agency", "statistical_patterns",
    }
    assert set(CHECKS) == expected


# --------------------------------------------------------------------------- #
# impl-review fixes (Codex, 2026-06-04)
# --------------------------------------------------------------------------- #

def test_ai_moderation_partial_on_invalid_timestamps():
    """A present-but-unparseable timestamp_col must NOT manufacture a burst.

    Grouping junk strings as if they were real seconds would fabricate a
    same-second batch (a false automation finding), and an all-invalid column
    must not fall through to a fake max_same_second == 0 with status ok. With no
    VALID second-resolution timestamps, burstiness is undefined -> partial/None.
    """
    df = sample_audit()
    df["ts"] = ["not a timestamp"] * len(df)
    out = check_ai_moderation(
        df, {"hour_col": "hour_et", "user_col": "agency", "timestamp_col": "ts"}
    )
    assert out["status"] == "partial"
    assert out["max_same_second"] is None
    assert "timestamp" in out["reason"].lower()
    # the overnight-signature half still computed.
    assert out["n_overnight_heavy"] == 2


def test_ai_moderation_burstiness_ignores_invalid_timestamp_rows():
    """Junk timestamp rows are dropped; the burst is computed over valid rows only."""
    df = sample_audit()
    ts = df["ts"].tolist()
    ts[2] = "junk"  # corrupt one row that was a singleton second anyway
    df["ts"] = ts
    out = check_ai_moderation(
        df, {"hour_col": "hour_et", "user_col": "agency", "timestamp_col": "ts"}
    )
    assert out["status"] == "ok"
    # rows 0,1 (Houston, same valid second) still form the batch of 2.
    assert out["max_same_second"] == 2


def test_cross_agency_partial_when_external_geo_col_absent():
    """Missing the geo column means external actors are UNCLASSIFIED, not zero.

    Reporting status ok with external_actor_counts == {} would let rollup read
    'cannot classify external' as 'measured no external actors' and silently
    suppress the recurrence thesis. It must be partial so rollup excludes it.
    """
    df = sample_audit().drop(columns=["geo"])
    out = check_cross_agency(df, {"user_col": "agency"})  # external_geo_col defaults to 'geo'
    assert out["status"] == "partial"
    assert out["external_actor_counts"] == {}
    assert "external" in out["reason"].lower()
    # the full actor set is still reported.
    assert out["actor_counts"]["Houston TX PD"] == 5


def test_cross_agency_external_actor_sort_survives_mixed_type_labels():
    """A user column mixing str + numeric labels must not crash the sorted() list."""
    df = pd.DataFrame(
        {"agency": ["Houston", 42, "Houston", 42], "geo": ["OOS"] * 4}
    )
    out = check_cross_agency(
        df, {"user_col": "agency", "external_geo_col": "geo", "external_label": "OOS"}
    )
    assert out["status"] == "ok"
    assert set(out["external_actors"]) == {"Houston", 42}
    assert out["external_actor_counts"][42] == 2

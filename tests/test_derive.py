"""TDD for ``scripts/derive.py`` -- the config-driven derived-column transform.

All fixtures are SYNTHETIC and inline (no real corpus is read). The suite pins
the rigor guardrails the published findings depend on:

* the column is named ``geo``, never ``loc`` (pandas ``.loc`` collision);
* keyword categorization is WORD-BOUNDARY matched, so the canonical trap -- a
  reason containing "police" (or "service" / "justice") flagged immigration
  merely because it contains the substring "ice" -- MUST fail to match;
* a redaction sentinel like ``***`` counts as PRESENT for ``has_case`` while a
  blank / ``NaN`` / ``""`` counts as ABSENT (presence is not value);
* timezone conversion is correct across DST (a July and a January UTC instant
  land at the right ET wall-clock hour);
* ``derive_columns`` never mutates the input frame.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.derive import (
    derive_base_type,
    derive_columns,
    derive_geo,
    derive_has_case,
    derive_immigration,
    derive_nets,
    derive_reason,
    derive_temporal_et,
)


# --------------------------------------------------------------------------- #
# geo
# --------------------------------------------------------------------------- #

GEO_CONFIG = {
    "source_col": "agency_state",
    "home_value": "SC",
    "in_label": "SC",
    "out_label": "OOS",
    "unknown_label": "UNK",
}


def test_geo_classifies_home_away_blank():
    df = pd.DataFrame(
        {"agency_state": ["SC", "NC", "GA", "", None, "sc", "  SC  "]}
    )
    out = derive_geo(df, GEO_CONFIG)
    # home (incl. case/whitespace variants) -> SC; other non-blank -> OOS;
    # blank / null -> UNK.
    assert list(out) == ["SC", "OOS", "OOS", "UNK", "UNK", "SC", "SC"]


def test_geo_column_is_named_geo_not_loc():
    """The derived column MUST be ``geo`` -- ``loc`` collides with df.loc."""
    df = pd.DataFrame({"agency_state": ["SC", "NC"]})
    result = derive_columns(df, {"geo": GEO_CONFIG})
    assert "geo" in result.columns
    assert "loc" not in result.columns
    # And df.loc is still the indexer, not a data column we shadowed.
    assert callable(getattr(result.loc, "__getitem__", None))


def test_geo_missing_source_value_is_unknown():
    df = pd.DataFrame({"agency_state": [np.nan, "SC", "   "]})
    out = derive_geo(df, GEO_CONFIG)
    assert list(out) == ["UNK", "SC", "UNK"]


# --------------------------------------------------------------------------- #
# reason_cat / reason_text  +  is_immigration  -- the WORD-BOUNDARY guardrail
# --------------------------------------------------------------------------- #

REASON_CONFIG = {
    "source_col": "reason",
    "keyword_map": {
        "Immigration": ["ice", "immigration", "detainer", "deportation"],
        "Traffic": ["traffic", "speeding", "dui"],
        "Violent": ["homicide", "assault", "robbery"],
    },
    "default": "Other",
}

IMMIGRATION_CONFIG = {
    "source_col": "reason",
    "keywords": ["ice", "immigration", "detainer", "deportation", "cbp"],
}


def test_reason_text_is_normalized_readable():
    df = pd.DataFrame({"reason": ["  Assist Local Police  ", "ICE Detainer"]})
    out = derive_reason(df, REASON_CONFIG)
    # reason_text keeps a readable (stripped) form, not the lowercase match form.
    assert out["reason_text"].tolist() == ["Assist Local Police", "ICE Detainer"]


def test_reason_cat_assigns_first_matching_category():
    df = pd.DataFrame(
        {"reason": ["Traffic stop for speeding", "Homicide investigation", "ICE hold"]}
    )
    out = derive_reason(df, REASON_CONFIG)
    assert out["reason_cat"].tolist() == ["Traffic", "Violent", "Immigration"]


def test_reason_cat_default_when_no_keyword():
    df = pd.DataFrame({"reason": ["Welfare check", ""]})
    out = derive_reason(df, REASON_CONFIG)
    assert out["reason_cat"].tolist() == ["Other", "Other"]


def test_immigration_guardrail_police_is_not_immigration():
    """THE canonical trap: 'police' contains 'ice' but is NOT immigration.

    A naive substring match would flag "Assist local police" as immigration
    because "polICE" ends in the substring "ice". Word-boundary matching MUST
    reject it. Same for "service" and "justice".
    """
    df = pd.DataFrame(
        {
            "reason": [
                "Assist local police",
                "Community service detail",
                "Department of Justice liaison",
                "Officer needs assistance",
            ]
        }
    )
    flags = derive_immigration(df, IMMIGRATION_CONFIG)
    assert flags.tolist() == [False, False, False, False]
    # And the categorizer must not mislabel them Immigration either.
    cats = derive_reason(df, REASON_CONFIG)["reason_cat"]
    assert "Immigration" not in cats.tolist()


def test_immigration_genuine_hit_is_flagged():
    df = pd.DataFrame(
        {
            "reason": [
                "ICE detainer / immigration hold",
                "CBP referral",
                "Deportation order follow-up",
                "Routine patrol",
            ]
        }
    )
    flags = derive_immigration(df, IMMIGRATION_CONFIG)
    assert flags.tolist() == [True, True, True, False]
    cats = derive_reason(df, REASON_CONFIG)["reason_cat"]
    assert cats.iloc[0] == "Immigration"


def test_immigration_is_case_insensitive_and_handles_null():
    df = pd.DataFrame({"reason": ["ICE DETAINER", "ice hold", None, "", np.nan]})
    flags = derive_immigration(df, IMMIGRATION_CONFIG)
    assert flags.tolist() == [True, True, False, False, False]


def test_immigration_keyword_at_string_boundaries():
    """A keyword that sits at the very start/end of the text still matches."""
    df = pd.DataFrame({"reason": ["ice", "immigration", "see ice"]})
    flags = derive_immigration(df, IMMIGRATION_CONFIG)
    assert flags.tolist() == [True, True, True]


# --------------------------------------------------------------------------- #
# nets  (numeric blast-radius)
# --------------------------------------------------------------------------- #

NETS_CONFIG = {"source_col": "net_width"}


def test_nets_parses_numeric_and_nas_non_numeric():
    df = pd.DataFrame({"net_width": ["5", "12", "abc", "", None, "7"]})
    out = derive_nets(df, NETS_CONFIG)
    assert out.iloc[0] == 5
    assert out.iloc[1] == 12
    assert pd.isna(out.iloc[2])  # non-numeric -> NA
    assert pd.isna(out.iloc[3])  # "" -> NA
    assert pd.isna(out.iloc[4])  # None -> NA
    assert out.iloc[5] == 7
    # nullable integer dtype (so NA can coexist with ints)
    assert str(out.dtype) == "Int64"


def test_nets_already_numeric():
    df = pd.DataFrame({"net_width": [3, 9, 0]})
    out = derive_nets(df, NETS_CONFIG)
    assert out.tolist() == [3, 9, 0]


# --------------------------------------------------------------------------- #
# has_case  (presence, with *** == present)
# --------------------------------------------------------------------------- #

HAS_CASE_CONFIG = {"source_col": "case_number", "redaction_sentinels": ["***"]}


def test_has_case_presence_semantics():
    df = pd.DataFrame(
        {"case_number": ["CASE-1", "***", "", None, np.nan, "   ", "2024-99"]}
    )
    out = derive_has_case(df, HAS_CASE_CONFIG)
    # value present -> True; *** (redacted but PRESENT) -> True;
    # blank / None / NaN / whitespace -> False (ABSENT).
    assert out.tolist() == [True, True, False, False, False, False, True]


def test_has_case_redaction_is_present_not_blank():
    """The ***-vs-blank guardrail in isolation: *** is PRESENT, '' is ABSENT."""
    df = pd.DataFrame({"case_number": ["***", ""]})
    out = derive_has_case(df, HAS_CASE_CONFIG)
    assert out.tolist() == [True, False]


def test_has_case_custom_sentinels():
    df = pd.DataFrame({"case_number": ["[REDACTED]", "REAL-1", ""]})
    cfg = {"source_col": "case_number", "redaction_sentinels": ["[REDACTED]"]}
    out = derive_has_case(df, cfg)
    assert out.tolist() == [True, True, False]


# --------------------------------------------------------------------------- #
# base_type  (mapping + default passthrough)
# --------------------------------------------------------------------------- #

BASE_TYPE_CONFIG = {
    "source_col": "raw_type",
    "mapping": {
        "VEHICLE_PLATE": "Vehicle",
        "VEHICLE_VIN": "Vehicle",
        "PERSON_FACE": "Person",
    },
    "default": "Unknown",
}


def test_base_type_maps_and_collapses():
    df = pd.DataFrame(
        {"raw_type": ["VEHICLE_PLATE", "VEHICLE_VIN", "PERSON_FACE", "WIDGET", None]}
    )
    out = derive_base_type(df, BASE_TYPE_CONFIG)
    # mapped values collapse to base categories; unmapped -> default.
    assert out.tolist() == ["Vehicle", "Vehicle", "Person", "Unknown", "Unknown"]


def test_base_type_passthrough_default():
    df = pd.DataFrame({"raw_type": ["X"]})
    cfg = {"source_col": "raw_type", "mapping": {}, "default": "passthrough"}
    # default "passthrough" sentinel keeps the original value
    out = derive_base_type(df, {**cfg, "default": None})
    assert out.tolist() == ["X"]  # default None -> keep raw value


# --------------------------------------------------------------------------- #
# temporal  (tz convert across DST)
# --------------------------------------------------------------------------- #

TEMPORAL_CONFIG = {
    "source_col": "ts_utc",
    "source_tz": "UTC",
    "target_tz": "America/New_York",
}


def test_temporal_dst_vs_standard_offsets():
    """A July UTC 18:00 -> 14:00 EDT; a January UTC 18:00 -> 13:00 EST.

    DST correctness is the rigor point: ET is UTC-4 in July (EDT) and UTC-5 in
    January (EST). pandas tz_convert must apply the right offset per-date.
    """
    df = pd.DataFrame(
        {"ts_utc": ["2024-07-15 18:00:00", "2024-01-15 18:00:00"]}
    )
    out = derive_temporal_et(df, TEMPORAL_CONFIG)
    assert out["hour_et"].tolist() == [14, 13]
    assert out["date_et"].tolist() == [
        pd.Timestamp("2024-07-15").date(),
        pd.Timestamp("2024-01-15").date(),
    ]


def test_temporal_dow():
    # 2024-07-15 is a Monday; 2024-01-15 is a Monday too. Use a Wednesday to vary.
    df = pd.DataFrame({"ts_utc": ["2024-07-17 12:00:00"]})  # Wed 08:00 ET
    out = derive_temporal_et(df, TEMPORAL_CONFIG)
    assert out["hour_et"].tolist() == [8]
    # dow_et: Monday=0 .. Sunday=6 (pandas convention). 2024-07-17 is Wednesday.
    assert out["dow_et"].tolist() == [2]


def test_temporal_date_rolls_back_across_midnight():
    """An early-UTC instant can fall on the PREVIOUS ET calendar day."""
    # 2024-07-15 02:00 UTC -> 2024-07-14 22:00 EDT (date rolls back a day).
    df = pd.DataFrame({"ts_utc": ["2024-07-15 02:00:00"]})
    out = derive_temporal_et(df, TEMPORAL_CONFIG)
    assert out["date_et"].tolist() == [pd.Timestamp("2024-07-14").date()]
    assert out["hour_et"].tolist() == [22]


def test_temporal_unparseable_is_nat_na_no_crash():
    df = pd.DataFrame({"ts_utc": ["2024-07-15 18:00:00", "not a date", "", None]})
    out = derive_temporal_et(df, TEMPORAL_CONFIG)
    assert out["hour_et"].iloc[0] == 14
    # unparseable rows -> NA hour, NaT/None date, NA dow (no exception)
    assert pd.isna(out["hour_et"].iloc[1])
    assert out["date_et"].iloc[1] is None or pd.isna(out["date_et"].iloc[1])
    assert pd.isna(out["dow_et"].iloc[1])
    assert pd.isna(out["hour_et"].iloc[2])
    assert pd.isna(out["hour_et"].iloc[3])


def test_temporal_already_tzaware_source():
    """If the source already carries an offset, it is honored (not double-localized)."""
    df = pd.DataFrame({"ts_utc": ["2024-07-15T18:00:00+00:00"]})
    out = derive_temporal_et(df, TEMPORAL_CONFIG)
    assert out["hour_et"].tolist() == [14]


def test_temporal_mixed_naive_and_aware_no_crash():
    """IMPORTANT 4: a column MIXING naive and offset-bearing strings (plus junk)
    must not raise.

    A bare ``pd.to_datetime(..., format='mixed')`` yields an OBJECT series (or,
    in pandas 3, raises "Mixed timezones detected") on such input, and the
    subsequent ``.dt`` then blows up -- contradicting the unparseable -> NaT
    promise. With source_tz=UTC both the naive "18:00" and the aware "18:00+00:00"
    denote the same instant, so both land at 14:00 EDT; the junk row is NaT/NA.
    """
    df = pd.DataFrame(
        {
            "ts_utc": [
                "2026-07-01 18:00:00",          # naive
                "2026-07-01T18:00:00+00:00",    # offset-bearing (UTC)
                "not a date",                   # junk
            ]
        }
    )
    out = derive_temporal_et(df, TEMPORAL_CONFIG)  # must NOT raise
    # Both parseable rows -> 14:00 ET (UTC source: naive and +00:00 coincide).
    assert out["hour_et"].iloc[0] == 14
    assert out["hour_et"].iloc[1] == 14
    assert out["date_et"].iloc[0] == pd.Timestamp("2026-07-01").date()
    assert out["date_et"].iloc[1] == pd.Timestamp("2026-07-01").date()
    assert out["dow_et"].iloc[0] == 2  # 2026-07-01 is a Wednesday
    # Junk row -> NA / None throughout, never an exception.
    assert pd.isna(out["hour_et"].iloc[2])
    assert out["date_et"].iloc[2] is None or pd.isna(out["date_et"].iloc[2])
    assert pd.isna(out["dow_et"].iloc[2])


def test_temporal_mixed_offsets_no_crash():
    """Mixed OFFSETS (not just naive+aware) also stay total and per-row correct.

    +00:00 and +05:00 denote different instants; each must convert independently
    rather than tripping the "mixed timezones" parse error.
    """
    df = pd.DataFrame(
        {
            "ts_utc": [
                "2026-07-01T18:00:00+00:00",  # -> 14:00 EDT
                "2026-07-01T18:00:00+05:00",  # 13:00 UTC -> 09:00 EDT
            ]
        }
    )
    out = derive_temporal_et(df, TEMPORAL_CONFIG)
    assert out["hour_et"].tolist() == [14, 9]


def test_temporal_mixed_naive_aware_non_utc_source():
    """A non-UTC source_tz: the naive row is read as source_tz wall-clock, the
    aware row keeps its absolute instant.

    Naive "18:00" in America/Chicago (CDT, UTC-5) is 23:00 UTC -> 19:00 New_York;
    the aware "18:00+00:00" is 14:00 New_York. This pins that naive members are
    localized to source_tz (not silently treated as UTC).
    """
    cfg = {
        "source_col": "ts",
        "source_tz": "America/Chicago",
        "target_tz": "America/New_York",
    }
    df = pd.DataFrame(
        {"ts": ["2026-07-01 18:00:00", "2026-07-01T18:00:00+00:00"]}
    )
    out = derive_temporal_et(df, cfg)
    assert out["hour_et"].tolist() == [19, 14]


# --------------------------------------------------------------------------- #
# derive_columns -- end-to-end, config-driven, no mutation
# --------------------------------------------------------------------------- #

FULL_CONFIG = {
    "geo": GEO_CONFIG,
    "reason": REASON_CONFIG,
    "immigration": IMMIGRATION_CONFIG,
    "nets": NETS_CONFIG,
    "has_case": HAS_CASE_CONFIG,
    "base_type": BASE_TYPE_CONFIG,
    "temporal": TEMPORAL_CONFIG,
}


def _full_df():
    return pd.DataFrame(
        {
            "agency_state": ["SC", "NC", ""],
            "reason": ["Assist local police", "ICE detainer", "Traffic stop"],
            "net_width": ["5", "abc", "12"],
            "case_number": ["CASE-1", "***", ""],
            "raw_type": ["VEHICLE_PLATE", "PERSON_FACE", "WIDGET"],
            "ts_utc": [
                "2024-07-15 18:00:00",
                "2024-01-15 18:00:00",
                "not a date",
            ],
        }
    )


def test_derive_columns_runs_full_config():
    df = _full_df()
    out = derive_columns(df, FULL_CONFIG)

    # geo
    assert out["geo"].tolist() == ["SC", "OOS", "UNK"]
    # reason
    assert out["reason_cat"].tolist() == ["Other", "Immigration", "Traffic"]
    assert out["reason_text"].iloc[0] == "Assist local police"
    # immigration guardrail end-to-end: 'police' row is NOT immigration
    assert out["is_immigration"].tolist() == [False, True, False]
    # nets
    assert out["nets"].iloc[0] == 5
    assert pd.isna(out["nets"].iloc[1])
    # has_case: *** is present
    assert out["has_case"].tolist() == [True, True, False]
    # base_type
    assert out["base_type"].tolist() == ["Vehicle", "Person", "Unknown"]
    # temporal
    assert out["hour_et"].tolist()[:2] == [14, 13]
    assert pd.isna(out["hour_et"].iloc[2])


def test_derive_columns_does_not_mutate_input():
    df = _full_df()
    before_cols = list(df.columns)
    before_copy = df.copy(deep=True)

    out = derive_columns(df, FULL_CONFIG)

    # input columns unchanged (no derived columns leaked back onto df)
    assert list(df.columns) == before_cols
    pd.testing.assert_frame_equal(df, before_copy)
    # and the result is a different object with MORE columns
    assert out is not df
    assert set(before_cols).issubset(set(out.columns))
    assert "geo" in out.columns


def test_derive_columns_skips_absent_derivations():
    """A derivation not present in config is simply not run."""
    df = _full_df()
    out = derive_columns(df, {"geo": GEO_CONFIG})
    assert "geo" in out.columns
    # none of the other derived columns were produced
    for missing in ["reason_cat", "is_immigration", "nets", "has_case", "base_type", "hour_et"]:
        assert missing not in out.columns


def test_derive_columns_empty_config_returns_copy():
    df = _full_df()
    out = derive_columns(df, {})
    pd.testing.assert_frame_equal(out, df)
    assert out is not df


def test_derive_columns_unknown_key_raises():
    """An unrecognized derivation key is a config error, surfaced loudly."""
    df = _full_df()
    with pytest.raises(ValueError, match="unknown derivation"):
        derive_columns(df, {"bogus": {}})

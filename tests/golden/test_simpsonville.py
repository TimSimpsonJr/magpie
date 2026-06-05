import os
from pathlib import Path

import pytest

_CORPUS = os.environ.get("MAGPIE_SIMPSONVILLE_CORPUS")

pytestmark = pytest.mark.skipif(
    not _CORPUS, reason="MAGPIE_SIMPSONVILLE_CORPUS not set (private corpus, env-gated)"
)

# --- MAIN-THREAD-PIN: replace each None with the exact value computed on the real
# --- corpus, with a comment tying it to the documented pilot figure. NO '~' here.
EXPECTED_ROWS = 1_048_575                      # documented truncation ceiling 2^20-1
EXPECTED_OOS_COUNT = None                      # rounds to documented 89.7%
EXPECTED_IMMIGRATION = None                    # documented ~770 (criminal OR civil)
EXPECTED_PRETEXT_MIN = 175                     # documented floor >= 175
EXPECTED_GINI_3DP = 0.805                      # documented Gini
EXPECTED_TRAFFIC_MEDIAN = None                 # documented ~2943
EXPECTED_HOMICIDE_MEDIAN = None                # documented ~1792
EXPECTED_PII_AGENCIES = 747                    # documented distinct agencies (exact)
EXPECTED_PII_LOW = None                        # tolerance band low  (documented ~11900)
EXPECTED_PII_HIGH = None                       # tolerance band high
PRETEXT_CATS = []                              # MAIN-THREAD-PIN the Flock pretext set


def _network_frame():
    import pandas as pd
    from tests.golden._adapters import extract_state, reason_category
    # the network audit CSV in the corpus folder (the ~1M-row file)
    path = next(Path(_CORPUS).glob("*Network-Audit.csv"))
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    df.columns = [c.strip() for c in df.columns]
    df["state"] = df["Org Name"].map(extract_state)
    df["reason_cat"] = df["Reason"].map(reason_category)
    return df


def test_truncation_at_ceiling():
    from scripts.data_quality import check_truncation
    df = _network_frame()
    assert len(df) == EXPECTED_ROWS
    assert check_truncation(df)["truncated"] is True


def test_out_of_state_share():
    from scripts.derive import derive_columns
    from scripts.stats import category_pct
    df = derive_columns(_network_frame(), {
        "geo": {"source_col": "state", "home_value": "SC", "in_label": "SC",
                "out_label": "OOS", "unknown_label": "UNK"}})
    oos = int((df["geo"] == "OOS").sum())
    assert oos == EXPECTED_OOS_COUNT
    assert round(category_pct(df, df["geo"] == "OOS") * 100, 1) == 89.7


def test_blast_radius_traffic_over_homicide():
    from scripts.derive import derive_columns
    from scripts.stats import median_by_category
    df = derive_columns(_network_frame(), {
        "nets": {"source_col": "Total Networks Searched"}})
    med = median_by_category(df, "nets", "reason_cat")  # pandas.Series, DESC by median
    assert med.loc["Traffic Infraction"] > med.loc["Homicide/Death Investigation"]
    assert int(med.loc["Traffic Infraction"]) == EXPECTED_TRAFFIC_MEDIAN
    assert int(med.loc["Homicide/Death Investigation"]) == EXPECTED_HOMICIDE_MEDIAN

# immigration / pretext / gini: same shape -- use derive_columns + the stats/recipe
# APIs from the block above; pin each constant MAIN-THREAD. gini:
# stats.gini(df["Org Name"].value_counts().values) -> round(.,3)==0.805. pretext:
# recipe.check_pretext(df, {"cat_col": "reason_cat", "pretext_cats": PRETEXT_CATS})
# ["pretext"] >= EXPECTED_PRETEXT_MIN.


@pytest.mark.spacy
def test_pii_exposure_and_agency_count():
    from scripts.pii_sweep import sweep
    df = _network_frame()
    # COMPATIBILITY PROFILE: broad_only_names=frozenset() folds the broad-only
    # patterns (possible_birthdate/race_sex) INTO the headline, reproducing the
    # documented pilot figure. The headline metric is exposure.strict.weighted
    # (row-weighted: NER runs over distinct texts, then weights by row counts).
    res = sweep(df["Reason"], broad_only_names=frozenset(), collect_local_texts=False)
    weighted = res["exposure"]["strict"]["weighted"]
    assert EXPECTED_PII_LOW <= weighted <= EXPECTED_PII_HIGH   # documented ~11,900 (band)
    # MAIN-THREAD-PIN: confirm whether 747 is total distinct agencies or PII-bearing.
    agencies = df["Org Name"].str.strip().nunique()
    assert agencies == EXPECTED_PII_AGENCIES                   # documented 747


@pytest.mark.docling
def test_citation_anchor_revalidation_issue_6():
    # reuse MAGPIE_PHASE8_REAL_PDF (skip if unset); assert anchors resolve clean
    # (exact/relocated, no false exact) on the real Greenville RFP (Phase-8 Tier-2b).
    pdf = os.environ.get("MAGPIE_PHASE8_REAL_PDF")
    if not pdf:
        pytest.skip("MAGPIE_PHASE8_REAL_PDF not set")
    ...

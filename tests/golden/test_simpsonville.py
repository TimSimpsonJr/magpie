"""Golden tests pinning magpie's engine to the documented Simpsonville pilot values,
over the REAL private corpus (env-gated on MAGPIE_SIMPSONVILLE_CORPUS, never
committed). Each constant is the EXACT deterministic engine output on the fixed
corpus, annotated with the documented pilot figure it reproduces. The spaCy PII tally
(the one genuinely model-sensitive value) uses an explicit tolerance band; everything
else is exact.

Issue #6 (citation-anchor re-validation) is covered by the existing docling-marked
Tier-2b test in tests/test_citation_docling.py (gated on MAGPIE_PHASE8_REAL_PDF) --
it is not duplicated here.
"""
import os
from pathlib import Path

import pytest

_CORPUS = os.environ.get("MAGPIE_SIMPSONVILLE_CORPUS")

pytestmark = pytest.mark.skipif(
    not _CORPUS, reason="MAGPIE_SIMPSONVILLE_CORPUS not set (private corpus, env-gated)"
)

EXPECTED_ROWS = 1_048_575           # documented truncation ceiling 2^20 - 1
EXPECTED_OOS_COUNT = 940_551        # 89.7% of rows (documented out-of-state ~89.7%)
EXPECTED_IMMIGRATION = 770          # criminal (614) OR civil (158), deduped; documented ~770
EXPECTED_IMMIGRATION_KEYWORD = 774  # magpie derive_immigration keyword 'immigration' (+4)
EXPECTED_GINI = 0.8055              # documented Gini ~0.805 (exact to 4 dp)
EXPECTED_TRAFFIC_MEDIAN = 2943      # documented Traffic net median ~2943
EXPECTED_HOMICIDE_MEDIAN = 1792     # documented Homicide net median ~1792
EXPECTED_PRETEXT = 85               # see the PRETEXT note below

PRETEXT_KEYWORDS = [
    "pretext", "parallel construction", "walled off", "wall off",
    "own pc", "own probable cause", "find pc", "find your pc", "find your own pc",
    "no pc to stop", "no pc for stop", "develop pc", "develop probable cause",
    "developing pc", "establish pc", "create pc", "pc to stop", "reason to stop",
    "reasonable suspicion", "pc permitting", "own legal",
]
# PRETEXT: the documented pilot figure (>=175) came from a RICHER free-text regex (the
# pilot's analysis6.py broad+extra over PC-development / reason-to-stop language) that
# magpie's GENERIC word-boundary keyword primitive (check_pretext + keyword_mask) does
# not replicate. 85 is the deterministic characterization of check_pretext over the
# keyword set above on the real corpus; it still clears the pilot's narrow
# PC-development floor (57).
#
# PII: magpie's refined exposure.broad EXCLUDES officials (a Phase-5 refinement over
# the pilot's any-PERSON union). On the network audit the only officials are rank/title
# prefixes in the reason text (257 exposures), so magpie's broad lands at 11,720 / 742
# -- just under the documented ~11,900 / 747. A tolerance band absorbs cross-platform
# spaCy-NER variance (the en_core_web_lg model version is pinned in requirements).
EXPECTED_PII_BROAD_LOW, EXPECTED_PII_BROAD_HIGH = 11_400, 12_050
EXPECTED_PII_AGENCIES_LOW, EXPECTED_PII_AGENCIES_HIGH = 728, 756


_FRAME = None


def _network_frame():
    # Load + adapt the ~1M-row network audit ONCE per session (each test derives its
    # own copy via derive_columns, so the cached frame is never mutated in place).
    global _FRAME
    if _FRAME is None:
        import pandas as pd
        from tests.golden._adapters import extract_state, reason_category, reason_text
        path = next(Path(_CORPUS).glob("*Network-Audit.csv"))
        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
        df.columns = [c.strip() for c in df.columns]
        df["state"] = df["Org Name"].map(extract_state)
        df["reason_cat"] = df["Reason"].map(reason_category)
        df["reason_text"] = df["Reason"].map(reason_text)
        _FRAME = df
    return _FRAME


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
    assert int((df["geo"] == "OOS").sum()) == EXPECTED_OOS_COUNT
    assert round(category_pct(df, df["geo"] == "OOS") * 100, 1) == 89.7


def test_immigration_count():
    from scripts.derive import derive_columns
    df = _network_frame()
    catl = df["reason_cat"].str.lower()
    crim = catl.str.contains(r"immigration \(criminal\)", regex=True)
    civ = catl.str.contains(r"immigration \(civil/administrative\)", regex=True)
    assert int((crim | civ).sum()) == EXPECTED_IMMIGRATION
    dfi = derive_columns(df, {"immigration": {"source_col": "Reason",
            "keywords": ["immigration"]}})
    assert int(dfi["is_immigration"].sum()) == EXPECTED_IMMIGRATION_KEYWORD


def test_pretext_keyword_count():
    from scripts.recipe import check_pretext
    df = _network_frame()
    res = check_pretext(df, {"text_col": "Reason", "keywords": PRETEXT_KEYWORDS})
    assert res["pretext"] == EXPECTED_PRETEXT
    assert res["pretext"] >= 57  # clears the pilot's narrow PC-development floor


def test_gini_of_search_volume():
    from scripts.stats import gini
    df = _network_frame()
    counts = df["Org Name"].str.strip().value_counts().values
    assert round(gini(counts), 4) == EXPECTED_GINI


def test_blast_radius_traffic_over_homicide():
    from scripts.derive import derive_columns
    from scripts.stats import median_by_category
    df = derive_columns(_network_frame(), {
        "nets": {"source_col": "Total Networks Searched"}})
    med = median_by_category(df, "nets", "reason_cat")  # pandas.Series, DESC by median
    assert med.loc["Traffic Infraction"] > med.loc["Homicide/Death Investigation"]
    assert int(med.loc["Traffic Infraction"]) == EXPECTED_TRAFFIC_MEDIAN
    assert int(med.loc["Homicide/Death Investigation"]) == EXPECTED_HOMICIDE_MEDIAN


@pytest.mark.spacy
def test_pii_exposure_and_agency_count():
    from scripts.pii_sweep import sweep
    df = _network_frame()
    # magpie's refined exposure.broad (officials excluded) over the after-dash reason
    # text -- the person-inclusive analog of the documented ~11,900. broad_only=
    # frozenset() folds the broad-only patterns into the tally (the pilot's profile).
    res = sweep(df["reason_text"], broad_only_names=frozenset(), collect_local_texts=True)
    weighted = res["exposure"]["broad"]["weighted"]
    assert EXPECTED_PII_BROAD_LOW <= weighted <= EXPECTED_PII_BROAD_HIGH
    flagged = set(v["text"] for v in res["local_texts"].values())
    mask = df["reason_text"].str.strip().isin(flagged)
    agencies = int(df.loc[mask, "Org Name"].str.strip().nunique())
    assert EXPECTED_PII_AGENCIES_LOW <= agencies <= EXPECTED_PII_AGENCIES_HIGH

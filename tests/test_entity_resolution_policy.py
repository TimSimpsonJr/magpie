"""Golden tests for scripts.entity_resolution_policy (Phase 13a Task 1).

ASCII only. Windows-safe (no infra, no nomenklatura, no neo4j). Pins the
deterministic policy + id layer: canonical_id, edge_id, bucket boundaries,
and the Candidate/CandidateSide/Mention/Verdict dataclasses.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import subprocess
import sys

import pytest

from scripts.entity_extract import stable_id
from scripts.entity_resolution_policy import (
    VALID_VERDICTS,
    Candidate,
    CandidateSide,
    Mention,
    ResolutionConfig,
    Verdict,
    bucket,
    canonical_id,
    edge_id,
)

HEX40 = re.compile(r"^[0-9a-f]{40}$")


# ---------------------------------------------------------------------------
# ResolutionConfig
# ---------------------------------------------------------------------------

def test_resolution_config_defaults():
    config = ResolutionConfig()
    assert config.algorithm == "logic-v2"
    assert config.auto_threshold == 0.98
    assert config.review_floor == 0.70


def test_resolution_config_overridable():
    config = ResolutionConfig(
        algorithm="regression-v1", auto_threshold=0.95, review_floor=0.60
    )
    assert config.algorithm == "regression-v1"
    assert config.auto_threshold == 0.95
    assert config.review_floor == 0.60


def test_resolution_config_is_frozen():
    config = ResolutionConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.auto_threshold = 0.5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# canonical_id
# ---------------------------------------------------------------------------

def test_canonical_id_is_40_lowercase_hex():
    result = canonical_id(["alpha", "beta"])
    assert HEX40.match(result), result


def test_canonical_id_deterministic_across_calls():
    members = ["m1", "m2", "m3"]
    assert canonical_id(members) == canonical_id(members)


def test_canonical_id_order_independent():
    assert canonical_id(["a", "b"]) == canonical_id(["b", "a"])
    assert canonical_id(["a", "b", "c"]) == canonical_id(["c", "a", "b"])


def test_canonical_id_dedup_safe():
    assert canonical_id(["a", "a", "b"]) == canonical_id(["a", "b"])
    assert canonical_id(["x", "x", "x"]) == canonical_id(["x"])


def test_canonical_id_singleton_equals_stable_id_of_member():
    assert canonical_id(["x"]) == stable_id("x")
    # Hard golden: pins the exact sha256[:40] so a change in the underlying
    # stable_id algorithm (delimiter or hash) fails loudly here.
    assert canonical_id(["x"]) == "2d711642b726b04401627ca9fbac32f5c8530fb1"


def test_canonical_id_distinct_member_sets_differ():
    assert canonical_id(["a", "b"]) != canonical_id(["a", "c"])
    assert canonical_id(["a"]) != canonical_id(["b"])
    # A superset is a genuinely different cluster -> different id.
    assert canonical_id(["a", "b"]) != canonical_id(["a", "b", "c"])


def test_canonical_id_empty_raises_value_error():
    with pytest.raises(ValueError):
        canonical_id([])


# ---------------------------------------------------------------------------
# edge_id
# ---------------------------------------------------------------------------

def test_edge_id_is_40_lowercase_hex():
    result = edge_id("Directorship", "head", "tail", "director")
    assert HEX40.match(result), result


def test_edge_id_stable():
    args = ("Directorship", "headcanon", "tailcanon", "director")
    assert edge_id(*args) == edge_id(*args)


def test_edge_id_matches_stable_id_formula():
    assert edge_id("S", "H", "T", "r") == stable_id("S", "H", "T", "r")


def test_edge_id_role_none_equals_empty_string():
    assert edge_id("S", "H", "T", None) == edge_id("S", "H", "T", "")


def test_edge_id_changes_with_each_field():
    base = edge_id("S", "H", "T", "r")
    assert edge_id("S2", "H", "T", "r") != base
    assert edge_id("S", "H2", "T", "r") != base
    assert edge_id("S", "H", "T2", "r") != base
    assert edge_id("S", "H", "T", "r2") != base


# ---------------------------------------------------------------------------
# bucket
# ---------------------------------------------------------------------------

def test_bucket_boundaries_default_config():
    config = ResolutionConfig()
    assert bucket(1.0, config) == "auto"
    assert bucket(0.98, config) == "auto"
    assert bucket(0.9799, config) == "review"
    assert bucket(0.85, config) == "review"
    assert bucket(0.70, config) == "review"
    assert bucket(0.6999, config) == "distinct"
    assert bucket(0.0, config) == "distinct"


def test_bucket_inclusive_at_both_thresholds():
    config = ResolutionConfig()
    # Exactly the auto threshold is auto.
    assert bucket(config.auto_threshold, config) == "auto"
    # Exactly the review floor is review.
    assert bucket(config.review_floor, config) == "review"


def test_bucket_config_driven_with_custom_thresholds():
    config = ResolutionConfig(auto_threshold=0.90, review_floor=0.50)
    assert bucket(0.90, config) == "auto"
    assert bucket(0.8999, config) == "review"
    assert bucket(0.50, config) == "review"
    assert bucket(0.4999, config) == "distinct"
    # A score that is "auto" under defaults can be merely "review" under a
    # higher floor, proving the result is genuinely config-driven.
    strict = ResolutionConfig(auto_threshold=0.99, review_floor=0.70)
    assert bucket(0.98, strict) == "review"


def test_bucket_rejects_nan_score():
    # A NaN score is a pipeline bug; bucket must raise, not silently return
    # "distinct" (nan >= x is always False).
    config = ResolutionConfig()
    with pytest.raises(ValueError):
        bucket(float("nan"), config)


# ---------------------------------------------------------------------------
# Mention / CandidateSide / Candidate
# ---------------------------------------------------------------------------

def test_mention_fields_and_defaults():
    mention = Mention(doc_id="doc1", page=3, char_start=10, char_end=20)
    assert mention.doc_id == "doc1"
    assert mention.page == 3
    assert mention.char_start == 10
    assert mention.char_end == 20
    assert mention.text == ""
    with_text = Mention(
        doc_id="doc1", page=3, char_start=10, char_end=20, text="John Smith"
    )
    assert with_text.text == "John Smith"


def test_mention_is_frozen():
    mention = Mention(doc_id="doc1", page=1, char_start=0, char_end=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        mention.page = 99  # type: ignore[misc]


def test_candidate_side_defaults():
    side = CandidateSide(id="n1", caption="John Smith", schema="Person")
    assert side.id == "n1"
    assert side.caption == "John Smith"
    assert side.schema == "Person"
    assert side.aliases == []
    assert side.properties == {}
    assert side.mentions == []


def test_candidate_side_defaults_are_not_shared():
    a = CandidateSide(id="a", caption="A", schema="Person")
    b = CandidateSide(id="b", caption="B", schema="Person")
    a.aliases.append("Johnny")
    a.properties.setdefault("dob", []).append("1980-01-01")
    a.mentions.append(Mention(doc_id="d", page=1, char_start=0, char_end=1))
    assert b.aliases == []
    assert b.properties == {}
    assert b.mentions == []


def test_candidate_full_construction_and_field_access():
    left_mentions = [
        Mention(doc_id="d1", page=1, char_start=0, char_end=4, text="Jane"),
        Mention(doc_id="d2", page=5, char_start=8, char_end=12, text="J. Doe"),
    ]
    right_mentions = [
        Mention(doc_id="d3", page=2, char_start=3, char_end=7, text="Jane"),
    ]
    left = CandidateSide(
        id="L",
        caption="Jane Doe",
        schema="Person",
        aliases=["J. Doe", "Janie"],
        properties={"address": ["1 Main St"], "dob": ["1980-01-01"]},
        mentions=left_mentions,
    )
    right = CandidateSide(
        id="R",
        caption="Jane Doe",
        schema="Person",
        aliases=["Jane D."],
        properties={"address": ["1 Main St"]},
        mentions=right_mentions,
    )
    candidate = Candidate(left=left, right=right, score=0.84)

    assert candidate.left.id == "L"
    assert candidate.right.id == "R"
    assert candidate.score == 0.84
    assert len(candidate.left.mentions) == 2
    assert len(candidate.right.mentions) == 1
    assert candidate.left.properties["address"] == ["1 Main St"]
    assert candidate.left.properties["dob"] == ["1980-01-01"]
    assert all(isinstance(m, Mention) for m in candidate.left.mentions)
    assert all(isinstance(m, Mention) for m in candidate.right.mentions)
    assert candidate.left.aliases == ["J. Doe", "Janie"]


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def test_valid_verdicts_set():
    assert VALID_VERDICTS == {"merge", "distinct", "unsure"}


def test_verdict_accepts_valid_values():
    for value in ("merge", "distinct", "unsure"):
        verdict = Verdict(left_id="L", right_id="R", verdict=value)
        assert verdict.verdict == value
        assert verdict.left_id == "L"
        assert verdict.right_id == "R"


def test_verdict_rejects_junk():
    with pytest.raises(ValueError):
        Verdict(left_id="L", right_id="R", verdict="bogus")


def test_verdict_is_frozen():
    verdict = Verdict(left_id="L", right_id="R", verdict="merge")
    with pytest.raises(dataclasses.FrozenInstanceError):
        verdict.verdict = "distinct"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Import purity (no marker; runs in the offline suite)
# ---------------------------------------------------------------------------

def test_importing_entity_resolution_policy_is_pure():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "import scripts.entity_resolution_policy as m\n"
        "bad = [x for x in ('nomenklatura','neo4j','followthemoney') if x in sys.modules]\n"
        "assert not bad, bad\n"
        "print('PURE_OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "PURE_OK" in proc.stdout

"""TDD for ``scripts/rollup.py`` -- the cross-source recurrence synthesis.

All fixtures are SYNTHETIC and inline: hand-built per-source findings objects in
the shape ``run_recipe`` emits. The suite pins the publish-critical contracts the
plan review (2026-06-04) sharpened:

* the recurrence thesis keys off EXTERNAL actors only (an out-of-state agency
  appearing across >= 2 sources), via each source's ``cross_agency
  .external_actor_counts`` -- an in-state actor can never manufacture a
  cross-source recurrence finding, and there is NO mega-user fallback that would
  mix the two entity kinds;
* cross-source rates are POOLED (sum of numerators / sum of denominators), not a
  mean of per-source percentages (which would lie when sources differ in size);
  any unweighted mean is named ``unweighted_mean_*`` so it can't be mistaken for
  the real rate;
* a source that SKIPPED a check contributes nothing to that aggregate -- no
  crash, no denominator pollution.
"""

from __future__ import annotations

from scripts.rollup import rollup


def _finding(source_id, n_records, **checks):
    return {"source_id": source_id, "n_records": n_records, "checks": checks}


def _two_sources_sharing_houston():
    """Source A and B both have the EXTERNAL actor 'Houston TX PD' (the shared one)."""
    a = _finding(
        "agency-A",
        100,
        out_of_state={
            "status": "ok", "out_of_state": 90, "in_state": 8,
            "unknown": 2, "total": 100, "out_of_state_pct": 0.9,
        },
        immigration={"status": "ok", "immigration": 40, "total": 100},
        truncation={"status": "ok", "truncated": True},
        statistical_patterns={"status": "ok", "gini": 0.8, "n_actors": 200},
        accountability={
            "status": "ok", "with_case": 30, "without_case": 70,
            "total": 100, "no_case_pct": 0.7,
        },
        cross_agency={
            "status": "ok",
            "external_actor_counts": {"Houston TX PD": 50, "Dallas TX PD": 50},
        },
    )
    b = _finding(
        "agency-B",
        60,
        out_of_state={
            "status": "ok", "out_of_state": 30, "in_state": 30,
            "unknown": 0, "total": 60, "out_of_state_pct": 0.5,
        },
        immigration={"status": "ok", "immigration": 10, "total": 60},
        truncation={"status": "ok", "truncated": False},
        statistical_patterns={"status": "ok", "gini": 0.6, "n_actors": 80},
        accountability={
            "status": "ok", "with_case": 36, "without_case": 24,
            "total": 60, "no_case_pct": 0.4,
        },
        cross_agency={
            "status": "ok",
            "external_actor_counts": {"Houston TX PD": 20, "Atlanta GA PD": 40},
        },
    )
    return [a, b]


def _by_actor(recurring):
    return {row["actor"]: row for row in recurring}


# --------------------------------------------------------------------------- #
# recurrence thesis: an EXTERNAL actor across >= 2 sources is recurring
# --------------------------------------------------------------------------- #

def test_rollup_surfaces_shared_external_actor_across_sources():
    out = rollup(_two_sources_sharing_houston())
    assert out["n_sources"] == 2

    recurring = _by_actor(out["recurring_external_actors"])
    assert "Houston TX PD" in recurring
    houston = recurring["Houston TX PD"]
    assert houston["n_sources"] == 2
    assert houston["total_count"] == 70           # 50 + 20
    assert sorted(houston["sources"]) == ["agency-A", "agency-B"]

    # Actors seen in only one source are NOT recurring.
    assert "Dallas TX PD" not in recurring
    assert "Atlanta GA PD" not in recurring


def test_rollup_recurring_actors_sorted_by_breadth_then_volume():
    findings = [
        _finding("s1", 10, cross_agency={
            "status": "ok", "external_actor_counts": {"X": 5, "Y": 3, "Z": 1}}),
        _finding("s2", 10, cross_agency={
            "status": "ok", "external_actor_counts": {"X": 5, "Y": 3}}),
        _finding("s3", 10, cross_agency={
            "status": "ok", "external_actor_counts": {"X": 5}}),
    ]
    out = rollup(findings)
    actors = [row["actor"] for row in out["recurring_external_actors"]]
    # X (3 sources) before Y (2 sources); Z (1 source) excluded.
    assert actors == ["X", "Y"]


# --------------------------------------------------------------------------- #
# aggregate roll-up: POOLED rates, not a mean of percentages
# --------------------------------------------------------------------------- #

def test_rollup_pools_rates_across_sources():
    out = rollup(_two_sources_sharing_houston())
    agg = out["aggregate"]
    assert agg["total_records"] == 160
    assert agg["total_immigration"] == 50                 # 40 + 10
    # POOLED: (90 + 30) / (100 + 60) = 0.75 -- the honest cross-source rate.
    assert agg["out_of_state_pct_total"] == 0.75
    # the unweighted mean of [0.9, 0.5] = 0.7 is reported but explicitly named.
    assert agg["unweighted_mean_out_of_state_pct"] == 0.7
    assert agg["sources_truncated"] == ["agency-A"]


# --------------------------------------------------------------------------- #
# robustness: a skipped check must not crash or pollute an aggregate
# --------------------------------------------------------------------------- #

def test_rollup_robust_to_skipped_checks():
    findings = _two_sources_sharing_houston()
    findings.append(
        _finding(
            "agency-C",
            40,
            immigration={"status": "skipped", "reason": "no column"},
            out_of_state={"status": "skipped", "reason": "no geo"},
            cross_agency={
                "status": "ok",
                "external_actor_counts": {"Houston TX PD": 5},
            },
        )
    )
    out = rollup(findings)
    agg = out["aggregate"]
    # total_records counts all three; rates only pool the sources that ran them.
    assert agg["total_records"] == 200
    assert agg["total_immigration"] == 50
    assert agg["out_of_state_pct_total"] == 0.75          # C's denom NOT added
    assert agg["unweighted_mean_out_of_state_pct"] == 0.7
    # Houston now recurs across all three sources.
    assert _by_actor(out["recurring_external_actors"])["Houston TX PD"]["n_sources"] == 3


def test_rollup_emits_theses_referencing_supporting_sources():
    out = rollup(_two_sources_sharing_houston())
    assert isinstance(out["theses"], list) and out["theses"]
    for thesis in out["theses"]:
        assert "thesis" in thesis
        assert "sources" in thesis
        assert thesis["n_sources_supporting"] == len(thesis["sources"])


def test_rollup_empty_input_is_safe():
    out = rollup([])
    assert out["n_sources"] == 0
    assert out["recurring_external_actors"] == []
    assert out["aggregate"]["total_records"] == 0
    # an undefined pooled rate over zero sources is None, not a fake 0.0.
    assert out["aggregate"]["out_of_state_pct_total"] is None

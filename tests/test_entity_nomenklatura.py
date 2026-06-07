"""Contract tests for the resolution edge (scripts/entity_nomenklatura.py).

ALL tests here are `ftm`-marked and SKIP on Windows: nomenklatura pulls
followthemoney, which does NOT install there (PyICU/ICU has no Windows wheel).
Their CORRECTNESS is verified in the CI `ftm` job (Ubuntu). The module must still
COLLECT cleanly during Windows pytest collection, so nothing nomenklatura- or
entity_nomenklatura-dependent is imported at module top -- every test imports what
it needs inside its own body.

The `pytestmark = ftm` marker means the offline subset (run with
`-m "not ... and not ftm"`) DESELECTS these (they do not even show as skips). The
`@ftm` skipif on each test makes a hypothetical `-m ftm` run on Windows SKIP
cleanly (nomenklatura absent) with no collection error.

This EXTENDS tests/test_entity_ftmize.py::test_nomenklatura_xref_candidate_smoke:
that smoke proves load -> xref -> get_candidates surfaces the two same-name John
Smith ids as a pair; here we drive the full resolve -> apply -> resolved-snapshot
flow (LogicV2, fail-closed apply, cluster + edge coalesce).
"""
import importlib.util
import json
import pathlib

import pytest

pytestmark = pytest.mark.ftm

ftm = pytest.mark.skipif(
    importlib.util.find_spec("nomenklatura") is None,
    reason="nomenklatura not installed (Linux/CI only)",
)

FIXTURE = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "reviewed_intermediate_sample"
    / "intermediate.json"
)

COALESCE_FIXTURE = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "reviewed_intermediate_coalesce"
    / "intermediate.json"
)

# The two same-name (John Smith) different-doc Person ids from the fixture.
# Phase 12 deliberately keeps cross-doc homonyms DISTINCT so Phase-13
# nomenklatura xref sees them as resolution candidates.
ID_A = "node_js_doc1_0a1b2c3d4e5f60718293a4b5c6d7e8f900112233"
ID_B = "node_js_doc2_99887766554433221100ffeeddccbbaa0a1b2c3d"

# A config whose auto_threshold is high enough that an identical-name pair lands
# in the review band (not auto-merge) under logic-v2 (per the design / task).
AUTO_THRESHOLD = 0.98
REVIEW_FLOOR = 0.70


def _load_intermediate(fixture=FIXTURE) -> dict:
    with fixture.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _config():
    from scripts.entity_resolution_policy import ResolutionConfig

    return ResolutionConfig(
        algorithm="logic-v2",
        auto_threshold=AUTO_THRESHOLD,
        review_floor=REVIEW_FLOOR,
    )


def _drop_resolver_table(scratch_dir) -> None:
    """Belt-and-suspenders teardown mirroring the ftmize smoke: reopen the
    per-test resolver and drop its table so a shared process-global engine cannot
    leak judgements across tests. The per-test scratch sqlite is the primary
    isolation; this is defensive."""
    try:
        import os

        from followthemoney import StatementEntity as Entity
        from nomenklatura.resolver import Resolver

        os.environ["NOMENKLATURA_DB_URL"] = (
            "sqlite:///" + (pathlib.Path(scratch_dir) / "resolver.db").as_posix()
        )
        resolver = Resolver[Entity].make_default()
        resolver.close()
        try:
            resolver._table.drop(resolver._engine, checkfirst=True)
        except Exception:
            pass
    except Exception:
        pass


def _bundle_entities_path(tmp_path, fixture=FIXTURE) -> pathlib.Path:
    """Write the FtM bundle from a fixture and return its entities path."""
    import scripts.entity_ftmize as ftmize

    intermediate = _load_intermediate(fixture)
    paths = ftmize.write_bundle(intermediate, tmp_path / "bundle")
    return pathlib.Path(paths["entities"])


def _find_pair(candidates, left_id, right_id):
    """Return the candidate dict for the (left,right) pair regardless of order."""
    want = frozenset((left_id, right_id))
    for c in candidates:
        if frozenset((c["left"]["id"], c["right"]["id"])) == want:
            return c
    return None


@ftm
def test_resolve_surfaces_review_band_pair(tmp_path):
    import scripts.entity_nomenklatura as en

    try:
        import nomenklatura  # noqa: F401
        import followthemoney  # noqa: F401
    except ImportError as exc:  # pragma: no cover - defensive
        pytest.skip("nomenklatura/followthemoney not installed: %s" % exc)

    entities_path = _bundle_entities_path(tmp_path)
    scratch = tmp_path / "scratch"

    result = en.resolve([entities_path], scratch, _config())

    candidate_path = pathlib.Path(result.candidate_snapshot_path)
    auto_log_path = pathlib.Path(result.auto_merge_log_path)
    run_path = scratch / "run.json"

    assert candidate_path.exists(), "resolve did not write candidate_snapshot.json"
    assert auto_log_path.exists(), "resolve did not write auto_merge_log.jsonl"
    assert run_path.exists(), "resolve did not write run.json"

    snapshot = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidates = snapshot["candidates"]

    pair = _find_pair(candidates, ID_A, ID_B)
    assert pair is not None, (
        "the two same-name John Smith ids did not surface as a review-band candidate"
    )
    score = pair["score"]
    assert REVIEW_FLOOR <= score < AUTO_THRESHOLD, (
        "John Smith pair score %r is not in the review band [%.2f, %.2f)"
        % (score, REVIEW_FLOOR, AUTO_THRESHOLD)
    )
    # packet_hash is the canonical "sha256:"-prefixed digest of the candidates.
    assert result.packet_hash.startswith("sha256:")

    _drop_resolver_table(scratch)


@ftm
def test_apply_merge_verdict(tmp_path):
    import scripts.entity_nomenklatura as en

    entities_path = _bundle_entities_path(tmp_path)
    scratch = tmp_path / "scratch"
    config = _config()

    result = en.resolve([entities_path], scratch, config)

    verdict = {
        "investigation_id": "greenville_flock_rfp",
        "packet_hash": result.packet_hash,
        "verdicts": [{"left": ID_A, "right": ID_B, "verdict": "merge"}],
    }
    verdict_path = scratch / "verdicts.json"
    verdict_path.write_text(json.dumps(verdict), encoding="utf-8")

    applied = en.apply_verdicts(verdict_path, scratch, config)

    assert applied.aborted_reason is None, (
        "apply aborted unexpectedly: %s" % applied.aborted_reason
    )
    assert applied.applied >= 1, "no verdict was applied"

    _drop_resolver_table(scratch)


@ftm
def test_build_resolved_snapshot_one_cluster_after_merge(tmp_path):
    import scripts.entity_nomenklatura as en
    from scripts.entity_resolution_policy import canonical_id
    from scripts.entity_resolved_snapshot import assert_snapshot_consumable

    entities_path = _bundle_entities_path(tmp_path)
    scratch = tmp_path / "scratch"
    config = _config()

    result = en.resolve([entities_path], scratch, config)

    verdict = {
        "investigation_id": "greenville_flock_rfp",
        "packet_hash": result.packet_hash,
        "verdicts": [{"left": ID_A, "right": ID_B, "verdict": "merge"}],
    }
    verdict_path = scratch / "verdicts.json"
    verdict_path.write_text(json.dumps(verdict), encoding="utf-8")
    applied = en.apply_verdicts(verdict_path, scratch, config)
    assert applied.aborted_reason is None and applied.applied >= 1

    # SEPARATE call that RELOADS from run.json (no in-memory store arg).
    snapshot = en.build_resolved_snapshot(scratch, "greenville_flock_rfp", config)
    assert_snapshot_consumable(snapshot)

    entities = snapshot["entities"]
    expected_cid = canonical_id([ID_A, ID_B])
    merged = [e for e in entities if e["canonical_id"] == expected_cid]
    assert len(merged) == 1, (
        "expected exactly one cluster for the merged John Smiths (cid %s)"
        % expected_cid
    )
    cluster = merged[0]
    assert sorted(cluster["member_ids"]) == sorted([ID_A, ID_B]), (
        "merged cluster members %r != the two John Smith ids"
        % cluster["member_ids"]
    )
    assert cluster["resolver_id"], (
        "merged cluster has no resolver_id (NK- id) set"
    )
    assert str(cluster["resolver_id"]).startswith("NK-"), (
        "resolver_id %r is not an NK- canonical" % cluster["resolver_id"]
    )

    _drop_resolver_table(scratch)


@ftm
def test_edge_coalesce_after_merge(tmp_path):
    import scripts.entity_nomenklatura as en
    from scripts.entity_resolution_policy import canonical_id

    # The coalesce fixture has TWO Membership edges (one from each John Smith) to
    # the SAME organization; after the merge they share (schema, head_canonical,
    # tail_canonical, role) -> ONE coalesced edge with unioned provenance.
    entities_path = _bundle_entities_path(tmp_path, COALESCE_FIXTURE)
    scratch = tmp_path / "scratch"
    config = _config()

    result = en.resolve([entities_path], scratch, config)

    verdict = {
        "investigation_id": "greenville_flock_coalesce",
        "packet_hash": result.packet_hash,
        "verdicts": [{"left": ID_A, "right": ID_B, "verdict": "merge"}],
    }
    verdict_path = scratch / "verdicts.json"
    verdict_path.write_text(json.dumps(verdict), encoding="utf-8")
    applied = en.apply_verdicts(verdict_path, scratch, config)
    assert applied.aborted_reason is None and applied.applied >= 1

    snapshot = en.build_resolved_snapshot(
        scratch, "greenville_flock_coalesce", config
    )

    edges = snapshot["edges"]
    entity_ids = {e["canonical_id"] for e in snapshot["entities"]}

    # No duplicate edge_id, and every endpoint is a known canonical_id.
    edge_ids = [e["edge_id"] for e in edges]
    assert len(edge_ids) == len(set(edge_ids)), (
        "duplicate edge_id in resolved edges: %r" % edge_ids
    )
    for e in edges:
        assert e["head_canonical"] in entity_ids, (
            "edge head %s is not a known canonical_id" % e["head_canonical"]
        )
        assert e["tail_canonical"] in entity_ids, (
            "edge tail %s is not a known canonical_id" % e["tail_canonical"]
        )

    # The two member Membership edges coalesce onto ONE canonical edge whose
    # provenance_refs union both member edges' statement ids.
    js_cid = canonical_id([ID_A, ID_B])
    membership = [
        e
        for e in edges
        if e["schema"] == "Membership" and e["head_canonical"] == js_cid
    ]
    assert len(membership) == 1, (
        "expected exactly one coalesced Membership edge from the merged John "
        "Smiths, got %d" % len(membership)
    )
    refs = membership[0]["provenance_refs"]
    assert (
        "stmt_e1_7e7e7e7e7e7e7e7e7e7e7e7e7e7e7e7e7e7e7e7e" in refs
        and "stmt_e2_8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f8f" in refs
    ), "coalesced Membership edge did not union both member edges' provenance: %r" % refs

    _drop_resolver_table(scratch)


@ftm
def test_apply_fail_closed_on_wrong_hash(tmp_path):
    import scripts.entity_nomenklatura as en
    from scripts.entity_resolution_policy import canonical_id

    entities_path = _bundle_entities_path(tmp_path)
    scratch = tmp_path / "scratch"
    config = _config()

    en.resolve([entities_path], scratch, config)

    verdict = {
        "investigation_id": "greenville_flock_rfp",
        "packet_hash": "sha256:deadbeef",
        "verdicts": [{"left": ID_A, "right": ID_B, "verdict": "merge"}],
    }
    verdict_path = scratch / "verdicts.json"
    verdict_path.write_text(json.dumps(verdict), encoding="utf-8")

    applied = en.apply_verdicts(verdict_path, scratch, config)

    assert applied.applied == 0, "fail-closed apply must apply nothing"
    assert applied.aborted_reason, "fail-closed apply must report an aborted_reason"
    assert isinstance(applied.aborted_reason, str) and applied.aborted_reason

    # Nothing was decided: a following build_resolved_snapshot still shows the
    # John Smiths UNMERGED -> two separate singleton clusters.
    snapshot = en.build_resolved_snapshot(scratch, "greenville_flock_rfp", config)
    merged_cid = canonical_id([ID_A, ID_B])
    assert all(
        e["canonical_id"] != merged_cid for e in snapshot["entities"]
    ), "the John Smiths were merged despite the fail-closed abort"

    a_singleton = canonical_id([ID_A])
    b_singleton = canonical_id([ID_B])
    cids = {e["canonical_id"] for e in snapshot["entities"]}
    assert a_singleton in cids and b_singleton in cids, (
        "expected both John Smiths as separate singleton clusters after abort"
    )

    _drop_resolver_table(scratch)


@ftm
def test_auto_merge_log_is_valid_jsonl(tmp_path):
    import scripts.entity_nomenklatura as en

    entities_path = _bundle_entities_path(tmp_path)
    scratch = tmp_path / "scratch"

    result = en.resolve([entities_path], scratch, _config())

    log_path = pathlib.Path(result.auto_merge_log_path)
    assert log_path.exists()

    lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # The log MAY be empty for this fixture (identical names can land in review,
    # not auto-merge under logic-v2). When non-empty, every row is valid JSON with
    # the expected shape.
    for ln in lines:
        row = json.loads(ln)
        for key in ("canonical", "members", "names", "score", "algorithm", "auto_threshold"):
            assert key in row, "auto-merge log row missing %s: %r" % (key, row)
        assert isinstance(row["members"], list)
        assert isinstance(row["names"], list)

    _drop_resolver_table(scratch)

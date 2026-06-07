"""Golden tests for scripts.entity_resolved_snapshot (Phase 13a Task 2).

ASCII only. Windows-runnable, offline, pure stdlib -- no nomenklatura/neo4j/ftm.
Covers the resolved-snapshot schema + serializer + the 13a/13b contract check
(design D3). Determinism + injected generated_at (no clock) are pinned here.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from scripts.entity_resolved_snapshot import (
    ResolvedEdge,
    ResolvedEntity,
    assert_snapshot_consumable,
    build_snapshot,
)


# --------------------------------------------------------------------------
# Fixtures: a small, well-formed resolved graph (2 entities + 1 edge).
# --------------------------------------------------------------------------
def _provenance():
    return [
        {
            "ref_id": "p1",
            "doc_id": "docA",
            "page": 1,
            "char_start": 0,
            "char_end": 10,
            "model": "gliner",
            "confidence": 0.9,
        },
        {
            "ref_id": "p2",
            "doc_id": "docB",
            "page": 2,
            "char_start": 5,
            "char_end": 20,
            "model": "gliner",
            "confidence": 0.8,
        },
    ]


def _entities():
    return [
        ResolvedEntity(
            canonical_id="c-alice",
            schema="Person",
            caption="Alice Example",
            aliases=["A. Example"],
            member_ids=["n1", "n2"],
            properties={"name": ["Alice Example"], "country": ["us"]},
            resolver_id="NK-aaa",
            provenance_refs=["p1"],
        ),
        ResolvedEntity(
            canonical_id="c-acme",
            schema="Organization",
            caption="Acme LLC",
            aliases=[],
            member_ids=["n3"],
            properties={"name": ["Acme LLC"]},
            resolver_id=None,
            provenance_refs=["p2"],
        ),
    ]


def _edges():
    return [
        ResolvedEdge(
            edge_id="e1",
            schema="Directorship",
            head_canonical="c-alice",
            tail_canonical="c-acme",
            role="director",
            properties={"role": ["director"]},
            provenance_refs=["p1", "p2"],
        ),
    ]


def _build(generated_at="2026-06-07T00:00:00Z"):
    return build_snapshot(
        _entities(),
        _edges(),
        _provenance(),
        investigation_id="inv-42",
        algorithm="logic-v2",
        thresholds={"auto_threshold": 0.98, "review_floor": 0.70},
        generated_at=generated_at,
    )


# --------------------------------------------------------------------------
# Top-level shape + metadata.
# --------------------------------------------------------------------------
def test_build_snapshot_top_level_keys():
    snap = _build()
    assert set(snap.keys()) == {"metadata", "entities", "edges", "provenance"}


def test_metadata_carries_injected_values():
    snap = _build()
    meta = snap["metadata"]
    assert meta["investigation_id"] == "inv-42"
    assert meta["algorithm"] == "logic-v2"
    assert meta["thresholds"] == {"auto_threshold": 0.98, "review_floor": 0.70}
    assert meta["generated_at"] == "2026-06-07T00:00:00Z"
    # default snapshot_version
    assert meta["snapshot_version"] == "1.0"


def test_snapshot_version_override():
    snap = build_snapshot(
        _entities(),
        _edges(),
        _provenance(),
        investigation_id="inv-42",
        algorithm="logic-v2",
        thresholds={},
        generated_at="t",
        snapshot_version="2.5",
    )
    assert snap["metadata"]["snapshot_version"] == "2.5"


# --------------------------------------------------------------------------
# Top-level (NOT nested) entities/edges, all dataclass fields present.
# --------------------------------------------------------------------------
def test_entities_and_edges_are_top_level_lists():
    snap = _build()
    assert isinstance(snap["entities"], list)
    assert isinstance(snap["edges"], list)
    assert len(snap["entities"]) == 2
    assert len(snap["edges"]) == 1


def test_entity_dicts_have_all_dataclass_fields():
    snap = _build()
    expected = {
        "canonical_id",
        "schema",
        "caption",
        "aliases",
        "member_ids",
        "properties",
        "resolver_id",
        "provenance_refs",
    }
    for ent in snap["entities"]:
        assert set(ent.keys()) == expected
    # values round-trip from the dataclass
    alice = snap["entities"][0]
    assert alice["canonical_id"] == "c-alice"
    assert alice["member_ids"] == ["n1", "n2"]
    assert alice["resolver_id"] == "NK-aaa"
    assert snap["entities"][1]["resolver_id"] is None


def test_edge_dicts_have_all_dataclass_fields():
    snap = _build()
    expected = {
        "edge_id",
        "schema",
        "head_canonical",
        "tail_canonical",
        "role",
        "properties",
        "provenance_refs",
    }
    for edge in snap["edges"]:
        assert set(edge.keys()) == expected
    edge = snap["edges"][0]
    assert edge["head_canonical"] == "c-alice"
    assert edge["tail_canonical"] == "c-acme"
    assert edge["role"] == "director"
    assert edge["provenance_refs"] == ["p1", "p2"]


def test_provenance_passed_through_unchanged():
    snap = _build()
    assert snap["provenance"] == _provenance()


# --------------------------------------------------------------------------
# generated_at is INJECTED (no clock). Two builds differ ONLY in that field.
# --------------------------------------------------------------------------
def test_generated_at_is_injected_only_difference():
    snap_a = _build(generated_at="2020-01-01T00:00:00Z")
    snap_b = _build(generated_at="2099-12-31T23:59:59Z")

    assert snap_a["metadata"]["generated_at"] == "2020-01-01T00:00:00Z"
    assert snap_b["metadata"]["generated_at"] == "2099-12-31T23:59:59Z"

    # Strip generated_at -> the snapshots are otherwise identical (no clock read).
    a_meta = dict(snap_a["metadata"])
    b_meta = dict(snap_b["metadata"])
    a_meta.pop("generated_at")
    b_meta.pop("generated_at")
    assert a_meta == b_meta
    assert snap_a["entities"] == snap_b["entities"]
    assert snap_a["edges"] == snap_b["edges"]
    assert snap_a["provenance"] == snap_b["provenance"]


def test_generated_at_accepts_epoch_int():
    snap = _build(generated_at=1717718400)
    assert snap["metadata"]["generated_at"] == 1717718400


# --------------------------------------------------------------------------
# Determinism: identical inputs -> identical serialized dict.
# --------------------------------------------------------------------------
def test_determinism_identical_inputs():
    a = _build()
    b = _build()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# --------------------------------------------------------------------------
# assert_snapshot_consumable: passes a good snapshot, raises on each defect.
# --------------------------------------------------------------------------
def test_consumable_passes_well_formed_snapshot():
    snap = _build()
    # Must not raise.
    assert assert_snapshot_consumable(snap) is None


def test_consumable_raises_on_dangling_head_endpoint():
    snap = _build()
    snap["edges"][0]["head_canonical"] = "c-nope"
    with pytest.raises(AssertionError) as exc:
        assert_snapshot_consumable(snap)
    assert "c-nope" in str(exc.value)


def test_consumable_raises_on_dangling_tail_endpoint():
    snap = _build()
    snap["edges"][0]["tail_canonical"] = "c-ghost"
    with pytest.raises(AssertionError) as exc:
        assert_snapshot_consumable(snap)
    assert "c-ghost" in str(exc.value)


def test_consumable_raises_on_unresolved_entity_provenance_ref():
    snap = _build()
    snap["entities"][0]["provenance_refs"] = ["p1", "p-missing"]
    with pytest.raises(AssertionError) as exc:
        assert_snapshot_consumable(snap)
    assert "p-missing" in str(exc.value)


def test_consumable_raises_on_unresolved_edge_provenance_ref():
    snap = _build()
    snap["edges"][0]["provenance_refs"] = ["p1", "p-bad"]
    with pytest.raises(AssertionError) as exc:
        assert_snapshot_consumable(snap)
    assert "p-bad" in str(exc.value)


def test_consumable_raises_on_missing_top_level_key():
    snap = _build()
    del snap["edges"]
    with pytest.raises(AssertionError) as exc:
        assert_snapshot_consumable(snap)
    assert "edges" in str(exc.value)


def test_consumable_raises_on_missing_investigation_id():
    snap = _build()
    del snap["metadata"]["investigation_id"]
    with pytest.raises(AssertionError):
        assert_snapshot_consumable(snap)


def test_consumable_raises_on_empty_investigation_id():
    snap = _build()
    snap["metadata"]["investigation_id"] = ""
    with pytest.raises(AssertionError):
        assert_snapshot_consumable(snap)


def test_consumable_raises_on_non_dict_metadata():
    snap = _build()
    snap["metadata"] = ["not", "a", "dict"]
    with pytest.raises(AssertionError):
        assert_snapshot_consumable(snap)


# --------------------------------------------------------------------------
# Import purity: no nomenklatura/neo4j/followthemoney pulled in (subprocess).
# --------------------------------------------------------------------------
def test_importing_entity_resolved_snapshot_is_pure():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "import scripts.entity_resolved_snapshot as m\n"
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

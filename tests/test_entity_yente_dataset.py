"""ASCII only. Windows-golden, stdlib-only tests for the pure core in
scripts/entity_yente_dataset.py (snapshot -> yente own-corpus dataset + manifest).

Builds fixture snapshots via the real Phase-13a seam (importing
scripts.entity_resolved_snapshot is fine -- it is stdlib too).
"""
from __future__ import annotations

import json

import pytest

from scripts.entity_resolved_snapshot import ResolvedEdge, ResolvedEntity, build_snapshot
from scripts.entity_yente_dataset import (
    DatasetEntry,
    dataset_version,
    entity_to_ftm_dict,
    render_manifest,
    snapshot_to_entities,
    write_dataset,
)


def _snapshot(entities, edges=None):
    return build_snapshot(
        list(entities),
        list(edges or []),
        [],
        investigation_id="inv-1",
        algorithm="logic-v2",
        thresholds={},
        generated_at="2026-06-07",
    )


def _fixture_snapshot():
    """2 Person + 1 Organization, with an alias dup and a pass-through prop."""
    return _snapshot(
        [
            ResolvedEntity(
                canonical_id="p1",
                schema="Person",
                caption="Alice Example",
                aliases=["A. Example", "Alice Example"],
                properties={"country": ["us"]},
            ),
            ResolvedEntity(
                canonical_id="p2",
                schema="Person",
                caption="Bob Sample",
                aliases=["Bobby Sample"],
            ),
            ResolvedEntity(
                canonical_id="o1",
                schema="Organization",
                caption="Acme Holdings",
            ),
        ]
    )


# --- 1. entity_to_ftm_dict happy path ---------------------------------------


def test_entity_to_ftm_dict_name_caption_plus_aliases_order_deduped():
    e = {
        "canonical_id": "p1",
        "schema": "Person",
        "caption": "Alice Example",
        "aliases": ["A. Example", "Alice Example"],
        "properties": {},
    }
    out = entity_to_ftm_dict(e)
    # caption first, alias dup of caption dropped, order preserved.
    assert out["properties"]["name"] == ["Alice Example", "A. Example"]


def test_entity_to_ftm_dict_merges_existing_name_property_first():
    e = {
        "canonical_id": "p1",
        "schema": "Person",
        "caption": "Alice Example",
        "aliases": ["A. Example"],
        "properties": {"name": ["Alice E."]},
    }
    out = entity_to_ftm_dict(e)
    # existing properties['name'] leads, then caption, then aliases; deduped.
    assert out["properties"]["name"] == ["Alice E.", "Alice Example", "A. Example"]


def test_entity_to_ftm_dict_passes_other_props_through_unchanged():
    e = {
        "canonical_id": "p1",
        "schema": "Person",
        "caption": "Alice Example",
        "aliases": [],
        "properties": {"country": ["us"], "birthDate": ["1980-01-01"]},
    }
    out = entity_to_ftm_dict(e)
    assert out["properties"]["country"] == ["us"]
    assert out["properties"]["birthDate"] == ["1980-01-01"]


def test_entity_to_ftm_dict_exact_keys_no_canonicalid_and_id_matches():
    e = {
        "canonical_id": "p1",
        "schema": "Person",
        "caption": "Alice Example",
        "aliases": [],
        "properties": {},
    }
    out = entity_to_ftm_dict(e)
    assert set(out.keys()) == {"id", "schema", "properties"}
    assert "canonicalId" not in out
    assert "canonicalId" not in out["properties"]
    assert out["id"] == "p1"


# --- 2. snapshot-contract-gap guard -----------------------------------------


def test_entity_to_ftm_dict_raises_on_missing_canonical_id():
    e = {"schema": "Person", "caption": "Alice", "aliases": [], "properties": {}}
    with pytest.raises(ValueError):
        entity_to_ftm_dict(e)


def test_entity_to_ftm_dict_raises_on_missing_schema_names_cid():
    e = {"canonical_id": "p9", "caption": "Alice", "aliases": [], "properties": {}}
    with pytest.raises(ValueError) as ei:
        entity_to_ftm_dict(e)
    assert "p9" in str(ei.value)


def test_entity_to_ftm_dict_raises_on_empty_schema():
    e = {
        "canonical_id": "p9",
        "schema": "   ",
        "caption": "Alice",
        "aliases": [],
        "properties": {},
    }
    with pytest.raises(ValueError):
        entity_to_ftm_dict(e)


def test_entity_to_ftm_dict_raises_on_no_usable_name_names_cid():
    e = {
        "canonical_id": "p9",
        "schema": "Person",
        "caption": "",
        "aliases": [],
        "properties": {},
    }
    with pytest.raises(ValueError) as ei:
        entity_to_ftm_dict(e)
    msg = str(ei.value)
    assert "p9" in msg
    assert "name" in msg


# --- 3. snapshot_to_entities ------------------------------------------------


def test_snapshot_to_entities_runs_consumable_guard_dangling_edge():
    snap = _snapshot(
        [ResolvedEntity(canonical_id="c1", schema="Person", caption="Alice")],
        edges=[
            ResolvedEdge(
                edge_id="e1",
                schema="Family",
                head_canonical="DANGLING",
                tail_canonical="c1",
            )
        ],
    )
    with pytest.raises(AssertionError):
        snapshot_to_entities(snap)


def test_snapshot_to_entities_one_ftm_dict_per_entity():
    snap = _fixture_snapshot()
    out = snapshot_to_entities(snap)
    assert len(out) == 3
    assert [d["id"] for d in out] == ["p1", "p2", "o1"]
    assert all(set(d.keys()) == {"id", "schema", "properties"} for d in out)


# --- 4. write_dataset -------------------------------------------------------


def test_write_dataset_line_delimited_json_and_count(tmp_path):
    snap = _fixture_snapshot()
    res = write_dataset(snap, tmp_path)
    assert res["count"] == 3
    text = (tmp_path / "entities.ftm.json").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln]
    assert len(lines) == 3
    parsed = [json.loads(ln) for ln in lines]
    assert [p["id"] for p in parsed] == ["p1", "p2", "o1"]
    assert res["entities_path"].endswith("entities.ftm.json")


def test_write_dataset_deterministic_same_snapshot_same_version(tmp_path):
    snap = _fixture_snapshot()
    a = write_dataset(snap, tmp_path / "a")
    b = write_dataset(snap, tmp_path / "b")
    assert a["version"] == b["version"]


def test_write_dataset_changing_caption_changes_version(tmp_path):
    a = write_dataset(_fixture_snapshot(), tmp_path / "a")
    changed = _snapshot(
        [
            ResolvedEntity(
                canonical_id="p1",
                schema="Person",
                caption="Alice CHANGED",
                aliases=["A. Example", "Alice Example"],
                properties={"country": ["us"]},
            ),
            ResolvedEntity(
                canonical_id="p2",
                schema="Person",
                caption="Bob Sample",
                aliases=["Bobby Sample"],
            ),
            ResolvedEntity(
                canonical_id="o1",
                schema="Organization",
                caption="Acme Holdings",
            ),
        ]
    )
    b = write_dataset(changed, tmp_path / "b")
    assert a["version"] != b["version"]


# --- 5. dataset_version -----------------------------------------------------


def test_dataset_version_stable_for_identical_text():
    assert dataset_version("same text\n") == dataset_version("same text\n")


def test_dataset_version_differs_for_different_text():
    assert dataset_version("text one\n") != dataset_version("text two\n")


# --- 6. render_manifest -----------------------------------------------------


def _entry():
    return DatasetEntry(
        name="magpie_corpus",
        title="Magpie Corpus",
        path="/data/entities.ftm.json",
        version="abc123def456",
    )


def test_render_manifest_own_corpus_no_catalogs():
    out = render_manifest([_entry()], include_watchlist=False)
    assert "catalogs:" not in out
    assert "datasets:" in out
    assert "namespace: true" in out
    assert "name: magpie_corpus" in out
    assert "Magpie Corpus" in out
    assert "/data/entities.ftm.json" in out
    assert "abc123def456" in out


def test_render_manifest_watchlist_adds_civic_catalog():
    out = render_manifest([_entry()], include_watchlist=True)
    assert "catalogs:" in out
    assert "data.opensanctions.org" in out
    assert "scope: default" in out
    # dataset block still present.
    assert "datasets:" in out
    assert "name: magpie_corpus" in out


def test_render_manifest_namespace_false_branch():
    # The namespace=False branch (review-flagged gap): renders "namespace: false".
    entry = DatasetEntry(
        name="magpie_corpus",
        title="Magpie Corpus",
        path="/data/entities.ftm.json",
        version="v1",
        namespace=False,
    )
    out = render_manifest([entry], include_watchlist=False)
    assert "namespace: false" in out
    assert "namespace: true" not in out


def test_write_dataset_returns_echoed_name(tmp_path):
    # Review fold: write_dataset echoes `name` so a caller can build DatasetEntry.
    res = write_dataset(_fixture_snapshot(), tmp_path, name="magpie_corpus")
    assert res["name"] == "magpie_corpus"

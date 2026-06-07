"""ASCII only. Windows-golden tests for scripts/entity_crossref.py.

Pure stdlib: pathlib + json to load the captured /match fixture; no httpx/yente.
Drives the PURE shaping core: build_match_query, build_match_body,
parse_match_response, group_hits_by_dataset, build_crossref_report.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib

import pytest

from scripts.entity_crossref import (
    CrossRefHit,
    build_crossref_report,
    build_match_body,
    build_match_query,
    group_hits_by_dataset,
    parse_match_response,
)

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "yente_match_response.json"
_RESPONSE_KEY = "magpiecanonaaa0000000000000000000000000000000"
_NAMESPACED_ID = "magpie-canon-aaa.e8d592d5679046f6e547fc2237bccab948835d5f"


@pytest.fixture()
def match_response() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="ascii"))


# ---------------------------------------------------------------------------
# build_match_query
# ---------------------------------------------------------------------------

def test_build_match_query_returns_key_and_item():
    entity = {
        "canonical_id": "magpie-canon-aaa",
        "schema": "Person",
        "caption": "Jonathan Edward Maple",
        "properties": {
            "name": ["Jonathan Edward Maple"],
            "country": ["us"],
            "occupation": ["mayor"],  # stray prop -- must be dropped
        },
    }
    key, item = build_match_query(entity)

    # query_key == canonical_id (the attribution handle)
    assert key == "magpie-canon-aaa"
    # item carries schema + properties only
    assert set(item.keys()) == {"schema", "properties"}
    assert item["schema"] == "Person"
    # only _MATCH_PROPS survive; the stray "occupation" is gone
    assert item["properties"] == {
        "name": ["Jonathan Edward Maple"],
        "country": ["us"],
    }
    assert "occupation" not in item["properties"]


def test_build_match_query_name_falls_back_to_caption_plus_aliases():
    entity = {
        "canonical_id": "c1",
        "schema": "Person",
        "caption": "Jane Q Public",
        "aliases": ["Jane Public", "Jane Q Public", "J. Public"],  # dup of caption
        "properties": {"country": ["us"]},  # no name
    }
    key, item = build_match_query(entity)

    assert key == "c1"
    # name synthesized from [caption] + aliases, deduped, order preserved
    assert item["properties"]["name"] == [
        "Jane Q Public",
        "Jane Public",
        "J. Public",
    ]


def test_build_match_query_uses_existing_name_no_caption_append():
    entity = {
        "canonical_id": "c2",
        "schema": "Person",
        "caption": "Should Not Appear",
        "aliases": ["Also Not Appear"],
        "properties": {"name": ["Real Name One", "Real Name Two"]},
    }
    _key, item = build_match_query(entity)

    # existing properties["name"] is used verbatim; caption/aliases NOT appended
    assert item["properties"]["name"] == ["Real Name One", "Real Name Two"]
    assert "Should Not Appear" not in item["properties"]["name"]


# ---------------------------------------------------------------------------
# build_match_body
# ---------------------------------------------------------------------------

def test_build_match_body_batches_by_canonical_id():
    entities = [
        {
            "canonical_id": "a",
            "schema": "Person",
            "properties": {"name": ["A One"]},
        },
        {
            "canonical_id": "b",
            "schema": "Company",
            "properties": {"name": ["B Two"]},
        },
    ]
    body = build_match_body(entities)

    assert set(body.keys()) == {"queries"}
    assert set(body["queries"].keys()) == {"a", "b"}
    assert body["queries"]["a"] == {
        "schema": "Person",
        "properties": {"name": ["A One"]},
    }
    assert body["queries"]["b"]["schema"] == "Company"


# ---------------------------------------------------------------------------
# parse_match_response
# ---------------------------------------------------------------------------

def test_parse_match_response_attribution_and_threshold(match_response):
    hits = parse_match_response(
        match_response, scope="own_corpus", threshold=0.7, cap=5
    )

    # weak hit (0.42, match False) dropped at threshold 0.7 -> only the strong one
    assert len(hits) == 1
    hit = hits[0]
    # attributed by the RESPONSE KEY (== our canonical_id)
    assert hit.query_canonical_id == _RESPONSE_KEY
    # namespaced result id preserved VERBATIM (never split/parsed)
    assert hit.result_id == _NAMESPACED_ID
    assert hit.scope == "own_corpus"
    assert hit.caption == "Jonathan Edward Maple"
    assert hit.schema == "Person"
    assert hit.datasets == ["magpie_corpus"]
    assert hit.score == 1.0
    assert hit.match is True
    assert hit.properties == {"name": ["Jonathan Edward Maple"], "country": ["us"]}


def test_parse_match_response_threshold_zero_keeps_both(match_response):
    # threshold 0.0: weak hit kept because score 0.42 >= 0.0
    hits = parse_match_response(
        match_response, scope="own_corpus", threshold=0.0, cap=5
    )
    assert len(hits) == 2
    assert [h.result_id for h in hits] == [_NAMESPACED_ID, "other-weak.1234"]
    assert all(h.query_canonical_id == _RESPONSE_KEY for h in hits)


def test_parse_match_response_cap_limits_per_query(match_response):
    # cap=1 keeps only the first result even though both pass threshold 0.0
    hits = parse_match_response(
        match_response, scope="watchlists", threshold=0.0, cap=1
    )
    assert len(hits) == 1
    assert hits[0].result_id == _NAMESPACED_ID
    assert hits[0].scope == "watchlists"


# ---------------------------------------------------------------------------
# group_hits_by_dataset
# ---------------------------------------------------------------------------

def test_group_hits_by_dataset():
    hits = [
        CrossRefHit(
            query_canonical_id="q1", scope="own_corpus", result_id="r1",
            caption="One", schema="Person", datasets=["ds_a", "ds_b"],
            score=0.9, match=True,
        ),
        CrossRefHit(
            query_canonical_id="q2", scope="own_corpus", result_id="r2",
            caption="Two", schema="Person", datasets=["ds_a"],
            score=0.8, match=True,
        ),
        CrossRefHit(
            query_canonical_id="q3", scope="own_corpus", result_id="r3",
            caption="Three", schema="Person", datasets=[],
            score=0.8, match=True,
        ),
    ]
    grouped = group_hits_by_dataset(hits)

    assert set(grouped.keys()) == {"ds_a", "ds_b", "(unknown)"}
    assert [h.result_id for h in grouped["ds_a"]] == ["r1", "r2"]
    assert [h.result_id for h in grouped["ds_b"]] == ["r1"]
    assert [h.result_id for h in grouped["(unknown)"]] == ["r3"]


# ---------------------------------------------------------------------------
# build_crossref_report
# ---------------------------------------------------------------------------

def test_build_crossref_report_shape():
    hit = CrossRefHit(
        query_canonical_id="q1", scope="own_corpus", result_id="r1",
        caption="One", schema="Person", datasets=["magpie_corpus"],
        score=0.95, match=True, properties={"name": ["One"]},
    )
    provenance = {
        "manifest_hash": "abc123",
        "dataset_file_hashes": {"magpie_corpus": "deadbeef"},
        "yente_image_tag": "yente:5.4.0",
        "opensearch_image_tag": "opensearch:2.0",
        "catalog": [
            {
                "name": "magpie_corpus",
                "version": "1",
                "updated_at": "2026-06-07",
                "last_export": "2026-06-07",
                "index_current": True,
            }
        ],
    }
    report = build_crossref_report(
        {"own_corpus": [hit]},
        index_provenance=provenance,
        threshold=0.7,
        algorithm="logic-v1",
        generated_at="2026-06-07T00:00:00Z",
    )

    # metadata block
    meta = report["metadata"]
    assert meta["threshold"] == 0.7
    assert meta["algorithm"] == "logic-v1"
    assert meta["generated_at"] == "2026-06-07T00:00:00Z"
    assert meta["reproducibility"] == {
        "own_corpus": "reproducible",
        "watchlists": "best_effort_externally_versioned",
    }

    # provenance passes through unchanged
    assert report["index_provenance"] == provenance
    assert report["index_provenance"]["catalog"][0]["index_current"] is True

    # scopes maps scope -> list of asdict(hit) dicts
    assert set(report["scopes"].keys()) == {"own_corpus"}
    serialized = report["scopes"]["own_corpus"]
    assert serialized == [dataclasses.asdict(hit)]
    assert serialized[0]["result_id"] == "r1"
    assert serialized[0]["properties"] == {"name": ["One"]}

"""ASCII only. neo4j-marked tests for scripts.entity_graph_neo4j.

These tests need a LIVE Neo4j (a service container in CI; verified locally
against a real Neo4j). They are gated three ways so the offline suite stays
green WITHOUT the neo4j driver or a database:
  - the module-level `pytestmark = pytest.mark.neo4j` lets `-m "not neo4j"`
    deselect the whole file;
  - the neo4j driver and scripts.entity_graph_neo4j are imported INSIDE the test
    bodies (never at module top), so the file COLLECTS even when the driver is
    absent (no ImportError at collection);
  - `_driver_or_skip()` skips at runtime when no live DB is reachable.

Each test uses a UNIQUE investigation_id (containing the test name) and DETACH
DELETEs that investigation's nodes at the start, so reruns are clean and a
shared database stays isolated. Snapshots are CANNED dicts hand-built to match
entity_resolved_snapshot.build_snapshot's shape -- NO nomenklatura needed.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.neo4j


def _driver_or_skip():
    """Return a live neo4j driver, or skip if the driver / DB is unavailable."""
    neo4j = pytest.importorskip("neo4j")
    uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pw = os.environ.get("NEO4J_PASSWORD", "testpassword")
    try:
        driver = neo4j.GraphDatabase.driver(uri, auth=(user, pw))
        driver.verify_connectivity()
    except Exception as exc:  # pragma: no cover - depends on live infra
        pytest.skip("no live Neo4j: %s" % exc)
    return driver


def _canned_snapshot(investigation_id: str, *, entities, edges):
    """Build a minimal snapshot dict (the build_snapshot shape) by hand.

    entities / edges are passed in so a test can drop one to exercise the
    scoped REPLACE. provenance is left empty -- the writer does not read it.
    """
    return {
        "metadata": {
            "investigation_id": investigation_id,
            "algorithm": "logic-v2",
            "thresholds": {"auto": 0.98, "floor": 0.70},
            "generated_at": "2026-06-07T00:00:00Z",
            "snapshot_version": "1.0",
        },
        "entities": entities,
        "edges": edges,
        "provenance": [],
    }


def _entity(canonical_id, *, caption, schema="Person"):
    return {
        "canonical_id": canonical_id,
        "schema": schema,
        "caption": caption,
        "aliases": [caption],
        "member_ids": [canonical_id + "-m0"],
        "properties": {"name": [caption]},
        "resolver_id": None,
        "provenance_refs": [],
    }


def _edge(edge_id, head, tail, *, schema="Associate", role=None):
    return {
        "edge_id": edge_id,
        "schema": schema,
        "head_canonical": head,
        "tail_canonical": tail,
        "role": role,
        "properties": {},
        "provenance_refs": [],
    }


def _two_entity_one_edge(investigation_id):
    """A 2-entity + 1-edge canned snapshot."""
    entities = [
        _entity("c_alice", caption="Alice"),
        _entity("c_bob", caption="Bob"),
    ]
    edges = [_edge("e_ab", "c_alice", "c_bob", role="contact")]
    return _canned_snapshot(investigation_id, entities=entities, edges=edges)


def _clean_investigation(driver, investigation_id):
    """DETACH DELETE every node for this investigation (clean-slate rerun)."""
    with driver.session(database="neo4j") as session:
        session.run(
            "MATCH (e:Entity {investigation_id: $inv}) DETACH DELETE e",
            inv=investigation_id,
        )


def _entity_count(driver, investigation_id):
    with driver.session(database="neo4j") as session:
        rec = session.run(
            "MATCH (e:Entity {investigation_id: $inv}) RETURN count(e) AS n",
            inv=investigation_id,
        ).single()
    return rec["n"]


def _rel_count(driver, investigation_id):
    with driver.session(database="neo4j") as session:
        rec = session.run(
            "MATCH (:Entity {investigation_id: $inv})"
            "-[r:REL {investigation_id: $inv}]->() RETURN count(r) AS n",
            inv=investigation_id,
        ).single()
    return rec["n"]


def test_ensure_schema_idempotent_and_constraint_and_index_exist():
    """ensure_schema twice raises nothing; the scoped_id constraint AND the
    entity_investigation index both exist."""
    import scripts.entity_graph_neo4j as graph

    driver = _driver_or_skip()
    try:
        graph.ensure_schema(driver)
        graph.ensure_schema(driver)  # idempotent -- second call must not error.
        with driver.session(database="neo4j") as session:
            constraint_names = [
                r["name"] for r in session.run("SHOW CONSTRAINTS YIELD name")
            ]
            index_names = [r["name"] for r in session.run("SHOW INDEXES YIELD name")]
        assert "entity_scoped_id" in constraint_names
        assert "entity_investigation" in index_names
    finally:
        driver.close()


def test_write_is_idempotent():
    """Writing the same snapshot twice yields identical node/rel counts."""
    import scripts.entity_graph_neo4j as graph

    inv = "test_write_is_idempotent_1"
    driver = _driver_or_skip()
    try:
        graph.ensure_schema(driver)
        _clean_investigation(driver, inv)
        snap = _two_entity_one_edge(inv)

        graph.write(driver, snap)
        first_nodes = _entity_count(driver, inv)
        first_rels = _rel_count(driver, inv)
        assert first_nodes == 2
        assert first_rels == 1

        graph.write(driver, snap)  # re-run: MERGE upserts, no duplicates.
        assert _entity_count(driver, inv) == first_nodes
        assert _rel_count(driver, inv) == first_rels
    finally:
        _clean_investigation(driver, inv)
        driver.close()


def test_scoped_replace_deletes_in_scope_orphan():
    """Re-writing the same investigation with an entity + its edge removed
    deletes that in-scope orphan (and its edge); the survivor remains."""
    import scripts.entity_graph_neo4j as graph

    inv = "test_scoped_replace_deletes_in_scope_orphan_1"
    driver = _driver_or_skip()
    try:
        graph.ensure_schema(driver)
        _clean_investigation(driver, inv)

        graph.write(driver, _two_entity_one_edge(inv))
        assert _entity_count(driver, inv) == 2
        assert _rel_count(driver, inv) == 1

        # Re-write the SAME investigation with Bob (and the edge) dropped.
        reduced = _canned_snapshot(
            inv,
            entities=[_entity("c_alice", caption="Alice")],
            edges=[],
        )
        graph.write(driver, reduced)

        assert _entity_count(driver, inv) == 1
        assert _rel_count(driver, inv) == 0
        with driver.session(database="neo4j") as session:
            survivors = [
                r["cid"]
                for r in session.run(
                    "MATCH (e:Entity {investigation_id: $inv}) "
                    "RETURN e.canonical_id AS cid",
                    inv=inv,
                )
            ]
        assert survivors == ["c_alice"]
    finally:
        _clean_investigation(driver, inv)
        driver.close()


def test_scoped_isolation_across_investigations():
    """The D4 critical: writing investigation B (even with an overlapping
    canonical_id) leaves investigation A's subgraph untouched, keyed by
    scoped_id so the shared canonical_id does not collide."""
    import scripts.entity_graph_neo4j as graph

    inv_a = "test_scoped_isolation_A_1"
    inv_b = "test_scoped_isolation_B_1"
    driver = _driver_or_skip()
    try:
        graph.ensure_schema(driver)
        _clean_investigation(driver, inv_a)
        _clean_investigation(driver, inv_b)

        graph.write(driver, _two_entity_one_edge(inv_a))
        a_nodes_before = _entity_count(driver, inv_a)
        a_rels_before = _rel_count(driver, inv_a)
        assert a_nodes_before == 2
        assert a_rels_before == 1

        # B shares canonical_id "c_alice" but is a different investigation.
        snap_b = _canned_snapshot(
            inv_b,
            entities=[
                _entity("c_alice", caption="Alice-in-B"),
                _entity("c_carol", caption="Carol"),
            ],
            edges=[_edge("e_ac", "c_alice", "c_carol")],
        )
        graph.write(driver, snap_b)

        # A is UNTOUCHED.
        assert _entity_count(driver, inv_a) == a_nodes_before
        assert _rel_count(driver, inv_a) == a_rels_before
        # B coexists with its own subgraph.
        assert _entity_count(driver, inv_b) == 2
        assert _rel_count(driver, inv_b) == 1

        # The shared canonical_id resolves to TWO distinct scoped nodes.
        with driver.session(database="neo4j") as session:
            rec = session.run(
                "MATCH (e:Entity {canonical_id: 'c_alice'}) "
                "WHERE e.investigation_id IN [$a, $b] "
                "RETURN count(e) AS n",
                a=inv_a,
                b=inv_b,
            ).single()
        assert rec["n"] == 2

        # CONTENT isolation: A's shared-canonical node keeps its ORIGINAL caption
        # ("Alice"), not B's "Alice-in-B" -- proving the MERGE keys on scoped_id
        # (inv + ":" + canonical), not on the bare canonical_id.
        with driver.session(database="neo4j") as session:
            rec = session.run(
                "MATCH (e:Entity {scoped_id: $sid}) RETURN e.caption AS caption",
                sid=inv_a + ":c_alice",
            ).single()
        assert rec["caption"] == "Alice"
    finally:
        _clean_investigation(driver, inv_a)
        _clean_investigation(driver, inv_b)
        driver.close()


def test_empty_snapshot_wipes_investigation():
    """The snapshot is the source of truth: writing an EMPTY snapshot (no
    entities, no edges) for an investigation deletes ALL of its nodes. Pins the
    "empty snapshot wipes the investigation" REPLACE semantics. An empty snapshot
    is consumable (no edges -> nothing can dangle)."""
    import scripts.entity_graph_neo4j as graph

    inv = "test_empty_snapshot_wipes_investigation_1"
    driver = _driver_or_skip()
    try:
        graph.ensure_schema(driver)
        _clean_investigation(driver, inv)

        graph.write(driver, _two_entity_one_edge(inv))
        assert _entity_count(driver, inv) == 2
        assert _rel_count(driver, inv) == 1

        # An EMPTY snapshot for the SAME investigation wipes it entirely.
        graph.write(driver, _canned_snapshot(inv, entities=[], edges=[]))
        assert _entity_count(driver, inv) == 0
        assert _rel_count(driver, inv) == 0
    finally:
        _clean_investigation(driver, inv)
        driver.close()


def test_write_rejects_dangling_edge():
    """write() fails fast (AssertionError) on a snapshot whose edge references a
    canonical_id absent from entities, BEFORE any partial DB write (fix: the
    assert_snapshot_consumable guard at the top of write)."""
    import scripts.entity_graph_neo4j as graph

    inv = "test_write_rejects_dangling_edge_1"
    driver = _driver_or_skip()
    try:
        graph.ensure_schema(driver)
        _clean_investigation(driver, inv)

        # Edge tail "c_ghost" is NOT among the entities -> dangling.
        bad = _canned_snapshot(
            inv,
            entities=[_entity("c_alice", caption="Alice")],
            edges=[_edge("e_bad", "c_alice", "c_ghost")],
        )
        with pytest.raises(AssertionError):
            graph.write(driver, bad)

        # Fail-fast: nothing was written (the guard runs before any round-trip).
        assert _entity_count(driver, inv) == 0
    finally:
        _clean_investigation(driver, inv)
        driver.close()


def test_null_role_edge_is_stored():
    """An edge with role=None is written without crashing and is queryable with
    a null r.role (role may legitimately be absent)."""
    import scripts.entity_graph_neo4j as graph

    inv = "test_null_role_edge_is_stored_1"
    driver = _driver_or_skip()
    try:
        graph.ensure_schema(driver)
        _clean_investigation(driver, inv)

        snap = _canned_snapshot(
            inv,
            entities=[
                _entity("c_alice", caption="Alice"),
                _entity("c_bob", caption="Bob"),
            ],
            edges=[_edge("e_ab", "c_alice", "c_bob", role=None)],
        )
        graph.write(driver, snap)

        assert _rel_count(driver, inv) == 1
        with driver.session(database="neo4j") as session:
            rec = session.run(
                "MATCH (:Entity {investigation_id: $inv})"
                "-[r:REL {edge_id: 'e_ab'}]->() RETURN r.role AS role",
                inv=inv,
            ).single()
        assert rec["role"] is None
    finally:
        _clean_investigation(driver, inv)
        driver.close()

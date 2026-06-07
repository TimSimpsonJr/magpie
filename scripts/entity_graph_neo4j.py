"""ASCII only. The Neo4j graph writer -- the ONLY neo4j-driver importer.

Phase 13a, Track B, Layer-2 (Docker tier). Consumes the portable resolved
snapshot (scripts/entity_resolved_snapshot.build_snapshot) and writes it to
Neo4j as an investigation-SCOPED REPLACE (design D4): MERGE the snapshot's
current entities + relationships keyed on a synthesized per-investigation
scoped_id, then DELETE only the in-scope rows that the snapshot no longer
contains. It NEVER reads or deletes another investigation's subgraph -- the
load-bearing D4 isolation property -- so a re-run with changed cluster
membership cleanly replaces THIS investigation's graph and is idempotent.

This module imports the neo4j driver at top level. That is Windows-safe ONLY
when the driver is installed (it lives in requirements-graph.txt, kept out of
requirements-dev.txt); the module is Docker-tier, so if the driver is absent it
simply fails to import, which is fine -- nothing offline imports it. Its tests
import neo4j + this module INSIDE their bodies and are neo4j-marked, so the
offline suite collects them without ImportError.

Scoped-identity model (D2/D4), Neo4j-Community-safe:
  - entity scoped_id    = investigation_id + ":" + canonical_id  (UNIQUE constraint)
  - edge   edge_scoped_id = investigation_id + ":" + edge_id      (MERGE key)
  A single-property uniqueness constraint on scoped_id is Community-supported; a
  composite (investigation_id, canonical_id) NODE KEY is Enterprise-only and is
  deliberately NOT used. Community cannot enforce a relationship-uniqueness
  constraint either, so relationship idempotence comes from MERGE on
  edge_scoped_id, not a constraint.

Property storage: Neo4j node/relationship properties cannot be nested maps, so
each entity/edge `properties` dict is stored as a JSON STRING (properties_json),
never as a map. aliases / member_ids / provenance_refs are lists of strings and
are stored as native Neo4j arrays. resolver_id / role may be null.

Relationship type: a FIXED type :REL is used, with the FtM schema kept as the
r.schema property. Dynamic relationship types would need APOC (apoc.merge.*),
which a Community install may lack, so the type is fixed and the schema is a
property instead.

The write functions take an ALREADY-CONSTRUCTED driver (injected), so tests
control the connection; this module never constructs a driver itself.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from neo4j import GraphDatabase  # noqa: F401  (the only neo4j-driver import surface)

from scripts import entity_resolved_snapshot  # pure stdlib; does NOT taint the neo4j-only-importer status


@dataclass
class WriteStats:
    """Counters reported by an investigation-scoped REPLACE (from the tx counters).

    nodes_created / relationships_created come from the MERGE phase (only newly
    inserted rows count, so an idempotent re-run reports 0). nodes_deleted /
    relationships_deleted come from the in-scope orphan DELETE phase.
    properties_set is the total property writes across the transaction.
    """

    nodes_created: int = 0
    nodes_deleted: int = 0
    relationships_created: int = 0
    relationships_deleted: int = 0
    properties_set: int = 0


# --- Cypher (each constant is ONE statement) --------------------------------

# Single-property uniqueness on the synthesized scoped_id. Community-supported.
# A composite (investigation_id, canonical_id) NODE KEY is Enterprise-only and
# is intentionally avoided -- scoped_id gives the same scoped-identity guarantee.
_CONSTRAINT_ENTITY_SCOPED_ID = (
    "CREATE CONSTRAINT entity_scoped_id IF NOT EXISTS "
    "FOR (e:Entity) REQUIRE e.scoped_id IS UNIQUE"
)

# Supporting index so the investigation-scoped MATCHes (MERGE/DELETE) are cheap.
_INDEX_ENTITY_INVESTIGATION = (
    "CREATE INDEX entity_investigation IF NOT EXISTS "
    "FOR (e:Entity) ON (e.investigation_id)"
)

# (a) MERGE the snapshot's entities on scoped_id; SET all scalar/array props.
# properties is stored as the JSON string properties_json (Neo4j forbids nested
# maps). MERGE makes a re-run upsert the same node -> idempotent.
_MERGE_ENTITIES = (
    "UNWIND $entities AS ent "
    "MERGE (e:Entity {scoped_id: ent.scoped_id}) "
    "SET e.investigation_id = $inv, "
    "e.canonical_id = ent.canonical_id, "
    "e.schema = ent.schema, "
    "e.caption = ent.caption, "
    "e.aliases = ent.aliases, "
    "e.member_ids = ent.member_ids, "
    "e.resolver_id = ent.resolver_id, "
    "e.properties_json = ent.properties_json, "
    "e.provenance_refs = ent.provenance_refs"
)

# (b) MERGE each relationship on edge_scoped_id between its in-scope endpoints.
# A FIXED type :REL is used; the FtM schema is the r.schema property (dynamic
# rel types need APOC, which Community may lack). MATCH (not MERGE) the
# endpoints: they were created by (a), and we must not conjure stub nodes.
_MERGE_EDGES = (
    "UNWIND $edges AS ed "
    "MATCH (h:Entity {scoped_id: ed.head_scoped_id}), "
    "(t:Entity {scoped_id: ed.tail_scoped_id}) "
    "MERGE (h)-[r:REL {edge_scoped_id: ed.edge_scoped_id}]->(t) "
    "SET r.investigation_id = $inv, "
    "r.edge_id = ed.edge_id, "
    "r.schema = ed.schema, "
    "r.role = ed.role, "
    "r.properties_json = ed.properties_json, "
    "r.provenance_refs = ed.provenance_refs"
)

# (c) DELETE in-scope relationships absent from the snapshot. Both endpoints'
# nodes may SURVIVE; only the stale edge is removed. Scoped to $inv on BOTH the
# source node and the relationship so no other investigation is touched.
_DELETE_ORPHAN_EDGES = (
    "MATCH (:Entity {investigation_id: $inv})-[r:REL {investigation_id: $inv}]->() "
    "WHERE NOT r.edge_id IN $keep_edge_ids "
    "DELETE r"
)

# (d) DETACH DELETE in-scope entities absent from the snapshot (DETACH removes
# any of their remaining rels too). Scoped to $inv, so a different
# investigation's nodes are never read or deleted (the D4 isolation).
_DELETE_ORPHAN_ENTITIES = (
    "MATCH (e:Entity {investigation_id: $inv}) "
    "WHERE NOT e.canonical_id IN $keep_canonical_ids "
    "DETACH DELETE e"
)


def ensure_schema(driver) -> None:
    """Create the constraint + index (idempotent; IF NOT EXISTS).

    Single-property uniqueness on Entity.scoped_id (Community-safe) plus a
    supporting index on Entity.investigation_id. Safe to call repeatedly. Takes
    an injected, already-constructed driver.
    """
    with driver.session(database="neo4j") as session:
        session.run(_CONSTRAINT_ENTITY_SCOPED_ID)
        session.run(_INDEX_ENTITY_INVESTIGATION)


def _entity_rows(investigation_id: str, snapshot: dict) -> list[dict]:
    """Flatten snapshot entities into Cypher UNWIND rows (properties -> JSON)."""
    rows = []
    for ent in snapshot["entities"]:
        canonical_id = ent["canonical_id"]
        rows.append(
            {
                "scoped_id": investigation_id + ":" + canonical_id,
                "canonical_id": canonical_id,
                "schema": ent["schema"],
                "caption": ent["caption"],
                "aliases": ent.get("aliases", []),
                "member_ids": ent.get("member_ids", []),
                "resolver_id": ent.get("resolver_id"),
                # Neo4j forbids nested maps -> store the properties bag as JSON.
                "properties_json": json.dumps(ent.get("properties", {}), sort_keys=True),
                "provenance_refs": ent.get("provenance_refs", []),
            }
        )
    return rows


def _edge_rows(investigation_id: str, snapshot: dict) -> list[dict]:
    """Flatten snapshot edges into Cypher UNWIND rows (properties -> JSON)."""
    rows = []
    for ed in snapshot["edges"]:
        edge_id = ed["edge_id"]
        rows.append(
            {
                "edge_scoped_id": investigation_id + ":" + edge_id,
                "edge_id": edge_id,
                "head_scoped_id": investigation_id + ":" + ed["head_canonical"],
                "tail_scoped_id": investigation_id + ":" + ed["tail_canonical"],
                "schema": ed["schema"],
                "role": ed.get("role"),
                "properties_json": json.dumps(ed.get("properties", {}), sort_keys=True),
                "provenance_refs": ed.get("provenance_refs", []),
            }
        )
    return rows


def write(driver, snapshot: dict) -> WriteStats:
    """Write the resolved snapshot to Neo4j as an investigation-scoped REPLACE.

    Steps (a-d) run inside ONE managed write transaction (execute_write, which
    retries on transient failures) so the REPLACE is atomic:
      (a) MERGE the snapshot's entities on scoped_id;
      (b) MERGE its relationships on edge_scoped_id between their endpoints;
      (c) DELETE in-scope relationships whose edge_id the snapshot dropped;
      (d) DETACH DELETE in-scope entities whose canonical_id the snapshot dropped.
    Every MATCH/MERGE/DELETE is scoped to investigation_id=$inv, so a DIFFERENT
    investigation's subgraph is never read or deleted (the D4 isolation). The tx
    counters are summed into WriteStats. Takes an injected driver.

    Ordering contract: ensure_schema(driver) MUST have been called before the
    first write() (the skill orchestrates this) -- the scoped_id uniqueness
    constraint is what makes the MERGE upsert idempotent.

    Fails fast: the snapshot is validated via
    entity_resolved_snapshot.assert_snapshot_consumable BEFORE any DB round-trip,
    so an inconsistent snapshot (e.g. a dangling edge endpoint) raises a clear
    AssertionError instead of silently producing a partial write.
    """
    entity_resolved_snapshot.assert_snapshot_consumable(snapshot)
    investigation_id = snapshot["metadata"]["investigation_id"]
    entities = _entity_rows(investigation_id, snapshot)
    edges = _edge_rows(investigation_id, snapshot)
    keep_canonical_ids = [ent["canonical_id"] for ent in snapshot["entities"]]
    keep_edge_ids = [ed["edge_id"] for ed in snapshot["edges"]]

    def _txn(tx) -> WriteStats:
        stats = WriteStats()

        result = tx.run(_MERGE_ENTITIES, entities=entities, inv=investigation_id)
        counters = result.consume().counters
        stats.nodes_created += counters.nodes_created
        stats.properties_set += counters.properties_set

        result = tx.run(_MERGE_EDGES, edges=edges, inv=investigation_id)
        counters = result.consume().counters
        stats.relationships_created += counters.relationships_created
        stats.properties_set += counters.properties_set

        result = tx.run(
            _DELETE_ORPHAN_EDGES,
            inv=investigation_id,
            keep_edge_ids=keep_edge_ids,
        )
        counters = result.consume().counters
        stats.relationships_deleted += counters.relationships_deleted

        result = tx.run(
            _DELETE_ORPHAN_ENTITIES,
            inv=investigation_id,
            keep_canonical_ids=keep_canonical_ids,
        )
        counters = result.consume().counters
        stats.nodes_deleted += counters.nodes_deleted
        # No double count: step (c) already pre-cleaned every in-scope stale rel,
        # so for a CONSISTENT snapshot this DETACH only removes rels of the nodes
        # being deleted here that survived step (c) -- which is none (any rel of a
        # to-be-deleted node is in-scope and was dropped by (c)). The += is the
        # honest tx-counter sum; the two steps cannot count the same rel twice.
        stats.relationships_deleted += counters.relationships_deleted

        return stats

    with driver.session(database="neo4j") as session:
        return session.execute_write(_txn)

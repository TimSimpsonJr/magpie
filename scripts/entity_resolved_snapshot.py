"""ASCII only. The portable resolved-snapshot schema + serializer.

Phase 13a deliverable / the 13a->13b seam (design D3): a PURE stdlib module
that defines the in-memory resolved-graph schema (top-level entities + edges,
NOT cluster-nested, so a cross-cluster edge has ONE unambiguous owner),
serializes it to the durable snapshot dict, and provides the 13a/13b contract
check (the in-memory analogue of entity_ftmize.assert_phase13_consumable).

Imports NO nomenklatura / neo4j / followthemoney and reads NO clock --
generated_at is injected by the caller, so identical inputs yield an identical
dict (determinism). Tested Windows-golden.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ResolvedEntity:
    """One resolved cluster: a real-world person/org and its Phase-12 members."""

    canonical_id: str
    schema: str
    caption: str  # representative display name
    aliases: list[str] = field(default_factory=list)
    member_ids: list[str] = field(default_factory=list)  # Phase-12 node ids in cluster
    properties: dict[str, list[str]] = field(default_factory=dict)
    resolver_id: Optional[str] = None  # the nomenklatura NK- id, or None (metadata)
    provenance_refs: list[str] = field(default_factory=list)


@dataclass
class ResolvedEdge:
    """One resolved relationship between two canonical entities."""

    edge_id: str
    schema: str
    head_canonical: str
    tail_canonical: str
    role: Optional[str] = None
    properties: dict[str, list[str]] = field(default_factory=dict)
    provenance_refs: list[str] = field(default_factory=list)


def build_snapshot(
    entities: list[ResolvedEntity],
    edges: list[ResolvedEdge],
    provenance: list[dict],
    *,
    investigation_id: str,
    algorithm: str,
    thresholds: dict,
    generated_at: Any,
    snapshot_version: str = "1.0",
) -> dict:
    """Serialize the resolved graph to the durable snapshot dict (design D3).

    entities / edges are TOP-LEVEL lists (not nested per cluster) so a
    cross-cluster edge is owned ONCE. Each dataclass is emitted with every field
    present (dataclasses.asdict). provenance is the list of refs given, passed
    through unchanged. generated_at is INJECTED by the caller (string or epoch);
    this module reads no clock, so the same inputs produce an identical dict.
    """
    return {
        "metadata": {
            "investigation_id": investigation_id,
            "algorithm": algorithm,
            "thresholds": thresholds,
            "generated_at": generated_at,
            "snapshot_version": snapshot_version,
        },
        "entities": [dataclasses.asdict(e) for e in entities],
        "edges": [dataclasses.asdict(edge) for edge in edges],
        "provenance": list(provenance),
    }


def assert_snapshot_consumable(snapshot: dict) -> None:
    """The 13a/13b contract check (pure; analogue of assert_phase13_consumable).

    Raises AssertionError (with the offending id named) if the snapshot is not
    safe for 13b to consume:
      a. top-level keys metadata/entities/edges/provenance present;
      b. metadata is a dict carrying a non-empty investigation_id;
      c. every edge endpoint references a known entity canonical_id (no dangling
         head_canonical / tail_canonical);
      d. every entity / edge provenance_ref resolves to a provenance ref_id.
    """
    # (a) top-level keys.
    for key in ("metadata", "entities", "edges", "provenance"):
        assert key in snapshot, "snapshot missing top-level key: %s" % key

    # (b) metadata + investigation_id.
    metadata = snapshot["metadata"]
    assert isinstance(metadata, dict), "snapshot metadata is not a dict"
    investigation_id = metadata.get("investigation_id")
    assert investigation_id, "snapshot metadata has a missing/empty investigation_id"

    # (c) edge endpoints reference a known canonical_id.
    known = {e["canonical_id"] for e in snapshot["entities"]}
    for edge in snapshot["edges"]:
        assert edge["head_canonical"] in known, (
            "edge %s has a dangling head_canonical: %s"
            % (edge["edge_id"], edge["head_canonical"])
        )
        assert edge["tail_canonical"] in known, (
            "edge %s has a dangling tail_canonical: %s"
            % (edge["edge_id"], edge["tail_canonical"])
        )

    # (d) provenance_refs resolve to a known ref_id.
    known_refs = {p["ref_id"] for p in snapshot["provenance"]}
    for entity in snapshot["entities"]:
        for ref in entity["provenance_refs"]:
            assert ref in known_refs, (
                "entity %s has an unresolved provenance_ref: %s"
                % (entity["canonical_id"], ref)
            )
    for edge in snapshot["edges"]:
        for ref in edge["provenance_refs"]:
            assert ref in known_refs, (
                "edge %s has an unresolved provenance_ref: %s"
                % (edge["edge_id"], ref)
            )

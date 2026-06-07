"""Task 6: orchestrator + input gate + fake extractor tests.

All tests use fake extractors -- no model weights required.
ASCII only.
"""
from __future__ import annotations

import pytest

from scripts.entity_taxonomy import resolve
from scripts.entity_extract import ExtractResult, extract, ReviewQueue
from tests.conftest_entity import FakeEntityExtractor, FakeRelationExtractor

TAXONOMY = resolve("generic")
NAMESPACE = "test_pipeline"


# ---------------------------------------------------------------------------
# Helper builder
# ---------------------------------------------------------------------------

def _doc(pages, *, trustworthy=True, doc_id="doc-001"):
    return {
        "doc_id": doc_id,
        "trustworthy_for_extraction": trustworthy,
        "pages": [{"page_no": i + 1, "text": t} for i, t in enumerate(pages)],
    }


# ---------------------------------------------------------------------------
# 1. Non-trustworthy doc -> refused
# ---------------------------------------------------------------------------

def test_refused_on_untrusted_doc():
    doc = _doc(["Jane Smith works at Greenville Police Department."], trustworthy=False)
    entity_ext = FakeEntityExtractor([("Jane Smith", "person", 0.9)])
    relation_ext = FakeRelationExtractor([])
    result = extract(
        doc,
        taxonomy=TAXONOMY,
        namespace=NAMESPACE,
        entity_extractor=entity_ext,
        relation_extractor=relation_ext,
    )
    assert isinstance(result, ExtractResult)
    assert result.refused is True
    assert result.nodes == []
    assert result.edges == []
    assert result.review_queue.all_statements() == []
    assert any("trustworthy" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 2. Trustworthy 2-page doc: nodes, edges, queue provenance
# ---------------------------------------------------------------------------

def test_two_page_doc_nodes_edges_statements():
    page1 = "Jane Smith is a member of Greenville Police Department."
    page2 = "Greenville Police Department issued a report on Jane Smith."

    entity_ext = FakeEntityExtractor([
        ("Jane Smith", "person", 0.9),
        ("Greenville Police Department", "government agency", 0.9),
    ])
    relation_ext = FakeRelationExtractor([
        ("Jane Smith", "Greenville Police Department", "member of", 0.8),
    ])

    doc = _doc([page1, page2])
    result = extract(
        doc,
        taxonomy=TAXONOMY,
        namespace=NAMESPACE,
        entity_extractor=entity_ext,
        relation_extractor=relation_ext,
    )

    assert result.refused is False
    assert result.warnings == []

    # Nodes: Person + Organization (by FtM schema)
    schemas = {n.schema for n in result.nodes}
    names = {n.name for n in result.nodes}
    assert "Person" in schemas
    assert "Organization" in schemas or "PublicAuthority" in schemas
    assert "Jane Smith" in names
    assert "Greenville Police Department" in names

    # Edge: schema is Membership
    assert len(result.edges) >= 1
    edge_schemas = {e.schema for e in result.edges}
    assert "Membership" in edge_schemas

    # Review queue has statements with correct doc_id and page coverage
    stmts = result.review_queue.all_statements()
    assert len(stmts) > 0
    doc_ids = {s.doc_id for s in stmts}
    assert "doc-001" in doc_ids

    # All statements are pending
    assert all(s.decision == "pending" for s in stmts)

    # Entity statements (kind=="entity") carry correct page numbers
    entity_stmts = [s for s in stmts if s.kind == "entity"]
    assert len(entity_stmts) >= 2  # at least one per page

    # Relation statement exists (kind=="relation")
    rel_stmts = [s for s in stmts if s.kind == "relation"]
    assert len(rel_stmts) >= 1
    rel_stmt = rel_stmts[0]
    assert rel_stmt.doc_id == "doc-001"
    assert rel_stmt.char_start >= 0
    assert rel_stmt.char_end > rel_stmt.char_start


# ---------------------------------------------------------------------------
# 3. Disallowed relation (tail->head reversed) yields no edge, no rel statement
# ---------------------------------------------------------------------------

def test_disallowed_relation_yields_no_edge():
    page1 = "Greenville Police Department employs Jane Smith."

    entity_ext = FakeEntityExtractor([
        ("Jane Smith", "person", 0.9),
        ("Greenville Police Department", "government agency", 0.9),
    ])
    # Reversed direction: agency -> person for "member of" is disallowed
    relation_ext = FakeRelationExtractor([
        ("Greenville Police Department", "Jane Smith", "member of", 0.8),
    ])

    doc = _doc([page1])
    result = extract(
        doc,
        taxonomy=TAXONOMY,
        namespace=NAMESPACE,
        entity_extractor=entity_ext,
        relation_extractor=relation_ext,
    )

    assert result.edges == []
    rel_stmts = [s for s in result.review_queue.all_statements() if s.kind == "relation"]
    assert rel_stmts == []


# ---------------------------------------------------------------------------
# 4. Same entity on two pages -> one node id, two entity-mention statements
# ---------------------------------------------------------------------------

def test_same_entity_two_pages_deduped_node_two_statements():
    page1 = "Jane Smith filed a complaint."
    page2 = "Jane Smith received a response."

    entity_ext = FakeEntityExtractor([
        ("Jane Smith", "person", 0.9),
    ])
    relation_ext = FakeRelationExtractor([])

    doc = _doc([page1, page2])
    result = extract(
        doc,
        taxonomy=TAXONOMY,
        namespace=NAMESPACE,
        entity_extractor=entity_ext,
        relation_extractor=relation_ext,
    )

    # Exactly one node (per-doc dedup)
    jane_nodes = [n for n in result.nodes if n.name == "Jane Smith"]
    assert len(jane_nodes) == 1

    # Two entity statements (one mention per page; statement ids are distinct)
    entity_stmts = [s for s in result.review_queue.all_statements() if s.kind == "entity"]
    jane_stmts = [s for s in entity_stmts if s.target_id == jane_nodes[0].id]
    assert len(jane_stmts) == 2
    stmt_ids = {s.statement_id for s in jane_stmts}
    assert len(stmt_ids) == 2  # distinct ids
    pages = {s.page for s in jane_stmts}
    assert pages == {1, 2}

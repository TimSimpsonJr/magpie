"""Tests for Task 4: statements, review queue, intermediate bundle.

RED -> GREEN TDD; run with:
    .venv/Scripts/python.exe -m pytest tests/test_entity_extract_bundle.py -q
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from scripts.entity_extract import (
    Node,
    Edge,
    stable_id,
    # Task 4 additions (will fail RED until implemented)
    statement_id,
    Mention,
    Statement,
    build_statements,
    ReviewQueue,
    build_intermediate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mention(**kwargs) -> Mention:
    defaults = dict(
        target_id="node-abc",
        target_kind="entity",
        schema="Person",
        prop="name",
        value="Alice",
        doc_id="doc-1",
        page=1,
        char_start=10,
        char_end=15,
        model="test-model",
        confidence=0.9,
    )
    defaults.update(kwargs)
    return Mention(**defaults)


# ---------------------------------------------------------------------------
# statement_id
# ---------------------------------------------------------------------------

class TestStatementId:
    def test_same_args_same_id(self):
        a = statement_id("ns", "doc-1", 1, 10, 15, "node-abc", "name")
        b = statement_id("ns", "doc-1", 1, 10, 15, "node-abc", "name")
        assert a == b

    def test_same_args_match_stable_id(self):
        sid = statement_id("ns", "doc-1", 1, 10, 15, "node-abc", "name")
        expected = stable_id("ns", "doc-1", 1, 10, 15, "node-abc", "name")
        assert sid == expected

    def test_different_page_different_id(self):
        a = statement_id("ns", "doc-1", 1, 10, 15, "node-abc", "name")
        b = statement_id("ns", "doc-1", 2, 10, 15, "node-abc", "name")
        assert a != b

    def test_returns_40_chars(self):
        sid = statement_id("ns", "doc-1", 1, 0, 5, "node-x", "prop")
        assert len(sid) == 40
        assert all(c in "0123456789abcdef" for c in sid)


# ---------------------------------------------------------------------------
# Mention dataclass
# ---------------------------------------------------------------------------

class TestMention:
    def test_frozen(self):
        m = _mention()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
            m.value = "changed"  # type: ignore[misc]

    def test_fields_present(self):
        m = _mention()
        assert m.target_id == "node-abc"
        assert m.target_kind == "entity"
        assert m.schema == "Person"
        assert m.prop == "name"
        assert m.value == "Alice"
        assert m.doc_id == "doc-1"
        assert m.page == 1
        assert m.char_start == 10
        assert m.char_end == 15
        assert m.model == "test-model"
        assert m.confidence == 0.9


# ---------------------------------------------------------------------------
# build_statements
# ---------------------------------------------------------------------------

class TestBuildStatements:
    def _make_mentions(self, n: int = 3) -> list[Mention]:
        return [
            _mention(char_start=i * 20, char_end=i * 20 + 5, page=i + 1)
            for i in range(n)
        ]

    def test_count_matches(self):
        mentions = self._make_mentions(4)
        stmts = build_statements(mentions, "ns")
        assert len(stmts) == 4

    def test_all_pending(self):
        stmts = build_statements(self._make_mentions(3), "ns")
        assert all(s.decision == "pending" for s in stmts)

    def test_ids_match_statement_id(self):
        mentions = self._make_mentions(3)
        stmts = build_statements(mentions, "ns")
        for m, s in zip(mentions, stmts):
            expected = statement_id("ns", m.doc_id, m.page, m.char_start, m.char_end, m.target_id, m.prop)
            assert s.statement_id == expected

    def test_kind_entity(self):
        m = _mention(target_kind="entity")
        stmts = build_statements([m], "ns")
        assert stmts[0].kind == "entity"

    def test_kind_relation_for_edge(self):
        m = _mention(target_kind="edge")
        stmts = build_statements([m], "ns")
        assert stmts[0].kind == "relation"

    def test_returns_statement_objects(self):
        stmts = build_statements(self._make_mentions(1), "ns")
        assert all(isinstance(s, Statement) for s in stmts)

    def test_empty_input(self):
        assert build_statements([], "ns") == []


# ---------------------------------------------------------------------------
# ReviewQueue
# ---------------------------------------------------------------------------

def _make_queue(n: int = 3, **mention_kwargs) -> tuple[ReviewQueue, list[Statement]]:
    mentions = [
        _mention(char_start=i * 20, char_end=i * 20 + 5, page=i + 1, **mention_kwargs)
        for i in range(n)
    ]
    stmts = build_statements(mentions, "test-ns")
    return ReviewQueue(stmts), stmts


class TestReviewQueueBasic:
    def test_all_statements_returns_all(self):
        q, stmts = _make_queue(3)
        assert len(q.all_statements()) == 3

    def test_pending_initially_all(self):
        q, stmts = _make_queue(3)
        assert len(q.pending()) == 3

    def test_accepted_initially_empty(self):
        q, _ = _make_queue(3)
        assert q.accepted() == []

    def test_get_existing(self):
        q, stmts = _make_queue(2)
        s = stmts[0]
        assert q.get(s.statement_id) == s

    def test_get_missing_returns_none(self):
        q, _ = _make_queue(2)
        assert q.get("nonexistent-id") is None


class TestReviewQueueDecide:
    def test_decide_accepted_moves_to_accepted(self):
        q, stmts = _make_queue(3)
        sid = stmts[0].statement_id
        q.decide(sid, "accepted", reviewer="tim")
        assert len(q.accepted()) == 1
        assert q.accepted()[0].statement_id == sid

    def test_decide_accepted_out_of_pending(self):
        q, stmts = _make_queue(3)
        sid = stmts[0].statement_id
        q.decide(sid, "accepted")
        pending_ids = [s.statement_id for s in q.pending()]
        assert sid not in pending_ids

    def test_decide_rejected_not_in_accepted(self):
        q, stmts = _make_queue(3)
        sid = stmts[0].statement_id
        q.decide(sid, "rejected")
        assert len(q.accepted()) == 0

    def test_decide_rejected_not_in_pending(self):
        q, stmts = _make_queue(3)
        sid = stmts[0].statement_id
        q.decide(sid, "rejected")
        pending_ids = [s.statement_id for s in q.pending()]
        assert sid not in pending_ids

    def test_decide_invalid_raises_value_error(self):
        q, stmts = _make_queue(2)
        sid = stmts[0].statement_id
        with pytest.raises(ValueError):
            q.decide(sid, "bogus")

    def test_decide_missing_raises_key_error(self):
        q, _ = _make_queue(2)
        with pytest.raises(KeyError):
            q.decide("no-such-id", "accepted")

    def test_reviewer_stored(self):
        q, stmts = _make_queue(2)
        sid = stmts[0].statement_id
        q.decide(sid, "accepted", reviewer="alice")
        accepted = q.accepted()[0]
        assert accepted.reviewer == "alice"


class TestReviewQueueEdit:
    def test_edit_original_becomes_edited(self):
        q, stmts = _make_queue(2)
        orig_id = stmts[0].statement_id
        q.edit(orig_id, "New Value")
        orig = q.get(orig_id)
        assert orig.decision == "edited"

    def test_edit_original_has_superseded_by(self):
        q, stmts = _make_queue(2)
        orig_id = stmts[0].statement_id
        expected_new_id = stable_id(orig_id, "edit", 1)
        q.edit(orig_id, "New Value")
        orig = q.get(orig_id)
        assert orig.superseded_by == expected_new_id

    def test_edit_new_statement_appended(self):
        q, stmts = _make_queue(2)
        orig_id = stmts[0].statement_id
        replacement = q.edit(orig_id, "New Value")
        expected_new_id = stable_id(orig_id, "edit", 1)
        assert replacement.statement_id == expected_new_id

    def test_edit_new_id_different_from_orig(self):
        q, stmts = _make_queue(2)
        orig_id = stmts[0].statement_id
        replacement = q.edit(orig_id, "New Value")
        assert replacement.statement_id != orig_id

    def test_edit_new_value_stored(self):
        q, stmts = _make_queue(2)
        orig_id = stmts[0].statement_id
        replacement = q.edit(orig_id, "New Value")
        assert replacement.value == "New Value"

    def test_edit_new_statement_accepted(self):
        q, stmts = _make_queue(2)
        orig_id = stmts[0].statement_id
        replacement = q.edit(orig_id, "New Value")
        assert replacement.decision == "accepted"

    def test_edit_new_statement_supersedes_original(self):
        q, stmts = _make_queue(2)
        orig_id = stmts[0].statement_id
        replacement = q.edit(orig_id, "New Value")
        assert replacement.supersedes == orig_id

    def test_edit_new_statement_has_reviewer(self):
        q, stmts = _make_queue(2)
        orig_id = stmts[0].statement_id
        replacement = q.edit(orig_id, "New Value", reviewer="bob")
        assert replacement.reviewer == "bob"

    def test_second_edit_ordinal_2(self):
        q, stmts = _make_queue(2)
        orig_id = stmts[0].statement_id
        first = q.edit(orig_id, "Edit 1")
        second = q.edit(orig_id, "Edit 2")
        expected_second_id = stable_id(orig_id, "edit", 2)
        assert second.statement_id == expected_second_id

    def test_second_edit_distinct_from_first(self):
        q, stmts = _make_queue(2)
        orig_id = stmts[0].statement_id
        first = q.edit(orig_id, "Edit 1")
        second = q.edit(orig_id, "Edit 2")
        assert first.statement_id != second.statement_id

    def test_edit_missing_raises_key_error(self):
        q, _ = _make_queue(2)
        with pytest.raises(KeyError):
            q.edit("no-such-id", "anything")

    def test_edit_appends_to_list(self):
        q, stmts = _make_queue(2)
        orig_id = stmts[0].statement_id
        q.edit(orig_id, "New Value")
        # total statements = 2 original + 1 appended replacement
        assert len(q.all_statements()) == 3


class TestReviewQueueJsonl:
    def test_round_trip(self):
        q, stmts = _make_queue(3)
        q.decide(stmts[0].statement_id, "accepted", reviewer="tim")
        q.decide(stmts[1].statement_id, "rejected")
        jsonl = q.to_jsonl()
        q2 = ReviewQueue.from_jsonl(jsonl)
        assert q2.all_statements() == q.all_statements()

    def test_to_jsonl_produces_valid_json_lines(self):
        q, _ = _make_queue(2)
        lines = q.to_jsonl().strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert "statement_id" in obj

    def test_from_jsonl_ignores_empty_lines(self):
        q, _ = _make_queue(2)
        jsonl = q.to_jsonl()
        # Insert blank lines
        padded = "\n" + jsonl + "\n\n"
        q2 = ReviewQueue.from_jsonl(padded)
        assert len(q2.all_statements()) == 2


# ---------------------------------------------------------------------------
# build_intermediate
# ---------------------------------------------------------------------------

def _make_node(name: str, schema: str = "Person") -> Node:
    nid = stable_id("test-ns", "doc-1", schema, name.casefold())
    return Node(id=nid, schema=schema, name=name, label="PER")


def _make_edge(head: Node, tail: Node, label: str = "knows") -> Edge:
    eid = stable_id("test-ns", "Relation", head.id, tail.id, label)
    return Edge(id=eid, schema="Relation", head_id=head.id, tail_id=tail.id, role=label, label=label)


class TestBuildIntermediate:
    def _setup(self):
        node_a = _make_node("Alice")
        node_b = _make_node("Bob")
        edge_ab = _make_edge(node_a, node_b)

        # Mentions for both nodes and the edge
        m_a = _mention(target_id=node_a.id, target_kind="entity", schema="Person", prop="name", value="Alice", page=1, char_start=0, char_end=5)
        m_b = _mention(target_id=node_b.id, target_kind="entity", schema="Person", prop="name", value="Bob", page=1, char_start=10, char_end=13)
        m_e = _mention(target_id=edge_ab.id, target_kind="edge", schema="Relation", prop="label", value="knows", page=1, char_start=5, char_end=10)

        stmts = build_statements([m_a, m_b, m_e], "test-ns")
        q = ReviewQueue(stmts)
        return q, stmts, node_a, node_b, edge_ab

    def test_only_accepted_nodes_in_bundle(self):
        q, stmts, node_a, node_b, edge_ab = self._setup()
        # Accept only node_a's statement
        q.decide(stmts[0].statement_id, "accepted")  # node_a
        # stmts[1] (node_b) stays pending
        # stmts[2] (edge_ab) stays pending
        bundle, warnings = build_intermediate(
            q, [node_a, node_b], [edge_ab],
            namespace="test-ns", source_doc_ids=["doc-1"]
        )
        assert len(bundle["nodes"]) == 1
        assert bundle["nodes"][0]["id"] == node_a.id

    def test_rejected_statement_target_excluded(self):
        q, stmts, node_a, node_b, edge_ab = self._setup()
        q.decide(stmts[0].statement_id, "rejected")  # node_a rejected
        q.decide(stmts[1].statement_id, "accepted")  # node_b accepted
        bundle, warnings = build_intermediate(
            q, [node_a, node_b], [],
            namespace="test-ns", source_doc_ids=["doc-1"]
        )
        node_ids = [n["id"] for n in bundle["nodes"]]
        assert node_a.id not in node_ids
        assert node_b.id in node_ids

    def test_accepted_edge_with_both_nodes_accepted_included(self):
        q, stmts, node_a, node_b, edge_ab = self._setup()
        q.decide(stmts[0].statement_id, "accepted")  # node_a
        q.decide(stmts[1].statement_id, "accepted")  # node_b
        q.decide(stmts[2].statement_id, "accepted")  # edge_ab
        bundle, warnings = build_intermediate(
            q, [node_a, node_b], [edge_ab],
            namespace="test-ns", source_doc_ids=["doc-1"]
        )
        assert len(bundle["edges"]) == 1
        assert bundle["edges"][0]["id"] == edge_ab.id
        assert warnings == []

    def test_closure_drops_edge_when_endpoint_not_accepted(self):
        """Graph-closure: accepted edge A->B where B not accepted is DROPPED with warning."""
        q, stmts, node_a, node_b, edge_ab = self._setup()
        q.decide(stmts[0].statement_id, "accepted")  # node_a accepted
        # stmts[1] (node_b) NOT accepted
        q.decide(stmts[2].statement_id, "accepted")  # edge_ab accepted
        bundle, warnings = build_intermediate(
            q, [node_a, node_b], [edge_ab],
            namespace="test-ns", source_doc_ids=["doc-1"]
        )
        assert len(bundle["edges"]) == 0
        assert len(warnings) == 1
        assert edge_ab.id in warnings[0]

    def test_provenance_one_row_per_accepted(self):
        q, stmts, node_a, node_b, edge_ab = self._setup()
        q.decide(stmts[0].statement_id, "accepted")  # node_a
        q.decide(stmts[1].statement_id, "accepted")  # node_b
        q.decide(stmts[2].statement_id, "accepted")  # edge_ab
        bundle, _ = build_intermediate(
            q, [node_a, node_b], [edge_ab],
            namespace="test-ns", source_doc_ids=["doc-1"]
        )
        assert len(bundle["provenance"]) == 3

    def test_provenance_has_reviewed_true(self):
        q, stmts, node_a, node_b, edge_ab = self._setup()
        q.decide(stmts[0].statement_id, "accepted")
        bundle, _ = build_intermediate(
            q, [node_a, node_b], [],
            namespace="test-ns", source_doc_ids=["doc-1"]
        )
        assert all(p["reviewed"] is True for p in bundle["provenance"])

    def test_counts_match(self):
        q, stmts, node_a, node_b, edge_ab = self._setup()
        q.decide(stmts[0].statement_id, "accepted")  # node_a
        q.decide(stmts[1].statement_id, "accepted")  # node_b
        q.decide(stmts[2].statement_id, "accepted")  # edge_ab
        bundle, _ = build_intermediate(
            q, [node_a, node_b], [edge_ab],
            namespace="test-ns", source_doc_ids=["doc-1"]
        )
        counts = bundle["counts"]
        assert counts["nodes"] == len(bundle["nodes"])
        assert counts["edges"] == len(bundle["edges"])
        assert counts["provenance"] == len(bundle["provenance"])

    def test_schema_version_and_namespace(self):
        q, stmts, node_a, node_b, edge_ab = self._setup()
        q.decide(stmts[0].statement_id, "accepted")
        bundle, _ = build_intermediate(
            q, [node_a, node_b], [],
            namespace="my-ns", source_doc_ids=["doc-1"],
            schema_version="2.0"
        )
        assert bundle["schema_version"] == "2.0"
        assert bundle["dataset_namespace"] == "my-ns"

    def test_created_with_stored(self):
        q, stmts, node_a, node_b, edge_ab = self._setup()
        q.decide(stmts[0].statement_id, "accepted")
        bundle, _ = build_intermediate(
            q, [node_a, node_b], [],
            namespace="test-ns", source_doc_ids=["doc-1"],
            created_with={"model": "gpt-x", "version": "1"}
        )
        assert bundle["created_with"] == {"model": "gpt-x", "version": "1"}

    def test_created_with_defaults_empty_dict(self):
        q, stmts, node_a, node_b, edge_ab = self._setup()
        q.decide(stmts[0].statement_id, "accepted")
        bundle, _ = build_intermediate(
            q, [node_a, node_b], [],
            namespace="test-ns", source_doc_ids=["doc-1"]
        )
        assert bundle["created_with"] == {}

from __future__ import annotations

import pytest
from scripts.entity_taxonomy import resolve
from scripts.entity_extract import Span, Node, Edge, stable_id, make_node, make_edge


TAXONOMY = resolve("generic")


def _span(text: str, label: str, start: int = 0, end: int = None) -> Span:
    if end is None:
        end = start + len(text)
    return Span(text=text, label=label, char_start=start, char_end=end, score=1.0)


# ---------------------------------------------------------------------------
# stable_id
# ---------------------------------------------------------------------------

class TestStableId:
    def test_deterministic(self):
        assert stable_id("a", "b", "c") == stable_id("a", "b", "c")

    def test_length_40(self):
        assert len(stable_id("x")) == 40

    def test_different_parts_differ(self):
        assert stable_id("a", "b") != stable_id("a", "c")

    def test_int_parts_converted(self):
        # Should not crash with int parts
        result = stable_id("ns", 42, "thing")
        assert isinstance(result, str)
        assert len(result) == 40


# ---------------------------------------------------------------------------
# make_node -- determinism and schema resolution
# ---------------------------------------------------------------------------

class TestMakeNode:
    def test_deterministic_same_call(self):
        span = _span("Alice", "person")
        n1 = make_node(span, "doc1", "test_ns", TAXONOMY)
        n2 = make_node(span, "doc1", "test_ns", TAXONOMY)
        assert n1.id == n2.id

    def test_person_label_gives_person_schema(self):
        span = _span("Alice", "person")
        node = make_node(span, "doc1", "test_ns", TAXONOMY)
        assert node.schema == "Person"

    def test_government_agency_gives_organization_schema(self):
        span = _span("SVPD", "government agency")
        node = make_node(span, "doc1", "test_ns", TAXONOMY)
        assert node.schema == "Organization"

    def test_company_gives_company_schema(self):
        span = _span("Acme Corp", "company")
        node = make_node(span, "doc1", "test_ns", TAXONOMY)
        assert node.schema == "Company"

    def test_name_is_stripped(self):
        span = _span("  Alice  ", "person", 0, 9)
        node = make_node(span, "doc1", "test_ns", TAXONOMY)
        assert node.name == "Alice"

    def test_label_preserved(self):
        span = _span("Alice", "person")
        node = make_node(span, "doc1", "test_ns", TAXONOMY)
        assert node.label == "person"

    def test_id_is_hex_string_40_chars(self):
        span = _span("Alice", "person")
        node = make_node(span, "doc1", "test_ns", TAXONOMY)
        assert len(node.id) == 40
        assert all(c in "0123456789abcdef" for c in node.id)


# ---------------------------------------------------------------------------
# make_node -- scoping: no cross-doc merge; within-doc dedup
# ---------------------------------------------------------------------------

class TestMakeNodeScoping:
    def test_same_name_label_same_doc_same_id(self):
        """Same name+label in ONE doc -> same id regardless of char offsets."""
        span1 = _span("Alice", "person", 0, 5)
        span2 = _span("Alice", "person", 100, 105)
        n1 = make_node(span1, "doc1", "test_ns", TAXONOMY)
        n2 = make_node(span2, "doc1", "test_ns", TAXONOMY)
        assert n1.id == n2.id

    def test_same_name_label_different_docs_different_ids(self):
        """Same name+label in TWO different doc_ids -> DIFFERENT ids."""
        span = _span("Alice", "person")
        n1 = make_node(span, "doc1", "test_ns", TAXONOMY)
        n2 = make_node(span, "doc2", "test_ns", TAXONOMY)
        assert n1.id != n2.id

    def test_different_namespace_different_ids(self):
        span = _span("Alice", "person")
        n1 = make_node(span, "doc1", "ns_a", TAXONOMY)
        n2 = make_node(span, "doc1", "ns_b", TAXONOMY)
        assert n1.id != n2.id

    def test_case_folded_for_id(self):
        """Names that differ only in case should share the same id in one doc."""
        span_upper = _span("ALICE", "person")
        span_lower = _span("alice", "person")
        n1 = make_node(span_upper, "doc1", "test_ns", TAXONOMY)
        n2 = make_node(span_lower, "doc1", "test_ns", TAXONOMY)
        # Both casefold to "alice" -> same id
        assert n1.id == n2.id

    def test_name_preserved_in_node(self):
        """The Node.name preserves original case even though id is case-folded."""
        span = _span("ALICE", "person")
        node = make_node(span, "doc1", "test_ns", TAXONOMY)
        assert node.name == "ALICE"


# ---------------------------------------------------------------------------
# make_edge -- allowed pairs
# ---------------------------------------------------------------------------

class TestMakeEdge:
    def _person_node(self, name="Alice", doc="doc1"):
        span = _span(name, "person")
        return make_node(span, doc, "test_ns", TAXONOMY)

    def _agency_node(self, name="SVPD", doc="doc1"):
        span = _span(name, "government agency")
        return make_node(span, doc, "test_ns", TAXONOMY)

    def _company_node(self, name="Acme", doc="doc1"):
        span = _span(name, "company")
        return make_node(span, doc, "test_ns", TAXONOMY)

    def _org_node(self, name="Globex", doc="doc1"):
        span = _span(name, "organization")
        return make_node(span, doc, "test_ns", TAXONOMY)

    def test_member_of_person_to_agency_returns_edge(self):
        head = self._person_node()
        tail = self._agency_node()
        edge = make_edge("member of", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge is not None
        assert isinstance(edge, Edge)

    def test_member_of_person_to_agency_schema_membership(self):
        head = self._person_node()
        tail = self._agency_node()
        edge = make_edge("member of", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge.schema == "Membership"

    def test_member_of_person_to_agency_wires_ids(self):
        head = self._person_node()
        tail = self._agency_node()
        edge = make_edge("member of", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge.head_id == head.id
        assert edge.tail_id == tail.id

    def test_member_of_person_to_agency_label_preserved(self):
        head = self._person_node()
        tail = self._agency_node()
        edge = make_edge("member of", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge.label == "member of"

    def test_disallowed_member_of_agency_to_person_returns_none(self):
        """government agency -> person via 'member of' is NOT allowed."""
        head = self._agency_node()
        tail = self._person_node()
        edge = make_edge("member of", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge is None

    def test_owns_subsidiary_company_to_org_degrades_to_unknown_link(self):
        """Ownership of a non-ownable Organization DEGRADES to UnknownLink
        (plan Task 3), not dropped -- the real signal is preserved."""
        head = self._company_node()
        tail = self._org_node()
        edge = make_edge("owns/subsidiary of", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge is not None
        assert edge.schema == "UnknownLink"
        assert edge.role == "owns/subsidiary of"
        assert edge.label == "owns/subsidiary of"

    def test_owns_subsidiary_person_to_org_degrades_to_unknown_link(self):
        """Same ownership degrade with a person head and an organization tail."""
        head = self._person_node()
        tail = self._org_node()
        edge = make_edge("owns/subsidiary of", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge is not None
        assert edge.schema == "UnknownLink"
        assert edge.role == "owns/subsidiary of"

    def test_owns_subsidiary_company_to_agency_degrades_to_unknown_link(self):
        """Ownership of a non-ownable government agency also degrades (not None)."""
        head = self._company_node()
        tail = self._agency_node()
        edge = make_edge("owns/subsidiary of", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge is not None
        assert edge.schema == "UnknownLink"
        assert edge.role == "owns/subsidiary of"

    def test_unmapped_label_gives_unknown_link(self):
        """A rel_label not in the taxonomy -> deterministic UnknownLink fallback."""
        head = self._person_node()
        tail = self._company_node()
        edge = make_edge("totally unknown relation", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge is not None
        assert edge.schema == "UnknownLink"
        assert edge.role == "totally unknown relation"
        assert edge.label == "totally unknown relation"

    def test_incompatible_known_non_ownership_pair_returns_none(self):
        """A type-incompatible KNOWN pair that is NOT ownership is dropped.

        'employed by' requires a person/official head; a company head is
        incompatible and (not being Ownership) yields None."""
        head = self._company_node()
        tail = self._company_node(name="Initech")
        edge = make_edge("employed by", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge is None

    def test_incompatible_member_of_org_head_returns_none(self):
        """'member of' requires a person/official head; an organization head is
        incompatible and (not Ownership) yields None."""
        head = self._org_node()
        tail = self._agency_node()
        edge = make_edge("member of", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge is None

    def test_affiliated_linked_person_to_company_returns_edge(self):
        head = self._person_node()
        tail = self._company_node()
        edge = make_edge("affiliated/linked", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge is not None

    def test_affiliated_linked_schema_is_unknown_link(self):
        head = self._person_node()
        tail = self._company_node()
        edge = make_edge("affiliated/linked", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge.schema == "UnknownLink"

    def test_affiliated_linked_label_preserved(self):
        head = self._person_node()
        tail = self._company_node()
        edge = make_edge("affiliated/linked", head, tail, "span_0", "test_ns", TAXONOMY)
        assert edge.label == "affiliated/linked"

    def test_edge_id_is_40_hex_chars(self):
        head = self._person_node()
        tail = self._agency_node()
        edge = make_edge("member of", head, tail, "span_0", "test_ns", TAXONOMY)
        assert len(edge.id) == 40
        assert all(c in "0123456789abcdef" for c in edge.id)

    def test_edge_id_is_deterministic(self):
        head = self._person_node()
        tail = self._agency_node()
        e1 = make_edge("member of", head, tail, "span_0", "test_ns", TAXONOMY)
        e2 = make_edge("member of", head, tail, "span_0", "test_ns", TAXONOMY)
        assert e1.id == e2.id

    def test_edge_id_differs_by_span_key(self):
        head = self._person_node()
        tail = self._agency_node()
        e1 = make_edge("member of", head, tail, "span_0", "test_ns", TAXONOMY)
        e2 = make_edge("member of", head, tail, "span_1", "test_ns", TAXONOMY)
        assert e1.id != e2.id

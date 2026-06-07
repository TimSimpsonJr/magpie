"""Tests for docling_to_extraction_input -- the DoclingDocument -> extract() adapter.

Pure tests: synthetic DoclingDocument JSON dicts (ASCII), NO models, NO docling
import. The adapter is stdlib-only and parses the already-saved DoclingDocument
JSON dict that scripts/ingest.py save_as_json writes.
"""
from __future__ import annotations

from scripts.entity_extract import docling_to_extraction_input, extract
from scripts.entity_taxonomy import resolve
from tests.conftest_entity import FakeEntityExtractor, FakeRelationExtractor


def _text_item(self_ref, text, page_no, char_start=0, char_end=0):
    """Build a DoclingDocument `texts` item with a single prov entry."""
    return {
        "self_ref": self_ref,
        "text": text,
        "prov": [
            {
                "page_no": page_no,
                "bbox": {"l": 0, "t": 0, "r": 1, "b": 1},
                "charspan": [char_start, char_end],
            }
        ],
    }


def _docling_doc(texts):
    return {
        "texts": texts,
        "pages": {},
        "schema_name": "DoclingDocument",
        "version": "1.10.0",
    }


# ---------------------------------------------------------------------------
# Multi-page reconstruction
# ---------------------------------------------------------------------------

def test_multi_page_grouped_and_sorted_by_page_no():
    doc = _docling_doc([
        _text_item("#/texts/0", "Page one alpha.", 1),
        _text_item("#/texts/1", "Page one beta.", 1),
        _text_item("#/texts/2", "Page two gamma.", 2),
    ])
    out = docling_to_extraction_input(
        doc, doc_id="sha-abc", trustworthy_for_extraction=True
    )

    assert [p["page_no"] for p in out["pages"]] == [1, 2]
    # page 1: two blocks joined in document order with a newline
    assert out["pages"][0]["text"] == "Page one alpha.\nPage one beta."
    # page 2: single block
    assert out["pages"][1]["text"] == "Page two gamma."


def test_pages_sorted_even_when_items_out_of_page_order():
    doc = _docling_doc([
        _text_item("#/texts/0", "Second page text.", 2),
        _text_item("#/texts/1", "First page text.", 1),
    ])
    out = docling_to_extraction_input(
        doc, doc_id="d1", trustworthy_for_extraction=True
    )
    assert [p["page_no"] for p in out["pages"]] == [1, 2]
    assert out["pages"][0]["text"] == "First page text."
    assert out["pages"][1]["text"] == "Second page text."


def test_document_order_preserved_within_a_page():
    # Three blocks on the same page must keep their list order in the join.
    doc = _docling_doc([
        _text_item("#/texts/0", "one", 1),
        _text_item("#/texts/1", "two", 1),
        _text_item("#/texts/2", "three", 1),
    ])
    out = docling_to_extraction_input(
        doc, doc_id="d", trustworthy_for_extraction=True
    )
    assert out["pages"][0]["text"] == "one\ntwo\nthree"


# ---------------------------------------------------------------------------
# Skip rules: empty/whitespace text, no prov, prov without page_no
# ---------------------------------------------------------------------------

def test_empty_and_whitespace_text_items_are_skipped():
    doc = _docling_doc([
        _text_item("#/texts/0", "", 1),
        _text_item("#/texts/1", "   \n\t ", 1),
        _text_item("#/texts/2", "kept", 1),
    ])
    out = docling_to_extraction_input(
        doc, doc_id="d", trustworthy_for_extraction=True
    )
    assert len(out["pages"]) == 1
    assert out["pages"][0]["text"] == "kept"


def test_item_with_no_prov_is_skipped():
    doc = _docling_doc([
        {"self_ref": "#/texts/0", "text": "no prov here", "prov": []},
        {"self_ref": "#/texts/1", "text": "also no prov"},  # prov key absent
        _text_item("#/texts/2", "has prov", 1),
    ])
    out = docling_to_extraction_input(
        doc, doc_id="d", trustworthy_for_extraction=True
    )
    assert len(out["pages"]) == 1
    assert out["pages"][0]["page_no"] == 1
    assert out["pages"][0]["text"] == "has prov"


def test_item_with_prov_missing_page_no_is_skipped():
    doc = _docling_doc([
        {
            "self_ref": "#/texts/0",
            "text": "page_no absent",
            "prov": [{"bbox": {"l": 0, "t": 0, "r": 1, "b": 1}}],
        },
        _text_item("#/texts/1", "good", 3),
    ])
    out = docling_to_extraction_input(
        doc, doc_id="d", trustworthy_for_extraction=True
    )
    assert len(out["pages"]) == 1
    assert out["pages"][0]["page_no"] == 3
    assert out["pages"][0]["text"] == "good"


def test_empty_texts_yields_no_pages():
    out = docling_to_extraction_input(
        _docling_doc([]), doc_id="d", trustworthy_for_extraction=True
    )
    assert out["pages"] == []


def test_missing_texts_key_is_tolerated():
    out = docling_to_extraction_input(
        {"schema_name": "DoclingDocument"}, doc_id="d", trustworthy_for_extraction=True
    )
    assert out["pages"] == []


# ---------------------------------------------------------------------------
# Passthrough: doc_id and trustworthy_for_extraction (as a bool)
# ---------------------------------------------------------------------------

def test_doc_id_passes_through():
    out = docling_to_extraction_input(
        _docling_doc([_text_item("#/texts/0", "x", 1)]),
        doc_id="source-sha256-value",
        trustworthy_for_extraction=True,
    )
    assert out["doc_id"] == "source-sha256-value"


def test_trustworthy_coerced_to_bool():
    out_true = docling_to_extraction_input(
        _docling_doc([_text_item("#/texts/0", "x", 1)]),
        doc_id="d",
        trustworthy_for_extraction=1,  # truthy non-bool
    )
    assert out_true["trustworthy_for_extraction"] is True

    out_false = docling_to_extraction_input(
        _docling_doc([_text_item("#/texts/0", "x", 1)]),
        doc_id="d",
        trustworthy_for_extraction=0,  # falsy non-bool
    )
    assert out_false["trustworthy_for_extraction"] is False


# ---------------------------------------------------------------------------
# End-to-end: adapter output is a valid extract() input
# ---------------------------------------------------------------------------

def test_adapter_output_drives_extract_with_correct_provenance():
    # Page 1 has two blocks; the reconstructed page text is block0 + "\n" + block1.
    block0 = "Mayor Jane Smith leads the council."
    block1 = "Acme Corp signed the deal."
    doc = _docling_doc([
        _text_item("#/texts/0", block0, 1),
        _text_item("#/texts/1", block1, 1),
    ])

    adapted = docling_to_extraction_input(
        doc, doc_id="doc-1", trustworthy_for_extraction=True
    )
    page_text = adapted["pages"][0]["text"]
    assert page_text == block0 + "\n" + block1

    entity_extractor = FakeEntityExtractor([
        ("Mayor Jane Smith", "government official", 0.9),
        ("Acme Corp", "company", 0.9),
    ])
    relation_extractor = FakeRelationExtractor([])

    result = extract(
        adapted,
        taxonomy=resolve("generic"),
        namespace="t",
        entity_extractor=entity_extractor,
        relation_extractor=relation_extractor,
        threshold=0.5,
    )

    assert result.refused is False
    stmts = result.review_queue.all_statements()
    # Two entity statements, both on page 1.
    assert len(stmts) == 2
    assert all(s.page == 1 for s in stmts)

    # The cited char span slices back to the entity surface in the reconstructed
    # page text -- proving the adapter produced a valid extract() input.
    by_value = {s.value: s for s in stmts}
    smith = by_value["Mayor Jane Smith"]
    acme = by_value["Acme Corp"]
    assert page_text[smith.char_start:smith.char_end] == "Mayor Jane Smith"
    assert page_text[acme.char_start:acme.char_end] == "Acme Corp"
    # The second block's entity is offset past block0 + the newline separator.
    assert acme.char_start == len(block0) + 1


def test_non_trustworthy_adapter_output_makes_extract_refuse():
    doc = _docling_doc([_text_item("#/texts/0", "Jane Smith is here.", 1)])
    adapted = docling_to_extraction_input(
        doc, doc_id="doc-2", trustworthy_for_extraction=False
    )
    assert adapted["trustworthy_for_extraction"] is False

    result = extract(
        adapted,
        taxonomy=resolve("generic"),
        namespace="t",
        entity_extractor=FakeEntityExtractor([("Jane Smith", "person", 0.9)]),
        relation_extractor=FakeRelationExtractor([]),
        threshold=0.5,
    )

    assert result.refused is True
    assert result.review_queue.all_statements() == []

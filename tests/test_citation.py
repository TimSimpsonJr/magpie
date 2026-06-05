# tests/test_citation.py -- ASCII only
import pytest

from scripts.citation import (
    CitationRecord, sha256_text, block_index_of, SCHEMA_NAME, SCHEMA_VERSION,
    build_anchor, QuoteContractError,
)
from tests.conftest_citation import make_block, make_doc


def _record(**kw):
    base = dict(
        claim_text="Officer Ramirez ran 482 searches.",
        verbatim_quote="482 searches",
        context_prefix="Ramirez ran ", context_suffix=" in March",
        doc_id="abc123", doc_schema_name="DoclingDocument", doc_schema_version="1.10.0",
        page_no=1, block_index=0, block_self_ref="#/texts/0",
        char_start=12, char_end=24, text_hash=sha256_text("482 searches"),
        bbox={"l": 1.0, "t": 2.0, "r": 3.0, "b": 4.0, "coord_origin": "BOTTOMLEFT"},
        n_prov=1, verifier_result="indeterminate", verifier_confidence=None,
        checker_level="exact", extractor_model="claude-opus-4-8", prompt_version="v1",
        timestamp="2026-06-05T00:00:00Z",
    )
    base.update(kw)
    return CitationRecord(**base)


def test_sha256_text_is_full_untruncated_no_strip():
    # full 64-hex; differs from pii_sweep.text_id (stripped + [:16])
    h = sha256_text(" 482 ")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
    assert sha256_text(" 482 ") != sha256_text("482")  # NOT stripped


def test_block_index_of_parses_self_ref():
    assert block_index_of("#/texts/12") == 12


def test_to_dict_is_json_able_and_round_trips():
    import json
    d = _record().to_dict()
    assert json.loads(json.dumps(d))["text_hash"] == sha256_text("482 searches")
    assert d["schema_name"] == SCHEMA_NAME and d["schema_version"] == SCHEMA_VERSION


def test_public_anchor_is_exactly_the_approved_minimal_surface():
    pub = _record().public_anchor()
    # raw fields dropped
    for raw in ("claim_text", "verbatim_quote", "context_prefix", "context_suffix"):
        assert raw not in pub
    # EXACTLY the approved design's public set (design section 3) -- set-equality so
    # the test catches BOTH a dropped key AND any silent widening (e.g. char_start,
    # n_prov, timestamp must NOT leak onto the published surface).
    assert set(pub) == {
        "doc_id", "page_no", "block_index", "block_self_ref", "text_hash", "bbox",
        "checker_level", "verifier_result", "schema_name", "schema_version",
    }


# --------------------------------------------------------------------------- #
# Task 2: build_anchor + the v1 quote contract (design 2.4).
# --------------------------------------------------------------------------- #


def _kw(**kw):
    base = dict(claim_text="c", doc_id="d", doc_schema_name="DoclingDocument",
                doc_schema_version="1.10.0", extractor_model="m", prompt_version="v1",
                timestamp="t")
    base.update(kw)
    return base


def test_build_anchor_happy_path_single_prov():
    blk = make_block(3, "Officer Ramirez ran 482 searches in March 2026.", page_no=2)
    rec = build_anchor(blk, verbatim_quote="482 searches", **_kw())
    assert rec.block_index == 3 and rec.block_self_ref == "#/texts/3"
    assert rec.page_no == 2 and rec.n_prov == 1
    txt = "Officer Ramirez ran 482 searches in March 2026."
    assert txt[rec.char_start:rec.char_end] == "482 searches"   # half-open
    assert rec.context_prefix.endswith("ran ") and rec.context_suffix.startswith(" in")
    assert rec.bbox == blk["prov"][0]["bbox"]


def test_build_anchor_offsets_independent_of_docling_charspan():
    # 6/189 real blocks had prov.charspan != [0,len). build_anchor must NOT use it.
    blk = make_block(0, "alpha 482 searches beta", charspan=[100, 123])
    rec = build_anchor(blk, verbatim_quote="482 searches", **_kw())
    assert "alpha 482 searches beta"[rec.char_start:rec.char_end] == "482 searches"


def test_rejects_empty_or_whitespace_quote():
    blk = make_block(0, "some text here")
    for bad in ("", "   ", "\t"):
        with pytest.raises(QuoteContractError):
            build_anchor(blk, verbatim_quote=bad, **_kw())


def test_rejects_quote_not_in_block():
    blk = make_block(0, "some text here")
    with pytest.raises(QuoteContractError):
        build_anchor(blk, verbatim_quote="absent", **_kw())


def test_rejects_multi_prov_block():
    blk = make_block(0, "spans two pages", prov=[
        {"page_no": 1, "bbox": {}, "charspan": [0, 8]},
        {"page_no": 2, "bbox": {}, "charspan": [8, 15]}])
    with pytest.raises(QuoteContractError):
        build_anchor(blk, verbatim_quote="two", **_kw())


def test_rejects_mid_token_subspan_not_word_boundary():
    # "ice" inside "police" must be rejected (the ICE/polICE trap at anchor level)
    blk = make_block(0, "the police arrived")
    with pytest.raises(QuoteContractError):
        build_anchor(blk, verbatim_quote="ice", **_kw())


def test_rejects_quote_occurring_twice_in_block():
    blk = make_block(0, "482 searches and 482 searches")
    with pytest.raises(QuoteContractError):
        build_anchor(blk, verbatim_quote="482 searches", **_kw())

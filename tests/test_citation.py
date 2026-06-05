# tests/test_citation.py -- ASCII only
import pytest

from scripts.citation import (
    CitationRecord, sha256_text, block_index_of, SCHEMA_NAME, SCHEMA_VERSION,
    build_anchor, QuoteContractError, resolve_anchor, is_clean_citation,
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


def test_rejects_overlapping_repeated_quote_in_block():
    # "A A" occurs TWICE in "A A A" as OVERLAPPING repeats (index 0 and index 2),
    # so it is NOT unique-in-block and build_anchor must reject it. Both edges of
    # "A A" land on whitespace/string boundaries in "A A A" (left: start==0 or a
    # space; right: a space or end), so it is genuinely word-boundary aligned --
    # the rejection is therefore specifically about non-uniqueness, NOT the
    # word-boundary guard. The pre-fix occurrence search advanced by len(sub) and
    # found only ONE occurrence, wrongly accepting "A A" as unique; counting
    # overlapping positions makes it (correctly) non-unique.
    blk = make_block(0, "A A A")
    with pytest.raises(QuoteContractError):
        build_anchor(blk, verbatim_quote="A A", **_kw())


# --------------------------------------------------------------------------- #
# Task 3: resolve_anchor fallback chain + clean-citation gate (design 2.2/2.3).
# --------------------------------------------------------------------------- #


def _anchor_in(doc, block, quote):
    return build_anchor(block, verbatim_quote=quote, **_kw())


def test_exact_level_when_offsets_intact():
    blk = make_block(0, "Officer Ramirez ran 482 searches in March.")
    rec = _anchor_in(None, blk, "482 searches")
    r = resolve_anchor(rec, make_doc([blk]))
    assert r.level == "exact" and r.matched_text == "482 searches"
    assert is_clean_citation(r) is True


def test_exact_disambiguates_duplicate_via_offsets():
    # same quote twice in the DOC (different blocks); stored offsets+block_index pick one
    b0 = make_block(0, "alpha 482 searches alpha")
    rec = _anchor_in(None, b0, "482 searches")
    b1 = make_block(1, "beta 482 searches beta")
    r = resolve_anchor(rec, make_doc([b0, b1]))
    assert r.level == "exact" and r.block_index == 0


def test_relocated_when_offsets_shift_but_quote_unique():
    blk = make_block(0, "Officer Ramirez ran 482 searches in March.")
    rec = _anchor_in(None, blk, "482 searches")
    # OCR re-run: PREPEND a header to the block (shifts offsets) + insert an earlier block
    shifted = make_block(1, "PAGE 1 HEADER. Officer Ramirez ran 482 searches in March.")
    doc2 = make_doc([make_block(0, "INSERTED EARLIER BLOCK"), shifted])
    r = resolve_anchor(rec, doc2)
    assert r.level == "relocated" and r.matched_text == "482 searches"
    assert is_clean_citation(r) is True


def test_ambiguous_when_quote_repeats_and_context_cannot_disambiguate():
    blk = make_block(0, "x 482 searches y")
    rec = _anchor_in(None, blk, "482 searches")
    # two identical-context occurrences after an offset shift
    doc2 = make_doc([make_block(0, "HDR x 482 searches y ... x 482 searches y")])
    r = resolve_anchor(rec, doc2)
    assert r.level == "ambiguous" and r.n_matches >= 2
    assert is_clean_citation(r) is False


def test_ambiguous_when_quote_repeats_OVERLAPPING_in_target():
    # Build a UNIQUE quote "A A" on a tiny single-prov block (its context windows
    # are empty, so they match trivially anywhere). In the target, the stored
    # exact offsets FAIL (block 0 is a different inserted block), and the quote
    # then appears as OVERLAPPING repeats in block 1 "A A A" -- at index 0 AND
    # index 2, which overlap on the shared middle space. Both occurrences are
    # word-boundary aligned, both carry the (empty) stored context, and both hash
    # to the quote -> TWO relocation candidates -> ambiguous (n_matches >= 2),
    # NEVER a clean relocated.
    #
    # This PINS finding 1: the pre-fix occurrence search advanced by len(sub) and
    # saw only the index-0 occurrence (a single candidate), so resolve_anchor
    # would wrongly relocate "A A" cleanly. Counting overlapping positions makes
    # the second occurrence visible and forces the correct ambiguous verdict.
    build_blk = make_block(0, "A A")  # quote unique here; empty context windows
    rec = _anchor_in(None, build_blk, "A A")
    assert rec.context_prefix == "" and rec.context_suffix == ""  # both windows empty
    doc2 = make_doc([
        make_block(0, "INSERTED EARLIER BLOCK"),  # breaks the stored exact offsets
        make_block(1, "A A A"),                    # "A A" overlaps at idx 0 and idx 2
    ])
    r = resolve_anchor(rec, doc2)
    assert r.level == "ambiguous" and r.n_matches >= 2
    assert is_clean_citation(r) is False


def test_block_level_when_characters_changed_but_block_valid():
    blk = make_block(0, "Officer Ramirez ran 482 searches in March.", page_no=2)
    rec = _anchor_in(None, blk, "482 searches")
    # OCR mangled the chars (rn->m etc.); the quote no longer appears, block still on page 2
    doc2 = make_doc([make_block(0, "Officer Rarnirez ran 4B2 searches in March.", page_no=2)],
                    pages={"2": {"size": {"width": 1.0, "height": 1.0}, "page_no": 2}})
    r = resolve_anchor(rec, doc2)
    assert r.level == "block" and is_clean_citation(r) is False


def test_page_level_when_block_index_gone_but_page_exists():
    blk = make_block(5, "482 searches", page_no=3)
    rec = _anchor_in(None, blk, "482 searches")
    doc2 = make_doc([make_block(0, "unrelated mangled text", page_no=3)],
                    pages={"3": {"size": {"width": 1.0, "height": 1.0}, "page_no": 3}})
    r = resolve_anchor(rec, doc2)
    assert r.level == "page" and is_clean_citation(r) is False


def test_unresolved_when_nothing_matches():
    blk = make_block(9, "482 searches", page_no=8)
    rec = _anchor_in(None, blk, "482 searches")
    r = resolve_anchor(rec, make_doc([make_block(0, "x", page_no=1)]))
    assert r.level == "unresolved" and is_clean_citation(r) is False


def test_relocated_rejects_interior_substring_via_word_boundary():
    # quote "ice" stored; OCR doc has it only inside "police" -> must NOT relocate
    blk = make_block(0, "the ice cream truck")  # valid build (word-boundary "ice")
    rec = _anchor_in(None, blk, "ice")
    doc2 = make_doc([make_block(0, "the police came")])  # "ice" only inside "police"
    r = resolve_anchor(rec, doc2)
    assert r.level in ("block", "page", "unresolved") and r.level != "relocated"


def test_relocated_uses_context_to_break_a_tie():
    # quote unique in the BUILD block but appears TWICE in the target (offsets
    # shifted). Only one occurrence carries the stored context -> unique relocated.
    # A context-IGNORING resolver would see two matches and wrongly return ambiguous.
    blk = make_block(0, "alpha 482 searches beta")
    rec = _anchor_in(None, blk, "482 searches")
    doc2 = make_doc([
        make_block(0, "HEADER LINE"),
        make_block(1, "gamma 482 searches delta"),   # wrong context
        make_block(2, "alpha 482 searches beta"),     # the stored context
    ])
    r = resolve_anchor(rec, doc2)
    assert r.level == "relocated" and r.block_index == 2 and is_clean_citation(r)


def test_multi_prov_target_block_degrades_geometry_not_exact():
    # build on a single-prov block; the SAME block_index in the target is now
    # multi-prov (a re-ingest split it across pages). Text still matches at the
    # stored offsets, but faithful geometry is impossible -> degrade to block.
    blk = make_block(0, "Officer Ramirez ran 482 searches in March.")
    rec = _anchor_in(None, blk, "482 searches")
    bb = {"l": 1.0, "t": 2.0, "r": 3.0, "b": 4.0, "coord_origin": "BOTTOMLEFT"}
    split = make_block(0, "Officer Ramirez ran 482 searches in March.", prov=[
        {"page_no": 1, "bbox": bb, "charspan": [0, 25]},
        {"page_no": 2, "bbox": bb, "charspan": [25, 43]}])
    r = resolve_anchor(rec, make_doc([split]))
    assert r.level == "block" and r.bbox is None and r.page_no is None
    assert is_clean_citation(r) is False


def test_relocated_rejects_single_candidate_with_wrong_context():
    # exact fails (offsets shifted); the target has exactly ONE boundary-aligned
    # occurrence of the quote, but its context does NOT match the stored
    # prefix/suffix -> must NOT relocate. Pins that context is checked even for a
    # LONE candidate (a resolver that only checks context to break ties would
    # wrongly relocate here).
    blk = make_block(0, "alpha 482 searches beta")
    rec = _anchor_in(None, blk, "482 searches")
    doc2 = make_doc([make_block(0, "HEADER gamma 482 searches delta")])  # wrong context, shifted
    r = resolve_anchor(rec, doc2)
    assert r.level != "relocated" and r.level in ("block", "page", "unresolved")
    assert is_clean_citation(r) is False


def test_relocated_into_multi_prov_target_degrades_to_block():
    # exact fails (block_index shifted); the UNIQUE relocated candidate (text +
    # context match) lives in a MULTI-prov target block -> degrade to block with
    # page_no/bbox None. Pins the single-prov guard on the RELOCATED branch too
    # (not just the exact branch).
    blk = make_block(0, "alpha 482 searches beta")
    rec = _anchor_in(None, blk, "482 searches")
    bb = {"l": 1.0, "t": 2.0, "r": 3.0, "b": 4.0, "coord_origin": "BOTTOMLEFT"}
    target = make_block(1, "alpha 482 searches beta", prov=[
        {"page_no": 1, "bbox": bb, "charspan": [0, 11]},
        {"page_no": 2, "bbox": bb, "charspan": [11, 23]}])
    doc2 = make_doc([make_block(0, "INSERTED EARLIER"), target])
    r = resolve_anchor(rec, doc2)
    assert r.level == "block" and r.page_no is None and r.bbox is None
    assert is_clean_citation(r) is False

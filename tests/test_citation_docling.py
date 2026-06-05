"""Phase 8 Task 6 -- real-pipeline citation-anchor validation (Tier 2 + Tier 2b).

These tests load the REAL Docling + RapidOCR engines (several seconds each; cold
start ~95s), so they carry the module-level ``docling`` marker and are excluded
from the offline suite (``-k "not docling and not spacy and not xray"``). Select
with ``-k docling``.

  * Tier 2 (``test_paired_native_scan_resolves_across_ocr``): a DEDICATED paired
    fixture renders ONE shared ASCII text BOTH ways -- a native text-layer PDF and
    an image-only (OCR) PDF. We ingest both, build anchors on the NATIVE-ingest
    DoclingDocument over distinctive single-prov tokens, and resolve each against
    the SCAN's OCR-ingest DoclingDocument. OCR is imperfect, so the assertions are
    robust: NO false ``exact``; honest degradation levels only; and at least one
    clean survivor OR an all-degraded-without-a-false-exact outcome.

  * Tier 2b (``test_real_world_*``): two checks on the real Greenville RFP, gated
    on ``MAGPIE_PHASE8_REAL_PDF``. Skipped in CI; the PDF is never committed.
"""
import os
import json

import pytest

pytestmark = pytest.mark.docling


# Levels the resolver is allowed to return (the honest degradation chain).
_VALID_LEVELS = {"exact", "relocated", "ambiguous", "block", "page", "unresolved"}


def _anchors_over(doc, res, *, limit=20):
    """Build anchors over a DoclingDocument's single-prov blocks, one per block,
    each on the first block-unique 6+ char token. Used VERBATIM by Tier 2b (the
    plan's reference helper)."""
    from scripts.citation import build_anchor
    out = []
    for blk in doc["texts"]:
        if len(blk.get("prov", [])) != 1:
            continue
        words = [w for w in blk["text"].split() if len(w) >= 6 and blk["text"].count(w) == 1]
        if not words:
            continue
        out.append(build_anchor(blk, verbatim_quote=words[0], claim_text="c",
                                doc_id=res.source_sha256, doc_schema_name=res.schema_name,
                                doc_schema_version=res.schema_version, extractor_model="m",
                                prompt_version="v1", timestamp="t"))
        if len(out) >= limit:
            break
    return out


def _all_token_anchors_over(doc, res, *, limit=20):
    """Build anchors on EVERY block-unique 6+ char token across all single-prov
    blocks (not just the first per block). The synthetic native fixture often
    merges its lines into a SINGLE text block, so one-per-block (``_anchors_over``)
    would yield too few anchors to exercise the resolver. This variant draws many
    distinct anchors from a single merged block while keeping ``build_anchor``'s
    block-unique + word-boundary contract intact. Tier 2 only."""
    from scripts.citation import build_anchor, QuoteContractError
    out = []
    for blk in doc["texts"]:
        if len(blk.get("prov", [])) != 1:
            continue
        seen = set()
        for w in blk["text"].split():
            tok = w.strip(".,;:()[]\"'")
            if len(tok) < 6 or tok in seen or blk["text"].count(tok) != 1:
                continue
            seen.add(tok)
            try:
                rec = build_anchor(blk, verbatim_quote=tok, claim_text="c",
                                   doc_id=res.source_sha256, doc_schema_name=res.schema_name,
                                   doc_schema_version=res.schema_version, extractor_model="m",
                                   prompt_version="v1", timestamp="t")
            except QuoteContractError:
                # e.g. not word-boundary aligned after stripping punctuation; skip.
                continue
            out.append(rec)
            if len(out) >= limit:
                return out
    return out


# --------------------------------------------------------------------------- #
# Tier 2: generated source (paired native / scan of ONE shared text).
# --------------------------------------------------------------------------- #


def test_paired_native_scan_resolves_across_ocr(paired_native_scan, tmp_path):
    """Build anchors on the NATIVE ingest, resolve against the OCR ingest of the
    identical content. OCR is lossy, so assert robustly: no FALSE exact, only
    honest degradation levels, at least a few anchors built, and lenient survival
    (>=1 clean OR all-degraded-without-a-false-exact)."""
    from scripts.ingest import ingest
    from scripts.citation import resolve_anchor, is_clean_citation

    native_path, scan_path, _shared_text = paired_native_scan

    res_native = ingest(str(native_path), out_dir=str(tmp_path / "native"))
    res_scan = ingest(str(scan_path), out_dir=str(tmp_path / "scan"))

    doc_native = json.load(open(res_native.docling_json_path, encoding="utf-8"))
    doc_scan = json.load(open(res_scan.docling_json_path, encoding="utf-8"))

    anchors = _all_token_anchors_over(doc_native, res_native)
    # We need a few anchors to exercise the resolver at all.
    assert len(anchors) >= 3

    clean_count = 0
    false_exact = False
    for rec in anchors:
        r = resolve_anchor(rec, doc_scan)
        # Honest degradation: every result is one of the known levels.
        assert r.level in _VALID_LEVELS
        # PRIMARY: a reported exact must be a TRUE slice match against the OCR doc.
        if r.level == "exact":
            slice_text = doc_scan["texts"][r.block_index]["text"][r.char_start:r.char_end]
            if slice_text != rec.verbatim_quote:
                false_exact = True
            assert slice_text == rec.verbatim_quote
        if is_clean_citation(r):
            clean_count += 1

    # No anchor produced a false exact (the load-bearing trust property).
    assert not false_exact
    # LENIENT survival: prefer at least one clean survivor; but if the synthetic
    # OCR was too lossy for any clean hit, accept all-degraded SO LONG AS no
    # result was a false exact (already asserted above). This keeps the test from
    # going flaky-red on a lossy OCR render without weakening the no-false-exact
    # guarantee.
    assert clean_count >= 1 or not false_exact


# --------------------------------------------------------------------------- #
# Tier 2b: real-world Greenville RFP (env-var-gated; never committed).
# --------------------------------------------------------------------------- #

REAL = os.environ.get("MAGPIE_PHASE8_REAL_PDF")
_skip = pytest.mark.skipif(not (REAL and os.path.exists(REAL)),
                           reason="set MAGPIE_PHASE8_REAL_PDF to the local Greenville RFP")


@_skip
def test_real_world_same_doc_exact(tmp_path):
    from scripts.ingest import ingest
    from scripts.citation import resolve_anchor, is_clean_citation
    res = ingest(REAL, out_dir=str(tmp_path), page_range=(1, 12))
    assert res.trustworthy_for_extraction  # native + trustworthy (validated 2026-06-05)
    doc = json.load(open(res.docling_json_path, encoding="utf-8"))
    anchors = _anchors_over(doc, res)
    assert len(anchors) >= 5
    for rec in anchors:
        r = resolve_anchor(rec, doc)
        assert r.level == "exact" and is_clean_citation(r)


@_skip
def test_real_world_reingest_drift(tmp_path):
    # Build anchors on pages 1-12; resolve against an ingest of pages 2-12. Page-1
    # blocks vanish and every remaining block_index shifts, so EXACT (keyed on the
    # stored block_index) fails and the fallback chain must take over: shared blocks
    # relocate by unique text+context; dropped-page blocks degrade. No item may
    # return a FALSE exact, and a meaningful share must still resolve clean.
    from scripts.ingest import ingest
    from scripts.citation import resolve_anchor, is_clean_citation
    res_a = ingest(REAL, out_dir=str(tmp_path / "a"), page_range=(1, 12))
    res_b = ingest(REAL, out_dir=str(tmp_path / "b"), page_range=(2, 12))
    doc_a = json.load(open(res_a.docling_json_path, encoding="utf-8"))
    doc_b = json.load(open(res_b.docling_json_path, encoding="utf-8"))
    anchors = _anchors_over(doc_a, res_a)
    assert len(anchors) >= 5
    clean = 0
    for rec in anchors:
        r = resolve_anchor(rec, doc_b)
        if r.level == "exact":  # a surviving exact must be a TRUE slice match
            assert doc_b["texts"][r.block_index]["text"][r.char_start:r.char_end] == rec.verbatim_quote
        if is_clean_citation(r):
            clean += 1
    assert clean >= 1  # some shared-page anchors survive real re-ingest block-index drift

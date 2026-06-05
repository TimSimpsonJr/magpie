"""TDD for scripts.ingest -- the Docling edge of the Phase 6 ``ingest`` skill.

Two tiers:
  * The Task-4 ``sha256_file`` / ``IngestResult`` tests are PURE (no docling, no
    PDF, no models) -- importing ``scripts.ingest`` must stay cheap (docling is
    imported LAZILY inside ``ingest()``), so these run in the offline suite.
  * The ``ingest()`` end-to-end tests load the real Docling layout + RapidOCR
    models, so each is marked ``@pytest.mark.docling`` (select with -k docling;
    the offline suite excludes them via -k "not docling"). They are slow: each
    OCR convert takes several seconds on CPU.

All fixtures are SYNTHETIC, built into ``tmp_path`` (see tests/conftest.py); no
real corpus is touched.
"""
import json
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# Task 4 -- pure source-identity + IngestResult shape (NO docling).
# --------------------------------------------------------------------------- #


def test_sha256_file_matches_known(tmp_path):
    from scripts.ingest import sha256_file
    p = tmp_path / "a.bin"
    p.write_bytes(b"magpie")
    import hashlib
    assert sha256_file(p) == hashlib.sha256(b"magpie").hexdigest()


def test_ingestresult_is_jsonable_with_required_keys():
    from scripts.ingest import IngestResult
    r = IngestResult(source_path="x.pdf", source_sha256="ab", docling_json_path="x.json",
                     schema_name="DoclingDocument", schema_version="1.10.0", n_pages=1,
                     doc_decision="native", trustworthy_for_extraction=True,
                     ocr_engine_used="none", per_page=[], bates=[], warnings=[])
    d = json.loads(json.dumps(r.to_dict()))
    assert d["trustworthy_for_extraction"] is True and d["ocr_engine_used"] == "none"


# --------------------------------------------------------------------------- #
# Task 5 -- docling marker + do_ocr=False gate pass (native branch).
# --------------------------------------------------------------------------- #


@pytest.mark.docling
def test_clean_digital_pdf_decides_native_and_does_not_flag(tmp_path, native_pdf):
    from scripts.ingest import ingest
    r = ingest(native_pdf, out_dir=tmp_path)
    assert r.doc_decision == "native"
    assert r.ocr_engine_used == "none"
    assert r.trustworthy_for_extraction is True
    # nan semantics (Codex r2): native pages have ocr_score == nan -> nan is N/A,
    # NOT 0 -> a clean native page must NOT be flagged as degraded.
    assert not any(p["flagged"] for p in r.per_page)
    doc = json.loads(Path(r.docling_json_path).read_text(encoding="utf-8"))
    assert doc["schema_name"] == "DoclingDocument"


# --------------------------------------------------------------------------- #
# Task 6 -- apply doc-decision branches + OCRmyPDF seam.
# --------------------------------------------------------------------------- #


@pytest.mark.docling
def test_image_only_scan_engages_rapidocr_with_full_provenance(tmp_path, scan_pdf):
    from scripts.ingest import ingest
    r = ingest(scan_pdf, out_dir=tmp_path)
    assert r.doc_decision == "ocr_images"          # image-only != garbled (tight, no OR)
    assert r.ocr_engine_used == "rapidocr"
    doc = json.loads(Path(r.docling_json_path).read_text(encoding="utf-8"))
    provs = [p for t in doc["texts"] for p in t.get("prov", [])]
    assert provs, "no provenance on OCR'd items"
    for p in provs:                                # FULL contract {page_no, bbox, charspan}
        assert "page_no" in p and "charspan" in p
        assert {"l", "t", "r", "b", "coord_origin"} <= set(p["bbox"])
    assert len({p["bbox"]["coord_origin"] for p in provs}) == 1, "bbox origins not normalized"
    # nan semantics (Codex r2): an OCR'd scan has parse_score == nan -> N/A, not 0;
    # a CLEAN scan (good ocr_score) must NOT be flagged merely because parse_score is nan.
    assert not any(p["flagged"] for p in r.per_page)


@pytest.mark.docling
def test_garbled_text_layer_forces_full_doc_ocr(tmp_path, garbled_pdf):
    from scripts.ingest import ingest
    r = ingest(garbled_pdf, out_dir=tmp_path)
    assert r.doc_decision == "force_full_doc_ocr"  # present-but-bad layer overridden doc-wide
    assert r.ocr_engine_used == "rapidocr"


@pytest.mark.docling
def test_mostly_native_with_two_bad_pages_stays_native_and_flags(tmp_path, mixed_pdf):
    from scripts.ingest import ingest
    r = ingest(mixed_pdf, out_dir=tmp_path)
    assert r.doc_decision == "native"              # NOT flipped to full OCR (conservative rule)
    assert any(p["flagged"] for p in r.per_page)   # the bad pages ARE flagged


@pytest.mark.docling
def test_ocrmypdf_seam_skips_warns_and_flags_when_tesseract_absent(monkeypatch, tmp_path, scan_pdf):
    from scripts.ingest import ingest
    # force the seam's detect to report Tesseract absent (the Phase-6 reality)
    monkeypatch.setattr("scripts.ingest._tesseract_available", lambda: False)
    r = ingest(scan_pdf, out_dir=tmp_path, deskew=True)
    assert any("OCRmyPDF" in w and "Tesseract" in w for w in r.warnings)
    assert any(p["flagged"] for p in r.per_page)   # requested-but-unavailable preprocess -> flag


# --------------------------------------------------------------------------- #
# Task 7 -- Bates post-pass + degraded flagging + trustworthy_for_extraction.
# --------------------------------------------------------------------------- #


@pytest.mark.docling
def test_bates_captured_separately_with_provenance(tmp_path, bates_pdf):
    from scripts.ingest import ingest
    r = ingest(bates_pdf, out_dir=tmp_path)
    assert any(b["value"].startswith("SVPD-") for b in r.bates)
    assert all("page_no" in b and "bbox" in b for b in r.bates)


@pytest.mark.docling
def test_degraded_page_is_flagged(tmp_path, degraded_pdf):
    from scripts.ingest import ingest
    r = ingest(degraded_pdf, out_dir=tmp_path)
    assert any(p["flagged"] and p["flag_reason"] for p in r.per_page)


@pytest.mark.docling
def test_review_doc_contract_pinned_deterministically(tmp_path, native_pdf, monkeypatch):
    from scripts.ingest import ingest
    from scripts.ingest_gate import DocDecision
    # FORCE the rollup to review so the safety contract is ALWAYS exercised
    # (the old `if doc_decision == review` guard was green-on-broken).
    monkeypatch.setattr("scripts.ingest.decide_doc", lambda diags: DocDecision.review)
    r = ingest(native_pdf, out_dir=tmp_path)
    assert r.doc_decision == "review"
    assert r.trustworthy_for_extraction is False      # downstream (Phase 8) MUST refuse extraction
    assert Path(r.docling_json_path).exists()         # JSON still written for human inspection
    assert all(p["flagged"] for p in r.per_page)      # every page flagged on review


# --------------------------------------------------------------------------- #
# Finding 1 (Codex impl-review) -- the per_page CONTRACT (design §5). Pin the
# EXACT key set so it can't silently drift again (parse_score/ocr_score regressed
# away from the design's hit_rate/confidence_grade), and prove ocr_applied is
# PAGE-ACCURATE (True on the scan's OCR'd page, False on a clean native page).
# --------------------------------------------------------------------------- #

# The design §5 per_page fields PLUS the kept raw-score debug fields
# (parse_score / ocr_score), re-approved as the superset in the doc.
_PER_PAGE_KEYS = {
    "page_no", "native_chars", "hit_rate", "parse_score", "ocr_score",
    "diagnosis", "ocr_applied", "confidence_grade", "flagged", "flag_reason",
}


@pytest.mark.docling
def test_per_page_pins_contract_keys_and_page_accurate_ocr_applied(
    tmp_path, scan_pdf, native_pdf
):
    from scripts.ingest import ingest

    # Image-only scan -> ocr_images: the (single) OCR'd page must be ocr_applied.
    rs = ingest(scan_pdf, out_dir=tmp_path)
    assert rs.doc_decision == "ocr_images"
    assert rs.per_page, "scan produced no per_page entries"
    for p in rs.per_page:
        assert set(p) == _PER_PAGE_KEYS, f"per_page key drift: {set(p) ^ _PER_PAGE_KEYS}"
    # The OCR'd image-only page actually triggered OCR -> ocr_applied True there.
    assert any(p["ocr_applied"] for p in rs.per_page)
    assert any(p["diagnosis"] == "image_only" and p["ocr_applied"] for p in rs.per_page)
    # hit_rate is real-or-None (never a fake 0.0): an image-only page with no alpha
    # tokens reports None, never 0.0.
    for p in rs.per_page:
        assert p["hit_rate"] is None or isinstance(p["hit_rate"], float)
    # confidence_grade is a human-readable string or None (nan-safe), never an enum.
    for p in rs.per_page:
        assert p["confidence_grade"] is None or isinstance(p["confidence_grade"], str)

    # Clean native PDF -> native: NO page was OCR'd -> ocr_applied False everywhere.
    rn = ingest(native_pdf, out_dir=tmp_path)
    assert rn.doc_decision == "native"
    assert rn.per_page
    for p in rn.per_page:
        assert set(p) == _PER_PAGE_KEYS
        assert p["ocr_applied"] is False             # page-accurate: native page not OCR'd
    # A clean native page DOES carry a hit_rate (real English text -> alpha tokens).
    assert any(isinstance(p["hit_rate"], float) for p in rn.per_page)


# --------------------------------------------------------------------------- #
# Finding 2 (Codex impl-review) -- the ugly-PDF failure contract (design §2.6).
# A corrupt / unparseable PDF drives Docling to ConversionStatus.FAILURE; ingest
# must NOT crash or dereference a bad document -- it returns a flagged result.
# --------------------------------------------------------------------------- #


@pytest.mark.docling
def test_corrupt_pdf_is_flagged_not_crashed(tmp_path, corrupt_pdf):
    from scripts.ingest import ingest
    # No exception, even though the document is unparseable.
    r = ingest(corrupt_pdf, out_dir=tmp_path)
    assert r.warnings, "a corrupt PDF must surface a warning"
    # skip-and-flag: route to the human gate (review) OR mark not-extractable.
    assert r.doc_decision == "review" or r.trustworthy_for_extraction is False
    assert r.ocr_engine_used == "none"               # never OCR a doc that failed to load

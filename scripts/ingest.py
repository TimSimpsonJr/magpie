"""The Docling EDGE of the Phase 6 ``ingest`` skill -- the ONLY docling importer.

Turns a PDF into a ``DoclingDocument`` JSON kept internally (never Markdown, so
every extracted element keeps its ``{page_no, bbox, charspan}`` provenance for
the Phase-8 citation anchor). A pure text-layer quality gate
(``scripts.ingest_gate``) decides native-vs-re-OCR BEFORE any OCR runs; degraded
/ handwriting pages are flagged for humans, never silently OCR'd as fact.

Engine at the edge (mirrors ``pii_sweep``'s lazy spaCy edge): docling is imported
LAZILY inside ``ingest()`` so importing this module stays cheap. ``sha256_file``
and ``IngestResult`` are PURE (no docling) -- the source-identity foundation.

The gate functions are imported INTO this module's namespace
(``from scripts.ingest_gate import diagnose_page, decide_doc``) so
``scripts.ingest.decide_doc`` is the monkeypatch target the review-doc test pins.
"""
from __future__ import annotations

import hashlib
import math
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from scripts.ingest_gate import decide_doc, diagnose_page, DocDecision, PageDiagnosis

__all__ = ["sha256_file", "IngestResult", "ingest"]


# --------------------------------------------------------------------------- #
# Edge constants (named -- never a bare number in a branch). The OCR-confidence
# floor is tuned against the synthetic fixtures at the research gate: a CLEAN
# scan scores ocr_score ~0.98 and a faint/noisy degraded scan ~0.54, so a floor
# at 0.85 separates them with wide margin (not threshold-fragile).
# --------------------------------------------------------------------------- #

_DEGRADED_OCR_SCORE = 0.85   # OCR ran AND ocr_score below this (and not nan) -> flag
_DEFAULT_MAX_PAGES = 2000    # convert() large-doc guard (oversized/malicious PDF)
_DEFAULT_MAX_FILE_SIZE = 512 * 1024 * 1024  # 512 MiB convert() guard

# Bates: an alphanumeric prefix + optional separator + a zero-padded digit run,
# word-boundary anchored (design §7 / prior-art §7). Capture-and-tag only --
# NEVER rewrites the underlying text.
_BATES_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,}[-_ ]?\d{3,}\b")


# --------------------------------------------------------------------------- #
# Source identity (PURE -- no docling). Part of provenance (design §2.6): a
# citation must tie back to WHICH file produced the geometry.
# --------------------------------------------------------------------------- #

_SHA_CHUNK = 1 << 20  # 1 MiB streamed read -- never slurp a huge PDF into memory


def sha256_file(path: str | Path) -> str:
    """Streamed SHA-256 hex digest of a file's bytes (artifact identity).

    Reads in ``_SHA_CHUNK``-sized blocks so a multi-hundred-MB PDF hashes without
    being loaded whole into memory.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_SHA_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Output contract (design §5). JSON-able summary returned alongside the written
# DoclingDocument JSON. PURE dataclass -- no docling types leak into it.
# --------------------------------------------------------------------------- #


@dataclass
class IngestResult:
    """Provenance summary of one ingested PDF (design §5 output contract).

    ``to_dict()`` is JSON-able (plain str/int/bool/list/dict). ``per_page`` and
    ``bates`` are lists of plain dicts (already JSON-able), so a single
    ``asdict`` round-trips cleanly. ``docling_json_path`` is ALWAYS written --
    including on a ``review`` decision (the JSON is evidence for human
    inspection; ``trustworthy_for_extraction`` is then False).
    """

    source_path: str
    source_sha256: str                 # artifact identity (design §2.6)
    docling_json_path: str             # ALWAYS written, incl. on `review`
    schema_name: str                   # "DoclingDocument"
    schema_version: str                # e.g. "1.10.0"
    n_pages: int
    doc_decision: str                  # native|ocr_images|force_full_doc_ocr|review
    trustworthy_for_extraction: bool   # FALSE iff doc_decision == review
    ocr_engine_used: str               # "none" | "rapidocr" (Phase-6 scope)
    per_page: list[dict[str, Any]] = field(default_factory=list)
    bates: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-able dict of every field (no docling objects inside)."""
        return asdict(self)


# --------------------------------------------------------------------------- #
# OCRmyPDF seam -- DETECT / SKIP / FLAG only (design §2.5). No deskew-vs-redo
# decision engine: Tesseract is absent on the build box, so an OCRmyPDF op that
# is requested but unavailable is skipped with a warning + the pages flagged.
# --------------------------------------------------------------------------- #


def _tesseract_available() -> bool:
    """True iff the OCRmyPDF hard dependency (Tesseract) is on PATH.

    ``shutil.which`` is the cheap probe; the ``ocrmypdf`` import + its
    ``MissingDependencyError`` are only consulted if a run is actually attempted
    (out of scope for Phase 6 -- the seam detects and skips). Monkeypatched by
    the seam test to force the Tesseract-absent path deterministically.
    """
    if shutil.which("tesseract"):
        return True
    return False


# --------------------------------------------------------------------------- #
# nan-safe score helpers (load-bearing, design §2.4/§5). res.confidence reports
# parse_score == nan for an image-only scan and ocr_score == nan for a native
# page -- nan means "N/A for this modality", NEVER 0/bad.
# --------------------------------------------------------------------------- #


def _is_real(score: float | None) -> bool:
    """True iff ``score`` is a real (non-None, non-nan) float we may threshold."""
    return score is not None and not math.isnan(score)


def _score_or_none(score: float | None) -> float | None:
    """nan/None -> None (N/A); a real float passes through (for JSON + threshold)."""
    return score if _is_real(score) else None


# --------------------------------------------------------------------------- #
# Per-page native text + parse_score from a do_ocr=False ConversionResult.
# --------------------------------------------------------------------------- #


def _page_native_text(doc) -> dict[int, str]:
    """Concatenate each text item's text under the page its prov[0] points at.

    Docling collapses a run of lines into one text item, so per-page native text
    is the join of every item whose ``prov[0].page_no`` is that page (the same
    item-level provenance that is the §7 citation seam). Pages with no text item
    (an image-only scan) get no entry -> empty native text downstream.
    """
    by_page: dict[int, list[str]] = {}
    for item, _level in doc.iterate_items():
        text = getattr(item, "text", None)
        prov = getattr(item, "prov", None) or []
        if not text or not prov:
            continue
        page_no = prov[0].page_no
        by_page.setdefault(page_no, []).append(text)
    return {p: "\n".join(parts) for p, parts in by_page.items()}


def _page_numbers(doc) -> list[int]:
    """Sorted 1-based page numbers from ``doc.pages`` (authoritative page count)."""
    return sorted(doc.pages.keys())


def _page_parse_score(res, page_no: int) -> float | None:
    """This page's Docling ``parse_score`` (real float, or None if nan/absent)."""
    pages = getattr(getattr(res, "confidence", None), "pages", {}) or {}
    pc = pages.get(page_no)
    return _score_or_none(getattr(pc, "parse_score", None)) if pc is not None else None


def _page_ocr_score(res, page_no: int) -> float | None:
    """This page's Docling ``ocr_score`` (real float, or None if nan/absent)."""
    pages = getattr(getattr(res, "confidence", None), "pages", {}) or {}
    pc = pages.get(page_no)
    return _score_or_none(getattr(pc, "ocr_score", None)) if pc is not None else None


# --------------------------------------------------------------------------- #
# Provenance normalization (coordinate-origin trap, prior-art §2.3). Assembled
# item provenance is typically BOTTOMLEFT, but mixed origins MUST be normalized
# to a SINGLE origin across ALL provs before save_as_json (Task 6 asserts it).
# --------------------------------------------------------------------------- #


def _normalize_prov_origins(doc) -> None:
    """Force every text item's prov bbox to BOTTOMLEFT in place.

    The research gate found assembled provenance is typically already BOTTOMLEFT,
    but Docling can emit mixed origins; the saved JSON contract requires ONE
    ``coord_origin`` across all provs. ``bbox.to_bottom_left_origin(page_height)``
    is a no-op when the bbox is already BOTTOMLEFT, so this is safe to apply
    unconditionally (page height from ``doc.pages[page_no].size.height``).
    """
    for item, _level in doc.iterate_items():
        for prov in getattr(item, "prov", None) or []:
            page = doc.pages.get(prov.page_no)
            if page is None:
                continue
            height = page.size.height
            prov.bbox = prov.bbox.to_bottom_left_origin(height)


def _bbox_to_dict(bbox) -> dict[str, Any]:
    """A prov bbox as a plain JSON-able dict ``{l,t,r,b,coord_origin}``."""
    return {
        "l": bbox.l, "t": bbox.t, "r": bbox.r, "b": bbox.b,
        "coord_origin": bbox.coord_origin.value
        if hasattr(bbox.coord_origin, "value") else str(bbox.coord_origin),
    }


# --------------------------------------------------------------------------- #
# Bates post-pass (design §7). Word-boundary regex over iterate_items() text;
# capture {value, page_no, bbox} from item.prov[0] into a SEPARATE list. Never
# rewrites the text (leads-not-verdicts).
# --------------------------------------------------------------------------- #


def _collect_bates(doc) -> list[dict[str, Any]]:
    """Capture Bates-style stamps separately, each with its {page_no, bbox}."""
    out: list[dict[str, Any]] = []
    for item, _level in doc.iterate_items():
        text = getattr(item, "text", None)
        prov = getattr(item, "prov", None) or []
        if not text or not prov:
            continue
        p0 = prov[0]
        for match in _BATES_RE.finditer(text):
            out.append({
                "value": match.group(0),
                "page_no": p0.page_no,
                "bbox": _bbox_to_dict(p0.bbox),
            })
    return out


# --------------------------------------------------------------------------- #
# The Docling edge -- the ONLY place docling is imported (LAZILY).
# --------------------------------------------------------------------------- #


def _docling_imports():
    """Import docling LAZILY (heavy edge) and return the symbols ingest needs.

    Kept in one helper so importing ``scripts.ingest`` stays cheap (the layout /
    OCR models load on first convert, like ``pii_sweep``'s lazy spaCy edge).
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    return (DocumentConverter, PdfFormatOption, InputFormat,
            PdfPipelineOptions, RapidOcrOptions)


def _convert(pdf_path, *, do_ocr, force_full_page_ocr, lang,
             max_num_pages, max_file_size, page_range):
    """One Docling convert with an explicit ingest profile (don't inherit
    defaults). do_table_structure OFF for latency (the gate needs text, not table
    geometry); enrichments off. convert() large-doc guards from prior-art §2.6.
    """
    (DocumentConverter, PdfFormatOption, InputFormat,
     PdfPipelineOptions, RapidOcrOptions) = _docling_imports()

    opts = PdfPipelineOptions(do_ocr=do_ocr)
    opts.do_table_structure = False
    if do_ocr:
        opts.ocr_options = RapidOcrOptions(
            lang=[lang], force_full_page_ocr=force_full_page_ocr,
        )
    conv = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    convert_kwargs: dict[str, Any] = {
        "max_num_pages": max_num_pages,
        "max_file_size": max_file_size,
        "raises_on_error": False,
    }
    if page_range is not None:
        convert_kwargs["page_range"] = page_range
    return conv.convert(str(pdf_path), **convert_kwargs)


def ingest(
    pdf_path: str | Path,
    *,
    out_dir: str | Path,
    lang: str = "en",
    deskew: bool = False,
    max_num_pages: int = _DEFAULT_MAX_PAGES,
    max_file_size: int = _DEFAULT_MAX_FILE_SIZE,
    page_range: tuple[int, int] | None = None,
) -> IngestResult:
    """Ingest a PDF into a ``DoclingDocument`` JSON (internal) + an ``IngestResult``.

    Pipeline (design §3): source hash -> Docling ``do_ocr=False`` pass -> pure gate
    (diagnose each page -> conservative doc decision) -> apply the decision (reuse
    the native pass, or a second OCR convert) -> normalize prov origins ->
    save_as_json -> Bates post-pass + degraded-page flags. The
    ``DoclingDocument`` JSON is ALWAYS written (incl. on ``review`` -- evidence
    for human inspection); never Markdown (it would drop the bbox/charspan a
    citation needs).

    docling is imported LAZILY inside ``_convert`` so importing this module is
    cheap. ``decide_doc`` is referenced via this module's namespace so the
    review-doc test can monkeypatch ``scripts.ingest.decide_doc``.
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    source_sha256 = sha256_file(pdf_path)

    # ---- Pass #1: do_ocr=False -> what the gate sees (reused if native). ----
    res = _convert(
        pdf_path, do_ocr=False, force_full_page_ocr=False, lang=lang,
        max_num_pages=max_num_pages, max_file_size=max_file_size, page_range=page_range,
    )
    doc = res.document
    page_nos = _page_numbers(doc)
    native_by_page = _page_native_text(doc)

    # ---- Pure gate: per-page diagnosis -> conservative doc decision. ----
    diagnoses: list[PageDiagnosis] = []
    for page_no in page_nos:
        diag = diagnose_page(
            native_by_page.get(page_no, ""),
            parse_score=_page_parse_score(res, page_no),
            lang=lang,
        )
        diagnoses.append(diag)
    decision = decide_doc(diagnoses)

    # ---- Apply the decision (Task 6/7 fill the OCR + review branches). ----
    ocr_engine_used = "none"
    if decision is DocDecision.ocr_images:
        res = _convert(
            pdf_path, do_ocr=True, force_full_page_ocr=False, lang=lang,
            max_num_pages=max_num_pages, max_file_size=max_file_size, page_range=page_range,
        )
        doc = res.document
        ocr_engine_used = "rapidocr"
    elif decision is DocDecision.force_full_doc_ocr:
        res = _convert(
            pdf_path, do_ocr=True, force_full_page_ocr=True, lang=lang,
            max_num_pages=max_num_pages, max_file_size=max_file_size, page_range=page_range,
        )
        doc = res.document
        ocr_engine_used = "rapidocr"
    # native / review reuse pass #1's doc (review never silently OCRs).

    # ---- OCRmyPDF seam: detect / skip / flag (design §2.5). ----
    ocrmypdf_unavailable = deskew and not _tesseract_available()
    if ocrmypdf_unavailable:
        warnings.append(
            "OCRmyPDF preprocessing (deskew) requested but Tesseract is absent; "
            "skipped -- affected pages flagged for review."
        )

    # ---- Normalize prov origins, then persist the DoclingDocument JSON. ----
    _normalize_prov_origins(doc)
    json_path = out_dir / f"{pdf_path.stem}.docling.json"
    doc.save_as_json(json_path)

    # ---- Bates post-pass (separate list; never rewrites text). ----
    bates = _collect_bates(doc)

    # ---- Per-page records + degraded flagging (nan-safe). ----
    review = decision is DocDecision.review
    ocr_ran = ocr_engine_used == "rapidocr"
    diag_by_page = dict(zip(page_nos, diagnoses))
    per_page: list[dict[str, Any]] = []
    for page_no in page_nos:
        diag = diag_by_page[page_no]
        native_text = native_by_page.get(page_no, "")
        ocr_score = _page_ocr_score(res, page_no)
        parse_score = _page_parse_score(res, page_no)

        flag_reason = _flag_reason(
            diag=diag, decision=decision, ocr_ran=ocr_ran, ocr_score=ocr_score,
            review=review, ocrmypdf_unavailable=ocrmypdf_unavailable,
        )
        per_page.append({
            "page_no": page_no,
            "native_chars": len(native_text.strip()),
            "parse_score": parse_score,
            "ocr_score": ocr_score,
            "diagnosis": diag.value,
            "ocr_applied": ocr_ran,
            "flagged": flag_reason is not None,
            "flag_reason": flag_reason,
        })

    return IngestResult(
        source_path=str(pdf_path),
        source_sha256=source_sha256,
        docling_json_path=str(json_path),
        schema_name=getattr(doc, "schema_name", "DoclingDocument"),
        schema_version=str(getattr(doc, "version", "")),
        n_pages=len(page_nos),
        doc_decision=decision.value,
        trustworthy_for_extraction=not review,
        ocr_engine_used=ocr_engine_used,
        per_page=per_page,
        bates=bates,
        warnings=warnings,
    )


def _flag_reason(
    *, diag, decision, ocr_ran, ocr_score, review, ocrmypdf_unavailable,
) -> str | None:
    """The reason this page is flagged for a human, or None if it is trustworthy.

    nan-safe (design §2.4): a clean native page (ocr_score nan) and a clean scan
    (parse_score nan) are NOT flagged on a missing-modality score. A page is
    flagged only when a signal is GENUINELY bad:
      * ``review`` decision -> every page flagged (safety contract);
      * the gate diagnosed the page garbled / uncertain / image-only-not-OCR'd;
      * OCR ran and the page's real ``ocr_score`` is below the degraded floor;
      * an OCRmyPDF op was requested but skipped (Tesseract absent).
    """
    if review:
        return "review: document not trustworthy for unattended extraction"
    if diag is PageDiagnosis.garbled_text:
        return "garbled text layer"
    if diag is PageDiagnosis.uncertain_review:
        return "uncertain: too little signal to trust the text layer"
    if diag is PageDiagnosis.image_only and not ocr_ran:
        return "image-only page not OCR'd"
    if ocr_ran and _is_real(ocr_score) and ocr_score < _DEGRADED_OCR_SCORE:
        return f"low OCR confidence ({ocr_score:.2f})"
    if ocrmypdf_unavailable:
        return "OCRmyPDF preprocessing requested but unavailable (Tesseract absent)"
    return None

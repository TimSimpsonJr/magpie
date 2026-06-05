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

from scripts.ingest_gate import (
    decide_doc,
    diagnose_page,
    load_default_wordlist,
    wordlist_hit_rate,
    DocDecision,
    PageDiagnosis,
)

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

# Hard wall-clock ceiling on ONE Docling convert (design §2.6 ugly-PDF guard). A
# pathological/huge PDF that would otherwise hang aborts with PARTIAL_SUCCESS
# rather than wedging the pipeline. 300 s is generous for laptop-local CPU OCR
# (warm native ~1.3 s/page, force-full OCR ~5.7 s/page per the research gate);
# overridable via ingest(document_timeout=...).
_DEFAULT_DOCUMENT_TIMEOUT = 300.0  # seconds, per-convert

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


# A page with no real modality score yields Docling's UNSPECIFIED grade (its
# mean_score is nan -> _score_to_grade falls through). nan IS N/A NOT a fake
# grade (design §2.4/§5), so UNSPECIFIED maps to None alongside an absent entry.
_UNSPECIFIED_GRADE = "unspecified"


def _page_confidence_grade(res, page_no: int) -> str | None:
    """This page's human-readable Docling quality grade, or ``None``.

    ``res.confidence.pages[page_no].mean_grade`` is a ``QualityGrade`` (str,Enum)
    computed from the mean of the page's parse/layout/table/ocr scores. Rendered
    as its lowercase ``.value`` string for the JSON contract. nan-safe: an absent
    page entry OR the ``UNSPECIFIED`` grade (a page with no real score) -> ``None``
    (never a fake grade), mirroring ``_score_or_none`` for the raw scores.
    """
    pages = getattr(getattr(res, "confidence", None), "pages", {}) or {}
    pc = pages.get(page_no)
    if pc is None:
        return None
    grade = getattr(pc, "mean_grade", None)
    if grade is None:
        return None
    value = getattr(grade, "value", None)
    text = value if isinstance(value, str) else str(grade)
    if text == _UNSPECIFIED_GRADE:
        return None
    return text


# --------------------------------------------------------------------------- #
# Page-accurate ocr_applied (design §5). The per-page truth, not a doc-wide
# stamp: which pages OCR ACTUALLY ran on depends on the decision + that page's
# pass-#1 diagnosis.
# --------------------------------------------------------------------------- #

# do_ocr=True force_full_page_ocr=False only re-runs OCR on the bitmap regions of
# pages Docling found needed it -- i.e. the pages the gate diagnosed as having no
# / garbage native text. Native pages keep their pass-#1 text (no OCR).
_OCR_IMAGES_DIAGNOSES = frozenset({PageDiagnosis.image_only, PageDiagnosis.garbled_text})


def _page_ocr_applied(decision: DocDecision, diagnosis: PageDiagnosis) -> bool:
    """Did OCR actually run on THIS page? (page-accurate, design §5).

    * ``force_full_doc_ocr`` (force_full_page_ocr=True) -> every page OCR'd.
    * ``ocr_images`` (force_full_page_ocr=False) -> only the pages whose pass-#1
      diagnosis was image_only / garbled_text (a blank/garbage native layer);
      native pages kept their text and were NOT OCR'd.
    * ``native`` / ``review`` -> no OCR ran on any page.
    """
    if decision is DocDecision.force_full_doc_ocr:
        return True
    if decision is DocDecision.ocr_images:
        return diagnosis in _OCR_IMAGES_DIAGNOSES
    return False


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
# Ugly-PDF failure handling (design §2.6). With ``raises_on_error=False`` a
# corrupt / encrypted / unparseable PDF comes back as ConversionStatus.FAILURE
# (verified: the pdfium "Data format error" is swallowed, res.status == FAILURE,
# res.document exists but res.document.pages == {}). We must NOT dereference a
# bad document for extraction -- skip and flag.
# --------------------------------------------------------------------------- #


def _conversion_status_enum():
    """The lazily-imported ``ConversionStatus`` enum (no docling import at top)."""
    return _docling_imports()[3]


def _is_failure(res) -> bool:
    """True iff Docling reported ``ConversionStatus.FAILURE`` for this convert."""
    return getattr(res, "status", None) is _conversion_status_enum().FAILURE


def _is_partial_success(res) -> bool:
    """True iff Docling reported ``ConversionStatus.PARTIAL_SUCCESS``."""
    return getattr(res, "status", None) is _conversion_status_enum().PARTIAL_SUCCESS


def _errors_summary(res) -> str:
    """A short, human-readable summary of ``res.errors`` (possibly empty).

    The pdfium backend-init failure logs but leaves ``res.errors`` empty, so this
    is best-effort context appended to the status in the warning -- never relied
    on for the decision (the status is authoritative).
    """
    errors = getattr(res, "errors", None) or []
    parts: list[str] = []
    for err in errors:
        msg = getattr(err, "error_message", None) or str(err)
        parts.append(str(msg))
    return "; ".join(parts)


def _build_failure_result(
    *, pdf_path, source_sha256, json_path, warnings, doc, message,
):
    """Assemble the flagged skip-and-flag ``IngestResult`` for a failed convert.

    Shared by the FAILURE-status path AND the caught-exception path so both honor
    the same contract (no document dereference for extraction, no OCR, every
    existing page flagged). Still writes the (minimal) ``DoclingDocument`` JSON if
    a document object exists -- evidence for human inspection, consistent with the
    ``review`` artifact contract. If no document exists, ``docling_json_path`` is
    "" and a warning notes it. NEVER crashes.
    """
    warnings.append(message)

    json_path_str = ""
    schema_name = "DoclingDocument"
    schema_version = ""
    page_nos: list[int] = []
    if doc is not None:
        schema_name = getattr(doc, "schema_name", "DoclingDocument")
        schema_version = str(getattr(doc, "version", ""))
        try:
            page_nos = _page_numbers(doc)
        except Exception:  # pragma: no cover - a failed doc may lack .pages
            page_nos = []
        try:
            doc.save_as_json(json_path)
            json_path_str = str(json_path)
        except Exception:  # pragma: no cover - persist best-effort on a bad doc
            warnings.append(
                "Could not write a DoclingDocument JSON for the failed convert."
            )
    else:
        warnings.append("No DoclingDocument was produced for the failed convert.")

    per_page = [
        {
            "page_no": page_no,
            "native_chars": 0,
            "hit_rate": None,
            "parse_score": None,
            "ocr_score": None,
            "diagnosis": PageDiagnosis.uncertain_review.value,
            "ocr_applied": False,
            "confidence_grade": None,
            "flagged": True,
            "flag_reason": "conversion failed; page not trustworthy for extraction",
        }
        for page_no in page_nos
    ]

    return IngestResult(
        source_path=str(pdf_path),
        source_sha256=source_sha256,
        docling_json_path=json_path_str,
        schema_name=schema_name,
        schema_version=schema_version,
        n_pages=len(page_nos),
        doc_decision=DocDecision.review.value,
        trustworthy_for_extraction=False,
        ocr_engine_used="none",
        per_page=per_page,
        bates=[],
        warnings=warnings,
    )


def _failure_result_if_unparseable(
    res, *, pdf_path, source_sha256, json_path, warnings,
):
    """If pass #1 reported FAILURE, build the flagged result; else ``None``."""
    if not _is_failure(res):
        return None
    status_value = getattr(getattr(res, "status", None), "value", "failure")
    detail = _errors_summary(res)
    message = (
        f"Docling could not parse the PDF (status={status_value}; "
        "likely corrupt, encrypted, or not a valid PDF); routed to review, "
        "not extracted."
    )
    if detail:
        message += f" Docling errors: {detail}"
    return _build_failure_result(
        pdf_path=pdf_path, source_sha256=source_sha256, json_path=json_path,
        warnings=warnings, doc=getattr(res, "document", None), message=message,
    )


# --------------------------------------------------------------------------- #
# The Docling edge -- the ONLY place docling is imported (LAZILY).
# --------------------------------------------------------------------------- #


def _docling_imports():
    """Import docling LAZILY (heavy edge) and return the symbols ingest needs.

    Kept in one helper so importing ``scripts.ingest`` stays cheap (the layout /
    OCR models load on first convert, like ``pii_sweep``'s lazy spaCy edge).
    ``ConversionStatus`` is imported here too (same lazy edge) so the ugly-PDF
    failure check (design §2.6) never adds a docling import at module top.
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat, ConversionStatus
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    return (DocumentConverter, PdfFormatOption, InputFormat, ConversionStatus,
            PdfPipelineOptions, RapidOcrOptions)


def _convert(pdf_path, *, do_ocr, force_full_page_ocr, lang,
             max_num_pages, max_file_size, page_range, document_timeout):
    """One Docling convert with an explicit ingest profile (don't inherit
    defaults). do_table_structure OFF for latency (the gate needs text, not table
    geometry); enrichments off. convert() large-doc guards from prior-art §2.6
    plus a ``document_timeout`` hard wall-clock ceiling (an over-long convert
    aborts to PARTIAL_SUCCESS rather than hanging). ``raises_on_error=False`` so a
    corrupt/encrypted PDF returns ConversionStatus.FAILURE (caught by the caller)
    instead of raising.
    """
    (DocumentConverter, PdfFormatOption, InputFormat, _ConversionStatus,
     PdfPipelineOptions, RapidOcrOptions) = _docling_imports()

    opts = PdfPipelineOptions(do_ocr=do_ocr)
    opts.do_table_structure = False
    opts.document_timeout = document_timeout
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
    document_timeout: float = _DEFAULT_DOCUMENT_TIMEOUT,
) -> IngestResult:
    """Ingest a PDF into a ``DoclingDocument`` JSON (internal) + an ``IngestResult``.

    Pipeline (design §3): source hash -> Docling ``do_ocr=False`` pass -> pure gate
    (diagnose each page -> conservative doc decision) -> apply the decision (reuse
    the native pass, or a second OCR convert) -> normalize prov origins ->
    save_as_json -> Bates post-pass + degraded-page flags. The
    ``DoclingDocument`` JSON is ALWAYS written (incl. on ``review`` -- evidence
    for human inspection); never Markdown (it would drop the bbox/charspan a
    citation needs).

    Ugly-PDF safety (design §2.6): a corrupt / encrypted / unparseable PDF that
    Docling reports as ``ConversionStatus.FAILURE`` short-circuits to a flagged
    ``review`` result (no bad-document dereference, no OCR); ``document_timeout``
    caps each convert so a pathological PDF aborts instead of hanging.

    docling is imported LAZILY inside ``_convert`` so importing this module is
    cheap. ``decide_doc`` is referenced via this module's namespace so the
    review-doc test can monkeypatch ``scripts.ingest.decide_doc``.
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    json_path = out_dir / f"{pdf_path.stem}.docling.json"

    source_sha256 = sha256_file(pdf_path)

    # ---- Pass #1: do_ocr=False -> what the gate sees (reused if native). ----
    # raises_on_error=False makes a corrupt/encrypted PDF come back as
    # ConversionStatus.FAILURE (verified). The try/except is a belt-and-suspenders
    # for the rare backend that raises DESPITE that flag -- the contract is "no
    # crash, return a flagged result" (design §2.6), so a raise routes to the same
    # skip-and-flag path.
    try:
        res = _convert(
            pdf_path, do_ocr=False, force_full_page_ocr=False, lang=lang,
            max_num_pages=max_num_pages, max_file_size=max_file_size,
            page_range=page_range, document_timeout=document_timeout,
        )
    except Exception as exc:  # pragma: no cover - exercised only if docling raises
        return _build_failure_result(
            pdf_path=pdf_path, source_sha256=source_sha256, json_path=json_path,
            warnings=warnings, doc=None,
            message=(
                "Docling raised while converting the PDF "
                f"({type(exc).__name__}: {exc}); likely corrupt, encrypted, or "
                "not a valid PDF; routed to review, not extracted."
            ),
        )

    # ---- Ugly-PDF gate (design §2.6): a FAILURE convert never gets
    # dereferenced for extraction. Skip-and-flag: a review result, OCR never
    # runs, a (minimal) JSON is still written if a document object exists. ----
    failure_result = _failure_result_if_unparseable(
        res, pdf_path=pdf_path, source_sha256=source_sha256,
        json_path=json_path, warnings=warnings,
    )
    if failure_result is not None:
        return failure_result

    doc = res.document
    page_nos = _page_numbers(doc)
    native_by_page = _page_native_text(doc)

    # A PARTIAL_SUCCESS proceeds (partial geometry is still useful) but is noted +
    # the whole doc is flagged not-fully-trustworthy.
    partial = _is_partial_success(res)
    if partial:
        warnings.append(
            "Docling reported PARTIAL_SUCCESS (incomplete conversion); "
            "pages flagged for review."
        )

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
            max_num_pages=max_num_pages, max_file_size=max_file_size,
            page_range=page_range, document_timeout=document_timeout,
        )
        doc = res.document
        ocr_engine_used = "rapidocr"
    elif decision is DocDecision.force_full_doc_ocr:
        res = _convert(
            pdf_path, do_ocr=True, force_full_page_ocr=True, lang=lang,
            max_num_pages=max_num_pages, max_file_size=max_file_size,
            page_range=page_range, document_timeout=document_timeout,
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
    doc.save_as_json(json_path)

    # ---- Bates post-pass (separate list; never rewrites text). ----
    bates = _collect_bates(doc)

    # ---- Per-page records + degraded flagging (nan-safe). ----
    # hit_rate reuses the gate's DEFAULT wordlist so per_page matches what the
    # gate actually scored (design §5). Loaded once; None when no alpha tokens.
    wordlist = load_default_wordlist()
    review = decision is DocDecision.review
    diag_by_page = dict(zip(page_nos, diagnoses))
    per_page: list[dict[str, Any]] = []
    for page_no in page_nos:
        diag = diag_by_page[page_no]
        native_text = native_by_page.get(page_no, "")
        ocr_score = _page_ocr_score(res, page_no)
        parse_score = _page_parse_score(res, page_no)
        ocr_applied = _page_ocr_applied(decision, diag)

        flag_reason = _flag_reason(
            diag=diag, ocr_applied=ocr_applied, ocr_score=ocr_score,
            review=review, partial=partial, ocrmypdf_unavailable=ocrmypdf_unavailable,
        )
        per_page.append({
            "page_no": page_no,
            "native_chars": len(native_text.strip()),
            "hit_rate": wordlist_hit_rate(native_text, wordlist),
            "parse_score": parse_score,
            "ocr_score": ocr_score,
            "diagnosis": diag.value,
            "ocr_applied": ocr_applied,
            "confidence_grade": _page_confidence_grade(res, page_no),
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
        trustworthy_for_extraction=not (review or partial),
        ocr_engine_used=ocr_engine_used,
        per_page=per_page,
        bates=bates,
        warnings=warnings,
    )


def _flag_reason(
    *, diag, ocr_applied, ocr_score, review, partial, ocrmypdf_unavailable,
) -> str | None:
    """The reason this page is flagged for a human, or None if it is trustworthy.

    nan-safe (design §2.4): a clean native page (ocr_score nan) and a clean scan
    (parse_score nan) are NOT flagged on a missing-modality score. ``ocr_applied``
    is PAGE-ACCURATE (did OCR run on THIS page), so an image-only page that the
    decision did OCR is not spuriously flagged "not OCR'd". A page is flagged only
    when a signal is GENUINELY bad:
      * ``review`` decision OR ``partial`` (PARTIAL_SUCCESS) -> every page flagged;
      * the gate diagnosed the page garbled / uncertain / image-only-not-OCR'd;
      * OCR ran and the page's real ``ocr_score`` is below the degraded floor;
      * an OCRmyPDF op was requested but skipped (Tesseract absent).
    """
    if review:
        return "review: document not trustworthy for unattended extraction"
    if partial:
        return "partial conversion (Docling PARTIAL_SUCCESS)"
    if diag is PageDiagnosis.garbled_text:
        return "garbled text layer"
    if diag is PageDiagnosis.uncertain_review:
        return "uncertain: too little signal to trust the text layer"
    if diag is PageDiagnosis.image_only and not ocr_applied:
        return "image-only page not OCR'd"
    if ocr_applied and _is_real(ocr_score) and ocr_score < _DEGRADED_OCR_SCORE:
        return f"low OCR confidence ({ocr_score:.2f})"
    if ocrmypdf_unavailable:
        return "OCRmyPDF preprocessing requested but unavailable (Tesseract absent)"
    return None

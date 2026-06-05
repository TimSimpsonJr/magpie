"""detect_tier.py -- Magpie Layer 0-1 capability probe (the setup/doctor engine).

Pure-core / IO-at-the-edge, like pii_sweep's injectable classifier and
ingest_gate's injectable wordlist: build_capability_map / summarize / render_text
are PURE functions of an injected probe dict (golden-testable with mocked
presence/absence); the check_* probe functions are the only IO edge (stdlib only:
importlib.metadata, importlib.util, shutil.which, subprocess -- no heavy imports,
no network, no side effects). Probing torch/docling/spacy via metadata.version
does NOT load them, so doctor stays fast.
"""
from __future__ import annotations

import importlib.metadata as _md
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

READY = "READY"
DEGRADED = "DEGRADED"
UNAVAILABLE = "UNAVAILABLE"

# Probe by DISTRIBUTION name (the names installed into the venv that
# requirements-dev.txt bootstraps -- some, like pikepdf / pdfminer.six / requests /
# cryptography, arrive as TRANSITIVE deps, not top-level pins, but are resolvable
# distributions all the same). dist name != import name (x-ray->xray,
# PyMuPDF->fitz, pdfminer.six->pdfminer, rfc3161-client->rfc3161_client,
# sqlite-utils->sqlite_utils, charset-normalizer->charset_normalizer), so
# metadata.version on the dist name is the unambiguous probe and never imports
# the package.
_CORE_DISTS = ["pandas", "numpy", "duckdb", "pyarrow", "openpyxl",
               "charset-normalizer", "sqlite-utils"]
_INGEST_DISTS = ["docling", "rapidocr", "onnxruntime", "torch"]
_REDACT_OFFLINE_DISTS = ["pikepdf", "pdfminer.six"]
_EVIDENCE_DISTS = ["rfc3161-client", "requests", "cryptography"]
_XRAY_DIST = "x-ray"
_OCRMYPDF_DIST = "ocrmypdf"
_SPACY_DIST = "spacy"
_SPACY_MODEL = "en_core_web_lg"

_ALL_DISTS = sorted(set(
    _CORE_DISTS + _INGEST_DISTS + _REDACT_OFFLINE_DISTS + _EVIDENCE_DISTS
    + [_XRAY_DIST, _OCRMYPDF_DIST, _SPACY_DIST]
))

_DOC_CAPS = ["ingest native PDFs", "PII scan", "redaction QA", "citation verify",
             "evidence timestamp", "OCR preprocessing for scans"]


def _missing(dists, names):
    return [n for n in names if not dists.get(n, {}).get("present")]


def _cap(requires, missing, optional_missing, blocks, fix, optional_fix=None,
         degraded_note=None, unavailable_note=None):
    # fix is for the UNAVAILABLE (required-missing) case; optional_fix is for the
    # DEGRADED (optional-missing) case -- a system binary that pip/bootstrap
    # cannot install must NOT be told to "run bootstrap".
    if missing:
        status, the_fix = UNAVAILABLE, fix
    elif optional_missing:
        status = DEGRADED
        the_fix = optional_fix if optional_fix is not None else fix
    else:
        status, the_fix = READY, None
    entry = {
        "status": status,
        "requires": list(requires),
        "missing": list(missing),
        "optional_missing": list(optional_missing),
        "blocks": blocks,
        "fix": the_fix,
    }
    if status == DEGRADED and degraded_note:
        entry["note"] = degraded_note
    if status == UNAVAILABLE and unavailable_note:
        entry["note"] = unavailable_note
    return entry


def build_capability_map(probes):
    """PURE. probes (the run_probes shape) -> {capability: cap-entry}. No IO."""
    dists = probes.get("dists", {})
    model = probes.get("spacy_model", {})
    tess = probes.get("tesseract", {})
    gs = probes.get("ghostscript", {})
    ossl = probes.get("openssl_ts", {})
    mcp = probes.get("mcp", {})

    caps = {}

    # The conversational query surface needs BOTH uvx (to launch the server) AND
    # a .mcp.json that declares mcp-sqlite -- all three, or it is unavailable.
    mcp_ok = (mcp.get("uvx_present") and mcp.get("mcp_json_present")
              and mcp.get("declares_mcp_sqlite"))
    caps["analyze datasets"] = _cap(
        requires=_CORE_DISTS,
        missing=_missing(dists, _CORE_DISTS),
        optional_missing=([] if mcp_ok
                          else ["the conversational mcp-sqlite query surface (uvx + .mcp.json wiring)"]),
        blocks="Quantitative analysis of FOIA CSV/XLSX releases (stats, recipes, rollups).",
        degraded_note="analysis runs, but the conversational SQL query surface (mcp-sqlite) is unavailable",
        fix="run setup (mise run bootstrap)",
        optional_fix="install uv (provides uvx) and ensure .mcp.json declares the mcp-sqlite server; see OPERATOR_GUIDE.md",
    )

    caps["ingest native PDFs"] = _cap(
        requires=_INGEST_DISTS,
        missing=_missing(dists, _INGEST_DISTS),
        optional_missing=[],
        blocks="Turning PDF document releases into clean, citable text.",
        fix="run setup (mise run bootstrap)",
    )

    ocr_missing = _missing(dists, [_OCRMYPDF_DIST])
    if not tess.get("present"):
        ocr_missing.append("tesseract (system binary)")
    if not gs.get("present"):
        ocr_missing.append("ghostscript (system binary)")
    caps["OCR preprocessing for scans"] = _cap(
        requires=[_OCRMYPDF_DIST, "tesseract", "ghostscript"],
        missing=ocr_missing,
        optional_missing=[],
        blocks="Deskew / re-OCR of ugly scanned PDFs before ingest (native-text PDFs are unaffected).",
        fix="install Tesseract + Ghostscript (see OPERATOR_GUIDE.md), then run setup",
    )

    pii_missing = _missing(dists, [_SPACY_DIST])
    if not model.get("present"):
        pii_missing.append(_SPACY_MODEL + " (spaCy model)")
    caps["PII scan"] = _cap(
        requires=[_SPACY_DIST, _SPACY_MODEL],
        missing=pii_missing,
        optional_missing=[],
        blocks="Authoritative PERSON-name + structured-PII exposure tally; redaction of uninvolved names.",
        fix="run setup (mise run bootstrap)",
    )

    caps["redaction QA"] = _cap(
        requires=_REDACT_OFFLINE_DISTS,
        missing=_missing(dists, _REDACT_OFFLINE_DISTS),
        optional_missing=([] if dists.get(_XRAY_DIST, {}).get("present")
                          else ["x-ray (box-over-text, the 8th check)"]),
        blocks="Finding bad redactions in a received PDF and pre-publish self-checks.",
        degraded_note="7 of 8 checks run; the x-ray box-over-text check is unavailable",
        fix="run setup (mise run bootstrap)",
    )

    caps["citation verify"] = _cap(
        requires=["(stdlib engine)"] + _INGEST_DISTS,
        missing=_missing(dists, _INGEST_DISTS),
        optional_missing=[],
        blocks="Anchoring published claims to a verifiable source span in an ingested document.",
        unavailable_note="the citation engine is stdlib, but verifying needs the ingest stack to produce documents",
        fix="run setup (mise run bootstrap)",
    )

    caps["evidence timestamp"] = _cap(
        requires=_EVIDENCE_DISTS,
        missing=_missing(dists, _EVIDENCE_DISTS),
        optional_missing=([] if ossl.get("ts_subcommand")
                          else ["openssl 'ts' subcommand (cross-tool verify)"]),
        blocks="Hash-on-receipt + RFC 3161 trusted timestamp + chain-of-custody for FOIA evidence.",
        degraded_note="timestamping and verify-on-store work; the openssl second-tool cross-check is unavailable",
        fix="run setup (mise run bootstrap)",
        optional_fix="install OpenSSL providing the 'ts' subcommand; see OPERATOR_GUIDE.md",
    )

    return caps


def summarize(capability_map):
    """PURE. The two-line subordinate headline. Core is BINARY READY/NOT READY
    (required deps only -- an optional reduction like a missing uvx lives in the
    capability map, NOT the headline); the document rollup is
    READY/PARTIAL/UNAVAILABLE. NO 1/2/3 tier score is ever produced."""
    core_status = capability_map.get("analyze datasets", {}).get("status")
    core_ready = core_status != UNAVAILABLE  # DEGRADED (e.g. no uvx) still READY in the headline
    doc_statuses = {c: capability_map.get(c, {}).get("status") for c in _DOC_CAPS}
    vals = list(doc_statuses.values())
    if vals and all(v == READY for v in vals):
        doc = "READY"
    elif vals and all(v == UNAVAILABLE for v in vals):
        doc = "UNAVAILABLE"
    else:
        doc = "PARTIAL"
    reduced = [c for c, st in doc_statuses.items() if st != READY]
    return {
        "core_structured_data": "READY" if core_ready else "NOT READY",
        "core_ready": core_ready,
        "document_workflows": doc,
        "document_reduced": reduced,
    }

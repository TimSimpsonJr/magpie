"""TDD for scripts/detect_tier.py -- pure capability map + stdlib probe edge.

The pure aggregator is golden-tested from INJECTED probe dicts (mocked
presence/absence), so the suite never depends on what is installed on the box.
The probe functions are unit-tested with monkeypatched stdlib.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import detect_tier as dt

CORE = ["pandas", "numpy", "duckdb", "pyarrow", "openpyxl", "charset-normalizer", "sqlite-utils"]
INGEST = ["docling", "rapidocr", "onnxruntime", "torch"]
REDACT_OFFLINE = ["pikepdf", "pdfminer.six"]
EVIDENCE = ["rfc3161-client", "requests", "cryptography"]
EXTRA = ["x-ray", "ocrmypdf", "spacy"]
ALL_DISTS = CORE + INGEST + REDACT_OFFLINE + EVIDENCE + EXTRA


def _present(version="9.9.9"):
    return {"present": True, "version": version}


def all_present_probes():
    """A probe dict with EVERYTHING present -- tests mutate it to absent."""
    return {
        "dists": {d: _present() for d in ALL_DISTS},
        "spacy_model": {"present": True, "version": "3.8.0"},
        "tesseract": {"present": True, "path": "/usr/bin/tesseract", "name": "tesseract"},
        "ghostscript": {"present": True, "path": "/usr/bin/gs", "name": "gs"},
        "openssl_ts": {"present": True, "path": "/usr/bin/openssl", "ts_subcommand": True},
        "mcp": {"uvx_present": True, "uvx_path": "/usr/bin/uvx",
                "mcp_json_present": True, "declares_mcp_sqlite": True},
        "platform": {"os": "linux", "arch": "x86_64"},
    }


def test_all_present_every_capability_ready():
    cm = dt.build_capability_map(all_present_probes())
    assert set(cm) == {
        "analyze datasets", "ingest native PDFs", "OCR preprocessing for scans",
        "PII scan", "redaction QA", "citation verify", "evidence timestamp",
    }
    for name, cap in cm.items():
        assert cap["status"] == dt.READY, name
    s = dt.summarize(cm)
    assert s["core_structured_data"] == dt.READY
    assert s["document_workflows"] == "READY"


def test_all_absent_required_caps_unavailable():
    probes = all_present_probes()
    probes["dists"] = {d: {"present": False, "version": None} for d in ALL_DISTS}
    probes["spacy_model"] = {"present": False, "version": None}
    probes["tesseract"] = {"present": False, "path": None, "name": None}
    probes["ghostscript"] = {"present": False, "path": None, "name": None}
    probes["openssl_ts"] = {"present": False, "path": None, "ts_subcommand": False}
    probes["mcp"] = {"uvx_present": False, "uvx_path": None,
                     "mcp_json_present": False, "declares_mcp_sqlite": False}
    cm = dt.build_capability_map(probes)
    for name, cap in cm.items():
        assert cap["status"] == dt.UNAVAILABLE, name
    s = dt.summarize(cm)
    assert s["core_structured_data"] == "NOT READY"
    assert s["core_ready"] is False
    assert s["document_workflows"] == "UNAVAILABLE"


def test_independence_ocr_binaries_absent_does_not_break_ingest():
    """The load-bearing honesty rule: missing Tesseract/Ghostscript makes ONLY
    'OCR preprocessing for scans' unavailable; ingest native PDFs stays READY."""
    probes = all_present_probes()
    probes["tesseract"] = {"present": False, "path": None, "name": None}
    probes["ghostscript"] = {"present": False, "path": None, "name": None}
    cm = dt.build_capability_map(probes)
    assert cm["OCR preprocessing for scans"]["status"] == dt.UNAVAILABLE
    assert cm["ingest native PDFs"]["status"] == dt.READY
    # names the missing binaries, not a package
    miss = " ".join(cm["OCR preprocessing for scans"]["missing"]).lower()
    assert "tesseract" in miss and "ghostscript" in miss


def test_xray_absent_redaction_degraded_not_unavailable():
    probes = all_present_probes()
    probes["dists"]["x-ray"] = {"present": False, "version": None}
    cm = dt.build_capability_map(probes)
    cap = cm["redaction QA"]
    assert cap["status"] == dt.DEGRADED
    assert any("x-ray" in m for m in cap["optional_missing"])


def test_uvx_absent_analyze_datasets_degraded_not_unavailable():
    probes = all_present_probes()
    probes["mcp"]["uvx_present"] = False
    probes["mcp"]["uvx_path"] = None
    cm = dt.build_capability_map(probes)
    assert cm["analyze datasets"]["status"] == dt.DEGRADED


def test_core_headline_is_binary_when_only_uvx_missing():
    """The core HEADLINE stays READY when only an optional (uvx) is missing; the
    reduction lives in the capability map, not the headline (design 3.1 binary)."""
    probes = all_present_probes()
    probes["mcp"]["uvx_present"] = False
    s = dt.summarize(dt.build_capability_map(probes))
    assert s["core_structured_data"] == "READY"
    assert s["core_ready"] is True


def test_mcp_json_missing_or_undeclared_degrades_analyze_datasets():
    """uvx present but .mcp.json missing or not declaring mcp-sqlite -> DEGRADED,
    never a false READY for the conversational query surface."""
    for bad in ({"mcp_json_present": False}, {"declares_mcp_sqlite": False}):
        probes = all_present_probes()
        probes["mcp"].update(bad)
        cm = dt.build_capability_map(probes)
        assert cm["analyze datasets"]["status"] == dt.DEGRADED


def test_optional_fix_for_system_binaries_is_not_bootstrap():
    """A missing SYSTEM binary (uvx, openssl ts) must NOT be told to run bootstrap
    -- bootstrap is pip and cannot install a system binary."""
    probes = all_present_probes()
    probes["mcp"]["uvx_present"] = False
    probes["openssl_ts"]["ts_subcommand"] = False
    cm = dt.build_capability_map(probes)
    assert "bootstrap" not in cm["analyze datasets"]["fix"].lower()
    assert "bootstrap" not in cm["evidence timestamp"]["fix"].lower()
    # a missing REQUIRED pip dep still points at bootstrap
    probes2 = all_present_probes()
    probes2["dists"]["docling"] = {"present": False, "version": None}
    cm2 = dt.build_capability_map(probes2)
    assert "bootstrap" in cm2["ingest native PDFs"]["fix"].lower()


def test_openssl_ts_absent_evidence_degraded():
    probes = all_present_probes()
    probes["openssl_ts"]["ts_subcommand"] = False
    cm = dt.build_capability_map(probes)
    assert cm["evidence timestamp"]["status"] == dt.DEGRADED


def test_spacy_model_absent_pii_unavailable_names_model():
    probes = all_present_probes()
    probes["spacy_model"] = {"present": False, "version": None}  # spacy dist still present
    cm = dt.build_capability_map(probes)
    cap = cm["PII scan"]
    assert cap["status"] == dt.UNAVAILABLE
    assert any("en_core_web_lg" in m for m in cap["missing"])


def test_citation_verify_tracks_ingest_stack():
    probes = all_present_probes()
    probes["dists"]["docling"] = {"present": False, "version": None}
    cm = dt.build_capability_map(probes)
    assert cm["citation verify"]["status"] == dt.UNAVAILABLE
    # ingest also unavailable, citation verify mirrors it
    assert cm["ingest native PDFs"]["status"] == dt.UNAVAILABLE


def test_document_workflows_partial_when_some_reduced():
    probes = all_present_probes()
    probes["tesseract"] = {"present": False, "path": None, "name": None}
    probes["ghostscript"] = {"present": False, "path": None, "name": None}
    cm = dt.build_capability_map(probes)
    s = dt.summarize(cm)
    assert s["document_workflows"] == "PARTIAL"
    assert "OCR preprocessing for scans" in s["document_reduced"]

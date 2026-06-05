"""Smoke test for the ``ingest`` SKILL.md frontmatter + body.

Mirrors ``test_pii_sweep_skill.py``: a malformed or mis-named skill silently
fails to load in Claude Code, so guard the YAML frontmatter (name / description /
version) and that the body documents the gate, the internal-JSON rule, the
review/trustworthy contract, the OCRmyPDF seam, and the downstream hand-offs.
No runtime wiring (no .mcp.json) ships with this skill -- the engine it documents
is covered by ``test_ingest_gate.py`` + ``test_ingest.py``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_SKILL = Path(__file__).resolve().parent.parent / "skills" / "ingest" / "SKILL.md"


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), "SKILL.md must open with a YAML frontmatter block"
    _, fm, _body = text.split("---", 2)
    data = yaml.safe_load(fm)
    assert isinstance(data, dict), "frontmatter must parse to a mapping"
    return data


def _body(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("---", 2)[2]


def test_skill_md_exists():
    assert _SKILL.is_file(), f"missing skill file: {_SKILL}"


def test_frontmatter_has_required_fields():
    fm = _frontmatter(_SKILL)
    assert fm.get("name") == "ingest"
    assert isinstance(fm.get("description"), str) and fm["description"].strip()
    assert "version" in fm


def test_description_has_triggers_and_mentions_pdf_provenance():
    desc = _frontmatter(_SKILL)["description"].lower()
    # the trigger description must name what it ingests + the provenance promise.
    assert "pdf" in desc or "document" in desc
    assert "provenance" in desc or "bbox" in desc or "bounding-box" in desc


def test_body_documents_gate_and_internal_json():
    body = _body(_SKILL).lower()
    # the gate decides native-vs-re-OCR BEFORE OCR; the JSON is internal, never Markdown.
    assert "doclingdocument" in body
    assert "never markdown" in body or "internal" in body
    assert "ocr" in body  # the gate is about native-text-vs-re-OCR


def test_body_documents_review_contract_and_ocrmypdf_seam():
    body = _body(_SKILL)
    # the safety contract + the Tesseract-gated OCRmyPDF seam.
    assert "trustworthy_for_extraction" in body
    assert "OCRmyPDF" in body and "Tesseract" in body


def test_body_names_the_engine_and_downstream_consumers():
    body = _body(_SKILL)
    # the engine modules + the Phase-7/8 hand-offs.
    assert "ingest_gate" in body and "ingest.py" in body
    assert "investigate" in body and "redaction-check" in body

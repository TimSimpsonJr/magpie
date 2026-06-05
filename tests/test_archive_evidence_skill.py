"""Smoke test for the archive-evidence SKILL.md (mirrors test_investigate_skill)."""
from __future__ import annotations

from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent / "skills" / "archive-evidence" / "SKILL.md"


def _frontmatter_and_body(p):
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---")
    _, fm, body = text.split("---", 2)
    return yaml.safe_load(fm), body


def test_frontmatter():
    fm, _ = _frontmatter_and_body(SKILL)
    assert fm["name"] == "archive-evidence"
    assert "version" in fm
    d = fm["description"].lower()
    assert "provenance" in d or "custody" in d
    assert "timestamp" in d or "evidence" in d


def test_body_documents_contracts():
    _, body = _frontmatter_and_body(SKILL)
    low = body.lower()
    assert "evidence.py" in body                       # names the engine
    assert "archive_evidence" in body
    assert "on receipt" in low or "on-receipt" in low  # receipt-first ordering
    assert "rfc 3161" in low or "rfc3161" in low
    assert "custody" in low and "manifest" in low
    assert "librarian" in low                          # the note split
    assert "tamper-evident" in low                     # honest custody limit
    # honest limits / degrade vocabulary
    assert "unavailable" in low and "verified" in low
    assert "does not prove" in low or "does not establish" in low

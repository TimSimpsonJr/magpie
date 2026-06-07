"""Smoke test for the setup SKILL.md."""
from __future__ import annotations

from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent / "skills" / "setup" / "SKILL.md"


def _frontmatter_and_body(p):
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---")
    _, fm, body = text.split("---", 2)
    return yaml.safe_load(fm), body


def test_frontmatter():
    fm, _ = _frontmatter_and_body(SKILL)
    assert fm["name"] == "setup"
    assert "version" in fm
    d = fm["description"].lower()
    assert "operator" in d or "install" in d or "set up" in d


def test_body_documents_setup_contract():
    _, body = _frontmatter_and_body(SKILL)
    low = body.lower()
    assert "detect_tier" in body                       # names the engine
    assert "mise run bootstrap" in low                 # runs the repo-managed step
    assert "tesseract" in low and "ghostscript" in low # instructs for system binaries
    assert "operator" in low
    # setup MAY install; doctor is the read-only sibling -- the asymmetry is stated
    assert "doctor" in low
    # Layer 2 (Phase 13a): setup INSTRUCTS for Docker (entity-graph) but NEVER
    # auto-installs it -- it points the operator to the official installer. (The
    # Layer 0-1 no-Docker framing now applies only to the journalist/operator
    # ONRAMP docs; that is pinned by test_onramp_docs.py.)
    assert "docker" in low                             # Layer-2 entity-graph section
    assert "never auto-installs" in low or "never silently installs" in low

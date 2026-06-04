"""Smoke test for the ``pii-sweep`` SKILL.md frontmatter.

Mirrors ``test_analysis_recipe_skill.py``: a malformed or mis-named skill
silently fails to load in Claude Code, so guard the YAML frontmatter (name /
description / version) and that the body documents the engine + the redact-output
seam. No runtime wiring (no .mcp.json) ships with this skill -- the engine it
documents is covered by ``test_pii_sweep.py``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_SKILL = Path(__file__).resolve().parent.parent / "skills" / "pii-sweep" / "SKILL.md"


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), "SKILL.md must open with a YAML frontmatter block"
    # Split on the first two '---' fences and parse the block between them.
    _, fm, _body = text.split("---", 2)
    data = yaml.safe_load(fm)
    assert isinstance(data, dict), "frontmatter must parse to a mapping"
    return data


def test_skill_md_exists():
    assert _SKILL.is_file(), f"missing skill file: {_SKILL}"


def test_frontmatter_has_required_fields():
    fm = _frontmatter(_SKILL)
    assert fm.get("name") == "pii-sweep"
    assert isinstance(fm.get("description"), str) and fm["description"].strip()
    assert "version" in fm


def test_description_mentions_pii():
    desc = _frontmatter(_SKILL)["description"]
    # case-insensitive: the trigger description must name what it sweeps.
    assert "pii" in desc.lower()


def test_body_documents_engine_and_redact_seam():
    body = _SKILL.read_text(encoding="utf-8").split("---", 2)[2]
    # the engine + the Phase 7 hand-off seam.
    assert "pii_sweep" in body
    assert "redact-output" in body


def test_body_documents_officials_split_and_strict_headline():
    body = _SKILL.read_text(encoding="utf-8").split("---", 2)[2]
    # officials are split out of exposure; the strict tally is the headline.
    assert "person_official" in body
    assert "strict" in body

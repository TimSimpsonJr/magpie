"""Smoke test for the ``analysis-recipe`` SKILL.md frontmatter.

Mirrors the frontmatter checks in ``test_dataset_analyze_wiring.py``: a malformed
or mis-named skill silently fails to load in Claude Code, so guard the YAML
frontmatter (name / description / version) and that the description carries
trigger phrases. No runtime wiring (no .mcp.json) ships with this skill -- the
scripts it documents are covered by ``test_recipe.py`` / ``test_rollup.py``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_SKILL = Path(__file__).resolve().parent.parent / "skills" / "analysis-recipe" / "SKILL.md"


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
    assert fm.get("name") == "analysis-recipe"
    assert isinstance(fm.get("description"), str) and fm["description"].strip()
    assert "version" in fm


def test_description_carries_trigger_phrases():
    fm = _frontmatter(_SKILL)
    desc = fm["description"].lower()
    # third-person trigger framing + at least one concrete recipe trigger phrase.
    assert "this skill should be used when" in desc
    assert any(
        phrase in desc
        for phrase in ("13-point", "analysis recipe", "recurrence", "roll up")
    )


def test_body_references_both_scripts():
    body = _SKILL.read_text(encoding="utf-8")
    assert "scripts/recipe.py" in body
    assert "scripts/rollup.py" in body

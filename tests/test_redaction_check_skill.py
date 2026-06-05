"""Smoke test for the ``redaction-check`` SKILL.md frontmatter + body.

Mirrors ``test_ingest_skill.py``: a malformed or mis-named skill silently fails to
load in Claude Code, so guard the YAML frontmatter (name / description / version)
and that the body documents the leads-not-verdicts stance, the dual mode, the
never-publish-raw split, the fail-closed gate, and the engine + downstream
hand-offs. No .mcp.json ships -- the engine is covered by ``test_redaction_check.py``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_SKILL = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "redaction-check"
    / "SKILL.md"
)


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
    assert fm.get("name") == "redaction-check"
    assert isinstance(fm.get("description"), str) and fm["description"].strip()
    assert "version" in fm


def test_description_has_triggers_and_mentions_bad_redactions():
    desc = _frontmatter(_SKILL)["description"].lower()
    assert "redaction" in desc or "redactions" in desc
    assert "pdf" in desc


def test_body_documents_leads_not_verdicts_and_modes():
    body = _body(_SKILL).lower()
    # leads, never verdicts + the honesty footer; the dual mode.
    assert "lead" in body and "verdict" in body
    assert "cannot_catch" in body
    assert "received" in body and "pre-publish" in body


def test_body_documents_never_publish_raw_and_fail_closed():
    body = _body(_SKILL)
    # the publish-path safety contract + the fail-closed gate.
    assert "local_evidence" in body and "publishable_view" in body
    assert "safe_to_publish" in body and "fail-closed".lower() in body.lower()


def test_body_names_the_engine_and_downstream():
    body = _body(_SKILL)
    assert "redaction_check.py" in body or "redaction_check" in body
    # the ingest seam + the redact-output sibling.
    assert "ingest" in body and "redact-output" in body

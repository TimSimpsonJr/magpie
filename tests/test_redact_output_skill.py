"""Smoke test for the ``redact-output`` SKILL.md frontmatter + body.

Mirrors ``test_ingest_skill.py``: guard the YAML frontmatter (name / description /
version) and that the body documents the involved-vs-uninvolved policy + keep_names,
the four entry points + the never-publish-raw contract, the pii_sweep local_texts
seam, and the vault-guarded local exhibit. No .mcp.json ships -- the engine is
covered by ``test_redact_output.py``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_SKILL = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "redact-output"
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
    assert fm.get("name") == "redact-output"
    assert isinstance(fm.get("description"), str) and fm["description"].strip()
    assert "version" in fm


def test_description_has_triggers_and_mentions_redaction():
    desc = _frontmatter(_SKILL)["description"].lower()
    assert "redact" in desc
    assert "initials" in desc or "pii" in desc


def test_body_documents_involved_vs_uninvolved_policy():
    body = _body(_SKILL).lower()
    assert "involved" in body and "uninvolved" in body
    assert "keep_names" in body
    assert "official" in body


def test_body_documents_entry_points_and_never_publish_raw():
    body = _body(_SKILL)
    # the four entry points + the publish-path contract.
    assert "redact_text" in body and "redact_note" in body
    assert "redact_local_texts" in body and "write_local_exhibit" in body
    assert "text_id" in body  # the never-publish-raw join key
    assert "vault" in body.lower()  # the vault-guarded exhibit


def test_body_documents_pii_sweep_seam():
    body = _body(_SKILL)
    assert "local_texts" in body and "pii_sweep" in body
    assert "person_role_in_span" in body

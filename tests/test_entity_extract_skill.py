"""Frontmatter + body smoke test for skills/entity-extract/SKILL.md.

Mirrors the other Magpie skill smokes: split the YAML frontmatter, parse it,
and assert the description carries the trigger ideas and the body documents the
load-bearing decisions (human gate, ftmize hand-off, NC weights, PyICU/Windows
decouple, per-document scope, and the trustworthy refusal seam).
"""
from __future__ import annotations

import pathlib

import yaml

SKILL_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "skills"
    / "entity-extract"
    / "SKILL.md"
)


def _split_frontmatter(raw: str):
    """Return (frontmatter_dict, body_str) from a `---`-delimited skill file."""
    assert raw.startswith("---"), "SKILL.md must start with YAML frontmatter"
    parts = raw.split("---", 2)
    assert len(parts) >= 3, "SKILL.md frontmatter must be delimited by two '---' lines"
    front = yaml.safe_load(parts[1])
    body = parts[2]
    return front, body


def _load():
    raw = SKILL_PATH.read_text(encoding="utf-8")
    return _split_frontmatter(raw)


def test_skill_file_exists():
    assert SKILL_PATH.exists(), "skills/entity-extract/SKILL.md is missing"


def test_frontmatter_keys():
    front, _ = _load()
    assert front.get("name") == "entity-extract"
    assert isinstance(front.get("description"), str) and front["description"].strip()
    assert front.get("version"), "version must be present and non-empty"


def test_description_carries_entity_and_relation_extraction():
    front, _ = _load()
    desc = front["description"].lower()
    assert "entit" in desc
    assert "relation" in desc
    # Network / graph framing is a core trigger idea.
    assert ("network" in desc) or ("graph" in desc)
    # Both engines named in the description.
    assert "entity extraction" in desc
    assert "relation extraction" in desc


def test_body_documents_the_load_bearing_decisions():
    _, body = _load()
    low = body.lower()

    # 1. The mandatory human gate.
    assert "human" in low and "gate" in low
    assert "mandatory" in low

    # 2. The intermediate -> ftmize / FtM bundle hand-off.
    assert ("entity_ftmize" in low) or ("ftm bundle" in low)

    # 3. The GLiREL NC-weights / non-commercial note.
    assert ("non-commercial" in low) or ("cc by-nc-sa" in low) or ("nc by" in low)

    # 4. The PyICU / Windows decouple.
    assert "pyicu" in low
    assert "windows" in low

    # 5. Per-document scope -- no cross-document merge.
    assert "per-document" in low or "per document" in low
    assert ("cross-document" in low) or ("cross document" in low) or ("cross-doc" in low)

    # 6. The refuse-on-trustworthy seam.
    assert "trustworthy_for_extraction" in low

    # 7. Distinct from pii_sweep.
    assert "pii_sweep" in low

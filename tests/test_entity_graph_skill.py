"""Frontmatter + body smoke test for skills/entity-graph/SKILL.md.

Mirrors the other Magpie skill smokes: split the YAML frontmatter, parse it,
and assert the description carries the trigger ideas and the body documents the
load-bearing operator-flow concepts (the mandatory human review gate, resolve ->
review packet -> apply -> snapshot -> neo4j write, fail-closed apply, and the
Neo4j GPL licensing note). No pytest marker -> it runs in the offline suite.
"""
from __future__ import annotations

import pathlib

import yaml

SKILL_DIR = pathlib.Path(__file__).resolve().parents[1] / "skills" / "entity-graph"
SKILL_PATH = SKILL_DIR / "SKILL.md"
PRIOR_ART_PATH = SKILL_DIR / "references" / "prior-art.md"


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
    assert SKILL_PATH.exists(), "skills/entity-graph/SKILL.md is missing"


def test_frontmatter_keys():
    front, _ = _load()
    assert front.get("name") == "entity-graph"
    assert isinstance(front.get("description"), str) and front["description"].strip()


def test_body_documents_the_load_bearing_operator_concepts():
    _, body = _load()
    low = body.lower()

    # The body is non-trivial.
    assert len(body) > 800, "SKILL.md body is too thin to be the operator flow"

    # The mandatory human review gate -- the differentiator.
    assert "human" in low
    assert "review" in low
    assert "mandatory" in low
    assert "gate" in low

    # The operator-flow steps (real function/concept names).
    assert "resolve" in low
    assert ("review packet" in low) or ("review" in low)
    assert "apply" in low
    assert "snapshot" in low
    assert "neo4j" in low

    # The fail-closed apply discipline.
    assert ("fail-closed" in low) or ("fail closed" in low)

    # The Neo4j GPL licensing note (ship compose + docs, never the image).
    assert "gpl" in low


def test_prior_art_present_and_carries_verified_facts():
    assert PRIOR_ART_PATH.exists(), "references/prior-art.md is missing"
    text = PRIOR_ART_PATH.read_text(encoding="utf-8")
    assert text.strip(), "references/prior-art.md is empty"
    low = text.lower()
    # The chosen model-free matcher.
    assert "logic-v2" in low
    # The pinned Neo4j Community server image tag.
    assert "5.26.26" in text


def test_skill_md_is_ascii():
    """Guard: the skill stays subagent-readable (no non-ASCII bytes block reads)."""
    data = SKILL_PATH.read_bytes()
    bad = [b for b in data if b > 127]
    assert not bad, "SKILL.md must be pure ASCII; found non-ASCII bytes"

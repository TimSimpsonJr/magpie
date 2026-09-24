"""Smoke tests for the Jev usage guide and the three skill links (plan Task 11, spec 3.2).

PURE: reads markdown files only. Whitespace is collapsed before phrase matching so later
rewraps of the guide cannot break these assertions.
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
GUIDE = REPO / "skills" / "dataset-analyze" / "references" / "jev-guide.md"
LINKING_SKILLS = (
    REPO / "skills" / "dataset-analyze" / "SKILL.md",
    REPO / "skills" / "entity-crossref" / "SKILL.md",
    REPO / "skills" / "investigate" / "SKILL.md",
)
HEADINGS = (
    "## What Jev is",
    "## Turning it on",
    "## Use it for",
    "## Don't use it for",
    "## Writing questions",
    "## Reading answers",
    "## Privacy",
    "## Provenance",
)
REQUIRED_PHRASES = (
    "act", "check", "escalate", "--sample", "results.meta.json", "pandas",
    "entity-crossref", "magpie_jev", "openrouter_api_key", "names", "jev_live",
    "skipped: secret", "token count", "--text-fields", "spelled-out",
)


def _flat(path: pathlib.Path) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def test_guide_exists_and_is_ascii():
    assert GUIDE.is_file()
    assert GUIDE.read_bytes().isascii(), "jev-guide.md must be ASCII-only"


def test_guide_has_every_section_heading():
    lines = GUIDE.read_text(encoding="utf-8").splitlines()
    for heading in HEADINGS:
        assert heading in lines, f"missing heading line: {heading}"


def test_guide_mentions_required_terms():
    text = _flat(GUIDE).lower()
    missing = [p for p in REQUIRED_PHRASES if p not in text]
    assert not missing, f"guide is missing: {missing}"


def test_guide_worked_command_uses_jev_ask_flags():
    text = _flat(GUIDE)
    assert "scripts/jev_ask.py" in text
    for flag in ("--input", "--id-field", "--text-fields", "--type", "--question",
                 "--criteria-true", "--criteria-false", "--out", "--sample", "--seed"):
        assert flag in text, f"worked command lacks {flag}"


def test_guide_names_the_live_eval_command():
    text = _flat(GUIDE)
    assert "-m jev_live tests/test_jev_live_prescreen.py" in text


def test_guide_example_secret_hit_really_trips_the_guard():
    # The guide explains a benign skipped: secret row with a concrete example; keep it true.
    from scripts.jev_guards import guard

    text = _flat(GUIDE)
    assert "token_count: 5" in text
    assert guard("token_count: 5") == "secret"


def test_three_skills_link_the_guide():
    for skill in LINKING_SKILLS:
        assert "jev-guide.md" in skill.read_text(encoding="utf-8"), f"{skill} lacks the guide link"


def test_link_targets_resolve():
    for skill in LINKING_SKILLS:
        text = skill.read_text(encoding="utf-8")
        for target in re.findall(r"[\w./-]*jev-guide\.md", text):
            resolved = (skill.parent / target).resolve()
            assert resolved == GUIDE.resolve(), f"{skill}: {target} does not resolve to the guide"

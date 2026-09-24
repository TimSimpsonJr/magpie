"""README privacy statement stays honest about the opt-in Jev features.

The "Your data & privacy" callout must document the opt-in switch, the endpoint,
the local guards and the limits of the PII screen; the intro's "never leave your
machine" line must be qualified; and the README must point at the Jev guide.
The onramp invariants live in tests/test_onramp_docs.py and are not repeated here.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

CALLOUT_START = "> **Your data & privacy:**"
CALLOUT_TERMS = (
    "by default",
    "magpie_jev",
    "openrouter_api_key",
    "openrouter",
    "typesafe",
    "pii",
    "names",
    "not independently verified",
)


def _text() -> str:
    return README.read_text(encoding="utf-8")


def _privacy_callout(text: str) -> str:
    """The block from the callout start through the next Markdown heading."""
    start = text.find(CALLOUT_START)
    assert start != -1, "privacy callout missing"
    heading = re.search(r"^#{1,6} ", text[start:], flags=re.MULTILINE)
    end = start + heading.start() if heading else len(text)
    return " ".join(text[start:end].split())


def test_privacy_callout_documents_the_jev_opt_in():
    low = _privacy_callout(_text()).lower()
    missing = [term for term in CALLOUT_TERMS if term not in low]
    assert not missing, f"privacy callout lacks: {missing}"


def test_never_leave_your_machine_is_qualified():
    flat = " ".join(_text().split())
    sentences = re.split(r"(?<=[.!?])\s+", flat)
    hits = [s for s in sentences if "never leave your machine" in s.lower()]
    for sentence in hits:
        assert "by default" in sentence.lower(), sentence


def test_readme_points_at_the_jev_guide():
    text = _text()
    assert "jev-guide.md" in text or "jev_ask" in text

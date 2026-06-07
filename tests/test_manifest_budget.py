"""Budget guard for MANIFEST.md.

MANIFEST.md is loaded into context at the start of every session, so it must
stay a cheap, scannable one-line-per-file INDEX -- not a prose re-implementation
of the codebase. It drifted to 218 lines / ~15,100 words / ~52k tokens once the
per-phase "regenerate MANIFEST" step started APPENDING detail that belongs in the
design docs (the WHY), the module docstrings (the HOW / source of truth), and the
tests (the contract). This test is the recurrence guard: depth must live in those
homes, and the MANIFEST must point to them, never duplicate them.

The budgets are deliberately generous (well above a healthy ~85-line index) so a
normal phase addition never trips them -- they fire only on paragraph-style bloat.
If a change legitimately needs more room, raise the constant in the SAME commit
with a reason; do not work around a failure by splitting one long line into two.
"""
from __future__ import annotations

import pathlib

MANIFEST = pathlib.Path(__file__).resolve().parent.parent / "MANIFEST.md"

# A one-line-per-file index of this repo fits comfortably under these. The bloat
# being guarded against was 218 lines / ~15,100 words.
MAX_LINES = 130
MAX_WORDS = 3000
# No single entry may be a paragraph. A genuine index line is ~10-30 words; 60 is
# a generous ceiling that still forbids the 600-word entries that caused the drift.
MAX_WORDS_PER_LINE = 60


def _lines() -> list:
    return MANIFEST.read_text(encoding="utf-8").splitlines()


def test_manifest_exists():
    assert MANIFEST.exists(), "MANIFEST.md must exist at the repo root"


def test_manifest_line_budget():
    n = len(_lines())
    assert n <= MAX_LINES, (
        "MANIFEST.md has %d lines (budget %d). It is a one-line-per-file index, "
        "not a codebase replica -- group predictable mirrors (per-file tests, "
        "per-phase design/plan docs) and move depth to the design docs/docstrings."
        % (n, MAX_LINES)
    )


def test_manifest_word_budget():
    words = sum(len(ln.split()) for ln in _lines())
    assert words <= MAX_WORDS, (
        "MANIFEST.md has %d words (budget %d) -- detail has leaked in; it belongs "
        "in docs/plans/*-design.md (WHY), docstrings (HOW), and tests (contract)."
        % (words, MAX_WORDS)
    )


def test_no_manifest_line_is_a_paragraph():
    offenders = [
        (i + 1, len(ln.split()))
        for i, ln in enumerate(_lines())
        if len(ln.split()) > MAX_WORDS_PER_LINE
    ]
    assert not offenders, (
        "MANIFEST.md lines exceed %d words (line, words): %s -- an entry this long "
        "is a paragraph, not an index line. Compress to a one-liner and move the "
        "detail to the design doc / docstring." % (MAX_WORDS_PER_LINE, offenders)
    )

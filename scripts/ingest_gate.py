"""PURE text-layer quality gate for the Phase 6 ``ingest`` skill.

Decides native-vs-re-OCR for a Docling ``do_ocr=False`` pass BEFORE any OCR
runs, and flags degraded / handwriting pages for humans -- using only the
extracted text plus a few numeric signals. Mirrors the suite's pure-core / edge
split: this module imports ONLY stdlib (``re``, ``enum``, ``pathlib``,
``dataclasses``) -- no docling / PDF / pandas / numpy / spaCy -- so the whole
gate is golden-testable with no model.

Signals are LEADS, not verdicts. Every threshold is a NAMED MODULE CONSTANT
(never a magic number in a branch) and the wordlist is INJECTABLE (mirrors
``pii_sweep``'s injectable classifier): the gate functions take
``wordlist: frozenset[str] | None``; ``None`` lazily loads + caches the bundled
default, while tests inject a tiny synthetic set so the pure suite never reads
the big file. Pure / deterministic: no clock / random / network, and the only IO
is the single cached read of the bundled wordlist.
"""
from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

# --------------------------------------------------------------------------- #
# Enums -- (str, Enum) so they are JSON-able (``.value`` is the wire string).
# --------------------------------------------------------------------------- #


class PageDiagnosis(str, Enum):
    """Per-page text-layer diagnosis (a LEAD about one page)."""

    native_ok = "native_ok"          # trustworthy extracted text
    image_only = "image_only"        # (near-)empty text layer -> needs OCR
    garbled_text = "garbled_text"    # present but mojibake/garbage -> needs OCR
    uncertain_review = "uncertain_review"  # too little signal / contradicted -> flag


class DocDecision(str, Enum):
    """Conservative doc-wide rollup decision."""

    native = "native"                          # trust the text layer doc-wide
    ocr_images = "ocr_images"                  # OCR the image-only pages
    force_full_doc_ocr = "force_full_doc_ocr"  # re-OCR the whole doc
    review = "review"                          # not safely auto-extractable


# --------------------------------------------------------------------------- #
# Named thresholds. Tuned TOGETHER with the Task-1/2/3 golden fixtures so every
# test passes deterministically; never inline a bare number in a branch below.
# --------------------------------------------------------------------------- #

# diagnose_page floors
_MIN_CHARS = 12          # below this stripped length -> image_only (empty layer)
_MIN_TOKENS = 4          # fewer alpha tokens than this -> not enough signal
_GARBLED_HIT_RATE = 0.20  # wordlist hit-rate below this is "low" (garbled co-signal)
_MIN_PARSE_SCORE = 0.05  # Docling parse_score at/under this contradicts good text

# char_density_ok floors. A numeric TABLE page legitimately has a low letter
# ratio (mostly digits), so the letter floor is only a "has essentially no
# letters at all" guard (catches a wall of pure symbols / digits); a long
# identical-symbol / digit run is caught by the run-length guard. The two guards
# are independent.
_MIN_LETTER_RATIO = 0.05  # letters / non-space chars must be at least this
_MAX_NONLETTER_RUN = 8    # longest run of consecutive non-letter, non-space chars

# decide_doc fractions (conservative; ~0.5 majority)
_REVIEW_FRACTION = 0.5    # uncertain share at/above this -> review
_ESCALATE_FRACTION = 0.5  # bad-page share at/above this -> escalate to OCR


# --------------------------------------------------------------------------- #
# Pure signal helpers
# --------------------------------------------------------------------------- #

_ALPHA_RUN = re.compile(r"[A-Za-z]+")
_NONLETTER_NONSPACE_RUN = re.compile(r"[^A-Za-z\s]+")

# ASCII-letter membership set for the density ratio (cheap, explicit).
_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def alphabetic_tokens(text: str) -> list[str]:
    """Lowercased maximal runs of ASCII letters.

    ``re.findall(r"[A-Za-z]+")`` drops digits and punctuation and splits on
    hyphens / spaces for free, so ``"Dept-99"`` yields ``["dept"]`` and a
    pure-digit token contributes nothing.
    """
    return [m.lower() for m in _ALPHA_RUN.findall(text)]


def wordlist_hit_rate(text: str, wordlist: frozenset[str]) -> float | None:
    """Fraction of alpha tokens present in ``wordlist``.

    Returns ``None`` (never a fake ``0.0``) when there are no alpha tokens to
    judge -- the caller must treat "no signal" distinctly from "zero hits".
    """
    toks = alphabetic_tokens(text)
    if not toks:
        return None
    return sum(t in wordlist for t in toks) / len(toks)


def char_density_ok(text: str) -> bool:
    """True iff ``text`` reads like real prose/data rather than symbol garbage.

    Two independent guards, both NAMED-constant driven (each catches a distinct
    failure mode a real page never trips):
      * letters / non-space chars is at least ``_MIN_LETTER_RATIO`` -- a "has
        essentially some letters" floor that rejects a wall of pure symbols or
        digits (a numeric TABLE page legitimately has a LOW letter ratio, so this
        floor is deliberately small, not a majority threshold); AND
      * no run of consecutive non-letter, non-space chars exceeds
        ``_MAX_NONLETTER_RUN`` -- a long ``;;;;;;`` / ``########`` / 16-digit run
        is an anomaly even amid otherwise-plain text.

    A garbled OCR / encoding layer trips one of these (a low letter ratio or a
    long symbol/digit run) together with a low wordlist-hit-rate co-signal in
    ``diagnose_page``. We deliberately do NOT special-case exotic Unicode glyphs:
    heavy mojibake already collapses the letter-ratio floor, and an over-broad
    "scary glyph" heuristic only added fixture complexity for no real gain.

    Empty / whitespace-only text has no content, so it is not "ok" here;
    ``diagnose_page`` screens emptiness earlier via ``_MIN_CHARS``.
    """
    non_space = [c for c in text if not c.isspace()]
    if not non_space:
        return False
    letters = sum(1 for c in non_space if c in _LETTERS)
    if letters / len(non_space) < _MIN_LETTER_RATIO:
        return False
    longest_run = max((len(m) for m in _NONLETTER_NONSPACE_RUN.findall(text)), default=0)
    if longest_run > _MAX_NONLETTER_RUN:
        return False
    return True


# --------------------------------------------------------------------------- #
# Bundled default wordlist -- the one (cached) IO touch-point.
# --------------------------------------------------------------------------- #

_DEFAULT_WORDLIST_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills" / "ingest" / "references" / "common_words.txt"
)

# Module-level cache: the bundled file is read at most once per process (mirrors
# pii_sweep's lazy model cache). Tests inject SMALL_WL and never trigger this.
_DEFAULT_WORDLIST_CACHE: frozenset[str] | None = None


def load_default_wordlist() -> frozenset[str]:
    """Load (once, cached) the bundled common-word sanity-check list.

    Lowercased, one word per line; blank lines ignored. A WEAK signal -- exact
    membership is not load-bearing -- so a solid public-domain-style common-word
    list is sufficient.
    """
    global _DEFAULT_WORDLIST_CACHE
    if _DEFAULT_WORDLIST_CACHE is None:
        words = {
            line.strip().lower()
            for line in _DEFAULT_WORDLIST_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        _DEFAULT_WORDLIST_CACHE = frozenset(words)
    return _DEFAULT_WORDLIST_CACHE


def _resolve_wordlist(wordlist: frozenset[str] | None) -> frozenset[str]:
    """Injectable-wordlist resolver: an explicit set wins; ``None`` lazily loads
    the bundled default. Centralizes the "None -> default" rule so every gate
    function shares it (and tests can inject without touching the file)."""
    if wordlist is not None:
        return wordlist
    return load_default_wordlist()

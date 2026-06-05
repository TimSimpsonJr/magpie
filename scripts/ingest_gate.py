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


# --------------------------------------------------------------------------- #
# Per-page diagnosis
# --------------------------------------------------------------------------- #


def diagnose_page(
    native_text: str,
    *,
    parse_score: float | None = None,
    lang: str = "en",
    wordlist: frozenset[str] | None = None,
) -> PageDiagnosis:
    """Diagnose ONE page's ``do_ocr=False`` native text layer (a LEAD, not a
    verdict). Pure: only the extracted text + an optional Docling ``parse_score``
    feed the decision, against the injected (or bundled-default) ``wordlist``.

    Ordered, first-match rules -- each branch is driven by a NAMED constant and
    deliberately conservative (false positives that needlessly OCR or flag a good
    page are worse than a missed garble, which the doc-wide rollup can still
    catch):

    1. ``len(stripped) < _MIN_CHARS`` -> ``image_only`` -- an essentially empty
       text layer is a scanned image that needs OCR.
    2. fewer than ``_MIN_TOKENS`` alpha tokens -> ``uncertain_review`` -- too
       little signal to judge a hit-rate (a Bates / letterhead-only page); flag
       it rather than trust OR garble it.
    3. ``garbled_text`` requires CO-OCCURRENCE: a low wordlist hit-rate
       (``hit is not None and hit < _GARBLED_HIT_RATE``) AND a density anomaly
       (``not char_density_ok``). A low hit-rate ALONE never garbles, so numeric
       tables / all-caps headers / multi-column run-ons stay ``native_ok``.
    4. contradictory-signal / handwriting hook: an otherwise-acceptable page
       whose Docling ``parse_score`` is at/under ``_MIN_PARSE_SCORE`` (Docling
       itself flags an unreliable parse) -> ``uncertain_review``.
    5. else -> ``native_ok``.
    """
    resolved_wl = _resolve_wordlist(wordlist)

    stripped = native_text.strip()
    toks = alphabetic_tokens(native_text)

    # (1) Essentially-empty text layer -> a scanned image that needs OCR. The
    # design's guard is "native_char_count ~= 0": a SHORT but present text token
    # (e.g. a sparse "SVPD-000123" form/Bates page) is NOT image_only -- it has
    # alphabetic content and must fall through to the token-floor branch below
    # (-> uncertain_review). So the _MIN_CHARS floor only fires when there is no
    # alphabetic content to judge (a truly blank / image-only layer).
    if len(stripped) < _MIN_CHARS and not toks:
        return PageDiagnosis.image_only

    # (2) Some text, but below the alpha-token floor -> not enough signal to
    # judge a hit-rate (a sparse form / Bates / letterhead-only page). Flag it.
    if len(toks) < _MIN_TOKENS:
        return PageDiagnosis.uncertain_review

    hit = wordlist_hit_rate(native_text, resolved_wl)
    density = char_density_ok(native_text)
    if hit is not None and hit < _GARBLED_HIT_RATE and not density:
        return PageDiagnosis.garbled_text
    if parse_score is not None and parse_score <= _MIN_PARSE_SCORE:
        return PageDiagnosis.uncertain_review
    return PageDiagnosis.native_ok


# --------------------------------------------------------------------------- #
# Conservative doc-wide rollup
# --------------------------------------------------------------------------- #


def decide_doc(diagnoses: list[PageDiagnosis]) -> DocDecision:
    """Roll a list of per-page diagnoses up into ONE conservative doc action.

    Conservative by construction: a doc-wide re-OCR is expensive and a minority
    of bad pages is flagged (not re-OCR'd) rather than flipping a 200-page native
    brief to full OCR. Ordered, FIRST-MATCH rule over each diagnosis' share of the
    page count (every fraction is a NAMED module constant):

    - empty list -> ``review`` (nothing to trust).
    1. ``uncertain`` share >= ``_REVIEW_FRACTION`` -> ``review`` (too much of the
       doc is un-judgeable for unattended extraction).
    2. ``garbled`` share >= ``_ESCALATE_FRACTION`` OR (``garbled`` present AND the
       combined ``image_only + garbled`` share >= ``_ESCALATE_FRACTION``) ->
       ``force_full_doc_ocr``. A present-but-bad text layer must be overridden
       doc-wide; this is also the safe superset when image+garbled are jointly
       high but neither alone dominates.
    3. ``image_only`` share >= ``_ESCALATE_FRACTION`` (no significant garbled) ->
       ``ocr_images`` (OCR the blank-layer pages only).
    4. otherwise (mostly native, minority bad) -> ``native`` -- the minority bad
       pages are flagged later by ``ingest.py``, NOT re-OCR'd doc-wide.
    """
    n = len(diagnoses)
    if n == 0:
        return DocDecision.review

    uncertain = sum(d is PageDiagnosis.uncertain_review for d in diagnoses)
    garbled = sum(d is PageDiagnosis.garbled_text for d in diagnoses)
    image_only = sum(d is PageDiagnosis.image_only for d in diagnoses)

    if uncertain / n >= _REVIEW_FRACTION:
        return DocDecision.review
    if garbled / n >= _ESCALATE_FRACTION or (
        garbled > 0 and (image_only + garbled) / n >= _ESCALATE_FRACTION
    ):
        return DocDecision.force_full_doc_ocr
    if image_only / n >= _ESCALATE_FRACTION:
        return DocDecision.ocr_images
    return DocDecision.native

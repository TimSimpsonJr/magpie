"""Redact UNINVOLVED third-party PII for PUBLISHED artifacts, while the FULL
un-redacted exhibit is kept on a LOCAL non-vault path (Phase 7, output side).

Policy (Tim's call -- design 2.1): the line is INVOLVED vs UNINVOLVED, not
official vs non-official. A flagged PERSON name that is NEITHER an official
(rank/title prefix OR officials-lexicon subset) NOR an investigator-designated
involved subject (a ``keep_names`` allowlist) is an uninvolved third party and is
redacted to INITIALS. Officials and involved subjects stay NAMED. Structured PII
(ssn, dob, phone, email, alien#, driver-lic, ...) is ALWAYS masked to a TYPED
placeholder, regardless of the name policy. With no ``keep_names`` supplied every
non-official flagged name is treated as uninvolved (the safe default).

PUBLISH-PATH CONTRACT (design 2.0): only ``redact_text`` and ``redact_note``
produce PUBLISHED strings, and NEITHER carries a ``text_id`` or a raw matched
text. ``redact_local_texts``'s text_id->redacted map and ``write_local_exhibit``'s
CSV are the ONLY surfaces that carry text_id / raw text, and both are LOCAL-only
(the exhibit is additionally vault-guarded). A ``text_id`` or a raw matched text
NEVER crosses a published path.

Pure-core / spaCy-at-the-edge (mirrors pii_sweep / ingest): this module imports
ONLY the PURE helpers from pii_sweep (``person_role_in_span``, ``_norm_name_tokens``,
``OFFICIAL_TITLES``, ``DEFAULT_PII_PATTERNS``, ``text_id``) -- importing pii_sweep
is ML-free. spaCy loads only when the default ``SpacyPersonSpans`` actually runs
(a lazy edge), so ``import scripts.redact_output`` never pulls spaCy.

ASCII-only (SDD content-filter rule).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from scripts.pii_sweep import (
    DEFAULT_PII_PATTERNS,
    OFFICIAL_TITLES,
    _norm_name_tokens,
    person_role_in_span,
    text_id,
)

# A person-span provider maps a text -> the PERSON ent spans in it, each as
# (span_text, start_char, end_char, preceding_token_texts). The default is the
# lazy spaCy edge (SpacyPersonSpans); tests inject a fake.
PersonSpan = "tuple[str, int, int, Sequence[str]]"
PersonSpans = Callable[[str], "list[PersonSpan]"]


# Typed placeholders per structured-PII category (design 2.3): typed, not a
# blanket [REDACTED], so the published artifact still conveys WHAT kind of PII
# was present. Keys are pii_sweep's DEFAULT_PII_PATTERNS category names; the two
# broad-only categories (race_sex, possible_birthdate) get sensible types too.
_PII_PLACEHOLDERS: dict[str, str] = {
    "phone": "[PHONE]",
    "ssn": "[SSN]",
    "email": "[EMAIL]",
    "dob_kw": "[DOB]",
    "alien_num": "[A-NUMBER]",
    "driver_lic": "[DL]",
    "race_sex": "[DEMOGRAPHIC]",
    "possible_birthdate": "[DATE]",
}


def _placeholder_for(category: str) -> str:
    """Typed placeholder for a structured-PII category; a sensible upper-cased
    fallback for any non-default pattern name a caller might add."""
    return _PII_PLACEHOLDERS.get(category, "[" + category.upper() + "]")


def initials(name: str) -> str:
    """A redacted PERSON span -> initials: the first ALPHABETIC char of each
    whitespace-split token, upper-cased, each followed by a dot and concatenated
    ("John Q Public" -> "J.Q.P."; "Madonna" -> "M."). A token with NO alphabetic
    char (e.g. a date "01/02/1990") contributes NOTHING. Deterministic."""
    out: list[str] = []
    for token in name.split():
        for ch in token:
            if ch.isalpha():
                out.append(ch.upper() + ".")
                break
    return "".join(out)


def _normalize_lexicon(names: Sequence[str]) -> frozenset:
    """Names -> the officials/involved lexicon shape pii_sweep uses: a frozenset
    of normalized token-frozensets (empty token-sets dropped). Compared by
    subset in ``person_role_in_span``."""
    return frozenset(
        toks for toks in (_norm_name_tokens(n) for n in names) if toks
    )


def _person_spans(
    text: str,
    person_spans: PersonSpans,
    *,
    officials: frozenset,
    involved: frozenset,
) -> "list[tuple[int, int, str]]":
    """Replacement spans for PERSON ents whose role is 'uninvolved': each becomes
    ``(start, end, initials(span_text))``. Officials and involved subjects yield
    NO span (kept verbatim). PERSON spans are tagged kind=0 for tie-breaking."""
    spans: list[tuple[int, int, str]] = []
    for span_text, start, end, preceding in person_spans(text):
        role = person_role_in_span(
            span_text, preceding, officials=officials, involved=involved
        )
        if role == "uninvolved":
            spans.append((start, end, initials(span_text)))
    return spans


def _pii_spans(
    text: str, patterns: Mapping[str, re.Pattern[str]]
) -> "list[tuple[int, int, str]]":
    """Replacement spans for every structured-PII regex match: each becomes
    ``(start, end, typed_placeholder)``."""
    spans: list[tuple[int, int, str]] = []
    for category, pattern in patterns.items():
        placeholder = _placeholder_for(category)
        for m in pattern.finditer(text):
            spans.append((m.start(), m.end(), placeholder))
    return spans


def _apply_spans(text: str, spans: "Sequence[tuple[int, int, str]]") -> str:
    """Apply ``(start, end, replacement)`` spans to ``text`` with overlap
    resolution + right-to-left application (design 2.3).

    Overlap rule: when two spans overlap, the LONGER span wins and the contained
    (or merely overlapping shorter) span is DROPPED -- a PII match inside a PERSON
    span (or vice versa) is covered by the outer replacement, never
    double-redacted. PERSON-before-PII on equal-length ties (the caller passes
    PERSON spans first, so a stable sort preserves that order for ties).

    Application is RIGHT-TO-LEFT (descending start offset) so an earlier
    replacement never shifts a later span's offsets. BOTH ``redact_text`` and
    ``redact_note`` use this ONE helper -- one code path, one set of overlap tests.
    """
    # Keep insertion order for equal-length ties (PERSON-before-PII): a stable
    # sort by DESCENDING length keeps the longer span first; among equal lengths
    # the original order (PERSON spans were appended before PII spans) survives.
    ordered = sorted(spans, key=lambda s: (s[1] - s[0]), reverse=True)

    chosen: list[tuple[int, int, str]] = []
    for start, end, replacement in ordered:
        # Drop this span if it overlaps an already-chosen (longer-or-equal) span.
        if any(start < c_end and c_start < end for c_start, c_end, _ in chosen):
            continue
        chosen.append((start, end, replacement))

    # Apply right-to-left so offsets never shift.
    result = text
    for start, end, replacement in sorted(chosen, key=lambda s: s[0], reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


def redact_text(
    text: str,
    *,
    keep_names: Sequence[str] = (),
    officials: Sequence[str] = (),
    person_spans: PersonSpans | None = None,
    patterns: Mapping[str, re.Pattern[str]] | None = None,
) -> str:
    """Redact ONE flagged text for publication: uninvolved PERSON names ->
    initials, structured PII -> typed placeholders (design 2.0 #1).

    SCOPE (locked): applied to ``pii_sweep``-flagged reason-field texts, NOT run
    as an autonomous scanner over arbitrary analyst narrative.

    Returns a redacted string carrying NO ``text_id``. Pure-testable via an
    injected ``person_spans``; when None a lazy spaCy-backed ``SpacyPersonSpans``
    is built (the only spaCy touch-point).

    Policy: ``keep_names`` are the investigator-designated INVOLVED subjects to
    keep; they ALSO count as officials for the role decision (so a keep-named
    person is never redacted). ``officials`` is an additional officials lexicon.
    A PERSON span whose role is 'uninvolved' is initialed; 'official'/'involved'
    spans are kept. Structured PII is always masked.
    """
    if person_spans is None:
        person_spans = SpacyPersonSpans()
    patterns = DEFAULT_PII_PATTERNS if patterns is None else patterns

    # officials lexicon = officials + keep_names (a keep-named person is treated
    # as official for the role decision so it is never redacted); involved
    # lexicon = keep_names (design 2.1).
    officials_lex = _normalize_lexicon(tuple(keep_names) + tuple(officials))
    involved_lex = _normalize_lexicon(keep_names)

    spans = _person_spans(
        text, person_spans, officials=officials_lex, involved=involved_lex
    )
    spans.extend(_pii_spans(text, patterns))
    return _apply_spans(text, spans)


class SpacyPersonSpans:
    """Default ``person_spans`` provider: lazy spaCy PERSON-NER over ONE text,
    yielding ``(ent.text, ent.start_char, ent.end_char, preceding_token_texts)``
    for each PERSON ent. The up-to-2 preceding tokens feed the title-prefix check
    in ``person_role_in_span``.

    Unlike ``pii_sweep.SpacyPersonClassifier`` this applies NO ``len<=2`` skip:
    for redaction a missed name is a LEAK, so EVERY PERSON ent is considered and
    redacted if it is not official/involved (per-consumer short-name policy,
    design 2.2). Loads ``en_core_web_lg`` lazily on first call -- importing this
    module never pulls spaCy.
    """

    def __init__(self, *, model: str = "en_core_web_lg") -> None:
        self._model = model
        self._nlp = None

    def _load(self):
        if self._nlp is None:
            import spacy

            self._nlp = spacy.load(
                self._model,
                disable=["tagger", "parser", "lemmatizer", "attribute_ruler"],
            )
        return self._nlp

    def __call__(self, text: str) -> "list[PersonSpan]":
        nlp = self._load()
        doc = nlp(text)
        out: list[PersonSpan] = []
        for ent in doc.ents:
            if ent.label_ != "PERSON":
                continue
            preceding = [doc[j].text for j in range(max(0, ent.start - 2), ent.start)]
            out.append((ent.text, ent.start_char, ent.end_char, preceding))
        return out

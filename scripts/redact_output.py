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
    BROAD_ONLY_PATTERN_NAMES,
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


# Typed placeholders for the SIX high-precision structured-PII categories
# (design 2.3): typed, not a blanket [REDACTED], so the published artifact still
# conveys WHAT kind of PII was present. Keys are pii_sweep's DEFAULT_PII_PATTERNS
# category names. The two broad-only leads (race_sex, possible_birthdate) are
# deliberately ABSENT: redact_text does not mask them (see _pii_spans), so they
# need no placeholder.
_PII_PLACEHOLDERS: dict[str, str] = {
    "phone": "[PHONE]",
    "ssn": "[SSN]",
    "email": "[EMAIL]",
    "dob_kw": "[DOB]",
    "alien_num": "[A-NUMBER]",
    "driver_lic": "[DL]",
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
    """Replacement spans for each HIGH-PRECISION structured-PII match: each
    becomes ``(start, end, typed_placeholder)``.

    Masks ONLY the six high-precision categories (ssn, dob_kw, phone, email,
    alien_num, driver_lic). The two BROAD-ONLY leads in
    ``BROAD_ONLY_PATTERN_NAMES`` (race_sex, possible_birthdate) are SKIPPED: a
    bare possible_birthdate also matches an ordinary INCIDENT date, so masking it
    would over-redact. The exclusion follows pii_sweep's canonical broad-only
    set so the two modules stay in lock-step."""
    spans: list[tuple[int, int, str]] = []
    for category, pattern in patterns.items():
        if category in BROAD_ONLY_PATTERN_NAMES:
            continue
        placeholder = _placeholder_for(category)
        for m in pattern.finditer(text):
            spans.append((m.start(), m.end(), placeholder))
    return spans


def _apply_spans(text: str, spans: "Sequence[tuple[int, int, str]]") -> str:
    """Apply ``(start, end, replacement)`` spans to ``text`` by MERGING every
    overlapping cluster into a single UNION span + applying RIGHT-TO-LEFT
    (design 2.3).

    Overlap rule (merge-to-union): overlapping spans are coalesced into ONE span
    spanning the UNION of their character ranges, and the LONGEST contributing
    span's replacement covers the whole cluster. This is leak-safe for BOTH a
    contained overlap (a PII match inside a PERSON span -- the outer replacement
    covers it) AND a PARTIAL overlap (a PERSON span half-overlapping an SSN/phone
    -- the union still covers every sensitive char, so NO raw prefix/suffix of the
    shorter span can survive). Over-redacting the union of two overlapping
    sensitive spans is the SAFE choice: it is never a leak. PERSON-before-PII on
    equal-length ties (the caller appends PERSON spans before PII spans, and a
    stable sort preserves that order so PERSON wins an equal-length tie).

    Application is RIGHT-TO-LEFT (descending start offset) so an earlier
    replacement never shifts a later cluster's offsets. BOTH ``redact_text`` and
    ``redact_note`` use this ONE helper -- one code path, one set of overlap tests.
    """
    if not spans:
        return text

    # Sort by start, then LONGEST-first so the first span in a start-tie is the
    # longest -> its replacement wins the cluster; PERSON spans were appended
    # before PII spans, so equal-length ties keep PERSON via the stable sort.
    ordered = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0])))

    # Each cluster: [start, end, replacement, winning_len].
    clusters: list[list] = []
    for start, end, replacement in ordered:
        if clusters and start < clusters[-1][1]:  # overlaps the current cluster
            cluster = clusters[-1]
            cluster[1] = max(cluster[1], end)  # extend to the UNION
            if (end - start) > cluster[3]:  # a strictly longer span wins the repl
                cluster[2], cluster[3] = replacement, end - start
        else:
            clusters.append([start, end, replacement, end - start])

    # Apply right-to-left so offsets never shift.
    result = text
    for start, end, replacement, _ in sorted(
        clusters, key=lambda c: c[0], reverse=True
    ):
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


def redact_local_texts(
    local_texts: Mapping[str, Mapping[str, Any]],
    *,
    keep_names: Sequence[str] = (),
    officials: Sequence[str] = (),
    person_spans: PersonSpans | None = None,
    patterns: Mapping[str, re.Pattern[str]] | None = None,
) -> dict[str, str]:
    """LOCAL-ONLY map: each ``pii_sweep`` ``text_id`` -> ``redact_text(entry["text"])``
    (design 2.0 #2). Consumed ONLY by the exhibit; it is NEVER returned to or used
    as a published surface (the ``text_id`` key is sha256(raw)[:16], treated as
    local). ``local_texts`` is the ``{text_id: {text, count, categories}}`` shape
    pii_sweep emits with ``collect_local_texts=True``."""
    return {
        tid: redact_text(
            entry["text"],
            keep_names=keep_names,
            officials=officials,
            person_spans=person_spans,
            patterns=patterns,
        )
        for tid, entry in local_texts.items()
    }


def redact_note(
    note_text: str,
    local_texts: Mapping[str, Mapping[str, Any]],
    *,
    keep_names: Sequence[str] = (),
    officials: Sequence[str] = (),
    person_spans: PersonSpans | None = None,
    patterns: Mapping[str, re.Pattern[str]] | None = None,
) -> str:
    """PUBLISH-safe note sanitizer (design 2.0 #3): replace every occurrence of
    every KNOWN flagged ``text`` (from ``local_texts``) in ``note_text`` with its
    ``redact_text`` form, leaving the analyst's surrounding narrative untouched.

    Emits NO ``text_id`` and no un-redacted flagged text -- the only redact-output
    function that produces a PUBLISHED multi-sentence string. By construction it
    only touches spans equal to a known flagged text, so contextual names the
    analyst wrote into the narrative are not NER-scanned.

    Known texts are sorted LONGEST-FIRST so a longer flagged text is not
    pre-empted by a shorter one that is its prefix; overlap resolution +
    right-to-left application is delegated to the SHARED ``_apply_spans`` (longest
    match wins, contained dropped) -- NOT a hand-rolled ``str.replace`` loop
    (shorter-first replace was the bug to avoid). This is the no-raw-suffix
    guarantee."""
    if person_spans is None:
        person_spans = SpacyPersonSpans()

    # Distinct flagged texts, longest-first (so a longer match is collected before
    # any shorter prefix; _apply_spans then drops the contained shorter span).
    flagged = sorted(
        {entry["text"] for entry in local_texts.values()},
        key=len,
        reverse=True,
    )

    spans: list[tuple[int, int, str]] = []
    for flagged_text in flagged:
        if not flagged_text:
            continue
        replacement = redact_text(
            flagged_text,
            keep_names=keep_names,
            officials=officials,
            person_spans=person_spans,
            patterns=patterns,
        )
        start = note_text.find(flagged_text)
        while start != -1:
            spans.append((start, start + len(flagged_text), replacement))
            start = note_text.find(flagged_text, start + 1)

    return _apply_spans(note_text, spans)


_EXHIBIT_FIELDS = ("text_id", "text", "count", "categories")


def write_local_exhibit(
    local_texts: Mapping[str, Mapping[str, Any]],
    exhibit_dir,
    *,
    vault_roots: Sequence[Any] = (),
    redacted: bool = False,
    keep_names: Sequence[str] = (),
    officials: Sequence[str] = (),
    person_spans: PersonSpans | None = None,
    patterns: Mapping[str, re.Pattern[str]] | None = None,
) -> Path:
    """Write the exhibit CSV (FULL un-redacted, or the redacted view if
    ``redacted=True``) under ``exhibit_dir`` and return its path (design 2.0 #4).
    This LOCAL CSV is the ONLY surface that carries ``text_id`` + raw text.

    VAULT GUARD (design 2.4, fail-closed): both ``exhibit_dir`` and each
    ``vault_roots`` entry are resolved via ``Path.resolve()`` (collapsing symlinks
    and ``..``); RAISE ValueError if the resolved exhibit path is AT or UNDER any
    resolved vault root -- the full exhibit must never land where it could be
    published/synced. The directory is created (after the guard) if absent."""
    exhibit_dir = Path(exhibit_dir)
    resolved_dir = exhibit_dir.resolve()
    for root in vault_roots:
        resolved_root = Path(root).resolve()
        if resolved_dir == resolved_root or resolved_root in resolved_dir.parents:
            raise ValueError(
                "exhibit_dir resolves at/under a vault root "
                f"({resolved_dir} <= {resolved_root}); the un-redacted exhibit "
                "must be written outside every vault root"
            )

    resolved_dir.mkdir(parents=True, exist_ok=True)
    out_path = resolved_dir / ("exhibit_redacted.csv" if redacted else "exhibit.csv")

    redacted_map: dict[str, str] = {}
    if redacted:
        redacted_map = redact_local_texts(
            local_texts,
            keep_names=keep_names,
            officials=officials,
            person_spans=person_spans,
            patterns=patterns,
        )

    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_EXHIBIT_FIELDS)
        writer.writeheader()
        for tid, entry in local_texts.items():
            text = redacted_map[tid] if redacted else entry["text"]
            categories = entry.get("categories", [])
            writer.writerow(
                {
                    "text_id": tid,
                    "text": text,
                    "count": entry.get("count", ""),
                    "categories": ";".join(categories),
                }
            )
    return out_path


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

"""Authoritative spaCy-NER + structured-regex PII-EXPOSURE tally over a FOIA
free-text column. NER runs over DISTINCT values then weights by row counts (the
pilot's ~7x efficiency lesson). Officials named for accountability are split from
PII that should have been sanitized. See the Phase 5 design doc for rationale.

Pure core, spaCy only at the edge: the distinct/weight/regex/tally logic imports
no heavy ML; the lazy SpacyPersonClassifier is the only spaCy touch-point, so the
tally is golden-testable with a fake classifier. Decoupled from recipe.check_pii.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import pandas as pd


def _is_blank(value: Any) -> bool:
    """True for None / NaN / empty-or-whitespace-only string."""
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(value, str) and value.strip() == ""


def distinct_texts(series: pd.Series) -> tuple[list[str], list[int]]:
    """Distinct non-blank texts + the row count each covers.

    Outer whitespace is STRIPPED before counting (so "John " and "John"
    collapse); case is PRESERVED (spaCy NER is case-sensitive). Null / blank /
    whitespace-only rows are dropped.
    """
    stripped = series.map(lambda v: v.strip() if isinstance(v, str) else v)
    nonblank = stripped[~stripped.map(_is_blank)]
    # value_counts() tie-order (texts with equal counts) is NOT relied upon
    # downstream: every result is keyed by category name or by text_id, so the
    # iteration order of equal-count texts cannot affect any output.
    vc = nonblank.value_counts()
    return [str(t) for t in vc.index], [int(c) for c in vc.values]


def text_id(text: str) -> str:
    """Stable LOCAL join key for a distinct text: truncated sha256 hex of the
    STRIPPED text, so it matches distinct_texts (``text_id("John ") ==
    text_id("John")``). Case-preserved. redact-output joins on this id LOCALLY;
    it never crosses a published path (published notes carry the aggregate tally
    only -- design doc Section 7).
    """
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


DEFAULT_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "phone": re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "dob_kw": re.compile(r"\bD\.?O\.?B\.?\b", re.IGNORECASE),
    "alien_num": re.compile(r"\bA\d{8,9}\b"),           # tightened from prototype 8,12
    "driver_lic": re.compile(r"\b(?:OLN|DLN|DL|OL)\s?#?\s?(?=[A-Z0-9]*\d)[A-Z0-9]{6,}\b"),
    "race_sex": re.compile(r"\b[BWHAI]\s?/\s?[MF]\b"),
    "possible_birthdate": re.compile(
        r"\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19|20)\d\d\b"
    ),
}

# Two DEFAULT broad-only patterns, both medium-precision LEADS rather than the
# publishable headline:
#   - possible_birthdate: a bare MM/DD/YYYY also matches an INCIDENT date.
#   - race_sex: the 2-char demographic ratio is ambiguous with ordinary prose
#     ("H/M ratio", "W/M reading"), so it is a lead, not the headline.
# Each still FIRES as a category; only its TIER is broad. Every other DEFAULT
# pattern is high-precision PII (counts toward STRICT). This is only the DEFAULT
# broad-only set; `sweep(broad_only_names=...)` overrides it -- e.g. the Phase 11
# compatibility profile passes `frozenset()` to fold possible_birthdate INTO the
# headline and reproduce the pilot's documented figure. Naming the headline
# honestly: "high-precision PII", NOT "structured identifiers" -- dob_kw is a
# sensitive descriptor, not a literal ID.
BROAD_ONLY_PATTERN_NAMES: frozenset[str] = frozenset({"possible_birthdate", "race_sex"})


def _regex_hit(pattern: re.Pattern[str], text: str) -> bool:
    """True iff ``pattern`` matches ``text`` (``text`` is always a real str here)."""
    return pattern.search(text) is not None


@dataclass(frozen=True)
class PersonFlags:
    """Per-text PERSON classification (a text may carry both)."""
    official: bool = False
    unknown_role: bool = False


# A classifier maps texts -> one PersonFlags per text (order-preserving).
PersonClassifier = Callable[[Sequence[str]], "list[PersonFlags]"]


def _tally(counts: Sequence[int], bools: Sequence[bool]) -> dict[str, int]:
    return {
        "weighted": int(sum(c for c, b in zip(counts, bools) if b)),
        "distinct": int(sum(1 for b in bools if b)),
    }


def sweep(
    series: pd.Series,
    *,
    person_classifier: PersonClassifier | None = None,
    patterns: Mapping[str, re.Pattern[str]] | None = None,
    broad_only_names: frozenset[str] | None = None,
    official_names: Sequence[str] | None = None,
    collect_local_texts: bool = False,
) -> dict[str, Any]:
    """PII-exposure tally over ``series`` (one free-text column).

    NER/classification runs over DISTINCT texts; every category is weighted by
    the row counts those distinct texts cover. If ``person_classifier`` is None a
    lazy :class:`SpacyPersonClassifier` is built (using ``official_names``). See
    the design doc for the output contract.
    """
    patterns = DEFAULT_PII_PATTERNS if patterns is None else patterns
    n_rows = len(series)
    texts, counts = distinct_texts(series)
    n_distinct = len(texts)
    n_nonblank = int(sum(counts))

    if person_classifier is None:
        person_classifier = SpacyPersonClassifier(
            official_names=frozenset(official_names or ())
        )
    flags = list(person_classifier(texts))
    if len(flags) != n_distinct:
        raise ValueError("person_classifier must return one PersonFlags per text")

    official = [f.official for f in flags]
    unknown = [f.unknown_role for f in flags]
    regex_hits = {name: [_regex_hit(p, t) for t in texts] for name, p in patterns.items()}

    categories: dict[str, dict[str, int]] = {
        name: _tally(counts, hits) for name, hits in regex_hits.items()
    }
    categories["person_official"] = _tally(counts, official)
    categories["person_unknown_role"] = _tally(counts, unknown)

    result: dict[str, Any] = {
        "n_rows": int(n_rows),
        "n_nonblank_rows": n_nonblank,
        "n_distinct_texts": int(n_distinct),
        "efficiency_ratio": (n_nonblank / n_distinct) if n_distinct else None,
        "categories": categories,
    }

    broad_only = (BROAD_ONLY_PATTERN_NAMES if broad_only_names is None
                  else frozenset(broad_only_names))
    strict_names = [n for n in patterns if n not in broad_only]
    broad_only_present = [n for n in patterns if n in broad_only]
    strict_bool, broad_bool = [], []
    for i in range(n_distinct):
        strict_i = any(regex_hits[n][i] for n in strict_names)
        broad_i = strict_i or unknown[i] or any(regex_hits[n][i] for n in broad_only_present)
        strict_bool.append(strict_i)
        broad_bool.append(broad_i)

    result["exposure"] = {
        "strict": _tally(counts, strict_bool),   # publishable headline (high-precision PII)
        "broad": _tally(counts, broad_bool),     # + name-leads + possible_birthdate
    }

    if collect_local_texts:
        local: dict[str, dict[str, Any]] = {}
        for i, t in enumerate(texts):
            if not broad_bool[i]:           # only redaction targets; officials-only excluded
                continue
            cats = [n for n in patterns if regex_hits[n][i]]
            if official[i]:
                cats.append("person_official")
            if unknown[i]:
                cats.append("person_unknown_role")
            tid = text_id(t)
            if tid in local:   # two DISTINCT texts collided on the truncated hash
                raise ValueError(f"text_id collision {tid!r}; widen the text_id truncation")
            local[tid] = {"text": t, "count": int(counts[i]), "categories": cats}
        result["local_texts"] = local
    return result


OFFICIAL_TITLES: frozenset[str] = frozenset({
    "officer", "ofc", "ofcr", "sgt", "sergeant", "deputy", "dep", "det",
    "detective", "lt", "lieutenant", "cpl", "corporal", "capt", "cpt", "captain",
    "chief", "sheriff", "trooper", "marshal", "agent", "investigator",
    "patrolman", "cmdr", "commander", "major", "col", "colonel",
})


def _norm_name_tokens(text: str) -> frozenset[str]:
    """Normalize a name into comparable tokens for the officials lexicon: lower,
    split on whitespace, strip SURROUNDING punctuation, keep tokens with >=1
    letter. Internal apostrophes/hyphens survive ("o'brien", "anne-marie"); a
    badge suffix like "#4471" drops. Used for BOTH the lexicon and the PERSON
    span so they compare on the same footing (and span over-extension is safe --
    extra span tokens never block a subset match)."""
    out: set[str] = set()
    for w in text.lower().split():
        w = w.strip(".,'\"-#/()")
        if any(ch.isalpha() for ch in w):
            out.add(w)
    return frozenset(out)


class SpacyPersonClassifier:
    """Production PersonClassifier: spaCy PERSON NER + official/unknown split.

    Lazily loads ``en_core_web_lg`` (NER-only) on first call. A PERSON span is
    ``official`` if (a) a title/rank token immediately precedes it (<=2 tokens),
    or (b) the span CONTAINS an ``official_names`` entry -- a normalized token-
    SUBSET match (the official's name tokens are a subset of the span's tokens,
    robust to span over-extension), the lexicon built by the caller from the
    structured searcher/user field; else ``unknown_role``.
    Classification uses token/context windows, NEVER exact span strings (spans
    over-extend). Heuristic by design -- a lead, not a verdict (see design doc).
    """

    def __init__(self, *, official_names: Sequence[str] = (),
                 titles: frozenset[str] = OFFICIAL_TITLES,
                 model: str = "en_core_web_lg") -> None:
        self._lexicon = frozenset(
            toks for toks in (_norm_name_tokens(n) for n in official_names) if toks
        )
        self._titles = frozenset(t.lower() for t in titles)
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

    def __call__(self, texts: Sequence[str]) -> list[PersonFlags]:
        nlp = self._load()
        out: list[PersonFlags] = []
        for doc in nlp.pipe(list(texts), batch_size=256):
            official = unknown = False
            for ent in doc.ents:
                # len>2 floor: drop 1-2 char PERSON false positives (initials).
                # DELIBERATE default (matches the pilot); misses very short
                # surnames like "Li"/"Ng" -- documented trade (user decision).
                if ent.label_ != "PERSON" or len(ent.text.strip()) <= 2:
                    continue
                if self._is_official(doc, ent):
                    official = True
                else:
                    unknown = True
            out.append(PersonFlags(official=official, unknown_role=unknown))
        return out

    def _is_official(self, doc, ent) -> bool:
        # (b) lexicon: some official's normalized name-token-set is contained in
        # the span (span over-extension is safe -- extra tokens don't block it).
        span_tokens = _norm_name_tokens(ent.text)
        if self._lexicon and any(name <= span_tokens for name in self._lexicon):
            return True
        # (a) title/rank token immediately preceding the span (<=2 tokens back)
        for j in range(max(0, ent.start - 2), ent.start):
            if doc[j].text.strip(".").lower() in self._titles:
                return True
        return False

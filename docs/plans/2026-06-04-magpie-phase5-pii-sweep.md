# pii-sweep (Phase 5) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build `scripts/pii_sweep.py` — the authoritative spaCy-NER + structured-regex PII-**exposure** tally over a FOIA free-text column, run over DISTINCT values and weighted by row counts, distinguishing officials (named for accountability) from PII that should have been sanitized — plus the `pii-sweep` SKILL.md wiring the output to `redact-output`.

**Architecture:** Pure core, spaCy at the edge (mirrors the `stats`/`derive` purity + `build_dataset_db` IO-at-edge idiom). An injectable `PersonClassifier` seam lets the pure tally/regex/weight logic be golden-tested with a fake (no 400 MB model); a lazy `SpacyPersonClassifier` is the production default. Decoupled from `recipe.check_pii` (no imports either way; a drift test guards the overlap). See `docs/plans/2026-06-04-magpie-phase5-pii-sweep-design.md` for the *why*; this plan is the *how*.

**Tech Stack:** Python 3.12.10, pandas 3.0.3, spaCy 3.8.14 + `en_core_web_lg` 3.8.0 (CPU, NER-only), pytest 9.0.3. stdlib `re` + `hashlib`.

---

## Conventions for the executor (read once)

- **Run tests with `mise run test`** (the whole suite) or a single file/test via
  `& .venv\Scripts\python.exe -m pytest tests/test_pii_sweep.py -v`.
  **NEVER bare `python`** — the Claude Code PowerShell tool is `-NoProfile`, so bare
  `python` hits the global interpreter with mismatched deps. Run git + pytest via
  the **PowerShell** tool (Bash mangles `\`-paths).
- **All fixtures are SYNTHETIC.** Never read or commit the private corpus.
- Commit messages end with the `Co-Authored-By` trailer (see existing history).
  PowerShell has no heredoc — use repeated `-m` paragraphs. CRLF warnings are benign.
- `pyproject.toml` sets `pythonpath = ["."]`, so `from scripts.pii_sweep import ...`
  works from the repo root (same as `test_recipe.py`).

---

## Task 1: `distinct_texts` + `text_id` (pure foundation)

**Files:**
- Create: `scripts/pii_sweep.py`
- Create: `tests/test_pii_sweep.py`

**Step 1: Write the failing tests**

```python
# tests/test_pii_sweep.py
import pandas as pd
from scripts.pii_sweep import distinct_texts, text_id


def test_distinct_texts_strips_outer_whitespace_and_drops_blanks():
    s = pd.Series(["John ", "John", "  ", "", None, "Mary", "Mary"])
    texts, counts = distinct_texts(s)
    by = dict(zip(texts, counts))
    assert by == {"John": 2, "Mary": 2}        # "John " collapses into "John"
    assert "" not in texts and "  " not in texts


def test_distinct_texts_preserves_case():
    texts, _ = distinct_texts(pd.Series(["ICE", "ice"]))
    assert set(texts) == {"ICE", "ice"}        # NER is case-sensitive; do NOT lowercase


def test_text_id_is_stable_truncated_and_strip_consistent():
    a, b = text_id("John Smith"), text_id("John Smith")
    assert a == b and len(a) == 16 and a != text_id("Jane Smith")
    assert text_id("John ") == text_id("John")   # strips like distinct_texts (join-safe)
```

**Step 2: Run to verify they fail**

Run: `& .venv\Scripts\python.exe -m pytest tests/test_pii_sweep.py -v`
Expected: FAIL (ImportError — module/functions not defined).

**Step 3: Minimal implementation**

```python
# scripts/pii_sweep.py
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
```

**Step 4: Run to verify pass**

Run: `& .venv\Scripts\python.exe -m pytest tests/test_pii_sweep.py -v`
Expected: PASS (3 tests).

**Step 5: Commit**

```
git add scripts/pii_sweep.py tests/test_pii_sweep.py
git commit -m "Phase 5.1: pii_sweep distinct_texts + text_id (TDD)" -m "Co-Authored-By: ..."
```

---

## Task 2: structured-PII patterns + `_regex_hit`

**Files:** Modify `scripts/pii_sweep.py`; Modify `tests/test_pii_sweep.py`.

**Step 1: Write the failing tests**

```python
from scripts.pii_sweep import DEFAULT_PII_PATTERNS, BROAD_ONLY_PATTERN_NAMES, _regex_hit

def test_each_pattern_matches_positive_and_rejects_negative():
    pos = {
        "phone": "call 864-555-1212", "ssn": "ssn 123-45-6789",
        "email": "x@y.org", "dob_kw": "see DOB below",
        "alien_num": "A123456789", "driver_lic": "OLN# AB1234567",
        "race_sex": "susp B/M", "possible_birthdate": "04/12/1989",
    }
    for name, text in pos.items():
        assert _regex_hit(DEFAULT_PII_PATTERNS[name], text), name
    assert not _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "PARA1234567")  # word-boundary
    assert not _regex_hit(DEFAULT_PII_PATTERNS["ssn"], "order 12345 6789")

def test_alien_num_is_8_or_9_digits_not_more():
    assert _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "A12345678")        # 8
    assert _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "A123456789")       # 9
    assert not _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "A1234567")     # 7
    assert not _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "A1234567890")  # 10

def test_possible_birthdate_is_the_only_broad_only_pattern():
    # a bare date is also an incident date -> broad-only; every OTHER default
    # pattern is high-precision PII (counts toward the strict headline).
    assert BROAD_ONLY_PATTERN_NAMES == {"possible_birthdate"}
    assert {"ssn", "phone", "email", "alien_num", "dob_kw", "race_sex",
            "driver_lic"}.isdisjoint(BROAD_ONLY_PATTERN_NAMES)
```

**Step 2: Run — FAIL** (names not defined).

**Step 3: Implementation** (append to `scripts/pii_sweep.py`)

```python
DEFAULT_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "phone": re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "dob_kw": re.compile(r"\bD\.?O\.?B\.?\b", re.IGNORECASE),
    "alien_num": re.compile(r"\bA\d{8,9}\b"),           # tightened from prototype 8,12
    "driver_lic": re.compile(r"\b(?:OLN|DLN|OLN#|DL|OL)\s?#?\s?[A-Z0-9]{6,}\b"),
    "race_sex": re.compile(r"\b[BWHAI]\s?/\s?[MF]\b"),
    "possible_birthdate": re.compile(
        r"\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19|20)\d\d\b"
    ),
}

# possible_birthdate is the ONLY broad-only pattern: a bare MM/DD/YYYY also
# matches an INCIDENT date, so it is a lead, never the publishable headline.
# Every OTHER pattern is high-precision PII and counts toward the STRICT headline.
# (Defining the broad-only set -- rather than enumerating "strict" -- keeps the
# rule correct even when a caller passes a custom `patterns` map, and names the
# headline honestly: "high-precision PII", NOT "structured identifiers" -- dob_kw
# and race_sex are sensitive descriptors, not IDs.)
BROAD_ONLY_PATTERN_NAMES: frozenset[str] = frozenset({"possible_birthdate"})


def _regex_hit(pattern: re.Pattern[str], text: str) -> bool:
    """True iff ``pattern`` matches ``text`` (``text`` is always a real str here)."""
    return pattern.search(text) is not None
```

**Step 4: Run — PASS.**
**Step 5: Commit** `"Phase 5.1: pii_sweep structured-PII patterns (A#-tightened, birthdate broad-only)"`.

---

## Task 3: `PersonClassifier` seam + `sweep` category tallies

**Files:** Modify `scripts/pii_sweep.py`; Modify `tests/test_pii_sweep.py`.

**Step 1: Write the failing tests** (a fake classifier — no spaCy)

```python
from scripts.pii_sweep import PersonFlags, sweep

class FakePersonClassifier:
    """Marker-driven: '<<OFFICIAL>>' -> official, '<<PERSON>>' -> unknown_role."""
    def __call__(self, texts):
        return [PersonFlags(official="<<OFFICIAL>>" in t,
                            unknown_role="<<PERSON>>" in t) for t in texts]

def test_sweep_weights_distinct_by_counts():
    s = pd.Series(["ssn 123-45-6789"] * 50 + ["nothing here"] * 3)
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["categories"]["ssn"] == {"weighted": 50, "distinct": 1}
    assert r["n_rows"] == 53 and r["n_nonblank_rows"] == 53
    assert r["n_distinct_texts"] == 2

def test_sweep_classifies_official_vs_unknown_role():
    s = pd.Series(["<<OFFICIAL>> Sgt called", "<<PERSON>> a subject", "plain"])
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["categories"]["person_official"]["distinct"] == 1
    assert r["categories"]["person_unknown_role"]["distinct"] == 1
```

**Step 2: Run — FAIL.**

**Step 3: Implementation** (append)

```python
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
    # exposure + local_texts added in Tasks 4 & 5.
    return result
```

> NOTE: `sweep` references `SpacyPersonClassifier` (Task 7) only on the lazy
> default path; tests inject a classifier, so Tasks 3–6 pass before Task 7 exists.
> Add a forward stub at the bottom now so the module imports cleanly (it is never
> instantiated until Task 7 — accept `*args/**kwargs` so the signature can't break):
>
> ```python
> class SpacyPersonClassifier:  # real implementation lands in Task 7
>     def __init__(self, *args, **kwargs):
>         raise NotImplementedError("SpacyPersonClassifier is implemented in Task 7")
> ```

**Step 4: Run — PASS.**
**Step 5: Commit** `"Phase 5.1: pii_sweep PersonClassifier seam + category tallies (TDD)"`.

---

## Task 4: `exposure` strict/broad + efficiency + the weighting invariant

**Files:** Modify `scripts/pii_sweep.py`; Modify `tests/test_pii_sweep.py`.

**Step 1: Write the failing tests**

```python
class RecordingFakeClassifier:
    """Records the texts it was called with, so a test can PROVE NER ran over
    DISTINCT texts (n_distinct calls), not every row."""
    def __init__(self):
        self.seen = None
    def __call__(self, texts):
        self.seen = list(texts)
        return [PersonFlags(official="<<OFFICIAL>>" in t,
                            unknown_role="<<PERSON>>" in t) for t in texts]

def test_exposure_strict_excludes_birthdate_and_officials():
    s = pd.Series([
        "ssn 123-45-6789",          # strict + broad
        "04/12/1989",               # BARE date -> possible_birthdate, broad ONLY
        "<<PERSON>> a name",        # unknown_role -> broad only
        "<<OFFICIAL>> Sgt Doe",     # official -> neither exposure metric
    ])
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["exposure"]["strict"]["distinct"] == 1            # only the ssn row
    assert r["exposure"]["broad"]["distinct"] == 3             # ssn + bare date + name
    assert r["categories"]["possible_birthdate"]["distinct"] == 1
    assert r["categories"]["person_official"]["distinct"] == 1  # reported, NOT exposure

def test_dob_keyword_is_strict_but_bare_date_is_not():
    # explicit "DOB" label = high-precision PII (strict); a bare date = a lead.
    r = sweep(pd.Series(["see DOB on file", "stopped 04/12/1989"]),
              person_classifier=FakePersonClassifier())
    assert r["categories"]["dob_kw"]["distinct"] == 1
    assert r["exposure"]["strict"]["distinct"] == 1            # the DOB-label row only
    assert r["exposure"]["broad"]["distinct"] == 2            # + the bare-date row

def test_mixed_official_plus_ssn_hits_strict_headline():
    s = pd.Series(["<<OFFICIAL>> Sgt Doe ssn 123-45-6789"])
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["exposure"]["strict"]["distinct"] == 1            # SSN is exposure regardless
    assert r["categories"]["person_official"]["distinct"] == 1

def test_classifier_runs_over_distinct_texts_not_every_row():
    rec = RecordingFakeClassifier()
    sweep(pd.Series(["ssn 123-45-6789"] * 50 + ["clean"] * 3), person_classifier=rec)
    assert rec.seen is not None and len(rec.seen) == 2         # n_distinct, NOT 53

def test_exposure_is_a_per_text_union_not_a_sum_of_categories():
    # one text matches TWO strict patterns: the row counts ONCE in exposure but
    # in BOTH categories -> sum(category weighted) != exposure weighted.
    s = pd.Series(["ssn 123-45-6789 ph 864-555-1212"] * 10)
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["categories"]["ssn"]["weighted"] == 10 and r["categories"]["phone"]["weighted"] == 10
    assert r["exposure"]["strict"]["weighted"] == 10           # union (10), NOT the sum (20)
    assert r["exposure"]["strict"]["distinct"] == 1

def test_weighting_matches_naive_per_row_scan():
    s = pd.Series(["A123456789"] * 7 + ["clean"] * 2 + ["x@y.org"] * 4)
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["exposure"]["broad"]["weighted"] == 11            # 7 A# rows + 4 email rows
    assert r["exposure"]["strict"]["weighted"] == 11
    for cat in r["categories"].values():
        assert cat["weighted"] >= cat["distinct"]

def test_efficiency_ratio():
    s = pd.Series(["dup"] * 6 + ["uniq"])
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["efficiency_ratio"] == 7 / 2                      # 7 nonblank rows / 2 distinct
```

**Step 2: Run — FAIL** (`exposure` KeyError).

**Step 3: Implementation** — insert before `return result` in `sweep`:

```python
    strict_names = [n for n in patterns if n not in BROAD_ONLY_PATTERN_NAMES]
    broad_only_names = [n for n in patterns if n in BROAD_ONLY_PATTERN_NAMES]
    strict_bool, broad_bool = [], []
    for i in range(n_distinct):
        strict_i = any(regex_hits[n][i] for n in strict_names)
        broad_i = strict_i or unknown[i] or any(regex_hits[n][i] for n in broad_only_names)
        strict_bool.append(strict_i)
        broad_bool.append(broad_i)

    result["exposure"] = {
        "strict": _tally(counts, strict_bool),   # publishable headline (high-precision PII)
        "broad": _tally(counts, broad_bool),     # + name-leads + possible_birthdate
    }
```

> `strict_bool` / `broad_bool` stay LOCAL variables — Task 5's `local_texts` block
> reads `broad_bool` directly (same function). NEVER stash bool lists in `result`;
> `sweep` ends with a plain `return result`, so no `_`-prefixed key can leak (a
> test in Task 5 asserts this explicitly, since `json.dumps` would not catch it).

**Step 4: Run — PASS.**
**Step 5: Commit** `"Phase 5.1: pii_sweep strict/broad exposure + weighting invariant (TDD)"`.

---

## Task 5: hashed `text_id` redact seam + opt-in `local_texts` + edge-safety

**Files:** Modify `scripts/pii_sweep.py`; Modify `tests/test_pii_sweep.py`.

**Step 1: Write the failing tests**

```python
import json

def test_local_texts_off_by_default():
    r = sweep(pd.Series(["ssn 123-45-6789"]), person_classifier=FakePersonClassifier())
    assert "local_texts" not in r                          # raw PII never returned unless asked

def test_local_texts_opt_in_is_keyed_by_text_id_and_excludes_official_only():
    s = pd.Series(["ssn 123-45-6789", "<<OFFICIAL>> Sgt Doe"])
    r = sweep(s, person_classifier=FakePersonClassifier(), collect_local_texts=True)
    assert len(r["local_texts"]) == 1                      # official-only row is NOT a redaction target
    (tid, entry), = r["local_texts"].items()
    assert tid == text_id("ssn 123-45-6789")
    assert "ssn" in entry["categories"] and entry["count"] == 1

def test_empty_input_is_safe_and_json_able():
    r = sweep(pd.Series([], dtype=object), person_classifier=FakePersonClassifier())
    assert r["efficiency_ratio"] is None                  # no fake 0
    assert r["exposure"]["strict"] == {"weighted": 0, "distinct": 0}
    json.dumps(r)                                          # native types only

def test_internal_bool_lists_do_not_leak_into_the_result():
    # _strict_bool / _broad_bool ARE json-serializable, so json.dumps() would not
    # catch a leak -- assert the keys are absent explicitly.
    r = sweep(pd.Series(["ssn 123-45-6789"]), person_classifier=FakePersonClassifier())
    assert "_strict_bool" not in r and "_broad_bool" not in r

def test_official_and_unknown_role_can_both_appear_in_one_text():
    r = sweep(pd.Series(["<<OFFICIAL>> Sgt Doe stopped <<PERSON>>"]),
              person_classifier=FakePersonClassifier())
    assert r["categories"]["person_official"]["distinct"] == 1
    assert r["categories"]["person_unknown_role"]["distinct"] == 1
    assert r["exposure"]["broad"]["distinct"] == 1         # unknown_role -> broad
    assert r["exposure"]["strict"]["distinct"] == 0        # no structured ID present

def test_non_string_cells_do_not_crash_and_carry_no_pii():
    s = pd.Series(["ssn 123-45-6789", 42, 3.14, None], dtype=object)
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["n_nonblank_rows"] == 3                        # None dropped; 42/3.14 kept as text
    assert r["categories"]["ssn"]["distinct"] == 1
    assert r["exposure"]["strict"]["distinct"] == 1
```

**Step 2: Run — FAIL.**

**Step 3: Implementation** — insert just before `return result` (reads the local `broad_bool`):

```python
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
            local[text_id(t)] = {"text": t, "count": int(counts[i]), "categories": cats}
        result["local_texts"] = local
```

**Step 4: Run — PASS** (full file: `& .venv\Scripts\python.exe -m pytest tests/test_pii_sweep.py -v`).
**Step 5: Commit** `"Phase 5.1: pii_sweep hashed text_id seam + opt-in local_texts (TDD)"`.

---

## Task 6: drift-protection test vs `recipe.check_pii`

**Files:** Modify `tests/test_pii_sweep.py` (test-only — no production change).

**Step 1: Write the test**

```python
from scripts.recipe import _DEFAULT_PII_PATTERNS as RECIPE_PII  # Phase 4 defaults (regex STRINGS)
from scripts.pii_sweep import DEFAULT_PII_PATTERNS

def test_overlap_patterns_stay_consistent_with_recipe_check_pii():
    """Decoupled modules (no imports either way); this tripwire fires if the
    SHARED-INTENT patterns silently diverge. recipe stores regex STRINGS,
    pii_sweep COMPILED -> compare `.pattern` to the string. recipe's `a_number`
    is pii_sweep's `alien_num` (different key, same concept; both `A\\d{8,9}`)."""
    overlap = {"ssn": "ssn", "phone": "phone", "email": "email", "alien_num": "a_number"}
    for sweep_key, recipe_key in overlap.items():
        assert DEFAULT_PII_PATTERNS[sweep_key].pattern == RECIPE_PII[recipe_key], sweep_key
```

> Verify `recipe._DEFAULT_PII_PATTERNS` key names + values first (it stores regex
> STRINGS; `pii_sweep` stores compiled patterns — compare `.pattern`). Recipe's
> `a_number` key maps to `r"\bA\d{8,9}\b"`; if its phone/ssn/email strings differ
> textually from pii_sweep's, align the assertion to the real strings (the goal is
> a tripwire on *accidental* drift, not to force false equality). `a_number` vs
> `alien_num` are intentionally different key names for the same concept.

**Step 2–4:** Run — adjust assertion to the actual recipe strings — PASS.
**Step 5: Commit** `"Phase 5.1: drift-protection test (pii_sweep vs recipe.check_pii overlap)"`.

---

## Task 7: `SpacyPersonClassifier` (real spaCy) + model-gated integration test

**Files:** Modify `scripts/pii_sweep.py` (replace the Task-3 stub); Modify `tests/test_pii_sweep.py`.

**Step 1: Write the failing (model-gated) integration test**

```python
import pytest

@pytest.fixture(scope="module")
def spacy_classifier():
    pytest.importorskip("en_core_web_lg")     # CI without the 400MB model skips cleanly
    from scripts.pii_sweep import SpacyPersonClassifier
    return SpacyPersonClassifier

def test_real_ner_finds_person_presence(spacy_classifier):
    flags = spacy_classifier()(["John Smith was pulled over", "vehicle of interest"])
    assert flags[0].unknown_role and not flags[0].official     # bare name -> unknown_role
    assert not flags[1].unknown_role and not flags[1].official # no person

def test_title_prefix_marks_official(spacy_classifier):
    (f,) = spacy_classifier()(["Officer Ramirez requested backup"])
    assert f.official and not f.unknown_role                   # title prefix -> official

def test_official_names_lexicon_marks_untitled_official(spacy_classifier):
    clf = spacy_classifier()(official_names={"dana wheeler"})  # built from the searcher field
    (f,) = clf(["Dana Wheeler ran the plate"])
    assert f.official

def test_norm_name_tokens_keeps_internal_punctuation_drops_badge():
    from scripts.pii_sweep import _norm_name_tokens            # PURE -- no model needed
    assert _norm_name_tokens("O'Brien") == frozenset({"o'brien"})
    assert _norm_name_tokens("Anne-Marie Diaz, #4471") == frozenset({"anne-marie", "diaz"})

def test_sweep_wires_official_names_through_to_default_classifier(spacy_classifier):
    # the LAZY default path: sweep() must build SpacyPersonClassifier(official_names=...)
    r = sweep(pd.Series(["Dana Wheeler ran the plate"]), official_names={"dana wheeler"})
    assert r["categories"]["person_official"]["distinct"] == 1
    assert r["categories"]["person_unknown_role"]["distinct"] == 0
```

> Assert PERSON **presence**, never span text (spaCy spans over-extend — verified
> in the research gate). The lexicon match is heuristic; the design doc documents
> its limits (shared-name false positives) — keep the test to a clear case.

**Step 2: Run — FAIL** (stub raises `NotImplementedError`).

**Step 3: Implementation** — replace the stub with:

```python
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
    or (b) the span's name tokens match the ``official_names`` lexicon (built by
    the caller from the structured searcher/user field); else ``unknown_role``.
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
```

Now DELETE the Task-3 forward stub.

**Step 4: Run** the integration tests locally (model installed):
`& .venv\Scripts\python.exe -m pytest tests/test_pii_sweep.py -v` → PASS (incl. the 3 spaCy tests; they `importorskip` if the model is absent).

**Step 5: Commit** `"Phase 5.1: SpacyPersonClassifier (title + lexicon official split) + integration tests"`.

---

## Task 8: `pii-sweep` SKILL.md + wiring smoke test

**Files:**
- Create: `skills/pii-sweep/SKILL.md` (author via `plugin-dev:skill-development`)
- Create: `tests/test_pii_sweep_skill.py`

**Step 1: Write the failing smoke test** (mirrors `test_analysis_recipe_skill.py`)

```python
# tests/test_pii_sweep_skill.py — PyYAML frontmatter smoke
from pathlib import Path
import yaml

SKILL = Path("skills/pii-sweep/SKILL.md")

def test_skill_frontmatter_and_body():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---")
    fm = yaml.safe_load(text.split("---", 2)[1])
    assert fm["name"] == "pii-sweep"
    assert "PII" in fm["description"] or "pii" in fm["description"]
    body = text.split("---", 2)[2]
    assert "pii_sweep" in body and "redact-output" in body     # documents the engine + seam
    assert "person_official" in body and "strict" in body       # documents officials split + headline
```

**Step 2: Run — FAIL** (no SKILL.md).

**Step 3: Author the skill** via `plugin-dev:skill-development`. SKILL.md must cover:
- **Trigger description** (third-person, imperative; "quantify/sweep PII exposure in a FOIA free-text field").
- **Pipeline:** load via `dataset-analyze` (load_table → data_quality → derive) → build the
  **officials lexicon** from the structured searcher/user/org column → `sweep(reason_series,
  official_names=lexicon)` → publish the **`exposure.strict`** headline + per-category tally via
  **Librarian**; route **`local_texts`** (opt-in) to a LOCAL non-vault exhibit for `redact-output`.
- **The redact-output seam:** published notes carry the AGGREGATE tally only (no per-text data,
  no `text_id`s on any published path); the opt-in `local_texts` map (LOCAL, non-vault) is keyed
  by `text_id` for `redact-output` (Phase 7) to join on — names→initials, exhibit local only (design §7).
- **Rigor:** officials (named for accountability) excluded from exposure; `person_unknown_role`
  is a lead ("names not identified as officials"), not a verdict; `possible_birthdate` is broad-only;
  the ~400 MB model + ~1.5 s load; CPU-only.
- Note pii-sweep is the **authoritative** tally (vs `recipe.check_pii`'s presence indicator).

**Step 4: Run — PASS.**
**Step 5: Commit** `"Phase 5.2: pii-sweep SKILL.md + wiring smoke (redact-output seam)"`.

---

## Final verification (before the impl-review gate)

1. `mise run test` → the FULL suite is green (Phase 4's 269 + the new pii_sweep tests).
2. Confirm the integration tests actually RAN locally (model installed) — not silently
   skipped: `& .venv\Scripts\python.exe -m pytest tests/test_pii_sweep.py -k spacy -v`.
3. No raw-PII / corpus path anywhere; all fixtures synthetic.
4. `scripts/pii_sweep.py` imports cleanly with NO spaCy import at module top (spaCy only
   inside `SpacyPersonClassifier._load`): `& .venv\Scripts\python.exe -c "import scripts.pii_sweep"`
   must not import spacy (so `recipe.py`/others stay ML-free). Add a test asserting
   `"spacy" not in sys.modules` after importing `scripts.pii_sweep` fresh if practical.

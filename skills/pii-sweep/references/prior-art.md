# Phase 5 research gate — `pii-sweep` (spaCy NER + structured-PII regex)

The **research gate** for Task 5.1 / 5.2: *verified library facts only*. The
design — the distinct→weight algorithm, the output/tally contract, the module
shape, the test plan, and the open decisions — lives in the Phase 5 **plan**
(`docs/plans/2026-06-04-magpie-phase5-pii-sweep.md`), which goes through the
native plan-review gate. This file is the durable per-skill reference that ships
with `pii-sweep`; it records what is *true about the tools*, so the plan and its
reviewer can rely on it.

Source material: the Simpsonville pilot prototype `pii_ner.py` (productized here),
design doc §5.2 / §6.1 step 4 / §7, and the official spaCy docs. **All tests use
SYNTHETIC fixtures — the private corpus is never read here** (it is wired into
Phase 11 golden tests via an env var only).

---

## 0. Scope — and what this is NOT

`pii-sweep` is the **authoritative** free-text PII tally: spaCy `en_core_web_lg`
**PERSON** NER plus structured-identifier regex, run over **distinct** values and
**weighted by row counts**, emitting a structured exposure tally that feeds
`redact-output` (Phase 7).

It does **not** duplicate Phase 4's `recipe.check_pii`. That check is a fast
**structured-regex PRESENCE indicator** (4 patterns: a_number/ssn/phone/email; no
bare date; no NER) used inside the per-source recipe, and it explicitly **defers
semantic NER (names, weighted by count) to this phase** (see its `note`). The two
stay **decoupled**: `recipe.py` imports no heavy ML module, so `pii_sweep.py`
(which imports spaCy) must not share code with `recipe.py` in either direction —
`pii_sweep` carries its own, broader pattern set as the authoritative superset.

---

## 1. Verified environment facts (empirical — run in `.venv`, 2026-06-04)

| Fact | Value | How verified |
|---|---|---|
| spaCy version | **3.8.14** (latest stable) | PyPI JSON; `pip install` |
| `requires_python` | `<3.15,>=3.9` → 3.12.10 OK | PyPI JSON |
| numpy constraint | `numpy>=1.19.0` → **pinned `numpy==2.4.6` untouched** | pip log: "already satisfied (2.4.6)" |
| transitive pins | thinc 8.3.13, blis 1.3.3 (CPU BLAS), pydantic 2.13.4 | pip log |
| Model | **`en_core_web_lg==3.8.0`**, 400.7 MB wheel | `spacy download`; pip log |
| spaCy install time | ~44 s (wheels cached) | timed |
| Model download+install | ~121 s (400 MB @ ~3.4 MB/s) | timed |
| Cold model **load** (NER-only) | **≈ 1.5 s** | timed `spacy.load(...)` |
| `nlp.pipe` of 6 short texts | 0.014 s | timed |
| ABI sanity | `import spacy, numpy, thinc` clean (no numpy-2 ABI break) | imported |

CPU-only, no GPU/CUDA extras (the `spacy[cuda]`/cupy numpy-2 incompatibility in
issue #13681 is avoided by installing plain `spacy`). **Pinned** in
`requirements-dev.txt`: `spacy==3.8.14` + the model by its reproducible
GitHub-release wheel URL (the spaCy-recommended way to pin a model in a
requirements file). `mise run bootstrap` now pulls ~400 MB for the model.

### NER labels (18, OntoNotes 5) — empirically read from the loaded model
`CARDINAL, DATE, EVENT, FAC, GPE, LANGUAGE, LAW, LOC, MONEY, NORP, ORDINAL, ORG,
PERCENT, PERSON, PRODUCT, QUANTITY, TIME, WORK_OF_ART`. We use **PERSON** only
(names = the semantic-PII signal regex can't catch). GPE/ORG/etc. are out of scope
for the tally (agencies/places are not third-party PII to redact).

### Why `lg` (not `md`/`sm`)
The pilot validated `en_core_web_lg` specifically, so the Phase 11 golden tally
(~11,900 exposures / 747 agencies) is pinned to `lg`. `lg`'s NER consumes the
model's static vectors via `tok2vec`, so its PERSON output is not interchangeable
with `md`/`sm`. Keep `lg`.

---

## 2. spaCy NER API contract (current on 3.8.14 — matches the prototype)

```python
import spacy
nlp = spacy.load("en_core_web_lg",
                 disable=["tagger", "parser", "lemmatizer", "attribute_ruler"])
# → nlp.pipe_names == ["tok2vec", "ner"]   (NER-only; faster, lower memory)

for doc in nlp.pipe(texts, batch_size=256):
    has_person = any(e.label_ == "PERSON" and len(e.text.strip()) > 2
                     for e in doc.ents)
```

- `disable=[...]` at load is the supported way to drop unused components; verified
  the live pipeline is just `tok2vec + ner`.
- `nlp.pipe(texts, batch_size=256)` is the batched path; preserves input order.
- The `len(e.text.strip()) > 2` floor (from the prototype) drops 1–2 char PERSON
  false positives (initials, stray tokens).
- **Span over-extension finding (test-critical):** NER spans can absorb adjacent
  tokens — `"Maria Gonzalez DOB 04/12/1989"` returned a single PERSON span
  `"Maria Gonzalez DOB"`. **Tests must assert PERSON *presence*, never an exact
  span string.** The tally only needs the boolean "this text contains a PERSON".
- Negative control verified: `"Assist local police department"` yields **no**
  PERSON (NER does not false-positive on "police"; the substring-"ice" trap is a
  keyword concern that does not arise here — see §4).

---

## 3. Candidate structured-PII regex patterns (from the prototype `pii_ner.py`)

The patterns the pilot used, recorded here as **candidates** with precision notes.
*Which become the default set, and whether `birthdate` counts toward `any_pii`, is
a plan decision* — not fixed here. All are `\b`-anchored.

| name | pattern | precision | notes |
|---|---|---|---|
| `phone` | `\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b` | high | NANP 3-3-4 |
| `ssn` | `\b\d{3}-\d{2}-\d{4}\b` | high | dashed SSN |
| `email` | `\b[\w.+-]+@[\w-]+\.[\w.-]+\b` | high | |
| `dob_kw` | `\bD\.?O\.?B\.?\b` (IGNORECASE) | high | explicit "DOB" label |
| `birthdate` | `\b(?:0?[1-9]\|1[0-2])[/-](?:0?[1-9]\|[12]\d\|3[01])[/-](?:19\|20)\d\d\b` | **LOWER** | any MM/DD/YYYY token — also matches *incident* dates (not PII); a lead, not a verdict |
| `race_sex` | `\b[BWHAI]\s?/\s?[MF]\b` | med | demographic descriptor (e.g. `B/M`, `W/F`) |
| `alien_num` | `\bA\d{8,12}\b` | med | A-number; digit range differs from `recipe`'s `A\d{8,9}` — reconcile in the plan |
| `driver_lic` | `\b(?:OLN\|DLN\|OLN#\|DL\|OL)\s?#?\s?[A-Z0-9]{6,}\b` | med | DL prefixes |

Null/blank-safe matching (a non-string / NaN / blank cell yields no match) mirrors
`recipe._regex_hits`. Each pattern is a **separate category** so a low-precision
signal can't silently inflate a high-precision headline.

---

## 4. Rigor stance (verified, publish-critical)

- **Leads-not-verdicts:** report every category separately; `birthdate` is
  explicitly lower-confidence (incident-date false positives) and paired with the
  high-precision `dob_kw`.
- **Word-boundary discipline:** all structured patterns are `\b`-anchored. Note
  `pii-sweep` does **no keyword categorization**, so there is nothing here to route
  through `derive.keyword_mask` (the ICE/polICE guardrail) — the analogous rigor is
  the `\b` anchors. (Considered and N/A, not overlooked.)
- **PII never published:** raw matched names/texts are themselves PII — only the
  aggregate tally is publishable; matched exhibits stay local (design §7). The real
  corpus is never read/committed; all fixtures synthetic.

---

## Design deferred to the Phase 5 plan (`docs/plans/`)
The following are **design decisions**, authored via brainstorming → `writing-plans`
and reviewed by the native plan-review gate — NOT settled in this research gate:
the distinct→NER→weight algorithm and the "~7× efficiency" multiplier invariant;
the output/tally schema and the `redact-output` hand-off contract; the module shape
(injectable detector + lazy spaCy classifier); the TDD test plan
(pure fake-detector tests + a model-gated integration test); and the open
decisions (default pattern set / `birthdate` membership / `alien_num` digit range).

> **RESOLVED in the plan + design doc (authoritative — this gate keeps the prototype
> CANDIDATES above as the starting record).** The shipped engine: the seam is
> `PersonClassifier` / `SpacyPersonClassifier` (returns `{official, unknown_role}` per
> text, not a bare bool); `alien_num` = `A\d{8,9}` (the §3 `A\d{8,12}` candidate was
> tightened to match `recipe`); `race_sex` is **broad-only** and `driver_lic` requires
> a digit in the body (code-quality refinements); `birthdate` → `possible_birthdate`,
> excluded from the strict headline. See `docs/plans/2026-06-04-magpie-phase5-pii-sweep*.md`.

---

## Sources
- spaCy install / models / pipeline: <https://spacy.io/usage>, <https://spacy.io/usage/models>, <https://spacy.io/models/en>
- spaCy version + deps: <https://pypi.org/pypi/spacy/json> (3.8.14; `numpy>=1.19.0`; `requires_python <3.15,>=3.9`)
- model wheels (reproducible pin): <https://github.com/explosion/spacy-models/releases>
- numpy-2 / CUDA caveat: <https://github.com/explosion/spaCy/issues/13681>
- Empirical: installed `spacy==3.8.14` + `en_core_web_lg==3.8.0` in the project `.venv`; load 1.49 s; PERSON detection + NER labels confirmed (2026-06-04).
- Pilot prototype `pii_ner.py` + design doc §5.2 / §6.1 / §7; golden anchor in `magpie-pilot-source` (~11,900 PII / 747 agencies — Phase 11, not now).

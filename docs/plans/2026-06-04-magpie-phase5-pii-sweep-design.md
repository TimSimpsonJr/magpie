# Magpie Phase 5 — `pii-sweep` design

- **Date:** 2026-06-04
- **Status:** Design approved (brainstorming complete; Codex stand-in converged "looks good, write the plan" with three conditions, all adopted). Next: `writing-plans`.
- **Author:** Tim Simpson (with Claude; Codex as brainstorm partner, autonomous mode)
- **Phase:** Layer 0–1, Phase 5 (Tasks 5.0–5.2) of `docs/plans/2026-06-03-magpie-layer-0-1.md`.
- **Research gate:** `skills/pii-sweep/references/prior-art.md` (verified spaCy facts).
- **Source of truth:** design doc `2026-06-03-magpie-design.md` §5.2 / §6.1 step 4 / §7; pilot prototype `pii_ner.py`.

---

## 1. Goal & scope

`pii-sweep` is the **authoritative** free-text PII-**exposure** tally for a FOIA
free-text field (the Flock ALPR "reason" field): spaCy `en_core_web_lg` PERSON NER
plus structured-identifier regex, run over **distinct** values and **weighted by
row counts** (the pilot's ~7× efficiency lesson), emitting a structured exposure
tally that feeds `redact-output` (Phase 7).

**Not `recipe.check_pii`.** Phase 4's check is a fast structured-regex *presence
indicator* (4 patterns; no NER) that explicitly defers semantic NER to here. The
two stay **decoupled** — `recipe.py` imports no heavy ML module, so `pii_sweep.py`
(which imports spaCy) shares no code with it in either direction. A **drift-
protection test** asserts the overlap patterns still match Phase 4's four defaults.

**Publishable honesty over prototype reproduction.** Where the prototype's choices
would inflate a breach headline (bare dates as PII; loose A-numbers; every name as
"exposure"), the productized default is the *defensible* number; the prototype's
looser figures live in a documented **compatibility profile** (Phase 11).

---

## 2. Key decisions (the heart of this design)

### 2.1 Officials vs PII-that-should-have-been-sanitized (Tim's requirement, refined)
The searching **officer and named agency are public actors in their official
capacity** — named *for* accountability, not redacted. Counting them as "exposure"
overstates the leak and undercuts the accountability framing. But the inverse
over-claim is just as wrong: the non-official remainder is **not** provably
"private third parties." So the PERSON category splits three honest ways:

- **`person_official`** — positively identified as an official, by either signal:
  1. an official **title/rank** token immediately precedes the PERSON span
     (`Officer/Ofc/Sgt/Sergeant/Deputy/Det/Detective/Lt/Cpl/Capt/Captain/Chief/
     Sheriff/Trooper/Marshal/Agent/Investigator/Patrolman/Cmdr/Major/Col/...`,
     config-overridable), **or**
  2. the span **contains** an **officials-lexicon** name — a normalized
     token-SUBSET match (the official's name tokens ⊆ the span's tokens, robust to
     spaCy span over-extension), the lexicon built by the skill from the structured
     *searcher/user/org* field and passed in. This is the stronger signal (catches
     an official named without a title); e.g. `"Dana Wheeler DOB"` still matches.
- **`person_unknown_role`** — a PERSON **not** identified as official. Published
  wording: *"person names not identified as officials"* — a **lead**, never
  "private people." (Positive private-subject cues — `victim`/`subject`/`passenger`
  /`juvenile`/`DOB` near the span — that would justify a stronger `person_third_party`
  label are a documented **future enhancement**, not v1.)
- Officials are **excluded** from every exposure metric.

### 2.2 Two exposure metrics — strict headline + broad leads
- **`exposure.strict`** — rows carrying a **high-precision PII pattern**: every
  default pattern EXCEPT the ambiguous bare date — `ssn`, `phone`, `email`,
  `dob_kw`, `alien_num`, `driver_lic`, `race_sex`. Defined as "not in the
  broad-only set" — the default is `BROAD_ONLY_PATTERN_NAMES`, and a caller passing
  a custom `patterns` map marks its own ambiguous patterns via
  `sweep(broad_only_names=...)` (this is also how the Phase 11 compat profile folds
  `possible_birthdate` back into the headline). This is the **publishable
  headline**: each is an identifier or sensitive descriptor that should have been
  sanitized. (`dob_kw`/`race_sex` are sensitive *descriptors*, not
  literal IDs — hence "high-precision PII", not "structured identifiers".)
- **`exposure.broad`** — `strict` ∪ `person_unknown_role` ∪ `possible_birthdate`:
  the broader **review/leads** set (names not identified as officials; bare dates
  that *might* be DOBs). Not a headline; the analyst escalates leads → verdicts via
  the human gate / `redact-output` exhibit.

### 2.3 Pattern decisions
- **`possible_birthdate`** (renamed from `birthdate`): kept, but **excluded from
  the default headline** — a bare `MM/DD/YYYY` is also an *incident* date (exactly
  why `recipe.check_pii` omits bare dates). Counts separately; lands only in
  `broad`. Config can include it in a compatibility metric.
- **`alien_num` = `A\d{8,9}`** (tightened from the prototype's `A\d{8,12}` to match
  `recipe.check_pii`; USCIS/EOIR put A-numbers at 7–9 digits). The pilot's `8,12`
  is a compatibility-profile override only.
- The full pattern set is **config-overridable** (`patterns=` argument); the module
  ships `DEFAULT_PII_PATTERNS`.

### 2.4 `keyword_mask` reuse — N/A (decided)
Official-title classification is **token-level** (compare normalized spaCy tokens
to a title set) — inherently word-bounded, so the ICE/`polICE` substring guardrail
(`derive.keyword_mask`) does not apply. We do **not** force `compile_keyword_regex`
in for "lineage theater."

---

## 3. Architecture — pure core, spaCy at the edge

`scripts/pii_sweep.py` (mirrors the codebase's `stats`/`derive` purity +
`build_dataset_db` IO-at-edge idiom):

- `DEFAULT_PII_PATTERNS: dict[str, re.Pattern]` — §2.3 set, all `\b`-anchored.
- `OFFICIAL_TITLES: frozenset[str]` — default rank/title tokens (overridable).
- **`PersonClassifier`** = `Callable[[list[str]], list[PersonFlags]]` where
  `PersonFlags` is per-text `{official: bool, unknown_role: bool}` (a text may have
  both). This is the **injection seam**:
  - `SpacyPersonClassifier(*, official_names=frozenset(), titles=OFFICIAL_TITLES)`
    — lazily `spacy.load("en_core_web_lg", disable=[...])` on first call; runs
    `nlp.pipe(batch_size=256)`; for each PERSON ent (`len(text.strip())>2`)
    classifies **official** if a title token precedes the span (≤2 tokens) OR the
    normalized span matches `official_names`, else **unknown_role**. Classification
    uses **token/context windows, never exact span strings** (spaCy spans
    over-extend, e.g. "Maria Gonzalez DOB").
  - Tests inject a **fake** classifier → the pure tally/regex/weight logic needs no
    400 MB model.
- `distinct_texts(series) -> (texts, counts)` — pure; **strip outer whitespace**
  (so `"John "`/`"John"` collapse), drop null/blank, **preserve case** (NER cares),
  `value_counts`.
- `sweep(series, *, person_classifier=None, patterns=DEFAULT_PII_PATTERNS,
  official_names=None, collect_local_texts=False) -> PiiSweepResult` — orchestrates
  §4; if `person_classifier is None`, lazily builds
  `SpacyPersonClassifier(official_names=official_names or frozenset())`.

---

## 4. The distinct → classify → weight algorithm (the ~7× lesson)

```
1. texts, counts = distinct_texts(series)         # NER runs over DISTINCT only
2. flags = person_classifier(texts)               # {official, unknown_role}/text
   regex[name][i] = bool(pattern.search(texts[i]))
3. per text i: strict[i]  = any(structured-id pattern hit)
               broad[i]   = strict[i] OR unknown_role[i] OR possible_birthdate[i]
4. WEIGHTED tally[c] = sum(counts[i] where flag[c][i])    # ≡ a per-row scan
   DISTINCT tally[c] = count(i where flag[c][i])
5. efficiency_ratio = n_nonblank_rows / n_distinct_texts  # the multiplier; None if 0
```

**Invariant the test pins:** weighted-from-distinct **≡** a naive per-row scan, at
`n_distinct` classifier calls instead of `n_rows`; and `weighted ≥ distinct` per
category.

---

## 5. Output contract — tally + hashed redact seam

`PiiSweepResult` (plain, JSON-able; native `int`s; `None` never a fake `0`):

```python
{
  "n_rows": int,
  "n_nonblank_rows": int,            # the weighting base (stated explicitly)
  "n_distinct_texts": int,
  "efficiency_ratio": float | None,  # n_nonblank_rows / n_distinct_texts
  "categories": {                    # one per category, all reported separately
     "person_official":     {"weighted": int, "distinct": int},
     "person_unknown_role": {"weighted": int, "distinct": int},
     "ssn": {...}, "phone": {...}, "email": {...}, "dob_kw": {...},
     "alien_num": {...}, "driver_lic": {...}, "race_sex": {...},
     "possible_birthdate":  {"weighted": int, "distinct": int},
  },
  "exposure": {
     "strict": {"weighted": int, "distinct": int},   # publishable headline
     "broad":  {"weighted": int, "distinct": int},   # strict + name-leads + possible_birthdate
  },
}
```

**Redact seam (Phase 7), privacy-safe by construction:**
- Every distinct text gets a stable **`text_id` = `sha256(text.strip())`** (hex,
  truncated, case-preserved — strips so it matches `distinct_texts`). It is the
  **local** join key in `local_texts`; published notes carry the **aggregate tally
  only** — no per-text data and no `text_id`s cross a published path.
- Raw PII-bearing text is **opt-in only**: `collect_local_texts=False` by default.
  When `True`, the result also carries `local_texts: {text_id: {text, count,
  categories}}` for the skill to write a **local, non-vault** exhibit. Default-off
  prevents accidental serialization/logging of raw names.
- `redact-output` joins on `text_id` (names → initials in published artifacts; full
  exhibit local only — design §7).

---

## 6. Testing (all SYNTHETIC fixtures; `mise run test`)

**Pure tests (fast, fake classifier — no model):**
- `distinct_texts`: outer-whitespace strip + null/blank drop; **case preserved**;
  counts correct.
- weighting **invariant** (§4): weighted-from-distinct ≡ per-row scan; `weighted ≥
  distinct`.
- each regex: positive + negative + `\b` word-boundary; null/blank-safe.
- strict vs broad composition; `possible_birthdate` ∈ broad only, ∉ strict;
  officials ∉ either exposure.
- **mixed-case** (Codex): official-only name → ∉ headline; official **+ SSN** → ∈
  strict; official **+ bare date** → ∈ broad only.
- `text_id` stable + deterministic; `collect_local_texts=False` → no raw text in
  the result; `=True` → `local_texts` keyed by `text_id`.
- empty / all-blank input → zero tallies + `efficiency_ratio is None` (no div-by-0,
  no fake 0); result is native/JSON-able.
- **drift-protection** (Codex): `pii_sweep`'s `{ssn, phone, email, alien_num}`
  strict-overlap patterns still match `recipe.check_pii`'s four defaults.

**Integration test (real spaCy, model-gated):**
- `pytest.importorskip("en_core_web_lg")` (CI without the 400 MB model still
  passes; locally it runs).
- a known PERSON (`"John Smith ..."`) → person tally ≥ 1; a non-name
  (`"vehicle of interest"`) → none. Assert **presence**, not span text.
- title-prefix (`"Officer Ramirez ..."`) → `person_official`; bare name → `person_
  unknown_role`; an `official_names` lexicon hit on an untitled name → `official`.

---

## 7. Rigor guardrails (publish-critical)

- **Leads-not-verdicts:** officials excluded from exposure; the non-official
  remainder is a **lead** ("not identified as officials"), not a verdict;
  `possible_birthdate` is broad-only; undefined ratios are `None`. Strict headline
  is the only thing called "exposure."
- **Presence ≠ value:** every category reported separately; the strict subset is
  recomputable.
- **PII never published:** raw text opt-in + behind a `text_id` hash; exhibits
  local-only; real corpus never read/committed; all fixtures synthetic.
- **Word-boundary discipline:** `\b`-anchored structured patterns; token-level
  official-title match (no substring trap).

---

## 8. Phase 11 note (not now)
The documented pilot tally (~11,900 / 747 agencies) used `A\d{8,12}` +
birthdate-in-headline + every-name-as-exposure. That figure is reproduced via a
documented **compatibility config** (looser A-number, `possible_birthdate` folded
into a compat metric, names un-split), NOT the default. The default strict/broad
numbers are the publishable headline. The golden test targets the compat metric for
the historical figure and asserts the strict/broad split separately.

---

## 9. Brainstorm provenance
Codex (thread `019e944f…`, `[MODE: brainstorm-partner]`, autonomous) reviewed the
draft and converged conditional on: PERSON split → official + unknown/possible (not
"third_party"); bare dates out of the default headline; tighten A-numbers. All
adopted above, plus its six refinements (independent modules + drift test; N/A
`keyword_mask`; opt-in raw text; hashed `text_id` seam; mixed-case tests; strip-not-
lowercase). Full reply: `.codex-review/phase5-brainstorm-result.txt` (gitignored).

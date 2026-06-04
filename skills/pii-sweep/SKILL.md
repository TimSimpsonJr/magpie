---
name: pii-sweep
description: This skill should be used when the user asks to "sweep PII exposure in a FOIA free-text field", "quantify how much PII a free-text reason/narrative column exposed", "run the authoritative PII tally", "count names / SSNs / A-numbers / DOBs in a reason column weighted by row count", "separate officials named for accountability from PII that should have been redacted", or otherwise wants the authoritative spaCy-NER + structured-regex PII-exposure tally over a single FOIA / audit-log free-text column (feeding redact-output). NOT the fast presence check inside the recipe.
version: 0.1.0
---

# pii-sweep

Produce Magpie's **authoritative** PII-exposure tally over one FOIA / audit-log
free-text column (the reason / narrative / justification field): spaCy
`en_core_web_lg` **PERSON** NER plus structured-identifier regex, run over the
**distinct** values and **weighted by row counts**, splitting officials (named
for accountability) from PII that should have been sanitized. The aggregate tally
is the publishable headline; the matched texts stay LOCAL and feed `redact-output`
(Phase 7). This is the engine behind the recipe's `pii` check — that check is a
fast presence indicator; this is the authoritative count.

One deterministic engine does the work; the agent orchestrates the prep and the
output:

- `scripts/pii_sweep.py::sweep(series, ...)` — the distinct → classify → weight →
  tally pass (pure core; spaCy only at the lazy classifier edge).

The pure core is golden-tested with a fake classifier (no 400 MB model). The
verified spaCy facts, the NER-label scope, and the candidate pattern set live in
`references/prior-art.md` (the Phase 5 research gate) — consult it before changing
a model call or a regex. `pii-sweep` shares NO code with `recipe.check_pii` in
either direction (a drift test guards the overlapping patterns), so importing it
stays ML-free until `sweep` actually runs.

## The pipeline

Run the steps in order.

1. **Prep the source via `dataset-analyze`** (load_table → data_quality gate →
   derive). The sweep runs on ONE column: the cleaned free-text reason / narrative
   series. Gate on truncation FIRST — never sweep a silently-truncated export.

2. **Build the officials lexicon from the STRUCTURED column** — not from the
   free text. Take the searcher / user / requesting-agency field (the structured
   identity already in the export) and pass its distinct values as
   `official_names=`. `sweep` normalizes each into name tokens and marks a PERSON
   span `official` when an official's tokens are a subset of the span (robust to
   spaCy span over-extension) OR a rank/title token immediately precedes the span
   (`Sgt`, `Officer`, `Deputy`, ...). This is how an UNTITLED official ("Dana
   Wheeler ran the plate") is still attributed for accountability rather than
   counted as exposed PII.

3. **Sweep** —

   ```python
   from scripts.pii_sweep import sweep

   result = sweep(
       reason_series,                 # the ONE cleaned free-text column
       official_names=lexicon,        # distinct values of the structured searcher/user field
       collect_local_texts=True,      # opt in ONLY for the local redact-output exhibit
   )
   ```

   With `person_classifier` omitted, `sweep` lazily builds the production
   `SpacyPersonClassifier` (loads `en_core_web_lg`, NER-only, on first call).
   The result is:

   - `categories` — a `{weighted, distinct}` pair per category: the regex
     categories (`phone`, `ssn`, `email`, `dob_kw`, `alien_num`, `driver_lic`,
     `race_sex`, `possible_birthdate`) plus `person_official` and
     `person_unknown_role`.
   - `exposure.strict` — the **publishable headline**: the per-row UNION of the
     high-precision PII categories (a row with both an SSN and a phone counts
     ONCE, not twice). Officials are EXCLUDED.
   - `exposure.broad` — `strict` plus the leads: `person_unknown_role`,
     `possible_birthdate`, and `race_sex`.
   - `efficiency_ratio` — `n_nonblank_rows / n_distinct_texts`: how much work the
     distinct-then-weight pass saved (the pilot's ~7× lesson — running NER over
     every row instead of distinct values is ~7× the work for the same answer).
   - `local_texts` — present ONLY when `collect_local_texts=True`: a map keyed by
     `text_id` of the redaction-target texts (officials-only rows are excluded).

4. **Publish the AGGREGATE via Librarian.** Write the `exposure.strict` headline
   and the per-category tally through **Librarian** (hub-and-spoke notes, vault or
   portable Markdown; tabular tallies to CSV alongside). Publish counts ONLY.

5. **Route `local_texts` to a LOCAL, non-vault exhibit** for `redact-output`
   (Phase 7). This file never enters the vault or any published path.

## The redact-output seam (design §7)

Published notes carry the **aggregate tally ONLY** — no per-text data, and no
`text_id`s on any published path. The opt-in `local_texts` map is the bridge:

- It is keyed by `text_id` (a stable, stripped-sha256 LOCAL join key), so
  `redact-output` (Phase 7) joins matched texts back on that id LOCALLY, replaces
  third-party names with initials, and writes the redacted exhibit to a LOCAL,
  non-vault file.
- `text_id` exists to JOIN locally, never to publish. A `text_id` (or any raw
  matched text) must never appear in a vault note. If a published artifact needs a
  per-text reference, that is a `redact-output` job on the local exhibit — not a
  field on the headline.
- `local_texts` is off by default. Request it only when building the local
  exhibit; the default `sweep` returns counts and never raw PII.

## Rigor guardrails (preserve across the sweep)

- **Officials are excluded from exposure.** A name attributed to an official
  (lexicon subset match or preceding rank/title) is accountability, not leaked
  PII. `person_official` is REPORTED in `categories` but never counts toward
  `exposure.strict` or `exposure.broad`. Build the lexicon from the structured
  searcher field so untitled officials are caught.
- **`person_unknown_role` is a LEAD, not a verdict.** It means "a PERSON the
  classifier did NOT identify as an official" — names not yet matched to the
  officials lexicon. Frame it as "names not identified as officials", never as a
  confirmed civilian-PII count. It lives in `broad`, never in the `strict`
  headline.
- **Leads vs the headline.** `possible_birthdate` (a bare `MM/DD/YYYY` is also an
  INCIDENT date) and `race_sex` (a 2-char demographic ratio is ambiguous with
  ordinary prose) are BROAD-ONLY leads — they fire as categories but stay out of
  `strict`. The explicit `dob_kw` label ("DOB on file") IS high-precision and
  counts toward `strict`; the bare date does not.
- **Distinct, then weight.** NER runs over DISTINCT texts (case PRESERVED — spaCy
  is case-sensitive; outer whitespace stripped) and every tally is weighted by the
  row counts those distinct texts cover. Never run the classifier per row; the
  weighted count equals a naive per-row scan at a fraction of the cost.
- **Exposure is a per-row union, not a sum of categories.** A row matching two
  categories counts once in `exposure`; `sum(category weighted)` will exceed
  `exposure.strict.weighted` and that is correct — do not add categories to get an
  exposure figure.
- **Presence, leads, and an honest empty.** Empty / all-blank input yields a
  `None` `efficiency_ratio` (never a fake `0`) and zeroed exposure; the result is
  JSON-serializable (native types only).
- **The model is `en_core_web_lg`** (~400 MB, ~1.5 s cold load, NER-only,
  CPU-only — no GPU/CUDA extras). PERSON is the only label used; agencies / places
  (GPE / ORG) are out of scope. The `len(span) > 2` floor drops 1–2 char PERSON
  false positives (a documented trade — it misses very short surnames).

## pii-sweep vs the recipe's `pii` check

`pii-sweep` is the **authoritative** PII tally: semantic NER (names, weighted by
count) plus the broader structured pattern set, emitting the publishable
`exposure.strict` headline and the `redact-output` seam. `recipe.check_pii`
(Phase 4) is a fast structured-regex **PRESENCE indicator** (a_number / ssn /
phone / email; no NER, no bare date) used inside the per-source pass, and it
explicitly defers the weighted semantic tally to this skill. Use the recipe's
check to flag that a source has structured PII at all; use `pii-sweep` to quantify
the exposure and feed redaction.

## Resources

- **`references/prior-art.md`** — Phase 5 research gate: verified spaCy 3.8.14 /
  `en_core_web_lg` facts (load time, NER labels, span over-extension), the
  candidate structured-PII patterns, and the `recipe.check_pii` decoupling.
- **`scripts/pii_sweep.py`** — the engine: `sweep`, `distinct_texts`, `text_id`,
  `DEFAULT_PII_PATTERNS` / `BROAD_ONLY_PATTERN_NAMES`, and the
  `PersonClassifier` seam / lazy `SpacyPersonClassifier` (detailed docstrings).
- **`dataset-analyze` skill** — the upstream prep (load → quality-gate → derive)
  that produces both the cleaned free-text series and the structured searcher
  column the officials lexicon is built from.
- **Librarian** — the shared notes layer the aggregate tally is published through
  (hub-and-spoke, vault-or-portable; counts only).
- **`redact-output`** (Phase 7) — the downstream consumer that joins `local_texts`
  on `text_id`, redacts names to initials, and writes the local exhibit.

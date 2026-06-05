---
title: Magpie Phase 8 -- investigate (verification gate + citation anchor) (design)
date: 2026-06-05
phase: 8
status: draft
design_review: PENDING
codex_thread_id: 019e95ea-d5d9-72f0-87db-e1bbd50a4c42
supersedes:
---

# Phase 8 design -- investigate (the WHY)

ASCII-only (SDD subagents content-filter-block on exotic glyphs). This doc is the
architecture + contracts. The HOW / TDD steps live in the Phase-8 plan (next
gate). The library facts this phase rests on were verified at the Phase-6 ingest
research gate (`skills/ingest/references/prior-art.md`, section 2.2-2.3: the
`save_as_json` DoclingDocument schema 1.10.0 and the `{page_no, bbox, charspan}`
provenance triple) and re-confirmed against the pinned `docling-core==2.78.1` in
the venv; a thin Phase-8 `references/prior-art.md` records the invented-format
rationale.

This phase treats LLM output as **unverified source material** (the ProPublica
posture, design section 7). The deliverable is the *discipline*, not an extractor.

## 0. Scope

`investigate` is the verification gate that sits between a trustworthy ingested
document and a published finding. Three deliverables (plan Tasks 8.1-8.3):

- **`scripts/citation.py`** (Task 8.1) -- the finding RECORD + the citation
  ANCHOR and its fallback-chain RESOLVER. PURE (stdlib `hashlib`/`json`/
  `dataclasses` only; **no docling import** -- it resolves over a plain
  `json.load` dict), deterministic (no clock/random/network; the timestamp is
  injected), golden-testable. Mirrors `ingest_gate`'s pure core.
- **`agents/extraction-verifier.md`** + **`agents/citation-checker.md`**
  (Task 8.2) -- two structurally-separate agent prompts (authored via
  `plugin-dev:agent-development`): a SEMANTIC adversarial re-read and a MECHANICAL
  anchor-integrity check.
- **`skills/investigate/SKILL.md`** (Task 8.3) -- the orchestration: extract ->
  adversarial re-check + citation-check -> a MANDATORY solo human gate
  (evidence-before-claim) -> redacted Librarian output. Encodes the section-7
  rigor guardrails.

`investigate` shares **no code** with the Track-A analysis modules. Its only
upstream contract is `ingest`'s `IngestResult` + the DoclingDocument JSON; its
only downstream contract is the Librarian findings output and the section-7
citation schema. It consumes `redaction-check` leads and `redact-output` at the
publish edge (both Phase 7).

## 1. The upstream seam -- and the refuse-on-`review` contract

`investigate` consumes one `ingest` `IngestResult`: `source_sha256` (the
`doc_id`), `docling_json_path`, `schema_name`/`schema_version`,
`trustworthy_for_extraction`, `doc_decision`, `per_page[]`.

**Hard refusal (safety-critical, the seam `ingest` already documents):** if
`trustworthy_for_extraction == false` (a `review` doc -- handwriting / garbled /
weak-signal pages dominate), `investigate` **refuses to auto-extract**. A `review`
doc is evidence-for-human-inspection, never an automated-extraction source. This
is the first thing the skill checks; there is no override flag in v1.

The anchor resolves over the DoclingDocument JSON's `texts[]` array. Each item
(verified serialization) carries `self_ref` (`"#/texts/{i}"`), `text` (the
**sanitized** surface -- the one canonical surface we anchor against), `orig`
(untreated; **never** anchored against), and
`prov: [{page_no, bbox:{l,t,r,b,coord_origin}, charspan:[start,end)}]`. Note
`prov[].charspan` spans the WHOLE item, so the magpie anchor computes its OWN
sub-offsets into a block's `.text`.

## 2. The citation anchor (the invented format)

This anchor is a magpie invention; the design (section 7) and plan (8.1) both
flag it for early validation on real ingested output (see section 7 below).

### 2.1 `CitationRecord` (design section 7 schema + the anchor fields it needs)

A dataclass; native-typed and JSON-able. Fields:

- `claim_text` -- the extractor's claim. **LOCAL-only raw** (may name a third
  party).
- `verbatim_quote` -- the exact supporting span, an **exact substring of the
  cited block's `.text`** (`[start, end)`). **LOCAL-only raw.**
- `doc_id` -- `ingest` `source_sha256` (artifact identity).
- `doc_schema_name` / `doc_schema_version` -- the SOURCE doc schema
  (`"DoclingDocument"` / `"1.10.0"`), so a later Docling schema bump is
  detectable.
- `page_no` -- 1-based.
- `block_index` (int) + `block_self_ref` (`"#/texts/{i}"`) -- which `texts[]`
  item; the two agree and are cross-checked.
- `char_start` / `char_end` -- **half-open `[start, end)`** offsets into the
  block's `.text` (Python-slice semantics; pinned, never fuzzy).
- `text_hash` -- **full** `sha256(verbatim_quote)` hex (exact span, no strip): a
  content-integrity + relocation hash. **Deliberately different** from
  `pii_sweep.text_id` (stripped + `[:16]`, a local *join* key) -- this is an
  *integrity* hash, untruncated and unstripped.
- `bbox` (`{l,t,r,b,coord_origin}`) + `prov_index` + `n_prov` -- the human card's
  geometry (see 2.3).
- `verifier_result` (`supported|contradicted|indeterminate`) +
  `verifier_confidence` -- ADVISORY (see section 4); `indeterminate` default.
- `checker_level` -- the mechanical anchor-resolution level (2.2).
- `extractor_model` / `prompt_version` -- provenance of the claim.
- `schema_name` (`"magpie-citation"`) + `schema_version` (`"1"`) -- this record's
  OWN namespaced schema identity (not a bare `version=1`).
- `timestamp` -- ISO-8601, **injected by the caller** (the pure core never calls
  the clock, so it stays deterministic/golden-testable).

### 2.2 The fallback chain resolver -- levels, each DEGRADING precision

`resolve_anchor(record, docling_json_dict) -> ResolvedAnchor{level, page_no,
block_index, char_start, char_end, matched_text, bbox, n_matches}`. Ordered:

1. **`exact`** -- `texts[block_index].text[char_start:char_end]` hashes to
   `text_hash`. Offsets intact; this also disambiguates duplicates (the offsets
   pick the right occurrence).
2. **`relocated`** -- exact failed (offsets shifted by an OCR re-run), AND
   `verbatim_quote` occurs **exactly once** across all blocks' `.text`. Resolve to
   that unique block + recomputed offsets; confirm `sha256`. This is the
   OCR-resilience path (the required round-trip test).
3. **`ambiguous`** -- exact failed AND `verbatim_quote` occurs **more than once**
   (across or within blocks). **Not a clean resolution** -- we never silently pick
   one. Record `n_matches`; the citation-checker surfaces it as mis-cited.
4. **`block`** -- exact + relocated failed (the characters themselves changed,
   e.g. OCR `rn`->`m`), BUT the stored `block_index` is in range and its
   `page_no` matches. Block-level localization only; offsets `None`. **Degraded.**
5. **`page`** -- only the `page_no` is still valid. **Degraded.**
6. **`unresolved`** -- none of the above.

**No fuzzy/edit-distance tier** -- it would fake confidence. The clean-citation
gate (section 4) passes ONLY at `exact` or unique `relocated`; `ambiguous` /
`block` / `page` / `unresolved` are degraded anchors flagged for the human, never
an auto-pass.

### 2.3 Multi-`prov` blocks -- charspan-containment, never naive `prov[0]`

A block's `prov` is a LIST (an item split across a page boundary has one prov
per fragment, each with its own bbox + charspan). The record stores `n_prov`
locally; the human card's `page_no`/`bbox` come from the prov entry whose
`charspan` **contains** the quoted `[char_start, char_end)` (`prov_index`). If no
single prov contains it (or the block is degraded), the card drops to page-level
geometry (`bbox = None`) and is flagged -- we never point a precise box at the
wrong fragment. For the common single-prov block, the containing prov is
`prov[0]` and `charspan` is `[0, len(text))`, so this reduces to the simple case.

## 3. Never-publish-raw -- three local-only leak surfaces

Mirrors the Phase-7 discipline (`redaction_check`'s `local_evidence` /
`publishable_view()`), extended to THREE surfaces:

1. The `CitationRecord` -- `verbatim_quote` and `claim_text` are raw.
2. The **verifier** output -- its reasoning + any quoted span.
3. The **checker** output -- `matched_text` + mismatch payloads.

All three stay on a LOCAL citations log (non-vault, like `redact_output`'s local
exhibit). `citation.py` exposes `public_anchor(record) -> dict` carrying ONLY the
non-raw anchor + status: `doc_id, page_no, block_index, block_self_ref,
text_hash, bbox, checker_level, verifier_result, schema_name/version`. It is NOT
the published finding -- it carries no `claim_text`.

The **published finding** is assembled by the SKILL, not by `citation.py`:
`redact-output(claim_text)` (uninvolved names -> initials, structured PII ->
typed placeholders) + `public_anchor(record)` + the verification status. So
`citation.py` never imports spaCy / `redact_output` and stays pure; redaction
happens at the skill's publish edge, where the officials / `keep_names` context
already lives.

## 4. The verifier + citation-checker (Task 8.2)

Two structurally-separate agents. Neither auto-accepts a claim.

### 4.1 `extraction-verifier` -- SEMANTIC, and HONESTLY DEGRADED

Re-reads the cited span (from `.text`) plus the claim, **blinded to the
extractor's reasoning** (only span + claim in), and emits
`supported|contradicted|indeterminate` + confidence + reasoning (LOCAL-only).
`indeterminate` is the conservative default (presence OR entailment in doubt ->
indeterminate). Presence check: is the quote actually in the source span?
Entailment check: does the span actually support the claim?

**Honest limit (design section 7, corrected at the brainstorm gate):** in Layer
0-1 this runs as the **same model** in a fresh context. Same-model, fresh-context,
blinded is **still single-model self-verification**, which section 7 explicitly
disallows (correlated errors; LLM-judge recall ~16%). Therefore this agent is an
**adversarial re-check helper -- advisory signal for the human, NOT the
spec-compliant independent verifier.** It NEVER gates autonomously. A truly
independent verifier (a different model / structural independence) is a documented
later-layer upgrade. In Layer 0-1 the **human gate is the only real verifier.**

### 4.2 `citation-checker` -- MECHANICAL anchor integrity

Drives `citation.resolve_anchor` over every claim and applies the clean-citation
gate. Flags: uncited claims; anchors that resolve only at `ambiguous` / `block` /
`page` / `unresolved` (degraded, not a pass); `matched_text != verbatim_quote`.
Deterministic (it wraps the pure resolver) -- no semantic judgment. It is the
mechanical complement to the semantic re-check.

## 5. The investigate skill orchestration (Task 8.3)

1. **Refuse non-trustworthy.** If `trustworthy_for_extraction == false`, stop
   (section 1). No override in v1.
2. **Extract (schema-constrained, LLM-prompted -- not a script, not an ML
   model).** The skill instructs the extractor to read the DoclingDocument
   `.text` and emit a **schema-constrained** list of
   `{claim_text, verbatim_quote, block_self_ref}` where each `verbatim_quote` is
   an **exact single-block substring** of that block's `.text`. No free-form
   extraction, no "close enough" quotes, no cross-block quotes (rejected in v1).
   `citation.build_anchor` stamps each (computing offsets + `text_hash` +
   page/bbox).
3. **Verify independently.** Per claim: the `citation-checker` (mechanical) +
   the `extraction-verifier` (semantic adversarial re-check, advisory). Both
   blinded to the extractor's chain-of-thought.
4. **Mandatory solo human gate -- evidence BEFORE claim.** A card that shows, in
   order: (a) the SOURCE SPAN first (the resolved `.text` span + page/bbox
   context) -- evidence before the claim, to counter automation bias (documented
   51% rubber-stamp rate); (b) THEN the AI `claim_text`; (c) THEN the advisory
   verifier verdict + the mechanical checker level. A degraded anchor or a
   `contradicted`/`indeterminate` verdict is surfaced prominently and must be
   consciously resolved. The human accepts / edits / rejects. **Solo
   single-reviewer sign-off is the only required gate** (this is mostly solo
   work); a two-reviewer sign-off is optional + logged, NEVER required. Only
   human-ACCEPTED claims proceed.
5. **Output (redacted).** Accepted findings route to Librarian as
   `redact-output(claim_text)` + `public_anchor` + status. The raw
   `CitationRecord` + verifier/checker reasoning stay on the LOCAL citations log.
   `redaction-check` leads on the source PDF can feed the investigation (a
   suspected bad redaction is a lead to investigate, not a verdict).

## 6. Rigor guardrails (section 7, encoded as explicit skill checks)

- **Walk back unsupported claims.** Entailment must hold; otherwise the verifier
  returns `indeterminate`/`contradicted` and the human must reject or revise. No
  claim ships on a span that does not support it.
- **Verify keyword matches (the ICE / "polICE" trap).** A claim that hinges on a
  keyword (e.g. "ICE") must pass `derive.keyword_mask` (the shared word-boundary
  guardrail) over the cited span -- a substring of `police` / `notice` /
  `service` is NOT a match. The skill applies `keyword_mask` to keyword-derived
  claims; `citation.py` itself stays keyword-free (the guard is a skill-level
  check).
- **`***` != blank (presence vs value).** A redaction sentinel means
  present-but-withheld, NOT a real value. A claim must not read a value off
  `***`; the verifier rejects "the value is X" when the cited span is a redaction
  sentinel.
- **Refuse out-of-scope claims.** A claim whose support requires a field /
  dimension absent from the source (e.g. racial disparity inferred from audit
  logs that carry no race field) is refused -- the data cannot support it.
- **Window-asked != retention-proven.** A requested FOIA date window is not proof
  of the retention period; a retention claim supported only by a request window
  is walked back.

## 7. Testing -- and the early real-corpus validation (brainstorm gate: NOT
synthetic-only)

Three tiers, escalating realism:

- **Tier 1 (offline, fast -- the bulk).** Hand-built ASCII DoclingDocument-shaped
  dict fixtures (the exact serialized shape: `texts[].{self_ref, text, prov[].
  {page_no,bbox,charspan}}`, `pages`, `schema_name`/`version`). Tests
  `build_anchor` + `resolve_anchor` at EVERY level (`exact`, unique `relocated`,
  `ambiguous`, `block`, `page`, `unresolved`), the half-open offsets, the
  multi-prov charspan-containment selection, `.text`-only anchoring, the
  `public_anchor` raw/public split, and the clean-citation gate (only `exact` /
  unique `relocated` pass). **The required round-trip test:** build an anchor over
  a fixture, simulate an OCR re-run that PREPENDS a header (shifts char offsets)
  and inserts an earlier block (shifts `block_index`), assert level-1 `exact`
  FAILS but level-2 `relocated` RESOLVES via the hash.
- **Tier 2 (docling-marked -- the "prototype + validate on real ingested output
  early" step the design demands).** A `@pytest.mark.docling` test ingests a real
  PDF through the ACTUAL Docling + RapidOCR pipeline (reusing the Phase-6
  fpdf2-native + Pillow-scan conftest fixtures), builds anchors over the
  NATIVE-ingest JSON, and resolves them against the OCR-ingest JSON of the SAME
  content -- exercising REAL cross-pass offset drift. Asserts clean spans survive
  (`exact`/`relocated`) and mangled spans degrade honestly (`block`/`page`), NOT a
  false `exact`. This validates the invented format against real Docling
  serialization + real OCR drift, with **no PII and no external-PDF licensing
  risk** (the source PDF is generated, but the Docling output + OCR noise are
  real -- and the source-PDF provenance is irrelevant to validating the anchor
  over Docling's output).
- **Tier 3 (Task 11.2, deferred).** The real Simpsonville ingested PDFs behind
  the local env var (never committed). Filed as an `autonomous-safe` follow-up so
  the anchor is re-validated against genuinely-messy real-world OCR before
  release. The genuinely-public-domain sample corpus is Task 11.1.

All Phase-8 fixtures are SYNTHETIC and ASCII-only.

## 8. Honest limits (documented, not hidden)

- **The semantic verifier is degraded** (same-model -> correlated errors); the
  **human gate is the only real verifier** in Layer 0-1. Zero-shot
  relation/claim extraction F1 is low (~25-40, section 7) -> the human gate is
  MANDATORY, never autonomous.
- **Degraded anchors are surfaced, never auto-passed.** `block` / `page` /
  `ambiguous` / `unresolved` resolutions are flagged for the human.
- **A truly independent (different-model) verifier is a later-layer upgrade**, as
  is genuinely-messy real-world-corpus validation (Tier 3).

## 9. Scope / YAGNI / deferred

- **In:** the citation record + resolver, the two agents, the skill + human gate,
  the encoded guardrails, Tier-1/Tier-2 tests.
- **Out (v1):** a multi-document corpus index (resolve over ONE doc JSON at a
  time, pinned by `doc_id`); two-reviewer workflow infra (solo is the only
  required path); a claim-extraction ML model (LLM-prompted, schema-constrained);
  any fuzzy/edit-distance resolution tier; cross-block quotes.
- **Follow-up (filed `autonomous-safe`):** validate the anchor against the real
  Simpsonville ingested PDFs behind the Task-11.2 env var.

## 10. Module placement + decoupling

- `scripts/citation.py` -- PURE (stdlib only; no docling/spaCy/pandas import;
  deterministic, timestamp injected). Resolves over a plain dict. Mirrors
  `ingest_gate`'s pure core. Imports none of its neighbors.
- `agents/extraction-verifier.md`, `agents/citation-checker.md` -- prompts
  (authored via `plugin-dev:agent-development`).
- `skills/investigate/SKILL.md` (+ a light `references/prior-art.md`) -- the
  orchestration + the human-gate contract + the encoded guardrails. The skill is
  the ONLY place that touches `redact-output` (publish edge) and `derive.
  keyword_mask` (the keyword guard); `citation.py` stays free of both.

## 11. Provenance

Brainstormed 2026-06-05 with Codex standing in as the critic partner (autonomous
mode). Codex's pushback reshaped four things: the verifier is an advisory
adversarial re-check, NOT a spec-compliant independent verifier (the human gate is
the only real verifier in Layer 0-1); real-Docling validation is required early
(Tier 2), not synthetic-only; multi-`prov` blocks select geometry by
charspan-containment, never naive `prov[0]`; and duplicate/ambiguous spans fail
clean resolution rather than being silently picked. Source of truth: design
section 7 + plan Tasks 8.1-8.3.

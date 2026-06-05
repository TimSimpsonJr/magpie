---
title: Magpie Phase 8 -- investigate (verification gate + citation anchor) (design)
date: 2026-06-05
phase: 8
status: approved
design_review: APPROVE (Codex, round 2, 2026-06-05)
codex_design_review_status: approved
codex_design_review_approved_hash: 27dda6cb7c1eadb16fb72cbfff03fd16b19b33b4b7a6c12c1cd917f69db5d11a
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

`scripts/citation.py` shares **no code** with the Track-A analysis modules. The
`investigate` **skill** deliberately reuses ONE public Track-A helper --
`derive.keyword_mask` (the shared word-boundary ICE/polICE guardrail, exactly as
`recipe.py` does) -- at its keyword-guard step (section 6); that single,
intentional reuse is the only Track-A coupling. The only upstream contract is
`ingest`'s `IngestResult` + the DoclingDocument JSON; the only downstream contract
is the Librarian findings output and the section-7 citation schema. The skill
consumes `redaction-check` leads and `redact-output` at the publish edge (both
Phase 7).

## 1. The upstream seam -- and the refuse-on-`review` contract

`investigate` consumes one `ingest` `IngestResult`: `source_sha256` (the
`doc_id`), `docling_json_path`, `schema_name`/`schema_version`,
`trustworthy_for_extraction`, `doc_decision`, `per_page[]`.

**Hard refusal (safety-critical, the seam `ingest` already documents):**
`investigate` keys on the **`trustworthy_for_extraction` boolean**, NOT on
`doc_decision`. `ingest` sets `trustworthy_for_extraction = not (review or
partial)` (`scripts/ingest.py:721`), so it is false for BOTH a `review` decision
(handwriting / garbled / weak-signal pages dominate) AND a `PARTIAL_SUCCESS`
conversion. `investigate` **refuses to auto-extract** from any non-trustworthy
result -- an implementer must check the boolean, never `doc_decision == review`
(which would let a flagged partial-success doc leak through). A non-trustworthy
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
  cited block's `.text`** (`[start, end)`), obeying the v1 quote contract (2.4).
  **LOCAL-only raw.**
- `context_prefix` / `context_suffix` -- a small fixed-width window of the block's
  `.text` immediately before / after the span (the W3C TextQuoteSelector
  prefix/suffix pattern). **LOCAL-only raw.** Used to disambiguate duplicate spans
  and to stop a short quote relocating into the interior of a larger token
  elsewhere (2.2).
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
2. **`relocated`** -- exact failed (offsets shifted by an OCR re-run). Search all
   blocks' `.text` for `verbatim_quote` as a **word-boundary-aligned** substring
   (edges on token boundaries, so a short quote cannot relocate into the
   *interior* of a larger token elsewhere), and confirm the candidate's
   surrounding text matches the stored `context_prefix` / `context_suffix`.
   Resolve ONLY if exactly ONE candidate survives both the boundary and context
   checks; recompute offsets and confirm `sha256`. This is the OCR-resilience path
   (the required round-trip test).
3. **`ambiguous`** -- exact failed AND **more than one** candidate survives the
   boundary + context checks (a genuinely repeated span the context window cannot
   disambiguate). **Not a clean resolution** -- we never silently pick one. Record
   `n_matches`; the citation-checker surfaces it as mis-cited.
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
`charspan` **contains** the quoted `[char_start, char_end)` (`prov_index`) -- its
`page_no` is the card's page, its `bbox` the card's box. **v1 requires the quoted
span to be contained within a SINGLE prov fragment:** if it straddles two prov
fragments (a block wrapping across a page boundary), `build_anchor` REJECTS it
(the citation-checker flags it), exactly like a cross-block quote (2.4). This
keeps `page_no` a faithful scalar -- a lone page number never misstates where
multi-fragment evidence lives. For the common single-prov block the containing
prov is `prov[0]` with `charspan == [0, len(text))`, so this reduces to the
simple case.

### 2.4 The v1 quote contract (what `build_anchor` accepts)

A valid v1 `verbatim_quote` is: **non-empty and not whitespace-only**; an **exact
substring of ONE block's `.text`** (never `.orig`, never spanning two blocks);
**contained within a single `prov` fragment** of that block (2.3); and
**word-boundary-aligned** at both edges. `build_anchor` REJECTS anything else
(empty/blank, cross-block, cross-prov-fragment, or a bare mid-token sub-span); the
extractor is instructed to honor it and the citation-checker enforces it. This is
the contract that makes `text_hash` stable and `relocated` safe -- without it a
short quote could relocate into the interior of a larger token (2.2).

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

The **published finding** is assembled by the SKILL, not by `citation.py`. The
skill runs `pii_sweep` over the cited source spans to collect the flagged
`local_texts`, then sanitizes the claim with **`redact_note(claim_text,
local_texts, keep_names=..., officials=...)`** -- the Phase-7 publish-safe path
for narrative built from KNOWN flagged texts (it replaces each known flagged
source-PII string the claim repeats: uninvolved names -> initials, structured PII
-> typed placeholders). The published finding = the redacted claim +
`public_anchor(record)` + verification status. So `citation.py` never imports
spaCy / `redact_output` and stays pure; redaction happens at the skill's publish
edge, where the officials / `keep_names` context already lives.

**Honest limit (respecting `redact_output`'s locked scope).** `redact_note` only
redacts KNOWN flagged texts -- it deliberately does NOT autonomously NER-scan
novel claim narrative (`scripts/redact_output.py:186-205,261-268`; the scope lock
exists to avoid false confidence on free text). So NOVEL uninvolved-third-party
PII an extractor writes into a claim that was NOT a flagged source text is NOT
auto-redacted. This is mitigated, not solved: (a) a skill GUARDRAIL instructs the
extractor to reference people by ROLE or already-public official names and never
introduce an uninvolved third party's name into a claim; (b) the HUMAN GATE is
where a reviewer rejects / edits any claim that names an uninvolved party (and an
edit re-runs verification, section 5). An autonomous novel-narrative PII scanner
is a documented later-layer item, not a v1 hand-wave.

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
   human-ACCEPTED claims proceed. **Editing invalidates verification:** if the
   human edits a claim's text or its quote, the skill re-stamps the anchor
   (`build_anchor`) and **re-runs the citation-checker + advisory verifier** on
   the edited claim before it can be accepted -- an edited claim never ships under
   a verdict that only applied to the pre-edit text.
5. **Output (redacted).** Accepted findings route to Librarian as the
   **`redact_note`-sanitized** claim (section 3) + `public_anchor` + status. The
   raw `CitationRecord` + verifier/checker reasoning stay on the LOCAL citations
   log. `redaction-check` leads on the source PDF can feed the investigation (a
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
- **Tier 2 (docling-marked, generated source).** A `@pytest.mark.docling` test
  ingests a generated PDF through the ACTUAL Docling + RapidOCR pipeline (reusing
  the Phase-6 fpdf2-native + Pillow-scan conftest fixtures), builds anchors over
  the NATIVE-ingest JSON, and resolves them against the OCR-ingest JSON of the
  SAME content -- exercising REAL cross-pass offset drift. Asserts clean spans
  survive (`exact`/`relocated`) and mangled spans degrade honestly
  (`block`/`page`), NOT a false `exact`. Validates the format against real Docling
  serialization + real OCR drift with no committed binary.
- **Tier 2b (real-world PDF, env-var-gated -- the "prototype + validate on real
  ingested output early" step section 7 / plan 8.1 demand).** Validate the anchor
  against a genuinely messy, real-world public record: the **City of Greenville
  RFP No. 21-3746 (LPR/Flock) FOIA response** (`Responsive records (1).pdf`, 96
  pages -- DocuSign-stamped pages, repeating "RFP No. ... -- Page N" footers,
  affidavit/forms, and OCR'd tabular pages: real item fragmentation). A
  `@pytest.mark.docling` test gated on a local env var (e.g.
  `MAGPIE_PHASE8_REAL_PDF`) ingests the pointed-at PDF, builds anchors over the
  real DoclingDocument JSON, resolves them (including across a re-ingest pass),
  and asserts the fallback chain behaves on real-world layout. **The PDF is used
  LOCALLY only and is NEVER committed** (it is a public SC-FOIA record, but the
  same env-var discipline as the Simpsonville corpus keeps the repo binary-free
  and PII-free; the test SKIPS when the env var is unset, so CI stays hermetic).
  This is run once during implementation and the empirical result recorded in the
  plan / PR (the early real-world validation the invented format requires).
- **Tier 3 (Task 11.2, deferred).** The real Simpsonville ingested PDFs behind a
  local env var (never committed). Filed as an `autonomous-safe` follow-up so the
  anchor is re-validated against the flagship corpus before release. The
  genuinely-redistributable public sample corpus is Task 11.1.

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
mode). Codex's brainstorm pushback reshaped four things: the verifier is an
advisory adversarial re-check, NOT a spec-compliant independent verifier (the
human gate is the only real verifier in Layer 0-1); real validation is required
early, not synthetic-only; multi-`prov` blocks select geometry by
charspan-containment, never naive `prov[0]`; and duplicate/ambiguous spans fail
clean resolution rather than being silently picked. The Codex **design-review**
round then folded seven more fixes: refuse on the `trustworthy_for_extraction`
boolean (not `doc_decision`, so `PARTIAL_SUCCESS` is caught); the v1 quote
contract (non-empty, single-block, single-prov-fragment, word-boundary-aligned)
plus stored `context_prefix`/`context_suffix` so `relocated` cannot slip into a
larger token's interior; a human edit re-runs verification (no stale verdict); the
publish edge uses `redact_note` with known flagged texts (the correct Phase-7 API)
plus an honest limit on novel-narrative PII; and the early real-world validation
runs against the Greenville RFP 21-3746 FOIA response (Tim's call: a real public
record, used locally / never committed). Source of truth: design section 7 + plan
Tasks 8.1-8.3.

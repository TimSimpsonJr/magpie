# Magpie — Jev integration: citation pre-screen + dataset tool (design)

**Status:** approved design (brainstorm 2026-09-24). One spec, one PR.
**Scope:** Magpie only. Researcher gets a separate design later; Librarian is unchanged (it never sees source text).

Jev is TypeSafe's System One decision model, reached through OpenRouter. It answers typed questions (yes/no → probability, choice → label + distribution, score → value) over a shared text state, batched, in roughly 0.3–1 s per request for fractions of a cent. It does not generate text, do arithmetic, or reason in multiple steps.

- **Part A — citation pre-screen:** a cheap, independent first pass in `investigate`'s verification gate so confidently supported claims skip the per-claim `extraction-verifier` subagent.
- **Part B — `jev_ask` dataset tool:** a general, batched Jev question tool for dataset work, plus guidance on how and when to use it.

## 1. Shared foundation

### 1.1 Opt-in and privacy (both parts)
- **Off by default.** Enabled only when `MAGPIE_JEV=1` **and** `OPENROUTER_API_KEY` is set. With either missing, Magpie behaves exactly as today and no network call is made.
- **What is sent:** Part A — per claim, the `claim_text`, `verbatim_quote` and resolved source span. Part B — the configured text fields of each record plus the question/criteria. Never whole documents, file names, paths or document IDs.
- **Local-only guards (applied to every outbound string before sending):**
  - **Secrets:** API keys, tokens, bearer values, PEM blocks, `password=`/`secret:`-style assignments.
  - **PII:** Magpie's existing `scripts/pii_sweep.py` `DEFAULT_PII_PATTERNS`. If that module cannot be imported, treat every item as a PII hit (send nothing) rather than sending unscreened text.
  - A guarded item is never sent. Part A routes it to `verify`; Part B reports it as `skipped: pii` / `skipped: secret`.
- **README "Your data & privacy":** documents the opt-in, the endpoint (OpenRouter → TypeSafe), exactly what is sent, and the local guards. The "nothing uploaded" statement is qualified: true by default; the optional Jev features send the listed text when explicitly enabled. README edits must keep the `tests/test_onramp_docs.py` invariants (no "docker"; both persona guides routed; `detect_tier` mentioned).
- **`magpie:doctor`:** one line, `jev: on` or `jev: off (<reason>)` — reasons `MAGPIE_JEV not set`, `no OPENROUTER_API_KEY`.

### 1.2 `scripts/jev_client.py` (vendored, stdlib only)
- `ask(state, questions) -> JevResult{answers, model, usage, latency_ms}` or raises `JevUnavailable(reason)`.
- `POST https://openrouter.ai/api/v1/systemone`, model `~typesafe/jev-latest`, header `Authorization: Bearer $OPENROUTER_API_KEY`.
- 8 s timeout; one retry on 429/529. Cloudflare "Attention Required" 403 → `waf_blocked`; HTTP 400 containing `max_tokens_exceeded` → `too_large`; other HTTP errors → `http_<code>`.
- Strict validation: every requested id present; `noul` answers a finite real (not bool) in `[0, 1]`; `choice` answers name a requested option with probabilities in `[0, 1]`; `score` answers numeric. Anything else → `bad_shape`.
- Windows: items are packed until the state estimate (chars/4) reaches ≈14k tokens (Jev's state limit is ≈32.7k real tokens; dense text tokenizes worse than chars/4). An item larger than a window alone is not sent (`too_large`). On `too_large`/`waf_blocked` for a window, split it in half once; items still failing get that reason.

### 1.3 Model-change safeguard
`data/jev_state.json` (untracked, created on first use) records the model ID that last passed the Part A live eval. If a response reports a different model, Part A routes **every** claim to `verify` (reason `model_changed`) until the eval is re-run and passes; Part B continues but writes `model_changed: true` in its meta file and prints a warning.

## 2. Part A — citation pre-screen

### 2.1 Flow
`scripts/jev_prescreen.py` (CLI `python scripts/jev_prescreen.py <claims.json>`, also importable):
- **Input:** JSON list of `{claim_id, claim_text, verbatim_quote, span, clean_citation}` — `span` is what `citation.resolve_anchor` resolves the claim to; `clean_citation` is `citation.is_clean_citation(resolved)`.
- **Output (stdout JSON):** `{enabled, model, claims: {claim_id: {presence, entailment, route, reason, spot_check}}}`, `route ∈ {"skip","verify"}`.
- `skills/investigate/SKILL.md` §2: after anchoring, if Jev is on, run the pre-screen on the batch, dispatch `citation-checker` for **every** claim, and `extraction-verifier` for every claim with `route == "verify"` or `spot_check == true`.

### 2.2 Questions
State `{"claims": {"K01": {"claim": …, "quote": …, "span": …}}}`; two `noul` questions per claim with contrastive criteria:
- `presence_Knn` — "Does the span for Knn contain the quoted text or an equivalent passage?" true: the quote or a faithful rendering appears; false: absent, or only similar wording about something else.
- `entail_Knn` — "Does the span for Knn support the claim as stated?" true: the span states or directly implies the claim, including its names, numbers and dates; false: silent, contradicts it, or supports only a weaker or different claim.

### 2.3 Routing — `skip` requires ALL of:
1. Jev enabled, no model change, the claim was sent (not guarded, not too large) and answered;
2. `clean_citation` is true (a degraded anchor may point at the wrong text);
3. **numeric/date gate (deterministic):** every number and date token in `claim_text` also appears in the span (normalized: thousands separators, common date formats); and the claim contains no computed-value cue (`total`, `sum`, `average`, `percent`/`%`, `per`, `ratio`, `more than`, `less than`, `increase`, `decrease`, `rate`, and the multiplier/proportion cues `doubled`, `tripled`, `quadrupled`, `twice`, `thrice`, `N-fold`, `half`/`halved`, `majority`, `minority`, `fraction`, `most of`, `nearly all`);
4. `presence ≥ 0.85` **and** `entailment ≥ 0.85` (starting values, tuned on the live eval).

Anything else → `verify`, with the first failing reason (`jev_off`, `model_changed`, `pii`, `secret`, `too_large`, `waf_blocked`, `jev_error`, `degraded_anchor`, `numeric_mismatch`, `computed_value`, `low_score`). **The pre-screen can only remove verifier calls; it never rejects or accepts a claim.**

### 2.4 Spot-checks
A deterministic ~10% of `skip` claims (hash of `claim_id` + run seed) are marked `spot_check: true` and also get `extraction-verifier`. Any disagreement (verifier not `supported`) is logged to `data/jev_spotcheck.jsonl` (untracked) with claim id, scores, model and verdict, and surfaced in the run summary.

### 2.5 Audit trail and labeling
- Each claim's record gains `prescreen: {presence, entailment, route, reason, model, spot_check}` and is carried into evidence exports (`archive-evidence`).
- The human gate labels skipped claims **"Jev pre-screen: supported — not independently verified (presence 0.94, entailment 0.91)"**, never "verified".
- Run summary: `N claims pre-screened · M skipped · K sent to extraction-verifier · S spot-checked · D disagreements`.

## 3. Part B — `jev_ask` dataset tool

### 3.1 CLI
`python scripts/jev_ask.py --input records.{jsonl,csv} --id-field ID --text-fields F1,F2 --type noul|choice|score --question "…" [--criteria-true … --criteria-false …] [--options options.json] [--levels levels.json] --out results.jsonl [--sample N] [--seed S]`
- `noul` requires both criteria; `choice` requires `--options` (JSON map label → description, 2–255 entries); `score` requires `--levels` (JSON list of 2–10 level descriptions).
- Records are packed into windows (state `{"records": {"R0001": {…text fields…}}}`), one question per record.
- **Output (a new file; source data is never modified):** one JSON line per input record: `{id, answer | skipped}` — `noul` → `{p}`; `choice` → `{label, probabilities, confidence}`; `score` → `{value, probabilities, confidence}`; or `skipped: pii|secret|too_large|waf_blocked|jev_error|empty`.
- **`results.meta.json`:** question, type, criteria/options/levels, text fields, model, timestamp, counts per outcome, `model_changed`, and the input file's sha256.
- `--sample N` also writes `results.sample.jsonl` with N deterministic-random answered records for spot-checking.
- With Jev off: exit non-zero with the doctor reason; nothing is sent.

### 3.2 Guidance: `skills/dataset-analyze/references/jev-guide.md`
Linked from `dataset-analyze`, `entity-crossref` and `investigate`. Covers:
- **Use it for:** bounded questions over many records — triage/relevance screening, fixed-label classification, flagging rows for human review, "same entity?" pre-screens before `entity-crossref`, pre-filtering before an expensive LLM step.
- **Don't use it for:** arithmetic or aggregates (use pandas/SQL), multi-step reasoning, generating text, extracting values, or as the sole basis for any published finding.
- **Writing questions:** one judgment per question; contrastive true/false criteria with examples; choice labels mutually exclusive with a "none/other" option.
- **Reading answers:** three zones (act / check / escalate); thresholds set from a spot-check of `--sample` output, not assumed; probabilities are relative confidence, not ground truth.
- **Privacy:** what is sent; PII/secret rows stay local and are reported as skipped; opt-in required.
- **Provenance:** keep the meta file with any derived dataset; cite it in methodology notes.

## 4. Testing

- **Offline (default pytest run, no network, mocked transport):**
  - `jev_client`: request shape, retry, error mapping, strict validation (negative, >1, NaN, bool, string, missing id, unknown choice label), window packing and single split.
  - Guards: secret and PII hits never appear in a serialized request (canary test over a full request); PII-module import failure sends nothing.
  - Part A: every routing condition in §2.3 including the numeric/date gate and computed-value cues; fail-to-verify on every error; spot-check determinism and disagreement logging; model-change routing; audit fields present.
  - Part B: each question type; CSV and JSONL input; skipped reasons; meta file contents; `--sample` determinism; Jev-off exit.
  - Skill-body smoke tests: `investigate` §2 documents the pre-screen dispatch rule; the three skills link the guide; doctor on/off lines; README invariants still pass.
- **Live (pytest marker `jev_live`, skipped unless Jev is on):**
  - Part A eval: ~20 recorded claim/span pairs from fixtures — supported, paraphrased-supported, wrong number/date, wrong entity, span-silent, contradicted. **Gate: no unsupported or contradicted claim routes to `skip`.** Reports the skip rate. Passing records the model in `data/jev_state.json`.
  - Part B smoke: 20 fixture records with known labels → accuracy ≥ a stated floor.

## 5. Reviews
One Codex pass on this spec + plan, one on the implementation. After round 1 only critical findings block — a secret/PII leak, any path that routes an unsupported claim to `skip`, or data leaving the machine while opted out. Everything else becomes a labeled follow-up issue.

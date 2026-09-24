# Magpie — Jev citation pre-screen (design)

**Status:** approved design (brainstorm 2026-09-24).
**Scope:** Magpie only. Researcher gets a separate design later; Librarian is unchanged (it never sees source text).

## 1. Goal

Cut the per-claim LLM cost of `investigate`'s verification gate without weakening it. Today every extracted claim gets two subagents (`citation-checker`, mechanical; `extraction-verifier`, semantic) and then a human gate. Jev (TypeSafe's System One decision model, via OpenRouter) can answer the semantic question — "does this span support this claim?" — as a calibrated yes/no in milliseconds for fractions of a cent, batched across many claims.

Jev is a **pre-screen**: confidently supported claims skip `extraction-verifier`; everything else still gets it. The human gate is unchanged. Jev never rejects a claim and never auto-accepts one.

**Non-goals:** replacing `citation-checker` (it is a pure mechanical resolver); changing the human gate; Researcher/Librarian.

## 2. Opt-in and privacy

- **Off by default.** Enabled only when `MAGPIE_JEV=1` **and** `OPENROUTER_API_KEY` is set. With either missing, `investigate` behaves exactly as today.
- **What is sent:** per claim, the `claim_text`, the `verbatim_quote`, and the resolved source span. Never whole documents, never file names or document IDs.
- **Secret/PII guard:** a span or claim matching a secret pattern (API keys, tokens, bearer values, PEM blocks, `password=`-style assignments) is not sent; that claim routes to `verify`.
- **README "Your data & privacy"** documents the opt-in, the endpoint (OpenRouter → TypeSafe), and exactly what is sent. The existing "nothing uploaded" statement is qualified: true by default; the optional pre-screen sends claim/quote/span text when explicitly enabled.
- README edits must keep the `tests/test_onramp_docs.py` invariants (no "docker"; both persona guides routed; `detect_tier` mentioned).
- **`magpie:doctor`** reports `jev pre-screen: on` or `off (<reason>)` — reasons: `MAGPIE_JEV not set`, `no OPENROUTER_API_KEY`.

## 3. Components

- `scripts/jev_client.py` — vendored stdlib client (no dependency on anything outside the repo).
  - `ask(state, questions) -> JevResult | raises JevUnavailable(reason)`.
  - `POST https://openrouter.ai/api/v1/systemone`, model `~typesafe/jev-latest`, header `Authorization: Bearer $OPENROUTER_API_KEY`.
  - 8 s timeout; one retry on 429/529; Cloudflare "Attention Required" 403 → `JevUnavailable("waf_blocked")`; HTTP 400 containing `max_tokens_exceeded` → `JevUnavailable("too_large")`.
  - Strict answer validation: every requested id present, `type == "noul"`, `noul` a finite real number (not bool) in `[0, 1]`; else `JevUnavailable("bad_shape")`.
- `scripts/jev_prescreen.py` — the pre-screen, runnable as a CLI (`python scripts/jev_prescreen.py <claims.json>`) and importable.
  - Input: a JSON list of `{claim_id, claim_text, verbatim_quote, span}` (the span is what `citation.py` resolves the claim's anchor to).
  - Output (stdout JSON): `{enabled, model, claims: {claim_id: {presence, entailment, route, reason}}}` where `route ∈ {"skip", "verify"}`.
- `skills/investigate/SKILL.md` §2 — after anchoring: if the pre-screen is enabled, write the claims batch to a temp JSON, run `jev_prescreen.py`, dispatch `citation-checker` for **every** claim and `extraction-verifier` **only** for `route == "verify"`.
- `skills/doctor/` — add the on/off line (§2).

## 4. Scoring and routing

- **Windows:** claims packed into windows of ≈14k tokens (chars/4 estimate; Jev's state limit is ≈32.7k real tokens and code/legal text tokenizes denser than chars/4). A claim whose own span exceeds the window is not sent (`verify`, reason `too_large`).
- **State:** `{"claims": {"K01": {"claim": …, "quote": …, "span": …}, …}}`.
- **Two `noul` questions per claim**, with contrastive criteria:
  - `presence_Knn` — "Does the span for Knn contain the quoted text or an equivalent passage?" true: the quote or a faithful rendering appears; false: absent, or only similar wording about something else.
  - `entail_Knn` — "Does the span for Knn support the claim as stated?" true: the span states or directly implies the claim, including its numbers, names and dates; false: the span is silent, contradicts it, or supports only a weaker/different claim.
- **Route:** `skip` iff `presence ≥ 0.85` **and** `entailment ≥ 0.85` (starting values; tuned on the eval, §6). Otherwise `verify`.
- **Fail toward verify:** any `JevUnavailable`, timeout, malformed answer, secret-guard hit, or oversized span → `verify` with the reason. The pre-screen can only remove verifier calls, never weaken the gate.

## 5. Human gate labeling

For a skipped claim the gate shows `Jev pre-screen: supported (presence 0.94, entailment 0.91)` in place of the extraction-verifier verdict, plus the unchanged citation-checker result. Claims that went through the verifier show its verdict as today. The run summary reports `N claims pre-screened, M skipped, K sent to extraction-verifier`.

## 6. Testing

- **Offline (default pytest run, no network):**
  - `jev_client`: request shape, retry, WAF/too-large/bad-shape mapping, strict validation (negative, >1, NaN, bool, string, missing id).
  - `jev_prescreen`: opt-in gate (off → `enabled: false`, no network call); window packing; routing thresholds and edges; fail-to-verify on every error class; secret guard withholds and routes to verify; CLI I/O.
  - `investigate` skill body: smoke test that §2 documents the pre-screen dispatch rule (matches the existing skill-body tests).
  - `doctor`: on/off lines.
- **Live eval (pytest marker `jev_live`, skipped unless `MAGPIE_JEV=1` and a key are set):** ~20 recorded claim/span pairs built from existing fixtures, stratified: supported, paraphrased-supported, wrong number/date, wrong entity, span-silent, contradicted. **Gate: no unsupported or contradicted claim may route to `skip`.** Reports the skip rate (the savings).

## 7. Reviews

One Codex pass on this spec + the plan, one on the implementation. After round 1 only critical findings (secret leak, a path that routes an unsupported claim to skip, data leaving the machine while opted out) block; everything else becomes a follow-up issue.

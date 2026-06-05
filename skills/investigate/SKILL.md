---
name: investigate
description: This skill should be used when the user wants to investigate a trustworthy ingested document, extract findings or claims from it, turn a converted PDF into cited findings, fact-check or verify AI-extracted claims against the source, or publish anchored findings to the Librarian. It runs the verification gate that turns a trustworthy ingested document into human-gated, citation-anchored, redacted findings, names the mandatory solo human verification gate, and anchors every claim with the citation engine.
version: 0.1.0
---

# investigate

investigate is the verification gate that sits between a trustworthy ingested
document and a published finding. It turns the document into human-gated,
citation-anchored, redacted findings by treating every LLM extraction as
unverified source material -- the ProPublica posture. The skill does not trust
its own extractor: it stamps each claim with an exact citation anchor (the engine
scripts/citation.py build_anchor), re-checks each claim two independent ways, and
then routes every claim through a mandatory solo human gate before anything is
published. The semantic re-check is advisory only; in Layer 0-1 the human gate is
the only real verifier.

Call one engine: scripts/citation.py is the pure citation-anchor record +
resolver. The skill orchestrates extract -> verify -> human gate -> redacted
Librarian output around it. The skill is the only place that touches the publish
edge (redact_note) and the keyword guard (derive.keyword_mask); citation.py stays
free of both. No .mcp.json ships with this skill.

## 0. Refuse a non-trustworthy document (safety-critical, checked first)

The only upstream input is one ingest IngestResult plus its DoclingDocument JSON.
Before doing anything else, check the boolean trustworthy_for_extraction on that
result.

- ingest sets trustworthy_for_extraction as not (review or partial)
  (scripts/ingest.py). It is false for BOTH a review decision (handwriting,
  garbled, or weak-signal pages dominate) AND a PARTIAL_SUCCESS conversion.
- If trustworthy_for_extraction is false, STOP. A non-trustworthy document is
  evidence for human inspection, never an automated-extraction source. There is
  no override flag in v1.

Key on the boolean, never on the decision string. Do NOT key on
doc_decision == review: that would let a flagged partial-success document leak
through. The boolean is the one correct seam.

## 1. Extract (LLM, schema-constrained -- not a script, not an ML model)

The extractor reads the DoclingDocument .text and emits a schema-constrained list
of {claim_text, verbatim_quote, block_self_ref}. The contract on each quote:

- verbatim_quote is an EXACT substring of ONE block's .text (the sanitized
  surface; never .orig, never spanning two blocks).
- The block must be single-prov (n_prov == 1). A multi-prov block is rejected.
- The quote is word-boundary-aligned at both edges (no bare mid-token sub-span).

No free-form extraction, no close-enough quotes, no cross-block quotes (rejected
in v1). scripts/citation.py build_anchor stamps each claim: it locates the quote
in .text, computes its own half-open [char_start, char_end) offsets, takes
page_no and bbox from prov[0], and records the full sha256 text_hash. build_anchor
raises if the quote is empty or blank, absent, not unique in the block, on a
multi-prov block, or not word-boundary-aligned.

## 2. Verify independently (two blinded agents, neither auto-accepts)

Dispatch both agents per claim. Both are blinded to the extractor's
chain-of-thought (its reasoning/justification) -- NOT to the quote. Each agent
receives a DIFFERENT, purpose-built input:

- citation-checker (MECHANICAL): receives each claim's CitationRecord (the
  anchor) plus the DoclingDocument JSON, and drives scripts/citation.py
  resolve_anchor + is_clean_citation over every claim. It reports the anchor level
  per claim and flags uncited claims, anchors resolving only at ambiguous / block
  / page / unresolved (degraded -- NOT a pass), and any matched_text that does not
  equal the verbatim_quote. Deterministic; no semantic judgment.
- extraction-verifier (SEMANTIC, advisory): receives {claim_text, verbatim_quote,
  source span} -- blinded to the extractor's reasoning, but it DOES get the
  verbatim_quote (it needs it for the presence check) and the cited span re-read
  independently from .text (for entailment). It returns supported / contradicted /
  indeterminate + confidence + reasoning. indeterminate is the conservative
  default (presence OR entailment in doubt -> indeterminate).

Honest limit: in Layer 0-1 the semantic verifier runs as the SAME model in a
fresh context, so its errors are correlated with the extractor's. It is an
advisory adversarial re-check, NOT an independent verifier. It never gates on its
own.

## 3. Mandatory solo human gate -- evidence BEFORE claim

Show every claim to a human on a card that presents, in this order:

1. The SOURCE SPAN first -- the resolved .text span plus its page and bbox
   context. Evidence is shown before the claim to counter automation bias (a
   documented rubber-stamp rate around 51 percent).
2. THEN the AI claim_text.
3. THEN the advisory verifier verdict plus the mechanical checker level.

Surface a degraded anchor (ambiguous / block / page / unresolved) or a
contradicted / indeterminate verdict PROMINENTLY; it must be consciously
resolved, never auto-passed. The human accepts, edits, or rejects each claim.
Only human-accepted claims proceed.

Solo single-reviewer sign-off is the ONLY required gate (this is mostly solo
work). A two-reviewer sign-off is optional and logged, NEVER required.

Editing invalidates verification: if the human edits a claim's text or its quote,
re-stamp the anchor with build_anchor and re-run BOTH the citation-checker and
the advisory verifier on the edited claim before it can be accepted. An edited
claim never ships under a stale verdict that only applied to the pre-edit text.

## 4. Output (redacted) -- publish the anchor, keep the raw local

For each accepted finding:

1. Run pii_sweep over the cited source spans to collect the flagged local_texts.
2. Sanitize the claim with redact_note(claim_text, local_texts, keep_names=...,
   officials=...) -- the Phase-7 publish-safe path for narrative built from KNOWN
   flagged source texts (uninvolved names become initials; structured PII becomes
   typed placeholders).
3. Publish to the Librarian: the redacted claim + the citation public_anchor +
   the verification status.

The raw CitationRecord (verbatim_quote, claim_text, context windows) and the
verifier / checker reasoning stay on a LOCAL citations log, never in the vault.
public_anchor carries only the non-raw anchor + status (doc_id, page_no,
block_index, block_self_ref, text_hash, bbox, checker_level, verifier_result,
schema name/version); it carries no claim_text and is not itself the published
finding.

Honest limit on redaction: redact_note only redacts KNOWN flagged texts; it does
NOT autonomously NER-scan novel claim narrative. So a novel uninvolved-third-party
name an extractor writes into a claim that was not a flagged source text is NOT
auto-redacted. Mitigation: (a) a guardrail instructs the extractor to reference
people by role or by already-public official names and never introduce an
uninvolved third party's name; (b) the human gate is where a reviewer rejects or
edits any claim that names an uninvolved party (and an edit re-runs verification).

## 5. Rigor guardrails (encoded as explicit checks)

- Walk back unsupported claims. Entailment must hold; otherwise the verifier
  returns indeterminate or contradicted and the human must reject or revise. No
  claim ships on a span that does not support it.
- Verify keyword matches (the ICE / polICE trap). A claim that hinges on a
  keyword (for example ICE) must pass derive.keyword_mask -- the shared
  word-boundary guardrail -- over the cited span. A substring of police, notice,
  or service is NOT a match. The skill applies keyword_mask to keyword-derived
  claims; citation.py itself stays keyword-free.
- A redaction sentinel is NOT a value (presence vs value). A sentinel such as
  three asterisks means present-but-withheld, not a real value. A claim must not
  read a value off a sentinel; the verifier rejects the value is X when the cited
  span is a redaction sentinel.
- Refuse out-of-scope claims. A claim whose support requires a field or dimension
  absent from the source (for example racial disparity inferred from audit logs
  that carry no race field) is refused. The data cannot support it.
- A requested window is not retention-proven. A requested FOIA date window is not
  proof of the retention period; a retention claim supported only by a request
  window is walked back.

## 6. Honest limits (documented, not hidden)

- The semantic verifier is degraded (same model -> correlated errors). The human
  gate is the only real verifier in Layer 0-1. Zero-shot relation / claim
  extraction F1 is low, so the human gate is MANDATORY, never autonomous.
- Degraded anchors are surfaced, never auto-passed. block / page / ambiguous /
  unresolved resolutions are flagged for the human. is_clean_citation passes ONLY
  at exact or unique relocated.
- A truly independent (different-model) verifier is a later-layer upgrade, as is
  genuinely-messy real-world-corpus validation.

## Engine and downstream

- Engine module: scripts/citation.py (pure stdlib; build_anchor, resolve_anchor,
  is_clean_citation, public_anchor). It imports no docling, spaCy, or pandas and
  resolves over a plain dict.
- Agents: agents/citation-checker.md (mechanical), agents/extraction-verifier.md
  (semantic, advisory).
- Publish edge: pii_sweep + redact_note (Phase 7); keyword guard:
  derive.keyword_mask. Downstream: the Librarian findings output.
- references/prior-art.md records the invented-format rationale (the verified
  docling-core 2.78.1 shape, the W3C TextQuoteSelector relocation context, and the
  early Greenville-RFP charspan finding).
- No .mcp.json ships.

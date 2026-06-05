---
name: redact-output
description: This skill should be used when the user asks to "redact PII before publishing", "redact third-party names to initials in a findings note", "redact a note before it goes in the vault", "mask suspect / person-of-interest / minor names that landed in the reason fields", "keep the full un-redacted exhibit local / out of the vault", or otherwise wants to redact UNINVOLVED third-party PII (names to initials, structured PII to typed placeholders) in PUBLISHED artifacts while keeping the full exhibit on a LOCAL non-vault path. It consumes pii-sweep's opt-in local_texts. It does NOT find bad redactions in a PDF; that is redaction-check.
version: 0.1.0
---

# redact-output

Redact UNINVOLVED third-party PII for PUBLISHED artifacts, while the FULL
un-redacted exhibit is written to a LOCAL non-vault path. This is the OUTPUT side
of Phase 7; it consumes Phase-5 `pii_sweep`'s opt-in `local_texts`.

## The redaction POLICY (Tim's call -- INVOLVED vs UNINVOLVED)

The line is involved vs uninvolved, NOT official vs non-official:

- **KEEP named:** officials (rank/title prefix OR an officials-lexicon match) AND
  investigator-designated INVOLVED subjects -- supplied via a `keep_names`
  allowlist (parallel to pii_sweep's `official_names`).
- **REDACT to initials:** a flagged PERSON name that is NEITHER official NOR
  involved is an uninvolved third party (the Simpsonville pattern: suspect / POI /
  minor names dumped into the reason fields).
- **SAFE DEFAULT:** with no `keep_names`, every non-official flagged name is treated
  as uninvolved and redacted -- Simpsonville works out of the box; a project that
  is tracing involved non-official subjects (e.g. financial dealings) supplies them
  in `keep_names` so they stay named.
- **ALWAYS mask** structured PII (ssn, dob, phone, email, alien#, driver-lic) to a
  TYPED placeholder regardless of the name policy.

## The four entry points (design 2.0)

```python
from scripts.redact_output import (
    redact_text, redact_note, redact_local_texts, write_local_exhibit,
)
```

1. `redact_text(text, *, keep_names=(), officials=()) -> str` -- redact ONE flagged
   reason-field text (uninvolved names -> initials, structured PII -> typed
   placeholder). PUBLISH-safe; carries no `text_id`.
2. `redact_local_texts(local_texts, *, keep_names=()) -> {text_id: redacted}` --
   a LOCAL-ONLY map (feeds the exhibit; NEVER a published surface).
3. `redact_note(note_text, local_texts, *, keep_names=()) -> str` -- the PUBLISH-safe
   note sanitizer: replaces each KNOWN flagged text in the note with its
   `redact_text` form, leaving the analyst's surrounding narrative untouched. The
   Task-7.2 "redact a findings note" path.
4. `write_local_exhibit(local_texts, exhibit_dir, *, vault_roots=()) -> Path` --
   write the FULL un-redacted exhibit CSV under `exhibit_dir`, the ONLY surface that
   carries `text_id` + raw text. Vault-guarded.

## The pii-sweep seam

Run `pii_sweep.sweep(reason_series, official_names=..., collect_local_texts=True)`
first; route its opt-in `local_texts` (`{text_id: {text, count, categories}}`) here.
Publish the AGGREGATE tally via Librarian (counts only); hand `local_texts` to
redact-output for the redacted published view and the local exhibit.

## Rigor guardrails (preserve)

- **Never publish raw.** Only `redact_text` and `redact_note` produce published
  strings, and NEITHER carries a `text_id` or a raw matched text. The `text_id`
  <-> raw mapping lives ONLY in `redact_local_texts`'s map and the exhibit CSV,
  both LOCAL. A `text_id` (sha256 of the raw text) or a raw matched text NEVER
  crosses a published path (design 7).
- **Vault-guarded exhibit.** `write_local_exhibit` resolves `exhibit_dir` AND each
  `vault_roots` entry via `Path.resolve()` (collapsing symlinks and `..`) and
  RAISES if the exhibit path is at/under any vault root -- the full un-redacted
  exhibit must never land where it could be published or synced.
- **Redact short names too.** Unlike pii_sweep's counting (which skips 1-2 char
  PERSON ents as a documented under-count), redaction considers EVERY PERSON ent --
  a missed name is a leak, so a 2-char uninvolved name is still initialed.
- **Overlap + order.** A PERSON span containing a structured-PII span (e.g. a name
  next to a date) redacts cleanly: the longer span wins, the contained span is
  dropped, replacements apply right-to-left (no offset corruption, no
  double-redaction). `redact_note` sorts known texts longest-first via the same
  `_apply_spans` helper (the no-raw-suffix guarantee).
- **Scoped, not a narrative scanner.** redact-output only masks pii_sweep-flagged
  reason-field texts (and known-text occurrences in a note). It does NOT NER-scan
  the analyst's own narrative -- contextual names the analyst writes stay untouched.
- **Pure-core / spaCy-at-the-edge.** Importing `scripts.redact_output` is ML-free;
  spaCy loads only when the default `SpacyPersonSpans` runs. The module imports only
  PURE helpers from `pii_sweep` (`person_role_in_span`, `_norm_name_tokens`,
  `DEFAULT_PII_PATTERNS`, `text_id`) -- a shared rule, not duplicated logic.

## Resources

- **`scripts/redact_output.py`** -- the engine: `redact_text` / `redact_local_texts`
  / `redact_note` / `write_local_exhibit`, the `_apply_spans` overlap+right-to-left
  helper, the typed-placeholder map, and the lazy `SpacyPersonSpans` edge.
- **`scripts/pii_sweep.py`** -- the upstream producer of `local_texts` and the home
  of the shared `person_role_in_span` role classifier.
- **`redaction-check`** -- the INPUT-side sibling (finds bad redactions in a PDF).
- **Librarian** -- the shared notes layer the redacted published view is routed
  through (the aggregate tally + the redacted note; never the raw exhibit).

---
name: redaction-check
description: This skill should be used when the user asks to "check a PDF for bad redactions", "find improper / failed redactions", "did this FOIA response leak text under the black boxes", "is there still-extractable text under the redaction boxes", "scan a PDF for un-redacted metadata / embedded files / form values", "check my own output before publishing for extractable PII", or otherwise wants to find bad redactions in a PDF as flag-for-a-human LEADS (received mode) or to verify our own artifact before release (pre-publish mode). It does NOT modify the PDF; for redacting third-party PII in published findings use redact-output.
version: 0.1.0
---

# redaction-check

Find BAD REDACTIONS in a PDF and emit each as a FLAG-FOR-A-HUMAN LEAD -- NEVER an
"improper redaction" verdict. A layered set of independent checks runs over one
PDF and returns a `RedactionReport`. This is the INPUT side of Phase 7 (the output
side is `redact-output`).

The verified library facts (the x-ray `inspect()` contract, the pikepdf /
pdfminer APIs, the AGPL/PyMuPDF licensing call, the coordinate trap) live in
`references/prior-art.md` -- consult it before changing a check or a dependency.

## The one entrypoint

```python
from scripts.redaction_check import check_redactions

report = check_redactions(pdf_path, mode="received")   # or mode="pre-publish"
publishable = report.publishable_view()                # safe to route to Librarian
```

`mode`:
- **`received`** -- inspect a FOIA response WE GOT for the AGENCY's bad redactions.
  Findings are investigative LEADS (recover/quantify what they failed to hide; feed
  `request-the-gap`). `safe_to_publish` is `None` (no pass/fail).
- **`pre-publish`** -- inspect OUR OWN output before release. A finding is a STOP
  signal; severities come from a pinned map and the report carries
  `safe_to_publish: bool`.

## The checks (each a LEAD)

1. **box_over_text** -- Free Law `x-ray` (lazy -> PyMuPDF): a rectangle drawn over
   still-extractable text. The recovered under-box text is LOCAL-only.
2. **text_layer** -- pdfminer.six: extractable text that CO-OCCURS (page-level)
   with another redaction signal. NOT a standalone "text exists" alarm.
3. **metadata** -- pikepdf docinfo + XMP: leaked author / producer / title / dates.
4. **incremental_save** -- raw bytes: `%%EOF` paired with `startxref` > 1 (a prior
   revision may hold pre-redaction content). A lead, not proof.
5. **unapplied_redact** -- pikepdf: a `/Subtype /Redact` annot MARKED but never
   APPLIED (underlying content still present).
6. **embedded_files** -- pikepdf: attachments + `/AF` + `/FileAttachment` (may
   carry un-redacted source). Name + size only; never extract contents.
7. **acroform_values** -- pikepdf: form field `/V` values behind a flat-looking page.
8. **annotation_text** -- pikepdf: comment-annot `/Contents` (a reviewer note).

## Rigor guardrails (preserve across the checks)

- **Leads, never verdicts.** Every finding is a flag for a human. A clean report
  is NEVER read as "fully redacted" -- the always-emitted `cannot_catch` honesty
  footer lists the failure classes with no reliable FOSS auto-detector
  (glyph-position / off-page text, pixelation / blur, cross-version reconstruction,
  proportional-font side-channels, semantic reconstruction, OCG layers).
- **Never publish raw.** A raw recovered/leaked STRING (under-box text, metadata
  values, AcroForm values, annotation contents, an embedded filename) lives ONLY
  in a finding's `local_evidence`. `detail` carries publishable facts only (counts,
  field NAMES, page, bbox+origin). `report.publishable_view()` DROPS every
  `local_evidence` AND asserts no raw string leaked into a published field --
  route ONLY `publishable_view()` to Librarian; keep the full `to_dict()` LOCAL.
- **Fail-closed (pre-publish).** `safe_to_publish` is True only when every check
  ran AND no finding is `high`. ANY unavailable check forces `safe_to_publish =
  False` ("cannot certify") -- a check that did not run never certifies the absence
  of what it checks for.
- **Degrade, don't crash.** x-ray is imported LAZILY inside `box_over_text`; a
  missing x-ray or a malformed PDF degrades THAT check to `checks_unavailable` (a
  flag for humans), never a crash and never a false "clean". One failing check
  never sinks the others.
- **Page-level co-occurrence only.** x-ray, pdfminer, and pikepdf bboxes use
  DIFFERENT coordinate origins; never correlate them geometrically (design 1.4).
  Co-occurrence between checks is by page number.
- **Synthetic fixtures only.** All tests build synthetic PDFs in `tmp_path`
  (pikepdf / fpdf2 / crafted bytes); the offline tier never imports PyMuPDF. The
  real corpus is wired only at Task 11.2 behind an env var, never committed.

## Downstream + relationships

- **`ingest`** (Phase 6) preserved live-text-under-redaction-boxes specifically so
  `box_over_text` can reason about it -- the same PDFs feed both.
- **`request-the-gap`** (Layer 2) consumes received-mode leads to draft follow-up
  requests.
- **`redact-output`** is the OUTPUT-side sibling: redaction-check FINDS leaks;
  redact-output REDACTS third-party PII in our own published artifacts.

`redaction_check.py` shares NO code with the Track-A analysis modules or `ingest`
(design 5): it re-implements its small streamed-sha256 inline rather than importing
`scripts.ingest`.

## Resources

- **`references/prior-art.md`** -- the Phase-7 research gate: the verified x-ray /
  pdfminer / pikepdf APIs, the AGPL(PyMuPDF)-does-not-infect-MIT call, the
  coordinate trap, and the honest limits.
- **`scripts/redaction_check.py`** -- the engine: `check_redactions` orchestrator,
  the 8 `check_*` functions, the `RedactionFinding` / `RedactionReport` schema with
  `publishable_view()`, and the `CheckUnavailable` degrade sentinel (detailed
  docstrings).

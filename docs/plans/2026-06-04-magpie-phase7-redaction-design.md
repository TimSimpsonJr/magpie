---
title: Magpie Phase 7 -- redaction-check + redact-output (design)
date: 2026-06-04
phase: 7
status: approved
design_review: APPROVE (Codex, round 2, 2026-06-05)
codex_thread_id: 019e95ea-d5d9-72f0-87db-e1bbd50a4c42
supersedes:
---

# Phase 7 design -- redaction-check + redact-output (the WHY)

ASCII-only (SDD subagents content-filter-block on exotic glyphs). The verified
library facts live in `skills/redaction-check/references/prior-art.md` (the
Codex-approved research gate); this doc is the architecture + contracts. The
HOW / TDD steps live in the Phase-7 plan (next gate).

## 0. Scope

Two shared-spine skills, both publish-critical:

- **`redaction-check`** (INPUT side) -- `scripts/redaction_check.py`: a layered
  set of independent checks that find BAD REDACTIONS in a PDF and emit each as a
  FLAG-FOR-A-HUMAN LEAD, never an "improper redaction" verdict. Dual `--mode`
  (`received` / `pre-publish`).
- **`redact-output`** (OUTPUT side) -- `scripts/redact_output.py`: redacts
  UNINVOLVED third-party PII (names -> initials; structured PII -> typed
  placeholders) in PUBLISHED artifacts, while writing the FULL un-redacted
  exhibit to a LOCAL non-vault path. Consumes Phase-5 `pii_sweep`'s opt-in
  `local_texts`.

Both follow the suite's PURE-CORE / ENGINE-AT-THE-EDGE split (like `pii_sweep`
and `ingest`): the byte/metadata/annotation logic is stdlib+pikepdf (golden-
testable offline); x-ray (-> PyMuPDF) and spaCy are lazy edges. Both share NO
code with the Track-A analysis modules; the only contracts are the `pii_sweep`
`local_texts` DATA shape and the eventual Librarian findings output.

## 1. redaction-check -- architecture

### 1.1 The checks (each a LEAD, never a verdict)

One entrypoint runs all available checks over a PDF and returns a
`RedactionReport`. Checks (verified in the research gate):

1. **box_over_text** (x-ray, lazy edge -> PyMuPDF): rectangles/highlights drawn
   over still-extractable text. `xray.inspect()` -> `{page: [{bbox, text}]}`.
   The recovered `text` is LOCAL-ONLY (never published).
2. **text_layer** (pdfminer.six): per-page extractable-text presence + char
   count. NOT a standalone alarm (every normal PDF has text). It becomes a
   FINDING only on page-level CO-OCCURRENCE with another redaction signal
   (a box_over_text hit, a /Redact annot, or an image-only/scanned-looking page
   that nonetheless carries a substantial hidden text layer). Page-level, never
   cross-engine bbox correlation (the coordinate trap, 1.4).
3. **metadata** (pikepdf): `docinfo` + XMP (`open_metadata()`) author / producer
   / title / creator / dates. Leaked names, internal filenames, or software are
   a LEAD. Read-only.
4. **incremental_save** (stdlib bytes): `raw.count(b"%%EOF")` paired with
   `raw.count(b"startxref")`; > 1 => prior revision(s) may carry pre-"redaction"
   content. A LEAD (legitimate incremental saves exist: signatures, form fill),
   never proof.
5. **unapplied_redact** (pikepdf): page `/Annots` with `/Subtype /Redact` =
   content marked for redaction but never applied (underlying text still
   present).
6. **embedded_files** (pikepdf): `pdf.attachments` + the `/Names/EmbeddedFiles`
   name tree + `/AF` associated files + `/FileAttachment` annots. Enumerate
   name/size only; NEVER auto-extract contents. A LEAD.
7. **acroform_values** (pikepdf): `Root/AcroForm/Fields` `/V` values -- form
   fields can hold un-redacted data behind a flattened-looking page. A LEAD.
8. **annotation_text** (pikepdf): annotation `/Contents` on comment-type annots
   (`/Text`, `/FreeText`, `/Popup`) -- reviewer notes / leaked text. A LEAD.

Checks 1-6 are the design-mandated core; 7-8 are the cheap high-value additions
the research-gate Codex review surfaced. **OCG (optional-content / layer)
analysis is DEFERRED** with an explicit disclaimer in the honesty footer (more
complex; a known gap, flagged for humans).

### 1.2 Dual mode + severity

`mode` parameter, two values:

- **`received`**: inspecting a FOIA response WE GOT. Findings are the AGENCY's
  bad redactions -- investigative LEADS: recover/quantify what they failed to
  hide and feed `request-the-gap` follow-ups. No pass/fail; severity reflects
  investigative interest. `safe_to_publish` is N/A (None).
- **`pre-publish`**: inspecting OUR OWN output before release. A finding is a
  STOP signal -- did WE leave third-party PII extractable? Severity is elevated;
  the report carries `safe_to_publish: bool` = False if any finding at/above a
  high threshold. The SKILL refuses to publish until cleared.

**FAIL-CLOSED (design-review fix, cluster publish-gate):** in `pre-publish` mode
`safe_to_publish` is True ONLY when every check ran AND no finding meets the high
threshold. If ANY check is `checks_unavailable` (x-ray absent, a check raised),
`safe_to_publish` is **False** with a warning ("cannot certify: <check> did not
run") -- an un-run check NEVER yields a publish-safe verdict. The gate fails
closed, never open.

Same check engines both ways; mode sets framing, default severity, and the
`safe_to_publish` disposition (only computed in `pre-publish`).

### 1.3 Output schema (leads, publishable-vs-local)

```python
@dataclass(frozen=True)
class RedactionFinding:
    check: str            # "box_over_text" | "metadata" | ...
    severity: str         # "info" | "low" | "medium" | "high"
    page: int | None      # 1-based; None for doc-level (metadata, incr_save)
    summary: str          # human lead, PUBLISHABLE, e.g. "1 box-over-text region on page 2"
    detail: dict          # PUBLISHABLE FACTS ONLY: counts, field NAMES, page, {bbox, origin}.
                          # MUST NOT contain any raw leaked STRING -- no metadata VALUES, no
                          # AcroForm /V values, no annotation /Contents, no under-box text.
    local_evidence: dict | None = None  # LOCAL-ONLY (design-review fix): EVERY raw leaked
                          # string this finding exposed (under-box text, metadata values, form
                          # values, comment text), keyed by sub-source. publishable_view() DROPS
                          # this whole field. The ONLY place raw recovered strings live.

@dataclass
class RedactionReport:
    source_path: str
    source_sha256: str
    mode: str
    checks_run: list[str]
    checks_unavailable: list[str]     # e.g. ["box_over_text: x-ray not installed"]
    findings: list[RedactionFinding]
    n_findings: int
    safe_to_publish: bool | None      # pre-publish only; None in received mode
    warnings: list[str]
    cannot_catch: list[str]           # the honesty footer (1.5)
    def to_dict(self) -> dict: ...    # JSON-able; publishable_view() strips recovered_text
```

`publishable_view()` returns the report with EVERY finding's `local_evidence`
dropped AND asserts no finding's `detail` carries a raw string (a defensive
schema check, not just convention) -- so no raw recovered/leaked string EVER
crosses a published path. Librarian publishes summary + counts + locations +
severity + check names + the cannot_catch footer; all raw strings stay in the
LOCAL report object's `local_evidence` only (same discipline as `pii_sweep`
counts-publish / texts-stay-local). Every check that can surface a raw string
(box_over_text, metadata, acroform_values, annotation_text, embedded_files names)
puts that string in `local_evidence`, never in `detail`.

### 1.4 Coordinate-space discipline (research-gate Codex finding)

x-ray/PyMuPDF bboxes (top-left origin), pdfminer line bboxes (bottom-left), and
pikepdf annot rects do NOT share a page coordinate convention. redaction-check
does NO cross-engine geometric overlap math. Co-occurrence between checks is
PAGE-LEVEL only (same page number). Any bbox that appears in `detail` is reported
in its native engine space with the origin named; it is evidence for a human, not
an input to an automated overlap test. (Same coordinate-origin caution as
`ingest`'s `_normalize_prov_origins`.)

### 1.5 What it CANNOT catch (honesty footer -- always emitted)

`cannot_catch` lists the redaction-failure classes with no reliable FOSS
auto-detector, so a clean report is NEVER read as "fully redacted": glyph-position
/ off-page / white-on-white text; pixelation / blur / mosaic (reversible raster);
cross-version reconstruction beyond flagging that revisions exist; proportional-
font / kerning side-channels; semantic reconstruction; and OCG/optional-content
layers (deferred this phase). Leads-not-verdicts, identical in spirit to
`ingest`'s flag-don't-fake and `foia-exemptions`' flags-as-leads.

### 1.6 Pure-core / lazy-edge split + degrade-don't-crash

`scripts/redaction_check.py` is pure-ish: the byte (incremental_save) and pikepdf
checks (metadata, unapplied_redact, embedded_files, acroform_values,
annotation_text) need no ML and are golden-testable offline. x-ray is imported
LAZILY inside the box_over_text check; if x-ray is absent OR `inspect()` raises on
a malformed PDF, that ONE check degrades to `checks_unavailable` / a
flag-for-humans warning -- never a crash, and never a false "clean". A
per-check try/except keeps one failing check from sinking the others (each failure
-> a warning + that check listed unavailable). **In `pre-publish` mode an
unavailable check is fail-closed (1.2): it forces `safe_to_publish=False`, because
a check that did not run cannot certify the absence of what it checks for.**

## 2. redact-output -- architecture

### 2.0 Entry points (I/O)

Four functions. The PUBLISH-PATH CONTRACT (design-review fix) is enforced by
which functions can emit a published string and which carry `text_id`/raw text:

1. `redact_text(text, *, keep_names=(), officials=(), person_classifier=None,
   patterns=DEFAULT_PII_PATTERNS) -> str` -- the CORE per-text redactor: a lazy
   PERSON-NER + regex pass over ONE FLAGGED reason-field string that initials
   uninvolved names and masks structured PII (2.1-2.3). SCOPE (locked, prior-art
   RESOLVED): applied to `pii_sweep`-flagged reason-field texts, NOT run as an
   autonomous scanner over arbitrary analyst narrative. Returns a redacted string
   with NO `text_id`. Pure-testable with a fake classifier.
2. `redact_local_texts(local_texts, *, keep_names=(), ...) -> dict[text_id, str]`
   -- a LOCAL-ONLY helper that pairs each `pii_sweep` `text_id` with its
   `redact_text` output. This text_id->redacted map is consumed ONLY by (4) (the
   local exhibit); it is NEVER returned to or used as a published surface. (The
   `text_id` is itself sha256(raw)[:16] -- treated as local, never published.)
3. `redact_note(note_text, local_texts, *, keep_names=(), ...) -> str` -- the
   PUBLISH-safe note sanitizer (the Task-7.2 "a findings note with PII is redacted
   to initials" path): finds each KNOWN flagged raw text from `local_texts` that
   occurs in `note_text` and replaces it IN PLACE with its `redact_text` form,
   leaving the analyst's surrounding narrative untouched. Emits NO `text_id` and
   no un-redacted flagged text. This is the ONLY redact-output function that
   produces a PUBLISHED multi-sentence string.
4. `write_local_exhibit(local_texts, exhibit_dir, *, vault_roots=(),
   redacted=False) -> Path` -- writes the exhibit CSV (FULL un-redacted, or the
   redacted view if `redacted=True`) under `exhibit_dir` AFTER the vault-root
   validation (2.4). This LOCAL CSV is the ONLY surface that carries `text_id` +
   raw text. Returns the written path.

CONTRACT: the only PUBLISHED outputs are (1) a redacted string and (3) a redacted
note -- neither carries a `text_id` or a raw matched text. The `text_id` <-> raw
mapping lives ONLY in (2)'s local map and (4)'s local CSV. A test asserts neither
(1) nor (3)'s output contains any `text_id` or any original flagged substring.

### 2.1 The redaction POLICY (Tim's call -- involved vs uninvolved)

The line is INVOLVED vs UNINVOLVED, not official vs non-official:

- **KEEP named:** (a) officials (accountability) AND (b) INVOLVED subjects the
  investigator designates -- supplied via a `keep_names` allowlist, parallel to
  `pii_sweep`'s `official_names`. (Officials are detected as in pii_sweep:
  rank/title prefix OR token-subset match against an officials lexicon; involved
  subjects are an additional keep lexicon.)
- **REDACT -> initials:** a PERSON name that is NEITHER official NOR involved =
  an uninvolved third party (the Simpsonville pattern: suspect/POI/minor names in
  reason fields).
- **SAFE DEFAULT:** with no `keep_names` supplied, every non-official flagged
  name is treated as uninvolved and redacted. Simpsonville works out of the box;
  a financial-tracing project OPTS its involved subjects into `keep_names`.
- **ALWAYS mask** structured PII (ssn, dob_kw, alien_num, driver_lic, phone,
  email) regardless of name policy -- "PII that should already have been
  redacted".

### 2.2 Span resolution (research-gate Codex finding)

`pii_sweep.local_texts` is `{text_id: {text, count, categories}}` -- no per-name
spans, so it cannot selectively redact a MIXED text (one reason field naming both
an official to keep and an uninvolved party to redact). Mechanism:

- redact-output does its OWN lazy-spaCy PERSON pass over each `local_texts` text
  (it has the full `text`), getting PERSON ent spans, then redacts each span that
  is neither official nor involved.
- To avoid logic drift with pii_sweep, extract the official/involved decision into
  a SHARED PURE helper `person_role_in_span(span_text, preceding_text, *,
  officials, involved=()) -> "official"|"involved"|"uninvolved"` (lives in
  `pii_sweep`, ML-free, importable). `pii_sweep.SpacyPersonClassifier` refactors
  to delegate to it (behavior-preserving; guarded by the existing Phase-5 tests),
  and redact-output calls the same helper. Structured-PII spans are located by
  re-running the SHARED `DEFAULT_PII_PATTERNS` over the text. redact-output thus
  imports only PURE helpers from pii_sweep (`text_id`, `DEFAULT_PII_PATTERNS`,
  `_norm_name_tokens`/`OFFICIAL_TITLES` via the shared helper); importing
  pii_sweep stays ML-free by design, so no spaCy is pulled until redact-output's
  own lazy pass runs.

This keeps the `pii_sweep` DATA contract (local_texts shape) UNCHANGED; the only
Phase-5 touch is extracting a pure helper the existing classifier already
implements inline.

**Per-consumer short-name policy (design-review fix, cluster role-helper-drift).**
The shared `person_role_in_span` helper decides ROLE only (official/involved/
uninvolved); the spaCy ent-filtering policy DIFFERS by consumer and is NOT shared:
- `pii_sweep` (COUNTING) keeps its `len(ent.text.strip()) <= 2` skip -- an
  acceptable, documented UNDER-count of 1-2 char names (Li/Ng).
- `redact_output` (REDACTING) does NOT apply that skip: for redaction, a missed
  name is a LEAK (an uninvolved/minor 2-char name would otherwise stay visible),
  so it considers EVERY PERSON ent and redacts any that is not official/involved.
The shared helper takes `span_text` + `preceding_token_texts` (the up-to-2
normalized preceding tokens) + `officials` + `involved` + `titles`; it does the
token-subset match and the title-prefix check EXACTLY as `_is_official` does
today. The **drift test pins Phase-5 behavior precisely**: it re-runs the existing
pii_sweep classifier cases through the refactored `SpacyPersonClassifier` and
asserts identical PersonFlags (lexicon subset match, `<=2`-token title lookback,
AND the `len<=2` ent-skip all preserved) -- the refactor is behavior-preserving or
the test fails.

### 2.3 names -> initials + typed PII placeholders

- A redacted PERSON span -> initials: first alphabetic char of each
  whitespace-split token, upper-cased, dot-joined ("John Q Public" -> "J.Q.P.";
  "Madonna" -> "M."). Hyphen/apostrophe tokens keep their first char only
  ("Anne-Marie" -> "A."). Deterministic; documented edge cases in tests.
- Structured PII -> a TYPED placeholder preserving the kind: ssn -> `[SSN]`,
  dob_kw -> `[DOB]`, phone -> `[PHONE]`, email -> `[EMAIL]`, alien_num ->
  `[A-NUMBER]`, driver_lic -> `[DL]`. (Typed, not a blanket `[REDACTED]`, so the
  published artifact still conveys WHAT kind of PII was present.)

**Span application order + overlap (design-review fix, cluster span-application).**
`redact_text` collects ALL replacement spans (PERSON-name spans to initialize +
regex PII spans to mask) as `(start, end, replacement)` against the original
string, then applies them **right-to-left (descending start offset)** so earlier
offsets never shift. Overlap rule: if two spans overlap, the LONGER span wins and
the contained span is dropped (a PII match inside a PERSON span, or vice versa, is
covered by the outer replacement -- never double-redacted); equal-length ties
resolve PERSON-before-PII deterministically. Tests pin an overlapping fixture
(e.g. a name adjacent to / containing a date).

### 2.4 Surfaces + the never-publish-raw invariant

`redact_output` produces two surfaces from `local_texts`:

- **Published artifact** (redacted): the output of `redact_text` (2.0 #1) or
  `redact_note` (2.0 #3) -- uninvolved names initialized, structured PII masked.
  Carries NO `text_id` and NO raw matched text. Routed through Librarian or
  returned for the caller to publish.
- **Local exhibit** (FULL, un-redacted): the original matched texts + counts +
  categories written to a CSV at a caller-supplied `exhibit_dir`. redact-output
  VALIDATES `exhibit_dir` is OUTSIDE every configured vault root (a `vault_roots`
  param): it resolves BOTH `exhibit_dir` and each vault root to a real absolute
  path (`Path.resolve()` -- collapsing symlinks and `..`) and RAISES if the
  resolved exhibit path is at or under any resolved vault root (a fail-closed
  guard -- the full exhibit must never land where it could be published/synced).

Invariant (design 7): a `text_id` or a raw matched `text` NEVER crosses a
published path. The published surface is redacted; the un-redacted surface is
local-only and vault-validated.

## 3. Wiring + skills

Both are PURE-SCRIPT (no `.mcp.json`), like `ingest`. Two SKILL.md files:

- `skills/redaction-check/SKILL.md`: orchestrates `redaction_check.py`; documents
  the leads-not-verdicts contract, the dual mode, the cannot_catch honesty,
  publishable-vs-local, the lazy x-ray edge / degrade-don't-crash, and the
  ingest->redaction-check seam (ingest PRESERVED live-text-under-boxes for this).
- `skills/redact-output/SKILL.md`: orchestrates `redact_output.py`; documents the
  involved-vs-uninvolved policy + keep_names, the pii_sweep `local_texts` join,
  the never-publish-raw invariant, the vault-validated local exhibit, and the
  span-resolution reuse of the shared pure helper.

## 4. Testing (synthetic, ASCII-only)

Mirror the `docling`/`spacy` marker pattern with a new **`xray` pytest marker**
for the box_over_text e2e (loads PyMuPDF). Two tiers:

- **Offline/pure tier** (no x-ray, no spaCy): byte-level incremental_save; pikepdf
  metadata / unapplied_redact / embedded_files / acroform_values / annotation_text
  against synthetic fixtures built in `tmp_path` (fpdf2 / pikepdf / fitz); the
  redact-output initials + typed-placeholder + keep_names policy + exhibit-path
  vault guard, golden-tested with a FAKE person classifier (no 400MB model, like
  pii_sweep's fake-classifier tests).
- **`xray`-marked tier**: real x-ray over a synthetic bad-redaction PDF (fpdf2
  text + solid black rect) -> asserts the page/bbox/text contract + the clean
  control returns no finding.
- A **drift test** pins the shared `person_role_in_span` extraction: it asserts
  the refactored `SpacyPersonClassifier` returns IDENTICAL PersonFlags to the
  pre-refactor logic across the existing Phase-5 cases (lexicon subset match,
  `<=2`-token title lookback, and the `len<=2` ent-skip) -- all existing pii_sweep
  tests must stay green.
- A **publish-path leak test** (the safety-critical one): asserts that
  `publishable_view()` drops every `local_evidence` and that no finding's `detail`
  contains a raw string; and that `redact_text` / `redact_note` outputs contain no
  `text_id` and no original flagged substring. A redact_output test also confirms
  a SHORT uninvolved name (2-char) IS redacted (the role-helper-drift guard) and a
  PERSON/PII overlap fixture redacts cleanly (the span-application guard).
- A **fail-closed test**: in `pre-publish` mode with x-ray forced unavailable,
  `safe_to_publish` is False (never True) with the cannot-certify warning.
- A **vault-guard test**: `write_local_exhibit` RAISES when `exhibit_dir` resolves
  (via realpath, after symlink/`..` resolution) inside any `vault_roots` entry.

All fixtures SYNTHETIC + ASCII-only; the real corpus is wired only at Task 11.2
behind an env var, never committed.

## 5. Decoupling + relationships

- redaction_check + redact_output share NO code with stats/load_table/derive/
  recipe/ingest. redact_output imports only PURE helpers from pii_sweep
  (`text_id`, `DEFAULT_PII_PATTERNS`, the shared `person_role_in_span`); importing
  pii_sweep stays ML-free.
- ingest (Phase 6) -> redaction-check: both operate on the same PDFs; ingest
  deliberately PRESERVED live-text-under-redaction-boxes so redaction-check can
  reason about it.
- pii_sweep (Phase 5) -> redact-output: the opt-in `local_texts` DATA contract
  (join on `text_id`), unchanged.
- redaction-check (received mode) -> request-the-gap (Layer 2): findings feed
  follow-up requests. Soft, design-level only this phase.

## 6. Decisions locked / deferred

- LOCKED (Tim): x-ray is a normal pinned dep; AGPL (PyMuPDF) does not infect
  Magpie's MIT license under pip-install/no-vendor; NEVER vendor or ship a bundled
  binary embedding PyMuPDF. (prior-art 5.)
- LOCKED (Tim): redaction POLICY is involved-vs-uninvolved via `keep_names`;
  safe-default redacts non-official flagged names; officials + involved subjects
  stay named; structured PII always masked. (prior-art 10 RESOLVED.)
- DEFERRED (disclaimed in cannot_catch): OCG/optional-content layer analysis;
  glyph-position / pixelation / cross-version reconstruction (no FOSS detector).
- DECIDED here (for design-review): the output schema (1.3), page-level
  co-occurrence (1.4), the span-resolution shared-helper mechanism (2.2), the
  extra surfaces in-scope (acroform_values, annotation_text, /AF) vs deferred
  (OCG), and typed PII placeholders (2.3).

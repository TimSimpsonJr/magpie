# Phase 7 -- redaction-check + redact-output Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> to implement this plan task-by-task (autonomous mode; Codex impl-review gate
> after).

**Goal:** Build `scripts/redaction_check.py` (find bad redactions in a PDF as
leads-not-verdicts) and `scripts/redact_output.py` (redact uninvolved third-party
PII names->initials + structured PII for published artifacts; full exhibit kept
local), plus their two SKILL.md files.

**Architecture:** Pure-core / engine-at-the-edge (like `pii_sweep`/`ingest`):
stdlib + pikepdf + pdfminer.six checks are golden-testable offline; x-ray (lazy ->
PyMuPDF) and spaCy are lazy edges. redact-output reuses pii_sweep's PURE helpers
plus a newly-extracted shared `person_role_in_span`. Design (APPROVED) is
`docs/plans/2026-06-04-magpie-phase7-redaction-design.md`; verified library facts
are `skills/redaction-check/references/prior-art.md`.

**Tech Stack:** Python 3.12, x-ray 0.3.6 (-> PyMuPDF 1.24.14), pdfminer.six,
pikepdf, spaCy en_core_web_lg (reused from Phase 5), fpdf2 (test fixtures),
pytest. Run tests with `mise run test` (NEVER bare python; fallback
`& .venv\Scripts\python.exe -m pytest`).

**RULES (carry into every task):**
- **ASCII-ONLY** in every test, fixture, and source file (SDD subagents
  content-filter-block on non-ASCII).
- **Leads, never verdicts.** Every redaction-check finding is a flag for a human.
- **Never-publish-raw invariant.** Raw recovered/leaked strings live only in
  `local_evidence` / the local exhibit; `publishable_view()` and the redact-output
  published surfaces carry no raw text and no `text_id`.
- **Fail-closed.** pre-publish `safe_to_publish` is False if any check is
  unavailable.
- Synthetic fixtures only; no real corpus. Offline tier must NOT import PyMuPDF
  (build /Redact-annot, AcroForm, annotation, embedded, incremental-save fixtures
  with pikepdf / fpdf2 / crafted bytes -- x-ray inspection is the only `xray`-marked
  part).
- TDD: failing test -> run (confirm fail) -> minimal impl -> run (pass) -> commit.

---

## Task 0: Scaffolding (marker + dep pin)

**Files:**
- Modify: `pyproject.toml` (add the `xray` marker)
- Modify: `requirements-dev.txt` (pin `x-ray==0.3.6`)

**Step 1:** In `pyproject.toml`, under `[tool.pytest.ini_options] markers`, add
alongside the existing `spacy` / `docling` markers:
```
"xray: tests that import x-ray / PyMuPDF (box-over-text detection); select with -k xray",
```

**Step 2:** In `requirements-dev.txt`, after the Phase-6 block, add:
```
# Phase 7 (redaction-check): Free Law x-ray (BSD-2) box-over-live-text detector.
# Pulls PyMuPDF (fitz) 1.24.14 (AGPL-3.0) as a DECLARED pip dep -- never vendored
# / never bundled-as-binary, so Magpie's MIT license is not infected (prior-art
# section 5). PyMuPDF does not depend on numpy/pandas (pins untouched).
x-ray==0.3.6
```

**Step 3:** Verify the venv already satisfies it (installed at the research gate):
Run: `& .venv\Scripts\python.exe -c "import xray, fitz; print(xray.__version__)"`
Expected: `0.3.6`

**Step 4: Commit**
```
git add pyproject.toml requirements-dev.txt
git commit -m "Phase 7.0: xray pytest marker + x-ray==0.3.6 dep pin"
```

---

## Task 1: Shared `person_role_in_span` helper (pii_sweep refactor)

Extract pii_sweep's inline official-detection into a PURE, importable helper that
both pii_sweep (Phase 5) and redact_output (Phase 7) use. Behavior-preserving.

**Files:**
- Modify: `scripts/pii_sweep.py` (add helper; refactor `SpacyPersonClassifier._is_official`)
- Test: `tests/test_pii_sweep.py` (add drift + helper tests)

**Step 1: Write the failing tests** (append to `tests/test_pii_sweep.py`):
```python
from scripts.pii_sweep import person_role_in_span, OFFICIAL_TITLES

def test_person_role_official_by_lexicon():
    # officials lexicon = normalized token frozensets (as SpacyPersonClassifier builds)
    officials = frozenset({frozenset({"dana", "wheeler"})})
    assert person_role_in_span("Dana Wheeler", [], officials=officials) == "official"

def test_person_role_official_by_title_prefix():
    assert person_role_in_span("Ramirez", ["officer"], officials=frozenset()) == "official"

def test_person_role_involved_keep_list():
    involved = frozenset({frozenset({"john", "doe"})})
    assert person_role_in_span("John Doe", [], officials=frozenset(),
                               involved=involved) == "involved"

def test_person_role_uninvolved_default():
    assert person_role_in_span("Some Bystander", [], officials=frozenset()) == "uninvolved"

def test_person_role_official_beats_involved():
    toks = frozenset({frozenset({"pat", "lee"})})
    assert person_role_in_span("Pat Lee", [], officials=toks, involved=toks) == "official"
```

**Step 2: Run to verify fail**
Run: `& .venv\Scripts\python.exe -m pytest tests/test_pii_sweep.py -k person_role -v`
Expected: FAIL (ImportError: cannot import name 'person_role_in_span')

**Step 3: Implement** in `scripts/pii_sweep.py` (a pure module-level function near
`_norm_name_tokens`):
```python
def person_role_in_span(
    span_text: str,
    preceding_token_texts: Sequence[str],
    *,
    officials: frozenset,                 # frozenset[frozenset[str]] normalized name-token sets
    involved: frozenset = frozenset(),    # same shape; keep-named non-officials
    titles: frozenset[str] = OFFICIAL_TITLES,
) -> str:
    """Role of a PERSON span: 'official' | 'involved' | 'uninvolved'. PURE (no
    spaCy). Order: lexicon-subset (officials) -> title-prefix -> involved-subset
    -> uninvolved. Officials win ties. Mirrors SpacyPersonClassifier._is_official
    so both consumers share ONE rule."""
    span_tokens = _norm_name_tokens(span_text)
    if officials and any(name <= span_tokens for name in officials):
        return "official"
    titles_l = frozenset(t.lower() for t in titles)
    if any(p.strip(".").lower() in titles_l for p in preceding_token_texts):
        return "official"
    if involved and any(name <= span_tokens for name in involved):
        return "involved"
    return "uninvolved"
```
Then refactor `SpacyPersonClassifier._is_official` to delegate (behavior-preserving
-- it builds the preceding tokens from the spaCy doc and calls the helper with
`involved=frozenset()`):
```python
def _is_official(self, doc, ent) -> bool:
    preceding = [doc[j].text for j in range(max(0, ent.start - 2), ent.start)]
    return person_role_in_span(
        ent.text, preceding, officials=self._lexicon, titles=self._titles
    ) == "official"
```

**Step 4: Run to verify pass** (new tests AND the whole existing pii_sweep suite --
the drift guard):
Run: `& .venv\Scripts\python.exe -m pytest tests/test_pii_sweep.py -v`
Expected: PASS (all existing Phase-5 tests stay green = behavior preserved;
including the `@pytest.mark.spacy` ones if the model is present)

**Step 5: Commit**
```
git add scripts/pii_sweep.py tests/test_pii_sweep.py
git commit -m "Phase 7.1: extract shared person_role_in_span from pii_sweep (behavior-preserving)"
```

---

## Task 2: redaction-check fixtures (synthetic, offline, PyMuPDF-free)

**Files:**
- Create: `tests/conftest_redaction.py` (or extend `tests/conftest.py`) with the
  fixtures below. Build with fpdf2 + pikepdf + crafted bytes ONLY (no fitz), so
  the offline tier never imports PyMuPDF.

**Fixtures (each returns a Path in tmp_path):**
- `bad_redaction_pdf`: fpdf2 -- write the ASCII line `SECRET NAME John Q Public`
  at (40,90), then `set_fill_color(0,0,0); rect(38,86,230,22,style="F")` over it.
  (x-ray will flag this; used in the `xray`-marked test.)
- `clean_pdf`: fpdf2 -- the line `Visible public text`, no covering rect (x-ray
  control; also a generic clean PDF for other checks).
- `metadata_pdf`: pikepdf -- `open_metadata()` set `dc:creator=["Jane Author"]`,
  `dc:title="Internal Draft"`; save.
- `redact_annot_pdf`: pikepdf -- add a page `/Annots` array with a
  `pikepdf.Dictionary(Type=Name.Annot, Subtype=Name.Redact, Rect=[18,40,120,55])`;
  do NOT "apply" it. (Build with pikepdf, NOT fitz.)
- `embedded_file_pdf`: pikepdf -- `pdf.attachments["hidden_notes.txt"] = b"internal only"`.
- `acroform_pdf`: pikepdf -- add `Root.AcroForm` with one text field whose `/V` is
  `pikepdf.String("123-45-6789")` (a value that should have been redacted).
- `annotation_text_pdf`: pikepdf -- a `/Subtype /FreeText` (or `/Text`) annot with
  `/Contents = pikepdf.String("reviewer note: suspect John Doe DOB on file")`.
- `incremental_save_pdf`: write a pikepdf, read its bytes, append a minimal
  synthetic incremental section so `count(b"%%EOF") == 2` and
  `count(b"startxref") == 2` (comment it clearly as a crafted second revision; the
  detector is a pure byte scan, no parse).
- `clean_single_rev_pdf`: a plain pikepdf save (`%%EOF` count == 1) -- the negative
  control for the incremental check.

No commit yet (fixtures land with Task 3's first test).

---

## Task 3: redaction-check -- schema + pure checks (no x-ray)

**Files:**
- Create: `scripts/redaction_check.py`
- Test: `tests/test_redaction_check.py`

Implement in TDD order; ONE check per cycle. For each: write the failing test,
run-fail, implement the check + wire it into the orchestrator, run-pass, commit.

### 3a. Schema + `publishable_view`

**Test:**
```python
from scripts.redaction_check import RedactionFinding, RedactionReport

def test_publishable_view_drops_local_evidence_and_asserts_clean_detail():
    f = RedactionFinding(check="metadata", severity="low", page=None,
                         summary="author present", detail={"fields": ["Author"]},
                         local_evidence={"Author": "Jane Author"})
    rep = RedactionReport(source_path="x.pdf", source_sha256="ab", mode="received",
                          checks_run=["metadata"], checks_unavailable=[],
                          findings=[f], n_findings=1, safe_to_publish=None,
                          warnings=[], cannot_catch=["pixelation"])
    pub = rep.publishable_view()
    assert pub["findings"][0]["local_evidence"] is None  # dropped
    assert "Jane Author" not in str(pub)                  # raw value gone
    assert pub["findings"][0]["detail"] == {"fields": ["Author"]}
```
**Implement:** the two dataclasses (design 1.3); `to_dict()` via `asdict`;
`publishable_view()` deep-copies, sets every finding's `local_evidence=None`, and
asserts no `detail` value contains a raw string the way `local_evidence` does
(defensive: raise if a finding sets `detail` to something that looks like raw
evidence -- keep simple: assert `local_evidence` is the only raw carrier).

### 3b. incremental_save check (bytes)
**Test:** `check_incremental_save(incremental_save_pdf)` -> a finding
(severity medium, page None, detail has `eof_count==2`/`startxref_count==2`);
`check_incremental_save(clean_single_rev_pdf)` -> no finding.
**Implement:** read bytes, count `b"%%EOF"` + `b"startxref"`; finding iff
`eof_count > 1`. Lead framing in `summary`.

### 3c. metadata check (pikepdf)
**Test:** `check_metadata(metadata_pdf)` -> finding; the raw author string is in
`local_evidence`, NOT in `detail` (detail lists field NAMES only).
**Implement:** pikepdf `docinfo` + `open_metadata()`; collect present
author/creator/producer/title; field names -> detail, raw values -> local_evidence.

### 3d. unapplied_redact check (pikepdf)
**Test:** `check_unapplied_redact(redact_annot_pdf)` -> finding (page 1,
`/Subtype /Redact`); `clean_pdf` -> none.
**Implement:** iterate pages `/Annots`, match `str(a.get("/Subtype")) == "/Redact"`.

### 3e. embedded_files check (pikepdf, incl /AF + /FileAttachment)
**Test:** `check_embedded_files(embedded_file_pdf)` -> finding listing
`hidden_notes.txt` (name + size in detail; NEVER the bytes).
**Implement:** `pdf.attachments` names + `/Names/EmbeddedFiles` + `/AF` +
`/FileAttachment` annots; enumerate name/size only.

### 3f. acroform_values check (pikepdf)
**Test:** `check_acroform_values(acroform_pdf)` -> finding; the `/V` value
`123-45-6789` is in `local_evidence`, detail has the field name only.
**Implement:** `Root/AcroForm/Fields` -> field name (detail) + `/V` (local_evidence).

### 3g. annotation_text check (pikepdf)
**Test:** `check_annotation_text(annotation_text_pdf)` -> finding; the `/Contents`
string is in `local_evidence`, not detail.
**Implement:** comment-type annots (`/Text`/`/FreeText`/`/Popup`) `/Contents`.

### 3h. text_layer check (pdfminer)
**Test:** on a page that co-occurs with a redaction signal, the sweep reports
extractable text as a lead; on a plain clean PDF it does NOT fire standalone
(page-level co-occurrence only; no cross-engine bbox math).
**Implement:** pdfminer per-page char count; finding only when the page also has
another redaction signal (a /Redact annot on that page) OR is image-only-but-has-text.

### 3i. Orchestrator `check_redactions(pdf_path, *, mode, vault_roots=())`
**Test:** runs all available checks; `mode="pre-publish"` sets `safe_to_publish`;
each check wrapped so one raising check -> a warning + listed in
`checks_unavailable`, others still run; `cannot_catch` always populated;
`publishable_view()` clean.
**Implement:** `source_sha256` (reuse `scripts.ingest.sha256_file`), run each pure
check under try/except, assemble `RedactionReport`. pre-publish `safe_to_publish`
= (no finding >= high) AND (checks_unavailable is empty) -- **fail-closed**.

**Commit after EACH of 3a-3i** (`git commit -m "Phase 7.1: redaction-check <check>"`).

---

## Task 4: redaction-check -- box_over_text (x-ray lazy edge)

**Files:** Modify `scripts/redaction_check.py`; Test `tests/test_redaction_check.py`.

**Step 1 (xray-marked test):**
```python
@pytest.mark.xray
def test_box_over_text_flags_bad_redaction(bad_redaction_pdf, clean_pdf):
    from scripts.redaction_check import check_box_over_text
    hits = check_box_over_text(bad_redaction_pdf)
    assert hits and hits[0].page == 1
    assert "John Q Public" in (hits[0].local_evidence or {}).get("text", "")  # LOCAL only
    assert "John Q Public" not in str(hits[0].detail)                          # not published
    assert check_box_over_text(clean_pdf) == []
```
Plus an offline test that `check_box_over_text` DEGRADES (returns a
`checks_unavailable` signal, not a crash) when x-ray import is monkeypatched to
raise.

**Step 2-4:** implement `check_box_over_text` with a LAZY `import xray` inside the
function; map `xray.inspect()` `{page: [{bbox, text}]}` -> findings (bbox+origin in
detail, `text` in `local_evidence`); wrap import/inspect errors -> raise a sentinel
the orchestrator catches into `checks_unavailable` (degrade-don't-crash, never a
false clean). Wire into `check_redactions`.

**Step 5: Commit** `Phase 7.1: redaction-check box_over_text (x-ray lazy edge)`

---

## Task 5: redact-output -- redact_text core (NER + regex, policy, spans)

**Files:**
- Create: `scripts/redact_output.py`
- Test: `tests/test_redact_output.py`

Use a FAKE person classifier (like pii_sweep's tests) so the core is model-free.

**Step 1: Failing tests (the policy + algorithm spec):**
```python
from scripts.redact_output import redact_text

class FakeSpans:
    # returns (span_text, start, end, preceding_tokens) tuples for PERSON ents
    def __init__(self, ents): self.ents = ents
    def __call__(self, text): return self.ents

def test_redacts_uninvolved_name_to_initials():
    ents = [("John Q Public", 0, 13, [])]
    out = redact_text("John Q Public stopped here", person_spans=FakeSpans(ents))
    assert out.startswith("J.Q.P.")
    assert "John" not in out

def test_keeps_official_and_involved():
    ents = [("Officer Ramirez", 0, 15, ["Officer"])]  # title-prefixed
    out = redact_text("Officer Ramirez ran the plate", person_spans=FakeSpans(ents))
    assert "Ramirez" in out
    invo = [("Jane Subject", 0, 12, [])]
    out2 = redact_text("Jane Subject paid", person_spans=FakeSpans(invo),
                       keep_names=["Jane Subject"])
    assert "Jane Subject" in out2

def test_masks_structured_pii_typed():
    out = redact_text("call 555-123-4567 ssn 123-45-6789", person_spans=FakeSpans([]))
    assert "[PHONE]" in out and "[SSN]" in out

def test_short_name_is_redacted_no_len_skip():
    out = redact_text("Li was here", person_spans=FakeSpans([("Li", 0, 2, [])]))
    assert "L." in out and "Li " not in out

def test_overlapping_person_and_pii_right_to_left():
    # a name span and a date span that touch -> no offset corruption, no double-redact
    ents = [("Sam 01/02/1990", 0, 14, [])]
    out = redact_text("Sam 01/02/1990 noted", person_spans=FakeSpans(ents))
    assert "Sam" not in out and out.count("[") <= 1  # contained PII not double-applied
```

**Step 2: run-fail.**

**Step 3: Implement `redact_text`:** lazy spaCy edge by default (a
`SpacyPersonSpans` that yields `(ent.text, ent.start_char, ent.end_char,
preceding_tokens)` for PERSON ents WITHOUT the `len<=2` skip), injectable
`person_spans` for tests. Build replacement spans: for each PERSON ent call
`person_role_in_span` (reused) with `officials`/`involved` lexicons (normalized
from `keep_names` via the imported `_norm_name_tokens`); role `uninvolved` ->
initials replacement; else keep. For each `DEFAULT_PII_PATTERNS` match ->
typed-placeholder replacement. Resolve overlaps (longer wins, PERSON-before-PII
tie), apply RIGHT-TO-LEFT. Initials: first alpha char per whitespace token,
upper, dot-joined.

**Step 4: run-pass. Step 5: commit** `Phase 7.2: redact_output redact_text core`.

---

## Task 6: redact-output -- local_texts paths + exhibit + publish-path contract

**Files:** Modify `scripts/redact_output.py`; Test `tests/test_redact_output.py`.

**Tests (the safety-critical ones):**
```python
def test_redact_local_texts_is_local_only_map():
    lt = {"abc123": {"text": "John Doe stopped", "count": 3, "categories": ["person_unknown_role"]}}
    m = redact_local_texts(lt, person_spans=...)
    assert set(m.keys()) == {"abc123"}           # text_id key is LOCAL (feeds exhibit only)
    assert "John" not in m["abc123"]

def test_redact_note_replaces_known_text_no_textid_no_raw():
    lt = {"abc123": {"text": "John Doe stopped", "count": 1, "categories": [...]}}
    note = "Per the log, John Doe stopped near 5th St. Officer Ruiz responded."
    out = redact_note(note, lt, person_spans=...)
    assert "abc123" not in out and "John Doe" not in out
    assert "Officer Ruiz" in out and "5th St" in out   # analyst narrative untouched

def test_write_local_exhibit_outside_vault(tmp_path):
    exhibit_dir = tmp_path / "exhibits"; exhibit_dir.mkdir()
    p = write_local_exhibit({...}, exhibit_dir, vault_roots=[tmp_path / "vault"])
    assert p.exists() and "John Doe" in p.read_text()   # FULL un-redacted, LOCAL

def test_write_local_exhibit_raises_inside_vault(tmp_path):
    vault = tmp_path / "vault"; (vault / "ex").mkdir(parents=True)
    with pytest.raises(ValueError):
        write_local_exhibit({...}, vault / "ex", vault_roots=[vault])

def test_write_local_exhibit_raises_on_symlink_into_vault(tmp_path):
    # a link that resolves into the vault must be rejected (Path.resolve())
    ...
```
**Implement** the three functions per design 2.0 (#2 local-only map, #3 note
sanitizer = find-and-replace each known flagged `text` with its `redact_text`
form, #4 vault-guarded CSV via `Path.resolve()` containment). Commit
`Phase 7.2: redact_output local_texts/note/exhibit + vault guard`.

---

## Task 7: SKILL.md x2 + smoke tests

Use `plugin-dev:skill-development`. Mirror `skills/ingest/SKILL.md` structure.

- `skills/redaction-check/SKILL.md`: third-person trigger description; documents
  the 8 checks, dual mode, leads-not-verdicts + cannot_catch, publishable-vs-local
  (local_evidence), lazy x-ray edge / degrade-don't-crash, fail-closed pre-publish,
  the ingest->redaction-check seam. Resources: prior-art.md + the script.
- `skills/redact-output/SKILL.md`: documents the involved-vs-uninvolved policy +
  keep_names, the 4 entry points + publish-path contract, the pii_sweep
  `local_texts` join, the vault-guarded exhibit, the shared `person_role_in_span`.
- Smoke tests `tests/test_redaction_check_skill.py` + `tests/test_redact_output_skill.py`
  (PyYAML; mirror `test_ingest_skill.py`): frontmatter name/description/version;
  body documents the key contracts.

Commit `Phase 7.3: redaction-check + redact-output SKILL.md + smoke tests`.

---

## Task 8: Integration + full suite

- Add a wiring/integration smoke that `check_redactions` over a multi-finding
  fixture returns a clean `publishable_view()` (no raw strings anywhere) -- the
  end-to-end never-publish-raw guard.
- Run the FULL suite: `mise run test` (expect all green; the `xray`/`spacy`/
  `docling` marked tests load real engines). Confirm offline subset stays
  PyMuPDF-free: `& .venv\Scripts\python.exe -m pytest -k "not xray and not spacy and not docling" -q`.
- Commit `Phase 7: integration smoke + full suite green`.

---

## Execution

Driven via **superpowers:subagent-driven-development** (autonomous; one implementer
subagent per task cluster, ASCII-only prompts/fixtures). After all tasks: the Codex
**impl-review** gate (resume thread `019e95ea-d5d9-72f0-87db-e1bbd50a4c42`), route
critical/important findings to fix-subagents, then regenerate MANIFEST.md and open
PR #4 (merge commit).

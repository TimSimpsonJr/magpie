# ingest (Phase 6) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL — execute this plan with
> `superpowers:subagent-driven-development` (autonomous SDD), one task per
> subagent, code-review between tasks. (Not `executing-plans`; this is the
> in-session SDD path.)

**Goal:** Build the `ingest` skill — a PDF → `DoclingDocument` JSON ingest that
preserves `{page_no, bbox, charspan}` provenance, gated by a pure text-layer
quality check that decides native-vs-re-OCR before OCRing and flags
degraded/handwriting pages for humans.

**Architecture:** Two modules, mirroring the suite's pure-core/engine-at-edge
split. `scripts/ingest_gate.py` is PURE (text + numeric signals + an *injectable*
wordlist; golden-testable, no PDF/model). `scripts/ingest.py` is the only Docling
importer (source hash, the `do_ocr=False` pass, applying the gate's doc-decision,
the Tesseract-gated OCRmyPDF detect/skip/flag seam, the Bates post-pass,
degraded-page flagging, the `IngestResult`). Source of truth: the approved design
`docs/plans/2026-06-04-magpie-phase6-ingest-design.md` (Codex design-review
approved, round 2).

**Tech Stack:** Python 3.12.10; docling 2.97 (RapidOcrOptions, do_ocr,
force_full_page_ocr, save_as_json, res.confidence, iterate_items); ocrmypdf 17.5
(seam only); pytest with a new `docling` marker (model-gated integration, like
`spacy`). All deps pinned at the research gate; numpy/pandas pins untouched.

---

## Conventions for the executor (read once)

- **Run tests with `mise run test`** (the venv has the deps). NEVER bare `python`
  — the CC PowerShell tool is `-NoProfile`, so bare `python` hits the global
  interpreter. Fallback: `& .venv\Scripts\python.exe -m pytest`. Select the
  model-gated integration tests with `-k docling`; the pure suite excludes them
  with `-k "not docling"` (CI-fast).
- **Pure core vs edge.** `ingest_gate.py` imports only stdlib (`re`, `enum`,
  `pathlib`, `dataclasses`) — NO docling/PDF. `ingest.py` is the only module that
  imports docling; it imports docling *lazily* inside the function that needs it,
  so importing `ingest` stays cheap (mirrors `pii_sweep`'s lazy spaCy edge).
- **Injectable wordlist** (mirrors `pii_sweep`'s injectable classifier): gate
  functions take `wordlist: frozenset[str] | None = None`; `None` lazily loads the
  bundled default. Tests inject a tiny synthetic set — the pure suite never reads
  the big file.
- **Synthetic fixtures only.** No real corpus (wired in at Task 11.2). Build PDFs
  with fpdf2 (native text) + Pillow (image-only scans) — both already installed.
- **Determinism / rigor.** Gate functions are pure (no IO/clock/random/network),
  JSON-able outputs, leads-not-verdicts. Thresholds are NAMED MODULE CONSTANTS,
  never magic numbers in branches.
- **Commits:** one per task (after its tests pass). Multi-line messages via a
  UTF-8 file + `git commit -F` (PowerShell has no reliable git heredoc).
- **Decoupling:** `ingest*` import none of `stats/load_table/derive/recipe/
  rollup/pii_sweep` and vice-versa (a drift test is not needed — there is no
  shared surface; the only contract is the §7 citation schema, Phase 8).

---

## Task 1: `ingest_gate` enums + signals + injectable wordlist (pure foundation)

**Files:**
- Create: `scripts/ingest_gate.py`
- Create: `skills/ingest/references/common_words.txt` (the bundled default wordlist)
- Test: `tests/test_ingest_gate.py`

**Step 1 — failing test** (`tests/test_ingest_gate.py`):

```python
import pytest
from scripts.ingest_gate import (
    PageDiagnosis, DocDecision,
    alphabetic_tokens, wordlist_hit_rate, char_density_ok, load_default_wordlist,
)

SMALL_WL = frozenset({"the","police","department","search","reason","vehicle",
                      "officer","requested","record","this","contains","data"})

def test_alphabetic_tokens_splits_and_lowercases():
    toks = alphabetic_tokens("The Police, Dept-99 searched 2 cars!")
    # numbers and pure-digit tokens dropped; words lowercased; hyphen splits
    assert toks == ["the","police","dept","searched","cars"]

def test_wordlist_hit_rate_fraction_in_list():
    # 3 of 4 alpha tokens are in SMALL_WL
    rate = wordlist_hit_rate("the police vehicle xqzklmn", SMALL_WL)
    assert rate == pytest.approx(3/4)

def test_wordlist_hit_rate_none_when_no_tokens():
    assert wordlist_hit_rate("123 456 !!!", SMALL_WL) is None  # no alpha tokens

def test_char_density_flags_garbage_glyph_runs():
    assert char_density_ok("Normal readable sentence with words.") is True
    # mojibake-like (exotic glyphs): high non-letter ratio / long non-space runs
    assert char_density_ok("∎˚¬∆ﬁ�����⁂⁂⁂⁂⁂⁂⁂⁂⁂⁂⁂⁂") is False
    # the EXACT latin-1 garbled_pdf fixture text MUST also fail (ties the density
    # constants to the fixture so Task-6's garbled e2e is deterministic, not fragile)
    assert char_density_ok(";;;;;;;;;;;;;;;; ################ %%%%%%%%%%%% 8492037184920371 @@@@@@@@@@@@ ") is False

def test_enums_have_the_documented_members():
    assert {d.value for d in PageDiagnosis} == {
        "native_ok","image_only","garbled_text","uncertain_review"}
    assert {d.value for d in DocDecision} == {
        "native","ocr_images","force_full_doc_ocr","review"}

def test_default_wordlist_loads_and_is_lowercased():
    wl = load_default_wordlist()
    assert "the" in wl and len(wl) > 200 and all(w == w.lower() for w in wl)
```

**Step 2 — run, expect FAIL** (`ImportError`). `mise run test -- -k ingest_gate`.

**Step 3 — implement** `scripts/ingest_gate.py`:
- `class PageDiagnosis(str, Enum)`: `native_ok / image_only / garbled_text /
  uncertain_review`. `class DocDecision(str, Enum)`: `native / ocr_images /
  force_full_doc_ocr / review`.
- `alphabetic_tokens(text) -> list[str]`: `re.findall(r"[A-Za-z]+", text)`
  lowercased (drops digits/punct; hyphen/space split for free).
- `wordlist_hit_rate(text, wordlist) -> float | None`: tokens = alphabetic_tokens;
  `None` if no tokens (never a fake 0); else `sum(t in wordlist)/len(tokens)`.
- `char_density_ok(text) -> bool`: letters/total ratio above a floor AND no
  absurd non-letter run — NAMED constants `_MIN_LETTER_RATIO`,
  `_MAX_NONLETTER_RUN`.
- `load_default_wordlist() -> frozenset[str]`: read the bundled
  `references/common_words.txt` (lowercased, one word per line) ONCE; module-level
  cache. Create the file with ~1–2k common English words (public-domain frequency
  list; weak sanity-check signal — exact membership not load-bearing).

**Step 4 — run, expect PASS.** **Step 5 — commit** `Phase 6.1: ingest_gate enums + signals + bundled wordlist`.

---

## Task 2: `diagnose_page` — per-page diagnosis (pure)

**Files:** Modify `scripts/ingest_gate.py`; Test `tests/test_ingest_gate.py`.

**Step 1 — failing tests** (golden; inject `SMALL_WL`; constants tunable):

```python
from scripts.ingest_gate import diagnose_page, PageDiagnosis as PD

def D(text, **kw): return diagnose_page(text, wordlist=SMALL_WL, **kw)

def test_clean_native_text_is_native_ok():
    assert D("The police department search reason vehicle record.") == PD.native_ok

def test_empty_or_near_empty_is_image_only():
    assert D("") == PD.image_only
    assert D("   \n  ") == PD.image_only

def test_present_but_garbled_is_garbled_text():
    # enough tokens, low hit-rate, AND density anomaly => garbled
    assert D("xqzklm ﬁﬁﬁ ∎∎∎ qwzx ⁂⁂ lkjhg zzzz vvvv bbbb nnnn") == PD.garbled_text

def test_sparse_below_token_floor_is_uncertain_not_garbled():
    # too few alpha tokens to judge a hit-rate => uncertain_review (NOT garbled)
    assert D("Ref: 88-A") == PD.uncertain_review

def test_native_table_page_low_hitrate_stays_native_ok():
    # numeric/short-token table content: low wordlist hit-rate but normal density
    assert D("2026 2026 14 18 SC OOS 49417 1792 2943 1979 SVPD ALPR") == PD.native_ok

def test_all_caps_legal_page_stays_native_ok():
    assert D("FOIA RESPONSE CONFIDENTIAL RECORDS DIVISION CASE NUMBER REDACTED") == PD.native_ok

def test_multi_column_runon_stays_native_ok_when_density_normal():
    assert D("the police department the search reason the vehicle the officer record") == PD.native_ok

def test_repeated_boilerplate_does_not_force_or_avoid_ocr_spuriously():
    # a Bates/letterhead-only page is sparse text => uncertain_review (flag), not native_ok-trusted nor garbled
    assert D("SVPD-000123") == PD.uncertain_review

def test_low_parse_score_downgrades_otherwise_native_to_uncertain():
    # parse_score MUST be consumed: Docling's own low-confidence parse contradicts
    # otherwise-acceptable text -> uncertain_review (the contradictory-signal hook).
    text = "the police department search reason record vehicle officer"
    assert D(text) == PD.native_ok
    assert D(text, parse_score=0.02) == PD.uncertain_review
```

**Step 2 — run, expect FAIL.** **Step 3 — implement** `diagnose_page(native_text, *, parse_score=None, lang="en", wordlist=None, min_chars=..., min_tokens=..., garbled_hit_rate=...) -> PageDiagnosis`:
- normalize; `n = len(native_text.strip())`.
- `n < min_chars` → `image_only`.
- `toks = alphabetic_tokens`; `len(toks) < min_tokens` → `uncertain_review`
  (not enough signal — the boilerplate/sparse guard).
- compute `hit = wordlist_hit_rate`; `density = char_density_ok`.
- **garbled requires co-occurrence:** `hit is not None and hit < garbled_hit_rate
  AND not density` → `garbled_text`. (Low hit-rate ALONE never garbles → tables /
  all-caps / multi-column stay native.)
- contradictory-signal / handwriting hook: a very low `parse_score` (Docling
  itself flags an unreliable parse) on text that would otherwise pass →
  `uncertain_review` (consumed by the test above — the impl must read parse_score).
- else → `native_ok`. All thresholds are named module constants.

**Step 4 — PASS.** **Step 5 — commit** `Phase 6.1: diagnose_page per-page diagnosis (false-positive guards)`.

---

## Task 3: `decide_doc` — conservative doc rollup (pure)

**Files:** Modify `scripts/ingest_gate.py`; Test `tests/test_ingest_gate.py`.

**Step 1 — failing tests:**

```python
from scripts.ingest_gate import decide_doc, DocDecision as DD, PageDiagnosis as PD

def test_all_native_is_native():
    assert decide_doc([PD.native_ok]*5) == DD.native

def test_all_image_only_is_ocr_images():
    assert decide_doc([PD.image_only]*5) == DD.ocr_images

def test_substantial_garbled_escalates_force_full_doc_ocr():
    assert decide_doc([PD.garbled_text]*4 + [PD.native_ok]) == DD.force_full_doc_ocr

def test_mostly_native_few_bad_stays_native_and_flags():
    # 200-page brief with 2 bad pages must NOT flip to full OCR (the load-bearing rule)
    diag = [PD.native_ok]*198 + [PD.garbled_text, PD.image_only]
    assert decide_doc(diag) == DD.native

def test_uncertain_dominant_is_review():
    assert decide_doc([PD.uncertain_review]*4 + [PD.native_ok]) == DD.review

def test_empty_pagelist_is_review():
    assert decide_doc([]) == DD.review

def test_combined_image_and_garbled_escalates_to_force_full_doc_ocr():
    # neither share alone dominant, but combined bad is high with garbled present
    # => force_full_doc_ocr (the safe superset; pins the prose rule)
    assert decide_doc([PD.image_only]*2 + [PD.garbled_text]*2 + [PD.native_ok]) == DD.force_full_doc_ocr
```

**Step 2 — run, expect FAIL.** **Step 3 — implement** `decide_doc(diagnoses) -> DocDecision`.
Compute each diagnosis share over `n`, then evaluate **IN THIS ORDER (first match
wins)** so every Task-3 test pins a single deterministic decision:
- empty list → `review`.
1. `uncertain` share ≥ `_REVIEW_FRACTION` → `review`.
2. `garbled` share ≥ `_ESCALATE_FRACTION`, **OR** (`garbled` > 0 AND
   `(image_only + garbled)` share ≥ `_ESCALATE_FRACTION`) → `force_full_doc_ocr`
   (a present-but-bad text layer must be overridden doc-wide; this is also the
   safe superset when image+garbled are jointly high but neither alone dominates).
3. `image_only` share ≥ `_ESCALATE_FRACTION` (no significant garbled) → `ocr_images`.
4. otherwise (mostly native, minority bad) → `native` — the minority bad pages are
   flagged later by `ingest.py`, NOT re-OCR'd doc-wide.

Conservative by construction; `_REVIEW_FRACTION` / `_ESCALATE_FRACTION` are named
module constants (default ≈ 0.5) tuned so the Task-3 golden tests pass.

**Step 4 — PASS.** **Step 5 — commit** `Phase 6.1: decide_doc conservative rollup`.

---

## Task 4: `ingest` source identity + `IngestResult` shape (edge foundation)

**Files:** Create `scripts/ingest.py`; Test `tests/test_ingest.py`.

**Step 1 — failing tests** (pure — no docling yet):

```python
from scripts.ingest import sha256_file, IngestResult

def test_sha256_file_matches_known(tmp_path):
    p = tmp_path/"a.bin"; p.write_bytes(b"magpie")
    import hashlib
    assert sha256_file(p) == hashlib.sha256(b"magpie").hexdigest()

def test_ingestresult_is_jsonable_with_required_keys():
    import json
    r = IngestResult(source_path="x.pdf", source_sha256="ab", docling_json_path="x.json",
                     schema_name="DoclingDocument", schema_version="1.10.0", n_pages=1,
                     doc_decision="native", trustworthy_for_extraction=True,
                     ocr_engine_used="none", per_page=[], bates=[], warnings=[])
    d = json.loads(json.dumps(r.to_dict()))
    assert d["trustworthy_for_extraction"] is True and d["ocr_engine_used"] == "none"
```

**Step 2 — FAIL.** **Step 3 — implement** the `IngestResult` dataclass (fields per
design §5, `to_dict()` JSON-able) + `sha256_file(path)` (streamed). NO docling
import at module top.

**Step 4 — PASS.** **Step 5 — commit** `Phase 6.2: ingest source identity + IngestResult`.

---

## Task 5: `docling` marker + `ingest()` `do_ocr=False` pass + gate wiring

**Files:** Modify `pyproject.toml` (register `docling` marker); Modify
`scripts/ingest.py`; Create `tests/conftest.py` fixture helpers + `tests/fixtures/`
generators; Test `tests/test_ingest.py`.

**Step 1 — failing test** (model-gated):

```python
import pytest
pytestmark = pytest.mark.docling   # whole module needs the real models

def test_clean_digital_pdf_decides_native_and_does_not_flag(tmp_path, native_pdf):
    from pathlib import Path
    import json
    from scripts.ingest import ingest
    r = ingest(native_pdf, out_dir=tmp_path)
    assert r.doc_decision == "native"
    assert r.ocr_engine_used == "none"
    assert r.trustworthy_for_extraction is True
    # nan semantics (Codex r2): native pages have ocr_score == nan -> nan is N/A,
    # NOT 0 -> a clean native page must NOT be flagged as degraded.
    assert not any(p["flagged"] for p in r.per_page)
    doc = json.loads(Path(r.docling_json_path).read_text(encoding="utf-8"))
    assert doc["schema_name"] == "DoclingDocument"
```

(`native_pdf` fixture builds an fpdf2 text PDF; see conftest.)

**Step 2 — FAIL.** **Step 3 — implement:**
- `pyproject.toml`: add `docling` to `[tool.pytest.ini_options] markers` (mirror
  `spacy`), description "model-gated docling integration; select with -k docling".
- `tests/conftest.py` fixtures (all synthetic, built into `tmp_path`):
  - `native_pdf` — fpdf2 text PDF (clean English).
  - `scan_pdf` — Pillow image-only PDF (no text layer).
  - `garbled_pdf` — **ONE concrete recipe, built to TRIP `char_density_ok`
    unambiguously** (Codex r2): a latin-1-safe garbage string with a LOW letter
    ratio AND explicit LONG non-letter runs, e.g.
    `";;;;;;;;;;;;;;;; ################ %%%%%%%%%%%% 8492037184920371 @@@@@@@@@@@@ "`
    repeated to fill the page (long identical-symbol runs + a long digit run; very
    few letters). This is a PRESENT native text layer Docling would otherwise
    TRUST, which the gate MUST diagnose as `garbled_text` — no wordlist hits AND
    `char_density_ok` returns False (letter-ratio below `_MIN_LETTER_RATIO` and a
    non-letter run beyond `_MAX_NONLETTER_RUN`) → `force_full_doc_ocr`. The Task-1
    `char_density_ok` constants and this fixture are tuned TOGETHER so the trip is
    deterministic, not threshold-fragile. Latin-1-safe so fpdf2's Helvetica renders it.
  - `mixed_pdf` — multi-page fpdf2 doc: mostly clean native pages + 1–2
    garbage/blank pages (→ `native` with those pages flagged).
  - `bates_pdf` — native page(s) stamped with a `SVPD-000123`-style Bates number.
  - `degraded_pdf` — a faint/noisy Pillow scan → low OCR confidence → flagged.
- `ingest(pdf_path, *, out_dir, lang="en", max_num_pages=..., max_file_size=...,
  page_range=None) -> IngestResult`: lazily import docling; `sha256_file`; run a
  Docling `do_ocr=False` pass (default `DoclingParseDocumentBackend`,
  `do_table_structure` per profile, convert() guards from §2.6); pull per-page
  native text + `parse_score`; `diagnose_page` each → `decide_doc`. For Task 5,
  wire only the `native` branch end-to-end (reuse the pass-#1 doc; `save_as_json`;
  build `IngestResult`).

**Step 4 — PASS** (`mise run test -- -k docling`). **Step 5 — commit** `Phase 6.2: docling marker + do_ocr=False gate pass (native branch)`.

---

## Task 6: apply doc-decision branches + OCRmyPDF seam

**Files:** Modify `scripts/ingest.py`; Test `tests/test_ingest.py`.

**Step 1 — failing tests** (docling-marked + a mocked-seam unit test):

```python
def test_image_only_scan_engages_rapidocr_with_full_provenance(tmp_path, scan_pdf):
    r = ingest(scan_pdf, out_dir=tmp_path)
    assert r.doc_decision == "ocr_images"          # image-only != garbled (tight, no OR)
    assert r.ocr_engine_used == "rapidocr"
    import json
    doc = json.loads(Path(r.docling_json_path).read_text(encoding="utf-8"))
    provs = [p for t in doc["texts"] for p in t.get("prov", [])]
    assert provs, "no provenance on OCR'd items"
    for p in provs:                                # FULL contract {page_no, bbox, charspan}
        assert "page_no" in p and "charspan" in p
        assert {"l","t","r","b","coord_origin"} <= set(p["bbox"])
    assert len({p["bbox"]["coord_origin"] for p in provs}) == 1, "bbox origins not normalized"
    # nan semantics (Codex r2): an OCR'd scan has parse_score == nan -> N/A, not 0;
    # a CLEAN scan (good ocr_score) must NOT be flagged merely because parse_score is nan.
    assert not any(p["flagged"] for p in r.per_page)

def test_garbled_text_layer_forces_full_doc_ocr(tmp_path, garbled_pdf):
    r = ingest(garbled_pdf, out_dir=tmp_path)
    assert r.doc_decision == "force_full_doc_ocr"  # present-but-bad layer overridden doc-wide
    assert r.ocr_engine_used == "rapidocr"

def test_mostly_native_with_two_bad_pages_stays_native_and_flags(tmp_path, mixed_pdf):
    r = ingest(mixed_pdf, out_dir=tmp_path)
    assert r.doc_decision == "native"              # NOT flipped to full OCR (conservative rule)
    assert any(p["flagged"] for p in r.per_page)   # the bad pages ARE flagged

def test_ocrmypdf_seam_skips_warns_and_flags_when_tesseract_absent(monkeypatch, tmp_path, scan_pdf):
    # force the seam's detect to report Tesseract absent (the Phase-6 reality)
    monkeypatch.setattr("scripts.ingest._tesseract_available", lambda: False)
    r = ingest(scan_pdf, out_dir=tmp_path, deskew=True)
    assert any("OCRmyPDF" in w and "Tesseract" in w for w in r.warnings)
    assert any(p["flagged"] for p in r.per_page)   # requested-but-unavailable preprocess -> flag
```

**Step 2 — FAIL.** **Step 3 — implement:** the `ocr_images` (`do_ocr=True,
force=False`) and `force_full_doc_ocr` (`force_full_page_ocr=True`) branches via a
second Docling convert; set `ocr_engine_used="rapidocr"`; `native` branch flags
the minority bad pages. OCRmyPDF seam: `_tesseract_available()` (Get/shutil.which
+ catch `ocrmypdf.exceptions.MissingDependencyError`) → if absent, skip + append a
warning + flag affected pages; NO deskew/redo decision engine (seam only).

**Step 4 — PASS.** **Step 5 — commit** `Phase 6.2: apply doc-decision + OCRmyPDF detect/skip/flag seam`.

---

## Task 7: Bates post-pass + degraded flagging + `trustworthy_for_extraction`

**Files:** Modify `scripts/ingest.py`; Test `tests/test_ingest.py`.

**Step 1 — failing tests:**

```python
def test_bates_captured_separately_with_provenance(tmp_path, bates_pdf):
    r = ingest(bates_pdf, out_dir=tmp_path)
    assert any(b["value"].startswith("SVPD-") for b in r.bates)
    assert all("page_no" in b and "bbox" in b for b in r.bates)

def test_degraded_page_is_flagged(tmp_path, degraded_pdf):
    r = ingest(degraded_pdf, out_dir=tmp_path)
    assert any(p["flagged"] and p["flag_reason"] for p in r.per_page)

def test_review_doc_contract_pinned_deterministically(tmp_path, native_pdf, monkeypatch):
    from scripts.ingest_gate import DocDecision
    # FORCE the rollup to review so the safety contract is ALWAYS exercised
    # (the old `if doc_decision == review` guard was green-on-broken).
    monkeypatch.setattr("scripts.ingest.decide_doc", lambda diags: DocDecision.review)
    r = ingest(native_pdf, out_dir=tmp_path)
    assert r.doc_decision == "review"
    assert r.trustworthy_for_extraction is False      # downstream (Phase 8) MUST refuse extraction
    assert Path(r.docling_json_path).exists()         # JSON still written for human inspection
    assert all(p["flagged"] for p in r.per_page)      # every page flagged on review
```

(Impl note: `ingest.py` must `from scripts.ingest_gate import decide_doc` — i.e.
reference it as `scripts.ingest.decide_doc` — so the monkeypatch target resolves.)

**Step 2 — FAIL.** **Step 3 — implement:**
- Bates post-pass: a word-boundary regex (`\b[A-Z][A-Z0-9]{1,}[-_ ]?\d{3,}\b`
  shape, tuned) over `doc.iterate_items()` text; capture `{value, page_no, bbox}`
  from `item.prov[0]`; SEPARATE list, never rewrites text.
- degraded flagging: read `res.confidence` per page (`ocr_score`/`mean_grade`/
  `low_grade`, handle `nan` per modality) + gate diagnoses; set `per_page[i].
  flagged/flag_reason`.
- `trustworthy_for_extraction = (doc_decision != "review")`; on `review` still
  `save_as_json` (human inspection) but flag all pages.

**Step 4 — PASS.** **Step 5 — commit** `Phase 6.2: Bates post-pass + degraded flagging + review-doc contract`.

---

## Task 8: `ingest` SKILL.md + wiring smoke test

**Files:** Create `skills/ingest/SKILL.md` (via `plugin-dev:skill-development`);
Test `tests/test_ingest_skill.py` (PyYAML smoke, mirrors `test_pii_sweep_skill`).

**Step 1 — failing smoke test:** frontmatter parses; `name == ingest`;
description has trigger phrases + mentions PDF/provenance; body documents the gate
(native-vs-re-OCR before OCR), `DoclingDocument` JSON internal (never Markdown),
the `review`/`trustworthy_for_extraction` contract, the Tesseract-gated OCRmyPDF
seam, and that it feeds `investigate`/`redaction-check`.

**Step 2 — FAIL.** **Step 3 — author SKILL.md** via the
`plugin-dev:skill-development` sub-skill: lean, imperative, third-person triggers;
orchestration = source-hash → `do_ocr=False` gate → apply decision (RapidOCR
default; OCRmyPDF seam) → Bates + flags → `IngestResult` + internal JSON →
hand-off to `investigate` (citation) / `redaction-check` (Phase 7). Document the
rigor guardrails (gate-before-OCR, flag-don't-fake, review-not-auto-extractable,
preserve-don't-fix, source identity).

**Step 4 — PASS.** **Step 5 — commit** `Phase 6.3: ingest SKILL.md + wiring smoke`.

---

## Final verification (before the impl-review gate)

1. `mise run test` — FULL suite green (308 prior + new), incl. `-k docling`
   integration on the real models. Confirm `-k "not docling"` stays green offline.
2. Confirm `ingest_gate.py` imports NO docling/PDF (pure); `ingest.py` imports
   docling LAZILY (importing the module is cheap).
3. Confirm numpy 2.4.6 / pandas 3.0.3 still pinned (`pip freeze`); no real corpus
   touched (all fixtures synthetic, built in `tmp_path`).
4. Regenerate `MANIFEST.md` (new scripts/ingest_gate.py, scripts/ingest.py,
   skills/ingest/, the wordlist data file, the docling marker, new tests/fixtures).
5. Hand to the **impl-review gate** (Codex, `[MODE: impl-review]`, same Phase-6
   thread) — branch-base→HEAD diff vs this plan; route critical/important findings
   to one fix-subagent per cluster; confirmatory pass; then PR (merge commit).

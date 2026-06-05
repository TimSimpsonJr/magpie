# Magpie Phase 6 — `ingest` design

- **Date:** 2026-06-04
- **Status:** Design approved (brainstorming complete; Codex stand-in converged
  "looks good, write the plan" with conditions — gate-API split, conservative
  doc-rollup, source-identity provenance, boilerplate/table false-positive
  fixtures, minimal OCRmyPDF seam, handwriting→review — all adopted). Next:
  `writing-plans`.
- **Author:** Tim Simpson (with Claude; Codex as brainstorm partner, autonomous mode)
- **Phase:** Layer 0–1, Phase 6 (Tasks 6.0–6.3) of `docs/plans/2026-06-03-magpie-layer-0-1.md`.
- **Research gate:** `skills/ingest/references/prior-art.md` (verified Docling/RapidOCR/OCRmyPDF facts).
- **Source of truth:** design doc `2026-06-03-magpie-design.md` §5.1 (ingest engine) / §6 (data flow) / §10 (prior-art deltas) / §11 (CPU-latency risk).

---

## 1. Goal & scope

`ingest` is the **document/PDF path** of the suite (the structured-data path is
`dataset-analyze`). It turns a PDF into a **`DoclingDocument` JSON kept
internally** — never Markdown — so every extracted element keeps its
`{page_no, bbox, charspan}` provenance for the Phase-8 citation anchor. Before
OCRing, a **text-layer quality gate** decides native-vs-re-OCR; **degraded /
handwriting pages are flagged for humans**, never silently OCR'd as fact.

**In scope (Tasks 6.1–6.3):** the pure quality gate (`ingest_gate.py`), the
Docling wrapper preserving provenance + Bates post-pass + degraded-page flagging
(`ingest.py`), and the `ingest` SKILL.md. OCR engine = **RapidOCR-via-Docling**
(no system binaries); **OCRmyPDF** is a Tesseract-gated optional seam (detect +
skip + flag), not a full preprocessing engine in this phase.

**Out of scope (deferred, YAGNI for Layer-0-1):** per-page selective force-OCR;
an OCRmyPDF deskew-vs-redo decision engine; offline model-prefetch machinery
(setup-doctor, Phase 10); fixing live-text-under-redaction-boxes (preserved here;
`redaction-check` Phase 7 reasons about it); the real-corpus golden tests
(Task 11.2). Track-B entity work is Layer 2.

---

## 2. Key decisions (the heart of this design)

### 2.1 Two modules — pure gate + Docling at the edge
Mirrors the suite's established split (`pii_sweep`: pure core, spaCy at the edge):
- **`scripts/ingest_gate.py`** — PURE quality-gate logic over **text + numeric
  signals** (no PDF/model imports). Golden-testable with synthetic strings.
- **`scripts/ingest.py`** — the Docling wrapper (engine at the edge): runs Docling,
  feeds page text to the gate, applies the gate's decision, emits the
  `DoclingDocument` JSON + `IngestResult`. The only module that imports docling.

### 2.2 Gate API: per-page **diagnosis** ≠ doc-level **rollup** (Codex)
The pure core never bakes a document-wide *action* into a per-page *label*:
- **Per-page diagnosis** — `PageDiagnosis ∈ {native_ok, image_only, garbled_text,
  uncertain_review}`. A *diagnosis* of what the page's text layer is.
- **Doc-level rollup** — `DocDecision ∈ {native, ocr_images, force_full_doc_ocr,
  review}`. The *action* derived by rolling per-page diagnoses up under a
  conservative threshold rule (§4).

### 2.3 Conservative rollup (Codex — the load-bearing safety rule)
One ugly page must **not** flip a 200-page digital brief into full-page OCR.
- *Mostly-good native text with a few bad pages* → `DocDecision = native` (keep
  the cheap native parse) **and flag** the bad pages for review.
- *A meaningful share of image-only pages* → `ocr_images` (`do_ocr=True`,
  `force_full_page_ocr=False`) — Docling OCRs the bitmap regions per page while
  keeping native text where present (handles mixed docs with **no** per-page
  logic of our own).
- *A meaningful share of garbled (present-but-bad) text* → `force_full_doc_ocr`
  (`force_full_page_ocr=True`) — the only doc-wide lever that overrides a bad
  text layer Docling would otherwise trust.
- *Handwriting / weak-or-contradictory signals dominate* → `review`.
The exact fractions/floors are tuned in the plan against an explicit **mixed-doc
fixture**; the rule is *conservative by construction* (escalate doc-wide only on a
substantial bad share).

### 2.4 The wordlist is a WEAK secondary signal (Codex — false-positive defense)
A low dictionary-hit-rate **alone never** forces `garbled_text`. Native tables,
multi-column layouts, and all-caps legal/agency pages legitimately score low on a
word list. `garbled_text` requires the low hit-rate to **co-occur** with a
char/glyph-density or token-structure anomaly, and only **above a minimum
alphabetic-token floor** (too few tokens → `uncertain_review`, not a verdict). The
bundled list is a small, deterministic, pluggable **common-word sanity check** —
not a real dictionary.

### 2.5 OCR composition — RapidOCR default; OCRmyPDF = a thin Tesseract-gated seam
- **RapidOCR-via-Docling** is the guaranteed OCR engine (pure Python + ONNX, no
  system binaries; ~5.7 s/scanned page — acceptable per the gate).
- **OCRmyPDF** is an **optional seam only** for this phase: a dependency
  **detect** (Tesseract present?), a thin **interface**, and a **warn/skip + flag**
  path when absent. **No** deskew-vs-redo decision engine (Tesseract is absent on
  the build box; an untestable heuristic matrix is YAGNI). When OCRmyPDF is
  unavailable, rotated/skewed/garbled pages that would benefit from it are
  **flagged for review** rather than silently mishandled.

### 2.6 Source identity is part of provenance (Codex)
Preserving geometry without artifact identity is half a provenance. `IngestResult`
carries **`source_path` + `source_sha256`** (and the `DoclingDocument`
`schema_name`+`version`), so a citation ties back to *which* file and *which*
schema produced it — the seam to `archive-evidence` (Phase 9) and the §7 citation
contract's `doc_id`.

### 2.7 Preserve, don't fix (decoupling)
Live text under black redaction boxes is **preserved as-is** — `ingest` does not
"repair" or strip it; `redaction-check` (Phase 7) reasons about box-over-live-text
later. `ingest` shares no code with the Track-A analysis modules.

---

## 3. Architecture — pure core, Docling at the edge

```
PDF ──> ingest.py (edge)
          │  1. source_sha256 + size/large-doc guards
          │  2. Docling pass #1: do_ocr=False  ──> per-page native text + parse_score
          │                                         (reused as the final doc if native)
          ▼
        ingest_gate.py (PURE)
          │  diagnose each page  ──> [PageDiagnosis]
          │  rollup (conservative) ──> DocDecision
          ▼
        ingest.py (edge)
          │  apply DocDecision:
          │    native            -> reuse pass #1 doc
          │    ocr_images        -> Docling do_ocr=True, force=False
          │    force_full_doc_ocr-> Docling do_ocr=True, force_full_page_ocr=True
          │    review            -> minimal extract + flag (no silent OCR)
          │  + OCRmyPDF seam (Tesseract-gated; detect/skip/flag)
          │  3. Bates regex post-pass  (separate, keeps {page_no,bbox})
          │  4. degraded-page flags from res.confidence + diagnoses
          ▼
        DoclingDocument JSON (internal)  +  IngestResult (provenance summary)
```

- **`ingest_gate.py` is pure** (text strings + numeric signals + a bundled
  wordlist data file; stdlib `re` only). No docling/PDF imports → golden-testable
  without a model. This is the same purity stance as `stats`/`derive`/`pii_sweep`'s
  core.
- **`ingest.py` is the only docling importer.** It owns the Docling calls, the
  source hash, the convert() guards, the Bates pass, and the flagging. Docling's
  layout/OCR models load lazily on first convert (the heavy-edge boundary, like
  `SpacyPersonClassifier` in `pii_sweep`).

---

## 4. The gate algorithm (diagnose → rollup)

**Per-page diagnosis** `diagnose_page(native_text, *, parse_score=None,
lang=...) -> PageDiagnosis` (pure):
- `native_char_count` ≈ 0 (below a small floor) → **`image_only`** (scanned page).
- enough chars, but `alphabetic_token_count` below the **min-token floor** →
  **`uncertain_review`** (not enough signal to judge; e.g. a sparse form page).
- enough tokens, but a **low wordlist-hit-rate co-occurring with a char/glyph-
  density anomaly** → **`garbled_text`** (present-but-garbage text layer).
- otherwise → **`native_ok`** (includes legitimately low-hit-rate native tables /
  multi-column / all-caps pages — the density check clears them).
- handwriting-like / contradictory signals → **`uncertain_review`** (never
  "force OCR harder").

**Doc rollup** `decide_doc(diagnoses) -> DocDecision` (pure, conservative §2.3):
counts the diagnoses; escalates doc-wide only on a substantial `image_only` /
`garbled_text` share; otherwise keeps `native` and flags the minority bad pages;
`review` when `uncertain_review` dominates. Thresholds are module constants tuned
in the plan against fixtures (no magic numbers buried in branches).

Both functions are deterministic, JSON-able, and take no IO/clock/network.

---

## 5. Output contract

`ingest.py` writes the **`DoclingDocument` JSON** (the internal artifact;
`save_as_json`, never Markdown) and returns an **`IngestResult`**:

```
IngestResult = {
  source_path: str,
  source_sha256: str,                  # artifact identity (Codex)
  docling_json_path: str,
  schema_name: str, schema_version: str,   # "DoclingDocument", "1.10.0"
  n_pages: int,
  doc_decision: "native"|"ocr_images"|"force_full_doc_ocr"|"review",
  ocr_engine_used: "none"|"rapidocr"|"ocrmypdf+rapidocr",
  per_page: [ {page_no, native_chars, hit_rate, diagnosis,
               ocr_applied: bool, confidence_grade, flagged: bool, flag_reason} ],
  bates: [ {value, page_no, bbox} ],   # captured SEPARATELY, keeps provenance
  warnings: [str],                     # e.g. "OCRmyPDF unavailable: Tesseract absent"
}
```

- **Bates post-pass:** a word-boundary-anchored regex over the `DoclingDocument`
  text items captures Bates stamps **separately** with each one's `{page_no, bbox}`
  (from `item.prov`). Leads-not-verdicts: capture + tag, never rewrite the text.
- **Degraded-page flagging:** `flagged` is set from `res.confidence`
  (`ocr_score`/`mean_grade`/`low_grade` per page; `nan` handled per modality) plus
  the gate's `uncertain_review`/`image_only`-without-OCR diagnoses. Flagged pages
  are surfaced for a human; nothing degraded is published as fact.

---

## 6. Testing (all SYNTHETIC fixtures; `mise run test`)

**Pure gate — golden tests (no PDF/model)** over crafted text strings →
expected `PageDiagnosis` + `DocDecision`:
- clean native text → `native_ok`; image-only (empty/near-empty) → `image_only`;
  garbled (mojibake/low-hit + density anomaly) → `garbled_text`; sparse form →
  `uncertain_review`.
- **false-positive guards (Codex):** native **table** page, **multi-column** page,
  **all-caps legal/agency** page → `native_ok` (NOT `garbled_text`); repeated
  **header/footer/Bates boilerplate** pages → do not spuriously avoid OCR or
  trigger full-doc OCR.
- **conservative rollup:** a **mixed-doc** diagnosis list (mostly `native_ok` + 1–2
  `garbled_text`/`image_only`) → `DocDecision = native` + those pages flagged
  (NOT `force_full_doc_ocr`); a heavily-bad list → escalates.

**Docling edge — integration tests behind a `docling` pytest marker** (model-gated,
like the `spacy` marker; selected with `-k docling`):
- clean-digital fixture (fpdf2) → gate skips OCR, `native`.
- image-only scan fixture (Pillow) → OCR engaged, items carry `{page_no, bbox,
  charspan}`.
- garbled-text-layer fixture → `force_full_doc_ocr`.
- Bates-stamped fixture → Bates captured separately with provenance.
- degraded/low-confidence fixture → page flagged.
- mixed-doc fixture → conservative `native` + flag (the §2.3 rule end-to-end).
- **OCRmyPDF seam** tested by mocking `MissingDependencyError` → warn/skip/flag
  (no live Tesseract needed).

`pyproject.toml` gains a `docling` marker (mirrors `spacy`).

---

## 7. Rigor guardrails (publish-critical)

- **`DoclingDocument` JSON internal, never Markdown** — Markdown drops the
  bbox/charspan a citation needs.
- **Gate before OCR** — don't OCR a clean layer; don't trust a garbled one.
- **Conservative rollup** — never escalate a mostly-native doc to full OCR on a
  couple of bad pages.
- **Wordlist is a weak signal** — a low hit-rate alone is never a verdict
  (tables/multi-column/all-caps are native).
- **Flag, don't fake** — handwriting / degraded / `uncertain` pages go to a human;
  nothing degraded is emitted as fact (leads-not-verdicts).
- **Preserve, don't fix** — live text under redaction boxes is kept for Phase 7.
- **Artifact identity** — `source_sha256` ties geometry to the source file.
- **Pins untouched** — numpy 2.4.6 / pandas 3.0.3 unchanged by the heavy stack
  (verified at the gate).
- **Synthetic fixtures only** — the real Simpsonville corpus is wired in at
  Task 11.2 behind an env var, never committed.

---

## 8. Future / not-now notes

- **Per-page selective force-OCR** (re-OCR only the bad pages of a mostly-native
  doc) — a future enhancement; v1 flags them.
- **OCRmyPDF deskew/redo decision engine** — when Tesseract is a supported
  dependency (setup-doctor, Phase 10), grow the seam into the two real modes.
- **`docling-slim` footprint trim** — drop the unused chunking/transformers stack;
  measure + adopt later (not while it risks the OCR path).
- **Offline model prefetch** (`artifacts_path` + `docling-tools models download`)
  — owned by setup-doctor (Phase 10); `ingest` only fails fast with a clear
  message if models are missing offline.
- **Repeated-boilerplate suppression** — v1 proves via fixtures that boilerplate
  doesn't break the gate; active header/footer suppression is a later refinement.

---

## 9. Brainstorm provenance

Brainstormed 2026-06-04 (autonomous mode; Codex stand-in for Tim, critic
disposition, threaded on the Phase-6 Codex chain). The proposed design was sent
in one pass; Codex confirmed the big architecture (two-module split, reuse the
`do_ocr=False` pass over a `pdfminer` pre-pass, RapidOCR-default + OCRmyPDF-seam,
internal JSON, default backend, full `docling`) and pushed back with the
conditions now folded in (§2.2 gate-API split, §2.3 conservative rollup, §2.4
wordlist-as-weak-signal + table/multi-column/all-caps fixtures, §2.5 minimal
OCRmyPDF seam, §2.6 source identity, §2.7 preserve-don't-fix, handwriting→review).
Convergence signal: *"If you tighten the gate API, add source-identity provenance,
and pin the boilerplate/table false-positive cases, looks good, write the plan."*
All conditions adopted.

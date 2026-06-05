---
name: ingest
description: This skill should be used when the user asks to "ingest a PDF / document", "extract text from a scanned PDF preserving page and bounding-box provenance", "convert a FOIA PDF to DoclingDocument keeping citations", "OCR a scanned document but decide native-text-vs-re-OCR first", "turn a PDF into provenance-preserving structured text for investigation", or otherwise wants the document/PDF path that produces a provenance-preserving DoclingDocument (page + bbox + charspan) with a text-layer quality gate before any OCR. This is the document path; the CSV/XLSX structured-data path is `dataset-analyze`.
version: 0.1.0
---

# ingest

Turn a PDF into a **`DoclingDocument` JSON kept internally** (never Markdown) so
every extracted element keeps its `{page_no, bbox, charspan}` provenance for the
Phase-8 citation anchor. A **pure text-layer quality gate** decides
native-text-vs-re-OCR **before** any OCR runs; degraded / handwriting pages are
**flagged for a human**, never silently OCR'd as fact.

This is the suite's **document/PDF** path. The structured-data (CSV/XLSX) path is
`dataset-analyze` (`load_table` + `data_quality`); `ingest` does not duplicate it.

Two modules, mirroring the suite's pure-core / engine-at-the-edge split (like
`pii_sweep`):

- `scripts/ingest_gate.py` — the **PURE** gate (stdlib only; golden-testable with
  no model): per-page `diagnose_page(...)` → `decide_doc(...)` conservative rollup.
- `scripts/ingest.py` — the **Docling edge** (the only docling importer; imports
  it lazily): `ingest(pdf_path, *, out_dir, ...)` → writes the `DoclingDocument`
  JSON and returns an `IngestResult`.

The verified Docling / RapidOCR / OCRmyPDF facts (the API surface, the
coordinate-origin trap, the confidence/`nan` semantics, the CPU-latency budget)
live in `references/prior-art.md` (the Phase-6 research gate) — consult it before
changing a convert call, a backend, or a confidence threshold.

## The pipeline

Call one function; it runs the gate and applies the decision.

```python
from scripts.ingest import ingest

result = ingest(
    pdf_path,            # the source PDF
    out_dir=work_dir,    # where the DoclingDocument JSON is written
    deskew=False,        # OCRmyPDF preprocess (Tesseract-gated; see the seam)
)
```

Internally (design §3):

1. **Source identity** — SHA-256 the file (`source_sha256`). Provenance is
   geometry **and** artifact identity: a citation ties back to *which* file.
2. **Pass #1, `do_ocr=False`** — one Docling parse over the native text layer.
   Its per-page native text + `parse_score` are what the gate sees, and the doc
   is **reused** if the decision is `native` (no second parse).
3. **Gate (pure)** — `diagnose_page` labels each page `native_ok` / `image_only` /
   `garbled_text` / `uncertain_review`; `decide_doc` rolls those up conservatively
   into a doc decision: `native` / `ocr_images` / `force_full_doc_ocr` / `review`.
4. **Apply the decision** — `native` reuses pass #1; `ocr_images`
   (`do_ocr=True, force_full_page_ocr=False`) lets Docling OCR the image regions;
   `force_full_doc_ocr` (`force_full_page_ocr=True`) overrides a present-but-bad
   text layer; `review` never silently OCRs. OCR uses **RapidOCR** (no system
   binaries).
5. **Normalize provenance + persist** — force every item's prov bbox to a single
   `coord_origin`, then `save_as_json` (never Markdown).
6. **Bates post-pass + degraded flags** — capture Bates stamps separately
   (keeping `{page_no, bbox}`), and flag degraded / low-confidence / uncertain
   pages from `res.confidence` + the gate diagnoses.

`ingest` returns an `IngestResult` (design §5): `source_path`, `source_sha256`,
`docling_json_path`, `schema_name`/`schema_version`, `n_pages`, `doc_decision`,
`trustworthy_for_extraction`, `ocr_engine_used`, `per_page[]` (each with
`diagnosis`, scores, `flagged`, `flag_reason`), `bates[]`, and `warnings[]`.

## The `review` contract (safety-critical — design §5)

When `decide_doc` returns `review` (handwriting / garbled / weak-signal pages
dominate), `ingest` **still writes** the `DoclingDocument` JSON — for a human to
inspect — but sets **`trustworthy_for_extraction = false`** and flags every page.

**Downstream consumers (`investigate` / citation, Phase 8) MUST refuse to
auto-extract claims or citations from a doc with
`trustworthy_for_extraction == false`.** A `review` doc is evidence-for-inspection,
never an automated-extraction source. This is the citation-discipline seam: a
garbled or handwritten artifact routes to the human gate first.

## The OCRmyPDF seam (Tesseract-gated — design §2.5)

RapidOCR-via-Docling is the guaranteed OCR engine (pure Python + ONNX, no system
binaries). **OCRmyPDF is an optional preprocess only** (deskew; `--redo-ocr` /
`--force-ocr` to replace a poisoned text layer), and it requires **Tesseract** on
PATH (Ghostscript only for PDF/A). `ingest` **detects and skips** when Tesseract
is absent: the requested op is not run, a `warning` is appended, and the affected
pages are **flagged for review** — never a crash, never a silent skip. Installing
Tesseract is a `setup-doctor` (Phase 10) concern; the seam grows into the real
deskew/redo modes there.

## Rigor guardrails (preserve across ingest)

- **`DoclingDocument` JSON is the internal form — never Markdown.** Markdown drops
  the `bbox` / `charspan` a citation needs. Keep the JSON; export Markdown only as
  a throwaway human view, never as the stored artifact.
- **Gate before OCR.** Decide native-vs-re-OCR on the cheap `do_ocr=False` pass
  first. Don't burn OCR on a clean text layer; don't trust a garbled one.
- **Conservative rollup.** A couple of bad pages in a mostly-native brief stays
  `native` and flags those pages — never flip a 200-page digital doc to full OCR
  for one ugly page.
- **Flag, don't fake.** Handwriting / degraded / `uncertain` pages go to a human
  (`flagged` + `flag_reason`); nothing degraded is emitted as trustworthy fact.
- **`review` docs are not auto-extractable** (`trustworthy_for_extraction=false`).
- **Preserve, don't fix.** Live text under black redaction boxes is kept as-is;
  `redaction-check` (Phase 7) reasons about box-over-live-text — `ingest` never
  "repairs" or strips it.
- **`nan` is N/A, never 0.** A native page has `ocr_score == nan`; an OCR'd scan
  has `parse_score == nan`. Never treat a missing-modality score as a low score —
  a clean page must not be flagged for it.
- **Synthetic fixtures only.** The gate + edge are tested against fpdf2 / Pillow
  fixtures; the real corpus is wired in at Task 11.2 behind an env var, never
  committed.

## Downstream consumers

- **`investigate`** (Phase 8) — builds the citation anchor from the preserved
  `{page_no, bbox, charspan}`; refuses non-trustworthy (`review`) docs.
- **`redaction-check`** (Phase 7) — operates on the same PDFs / text layer to find
  bad redactions (box-over-live-text), which `ingest` deliberately preserved.
- **`entity-extract`** (Track B, Layer 2) — consumes the per-span provenance.

`ingest` shares **no code** with the Track-A analysis modules (`stats`,
`load_table`, `derive`, `recipe`, `pii_sweep`); it is the document path, they are
the structured-data path. The only contract between them is the eventual Librarian
findings output and the §7 citation schema.

## Resources

- **`references/prior-art.md`** — the Phase-6 research gate: verified Docling 2.97
  API (`save_as_json`, `prov`/`bbox`/`charspan`, `ConfidenceReport`,
  `RapidOcrOptions`), the coordinate-origin trap, the `nan` semantics, the
  CPU-latency budget, and the OCRmyPDF system-binary gate. Consult before changing
  a convert call, a backend, or a threshold.
- **`scripts/ingest_gate.py`** — the pure gate: `diagnose_page`, `decide_doc`, the
  signal helpers, the named thresholds, and the injectable wordlist.
- **`scripts/ingest.py`** — the Docling edge: `ingest`, `sha256_file`,
  `IngestResult`, the OCR branches, prov normalization, the Bates pass, and the
  `_tesseract_available` seam (detailed docstrings).
- **`dataset-analyze` skill** — the sibling structured-data (CSV/XLSX) path; not
  duplicated here.

# Phase 6 — `ingest` research gate (prior-art)

**Status:** verified-facts-only. The ingest *algorithm*, file contract, and test
plan live in the Phase-6 implementation plan (`docs/plans/`). This document
records what was empirically verified at the gate so the 6.1/6.2/6.3 implementers
build against real APIs, not training-time guesses.

**Method:** Context7 (`/docling-project/docling`, `/docling-project/docling-core`,
`/docling-project/docling-parse`, `/ocrmypdf/ocrmypdf`, `/rapidai/rapidocr`) +
an empirical install/introspection pass in the project `.venv`
(Python 3.12.10, Windows). No real corpus touched — fixtures are synthetic.

**Decision deferral:** engine *composition* choices (RapidOCR-only vs
OCRmyPDF-preprocess; Docling footprint; gate thresholds) are flagged in §9 as
brainstorming inputs. This gate does **not** lock them.

---

## 1. Engine stack & pinned versions (empirical)

Installed into the `.venv` on top of the existing Phase 0–5 pins. **numpy 2.4.6
and pandas 3.0.3 were left UNTOUCHED** (pip reported "Requirement already
satisfied" for both — the heavy ingest stack constrains `numpy<3,>=1.24` and
`pandas<4,>=2.1.4`, both satisfied by our pins). This repeats the Phase-5 rigor
invariant: a heavy ML dep must not silently move the numeric core.

| Package | Version | Role |
|---|---|---|
| `docling` | 2.97.0 | convenience metapackage → `docling-slim[standard]` |
| `docling-core` | 2.78.1 | `DoclingDocument` model, provenance types, JSON (de)serialization |
| `docling-parse` | 6.2.0 | PDF backend — char/word/line cell geometry → bbox provenance |
| `docling-ibm-models` | 3.13.3 | layout (RT-DETR) + TableFormer models (torch) |
| `rapidocr` | 3.8.1 | **OCR engine Docling 2.97 actually uses** (`rapidocr<4,>=3.8`) |
| `onnxruntime` | 1.26.0 | CPU inference backend for RapidOCR |
| `torch` / `torchvision` | 2.12.0 / 0.27.0 | layout/table model runtime (CPU wheel, 123 MB) |
| `transformers` / `tokenizers` | 5.10.2 / 0.22.2 | pulled by `[standard]` chunking extra (NOT used by ingest) |
| `ocrmypdf` | 17.5.0 | deskew / re-OCR preprocessing (Python pkg; needs system binaries — §4) |
| `pikepdf` / `pdfminer.six` | 10.7.2 / 20260107 | OCRmyPDF PDF plumbing + a native text-layer reader |
| `pypdfium2` | 5.9.0 | PDF rasterization (also an alternate Docling backend) |
| `pillow` | 12.2.0 | imaging (also used to build the synthetic "scan" fixture) |
| `opencv-python` | 4.13.0.92 | pulled by `rapidocr` |
| `fpdf2` / `img2pdf` | 2.8.7 / 0.6.3 | OCRmyPDF text-layer render (also build the native-text fixture) |

**Pin guidance for `requirements-dev.txt`:** pin the four *top-level* packages
(`docling==2.97.0`, `rapidocr==3.8.1`, `onnxruntime==1.26.0`, `ocrmypdf==17.5.0`)
plus an explicit `torch==2.12.0` (CPU) so a rebuild can't drift onto a CUDA
wheel. Do **not** pin `rapidocr-onnxruntime` — it is the *legacy* package and
Docling 2.97 does not use it (it appeared in the first install only because it
was requested explicitly; it should be omitted). Do not pin transitive deps
(consistent with how Phase 5 pinned `spacy` + the model, not `thinc`/`blis`).

**Footprint reality (heavier than spaCy was):** the heavy wheels are torch
(123 MB) + scipy (36 MB) + opencv (40 MB) + transformers (11 MB) + docling-parse
(11 MB) + rapidocr (15 MB) + onnxruntime (13 MB), plus first-run model
downloads from HuggingFace (layout/TableFormer) and the RapidOCR ONNX models.
Budget ~1.5–2 GB of venv + model cache. `mise run bootstrap` will pull all of
it. **Footprint option (→ §9):** `docling` = `docling-slim[standard]`, and
`[standard]` adds the *chunking* stack (transformers, tree-sitter ×4, accelerate,
semchunk, mpire) that ingest never calls. A narrower install
(`docling-slim` + only the parse/OCR extras) likely trims hundreds of MB; to be
evaluated in the plan, not silently adopted.

---

## 2. Docling API (verified)

### 2.1 Convert + OCR engine selection

```python
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
from docling.datamodel.base_models import InputFormat

opts = PdfPipelineOptions(do_ocr=True)                 # do_ocr gates OCR entirely
opts.ocr_options = RapidOcrOptions(force_full_page_ocr=True)   # RapidOCR backend
conv = DocumentConverter(
    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
)
res = conv.convert("input.pdf")           # -> ConversionResult
doc = res.document                        # -> DoclingDocument
```

- `RapidOcrOptions` is the lightweight, **no-C-dependency** OCR engine
  (Context7: "RapidOCR (lightweight, no C deps)"). Alternatives in 2.97:
  `TesseractOcrOptions`, `EasyOcrOptions`, `OcrMacOptions`. RapidOCR is the
  design's default (§10 prior-art delta: Tesseract→RapidOCR).
- `force_full_page_ocr=True` makes OCR run over the **whole page** as one
  rectangle (verified in `base_ocr_model.py`); `False` OCRs only detected bitmap
  regions above a coverage threshold. For re-OCRing a scanned page, force-full.
- `do_ocr=False` runs the **native-text path** (no OCR) — this is the
  text-layer-good branch of the quality gate.
- `RapidOcrOptions` fields (verified): `lang`, `force_full_page_ocr`,
  `text_score`, `bitmap_area_threshold`, `backend`, `use_det`/`use_cls`/`use_rec`,
  and model-path overrides (`det_model_path`/`cls_model_path`/`rec_model_path`/
  `rec_keys_path`), plus `rapidocr_params`. **`lang` matters:** RapidOCR 3.8.1's
  default models are the `ch_PP-OCRv4` set (Chinese+Latin/English/digits — fine
  for English FOIA text, but the plan should set `lang` explicitly and consider a
  dedicated English recognition model for accuracy).
- `PdfPipelineOptions` fields (salient, verified): `do_ocr`, `ocr_options`,
  `do_table_structure`, `table_structure_options`, `layout_options`,
  `force_backend_text` (prefer native backend text over OCR where present),
  `generate_parsed_pages` (retain page cell geometry), `accelerator_options`
  (CPU thread count / device), `document_timeout`, `images_scale`. Ingest will
  likely **disable** `do_table_structure`/enrichments it doesn't need (latency).

### 2.2 DoclingDocument → JSON (NEVER Markdown internally)

Verified canonical persistence (keeps page+bbox; Markdown would discard it):

```python
doc.save_as_json(path)                      # write JSON
DoclingDocument.load_from_json(path)        # reload (classmethod)
# equivalently, the Pydantic-explicit form the docs recommend for consistency:
import json
json.dump(doc.export_to_dict(), open(path, "w"))
doc2 = DoclingDocument.model_validate(json.load(open(path)))
```

- The internal store is the `DoclingDocument` JSON. `export_to_markdown()` exists
  but is an **output-only** view — ingest must not round-trip through it
  (it drops `prov`/bbox, breaking the citation anchor in Phase 8).
- Verified: `save_as_json` writes `schema_name: "DoclingDocument"`,
  `version: "1.10.0"`; top-level keys are `body, texts, tables, pictures, groups,
  pages, form_items, key_value_items, furniture, name, origin, schema_name,
  version`. Text items live under `texts` (each with `prov`); page sizes under
  `pages`. Record `(schema_name, version)` with each ingested doc so a future
  schema bump is detectable.

### 2.3 Provenance: page number + bounding box + char span (verified)

From `docling-core` (`types/doc/document.py`, `types/base.py`):

```python
class ProvenanceItem(BaseModel):
    page_no: int                 # 1-based page number
    bbox: BoundingBox
    charspan: CharSpan           # (start, end) offsets into the item's text

class DocItem(NodeItem):         # base of TextItem/TableItem/PictureItem/...
    label: DocItemLabel
    prov: list[ProvenanceItem] = []

class BoundingBox(BaseModel):
    l: float; t: float; r: float; b: float
    coord_origin: CoordOrigin = CoordOrigin.TOPLEFT     # or BOTTOMLEFT
```

- Iterate everything with `for item, level in doc.iterate_items(): item.prov`.
- A bbox can be normalized origin via `bbox.to_bottom_left_origin(page_height)`
  (page size is on `doc.pages[page_no].size`).
- **OCR cells carry their own quality signal:** each OCR `TextCell` is built with
  `from_ocr=True` and a per-cell `confidence`, and is dropped if
  `confidence < confidence_threshold` (verified in the OCR model code). This is a
  per-cell provenance flag the degraded-page logic (Task 6.2) can read.
- This `{page_no, bbox, charspan}` triple is the seam to the **§7 citation-anchor
  fallback chain** (char-offset → text-hash → block-index → page) that Phase 8
  `investigate` builds — ingest must preserve it losslessly.

### 2.4 Confidence / degraded-page signal (the "flag for humans" mechanism)

Docling computes a per-document + per-page **confidence report** on the
`ConversionResult` (`res.confidence`), the programmatic hook for the design's
"flag handwriting / degraded pages for review, don't silently OCR garbage."

- Verified: `res.confidence` is a `ConfidenceReport`
  (`docling.datamodel.document.ConfidenceReport`) with float scores
  `parse_score`, `layout_score`, `table_score`, `ocr_score`; rolled-up
  `mean_grade` / `low_grade` (a `QualityGrade` enum — observed
  `QualityGrade.EXCELLENT`; the enum carries the usual GOOD/FAIR/POOR grades);
  and `pages: {page_no -> PageConfidenceScores}` with the same per-page fields.
- **`nan` semantics (load-bearing for the gate):** `parse_score` is `nan` for an
  image-only scan (no native layer) and `ocr_score` is `nan` for a native-text
  page (no OCR). The gate must branch per score type — never treat `nan` as `0`.
  On the clean synthetic scan: `ocr_score≈0.98`, `layout_score≈0.93`,
  `mean_grade=EXCELLENT`.
- Design use: a page whose `low_grade`/`mean_grade` is POOR (or whose `ocr_score`
  is low / whose native char-count is near-zero) is **flagged for a human**, not
  trusted. Handwriting/degraded scans surface as low `ocr_score`.

### 2.5 PDF backend

- Verified importable: `DoclingParseV4DocumentBackend`,
  `DoclingParseV2DocumentBackend`, `DoclingParseDocumentBackend` (v1), and
  `PyPdfiumDocumentBackend`. `DoclingParseV4` is the current default in 2.97.
- Backend is set per-format via `PdfFormatOption(backend=...)`. The
  `docling-parse` backends are what yield char/word/line cell geometry; pypdfium2
  is a lighter alternative. Backend choice → §9 (charspan fidelity matters for
  citations).

---

## 3. RapidOCR (verified)

- **Package identity resolved empirically:** Docling 2.97 depends on
  `rapidocr` (the new unified 2.x/3.x package), resolved to **3.8.1**, which pulls
  `onnxruntime`, `opencv-python`, `pyclipper`, `shapely`. The **legacy**
  `rapidocr-onnxruntime` (1.4.4) is a *different* distribution Docling 2.97 does
  **not** import — omit it.
- **No system binaries** — pure Python + ONNX runtime (CPU). This is the
  works-out-of-the-box OCR path on a laptop, in contrast to OCRmyPDF (§4).
- Models: Docling manages RapidOCR model download (the `onnxruntime` backend,
  English) on first OCR use; `docling-tools models download` can pre-fetch.
- **CPU latency — the §11 risk — measured on a synthetic single page (this box,
  CPU-only):**
  - **cold convert** (first run: layout `docling-layout-heron` + RapidOCR ONNX
    model download + load + first inference): **~95 s** (one-time).
  - **warm native (no-OCR) convert:** **~1.3 s/page** (layout runs; no OCR).
  - **warm native, `do_ocr=True` (auto-skips covered text):** **~1.9 s/page**.
  - **warm scanned page, `force_full_page_ocr=True`:** **~5.7 s/page**.
  - **Verdict: ACCEPTABLE for laptop-local, single-document ingest.** A 50-page
    native PDF ≈ ~1 min; a 50-page scanned PDF ≈ ~5 min of OCR; the cold first run
    pays a one-time ~95 s for model fetch+load. Caveats: (a) latency scales
    ~linearly with OCR'd page count + text density — large scanned batches are a
    Workflow-fan-out + progress-reporting concern, not a per-doc blocker;
    (b) real scans (noise, more regions) run slower than this clean synthetic
    page; (c) `accelerator_options` (thread count) can tune throughput.
- **OCR granularity caveat:** with `force_full_page_ocr=True` the whole page
  merged into a single text item with run-together text
  (`'FOIA RESPONSE--SimpsonvillePoliceDepartmen…'`, `charspan (0,601)`). For
  per-line cells + tighter bboxes (better citations), evaluate
  `force_full_page_ocr=False` (OCR detected regions) in the plan.
- **Provenance verified on the OCR path:** the OCR'd text item carried `page=1`,
  `bbox=(l=43,t=747,r=391,b=528, origin=BOTTOMLEFT)`, `charspan=(0,601)` —
  page+bbox+charspan survive OCR into the JSON, as the citation anchor requires.

---

## 4. OCRmyPDF (verified) — and the **system-binary gate**

OCRmyPDF is the design's deskew / re-OCR preprocessor (§5.1). The Python package
(`ocrmypdf==17.5.0`) installed cleanly, **but it requires external system
programs that are ABSENT on this box** (verified via `Get-Command`):

| Binary | Needed for | Present here? |
|---|---|---|
| `tesseract` | OCR (required for any run) | **MISSING** |
| `ghostscript` (`gswin64c`) | PDF rasterization + PDF/A | **MISSING** |
| `qpdf` (via pikepdf) | PDF repair | MISSING (pikepdf vendors libqpdf — ok) |
| `unpaper` | `--clean` / `--clean-final` only | MISSING (optional) |
| `pngquant` / `jbig2enc` | `--optimize 2/3` only | MISSING (optional) |

⇒ **OCRmyPDF cannot run here until Tesseract + Ghostscript are installed**
(Windows: `winget install UB-Mannheim.TesseractOCR` + `winget install
ArtifexSoftware.GhostScript`, or `choco install tesseract ghostscript`). This is
a setup-doctor (Phase 10) concern; ingest must **detect-and-skip** OCRmyPDF when
the binaries are absent rather than crash.

**Policy flags (mutually exclusive — verified):**

| Flag / kwarg | Behavior |
|---|---|
| `--skip-text` / `skip_text=True` | leave pages that already have text untouched |
| `--redo-ocr` / `redo_ocr=True` | strip existing OCR text, OCR again (best for a *bad* existing text layer / mixed digital+scanned) |
| `--force-ocr` / `force_ocr=True` | rasterize **all** content and OCR (failed prior OCR, watermarks) |
| `--deskew` / `deskew=True` | straighten skewed scans (Leptonica; no `unpaper` needed) |

**Python API (verified signature):** `ocrmypdf.ocr(input_file_or_options,
output_file=None, *, language=None, image_dpi=None, output_type=None,
sidecar=None, jobs=None, deskew=…, redo_ocr=…, force_ocr=…, skip_text=…, …)` —
the first arg accepts **either** a path **or** an `OcrOptions` object (modern
form). `from ocrmypdf.exceptions import MissingDependencyError` imports cleanly
(verified) and is raised when Tesseract/Ghostscript are absent — the catchable
signal for the detect-and-skip seam.

**Architectural note (→ §9):** OCRmyPDF (Tesseract) and Docling+RapidOCR are two
OCR engines. The coherent split: **RapidOCR-via-Docling = the guaranteed OCR
path** (no system deps); **OCRmyPDF = optional preprocessing** (deskew, and
`--redo-ocr` to fix a poisoned text layer) used only when its binaries exist.
Output of OCRmyPDF is a new PDF Docling then ingests (`do_ocr=False`, since the
text layer is now clean).

---

## 5. docling-parse (verified)

- `docling-parse==6.2.0` — extracts text, paths, and bitmaps **with coordinates**
  from programmatic PDFs at **character, word, and line** granularity (Context7
  summary; confirmed dependency of `docling`). It is the backend that produces
  the cell geometry Docling assembles into `prov.bbox` + `charspan`. The
  char-level boxes are what make the citation anchor robust to OCR re-runs.

---

## 6. Text-layer quality gate — Task 6.1 design inputs (verified primitives)

The gate decides **native-text vs re-OCR before OCRing**. Verified building
blocks (no new heavy dep needed):

- **chars-per-page:** count extractable native-text characters per page. Source
  options: `pdfminer.six` (already installed via OCRmyPDF) `extract_text` per
  page, or a Docling `do_ocr=False` parse + sum of native (non-`from_ocr`) cell
  text. A near-zero count ⇒ image-only scan ⇒ must OCR.
- **dictionary-hit-rate:** tokenize the native text, compute the fraction of
  alphabetic tokens found in an English word list. A *present but garbled* text
  layer (mojibake, bad embedded OCR) scores low and forces re-OCR even though
  chars-per-page is high. **[→ §9] wordlist source** — a small bundled list, or
  Python's `str` heuristics; must stay dependency-light and deterministic.
- **why this gate is needed (not redundant with Docling):** with `do_ocr=True`,
  Docling only OCRs *bitmap regions not covered by programmatic text* — it
  **trusts a present text layer even if it is garbage**. Our dictionary-hit-rate
  gate is exactly what catches a garbled-but-present layer and forces a
  `force_full_page_ocr` re-OCR. (Empirically the native PDF with `do_ocr=True`
  finished in ~1.9 s having skipped OCR — confirming Docling won't re-OCR a
  present-but-bad layer on its own.)
- **optional cross-check:** Docling's own `parse_score` / `ocr_score` /
  OCR-cell confidence (§2.4) corroborates the heuristic.
- Thresholds are set in the plan; tests use a **clean-digital** fixture (gate →
  skip OCR) and a **garbled-text-layer** fixture (gate → force re-OCR), both
  synthetic.

---

## 7. Bates numbering — Task 6.2 post-pass (reference)

Bates numbers are sequential legal-document identifiers stamped per page
(e.g. `SVPD-000123`, `ABC 0001234`, `DEF_00045`). After extraction, a regex
post-pass over item text captures them **separately** while keeping each label's
`{page_no, bbox}` provenance (so a citation can point at the stamp). Pattern
shape: an alphanumeric prefix + separator + a zero-padded run of digits,
word-boundary anchored. Leads-not-verdicts: capture and tag, never rewrite the
underlying text.

---

## 8. Rigor invariants carried into Phase 6

- **DoclingDocument JSON is the internal form — never Markdown** (Markdown drops
  the bbox/charspan a citation needs).
- **Quality gate runs BEFORE OCR** (don't burn CPU OCRing a clean text layer;
  don't trust a garbled one).
- **Flag handwriting / degraded pages for humans** via the confidence signal
  (§2.4); never silently emit OCR garbage as fact.
- **numpy/pandas pins untouched** by the heavy install (verified §1).
- **Synthetic fixtures only**; the real Simpsonville corpus is wired in at
  Task 11.2 behind an env var, never committed.

---

## 9. Open questions → brainstorming (NOT decided at this gate)

1. **OCR composition.** Recommend **RapidOCR-via-Docling as the default,
   no-system-dep OCR path**, with **OCRmyPDF deskew/`--redo-ocr` as an optional
   preprocess gated on a Tesseract+Ghostscript presence check** (detect-and-skip,
   flag when unavailable). Confirm in brainstorm.
2. **Docling footprint.** Full `docling` (`-slim[standard]`, pulls chunking/
   transformers) vs a narrower `docling-slim` + parse/OCR extras. Measure the MB
   delta; decide whether to trim.
3. **Dictionary source** for the hit-rate heuristic (bundled list vs heuristic;
   keep deterministic + light).
4. **PDF backend default** (`DoclingParseV4` vs `V2`) — which yields the most
   reliable `charspan` for the citation anchor.
5. **Where the gate reads native text** (pdfminer.six vs a Docling no-OCR pass) —
   avoid double-parsing the PDF if one source suffices.
6. **Per-page vs per-doc OCR decision** — the gate may need to re-OCR only the
   bad pages, not the whole document.

---

## 10. Decoupling & downstream consumers

- `ingest` feeds: `investigate` (Phase 8 — the citation anchor consumes
  `{page_no,bbox,charspan,text}`), `redaction-check` (Phase 7 — operates on the
  same PDFs/text layer), and `entity-extract` (Track B, Layer 2 — per-span
  provenance).
- ingest shares **no code** with the Track-A analysis modules (`stats`,
  `load_table`, `derive`, `recipe`, `pii_sweep`); it is the *document* path, they
  are the *structured-data* path. The only contract between them is the eventual
  Librarian findings output and the §7 citation schema.

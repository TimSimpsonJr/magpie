# Phase 7 research gate -- redaction-check + redact-output (prior-art)

Verified-facts-only. The algorithm / contracts / test-plan live in the Phase-7
**plan** (`docs/plans/2026-06-04-magpie-phase7-*`), not here. This file pins the
empirical library facts, the verified API shapes, the honest limits, the
licensing finding, and the resolved open questions -- the spec the implementers
build against. Mirrors `skills/ingest/references/prior-art.md`.

ASCII-only (SDD subagents content-filter-block on exotic glyphs; see the
`magpie-subagent-ascii-fixtures` rule).

---

## 0. Scope & method

`redaction-check` (input side) finds bad redactions in PDFs -- a layered set of
SIX independent checks, each emitting **flags-as-LEADS, never an "improper
redaction" verdict** (design 5.1 / 7; the `foia-exemptions` flags-not-verdicts
stance). `redact-output` (output side) redacts third-party PII names->initials in
PUBLISHED artifacts and routes full exhibits to a LOCAL non-vault path, consuming
Phase-5 `pii_sweep`'s opt-in `local_texts` (5.2 below).

Every fact below was verified empirically against the project `.venv`
(Python 3.12.10) on 2026-06-04 with a scratch probe that built synthetic PDF
fixtures (fpdf2 / pikepdf / fitz) and ran each check. Probe results are quoted
inline. Context7 (`/pikepdf/pikepdf`, `/pymupdf/pymupdf`) + focused web
(freelawproject/x-ray README + pyproject, PyPI metadata) corroborated the APIs.

---

## 1. Engine stack + pins (verified install, .venv-resolved)

| Lib | Version (resolved) | License | Role | New? |
|---|---|---|---|---|
| `x-ray` | 0.3.6 | BSD-2-Clause | box-over-live-text detector | **NEW** |
| `PyMuPDF` (`fitz`) | 1.24.14 (MuPDF 1.24.11) | **AGPL-3.0** or Artifex commercial | x-ray's hard dep; also used to BUILD fixtures | **NEW** (pulled by x-ray) |
| `pdfminer.six` | 20260107 | MIT | text-layer sweep (text + bbox) | present (via ocrmypdf) |
| `pikepdf` | 10.7.2 | MPL-2.0 | metadata/XMP, /Redact annots, embedded files | present (via ocrmypdf) |
| `pypdfium2` | 5.9.0 | BSD-3 + Apache-2.0 | (not required this phase; available) | present |
| `fpdf2` | 2.8.7 | LGPL-3.0 | **TEST-ONLY** synthetic-fixture builder | present |
| `pillow` | 12.2.0 | HPND/MIT | (not required this phase; available) | present |

- `pip install x-ray` dry-run was **clean**: `Would install PyMuPDF-1.24.14
  x-ray-0.3.6` and touched **nothing** in the pinned heavy stack
  (numpy 2.4.6 / pandas 3.0.3 / torch / onnxruntime / docling all UNTOUCHED).
  `requests`/`charset_normalizer`/`idna`/`urllib3`/`certifi` already satisfied.
- Real install confirmed: `import xray` + `import fitz` both load;
  `xray==0.3.6`, `pymupdf==1.24.14` (MuPDF 1.24.11 rebased). x-ray pins
  `PyMuPDF==1.24.14` exactly in its own metadata, so it controls that pin.
- PyMuPDF wheel is `cp39-abi3` ~16.3 MB -- modest next to the docling/torch
  footprint; CPU-only; no system binaries.
- **NO PyMuPDF<->numpy edge** -- PyMuPDF does not depend on numpy/pandas, so it
  cannot perturb the Phase-5/6 pins.

The license `(none)` shown by some dists in `importlib.metadata` is an unpopulated
pip `License` field (they use SPDX expressions / license files); the licenses in
the table are the authoritative upstream licenses.

---

## 2. x-ray -- verified API + exactly what it detects

```python
import xray
result = xray.inspect(path_or_url_or_bytes)   # str path | https URL | pathlib.Path | bytes
```

**Return shape (verified):** a `dict` keyed by **1-based page number** ->
`list` of `dict`, each `{"bbox": (x0, y0, x1, y1), "text": "<text under the box>"}`.
An **empty dict `{}` means no bad redactions found.**

Probe output (synthetic bad-redaction PDF = live text with a solid black rect
drawn over it):
```
inspect(bad)   -> {1: [{'bbox': (38.0, 86.0, 268.0, 108.0), 'text': 'SECRET NAME John Q Public'}]}
inspect(clean) -> {}            # text not covered -> not flagged
```

**Detects ONE class only:** a rectangle / highlight drawn OVER still-extractable
text (the draw command sequenced after the text command). Algorithm (from the
README): find rectangles -> find letters at those coords -> render the rectangle
region -> single color => bad redaction; mixed color => acceptable (text not
fully hidden). It catches non-pure-black boxes too (newer x-ray).

**Engineering contract for `redaction_check.py`:** import x-ray **LAZILY at the
edge** (mirrors `pii_sweep`->spaCy and `ingest`->docling lazy imports), so the
module stays importable and the other 5 checks always run even if x-ray is ever
absent. If `inspect()` raises on a malformed PDF, that check degrades to a
flag-for-humans lead ("box-over-text check could not run"), never a crash and
never a false "clean" verdict.

---

## 3. The layered checks -- verified APIs (probe results)

### 3.1 box-over-live-text -- x-ray (2 above). PASS.

### 3.2 text-layer sweep -- pdfminer.six (MIT). PASS.
```python
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer, LTTextLine, LTChar
for pageno, layout in enumerate(extract_pages(path), start=1):
    for el in layout:
        if isinstance(el, LTTextContainer):
            for line in el:                       # LTTextLine
                text = line.get_text()            # str
                x0, y0, x1, y1 = line.bbox        # floats, PDF coord space
```
Probe extracted the SAME hidden text x-ray flagged, with bbox
`(42.8, 95.9, 235.0, 109.9)` -- proving the text under a redaction box is
independently recoverable from the text layer. The sweep is the broad LEAD layer:
it reports extractable text (and is the natural place to cross-check x-ray hits
and surface hidden text that x-ray's geometric test misses). NOTE: a normal PDF
has lots of legitimate extractable text, so the sweep is a lead-generator scoped
by mode/region, NOT a "every page has text => bad" alarm (a false-positive trap
to design around in the plan).

**COORDINATE-SPACE TRAP (Codex research-gate finding).** x-ray/PyMuPDF bboxes,
pdfminer.six line bboxes, and pikepdf `/Redact`-annot rects do NOT share a page
coordinate convention (PyMuPDF defaults to a TOP-left origin; the PDF spec /
pdfminer use a BOTTOM-left origin). Any GEOMETRIC correlation across engines (e.g.
"is this pdfminer text under that x-ray box?") MUST normalize to ONE canonical
page space FIRST -- the same coordinate-origin discipline `ingest`'s
`_normalize_prov_origins` enforces (the Phase-6 trap). The design/tests MUST pin
that origin. Prefer NOT correlating geometry across engines where a non-geometric
signal suffices (x-ray already returns the under-box text directly).

### 3.3 metadata / XMP scan -- pikepdf (MPL-2.0). PASS.
```python
import pikepdf
with pikepdf.open(path) as p:
    info = dict(p.docinfo)                 # /Title /Author /Producer /Creator ...
    with p.open_metadata() as m:           # XMP (namespaced keys)
        creator = m.get("dc:creator")      # ['Jane Author']
        keys = sorted(m.keys())            # {http://purl.org/dc/elements/1.1/}creator, ...
    has_xmp = "/Metadata" in p.Root
    raw_xmp = p.Root.Metadata.read_bytes() if has_xmp else b""   # raw XMP packet
```
Probe read back `docinfo` `{/Title, /Producer, /Author}` and XMP `dc:creator`,
`dc:title`. Metadata leakage (author/producer/title revealing names, software,
internal filenames, edit dates) is a LEAD. The detector READS only -- never
edits (open_metadata's editor-mode producer rewrite is irrelevant to reads).

### 3.4 incremental-save -- stdlib raw bytes. PASS.
```python
raw = open(path, "rb").read()
n_eof = raw.count(b"%%EOF")           # > 1 => incremental update(s)
n_startxref = raw.count(b"startxref") # increments in lockstep per revision
```
Probe: clean single-revision save => `%%EOF` count **1**, `startxref` count 1;
after one in-place incremental update (fitz `save(..., incremental=True)`) =>
`%%EOF` count **2**, `startxref` count **2**. A PDF with >1 `%%EOF` carries prior
revision(s) whose content (including pre-"redaction" text) may be recoverable --
a LEAD, NOT proof (legitimate incremental saves exist: signatures, form fill).
Pure byte scan; engine-independent; no parse needed. **RECOMMENDATION (Codex
finding): pair the `%%EOF` count with the `startxref` count** (both increment per
revision) rather than leaning on `%%EOF` alone -- a sturdier revision-count lead.
Still a LEAD, never proof; keep that framing strong in the design/tests.

### 3.5 unapplied /Redact annotation -- pikepdf. PASS.
```python
for i, page in enumerate(p.pages, start=1):
    annots = page.get("/Annots")
    if annots is not None:
        for a in annots:
            if str(a.get("/Subtype")) == "/Redact":
                ...   # a Redact annotation that was MARKED but never APPLIED
```
Probe built a `/Redact` annot with fitz `add_redact_annot` and deliberately did
NOT call `apply_redactions()`; pikepdf enumerated `[(1, '/Redact')]`. An
unapplied `/Redact` annot means the tool marked content for redaction but the
content was never removed -- the underlying text is still present. LEAD.

### 3.6 embedded-file enumeration -- pikepdf. PASS.
```python
with pikepdf.open(path) as p:
    names = list(p.attachments)                              # name -> filespec
    has_ef = "/Names" in p.Root and "/EmbeddedFiles" in p.Root.Names
```
Probe: `pdf.attachments` => `['hidden_notes.txt']`; `Root/Names/EmbeddedFiles`
present. Embedded/attached files can carry un-redacted source data (spreadsheets,
originals). Enumerate name + size as a LEAD (do NOT auto-extract contents). Also
consider file-attachment ANNOTATIONS (`/Subtype /FileAttachment`) and associated
files (`/AF`) as additional embedded-content surfaces (design detail for plan).

---

## 4. What redaction-check CANNOT catch (flag-for-humans; design 7 honest limits)

x-ray catches box-over-live-text ONLY. The six checks together still leave whole
classes of redaction failure with **no FOSS auto-detector** -- these are
DOCUMENTED and flagged for a human, never silently "passed" as clean:

- **Glyph-position / off-page leakage** -- text rendered white-on-white, pushed
  off the visible page, or positioned via transforms; extractable but not
  geometrically "under a box". (Partially surfaced by the 3.2 text sweep as a
  lead, never as a verdict.)
- **Pixelation / blur / mosaic** -- raster redaction that is mathematically
  reversible (depixelation, JPEG block recovery). No CPU-viable FOSS detector.
- **Cross-version / diff reconstruction** -- content recoverable from a prior
  incremental-save revision or a version-control diff. The 3.4 `%%EOF` check
  flags the PRESENCE of revisions as a lead; it does NOT diff or reconstruct.
- **Proportional-font / kerning reconstruction** -- inferring redacted text from
  line-width and glyph-advance side-channels.
- **Semantic reconstruction** -- inferring hidden content from surrounding
  context. Out of scope for a deterministic checker.

`redaction_check.py` MUST surface "checks run / checks NOT possible" honestly so a
clean result is never read as "fully redacted". This is the leads-not-verdicts
contract, identical in spirit to `ingest`'s flag-don't-fake and
`foia-exemptions`' flags-as-leads.

---

## 5. Licensing -- the AGPL chain + the no-infect / no-vendor rule

**Finding:** `redaction-check -> x-ray (BSD-2) -> PyMuPDF (AGPL-3.0)`. Design 8
listed "x-ray (BSD-2)" among the permissive default stack without noting that
x-ray transitively pulls **AGPL** PyMuPDF. (Correct 8 to reflect this.)

**Tim's call (2026-06-04, recorded):** he does NOT require deps to be permissive;
the only hard line is **nothing may force a copyleft license onto Magpie itself**
(license infection). AGPL does NOT infect here:
- Magpie's own source stays **MIT** -- declaring a dependency never relicenses
  your own code.
- AGPL copyleft only reaches a **conveyed combined work**. Magpie ships as an
  open-source plugin and declares x-ray/PyMuPDF as a **pip dependency the USER
  installs** -- it does not bundle, vendor, or ship a combined binary. Local
  assembly of deps for the user's own use is private use (AGPL 2); 13's
  network clause never triggers for a laptop-local tool.
- **Rule to keep it clean:** NEVER vendor x-ray / PyMuPDF source into the repo,
  and NEVER ship a bundled binary / container that embeds it. Declared pip dep
  only (in `requirements-dev.txt`, pinned `x-ray==0.3.6`; PyMuPDF version is
  controlled by x-ray's own pin).

**Decision:** use x-ray as a normal pinned dependency (battle-tested for exactly
this job, e.g. the high-profile bad-redaction exposes), lazy-imported at the edge.

**Caveat (Codex finding) -- scope of the no-infect conclusion.** It holds for the
CURRENT mode only: MIT repo + declared pip dep + laptop-local, user-installed. It
does NOT automatically carry to a future HOSTED MCP/service deployment, a
prebuilt/shipped venv, a frozen executable, or a bundled container image that
EMBEDS PyMuPDF -- each such packaging needs a SEPARATE AGPL review before it ships
(the Layer-2/3 Docker profiles especially).

---

## 6. redact-output <-> pii_sweep join contract (VERIFIED against scripts/pii_sweep.py)

`pii_sweep.sweep(..., collect_local_texts=True)` emits an OPT-IN `local_texts`
(off by default; officials-only rows excluded):
```python
result["local_texts"] = {
    text_id: {"text": <distinct, outer-stripped, case-preserved>,
              "count": <int row count this text covers>,
              "categories": [<pattern names> + maybe "person_official"/"person_unknown_role"]},
    ...
}
# text_id = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]   (stable LOCAL key)
# included iff broad_bool[i] is True (a redaction target); a truncated-hash collision raises loud.
```
`redact_output.py` therefore:
- joins on `text_id` LOCALLY,
- redacts the `text` field names->initials,
- writes the full exhibit to a **LOCAL non-vault path** (CSV per design 5.7),
- and NEVER lets a `text_id` or a raw matched `text` cross a published path
  (design 7: counts publish, raw texts/ids stay local).

The local_texts SHAPE matches and is stable by design (the join KEY + shape need
no pii_sweep change). BUT (Codex research-gate finding) the shape is NOT
sufficient on its own for SELECTIVE name redaction: it carries no per-name SPAN
offsets and no per-span role labels, only whole-text `categories`. A MIXED reason
field naming BOTH an official to KEEP and an uninvolved third party to REDACT
cannot be selectively redacted from `{text, count, categories}` alone. OPEN
(design decision, ties to the involved-vs-uninvolved policy in section 10): how
redact-output resolves per-name spans + their keep/redact role -- either (a)
redact-output re-runs the PERSON classifier LOCALLY over `local_texts` to locate
PERSON spans and apply the officials + involved keep-list per span, or (b)
pii_sweep gains a LOCAL-only per-span/role handoff. Decide at the design gate.
Either way the raw text + spans stay LOCAL; only counts publish.

---

## 7. Dual --mode framing (received / pre-publish) -- to finalize in design

- `received`: inspect a FOIA response WE RECEIVED for the AGENCY's bad
  redactions -- recover/quantify what they failed to hide (feeds `request-the-gap`
  follow-ups) and document it. All six checks apply.
- `pre-publish`: inspect OUR OWN output before release -- did WE leave third-party
  PII extractable (box-over-text, residual text layer, metadata, embedded files,
  prior revisions)? A pre-publish FAIL should block/warn loudly.
The check ENGINES are the same; mode changes framing, default severity, and which
findings are actionable. Precise per-mode behavior is a DESIGN decision (next
gate), not a research fact.

---

## 8. Fixture strategy (synthetic, ASCII-only)

All fixtures SYNTHETIC + ASCII-only (the content-filter rule), built in `tmp_path`
from code (mirrors Phase-6 `conftest.py`); the real Simpsonville corpus is wired
only at Task 11.2 behind an env var, never committed.
- bad-redaction PDF: fpdf2 writes live text, then a solid black `rect(..., style="F")`
  over it (verified to trip x-ray).
- clean control: text not covered (verified x-ray returns `{}`).
- metadata PDF: pikepdf `open_metadata()` + `docinfo`.
- /Redact-annot PDF: fitz `add_redact_annot` WITHOUT `apply_redactions()`.
- embedded-file PDF: pikepdf `pdf.attachments[name] = b"..."`.
- incremental-save PDF: write clean, copy, reopen the COPY, fitz
  `save(same_path, incremental=True, encryption=PDF_ENCRYPT_KEEP)` -> `%%EOF` x2.
  (fitz incremental save MUST target the file it opened -- "incremental needs
  original file" if a different path is given.)
Two test tiers like ingest: a PURE/offline tier (byte-level + pikepdf, no x-ray)
and an x-ray-marked tier (loads PyMuPDF), so the offline suite stays fast and the
box-detection path is selectable -- mirror the `docling`/`spacy` pytest markers
with an `xray` marker.

---

## 9. Decoupling

`redaction_check.py` and `redact_output.py` share NO code with the Track-A
analysis modules (`stats`/`load_table`/`derive`/`recipe`/`pii_sweep`/`ingest_gate`/
`ingest`). The ONLY contract is: (a) `redact_output` CONSUMES `pii_sweep`'s
`local_texts` dict shape (5.2) -- a data contract, not an import; (b) both can
operate on the same PDFs `ingest` processed (ingest deliberately PRESERVED
live-text-under-redaction-boxes so redaction-check can reason about it). Pure-core
/ engine-at-the-edge mirrors `pii_sweep` (spaCy) and `ingest` (docling): the
byte/metadata/annot/embedded checks are pure-ish (stdlib + pikepdf, golden-
testable offline); x-ray is the lazy ML-ish edge.

---

## 10. Open questions deferred to brainstorming / design

1. **Output schema** -- the finding/lead record shape (per-check, per-page,
   bbox/text where applicable, severity, mode) + how it routes through Librarian
   (counts/leads publishable; raw recovered text stays LOCAL like pii_sweep).
2. **Per-mode severity policy** (7) -- what a pre-publish FAIL blocks; how
   received-mode leads feed request-the-gap.
3. **Wiring shape** -- almost certainly pure-script (no `.mcp.json`), like ingest;
   confirm in design.
4. **text-sweep scoping** -- how to make 3.2 a useful lead without a
   "text-exists => bad" false alarm (region/mode scoping; cross-check x-ray hits).
5. **redact-output redaction rules** -- the names->initials algorithm details
   (how initials are formed for multi-token / titled names; how
   possible_birthdate/race_sex broad-leads are handled in published output) and
   the exact LOCAL exhibit path policy (outside any configured vault).
6. **Additional hidden-content leak surfaces** (Codex research-gate finding) --
   beyond the 6 core checks, the design must either ADD as candidate checks or
   explicitly DISCLAIM (with the same flag-for-humans honesty): annotation
   contents/comments beyond `/Redact` (`/Text`, `/Popup`, `/FreeText`), AcroForm
   field values, optional-content groups (OCG layers -- hidden in a viewer but the
   content is still extractable), and associated files `/AF` + `/FileAttachment`
   annots. Several are pikepdf-checkable (real candidate checks, not "cannot
   catch"); pin which are IN-SCOPE for Phase 7 vs deferred-with-disclaimer.
7. **Cross-engine coordinate normalization** (Codex research-gate finding) -- if
   any check correlates bboxes across x-ray / pdfminer / annots, normalize to one
   canonical page space FIRST (the section-3.2 trap); pin the origin in tests, or
   avoid cross-engine geometry where a non-geometric signal suffices.
8. **Span-level name resolution for redact-output** (section 6) -- the chosen
   mechanism (local NER rerun vs. pii_sweep span handoff) for selectively
   redacting uninvolved names while keeping officials + involved subjects in a
   mixed text.

**RESOLVED (Tim's call, 2026-06-04) -- the redact-output redaction POLICY.**
The line is INVOLVED-vs-UNINVOLVED, NOT official-vs-non-official. The target is
**uninvolved third parties** only (bystanders, surveilled suspects/POIs, MINORS
swept into reason fields). Many NON-official people are INVOLVED subjects who must
STAY NAMED -- e.g., tracing financial dealings, the non-official subjects are the
story.
- KEEP named: (a) officials (`person_official`, accountability) AND (b) INVOLVED
  subjects the investigator designates -- supplied via a `keep_names` /
  `involved_names` ALLOWLIST parameter, exactly parallel to pii_sweep's
  `official_names` lexicon. Officials are always kept; involved names are kept too.
- REDACT to initials: a flagged PERSON name that is NEITHER an official NOR on the
  involved allowlist = an uninvolved third party.
- SAFE DEFAULT: with NO involved-list supplied, every non-official flagged name is
  treated as uninvolved and redacted (the Simpsonville behavior: suspect/POI/minor
  reason-field names -> initials; no fragile "is-this-a-minor" heuristic;
  over-redacting an incidental name is the acceptable cost vs. leaking a minor).
  A project like financial-tracing OPTS its involved subjects INTO the keep-list so
  they stay named.
- ALWAYS mask: the high-precision structured PII categories (ssn, dob_kw,
  alien_num, driver_lic, phone, email) -- "PII that should already have been
  redacted" -- regardless of any name policy.
- SCOPE: redact-output ONLY masks text that pii_sweep already flagged (it joins on
  `text_id`); it NEVER NER-scans the analyst's own narrative, so contextual names
  the analyst writes into findings notes are untouched by construction.
- SURFACES: the PUBLISHED artifact gets the redactions (initials + masked PII);
  the FULL un-redacted exhibit is written to a LOCAL non-vault path only, never
  published. (design 5.1 / 6.1 step 6 / 7.)
- OPEN (design detail, not blocking): how redact-output locates the PERSON spans
  to initialize within a flagged text -- reuse the NER classifier over
  `local_texts` (redact spans not official/involved), OR extend pii_sweep to emit
  per-name spans. Decide at the design gate.

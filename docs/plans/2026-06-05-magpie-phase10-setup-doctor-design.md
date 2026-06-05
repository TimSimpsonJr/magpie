---
phase: 10
title: setup / doctor onboarding wizard
status: design
codex_thread_id: 019e95ea-d5d9-72f0-87db-e1bbd50a4c42
date: 2026-06-05
---

# Phase 10 -- setup / doctor onboarding wizard (design)

The WHY. The HOW lives in the companion implementation plan
(`2026-06-05-magpie-phase10-setup-doctor.md`). ASCII-only (this file is read by
SDD subagents).

## 1. Problem

Magpie's Layer 0-1 is a laptop-local FOSS stack with a wide, heterogeneous
dependency surface: a core pandas/numpy/duckdb/pyarrow/sqlite-utils analysis
stack, a heavy spaCy NER model (en_core_web_lg, ~400 MB), an even heavier Docling
+ RapidOCR + torch document stack (~1.5-2 GB), Free Law x-ray (-> PyMuPDF),
rfc3161-client for timestamping, plus OPTIONAL system binaries pip cannot manage
(Tesseract + Ghostscript for OCR preprocessing; uv/uvx for the read-only
mcp-sqlite served-query surface; openssl `ts` for an evidence cross-check).

Two very different people touch this stack (design 2, 5.1):

- An OPERATOR performs a ONE-TIME technical setup (create the venv, install the
  pinned deps, download the model weights). Technical, present at a terminal.
- A JOURNALIST/investigator does DAILY conversational analysis and should never
  have to think about venvs, pip, or infrastructure. When something is wrong they
  need a plain-language health report, not an install transcript.

Phase 10 builds the onboarding wizard that serves both: a shared probe engine
(`scripts/detect_tier.py`) plus two skills with a hard write/read asymmetry --
`setup` (operator, MAY install) and `doctor` (journalist, READ-ONLY) -- and two
separate onramp guides.

Non-negotiable framing (design 4, 8; locked at the Phase 10 research gate +
brainstorm):

- Layer 0-1 is laptop-local with NO Docker. Docker is Layer 2 (the entity-graph
  stack) and is OUT OF SCOPE here. The journalist guide NEVER mentions Docker.
  The operator guide does not mention Docker either -- not even a forward note --
  so Layer-2 terminology is never planted in a Layer 0-1 operator's head.
- No new Python dependencies. The probe engine is pure stdlib. (Validated live at
  the research gate.)

## 2. Prior art: research-workflow detect_tier

`research-workflow/scripts/detect_tier.py` is the model. Its shape:

- One `check_<thing>()` function per probe, each returning a small status dict.
- A `build_tier_report()` aggregator that turns the probes into a component map +
  a named tier (full / mid / base) + "what is missing for the next tier" lists.
- A thin `detect_tier()` that returns just the tier name.

What we ADOPT: the per-probe function shape, the aggregator that builds a map,
and the "what is missing + how to fix" guidance.

What we REJECT (does not fit Magpie):

- Its linear full/mid/base tier. research-workflow's two gating components
  (Ollama, SearXNG) nest cleanly. Magpie's capabilities do NOT: you can have the
  spaCy model but not Tesseract, or Docling but not x-ray. A single linear score
  is dishonest. We lead with a CAPABILITY MAP and subordinate any summary to it.
- Its hardware (RAM/VRAM) -> Ollama-model recommendation. Magpie uses Claude; it
  has no local-LLM tier. Dropped entirely.
- Its `ensure_searxng()` docker-compose auto-start and network health probes. No
  Magpie analog. The only "service" is the local read-only mcp-sqlite launched
  via uvx, and we deliberately do NOT execute uvx as a health probe (see 5).

## 3. The capability map (primary output)

The primary output is a CAPABILITY MAP keyed by USER VERBS, not phase names or
package names. Each capability has a status and names exactly what is missing and
what that gap blocks. The seven capabilities and their requirements:

| capability (user verb)        | required (hard)                                              | optional (enhances)                  | maps to skill(s)            |
|-------------------------------|-------------------------------------------------------------|--------------------------------------|-----------------------------|
| analyze datasets              | pandas, numpy, duckdb, pyarrow, openpyxl, charset-normalizer, sqlite-utils | uvx (conversational mcp-sqlite query surface) | dataset-analyze, analysis-recipe |
| ingest native PDFs            | docling, rapidocr, onnxruntime, torch                       | --                                   | ingest                      |
| OCR preprocessing for scans   | ocrmypdf (py) + Tesseract + Ghostscript (system binaries)   | --                                   | ingest (deskew / redo-ocr)  |
| PII scan                      | spacy, en_core_web_lg                                        | --                                   | pii-sweep, redact-output    |
| redaction QA                  | pikepdf, pdfminer.six (the 7 offline checks)                | x-ray -> PyMuPDF (box-over-text, the 8th check) | redaction-check  |
| citation verify               | (engine is stdlib) + the "ingest native PDFs" stack         | --                                   | investigate                 |
| evidence timestamp            | rfc3161-client, requests, cryptography                      | openssl `ts` (cross-tool verify)     | archive-evidence            |

Status vocabulary (three values, always with specifics):

- READY -- every REQUIRED dep present.
- DEGRADED -- every required dep present but an OPTIONAL enhancement missing; the
  capability works with a named reduction (e.g. redaction QA without x-ray runs 7
  of 8 checks; analyze datasets without uvx loses the conversational SQL surface
  but stats/recipe still run; evidence timestamp without openssl `ts` still
  timestamps + verifies via rfc3161-client, just no second-tool cross-check).
- UNAVAILABLE -- a REQUIRED dep missing; names the missing piece + the fix.

Honesty rules baked into the map (Codex brainstorm, standing in for Tim):

- The map is the TRUTH; capabilities are independent. A missing OCR-preprocessing
  binary does NOT make ingest or the product "incomplete" -- it makes exactly one
  capability (OCR preprocessing for scans) UNAVAILABLE and says so.
- "OCR preprocessing for scans" is named for what it IS, never "tier 3 / full."
  Missing Tesseract is "scan preprocessing unavailable," not "Magpie is not full."
- redaction QA degrading without x-ray mirrors redaction_check's own
  degrade-don't-crash contract (the box-over-text check raises CheckUnavailable;
  the other 7 still run).
- citation verify's ENGINE (scripts/citation.py) is pure stdlib and always
  importable, but the WORKFLOW needs documents to cite, so its readiness tracks
  the "ingest native PDFs" stack. The map states this rather than faking READY.

### 3.1 The subordinate headline

Below the map (never above it), a two-line plain headline for the journalist:

- `core structured-data analysis: READY | NOT READY` (capability "analyze
  datasets" required deps).
- `document workflows: READY | PARTIAL | UNAVAILABLE` -- a rollup over the
  document-side capabilities (ingest native PDFs, PII scan, redaction QA,
  citation verify, evidence timestamp, OCR preprocessing for scans): READY if all
  READY, UNAVAILABLE if all UNAVAILABLE, else PARTIAL with a one-line list of
  which are reduced. NO single 1/2/3 score is ever shown to a user.

## 4. detect_tier.py -- the pure probe engine

Same pure-core / IO-at-the-edge split as the rest of the suite (pii_sweep's
injectable classifier, ingest_gate's injectable wordlist, citation's injected
timestamp, evidence's injected Timestamper). The CAPABILITY-MAP LOGIC is pure and
golden-testable from injected probe dicts; the PROBES are the only IO edge.

### 4.1 Probe functions (the IO edge -- stdlib only, no heavy import, no network, no side effects)

- `check_python_dist(dist_name) -> {"present": bool, "version": str|None}` --
  `importlib.metadata.version(dist_name)`; PackageNotFoundError -> not present.
  Probe by DISTRIBUTION name (matches requirements-dev.txt). This NEVER imports
  the package, so probing torch/docling/spacy does not load them (doctor stays
  fast). GOTCHA captured: dist name != import name (x-ray->xray, PyMuPDF->fitz,
  pdfminer.six->pdfminer, PyYAML->yaml, rfc3161-client->rfc3161_client,
  sqlite-utils->sqlite_utils, charset-normalizer->charset_normalizer); the
  en_core_web_lg model registers as a normal distribution so metadata.version
  detects it WITHOUT loading the 400 MB model.
- `check_spacy_model(name="en_core_web_lg") -> {"present": bool, "version": str|None}`
  -- thin wrapper over check_python_dist for the model. Deliberately does NOT
  import spacy or load the model (presence is the health signal; a deep "does it
  load" check would cost ~1.5 s and is out of scope -- see non-goals).
- `check_binary(names) -> {"present": bool, "path": str|None, "name": str|None}`
  -- `shutil.which` over a name or a LIST of fallback names (Ghostscript is
  `gswin64c` / `gswin32c` on Windows, `gs` on POSIX; returns the first hit).
- `check_openssl_ts() -> {"present": bool, "path": str|None, "ts_subcommand": bool}`
  -- shutil.which("openssl"); if present, `subprocess.run([openssl, "ts",
  "-help"], timeout=...)` and treat rc==0 as the `ts` subcommand existing (some
  OpenSSL builds omit `ts`). A bounded subprocess; no network.
- `check_mcp_wiring(mcp_json_path) -> {"uvx_present": bool, "uvx_path": str|None,
  "mcp_json_present": bool, "declares_mcp_sqlite": bool}` -- READ-ONLY: uvx via
  shutil.which + parse the .mcp.json TEXT and confirm it references the
  `mcp-sqlite` server. It does NOT execute uvx and does NOT start the server
  (Codex's risk fix: a health check must have no side effects and hit no network;
  any real launch probe is setup-only and explicit). The served .magpie/dataset.db
  is built by dataset-analyze at analysis time, not by setup; its absence is
  normal and is NOT a health failure.

Each probe is individually monkeypatch-friendly (tests patch shutil.which /
importlib.metadata.version / subprocess.run), but the heavy testing is on the
pure aggregator below via INJECTED probe dicts.

### 4.2 Pure aggregator + summary (golden-testable, no IO)

- `build_capability_map(probes: dict) -> dict` -- PURE. Takes a dict of probe
  results (the exact shape the probe functions emit) and returns
  `{capability_name: {"status": READY|DEGRADED|UNAVAILABLE, "requires": [...],
  "missing": [...], "optional_missing": [...], "blocks": "<plain sentence>",
  "fix": "<plain instruction>"}}` for the 7 capabilities in section 3. No IO,
  deterministic -> golden tests inject mocked all-present / all-absent / mixed
  probe dicts and pin the resulting map. THIS is where mocked presence/absence
  lives (plan Task 10.1), so tests never depend on what is actually installed.
- `summarize(capability_map) -> dict` -- PURE. The two-line subordinate headline
  of section 3.1 (`core_structured_data` bool/label + `document_workflows` rollup
  label + the reduced-capability list). No 1/2/3 score.

### 4.3 The one IO entry point + CLI

- `detect(mcp_json_path=..., repo_root=...) -> dict` -- runs the probe functions,
  feeds them to build_capability_map + summarize, returns the full report
  (capabilities + headline + raw probe detail + platform info). The only function
  that performs IO.
- `render_text(report) -> str` -- a plain-text rendering of the map + headline for
  humans (the doctor/setup skills and the CLI use it).
- CLI `__main__`: `python scripts/detect_tier.py [--json]` -- default prints
  render_text; `--json` prints `json.dumps(report)`. This gives the operator a
  REAL health-check command the OPERATOR_GUIDE can invoke OUTSIDE Claude Code
  (`& .venv\Scripts\python.exe scripts\detect_tier.py`), per Codex Q4. We do NOT
  invent a `magpie setup` / `magpie doctor` shell surface -- those are skills, and
  the docs use skill phrasing for them and CLI phrasing only for this real script.

## 5. setup vs doctor -- the hard asymmetry

Both skills are thin orchestrators over `scripts/detect_tier.py`. The asymmetry IS
the design:

### 5.1 setup (operator, one-time) -- MAY write/install

Flow:
1. Run detect_tier and SHOW the operator exactly what is present vs missing.
2. For the venv/pip layer: with the operator present and consenting, RUN
   `mise run bootstrap` (the repo-managed step: pip install -r
   requirements-dev.txt -> all pinned deps + the en_core_web_lg wheel; first-run
   Docling/RapidOCR model weights download on first ingest). setup that does not
   execute the repo-managed step is ornamental (Codex Q2).
3. For the system binaries pip cannot manage (uv/uvx, Tesseract, Ghostscript):
   INSTRUCT only -- print the exact per-OS install commands (winget / choco / apt
   / brew). setup never silently installs a system binary.
4. Re-run detect_tier and report the now-current capability map.
5. Note that the bundled public sample corpus (corpus/public/) is forthcoming
   (Phase 11); setup does not download a corpus.

setup is the ONLY skill that may run an install. It runs on the operator's
machine with the operator watching.

### 5.2 doctor (journalist, anytime) -- strictly READ-ONLY

Flow:
1. Run detect_tier (read-only: no installs, no uvx execution, no network).
2. Render the capability map (user verbs) + the subordinate headline + for each
   gap, what it blocks in plain language + the single instruction "ask your
   operator to run setup" (or, for a system binary, the one-line install hint).
3. NEVER installs, NEVER runs `mise run bootstrap`, NEVER invokes setup, NEVER
   starts the mcp-sqlite server. If core structured-data analysis is UNAVAILABLE,
   doctor says so plainly and points back to the operator.

doctor is safe to run a hundred times a day. It changes nothing.

## 6. The two onramps + the README

- `docs/OPERATOR_GUIDE.md` -- the one-time technical setup, Layer 0-1 scoped, NO
  Docker anywhere (not even a footnote). Covers: prereqs (Python 3.12 via mise;
  uv/uvx; the optional Tesseract + Ghostscript for scan preprocessing); the
  bootstrap (mise run bootstrap, or the explicit `& .venv\Scripts\python.exe -m
  pip install -r requirements-dev.txt` fallback for the -NoProfile shell); the
  model-weight downloads (size + first-run timing); verifying via `setup` /
  `python scripts/detect_tier.py`; and troubleshooting (the -NoProfile bare-python
  trap, the dist!=import name confusion, what each missing binary disables).
- `docs/JOURNALIST_START.md` -- daily conversational use in journalist terms (you
  have a FOIA spreadsheet / a scanned PDF release / need a PII or redaction check
  / need a citable claim). "Open Claude Code and ask in plain language"; "run
  doctor if something looks off." NEVER mentions Docker, venv, pip, or package
  names. If something is missing it says "ask whoever set this up to run setup."
- `README.md` -- polish the existing Operators / Investigators dual-onramp split
  so each persona is routed to its guide in one glance, and the "two onramps"
  framing is explicit.

## 7. Testing (TDD; detail in the plan)

- The pure aggregator (`build_capability_map`, `summarize`, `render_text`) is
  golden-tested from INJECTED probe dicts: all-present -> every capability READY +
  headline READY/READY; all-absent -> every capability UNAVAILABLE + a NOT READY
  headline; and the load-bearing MIXED cases -- Tesseract/Ghostscript absent ->
  exactly "OCR preprocessing for scans" UNAVAILABLE while ingest stays READY
  (the independence honesty rule); x-ray absent -> redaction QA DEGRADED (7/8) not
  UNAVAILABLE; uvx absent -> analyze datasets DEGRADED not UNAVAILABLE; openssl ts
  absent -> evidence timestamp DEGRADED. These pin the honesty rules so they cannot
  silently regress.
- The probe functions are unit-tested with monkeypatched shutil.which /
  importlib.metadata.version / subprocess.run (mocked presence/absence) so the
  suite is independent of what happens to be installed on the box.
- A read-only guarantee test for `check_mcp_wiring`: it must NOT call subprocess to
  run uvx (assert via a patched subprocess.run that records calls); it only reads
  .mcp.json text + shutil.which.
- Skill smoke tests (PyYAML, mirroring the existing test_*_skill.py): the setup +
  doctor SKILL.md frontmatter parses (name/description/version), the setup body
  documents the run-bootstrap + instruct-binaries flow + may-install, and the
  doctor body documents the READ-ONLY / never-install / never-start-server
  contract. A guard test that JOURNALIST_START.md contains no "docker" token
  (case-insensitive) -- the no-Docker rule is load-bearing and testable.
- detect_tier carries NO heavy markers (it is stdlib); it runs in the default
  offline suite. Confirm a clean native-import stays cheap (no torch/docling/spacy
  pulled by importing scripts.detect_tier).

## 8. Scope / non-goals (YAGNI; confirmed with Codex)

- NO Docker, no docker-compose, no Layer-2 service wiring.
- NO auto-install of system binaries (operator instruction only).
- NO hardware (RAM/VRAM) probe and NO local-LLM model recommendation.
- NO running-service health probe; NO uvx execution from doctor; NO network from
  any probe. mcp-sqlite is checked as "uvx present + .mcp.json declares it,"
  nothing more.
- NO deep "does the spaCy model actually load" check (presence via metadata is the
  signal); a deep/`--verify-load` mode is a possible future, not Phase 10.
- setup does not bundle/download the public sample corpus (that is Phase 11
  Task 11.1); it only notes it is forthcoming.

## 9. Honest limits (the suite's standing stance)

- detect_tier reports PRESENCE + VERSION, not correctness. A present-but-broken
  install (a corrupt wheel, a torch that imports but cannot run) reads as READY;
  the deep-verify is out of scope. The map says what is INSTALLED, which is the
  health signal an operator/journalist needs, not a guarantee every code path runs.
- A version mismatch against requirements-dev.txt is reported (present + the actual
  version) but is not by itself a failure -- the pinned versions are the source of
  truth and setup installs them; doctor surfaces drift as a note, not a block.

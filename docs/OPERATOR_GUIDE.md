# Operator Guide -- one-time Magpie setup (Layer 0-1)

You are the **operator**: the one person who installs Magpie on a machine so an
investigator can use it conversationally. You do this once per machine. After
setup, the journalist never touches any of this -- they just open Claude Code and
ask in plain language (see `docs/JOURNALIST_START.md`).

This is Layer 0-1: it runs on a single laptop with no heavy infrastructure. There
is no container, no orchestration layer, and no service to keep running. Just the
Python toolchain, a couple of model weights, and two optional system binaries for
scanned PDFs.

The fastest path is to run the **`setup`** skill inside Claude Code and let it walk
you through everything below. This guide is the manual reference for when you want
to do it by hand, or when something needs troubleshooting.

---

## 1. Prerequisites

### Python 3.12 via mise (required)

Magpie targets Python 3.12. Install and pin it with [mise](https://mise.jdx.dev):

```
mise use python@3.12
```

mise manages the interpreter and the virtual environment for the repo, so you do
not have to manage either by hand.

### uv / uvx (required for the conversational query surface)

`uvx` launches the pinned, read-only `mcp-sqlite` server that lets an investigator
ask questions of a dataset in plain language. Without it, structured-data analysis
still runs, but the conversational SQL surface is unavailable (the capability map
reports this as DEGRADED, not broken).

Install uv (which provides `uvx`):

- **Windows:** `winget install astral-sh.uv` (or `pip install uv`)
- **macOS:** `brew install uv`
- **Linux:** `curl -LsSf https://astral.sh/uv/install.sh | sh`

### Tesseract + Ghostscript (OPTIONAL -- only for scanned PDFs)

These two system binaries are used **only** to deskew and re-OCR ugly scanned PDFs
before ingest. Native-text PDFs (the common case) do not need them at all. If you
skip these, every other capability still works; only "OCR preprocessing for scans"
goes UNAVAILABLE.

`pip` cannot install these -- they are operating-system packages. Install them with
your platform package manager:

- **Windows:** `winget install UB-Mannheim.TesseractOCR` and
  `winget install ArtifexSoftware.GhostScript` (or `choco install tesseract ghostscript`)
- **macOS:** `brew install tesseract ghostscript`
- **Linux (Debian/Ubuntu):** `sudo apt install tesseract-ocr ghostscript`

---

## 2. Install

From the repo root, the repo-managed bootstrap installs the Python dependencies and
the spaCy model in one step:

```
mise run bootstrap
```

That is the supported path. If you are in a bare PowerShell session started with
`-NoProfile` (so `mise` activation has not run and bare `python` resolves to the
wrong interpreter), call the venv interpreter explicitly instead:

```
& .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Both routes install the same pinned dependencies. No new dependency is introduced
by setup; everything is already declared in `requirements-dev.txt`.

---

## 3. Model weights (sizes + timing)

Two sets of weights download outside the plain `pip` dependency list. Plan for the
bandwidth and time on a first install:

- **spaCy `en_core_web_lg`** -- about 400 MB. This is the large English model the
  PII scan uses for authoritative PERSON-name detection. `mise run bootstrap`
  fetches it as a wheel during install, so it is on disk before first use. On a
  typical broadband connection this adds a couple of minutes to bootstrap.
- **Docling / RapidOCR weights** -- downloaded lazily on the **first** document
  ingest, not during bootstrap. The first time an investigator reads a PDF, Magpie
  pulls these layout/OCR weights (a few hundred MB) and caches them; that first
  ingest therefore takes noticeably longer (often a minute or two) while every
  ingest after that is fast. If you want the first real ingest to be quick, run one
  throwaway ingest yourself after setup to warm the cache.

Both downloads are one-time per machine. There is no recurring download and nothing
to refresh on a schedule.

---

## 4. Verify

After install, confirm what the machine can actually do. Run the **`setup`** skill
(it re-checks and reports), or run the probe directly:

```
& .venv\Scripts\python.exe scripts\detect_tier.py
```

That prints a capability map in the investigator's own verbs (analyze datasets,
ingest native PDFs, PII scan, redaction QA, citation verify, evidence timestamp,
OCR preprocessing for scans) with each one marked READY, DEGRADED, or UNAVAILABLE,
plus a two-line headline (core structured-data analysis, and the document-workflow
rollup). Add `--json` for a machine-readable report:

```
& .venv\Scripts\python.exe scripts\detect_tier.py --json
```

On a typical machine without Tesseract/Ghostscript you should see core READY and
document workflows PARTIAL, with "OCR preprocessing for scans" the one UNAVAILABLE
line. That is the expected healthy state when you have skipped the optional scan
binaries.

The probe reports **presence and version**, not correctness -- see the honest limit
at the end of this guide.

---

## 5. Troubleshooting

**Bare `python` runs the wrong interpreter.** In a `-NoProfile` PowerShell session,
`mise` has not activated the environment, so bare `python` is the system Python, not
the repo venv. Always call the venv interpreter explicitly:
`& .venv\Scripts\python.exe ...`. This is the single most common setup trap on
Windows. The same applies to `pip`: use `& .venv\Scripts\python.exe -m pip ...`,
never a bare `pip`.

**"It says X is missing but I installed it."** Distribution name is not the same as
import name. The probe checks the **distribution** name (what `pip`/the wheel
registers), which often differs from what you `import` in code. For example the
distribution `x-ray` imports as `xray`, `pdfminer.six` imports as `pdfminer`,
`rfc3161-client` imports as `rfc3161_client`, `sqlite-utils` imports as
`sqlite_utils`, and `charset-normalizer` imports as `charset_normalizer`. If the
probe reports a distribution missing, check the distribution name in
`requirements-dev.txt`, not the import line in some module.

**What each missing system binary disables (exactly).** The capability map is
honest about scope -- one missing piece does not cascade into unrelated workflows:

- **Tesseract or Ghostscript missing** -> only **OCR preprocessing for scans** goes
  UNAVAILABLE. Ingesting native-text PDFs is unaffected and stays READY. The probe
  names the missing binary (tesseract / ghostscript), not a Python package.
- **uv / uvx missing** -> **analyze datasets** drops to DEGRADED: quantitative
  analysis still runs, but the conversational `mcp-sqlite` query surface is
  unavailable. The fix here is to install uv, NOT to re-run bootstrap (bootstrap is
  `pip` and cannot install a system binary).
- **`.mcp.json` missing or not declaring `mcp-sqlite`** -> **analyze datasets** is
  DEGRADED for the same conversational-surface reason, even when `uvx` is present.
- **openssl `ts` subcommand missing** -> **evidence timestamp** drops to DEGRADED:
  hash-on-receipt, RFC 3161 timestamping, and verify-on-store still work; only the
  optional openssl second-tool cross-check is unavailable. Installing an OpenSSL
  build that carries the `ts` subcommand restores it. Again, not a bootstrap fix.

The rule of thumb: a missing **Python** dependency points you at `mise run
bootstrap`; a missing **system binary** points you at the platform package manager.
The probe's `fix` field already tells you which.

---

## Honest limit

`scripts/detect_tier.py` reports that a distribution, model, or binary is present
and at what version. It does not exercise the code path -- it will not catch a
corrupt model file, a partial download, or a binary that is on PATH but broken.
"READY" means "installed and resolvable," not "proven correct end to end." When a
workflow misbehaves despite a READY line, look past presence to the actual run.

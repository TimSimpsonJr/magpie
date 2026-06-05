---
name: setup
description: Use when an operator is setting up Magpie on a new machine for the first time and needs to install the Python dependencies, download the spaCy model, and verify the local toolchain. This skill runs the repo-managed bootstrap, instructs the operator for the system binaries pip cannot install, and re-checks the capability map. Invoke it whenever a user wants to install Magpie, set up the environment, bootstrap the venv, or fix a missing dependency reported by doctor.
version: 0.1.0
---

The setup skill is the operator's one-time onramp: it brings a fresh machine from
nothing to a working Magpie install and then proves what is ready. It is driven by
the shared capability engine `scripts/detect_tier.py`, which probes the toolchain
(installed distributions, the spaCy model, the system binaries, and the mcp-sqlite
wiring) with no heavy imports and no side effects, and reports a per-capability map
in plain user verbs. Setup is the only Magpie skill that MAY install; its read-only
sibling is doctor.

## 1. What setup does

Setup runs `scripts/detect_tier.py` to see the current state, installs the missing
pieces it is allowed to install (with the operator present and consenting), instructs
the operator for the pieces pip cannot manage, and re-runs `scripts/detect_tier.py`
to report the now-current capability map. It is meant to be run by an operator on the
machine, once, during onboarding (or again later to repair a gap doctor flagged).

## 2. The flow

a. Probe first. Run `scripts/detect_tier.py` and SHOW the operator what is present
   versus missing, capability by capability, before changing anything.

b. Bootstrap the Python side. With the operator present and consenting, run
   `mise run bootstrap`. That installs the pinned pip dependencies and the
   en_core_web_lg spaCy wheel into the project venv. (The Docling and RapidOCR model
   weights are not downloaded here; they fetch on the first ingest run.) In the
   -NoProfile PowerShell shell, bare `python` is the wrong interpreter, so the
   fallback that does the same install is:
   `& .venv\Scripts\python.exe -m pip install -r requirements-dev.txt`

c. Instruct for the system binaries. The binaries pip cannot manage are uv/uvx,
   Tesseract, and Ghostscript. Setup never silently installs a system binary; it
   gives the operator the exact per-OS command to run themselves:
   - uv/uvx (provides the uvx that launches the mcp-sqlite query surface):
     `winget install astral-sh.uv` or `choco install uv` (Windows),
     `brew install uv` (macOS), or the astral install script (Linux).
   - Tesseract (OCR engine for scan preprocessing):
     `winget install UB-Mannheim.TesseractOCR` or `choco install tesseract`
     (Windows), `brew install tesseract` (macOS), `apt install tesseract-ocr`
     (Debian/Ubuntu).
   - Ghostscript (PDF rasterizer ocrmypdf depends on):
     `winget install ArtifexSoftware.GhostScript` or `choco install ghostscript`
     (Windows), `brew install ghostscript` (macOS), `apt install ghostscript`
     (Debian/Ubuntu).

d. Re-probe and report. Run `scripts/detect_tier.py` again and report the
   now-current capability map so the operator can see exactly what each install
   unlocked and what (if anything) is still reduced.

## 3. The setup / doctor asymmetry

Setup MAY install: it runs `mise run bootstrap` and instructs for system binaries.
Doctor is strictly read-only and installs nothing. Keep the asymmetry sharp. Anything
that changes the machine belongs to setup and to a present, consenting operator;
checking the state belongs to doctor and is safe to run anytime. If a user only wants
to know what is ready, point them at doctor, not setup.

## 4. The sample corpus is forthcoming

Setup does not download a corpus. The bundled public sample corpus (corpus/public/)
is forthcoming in a later phase (Phase 11); until then, operators point Magpie at
their own files.

## 5. Honest limit

`scripts/detect_tier.py` reports presence and version, not correctness. A capability
shown as READY means its dependencies are installed and importable by name, not that
every downstream tool is wired perfectly for a given document. Treat the report as a
toolchain inventory, not a guarantee of a successful run.

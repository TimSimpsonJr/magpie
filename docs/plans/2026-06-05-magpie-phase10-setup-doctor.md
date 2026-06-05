# Phase 10 -- setup / doctor onboarding wizard -- Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> superpowers:subagent-driven-development) to implement this plan task-by-task.

**Goal:** Build the Magpie Layer 0-1 onboarding wizard -- a pure stdlib capability
probe (`scripts/detect_tier.py`) plus a `setup` (operator, may install) and
`doctor` (journalist, read-only) skill, two onramp guides, and a dual-onramp
README.

**Architecture:** Pure-core / IO-at-the-edge (the suite's standing shape). The
capability-MAP logic is a pure function of an injected probe dict (golden-tested
with mocked presence/absence); the PROBE functions are the only IO edge (stdlib:
importlib.metadata, importlib.util, shutil.which, subprocess -- no heavy imports,
no network, no side effects). Two thin skills orchestrate the engine with a hard
write/read asymmetry.

**Tech Stack:** Python 3.12 stdlib only (NO new dependencies). pytest + PyYAML
(already present) for tests. Design source of truth:
`docs/plans/2026-06-05-magpie-phase10-setup-doctor-design.md`.

---

## Conventions (read these; they are inlined so you never open another repo file)

- **You are ALREADY on the feature branch `feat/phase10-setup-doctor`. Commit
  directly to it. Do NOT create or switch branches.**
- **Read ONLY this plan, the design doc named above, and files you create.** Do
  NOT open any other repo file -- several carry non-ASCII that will block your
  Read tool. Everything you need (house style, patterns, exact code) is inlined
  here. Importing a module at test runtime is fine; only the Read tool blocks.
- **Keep everything you write ASCII-only.** No em-dashes, smart quotes, or
  non-ASCII glyphs in code, tests, skills, docs, or commit messages.
- **Run tests with the venv interpreter, never bare `python`** (the shell is
  -NoProfile, so bare `python` is the wrong interpreter):
  `& .venv\Scripts\python.exe -m pytest tests/test_detect_tier.py -q`
  Full offline suite:
  `& .venv\Scripts\python.exe -m pytest -m "not docling and not spacy and not xray and not tsa" -q`
- **Module style:** start each .py with `from __future__ import annotations`.
  Pure functions, JSON-able native-typed returns, no clock/random/network in the
  pure core. Match the existing scripts/ style (small focused functions, module
  constants in UPPER_CASE, docstrings that state the contract).
- **SKILL.md house style** (exact shape -- mirror it):

  ```
  ---
  name: <skill-name>
  description: <one long third-person sentence-or-two with trigger phrases:
    "Use when a user wants to ...; invoke it whenever ...">
  version: 0.1.0
  ---

  <one-paragraph what-it-does + which engine it calls>

  ## 1. <Section>
  ...
  ```

  Lean, imperative, third-person trigger description. No .mcp.json ships with
  these skills (the engine is the script).

- **Skill smoke-test house style** (mirror exactly -- this is how every
  test_*_skill.py in this repo works):

  ```python
  """Smoke test for the <skill> SKILL.md."""
  from __future__ import annotations

  from pathlib import Path

  import yaml

  SKILL = Path(__file__).resolve().parent.parent / "skills" / "<skill>" / "SKILL.md"


  def _frontmatter_and_body(p):
      text = p.read_text(encoding="utf-8")
      assert text.startswith("---")
      _, fm, body = text.split("---", 2)
      return yaml.safe_load(fm), body
  ```

- **Commit after each task** with a clear ASCII conventional-commit message.
  Multi-line commit body: write it to a UTF-8 file and `git commit -F <file>`
  (an inline -m with embedded quotes breaks in this shell). End every commit
  message with the trailer line:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Task 1: scripts/detect_tier.py -- the probe engine (TDD)

**Files:**
- Create: `scripts/detect_tier.py`
- Test: `tests/test_detect_tier.py`

The module has two halves: the PURE aggregator (`build_capability_map`,
`summarize`, `render_text`) tested from injected probe dicts, and the PROBE edge
(`check_*`, `run_probes`, `detect`, CLI) tested with monkeypatched stdlib. Build
the pure half first (TDD), then the edge.

### The canonical probe-dict shape (the contract between edge and core)

`run_probes()` returns -- and `build_capability_map()` consumes -- exactly this:

```python
{
    "dists": {                      # keyed by DISTRIBUTION name (installed in the bootstrapped venv)
        "pandas": {"present": True, "version": "3.0.3"},
        # ... every dist in _ALL_DISTS ...
    },
    "spacy_model": {"present": True, "version": "3.8.0"},
    "tesseract":   {"present": False, "path": None, "name": None},
    "ghostscript": {"present": False, "path": None, "name": None},
    "openssl_ts":  {"present": True, "path": "C:/.../openssl.EXE", "ts_subcommand": True},
    "mcp": {"uvx_present": True, "uvx_path": "...", "mcp_json_present": True,
            "declares_mcp_sqlite": True},
    "platform": {"os": "windows", "arch": "AMD64"},
}
```

### Step 1.1: Write the failing pure-aggregator tests

Create `tests/test_detect_tier.py`. Start with a helper that builds an
all-present baseline probe dict, plus the golden tests. The DISTRIBUTION lists
must match the module (inline them in the test too so the test is self-checking):

```python
"""TDD for scripts/detect_tier.py -- pure capability map + stdlib probe edge.

The pure aggregator is golden-tested from INJECTED probe dicts (mocked
presence/absence), so the suite never depends on what is installed on the box.
The probe functions are unit-tested with monkeypatched stdlib.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import detect_tier as dt

CORE = ["pandas", "numpy", "duckdb", "pyarrow", "openpyxl", "charset-normalizer", "sqlite-utils"]
INGEST = ["docling", "rapidocr", "onnxruntime", "torch"]
REDACT_OFFLINE = ["pikepdf", "pdfminer.six"]
EVIDENCE = ["rfc3161-client", "requests", "cryptography"]
EXTRA = ["x-ray", "ocrmypdf", "spacy"]
ALL_DISTS = CORE + INGEST + REDACT_OFFLINE + EVIDENCE + EXTRA


def _present(version="9.9.9"):
    return {"present": True, "version": version}


def all_present_probes():
    """A probe dict with EVERYTHING present -- tests mutate it to absent."""
    return {
        "dists": {d: _present() for d in ALL_DISTS},
        "spacy_model": {"present": True, "version": "3.8.0"},
        "tesseract": {"present": True, "path": "/usr/bin/tesseract", "name": "tesseract"},
        "ghostscript": {"present": True, "path": "/usr/bin/gs", "name": "gs"},
        "openssl_ts": {"present": True, "path": "/usr/bin/openssl", "ts_subcommand": True},
        "mcp": {"uvx_present": True, "uvx_path": "/usr/bin/uvx",
                "mcp_json_present": True, "declares_mcp_sqlite": True},
        "platform": {"os": "linux", "arch": "x86_64"},
    }


def test_all_present_every_capability_ready():
    cm = dt.build_capability_map(all_present_probes())
    assert set(cm) == {
        "analyze datasets", "ingest native PDFs", "OCR preprocessing for scans",
        "PII scan", "redaction QA", "citation verify", "evidence timestamp",
    }
    for name, cap in cm.items():
        assert cap["status"] == dt.READY, name
    s = dt.summarize(cm)
    assert s["core_structured_data"] == dt.READY
    assert s["document_workflows"] == "READY"


def test_all_absent_required_caps_unavailable():
    probes = all_present_probes()
    probes["dists"] = {d: {"present": False, "version": None} for d in ALL_DISTS}
    probes["spacy_model"] = {"present": False, "version": None}
    probes["tesseract"] = {"present": False, "path": None, "name": None}
    probes["ghostscript"] = {"present": False, "path": None, "name": None}
    probes["openssl_ts"] = {"present": False, "path": None, "ts_subcommand": False}
    probes["mcp"] = {"uvx_present": False, "uvx_path": None,
                     "mcp_json_present": False, "declares_mcp_sqlite": False}
    cm = dt.build_capability_map(probes)
    for name, cap in cm.items():
        assert cap["status"] == dt.UNAVAILABLE, name
    s = dt.summarize(cm)
    assert s["core_structured_data"] == "NOT READY"
    assert s["core_ready"] is False
    assert s["document_workflows"] == "UNAVAILABLE"


def test_independence_ocr_binaries_absent_does_not_break_ingest():
    """The load-bearing honesty rule: missing Tesseract/Ghostscript makes ONLY
    'OCR preprocessing for scans' unavailable; ingest native PDFs stays READY."""
    probes = all_present_probes()
    probes["tesseract"] = {"present": False, "path": None, "name": None}
    probes["ghostscript"] = {"present": False, "path": None, "name": None}
    cm = dt.build_capability_map(probes)
    assert cm["OCR preprocessing for scans"]["status"] == dt.UNAVAILABLE
    assert cm["ingest native PDFs"]["status"] == dt.READY
    # names the missing binaries, not a package
    miss = " ".join(cm["OCR preprocessing for scans"]["missing"]).lower()
    assert "tesseract" in miss and "ghostscript" in miss


def test_xray_absent_redaction_degraded_not_unavailable():
    probes = all_present_probes()
    probes["dists"]["x-ray"] = {"present": False, "version": None}
    cm = dt.build_capability_map(probes)
    cap = cm["redaction QA"]
    assert cap["status"] == dt.DEGRADED
    assert any("x-ray" in m for m in cap["optional_missing"])


def test_uvx_absent_analyze_datasets_degraded_not_unavailable():
    probes = all_present_probes()
    probes["mcp"]["uvx_present"] = False
    probes["mcp"]["uvx_path"] = None
    cm = dt.build_capability_map(probes)
    assert cm["analyze datasets"]["status"] == dt.DEGRADED


def test_core_headline_is_binary_when_only_uvx_missing():
    """The core HEADLINE stays READY when only an optional (uvx) is missing; the
    reduction lives in the capability map, not the headline (design 3.1 binary)."""
    probes = all_present_probes()
    probes["mcp"]["uvx_present"] = False
    s = dt.summarize(dt.build_capability_map(probes))
    assert s["core_structured_data"] == "READY"
    assert s["core_ready"] is True


def test_mcp_json_missing_or_undeclared_degrades_analyze_datasets():
    """uvx present but .mcp.json missing or not declaring mcp-sqlite -> DEGRADED,
    never a false READY for the conversational query surface."""
    for bad in ({"mcp_json_present": False}, {"declares_mcp_sqlite": False}):
        probes = all_present_probes()
        probes["mcp"].update(bad)
        cm = dt.build_capability_map(probes)
        assert cm["analyze datasets"]["status"] == dt.DEGRADED


def test_optional_fix_for_system_binaries_is_not_bootstrap():
    """A missing SYSTEM binary (uvx, openssl ts) must NOT be told to run bootstrap
    -- bootstrap is pip and cannot install a system binary."""
    probes = all_present_probes()
    probes["mcp"]["uvx_present"] = False
    probes["openssl_ts"]["ts_subcommand"] = False
    cm = dt.build_capability_map(probes)
    assert "bootstrap" not in cm["analyze datasets"]["fix"].lower()
    assert "bootstrap" not in cm["evidence timestamp"]["fix"].lower()
    # a missing REQUIRED pip dep still points at bootstrap
    probes2 = all_present_probes()
    probes2["dists"]["docling"] = {"present": False, "version": None}
    cm2 = dt.build_capability_map(probes2)
    assert "bootstrap" in cm2["ingest native PDFs"]["fix"].lower()


def test_openssl_ts_absent_evidence_degraded():
    probes = all_present_probes()
    probes["openssl_ts"]["ts_subcommand"] = False
    cm = dt.build_capability_map(probes)
    assert cm["evidence timestamp"]["status"] == dt.DEGRADED


def test_spacy_model_absent_pii_unavailable_names_model():
    probes = all_present_probes()
    probes["spacy_model"] = {"present": False, "version": None}  # spacy dist still present
    cm = dt.build_capability_map(probes)
    cap = cm["PII scan"]
    assert cap["status"] == dt.UNAVAILABLE
    assert any("en_core_web_lg" in m for m in cap["missing"])


def test_citation_verify_tracks_ingest_stack():
    probes = all_present_probes()
    probes["dists"]["docling"] = {"present": False, "version": None}
    cm = dt.build_capability_map(probes)
    assert cm["citation verify"]["status"] == dt.UNAVAILABLE
    # ingest also unavailable, citation verify mirrors it
    assert cm["ingest native PDFs"]["status"] == dt.UNAVAILABLE


def test_document_workflows_partial_when_some_reduced():
    probes = all_present_probes()
    probes["tesseract"] = {"present": False, "path": None, "name": None}
    probes["ghostscript"] = {"present": False, "path": None, "name": None}
    cm = dt.build_capability_map(probes)
    s = dt.summarize(cm)
    assert s["document_workflows"] == "PARTIAL"
    assert "OCR preprocessing for scans" in s["document_reduced"]
```

### Step 1.2: Run the tests; verify they FAIL

`& .venv\Scripts\python.exe -m pytest tests/test_detect_tier.py -q`
Expected: collection error / FAIL ("module scripts.detect_tier not found" or
attribute errors). Good -- now implement.

### Step 1.3: Implement the pure core of scripts/detect_tier.py

```python
"""detect_tier.py -- Magpie Layer 0-1 capability probe (the setup/doctor engine).

Pure-core / IO-at-the-edge, like pii_sweep's injectable classifier and
ingest_gate's injectable wordlist: build_capability_map / summarize / render_text
are PURE functions of an injected probe dict (golden-testable with mocked
presence/absence); the check_* probe functions are the only IO edge (stdlib only:
importlib.metadata, importlib.util, shutil.which, subprocess -- no heavy imports,
no network, no side effects). Probing torch/docling/spacy via metadata.version
does NOT load them, so doctor stays fast.
"""
from __future__ import annotations

import importlib.metadata as _md
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

READY = "READY"
DEGRADED = "DEGRADED"
UNAVAILABLE = "UNAVAILABLE"

# Probe by DISTRIBUTION name (the names installed into the venv that
# requirements-dev.txt bootstraps -- some, like pikepdf / pdfminer.six / requests /
# cryptography, arrive as TRANSITIVE deps, not top-level pins, but are resolvable
# distributions all the same). dist name != import name (x-ray->xray,
# PyMuPDF->fitz, pdfminer.six->pdfminer, rfc3161-client->rfc3161_client,
# sqlite-utils->sqlite_utils, charset-normalizer->charset_normalizer), so
# metadata.version on the dist name is the unambiguous probe and never imports
# the package.
_CORE_DISTS = ["pandas", "numpy", "duckdb", "pyarrow", "openpyxl",
               "charset-normalizer", "sqlite-utils"]
_INGEST_DISTS = ["docling", "rapidocr", "onnxruntime", "torch"]
_REDACT_OFFLINE_DISTS = ["pikepdf", "pdfminer.six"]
_EVIDENCE_DISTS = ["rfc3161-client", "requests", "cryptography"]
_XRAY_DIST = "x-ray"
_OCRMYPDF_DIST = "ocrmypdf"
_SPACY_DIST = "spacy"
_SPACY_MODEL = "en_core_web_lg"

_ALL_DISTS = sorted(set(
    _CORE_DISTS + _INGEST_DISTS + _REDACT_OFFLINE_DISTS + _EVIDENCE_DISTS
    + [_XRAY_DIST, _OCRMYPDF_DIST, _SPACY_DIST]
))

_DOC_CAPS = ["ingest native PDFs", "PII scan", "redaction QA", "citation verify",
             "evidence timestamp", "OCR preprocessing for scans"]


def _missing(dists, names):
    return [n for n in names if not dists.get(n, {}).get("present")]


def _cap(requires, missing, optional_missing, blocks, fix, optional_fix=None,
         degraded_note=None, unavailable_note=None):
    # fix is for the UNAVAILABLE (required-missing) case; optional_fix is for the
    # DEGRADED (optional-missing) case -- a system binary that pip/bootstrap
    # cannot install must NOT be told to "run bootstrap".
    if missing:
        status, the_fix = UNAVAILABLE, fix
    elif optional_missing:
        status = DEGRADED
        the_fix = optional_fix if optional_fix is not None else fix
    else:
        status, the_fix = READY, None
    entry = {
        "status": status,
        "requires": list(requires),
        "missing": list(missing),
        "optional_missing": list(optional_missing),
        "blocks": blocks,
        "fix": the_fix,
    }
    if status == DEGRADED and degraded_note:
        entry["note"] = degraded_note
    if status == UNAVAILABLE and unavailable_note:
        entry["note"] = unavailable_note
    return entry


def build_capability_map(probes):
    """PURE. probes (the run_probes shape) -> {capability: cap-entry}. No IO."""
    dists = probes.get("dists", {})
    model = probes.get("spacy_model", {})
    tess = probes.get("tesseract", {})
    gs = probes.get("ghostscript", {})
    ossl = probes.get("openssl_ts", {})
    mcp = probes.get("mcp", {})

    caps = {}

    # The conversational query surface needs BOTH uvx (to launch the server) AND
    # a .mcp.json that declares mcp-sqlite -- all three, or it is unavailable.
    mcp_ok = (mcp.get("uvx_present") and mcp.get("mcp_json_present")
              and mcp.get("declares_mcp_sqlite"))
    caps["analyze datasets"] = _cap(
        requires=_CORE_DISTS,
        missing=_missing(dists, _CORE_DISTS),
        optional_missing=([] if mcp_ok
                          else ["the conversational mcp-sqlite query surface (uvx + .mcp.json wiring)"]),
        blocks="Quantitative analysis of FOIA CSV/XLSX releases (stats, recipes, rollups).",
        degraded_note="analysis runs, but the conversational SQL query surface (mcp-sqlite) is unavailable",
        fix="run setup (mise run bootstrap)",
        optional_fix="install uv (provides uvx) and ensure .mcp.json declares the mcp-sqlite server; see OPERATOR_GUIDE.md",
    )

    caps["ingest native PDFs"] = _cap(
        requires=_INGEST_DISTS,
        missing=_missing(dists, _INGEST_DISTS),
        optional_missing=[],
        blocks="Turning PDF document releases into clean, citable text.",
        fix="run setup (mise run bootstrap)",
    )

    ocr_missing = _missing(dists, [_OCRMYPDF_DIST])
    if not tess.get("present"):
        ocr_missing.append("tesseract (system binary)")
    if not gs.get("present"):
        ocr_missing.append("ghostscript (system binary)")
    caps["OCR preprocessing for scans"] = _cap(
        requires=[_OCRMYPDF_DIST, "tesseract", "ghostscript"],
        missing=ocr_missing,
        optional_missing=[],
        blocks="Deskew / re-OCR of ugly scanned PDFs before ingest (native-text PDFs are unaffected).",
        fix="install Tesseract + Ghostscript (see OPERATOR_GUIDE.md), then run setup",
    )

    pii_missing = _missing(dists, [_SPACY_DIST])
    if not model.get("present"):
        pii_missing.append(_SPACY_MODEL + " (spaCy model)")
    caps["PII scan"] = _cap(
        requires=[_SPACY_DIST, _SPACY_MODEL],
        missing=pii_missing,
        optional_missing=[],
        blocks="Authoritative PERSON-name + structured-PII exposure tally; redaction of uninvolved names.",
        fix="run setup (mise run bootstrap)",
    )

    caps["redaction QA"] = _cap(
        requires=_REDACT_OFFLINE_DISTS,
        missing=_missing(dists, _REDACT_OFFLINE_DISTS),
        optional_missing=([] if dists.get(_XRAY_DIST, {}).get("present")
                          else ["x-ray (box-over-text, the 8th check)"]),
        blocks="Finding bad redactions in a received PDF and pre-publish self-checks.",
        degraded_note="7 of 8 checks run; the x-ray box-over-text check is unavailable",
        fix="run setup (mise run bootstrap)",
    )

    caps["citation verify"] = _cap(
        requires=["(stdlib engine)"] + _INGEST_DISTS,
        missing=_missing(dists, _INGEST_DISTS),
        optional_missing=[],
        blocks="Anchoring published claims to a verifiable source span in an ingested document.",
        unavailable_note="the citation engine is stdlib, but verifying needs the ingest stack to produce documents",
        fix="run setup (mise run bootstrap)",
    )

    caps["evidence timestamp"] = _cap(
        requires=_EVIDENCE_DISTS,
        missing=_missing(dists, _EVIDENCE_DISTS),
        optional_missing=([] if ossl.get("ts_subcommand")
                          else ["openssl 'ts' subcommand (cross-tool verify)"]),
        blocks="Hash-on-receipt + RFC 3161 trusted timestamp + chain-of-custody for FOIA evidence.",
        degraded_note="timestamping and verify-on-store work; the openssl second-tool cross-check is unavailable",
        fix="run setup (mise run bootstrap)",
        optional_fix="install OpenSSL providing the 'ts' subcommand; see OPERATOR_GUIDE.md",
    )

    return caps


def summarize(capability_map):
    """PURE. The two-line subordinate headline. Core is BINARY READY/NOT READY
    (required deps only -- an optional reduction like a missing uvx lives in the
    capability map, NOT the headline); the document rollup is
    READY/PARTIAL/UNAVAILABLE. NO 1/2/3 tier score is ever produced."""
    core_status = capability_map.get("analyze datasets", {}).get("status")
    core_ready = core_status != UNAVAILABLE  # DEGRADED (e.g. no uvx) still READY in the headline
    doc_statuses = {c: capability_map.get(c, {}).get("status") for c in _DOC_CAPS}
    vals = list(doc_statuses.values())
    if vals and all(v == READY for v in vals):
        doc = "READY"
    elif vals and all(v == UNAVAILABLE for v in vals):
        doc = "UNAVAILABLE"
    else:
        doc = "PARTIAL"
    reduced = [c for c, st in doc_statuses.items() if st != READY]
    return {
        "core_structured_data": "READY" if core_ready else "NOT READY",
        "core_ready": core_ready,
        "document_workflows": doc,
        "document_reduced": reduced,
    }
```

### Step 1.4: Run the pure tests; verify PASS

`& .venv\Scripts\python.exe -m pytest tests/test_detect_tier.py -q`
Expected: the Step 1.1 tests PASS. Commit.

### Step 1.5: Add the probe-edge tests (monkeypatched stdlib)

Append to `tests/test_detect_tier.py`:

```python
def test_check_python_dist_present(monkeypatch):
    monkeypatch.setattr(dt._md, "version", lambda name: "1.2.3")
    r = dt.check_python_dist("pandas")
    assert r == {"present": True, "version": "1.2.3"}


def test_check_python_dist_absent(monkeypatch):
    def boom(name):
        raise dt._md.PackageNotFoundError(name)
    monkeypatch.setattr(dt._md, "version", boom)
    r = dt.check_python_dist("nope")
    assert r == {"present": False, "version": None}


def test_check_binary_list_fallback(monkeypatch):
    # gswin64c missing, gs present -> returns the gs hit
    seen = {}
    def which(n):
        return "/usr/bin/gs" if n == "gs" else None
    monkeypatch.setattr(dt.shutil, "which", which)
    r = dt.check_binary(["gswin64c", "gswin32c", "gs"])
    assert r["present"] and r["name"] == "gs"
    assert dt.check_binary("nope")["present"] is False


def test_check_openssl_ts(monkeypatch):
    monkeypatch.setattr(dt.shutil, "which", lambda n: "/usr/bin/openssl")
    monkeypatch.setattr(dt.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 0})())
    r = dt.check_openssl_ts()
    assert r["present"] and r["ts_subcommand"] is True
    monkeypatch.setattr(dt.shutil, "which", lambda n: None)
    assert dt.check_openssl_ts()["present"] is False


def test_check_mcp_wiring_is_read_only(monkeypatch, tmp_path):
    """The Codex risk fix: check_mcp_wiring must NOT execute uvx (no subprocess)."""
    mcp = tmp_path / ".mcp.json"
    mcp.write_text('{"mcpServers": {"magpie-dataset": {"args": ["mcp-sqlite==0.3.2"]}}}',
                   encoding="utf-8")
    monkeypatch.setattr(dt.shutil, "which", lambda n: "/usr/bin/uvx")
    calls = []
    monkeypatch.setattr(dt.subprocess, "run",
                        lambda *a, **k: calls.append(a) or None)
    r = dt.check_mcp_wiring(mcp)
    assert r["uvx_present"] and r["mcp_json_present"] and r["declares_mcp_sqlite"]
    assert calls == []  # NEVER ran a subprocess (no uvx execution)
    missing = tmp_path / "absent.json"
    r2 = dt.check_mcp_wiring(missing)
    assert r2["mcp_json_present"] is False and r2["declares_mcp_sqlite"] is False


def test_render_text_has_no_tier_language():
    cm = dt.build_capability_map(all_present_probes())
    report = {"capabilities": cm, "summary": dt.summarize(cm),
              "probes": all_present_probes(),
              "python": {"version": "3.12.10", "executable": "x"}}
    out = dt.render_text(report)
    low = out.lower()
    assert "core structured-data analysis" in low
    assert "document workflows" in low
    # honesty guard: never expose a linear tier / "full" score to a user
    assert "tier 1" not in low and "tier 2" not in low and "tier 3" not in low
    for name in cm:
        assert name in out


def test_detect_smoke():
    report = dt.detect()
    assert set(report) >= {"capabilities", "summary", "probes", "python"}
    assert "analyze datasets" in report["capabilities"]


def test_cli_json(capsys):
    rc = dt.main(["--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "capabilities" in payload


def test_import_is_cheap():
    """Importing the module must not pull in heavy stacks (subprocess-isolated)."""
    code = ("import importlib, sys; importlib.import_module('scripts.detect_tier'); "
            "heavy = [m for m in ('torch','docling','spacy','fitz') if m in sys.modules]; "
            "print(heavy); sys.exit(1 if heavy else 0)")
    p = subprocess.run([sys.executable, "-c", code],
                       cwd=str(Path(__file__).resolve().parent.parent),
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
```

### Step 1.6: Implement the probe edge + detect + render + CLI

Append to `scripts/detect_tier.py`:

```python
def check_python_dist(dist_name):
    """Distribution presence + version WITHOUT importing the package."""
    try:
        return {"present": True, "version": _md.version(dist_name)}
    except _md.PackageNotFoundError:
        return {"present": False, "version": None}


def check_spacy_model(name=_SPACY_MODEL):
    """spaCy model presence via distribution metadata (NEVER loads the model)."""
    return check_python_dist(name)


def check_binary(names):
    """shutil.which over a name or a list of fallback names; first hit wins."""
    if isinstance(names, str):
        names = [names]
    for n in names:
        path = shutil.which(n)
        if path:
            return {"present": True, "path": path, "name": n}
    return {"present": False, "path": None, "name": None}


def check_openssl_ts(timeout=10):
    """openssl on PATH + whether it carries the `ts` subcommand (rc==0 on ts -help)."""
    path = shutil.which("openssl")
    if not path:
        return {"present": False, "path": None, "ts_subcommand": False}
    try:
        p = subprocess.run([path, "ts", "-help"], capture_output=True,
                           text=True, timeout=timeout)
        return {"present": True, "path": path, "ts_subcommand": p.returncode == 0}
    except Exception:
        return {"present": True, "path": path, "ts_subcommand": False}


def check_mcp_wiring(mcp_json_path):
    """READ-ONLY: uvx on PATH + .mcp.json TEXT references the mcp-sqlite server.
    Does NOT execute uvx and does NOT start the server (no side effects, no network).
    """
    uvx = shutil.which("uvx")
    p = Path(mcp_json_path)
    present = p.is_file()
    declares = False
    if present:
        try:
            declares = "mcp-sqlite" in p.read_text(encoding="utf-8")
        except OSError:
            present = False
    return {"uvx_present": uvx is not None, "uvx_path": uvx,
            "mcp_json_present": present, "declares_mcp_sqlite": declares}


def run_probes(mcp_json_path, repo_root=None):
    """Run every probe; return the dict build_capability_map consumes. The IO edge."""
    dists = {d: check_python_dist(d) for d in _ALL_DISTS}
    return {
        "dists": dists,
        "spacy_model": check_spacy_model(),
        "tesseract": check_binary("tesseract"),
        "ghostscript": check_binary(["gswin64c", "gswin32c", "gs"]),
        "openssl_ts": check_openssl_ts(),
        "mcp": check_mcp_wiring(mcp_json_path),
        "platform": {"os": platform.system().lower(), "arch": platform.machine()},
    }


def detect(mcp_json_path=None, repo_root=None):
    """The one IO entry point: probe -> capability map -> summary -> full report."""
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parent.parent
    if mcp_json_path is None:
        mcp_json_path = root / ".mcp.json"
    probes = run_probes(mcp_json_path, root)
    cap_map = build_capability_map(probes)
    return {
        "capabilities": cap_map,
        "summary": summarize(cap_map),
        "probes": probes,
        "python": {"version": sys.version.split()[0], "executable": sys.executable},
    }


_STATUS_MARK = {READY: "[OK]", DEGRADED: "[~]", UNAVAILABLE: "[X]"}


def render_text(report):
    """Plain-text rendering of the capability map + the subordinate headline."""
    s = report["summary"]
    lines = ["Magpie health check", "=" * 40]
    # core headline is binary (READY / NOT READY); optional reductions show in the map
    lines.append("core structured-data analysis: " + s["core_structured_data"])
    doc_line = "document workflows: " + s["document_workflows"]
    if s["document_workflows"] == "PARTIAL" and s["document_reduced"]:
        doc_line += " (reduced: " + ", ".join(s["document_reduced"]) + ")"
    lines.append(doc_line)
    lines.append("")
    lines.append("Capabilities:")
    for name, cap in report["capabilities"].items():
        mark = _STATUS_MARK.get(cap["status"], "[?]")
        lines.append("  " + mark + " " + name + " -- " + cap["status"])
        if cap["status"] != READY:
            if cap.get("missing"):
                lines.append("      missing: " + ", ".join(cap["missing"]))
            if cap.get("optional_missing"):
                lines.append("      optional: " + ", ".join(cap["optional_missing"]))
            if cap.get("note"):
                lines.append("      note: " + cap["note"])
            lines.append("      blocks: " + cap["blocks"])
            if cap.get("fix"):
                lines.append("      fix: " + cap["fix"])
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    report = detect()
    if "--json" in argv:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### Step 1.7: Run the full module test + the offline suite; verify PASS

`& .venv\Scripts\python.exe -m pytest tests/test_detect_tier.py -q`
then the whole offline suite (must stay green):
`& .venv\Scripts\python.exe -m pytest -m "not docling and not spacy and not xray and not tsa" -q`
Expected: all pass.

### Step 1.8: Commit

```
git add scripts/detect_tier.py tests/test_detect_tier.py
git commit -F <ascii-msg-file>
```
Message subject: `feat(detect-tier): pure capability-map probe engine + CLI (Phase 10.1)`

---

## Task 2: setup + doctor skills + smoke tests (TDD via the smoke tests)

**Files:**
- Create: `skills/setup/SKILL.md`
- Create: `skills/doctor/SKILL.md`
- Test: `tests/test_setup_skill.py`
- Test: `tests/test_doctor_skill.py`

### Step 2.1: Write the failing smoke tests

`tests/test_setup_skill.py`:

```python
"""Smoke test for the setup SKILL.md."""
from __future__ import annotations

from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent / "skills" / "setup" / "SKILL.md"


def _frontmatter_and_body(p):
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---")
    _, fm, body = text.split("---", 2)
    return yaml.safe_load(fm), body


def test_frontmatter():
    fm, _ = _frontmatter_and_body(SKILL)
    assert fm["name"] == "setup"
    assert "version" in fm
    d = fm["description"].lower()
    assert "operator" in d or "install" in d or "set up" in d


def test_body_documents_setup_contract():
    _, body = _frontmatter_and_body(SKILL)
    low = body.lower()
    assert "detect_tier" in body                       # names the engine
    assert "mise run bootstrap" in low                 # runs the repo-managed step
    assert "tesseract" in low and "ghostscript" in low # instructs for system binaries
    assert "operator" in low
    # setup MAY install; doctor is the read-only sibling -- the asymmetry is stated
    assert "doctor" in low
    # no Docker anywhere in Layer 0-1
    assert "docker" not in low
```

`tests/test_doctor_skill.py`:

```python
"""Smoke test for the doctor SKILL.md (read-only health check)."""
from __future__ import annotations

from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent / "skills" / "doctor" / "SKILL.md"


def _frontmatter_and_body(p):
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---")
    _, fm, body = text.split("---", 2)
    return yaml.safe_load(fm), body


def test_frontmatter():
    fm, _ = _frontmatter_and_body(SKILL)
    assert fm["name"] == "doctor"
    assert "version" in fm
    d = fm["description"].lower()
    assert "health" in d or "check" in d or "diagnos" in d


def test_body_documents_read_only_contract():
    _, body = _frontmatter_and_body(SKILL)
    low = body.lower()
    assert "detect_tier" in body
    assert "read-only" in low or "read only" in low
    # never installs / never runs setup / never starts the server
    assert "never" in low
    assert "setup" in low                              # points back to setup/operator
    assert "docker" not in low
```

### Step 2.2: Run -> FAIL (skills absent). Then author the two SKILL.md files.

`skills/setup/SKILL.md` -- mirror the house style. Frontmatter `name: setup`,
a description like: "Use when an operator is setting up Magpie on a new machine
for the first time and needs to install the Python dependencies, download the
spaCy model, and verify the local toolchain. This skill runs the repo-managed
bootstrap, instructs the operator for the system binaries pip cannot install, and
re-checks the capability map. Invoke it whenever a user wants to install Magpie,
set up the environment, bootstrap the venv, or fix a missing dependency reported
by doctor.", `version: 0.1.0`.

Body sections (keep ASCII, imperative, third-person):
1. What setup does + that it calls `scripts/detect_tier.py` (the shared engine).
2. The flow: (a) run detect_tier and SHOW present-vs-missing; (b) with the
   operator present and consenting, run `mise run bootstrap` (pip deps + the
   en_core_web_lg wheel; first-run Docling/RapidOCR weights download on first
   ingest) -- the fallback for the -NoProfile shell is
   `& .venv\Scripts\python.exe -m pip install -r requirements-dev.txt`;
   (c) for the system binaries pip cannot manage (uv/uvx, Tesseract, Ghostscript)
   INSTRUCT with exact per-OS commands (winget / choco / apt / brew) -- setup
   never silently installs a system binary; (d) re-run detect_tier and report the
   now-current capability map.
3. The setup/doctor asymmetry: setup MAY install; doctor is strictly read-only.
4. Note the bundled public sample corpus (corpus/public/) is forthcoming
   (Phase 11); setup does not download a corpus.
5. Honest limit: detect_tier reports presence + version, not correctness.

`skills/doctor/SKILL.md` -- frontmatter `name: doctor`, a description like: "Use
when a journalist or investigator wants to check whether Magpie is healthy on
this machine -- which analysis and document workflows are ready, what is missing,
and what to ask their operator to fix. This is a strictly read-only health check;
it installs nothing. Invoke it whenever a user wants to run a health check,
diagnose why a Magpie skill is unavailable, or see what Magpie can currently do."
`version: 0.1.0`.

Body sections:
1. What doctor does + that it calls `scripts/detect_tier.py` read-only (e.g. the
   operator can also run `& .venv\Scripts\python.exe scripts\detect_tier.py`).
2. It renders the capability map (user verbs) + the subordinate headline + for
   each gap what it blocks + the one instruction "ask your operator to run setup"
   (or the one-line system-binary hint).
3. The READ-ONLY contract, stated strongly: doctor NEVER installs, NEVER runs
   `mise run bootstrap`, NEVER invokes setup, and NEVER starts the mcp-sqlite
   server (it only checks that uvx exists and that .mcp.json declares the server).
4. Honest limit: presence + version, not correctness.

### Step 2.3: Run the smoke tests -> PASS. Commit.

`& .venv\Scripts\python.exe -m pytest tests/test_setup_skill.py tests/test_doctor_skill.py -q`
Commit subject: `feat(setup-doctor): operator setup + journalist doctor skills (Phase 10.2)`

---

## Task 3: onramp docs + README + the no-Docker guard test

**Files:**
- Create: `docs/OPERATOR_GUIDE.md`
- Create: `docs/JOURNALIST_START.md`
- Test: `tests/test_onramp_docs.py`
- (Modify `README.md` is a MAIN-THREAD step, NOT this subagent's -- see the note
  below: README.md carries non-ASCII em-dashes that would block a subagent Read.)

> **SUBAGENT SCOPE:** create the two new docs + the test ONLY. Do NOT edit
> README.md. The orchestrator (main thread) applies the README polish (inlined in
> Step 3.0 below) BEFORE this task runs, so the README assertions in the guard
> test are already satisfiable when you run it.

### Step 3.0: (MAIN THREAD, not the subagent) polish README.md

README.md is non-ASCII (em-dashes), so the orchestrator edits it directly. Replace
the existing "## Getting started" section with this exact block (em-dashes are fine
here -- README is not subagent-read):

```markdown
## Getting started

Magpie has **two onramps**, one per person who touches it:

- **Operators** set it up once -- install dependencies, download the spaCy model,
  verify the toolchain: [`docs/OPERATOR_GUIDE.md`](docs/OPERATOR_GUIDE.md). Run the
  **`setup`** skill, or `& .venv\Scripts\python.exe scripts\detect_tier.py` to
  install-and-verify outside Claude Code.
- **Investigators** use it every day, conversationally, with no infrastructure to
  manage: [`docs/JOURNALIST_START.md`](docs/JOURNALIST_START.md). Run the
  **`doctor`** skill anytime for a read-only health check of what is ready.

Layer 0-1 runs on a laptop with no heavy infrastructure: pandas + DuckDB, spaCy,
Docling, and Free Law `x-ray`, queried through a pinned read-only `mcp-sqlite`.
```

(Use real em-dashes in the prose if you prefer; the guard test only checks the
lowercased tokens "two onramps", "setup", "doctor", "detect_tier",
"operator_guide.md", "journalist_start.md".) After this main-thread edit, the
subagent's guard test README assertions pass.

### Step 3.1: Write the failing guard test

`tests/test_onramp_docs.py`:

```python
"""Guards for the dual onramp: the journalist guide never mentions Docker, and
the operator guide stays Layer 0-1 (no Docker either)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPERATOR = ROOT / "docs" / "OPERATOR_GUIDE.md"
JOURNALIST = ROOT / "docs" / "JOURNALIST_START.md"
README = ROOT / "README.md"


def test_guides_exist():
    assert OPERATOR.is_file()
    assert JOURNALIST.is_file()


def test_journalist_guide_never_mentions_docker():
    assert "docker" not in JOURNALIST.read_text(encoding="utf-8").lower()


def test_operator_guide_is_layer_0_1_no_docker():
    low = OPERATOR.read_text(encoding="utf-8").lower()
    assert "docker" not in low
    assert "mise run bootstrap" in low                 # the real setup step
    assert "detect_tier" in low                        # the verify step


def test_journalist_guide_is_conversational_not_infra():
    low = JOURNALIST.read_text(encoding="utf-8").lower()
    assert "doctor" in low                              # points at the health check
    # journalist terms, not infra plumbing
    assert "pip install" not in low and "venv" not in low


def test_readme_routes_both_personas():
    low = README.read_text(encoding="utf-8").lower()
    assert "operator_guide.md" in low and "journalist_start.md" in low
    # the polished dual-onramp routes by SKILL (setup / doctor), not just by file,
    # and mentions the health check -- pins the main-thread README polish so the
    # test is not vacuously green against the pre-existing README
    assert "two onramps" in low
    assert "setup" in low and "doctor" in low
    assert "detect_tier" in low
```

### Step 3.2: Run -> FAIL. Author the docs.

`docs/OPERATOR_GUIDE.md` -- one-time technical setup, Layer 0-1, NO Docker
anywhere (not even a footnote). Sections: Prerequisites (Python 3.12 via mise;
uv/uvx; the OPTIONAL Tesseract + Ghostscript for scan preprocessing, with per-OS
install hints); Install (`mise run bootstrap`, and the explicit
`& .venv\Scripts\python.exe -m pip install -r requirements-dev.txt` fallback for
the -NoProfile shell); Model weights (the en_core_web_lg ~400 MB wheel + the
first-run Docling/RapidOCR download, size + timing); Verify
(`& .venv\Scripts\python.exe scripts\detect_tier.py`, or the setup skill);
Troubleshooting (the bare-`python` -NoProfile trap; dist-name vs import-name
confusion; what each missing system binary disables -> exactly which capability
goes UNAVAILABLE/DEGRADED).

`docs/JOURNALIST_START.md` -- daily conversational use, journalist terms, NO
Docker, NO pip/venv/package names. Sections: What Magpie does for you (analyze a
FOIA spreadsheet; read a scanned PDF release; sweep for PII; check a redaction;
get a citable claim; timestamp received evidence); How to use it (open Claude
Code and ask in plain language; one or two example asks); If something looks off
(run doctor; if it says something is missing, ask whoever set this up to run
setup). Keep it short and welcoming.

`README.md` -- already handled by the orchestrator in Step 3.0 (main thread). The
subagent does NOT touch README.md. Just author the two docs above and the guard
test; the README assertions are already green from Step 3.0.

### Step 3.3: Run the guard test + full offline suite -> PASS. Commit.

`& .venv\Scripts\python.exe -m pytest tests/test_onramp_docs.py -q`
then `& .venv\Scripts\python.exe -m pytest -m "not docling and not spacy and not xray and not tsa" -q`
Commit subject: `docs(phase10): operator + journalist onramps + dual-onramp README`

---

## Final verification (before the impl-review gate)

1. Full offline suite green:
   `& .venv\Scripts\python.exe -m pytest -m "not docling and not spacy and not xray and not tsa" -q`
   (expect the prior count + the new detect_tier / skill / doc tests).
2. The CLI runs on this box:
   `& .venv\Scripts\python.exe scripts\detect_tier.py`
   (expect: core READY, document workflows PARTIAL -- OCR preprocessing for scans
   UNAVAILABLE because Tesseract/Ghostscript are absent here).
   `& .venv\Scripts\python.exe scripts\detect_tier.py --json` (valid JSON).
3. ASCII check every SUBAGENT-CREATED file (NOT README.md -- README is
   intentionally non-ASCII, main-thread-authored, and never subagent-read):
   no matches for `[^\x00-\x7F]` in scripts/detect_tier.py, tests/test_detect_tier.py,
   the two SKILL.md, docs/OPERATOR_GUIDE.md, docs/JOURNALIST_START.md,
   tests/test_setup_skill.py, tests/test_doctor_skill.py, tests/test_onramp_docs.py.
   (README.md already carries non-ASCII outside the Getting started block; it is
   deliberately excluded from this gate.)
4. Confirm no new dependency was added (requirements-dev.txt unchanged).

## Non-goals (do NOT build)

No Docker / docker-compose / Layer-2 wiring; no auto-install of system binaries;
no hardware (RAM/VRAM) probe or local-LLM model recommendation; no running-service
health probe and no uvx execution from doctor; no deep "does the spaCy model load"
check; setup does not bundle/download the public sample corpus (Phase 11).

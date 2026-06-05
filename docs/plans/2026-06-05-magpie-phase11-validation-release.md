# Magpie Phase 11 -- Validation & Release Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development to
> execute this plan task-by-task in this session. Design source of truth:
> docs/plans/2026-06-05-magpie-phase11-validation-release-design.md.

**Goal:** Validate the Layer 0-1 engine against real + public corpora and cut the
v0.1.0 release: a PII-free public sample (machinery now, RANGE slice fast-follow),
env-gated golden tests pinning the documented Simpsonville pilot values, CI +
structural/install smoke, and the v0.1.0 tag.

**Architecture:** Pure additive validation + release scaffolding. New code is small
and offline-testable: a deterministic slice builder, Flock-format test adapters,
golden + smoke tests, and GitHub Actions CI. No engine changes, no new deps.

**Tech Stack:** Python 3.12 (.venv), pytest (markers: spacy/docling/xray/tsa +
skipif env-gating), pandas/numpy, the existing magpie scripts (stats, load_table,
data_quality, derive, recipe, pii_sweep, citation, build_dataset_db), GitHub Actions.

---

## SDD-DISPATCH RULES (apply to EVERY subagent task below)

Bake these into every Agent dispatch prompt verbatim:

1. "You are ALREADY on the feature branch `feat/phase11-validation-release`. Commit
   your work directly to it. Do NOT create or switch branches."
2. "Read ONLY this plan, the design doc, and the files you create. Do NOT open any
   other repo file with the Read tool -- most existing files carry non-ASCII that
   will content-filter-block you. All house-style and APIs you need are inlined
   below." (Importing a module at test RUNTIME is fine; only the Read tool blocks.)
3. Keep everything you write ASCII-only (no smart quotes, em-dashes, or exotic
   glyphs). Use plain ASCII in code, comments, and fixtures.
4. Run the offline suite before declaring done:
   `& .venv\Scripts\python.exe -m pytest -m "not docling and not spacy and not xray and not tsa" -q`
   (must stay green: 494 passed / 1 skipped, plus your new offline tests).
   NEVER bare `python` (the tool shell is -NoProfile -> global interpreter).
5. Fix subagents run SEQUENTIALLY on this shared branch (concurrent commits race the
   git index lock).

**MAIN-THREAD-ONLY** (never dispatched to a subagent): editing non-ASCII files
(MANIFEST.md, README.md); reading/running against the private Simpsonville corpus;
pinning the 11.2 exact values; acquiring/scrubbing/bundling the RANGE slice + Skokie
PDF; the RANGE permission email; PR/merge/tag.

Status legend: [SUBAGENT n] buildable by a dispatched implementer; [MAIN] main thread.

---

## Task A -- 11.1 public-corpus machinery  [SUBAGENT 1]

No real corpus data is touched here. Builds the deterministic slice tool, the
provenance datasheet, and the skip-if-absent public goldens. All fixtures synthetic.

### A1: tools/build_public_slice.py + test

**Files:** Create `tools/build_public_slice.py`, `tests/test_build_public_slice.py`.

**Step 1 -- failing test** (`tests/test_build_public_slice.py`):

```python
import pandas as pd
from tools.build_public_slice import build_slice

def _frame():
    # synthetic, deliberately UNSORTED, with one all-blank row
    return pd.DataFrame({
        "Org Name": ["Zeta PD TX", "Alpha PD SC", "Alpha PD SC", "", "Mid PD NC"],
        "Search Time": ["03/30/2026, 01:00:00 PM", "03/30/2026, 09:00:00 AM",
                        "03/30/2026, 08:00:00 AM", "", "03/30/2026, 10:00:00 AM"],
        "Reason": ["Traffic - x", "Homicide - y", "Drugs - z", "", "Welfare - w"],
    })

def test_slice_is_deterministic_and_sorted_then_head_n():
    df = _frame()
    out1 = build_slice(df, n=3, sort_columns=["Org Name", "Search Time"])
    out2 = build_slice(df, n=3, sort_columns=["Org Name", "Search Time"])
    # deterministic: identical bytes on repeat
    assert out1.to_csv(index=False) == out2.to_csv(index=False)
    # all-blank row dropped; stable total-order sort then first 3
    assert list(out1["Org Name"]) == ["Alpha PD SC", "Alpha PD SC", "Mid PD NC"]
    # the two Alpha rows are tie-broken by Search Time ascending (08:00 before 09:00)
    assert list(out1["Search Time"])[:2] == ["03/30/2026, 08:00:00 AM",
                                             "03/30/2026, 09:00:00 AM"]

def test_drop_all_empty_rows_only_drops_fully_blank():
    df = pd.DataFrame({"a": ["x", "", " "], "b": ["", "", ""]})
    out = build_slice(df, n=10, drop_all_empty_rows=True)
    assert list(out["a"]) == ["x"]  # rows 2 and 3 are fully blank -> dropped
```

**Step 2 -- run, expect fail** (`ModuleNotFoundError: tools.build_public_slice`):
`& .venv\Scripts\python.exe -m pytest tests/test_build_public_slice.py -q`

**Step 3 -- implement** (`tools/build_public_slice.py`), complete code:

```python
"""Build a small, deterministic, NEUTRAL slice of a large public CSV.

For Magpie's corpus/public/ "try it now" + CI golden source. The slice rule is
intentionally neutral (NO outcome tuning): a stable total-order sort, then the first
N rows. Anyone can re-run this against the source to reproduce the slice
byte-for-byte. See the Phase 11 design doc, section 3.2.
"""
from __future__ import annotations

import argparse
from typing import Sequence

import pandas as pd


def build_slice(
    df: pd.DataFrame,
    *,
    n: int,
    sort_columns: Sequence[str] | None = None,
    drop_all_empty_rows: bool = True,
) -> pd.DataFrame:
    """Return a deterministic first-N slice after a stable total-order sort.

    drop_all_empty_rows: the ONE permitted STRUCTURAL filter -- drop rows whose every
    cell is blank/NA/whitespace. Never an outcome-based filter.
    sort_columns: leading sort keys; ALL remaining columns are appended as
    deterministic tiebreakers so the order is total (fully reproducible). Defaults to
    file column order.
    n: rows to keep -- fixed for file size, NOT tuned to outputs.
    """
    work = df.copy()
    if drop_all_empty_rows:
        nonblank = work.apply(
            lambda col: col.fillna("").astype(str).str.strip() != "", axis=0
        )
        work = work[nonblank.any(axis=1)]
    cols = list(work.columns)
    leading = [c for c in (sort_columns or []) if c in cols]
    order = leading + [c for c in cols if c not in leading]
    # Sort on a string view so mixed/blank dtypes order deterministically.
    key = work[order].fillna("").astype(str)
    work = work.loc[key.sort_values(by=list(order), kind="stable").index]
    return work.head(n).reset_index(drop=True)


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Build a deterministic public CSV slice.")
    ap.add_argument("source", help="path to the full source CSV (local; not committed)")
    ap.add_argument("out", help="path to write the slice CSV")
    ap.add_argument("-n", type=int, required=True, help="rows to keep (for file size)")
    ap.add_argument("--sort", nargs="*", default=None, help="leading sort columns")
    args = ap.parse_args(argv)
    df = pd.read_csv(args.source, dtype=str, keep_default_na=False, na_values=[""])
    sliced = build_slice(df, n=args.n, sort_columns=args.sort)
    sliced.to_csv(args.out, index=False)
    print(f"wrote {len(sliced)} rows -> {args.out}")


if __name__ == "__main__":
    main()
```

**Step 4 -- run, expect pass.** **Step 5 -- commit:**
`git add tools/build_public_slice.py tests/test_build_public_slice.py`
`git commit -m "feat(phase11): deterministic neutral public-CSV slice builder"`

### A2: corpus/public/DATASHEET.md (provenance template)

**Files:** Create `corpus/public/DATASHEET.md` (ASCII). No test (a doc). Template
with explicit TODO placeholders the main thread fills when artifacts land:

```markdown
# corpus/public -- Provenance Datasheet

Provenance + PII record for every bundled public artifact. A FOIA/PII tool carries
its own data's provenance. Nothing here is committed until it is PII-clean and (for
re-hosted third-party data) permission is recorded.

## <slice-filename>.csv  (RANGE Media -- Spokane County Flock network audit slice)
- Source URL: <RANGE public data page + the Google Drive link>
- Attribution: RANGE Media (https://www.rangemedia.co/public-flock-data/)
- License / permission: re-host permission GRANTED by RANGE on <DATE> (record kept
  out-of-repo). The full release carries no stated license; we re-host a slice with
  permission. NOT public domain.
- Slice rule: tools/build_public_slice.py, stable total-order sort by (Org Name,
  Search Time, <rest>), first N=<N> rows, drop-all-empty-rows. Reproducible.
- PII-scrub: pii_sweep over the `reason` column on <DATE>; result <clean | redacted
  via redact_output | reason column dropped>. Re-scan confirmed clean.
- sha256: <final sha256 of the committed slice>
- Status: <pending permission | bundled>

## <skokie-filename>.pdf  (Skokie PD -- Flock FOIA cover letter)
- Source URL: <MuckRock request URL + the CDN PDF URL>
- Attribution: Skokie Police Department, via MuckRock.
- License / permission: government record published on MuckRock (public-by-default);
  attribution to MuckRock + the agency.
- PII-scrub: ingest -> redaction_check (received) + pii_sweep over extracted text on
  <DATE>; human eyeball. Result <clean | not bundled>.
- sha256: <final sha256 of the committed PDF>
- Status: <pending vetting | bundled>
```

**Commit:** `git add corpus/public/DATASHEET.md`
`git commit -m "docs(phase11): corpus/public provenance datasheet template"`

### A3: tests/golden/public/test_public_corpus.py (skip-if-absent goldens)

**Files:** Create `tests/golden/__init__.py` (empty), `tests/golden/public/__init__.py`
(empty), `tests/golden/public/test_public_corpus.py`.

House-style: the test SKIPS cleanly when the slice fixture is not present (it is a
fast-follow), so the offline suite is green today. When the slice + a frozen
`expected.json` exist, it runs the offline pipeline and compares. Use pathlib;
compute paths relative to the repo root via `Path(__file__).resolve().parents[3]`.

```python
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SLICE = _REPO / "corpus" / "public" / "spokane_flock_slice.csv"
_EXPECTED = Path(__file__).resolve().parent / "expected_public.json"

pytestmark = pytest.mark.skipif(
    not (_SLICE.exists() and _EXPECTED.exists()),
    reason="public slice + frozen goldens not bundled yet (RANGE permission pending)",
)

def _run_pipeline(df):
    # OFFLINE only: load -> derive -> recipe/stats. No spaCy/docling.
    # (Wire via scripts.load_table / scripts.derive / scripts.recipe / scripts.stats,
    #  using the same adapter helpers as tests/golden/_adapters.py. Filled when the
    #  slice lands; the structure is what matters now.)
    raise NotImplementedError

def test_public_slice_reproduces_frozen_goldens():
    import pandas as pd
    df = pd.read_csv(_SLICE, dtype=str, keep_default_na=False, na_values=[""])
    got = _run_pipeline(df)
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert got == expected
```

Note for the subagent: leave `_run_pipeline` as a documented stub (the test skips
today, so it never runs). The MAIN thread implements `_run_pipeline` + freezes
`expected_public.json` when the slice is bundled. Verify the file IMPORTS and the
module COLLECTS as skipped:
`& .venv\Scripts\python.exe -m pytest tests/golden/public/ -q` -> "1 skipped".

**Commit:** `git add tests/golden/__init__.py tests/golden/public/`
`git commit -m "test(phase11): skip-if-absent public-corpus golden scaffold"`

---

## Task B -- 11.2 private-corpus goldens  [SUBAGENT 2, then MAIN]

### B1: tests/golden/_adapters.py + tests/golden/test_adapters.py  [SUBAGENT 2]

**Files:** Create `tests/golden/_adapters.py`, `tests/golden/test_adapters.py`.

**Step 1 -- failing test** (`tests/golden/test_adapters.py`):

```python
from tests.golden._adapters import extract_state, reason_category

def test_extract_state_last_token_and_sc_special_case():
    assert extract_state("Houston TX Police Department") == "TX"
    assert extract_state("Greenville County SC Sheriff") == "SC"
    assert extract_state("South Carolina Law Enforcement Div") == "SC"  # spelled-out
    assert extract_state("Springfield Police") is None                  # no token
    assert extract_state(None) is None
    # last token wins when several appear
    assert extract_state("Kansas City MO PD KS") == "KS"

def test_reason_category_splits_on_dash():
    assert reason_category("Immigration (criminal) - assist ICE") == "Immigration (criminal)"
    assert reason_category("Traffic Infraction") == "Traffic Infraction"
    assert reason_category("") == ""
    assert reason_category(None) == ""
```

**Step 2 -- run, expect fail.**

**Step 3 -- implement** (`tests/golden/_adapters.py`), complete code:

```python
"""Flock-format pipeline-configuration adapters for the Simpsonville golden tests.

These are NOT part of magpie's generic engine. They are the jurisdiction-specific
configuration a real Flock-audit run supplies (the same mapping the pilot's clean()
did), kept here so derive_geo stays generic (design doc, Decision 2). Pure stdlib.
"""
from __future__ import annotations

import re

_US_STATES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
})


def extract_state(org):
    """Last US-state two-letter token in a free-form Org Name, else None.

    'Houston TX Police Department' -> 'TX'. A spelled-out 'South Carolina ...' with no
    two-letter token is special-cased to 'SC' (the pilot's home state). Mirrors the
    pilot build_cache.clean() exactly.
    """
    if not isinstance(org, str):
        return None
    toks = [t.upper() for t in re.findall(r"[A-Za-z]{2,}", org) if t.upper() in _US_STATES]
    if toks:
        return toks[-1]
    if re.search(r"south carolina", org, re.IGNORECASE):
        return "SC"
    return None


def reason_category(reason):
    """The Flock standardized category: the text before ' - ' (stripped); '' if blank."""
    if not isinstance(reason, str):
        return ""
    return reason.split(" - ")[0].strip()
```

**Step 4 -- run, expect pass.** **Step 5 -- commit:**
`git add tests/golden/_adapters.py tests/golden/test_adapters.py`
`git commit -m "test(phase11): Flock-format golden-test adapters (extract_state, reason_category)"`

### B2: tests/golden/test_simpsonville.py SKELETON  [SUBAGENT 2]

**Files:** Create `tests/golden/test_simpsonville.py`.

The subagent writes the STRUCTURE: env-gating, the load + adapter + derive wiring,
and each assertion with a CLEARLY-MARKED placeholder constant (`None`) plus a
`# MAIN-THREAD-PIN` comment. The subagent CANNOT run this (no corpus); it only needs
to import-clean and collect-as-skipped when the env var is unset.

Inline APIs the subagent needs (do NOT open the source files):
- `scripts.derive.derive_columns(df, config)` -> copy of df + derived cols. Configs:
  `{"immigration": {"source_col": "Reason", "keywords": ["immigration"]},
    "nets": {"source_col": "Total Networks Searched"},
    "has_case": {"source_col": "Case #", "redaction_sentinels": ["***"]},
    "geo": {"source_col": "state", "home_value": "SC", "in_label": "SC",
            "out_label": "OOS", "unknown_label": "UNK"}}`
  (build `df["state"] = df["Org Name"].map(extract_state)` and
   `df["reason_cat"] = df["Reason"].map(reason_category)` BEFORE derive_columns.)
- `scripts.data_quality.check_truncation(rows_or_df)` -> dict with a `truncated`
  bool + the ceiling 1048575.
- `scripts.stats.gini(seq)`, `scripts.stats.category_pct(series)` -> {label: pct},
  `scripts.stats.median_by_category(df, value_col, category_col)` -> {cat: median}.
- `scripts.recipe.check_pretext(df, {"cat_col": "reason_cat", "pretext_cats": [...]})`
  -> dict with a `pretext` int.
- `scripts.pii_sweep.sweep(series, official_names=..., collect_local_texts=False)`
  (spaCy path -> mark that one test `@pytest.mark.spacy`).

Skeleton:

```python
import os
from pathlib import Path

import pytest

_CORPUS = os.environ.get("MAGPIE_SIMPSONVILLE_CORPUS")

pytestmark = pytest.mark.skipif(
    not _CORPUS, reason="MAGPIE_SIMPSONVILLE_CORPUS not set (private corpus, env-gated)"
)

# --- MAIN-THREAD-PIN: replace each None with the exact value computed on the real
# --- corpus, with a comment tying it to the documented pilot figure. NO '~' here.
EXPECTED_ROWS = 1_048_575                      # documented truncation ceiling 2^20-1
EXPECTED_OOS_COUNT = None                      # rounds to documented 89.7%
EXPECTED_IMMIGRATION = None                    # documented ~770 (criminal OR civil)
EXPECTED_PRETEXT_MIN = 175                     # documented floor >= 175
EXPECTED_GINI_3DP = 0.805                      # documented Gini
EXPECTED_TRAFFIC_MEDIAN = None                 # documented ~2943
EXPECTED_HOMICIDE_MEDIAN = None                # documented ~1792
EXPECTED_PII_AGENCIES = 747                    # documented distinct agencies (exact)
EXPECTED_PII_LOW = None                        # tolerance band low  (documented ~11900)
EXPECTED_PII_HIGH = None                       # tolerance band high
PRETEXT_CATS = []                              # MAIN-THREAD-PIN the Flock pretext set


def _network_frame():
    import pandas as pd
    from tests.golden._adapters import extract_state, reason_category
    # the network audit CSV in the corpus folder (the ~1M-row file)
    path = next(Path(_CORPUS).glob("*Network-Audit.csv"))
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    df.columns = [c.strip() for c in df.columns]
    df["state"] = df["Org Name"].map(extract_state)
    df["reason_cat"] = df["Reason"].map(reason_category)
    return df


def test_truncation_at_ceiling():
    from scripts.data_quality import check_truncation
    df = _network_frame()
    assert len(df) == EXPECTED_ROWS
    assert check_truncation(df)["truncated"] is True


def test_out_of_state_share():
    from scripts.derive import derive_columns
    from scripts.stats import category_pct
    df = derive_columns(_network_frame(), {
        "geo": {"source_col": "state", "home_value": "SC", "in_label": "SC",
                "out_label": "OOS", "unknown_label": "UNK"}})
    oos = int((df["geo"] == "OOS").sum())
    assert oos == EXPECTED_OOS_COUNT
    assert round(category_pct(df["geo"])["OOS"] * 100, 1) == 89.7

# ... (immigration, pretext, gini, blast_radius -- same shape, pin each constant)

@pytest.mark.spacy
def test_pii_exposure_and_agency_count():
    # distinct-then-weight pii_sweep over the reason text; agency count EXACT, the
    # exposure tally within an explicit band (spaCy model-version tolerance).
    ...

@pytest.mark.docling
def test_citation_anchor_revalidation_issue_6():
    # reuse MAGPIE_PHASE8_REAL_PDF (skip if unset); assert anchors resolve clean
    # (exact/relocated, no false exact) on the real Greenville RFP (Phase-8 Tier-2b).
    pdf = os.environ.get("MAGPIE_PHASE8_REAL_PDF")
    if not pdf:
        pytest.skip("MAGPIE_PHASE8_REAL_PDF not set")
    ...
```

**Verify (subagent):** import-clean + collects as skipped with the env var unset:
`& .venv\Scripts\python.exe -m pytest tests/golden/test_simpsonville.py -q` -> all skipped.
**Commit:** `git add tests/golden/test_simpsonville.py`
`git commit -m "test(phase11): env-gated Simpsonville golden skeleton (values pinned main-thread)"`

### B-MAIN: pin the exact values  [MAIN]

After SUBAGENT 2: in the MAIN thread, set `MAGPIE_SIMPSONVILLE_CORPUS` to the private
folder, run the goldens, read the computed values, replace each `None`/placeholder
with the exact value (annotated), choose the PII tolerance band + PRETEXT_CATS,
confirm green, commit. Never dispatch this; never let a subagent read the corpus.

---

## Task C -- 11.3 structural + install smoke  [SUBAGENT 3]

### C1: tests/test_plugin_loads.py (consolidated load smoke)

Reads the repo's OWN manifest + skills at RUNTIME (open()/json/yaml -- NOT the Read
tool, so non-ASCII SKILL.md is fine at runtime). House-style mirrors the existing
skill-smoke tests (PyYAML; iterate `skills/*/SKILL.md`).

```python
import json
from pathlib import Path

import pytest
import yaml  # PyYAML, already a dev dep

_REPO = Path(__file__).resolve().parents[1]

def test_plugin_manifest_loads():
    manifest = json.loads((_REPO / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    assert manifest["name"] == "magpie"
    assert "librarian" in [d if isinstance(d, str) else d.get("name")
                           for d in manifest["dependencies"]]

def test_every_skill_frontmatter_parses():
    skills = list((_REPO / "skills").glob("*/SKILL.md"))
    assert skills, "no skills found"
    for sk in skills:
        text = sk.read_text("utf-8")
        assert text.startswith("---")
        fm = yaml.safe_load(text.split("---", 2)[1])
        assert fm.get("name") and fm.get("description")

def test_mcp_config_parses_and_interpolates():
    cfg = json.loads((_REPO / ".mcp.json").read_text("utf-8"))
    blob = json.dumps(cfg)
    assert "magpie-dataset" in cfg["mcpServers"]
    # no bare ${VAR} without a :- default would crash CC config parsing; ours use
    # ${CLAUDE_PROJECT_DIR}/${CLAUDE_PLUGIN_ROOT} which CC substitutes directly.
    assert "mcp-sqlite" in blob
```

Run offline -> 3 passed. Commit
`git commit -m "test(phase11): consolidated plugin-load/install smoke"`.

### C2: tests/test_mcp_sqlite_smoke.py (served-DB start smoke) + pyproject marker

The one test that proves the served-DB path end-to-end. Needs uvx; SKIP if absent so
it is safe locally + runs in CI (uv installed). Build a tiny DB with
`scripts.build_dataset_db.build_dataset_db`, launch `uvx mcp-sqlite==0.3.2 <db>
--metadata <yml> --prefix ds_`, confirm the process serves (probe stdin/stdout per
the mcp-sqlite stdio protocol OR, simpler + robust, assert the process starts and
stays up ~2s then terminate it cleanly). Mark `@pytest.mark.mcp` and add `mcp` to
pyproject.toml `[tool.pytest.ini_options] markers` (inline the exact addition):

```toml
# add to the markers list in pyproject.toml:
"mcp: served-DB smoke that launches uvx mcp-sqlite (skipped if uvx absent)",
```

Skip guard: `pytest.mark.skipif(shutil.which("uvx") is None, reason="uvx not on PATH")`.
Keep the body minimal + deterministic; do NOT hit the network beyond uvx's own fetch.
Run offline (skips if no uvx) + commit
`git commit -m "test(phase11): mcp-sqlite served-DB start smoke (uvx-gated)"`.

Note: this marker is NOT in the offline `-m "not docling and not spacy and not xray
and not tsa"` exclusion, so add it there in CI too OR rely on the skipif. Decision:
rely on the skipif (so the offline command needs no change); document it.

---

## Task D -- 11.3 CI + release docs  [SUBAGENT 4]

### D1: .github/workflows/ci.yml

Create `.github/workflows/ci.yml` (ASCII YAML), complete:

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:
    inputs:
      heavy:
        description: "Run the heavy (docling/spacy/xray) suite"
        type: boolean
        default: false
jobs:
  offline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install uv (for the mcp-sqlite smoke)
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Create venv + install offline deps
        run: |
          python -m venv .venv
          . .venv/bin/activate
          python -m pip install -U pip
          python -m pip install -r requirements-dev.txt
      - name: detect_tier pre-flight
        run: . .venv/bin/activate && python scripts/detect_tier.py --json > tier.json && cat tier.json
      - name: Offline suite + smoke
        run: |
          . .venv/bin/activate
          python -m pytest -m "not docling and not spacy and not xray and not tsa" -q
  heavy:
    if: ${{ github.event_name == 'workflow_dispatch' && inputs.heavy }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - name: Create venv + install ALL deps (~2 GB models on first run)
        run: |
          python -m venv .venv
          . .venv/bin/activate
          python -m pip install -U pip
          python -m pip install -r requirements-dev.txt
          python -m spacy download en_core_web_lg || true
      - name: Heavy suite (docling/spacy/xray)
        run: |
          . .venv/bin/activate
          python -m pytest -m "docling or spacy or xray" -q
```

Note for the subagent: requirements-dev.txt already pins the heavy stack; the offline
job installs it too (the heavy WHEELS install fast; only the heavy MODELS/markers are
deferred). If install time is a problem, the plan's fallback is a trimmed offline
requirements file -- but default to the single requirements-dev.txt for simplicity.
Cache wheels with actions/cache if runtime is high (optional, note only).

Commit `git commit -m "ci(phase11): offline default + workflow_dispatch heavy GitHub Actions"`.

### D2: release notes + pre-tag checklist

Create `docs/RELEASE-NOTES-0.1.0.md` and `docs/RELEASE-CHECKLIST.md` (ASCII).
RELEASE-NOTES: what v0.1.0 includes (the 8 skills + the engine surface), what is
deferred (Track B / Layer 2 / Docker), and the public-corpus status (Skokie if
vetted; RANGE slice fast-follow). RELEASE-CHECKLIST: the required-green pre-tag gate
from the design (offline CI; heavy job or local heavy run; tsa local; artifact
sha256s match the datasheet; MANIFEST regenerated; Librarian acceptance test green).
Commit `git commit -m "docs(phase11): v0.1.0 release notes + pre-tag checklist"`.

### D3: plugin.json version bump

Edit `.claude-plugin/plugin.json` `"version": "0.0.1"` -> `"0.1.0"`. (plugin.json is
ASCII; safe for a subagent.) The existing test_plugin_manifest.py / test_plugin_loads.py
must still pass. Commit `git commit -m "chore(phase11): bump magpie to v0.1.0"`.

---

## Main-thread closeout  [MAIN]

- **M1 (after B):** pin the 11.2 exact values against the real corpus; confirm green.
- **M2:** acquire the RANGE full CSV locally (gitignored); run build_public_slice.py;
  run pii_sweep over the slice reason column; redact (redact_output) or drop if not
  clean; freeze the slice + generate `tests/golden/public/expected_public.json` +
  implement `_run_pipeline`; fill the datasheet (sha256, dates, permission). This
  lands when RANGE permission is recorded (else fast-follow / v0.1.1).
- **M3:** vet + bundle the Skokie PDF (ingest -> redaction_check + pii_sweep) and add
  its docling-marked OCR+anchor golden.
- **M4:** draft the RANGE permission email (prose-craft) for Tim to send.
- **M5:** regenerate MANIFEST.md (non-ASCII -> main thread).
- **M6:** Codex impl-review gate -> fix subagents per cluster -> confirmatory pass.
- **M7:** PR (merge commit) -> after merge, run the pre-tag checklist -> tag v0.1.0.

---

## Verification gates

- Offline suite green after every task:
  `& .venv\Scripts\python.exe -m pytest -m "not docling and not spacy and not xray and not tsa" -q`
  (494 + new offline tests passed; private/public goldens SKIPPED).
- Codex plan-review gate BEFORE SDD; Codex impl-review gate AFTER SDD.

## Execution Handoff

Plan complete. Execution: **Subagent-Driven (this session)** via
superpowers:subagent-driven-development -- a fresh implementer subagent per task
(SUBAGENT 1..4, sequential on the shared branch), main-thread review + the offline
suite between tasks, then the main-thread closeout. The Codex plan-review gate runs
first.

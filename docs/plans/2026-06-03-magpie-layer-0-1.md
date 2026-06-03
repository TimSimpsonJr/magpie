# Magpie Layer 0–1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship the laptop-local flagship of Fieldwork: Magpie — a Claude Code plugin that runs the DeflockSC Flock-style structured-data analysis (Track A) end-to-end, with the shared spine and Librarian output, validated against the real Simpsonville corpus.

**Architecture:** Three repos — `magpie` (the toolkit plugin), `librarian` (shared notes skill extracted from research-workflow), and `fieldwork` (pure marketplace pointer, repurposed from `fieldwork-plugins`). Layer 1 is pure local Python (DuckDB/pandas, spaCy, Docling, x-ray) + SQLite/Datasette via `mcp-sqlite` — **no heavy Docker**. The flagship engine is a deterministic stats module + a parameterized per-source analysis recipe that rolls up across sources.

**Tech Stack:** Python 3.12 (`C:\Users\tim\AppData\Local\Programs\Python\Python312\python.exe`), pytest, pandas + DuckDB + pyarrow, spaCy `en_core_web_lg`, Docling + RapidOCR + OCRmyPDF, Free Law `x-ray`, `mcp-sqlite`, Claude Code plugin format (`.claude-plugin/`).

**Source of truth:** `docs/plans/2026-06-03-magpie-design.md` (the design doc). Read §5 (components), §6.1 (Flock data flow), §7 (verification/safety), §9 (testing) before starting.

---

## How to read this plan

- **TDD throughout.** Each component: write the failing test → run it (confirm fail) → minimal implementation → run (confirm pass) → commit. Commits are frequent and small.
- **Research-gate-first for library code.** Tasks touching Docling, x-ray, spaCy, `mcp-sqlite`, RFC 3161, or the Claude Code `dependencies`/marketplace syntax **begin** with a Tier-2 gate: query Context7 + a focused web refresh, write `references/prior-art.md` in the skill folder, *then* write tests against the verified API. Do not write library calls from memory.
- **Phases are ordered by dependency.** Phase 0 (foundations) and Phase 2 (stats module — the deterministic flagship) are fully specified with code. Library-dependent phases give tests + interface + approach.
- **Python invocation:** use the full path above; `python` is not on PATH in bash sessions.
- **Owned repo:** `magpie`, `librarian`, `fieldwork` are TimSimpsonJr-owned — create the `autonomous-safe` / `design-input-needed` labels on each new repo (per CLAUDE.md) and generate `MANIFEST.md` once real structure exists (end of Phase 2+).
- **Plugin authoring:** use `plugin-dev` sub-skills (`plugin-dev:plugin-structure`, `plugin-dev:skill-development`, `plugin-dev:agent-development`) for every manifest/skill/agent — they document the field formats that prevent silent install failures.

---

## Phase 0 — Foundations: repos, manifests, Fieldwork pointer

### Task 0.1: Confirm toolchain
**Step 1:** Run `& "C:\Users\tim\AppData\Local\Programs\Python\Python312\python.exe" --version` → expect `Python 3.12.10`.
**Step 2:** Create and activate a project venv: `... -m venv .venv` in `magpie/`; add `.venv/` to `.gitignore`.
**Step 3:** `pip install pytest pandas duckdb pyarrow` (pin versions in `requirements-dev.txt`).
**Step 4:** Commit `requirements-dev.txt` + `.gitignore`.

### Task 0.2: Magpie repo skeleton
**Files — Create:**
- `magpie/.claude-plugin/plugin.json`
- `magpie/.gitignore`, `magpie/LICENSE` (MIT), `magpie/README.md`
- `magpie/skills/`, `magpie/agents/`, `magpie/scripts/`, `magpie/tests/`, `magpie/corpus/` (dirs with `.gitkeep`)

**`plugin.json`** (verify field set against `plugin-dev:plugin-structure` first):
```json
{
  "name": "magpie",
  "description": "FOSS-first investigative analysis toolkit: structured FOIA/data analysis (stats, PII sweep, repeatable recipes) plus document ingest, redaction-check, and verifiable findings. Fieldwork: Magpie.",
  "version": "0.0.1",
  "author": { "name": "Tim Simpson" },
  "license": "MIT",
  "keywords": ["foia", "osint", "investigation", "data-journalism", "redaction", "datasette"],
  "dependencies": [{ "name": "librarian", "version": ">=0.1.0" }]
}
```
**`.gitignore`** must include: `.venv/`, `__pycache__/`, `*.parquet`, `corpus/private/`, `.research-workflow/`, and an explicit `# NEVER commit the Simpsonville corpus (PII)` block excluding any real-data path.

**Step:** Write a smoke test `tests/test_plugin_manifest.py` asserting `plugin.json` parses and has required keys (`name`, `description`, `version`). Run → fail (no file) → create files → pass → commit.

### Task 0.3: Fieldwork pointer-marketplace (research-gated)
**Research gate:** Confirm current `marketplace.json` external-source syntax (`{"source":"github","repo":"owner/repo"}`) and the `dependencies` / `allowCrossMarketplaceDependenciesOn` / version-tag mechanics against https://code.claude.com/docs/en/plugin-marketplaces.md and plugin-dependencies.md. Write findings to `fieldwork/NOTES.md`.

**Files:**
- Modify repo `fieldwork-plugins` → rename direction TBD; **Create** `fieldwork/.claude-plugin/marketplace.json`
- Delete vendored `fieldwork-plugins/research-workflow/` and `fieldwork-plugins/obsidian-publisher/`

**`marketplace.json`** (pure pointer):
```json
{
  "name": "fieldwork",
  "owner": { "name": "Tim Simpson", "email": "tim@timsimpsonjr.com" },
  "metadata": { "description": "Fieldwork — investigative toolkit suite for Claude Code." },
  "plugins": [
    { "name": "magpie", "source": { "source": "github", "repo": "TimSimpsonJr/magpie" }, "description": "Fieldwork: Magpie — investigations analysis toolkit." },
    { "name": "research-workflow", "source": { "source": "github", "repo": "TimSimpsonJr/research-workflow" }, "description": "Fieldwork: Research — deep web-research pipeline." },
    { "name": "librarian", "source": { "source": "github", "repo": "TimSimpsonJr/librarian" }, "description": "Fieldwork: Librarian — structured notes for follow-up & browsing." },
    { "name": "prose-craft", "source": { "source": "github", "repo": "TimSimpsonJr/prose-craft" }, "description": "Fieldwork: Prose Craft — outward-facing prose + review gate." }
  ]
}
```
**Step:** Validate via `claude plugin validate` (or `/plugin marketplace add` against the local path). Confirm each member resolves from GitHub. Commit. **This supersedes the de-vendor task** — dismiss that chip.

### Task 0.4: Librarian repo skeleton
**Files — Create:** `librarian/.claude-plugin/plugin.json` (`name: librarian`, `version: 0.1.0`, MIT), `librarian/skills/`, `librarian/agents/`, `librarian/references/`, `librarian/tests/`, README.
**Step:** Manifest smoke test → fail → create → pass → commit.

---

## Phase 1 — Librarian: extract from research-workflow

> **Read first:** `research-workflow/skills/research/SKILL.md` Stage 6 (classify), Stage 7 (write), Stage 8 (wikilink); `agents/classify-agent.md`, `agents/wikilink-scanner.md`. The extraction is **decoupling, not rewriting** (design doc §5.7).

### Task 1.0: Research/extraction-map gate
Write `librarian/references/prior-art.md` mapping each research-workflow piece → its Librarian home, and listing the three couplings to sever: (1) vault-index optional, (2) neutral input contract, (3) taxonomy as config.

### Task 1.1: Neutral input contract + portable Markdown writer (TDD)
**Input contract:** `[{title, content, frontmatter_meta, citations, link_hints, priority, action}]` + optional `vault_context`.
**Test:** `librarian/tests/test_write_portable.py` — given one note spec with `frontmatter_meta` + `citations`, the writer emits a Markdown file with a YAML frontmatter block, a `## Sources` section listing citations, and the body; no vault, no wikilink lookups. Assert file content matches expected structure.
**Implement:** `librarian/scripts/write_note.py` (pure; no vault-index import). Commit per the TDD cycle.

### Task 1.2: Tabular/CSV output (TDD)
**Test:** given a tabular payload (list of dicts), writer emits a `.csv` *and* a Markdown-table mirror for inline vault display. Assert both.
**Implement** + commit.

### Task 1.3: Classify/structuring step, taxonomy as config (TDD)
Port `classify-agent.md` → `librarian/skills/librarian/SKILL.md` structuring rules; move the content-type tag list + folder conventions into `librarian/config/taxonomy.example.json`. **Test:** structuring a sample summary set returns `notes_to_create[]` with title/folder/tags/links/priority and respects a supplied taxonomy config. (Agent-driven; test via a fixture + a dispatched subagent, asserting JSON shape.)

### Task 1.4: Vault mode + wikilink-scanner (TDD, optional path)
Copy `wikilink-scanner.md` → `librarian/agents/`; make vault-index queries conditional on a configured vault. **Test:** with a temp "vault" fixture, placement + a wikilink edit plan are produced; with no vault, both are skipped and portable output is used.

### Task 1.5: `dependencies` wiring + migration stub
- `magpie/plugin.json` already declares `librarian` (Task 0.2).
- Add `librarian` to `research-workflow/.claude-plugin/plugin.json` `dependencies`.
- Add a thin shim in research-workflow Stage 7 that delegates to `librarian:write` **behind a feature flag** (full migration is a fast-follow; this task only proves the call path).

### Task 1.6: ACCEPTANCE TEST — standalone auto-pull (hard requirement)
**Step 1:** On a clean Claude Code profile, `/plugin marketplace add TimSimpsonJr/research-workflow` then install it. **Expected:** `librarian` auto-installs via `dependencies`.
**Step 2:** Run a minimal `/research` that reaches the write stage. **Expected:** Stages 6/7/8 work (via shim or native).
**Step 3:** If auto-pull fails, document the **co-install fallback** (`/plugin install librarian@…`) in research-workflow README and file an `autonomous-safe` issue. Record the result in `librarian/references/prior-art.md`.

---

## Phase 2 — Stats module (the deterministic flagship)

> Pure Python/pandas. Fully specified + TDD. This is the crown jewel — `stats.py` from the Simpsonville pilot, productized. Validate every function against the documented pilot values (design doc §9).

**Files:** Create `magpie/scripts/stats.py`, `magpie/tests/test_stats.py`.

### Task 2.1: Gini concentration (TDD)
**Step 1 — failing test:**
```python
import numpy as np
from scripts.stats import gini

def test_gini_uniform_is_zero():
    assert gini([5, 5, 5, 5]) == 0.0

def test_gini_total_inequality_near_one():
    vals = [0]*99 + [100]
    assert gini(vals) > 0.95

def test_gini_matches_pilot_floor():
    # Simpsonville network volume concentration was ~0.805; a known fixture must land in range
    counts = load_fixture_counts()  # tests/fixtures/agency_counts_sample.json
    assert 0.75 <= gini(counts) <= 0.85
```
**Step 3 — implementation:**
```python
def gini(values):
    """Gini coefficient of a list of non-negative magnitudes (e.g., searches per agency)."""
    x = np.sort(np.asarray([v for v in values if v is not None], dtype=float))
    n = x.size
    if n == 0 or x.sum() == 0:
        return 0.0
    cum = np.cumsum(x)
    # relative mean absolute difference form
    return float((2 * np.sum((np.arange(1, n + 1)) * x) - (n + 1) * cum[-1]) / (n * cum[-1]))
```
Run → pass → commit.

### Task 2.2: Top/bottom share (TDD)
`top_k_share(counts, k_frac=0.01)` and `bottom_half_share(counts)` returning the fraction of total volume held by the top-1% / bottom-50%. Test against the pilot ("top 1% = 27%, bottom 50% = 2.5%"). Implement (sort, cumulative). Commit.

### Task 2.3: Distribution-by-category — net-width-by-severity (TDD)
`median_by_category(df, value_col, category_col)` → returns a sorted Series of medians. Test: a fixture where Traffic median > Homicide median (guts the "needed for serious crime" defense). Implement (`df.groupby(category_col)[value_col].median().sort_values(ascending=False)`). Commit.

### Task 2.4: Automation signature (TDD)
`automation_signature(df, hour_col, agency_col, day_start=6, day_end=18)` → per-agency `{daytime_pct, overnight_pct}`; flags agencies with overnight_pct above a threshold (scheduled-job signature). Test against a fixture with a clear day/night skew. Implement. Commit.

### Task 2.5: Burstiness (TDD)
`burstiness(df, timestamp_col)` → max same-second count and the distribution of per-second batch sizes (detects ≥N/sec automated batches). Test: a fixture with a 10-in-one-second batch returns max==10. Implement. Commit.

### Task 2.6: Rate helpers (TDD)
`presence_rate(series, present_predicate)` (e.g., case-number rate where `***`≠blank), `category_pct(df, mask)` (e.g., out-of-state %). Test (incl. the `***`-vs-blank distinction). Implement. Commit. **Generate `magpie/MANIFEST.md`** now that real structure exists.

---

## Phase 3 — dataset-analyze (research-gated)

### Task 3.0: Research gate
Verify current APIs: `duckdb`, `sqlite-utils`, `pandas.read_excel`/`openpyxl` merged-cell handling, and **`mcp-sqlite`** (pin a version; confirm read-only `?mode=ro`, canned-queries-as-tools, row-cap). Anthropic's `mcp-server-sqlite` is archived (SQLi CVE) — do **not** use. Write `magpie/skills/dataset-analyze/references/prior-art.md`.

### Task 3.1: Loader with dirty-data heuristics (TDD)
`scripts/load_table.py`: chardet pre-flight → explicit encoding; TEXT-whitelist for ZIP/FIPS/IDs/case-numbers; `--empty-null`; openpyxl unmerge for XLSX; Parquet cache. Tests against dirty-CSV fixtures (latin-1 bytes, zero-padded IDs, merged cells, junk header rows).

### Task 3.2: Truncation + data-quality gate (TDD)
`scripts/data_quality.py`: detect row count == `2**20 - 1` (1,048,575) → flag truncation; compare date-span to requested window; `analyze-tables`-style anomaly warnings (>90% null, type-coercion failures). Test: a 1,048,575-row fixture trips the truncation flag with a message recommending `request-the-gap`.

### Task 3.3: Derived-column recipe (TDD)
`scripts/derive.py`: build the Flock-style derived columns (`geo`, `reason_cat/text`, `is_immigration`, `nets`, `has_case`, `base_type`, `date_et/hour_et/dow_et`) as a config-driven transform. **Column name `geo` not `loc`** (pandas `.loc` collision — from the handoff). Tests per derived column.

### Task 3.4: `dataset-analyze` SKILL.md + mcp-sqlite wiring
Author the skill (via `plugin-dev:skill-development`): load → cache → derive → expose via `mcp-sqlite` (pinned, read-only, row-cap). Bundle `mcp-sqlite` config in `magpie/.mcp.json` using `${CLAUDE_PLUGIN_ROOT}`.

---

## Phase 4 — analysis-recipe + cross-source rollup

### Task 4.1: Recipe runner (TDD)
`scripts/recipe.py`: a parameterized pass that runs the **13-point checklist** (truncation → out-of-state → immigration → pretext → PII → accountability → co-travel → blast-radius → mega-users → operations → AI/moderation → cross-agency overlap → statistical patterns) over one source, emitting a standard findings object. Each check is a small pure function with its own test. Pretext/immigration/co-travel categorization includes the **keyword-verification guardrail** (the `ICE`/"polICE" trap) — test that the verifier rejects substring false-positives.

### Task 4.2: Cross-source rollup (TDD)
`scripts/rollup.py`: tally recurrence of mega-users + patterns across multiple sources' findings objects (the recurrence thesis). Test with two source fixtures sharing an out-of-state agency.

### Task 4.3: `analysis-recipe` SKILL.md + Workflow fan-out
Author the skill; per-source fan-out via the Workflow tool, synthesize via rollup. Output through Librarian (hub-and-spoke). Document that statistical patterns are a first-class per-log step, not an afterthought.

---

## Phase 5 — pii-sweep (research-gated)

### Task 5.0: Research gate
Verify spaCy `en_core_web_lg` install + API; structured-PII regex patterns (A-numbers, DOB, names). Write `references/prior-art.md`.

### Task 5.1: NER sweep over distinct texts (TDD)
`scripts/pii_sweep.py`: run NER over **distinct** free-text values then weight by counts (the pilot's ~7× efficiency lesson). Test: a fixture with a known PERSON entity is found; weighting reproduces the documented multiplier shape. Emit structured exposure tallies feeding `redact-output`.

### Task 5.2: `pii-sweep` SKILL.md
Author the skill; wire output to `redact-output`.

---

## Phase 6 — ingest (research-gated)

### Task 6.0: Research gate
Verify Docling current API (`DoclingDocument`, `prov`/`bbox`), RapidOCR backend default, OCRmyPDF preprocessing flags, `docling-parse` char-level boxes. Write `references/prior-art.md`. Confirm CPU performance is acceptable on a sample scan.

### Task 6.1: Text-layer quality gate (TDD)
`scripts/ingest_gate.py`: chars-per-page + dictionary-hit-rate heuristic → decide native-text vs re-OCR. Tests against a clean-digital fixture (skip OCR) and a garbled-text-layer fixture (force re-OCR).

### Task 6.2: Docling wrapper preserving provenance (TDD)
`scripts/ingest.py`: PDF → `DoclingDocument` JSON (keep page+bbox; **never** Markdown internally); OCRmyPDF deskew/`--redo-ocr` preprocessing; Bates-number regex post-pass. Test: extracted elements carry `page_no` + `bbox`; Bates numbers captured separately. Flag handwriting/degraded pages for review.

### Task 6.3: `ingest` SKILL.md.

---

## Phase 7 — redaction-check + redact-output (research-gated)

### Task 7.0: Research gate
Verify Free Law `x-ray` Python API + its documented limits; PyMuPDF text-layer extraction; ExifTool/XMP metadata; `%%EOF` incremental-save detection. Write `references/prior-art.md`.

### Task 7.1: Layered redaction checks (TDD)
`scripts/redaction_check.py`: x-ray (box-over-live-text) **+** text-layer sweep + metadata scan + incremental-save (`%%EOF`>1) + unapplied `/Redact` + embedded-file enum. Dual `--mode` (`received` / `pre-publish`). Tests per check against crafted PDF fixtures. **Document what it cannot catch** (glyph-position, pixelation, cross-version) — these are flagged for humans, not solved.

### Task 7.2: redact-output (TDD)
`scripts/redact_output.py`: names→initials in published artifacts; full exhibits routed to a local non-vault path only. Test: a findings note with PII is redacted; the exhibit path is outside any vault.

### Task 7.3: SKILL.md for both.

---

## Phase 8 — investigate (verification gate)

### Task 8.1: Citation contract + anchor fallback chain (TDD)
`scripts/citation.py`: build the finding record (design doc §7 schema); anchor resolves via char-offset → text-hash (SHA-256 of span) → block-index → page. **Prototype + validate on the real corpus early** (this format is a magpie invention). Tests: anchor round-trips across a simulated OCR re-run (offset shifts, hash still resolves).

### Task 8.2: Verifier + citation-checker agents
Author `agents/extraction-verifier.md` (structurally separate prompt; re-reads span; presence + entailment; `indeterminate` default) and `agents/citation-checker.md`. Via `plugin-dev:agent-development`.

### Task 8.3: `investigate` SKILL.md
Orchestrate extract → verify → **solo human gate** (evidence-before-claim card; two-reviewer optional, never required). Encode rigor guardrails (walk-back unsupported; verify keyword matches; `***`≠blank; refuse out-of-scope claims like racial-disparity-from-logs; window-asked≠retention-proven).

---

## Phase 9 — archive-evidence (minimal)

### Task 9.1: Hash + RFC 3161 + manifest (TDD)
`scripts/evidence.py`: SHA-256 on receipt (before any processing); RFC 3161 timestamp via a free TSA (OpenSSL); append-only custody log; provenance manifest JSON written via Librarian. Tests: manifest schema; mtime-before-hash warning. Defer Auto Archiver/WACZ web capture and C2PA.

### Task 9.2: `archive-evidence` SKILL.md.

---

## Phase 10 — setup / doctor wizard

### Task 10.1: Tier detection + health checks (TDD)
`scripts/detect_tier.py` (modeled on research-workflow's): probe local tools (Python deps, spaCy model, x-ray) and any optional services; build a capability map; name tiers plainly. Tests with mocked presence/absence.

### Task 10.2: `setup` (operator) + `doctor` (journalist) skills
`magpie setup` (one-time: venv/deps, model download, sample corpus, `.mcp.json`) vs `magpie doctor` (health-check only; never runs setup). Separate `docs/OPERATOR_GUIDE.md` and `docs/JOURNALIST_START.md` (the journalist guide never mentions Docker). Dual-onramp README.

---

## Phase 11 — Validation & release

### Task 11.1: Clean public sample corpus
Curate a small, **redistributable, PII-free** government release (scanned PDF + a CSV) into `magpie/corpus/public/` with expected-output fixtures. This is the "try it now" path + CI golden source.

### Task 11.2: Golden tests vs the real Simpsonville corpus (private)
`tests/golden/test_simpsonville.py` (gated on a local env var pointing at the private corpus, **never committed**): assert truncation detection (row count `1,048,575`), out-of-state ≈ 89.7%, immigration ≈ 770, pretext ≥ 175, Gini ≈ 0.805, Traffic-median-net > Homicide-median-net, PII tally ≈ 11,900/747 agencies. These pin the engine to documented pilot reality.

### Task 11.3: Structural smoke + CI
CI runs the public-sample golden tests + structural smoke (skills load, recipe runs end-to-end, `mcp-sqlite` starts). Tag `magpie` v0.1.0; regenerate `MANIFEST.md`; ensure the Librarian acceptance test (Task 1.6) is green.

---

## Risks carried from design (re-verify during execution)
- `mcp-sqlite` maturity → pin, read-only, row-cap; thin custom wrapper if unreliable.
- Citation-anchor format is invented → prototype + validate on real corpus in Phase 8.1 before depending on it.
- Cross-repo `dependencies` auto-pull → Phase 1.6 acceptance test is the gate; co-install fallback documented.
- Docling RapidOCR CPU latency → confirm acceptable in Phase 6.0 on a real sample.
- Librarian extraction must not break standalone research-workflow → Phase 1.6.

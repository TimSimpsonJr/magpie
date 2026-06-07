# Magpie -- Structural Map

Fieldwork suite **Magpie**: a FOSS-first FOIA / investigative-analysis Claude Code plugin.
State: **Layer 0-1 complete + tagged v0.1.0** (Track A analysis core, document ingest,
redaction, citation, evidence, onboarding). **Track B (entity-network) underway** --
Phase 12 `entity-extract` shipped; Phase 13 `entity-graph` (resolution + Neo4j + yente)
next. This file is a one-line-per-file INDEX; depth lives in the design docs, docstrings,
and tests it points to (see the last Key Relationship).

## Stack

- **Language / shape:** Python 3.12.10 (mise-pinned; the project `.venv` is the source of truth). Claude Code plugin (`.claude-plugin/`); hard-deps the `librarian` plugin. `scripts/` are invoked directly by skills; `pyproject.toml` configures pytest only (not a pip package).
- **Layer 0-1 deps (laptop-local, no Docker):** pandas/numpy/duckdb/pyarrow + openpyxl/charset-normalizer/sqlite-utils/PyYAML; heavy CPU-only ML *edges* -- spaCy + en_core_web_lg (pii-sweep), docling+rapidocr+onnxruntime+torch+ocrmypdf (ingest), x-ray -> PyMuPDF (redaction-check), rfc3161-client (archive-evidence). numpy/pandas/torch pins held across every phase.
- **Track B (Layer 2) deps:** gliner + glirel + loguru, with `transformers==4.57.6` + `typer==0.24.2` PINNED (the GLiNER<->docling coexistence). The FtM stack (followthemoney + nomenklatura) is **Linux/CI-only** in `requirements-ftm.txt` -- PyICU has no Windows wheel.
- **Requirements split:** `requirements-dev.txt` (full) / `requirements-offline.txt` (trimmed CI subset) / `requirements-ftm.txt` (Linux-CI-only FtM).

## Structure

```
.claude-plugin/plugin.json   Plugin manifest (name "magpie", v0.1.0, MIT; dependencies: ["librarian"]).
.mcp.json                    Declares the magpie-dataset server (uvx mcp-sqlite, read-only, ds_ prefix).
scripts/                     Engine modules invoked by skills. House pattern: stdlib pure core + lazy heavy edge.
  stats.py                   Deterministic stats flagship (gini, concentration, automation signature, burstiness).
  load_table.py              Dirty CSV/XLSX loader: encoding pre-flight, TEXT-whitelist, empty_null, merged-cell fill.
  data_quality.py            Truncation (2^20-1 cap) + date-window + per-column anomaly gate (leads, not verdicts).
  derive.py                  Config-driven derived columns (geo/reason_cat/is_immigration/nets/temporal); \b keyword guardrail.
  build_dataset_db.py        Served read-only SQLite builder (fail-closed include_columns allowlist; FTS5).
  recipe.py                  13-point per-source analysis pass (run_recipe/CHECKS); composes stats+data_quality+derive.
  rollup.py                  Cross-source recurrence synthesis (external-only, pooled denominators, theses).
  pii_sweep.py               spaCy PERSON NER + structured-PII tally over distinct values, weighted by row counts.
  ingest_gate.py             Pure text-layer quality gate (diagnose_page -> decide_doc); injectable wordlist.
  ingest.py                  Docling edge: gate-before-OCR, DoclingDocument JSON kept internal, Bates pass, trust seam.
  redaction_check.py         8 leads-not-verdicts redaction checks (x-ray/pikepdf/pdfminer); never-publish-raw.
  redact_output.py           Redact uninvolved PII to initials for publish; officials/involved kept; vault-guarded exhibit.
  citation.py                Pure citation-anchor engine (build_anchor/resolve_anchor fallback chain); own-.text offsets.
  evidence.py                Provenance/custody: sha256-on-receipt + RFC 3161 timestamp + hash-chained custody log.
  detect_tier.py             setup/doctor capability probe (stdlib metadata/which/subprocess) + a --json CLI.
  entity_taxonomy.py         Track B: entity/relation taxonomy config (GENERIC + FLOCK presets).
  entity_extract.py          Track B pure core: windowing, span dedup, FtM-shaped nodes/edges, ReviewQueue, build_intermediate.
  entity_models.py           Track B lazy GLiNER/GLiREL edge (the only torch/gliner/glirel/spaCy importer).
  entity_ftmize.py           Track B FtM layer (Linux/CI only; only followthemoney importer): intermediate -> FtM bundle.
agents/
  extraction-verifier.md     Semantic advisory re-check of a cited span (presence + entailment; indeterminate default).
  citation-checker.md        Mechanical anchor-integrity check (drives citation.resolve_anchor / is_clean_citation).
skills/                      One SKILL.md per skill; each carries references/prior-art.md = that phase's verified-facts gate.
  dataset-analyze/           Track A: load -> quality-gate -> derive -> served DB -> mcp-sqlite query -> stats (+ canned_queries.yml).
  analysis-recipe/           Track A: per-source 13-point recipe + cross-source rollup (Workflow fan-out).
  pii-sweep/                 Track A: authoritative PII-exposure tally; feeds redact-output.
  ingest/                    Spine: document/PDF ingest (engine = ingest_gate + ingest; + bundled common_words wordlist).
  redaction-check/           Spine: find bad redactions (input side).
  redact-output/             Spine: redact uninvolved PII on output.
  investigate/               Spine: verification gate / citation discipline (engine citation.py + the two agents).
  archive-evidence/          Spine: evidence provenance + chain-of-custody (engine evidence.py; + bundled freeTSA root cert).
  setup/  doctor/            Onboarding: setup (operator, MAY install) vs doctor (journalist, READ-ONLY); engine detect_tier.
  entity-extract/            Track B: GLiNER+GLiREL -> reviewed FtM-shaped intermediate after a mandatory human gate.
tests/                       TDD suite; test_<module>.py mirrors each script + test_<skill>_skill.py smokes (675 offline + 1 guard).
                             Marker-gated, excluded from the offline default: spacy / docling / xray / tsa / gliner / ftm.
  golden/                    Env-gated real-corpus goldens (test_simpsonville.py) + Flock _adapters.py; skip-if-absent public slice.
  fixtures/                  Synthetic fixtures ONLY (no real corpus): agency_counts_sample.json, sample CSV/XLSX, reviewed_intermediate_sample.
  conftest*.py               Shared synthetic builders (PDF / DoclingDocument / redaction / entity fakes).
  test_manifest_budget.py    The recurrence guard for THIS file (line / word / per-line-word budgets).
docs/
  OPERATOR_GUIDE / JOURNALIST_START   Phase 10 dual onramp (operator setup vs journalist daily use; no Docker).
  RELEASE-NOTES-0.1.0 / RELEASE-CHECKLIST   v0.1.0 release notes + the pre-tag green gate.
  plans/                     Source-of-truth design (2026-06-03-magpie-design.md) + per-phase design(WHY)+plan(HOW) pairs.
  handoffs/                  Session-boundary handoff docs (latest: phase12-shipped-phase13-entity-graph).
tools/
  build_public_slice.py      Deterministic neutral public-CSV slice builder (for the forthcoming corpus/public sample).
  codex-review.ps1           Dev helper: UTF-8-pinned Codex cross-model review (PS 5.1 cp1252 workaround).
corpus/public/               Reserved for the redistributable public sample (DATASHEET.md template; the corpus is a fast-follow).
.github/workflows/ci.yml     CI: offline job (default) + workflow_dispatch heavy job + a continuous ftm job (Linux FtM contract).
requirements-{dev,offline,ftm}.txt   Full dev / trimmed offline-CI subset / Linux-CI-only FtM stack.
mise.toml / pyproject.toml   mise dev env (Python 3.12.10 + .venv bind; test/bootstrap tasks) / pytest config + markers.
.gitignore / .gitattributes  PII-corpus hard block + scratch ignores / binary-mark certs+tokens against line-ending corruption.
```

## Key Relationships

- **Pure-core / engine-at-edge split (suite-wide).** Every heavy module keeps a stdlib pure core plus a lazy model/IO edge (pii_sweep, ingest, citation, evidence, detect_tier, entity_*), so importing stays cheap and the core golden-tests with injected fakes. The edge is the sole importer of its heavy dependency.
- **FtM boundary is Linux/CI-only.** `entity_ftmize` is the only followthemoney importer; PyICU has no Windows wheel, so it and the `ftm`-marked tests SKIP on Windows. **The CI `ftm` job is the only verification surface for that code -- gate merges on it, never on Windows-green + Codex-green alone** (it caught two real bugs in Phase 12).
- **Track B data contract (Phase 12 -> 13).** `entity_extract.build_intermediate` emits a reviewed, followthemoney-FREE intermediate (per-document node scope, NO cross-doc merge); `entity_ftmize.write_bundle` realizes the three-file FtM bundle; `assert_phase13_consumable` is the Phase-13 entry check. The first true cross-doc merge is Phase-13 nomenklatura.
- **Deliberate decoupling.** `scripts/` import no neighbors (a skill wires each chain); the two tracks share only the Librarian output and the design-section-7 citation schema. pii_sweep (count PII) and entity-extract (build a graph) share NO code though both run NER.
- **Hard dep on Librarian.** `plugin.json` `dependencies: ["librarian"]` wires findings output to the shared notes layer (auto-installed with Magpie).
- **Private corpus is gitignored.** Real Flock/Simpsonville PII lives outside the repo; scope any corpus search to `*.py` (an unscoped grep leaks PII) and have corpus compute print aggregates only via a gitignored `.codex-review/` script.
- **Dev env: never bare `python`.** The Claude Code PowerShell tool is `-NoProfile`, so mise activation cannot auto-load -- use `mise run`/`mise exec` or `& .venv\Scripts\python.exe`. Offline suite: `-m "not docling and not spacy and not xray and not tsa and not gliner and not ftm"` (use `-m`, NOT `-k` -- a filename collision drops whole files).
- **This file only indexes; depth lives elsewhere.** WHY -> `docs/plans/*-design.md`; HOW / source-of-truth -> module docstrings; the contract -> `tests/`; per-phase verified library facts -> `skills/*/references/prior-art.md`; build state + lessons -> project memory. Regenerating MANIFEST means rewriting this index to its budget, never appending detail (guarded by `tests/test_manifest_budget.py`).
```
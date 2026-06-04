# Magpie — Structural Map

Fieldwork suite **Magpie** plugin: the FOSS-first FOIA / investigative analysis
toolkit. **Layer 0–1 (laptop-local flagship) is in active development** — most of
the toolkit is still to be built. What exists today is the deterministic stats
flagship plus the plugin skeleton; Track B (entity-network) and heavy infra are
later layers and are NOT yet present.

## Stack

- **Language:** Python 3.12 (pinned 3.12.10). Claude Code plugin (`.claude-plugin/`); hard-depends on the `librarian` plugin.
- **Layer 0–1 deps (laptop-local, no Docker):** pandas 3.0.3, numpy 2.4.6, duckdb 1.5.3, pyarrow 24.0.0; pytest 9.0.3. Phase 3.1 adds openpyxl 3.1.5 (XLSX read/unmerge) + charset-normalizer 3.4.3 (encoding pre-flight). Later phases append spaCy, Docling/OCR, Free Law x-ray, mcp-sqlite.
- **Shape:** `scripts/` invoked directly by skills; `pyproject.toml` configures pytest only (not a pip package).

## Structure

```
.claude-plugin/plugin.json    Plugin manifest (name "magpie", v0.0.1, MIT). `dependencies: ["librarian"]` — hard dep on the shared notes layer.
scripts/
  stats.py                    FLAGSHIP deterministic stats module. Generic, decoupled from any corpus loader (inputs are plain sequences / DataFrame + col names). Functions: gini, top_k_share, bottom_half_share, median_by_category, automation_signature, burstiness, presence_rate, category_pct. Pure (numpy + pandas), golden-testable.
  load_table.py               Phase 3.1 dirty-data loader (CSV/XLSX -> clean DataFrame + load report). load_table(path, *, encoding, text_columns, empty_null, skiprows, sheet_name, forward_fill_columns, parquet_cache) -> LoadResult(.df, .report). Encoding pre-flight (charset-normalizer), ID-like TEXT-whitelist (leading-zero preservation), ""->NULL, openpyxl unmerge-then-fill for merged XLSX cells, skiprows for junk headers, df.ffill() label fill, optional Parquet cache. Pure except file IO; decoupled from stats.py.
  .gitkeep                    scripts/ placeholder (more modules to come).
tests/
  test_stats.py               Golden tests pinned to documented Simpsonville pilot values (Gini ~0.805, top-1% ~27%, bottom-50% ~2.5%, etc.). All fixtures SYNTHETIC; no real corpus read.
  test_load_table.py          TDD for load_table: latin-1 encoding (no mojibake), zero-padded ID preservation, empty_null, XLSX merged-cell fill, skiprows junk headers, df.ffill() label fill, Parquet round-trip. SYNTHETIC fixtures only.
  test_plugin_manifest.py     Smoke test: plugin.json parses, has required keys, and declares the librarian dependency (bare-string or {name} object).
  fixtures/
    agency_counts_sample.json Synthetic heavy-tailed per-agency search counts, tuned so Gini lands ~0.805 (band [0.79,0.82]) and top-1%/bottom-50% shares reproduce the pilot.
    latin1_accents.csv        Synthetic latin-1 CSV (raw 0xE9/0xFC bytes) — exercises the encoding pre-flight (loads José/Montréal/Zürich without crash/mojibake).
    zero_padded_ids.csv       Synthetic CSV with zero-padded zip/case_number (07054, 00891), a numeric amount, an empty cell, and a non-ID router col — exercises the TEXT-whitelist, empty_null, and explicit text_columns.
    junk_header.csv           Synthetic CSV with 2 preamble lines before the real header — exercises skiprows. CRLF written as bytes (Windows text-mode would double \r\n).
    merged_label.xlsx         Synthetic .xlsx with a vertically-merged group label (A2:A4) + a text-formatted zip col — exercises openpyxl unmerge-then-fill and post-read TEXT coercion.
    ffill_labels.csv          Synthetic report-style CSV with blank continuation labels — exercises forward_fill_columns (df.ffill()).
corpus/public/.gitkeep        Placeholder for the redistributable sample corpus (not yet added). The private Simpsonville corpus is NEVER committed (gitignored; see below).
agents/.gitkeep               Placeholder — agents not yet built.
skills/.gitkeep               Placeholder — skills not yet built.
docs/plans/
  2026-06-03-magpie-design.md     Full design doc — source of truth (two tracks A/B, Fieldwork suite & inter-member couplings, locked decisions, §5 components, §6.1 Flock data flow, §7 verification/safety, §9 testing).
  2026-06-03-magpie-layer-0-1.md  Layer 0–1 implementation plan: phased + TDD, research-gate-first for library code (Docling/x-ray/spaCy/mcp-sqlite). Phase 2 = the stats module above.
pyproject.toml                pytest config only (pythonpath ["."], testpaths ["tests"]).
requirements-dev.txt          Layer 0–1 dev+runtime pins (pytest, pandas, duckdb, pyarrow, numpy).
.gitignore                    Python/venv/parquet ignores + a hard block that NEVER commits the Simpsonville/Flock PII corpus.
README.md / LICENSE           Overview + MIT license.
```

## Key Relationships

- **`stats.py` is decoupled from any corpus loader.** Every function takes plain values (a sequence of magnitudes, or a `pandas.DataFrame` + column names) — never a Simpsonville-specific loader — so the math is domain-agnostic and reusable across data sources. Docstrings reference the motivating Simpsonville pilot, but nothing imports it.
- **`load_table.py` is the ingest counterpart to `stats.py`, and they are NOT coupled.** The loader turns a dirty CSV/XLSX into a clean DataFrame; `stats.py` consumes a DataFrame. Neither imports the other — a skill wires them (load -> analyze). The loader's TEXT-whitelist (leading-zero preservation for zip/fips/id/case/...) and `empty_null`/`***`-vs-blank handling feed the same rigor invariants `stats.presence_rate` enforces downstream. Loader APIs (charset-normalizer `from_bytes`, openpyxl `merged_cells.ranges`+unmerge, pandas `df.ffill()` since `fillna(method=)` is removed in pandas 3, pyarrow Parquet) are pinned to the Phase 3 research gate at `skills/dataset-analyze/references/prior-art.md`.
- **Golden tests ↔ synthetic fixture.** `tests/fixtures/agency_counts_sample.json` is hand-tuned so `gini()` reproduces the pilot's ~0.805 and the top-1%/bottom-50% shares match documented values; `test_stats.py` pins those numbers. The fixture is synthetic — the real corpus is never read in tests.
- **Private corpus is gitignored, by design.** The real Flock/Simpsonville FOIA data (names, DOBs, plate context) lives OUTSIDE the repo; `.gitignore` carries an explicit belt-and-suspenders block (`corpus/private/`, `*-Audit.csv`, `**/*[Ss]impsonville*[Aa]udit*`, exhibit files) so PII can never be committed. `corpus/public/` is reserved for the future redistributable sample.
- **Hard dependency on Librarian.** `plugin.json` `dependencies: ["librarian"]` (and a manifest test asserting it) wires Magpie's findings output to the shared Librarian notes layer; this is auto-installed with Magpie. Soft couplings to Research / Prose Craft are design-level only, not yet wired.
- **Plan → code traceability.** `docs/plans/2026-06-03-magpie-layer-0-1.md` is the executable plan and names `scripts/stats.py` as its Phase 2 deliverable; the design doc is its cited source of truth. The plan drives what lands next (per-source recipe, PII sweep, ingest), so the tree will grow under `scripts/`, `skills/`, and `agents/` as phases complete.
- **Python is not on PATH in these sessions.** Per the layer-0-1 plan, the dev venv interpreter is invoked by full path (`...Python312\python.exe`); the manifest/stats tests assume `pythonpath ["."]` from `pyproject.toml`.
- **Layer framing.** Layer 0–1 (this repo's current scope) is pure local Python (stats flagship + future pandas/DuckDB recipe, spaCy, Docling, x-ray via mcp-sqlite). Track B (entity-network) and any docker-compose infra are later layers, not represented in the tree yet.

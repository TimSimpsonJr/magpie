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
  load_table.py               Phase 3.1 dirty-data loader (CSV/XLSX -> clean DataFrame + load report). load_table(path, *, encoding, text_columns, empty_null, skiprows, sheet_name, forward_fill_columns, parquet_cache) -> LoadResult(.df, .report). Encoding pre-flight (charset-normalizer) that HONESTLY flags single-byte ambiguity (report.encoding_low_confidence + encoding_alternatives; pin encoding= for byte-exactness), TOKEN-BOUNDARY ID-like TEXT-whitelist (whole-token id/case/plate/ssn/dob + substring zip/fips/phone/account/license; leading-zero preservation without coercing real numerics like incident_count/casein), ""->NULL, openpyxl unmerge-then-fill for merged XLSX cells + a >2**53 big-int-ID precision warning, skiprows for junk headers, df.ffill() label fill, optional Parquet cache. Pure except file IO; decoupled from stats.py. (pd.read_csv dtype=str is the load; DuckDB is reserved for the Phase 3.3/3.4 query layer.)
  data_quality.py             Phase 3.2 truncation + data-quality GATE (a rigor guardrail: don't publish on incomplete data). check_truncation(rows_or_df, *, ceiling=2**20-1) flags an EXACT-at-ceiling row count (the silent Google-Sheets/CSV 1,048,575 cap) with a request-the-gap message (near-but-below counts NOT auto-flagged). check_date_window(df, date_col, *, requested_start, requested_end) coerces dates (errors=coerce, format=mixed) and flags missing_head/missing_tail when the delivery is NARROWER than the requested window (strict narrower-than; counts n_undated NaT; all-NaT/empty safe). analyze_anomalies(df) emits per-column descriptive LEADS not verdicts (all_null / all_blank / high_null >90% / type_coercion_mismatch for a >80%-numeric-parseable object col with some junk). data_quality_report(df, *, date_col, requested_start, requested_end) aggregates the three. Pure (pandas only), deterministic, no IO/clock/random/network; decoupled from stats.py AND load_table.py (it re-derives the 2**20-1 ceiling rather than importing load_table's copy).
  .gitkeep                    scripts/ placeholder (more modules to come).
tests/
  test_stats.py               Golden tests pinned to documented Simpsonville pilot values (Gini ~0.805, top-1% ~27%, bottom-50% ~2.5%, etc.). All fixtures SYNTHETIC; no real corpus read.
  test_load_table.py          TDD for load_table: latin-1 encoding (no mojibake) + honest single-byte-ambiguity flagging (low_confidence/alternatives), token-boundary TEXT-whitelist (14 false-positive names NOT coerced, 12 true-positives coerced), zero-padded ID preservation, empty_null, XLSX merged-cell fill, XLSX >2**53 big-int precision warning, skiprows junk headers, df.ffill() label fill, Parquet round-trip. SYNTHETIC fixtures only.
  test_data_quality.py        TDD for data_quality (28 tests): truncation flags EXACTLY at 2**20-1 with request-the-gap msg (off-by-one & above-ceiling do NOT flag; custom ceiling; DataFrame branch via len() on a 3-row frame — NO 1M-row materialization); date-window missing_head/tail on a narrow delivery, exact-boundary = full coverage, n_undated count, all-NaT/empty safe; anomalies as LEADS (high_null/all_null/all_blank/type_coercion_mismatch, clean cols silent); aggregator ties all three. All fixtures tiny & inline.
  test_plugin_manifest.py     Smoke test: plugin.json parses, has required keys, and declares the librarian dependency (bare-string or {name} object).
  fixtures/
    agency_counts_sample.json Synthetic heavy-tailed per-agency search counts, tuned so Gini lands ~0.805 (band [0.79,0.82]) and top-1%/bottom-50% shares reproduce the pilot.
    latin1_accents.csv        Synthetic latin-1 CSV (raw 0xE9/0xFC bytes) — exercises the encoding pre-flight (loads José/Montréal/Zürich without crash/mojibake).
    zero_padded_ids.csv       Synthetic CSV with zero-padded zip/case_number (07054, 00891), a numeric amount, an empty cell, and a non-ID router col — exercises the TEXT-whitelist, empty_null, and explicit text_columns.
    junk_header.csv           Synthetic CSV with 2 preamble lines before the real header — exercises skiprows. CRLF written as bytes (Windows text-mode would double \r\n).
    merged_label.xlsx         Synthetic .xlsx with a vertically-merged group label (A2:A4) + a text-formatted zip col — exercises openpyxl unmerge-then-fill and post-read TEXT coercion.
    ffill_labels.csv          Synthetic report-style CSV with blank continuation labels — exercises forward_fill_columns (df.ffill()).
    bigint_id.xlsx            Synthetic .xlsx whose whitelisted account_id holds a 17-digit value stored as an Excel NUMBER (already a rounded IEEE-754 double) — exercises the >2**53 precision warning in report.anomalies.
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
- **`data_quality.py` is the rigor GATE that sits between ingest and analysis, and it imports neither neighbor.** It takes a DataFrame (or a bare row-count int) and reports truncation / date-window / per-column anomaly LEADS so the analyst doesn't publish on incomplete data. It is intentionally standalone: it RE-DERIVES the `2**20 - 1` truncation ceiling rather than importing `load_table._TRUNCATION_CEILING` (the two are independent on purpose — `load_table` flags the same ceiling as a load-time anomaly note over real file IO, whereas `data_quality` is a pure post-hoc gate over an in-memory frame). Both encode the same documented Google-Sheets/CSV cap; if that constant ever moves it must move in both places. The gate's leads-not-verdicts stance mirrors `load_table`'s honest-ambiguity flagging and `stats`' rigor invariants.
- **Golden tests ↔ synthetic fixture.** `tests/fixtures/agency_counts_sample.json` is hand-tuned so `gini()` reproduces the pilot's ~0.805 and the top-1%/bottom-50% shares match documented values; `test_stats.py` pins those numbers. The fixture is synthetic — the real corpus is never read in tests.
- **Private corpus is gitignored, by design.** The real Flock/Simpsonville FOIA data (names, DOBs, plate context) lives OUTSIDE the repo; `.gitignore` carries an explicit belt-and-suspenders block (`corpus/private/`, `*-Audit.csv`, `**/*[Ss]impsonville*[Aa]udit*`, exhibit files) so PII can never be committed. `corpus/public/` is reserved for the future redistributable sample.
- **Hard dependency on Librarian.** `plugin.json` `dependencies: ["librarian"]` (and a manifest test asserting it) wires Magpie's findings output to the shared Librarian notes layer; this is auto-installed with Magpie. Soft couplings to Research / Prose Craft are design-level only, not yet wired.
- **Plan → code traceability.** `docs/plans/2026-06-03-magpie-layer-0-1.md` is the executable plan and names `scripts/stats.py` as its Phase 2 deliverable; the design doc is its cited source of truth. The plan drives what lands next (per-source recipe, PII sweep, ingest), so the tree will grow under `scripts/`, `skills/`, and `agents/` as phases complete.
- **Python is not on PATH in these sessions.** Per the layer-0-1 plan, the dev venv interpreter is invoked by full path (`...Python312\python.exe`); the manifest/stats tests assume `pythonpath ["."]` from `pyproject.toml`.
- **Layer framing.** Layer 0–1 (this repo's current scope) is pure local Python (stats flagship + future pandas/DuckDB recipe, spaCy, Docling, x-ray via mcp-sqlite). Track B (entity-network) and any docker-compose infra are later layers, not represented in the tree yet.

---
name: dataset-analyze
description: This skill should be used when the user asks to "analyze a FOIA dataset", "load and analyze an audit-log CSV/XLSX", "build a queryable dataset", "run the dataset analysis pipeline", "check a data export for truncation", or otherwise wants to turn a dirty FOIA / audit-log CSV or spreadsheet into clean, derived, statistically-summarized data exposed for read-only SQL querying. Covers the load -> data-quality gate -> derive -> SQLite (mcp-sqlite) pipeline.
version: 0.1.0
---

# dataset-analyze

Turn a dirty FOIA / audit-log export (CSV or XLSX) into clean, derived data that
is both summarized with the deterministic `stats` module and exposed for
read-only SQL querying through an `mcp-sqlite` server. This is Magpie's
structured-data flagship (Track A). It is deterministic and rigor-gated: the
pipeline is built to refuse to silently publish on incomplete or misread data.

## The pipeline

Run the steps in order. Each is a script under `scripts/` (invoke with the
project's Python; none import each other). The verified-API contract for every
step lives in `references/prior-art.md` (the Phase 3 research gate) — consult it
before changing a library call.

1. **Load** — `scripts/load_table.py::load_table(path, ...)`. Read a dirty
   CSV/XLSX into a clean DataFrame plus a load report. Pin `encoding=` when the
   report flags `encoding_low_confidence` (a single-byte codepage sniff is not
   trustworthy). The token-boundary TEXT-whitelist preserves leading-zero IDs;
   the NARROW `empty_null` turns only whitespace-only cells into NA, so a literal
   `N/A` / `NULL` survives as a string.

2. **Gate on data quality FIRST** —
   `scripts/data_quality.py::data_quality_report(df, date_col=..., requested_start=..., requested_end=...)`.
   Check truncation BEFORE analyzing: a row count of exactly `2**20 - 1`
   (1,048,575) means the export was silently truncated upstream — stop and
   request the gap rather than publishing on a partial slice. The report also
   surfaces date-window head/tail gaps and per-column anomaly leads.

3. **Derive** — `scripts/derive.py::derive_columns(df, config)`. Add the
   conventional derived columns the analysis needs, driven entirely by `config`
   (home state, keyword vocab, type map, timezone — no jurisdiction is
   hardcoded): `geo`, `reason_cat`, `is_immigration`, `nets`, `has_case`,
   `base_type`, `date_et` / `hour_et` / `dow_et`. Keyword matching is
   word-boundary (so `police` / `service` / `justice` never trip the `ice`
   immigration keyword) and a `***` redaction counts as PRESENT.

4. **Build the served DB** —
   `scripts/build_dataset_db.py::build_dataset_db(df, db_path, text_columns=..., fts_columns=..., exclude_columns=...)`.
   Write the cleaned/derived frame to the SQLite file the server reads. Use
   `${CLAUDE_PROJECT_DIR}/.magpie/dataset.db` — the path the bundled `.mcp.json`
   points at. EXCLUDE every raw-PII column with `exclude_columns` (see "PII
   omission"). Pass leading-zero ID columns in `text_columns` and name-like
   columns in `fts_columns`.

5. **Query read-only** through the `magpie-dataset` MCP server (bundled
   `.mcp.json`). Call `sqlite_get_catalog` first, then use the `ds_`-prefixed
   canned queries or `ds_sqlite_execute` (see "Querying").

6. **Summarize** — feed the derived columns to `scripts/stats.py` (Gini, top-k /
   bottom-half shares, automation signature, burstiness, presence rate). The
   canned queries supply the counts; `stats` supplies the distributional
   measures.

## PII omission (a hard rule)

The served SQLite DB is a surface an analysis agent can hit with arbitrary
read-only SQL. `mcp-sqlite`'s `hidden:` flag only omits a table from the
catalog — the agent can still `SELECT` from it — so it is NOT a security
boundary. The only safe way to keep raw PII (names, DOBs, full reason free-text)
unreachable is to never put it in the served file: pass those columns to
`build_dataset_db(..., exclude_columns=[...])`. Serve derived / aggregate
columns (`geo`, `is_immigration`, `has_case`, counts) instead of raw
identifiers. Full exhibits stay in local, non-served files; `redact-output`
handles PII in published artifacts.

## Querying (read-only; mind the row cap)

The `magpie-dataset` server runs `mcp-sqlite==0.3.2` via `uvx`, read-only by
default — every query opens the DB `?mode=ro`, and no write path is shipped.

- **Call `sqlite_get_catalog` first** to learn the served schema.
- **Prefer the `ds_` canned queries** (`canned_queries.yml`): each is a named,
  reviewed query with an embedded `LIMIT`, mapping to the analysis recipe (geo
  breakdown, immigration count, automation-by-hour, case presence, ...).
- **`ds_sqlite_execute` runs arbitrary read-only SQL — always add a `LIMIT`.**
  mcp-sqlite 0.3.2 enforces NO row cap (it returns every row as one HTML table),
  so an unbounded `SELECT *` on a 1M-row table will try to materialize the whole
  thing. For machine-parseable rows (not just agent-readable output), query the
  Parquet / DuckDB cache directly rather than parsing the MCP HTML.

Build the DB (step 4) before querying — the server reads it at the configured
path. Launching the server needs `uvx` on PATH (the `setup` / `doctor` wizard
checks for it).

## Rigor guardrails (preserve across the pipeline)

- **Truncation before analysis.** Row count `== 2**20 - 1` → truncated; request
  the gap; do not publish.
- **Presence ≠ value.** A `***` redaction is PRESENT; a blank / NaN is ABSENT.
- **Keyword matches are word-boundary.** Confirm a categorization is not a
  substring false hit (the `ICE` / `polICE` trap) before treating it as a finding.
- **`geo`, not `loc`.** The location column is named `geo` (a `loc` column
  collides with the pandas `.loc` indexer).
- **Honest encoding.** When the load report flags `encoding_low_confidence`, pin
  `encoding=` for a byte-exact read rather than trusting the sniff.

## Resources

- **`references/prior-art.md`** — the Phase 3 research gate: verified DuckDB /
  sqlite-utils / openpyxl / `mcp-sqlite` APIs, the read-only + row-cap analysis,
  and the `.mcp.json` interpolation rules.
- **`canned_queries.yml`** — the `mcp-sqlite` metadata (Datasette-compatible):
  served table/column descriptions and the row-capped `ds_` canned queries.
- **`../../.mcp.json`** — the bundled `magpie-dataset` server config (plugin root).
- **`scripts/load_table.py`, `data_quality.py`, `derive.py`, `build_dataset_db.py`,
  `stats.py`** — the pipeline scripts; each has a detailed module docstring.

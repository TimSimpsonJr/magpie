# dataset-analyze — Prior-Art & Verified-API Reference (Phase 3.0 research gate)

> **Status:** Tier-2 research gate for Magpie Phase 3 (`dataset-analyze`). This is the
> spec the Phase 3.1–3.4 implementers build against. Every API below was verified on
> **2026-06-04** against Context7 docs, the upstream source, and/or live introspection of
> the pinned dev `.venv` — **not** from model memory. Where a check found that a doc or a
> web summary contradicted the design's assumption, it is called out in **bold**.
>
> **Verification methods used (so a re-checker can reproduce):**
> - `duckdb`, `pandas` — introspected + executed in the project `.venv`
>   (`.venv/Scripts/python.exe`), which matches `requirements-dev.txt`.
> - `openpyxl` — installed `3.1.5` into a throwaway `--target` dir and ran the unmerge
>   recipe end-to-end (the dev venv was **not** modified).
> - `sqlite-utils`, `chardet`, latest PyPI versions — PyPI JSON API + Context7.
> - `mcp-sqlite` — read the upstream source verbatim via `gh api`
>   (`mcp_sqlite/server.py`, `pyproject.toml`, sample metadata, test suite). This is the
>   security-sensitive target, so claims are grounded in code, not README prose.

## Version pin table (what to put in `requirements-dev.txt` for Phase 3)

| Package | Installed in `.venv` today | Latest on PyPI (2026-06-04) | Phase 3 action | requires-python |
|---|---|---|---|---|
| `duckdb` | **1.5.3** ✅ | 1.5.3 | keep pinned `1.5.3` | — |
| `pandas` | **3.0.3** ✅ | 3.0.3 | keep pinned `3.0.3` | — |
| `numpy` | 2.4.6 ✅ | — | keep `2.4.6` | — |
| `pyarrow` | 24.0.0 ✅ | — | keep `24.0.0` (DuckDB Parquet uses its own engine; pyarrow only needed for Arrow interchange) | — |
| `openpyxl` | **not installed** | **3.1.5** | add `openpyxl==3.1.5` (XLSX read + unmerge) | >=3.8 |
| `sqlite-utils` | **not installed** | **3.39** | add `sqlite-utils==3.39` (build/index the SQLite DB) | >=3.10 |
| `chardet` | **not installed** | **7.4.3** (major-7 line) | add `chardet==7.4.3` for the encoding pre-flight — **or** reuse the already-present `charset-normalizer` 3.4.x (see §5.1) | >=3.10 |
| `mcp-sqlite` | n/a (run via `uvx`, not pip-installed into the venv) | **0.3.2** (rel. 2025-10-25) | pin `mcp-sqlite==0.3.2`; launch via `uvx mcp-sqlite==0.3.2 …` | **>=3.12** |

> **Note on the two Pythons on this box:** the bare `...\Python312\python.exe` has a
> *different* set of packages (pandas 3.0.1 / numpy 2.4.2). The **project `.venv` is the
> source of truth** and matches `requirements-dev.txt` exactly. Always invoke
> `.venv/Scripts/python.exe`.

---

## 1. DuckDB (Python) — `duckdb==1.5.3` ✅ verified by execution

Source: Context7 `/duckdb/duckdb-python` + live execution in the `.venv`.

### Top-level API present in 1.5.3 (introspected)
`duckdb.read_csv` ✅, `duckdb.read_parquet` ✅, `duckdb.from_df` ✅, `duckdb.sql` ✅,
`duckdb.connect` ✅, `duckdb.query` ✅, `duckdb.execute` ✅.

> **Correction to the design's wording:** there is **no top-level `duckdb.read_csv_auto()`**
> in the Python client — `read_csv_auto` is a *SQL table function* (`SELECT * FROM
> read_csv_auto(...)`). The Python entry point is **`duckdb.read_csv(...)`**, which already
> does auto-detection and accepts the type/sample overrides we need. Use `read_csv`.

### CSV → DuckDB → Parquet cache (the loader's fast path) — executed, works

```python
import duckdb

# Explicit types to protect leading-zero columns; sample_size=-1 scans the whole file
# for type inference (vs. the default 20480-row sample) when a column is ambiguous.
rel = duckdb.read_csv(
    "input.csv",
    dtype={"zip": "VARCHAR", "fips": "VARCHAR", "case_number": "VARCHAR"},  # TEXT-whitelist
    sample_size=-1,          # -1 = scan all rows for inference; or pass a large int
    # all_varchar=True,      # nuclear option: read EVERY column as VARCHAR (see §5.2)
)
rel.write_parquet("cache.parquet")   # relation method — verified to create the file
```

Verified behavior: `read_csv("…", dtype={"zip":"VARCHAR"})` keeps `02134` as VARCHAR
(leading zero preserved); `read_csv("…", all_varchar=True)` returns every column typed
`VARCHAR`; `relation.write_parquet(path)` writes the Parquet cache.

> `read_csv`'s explicit-type kwarg is **`dtype=`** (a `{col: "DUCKDB_TYPE"}` dict, values
> are DuckDB SQL type names like `VARCHAR`/`INTEGER`/`DATE`). Confirmed by execution.
> Other useful kwargs available on the Python reader: `header`, `sep`/`delimiter`,
> `na_values`, `encoding`, `names`, `skiprows`, `null_padding`, `ignore_errors`,
> `store_rejects`. (`ignore_errors` / `store_rejects` are how you survive a few malformed
> rows without aborting the whole load — relevant to the dirty-CSV fixtures in Task 3.1.)

### Read-only query path — executed, write actually blocked

```python
# Caching to a persistent DuckDB file and re-opening read-only:
con = duckdb.connect("cache.duckdb", read_only=True)   # kwarg verified in Context7 + run
con.execute("SELECT count(*) FROM my_table").fetchall()
# An INSERT/UPDATE/CREATE against this handle raises duckdb.InvalidInputException.
```

Verified: a write through a `read_only=True` connection raises
`duckdb.InvalidInputException`. Context-manager form also documented:
`with duckdb.connect(database=":memory:", read_only=False) as con: …`.

> For the canonical Track-A flow we **don't** need a persistent DuckDB file — DuckDB is the
> *loader/cache builder* (CSV/XLSX → typed → Parquet), and the **read-only query surface
> exposed to Claude is SQLite via `mcp-sqlite`** (§4). DuckDB read-only mode is documented
> here for the cases where a recipe wants to re-query the Parquet/DuckDB cache directly.

### Register a pandas DataFrame — executed, works (both forms)

```python
con = duckdb.connect()
con.register("my_df", df)                     # df is a pandas.DataFrame  ✅
con.execute("SELECT sum(x) FROM my_df").fetchone()
# or, zero-copy relation from a frame:
duckdb.from_df(df)                            # returns a DuckDBPyRelation  ✅
```

### Reading Parquet back
`duckdb.read_parquet("cache.parquet")` → relation; `.fetchall()` / `.df()` /
`.to_arrow_table()` to materialize. Round-trip verified (leading-zero `zip` survived
CSV→Parquet→read).

**Streaming large results** (for the >1M-row Flock logs): `result.fetch_df_chunk()`
(≈2048-row pandas chunks), `result.fetchmany(size=N)`, or
`result.to_arrow_reader(batch_size=N)` — all documented in `/duckdb/duckdb-python`.

---

## 2. sqlite-utils — `sqlite-utils==3.39` ✅ verified via Context7 + PyPI

Source: Context7 `/simonw/sqlite-utils` (python-api.rst, cli.rst). Latest PyPI **3.39**
(requires-python >=3.10). Use this to **build and index** the SQLite DB that `mcp-sqlite`
then serves read-only.

### Create a DB + insert rows with explicit column types

```python
from sqlite_utils import Database

db = Database("dataset.db")

# Explicit schema (protects ID/ZIP/FIPS columns as TEXT):
db["records"].create({
    "id": int,
    "zip": str,
    "fips": str,
    "name": str,
    "n": int,
}, pk="id", not_null=["id"], strict=True)        # strict=True → STRICT table (SQLite 3.37+)

# Bulk insert (types inferred from first 100 rows unless the table already exists):
db["records"].insert_all(rows, pk="id", batch_size=1000)

# Override / pin a column type at insert time without a prior .create():
db["records"].insert({"id": 1, "name": "Cleo", "age": "5"},
                     pk="id", columns={"age": int})
```

### Insert from CSV (CLI path — handy for the loader, mirrors chardet pre-flight)

```bash
sqlite-utils insert dataset.db records data.csv --csv -d                 # -d = detect types
sqlite-utils insert dataset.db records data.csv --csv --encoding latin-1 # explicit encoding
```

> For the TEXT-whitelist, prefer the **Python `.create()` with explicit `str` columns
> *before* `insert_all`** (or `--csv` without `-d`, which keeps everything TEXT) so detect
> doesn't coerce `02134` → `2134`. `-d`/`--detect-types` is the coercion we must *avoid* on
> whitelisted columns.

### Enable FTS5 on name columns (auto-FTS requirement)

```python
db["records"].enable_fts(["name"], create_triggers=True)   # FTS5 is the DEFAULT
# variants confirmed in docs:
db["records"].enable_fts(["name", "alias"], tokenize="porter")   # stemming
db["records"].enable_fts(["name"], fts_version="FTS4")           # only if FTS5 unavailable
results = list(db["records"].search("smith"))                    # search() helper
```

- `enable_fts(columns, *, create_triggers=False, tokenize=None, fts_version="FTS5", replace=…)`.
  **FTS5 is the default**; pass `create_triggers=True` so the index self-updates on insert.
- CLI equivalent: `sqlite-utils enable-fts dataset.db records name --create-triggers`
  (FTS5 default; `--fts5`/`--tokenize porter` available).

### `analyze-tables` (anomaly warnings — **library-native**, not our own)

```bash
sqlite-utils analyze-tables dataset.db                # all tables, human-readable
sqlite-utils analyze-tables dataset.db records -c zip # one column
```

Per-column it reports **null rows, blank rows (empty-string `""` — distinct from NULL),
distinct count, and most/least common values**; can persist to a stats table
(`total_rows, num_null, num_blank, num_distinct, most_common, least_common`). Options:
`--common-limit N`, `--no-most`, `--no-least`.

> This gives us the **>90%-null** and **blank-vs-NULL** signals for free
> (`num_null / total_rows`, and the explicit `num_blank`). The "***" ≠ blank rigor rule
> from the Simpsonville handoff maps onto `num_blank` vs a presence predicate. **Library-native.**

---

## 3. pandas `read_excel` + openpyxl merged-cell handling

Sources: Context7 `/websites/openpyxl_readthedocs_io_en_stable`; pandas 3.0.3 introspected
in the `.venv`; **unmerge recipe executed end-to-end with openpyxl 3.1.5**.

### Reading .xlsx — `pandas==3.0.3`, `engine="openpyxl"` ✅

```python
import pandas as pd
df = pd.read_excel("book.xlsx", sheet_name=0, engine="openpyxl",
                   dtype={"zip": str, "fips": str},   # TEXT-whitelist also applies to XLSX
                   header=0, skiprows=0)
```

`read_excel` params `engine`, `dtype`, `sheet_name`, `header`, `skiprows` all present in
3.0.3 (introspected). `openpyxl` is the correct engine for `.xlsx`.

> **pandas-3 breaking change — bake into the loader:** `DataFrame.fillna(method="ffill")`
> **raises `TypeError: NDFrame.fillna() got an unexpected keyword argument 'method'`** in
> 3.0.3 (verified). The `method=` arg is *gone*, not merely deprecated. Use **`df.ffill()`**
> (verified: `[1, None, None, 2, None] → [1, 1, 1, 2, 2]`). Any forward-fill-after-unmerge
> via pandas MUST call `df.ffill()` / `df["col"].ffill()`.

### Unmerge + forward-fill merged regions — **the recipe (executed, verified)**

openpyxl drops every cell's value except the **top-left** when a range is merged. To
"unmerge and fill", read the top-left value, unmerge, then write it into every cell of the
former range. `ws.merged_cells.ranges` is the current API
(`ws.merged_cell_ranges` is **deprecated** → don't use). Each entry is a `CellRange` whose
**`.bounds` returns `(min_col, min_row, max_col, max_row)`** (note order: **col first**)
and whose `str(range)`/`.coord` is the `"A2:A4"` string `unmerge_cells` accepts.

```python
from openpyxl import load_workbook

wb = load_workbook("book.xlsx")
ws = wb.active
for mr in list(ws.merged_cells.ranges):          # MUST copy to a list — unmerge mutates the set
    min_col, min_row, max_col, max_row = mr.bounds        # (min_col, min_row, max_col, max_row)
    top_left = ws.cell(row=min_row, column=min_col).value
    ws.unmerge_cells(str(mr))                              # e.g. "A2:A4"
    for r in range(min_row, max_row + 1):
        for c in range(min_col, max_col + 1):
            ws.cell(row=r, column=c).value = top_left
wb.save("book.unmerged.xlsx")
# then: pd.read_excel("book.unmerged.xlsx", engine="openpyxl")  — every former-merged cell now populated
```

**Verified output:** input merge `A2:A4 = "GroupX"` over data rows → after the recipe,
`pd.read_excel(...)` yields `GroupX` on all three rows. `ws.unmerge_cells` accepts either
`range_string="A2:A4"` or `start_row/start_column/end_row/end_column` ints (docs).

> **Two valid strategies — pick per use case (note in §5.4):**
> 1. **openpyxl unmerge-then-fill** (above) — robust, handles *vertical and horizontal*
>    merges and merged *headers*; required when merges are in the header block.
> 2. **pandas `df.ffill()` after a plain `read_excel`** — simpler, but only correct for
>    the common "category merged down a column" case and only *after* you know which axis.
>    Our loader should default to the openpyxl pass (deterministic) and treat `ffill` as the
>    quick path. **Both are documented; neither is fully library-automatic for arbitrary layouts.**

---

## 4. `mcp-sqlite` decision (read-only SQLite MCP server) — SECURITY-SENSITIVE

### 4.0 The server to AVOID (design premise confirmed)
**Anthropic's reference `mcp-server-sqlite` is archived and vulnerable — do NOT use.**
Verified via web research: last release **v2025.4.25 (April 2025)**, moved to
`modelcontextprotocol/servers-archived`, **SQL injection via unsanitized table names
concatenated into SQL with f-strings**, publicly disclosed **June 2025 by Trend Micro**;
**Anthropic declined to patch**, citing archived status. Still ~9.8k weekly PyPI downloads
as of May 2026 (i.e. lots of people are still exposed). This matches the design doc §5.6 /
§10 note exactly.

### 4.1 CHOSEN server: **`mcp-sqlite` by `panasenco` — pin `0.3.2`**

- PyPI/source: `mcp-sqlite==0.3.2` (released **2025-10-25**), repo
  `https://github.com/panasenco/mcp-sqlite`, **requires Python >=3.12**.
- Console script: `mcp-sqlite` (`[project.scripts] mcp-sqlite = "mcp_sqlite.server:main_cli"`).
- Runtime deps (pinned transitive surface to audit): `aiosqlite>=0.21.0`, `anyio>=4.9.0`,
  `mcp>=1.9.2`, `pydantic>=2.11.5`, `pyyaml>=6.0.2`.

**All claims below are read from `mcp_sqlite/server.py` and the test suite — not the README.**

#### Read-only: ✅ YES, and it's the default — but the mechanism is **not** what the web says

> **Correction — flag for the implementer:** a web summary claimed mcp-sqlite has a
> **`-w/--write` CLI flag** that toggles write mode. **This is false.** Reading
> `main_cli()` in `server.py`, the *only* CLI args are:
> `sqlite_file` (positional), `-m/--metadata`, `-p/--prefix`, `-v/--verbose`. **There is no
> `--write` flag.**

How read-only is actually enforced (verbatim from source): every query opens a *fresh*
connection per call with the SQLite URI read-only mode:

```python
# mcp_sqlite/server.py
async def execute(sqlite_file, sql, parameters={}, write=False):
    async with aiosqlite.connect(
        f"file:{sqlite_file}?mode={'rw' if write else 'ro'}", uri=True
    ) as conn:
        ...
```

- The generic **`sqlite_execute(sql)`** tool calls `execute(...)` with the default
  **`write=False` → `?mode=ro`**. So *arbitrary* agent SQL is **always read-only**.
- `write=True` (→ `?mode=rw`) is reachable **only** by a *canned query* whose metadata
  declares `write: true`. There is no path for the agent to flip an arbitrary statement to
  write mode.
- The catalog tool (`sqlite_get_catalog`) also opens `?mode=ro`.

Proven by upstream tests:
- `test_execute_write_not_allowed_default`: `sqlite_execute("create table …")` →
  returns the string `"attempt to write a readonly database"`.
- `test_canned_query_write_fails` vs `test_canned_query_write_succeeds`: a canned query
  writes **iff** its metadata sets `write: true`, else it also hits read-only.

> Security read: this is a **clean read-only-by-default model** and is exactly the posture
> the design wants — *better* than a global `--write` toggle, because read-only is the
> structural default and write is opt-in per named, reviewed query. The `?mode=ro` the
> design assumed is literally what the code uses.

#### Canned queries as MCP tools: ✅ YES (Datasette-compatible metadata)

Metadata is a Datasette-compatible YAML/JSON file passed with `-m`. Structure (from the
shipped `sample/titanic.yml`):

```yaml
databases:
  titanic:                 # <-- must match the DB file *stem* (titanic.db -> "titanic")
    tables:
      Observation:
        description: Main table connecting passenger attributes to outcomes.
        columns:
          survived: "0/1 indicator whether the passenger survived."
      secret_table:
        hidden: true       # hides from sqlite_get_catalog() ONLY (see security note)
    queries:
      get_survivors_of_age:
        title: Count survivors of a specific age
        description: Returns total + survivor counts, overall and for one age.
        sql: |-
          select count(*) as total_passengers,
                 sum(survived) as survived_passengers
          from Observation
          where age = :age          # ':name' params -> required tool input args
        # hide_sql: true            # omit the SQL from the tool description
        # write: true               # opt THIS query into ?mode=rw (default false)
```

Behavior confirmed in source: each `queries.<slug>` becomes an MCP tool named
`{prefix}{slug}`; `:param` tokens are extracted by regex and become required string inputs;
slugs starting with `sqlite_` are rejected (reserved). `hide_sql: true` removes the SQL
from the tool's description; otherwise the SQL is appended to the description.

#### Tools exposed
`sqlite_get_catalog()` (call first — returns DBs/tables/columns + metadata),
`sqlite_execute(sql)` (arbitrary **read-only** SQL), plus one tool per canned query.

#### Result shape
Results are returned as an **HTML `<table>`** string (cells `html.escape`'d) — chosen per
Siu et al. 2023 (arXiv 2305.13062) for LLM legibility. **Not JSON/CSV.** Recipes that need
machine-parseable output should query the Parquet/DuckDB cache directly instead of round-
tripping through this tool.

#### Row-cap / result limit: ❌ **NOT SUPPORTED — real gap, must mitigate**

> **Correction to the design's assumption.** The design lists mcp-sqlite as providing a
> "row-cap". **It does not.** `execute()` does `await cursor.fetchall()` and renders **every
> row** into the HTML table — no `LIMIT`, no cap, no pagination (pagination is listed
> "planned" upstream, not shipped in 0.3.2). On a 1.05M-row Flock table, an unbounded
> `SELECT *` would try to materialize the whole table as one HTML blob.

**Mitigations (in priority order):**
1. **Expose data only through canned queries that embed `LIMIT`** (and aggregate rather
   than dump rows). Since canned queries are the intended safe surface and each is a
   reviewed, named tool, this is the cleanest fix and needs no code.
2. If arbitrary `sqlite_execute` must stay enabled, **document the no-cap behavior as a
   known sharp edge** and rely on the agent issuing `LIMIT`; do not treat the server as
   enforcing any bound.
3. **Thin custom wrapper fallback** (the design's stated fallback): if hard row-capping is
   required, wrap/patch `execute()` to inject/enforce a `LIMIT` (e.g. wrap the SQL as
   `SELECT * FROM (<sql>) LIMIT N`) or post-truncate `fetchall()`. Small, localized change
   to one function. **Decision:** start with **option 1 (canned-queries-with-LIMIT)**; only
   build the wrapper if a recipe genuinely needs unbounded ad-hoc SQL with a guaranteed cap.

#### `hidden: true` is **not** a security boundary
Verified in source + README: `hidden: true` only omits a table from
`sqlite_get_catalog()`; **the agent can still `SELECT` from it** via `sqlite_execute`. Do
not use it to "hide" PII. If a table must be unreachable, **don't put it in the served DB.**

### 4.2 Alternative considered (record for completeness)
`hannesrudolph/sqlite-explorer-fastmcp-mcp-server` — a FastMCP read-only SQLite server with
query validation. Viable as a backup, but **panasenco/mcp-sqlite is the pick** because it
(a) is on PyPI with a clean pinnable version, (b) has the Datasette canned-queries-as-tools
model the design specifically wants, (c) is read-only-by-default at the connection layer,
and (d) has a real test suite proving the read-only guarantee. Keep the FastMCP server as
the named fallback if panasenco/mcp-sqlite goes unmaintained.

### 4.3 Security posture (for Task 3.4 / design §7 "MCP is untrusted")
Treat the served DB + server as **untrusted plumbing** (the 2026 MCP ecosystem had 30+ CVEs):
- **Pinned:** `uvx mcp-sqlite==0.3.2` (and pin the Python: server requires >=3.12).
- **Read-only:** structural default (`?mode=ro`); never ship a metadata file with a
  `write: true` query for this read-only analysis use case.
- **Row-capped:** via canned-query `LIMIT` (server enforces none) — see §4.1 mitigations.
- **Least data:** only put the analysis DB (no raw PII exhibits) behind the server; remember
  `hidden:` is not a boundary, so omit sensitive tables from the file entirely.
- **Prompt-injection awareness:** the same stored-payload→prompt-injection risk that sank
  Anthropic's server applies to *any* server that feeds DB cell values to the agent — cells
  are `html.escape`'d here (blunts HTML injection) but **not** semantically sanitized.
  Findings drawn from served data still pass through Magpie's verification gate (§7).
- Launch (bundled in `magpie/.mcp.json`, Task 3.4), read-only + metadata:
  ```jsonc
  // .mcp.json (use ${CLAUDE_PLUGIN_ROOT} for the metadata path)
  {
    "mcpServers": {
      "magpie-sqlite": {
        "command": "uvx",
        "args": ["mcp-sqlite==0.3.2",
                 "${DATASET_DB}",                                  // path to the built dataset.db
                 "--metadata", "${CLAUDE_PLUGIN_ROOT}/skills/dataset-analyze/canned_queries.yml",
                 "--prefix", "ds_"]
      }
    }
  }
  ```
  (Verify `${CLAUDE_PLUGIN_ROOT}` / env-var interpolation support in `.mcp.json` against
  live Claude Code docs at implementation — see open questions.)

---

## 5. Loading heuristics — library-native vs. our own logic

| Heuristic | Verdict | Where it comes from |
|---|---|---|
| **5.1 chardet encoding pre-flight** | **our own glue; library does detection** | Use `chardet==7.4.3` (`chardet.detect(raw_bytes) -> {"encoding","confidence"}`) **or** the already-installed `charset-normalizer` 3.4.x (`from charset_normalizer import from_bytes`). Both are libraries; the *pre-flight policy* (sniff → pass explicit `encoding=` to `read_csv`/`read_excel`/`sqlite-utils --encoding`) is **our logic**. Recommend `charset-normalizer` to avoid a new dep unless chardet's result is needed specifically. |
| **5.2 TEXT-whitelist (ZIP/FIPS/ID/case-number)** | **our own logic** (libraries provide the mechanism) | DuckDB: `read_csv(dtype={col:"VARCHAR"})` or `all_varchar=True`. pandas: `read_excel(dtype={col:str})`. sqlite-utils: explicit `.create({col:str})` before `insert_all`, or `--csv` without `-d`. The *whitelist of column-name patterns* (`zip`, `fips`, `*_id`, `case*`, leading-zero detection) is **ours**. **Verified:** DuckDB keeps `02134` as VARCHAR with `dtype`. |
| **5.3 `--empty-null` (""→NULL)** | **our own logic** | No single cross-tool flag. DuckDB `read_csv` has `na_values=[""]`. pandas reads `""`→`NaN` by default already. sqlite-utils `--csv` keeps `""` as empty **string** (its `analyze-tables` even reports `num_blank` separately from `num_null`, *because* it distinguishes them). So the `--empty-null` *option* is **ours** to implement consistently across loaders; the underlying knobs exist. (Ties to the "`***`≠blank, blank≠NULL" rigor rule.) |
| **5.4 openpyxl unmerge for XLSX** | **library mechanism + our recipe** | `openpyxl==3.1.5`. Unmerge API is native (`ws.unmerge_cells`, `ws.merged_cells.ranges`); the **unmerge-then-fill loop** (capture top-left → unmerge → fill range) is **our code**. **Recipe executed & verified** (§3). |
| **5.5 auto-FTS5 on name columns** | **library-native** | `sqlite-utils` `enable_fts([...], create_triggers=True)` — **FTS5 is the default**. The *choice of which columns are "name columns"* is **ours**. |
| **5.6 truncation detection @ `2**20 - 1 = 1,048,575`** | **our own logic** | **Verified arithmetic:** `2**20 - 1 == 1048575`. No library flags the Google-Sheets/export ceiling; compare loaded `row_count == 1048575` and raise the `request-the-gap` recommendation. **Ours.** |
| **5.7 `analyze-tables`-style anomaly warnings (>90% null, type-coercion failures)** | **split** | **>90%-null / blank-vs-null = library-native** via `sqlite-utils analyze-tables` (reports `num_null`, `num_blank`, `num_distinct`, `total_rows`). **Type-coercion-failure detection = ours** (e.g. DuckDB `read_csv(store_rejects=True)`/`ignore_errors` surfaces rejected rows; comparing a column's whitelisted-TEXT vs. would-be-INT parse is our check). |

---

## Open questions / verify-at-implementation

1. **`.mcp.json` interpolation** — confirm `${CLAUDE_PLUGIN_ROOT}` *and* arbitrary env-var
   substitution (e.g. `${DATASET_DB}`) are supported in plugin-bundled `.mcp.json` args
   against live Claude Code docs (Task 3.4). If env-var interpolation isn't supported for
   the DB path, the skill must template/emit the `.mcp.json` (or pass an absolute path).
2. **`uvx` availability on the operator's machine** — `mcp-sqlite` is launched via `uvx`
   (uv). The `setup`/`doctor` wizard (Phase 10) should probe for `uv`/`uvx` and document it
   as a Track-A prerequisite, or fall back to `pipx run mcp-sqlite==0.3.2`. Server needs
   **Python >=3.12**.
3. **Row-cap fallback** — decide at 3.4 whether canned-queries-with-`LIMIT` (no code) is
   sufficient, or whether the thin `execute()` wrapper (enforced `LIMIT N`) is needed. If
   arbitrary `sqlite_execute` is left enabled, the no-cap behavior is a documented sharp edge.
4. **chardet vs charset-normalizer** — pick one for the pre-flight. `charset-normalizer`
   3.4.x is already in the environment (pulled by `requests`); `chardet==7.4.3` is a heavier
   but more familiar dep. Recommend defaulting to `charset-normalizer` and only adding
   `chardet` if a fixture needs its specific verdicts.
5. **mcp-sqlite maintenance risk** — single-maintainer, last release 2025-10-25, no tagged
   1.0. Re-check for a newer release at implementation; keep
   `hannesrudolph/sqlite-explorer-fastmcp-mcp-server` as the named fallback (§4.2). Audit
   the pinned transitive deps (`mcp`, `aiosqlite`, `pydantic`) for CVEs at pin time.
6. **HTML result parsing** — mcp-sqlite returns HTML `<table>`, not JSON/CSV. For any recipe
   step that needs structured rows back (not just agent-readable output), query the
   Parquet/DuckDB cache directly rather than parsing the MCP HTML.

---

### Verified-fact provenance (one line each)
- duckdb 1.5.3 `read_csv(dtype=,sample_size=,all_varchar=)`, `write_parquet`, `read_parquet`,
  `connect(read_only=True)` (write → `InvalidInputException`), `register`/`from_df` — **executed in `.venv`**.
- pandas 3.0.3 `read_excel(engine="openpyxl", dtype=, ...)`; `fillna(method=)` **raises**; `df.ffill()` works — **executed in `.venv`**.
- openpyxl 3.1.5 unmerge-then-fill recipe (`merged_cells.ranges`, `.bounds`, `unmerge_cells`) — **executed in a throwaway env**; pandas re-read confirmed fill.
- sqlite-utils 3.39 `create/insert_all/insert(columns=)/enable_fts(FTS5 default, create_triggers, tokenize)/search`, `analyze-tables` (num_null/num_blank/...) — **Context7 + PyPI**.
- mcp-sqlite 0.3.2 read-only-by-default (`?mode=ro`, no `--write` flag), canned-queries-as-tools (`:param`, `write:`/`hide_sql:`), HTML `<table>` output, **no row-cap** (`fetchall`) — **read from upstream `server.py`/tests/pyproject via `gh api`**.
- Anthropic `mcp-server-sqlite` archived (v2025.4.25), SQLi via unsanitized table names, Trend Micro June 2025, unpatched — **web research**.

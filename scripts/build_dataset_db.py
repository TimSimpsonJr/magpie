"""Build a read-only SQLite database from a cleaned / derived DataFrame.

This is the final "expose" step of the dataset-analyze pipeline (load_table ->
data_quality -> derive -> **build_dataset_db** -> mcp-sqlite): it turns one
:class:`pandas.DataFrame` into the SQLite file an ``mcp-sqlite`` server serves to
an analysis agent, returning a structured :class:`BuildResult.report` describing
exactly what it wrote. It is deliberately GENERIC -- every column choice is a
parameter, nothing about a specific jurisdiction / agency / FOIA schema is
hardcoded -- and pure except for writing the db file. It imports no other
``scripts/`` module.

Four behaviors are load-bearing and pinned by the test suite, because the naive
implementation (hand the whole frame to ``db[t].insert_all(df.to_dict(...))``)
gets each one WRONG:

* **PII omission is a HARD EXCLUSION, not hiding.** Every name in
  ``exclude_columns`` is dropped from the frame BEFORE the table is created, so
  it is absent from the served schema ENTIRELY -- there is no column to
  ``SELECT``. This is deliberate and not the same as ``mcp-sqlite``'s ``hidden:``
  knob: ``hidden:`` only omits a table from the catalog the agent is shown, but
  the agent can still issue ``SELECT pii_col FROM ...`` against it. The ONLY way
  to make a sensitive column unreachable is to never put it in the file, so
  exclusion happens here, at build time, and exclusion WINS over
  ``text_columns`` / ``fts_columns`` if a name appears in both (the safe column
  cannot be re-introduced by another option).

* **Leading-zero / ID preservation.** A ``text_columns`` column (a ZIP, a
  case number, an account id) is given an explicit ``str`` type when the table
  is CREATED, before any row is inserted, so ``"07054"`` round-trips as the TEXT
  ``"07054"`` -- not the int ``7054`` (leading zero lost) and not ``"7054"``.
  Relying on sqlite-utils' value-based type inference is unsafe: an upstream
  step that already produced an integer-typed column would be stored INTEGER and
  silently drop the zero.

* **pandas null sentinels -> SQL NULL.** ``None``, ``numpy.nan``, ``pandas.NA``
  (including from a nullable ``Int64`` column, as ``derive_nets`` produces), and
  ``NaT`` are ALL converted to ``None`` (SQL ``NULL``) during the
  frame-to-rows conversion. This is not automatic: binding a ``pandas.NA``
  raises ``sqlite3.ProgrammingError`` ("type 'NAType' is not supported"), and a
  ``NaT`` in a text column binds as the literal string ``"NaT"`` -- so the
  sentinels are normalized explicitly. A missing value is never the literal
  string ``"nan"`` / ``"NA"`` / ``"NaT"`` and never the number ``0``.

* **numpy scalars -> native Python.** A pandas column yields numpy scalar cells
  (``numpy.int64``, ``numpy.float64``, ``numpy.bool_``). Inserting a raw
  ``numpy.int64`` makes sqlite store it as an 8-byte BLOB (a verified
  sqlite-utils 3.39 behavior), which breaks every numeric comparison. Each
  numpy integer / float / bool scalar is therefore cast to native ``int`` /
  ``float`` / ``bool`` so the column lands as a clean INTEGER / REAL and stays
  numerically queryable. ``numpy.datetime64`` / ``Timestamp`` / ``date`` cells
  are left for sqlite-utils to ISO-stringify.

The table is created with EXPLICIT types so the schema is deterministic and
text columns are honored: ``str`` for ``text_columns``, and an inferred
``int`` / ``float`` / ``str`` for the rest based on the column's pandas dtype
(falling back to ``str`` for object/mixed columns). With ``replace=True``
(default) any existing file at ``db_path`` is removed first, so a rebuild is
idempotent -- same schema, same row count, no duplicate rows, no stale columns.
The parent directory is created if missing.

The builder is pure except for file IO: no network, no clock, no randomness, and
the caller's DataFrame is never mutated (exclusion / sanitization operate on a
local copy / fresh row dicts).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlite_utils import Database


@dataclass
class BuildResult:
    """The built database's path plus a structured report of what was written.

    Attributes:
        db_path: the resolved string path of the SQLite file written.
        report: a dict describing the build. Keys:

            * ``table`` -- the table name written.
            * ``rows_written`` -- number of data rows inserted.
            * ``served_columns`` -- sorted list of columns ACTUALLY written
              (i.e. after exclusions), the columns the served DB exposes.
            * ``excluded_columns`` -- sorted list of columns DROPPED from the
              served DB entirely (the PII-omission set that was present in the
              frame; a name not in the frame is silently ignored, not listed).
            * ``text_columns`` -- sorted list of columns forced to TEXT (only
              those present and not excluded).
            * ``fts_columns`` -- sorted list of columns given an FTS5 full-text
              index (only those present and not excluded).
    """

    db_path: str
    report: dict[str, Any] = field(default_factory=dict)


def _is_scalar_null(value: Any) -> bool:
    """True for a scalar pandas missing sentinel (None / NaN / NA / NaT).

    Guarded so a list/array cell (for which :func:`pandas.isna` returns an
    array) is treated as non-null rather than raising on ``bool(array)``. The
    cells here are scalars, but the guard keeps the conversion total against an
    object column that happens to hold a list.
    """
    result = pd.isna(value)
    return result is True


def _to_sql_value(value: Any) -> Any:
    """Sanitize one DataFrame cell into a value sqlite3 can bind cleanly.

    * Any null sentinel (``None`` / ``numpy.nan`` / ``pandas.NA`` / ``NaT``) ->
      ``None`` (SQL ``NULL``). This is required, not cosmetic: ``pandas.NA``
      cannot be bound at all (``sqlite3.ProgrammingError``), and a ``NaT`` in a
      text column would otherwise bind as the literal string ``"NaT"``.
    * A numpy integer / float / bool scalar -> the native Python ``int`` /
      ``float`` / ``bool``. A raw ``numpy.int64`` would otherwise be stored as
      an 8-byte BLOB by sqlite-utils, breaking numeric comparison.
    * Everything else (native ``int`` / ``float`` / ``str`` / ``bytes``, and
      ``datetime`` / ``date`` / ``Timestamp`` which sqlite-utils ISO-stringifies)
      passes through unchanged.
    """
    if _is_scalar_null(value):
        return None
    # numpy bool is a subclass-ish scalar; check it before integer (np.bool_ is
    # not an np.integer, but be explicit so a bool never falls through as int).
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def _sql_type_for_dtype(series: pd.Series, *, force_text: bool) -> type:
    """The sqlite-utils column type (``int`` / ``float`` / ``str``) for a column.

    ``force_text`` (a ``text_columns`` member) always maps to ``str`` so leading
    zeros survive. Otherwise the type is read from the pandas dtype:

    * integer dtypes (incl. the nullable ``Int64``) -> ``int`` (INTEGER);
    * float dtypes -> ``float`` (REAL);
    * boolean dtypes -> ``int`` (sqlite has no BOOLEAN; 0/1 stays numerically
      queryable);
    * datetime dtypes -> ``str`` (sqlite-utils ISO-stringifies the values);
    * everything else (object / string / mixed / category) -> ``str``.

    A ``str`` column type does not force-stringify the inserted values
    (sqlite is dynamically typed); it sets the DECLARED column affinity so an
    integer-looking text value is stored and read back as TEXT.
    """
    if force_text:
        return str
    dtype = series.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return int
    if pd.api.types.is_integer_dtype(dtype):
        return int
    if pd.api.types.is_float_dtype(dtype):
        return float
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return str
    return str


def _normalize_name_list(names: list[str] | None) -> list[str]:
    """De-dupe a caller name list, preserving first-seen order; ``None`` -> []."""
    seen: dict[str, None] = {}
    for name in names or ():
        if name not in seen:
            seen[name] = None
    return list(seen)


def build_dataset_db(
    df: pd.DataFrame,
    db_path: str | os.PathLike,
    *,
    table_name: str = "records",
    text_columns: list[str] | None = None,
    fts_columns: list[str] | None = None,
    exclude_columns: list[str] | None = None,
    pk: str | None = None,
    replace: bool = True,
) -> BuildResult:
    """Build a read-only SQLite database from a DataFrame.

    Args:
        df: the cleaned / derived frame to serve. Never mutated.
        db_path: destination path for the SQLite file (``str`` or
            :class:`os.PathLike`). Its parent directory is created if missing.
        table_name: name of the table to create (default ``"records"``).
        text_columns: column names to store as TEXT regardless of their pandas
            dtype, so leading-zero IDs (a ZIP, a case number) survive byte-exact
            (``"07054"`` stays ``"07054"``). A name not present in the frame --
            or one also in ``exclude_columns`` -- is ignored.
        fts_columns: column names to expose through an FTS5 full-text index
            (``create_triggers=True``). A name not present in the frame -- or one
            also in ``exclude_columns`` -- is ignored.
        exclude_columns: column names to DROP from the served DB entirely (PII
            omission). These are removed before the table is created, so they are
            absent from the schema and cannot be ``SELECT``ed. Exclusion takes
            precedence over ``text_columns`` / ``fts_columns``. A name not present
            in the frame is ignored.
        pk: optional primary-key column name (passed through to table creation).
        replace: when ``True`` (default), an existing file at ``db_path`` is
            removed first so the rebuild is idempotent (same schema + row count,
            no duplicates, no stale columns). When ``False``, rows are inserted
            into an existing table at the path if present (append).

    Returns:
        A :class:`BuildResult` with ``.db_path`` and ``.report``.
    """
    path = Path(db_path)

    # Resolve the option name lists against the ACTUAL columns. Exclusion is
    # computed first and wins: a column dropped for PII cannot be re-introduced
    # as a text/FTS column, so the served set never contains an excluded name.
    present = set(df.columns)
    exclude_set = {c for c in _normalize_name_list(exclude_columns) if c in present}

    served = [c for c in df.columns if c not in exclude_set]
    text_set = [
        c for c in _normalize_name_list(text_columns) if c in present and c not in exclude_set
    ]
    fts_set = [
        c for c in _normalize_name_list(fts_columns) if c in present and c not in exclude_set
    ]

    # Operate on a column-subset VIEW for type/row extraction without mutating
    # the caller's frame (pure-except-IO). ``df[served]`` is a new frame.
    served_df = df[served]
    text_lookup = set(text_set)

    # Explicit column types: str for text_columns, dtype-inferred otherwise. An
    # explicit schema is what makes the build deterministic AND honors the
    # TEXT-whitelist before any value-based inference can int-ify a leading zero.
    columns_spec: dict[str, type] = {
        col: _sql_type_for_dtype(served_df[col], force_text=col in text_lookup)
        for col in served
    }

    # Convert to plain row dicts, sanitizing every cell (NA sentinels -> None,
    # numpy scalars -> native). itertuples/to_dict would re-introduce numpy
    # scalars and pd.NA, so we sanitize cell-by-cell here.
    rows: list[dict[str, Any]] = [
        {col: _to_sql_value(record[col]) for col in served}
        for record in served_df.to_dict(orient="records")
    ]

    # File IO begins here. replace=True makes the rebuild idempotent by starting
    # from a clean file (so a prior build's stale columns / rows cannot survive).
    path.parent.mkdir(parents=True, exist_ok=True)
    if replace and path.exists():
        path.unlink()

    db = Database(path)
    try:
        table = db[table_name]

        # Create the table with the explicit schema FIRST (even when there are
        # no rows), so an empty frame still yields a correctly-typed empty table
        # and the text-column affinity is set before any insert. When appending
        # (replace=False) to an already-existing table, skip creation.
        if not table.exists():
            table.create(columns_spec, pk=pk)

        if rows:
            table.insert_all(rows, pk=pk)

        if fts_set:
            # FTS5 (sqlite-utils' default) with triggers so the index stays in
            # sync with the base table.
            table.enable_fts(fts_set, create_triggers=True)
    finally:
        # Close the connection so the file handle is released. This is required
        # for correctness, not hygiene: on Windows a subsequent rebuild's
        # ``path.unlink()`` raises ``PermissionError`` (WinError 32) while any
        # connection to the file is still open, so leaking it would break the
        # idempotent replace=True path within a single process.
        db.close()

    report = {
        "table": table_name,
        "rows_written": len(rows),
        "served_columns": sorted(served, key=str),
        "excluded_columns": sorted(exclude_set, key=str),
        "text_columns": sorted(text_set, key=str),
        "fts_columns": sorted(fts_set, key=str),
    }
    return BuildResult(db_path=str(path), report=report)

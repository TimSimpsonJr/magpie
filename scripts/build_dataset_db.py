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

* **PII omission is allowlist-first / fail-CLOSED, with a denylist backstop.**
  Two complementary controls decide which columns reach the served file, and a
  column is dropped from the frame BEFORE the table is created so it is absent
  from the served schema ENTIRELY -- there is no column to ``SELECT``:

  - ``include_columns`` (the ALLOWLIST) is fail-CLOSED: when given, ONLY the
    listed columns are served, so a sensitive column the caller never names is
    ABSENT, not exposed. This is the safe default for FOIA PII: forgetting to
    list a column hides it, rather than leaking it.
  - ``exclude_columns`` (the DENYLIST) is fail-OPEN: a forgotten PII column gets
    served. It is the backstop -- it ALWAYS wins, so a column in both the
    allowlist and the denylist is dropped (a safe column cannot be
    re-introduced by also listing it in ``include_columns``).

  This is deliberate and not the same as ``mcp-sqlite``'s ``hidden:`` knob:
  ``hidden:`` only omits a table from the catalog the agent is shown, but the
  agent can still issue ``SELECT pii_col FROM ...`` against it. The ONLY way to
  make a sensitive column unreachable is to never put it in the file, so the
  allowlist/denylist resolution happens here, at build time, and WINS over
  ``text_columns`` / ``fts_columns`` (a text/FTS name for a column that is not
  served is silently ignored).

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

An FTS5 index is enabled when the table does NOT already have an FTS index
(detected via ``Table.detect_fts()``), not merely when the table is freshly
created. ``enable_fts`` runs AFTER the rows are inserted, so its populate covers
them. This resolves all three cases honestly:

* a freshly-created table has no FTS yet, so FTS is enabled and indexes the
  just-inserted rows;
* a ``replace=False`` append to an EXISTING FTS table is detected (``detect_fts``
  returns the FTS table's name) and ``enable_fts`` is SKIPPED -- the
  ``create_triggers=True`` triggers from the first build already keep the index
  in sync, and re-enabling would be an error (``CREATE VIRTUAL TABLE
  [records_fts]`` -> ``sqlite3.OperationalError: table already exists``);
* a ``replace=False`` append to an EXISTING table that was built WITHOUT FTS,
  now requesting ``fts_columns``, has no FTS index, so FTS is enabled on the
  now-populated table and indexes the pre-existing + appended rows. (Gating on
  freshness instead would SILENTLY skip this -- a no-op that still dishonestly
  reported ``fts_columns``.)

With ``replace=True`` the file is unlinked first, so the table is always fresh
and FTS is always enabled.

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
              (i.e. after the allowlist/denylist resolution), the columns the
              served DB exposes.
            * ``included_columns`` -- when an ``include_columns`` allowlist was
              given, the sorted list of its columns PRESENT in the frame; when no
              allowlist was given, ``None``. ``None`` (no allowlist) is distinct
              from ``[]`` (an allowlist that matched no present column).
            * ``excluded_columns`` -- sorted list of columns DROPPED by the
              ``exclude_columns`` denylist backstop (the names that were present
              in the frame; a name not in the frame is silently ignored, not
              listed). The denylist always wins over the allowlist.
            * ``text_columns`` -- sorted list of columns forced to TEXT (only
              those actually served).
            * ``fts_columns`` -- sorted list of columns given an FTS5 full-text
              index (only those actually served).
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
    include_columns: list[str] | None = None,
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
        include_columns: the fail-CLOSED PII ALLOWLIST. When ``None`` (default),
            every column is served minus ``exclude_columns`` (the historical
            all-minus-denylist behavior). When a list, ONLY the listed columns
            are served (intersected with the frame's actual columns), still minus
            ``exclude_columns`` -- so a sensitive column the caller never lists is
            ABSENT, not exposed. Prefer this over ``exclude_columns`` for PII:
            forgetting to list a column hides it (fail-closed), whereas forgetting
            to exclude one leaks it (fail-open). A name not present in the frame
            is silently dropped from the allowlist.
        text_columns: column names to store as TEXT regardless of their pandas
            dtype, so leading-zero IDs (a ZIP, a case number) survive byte-exact
            (``"07054"`` stays ``"07054"``). A name that is not ultimately served
            (not present in the frame, outside the ``include_columns`` allowlist,
            or in ``exclude_columns``) is ignored.
        fts_columns: column names to expose through an FTS5 full-text index
            (``create_triggers=True``). The index is enabled when the table does
            not already have an FTS index (detected via ``Table.detect_fts()``),
            after the rows are inserted -- so a ``replace=False`` append onto a
            previously-non-FTS table indexes the pre-existing + appended rows,
            while an append onto an already-FTS table is left to its existing
            triggers (re-enabling would error). A name that is not ultimately
            served (not present in the frame, outside the ``include_columns``
            allowlist, or in ``exclude_columns``) is ignored.
        exclude_columns: the DENYLIST backstop -- column names to DROP from the
            served DB entirely (PII omission). These are removed before the table
            is created, so they are absent from the schema and cannot be
            ``SELECT``ed. Exclusion ALWAYS wins: a column in both
            ``include_columns`` and ``exclude_columns`` is dropped, and exclusion
            takes precedence over ``text_columns`` / ``fts_columns``. A name not
            present in the frame is ignored. Note this denylist is fail-OPEN (a
            forgotten PII column is served); ``include_columns`` is the
            fail-closed control.
        pk: optional primary-key column name (passed through to table creation).
        replace: when ``True`` (default), an existing file at ``db_path`` is
            removed first so the rebuild is idempotent (same schema + row count,
            no duplicates, no stale columns). When ``False``, rows are inserted
            into an existing table at the path if present (append).

    Returns:
        A :class:`BuildResult` with ``.db_path`` and ``.report``.
    """
    path = Path(db_path)

    # Resolve the option name lists against the ACTUAL columns. Two PII controls
    # decide the served set, and exclusion always wins so a dropped column can
    # never be re-introduced by another option:
    #   * include_columns is the fail-CLOSED allowlist -- when given, a column is
    #     served only if it is listed (a column the caller never names is absent,
    #     not leaked).
    #   * exclude_columns is the fail-OPEN denylist backstop -- it always drops,
    #     even a column that also appears in the allowlist.
    # A text/FTS name is honored only if its column is ultimately served.
    present = set(df.columns)
    exclude_set = {c for c in _normalize_name_list(exclude_columns) if c in present}

    # include_set is None when no allowlist was given (serve all-minus-exclude),
    # else the requested names intersected with the present columns. None (no
    # allowlist) stays distinct from [] (an allowlist matching no column).
    include_set: set[str] | None
    if include_columns is None:
        include_set = None
    else:
        include_set = {c for c in _normalize_name_list(include_columns) if c in present}

    def _is_served(col: str) -> bool:
        if col not in present:  # a name not in the frame is never served
            return False
        if col in exclude_set:  # denylist wins, fail-open backstop
            return False
        if include_set is not None and col not in include_set:  # fail-closed allowlist
            return False
        return True

    served = [c for c in df.columns if _is_served(c)]
    text_set = [c for c in _normalize_name_list(text_columns) if _is_served(c)]
    fts_set = [c for c in _normalize_name_list(fts_columns) if _is_served(c)]

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

        # Capture freshness BEFORE creating the table: it gates table.create
        # (an already-existing table on a replace=False append must not be
        # re-created). With replace=True the file was unlinked above, so the
        # table is always fresh here.
        fresh = not table.exists()

        # Create the table with the explicit schema FIRST (even when there are
        # no rows), so an empty frame still yields a correctly-typed empty table
        # and the text-column affinity is set before any insert. When appending
        # (replace=False) to an already-existing table, skip creation.
        if fresh:
            table.create(columns_spec, pk=pk)

        if rows:
            table.insert_all(rows, pk=pk)

        # Enable FTS5 (sqlite-utils' default) with triggers whenever the table
        # does NOT already have an FTS index -- detect, don't assume. Running
        # AFTER insert_all means the populate covers the just-inserted rows
        # (including a replace=False append onto a previously-non-FTS table).
        # detect_fts() returns the associated FTS table's name if FTS is
        # configured, else None, so the three cases resolve correctly:
        #   * fresh table -> no FTS yet -> enable (indexes the inserted rows);
        #   * replace=False append onto an EXISTING FTS table -> detect_fts() is
        #     not None -> skip (the original create_triggers keep the index in
        #     sync; re-enabling would raise "table [<t>_fts] already exists");
        #   * replace=False append onto an EXISTING NON-FTS table requesting fts
        #     -> detect_fts() is None -> enable now (enable_fts on the populated
        #     table indexes the pre-existing + appended rows). The old
        #     "only when fresh" gate SILENTLY skipped this case while still
        #     reporting fts_columns -- a dishonest no-op.
        if fts_set and table.detect_fts() is None:
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
        # None (no allowlist given) stays distinct from [] (allowlist matched no
        # present column); when given, the requested allowlist's present columns.
        "included_columns": None if include_set is None else sorted(include_set, key=str),
        "excluded_columns": sorted(exclude_set, key=str),
        "text_columns": sorted(text_set, key=str),
        "fts_columns": sorted(fts_set, key=str),
    }
    return BuildResult(db_path=str(path), report=report)

"""Tests for the read-only SQLite builder (``scripts/build_dataset_db.py``).

``build_dataset_db`` is the final "expose" step of the dataset-analyze pipeline
(load_table -> data_quality -> derive -> **build_dataset_db** -> mcp-sqlite). It
turns a cleaned / derived :class:`pandas.DataFrame` into the read-only SQLite
database an ``mcp-sqlite`` server serves. The builder is GENERIC (no
jurisdiction / FOIA-specific column names -- everything is a parameter) and pure
except for writing the db file.

Every fixture here is SYNTHETIC and inline; the db is written under ``tmp_path``.
No real corpus is read. The builder is decoupled from ``load_table`` / ``derive``
/ ``stats`` / ``data_quality``.

The PII-omission, leading-zero, NA-sentinel, and numpy-scalar tests are written
to FAIL against a naive implementation (``db[t].insert_all(df.to_dict(...))``):
a naive insert leaks excluded columns, coerces ``"07054"`` to the int ``7054``,
raises ``ProgrammingError`` on ``pd.NA``, and stores ``np.int64`` as a raw BLOB.
"""

from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlite_utils import Database

from scripts.build_dataset_db import BuildResult, build_dataset_db


@contextmanager
def _open(db_path):
    """Open a sqlite-utils Database and CLOSE it on exit.

    On Windows a lingering open handle blocks a later build's replace=True
    unlink (WinError 32), so every reader here is closed deterministically.
    """
    db = Database(str(db_path))
    try:
        yield db
    finally:
        db.close()


# Each reader opens its own connection and CLOSES it before returning. On
# Windows a lingering open handle blocks a later build's replace=True unlink
# (WinError 32), so a leaked reader connection -- not just a leaked writer -- can
# break the rebuild tests. Closing here keeps the read-backs side-effect-free.
def _columns(db_path, table="records") -> dict:
    """The served table's {column: python_type} as sqlite-utils sees it."""
    db = Database(str(db_path))
    try:
        return db[table].columns_dict
    finally:
        db.close()


def _rows(db_path, table="records") -> list[dict]:
    db = Database(str(db_path))
    try:
        return list(db[table].rows)
    finally:
        db.close()


def _count(db_path, table="records") -> int:
    db = Database(str(db_path))
    try:
        return db[table].count
    finally:
        db.close()


# ==========================================================================
# Invariant 1 -- PII omission is a HARD EXCLUSION, not hiding.
# Excluded columns are dropped from the frame BEFORE writing, so they are
# absent from the served DB's SCHEMA entirely. (mcp-sqlite's `hidden:` only
# omits a table from the catalog -- the agent can still SELECT it -- so the
# only safe way to keep PII unreachable is to never put it in the file.)
# A naive insert_all of the whole frame would leak the column.
# ==========================================================================

def test_excluded_column_absent_from_schema(tmp_path):
    df = pd.DataFrame(
        {"id": [1, 2], "ssn": ["111-22-3333", "444-55-6666"], "amount": [10, 20]}
    )
    db_path = tmp_path / "out.db"
    result = build_dataset_db(df, db_path, exclude_columns=["ssn"])

    cols = _columns(db_path)
    assert "ssn" not in cols, (
        f"PII column 'ssn' must be ABSENT from the served schema, not merely "
        f"hidden; got columns {sorted(cols)!r}"
    )
    assert set(cols) == {"id", "amount"}
    # The report records the omission honestly.
    assert result.report["excluded_columns"] == ["ssn"]
    assert "ssn" not in result.report["served_columns"]
    assert result.report["served_columns"] == ["amount", "id"]


def test_excluded_column_value_is_unreachable_via_select(tmp_path):
    # Belt-and-suspenders: prove an excluded column cannot be SELECTed at all
    # (the failure mode `hidden:` would NOT prevent). A raw query for the column
    # must raise an OperationalError, because the column does not exist.
    import sqlite3

    df = pd.DataFrame({"id": [1], "home_address": ["123 Main St"]})
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path, exclude_columns=["home_address"])

    con = sqlite3.connect(str(db_path))
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("SELECT home_address FROM records").fetchall()
    finally:
        con.close()


def test_excluding_all_but_one_column_still_builds(tmp_path):
    df = pd.DataFrame({"keep": [1, 2], "drop_a": ["x", "y"], "drop_b": [9, 8]})
    db_path = tmp_path / "out.db"
    result = build_dataset_db(df, db_path, exclude_columns=["drop_a", "drop_b"])
    assert set(_columns(db_path)) == {"keep"}
    assert result.report["excluded_columns"] == ["drop_a", "drop_b"]


# ==========================================================================
# Invariant 2 -- leading-zero / ID preservation.
# A `text_columns` column like `zip` with value "07054" must round-trip as the
# TEXT "07054" (not the int 7054, not "7054"). The table is created with
# explicit `str` types for text_columns BEFORE inserting, so type inference
# can't int-ify it. A naive insert that let sqlite-utils infer types from an
# already-int value would store INTEGER and drop the zero.
# ==========================================================================

def test_text_column_preserves_leading_zero(tmp_path):
    df = pd.DataFrame({"id": [1, 2], "zip": ["07054", "00123"]})
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path, text_columns=["zip"])

    cols = _columns(db_path)
    assert cols["zip"] is str, f"zip must be TEXT, got {cols['zip']!r}"
    rows = _rows(db_path)
    assert rows[0]["zip"] == "07054"
    assert rows[1]["zip"] == "00123"


def test_text_column_with_numeric_looking_values_stays_text(tmp_path):
    # A case_number-style column whose values look like integers must still be
    # stored as TEXT when named in text_columns, so "00891" != 891.
    df = pd.DataFrame({"id": [1], "case_number": ["00891"]})
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path, text_columns=["case_number"])
    assert _columns(db_path)["case_number"] is str
    assert _rows(db_path)[0]["case_number"] == "00891"


def test_text_column_is_reported(tmp_path):
    df = pd.DataFrame({"id": [1], "zip": ["07054"]})
    db_path = tmp_path / "out.db"
    result = build_dataset_db(df, db_path, text_columns=["zip"])
    assert result.report["text_columns"] == ["zip"]


# ==========================================================================
# Invariant 3 -- pandas null sentinels -> SQL NULL.
# None / np.nan / pd.NA (incl. from a nullable Int64 col like derive_nets
# produces) / NaT must all become SQL NULL -- never "nan"/"NA"/"NaT", never 0.
# A naive insert_all raises ProgrammingError on pd.NA and stringifies NaT to
# "NaT"; these tests would fail against it.
# ==========================================================================

def test_nullable_int64_na_becomes_sql_null(tmp_path):
    # derive_nets emits a nullable Int64 column carrying pd.NA. The pd.NA cell
    # must read back as None (SQL NULL), and the present values as ints.
    nets = pd.array([5, pd.NA, 12], dtype="Int64")
    df = pd.DataFrame({"id": [1, 2, 3], "nets": nets})
    assert str(df["nets"].dtype) == "Int64"  # guard the fixture's intent
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path)

    rows = _rows(db_path)
    assert rows[0]["nets"] == 5
    assert rows[1]["nets"] is None, (
        f"pd.NA from a nullable Int64 column must become SQL NULL, got "
        f"{rows[1]['nets']!r}"
    )
    assert rows[2]["nets"] == 12
    # And the present values are genuine integers, not bytes / floats.
    assert isinstance(rows[0]["nets"], int)


def test_none_and_nan_become_sql_null(tmp_path):
    df = pd.DataFrame(
        {"id": [1, 2, 3], "label": ["a", None, "c"], "score": [1.0, np.nan, 3.0]}
    )
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path)
    rows = _rows(db_path)
    assert rows[1]["label"] is None
    assert rows[1]["score"] is None
    # Never the literal strings.
    assert rows[1]["label"] != "nan" and rows[1]["label"] != "None"


def test_nat_becomes_sql_null_not_string(tmp_path):
    # A NaT in a datetime column must be NULL, not the literal string "NaT".
    df = pd.DataFrame(
        {
            "id": [1, 2],
            "ts": pd.to_datetime(["2024-01-02 03:04:05", None]),
        }
    )
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path)
    rows = _rows(db_path)
    assert rows[1]["ts"] is None, (
        f"NaT must become SQL NULL, got {rows[1]['ts']!r}"
    )
    assert rows[1]["ts"] != "NaT"


def test_na_in_text_column_becomes_null_not_literal(tmp_path):
    # A missing value in a forced-TEXT column must be NULL, never the string
    # "nan" / "NA" / "<NA>".
    df = pd.DataFrame({"id": [1, 2], "zip": ["07054", None]})
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path, text_columns=["zip"])
    rows = _rows(db_path)
    assert rows[1]["zip"] is None
    assert rows[1]["zip"] not in ("nan", "NA", "<NA>", "None")


# ==========================================================================
# Invariant 4 -- numeric columns stay numeric (and are queryable numerically).
# A plain int/float column is INTEGER/REAL, not TEXT. Critically: a numpy
# scalar (np.int64) must be cast to native int, else sqlite-utils stores it as
# a raw 8-byte BLOB -- which a naive implementation does, breaking numeric
# comparison entirely.
# ==========================================================================

def test_numeric_column_is_numeric_type(tmp_path):
    df = pd.DataFrame({"id": [1, 2], "amount": [10, 250], "rate": [0.5, 2.5]})
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path)
    cols = _columns(db_path)
    assert cols["amount"] is int, f"amount must be INTEGER, got {cols['amount']!r}"
    assert cols["rate"] is float, f"rate must be REAL, got {cols['rate']!r}"


def test_numeric_column_supports_numeric_comparison(tmp_path):
    # The real test of "stays numeric": a WHERE amount > 100 must work and a
    # MAX/SUM must aggregate -- impossible if the value were stored as a BLOB or
    # a string.
    df = pd.DataFrame({"id": [1, 2, 3], "amount": [10, 250, 99]})
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path)
    with _open(db_path) as db:
        big = list(db.query("SELECT id FROM records WHERE amount > 100"))
        assert [r["id"] for r in big] == [2]
        agg = list(db.query("SELECT SUM(amount) AS s, MAX(amount) AS m FROM records"))
        assert agg[0]["s"] == 359 and agg[0]["m"] == 250


def test_numpy_int_scalar_stored_as_integer_not_blob(tmp_path):
    # Regression guard for the sqlite-utils surprise: inserting a raw np.int64
    # binds as an 8-byte BLOB (b'\\x05\\x00...'), NOT an integer. The builder
    # must cast numpy scalars to native Python ints. A pandas int64 column
    # yields np.int64 scalars under .items(), so this exercises the real path.
    df = pd.DataFrame({"id": [1, 2], "count": np.array([5, 9], dtype="int64")})
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path)
    rows = _rows(db_path)
    assert rows[0]["count"] == 5
    assert isinstance(rows[0]["count"], int) and not isinstance(rows[0]["count"], bytes)
    # Numeric comparison confirms it is a genuine INTEGER, not a BLOB.
    with _open(db_path) as db:
        got = list(db.query("SELECT id FROM records WHERE count >= 9"))
        assert [r["id"] for r in got] == [2]


# ==========================================================================
# Invariant 5 -- FTS5 search works.
# After fts_columns=["name"], a sqlite-utils db[t].search("...") returns the
# expected row (FTS5, create_triggers=True).
# ==========================================================================

def test_fts_search_returns_matching_row(tmp_path):
    df = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "name": ["John Smith", "Jane Doe", "Sam Smithson"],
        }
    )
    db_path = tmp_path / "out.db"
    result = build_dataset_db(df, db_path, fts_columns=["name"])

    with _open(db_path) as db:
        hits = list(db["records"].search("smith"))
    ids = sorted(h["id"] for h in hits)
    assert ids == [1], f"FTS MATCH 'smith' should hit only row 1, got {ids!r}"
    assert result.report["fts_columns"] == ["name"]


def test_fts_columns_can_combine_with_text_columns(tmp_path):
    # A column can be both forced TEXT and FTS-indexed (a redacted-but-named
    # field). Both behaviors must hold simultaneously.
    df = pd.DataFrame({"zip": ["07054", "10001"], "name": ["Alpha Co", "Beta LLC"]})
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path, text_columns=["zip"], fts_columns=["name"])
    assert _columns(db_path)["zip"] is str
    assert _rows(db_path)[0]["zip"] == "07054"
    with _open(db_path) as db:
        hits = list(db["records"].search("beta"))
    assert [h["name"] for h in hits] == ["Beta LLC"]


# ==========================================================================
# Invariant 6 -- determinism / idempotence.
# Building twice to the same path with replace=True yields the same served
# schema + row count (no duplicate rows, no stale columns). Parent dir created.
# ==========================================================================

def test_rebuild_is_idempotent(tmp_path):
    df = pd.DataFrame({"id": [1, 2], "zip": ["07054", "00123"], "amount": [10, 20]})
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path, text_columns=["zip"])
    first_cols = _columns(db_path)
    first_count = _count(db_path)

    build_dataset_db(df, db_path, text_columns=["zip"])
    second_cols = _columns(db_path)
    second_count = _count(db_path)

    assert second_cols == first_cols
    assert first_count == second_count == 2, "rebuild must not duplicate rows"


def test_rebuild_drops_stale_columns(tmp_path):
    # Building a DIFFERENT-shaped frame to the same path with replace=True must
    # not leave a stale column from the prior build.
    db_path = tmp_path / "out.db"
    build_dataset_db(pd.DataFrame({"id": [1], "old_col": ["x"]}), db_path)
    assert "old_col" in _columns(db_path)
    build_dataset_db(pd.DataFrame({"id": [1], "new_col": ["y"]}), db_path)
    cols = _columns(db_path)
    assert "old_col" not in cols, "stale column survived a replace=True rebuild"
    assert "new_col" in cols


def test_creates_missing_parent_directory(tmp_path):
    nested = tmp_path / "a" / "b" / "c" / "out.db"
    assert not nested.parent.exists()
    df = pd.DataFrame({"id": [1]})
    result = build_dataset_db(df, nested)
    assert nested.exists()
    assert result.db_path == str(nested)


def test_replace_false_appends_to_existing(tmp_path):
    # With replace=False, building onto an existing table appends rows rather
    # than recreating the file (idempotence is opt-in via replace=True).
    df = pd.DataFrame({"id": [1, 2], "amount": [10, 20]})
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path, pk="id", replace=True)
    build_dataset_db(
        pd.DataFrame({"id": [3], "amount": [30]}), db_path, pk="id", replace=False
    )
    assert _count(db_path) == 3


# ==========================================================================
# Invariant 7 -- empty / edge configs.
# Defaults (all-None lists) work; an empty DataFrame builds an empty table;
# excluding / texting / fts-ing a non-present column is ignored gracefully.
# ==========================================================================

def test_defaults_all_none_build_cleanly(tmp_path):
    df = pd.DataFrame({"id": [1, 2], "amount": [10, 20]})
    db_path = tmp_path / "out.db"
    result = build_dataset_db(df, db_path)
    assert result.report["rows_written"] == 2
    assert result.report["served_columns"] == ["amount", "id"]
    assert result.report["excluded_columns"] == []
    assert result.report["text_columns"] == []
    assert result.report["fts_columns"] == []


def test_empty_dataframe_builds_empty_table(tmp_path):
    # An empty frame (no rows) with known columns must build an empty table with
    # the right schema, not crash.
    df = pd.DataFrame({"id": pd.Series([], dtype="int64"), "name": pd.Series([], dtype="object")})
    db_path = tmp_path / "out.db"
    result = build_dataset_db(df, db_path)
    assert result.report["rows_written"] == 0
    assert _count(db_path) == 0
    assert set(_columns(db_path)) == {"id", "name"}


def test_exclude_column_not_present_is_ignored(tmp_path):
    df = pd.DataFrame({"id": [1], "amount": [10]})
    db_path = tmp_path / "out.db"
    result = build_dataset_db(df, db_path, exclude_columns=["nonexistent"])
    # No error; nothing actually excluded.
    assert result.report["excluded_columns"] == []
    assert set(_columns(db_path)) == {"id", "amount"}


def test_text_and_fts_columns_not_present_are_ignored(tmp_path):
    df = pd.DataFrame({"id": [1], "amount": [10]})
    db_path = tmp_path / "out.db"
    result = build_dataset_db(
        df, db_path, text_columns=["ghost"], fts_columns=["phantom"]
    )
    # Names not in the frame are dropped gracefully and not reported as applied.
    assert result.report["text_columns"] == []
    assert result.report["fts_columns"] == []
    assert set(_columns(db_path)) == {"id", "amount"}


def test_excluded_column_takes_precedence_over_text_and_fts(tmp_path):
    # If a column is both excluded AND named in text_columns/fts_columns,
    # exclusion wins (it is dropped, so it cannot be texted or indexed). This
    # keeps the PII guarantee absolute even under contradictory config.
    df = pd.DataFrame({"id": [1, 2], "ssn": ["111-22-3333", "444-55-6666"]})
    db_path = tmp_path / "out.db"
    result = build_dataset_db(
        df, db_path, exclude_columns=["ssn"], text_columns=["ssn"], fts_columns=["ssn"]
    )
    assert "ssn" not in _columns(db_path)
    assert result.report["text_columns"] == []
    assert result.report["fts_columns"] == []
    assert result.report["excluded_columns"] == ["ssn"]


# ==========================================================================
# API surface / config plumbing
# ==========================================================================

def test_custom_table_name_is_used(tmp_path):
    df = pd.DataFrame({"id": [1]})
    db_path = tmp_path / "out.db"
    result = build_dataset_db(df, db_path, table_name="incidents")
    assert result.report["table"] == "incidents"
    assert _count(db_path, table="incidents") == 1


def test_pk_is_applied(tmp_path):
    df = pd.DataFrame({"id": [1, 2], "amount": [10, 20]})
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path, pk="id")
    db = Database(str(db_path))
    try:
        assert db["records"].pks == ["id"]
    finally:
        db.close()


def test_returns_build_result_with_pathlike_db_path(tmp_path):
    # db_path may be a PathLike, not just a str; the result carries the resolved
    # string path.
    df = pd.DataFrame({"id": [1]})
    db_path = tmp_path / "out.db"
    result = build_dataset_db(df, db_path)
    assert isinstance(result, BuildResult)
    assert result.db_path == str(db_path)
    assert isinstance(result.report, dict)


def test_input_frame_is_not_mutated(tmp_path):
    # Pure-except-IO: excluding a column must not mutate the caller's frame.
    df = pd.DataFrame({"id": [1, 2], "ssn": ["a", "b"], "amount": [10, 20]})
    original_columns = list(df.columns)
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path, exclude_columns=["ssn"])
    assert list(df.columns) == original_columns, "caller's frame was mutated"
    assert "ssn" in df.columns


# ==========================================================================
# Invariant 8 -- include_columns is a FAIL-CLOSED allowlist.
# The denylist (exclude_columns) is fail-OPEN: a forgotten PII column is
# served. The allowlist serves ONLY the named columns, so a column the caller
# never mentions is ABSENT, not exposed -- the safe default for FOIA PII.
# (Codex phase-3.4 review, cluster pii-fail-open.)
# ==========================================================================

def test_include_columns_serves_only_listed(tmp_path):
    df = pd.DataFrame(
        {"id": [1, 2], "geo": ["SC", "OOS"], "name": ["A", "B"], "ssn": ["x", "y"]}
    )
    db_path = tmp_path / "out.db"
    result = build_dataset_db(df, db_path, include_columns=["id", "geo"])
    assert set(_columns(db_path)) == {"id", "geo"}
    assert result.report["served_columns"] == ["geo", "id"]
    assert result.report["included_columns"] == ["geo", "id"]


def test_include_columns_forgotten_column_is_absent_fail_closed(tmp_path):
    # The fail-closed property: a sensitive column NOT in the allowlist is absent
    # even though it was never named in exclude_columns. (A denylist would have
    # served it -- the failure mode this allowlist exists to prevent.)
    df = pd.DataFrame({"id": [1], "geo": ["SC"], "home_address": ["123 Main St"]})
    db_path = tmp_path / "out.db"
    build_dataset_db(df, db_path, include_columns=["id", "geo"])  # home_address never mentioned
    cols = _columns(db_path)
    assert "home_address" not in cols
    assert set(cols) == {"id", "geo"}


def test_exclude_wins_over_include(tmp_path):
    # A column in BOTH include and exclude is DROPPED (exclude is the absolute
    # PII guarantee; it cannot be undone by also listing the column in include).
    df = pd.DataFrame({"id": [1], "ssn": ["111-22-3333"]})
    db_path = tmp_path / "out.db"
    result = build_dataset_db(
        df, db_path, include_columns=["id", "ssn"], exclude_columns=["ssn"]
    )
    assert "ssn" not in _columns(db_path)
    assert result.report["served_columns"] == ["id"]


def test_include_columns_none_serves_all_minus_exclude(tmp_path):
    # Backward-compat: include_columns=None keeps the all-minus-exclude default,
    # and the report distinguishes "no allowlist" (None) from an empty one.
    df = pd.DataFrame({"id": [1], "a": [2], "b": [3]})
    db_path = tmp_path / "out.db"
    result = build_dataset_db(df, db_path, exclude_columns=["b"])
    assert set(_columns(db_path)) == {"id", "a"}
    assert result.report["included_columns"] is None


def test_include_columns_restricts_text_and_fts_to_allowlist(tmp_path):
    # A text_columns / fts_columns name not in the allowlist is ignored, because
    # it is not served at all.
    df = pd.DataFrame(
        {"id": [1], "zip": ["07054"], "name": ["Acme"], "secret": ["s"]}
    )
    db_path = tmp_path / "out.db"
    result = build_dataset_db(
        df, db_path, include_columns=["id", "zip"], text_columns=["zip"], fts_columns=["name"]
    )
    assert set(_columns(db_path)) == {"id", "zip"}
    assert result.report["text_columns"] == ["zip"]
    assert result.report["fts_columns"] == []  # name not served -> not indexed


# ==========================================================================
# Invariant 9 -- replace=False append is safe with FTS, and FTS is enabled
# whenever the table does NOT already have an FTS index (detect-don't-assume).
# Gating enable_fts on "fresh table" was wrong twice over:
#   * append onto an existing FTS table -> must SKIP (the original
#     create_triggers keep the index in sync; re-enabling errors with
#     "table <t>_fts already exists").
#   * append onto an existing NON-FTS table requesting fts -> must ENABLE now
#     (a "fresh"-only gate silently no-ops AND dishonestly reports fts_columns).
# So gate on table.detect_fts() is None, not on freshness.
# (Codex phase-3.4 review, cluster append-fts-path; confirmatory re-review,
# cluster fts-existing-table.)
# ==========================================================================

def test_replace_false_append_with_fts_indexes_new_rows(tmp_path):
    db_path = tmp_path / "out.db"
    build_dataset_db(
        pd.DataFrame({"id": [1, 2], "name": ["John Smith", "Jane Doe"]}),
        db_path, fts_columns=["name"], pk="id", replace=True,
    )
    # Append onto the existing FTS table -- must not crash re-enabling FTS.
    build_dataset_db(
        pd.DataFrame({"id": [3], "name": ["Mary Smith"]}),
        db_path, fts_columns=["name"], pk="id", replace=False,
    )
    assert _count(db_path) == 3
    with _open(db_path) as db:
        hits = sorted(h["id"] for h in db["records"].search("smith"))
    # Both the original (John Smith) and the appended (Mary Smith) row are found,
    # proving the existing triggers indexed the appended row.
    assert hits == [1, 3]


def test_replace_false_append_enables_fts_on_existing_non_fts_table(tmp_path):
    # Regression (confirmatory re-review, cluster fts-existing-table): the prior
    # "enable FTS only when fresh" gate SILENTLY skipped enable_fts here -- the
    # table exists (not fresh) but was built WITHOUT FTS, so requesting fts on a
    # replace=False append was a no-op AND the report dishonestly claimed
    # fts_columns. Gating on detect_fts() is None enables FTS on the now-populated
    # table, indexing both the original and the appended rows.
    db_path = tmp_path / "out.db"
    # First build: an EXISTING table created WITHOUT any FTS index.
    build_dataset_db(
        pd.DataFrame({"id": [1, 2], "name": ["John Smith", "Jane Doe"]}),
        db_path, pk="id", replace=True,  # no fts_columns
    )
    with _open(db_path) as db:
        assert db["records"].detect_fts() is None  # guard: not FTS-indexed yet

    # Append with replace=False AND request FTS for the first time.
    result = build_dataset_db(
        pd.DataFrame({"id": [3], "name": ["Mary Smith"]}),
        db_path, fts_columns=["name"], pk="id", replace=False,
    )

    # (a) no crash, and the rows are all present.
    assert _count(db_path) == 3
    # (b) an FTS search now works and returns BOTH an original (John Smith, id=1)
    #     and the appended (Mary Smith, id=3) row -- proving enable_fts ran on the
    #     populated table and indexed the pre-existing + appended rows.
    with _open(db_path) as db:
        assert db["records"].detect_fts() is not None
        hits = sorted(h["id"] for h in db["records"].search("smith"))
    assert hits == [1, 3]
    # (c) the report honestly records the indexing that actually happened.
    assert result.report["fts_columns"] == ["name"]

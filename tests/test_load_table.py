"""Tests for the dirty-data loader (``scripts/load_table.py``), Phase 3.1.

FOIA exports arrive dirty: a county clerk's CSV is latin-1, a ZIP column has
lost its leading zeros to a previous Excel round-trip, empty cells mean
"missing" but read back as ``""``, and a spreadsheet uses vertically-merged
cells for a category label that openpyxl only stores in the top-left cell.
``load_table`` turns each of these into a clean :class:`pandas.DataFrame` plus
a structured load report.

Every fixture here is SYNTHETIC and committed under ``tests/fixtures/`` so the
exact bytes (encoding, leading zeros, merged regions) are inspectable. No real
corpus is read. The loader is decoupled from ``stats.py`` and from any
Simpsonville-specific loader.

The latin-1 fixture is built from explicit byte escapes, never a shell string
round-trip, because non-ASCII bytes are exactly what the loader must survive.
"""

from pathlib import Path

import pandas as pd
import pytest

from scripts.load_table import LoadResult, load_table

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Byte 0xE9 is "é" in latin-1; 0xFC is "ü"; 0xEF is "ï". These are NOT valid
# standalone UTF-8 lead bytes here, so a strict utf-8 read raises. The loader
# must detect (or be told) the encoding and read them without mojibake.
LATIN1_CSV = FIXTURES / "latin1_accents.csv"
ZERO_PADDED_CSV = FIXTURES / "zero_padded_ids.csv"
JUNK_HEADER_CSV = FIXTURES / "junk_header.csv"
MERGED_LABEL_XLSX = FIXTURES / "merged_label.xlsx"
FFILL_LABELS_CSV = FIXTURES / "ffill_labels.csv"


# ==========================================================================
# 3.1.a  Encoding pre-flight (latin-1, non-ASCII bytes)
# ==========================================================================

def test_latin1_loads_without_crash_and_records_encoding():
    # Auto-detect path: the loader must read a latin-1 file (0xE9 etc.) without
    # raising a UnicodeDecodeError, and the report must show detection ran and
    # landed on a non-utf-8 encoding.
    result = load_table(LATIN1_CSV)
    assert isinstance(result, LoadResult)
    assert result.df.shape[0] == 2  # two data rows
    enc = result.report["encoding"].lower()
    assert enc not in ("utf-8", "utf8", "ascii"), (
        f"expected a non-utf-8 detected encoding, got {enc!r}"
    )
    assert result.report["encoding_detected"] is True


def test_latin1_explicit_encoding_round_trips_exactly():
    # When the caller pins encoding="latin-1", the accented characters must
    # survive byte-exact (no mojibake, no replacement chars). This is the
    # deterministic correctness anchor: statistical detection may guess a
    # different single-byte codepage, but an explicit latin-1 read is exact.
    result = load_table(LATIN1_CSV, encoding="latin-1")
    assert result.report["encoding"].lower() in ("latin-1", "latin1", "iso-8859-1")
    assert result.report["encoding_detected"] is False  # caller-provided, not sniffed
    names = list(result.df["name"])
    assert names == ["Jos\xe9", "Ren\xe9e"]  # José, Renée
    cities = list(result.df["city"])
    assert cities == ["Montr\xe9al", "Z\xfcrich"]  # Montréal, Zürich


def test_latin1_no_replacement_characters():
    # A clumsy "read as utf-8 with errors=replace" would smuggle U+FFFD into the
    # data. Assert the loaded cells contain no replacement character.
    result = load_table(LATIN1_CSV, encoding="latin-1")
    joined = "".join(str(v) for v in result.df.to_numpy().ravel())
    assert "�" not in joined


# ==========================================================================
# 3.1.b  TEXT-whitelist (leading zeros preserved)
# ==========================================================================

def test_zero_padded_zip_stays_string_with_leading_zero():
    # A "zip" column matches the ID-like name pattern, so it loads as text and
    # 07054 stays "07054" rather than collapsing to the int 7054.
    result = load_table(ZERO_PADDED_CSV)
    zips = result.df["zip"]
    assert zips.iloc[0] == "07054"
    assert zips.iloc[1] == "00123"
    # The column is object/string dtype, not an integer dtype.
    assert zips.dtype == object or pd.api.types.is_string_dtype(zips)
    # And the report says which columns were forced to text.
    assert "zip" in result.report["text_columns"]


def test_case_number_column_forced_to_text():
    # "case_number" also matches the ID-like pattern (substring "case").
    result = load_table(ZERO_PADDED_CSV)
    assert "case_number" in result.report["text_columns"]
    assert result.df["case_number"].iloc[0] == "00891"


def test_non_id_numeric_column_stays_numeric():
    # A plain "amount" column is NOT in the whitelist, so it is free to be
    # numeric -- the whitelist must be targeted, not blanket all-text.
    result = load_table(ZERO_PADDED_CSV)
    assert "amount" not in result.report["text_columns"]
    assert pd.api.types.is_numeric_dtype(result.df["amount"])


def test_explicit_text_columns_are_honored():
    # A column whose NAME doesn't match the ID pattern can still be forced to
    # text by the caller. "router" is not ID-like, but passing it keeps 0080.
    result = load_table(ZERO_PADDED_CSV, text_columns=["router"])
    assert "router" in result.report["text_columns"]
    assert result.df["router"].iloc[0] == "0080"


# ==========================================================================
# 3.1.c  empty_null  ("" -> missing)
# ==========================================================================

def test_empty_cells_become_null_when_empty_null_true():
    # Empty string cells should read as missing (NaN/None), consistently.
    result = load_table(ZERO_PADDED_CSV, empty_null=True)
    # Row 2 has an empty "amount" cell in the fixture.
    assert pd.isna(result.df["amount"].iloc[1])
    # A whitelisted TEXT column with an empty cell is also missing, not "".
    assert pd.isna(result.df["middle_name"].iloc[0])


def test_empty_null_false_keeps_empty_string_in_text_columns():
    # With empty_null=False, an empty cell in a TEXT column stays "" rather than
    # becoming NaN -- the caller opted out of the ""->NULL normalization.
    result = load_table(ZERO_PADDED_CSV, empty_null=False)
    assert result.df["middle_name"].iloc[0] == ""


def test_empty_null_default_is_true():
    # empty_null defaults to True (the rigor-friendly default).
    result = load_table(ZERO_PADDED_CSV)
    assert pd.isna(result.df["middle_name"].iloc[0])


# ==========================================================================
# 3.1.d  XLSX merged cells (vertical label fills its region)
# ==========================================================================

def test_xlsx_merged_label_fills_every_row():
    # The fixture merges a "group" label vertically over three rows. After load,
    # all three rows must carry the label, not just the top one (the others
    # would be NaN without the unmerge-then-fill pass).
    result = load_table(MERGED_LABEL_XLSX)
    groups = list(result.df["group"])
    assert groups == ["GroupX", "GroupX", "GroupX", "GroupY"]
    # No NaN leaked into the merged region.
    assert result.df["group"].notna().all()


def test_xlsx_reports_rows_and_no_anomalous_nulls_in_label():
    result = load_table(MERGED_LABEL_XLSX)
    assert result.report["rows_read"] == 4
    # The "item" data column is intact too.
    assert list(result.df["item"]) == ["a", "b", "c", "d"]


def test_xlsx_text_whitelist_applies_too():
    # A zip-like column in an XLSX must also keep its leading zeros (post-read
    # coercion, since openpyxl/pandas would otherwise int-ify it).
    result = load_table(MERGED_LABEL_XLSX)
    assert "zip" in result.report["text_columns"]
    assert result.df["zip"].iloc[0] == "07054"
    assert result.df["zip"].iloc[3] == "08540"


# ==========================================================================
# 3.1.e  Junk header rows (skiprows)
# ==========================================================================

def test_skiprows_skips_preamble_and_finds_header():
    # The fixture has two junk preamble lines before the real header row.
    # skiprows=2 must yield the correct column names and data.
    result = load_table(JUNK_HEADER_CSV, skiprows=2)
    assert list(result.df.columns) == ["agency", "searches", "zip"]
    assert result.df.shape[0] == 3
    assert result.df["agency"].iloc[0] == "PD-1"


def test_skiprows_list_form():
    # skiprows also accepts a list of 0-based row indices to drop.
    result = load_table(JUNK_HEADER_CSV, skiprows=[0, 1])
    assert list(result.df.columns) == ["agency", "searches", "zip"]


def test_skiprows_preserves_text_whitelist():
    # The whitelist still applies after skipping junk: zip keeps its zero.
    result = load_table(JUNK_HEADER_CSV, skiprows=2)
    assert "zip" in result.report["text_columns"]
    assert result.df["zip"].iloc[0] == "07054"


# ==========================================================================
# 3.1.e'  Forward-fill label columns (df.ffill() quick path)
# ==========================================================================

def test_forward_fill_carries_label_down():
    # A report-style CSV where the repeated "group" label is blank on
    # continuation rows. forward_fill_columns=["group"] must carry GroupX down
    # via df.ffill() (NOT the pandas-3-removed fillna(method=)).
    result = load_table(FFILL_LABELS_CSV, forward_fill_columns=["group"])
    assert list(result.df["group"]) == ["GroupX", "GroupX", "GroupX", "GroupY"]
    assert result.report["forward_filled_columns"] == ["group"]


def test_forward_fill_runs_after_empty_null():
    # The blanks only become fillable because empty_null turned "" into NaN
    # first; then ffill carries the value. Without forward_fill, the blanks stay
    # missing (proving the fill is what populates them, not the read).
    plain = load_table(FFILL_LABELS_CSV)
    assert pd.isna(plain.df["group"].iloc[1])  # blank -> NaN, not carried
    assert plain.report["forward_filled_columns"] == []


def test_forward_fill_unknown_column_is_ignored():
    # Naming a column that isn't present is a no-op, not an error.
    result = load_table(FFILL_LABELS_CSV, forward_fill_columns=["nope"])
    assert result.report["forward_filled_columns"] == []


# ==========================================================================
# 3.1.f  Parquet cache round-trip (optional)
# ==========================================================================

def test_parquet_cache_is_written_and_noted(tmp_path):
    cache = tmp_path / "cache.parquet"
    result = load_table(ZERO_PADDED_CSV, parquet_cache=cache)
    assert cache.exists()
    assert result.report["parquet_cache"] == str(cache)


def test_parquet_cache_round_trips_leading_zeros(tmp_path):
    # Writing then reading the Parquet must preserve the text ZIP exactly.
    cache = tmp_path / "cache.parquet"
    load_table(ZERO_PADDED_CSV, parquet_cache=cache)
    back = pd.read_parquet(cache)
    assert back["zip"].iloc[0] == "07054"
    assert back["case_number"].iloc[0] == "00891"


# ==========================================================================
# Report shape
# ==========================================================================

def test_report_has_expected_keys():
    result = load_table(ZERO_PADDED_CSV)
    report = result.report
    for key in (
        "encoding",
        "encoding_detected",
        "text_columns",
        "rows_read",
        "anomalies",
    ):
        assert key in report, f"report missing key {key!r}"
    assert report["rows_read"] == len(result.df)
    assert isinstance(report["text_columns"], list)
    assert isinstance(report["anomalies"], list)

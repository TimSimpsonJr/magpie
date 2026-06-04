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

from scripts.load_table import LoadResult, _is_id_like, load_table

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Byte 0xE9 is "é" in latin-1; 0xFC is "ü"; 0xEF is "ï". These are NOT valid
# standalone UTF-8 lead bytes here, so a strict utf-8 read raises. The loader
# must detect (or be told) the encoding and read them without mojibake.
LATIN1_CSV = FIXTURES / "latin1_accents.csv"
ZERO_PADDED_CSV = FIXTURES / "zero_padded_ids.csv"
JUNK_HEADER_CSV = FIXTURES / "junk_header.csv"
MERGED_LABEL_XLSX = FIXTURES / "merged_label.xlsx"
FFILL_LABELS_CSV = FIXTURES / "ffill_labels.csv"
# An XLSX whose whitelisted "account_id" column holds a 17-digit value stored as
# an Excel NUMBER (already a rounded IEEE-754 double by the time openpyxl yields
# it) -- exercises the >2**53 precision warning.
BIGINT_ID_XLSX = FIXTURES / "bigint_id.xlsx"


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

# "middle_name" is NOT auto-whitelisted by the token matcher (it must not, since
# "mid"/"id" inside it is a false positive -- see CRITICAL 2). To exercise
# empty_null on a TEXT column we make it text explicitly via text_columns.
def test_empty_cells_become_null_when_empty_null_true():
    # Empty string cells should read as missing (NaN/None), consistently.
    result = load_table(ZERO_PADDED_CSV, empty_null=True, text_columns=["middle_name"])
    # Row 2 has an empty "amount" cell in the fixture.
    assert pd.isna(result.df["amount"].iloc[1])
    # A whitelisted TEXT column with an empty cell is also missing, not "".
    assert pd.isna(result.df["middle_name"].iloc[0])


def test_empty_null_false_keeps_empty_string_in_text_columns():
    # With empty_null=False, an empty cell in a TEXT column stays "" rather than
    # becoming NaN -- the caller opted out of the ""->NULL normalization.
    result = load_table(ZERO_PADDED_CSV, empty_null=False, text_columns=["middle_name"])
    assert result.df["middle_name"].iloc[0] == ""


def test_empty_null_default_is_true():
    # empty_null defaults to True (the rigor-friendly default).
    result = load_table(ZERO_PADDED_CSV, text_columns=["middle_name"])
    assert pd.isna(result.df["middle_name"].iloc[0])


# ==========================================================================
# 3.1.c'  empty_null is NARROW: only literal empty/space cells become NA
# (regression: CRITICAL 1 -- the empty_null=True read used keep_default_na=True,
#  so pandas ALSO applied its default NA spellings and a cell literally
#  containing "N/A" / "NULL" / "NaN" / "None" was silently turned into NaN --
#  data loss before derive_has_case / keyword matching ever sees it, in direct
#  contradiction of the module's narrow-NA contract. ONLY a literal empty/space
#  cell may become NA.)
# ==========================================================================

def test_empty_null_preserves_literal_na_sentinels(tmp_path):
    # A column literally containing "N/A", "NULL", "NaN", "None" plus a genuinely
    # empty cell. With empty_null=True ONLY the empty cell may become NA; the
    # default-NA spellings must survive as STRINGS (pre-fix keep_default_na=True
    # nullified N/A / NULL / NaN / None).
    p = tmp_path / "literal_na.csv"
    p.write_bytes(
        b"row,note\n"
        b"r1,N/A\n"
        b"r2,NULL\n"
        b"r3,NaN\n"
        b"r4,None\n"
        b"r5,\n"          # genuinely empty cell -> the ONLY NA
        b"r6,plain\n"
    )
    result = load_table(p, empty_null=True)
    note = result.df["note"]
    # The four literal sentinels survive verbatim as strings (NOT nulled).
    assert note.iloc[0] == "N/A"
    assert note.iloc[1] == "NULL"
    assert note.iloc[2] == "NaN"
    assert note.iloc[3] == "None"
    assert not note.iloc[:4].isna().any(), (
        f"literal NA spellings were silently nulled: {note.tolist()!r}"
    )
    # Only the empty cell is NA.
    assert pd.isna(note.iloc[4])
    assert note.iloc[5] == "plain"


def test_empty_null_preserves_literal_na_in_text_whitelist_column(tmp_path):
    # Same contract on a whitelisted TEXT column (dtype=str path). A case_number
    # literally redacted as the string "NULL" must NOT vanish before the
    # ***-vs-blank-vs-NULL rigor distinction downstream can weigh it.
    p = tmp_path / "literal_na_id.csv"
    p.write_bytes(
        b"case_number,amount\n"
        b"NULL,1\n"
        b"N/A,2\n"
        b"00891,3\n"
        b",4\n"           # empty -> NA
    )
    result = load_table(p, empty_null=True)
    assert "case_number" in result.report["text_columns"]
    cn = result.df["case_number"]
    assert cn.iloc[0] == "NULL"
    assert cn.iloc[1] == "N/A"
    assert cn.iloc[2] == "00891"   # leading zero still preserved
    assert pd.isna(cn.iloc[3])     # only the empty cell is NA


def test_empty_null_false_also_preserves_literal_na_sentinels(tmp_path):
    # Consistency check (the empty_null=False path): opting out of ""->NA must
    # ALSO leave the default-NA spellings as literal strings (it must not nullify
    # N/A / NULL either), and an empty cell stays "" rather than NaN.
    p = tmp_path / "literal_na_false.csv"
    p.write_bytes(
        b"row,note\n"
        b"r1,N/A\n"
        b"r2,NULL\n"
        b"r3,\n"           # empty cell -> stays "" (opted out of ""->NA)
        b"r4,plain\n"
    )
    result = load_table(p, empty_null=False)
    note = result.df["note"]
    assert note.iloc[0] == "N/A"
    assert note.iloc[1] == "NULL"
    assert note.iloc[2] == ""       # empty kept literally, not NA
    assert note.iloc[3] == "plain"
    assert not note.isna().any()


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
        "encoding_used",
        "encoding_detected",
        "encoding_confidence",
        "encoding_low_confidence",
        "encoding_alternatives",
        "text_columns",
        "rows_read",
        "anomalies",
    ):
        assert key in report, f"report missing key {key!r}"
    assert report["rows_read"] == len(result.df)
    assert isinstance(report["text_columns"], list)
    assert isinstance(report["anomalies"], list)
    # The transient precision-warning carrier must not leak into the public
    # report (it is merged into "anomalies" and popped).
    assert "_precision_warnings" not in report


# ==========================================================================
# 3.1.a'  Encoding auto-detect is HONEST about single-byte ambiguity
# (regression: CRITICAL 1 -- auto-detect silently mojibaked José->Josķ on this
#  venv while reporting confidence == 1.0; the report must FLAG the ambiguity so
#  a caller can require an explicit encoding= rather than trust a silent guess.)
# ==========================================================================

def test_latin1_auto_detect_flags_low_confidence():
    # No encoding= given: on this venv charset-normalizer picks a single-byte
    # codepage (e.g. cp775) with chaos 0.0 -> "confidence 1.0", yet that decode
    # corrupts José/Montréal. Single-byte detection is INHERENTLY ambiguous, so
    # the report must mark it low-confidence (not silently certain). This fails
    # against the pre-fix loader, which had no such flag.
    result = load_table(LATIN1_CSV)
    assert result.report["encoding_detected"] is True
    assert result.report["encoding_low_confidence"] is True, (
        "a sniffed single-byte codepage must be flagged ambiguous, not trusted "
        f"silently; report={result.report}"
    )


def test_latin1_auto_detect_exposes_alternatives():
    # The ambiguity must be actionable: the report lists the OTHER plausible
    # encodings the detector ranked, so a caller/skill knows which codepages an
    # explicit encoding= would have to choose between.
    result = load_table(LATIN1_CSV)
    alts = result.report["encoding_alternatives"]
    assert isinstance(alts, list)
    assert len(alts) > 0, (
        "an ambiguous single-byte detection should surface rival candidates; "
        f"report={result.report}"
    )
    # The winner is not also listed as one of its own alternatives.
    assert result.report["encoding_used"] not in alts


def test_explicit_encoding_is_not_flagged_low_confidence():
    # When the caller PINS encoding="latin-1", the read is byte-exact and there
    # is nothing ambiguous to flag.
    result = load_table(LATIN1_CSV, encoding="latin-1")
    assert result.report["encoding_detected"] is False
    assert result.report["encoding_low_confidence"] is False
    assert result.report["encoding_alternatives"] == []
    # Byte-exact: the explicit-encoding contract still round-trips exactly.
    assert list(result.df["name"]) == ["Jos\xe9", "Ren\xe9e"]


def test_utf8_auto_detect_is_high_confidence(tmp_path):
    # Contrast case: a clean UTF-8 file is self-validating, so auto-detect is
    # NOT flagged low-confidence. This proves the flag discriminates the
    # ambiguous (single-byte) class from the unambiguous (utf/ascii) one rather
    # than blanket-flagging every sniffed read.
    p = tmp_path / "utf8.csv"
    p.write_bytes("name,city\nJosé,Montréal\n".encode("utf-8"))
    result = load_table(p)
    assert result.report["encoding_detected"] is True
    enc = result.report["encoding_used"].lower().replace("_", "-")
    assert enc.startswith("utf") or enc in ("ascii", "us-ascii")
    assert result.report["encoding_low_confidence"] is False


# ==========================================================================
# 3.1.b'  TEXT-whitelist matches on TOKEN BOUNDARIES, not raw substrings
# (regression: naive substring matching coerced real numeric columns to text
#  because "id"/"case"/"plate"/"ssn" appeared inside "valid", "incident_count",
#  "plateau", "casein", ... which breaks the stats.py consumer that casts the
#  column to float. IMPORTANT 3 extends this: the LONG patterns
#  (zip/phone/account/...) used to keep substring matching, so "microphone_level"
#  / "zipper_count" / "accountability_score" were ALSO mis-coerced. Whole-token
#  matching is now applied UNIFORMLY to every pattern.)
# ==========================================================================

# Names that look ID-ish to a substring matcher but are real (often numeric)
# columns -- these must NOT be coerced to text.
#
# The first group trips the SHORT patterns (id/case/plate/ssn/dob inside
# valid/casein/plateau/...); the second group (IMPORTANT 3) trips the LONG
# patterns under the old substring branch -- "microphone_level" contains
# "phone", "zipper_count" contains "zip", "accountability_score" contains
# "account". Whole-token matching, applied UNIFORMLY, must reject all of them.
NOT_ID_LIKE_NAMES = [
    "valid",
    "valid_flag",
    "incident_count",
    "residual_minutes",
    "candidate_total",
    "raids",
    "rapidity",
    "humid",
    "paid",
    "said",
    "grid",
    "plateau",
    "casein",
    "midpoint",
    # IMPORTANT 3: long-pattern substring false positives.
    "microphone_level",      # contains "phone"
    "zipper_count",          # contains "zip"
    "accountability_score",  # contains "account"
]

# Genuine ID-like names that MUST be coerced to text (leading-zero safety).
ID_LIKE_NAMES = [
    "zip",
    "zip_code",
    "zipcode",
    "fips",
    "case",
    "case_number",
    "caseNumber",
    "Account_ID",
    "account_id",
    "phone_number",
    "ssn",
    "plate",
    "plate_number",
    "dob",
]


@pytest.mark.parametrize("name", NOT_ID_LIKE_NAMES)
def test_token_matcher_does_not_flag_real_numeric_names(name):
    assert _is_id_like(name) is False, (
        f"{name!r} is a real (likely numeric) column and must NOT be coerced "
        f"to text -- token-boundary matching, not raw substring"
    )


@pytest.mark.parametrize("name", ID_LIKE_NAMES)
def test_token_matcher_flags_genuine_id_names(name):
    assert _is_id_like(name) is True, (
        f"{name!r} is a genuine ID-like column and must be coerced to text"
    )


def test_false_positive_numeric_column_stays_numeric_end_to_end(tmp_path):
    # End-to-end proof: an "incident_count" column (substring "id" inside
    # "incident") must load as a NUMBER, not text -- otherwise stats.gini()'s
    # float cast on the column breaks. The pre-fix substring matcher coerced it.
    p = tmp_path / "counts.csv"
    p.write_bytes(
        b"agency,incident_count,candidate_total,raids\n"
        b"PD-1,10,5,3\n"
        b"PD-2,20,7,4\n"
    )
    result = load_table(p)
    assert result.report["text_columns"] == []
    for col in ("incident_count", "candidate_total", "raids"):
        assert pd.api.types.is_numeric_dtype(result.df[col]), (
            f"{col!r} was coerced to text but should stay numeric"
        )


def test_true_positive_id_columns_coerced_end_to_end(tmp_path):
    # The genuine ID columns still load as text and keep leading zeros, via the
    # token matcher (whole-token "case"/"zip"/"phone", camelCase-split
    # "caseNumber"/"Account_ID", separator-split "zip_code"/"phone_number").
    p = tmp_path / "ids.csv"
    p.write_bytes(
        b"zip_code,caseNumber,Account_ID,phone_number,amount\n"
        b"07054,00891,007,0123456789,42\n"
    )
    result = load_table(p)
    for col in ("zip_code", "caseNumber", "Account_ID", "phone_number"):
        assert col in result.report["text_columns"], (
            f"{col!r} should be in the text whitelist"
        )
    assert result.df["zip_code"].iloc[0] == "07054"
    assert result.df["Account_ID"].iloc[0] == "007"
    # The plain numeric "amount" is untouched.
    assert "amount" not in result.report["text_columns"]


def test_long_pattern_substring_false_positives_stay_numeric_end_to_end(tmp_path):
    # IMPORTANT 3 end-to-end: columns that merely CONTAIN a long whitelist
    # pattern -- "microphone_level" (phone), "zipper_count" (zip),
    # "accountability_score" (account) -- must load NUMERIC, not text. The pre-fix
    # substring branch coerced all three, which would break stats.py's float cast.
    p = tmp_path / "long_fp.csv"
    p.write_bytes(
        b"agency,microphone_level,zipper_count,accountability_score\n"
        b"PD-1,3,10,88\n"
        b"PD-2,7,20,91\n"
    )
    result = load_table(p)
    assert result.report["text_columns"] == []
    for col in ("microphone_level", "zipper_count", "accountability_score"):
        assert pd.api.types.is_numeric_dtype(result.df[col]), (
            f"{col!r} was coerced to text but should stay numeric"
        )


def test_single_token_zipcode_and_fips_coerce_end_to_end(tmp_path):
    # Single-word ID columns with no separator to split ("zipcode", "fips") must
    # still match by whole token and keep leading zeros.
    p = tmp_path / "single_tok.csv"
    p.write_bytes(
        b"zipcode,fips,amount\n"
        b"07054,001,42\n"
    )
    result = load_table(p)
    assert "zipcode" in result.report["text_columns"]
    assert "fips" in result.report["text_columns"]
    assert result.df["zipcode"].iloc[0] == "07054"
    assert result.df["fips"].iloc[0] == "001"
    assert "amount" not in result.report["text_columns"]


# ==========================================================================
# 3.1.g  XLSX numeric ID past 2**53 -> precision warning
# (regression: IMPORTANT 4 -- a 17-digit ID stored as an Excel NUMBER is already
#  a rounded double by the time openpyxl yields it; str(int(v)) then stringifies
#  the WRONG number. The loader must WARN rather than present it as exact.)
# ==========================================================================

def test_xlsx_bigint_id_emits_precision_warning():
    result = load_table(BIGINT_ID_XLSX)
    # The whitelisted account_id column triggers the warning.
    assert "account_id" in result.report["text_columns"]
    anomalies = result.report["anomalies"]
    matching = [a for a in anomalies if "account_id" in a and "2^53" in a]
    assert matching, (
        "an XLSX numeric ID past 2**53 must surface a precision warning in "
        f"report['anomalies']; got {anomalies!r}"
    )
    assert "precision" in matching[0].lower()


def test_xlsx_bigint_id_still_stringifies_best_value():
    # We still return a best-effort string (never crash, never drop the column);
    # the warning is the honesty mechanism, not omission.
    result = load_table(BIGINT_ID_XLSX)
    col = result.df["account_id"]
    assert (col.dtype == object) or pd.api.types.is_string_dtype(col)
    # Every cell is a non-empty string of digits.
    assert all(isinstance(v, str) and v.isdigit() for v in col)


def test_xlsx_small_ids_do_not_warn(tmp_path):
    # Contrast: an XLSX whose ID column is comfortably under 2**53 must NOT
    # produce a precision warning -- the flag is specific to the lossy case.
    from openpyxl import Workbook

    p = tmp_path / "small_ids.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["account_id", "amount"])
    ws.append([42, 100])
    ws.append([1001, 200])
    wb.save(p)

    result = load_table(p)
    assert "account_id" in result.report["text_columns"]
    assert not any("2^53" in a for a in result.report["anomalies"])

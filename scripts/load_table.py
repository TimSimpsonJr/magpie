"""Robust dirty-data loader for FOIA CSV / XLSX exports.

FOIA and audit-log exports arrive dirty. A county system writes latin-1, an
upstream Excel round-trip strips the leading zero off every ZIP, empty cells
that mean "missing" come back as ``""``, and a spreadsheet encodes a category
label as a vertical merge that the reader only stores in the top-left cell.
:func:`load_table` turns one such file into a clean :class:`pandas.DataFrame`
plus a structured :class:`LoadResult.report` describing what it had to do.

Design notes (verified against the Phase 3 research gate,
``skills/dataset-analyze/references/prior-art.md``, on the pinned dev venv):

* **Encoding pre-flight** uses ``charset-normalizer`` (already a transitive dep
  of ``requests``) to sniff a byte sample when the caller does not pin an
  encoding. Statistical detection on a short sample can guess a *different*
  single-byte codepage than the true one (e.g. ``cp1250`` for a latin-1 file),
  but any of those reads the bytes without raising; the caller can always pin
  ``encoding=`` for a byte-exact read. The report records both the encoding
  used and whether it was sniffed.
* **TEXT-whitelist.** Columns whose name matches an ID-like pattern (``zip``,
  ``fips``, ``*id*``, ``case``, ``plate``, ``ssn``, ``phone``, ``account``,
  ...) -- or any name the caller passes in ``text_columns`` -- load as strings
  so leading zeros survive (``07054`` stays ``"07054"``, not the int ``7054``).
  On the CSV path this is ``dtype=str`` for those columns; on the XLSX path it
  is post-read coercion (pandas/openpyxl would otherwise int-ify them).
* **``empty_null``.** When True (default), empty-string cells read as missing
  (``NaN``) consistently across both paths. This keeps the downstream
  ``***``-vs-blank-vs-NULL rigor distinction honest (a redaction is present; a
  blank is absent).
* **XLSX merged cells.** Merged regions are *unmerged then filled* from the
  top-left value (openpyxl drops every non-top-left cell on merge), so a
  vertically-merged label populates every row of its region. pandas 3.0.3
  removed ``fillna(method="ffill")`` (it raises ``TypeError``); the fallback
  forward-fill uses ``df.ffill()``.
* **Junk headers.** ``skiprows`` (int or list of 0-based indices) drops
  preamble rows before the real header.
* **Parquet cache.** When ``parquet_cache`` is given, the cleaned frame is
  written to Parquet (via pandas/pyarrow) and the path is noted in the report;
  a follow-up call could read it back.

The loader is pure except for file IO. It is decoupled from ``stats.py`` and
from any corpus-specific loader: its only inputs are a path and options.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

# Column-NAME substrings that mark an ID-like field whose leading zeros must be
# preserved. Matched case-insensitively as substrings, so "ZipCode",
# "case_number", "Account_ID", "phone_number" all match. Kept deliberately
# conservative: a false positive only forces a column to text (safe), a false
# negative risks silently dropping a leading zero (the failure we guard against).
ID_LIKE_PATTERNS: tuple[str, ...] = (
    "zip",
    "zipcode",
    "fips",
    "id",
    "case",
    "plate",
    "ssn",
    "phone",
    "account",
)

# Empty-string spellings that ``empty_null`` collapses to missing. Kept narrow
# on purpose: only a literal empty cell (and surrounding whitespace) is treated
# as absent. Sentinel tokens like "***", "N/A", "NULL" are NOT swept here --
# distinguishing a redaction ("***", a value that exists but is withheld) from a
# genuine blank is a downstream rigor concern, and over-eager NA coercion would
# erase that signal.
_EMPTY_NA_VALUES: tuple[str, ...] = ("", " ")


@dataclass
class LoadResult:
    """The cleaned table plus a structured report of how it was loaded.

    Attributes:
        df: the cleaned :class:`pandas.DataFrame`.
        report: a dict describing the load. Keys:

            * ``encoding`` -- the encoding actually used to read the file.
            * ``encoding_detected`` -- ``True`` if it was sniffed,
              ``False`` if the caller pinned it (or it was a native binary
              read, as for XLSX).
            * ``encoding_confidence`` -- detector confidence in ``[0, 1]`` when
              sniffed, else ``None``.
            * ``text_columns`` -- sorted list of columns forced to text.
            * ``rows_read`` -- number of data rows in ``df``.
            * ``source`` -- ``"csv"`` or ``"xlsx"``.
            * ``parquet_cache`` -- path the cleaned frame was written to, or
              ``None``.
            * ``anomalies`` -- list of human-readable anomaly notes (e.g. a
              row count at the spreadsheet truncation ceiling).
    """

    df: pd.DataFrame
    report: dict[str, Any] = field(default_factory=dict)


# Google-Sheets / common spreadsheet-export row ceiling (2**20 - 1). A load that
# lands exactly here is very likely truncated upstream; flag it (verified
# arithmetic in the research gate: 2**20 - 1 == 1048575).
_TRUNCATION_CEILING = 2**20 - 1


def _normalize_name(name: object) -> str:
    return str(name).strip().lower()


def _is_id_like(column_name: object) -> bool:
    """True if a column NAME matches any ID-like substring pattern."""
    norm = _normalize_name(column_name)
    return any(pat in norm for pat in ID_LIKE_PATTERNS)


def _resolve_text_columns(
    columns: Iterable[object],
    explicit: Iterable[str] | None,
) -> list[str]:
    """Columns to force to text: ID-like names plus caller-named ones.

    Matching is by normalized (stripped, lowercased) name so a caller can pass
    ``"Zip"`` and have it match a ``" zip "`` header. Returns the ORIGINAL
    column labels (as they appear in the frame), sorted for a stable report.
    """
    explicit_norm = {_normalize_name(c) for c in (explicit or ())}
    chosen = [
        col
        for col in columns
        if _is_id_like(col) or _normalize_name(col) in explicit_norm
    ]
    return sorted(chosen, key=lambda c: str(c))


def _detect_encoding(path: Path, sample_size: int = 65_536) -> tuple[str, float | None]:
    """Sniff a file's encoding from a byte sample.

    Reads up to ``sample_size`` bytes and runs ``charset-normalizer``. Returns
    ``(encoding, confidence)``. Falls back to ``utf-8`` when the sample is empty
    or the detector returns nothing. ``confidence`` is ``1 - chaos`` from the
    best match (``None`` on fallback).
    """
    raw = path.read_bytes()[:sample_size]
    if not raw:
        return "utf-8", None
    # Imported lazily so importing this module does not hard-require the
    # detector unless an auto-detect load actually runs.
    from charset_normalizer import from_bytes

    best = from_bytes(raw).best()
    if best is None or not best.encoding:
        return "utf-8", None
    # charset-normalizer exposes "chaos" (lower is better); turn it into a
    # rough confidence in [0, 1] for the report.
    confidence = None
    chaos = getattr(best, "chaos", None)
    if isinstance(chaos, (int, float)):
        confidence = max(0.0, min(1.0, 1.0 - float(chaos)))
    return best.encoding, confidence


def _coerce_text_columns(df: pd.DataFrame, text_columns: list[str]) -> None:
    """In-place: cast the given columns to leading-zero-safe strings.

    Used on the XLSX path (and as a belt-and-suspenders pass on CSV). A value
    that pandas already read as a float ``7054.0`` is rendered back to the
    integer-looking ``"7054"`` (not ``"7054.0"``); genuine missing cells stay
    missing rather than becoming the literal string ``"nan"``.
    """
    for col in text_columns:
        if col not in df.columns:
            continue
        series = df[col]
        na_mask = series.isna()
        # Numeric whole-number floats -> int-looking strings (drop the ".0").
        if pd.api.types.is_float_dtype(series):
            as_str = series.map(
                lambda v: "" if pd.isna(v) else (
                    str(int(v)) if float(v).is_integer() else str(v)
                )
            )
        else:
            as_str = series.astype("string").astype(object)
        as_str = as_str.where(~na_mask, other=pd.NA)
        df[col] = as_str.astype(object)
        # Restore NA sentinels as actual NaN/None for consistency.
        df.loc[na_mask, col] = None


def _apply_empty_null(df: pd.DataFrame) -> None:
    """In-place: turn empty / whitespace-only string cells into ``None``."""
    for col in df.columns:
        series = df[col]
        if series.dtype != object and not pd.api.types.is_string_dtype(series):
            continue
        stripped_is_empty = series.map(
            lambda v: isinstance(v, str) and v.strip() == ""
        )
        if stripped_is_empty.any():
            df.loc[stripped_is_empty, col] = None


def _detect_anomalies(df: pd.DataFrame) -> list[str]:
    """Cheap, non-fatal anomaly notes for the report."""
    anomalies: list[str] = []
    n = len(df)
    if n == _TRUNCATION_CEILING:
        anomalies.append(
            f"row count is exactly the spreadsheet export ceiling "
            f"({_TRUNCATION_CEILING}); data is likely truncated upstream -- "
            f"request the gap"
        )
    # Columns that are entirely null after cleaning are worth surfacing.
    for col in df.columns:
        if len(df) and df[col].isna().all():
            anomalies.append(f"column {col!r} is entirely null")
    return anomalies


def _load_csv(
    path: Path,
    *,
    encoding: str | None,
    text_columns: list[str] | None,
    empty_null: bool,
    skiprows: int | list[int] | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """CSV path: encoding pre-flight, then a single typed ``read_csv``.

    The text-whitelist is resolved in two passes because we cannot know the
    column names until we have read the header. First read the header row to
    learn the columns (cheap), resolve which are ID-like / caller-named, then
    read for real with ``dtype=str`` on exactly those columns.
    """
    detected = False
    confidence: float | None = None
    used_encoding = encoding
    if used_encoding is None:
        used_encoding, confidence = _detect_encoding(path)
        detected = True

    # Pass 1: read just the header (nrows=0) to learn the column names, honoring
    # skiprows so junk preamble does not masquerade as the header.
    header_df = pd.read_csv(
        path,
        encoding=used_encoding,
        skiprows=skiprows,
        nrows=0,
    )
    resolved_text = _resolve_text_columns(header_df.columns, text_columns)

    # Pass 2: the real read. dtype=str on the whitelisted columns preserves
    # leading zeros. empty_null is implemented via na_values=[""] +
    # keep_default_na so an empty cell becomes NaN even inside a dtype=str
    # column; with empty_null=False we keep "" literally in str columns.
    dtype = {col: str for col in resolved_text}
    read_kwargs: dict[str, Any] = {
        "encoding": used_encoding,
        "skiprows": skiprows,
        "dtype": dtype,
    }
    if empty_null:
        # Treat empty / whitespace cells as NA everywhere (including str cols).
        read_kwargs["na_values"] = list(_EMPTY_NA_VALUES)
        read_kwargs["keep_default_na"] = True
    else:
        # Opt out of ""->NA: do not add "" to na_values, and stop pandas from
        # treating its default empty-string NA token inside str columns.
        read_kwargs["keep_default_na"] = True
        read_kwargs["na_values"] = []
        # With keep_default_na True, pandas still maps "" -> NaN for object
        # columns by default; force the str columns to retain "". We post-fix
        # below rather than fight read_csv's NA machinery.

    df = pd.read_csv(path, **read_kwargs)

    if not empty_null:
        # Restore literal "" in the whitelisted text columns (read_csv may have
        # NA'd them). Non-text columns are left as pandas read them.
        df = _restore_empty_strings_for_text(path, df, resolved_text, used_encoding, skiprows)

    report = {
        "source": "csv",
        "encoding": used_encoding,
        "encoding_detected": detected,
        "encoding_confidence": confidence,
        "text_columns": resolved_text,
    }
    return df, report


def _restore_empty_strings_for_text(
    path: Path,
    df: pd.DataFrame,
    text_columns: list[str],
    encoding: str,
    skiprows: int | list[int] | None,
) -> pd.DataFrame:
    """For ``empty_null=False``: put literal ``""`` back in text columns.

    ``read_csv`` collapses ``""`` to ``NaN`` for object columns even with
    ``na_values=[]``. To honor ``empty_null=False`` we re-read with the C
    parser's ``na_filter=False`` (which disables ALL NA conversion) and copy the
    text columns across, leaving the numeric columns from the primary read
    untouched.
    """
    raw = pd.read_csv(
        path,
        encoding=encoding,
        skiprows=skiprows,
        dtype={col: str for col in text_columns},
        na_filter=False,
    )
    for col in text_columns:
        if col in raw.columns and col in df.columns:
            df[col] = raw[col].astype(object)
    return df


def _unmerge_and_fill(path: Path):
    """Load an XLSX with merged regions unmerged and filled from the top-left.

    Returns an in-memory workbook bytes buffer ready for ``pd.read_excel``.
    openpyxl drops every non-top-left cell value when a range is merged; this
    reads the top-left, unmerges, and writes that value into every cell of the
    former range -- handling vertical and horizontal merges and merged headers.
    """
    import io

    from openpyxl import load_workbook

    wb = load_workbook(path)
    for ws in wb.worksheets:
        # Copy to a list first: unmerge_cells mutates the live ranges set.
        for mr in list(ws.merged_cells.ranges):
            min_col, min_row, max_col, max_row = mr.bounds  # (col, row) order
            top_left = ws.cell(row=min_row, column=min_col).value
            ws.unmerge_cells(str(mr))
            for r in range(min_row, max_row + 1):
                for c in range(min_col, max_col + 1):
                    ws.cell(row=r, column=c).value = top_left
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _load_xlsx(
    path: Path,
    *,
    text_columns: list[str] | None,
    empty_null: bool,
    skiprows: int | list[int] | None,
    sheet_name: int | str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """XLSX path: unmerge+fill, read with openpyxl, then post-read coercion.

    The text-whitelist is applied as a post-read coercion (pandas/openpyxl would
    int-ify a leading-zero column), and ``empty_null`` as a post-read sweep. The
    deterministic unmerge pass (rather than a bare ``df.ffill()``) is preferred
    because it also fixes merged *headers* and horizontal merges; a plain ffill
    would only handle the "category merged down one column" case.
    """
    buf = _unmerge_and_fill(path)
    df = pd.read_excel(
        buf,
        sheet_name=sheet_name,
        engine="openpyxl",
        skiprows=skiprows,
        dtype=object,  # read everything raw; we coerce the whitelist ourselves
    )

    resolved_text = _resolve_text_columns(df.columns, text_columns)
    _coerce_text_columns(df, resolved_text)

    if empty_null:
        _apply_empty_null(df)

    report = {
        "source": "xlsx",
        "encoding": "binary",  # XLSX is a zip container; no text encoding sniff
        "encoding_detected": False,
        "encoding_confidence": None,
        "text_columns": resolved_text,
    }
    return df, report


def load_table(
    path,
    *,
    encoding: str | None = None,
    text_columns: list[str] | None = None,
    empty_null: bool = True,
    skiprows: int | list[int] | None = None,
    sheet_name: int | str = 0,
    forward_fill_columns: list[str] | None = None,
    parquet_cache=None,
) -> LoadResult:
    """Load a dirty CSV/XLSX into a clean DataFrame plus a load report.

    Args:
        path: path to a ``.csv`` / ``.tsv`` / ``.xlsx`` / ``.xls`` file. The
            suffix selects the path (CSV vs XLSX); unknown suffixes are treated
            as CSV.
        encoding: when ``None`` (default), the CSV encoding is sniffed from a
            byte sample and recorded; pass e.g. ``"latin-1"`` for a byte-exact
            read. Ignored for XLSX (a binary container).
        text_columns: extra column names to force to text beyond the ID-like
            auto-whitelist (matched case-insensitively by normalized name).
        empty_null: when ``True`` (default), empty-string cells become missing
            (``NaN``) consistently; ``False`` keeps literal ``""`` in text
            columns.
        skiprows: int or list of 0-based row indices to drop before the header
            (junk preamble rows).
        sheet_name: XLSX sheet index or name (default first sheet).
        forward_fill_columns: column names to forward-fill (``df.ffill()``)
            after cleaning -- the quick path for a "category label runs down a
            column, blank means same-as-above" layout, e.g. a CSV exported from
            a report that left the repeated label blank. Applied AFTER
            ``empty_null`` so blanks-turned-NaN are carried down. (The XLSX path
            already unmerges+fills merged regions deterministically; this is for
            the cases that arrive as plain blanks rather than true merges.)
        parquet_cache: when given, the cleaned frame is written to this path as
            Parquet and the path is noted in the report.

    Returns:
        A :class:`LoadResult` with ``.df`` and ``.report``.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in (".xlsx", ".xlsm", ".xls"):
        df, report = _load_xlsx(
            path,
            text_columns=text_columns,
            empty_null=empty_null,
            skiprows=skiprows,
            sheet_name=sheet_name,
        )
    else:
        df, report = _load_csv(
            path,
            encoding=encoding,
            text_columns=text_columns,
            empty_null=empty_null,
            skiprows=skiprows,
        )

    df = df.reset_index(drop=True)

    # Forward-fill named label columns (the quick path for blank-means-repeat
    # layouts). pandas 3.0.3 removed fillna(method="ffill") -- it raises
    # TypeError -- so use the dedicated df[col].ffill(). Runs after empty_null so
    # "" has already become NaN and gets carried down.
    filled: list[str] = []
    for col in forward_fill_columns or ():
        if col in df.columns:
            df[col] = df[col].ffill()
            filled.append(col)
    report["forward_filled_columns"] = sorted(filled, key=str)

    report["rows_read"] = len(df)
    report["anomalies"] = _detect_anomalies(df)
    report["parquet_cache"] = None

    if parquet_cache is not None:
        cache_path = Path(parquet_cache)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # pandas -> pyarrow engine; preserves the text dtype of ID columns.
        df.to_parquet(cache_path, engine="pyarrow", index=False)
        report["parquet_cache"] = str(cache_path)

    return LoadResult(df=df, report=report)

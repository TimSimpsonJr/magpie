"""Truncation + data-quality gate for FOIA / audit-log datasets.

FOIA exports are routinely incomplete in two ways that quietly invalidate a
finding:

1. **Silent truncation.** Google Sheets and many CSV exporters stop at
   ``2**20 - 1 == 1,048,575`` data rows (``2**20`` rows minus one header). A
   delivery that lands EXACTLY on that ceiling was almost certainly clipped
   upstream; the rows past the cap simply never came.
2. **A narrower-than-requested window.** You ask for Jan-Dec; the agency ships
   Mar-Oct. The head and/or tail of the requested period is missing, so any
   "activity dropped to zero in February" reading is an artifact of the gap.

This module is the rigor guardrail that catches both before an analyst
publishes on incomplete data. Like the Simpsonville-pilot tooling it sits
beside, it is GENERIC and standalone: every function takes plain values (an int
row-count, or a :class:`pandas.DataFrame` plus column names) and is pure and
deterministic (pandas + numpy only -- no file IO, no clock, no randomness, no
network). It is deliberately decoupled from ``stats.py``, ``load_table.py``,
and any corpus-specific loader, so it can gate a frame from any source.

A guiding principle, shared with the analyze-tables work: these checks emit
LEADS, not verdicts. :func:`analyze_anomalies` surfaces "this column is 95%
null" or "this looks like a dirty numeric column" so a human can investigate; it
never silently drops or rewrites data. The one high-confidence assertion is the
EXACT truncation match, and even that recommends an action (request the gap)
rather than mutating anything. Counts merely *near* the ceiling are intentionally
NOT auto-flagged here -- a 1,048,000-row file is far likelier to be genuine than
clipped, and a false truncation alarm would itself be a rigor failure.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# Google-Sheets / common CSV-export row ceiling: 2**20 rows minus one header
# row. A delivery landing EXACTLY here is the high-confidence truncation signal.
# (Arithmetic anchored in the test-suite: 2**20 - 1 == 1,048,575.)
DEFAULT_TRUNCATION_CEILING: int = 2**20 - 1

# A column at or above this null fraction is surfaced as a lead. 0.90 == "more
# than 90% of the cells are missing", i.e. the field is too sparse to trust
# without checking why.
_HIGH_NULL_THRESHOLD: float = 0.90

# An object column that parses as numeric for at least this fraction of its
# NON-NULL cells -- but not ALL of them -- is flagged as a likely dirty numeric
# column (mostly numbers with a little junk). Below this bar it reads as genuine
# free text and is left alone.
_NUMERIC_COERCION_THRESHOLD: float = 0.80


def check_truncation(
    rows: int | pd.DataFrame,
    *,
    ceiling: int = DEFAULT_TRUNCATION_CEILING,
) -> dict[str, Any]:
    """Flag a row count that lands EXACTLY on a spreadsheet/export ceiling.

    ``rows`` is either an integer row-count or a :class:`pandas.DataFrame` (in
    which case ``len(df)`` is used). The default ``ceiling`` is the Google-Sheets
    / CSV-export cap ``2**20 - 1 == 1,048,575`` (``2**20`` rows minus one header
    row); pass ``ceiling=`` to check a different cap (e.g. the legacy ``.xls``
    limit of ``65,536``).

    Only an EXACT match (``n_rows == ceiling``) is treated as the
    high-confidence truncation signal. This is deliberate: a delivery clipped by
    an exporter stops at precisely the cap, so an exact hit is a strong tell,
    whereas a count merely *near but below* the ceiling is far more likely to be
    a genuine dataset than a clipped one. Near-ceiling counts are therefore NOT
    auto-flagged here -- raising a false truncation alarm would itself be a rigor
    failure. (A human who wants to scrutinize a suspiciously-round near-ceiling
    count can still do so; this gate just won't assert it for them.)

    Returns a dict::

        {"truncated": bool, "n_rows": int, "ceiling": int, "message": str}

    When ``truncated`` is True the ``message`` recommends *request-the-gap*: go
    back to the source for a weekly / native / database export that sidesteps the
    ``2**20`` row cap, rather than analyzing the clipped file.
    """
    n_rows = len(rows) if isinstance(rows, pd.DataFrame) else int(rows)
    truncated = n_rows == ceiling
    if truncated:
        message = (
            f"row count is EXACTLY the export ceiling ({ceiling:,}); the data is "
            f"almost certainly truncated upstream. Request the gap: re-request a "
            f"weekly / native / database export to dodge the 2^20 row cap rather "
            f"than publishing on the clipped file."
        )
    else:
        message = (
            f"row count {n_rows:,} is not at the export ceiling ({ceiling:,}); "
            f"no exact-match truncation signal. (Counts near but below the "
            f"ceiling are not auto-flagged -- inspect manually if a clip is "
            f"suspected.)"
        )
    return {
        "truncated": bool(truncated),
        "n_rows": int(n_rows),
        "ceiling": int(ceiling),
        "message": message,
    }


def _coerce_bound(value: Any) -> pd.Timestamp | None:
    """Normalize a date-like bound to a :class:`pandas.Timestamp`, or ``None``.

    Accepts anything ``pd.Timestamp`` understands (an ISO string, a
    ``datetime``, an existing ``Timestamp``). ``None`` passes through; an
    unparseable value becomes ``None`` rather than raising, so a sloppy caller
    bound can never crash the gate.
    """
    if value is None:
        return None
    ts = pd.Timestamp(value) if not isinstance(value, pd.Timestamp) else value
    return None if pd.isna(ts) else ts


def check_date_window(
    df: pd.DataFrame,
    date_col: str,
    *,
    requested_start: Any = None,
    requested_end: Any = None,
) -> dict[str, Any]:
    """Flag a delivery whose date span is NARROWER than the requested window.

    ``df[date_col]`` is coerced to datetime with ``pd.to_datetime(...,
    errors="coerce")`` so unparseable entries become ``NaT`` (and are counted,
    not silently dropped). The actual span is the min/max of the parseable dates.

    If ``requested_start`` / ``requested_end`` are given (any date-like value),
    the delivery is flagged where it falls SHORT of the request:

    * ``missing_head`` -- the earliest delivered date is later than
      ``requested_start`` (the front of the requested window is absent).
    * ``missing_tail`` -- the latest delivered date is earlier than
      ``requested_end`` (the back of the requested window is absent).

    The comparison is strictly *narrower-than*: an actual bound landing exactly
    on the requested bound is full coverage, not a gap. A delivery WIDER than the
    request (extra data on either end) is fine and never flagged.

    An all-``NaT`` / empty column is handled gracefully: ``actual_start`` /
    ``actual_end`` are ``None`` and neither head nor tail is reported missing
    (you cannot assert a gap when there are no dates to compare).

    Returns a dict::

        {"actual_start", "actual_end", "requested_start", "requested_end",
         "missing_head": bool, "missing_tail": bool, "n_undated": int}

    The ``*_start`` / ``*_end`` values are :class:`pandas.Timestamp` (or
    ``None``); ``n_undated`` is the count of rows whose date would not parse.
    """
    # format="mixed" parses each element individually (the robust path for a
    # heterogeneous real-world FOIA date column) and, by making that intent
    # explicit, suppresses pandas' "could not infer format, falling back to
    # dateutil" UserWarning that an all-/partly-unparseable column would emit.
    parsed = pd.to_datetime(df[date_col], errors="coerce", format="mixed")
    n_undated = int(parsed.isna().sum())

    valid = parsed.dropna()
    actual_start = valid.min() if not valid.empty else None
    actual_end = valid.max() if not valid.empty else None

    req_start = _coerce_bound(requested_start)
    req_end = _coerce_bound(requested_end)

    # A gap can only be asserted when we have BOTH a requested bound and an actual
    # bound to compare it against; otherwise there is nothing to be narrower-than.
    missing_head = (
        actual_start is not None
        and req_start is not None
        and actual_start > req_start
    )
    missing_tail = (
        actual_end is not None
        and req_end is not None
        and actual_end < req_end
    )

    return {
        "actual_start": actual_start,
        "actual_end": actual_end,
        "requested_start": req_start,
        "requested_end": req_end,
        "missing_head": bool(missing_head),
        "missing_tail": bool(missing_tail),
        "n_undated": n_undated,
    }


def _is_blank_scalar(value: Any) -> bool:
    """True for an empty / whitespace-only string cell (an information-free blank).

    A pandas null is NOT a blank here (it is handled by the null check); only an
    actual ``str`` that strips to ``""`` counts, so this distinguishes an
    all-blank ``""`` column from an all-``NaN`` one.
    """
    return isinstance(value, str) and value.strip() == ""


def analyze_anomalies(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Per-column descriptive LEADS for a data-quality review (never verdicts).

    Walks each column and emits warnings a human should look at, in the spirit of
    an analyze-tables pass. It DESCRIBES; it never drops or rewrites data. The
    kinds emitted:

    * ``all_null`` -- every cell is missing (the column carried no data).
    * ``all_blank`` -- every cell is an empty / whitespace string (present to a
      naive null check, but information-free).
    * ``high_null`` -- more than 90% of cells are missing (``null_pct`` in the
      detail); the field is too sparse to lean on without knowing why.
    * ``type_coercion_mismatch`` -- an ``object`` column that parses as numeric
      for >80% of its non-null cells but NOT all of them: a likely dirty numeric
      column (mostly numbers with a little junk). The detail reports how many
      cells failed to parse, so a reader can decide whether to clean or discard.

    Each warning is ``{"column": str, "kind": str, "detail": str}``. A clean,
    well-typed column produces nothing. The order is column order, with a
    column's own warnings in the order checked.

    Returns a list of warning dicts (possibly empty).
    """
    warnings: list[dict[str, Any]] = []
    n = len(df)
    if n == 0:
        return warnings

    for col in df.columns:
        series = df[col]
        null_mask = series.isna()
        n_null = int(null_mask.sum())

        # All-null: the strongest "this column is empty" signal.
        if n_null == n:
            warnings.append(
                {
                    "column": col,
                    "kind": "all_null",
                    "detail": f"every one of {n} cells is null; the column carried no data",
                }
            )
            continue

        # All-blank: non-null cells exist but they are all empty/whitespace
        # strings -- present to a null check, yet information-free.
        non_null = series[~null_mask]
        if len(non_null) > 0 and non_null.map(_is_blank_scalar).all():
            warnings.append(
                {
                    "column": col,
                    "kind": "all_blank",
                    "detail": (
                        f"all {len(non_null)} non-null cells are empty/whitespace "
                        f"strings; the column is present but information-free"
                    ),
                }
            )
            continue

        # High-null: sparse enough to be a lead (but not entirely empty).
        null_pct = n_null / n
        if null_pct > _HIGH_NULL_THRESHOLD:
            warnings.append(
                {
                    "column": col,
                    "kind": "high_null",
                    "detail": (
                        f"null_pct={null_pct:.0%} ({n_null} of {n} cells missing); "
                        f"the field is too sparse to trust without investigation"
                    ),
                }
            )

        # Type-coercion mismatch: a dirty numeric column. Only meaningful for
        # object/string dtype (a real numeric column is already typed); a column
        # that is mostly-but-not-entirely numeric-parseable is the dirty case.
        if _is_object_like(series):
            mismatch = _numeric_coercion_mismatch(non_null)
            if mismatch is not None:
                n_unparseable, n_considered = mismatch
                warnings.append(
                    {
                        "column": col,
                        "kind": "type_coercion_mismatch",
                        "detail": (
                            f"{n_considered - n_unparseable} of {n_considered} "
                            f"non-null cells parse as numeric but {n_unparseable} "
                            f"do not; likely a dirty numeric column (numbers with "
                            f"some non-numeric junk)"
                        ),
                    }
                )

    return warnings


def _is_object_like(series: pd.Series) -> bool:
    """True if a Series holds Python objects / strings (not a native numeric dtype).

    The numeric-coercion lead only applies to a column whose dtype did NOT
    already resolve to a number; a genuine ``int64`` / ``float64`` column has
    nothing to coerce.
    """
    return series.dtype == object or pd.api.types.is_string_dtype(series)


def _numeric_coercion_mismatch(non_null: pd.Series) -> tuple[int, int] | None:
    """Detect a mostly-numeric-with-some-junk object column.

    Given the NON-NULL cells of an object column, returns
    ``(n_unparseable, n_considered)`` when the column parses as numeric for
    ``> _NUMERIC_COERCION_THRESHOLD`` of its cells but NOT all of them (the dirty
    mixture worth flagging), else ``None``.

    ``None`` is returned when the column is empty, fully numeric-parseable (no
    mismatch -- nothing dirty), or mostly non-numeric (genuine free text, below
    the bar).
    """
    n_considered = len(non_null)
    if n_considered == 0:
        return None

    coerced = pd.to_numeric(non_null, errors="coerce")
    n_unparseable = int(coerced.isna().sum())
    n_parseable = n_considered - n_unparseable

    # Nothing failed -> not a mismatch (the column is cleanly numeric-looking).
    if n_unparseable == 0:
        return None

    parse_rate = n_parseable / n_considered
    if parse_rate > _NUMERIC_COERCION_THRESHOLD:
        return n_unparseable, n_considered
    return None


def data_quality_report(
    df: pd.DataFrame,
    *,
    date_col: str | None = None,
    requested_start: Any = None,
    requested_end: Any = None,
) -> dict[str, Any]:
    """Run every gate on one frame and return a combined report.

    Convenience aggregator that bundles:

    * ``truncation`` -- :func:`check_truncation` on the frame's row count.
    * ``date_window`` -- :func:`check_date_window` when ``date_col`` is given,
      else ``None``.
    * ``anomalies`` -- :func:`analyze_anomalies` (the per-column lead list).

    Returns ``{"truncation": dict, "date_window": dict | None, "anomalies":
    list}``. Nothing is mutated; the frame is only read.
    """
    return {
        "truncation": check_truncation(df),
        "date_window": (
            check_date_window(
                df,
                date_col,
                requested_start=requested_start,
                requested_end=requested_end,
            )
            if date_col is not None
            else None
        ),
        "anomalies": analyze_anomalies(df),
    }

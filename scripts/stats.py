"""Flagship statistics for investigative FOIA / audit-log analysis.

Every function here is GENERIC and standalone. Inputs are plain values: a
sequence of numbers, or a :class:`pandas.DataFrame` plus column names. Nothing
is coupled to a specific corpus loader. The functions are pure and
deterministic (numpy + pandas only) so they can be golden-tested against
documented values and reused across data sources.

The toolkit grew out of a surveillance-network audit (the "Simpsonville
pilot"), and the docstrings reference that motivating case, but the math is
domain-agnostic: feed it searches-per-agency, requests-per-user,
pulls-per-incident, or any comparable magnitudes.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _drop_nulls_to_float_array(values) -> np.ndarray:
    """Magnitudes as a float ``ndarray`` with ALL pandas nulls dropped.

    Filters every pandas missing sentinel -- ``None``, ``float('nan')``,
    ``np.nan``, ``pd.NA``, ``NaT`` -- via :func:`pandas.isna`, which recognizes
    all of them, BEFORE the float cast. This is load-bearing for the real
    derive -> stats pipeline: ``derive_nets`` emits a nullable ``Int64`` whose
    missing cells are ``pd.NA``, and a raw ``np.asarray(..., dtype=float)`` would
    either raise on ``pd.NA`` (it cannot be floated) or let ``np.nan`` through
    and poison the result with ``nan``. Dropping the nulls first keeps the
    statistic well-defined over the present values only.

    Accepts any iterable (a list, a tuple, a numpy array, an ``Int64`` extension
    array, or a :class:`pandas.Series`). ``pd.isna`` is applied per scalar, so a
    plain Python ``None`` and a numpy ``nan`` are handled uniformly. Returns a
    1-D ``float64`` array (possibly empty).
    """
    return np.asarray([v for v in values if not pd.isna(v)], dtype=float)


def gini(values):
    """Gini coefficient of non-negative magnitudes (e.g., searches per agency).

    Returns a float in ``[0, 1]``: ``0.0`` is perfect equality (everyone the
    same), approaching ``1.0`` is total concentration (one actor holds it all).
    Pandas null sentinels (``None``, ``NaN``, ``pd.NA``, ``NaT``) are dropped --
    so a nullable ``Int64`` column straight from ``derive_nets`` is safe and a
    stray ``np.nan`` cannot poison the result into ``nan``. An empty or all-zero
    input returns ``0.0``.

    Negative magnitudes are rejected with :class:`ValueError`. The Gini formula
    here assumes non-negative magnitudes; a negative value silently yields an
    out-of-``[0, 1]`` result (e.g. ``gini([-5, 0, 5, 10])`` would be ``1.25``)
    and the all-zero guard can mask real dispersion (e.g. ``[-10, -10, 10, 10]``
    sums to zero). Since these figures get published, fail loudly instead.

    Example:
        >>> gini([5, 5, 5, 5])
        0.0
    """
    x = np.sort(_drop_nulls_to_float_array(values))
    if x.size and x[0] < 0:
        raise ValueError(
            f"gini expects non-negative magnitudes; got negative value {x[0]:g}"
        )
    n = x.size
    if n == 0 or x.sum() == 0:
        return 0.0
    cum = np.cumsum(x)
    return float((2 * np.sum((np.arange(1, n + 1)) * x) - (n + 1) * cum[-1]) / (n * cum[-1]))


def top_k_share(counts, k_frac=0.01):
    """Fraction of total volume held by the top ``k_frac`` of actors.

    Actors are ranked by magnitude (descending). ``k = max(1, ceil(N * k_frac))``
    so at least one actor is always counted. Returns ``sum(top k) / total``.

    In the pilot, the top 1% of agencies accounted for ~27% of all network
    searches. Pandas null sentinels (``None``, ``NaN``, ``pd.NA``, ``NaT``) are
    dropped; empty / all-zero input returns ``0.0``.
    """
    x = np.sort(_drop_nulls_to_float_array(counts))[::-1]
    n = x.size
    total = x.sum()
    if n == 0 or total == 0:
        return 0.0
    k = max(1, math.ceil(n * k_frac))
    return float(x[:k].sum() / total)


def bottom_half_share(counts):
    """Fraction of total volume held by the smallest 50% of actors.

    Actors are ranked by magnitude (ascending); the bottom ``N // 2`` are
    summed and divided by the total. In the pilot, the bottom half of agencies
    accounted for only ~2.5% of all searches (the long tail barely registers
    against a few heavy users). Pandas null sentinels (``None``, ``NaN``,
    ``pd.NA``, ``NaT``) are dropped; empty / all-zero input returns ``0.0``.
    """
    x = np.sort(_drop_nulls_to_float_array(counts))
    n = x.size
    total = x.sum()
    if n == 0 or total == 0:
        return 0.0
    half = n // 2
    return float(x[:half].sum() / total)


def median_by_category(df, value_col, category_col):
    """Median of ``value_col`` per ``category_col``, sorted high to low.

    Returns a :class:`pandas.Series` indexed by category, descending by median.

    Motivating finding: with ``value_col`` as the surveillance net width per
    incident and ``category_col`` as the stated reason, the *Traffic* median
    sits above the *Homicide* median, i.e. routine traffic stops cast a wider
    net than homicide investigations.
    """
    return df.groupby(category_col)[value_col].median().sort_values(ascending=False)


def automation_signature(
    df,
    hour_col,
    agency_col,
    day_start=6,
    day_end=18,
    overnight_threshold=0.5,
):
    """Descriptive per-actor day/overnight activity split (a LEAD, not a verdict).

    ``hour_col`` holds integer hours (0-23). For each actor in ``agency_col``:

    * ``daytime_pct``    = share of rows with ``day_start <= hour < day_end``
    * ``overnight_pct``  = share with ``hour < day_start`` or ``hour >= day_end``
    * ``overnight_heavy`` = ``overnight_pct > overnight_threshold``

    The day interval is half-open ``[day_start, day_end)``, so ``day_end`` itself
    counts as overnight.

    TIMEZONE CONFOUND -- read before treating this as a behavior signal. The
    ``hour_col`` is derived in a SINGLE timezone, so "overnight" is defined
    relative to that one clock. An actor working ORDINARY business hours in
    another timezone (e.g. Central or Pacific against an Eastern hour column)
    lands partly outside ``[day_start, day_end)`` and can show a high overnight
    share by GEOGRAPHY, not behavior. ``overnight_heavy`` is therefore a LEAD to
    inspect -- which actors to look at next -- and NOT an automation verdict.
    (Same-second burstiness, computed elsewhere, is the timezone-independent
    automation tell.)

    Rows whose ``hour`` is null/NaN or outside ``0..23`` are DROPPED before each
    actor's shares are computed, so missing or garbage timestamps can never
    inflate ``overnight_pct`` or trip ``overnight_heavy`` (a rigor guardrail: a
    dirty hour must not manufacture a signal). An actor whose rows are *all*
    invalid still appears in the output but with
    ``daytime_pct == overnight_pct == 0.0`` and ``overnight_heavy == False``.

    Returns a :class:`pandas.DataFrame` indexed by actor with columns
    ``daytime_pct``, ``overnight_pct``, ``overnight_heavy``.
    """
    agencies = pd.Series(df[agency_col].values, name="agency")
    hours = pd.to_numeric(df[hour_col], errors="coerce").to_numpy()
    valid = (hours >= 0) & (hours <= 23)  # False for NaN and out-of-range
    is_daytime = valid & (hours >= day_start) & (hours < day_end)

    work = pd.DataFrame(
        {"agency": agencies.values, "valid": valid, "daytime": is_daytime}
    )
    grouped = work.groupby("agency")
    valid_count = grouped["valid"].sum()
    daytime_count = grouped["daytime"].sum()

    # Shares are over VALID rows only; agencies with zero valid rows -> 0.0/0.0.
    daytime_pct = (daytime_count / valid_count).where(valid_count > 0, 0.0)
    overnight_pct = ((valid_count - daytime_count) / valid_count).where(
        valid_count > 0, 0.0
    )
    result = pd.DataFrame(
        {
            "daytime_pct": daytime_pct,
            "overnight_pct": overnight_pct,
            "overnight_heavy": overnight_pct > overnight_threshold,
        }
    )
    result.index.name = agency_col
    return result


def burstiness(df, timestamp_col, agency_col=None):
    """Detect same-second batches (an automated-submission tell).

    Rows are grouped by ``timestamp_col`` (and ``agency_col`` when given) and
    counted. A genuine human generates mostly singleton seconds; a script can
    fire many rows in the same second.

    Returns a dict:

    * ``max_same_second`` -- the largest single-group row count (int)
    * ``size_distribution`` -- ``{batch_size: number_of_groups}`` (dict)

    Pass ``agency_col`` to attribute bursts per actor; omit it to count raw
    same-second collisions across all actors.
    """
    keys = [timestamp_col] if agency_col is None else [timestamp_col, agency_col]
    group_sizes = df.groupby(keys).size()
    if group_sizes.empty:
        return {"max_same_second": 0, "size_distribution": {}}
    size_counts = group_sizes.value_counts()
    size_distribution = {int(size): int(count) for size, count in size_counts.items()}
    return {
        "max_same_second": int(group_sizes.max()),
        "size_distribution": size_distribution,
    }


def presence_rate(series, present_predicate):
    """Fraction of ``series`` for which ``present_predicate(value)`` is True.

    The motivating distinction is presence-of-a-value versus the value itself.
    A redacted ``***`` means a value EXISTS but is withheld -- it is PRESENT.
    A blank / ``None`` / ``""`` means a value is genuinely ABSENT. So with a
    predicate that treats "non-empty" as present, ``***`` counts toward the 3
    present values (CASE123, ``***``, C-9) while ``""`` and ``None`` are the 2
    absent ones -- conflating ``***`` with blank would undercount how often a
    field was actually filled in:

        >>> non_empty = lambda v: str(v).strip() != ""
        >>> presence_rate(["CASE123", "***", "", None, "C-9"], non_empty)
        0.6

    Accepts any iterable; returns ``0.0`` for an empty input.

    Pandas null sentinels (``None``, ``NaN``, ``pd.NA``, ``NaT``) are treated
    as ABSENT and are never handed to the predicate. This matters: a pandas
    string Series silently turns ``None`` into the float ``NaN`` (whose
    ``str()`` is the non-empty string ``"nan"``), so a naive predicate would
    miscount a genuinely missing field as present. Normalizing nulls here keeps
    the ``***``-versus-blank distinction robust no matter how the caller's
    framework spelled "missing".
    """
    values = list(series)
    if not values:
        return 0.0
    present = sum(
        1 for v in values if not _is_null_scalar(v) and present_predicate(v)
    )
    return present / len(values)


def _is_null_scalar(value):
    """True for scalar pandas missing sentinels (None / NaN / NA / NaT).

    Array-likes are treated as non-null: :func:`pandas.isna` returns an array
    for them, which is not a presence/absence verdict for a single cell.
    """
    result = pd.isna(value)
    return result is True


def category_pct(df, mask):  # noqa: ARG001 - df kept for a stable call signature
    """Fraction of rows where ``mask`` is True (e.g., out-of-state share).

    ``mask`` is a boolean Series aligned to ``df``; the result is ``mask.mean()``.
    ``df`` is accepted for an explicit, self-documenting call site even though
    the computation only needs the mask.
    """
    return float(pd.Series(mask).mean())

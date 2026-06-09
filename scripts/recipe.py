"""The 13-point parameterized per-source analysis pass for FOIA / audit logs.

This is the Phase 4 *recipe*: a fixed battery of 13 investigative checks, each
config-driven, run over a single already-loaded / quality-gated / derived frame.
:func:`run_recipe` reads ``config["checks"]`` and runs ONLY the named checks,
returning a findings object the cross-source ``rollup`` then synthesizes.

Unlike its pure siblings (``stats.py`` / ``derive.py`` / ``data_quality.py``,
which deliberately import no neighbors), ``recipe.py`` is the ORCHESTRATOR layer:
composing those modules is its whole job, so it imports them and nothing else
outside the stdlib + pandas/numpy. It stays just as pure and deterministic --
no file IO, no clock, no randomness, no network -- so the full battery is
golden-testable against hand-computed expectations.

Three rigor stances are inherited verbatim from the siblings and are
publish-critical, because these numbers get published:

* **One word-boundary keyword defense, reused.** The keyword checks --
  immigration (#3), pretext (#4), co-travel (#7) -- categorize free text through
  the SINGLE shared guardrail :func:`derive.keyword_mask`, never an ad-hoc
  substring test. The canonical trap is "police": it ends in the substring
  "ice", and a naive ``"ice" in text`` would flag "Assist local police" as
  immigration. ``\\b``-anchored matching rejects it (also "service", "justice"),
  because a false keyword hit becomes a false published finding.
* **Leads, not verdicts; never a silent zero.** A check whose REQUIRED column is
  absent returns ``{"status": "skipped", "reason": ...}`` -- recorded in the
  findings, never a crash and never a silently-zeroed metric. A check that runs
  but leaves a sub-metric UNDEFINED (an empty-input median / share / gini, zero
  observations) reports that sub-metric as ``None`` -- NEVER a fake ``0.0`` that
  would read as a real measurement -- with ``status == "partial"`` and the
  relevant observation count (``n_with_nets`` / ``n_actors`` / ``n_users``) so a
  reader can see the denominator was empty.
* **Presence is not value.** Accountability (#6) routes through
  :func:`stats.presence_rate`, so a redaction sentinel counts as a case-number
  PRESENT (a value exists but was withheld) while a blank / ``NaN`` is ABSENT.

An UNKNOWN check name in ``config["checks"]`` raises :class:`ValueError` (a typo
must not silently drop a check), mirroring ``derive_columns``. Every check result
carries a ``"status"`` in ``{"ok", "partial", "skipped"}`` and a one-line
``"summary"``; scalar outputs are converted from numpy / pandas types to native
Python (``int`` / ``float`` / ``str`` / ``dict``) so the findings object is plain
data the rollup and any serializer can consume.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping

import pandas as pd

from scripts import stats
from scripts.data_quality import check_truncation as _dq_check_truncation
from scripts.derive import keyword_mask

# --------------------------------------------------------------------------- #
# native-type coercion helpers
#
# Tests compare check outputs to plain Python int / float / str / dict. A numpy
# or pandas scalar (np.int64, Int64 NA, np.float64) would compare equal in some
# spots but is the wrong type for a clean findings object that gets serialized
# downstream, so every emitted scalar is funneled through these.
# --------------------------------------------------------------------------- #

def _to_int(value: Any) -> int:
    """A count as a native ``int`` (from a numpy / pandas integer scalar)."""
    return int(value)


def _to_float(value: Any) -> float:
    """A rate / magnitude as a native ``float`` (from a numpy / pandas scalar)."""
    return float(value)


def _to_native_key(value: Any) -> Any:
    """A group / category label as a native Python scalar for use as a dict key.

    ``value_counts``/``groupby`` hand back numpy scalars (``np.str_``,
    ``np.int64``) as index labels; ``.item()`` recovers the plain Python object
    so the result dict has ordinary keys. A genuine Python object (a ``str`` from
    an object column) is returned unchanged.
    """
    item = getattr(value, "item", None)
    return item() if callable(item) else value


def _present_count(series: pd.Series) -> int:
    """Number of non-null cells in ``series`` as a native ``int``."""
    return int(series.notna().sum())


# --------------------------------------------------------------------------- #
# 1. truncation
# --------------------------------------------------------------------------- #

def check_truncation(df: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Wrap :func:`data_quality.check_truncation`, adding status + summary.

    Reuses the data-quality gate (an EXACT match on a spreadsheet/export row
    ceiling is the high-confidence truncation tell) and decorates its
    ``{"truncated", "n_rows", "ceiling", "message"}`` result with the recipe's
    uniform ``status`` / ``summary``. ``cfg["ceiling"]`` overrides the default
    ``2**20 - 1`` cap.

    The row COUNT is passed through, never the frame, so the underlying check
    never materializes a million-row DataFrame just to call ``len`` on it.
    """
    ceiling = cfg.get("ceiling")
    n_rows = len(df)
    result = (
        _dq_check_truncation(n_rows, ceiling=ceiling)
        if ceiling is not None
        else _dq_check_truncation(n_rows)
    )
    result["status"] = "ok"
    result["summary"] = (
        f"row count {result['n_rows']:,} "
        + (
            f"is EXACTLY the export ceiling ({result['ceiling']:,}) -- likely truncated"
            if result["truncated"]
            else f"is below the export ceiling ({result['ceiling']:,}); no truncation signal"
        )
    )
    return result


# --------------------------------------------------------------------------- #
# 2. out-of-state
# --------------------------------------------------------------------------- #

def check_out_of_state(df: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Count in-state / out-of-state / unknown rows with HONEST denominators.

    Reads the derived ``geo`` column (``cfg["geo_col"]``) and the label values
    ``out_label`` / ``in_label`` / ``unknown_label``. Reports the out-of-state
    share two ways so neither denominator is silently assumed: ``out_of_state_pct``
    over the TOTAL (incl. unknowns) and ``out_of_state_pct_of_known`` over the
    KNOWN rows (in + out) only. Skipped (not crashed) when the geo column is
    absent.
    """
    geo_col = cfg.get("geo_col", "geo")
    if geo_col not in df.columns:
        return _skipped(f"out_of_state needs column {geo_col!r}, which is absent")

    out_label = cfg["out_label"]
    in_label = cfg.get("in_label", "SC")
    unknown_label = cfg.get("unknown_label", "UNK")

    geo = df[geo_col]
    total = len(df)
    out_of_state = _to_int((geo == out_label).sum())
    in_state = _to_int((geo == in_label).sum())
    unknown = _to_int((geo == unknown_label).sum())
    known = out_of_state + in_state

    out_pct = out_of_state / total if total else None
    out_pct_known = out_of_state / known if known else None

    return {
        "status": "ok",
        "summary": (
            f"{out_of_state} of {total} rows out-of-state "
            f"({_fmt_pct(out_pct)}; {_fmt_pct(out_pct_known)} of known)"
        ),
        "total": total,
        "in_state": in_state,
        "out_of_state": out_of_state,
        "unknown": unknown,
        "out_of_state_pct": out_pct,
        "out_of_state_pct_of_known": out_pct_known,
    }


# --------------------------------------------------------------------------- #
# 3. immigration  (word-boundary keyword guardrail, or a precomputed flag)
# --------------------------------------------------------------------------- #

def check_immigration(df: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Count immigration / ICE rows via the shared word-boundary guardrail.

    Two paths: a precomputed boolean ``flag_col`` (e.g. ``is_immigration`` from
    ``derive``) fast path, OR a ``text_col`` + ``keywords`` path that routes
    through :func:`derive.keyword_mask` -- so "Assist local police" / "community
    service" / "Department of Justice" do NOT count off the substring "ice",
    while "ICE detainer" / "CBP" / "deportation" do. With an optional
    ``subtype_col`` it also breaks the immigration rows down ``by_subtype``
    (native ``{label: int}``). Skipped when neither a usable flag nor text column
    is present.
    """
    flag_col = cfg.get("flag_col")
    text_col = cfg.get("text_col")

    if flag_col is not None and flag_col in df.columns:
        mask = df[flag_col].fillna(False).astype(bool)
    elif text_col is not None and text_col in df.columns:
        mask = keyword_mask(df[text_col], cfg.get("keywords", []))
    else:
        return _skipped(
            "immigration needs a present flag_col or text_col; neither was found"
        )

    total = len(df)
    immigration = _to_int(mask.sum())
    pct = immigration / total if total else None

    result: dict[str, Any] = {
        "status": "ok",
        "summary": f"{immigration} of {total} rows immigration-related ({_fmt_pct(pct)})",
        "immigration": immigration,
        "immigration_pct": pct,
        "total": total,
    }

    subtype_col = cfg.get("subtype_col")
    if subtype_col is not None and subtype_col in df.columns:
        result["by_subtype"] = _native_counts(df.loc[mask, subtype_col])
    return result


# --------------------------------------------------------------------------- #
# 4. pretext  (free-text categorization, same guardrail)
# --------------------------------------------------------------------------- #

def check_pretext(df: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Count pretextual-reason rows by keyword OR by a precomputed category.

    Two paths: a ``text_col`` + ``keywords`` path through the shared
    :func:`derive.keyword_mask` word-boundary guardrail (so "traffic" matches
    "Traffic-related inquiry" but a substring trap would not over-match), OR a
    ``cat_col`` + ``pretext_cats`` path that counts rows whose categorical reason
    is in the configured pretext set. Skipped when neither configured column is
    present.
    """
    text_col = cfg.get("text_col")
    cat_col = cfg.get("cat_col")

    if text_col is not None and text_col in df.columns:
        mask = keyword_mask(df[text_col], cfg.get("keywords", []))
    elif cat_col is not None and cat_col in df.columns:
        pretext_cats = set(cfg.get("pretext_cats", []))
        mask = df[cat_col].isin(pretext_cats)
    else:
        return _skipped(
            "pretext needs a present text_col or cat_col; neither was found"
        )

    total = len(df)
    pretext = _to_int(mask.sum())
    pct = pretext / total if total else None

    return {
        "status": "ok",
        "summary": f"{pretext} of {total} rows pretextual ({_fmt_pct(pct)})",
        "pretext": pretext,
        "pretext_pct": pct,
        "total": total,
    }


# --------------------------------------------------------------------------- #
# 5. PII  (structured-regex PRESENCE indicator -- not the spaCy pii-sweep)
# --------------------------------------------------------------------------- #

# High-precision default patterns. Record-level presence only; deliberately NO
# bare-date pattern (an incident date is not exposure -- DOB detection is opt-in
# via cfg["patterns"]). Semantic NER over names is deferred to Phase 5 pii-sweep.
# a_number tolerates an optional '#' and a single space/hyphen separator and
# spans 8-9 digits -- favors recall on this most-sensitive identifier (the real
# A-numbers in the corpus carry separators) while staying anchored on the shape.
_DEFAULT_PII_PATTERNS: dict[str, str] = {
    "a_number": r"\bA#?[\s-]?\d{3}[\s-]?\d{3}[\s-]?\d{2,3}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "phone": r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b",
    "email": r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
}

_PII_NOTE = (
    "structured-regex presence indicator only; semantic NER over names is "
    "deferred to pii-sweep (Phase 5)"
)


def check_pii(df: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """RECORD-LEVEL structured-PII presence across one or more text columns.

    For each pattern in ``cfg["patterns"]`` (default: ``a_number``, ``ssn``,
    ``phone``, ``email`` -- no bare date), scans every ``cfg["text_cols"]`` cell.
    ``records_with_pii`` counts ROWS with at least one match in ANY text column
    (deduped per row, so a row matching in two columns is one record);
    ``records_by_pattern`` is rows-per-pattern. This is a fast PRESENCE signal,
    not an authoritative tally -- ``note`` says so (``pii-sweep`` defers the
    semantic NER to Phase 5). Skipped when none of the configured text columns
    are present.
    """
    text_cols = [c for c in cfg.get("text_cols", []) if c in df.columns]
    if not text_cols:
        return _skipped(
            "pii needs at least one present text_col; none of "
            f"{list(cfg.get('text_cols', []))} were found"
        )

    patterns_src: Mapping[str, str] = cfg.get("patterns", _DEFAULT_PII_PATTERNS)
    compiled = {name: re.compile(pat) for name, pat in patterns_src.items()}

    total = len(df)
    # Per-pattern boolean row masks (True if the pattern hits in ANY text col),
    # so record_with_pii dedupes a row that matches in multiple columns.
    any_row = pd.Series(False, index=df.index)
    records_by_pattern: dict[str, int] = {}
    for name, pattern in compiled.items():
        per_pattern = pd.Series(False, index=df.index)
        for col in text_cols:
            cell_hits = df[col].map(lambda v: _regex_hits(pattern, v))
            per_pattern = per_pattern | cell_hits
        records_by_pattern[name] = _to_int(per_pattern.sum())
        any_row = any_row | per_pattern

    records_with_pii = _to_int(any_row.sum())
    pct = records_with_pii / total if total else None

    return {
        "status": "ok",
        "summary": (
            f"{records_with_pii} of {total} rows carry structured PII "
            f"({_fmt_pct(pct)})"
        ),
        "records_with_pii": records_with_pii,
        "pii_pct": pct,
        "records_by_pattern": records_by_pattern,
        "note": _PII_NOTE,
    }


def _regex_hits(pattern: re.Pattern[str], value: Any) -> bool:
    """True iff ``pattern`` finds a match in ``value`` (null / blank -> False)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return pattern.search(str(value)) is not None


# --------------------------------------------------------------------------- #
# 6. accountability  (case-number presence; presence != value)
# --------------------------------------------------------------------------- #

def check_accountability(df: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Rate of records WITHOUT a case number; presence is not value.

    Routes through :func:`stats.presence_rate` so a redaction sentinel counts as
    a case number PRESENT (a value exists but was withheld) while a blank /
    ``NaN`` is ABSENT. Reports ``with_case`` / ``without_case`` / ``no_case_pct``
    over ``cfg["case_col"]``; with an optional ``group_col`` it also emits
    per-group ``no_case_pct`` in ``by_group`` (native ``{label: float}``).
    Skipped when the case column is absent.
    """
    case_col = cfg.get("case_col", "has_case")
    if case_col not in df.columns:
        return _skipped(f"accountability needs column {case_col!r}, which is absent")

    total = len(df)
    case_rate = stats.presence_rate(df[case_col], _is_present)
    with_case = round(case_rate * total)
    without_case = total - with_case
    no_case_pct = without_case / total if total else None

    result: dict[str, Any] = {
        "status": "ok",
        "summary": (
            f"{without_case} of {total} rows lack a case number "
            f"({_fmt_pct(no_case_pct)})"
        ),
        "with_case": with_case,
        "without_case": without_case,
        "no_case_pct": no_case_pct,
    }

    group_col = cfg.get("group_col")
    if group_col is not None and group_col in df.columns:
        by_group: dict[Any, float] = {}
        for group_value, sub in df.groupby(group_col):
            grp_present = stats.presence_rate(sub[case_col], _is_present)
            by_group[_to_native_key(group_value)] = 1.0 - grp_present
        result["by_group"] = by_group
    return result


def _is_present(value: Any) -> bool:
    """Presence predicate: any non-blank token is present (incl. a True flag)."""
    return str(value).strip() not in ("", "False")


# --------------------------------------------------------------------------- #
# 7. co-travel  (keyword / search-type, same guardrail; not a spatial join)
# --------------------------------------------------------------------------- #

def check_co_travel(df: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Count co-travel / convoy search-type rows via the shared guardrail.

    A keyword pass over ``cfg["text_col"]`` through :func:`derive.keyword_mask`
    (Flock "convoy" / "co-travel" query indicators), NOT a spatial plate-pair
    self-join -- consistent with the audit-log grain. The word-boundary guardrail
    is the point: "travelogue review" does NOT count off the substring "travel".
    Skipped when the text column is absent.
    """
    text_col = cfg.get("text_col")
    if text_col is None or text_col not in df.columns:
        return _skipped(
            f"co_travel needs a present text_col; {text_col!r} was not found"
        )

    mask = keyword_mask(df[text_col], cfg.get("keywords", []))
    total = len(df)
    co_travel = _to_int(mask.sum())
    pct = co_travel / total if total else None

    return {
        "status": "ok",
        "summary": f"{co_travel} of {total} rows co-travel-related ({_fmt_pct(pct)})",
        "co_travel": co_travel,
        "co_travel_pct": pct,
        "total": total,
    }


# --------------------------------------------------------------------------- #
# 8. blast-radius  (net width + net-width-by-severity)
# --------------------------------------------------------------------------- #

def check_blast_radius(df: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Surveillance net-width distribution, overall and by severity.

    Over the derived ``cfg["nets_col"]`` (a nullable ``Int64``): ``median`` /
    ``max`` / ``total_exposure`` / ``n_with_nets`` (the count of NON-null
    observations). With an optional ``severity_col`` it adds ``by_severity`` via
    :func:`stats.median_by_category`, sorted high -> low (the headline inversion:
    routine Traffic can cast a WIDER median net than Homicide). Skipped when the
    nets column is absent. When the column is present but has ZERO observations,
    status is ``partial`` and the undefined ``median`` / ``max`` are ``None`` --
    never a fake ``0`` -- with ``n_with_nets == 0``.
    """
    nets_col = cfg.get("nets_col", "nets")
    if nets_col not in df.columns:
        return _skipped(f"blast_radius needs column {nets_col!r}, which is absent")

    nets = df[nets_col]
    n_with_nets = _present_count(nets)

    if n_with_nets == 0:
        return {
            "status": "partial",
            "summary": "no net-width observations present; median/max undefined",
            "n_with_nets": 0,
            "median": None,
            "max": None,
            "total_exposure": 0,
            "reason": "nets column present but has zero non-null observations",
        }

    median = _to_float(nets.median())
    max_val = _to_int(nets.max())
    total_exposure = _to_int(nets.sum())

    result: dict[str, Any] = {
        "status": "ok",
        "summary": (
            f"median net {median:g} over {n_with_nets} rows; "
            f"max {max_val}, total exposure {total_exposure}"
        ),
        "n_with_nets": n_with_nets,
        "median": median,
        "max": max_val,
        "total_exposure": total_exposure,
    }

    severity_col = cfg.get("severity_col")
    if severity_col is not None and severity_col in df.columns:
        by_cat = stats.median_by_category(df, nets_col, severity_col)
        result["by_severity"] = {
            _to_native_key(k): _to_float(v)
            for k, v in by_cat.items()
            if not pd.isna(v)
        }
    return result


# --------------------------------------------------------------------------- #
# 9. mega-users  (concentration: top-N + largest)
# --------------------------------------------------------------------------- #

def check_mega_users(df: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Heaviest actors: top-N named users + the single largest + top-frac share.

    Counts rows per ``cfg["user_col"]`` and reports ``n_users``, the
    ``largest_user`` (``{user, count}``), the ``top_users`` list (up to
    ``cfg["top_n"]``, default 10, each ``{user, count}``), and ``top_frac_share``
    -- the volume share held by the top ``cfg["top_frac"]`` (default 0.01) of
    actors, via :func:`stats.top_k_share`. Skipped when the user column is absent.
    When NO actors are observed (empty frame) status is ``partial`` and
    ``largest_user`` / ``top_frac_share`` are ``None`` -- never a fake ``0``.
    """
    user_col = cfg.get("user_col")
    if user_col is None or user_col not in df.columns:
        return _skipped(
            f"mega_users needs a present user_col; {user_col!r} was not found"
        )

    counts = df[user_col].value_counts()
    n_users = _to_int(counts.size)

    if n_users == 0:
        return {
            "status": "partial",
            "summary": "no actors observed; concentration undefined",
            "n_users": 0,
            "largest_user": None,
            "top_users": [],
            "top_frac_share": None,
            "reason": "user column present but no actors observed",
        }

    top_n = cfg.get("top_n", 10)
    top_frac = cfg.get("top_frac", 0.01)

    top_users = [
        {"user": _to_native_key(user), "count": _to_int(count)}
        for user, count in counts.head(top_n).items()
    ]
    largest_user = top_users[0]
    top_frac_share = _to_float(stats.top_k_share(counts, top_frac))

    return {
        "status": "ok",
        "summary": (
            f"{n_users} actors; largest {largest_user['user']!r} "
            f"({largest_user['count']} rows); top-frac share {top_frac_share:.2f}"
        ),
        "n_users": n_users,
        "largest_user": largest_user,
        "top_users": top_users,
        "top_frac_share": top_frac_share,
    }


# --------------------------------------------------------------------------- #
# 10. operations  (tempo / volume overview)
# --------------------------------------------------------------------------- #

def check_operations(df: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Volume / tempo overview: span, active days, busiest day & hour.

    Over the derived ``date_col`` / ``hour_col`` (and optional ``user_col``):
    ``total_records``; ``n_users``; ``date_start`` / ``date_end`` as ISO strings;
    ``span_days`` (end - start in days); ``n_active_days`` (distinct days
    present); ``records_per_active_day`` = total / active days; ``busiest_hour``
    and ``busiest_day`` (each ``{..., count}``). Both "busiest" ties break
    DETERMINISTICALLY to the EARLIEST (smallest date / smallest hour) so a rerun
    is stable. Skipped when the date column is absent.
    """
    date_col = cfg.get("date_col", "date_et")
    if date_col not in df.columns:
        return _skipped(f"operations needs column {date_col!r}, which is absent")

    total_records = len(df)
    result: dict[str, Any] = {
        "status": "ok",
        "total_records": total_records,
    }

    user_col = cfg.get("user_col")
    if user_col is not None and user_col in df.columns:
        result["n_users"] = _to_int(df[user_col].nunique())

    dates = df[date_col].dropna()
    date_counts = dates.value_counts()
    date_start = min(dates) if not dates.empty else None
    date_end = max(dates) if not dates.empty else None
    n_active_days = _to_int(dates.nunique())
    span_days = (date_end - date_start).days if date_start is not None else None
    per_active_day = total_records / n_active_days if n_active_days else None

    result["date_start"] = _iso_date(date_start)
    result["date_end"] = _iso_date(date_end)
    result["span_days"] = span_days
    result["n_active_days"] = n_active_days
    result["records_per_active_day"] = per_active_day
    result["busiest_day"] = _busiest(date_counts, "date", _iso_date)

    hour_col = cfg.get("hour_col", "hour_et")
    if hour_col in df.columns:
        hour_counts = df[hour_col].dropna().value_counts()
        result["busiest_hour"] = _busiest(hour_counts, "hour", _to_int)

    busiest_day = result["busiest_day"]
    result["summary"] = (
        f"{total_records} rows over {n_active_days} active days "
        f"({result['date_start']} -> {result['date_end']})"
        + (
            f"; busiest day {busiest_day['date']} ({busiest_day['count']})"
            if busiest_day is not None
            else ""
        )
    )
    return result


def _busiest(
    counts: pd.Series, label: str, coerce: Callable[[Any], Any]
) -> dict[str, Any] | None:
    """Top entry of a ``value_counts`` Series, ties broken to the SMALLEST key.

    Sorting by count descending then by key ascending makes the highest-count,
    earliest-key entry first, so an all-tie distribution resolves to the earliest
    date / smallest hour deterministically. ``coerce`` renders the key (ISO date
    string or native int). Returns ``None`` for an empty Series.
    """
    if counts.empty:
        return None
    ordered = counts.sort_index(ascending=True).sort_values(
        ascending=False, kind="stable"
    )
    key = ordered.index[0]
    return {label: coerce(key), "count": _to_int(ordered.iloc[0])}


# --------------------------------------------------------------------------- #
# 11. AI / moderation  (automation signature + burstiness)
# --------------------------------------------------------------------------- #

def check_ai_moderation(df: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Two halves: same-second burstiness (a genuine automation tell) + an
    overnight-share split (a timezone-confounded LEAD, not an automation verdict).

    Always runs :func:`stats.automation_signature` over ``cfg["hour_col"]`` /
    ``cfg["user_col"]`` to mark actors whose overnight share crosses
    ``cfg["overnight_threshold"]`` (default 0.5) as ``overnight_heavy``. The hour
    column is derived in a SINGLE timezone, so an actor working ordinary hours in
    another timezone can show a high overnight share by GEOGRAPHY, not behavior;
    ``overnight_heavy`` is reported as a LEAD to inspect (with ``overnight_caveat``
    spelling out the confound), NOT as an automation verdict. Burstiness
    (:func:`stats.burstiness`, same-second batches) is the timezone-INDEPENDENT
    automation tell and runs ONLY when a ``timestamp_col`` is configured AND
    present; otherwise the burst half is undefined, so status is ``partial``,
    ``max_same_second`` is ``None`` (never a fake ``0``), and ``reason`` says a
    timestamp column is needed.

    A present ``timestamp_col`` is VALIDATED, not trusted: it is coerced with
    ``pd.to_datetime(..., errors="coerce")`` and the invalid (``NaT``) rows are
    dropped before :func:`stats.burstiness` runs, so junk strings can't be
    grouped as if they were real seconds -- a column of unparseable values would
    otherwise fabricate one giant same-second batch (a false automation finding),
    and an all-invalid column would fall through to ``max_same_second == 0`` read
    as a real measurement. When NO valid timestamps remain the burst half is
    undefined exactly as in the no-column case (``partial`` / ``None``); the
    overnight-signature half is always computed regardless.
    """
    hour_col = cfg.get("hour_col", "hour_et")
    user_col = cfg.get("user_col")
    if hour_col not in df.columns or user_col is None or user_col not in df.columns:
        missing = hour_col if hour_col not in df.columns else user_col
        return _skipped(
            f"ai_moderation needs hour_col and user_col; {missing!r} is absent"
        )

    signature = stats.automation_signature(
        df,
        hour_col,
        user_col,
        day_start=cfg.get("day_start", 6),
        day_end=cfg.get("day_end", 18),
        overnight_threshold=cfg.get("overnight_threshold", 0.5),
    )
    overnight_heavy = signature.index[signature["overnight_heavy"]].tolist()
    overnight_heavy_actors = [_to_native_key(a) for a in overnight_heavy]
    n_actors = _to_int(signature.shape[0])
    n_overnight_heavy = len(overnight_heavy_actors)

    # The overnight half is timezone-relative: "overnight" is defined against the
    # single timezone hour_col is derived in, so a high overnight share can be
    # geography, not behavior. Attach the caveat so no downstream consumer reads
    # overnight_heavy as an automation verdict (issue #18).
    overnight_caveat = (
        f"overnight share is computed from a single-timezone hour column ({hour_col}); "
        "an actor working ordinary hours in another timezone can show a high overnight "
        "share by geography, not behavior -- treat overnight_heavy as a lead to inspect, "
        "not an automation verdict"
    )

    result: dict[str, Any] = {
        "n_actors": n_actors,
        "n_overnight_heavy": n_overnight_heavy,
        "overnight_heavy_actors": overnight_heavy_actors,
        "overnight_caveat": overnight_caveat,
    }

    timestamp_col = cfg.get("timestamp_col")
    # Coerce + drop invalid rows BEFORE burstiness: a present timestamp_col is
    # validated, never trusted (junk strings would otherwise group as one giant
    # fake same-second batch, and an all-invalid column would read as a real
    # max_same_second == 0). With no usable column or no valid rows left, the
    # burst half is undefined -> partial / None, exactly like the no-column case.
    valid_ts: pd.DataFrame | None = None
    if timestamp_col is not None and timestamp_col in df.columns:
        coerced = pd.to_datetime(df[timestamp_col], errors="coerce", format="mixed")
        valid_mask = coerced.notna()
        if valid_mask.any():
            valid_ts = pd.DataFrame(
                {timestamp_col: coerced[valid_mask], user_col: df.loc[valid_mask, user_col]}
            )

    if valid_ts is not None:
        burst = stats.burstiness(valid_ts, timestamp_col, user_col)
        result["status"] = "ok"
        result["max_same_second"] = _to_int(burst["max_same_second"])
        result["burst_size_distribution"] = {
            _to_int(k): _to_int(v) for k, v in burst["size_distribution"].items()
        }
        result["summary"] = (
            f"{n_overnight_heavy} of {n_actors} actors have a heavy overnight share "
            "(lead; overnight is timezone-relative, see overnight_caveat); "
            f"max same-second batch {result['max_same_second']}"
        )
    else:
        result["status"] = "partial"
        result["max_same_second"] = None
        result["reason"] = (
            "burstiness needs a second-resolution timestamp_col with valid "
            "values; none configured/present or all timestamps were unparseable, "
            "so same-second batching is undefined"
        )
        result["summary"] = (
            f"{n_overnight_heavy} of {n_actors} actors have a heavy overnight share "
            "(lead; overnight is timezone-relative); burstiness undefined "
            "(no valid timestamp column)"
        )
    return result


# --------------------------------------------------------------------------- #
# 12. cross-agency overlap  (per-source EXTERNAL actor set for the rollup)
# --------------------------------------------------------------------------- #

def check_cross_agency(df: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Per-source actor set, with the EXTERNAL subset the rollup keys recurrence off.

    Emits ``actor_counts`` -- the FULL ``{actor: count}`` machine field, NEVER
    truncated, so the cross-source rollup can't silently miss a shared actor --
    plus ``external_actor_counts`` (``{actor: count}`` for actors seen on any row
    where ``cfg["external_geo_col"] == cfg["external_label"]``),
    ``external_actors`` (the sorted list), and ``n_external_actors``. Rollup keys
    recurrence off the EXTERNAL counts only, so an in-state actor cannot
    manufacture a cross-source finding. Skipped when the user column is absent.

    When the user column IS present but ``cfg["external_geo_col"]`` is ABSENT, the
    external rows can't be classified at all -- that is UNCLASSIFIED, not measured
    zero -- so status is ``partial`` (the ``reason`` names the missing geo column)
    with an empty ``external_actor_counts`` / ``external_actors`` and
    ``n_external_actors == 0``; the FULL ``actor_counts`` / ``n_actors`` are still
    reported. Returning ``ok`` with an empty external set would let the rollup
    read "cannot classify external" as "no external actors" and silently suppress
    the recurrence thesis.

    ``external_actors`` is sorted with ``key=str`` so a user column mixing string
    and numeric labels sorts deterministically instead of raising; the machine
    ``actor_counts`` / ``external_actor_counts`` dicts keep their native keys.
    """
    user_col = cfg.get("user_col")
    if user_col is None or user_col not in df.columns:
        return _skipped(
            f"cross_agency needs a present user_col; {user_col!r} was not found"
        )

    actor_counts = _native_counts(df[user_col])
    n_actors = len(actor_counts)

    external_geo_col = cfg.get("external_geo_col", "geo")
    external_label = cfg.get("external_label", "OOS")
    if external_geo_col not in df.columns:
        # Can't classify external rows at all: UNCLASSIFIED, not measured zero.
        # status partial so rollup excludes this source rather than reading an
        # empty external set as "no external actors" and suppressing recurrence.
        return {
            "status": "partial",
            "summary": (
                f"{n_actors} actors; external set undefined "
                f"(geo column {external_geo_col!r} absent)"
            ),
            "reason": (
                f"external actors need the geo column {external_geo_col!r}, which "
                "is absent, so the external subset is unclassified (not zero)"
            ),
            "n_actors": n_actors,
            "actor_counts": actor_counts,
            "external_actor_counts": {},
            "external_actors": [],
            "n_external_actors": 0,
        }

    external_rows = df[df[external_geo_col] == external_label]
    external_actor_counts = _native_counts(external_rows[user_col])

    # key=str so a user_col mixing str + numeric labels sorts deterministically
    # rather than raising; the machine count dicts keep their native keys.
    external_actors = sorted(external_actor_counts, key=str)
    n_external_actors = len(external_actors)

    return {
        "status": "ok",
        "summary": (
            f"{n_actors} actors, {n_external_actors} seen on external rows"
        ),
        "n_actors": n_actors,
        "actor_counts": actor_counts,
        "external_actor_counts": external_actor_counts,
        "external_actors": external_actors,
        "n_external_actors": n_external_actors,
    }


# --------------------------------------------------------------------------- #
# 13. statistical patterns  (Gini + concentration shares)
# --------------------------------------------------------------------------- #

def check_statistical_patterns(
    df: pd.DataFrame, cfg: Mapping[str, Any]
) -> dict[str, Any]:
    """Concentration of activity: Gini + top-frac / bottom-half shares.

    Counts rows per ``cfg["user_col"]`` and measures HOW concentrated the load
    is via :func:`stats.gini`, :func:`stats.top_k_share` (top ``cfg["top_frac"]``,
    default 0.01), and :func:`stats.bottom_half_share`. Skipped when the user
    column is absent. When NO actors are observed, Gini and both shares are
    UNDEFINED (``None``, never a misleading real ``0.0``) and status is
    ``partial``, with ``n_actors == 0``.
    """
    user_col = cfg.get("user_col")
    if user_col is None or user_col not in df.columns:
        return _skipped(
            f"statistical_patterns needs a present user_col; {user_col!r} was not found"
        )

    counts = df[user_col].value_counts()
    n_actors = _to_int(counts.size)

    if n_actors == 0:
        return {
            "status": "partial",
            "summary": "no actors observed; Gini and shares undefined",
            "n_actors": 0,
            "gini": None,
            "top_frac_share": None,
            "bottom_half_share": None,
            "reason": "user column present but no actors observed",
        }

    top_frac = cfg.get("top_frac", 0.01)
    gini_val = _to_float(stats.gini(counts))
    top_frac_share = _to_float(stats.top_k_share(counts, top_frac))
    bottom_half = _to_float(stats.bottom_half_share(counts))

    return {
        "status": "ok",
        "summary": (
            f"{n_actors} actors; Gini {gini_val:.3f}, "
            f"top-frac share {top_frac_share:.2f}, bottom-half {bottom_half:.2f}"
        ),
        "n_actors": n_actors,
        "gini": gini_val,
        "top_frac_share": top_frac_share,
        "bottom_half_share": bottom_half,
    }


# --------------------------------------------------------------------------- #
# shared check helpers
# --------------------------------------------------------------------------- #

def _skipped(reason: str) -> dict[str, Any]:
    """A uniform skipped-with-reason result (a required column was absent)."""
    return {"status": "skipped", "reason": reason, "summary": f"skipped: {reason}"}


def _fmt_pct(value: float | None) -> str:
    """Render a fraction as a percent for a summary line (``"n/a"`` for None)."""
    return "n/a" if value is None else f"{value:.1%}"


def _iso_date(value: Any) -> str | None:
    """A date as an ISO ``YYYY-MM-DD`` string (``None`` passes through)."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    return pd.Timestamp(value).date().isoformat()


def _native_counts(series: pd.Series) -> dict[Any, int]:
    """``value_counts`` as a native ``{label: int}`` dict (numpy types unwrapped)."""
    return {
        _to_native_key(label): _to_int(count)
        for label, count in series.value_counts().items()
    }


# --------------------------------------------------------------------------- #
# check registry + orchestrator
# --------------------------------------------------------------------------- #

# The 13 checks, by config name. An unknown name in config["checks"] is a loud
# ValueError (a typo must not silently drop a check), mirroring derive_columns.
CHECKS: dict[str, Callable[[pd.DataFrame, Mapping[str, Any]], dict[str, Any]]] = {
    "truncation": check_truncation,
    "out_of_state": check_out_of_state,
    "immigration": check_immigration,
    "pretext": check_pretext,
    "pii": check_pii,
    "accountability": check_accountability,
    "co_travel": check_co_travel,
    "blast_radius": check_blast_radius,
    "mega_users": check_mega_users,
    "operations": check_operations,
    "ai_moderation": check_ai_moderation,
    "cross_agency": check_cross_agency,
    "statistical_patterns": check_statistical_patterns,
}


def run_recipe(df: pd.DataFrame, config: Mapping[str, Any]) -> dict[str, Any]:
    """Run the configured subset of the 13 checks over one source frame.

    ``config`` is ``{"source_id": str, "checks": {check_name: check_cfg, ...}}``.
    ONLY the checks named in ``config["checks"]`` are run; each is looked up in
    :data:`CHECKS` and called with its own config. An UNKNOWN check name raises
    :class:`ValueError` whose message contains "unknown check" (a typo must not
    silently drop a check, the same guard ``derive_columns`` applies to
    derivations).

    A check whose required column is absent records a skipped-with-reason result
    rather than crashing the whole pass, so one missing column never sinks the
    other 12 checks. Returns::

        {"source_id": str, "n_records": int, "checks": {name: result, ...}}

    Pure and deterministic: the frame is only read, never mutated.
    """
    checks_cfg: Mapping[str, Mapping[str, Any]] = config.get("checks", {})
    unknown = set(checks_cfg) - set(CHECKS)
    if unknown:
        raise ValueError(
            f"unknown check(s) in config: {sorted(unknown)}; "
            f"recognized: {sorted(CHECKS)}"
        )

    results: dict[str, dict[str, Any]] = {}
    for name, check_cfg in checks_cfg.items():
        results[name] = CHECKS[name](df, check_cfg)

    return {
        "source_id": config.get("source_id"),
        "n_records": len(df),
        "checks": results,
    }

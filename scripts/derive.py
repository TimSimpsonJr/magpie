"""Config-driven derived columns for FOIA / audit-log datasets.

The analysis recipe (Phase 4) and the stats module both consume a handful of
*derived* columns -- a geographic in/out-of-state class, a categorized reason, an
immigration flag, a numeric blast-radius, a case-present flag, a collapsed record
type, and timezone-localized date / hour / day-of-week fields. This module builds
them.

It is deliberately GENERIC: the derivation RULES live in a ``config`` dict, and
the module only supplies the primitives. Nothing here is hardcoded to a specific
agency, state, reason vocabulary, or record-type scheme -- a Simpsonville run and
a run over some other jurisdiction differ only in the config they pass. Each
primitive (``derive_geo``, ``derive_reason``, ``derive_immigration``,
``derive_nets``, ``derive_has_case``, ``derive_base_type``,
``derive_temporal_et``) is a small pure function you can import and test in
isolation; :func:`derive_columns` is the orchestrator that runs whichever
derivations the config selects.

Every function is pure and deterministic (pandas + stdlib :mod:`zoneinfo` only --
no file IO, no clock, no randomness, no network) and returns NEW data; the input
frame is never mutated. The module is decoupled from ``stats.py``,
``load_table.py``, ``data_quality.py``, and any corpus loader.

Two rigor guardrails are load-bearing and pinned by the test suite:

* **Word-boundary keyword matching.** Categorization and the immigration flag
  match keywords on WORD BOUNDARIES, never as raw substrings. The canonical trap
  is "police": it ends in the substring "ice", and a naive ``"ice" in text``
  would flag "Assist local police" as immigration. ``\b``-anchored matching
  rejects it (also "service", "justice"), because a false keyword hit becomes a
  false published finding.
* **Presence is not value.** ``has_case`` treats a redaction sentinel like
  ``***`` as PRESENT (a case number EXISTS but was withheld) while a blank /
  ``NaN`` / ``""`` is ABSENT. Conflating ``***`` with blank would undercount how
  often a field was actually populated.

And one ergonomic guardrail: the geographic column is named ``geo``, never
``loc`` -- ``loc`` shadows :attr:`pandas.DataFrame.loc`, which bit an earlier
prototype.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Mapping

import pandas as pd

# The set of derivations :func:`derive_columns` knows how to run. A config key
# outside this set is a configuration error (surfaced loudly rather than
# silently skipped, so a typo'd derivation name can't quietly drop a column).
_KNOWN_DERIVATIONS: frozenset[str] = frozenset(
    {"geo", "reason", "immigration", "nets", "has_case", "base_type", "temporal"}
)


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #

def _is_null(value: Any) -> bool:
    """True for a scalar pandas missing sentinel (None / NaN / NA / NaT).

    Guarded so an array-like (for which :func:`pandas.isna` returns an array)
    is treated as non-null rather than raising on ``bool(array)``.
    """
    result = pd.isna(value)
    return result is True


def _readable_text(value: Any) -> Any:
    """Stripped, human-readable form of a free-text cell (case preserved).

    A null stays null (so a missing reason does not become the literal string
    ``"nan"``); any other value is coerced to ``str`` and stripped of
    surrounding whitespace. Internal whitespace is left intact -- this is the
    form a human reads, not the lowercased form used for matching.
    """
    if _is_null(value):
        return value
    return str(value).strip()


def _match_text(value: Any) -> str:
    """Lowercased matching form of a cell ("" for a null), for keyword search."""
    if _is_null(value):
        return ""
    return str(value).strip().lower()


def _compile_keyword_regex(keywords: Iterable[str]) -> re.Pattern[str] | None:
    """Compile keywords into one case-insensitive WORD-BOUNDARY alternation.

    Returns a pattern that matches when ANY keyword appears as a whole word in
    the text, i.e. ``\\b(?:kw1|kw2|...)\\b`` (case-insensitive). The ``\\b``
    anchors are the guardrail: ``"ice"`` matches the standalone token "ice" or
    "ice" in "ICE detainer", but NOT the "ice" inside "police" / "service" /
    "justice", because those are preceded by a word character (no boundary).

    Each keyword is :func:`re.escape`-d, so a keyword may itself contain regex
    metacharacters or multiple words (e.g. ``"department of justice"``) and is
    matched literally. Keywords are sorted longest-first inside the alternation
    so the regex prefers the most specific match. Returns ``None`` for an empty
    keyword set (matches nothing).

    Note on boundaries: ``\\b`` is defined against ``\\w`` (``[A-Za-z0-9_]``).
    For a keyword that begins or ends with a non-word character the adjacent
    ``\\b`` would never match; keywords here are alphabetic tokens, for which
    ``\\b`` is exactly the whole-word anchor we want.
    """
    cleaned = [str(k).strip().lower() for k in keywords if str(k).strip()]
    if not cleaned:
        return None
    # Longest-first: a literal alternation is leftmost-eager, so listing
    # "immigration" before "ice" avoids a shorter keyword pre-empting a longer
    # overlapping one. (Harmless for disjoint keywords; correct for nested ones.)
    cleaned.sort(key=len, reverse=True)
    alternation = "|".join(re.escape(k) for k in cleaned)
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# geo
# --------------------------------------------------------------------------- #

def derive_geo(df: pd.DataFrame, config: Mapping[str, Any]) -> pd.Series:
    """Classify each row in-state / out-of-state / unknown.

    From ``config[source_col]`` and a ``home_value``, label each row:

    * ``in_label``      -- the source value equals ``home_value`` (compared
      case-insensitively after stripping, so ``"sc"`` / ``"  SC "`` count as
      home);
    * ``out_label``     -- a non-blank source value that is not home;
    * ``unknown_label`` -- a missing / blank / whitespace-only source value.

    Config keys: ``source_col``, ``home_value``, ``in_label``, ``out_label``,
    ``unknown_label``.

    Returns a :class:`pandas.Series` (the ``geo`` column). It is named ``geo``
    by the orchestrator -- NEVER ``loc``, which would shadow ``df.loc``.
    """
    source = df[config["source_col"]]
    home = str(config["home_value"]).strip().lower()
    in_label = config["in_label"]
    out_label = config["out_label"]
    unknown_label = config["unknown_label"]

    def classify(value: Any) -> Any:
        norm = _match_text(value)
        if norm == "":
            return unknown_label
        return in_label if norm == home else out_label

    return source.map(classify)


# --------------------------------------------------------------------------- #
# reason_cat / reason_text
# --------------------------------------------------------------------------- #

def derive_reason(df: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    """Derive ``reason_text`` (readable) and ``reason_cat`` (keyword category).

    ``reason_text`` is the stripped, case-preserved free-text reason (a null
    stays null). ``reason_cat`` is the first category whose keyword set matches
    the reason on a WORD BOUNDARY, scanning ``config[keyword_map]`` in insertion
    order; a reason that matches nothing gets ``config[default]`` (e.g.
    ``"Other"``).

    Config keys: ``source_col``; ``keyword_map`` (``{category: [keywords]}``);
    ``default`` (the fallback category label).

    Word-boundary matching is the guardrail: a "police" reason must not be
    categorized "Immigration" off the substring "ice". Returns a NEW two-column
    :class:`pandas.DataFrame` (``reason_text``, ``reason_cat``) aligned to ``df``.
    """
    source = df[config["source_col"]]
    keyword_map: Mapping[str, Iterable[str]] = config["keyword_map"]
    default = config.get("default", "Other")

    # Compile once per category (insertion order preserved -> first match wins).
    compiled: list[tuple[str, re.Pattern[str] | None]] = [
        (category, _compile_keyword_regex(keywords))
        for category, keywords in keyword_map.items()
    ]

    def categorize(value: Any) -> Any:
        text = _match_text(value)
        if text == "":
            return default
        for category, pattern in compiled:
            if pattern is not None and pattern.search(text):
                return category
        return default

    return pd.DataFrame(
        {
            "reason_text": source.map(_readable_text),
            "reason_cat": source.map(categorize),
        },
        index=df.index,
    )


# --------------------------------------------------------------------------- #
# is_immigration
# --------------------------------------------------------------------------- #

def derive_immigration(df: pd.DataFrame, config: Mapping[str, Any]) -> pd.Series:
    """Boolean immigration / ICE flag, WORD-BOUNDARY matched.

    True where the reason text contains any ``config[keywords]`` keyword as a
    whole word (case-insensitive). The word-boundary guardrail is the whole
    point: "police", "service", and "justice" all contain the substring "ice"
    yet MUST yield False, because a false immigration hit becomes a false
    finding.

    Config keys: ``source_col``; ``keywords`` (an immigration keyword list).
    Null / blank reasons are False. Returns a boolean :class:`pandas.Series`.
    """
    source = df[config["source_col"]]
    pattern = _compile_keyword_regex(config["keywords"])
    if pattern is None:
        return pd.Series(False, index=df.index, dtype=bool)

    def is_immigration(value: Any) -> bool:
        text = _match_text(value)
        return bool(text) and pattern.search(text) is not None

    return source.map(is_immigration).astype(bool)


# --------------------------------------------------------------------------- #
# nets  (numeric blast-radius)
# --------------------------------------------------------------------------- #

def derive_nets(df: pd.DataFrame, config: Mapping[str, Any]) -> pd.Series:
    """Numeric blast-radius (net width) as a nullable integer.

    ``config[source_col]`` is coerced with ``pd.to_numeric(..., errors="coerce")``
    so any non-numeric value (``"abc"``, ``""``, ``None``) becomes NA, then cast
    to the nullable ``Int64`` dtype so integers and NA coexist (a plain ``int64``
    cannot hold NA; ``float64`` would render counts as ``5.0``).

    Config keys: ``source_col``. Returns an ``Int64`` :class:`pandas.Series`.
    """
    coerced = pd.to_numeric(df[config["source_col"]], errors="coerce")
    # Round-then-Int64: tolerate a float source (e.g. 5.0) while landing on a
    # clean nullable integer; genuine non-numerics are already NA from coerce.
    return coerced.round().astype("Int64")


# --------------------------------------------------------------------------- #
# has_case  (presence, with a redaction sentinel counting as PRESENT)
# --------------------------------------------------------------------------- #

def derive_has_case(df: pd.DataFrame, config: Mapping[str, Any]) -> pd.Series:
    """Boolean: a case number is PRESENT (value exists, even if redacted).

    Presence, not value: a real case number is present; a redaction sentinel
    from ``config[redaction_sentinels]`` (e.g. ``"***"``) is ALSO present (a
    number exists but was withheld); a blank / ``NaN`` / ``""`` /
    whitespace-only cell is ABSENT. Conflating ``***`` with blank would
    undercount how often the field was actually filled.

    Config keys: ``source_col``; ``redaction_sentinels`` (values that count as
    present despite not being a literal case number). Returns a boolean
    :class:`pandas.Series`.
    """
    source = df[config["source_col"]]
    # The redaction sentinels are read from config to make the ***-counts-as-
    # present contract explicit and validated, even though the presence test
    # below reduces to "non-blank": a sentinel like "***" is non-blank, so it
    # is PRESENT, exactly as required. (A sentinel that stripped to "" would be
    # a contradiction -- a "redaction" indistinguishable from a blank -- so we
    # reject that loudly rather than silently treating it as absent.)
    sentinels = {str(s).strip() for s in config.get("redaction_sentinels", [])}
    blank_sentinels = {s for s in sentinels if s == ""}
    if blank_sentinels:
        raise ValueError(
            "redaction_sentinels must be non-blank (a blank sentinel is "
            "indistinguishable from an absent value)"
        )

    def has_case(value: Any) -> bool:
        if _is_null(value):
            return False
        # Any non-blank token -- a real case number OR a redaction sentinel
        # such as '***' -- means a value is PRESENT; only blank/whitespace is
        # ABSENT.
        return str(value).strip() != ""

    return source.map(has_case).astype(bool)


# --------------------------------------------------------------------------- #
# base_type  (collapse a raw type column via a mapping)
# --------------------------------------------------------------------------- #

def derive_base_type(df: pd.DataFrame, config: Mapping[str, Any]) -> pd.Series:
    """Collapse a raw record-type column to a base category via a mapping.

    Each value is looked up in ``config[mapping]`` (``{raw: base}``). An unmapped
    value falls back to ``config[default]``; when ``default`` is ``None`` the
    original raw value passes through unchanged (passthrough mode).

    Config keys: ``source_col``; ``mapping`` (``{raw: base}``); ``default`` (the
    fallback label, or ``None`` for passthrough). Returns a
    :class:`pandas.Series`.
    """
    source = df[config["source_col"]]
    mapping: Mapping[Any, Any] = config.get("mapping", {})
    default = config.get("default", None)
    passthrough = default is None

    def to_base(value: Any) -> Any:
        if value in mapping:
            return mapping[value]
        return value if passthrough else default

    return source.map(to_base)


# --------------------------------------------------------------------------- #
# temporal  (parse + tz-localize + tz-convert, then derive date / hour / dow)
# --------------------------------------------------------------------------- #

def derive_temporal_et(df: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    """Parse a timestamp column and derive tz-converted date / hour / dow.

    ``config[source_col]`` is parsed with ``pd.to_datetime(...,
    errors="coerce")`` (unparseable -> ``NaT``). The parsed instants are
    localized from ``config[source_tz]`` and converted to ``config[target_tz]``
    via stdlib :mod:`zoneinfo`, so DST is handled correctly (ET is UTC-4 in
    summer, UTC-5 in winter; a single July-vs-January pair lands at the right
    wall-clock hour). A source value that already carries a UTC offset is
    converted directly rather than double-localized. A column that MIXES naive
    and offset-bearing strings (or mixes offsets) does NOT raise: each aware row
    keeps its absolute instant and each naive row is read as ``source_tz``
    wall-clock, so the transform stays total (the ``.dt`` path is guaranteed a
    uniform tz-aware series, never an object/mixed one).

    From the target-zone wall clock it derives:

    * ``date_et`` -- the local calendar :class:`datetime.date` (``None`` for NaT);
    * ``hour_et`` -- the local hour ``0..23`` as nullable ``Int64`` (NA for NaT);
    * ``dow_et``  -- the local day of week as nullable ``Int64``
      (Monday=0 .. Sunday=6, NA for NaT).

    Config keys: ``source_col``; ``source_tz`` (e.g. ``"UTC"``); ``target_tz``
    (e.g. ``"America/New_York"``). Returns a NEW three-column
    :class:`pandas.DataFrame` aligned to ``df``; unparseable rows are NA/NaT
    throughout, never an exception.
    """
    source_tz = config.get("source_tz", "UTC")
    target_tz = config["target_tz"]

    # Parse to a UNIFORM tz-aware (UTC) series so the .dt accessor below can
    # never raise -- even on a column that MIXES naive and offset-bearing strings
    # (or mixed offsets). A bare ``pd.to_datetime(..., format="mixed")`` returns
    # an OBJECT series (or, in pandas 3, raises "Mixed timezones detected") on
    # such input, and the subsequent ``.dt`` then blows up, contradicting the
    # unparseable-row -> NaT promise. ``_parse_to_utc`` instead yields a single
    # ``datetime64[*, UTC]`` series with unparseable rows as NaT.
    converted = _parse_to_utc(df[config["source_col"]], source_tz).dt.tz_convert(
        target_tz
    )

    # date: a NaT must yield None (not pandas' NaT-as-object) for clean equality.
    date_et = converted.dt.date.where(converted.notna(), other=None)
    hour_et = converted.dt.hour.astype("Int64")
    dow_et = converted.dt.dayofweek.astype("Int64")

    return pd.DataFrame(
        {"date_et": date_et, "hour_et": hour_et, "dow_et": dow_et},
        index=df.index,
    )


def _ts_is_aware(value: Any) -> bool:
    """True iff ``value`` parses to a tz-AWARE timestamp (carries an offset).

    Unparseable / null values are treated as not-aware (they become NaT and are
    handled by the naive branch, which also yields NaT). Used to split a mixed
    column into its aware and naive members so each is localized correctly.
    """
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError, OverflowError):
        return False
    return ts.tz is not None


def _parse_to_utc(source: pd.Series, source_tz: str) -> pd.Series:
    """Parse a heterogeneous timestamp column to a uniform ``datetime64[*, UTC]``.

    Total and never raises, even on input that mixes naive strings with
    offset-bearing strings (or mixes offsets):

    * AWARE rows (an offset was present) keep their absolute instant -- parsed
      with ``utc=True`` so every offset is normalized to the same UTC instant.
    * NAIVE rows (no offset) are interpreted as wall-clock time in ``source_tz``,
      then converted to UTC. (A plain ``utc=True`` parse would WRONGLY treat a
      naive string as UTC; when ``source_tz`` is not UTC that shifts the hour, so
      the naive members are localized separately.)
    * Unparseable rows (junk, ``""``, ``None``) -> ``NaT``.

    DST edges in the SOURCE zone (ambiguous/nonexistent naive wall-clock times)
    map to ``NaT`` rather than raising, keeping the transform total.
    """
    s = source.astype("object")

    # Aware rows: utc=True normalizes any offset to a common UTC instant. Naive
    # rows are (wrongly) treated as UTC here, so we overwrite them below.
    aware_utc = pd.to_datetime(s, errors="coerce", utc=True, format="mixed")

    aware_mask = s.map(_ts_is_aware)

    # Naive rows only: recover the wall clock, localize to source_tz, -> UTC.
    naive_wall = pd.to_datetime(s.where(~aware_mask), errors="coerce", format="mixed")
    naive_utc = naive_wall.dt.tz_localize(
        source_tz, ambiguous="NaT", nonexistent="NaT"
    ).dt.tz_convert("UTC")

    # Index-safe combine: aware rows from aware_utc, naive rows from naive_utc.
    return aware_utc.where(aware_mask, other=naive_utc)


# --------------------------------------------------------------------------- #
# orchestrator
# --------------------------------------------------------------------------- #

# Each derivation key maps to (primitive, output-column-spec). A spec that is a
# str names the single output column; a tuple/list names the columns of a
# multi-column DataFrame result.
_SCALAR_DERIVATIONS: dict[str, Any] = {
    "geo": (derive_geo, "geo"),
    "immigration": (derive_immigration, "is_immigration"),
    "nets": (derive_nets, "nets"),
    "has_case": (derive_has_case, "has_case"),
    "base_type": (derive_base_type, "base_type"),
}
_FRAME_DERIVATIONS: dict[str, Any] = {
    "reason": derive_reason,          # -> reason_text, reason_cat
    "temporal": derive_temporal_et,   # -> date_et, hour_et, dow_et
}


def derive_columns(df: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    """Return a COPY of ``df`` with derived columns added per ``config``.

    ``config`` is a mapping ``{derivation_name: derivation_config}`` selecting
    which derivations to run and supplying each one's parameters. The recognized
    derivation names are ``geo``, ``reason``, ``immigration``, ``nets``,
    ``has_case``, ``base_type``, and ``temporal`` (see the corresponding
    ``derive_*`` primitive for each one's config keys and output columns). A
    derivation absent from ``config`` is simply not run; an UNRECOGNIZED key
    raises :class:`ValueError` (a typo must not silently drop a column).

    The input frame is never mutated -- a fresh copy is returned with the new
    columns appended. The ``geo`` output column is named ``geo`` (never ``loc``).
    """
    unknown = set(config) - _KNOWN_DERIVATIONS
    if unknown:
        raise ValueError(
            f"unknown derivation(s) in config: {sorted(unknown)}; "
            f"recognized: {sorted(_KNOWN_DERIVATIONS)}"
        )

    result = df.copy()
    for name, cfg in config.items():
        if name in _SCALAR_DERIVATIONS:
            func, out_col = _SCALAR_DERIVATIONS[name]
            result[out_col] = func(df, cfg)
        else:  # frame-producing derivation
            func = _FRAME_DERIVATIONS[name]
            produced = func(df, cfg)
            for col in produced.columns:
                result[col] = produced[col]
    return result

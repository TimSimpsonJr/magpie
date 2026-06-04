"""Cross-source synthesis for the Phase 4 analysis recipe.

Where :mod:`scripts.recipe` runs the 13-point per-source pass and emits one
findings object per source, this module folds a *list* of those findings into a
single cross-source picture: which external actors recur across sources, the
pooled aggregates, and a handful of recurrence theses. It is the synthesis
barrier the skill hits after fanning the recipe out over every source.

Two rigor stances are load-bearing and pinned by ``tests/test_rollup.py``:

* **External-only recurrence -- no entity-kind mixing.** A "recurring actor" is
  an EXTERNAL actor (e.g. an out-of-state agency) that appears in >= 2 sources,
  tallied SOLELY from each source's ``cross_agency.external_actor_counts``. There
  is deliberately NO fallback to ``mega_users`` / top-users: mixing in-corpus
  heavy users with external agencies would manufacture false cross-source
  recurrence (an actor "recurring" only because two different entity kinds
  happened to share a label). One actor model, one input field.

* **Pooled, honest denominators -- never a mean of percentages.** A cross-source
  rate is the POOLED rate ``Σ(numerator) / Σ(denominator)`` over the sources that
  actually ran the check, which is the truthful figure when sources differ in
  size. The arithmetic mean of per-source percentages is also reported, but
  named ``unweighted_mean_*`` so it can never be mistaken for the real rate. An
  undefined pooled rate (no contributing source) is ``None``, never a fake
  ``0.0`` that would read as "measured and zero".

A third stance follows from the recipe's "leads, not verdicts" contract: a check
that a source SKIPPED (or never ran at all) contributes NOTHING to that
aggregate -- it neither crashes the roll-up nor pollutes a numerator or
denominator. Every field is read with :meth:`dict.get` and a sensible default so
a malformed or partial findings object degrades gracefully.

The module is pure and deterministic. It consumes ONLY the findings dicts -- no
DataFrame, no file IO, no clock, no randomness, no network -- mirroring
``stats.py`` / ``derive.py`` / ``data_quality.py``, so it can be golden-tested
against documented values and reused across data sources.
"""

from __future__ import annotations

import numbers
from typing import Any, Mapping, Sequence

# A check whose ``status`` is anything other than this ran-cleanly marker
# (``"skipped"``, ``"partial"``, a missing status, ...) is treated as not having
# produced a trustworthy figure for the pooled aggregates, so it contributes
# nothing rather than polluting a numerator / denominator.
_STATUS_OK = "ok"

# Thresholds for the recurrence theses. These are framing cut-offs for "this
# pattern recurs strongly enough to call out", not measurement parameters; a
# source must clear the bar to *support* a thesis. Kept here (not hardcoded
# mid-function) so they are visible and overridable via ``config``.
_DEFAULT_OUT_OF_STATE_THESIS_PCT = 0.5
_DEFAULT_GINI_THESIS = 0.7


def rollup(
    findings_list: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fold per-source findings into a cross-source recurrence synthesis.

    ``findings_list`` is a sequence of per-source findings objects in the shape
    :func:`scripts.recipe.run_recipe` emits::

        {"source_id": str, "n_records": int,
         "checks": {check_name: result_dict, ...}}

    where each ``result_dict`` carries a ``"status"`` plus check-specific keys.
    ``config`` may carry thesis thresholds (``out_of_state_thesis_pct``,
    ``gini_thesis``); it is otherwise unused (the roll-up takes no jurisdiction
    or column knowledge -- that all lives in the per-source findings).

    Returns a dict with EXACTLY these top-level keys:

    * ``n_sources`` -- the number of source findings rolled up.
    * ``recurring_external_actors`` -- external actors seen in >= 2 sources,
      each ``{actor, n_sources, total_count, sources}``; see
      :func:`_recurring_external_actors`.
    * ``aggregate`` -- pooled cross-source totals and rates; see
      :func:`_aggregate`.
    * ``theses`` -- recurrence theses derived from the aggregate and recurring
      actors, each ``{thesis, n_sources_supporting, sources}`` with
      ``n_sources_supporting == len(sources)``; see :func:`_theses`.

    Pure and deterministic; defensive against missing keys. An empty
    ``findings_list`` is safe: zero sources, no recurring actors, zeroed totals,
    and an undefined pooled rate reported as ``None`` (never a fake ``0.0``).
    """
    cfg = config or {}
    findings = list(findings_list)

    recurring = _recurring_external_actors(findings)
    aggregate = _aggregate(findings)
    theses = _theses(findings, aggregate, recurring, cfg)

    return {
        "n_sources": len(findings),
        "recurring_external_actors": recurring,
        "aggregate": aggregate,
        "theses": theses,
    }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _source_id(finding: Mapping[str, Any]) -> Any:
    """The source's id, defaulting to ``None`` for a malformed finding."""
    return finding.get("source_id")


def _check(finding: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    """The named check's result dict for a source, or ``{}`` if absent.

    Returns an empty mapping (never ``None``) so callers can chain ``.get``
    without a guard: a source that never ran the check reads as "no signal".
    """
    checks = finding.get("checks") or {}
    result = checks.get(name)
    return result if isinstance(result, Mapping) else {}


def _ran_ok(check: Mapping[str, Any]) -> bool:
    """True iff a check ran cleanly (``status == "ok"``).

    A ``"skipped"`` / ``"partial"`` / status-less check returns False, so it is
    excluded from the pooled aggregates -- no crash, no denominator pollution.
    An absent check (``{}`` from :func:`_check`) is likewise excluded.
    """
    return check.get("status") == _STATUS_OK


# --------------------------------------------------------------------------- #
# recurring external actors
# --------------------------------------------------------------------------- #

def _recurring_external_actors(
    findings: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """External actors appearing in >= 2 sources, tallied across sources.

    The ONLY input is each source's
    ``checks["cross_agency"]["external_actor_counts"]`` -- a ``{actor: count}``
    mapping of actors seen on that source's external rows. An actor is
    "recurring" when it appears in two or more *distinct* sources; an actor in a
    single source is excluded (a one-source actor cannot evidence cross-source
    recurrence). There is NO fallback to ``mega_users`` / top-users: that would
    mix entity kinds and manufacture false recurrence.

    Each recurring entry is ``{"actor", "n_sources", "total_count", "sources"}``
    where ``total_count`` is the count summed across the sources the actor
    appears in and ``sources`` is the list of those source ids. Counts are
    coerced defensively (a non-numeric count contributes 0) so a malformed
    findings object cannot raise. The result is sorted by ``n_sources``
    descending, then ``total_count`` descending, with ``actor`` as a final
    deterministic tie-break.

    Only the ``cross_agency`` check's status gates inclusion: a source whose
    ``cross_agency`` was skipped contributes no actors. A clean source with an
    empty ``external_actor_counts`` simply adds nothing.
    """
    # actor -> {"total_count": int|float, "sources": [source_id, ...]}. The
    # source list preserves first-seen order and never double-counts a source
    # (an actor listed twice within one source's dict still counts as one
    # source for recurrence breadth).
    tally: dict[Any, dict[str, Any]] = {}

    for finding in findings:
        cross = _check(finding, "cross_agency")
        if not _ran_ok(cross):
            continue
        counts = cross.get("external_actor_counts") or {}
        if not isinstance(counts, Mapping):
            continue
        source_id = _source_id(finding)
        for actor, count in counts.items():
            entry = tally.setdefault(actor, {"total_count": 0, "sources": []})
            entry["total_count"] += _as_number(count)
            if source_id not in entry["sources"]:
                entry["sources"].append(source_id)

    recurring = [
        {
            "actor": actor,
            "n_sources": len(entry["sources"]),
            "total_count": entry["total_count"],
            "sources": entry["sources"],
        }
        for actor, entry in tally.items()
        if len(entry["sources"]) >= 2
    ]

    # Breadth first (how many sources), then volume (summed count), then actor
    # label for a stable, reproducible ordering.
    recurring.sort(
        key=lambda row: (-row["n_sources"], -row["total_count"], str(row["actor"]))
    )
    return recurring


def _as_number(value: Any) -> float | int:
    """Coerce a count to a number, treating anything non-numeric as ``0``.

    Any real number is accepted, including numpy real scalars (``np.int64`` /
    ``np.float64``), which register with :class:`numbers.Real` -- so a non-native
    count that leaks into a findings object is summed rather than silently
    dropped, and without importing numpy here. Booleans are deliberately excluded
    (``True`` is not a count of 1 here) and a string / ``None`` / unparseable
    value contributes ``0`` rather than raising, keeping the tally total over a
    possibly-dirty findings object.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, numbers.Real):
        return value
    return 0


# --------------------------------------------------------------------------- #
# aggregate
# --------------------------------------------------------------------------- #

def _aggregate(findings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pooled cross-source totals and rates over the sources that ran each check.

    Returns a dict with:

    * ``total_records`` -- ``n_records`` summed across ALL sources (every source
      counts, regardless of which checks it ran).
    * ``total_immigration`` -- ``checks["immigration"]["immigration"]`` summed
      over sources whose immigration check ran cleanly (``status == "ok"``).
    * ``out_of_state_pct_total`` -- the POOLED out-of-state rate,
      ``Σ(out_of_state) / Σ(total)`` over sources whose out_of_state check ran
      cleanly; ``None`` when no such source exists or the pooled denominator is
      zero (an undefined rate, never a fake ``0.0``).
    * ``unweighted_mean_out_of_state_pct`` -- the arithmetic mean of per-source
      ``out_of_state_pct`` over the ok sources, named explicitly so it cannot be
      mistaken for the real pooled rate; ``None`` when no ok source exists.
    * ``sources_truncated`` -- ids of sources whose ``truncation`` check ran and
      reported ``truncated is True`` (in source order).

    A check that is absent or skipped contributes nothing: it is never summed
    into a total and never added to a pooled denominator. All arithmetic is
    defensive (non-numeric submetrics coerce to ``0``) so a partial findings
    object degrades gracefully rather than raising.
    """
    total_records = 0
    total_immigration = 0

    out_of_state_num = 0  # Σ out_of_state over ok sources
    out_of_state_den = 0  # Σ total over ok sources
    out_of_state_pcts: list[float] = []  # per-source pct over ok sources

    sources_truncated: list[Any] = []

    for finding in findings:
        # total_records counts EVERY source, even one that ran no rate checks.
        total_records += _as_number(finding.get("n_records"))

        immigration = _check(finding, "immigration")
        if _ran_ok(immigration):
            total_immigration += _as_number(immigration.get("immigration"))

        oos = _check(finding, "out_of_state")
        if _ran_ok(oos):
            out_of_state_num += _as_number(oos.get("out_of_state"))
            out_of_state_den += _as_number(oos.get("total"))
            pct = oos.get("out_of_state_pct")
            if isinstance(pct, (int, float)) and not isinstance(pct, bool):
                out_of_state_pcts.append(float(pct))

        truncation = _check(finding, "truncation")
        if _ran_ok(truncation) and truncation.get("truncated") is True:
            sources_truncated.append(_source_id(finding))

    return {
        "total_records": total_records,
        "total_immigration": total_immigration,
        "out_of_state_pct_total": _safe_ratio(out_of_state_num, out_of_state_den),
        "unweighted_mean_out_of_state_pct": _mean_or_none(out_of_state_pcts),
        "sources_truncated": sources_truncated,
    }


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    """``numerator / denominator`` as a float, or ``None`` for a zero denominator.

    A zero (or absent) pooled denominator means the rate is UNDEFINED -- there
    were no observations to pool -- so it is reported as ``None`` rather than a
    misleading ``0.0`` that would read as a measured-and-empty rate.
    """
    if denominator == 0:
        return None
    return numerator / denominator


def _mean_or_none(values: Sequence[float]) -> float | None:
    """Arithmetic mean of ``values``, or ``None`` for an empty sequence.

    An empty input means there is nothing to average (no source ran the check),
    so the mean is undefined and reported as ``None`` -- consistent with the
    "no fake 0.0" stance for undefined figures.
    """
    if not values:
        return None
    return sum(values) / len(values)


# --------------------------------------------------------------------------- #
# theses
# --------------------------------------------------------------------------- #

def _theses(
    findings: Sequence[Mapping[str, Any]],
    aggregate: Mapping[str, Any],  # noqa: ARG001 - kept for a stable, extensible signature
    recurring: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Derive recurrence theses from the per-source signals and recurring actors.

    Each thesis is ``{"thesis": str, "n_sources_supporting": int, "sources":
    [source_id, ...]}`` with the invariant ``n_sources_supporting ==
    len(sources)`` -- the count is always the length of the supporting-source
    list, never an independent number. A thesis is emitted ONLY when it has at
    least one supporting source, so every returned thesis names real evidence.

    Three recurrence patterns are surfaced (each gated by a config-overridable
    threshold, defaulting to :data:`_DEFAULT_OUT_OF_STATE_THESIS_PCT` /
    :data:`_DEFAULT_GINI_THESIS`):

    * **Out-of-state dominance** -- sources whose ``out_of_state.out_of_state_pct``
      is at or above ``out_of_state_thesis_pct`` (ok sources only).
    * **Concentration** -- sources whose ``statistical_patterns.gini`` is at or
      above ``gini_thesis`` (ok sources only).
    * **Recurring external actor** -- if any external actor recurs across >= 2
      sources, the single broadest one (already first after the recurrence sort)
      becomes a thesis supported by exactly the sources it appears in.

    ``aggregate`` is accepted for a stable, extensible signature (future
    aggregate-derived theses) even though the current theses are computed
    directly from the findings and ``recurring`` so each thesis can name its own
    supporting sources. The result is non-empty for the standard two-source
    fixture (both sources clear the out-of-state and concentration bars and share
    an external actor).
    """
    oos_threshold = config.get(
        "out_of_state_thesis_pct", _DEFAULT_OUT_OF_STATE_THESIS_PCT
    )
    gini_threshold = config.get("gini_thesis", _DEFAULT_GINI_THESIS)

    theses: list[dict[str, Any]] = []

    # Out-of-state dominance recurs across sources where the share is high.
    oos_sources = [
        _source_id(f)
        for f in findings
        if _ran_ok(_check(f, "out_of_state"))
        and _at_least(_check(f, "out_of_state").get("out_of_state_pct"), oos_threshold)
    ]
    if oos_sources:
        theses.append(
            _thesis(
                f"Out-of-state searches dominate (>= {oos_threshold:g} of "
                f"classified records) across {len(oos_sources)} source(s)",
                oos_sources,
            )
        )

    # High concentration (a few actors drive most volume) recurs across sources.
    concentrated_sources = [
        _source_id(f)
        for f in findings
        if _ran_ok(_check(f, "statistical_patterns"))
        and _at_least(_check(f, "statistical_patterns").get("gini"), gini_threshold)
    ]
    if concentrated_sources:
        theses.append(
            _thesis(
                f"Activity is highly concentrated (Gini >= {gini_threshold:g}) "
                f"across {len(concentrated_sources)} source(s)",
                concentrated_sources,
            )
        )

    # A single external actor recurring across sources is itself a thesis. The
    # recurrence list is already sorted broadest-first, so take the leader.
    if recurring:
        leader = recurring[0]
        theses.append(
            _thesis(
                f"External actor {leader['actor']!r} recurs across "
                f"{leader['n_sources']} sources",
                list(leader["sources"]),
            )
        )

    return theses


def _at_least(value: Any, threshold: float) -> bool:
    """True iff ``value`` is a real number at or above ``threshold``.

    A ``None`` / non-numeric submetric (an undefined rate or gini) is treated as
    NOT clearing the bar rather than raising, so a source that left the metric
    undefined simply does not support the thesis.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value >= threshold


def _thesis(text: str, sources: Sequence[Any]) -> dict[str, Any]:
    """Build a thesis entry, enforcing ``n_sources_supporting == len(sources)``.

    Centralizes the invariant so no call site can set a supporting count that
    disagrees with the actual list of supporting sources.
    """
    source_list = list(sources)
    return {
        "thesis": text,
        "n_sources_supporting": len(source_list),
        "sources": source_list,
    }

---
name: analysis-recipe
description: This skill should be used when the user asks to "run the analysis recipe", "run the 13-point checklist", "run the per-source investigative pass", "analyze this FOIA/audit-log source for out-of-state / immigration / pretext / mega-users / co-travel", "roll up findings across multiple sources/agencies", or "test the recurrence thesis across sources". Runs the parameterized 13-point per-source pass and the cross-source rollup over already-derived FOIA / audit-log data.
version: 0.1.0
---

# analysis-recipe

Run Magpie's repeatable investigative pass: a fixed, parameterized **13-point
checklist** applied identically to one FOIA / audit-log source, then a
**cross-source rollup** that tests whether patterns and actors recur across
sources. This is the Track A flagship's analysis layer — it sits on top of
`dataset-analyze` (which loads, quality-gates, and derives each source) and feeds
its findings to Librarian.

Two deterministic scripts do the work; the agent orchestrates the fan-out:

- `scripts/recipe.py::run_recipe(df, config)` — the per-source 13-point pass.
- `scripts/rollup.py::rollup(findings_list)` — the cross-source synthesis.

Both are pure and golden-tested. The verified-API contract and the exact
findings / check / rollup schemas live in `references/prior-art.md` (the Phase 4
research gate) — consult §3 before changing a call or a config key.

## Per-source pass

For each source, first produce a clean, derived DataFrame via the
**`dataset-analyze`** pipeline (load → data-quality gate → derive: `geo`,
`reason_cat`/`reason_text`, `is_immigration`, `nets`, `has_case`, `base_type`,
`date_et`/`hour_et`). Then run the recipe:

```python
from scripts.recipe import run_recipe

config = {
    "source_id": "simpsonville-network-audit",
    "checks": {
        "truncation": {},
        "out_of_state": {"geo_col": "geo", "out_label": "OOS", "unknown_label": "UNK"},
        "immigration": {"text_col": "reason_text", "keywords": ["ice", "immigration", "deportation", "cbp"]},
        "pretext": {"text_col": "reason_text", "keywords": ["traffic", "registration", "equipment"]},
        "pii": {"text_cols": ["reason_text"]},
        "accountability": {"case_col": "has_case", "group_col": "geo"},
        "co_travel": {"text_col": "reason_text", "keywords": ["convoy", "co-travel", "caravan"]},
        "blast_radius": {"nets_col": "nets", "severity_col": "base_type"},
        "mega_users": {"user_col": "agency"},
        "operations": {"user_col": "agency", "date_col": "date_et", "hour_col": "hour_et"},
        "ai_moderation": {"hour_col": "hour_et", "user_col": "agency", "timestamp_col": "ts"},
        "cross_agency": {"user_col": "agency", "external_geo_col": "geo", "external_label": "OOS"},
        "statistical_patterns": {"user_col": "agency"},
    },
}
findings = run_recipe(df, config)
```

`config` is `{"source_id", "checks": {check_name: check_cfg}}`. Only the named
checks run; an unknown check name raises `ValueError` (a typo must not silently
drop a check). The result is a findings object —
`{"source_id", "n_records", "checks": {name: result}}` — where every result
carries a `status` (`ok` / `partial` / `skipped`) and a one-line `summary`.

**The 13 checks** (see `references/prior-art.md` §3.2 for each one's config keys
and return fields): truncation, out_of_state, immigration, pretext, pii,
accountability, co_travel, blast_radius, mega_users, operations, ai_moderation,
cross_agency, statistical_patterns.

**Statistical patterns are a first-class per-source step, not an afterthought.**
Gini concentration (`statistical_patterns`), net-width-by-severity
(`blast_radius`), the automation signature and burstiness (`ai_moderation`) run
on every pass via `scripts/stats.py` — distributional evidence is part of the
checklist, computed alongside the categorical counts, never bolted on later.

**Reuse the word-boundary keyword guardrail.** The keyword checks (immigration,
pretext, co_travel) categorize free text through `derive.keyword_mask` — the
single `\b`-anchored matcher — so "Assist local police" / "community service" /
"Department of Justice" never count off the substring "ice". Do not hand-roll a
substring test; a false keyword hit becomes a false published finding.

## Fan-out across sources, then roll up

For a SINGLE source, run `run_recipe` and pass the one findings object to
`rollup` (or report it directly). For MULTIPLE sources, run the SAME per-source
pass over each source independently, collect the findings objects, then
synthesize once:

```python
from scripts.rollup import rollup
summary = rollup([findings_a, findings_b, findings_c])
```

**Prefer the Workflow tool for the fan-out when orchestration is available.**
The per-source work is independent, so fan it out: a per-source stage (one agent
per source — each runs `dataset-analyze` prep + `run_recipe` and returns its
findings dict) run concurrently, then a synthesis **barrier** that calls
`rollup` over all the collected findings (the rollup genuinely needs every
source's findings before it can test recurrence). In Workflow terms this is
`parallel(sources.map(...))` followed by a single `rollup` step.

**Degrade gracefully when Workflow is unavailable.** If the session has not
opted into Workflow orchestration, process the sources sequentially (or dispatch
one subagent per source with the Agent tool) and call `rollup` over the
collected findings. The final result is identical — only the wall-clock differs.
Do not hard-depend on the Workflow tool.

`rollup` returns `{"n_sources", "recurring_external_actors", "aggregate",
"theses"}`:

- **`recurring_external_actors`** — external actors (e.g. out-of-state agencies)
  appearing in ≥ 2 sources, tallied SOLELY from each source's
  `cross_agency.external_actor_counts`. This is the recurrence thesis: one
  out-of-state agency searching many jurisdictions' data.
- **`aggregate`** — POOLED cross-source rates (`out_of_state_pct_total =
  Σnum/Σdenom`, not a mean of percentages), plus totals and `sources_truncated`.
- **`theses`** — recurrence statements, each naming the exact sources that
  support it.

## Output via Librarian (hub-and-spoke)

Route findings through **Librarian** (a required dependency): write hub-and-spoke
findings notes — a hub note per source/rollup linking spoke notes per check —
into the configured vault, or portable Markdown when no vault is set. Tabular
payloads (per-agency counts, exposure tallies) go to CSV alongside the notes.

Keep PII out of published notes: redact third-party PII to initials via
`redact-output`, and route full exhibits to local, non-served files only. The
recipe's `pii` check is a structured-PII PRESENCE indicator (A-numbers, SSNs,
phones, emails) — authoritative semantic PII tallies (names, weighted by count)
are `pii-sweep`'s job (Phase 5), not this pass.

## Rigor guardrails (preserve across the pass)

- **Word-boundary keyword matching.** Confirm a categorization is a whole-word
  hit, not a substring (the `ICE` / `polICE` trap), before treating it as a
  finding. The keyword checks route through `derive.keyword_mask`.
- **Leads, not verdicts; never a fake zero.** A missing required column → the
  check is `skipped` (with a reason), recorded in the findings, never a crash. An
  undefined median / share / Gini (zero observations) → the value is `None` with
  `status: partial` and an observation count — never a `0.0` that reads as a real
  measurement.
- **Presence is not value.** `***` is a case number PRESENT (withheld); blank /
  `NaN` is ABSENT (`accountability` routes through `stats.presence_rate`).
- **Honest denominators.** `out_of_state` reports the share over the total AND
  over the known rows; the rollup pools numerators and denominators rather than
  averaging per-source percentages.
- **External-only recurrence.** The rollup keys recurrence off external actors
  only — an in-state actor cannot manufacture a cross-source finding, and there
  is no mega-user/top-user fallback that would mix entity kinds.

## Resources

- **`references/prior-art.md`** — Phase 4 research gate: verified pandas
  aggregation APIs, the Workflow fan-out idiom, and §3 the authoritative findings
  / 13-check / rollup contracts (config keys + return fields + the plan-review
  fixes). Read §3 before changing a check's contract.
- **`scripts/recipe.py`, `scripts/rollup.py`** — the per-source pass and the
  cross-source synthesis (detailed module + function docstrings).
- **`scripts/stats.py`, `scripts/derive.py`, `scripts/data_quality.py`** — the
  reused primitives (stats, the keyword guardrail, the truncation gate).
- **`dataset-analyze` skill** — the upstream per-source pipeline (load →
  quality-gate → derive) that produces the DataFrame this recipe consumes.
- **Librarian** — the shared notes layer this skill's findings are written
  through (hub-and-spoke, vault-or-portable).

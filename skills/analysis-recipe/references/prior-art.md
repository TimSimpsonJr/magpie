# analysis-recipe — Prior-Art & Verified-API Reference (Phase 4 research gate)

> **Status:** Tier-2 research gate for Magpie Phase 4 (`analysis-recipe` + cross-source
> `rollup`). This is the spec the Phase 4 implementers build against (TDD). Every API
> claim below was verified on **2026-06-04** by **introspecting / executing the pinned dev
> `.venv`** (the source of truth — it matches `requirements-dev.txt`) and/or live Claude
> Code docs — **not** from model memory. Where a doc or a web summary contradicted an
> assumption, it is called out in **bold**.
>
> **Verification methods used (so a re-checker can reproduce):**
> - `pandas` / `numpy` aggregation surface — introspected + executed in the project
>   `.venv` via `mise exec -- python` (`signature()` + real DataFrame runs).
> - pandas-3.0 breaking-change scan — pandas.pydata.org "What's new in 3.0.0" (web) cross-
>   checked against the executed behavior.
> - Workflow-tool fan-out idiom for a skill — live Claude Code docs (workflows / skills).
> - Context7 was **NOT reachable this session** (MCP server not connected); the `.venv`
>   introspection is the stronger authority for the *installed* API anyway, and is what
>   Phase 3's gate also relied on. Re-run a Context7 pass if the pins move.

## TL;DR decisions

1. **No new Python dependency.** `recipe.py` and `rollup.py` are pure `pandas` + `numpy`
   + stdlib, and REUSE the already-pinned `scripts/stats.py`, `scripts/derive.py`,
   `scripts/data_quality.py`. The Phase 3 pins (`pandas==3.0.3`, `numpy==2.4.6`) cover it.
   Nothing to add to `requirements-dev.txt`.
2. **Reuse the word-boundary keyword guardrail — do not reimplement it.** Promote
   `derive.py`'s private `_compile_keyword_regex` to a public `compile_keyword_regex`
   plus a convenience `keyword_mask(series, keywords) -> bool Series`, and have the
   recipe's keyword checks (immigration / pretext / co-travel) call it. One implementation
   of the ICE/"polICE" defense, used everywhere. (Only existing-module edit in Phase 4.)
3. **Statistical patterns are a first-class per-source check, not an afterthought** — Gini
   / net-width-by-severity / automation-signature / burstiness are checks #8/#11/#13 of
   the 13, computed every pass via `scripts/stats.py`.
4. **Fan-out is the skill's job, synthesis is `rollup.py`'s.** The per-source pass
   (`recipe.py`) is deterministic Python; the SKILL.md instructs the agent to fan it out
   per source (Workflow tool when available, sequential/subagents otherwise) and then call
   `rollup.py`. The Python stays orchestration-free and golden-testable.

---

## 1. pandas 3.0.3 aggregation surface — ✅ verified by execution in `.venv`

All run against `pandas 3.0.3 / numpy 2.4.6` (the pinned `.venv`).

| API | Verified behavior | Used by |
|---|---|---|
| `Series.value_counts(normalize=False, sort=True, ascending=False, bins=None, dropna=True)` | Sorted **descending**, result `Series` indexed by value, `name="count"`; drops NaN by default, `dropna=False` keeps an `NaN`/`<NA>` bucket | mega_users, cross_agency, operations |
| `counts.nlargest(n)` / `counts.head(n)` | Top-n by value | mega_users top-N |
| `counts.idxmax()` / `counts.max()` | Largest actor's label / count | mega_users largest_user |
| `Series.nunique(dropna=True)` | Distinct count; `dropna=False` counts NA as a level | operations, cross_agency |
| `Series.dropna().unique().tolist()` | Distinct present values (sortable) | cross_agency agency set |
| `df.groupby(col).size()` | Per-group row count `Series` | operations per-day, burstiness |
| `df.groupby(cat)[val].median().sort_values(ascending=False)` | Per-category median, high→low (this **is** `stats.median_by_category`) | blast_radius by-severity |
| `dt.max() - dt.min()` → `Timedelta`; `.days` | Date-span in days | operations span |
| `Series.dt.date.value_counts()` | Per-calendar-day counts | operations busiest day |
| `pd.Series(Int64Array).value_counts(dropna=False)` | Works on a nullable `Int64` (keeps `Int64` dtype, `<NA>` bucket) | nets / derived-col safety |

**pandas-3.0 breaking changes scanned — none affect the planned code:**
- `groupby(..., observed=True)` is now the default. Only matters for **Categorical** groupers; the recipe groups on plain object/Int64 columns, so behavior is unchanged. (Noted so a future Categorical column doesn't surprise us.)
- `Series.value_counts()` on object dtype **no longer infers dtype** — irrelevant to counting.
- `str.contains/startswith/endswith` `na=` handling changed — **not used**; keyword matching goes through `derive`'s compiled-regex `.map`, not the `.str` accessor.
- `DataFrame.fillna(method=)` is **removed** (use `.ffill()`) — already known from Phase 3; the recipe does no forward-fill.

> **Rigor note carried from `stats.py`:** `gini` / `top_k_share` / `bottom_half_share`
> already drop ALL pandas null sentinels before computing, so feeding them a per-actor
> `value_counts()` (or a nullable `Int64` `nets` column) is safe — no `nan` poisoning.

---

## 2. Workflow-tool fan-out for the SKILL.md — ✅ verified against live Claude Code docs

- **A skill's instructions DO count as Workflow opt-in.** Claude Code treats "a skill or
  command whose instructions tell you to call Workflow" as a legitimate opt-in, equivalent
  to the user typing "use a workflow". So the SKILL.md may direct the agent to fan out with
  the Workflow tool.
- **Pattern for "same deterministic step over N sources, then synthesize":** a per-source
  stage run concurrently, then a **barrier** before synthesis (the rollup genuinely needs
  ALL per-source findings to test recurrence). In Workflow terms: `parallel(sources.map(s
  => () => agent(run recipe on s)))` → then `rollup(allFindings)`. (Use `pipeline()` only
  if a source had multiple *independent* per-source stages; here each source is one recipe
  call, so `parallel` + a synthesis barrier is the clean shape.)
- **Graceful degradation is mandatory.** The Workflow tool requires opt-in and may be off.
  The skill therefore documents BOTH: Workflow fan-out when available, and a sequential /
  `Agent`-tool-subagent fallback otherwise — identical final result, just slower. The skill
  stays intent-level (what to analyze + how to synthesize); it does **not** hand-code a
  workflow script.

---

## 3. Phase 4 component contracts (the build-against spec)

> **Plan-review fixes applied (Codex, 2026-06-04, verdict PROCEED-WITH-FIXES).**
> The tests in `tests/test_recipe.py` / `tests/test_rollup.py` are the AUTHORITATIVE
> spec; where the tables below differ, follow the tests + these amendments:
>
> - **(C1) One consistent actor model; external-only recurrence.** `check_cross_agency`
>   takes `user_col` and emits `actor_counts` (the FULL machine field, never truncated)
>   plus `external_actor_counts` / `external_actors` / `n_external_actors` (actors seen on
>   external rows). `rollup` computes recurrence ONLY from `external_actor_counts` →
>   output key `recurring_external_actors` (`{actor, n_sources, total_count, sources}`).
>   There is NO `mega_users.top_users` fallback (it mixed actors with agencies and
>   manufactured false recurrence).
> - **(C2) Pooled, honest denominators.** Every rate-bearing check emits its numerator AND
>   denominator (e.g. out_of_state emits `out_of_state`,`in_state`,`unknown`,`total`).
>   `rollup` reports POOLED rates — `out_of_state_pct_total = Σnum/Σdenom` — and names any
>   mean-of-percentages `unweighted_mean_*` so it can't be mistaken for the real rate.
> - **(C3) `status ∈ {ok, partial, skipped}` + nullable submetrics.** A check that runs but
>   leaves a submetric undefined is `partial` (with a `reason`); an UNDEFINED median / share
>   / gini (n_observed == 0) is `None`, never a fake `0.0`. Distributional checks expose the
>   observation count (`n_with_nets` / `n_actors` / `n_users`). `check_ai_moderation` without
>   a `timestamp_col` is `partial` (signature runs, `max_same_second` is `None`).
> - **(I1)** the `actor_counts` machine field is NOT bounded (truncating it would drop a
>   shared actor → false negative); any presentation cap is a separate display field.
> - **(I2)** the speculative `sources_traffic_gt_homicide` aggregate is REMOVED from the
>   default; a blast-radius inversion roll-up is opt-in via rollup config naming the
>   category pair (no hardcoded jurisdiction).
> - **(I3) PII is record-level + high-precision.** `records_with_pii` counts ROWS (deduped
>   across `text_cols`); `records_by_pattern` is rows-per-pattern. Default patterns are
>   `a_number`, `ssn`, `phone`, `email` only — NO bare-date pattern (an incident date is not
>   exposure); DOB detection is opt-in via `patterns`.
> - **(I5) `check_operations` definitions locked.** `records_per_active_day =
>   total_records / n_active_days`; `busiest_day` / `busiest_hour` ties break to the
>   EARLIEST (smallest date / hour). `date_start`/`date_end` are ISO strings.
> - **(NICE)** the new `derive.compile_keyword_regex` / `keyword_mask` get direct tests
>   (empty list → None/all-False, multi-word phrase, regex-escape literal, index-aligned).

### 3.0 Shared conventions
- Every module is **pure & deterministic** (pandas/numpy/stdlib only; no IO, clock,
  randomness, network), mirroring `stats.py` / `derive.py` / `data_quality.py`.
- `recipe.py` is the **orchestrator layer**: unlike its siblings (which import no
  neighbors) it MAY import `stats`, `data_quality`, and `derive`'s public keyword helper —
  composing them is its whole job.
- **Config-driven, zero hardcoded jurisdiction** (same stance as `derive.py`): home state,
  keyword vocabularies, column names, severity column, thresholds all come from `config`. A
  Simpsonville run and any other run differ only in config.
- **Leads, not verdicts** (same stance as `data_quality.py`): a check whose required
  column is absent returns `{"status": "skipped", "reason": ...}` — recorded in the
  findings, never a silent drop and never a crash. Present-but-empty data yields zeroed
  metrics, not an exception.

### 3.1 `derive.py` change (single existing-module edit)
Promote the guardrail to a public, reusable surface:
```python
def compile_keyword_regex(keywords) -> re.Pattern | None: ...   # was _compile_keyword_regex
_compile_keyword_regex = compile_keyword_regex                  # back-compat alias (internal callers)

def keyword_mask(series, keywords) -> pd.Series:                # NEW
    """Boolean Series: True where a cell contains ANY keyword on a WORD boundary
    (case-insensitive, null/blank -> False). The ICE/'polICE' guardrail, reusable."""
```
Existing 30 derive tests must stay green (pure addition + alias).

### 3.2 `recipe.py` — the 13-point parameterized per-source pass
`run_recipe(df, config) -> findings`, where
`config = {"source_id": str, "checks": {check_name: check_cfg, ...}}`.

Findings object:
```python
{
  "source_id": str,
  "n_records": int,
  "checks": { "<name>": <result>, ... },   # only the checks named in config["checks"]
}
```
Each `<result>` carries `"status": "ok" | "skipped"`, a one-line `"summary"`, plus the
numbers below. An **unknown check name → ValueError** (a typo must not silently drop a
check — same guard as `derive_columns`). Each check is a small pure fn with its own test.

| # | `check_*` | key config | returns (besides status/summary) | reuses |
|---|---|---|---|---|
| 1 | `check_truncation` | `ceiling?` | `truncated, n_rows, ceiling` | `data_quality.check_truncation` |
| 2 | `check_out_of_state` | `geo_col="geo", out_label, in_label, unknown_label` | `total, in_state, out_of_state, unknown, out_of_state_pct, out_of_state_pct_of_known` | — |
| 3 | `check_immigration` | `flag_col="is_immigration"` **or** `text_col,keywords`; `subtype_col?` | `immigration, immigration_pct, by_subtype?` | `derive.keyword_mask` |
| 4 | `check_pretext` | `text_col,keywords` **or** `cat_col,pretext_cats` | `pretext, pretext_pct` | `derive.keyword_mask` |
| 5 | `check_pii` | `text_cols, patterns?` | `records_with_pii, pii_pct, by_pattern, note` | stdlib `re` |
| 6 | `check_accountability` | `case_col="has_case", group_col?` | `with_case, without_case, no_case_pct, by_group?` | `stats.presence_rate` |
| 7 | `check_co_travel` | `text_col, keywords` | `co_travel, co_travel_pct` | `derive.keyword_mask` |
| 8 | `check_blast_radius` | `nets_col="nets", severity_col?` | `median, max, total_exposure, n_with_nets, by_severity?` | `stats.median_by_category` |
| 9 | `check_mega_users` | `user_col, top_n=10, top_frac=0.01` | `n_users, largest_user, top_users, top_frac_share` | `stats.top_k_share` |
| 10 | `check_operations` | `user_col?, date_col="date_et", hour_col="hour_et"` | `total_records, n_users?, date_start, date_end, span_days, records_per_day_mean, busiest_day, busiest_hour` | — |
| 11 | `check_ai_moderation` | `hour_col="hour_et", user_col, timestamp_col?, overnight_threshold=0.5, day_start=6, day_end=18` | `n_actors, n_overnight_heavy, overnight_heavy_actors, overnight_caveat, max_same_second?, burst_size_distribution?` | `stats.automation_signature` + `stats.burstiness` |
| 12 | `check_cross_agency` | `user_col, external_geo_col="geo", external_label="OOS", max_list=200` | `n_agencies, n_external_agencies, agency_counts (bounded), external_agencies` | — |
| 13 | `check_statistical_patterns` | `user_col, top_frac=0.01` | `n_actors, gini, top_frac_share, bottom_half_share` | `stats.gini/top_k_share/bottom_half_share` |

Notes:
- **#3/#4/#7 are the keyword-categorization checks** and ALL route through
  `derive.keyword_mask` — this is where the ICE/"polICE" guardrail is exercised at recipe
  level. The TDD spec asserts a row reading "Assist local **police**" / "community
  **service**" / "Dept of **Justice**" does NOT count as immigration, while "ICE detainer"
  / "CBP" / "deportation" does. #3 also accepts a precomputed `flag_col` (`is_immigration`
  from `derive`) as a fast path; the guardrail test uses the `text_col,keywords` path.
- **#5 PII is structured-regex only** (A-number, SSN, DOB, phone, email) and says so in a
  `note`. Semantic NER over names, weighted by count (the pilot's ~11,900 tally), is
  `pii-sweep` (Phase 5, spaCy) — **out of scope here on purpose** so Phase 4 needs no
  spaCy dependency. #5 is a fast structured-PII presence signal within the 13-point pass.
- **#7 co-travel is the keyword/search-type pass** (Flock "convoy"/"co-travel" query
  indicators), not a spatial plate-pair self-join — consistent with the audit-log grain and
  with Task 4.1 grouping co-travel under keyword categorization. (Spatial co-travel over
  raw detections is a richer, separate analysis; documented as out of scope for this pass.)
- **#9 vs #13 split:** mega_users is the *who* (named top-N actors + largest); statistical
  patterns is the *how concentrated* (Gini + top-1% / bottom-50% shares). `top_frac_share`
  may appear in both — same `stats.top_k_share`, different framing.
- **#12 cross_agency** emits the per-source actor set (bounded) so `rollup` can intersect
  across sources for the recurrence thesis.

### 3.3 `rollup.py` — cross-source recurrence
`rollup(findings_list, config=None) -> dict`:
```python
{
  "n_sources": int,
  "recurring_agencies": [ {"agency","n_sources","total_count","sources":[id...]}, ... ],   # sorted: n_sources desc, then total_count desc
  "aggregate": { "total_records", "total_immigration", "total_pretext",
                 "mean_out_of_state_pct", "sources_truncated":[id...],
                 "sources_traffic_gt_homicide":[id...], "total_pii_records", ... },
  "theses": [ {"thesis": str, "n_sources_supporting": int, "sources":[id...]}, ... ],
}
```
- **Recurring agencies** are tallied from each source's `cross_agency.agency_counts`
  (fallback: `mega_users.top_users`); an agency in ≥2 sources is "recurring" (the
  recurrence thesis). **Task 4.2 test:** two source fixtures sharing one OOS agency →
  that agency appears in `recurring_agencies` with `n_sources == 2` and summed count.
- **Aggregate / theses are robust to skipped checks** — a source missing a check simply
  doesn't contribute to that aggregate (no crash, no zero-pollution of means).
- Pure & deterministic; consumes only the findings dicts (no DataFrame, no IO).

---

## 4. Open questions / verify-at-implementation
1. **`stats.burstiness` needs a real timestamp column.** `derive` emits `date_et` /
   `hour_et` / `dow_et` but not a second-resolution timestamp; same-second burst detection
   needs the original timestamp. → `check_ai_moderation` runs burstiness ONLY when
   `timestamp_col` is configured & present, else returns the automation-signature half and
   marks burstiness `null`. (Resolve in TDD; don't fabricate a timestamp.)
2. **"own vs external" accountability split** (pilot 66% external / 77% own) needs a column
   distinguishing the source agency's own searches from external ones. → `check_accountability`
   takes an optional `group_col` (e.g. `geo`, or an explicit own/external flag) and emits
   `by_group`; with no such column it reports the overall `no_case_pct` only. Don't hardcode.
3. **PII regex set** — ship sensible defaults (A-number `A\d{8,9}`, SSN, US phone, email,
   ISO/US date) but keep `patterns` overridable; document that this is a *presence
   indicator*, deferring authoritative PII tallies to `pii-sweep`. Confirm patterns against
   the synthetic fixture in TDD (no real corpus).
4. **Workflow availability at runtime** — the skill must not hard-depend on the Workflow
   tool; the sequential/subagent fallback path is required (see §2).

---

### Verified-fact provenance (one line each)
- pandas 3.0.3 `value_counts(dropna=)` / `nlargest` / `idxmax` / `nunique(dropna=)` /
  `groupby().size()` / `groupby[val].median().sort_values()` / `dt.date.value_counts()` /
  Timedelta `.days` / nullable-`Int64` `value_counts` — **executed in `.venv`**.
- pandas-3.0 breaking changes (`observed=True` default, value_counts no dtype inference,
  `str.contains` na, `fillna(method=)` removed) — pandas.pydata.org What's-new 3.0.0
  (**web**); confirmed non-impacting against executed behavior.
- Skill-instructions-count-as-Workflow-opt-in + `parallel`+barrier fan-out + graceful
  fallback — live Claude Code workflows/skills docs.
- Reuse target `derive._compile_keyword_regex` (\b-anchored, longest-first, IGNORECASE) —
  read from `scripts/derive.py` in-repo.

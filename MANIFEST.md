# Magpie -- Structural Map

Fieldwork suite **Magpie** (v0.2.2): a FOSS-first FOIA / investigative-analysis Claude Code plugin.
Track A (analysis core, ingest, redaction, citation, evidence, onboarding) + Track B entity network
(12 `entity-extract`, 13a `entity-graph`, 13b `entity-crossref`) + opt-in **Jev** (citation pre-screen + `jev_ask`).
One-line-per-file INDEX only; depth lives in the design docs, docstrings and tests it points to.

## Stack

- **Shape:** Python 3.12.10 (mise-pinned; project `.venv` is the source of truth). Claude Code plugin; hard-deps `librarian`. `scripts/` run directly from skills; `pyproject.toml` is pytest config only.
- **Layer 0-1 (laptop, no Docker):** pandas/numpy/duckdb/pyarrow + openpyxl/charset-normalizer/sqlite-utils/PyYAML; lazy CPU ML edges: spaCy (pii-sweep), docling+OCR+torch (ingest), x-ray/PyMuPDF (redaction-check), rfc3161-client (archive-evidence).
- **Track B (Layer 2):** gliner + glirel (`transformers==4.57.6`, `typer==0.24.2` pinned); FtM/nomenklatura Linux/CI-only; neo4j driver + httpx/mcp lazy. neo4j / OpenSearch / yente ship as compose + docs, never bundled.
- **Jev:** no deps. Stdlib `urllib` POST to OpenRouter (TypeSafe's Jev), no-redirect opener; live only with `MAGPIE_JEV=1` + `OPENROUTER_API_KEY`.
- **Requirements:** `requirements-dev` (full) / `-offline` (CI subset) / `-ftm` (Linux FtM) / `-graph` (neo4j) / `-crossref` (httpx + mcp).

## Structure

```
.claude-plugin/plugin.json   Plugin manifest (name "magpie", MIT, dependencies ["librarian"]).
.mcp.json                    magpie-dataset server (uvx mcp-sqlite, read-only, ds_ prefix).
.mcp.yente.example.json      Operator-wired yente-mcp snippet (13b); never in the default .mcp.json.
scripts/                     Engines invoked by skills. House pattern: stdlib pure core + lazy heavy edge.
  stats.py                   Deterministic stats (gini, concentration, automation signature, burstiness).
  load_table.py              Dirty CSV/XLSX loader: encoding pre-flight, TEXT whitelist, merged-cell fill.
  data_quality.py            Truncation cap + date-window + per-column anomaly gate (leads, not verdicts).
  derive.py                  Config-driven derived columns (geo/reason_cat/nets/temporal); \b keyword guardrail.
  build_dataset_db.py        Served read-only SQLite builder (fail-closed include_columns allowlist; FTS5).
  recipe.py                  13-point per-source analysis pass (run_recipe / CHECKS).
  rollup.py                  Cross-source recurrence synthesis (external-only, pooled denominators).
  pii_sweep.py               spaCy PERSON NER + structured-PII tally (DEFAULT_PII_PATTERNS reused by jev_guards).
  ingest_gate.py             Pure text-layer quality gate (diagnose_page -> decide_doc).
  ingest.py                  Docling edge: gate-before-OCR, Bates pass, trust seam.
  redaction_check.py         8 leads-not-verdicts bad-redaction checks; never-publish-raw.
  redact_output.py           Uninvolved PII -> initials for publish; officials/involved kept.
  citation.py                Pure citation-anchor engine (build/resolve_anchor); CitationRecord.prescreen (local log only).
  evidence.py                sha256-on-receipt + RFC 3161 timestamp + hash-chained custody log.
  detect_tier.py             setup/doctor capability probe + read-only Docker probe + Jev on/off/approved line + --json.
  jev_client.py              Jev EDGE (only network module): ask(), strict validation, no redirects, ~14k windows + one split.
  jev_guards.py              PURE send guard: secret patterns + PII patterns (failed import = hit).
  jev_state.py               IO: data/jev_state.json approved model + model_status().
  jev_prescreen.py           Part A: numeric/date + computed-cue gate, skip/verify routing, spot-checks, audit fields, CLI.
  jev_ask.py                 Part B: batched noul/choice/score question over JSONL/CSV text fields -> results + meta + sample.
  entity_taxonomy.py         Track B entity/relation taxonomy (GENERIC + FLOCK presets).
  entity_extract.py          Track B pure core: windowing, span dedup, FtM-shaped nodes/edges, ReviewQueue.
  entity_models.py           Track B lazy GLiNER/GLiREL edge (sole torch/gliner/glirel importer).
  entity_ftmize.py           Track B FtM layer (Linux/CI; sole followthemoney importer).
  entity_resolution_policy.py  13a pure core: ResolutionConfig, canonical_id/edge_id, score buckets.
  entity_resolved_snapshot.py  13a pure core: portable resolved-snapshot schema (the 13a/13b seam).
  entity_review_packet.py    13a pure core: HITL HTML review packet + packet_hash + verdict parse.
  entity_nomenklatura.py     13a Linux/CI edge (sole nomenklatura importer): xref, fail-closed apply.
  entity_graph_neo4j.py      13a Docker edge (sole neo4j importer): investigation-scoped REPLACE writer.
  entity_yente_dataset.py    13b pure core: snapshot -> yente entities file + manifest (content-hash version).
  entity_crossref.py         13b pure core: /match shaping, CrossRefHit, scope grouping, report.
  entity_yente_client.py     13b live edge (sole httpx importer): byte-capped yente client + run_crossref.
  yente_mcp_server.py        13b live edge (lazy mcp): read-only yente-mcp (5 tools, loopback + scope allowlist).
agents/
  extraction-verifier.md     Semantic advisory re-check of a cited span; also the Jev spot-checker (blind to Jev scores).
  citation-checker.md        Mechanical anchor-integrity check (citation.resolve_anchor / is_clean_citation).
skills/                      One SKILL.md each + references/prior-art.md (that phase's verified-facts gate).
  dataset-analyze/           Load -> quality-gate -> derive -> served DB -> mcp-sqlite query -> stats.
    references/jev-guide.md  jev_ask + pre-screen usage guide; linked from dataset-analyze, entity-crossref, investigate, doctor.
  analysis-recipe/           Per-source 13-point recipe + cross-source rollup (Workflow fan-out).
  pii-sweep/  redact-output/ Authoritative PII tally -> redact uninvolved PII on output.
  ingest/  redaction-check/  Document/PDF ingest (+ common_words wordlist) / find bad redactions.
  investigate/               Verification gate + citation discipline (two agents + optional Jev pre-screen).
  archive-evidence/          Provenance + chain of custody (carries the prescreen block; + freeTSA root cert).
  setup/  doctor/            Operator install (MAY install) vs journalist health check (READ-ONLY).
  entity-extract/            GLiNER+GLiREL -> reviewed FtM-shaped intermediate behind a mandatory human gate.
  entity-graph/              13a: resolve -> HITL review -> Neo4j (operator-tier, Docker-gated).
  entity-crossref/           13b: snapshot -> yente own-corpus (opt-in watchlist) + yente-mcp (operator-tier).
tests/                       TDD suite, test_<module>.py + test_<skill>_skill.py smokes (1356 offline pass).
                             Marker-gated out of offline: spacy/docling/xray/tsa/gliner/ftm/neo4j/compose/yente/jev_live.
  test_jev_*.py              Jev offline suites (client/guards/windows/state/numeric_gate/prescreen/spotcheck/audit/ask/guide).
  test_jev_live_*.py         jev_live only (addopts deselects): pre-screen live eval (approves a model) + jev_ask smoke.
  golden/                    Env-gated real-corpus goldens + Flock _adapters.py; skip-if-absent public slice.
  fixtures/                  Synthetic ONLY: CSV/XLSX samples, reviewed intermediates, yente response, jev/ eval + smoke.
  helpers/                   emit_smoke_dataset.py (own-corpus emitter) + jev_fakes.py (FakeTransport + responders).
  test_manifest_budget.py    Budget guard for THIS file (line / word / per-line-word caps).
docs/
  OPERATOR_GUIDE / JOURNALIST_START   Dual onramp (operator setup vs journalist daily use; no Docker in either).
  RELEASE-NOTES-* / RELEASE-CHECKLIST*  Release notes (0.1.0, 0.2.0) + pre-tag green gates.
  plans/                     Source-of-truth design (2026-06-03-magpie-design.md) + per-phase design/plan pairs (Jev: 2026-09-24).
  handoffs/                  Session handoffs (gitignored; local only).
data/                        UNTRACKED (gitignored): jev_state.json, jev_spotcheck.jsonl, own-corpus yente dataset.
infra/docker-compose.yml     Neo4j `graph` + OpenSearch/yente `crossref` profiles; localhost-bound, healthchecks, .env.example.
infra/yente/                 Manifest templates: magpie-own.yml (default) + magpie-watchlist.yml (opt-in, CC-BY-NC).
tools/                       build_public_slice.py (neutral public-CSV slice) + codex-review.ps1 (UTF-8 Codex review helper).
corpus/public/               Reserved redistributable public sample (DATASHEET.md template; corpus is a fast-follow).
.github/workflows/ci.yml     offline (default) + dispatch heavy/ftm/graph/compose/crossref jobs; no job runs live Jev.
mise.toml / pyproject.toml   Dev env (Python + .venv; test/bootstrap tasks) / pytest markers + addopts `-m "not jev_live"`.
.gitignore / .gitattributes  PII-corpus block + scratch/resolver-DB/infra-secret/`/data/` ignores / binary certs+tokens.
```

## Key Relationships

- **Pure core, engine at the edge.** Every heavy module pairs a stdlib core with a lazy model/IO edge that is the sole importer of its dependency; core tests run on injected fakes.
- **Track B is verified in CI, not on Windows.** ftm / graph / compose / crossref jobs are the real surface for the Linux- and Docker-gated edges; gate merges on them.
- **Track B data contract.** extract -> intermediate -> FtM bundle -> nomenklatura xref behind a HITL packet -> resolved snapshot (the seam) -> Neo4j REPLACE write + yente dataset for cross-ref.
- **Stable identity.** `canonical_id = sha256(sorted(member_ids))[:40]`; Neo4j keys `investigation_id:canonical_id`; cross-ref attributes hits by the query key.
- **Operator-tier Layer 2.** entity-graph + entity-crossref need Docker; the journalist onramp stays Docker-free; watchlist data is opt-in (CC-BY-NC).
- **Jev is opt-in and fails toward today.** Off unless flag + key; guards before send; only claim/quote/span or chosen text fields go out under positional keys; any error -> verify / skipped.
- **Jev skip safeguards.** Clean citation + numeric/computed-cue gate + both scores >= threshold + approved model; spot-checks and a "not independently verified" label; never replaces the human gate.
- **Private data stays out of git.** Real corpora, the resolver DB, `infra/.env` and `/data/` are gitignored; scope corpus searches to `*.py`.
- **Dev env: never bare `python`.** Use `mise exec` or `.venv\Scripts\python.exe`; offline suite via `-m "not docling and not spacy and ... and not jev_live"` (use `-m`, not `-k`).
- **This file only indexes.** WHY -> `docs/plans/*-design.md`; HOW -> docstrings; contract -> `tests/`; library facts -> `skills/*/references/prior-art.md`. Regenerate by full rewrite to budget.

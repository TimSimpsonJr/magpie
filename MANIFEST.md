# Magpie -- Structural Map

Fieldwork suite **Magpie**: a FOSS-first FOIA / investigative-analysis Claude Code plugin (**v0.2.2**).
Layer 0-1 Track A (analysis core, document ingest, redaction, citation, evidence, onboarding) +
Track B entity network (Phase 12 `entity-extract`, 13a `entity-graph`, 13b `entity-crossref`) +
the opt-in **Jev** integration (citation pre-screen + `jev_ask` dataset questions; off by default).
This file is a one-line-per-file INDEX; depth lives in the design docs, docstrings and tests it points to.

## Stack

- **Language / shape:** Python 3.12.10 (mise-pinned; the project `.venv` is the source of truth). Claude Code plugin (`.claude-plugin/`); hard-deps the `librarian` plugin. `scripts/` are invoked directly by skills; `pyproject.toml` configures pytest only (not a pip package).
- **Layer 0-1 deps (laptop-local, no Docker):** pandas/numpy/duckdb/pyarrow + openpyxl/charset-normalizer/sqlite-utils/PyYAML; lazy CPU-only ML edges -- spaCy + en_core_web_lg (pii-sweep), docling+rapidocr+onnxruntime+torch+ocrmypdf (ingest), x-ray -> PyMuPDF (redaction-check), rfc3161-client (archive-evidence). numpy/pandas/torch pins held.
- **Track B (Layer 2) deps:** gliner + glirel + loguru, `transformers==4.57.6` + `typer==0.24.2` PINNED. FtM (followthemoney/nomenklatura) is **Linux/CI-only**; the Neo4j driver + cross-ref `httpx`/`mcp` are cross-platform + LAZY. Server images (neo4j 5.26 / OpenSearch 2.19.5 / yente 5.4.0) ship as compose + docs, never bundled.
- **Jev deps:** none. Stdlib `urllib` to OpenRouter (TypeSafe's Jev model); live only with `MAGPIE_JEV=1` + `OPENROUTER_API_KEY`.
- **Requirements split:** `requirements-dev.txt` (full) / `-offline` (trimmed CI subset) / `-ftm` (Linux-CI FtM) / `-graph` (Neo4j driver) / `-crossref` (httpx + mcp).

## Structure

```
.claude-plugin/plugin.json   Plugin manifest (name "magpie", MIT; dependencies: ["librarian"]).
.mcp.json                    Declares the magpie-dataset server (uvx mcp-sqlite, read-only, ds_ prefix).
.mcp.yente.example.json      Operator-wired yente-mcp config snippet (13b; NOT auto-loaded -- never in the default .mcp.json).
scripts/                     Engine modules invoked by skills. House pattern: stdlib pure core + lazy heavy edge.
  stats.py                   Deterministic stats flagship (gini, concentration, automation signature, burstiness).
  load_table.py              Dirty CSV/XLSX loader: encoding pre-flight, TEXT-whitelist, empty_null, merged-cell fill.
  data_quality.py            Truncation (2^20-1 cap) + date-window + per-column anomaly gate (leads, not verdicts).
  derive.py                  Config-driven derived columns (geo/reason_cat/is_immigration/nets/temporal); \b keyword guardrail.
  build_dataset_db.py        Served read-only SQLite builder (fail-closed include_columns allowlist; FTS5).
  recipe.py                  13-point per-source analysis pass (run_recipe/CHECKS); composes stats+data_quality+derive.
  rollup.py                  Cross-source recurrence synthesis (external-only, pooled denominators, theses).
  pii_sweep.py               spaCy PERSON NER + structured-PII tally (DEFAULT_PII_PATTERNS, reused by jev_guards).
  ingest_gate.py             Pure text-layer quality gate (diagnose_page -> decide_doc); injectable wordlist.
  ingest.py                  Docling edge: gate-before-OCR, DoclingDocument JSON kept internal, Bates pass, trust seam.
  redaction_check.py         8 leads-not-verdicts redaction checks (x-ray/pikepdf/pdfminer); never-publish-raw.
  redact_output.py           Redact uninvolved PII to initials for publish; officials/involved kept; vault-guarded exhibit.
  citation.py                Pure citation-anchor engine (build/resolve_anchor fallback chain); CitationRecord.prescreen (local log only).
  evidence.py                Provenance/custody: sha256-on-receipt + RFC 3161 timestamp + hash-chained custody log.
  detect_tier.py             setup/doctor capability probe + Layer-2 read-only Docker probe + `jev: on/off (...)` line + --json CLI.
  jev_client.py              Jev EDGE (the only new network module): ask(), strict validation, jev_status(), ~14k windows + single split.
  jev_guards.py              PURE send guard: secret patterns + PII (pii_sweep patterns; failed import = hit) -> guard().
  jev_state.py               IO: data/jev_state.json approved model + model_status() (unknown/unapproved/approved/changed).
  jev_prescreen.py           Part A: numeric/date gate, skip/verify routing, spot-checks, disagreement log, audit fields, CLI.
  jev_ask.py                 Part B: batched noul/choice/score question over JSONL/CSV text fields -> results + meta + sample.
  entity_taxonomy.py         Track B: entity/relation taxonomy config (GENERIC + FLOCK presets).
  entity_extract.py          Track B pure core: windowing, span dedup, FtM-shaped nodes/edges, ReviewQueue, build_intermediate.
  entity_models.py           Track B lazy GLiNER/GLiREL edge (the only torch/gliner/glirel/spaCy importer).
  entity_ftmize.py           Track B FtM layer (Linux/CI only; only followthemoney importer): intermediate -> FtM bundle.
  entity_resolution_policy.py  13a pure core: ResolutionConfig, canonical_id/edge_id, score bucket, Candidate/Verdict.
  entity_resolved_snapshot.py  13a pure core: portable resolved-snapshot schema + serializer + consumable check (the 13a/13b seam).
  entity_review_packet.py    13a pure core: HITL HTML review packet + packet_hash + verdict parse.
  entity_nomenklatura.py     13a Linux/CI edge (only nomenklatura importer): xref LogicV2, fail-closed apply, snapshot build.
  entity_graph_neo4j.py      13a Docker edge (only neo4j importer): investigation-scoped REPLACE writer (scoped_id).
  entity_yente_dataset.py    13b pure core: resolved snapshot -> yente entities file (FtM JSONL) + manifest; content-hash version.
  entity_crossref.py         13b pure core: /match request/response shaping, CrossRefHit, scope grouping, cross-ref report.
  entity_yente_client.py     13b live edge (only httpx importer): thin yente HTTP client (byte-capped reads) + run_crossref.
  yente_mcp_server.py        13b live edge (lazy mcp): read-only yente-mcp (5 tools; caps, loopback + scope allowlist).
agents/
  extraction-verifier.md     Semantic advisory re-check of a cited span; also the Jev spot-checker (never sees Jev scores).
  citation-checker.md        Mechanical anchor-integrity check (drives citation.resolve_anchor / is_clean_citation).
skills/                      One SKILL.md per skill; each carries references/prior-art.md = that phase's verified-facts gate.
  dataset-analyze/           Track A: load -> quality-gate -> derive -> served DB -> mcp-sqlite query -> stats (+ canned_queries.yml).
    references/jev-guide.md  When/how to use jev_ask + what the pre-screen is; linked from dataset-analyze, entity-crossref, investigate, doctor.
  analysis-recipe/           Track A: per-source 13-point recipe + cross-source rollup (Workflow fan-out).
  pii-sweep/                 Track A: authoritative PII-exposure tally; feeds redact-output.
  ingest/                    Spine: document/PDF ingest (engine = ingest_gate + ingest; + bundled common_words wordlist).
  redaction-check/           Spine: find bad redactions (input side).
  redact-output/             Spine: redact uninvolved PII on output.
  investigate/               Spine: verification gate / citation discipline (citation.py + the two agents + optional Jev pre-screen).
  archive-evidence/          Spine: provenance + chain-of-custody (evidence.py; carries the prescreen block; + freeTSA root cert).
  setup/  doctor/            Onboarding: setup (operator, MAY install) vs doctor (journalist, READ-ONLY); engine detect_tier.
  entity-extract/            Track B: GLiNER+GLiREL -> reviewed FtM-shaped intermediate after a mandatory human gate.
  entity-graph/              13a: resolve entities -> HITL review -> Neo4j (operator-tier, Docker-gated, mandatory human gate).
  entity-crossref/           13b: snapshot -> yente own-corpus (opt-in watchlist) cross-ref + yente-mcp (operator-tier, Docker-gated).
tests/                       TDD suite; test_<module>.py per script + test_<skill>_skill.py smokes (1288 offline + guards).
                             Marker-gated, excluded from offline: spacy/docling/xray/tsa/gliner/ftm/neo4j/compose/yente/jev_live.
  test_jev_*.py              Jev offline suites (client/guards/windows/state/numeric_gate/prescreen/spotcheck/audit/ask/guide) + test_readme_jev_privacy.
  test_jev_live_*.py         jev_live only (addopts deselects): prescreen live eval (approves the model) + jev_ask smoke.
  golden/                    Env-gated real-corpus goldens (test_simpsonville.py) + Flock _adapters.py; skip-if-absent public slice.
  fixtures/                  Synthetic ONLY: sample CSV/XLSX, reviewed_intermediate, yente_match_response.json, jev/ (eval + smoke).
  helpers/                   emit_smoke_dataset.py (crossref own-corpus emitter) + jev_fakes.py (FakeTransport + Jev responses).
  conftest*.py               Shared synthetic builders (PDF / DoclingDocument / redaction / entity fakes).
  test_manifest_budget.py    The recurrence guard for THIS file (line / word / per-line-word budgets).
docs/
  OPERATOR_GUIDE / JOURNALIST_START   Dual onramp (operator setup vs journalist daily use; no Docker in either).
  RELEASE-NOTES-0.1.0 / RELEASE-CHECKLIST   v0.1.0 release notes + the pre-tag green gate.
  plans/                     Source-of-truth design (2026-06-03-magpie-design.md) + per-phase design(WHY)+plan(HOW) pairs (incl. Jev 2026-09-24).
  handoffs/                  Session-boundary handoff docs (gitignored; local-only).
data/                        UNTRACKED (gitignored): jev_state.json (approved model), jev_spotcheck.jsonl, own-corpus yente dataset.
infra/docker-compose.yml     Neo4j `graph` profile (13a) + OpenSearch+yente `crossref` profile (13b); localhost-bound, healthchecks + .env.example.
infra/yente/                 Committed manifest TEMPLATES: magpie-own.yml (default, no catalogs) + magpie-watchlist.yml (opt-in, CC-BY-NC).
tools/                       build_public_slice.py (deterministic neutral public-CSV slice) + codex-review.ps1 (UTF-8-pinned Codex review helper).
corpus/public/               Reserved for the redistributable public sample (DATASHEET.md template; the corpus is a fast-follow).
.github/workflows/ci.yml     CI: offline (default) + workflow_dispatch heavy + ftm + graph + compose + crossref jobs; no job runs live Jev.
requirements-{dev,offline,ftm,graph,crossref}.txt   Full / trimmed offline-CI / Linux-CI FtM / Neo4j driver / cross-ref (httpx+mcp).
mise.toml / pyproject.toml   mise dev env (Python 3.12.10 + .venv bind; test/bootstrap tasks) / pytest markers + addopts `-m "not jev_live"`.
.gitignore / .gitattributes  PII-corpus hard block + scratch/resolver-DB/infra-secret/`/data/` ignores / binary-mark certs+tokens.
```

## Key Relationships

- **Pure-core / engine-at-edge split (suite-wide).** Every heavy module keeps a stdlib pure core plus a lazy model/IO edge (pii_sweep, ingest, citation, evidence, detect_tier, entity_*, jev_*), so importing stays cheap and the core tests run with injected fakes. The edge is the sole + lazy importer of its heavy dependency.
- **Track B edges are Linux/CI-or-Docker-gated; CI is the only real verification surface.** `entity_ftmize`+`entity_nomenklatura` (`ftm` job), `entity_graph_neo4j` (`graph`+`compose`), `entity_yente_client`+`yente_mcp_server` (`crossref`: live OpenSearch + yente /match + mcp smoke). **Gate merges on ftm+graph+compose+crossref, never Windows-green alone.**
- **Track B data contract (12 -> 13a -> 13b).** `entity_extract` -> intermediate -> `entity_ftmize` FtM bundle -> `entity_nomenklatura` xref behind a mandatory HITL packet -> `entity_resolved_snapshot` (the portable seam) -> Neo4j REPLACE write AND a yente dataset for `entity_crossref`. 13b consumes it UNCHANGED (`assert_snapshot_consumable`).
- **Stable content-addressed identity + query-side attribution.** `canonical_id = sha256(sorted(member_ids))[:40]`; Neo4j keys `scoped_id = investigation_id+':'+canonical_id`; cross-ref attributes hits by the QUERY key, never the yente result id.
- **Operator-tier, Docker-gated, opt-in posture.** entity-graph + entity-crossref are Layer-2 (Docker); the journalist onramp stays Docker-free. Watchlist data is CC-BY-NC -> opt-in; own-corpus cross-ref pulls zero external data; yente-mcp is operator-wired only.
- **Jev is opt-in and fail-toward-today.** Off unless MAGPIE_JEV=1 + key; guards before send; pre-screen only removes verifier calls; model change -> verify; live eval approves the model. Only claim/quote/span or chosen text fields go out under positional K/R keys (never ids, paths, documents); skips are labeled "not independently verified" and never replace the human gate.
- **Hard dep on Librarian.** `plugin.json` `dependencies: ["librarian"]` wires findings output to the shared notes layer.
- **Private corpus + secrets are gitignored.** Real Flock/Simpsonville PII lives outside the repo; scope any corpus search to `*.py`. The resolver DB, `infra/.env` and `/data/` (PII-derived dataset + Jev state/log) never land in a commit.
- **Dev env: never bare `python`.** Use `mise run`/`mise exec` or `& .venv\Scripts\python.exe`. Offline suite: `-m "not docling and not spacy and not xray and not tsa and not gliner and not ftm and not neo4j and not compose and not yente and not jev_live"` (use `-m`, NOT `-k`).
- **This file only indexes; depth lives elsewhere.** WHY -> `docs/plans/*-design.md`; HOW -> module docstrings; contract -> `tests/`; verified library facts -> `skills/*/references/prior-art.md`. Regenerating MANIFEST means rewriting this index to budget, never appending (guarded by `tests/test_manifest_budget.py`).

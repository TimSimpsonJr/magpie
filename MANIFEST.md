# Magpie -- Structural Map

Fieldwork suite **Magpie**: a FOSS-first FOIA / investigative-analysis Claude Code plugin.
State: **tagged v0.2.0** -- Layer 0-1 (Track A analysis core, document ingest, redaction,
citation, evidence, onboarding; the v0.1.0 cut) PLUS **Track B (entity-network) SHIPPED** in
v0.2.0: Phase 12 `entity-extract` + Phase 13a `entity-graph` (resolution + HITL packet + Neo4j)
+ Phase 13b `entity-crossref` (yente own-corpus / opt-in watchlist cross-ref + yente-mcp).
This file is a one-line-per-file INDEX; depth lives in the design docs, docstrings, and
tests it points to (see the last Key Relationship).

## Stack

- **Language / shape:** Python 3.12.10 (mise-pinned; the project `.venv` is the source of truth). Claude Code plugin (`.claude-plugin/`); hard-deps the `librarian` plugin. `scripts/` are invoked directly by skills; `pyproject.toml` configures pytest only (not a pip package).
- **Layer 0-1 deps (laptop-local, no Docker):** pandas/numpy/duckdb/pyarrow + openpyxl/charset-normalizer/sqlite-utils/PyYAML; heavy CPU-only ML *edges* -- spaCy + en_core_web_lg (pii-sweep), docling+rapidocr+onnxruntime+torch+ocrmypdf (ingest), x-ray -> PyMuPDF (redaction-check), rfc3161-client (archive-evidence). numpy/pandas/torch pins held across every phase.
- **Track B (Layer 2) deps:** gliner + glirel + loguru, `transformers==4.57.6` + `typer==0.24.2` PINNED. FtM (followthemoney/nomenklatura) is **Linux/CI-only** (`requirements-ftm.txt`; no Windows PyICU wheel). The Neo4j driver + the cross-ref `httpx`/`mcp` (FastMCP) are cross-platform + LAZY (`requirements-graph.txt` / `requirements-crossref.txt`). Server images (neo4j 5.26 / OpenSearch 2.19.5 / yente 5.4.0) ship as compose + docs, never bundled.
- **Requirements split:** `requirements-dev.txt` (full) / `requirements-offline.txt` (trimmed CI subset) / `requirements-ftm.txt` (Linux-CI FtM) / `requirements-graph.txt` (Neo4j driver) / `requirements-crossref.txt` (httpx + mcp).

## Structure

```
.claude-plugin/plugin.json   Plugin manifest (name "magpie", v0.1.0, MIT; dependencies: ["librarian"]).
.mcp.json                    Declares the magpie-dataset server (uvx mcp-sqlite, read-only, ds_ prefix).
.mcp.yente.example.json      Operator-wired yente-mcp config snippet (Phase 13b; NOT auto-loaded -- never in the default .mcp.json).
scripts/                     Engine modules invoked by skills. House pattern: stdlib pure core + lazy heavy edge.
  stats.py                   Deterministic stats flagship (gini, concentration, automation signature, burstiness).
  load_table.py              Dirty CSV/XLSX loader: encoding pre-flight, TEXT-whitelist, empty_null, merged-cell fill.
  data_quality.py            Truncation (2^20-1 cap) + date-window + per-column anomaly gate (leads, not verdicts).
  derive.py                  Config-driven derived columns (geo/reason_cat/is_immigration/nets/temporal); \b keyword guardrail.
  build_dataset_db.py        Served read-only SQLite builder (fail-closed include_columns allowlist; FTS5).
  recipe.py                  13-point per-source analysis pass (run_recipe/CHECKS); composes stats+data_quality+derive.
  rollup.py                  Cross-source recurrence synthesis (external-only, pooled denominators, theses).
  pii_sweep.py               spaCy PERSON NER + structured-PII tally over distinct values, weighted by row counts.
  ingest_gate.py             Pure text-layer quality gate (diagnose_page -> decide_doc); injectable wordlist.
  ingest.py                  Docling edge: gate-before-OCR, DoclingDocument JSON kept internal, Bates pass, trust seam.
  redaction_check.py         8 leads-not-verdicts redaction checks (x-ray/pikepdf/pdfminer); never-publish-raw.
  redact_output.py           Redact uninvolved PII to initials for publish; officials/involved kept; vault-guarded exhibit.
  citation.py                Pure citation-anchor engine (build_anchor/resolve_anchor fallback chain); own-.text offsets.
  evidence.py                Provenance/custody: sha256-on-receipt + RFC 3161 timestamp + hash-chained custody log.
  detect_tier.py             setup/doctor capability probe (stdlib) + Layer-2 read-only Docker probe (entity-graph + entity-crossref) + --json CLI.
  entity_taxonomy.py         Track B: entity/relation taxonomy config (GENERIC + FLOCK presets).
  entity_extract.py          Track B pure core: windowing, span dedup, FtM-shaped nodes/edges, ReviewQueue, build_intermediate.
  entity_models.py           Track B lazy GLiNER/GLiREL edge (the only torch/gliner/glirel/spaCy importer).
  entity_ftmize.py           Track B FtM layer (Linux/CI only; only followthemoney importer): intermediate -> FtM bundle.
  entity_resolution_policy.py  Phase 13a pure core: ResolutionConfig, canonical_id/edge_id, score bucket, Candidate/Verdict.
  entity_resolved_snapshot.py  Phase 13a pure core: portable resolved-snapshot schema + serializer + consumable check (the 13a/13b seam).
  entity_review_packet.py    Phase 13a pure core: HITL HTML review packet + packet_hash + verdict parse (matches signed-off mockup).
  entity_nomenklatura.py     Phase 13a Linux/CI edge (only nomenklatura importer): xref LogicV2, fail-closed apply, resolved-snapshot build.
  entity_graph_neo4j.py      Phase 13a Docker edge (only neo4j importer): investigation-scoped REPLACE writer (single-property scoped_id).
  entity_yente_dataset.py    Phase 13b pure core: resolved snapshot -> yente entities file (FtM JSONL) + manifest render; content-hash version.
  entity_crossref.py         Phase 13b pure core: /match request/response shaping, CrossRefHit, scope grouping, cross-ref report.
  entity_yente_client.py     Phase 13b live edge (only httpx importer): thin yente HTTP client (streamed byte-capped reads) + run_crossref.
  yente_mcp_server.py        Phase 13b live edge (lazy mcp/FastMCP): thin read-only yente-mcp (5 tools; caps, loopback + scope allowlist, no-write).
agents/
  extraction-verifier.md     Semantic advisory re-check of a cited span (presence + entailment; indeterminate default).
  citation-checker.md        Mechanical anchor-integrity check (drives citation.resolve_anchor / is_clean_citation).
skills/                      One SKILL.md per skill; each carries references/prior-art.md = that phase's verified-facts gate.
  dataset-analyze/           Track A: load -> quality-gate -> derive -> served DB -> mcp-sqlite query -> stats (+ canned_queries.yml).
  analysis-recipe/           Track A: per-source 13-point recipe + cross-source rollup (Workflow fan-out).
  pii-sweep/                 Track A: authoritative PII-exposure tally; feeds redact-output.
  ingest/                    Spine: document/PDF ingest (engine = ingest_gate + ingest; + bundled common_words wordlist).
  redaction-check/           Spine: find bad redactions (input side).
  redact-output/             Spine: redact uninvolved PII on output.
  investigate/               Spine: verification gate / citation discipline (engine citation.py + the two agents).
  archive-evidence/          Spine: evidence provenance + chain-of-custody (engine evidence.py; + bundled freeTSA root cert).
  setup/  doctor/            Onboarding: setup (operator, MAY install) vs doctor (journalist, READ-ONLY); engine detect_tier.
  entity-extract/            Track B: GLiNER+GLiREL -> reviewed FtM-shaped intermediate after a mandatory human gate.
  entity-graph/              Phase 13a: resolve entities -> HITL review -> Neo4j (operator-tier, Docker-gated, mandatory human gate).
  entity-crossref/           Phase 13b: snapshot -> yente own-corpus (opt-in watchlist) cross-ref + yente-mcp (operator-tier, Docker-gated).
tests/                       TDD suite; test_<module>.py mirrors each script + test_<skill>_skill.py smokes (860 offline + guards).
                             Marker-gated, excluded from offline: spacy/docling/xray/tsa/gliner/ftm/neo4j/compose/yente.
  golden/                    Env-gated real-corpus goldens (test_simpsonville.py) + Flock _adapters.py; skip-if-absent public slice.
  fixtures/                  Synthetic fixtures ONLY (no real corpus): sample CSV/XLSX, reviewed_intermediate, yente_match_response.json.
  helpers/                   emit_smoke_dataset.py -- shared tiny own-corpus emitter for the crossref CI job + local re-smoke.
  conftest*.py               Shared synthetic builders (PDF / DoclingDocument / redaction / entity fakes).
  test_manifest_budget.py    The recurrence guard for THIS file (line / word / per-line-word budgets).
docs/
  OPERATOR_GUIDE / JOURNALIST_START   Phase 10 dual onramp (operator setup vs journalist daily use; no Docker in either).
  RELEASE-NOTES-0.1.0 / RELEASE-CHECKLIST   v0.1.0 release notes + the pre-tag green gate.
  plans/                     Source-of-truth design (2026-06-03-magpie-design.md) + per-phase design(WHY)+plan(HOW) pairs + the phase13a review-packet mockup.
  handoffs/                  Session-boundary handoff docs (gitignored; local-only).
infra/
  docker-compose.yml         Neo4j `graph` profile (13a) + index(OpenSearch)+yente `crossref` profile (13b); localhost-bound, healthchecks, named volumes + .env.example.
  yente/                     Committed manifest TEMPLATES: magpie-own.yml (default, no catalogs) + magpie-watchlist.yml (opt-in civic catalog, CC-BY-NC).
tools/
  build_public_slice.py      Deterministic neutral public-CSV slice builder (for the forthcoming corpus/public sample).
  codex-review.ps1           Dev helper: UTF-8-pinned Codex cross-model review (PS 5.1 cp1252 workaround).
corpus/public/               Reserved for the redistributable public sample (DATASHEET.md template; the corpus is a fast-follow).
.github/workflows/ci.yml     CI: offline (default) + workflow_dispatch heavy + ftm + graph (Neo4j svc) + compose (graph-profile up) + crossref (yente+OpenSearch live /match + mcp smoke) jobs.
requirements-{dev,offline,ftm,graph,crossref}.txt   Full / trimmed offline-CI / Linux-CI FtM / Neo4j driver / cross-ref (httpx+mcp).
mise.toml / pyproject.toml   mise dev env (Python 3.12.10 + .venv bind; test/bootstrap tasks) / pytest config + markers.
.gitignore / .gitattributes  PII-corpus hard block + scratch/resolver-DB/infra-secret/own-corpus-data ignores / binary-mark certs+tokens.
```

## Key Relationships

- **Pure-core / engine-at-edge split (suite-wide).** Every heavy module keeps a stdlib pure core plus a lazy model/IO edge (pii_sweep, ingest, citation, evidence, detect_tier, entity_*), so importing stays cheap and the core golden-tests with injected fakes. The edge is the sole + lazy importer of its heavy dependency (torch/followthemoney/neo4j/httpx/mcp).
- **Track B edges are Linux/CI-or-Docker-gated; CI is the only real verification surface.** `entity_ftmize`+`entity_nomenklatura` (followthemoney/nomenklatura -> `ftm` job), `entity_graph_neo4j` (`graph`+`compose` jobs), `entity_yente_client`+`yente_mcp_server` (the `crossref` job: live OpenSearch 2.19.5 + yente 5.4.0, real /match + mcp smoke). **Gate merges on ftm+graph+compose+crossref, never Windows-green + Codex-green alone** (the Phase-12 lesson; each job has caught real bugs).
- **Track B data contract (12 -> 13a -> 13b).** `entity_extract` -> intermediate; `entity_ftmize` -> FtM bundle; `entity_nomenklatura` xref behind a mandatory HITL packet (`entity_review_packet`, fail-closed) -> `entity_resolved_snapshot` (the portable seam) -> `entity_graph_neo4j.write` (scoped Neo4j REPLACE) AND `entity_yente_dataset` -> a yente dataset `entity_crossref`/`entity_yente_client` screen (own-corpus + opt-in CC-BY-NC watchlists). 13b consumes it UNCHANGED (`assert_snapshot_consumable`).
- **Stable content-addressed identity + query-side attribution.** `canonical_id = sha256(sorted(member_ids))[:40]` (NOT the mutable NK- id). Neo4j keys a synthesized `scoped_id = investigation_id+':'+canonical_id` (Community-safe uniqueness). Cross-ref attributes hits by the QUERY key (= canonical_id), never the yente-namespaced result id; the emit invents no FtM property.
- **Operator-tier, Docker-gated, opt-in posture.** entity-graph + entity-crossref are Layer-2 (Docker); the journalist onramp stays Docker-free. Watchlist data is CC-BY-NC -> opt-in + documented; own-corpus cross-ref pulls zero external data. yente-mcp is read-only + operator-wired (never the default .mcp.json). Determinism: `YENTE_AUTO_REINDEX=false` + content-hash dataset version + explicit `yente reindex`.
- **Hard dep on Librarian.** `plugin.json` `dependencies: ["librarian"]` wires findings output to the shared notes layer (auto-installed with Magpie).
- **Private corpus + secrets are gitignored.** Real Flock/Simpsonville PII lives outside the repo; scope any corpus search to `*.py`. The resolver DB, `infra/.env`, and the emitted own-corpus dataset (`/data/`, PII-derived) are gitignored; corpus compute prints aggregates only via a gitignored `.codex-review/` script.
- **Dev env: never bare `python`.** The Claude Code PowerShell tool is `-NoProfile`, so use `mise run`/`mise exec` or `& .venv\Scripts\python.exe`. Offline suite: `-m "not docling and not spacy and not xray and not tsa and not gliner and not ftm and not neo4j and not compose and not yente"` (use `-m`, NOT `-k`).
- **This file only indexes; depth lives elsewhere.** WHY -> `docs/plans/*-design.md`; HOW / source-of-truth -> module docstrings; the contract -> `tests/`; per-phase verified library facts -> `skills/*/references/prior-art.md`; build state + lessons -> project memory. Regenerating MANIFEST means rewriting this index to its budget, never appending detail (guarded by `tests/test_manifest_budget.py`).
```

# Magpie — Design Document

**Fieldwork: Magpie** — a FOSS-first investigative analysis toolkit for Claude Code.

- **Date:** 2026-06-03
- **Status:** Design approved (brainstorming complete). Next step: `writing-plans`.
- **Author:** Tim Simpson (with Claude)
- **Source material:** `foia-tooling-report-external.md` (2026 landscape report) + a 10-skill Tier-1 prior-art scan (run via the research-workflow method) + the DeflockSC Simpsonville Flock audit (flagship use case).

---

## 1. Overview & purpose

Magpie is a Claude Code plugin that helps an investigator analyze material obtained through FOIA / public-records requests and (later) broader OSINT sources. The name fits the job: a magpie gathers scattered shiny things into one nest — Magpie pulls documents, structured data, and entities into one analyzable, citable whole.

It serves **two co-equal investigative paradigms**:

- **Track A — Dataset analysis (data-journalism paradigm).** Quantitative analysis of large structured FOIA releases (CSV/XLSX), plus NLP on free-text fields, run as a repeatable per-source pass and rolled up across sources. *This is the flagship, tool-validating use case* (see §6.1, the Flock ALPR audit).
- **Track B — Entity-network analysis (Aleph/OCCRP paradigm).** Documents → entity + relationship extraction → resolution → graph → cross-reference against watchlists/corpora.

Both ride a **shared spine** (ingest, verify, redaction-check, evidence/provenance, setup) and emit findings through a **shared output layer** (portable Markdown/CSV by default; Obsidian when a vault is present).

**Primary users (layered):** a technical person performs one-time setup (Docker stack, MCP wiring); a journalist/investigator does daily analysis conversationally. Setup complexity is hidden behind a wizard.

**Design philosophy:** FOSS-first and permissively licensed by default; correctness over reach (human-verification gates, honest limits); shareable (works on a laptop with no heavy infra for the flagship track).

---

## 2. The Fieldwork suite (packaging & naming)

Magpie is one member of a small family of investigative Claude Code plugins under the **Fieldwork** brand. Fieldwork itself is a **pure marketplace pointer** — a repo containing only a `marketplace.json` that catalogs each member by its own upstream repo. No member is ever vendored into another; each is a single source of truth.

```
Fieldwork  ── umbrella marketplace (pure pointer repo; references members by github source)
├─ Magpie     ── investigations analysis toolkit   (this design)        repo: TimSimpsonJr/magpie
├─ Research   ── deep web-research pipeline         (today's research-workflow; name kept stable)
├─ Librarian  ── shared structured-notes skill      (own repo; see §5.7)
└─ Publisher  ── vault → blog/social                (today's obsidian-publisher)
```

- **Plugin names stay short** (`magpie`, `research`, `librarian`, `publisher`) for clean skill namespaces (`magpie:ingest`, `librarian:write`). "Fieldwork: X" is the display/brand framing only.
- The existing `fieldwork-plugins` repo is **repurposed** into the Fieldwork pointer-marketplace: clear the vendored plugin copies, leave only `marketplace.json` referencing each member's own repo. (This supersedes the previously-queued de-vendor task.)
- `research-workflow`'s repo and plugin name are **kept stable** (the user runs it standalone on multiple machines); it is branded "Fieldwork: Research" at the marketplace/display layer only — no install churn.
- If `obsidian-publisher` is not already its own standalone repo, extract it so Fieldwork can point at it.

**Inter-member relationships:**
- `magpie` → `librarian`: hard dependency (`plugin.json` `dependencies`) — auto-installed with Magpie.
- `research-workflow` → `librarian`: hard dependency — so installing Research standalone auto-pulls Librarian (acceptance-tested; see §9).
- `magpie` → `research`: soft coupling — Magpie uses Research's `/research` for adjacent web corroboration *if present*, degrades gracefully if not.

---

## 3. Locked decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Nature | Full toolkit + infra (skills + MCP servers + docker-compose) |
| 2 | Audience | Both, layered (technical setup → journalist daily use) |
| 3 | Licensing | Permissive-core default; copyleft (AGPL) / non-free as opt-in profiles |
| 4 | Validation data | Real Simpsonville Flock corpus (private dev); clean bundled public sample (shared repo) |
| 5 | Build approach | Layered spine-first (Layers 0–3) |
| 6 | Tracks | Co-equal: Track A (dataset analysis) + Track B (entity-network) |
| 7 | Entity engine | OpenSanctions tooling (rigour/nomenklatura/yente/ftm-graph → Neo4j); OpenAleph optional profile |
| 8 | Net-new MCP | `yente-mcp` (OpenAleph MCP only if the optional profile is enabled) |
| 9 | Output | Portable Markdown + CSV default; Obsidian auto-adapter via the shared Librarian skill |
| 10 | Librarian | Shared structured-notes skill, own repo, auto-pulled via `dependencies`; distinct from prose-craft |
| 11 | Brand | Fieldwork pure-pointer marketplace → Magpie / Research / Librarian / Publisher |
| 12 | research-workflow | Name kept stable; branded at display layer; migrates to Librarian as a fast-follow |
| 13 | `foia-request` | Deferred; replaced by the novel `request-the-gap` output on analysis skills |
| 14 | Obsidian | Optional output adapter (auto-detect vault), not a core dependency |
| 15 | Prior-art | Tier-1 scan done (10 briefs); Tier-2 per-skill research gate baked into the build plan |

---

## 4. Architecture

```
                         ┌─────────────────── SHARED SPINE ───────────────────┐
  sources ──▶ ingest ──▶ │  (provenance-preserving; generic → future "core")   │ ──▶ output layer
 (docs, CSV,            │  ingest · investigate(verify) · redaction-check ·    │   (Librarian:
  web)                  │  redact-output · archive-evidence · setup            │    MD/CSV default,
                         └──────────────┬───────────────────────┬──────────────┘    Obsidian adapter)
                                        │                        │
                        ┌───────────────▼──────────┐  ┌──────────▼───────────────┐
                        │  TRACK A — Dataset analysis│  │ TRACK B — Entity-network │
                        │  dataset-analyze (+stats)  │  │ entity-extract           │
                        │  analysis-recipe + rollup  │  │ entity-graph             │
                        │  pii-sweep                 │  │ (OpenSanctions engine)   │
                        └────────────────────────────┘  └──────────────────────────┘
                        FOIA-specific (in magpie): foia-exemptions · request-the-gap
```

**Layered build** (each layer ships working value; Track A is laptop-local, heavy infra only appears at Layer 2):

- **Layer 0 — skeleton.** Repo, `plugin.json`, bundled public sample, dual-onramp README, `setup`/`doctor` wizard.
- **Layer 1 — laptop-local (no heavy Docker).** Full Track A (dataset-analyze + stats + analysis-recipe + pii-sweep), plus spine: ingest, redaction-check, redact-output, investigate (verify discipline), archive-evidence (minimal), Librarian output. Validates against the real Simpsonville corpus immediately.
- **Layer 2 — entity stack.** Track B: entity-extract, entity-graph, Neo4j + yente + `yente-mcp`, cross-referencing, request-the-gap.
- **Layer 3 — opt-in + capstone.** Optional profiles (OpenAleph / Datashare / Marker / DocumentCloud); deepened verification/archival; cross-source rollup polish.

---

## 5. Components

Engine choices below reflect the Tier-1 prior-art scan (§10). Each skill's build task begins with a Tier-2 just-in-time research + Context7 API check (§9).

### 5.1 Shared spine (skills) — generic, factored for a future `investigation-core` extraction

| Skill | Purpose | Engine / key heuristics | Layer | License |
|---|---|---|---|---|
| `ingest` | Docs + structured data → clean, provenance-preserving form | **Docling (MIT)** + **RapidOCR** default + **OCRmyPDF** deskew/`--redo-ocr`; keep **`DoclingDocument` JSON internally** (never Markdown — preserves page+bbox for citations); **text-layer quality gate** before re-OCR; Bates-number regex; CSV/XLSX path with **truncation + data-quality checks** | 1 | MIT |
| `investigate` | Verification orchestrator (both tracks) | schema extract → **independent verifier agent** → **human gate (evidence shown before AI claim)**; **citation-anchor fallback chain** (char-offset → text-hash → block-index → page); encodes rigor guardrails (§7) | 1→3 | MIT |
| `redaction-check` | Find bad redactions (input side) | **Free Law x-ray (BSD-2)** + layered checks: text-layer sweep, metadata/XMP, incremental-save (`%%EOF`>1), unapplied `/Redact`, embedded-file enum; dual `--mode` (`received` / `pre-publish`) | 1 | BSD-2 |
| `redact-output` | PII redaction on output | names→initials in published artifacts; full exhibits stay local (non-vault) — implements the Simpsonville redaction policy | 1 | MIT |
| `archive-evidence` | Provenance & chain-of-custody | hash-on-receipt (SHA-256) + **RFC 3161** timestamp + manifest + append-only custody log; wrap **Bellingcat Auto Archiver** (WACZ) for web; writes via Librarian | 1→3 | MIT |
| `setup` | Onboarding wizard | `magpie setup` (operator, once) / `magpie doctor` (journalist, health-check); Docker `healthcheck`+`service_healthy`+`start_period`; Compose **profiles → `.env`**; MCP wiring (creds in local scope); tier-detect → capability map (à la research-workflow `detect_tier`) | 0-1 | MIT |

### 5.2 Track A — Dataset analysis (flagship)

| Skill | Purpose | Engine / key heuristics | Layer |
|---|---|---|---|
| `dataset-analyze` | Quantitative analysis of CSV/XLSX | fast columnar (**Parquet/DuckDB**) + derived-column recipes + **stats module** (Gini/concentration, distribution-by-category, automation signature, burstiness, rates); query via **`mcp-sqlite`** (pinned, read-only `?mode=ro`, row-cap, canned-queries-as-tools) / Datasette; loading heuristics: chardet pre-flight, TEXT-whitelist (ZIP/FIPS/IDs), `--empty-null`, openpyxl unmerge, auto-FTS5 on name columns, `analyze-tables` anomaly warnings | 1 |
| `analysis-recipe` + rollup | Repeatable per-source checklist + cross-source rollup | encodes the 13-point Flock checklist as a parameterized pass run identically per source; **Workflow tool** for per-source fan-out; rolls findings up to test recurrence theses | 1-2 |
| `pii-sweep` | Find/quantify PII in a free-text column | **spaCy `en_core_web_lg`** over distinct texts + structured-PII regex; feeds `redact-output`; mode of the entity NER engine, distinct from graph-building | 1 |

*(Prototype already exists: the Simpsonville `build_cache.py`, `sv.py`, `stats.py`, `pii_ner.py` — Magpie productizes these into repeatable skills.)*

### 5.3 Track B — Entity-network

| Skill | Purpose | Engine / key heuristics | Layer |
|---|---|---|---|
| `entity-extract` | Schema entity + **relation** extraction | **GLiNER2** (entities) + **GLiNER-Relex** (joint NER+RE) + **GLiDRE** (document-level relations); FtM mapping via **deterministic functions, not LLM YAML**; per-span provenance (`{doc_id,page,char_start,char_end,model,confidence}`); cap ~15-20 entity types; filter pairs by type compatibility | 2 |
| `entity-graph` | Resolve → graph → cross-ref | **rigour** (normalize/fingerprint) + **nomenklatura** (resolution; 3-bucket routing >95/70-95/<70) + **followthemoney-graph** → **Neo4j**; **yente** for watchlist + own-corpus cross-ref; **HITL review queue** (the differentiator); pin **FtM 4.0** | 2 |

### 5.4 FOIA-specific (stay in `magpie`)

| Skill | Purpose | Engine / key heuristics | Layer |
|---|---|---|---|
| `foia-exemptions` | Exemption reference + scrutiny | bundle **DOJ OIP Guide b1–b9 (public domain)** + foreseeable-harm framework + exclusions; classifier emits **flags-as-leads, never "improper" verdicts**; circuit-awareness caveat; state mode deferred | 1 |
| `request-the-gap` | *(replaces `foia-request`)* Suggest follow-up requests | from detected redactions / missing records / truncation → targeted follow-up request fragments (incl. "request weekly/native export to dodge the 2²⁰ row cap"); novel, analysis-native | 2 |

### 5.5 Agents
- `extraction-verifier` — re-reads the cited source span independently; judges support (adversarial gate); structurally separate prompt from the extractor.
- `citation-checker` — confirms every claim carries a resolvable anchor whose text matches; flags uncited/mis-cited claims.

### 5.6 MCP servers
- **`yente-mcp`** (net-new build) — thin MCP over the yente match API: search / get-entity / cross-reference (watchlist + own-corpus) / list-datasets. Layer 2.
- **`mcp-sqlite`** (wired, pinned, read-only) — for `dataset-analyze`. *Anthropic's `mcp-server-sqlite` is archived with an unpatched SQLi CVE — do not use. `datasette-mcp` is not a stable purpose-built server.* Layer 1.
- **`openaleph-mcp`** — only if the optional OpenAleph profile is enabled.

### 5.7 Output layer — the shared **Librarian** skill

Librarian is its own repo/plugin (Fieldwork member), auto-pulled by both Magpie and Research via `dependencies`. It authors **structured, interlinked, browsable knowledge notes organized for follow-up and retrieval** — explicitly *not* prose (prose-craft is a separate ceremony; the two must never trigger on each other).

- **Extracted from research-workflow** Stages 6/7/8 (classify → write → wikilink-scan), which are clean agent-instruction units. Extraction work = decoupling, not rewriting:
  1. make vault-index queries optional (portable-first; vault-aware when present),
  2. generalize the input contract to `[{title, content, frontmatter_meta, citations, link_hints, priority, action}]`,
  3. parameterize taxonomy/folder-conventions as config (not hardcoded surveillance-vault values).
- **Output:** portable Markdown notes (frontmatter, sources/citations) + **CSV for tabular payloads** (stats, redacted exhibits); when a vault is configured, places notes + adds wikilinks (companion `wikilink-scanner` agent) + enforces the vault redaction policy.
- **Consumers:** `investigate` (findings reports), `analysis-recipe` (findings notes + rollup), `archive-evidence` (manifests).
- **Migration:** research-workflow's Stages 6/7/8 delegate to Librarian as a fast-follow (so Magpie v1 isn't blocked on refactoring a working plugin; end state = one source of truth).

### 5.8 Infra (Docker, bundled in the plugin)
- **Layer 1 core:** mostly local Python (Docling, DuckDB/pandas, spaCy, x-ray) + SQLite/Datasette + `mcp-sqlite`. No heavy Docker required for Track A.
- **Layer 2:** `docker-compose.yml` — Neo4j (Community) + yente (+ OpenSearch).
- **Opt-in profiles (`infra/profiles/`):** `openaleph.yml` (full document platform), `datashare.yml` (AGPL), `marker.yml` (non-free weights), `documentcloud.yml`.

---

## 6. Data flow — two worked journeys

### 6.1 Track A — the Flock/Simpsonville pass (flagship)

1. **Receive** FOIA CSVs (outbound + network audit logs). `archive-evidence`: hash + timestamp + manifest on receipt.
2. **`ingest`** structured-data path → `dataset-analyze` builds a Parquet cache with derived columns (`geo`, `reason_cat`, `is_immigration`, `has_case`, temporal). **Truncation check first** (row count == 2²⁰−1 → flag, suggest re-request via `request-the-gap`).
3. **`analysis-recipe`** runs the 13-point checklist: out-of-state %, immigration, **pretext** (free-text categorization w/ keyword-verification guardrail), accountability, blast-radius/mega-users, co-travel, operations, **statistical patterns** (Gini, net-width-by-severity, automation signature, burstiness via the stats module).
4. **`pii-sweep`** (spaCy NER) quantifies reason-field PII exposure.
5. **`investigate`** verification gate: every headline claim carries a citation + passes the verifier; rigor guardrails enforced (§7).
6. **Output** via Librarian: hub-and-spoke findings notes (vault if configured, portable MD otherwise); `redact-output` redacts third-party PII to initials in published notes; full exhibits → local CSV only.
7. **Rollup:** repeat per agency (Workflow fan-out) → cross-source rollup tests the recurrence thesis.

### 6.2 Track B — a document → entity → graph investigation

1. `ingest` documents (Docling, provenance preserved).
2. `entity-extract` → entities + relations (GLiNER2/GLiNER-Relex/GLiDRE) → FtM (deterministic mapping), per-span provenance.
3. `entity-graph` → rigour normalize → nomenklatura resolve (3-bucket; HITL review queue for 70–95%) → Neo4j graph → yente cross-ref against watchlists + own corpus.
4. (optional) `research` web corroboration of surfaced entities; `archive-evidence` snapshots web sources.
5. `investigate` verification gate → Librarian findings output.

---

## 7. Verification & safety model

Magpie treats LLM output as **unverified source material** (the ProPublica posture). Non-negotiable for publishable claims.

- **Citation contract.** Every extracted claim carries `{claim_text, verbatim_quote, doc_id, page, block_index, text_hash, verifier_result, verifier_confidence, extractor_model, prompt_version, schema_version, timestamp}`. Anchor uses the **fallback chain** (char-offset → text-hash → block-index → page) to survive OCR instability.
- **Independent verifier.** A structurally-separate sub-agent re-reads the cited span (presence + entailment checks); `indeterminate` is the conservative default. Single-model self-verification is disallowed (correlated errors; LLM-judge recall ~16%).
- **Human gate.** Mid-pipeline, not final. The verification card shows the **source span before the AI claim** (counters automation bias; documented 51% rubber-stamp rate). Supports two-reviewer sign-off (logged).
- **PII discipline.** `pii-sweep` finds exposure; `redact-output` + the Obsidian adapter enforce vault→initials / exhibits-local-only.
- **Rigor guardrails** (encoded from the Simpsonville handoff): walk back claims the data doesn't support; verify keyword matches (the `ICE`/"polICE" trap); `***` ≠ blank (presence vs. value); refuse out-of-scope claims (e.g., racial disparity from audit logs); window-asked ≠ retention-proven.
- **Honest limits (documented, not hidden):** zero-shot relation-extraction F1 ~25–40 → human gate mandatory, never autonomous; glyph-position / proportional-font pixelation / cross-version-diff / semantic-reconstruction redaction failures have no FOSS auto-detector → flag for humans; worst-case scans & handwriting → no CPU-viable solution → flag for review; MCP ecosystem is security-immature (30+ CVEs in 2026) → servers touching FOIA data stay read-only, pinned, treated as untrusted.

---

## 8. Licensing model

- **Magpie's own code:** MIT.
- **Default stack is permissive-only:** Docling (MIT), x-ray (BSD-2), GLiNER family (Apache-2.0), OpenSanctions tooling (MIT), Datasette/SQLite/DuckDB (Apache/PD), Neo4j Community, spaCy (MIT).
- **Copyleft/non-free as clearly-labeled opt-in profiles:** Datashare (AGPL), DocumentCloud (AGPL), Marker (non-free weights), OpenAleph (MIT but heavy).
- **AGPL reasoning (for the optional profiles):** AGPL reaches the *software and modifications*, not the *output* — extracted entities, graphs, notes, and journalism are unencumbered. Running stock images and calling their APIs across a process boundary imposes no obligation on Magpie's MIT code; only modifying-and-network-serving those tools would. Hence opt-in profiles are safe for normal use.

---

## 9. Testing & validation

- **Golden-output tests** against the **real Simpsonville corpus** (private; never committed — PII): truncation detection, geo/immigration/pretext counts, Gini and net-width statistics, PII-sweep tallies — pinned to the values documented in the pilot.
- **Bundled clean public sample** (redistributable, PII-free government release) for "try it now" onboarding and CI golden tests.
- **Structural smoke tests:** skills load, MCP servers start, Docker health, recipe runs end-to-end.
- **Librarian auto-pull acceptance test (hard requirement):** install `research-workflow` alone on a clean profile → confirm Librarian auto-installs and Stages 6/7/8 work; documented co-install fallback if cross-repo dependency resolution has rough edges.
- **Tier-2 research gate:** each skill's build task begins with a just-in-time prior-art refresh + Context7 API check; the full Tier-1 briefs become each skill's `references/prior-art.md`.

---

## 10. Prior-art deltas (Tier-1 scan summary)

| Skill | Delta from naive design |
|---|---|
| ingest | Tesseract→**RapidOCR** default; drop standalone Surya (CPU too slow); add OCRmyPDF; keep DoclingDocument JSON (bbox); text-layer quality gate |
| entity-extract | GLiREL→**GLiNER-Relex** + add **GLiDRE** (doc-level); FtM via deterministic mapping; human gate mandatory (F1 ~25–40) |
| entity-graph | **Reuse OpenSanctions stack** (don't build ER); **yente** = watchlist + own-corpus; HITL review queue is the value-add; pin FtM 4.0 |
| dataset-query | No stable `datasette-mcp`; Anthropic sqlite server archived (CVE) → **`mcp-sqlite`** pinned read-only; loading heuristics; Splink for linkage |
| investigate | 3-stage; **citation-anchor fallback chain**; independent verifier; evidence-before-claim gate |
| archive-evidence | hash + RFC3161 + manifest + custody; wrap Auto Archiver (WACZ); **adapt Librarian writer**; genuine whitespace for FOIA receipts |
| setup | `setup`/`doctor` split; healthchecks + `start_period`; profiles→`.env`; creds in local scope; tier-detect |
| redaction-check | x-ray + layered checks (text-layer, metadata, `%%EOF`, unapplied `/Redact`, embedded); dual `--mode` |
| foia-exemptions | DOJ Guide public-domain bundle; **flags-as-leads not verdicts**; circuit caveat; state deferred |
| foia-request | **DEFER** → `request-the-gap` analysis-native output |

**magpie's genuine whitespace (what it invents):** NLP→FtM relation mapping; stable citation anchors for OCR'd FOIA PDFs; FOIA-receipt provenance/custody; per-document exemption classifier; entity-resolution HITL review queue; request-the-gap.

---

## 11. Open risks & verify-at-implementation

- Cross-repo `dependencies` auto-pull for Librarian (acceptance-tested; fallback = documented co-install). Verify exact `dependencies` / `allowCrossMarketplaceDependenciesOn` / version-tag syntax against live Claude Code docs.
- `mcp-sqlite` maturity (alpha) — pin, audit, row-cap; consider a thin custom wrapper if unreliable.
- `GLiNER-Relex` / `GLiDRE` packaging & CPU latency — confirm pip availability and throughput; GLiREL fallback if needed.
- `yente` footprint (needs OpenSearch) — confirm laptop-viability for Layer 2; document RAM needs.
- FtM 4.0 breaking changes — pin versions in the Docker image.
- Citation-anchor format for OCR'd PDFs is a magpie invention — prototype + validate on the real corpus early.

---

## 12. Out of scope / deferred / future

- `foia-request` standalone drafting (→ `request-the-gap`).
- OSINT verticals (geo, transport, social, image/video) — future Fieldwork plugins or Magpie skills.
- `investigation-core` extraction (split the spine into a shared plugin) — when a 2nd vertical pulls on it.
- OpenAleph / Datashare / DocumentCloud / Marker — opt-in profiles, not default.
- C2PA provenance; signed WACZ; state-PRA exemption classification — deferred (immature / out of v1 scope).
- Lightweight export helpers for DocumentCloud / Google Docs — future.

---

## Appendix — provenance of this design

Brainstormed 2026-06-03. Key inputs: the external FOIA-tooling report; a 10-skill Tier-1 prior-art scan executed with the research-workflow method (T1–T4 source tiering, confidence-gated hops); and the DeflockSC Simpsonville Flock audit handoff (the flagship use case and the source of the rigor guardrails). Full Tier-1 briefs to be saved as per-skill `references/prior-art.md` during implementation.

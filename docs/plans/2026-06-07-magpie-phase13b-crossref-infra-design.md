# Magpie Phase 13b -- cross-ref + infra (Track B / Layer 2) -- Design

Date: 2026-06-07. The WHY for Phase 13b. Inputs: research gate B
(`.codex-review/research/phase13-gate-B-crossref-infra.md`) + its authoritative
re-validation against yente v5.4.0 source
(`.codex-review/research/phase13b-gateB-validation-2026-06-07.md`), the Codex
brainstorm (`.codex-review/phase13b-brainstorm-out.txt`, a fresh
`[CHAIN-BOUNDARY]`), the master design section 9 (the 13b outline), and Tim's
fixed product decisions. It CONSUMES the Phase-13a portable resolved snapshot
(`scripts/entity_resolved_snapshot.py`, `assert_snapshot_consumable`) UNCHANGED;
it does NOT reach back into the nomenklatura resolver DB.

ASCII-only (SDD subagents read this file). The HOW is the implementation plan
(`docs/plans/2026-06-07-magpie-phase13b-crossref-infra.md`, written next).

---

## 1. Scope & deliverables

Phase 13b = `entity-crossref`: take the Phase-13a resolved snapshot, index it as a
private yente dataset, and cross-reference the resolved entities against (a) the
investigator's OWN corpus (the pure-FOSS default, zero external data) and (b)
sanctions / PEP WATCHLISTS (opt-in, CC-BY-NC, documented). A net-new thin
read-only `yente-mcp` server exposes the same lookups to the model. This is the
second heavy-infra (Docker) phase; it extends 13a's `infra/docker-compose.yml`.

Deliverables:
- `scripts/entity_yente_dataset.py` -- pure core: resolved snapshot -> a yente
  entities file (line-delimited FtM JSON) + the manifest render helper.
- `scripts/entity_crossref.py` -- pure core: /match request/response SHAPING,
  typed `CrossRefHit`s, scope grouping, threshold/cap policy, the cross-ref
  report assembler (with the index-provenance block).
- `scripts/entity_yente_client.py` -- the live edge (the ONLY httpx importer): a
  thin yente HTTP client (match/search/get_entity/catalog/readyz) + the
  batched `run_crossref` fan-out.
- `scripts/yente_mcp_server.py` -- the thin read-only FastMCP server (5 tools).
- `infra/docker-compose.yml` -- extended with `index` (OpenSearch 2.19.5) +
  `yente` (5.4.0) under a NEW `crossref` profile.
- `infra/yente/magpie-own.yml` + `infra/yente/magpie-watchlist.yml` -- the two
  manifests (clean default / explicit opt-in).
- `.mcp.yente.example.json` -- the copy-paste operator wiring for yente-mcp.
- `skills/entity-crossref/SKILL.md` + `references/prior-art.md`.
- `detect_tier` Layer-2 "cross-reference entities" capability + setup/doctor
  wording; the new CI `crossref` job; new pytest marker(s).

The 13a/13b seam is the resolved snapshot: 13a emits it, 13b consumes it
UNCHANGED. `assert_snapshot_consumable(snapshot)` is the entry check before any
emit (mirrors `entity_ftmize.assert_phase13_consumable`).

---

## 2. Fixed product decisions (NOT re-litigated)

From the master design + Tim's decisions, reaffirmed by this brainstorm:

1. **Watchlists are OPT-IN.** Own-corpus cross-ref (zero external data) is the
   pure-FOSS DEFAULT; the watchlist pull (OpenSanctions `default`, CC-BY-NC) is a
   deliberate enable, NC documented in the skill + OPERATOR_GUIDE + release notes.
   Ship the FREE civic-style catalog, NEVER the commercial token-gated one.
2. **Layer-2, operator-tier, Docker-gated.** The journalist onramp
   (`JOURNALIST_START`, the `doctor` Track-A capabilities) stays Docker-free and
   is untouched. `setup`/`doctor` only PROBE for Docker (read-only).
3. **Two PRs:** 13a (shipped) then 13b (this one).
4. **MCP servers are read-only + version-pinned** (master design 7); treat the
   yente server as untrusted.

---

## 3. Architecture & module decomposition

The suite's pure-core / lazy-edge / decoupled-boundary pattern, extended. The
DATASET-EMIT and CROSS-REF-SHAPING cores are pure stdlib (Windows-testable,
golden with fakes); the yente HTTP client and the MCP server are the live edges.
The CI `crossref` job is the ONLY real verification surface for the live edges
(the load-bearing Phase-12/13a lesson).

| Module | Tier | Responsibility |
|---|---|---|
| `scripts/entity_yente_dataset.py` | pure core (Windows; stdlib only) | resolved snapshot -> FtM entity dicts -> line-delimited yente entities file; `render_manifest(...)` -> manifest YAML; content-hash dataset versioning |
| `scripts/entity_crossref.py` | pure core (Windows; stdlib only) | `build_match_query`, `CrossRefHit`, `parse_match_response`, `group_hits_by_dataset`, threshold/cap policy, `build_crossref_report` (incl. the index-provenance block) |
| `scripts/entity_yente_client.py` | live edge (only httpx importer) | thin yente HTTP client (match/search/get_entity/catalog/readyz; loopback guard) + batched `run_crossref` fan-out per scope |
| `scripts/yente_mcp_server.py` | live edge (mcp + the two cores above) | thin read-only FastMCP server: search/match/get_entity/list_datasets/cross_reference; caps, timeouts, scope allowlist, loopback guard |
| `infra/docker-compose.yml` | infra | add `index` + `yente` under the `crossref` profile (Neo4j stays `graph`) |
| `infra/yente/magpie-own.yml` / `magpie-watchlist.yml` | infra | default own-corpus manifest / opt-in watchlist manifest |
| `skills/entity-crossref/SKILL.md` | orchestration | the operator-run cross-ref flow |

Decoupling (Codex D4 fold): the pure SHAPING (`entity_crossref`) and the live
HTTP EDGE (`entity_yente_client`) are SEPARATE files -- never mixed, exactly as
13a split `entity_resolution_policy` (pure) from `entity_nomenklatura` (edge).
`entity_yente_dataset` imports NO yente/httpx/followthemoney -- it emits the FtM
JSON dict shape directly (the snapshot already carries `canonical_id`/`schema`/
`properties`), so it is Windows-golden-testable; an `ftm`-marked Linux/CI test
validates the emitted file against followthemoney (`ftm validate` / model
round-trip) as a contract check.

---

## 4. Key design decisions

### D1. Two manifests; the default never names the external catalog
The shipped `manifests/civic.yml` is NOT zero-data -- it still pulls the full
OpenSanctions `default` catalog (CC-BY-NC, multi-GB). So Magpie ships TWO files:

- `infra/yente/magpie-own.yml` (DEFAULT): `catalogs: []` (omitted) +
  `datasets: [magpie_corpus]`. ZERO external pull. The default file does not even
  contain the external URL.
- `infra/yente/magpie-watchlist.yml` (OPT-IN): the same `magpie_corpus` dataset
  PLUS a `catalogs:` block for the FREE civic `default` catalog
  (`https://data.opensanctions.org/datasets/latest/default/catalog.json`, no
  token). The operator opts in by pointing `YENTE_MANIFEST` at this file.

Opt-in is a single documented env-var swap (`YENTE_MANIFEST`), not YAML editing.
Both manifests + the CC-BY-NC term are documented in the skill, OPERATOR_GUIDE,
and release notes so the NC pull can never be called "implicit."

### D2. Own-corpus dataset = per-investigation v1, multi-snapshot-ready
The `magpie_corpus` yente dataset v1 contains ONLY the current investigation's
resolved snapshot (keeps 13a's per-investigation isolation; no global/cross-
investigation index yet -- that is deferred, section 9). Honest framing (Codex
D2): resolution already merged same-entity mentions WITHIN the investigation, so
own-corpus `/match` is NOT re-resolution -- it is (i) a fuzzy lookup/search over
the resolved dataset and (ii) the plumbing for future multi-snapshot indexing.
The headline value of 13b is WATCHLIST screening; own-corpus is the FOSS-default
fuzzy lookup. The emitter + manifest take a LIST of snapshot files, so indexing
several snapshots later is config, not code. Do not oversell own-corpus `/match`.

### D3. Namespacing + id join-back
- The emitted own-corpus FtM entity keeps `id = canonical_id` (the stable
  sha256-of-sorted-member-ids from 13a).
- The yente manifest sets `namespace: true` on the dataset -- yente generates
  dataset-scoped ids at index time (collision-avoidance, esp. once multiple
  snapshots are indexed). NOT `ftm sign -s` (it mutates ids upstream and breaks
  the Neo4j/member-id join).
- Attribution is QUERY-SIDE and is the SOLE contract: cross-ref POSTs OUR entity
  as a `/match` query keyed by a query-KEY we choose (= `canonical_id`); yente
  returns `responses[<query-key>]`, so each result set attributes to our entity
  via the key, never the (possibly namespaced) result id. This is sufficient --
  we always know which entity we queried -- so namespacing the index is harmless.
  (Local-smoke-confirmed: a 40-char canonical-style query key round-trips as the
  response key.)
- Do NOT emit a `canonicalId` FtM property (Codex design-review ftm-joinback):
  `canonicalId` is not a known FtM field, the 13a snapshot only guarantees a
  generic `properties` bag + schema/caption/aliases, and
  `assert_snapshot_consumable` does not validate FtM vocabulary -- so an invented
  property is unproven for `ftm validate` / yente ingest. The emit therefore
  carries ONLY snapshot-derived valid FtM props, and the `ftm`-marked contract
  test (section 6) that the emit passes `ftm validate` is LOAD-BEARING.
- Result-side recovery (NOT needed for the primary flow): the local smoke shows
  yente's `namespace:true` id is `<our-id>.<hmac-hex>` (our `canonical_id` is a
  PREFIX of the result id), so a result id is recoverable to our id by stripping
  at the first dot. Treat this as an OBSERVED convenience for manual inspection,
  NOT a contract (it is yente's internal scheme); if a tool ever needs guaranteed
  result-side recovery, build a local `{result_id -> canonical_id}` sidecar map
  OUTSIDE the FtM payload, never an FtM property.

### D4. Module split (pure shaping vs live edge) -- see section 3
`entity_crossref.py` (pure) builds the /match body and parses/groups responses;
`entity_yente_client.py` (edge, only httpx) does the live calls. The MCP server
and the skill BOTH drive the live client through the same pure shaping -- one
code path, two front-ends.

### D5. New `entity-crossref` skill (entity-graph stays as-is + a pointer)
Cross-ref is a distinct operator workflow with distinct infra (index+yente) and
distinct risk (CC-BY-NC data, an untrusted networked server). It gets its OWN
skill `skills/entity-crossref/SKILL.md`; `skills/entity-graph/SKILL.md` keeps the
existing one-line "cross-ref is the entity-crossref skill, 13b" pointer (already
present). entity-graph is unchanged otherwise.

### D6. yente-mcp is operator-wired, never in the default `.mcp.json`
Magpie's default `.mcp.json` (the `magpie-dataset` mcp-sqlite server) auto-starts
on every session. yente-mcp needs a running yente and is Docker/operator-tier --
it must NEVER auto-start for a journalist. So it is NOT added to `.mcp.json`.
Instead Magpie ships `.mcp.yente.example.json` -- a copy-paste server config the
operator merges into their project `.mcp.json` when doing cross-ref (Codex D6: a
ready snippet, not JSON transcribed from prose). The entity-crossref SKILL +
OPERATOR_GUIDE document the wiring.

### D7. Index freshness / reproducibility (the determinism fold -- Codex)
Cross-ref results must not drift silently underneath the operator. Three measures:
- **Deterministic, explicit indexing:** Magpie's yente runs with
  `YENTE_AUTO_REINDEX=false` -- no hourly background reindex AND no auto-index on
  startup (local-source-confirmed: `yente/app.py` gates the startup index on
  `AUTO_REINDEX`; with it false, "data will only be refreshed and re-indexed when
  running `yente reindex`"). So the index is built by an EXPLICIT, logged step:
  the skill / CI runs `docker compose --profile crossref exec yente yente reindex`
  after bring-up. There is no surprise background reindex.
- **Content-hash dataset versioning:** the emitter derives the own-corpus dataset
  `version` from the entities-file CONTENT HASH, so a changed snapshot is a new
  version -- a deliberate re-`yente reindex` rebuilds it; an unchanged snapshot is
  a no-op. The own-corpus scope is therefore fully REPRODUCIBLE.
- **Provenance in every cross-ref output:** `build_crossref_report` records an
  index-provenance block -- `{manifest_hash, dataset_file_hashes:{name:sha256},
  yente_image_tag, opensearch_image_tag, generated_at, threshold, algorithm,
  scopes}` PLUS, per scope, the yente `/catalog` per-dataset metadata
  `{name, version, updated_at, last_export, index_current}` (local-smoke-confirmed
  these fields are exposed). For the OWN-corpus scope this is a true content
  fingerprint => "reproducible". For the WATCHLIST scope it is only
  "best-effort externally-versioned" (Codex design-review watchlist-drift): the
  external `default` catalog's contents can change behind a stable URL, so
  `version/updated_at` from `/catalog` is the best replayable fingerprint we can
  capture -- we record it and do NOT claim bit-for-bit reproducibility for
  watchlist results. Opting into watchlists is the operator's deliberate choice;
  a re-`yente reindex` may pull fresher sanctions data, and the provenance block
  records exactly what was indexed when.

### D8. yente-mcp safety knobs (untrusted-server posture)
- Fixed base URL via `YENTE_MCP_BASE_URL` (default `http://127.0.0.1:8000`).
  REFUSE a non-loopback base URL unless `YENTE_MCP_ALLOW_REMOTE=1` (explicit).
- Scope names are a FIXED ALLOWLIST exposed to the model -- `own_corpus` ->
  `magpie_corpus`, `watchlists` -> `default`. The model cannot pass arbitrary
  dataset paths/scopes.
- Hard per-request httpx timeout (10s); a cross_reference fan-out total budget
  (~30s). Result cap 25 hits/scope returned to the model. Cap the response body
  size read from yente.
- Effective `MAX_BATCH=1` for the MCP match/cross_reference tools (one query
  entity per call; no bulk pass-through). The SKILL's batch cross-ref of the
  whole graph uses the client's batched fan-out (up to yente's MAX_BATCH=100 per
  call) -- that efficiency lives in the client/skill, NOT the MCP surface.
- NO `/updatez` / reindex / write tool. NO raw pass-through query tool.

### D9. Compose: a `crossref` profile, NOT the default profile
The master design said "default profile" for index+yente, but Docker Compose
starts NO-profile services under EVERY `--profile` invocation -- so default-
profile index+yente would be silently pulled into the existing 13a `graph` /
`compose` CI jobs (which run `--profile graph up`). To keep the 13a jobs isolated
AND keep the design intent (index+yente come up together for cross-ref; Neo4j
stays separate), index+yente go under a dedicated `crossref` profile:
`docker compose --profile crossref up` for cross-ref, `--profile graph up` for the
graph (unchanged). Cross-ref consumes the snapshot JSON, not Neo4j, so the two
profiles are independent.

OpenSearch single-node local env (research gate B section 4, verified):
`discovery.type=single-node`, `DISABLE_SECURITY_PLUGIN=true`,
`DISABLE_INSTALL_DEMO_CONFIG=true`, `bootstrap.memory_lock=true`,
`OPENSEARCH_JAVA_OPTS=-Xms1g -Xmx1g` (laptop heap, not the vendor's 4g),
`OPENSEARCH_INITIAL_ADMIN_PASSWORD=${OPENSEARCH_ADMIN_PASSWORD}` (2.12+ format
check even with security off). ulimits memlock -1/-1, nofile 65536. Volume
`index-data`. Ports `127.0.0.1:9200`. Healthcheck `curl --fail
http://localhost:9200/_cluster/health` with explicit timings + a long
start_period. The index service MUST be named `index` (yente reaches it at
`YENTE_INDEX_URL=http://index:9200`). yente env: `YENTE_INDEX_TYPE=opensearch`,
`YENTE_INDEX_URL=http://index:9200`, `YENTE_INDEX_NAME=yente`,
`YENTE_MANIFEST=/app/manifests/magpie-own.yml` (default),
`YENTE_AUTO_REINDEX=false`, `YENTE_UPDATE_TOKEN=${YENTE_UPDATE_TOKEN}`; leave
`YENTE_OPENSEARCH_REGION/SERVICE` UNSET (self-hosted, no AWS signing).
`depends_on: index: service_healthy`. Mounts: `infra/yente/*.yml ->
/app/manifests/`, the gitignored own-corpus data dir -> `/data`. Ports
`127.0.0.1:8000`. yente healthcheck hits `/healthz` (liveness); the operator/CI
poll `/readyz` (index loaded) before the first `/match`. `.env` gains
`OPENSEARCH_ADMIN_PASSWORD` + `YENTE_UPDATE_TOKEN`.

### D10. WSL2 vm.max_map_count -- documented, not enforced
OpenSearch needs `vm.max_map_count >= 262144`. This dev box already reads
1048576 (Docker Desktop 29.x default), so no change is needed here, but older /
differently-configured Docker Desktop defaults to a low value and OpenSearch then
fails its bootstrap check -- a classic "works then breaks after reboot" trap. The
setup/doctor surfaces already document the WSL2 persistence step (13a Task 8);
13b keeps that documentation and the entity-crossref SKILL points to it.

---

## 5. 13b data flow (operator-run, end to end)

1. Operator has a Phase-13a resolved snapshot (one JSON) for the investigation +
   Docker. Confirm the Layer-2 capability via `doctor` (read-only Docker probe).
2. `entity_yente_dataset.write_dataset(snapshot, data_dir)`:
   `assert_snapshot_consumable(snapshot)` -> emit `entities.ftm.json`
   (line-delimited FtM dicts: `{id:canonical_id, schema, properties{name:[caption]+
   aliases, ...only snapshot-derived valid FtM props}}` -- NO invented
   `canonicalId` prop) into a gitignored data dir + compute the content-hash
   dataset version + render the live manifest.
3. Bring up + EXPLICITLY index (deterministic; AUTO_REINDEX=false does NOT
   auto-index): `docker compose --profile crossref up -d --wait` then
   `docker compose --profile crossref exec yente yente reindex`. Poll `/readyz`
   AND `/catalog` until `index_current` for the dataset (local-smoke-confirmed
   sequence).
4. `entity_yente_client.run_crossref(snapshot, scopes, client)`: for each scope
   (own_corpus always; watchlists only if opted in), batch the resolved entities
   into `POST /match/{scope}` calls keyed by `canonical_id` (the query key =
   attribution handle); `entity_crossref` shapes the queries and parses the
   `responses[<canonical_id>]` sets, grouping hits by `result.datasets`.
5. `entity_crossref.build_crossref_report(hits, index_provenance)` -> the
   cross-ref report (grouped hits + the D7 provenance block).
6. Output via the Librarian: an AGGREGATE findings note (counts per scope, top
   watchlist matches by score, the index-provenance block). Raw matched
   names/snippets are PII -> route any surfaced text through `redact-output`
   (the spine seam). Watchlist hits are LEADS for a human, never verdicts.

Separately, the operator may wire `yente-mcp` (D6) for interactive
search/match/get_entity/cross_reference against the same running yente.

---

## 6. Test & CI strategy (the only real verification surface is CI)

- **Windows / offline pure-core (golden, no infra):** the dataset emit (snapshot
  -> FtM JSONL shape, name/aliases merge, content-hash version, manifest render);
  the cross-ref shaping (`build_match_query` keys by `canonical_id`,
  `parse_match_response` attributes via the response key, `group_hits_by_dataset`,
  threshold/cap policy, `build_crossref_report` provenance); the MCP tool logic
  with a FAKE client (scope allowlist, loopback guard, caps, timeout wiring,
  no-write surface).
- **`ftm`-marked Linux/CI contract test:** the emitted own-corpus entities file
  validates against followthemoney (`ftm validate` / model round-trip) -- guards
  that the pure emit produces FtM yente actually accepts (LOAD-BEARING, since the
  pure emit does not import followthemoney and cannot self-validate the vocab).
- **NEW `crossref` CI job (the gate):** `docker compose --profile crossref
  config` -> `up --wait` (OpenSearch 2.19.5 + yente 5.4.0) -> `exec yente yente
  reindex` (AUTO_REINDEX=false does not auto-index) -> poll `/readyz` + `/catalog`
  index_current -> a REAL `POST /match` smoke that asserts NOT just "a hit" but
  (Codex crossref-smoke) that the self-hit comes back under the originating QUERY
  key (= the entity's `canonical_id`) AND is grouped under `datasets:
  ["magpie_corpus"]`, regardless of the namespaced result id -- i.e. the D3
  attribution path survives namespacing -> a tiny yente-mcp PROCESS smoke (start
  the server against the live yente, exercise one read-only tool) -> teardown.
  NEVER pull the watchlist (CC-BY-NC, multi-GB) in CI -- own-corpus only. EVERY
  compose step (incl. `down -v`) gets its env vars (the 13a per-step-env gotcha).
  New pytest marker `yente` (live yente+OpenSearch); add it to pyproject + the
  offline `-m "not ..."` exclusion + the job. (This whole sequence is
  local-smoke-validated on the EXACT images before any push.)
- RULE (Phase-12/13a lesson): gate the merge on the `crossref` job (plus the
  existing offline + ftm + graph + compose jobs), NEVER on Windows-green +
  Codex-green alone. Stand the EXACT images up LOCALLY before pushing.

---

## 7. Positioning & onboarding (Layer-2, operator-tier, Docker-gated)

`detect_tier` gains a "cross-reference entities (Layer 2)" capability, READY/
UNAVAILABLE off the SAME read-only Docker probe 13a added (cross-ref needs Docker
for index+yente). It does NOT probe a live yente (a health check must not assume
services are up -- the Phase-10 side-effect-free `doctor` rule); runtime yente
readiness is the skill's `/readyz` poll. `setup` (operator) INSTRUCTS installing
Docker + the WSL2 `vm.max_map_count` step; `doctor` (journalist) stays read-only.
No Docker token or cross-ref jargon enters any journalist surface.

---

## 8. Honest limits & risks

- **OpenSearch 2.x is the less-trodden path.** yente is vendor-tested against ES
  8.19.13; self-hosted OpenSearch 2.19.5 is reached via `YENTE_INDEX_TYPE=
  opensearch`. ALREADY PROVEN locally on the EXACT images (2.19.5 + yente 5.4.0):
  the AUTO_REINDEX=false + `yente reindex` path indexes an own-corpus dataset and
  answers `POST /match` with a correctly-attributed self-hit. The `crossref` CI
  job makes that proof continuous -- not "the vendor says ES8 works."
- **Watchlist data is CC-BY-NC.** Opt-in, documented; the FOSS default pulls zero
  external data. Magpie CODE stays MIT; the data term attaches only when the
  operator opts in. Commercial users need the paid delivery token (never shipped).
- **Resolution precision is unverified on FOIA names (carried from 13a).** A
  watchlist `/match` hit is a LEAD for a human, never a verdict; the report frames
  hits as candidates with scores + provenance, and surfaced names route through
  `redact-output`.
- **Own-corpus `/match` is a fuzzy lookup, not new resolution.** Do not oversell
  it (D2).
- **Index drift.** Own-corpus is REPRODUCIBLE (content-hash version +
  `AUTO_REINDEX=false` + explicit `yente reindex`). The watchlist scope is only
  "best-effort externally-versioned" -- the external `default` catalog can change
  behind a stable URL, so a re-`yente reindex` may pull fresher data; the
  provenance block records the `/catalog` per-dataset `version/updated_at` so a
  result names exactly what was indexed when (D7). We do not claim bit-for-bit
  watchlist reproducibility.
- **The own-corpus entities file is PII-derived** (corpus names) -> it lives in a
  gitignored data dir; never committed (the never-commit-corpus rule).
- **CI cost:** an OpenSearch + yente bring-up per run is the price of honest
  verification; kept minimal (tiny own-corpus, no watchlist pull).

---

## 9. Out of scope / deferred

- The ACCUMULATING multi-investigation own-corpus index (v1 is per-investigation;
  the emitter/manifest are multi-snapshot-ready, but the cross-investigation
  dedup/version story is deferred).
- The commercial / token-gated OpenSanctions delivery catalog (never shipped).
- Indexing snapshot EDGES into yente (v1 emits NODE entities -- the screening
  targets; relationships stay in Neo4j).
- A yente reindex/write MCP tool (read-only server only).
- Any yente/OpenSearch UI (the API + MCP are the surface).
- Auto-bumping yente past 5.4.0 (the last ES8 line; re-verify OpenSearch-2.x
  compat before any bump).

---

## 10. Open items -> implementation plan (13b)

- Exact module APIs + dataclasses (`CrossRefHit`, `CrossRefReport`,
  `DatasetEntry`, the emit/shaping/client/MCP signatures).
- The FtM property mapping for the emit + the /match query (name/aliases/
  birthDate/country/address from snapshot `properties`); the content-hash version
  scheme; the `render_manifest` shape for own vs watchlist.
- The exact compose `crossref`-profile YAML (index + yente env, healthchecks,
  mounts, ports) + the `.env` additions + `.env.example` update.
- The two manifest files; `.mcp.yente.example.json`.
- The yente-mcp FastMCP tool signatures + the caps/loopback/allowlist enforcement.
- The `crossref` CI job YAML (config -> up -> readyz -> index -> /match smoke ->
  mcp smoke -> teardown) + the `yente` marker wiring (pyproject + offline
  exclusion + job).
- `detect_tier` capability addition + setup/doctor wording.
- The `entity-crossref` SKILL.md operator flow + the Librarian aggregate output +
  `references/prior-art.md` (the verified library facts).
- requirements: a new `requirements-crossref.txt`? (httpx + mcp SDK,
  cross-platform) vs adding to an existing file -- confirm in the plan. Pin the
  yente + OpenSearch image tags (by tag; digest-pin optional) + the mcp SDK.
- The Context7 re-verify of the mcp FastMCP API before writing the server.

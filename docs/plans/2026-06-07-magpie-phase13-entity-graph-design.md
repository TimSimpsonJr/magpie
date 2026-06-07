# Magpie Phase 13 -- entity-graph (Track B / Layer 2) -- Design

Date: 2026-06-07. The WHY for Phase 13. Inputs: the two research gates
(`.codex-review/research/phase13-gate-A-resolution-graph.md`,
`phase13-gate-B-crossref-infra.md`), the Codex brainstorm
(`.codex-review/phase13-brainstorm-codex-out.txt`, a fresh `[CHAIN-BOUNDARY]`),
the master design (5.3 Track B, 5.6 MCP, 5.8 infra, 7 verification, 8 licensing),
and Tim's product decisions (this session). It CONSUMES the Phase-12
`entity_ftmize` FtM bundle + the reviewed-intermediate contract
(`scripts/entity_ftmize.py`, `assert_phase13_consumable`).

ASCII-only (SDD subagents read this file). The HOW is the implementation plan
(`docs/plans/2026-06-07-magpie-phase13a-*.md`, written next).

---

## 1. Scope & deliverables

Phase 13 = `entity-graph`: take the per-document reviewed FtM bundles Phase 12
produces, RESOLVE which entities across documents are the same real-world
person/org, build a Neo4j graph of the resolved network, and cross-reference the
entities against sanctions/PEP watchlists + the investigator's own corpus via
yente. It is the FIRST heavy-infra (Docker) phase.

Shipped as TWO sequential PRs (Tim's call):

- **13a -- resolution + Neo4j (this design details it):** rigour/nomenklatura
  3-bucket resolution with a HITL review packet -> a portable **resolved
  snapshot** -> a Neo4j graph; an `infra/docker-compose.yml` introducing Neo4j.
- **13b -- cross-ref + MCP (outlined in section 9; its own design when 13a
  ships):** yente watchlist + own-corpus cross-ref + OpenSearch + a net-new
  `yente-mcp` server, extending the compose.

The 13a/13b seam is the resolved snapshot: 13a emits it; 13b consumes it
UNCHANGED. Designing that seam now is what prevents 13b rework (Codex's point).

---

## 2. Fixed product decisions (Tim, this session -- not re-litigated)

1. **Auto-merge:** the top-confidence bucket auto-merges, but every auto-merge is
   LOGGED with its evidence and is REVERSIBLE; the human ACTIVELY reviews only the
   middle "maybe" band. Nothing merges unaccountably.
2. **Review UI:** an HTML "review packet" (self-contained page; each candidate
   pair a side-by-side card with evidence + score + merge/distinct/unsure);
   verdicts return as a small JSON the skill applies. Text-inline is the fallback
   for a handful of pairs. NO running server. The HTML gets mocked-up for Tim's
   sign-off before it is built.
3. **Watchlists:** OPT-IN. Own-corpus cross-ref (zero external data) is the
   pure-FOSS default; watchlist cross-ref (OpenSanctions, CC-BY-NC) is a
   deliberate enable, NC documented. Ship the FREE civic manifest, never the
   commercial token-gated one.
4. **Scope/sequencing:** two sequential PRs (13a then 13b), full scope.
5. **Positioning:** entity-graph is Layer-2, OPERATOR-tier, Docker-gated. The
   journalist onramp stays Docker-free; `setup`/`doctor` grow a Layer-2 Docker
   probe (they do NOT auto-install Docker).

---

## 3. Architecture & module decomposition

The suite's pure-core / lazy-edge / decoupled-boundary pattern, extended. The
POLICY, SNAPSHOT, and REVIEW-PACKET cores are pure stdlib (Windows-testable,
golden with fakes); nomenklatura and the Neo4j driver are the Linux/CI/Docker
EDGES (the CI `ftm` job + a new Neo4j-service job are the ONLY real verification
surface -- the load-bearing Phase-12 lesson).

| Module | Tier | Responsibility |
|---|---|---|
| `scripts/entity_resolution_policy.py` | pure core (Windows) | score buckets, threshold config, the STABLE `canonical_id` derivation, candidate/verdict dataclasses |
| `scripts/entity_review_packet.py` | pure core (Windows) | candidate-snapshot JSON, self-contained HTML packet render, verdict-JSON schema + validation, stale-pair guard logic |
| `scripts/entity_resolved_snapshot.py` | pure core (Windows) | the portable resolved-snapshot schema + serializer (the 13a deliverable / 13a->13b seam) |
| `scripts/entity_nomenklatura.py` | Linux/CI edge (only nomenklatura importer) | load bundle -> `xref(LogicV2)` -> auto-merge log -> drain review band -> apply verdicts -> emit resolved cluster membership |
| `scripts/entity_graph_neo4j.py` | Docker edge (only neo4j-driver importer) | constraints/indexes + MERGE canonical nodes / member aliases / provenance / resolved relationships; rerun-safe |
| `skills/entity-graph/SKILL.md` | orchestration | the operator-run flow + the HITL gate |
| `infra/docker-compose.yml` | infra | Neo4j (13a); localhost-bound; healthcheck; `graph` profile |

Decoupling: the pure cores import NO nomenklatura / neo4j / followthemoney, so
they golden-test on Windows with fakes (mirrors `entity_extract` vs
`entity_models` vs `entity_ftmize`). `entity_nomenklatura` is the ONLY nomenklatura
importer; `entity_graph_neo4j` the ONLY neo4j-driver importer. Both SKIP on Windows.

---

## 4. Key design decisions

### D1. Matching algorithm = LogicV2 (model-free), conservative configurable thresholds
nomenklatura's in-code default is `RegressionV1` (NAME "regression-v1"), a logistic
matcher needing a trained sklearn artifact + `scikit-learn==1.7.2`. `LogicV2`
(NAME "logic-v2") is a model-free heuristic matcher that ships in-package. For a
portable FOSS tool we select **LogicV2** explicitly (`xref(..., algorithm=LogicV2)`)
-- deterministic, no model artifact, no sklearn version brittleness. `RegressionV1`
is a documented FUTURE opt-in, not the baseline. (The Phase-12 xref smoke ran the
DEFAULT and passed in CI, so RegressionV1 is available; we still prefer LogicV2.)

Honest limit: LogicV2's real precision on FOIA person/org names is unverified, and
score calibration is algorithm-specific (a 0.70 on logic-v2 != 0.70 on
regression-v1). So thresholds are CONFIG, logged in run metadata, and default
CONSERVATIVE -- see D8. They are tuned empirically against real bundles, never
baked as truth.

### D2. Stable, content-addressed canonical id (the riskiest thing, per Codex)
Do NOT key the graph on nomenklatura's minted `NK-...` id -- it is mutable resolver
bookkeeping (random `Identifier.make()`, dependent on resolver-DB history; a rebuilt
resolver yields different NK- ids for the same clusters). Instead Magpie owns a
STABLE, content-addressed canonical id:

    canonical_id = sha256("|".join(sorted(member_node_ids)))[:40]

- Members are the Phase-12 sha256 node ids (preserved through the bundle).
- A SINGLETON (no merge) uses the same rule over its one member id -> stable from
  day one; every node has a canonical_id immediately.
- The Neo4j node is keyed (MERGE) on `canonical_id`; member sha256 ids are stored
  as a `members` property (+ optional `:Mention` alias nodes); the nomenklatura
  `NK-` id is stored as `resolver_id` METADATA only.
- Reproducible: the same (bundle + accepted verdicts) always yields the same
  graph, independent of resolver-DB churn. A membership change yields a NEW
  canonical_id -- correct, it is a genuinely different cluster; the snapshot is the
  source of truth and the graph is re-MERGEd from it (orphan canonical nodes absent
  from the current snapshot are pruned).

### D3. The resolved snapshot (the 13a deliverable + the 13a/13b seam)
13a's durable output is NOT "the live resolver DB". It is a portable **resolved
snapshot** (one JSON artifact), mirroring Phase-12's reviewed-intermediate ->
FtM-bundle decoupling. One record per resolved cluster:

    { canonical_id, schema, name (representative), members:[node_id...],
      aliases:[name...], resolver_id (NK- or null), provenance_refs:[...],
      edges:[{schema, head_canonical, tail_canonical, role, provenance_refs}],
      algorithm, thresholds, generated_at }

`entity_resolved_snapshot.py` is a pure serializer over the cluster membership
`entity_nomenklatura` emits, so the snapshot SCHEMA is Windows-golden-testable.
The Neo4j writer (13a) and the yente own-corpus dataset + cross-ref (13b) both
consume the snapshot unchanged -- 13b never reaches back into the resolver DB.

### D4. Graph write = direct neo4j 6.2.0 driver MERGE (idempotent)
`ftm export-neo4j-bulk` needs a stopped/empty DB -> out (we need incremental
upserts). `ftm export-cypher` renders FtM edge-schemas as relationships for free,
but emits whatever it emits this year -> we keep it as a CONTRACT ORACLE / debug
bootstrap, not the production path. Production write = direct driver `session.run`
with explicit `MERGE` on `canonical_id` over our small fixed edge taxonomy (full
control, idempotent, rerun-safe). Uniqueness constraint on `:Entity(canonical_id)`.

### D5. Cross-corpus resolution, per-investigation resolver DB, single writer
Resolve ACROSS the whole investigation corpus (all docs' bundles loaded into one
store, `xref` once) -- not per-doc-then-link; that is what lets a homonym across
two documents surface as one review candidate. The resolver DB is PER-INVESTIGATION
(a sqlite file under a gitignored scratch dir via `NOMENKLATURA_DB_URL`), never
global -- global resolution would contaminate unrelated cases and make
reversibility ugly. The skill is the SINGLE writer: it owns the `begin()/commit()`
boundary around `xref` + candidate drain + verdict apply; no concurrent writers
(SQLite contention).

### D6. The HITL review packet + JSON handback + snapshot discipline
The 70%-to-auto-merge band drains from the LIVE resolver via
`get_candidates()` (NOT from `dump()`, which drops NO_JUDGEMENT edges). Flow:

1. `entity_nomenklatura` drains `get_candidates()` for `score in [floor, auto)`.
2. `entity_review_packet` writes a candidate-SNAPSHOT JSON AND renders a
   self-contained HTML packet (side-by-side cards: entity A vs B, their
   provenance/source snippets, the matched score). Packet metadata carries:
   investigation id, resolver-DB path hash, algorithm, thresholds, generated_at,
   and a candidate-snapshot HASH.
3. The human reviews in-browser, exports a verdict JSON `[{left, right, verdict}]`.
4. `entity_nomenklatura` applies each verdict via `resolver.decide(...)` inside
   `begin()/commit()` -- but RE-CHECKS each pair is still LIVE and unresolved
   first; a stale pair (already moved by another decision / a rerun / an
   auto-merge) is SKIPPED and REPORTED, never blindly applied. The snapshot hash
   detects a packet generated against a different resolver state.

This is the stale-candidate guard Codex flagged: static HTML + downloaded JSON is
robust ONLY with this snapshot discipline. Verdict-JSON validation + the
stale-apply behavior are Windows-golden-testable in `entity_review_packet`.

### D7. Auto-merge logged + reversible (Tim's decision, realized)
`xref(auto_threshold=<auto>)` auto-decides `score > auto` as POSITIVE. Each
auto-merge is recorded to an AUTO-MERGE LOG artifact (pair, score, evidence refs,
algorithm, threshold, timestamp) alongside the snapshot. Reversibility: a logged
auto-merge can be undone by a NEGATIVE `decide` on the pair (the resolver supports
re-deciding); the SKILL surfaces "review the N auto-merges" as an explicit,
optional step. So nothing merges unaccountably.

### D8. 3-bucket thresholds = conservative, configurable, logged
The CLI/xref expose ONLY an upper `auto_threshold` (+ a fixed `min_threshold=0.01`
far below our floor); the 0.70 review FLOOR is enforced in Magpie code by draining
`get_candidates()` for `score in [floor, auto)`. Defaults (CONSERVATIVE, config-
overridable, logged in run metadata): **auto-merge `>= 0.98`** (Codex: start more
defensive than 0.95 absent FOIA evidence), **review band `[0.70, 0.98)`**,
**keep-distinct `< 0.70`**. These are placeholders pending calibration on real
bundles; they are config, not truth.

---

## 5. 13a data flow (operator-run, end to end)

1. Operator has Phase-12 bundles for the corpus' documents + a running Neo4j
   (`docker compose --profile graph up`).
2. `entity_nomenklatura.resolve(bundles, scratch_dir, thresholds, algorithm=LogicV2)`:
   set `NOMENKLATURA_DB_URL` to a scratch sqlite -> `Resolver.make_default()` ->
   `load_entity_file_store(each bundle)` -> `xref(auto_threshold=auto)` ->
   write the auto-merge log -> drain `get_candidates()` in `[floor, auto)`.
3. `entity_review_packet.build(candidates)` -> candidate-snapshot JSON + the HTML
   packet. Operator reviews -> verdict JSON.
4. `entity_nomenklatura.apply_verdicts(verdict_json)` with the stale-pair guard ->
   resolver now holds the human decisions.
5. `entity_resolved_snapshot.build(resolver, store)` -> the portable resolved
   snapshot (D3), deriving each cluster's `canonical_id` (D2).
6. `entity_graph_neo4j.write(snapshot, bolt_uri, auth)` -> constraints + idempotent
   MERGE of canonical nodes / member aliases / provenance / resolved relationships.
7. Output via Librarian: an aggregate findings note (cluster counts, the N
   auto-merges, the M human merges, top connected entities); raw member PII stays
   local; any surfaced PII routes through `redact-output` (the spine seam).

---

## 6. Test & CI strategy (the only real verification surface is CI)

Three tiers (Codex-converged), building on the existing Phase-12 xref smoke:

- **Windows / offline pure-core (golden, no infra):** bucket policy; stable
  `canonical_id` derivation; review-packet render + candidate-snapshot hashing;
  verdict-JSON validation; the STALE-verdict-apply behavior; the resolved-snapshot
  schema/serializer. Fakes stand in for the resolver.
- **Ubuntu `ftm` contract job (extends the existing one):** `load_entity_file_store`
  the real bundle -> `xref(algorithm=LogicV2, auto_threshold=...)` ->
  `get_candidates()` band drain -> `decide()` apply (+ stale re-check) ->
  `entity_resolved_snapshot` generation. (The Phase-12 candidate smoke already
  proves load->xref->get_candidates; 13a adds LogicV2 + decide + snapshot.)
- **Ubuntu Neo4j service-container job (NEW):** spin a `neo4j:...-community`
  service container -> create constraints/indexes -> MERGE the resolved snapshot
  TWICE -> assert idempotent node/relationship counts (rerun-safe). Plus a
  `docker compose config` validation of `infra/docker-compose.yml`.

For 13a we do NOT run full compose in CI (a Neo4j service container suffices to
prove the writer). RULE (Phase-12 lesson): gate the merge on these CI jobs, NEVER
on Windows-green + Codex-green alone.

---

## 7. Positioning & onboarding (Layer-2, operator-tier, Docker-gated)

entity-graph is operator-tier and Docker-gated. The journalist onramp
(`JOURNALIST_START.md`, the `doctor` skill) stays Docker-free and unchanged.
`setup`/`doctor` grow a Layer-2 capability probe -- `detect_tier` gains a Docker /
compose check (a metadata/`shutil.which` + `docker version` rc probe, READ-ONLY in
`doctor`) reporting "entity-graph (Layer 2)" as READY/UNAVAILABLE. `setup` (operator)
INSTRUCTS the operator to install Docker + run the WSL2 `vm.max_map_count` step
(13b) -- never auto-installs Docker. No Docker token enters any journalist surface.

---

## 8. Honest limits & risks

- **Resolution precision unverified on FOIA names.** Zero-shot/heuristic resolution
  is not ground truth -> conservative auto-merge (>=0.98), MANDATORY human review of
  the band, every auto-merge logged + reversible. The differentiator IS the human
  gate, not the matcher.
- **Neo4j Community is GPLv3.** Bolt-over-TCP across a process boundary = no
  copyleft reach for local use (community consensus, not an official ruling). Ship
  the compose file + docs, NEVER the Neo4j image; the operator pulls it.
- **Canonical-id churn on membership change** is by-design (a changed cluster is a
  new entity); the snapshot is the source of truth and the graph is re-derived from
  it, with orphan pruning -- documented, not hidden.
- **Resolver SQLite is single-writer**; the skill serializes access. A crashed run
  leaves a scratch DB the operator can discard (per-investigation, gitignored).
- **CI cost:** a Neo4j service container per run is the price of honest
  verification; kept minimal (no full compose in 13a CI).

---

## 9. 13b outline (cross-ref + MCP -- detailed in its own design when 13a ships)

- **Stack:** yente 5.4.0 (image `ghcr.io/opensanctions/yente:5.4.0`, MIT, LAST ES8
  release, supports OpenSearch 2.x) + OpenSearch 2.19.5 (Apache-2.0, final 2.x) +
  `mcp` SDK 1.27.2 (FastMCP) for `yente-mcp`. Extends `infra/docker-compose.yml`
  with `index` + `yente` (default profile; Neo4j stays the `graph` profile).
- **Own-corpus dataset:** emit the resolved snapshot as a yente entities file ->
  mount -> declare in a yente manifest. **Namespacing owner = the yente manifest
  `namespace: true`** (NOT `ftm sign -s`, which mutates ids and breaks the join back
  to Neo4j/member ids). The FREE civic catalog (`data.opensanctions.org/.../default/
  catalog.json`, no token); watchlist datasets OPT-IN (`datasets:[]` default).
- **Cross-ref topology:** SEPARATE `POST /match/{scope}` per scope (own-corpus vs
  watchlist), group hits by `result.datasets` (combined scope muddies
  thresholding/semantics).
- **`yente-mcp`:** thin read-only httpx MCP (`search` / `match` / `get_entity` /
  `list_datasets` / `cross_reference`); hard request timeouts + result caps + a
  fixed base-URL env; NO pass-through query, NO write/reindex tool; treat the server
  as untrusted (design 7).
- **OpenSearch risk:** yente is vendor-tested against ES 8.19.13; self-hosted
  OpenSearch 2.19.5 is the less-trodden path -> 13b MUST include a real smoke on the
  EXACT images shipped (do not trust "vendor says ES8 works"). Bake the WSL2
  `vm.max_map_count=262144` persistence step into setup/doctor (the reboot trap).
- **Watchlist data is CC-BY-NC** -> opt-in + documented in setup/doctor + release
  notes (the GLiREL-weights posture).

---

## 10. Out of scope / deferred

- 13b (this PR ships 13a only).
- The `RegressionV1` matcher (future opt-in; needs the sklearn model + pin).
- Address/identifier dedup via `rigour.addresses`/`rigour.ids` (13a does NAME
  resolution; confirm symbols if address dedup is later wanted).
- Any Neo4j browser/visualization UI (the graph is queryable via Bolt; viz is
  out of scope).
- Cross-investigation / global resolution (per-investigation only).

---

## 11. Open items -> implementation plan (13a)

- Exact module APIs + dataclasses (`Candidate`, `Verdict`, `ResolvedCluster`,
  `ResolutionConfig`); the canonical_id helper signature.
- The HTML packet template (to be MOCKED UP for Tim's sign-off before building).
- The Neo4j schema (labels, constraints, the member/provenance/edge Cypher).
- The exact `ftm`-contract + Neo4j-service CI job YAML.
- The `entity-graph` SKILL.md operator flow + the Librarian aggregate output.
- `detect_tier` Layer-2 Docker probe + the setup/doctor wording.
- requirements-ftm.txt additions (nomenklatura already present; add the neo4j
  driver -- confirm whether it belongs in requirements-ftm or a new
  requirements-graph) + the Neo4j image pin (by digest).

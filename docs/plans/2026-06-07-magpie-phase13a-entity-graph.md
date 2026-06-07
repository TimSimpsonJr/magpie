# Magpie Phase 13a -- entity-graph (resolution + Neo4j) -- Implementation Plan

Date: 2026-06-07. The HOW for Phase 13a. Builds on the APPROVED design
(`docs/plans/2026-06-07-magpie-phase13-entity-graph-design.md`, Codex
BLOCK->PROCEED->APPROVE). TDD + SDD. ASCII-only (SDD subagents read this).

Branch: `feat/phase13a-entity-graph` (already created; the design is committed on
it). 13b (yente cross-ref + yente-mcp) is a SEPARATE later PR.

---

## 0. Deps, pins, markers (Task 0 -- scaffolding, do FIRST)

- **`requirements-graph.txt` (NEW):** `neo4j==6.2.0` (the Python driver; Apache-2.0;
  pure-Python, Windows-INSTALLABLE). Layer-2 graph deps live here -- NOT in the
  Layer 0-1 `requirements-dev.txt` (Track-A users do not get it) and NOT in
  `requirements-ftm.txt` (that file is Linux-only via PyICU; the neo4j driver is
  cross-platform). So `entity_graph_neo4j` IMPORTS on Windows; only its CONNECT
  tests are gated.
- **`requirements-ftm.txt`:** unchanged -- `nomenklatura==4.9.1` is already present
  and pulls followthemoney + rigour + scikit-learn. `entity_nomenklatura` imports
  nomenklatura -> SKIPS on Windows (PyICU), runs in the CI `ftm` job.
- **Neo4j image:** `neo4j:5.26.26-community` (GPLv3; the LTS line). Pin the tag in
  compose; record the digest in a comment. Magpie ships compose + docs, NEVER the
  image (the user pulls it).
- **`pyproject.toml` markers (add):** `neo4j` (Neo4j-service-container-gated graph
  tests) and `compose` (the docker-compose-up smoke). The offline subset already
  excludes unknown markers via the explicit `-m "not ..."` list -> ADD
  `and not neo4j and not compose` to the offline command + the CI offline job.
- **Module skip guards:** `entity_nomenklatura` guards with
  `importlib.util.find_spec("nomenklatura")` (like `entity_ftmize`);
  `entity_graph_neo4j` guards its TESTS with the `neo4j` marker + a live-DB env
  check, but the module imports the driver at top (driver present via
  requirements-graph; only a live Neo4j is gated).

Pure-core modules (Tasks 1-3) import NONE of the above -> Windows-golden-testable.

---

## 1. Task list (TDD; one SDD implementer per task unless noted)

### Task 1 -- `scripts/entity_resolution_policy.py` (PURE core)
The deterministic policy + id layer. Stdlib only (hashlib/dataclasses).
- `@dataclass(frozen=True) ResolutionConfig`: `algorithm="logic-v2"`,
  `auto_threshold=0.98`, `review_floor=0.70` (all overridable; logged in metadata).
- `canonical_id(member_ids: list[str]) -> str` =
  `sha256("|".join(sorted(set(member_ids)))).hexdigest()[:40]`. Order-independent,
  dedup-safe; a singleton hashes its one member. (Reuses the Phase-12 `stable_id`
  convention; may import `scripts.entity_extract.stable_id` -- that module is pure.)
- `edge_id(schema, head_canonical, tail_canonical, role) -> str` =
  `sha256("|".join([schema, head_canonical, tail_canonical, role or ""]))[:40]`.
- `bucket(score: float, config) -> "auto" | "review" | "distinct"`:
  `>= auto_threshold` -> auto; `>= review_floor` -> review; else distinct.
- `@dataclass Candidate` (left_id, right_id, score, plus the two entities'
  display fields + member/provenance refs for the packet).
- `@dataclass(frozen=True) Verdict` (left_id, right_id, verdict in
  {"merge","distinct","unsure"}); `VALID_VERDICTS` set.
- TESTS (golden, Windows): canonical_id determinism + order-independence + dedup +
  singleton; edge_id stability + role-None handling; bucket boundaries EXACTLY at
  0.70 and 0.98 (inclusive/exclusive pinned); Verdict validation rejects junk.

### Task 2 -- `scripts/entity_resolved_snapshot.py` (PURE core)
The portable resolved-snapshot schema + serializer (the 13a/13b seam, design D3).
- `@dataclass ResolvedEntity` (canonical_id, schema, caption, aliases, member_ids,
  properties: dict[str,list[str]], resolver_id, provenance_refs).
- `@dataclass ResolvedEdge` (edge_id, schema, head_canonical, tail_canonical, role,
  properties, provenance_refs).
- `build_snapshot(entities, edges, provenance, *, investigation_id, algorithm,
  thresholds, generated_at, snapshot_version="1.0") -> dict` -> the top-level
  `{metadata, entities[], edges[], provenance[]}` (design D3 exactly).
- `assert_snapshot_consumable(snapshot) -> None` -- the 13a/13b contract check
  (pure; the analogue of `entity_ftmize.assert_phase13_consumable`): top-level
  keys present; every edge endpoint references a known canonical_id; metadata has
  investigation_id; provenance_refs resolve.
- TESTS (golden, Windows): the schema shape; top-level entities/edges (NOT nested);
  edge endpoints validate against entity ids; `generated_at` is INJECTED (no clock);
  `assert_snapshot_consumable` passes a good snapshot + raises on a dangling edge.

### Task 3 -- `scripts/entity_review_packet.py` (PURE core)
The HITL packet generator + verdict handback (design D6). Stdlib + html.escape.
- `SnippetResolver = Callable[[str, int, int, int], str]`  # (doc_id, page,
  char_start, char_end) -> source text. INJECTED (Windows-testable with a fake).
- `build_candidate_snapshot(candidates, *, investigation_id, algorithm, thresholds,
  resolver_db_hash, generated_at) -> tuple[dict, str]` -> (candidate_snapshot,
  packet_hash). `packet_hash = sha256(canonical_json(candidate_snapshot))`.
- `render_html(candidate_snapshot, snippet_resolver) -> str` -> the self-contained
  HTML packet (NO external CSS/JS/fonts; matches the signed-off mockup
  `docs/plans/2026-06-07-phase13a-review-packet-mockup.html`). Hydrates each card's
  snippets via `snippet_resolver`; ALL user text `html.escape`d. Embeds
  `packet_hash` + `investigation_id` into the exported-verdict JS.
- `parse_verdicts(verdict_json_text) -> tuple[str, list[Verdict]]` -> (packet_hash,
  verdicts); raises on malformed JSON / bad verdict value / missing packet_hash.
- TESTS (golden, Windows, fake snippet_resolver): packet_hash deterministic +
  stable under key order; render_html is self-contained (assert no `http`/`src=`
  external refs; contains each pair + the hydrated snippet + escapes an injected
  `<script>` in a name); parse_verdicts round-trips + rejects junk + extracts the
  hash. NOTE: the exact HTML/CSS is the MOCKUP, pending Tim's sign-off -- match it.

### Task 4 -- `scripts/entity_nomenklatura.py` (LINUX/CI edge; only nomenklatura importer)
Runs resolution against the real resolver. `ftm`-marked tests; SKIPS on Windows.
- `resolve(bundle_paths, scratch_dir, config) -> ResolveResult(candidate_snapshot,
  packet_hash, auto_merge_log)`: set `NOMENKLATURA_DB_URL` to
  `sqlite:///<scratch>/resolver.db`; `Resolver.make_default()`; `begin()`;
  `load_entity_file_store(each bundle, resolver)`; `xref(resolver, store, index_dir,
  algorithm=LogicV2, auto_threshold=config.auto_threshold)`; record the POSITIVE
  auto-merges (pair, score, evidence) to `auto_merge_log`; drain
  `get_candidates()` for `score in [review_floor, auto_threshold)` -> Candidates
  (hydrate display fields + provenance from the store); `commit()`. Build the
  candidate snapshot (Task 3) for the review packet.
- `apply_verdicts(verdict_json, scratch_dir, config) -> ApplyResult(applied,
  skipped, aborted_reason)`: FAIL-CLOSED (design D6) -- recompute the LIVE
  candidate-snapshot hash; if `!=` the verdict file's packet_hash, RETURN
  aborted("regenerate the packet"), apply nothing. Else `begin()`; per pair
  re-check it is still a live NO_JUDGEMENT candidate at the packet's score; if so
  `resolver.decide(left, right, Judgement.POSITIVE|NEGATIVE)` (unsure -> skip);
  else record skipped; `commit()`.
- `build_resolved_snapshot(scratch_dir, investigation_id, store) -> dict`: for every
  member id, `resolver.get_canonical(id)` -> group members by canonical cluster;
  per cluster derive `canonical_id` (Task 1) from the member ids, collect
  caption/aliases/properties (from the store proxies) + the `resolver_id` (the NK-
  canonical) + provenance; map edges' endpoints member->canonical; call
  `entity_resolved_snapshot.build_snapshot`.
- TESTS (`ftm`-marked, CI; EXTENDS `test_entity_ftmize.test_nomenklatura_xref_candidate_smoke`):
  load the `reviewed_intermediate_sample` bundle -> `resolve(LogicV2)` -> the two
  same-name Persons surface in the review band; apply a `merge` verdict ->
  `build_resolved_snapshot` -> ONE cluster, 2 members, a derived canonical_id, a
  resolver_id; the FAIL-CLOSED abort on a wrong packet_hash; the auto_merge_log
  shape. (Reuse the Phase-12 fixture; add a 2nd fixture with a clear-merge pair if
  the sample's threshold lands outside the band under logic-v2.)

### Task 5 -- `scripts/entity_graph_neo4j.py` (DOCKER edge; only neo4j importer)
Writes the resolved snapshot to Neo4j. `neo4j`-marked tests (service container).
- `ensure_schema(driver) -> None`: composite NODE-KEY constraint
  `FOR (e:Entity) REQUIRE (e.investigation_id, e.canonical_id) IS NODE KEY`;
  supporting indexes.
- `write(driver, snapshot) -> WriteStats`: investigation-SCOPED REPLACE (design D4)
  in one managed transaction: (a) MERGE each `:Entity {investigation_id,
  canonical_id}` SET props/member_ids/aliases/resolver_id; MERGE each relationship
  keyed `(investigation_id, edge_id)` between its canonical endpoints; (b) DELETE
  `:Entity {investigation_id: $inv}` (and its rels) whose canonical_id is NOT in
  the snapshot's id set, and in-scope rels whose edge_id is NOT in the snapshot.
  Idempotent (re-running the same snapshot is a no-op net of MERGE).
- TESTS (`neo4j`-marked, service container; CANNED snapshot fixtures -- NO
  nomenklatura needed): ensure_schema; write a snapshot TWICE -> identical
  node/rel counts (idempotent); write a snapshot with one cluster + its edge
  REMOVED -> only that in-scope orphan deleted; write a SECOND investigation_id ->
  the first's subgraph is untouched (scoped isolation, the D4 CRITICAL fix).

### Task 6 -- `infra/docker-compose.yml` + `infra/.env.example`
- `neo4j` service (profile `graph`): `neo4j:5.26.26-community`, `NEO4J_AUTH`,
  heap/pagecache 1G each, ports bound `127.0.0.1:7474`/`7687`, a healthcheck,
  a named volume. (13b will add `index` + `yente`.)
- `.env.example`: `NEO4J_PASSWORD=<random>` (gitignore `.env`; commit `.env.example`).
- TESTS: a `compose`-marked smoke (Task 9) -- `docker compose config` parses AND
  `docker compose --profile graph up -d` + a Bolt connect succeeds.

### Task 7 -- `skills/entity-graph/SKILL.md` (orchestration) + `references/prior-art.md`
Operator flow: confirm Docker/Neo4j up (point to `doctor`) -> `resolve` the corpus
bundles -> generate + open the HTML review packet -> apply the exported verdicts
(FAIL-CLOSED) -> `build_resolved_snapshot` -> `entity_graph_neo4j.write` -> a
Librarian AGGREGATE findings note (cluster counts, N auto-merged [logged/reversible],
M human-decided, top-degree entities; raw member PII stays local; surfaced PII via
`redact-output`). Documents: the mandatory human gate (never autonomous), the
per-investigation scratch resolver DB, the Docker/operator-tier positioning, the
Neo4j GPLv3 (ship-compose-not-image) + the watchlist-data-is-13b notes. `prior-art.md`
= the distilled research gates A/B + the verified nomenklatura/neo4j API. TEST: a
PyYAML frontmatter+body smoke (mirrors the other skill smokes).

### Task 8 -- `scripts/detect_tier.py` Layer-2 probe + setup/doctor
- ADD a capability "build an entity graph (Layer 2)" gated on a Docker probe:
  `check_binary(["docker"])` + a bounded read-only `docker version`/`compose
  version` rc probe (NEVER `docker run`/pulls/starts -- doctor stays side-effect-free).
  READY when Docker+compose present; UNAVAILABLE otherwise with a setup-pointer fix.
- `setup` SKILL.md: INSTRUCT (never auto-install) Docker Desktop + the WSL2
  `vm.max_map_count=262144` persistence step (for 13b OpenSearch; harmless now).
- `doctor` stays strictly READ-ONLY; the new probe is metadata/which/rc only.
- TESTS: golden (inject Docker-present -> Layer-2 READY; absent -> UNAVAILABLE +
  setup-pointer, with the core Track-A capabilities UNCHANGED); the no-docker
  guard test on JOURNALIST_START stays green (the journalist onramp is untouched).

### Task 9 -- CI (`.github/workflows/ci.yml`)
- `offline` job: extend the marker exclusion to `... and not neo4j and not compose`.
- `ftm` job: unchanged install (requirements-offline + requirements-ftm); it now
  also runs the Task-4 `ftm`-marked resolution-contract tests (same `-m ftm`).
- `graph` job (NEW): a `neo4j:5.26.26-community` SERVICE container + install
  requirements-graph; run `-m neo4j` (Task-5 writer tests against the live DB).
- `compose` job (NEW): `docker compose config` + `docker compose --profile graph
  up -d` + a Bolt connect smoke (`-m compose`), then `down`.
- RULE (Phase-12 lesson, restated): gate the PR merge on `ftm` + `graph` + `compose`,
  never on Windows-green + Codex-green alone.

---

## 2. Test/CI tier mapping (design section 6)

| Tier | Runs | Covers |
|---|---|---|
| Windows / offline pure-core | everywhere (the 675+ suite) | Tasks 1-3 + 8 (policy, snapshot schema, packet render/validate/stale-logic with fakes, detect_tier probe) |
| Ubuntu `ftm` job | CI | Task 4 (load->xref LogicV2->drain->decide->resolved snapshot; fail-closed) |
| Ubuntu `graph` (Neo4j service) | CI | Task 5 (scoped-replace MERGE/DELETE, idempotence, scoped isolation) |
| Ubuntu `compose` smoke | CI | Task 6 (the shipped graph-profile wiring) |

The stale-pair guard's POLICY (hash mismatch -> abort; per-pair re-check) is
Windows-golden-tested in Task 3 with a fake; its INTEGRATION (against the live
resolver) is in Task 4.

---

## 3. SDD dispatch notes (bake into EVERY implementer/fix-subagent prompt)

- "You are ALREADY on `feat/phase13a-entity-graph`; commit directly to it; do NOT
  create or switch branches."
- ASCII-only output; read ONLY the ASCII design + this plan + the files you create.
  Do NOT open other repo files (most are non-ASCII -> the Read tool content-filter-
  blocks). House style + the exact APIs are inline above.
- Sequential commits (no parallel committers on one branch).
- Run the offline suite via `& .venv\Scripts\python.exe -m pytest -m "not docling
  and not spacy and not xray and not tsa and not gliner and not ftm and not neo4j
  and not compose" -q` (use `-m`, never `-k`).
- Pure-core tasks (1-3, 8) are golden-tested with fakes and must import no
  nomenklatura/neo4j. Verify import purity (subprocess test, like entity_models).

---

## 4. Sequencing

Task 0 (deps/markers) -> Tasks 1,2,3 (pure cores, parallel-safe -- distinct files)
-> Task 4 (nomenklatura, depends on 1+2) -> Task 5 (neo4j, depends on 2; canned
fixtures) -> Task 6 (compose) -> Task 7 (skill, depends on the API surface) ->
Task 8 (detect_tier) -> Task 9 (CI, wires the markers). Then: impl-review (Codex)
-> confirmatory -> PR -> merge (gated on the `ftm` + `graph` + `compose` CI jobs).

DEPENDENCY ON TIM: Task 3's HTML template must match the review-packet mockup
Tim signs off on (`docs/plans/2026-06-07-phase13a-review-packet-mockup.html`).
The rest of the plan is independent of that sign-off; build Task 3's logic
(hash/validate/snippet-injection) against the mockup, fold any visual changes when
Tim responds.

---

## 5. Open items to confirm at plan-review

- Does the `reviewed_intermediate_sample` pair land in the [0.70, 0.98) band under
  LogicV2, or do we need a 2nd fixture tuned to the band? (Empirical -- the CI ftm
  job answers it; Task 4 may add a fixture.)
- Neo4j composite NODE KEY needs Enterprise in some versions -- CONFIRM Community
  5.26 supports a composite node-key constraint; if not, fall back to a uniqueness
  constraint on a synthesized `scoped_id = investigation_id + ":" + canonical_id`
  property (MERGE on `scoped_id`). (Verify at impl time / in the `graph` CI job.)
- The auto_merge_log persistence format + where the skill surfaces "review the N
  auto-merges" (a second, optional packet?).

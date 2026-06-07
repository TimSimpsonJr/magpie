# entity-graph -- prior art and verified facts

The phase's verified-facts gate for Phase 13a (resolution + Neo4j). Every fact
below is PRIMARY-SOURCE-VERIFIED as of 2026-06-07 (the two research gates
phase13-gate-A-resolution-graph and phase13-gate-B-crossref-infra). This is the
distilled record the SKILL.md flow relies on; keep it ASCII.

## Versions and pins

- `nomenklatura==4.9.1` (MIT) -- the resolver + xref engine. Linux/CI-only: it
  pulls followthemoney, which needs PyICU/ICU, which has no Windows wheel. So
  `scripts/entity_nomenklatura.py` does not import on the Windows dev venv (by
  design) and runs only on the operator's Linux/Docker host or in CI.
- `followthemoney==4.9.0` (MIT) -- the FtM entity model (Person/Company/edges).
  PyICU dependency -> Linux/CI-only (same constraint as above).
- `rigour==2.1.2` (MIT) -- the name/normalization primitives nomenklatura uses.
  Linux/CI-only (PyICU).
- `neo4j==6.2.0` (Apache-2.0 + Python-2.0) -- the Neo4j Python DRIVER. Pure
  Python, CROSS-PLATFORM (installs on Windows). It lives in
  `requirements-graph.txt`, kept out of `requirements-dev.txt` (Track-A users do
  not get it) and out of `requirements-ftm.txt` (that file is Linux-only via
  PyICU; the driver is not). So `scripts/entity_graph_neo4j.py` IMPORTS on
  Windows; only its CONNECT tests are gated.
- Neo4j Community SERVER image `neo4j:5.26.26-community` (GPLv3; the LTS line).
  Magpie ships the compose file + docs, NEVER the image -- the operator pulls it.
  Digest verified 2026-06-07:
  `sha256:0b5d3ab6ec1b866890dbfb53bf4fe1cf039f9e03c96165599a403005b7e7bcc3`.

## Resolution (nomenklatura)

- The flow loads the Phase-12 FtM bundles into an entity store, then runs
  nomenklatura `xref` over it. xref auto-decides the top bucket and records
  NO_JUDGEMENT candidates for the rest.
- ALGORITHM = `LogicV2` (NAME `"logic-v2"`), a MODEL-FREE heuristic matcher. It is
  chosen DELIBERATELY over nomenklatura's in-code default `RegressionV1` (NAME
  `"regression-v1"`), a logistic matcher that needs a TRAINED sklearn artifact +
  `scikit-learn==1.7.2`. LogicV2 ships in-package: portable, deterministic, no
  model file, no sklearn version brittleness. RegressionV1 is a documented FUTURE
  opt-in, not the baseline.
- SCORES are in [0, 1]. Calibration is algorithm-specific: a 0.70 on logic-v2 is
  NOT a 0.70 on regression-v1. So thresholds are CONFIG (logged in run metadata),
  not truth.
- The library exposes ONLY an upper `auto_threshold` (plus a fixed
  `min_threshold=0.01` far below our floor). The 0.70 REVIEW FLOOR is enforced in
  MAGPIE CODE by draining `get_candidates()` for `score in [review_floor,
  auto_threshold)`. Defaults: auto-merge `>= 0.98`, review band `[0.70, 0.98)`,
  keep-distinct `< 0.70` (conservative; config-overridable).
- The resolver STORE is an on-disk SQLite DB selected via `NOMENKLATURA_DB_URL`,
  PER-INVESTIGATION (a scratch file), never global.
- A POSITIVE `decide()` MINTS a new `NK-` canonical id ON TOP OF the preserved
  Phase-12 member ids. That `NK-` id is MUTABLE resolver bookkeeping (random
  `Identifier.make()`, dependent on resolver-DB history). So Magpie does NOT key
  the graph on it -- it derives its own stable `canonical_id =
  sha256("|".join(sorted(member_ids)))[:40]` and stores `NK-` only as
  `resolver_id` metadata. Downstream must map member -> `get_canonical()` to find
  a node's cluster.

## Graph write (Neo4j)

- Write resolved canonical nodes/edges via the `neo4j==6.2.0` driver with MERGE on
  a synthesized `scoped_id = investigation_id + ":" + canonical_id`, guarded by a
  SINGLE-PROPERTY uniqueness constraint (`FOR (e:Entity) REQUIRE e.scoped_id IS
  UNIQUE`). Single-property uniqueness is Neo4j-COMMUNITY-safe. A composite
  `(investigation_id, canonical_id)` NODE KEY would give the same scoped identity
  but is ENTERPRISE-only, so it is deliberately NOT used.
- Relationships key on `edge_scoped_id = investigation_id + ":" + edge_id`.
  Community cannot enforce a relationship-uniqueness constraint, so relationship
  idempotence comes from MERGE on `edge_scoped_id`, not a constraint.
- `ftm export-neo4j-bulk` needs a STOPPED / EMPTY DB -> OUT (we need incremental
  upserts into a running DB). `ftm export-cypher` is kept as a contract
  oracle / bootstrap-debug aid only. The DIRECT-DRIVER MERGE is the PRODUCTION
  path.

## Honest limits

- Resolution PRECISION on FOIA names is UNVERIFIED. Mitigation: conservative
  auto-merge (>=0.98) + a MANDATORY human review of the band + every auto-merge
  LOGGED and REVERSIBLE. The differentiator is the human gate, not the matcher.
- Neo4j Community is GPLv3. Bolt-over-TCP across a process boundary = no copyleft
  reach for local use is the COMMUNITY READING, not an official ruling. So ship
  the compose + docs, NEVER the image.
- CANONICAL-ID CHURN on membership change is BY-DESIGN: a changed cluster is a
  genuinely different entity, so it gets a new canonical_id. The snapshot is the
  source of truth; the graph is re-derived via the investigation-scoped REPLACE.
- The resolver SQLite is SINGLE-WRITER; the skill serializes access. A crashed run
  leaves a per-investigation, gitignored scratch DB the operator can DISCARD.

## Verification surface (the Phase-12 lesson)

CI is the ONLY real verification surface for the FtM + graph code -- these modules
do not import on the Windows dev venv. The PR merge is gated on:

- the `ftm` job (Ubuntu): the nomenklatura resolution-contract tests
  (load -> xref LogicV2 -> drain band -> decide -> resolved snapshot; fail-closed).
- the `graph` job (Ubuntu): a `neo4j:5.26.26-community` SERVICE container running
  the writer tests (scoped-replace MERGE/DELETE, idempotence, scoped isolation).
- the `compose` job (Ubuntu): `docker compose config` + bringing the `graph`
  profile UP + a Bolt connect smoke.

RULE: never gate the merge on Windows-green + Codex-green alone.

## Out of scope here (13b -- a separate later PR)

Watchlist / sanctions / PEP cross-reference and own-corpus cross-ref via yente +
OpenSearch + the `yente-mcp` server are 13b. 13a emits the portable resolved
snapshot as the seam; 13b consumes it unchanged.

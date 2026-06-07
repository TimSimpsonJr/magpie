# Magpie v0.2.0 -- Release Notes

Magpie v0.2.0 adds **Track B (the entity-network track)** on top of the Layer 0-1
analysis suite. Track B takes ingested documents through entity + relation
extraction, resolves which entities across documents are the same real-world
person/org behind a mandatory human gate, writes the resolved network to a Neo4j
graph, and cross-references the resolved entities against the investigator's own
corpus (and, opt-in, sanctions/PEP watchlists).

Track B is **Layer 2: operator-tier and Docker-gated**. The Layer 0-1 surface
(everything in v0.1.0) is unchanged and still runs laptop-local with no Docker;
the journalist onramp (`JOURNALIST_START`, the `doctor` Track-A capabilities)
never mentions Docker.

## What is new in v0.2.0

### 3 Track-B skills

- `entity-extract` -- GLiNER entities + GLiREL relations over an ingested
  document -> a reviewed, FtM-shaped intermediate after a mandatory human gate.
- `entity-graph` -- resolve entities across the corpus (nomenklatura LogicV2) ->
  a HITL review packet (you decide the uncertain pairs) -> a portable resolved
  snapshot -> an investigation-scoped Neo4j graph.
- `entity-crossref` -- emit the resolved snapshot as a private yente dataset and
  cross-reference it against your own corpus (the FOSS default, zero external
  data) and, opt-in, sanctions/PEP watchlists; plus a thin read-only `yente-mcp`
  server.

### The Track-B engine surface

New engine modules (same pure-core / lazy-edge pattern as Layer 0-1): the pure
cores `entity_taxonomy`, `entity_extract`, `entity_resolution_policy`,
`entity_resolved_snapshot`, `entity_review_packet`, `entity_yente_dataset`,
`entity_crossref`; and the gated edges `entity_models` (GLiNER/GLiREL),
`entity_ftmize` + `entity_nomenklatura` (followthemoney/nomenklatura, Linux/CI
only), `entity_graph_neo4j` (the Neo4j driver), `entity_yente_client` (httpx),
and `yente_mcp_server` (mcp/FastMCP). Heavy and platform-specific dependencies
are imported lazily, so the offline path stays light and Windows-importable.

### Infrastructure

`infra/docker-compose.yml` ships two profiles: `graph` (Neo4j) and `crossref`
(OpenSearch + yente). All ports are bound to `127.0.0.1`. Server images
(`neo4j:5.26.26-community`, `opensearchproject/opensearch:2.19.5`,
`ghcr.io/opensanctions/yente:5.4.0`) ship as a compose file + docs and are pulled
by the operator -- never bundled. `detect_tier` gains two Layer-2 capabilities
("build an entity graph" / "cross-reference entities") off a read-only Docker
probe; `setup`/`doctor` only probe for Docker, never auto-install it.

### Dependencies

Track-B deps are isolated from the Layer 0-1 stack: `gliner` + `glirel` +
`loguru` with `transformers` + `typer` pinned (`requirements-dev`); the FtM stack
(`followthemoney` + `nomenklatura`) is Linux/CI-only (`requirements-ftm.txt`, no
Windows PyICU wheel); the Neo4j driver is `requirements-graph.txt`; the cross-ref
client/server (`httpx` + `mcp`) is `requirements-crossref.txt`. All cross-platform
deps are lazy-imported, so installing them is optional unless you run Track B.

## Posture

- **Mandatory human gates.** Extraction, resolution, and cross-ref surface
  candidates and matches as LEADS for a human, never as verdicts. The resolution
  review packet is fail-closed; auto-merges are logged and reversible.
- **Watchlists are OPT-IN.** Own-corpus cross-ref pulls zero external data and is
  the FOSS default. The watchlist catalog (OpenSanctions `default`) is CC-BY-NC
  (non-commercial); enabling it is a deliberate, documented choice. The GLiREL
  weights are CC-BY-NC-SA (adopted + documented). Magpie's code stays MIT.
- **Determinism.** The resolved snapshot is content-addressed and reproducible;
  the yente own-corpus index runs with a content-hash dataset version + an
  explicit reindex. Own-corpus cross-ref results are reproducible; watchlist
  results are best-effort externally-versioned (the report records `/catalog`
  metadata).

## Validation

- CI gates the merge on the offline subset PLUS the Track-B jobs: `ftm` (the
  followthemoney/nomenklatura contract on Ubuntu), `graph` (a live Neo4j service
  container), `compose` (the graph-profile bring-up), and `crossref` (a live
  OpenSearch + yente stack: real `/match` attribution + a yente-mcp smoke). These
  jobs are the only real verification surface for the Linux/Docker edges, which
  are invisible on the Windows dev box -- the merge is never gated on
  Windows-green alone.

## Deferred (NOT in v0.2.0)

- The accumulating multi-investigation own-corpus index (v0.2.0 is
  per-investigation; the emitter/manifest are multi-snapshot-ready).
- Indexing snapshot edges into yente; the RegressionV1 matcher; address/identifier
  dedup; a Neo4j visualization UI.
- The `foia-exemptions` skill and Layer 3 (opt-in capstone) remain future work.
- The public sample corpus is still a fast-follow (machinery ships; no data
  artifact bundled yet) -- unchanged from v0.1.0.

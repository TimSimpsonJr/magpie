---
name: entity-graph
description: Resolve entities across an investigation's documents into a Neo4j graph after a mandatory human review gate. Layer-2, operator-tier, Docker-gated -- run it to deduplicate people/orgs across the corpus, decide the "maybe" matches yourself, then write the resolved network to Neo4j for queryable analysis.
---

# entity-graph

Take the per-document reviewed FtM bundles Phase 12 produced, resolve which
entities ACROSS documents are the same real-world person or organization, let a
human decide the uncertain matches, and write the resolved network to a Neo4j
graph scoped to one investigation. This is the FIRST heavy-infra (Docker) phase.

This is a Layer-2, OPERATOR-tier, Docker-gated workflow. The journalist onramp
(JOURNALIST_START, the `doctor` skill's Track-A capabilities) stays Docker-free
and is NOT touched by this skill. Do not pull this flow into any journalist
surface.

The differentiator is the MANDATORY HUMAN REVIEW GATE, not the matcher. The
matcher (nomenklatura LogicV2, a model-free heuristic) is imperfect on FOIA
names; what makes the resolved graph trustworthy is that a person actively
decides every uncertain pair and every auto-merge is logged and reversible. NEVER
run this flow autonomously past the review gate.

13a (this skill) does resolution + the Neo4j write. Watchlist / sanctions / PEP
cross-reference and own-corpus cross-ref (yente + OpenSearch + the `yente-mcp`
server) are 13b -- a SEPARATE, later PR. Do not attempt cross-ref here.

---

## What you need before you start

- Phase-12 FtM bundles for the corpus documents (the `<name>.entities.ftm.json`
  + sibling `<name>.provenance.jsonl` + `<name>.manifest.json` triples that
  `entity_ftmize.write_bundle` produced). Resolution runs ACROSS the whole
  investigation corpus at once -- pass ALL the bundles together, not one per
  document. That cross-corpus pass is what lets a homonym in two different
  documents surface as one review candidate.
- The Phase-6 source page text for the same documents, reachable through a
  `snippet_resolver` callable (see STEP 2) -- the review packet hydrates each
  candidate's source snippet from it at render time.
- Docker + Neo4j running (the PRECONDITION below).
- A per-investigation scratch directory (gitignored). The resolver SQLite DB, the
  candidate snapshot, the auto-merge log, and `run.json` all live here. It is
  per-investigation on purpose (global resolution would contaminate unrelated
  cases). If a run crashes mid-way, just DISCARD the scratch DB and re-run -- it
  is disposable.

The code surface:

- `scripts/entity_nomenklatura.py` -- the Linux/CI resolution edge (the only
  nomenklatura importer; does NOT import on the Windows dev venv -- PyICU has no
  Windows wheel -- so it runs on the operator's Linux/Docker host or in CI).
- `scripts/entity_review_packet.py` -- the pure-core HTML review-packet generator
  + verdict parser (Windows-safe).
- `scripts/entity_resolved_snapshot.py` -- the pure-core portable snapshot schema
  (the 13a/13b seam; Windows-safe).
- `scripts/entity_resolution_policy.py` -- the pure-core policy + id layer
  (`ResolutionConfig`, thresholds, `canonical_id`; Windows-safe).
- `scripts/entity_graph_neo4j.py` -- the Docker-tier Neo4j writer (the only neo4j
  driver importer).

---

## PRECONDITION -- Docker + Neo4j up

This skill needs Docker running and a Neo4j server reachable over Bolt. It does
NOT install or start Docker for you.

1. Confirm the Layer-2 capability with the `doctor` skill. `doctor` runs a
   read-only Docker probe (which/`docker version`/`docker compose version` rc --
   it NEVER pulls or starts anything) and reports "build an entity graph (Layer
   2)" as READY or UNAVAILABLE. If UNAVAILABLE, follow its setup pointer (the
   `setup` skill INSTRUCTS the operator to install Docker Desktop; it never
   auto-installs).

2. Provide the Neo4j password, then start the graph profile:

   ```
   cp infra/.env.example infra/.env
   # edit infra/.env and set a strong, unique NEO4J_PASSWORD
   docker compose -f infra/docker-compose.yml --env-file infra/.env --profile graph up -d
   ```

   `infra/.env` is gitignored; only `infra/.env.example` is committed. Neo4j is
   bound to `127.0.0.1` only (HTTP 7474, Bolt 7687) and lives under the `graph`
   profile, so the default (journalist) compose surface never starts it. Wait for
   the container healthcheck to pass before writing.

   LICENSING NOTE: the Neo4j Community server image (`neo4j:5.26.26-community`) is
   GPLv3. Magpie talks to it over Bolt/TCP across a process boundary and ships
   THIS compose file + these docs, NEVER the image -- YOU (the operator) pull
   `neo4j:5.26.26-community` from Docker Hub. See `references/prior-art.md`.

---

## The flow (6 steps)

The resolve -> review -> apply -> snapshot -> write sequence spans SEPARATE
process invocations (the human reviews the HTML offline between resolve and
apply), so everything persists to the scratch dir and each entry point reloads
from disk. There is NO long-running server and NO in-memory state carried across
the gate.

### STEP 1 -- RESOLVE (cross-corpus)

Run nomenklatura xref over the combined corpus and drain the review band:

```python
from scripts.entity_nomenklatura import resolve
from scripts.entity_resolution_policy import ResolutionConfig

result = resolve(entities_paths, scratch_dir, ResolutionConfig())
# result.candidate_snapshot_path, result.packet_hash, result.auto_merge_log_path
```

`resolve(entities_paths, scratch_dir, config)`:

- Points `NOMENKLATURA_DB_URL` at a sqlite DB under `scratch_dir`, loads every
  bundle into one combined store, and runs `xref(algorithm=LogicV2,
  auto_threshold=config.auto_threshold, user="magpie-auto")` -- a SINGLE
  cross-corpus pass.
- AUTO-MERGES the top bucket (score `>= auto_threshold`, default 0.98). Every
  auto-merge is LOGGED and REVERSIBLE: it writes `auto_merge_log.jsonl`
  (canonical, members, names, score, algorithm, threshold) and a logged
  auto-merge can be undone later by a NEGATIVE `decide` on the pair. The
  `user="magpie-auto"` tag keeps auto merges distinguishable from human ones.
  Nothing merges unaccountably.
- DRAINS the `[review_floor, auto_threshold)` band (default `[0.70, 0.98)`) from
  the LIVE resolver via `get_candidates()` into a candidate snapshot. (The
  library exposes only the upper `auto_threshold`; Magpie enforces the 0.70 floor
  itself.)
- Persists, under the scratch dir: `resolver.db` (the per-investigation resolver
  DB), `auto_merge_log.jsonl`, `candidate_snapshot.json` (its hash IS the
  `packet_hash`), and `run.json` (records the bundle paths + config so the later
  steps reload the SAME inputs).

### STEP 2 -- REVIEW (the MANDATORY human gate -- NEVER autonomous)

This is the gate. A human MUST decide the band before anything is applied. Do not
skip it, do not have the model decide the pairs.

Generate the self-contained HTML review packet from the candidate snapshot and
open it in a browser:

```python
import json, pathlib
from scripts.entity_review_packet import build_candidate_snapshot, render_html

snapshot = json.loads(pathlib.Path(result.candidate_snapshot_path).read_text())
html = render_html(snapshot, snippet_resolver)
pathlib.Path(scratch_dir, "review_packet.html").write_text(html, encoding="utf-8")
# open review_packet.html in a browser
```

- `resolve` already called `build_candidate_snapshot(...)` to produce the
  candidate snapshot on disk; `render_html(candidate_snapshot, snippet_resolver)`
  renders it. (You can rebuild the snapshot from `Candidate`s with
  `build_candidate_snapshot(...)` directly if you need to, but the normal flow
  loads the one `resolve` wrote.)
- `snippet_resolver(doc_id, page, char_start, char_end, *, context_chars=0) ->
  str` is INJECTED and returns the SOURCE TEXT around one mention, hydrated from
  the Phase-6 page text. Phase-12 offsets are PAGE-LOCAL, so `page` is required.
  Snippet text is LOCAL raw source -- it is rendered into the packet for the
  operator's eyes only and is never persisted or published.
- The packet is ONE offline HTML document (no external CSS/JS/fonts; theme
  follows the OS via prefers-color-scheme). Each pair is a side-by-side card:
  entity A vs B, score, the top source snippet collapsed, an expandable "More
  evidence" panel (additional mentions, wider-window snippets, and disambiguating
  `properties` like address/dob/badge), and merge / keep-distinct / unsure
  buttons. A thin 0.73 name match becomes decidable when a shared address
  surfaces in the panel.
- The human decides every pair, then clicks Export to download `verdicts.json`
  (`{investigation_id, packet_hash, verdicts:[{left, right, verdict}]}`). The
  `packet_hash` is embedded in the export so STEP 3 can detect drift.

For just a handful of pairs, an inline text review is the documented fallback,
but it must still be a HUMAN deciding -- the gate does not move.

### STEP 3 -- APPLY (FAIL-CLOSED)

Apply the exported verdicts to the resolver:

```python
from scripts.entity_nomenklatura import apply_verdicts

outcome = apply_verdicts(verdict_json_path, scratch_dir, ResolutionConfig())
# outcome.applied, outcome.skipped, outcome.aborted_reason
```

`apply_verdicts(verdict_json_path, scratch_dir, config)` is FAIL-CLOSED:

- It reopens the resolver, reloads the store from `run.json`, and RECOMPUTES the
  live candidate-snapshot hash. If the resolver MOVED since the packet was
  generated (the recomputed hash != the verdict file's `packet_hash`), it applies
  NOTHING and returns `aborted_reason` telling you to REGENERATE the packet. This
  is the default safe behavior, not a silent per-pair skip -- if you see an abort,
  go back to STEP 1/2.
- When the hashes match, it applies each verdict after a PER-PAIR live re-check
  (the pair must still be a live `NO_JUDGEMENT` candidate): `merge -> POSITIVE`,
  `distinct -> NEGATIVE`, `unsure -> skip`. A pair whose live state drifted is
  skipped and reported. Human merges are tagged with a reviewer `user`, so they
  stay distinguishable from the `magpie-auto` merges.

OPTIONAL -- review the auto-merges: `auto_merge_log.jsonl` lists every auto-merge
with its evidence. To undo one, feed a `distinct` verdict for that pair through
the same apply path (the resolver supports re-deciding). Surface "review the N
auto-merges" as an explicit, optional step when the operator wants maximum
scrutiny.

### STEP 4 -- SNAPSHOT (the portable 13a/13b seam)

Build the portable resolved snapshot:

```python
from scripts.entity_nomenklatura import build_resolved_snapshot

snapshot = build_resolved_snapshot(scratch_dir, investigation_id, ResolutionConfig())
```

`build_resolved_snapshot(scratch_dir, investigation_id, config)` reopens the
resolver, reloads the store from `run.json`, clusters every node by
`resolver.get_canonical`, and emits the durable snapshot dict
`{metadata, entities[], edges[], provenance[]}` (top-level entities + edges, NOT
cluster-nested). Each entity carries a STABLE, content-addressed `canonical_id =
sha256("|".join(sorted(member_ids)))[:40]` -- NOT the mutable nomenklatura `NK-`
id (that is stored only as `resolver_id` metadata). The snapshot is the SINGLE
source of truth for this investigation's graph and the seam 13b will consume
UNCHANGED.

Canonical-id churn is by design: a changed cluster membership yields a NEW
canonical_id because it is a genuinely different entity. The snapshot is the
source of truth; the graph is re-derived from it (STEP 5's scoped REPLACE).

### STEP 5 -- WRITE (investigation-scoped REPLACE; idempotent)

Construct an injected Neo4j driver, ensure the schema, then write:

```python
from neo4j import GraphDatabase
from scripts import entity_graph_neo4j

driver = GraphDatabase.driver("bolt://127.0.0.1:7687", auth=("neo4j", password))
entity_graph_neo4j.ensure_schema(driver)        # MUST run before the first write
stats = entity_graph_neo4j.write(driver, snapshot)
driver.close()
```

- `ensure_schema(driver)` creates a single-property uniqueness constraint on a
  synthesized `scoped_id = investigation_id + ":" + canonical_id` plus a
  supporting index (single-property uniqueness is Neo4j-Community-safe; a
  composite NODE KEY would be Enterprise-only).
- `write(driver, snapshot)` is an investigation-SCOPED REPLACE: it MERGEs the
  snapshot's current entities/relationships on the scoped ids, then DELETEs only
  the in-scope rows the snapshot no longer contains. It is idempotent and
  rerun-safe, and it NEVER reads or deletes another investigation's subgraph -- a
  re-run with changed membership cleanly replaces THIS investigation's graph
  only. `ensure_schema` MUST have been called before the first `write`.

### STEP 6 -- OUTPUT (via the Librarian, AGGREGATE only)

Emit an AGGREGATE findings note through the `librarian` skill:

- cluster counts;
- N auto-merged (logged + reversible) -- from `auto_merge_log.jsonl`;
- M human-decided -- from the apply outcome;
- the top-degree (most connected) entities in the graph.

Raw member PII stays LOCAL (the scratch dir + the source text). The aggregate
note carries counts and structure, not raw rows. If ANY surfaced text would
include PII (a name in a top-degree list, a snippet), route it through the
`redact-output` skill first -- that is the spine seam every Magpie output crosses.

---

## Load-bearing decisions (do not deviate)

- MANDATORY HUMAN REVIEW GATE. STEP 2 is never autonomous; the human decides the
  band. The human gate -- not the matcher -- is the differentiator. Resolution
  precision on FOIA names is unverified, so auto-merge is conservative (>=0.98),
  the band is actively reviewed, and every auto-merge is logged + reversible.
- PER-INVESTIGATION SCRATCH RESOLVER DB. The resolver SQLite lives under a
  gitignored scratch dir via `NOMENKLATURA_DB_URL`, never global. The skill is the
  SINGLE writer (it owns the begin/commit boundary; SQLite is single-writer). A
  crashed run leaves a disposable scratch DB -- discard it and re-run.
- FAIL-CLOSED APPLY. A packet-hash mismatch aborts the WHOLE apply (regenerate the
  packet); a drifted pair is skipped + reported. Never apply on a moved resolver.
- DOCKER / OPERATOR-TIER POSITIONING. Layer-2 only. No Docker in any journalist
  surface; `setup`/`doctor` only PROBE for Docker (read-only), never auto-install.
- NEO4J COMMUNITY IS GPLv3. Ship the compose file + docs, NEVER the image; the
  operator pulls `neo4j:5.26.26-community` themselves. Bolt across a process
  boundary is the community reading of the license, not an official ruling.
- WATCHLIST / OWN-CORPUS CROSS-REF IS 13b. A separate, later PR (yente +
  OpenSearch + `yente-mcp`). Not in scope here.

See `references/prior-art.md` for the verified library versions, the exact
resolution/graph API facts, the honest limits, and the CI verification surface.

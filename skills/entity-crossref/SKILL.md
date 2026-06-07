---
name: entity-crossref
description: Cross-reference an investigation's resolved entities against the investigator's own corpus (FOSS default, zero external data) and, opt-in, sanctions/PEP watchlists, using a local yente + OpenSearch stack. Layer-2, operator-tier, Docker-gated -- run it after building the entity graph to screen the resolved people/orgs and surface matches as leads for a human.
---

# entity-crossref

Take the Phase-13a resolved snapshot for an investigation, index it as a private yente dataset, and cross-reference the resolved entities against (a) your own corpus -- the pure-FOSS default, with ZERO external data -- and (b) sanctions/PEP watchlists, which are OPT-IN, CC-BY-NC, and documented below. A thin read-only yente-mcp server exposes the same lookups to the model. This runs AFTER entity-graph (13a): it consumes the resolved snapshot UNCHANGED and never reaches back into the resolver DB.

This skill is Layer-2, OPERATOR-tier, and Docker-gated. The journalist onramp (the JOURNALIST_START path and the doctor Track-A capabilities) stays Docker-free and is NOT touched by anything here. Watchlist hits are LEADS for a human to chase, never verdicts.

## What you need before you start

- A Phase-13a resolved snapshot (one JSON file) for the investigation -- the entity_resolved_snapshot output.
- Docker running. Confirm with the `doctor` skill: it reports "cross-reference entities (Layer 2)" as READY or UNAVAILABLE off a read-only Docker probe. doctor never starts services; it only probes.
- The crossref deps installed from requirements-crossref.txt (httpx + mcp). These are cross-platform.

The code surface (all under scripts/, plus infra/):

- scripts/entity_yente_dataset.py -- pure: turns a snapshot into a yente entities file (line-delimited FtM JSON) plus render_manifest.
- scripts/entity_crossref.py -- pure: /match request and response shaping, typed hits, and the cross-ref report.
- scripts/entity_yente_client.py -- the live yente HTTP edge (lazy httpx import) plus run_crossref.
- scripts/yente_mcp_server.py -- the thin read-only yente-mcp server (5 tools).
- infra/docker-compose.yml (the `crossref` profile) plus infra/yente/*.yml (manifest TEMPLATES).

## PRECONDITION -- emit the dataset, bring up the stack, build the index

1. Emit the own-corpus dataset and render the live manifest into the gitignored data dir. In Python:

   ```
   from scripts.entity_yente_dataset import (
       write_dataset, render_manifest, DatasetEntry, DATASET_NAME,
   )
   res = write_dataset(snapshot, "data/magpie_corpus")
   render_manifest(
       [DatasetEntry(
           name=res["name"],
           title="Magpie corpus",
           path="/data/entities.ftm.json",
           version=res["version"],
       )],
   )  # write the rendered manifest to data/magpie_corpus/manifest.yml
   ```

2. Copy the env template and set strong secrets:

   ```
   cp infra/.env.example infra/.env
   ```

   Set OPENSEARCH_ADMIN_PASSWORD and YENTE_UPDATE_TOKEN to strong values, and also set NEO4J_PASSWORD (see the IMPORTANT NOTE below).

3. Bring the crossref stack up:

   ```
   docker compose -f infra/docker-compose.yml --env-file infra/.env --profile crossref up -d --wait
   ```

4. Build the index explicitly:

   ```
   docker compose -f infra/docker-compose.yml --env-file infra/.env --profile crossref exec -T yente yente reindex
   ```

   AUTO_REINDEX is false on purpose: yente does NOT auto-index, so this explicit `yente reindex` is the deterministic index build. Re-run it whenever the snapshot changes.

IMPORTANT NOTE: any `docker compose` invocation interpolates the WHOLE compose file, so even the crossref profile needs NEO4J_PASSWORD set (it is the graph service's guard), and the graph profile needs the crossref vars. Using `--env-file infra/.env` (which carries all three secrets, straight from infra/.env.example) satisfies this. OpenSearch also needs the host WSL2 `vm.max_map_count` >= 262144; the setup skill documents the persistence step.

## The flow

1. RESOLVE -> you already have the resolved snapshot from entity-graph (13a).
2. EMIT + INDEX -> the precondition above (write the dataset, render the manifest, bring up the stack, run `yente reindex`).
3. CROSS-REFERENCE:

   ```
   run_crossref(
       snapshot,
       scopes,
       YenteClient("http://127.0.0.1:8000"),
       index_provenance={...},
   )
   ```

   `scopes` is ["own_corpus"] by default, or ["own_corpus", "watchlists"] when watchlists are opted in. run_crossref POSTs /match per scope, keyed by canonical_id, parses and groups the hits by dataset, and assembles a cross-ref REPORT carrying an index-provenance block.
4. OUTPUT via the Librarian: an AGGREGATE findings note -- counts per scope, top watchlist matches by score, and the index-provenance block. Raw matched names are PII, so route any surfaced text through the redact-output skill. Hits are LEADS, never verdicts.

## WATCHLISTS ARE OPT-IN (CC-BY-NC)

The default own-corpus cross-ref pulls ZERO external data. Watchlists -- the OpenSanctions `default` catalog -- are CC-BY-NC (NON-COMMERCIAL). Enabling them is a deliberate, documented choice:

- Render the manifest with the watchlist catalog: `entity_yente_dataset.render_manifest(..., include_watchlist=True)`.
- Re-run `yente reindex`. This pulls a multi-GB dataset and is slow.

Own-corpus results are REPRODUCIBLE: the dataset version is a content hash. Watchlist results are best-effort and externally versioned -- the upstream catalog can change behind a stable URL, so the report records the /catalog version and updated_at. Commercial users need the paid OpenSanctions delivery token, which Magpie never ships.

## yente-mcp (optional, operator-wired)

yente-mcp is a thin READ-ONLY MCP server: search / match / get_entity / list_datasets / cross_reference. It has hard timeouts, result caps, a fixed loopback base URL, a fixed scope allowlist, NO write or reindex tool, and NO raw pass-through. It is NOT in the default .mcp.json -- if it were, it would auto-start for journalists and would need a running yente. To wire it, merge the snippet in `.mcp.yente.example.json` into your project .mcp.json while the crossref stack is up. Treat the server as untrusted.

## Load-bearing decisions (do not deviate)

- Consumes the resolved snapshot UNCHANGED (assert_snapshot_consumable is the entry check); never touches the resolver DB.
- Watchlists are OPT-IN; own-corpus (zero external data) is the FOSS default; CC-BY-NC is documented.
- Deterministic indexing: AUTO_REINDEX=false plus an explicit `yente reindex`; the dataset version is a content hash.
- yente-mcp is read-only and operator-wired (never in the default .mcp.json); treat the server as untrusted.
- Layer-2 / operator-tier / Docker-gated; no Docker in any journalist surface. Hits are leads for a human.

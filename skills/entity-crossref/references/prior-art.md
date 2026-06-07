# entity-crossref prior art -- verified library facts

This is the verified prior art for the Phase-13b crossref surface, so future work
does not have to re-derive it. All facts below were confirmed against the pinned
versions and the live CI surface.

## Versions and licenses

- yente 5.4.0 -- image ghcr.io/opensanctions/yente:5.4.0, MIT. This is the LAST
  Elasticsearch-8 release of yente. OpenSearch is reached by setting
  YENTE_INDEX_TYPE=opensearch and LEAVING the AOSS-only settings
  YENTE_OPENSEARCH_REGION and YENTE_OPENSEARCH_SERVICE UNSET (those force the
  Amazon OpenSearch Serverless signing path, which we do not want for a local
  container).
- OpenSearch 2.19.5 -- Apache-2.0. Final release of the 2.x line.
- mcp SDK 1.27.2 -- MIT. FastMCP lives at mcp.server.fastmcp.
- httpx -- BSD. Used as the yente HTTP client (lazy import on the live edge).
- followthemoney (ftm) -- Linux/CI-only. Not importable on the Windows dev box;
  the FtM-touching code is exercised on the CI surface, not locally.

## yente env surface (authoritative, from yente v5.4.0 settings.py)

- YENTE_INDEX_TYPE -- default elasticsearch; we set opensearch.
- YENTE_INDEX_URL -- default http://localhost:9200.
- YENTE_INDEX_NAME -- default yente.
- YENTE_MANIFEST -- default is the COMMERCIAL manifest; we point it at our own.
- YENTE_AUTO_REINDEX -- default true; we set false.
- YENTE_CRONTAB -- hourly at a random minute when auto-reindex is on.
- YENTE_DELTA_UPDATES -- delta-update toggle.
- YENTE_UPDATE_TOKEN -- env_opt; guards POST /updatez.
- YENTE_MAX_BATCH -- 100.
- Read endpoints (search / match / entities / catalog) have NO auth.

## AUTO_REINDEX=false fact

With YENTE_AUTO_REINDEX false, yente does NOT auto-index on startup. You build the
index with `yente reindex`. This is what makes the index build deterministic and
reproducible: index only when we say so, after the dataset is emitted.

## The /match contract

- Request: POST /match/{scope} with body
  {"queries": {"<key>": {"schema": ..., "properties": {prop: [vals]}}}}.
- The response is keyed by OUR query key -- that is the attribution mechanism.
- namespace:true rewrites the indexed id to "<our-id>.<hmac-hex>" (our id is a
  prefix). This is an OBSERVED convenience, NOT a contract: always attribute by
  the query key, not by parsing the rewritten id.
- The /match response includes per-feature explanations in 5.4.0.

## The civic.yml landmine

The shipped manifests/civic.yml STILL pulls the CC-BY-NC `default` catalog. So
Magpie ships its OWN datasets-only default manifest (catalogs omitted, zero
external data) and a SEPARATE opt-in watchlist manifest. Do not reuse the shipped
civic.yml as-is; it would silently pull the non-commercial catalog.

## CI verification surface

The `crossref` CI job is the ONLY real surface for the yente + OpenSearch edges:
compose config -> up --wait -> exec yente reindex -> live `-m yente` tests ->
teardown. Gate the merge on it (the Phase-12 / 13a lesson: these edges are
invisible on Windows, so they must be proven in CI). Every docker compose step
needs all three secrets present because of the file-wide ${VAR:?} interpolation
across the compose file. The own-corpus entities file is PII-derived and is
gitignored.

## Licensing summary

- yente -- MIT.
- OpenSearch -- Apache-2.0.
- mcp -- MIT.
- httpx -- BSD.
- OpenSanctions DATA -- CC-BY-NC (NON-COMMERCIAL). Watchlists are opt-in only;
  commercial use needs the paid OpenSanctions delivery token, which Magpie never
  ships.

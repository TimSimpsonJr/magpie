# Phase 13b -- cross-ref + infra -- Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> to implement this plan task-by-task (one implementer subagent per task; per-task
> spec-review + code-quality-review; fold fixes). Bake into EVERY dispatch:
> "You are ALREADY on branch feat/phase13b-crossref-infra; commit to it; do NOT
> branch." ASCII-only. "Read ONLY this plan + the design doc + the files you
> create; do NOT open other repo files (many carry non-ASCII)." Pure cores import
> ONLY stdlib; the edges LAZY-import httpx / mcp. Sequential commits.

**Goal:** Cross-reference Phase-13a resolved entities against the investigator's
own corpus (FOSS default) and sanctions/PEP watchlists (opt-in) via yente +
OpenSearch, plus a thin read-only yente-mcp server.

**Architecture:** Suite pure-core / lazy-edge / decoupled split. Two pure stdlib
cores (`entity_yente_dataset` emit, `entity_crossref` shaping) are Windows-golden;
two live edges (`entity_yente_client` lazy-imports httpx, `yente_mcp_server`
lazy-imports mcp) are exercised by the CI `crossref` job (the only real surface).
Infra: index (OpenSearch 2.19.5) + yente (5.4.0) under a `crossref` compose
profile. The design doc (`docs/plans/2026-06-07-magpie-phase13b-crossref-infra-design.md`)
is the WHY; this is the HOW.

**Tech Stack:** Python 3.12; httpx + mcp (FastMCP) in a new cross-platform
`requirements-crossref.txt`; yente 5.4.0 + OpenSearch 2.19.5 images;
followthemoney (Linux/CI `ftm` job only) for the emit-validation contract test.

**VERIFIED FACTS (local smoke + authoritative source -- build to these exactly;
see `.codex-review/research/phase13b-gateB-validation-2026-06-07.md`):**
- yente FtM entity dict = `{"id":..., "schema":..., "properties":{prop:[vals]}}`,
  one JSON per line, no pretty-print.
- `POST /match/{scope}` body: `{"queries":{"<key>":{"schema":...,"properties":
  {...}}}}`; response: `{"responses":{"<key>":{"results":[{"id","caption",
  "schema","properties","datasets":[...],"score","match",...}], ...}}}`. The
  response is keyed by OUR query key => attribution. (smoke-proven with a 40-char key)
- `namespace:true` -> result id `<our-id>.<hmac-hex>` (our id is a prefix; an
  observed convenience, NOT a contract). Attribution is the query key only.
- `AUTO_REINDEX=false` does NOT auto-index; build the index with
  `docker compose --profile crossref exec -T yente yente reindex`.
- A datasets-only manifest (no `catalogs:`) pulls ZERO external data (/catalog
  shows only magpie_corpus). yente env: `YENTE_INDEX_TYPE=opensearch`,
  `YENTE_INDEX_URL=http://index:9200` (service MUST be named `index`),
  `YENTE_MANIFEST`, `YENTE_AUTO_REINDEX=false`. Leave OPENSEARCH_REGION/SERVICE unset.
- mcp 1.27.2: `from mcp.server.fastmcp import FastMCP`; `FastMCP("name")`;
  `@mcp.tool()` (type hints -> schema, docstring -> description); `mcp.run()` stdio.

---

## Task 0: Scaffolding (markers, requirements, env, gitignore)

**Files:**
- Modify: `pyproject.toml` (add the `yente` marker)
- Create: `requirements-crossref.txt`
- Modify: `infra/.env.example`
- Modify: `.gitignore`
- Modify: `.github/workflows/ci.yml` (add `not yente` to the OFFLINE job exclusion only; the new `crossref` job comes in Task 7)
- Test: `tests/test_phase13b_scaffolding.py`

**Step 1: Write the failing test** (`tests/test_phase13b_scaffolding.py`)

```python
import pathlib, tomllib

REPO = pathlib.Path(__file__).resolve().parent.parent

def test_yente_marker_registered():
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    markers = "\n".join(cfg["tool"]["pytest"]["ini_options"]["markers"])
    assert "yente:" in markers

def test_offline_exclusion_excludes_yente():
    # The offline CI job must exclude the yente marker (live infra).
    ci = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "not yente" in ci

def test_requirements_crossref_present():
    req = (REPO / "requirements-crossref.txt").read_text(encoding="utf-8")
    assert "httpx" in req and "mcp" in req

def test_env_example_has_opensearch_and_update_token():
    env = (REPO / "infra/.env.example").read_text(encoding="utf-8")
    assert "OPENSEARCH_ADMIN_PASSWORD" in env and "YENTE_UPDATE_TOKEN" in env

def test_gitignore_blocks_own_corpus_data():
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "/data/" in gi or "data/magpie_corpus/" in gi
```

**Step 2:** Run `& .venv\Scripts\python.exe -m pytest tests/test_phase13b_scaffolding.py -q` -> FAIL.

**Step 3: Implement.**
- `pyproject.toml` markers: add
  `"yente: live yente + OpenSearch cross-ref tests (Track B Phase 13b; need the crossref compose up); select with -m yente",`
- `requirements-crossref.txt` (cross-platform, Layer-2-only):
  ```
  # Phase 13b cross-ref runtime (Layer-2; cross-platform). The yente HTTP client
  # (httpx) + the yente-mcp server (mcp / FastMCP). Lazy-imported by the edges, so
  # the offline suite does NOT need these; install for cross-ref + the CI crossref job.
  httpx==0.28.1
  mcp==1.27.2
  ```
  (Confirm the current httpx pin at build time; 0.28.x is fine on 3.12.)
- `infra/.env.example`: append
  ```
  # Phase 13b cross-ref (crossref profile). OpenSearch 2.12+ validates this
  # password's FORMAT even with the security plugin disabled -- set a strong one.
  OPENSEARCH_ADMIN_PASSWORD=change-me-to-a-strong-random-password
  # yente reindex token (POST /updatez). Any non-empty value; never exposed read-side.
  YENTE_UPDATE_TOKEN=change-me-to-a-random-hex
  ```
- `.gitignore`: add under a new heading
  ```
  # --- Phase 13b: emitted own-corpus yente dataset (PII-derived; never commit) ---
  /data/
  ```
- `ci.yml`: in the OFFLINE job's pytest `-m` string, append ` and not yente`
  (leave the other jobs for Task 7).

**Step 4:** Run the test -> PASS. Run the full offline suite to confirm no
regression: `& .venv\Scripts\python.exe -m pytest -m "not docling and not spacy and not xray and not tsa and not gliner and not ftm and not neo4j and not compose and not yente" -q`.

**Step 5: Commit** `feat(phase13b): scaffolding -- yente marker, requirements-crossref, env, gitignore`.

---

## Task 1: entity_yente_dataset.py (pure core -- snapshot -> yente dataset)

**Files:**
- Create: `scripts/entity_yente_dataset.py`
- Test: `tests/test_entity_yente_dataset.py`

**API (stdlib only -- NO followthemoney/httpx/mcp/yaml-lib import; render YAML by
hand to keep it dependency-free and deterministic):**

```python
"""ASCII only. Pure core: a Phase-13a resolved snapshot -> a yente own-corpus
dataset (line-delimited FtM entities file) + the yente manifest YAML.

Imports ONLY stdlib (json, hashlib, pathlib). The emitted FtM dict shape
{id, schema, properties} is what yente indexes; an ftm-marked Linux/CI test
validates it against followthemoney. Reads no clock -- the dataset `version` is a
CONTENT HASH, so identical snapshots emit an identical dataset (determinism).
"""
from __future__ import annotations
import hashlib, json, pathlib
from dataclasses import dataclass

DATASET_NAME = "magpie_corpus"
WATCHLIST_CATALOG_URL = "https://data.opensanctions.org/datasets/latest/default/catalog.json"

@dataclass
class DatasetEntry:
    name: str
    title: str
    path: str          # path INSIDE the yente container, e.g. /data/entities.ftm.json
    version: str
    namespace: bool = True

def _require_field(entity: dict, key: str):
    # Plan-review snapshot-contract-gap: assert_snapshot_consumable does NOT
    # guarantee schema/caption/name, so the emit validates its OWN needs and
    # raises naming the offending entity (never emits a malformed FtM entity).
    v = entity.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        raise ValueError("emit: entity missing/empty %r (canonical_id=%r)"
                         % (key, entity.get("canonical_id")))
    return v

def entity_to_ftm_dict(entity: dict) -> dict:
    """One ResolvedEntity dict (snapshot 'entities' item) -> an FtM entity dict.

    name = [caption] + aliases, order-deduped, merged with any existing
    properties['name']. All other snapshot properties pass through UNCHANGED
    (they are already FtM props from Phase-12 ftmize). NO invented props
    (no canonicalId -- design D3 / Codex ftm-joinback). id = canonical_id.
    Raises ValueError on a missing/empty canonical_id / schema / usable name.
    """
    cid = _require_field(entity, "canonical_id")
    schema = _require_field(entity, "schema")
    props = {k: list(v) for k, v in (entity.get("properties") or {}).items()}
    names, seen = [], set()
    for n in list(props.get("name") or []) + [entity.get("caption")] + list(entity.get("aliases") or []):
        if n and n not in seen:
            seen.add(n); names.append(n)
    if not names:
        raise ValueError("emit: entity %r has no usable name (caption/aliases/name all empty)" % cid)
    props["name"] = names
    return {"id": cid, "schema": schema, "properties": props}

def snapshot_to_entities(snapshot: dict) -> list[dict]:
    """All snapshot entities -> FtM dicts. Runs assert_snapshot_consumable first.
    v1 emits NODE entities only (the screening targets); edges stay in Neo4j."""
    from scripts.entity_resolved_snapshot import assert_snapshot_consumable
    assert_snapshot_consumable(snapshot)
    return [entity_to_ftm_dict(e) for e in snapshot["entities"]]

def _serialize_entities(entities: list[dict]) -> str:
    # one compact JSON per line, sort_keys for determinism, trailing newline.
    return "".join(json.dumps(e, sort_keys=True, ensure_ascii=True) + "\n" for e in entities)

def dataset_version(entities_text: str) -> str:
    return hashlib.sha256(entities_text.encode("utf-8")).hexdigest()[:16]

def write_dataset(snapshot: dict, out_dir, *, name: str = DATASET_NAME) -> dict:
    """Write <out_dir>/entities.ftm.json. Returns {entities_path, version, count}."""
    out = pathlib.Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    entities = snapshot_to_entities(snapshot)
    text = _serialize_entities(entities)
    version = dataset_version(text)
    entities_path = out / "entities.ftm.json"
    entities_path.write_text(text, encoding="utf-8")
    return {"entities_path": str(entities_path), "version": version, "count": len(entities)}

def render_manifest(datasets: list[DatasetEntry], *, include_watchlist: bool = False) -> str:
    """Render a yente manifest YAML by hand (no yaml dep). Own-corpus default has
    NO catalogs block; the watchlist manifest adds the FREE civic `default` catalog."""
    lines = []
    if include_watchlist:
        lines += [
            "catalogs:",
            '  - url: "%s"' % WATCHLIST_CATALOG_URL,
            "    scope: default",
            "    resource_name: entities.ftm.json",
            "",
        ]
    lines.append("datasets:")
    for d in datasets:
        lines += [
            "  - name: %s" % d.name,
            '    title: "%s"' % d.title,
            "    path: %s" % d.path,
            '    version: "%s"' % d.version,
            "    namespace: %s" % ("true" if d.namespace else "false"),
        ]
    return "\n".join(lines) + "\n"
```

**Tests (Windows-golden):** `entity_to_ftm_dict` (name merge/dedupe order, props
passthrough, NO canonicalId key, id == canonical_id); `snapshot_to_entities`
calls the consumable check (a dangling-edge snapshot raises); `write_dataset`
(line-delimited, deterministic bytes => stable version; re-emit of the same
snapshot gives the same version; a changed caption changes the version); JSONL
parses one-entity-per-line; `render_manifest` own (no `catalogs:`) vs watchlist
(has the civic URL) and `namespace: true`. Use a small fake snapshot fixture
(2 Person + 1 Company) shaped like `entity_resolved_snapshot.build_snapshot`.

**Commit** `feat(phase13b): entity_yente_dataset -- snapshot -> yente entities + manifest`.

---

## Task 2: entity_crossref.py (pure shaping)

**Files:**
- Create: `scripts/entity_crossref.py`
- Test: `tests/test_entity_crossref.py`

**API (stdlib only):**

```python
"""ASCII only. Pure /match request+response shaping for cross-ref. Imports ONLY
stdlib. No httpx/yente. Windows-golden with fixture JSON."""
from __future__ import annotations
from dataclasses import dataclass, field

# The fixed scope allowlist exposed everywhere (design D8). Maps a friendly scope
# name -> the yente dataset/collection scope. own_corpus is always available;
# watchlists only when the watchlist manifest is loaded.
SCOPES = {"own_corpus": "magpie_corpus", "watchlists": "default"}

# FtM match props worth sending from a resolved entity (others ignored).
_MATCH_PROPS = ("name", "birthDate", "country", "nationality", "address",
                "idNumber", "registrationNumber", "jurisdiction", "email", "phone")

@dataclass
class CrossRefHit:
    query_canonical_id: str
    scope: str                 # the friendly scope name (own_corpus / watchlists)
    result_id: str
    caption: str
    schema: str
    datasets: list[str]
    score: float
    match: bool
    properties: dict = field(default_factory=dict)

def build_match_query(entity: dict) -> tuple[str, dict]:
    """ResolvedEntity dict -> (query_key, single-query body item). query_key =
    canonical_id (the attribution handle). properties picked from _MATCH_PROPS;
    name falls back to [caption]+aliases when absent."""
    props = {p: list(entity["properties"][p]) for p in _MATCH_PROPS
             if p in (entity.get("properties") or {}) and entity["properties"][p]}
    if not props.get("name"):
        names, seen = [], set()
        for n in [entity.get("caption")] + list(entity.get("aliases") or []):
            if n and n not in seen:
                seen.add(n); names.append(n)
        props["name"] = names
    return entity["canonical_id"], {"schema": entity["schema"], "properties": props}

def build_match_body(entities: list[dict]) -> dict:
    """Batch up to N entities into one /match body keyed by canonical_id."""
    queries = {}
    for e in entities:
        key, item = build_match_query(e)
        queries[key] = item
    return {"queries": queries}

def parse_match_response(resp_json: dict, *, scope: str, threshold: float,
                         cap: int) -> list[CrossRefHit]:
    """yente /match response -> CrossRefHits, attributed by the response KEY
    (== our canonical_id), filtered by threshold (score >= threshold OR
    result['match'] True), capped at `cap` per query."""
    hits = []
    for key, payload in (resp_json.get("responses") or {}).items():
        kept = 0
        for r in (payload.get("results") or []):
            score = float(r.get("score") or 0.0)
            if not (r.get("match") or score >= threshold):
                continue
            hits.append(CrossRefHit(
                query_canonical_id=key, scope=scope, result_id=r.get("id", ""),
                caption=r.get("caption", ""), schema=r.get("schema", ""),
                datasets=list(r.get("datasets") or []), score=score,
                match=bool(r.get("match")), properties=dict(r.get("properties") or {})))
            kept += 1
            if kept >= cap:
                break
    return hits

def group_hits_by_dataset(hits: list[CrossRefHit]) -> dict:
    out: dict = {}
    for h in hits:
        for ds in (h.datasets or ["(unknown)"]):
            out.setdefault(ds, []).append(h)
    return out

def build_crossref_report(hits_by_scope: dict, *, index_provenance: dict,
                          threshold: float, algorithm: str, generated_at) -> dict:
    """Assemble the durable cross-ref report. Hits serialized via dataclasses.asdict.
    index_provenance carries {manifest_hash, dataset_file_hashes, yente_image_tag,
    opensearch_image_tag, catalog:[{name,version,updated_at,last_export,index_current}]}.
    Own-corpus is 'reproducible'; watchlist is 'best_effort_externally_versioned'."""
    import dataclasses
    return {
        "metadata": {"threshold": threshold, "algorithm": algorithm,
                     "generated_at": generated_at,
                     "reproducibility": {"own_corpus": "reproducible",
                                         "watchlists": "best_effort_externally_versioned"}},
        "index_provenance": index_provenance,
        "scopes": {scope: [dataclasses.asdict(h) for h in hits]
                   for scope, hits in hits_by_scope.items()},
    }
```

**Tests (Windows-golden, drive with a saved real /match response fixture from the
smoke):** `build_match_query` (key == canonical_id; name fallback; only
_MATCH_PROPS sent); `build_match_body` (batch keyed map); `parse_match_response`
(attribution by response key; threshold filter keeps match:true even below
threshold; cap per query; the namespaced result_id is preserved verbatim, NOT
parsed); `group_hits_by_dataset`; `build_crossref_report` (the reproducibility
labels + provenance passthrough). Add a fixture `tests/fixtures/yente_match_response.json`
(copy the smoke's response shape -- a self-hit with `datasets:["magpie_corpus"]`).

**Commit** `feat(phase13b): entity_crossref -- /match shaping, hits, report`.

---

## Task 3: entity_yente_client.py (live edge -- lazy httpx)

**Files:**
- Create: `scripts/entity_yente_client.py`
- Test: `tests/test_entity_yente_client.py` (pure parts Windows; live parts `@pytest.mark.yente`)

**API (httpx LAZY-imported -- module imports without httpx; loopback guard +
URL building are pure):**

```python
"""ASCII only. Thin live yente HTTP edge. LAZY-imports httpx (the only httpx
importer), so this module imports on the offline base; the live calls run in the
CI crossref job (-m yente). Loopback-guarded (design D8)."""
from __future__ import annotations
import ipaddress, urllib.parse

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

def is_loopback_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

class YenteClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, timeout: float = 10.0,
                 allow_remote: bool = False, max_bytes: int = 5_000_000):
        if not allow_remote and not is_loopback_url(base_url):
            raise ValueError("refusing non-loopback yente base URL %r (set allow_remote=True)" % base_url)
        self.base_url = base_url.rstrip("/"); self.timeout = timeout; self.max_bytes = max_bytes

    def _client(self):
        import httpx  # lazy
        return httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def _get_json(self, path, params=None):
        with self._client() as c:
            r = c.get(path, params=params); r.raise_for_status()
            if len(r.content) > self.max_bytes:
                raise ValueError("yente response exceeds max_bytes")
            return r.json()

    def readyz(self) -> bool:
        try:
            with self._client() as c:
                return c.get("/readyz").status_code == 200
        except Exception:
            return False

    def catalog(self) -> dict: return self._get_json("/catalog")
    def search(self, scope, q, *, limit=10): return self._get_json("/search/%s" % scope, {"q": q, "limit": limit})
    def get_entity(self, entity_id): return self._get_json("/entities/%s" % urllib.parse.quote(entity_id, safe=""))
    def match(self, scope, body, *, algorithm="best", threshold=0.7, limit=5):
        import httpx  # lazy
        with httpx.Client(base_url=self.base_url, timeout=self.timeout) as c:
            r = c.post("/match/%s" % scope, params={"algorithm": algorithm, "threshold": threshold, "limit": limit}, json=body)
            r.raise_for_status()
            if len(r.content) > self.max_bytes: raise ValueError("yente response exceeds max_bytes")
            return r.json()

def run_crossref(snapshot, scopes, client, *, threshold=0.7, cap=25, algorithm="best",
                 batch=100, generated_at=None, index_provenance=None) -> dict:
    """Batch the snapshot entities into /match calls per scope, parse + group,
    assemble the report. `scopes` is a list of friendly names from
    entity_crossref.SCOPES; `client` is injected (a real YenteClient live, or a
    fake in tests).

    Plan-review provenance-plumbing: the caller (skill/CI) supplies the FULL
    index_provenance it alone knows -- {manifest_hash, dataset_file_hashes,
    yente_image_tag, opensearch_image_tag} (run_crossref cannot invent the file
    hashes or the image tags). run_crossref AUGMENTS it with the live /catalog
    per-dataset metadata and passes the merged dict to build_crossref_report."""
    from scripts import entity_crossref as xref
    hits_by_scope = {}
    for scope in scopes:
        ds_scope = xref.SCOPES[scope]
        ents = snapshot["entities"]
        scope_hits = []
        for i in range(0, len(ents), batch):
            body = xref.build_match_body(ents[i:i+batch])
            resp = client.match(ds_scope, body, algorithm=algorithm, threshold=threshold)
            scope_hits += xref.parse_match_response(resp, scope=scope, threshold=threshold, cap=cap)
        hits_by_scope[scope] = scope_hits
    prov = dict(index_provenance or {})
    catalog = {}
    try: catalog = client.catalog()
    except Exception: pass
    prov["catalog"] = [{k: d.get(k) for k in ("name","version","updated_at","last_export","index_current")}
                       for d in (catalog.get("datasets") or [])]
    return xref.build_crossref_report(hits_by_scope, index_provenance=prov,
                                      threshold=threshold, algorithm=algorithm, generated_at=generated_at)
```

**Tests:** Windows-golden -- `is_loopback_url` (127.0.0.1/localhost/::1 True;
example.com / 10.x False); `YenteClient.__init__` refuses a non-loopback URL
unless allow_remote; `run_crossref` with a FAKE client (records calls, returns
the fixture response) asserts per-scope batching, the report shape + provenance
from a fake /catalog. Import-purity: `import scripts.entity_yente_client` works
WITHOUT httpx installed (subprocess test like the suite's). LIVE (`@pytest.mark.yente`):
against the running compose -- readyz True, match returns a self-hit, catalog
lists magpie_corpus.

**Commit** `feat(phase13b): entity_yente_client -- lazy-httpx yente edge + run_crossref`.

---

## Task 4: yente_mcp_server.py (live edge -- lazy mcp/FastMCP)

**Files:**
- Create: `scripts/yente_mcp_server.py`
- Test: `tests/test_yente_mcp_server.py` (tool logic Windows with a fake client; live process smoke `@pytest.mark.yente`)

**API (tool LOGIC in plain functions taking an injected client; FastMCP wiring in
a `build_server()` that LAZY-imports mcp):**

```python
"""ASCII only. Thin READ-ONLY yente-mcp server. Tool LOGIC lives in plain
functions (Windows-testable with a fake client); build_server() lazy-imports
FastMCP and registers them. Caps + scope allowlist + loopback guard (design D8).
NO write/reindex tool, NO raw pass-through."""
from __future__ import annotations
import os
from scripts import entity_crossref as xref
from scripts.entity_yente_client import YenteClient, DEFAULT_BASE_URL

MAX_HITS = 25

def _make_client() -> YenteClient:
    base = os.environ.get("YENTE_MCP_BASE_URL", DEFAULT_BASE_URL)
    allow_remote = os.environ.get("YENTE_MCP_ALLOW_REMOTE") == "1"
    return YenteClient(base, timeout=10.0, allow_remote=allow_remote)

def _resolve_scope(scope: str) -> str:
    if scope not in xref.SCOPES:
        raise ValueError("unknown scope %r; allowed: %s" % (scope, sorted(xref.SCOPES)))
    return xref.SCOPES[scope]

def tool_list_datasets(client) -> dict:
    return client.catalog()

def tool_search(client, scope: str, q: str, limit: int = 10) -> dict:
    return client.search(_resolve_scope(scope), q, limit=min(int(limit), MAX_HITS))

def tool_get_entity(client, entity_id: str) -> dict:
    return client.get_entity(entity_id)

def tool_match(client, scope: str, name: str, schema: str = "Person",
               threshold: float = 0.7) -> list[dict]:
    # ONE entity per call (effective MAX_BATCH=1; no bulk pass-through).
    body = {"queries": {"q": {"schema": schema, "properties": {"name": [name]}}}}
    resp = client.match(_resolve_scope(scope), body, threshold=threshold, limit=MAX_HITS)
    import dataclasses
    hits = xref.parse_match_response(resp, scope=scope, threshold=threshold, cap=MAX_HITS)
    return [dataclasses.asdict(h) for h in hits]

def tool_cross_reference(client, name: str, schema: str = "Person",
                         scopes: list = None, threshold: float = 0.7) -> dict:
    scopes = scopes or ["own_corpus", "watchlists"]
    # Plan-review scope-allowlist: FAIL-CLOSED -- validate EVERY requested scope
    # and raise on any unknown value (do NOT silently drop, unlike the old filter).
    for s in scopes:
        _resolve_scope(s)
    out = {}
    for scope in scopes:
        out[scope] = tool_match(client, scope, name, schema=schema, threshold=threshold)
    return out

def build_server():
    from mcp.server.fastmcp import FastMCP  # lazy
    mcp = FastMCP("magpie-yente")
    client = _make_client()
    @mcp.tool()
    def list_datasets() -> dict:
        """List indexed yente datasets/scopes (read-only)."""
        return tool_list_datasets(client)
    @mcp.tool()
    def search(scope: str, q: str, limit: int = 10) -> dict:
        """Full-text search within a scope ('own_corpus' or 'watchlists')."""
        return tool_search(client, scope, q, limit)
    @mcp.tool()
    def get_entity(entity_id: str) -> dict:
        """Fetch one entity by id (read-only)."""
        return tool_get_entity(client, entity_id)
    @mcp.tool()
    def match(scope: str, name: str, schema: str = "Person", threshold: float = 0.7) -> list:
        """Screen ONE name against a scope; returns capped scored hits."""
        return tool_match(client, scope, name, schema, threshold)
    @mcp.tool()
    def cross_reference(name: str, schema: str = "Person", threshold: float = 0.7) -> dict:
        """Screen ONE name against own_corpus AND watchlists; grouped hits."""
        return tool_cross_reference(client, name, schema=schema, threshold=threshold)
    return mcp

def main():
    build_server().run()

if __name__ == "__main__":
    main()
```

**Tests:** Windows -- the `tool_*` functions with a FAKE client (scope allowlist
rejects an unknown scope; match caps at MAX_HITS; cross_reference fans the
allowlisted scopes; one-entity-per-call body shape; no write path exists).
Import-purity: `import scripts.yente_mcp_server` works WITHOUT mcp installed
(build_server lazy-imports it). LIVE (`@pytest.mark.yente`): spawn
`python scripts/yente_mcp_server.py` as a subprocess against the running compose,
do one MCP stdio handshake + call `list_datasets`, assert it returns magpie_corpus,
then reap (USE HARD READ TIMEOUTS + separate stdout/stderr reader threads -- the
Phase-11 mcp-stdio-deadlock lesson). Keep this smoke minimal.

**Commit** `feat(phase13b): yente_mcp_server -- thin read-only FastMCP (5 tools, capped)`.

---

## Task 5: infra -- compose (crossref profile) + manifests + mcp example

**Files:**
- Modify: `infra/docker-compose.yml` (add `index` + `yente` under the `crossref` profile)
- Create: `infra/yente/magpie-own.yml`
- Create: `infra/yente/magpie-watchlist.yml`
- Create: `.mcp.yente.example.json`
- Test: `tests/test_phase13b_infra.py` (YAML/JSON structure -- Windows, no Docker)

**`infra/docker-compose.yml` -- ADD these services (proven in the smoke; Neo4j
stays the `graph` profile, untouched):**

```yaml
  index:
    image: opensearchproject/opensearch:2.19.5
    profiles: ["crossref"]
    environment:
      - node.name=magpie-index
      - cluster.name=magpie-index
      - discovery.type=single-node
      - bootstrap.memory_lock=true
      - DISABLE_SECURITY_PLUGIN=true
      - DISABLE_INSTALL_DEMO_CONFIG=true
      - "OPENSEARCH_JAVA_OPTS=-Xms1g -Xmx1g"
      - "OPENSEARCH_INITIAL_ADMIN_PASSWORD=${OPENSEARCH_ADMIN_PASSWORD:?set OPENSEARCH_ADMIN_PASSWORD in infra/.env}"
    ulimits:
      memlock: { soft: -1, hard: -1 }
      nofile: { soft: 65536, hard: 65536 }
    ports: ["127.0.0.1:9200:9200"]
    volumes: ["magpie_index_data:/usr/share/opensearch/data"]
    healthcheck:
      test: ["CMD-SHELL", "curl --fail http://localhost:9200/_cluster/health || exit 1"]
      interval: "10s"
      timeout: "10s"
      retries: 30
      start_period: "40s"
    restart: "unless-stopped"
  yente:
    image: ghcr.io/opensanctions/yente:5.4.0
    profiles: ["crossref"]
    depends_on:
      index: { condition: service_healthy }
    environment:
      YENTE_INDEX_TYPE: "opensearch"
      YENTE_INDEX_URL: "http://index:9200"
      YENTE_INDEX_NAME: "yente"
      # The LIVE, version-stamped manifest is rendered into the gitignored data
      # dir (see the manifest-version decision below); the committed infra/yente
      # files are TEMPLATES only and are NOT mounted.
      YENTE_MANIFEST: "/data/manifest.yml"
      YENTE_AUTO_REINDEX: "false"
      YENTE_UPDATE_TOKEN: "${YENTE_UPDATE_TOKEN:?set YENTE_UPDATE_TOKEN in infra/.env}"
    volumes:
      - ./data/magpie_corpus:/data:ro
    ports: ["127.0.0.1:8000:8000"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/healthz"]
      interval: "15s"
      timeout: "10s"
      retries: 30
      start_period: "30s"
    restart: "unless-stopped"
```
Add `magpie_index_data:` under the top-level `volumes:` (next to `magpie_neo4j_data`).
NOTE the single mount `./data/magpie_corpus:/data:ro` -- the operator/skill emits
BOTH `entities.ftm.json` AND the rendered `manifest.yml` there (write_dataset +
render_manifest) BEFORE `up`; the dir is gitignored. The compose comment header
must explain: render dataset+manifest -> `up --profile crossref` -> `exec yente
yente reindex` (no auto-index). To opt INTO watchlists, render the manifest with
`include_watchlist=True` (same `/data/manifest.yml` path; no env change needed).

**MANIFEST-VERSION DECISION (settled -- plan-review manifest-version-branch).**
The SINGLE source of truth for the live manifest is `render_manifest` (Task 1),
which stamps the dataset `version` = the content hash. The skill/CI/emit-helper
render it to `data/magpie_corpus/manifest.yml` (own-corpus by default; watchlist
when opted in) and the compose mounts ONLY that data dir at `/data`, so
`YENTE_MANIFEST=/data/manifest.yml` always points at the version-stamped file. A
changed snapshot => a new content-hash version => the explicit `yente reindex`
rebuilds; an unchanged snapshot => same version => reindex is a no-op. CI exercises
THIS exact path (Task 7's emit helper renders the manifest too).

**`infra/yente/magpie-own.yml`** + **`infra/yente/magpie-watchlist.yml`** are
COMMITTED TEMPLATES ONLY (documentation of the two shapes; NOT mounted, NOT the
live manifest). Each carries a header comment: `# TEMPLATE -- the live manifest is
rendered by entity_yente_dataset.render_manifest into data/magpie_corpus/manifest.yml
with a content-hash version; this file documents the shape, it is not mounted.`
The own template has NO `catalogs:` block; the watchlist template adds the FREE
civic `default` catalog (`https://data.opensanctions.org/datasets/latest/default/
catalog.json`); both set `namespace: true`, `path: /data/entities.ftm.json`.

**`.mcp.yente.example.json`** (copy-paste operator wiring; NOT auto-loaded):
```json
{
  "mcpServers": {
    "magpie-yente": {
      "command": "python",
      "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/yente_mcp_server.py"],
      "env": { "YENTE_MCP_BASE_URL": "http://127.0.0.1:8000" }
    }
  }
}
```

**Tests (Windows, no Docker):** parse `infra/docker-compose.yml` as text/structure
-- index + yente carry `profiles: ["crossref"]`, neo4j still `["graph"]`, ports
are 127.0.0.1-bound, YENTE_AUTO_REINDEX is "false", `YENTE_MANIFEST` is
`/data/manifest.yml` (the rendered live manifest, NOT a committed file), and the
ONLY yente mount is `./data/magpie_corpus:/data:ro`; the committed template
`magpie-own.yml` has NO `catalogs:` line + the TEMPLATE header comment,
`magpie-watchlist.yml` HAS the civic URL + the TEMPLATE header; both set
`namespace: true`; `.mcp.yente.example.json` parses and is NOT referenced by the
real `.mcp.json`. (A lightweight `yaml`-free check is fine -- assert substrings /
load via the stdlib if PyYAML is unavailable offline.)

**Commit** `feat(phase13b): infra -- crossref-profile compose + own/watchlist manifests + mcp example`.

---

## Task 6: detect_tier -- Layer-2 cross-reference capability

**Files:**
- Modify: `scripts/detect_tier.py` (add the "cross-reference entities (Layer 2)" capability off the SAME Docker probe 13a added)
- Modify: `skills/setup/SKILL.md` + `skills/doctor/SKILL.md` (one line each: cross-ref is Layer-2 Docker, points to the WSL2 max_map_count step)
- Test: `tests/test_detect_tier.py` (extend; Windows golden from injected probe dicts)

**Step:** Read the existing `build_capability_map` + the 13a Docker probe in
`detect_tier.py` FIRST (in the main thread, to extract the exact pattern INLINE
for the subagent -- detect_tier may carry non-ASCII). Add a capability keyed by
the user verb "cross-reference entities against watchlists / your corpus (Layer
2)" -> READY when the Docker probe is present, UNAVAILABLE otherwise, with the
same optional_fix routing as entity-graph. Do NOT probe a live yente (side-effect-free).

**Tests:** the new capability is READY when docker present / UNAVAILABLE when
absent (injected probe dicts), mirroring the entity-graph capability tests.

**Commit** `feat(phase13b): detect_tier -- Layer-2 cross-reference capability`.

---

## Task 7: CI crossref job + local re-smoke (the gate)

**Files:**
- Modify: `.github/workflows/ci.yml` (add the `crossref` job)
- (the `yente`-marked tests from Tasks 3/4 are what it runs)

**`crossref` job (mirrors the locally-proven sequence; every compose step gets its
env -- the 13a per-step-env gotcha):**

```yaml
  crossref:
    # Track B (Phase 13b). The ONLY real surface for the yente+OpenSearch edges
    # (invisible on Windows). config -> up --wait -> explicit reindex -> readyz/
    # catalog -> real /match self-hit (attribution survives namespacing) -> yente-mcp
    # process smoke -> teardown. NEVER pulls the CC-BY-NC watchlist (own-corpus only).
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - name: Create venv + install offline base + crossref deps
        run: |
          python -m venv .venv && . .venv/bin/activate
          python -m pip install -U pip
          python -m pip install -r requirements-offline.txt -r requirements-crossref.txt
      - name: Emit a tiny synthetic own-corpus dataset + render the live manifest
        run: . .venv/bin/activate && python -m tests.helpers.emit_smoke_dataset  # writes data/magpie_corpus/entities.ftm.json AND data/magpie_corpus/manifest.yml
      - name: compose config
        env: { OPENSEARCH_ADMIN_PASSWORD: "Magpie!CI#2026", YENTE_UPDATE_TOKEN: "ci-token" }
        run: docker compose -f infra/docker-compose.yml --profile crossref config
      - name: bring up index + yente (wait for health)
        env: { OPENSEARCH_ADMIN_PASSWORD: "Magpie!CI#2026", YENTE_UPDATE_TOKEN: "ci-token" }
        run: docker compose -f infra/docker-compose.yml --profile crossref up -d --wait
      - name: explicit reindex (AUTO_REINDEX=false)
        env: { OPENSEARCH_ADMIN_PASSWORD: "Magpie!CI#2026", YENTE_UPDATE_TOKEN: "ci-token" }
        run: docker compose -f infra/docker-compose.yml --profile crossref exec -T yente yente reindex
      - name: yente live tests (-m yente; attribution + mcp smoke)
        env: { YENTE_BASE_URL: "http://127.0.0.1:8000", YENTE_MCP_BASE_URL: "http://127.0.0.1:8000" }
        run: . .venv/bin/activate && python -m pytest -m yente -rs -q
      - name: teardown
        if: always()
        env: { OPENSEARCH_ADMIN_PASSWORD: "Magpie!CI#2026", YENTE_UPDATE_TOKEN: "ci-token" }
        run: docker compose -f infra/docker-compose.yml --profile crossref down -v
```
Add a small `tests/helpers/emit_smoke_dataset.py` (uses
`entity_yente_dataset.write_dataset` on a tiny built snapshot, THEN
`render_manifest([DatasetEntry(... version=<the write_dataset version>)])` written
to `data/magpie_corpus/manifest.yml`) so CI + a local run share one emitter and
exercise the SAME version-stamped-manifest path the skill uses. The `yente`-marked
tests read `YENTE_BASE_URL` (skip if unset, like the neo4j tests read NEO4J_URI).

**LOCAL RE-SMOKE (do BEFORE pushing -- the load-bearing lesson, on the SHIPPED
artifacts not the scratch harness):** from the repo root, emit the dataset, then
`docker compose -f infra/docker-compose.yml --env-file infra/.env --profile crossref up -d --wait`,
`... exec -T yente yente reindex`, `& .venv\Scripts\python.exe -m pytest -m yente -rs -q`,
then `... --profile crossref down -v`. Confirm green locally, THEN push.

**Commit** `ci(phase13b): crossref job -- live yente+OpenSearch /match + mcp smoke`.

---

## Task 8: skills/entity-crossref/SKILL.md + references/prior-art.md

**Files:**
- Create: `skills/entity-crossref/SKILL.md`
- Create: `skills/entity-crossref/references/prior-art.md`
- Modify: `skills/entity-graph/SKILL.md` -- it currently says cross-ref "is 13b, a
  SEPARATE later PR" (lines ~25-26, ~292-293) but does NOT name the skill; UPDATE
  those one-liners to name the now-existing `entity-crossref` skill (a pointer, not
  a behavior change). Do this MAIN-THREAD (the file carries non-ASCII).
- Test: `tests/test_entity_crossref_skill.py` (skill smoke: frontmatter, the no-Docker-in-journalist-surface guard, the steps reference the right scripts)

**SKILL.md (operator flow):** PRECONDITION (Docker + the crossref profile up,
explicit reindex). The 6-step flow from design section 5: emit dataset + RENDER
the live manifest (write_dataset + render_manifest -> data/magpie_corpus/) -> `up
--profile crossref -d --wait` + `exec yente yente reindex` -> run_crossref ->
report -> Librarian AGGREGATE output (route surfaced PII through redact-output).
WATCHLISTS OPT-IN (render the manifest with include_watchlist=True; CC-BY-NC
documented). yente-mcp wiring via `.mcp.yente.example.json` (operator-wired, never
auto-start). Hits are LEADS for a human, never verdicts. Layer-2/operator-tier;
no Docker in any journalist surface. Write it MAIN-THREAD if any non-ASCII is
needed (it should be ASCII); otherwise a subagent can.

**prior-art.md:** the verified library facts from
`.codex-review/research/phase13b-gateB-validation-2026-06-07.md` (versions, the
env-var surface, the AUTO_REINDEX=false+reindex fact, the /match shape, namespace
behavior, the CC-BY-NC term, the CI verification surface). ASCII.

**Commit** `feat(phase13b): entity-crossref SKILL + prior-art`.

---

## Wrap-up (main thread, after all tasks + reviews)

- Codex impl-review (FILE-SCOPED; never `git diff main...HEAD` -- the 13a context
  blow). Route fix clusters to fix-subagents; confirmatory pass.
- LOCAL re-smoke of the shipped compose + crossref tests (Task 7) BEFORE push.
- Push; gate the merge on green offline + ftm + graph + compose + **crossref**.
- Regenerate MANIFEST.md to budget (keep tests/test_manifest_budget.py green).
- PR; `gh pr merge --merge`. Update project memory.

## Task dependency / parallelism notes (for SDD)
- Order: 0 -> {1, 2} -> 3 (needs 2) -> 4 (needs 2,3) -> 5 -> 6 -> 7 (needs 5 + the
  yente tests) -> 8. Tasks 1 and 2 are independent (parallelizable, worktree-isolated
  if concurrent); the rest commit sequentially (avoid index races -- the Phase-8
  lesson). detect_tier (6) is independent of the yente modules.

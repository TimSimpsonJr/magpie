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

    def _check_size(self, resp):
        if len(resp.content) > self.max_bytes:
            raise ValueError("yente response exceeds max_bytes")
        return resp.json()

    def _get_json(self, path, params=None):
        with self._client() as c:
            r = c.get(path, params=params); r.raise_for_status()
            return self._check_size(r)

    def readyz(self) -> bool:
        try:
            with self._client() as c:
                return c.get("/readyz").status_code == 200
        except Exception:
            return False

    def catalog(self) -> dict:
        return self._get_json("/catalog")

    def search(self, scope, q, *, limit=10):
        return self._get_json("/search/%s" % scope, {"q": q, "limit": limit})

    def get_entity(self, entity_id):
        return self._get_json("/entities/%s" % urllib.parse.quote(entity_id, safe=""))

    def match(self, scope, body, *, algorithm="best", threshold=0.7, limit=5):
        with self._client() as c:
            r = c.post("/match/%s" % scope,
                       params={"algorithm": algorithm, "threshold": threshold, "limit": limit},
                       json=body)
            r.raise_for_status()
            return self._check_size(r)

def run_crossref(snapshot, scopes, client, *, threshold=0.7, cap=25, algorithm="best",
                 batch=100, generated_at=None, index_provenance=None) -> dict:
    """Batch the snapshot entities into /match calls per scope, parse + group,
    assemble the report. `scopes` is a list of friendly names from
    entity_crossref.SCOPES; `client` is injected (a real YenteClient live, or a
    fake in tests).

    The caller (skill/CI) supplies the FULL index_provenance it alone knows --
    {manifest_hash, dataset_file_hashes, yente_image_tag, opensearch_image_tag}
    (run_crossref cannot invent file hashes or image tags). run_crossref AUGMENTS
    it with the live /catalog per-dataset metadata and passes the merged dict to
    build_crossref_report."""
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
    try:
        catalog = client.catalog()
    except Exception:
        pass
    prov["catalog"] = [{k: d.get(k) for k in ("name", "version", "updated_at", "last_export", "index_current")}
                       for d in (catalog.get("datasets") or [])]
    return xref.build_crossref_report(hits_by_scope, index_provenance=prov,
                                      threshold=threshold, algorithm=algorithm, generated_at=generated_at)

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

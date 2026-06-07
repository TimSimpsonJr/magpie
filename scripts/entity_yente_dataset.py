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

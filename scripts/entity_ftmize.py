"""FtM layer: reviewed intermediate -> FollowTheMoney bundle (Linux/CI only).

This is the ONLY followthemoney importer in the repo. followthemoney does NOT
install on Windows (PyICU/ICU has no Windows wheel), so importing this module
fails on the Windows dev venv -- by design. It runs in the CI `ftm` job (Ubuntu)
and Phase-13 Docker, and is reused by Phase 13.

It consumes the followthemoney-FREE reviewed INTERMEDIATE produced by
`scripts.entity_extract.build_intermediate` (a plain dict) and:
  - to_ftm(intermediate) -> list of FtM proxies (ids PRESERVED from the
    intermediate -- Phase 12 owns the ids; ftmize never re-mints or re-merges).
  - write_bundle(intermediate, out_dir, name=None) -> writes the three Phase-13
    hand-off files and returns their paths.
  - assert_phase13_consumable(bundle_dir, name) -> pure file/JSON contract check
    Phase 13 imports (no followthemoney needed for the check itself).
"""
from __future__ import annotations

import json
import pathlib

from followthemoney import model


# Edge endpoint property names, keyed by FtM edge schema (fixed FtM standard).
_EDGE_PROPS = {
    "Employment": ("employee", "employer"),
    "Membership": ("member", "organization"),
    "Directorship": ("director", "organization"),
    "Ownership": ("owner", "asset"),
    "Representation": ("agent", "client"),
    "Family": ("person", "relative"),
    "Associate": ("person", "associate"),
    "ContractAward": ("authority", "supplier"),
    "UnknownLink": ("subject", "object"),
}


def _ftm_version() -> str:
    """Best-effort followthemoney version string (empty if unavailable)."""
    try:
        import followthemoney

        return getattr(followthemoney, "__version__", "") or ""
    except Exception:
        return ""


def to_ftm(intermediate: dict) -> list:
    """Map a reviewed intermediate bundle to a list of FtM proxies.

    Nodes first, then edges. The intermediate's stable ids are PRESERVED on the
    proxies (entity.id) -- Phase 12 owns the ids; ftmize never re-mints them.
    """
    proxies = []

    for node in intermediate.get("nodes", []):
        e = model.make_entity(node["schema"])
        e.id = node["id"]
        e.add("name", node["name"])
        proxies.append(e)

    for edge in intermediate.get("edges", []):
        e = model.make_entity(edge["schema"])
        e.id = edge["id"]
        src_prop, tgt_prop = _EDGE_PROPS[edge["schema"]]
        e.add(src_prop, edge["head_id"])
        e.add(tgt_prop, edge["tail_id"])
        if edge.get("role"):
            role_prop = "relationship" if edge["schema"] in ("Family", "Associate") else "role"
            # quiet: silently skip if this schema lacks the prop
            e.add(role_prop, edge["role"], quiet=True)
        proxies.append(e)

    return proxies


def write_bundle(intermediate: dict, out_dir, name: str = None) -> dict:
    """Write the three Phase-13 hand-off files and return their paths.

    Files (design section 7):
      <name>.entities.ftm.json -- newline-delimited json, one proxy.to_dict() per
        line (nodes then edges).
      <name>.provenance.jsonl  -- newline-delimited json, one row per intermediate
        provenance entry.
      <name>.manifest.json     -- the bundle header.

    Returns {"entities": <path>, "provenance": <path>, "manifest": <path>}.
    """
    if name is None:
        name = intermediate["dataset_namespace"]

    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    nodes = intermediate.get("nodes", [])
    edges = intermediate.get("edges", [])

    proxies = to_ftm(intermediate)
    entities_path = out / (name + ".entities.ftm.json")
    with entities_path.open("w", encoding="utf-8") as fh:
        for proxy in proxies:
            fh.write(json.dumps(proxy.to_dict()))
            fh.write("\n")

    provenance_path = out / (name + ".provenance.jsonl")
    with provenance_path.open("w", encoding="utf-8") as fh:
        for row in intermediate.get("provenance", []):
            fh.write(json.dumps(row))
            fh.write("\n")

    manifest = {
        "schema_version": intermediate["schema_version"],
        "dataset_namespace": name,
        "source_doc_ids": intermediate.get("source_doc_ids", []),
        "entity_count": len(nodes),
        "edge_count": len(edges),
        "counts": intermediate.get("counts", {}),
        "ftm_version": _ftm_version(),
    }
    manifest_path = out / (name + ".manifest.json")
    with manifest_path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh)

    return {
        "entities": str(entities_path),
        "provenance": str(provenance_path),
        "manifest": str(manifest_path),
    }


def assert_phase13_consumable(bundle_dir, name: str) -> None:
    """Assert a written bundle satisfies the Phase-13 input contract.

    Pure file/JSON checks (no followthemoney needed for the check itself), so
    Phase 13 can call it cheaply. Raises AssertionError with a clear message on
    any violation.
    """
    out = pathlib.Path(bundle_dir)
    entities_path = out / (name + ".entities.ftm.json")
    provenance_path = out / (name + ".provenance.jsonl")
    manifest_path = out / (name + ".manifest.json")

    assert entities_path.exists(), "missing entities file: %s" % entities_path
    assert provenance_path.exists(), "missing provenance file: %s" % provenance_path
    assert manifest_path.exists(), "missing manifest file: %s" % manifest_path

    with manifest_path.open("r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    assert manifest.get("schema_version"), (
        "manifest schema_version is missing or empty: %r" % manifest.get("schema_version")
    )
    assert isinstance(manifest.get("counts"), dict), (
        "manifest counts must be a dict, got: %r" % type(manifest.get("counts"))
    )

    entity_count = manifest.get("entity_count", 0)
    edge_count = manifest.get("edge_count", 0)
    expected_lines = entity_count + edge_count

    with entities_path.open("r", encoding="utf-8") as fh:
        non_empty = [ln for ln in fh.read().splitlines() if ln.strip()]
    assert len(non_empty) == expected_lines, (
        "entities file has %d non-empty lines, expected %d (entity_count %d + edge_count %d)"
        % (len(non_empty), expected_lines, entity_count, edge_count)
    )

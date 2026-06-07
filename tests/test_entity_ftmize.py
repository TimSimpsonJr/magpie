"""Contract tests for the FtM layer (scripts/entity_ftmize.py).

ALL tests here are `ftm`-marked and SKIP on Windows: followthemoney does NOT
install there (PyICU/ICU has no Windows wheel). Their CORRECTNESS is verified in
the CI `ftm` job (Ubuntu). The module must still IMPORT cleanly during Windows
pytest collection, so nothing followthemoney-dependent is imported at module top
-- every test imports what it needs inside its own body.

The `pytestmark = ftm` marker means the offline subset (run with
`-m "not ... and not ftm"`) DESELECTS these (they do not even show as skips). The
`@ftm` skipif on each test makes a hypothetical `-m ftm` run on Windows SKIP
cleanly (followthemoney absent) with no collection error.
"""
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.ftm

ftm = pytest.mark.skipif(
    importlib.util.find_spec("followthemoney") is None,
    reason="followthemoney not installed (Linux/CI only)",
)

FIXTURE = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "reviewed_intermediate_sample"
    / "intermediate.json"
)

# The two same-name (John Smith) different-doc Person ids from the fixture.
# Phase 12 deliberately keeps cross-doc homonyms DISTINCT so Phase-13
# nomenklatura xref sees them as resolution candidates.
ID_A = "node_js_doc1_0a1b2c3d4e5f60718293a4b5c6d7e8f900112233"
ID_B = "node_js_doc2_99887766554433221100ffeeddccbbaa0a1b2c3d"


def _load_intermediate() -> dict:
    with FIXTURE.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@ftm
def test_to_ftm_schema_validity():
    import scripts.entity_ftmize as ftmize
    from followthemoney import model

    intermediate = _load_intermediate()
    proxies = ftmize.to_ftm(intermediate)

    # Every proxy round-trips cleanly via model.get_proxy(to_dict()).
    for p in proxies:
        rt = model.get_proxy(p.to_dict())
        assert rt.id == p.id
        assert rt.schema == p.schema

    # Partition into node proxies (have "name") and edge proxies (an FtM edge schema).
    node_ids = {n["id"] for n in intermediate["nodes"]}
    edge_schemas = set(ftmize._EDGE_PROPS)

    # No dangling edge endpoint: every edge proxy's endpoint id values are node ids.
    for p in proxies:
        if p.schema.name in edge_schemas:
            src_prop, tgt_prop = ftmize._EDGE_PROPS[p.schema.name]
            endpoint_ids = list(p.get(src_prop)) + list(p.get(tgt_prop))
            assert endpoint_ids, "edge %s has no endpoints" % p.id
            for eid in endpoint_ids:
                assert eid in node_ids, (
                    "dangling edge endpoint %r on edge %s" % (eid, p.id)
                )

    # The two same-name Person ids are BOTH present and DISTINCT.
    proxy_ids = {p.id for p in proxies}
    assert ID_A in proxy_ids
    assert ID_B in proxy_ids
    assert ID_A != ID_B


@ftm
def test_write_bundle_emits_three_files(tmp_path):
    import scripts.entity_ftmize as ftmize

    intermediate = _load_intermediate()
    name = intermediate["dataset_namespace"]
    paths = ftmize.write_bundle(intermediate, tmp_path)

    entities_path = pathlib.Path(paths["entities"])
    provenance_path = pathlib.Path(paths["provenance"])
    manifest_path = pathlib.Path(paths["manifest"])

    assert entities_path.exists()
    assert provenance_path.exists()
    assert manifest_path.exists()

    entity_count = len(intermediate["nodes"])
    edge_count = len(intermediate["edges"])
    lines = [ln for ln in entities_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == entity_count + edge_count
    for ln in lines:
        obj = json.loads(ln)
        assert "id" in obj
        assert "schema" in obj

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == intermediate["schema_version"]

    # The Phase-13 contract helper must not raise on a well-formed bundle.
    ftmize.assert_phase13_consumable(tmp_path, name)


@ftm
def test_ftm_export_cypher(tmp_path):
    import scripts.entity_ftmize as ftmize

    intermediate = _load_intermediate()
    paths = ftmize.write_bundle(intermediate, tmp_path)
    entities_text = pathlib.Path(paths["entities"]).read_text(encoding="utf-8")

    ftm_cli = shutil.which("ftm")
    if ftm_cli is None:
        pytest.skip("ftm console script not on PATH")

    proc = subprocess.run(
        [ftm_cli, "export-cypher"],
        input=entities_text,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, "ftm export-cypher failed: %s" % proc.stderr
    assert proc.stdout.strip(), "ftm export-cypher produced no output"

    # The Cypher should reference at least one node id from the bundle.
    node_ids = [n["id"] for n in intermediate["nodes"]]
    assert any(nid in proc.stdout for nid in node_ids), (
        "no bundle node id found in the emitted Cypher"
    )


@ftm
def test_ftm_export_neo4j_bulk(tmp_path):
    import scripts.entity_ftmize as ftmize

    intermediate = _load_intermediate()
    paths = ftmize.write_bundle(intermediate, tmp_path)
    entities_text = pathlib.Path(paths["entities"]).read_text(encoding="utf-8")

    ftm_cli = shutil.which("ftm")
    if ftm_cli is None:
        pytest.skip("ftm console script not on PATH")

    out_dir = tmp_path / "neo4j_bulk"
    out_dir.mkdir()
    proc = subprocess.run(
        [ftm_cli, "export-neo4j-bulk", "-o", str(out_dir)],
        input=entities_text,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, "ftm export-neo4j-bulk failed: %s" % proc.stderr

    produced = [p for p in out_dir.iterdir() if p.is_file()]
    assert produced, "ftm export-neo4j-bulk produced no files in %s" % out_dir


@ftm
def test_nomenklatura_xref_candidate_smoke(tmp_path):
    import scripts.entity_ftmize as ftmize

    try:
        from followthemoney import Dataset, StatementEntity as Entity
        from nomenklatura.resolver import Resolver
        from nomenklatura.store import load_entity_file_store
        from nomenklatura.xref import xref
    except ImportError as exc:
        pytest.skip("nomenklatura not installed (optional CI dep): %s" % exc)

    intermediate = _load_intermediate()
    paths = ftmize.write_bundle(intermediate, tmp_path)
    entities_path = pathlib.Path(paths["entities"])

    resolver = Resolver[Entity].make_default()
    resolver.begin()
    try:
        dstore = load_entity_file_store(entities_path, resolver)
        index_dir = tmp_path / "xref-index"
        index_dir.mkdir()
        xref(resolver, dstore, index_dir)
        candidates = list(resolver.get_candidates(limit=50))

        assert candidates, "xref produced no candidate pairs"
        # The two same-name-different-doc Person ids should surface as a pair.
        pair_ids = {frozenset((left, right)) for (left, right, score) in candidates}
        assert frozenset((ID_A, ID_B)) in pair_ids, (
            "the two same-name Person ids did not surface as an xref candidate pair"
        )
    finally:
        resolver.close()
        try:
            resolver._table.drop(resolver._engine, checkfirst=True)
        except Exception:
            pass

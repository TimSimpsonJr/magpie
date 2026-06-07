import json, pathlib
REPO = pathlib.Path(__file__).resolve().parent.parent

def _compose():
    return (REPO / "infra/docker-compose.yml").read_text(encoding="utf-8")

def test_index_and_yente_under_crossref_profile():
    c = _compose()
    # crossref profile present for index + yente; graph profile still present for neo4j.
    assert 'profiles: ["crossref"]' in c
    assert 'profiles: ["graph"]' in c
    assert "opensearchproject/opensearch:2.19.5" in c
    assert "ghcr.io/opensanctions/yente:5.4.0" in c

def test_yente_env_determinism_and_manifest_path():
    c = _compose()
    assert 'YENTE_AUTO_REINDEX: "false"' in c
    assert 'YENTE_MANIFEST: "/data/manifest.yml"' in c
    assert 'YENTE_INDEX_TYPE: "opensearch"' in c
    assert "http://index:9200" in c

def test_ports_localhost_bound():
    c = _compose()
    assert "127.0.0.1:9200:9200" in c
    assert "127.0.0.1:8000:8000" in c

def test_yente_only_mounts_data_dir():
    c = _compose()
    # ../ because relative bind-mount paths resolve against the compose file's
    # dir (infra/); ../data/magpie_corpus is the repo-root data dir the emitter writes.
    assert "../data/magpie_corpus:/data:ro" in c
    # the committed infra/yente templates are NOT mounted into the container:
    assert "/app/manifests" not in c

def test_index_volume_added():
    c = _compose()
    assert "magpie_index_data" in c

def test_own_template_no_catalogs_and_template_header():
    t = (REPO / "infra/yente/magpie-own.yml").read_text(encoding="utf-8")
    assert "catalogs:" not in t
    assert "TEMPLATE" in t
    assert "namespace: true" in t

def test_watchlist_template_has_civic_catalog_and_header():
    t = (REPO / "infra/yente/magpie-watchlist.yml").read_text(encoding="utf-8")
    assert "data.opensanctions.org" in t
    assert "scope: default" in t
    assert "TEMPLATE" in t
    assert "CC-BY-NC" in t

def test_mcp_example_parses_and_not_in_real_mcp_json():
    ex = json.loads((REPO / ".mcp.yente.example.json").read_text(encoding="utf-8"))
    assert "magpie-yente" in ex["mcpServers"]
    real = json.loads((REPO / ".mcp.json").read_text(encoding="utf-8"))
    assert "magpie-yente" not in real.get("mcpServers", {})

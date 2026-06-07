"""ASCII only. LIVE yente cross-ref tests (-m yente). They need a running yente
(the crossref compose up + an explicit `yente reindex`) reachable at YENTE_BASE_URL.
SKIP when YENTE_BASE_URL is unset (like the neo4j tests skip on NEO4J_URI). Run in
the CI crossref job only; deselected from the offline suite by the marker."""
from __future__ import annotations
import os
import pytest

pytestmark = pytest.mark.yente

@pytest.fixture
def yente_base_url():
    url = os.environ.get("YENTE_BASE_URL")
    if not url:
        pytest.skip("YENTE_BASE_URL not set (live yente required)")
    return url

def test_live_match_self_hit_attribution(yente_base_url):
    """The D3 contract + Codex crossref-smoke fold: the self-hit comes back under
    the ORIGINATING query key (= our canonical_id) AND grouped under magpie_corpus,
    regardless of the namespaced result id."""
    from scripts.entity_yente_client import YenteClient
    from scripts.entity_crossref import build_match_body, parse_match_response
    from tests.helpers.emit_smoke_dataset import build_smoke_snapshot, SMOKE_CANONICAL_ID

    snap = build_smoke_snapshot()
    client = YenteClient(yente_base_url)
    resp = client.match("magpie_corpus", build_match_body(snap["entities"]), threshold=0.7)
    hits = parse_match_response(resp, scope="own_corpus", threshold=0.7, cap=25)

    self_hits = [h for h in hits if h.query_canonical_id == SMOKE_CANONICAL_ID]
    assert self_hits, "no hit attributed to the seeded canonical_id %s" % SMOKE_CANONICAL_ID
    assert any("magpie_corpus" in h.datasets for h in self_hits), \
        "self-hit not grouped under magpie_corpus"
    # attribution survives namespacing: the result id is namespaced (!= our id),
    # but we still attribute via the query key.
    assert all(h.query_canonical_id == SMOKE_CANONICAL_ID for h in self_hits)

def test_live_catalog_lists_only_own_corpus(yente_base_url):
    """The default manifest pulls ZERO external data -- /catalog shows only
    magpie_corpus (no watchlist 'default')."""
    from scripts.entity_yente_client import YenteClient
    client = YenteClient(yente_base_url)
    names = [d.get("name") for d in client.catalog().get("datasets", [])]
    assert "magpie_corpus" in names
    assert "default" not in names

def test_live_mcp_server_wiring_and_list_datasets(yente_base_url, monkeypatch):
    """yente-mcp process smoke: the FastMCP server BUILDS (lazy mcp import + the 5
    @mcp.tool registrations) and a read-only tool works against the live yente."""
    pytest.importorskip("mcp")
    monkeypatch.setenv("YENTE_MCP_BASE_URL", yente_base_url)
    from scripts.yente_mcp_server import build_server, tool_list_datasets, _make_client

    server = build_server()  # constructs FastMCP + registers tools (the wiring)
    assert server is not None
    cat = tool_list_datasets(_make_client())
    assert "magpie_corpus" in [d.get("name") for d in cat.get("datasets", [])]

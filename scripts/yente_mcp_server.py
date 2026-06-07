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
               threshold: float = 0.7) -> list:
    # ONE entity per call (effective MAX_BATCH=1; no bulk pass-through).
    body = {"queries": {"q": {"schema": schema, "properties": {"name": [name]}}}}
    resp = client.match(_resolve_scope(scope), body, threshold=threshold, limit=MAX_HITS)
    import dataclasses
    hits = xref.parse_match_response(resp, scope=scope, threshold=threshold, cap=MAX_HITS)
    return [dataclasses.asdict(h) for h in hits]

def tool_cross_reference(client, name: str, schema: str = "Person",
                         scopes: list = None, threshold: float = 0.7) -> dict:
    scopes = scopes or ["own_corpus", "watchlists"]
    # FAIL-CLOSED: validate EVERY requested scope and raise on any unknown value
    # (do NOT silently drop, unlike a filter).
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

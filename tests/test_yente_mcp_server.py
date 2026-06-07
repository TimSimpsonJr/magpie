"""ASCII only. Windows-golden tests for the thin read-only yente-mcp server.

Exercises the plain tool_* LOGIC functions with a FakeClient (no network, no
mcp installed) plus one subprocess import-purity test proving the module
imports without `mcp` AND without `httpx` in sys.modules (both lazy-imported).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import yente_mcp_server as srv

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    """Records calls and returns canned data; mirrors the YenteClient surface
    the tool_* functions touch. No loopback/network behavior."""

    def __init__(self):
        self.calls = []

    def catalog(self) -> dict:
        self.calls.append(("catalog",))
        return {"datasets": [{"name": "magpie_corpus", "title": "Magpie Corpus"}]}

    def search(self, scope, q, *, limit) -> dict:
        self.calls.append(("search", scope, q, limit))
        return {"scope": scope, "q": q, "limit": limit}

    def get_entity(self, entity_id) -> dict:
        self.calls.append(("get_entity", entity_id))
        return {"id": entity_id}

    def match(self, scope, body, *, threshold, limit) -> dict:
        self.calls.append(("match", scope, body, threshold, limit))
        return {
            "responses": {
                "q": {
                    "results": [
                        {
                            "id": "x.1",
                            "caption": "X",
                            "schema": "Person",
                            "datasets": ["magpie_corpus"],
                            "score": 1.0,
                            "match": True,
                        }
                    ]
                }
            }
        }


# ---------------------------------------------------------------------------
# 1. _resolve_scope
# ---------------------------------------------------------------------------

def test_resolve_scope_known():
    assert srv._resolve_scope("own_corpus") == "magpie_corpus"
    assert srv._resolve_scope("watchlists") == "default"


def test_resolve_scope_unknown_raises():
    with pytest.raises(ValueError):
        srv._resolve_scope("evil_scope")


def test_resolve_scope_rejects_arbitrary_dataset_path():
    # A raw dataset name (e.g. trying to address a dataset directly) is NOT a
    # scope key and must be rejected -- no pass-through to arbitrary datasets.
    with pytest.raises(ValueError):
        srv._resolve_scope("magpie_corpus")


# ---------------------------------------------------------------------------
# 2. tool_search
# ---------------------------------------------------------------------------

def test_tool_search_resolves_scope():
    c = FakeClient()
    out = srv.tool_search(c, "own_corpus", "alice", limit=5)
    # client.search must receive the RESOLVED dataset, not the scope key.
    assert c.calls == [("search", "magpie_corpus", "alice", 5)]
    assert out == {"scope": "magpie_corpus", "q": "alice", "limit": 5}


def test_tool_search_caps_limit():
    c = FakeClient()
    srv.tool_search(c, "watchlists", "bob", limit=999)
    assert c.calls == [("search", "default", "bob", srv.MAX_HITS)]
    assert srv.MAX_HITS == 25


def test_tool_search_unknown_scope_raises():
    c = FakeClient()
    with pytest.raises(ValueError):
        srv.tool_search(c, "evil", "bob", limit=5)


# ---------------------------------------------------------------------------
# 3. tool_match
# ---------------------------------------------------------------------------

def test_tool_match_builds_single_query_body():
    c = FakeClient()
    srv.tool_match(c, "own_corpus", "Alice Smith")
    assert len(c.calls) == 1
    kind, scope, body, threshold, limit = c.calls[0]
    assert kind == "match"
    assert scope == "magpie_corpus"
    assert body == {
        "queries": {"q": {"schema": "Person", "properties": {"name": ["Alice Smith"]}}}
    }
    # capped limit handed to the client
    assert limit == srv.MAX_HITS


def test_tool_match_returns_list_of_dicts():
    c = FakeClient()
    out = srv.tool_match(c, "own_corpus", "Alice Smith")
    assert isinstance(out, list)
    assert all(isinstance(h, dict) for h in out)
    assert out  # the canned /match response yields one hit
    # asdict(CrossRefHit) -> a plain dict; the result id "x.1" must surface in
    # one of the dataclass fields (field name owned by entity_crossref).
    assert any("x.1" == v for v in out[0].values())


def test_tool_match_unknown_scope_raises():
    c = FakeClient()
    with pytest.raises(ValueError):
        srv.tool_match(c, "evil", "Alice Smith")


# ---------------------------------------------------------------------------
# 4. tool_cross_reference (FAIL-CLOSED on scopes)
# ---------------------------------------------------------------------------

def test_tool_cross_reference_default_scopes():
    c = FakeClient()
    out = srv.tool_cross_reference(c, "Alice Smith")
    assert set(out.keys()) == {"own_corpus", "watchlists"}
    assert isinstance(out["own_corpus"], list)
    assert isinstance(out["watchlists"], list)
    # two scopes -> two client.match calls
    assert sum(1 for call in c.calls if call[0] == "match") == 2


def test_tool_cross_reference_fail_closed_on_unknown_scope():
    c = FakeClient()
    with pytest.raises(ValueError):
        srv.tool_cross_reference(c, "Alice Smith", scopes=["own_corpus", "evil"])
    # FAIL-CLOSED: it must NOT have silently dropped "evil" and queried the
    # valid scope -- validation happens up front, before any client call.
    assert c.calls == []


# ---------------------------------------------------------------------------
# 5. tool_get_entity / tool_list_datasets pass-through
# ---------------------------------------------------------------------------

def test_tool_get_entity_passthrough():
    c = FakeClient()
    out = srv.tool_get_entity(c, "ent.42")
    assert out == {"id": "ent.42"}
    assert c.calls == [("get_entity", "ent.42")]


def test_tool_list_datasets_passthrough():
    c = FakeClient()
    out = srv.tool_list_datasets(c)
    assert out == {"datasets": [{"name": "magpie_corpus", "title": "Magpie Corpus"}]}
    assert c.calls == [("catalog",)]


# ---------------------------------------------------------------------------
# 6. No-write surface
# ---------------------------------------------------------------------------

def test_no_write_surface_callables():
    forbidden = ("update", "reindex", "write", "delete")
    bad = [
        name
        for name in dir(srv)
        if callable(getattr(srv, name))
        and any(tok in name.lower() for tok in forbidden)
    ]
    assert bad == [], "unexpected write-ish callable(s): %r" % bad


def test_no_write_surface_source_text():
    # The docstring legitimately *names* "reindex"/"write" to announce their
    # ABSENCE ("NO write/reindex tool"), so we ban DEFINITIONS, not substrings.
    src = Path(srv.__file__).read_text(encoding="utf-8")
    for tok in ("reindex", "update", "write", "delete"):
        assert ("def tool_%s" % tok) not in src
        assert ("def %s" % tok) not in src  # no registered write-ish mcp tool


# ---------------------------------------------------------------------------
# 7. Import purity (subprocess): no mcp, no httpx in sys.modules
# ---------------------------------------------------------------------------

def test_import_purity_no_mcp_no_httpx():
    code = (
        "import sys\n"
        "import scripts.yente_mcp_server as m\n"
        "bad=[x for x in ('mcp','httpx') if x in sys.modules]\n"
        "assert not bad, bad\n"
        "print('PURE_OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "PURE_OK" in proc.stdout

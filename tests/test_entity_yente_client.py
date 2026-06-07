"""ASCII only. Offline tests for scripts.entity_yente_client.

These cover the PURE, Windows-testable surface: the loopback guard, the
YenteClient constructor (URL validation + trailing-slash strip), and the
run_crossref orchestration driven by a FAKE client (no httpx). A final
subprocess test proves the module imports WITHOUT httpx in sys.modules.

No live (-m yente) tests here -- those arrive with Task 7's CI wiring.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

import scripts.entity_yente_client as yc


# ---------------------------------------------------------------------------
# 1. is_loopback_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000",
        "http://localhost:9200",
        "http://[::1]:8000",
    ],
)
def test_is_loopback_url_true(url):
    assert yc.is_loopback_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://10.0.0.5:8000",
        "http://169.254.1.1",
    ],
)
def test_is_loopback_url_false(url):
    assert yc.is_loopback_url(url) is False


# ---------------------------------------------------------------------------
# 2. YenteClient.__init__
# ---------------------------------------------------------------------------

def test_init_rejects_non_loopback_by_default():
    with pytest.raises(ValueError):
        yc.YenteClient("http://example.com:8000")


def test_init_allows_non_loopback_when_allow_remote():
    client = yc.YenteClient("http://example.com:8000", allow_remote=True)
    assert client.base_url == "http://example.com:8000"


def test_init_strips_trailing_slash():
    client = yc.YenteClient("http://127.0.0.1:8000/")
    assert client.base_url == "http://127.0.0.1:8000"


def test_init_loopback_default_ok():
    client = yc.YenteClient()
    assert client.base_url == "http://127.0.0.1:8000"


# ---------------------------------------------------------------------------
# 3. run_crossref with a FAKE client
# ---------------------------------------------------------------------------

class FakeClient:
    """Records .match calls and returns a canned /match response. .catalog()
    returns one dataset. Mirrors the YenteClient surface run_crossref uses."""

    def __init__(self):
        self.match_calls = []  # list of (scope, body, kw)

    def match(self, scope, body, **kw):
        self.match_calls.append((scope, body, kw))
        # Build one self-hit per query id in the body: each query echoes a
        # result that points back at its own canonical id (datasets carry the
        # data-scope name 'magpie_corpus', score 1.0, match True).
        results = {}
        for cid in body["queries"]:
            results[cid] = {
                "results": [
                    {
                        "id": cid,
                        "caption": "self %s" % cid,
                        "score": 1.0,
                        "match": True,
                        "datasets": ["magpie_corpus"],
                        "schema": "Person",
                    }
                ]
            }
        return {"responses": results}

    def catalog(self):
        return {
            "datasets": [
                {
                    "name": "magpie_corpus",
                    "version": "v1",
                    "updated_at": None,
                    "last_export": None,
                    "index_current": True,
                }
            ]
        }


class RaisingCatalogClient(FakeClient):
    def catalog(self):
        raise RuntimeError("catalog boom")


def _snapshot(n):
    ents = []
    for i in range(n):
        cid = "Q%d" % i
        ents.append(
            {
                "canonical_id": cid,
                "schema": "Person",
                "caption": "Person %d" % i,
                "properties": {"name": ["Person %d" % i]},
            }
        )
    return {"entities": ents}


def test_run_crossref_batches_per_scope():
    fake = FakeClient()
    snap = _snapshot(3)
    yc.run_crossref(snap, ["own_corpus"], fake, batch=2)
    # 3 entities, batch=2 -> two calls (2 + 1) for the single scope.
    assert len(fake.match_calls) == 2
    # Each call targets the data-scope name, not the friendly name.
    assert all(call[0] == "magpie_corpus" for call in fake.match_calls)
    # run_crossref passes limit=cap (default 25) so yente returns up to `cap`
    # candidates per query (else the client default of 5 would silently govern).
    assert all(call[2].get("limit") == 25 for call in fake.match_calls)


def test_run_crossref_passes_explicit_cap_as_limit():
    fake = FakeClient()
    yc.run_crossref(_snapshot(1), ["own_corpus"], fake, batch=100, cap=10)
    assert fake.match_calls[0][2].get("limit") == 10


def test_run_crossref_report_has_requested_scopes_and_hits():
    fake = FakeClient()
    snap = _snapshot(2)
    report = yc.run_crossref(snap, ["own_corpus"], fake, batch=100)
    assert "own_corpus" in report["scopes"]
    # The fake returns a self-hit keyed by each canonical id; the parsed hits
    # surface those canonical ids as the response key (query_canonical_id).
    hits = report["scopes"]["own_corpus"]
    keyed = {h["query_canonical_id"] for h in hits}
    assert keyed == {"Q0", "Q1"}


def test_run_crossref_provenance_passthrough_and_catalog():
    fake = FakeClient()
    snap = _snapshot(2)
    prov_in = {"yente_image_tag": "ghcr.io/opensanctions/yente:5.4.0"}
    report = yc.run_crossref(
        snap, ["own_corpus"], fake, batch=100, index_provenance=prov_in
    )
    prov = report["index_provenance"]
    # Caller-supplied provenance survives.
    assert prov["yente_image_tag"] == "ghcr.io/opensanctions/yente:5.4.0"
    # And run_crossref augmented it with per-dataset catalog metadata.
    assert prov["catalog"] == [
        {
            "name": "magpie_corpus",
            "version": "v1",
            "updated_at": None,
            "last_export": None,
            "index_current": True,
        }
    ]


def test_run_crossref_catalog_failure_swallowed():
    fake = RaisingCatalogClient()
    snap = _snapshot(2)
    report = yc.run_crossref(snap, ["own_corpus"], fake, batch=100)
    # The try/except around client.catalog() yields an empty catalog list, but
    # still produces a report.
    assert report["index_provenance"]["catalog"] == []


# ---------------------------------------------------------------------------
# 4. Import purity: the module imports WITHOUT httpx in sys.modules.
# ---------------------------------------------------------------------------

def test_module_imports_without_httpx():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "import scripts.entity_yente_client as m\n"
        "bad=[x for x in ('httpx',) if x in sys.modules]\n"
        "assert not bad, bad\n"
        "print('PURE_OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "PURE_OK" in proc.stdout

"""mcp-sqlite served-DB START smoke.

Launch the bundled read-only MCP server (the one .mcp.json wires for the
dataset-analyze skill) via uvx, perform the MCP stdio handshake, and confirm it
advertises the ds_-prefixed canned-query tools -- proving the served-DB path
STARTS, LOADS the metadata, and SERVES, not merely that a process stayed up.

Skipped when uvx is absent (so it is safe locally); marked @pytest.mark.mcp. A
threaded reader with hard timeouts means a non-responsive server FAILS this test
rather than hanging it.
"""
import json
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from scripts.build_dataset_db import build_dataset_db

_REPO = Path(__file__).resolve().parents[1]
_YML = _REPO / "skills" / "dataset-analyze" / "canned_queries.yml"

pytestmark = [
    pytest.mark.mcp,
    pytest.mark.skipif(shutil.which("uvx") is None, reason="uvx not on PATH"),
]


def _pump(stream, q):
    """Feed every stdout line onto the queue; a final None marks EOF."""
    for line in stream:
        q.put(line)
    q.put(None)


def _drain(stream, sink):
    """Drain stderr into a list so its pipe buffer never fills and blocks the child.

    Kept SEPARATE from stdout (never merged via stderr=STDOUT) so the stdout queue
    stays pure JSON-RPC and json.loads never chokes on interleaved stderr text.
    """
    for line in stream:
        sink.append(line)


def _read_until_id(q, want_id, timeout):
    """Return the JSON-RPC message whose id == want_id, skipping notifications.

    Raises AssertionError on timeout or premature EOF, so the test never hangs.
    """
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("timed out waiting for MCP response id=%r" % (want_id,))
        try:
            line = q.get(timeout=remaining)
        except queue.Empty:
            raise AssertionError("timed out waiting for MCP response id=%r" % (want_id,))
        if line is None:
            raise AssertionError("mcp-sqlite closed stdout before responding")
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if msg.get("id") == want_id:
            return msg


def test_mcp_sqlite_serves_canned_query_tools(tmp_path):
    db = tmp_path / "dataset.db"
    df = pd.DataFrame(
        {"geo": ["SC", "OOS"], "reason_cat": ["Traffic", "Homicide"], "nets": [10, 20]}
    )
    build_dataset_db(df, str(db), table_name="records")

    proc = subprocess.Popen(
        ["uvx", "mcp-sqlite==0.3.2", str(db), "--metadata", str(_YML), "--prefix", "ds_"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    q = queue.Queue()
    err_lines = []
    threading.Thread(target=_pump, args=(proc.stdout, q), daemon=True).start()
    threading.Thread(target=_drain, args=(proc.stderr, err_lines), daemon=True).start()

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def _stderr_tail():
        tail = "".join(err_lines[-20:]).strip()
        return ("\n--- mcp-sqlite stderr tail ---\n" + tail) if tail else ""

    try:
        try:
            send({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "magpie-smoke", "version": "0.1.0"},
                },
            })
            # Generous first-read timeout: uvx may resolve mcp-sqlite on a cold cache.
            init = _read_until_id(q, 1, timeout=90)
            assert "result" in init, "initialize failed: %r" % (init,)

            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            listed = _read_until_id(q, 2, timeout=30)
            tools = listed.get("result", {}).get("tools", [])
            names = [t.get("name", "") for t in tools]
            assert any(n.startswith("ds_") for n in names), (
                "no ds_ canned-query tools advertised: %r" % (names,)
            )
        except AssertionError as exc:
            # Surface the stderr tail so a real failure (vs a buffer flake) is debuggable.
            raise AssertionError(str(exc) + _stderr_tail()) from exc
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()  # reap the hard-killed child so it is not left as a zombie

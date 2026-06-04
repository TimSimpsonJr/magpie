"""Structural smoke tests for the dataset-analyze skill wiring (Phase 3.4).

Validate the STATIC config that ties the pipeline to the ``mcp-sqlite`` server:
the bundled ``.mcp.json``, the ``canned_queries.yml`` metadata, and the
``SKILL.md`` frontmatter. These pin the rigor invariants of the served query
surface -- every canned query is row-capped (``LIMIT``), nothing is
write-enabled, and ``.mcp.json`` uses only the verified ``${...}`` interpolation
forms -- so a wiring regression fails CI instead of surfacing as a broken or
over-sharing MCP server at runtime. No server is launched; no real corpus is read.
"""

import json
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
MCP_JSON = REPO / ".mcp.json"
SKILL = REPO / "skills" / "dataset-analyze" / "SKILL.md"
CANNED = REPO / "skills" / "dataset-analyze" / "canned_queries.yml"

# The only ${...} tokens allowed WITHOUT a `:-default`. Claude Code substitutes
# these directly in a plugin-bundled .mcp.json; any OTHER bare ${VAR} with no
# default makes Claude Code fail to PARSE the config (verified against the live
# Claude Code MCP docs), so the wiring must never ship one.
_CLAUDE_PROVIDED = {"${CLAUDE_PROJECT_DIR}", "${CLAUDE_PLUGIN_ROOT}", "${CLAUDE_PLUGIN_DATA}"}


def _mcp() -> dict:
    return json.loads(MCP_JSON.read_text(encoding="utf-8"))


def _server() -> dict:
    return _mcp()["mcpServers"]["magpie-dataset"]


def _canned() -> dict:
    return yaml.safe_load(CANNED.read_text(encoding="utf-8"))


def _queries() -> dict:
    return _canned()["databases"]["dataset"]["queries"]


# ==========================================================================
# .mcp.json -- the bundled magpie-dataset server
# ==========================================================================

def test_mcp_json_parses_and_declares_magpie_dataset_server():
    cfg = _mcp()
    assert "mcpServers" in cfg
    assert "magpie-dataset" in cfg["mcpServers"]


def test_mcp_server_launches_pinned_mcp_sqlite_via_uvx():
    srv = _server()
    assert srv["command"] == "uvx"
    assert "mcp-sqlite==0.3.2" in srv["args"], "mcp-sqlite must be pinned to 0.3.2"


def test_mcp_metadata_path_uses_plugin_root():
    args = _server()["args"]
    meta = args[args.index("--metadata") + 1]
    assert meta == "${CLAUDE_PLUGIN_ROOT}/skills/dataset-analyze/canned_queries.yml"


def test_mcp_db_path_uses_project_dir():
    args = _server()["args"]
    db_arg = next(a for a in args if a.endswith("dataset.db"))
    assert db_arg == "${CLAUDE_PROJECT_DIR}/.magpie/dataset.db"


def test_mcp_interpolation_has_no_unset_var_without_default():
    # An unset ${VAR} with no `:-default` makes Claude Code fail to PARSE the
    # config. Every ${...} token must be Claude-provided OR carry a `:-default`.
    args = _server()["args"]
    for a in args:
        for inner in re.findall(r"\$\{([^}]*)\}", a):
            token = "${" + inner + "}"
            assert (":-" in inner) or (token in _CLAUDE_PROVIDED), (
                f"{token} is neither Claude-provided nor has a ':-' default; an "
                f"unset var with no default makes Claude Code fail to parse .mcp.json"
            )


def test_mcp_prefix_is_ds():
    args = _server()["args"]
    assert args[args.index("--prefix") + 1] == "ds_"


def test_mcp_ships_no_write_flag():
    # mcp-sqlite is read-only by default; assert no write-enabling arg is added.
    args = _server()["args"]
    assert not any(a in ("-w", "--write") for a in args)


# ==========================================================================
# canned_queries.yml -- the mcp-sqlite metadata (read-only, row-capped)
# ==========================================================================

def test_canned_db_key_matches_served_db_stem():
    # The `databases` key MUST be the served DB file STEM. .mcp.json serves
    # .../dataset.db, so the metadata key must be exactly `dataset`.
    assert list(_canned()["databases"].keys()) == ["dataset"]


def test_canned_table_is_records():
    assert "records" in _canned()["databases"]["dataset"]["tables"]


def test_every_canned_query_embeds_a_limit():
    # Row-cap rigor: mcp-sqlite 0.3.2 enforces NO row cap, so a canned query
    # without LIMIT could dump a 1M-row table as one HTML blob. Require the
    # *outer* result to be bounded -- the query must END with a `LIMIT <int>`
    # clause. A bare `\blimit\b` anywhere would pass on a LIMIT buried in a
    # subquery or comment while the outer SELECT stays unbounded.
    queries = _queries()
    assert queries, "expected at least one canned query"
    for name, q in queries.items():
        sql = q["sql"].strip()
        sql = sql.rstrip(";").strip().lower()
        assert re.search(r"\blimit\s+\d+\s*$", sql), (
            f"canned query {name!r} does not END with a `LIMIT <int>` clause -- "
            f"the outer result must be bounded (mcp-sqlite enforces no row cap)"
        )


def test_no_canned_query_is_write_enabled():
    # `write: true` is the ONLY path to ?mode=rw. The served surface stays read-only.
    for name, q in _queries().items():
        assert q.get("write") is not True, f"canned query {name!r} is write-enabled"


def test_canned_queries_read_from_records_table():
    # Every query reads from `records` (the only served table).
    for name, q in _queries().items():
        assert "from records" in q["sql"].lower(), (
            f"canned query {name!r} does not read from the served `records` table"
        )


# ==========================================================================
# SKILL.md -- frontmatter triggers the skill
# ==========================================================================

def test_skill_md_has_frontmatter_name_and_description():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---"), "SKILL.md must open with YAML frontmatter"
    front = text.split("---", 2)[1]
    meta = yaml.safe_load(front)
    assert meta["name"] == "dataset-analyze"
    assert meta.get("description", "").strip(), "SKILL.md needs a description"


def test_skill_md_uses_prefixed_builtin_tool_names():
    # The bundled .mcp.json launches mcp-sqlite with `--prefix ds_`, and (verified
    # against 0.3.2 source) `--prefix` prepends to ALL tools, the built-ins INCLUDED.
    # So SKILL.md must name the built-ins as ds_sqlite_get_catalog / ds_sqlite_execute
    # -- the bare sqlite_get_catalog / sqlite_execute tools do NOT exist under our prefix.
    text = SKILL.read_text(encoding="utf-8")
    assert "ds_sqlite_get_catalog" in text, "SKILL.md must use the prefixed catalog tool"
    assert "ds_sqlite_execute" in text, "SKILL.md must use the prefixed ad-hoc SQL tool"
    # No BARE built-in name. The lookbehind excludes the `ds_`-prefixed form. (For
    # `sqlite_execute` a plain `\b...\b` already won't match `ds_sqlite_execute` since
    # `_` is a word char, so the lookbehind there is belt-and-suspenders.)
    assert not re.search(r"(?<!ds_)\bsqlite_get_catalog\b", text), (
        "SKILL.md has a bare `sqlite_get_catalog` -- it does not exist under --prefix ds_"
    )
    assert not re.search(r"(?<!ds_)\bsqlite_execute\b", text), (
        "SKILL.md has a bare `sqlite_execute` -- it does not exist under --prefix ds_"
    )

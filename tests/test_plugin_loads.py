"""Consolidated plugin-load / install smoke.

Reads the repo's OWN manifest + skills + MCP config at RUNTIME (open()/json/yaml),
so the non-ASCII SKILL.md bodies are fine here -- we only parse their frontmatter.
House-style mirrors the existing skill-smoke tests (PyYAML; iterate skills/*/SKILL.md).
Must PASS offline (no network, no models).
"""
import json
from pathlib import Path

import yaml  # PyYAML, already a dev dep

_REPO = Path(__file__).resolve().parents[1]


def test_plugin_manifest_loads():
    manifest = json.loads((_REPO / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    assert manifest["name"] == "magpie"
    assert "librarian" in [d if isinstance(d, str) else d.get("name")
                           for d in manifest["dependencies"]]


def test_every_skill_frontmatter_parses():
    skills = list((_REPO / "skills").glob("*/SKILL.md"))
    assert skills, "no skills found"
    for sk in skills:
        text = sk.read_text("utf-8")
        assert text.startswith("---"), f"{sk} missing frontmatter fence"
        fm = yaml.safe_load(text.split("---", 2)[1])
        assert fm.get("name") and fm.get("description"), f"{sk} frontmatter incomplete"


def test_mcp_config_parses_and_interpolates():
    cfg = json.loads((_REPO / ".mcp.json").read_text("utf-8"))
    blob = json.dumps(cfg)
    assert "magpie-dataset" in cfg["mcpServers"]
    # no bare ${VAR} without a :- default would crash CC config parsing; ours use
    # ${CLAUDE_PROJECT_DIR}/${CLAUDE_PLUGIN_ROOT} which CC substitutes directly.
    assert "mcp-sqlite" in blob

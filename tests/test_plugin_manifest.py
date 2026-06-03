"""Smoke test: the magpie plugin manifest is well-formed (Task 0.2)."""

import json
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json"


def test_manifest_parses_and_has_required_keys():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for key in ("name", "description", "version"):
        assert key in data, f"plugin.json missing required key: {key}"
    assert data["name"] == "magpie"


def test_manifest_declares_librarian_dependency():
    """Magpie hard-depends on librarian (design doc §2, §5.7).

    An entry may be a bare plugin-name string or an object with a ``name``
    key (verified against the Claude Code plugin-dependencies docs).
    """
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    deps = data.get("dependencies", [])
    names = [d if isinstance(d, str) else d.get("name") for d in deps]
    assert "librarian" in names

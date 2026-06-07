"""Smoke test for the doctor SKILL.md (read-only health check)."""
from __future__ import annotations

from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent / "skills" / "doctor" / "SKILL.md"


def _frontmatter_and_body(p):
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---")
    _, fm, body = text.split("---", 2)
    return yaml.safe_load(fm), body


def test_frontmatter():
    fm, _ = _frontmatter_and_body(SKILL)
    assert fm["name"] == "doctor"
    assert "version" in fm
    d = fm["description"].lower()
    assert "health" in d or "check" in d or "diagnos" in d


def test_body_documents_read_only_contract():
    _, body = _frontmatter_and_body(SKILL)
    low = body.lower()
    assert "detect_tier" in body
    assert "read-only" in low or "read only" in low
    # never installs / never runs setup / never starts the server
    assert "never" in low
    assert "setup" in low                              # points back to setup/operator
    # Layer 2 (Phase 13a): doctor now reports the entity-graph capability via a
    # strictly READ-ONLY Docker probe -- which + `docker version`/`docker compose
    # version` rc only, NEVER a run/pull/start. The read-only contract is the pin;
    # installing Docker is setup's job, not doctor's. (The Layer 0-1 no-Docker
    # framing now applies only to the ONRAMP docs, pinned by test_onramp_docs.py.)
    assert "docker" in low                             # Layer-2 read-only probe
    assert "read-only" in low                          # the Docker probe is read-only
    assert "pull" in low or "starts a container" in low  # spells out what it never does

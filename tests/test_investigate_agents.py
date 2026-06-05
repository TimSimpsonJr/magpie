# tests/test_investigate_agents.py -- ASCII only.
# Frontmatter + body smoke test for the two Phase-8 investigate verifier agents
# (agents/extraction-verifier.md, agents/citation-checker.md). Mirrors the
# PyYAML frontmatter pattern used by the *_skill smoke tests. These assertions
# must FAIL if a required concept is dropped from an agent body.
import pathlib

import yaml


def _frontmatter(path):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    assert text.startswith("---"), f"{path} missing frontmatter"
    _, fm, body = text.split("---", 2)
    return yaml.safe_load(fm), body


def test_extraction_verifier_frontmatter_and_body():
    fm, body = _frontmatter("agents/extraction-verifier.md")
    assert fm["name"] == "extraction-verifier"
    assert fm.get("description", "").strip()
    assert fm.get("tools", "")  # declares a tool surface
    low = body.lower()
    # the conservative default + the two checks it runs
    assert "indeterminate" in low
    assert "presence" in low and "entailment" in low
    # honest limit: advisory adversarial re-check, never the real verifier
    assert "advisory" in low
    # blinded to the extractor's reasoning
    assert "blind" in low or "without the extractor" in low
    # output is the three-way result enum (supported/contradicted/indeterminate)
    assert "supported" in low and "contradicted" in low
    # honest limit names WHY it is degraded (same model -> correlated errors) and
    # that the human gate is the only real verifier
    assert "same model" in low or "correlated" in low
    assert "human gate" in low
    # never auto-accepts
    assert "never auto-accept" in low or "auto-accept" in low
    # reasoning is LOCAL-only, never published
    assert "local" in low and "never published" in low


def test_citation_checker_frontmatter_and_body():
    fm, body = _frontmatter("agents/citation-checker.md")
    assert fm["name"] == "citation-checker"
    assert fm.get("description", "").strip()
    assert fm.get("tools", "")  # declares a tool surface (it runs the resolver)
    low = body.lower()
    # drives the deterministic resolver in scripts/citation.py
    assert "resolve_anchor" in low and "is_clean_citation" in low
    assert "scripts/citation.py" in low
    # flags uncited claims
    assert "uncited" in low
    # degraded levels are NOT a clean pass
    assert "degraded" in low
    # names the degraded levels it must flag
    assert "ambiguous" in low and "block" in low and "page" in low and "unresolved" in low
    # the matched_text vs verbatim_quote mismatch flag
    assert "matched_text" in low and "verbatim_quote" in low
    # mechanical only -- makes NO semantic judgment (that is the verifier's job)
    assert "deterministic" in low
    assert "no semantic" in low or "not semantic" in low or "semantic judgment" in low

"""Smoke test for the investigate orchestration skill (Phase 8 Task 5).

ASCII only. Mirrors tests/test_ingest_skill.py: parse the SKILL.md frontmatter
with yaml.safe_load and assert the body documents the load-bearing contracts
(refuse seam, evidence-before-claim, redact publish edge, keyword guardrail,
human gate, engine module). Keep these assertions meaningful.
"""
import pathlib

import yaml


def _skill():
    text = pathlib.Path("skills/investigate/SKILL.md").read_text(encoding="utf-8")
    _, fm, body = text.split("---", 2)
    return yaml.safe_load(fm), body


def test_investigate_skill_frontmatter():
    fm, body = _skill()
    assert fm["name"] == "investigate"
    assert fm.get("description", "").strip()
    assert "version" in fm
    low = body.lower()
    assert "trustworthy_for_extraction" in low       # the refuse seam
    assert "evidence" in low and "before" in low       # evidence-before-claim
    assert "redact_note" in low                        # publish edge
    assert "keyword_mask" in low                       # keyword guardrail
    assert "human gate" in low or "human-gate" in low
    assert "citation.py" in low or "build_anchor" in low  # engine


def test_investigate_skill_documents_orchestration_contracts():
    """The five orchestration steps + the honest limits must be present so the
    skill body actually encodes the design, not just the smoke-keywords."""
    fm, body = _skill()
    low = body.lower()
    # Refuse seam keys on the boolean, and explicitly warns against keying on the
    # decision string (the design's leak-through trap for a PARTIAL_SUCCESS doc).
    assert "trustworthy_for_extraction" in low
    refuse = low.split("## 1", 1)[0]  # the refuse section, before Extract
    assert "do not key on" in refuse and "doc_decision" in refuse
    # The two blinded verifier agents are named.
    assert "citation-checker" in low
    assert "extraction-verifier" in low
    # The redacted-publish surface uses the public anchor, raw stays local.
    assert "public_anchor" in low
    assert "librarian" in low
    # Honest limit: the human gate is the only real verifier; degraded anchors
    # are never auto-passed.
    assert "indeterminate" in low or "contradicted" in low
    assert "degraded" in low


def test_investigate_skill_description_is_third_person_trigger():
    fm, _ = _skill()
    desc = fm["description"]
    # House style: third-person "This skill should be used when..." trigger.
    assert "this skill should be used when" in desc.lower()


def test_no_mcp_json_in_skill():
    assert not (pathlib.Path("skills/investigate") / ".mcp.json").exists()


def test_prior_art_reference_exists_and_records_verified_facts():
    path = pathlib.Path("skills/investigate/references/prior-art.md")
    assert path.exists()
    low = path.read_text(encoding="utf-8").lower()
    # (a) the verified docling-core serialized shape.
    assert "2.78.1" in low
    assert "self_ref" in low and "charspan" in low
    # (b) the W3C TextQuoteSelector prior art behind the relocation context.
    assert "textquoteselector" in low
    # (c) the Greenville-validation finding: charspan is not a reliable .text
    # offset, so the anchor computes its own offsets + requires single-prov.
    assert "single-prov" in low or "single prov" in low


def test_skill_and_prior_art_are_ascii():
    for rel in ("skills/investigate/SKILL.md",
                "skills/investigate/references/prior-art.md"):
        raw = pathlib.Path(rel).read_bytes()
        raw.decode("ascii")  # raises if any non-ASCII byte slipped in

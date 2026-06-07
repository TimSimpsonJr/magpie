"""Golden tests for scripts.entity_review_packet (Phase 13a, Task 3).

ASCII only. Windows-golden: a FAKE snippet_resolver stands in for the Phase-6
source-text hydration, so the renderer is exercised without any infra. Covers
the load-bearing fail-closed property (packet_hash excludes volatile metadata),
self-containment of the HTML, snippet marking + escaping, and verdict-JSON
parse/round-trip/rejection.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from scripts.entity_resolution_policy import Candidate, CandidateSide, Mention, Verdict
from scripts.entity_review_packet import (
    build_candidate_snapshot,
    parse_verdicts,
    render_html,
)


# ---------------------------------------------------------------------------
# A fake snippet resolver: deterministic, records its calls.
# ---------------------------------------------------------------------------

class FakeResolver:
    """Records every call; returns a deterministic window that EMBEDS the
    mention text so the renderer can <mark> it. A larger context_chars yields a
    visibly wider window (so a test can prove the expanded view widened)."""

    def __init__(self, mention_text_by_offset: dict | None = None) -> None:
        self.calls: list[dict] = []
        self._by_offset = mention_text_by_offset or {}

    def __call__(self, doc_id, page, char_start, char_end, *, context_chars=0):
        self.calls.append(
            {
                "doc_id": doc_id,
                "page": page,
                "char_start": char_start,
                "char_end": char_end,
                "context_chars": context_chars,
            }
        )
        mention = self._by_offset.get((doc_id, page, char_start, char_end), "")
        pad = "." * (1 + context_chars)
        return "left ctx " + pad + " " + mention + " " + pad + " right ctx"


def _mk_mention(doc_id, page, start, end, text):
    return Mention(doc_id=doc_id, page=page, char_start=start, char_end=end, text=text)


def _two_candidate_snapshot_inputs():
    """Two candidates: a Person pair (2 mentions/side + properties) and a
    Company pair. Offsets are arbitrary but unique so the fake can key on them."""
    left1 = CandidateSide(
        id="node_left_1",
        caption="Officer J. Smith",
        schema="Person",
        aliases=["J. Smith"],
        properties={"badge": ["214"], "title": ["Police Officer"]},
        mentions=[
            _mk_mention("incident_report.pdf", 2, 10, 26, "Officer J. Smith"),
            _mk_mention("incident_report.pdf", 4, 40, 50, "Ofc. Smith"),
        ],
    )
    right1 = CandidateSide(
        id="node_right_1",
        caption="John A. Smith",
        schema="Person",
        aliases=[],
        properties={"badge": ["214"]},
        mentions=[
            _mk_mention("roster.pdf", 1, 5, 18, "John A. Smith"),
            _mk_mention("payroll.pdf", 6, 70, 80, "J. Smith"),
        ],
    )
    left2 = CandidateSide(
        id="node_left_2",
        caption="Acme Towing LLC",
        schema="Company",
        aliases=[],
        properties={},
        mentions=[_mk_mention("contract.pdf", 3, 20, 35, "Acme Towing LLC")],
    )
    right2 = CandidateSide(
        id="node_right_2",
        caption="ACME Towing",
        schema="Organization",
        aliases=[],
        properties={"address": ["14 Industrial Way"]},
        mentions=[_mk_mention("vendors.pdf", 1, 8, 19, "ACME Towing")],
    )
    c1 = Candidate(left=left1, right=right1, score=0.91)
    c2 = Candidate(left=left2, right=right2, score=0.84)
    return c1, c2


def _resolver_for(*candidates):
    by_offset = {}
    for c in candidates:
        for side in (c.left, c.right):
            for m in side.mentions:
                by_offset[(m.doc_id, m.page, m.char_start, m.char_end)] = m.text
    return FakeResolver(by_offset)


_META = dict(
    investigation_id="simpsonville_alpr",
    algorithm="logic-v2",
    thresholds={"review_floor": 0.70, "auto_threshold": 0.98},
    resolver_db_hash="sha256:db_aaa",
    generated_at="2026-06-07",
)


# ---------------------------------------------------------------------------
# packet_hash determinism + order-independence + metadata exclusion
# ---------------------------------------------------------------------------

def test_packet_hash_is_deterministic():
    c1, c2 = _two_candidate_snapshot_inputs()
    _, h1 = build_candidate_snapshot([c1, c2], **_META)
    _, h2 = build_candidate_snapshot([c1, c2], **_META)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hexdigest


def test_packet_hash_stable_under_candidate_input_order():
    c1, c2 = _two_candidate_snapshot_inputs()
    snap_a, h_a = build_candidate_snapshot([c1, c2], **_META)
    snap_b, h_b = build_candidate_snapshot([c2, c1], **_META)
    # Sorted before hashing -> identical hash regardless of input order.
    assert h_a == h_b
    # And the serialized candidate order is identical too.
    assert snap_a["candidates"] == snap_b["candidates"]


def test_packet_hash_stable_under_property_key_order():
    c1, _ = _two_candidate_snapshot_inputs()
    # Rebuild the same candidate but insert properties in a different key order.
    left = c1.left
    left_reordered = CandidateSide(
        id=left.id,
        caption=left.caption,
        schema=left.schema,
        aliases=list(left.aliases),
        properties={"title": ["Police Officer"], "badge": ["214"]},  # swapped
        mentions=list(left.mentions),
    )
    c1b = Candidate(left=left_reordered, right=c1.right, score=c1.score)
    _, h_orig = build_candidate_snapshot([c1], **_META)
    _, h_swap = build_candidate_snapshot([c1b], **_META)
    assert h_orig == h_swap


def test_packet_hash_excludes_metadata():
    """The load-bearing fail-closed property: different generated_at +
    resolver_db_hash but identical candidates -> SAME packet_hash, so Task 4 can
    recompute it from the live resolver without knowing the generation time."""
    c1, c2 = _two_candidate_snapshot_inputs()
    meta_a = dict(_META)
    meta_b = dict(_META)
    meta_b["generated_at"] = "2099-01-01"
    meta_b["resolver_db_hash"] = "sha256:totally_different"
    _, h_a = build_candidate_snapshot([c1, c2], **meta_a)
    _, h_b = build_candidate_snapshot([c1, c2], **meta_b)
    assert h_a == h_b


def test_packet_hash_changes_when_candidate_drift():
    c1, c2 = _two_candidate_snapshot_inputs()
    # Change a display field (caption) -> the hash must move (fail-closed guard).
    drifted = CandidateSide(
        id=c1.left.id,
        caption="Officer J. SMITH (changed)",
        schema=c1.left.schema,
        aliases=list(c1.left.aliases),
        properties=dict(c1.left.properties),
        mentions=list(c1.left.mentions),
    )
    c1b = Candidate(left=drifted, right=c1.right, score=c1.score)
    _, h_orig = build_candidate_snapshot([c1, c2], **_META)
    _, h_drift = build_candidate_snapshot([c1b, c2], **_META)
    assert h_orig != h_drift


def test_snapshot_shape_and_metadata():
    c1, c2 = _two_candidate_snapshot_inputs()
    snap, packet_hash = build_candidate_snapshot([c1, c2], **_META)
    assert set(snap.keys()) == {"metadata", "candidates"}
    md = snap["metadata"]
    assert md["investigation_id"] == "simpsonville_alpr"
    assert md["algorithm"] == "logic-v2"
    assert md["thresholds"] == {"review_floor": 0.70, "auto_threshold": 0.98}
    assert md["resolver_db_hash"] == "sha256:db_aaa"
    assert md["generated_at"] == "2026-06-07"
    assert md["packet_version"] == "1.0"
    # Candidates are dicts (asdict) with left/right/score, sorted by ids.
    assert len(snap["candidates"]) == 2
    ids = [(c["left"]["id"], c["right"]["id"]) for c in snap["candidates"]]
    assert ids == sorted(ids)
    first = snap["candidates"][0]
    assert set(first["left"].keys()) == {
        "id",
        "caption",
        "schema",
        "aliases",
        "properties",
        "mentions",
    }


# ---------------------------------------------------------------------------
# render_html: self-contained
# ---------------------------------------------------------------------------

def test_render_html_is_self_contained():
    c1, c2 = _two_candidate_snapshot_inputs()
    snap, _ = build_candidate_snapshot([c1, c2], **_META)
    resolver = _resolver_for(c1, c2)
    html_out = render_html(snap, resolver)
    assert "<!DOCTYPE html>" in html_out
    # The dark-theme block must be present (prefers-color-scheme media query).
    assert "prefers-color-scheme" in html_out
    # No external references whatsoever.
    assert "http://" not in html_out
    assert "https://" not in html_out
    assert "src=" not in html_out


def test_render_html_embeds_real_packet_hash_and_investigation():
    c1, c2 = _two_candidate_snapshot_inputs()
    snap, packet_hash = build_candidate_snapshot([c1, c2], **_META)
    resolver = _resolver_for(c1, c2)
    html_out = render_html(snap, resolver)
    # The real packet_hash and investigation_id are embedded into the JS, not the
    # mockup's demo constants.
    assert packet_hash in html_out
    assert "3f9c1a...d2" not in html_out  # the mockup demo hash is gone
    assert "simpsonville_alpr" in html_out


# ---------------------------------------------------------------------------
# render_html: content (pairs, snippets, more-evidence, properties)
# ---------------------------------------------------------------------------

def test_render_html_renders_each_pair_and_snippet():
    c1, c2 = _two_candidate_snapshot_inputs()
    snap, _ = build_candidate_snapshot([c1, c2], **_META)
    resolver = _resolver_for(c1, c2)
    html_out = render_html(snap, resolver)

    # data-left / data-right ids for each pair (escaped attribute values).
    assert 'data-left="node_left_1"' in html_out
    assert 'data-right="node_right_1"' in html_out
    assert 'data-left="node_left_2"' in html_out
    assert 'data-right="node_right_2"' in html_out

    # Captions appear.
    assert "Officer J. Smith" in html_out
    assert "John A. Smith" in html_out
    assert "Acme Towing LLC" in html_out
    assert "ACME Towing" in html_out

    # The hydrated TOP snippet (the fake resolver's output) appears.
    assert "left ctx" in html_out and "right ctx" in html_out

    # Score badge class + same-question per schema.
    assert "s-hi" in html_out  # 0.91 -> s-hi
    assert "s-mid" in html_out  # 0.84 -> s-mid
    assert "Same person?" in html_out
    assert "Same organization?" in html_out


def test_render_html_more_evidence_panel_and_properties():
    c1, c2 = _two_candidate_snapshot_inputs()
    snap, _ = build_candidate_snapshot([c1, c2], **_META)
    resolver = _resolver_for(c1, c2)
    html_out = render_html(snap, resolver)

    # The expandable more-evidence panel is present.
    assert '<details class="more">' in html_out
    assert "More evidence" in html_out

    # Properties surface as kv pills: "<b>key</b>value".
    assert "<b>badge</b>214" in html_out
    assert "<b>title</b>Police Officer" in html_out
    assert "<b>address</b>14 Industrial Way" in html_out

    # The ADDITIONAL mention text (mentions[1:]) surfaces in the expanded view.
    assert "Ofc. Smith" in html_out


def test_render_html_expanded_view_uses_wider_context():
    c1, c2 = _two_candidate_snapshot_inputs()
    snap, _ = build_candidate_snapshot([c1, c2], **_META)
    resolver = _resolver_for(c1, c2)
    render_html(snap, resolver)
    # The expanded more-evidence view must hydrate with context_chars > 0.
    assert any(call["context_chars"] > 0 for call in resolver.calls)
    # The collapsed top snippet uses the default (0) window.
    assert any(call["context_chars"] == 0 for call in resolver.calls)


def test_render_html_handles_side_with_no_extra_evidence():
    """A side with a single mention and no properties must not crash the
    more-evidence panel (Company pair: left2 has no properties/extra mentions)."""
    c1, c2 = _two_candidate_snapshot_inputs()
    snap, _ = build_candidate_snapshot([c2], **_META)  # just the Company pair
    resolver = _resolver_for(c2)
    html_out = render_html(snap, resolver)
    assert "Acme Towing LLC" in html_out
    assert '<details class="more">' in html_out  # still renders, gracefully


# ---------------------------------------------------------------------------
# Escaping + marking
# ---------------------------------------------------------------------------

def test_render_html_escapes_injected_script_in_caption():
    payload = "<script>alert(1)</script>"
    side_l = CandidateSide(
        id="n_l",
        caption=payload,
        schema="Person",
        aliases=[],
        properties={},
        mentions=[_mk_mention("d.pdf", 1, 0, 5, "Smith")],
    )
    side_r = CandidateSide(
        id="n_r",
        caption="Plain Name",
        schema="Person",
        aliases=[],
        properties={"note": [payload]},  # also in a property value
        mentions=[_mk_mention("e.pdf", 1, 0, 5, "Smith")],
    )
    cand = Candidate(left=side_l, right=side_r, score=0.80)
    snap, _ = build_candidate_snapshot([cand], **_META)
    resolver = _resolver_for(cand)
    html_out = render_html(snap, resolver)
    # The escaped form is present; the live tag is NOT.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_out
    assert "<script>alert(1)</script>" not in html_out


def test_render_html_marks_mention_in_snippet():
    text = "Officer J. Smith"
    side_l = CandidateSide(
        id="n_l",
        caption="Officer J. Smith",
        schema="Person",
        aliases=[],
        properties={},
        mentions=[_mk_mention("d.pdf", 2, 10, 26, text)],
    )
    side_r = CandidateSide(
        id="n_r",
        caption="John A. Smith",
        schema="Person",
        aliases=[],
        properties={},
        mentions=[_mk_mention("e.pdf", 1, 0, 13, "John A. Smith")],
    )
    cand = Candidate(left=side_l, right=side_r, score=0.91)
    snap, _ = build_candidate_snapshot([cand], **_META)
    resolver = _resolver_for(cand)
    html_out = render_html(snap, resolver)
    # The mention text is wrapped in <mark>...</mark> (escaped text inside).
    assert "<mark>Officer J. Smith</mark>" in html_out


def test_render_html_does_not_crash_on_empty_or_missing_mention_text():
    # Empty mention text -> snippet rendered with no <mark>, no crash.
    side_l = CandidateSide(
        id="n_l",
        caption="Anon",
        schema="Person",
        aliases=[],
        properties={},
        mentions=[_mk_mention("d.pdf", 1, 0, 0, "")],
    )
    side_r = CandidateSide(
        id="n_r",
        caption="Other",
        schema="Person",
        aliases=[],
        properties={},
        mentions=[_mk_mention("e.pdf", 1, 0, 0, "")],
    )
    cand = Candidate(left=side_l, right=side_r, score=0.75)
    snap, _ = build_candidate_snapshot([cand], **_META)
    resolver = _resolver_for(cand)
    html_out = render_html(snap, resolver)  # must not raise
    assert "Anon" in html_out


def test_render_html_is_deterministic():
    c1, c2 = _two_candidate_snapshot_inputs()
    snap, _ = build_candidate_snapshot([c1, c2], **_META)
    out_a = render_html(snap, _resolver_for(c1, c2))
    out_b = render_html(snap, _resolver_for(c1, c2))
    assert out_a == out_b


# ---------------------------------------------------------------------------
# parse_verdicts
# ---------------------------------------------------------------------------

def test_parse_verdicts_round_trips_a_well_formed_export():
    export = json.dumps(
        {
            "investigation_id": "simpsonville_alpr",
            "packet_hash": "abc123",
            "verdicts": [
                {"left": "node_left_1", "right": "node_right_1", "verdict": "merge"},
                {"left": "node_left_2", "right": "node_right_2", "verdict": "distinct"},
            ],
        }
    )
    packet_hash, verdicts = parse_verdicts(export)
    assert packet_hash == "abc123"
    assert verdicts == [
        Verdict(left_id="node_left_1", right_id="node_right_1", verdict="merge"),
        Verdict(left_id="node_left_2", right_id="node_right_2", verdict="distinct"),
    ]
    # JSON keys are left/right; they map to Verdict.left_id / right_id.
    assert verdicts[0].left_id == "node_left_1"
    assert verdicts[0].right_id == "node_right_1"


def test_parse_verdicts_accepts_empty_verdict_list():
    export = json.dumps(
        {"investigation_id": "x", "packet_hash": "h", "verdicts": []}
    )
    packet_hash, verdicts = parse_verdicts(export)
    assert packet_hash == "h"
    assert verdicts == []


def test_parse_verdicts_rejects_non_json():
    with pytest.raises(ValueError):
        parse_verdicts("this is not json {{{")


def test_parse_verdicts_rejects_missing_packet_hash():
    export = json.dumps({"investigation_id": "x", "verdicts": []})
    with pytest.raises(ValueError):
        parse_verdicts(export)


def test_parse_verdicts_rejects_empty_packet_hash():
    export = json.dumps(
        {"investigation_id": "x", "packet_hash": "", "verdicts": []}
    )
    with pytest.raises(ValueError):
        parse_verdicts(export)


def test_parse_verdicts_rejects_missing_verdicts_key():
    export = json.dumps({"investigation_id": "x", "packet_hash": "h"})
    with pytest.raises(ValueError):
        parse_verdicts(export)


def test_parse_verdicts_rejects_non_list_verdicts():
    export = json.dumps(
        {"investigation_id": "x", "packet_hash": "h", "verdicts": {"a": 1}}
    )
    with pytest.raises(ValueError):
        parse_verdicts(export)


def test_parse_verdicts_rejects_row_missing_a_field():
    export = json.dumps(
        {
            "investigation_id": "x",
            "packet_hash": "h",
            "verdicts": [{"left": "a", "verdict": "merge"}],  # no "right"
        }
    )
    with pytest.raises(ValueError):
        parse_verdicts(export)


def test_parse_verdicts_rejects_bad_verdict_value():
    export = json.dumps(
        {
            "investigation_id": "x",
            "packet_hash": "h",
            "verdicts": [{"left": "a", "right": "b", "verdict": "bogus"}],
        }
    )
    with pytest.raises(ValueError):
        parse_verdicts(export)


# ---------------------------------------------------------------------------
# Import purity (offline; subprocess-isolated). No marker.
# ---------------------------------------------------------------------------

def test_importing_entity_review_packet_is_pure():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "import scripts.entity_review_packet as m\n"
        "bad=[x for x in ('nomenklatura','neo4j','followthemoney') if x in sys.modules]\n"
        "assert not bad, bad\nprint('PURE_OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "PURE_OK" in proc.stdout

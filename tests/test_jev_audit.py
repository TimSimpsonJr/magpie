"""ASCII only. Offline tests for the Part A audit fields and human-gate label (Task 8, spec 2.5).

Covers jev_prescreen.PRESCREEN_KEYS / prescreen_record / gate_label / PRESCREEN_VERIFIER_RESULT
/ claim_input_from_record and CitationRecord.prescreen. Every Jev call goes through
FakeTransport and the model-approval state lives in tmp_path.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts import jev_client as jc
from scripts import jev_state
from scripts.citation import CitationRecord, build_anchor
from scripts.jev_prescreen import (
    GATE_LABEL,
    PRESCREEN_KEYS,
    PRESCREEN_VERIFIER_RESULT,
    ClaimInput,
    claim_input_from_record,
    gate_label,
    parse_claims,
    prescreen,
    prescreen_record,
)
from tests.conftest_citation import make_block, make_doc
from tests.helpers.jev_fakes import FakeTransport, noul_responder

ENV = {"MAGPIE_JEV": "1", "OPENROUTER_API_KEY": "k"}
BLOCK_TEXT = "Officer Ramirez ran searches on the county system after the policy changed."
QUOTE = "ran searches on the county system"
CLAIM = "Officer Ramirez ran searches on the county system."
INPUT_KEYS = {"claim_id", "claim_text", "verbatim_quote", "span", "clean_citation"}


def _entry(route="skip", reason=None, presence=0.94, entailment=0.91, spot_check=False):
    return {"presence": presence, "entailment": entailment, "route": route, "reason": reason,
            "spot_check": spot_check}


def _record(**kw) -> CitationRecord:
    block = make_block(0, BLOCK_TEXT)
    rec = build_anchor(block, verbatim_quote=QUOTE, claim_text=CLAIM, doc_id="doc-abc123",
                       doc_schema_name="DoclingDocument", doc_schema_version="1.10.0",
                       extractor_model="m", prompt_version="v1", timestamp="t")
    for key, value in kw.items():
        setattr(rec, key, value)
    return rec


# --- PRESCREEN_KEYS / prescreen_record ----------------------------------------------------

def test_prescreen_keys_constant():
    assert PRESCREEN_KEYS == ("presence", "entailment", "route", "reason", "model", "spot_check")


def test_prescreen_record_has_exactly_the_keys_and_carries_values():
    rec = prescreen_record(_entry(spot_check=True), "m1")
    assert tuple(rec) == PRESCREEN_KEYS
    assert rec == {"presence": 0.94, "entailment": 0.91, "route": "skip", "reason": None,
                   "model": "m1", "spot_check": True}


def test_prescreen_record_for_a_verify_entry_with_no_model():
    rec = prescreen_record(_entry(route="verify", reason="jev_off", presence=None,
                                  entailment=None), None)
    assert set(rec) == set(PRESCREEN_KEYS)
    assert rec["model"] is None and rec["route"] == "verify" and rec["reason"] == "jev_off"
    assert rec["presence"] is None and rec["entailment"] is None and rec["spot_check"] is False


def test_prescreen_record_drops_unknown_entry_keys():
    entry = dict(_entry(), claim_text="never copied", extra=1)
    rec = prescreen_record(entry, "m1")
    assert set(rec) == set(PRESCREEN_KEYS)
    assert "never copied" not in json.dumps(rec)


def test_prescreen_record_from_a_real_prescreen_output(tmp_path):
    state = tmp_path / "jev_state.json"
    jev_state.record_passing_model("m1", eval_summary={"n": 20}, path=state)
    fake = FakeTransport(noul_responder(lambda qid: 0.95))
    claims = [ClaimInput(claim_id="c1", claim_text=CLAIM, verbatim_quote=QUOTE,
                         span=BLOCK_TEXT, clean_citation=True)]
    out = prescreen(claims, env=ENV, state_path=state, seed="s",
                    ask_fn=lambda s, q: jc.ask(s, q, env=ENV, transport=fake))
    rec = prescreen_record(out["claims"]["c1"], out["model"])
    assert tuple(rec) == PRESCREEN_KEYS
    assert rec["route"] == "skip" and rec["model"] == "m1"
    assert rec["spot_check"] is True  # the lone skip claim is always spot-checked


# --- CitationRecord.prescreen -------------------------------------------------------------

def test_citation_record_default_prescreen_is_none_and_round_trips():
    rec = _record()
    d = rec.to_dict()
    assert d["prescreen"] is None
    back = CitationRecord(**json.loads(json.dumps(d)))
    assert back == rec


def test_citation_record_with_prescreen_round_trips_and_public_anchor_unchanged():
    block = prescreen_record(_entry(spot_check=True), "m1")
    rec = _record(prescreen=block, verifier_result=PRESCREEN_VERIFIER_RESULT,
                  verifier_confidence=None)
    d = rec.to_dict()
    assert d["prescreen"] == block
    back = CitationRecord(**json.loads(json.dumps(d)))
    assert back == rec and back.prescreen == block
    pub = rec.public_anchor()
    assert "prescreen" not in pub and len(pub) == 10
    assert pub["verifier_result"] == "prescreen-skip"


def test_prescreen_verifier_result_constant():
    assert PRESCREEN_VERIFIER_RESULT == "prescreen-skip"


# --- gate_label ---------------------------------------------------------------------------

def test_gate_label_for_skip():
    label = gate_label(prescreen_record(_entry(), "m1"))
    assert label == ("Jev pre-screen: supported -- not independently verified "
                     "(presence 0.94, entailment 0.91)")
    assert label == GATE_LABEL.format(presence=0.94, entailment=0.91)
    assert label.isascii() and "verified (" in label


def test_gate_label_rounds_to_two_places():
    assert gate_label(_entry(presence=0.9, entailment=0.876)).endswith(
        "(presence 0.90, entailment 0.88)")


def test_gate_label_none_for_verify_and_none():
    assert gate_label(_entry(route="verify", reason="low_score")) is None
    assert gate_label(None) is None


def test_gate_label_none_when_skip_scores_missing():
    assert gate_label(_entry(presence=None)) is None


# --- claim_input_from_record ---------------------------------------------------------------

def test_claim_input_from_record_exact_anchor_is_clean_with_block_text_span():
    rec = _record()
    doc = make_doc([make_block(0, BLOCK_TEXT)])
    got = claim_input_from_record("doc-abc123:c1", rec, doc)
    assert set(got) == INPUT_KEYS
    assert got == {"claim_id": "doc-abc123:c1", "claim_text": CLAIM, "verbatim_quote": QUOTE,
                   "span": BLOCK_TEXT, "clean_citation": True}


def test_claim_input_from_record_relocated_uses_the_resolved_block_text():
    rec = _record()
    moved = "Header line."
    doc = make_doc([make_block(0, moved), make_block(1, BLOCK_TEXT)])
    got = claim_input_from_record("c1", rec, doc)
    assert got["clean_citation"] is True and got["span"] == BLOCK_TEXT


def test_claim_input_from_record_block_gone_is_not_clean():
    rec = _record()
    doc = make_doc([])
    got = claim_input_from_record("c1", rec, doc)
    assert got["clean_citation"] is False
    assert got["span"] is None  # page-level resolution has no block


def test_claim_input_from_record_block_level_is_degraded_with_block_span():
    rec = _record()
    changed = "Officer Ramirez ran queries on the state system after the policy changed."
    doc = make_doc([make_block(0, changed)])
    got = claim_input_from_record("c1", rec, doc)
    assert got["clean_citation"] is False and got["span"] == changed


def test_claim_input_from_record_output_is_accepted_by_parse_claims():
    rec = _record()
    doc = make_doc([make_block(0, BLOCK_TEXT)])
    inputs = [claim_input_from_record("c1", rec, doc),
              claim_input_from_record("c2", rec, make_doc([]))]
    parsed = parse_claims(json.loads(json.dumps(inputs)))
    assert [c.claim_id for c in parsed] == ["c1", "c2"]
    assert parsed[0].clean_citation is True and parsed[1].span is None


def test_audit_module_text_is_ascii():
    src = (Path(__file__).resolve().parent.parent / "scripts" / "jev_prescreen.py").read_text(
        encoding="utf-8")
    assert src.isascii()

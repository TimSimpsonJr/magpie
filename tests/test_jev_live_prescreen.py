"""ASCII only. LIVE Part A eval (Task 14): the only test that approves a Jev model.

Selected only with ``-m jev_live`` (pyproject addopts deselect it otherwise, and CI never runs
it) and skipped unless the operator's environment already has ``MAGPIE_JEV=1`` and
``OPENROUTER_API_KEY``. It sends the 20 SYNTHETIC claims in
``tests/fixtures/jev/prescreen_eval.json`` (claim text, quote and span under K-keys; never the
claim ids) and nothing else.

Run: ``"$PY" -m pytest -m jev_live tests/test_jev_live_prescreen.py -s``

Pass criteria (spec 4, Decision 10):

- degenerate-run check: no claim may end with a transport/guard reason (``too_large``,
  ``waf_blocked``, ``jev_error``, ``pii``, ``secret``) or ``degraded_anchor``, and some window
  must report a model; a run that answered nothing must never approve a model;
- gate: every ``expect: "unsupported"`` item routes ``verify``.

On pass it prints per-claim scores and the skip rate over the supported items, then records
the model in ``data/jev_state.json`` (the real, untracked state file) via
``record_passing_model``. Until that record exists the pre-screen skips nothing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import jev_state
from scripts.jev_client import jev_status
from scripts.jev_prescreen import ENTAIL_MIN, PRESENCE_MIN, ROUTE_SKIP, ROUTE_VERIFY, \
    parse_claims, prescreen

pytestmark = [
    pytest.mark.jev_live,
    pytest.mark.skipif(not jev_status()[0],
                       reason="live Jev eval needs MAGPIE_JEV=1 and OPENROUTER_API_KEY"),
]

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "jev" / "prescreen_eval.json"
# Reasons that mean the run did not really exercise Jev on a claim it should have scored.
DEGENERATE_REASONS = frozenset({"too_large", "waf_blocked", "jev_error", "pii", "secret",
                                "degraded_anchor"})
EVAL_SEED = "jev-live-eval"
REAL_CORPUS_NOTE = (
    "Real-corpus skip rates will be lower than this fixture's: PII patterns (compact 10-digit "
    "phone numbers that also match case numbers, MM/DD/YYYY birthdate-format dates) withhold "
    "whole claims before Jev sees them.")


def _fmt(value: float | None) -> str:
    return "  -  " if value is None else f"{value:.3f}"


def test_live_prescreen_eval_gates_and_approves_model():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    meta = {item["claim_id"]: item for item in raw}
    claims = parse_claims([{k: v for k, v in item.items() if k not in ("expect", "category")}
                           for item in raw])

    out = prescreen(claims, env=os.environ, seed=EVAL_SEED, enforce_model_gate=False)

    assert out["enabled"] is True
    print(f"\nJev live eval: model {out['model']} | thresholds presence >= {PRESENCE_MIN}, "
          f"entailment >= {ENTAIL_MIN}")
    for claim_id, entry in out["claims"].items():
        item = meta[claim_id]
        print(f"  {claim_id} {item['category']:<22} expect={item['expect']:<11} "
              f"presence={_fmt(entry['presence'])} entailment={_fmt(entry['entailment'])} "
              f"route={entry['route']} reason={entry['reason']}")
    print(f"  usage: {out['usage']}")

    degenerate = {cid: e["reason"] for cid, e in out["claims"].items()
                  if e["reason"] in DEGENERATE_REASONS}
    assert not degenerate, f"degenerate live run (no model approved): {degenerate}"
    assert out["model"], "no window reported a model; nothing was answered"

    leaked = [cid for cid, e in out["claims"].items()
              if meta[cid]["expect"] == "unsupported" and e["route"] != ROUTE_VERIFY]
    assert not leaked, f"GATE FAILED: unsupported claims routed skip: {leaked}"

    supported = [cid for cid in out["claims"] if meta[cid]["expect"] == "supported"]
    skipped = [cid for cid in supported if out["claims"][cid]["route"] == ROUTE_SKIP]
    skip_rate = len(skipped) / len(supported)
    print(f"  skip rate over supported items: {len(skipped)}/{len(supported)} = {skip_rate:.2f}")
    print(f"  {REAL_CORPUS_NOTE}")

    record = jev_state.record_passing_model(
        out["model"],
        eval_summary={"n": len(raw), "skip_rate": skip_rate, "presence_min": PRESENCE_MIN,
                      "entail_min": ENTAIL_MIN},
        path=jev_state.STATE_PATH)
    print(f"  approved model recorded in {jev_state.STATE_PATH.name}: {record['approved_model']}")
    assert jev_state.approved_model(jev_state.STATE_PATH) == out["model"]

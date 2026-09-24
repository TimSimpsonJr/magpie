"""ASCII only. LIVE Part B smoke (Task 14): jev_ask against real Jev on synthetic records.

Selected only with ``-m jev_live`` (pyproject addopts deselect it otherwise, and CI never runs
it) and skipped unless the operator's environment already has ``MAGPIE_JEV=1`` and
``OPENROUTER_API_KEY``. It sends only the ``text`` field of the 20 SYNTHETIC records in
``tests/fixtures/jev/ask_smoke.jsonl`` (under R-keys; ids and ``label`` never leave the
machine) plus the question and the options / levels fixtures.

- choice smoke: at least 18 of 20 answered and ``ACCURACY_FLOOR`` over the answered ones;
- score smoke: exercises the ``levels`` request key (the choice smoke exercises ``options``);
  at least 18 answered, every value finite, and surveillance records score higher on average.

The model-approval state is a tmp file, so the real ``data/jev_state.json`` is never read or
written here (only the Part A live eval approves a model).
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import jev_ask
from scripts.jev_client import jev_status

pytestmark = [
    pytest.mark.jev_live,
    pytest.mark.skipif(not jev_status()[0],
                       reason="live Jev smoke needs MAGPIE_JEV=1 and OPENROUTER_API_KEY"),
]

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jev"
SMOKE = FIXTURES / "ask_smoke.jsonl"
# At least 16 of 20 answered records labeled correctly.
ACCURACY_FLOOR = 0.80
# At most 2 of 20 records may come back skipped.
MIN_ANSWERED = 18
FIXED_NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _labels() -> dict[str, str]:
    rows = [json.loads(line) for line in SMOKE.read_text(encoding="utf-8").splitlines()]
    return {r["id"]: r["label"] for r in rows}


def _run(tmp_path: Path, qtype: str, extra: list[str]) -> list[dict]:
    out = tmp_path / f"{qtype}.jsonl"
    code = jev_ask.main(
        ["--input", str(SMOKE), "--id-field", "id", "--text-fields", "text",
         "--type", qtype, "--question", "What is this municipal record mainly about?",
         *extra, "--out", str(out)],
        env=os.environ, state_path=tmp_path / "jev_state.json", now=lambda: FIXED_NOW)
    assert code == jev_ask.EXIT_OK
    meta_path, _sample = jev_ask.derived_paths(out)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    print(f"\njev_ask {qtype}: model {meta['model']} | counts {meta['counts']} | "
          f"usage {meta['usage']}")
    assert meta["model"]
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]


def test_live_ask_choice_smoke(tmp_path):
    labels = _labels()
    rows = _run(tmp_path, "choice", ["--options", str(FIXTURES / "ask_smoke_options.json")])
    answered = [r for r in rows if "answer" in r]
    for r in rows:
        got = r["answer"]["label"] if "answer" in r else f"skipped:{r['skipped']}"
        print(f"  {r['id']} expect={labels[r['id']]:<12} got={got}")
    assert len(answered) >= MIN_ANSWERED, f"only {len(answered)} of {len(rows)} answered"
    correct = sum(1 for r in answered if r["answer"]["label"] == labels[r["id"]])
    accuracy = correct / len(answered)
    print(f"  accuracy {correct}/{len(answered)} = {accuracy:.2f} (floor {ACCURACY_FLOOR})")
    assert accuracy >= ACCURACY_FLOOR


def test_live_ask_score_smoke(tmp_path):
    labels = _labels()
    rows = _run(tmp_path, "score", ["--levels", str(FIXTURES / "ask_smoke_levels.json")])
    answered = [r for r in rows if "answer" in r]
    for r in rows:
        got = r["answer"]["value"] if "answer" in r else f"skipped:{r['skipped']}"
        print(f"  {r['id']} label={labels[r['id']]:<12} score={got}")
    assert len(answered) >= MIN_ANSWERED, f"only {len(answered)} of {len(rows)} answered"
    assert all(math.isfinite(r["answer"]["value"]) for r in answered)
    surv = [r["answer"]["value"] for r in answered if labels[r["id"]] == "surveillance"]
    rest = [r["answer"]["value"] for r in answered if labels[r["id"]] != "surveillance"]
    assert surv and rest
    print(f"  mean score: surveillance {sum(surv) / len(surv):.2f} | "
          f"other records {sum(rest) / len(rest):.2f}")
    assert sum(surv) / len(surv) > sum(rest) / len(rest)

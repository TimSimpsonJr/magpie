"""ASCII only. Offline tests for Part A spot-checks, the disagreement log and the run summary
in scripts/jev_prescreen.py (Task 7).

Every Jev call goes through FakeTransport; the disagreement log and model-approval state always
live in tmp_path, never the real data/ dir.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import jev_client as jc
from scripts import jev_prescreen as jp
from scripts import jev_state
from scripts.jev_prescreen import (
    ClaimInput,
    default_seed,
    log_spotcheck_disagreements,
    normalize_verdicts,
    prescreen,
    run_summary,
    select_spot_checks,
    spot_hash,
)
from tests.helpers.jev_fakes import FakeTransport, noul_responder

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "jev_prescreen.py"
ENV = {"MAGPIE_JEV": "1", "OPENROUTER_API_KEY": "k"}
LOG_KEYS = {"ts", "claim_id", "presence", "entailment", "model", "verdict", "seed"}
NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)

SPAN = "Officer Ramirez ran searches on the county system after the policy changed."
QUOTE = "Officer Ramirez ran searches on the county system"
CLAIM = "Officer Ramirez ran searches on the county system."


def mk(claim_id: str, claim_text: str = CLAIM, clean_citation: bool = True) -> ClaimInput:
    return ClaimInput(claim_id=claim_id, claim_text=claim_text, verbatim_quote=QUOTE,
                      span=SPAN, clean_citation=clean_citation)


@pytest.fixture
def approved(tmp_path) -> Path:
    path = tmp_path / "state" / "jev_state.json"
    jev_state.record_passing_model("m1", eval_summary={"n": 20}, path=path)
    return path


def fake_ask(fake: FakeTransport):
    return lambda state, questions: jc.ask(state, questions, env=ENV, transport=fake)


def sample_output() -> dict:
    """5 claims: 3 skip (c1 spot-checked), 2 verify."""
    def claim(route, reason, spot, p=0.95, e=0.95):
        return {"presence": p, "entailment": e, "route": route, "reason": reason,
                "spot_check": spot}
    return {
        "enabled": True, "model": "m1", "approved_model": "m1", "seed": "s",
        "claims": {
            "c1": claim("skip", None, True),
            "c2": claim("skip", None, False),
            "c3": claim("skip", None, False),
            "c4": claim("verify", "low_score", False, 0.5, 0.5),
            "c5": claim("verify", "degraded_anchor", False, None, None),
        },
        "summary": {}, "usage": {"input_tokens": 0, "output_tokens": 0, "cost": 0},
    }


def read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------- selection

def test_spot_hash_is_deterministic_and_in_unit_range():
    assert spot_hash("K1", "s") == spot_hash("K1", "s")
    assert 0.0 <= spot_hash("K1", "s") <= 1.0


def test_rate_zero_still_selects_one():
    ids = ["a", "b", "c"]
    selected = select_spot_checks(ids, "s", rate=0.0)
    assert len(selected) == jp.MIN_SPOT_CHECKS == 1
    assert selected <= set(ids)
    assert selected == {min(ids, key=lambda i: spot_hash(i, "s"))}


def test_rate_one_selects_all():
    ids = ["a", "b", "c"]
    assert select_spot_checks(ids, "s", rate=1.0) == set(ids)


def test_default_rate_selects_about_ten_percent():
    ids = [f"c{i:04d}" for i in range(1000)]
    fraction = len(select_spot_checks(ids, "s")) / len(ids)
    assert 0.07 <= fraction <= 0.13
    assert jp.SPOT_CHECK_RATE == 0.10


def test_different_seeds_select_differently():
    ids = [f"c{i:03d}" for i in range(200)]
    assert select_spot_checks(ids, "a") != select_spot_checks(ids, "b")


def test_no_skip_ids_selects_nothing():
    assert select_spot_checks([], "s") == set()
    assert select_spot_checks([], "s", rate=0.0) == set()


def test_spotcheck_log_default_path_is_under_data_dir():
    assert jp.SPOTCHECK_LOG == jev_state.DATA_DIR / "jev_spotcheck.jsonl"


# ---------------------------------------------------------------- prescreen wiring

def test_prescreen_marks_spot_checks_only_on_skip_claims(approved):
    claims = [mk("c1"), mk("c2"), mk("c3"),
              mk("c4", clean_citation=False),                       # degraded_anchor
              mk("c5", claim_text="The county total was 12 searches.")]  # numeric gate
    fake = FakeTransport(noul_responder(lambda qid: 0.95))
    out = prescreen(claims, env=ENV, ask_fn=fake_ask(fake), state_path=approved, seed="run1")
    routes = {cid: c["route"] for cid, c in out["claims"].items()}
    assert routes == {"c1": "skip", "c2": "skip", "c3": "skip", "c4": "verify", "c5": "verify"}
    spotted = {cid for cid, c in out["claims"].items() if c["spot_check"]}
    assert spotted
    assert spotted <= {"c1", "c2", "c3"}
    assert spotted == select_spot_checks(["c1", "c2", "c3"], "run1")
    assert out["seed"] == "run1"
    assert out["summary"]["spot_checked"] == len(spotted)


def test_prescreen_default_seed_is_content_derived(approved):
    claims = [mk("c1"), mk("c2")]
    fake = FakeTransport(noul_responder(lambda qid: 0.95))
    out = prescreen(claims, env=ENV, ask_fn=fake_ask(fake), state_path=approved)
    assert out["seed"] == default_seed(claims)


def test_prescreen_no_spot_checks_when_nothing_skips(approved):
    claims = [mk("c1"), mk("c2")]
    fake = FakeTransport(noul_responder(lambda qid: 0.10))
    out = prescreen(claims, env=ENV, ask_fn=fake_ask(fake), state_path=approved)
    assert not any(c["spot_check"] for c in out["claims"].values())


def test_prescreen_model_changed_has_no_spot_checks(tmp_path):
    fake = FakeTransport(noul_responder(lambda qid: 0.95, model="m2"))
    state = tmp_path / "state" / "jev_state.json"
    jev_state.record_passing_model("m1", eval_summary={"n": 20}, path=state)
    out = prescreen([mk("c1"), mk("c2")], env=ENV, ask_fn=fake_ask(fake), state_path=state)
    assert all(c["route"] == "verify" and not c["spot_check"] for c in out["claims"].values())


def test_prescreen_jev_off_has_no_spot_checks(tmp_path):
    out = prescreen([mk("c1")], env={}, state_path=tmp_path / "s.json")
    assert out["claims"]["c1"]["spot_check"] is False


def test_default_seed_tracks_content():
    a = [mk("c1"), mk("c2")]
    assert default_seed(a) == default_seed([mk("c1"), mk("c2")])
    assert default_seed(a) == default_seed([mk("c2"), mk("c1")])
    assert default_seed(a) != default_seed([mk("c1"), mk("c2", claim_text="Something else.")])


# ---------------------------------------------------------------- verdicts + log

def test_normalize_verdicts_accepts_both_shapes():
    raw = {"c1": "supported", "c2": {"result": "indeterminate", "confidence": 0.4}}
    assert normalize_verdicts(raw) == {"c1": "supported", "c2": "indeterminate"}


@pytest.mark.parametrize("raw", [[], "supported", {"c1": 3}, {"c1": {"confidence": 0.5}},
                                 {"c1": {"result": 1}}])
def test_normalize_verdicts_rejects_bad_shapes(raw):
    with pytest.raises(ValueError):
        normalize_verdicts(raw)


def test_supported_verdict_writes_nothing(tmp_path):
    log = tmp_path / "logs" / "spot.jsonl"
    lines = log_spotcheck_disagreements(sample_output(), {"c1": "supported"},
                                        log_path=log, now=NOW)
    assert lines == []
    assert read_log(log) == []


def test_indeterminate_verdict_logs_one_line_without_claim_text(tmp_path):
    log = tmp_path / "logs" / "spot.jsonl"
    lines = log_spotcheck_disagreements(sample_output(), {"c1": "indeterminate"},
                                        log_path=log, now=NOW)
    logged = read_log(log)
    assert len(lines) == 1 and logged == lines
    line = logged[0]
    assert set(line) == LOG_KEYS
    assert line == {"ts": NOW.isoformat(), "claim_id": "c1", "presence": 0.95,
                    "entailment": 0.95, "model": "m1", "verdict": "indeterminate",
                    "seed": "s"}
    assert CLAIM not in log.read_text(encoding="utf-8")


def test_missing_verdict_counts_as_disagreement(tmp_path):
    log = tmp_path / "spot.jsonl"
    lines = log_spotcheck_disagreements(sample_output(), {}, log_path=log, now=NOW)
    assert [line["verdict"] for line in lines] == ["missing"]
    assert read_log(log)[0]["verdict"] == "missing"


def test_verdicts_for_non_spot_checked_claims_are_ignored(tmp_path):
    log = tmp_path / "spot.jsonl"
    verdicts = {"c1": "supported", "c2": "contradicted", "c4": "contradicted"}
    assert log_spotcheck_disagreements(sample_output(), verdicts, log_path=log, now=NOW) == []
    assert read_log(log) == []


def test_log_appends_across_calls(tmp_path):
    log = tmp_path / "spot.jsonl"
    log_spotcheck_disagreements(sample_output(), {"c1": "contradicted"}, log_path=log, now=NOW)
    log_spotcheck_disagreements(sample_output(), {}, log_path=log, now=NOW)
    assert [line["verdict"] for line in read_log(log)] == ["contradicted", "missing"]


def test_log_defaults_now_to_utc(tmp_path):
    log = tmp_path / "spot.jsonl"
    lines = log_spotcheck_disagreements(sample_output(), {}, log_path=log)
    assert datetime.fromisoformat(lines[0]["ts"]).tzinfo is not None


# ---------------------------------------------------------------- summary

def test_run_summary_format():
    assert run_summary(sample_output(), 1) == (
        "5 claims pre-screened | 3 skipped | 3 sent to extraction-verifier | "
        "1 spot-checked | 1 disagreements")


def test_run_summary_is_ascii():
    run_summary(sample_output(), 0).encode("ascii")


# ---------------------------------------------------------------- CLI

def _cli(args: list[str], log: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in ("MAGPIE_JEV", "OPENROUTER_API_KEY")}
    env["MAGPIE_JEV_SPOTCHECK_LOG"] = str(log)
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                          env=env, cwd=str(REPO_ROOT), timeout=60)


def test_cli_spotcheck_appends_log_and_prints_summary(tmp_path):
    out_path = tmp_path / "out.json"
    out_path.write_text(json.dumps(sample_output()), encoding="utf-8")
    verdicts = tmp_path / "v.json"
    verdicts.write_text(json.dumps({"c1": {"result": "contradicted", "confidence": 0.8}}),
                        encoding="utf-8")
    log = tmp_path / "logs" / "spot.jsonl"
    proc = _cli(["--spotcheck", str(out_path), "--verdicts", str(verdicts)], log)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.decode("utf-8").strip() == (
        "5 claims pre-screened | 3 skipped | 3 sent to extraction-verifier | "
        "1 spot-checked | 1 disagreements")
    logged = read_log(log)
    assert len(logged) == 1 and logged[0]["verdict"] == "contradicted"


@pytest.mark.parametrize("bad", ["out", "verdicts"])
def test_cli_spotcheck_bad_files_exit_2(tmp_path, bad):
    out_path = tmp_path / "out.json"
    verdicts = tmp_path / "v.json"
    out_path.write_text("{not json" if bad == "out" else json.dumps(sample_output()),
                        encoding="utf-8")
    verdicts.write_text("[1, 2]" if bad == "verdicts" else "{}", encoding="utf-8")
    log = tmp_path / "spot.jsonl"
    proc = _cli(["--spotcheck", str(out_path), "--verdicts", str(verdicts)], log)
    assert proc.returncode == 2
    assert not log.exists()


def test_cli_spotcheck_missing_file_exits_2(tmp_path):
    proc = _cli(["--spotcheck", str(tmp_path / "nope.json"), "--verdicts",
                 str(tmp_path / "nope2.json")], tmp_path / "spot.jsonl")
    assert proc.returncode == 2


def test_cli_spotcheck_requires_verdicts(tmp_path):
    out_path = tmp_path / "out.json"
    out_path.write_text(json.dumps(sample_output()), encoding="utf-8")
    proc = _cli(["--spotcheck", str(out_path)], tmp_path / "spot.jsonl")
    assert proc.returncode == 2


def test_cli_without_claims_or_spotcheck_exits_2(tmp_path):
    proc = _cli([], tmp_path / "spot.jsonl")
    assert proc.returncode == 2

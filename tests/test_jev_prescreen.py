"""ASCII only. Offline tests for the Part A routing + CLI in scripts/jev_prescreen.py (Task 6).

Every Jev call goes through FakeTransport (tests/helpers/jev_fakes.py); nothing touches the
network. The model-approval state always lives in tmp_path, never the real data/ dir.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import jev_client as jc
from scripts import jev_guards as jg
from scripts import jev_prescreen as jp
from scripts import jev_state
from scripts.jev_prescreen import ClaimInput, build_questions, item_key, parse_claims, prescreen
from tests.helpers.jev_fakes import (
    FakeTransport,
    noul_responder,
    raw_answers_responder,
    status_responder,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "jev_prescreen.py"
ENV = {"MAGPIE_JEV": "1", "OPENROUTER_API_KEY": "k"}
CLAIM_KEYS = {"presence", "entailment", "route", "reason", "spot_check"}
TOP_KEYS = {"enabled", "model", "approved_model", "seed", "claims", "summary", "usage"}

SPAN = "Officer Ramirez ran searches on the county system after the policy changed."
QUOTE = "Officer Ramirez ran searches on the county system"
CLAIM = "Officer Ramirez ran searches on the county system."

TOO_LARGE_BODY = json.dumps(
    {"error": {"message": "state rejected: max_tokens_exceeded"}}).encode("utf-8")
WAF_BODY = b"<!DOCTYPE html><title>Attention Required! | Cloudflare</title>"
HTTP_500_BODY = json.dumps({"error": {"message": "internal"}}).encode("utf-8")


def mk(claim_id: str = "c1", claim_text: str = CLAIM, verbatim_quote: str = QUOTE,
       span: str | None = SPAN, clean_citation: bool = True) -> ClaimInput:
    return ClaimInput(claim_id=claim_id, claim_text=claim_text, verbatim_quote=verbatim_quote,
                      span=span, clean_citation=clean_citation)


@pytest.fixture
def approved(tmp_path) -> Path:
    path = tmp_path / "state" / "jev_state.json"
    jev_state.record_passing_model("m1", eval_summary={"n": 20}, path=path)
    return path


@pytest.fixture
def no_state(tmp_path) -> Path:
    return tmp_path / "missing" / "jev_state.json"


def fake_ask(fake: FakeTransport, env: dict = ENV):
    return lambda state, questions: jc.ask(state, questions, env=env, transport=fake)


def scores(presence: float, entail: float):
    return lambda qid: presence if qid.startswith("presence_") else entail


def run(claims, fake, state_path, **kw):
    return prescreen(claims, env=kw.pop("env", ENV), ask_fn=fake_ask(fake),
                     state_path=state_path, **kw)


def routes(out: dict) -> dict[str, tuple[str, str | None]]:
    return {cid: (c["route"], c["reason"]) for cid, c in out["claims"].items()}


# ---------------------------------------------------------------- keys + questions

def test_item_key_is_positional():
    assert item_key(0) == "K01"
    assert item_key(8) == "K09"
    assert item_key(98) == "K99"
    assert item_key(99) == "K100"


def test_build_questions_match_wire_block():
    qs = build_questions("K01")
    assert list(qs) == ["presence_K01", "entail_K01"]
    assert qs["presence_K01"] == {
        "type": "noul",
        "instructions": "Does the span for K01 contain the quoted text or an equivalent passage?",
        "criteria": {
            "true": "The quote for K01, or a faithful rendering of it, appears in the span for K01.",
            "false": "The quote for K01 is absent from the span, or the span has only similar "
                     "wording about something else.",
        },
    }
    assert qs["entail_K01"] == {
        "type": "noul",
        "instructions": "Does the span for K01 support the claim for K01 as stated?",
        "criteria": {
            "true": "The span for K01 states or directly implies the claim, including its names, "
                    "numbers and dates.",
            "false": "The span for K01 is silent on the claim, contradicts it, or supports only a "
                     "weaker or different claim.",
        },
    }


def test_constants():
    assert jp.PRESENCE_MIN == 0.85 and jp.ENTAIL_MIN == 0.85
    assert jp.STATE_KEY == "claims"
    assert jp.REASONS == ("jev_off", "model_changed", "pii", "secret", "too_large",
                          "waf_blocked", "jev_error", "degraded_anchor", "numeric_mismatch",
                          "computed_value", "low_score")


# ---------------------------------------------------------------- opt-in + model gate

def test_jev_off_routes_everything_to_verify_without_a_call(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.99))
    out = prescreen([mk("a"), mk("b")], env={}, ask_fn=fake_ask(fake), state_path=approved)
    assert out["enabled"] is False
    assert out["model"] is None
    assert routes(out) == {"a": ("verify", "jev_off"), "b": ("verify", "jev_off")}
    assert fake.count == 0


def test_supported_claim_skips(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    out = run([mk("a")], fake, approved)
    assert out["enabled"] is True
    assert out["model"] == "m1"
    assert out["approved_model"] == "m1"
    # The sole skip claim is spot-checked by the MIN_SPOT_CHECKS floor (Task 7).
    assert out["claims"]["a"] == {"presence": 0.95, "entailment": 0.95, "route": "skip",
                                  "reason": None, "spot_check": True}
    assert fake.count == 1


def test_changed_model_routes_every_claim_including_guarded(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.99, model="m2"))
    guarded = mk("pii", span="Call 864-555-0100 for details.")
    out = run([mk("a"), guarded], fake, approved)
    assert out["model"] == "m2"
    assert routes(out) == {"a": ("verify", "model_changed"), "pii": ("verify", "model_changed")}


def test_no_state_file_is_model_changed(no_state):
    fake = FakeTransport(noul_responder(lambda q: 0.99))
    out = run([mk("a"), mk("b", clean_citation=False)], fake, no_state)
    assert out["approved_model"] is None
    assert routes(out) == {"a": ("verify", "model_changed"), "b": ("verify", "model_changed")}


def test_no_model_returned_keeps_real_reasons(no_state):
    fake = FakeTransport(status_responder(500, HTTP_500_BODY))
    out = run([mk("a"), mk("b")], fake, no_state)
    assert out["model"] is None
    assert routes(out) == {"a": ("verify", "jev_error"), "b": ("verify", "jev_error")}


def test_too_large_window_keeps_local_reasons(approved):
    fake = FakeTransport(status_responder(400, TOO_LARGE_BODY))
    claims = [mk("sent"), mk("degraded", clean_citation=False),
              mk("num", claim_text="ran 483 searches", span="ran 482 searches")]
    out = run(claims, fake, approved)
    assert out["model"] is None
    assert routes(out) == {"sent": ("verify", "too_large"),
                           "degraded": ("verify", "degraded_anchor"),
                           "num": ("verify", "numeric_mismatch")}


def test_model_gate_off_allows_skip_without_state(no_state):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    out = run([mk("a")], fake, no_state, enforce_model_gate=False)
    assert routes(out) == {"a": ("skip", None)}
    assert out["model"] == "m1"


def test_any_unapproved_model_across_windows_is_model_changed(approved):
    # Two ~8k-token claims cannot share one 14k window, so two calls happen.
    big_span = "The officer logged the search in the county system. " * 620
    models = iter(["m1", "m2"])

    def respond(body):
        model = next(models)
        return noul_responder(lambda q: 0.99, model=model)(body)

    fake = FakeTransport(respond)
    out = run([mk("a", span=big_span), mk("b", span=big_span)], fake, approved)
    assert fake.count == 2
    assert out["model"] == "m1"
    assert routes(out) == {"a": ("verify", "model_changed"), "b": ("verify", "model_changed")}


# ---------------------------------------------------------------- local reasons

def test_pii_in_span_is_never_sent(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    out = run([mk("pii", span="Call 864-555-0100 about the searches."), mk("ok")], fake, approved)
    assert routes(out)["pii"] == ("verify", "pii")
    assert out["claims"]["pii"]["presence"] is None
    assert out["claims"]["pii"]["entailment"] is None
    assert fake.count >= 1
    assert all(b"864-555-0100" not in raw for raw in fake.raw)


def test_secret_in_claim_is_never_sent(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    out = run([mk("sec", claim_text="The config had password=hunter2 set."), mk("ok")],
              fake, approved)
    assert routes(out)["sec"] == ("verify", "secret")
    assert fake.count >= 1
    assert all(b"hunter2" not in raw for raw in fake.raw)


def test_degraded_anchor_is_not_sent(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    out = run([mk("d", clean_citation=False)], fake, approved)
    assert routes(out) == {"d": ("verify", "degraded_anchor")}
    assert fake.count == 0


@pytest.mark.parametrize("span", [None, "", "   "])
def test_clean_citation_with_missing_span_is_degraded(approved, span):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    out = run([mk("d", span=span)], fake, approved)
    assert routes(out) == {"d": ("verify", "degraded_anchor")}
    assert fake.count == 0


def test_numeric_mismatch_is_not_sent(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    out = run([mk("n", claim_text="Officer Ramirez ran 483 searches.",
                  span="Officer Ramirez ran 482 searches.")], fake, approved)
    assert routes(out) == {"n": ("verify", "numeric_mismatch")}
    assert fake.count == 0


def test_computed_value_is_not_sent(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    out = run([mk("t", claim_text="The total searches were logged.",
                  span="The total searches were logged.")], fake, approved)
    assert routes(out) == {"t": ("verify", "computed_value")}
    assert fake.count == 0


def test_pii_module_failure_sends_nothing(approved, monkeypatch):
    jg._pii_patterns.cache_clear()
    try:
        def boom(name, *a, **k):
            raise ImportError("no pandas")
        monkeypatch.setattr(jg.importlib, "import_module", boom)
        fake = FakeTransport(noul_responder(lambda q: 0.95))
        out = run([mk("a"), mk("b")], fake, approved)
        assert routes(out) == {"a": ("verify", "pii"), "b": ("verify", "pii")}
        assert fake.count == 0
    finally:
        jg._pii_patterns.cache_clear()


# ---------------------------------------------------------------- scores

@pytest.mark.parametrize("presence,entail,expected", [
    (0.95, 0.84, ("verify", "low_score")),
    (0.84, 0.95, ("verify", "low_score")),
    (0.85, 0.85, ("skip", None)),
])
def test_score_thresholds(approved, presence, entail, expected):
    fake = FakeTransport(noul_responder(scores(presence, entail)))
    out = run([mk("a")], fake, approved)
    assert routes(out) == {"a": expected}
    assert out["claims"]["a"]["presence"] == presence
    assert out["claims"]["a"]["entailment"] == entail


# ---------------------------------------------------------------- Jev failures

@pytest.mark.parametrize("responder,reason", [
    (status_responder(400, TOO_LARGE_BODY), "too_large"),
    (status_responder(403, WAF_BODY), "waf_blocked"),
    (status_responder(500, HTTP_500_BODY), "jev_error"),
    (raw_answers_responder({}), "jev_error"),  # bad_shape: requested ids missing
])
def test_window_failures_map_to_public_reasons(approved, responder, reason):
    fake = FakeTransport(responder)
    out = run([mk("a")], fake, approved)
    assert routes(out) == {"a": ("verify", reason)}
    assert out["claims"]["a"]["presence"] is None


def test_oversize_claim_is_never_sent(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    huge = ("The officer logged the search in the county system. " * 1200)[:60_000]
    out = run([mk("big", span=huge)], fake, approved)
    assert routes(out) == {"big": ("verify", "too_large")}
    assert fake.count == 0


def test_unexpected_ask_fn_exception_fails_to_verify(approved):
    def broken(state, questions):
        raise RuntimeError("bug in a custom ask_fn")

    out = prescreen([mk("a"), mk("d", clean_citation=False)], env=ENV, ask_fn=broken,
                    state_path=approved)
    assert routes(out) == {"a": ("verify", "jev_error"), "d": ("verify", "degraded_anchor")}
    assert out["model"] is None


def test_default_ask_fn_uses_given_env(approved, monkeypatch):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    monkeypatch.setattr(jc, "_urllib_transport", fake)
    out = prescreen([mk("a")], env=ENV, state_path=approved)
    assert fake.count == 1
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer k"
    assert routes(out) == {"a": ("skip", None)}


# ---------------------------------------------------------------- request body

def test_claim_ids_never_leave_the_machine(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    run([mk("doc-abc123:c1")], fake, approved)
    body = fake.bodies[0]
    assert list(body["state"]) == ["claims"]
    assert list(body["state"]["claims"]) == ["K01"]
    assert body["state"]["claims"]["K01"] == {"claim": CLAIM, "quote": QUOTE, "span": SPAN}
    assert all(b"doc-abc123" not in raw for raw in fake.raw)


def test_request_questions(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    run([mk("a")], fake, approved)
    questions = fake.bodies[0]["questions"]
    assert set(questions) == {"presence_K01", "entail_K01"}
    assert all(q["type"] == "noul" for q in questions.values())
    assert questions == build_questions("K01")


def test_only_unguarded_claims_are_sent_with_positional_keys(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    run([mk("d", clean_citation=False), mk("ok")], fake, approved)
    assert list(fake.bodies[0]["state"]["claims"]) == ["K02"]


# ---------------------------------------------------------------- output shape

def test_output_shape(approved):
    fake = FakeTransport(noul_responder(scores(0.95, 0.5)))
    claims = [mk("a"), mk("d", clean_citation=False)]
    out = run(claims, fake, approved, seed="S")
    assert set(out) == TOP_KEYS
    assert out["seed"] == "S"
    for entry in out["claims"].values():
        assert set(entry) == CLAIM_KEYS
        assert entry["spot_check"] is False
        assert (entry["reason"] is None) == (entry["route"] == "skip")
        assert entry["reason"] is None or entry["reason"] in jp.REASONS
    assert out["summary"] == {"prescreened": 2, "skipped": 0, "verify": 2, "spot_checked": 0}
    assert set(out["usage"]) == {"input_tokens", "output_tokens", "cost"}
    json.dumps(out)  # JSON-able


def test_summary_counts_and_default_seed(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    out = run([mk("a"), mk("b"), mk("d", clean_citation=False)], fake, approved)
    spotted = sum(1 for c in out["claims"].values() if c["spot_check"])
    assert spotted == len(jp.select_spot_checks(["a", "b"], out["seed"])) >= 1
    assert out["summary"] == {"prescreened": 3, "skipped": 2, "verify": 1,
                              "spot_checked": spotted}
    assert isinstance(out["seed"], str) and out["seed"]
    again = run([mk("a"), mk("b"), mk("d", clean_citation=False)], fake, approved)
    assert again["seed"] == out["seed"]


def test_empty_batch(approved):
    fake = FakeTransport(noul_responder(lambda q: 0.95))
    out = run([], fake, approved)
    assert out["claims"] == {}
    assert out["summary"] == {"prescreened": 0, "skipped": 0, "verify": 0, "spot_checked": 0}
    assert fake.count == 0


# ---------------------------------------------------------------- parse_claims

def _raw(**over):
    base = {"claim_id": "c1", "claim_text": CLAIM, "verbatim_quote": QUOTE, "span": SPAN,
            "clean_citation": True}
    base.update(over)
    return base


def test_parse_claims_ok():
    parsed = parse_claims([_raw(), _raw(claim_id="c2", span=None, clean_citation=False)])
    assert parsed == [mk("c1"), mk("c2", span=None, clean_citation=False)]


@pytest.mark.parametrize("raw", [
    {"claims": []},
    [_raw(), "not an object"],
    [{k: v for k, v in _raw().items() if k != "verbatim_quote"}],
    [_raw(), _raw()],
    [_raw(clean_citation="yes")],
    [_raw(clean_citation=1)],
    [_raw(claim_id="")],
    [_raw(claim_id=7)],
    [_raw(claim_text=None)],
    [_raw(span=12)],
])
def test_parse_claims_rejects(raw):
    with pytest.raises(ValueError):
        parse_claims(raw)


# ---------------------------------------------------------------- CLI

def _env_without_jev() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("MAGPIE_JEV", "OPENROUTER_API_KEY")}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=str(REPO_ROOT),
                          env=_env_without_jev(), capture_output=True, timeout=120)


def test_cli_jev_off(tmp_path):
    path = tmp_path / "claims.json"
    path.write_text(json.dumps([_raw(), _raw(claim_id="c2")]), encoding="utf-8")
    p = _cli(str(path), "--seed", "S")
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    out = json.loads(p.stdout.decode("utf-8"))
    assert out["enabled"] is False
    assert out["seed"] == "S"
    assert {c["reason"] for c in out["claims"].values()} == {"jev_off"}
    assert set(out["claims"]) == {"c1", "c2"}


@pytest.mark.parametrize("content", ["{not json", json.dumps({"claims": []}),
                                     json.dumps([_raw(clean_citation="yes")])])
def test_cli_bad_input_exits_2(tmp_path, content):
    path = tmp_path / "claims.json"
    path.write_text(content, encoding="utf-8")
    p = _cli(str(path))
    assert p.returncode == 2
    assert p.stdout == b""
    assert p.stderr


def test_cli_missing_file_exits_2(tmp_path):
    p = _cli(str(tmp_path / "nope.json"))
    assert p.returncode == 2


# --- Task 14: live-eval fixture checks + jev_live deselection (offline) --------------------

EVAL_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "jev" / "prescreen_eval.json"
EVAL_CATEGORY_COUNTS = {"supported": 5, "paraphrased_supported": 3, "wrong_number": 3,
                        "wrong_date": 2, "wrong_entity": 3, "span_silent": 2, "contradicted": 2}
SUPPORTED_CATEGORIES = {"supported", "paraphrased_supported"}
LOCALLY_GATED_CATEGORIES = {"wrong_number", "wrong_date"}


def _eval_items() -> list[dict]:
    return json.loads(EVAL_FIXTURE.read_text(encoding="utf-8"))


def test_eval_fixture_parses_and_has_the_planned_mix():
    raw = _eval_items()
    assert len(raw) == 20
    stripped = [{k: v for k, v in item.items() if k not in ("expect", "category")}
                for item in raw]
    claims = parse_claims(stripped)
    assert len(claims) == 20
    assert all(c.clean_citation for c in claims)
    counts: dict[str, int] = {}
    for item in raw:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    assert counts == EVAL_CATEGORY_COUNTS
    for item in raw:
        expected = "supported" if item["category"] in SUPPORTED_CATEGORIES else "unsupported"
        assert item["expect"] == expected, item["claim_id"]
        assert item["verbatim_quote"] in item["span"], item["claim_id"]
    assert EVAL_FIXTURE.read_bytes().isascii()


def test_eval_fixture_local_reasons_match_categories():
    """No item may trip the PII/secret guard (a guarded eval would approve nothing useful);
    supported items and every entity/silent/contradicted item reach Jev; wrong numbers and
    dates are caught by the local gate."""
    claims = {c.claim_id: c for c in parse_claims(_eval_items())}
    sent_with_matching_numbers = 0
    for item in _eval_items():
        reason = jp.local_reason(claims[item["claim_id"]])
        if item["category"] in LOCALLY_GATED_CATEGORIES:
            assert reason == "numeric_mismatch", item["claim_id"]
        else:
            assert reason is None, (item["claim_id"], reason)
            if item["category"] in ("wrong_entity", "span_silent") and \
                    jp.NUMBER_RE.search(item["claim_text"]):
                sent_with_matching_numbers += 1
    assert sent_with_matching_numbers >= 2


def test_default_pytest_run_deselects_jev_live():
    """The pyproject addopts keep a bare pytest run from ever selecting a live test. The Jev
    env vars are stripped too, so a broken addopts still could not reach the network."""
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_jev_live_prescreen.py",
         "tests/test_jev_live_ask.py", "-q", "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT), env=_env_without_jev(), capture_output=True, timeout=300)
    text = p.stdout.decode("utf-8", "replace")
    assert p.returncode == 5, text  # 5 = no tests ran
    assert "deselected" in text
    assert " passed" not in text and " skipped" not in text

"""ASCII only. Offline tests for scripts/jev_client.py (Task 1). Every call goes through
FakeTransport, or (redirect tests) a fake HTTPS handler inside the real opener; nothing
touches the network."""
from __future__ import annotations

import email.message
import io
import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path

import pytest

from scripts import jev_client as jc
from scripts.jev_client import JevUnavailable, ask, jev_status, public_reason
from tests.helpers.jev_fakes import (
    FakeTransport,
    choice_responder,
    noul_responder,
    raw_answers_responder,
    score_responder,
    sequence_responder,
    status_responder,
)

ENV = {"MAGPIE_JEV": "1", "OPENROUTER_API_KEY": "k"}
STATE = {"claims": {"K01": {"claim": "c", "quote": "q", "span": "s"}}}
NOUL_Q = {"q": jc.noul_question("Is it?", "yes it is", "no it is not")}
CHOICE_Q = {"c": jc.choice_question("Which?", {"budget": "a budget item", "other": "anything else"})}
SCORE_Q = {"s": jc.score_question("How much?", ["none", "some", "lots"])}


def _json(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


def _ok_body(answers: dict, model: str = "m1") -> bytes:
    return _json({"model": model, "answers": answers,
                  "usage": {"input_tokens": 1, "output_tokens": 1, "cost": 0.0}})


def _reason(transport, questions=NOUL_Q, env=ENV, **kw) -> str:
    with pytest.raises(JevUnavailable) as ei:
        ask(STATE, questions, env=env, transport=transport, sleep=lambda s: None, **kw)
    return ei.value.reason


# --- happy path -----------------------------------------------------------------

def test_ok_noul_response_request_and_result():
    t = FakeTransport(noul_responder(lambda qid: 0.92))
    res = ask(STATE, NOUL_Q, env=ENV, transport=t)
    assert t.count == 1
    call = t.calls[0]
    assert call["url"] == jc.JEV_URL == "https://openrouter.ai/api/v1/systemone"
    assert call["timeout"] == 8.0
    assert call["headers"]["Authorization"] == "Bearer k"
    assert call["headers"]["Content-Type"] == "application/json"
    assert t.bodies[0] == {"model": "~typesafe/jev-latest", "state": STATE, "questions": NOUL_Q}
    assert set(t.bodies[0]) == {"model", "state", "questions"}
    assert res.answers["q"]["noul"] == 0.92
    assert res.model == "m1"
    assert set(res.usage) == {"input_tokens", "output_tokens", "cost"}
    assert isinstance(res.latency_ms, int) and res.latency_ms >= 0


def test_usage_missing_keys_become_none():
    body = _json({"model": "m1", "answers": {"q": {"type": "noul", "noul": 0.5}},
                  "usage": {"input_tokens": 3, "extra": 9}})
    res = ask(STATE, NOUL_Q, env=ENV, transport=FakeTransport(status_responder(200, body)))
    assert res.usage == {"input_tokens": 3, "output_tokens": None, "cost": None}


def test_extra_unrequested_ids_ignored():
    t = FakeTransport(raw_answers_responder({"q": {"type": "noul", "noul": 0.4},
                                             "zz": {"type": "noul", "noul": 9}}))
    res = ask(STATE, NOUL_Q, env=ENV, transport=t)
    assert set(res.answers) == {"q"}


def test_choice_and_score_accepted():
    res = ask(STATE, CHOICE_Q, env=ENV, transport=FakeTransport(choice_responder(lambda q: "budget")))
    assert res.answers["c"]["choice"] == "budget"
    res = ask(STATE, SCORE_Q, env=ENV, transport=FakeTransport(score_responder(lambda q: 2.4)))
    assert res.answers["s"]["score"] == 2.4


def test_builders_shape():
    assert jc.noul_question("i", "t", "f") == {
        "type": "noul", "instructions": "i", "criteria": {"true": "t", "false": "f"}}
    # Jev carries the answer set in ``criteria`` for every question type (verified live).
    assert jc.choice_question("i", {"a": "A", "b": "B"}) == {
        "type": "choice", "instructions": "i", "criteria": {"a": "A", "b": "B"}}
    assert jc.score_question("i", ["x", "y"]) == {
        "type": "score", "instructions": "i", "criteria": ["x", "y"]}


# --- opt-in / key -----------------------------------------------------------------

@pytest.mark.parametrize("env", [{}, {"OPENROUTER_API_KEY": "k"}])
def test_disabled_when_flag_unset_even_with_api_key(env):
    t = FakeTransport(noul_responder(lambda q: 0.5))
    assert _reason(t, env=env, api_key="validkey") == "disabled"
    assert t.count == 0


def test_missing_key_when_api_key_blank():
    t = FakeTransport(noul_responder(lambda q: 0.5))
    assert _reason(t, api_key="") == "missing_key"
    assert t.count == 0


@pytest.mark.parametrize("env", [{"MAGPIE_JEV": "1"}, {"MAGPIE_JEV": "1", "OPENROUTER_API_KEY": "  "}])
def test_disabled_when_env_key_missing_even_with_explicit_api_key(env):
    # Spec 1.1 / plan Decision 14: jev_status(env) off means no send, whatever api_key says.
    t = FakeTransport(noul_responder(lambda q: 0.5))
    assert _reason(t, env=env, api_key="explicit") == "disabled"
    assert t.count == 0


def test_key_whitespace_is_stripped_in_header():
    t = FakeTransport(noul_responder(lambda q: 0.5))
    ask(STATE, NOUL_Q, env={"MAGPIE_JEV": "1", "OPENROUTER_API_KEY": " k\n"}, transport=t)
    assert t.calls[0]["headers"]["Authorization"] == "Bearer k"


def test_env_defaults_to_os_environ(monkeypatch):
    monkeypatch.delenv("MAGPIE_JEV", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    t = FakeTransport(noul_responder(lambda q: 0.5))
    with pytest.raises(JevUnavailable) as ei:
        ask(STATE, NOUL_Q, transport=t)
    assert ei.value.reason == "disabled"
    assert t.count == 0


# --- retry --------------------------------------------------------------------------

def test_retry_429_then_200():
    slept = []
    t = FakeTransport(sequence_responder([
        (429, b"{}"), (200, _ok_body({"q": {"type": "noul", "noul": 0.3}}))]))
    res = ask(STATE, NOUL_Q, env=ENV, transport=t, sleep=slept.append)
    assert res.answers["q"]["noul"] == 0.3
    assert slept == [1.0]
    assert t.count == 2


def test_529_twice_is_http_529_two_calls():
    t = FakeTransport(sequence_responder([(529, b"{}"), (529, b"{}")]))
    assert _reason(t) == "http_529"
    assert t.count == 2


def test_500_no_retry():
    t = FakeTransport(status_responder(500, b'{"error":{"message":"boom"}}'))
    assert _reason(t) == "http_500"
    assert t.count == 1


# --- error mapping ------------------------------------------------------------------

def test_400_max_tokens_exceeded_is_too_large():
    body = _json({"error": {"message": "state has 40000 tokens: max_tokens_exceeded (limit)"}})
    assert _reason(FakeTransport(status_responder(400, body))) == "too_large"


def test_400_other_is_http_400():
    body = _json({"error": {"message": "bad request"}})
    assert _reason(FakeTransport(status_responder(400, body))) == "http_400"


def test_403_bare_cloudflare_html_is_waf_blocked():
    body = b"<html><head><title>Attention Required! | Cloudflare</title></head></html>"
    assert _reason(FakeTransport(status_responder(403, body))) == "waf_blocked"


def test_403_json_wrapped_cloudflare_is_waf_blocked():
    body = _json({"error": {"message": "HTTP 403: <!DOCTYPE html><title>Attention Required</title>"}})
    assert _reason(FakeTransport(status_responder(403, body))) == "waf_blocked"


def test_403_plain_json_is_http_403():
    body = _json({"error": {"message": "forbidden"}})
    assert _reason(FakeTransport(status_responder(403, body))) == "http_403"


def test_401_json_is_http_401():
    body = _json({"error": {"message": "no auth"}})
    assert _reason(FakeTransport(status_responder(401, body))) == "http_401"


def test_transport_timeout_and_network():
    t = FakeTransport(sequence_responder([socket.timeout("slow")]))
    assert _reason(t) == "timeout"
    t = FakeTransport(sequence_responder([OSError("unreachable")]))
    assert _reason(t) == "network"


def test_402_json_is_http_402():
    body = _json({"error": {"message": "insufficient credits"}})
    assert _reason(FakeTransport(status_responder(402, body))) == "http_402"


def test_urlerror_wrapped_timeout_is_timeout():
    # The default urllib transport surfaces socket timeouts as URLError(reason=timeout).
    t = FakeTransport(sequence_responder([urllib.error.URLError(socket.timeout("timed out"))]))
    assert _reason(t) == "timeout"


@pytest.mark.parametrize("exc", [socket.timeout("slow sk-or-v1-SECRETVALUE123"),
                                 OSError("unreachable sk-or-v1-SECRETVALUE123")])
def test_timeout_and_network_details_carry_no_key(exc):
    secret = "sk-or-v1-SECRETVALUE123"
    env = {"MAGPIE_JEV": "1", "OPENROUTER_API_KEY": secret}
    with pytest.raises(JevUnavailable) as ei:
        ask(STATE, NOUL_Q, env=env, transport=FakeTransport(sequence_responder([exc])),
            sleep=lambda s: None)
    assert secret not in str(ei.value)
    assert secret not in (ei.value.detail or "")


def test_200_not_json_is_malformed_json():
    assert _reason(FakeTransport(status_responder(200, b"not json"))) == "malformed_json"


@pytest.mark.parametrize("status", [400, 401, 403, 500])
def test_error_body_echoing_key_is_scrubbed(status):
    secret = "sk-or-v1-SECRETVALUE123"
    body = _json({"error": {"message": f"invalid key {secret} " + "x" * 600}})
    env = {"MAGPIE_JEV": "1", "OPENROUTER_API_KEY": secret}
    with pytest.raises(JevUnavailable) as ei:
        ask(STATE, NOUL_Q, env=env, transport=FakeTransport(status_responder(status, body)),
            sleep=lambda s: None)
    exc = ei.value
    assert secret not in str(exc)
    assert exc.detail is not None and secret not in exc.detail
    assert len(exc.detail) <= 300


# --- strict validation: noul ---------------------------------------------------------

@pytest.mark.parametrize("value", [-0.1, 1.2, float("nan"), float("inf"), True, "0.9", None])
def test_noul_bad_values(value):
    t = FakeTransport(raw_answers_responder({"q": {"type": "noul", "noul": value}}))
    assert _reason(t) == "bad_shape"


@pytest.mark.parametrize("value", [0, 1, 0.5])
def test_noul_good_values_as_float(value):
    t = FakeTransport(raw_answers_responder({"q": {"type": "noul", "noul": value}}))
    res = ask(STATE, NOUL_Q, env=ENV, transport=t)
    got = res.answers["q"]["noul"]
    assert type(got) is float and got == float(value)


def test_missing_requested_id_is_bad_shape():
    t = FakeTransport(raw_answers_responder({"other": {"type": "noul", "noul": 0.5}}))
    assert _reason(t) == "bad_shape"


def test_answer_not_object_is_bad_shape():
    t = FakeTransport(raw_answers_responder({"q": 0.5}))
    assert _reason(t) == "bad_shape"


def test_wrong_answer_type_is_bad_shape():
    t = FakeTransport(raw_answers_responder({"q": {"type": "choice", "choice": "x"}}))
    assert _reason(t) == "bad_shape"


# --- strict validation: choice ----------------------------------------------------

@pytest.mark.parametrize("answer", [
    {"type": "choice", "choice": "nope", "probabilities": {"budget": 1.0}, "confidence": 1.0},
    {"type": "choice", "choice": "budget", "probabilities": {"budget": 1.5}, "confidence": 1.0},
    {"type": "choice", "choice": "budget", "probabilities": {"x": 1.0}, "confidence": 1.0},
    {"type": "choice", "choice": "budget", "probabilities": {"budget": 1.0}, "confidence": float("nan")},
    {"type": "choice", "choice": "budget", "probabilities": ["budget"], "confidence": 1.0},
])
def test_choice_bad_shapes(answer):
    t = FakeTransport(raw_answers_responder({"c": answer}))
    assert _reason(t, questions=CHOICE_Q) == "bad_shape"


# --- strict validation: score -----------------------------------------------------

@pytest.mark.parametrize("value", ["3", True, float("nan")])
def test_score_bad_values(value):
    t = FakeTransport(raw_answers_responder({"s": {"type": "score", "score": value}}))
    assert _reason(t, questions=SCORE_Q) == "bad_shape"


def test_score_bad_probabilities_and_confidence():
    t = FakeTransport(raw_answers_responder(
        {"s": {"type": "score", "score": 1.0, "probabilities": {"a": 2.0}}}))
    assert _reason(t, questions=SCORE_Q) == "bad_shape"
    t = FakeTransport(raw_answers_responder(
        {"s": {"type": "score", "score": 1.0, "confidence": -1}}))
    assert _reason(t, questions=SCORE_Q) == "bad_shape"


def test_score_accepted_value():
    t = FakeTransport(raw_answers_responder({"s": {"type": "score", "score": 2.4}}))
    res = ask(STATE, SCORE_Q, env=ENV, transport=t)
    assert res.answers["s"]["score"] == 2.4


# --- strict validation: envelope ------------------------------------------------------

def test_response_without_model_is_bad_shape():
    t = FakeTransport(raw_answers_responder({"q": {"type": "noul", "noul": 0.5}}, model=None))
    assert _reason(t) == "bad_shape"


def test_response_blank_model_is_bad_shape():
    t = FakeTransport(raw_answers_responder({"q": {"type": "noul", "noul": 0.5}}, model="  "))
    assert _reason(t) == "bad_shape"


def test_response_list_is_bad_shape():
    assert _reason(FakeTransport(status_responder(200, b"[1, 2]"))) == "bad_shape"


# --- jev_status ---------------------------------------------------------------------

@pytest.mark.parametrize("env", [
    {},
    {"MAGPIE_JEV": "0", "OPENROUTER_API_KEY": "k"},
    {"MAGPIE_JEV": "true", "OPENROUTER_API_KEY": "k"},
])
def test_jev_status_flag_off(env):
    assert jev_status(env) == (False, "MAGPIE_JEV not set")


@pytest.mark.parametrize("env", [
    {"MAGPIE_JEV": "1"},
    {"MAGPIE_JEV": "1", "OPENROUTER_API_KEY": "  "},
])
def test_jev_status_key_missing(env):
    assert jev_status(env) == (False, "no OPENROUTER_API_KEY")


def test_jev_status_on_trims_flag():
    assert jev_status({"MAGPIE_JEV": " 1 ", "OPENROUTER_API_KEY": "k"}) == (True, None)


def test_jev_status_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("MAGPIE_JEV", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert jev_status() == (True, None)


# --- public_reason / constants -------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("too_large", "too_large"), ("waf_blocked", "waf_blocked"), ("http_500", "jev_error"),
    ("timeout", "jev_error"), ("bad_shape", "jev_error"), ("missing_key", "jev_error"),
    ("disabled", "jev_error"),
])
def test_public_reason(raw, expected):
    assert public_reason(raw) == expected


def test_constants():
    assert jc.JEV_MODEL == "~typesafe/jev-latest"
    assert jc.API_KEY_ENV == "OPENROUTER_API_KEY"
    assert jc.ENABLE_ENV == "MAGPIE_JEV"
    assert jc.RETRY_STATUSES == (429, 529)
    assert jc.RETRY_BACKOFF_S == 1.0
    assert jc.TOO_LARGE_MARKER == "max_tokens_exceeded"
    assert jc.ABORT_REASONS == frozenset({"disabled", "missing_key", "http_401", "http_402", "http_403"})


def test_import_is_stdlib_only():
    code = ("import sys; import scripts.jev_client; "
            "bad = [m for m in ('pandas', 'torch') if m in sys.modules]; "
            "print(bad); sys.exit(1 if bad else 0)")
    p = subprocess.run([sys.executable, "-c", code],
                       cwd=str(Path(__file__).resolve().parent.parent),
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


# --- redirects (the key must never follow a 3xx to another host) -----------------

class _RedirectingHTTPS(urllib.request.HTTPSHandler):
    """Offline stand-in for the HTTPS handler: records every request it is asked to open.
    The Jev host answers 302 -> another host; any other host answers 200. No socket is opened."""

    def __init__(self, location: str = "https://evil.example/steal"):
        super().__init__()
        self.location = location
        self.seen: list[tuple[str, str | None]] = []  # (host, Authorization header)

    def https_open(self, req):
        self.seen.append((req.host, req.get_header("Authorization")))
        if req.host == "openrouter.ai":
            code, msg, headers, body = 302, "Found", {"Location": self.location}, b""
        else:
            code, msg, headers, body = 200, "OK", {}, b"{}"
        hdrs = email.message.Message()
        for k, v in headers.items():
            hdrs[k] = v
        resp = urllib.response.addinfourl(io.BytesIO(body), hdrs, req.full_url, code)
        resp.msg = msg
        return resp


def test_fake_https_handler_detects_redirect_following():
    # Control: a stock urllib opener DOES follow the 302 and forwards the key to the other
    # host, so the fake exercises the real redirect path the no-redirect opener must block.
    fake = _RedirectingHTTPS()
    opener = urllib.request.build_opener(fake)
    req = urllib.request.Request(jc.JEV_URL, data=b"{}", method="POST",
                                 headers={"Authorization": "Bearer k"})
    opener.open(req, timeout=1).close()
    assert [h for h, _ in fake.seen] == ["openrouter.ai", "evil.example"]
    assert fake.seen[1][1] == "Bearer k"


def test_no_redirect_handler_refuses_every_redirect():
    handler = jc._NoRedirectHandler()
    req = urllib.request.Request(jc.JEV_URL, data=b"{}", method="POST")
    for code in (301, 302, 303, 307, 308):
        assert handler.redirect_request(req, None, code, "x", {}, "https://evil.example/") is None


def test_default_opener_has_no_following_redirect_handler():
    redirect_handlers = [h for h in jc._OPENER.handlers
                         if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert len(redirect_handlers) == 1
    assert isinstance(redirect_handlers[0], jc._NoRedirectHandler)


@pytest.mark.parametrize("location", ["https://evil.example/steal", "https://openrouter.ai/other"])
def test_302_is_not_followed_and_key_never_leaves_jev_host(monkeypatch, location):
    fake = _RedirectingHTTPS(location)
    monkeypatch.setattr(jc, "_OPENER", jc._build_opener(fake))
    secret = "sk-or-v1-SECRETVALUE123"
    env = {"MAGPIE_JEV": "1", "OPENROUTER_API_KEY": secret}
    with pytest.raises(JevUnavailable) as ei:
        ask(STATE, NOUL_Q, env=env, sleep=lambda s: None)  # default urllib transport
    assert ei.value.reason == "http_302"
    assert fake.seen == [("openrouter.ai", f"Bearer {secret}")]
    assert secret not in str(ei.value)

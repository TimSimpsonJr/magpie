"""ASCII only. EDGE: the only network module for Magpie's optional Jev features.

One POST to Jev (TypeSafe's System One decision model) through OpenRouter's systemone
endpoint. ``ask()`` either returns a fully validated ``JevResult`` or raises
``JevUnavailable``; every caller falls back to today's behavior on any exception (Part A
routes to ``verify``, Part B reports ``skipped``). ``ask_windowed()`` packs many items into
state windows (chars/4 estimate, ~14k-token budget) and splits a window once on
``too_large`` / ``waf_blocked``.

Opt-in (spec 1.1): nothing is sent unless ``MAGPIE_JEV`` is exactly ``1`` (after trimming)
and ``OPENROUTER_API_KEY`` is non-blank. ``ask()`` checks this itself before doing any other
work, so no caller can send while opted out. The API key is never logged or embedded in
exception text.

Stdlib only: importing this module must not pull in pandas, torch or any other heavy stack.
The transport is injectable so the offline suite never touches the network. The default
urllib transport never follows redirects (a 3xx is an ``http_<code>`` failure), so the bearer
key is only ever sent to JEV_URL's host.
"""
from __future__ import annotations

import json
import math
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Mapping

JEV_URL = "https://openrouter.ai/api/v1/systemone"
# Floating alias; the concrete model id comes back in each response (model-change safeguard, spec 1.3).
JEV_MODEL = "~typesafe/jev-latest"
API_KEY_ENV = "OPENROUTER_API_KEY"
ENABLE_ENV = "MAGPIE_JEV"
# Jev answers in ~0.3-1 s; 8 s leaves room for OpenRouter queueing without stalling a run.
TIMEOUT_S = 8.0
# Rate-limited / overloaded: worth exactly one retry; anything else fails straight to fallback.
RETRY_STATUSES = (429, 529)
RETRY_BACKOFF_S = 1.0
# Jev reports an oversized state as HTTP 400 with this marker in the error message; surfaced as
# "too_large" so the window packer can split once.
TOO_LARGE_MARKER = "max_tokens_exceeded"
# A Cloudflare WAF in front of TypeSafe answers some states with a 403 "Attention Required" page,
# bare or wrapped by OpenRouter as {"error": {"message": "HTTP 403: <!DOCTYPE html>..."}}.
WAF_MARKERS = ("attention required", "cloudflare")
REASON_OFF_FLAG = "MAGPIE_JEV not set"
REASON_OFF_KEY = "no OPENROUTER_API_KEY"
# Reasons that will fail identically for every remaining window, so callers stop sending.
ABORT_REASONS = frozenset({"disabled", "missing_key", "http_401", "http_402", "http_403"})
# Reasons callers can act on (split a window); everything else is reported as "jev_error".
_PASS_THROUGH_REASONS = frozenset({"too_large", "waf_blocked"})
# Diagnostic detail is for humans; keep it short so a large error page never floods a log.
_DETAIL_MAX = 300
_RAY_ID_RE = re.compile(r"Ray ID:\s*(?:<[^>]*>\s*)*([0-9a-fA-F]{8,32})")
_USAGE_KEYS = ("input_tokens", "output_tokens", "cost")

Transport = Callable[[str, bytes, dict, float], "tuple[int, bytes]"]


class JevUnavailable(Exception):
    """Jev could not answer. ``reason`` is one of: disabled | missing_key | timeout | network |
    too_large | waf_blocked | http_<code> | malformed_json | bad_shape. ``detail`` is a short
    diagnostic with the API key scrubbed (max 300 chars), or None."""

    def __init__(self, reason: str, detail: str | None = None):
        self.reason = reason
        self.detail = detail
        super().__init__(reason if not detail else f"{reason}: {detail}")


@dataclass
class JevResult:
    answers: dict[str, dict]  # requested question id -> validated answer dict
    model: str  # concrete model id reported by the response
    usage: dict = field(default_factory=dict)  # exactly input_tokens, output_tokens, cost (None if absent)
    latency_ms: int = 0


# --- question builders -----------------------------------------------------------------
# Jev carries every question's answer set in ``criteria``: an object {"true", "false"} for noul,
# an object label -> description for choice, and a list of level descriptions for score.

def noul_question(instructions: str, true_criteria: str, false_criteria: str) -> dict:
    """A yes/no question answered with a probability in [0, 1]."""
    return {"type": "noul", "instructions": instructions,
            "criteria": {"true": true_criteria, "false": false_criteria}}


def choice_question(instructions: str, options: dict[str, str]) -> dict:
    """A choice question: ``options`` maps label -> description (sent as ``criteria``)."""
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def score_question(instructions: str, levels: list[str]) -> dict:
    """A score question over ordered level descriptions (sent as ``criteria``)."""
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


# --- opt-in ------------------------------------------------------------------------------

def jev_status(env: Mapping[str, str] | None = None) -> tuple[bool, str | None]:
    """(enabled, reason). Enabled only when MAGPIE_JEV is exactly "1" (trimmed) and the key is
    non-blank; the flag reason takes precedence over the key reason."""
    env = os.environ if env is None else env
    if (env.get(ENABLE_ENV) or "").strip() != "1":
        return False, REASON_OFF_FLAG
    if not (env.get(API_KEY_ENV) or "").strip():
        return False, REASON_OFF_KEY
    return True, None


def public_reason(raw: str) -> str:
    """Map a client reason to the public vocabulary: too_large / waf_blocked / jev_error."""
    return raw if raw in _PASS_THROUGH_REASONS else "jev_error"


# --- validation --------------------------------------------------------------------------

def _is_real(x: object) -> bool:
    return not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)


def _is_unit(x: object) -> bool:
    return _is_real(x) and 0.0 <= x <= 1.0


def _check_probabilities(qid: str, entry: dict, allowed: set[str] | None) -> dict | None:
    if "probabilities" not in entry:
        return None
    probs = entry["probabilities"]
    if not isinstance(probs, dict):
        raise JevUnavailable("bad_shape", f"answer {qid} probabilities is not an object")
    out = {}
    for k, v in probs.items():
        if allowed is not None and k not in allowed:
            raise JevUnavailable("bad_shape", f"answer {qid} probabilities has an unrequested option")
        if not _is_unit(v):
            raise JevUnavailable("bad_shape", f"answer {qid} probability out of range")
        out[k] = float(v)
    return out


def _check_confidence(qid: str, entry: dict) -> None:
    if "confidence" in entry and not _is_unit(entry["confidence"]):
        raise JevUnavailable("bad_shape", f"answer {qid} confidence out of range")


def _validate_answer(qid: str, entry: object, question: dict) -> dict:
    if not isinstance(entry, dict):
        raise JevUnavailable("bad_shape", f"answer {qid} missing or not an object")
    qtype = question.get("type") if isinstance(question, dict) else None
    if entry.get("type") != qtype:
        raise JevUnavailable("bad_shape", f"answer {qid} has type {entry.get('type')!r}")
    out = dict(entry)
    if qtype == "noul":
        if not _is_unit(entry.get("noul")):
            raise JevUnavailable("bad_shape", f"answer {qid} noul is not a real in [0, 1]")
        out["noul"] = float(entry["noul"])
    elif qtype == "choice":
        options = question.get("criteria")
        allowed = set(options) if isinstance(options, dict) else set()
        label = entry.get("choice")
        if not isinstance(label, str) or label not in allowed:
            raise JevUnavailable("bad_shape", f"answer {qid} choice is not a requested option")
        probs = _check_probabilities(qid, entry, allowed)
        if probs is not None:
            out["probabilities"] = probs
        _check_confidence(qid, entry)
    elif qtype == "score":
        if not _is_real(entry.get("score")):
            raise JevUnavailable("bad_shape", f"answer {qid} score is not a finite number")
        out["score"] = float(entry["score"])
        probs = _check_probabilities(qid, entry, None)
        if probs is not None:
            out["probabilities"] = probs
        _check_confidence(qid, entry)
    else:
        raise JevUnavailable("bad_shape", f"question {qid} has unknown type {qtype!r}")
    return out


def _validate_answers(answers: object, questions: dict) -> dict[str, dict]:
    if not isinstance(answers, dict):
        raise JevUnavailable("bad_shape", "answers is not an object")
    return {qid: _validate_answer(qid, answers.get(qid), q) for qid, q in questions.items()}


# --- transport + error mapping -----------------------------------------------------------

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect. urllib's stock handler re-sends the request headers, the
    Authorization bearer key included, to whatever host a 3xx names. Returning None makes
    urllib raise HTTPError(3xx), which the transport maps like any other HTTP error, so a
    redirect surfaces as JevUnavailable("http_<code>") and the key stays with JEV_URL's host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401, ARG002
        return None


def _build_opener(*handlers: urllib.request.BaseHandler) -> urllib.request.OpenerDirector:
    """An opener whose redirect handler never follows (extra handlers are for offline tests)."""
    return urllib.request.build_opener(_NoRedirectHandler(), *handlers)


_OPENER = _build_opener()


def _urllib_transport(url: str, body: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        try:
            data = e.read() or b""
        except Exception:  # noqa: BLE001 - an unreadable error body is just empty
            data = b""
        return e.code, data


def _scrub(text: str, api_key: str) -> str:
    if api_key:
        text = text.replace(api_key, "[key]")
    return text[:_DETAIL_MAX]


def _error_message(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    if isinstance(parsed, dict):
        err = parsed.get("error")
        msg = err.get("message") if isinstance(err, dict) else None
        if isinstance(msg, str):
            return msg
    return text


def _waf_detail(status: int, msg: str, body: bytes) -> str | None:
    """A short detail when a 403 carries the Cloudflare block page (wrapped or bare), else None."""
    if status != 403:
        return None
    text = msg + "\n" + body.decode("utf-8", errors="replace")
    if not any(m in text.lower() for m in WAF_MARKERS):
        return None
    ray = _RAY_ID_RE.search(text)
    return "Cloudflare block page" + (f" (Ray ID {ray.group(1)})" if ray else "")


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    return isinstance(exc, urllib.error.URLError) and isinstance(
        exc.reason, (socket.timeout, TimeoutError))


# --- ask ---------------------------------------------------------------------------------

def ask(state: dict, questions: dict, *, env: Mapping[str, str] | None = None,
        api_key: str | None = None, transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep, timeout: float = TIMEOUT_S) -> JevResult:
    """POST one Jev request and return validated answers, or raise JevUnavailable."""
    env = os.environ if env is None else env
    # Opt-in gate first (spec 1.1, plan Decision 14): either setting missing -> no send,
    # even when an explicit api_key is passed.
    enabled, _ = jev_status(env)
    if not enabled:
        raise JevUnavailable("disabled")
    key = (api_key if api_key is not None else env.get(API_KEY_ENV) or "").strip()
    if not key:
        raise JevUnavailable("missing_key")

    transport = transport or _urllib_transport
    body = json.dumps({"model": JEV_MODEL, "state": state, "questions": questions}).encode("utf-8")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    start = time.monotonic()
    status, resp_body = 0, b""
    for attempt in (0, 1):
        try:
            status, resp_body = transport(JEV_URL, body, headers, timeout)
        except Exception as e:  # noqa: BLE001 - every transport failure maps to a reason
            if _is_timeout(e):
                raise JevUnavailable("timeout") from None
            raise JevUnavailable("network", _scrub(type(e).__name__, key)) from None
        if status in RETRY_STATUSES and attempt == 0:
            sleep(RETRY_BACKOFF_S)
            continue
        break
    latency_ms = max(0, int((time.monotonic() - start) * 1000))

    resp_body = resp_body or b""
    if status != 200:
        msg = _error_message(resp_body)
        if status == 400 and TOO_LARGE_MARKER in msg:
            raise JevUnavailable("too_large", _scrub(msg, key))
        waf = _waf_detail(status, msg, resp_body)
        if waf is not None:
            raise JevUnavailable("waf_blocked", _scrub(waf, key))
        raise JevUnavailable(f"http_{status}", _scrub(msg, key))

    try:
        parsed = json.loads(resp_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise JevUnavailable("malformed_json") from None
    if not isinstance(parsed, dict):
        raise JevUnavailable("bad_shape", "response is not an object")
    model = parsed.get("model")
    if not isinstance(model, str) or not model.strip():
        raise JevUnavailable("bad_shape", "missing model")
    answers = _validate_answers(parsed.get("answers"), questions)
    raw_usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
    usage = {k: raw_usage.get(k) for k in _USAGE_KEYS}
    return JevResult(answers=answers, model=model, usage=usage, latency_ms=latency_ms)


# --- windows (spec 1.2) ------------------------------------------------------------------

# Cheap, dependency-free token estimate; deliberately conservative against the budget below.
CHARS_PER_TOKEN = 4
# Jev's state limit is about 32.7k real tokens; dense text tokenizes worse than chars/4.
WINDOW_TOKEN_BUDGET = 14_000
# Reasons worth one split into halves: a smaller state may fit, or may avoid the WAF rule.
_SPLIT_REASONS = frozenset({"too_large", "waf_blocked"})


def estimate_tokens(obj: object) -> int:
    """ceil(len(json) / CHARS_PER_TOKEN), counting non-ASCII as single characters."""
    return math.ceil(len(json.dumps(obj, ensure_ascii=False)) / CHARS_PER_TOKEN)


def _window_estimate(items: dict[str, dict], keys: list[str], state_key: str) -> int:
    return estimate_tokens({state_key: {k: items[k] for k in keys}})


def pack_windows(items: dict[str, dict], *, state_key: str,
                 budget: int = WINDOW_TOKEN_BUDGET) -> tuple[list[list[str]], list[str]]:
    """Greedy packing in insertion order -> (windows, oversize). An item whose solo window
    exceeds ``budget`` is oversize and never sent."""
    windows: list[list[str]] = []
    oversize: list[str] = []
    current: list[str] = []
    for key in items:
        if _window_estimate(items, [key], state_key) > budget:
            oversize.append(key)
            continue
        if current and _window_estimate(items, current + [key], state_key) > budget:
            windows.append(current)
            current = []
        current.append(key)
    if current:
        windows.append(current)
    return windows, oversize


@dataclass
class WindowedResult:
    answers: dict[str, dict[str, dict]]  # item key -> {question id -> validated answer}
    failures: dict[str, str]  # item key -> raw client reason (see JevUnavailable)
    models: list[str]  # distinct concrete model ids, first-seen order
    usage: dict  # input_tokens / output_tokens / cost summed over calls (None counted as 0)
    calls: int  # ask_fn invocations, failed ones included
    latency_ms: int  # summed over successful calls


def ask_windowed(items: dict[str, dict], questions_for: Callable[[str], dict[str, dict]], *,
                 state_key: str, ask_fn: Callable[[dict, dict], JevResult],
                 budget: int = WINDOW_TOKEN_BUDGET) -> WindowedResult:
    """Ask ``questions_for(key)`` about every item, packed into windows of state
    ``{state_key: {key: item}}``. Oversize items fail ``too_large`` without a call. A window
    failing ``too_large``/``waf_blocked`` with more than one item is split once into halves
    (first half ceil(n/2)); a failing half marks its items and is not split again. Any other
    reason marks the window's items. A reason in ABORT_REASONS stops every remaining window,
    which gets the same reason. Only JevUnavailable is caught."""
    windows, oversize = pack_windows(items, state_key=state_key, budget=budget)
    out = WindowedResult(answers={}, failures={k: "too_large" for k in oversize}, models=[],
                         usage={k: 0 for k in _USAGE_KEYS}, calls=0, latency_ms=0)
    aborted: str | None = None

    def fail(keys: list[str], reason: str) -> None:
        for k in keys:
            out.failures[k] = reason

    def run(keys: list[str]) -> str | None:
        """Ask one window; record answers, or return the failure reason."""
        questions: dict[str, dict] = {}
        per_item: dict[str, list[str]] = {}
        for k in keys:
            qs = questions_for(k)
            per_item[k] = list(qs)
            questions.update(qs)
        state = {state_key: {k: items[k] for k in keys}}
        out.calls += 1
        try:
            result = ask_fn(state, questions)
        except JevUnavailable as e:
            return e.reason
        if result.model not in out.models:
            out.models.append(result.model)
        for uk in _USAGE_KEYS:
            out.usage[uk] += (result.usage or {}).get(uk) or 0
        out.latency_ms += result.latency_ms
        for k in keys:
            out.answers[k] = {qid: result.answers[qid] for qid in per_item[k]}
        return None

    for window in windows:
        if aborted is not None:
            fail(window, aborted)
            continue
        reason = run(window)
        if reason is None:
            continue
        if reason in _SPLIT_REASONS and len(window) > 1:
            mid = math.ceil(len(window) / 2)
            for half in (window[:mid], window[mid:]):
                if aborted is not None:
                    fail(half, aborted)
                    continue
                half_reason = run(half)
                if half_reason is not None:
                    fail(half, half_reason)
                    if half_reason in ABORT_REASONS:
                        aborted = half_reason
            continue
        fail(window, reason)
        if reason in ABORT_REASONS:
            aborted = reason
    return out

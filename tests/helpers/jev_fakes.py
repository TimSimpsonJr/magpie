"""ASCII only. Offline fakes for the Jev client: a recording transport plus response
builders shared by the jev_* offline tests. Nothing here touches the network.

A responder is a callable ``responder(body: dict) -> (status: int, body: bytes)``; it may
raise to simulate a transport failure (``socket.timeout``, ``OSError``).
"""
from __future__ import annotations

import json
from typing import Callable, Iterable

DEFAULT_USAGE = {"input_tokens": 10, "output_tokens": 1, "cost": 0.0001}


class FakeTransport:
    """Stands in for ``jev_client``'s urllib transport and records every call."""

    def __init__(self, responder: Callable[[dict], tuple[int, bytes]]):
        self.responder = responder
        self.bodies: list[dict] = []
        self.raw: list[bytes] = []
        self.calls: list[dict] = []  # {"url", "headers", "timeout"} per call

    def __call__(self, url: str, body: bytes, headers: dict, timeout: float):
        self.raw.append(body)
        parsed = json.loads(body.decode("utf-8"))
        self.bodies.append(parsed)
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
        return self.responder(parsed)

    @property
    def count(self) -> int:
        return len(self.raw)


def _ok(answers: dict, model: str, usage: dict | None = None) -> tuple[int, bytes]:
    payload = {"model": model, "answers": answers,
               "usage": dict(DEFAULT_USAGE if usage is None else usage)}
    return 200, json.dumps(payload).encode("utf-8")


def noul_responder(score_for: Callable[[str], float], model: str = "m1"):
    """Answer every requested question id with ``{"type": "noul", "noul": score_for(qid)}``."""
    def respond(body: dict):
        answers = {qid: {"type": "noul", "noul": score_for(qid)} for qid in body["questions"]}
        return _ok(answers, model)
    return respond


def choice_responder(label_for: Callable[[str], str], model: str = "m1"):
    """Answer every choice question with ``label_for(qid)``, all probability mass on it."""
    def respond(body: dict):
        answers = {}
        for qid, q in body["questions"].items():
            label = label_for(qid)
            options = list((q.get("criteria") or {}).keys())
            probs = {opt: (1.0 if opt == label else 0.0) for opt in options}
            answers[qid] = {"type": "choice", "choice": label,
                            "probabilities": probs, "confidence": 1.0}
        return _ok(answers, model)
    return respond


def score_responder(value_for: Callable[[str], float], model: str = "m1"):
    """Answer every score question with ``value_for(qid)``."""
    def respond(body: dict):
        answers = {qid: {"type": "score", "score": value_for(qid), "legend": {},
                         "probabilities": {}, "confidence": 0.7}
                   for qid in body["questions"]}
        return _ok(answers, model)
    return respond


def status_responder(status: int, body: bytes):
    """Always return the given status and raw body."""
    def respond(_body: dict):
        return status, body
    return respond


def sequence_responder(items: Iterable):
    """Return each ``(status, body)`` in turn; an exception instance in the list is raised."""
    queue = list(items)

    def respond(_body: dict):
        if not queue:
            raise AssertionError("sequence_responder exhausted")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
    return respond


def raw_answers_responder(answers: dict, model: str | None = "m1"):
    """Return exactly ``answers`` (unvalidated) so tests can probe strict validation."""
    def respond(_body: dict):
        payload: dict = {"answers": answers, "usage": dict(DEFAULT_USAGE)}
        if model is not None:
            payload["model"] = model
        return 200, json.dumps(payload).encode("utf-8")
    return respond

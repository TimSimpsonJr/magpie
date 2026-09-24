"""ASCII only. Offline tests for the Part B batched dataset tool scripts/jev_ask.py (Task 10).

Every Jev call goes through FakeTransport (tests/helpers/jev_fakes.py); nothing touches the
network. The model-approval state always lives in tmp_path, never the real data/ dir.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import jev_ask as ja
from scripts import jev_client as jc
from scripts import jev_guards as jg
from scripts import jev_state
from tests.helpers.jev_fakes import (
    FakeTransport,
    choice_responder,
    noul_responder,
    score_responder,
    status_responder,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "jev_ask.py"
ENV = {"MAGPIE_JEV": "1", "OPENROUTER_API_KEY": "k"}
META_KEYS = {"question", "type", "criteria", "options", "levels", "id_field", "text_fields",
             "model", "approved_model", "model_changed", "timestamp", "counts", "input_file",
             "input_sha256", "usage", "sample"}
COUNT_KEYS = {"answered", "pii", "secret", "too_large", "waf_blocked", "jev_error", "empty"}
FIXED_NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

TOO_LARGE_BODY = json.dumps(
    {"error": {"message": "state rejected: max_tokens_exceeded"}}).encode("utf-8")
WAF_BODY = b"<!DOCTYPE html><title>Attention Required! | Cloudflare</title>"
HTTP_500_BODY = json.dumps({"error": {"message": "internal"}}).encode("utf-8")

PHONE = "864-555-0199"
SECRET = "api_key = 'x9'"

RECORDS = [
    {"rid": "a", "title": "Council budget memo", "body": "Discusses the road fund."},
    {"rid": "b", "title": "Parks schedule", "body": "Summer hours for the pool."},
    {"rid": "c", "title": "Police vendor contract", "body": "Camera lease terms."},
]


# ---------------------------------------------------------------- helpers

def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def write_csv(path: Path, rows: list[dict]) -> Path:
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def fake_ask(fake: FakeTransport, env: dict = ENV):
    return lambda state, questions: jc.ask(state, questions, env=env, transport=fake)


def noul_args(inp: Path, out: Path, *extra: str) -> list[str]:
    return ["--input", str(inp), "--id-field", "rid", "--text-fields", "title,body",
            "--type", "noul", "--question", "Is this record about public safety?",
            "--criteria-true", "The record concerns police, fire or emergency services.",
            "--criteria-false", "The record concerns something else.",
            "--out", str(out), *extra]


def run(argv, fake=None, state_path=None, env=ENV, tmp_path=None):
    ask_fn = fake_ask(fake, env) if fake is not None else None
    if state_path is None:
        state_path = (tmp_path or Path(".")) / "nostate" / "jev_state.json"
    return ja.main(argv, env=env, ask_fn=ask_fn, state_path=state_path, now=lambda: FIXED_NOW)


@pytest.fixture
def no_state(tmp_path) -> Path:
    return tmp_path / "missing" / "jev_state.json"


@pytest.fixture
def approved(tmp_path) -> Path:
    path = tmp_path / "state" / "jev_state.json"
    jev_state.record_passing_model("m1", eval_summary={"n": 20}, path=path)
    return path


@pytest.fixture
def options_file(tmp_path) -> Path:
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"budget": "About money", "other": "Anything else"}),
                    encoding="utf-8")
    return path


@pytest.fixture
def levels_file(tmp_path) -> Path:
    path = tmp_path / "levels.json"
    path.write_text(json.dumps(["not relevant", "somewhat relevant", "highly relevant"]),
                    encoding="utf-8")
    return path


# ---------------------------------------------------------------- usage errors (exit 2)

def test_noul_without_criteria_false(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    argv = noul_args(inp, tmp_path / "out.jsonl")
    i = argv.index("--criteria-false")
    del argv[i:i + 2]
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(argv, fake, no_state) == ja.EXIT_USAGE
    assert fake.count == 0
    assert not (tmp_path / "out.jsonl").exists()


def _choice_argv(inp: Path, out: Path, options: Path | None) -> list[str]:
    argv = ["--input", str(inp), "--id-field", "rid", "--text-fields", "title,body",
            "--type", "choice", "--question", "Which topic?", "--out", str(out)]
    if options is not None:
        argv += ["--options", str(options)]
    return argv


def test_choice_without_options(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    fake = FakeTransport(choice_responder(lambda q: "budget"))
    assert run(_choice_argv(inp, tmp_path / "o.jsonl", None), fake, no_state) == 2
    assert fake.count == 0


@pytest.mark.parametrize("n", [1, 256])
def test_choice_options_count_bounds(tmp_path, no_state, n):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    opts = tmp_path / "opts.json"
    opts.write_text(json.dumps({f"label{i}": f"desc {i}" for i in range(n)}), encoding="utf-8")
    fake = FakeTransport(choice_responder(lambda q: "label0"))
    assert run(_choice_argv(inp, tmp_path / "o.jsonl", opts), fake, no_state) == 2
    assert fake.count == 0


@pytest.mark.parametrize("content", [
    json.dumps(["budget", "other"]),          # not an object
    json.dumps({"": "blank label", "x": "y"}),  # blank label
    "{not json",
])
def test_choice_options_malformed(tmp_path, no_state, content):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    opts = tmp_path / "opts.json"
    opts.write_text(content, encoding="utf-8")
    fake = FakeTransport(choice_responder(lambda q: "budget"))
    assert run(_choice_argv(inp, tmp_path / "o.jsonl", opts), fake, no_state) == 2


def test_choice_options_missing_file(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    fake = FakeTransport(choice_responder(lambda q: "budget"))
    argv = _choice_argv(inp, tmp_path / "o.jsonl", tmp_path / "nope.json")
    assert run(argv, fake, no_state) == 2


def _score_argv(inp: Path, out: Path, levels: Path | None) -> list[str]:
    argv = ["--input", str(inp), "--id-field", "rid", "--text-fields", "title,body",
            "--type", "score", "--question", "How relevant is this record?", "--out", str(out)]
    if levels is not None:
        argv += ["--levels", str(levels)]
    return argv


@pytest.mark.parametrize("n", [1, 11])
def test_score_levels_count_bounds(tmp_path, no_state, n):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    lv = tmp_path / "levels.json"
    lv.write_text(json.dumps([f"level {i}" for i in range(n)]), encoding="utf-8")
    fake = FakeTransport(score_responder(lambda q: 1.0))
    assert run(_score_argv(inp, tmp_path / "o.jsonl", lv), fake, no_state) == 2
    assert fake.count == 0


def test_score_without_levels(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    fake = FakeTransport(score_responder(lambda q: 1.0))
    assert run(_score_argv(inp, tmp_path / "o.jsonl", None), fake, no_state) == 2


def test_mismatched_type_flags_rejected(tmp_path, no_state, options_file):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    argv = noul_args(inp, tmp_path / "o.jsonl", "--options", str(options_file))
    assert run(argv, fake, no_state) == 2
    assert fake.count == 0


def test_bad_input_suffix(tmp_path, no_state):
    inp = tmp_path / "data.txt"
    inp.write_text("rid,title\na,x\n", encoding="utf-8")
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(inp, tmp_path / "o.jsonl"), fake, no_state) == 2
    assert fake.count == 0


def test_missing_input_file(tmp_path, no_state):
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(tmp_path / "nope.jsonl", tmp_path / "o.jsonl"), fake, no_state) == 2


def test_record_missing_id(tmp_path, no_state):
    rows = [dict(RECORDS[0]), {"title": "no id here", "body": "x"}]
    inp = write_jsonl(tmp_path / "in.jsonl", rows)
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(inp, tmp_path / "o.jsonl"), fake, no_state) == 2
    assert fake.count == 0


def test_duplicate_ids(tmp_path, no_state):
    rows = [dict(RECORDS[0]), dict(RECORDS[0])]
    inp = write_jsonl(tmp_path / "in.jsonl", rows)
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(inp, tmp_path / "o.jsonl"), fake, no_state) == 2
    assert fake.count == 0


def test_invalid_jsonl_line(tmp_path, no_state):
    inp = tmp_path / "in.jsonl"
    inp.write_text(json.dumps(RECORDS[0]) + "\n[1, 2]\n", encoding="utf-8")
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(inp, tmp_path / "o.jsonl"), fake, no_state) == 2


def test_out_equal_to_input(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    before = inp.read_bytes()
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(inp, inp), fake, no_state) == 2
    assert inp.read_bytes() == before
    assert fake.count == 0


def test_derived_path_colliding_with_input(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "r.sample.jsonl", RECORDS)
    before = inp.read_bytes()
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    argv = noul_args(inp, tmp_path / "r.jsonl", "--sample", "1")
    assert run(argv, fake, no_state) == 2
    assert inp.read_bytes() == before


def test_id_field_in_text_fields_rejected(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    argv = noul_args(inp, tmp_path / "o.jsonl")
    argv[argv.index("title,body")] = "rid,title"
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(argv, fake, no_state) == 2
    assert fake.count == 0


@pytest.mark.parametrize("sample", ["0", "-1", "x"])
def test_bad_sample(tmp_path, no_state, sample):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(inp, tmp_path / "o.jsonl", "--sample", sample), fake, no_state) == 2


def test_question_secret_blocks_send(tmp_path, no_state, capsys):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    argv = noul_args(inp, tmp_path / "o.jsonl")
    argv[argv.index("--question") + 1] = "use password=hunter2"
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(argv, fake, no_state) == 2
    assert fake.count == 0
    assert "hunter2" not in capsys.readouterr().err
    assert not (tmp_path / "o.jsonl").exists()


def test_options_pii_blocks_send(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    opts = tmp_path / "opts.json"
    opts.write_text(json.dumps({"call": f"Call {PHONE}", "other": "else"}), encoding="utf-8")
    fake = FakeTransport(choice_responder(lambda q: "other"))
    assert run(_choice_argv(inp, tmp_path / "o.jsonl", opts), fake, no_state) == 2
    assert fake.count == 0


# ---------------------------------------------------------------- Jev off (exit 3)

def test_jev_off_exit_3(tmp_path, no_state, capsys):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    out = tmp_path / "o.jsonl"
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    code = run(noul_args(inp, out), fake, no_state, env={"OPENROUTER_API_KEY": "k"})
    assert code == ja.EXIT_JEV_OFF == 3
    assert "jev: off (MAGPIE_JEV not set)" in capsys.readouterr().err
    assert fake.count == 0
    assert not out.exists()
    assert not (tmp_path / "o.meta.json").exists()


def test_jev_off_no_key(tmp_path, no_state, capsys):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    code = run(noul_args(inp, tmp_path / "o.jsonl"), fake, no_state, env={"MAGPIE_JEV": "1"})
    assert code == 3
    assert "jev: off (no OPENROUTER_API_KEY)" in capsys.readouterr().err


# ---------------------------------------------------------------- answers per type

def test_noul_three_records(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    out = tmp_path / "results.jsonl"
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(inp, out), fake, no_state) == 0
    rows = read_jsonl(out)
    assert rows == [{"id": "a", "answer": {"p": 0.9}}, {"id": "b", "answer": {"p": 0.9}},
                    {"id": "c", "answer": {"p": 0.9}}]
    assert fake.count == 1


def test_choice_csv_input(tmp_path, no_state, options_file):
    inp = write_csv(tmp_path / "in.csv", RECORDS)
    out = tmp_path / "results.jsonl"
    fake = FakeTransport(choice_responder(lambda q: "budget"))
    assert run(_choice_argv(inp, out, options_file), fake, no_state) == 0
    rows = read_jsonl(out)
    assert [r["id"] for r in rows] == ["a", "b", "c"]
    for r in rows:
        assert set(r["answer"]) == {"label", "probabilities", "confidence"}
        assert r["answer"]["label"] == "budget"
        assert r["answer"]["probabilities"] == {"budget": 1.0, "other": 0.0}
    q = fake.bodies[0]["questions"]["q_R0001"]
    assert q["type"] == "choice"
    assert q["options"] == {"budget": "About money", "other": "Anything else"}


def test_score(tmp_path, no_state, levels_file):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    out = tmp_path / "results.jsonl"
    fake = FakeTransport(score_responder(lambda q: 2.0))
    assert run(_score_argv(inp, out, levels_file), fake, no_state) == 0
    rows = read_jsonl(out)
    for r in rows:
        assert set(r["answer"]) == {"value", "probabilities", "confidence"}
        assert r["answer"]["value"] == 2.0
    q = fake.bodies[0]["questions"]["q_R0001"]
    assert q["levels"] == ["not relevant", "somewhat relevant", "highly relevant"]


def test_shape_answer():
    assert ja.shape_answer("noul", {"type": "noul", "noul": 0.25}) == {"p": 0.25}
    assert ja.shape_answer("choice", {"type": "choice", "choice": "x",
                                      "probabilities": {"x": 0.8}, "confidence": 0.8}) == {
        "label": "x", "probabilities": {"x": 0.8}, "confidence": 0.8}
    assert ja.shape_answer("score", {"type": "score", "score": 2.4, "legend": {},
                                     "probabilities": {}, "confidence": 0.7}) == {
        "value": 2.4, "probabilities": {}, "confidence": 0.7}


# ---------------------------------------------------------------- skips

def test_record_skips_pii_secret_empty(tmp_path, no_state):
    rows = [
        {"rid": "p", "title": f"Call {PHONE}", "body": "x"},
        {"rid": "s", "title": "config", "body": SECRET},
        {"rid": "e", "title": "  ", "body": ""},
        {"rid": "ok", "title": "Road fund", "body": "Budget."},
    ]
    inp = write_jsonl(tmp_path / "in.jsonl", rows)
    out = tmp_path / "results.jsonl"
    fake = FakeTransport(noul_responder(lambda q: 0.7))
    assert run(noul_args(inp, out), fake, no_state) == 0
    got = read_jsonl(out)
    assert got == [{"id": "p", "skipped": "pii"}, {"id": "s", "skipped": "secret"},
                   {"id": "e", "skipped": "empty"}, {"id": "ok", "answer": {"p": 0.7}}]
    assert fake.count == 1
    assert list(fake.bodies[0]["state"]["records"]) == ["R0004"]


def test_missing_text_field_is_blank(tmp_path, no_state):
    rows = [{"rid": "a", "title": "Only a title"}, {"rid": "b"}]
    inp = write_jsonl(tmp_path / "in.jsonl", rows)
    out = tmp_path / "results.jsonl"
    fake = FakeTransport(noul_responder(lambda q: 0.6))
    assert run(noul_args(inp, out), fake, no_state) == 0
    assert read_jsonl(out) == [{"id": "a", "answer": {"p": 0.6}}, {"id": "b", "skipped": "empty"}]
    assert fake.bodies[0]["state"]["records"]["R0001"] == {"title": "Only a title", "body": ""}


def test_oversize_record_too_large(tmp_path, no_state):
    rows = [dict(RECORDS[0]), {"rid": "big", "title": "big", "body": "word " * 20000}]
    inp = write_jsonl(tmp_path / "in.jsonl", rows)
    out = tmp_path / "results.jsonl"
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(inp, out), fake, no_state) == 0
    assert read_jsonl(out) == [{"id": "a", "answer": {"p": 0.9}}, {"id": "big", "skipped": "too_large"}]
    assert all("R0002" not in b["state"]["records"] for b in fake.bodies)


def test_waf_window(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    out = tmp_path / "results.jsonl"
    fake = FakeTransport(status_responder(403, WAF_BODY))
    assert run(noul_args(inp, out), fake, no_state) == 0
    assert {r["skipped"] for r in read_jsonl(out)} == {"waf_blocked"}


def test_http_500_window(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    out = tmp_path / "results.jsonl"
    fake = FakeTransport(status_responder(500, HTTP_500_BODY))
    assert run(noul_args(inp, out), fake, no_state) == 0
    assert {r["skipped"] for r in read_jsonl(out)} == {"jev_error"}
    meta = json.loads((tmp_path / "results.meta.json").read_text(encoding="utf-8"))
    assert meta["counts"]["jev_error"] == 3
    assert meta["model"] is None


def test_unexpected_ask_exception_is_jev_error(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    out = tmp_path / "results.jsonl"

    def boom(state, questions):
        raise RuntimeError("unexpected")

    code = ja.main(noul_args(inp, out), env=ENV, ask_fn=boom, state_path=no_state,
                   now=lambda: FIXED_NOW)
    assert code == 0
    assert {r["skipped"] for r in read_jsonl(out)} == {"jev_error"}


def test_pii_module_failure_sends_nothing(tmp_path, no_state, monkeypatch):
    jg._pii_patterns.cache_clear()
    try:
        def boom(name, *a, **k):
            raise ImportError("no pandas")
        monkeypatch.setattr(jg.importlib, "import_module", boom)
        inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
        out = tmp_path / "results.jsonl"
        fake = FakeTransport(noul_responder(lambda q: 0.9))
        assert run(noul_args(inp, out), fake, no_state) == 0
        assert {r.get("skipped") for r in read_jsonl(out)} == {"pii"}
        assert fake.count == 0
    finally:
        jg._pii_patterns.cache_clear()


# ---------------------------------------------------------------- what is sent

def test_canary_nothing_sensitive_sent(tmp_path, no_state):
    rows = [
        {"rid": "rec-zulu-quokka", "title": "Road fund", "body": "Budget talk."},
        {"rid": "rec-yankee-wombat", "title": f"Call {PHONE}", "body": "x"},
        {"rid": "rec-xray-numbat", "title": "keys", "body": SECRET},
        {"rid": "rec-whiskey-dingo", "title": "Pool hours", "body": "Summer."},
    ]
    inp = write_jsonl(tmp_path / "canary-source-platypus.jsonl", rows)
    out = tmp_path / "results.jsonl"
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(inp, out), fake, no_state) == 0
    assert fake.count >= 1
    for raw in fake.raw:
        text = raw.decode("utf-8")
        assert PHONE not in text
        assert "x9" not in text
        assert "canary-source-platypus" not in text
        assert str(tmp_path.name) not in text
        for r in rows:
            assert r["rid"] not in text


def test_request_state_and_questions(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(inp, tmp_path / "o.jsonl"), fake, no_state) == 0
    body = fake.bodies[0]
    assert body["state"] == {"records": {
        "R0001": {"title": "Council budget memo", "body": "Discusses the road fund."},
        "R0002": {"title": "Parks schedule", "body": "Summer hours for the pool."},
        "R0003": {"title": "Police vendor contract", "body": "Camera lease terms."}}}
    assert list(body["questions"]) == ["q_R0001", "q_R0002", "q_R0003"]
    q = body["questions"]["q_R0001"]
    assert q["type"] == "noul"
    assert "record R0001" in q["instructions"]
    assert q["instructions"] == "Answer only about record R0001. Is this record about public safety?"
    assert q["criteria"] == {
        "true": "The record concerns police, fire or emergency services.",
        "false": "The record concerns something else."}


def test_record_key():
    assert ja.record_key(0) == "R0001"
    assert ja.record_key(41) == "R0042"
    assert ja.STATE_KEY == "records"


# ---------------------------------------------------------------- meta + files

def test_meta_contents(tmp_path, no_state):
    rows = RECORDS + [{"rid": "p", "title": f"Call {PHONE}", "body": "x"}]
    inp = write_jsonl(tmp_path / "in.jsonl", rows)
    out = tmp_path / "results.jsonl"
    before = inp.read_bytes()
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(inp, out), fake, no_state) == 0
    meta = json.loads((tmp_path / "results.meta.json").read_text(encoding="utf-8"))
    assert set(meta) == META_KEYS
    assert set(meta["counts"]) == COUNT_KEYS
    assert sum(meta["counts"].values()) == len(rows)
    assert meta["counts"]["answered"] == 3 and meta["counts"]["pii"] == 1
    assert meta["input_sha256"] == hashlib.sha256(before).hexdigest()
    assert meta["input_file"] == "in.jsonl"
    assert meta["type"] == "noul"
    assert meta["criteria"] == {"true": "The record concerns police, fire or emergency services.",
                                "false": "The record concerns something else."}
    assert meta["options"] is None and meta["levels"] is None
    assert meta["id_field"] == "rid"
    assert meta["text_fields"] == ["title", "body"]
    assert meta["model"] == "m1"
    assert meta["timestamp"] == FIXED_NOW.isoformat()
    assert meta["sample"] is None
    assert set(meta["usage"]) == {"input_tokens", "output_tokens", "cost"}
    assert inp.read_bytes() == before
    assert not (tmp_path / "results.sample.jsonl").exists()


def test_no_state_file(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(inp, tmp_path / "r.jsonl"), fake, no_state) == 0
    meta = json.loads((tmp_path / "r.meta.json").read_text(encoding="utf-8"))
    assert meta["model_changed"] is False
    assert meta["approved_model"] is None


def test_model_approved_no_warning(tmp_path, approved, capsys):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    fake = FakeTransport(noul_responder(lambda q: 0.9, model="m1"))
    assert run(noul_args(inp, tmp_path / "r.jsonl"), fake, approved) == 0
    meta = json.loads((tmp_path / "r.meta.json").read_text(encoding="utf-8"))
    assert meta["model_changed"] is False
    assert meta["approved_model"] == "m1"
    assert "WARNING" not in capsys.readouterr().err


def test_model_changed(tmp_path, approved, capsys):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    out = tmp_path / "r.jsonl"
    fake = FakeTransport(noul_responder(lambda q: 0.9, model="m2"))
    assert run(noul_args(inp, out), fake, approved) == 0
    meta = json.loads((tmp_path / "r.meta.json").read_text(encoding="utf-8"))
    assert meta["model_changed"] is True
    assert meta["approved_model"] == "m1"
    assert meta["model"] == "m2"
    err = capsys.readouterr().err
    assert ("WARNING: Jev model changed (approved m1, got m2); re-run the Part A live eval "
            "before relying on these answers.") in err
    assert len(read_jsonl(out)) == 3


def test_sample_deterministic(tmp_path, no_state):
    rows = [{"rid": f"r{i}", "title": f"Record {chr(97 + i)}", "body": "text"} for i in range(8)]
    rows.append({"rid": "p", "title": f"Call {PHONE}", "body": "x"})
    inp = write_jsonl(tmp_path / "in.jsonl", rows)
    samples = []
    for name in ("one", "two"):
        out = tmp_path / name / "results.jsonl"
        fake = FakeTransport(noul_responder(lambda q: 0.9))
        assert run(noul_args(inp, out, "--sample", "2", "--seed", "7"), fake, no_state) == 0
        samples.append((tmp_path / name / "results.sample.jsonl").read_bytes())
        meta = json.loads((tmp_path / name / "results.meta.json").read_text(encoding="utf-8"))
        assert meta["sample"] == {"n": 2, "seed": 7}
    assert samples[0] == samples[1]
    got = [json.loads(line) for line in samples[0].decode("utf-8").splitlines()]
    assert len(got) == 2
    assert all("answer" in r for r in got)
    ids = [r["id"] for r in got]
    order = [r["rid"] for r in rows]
    assert ids == sorted(ids, key=order.index)


def test_sample_larger_than_answered(tmp_path, no_state):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    out = tmp_path / "results.jsonl"
    fake = FakeTransport(noul_responder(lambda q: 0.9))
    assert run(noul_args(inp, out, "--sample", "50"), fake, no_state) == 0
    assert len(read_jsonl(tmp_path / "results.sample.jsonl")) == 3


def test_select_sample_input_order():
    rows = [{"id": str(i), "answer": {"p": 0.5}} for i in range(10)]
    rows.insert(3, {"id": "s", "skipped": "pii"})
    picked = ja.select_sample(rows, 4, 7)
    assert len(picked) == 4
    assert all("answer" in r for r in picked)
    assert picked == [r for r in rows if r in picked]
    assert ja.select_sample(rows, 4, 7) == picked


def test_derived_paths():
    assert ja.derived_paths(Path("r.jsonl")) == (Path("r.meta.json"), Path("r.sample.jsonl"))
    assert ja.derived_paths(Path("r.out")) == (Path("r.out.meta.json"), Path("r.out.sample.jsonl"))


# ---------------------------------------------------------------- load_records

def test_load_records_jsonl_and_csv(tmp_path):
    rows = [{"rid": 7, "title": "T", "n": 3, "gone": None}]
    j = write_jsonl(tmp_path / "in.jsonl", rows)
    assert ja.load_records(j, "rid", ["title", "n", "gone", "absent"]) == [
        ("7", {"title": "T", "n": "3", "gone": "", "absent": ""})]
    c = write_csv(tmp_path / "in.csv", [{"rid": "x", "title": "T", "n": "3"}])
    assert ja.load_records(c, "rid", ["title", "absent"]) == [("x", {"title": "T", "absent": ""})]


def test_load_records_errors(tmp_path):
    j = write_jsonl(tmp_path / "in.jsonl", [{"rid": "a"}, {"rid": "a"}])
    with pytest.raises(ValueError):
        ja.load_records(j, "rid", ["title"])
    j2 = write_jsonl(tmp_path / "in2.jsonl", [{"rid": ""}])
    with pytest.raises(ValueError):
        ja.load_records(j2, "rid", ["title"])


# ---------------------------------------------------------------- subprocess

def _env_without_jev() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("MAGPIE_JEV", "OPENROUTER_API_KEY")}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def test_cli_subprocess_jev_off(tmp_path):
    inp = write_jsonl(tmp_path / "in.jsonl", RECORDS)
    out = tmp_path / "results.jsonl"
    p = subprocess.run([sys.executable, str(SCRIPT), *noul_args(inp, out)], cwd=str(REPO_ROOT),
                       env=_env_without_jev(), capture_output=True, timeout=120)
    assert p.returncode == 3, p.stderr.decode("utf-8", "replace")
    assert b"jev: off (MAGPIE_JEV not set)" in p.stderr
    assert not out.exists()


# --- Task 14: live-smoke fixture checks (offline) -------------------------------------------

SMOKE_DIR = REPO_ROOT / "tests" / "fixtures" / "jev"


def test_ask_smoke_fixture_shape_and_guard():
    """The jev_live smoke input: 20 labeled records (7/7/6), every text and every option /
    level passes the local guard (a tripped guard would skip records or exit 2 live)."""
    records = ja.load_records(SMOKE_DIR / "ask_smoke.jsonl", "id", ["text"])
    assert [rid for rid, _ in records] == [f"S{i:02d}" for i in range(1, 21)]
    labels = [json.loads(line)["label"] for line in
              (SMOKE_DIR / "ask_smoke.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {lab: labels.count(lab) for lab in set(labels)} == \
        {"surveillance": 7, "budget": 7, "other": 6}
    for rid, fields in records:
        assert jg.guard(*fields.values()) is None, rid
    options = ja._load_options(str(SMOKE_DIR / "ask_smoke_options.json"))
    assert set(options) == {"surveillance", "budget", "other"}
    levels = ja._load_levels(str(SMOKE_DIR / "ask_smoke_levels.json"))
    assert jg.guard(*options, *options.values(), *levels) is None
    for name in ("ask_smoke.jsonl", "ask_smoke_options.json", "ask_smoke_levels.json"):
        assert (SMOKE_DIR / name).read_bytes().isascii(), name

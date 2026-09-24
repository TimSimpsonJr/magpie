"""ASCII only. Offline tests for scripts/jev_state.py (Task 4): the model-approval state file
behind the model-change safeguard (spec 1.3). Every test writes under tmp_path; the real
data/jev_state.json is never touched."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import jev_state
from scripts.jev_state import (
    approved_model,
    load_state,
    model_status,
    record_passing_model,
)

MODEL = "typesafe/jev-1.13-20260917"
FIXED_NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "d" / "s.json"


def test_paths_point_at_repo_data_dir():
    assert jev_state.REPO_ROOT == REPO_ROOT
    assert jev_state.DATA_DIR == REPO_ROOT / "data"
    assert jev_state.STATE_PATH == REPO_ROOT / "data" / "jev_state.json"


def test_missing_file_is_empty(state_path: Path):
    assert load_state(state_path) == {}
    assert approved_model(state_path) is None


@pytest.mark.parametrize("text", ["{bad json", "[1,2]", "", "null", '"m1"', "42"])
def test_invalid_or_non_object_is_empty(state_path: Path, text: str):
    state_path.parent.mkdir(parents=True)
    state_path.write_text(text, encoding="utf-8")
    assert load_state(state_path) == {}
    assert approved_model(state_path) is None


def test_unreadable_path_is_empty(tmp_path: Path):
    # A directory where the file should be cannot be read as a file.
    target = tmp_path / "s.json"
    target.mkdir()
    assert load_state(target) == {}


def test_non_utf8_bytes_are_empty(state_path: Path):
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(b"\xff\xfe\x00{")
    assert load_state(state_path) == {}


@pytest.mark.parametrize("value", [None, "", "   ", 7, ["m1"], {"m": 1}])
def test_approved_model_rejects_invalid_values(state_path: Path, value):
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"approved_model": value}), encoding="utf-8")
    assert approved_model(state_path) is None


def test_record_passing_model_writes_exact_record(state_path: Path):
    rec = record_passing_model(MODEL, eval_summary={"n": 20}, path=state_path, now=FIXED_NOW)
    assert state_path.parent.is_dir()
    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(on_disk) == {"approved_model", "approved_at", "eval"}
    assert on_disk["approved_model"] == MODEL
    assert on_disk["approved_at"] == "2026-09-24T12:00:00+00:00"
    assert on_disk["eval"] == {"n": 20}
    assert rec == on_disk
    assert list(state_path.parent.glob("*.tmp")) == []
    assert [p.name for p in state_path.parent.iterdir()] == ["s.json"]
    assert approved_model(state_path) == MODEL


def test_record_passing_model_normalizes_naive_and_offset_now(state_path: Path):
    from datetime import timedelta

    est = timezone(timedelta(hours=-4))
    rec = record_passing_model(
        MODEL, eval_summary={}, path=state_path, now=datetime(2026, 9, 24, 8, 0, 0, tzinfo=est)
    )
    assert rec["approved_at"] == "2026-09-24T12:00:00+00:00"


def test_record_passing_model_default_now_is_utc(state_path: Path):
    rec = record_passing_model(MODEL, eval_summary={}, path=state_path)
    parsed = datetime.fromisoformat(rec["approved_at"])
    assert parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


def test_record_passing_model_overwrites_previous(state_path: Path):
    record_passing_model("m1", eval_summary={"n": 1}, path=state_path, now=FIXED_NOW)
    record_passing_model("m2", eval_summary={"n": 2}, path=state_path, now=FIXED_NOW)
    assert approved_model(state_path) == "m2"
    assert load_state(state_path)["eval"] == {"n": 2}
    assert list(state_path.parent.glob("*.tmp")) == []


@pytest.mark.parametrize("bad", ["", "   ", None, 3])
def test_record_passing_model_rejects_blank_model(state_path: Path, bad):
    with pytest.raises(ValueError):
        record_passing_model(bad, eval_summary={}, path=state_path, now=FIXED_NOW)
    assert not state_path.exists()


def test_model_status_none_is_unknown(state_path: Path):
    assert model_status(None, state_path) == "unknown"
    # Even when a model is approved, None means nothing was answered.
    record_passing_model("m1", eval_summary={}, path=state_path, now=FIXED_NOW)
    assert model_status(None, state_path) == "unknown"


def test_model_status_no_file_is_unapproved(state_path: Path):
    assert model_status("m1", state_path) == "unapproved"


def test_model_status_approved_and_changed(state_path: Path):
    record_passing_model("m1", eval_summary={}, path=state_path, now=FIXED_NOW)
    assert model_status("m1", state_path) == "approved"
    assert model_status("m2", state_path) == "changed"


def test_model_status_corrupt_file_is_unapproved(state_path: Path):
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{bad json", encoding="utf-8")
    assert model_status("m1", state_path) == "unapproved"


def test_gitignore_excludes_data_dir():
    lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/data/" in [ln.strip() for ln in lines]


def test_module_is_ascii_only():
    src = (REPO_ROOT / "scripts" / "jev_state.py").read_bytes()
    src.decode("ascii")

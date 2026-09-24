"""ASCII only. IO: the model-approval state behind the Jev model-change safeguard (spec 1.3).

``data/jev_state.json`` (untracked; ``/data/`` is gitignored) records the Jev model ID that
last passed the Part A live eval. Consumers compare the model a response reports against it:

- Part A skips claims only when ``model_status`` is ``"approved"``; ``"changed"`` and
  ``"unapproved"`` both route every claim to ``verify`` with reason ``model_changed``
  (fail-safe: no passing eval on record means nothing skips).
- Part B sets ``model_changed = (status == "changed")`` in its meta file and warns.

A missing, unreadable, corrupt or non-object state file reads as ``{}`` (no approved model),
so a damaged file can only make Part A more conservative. Writes are atomic: a temp file in
the same directory, then ``os.replace``.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Already gitignored by the ``/data/`` rule, so the state never lands in a commit.
DATA_DIR = REPO_ROOT / "data"
STATE_PATH = DATA_DIR / "jev_state.json"

# The model_status vocabulary.
STATUS_UNKNOWN = "unknown"  # no model reported: nothing was answered
STATUS_UNAPPROVED = "unapproved"  # no valid approval record on disk
STATUS_APPROVED = "approved"  # reported model equals the approved one
STATUS_CHANGED = "changed"  # reported model differs from the approved one


def load_state(path: Path = STATE_PATH) -> dict:
    """The state object, or ``{}`` when missing, unreadable, invalid JSON, or not an object."""
    try:
        text = Path(path).read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def approved_model(path: Path = STATE_PATH) -> str | None:
    """The approved model ID, or None when there is no valid (non-blank string) record."""
    value = load_state(path).get("approved_model")
    if isinstance(value, str) and value.strip():
        return value
    return None


def model_status(model: str | None, path: Path = STATE_PATH) -> str:
    """Compare a reported model with the approved one: unknown / unapproved / approved / changed."""
    if model is None:
        return STATUS_UNKNOWN
    approved = approved_model(path)
    if approved is None:
        return STATUS_UNAPPROVED
    return STATUS_APPROVED if model == approved else STATUS_CHANGED


def record_passing_model(
    model: str,
    *,
    eval_summary: dict,
    path: Path = STATE_PATH,
    now: datetime | None = None,
) -> dict:
    """Atomically record ``model`` as approved after a passing live eval; returns the record.

    ``approved_at`` is UTC ISO-8601 (a naive ``now`` is taken as UTC). A blank model is
    refused so a bad eval run can never write a record that looks approved.
    """
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-blank string")
    moment = now if now is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    record = {
        "approved_model": model,
        "approved_at": moment.astimezone(timezone.utc).isoformat(),
        "eval": eval_summary,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return record

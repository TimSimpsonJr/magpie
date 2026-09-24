"""ASCII only. Part B: ``jev_ask``, a batched Jev question tool for dataset work (spec 3.1).

Asks one typed question (``noul`` yes/no probability, ``choice`` label, or ``score`` level)
about every record of a JSONL or CSV file and writes one JSON line per input record to a NEW
file, plus a meta file (question, counts, model, input sha256) and an optional deterministic
sample of answered records for spot-checking. The source file is never written.

- Loading, shaping, sampling and path derivation are PURE.
- ``run()`` / ``main()`` are EDGE only through the injected ``ask_fn`` (default:
  ``jev_client.ask`` with the caller's env), plus local file IO for the outputs.

Privacy (spec 1.1): only the configured text fields of each record (under positional keys
``R0001...``) and the question/criteria/options/levels are sent; record ids, file names and
paths never leave the machine (Decision 4). The question text and its criteria/options/levels
must pass ``jev_guards.guard`` or the run stops with exit 2 before any call. A record whose
text fields are all blank is ``skipped: empty``; one that trips the guard is ``skipped: pii``
or ``skipped: secret`` and is never sent. If the PII patterns cannot be loaded, every record
is ``skipped: pii`` and no call is made.

Exit codes (Decision 13): 0 ok (even with skips), 2 usage or input error, 3 Jev off (stderr
``jev: off (<reason>)``; no output files, no call).

CLI::

    python scripts/jev_ask.py --input records.jsonl --id-field id --text-fields title,body \\
        --type noul --question "..." --criteria-true "..." --criteria-false "..." \\
        --out results.jsonl [--sample N] [--seed S]
"""
from __future__ import annotations

import argparse
import csv
import functools
import hashlib
import io
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import jev_client, jev_guards, jev_state  # noqa: E402 - after the script-mode shim

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_JEV_OFF = 3

STATE_KEY = "records"
QUESTION_TYPES = ("noul", "choice", "score")
INPUT_SUFFIXES = (".jsonl", ".csv")
# Choice option and score level bounds (spec 3.1).
OPTIONS_MIN, OPTIONS_MAX = 2, 255
LEVELS_MIN, LEVELS_MAX = 2, 10

SKIP_EMPTY = "empty"
# Every outcome a record can have; the meta ``counts`` object has exactly these keys.
COUNT_KEYS = ("answered", "pii", "secret", "too_large", "waf_blocked", "jev_error", "empty")
_USAGE_KEYS = ("input_tokens", "output_tokens", "cost")
_MODEL_CHANGED_WARNING = ("WARNING: Jev model changed (approved {approved}, got {got}); re-run "
                          "the Part A live eval before relying on these answers.")


class UsageError(ValueError):
    """Bad arguments or input; reported on stderr with exit 2."""


# --- pure helpers --------------------------------------------------------------------------

def record_key(i: int) -> str:
    """Positional state key for the i-th input record (0-based): R0001, R0002, ..."""
    return f"R{i + 1:04d}"


def question_id(key: str) -> str:
    return f"q_{key}"


def derived_paths(out: Path) -> tuple[Path, Path]:
    """(meta, sample) paths next to ``out``: ``r.jsonl`` -> ``r.meta.json`` / ``r.sample.jsonl``;
    any other suffix appends ``.meta.json`` / ``.sample.jsonl`` to the full name."""
    out = Path(out)
    if out.suffix.lower() == ".jsonl":
        stem = out.with_suffix("")
        return stem.with_name(stem.name + ".meta.json"), stem.with_name(stem.name + ".sample.jsonl")
    return out.with_name(out.name + ".meta.json"), out.with_name(out.name + ".sample.jsonl")


def _cell(value: object) -> str:
    """A record value as text: None -> "", containers -> compact JSON, anything else str()."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _rows_jsonl(text: str) -> list[dict]:
    rows = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise ValueError(f"line {n} is not valid JSON") from None
        if not isinstance(row, dict):
            raise ValueError(f"line {n} is not a JSON object")
        rows.append(row)
    return rows


def _rows_csv(text: str) -> list[dict]:
    return [dict(r) for r in csv.DictReader(io.StringIO(text, newline=""))]


def load_records(path: Path, id_field: str,
                 text_fields: list[str]) -> list[tuple[str, dict[str, str]]]:
    """[(record id, {text field: text})] in input order. Missing text fields become "".
    Raises ValueError on an unreadable file, a bad line, or a missing/blank/duplicate id."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in INPUT_SUFFIXES:
        raise ValueError(f"input must be .jsonl or .csv, got {path.suffix or 'no suffix'!r}")
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"cannot read input: {type(e).__name__}") from None
    rows = _rows_jsonl(text) if suffix == ".jsonl" else _rows_csv(text)
    records: list[tuple[str, dict[str, str]]] = []
    seen: set[str] = set()
    for n, row in enumerate(rows, start=1):
        rid = _cell(row.get(id_field))
        if not rid.strip():
            raise ValueError(f"record {n} has no {id_field!r} value")
        if rid in seen:
            raise ValueError(f"record {n} repeats an id already used by an earlier record")
        seen.add(rid)
        records.append((rid, {f: _cell(row.get(f)) for f in text_fields}))
    return records


def shape_answer(qtype: str, raw: dict) -> dict:
    """The public answer shape: noul {p}; choice {label, probabilities, confidence};
    score {value, probabilities, confidence}."""
    if qtype == "noul":
        return {"p": raw["noul"]}
    if qtype == "choice":
        return {"label": raw["choice"], "probabilities": raw.get("probabilities"),
                "confidence": raw.get("confidence")}
    if qtype == "score":
        return {"value": raw["score"], "probabilities": raw.get("probabilities"),
                "confidence": raw.get("confidence")}
    raise ValueError(f"unknown question type {qtype!r}")


def select_sample(rows: list[dict], n: int, seed: int) -> list[dict]:
    """``min(n, answered)`` answered rows chosen by ``random.Random(seed)``, in input order."""
    answered = [i for i, r in enumerate(rows) if "answer" in r]
    picked = random.Random(seed).sample(answered, min(n, len(answered)))
    return [rows[i] for i in sorted(picked)]


def build_question(qtype: str, key: str, question: str, *, criteria: dict | None,
                   options: dict | None, levels: list | None) -> dict:
    instructions = f"Answer only about record {key}. {question}"
    if qtype == "noul":
        return jev_client.noul_question(instructions, criteria["true"], criteria["false"])
    if qtype == "choice":
        return jev_client.choice_question(instructions, options)
    return jev_client.score_question(instructions, levels)


# --- argument validation ---------------------------------------------------------------------

def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer >= 1") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be an integer >= 1")
    return value


def _read_json(path: str, what: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise UsageError(f"cannot read {what} file: {type(e).__name__}") from None


def _load_options(path: str) -> dict[str, str]:
    data = _read_json(path, "--options")
    if not isinstance(data, dict):
        raise UsageError("--options must be a JSON object of label -> description")
    if not OPTIONS_MIN <= len(data) <= OPTIONS_MAX:
        raise UsageError(f"--options needs {OPTIONS_MIN}-{OPTIONS_MAX} entries, got {len(data)}")
    for label, desc in data.items():
        if not label.strip():
            raise UsageError("--options labels must be non-empty strings")
        if not isinstance(desc, str):
            raise UsageError("--options descriptions must be strings")
    return dict(data)


def _load_levels(path: str) -> list[str]:
    data = _read_json(path, "--levels")
    if not isinstance(data, list):
        raise UsageError("--levels must be a JSON list of level descriptions")
    if not LEVELS_MIN <= len(data) <= LEVELS_MAX:
        raise UsageError(f"--levels needs {LEVELS_MIN}-{LEVELS_MAX} entries, got {len(data)}")
    if not all(isinstance(x, str) and x.strip() for x in data):
        raise UsageError("--levels entries must be non-empty strings")
    return list(data)


def _same_path(a: Path, b: Path) -> bool:
    try:
        if a.exists() and b.exists():
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def _pii_available() -> bool:
    return jev_guards.pii_hit("") != jev_guards.PII_UNAVAILABLE


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jev_ask.py",
        description="Ask Jev one typed question about every record of a JSONL/CSV file "
                    "(opt-in: MAGPIE_JEV=1 and OPENROUTER_API_KEY).")
    p.add_argument("--input", required=True, help=".jsonl or .csv records file (read only)")
    p.add_argument("--id-field", required=True, help="field holding each record's unique id "
                                                     "(never sent)")
    p.add_argument("--text-fields", required=True, help="comma-separated fields to send")
    p.add_argument("--type", required=True, choices=QUESTION_TYPES, dest="qtype")
    p.add_argument("--question", required=True)
    p.add_argument("--criteria-true", default=None, help="noul: what a true answer means")
    p.add_argument("--criteria-false", default=None, help="noul: what a false answer means")
    p.add_argument("--options", default=None, help="choice: JSON object label -> description")
    p.add_argument("--levels", default=None, help="score: JSON list of 2-10 level descriptions")
    p.add_argument("--out", required=True, help="results JSONL (a new file)")
    p.add_argument("--sample", type=_positive_int, default=None,
                   help="also write N deterministic-random answered rows for spot-checking")
    p.add_argument("--seed", type=int, default=0, help="sample seed (default 0)")
    return p


def _validate(args: argparse.Namespace) -> dict:
    """Checks that need no Jev call; returns the question spec. Raises UsageError."""
    text_fields = [f.strip() for f in args.text_fields.split(",") if f.strip()]
    if not text_fields:
        raise UsageError("--text-fields names no fields")
    if len(set(text_fields)) != len(text_fields):
        raise UsageError("--text-fields repeats a field")
    if args.id_field in text_fields:
        raise UsageError("--id-field must not be one of --text-fields (record ids are never sent)")
    if not args.question.strip():
        raise UsageError("--question is blank")

    criteria = options = levels = None
    if args.qtype == "noul":
        if not (args.criteria_true or "").strip() or not (args.criteria_false or "").strip():
            raise UsageError("--type noul needs both --criteria-true and --criteria-false")
        if args.options is not None or args.levels is not None:
            raise UsageError("--type noul takes no --options or --levels")
        criteria = {"true": args.criteria_true, "false": args.criteria_false}
    else:
        if args.criteria_true is not None or args.criteria_false is not None:
            raise UsageError(f"--type {args.qtype} takes no --criteria-true/--criteria-false")
        if args.qtype == "choice":
            if args.levels is not None:
                raise UsageError("--type choice takes no --levels")
            if args.options is None:
                raise UsageError("--type choice needs --options")
            options = _load_options(args.options)
        else:
            if args.options is not None:
                raise UsageError("--type score takes no --options")
            if args.levels is None:
                raise UsageError("--type score needs --levels")
            levels = _load_levels(args.levels)

    inp, out = Path(args.input), Path(args.out)
    if inp.suffix.lower() not in INPUT_SUFFIXES:
        raise UsageError("--input must be a .jsonl or .csv file")
    meta_path, sample_path = derived_paths(out)
    written = [out, meta_path] + ([sample_path] if args.sample is not None else [])
    if any(_same_path(p, inp) for p in written):
        raise UsageError("--out (or its meta/sample file) would overwrite the input file")

    # Everything below leaves the machine with each window, so it must pass the local guard.
    # With the PII patterns unavailable every record is skipped and nothing is sent at all.
    sent = [args.question, *text_fields]
    if criteria:
        sent += list(criteria.values())
    if options:
        sent += [*options.keys(), *options.values()]
    if levels:
        sent += levels
    if _pii_available():
        hit = jev_guards.guard(*sent)
        if hit is not None:
            raise UsageError(f"the question, criteria, options, levels or field names trip the "
                             f"local {hit} guard; nothing was sent")

    return {"type": args.qtype, "question": args.question, "criteria": criteria,
            "options": options, "levels": levels, "text_fields": text_fields,
            "input": inp, "out": out, "meta_path": meta_path, "sample_path": sample_path}


# --- run -------------------------------------------------------------------------------------

def run(spec: dict, records: list[tuple[str, dict[str, str]]], *,
        ask_fn: Callable[[dict, dict], jev_client.JevResult]) -> tuple[list[dict], list[str], dict]:
    """(output rows in input order, models seen, usage). Never raises on a Jev failure."""
    outcome: dict[int, dict] = {}
    items: dict[str, dict] = {}
    key_to_index: dict[str, int] = {}
    for i, (_rid, fields) in enumerate(records):
        values = list(fields.values())
        if not any(v.strip() for v in values):
            outcome[i] = {"skipped": SKIP_EMPTY}
            continue
        hit = jev_guards.guard(*values)
        if hit is not None:
            outcome[i] = {"skipped": hit}
            continue
        key = record_key(i)
        key_to_index[key] = i
        items[key] = dict(fields)

    models: list[str] = []
    usage = {k: 0 for k in _USAGE_KEYS}
    if items:
        def questions_for(key: str) -> dict[str, dict]:
            return {question_id(key): build_question(
                spec["type"], key, spec["question"], criteria=spec["criteria"],
                options=spec["options"], levels=spec["levels"])}

        try:
            result = jev_client.ask_windowed(items, questions_for, state_key=STATE_KEY,
                                             ask_fn=ask_fn)
        except Exception:  # noqa: BLE001 - any unexpected failure reports jev_error
            result = None
        if result is None:
            for i in key_to_index.values():
                outcome[i] = {"skipped": "jev_error"}
        else:
            models = list(result.models)
            usage = dict(result.usage)
            for key, i in key_to_index.items():
                if key in result.answers:
                    raw = result.answers[key][question_id(key)]
                    outcome[i] = {"answer": shape_answer(spec["type"], raw)}
                else:
                    reason = result.failures.get(key, "jev_error")
                    outcome[i] = {"skipped": jev_client.public_reason(reason)}

    rows = [{"id": rid, **outcome[i]} for i, (rid, _f) in enumerate(records)]
    return rows, models, usage


def _counts(rows: list[dict]) -> dict[str, int]:
    counts = {k: 0 for k in COUNT_KEYS}
    for r in rows:
        counts["answered" if "answer" in r else r["skipped"]] += 1
    return counts


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_bytes(text.encode("utf-8"))


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None,
         ask_fn: Callable[[dict, dict], jev_client.JevResult] | None = None,
         state_path: Path = jev_state.STATE_PATH,
         now: Callable[[], datetime] | None = None) -> int:
    env = os.environ if env is None else env
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:  # argparse already printed the usage error
        return EXIT_USAGE if e.code else EXIT_OK
    try:
        spec = _validate(args)
        input_bytes = spec["input"].read_bytes()
        records = load_records(spec["input"], args.id_field, spec["text_fields"])
    except (UsageError, ValueError, OSError) as e:
        msg = e if not isinstance(e, OSError) else f"cannot read input: {type(e).__name__}"
        print(f"jev_ask: {msg}", file=sys.stderr)
        return EXIT_USAGE
    if not records:
        print("jev_ask: the input has no records", file=sys.stderr)
        return EXIT_USAGE

    enabled, reason = jev_client.jev_status(env)
    if not enabled:
        print(f"jev: off ({reason})", file=sys.stderr)
        return EXIT_JEV_OFF

    if ask_fn is None:
        ask_fn = functools.partial(jev_client.ask, env=env)
    rows, models, usage = run(spec, records, ask_fn=ask_fn)

    approved = jev_state.approved_model(state_path)
    changed = [m for m in models if jev_state.model_status(m, state_path) == jev_state.STATUS_CHANGED]
    for m in changed:
        print(_MODEL_CHANGED_WARNING.format(approved=approved, got=m), file=sys.stderr)

    counts = _counts(rows)
    moment = (now or (lambda: datetime.now(timezone.utc)))()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    meta = {
        "question": spec["question"],
        "type": spec["type"],
        "criteria": spec["criteria"],
        "options": spec["options"],
        "levels": spec["levels"],
        "id_field": args.id_field,
        "text_fields": spec["text_fields"],
        # One model id normally; a list only if windows reported different models.
        "model": (models[0] if len(models) == 1 else (models or None)),
        "approved_model": approved,
        "model_changed": bool(changed),
        "timestamp": moment.astimezone(timezone.utc).isoformat(),
        "counts": counts,
        "input_file": spec["input"].name,
        "input_sha256": hashlib.sha256(input_bytes).hexdigest(),
        "usage": usage,
        "sample": ({"n": args.sample, "seed": args.seed} if args.sample is not None else None),
    }

    _write_jsonl(spec["out"], rows)
    spec["meta_path"].write_bytes(
        (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    if args.sample is not None:
        _write_jsonl(spec["sample_path"], select_sample(rows, args.sample, args.seed))

    skipped = len(rows) - counts["answered"]
    print(f"jev_ask: {len(rows)} records | {counts['answered']} answered | {skipped} skipped "
          f"-> {spec['out'].name}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

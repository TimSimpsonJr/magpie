"""ASCII only. Part A citation pre-screen (spec 2): a PURE numeric/date gate plus EDGE routing.

Part A lets a claim skip the per-claim extraction-verifier only when Jev is on, the model is
approved, the citation is clean, every number and date in the claim also appears in its cited
span, the claim makes no computed-value assertion (totals, averages, percentages, comparisons,
rates), and Jev scores both presence and entailment at or above the thresholds. Anything else
routes to ``verify`` with the first failing reason; the pre-screen can only remove verifier
calls, never accept or reject a claim.

- The gate (``extract_dates`` / ``normalize_number`` / ``numeric_gate``) is PURE.
- ``prescreen()`` is EDGE only through the injected ``ask_fn`` (default: ``jev_client.ask``
  with the caller's env). Claims with a local reason (guard, degraded anchor, numeric/date,
  computed cue) are never sent (Decision 3), and only positional keys ``K01...`` leave the
  machine, never claim ids (Decision 4).
- Spot-checks (spec 2.4): a deterministic ~10% of ``skip`` claims (hash of claim id + run
  seed, at least one whenever any claim skips) get ``spot_check: true`` and also go to the
  extraction-verifier. ``log_spotcheck_disagreements`` appends every non-``supported`` (or
  missing) spot-check verdict to ``data/jev_spotcheck.jsonl`` (IO, no claim text).
- Audit (spec 2.5, PURE): ``claim_input_from_record`` builds a claim's input from its
  ``CitationRecord`` (resolve_anchor + is_clean_citation, span = resolved block text);
  ``prescreen_record`` is the ``CitationRecord.prescreen`` block; ``gate_label`` is the human
  gate text for a skipped claim, which is stored with ``verifier_result`` ``prescreen-skip``.
- CLI: ``python scripts/jev_prescreen.py <claims.json> [--seed S]`` prints the output JSON;
  unreadable or invalid input exits 2 with no network call.
  ``python scripts/jev_prescreen.py --spotcheck <output.json> --verdicts <verdicts.json>``
  appends the disagreement log (``MAGPIE_JEV_SPOTCHECK_LOG`` overrides its path) and prints
  the run summary line; bad files exit 2.

Gate rules (Decision 9):

- Dates are read first with ``DATE_PATTERNS`` (longest-first) and blanked out of the text, so
  their parts are never re-read as bare numbers. A claim date agrees with a span date when the
  span date has every component the claim date has, with equal values (a claim may be less
  specific: "March 2026" agrees with "March 15, 2026").
- Bare numbers come from the date-blanked text and are normalized (thousands commas, leading
  zeros, trailing fractional zeros). The span number set is those numbers PLUS only the 4-digit
  year of each span date; a span date's month and day never join it.
- The gate is digit-only: spelled-out numbers ("fourteen") are not extracted or compared.
  Jev's entailment question and the verifier cover them.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import citation, jev_client, jev_state  # noqa: E402 - after the script-mode shim
from scripts.jev_guards import guard  # noqa: E402

# Month names and common abbreviations (with optional period, handled in the patterns).
_MONTH = (
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

# Longest-first: a full date must be consumed before a month-year pattern can take part of it.
DATE_PATTERNS = [
    ("ymd_iso", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    ("mdy_num", re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b")),
    ("mdy_name", re.compile(rf"(?i)\b{_MONTH}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b")),
    ("dmy_name", re.compile(rf"(?i)\b(\d{{1,2}})\s+{_MONTH}\.?,?\s+(\d{{4}})\b")),
    ("my_name", re.compile(rf"(?i)\b{_MONTH}\.?,?\s+(\d{{4}})\b")),
]

# Digits with optional thousands groups and an optional fractional part.
NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")

# A claim with any of these cues asserts a derived value the span may not state verbatim.
COMPUTED_CUE_RE = re.compile(
    r"(?i)%|\b(?:total(?:s|ed|ing)?|sum(?:s|med|ming)?|average(?:s|d)?|percent(?:age)?s?|per"
    r"|ratios?|more\s+than|less\s+than|fewer\s+than|increase(?:s|d)?|increasing"
    r"|decrease(?:s|d)?|decreasing|rates?)\b")

# Two-digit years at or below this pivot are 20xx; above it, 19xx.
_TWO_DIGIT_YEAR_PIVOT = 69

# Three-letter month prefix -> month number.
_MONTH_NUMBERS = {
    name: index + 1
    for index, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))
}

REASON_NUMERIC_MISMATCH = "numeric_mismatch"
REASON_COMPUTED_VALUE = "computed_value"


def _month_number(name: str) -> int:
    return _MONTH_NUMBERS[name[:3].lower()]


def _year(token: str) -> int:
    year = int(token)
    if len(token) == 2:
        year += 2000 if year <= _TWO_DIGIT_YEAR_PIVOT else 1900
    return year


def _normalize_match(kind: str, groups: tuple[str, ...]) -> tuple[int, ...]:
    if kind == "ymd_iso":
        year, month, day = groups
        return (int(year), int(month), int(day))
    if kind == "mdy_num":
        month, day, year = groups
        return (_year(year), int(month), int(day))
    if kind == "mdy_name":
        month, day, year = groups
        return (int(year), _month_number(month), int(day))
    if kind == "dmy_name":
        day, month, year = groups
        return (int(year), _month_number(month), int(day))
    if kind == "my_name":
        month, year = groups
        return (int(year), _month_number(month))
    raise ValueError(f"unknown date pattern: {kind}")


def extract_dates(text: str) -> tuple[list[tuple[int, ...]], str]:
    """Normalized ``(y, m, d)`` / ``(y, m)`` dates, and ``text`` with each date span blanked.

    Patterns run longest-first; each match is replaced by the same number of spaces before the
    next pattern runs, so offsets and word boundaries in the remaining text are preserved.
    Dates are returned in pattern order, then left-to-right within a pattern.
    """
    dates: list[tuple[int, ...]] = []
    rest = text
    for kind, pattern in DATE_PATTERNS:
        spans: list[tuple[int, int]] = []
        for match in pattern.finditer(rest):
            dates.append(_normalize_match(kind, match.groups()))
            spans.append(match.span())
        for start, end in reversed(spans):
            rest = rest[:start] + " " * (end - start) + rest[end:]
    return dates, rest


def normalize_number(tok: str) -> str:
    """Canonical form: no thousands commas, no leading zeros, no trailing fractional zeros."""
    plain = tok.replace(",", "")
    whole, _, frac = plain.partition(".")
    whole = whole.lstrip("0") or "0"
    frac = frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def _numbers(text: str) -> set[str]:
    return {normalize_number(tok) for tok in NUMBER_RE.findall(text)}


def _date_agrees(claim_date: tuple[int, ...], span_date: tuple[int, ...]) -> bool:
    return len(span_date) >= len(claim_date) and span_date[: len(claim_date)] == claim_date


def numeric_gate(claim_text: str, span: str) -> str | None:
    """``"numeric_mismatch"``, else ``"computed_value"``, else None (spec 2.3 reason order).

    numeric_mismatch: some claim date has no agreeing span date, or some bare claim number is
    not in the span number set (date-blanked span numbers plus each span date's 4-digit year).
    computed_value: the claim matches ``COMPUTED_CUE_RE``.
    """
    claim_dates, claim_rest = extract_dates(claim_text)
    span_dates, span_rest = extract_dates(span)

    for claim_date in claim_dates:
        if not any(_date_agrees(claim_date, span_date) for span_date in span_dates):
            return REASON_NUMERIC_MISMATCH

    span_numbers = _numbers(span_rest) | {str(date[0]) for date in span_dates}
    if not _numbers(claim_rest) <= span_numbers:
        return REASON_NUMERIC_MISMATCH

    if COMPUTED_CUE_RE.search(claim_text):
        return REASON_COMPUTED_VALUE
    return None


# --- routing (spec 2.1-2.3) --------------------------------------------------------------

# Starting thresholds from the spec; the live eval (Task 14) may only raise them.
PRESENCE_MIN = 0.85
ENTAIL_MIN = 0.85
# Jev state key for Part A windows: {"claims": {"K01": {...}}}.
STATE_KEY = "claims"
# Public reason vocabulary, in spec 2.3 order (the first failing reason wins).
REASONS = ("jev_off", "model_changed", "pii", "secret", "too_large", "waf_blocked",
           "jev_error", "degraded_anchor", "numeric_mismatch", "computed_value", "low_score")
REASON_JEV_OFF = "jev_off"
REASON_MODEL_CHANGED = "model_changed"
REASON_JEV_ERROR = "jev_error"
REASON_DEGRADED_ANCHOR = "degraded_anchor"
REASON_LOW_SCORE = "low_score"
ROUTE_SKIP = "skip"
ROUTE_VERIFY = "verify"
# Exit code for unreadable or invalid CLI input (argparse uses the same code for usage errors).
EXIT_BAD_INPUT = 2

# Spec 2.4: a deterministic ~10% of skip claims also go to the extraction-verifier.
SPOT_CHECK_RATE = 0.10
# Decision 6: whenever any claim skips, at least this many skip claims are spot-checked.
MIN_SPOT_CHECKS = 1
# Untracked local disagreement log (spec 2.4); main() honors MAGPIE_JEV_SPOTCHECK_LOG.
SPOTCHECK_LOG = jev_state.DATA_DIR / "jev_spotcheck.jsonl"
SPOTCHECK_LOG_ENV = "MAGPIE_JEV_SPOTCHECK_LOG"
# The only extraction-verifier result that agrees with a skip; anything else is logged.
VERDICT_SUPPORTED = "supported"
# Logged verdict for a spot-checked claim with no verifier verdict (Decision 6).
VERDICT_MISSING = "missing"

_CLAIM_FIELDS = ("claim_id", "claim_text", "verbatim_quote", "span", "clean_citation")
_USAGE_KEYS = ("input_tokens", "output_tokens", "cost")


@dataclass(frozen=True)
class ClaimInput:
    claim_id: str
    claim_text: str
    verbatim_quote: str
    span: str | None  # the resolved block text, or None when the anchor did not resolve
    clean_citation: bool  # citation.is_clean_citation(resolved)


def parse_claims(raw: object) -> list[ClaimInput]:
    """Validate the pre-screen input: a list of objects with the five exactly-typed keys.

    ``claim_id`` is a non-blank, unique str; ``claim_text`` / ``verbatim_quote`` are str;
    ``span`` is str or None; ``clean_citation`` is a real bool (not 0/1, not "yes"). Extra keys
    are ignored (they are never sent). Raises ValueError on anything else.
    """
    if not isinstance(raw, list):
        raise ValueError("claims input must be a JSON list")
    claims: list[ClaimInput] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"claim {index} is not an object")
        missing = [k for k in _CLAIM_FIELDS if k not in entry]
        if missing:
            raise ValueError(f"claim {index} is missing {', '.join(missing)}")
        claim_id = entry["claim_id"]
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise ValueError(f"claim {index} claim_id must be a non-empty string")
        if claim_id in seen:
            raise ValueError(f"duplicate claim_id at claim {index}")
        seen.add(claim_id)
        for key in ("claim_text", "verbatim_quote"):
            if not isinstance(entry[key], str):
                raise ValueError(f"claim {index} {key} must be a string")
        if entry["span"] is not None and not isinstance(entry["span"], str):
            raise ValueError(f"claim {index} span must be a string or null")
        if not isinstance(entry["clean_citation"], bool):
            raise ValueError(f"claim {index} clean_citation must be true or false")
        claims.append(ClaimInput(claim_id=claim_id, claim_text=entry["claim_text"],
                                 verbatim_quote=entry["verbatim_quote"], span=entry["span"],
                                 clean_citation=entry["clean_citation"]))
    return claims


def item_key(index: int) -> str:
    """Positional state key for the claim at ``index``: K01..K99, K100... (Decision 4)."""
    return f"K{index + 1:02d}"


def build_questions(key: str) -> dict[str, dict]:
    """The two contrastive noul questions for one claim (spec 2.2, plan wire format)."""
    return {
        f"presence_{key}": jev_client.noul_question(
            f"Does the span for {key} contain the quoted text or an equivalent passage?",
            f"The quote for {key}, or a faithful rendering of it, appears in the span for {key}.",
            f"The quote for {key} is absent from the span, or the span has only similar "
            f"wording about something else."),
        f"entail_{key}": jev_client.noul_question(
            f"Does the span for {key} support the claim for {key} as stated?",
            f"The span for {key} states or directly implies the claim, including its names, "
            f"numbers and dates.",
            f"The span for {key} is silent on the claim, contradicts it, or supports only a "
            f"weaker or different claim."),
    }


def default_seed(claims: list[ClaimInput]) -> str:
    """Content-derived run seed: first 16 hex of sha256 over the sorted (id, text) pairs."""
    pairs = sorted([c.claim_id, c.claim_text] for c in claims)
    return hashlib.sha256(json.dumps(pairs).encode("utf-8")).hexdigest()[:16]


def spot_hash(claim_id: str, seed: str) -> float:
    """Deterministic value in [0, 1] for ``claim_id`` under ``seed``."""
    digest = hashlib.sha256(f"{seed}:{claim_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def select_spot_checks(skip_ids: list[str], seed: str,
                       rate: float = SPOT_CHECK_RATE) -> set[str]:
    """Skip ids with ``spot_hash < rate``; if none and ``skip_ids`` is non-empty, the single id
    with the smallest hash (``MIN_SPOT_CHECKS``)."""
    selected = {cid for cid in skip_ids if spot_hash(cid, seed) < rate}
    if len(selected) < MIN_SPOT_CHECKS and skip_ids:
        ranked = sorted(skip_ids, key=lambda cid: (spot_hash(cid, seed), cid))
        selected.update(ranked[:MIN_SPOT_CHECKS])
    return selected


def local_reason(claim: ClaimInput) -> str | None:
    """The first local (network-free) reason a claim cannot skip, or None if it may be sent:
    ``pii`` / ``secret`` (guard), ``degraded_anchor``, ``numeric_mismatch``, ``computed_value``."""
    guarded = guard(claim.claim_text, claim.verbatim_quote, claim.span)
    if guarded is not None:
        return guarded
    if not claim.clean_citation or claim.span is None or not claim.span.strip():
        return REASON_DEGRADED_ANCHOR
    return numeric_gate(claim.claim_text, claim.span)


def _entry(route: str, reason: str | None, presence: float | None = None,
           entailment: float | None = None) -> dict:
    return {"presence": presence, "entailment": entailment, "route": route, "reason": reason,
            "spot_check": False}


def _noul(answers: dict, qid: str) -> float | None:
    answer = answers.get(qid)
    value = answer.get("noul") if isinstance(answer, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _summary(entries: dict[str, dict]) -> dict:
    routes = [e["route"] for e in entries.values()]
    return {"prescreened": len(entries),
            "skipped": routes.count(ROUTE_SKIP),
            "verify": routes.count(ROUTE_VERIFY),
            "spot_checked": sum(1 for e in entries.values() if e["spot_check"])}


def prescreen(claims: list[ClaimInput], *, env: Mapping | None = None,
              ask_fn: Callable | None = None, state_path: Path = jev_state.STATE_PATH,
              seed: str | None = None, enforce_model_gate: bool = True) -> dict:
    """Route every claim to ``skip`` or ``verify`` (spec 2.3). Never raises on a Jev failure:
    every failure routes the affected claims to ``verify`` with a reason from ``REASONS``."""
    run_seed = seed if seed is not None else default_seed(claims)
    usage = {k: 0 for k in _USAGE_KEYS}
    enabled, _ = jev_client.jev_status(env)
    out = {"enabled": enabled, "model": None, "approved_model": jev_state.approved_model(state_path),
           "seed": run_seed, "claims": {}, "summary": {}, "usage": usage}

    if not enabled:
        out["claims"] = {c.claim_id: _entry(ROUTE_VERIFY, REASON_JEV_OFF) for c in claims}
        out["summary"] = _summary(out["claims"])
        return out

    entries: dict[str, dict] = {}
    items: dict[str, dict] = {}
    key_to_id: dict[str, str] = {}
    for index, claim in enumerate(claims):
        reason = local_reason(claim)
        if reason is not None:
            entries[claim.claim_id] = _entry(ROUTE_VERIFY, reason)
            continue
        key = item_key(index)
        key_to_id[key] = claim.claim_id
        items[key] = {"claim": claim.claim_text, "quote": claim.verbatim_quote,
                      "span": claim.span}
        entries[claim.claim_id] = _entry(ROUTE_VERIFY, REASON_JEV_ERROR)  # until answered

    models: list[str] = []
    if items:
        if ask_fn is None:
            ask_fn = functools.partial(jev_client.ask, env=env)
        try:
            result = jev_client.ask_windowed(items, build_questions, state_key=STATE_KEY,
                                             ask_fn=ask_fn)
        except Exception:  # noqa: BLE001 - any unexpected failure fails toward verify
            result = None
        if result is not None:
            models = list(result.models)
            usage.update({k: result.usage.get(k, 0) for k in _USAGE_KEYS})
            for key, raw in result.failures.items():
                entries[key_to_id[key]]["reason"] = jev_client.public_reason(raw)
            for key, answers in result.answers.items():
                presence = _noul(answers, f"presence_{key}")
                entailment = _noul(answers, f"entail_{key}")
                entry = entries[key_to_id[key]]
                if presence is None or entailment is None:
                    continue  # stays jev_error
                entry["presence"], entry["entailment"] = presence, entailment
                if presence >= PRESENCE_MIN and entailment >= ENTAIL_MIN:
                    entry["route"], entry["reason"] = ROUTE_SKIP, None
                else:
                    entry["reason"] = REASON_LOW_SCORE

    # Model gate (spec 1.3, Decision 15): only when some window reported a model.
    if models:
        out["model"] = models[0]
        if enforce_model_gate and any(
                jev_state.model_status(m, state_path) != jev_state.STATUS_APPROVED
                for m in models):
            for entry in entries.values():
                entry["route"], entry["reason"] = ROUTE_VERIFY, REASON_MODEL_CHANGED

    # Spot-checks (spec 2.4): only claims that still skip after the model gate.
    skip_ids = [c.claim_id for c in claims if entries[c.claim_id]["route"] == ROUTE_SKIP]
    for claim_id in select_spot_checks(skip_ids, run_seed):
        entries[claim_id]["spot_check"] = True

    out["claims"] = entries
    out["summary"] = _summary(entries)
    return out


# --- spot-check verdicts, disagreement log, run summary (spec 2.4, 2.5) ------------------

def normalize_verdicts(raw: object) -> dict[str, str]:
    """``{claim_id: result}`` from ``{id: "supported"}`` or ``{id: {"result": ..., ...}}``
    (the extraction-verifier output). Raises ValueError on any other shape."""
    if not isinstance(raw, dict):
        raise ValueError("verdicts must be a JSON object keyed by claim_id")
    verdicts: dict[str, str] = {}
    for claim_id, value in raw.items():
        result = value.get("result") if isinstance(value, dict) else value
        if not isinstance(result, str):
            raise ValueError(f"verdict for {claim_id!r} must be a string or have a string result")
        verdicts[str(claim_id)] = result
    return verdicts


def log_spotcheck_disagreements(output: dict, verdicts: dict[str, str], *,
                                log_path: Path = SPOTCHECK_LOG,
                                now: datetime | None = None) -> list[dict]:
    """Append one JSON line per spot-checked claim whose verdict is not ``supported`` (a
    missing verdict is logged as ``"missing"``); returns the lines. Verdicts for claims that
    were not spot-checked are ignored. No claim text is ever written."""
    ts = (now or datetime.now(timezone.utc)).isoformat()
    lines: list[dict] = []
    for claim_id, entry in output["claims"].items():
        if not entry.get("spot_check"):
            continue
        verdict = verdicts.get(claim_id, VERDICT_MISSING)
        if verdict == VERDICT_SUPPORTED:
            continue
        lines.append({"ts": ts, "claim_id": claim_id, "presence": entry.get("presence"),
                      "entailment": entry.get("entailment"), "model": output.get("model"),
                      "verdict": verdict, "seed": output.get("seed")})
    if lines:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as fh:
            for line in lines:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    return lines


def run_summary(output: dict, disagreements: int) -> str:
    """The ASCII run summary line (spec 2.5, Decision 1: `` | `` separators)."""
    entries = list(output["claims"].values())
    skipped = sum(1 for e in entries if e["route"] == ROUTE_SKIP)
    verify = sum(1 for e in entries if e["route"] == ROUTE_VERIFY)
    spot = sum(1 for e in entries if e.get("spot_check"))
    return (f"{len(entries)} claims pre-screened | {skipped} skipped | "
            f"{verify + spot} sent to extraction-verifier | {spot} spot-checked | "
            f"{disagreements} disagreements")


# --- audit fields + human-gate label (spec 2.5) -------------------------------------------

# The CitationRecord.prescreen block: the claim's pre-screen entry plus the run's model.
PRESCREEN_KEYS = ("presence", "entailment", "route", "reason", "model", "spot_check")
# verifier_result for a skipped claim no extraction-verifier saw (published via public_anchor;
# verifier_confidence stays None). Never "supported": nothing independently verified it.
PRESCREEN_VERIFIER_RESULT = "prescreen-skip"
# Human-gate text for a skipped claim (Decision 1: ASCII " -- " for the spec's em dash).
GATE_LABEL = ("Jev pre-screen: supported -- not independently verified "
              "(presence {presence:.2f}, entailment {entailment:.2f})")


def prescreen_record(entry: dict, model: str | None) -> dict:
    """The ``CitationRecord.prescreen`` block for one claim: exactly ``PRESCREEN_KEYS``, taken
    from a pre-screen output entry (``out["claims"][claim_id]``) plus the run's top-level
    ``model``. Any other entry key is dropped."""
    return {"presence": entry.get("presence"), "entailment": entry.get("entailment"),
            "route": entry.get("route"), "reason": entry.get("reason"), "model": model,
            "spot_check": bool(entry.get("spot_check", False))}


def _score(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def gate_label(prescreen: dict | None) -> str | None:
    """``GATE_LABEL`` for a ``skip`` pre-screen block, else None (a verify claim is labeled by
    its extraction-verifier verdict as usual). A skip block without numeric scores gets None
    rather than a label with invented numbers."""
    if not isinstance(prescreen, dict) or prescreen.get("route") != ROUTE_SKIP:
        return None
    presence, entailment = prescreen.get("presence"), prescreen.get("entailment")
    if not (_score(presence) and _score(entailment)):
        return None
    return GATE_LABEL.format(presence=presence, entailment=entailment)


def claim_input_from_record(claim_id: str, record: citation.CitationRecord,
                            docling_json: dict) -> dict:
    """The five-key pre-screen input for one claim (Decision 5): resolve the record's anchor
    against the current DoclingDocument, ``clean_citation`` from ``is_clean_citation``, and
    ``span`` = the resolved block's ``.text`` (None when resolution found no block)."""
    resolved = citation.resolve_anchor(record, docling_json)
    span = None
    if resolved.block_index is not None:
        span = docling_json.get("texts", [])[resolved.block_index].get("text")
    return {"claim_id": claim_id, "claim_text": record.claim_text,
            "verbatim_quote": record.verbatim_quote, "span": span,
            "clean_citation": citation.is_clean_citation(resolved)}


# --- CLI ---------------------------------------------------------------------------------

def _load_json(path: str, what: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"cannot read {what} file: {type(e).__name__}") from None


def _load_claims(path: str) -> list[ClaimInput]:
    return parse_claims(_load_json(path, "claims"))


def _load_output(path: str) -> dict:
    """A pre-screen output: an object whose ``claims`` maps ids to entries with a route."""
    raw = _load_json(path, "pre-screen output")
    claims = raw.get("claims") if isinstance(raw, dict) else None
    if not isinstance(claims, dict) or not all(
            isinstance(e, dict) and e.get("route") in (ROUTE_SKIP, ROUTE_VERIFY)
            for e in claims.values()):
        raise ValueError("pre-screen output must be an object with a claims map of routed entries")
    return raw


def _spotcheck_main(output_path: str, verdicts_path: str) -> int:
    try:
        output = _load_output(output_path)
        verdicts = normalize_verdicts(_load_json(verdicts_path, "verdicts"))
    except ValueError as e:
        print(f"jev_prescreen: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    log_path = Path(os.environ[SPOTCHECK_LOG_ENV]) if os.environ.get(SPOTCHECK_LOG_ENV) \
        else SPOTCHECK_LOG
    lines = log_spotcheck_disagreements(output, verdicts, log_path=log_path)
    print(run_summary(output, len(lines)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jev_prescreen.py",
        description="Jev citation pre-screen (Part A): route claims to skip or verify, or "
                    "(--spotcheck) log spot-check disagreements and print the run summary.")
    parser.add_argument("claims", nargs="?", default=None,
                        help="JSON list of {claim_id, claim_text, verbatim_quote, span, "
                             "clean_citation}")
    parser.add_argument("--seed", default=None, help="spot-check seed (default: content hash)")
    parser.add_argument("--spotcheck", metavar="OUTPUT_JSON", default=None,
                        help="a pre-screen output JSON; requires --verdicts")
    parser.add_argument("--verdicts", metavar="VERDICTS_JSON", default=None,
                        help="extraction-verifier verdicts keyed by claim_id")
    args = parser.parse_args(argv)
    if args.spotcheck is not None:
        if args.claims is not None or args.seed is not None:
            parser.error("--spotcheck does not take a claims file or --seed")
        if args.verdicts is None:
            parser.error("--spotcheck requires --verdicts")
        return _spotcheck_main(args.spotcheck, args.verdicts)
    if args.verdicts is not None:
        parser.error("--verdicts is only valid with --spotcheck")
    if args.claims is None:
        parser.error("a claims file (or --spotcheck) is required")
    try:
        claims = _load_claims(args.claims)
    except ValueError as e:
        print(f"jev_prescreen: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    output = prescreen(claims, seed=args.seed)
    sys.stdout.buffer.write((json.dumps(output, ensure_ascii=False, indent=2) + "\n")
                            .encode("utf-8"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Magpie Jev Integration Implementation Plan

> **For Claude:** implementers use superpowers:subagent-driven-development, one task per
> implementer, and write code test-first against the Interface blocks and test tables below.
> This is a LIGHT plan: it fixes names, signatures, constants and expected behavior; it does
> not contain full implementations.

**Goal:** Add opt-in Jev support to Magpie: (A) a citation pre-screen that lets confidently
supported claims skip the per-claim `extraction-verifier` in `investigate`, and (B) a batched
`jev_ask` dataset tool with usage guidance. Off by default; nothing leaves the machine unless
`MAGPIE_JEV=1` and `OPENROUTER_API_KEY` are both set.

**Architecture:** Stdlib-only modules in `scripts/`: one network edge (`jev_client.py`,
including window packing/split), a local guard layer (`jev_guards.py`), a model-approval
state file (`jev_state.py`), and two consumers (`jev_prescreen.py`, `jev_ask.py`). Every
failure fails toward today's behavior: Part A routes to `verify`, Part B reports `skipped`.

**Tech Stack:** Python 3.12.10 (stdlib: json, re, hashlib, urllib, csv, argparse, dataclasses),
pytest with a new `jev_live` marker, the existing PyYAML skill-smoke pattern. No new deps.

**Source of truth:** `docs/plans/2026-09-24-magpie-jev-prescreen-design.md` (the spec). Section
references below (`spec 2.3`) point into it.

---

## Environment (read first)

- Worktree: `C:\Users\tim\workspace\magpie-jev-prescreen`, branch `feat/jev-prescreen`. Commit
  directly to it; do not create or switch branches.
- The worktree has no `.venv`. Use the main checkout's venv (same pinned deps). Every Bash call
  starts with:
  `PY=/c/Users/tim/workspace/magpie/.venv/Scripts/python.exe; cd /c/Users/tim/workspace/magpie-jev-prescreen &&`
  Commands below write `"$PY"` for that interpreter. Never use bare `python` (mise shims may
  create a second venv in the worktree). PowerShell equivalent:
  `& C:\Users\tim\workspace\magpie\.venv\Scripts\python.exe -m pytest ...`.
- Offline suite (use `-m`, never `-k`; `-k` also matches file names):
  `"$PY" -m pytest -m "not docling and not spacy and not xray and not tsa and not gliner and not ftm and not neo4j and not compose and not yente and not jev_live" -q`
  Below this is called **OFFLINE**.
- Write every script, fixture, payload and commit-message file with the Write tool, not
  heredocs (Git Bash mangles backslashes in regexes). Commit with
  `git add <files> && git commit -F "$MSG"` where `$MSG` is the absolute path of a message file
  you wrote into your scratchpad directory. Every message ends with a blank line then
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Two hooks deny commands and files whose TEXT contains certain literals: npm config flags,
  protected shell dotfiles, and dynamic-code-execution call literals. Never write the
  built-in evaluate/execute function names followed by an opening parenthesis anywhere (code,
  tests, docs, messages); call the live check "the live eval" in prose only. Use
  `subprocess.run([...])` with a list argv, never `shell=True`.
- Keep every new or edited file ASCII-only (no em dashes, smart quotes, middle dots). The
  investigate, entity-crossref and prior-art files are ASCII-pinned by tests.
- Live Jev calls happen only in Task 14, only under `-m jev_live`, and only when the operator's
  environment already has `MAGPIE_JEV=1` and `OPENROUTER_API_KEY`. Never print, log, or commit
  the key. All fixtures are synthetic (repo rule: no real corpus in the tree).
- House style: module docstring says PURE vs EDGE; `from __future__ import annotations`; named
  constants with a one-line rationale; dataclasses for records; injected clock (`now`) and
  injected transport (`ask_fn` / `transport`) so the offline suite never touches the network.
- CLI modules run as `python scripts/<name>.py` need the script-mode shim directly after the
  stdlib imports, before any `from scripts ...` import:
  ```python
  if __package__ in (None, ""):
      sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
  ```

## File map

```
scripts/jev_client.py            EDGE: the only network module. ask(), strict validation, jev_status(), windows + single split.
scripts/jev_guards.py            PURE: secret patterns + PII (pii_sweep.DEFAULT_PII_PATTERNS, fail-safe import) -> guard().
scripts/jev_state.py             IO: data/jev_state.json (approved model) + model_status().
scripts/jev_prescreen.py         Part A: numeric/date gate, routing, spot-checks, disagreement log, audit fields, CLI.
scripts/jev_ask.py               Part B: batched dataset question CLI (JSONL/CSV in, results + meta + sample out).
scripts/citation.py              MODIFY: CitationRecord gains optional `prescreen` (local log only; public_anchor unchanged).
scripts/detect_tier.py           MODIFY: jev_line() + `jev` block in detect() + one line in render_text().
skills/investigate/SKILL.md      MODIFY: section 2 pre-screen dispatch rule; section 3 gate label; section 4 prescreen-skip status.
agents/extraction-verifier.md    MODIFY: spot-check role; never receives Jev scores.
skills/archive-evidence/SKILL.md MODIFY: citations logs carry the prescreen block unchanged.
skills/dataset-analyze/references/jev-guide.md   NEW: when/how to use jev_ask (and what the pre-screen is).
skills/dataset-analyze/SKILL.md, skills/entity-crossref/SKILL.md   MODIFY: link the guide.
skills/doctor/SKILL.md           MODIFY: documents the jev line.
README.md                        MODIFY: qualified privacy statement + Jev opt-in paragraph.
pyproject.toml, .github/workflows/ci.yml   MODIFY: jev_live marker, default exclusion, CI offline expression.
MANIFEST.md                      REWRITE to budget.
tests/helpers/jev_fakes.py       NEW: FakeTransport + response builders shared by the offline tests.
tests/test_jev_client.py, test_jev_guards.py, test_jev_windows.py, test_jev_state.py,
tests/test_jev_numeric_gate.py, test_jev_prescreen.py, test_jev_spotcheck.py, test_jev_audit.py,
tests/test_jev_ask.py, test_jev_guide.py, test_readme_jev_privacy.py      NEW offline tests.
tests/test_jev_live_prescreen.py, test_jev_live_ask.py                    NEW live tests (jev_live).
tests/fixtures/jev/prescreen_eval.json, ask_smoke.jsonl                   NEW synthetic live fixtures.
tests/test_investigate_skill.py, test_investigate_agents.py, test_archive_evidence_skill.py,
tests/test_detect_tier.py, test_doctor_skill.py, test_citation.py         MODIFY: new assertions.
```

## Jev wire format (used by Tasks 1, 3, 6, 10)

Request (Part A example; Part B uses `"records"` / `R0001` / `q_R0001`):

```json
{"model": "~typesafe/jev-latest",
 "state": {"claims": {"K01": {"claim": "<claim_text>", "quote": "<verbatim_quote>", "span": "<block text>"}}},
 "questions": {
   "presence_K01": {"type": "noul",
     "instructions": "Does the span for K01 contain the quoted text or an equivalent passage?",
     "criteria": {"true": "The quote for K01, or a faithful rendering of it, appears in the span for K01.",
                  "false": "The quote for K01 is absent from the span, or the span has only similar wording about something else."}},
   "entail_K01": {"type": "noul",
     "instructions": "Does the span for K01 support the claim for K01 as stated?",
     "criteria": {"true": "The span for K01 states or directly implies the claim, including its names, numbers and dates.",
                  "false": "The span for K01 is silent on the claim, contradicts it, or supports only a weaker or different claim."}}}}
```

Response (validated shapes):

```json
{"model": "typesafe/jev-1.13-20260917",
 "answers": {"presence_K01": {"type": "noul", "noul": 0.94},
             "q_R0002": {"type": "choice", "choice": "budget", "probabilities": {"budget": 0.8, "other": 0.2}, "confidence": 0.8},
             "q_R0003": {"type": "score", "score": 2.4, "legend": {}, "probabilities": {}, "confidence": 0.7}},
 "usage": {"input_tokens": 812, "output_tokens": 4, "cost": 0.0003}}
```

---

## Task 1: Shared Jev client

**Files:** Create `scripts/jev_client.py`, `tests/helpers/jev_fakes.py`, `tests/test_jev_client.py`.

**Interface:**
- Constants: `JEV_URL = "https://openrouter.ai/api/v1/systemone"`, `JEV_MODEL = "~typesafe/jev-latest"`,
  `API_KEY_ENV = "OPENROUTER_API_KEY"`, `ENABLE_ENV = "MAGPIE_JEV"`, `TIMEOUT_S = 8.0`,
  `RETRY_STATUSES = (429, 529)`, `RETRY_BACKOFF_S = 1.0`, `TOO_LARGE_MARKER = "max_tokens_exceeded"`,
  `WAF_MARKERS = ("attention required", "cloudflare")`, `REASON_OFF_FLAG = "MAGPIE_JEV not set"`,
  `REASON_OFF_KEY = "no OPENROUTER_API_KEY"`, `ABORT_REASONS = frozenset({"disabled", "missing_key", "http_401", "http_402", "http_403"})`.
- `class JevUnavailable(Exception)`: `.reason: str` in `disabled | missing_key | timeout | network | too_large | waf_blocked | http_<code> | malformed_json | bad_shape`; `.detail: str | None` (API key scrubbed, max 300 chars).
- `@dataclass class JevResult: answers: dict[str, dict]; model: str; usage: dict; latency_ms: int` (answers are the validated per-id dicts in the response shapes above; `usage` has exactly `input_tokens`, `output_tokens`, `cost`, missing -> None).
- Builders: `noul_question(instructions, true_criteria, false_criteria) -> dict`, `choice_question(instructions, options: dict[str, str]) -> dict`, `score_question(instructions, levels: list[str]) -> dict`.
- `ask(state: dict, questions: dict, *, env: Mapping[str, str] | None = None, api_key: str | None = None, transport=None, sleep=time.sleep, timeout: float = TIMEOUT_S) -> JevResult`. Defense in depth: first line checks `jev_status(env)` (env defaults to `os.environ`) and raises `JevUnavailable("disabled")` when off, before any other work. The key is `api_key` if not None, else `env[API_KEY_ENV]`. `transport(url, body: bytes, headers: dict, timeout) -> (status: int, body: bytes)`; default is urllib. Body = `json.dumps({"model", "state", "questions"})`; headers `Authorization: Bearer <key>`, `Content-Type: application/json`.
- `jev_status(env: Mapping[str, str] | None = None) -> tuple[bool, str | None]`: enabled only when `env["MAGPIE_JEV"].strip() == "1"` and the key is non-blank; reason precedence flag then key.
- `public_reason(raw: str) -> str`: `too_large` and `waf_blocked` pass through; everything else -> `"jev_error"`.
- Validation rules (spec 1.2): every requested id present and an object; answer `type` equals requested type; `noul` is `int|float`, not `bool`, finite, in [0, 1]; `choice` label is a requested option, every `probabilities` key is a requested option and every value finite in [0, 1], `confidence` (if present) finite in [0, 1]; `score` is a finite non-bool number, `probabilities`/`confidence` values finite in [0, 1] when present. Unrequested extra ids are ignored. Missing/blank `model` or a non-object body -> `bad_shape`.
- `tests/helpers/jev_fakes.py`: `class FakeTransport(responder)` recording `.bodies: list[dict]` and `.raw: list[bytes]`; `noul_responder(score_for: Callable[[str], float], model="m1")`, `status_responder(status, body: bytes)`, `sequence_responder(list_of_(status, body))`, `choice_responder(label_for, model="m1")`, `score_responder(value_for, model="m1")`.

**Test cases:**

| Input | Expected |
|---|---|
| (all rows) | tests pass `env={"MAGPIE_JEV": "1", "OPENROUTER_API_KEY": "k"}` unless the row says otherwise |
| ok noul response | transport got `JEV_URL`, body `{"model": "~typesafe/jev-latest", "state", "questions"}`, Bearer header, timeout 8.0; `JevResult.answers["q"]["noul"] == 0.92`, usage keys exactly three |
| `env={}` (or `MAGPIE_JEV` unset), valid `api_key` | `disabled`; transport never called |
| env on, `api_key=""` | `missing_key`; transport never called |
| statuses 429 then 200 | success; `sleep` called once with 1.0; 2 transport calls |
| 529 then 529 | `http_529`; exactly 2 calls |
| 500 | `http_500`; 1 call (no retry) |
| 400 `{"error":{"message":"... max_tokens_exceeded ..."}}` | `too_large` |
| 400 other message | `http_400` |
| 403 bare HTML `<title>Attention Required! \| Cloudflare</title>` | `waf_blocked` |
| 403 JSON-wrapped `"HTTP 403: <!DOCTYPE html>...Attention Required"` | `waf_blocked` |
| 403 JSON `{"error":{"message":"forbidden"}}` | `http_403` |
| 401 JSON | `http_401` |
| transport raises `socket.timeout` / `OSError` | `timeout` / `network` |
| 200 body `not json` | `malformed_json` |
| error body echoing the key | key absent from `str(exc)` and `exc.detail` |
| noul value -0.1, 1.2, NaN, inf, True, "0.9", None | `bad_shape` each |
| noul value 0, 1, 0.5, int 1 | accepted as float |
| requested id missing; answer type "choice" for a noul question | `bad_shape` |
| choice label not in options; probabilities `{"x": 1.5}`; unknown probabilities key; confidence NaN | `bad_shape` each |
| score "3", True, NaN | `bad_shape`; score 2.4 accepted |
| response without `model`; response is a list | `bad_shape` |
| `jev_status({})`, `({"MAGPIE_JEV": "0", key})`, `({"MAGPIE_JEV": "true", key})` | `(False, "MAGPIE_JEV not set")` |
| `jev_status({"MAGPIE_JEV": "1"})`, key `"  "` | `(False, "no OPENROUTER_API_KEY")` |
| `jev_status({"MAGPIE_JEV": " 1 ", "OPENROUTER_API_KEY": "k"})` | `(True, None)` |
| `public_reason` of too_large / waf_blocked / http_500 / timeout / bad_shape / missing_key / disabled | too_large / waf_blocked / jev_error x5 |
| subprocess `import scripts.jev_client` | `pandas`, `torch` not in `sys.modules` |

**Steps:**
- [ ] Write `tests/helpers/jev_fakes.py` and `tests/test_jev_client.py` from the table.
- [ ] Run `"$PY" -m pytest tests/test_jev_client.py -q`; expect FAIL (module missing).
- [ ] Implement `scripts/jev_client.py` (borrow structure from `C:\Users\tim\.claude\jevf\jev_client.py`; do not import it).
- [ ] Run `"$PY" -m pytest tests/test_jev_client.py -q`; expect PASS.
- [ ] Commit: `git add scripts/jev_client.py tests/helpers/jev_fakes.py tests/test_jev_client.py && git commit -F "$MSG"` (subject `feat(jev): stdlib Jev client with strict validation`).

**Blocked by:** none.

## Task 2: Secret + PII guards (fail-safe import)

**Files:** Create `scripts/jev_guards.py`, `tests/test_jev_guards.py`.

**Interface:**
- `SECRET_PATTERNS: dict[str, re.Pattern]` (ordered; first hit names the kind):
  ```python
  SECRET_PATTERNS = {
      "pem": re.compile(r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----"),
      "api_key": re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"),
      "aws_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
      "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}|\bgithub_pat_[A-Za-z0-9_]{22,}"),
      "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
      "bearer": re.compile(r"(?i)\bauthorization\s*[:=]\s*\S{8,}|\bbearer\s+[A-Za-z0-9._~+/=\-]{16,}"),
      "secret_kv": re.compile(r"(?i)\b[\w.\-]*(?:password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key|private[_\-]?key|credential)s?[\w.\-]*[\"']?\s*[:=]\s*[\"']?[^\s\"',;]+"),
  }
  HIGH_ENTROPY_RE = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")
  ENTROPY_MIN_BITS = 4.0        # mixed-alphabet token
  HEX_ENTROPY_MIN_BITS = 3.5    # hex-only token (hex tops out at 4.0 bits)
  ```
- `secret_hit(text: str) -> str | None` (pattern kind, else `"high_entropy"`, else None).
- `_pii_patterns() -> dict[str, re.Pattern] | None`: `functools.lru_cache`d; `importlib.import_module("scripts.pii_sweep").DEFAULT_PII_PATTERNS` inside `try/except Exception` -> None on any failure (pii_sweep imports pandas; a broken env must not send unscreened text).
- `pii_hit(text: str) -> str | None`: first matching category name; `"pii_module_unavailable"` whenever `_pii_patterns()` is None (for every input, including empty).
- `guard(*texts: str | None) -> str | None`: `"pii"` if any text has a PII hit, else `"secret"` if any has a secret hit, else None (order follows the spec 2.3 reason order). None entries are skipped, but the PII-unavailable case still returns `"pii"`.

**Test cases:**

| Call | Expected |
|---|---|
| `secret_hit` PEM header block | `pem` |
| `secret_hit("key sk-or-v1-abcdefghijklmnop1234")` | `api_key` |
| `secret_hit("AKIAABCDEFGHIJKLMNOP")` | `aws_key` |
| `secret_hit("ghp_" + "a1"*18)` | `github_token` |
| `secret_hit("Authorization: Bearer abc.def.ghi123")` | `bearer` |
| `password=hunter2`, `secret: s3cr3t`, `api_key = 'x9'`, `"token": "abcd"` | `secret_kv` each |
| a 40-char random base64 string | `high_entropy` |
| `The officer ran 482 searches in March 2026.` | None |
| `The secretary signed the memo.`, `password reset policy`, `bearer bonds`, `basic training` | None each |
| `pii_hit("Call 864-555-0100")` / `SSN 123-45-6789` / `a@b.org` / `DOB` / `03/15/1980` / `8645550100` | phone / ssn / email / dob_kw / possible_birthdate / phone_compact |
| `pii_hit("Officer Ramirez ran 482 searches")` | None |
| `guard("clean", "also clean")` | None |
| `guard("clean", "864-555-0100")` | `pii` |
| `guard("password=x")` | `secret` |
| `guard("password=x 864-555-0100")` | `pii` |
| `guard(None, "clean")` | None |
| `monkeypatch.setitem(sys.modules, "scripts.pii_sweep", None)` + `_pii_patterns.cache_clear()` | `pii_hit("hello") == "pii_module_unavailable"`; `guard("hello") == "pii"`; `guard("") == "pii"` |
| `importlib.import_module` patched in `jev_guards` to raise `RuntimeError` | same as above |

Every test that patches the import calls `_pii_patterns.cache_clear()` before and after (fixture).

**Steps:**
- [ ] Write `tests/test_jev_guards.py`.
- [ ] Run `"$PY" -m pytest tests/test_jev_guards.py -q`; expect FAIL.
- [ ] Implement `scripts/jev_guards.py` (pattern ideas from `C:\Users\tim\.claude\jevf\redact.py`; gate, never redact).
- [ ] Run `"$PY" -m pytest tests/test_jev_guards.py -q`; expect PASS.
- [ ] Commit: `git add scripts/jev_guards.py tests/test_jev_guards.py && git commit -F "$MSG"` (subject `feat(jev): local secret + PII send guards with fail-safe import`).

**Blocked by:** Task 1.

## Task 3: Window packing + single split

**Files:** Modify `scripts/jev_client.py`; create `tests/test_jev_windows.py`.

**Interface:**
- `CHARS_PER_TOKEN = 4`; `WINDOW_TOKEN_BUDGET = 14_000` (Jev's state limit is about 32.7k real tokens; dense text tokenizes worse than chars/4).
- `estimate_tokens(obj) -> int`: `ceil(len(json.dumps(obj, ensure_ascii=False)) / CHARS_PER_TOKEN)`.
- `pack_windows(items: dict[str, dict], *, state_key: str, budget: int = WINDOW_TOKEN_BUDGET) -> tuple[list[list[str]], list[str]]`: greedy in insertion order; a window's estimate is `estimate_tokens({state_key: {k: items[k] for k in window}})`; an item whose solo window exceeds `budget` goes to the second list (oversize) and is never sent.
- `@dataclass class WindowedResult: answers: dict[str, dict[str, dict]]; failures: dict[str, str]; models: list[str]; usage: dict; calls: int; latency_ms: int` (`failures` hold raw client reasons; `models` distinct in first-seen order; `usage` sums the three keys, None treated as 0).
- `ask_windowed(items: dict[str, dict], questions_for: Callable[[str], dict[str, dict]], *, state_key: str, ask_fn: Callable[[dict, dict], JevResult], budget: int = WINDOW_TOKEN_BUDGET) -> WindowedResult`: oversize items get `too_large` with no call. For each window: call `ask_fn(state, questions)`; on `too_large` or `waf_blocked` with more than one item, split once into halves (first half `ceil(n/2)`) and ask each half; a failing half (any reason) marks its items with that reason, no further split. Other reasons mark the window's items. A reason in `ABORT_REASONS` stops all remaining windows, which get the same reason.

**Test cases:**

| Setup | Expected |
|---|---|
| `estimate_tokens({"a": "x"*400})` | `ceil(len(json)/4)` |
| 5 items of ~5k tokens, budget 14k | windows `[[i1,i2],[i3,i4],[i5]]`, oversize `[]` |
| one item ~15k tokens among small ones | it is in oversize, not in any window; order of the rest preserved |
| empty items | `([], [])` |
| all windows succeed | `answers` has every item's qids; `calls == len(windows)`; `models == ["m1"]` |
| 4-item window returns `too_large`, both halves succeed | all answered; `calls == 3` |
| `too_large`, half A ok, half B `too_large` | B items `failures == "too_large"`; `calls == 3` |
| same with `waf_blocked` | B items `waf_blocked` |
| single-item window `too_large` | `failures == {k: "too_large"}`; 1 call |
| window 1 `http_500`, window 2 ok | window 1 items `http_500`; window 2 answered; 2 calls |
| window 1 `http_401` | every item `http_401`; 1 call |
| oversize item | `too_large`; zero calls for it |
| two windows report models m1, m2 | `models == ["m1", "m2"]` |
| usage 10+20 input tokens | summed 30 |

**Steps:**
- [ ] Write `tests/test_jev_windows.py` (fake `ask_fn` raising `JevUnavailable` per call index).
- [ ] Run `"$PY" -m pytest tests/test_jev_windows.py -q`; expect FAIL.
- [ ] Implement in `scripts/jev_client.py`.
- [ ] Run `"$PY" -m pytest tests/test_jev_windows.py tests/test_jev_client.py -q`; expect PASS.
- [ ] Commit: `git add scripts/jev_client.py tests/test_jev_windows.py && git commit -F "$MSG"` (subject `feat(jev): window packing with a single split on too_large/waf_blocked`).

**Blocked by:** Task 1.

## Task 4: Model-approval state

**Files:** Create `scripts/jev_state.py`, `tests/test_jev_state.py`.

**Interface:**
- `REPO_ROOT = Path(__file__).resolve().parent.parent`; `DATA_DIR = REPO_ROOT / "data"` (already gitignored by `/data/`); `STATE_PATH = DATA_DIR / "jev_state.json"`.
- `load_state(path: Path = STATE_PATH) -> dict`: `{}` when missing, unreadable, invalid JSON, or not an object.
- `approved_model(path: Path = STATE_PATH) -> str | None`.
- `model_status(model: str | None, path: Path = STATE_PATH) -> str`: `"unknown"` (model None: nothing was answered), `"unapproved"` (no valid record), `"approved"` (equal), `"changed"` (differs).
- `record_passing_model(model: str, *, eval_summary: dict, path: Path = STATE_PATH, now: datetime | None = None) -> dict`: writes `{"approved_model", "approved_at" (UTC ISO-8601), "eval": eval_summary}` atomically (temp file in the same dir + `os.replace`), creating parent dirs.
- Consumers: Part A skips only on `"approved"`; `"changed"` and `"unapproved"` both give reason `model_changed` (fail-safe: no eval, no skipping). Part B sets `model_changed = (status == "changed")` and warns.

**Test cases:**

| Setup | Expected |
|---|---|
| no file | `load_state == {}`, `approved_model is None` |
| file `{bad json` / `[1,2]` | `{}` |
| `record_passing_model("typesafe/jev-1.13-20260917", eval_summary={"n": 20}, now=fixed)` into `tmp_path/"d"/"s.json"` | dir created; keys exactly `approved_model, approved_at, eval`; `approved_at == "2026-09-24T12:00:00+00:00"`; no `*.tmp` left |
| `model_status(None)` | `unknown` |
| `model_status("m1")`, no file | `unapproved` |
| approved m1: `model_status("m1")` / `("m2")` | `approved` / `changed` |
| corrupt file, `model_status("m1")` | `unapproved` |
| `.gitignore` text | contains the line `/data/` |

**Steps:**
- [ ] Write `tests/test_jev_state.py`.
- [ ] Run `"$PY" -m pytest tests/test_jev_state.py -q`; expect FAIL.
- [ ] Implement `scripts/jev_state.py`.
- [ ] Run `"$PY" -m pytest tests/test_jev_state.py -q`; expect PASS.
- [ ] Commit: `git add scripts/jev_state.py tests/test_jev_state.py && git commit -F "$MSG"` (subject `feat(jev): model-approval state for the model-change safeguard`).

**Blocked by:** none (independent of Tasks 1-3; ordered here per the plan sequence).

## Task 5: Part A numeric/date gate

**Files:** Create `scripts/jev_prescreen.py` (gate functions only), `tests/test_jev_numeric_gate.py`.

**Interface:**
- Date regexes, applied longest-first; each match is blanked out of the text before numbers are read:
  ```python
  _MONTH = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
  DATE_PATTERNS = [
      ("ymd_iso", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
      ("mdy_num", re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b")),
      ("mdy_name", re.compile(rf"(?i)\b{_MONTH}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b")),
      ("dmy_name", re.compile(rf"(?i)\b(\d{{1,2}})\s+{_MONTH}\.?,?\s+(\d{{4}})\b")),
      ("my_name", re.compile(rf"(?i)\b{_MONTH}\.?,?\s+(\d{{4}})\b")),
  ]
  NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
  COMPUTED_CUE_RE = re.compile(
      r"(?i)%|\b(?:total(?:s|ed|ing)?|sum(?:s|med|ming)?|average(?:s|d)?|percent(?:age)?s?|per"
      r"|ratios?|more\s+than|less\s+than|fewer\s+than|increase(?:s|d)?|increasing"
      r"|decrease(?:s|d)?|decreasing|rates?)\b")
  ```
- `extract_dates(text: str) -> tuple[list[tuple[int, ...]], str]`: normalized `(y, m, d)` or `(y, m)`; `mdy_num` is read month/day/year; a 2-digit year becomes `2000+yy` when `yy <= 69`, else `1900+yy`; returns the text with date spans replaced by spaces.
- `normalize_number(tok: str) -> str`: drop thousands commas; drop leading zeros (keep one `0`); strip trailing fractional zeros and a trailing dot (`"482.50"->"482.5"`, `"5.0"->"5"`, `"007"->"7"`).
- `numeric_gate(claim_text: str, span: str) -> str | None`: returns `"numeric_mismatch"` if any claim date has no span date agreeing on every component the claim date has, or any claim number (from the date-blanked claim text) is not in the span's number set; else `"computed_value"` if `COMPUTED_CUE_RE` matches the claim; else None. numeric_mismatch is checked first (spec reason order). The span number set is the normalized numbers of the date-blanked span PLUS only the 4-digit YEAR of each span date; a span date's month and day never join it (so a claim's "15" cannot be satisfied by "March 15").
- The gate is digit-only: spelled-out numbers ("fourteen") are not extracted or compared; Jev's entailment question and the verifier cover them. Documented in the jev-guide (Task 11) and Decision 9.

**Test cases (`numeric_gate(claim, span)`):**

| # | claim | span | expected |
|---|---|---|---|
| 1 | Officer Ramirez ran 482 searches in March 2026. | Officer Ramirez ran 482 searches in March 2026, exceeding the quota. | None |
| 2 | ran 1,482 searches | ran 1482 searches | None |
| 3 | ran 483 searches | ran 482 searches | numeric_mismatch |
| 4 | On 2026-03-15 the audit ran | On March 15, 2026 the audit ran | None |
| 5 | On 3/15/2026 | on 15 March 2026 | None |
| 6 | On March 16, 2026 | on March 15, 2026 | numeric_mismatch |
| 7 | In March 2026 | on March 15, 2026 | None |
| 8 | In April 2026 | on March 15, 2026 | numeric_mismatch |
| 9 | during 2026 | on March 15, 2026 | None |
| 10 | Sept. 3, 2025 | logged 2025-09-03 | None |
| 11 | The total was 482 | 482 searches | computed_value |
| 12 | Searches increased after the policy | Searches rose after the policy | computed_value |
| 13 | 12% of searches were flagged | 12% of searches were flagged | computed_value |
| 14 | three searches per day | three searches per day | computed_value |
| 15 | more than 400 searches | 482 searches | numeric_mismatch |
| 16 | ran 482 searches | ran 4820 searches | numeric_mismatch |
| 17 | paid 482.50 dollars | paid 482.5 dollars | None |
| 18 | Case 2026-0042 was closed | Case 2026-0042 was closed | None |
| 19 | The unit operated separately | The unit operated separately | None |
| 20 | The officer ran searches. | The officer ran searches in 2026. | None |
| 21 | The rate was 5 | the rate was 5 | computed_value |
| 22 | ran 15 searches | On March 15, 2026 the officer ran 14 searches | numeric_mismatch (day 15 is not in the number set) |

Rows 1-21 were re-checked under the year-only rule: row 9 passes via the span date's year 2026; rows 7 and 10 pass via date matching, not the number set; row 18 passes because `2026-0042` is not a date and both sides yield `2026` and `42`.

Plus direct cases: `extract_dates("on 3/4/99")[0] == [(1999, 3, 4)]`; `extract_dates("May 2026")[0] == [(2026, 5)]`; `extract_dates("you may 2 go")[0] == []`; `normalize_number("007") == "7"`.

**Steps:**
- [ ] Write `tests/test_jev_numeric_gate.py` (parametrized from the table).
- [ ] Run `"$PY" -m pytest tests/test_jev_numeric_gate.py -q`; expect FAIL.
- [ ] Implement the gate functions in `scripts/jev_prescreen.py`.
- [ ] Run `"$PY" -m pytest tests/test_jev_numeric_gate.py -q`; expect PASS.
- [ ] Commit: `git add scripts/jev_prescreen.py tests/test_jev_numeric_gate.py && git commit -F "$MSG"` (subject `feat(jev): deterministic numeric/date + computed-value gate`).

**Blocked by:** none (pure); sequenced after Task 4.

## Task 6: Part A routing + CLI

**Files:** Modify `scripts/jev_prescreen.py`; create `tests/test_jev_prescreen.py`.

**Interface:**
- Constants: `PRESENCE_MIN = 0.85`, `ENTAIL_MIN = 0.85` (starting values; Task 14 may only raise them), `STATE_KEY = "claims"`, `REASONS = ("jev_off", "model_changed", "pii", "secret", "too_large", "waf_blocked", "jev_error", "degraded_anchor", "numeric_mismatch", "computed_value", "low_score")`.
- `@dataclass(frozen=True) class ClaimInput: claim_id: str; claim_text: str; verbatim_quote: str; span: str | None; clean_citation: bool`.
- `parse_claims(raw: object) -> list[ClaimInput]`: raises `ValueError` unless raw is a list of objects with exactly-typed keys (`claim_id` non-empty str, unique; `claim_text`, `verbatim_quote` str; `span` str or None; `clean_citation` bool).
- `item_key(index: int) -> str`: `f"K{index + 1:02d}"` (K01..K99, K100...). Positional; claim ids never leave the machine.
- `build_questions(key: str) -> dict[str, dict]`: exactly `presence_<key>` and `entail_<key>`, noul, wording as in the wire-format block.
- `prescreen(claims: list[ClaimInput], *, env: Mapping | None = None, ask_fn: Callable | None = None, state_path: Path = jev_state.STATE_PATH, seed: str | None = None, enforce_model_gate: bool = True) -> dict`.
  1. `jev_status(env)` off -> every claim `verify`/`jev_off`; no call.
  2. Per claim, in order, the first local reason: `guard(claim_text, verbatim_quote, span)` (`pii`/`secret`); `degraded_anchor` if not `clean_citation` or span is None/blank; `numeric_gate(...)`. Claims with a local reason are NOT sent (data minimization); presence/entailment stay null.
  3. Remaining claims -> `ask_windowed(..., state_key="claims")`; default `ask_fn` is `functools.partial(jev_client.ask, env=env)`. Failures -> `public_reason(raw)`.
  4. Model: the model gate applies ONLY when at least one window returned a model (`models` non-empty). Then `model = models[0]`; if `enforce_model_gate` and any seen model has `model_status` other than `"approved"`, every claim (sent or not) becomes `verify`/`model_changed`. If no window answered, `model` is None and each claim keeps its real reason (`too_large`, `waf_blocked`, `jev_error`, or its local reason).
  5. Answered claims: `skip` iff `presence >= PRESENCE_MIN and entailment >= ENTAIL_MIN`, else `low_score`.
  6. Spot-checks: Task 7 (`spot_check` is False for every claim until then).
- Output (JSON-able): `{"enabled": bool, "model": str|None, "approved_model": str|None, "seed": str, "claims": {claim_id: {"presence": float|None, "entailment": float|None, "route": "skip"|"verify", "reason": str|None, "spot_check": bool}}, "summary": {"prescreened": n, "skipped": m, "verify": v, "spot_checked": s}, "usage": {...}}`. `reason` is None exactly when route is `skip`.
- CLI: `python scripts/jev_prescreen.py <claims.json> [--seed S]` -> output JSON on stdout (UTF-8), exit 0; unreadable/invalid input -> stderr message, exit 2, no network.

**Test cases** (fake transport via `tests/helpers/jev_fakes.py`; env on with key `"k"`; state file in `tmp_path` approving `m1` unless stated):

| Setup | Expected |
|---|---|
| env `{}` | `enabled False`; all `verify`/`jev_off`; transport not called |
| response model `m2` | all claims `verify`/`model_changed`, including a PII-guarded one |
| no state file | all `model_changed` |
| no state file, every window `http_500` (no model returned) | reasons stay `jev_error` (not `model_changed`); `model is None` |
| approved m1, sole window `too_large`, other claims locally gated | `too_large` + their local reasons; no `model_changed` |
| `enforce_model_gate=False`, no state file, scores 0.95/0.95 | `skip` |
| phone `864-555-0100` in span | `verify`/`pii`; phone string absent from every request body |
| `password=hunter2` in claim | `verify`/`secret`; string absent from bodies |
| `clean_citation False` | `degraded_anchor`; not sent |
| `clean_citation True`, span None | `degraded_anchor` |
| claim 483 vs span 482 | `numeric_mismatch`; not sent |
| claim contains "total" | `computed_value`; not sent |
| presence 0.95, entail 0.84 | `low_score` |
| presence 0.84, entail 0.95 | `low_score` |
| presence 0.85, entail 0.85 | `skip`, `reason None` |
| window `too_large` / `waf_blocked` / `http_500` / `bad_shape` | reason `too_large` / `waf_blocked` / `jev_error` / `jev_error` |
| one claim with a 60,000-char span | `too_large`; never sent |
| PII module import patched to fail | every claim `verify`/`pii`; transport never called |
| claim_id `"doc-abc123:c1"` | request body state key is `K01`; `doc-abc123` absent from bodies |
| request questions | ids `presence_K01`, `entail_K01`; type noul; criteria match the wire block |
| output shape | top keys as above; each claim has exactly the five keys |
| `parse_claims` missing key / duplicate id / `clean_citation: "yes"` | `ValueError` |
| subprocess `scripts/jev_prescreen.py claims.json`, env without `MAGPIE_JEV` | exit 0; stdout JSON all `jev_off` |
| subprocess with a malformed claims file | exit 2 |

**Steps:**
- [ ] Write `tests/test_jev_prescreen.py`.
- [ ] Run `"$PY" -m pytest tests/test_jev_prescreen.py -q`; expect FAIL.
- [ ] Implement routing + CLI (with the script-mode shim).
- [ ] Run `"$PY" -m pytest tests/test_jev_prescreen.py tests/test_jev_numeric_gate.py -q`; expect PASS.
- [ ] Commit: `git add scripts/jev_prescreen.py tests/test_jev_prescreen.py && git commit -F "$MSG"` (subject `feat(jev): Part A pre-screen routing and CLI`).

**Blocked by:** Tasks 2, 3, 4, 5.

## Task 7: Spot-checks, disagreement log, run summary

**Files:** Modify `scripts/jev_prescreen.py`; create `tests/test_jev_spotcheck.py`.

**Interface:**
- `SPOT_CHECK_RATE = 0.10`; `MIN_SPOT_CHECKS = 1` (at least one spot-check whenever any claim skips); `SPOTCHECK_LOG = jev_state.DATA_DIR / "jev_spotcheck.jsonl"`.
- `default_seed(claims: list[ClaimInput]) -> str`: first 16 hex of sha256 over `json.dumps(sorted([c.claim_id, c.claim_text] for c in claims))`.
- `spot_hash(claim_id: str, seed: str) -> float`: `int(sha256(f"{seed}:{claim_id}").hexdigest()[:8], 16) / 0xFFFFFFFF`.
- `select_spot_checks(skip_ids: list[str], seed: str, rate: float = SPOT_CHECK_RATE) -> set[str]`: ids with `spot_hash < rate`; if empty and `skip_ids` non-empty, the single id with the smallest hash.
- `prescreen` sets `spot_check` True only for selected `skip` claims and puts the seed in the output.
- `normalize_verdicts(raw: dict) -> dict[str, str]`: accepts `{id: "supported"}` or `{id: {"result": "supported", ...}}`.
- `log_spotcheck_disagreements(output: dict, verdicts: dict[str, str], *, log_path: Path = SPOTCHECK_LOG, now: datetime | None = None) -> list[dict]`: for every `spot_check` claim whose verdict is not `"supported"` (a missing verdict counts, logged as `"missing"`), append one JSON line `{"ts", "claim_id", "presence", "entailment", "model", "verdict", "seed"}`; no claim text; creates the parent dir; appends.
- `run_summary(output: dict, disagreements: int) -> str`: `"{n} claims pre-screened | {m} skipped | {k} sent to extraction-verifier | {s} spot-checked | {d} disagreements"` with n = all claims, m = route skip, s = spot-checked, k = verify count + s.
- CLI mode: `python scripts/jev_prescreen.py --spotcheck <prescreen_output.json> --verdicts <verdicts.json>` -> appends the log, prints the summary line, exit 0; bad files exit 2.

**Test cases:**

| Setup | Expected |
|---|---|
| `spot_hash("K1", "s")` twice | equal |
| rate 0.0 with 3 skip ids | exactly 1 id (MIN_SPOT_CHECKS) |
| rate 1.0 | all ids |
| 1,000 ids `c0000..c0999`, seed `"s"` | selected fraction in [0.07, 0.13] |
| 200 ids, seeds `"a"` vs `"b"` | selections differ |
| no skip ids | empty set |
| prescreen with 3 skip + 2 verify claims | `spot_check` True only on skip claims; at least one True |
| `default_seed` same claims twice / one claim_text changed | equal / different |
| verdict `supported` for the spot-checked claim | no line written |
| verdict `indeterminate` | one line; keys exactly the seven above; no `claim_text` value in the line |
| verdict missing | one line with `"verdict": "missing"` |
| verdict for a non-spot-checked claim | ignored |
| two calls | file has both lines (append) |
| `run_summary` on 5 claims (3 skip incl. 1 spot-check, 2 verify), d=1 | `"5 claims pre-screened \| 3 skipped \| 3 sent to extraction-verifier \| 1 spot-checked \| 1 disagreements"` |
| subprocess `--spotcheck out.json --verdicts v.json` (log path via `MAGPIE_JEV_SPOTCHECK_LOG` env override pointing into `tmp_path`) | exit 0; stdout is the summary line; log appended |

Add the env override `MAGPIE_JEV_SPOTCHECK_LOG` (read only by `main`) so the subprocess test never writes the real `data/`.

**Steps:**
- [ ] Write `tests/test_jev_spotcheck.py`.
- [ ] Run `"$PY" -m pytest tests/test_jev_spotcheck.py -q`; expect FAIL.
- [ ] Implement.
- [ ] Run `"$PY" -m pytest tests/test_jev_spotcheck.py tests/test_jev_prescreen.py -q`; expect PASS.
- [ ] Commit: `git add scripts/jev_prescreen.py tests/test_jev_spotcheck.py && git commit -F "$MSG"` (subject `feat(jev): deterministic spot-checks + disagreement log`).

**Blocked by:** Task 6.

## Task 8: Audit fields + gate label

**Files:** Modify `scripts/citation.py`, `scripts/jev_prescreen.py`, `tests/test_citation.py`; create `tests/test_jev_audit.py`.

**Interface:**
- `CitationRecord` gains a trailing field `prescreen: Optional[Dict[str, Any]] = None` (after `timestamp`). `to_dict()` includes it; `_PUBLIC_ANCHOR_KEYS` is unchanged (still exactly 10 keys).
- `jev_prescreen.PRESCREEN_KEYS = ("presence", "entailment", "route", "reason", "model", "spot_check")`.
- `prescreen_record(entry: dict, model: str | None) -> dict`: exactly `PRESCREEN_KEYS`.
- `PRESCREEN_VERIFIER_RESULT = "prescreen-skip"`: the `verifier_result` stored (and published through `public_anchor`) for a skipped claim that no extraction-verifier saw; `verifier_confidence` stays None.
- `GATE_LABEL = "Jev pre-screen: supported -- not independently verified (presence {presence:.2f}, entailment {entailment:.2f})"`; `gate_label(prescreen: dict | None) -> str | None` returns it for `route == "skip"`, else None.
- `claim_input_from_record(claim_id: str, record: CitationRecord, docling_json: dict) -> dict`: runs `citation.resolve_anchor` + `is_clean_citation`; `span` is the resolved block's `.text` when `block_index` is not None, else None; returns the five-key input dict.

**Test cases:**

| Call | Expected |
|---|---|
| `CitationRecord(...)` without prescreen | `to_dict()["prescreen"] is None`; JSON round-trip ok |
| with `prescreen=prescreen_record(entry, "m1")` | round-trips; `public_anchor()` keys unchanged (existing set-equality test still passes) |
| `prescreen_record` | keys exactly `PRESCREEN_KEYS` |
| `gate_label({"route": "skip", "presence": 0.94, "entailment": 0.91, ...})` | `"Jev pre-screen: supported -- not independently verified (presence 0.94, entailment 0.91)"` |
| `gate_label({"route": "verify", ...})`, `gate_label(None)` | None |
| `claim_input_from_record` on an exact anchor (`tests/conftest_citation.py` `make_block`/`make_doc`) | `clean_citation True`, `span == block text` |
| same record against a doc where the block is gone | `clean_citation False` |
| output of `claim_input_from_record` | accepted by `parse_claims` |

**Steps:**
- [ ] Write `tests/test_jev_audit.py`; add the prescreen round-trip test to `tests/test_citation.py`.
- [ ] Run `"$PY" -m pytest tests/test_jev_audit.py tests/test_citation.py -q`; expect FAIL.
- [ ] Implement.
- [ ] Run `"$PY" -m pytest tests/test_jev_audit.py tests/test_citation.py tests/test_jev_prescreen.py -q`; expect PASS.
- [ ] Commit: `git add scripts/citation.py scripts/jev_prescreen.py tests/test_jev_audit.py tests/test_citation.py && git commit -F "$MSG"` (subject `feat(jev): prescreen audit fields + human-gate label`).

**Blocked by:** Task 7.

## Task 9: investigate section 2 wiring, verifier + gate labeling, archive-evidence carry-through

**Files:** Modify `skills/investigate/SKILL.md`, `agents/extraction-verifier.md`, `skills/archive-evidence/SKILL.md`, `tests/test_investigate_skill.py`, `tests/test_investigate_agents.py`, `tests/test_archive_evidence_skill.py`.

**Interface (text contracts):**
- investigate section 2 gains this subsection (ASCII; adjust line wrapping only):
  ```
  ### Jev pre-screen (optional, off by default)

  If Jev is on (MAGPIE_JEV=1 and OPENROUTER_API_KEY set; doctor shows "jev: on"),
  run the pre-screen on the whole batch after anchoring, before dispatching agents:

  1. Build each claim's input with scripts/jev_prescreen.py claim_input_from_record
     (it runs resolve_anchor + is_clean_citation and uses the resolved block .text as
     the span), write the list to a local JSON file, and run
     `python scripts/jev_prescreen.py <claims.json>`.
  2. Dispatch citation-checker for EVERY claim, whatever the pre-screen says.
  3. Dispatch extraction-verifier for every claim whose route is "verify" OR whose
     spot_check is true. Only a claim with route "skip" and spot_check false goes
     without it.
  4. When the spot-check verifiers return, run
     `python scripts/jev_prescreen.py --spotcheck <prescreen.json> --verdicts <verdicts.json>`.
     It logs every disagreement (verifier result not "supported") to
     data/jev_spotcheck.jsonl and prints the run summary line; show that line to the
     human with the batch.
  5. Store prescreen_record(...) on each CitationRecord.prescreen. A skipped claim with
     no verifier run gets verifier_result "prescreen-skip" and verifier_confidence null.

  The pre-screen can only remove extraction-verifier calls. It never accepts, rejects
  or edits a claim, and never replaces the citation-checker or the human gate. When Jev
  is off, the model has changed, or anything fails, every claim routes to "verify" and
  this gate runs exactly as it does without Jev. Guidance:
  ../dataset-analyze/references/jev-guide.md.
  ```
- investigate section 3, existing "Editing invalidates verification" paragraph: append one
  sentence to that paragraph (not a new paragraph): "An edited claim always gets the
  extraction-verifier, even if the Jev pre-screen had routed it to skip; its old pre-screen
  result no longer applies."
- investigate section 3 item 3: for a skipped claim the card shows the `gate_label` text ("Jev pre-screen: supported -- not independently verified (presence 0.94, entailment 0.91)"), never "verified"; spot-checked claims show both that label and the verifier verdict; a spot-check disagreement is surfaced prominently.
- investigate section 4: the local citations log keeps the `prescreen` block; the published anchor carries `verifier_result` "prescreen-skip" for skipped claims.
- `agents/extraction-verifier.md`: a short paragraph: you may be dispatched as a spot-check of a claim Jev pre-screened as supported; your input is identical and equally blinded; you NEVER receive the Jev pre-screen scores; judge exactly as always; a not-supported verdict is logged as a disagreement for the human.
- `skills/archive-evidence/SKILL.md`: under section 4, one paragraph: an investigate citations log archived as evidence keeps each claim's `prescreen` block (presence, entailment, route, reason, model, spot_check) unchanged; archive_evidence hashes the file as received, so the block is covered by the receipt hash and timestamp.

**Test cases (append to existing smoke tests):**

| File | Assertion (on lowercased body unless noted) |
|---|---|
| investigate | contains `jev_prescreen.py`, `citation-checker for every claim`, `spot_check`, `route`, `--spotcheck`, `never accepts`, `prescreen-skip`, `jev-guide.md` |
| investigate section 3 (text between `## 3` and `## 4`) | contains `not independently verified`; the paragraph containing `editing invalidates verification` also contains `an edited claim always gets the extraction-verifier` |
| investigate | existing ASCII test still passes |
| extraction-verifier | contains `pre-screen`, `spot-check`, and `never receive` |
| archive-evidence | contains `prescreen` and `receipt hash` |

**Steps:**
- [ ] Add the assertions.
- [ ] Run `"$PY" -m pytest tests/test_investigate_skill.py tests/test_investigate_agents.py tests/test_archive_evidence_skill.py -q`; expect FAIL.
- [ ] Edit the three markdown files.
- [ ] Re-run the same command; expect PASS.
- [ ] Commit: `git add skills/investigate/SKILL.md agents/extraction-verifier.md skills/archive-evidence/SKILL.md tests/test_investigate_skill.py tests/test_investigate_agents.py tests/test_archive_evidence_skill.py && git commit -F "$MSG"` (subject `feat(investigate): wire the optional Jev pre-screen into the verification gate`).

**Blocked by:** Task 8.

## Task 10: Part B `jev_ask` CLI

**Files:** Create `scripts/jev_ask.py`, `tests/test_jev_ask.py`.

**Interface:**
- CLI (argparse): `--input PATH` (`.jsonl` or `.csv`, else exit 2), `--id-field NAME`, `--text-fields F1,F2`, `--type {noul,choice,score}`, `--question TEXT`, `--criteria-true TEXT`, `--criteria-false TEXT`, `--options PATH` (JSON object label -> description, 2-255 entries, non-empty string labels), `--levels PATH` (JSON list of 2-10 strings), `--out PATH`, `--sample N` (int >= 1), `--seed S` (int, default 0).
- Exit codes: `EXIT_OK = 0`, `EXIT_USAGE = 2` (bad args/input, question/criteria/options/levels trip `guard`, `--out` resolves to the input path, missing or duplicate id), `EXIT_JEV_OFF = 3` (stderr `jev: off (<reason>)`; no output files; no call).
- `STATE_KEY = "records"`; `record_key(i) = f"R{i + 1:04d}"`; question id `f"q_{key}"`; instructions `f"Answer only about record {key}. {question}"`; criteria from the flags (`noul`), options map (`choice`), levels list (`score`).
- `load_records(path: Path, id_field: str, text_fields: list[str]) -> list[tuple[str, dict[str, str]]]`: stdlib `json`/`csv`; missing text fields -> `""`; values coerced with `str()`; `ValueError` on missing/duplicate id.
- Per record: all text fields blank -> `skipped: empty`; `guard(*values)` -> `skipped: pii|secret`; else windowed with `ask_windowed`; failures -> `public_reason` (`too_large|waf_blocked|jev_error`).
- `shape_answer(qtype, raw) -> dict`: noul `{"p"}`; choice `{"label", "probabilities", "confidence"}`; score `{"value", "probabilities", "confidence"}`.
- Output `--out`: one line per input record, input order: `{"id": <id>, "answer": {...}}` or `{"id": <id>, "skipped": "<reason>"}`. The source file is never written.
- `derived_paths(out: Path) -> tuple[Path, Path]`: `results.jsonl` -> `results.meta.json`, `results.sample.jsonl`; any other suffix appends `.meta.json` / `.sample.jsonl` to the full name.
- Meta keys exactly: `question, type, criteria, options, levels, id_field, text_fields, model, approved_model, model_changed, timestamp, counts, input_file (basename), input_sha256, usage, sample`; `counts` keys exactly `answered, pii, secret, too_large, waf_blocked, jev_error, empty`; `sample` is `{"n": N, "seed": S}` or null.
- Model: `model_status` `"changed"` -> meta `model_changed: true` and stderr `WARNING: Jev model changed (approved <a>, got <b>); re-run the Part A live eval before relying on these answers.`; results still written.
- `select_sample(rows, n, seed) -> list[dict]`: `random.Random(seed).sample` over answered rows, `min(n, len)`, returned in input order.

**Test cases** (fake transport; state in `tmp_path`):

| Setup | Expected |
|---|---|
| noul without `--criteria-false` | exit 2 |
| choice without `--options`; options with 1 / 256 entries | exit 2 |
| score levels with 1 / 11 entries | exit 2 |
| `--input data.txt` | exit 2 |
| a record missing the id field; duplicate ids | exit 2 |
| `--out` equal to `--input` | exit 2; input unchanged |
| `--question "use password=hunter2"` | exit 2; transport never called |
| env without `MAGPIE_JEV` | exit 3; stderr `jev: off (MAGPIE_JEV not set)`; no out/meta files; no call |
| noul, 3 JSONL records | 3 lines, input order, `{"id": "a", "answer": {"p": 0.9}}` |
| choice, CSV input | `answer` keys `label, probabilities, confidence` |
| score | `answer` keys `value, probabilities, confidence` |
| record with phone / `api_key = 'x9'` / blank fields | `skipped` `pii` / `secret` / `empty` |
| one oversize record; window 403 WAF page; window 500 | `too_large`; `waf_blocked`; `jev_error` |
| canary: phone, secret, every record id, and the input file name | absent from every request body |
| request state | `{"records": {"R0001": {"F1": ..., "F2": ...}}}`; question ids `q_R0001...`; instructions contain `record R0001` |
| meta | exact key sets above; `input_sha256` equals `hashlib.sha256` of the input bytes; counts sum to record count |
| input file bytes after the run | unchanged |
| approved m1, response m2 | exit 0; meta `model_changed: true`; stderr WARNING |
| no state file | meta `model_changed: false`, `approved_model: null` |
| `--sample 2 --seed 7` twice | identical sample files; rows are answered rows only |
| `--sample 50` with 3 answered | 3 rows |
| PII import patched to fail | every record `skipped: pii`; no call |
| `derived_paths(Path("r.jsonl"))`, `(Path("r.out"))` | `r.meta.json`, `r.sample.jsonl`; `r.out.meta.json`, `r.out.sample.jsonl` |
| subprocess `scripts/jev_ask.py ...` with Jev off | exit 3 |

**Steps:**
- [ ] Write `tests/test_jev_ask.py` (call `jev_ask.main(argv, env=..., ask_fn=..., state_path=...)`; `main` takes those keyword overrides for tests).
- [ ] Run `"$PY" -m pytest tests/test_jev_ask.py -q`; expect FAIL.
- [ ] Implement `scripts/jev_ask.py` (with the script-mode shim).
- [ ] Run `"$PY" -m pytest tests/test_jev_ask.py -q`; expect PASS.
- [ ] Commit: `git add scripts/jev_ask.py tests/test_jev_ask.py && git commit -F "$MSG"` (subject `feat(jev): Part B jev_ask batched dataset question tool`).

**Blocked by:** Tasks 2, 3, 4.

## Task 11: `jev-guide.md` + skill links

**Files:** Create `skills/dataset-analyze/references/jev-guide.md`, `tests/test_jev_guide.py`; modify `skills/dataset-analyze/SKILL.md` (Resources list), `skills/entity-crossref/SKILL.md` (a one-line pointer near "The flow": use jev_ask for a "same entity?" pre-screen before cross-ref), `skills/investigate/SKILL.md` (link already added in Task 9).

**Interface (guide sections, ASCII, these exact `##` headings):**
- `## What Jev is` (typed yes/no, choice, score answers over shared text; no generation, arithmetic or multi-step reasoning; the investigate pre-screen uses the same client).
- `## Turning it on` (`MAGPIE_JEV=1` + `OPENROUTER_API_KEY`; `doctor` shows the jev line; Part A skips nothing until the operator runs the live eval: `"$PY" -m pytest -m jev_live tests/test_jev_live_prescreen.py -s`).
- `## Use it for` (spec 3.2 list) / `## Don't use it for` (spec 3.2 list; pandas/SQL for aggregates).
- `## Writing questions` (one judgment per question; contrastive true/false criteria with examples; mutually exclusive choice labels with a none/other option) with one worked `jev_ask` command.
- `## Reading answers` (act / check / escalate zones; thresholds set from a `--sample` spot-check, not assumed; probabilities are relative confidence, not ground truth).
- `## Privacy` (opt-in required; exactly what is sent; rows with structured PII or secret-shaped text stay local and show as skipped. Explain unexpected `skipped: secret` rows: the secret screen is deliberately broad, so benign text such as "Token count: 5" is withheld on purpose. Names are not pattern PII: personal names in the chosen text fields ARE sent when Jev is on. Part B users should keep name, address and DOB columns out of `--text-fields` unless the question needs them.)
- A short note (under `## Reading answers` or `## What Jev is`) that the pre-screen's number/date check is digit-only: spelled-out numbers ("fourteen") are not gated, only judged by Jev and the verifier.
- `## Provenance` (keep `results.meta.json` with any derived dataset; cite it in methodology notes; a `model_changed: true` meta means re-check before use).

**Test cases:**

| Assertion | Expected |
|---|---|
| guide exists, ASCII | True |
| all seven headings present | True |
| lowercased guide contains `act`, `check`, `escalate`, `--sample`, `results.meta.json`, `pandas`, `entity-crossref`, `magpie_jev`, `openrouter_api_key`, `names`, `jev_live`, `skipped: secret`, `token count`, `--text-fields`, `spelled-out` | True |
| `skills/dataset-analyze/SKILL.md`, `skills/entity-crossref/SKILL.md`, `skills/investigate/SKILL.md` each contain `jev-guide.md` | True |
| entity-crossref and investigate stay ASCII (existing tests) | True |

**Steps:**
- [ ] Write `tests/test_jev_guide.py`.
- [ ] Run `"$PY" -m pytest tests/test_jev_guide.py -q`; expect FAIL.
- [ ] Write the guide and the two links.
- [ ] Run `"$PY" -m pytest tests/test_jev_guide.py tests/test_entity_crossref_skill.py tests/test_dataset_analyze_wiring.py tests/test_investigate_skill.py -q`; expect PASS.
- [ ] Commit: `git add skills/dataset-analyze/references/jev-guide.md skills/dataset-analyze/SKILL.md skills/entity-crossref/SKILL.md tests/test_jev_guide.py && git commit -F "$MSG"` (subject `docs(jev): jev_ask usage guide linked from three skills`).

**Blocked by:** Tasks 9, 10.

## Task 12: doctor jev line

**Files:** Modify `scripts/detect_tier.py`, `skills/doctor/SKILL.md`, `tests/test_detect_tier.py`, `tests/test_doctor_skill.py`.

**Interface:**
- Script-mode shim at the top of `detect_tier.py`, then `from scripts.jev_client import jev_status` (stdlib only; import stays cheap).
- `jev_line(env: Mapping[str, str] | None = None) -> str`: `"jev: on"` or `f"jev: off ({reason})"`.
- `detect(mcp_json_path=None, repo_root=None, env=None)` adds `"jev": {"enabled": bool, "reason": str | None, "line": str}`. The key value is never stored.
- `render_text(report)` appends a blank line and `report["jev"]["line"]` when `"jev"` is present (reports without it still render, keeping the existing render test valid).
- doctor SKILL.md: new sentence in section 2 naming the `jev: on` / `jev: off (MAGPIE_JEV not set)` / `jev: off (no OPENROUTER_API_KEY)` line; section 3: the jev line only reads two environment variables, never contacts OpenRouter or Jev, and never prints the key.

**Test cases:**

| Call | Expected |
|---|---|
| `jev_line({})` | `jev: off (MAGPIE_JEV not set)` |
| `jev_line({"MAGPIE_JEV": "1"})` | `jev: off (no OPENROUTER_API_KEY)` |
| `jev_line({"MAGPIE_JEV": "1", "OPENROUTER_API_KEY": "sk-or-canary-123"})` | `jev: on` |
| `render_text(detect(env=on_env))` and `json.dumps(detect(env=on_env))` | contain `jev: on`; `sk-or-canary-123` absent from both |
| existing `test_render_text_has_no_tier_language` report (no `jev`) | still passes |
| subprocess `scripts/detect_tier.py` with `MAGPIE_JEV` removed from env | exit 0; stdout has `jev: off (MAGPIE_JEV not set)` |
| existing `test_import_is_cheap` | still passes |
| doctor SKILL.md | contains `jev: on`, `jev: off`, `openrouter`; existing read-only assertions pass |

**Steps:**
- [ ] Add the tests.
- [ ] Run `"$PY" -m pytest tests/test_detect_tier.py tests/test_doctor_skill.py -q`; expect FAIL.
- [ ] Implement + edit the skill.
- [ ] Re-run; expect PASS.
- [ ] Commit: `git add scripts/detect_tier.py skills/doctor/SKILL.md tests/test_detect_tier.py tests/test_doctor_skill.py && git commit -F "$MSG"` (subject `feat(doctor): report jev on/off`).

**Blocked by:** Task 1.

## Task 13: README privacy (keep onramp invariants)

**Files:** Modify `README.md`; create `tests/test_readme_jev_privacy.py`.

README is reader-facing: invoke `copydesk:write` before drafting the prose (user rule).

**Interface (intent, not wording):**
- Intro line 7 ("Your records never leave your machine...") gains the qualifier: true by default; two optional Jev features send limited text only when explicitly turned on.
- The "Your data & privacy" callout gains a paragraph that states: off by default; enabled only with `MAGPIE_JEV=1` plus `OPENROUTER_API_KEY`; the endpoint (OpenRouter, to TypeSafe's Jev model); exactly what is sent (pre-screen: claim text, quote and the cited passage per claim; `jev_ask`: the chosen text fields of each record plus the question); never whole documents, file names, paths or document IDs; local guards withhold any item with secret-shaped text or structured PII (phone, SSN, email, DOB and similar patterns) and treat a broken PII module as a hit; person names are not screened and can be sent; pre-screen results are labeled "not independently verified" and never replace the human gate.
- Keep: no "docker"; `OPERATOR_GUIDE.md`, `JOURNALIST_START.md`, "two onramps", setup, doctor, `detect_tier`.

**Test cases:**

| Assertion | Expected |
|---|---|
| privacy callout (the paragraph block starting `> **Your data & privacy:**` through the next heading), lowercased | contains `by default`, `magpie_jev`, `openrouter_api_key`, `openrouter`, `typesafe`, `pii`, `names`, `not independently verified` |
| intro sentence containing `never leave your machine` (if kept) | also contains `by default` |
| whole README | contains `jev-guide.md` or `jev_ask` |
| `tests/test_onramp_docs.py` | passes unchanged |

**Steps:**
- [ ] Write `tests/test_readme_jev_privacy.py`.
- [ ] Run `"$PY" -m pytest tests/test_readme_jev_privacy.py -q`; expect FAIL.
- [ ] Invoke `copydesk:write`, then edit `README.md`.
- [ ] Run `"$PY" -m pytest tests/test_readme_jev_privacy.py tests/test_onramp_docs.py -q`; expect PASS.
- [ ] Commit: `git add README.md tests/test_readme_jev_privacy.py && git commit -F "$MSG"` (subject `docs(README): qualify the privacy statement for the opt-in Jev features`).

**Blocked by:** Tasks 9, 10, 11.

## Task 14: Live eval (Part A) + live smoke (Part B)

**Files:** Modify `pyproject.toml`, `.github/workflows/ci.yml`; create `tests/fixtures/jev/prescreen_eval.json`, `tests/fixtures/jev/ask_smoke.jsonl`, `tests/test_jev_live_prescreen.py`, `tests/test_jev_live_ask.py`.

**Interface:**
- `pyproject.toml`: add marker `"jev_live: live OpenRouter/Jev calls (needs MAGPIE_JEV=1 + OPENROUTER_API_KEY); select with -m jev_live"` and `addopts = "-m \"not jev_live\""` so a bare `pytest` never runs them (an explicit `-m` on the command line replaces it).
- `ci.yml` offline job: append ` and not jev_live` to the `-m` expression. No CI job runs live Jev.
- Both live modules: `pytestmark = [pytest.mark.jev_live, pytest.mark.skipif(not jev_status()[0], reason=...)]`.
- `prescreen_eval.json`: 20 synthetic items `{claim_id, claim_text, verbatim_quote, span, clean_citation: true, expect: "supported"|"unsupported", category}`: supported 5, paraphrased_supported 3, wrong_number 3, wrong_date 2, wrong_entity 3, span_silent 2, contradicted 2. At least two wrong-entity/silent items carry numbers that DO match the span (so they reach Jev, not the numeric gate). Dates written as "March 3, 2026" (not MM/DD/YYYY, which the PII guard withholds).
- Eval test: `out = prescreen(claims, enforce_model_gate=False)`; fail if any claim reason is `too_large`/`waf_blocked`/`jev_error`/`pii`/`secret` (a degenerate run must not approve a model); **gate: every `unsupported` item routes `verify`**; print per-claim presence/entailment and the skip rate over `supported` items, followed by the note "Real-corpus skip rates will be lower than this fixture's: PII patterns (compact 10-digit phone numbers that also match case numbers, MM/DD/YYYY birthdate-format dates) withhold whole claims before Jev sees them."; on pass call `record_passing_model(out["model"], eval_summary={"n": 20, "skip_rate": r, "presence_min": PRESENCE_MIN, "entail_min": ENTAIL_MIN})` on the real `data/jev_state.json`.
- `ask_smoke.jsonl`: 20 synthetic records `{"id": "S01".."S20", "text": ..., "label": "surveillance"|"budget"|"other"}` (7/7/6). Smoke test runs `jev_ask.main([... "--type", "choice", "--options", <tmp options.json with those three labels>, "--out", tmp ...])`; `ACCURACY_FLOOR = 0.80` (16/20) over answered records; at least 18 answered.

**Test cases:** the gate and floor above; plus offline checks in `tests/test_jev_prescreen.py`: fixture file parses with `parse_claims` (after dropping `expect`/`category`), has 20 items and the category counts above; a default-options run of pytest deselects `jev_live` items (`"$PY" -m pytest tests/test_jev_live_prescreen.py -q` reports deselected, not run).

**Steps:**
- [ ] Write fixtures, live tests, the offline fixture checks; update `pyproject.toml` and `ci.yml`.
- [ ] Run OFFLINE; expect PASS with the live tests deselected.
- [ ] If `MAGPIE_JEV=1` and `OPENROUTER_API_KEY` are set in the environment, run `"$PY" -m pytest -m jev_live tests/test_jev_live_prescreen.py tests/test_jev_live_ask.py -s -q`; expect PASS. If an unsupported item routes `skip`, raise `PRESENCE_MIN`/`ENTAIL_MIN` (never below 0.85) and re-run; if the key is absent, stop and report that the live run is pending. Never commit `data/`.
- [ ] Commit: `git add pyproject.toml .github/workflows/ci.yml tests/fixtures/jev tests/test_jev_live_prescreen.py tests/test_jev_live_ask.py tests/test_jev_prescreen.py scripts/jev_prescreen.py && git commit -F "$MSG"` (subject `test(jev): live pre-screen eval + jev_ask smoke behind jev_live`; include thresholds and skip rate in the body).

**Blocked by:** Tasks 8, 10.

## Task 15: MANIFEST rewrite

**Files:** Rewrite `MANIFEST.md` (Magpie is owned: full rewrite, not an append).

**Interface:** same three sections (Stack / Structure / Key Relationships); one line per file or grouped mirror; target at most ~110 lines and well under `tests/test_manifest_budget.py` (130 lines, 3000 words, 60 words per line). Add one line each for `jev_client.py`, `jev_guards.py`, `jev_state.py`, `jev_prescreen.py`, `jev_ask.py`, `references/jev-guide.md`, the `data/` untracked state files; update the tests count and the offline `-m` expression (adds `not jev_live`); add one Key Relationship: "Jev is opt-in and fail-toward-today: off unless MAGPIE_JEV=1 + key; guards before send; pre-screen only removes verifier calls; model change -> verify; live eval approves the model." Compress older entries to keep the budget.

**Test cases:** `tests/test_manifest_budget.py` passes; `grep -c "jev" MANIFEST.md` is at least 6.

**Steps:**
- [ ] Rewrite `MANIFEST.md`.
- [ ] Run `"$PY" -m pytest tests/test_manifest_budget.py -q`; expect PASS.
- [ ] Commit: `git add MANIFEST.md && git commit -F "$MSG"` (subject `docs(MANIFEST): rewrite index for the Jev integration`).

**Blocked by:** Tasks 1-14.

## Task 16: Final verification

**Files:** none (fixes go back to the owning task's files).

**Steps:**
- [ ] Run OFFLINE; expect PASS (0 failures; `jev_live` deselected).
- [ ] Run `"$PY" -m pytest -q` (bare, the `mise run test` equivalent); expect PASS with `jev_live` deselected.
- [ ] ASCII check: `"$PY" -c "import pathlib,sys; bad=[p for p in ['scripts/jev_client.py','scripts/jev_guards.py','scripts/jev_state.py','scripts/jev_prescreen.py','scripts/jev_ask.py','skills/dataset-analyze/references/jev-guide.md','skills/investigate/SKILL.md','agents/extraction-verifier.md'] if not pathlib.Path(p).read_bytes().isascii()]; print(bad); sys.exit(1 if bad else 0)"`; expect `[]`.
- [ ] Opt-out check: `grep -rn "urllib.request" scripts/` lists only `jev_client.py` among the new files (evidence.py/yente paths are pre-existing); every Part A/B test with Jev off asserts zero transport calls (re-read Tasks 6, 10).
- [ ] Spec coverage re-read: walk spec sections 1.1-4 against the Task list below; any gap is a bug in this branch, not a follow-up.
- [ ] `git status --short` shows no untracked `data/` files and nothing unstaged.
- [ ] Hand off for a single Fable implementation review round (user decision; replaces the spec-5 Codex pass). After that round only a secret/PII leak, any path routing an unsupported claim to `skip`, or data leaving the machine while opted out blocks; everything else becomes a labeled follow-up issue (`autonomous-safe` or `design-input-needed`).

**Blocked by:** Task 15.

---

## Spec coverage

| Spec | Task(s) |
|---|---|
| 1.1 opt-in (MAGPIE_JEV=1 + key; no call when off) | 1, 6, 10, 16 |
| 1.1 what is sent; never documents/paths/ids | 6 (K-keys, canary), 10 (R-keys, canary), 13 |
| 1.1 secret + PII guards, fail-safe import, guarded -> verify / skipped | 2, 6, 10 |
| 1.1 README privacy + invariants | 13 |
| 1.1 doctor line | 12 |
| 1.2 client, endpoint, header, timeout, retry, error mapping, strict validation | 1 |
| 1.2 windows (~14k), oversize, single split | 3 |
| 1.3 model-change safeguard (Part A verify, Part B meta + warning) | 4, 6, 10 |
| 2.1 input/output/CLI; section 2 dispatch rule | 6, 8, 9 |
| 2.2 questions | 6 |
| 2.3 routing conditions, numeric/date gate, cues, thresholds, reason vocabulary | 5, 6 |
| 2.4 spot-checks + disagreement log + summary | 7, 9 |
| 2.5 audit fields, archive-evidence carry-through, gate label, run summary | 8, 9, 7 |
| 3.1 jev_ask CLI, outputs, meta, sample, Jev-off exit | 10 |
| 3.2 guide + three links | 11 |
| 4 offline tests | 1-13 |
| 4 live eval + smoke | 14 |
| 5 reviews | 16 |

## Decisions made while planning (spec ambiguities)

1. **ASCII label and summary.** investigate SKILL.md is ASCII-pinned, so the gate label uses ` -- ` for the em dash and the run summary uses ` | ` for the middle dot, identically in code and skill text.
2. **No approved model yet.** Part A treats a missing/corrupt `jev_state.json` as `model_changed` (nothing skips until the live eval passes). Part B sets `model_changed: true` only when a recorded model differs, and always writes `approved_model`.
3. **Data minimization.** Claims that already fail a local check (guard, degraded anchor, numeric/date, computed cue) are not sent; their presence/entailment are null. Reasons still follow the spec order among those that apply.
4. **Positional keys.** State keys are `K01...` / `R0001...`; claim ids and record ids never leave the machine.
5. **Span.** The span is the resolved block `.text` (`claim_input_from_record`); a clean citation with a null/blank span is `degraded_anchor`.
6. **Spot-check seed and floor.** Default seed is content-derived from the batch; at least one skip claim is spot-checked whenever any claim skips. A missing verifier verdict counts as a disagreement.
7. **Archive carry-through.** `CitationRecord.prescreen` travels in the local citations log, which archive-evidence hashes as received; `public_anchor` stays exactly 10 keys and publishes `verifier_result: "prescreen-skip"` for skipped claims.
8. **Error mapping.** Every client reason other than `too_large`/`waf_blocked` maps to `jev_error`; `missing_key`/`http_401`/`http_402`/non-WAF `http_403` stop the remaining windows.
9. **Numeric gate.** A claim date may be less specific than the span date; a bare claim number may match only the 4-digit YEAR of a span date, never its month or day; numeric slashed dates read month/day/year; the gate is digit-only, so spelled-out numbers ("fourteen") are not gated (Jev entailment and the verifier cover them; documented in jev-guide); extra computed cues (inflections, "fewer than") only add verifies.
10. **Live tests.** Excluded by `addopts`, skipped when Jev is off, and excluded in CI. The live eval approves a model only if all 20 items were answered and the gate held; thresholds may only rise.
11. **PII screen scope.** Only `DEFAULT_PII_PATTERNS` (no spaCy NER), so person names can be sent; README and guide say so.
12. **Enable flag.** `MAGPIE_JEV` must be exactly `1` (after trimming whitespace).
13. **Part B exit codes.** 0 ok (even with skips), 2 usage/input error, 3 Jev off.
14. **Client-side opt-in check.** `jev_client.ask()` itself refuses with `disabled` when `jev_status` is off, so no caller can send while opted out (defense in depth on top of the Part A/B checks).
15. **Model gate needs a model.** `model_changed` overrides reasons only when at least one window returned a model; a batch where nothing was answered keeps each claim's real failure reason.

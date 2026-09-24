# Jev guide: batched typed questions over records

How and when to use `scripts/jev_ask.py` (Part B), and what the optional citation
pre-screen in `investigate` (Part A) does. Both are off by default. In the commands
below, `$PY` is the project's Python (the Magpie venv interpreter).

## What Jev is

Jev is TypeSafe's decision model, reached through OpenRouter. It answers typed
questions over a shared text state, many at once, for a fraction of a cent per
request:

- `noul`: a yes/no question answered as a probability `p` in [0, 1].
- `choice`: pick one label from a fixed set; returns the label, a probability per
  label, and a confidence.
- `score`: place the text on an ordered list of 2-10 levels; returns a value,
  probabilities and a confidence.

Jev does not generate text, do arithmetic, extract values or reason in multiple
steps. Treat every answer as a fast first-pass judgment, never as a finding.

`jev_ask` and the `investigate` pre-screen share one client
(`scripts/jev_client.py`), the same opt-in switch and the same local guards. The
pre-screen asks two `noul` questions per claim (is the quote in the cited span;
does the span support the claim) and lets a confidently supported claim skip the
per-claim `extraction-verifier`. It never accepts, rejects or edits a claim, and
its skipped claims are labeled "not independently verified" at the human gate.

The pre-screen's number/date check is digit-only. Every digit number and date in
a claim must also appear in the span, and cue words for a derived value force a
verifier run: totals and averages ("total", "per", "rate", "%"), multipliers
("doubled", "twice", "3-fold") and proportions ("half", "majority", "most of",
"nearly all"). Spelled-out numbers ("fourteen cameras") are
NOT gated: only Jev's entailment answer and the `extraction-verifier` judge them.

## Turning it on

Set both environment variables before starting the session:

- `MAGPIE_JEV=1` (exactly `1`; any other value keeps Jev off).
- `OPENROUTER_API_KEY` (the operator's OpenRouter key; Magpie never prints it).

With either one missing, nothing is sent and Magpie behaves exactly as it does
without Jev. `doctor` reports the state in one line: `jev: on`,
`jev: off (MAGPIE_JEV not set)` or `jev: off (no OPENROUTER_API_KEY)`.

The pre-screen skips nothing until the operator runs the live eval, which checks
the current Jev model against synthetic supported and unsupported claims and, on
a pass, records that model in `data/jev_state.json`:

```
"$PY" -m pytest -m jev_live tests/test_jev_live_prescreen.py -s
```

If Jev later reports a different model, the pre-screen routes every claim to the
verifier again until the live eval is re-run and passes. `jev_ask` keeps working
but marks its meta file `model_changed: true` and prints a warning.

## Use it for

Bounded questions asked of many records:

- Triage and relevance screening ("is this email about the camera contract?").
- Fixed-label classification (topic, document type, department).
- Flagging rows for human review.
- A "same entity?" pre-screen on candidate pairs before `entity-crossref`: ask
  whether two name/address snippets describe the same person or organization,
  then send only the likely matches on to cross-referencing.
- Pre-filtering rows before an expensive LLM or manual step.

## Don't use it for

- Arithmetic or aggregates: counts, sums, averages, rates. Use pandas or SQL (the
  `dataset-analyze` pipeline and `stats`).
- Multi-step reasoning, or questions that need facts outside the record.
- Generating text or summaries.
- Extracting values (dates, amounts, names) from text.
- The sole basis for any published finding. A Jev answer routes work; a person
  checks the record before anything is reported.

## Writing questions

- Ask one judgment per question. Split "is it about surveillance and is it
  after 2024?" into two runs, and do the date part in pandas.
- Write contrastive criteria. `--criteria-true` and `--criteria-false` should
  each describe a concrete case, including the near miss that belongs on the
  false side (for example: true = "the record discusses buying, installing or
  operating license-plate readers"; false = "the record mentions cameras only for
  building security, or mentions readers only in a forwarded news link").
- Make `choice` labels mutually exclusive and include a "none/other" option so
  Jev is never forced into a wrong label.
- Keep the question about the record itself. Each record is asked
  `Answer only about record R0001. <question>` with its own state key.

Worked `noul` run over a JSONL export with `id`, `title` and `body` fields:

```
"$PY" scripts/jev_ask.py --input records.jsonl --id-field id \
  --text-fields title,body --type noul \
  --question "Is this record about automated license-plate readers?" \
  --criteria-true "The record discusses buying, installing, operating or auditing license-plate readers." \
  --criteria-false "The record is about other cameras or other topics, or names readers only in passing." \
  --out results.jsonl --sample 20 --seed 0
```

- `choice` needs `--options options.json`: a JSON object of 2-255
  label -> description entries, e.g.
  `{"surveillance": "...", "budget": "...", "other": "none of the above"}`.
- `score` needs `--levels levels.json`: a JSON list of 2-10 level descriptions,
  lowest first.
- The id field must not also be a text field. Input is `.jsonl` or `.csv`; the
  source file is never modified.
- Outputs: `results.jsonl` (one line per input record, in input order:
  `{"id": ..., "answer": {...}}` or `{"id": ..., "skipped": "<reason>"}`),
  `results.meta.json`, and with `--sample N` also `results.sample.jsonl`.
- Exit codes: 0 ok (even when some rows are skipped), 2 usage or input error
  (including a question, criteria, option or level text that trips the guards),
  3 Jev off (nothing written, nothing sent).

## Reading answers

Probabilities are relative confidence, not ground truth: 0.9 means "more clearly
yes than 0.6", not "right nine times in ten". Sort answers into three zones:

- **act**: clear answers in the band the spot-check showed to be reliable. Use
  them to route work (drop off-topic rows, queue likely matches), never as a
  finding.
- **check**: the middle band. A person reads these rows.
- **escalate**: low-confidence answers, `choice` answers spread across labels,
  and every `skipped` row. Send them to a full manual review or a stronger step.

Set the zone thresholds from a spot-check, not from assumed numbers: run with
`--sample N`, read the sampled records yourself, and note where Jev's answers
stop agreeing with you. Re-check after any change to the question, the criteria,
the text fields or the model.

Skipped reasons: `pii` and `secret` (withheld locally, see Privacy), `empty` (all
text fields blank), `too_large` (a record, or a window after one split, too big for
Jev's ~14k-token window budget), `waf_blocked` (OpenRouter's firewall refused the window) and
`jev_error` (any other failure). Skipped rows were never answered; treat them as
unknown, not as "no".

## Privacy

Nothing is sent unless `MAGPIE_JEV=1` and `OPENROUTER_API_KEY` are both set.
When they are, requests go to OpenRouter, which routes them to TypeSafe's Jev.

What is sent:

- `jev_ask`: for each record, only the fields named in `--text-fields` (under
  positional keys such as `R0001`), the field names, and the question, criteria,
  options or levels.
- Pre-screen: for each claim, the claim text, the verbatim quote and the cited
  passage (under positional keys such as `K01`).
- Never whole documents, record ids, claim ids, file names or paths.

Local guards check every item before it is sent. An item with structured PII
(phone numbers, SSNs, emails, MM/DD/YYYY dates and similar patterns from
`pii_sweep`) or secret-shaped text stays on the machine: `jev_ask` reports it as
`skipped: pii` or `skipped: secret`, and the pre-screen sends that claim to the
verifier. If the PII module cannot load, every item counts as PII and nothing
is sent.

The screens are deliberately broad, so expect some benign rows to be withheld:

- `skipped: secret` on harmless text is on purpose. A field such as
  `token_count: 5` (a token count, not a credential) matches the
  `token`/`password`/`secret`-style assignment pattern and is withheld rather than
  risk sending a real key. Long random-looking strings are withheld too.
- `skipped: pii` also catches any 10-digit run (a case number can look like a
  phone number) and MM/DD/YYYY dates.

Personal names are NOT pattern PII. Names, and anything else in the chosen text
fields that no pattern matches, ARE sent when Jev is on. Keep name, address and
date-of-birth columns out of `--text-fields` unless the question truly needs
them.

## Provenance

- Keep `results.meta.json` next to any dataset derived from a `jev_ask` run. It
  records the question, type, criteria/options/levels, id and text fields, the
  model that answered, the approved model, `model_changed`, a timestamp, counts
  per outcome, the input file's name and sha256, token usage and the sample
  settings.
- Cite the meta file in methodology notes whenever Jev answers shaped what was
  reviewed or dropped, and say which zone thresholds were used and how they were
  set.
- A meta file with `model_changed: true` means the answering model differs from
  the one the live eval approved. Re-check a fresh `--sample` before relying on
  those answers.
- Keep `results.sample.jsonl` with the spot-check notes that set the thresholds.

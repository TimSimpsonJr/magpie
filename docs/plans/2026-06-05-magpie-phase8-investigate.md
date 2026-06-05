---
title: Magpie Phase 8 -- investigate (verification gate + citation anchor) (plan)
date: 2026-06-05
phase: 8
plan_review: APPROVE (Codex, round 3, 2026-06-05)
codex_plan_review_status: approved
codex_plan_review_approved_hash: 2ab89d7e3e61bdf5073aa63e07028c24ab46d4607da5348fa104abb18424a0e7
codex_thread_id: 019e95ea-d5d9-72f0-87db-e1bbd50a4c42
---

# Phase 8 -- investigate (verification gate + citation anchor) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> superpowers:subagent-driven-development) to implement this plan task-by-task.

**Goal:** Build the `investigate` verification gate -- a pure citation-anchor
engine (`scripts/citation.py`), two structurally-separate verifier agents, and the
orchestration skill -- that turns a trustworthy ingested document into
human-gated, anchored, redacted findings.

**Architecture:** `scripts/citation.py` is PURE (stdlib `hashlib`/`json`/
`dataclasses` only; NO docling/spaCy/pandas import; deterministic, timestamp
injected) and resolves over a plain `json.load`ed DoclingDocument dict. It mirrors
`ingest_gate`'s pure core. Two agent prompts (`agents/extraction-verifier.md`
SEMANTIC advisory re-check; `agents/citation-checker.md` MECHANICAL anchor
integrity) plus `skills/investigate/SKILL.md` orchestrate extract -> verify ->
mandatory solo human gate -> redacted Librarian output. Source of truth (read
first): `docs/plans/2026-06-05-magpie-phase8-investigate-design.md`.

**Tech Stack:** Python 3.12 stdlib (`hashlib`, `json`, `dataclasses`, `re`);
pytest 9.0.3. Tests run via `mise run test` (NEVER bare `python`; fallback
`& .venv\Scripts\python.exe -m pytest`). The Tier-2 tests reuse the Phase-6
`@pytest.mark.docling` machinery; everything else is offline + ASCII-only.

**Conventions (match the existing modules):**
- ASCII-only in every source + test + fixture (SDD subagents content-filter-block
  on non-ASCII).
- Pure-core / engine-at-the-edge, like `ingest_gate` / `pii_sweep`.
- Native-typed, JSON-able dataclass outputs; `to_dict()` round-trips.
- Half-open `[start, end)` offsets everywhere (Python slice semantics).
- Commit after each green step (`git commit -F` for multi-line bodies; single
  `-m` is fine).

---

## Shared test fixture helper (used by every Tier-1 test)

**Files:**
- Create: `tests/conftest_citation.py` (star-imported into `tests/conftest.py`,
  mirroring how `conftest_redaction.py` is wired)

A tiny builder for DoclingDocument-shaped dicts in the EXACT serialized shape
`ingest` writes (verified against docling-core 2.78.1). No docling import.

```python
# tests/conftest_citation.py -- ASCII only. Synthetic DoclingDocument dicts.
def make_block(index, text, page_no=1, *, bbox=None, charspan=None, prov=None):
    """One texts[] item. self_ref == '#/texts/{index}'. Single-prov by default
    (charspan defaults to [0, len(text)) -- but pass charspan/prov to model the
    real-world cases where prov.charspan != [0,len) or n_prov > 1)."""
    if bbox is None:
        bbox = {"l": 72.0, "t": 700.0, "r": 540.0, "b": 688.0,
                "coord_origin": "BOTTOMLEFT"}
    if prov is None:
        cs = charspan if charspan is not None else [0, len(text)]
        prov = [{"page_no": page_no, "bbox": bbox, "charspan": cs}]
    return {"self_ref": f"#/texts/{index}", "parent": {"$ref": "#/body"},
            "children": [], "label": "text", "prov": prov,
            "orig": text, "text": text}

def make_doc(blocks, *, pages=None, schema_version="1.10.0"):
    """A minimal DoclingDocument dict: top-level texts[] + pages + schema."""
    if pages is None:
        pages = {"1": {"size": {"width": 612.0, "height": 792.0}, "page_no": 1}}
    return {"schema_name": "DoclingDocument", "version": schema_version,
            "texts": list(blocks), "tables": [], "pictures": [], "groups": [],
            "body": {"self_ref": "#/body", "children": [], "label": "unspecified"},
            "form_items": [], "key_value_items": [], "furniture": {}, "pages": pages,
            "name": "synthetic"}
```

No test asserts on this helper directly; it is the fixture substrate. Commit it
with Task 1's first test.

---

## Task 1: `citation.py` -- record, identity, public/raw split (TDD)

**Files:**
- Create: `scripts/citation.py`
- Test: `tests/test_citation.py`

**Step 1: Write failing tests** for the data layer.

```python
# tests/test_citation.py -- ASCII only
from scripts.citation import (
    CitationRecord, sha256_text, block_index_of, SCHEMA_NAME, SCHEMA_VERSION,
)

def _record(**kw):
    base = dict(
        claim_text="Officer Ramirez ran 482 searches.",
        verbatim_quote="482 searches",
        context_prefix="Ramirez ran ", context_suffix=" in March",
        doc_id="abc123", doc_schema_name="DoclingDocument", doc_schema_version="1.10.0",
        page_no=1, block_index=0, block_self_ref="#/texts/0",
        char_start=12, char_end=24, text_hash=sha256_text("482 searches"),
        bbox={"l": 1.0, "t": 2.0, "r": 3.0, "b": 4.0, "coord_origin": "BOTTOMLEFT"},
        n_prov=1, verifier_result="indeterminate", verifier_confidence=None,
        checker_level="exact", extractor_model="claude-opus-4-8", prompt_version="v1",
        timestamp="2026-06-05T00:00:00Z",
    )
    base.update(kw)
    return CitationRecord(**base)

def test_sha256_text_is_full_untruncated_no_strip():
    # full 64-hex; differs from pii_sweep.text_id (stripped + [:16])
    h = sha256_text(" 482 ")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
    assert sha256_text(" 482 ") != sha256_text("482")  # NOT stripped

def test_block_index_of_parses_self_ref():
    assert block_index_of("#/texts/12") == 12

def test_to_dict_is_json_able_and_round_trips():
    import json
    d = _record().to_dict()
    assert json.loads(json.dumps(d))["text_hash"] == sha256_text("482 searches")
    assert d["schema_name"] == SCHEMA_NAME and d["schema_version"] == SCHEMA_VERSION

def test_public_anchor_is_exactly_the_approved_minimal_surface():
    pub = _record().public_anchor()
    # raw fields dropped
    for raw in ("claim_text", "verbatim_quote", "context_prefix", "context_suffix"):
        assert raw not in pub
    # EXACTLY the approved design's public set (design section 3) -- set-equality so
    # the test catches BOTH a dropped key AND any silent widening (e.g. char_start,
    # n_prov, timestamp must NOT leak onto the published surface).
    assert set(pub) == {
        "doc_id", "page_no", "block_index", "block_self_ref", "text_hash", "bbox",
        "checker_level", "verifier_result", "schema_name", "schema_version",
    }
```

**Step 2:** `mise run test -- tests/test_citation.py -v` -> FAIL (ImportError).

**Step 3: Implement** `CitationRecord` (dataclass with the design 2.1 fields, in
that order), `SCHEMA_NAME = "magpie-citation"`, `SCHEMA_VERSION = "1"`,
`sha256_text(s) -> hashlib.sha256(s.encode("utf-8")).hexdigest()` (no strip),
`block_index_of(self_ref) -> int(self_ref.rsplit("/", 1)[1])`, `to_dict()` via
`dataclasses.asdict`, and `public_anchor()` returning EXACTLY the 10-key approved
set (design section 3) via an explicit allowlist literal -- never a denylist, so a
future raw field is absent by default, and `char_start`/`char_end`/`n_prov`/
`timestamp` stay in `to_dict()` but NOT on the published surface.

**Step 4:** `mise run test -- tests/test_citation.py -v` -> PASS.

**Step 5: Commit** `feat(citation): CitationRecord + public/raw split + identity helpers`.

---

## Task 2: `citation.py` -- `build_anchor` + the v1 quote contract (TDD)

**Files:**
- Modify: `scripts/citation.py`
- Test: `tests/test_citation.py`

**Step 1: Write failing tests.** The quote contract (design 2.4) is the spec.

```python
import pytest
from scripts.citation import build_anchor, QuoteContractError
from tests.conftest_citation import make_block  # or use the fixture directly

def _kw(**kw):
    base = dict(claim_text="c", doc_id="d", doc_schema_name="DoclingDocument",
                doc_schema_version="1.10.0", extractor_model="m", prompt_version="v1",
                timestamp="t")
    base.update(kw); return base

def test_build_anchor_happy_path_single_prov():
    blk = make_block(3, "Officer Ramirez ran 482 searches in March 2026.", page_no=2)
    rec = build_anchor(blk, verbatim_quote="482 searches", **_kw())
    assert rec.block_index == 3 and rec.block_self_ref == "#/texts/3"
    assert rec.page_no == 2 and rec.n_prov == 1
    txt = "Officer Ramirez ran 482 searches in March 2026."
    assert txt[rec.char_start:rec.char_end] == "482 searches"   # half-open
    assert rec.context_prefix.endswith("ran ") and rec.context_suffix.startswith(" in")
    assert rec.bbox == blk["prov"][0]["bbox"]

def test_build_anchor_offsets_independent_of_docling_charspan():
    # 6/189 real blocks had prov.charspan != [0,len). build_anchor must NOT use it.
    blk = make_block(0, "alpha 482 searches beta", charspan=[100, 123])
    rec = build_anchor(blk, verbatim_quote="482 searches", **_kw())
    assert "alpha 482 searches beta"[rec.char_start:rec.char_end] == "482 searches"

def test_rejects_empty_or_whitespace_quote():
    blk = make_block(0, "some text here")
    for bad in ("", "   ", "\t"):
        with pytest.raises(QuoteContractError):
            build_anchor(blk, verbatim_quote=bad, **_kw())

def test_rejects_quote_not_in_block():
    blk = make_block(0, "some text here")
    with pytest.raises(QuoteContractError):
        build_anchor(blk, verbatim_quote="absent", **_kw())

def test_rejects_multi_prov_block():
    blk = make_block(0, "spans two pages", prov=[
        {"page_no": 1, "bbox": {}, "charspan": [0, 8]},
        {"page_no": 2, "bbox": {}, "charspan": [8, 15]}])
    with pytest.raises(QuoteContractError):
        build_anchor(blk, verbatim_quote="two", **_kw())

def test_rejects_mid_token_subspan_not_word_boundary():
    # "ice" inside "police" must be rejected (the ICE/polICE trap at anchor level)
    blk = make_block(0, "the police arrived")
    with pytest.raises(QuoteContractError):
        build_anchor(blk, verbatim_quote="ice", **_kw())

def test_rejects_quote_occurring_twice_in_block():
    blk = make_block(0, "482 searches and 482 searches")
    with pytest.raises(QuoteContractError):
        build_anchor(blk, verbatim_quote="482 searches", **_kw())
```

**Step 2:** run -> FAIL.

**Step 3: Implement** `build_anchor(block, *, verbatim_quote, claim_text, doc_id,
doc_schema_name, doc_schema_version, extractor_model="", prompt_version="",
timestamp="", context_window=CONTEXT_WINDOW=32) -> CitationRecord`. Algorithm:
1. `if not verbatim_quote.strip(): raise QuoteContractError("empty/blank quote")`.
2. `prov = block.get("prov") or []; if len(prov) != 1: raise QuoteContractError("block is not single-prov")`.
3. `text = block["text"]`; find ALL start indices of `verbatim_quote` in `text`.
   `if len(starts) == 0: raise QuoteContractError("quote not in block")`;
   `if len(starts) > 1: raise QuoteContractError("quote not unique in block")`.
4. `start = starts[0]; end = start + len(verbatim_quote)`. Word-boundary check
   (`_word_boundary_aligned(text, start, end)` -- left ok if `start == 0` or not
   (`text[start-1].isalnum()` and `verbatim_quote[0].isalnum()`); symmetric on the
   right) else `raise QuoteContractError("not word-boundary aligned")`.
5. `context_prefix = text[max(0, start-context_window):start]`,
   `context_suffix = text[end:end+context_window]`.
6. `text_hash = sha256_text(verbatim_quote)`; `bbox = prov[0]["bbox"]`,
   `page_no = prov[0]["page_no"]`, `n_prov = 1`,
   `block_self_ref = block["self_ref"]`, `block_index = block_index_of(...)`.
7. Return a `CitationRecord` with `checker_level=""`,
   `verifier_result="indeterminate"`.

**Step 4:** run -> PASS. **Step 5: Commit** `feat(citation): build_anchor + v1 quote contract`.

---

## Task 3: `citation.py` -- `resolve_anchor` fallback chain + clean-gate (TDD)

**Files:**
- Modify: `scripts/citation.py`
- Test: `tests/test_citation.py`

**Step 1: Write failing tests** -- one per level, plus the REQUIRED round-trip.

```python
from scripts.citation import resolve_anchor, is_clean_citation
from tests.conftest_citation import make_block, make_doc

def _anchor_in(doc, block, quote):
    return build_anchor(block, verbatim_quote=quote, **_kw())

def test_exact_level_when_offsets_intact():
    blk = make_block(0, "Officer Ramirez ran 482 searches in March.")
    rec = _anchor_in(None, blk, "482 searches")
    r = resolve_anchor(rec, make_doc([blk]))
    assert r.level == "exact" and r.matched_text == "482 searches"
    assert is_clean_citation(r) is True

def test_exact_disambiguates_duplicate_via_offsets():
    # same quote twice in the DOC (different blocks); stored offsets+block_index pick one
    b0 = make_block(0, "alpha 482 searches alpha")
    rec = _anchor_in(None, b0, "482 searches")
    b1 = make_block(1, "beta 482 searches beta")
    r = resolve_anchor(rec, make_doc([b0, b1]))
    assert r.level == "exact" and r.block_index == 0

def test_relocated_when_offsets_shift_but_quote_unique():
    blk = make_block(0, "Officer Ramirez ran 482 searches in March.")
    rec = _anchor_in(None, blk, "482 searches")
    # OCR re-run: PREPEND a header to the block (shifts offsets) + insert an earlier block
    shifted = make_block(1, "PAGE 1 HEADER. Officer Ramirez ran 482 searches in March.")
    doc2 = make_doc([make_block(0, "INSERTED EARLIER BLOCK"), shifted])
    r = resolve_anchor(rec, doc2)
    assert r.level == "relocated" and r.matched_text == "482 searches"
    assert is_clean_citation(r) is True

def test_ambiguous_when_quote_repeats_and_context_cannot_disambiguate():
    blk = make_block(0, "x 482 searches y")
    rec = _anchor_in(None, blk, "482 searches")
    # two identical-context occurrences after an offset shift
    doc2 = make_doc([make_block(0, "HDR x 482 searches y ... x 482 searches y")])
    r = resolve_anchor(rec, doc2)
    assert r.level == "ambiguous" and r.n_matches >= 2
    assert is_clean_citation(r) is False

def test_block_level_when_characters_changed_but_block_valid():
    blk = make_block(0, "Officer Ramirez ran 482 searches in March.", page_no=2)
    rec = _anchor_in(None, blk, "482 searches")
    # OCR mangled the chars (rn->m etc.); the quote no longer appears, block still on page 2
    doc2 = make_doc([make_block(0, "Officer Rarnirez ran 4B2 searches in March.", page_no=2)],
                    pages={"2": {"size": {"width": 1.0, "height": 1.0}, "page_no": 2}})
    r = resolve_anchor(rec, doc2)
    assert r.level == "block" and is_clean_citation(r) is False

def test_page_level_when_block_index_gone_but_page_exists():
    blk = make_block(5, "482 searches", page_no=3)
    rec = _anchor_in(None, blk, "482 searches")
    doc2 = make_doc([make_block(0, "unrelated mangled text", page_no=3)],
                    pages={"3": {"size": {"width": 1.0, "height": 1.0}, "page_no": 3}})
    r = resolve_anchor(rec, doc2)
    assert r.level == "page" and is_clean_citation(r) is False

def test_unresolved_when_nothing_matches():
    blk = make_block(9, "482 searches", page_no=8)
    rec = _anchor_in(None, blk, "482 searches")
    r = resolve_anchor(rec, make_doc([make_block(0, "x", page_no=1)]))
    assert r.level == "unresolved" and is_clean_citation(r) is False

def test_relocated_rejects_interior_substring_via_word_boundary():
    # quote "ice" stored; OCR doc has it only inside "police" -> must NOT relocate
    blk = make_block(0, "the ice cream truck")  # valid build (word-boundary "ice")
    rec = _anchor_in(None, blk, "ice")
    doc2 = make_doc([make_block(0, "the police came")])  # "ice" only inside "police"
    r = resolve_anchor(rec, doc2)
    assert r.level in ("block", "page", "unresolved") and r.level != "relocated"

def test_relocated_uses_context_to_break_a_tie():
    # quote unique in the BUILD block but appears TWICE in the target (offsets
    # shifted). Only one occurrence carries the stored context -> unique relocated.
    # A context-IGNORING resolver would see two matches and wrongly return ambiguous.
    blk = make_block(0, "alpha 482 searches beta")
    rec = _anchor_in(None, blk, "482 searches")
    doc2 = make_doc([
        make_block(0, "HEADER LINE"),
        make_block(1, "gamma 482 searches delta"),   # wrong context
        make_block(2, "alpha 482 searches beta"),     # the stored context
    ])
    r = resolve_anchor(rec, doc2)
    assert r.level == "relocated" and r.block_index == 2 and is_clean_citation(r)

def test_multi_prov_target_block_degrades_geometry_not_exact():
    # build on a single-prov block; the SAME block_index in the target is now
    # multi-prov (a re-ingest split it across pages). Text still matches at the
    # stored offsets, but faithful geometry is impossible -> degrade to block.
    blk = make_block(0, "Officer Ramirez ran 482 searches in March.")
    rec = _anchor_in(None, blk, "482 searches")
    bb = {"l": 1.0, "t": 2.0, "r": 3.0, "b": 4.0, "coord_origin": "BOTTOMLEFT"}
    split = make_block(0, "Officer Ramirez ran 482 searches in March.", prov=[
        {"page_no": 1, "bbox": bb, "charspan": [0, 25]},
        {"page_no": 2, "bbox": bb, "charspan": [25, 43]}])
    r = resolve_anchor(rec, make_doc([split]))
    assert r.level == "block" and r.bbox is None and r.page_no is None
    assert is_clean_citation(r) is False

def test_relocated_rejects_single_candidate_with_wrong_context():
    # exact fails (offsets shifted); the target has exactly ONE boundary-aligned
    # occurrence of the quote, but its context does NOT match the stored
    # prefix/suffix -> must NOT relocate. Pins that context is checked even for a
    # LONE candidate (a resolver that only checks context to break ties would
    # wrongly relocate here).
    blk = make_block(0, "alpha 482 searches beta")
    rec = _anchor_in(None, blk, "482 searches")
    doc2 = make_doc([make_block(0, "HEADER gamma 482 searches delta")])  # wrong context, shifted
    r = resolve_anchor(rec, doc2)
    assert r.level != "relocated" and r.level in ("block", "page", "unresolved")
    assert is_clean_citation(r) is False

def test_relocated_into_multi_prov_target_degrades_to_block():
    # exact fails (block_index shifted); the UNIQUE relocated candidate (text +
    # context match) lives in a MULTI-prov target block -> degrade to block with
    # page_no/bbox None. Pins the single-prov guard on the RELOCATED branch too
    # (not just the exact branch).
    blk = make_block(0, "alpha 482 searches beta")
    rec = _anchor_in(None, blk, "482 searches")
    bb = {"l": 1.0, "t": 2.0, "r": 3.0, "b": 4.0, "coord_origin": "BOTTOMLEFT"}
    target = make_block(1, "alpha 482 searches beta", prov=[
        {"page_no": 1, "bbox": bb, "charspan": [0, 11]},
        {"page_no": 2, "bbox": bb, "charspan": [11, 23]}])
    doc2 = make_doc([make_block(0, "INSERTED EARLIER"), target])
    r = resolve_anchor(rec, doc2)
    assert r.level == "block" and r.page_no is None and r.bbox is None
    assert is_clean_citation(r) is False
```

**Step 2:** run -> FAIL.

**Step 3: Implement** `resolve_anchor(record, docling_json) -> ResolvedAnchor` and
`is_clean_citation(resolved) -> bool`. **Single-prov geometry guard at resolve
(design 2.3):** a matched TARGET block yields faithful geometry ONLY when it is
single-prov; helper `_geom(blk) = (prov[0]["page_no"], prov[0]["bbox"])` if
`len(blk.get("prov", [])) == 1` else `(None, None)`, and a multi-prov matched
block also DEGRADES the level to `block` (never a faux-precise `prov[0]`).
Algorithm (ordered; STOP at first hit):
- `texts = docling_json.get("texts", [])`.
- **exact:** if `0 <= record.block_index < len(texts)` and
  `texts[record.block_index]["text"][record.char_start:record.char_end]` hashes
  (`sha256_text`) to `record.text_hash`: let `blk = texts[record.block_index]`. If
  `blk` is single-prov -> `level="exact"` (block_index + offsets + `_geom(blk)` +
  `matched_text`=slice). If `blk` is MULTI-prov -> `level="block"` (block_index
  only; offsets/page/bbox None).
- **relocated/ambiguous:** else collect candidates across ALL blocks: every
  word-boundary-aligned occurrence of `record.verbatim_quote` in a block's `.text`
  whose preceding chars END WITH `record.context_prefix` AND following chars BEGIN
  WITH `record.context_suffix`, confirmed by `sha256_text`. If exactly ONE
  candidate: if its block is single-prov -> `level="relocated"` (new block_index +
  offsets + `_geom`, `n_matches=1`); if MULTI-prov -> `level="block"` (block_index
  only; geometry None). If >1 candidate -> `level="ambiguous"` (`n_matches=len`;
  offsets/page/bbox None).
- **block:** else if `0 <= record.block_index < len(texts)` and that block is
  single-prov with `prov[0]["page_no"] == record.page_no` -> `level="block"`
  (block_index + `_geom`; offsets None). (A multi-prov stored block does not
  satisfy this and falls to `page`/`unresolved`.)
- **page:** else if `record.page_no` is a key in `docling_json["pages"]` (compare
  as BOTH str and int) -> `level="page"` (page_no only).
- **unresolved:** else `level="unresolved"` (all None).
- `is_clean_citation(r)` -> `r.level in ("exact", "relocated")` (only the
  single-prov, text-matched, UNIQUE cases reach those levels).

NOTE the context match is what makes `relocated` safe: store the prefix/suffix at
build time, require them at relocation. Word-boundary + context together defeat
the interior-substring + duplicate traps; the single-prov guard defeats
faux-precise geometry on a re-split target block.

**Step 4:** run -> PASS (all levels + round-trip green).

**Step 5: Commit** `feat(citation): resolve_anchor fallback chain + clean-citation gate`.

---

## Task 4: verifier + citation-checker agents (plugin-dev:agent-development)

**Files:**
- Create: `agents/extraction-verifier.md`
- Create: `agents/citation-checker.md`
- Test: `tests/test_investigate_agents.py` (frontmatter smoke, mirrors
  `test_*_skill.py`)

Author BOTH via the `plugin-dev:agent-development` sub-skill (correct frontmatter:
`name`, `description` with trigger phrasing, `tools`, optional `model`).

**`extraction-verifier.md` (SEMANTIC, advisory):** Input contract = a single claim
+ the cited span text (re-read from `.text`), blinded to the extractor's
reasoning. Runs presence (is the quote in the span?) + entailment (does the span
support the claim?). Output JSON: `{result: supported|contradicted|indeterminate,
confidence: 0..1, reasoning}`. `indeterminate` is the conservative default
(presence OR entailment in doubt -> indeterminate). The prompt MUST state the
honest limit: this is an advisory adversarial re-check, NOT an independent verifier
(same model -> correlated errors, design section 4/8); it NEVER auto-accepts -- the
human gate is the only real verifier. Its reasoning is LOCAL-only (never published).

**`citation-checker.md` (MECHANICAL):** drives `scripts/citation.py`
`resolve_anchor` + `is_clean_citation` over every claim and reports per-claim
`{anchor_level, ok, reason}`. Flags: uncited claims; anchors resolving only at
`ambiguous`/`block`/`page`/`unresolved` (degraded -- NOT a pass); `matched_text !=
verbatim_quote`. Deterministic; no semantic judgment.

**Smoke test** (PyYAML, like `test_ingest_skill.py`): each agent file's frontmatter
parses, has `name`/`description`; `extraction-verifier` body documents
`indeterminate`-default + the advisory/honest-limit + blinded-to-extractor;
`citation-checker` body documents `resolve_anchor`/`is_clean_citation` +
degraded-is-not-a-pass.

**Steps:** write smoke test -> FAIL -> author the two agents -> PASS -> commit
`feat(investigate): extraction-verifier + citation-checker agents`.

---

## Task 5: `investigate` SKILL.md + prior-art + smoke test

**Files:**
- Create: `skills/investigate/SKILL.md`
- Create: `skills/investigate/references/prior-art.md`
- Test: `tests/test_investigate_skill.py`

**`SKILL.md`** (match the `ingest`/`redaction-check` house style: trigger-phrase
third-person `description`, `version`, a "call one engine" overview, contract
sections, rigor guardrails, downstream/resources). It documents the design's
orchestration:
0. **Refuse non-trustworthy:** check the ingest result's
   `trustworthy_for_extraction` BOOLEAN (false for `review` OR `PARTIAL_SUCCESS`,
   `scripts/ingest.py:721`); NEVER key on `doc_decision == review`. No override.
1. **Extract** (LLM, schema-constrained): emit
   `{claim_text, verbatim_quote, block_self_ref}` with each `verbatim_quote` a
   single-prov-block, word-boundary, exact `.text` substring; `build_anchor`
   stamps each.
2. **Verify** independently: `citation-checker` (mechanical) +
   `extraction-verifier` (advisory), both blinded.
3. **Solo human gate -- evidence BEFORE claim:** source span first, then the AI
   claim, then the advisory verdict + checker level; degraded anchors /
   contradicted / indeterminate surfaced prominently. Accept/edit/reject; an EDIT
   re-stamps + re-verifies (no stale verdict). Solo sign-off is the ONLY required
   gate; two-reviewer optional/logged, never required.
4. **Output (redacted):** run `pii_sweep` over the cited source spans to collect
   `local_texts`, then `redact_note(claim_text, local_texts, keep_names=...,
   officials=...)`; publish the redacted claim + `public_anchor` + status via
   Librarian; raw record + verifier/checker reasoning stay LOCAL.
- **Rigor guardrails** (design 6): walk back unsupported; verify keyword matches
  via `derive.keyword_mask` (ICE/polICE); `***` != blank; refuse out-of-scope;
  window-asked != retention-proven.
- **Honest limits:** advisory verifier; human gate mandatory; degraded anchors
  never auto-pass.

**`references/prior-art.md`** (LIGHT -- the format is mostly a magpie invention
over Phase-6-verified facts): record the verified docling-core 2.78.1 serialized
shape (`texts[].{self_ref, text, prov[].{page_no,bbox,charspan}}`), the TextQuote-
Selector prefix/exact/suffix prior art for the relocation context, and the early
Greenville-validation finding (`prov.charspan` not a reliable `.text` offset ->
own-offsets + single-prov requirement). No new library deps.

**Smoke test** (PyYAML, mirrors `test_ingest_skill.py`): frontmatter
`name == investigate` + `description` (triggers + names verification/citation) +
`version`; body documents the refuse-on-`trustworthy_for_extraction` seam, the
extract/verify/human-gate flow, evidence-before-claim, the `redact_note` publish
edge, the rigor guardrails, and the engine module (`scripts/citation.py`). No
`.mcp.json` ships.

**Steps:** write smoke test -> FAIL -> author SKILL.md + prior-art -> PASS ->
commit `feat(investigate): orchestration skill + prior-art`.

---

## Task 6: real-pipeline validation tests (Tier 2 + Tier 2b)

**Files:**
- Modify: `tests/conftest.py` (add a paired native/scan fixture built from ONE
  shared text, reusing the existing Phase-6 fpdf2 / Pillow builders)
- Test: `tests/test_citation_docling.py` (`@pytest.mark.docling`)

**Tier 2 (generated source, docling-marked) -- a DEDICATED paired fixture.** The
Phase-6 `native_pdf` and `scan_pdf` fixtures are DIFFERENT documents, so they
cannot validate native-vs-OCR of the SAME text. Add a `paired_native_scan` fixture
that renders ONE shared ASCII text BOTH ways (a native text-layer PDF via fpdf2
AND an image-only PDF via Pillow), reusing the same builder helpers the Phase-6
`native_pdf` / `scan_pdf` fixtures use (factor them to module-level helpers if
inline). The Tier-2 test ingests BOTH, builds anchors on the NATIVE-ingest JSON
over distinctive tokens, resolves them against the SCAN's OCR-ingest JSON, and
asserts: clean tokens resolve `exact`/`relocated` (survive OCR), no result is a
FALSE `exact` (a slice that does not equal the quote), and mismatches degrade to
`block`/`page`. Select with `-k docling`; excluded from the offline suite
(`-k "not docling and not spacy and not xray"`).

**Tier 2b (real-world PDF, env-var-gated)** -- two checks on the real Greenville
RFP: (a) same-doc exactness over real-world fragmentation, and (b) the design's
"across a re-ingest pass" drift, modeled by re-ingesting a SHIFTED page range so
real block indices move. Skip unless `MAGPIE_PHASE8_REAL_PDF` is set + exists.

```python
import os, json, pytest
pytestmark = pytest.mark.docling
REAL = os.environ.get("MAGPIE_PHASE8_REAL_PDF")
_skip = pytest.mark.skipif(not (REAL and os.path.exists(REAL)),
                           reason="set MAGPIE_PHASE8_REAL_PDF to the local Greenville RFP")

def _anchors_over(doc, res, *, limit=20):
    from scripts.citation import build_anchor
    out = []
    for blk in doc["texts"]:
        if len(blk.get("prov", [])) != 1:
            continue
        words = [w for w in blk["text"].split() if len(w) >= 6 and blk["text"].count(w) == 1]
        if not words:
            continue
        out.append(build_anchor(blk, verbatim_quote=words[0], claim_text="c",
                                doc_id=res.source_sha256, doc_schema_name=res.schema_name,
                                doc_schema_version=res.schema_version, extractor_model="m",
                                prompt_version="v1", timestamp="t"))
        if len(out) >= limit:
            break
    return out

@_skip
def test_real_world_same_doc_exact(tmp_path):
    from scripts.ingest import ingest
    from scripts.citation import resolve_anchor, is_clean_citation
    res = ingest(REAL, out_dir=str(tmp_path), page_range=(1, 12))
    assert res.trustworthy_for_extraction  # native + trustworthy (validated 2026-06-05)
    doc = json.load(open(res.docling_json_path, encoding="utf-8"))
    anchors = _anchors_over(doc, res)
    assert len(anchors) >= 5
    for rec in anchors:
        r = resolve_anchor(rec, doc)
        assert r.level == "exact" and is_clean_citation(r)

@_skip
def test_real_world_reingest_drift(tmp_path):
    # Build anchors on pages 1-12; resolve against an ingest of pages 2-12. Page-1
    # blocks vanish and every remaining block_index shifts, so EXACT (keyed on the
    # stored block_index) fails and the fallback chain must take over: shared blocks
    # relocate by unique text+context; dropped-page blocks degrade. No item may
    # return a FALSE exact, and a meaningful share must still resolve clean.
    from scripts.ingest import ingest
    from scripts.citation import resolve_anchor, is_clean_citation
    res_a = ingest(REAL, out_dir=str(tmp_path / "a"), page_range=(1, 12))
    res_b = ingest(REAL, out_dir=str(tmp_path / "b"), page_range=(2, 12))
    doc_a = json.load(open(res_a.docling_json_path, encoding="utf-8"))
    doc_b = json.load(open(res_b.docling_json_path, encoding="utf-8"))
    anchors = _anchors_over(doc_a, res_a)
    assert len(anchors) >= 5
    clean = 0
    for rec in anchors:
        r = resolve_anchor(rec, doc_b)
        if r.level == "exact":  # a surviving exact must be a TRUE slice match
            assert doc_b["texts"][r.block_index]["text"][r.char_start:r.char_end] == rec.verbatim_quote
        if is_clean_citation(r):
            clean += 1
    assert clean >= 1  # some shared-page anchors survive real re-ingest block-index drift
```

Run both once during implementation with `MAGPIE_PHASE8_REAL_PDF` set to
`C:\Users\tim\Downloads\Responsive records (1).pdf`; record the results in the PR.
The PDF is NEVER committed; both tests SKIP in CI. (If `clean >= 1` proves flaky on
the real layout, prefer doc-wide-unique tokens in `_anchors_over` -- tune during
the run-once, do not weaken the no-false-exact assertion.)

**Steps:** add the paired fixture -> write the tests -> run Tier 2 (`-k docling`)
green -> run Tier 2b with the env var (record results) -> commit
`test(citation): real-pipeline Tier-2 paired + Tier-2b real-world re-ingest`.

---

## Final integration + verification

1. `mise run test` -- the FULL suite green (offline + docling/spacy/xray-marked).
   Record the count (was 420; expect ~+40 citation tests + smokes).
2. `mise run test -- -k "not docling and not spacy and not xray"` -- the offline
   subset green in a few seconds.
3. Confirm `import scripts.citation` pulls in NO docling/spaCy/pandas (an
   import-purity assertion, mirroring `pii_sweep`/`ingest`):
   `python -c "import sys, scripts.citation; assert 'docling' not in sys.modules and 'spacy' not in sys.modules"`.
4. Regenerate `MANIFEST.md` (new `scripts/citation.py`, `agents/*.md`,
   `skills/investigate/*`, `tests/test_citation*.py`, `tests/conftest_citation.py`).
5. Open the PR; merge with a merge commit.

## Out of scope (v1 -- per the approved design)

No multi-document corpus index; no two-reviewer workflow infra; no claim-extraction
ML model; no fuzzy/edit-distance resolution tier; cross-block + multi-prov-block
quotes rejected. The autonomous-safe Task-11.2 real-Simpsonville anchor validation
is a filed follow-up.

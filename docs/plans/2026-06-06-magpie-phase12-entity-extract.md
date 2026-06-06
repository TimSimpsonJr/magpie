---
phase: 12
title: entity-extract implementation plan
codex_thread_id: 019e95ea-d5d9-72f0-87db-e1bbd50a4c42
status: plan
date: 2026-06-06
design: docs/plans/2026-06-05-magpie-phase12-entity-extract-design.md
plan_review: codex round 1 (5 findings folded: windows-bootstrap-drift, premature-node-merge, edit-path, graph-closure, transformers-coexistence)
---

# Phase 12 entity-extract Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development to
> execute this plan task-by-task on the `feat/phase12-entity-extract` branch.

**Goal:** Build the Track-B `entity-extract` skill -- GLiNER entities + GLiREL
relations over an ingested document, emitted as a REVIEWED INTERMEDIATE with
per-statement provenance after a mandatory human gate; a separate Linux/CI `ftmize`
layer turns that intermediate into a FollowTheMoney bundle (the Phase-13 hand-off).

**Architecture (decoupled -- see design Addendum section 15):**
- WINDOWS-NATIVE PURE CORE: `scripts/entity_taxonomy.py` + `scripts/entity_extract.py`
  -- stdlib only (NO followthemoney, NO models), golden-testable with a FAKE extractor.
  Produces a followthemoney-FREE reviewed intermediate (plain JSON, deterministic
  hashlib IDs).
- LAZY MODEL EDGE: `scripts/entity_models.py` -- gliner/glirel/spaCy imported only
  inside the lazy loader; injectable; `gliner`-marked integration tests.
- FtM LAYER (Linux/CI only): `scripts/entity_ftmize.py` -- intermediate ->
  followthemoney bundle + the export/resolution contract tests; `ftm`-marked, SKIPS
  on Windows (followthemoney does not install there: PyICU/ICU). Reused by Phase 13.

**Tech Stack:** gliner 0.2.26 (Apache-2.0), glirel 1.2.1 (weights CC BY-NC-SA, adopted
and documented), spaCy 3.8.14 (tokenizer, present), torch 2.12 CPU (pinned); the FtM
layer adds followthemoney 4.9.x + nomenklatura 4.9.x (MIT) -- LINUX/CI ONLY.

---

## Conventions (read first -- the SDD subagent MUST follow these)

- **You are ALREADY on the feature branch `feat/phase12-entity-extract`. Commit
  directly. Do NOT create or switch branches.**
- **Read ONLY this plan, the design doc
  `docs/plans/2026-06-05-magpie-phase12-entity-extract-design.md`, and the files you
  create.** Other repo files carry non-ASCII and will content-filter-block your Read
  tool. Everything you need is inline here.
- **ASCII ONLY** in every file/fixture you write.
- **Run tests via the venv python** (the CC PowerShell tool is -NoProfile, so bare
  `python` hits the wrong interpreter):
  `& .venv\Scripts\python.exe -m pytest <path> -q`
  Offline subset (the default suite):
  `& .venv\Scripts\python.exe -m pytest -m "not docling and not spacy and not xray and not tsa and not gliner and not ftm" -q`
- **TDD, frequent commits.** Red -> green -> commit. Use `-m` NOT `-k`.
- **Three requirements files (do not cross them):**
  - `requirements-dev.txt` -- the Windows dev venv source of truth. Gets gliner/glirel
    + a PINNED transformers (Task 0). NEVER followthemoney/nomenklatura (PyICU fails).
  - `requirements-offline.txt` -- the default CI offline subset. Unchanged (no FtM).
  - `requirements-ftm.txt` -- NEW, Linux/CI ONLY: followthemoney + nomenklatura.
    Installed only by the CI `ftm` job and Phase-13 Docker.
- **followthemoney does NOT install on this Windows box** (PyICU/ICU). Therefore:
  - The PURE CORE and its tests import NO followthemoney -- they run on Windows.
  - The FtM layer (`entity_ftmize.py`) and its tests are `ftm`-marked and import
    followthemoney; they SKIP on Windows and run in the CI `ftm` job (Ubuntu).
  - Guard the marker so it skips cleanly:
    `import importlib.util;
    ftm = pytest.mark.skipif(importlib.util.find_spec("followthemoney") is None,
    reason="followthemoney not installed (Linux/CI only)")` plus the `ftm` marker.
- **Subprocess tests need hard READ TIMEOUTS up front** (Phase-11 lesson): separate
  stdout/stderr reader threads, per-read timeout, reap after kill. Prefer a Python API
  over a subprocess where one exists.

### House pattern: pure core / lazy edge

```python
# edge: heavy import is lazy, behind an injectable protocol
class GlinerEntityExtractor:
    def __init__(self, model_name="urchade/gliner_medium-v2.1"):
        self._model = None; self._model_name = model_name
    def _load(self):
        if self._model is None:
            from gliner import GLiNER            # imported on FIRST use only
            self._model = GLiNER.from_pretrained(self._model_name)
        return self._model
    def predict_entities(self, text, labels, threshold):
        return self._load().predict_entities(text, labels, threshold=threshold)
```

### Deterministic IDs (pure core, NO followthemoney)

```python
import hashlib
def stable_id(*parts):                      # the intermediate OWNS the id
    key = "|".join(str(p) for p in parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]   # bounded, collision-safe
# node id:  stable_id(namespace, doc_id, schema, normalized_name)   # PER-DOCUMENT scope
# edge id:  stable_id(namespace, edge_schema, head_id, tail_id, span_key)
# stmt id:  stable_id(namespace, doc_id, page, char_start, char_end, target_id, prop)
```
ftmize later sets `entity.id = <that id>` on the FtM proxy, so ids match cross-platform.

### FtM API cheat-sheet (ftmize layer only; verified via Context7 /alephdata/followthemoney)

```python
from followthemoney import model
e = model.make_entity("Person"); e.id = node_id   # reuse the intermediate's id
e.add("name", "John Smith"); data = e.to_dict()
m = model.make_entity("Membership"); m.id = edge_id
m.add("member", member_id); m.add("organization", org_id); m.add("role", "board member")
```
Edge endpoint props: Membership(member, organization), Directorship(director,
organization), Employment(employee, employer), Ownership(owner, asset),
Representation(agent, client), Family(person, relative), Associate(person, associate),
ContractAward(authority, supplier), UnknownLink(subject, object).

### The trustworthy-ingest seam (reuse Phase 8)

The orchestrator checks `doc["trustworthy_for_extraction"]` FIRST and REFUSES (no
extraction) when false (review / PARTIAL_SUCCESS docs).

---

## Task 0: deps, markers, transformers PIN, latency (main-thread setup)

**Files:** Modify `requirements-dev.txt`, `pyproject.toml`; Create `requirements-ftm.txt`;
Create `skills/entity-extract/references/prior-art.md`.

Steps:
1. `requirements-dev.txt`: add ONLY `gliner==0.2.26`, `glirel==1.2.1` (+ the pinned
   transformers from step 3). Create a SEPARATE `requirements-ftm.txt` containing
   `followthemoney==4.9.0` and `nomenklatura==4.9.1` with a header comment: "Linux/CI
   ONLY -- PyICU has no Windows wheel; do NOT install on the Windows dev venv." Do NOT
   put the FtM deps in `requirements-dev.txt` OR `requirements-offline.txt` (both are
   installed on the Windows venv/bootstrap and would fail building PyICU).
2. `pyproject.toml` markers: add `gliner` ("model-gated GLiNER/GLiREL integration;
   loads real weights") and `ftm` ("followthemoney/nomenklatura contract; Linux/CI
   only; skips when followthemoney is absent").
3. Install gliner/glirel on this box (`& .venv\Scripts\python.exe -m pip install
   gliner==0.2.26 glirel==1.2.1 -c requirements-dev.txt`) -- this DOWNGRADES
   transformers to 5.1.0. RE-RUN the docling heavy tests (`-m docling`) to CONFIRM
   docling still passes at transformers 5.1.0. Then PIN the verified coexisting version
   in `requirements-dev.txt` (e.g. `transformers==5.1.0`) so a future rebuild cannot
   silently resolve a different <5.2 transformers and re-break docling. If docling does
   NOT pass at any gliner-compatible transformers, split docling and gliner into
   separate heavy install profiles and document that.
4. Empirical latency: load `urchade/gliner_medium-v2.1` + `jackboyla/glirel-large-v0`
   (~2.6 GB first-run download), run entities+relations over one real page (the
   Greenville RFP page 1 if MAGPIE_PHASE8_REAL_PDF is set, else a synthetic paragraph),
   record per-page CPU latency native vs windowed.
5. Write `skills/entity-extract/references/prior-art.md` (ASCII): verified versions +
   pins-held, the confirmed GLiNER `predict_entities` / GLiREL `predict_relations`
   signatures, the measured latency, the FtM 4.x API, the PyICU/Windows finding + the
   decouple, the GLiREL NC-weights note, and the windowing implication.
6. Commit.

---

## Task 1: entity_taxonomy.py -- taxonomy config (pure, Windows)

**Files:** Create `scripts/entity_taxonomy.py`; Test `tests/test_entity_taxonomy.py`

- `EntityType(label, ftm_schema)`; `RelationSpec(label, ftm_edge, head_prop, tail_prop,
  allowed_head: frozenset, allowed_tail: frozenset, role: str|None)`;
  `Taxonomy(name, entity_types, relations)` with `entity_labels()`,
  `ftm_schema_for(label)` (LegalEntity fallback), `relation_for(label)`,
  `allowed(rel_label, head_label, tail_label)`.
- `GENERIC_TAXONOMY` (the ~19 entities / ~9 relations from design section 9),
  `FLOCK_PRESET` (generic + `shares-data-with` agency<->agency -> UnknownLink role
  "data-sharing"), `resolve(name="generic")` (raises on unknown).

**TDD:** label set; ftm_schema_for person/official->Person, company/vendor->Company,
agency/org->Organization, unknown->LegalEntity; allowed() permits Person->Organization
for "member of", rejects Organization->Person; FLOCK_PRESET adds shares-data-with;
resolve("nope") raises. Implement, green, commit.

---

## Task 2: entity_extract.py -- windowing + span dedup (pure, Windows)

**Files:** Create `scripts/entity_extract.py`; Test `tests/test_entity_extract_windowing.py`

- `Span(text, label, char_start, char_end, score)` (PAGE char offsets, end exclusive).
- `plan_windows(page_text, *, max_chars=1400, overlap=200) -> list[Window(text, char_base)]`
  -- split on whitespace near the limit; deterministic.
- `dedup_spans(spans) -> list[Span]` -- drop exact (start,end,label) dups; for
  overlapping same-label spans keep the longer then higher score; stable sort.

**TDD:** multi-window char_base correctness; one-window short page; dedup keeps the
longer overlap, preserves distinct-label overlaps. Implement, green, commit.

---

## Task 3: entity_extract.py -- the intermediate model + builders (pure, Windows)

**Files:** Modify `scripts/entity_extract.py`; Test `tests/test_entity_extract_intermediate.py`

NO followthemoney. Build the followthemoney-FREE intermediate:
- `Node(id, schema, name, label)` ; `Edge(id, schema, head_id, tail_id, role, label)`.
- `make_node(span, doc_id, namespace, taxonomy) -> Node`: schema = ftm_schema_for(label);
  id = stable_id(namespace, doc_id, schema, name.strip().casefold()); name = span.text.strip().
  **PER-DOCUMENT scope (plan-review fix -- premature-node-merge):** the same name+schema
  in ONE doc -> one node, but cross-document homonyms stay DISTINCT nodes. Phase 12
  NEVER merges across documents -- the FIRST true merge (and homonym disambiguation) is
  Phase-13 nomenklatura resolution. Collapsing homonyms corpus-wide here would be
  irreversible.
- `make_edge(rel_label, head_node, tail_node, span_key, namespace, taxonomy) -> Edge|None`:
  if not taxonomy.allowed(rel_label, head.label, tail.label) -> None (pair filter);
  else schema = relation_for(rel_label).ftm_edge (or UnknownLink for an unmapped label),
  id = stable_id(namespace, schema, head.id, tail.id, span_key), role per spec/label.
  Ownership of a non-ownable Organization -> degrade to UnknownLink (record reason).

**TDD:** make_node deterministic + stable id; TWO different docs with the same name ->
TWO distinct node ids (NO cross-doc merge); the same name twice in ONE doc -> one id;
make_edge "member of" Person->Org yields Membership with head/tail set; disallowed pair
-> None; unmapped label -> UnknownLink; Ownership of Organization -> UnknownLink.
Implement, green, commit.

---

## Task 4: statements, provenance, review queue, intermediate bundle (pure, Windows)

**Files:** Modify `scripts/entity_extract.py`; Test `tests/test_entity_extract_bundle.py`

- `statement_id(namespace, doc_id, page, char_start, char_end, target_id, prop)`
  (stable_id; UNIQUE per mention -- never collapses repeats).
- `Statement(statement_id, kind, target_id, target_kind, schema, prop, value, doc_id,
  page, char_start, char_end, model, confidence, decision="pending", reviewer=None,
  supersedes=None, superseded_by=None)`.
- `build_statements(nodes, edges, spans_by_id, doc_meta, models) -> list[Statement]`:
  one per node-name mention; one per edge (the edge's evidence span carries the edge's
  provenance -- provenance attaches to edges, not only endpoint props).
- `ReviewQueue(statements)`: `pending()`, `decide(statement_id, decision, reviewer)`,
  `accepted()`; JSONL persist/load. **Statements are IMMUTABLE (plan-review fix --
  edit-path):** an EDIT does not mutate. `edit(statement_id, new_value, reviewer)` marks
  the original `decision="edited"` + `superseded_by=<new id>`, and APPENDS a new
  statement (fresh statement_id, `decision="accepted"`, `supersedes=<original id>`)
  carrying the corrected value. Auditable; ids stay stable per record.
- `build_intermediate(accepted_statements, nodes, edges, manifest_meta) -> dict` ->
  the reviewed INTERMEDIATE bundle (plain JSON). **GRAPH-CLOSURE RULE (plan-review fix
  -- graph-closure):** an accepted EDGE is emitted ONLY if BOTH endpoint nodes are
  accepted; an accepted edge with a pending/rejected endpoint is DROPPED with a warning
  (fail-closed -- never emit an edge to an unreviewed node, never silently auto-include
  an unreviewed node past the human gate). Outputs:
  - `<name>.intermediate.json` -> {schema_version:"1.0", dataset_namespace,
    source_doc_ids, nodes:[...], edges:[...], counts} (ACCEPTED + closure-valid only).
  - `<name>.provenance.jsonl` -> one row per accepted statement {statement_id,
    target_id, target_kind, prop, value, doc_id, page, char_start, char_end, model,
    confidence, reviewed:true}.

**TDD:** statement_id stable + DISTINCT for two mentions of the same value on different
pages; decide() moves a statement; edit() marks the original "edited" + appends a
superseding accepted statement with a fresh id + supersedes/superseded_by links;
build_intermediate includes only accepted targets + one provenance row per accepted
statement + schema_version/counts; CLOSURE: an accepted edge with a non-accepted
endpoint is DROPPED + warned (never emitted); rejected target excluded but kept in the
queue record. Implement, green, commit.

---

## Task 5: entity_models.py -- the lazy model edge (injectable; gliner-marked)

**Files:** Create `scripts/entity_models.py`; Test `tests/test_entity_models.py`

- Protocols: `EntityExtractor.predict_entities(text, labels, threshold) -> list[Span]`;
  `RelationExtractor.predict_relations(text, entity_spans, relation_specs, threshold)
  -> list[RelationTriple(head_span, tail_span, label, score)]`.
- `GlinerEntityExtractor` (lazy GLiNER). `GlirelRelationExtractor` (lazy GLiREL + lazy
  `spacy.blank("en")` tokenizer): converts entity char-spans to INCLUSIVE token
  indices, builds `ner=[[tok_start,tok_end,TYPE,text]]` + the glirel label dict
  (allowed_head/allowed_tail from RelationSpecs), calls predict_relations, maps back to
  char spans. (char<->token lives HERE so the pure core stays spaCy-free.)

**TDD:** import-purity (subprocess-isolated, hard timeout): importing
`scripts.entity_models` pulls in NEITHER torch NOR gliner NOR glirel (assert absent
from sys.modules). `@pytest.mark.gliner`: real models, predict_entities finds a known
span; predict_relations round-trips valid char offsets. Implement, green, commit.

---

## Task 6: orchestrator + input gate (pure end-to-end with a FAKE; Windows)

**Files:** Modify `scripts/entity_extract.py`; Create `tests/conftest_entity.py`
(FakeEntityExtractor + FakeRelationExtractor returning canned spans/triples);
Test `tests/test_entity_extract_pipeline.py`

- `extract(doc_json, *, taxonomy, namespace, entity_extractor, relation_extractor,
  threshold) -> ExtractResult(review_queue, refused, warnings)`:
  1. refuse if not `doc_json["trustworthy_for_extraction"]`;
  2. doc_id = doc_json's id; per page: plan_windows -> entity_extractor.predict_entities
     (offset back to page coords) -> dedup_spans;
  3. relation_extractor.predict_relations -> triples;
  4. make_node(span, doc_id, ...) / make_edge -> nodes+edges; build_statements ->
     ReviewQueue. (Thread doc_id through to make_node per Task 3's per-doc scope.)

**TDD (fakes, NO models):** non-trustworthy -> refused, empty queue; a 2-page synthetic
doc yields expected nodes/edges + a queue whose statements carry correct page/char
provenance; disallowed relations dropped; two docs with the same name -> distinct nodes.
Implement, green, commit.

---

## Task 7: entity_ftmize.py -- the FtM layer + contract (ftm-marked; Linux/CI)

**Files:** Create `scripts/entity_ftmize.py`; Test `tests/test_entity_ftmize.py`
(ALL `@ftm` -- skips on Windows); Create
`tests/fixtures/reviewed_intermediate_sample/` (a tiny pinned reviewed intermediate:
nodes + edges + provenance + ONE UnknownLink; closure-valid).

`entity_ftmize.py` (imports followthemoney):
- `to_ftm(intermediate) -> list[EntityProxy]`: for each node/edge make the FtM proxy,
  set `proxy.id = <intermediate id>` (ids are per-doc from Phase 12; ftmize PRESERVES
  them, never re-merges), add name/endpoint props/role; validate.
- `write_bundle(intermediate, out_dir)` -> `<name>.entities.ftm.json` (ndjson of
  to_dict()) reusing the intermediate's provenance.jsonl + manifest.

Contract tests (Codex's must-have -- prove graph-readiness; all `@ftm`):
1. Schema validity: every proxy round-trips via model.get_proxy; no edge has a dangling
   endpoint (closure already enforced upstream, re-assert here).
2. `ftm export-cypher`: prefer a followthemoney Python export API; else subprocess the
   `ftm export-cypher` console script (HARD read timeouts + reader threads + reap).
   Assert non-empty Cypher referencing node ids.
3. `ftm export-neo4j-bulk`: assert the CSV/import-script set is produced.
4. nomenklatura smoke: load the bundle into a nomenklatura resolver store (SQLite/JSON,
   NO Docker), run an xref pass, assert candidate pairs without error. Include the two
   same-name-different-doc nodes from the fixture so xref SEES them as resolution
   candidates (proving Phase 12 left them distinct for Phase 13 to resolve).
5. `assert_phase13_consumable(bundle_dir)` helper (Phase 13 imports it): checks
   manifest schema_version, the files, counts.

Implement, run on Linux/CI (`-m ftm`), commit. (On Windows these SKIP.)

---

## Task 8: the skill + the human review gate (SKILL.md + smoke)

**Files:** Create `skills/entity-extract/SKILL.md`; Test
`tests/test_entity_extract_skill.py` (PyYAML frontmatter smoke).

SKILL.md (lean, imperative, third-person triggers): take a trustworthy ingest doc
(REFUSE a non-trustworthy one) -> resolve taxonomy (generic | flock preset) ->
`extract(...)` -> the HYBRID HUMAN GATE (drain the statement queue inline: show the
SOURCE SPAN before the claim, accept/reject/edit [edit = supersede], persist; or
process the queue out-of-band) -> `build_intermediate` of ACCEPTED + closure-valid
statements (the Windows deliverable) -> NOTE: the FtM bundle is produced by
`entity_ftmize` at the Phase-13 boundary (Linux/Docker). Document: the mandatory gate
(F1 ~25-40, never autonomous), the GLiREL NC-weights note, the PyICU/decouple reality
(Windows -> intermediate; FtM -> Linux), the per-document scope (no cross-doc merge;
Phase 13 resolves), the trustworthy refusal seam, decoupling from pii_sweep, and that
the queue shape is the Phase-13 HITL surface.

**TDD:** frontmatter parses; name == entity-extract; description carries triggers +
entity/relation extraction; body documents the gate, the intermediate->ftmize hand-off,
the NC-weights + decouple + per-doc-scope notes, and the refuse-on-trustworthy seam.
Green, commit.

---

## Task 9: CI + onboarding (main thread for non-ASCII)

**Files:** Modify `.github/workflows/ci.yml` (add an `ftm` job: Ubuntu, install
`requirements-ftm.txt` [followthemoney + nomenklatura], run `-m ftm`; the existing
heavy `workflow_dispatch` job gains `-m gliner`); Modify `skills/setup/SKILL.md` +
`skills/doctor/SKILL.md` + `scripts/detect_tier.py` (an "extract entities (Track B)"
capability gated on gliner/glirel + the NC-weights note; the FtM/graph layer is
Linux/Docker -> Phase 13); keep the no-Docker onramp guard green (Phase 12 is
Docker-free).

**TDD:** extend `tests/test_detect_tier.py` for the new capability (gliner/glirel
present -> READY; absent -> UNAVAILABLE with an NC-weights-aware fix). Green, commit.

---

## Task 10: gliner-marked e2e + env-gated real-doc smoke

**Files:** Test `tests/test_entity_extract_integration.py` (`@pytest.mark.gliner`)

- `@gliner` e2e: real extractors over a small synthetic doc -> extract -> auto-accept
  all (in-test) -> build_intermediate -> assert the intermediate is well-formed
  (nodes/edges/provenance/manifest, closure-valid). (The `ftm` round-trip of this
  intermediate is covered by Task 7 on Linux/CI.)
- Env-gated real-doc smoke (MAGPIE_PHASE8_REAL_PDF = the Greenville Flock/ALPR RFP,
  NEVER committed): ingest -> extract with the flock preset -> assert agency/vendor/
  official entities and a procurement or data-sharing relation surface; record latency.
  Skips when the env var is absent.

Run `-m gliner` locally, commit.

---

## Closing (main thread, before the PR)
- Regenerate `MANIFEST.md` (non-ASCII -> main thread).
- Offline suite green:
  `& .venv\Scripts\python.exe -m pytest -m "not docling and not spacy and not xray and not tsa and not gliner and not ftm" -q`
- Heavy locally: `-m gliner` (and confirm `-m docling` still green at the pinned transformers).
- The `ftm` job runs in CI (Ubuntu); confirm it green there.
- Open the Phase-12 PR; merge with a merge commit.

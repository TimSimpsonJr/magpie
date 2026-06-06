---
phase: 12
title: entity-extract
track: B (entity-network)
codex_thread_id: 019e95ea-d5d9-72f0-87db-e1bbd50a4c42
status: design
date: 2026-06-05
---

# Phase 12 -- entity-extract (Track B, the entity-network input stage)

## 1. Purpose and scope

`entity-extract` is the FIRST skill of Track B. It reads an already-ingested,
trustworthy document (the Phase-6 `ingest` DoclingDocument JSON), extracts named
ENTITIES and the RELATIONS between them, maps them DETERMINISTICALLY (no LLM) into
FollowTheMoney (FtM) entities with per-claim provenance, gates every extracted
claim through a MANDATORY human review, and emits a bundle of REVIEWED FtM
entities plus a provenance sidecar. That bundle is the hand-off contract to
Phase-13 `entity-graph` (resolution + graph + watchlist cross-reference).

Scope of THIS phase: entity-extract only. NO Docker, NO graph, NO yente. The graph
half is Phase 13 (13a resolution + Neo4j import/export; 13b yente cross-ref +
yente-mcp). entity-extract is laptop-local Python, mirroring the Layer 0-1 skills.

Non-negotiable: zero-shot relation-extraction F1 on real text is ~25-40, so
NOTHING flows downstream until a human clears it. entity-extract is the first
Magpie skill whose output MUST pass a person before it is trusted (design 7).

## 2. Locked stack and license posture

- Entities: GLiNER (`gliner` 0.2.26, Apache-2.0), model `urchade/gliner_medium-v2.1`
  (209M params, ~781 MB weights, Apache-2.0). Zero-shot NER over a configured label
  set; CPU-only torch (already pinned 2.12).
- Relations: GLiREL (`glirel` 1.2.1, Apache-2.0 package), model
  `jackboyla/glirel-large-v0` (~1.87 GB weights). Zero-shot relation classification
  over candidate entity-span pairs given relation labels + type constraints.
  LICENSE: the glirel WEIGHTS are CC BY-NC-SA 4.0 (non-commercial). Tim's call
  (2026-06-05): ADOPT and DOCUMENT -- the user downloads the weights (not vendored,
  not bundled-as-binary), Magpie's MIT code is unaffected, and the non-commercial
  restriction is surfaced in setup/doctor, this skill's prior-art, and the release
  notes. This is the Phase-7 AGPL/PyMuPDF posture applied to model weights.
- Tokenizer: spaCy (`en_core_web_sm` or the existing `en_core_web_lg`) for the
  GLiNER char-offset -> GLiREL inclusive-token-index conversion. spaCy 3.8.14 is
  already a dependency (pii_sweep).
- Mapping: `followthemoney` 4.x (pin 4.9.x), MIT, pure-Python (no ML). The
  deterministic NLP-triple -> FtM entity mapping engine.
- GLiDRE (document-level RE): DEFERRED. Not on PyPI, no license, preprint, fragile.
  GLiREL with passage windowing covers relation extraction for v1.

(Supersedes the stale design 5.3 / 10 names `GLiNER-Relex`, `GLiDRE`,
`followthemoney-graph` -- see section 12.)

## 3. Architecture -- pure core / lazy model edge (the suite pattern)

Same split as `pii_sweep` (pure tally + lazy spaCy edge), `ingest_gate`/`ingest`
(pure gate + lazy Docling edge), `citation` (pure resolver), `detect_tier` (pure
aggregator + IO edge):

- `scripts/entity_extract.py` -- the PURE CORE. Imports stdlib ONLY (NO followthemoney,
  no torch/gliner/glirel). Holds: the type-compatibility pair filter, the page-windowing
  + offset math, the cross-window span dedup, the deterministic triple -> INTERMEDIATE
  (followthemoney-FREE nodes/edges with stable hashlib IDs), the statement/review-queue
  assembly, the provenance sidecar, and the reviewed-INTERMEDIATE builder. Golden-testable
  with a FAKE extractor -- no model weights. Importing the core stays ML-free AND
  followthemoney-free, so it runs on Windows (where followthemoney does NOT install --
  PyICU; see section 15). The taxonomy lives in `scripts/entity_taxonomy.py` (pure).
- `scripts/entity_models.py` -- the LAZY MODEL EDGE. Imports `gliner` / `glirel` /
  `torch` / spaCy ONLY inside the lazy loader (like `pii_sweep.SpacyPersonClassifier._load`
  and `ingest._docling_imports`), so importing the module stays cheap. Exposes an
  injectable `EntityExtractor` / `RelationExtractor` protocol; the real
  `GlinerEntityExtractor` / `GlirelRelationExtractor` load the HF weights on first
  call (first-run download, like Docling/spaCy weights). Tests inject a fake.
- `scripts/entity_ftmize.py` -- the FtM LAYER (Linux/CI ONLY). Imports `followthemoney`;
  turns the reviewed INTERMEDIATE into the FtM bundle (`*.entities.ftm.json`) and runs
  the export-cypher / nomenklatura contract tests. `ftm`-marked: SKIPS on Windows
  (followthemoney/PyICU does not install there), runs in the CI `ftm` job and Phase-13
  Docker, and is reused by Phase 13 (see section 15).

## 4. The pipeline (skill orchestration)

1. INPUT GATE. Accept an ingested DoclingDocument JSON. REFUSE if
   `trustworthy_for_extraction` is false (review / PARTIAL_SUCCESS) -- reuse the
   exact Phase-8 `investigate` seam; a non-trustworthy doc never auto-extracts.
   Pull per-page text + the page/char-offset frame.
2. WINDOW. GLiNER has a ~384-token window; split long pages into overlapping
   windows, tracking each window's (page, char_offset) base so every span maps back
   to a real document location.
3. ENTITIES (edge). Run GLiNER `predict_entities(text, labels, threshold)` over each
   window with the configured entity-type label set -> spans {text, label, start,
   end (char, exclusive), score}.
4. SPAN DEDUP (pure). Merge entity spans across overlapping windows (by char-span +
   label); resolve window-boundary duplicates/splits.
5. CHAR -> TOKEN (pure). Tokenize each window with the SHARED spaCy tokenizer; convert
   entity char-spans to the INCLUSIVE token indices GLiREL expects; build the
   `ner=[[tok_start, tok_end, TYPE, text]]` input. (Use the same tokenizer for both
   so offsets round-trip cleanly back to char spans for provenance.)
6. RELATIONS (edge). Run GLiREL `predict_relations(tokens, labels, ner, threshold,
   top_k)` with the configured relation labels + `allowed_head`/`allowed_tail`
   type constraints. The pair filter is BOTH a precision guard AND the load-bearing
   CPU-tractability guard (naive all-pairs enumeration is too slow). -> relation
   triples {head, tail, label, score}.
7. MAP (pure). Deterministically map each entity -> an INTERMEDIATE node (FtM-shaped:
   Person / Company / Organization / LegalEntity fallback) and each relation -> an
   INTERMEDIATE edge (section 6) -- followthemoney-FREE, with stable hashlib IDs.
   Assemble STATEMENTS (section 5) -- the atomic reviewable claims -- each with provenance.
8. HUMAN GATE (section 5). Drain the statement review queue; only ACCEPTED
   statements are assembled into the reviewed INTERMEDIATE.
9. OUTPUT. Write the reviewed INTERMEDIATE (`*.intermediate.json` + `*.provenance.jsonl`
   + `*.manifest.json`; followthemoney-free; section 7) -- the WINDOWS deliverable. The
   Linux/CI `ftmize` layer (section 15) turns it into the FtM bundle
   (`*.entities.ftm.json`). Emit a Librarian findings note with AGGREGATE counts only
   (raw spans stay local; any PII in surfaced entities routes through `redact-output`).

## 5. The human review gate -- hybrid statement queue (Tim's call)

The gate is the product's core honesty mechanism. Design: a HYBRID -- the review
RECORD is a first-class structured queue, drained by default via inline
conversational review.

- A STATEMENT is the atomic reviewable claim:
  - entity statement: "this span is a <type>" -> {statement_id, kind: entity,
    schema, value, doc_id, page, char_start, char_end, model, confidence,
    decision: pending}.
  - relation statement: "<head> <relation> <tail>" -> {statement_id, kind: relation,
    edge_schema, head_ref, tail_ref, doc_id, page, char_start, char_end (the
    relation's evidence span), model, confidence, decision: pending}.
- `statement_id` is a deterministic claim-level key (section 7). It is the unit of
  review AND of provenance -- it does NOT collapse repeated mentions (Codex's catch:
  `(entity_id, prop, value)` would lose multiplicity).
- DRAIN (default, inline): the skill presents each statement with its SOURCE SPAN
  shown BEFORE the extracted claim (evidence-before-claim, the automation-bias
  counter from design 7, same posture as `investigate`). The human accepts / rejects
  / edits; decisions persist back to the queue (decision + reviewer + timestamp).
  Solo single-reviewer is the default and only required gate (design 7).
- DRAIN (optional, batch): the queue is a persisted artifact, so a reviewer may
  process it out-of-band (open the file, mark decisions) for a large document.
- The queue's shape is deliberately the SAME pattern as Phase-13 `entity-graph`'s
  HITL resolution queue (nomenklatura get_candidates -> human -> resolver.decide),
  so both Track-B human-in-the-loop steps share one review surface.
- ACCEPTED OUTPUT vs UNRESOLVED QUEUE are distinct (Codex): only accepted statements
  enter `*.ftm.json` + the sidecar; pending/rejected stay in the queue record for
  audit, never in the output bundle.

## 6. The deterministic NLP -> FtM mapping (the crux)

Verified against FtM 4.x docs (Context7 `/alephdata/followthemoney`): create with
`model.make_entity(schema)`, `entity.make_id(*components)`, `entity.add(prop, value)`
or `entity.add(prop, other_entity)` for edge endpoints, serialize via
`entity.to_dict()` -> {id, schema, properties}.

Node mapping (entity type -> FtM schema):
- person, government official -> Person
- company, vendor -> Company
- organization, government agency -> Organization (PublicBody if clearly state)
- attorney/legal counsel -> Person (role recorded)
- LegalEntity is the FALLBACK when Person-vs-Organization is ambiguous (Codex:
  stable fallback for ambiguous orgs).

Edge mapping (relation -> FtM edge entity, with VERIFIED endpoint property names):
- employed by        -> Employment   (employee -> employer; role)
- member of          -> Membership   (member -> organization; role)
- director/officer of-> Directorship (director -> organization; role)
- owns / subsidiary  -> Ownership    (owner -> asset; role) [asset must be Company/Asset]
- represents/counsel -> Representation(agent -> client; role)
- family of          -> Family       (person -> relative; relationship)
- associate of       -> Associate    (person -> associate; relationship)
- party to contract / procurement -> ContractAward (authority -> supplier) [the
  agency<->vendor procurement backbone; central to the surveillance use case]
- affiliated / linked-> UnknownLink  (subject -> object; role) [DETERMINISTIC FALLBACK
  for any relation that does not map to a specific edge -- Codex's required fallback]

All edge entities inherit Interval (sourceUrl, proof -> Document, startDate,
endDate, recordId, summary) -- provenance can also reference the source document at
the entity level, but the char-level provenance lives in the sidecar (section 7).

Pair filtering: each relation label declares `allowed_head` / `allowed_tail` entity
types; only type-compatible candidate pairs are scored (e.g. Membership only for
Person -> Organization). Entity-type set capped at ~15-20 (above ~20 GLiNER precision
and latency degrade).

## 7. Provenance and the Phase-13 hand-off contract (Codex-hardened)

The WINDOWS deliverable is the reviewed INTERMEDIATE (followthemoney-free); the Linux/CI
`ftmize` layer (section 15) turns it into the FtM bundle Phase-13 consumes. Both, plus the
schema_version, are the entity-graph input contract -- get it right NOW:

- `<name>.intermediate.json` -- the reviewed INTERMEDIATE (WINDOWS output): nodes + edges
  (FtM-shaped, followthemoney-free, stable hashlib IDs). ftmize maps this to
  `<name>.entities.ftm.json` (newline-delimited `EntityProxy.to_dict()`, ids preserved)
  in CI/Phase-13.
- `<name>.provenance.jsonl` -- the provenance sidecar, one row PER STATEMENT:
  {statement_id, target_id (the entity_id or edge_id this statement supports),
  target_kind (entity|edge), prop, value, doc_id, page, char_start, char_end, model,
  confidence, reviewed: true}. Provenance attaches to EDGES too, not just node props
  (Codex). Multiple mentions of the same entity -> multiple sidecar rows (distinct
  statement_ids) under one target_id -- multiplicity preserved.
- `<name>.manifest.json` -- the bundle header: {schema_version, dataset_namespace,
  source_doc_ids, entity_count, edge_count, created_with (model + prompt versions),
  ftm_version}. EXPLICIT `schema_version` so Phase 13 can detect drift (Codex).

Deterministic ID + namespace scheme (Codex: lock now). The INTERMEDIATE owns the IDs:
they are stable hashlib hashes (`stable_id`), NOT followthemoney's make_id, so the core
computes them WITHOUT followthemoney on Windows; ftmize later sets `proxy.id` to these
exact values.
- `dataset_namespace` = the corpus/run name; scopes all IDs (mirrors yente's
  `ftm namespace --dataset` collision-avoidance for Phase 13).
- node entity_id = stable_id(dataset_namespace, doc_id, schema, normalized_name) -- PER
  DOCUMENT: the same name+type in ONE doc is one node, but cross-document homonyms stay
  DISTINCT nodes. Phase 12 NEVER merges across documents; the FIRST true merge (and
  homonym disambiguation) is Phase-13 nomenklatura -- collapsing homonyms in Phase 12
  would be irreversible (plan-review fix: premature-node-merge).
- edge_id = stable_id(dataset_namespace, edge_schema, head_id, tail_id, evidence_span).
- statement_id = stable_id(dataset_namespace, doc_id, page, char_start, char_end,
  target_id, prop) -- unique per mention. An EDITED statement's replacement id is
  stable_id(original_statement_id, "edit", ordinal), so it never collides with the
  original (plan-review fix: edit-id-collision).

Dedup scope (decision, hardened after plan-review): entity-extract dedups only WITHIN
A SINGLE DOCUMENT (same name+type in one doc -> one node); it NEVER merges across
documents, so two different "John Smith" persons in different docs remain DISTINCT
nodes. ALL entity RESOLUTION/merge (cross-doc + homonym disambiguation) is Phase-13
nomenklatura's job. Keeps the seam clean and avoids an irreversible premature merge;
entity-extract never imports graph/resolution code.

## 8. Phase-12 FtM-contract de-risk (Codex's must-have -- no Docker)

The `ftmize` layer + these contract tests are `ftm`-marked: they import followthemoney/
nomenklatura (Linux/CI ONLY -- PyICU blocks Windows), run in the CI `ftm` job (Ubuntu)
and Phase-13 Docker, and SKIP on Windows. They PROVE the reviewed intermediate is
graph-ready -- closing the loop the absent Phase-13 infra would otherwise leave open:

1. Validate EVERY reviewed entity/edge against its FtM schema (followthemoney
   validation) -- no invalid property, no dangling edge endpoint.
2. Run `ftm export-cypher` AND `ftm export-neo4j-bulk` on synthetic reviewed
   fixtures in tests; assert they produce well-formed Cypher / bulk-import output
   (no Neo4j server needed -- these are pure CLI transforms).
3. Run a `nomenklatura` resolution smoke on the reviewed bundle via its SQLite/JSON
   resolver store (no Docker) -- prove the bundle is xref-able.
4. Pin ONE tiny reviewed-corpus fixture: nodes + edges + provenance sidecar + an
   `UnknownLink` fallback edge, exercising the full contract.
5. Assert that exact bundle is consumed UNCHANGED by the Phase-13 contract (a
   contract test that Phase 13 will re-use).

These do NOT run in the default offline suite (followthemoney does not install on
Windows); the CI `ftm` job guards the hand-off contract continuously on Ubuntu.

## 9. Taxonomy -- generic default + surveillance/flock preset (Tim's call)

Generic engine, jurisdiction glue in config (design Decision 2, like Track A's
`_adapters.py`). Config-driven label sets so a jurisdiction tunes without code.

Generic default entity types (~19): person, organization, government agency,
company, government official, attorney/legal counsel, product/system/technology,
address, jurisdiction (city/county/state), phone, email, date, monetary amount,
case/docket number, permit/license/contract number, statute/regulation,
position/title, vehicle.

Generic default relations -> FtM edges (~9): employed by, member of,
director/officer of, owns/subsidiary of, represents/counsel for, family of,
associate of, party to contract/procurement (-> ContractAward), affiliated/linked
(-> UnknownLink fallback).

`surveillance/flock` preset (config bundle the DeflockSC use case selects): the
generic set PLUS a `shares-data-with / network-member` relation (agency <-> agency,
the Flock network-sharing edge) and tuned label phrasings for vendor/agency/official
extraction. (License plates are PII and live mostly in Track-A CSV logs; in
documents `vehicle` covers it and `redact-output` handles plate PII if it surfaces.)

## 10. Testing strategy

- Pure-core golden tests (the bulk): inject a FAKE EntityExtractor/RelationExtractor
  (deterministic span/relation fixtures), so the mapping, pair-filter, windowing,
  dedup, statement/queue, provenance, and FtM serialization are tested with NO model
  weights. Mirrors pii_sweep's fake-classifier suite.
- FtM-contract tests (section 8): offline, real `followthemoney`/`nomenklatura`,
  no models, no Docker -- in the default CI offline job.
- `gliner` pytest marker (like spacy/docling/xray/tsa): model-gated integration that
  loads real GLiNER + GLiREL weights, runs the actual extract over a small fixture,
  asserts the pipeline shape. Excluded from the offline subset; runs in the heavy job.
- Real-document validation: the Greenville Flock/ALPR RFP 21-3746 (confirmed by Tim
  to be the Flock RFP), env-gated + never committed (reuse `MAGPIE_PHASE8_REAL_PDF`
  or a new var), for the real-doc smoke + the CPU-latency measurement + the
  surveillance-preset integration check. Synthetic fixtures carry the deterministic
  committed goldens.
- Empirical CPU-latency sub-gate (Codex/research flagged latency UNVERIFIED): an
  early Task that measures GLiNER + GLiREL per-page latency on this box (like Phase
  9's live TSA smoke) to size windowing and set UX expectations before the gate UX
  is fixed.

## 11. Honest limits (documented, design 7)

- Zero-shot relation F1 ~25-40 on real text -> human gate MANDATORY, never
  autonomous. Benchmark RE numbers are inflated by distant supervision (the GLiREL
  authors disavow WikiZSL); do not promise benchmark accuracy.
- GLiNER zero-shot NER F1 ~48% average; domain-specific lower; label wording is the
  biggest lever (hence the config-driven taxonomy + preset).
- GLiNER ~384-token window -> long pages are windowed with overlap; boundary entities
  can split/duplicate (the dedup step mitigates, not eliminates).
- CPU latency per page UNVERIFIED -> the empirical sub-gate measures it; pair
  filtering is mandatory for GLiREL throughput.
- GLiREL weights are CC BY-NC-SA (non-commercial) -- documented, user-downloaded.
- NO cross-document coreference here -- deferred to Phase-13 nomenklatura.

## 12. Decisions and decoupling

Locked decisions:
- License: adopt + document the GLiREL NC weights (Tim).
- Phasing: Phase 12 entity-extract (no Docker); Phase 13 = 13a (resolution + Neo4j
  import/export) + 13b (yente cross-ref + yente-mcp), separable (Codex).
- Hybrid review gate; statement-level `statement_id` provenance key (Codex).
- Sidecar carries span provenance; the FtM bundle uses standard EntityProxy
  serialization (StatementEntity is a future option, YAGNI now).
- Dedup per-corpus by name+type; cross-doc resolution is Phase 13.
- Generic taxonomy + surveillance/flock preset.
- `investigation-core` split stays DEFERRED -- Track B is the same vertical, a
  different analysis branch; do not split the spine while crossing the Docker
  boundary for the first time (Codex).

Decoupling:
- entity-extract shares NO code with the Track-A analysis modules (stats / load_table
  / derive / recipe / pii_sweep). Its only spine seams: it REFUSES a non-trustworthy
  `ingest` doc (the Phase-8 `trustworthy_for_extraction` contract), routes output via
  Librarian, and sends any surfaced entity PII through `redact-output`.
- Distinct from `pii_sweep`: that does spaCy PERSON NER to COUNT PII exposure;
  entity-extract does GLiNER multi-type NER + GLiREL relations to BUILD a network.
  Different engines, different purposes; no shared code.

Supersedes (housekeeping, Codex-flagged): the master design
`docs/plans/2026-06-03-magpie-design.md` 5.3 / 10 still name `GLiNER-Relex`,
`GLiDRE`, and `followthemoney-graph`. The real build is GLiNER + GLiREL (GLiDRE
deferred) and the `ftm export-cypher` / `export-neo4j-bulk` CLI (the
followthemoney-graph package is gone). The master design's Track-B rows will be
corrected to match.

## 13. New dependencies

- `gliner` 0.2.26 (Apache-2.0) -- pulls torch (pinned 2.12 CPU) + transformers + HF
  model weights (gliner_medium-v2.1 ~781 MB, lazy first-run download).
- `glirel` 1.2.1 (Apache-2.0 package; weights CC BY-NC-SA, documented) -- ~1.87 GB
  weights, lazy first-run download.
- `followthemoney` 4.9.x (MIT, pure-Python) -- the mapping engine; light.
- `nomenklatura` 4.9.x (MIT) -- for the Phase-12 FtM-contract resolution smoke
  (section 8); also Phase-13's resolution engine.
- spaCy 3.8.14 already present (tokenizer).
- New `gliner` pytest marker. numpy/pandas/torch pins UNTOUCHED (verify at the
  research/plan gate that gliner/glirel install against the pinned numpy/torch).

## 14. Phase-13 hand-off contract (defined here, built there)

entity-graph (Phase 13) consumes the section-7 bundle: the reviewed
`*.entities.ftm.json` + `*.provenance.jsonl` + `*.manifest.json`. It runs
nomenklatura resolution (3-bucket auto/review/distinct + HITL queue) -> `ftm
export-cypher`/`export-neo4j-bulk` into Neo4j -> yente cross-ref against watchlists +
own corpus. entity-extract does NOT import any of that; the bundle + schema_version
is the entire seam.

## 15. Addendum (2026-06-06): the FtM layer is decoupled (Linux/CI-only)

Research-gate finding: PyICU has NO Windows wheel and `normality` (a `followthemoney`
dependency) HARD-requires it, so the OpenSanctions/FtM stack (followthemoney,
nomenklatura, rigour, yente) does NOT pip-install on the Windows venv without building
ICU from source. It is Linux-native -- and Phase 13 already runs it in Docker. Tim's
call (2026-06-06): DECOUPLE the FtM layer. This refines sections 3 / 7 / 8 / 13:

- Windows-native pure core (`entity_taxonomy.py` + `entity_extract.py`): GLiNER/GLiREL
  extraction -> a followthemoney-FREE REVIEWED INTERMEDIATE (plain JSON: nodes, edges,
  statements, provenance; DETERMINISTIC hashlib IDs computed WITHOUT followthemoney).
  Fully Windows-testable -- the pure core imports NO followthemoney.
- The FtM layer is a SEPARATE module `scripts/entity_ftmize.py`: reviewed-intermediate
  -> followthemoney bundle (`*.entities.ftm.json`) + the `ftm export-cypher` /
  `export-neo4j-bulk` / nomenklatura contract tests. It imports followthemoney and is
  gated by a NEW `ftm` pytest marker that SKIPS when followthemoney is not importable
  (i.e. on Windows). It runs in CI (Ubuntu) and Phase-13 (Docker/Linux), and Phase 13
  reuses it.
- The Phase-12 deliverable ON WINDOWS is the reviewed INTERMEDIATE; the FtM bundle is
  materialized by ftmize at the Phase-12/13 boundary (Linux). Codex's "prove
  graph-readiness in Phase 12" is satisfied by the `ftm`-marked contract tests running
  in CI continuously.
- Deterministic IDs: computed in the pure core as a stable hash over
  (dataset_namespace, doc_id, schema, normalized_name) for nodes (PER-DOCUMENT scope --
  no cross-doc merge; Phase 13 does the first true resolution) and
  (dataset_namespace, edge_schema, head_id, tail_id, span_key) for edges; ftmize sets
  these exact ids on the FtM proxies (entity.id), so the INTERMEDIATE owns the id and
  followthemoney's make_id algorithm is not a cross-platform dependency.
- Deps: gliner/glirel (Windows-ok, heavy, `gliner`-marked) install but DOWNGRADE
  transformers to <5.2.0 (docling currently uses a newer transformers); pip's resolver
  found this consistent with docling's declared range; once verified, PIN the coexisting
  transformers in requirements-dev.txt for deterministic rebuilds. followthemoney/
  nomenklatura go in a SEPARATE requirements-ftm.txt (NOT requirements-dev.txt or
  requirements-offline.txt -- both install on the Windows venv and would fail on PyICU);
  only the CI `ftm` job (Ubuntu) and Phase-13 Docker install it.

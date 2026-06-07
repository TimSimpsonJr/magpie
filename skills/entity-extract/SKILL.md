---
name: entity-extract
description: This skill should be used when the user wants to extract named entities and the relations between them from a trustworthy ingested document, build an entity/relationship network or knowledge graph from a FOIA PDF, map people / organizations / agencies / vendors and their connections, or produce a reviewed FtM-shaped entity intermediate. It runs GLiNER entity extraction (multi-type NER) plus GLiREL relation extraction over an ingested document, maps them deterministically to a FollowTheMoney-shaped reviewed intermediate with per-statement provenance, and gates every claim through a mandatory human review.
version: 0.1.0
---

# entity-extract

entity-extract is the first Track-B skill. It turns ONE trustworthy ingested
document into a REVIEWED, FtM-shaped intermediate -- but only after a mandatory
human gate clears every claim. It runs GLiNER entity NER + GLiREL relation
extraction, maps the result deterministically to an FtM-shaped graph with
per-statement provenance, and emits a followthemoney-free reviewed intermediate.

Engines (all pure or lazy -- nothing heavy imports until you call a real model):

- `scripts/entity_extract.py` -- the pure core: `docling_to_extraction_input`,
  `extract()`, `build_intermediate()`, `ReviewQueue`, `Statement`. stdlib only.
- `scripts/entity_taxonomy.py` -- `resolve()`, the entity/relation taxonomy
  (generic default + the surveillance/flock preset).
- `scripts/entity_models.py` -- the lazy GLiNER/GLiREL edge (gliner/glirel/spaCy
  imported only on first predict).

No `.mcp.json` ships -- this skill drives the scripts directly.

## 0. Refuse a non-trustworthy document (checked first)

The ONLY upstream input is a Phase-6 ingest DoclingDocument JSON plus its
IngestResult. Normalize it first:

    from scripts.entity_extract import docling_to_extraction_input
    doc = docling_to_extraction_input(
        docling_json,
        doc_id=source_sha256,                       # the ingest source hash
        trustworthy_for_extraction=ingest_result_bool,
    )

`extract()` then REFUSES (returns `refused=True`, an empty queue) when
`trustworthy_for_extraction` is false -- i.e. the ingest decision was `review`
or `PARTIAL_SUCCESS`. Key on the BOOLEAN, never the decision string: this is the
SAME Phase-8 trust seam `investigate` uses. There is NO override in v1 -- a
non-trustworthy document never auto-extracts.

## 1. Resolve the taxonomy + extract

    from scripts.entity_taxonomy import resolve
    from scripts.entity_models import GlinerEntityExtractor, GlirelRelationExtractor

    taxonomy = resolve("generic")        # or resolve("flock")
    result = extract(
        doc,
        taxonomy=taxonomy,
        namespace="<run name>",
        entity_extractor=GlinerEntityExtractor(),
        relation_extractor=GlirelRelationExtractor(),
        threshold=0.4,
    )

`resolve("flock")` is the surveillance/flock preset -- the generic set PLUS an
agency<->agency `shares data with` edge for the Flock/ALPR network-sharing case.

Thresholds: entities tolerate ~0.4-0.5; relations are NOISY and need a LOW
threshold (~0.2). Do NOT raise the relation threshold to chase precision -- the
HUMAN GATE, not the threshold, is the precision filter (see
`references/prior-art.md` section 6). Per page the core windows the text ->
GLiNER spans re-offset to page coords -> dedup -> nodes + relations -> a
`ReviewQueue` of `Statement`s.

## 2. The mandatory human gate -- the statement queue (evidence BEFORE claim)

The gate is the product's core honesty mechanism. Drain
`result.review_queue`. For each PENDING `Statement`, show the SOURCE SPAN FIRST
-- the `doc_id`, `page`, and `char_start:char_end` of the cited text -- BEFORE
the extracted claim (entity: "this span is a <schema>"; relation:
"<head> <label> <tail>"), then the model + confidence. Evidence before claim is
the automation-bias counter; never lead with the claim.

The human accepts / rejects / edits:

    result.review_queue.decide(statement_id, "accepted", reviewer="tim")
    result.review_queue.decide(statement_id, "rejected", reviewer="tim")
    result.review_queue.edit(statement_id, "Corrected Value", reviewer="tim")

Edit semantics (v1):

- ENTITY statements are EDITABLE. An EDIT SUPERSEDES, it never mutates:
  `edit()` marks the original `decision="edited"`, then appends a fresh
  `accepted` statement with `supersedes` / `superseded_by` links and a derived
  id. The corrected value FLOWS to the emitted node -- `build_intermediate`
  reconciles each node's `name` from the latest accepted entity name-statement,
  so a corrected entity name reaches the bundle's node (not just provenance).
- RELATION statements are ACCEPT / REJECT only. `edit()` on a relation RAISES
  `ValueError`: a relabel cannot recompute the FtM edge schema/id without an
  id-changing cascade in v1. To change a relation, REJECT it and re-extract.

The queue is a persisted artifact (`to_jsonl()` / `from_jsonl()`), so a large
document can be drained out-of-band (open the file, mark decisions) and
reloaded.

Solo single-reviewer is the only required gate. NEVER run this autonomously:
zero-shot relation-extraction F1 is ~25-40, so a machine-only edge is untrusted
by construction. This queue is deliberately the SAME shape as Phase-13
entity-graph's HITL resolution queue, so both Track-B human-in-the-loop steps
share one review surface.

## 3. Build the reviewed intermediate (the Windows deliverable)

    bundle, warnings = build_intermediate(
        result.review_queue,
        result.nodes,
        result.edges,
        namespace="<run name>",
        source_doc_ids=[source_sha256],
    )

`build_intermediate` emits ACCEPTED-only statements with GRAPH CLOSURE: an
accepted EDGE whose endpoint node is not also accepted is DROPPED with a warning
(fail-closed -- never emit an edge to an unreviewed node). The output is the
reviewed intermediate bundle dict (`schema_version`, `dataset_namespace`,
`source_doc_ids`, `nodes`, `edges`, `provenance`, `counts`) -- followthemoney-FREE,
with deterministic hashlib ids. On Windows this bundle IS the deliverable.

Route a Librarian findings note with AGGREGATE counts only (node/edge/provenance
counts, not raw spans). Any surfaced entity PII goes through `redact-output`
(Phase 7) before it leaves the box.

## 4. The FtM bundle is a Phase-13 boundary (Linux/Docker)

On Windows the deliverable stops at the reviewed intermediate.
`scripts/entity_ftmize.py` (the `ftm`-marked, Linux/CI-only layer) turns that
intermediate into the FollowTheMoney bundle -- `entities.ftm.json` +
`provenance.jsonl` + `manifest.json`. followthemoney does NOT install on Windows
(it pulls PyICU, which has no Windows wheel), so `entity_ftmize` runs only in the
CI `ftm` job (Ubuntu) and Phase-13 Docker. Phase-13 entity-graph consumes that
bundle (nomenklatura resolution + graph + watchlist cross-ref).

## Honest limits + decisions

- Zero-shot relation F1 ~25-40 -> the human gate is MANDATORY, never autonomous.
- GLiNER zero-shot NER F1 ~48% average; LABEL WORDING is the biggest accuracy
  lever -- hence the config-driven taxonomy + the flock preset.
- The GLiREL weights (`jackboyla/glirel-large-v0`) are CC BY-NC-SA 4.0
  (NON-COMMERCIAL). Adopted + documented: the user downloads the weights; they
  are not vendored. Magpie's MIT code is unaffected.
- PER-DOCUMENT scope. The same name+type in ONE document is ONE node, but
  cross-document homonyms stay DISTINCT. Phase 12 NEVER merges across documents
  -- the first true merge (and homonym disambiguation) is Phase-13 nomenklatura
  resolution. Collapsing homonyms corpus-wide here would be irreversible.
- The PyICU reality: on Windows the deliverable is the reviewed intermediate;
  the FtM bundle is produced on Linux/CI.
- entity-extract is DISTINCT from `pii_sweep`. `pii_sweep` runs spaCy PERSON NER
  to COUNT PII exposure; entity-extract runs GLiNER multi-type NER + GLiREL
  relations to BUILD a network. Different engines, different purpose, NO shared
  code.

## Engine and downstream

- `scripts/entity_extract.py` -- pure core + the `docling_to_extraction_input`
  adapter.
- `scripts/entity_taxonomy.py` -- taxonomy config (generic + flock preset).
- `scripts/entity_models.py` -- the lazy GLiNER/GLiREL edge.
- `scripts/entity_ftmize.py` -- the Linux/CI FtM layer (intermediate -> FtM
  bundle).
- `references/prior-art.md` -- the source-verified GLiNER/GLiREL/FtM API facts.

The trustworthy seam reuses Phase-8 `investigate`'s contract. Output goes via
Librarian (aggregate counts) + `redact-output` for any surfaced PII. No
`.mcp.json` ships.

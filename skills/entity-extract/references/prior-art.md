# Phase 12 entity-extract -- research / prior-art gate (verified facts)

Verified-facts-only reference (the algorithm, contracts, and test plan live in
`docs/plans/2026-06-06-magpie-phase12-entity-extract.md` + the design doc). Every
API fact below was confirmed by reading the INSTALLED package source in this venv
and by an empirical smoke on this box (gitignored `.codex-review/phase12_smoke.py`
+ `phase12_smoke2.py`), not from memory. ASCII only.

## 1. Verified versions + pins held (this venv, Python 3.12.10)

- gliner==0.2.26 (Apache-2.0), model `urchade/gliner_medium-v2.1` (209M params, ~781 MB).
- glirel==1.2.1 (Apache-2.0 package), model `jackboyla/glirel-large-v0` (~1.87 GB;
  WEIGHTS CC BY-NC-SA 4.0 -- non-commercial; adopt + document, see section 12).
- loguru==0.7.3 (glirel imports it but does NOT declare it -- pinned explicitly).
- transformers==4.57.6, typer==0.24.2 (the GLiNER<->Docling coexistence overlap,
  section 11). tokenizers==0.22.2 came along; torch==2.12.0 (CPU) UNTOUCHED.
- spaCy==3.8.14 already present (tokenizer only; we use `spacy.blank("en")`).
- numpy==2.4.6 / pandas==3.0.3 pins UNTOUCHED (gliner/glirel install against them).
- followthemoney==4.9.0 + nomenklatura==4.9.1 (MIT) are LINUX/CI ONLY -- they live in
  `requirements-ftm.txt`, NOT requirements-dev.txt (PyICU has no Windows wheel; section 10).
  Confirmed NOT installed on this Windows venv.

## 2. GLiNER entity API (verified by smoke)

    from gliner import GLiNER
    gm = GLiNER.from_pretrained("urchade/gliner_medium-v2.1")   # lazy first-run download
    ents = gm.predict_entities(text, labels, threshold=0.5)
    # -> [{"text": str, "label": str, "start": int, "end": int, "score": float}, ...]

- `start`/`end` are CHAR offsets into `text`, end EXCLUSIVE
  (e.g. "Mayor Jane Smith" -> start=0, end=16; text[0:16] == the span).
- `label` is echoed verbatim from the input label list (zero-shot; label wording is
  the biggest accuracy lever -- hence the config-driven taxonomy + flock preset).
- `threshold` filters returned spans by score.

## 3. GLiREL relation API (verified by reading glirel/model.py + spacy_integration.py)

Canonical DIRECT call (mirrors glirel's own spaCy component, spacy_integration.py:107-113):

    from glirel import GLiREL
    gr = GLiREL.from_pretrained("jackboyla/glirel-large-v0")
    rels = gr.predict_relations(tokens, labels, threshold=0.0, ner=ner, top_k=1)

- `tokens`: a LIST of token strings (already tokenized). If a str is passed instead,
  glirel re-tokenizes with its OWN regex; pass the LIST so token indices align with `ner`.
- `ner`: `[[start_tok, end_tok_INCLUSIVE, TYPE, surface_text], ...]`. End is INCLUSIVE
  on INPUT (glirel's component builds it as `[ent.start, ent.end - 1, ...]`).
- `labels`: **a FLAT LIST of relation-label strings** (or a dict whose KEYS are the
  relation labels -- glirel does `enumerate(labels)` and only the keys/elements become
  classes; base.py:321 with `fixed_relation_types=True`).
- `top_k`: keep the top-k labels PER ordered entity pair (we use top_k=1).
- Output: a list of dicts, one per scored pair:

      {"head_pos": [start, end_EXCLUSIVE], "tail_pos": [start, end_EXCLUSIVE],
       "head_text": [tok, ...], "tail_text": [tok, ...], "label": str, "score": float}

  NOTE the asymmetry: `head_pos`/`tail_pos` end is EXCLUSIVE on OUTPUT
  (model.py:594 adds +1), while the `ner` INPUT end is inclusive. Map an output
  relation back to an input span by the START token (unique after dedup), or by the
  pair `(start, end_excl - 1)` against the input `(start, end_incl)`.

### CRITICAL correction the smoke caught

The research-gate note `labels={"glirel_labels": {<rel>: {...}}}` is the spaCy-PIPELINE
context shape (`doc._context["glirel_labels"]`), NOT the direct `predict_relations`
argument. Passing that wrapper to `predict_relations` makes `"glirel_labels"` the ONLY
class (dict membership checks keys) -- every relation comes back labelled
`"glirel_labels"` with ~0.01 scores (observed in `phase12_smoke.py`). The fix
(`phase12_smoke2.py`): pass a FLAT LIST of the taxonomy's relation labels plus a
`"no relation"` sentinel; real labels then return.

### Pair-type constraints are a POST-filter, not a model input

glirel's `allowed_head`/`allowed_tail` are applied by `constrain_relations_by_entity_type`
in the spaCy component AFTER prediction (spacy_integration.py:116) -- the model's direct
`predict_relations` scores ALL ordered `ner` pairs regardless. Therefore:
- The pure core owns pair-type filtering via `taxonomy.allowed()` in `make_edge` (one
  place for the rule). The relation extractor returns every predicted relation except
  `"no relation"`; `make_edge` drops type-incompatible pairs.
- CPU tractability comes from WINDOWING + the ~15-20 entity-type cap (fewer entities per
  window -> fewer pairs), NOT from pre-pruning pairs (the direct API has no pair
  allowlist). This refines the design's "pair filter = the load-bearing CPU guard".

## 4. char-offset (GLiNER) <-> inclusive-token (GLiREL) conversion

Use ONE shared `spacy.blank("en")` doc per page (no trained model needed -- tokenizer
only, so we do NOT load en_core_web_lg here):

    nlp = spacy.blank("en"); doc = nlp(page_text)
    tokens = [t.text for t in doc]
    sp = doc.char_span(span.char_start, span.char_end, alignment_mode="expand")
    if sp is None: continue            # misaligned span -> skip for relations
    ner_entry = [sp.start, sp.end - 1, span.label.upper(), span.text]   # end INCLUSIVE

Building `tokens` and `ner` from the SAME doc keeps indices consistent, and output
token positions round-trip back to char spans for provenance. This char<->token glue
lives in `scripts/entity_models.py` so the pure core stays spaCy-free.

## 5. Measured CPU latency (this box, 31.6 GB RAM, CPU-only)

- First run downloads ~2.6 GB of weights (781 MB GLiNER + 1.87 GB GLiREL), lazily.
- WARM (cached) model load: GLiNER ~3.1 s, GLiREL ~3.5 s (the 83.6 s seen on the
  first run was the GLiREL DOWNLOAD, not load). Load ONCE per process -- never reload
  per page or per document.
- Per-page inference (199-char paragraph, 6 entities, 37 tokens, 30 ordered pairs):
  GLiNER predict ~0.15-0.20 s; GLiREL predict ~0.42-0.50 s. Sub-second per short page
  warm. Relation cost grows with pairs (~quadratic in entities/page) -- bounded by
  windowing + the entity cap. Acceptable for laptop-local + a mandatory human gate.

## 6. Empirical relation-quality finding (the human-gate justification)

On the corrected smoke (a clean, unambiguous sentence) GLiREL at top_k=1:
- assigned `"no relation"` to most pairs with HIGH confidence (0.97-0.99 for the
  obvious non-relations like money<->date);
- scored the GENUINE relations LOW (0.10-0.32, e.g. "employed by" for
  official->agency); and
- did NOT surface the actually-correct agency<->vendor "party to contract" at top_k=1.

This is concrete proof of the documented zero-shot RE F1 ~25-40: the relations are
noisy and under-confident. Consequences baked into the build:
- The relation extractor DROPS `"no relation"` results.
- A LOW relation threshold is required to surface candidates at all (the human gate,
  not the threshold, is the precision filter). Entities tolerate a higher threshold
  (~0.4-0.5) than relations (~0.2). The skill documents this; `extract()` keeps a
  single `threshold` param for v1 (the human gate is the real filter).
- NOTHING flows downstream until a person clears it (design 7).

## 7. FtM 4.x mapping API (ftmize layer only; Linux/CI)

    from followthemoney import model
    e = model.make_entity("Person"); e.id = node_id     # reuse the intermediate's stable_id
    e.add("name", "John Smith"); data = e.to_dict()     # -> {id, schema, properties}
    proxy = model.get_proxy(data)                        # round-trip / validate

Edge entities (FtM "interval" entities) + their endpoint property names:
- Employment(employee, employer), Membership(member, organization),
  Directorship(director, organization), Ownership(owner, asset),
  Representation(agent, client), Family(person, relative),
  Associate(person, associate), ContractAward(authority, supplier),
  UnknownLink(subject, object)  <- the deterministic catch-all fallback.
All inherit Interval (sourceUrl, proof->Document, startDate, endDate, recordId,
summary). Char-level provenance lives in the sidecar, not in FtM props.

## 8. ftm export CLI + nomenklatura (ftmize contract tests; Linux/CI)

ftm console scripts (followthemoney) operate on a newline-delimited entity stream
(one `to_dict()` per line) on stdin:
- `ftm validate` -- normalize / drop invalid property values.
- `ftm export-cypher -e <type> [-e <type> ...]` -- emit a Cypher script for Neo4j
  (e.g. `cat entities.ijson | ftm export-cypher -e name -e address`).
- `ftm export-neo4j-bulk -o <dir> -e <type> ...` -- emit CSVs + a `neo4j-admin import`
  shell script (needs a stopped, empty DB to actually load -- the CONTRACT test only
  asserts the files are produced, no server).

nomenklatura 4.9.x (resolution; Phase-13 engine, smoke-tested here): `Dataset`,
`Store` (file/SQLite via `NOMENKLATURA_DB_URL`, or in-memory), `blocker.Index`
(candidate generation), `Resolver` (judgement graph; `decide()` / `suggest()`;
`Judgement` POSITIVE/NEGATIVE/UNSURE/NO_JUDGEMENT). CLI: `nomenklatura xref
entities.ijson`, `nomenklatura dedupe`, `nomenklatura apply`. The exact 4.9.1 store/
xref signatures will be pinned via Context7 + the installed CI source when Task 7 runs
on Linux (they cannot be exercised on Windows -- PyICU). The contract smoke loads the
bundle, runs an xref pass, and asserts the two same-name-different-doc nodes appear as
resolution candidates (proving Phase 12 left them DISTINCT for Phase 13 to merge).

## 9. Windowing implication

GLiNER has a ~384-token window; long pages are split into overlapping CHAR windows
(`plan_windows`, max 1400 chars / 200 overlap, whitespace-boundary) with each window's
char base tracked so spans re-offset to page coordinates. Boundary entities can
split/duplicate across windows -> `dedup_spans` merges same-label overlaps (keep longer,
then higher score); distinct-label overlaps are both kept.

## 10. PyICU / Windows decouple (the architecture-shaping finding)

`followthemoney` -> `normality` -> `PyICU`, and PyICU has NO Windows wheel (needs ICU
built from source). So the OpenSanctions/FtM stack does not pip-install on the Windows
venv. Decision (Tim, 2026-06-06): the Windows-native PURE CORE
(`entity_taxonomy.py` + `entity_extract.py`) is followthemoney-FREE -- it emits a
plain-JSON reviewed INTERMEDIATE with deterministic hashlib IDs. `entity_ftmize.py`
is the ONLY followthemoney importer (`ftm`-marked: skips when followthemoney is absent),
turning the intermediate into the FtM bundle in CI (Ubuntu) and Phase-13 Docker. The
intermediate OWNS the IDs; ftmize sets `proxy.id` to them, so make_id is never a
cross-platform dependency.

## 11. transformers / typer / loguru coexistence

GLiNER needs transformers >=4.51.3,<5.2; docling-ibm-models excludes 5.0/5.1/5.2/5.3;
docling-core needs typer <0.25. A fresh `pip install gliner` resolves transformers
5.1.x (gliner's default), which BREAKS a `pip install -r requirements-dev.txt`
(docling-ibm-models rejects 5.1). The clean overlap -- pinned in requirements-dev.txt --
is transformers==4.57.6 + typer==0.24.2: `pip check` clean, the docling heavy suite
passes, gliner/glirel import. loguru==0.7.3 is pinned because glirel imports but does
not declare it. Do NOT let transformers drift off 4.57.6.

## 12. GLiREL weights license (CC BY-NC-SA, adopt + document)

The glirel PACKAGE is Apache-2.0 but the `jackboyla/glirel-large-v0` WEIGHTS are
CC BY-NC-SA 4.0 (non-commercial, share-alike). Tim's call (2026-06-05): ADOPT and
DOCUMENT -- the user downloads the weights (not vendored, not bundled-as-binary),
Magpie's MIT code is unaffected (the Phase-7 AGPL/PyMuPDF posture applied to weights).
The non-commercial restriction is surfaced in setup/doctor, this prior-art, and the
release notes.

## 13. HF symlink / Windows Developer Mode

A FRESH HuggingFace weights download (gliner/glirel) calls `os.symlink` in the HF cache,
which raises `OSError WinError 1314` on Windows WITHOUT Developer Mode. Developer Mode
is ON on this box (Tim, 2026-06-06), so fresh downloads + the `gliner`-marked tests run
locally. The `@gliner` tests still SKIP on model-load failure (portability +
CI-without-models).

## 14. Decoupling

entity-extract shares NO code with the Track-A modules (stats / load_table / derive /
recipe / pii_sweep) or with Track-A document ingest beyond two spine seams: it REFUSES a
non-trustworthy `ingest` doc (the Phase-8 `trustworthy_for_extraction` contract) and any
surfaced entity PII routes through `redact-output`. Distinct from `pii_sweep` (spaCy
PERSON NER to COUNT PII exposure): entity-extract does GLiNER multi-type NER + GLiREL
relations to BUILD a network. Different engines, different purpose, no shared code.

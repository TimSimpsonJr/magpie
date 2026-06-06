# Magpie v0.1.0 -- Release Notes

Magpie v0.1.0 is the Layer 0-1 cut: laptop-local, no Docker. It is an
investigative analysis toolkit for structured FOIA/data work plus document
ingest, redaction checking, and verifiable findings. Everything runs on a
single machine against the pure-Python engine, with a read-only served database
for interactive querying.

## What ships

### The 10 skills

Magpie exposes its capabilities as ten Claude Code skills over the engine:

- `dataset-analyze` -- deterministic descriptive statistics over a table.
- `analysis-recipe` -- the repeatable multi-point analysis recipe + cross-source
  rollup.
- `pii-sweep` -- spaCy-backed scan for person names and structured PII in
  free text.
- `ingest` -- Docling/RapidOCR document ingest (PDF/scanned -> text + anchors).
- `redaction-check` -- verify that received material is redacted as expected.
- `redact-output` -- emit typed-placeholder redactions of your own output.
- `investigate` -- citation-anchor resolution against ingested source documents.
- `archive-evidence` -- provenance / chain-of-custody record for findings.
- `setup` -- guided onboarding probe that wires the environment.
- `doctor` -- capability/health check of the local install.

### The engine surface

The skills are thin wrappers over a pure-Python engine. The committed engine
modules are: `stats`, `load_table`, `data_quality`, `derive`, `recipe`,
`rollup`, `pii_sweep`, `ingest_gate` + `ingest`, `redaction_check`,
`redact_output`, `citation`, `evidence`, `detect_tier`, and
`build_dataset_db`. Heavy dependencies (spaCy, Docling, torch) are imported
lazily, so the offline path stays light.

### Served database

A bundled, read-only `mcp-sqlite` served database exposes a built dataset as
canned-query tools for interactive exploration. The served database is built
from an allowlist of columns, so it never re-exposes raw PII-bearing columns.

### Dependency

Magpie has a hard dependency on the `librarian` plugin.

## Validation

- Env-gated golden tests pin the documented Simpsonville pilot values against the
  real (private, never-committed) corpus. They skip cleanly when the corpus env
  var is unset.
- CI runs the offline subset plus the structural/install smoke and the
  `mcp-sqlite` served-DB start smoke on every push and pull request.
- A `workflow_dispatch`-gated heavy job covers the docling/spacy/xray paths
  (the ~2 GB model stack) on demand.

## Deferred (NOT in v0.1.0)

The following are explicitly out of scope for this release:

- Track B (entity-network analysis).
- Layer 2 (any tier above laptop-local).
- Any Docker infrastructure.
- `foia-request`.

## Public corpus status

Stated honestly: the public sample corpus may be PARTIAL at tag time.

- The Skokie PD Flock FOIA cover-letter PDF ships if it passes PII vetting in
  time for the tag.
- The authentic RANGE Media Spokane County Flock-audit slice lands as a
  fast-follow (or v0.1.1) once two conditions are met: re-host permission is
  recorded, and the slice passes a clean `pii-sweep`.

v0.1.0 does NOT claim the corpus is fully shipped. The provenance datasheet
(`corpus/public/DATASHEET.md`) records the exact status, source, attribution,
permission posture, and sha256 of each bundled artifact.

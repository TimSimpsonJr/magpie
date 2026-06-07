"""Task 10: gliner-marked end-to-end integration + env-gated real-doc smoke.

Two gliner-marked tests:

  * ``test_real_extractor_e2e_builds_wellformed_intermediate`` -- runs the REAL
    GLiNER/GLiREL extractors over a tiny synthetic doc through the full pure-core
    pipeline (extract -> auto-accept -> build_intermediate) and asserts the
    intermediate bundle is well-formed and graph-closed. Weights are cached on
    this box, so it RUNS here; it stays portable by skipping on any model
    load/predict Exception (CI-without-models), mirroring tests/test_entity_models.py.
  * ``test_greenville_flock_rfp_smoke`` -- the FULL Phase-6 -> Phase-12 path over
    the real (private, NEVER committed) Greenville Flock/ALPR RFP. SKIPS unless
    MAGPIE_PHASE8_REAL_PDF points at an existing file, so it is a no-op on this
    box and only Tim / CI-with-corpus exercises it.

Both are HONEST about zero-shot RE: entities are reliable, but GLiREL at
top_k=1 is noisy/under-confident (F1 ~25-40 -- prior-art sections 5, 6), so
NEITHER test hard-requires a specific relation or any edge count. They only
assert graph closure on whatever edges DO surface.

ASCII only.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

import pytest


# ---------------------------------------------------------------------------
# Test 1 -- @gliner end-to-end over a synthetic doc (RUNS on this box)
# ---------------------------------------------------------------------------

_SYNTHETIC_TEXT = (
    "The Greenville Police Department signed a five-year contract with "
    "Flock Safety, Inc. Chief John Doe approved the agreement for automated "
    "license plate readers."
)


@pytest.mark.gliner
def test_real_extractor_e2e_builds_wellformed_intermediate():
    """Real extractors -> extract -> auto-accept -> build_intermediate.

    Asserts the intermediate bundle is well-formed and graph-closed. Robust to
    zero-shot RE noise: never hard-requires a specific relation or edge count.
    Skips on any model load/predict Exception so the suite stays portable.
    """
    from scripts.entity_extract import build_intermediate, extract
    from scripts.entity_models import GlinerEntityExtractor, GlirelRelationExtractor
    from scripts.entity_taxonomy import resolve

    doc = {
        "doc_id": "synthetic-1",
        "trustworthy_for_extraction": True,
        "pages": [{"page_no": 1, "text": _SYNTHETIC_TEXT}],
    }

    # Construct extractors + run the first (model-touching) call inside the
    # try/except so a missing/uncached weight set -> skip, not fail.
    try:
        result = extract(
            doc,
            taxonomy=resolve("generic"),
            namespace="t",
            entity_extractor=GlinerEntityExtractor(),
            relation_extractor=GlirelRelationExtractor(),
            threshold=0.3,
        )
    except Exception as exc:  # pragma: no cover - portability / CI-without-models
        pytest.skip("GLiNER/GLiREL unavailable: %r" % (exc,))

    # Input gate must have passed; GLiNER reliably finds the agency/company.
    assert result.refused is False
    assert result.nodes, "expected GLiNER to surface at least one entity node"

    # Auto-accept every pending statement (stands in for the human review gate).
    for s in result.review_queue.pending():
        result.review_queue.decide(s.statement_id, "accepted", reviewer="test")

    bundle, warnings = build_intermediate(
        result.review_queue,
        result.nodes,
        result.edges,
        namespace="t",
        source_doc_ids=["synthetic-1"],
    )

    # Well-formed bundle.
    assert bundle["schema_version"]
    assert bundle["nodes"], "expected accepted nodes in the bundle"

    # CLOSURE: every edge endpoint is an id present in the bundle's nodes.
    node_ids = {n["id"] for n in bundle["nodes"]}
    for e in bundle["edges"]:
        assert e["head_id"] in node_ids, "edge head not in bundle nodes"
        assert e["tail_id"] in node_ids, "edge tail not in bundle nodes"

    # Counts are consistent with the materialized lists.
    assert bundle["counts"]["nodes"] == len(bundle["nodes"])
    assert bundle["counts"]["edges"] == len(bundle["edges"])

    # Provenance: one row per accepted statement, every row reviewed.
    accepted = result.review_queue.accepted()
    assert len(bundle["provenance"]) == len(accepted)
    assert bundle["counts"]["provenance"] == len(bundle["provenance"])
    for row in bundle["provenance"]:
        assert row["reviewed"] is True

    # One-line CI-log summary of what surfaced.
    print(
        "E2E-SYNTHETIC nodes=%d edges=%d provenance=%d warnings=%d"
        % (
            len(bundle["nodes"]),
            len(bundle["edges"]),
            len(bundle["provenance"]),
            len(warnings),
        )
    )


# ---------------------------------------------------------------------------
# Test 2 -- env-gated real-document smoke (SKIPS on this box; Tim/CI runs it)
# ---------------------------------------------------------------------------

@pytest.mark.gliner
def test_greenville_flock_rfp_smoke(tmp_path):
    """Full Phase-6 -> Phase-12 path over the real Greenville Flock/ALPR RFP.

    The RFP is private and NEVER committed. This test SKIPS unless
    MAGPIE_PHASE8_REAL_PDF points at an existing file, so it is a no-op on this
    box and only runs where Tim / CI has the corpus.
    """
    pdf = os.environ.get("MAGPIE_PHASE8_REAL_PDF")
    if not pdf or not pathlib.Path(pdf).exists():
        pytest.skip("MAGPIE_PHASE8_REAL_PDF not set or file missing")

    try:
        from scripts.ingest import ingest  # Phase-6 docling edge
    except Exception as exc:  # pragma: no cover - docling optional
        pytest.skip("docling/ingest unavailable: %r" % (exc,))

    from scripts.entity_extract import docling_to_extraction_input, extract
    from scripts.entity_models import GlinerEntityExtractor, GlirelRelationExtractor
    from scripts.entity_taxonomy import resolve

    res = ingest(pdf, out_dir=str(tmp_path))
    if not res.trustworthy_for_extraction:
        pytest.skip(
            "ingest deemed the RFP non-trustworthy: %s" % res.doc_decision
        )

    docling_json = json.loads(
        pathlib.Path(res.docling_json_path).read_text(encoding="utf-8")
    )
    doc = docling_to_extraction_input(
        docling_json,
        doc_id=res.source_sha256,
        trustworthy_for_extraction=res.trustworthy_for_extraction,
    )

    t0 = time.perf_counter()
    result = extract(
        doc,
        taxonomy=resolve("flock"),
        namespace="greenville_rfp",
        entity_extractor=GlinerEntityExtractor(),
        relation_extractor=GlirelRelationExtractor(),
        threshold=0.3,
    )
    elapsed = time.perf_counter() - t0

    # Entities are reliable: the network should surface at least one of the
    # agency/vendor/official-style labels on a Flock/ALPR procurement RFP.
    assert result.refused is False
    labels = {n.label for n in result.nodes}
    expected = {
        "government agency",
        "company",
        "government official",
        "organization",
        "person",
    }
    assert labels & expected, (
        "expected an agency/vendor/official-style entity; got labels=%r"
        % (sorted(labels),)
    )

    # HONEST on relations: zero-shot RE F1 ~25-40 and the correct relation may
    # not surface at top_k=1 (prior-art section 6), so a procurement /
    # data-sharing edge assertion here would be a flaky false-red. Instead, if
    # any edges DID surface, assert each endpoint is a real node id (closure on
    # the live result); otherwise just record edges=0.
    node_ids = {n.id for n in result.nodes}
    for e in result.edges:
        assert e.head_id in node_ids, "edge head not a live node id"
        assert e.tail_id in node_ids, "edge tail not a live node id"

    print(
        "REAL-DOC latency=%.1fs pages=%d nodes=%d edges=%d"
        % (elapsed, len(doc["pages"]), len(result.nodes), len(result.edges))
    )

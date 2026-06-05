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

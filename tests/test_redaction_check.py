"""TDD for scripts.redaction_check -- the OFFLINE tier of the Phase 7
``redaction-check`` skill (schema + 6 pikepdf/byte checks; NO x-ray).

This tier must NEVER import PyMuPDF / fitz: importing ``scripts.redaction_check``
and running any of these checks loads only stdlib + pikepdf. The x-ray
box-over-text check (``@pytest.mark.xray``), the text_layer check, and the
orchestrator are a LATER dispatch and are not exercised here.

Every fixture is SYNTHETIC, built into ``tmp_path`` (see
tests/conftest_redaction.py); no real corpus is touched.

The load-bearing safety invariant under test: a raw recovered/leaked STRING
(metadata value, AcroForm /V, annotation /Contents, embedded filename) lives ONLY
in a finding's ``local_evidence`` and NEVER in ``detail`` -- so ``detail`` (which
publishes) carries no third-party PII.
"""
import pytest


# --------------------------------------------------------------------------- #
# 3a. Schema + publishable_view.
# --------------------------------------------------------------------------- #


def test_publishable_view_drops_local_evidence_and_asserts_clean_detail():
    from scripts.redaction_check import RedactionFinding, RedactionReport

    f = RedactionFinding(
        check="metadata",
        severity="low",
        page=None,
        summary="author present",
        detail={"fields": ["Author"]},
        local_evidence={"Author": "Jane Author"},
    )
    rep = RedactionReport(
        source_path="x.pdf",
        source_sha256="ab",
        mode="received",
        checks_run=["metadata"],
        checks_unavailable=[],
        findings=[f],
        n_findings=1,
        safe_to_publish=None,
        warnings=[],
        cannot_catch=["pixelation"],
    )
    pub = rep.publishable_view()
    assert pub["findings"][0]["local_evidence"] is None  # dropped
    assert "Jane Author" not in str(pub)  # raw value gone
    assert pub["findings"][0]["detail"] == {"fields": ["Author"]}


def test_to_dict_is_jsonable_and_keeps_local_evidence():
    import json

    from scripts.redaction_check import RedactionFinding, RedactionReport

    f = RedactionFinding(
        check="metadata",
        severity="low",
        page=None,
        summary="author present",
        detail={"fields": ["Author"]},
        local_evidence={"Author": "Jane Author"},
    )
    rep = RedactionReport(
        source_path="x.pdf",
        source_sha256="ab",
        mode="received",
        checks_run=["metadata"],
        checks_unavailable=[],
        findings=[f],
        n_findings=1,
        safe_to_publish=None,
        warnings=[],
        cannot_catch=["pixelation"],
    )
    # to_dict() is the LOCAL report: it KEEPS local_evidence (publishable_view
    # is the one that strips it).
    d = json.loads(json.dumps(rep.to_dict()))
    assert d["findings"][0]["local_evidence"] == {"Author": "Jane Author"}
    assert d["n_findings"] == 1


def test_publishable_view_raises_if_detail_carries_raw_evidence():
    # Defensive schema check: local_evidence is the ONLY raw carrier. If a finding
    # smuggles a raw string into detail under a value that ALSO appears in
    # local_evidence, publishable_view must refuse (never silently publish it).
    from scripts.redaction_check import RedactionFinding, RedactionReport

    bad = RedactionFinding(
        check="metadata",
        severity="low",
        page=None,
        summary="author present",
        detail={"leaked": "Jane Author"},  # raw value smuggled into detail
        local_evidence={"Author": "Jane Author"},
    )
    rep = RedactionReport(
        source_path="x.pdf",
        source_sha256="ab",
        mode="received",
        checks_run=["metadata"],
        checks_unavailable=[],
        findings=[bad],
        n_findings=1,
        safe_to_publish=None,
        warnings=[],
        cannot_catch=[],
    )
    with pytest.raises(AssertionError):
        rep.publishable_view()


# --------------------------------------------------------------------------- #
# 3b. incremental_save check (pure bytes).
# --------------------------------------------------------------------------- #


def test_check_incremental_save_flags_second_revision(incremental_save_pdf):
    from scripts.redaction_check import check_incremental_save

    findings = check_incremental_save(incremental_save_pdf)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "incremental_save"
    assert f.severity == "medium"
    assert f.page is None
    # counts are publishable ints -> they live in detail
    assert f.detail["eof_count"] == 2
    assert f.detail["startxref_count"] == 2
    # lead framing in the summary, never a verdict
    assert "revision" in f.summary.lower()


def test_check_incremental_save_clean_single_rev_no_finding(clean_single_rev_pdf):
    from scripts.redaction_check import check_incremental_save

    assert check_incremental_save(clean_single_rev_pdf) == []

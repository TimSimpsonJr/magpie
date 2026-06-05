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
import sys

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


def test_publishable_view_raises_if_summary_carries_raw_evidence():
    # The summary is ALSO a published field: a raw local_evidence string smuggled
    # into the human-readable summary must be refused too (not just detail).
    from scripts.redaction_check import RedactionFinding, RedactionReport

    bad = RedactionFinding(
        check="metadata",
        severity="low",
        page=None,
        summary="author Jane Author present",  # raw value smuggled into summary
        detail={"fields": ["/Author"]},
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


# --------------------------------------------------------------------------- #
# 3c. metadata check (pikepdf docinfo + XMP).
# --------------------------------------------------------------------------- #


def test_check_metadata_flags_author_title_value_local_not_detail(metadata_pdf):
    from scripts.redaction_check import check_metadata

    findings = check_metadata(metadata_pdf)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "metadata"
    assert f.page is None
    # detail lists field NAMES only -- never the raw values.
    detail_blob = str(f.detail)
    assert "Jane Author" not in detail_blob
    assert "Internal Draft" not in detail_blob
    # the present field names ARE in detail (publishable facts).
    fields = f.detail["fields"]
    assert any("Author" in name or "creator" in name for name in fields)
    # raw VALUES live ONLY in local_evidence.
    raw_blob = str(f.local_evidence)
    assert "Jane Author" in raw_blob
    assert "Internal Draft" in raw_blob


def test_check_metadata_clean_pdf_minimal_or_no_leak(clean_pdf):
    # fpdf2 stamps a /Producer (the fpdf2 version) but no author/title/creator
    # name. The check may surface the producer as a (low) lead, but must NEVER
    # invent an author/title leak. If it fires, the author/title fields are absent
    # and no human name string is present.
    from scripts.redaction_check import check_metadata

    findings = check_metadata(clean_pdf)
    for f in findings:
        assert "Jane Author" not in str(f.local_evidence)


# --------------------------------------------------------------------------- #
# 3d. unapplied_redact check (pikepdf /Annots /Subtype /Redact).
# --------------------------------------------------------------------------- #


def test_check_unapplied_redact_flags_redact_annot(redact_annot_pdf):
    from scripts.redaction_check import check_unapplied_redact

    findings = check_unapplied_redact(redact_annot_pdf)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "unapplied_redact"
    assert f.page == 1  # 1-based page number
    # a /Redact annot that was marked but never applied -> underlying text present
    assert "redact" in f.summary.lower()


def test_check_unapplied_redact_clean_pdf_no_finding(clean_pdf):
    from scripts.redaction_check import check_unapplied_redact

    assert check_unapplied_redact(clean_pdf) == []


# --------------------------------------------------------------------------- #
# 3e. embedded_files check (pikepdf attachments + /Names + /AF + /FileAttachment).
# --------------------------------------------------------------------------- #


def test_check_embedded_files_name_local_count_size_detail(embedded_file_pdf):
    from scripts.redaction_check import check_embedded_files

    findings = check_embedded_files(embedded_file_pdf)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "embedded_files"
    # the FILENAME can itself leak PII (e.g. John_Doe_DOB.xlsx) -> local_evidence.
    assert "hidden_notes.txt" in str(f.local_evidence)
    # detail carries ONLY non-string facts: count + byte sizes. NEVER the name,
    # NEVER the bytes.
    assert "hidden_notes.txt" not in str(f.detail)
    assert "internal only" not in str(f.detail)
    assert f.detail["count"] == 1
    assert f.detail["sizes"] == [len(b"internal only")]


def test_check_embedded_files_clean_pdf_no_finding(clean_pdf):
    from scripts.redaction_check import check_embedded_files

    assert check_embedded_files(clean_pdf) == []


# --------------------------------------------------------------------------- #
# 3f. acroform_values check (pikepdf Root/AcroForm/Fields /V).
# --------------------------------------------------------------------------- #


def test_check_acroform_values_value_local_name_detail(acroform_pdf):
    from scripts.redaction_check import check_acroform_values

    findings = check_acroform_values(acroform_pdf)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "acroform_values"
    # the /V value should have been redacted -> it is a raw leak -> local_evidence.
    assert "123-45-6789" in str(f.local_evidence)
    # detail carries the field NAME only -- never the value.
    assert "123-45-6789" not in str(f.detail)
    assert "ssn_field" in str(f.detail["fields"])


def test_check_acroform_values_clean_pdf_no_finding(clean_pdf):
    from scripts.redaction_check import check_acroform_values

    assert check_acroform_values(clean_pdf) == []


# --------------------------------------------------------------------------- #
# 3g. annotation_text check (pikepdf comment annots /Contents).
# --------------------------------------------------------------------------- #


def test_check_annotation_text_contents_local_not_detail(annotation_text_pdf):
    from scripts.redaction_check import check_annotation_text

    findings = check_annotation_text(annotation_text_pdf)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "annotation_text"
    assert f.page == 1
    # the /Contents reviewer note is a raw leak -> local_evidence only.
    assert "John Doe" in str(f.local_evidence)
    assert "reviewer note" in str(f.local_evidence)
    # detail must NOT carry the raw comment text.
    assert "John Doe" not in str(f.detail)
    assert "reviewer note" not in str(f.detail)


def test_check_annotation_text_clean_pdf_no_finding(clean_pdf):
    from scripts.redaction_check import check_annotation_text

    assert check_annotation_text(clean_pdf) == []


def test_check_annotation_text_ignores_redact_annot(redact_annot_pdf):
    # a /Redact annot is NOT a comment-type annot and carries no /Contents here;
    # the comment-text check must not fire on it (that is unapplied_redact's job).
    from scripts.redaction_check import check_annotation_text

    assert check_annotation_text(redact_annot_pdf) == []


# --------------------------------------------------------------------------- #
# 4. box_over_text check (x-ray lazy edge -> PyMuPDF). The ONLY xray-marked path.
#    The OFFLINE degrade test must NOT load x-ray.
# --------------------------------------------------------------------------- #


@pytest.mark.xray
def test_box_over_text_flags_bad_redaction(bad_redaction_pdf, clean_pdf):
    from scripts.redaction_check import check_box_over_text

    hits = check_box_over_text(bad_redaction_pdf)
    assert hits and hits[0].page == 1
    assert hits[0].check == "box_over_text"
    # the recovered under-box text is LOCAL-ONLY (local_evidence), never published.
    assert "John Q Public" in (hits[0].local_evidence or {}).get("text", "")
    # detail carries publishable facts (bbox + origin), NEVER the recovered text.
    assert "John Q Public" not in str(hits[0].detail)
    # a clean control (text not covered) returns no finding.
    assert check_box_over_text(clean_pdf) == []


def test_box_over_text_degrades_when_xray_missing(monkeypatch, clean_pdf):
    # DEGRADE-DON'T-CRASH and DON'T-FALSE-CLEAN: when ``import xray`` fails, the
    # check must raise the CheckUnavailable sentinel (the orchestrator turns it
    # into a checks_unavailable entry), NOT crash and NOT return [] (a false clean).
    # OFFLINE: forcing sys.modules["xray"] = None makes ``import xray`` raise
    # ImportError without ever loading PyMuPDF.
    from scripts.redaction_check import CheckUnavailable, check_box_over_text

    monkeypatch.setitem(sys.modules, "xray", None)
    with pytest.raises(CheckUnavailable):
        check_box_over_text(clean_pdf)


# --------------------------------------------------------------------------- #
# 3h. text_layer check (pdfminer) -- PAGE-LEVEL co-occurrence ONLY (design 1.1
#     check 2 / 1.4). A finding ONLY when a page co-occurs with a redaction
#     signal (signal_pages, from /Redact or box_over_text) AND has extractable
#     text -- NEVER a standalone "text exists => bad" alarm. No cross-engine bbox.
# --------------------------------------------------------------------------- #


def test_text_layer_fires_on_signal_page_with_text(redact_annot_text_pdf):
    # trigger (i): page 1 carries a /Redact signal (orchestrator passes it in via
    # signal_pages) AND extractable text -> a co-occurrence finding.
    from scripts.redaction_check import check_text_layer

    findings = check_text_layer(redact_annot_text_pdf, signal_pages={1})
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "text_layer"
    assert f.page == 1
    # the char count is a publishable fact -> detail; raw extracted text (if any)
    # is local-only.
    assert f.detail["char_count"] > 0
    assert "hidden secret text here" not in str(f.detail)


def test_text_layer_no_finding_without_co_occurrence(clean_pdf):
    # the standalone-false-positive guard: a plain PDF full of legitimate
    # extractable text but NO redaction signal (signal_pages empty) -> NO finding.
    from scripts.redaction_check import check_text_layer

    assert check_text_layer(clean_pdf, signal_pages=set()) == []


def test_text_layer_no_finding_on_signal_page_without_text(redact_annot_pdf):
    # a /Redact-signalled page that has NO extractable text (blank page + annot)
    # -> NO text_layer finding (there is nothing in the text layer to recover).
    from scripts.redaction_check import check_text_layer

    assert check_text_layer(redact_annot_pdf, signal_pages={1}) == []


@pytest.mark.xray
def test_text_layer_corroborates_box_over_text(bad_redaction_pdf):
    # trigger (ii): the box_over_text corroboration path. The orchestrator finds an
    # x-ray box on page 1 (a signal page) and pdfminer extracts text there -> a
    # text_layer corroboration finding. Page-level co-occurrence (NOT bbox math):
    # here we simulate the orchestrator by computing signal_pages from x-ray.
    from scripts.redaction_check import (
        check_box_over_text,
        check_text_layer,
    )

    box_hits = check_box_over_text(bad_redaction_pdf)
    signal_pages = {h.page for h in box_hits if h.page is not None}
    assert 1 in signal_pages  # x-ray flagged page 1
    findings = check_text_layer(bad_redaction_pdf, signal_pages=signal_pages)
    assert any(f.page == 1 and f.check == "text_layer" for f in findings)


# --------------------------------------------------------------------------- #
# 3i. Orchestrator check_redactions -- run order + signal_pages wiring + the
#     pinned pre-publish severity map + the FAIL-CLOSED safe_to_publish gate.
#     The offline orchestrator tests STUB check_box_over_text so they stay
#     PyMuPDF-free (the real x-ray path is covered by the xray-marked tests).
# --------------------------------------------------------------------------- #


def _stub_box_over_text_empty(monkeypatch):
    """Stub the x-ray edge to a no-op (ran, no findings) so an offline orchestrator
    test does not load PyMuPDF. The check still appears in checks_run."""
    import scripts.redaction_check as r

    monkeypatch.setattr(r, "check_box_over_text", lambda path: [])


def test_orchestrator_prepublish_severity_equals_pinned_map(
    monkeypatch, multi_finding_pdf
):
    # pre-publish mode: EVERY finding's severity must equal _PREPUBLISH_SEVERITY
    # for its check (the gate cannot be silently weakened without tripping this).
    from scripts.redaction_check import _PREPUBLISH_SEVERITY, check_redactions

    _stub_box_over_text_empty(monkeypatch)
    rep = check_redactions(multi_finding_pdf, mode="pre-publish")
    assert rep.findings  # the fixture trips several checks
    for f in rep.findings:
        assert f.severity == _PREPUBLISH_SEVERITY[f.check], f.check
    # the multi-finding fixture trips checks that expose CONTENT (high) -> blocked.
    assert rep.safe_to_publish is False


def test_orchestrator_publishable_view_has_no_raw_string(
    monkeypatch, multi_finding_pdf
):
    # the end-to-end never-publish-raw guard: across ALL findings, the published
    # view drops every local_evidence and carries NO raw leaked string anywhere.
    from scripts.redaction_check import check_redactions

    _stub_box_over_text_empty(monkeypatch)
    rep = check_redactions(multi_finding_pdf, mode="pre-publish")
    pub = rep.publishable_view()
    blob = str(pub)
    # the RAW leaked strings each check surfaced (metadata author, embedded
    # filename, AcroForm /V, the comment /Contents PII) -- none may survive.
    for raw in (
        "Jane Author",
        "hidden_notes.txt",
        "123-45-6789",
        "John Doe",
        "DOB on file",
    ):
        assert raw not in blob, raw
    # every finding's local_evidence is dropped in the published view.
    assert all(fd["local_evidence"] is None for fd in pub["findings"])


def test_orchestrator_high_finding_blocks_publish(monkeypatch, redact_annot_pdf):
    # a single high-severity finding (unapplied_redact) -> safe_to_publish False.
    from scripts.redaction_check import check_redactions

    _stub_box_over_text_empty(monkeypatch)
    rep = check_redactions(redact_annot_pdf, mode="pre-publish")
    assert any(f.check == "unapplied_redact" for f in rep.findings)
    assert rep.safe_to_publish is False


def test_orchestrator_metadata_only_is_publishable(monkeypatch, metadata_pdf):
    # a fixture whose ONLY finding is metadata (medium) -> medium does NOT block,
    # and with every check having run, safe_to_publish is True.
    from scripts.redaction_check import check_redactions

    _stub_box_over_text_empty(monkeypatch)
    rep = check_redactions(metadata_pdf, mode="pre-publish")
    checks = {f.check for f in rep.findings}
    assert checks == {"metadata"}  # the metadata_pdf trips metadata only
    assert rep.checks_unavailable == []
    assert rep.safe_to_publish is True


def test_orchestrator_fail_closed_when_check_unavailable(
    monkeypatch, metadata_pdf
):
    # FAIL-CLOSED: force a check unavailable in pre-publish (box_over_text raises
    # CheckUnavailable). Even with ZERO high findings, safe_to_publish must be
    # False, with a "cannot certify" warning naming the un-run check.
    import scripts.redaction_check as r
    from scripts.redaction_check import CheckUnavailable, check_redactions

    def _boom(path):
        raise CheckUnavailable("box_over_text: forced for test")

    monkeypatch.setattr(r, "check_box_over_text", _boom)
    rep = check_redactions(metadata_pdf, mode="pre-publish")
    # the only real finding is metadata (medium) -> no high findings present.
    assert all(f.severity != "high" for f in rep.findings)
    # ...yet the gate fails CLOSED because a check did not run.
    assert rep.safe_to_publish is False
    assert any("box_over_text" in u for u in rep.checks_unavailable)
    assert any("cannot certify" in w for w in rep.warnings)


def test_orchestrator_received_mode_safe_to_publish_is_none(
    monkeypatch, redact_annot_pdf
):
    # received mode: no pass/fail disposition -> safe_to_publish is None even with
    # a finding present; cannot_catch is still populated (the honesty footer).
    from scripts.redaction_check import check_redactions

    _stub_box_over_text_empty(monkeypatch)
    rep = check_redactions(redact_annot_pdf, mode="received")
    assert rep.safe_to_publish is None
    assert rep.cannot_catch  # honesty footer always present
    assert rep.source_sha256 and len(rep.source_sha256) == 64  # streamed sha256


def test_orchestrator_one_failing_check_does_not_sink_others(
    monkeypatch, metadata_pdf
):
    # a raising check -> a warning + a checks_unavailable entry; the OTHER checks
    # still run (metadata still fires) and are listed in checks_run.
    import scripts.redaction_check as r
    from scripts.redaction_check import check_redactions

    def _boom(path):
        raise RuntimeError("embedded_files exploded")

    monkeypatch.setattr(r, "check_box_over_text", lambda path: [])
    monkeypatch.setattr(r, "check_embedded_files", _boom)
    rep = check_redactions(metadata_pdf, mode="received")
    assert any(f.check == "metadata" for f in rep.findings)  # others still ran
    assert "metadata" in rep.checks_run
    assert any("embedded_files" in u for u in rep.checks_unavailable)


@pytest.mark.xray
def test_orchestrator_end_to_end_with_xray_blocks_bad_redaction(bad_redaction_pdf):
    # REAL end-to-end: the live x-ray edge finds a box on page 1, the orchestrator
    # unions it into signal_pages, text_layer corroborates, both are high in
    # pre-publish -> safe_to_publish False; and no raw string crosses publish.
    from scripts.redaction_check import check_redactions

    rep = check_redactions(bad_redaction_pdf, mode="pre-publish")
    checks = {f.check for f in rep.findings}
    assert "box_over_text" in checks
    assert "text_layer" in checks  # box_over_text page fed signal_pages
    assert rep.safe_to_publish is False
    assert "John Q Public" not in str(rep.publishable_view())

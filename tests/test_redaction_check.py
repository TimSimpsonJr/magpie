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

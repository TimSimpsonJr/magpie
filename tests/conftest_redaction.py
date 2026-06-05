"""Synthetic PDF fixtures for the Phase 7 ``redaction-check`` offline checks.

Every fixture is built into ``tmp_path`` from code with **pikepdf + fpdf2 +
crafted bytes ONLY** -- the offline tier must NEVER import PyMuPDF / fitz (the
x-ray box-over-text check is the only ``xray``-marked path and lives in a later
dispatch). No real corpus is touched (the Simpsonville corpus is wired in only at
Task 11.2 behind an env var, never committed).

ASCII-only (SDD subagents content-filter-block on exotic glyphs).

These fixtures are surfaced to ``tests/test_redaction_check.py`` by being
imported (star-import) into ``tests/conftest.py`` so pytest discovers them.

The fiddly part is building valid PDF objects with pikepdf rather than fitz: a
``/Redact`` annotation, an ``AcroForm`` text field, a comment-type annotation
with ``/Contents``, an embedded file, and a crafted incremental-save revision.
Each construction was verified to read back through the same pikepdf calls the
checks use (``str(a.get("/Subtype"))``, ``Root/AcroForm/Fields``,
``pdf.attachments``, ``open_metadata()``).
"""
from __future__ import annotations

from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# Low-level builders (no pytest plumbing -- callable from any fixture).
# --------------------------------------------------------------------------- #


def _fpdf_line_pdf(path: Path, line: str) -> Path:
    """Render a single ASCII ``line`` as a native fpdf2 text PDF (real text
    layer, no covering rect). Used for the clean control + a generic clean PDF
    the pikepdf checks read as a no-finding negative case."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.set_xy(40, 90)
    pdf.cell(0, 10, line)
    pdf.output(str(path))
    return path


def _fpdf_box_over_text_pdf(path: Path, line: str) -> Path:
    """Render an ASCII ``line`` of live text, THEN draw a solid black filled rect
    OVER it (text command first, rect command second -- so the box covers
    still-extractable text). This is the classic BAD redaction x-ray detects: the
    text is geometrically hidden but the text operator is still in the content
    stream, so ``xray.inspect()`` recovers it. Built with fpdf2 (no fitz), so the
    offline tier never imports PyMuPDF to BUILD it (the x-ray import is lazy in the
    check, exercised only by the ``xray``-marked test)."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.set_xy(40, 90)
    pdf.cell(0, 10, line)
    # Draw the covering box AFTER the text so it sits on top of live text.
    pdf.set_fill_color(0, 0, 0)
    pdf.rect(38, 86, 230, 22, style="F")
    pdf.output(str(path))
    return path


def _new_single_page_pdf():
    """A fresh one-blank-page pikepdf the annotation/AcroForm/embedded builders
    attach to."""
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    return pdf


# --------------------------------------------------------------------------- #
# Fixtures (each builds its PDF into tmp_path and returns the Path).
# --------------------------------------------------------------------------- #


@pytest.fixture
def clean_pdf(tmp_path) -> Path:
    """A clean native PDF: the line ``Visible public text`` with no covering
    rect. Doubles as (a) the x-ray control (later dispatch) and (b) the generic
    no-finding negative case for the pikepdf checks (no /Redact annot, no
    embedded files, no AcroForm, no comment annots)."""
    return _fpdf_line_pdf(tmp_path / "clean.pdf", "Visible public text")


@pytest.fixture
def bad_redaction_pdf(tmp_path) -> Path:
    """A BAD-redaction PDF: the ASCII line ``SECRET NAME John Q Public`` rendered
    as live text with a solid black rect drawn OVER it (text first, rect second).
    x-ray flags this (a box over still-extractable text); the recovered under-box
    string is the x-ray-marked test's local-only evidence. Also doubles as the
    box_over_text + text_layer co-occurrence corroboration fixture (the page has
    BOTH an x-ray box and an extractable text layer)."""
    return _fpdf_box_over_text_pdf(
        tmp_path / "bad_redaction.pdf", "SECRET NAME John Q Public"
    )


@pytest.fixture
def metadata_pdf(tmp_path) -> Path:
    """pikepdf with XMP + docinfo author/title leakage: ``dc:creator`` =
    ``Jane Author`` and ``dc:title`` = ``Internal Draft`` (pikepdf syncs these
    into docinfo /Author + /Title on save). The raw author/title strings are a
    LEAD; the check must keep them out of ``detail``."""
    pdf = _new_single_page_pdf()
    with pdf.open_metadata() as m:
        m["dc:creator"] = ["Jane Author"]
        m["dc:title"] = "Internal Draft"
    path = tmp_path / "metadata.pdf"
    pdf.save(str(path))
    return path


@pytest.fixture
def redact_annot_pdf(tmp_path) -> Path:
    """pikepdf page with a ``/Subtype /Redact`` annotation that was MARKED but
    never APPLIED (no ``apply_redactions``) -- so the underlying text would still
    be present. Built with pikepdf (NOT fitz): a ``Dictionary`` attached to the
    page ``/Annots`` array as an indirect object."""
    import pikepdf
    from pikepdf import Array, Dictionary, Name

    pdf = _new_single_page_pdf()
    page = pdf.pages[0]
    annot = Dictionary(
        Type=Name.Annot,
        Subtype=Name.Redact,
        Rect=Array([18, 40, 120, 55]),
    )
    page.Annots = Array([pdf.make_indirect(annot)])
    path = tmp_path / "redact_annot.pdf"
    pdf.save(str(path))
    return path


@pytest.fixture
def redact_annot_text_pdf(tmp_path) -> Path:
    """A page that carries BOTH a ``/Subtype /Redact`` annotation AND an
    extractable text layer ``hidden secret text here`` under it -- the trigger-(i)
    co-occurrence the text_layer check needs (a /Redact-flagged page whose text is
    still extractable). Built fpdf2-first (native text) then a pikepdf pass adds
    the unapplied /Redact annot (NO fitz). Distinct from ``redact_annot_pdf``,
    which is a blank page + annot with no text layer."""
    import pikepdf
    from pikepdf import Array, Dictionary, Name

    text_path = _fpdf_line_pdf(tmp_path / "redact_annot_text.pdf", "hidden secret text here")
    pdf = pikepdf.open(str(text_path), allow_overwriting_input=True)
    page = pdf.pages[0]
    annot = Dictionary(
        Type=Name.Annot,
        Subtype=Name.Redact,
        Rect=Array([18, 40, 120, 55]),
    )
    page.Annots = Array([pdf.make_indirect(annot)])
    pdf.save(str(text_path))
    return text_path


@pytest.fixture
def embedded_file_pdf(tmp_path) -> Path:
    """pikepdf carrying an embedded attachment ``hidden_notes.txt`` (13 bytes of
    ``internal only``). The FILENAME can itself leak PII (e.g. ``John_Doe_DOB``),
    so the check must put the name in ``local_evidence`` and keep only count +
    byte-sizes (non-strings) in ``detail``."""
    pdf = _new_single_page_pdf()
    pdf.attachments["hidden_notes.txt"] = b"internal only"
    path = tmp_path / "embedded_file.pdf"
    pdf.save(str(path))
    return path


@pytest.fixture
def acroform_pdf(tmp_path) -> Path:
    """pikepdf with a ``Root/AcroForm`` holding one text field whose ``/V`` is
    ``123-45-6789`` -- a form value that should have been redacted but sits behind
    a flat-looking page. The field NAME publishes; the ``/V`` VALUE is a LEAD."""
    import pikepdf
    from pikepdf import Array, Dictionary, Name, String

    pdf = _new_single_page_pdf()
    field = Dictionary(
        FT=Name.Tx,
        T=String("ssn_field"),
        V=String("123-45-6789"),
    )
    acroform = Dictionary(Fields=Array([pdf.make_indirect(field)]))
    pdf.Root.AcroForm = pdf.make_indirect(acroform)
    path = tmp_path / "acroform.pdf"
    pdf.save(str(path))
    return path


@pytest.fixture
def annotation_text_pdf(tmp_path) -> Path:
    """pikepdf with a comment-type ``/FreeText`` annotation whose ``/Contents``
    is ``reviewer note: suspect John Doe DOB on file`` -- a leaked reviewer note.
    The ``/Contents`` string is a LEAD (local_evidence only)."""
    import pikepdf
    from pikepdf import Array, Dictionary, Name, String

    pdf = _new_single_page_pdf()
    page = pdf.pages[0]
    note = Dictionary(
        Type=Name.Annot,
        Subtype=Name.FreeText,
        Rect=Array([10, 10, 100, 30]),
        Contents=String("reviewer note: suspect John Doe DOB on file"),
    )
    page.Annots = Array([pdf.make_indirect(note)])
    path = tmp_path / "annotation_text.pdf"
    pdf.save(str(path))
    return path


@pytest.fixture
def multi_finding_pdf(tmp_path) -> Path:
    """A single pikepdf document that trips SEVERAL offline checks at once, each
    carrying a RAW leaked string in ``local_evidence``: XMP author (``Jane Author``)
    + an unapplied ``/Redact`` annot + an embedded file (``hidden_notes.txt``) + an
    AcroForm ``/V`` (``123-45-6789``) + a ``/FreeText`` comment (``suspect John Doe``).
    The orchestrator end-to-end ``publishable_view()`` leak test asserts NONE of
    these raw strings survive into the published view (the never-publish-raw
    invariant across multiple findings)."""
    import pikepdf
    from pikepdf import Array, Dictionary, Name, String

    pdf = _new_single_page_pdf()
    page = pdf.pages[0]
    # metadata leak (XMP -> docinfo on save).
    with pdf.open_metadata() as m:
        m["dc:creator"] = ["Jane Author"]
    # embedded file (filename is a raw leak).
    pdf.attachments["hidden_notes.txt"] = b"internal only"
    # AcroForm field with a /V that should have been redacted.
    field = Dictionary(FT=Name.Tx, T=String("ssn_field"), V=String("123-45-6789"))
    pdf.Root.AcroForm = pdf.make_indirect(Dictionary(Fields=Array([pdf.make_indirect(field)])))
    # an unapplied /Redact annot + a /FreeText comment carrying a reviewer note.
    redact = Dictionary(Type=Name.Annot, Subtype=Name.Redact, Rect=Array([18, 40, 120, 55]))
    # NOTE: the raw /Contents deliberately avoids the words the annotation_text
    # SUMMARY uses ("reviewer note") so the leak test asserts on genuine PII
    # tokens, not a phrase the publishable summary legitimately contains.
    note = Dictionary(
        Type=Name.Annot,
        Subtype=Name.FreeText,
        Rect=Array([10, 10, 100, 30]),
        Contents=String("suspect John Doe DOB on file"),
    )
    page.Annots = Array([pdf.make_indirect(redact), pdf.make_indirect(note)])
    path = tmp_path / "multi_finding.pdf"
    pdf.save(str(path))
    return path


@pytest.fixture
def clean_single_rev_pdf(tmp_path) -> Path:
    """A plain single-revision pikepdf save -- ``%%EOF`` count == 1 /
    ``startxref`` count == 1. The negative control for the incremental-save
    check."""
    pdf = _new_single_page_pdf()
    path = tmp_path / "single_rev.pdf"
    pdf.save(str(path))
    return path


@pytest.fixture
def incremental_save_pdf(tmp_path) -> Path:
    """A pikepdf save with a CRAFTED synthetic SECOND revision appended, so
    ``bytes.count(b"%%EOF") == 2`` and ``bytes.count(b"startxref") == 2``.

    The incremental-save detector is a PURE byte scan (no parse), so the appended
    block only needs the byte shape of an incremental update: a tiny xref
    subsection + trailer + ``startxref`` + ``%%EOF``. It is NOT a parseable second
    generation of the document -- it exists only to trip the revision-count lead.
    """
    pdf = _new_single_page_pdf()
    path = tmp_path / "incremental.pdf"
    pdf.save(str(path))
    base = path.read_bytes()
    # --- CRAFTED second revision (byte shape only; detector does not parse it) ---
    second_rev = (
        b"\n"
        b"xref\n"
        b"0 1\n"
        b"0000000000 65535 f \n"
        b"trailer\n"
        b"<< /Size 1 /Prev 0 /Root 1 0 R >>\n"
        b"startxref\n"
        + str(len(base)).encode("ascii")
        + b"\n%%EOF\n"
    )
    path.write_bytes(base + second_rev)
    return path

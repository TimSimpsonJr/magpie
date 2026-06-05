"""redaction-check (INPUT side) -- find BAD REDACTIONS in a PDF as
FLAG-FOR-A-HUMAN LEADS, never an "improper redaction" verdict.

This module is the PURE-CORE / ENGINE-AT-THE-EDGE half of the Phase 7
``redaction-check`` skill (design
``docs/plans/2026-06-04-magpie-phase7-redaction-design.md``; verified library
facts in ``skills/redaction-check/references/prior-art.md``). The byte
(incremental_save) and pikepdf checks (metadata, unapplied_redact,
embedded_files, acroform_values, annotation_text) need no ML and are
golden-testable OFFLINE -- importing this module and running these checks loads
only stdlib + pikepdf, NEVER PyMuPDF / fitz. The x-ray box_over_text check, the
pdfminer text_layer check, and the orchestrator are a LATER dispatch.

THE NEVER-PUBLISH-RAW INVARIANT (design 1.3): a raw recovered/leaked STRING
(under-box text, metadata values, AcroForm /V values, annotation /Contents, an
embedded filename) lives ONLY in a finding's ``local_evidence``. It is NEVER put
in ``detail``. ``detail`` carries PUBLISHABLE FACTS ONLY: counts, field NAMES,
page numbers, byte-sizes. ``RedactionReport.publishable_view()`` drops every
``local_evidence`` AND defensively asserts no ``detail`` value smuggled a raw
string, so no third-party PII ever crosses a published path.

LEADS, NEVER VERDICTS: every finding is a flag for a human; a clean report is
never read as "fully redacted" (the ``cannot_catch`` honesty footer lists the
failure classes with no reliable FOSS auto-detector).

This module shares NO code with scripts/ingest.py / scripts/recipe.py etc.
(design 5): the Phase-7 modules are decoupled. The streamed sha256 helper the
later orchestrator needs is re-implemented inline rather than imported from
ingest.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from pathlib import Path


# --------------------------------------------------------------------------- #
# Output schema (design 1.3) -- leads, publishable-vs-local.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RedactionFinding:
    """One redaction LEAD for a human. ``detail`` is PUBLISHABLE (counts, field
    NAMES, page, byte-sizes -- never a raw leaked string); ``local_evidence`` is
    the ONLY place raw recovered/leaked strings live and is dropped by
    ``publishable_view()``."""

    check: str  # "box_over_text" | "metadata" | "incremental_save" | ...
    severity: str  # "info" | "low" | "medium" | "high"
    page: int | None  # 1-based; None for doc-level (metadata, incremental_save)
    summary: str  # human lead, PUBLISHABLE
    detail: dict  # PUBLISHABLE FACTS ONLY -- no raw leaked string
    local_evidence: dict | None = None  # LOCAL-ONLY raw strings; publishable_view drops it


@dataclass
class RedactionReport:
    """The full report. ``to_dict()`` is the LOCAL JSON-able view (keeps
    ``local_evidence``); ``publishable_view()`` is the publish-safe view (drops
    every ``local_evidence`` and asserts ``detail`` carries no raw string)."""

    source_path: str
    source_sha256: str
    mode: str
    checks_run: list[str]
    checks_unavailable: list[str]  # e.g. ["box_over_text: x-ray not installed"]
    findings: list[RedactionFinding]
    n_findings: int
    safe_to_publish: bool | None  # pre-publish only; None in received mode
    warnings: list[str]
    cannot_catch: list[str]  # the honesty footer (design 1.5)

    def to_dict(self) -> dict:
        """JSON-able LOCAL report. KEEPS each finding's ``local_evidence`` (the
        local report object is the home of the raw strings -- only
        ``publishable_view`` strips them)."""
        return asdict(self)

    def publishable_view(self) -> dict:
        """The publish-safe view: every finding's ``local_evidence`` is DROPPED,
        and -- defensively (design 1.3: a schema check, not just convention) -- it
        asserts no finding's ``detail`` smuggled a raw string that also appears in
        that finding's ``local_evidence``. ``local_evidence`` is the ONLY raw
        carrier; if a raw value leaked into ``detail`` this RAISES rather than
        publish it."""
        out = copy.deepcopy(asdict(self))
        for finding_dict, finding in zip(out["findings"], self.findings):
            # Defensive: no raw evidence string may also live in detail. We only
            # know what "raw" means from local_evidence, so flag any local raw
            # string that appears anywhere in the (publishable) detail blob.
            if finding.local_evidence:
                detail_blob = str(finding_dict.get("detail"))
                for raw in _iter_raw_strings(finding.local_evidence):
                    assert raw not in detail_blob, (
                        f"raw evidence leaked into publishable detail for check "
                        f"{finding.check!r}: {raw!r}"
                    )
            finding_dict["local_evidence"] = None  # drop the raw carrier
        return out


def _iter_raw_strings(local_evidence: dict):
    """Yield every raw string buried in a ``local_evidence`` dict (values may be
    strings or lists/tuples of strings, keyed by sub-source)."""
    for value in local_evidence.values():
        if isinstance(value, str):
            yield value
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str):
                    yield item


# --------------------------------------------------------------------------- #
# The checks. Each is a STANDALONE function (path -> list[RedactionFinding]).
# Each returns [] when nothing is found -- a clean check is never a "fully
# redacted" verdict (the orchestrator's cannot_catch footer carries that
# honesty). Every check that can surface a raw string puts it in
# ``local_evidence``, never in ``detail`` (the never-publish-raw invariant).
# --------------------------------------------------------------------------- #


def check_incremental_save(path) -> list[RedactionFinding]:
    """LEAD: the PDF carries more than one revision (prior content -- including
    pre-"redaction" text -- may be recoverable from an earlier generation).

    Pure byte scan (no parse, engine-independent): count ``b"%%EOF"`` paired with
    ``b"startxref"`` (both increment per revision -- the sturdier revision-count
    lead from the research gate). A finding iff ``eof_count > 1``. Legitimate
    incremental saves exist (signatures, form fill), so this is a LEAD, NEVER
    proof. The counts are publishable ints -> they go in ``detail``."""
    raw = Path(path).read_bytes()
    eof_count = raw.count(b"%%EOF")
    startxref_count = raw.count(b"startxref")
    if eof_count <= 1:
        return []
    n_revisions = eof_count  # one %%EOF per revision
    return [
        RedactionFinding(
            check="incremental_save",
            severity="medium",
            page=None,
            summary=(
                f"PDF carries {n_revisions} revisions (>1); a prior revision may "
                f"hold pre-redaction content -- a lead for a human, not proof"
            ),
            detail={
                "eof_count": eof_count,
                "startxref_count": startxref_count,
                "n_revisions": n_revisions,
            },
        )
    ]


# docinfo keys worth surfacing as a metadata LEAD (author/tool/title/dates can
# reveal names, internal filenames, software, or edit history). The value of any
# present key is a RAW string -> local_evidence; the KEY is a publishable field
# name -> detail.
_DOCINFO_LEAK_KEYS = (
    "/Author",
    "/Creator",
    "/Producer",
    "/Title",
    "/Subject",
    "/Keywords",
    "/CreationDate",
    "/ModDate",
)


def check_metadata(path) -> list[RedactionFinding]:
    """LEAD: document properties (docinfo + XMP) carry author / creator /
    producer / title / dates. A leaked name, internal filename, or software
    string is a lead (often it is just our own author/tool -- hence severity
    medium at the orchestrator). READ-ONLY (never edits).

    The never-publish-raw split: present field NAMES (``/Author``,
    ``dc:creator``, ...) go in ``detail["fields"]``; the raw VALUES go in
    ``local_evidence`` keyed by field name."""
    import pikepdf

    fields: list[str] = []
    local: dict = {}
    with pikepdf.open(str(path)) as pdf:
        docinfo = pdf.docinfo
        for key in _DOCINFO_LEAK_KEYS:
            if key in docinfo:
                value = str(docinfo[key]).strip()
                if value:
                    fields.append(key)
                    local[key] = value
        # XMP packet (namespaced keys). open_metadata() reads the dublin-core /
        # xmp fields; the value can be a str or a list (e.g. dc:creator).
        with pdf.open_metadata() as meta:
            for xmp_key in sorted(meta.keys()):
                try:
                    value = meta.get(xmp_key)
                except (KeyError, ValueError):  # pragma: no cover - defensive
                    continue
                rendered = _render_xmp_value(value)
                if rendered:
                    fields.append(xmp_key)
                    local[xmp_key] = rendered
    if not fields:
        return []
    return [
        RedactionFinding(
            check="metadata",
            severity="low",
            page=None,
            summary=(
                f"{len(fields)} document-metadata field(s) present "
                f"(author/tool/title/dates) -- a lead to review for leaked names"
            ),
            detail={"fields": fields},
            local_evidence=local,
        )
    ]


def _render_xmp_value(value) -> str:
    """Render an XMP value (str | list | None) as a single raw string for
    local_evidence (empty string => skip)."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip()


def check_unapplied_redact(path) -> list[RedactionFinding]:
    """LEAD: a page carries a ``/Subtype /Redact`` annotation that was MARKED but
    never APPLIED -- the tool flagged content for redaction but the underlying
    content (text/image) was never removed and is still present.

    pikepdf iterates each page's ``/Annots`` and matches
    ``str(a.get("/Subtype")) == "/Redact"`` (verified API). One finding per page
    that carries unapplied redaction annot(s); ``detail`` carries the per-page
    count (a publishable int) -- there is no raw string to surface here."""
    import pikepdf

    findings: list[RedactionFinding] = []
    with pikepdf.open(str(path)) as pdf:
        for pageno, page in enumerate(pdf.pages, start=1):
            annots = page.get("/Annots")
            if annots is None:
                continue
            count = sum(1 for a in annots if str(a.get("/Subtype")) == "/Redact")
            if count:
                findings.append(
                    RedactionFinding(
                        check="unapplied_redact",
                        severity="medium",
                        page=pageno,
                        summary=(
                            f"{count} /Redact annotation(s) marked but not applied "
                            f"on page {pageno} -- underlying content may still be "
                            f"present (a lead)"
                        ),
                        detail={"count": count},
                    )
                )
    return findings


def check_embedded_files(path) -> list[RedactionFinding]:
    """LEAD: the PDF carries embedded / attached files (a spreadsheet, an
    original) that can hold un-redacted source data. Enumerate name + size only;
    NEVER auto-extract contents.

    Surfaces (design 1.1 check 6): ``pdf.attachments`` (the document-level
    EmbeddedFiles name tree) PLUS ``/AF`` associated files PLUS ``/FileAttachment``
    annotations -- de-duplicated by (name, size).

    THE FILENAME IS A RAW STRING: a filename can itself leak PII
    (``John_Doe_DOB.xlsx``), so it goes in ``local_evidence["names"]``. ``detail``
    carries ONLY non-string facts: ``{count, sizes}`` (byte sizes are publishable
    ints). The file bytes are NEVER read into any output."""
    import pikepdf

    # collect (name, size) pairs; de-dup by the pair.
    seen: set[tuple[str, int]] = set()
    pairs: list[tuple[str, int]] = []

    def _add(name: str, size: int) -> None:
        key = (name, size)
        if key not in seen:
            seen.add(key)
            pairs.append(key)

    with pikepdf.open(str(path)) as pdf:
        # 1. Document-level attachments (the /Names/EmbeddedFiles name tree).
        for name in list(pdf.attachments):
            try:
                filespec = pdf.attachments[name]
                _add(str(name), _filespec_size(filespec.obj))
            except Exception:  # noqa: BLE001 - a malformed attachment is still a lead
                _add(str(name), -1)
        # 2. Associated files /AF on the document root.
        for spec in _iter_filespec_objs(pdf.Root.get("/AF")):
            name, size = _filespec_name_size(spec)
            if name is not None:
                _add(name, size)
        # 3. /FileAttachment annots + page-level /AF.
        for page in pdf.pages:
            annots = page.get("/Annots")
            if annots is None:
                continue
            for annot in annots:
                if str(annot.get("/Subtype")) == "/FileAttachment":
                    spec = annot.get("/FS")
                    if spec is not None:
                        name, size = _filespec_name_size(spec)
                        if name is not None:
                            _add(name, size)
                for spec in _iter_filespec_objs(annot.get("/AF")):
                    name, size = _filespec_name_size(spec)
                    if name is not None:
                        _add(name, size)

    if not pairs:
        return []
    names = [name for name, _ in pairs]
    sizes = [size for _, size in pairs]
    return [
        RedactionFinding(
            check="embedded_files",
            severity="medium",
            page=None,
            summary=(
                f"{len(pairs)} embedded/attached file(s) -- may carry un-redacted "
                f"source data; enumerated (name local-only), not extracted"
            ),
            detail={"count": len(pairs), "sizes": sizes},
            local_evidence={"names": names},
        )
    ]


def _iter_filespec_objs(af_value):
    """Yield filespec objects from an ``/AF`` value (an array of filespecs, or a
    single filespec, or None)."""
    if af_value is None:
        return
    try:
        items = list(af_value)
    except TypeError:
        items = [af_value]
    for item in items:
        if item is not None:
            yield item


def _filespec_name_size(spec) -> tuple[str | None, int]:
    """Return ``(filename, byte_size)`` for a filespec object; name is ``/UF``
    (preferred) or ``/F``; size from the embedded stream. ``(None, -1)`` if no
    name is present."""
    name = spec.get("/UF") or spec.get("/F")
    if name is None:
        return None, -1
    return str(name), _filespec_size(spec)


def _filespec_size(spec) -> int:
    """Byte size of a filespec's embedded stream (``/EF/F`` or ``/EF/UF``), or
    -1 if it cannot be read. Reads bytes ONLY to measure length -- the bytes
    never leave this function."""
    try:
        ef = spec.get("/EF")
        if ef is None:
            return -1
        stream = ef.get("/F") or ef.get("/UF")
        if stream is None:
            return -1
        return len(stream.read_bytes())
    except Exception:  # noqa: BLE001 - size is best-effort; a lead stands regardless
        return -1


def check_acroform_values(path) -> list[RedactionFinding]:
    """LEAD: ``Root/AcroForm/Fields`` carry ``/V`` values -- a form field can hold
    un-redacted data behind a flattened-looking page.

    Walks the AcroForm field tree (including ``/Kids``). For each field with a
    non-empty ``/V``: the field NAME (``/T``) is a publishable fact -> ``detail``;
    the ``/V`` VALUE is a raw leak that should already have been redacted ->
    ``local_evidence`` keyed by field name."""
    import pikepdf

    fields: list[str] = []
    local: dict = {}
    with pikepdf.open(str(path)) as pdf:
        acroform = pdf.Root.get("/AcroForm")
        if acroform is None:
            return []
        roots = acroform.get("/Fields")
        if roots is None:
            return []
        for field_obj in _walk_acroform_fields(roots):
            value = field_obj.get("/V")
            if value is None:
                continue
            rendered = str(value).strip()
            if not rendered:
                continue
            name = field_obj.get("/T")
            name_str = str(name) if name is not None else "<unnamed>"
            fields.append(name_str)
            local[name_str] = rendered
    if not fields:
        return []
    return [
        RedactionFinding(
            check="acroform_values",
            severity="medium",
            page=None,
            summary=(
                f"{len(fields)} AcroForm field value(s) present -- a form field can "
                f"hold data behind a flat-looking page (a lead)"
            ),
            detail={"fields": fields},
            local_evidence=local,
        )
    ]


def _walk_acroform_fields(fields_array):
    """Yield every terminal AcroForm field dict, descending ``/Kids`` subtrees."""
    for field_obj in fields_array:
        kids = field_obj.get("/Kids")
        if kids is not None:
            yield from _walk_acroform_fields(kids)
        else:
            yield field_obj

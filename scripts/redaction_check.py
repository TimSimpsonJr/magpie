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
import hashlib
from dataclasses import asdict, dataclass, replace
from pathlib import Path


# --------------------------------------------------------------------------- #
# Degrade-don't-crash sentinel (design 1.6). A check that CANNOT RUN (a lazy
# engine import failed, or an engine raised on a malformed PDF) raises this
# rather than crashing OR returning [] (a false "clean"). The orchestrator
# (check_redactions) catches it into ``checks_unavailable`` + a warning, the
# other checks still run, and in pre-publish mode an unavailable check forces
# ``safe_to_publish=False`` (fail-closed).
# --------------------------------------------------------------------------- #


class CheckUnavailable(Exception):
    """A check could not run (engine missing / engine raised). Carries a
    ``"<check>: <reason>"`` message the orchestrator records in
    ``checks_unavailable``. NOT a clean result -- a check that did not run never
    certifies the absence of what it checks for."""


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


# comment-type annotation subtypes that carry human-authored /Contents (reviewer
# notes / leaked text). NOT /Redact (that is check_unapplied_redact).
_COMMENT_ANNOT_SUBTYPES = ("/Text", "/FreeText", "/Popup")


def check_annotation_text(path) -> list[RedactionFinding]:
    """LEAD: a comment-type annotation (``/Text``, ``/FreeText``, ``/Popup``)
    carries ``/Contents`` -- a reviewer note or leaked text.

    The ``/Contents`` string is raw, potentially-PII text -> ``local_evidence``
    (one finding per page, the page's comment texts keyed by subtype). ``detail``
    carries only publishable facts: the per-page count and the subtypes seen --
    never the comment text."""
    import pikepdf

    findings: list[RedactionFinding] = []
    with pikepdf.open(str(path)) as pdf:
        for pageno, page in enumerate(pdf.pages, start=1):
            annots = page.get("/Annots")
            if annots is None:
                continue
            texts: list[str] = []
            subtypes: list[str] = []
            for annot in annots:
                subtype = str(annot.get("/Subtype"))
                if subtype not in _COMMENT_ANNOT_SUBTYPES:
                    continue
                contents = annot.get("/Contents")
                if contents is None:
                    continue
                rendered = str(contents).strip()
                if not rendered:
                    continue
                texts.append(rendered)
                subtypes.append(subtype)
            if texts:
                findings.append(
                    RedactionFinding(
                        check="annotation_text",
                        severity="medium",
                        page=pageno,
                        summary=(
                            f"{len(texts)} comment annotation(s) with text on page "
                            f"{pageno} -- a reviewer note can carry PII (a lead)"
                        ),
                        detail={
                            "count": len(texts),
                            "subtypes": sorted(set(subtypes)),
                        },
                        local_evidence={"contents": texts},
                    )
                )
    return findings


# --------------------------------------------------------------------------- #
# The x-ray LAZY EDGE (Task 4 / design 1.1 check 1, 1.6). This is the ONLY check
# that pulls PyMuPDF -- and ONLY when CALLED. ``import xray`` lives INSIDE the
# function so importing this module stays PyMuPDF-free (the offline tier loads
# stdlib + pikepdf only). DEGRADE-DON'T-CRASH: a missing x-ray OR an inspect()
# that raises on a malformed PDF -> raise ``CheckUnavailable`` (the orchestrator
# records it), NEVER crash and NEVER return [] (a false "clean").
# --------------------------------------------------------------------------- #

# x-ray's bbox comes from PyMuPDF, whose default page coordinate origin is the
# TOP-LEFT corner (the PDF spec / pdfminer use BOTTOM-left). We report the bbox in
# its NATIVE engine space and NAME the origin in detail so a human reads it
# correctly; we never do cross-engine bbox math (design 1.4).
_XRAY_BBOX_ORIGIN = "top-left (PyMuPDF/x-ray native)"


def check_box_over_text(path) -> list[RedactionFinding]:
    """LEAD: a rectangle / highlight was drawn OVER still-extractable text (a box
    that hides the text visually but leaves the text operator in the content
    stream -- the classic bad redaction). Uses Free Law's ``x-ray`` (lazy ->
    PyMuPDF).

    ``xray.inspect(str(path))`` -> ``{page: [{"bbox": (x0,y0,x1,y1), "text": ...}]}``
    (1-based page; empty dict => none found). One finding per page entry: the
    ``bbox`` (+ the named origin) is a publishable fact -> ``detail``; the recovered
    under-box ``text`` is a RAW leak -> ``local_evidence`` (NEVER ``detail``).

    DEGRADE-DON'T-CRASH (design 1.6): if ``import xray`` fails (x-ray/PyMuPDF
    absent) OR ``inspect()`` raises on a malformed PDF, raise ``CheckUnavailable``
    -- the orchestrator turns it into a ``checks_unavailable`` entry + a warning,
    and pre-publish fails closed. Returning [] here would be a FALSE "clean"."""
    try:
        import xray  # LAZY: pulls PyMuPDF only when this check actually runs.
    except ImportError as exc:
        raise CheckUnavailable(
            f"box_over_text: x-ray not importable ({exc})"
        ) from exc

    try:
        result = xray.inspect(str(path))
    except Exception as exc:  # noqa: BLE001 - any inspect() failure degrades, never a false clean
        raise CheckUnavailable(
            f"box_over_text: x-ray inspect() failed ({type(exc).__name__})"
        ) from exc

    findings: list[RedactionFinding] = []
    # result keys are 1-based page numbers; an empty dict means no bad redactions.
    for pageno in sorted(result):
        for entry in result[pageno]:
            bbox = entry.get("bbox")
            text = entry.get("text", "")
            findings.append(
                RedactionFinding(
                    check="box_over_text",
                    severity="high",
                    page=int(pageno),
                    summary=(
                        f"1 box-over-text region on page {pageno} -- a box covers "
                        f"still-extractable text (a lead; the under-box text is "
                        f"recoverable, kept LOCAL)"
                    ),
                    detail={
                        # bbox is a publishable geometric fact, reported in its
                        # NATIVE engine space with the origin named (design 1.4).
                        "bbox": list(bbox) if bbox is not None else None,
                        "bbox_origin": _XRAY_BBOX_ORIGIN,
                    },
                    # the recovered under-box string is the RAW leak -> LOCAL ONLY.
                    local_evidence={"text": text},
                )
            )
    return findings


# --------------------------------------------------------------------------- #
# text_layer (Task 3h / design 1.1 check 2, 1.4). pdfminer.six per-page
# extractable-text presence. This is NOT a standalone alarm (every normal PDF has
# legitimate extractable text). It becomes a FINDING only on PAGE-LEVEL
# CO-OCCURRENCE with another redaction signal:
#   (i)   the page is in ``signal_pages`` (pages the orchestrator flagged via
#         box_over_text OR unapplied_redact) AND the page has extractable text, OR
#   (iii) the page is image-bearing (scanned-looking) yet ALSO carries a text
#         layer (an image-only page should have no selectable text -- a hidden
#         text layer under a scan is itself the anomaly).
# (Trigger (ii), the box_over_text corroboration, is just case (i) with the
# signal page coming from x-ray -- the orchestrator unions both signal sources.)
# PAGE-LEVEL ONLY -- NO cross-engine bbox correlation (the coordinate trap, 1.4).
# pdfminer text is raw -> if surfaced it goes in ``local_evidence``; ``detail``
# carries page + char-count facts only.
# --------------------------------------------------------------------------- #


def check_text_layer(path, *, signal_pages) -> list[RedactionFinding]:
    """LEAD: a page carries extractable text that CO-OCCURS (page-level) with a
    redaction signal. ``signal_pages`` is the set of 1-based page numbers the
    orchestrator flagged via box_over_text / unapplied_redact (passed in -- this is
    the only cross-check ordering dependency). A finding fires for page ``p`` when
    either (i) ``p in signal_pages`` and the page has extractable text, OR (iii)
    the page is image-bearing yet still carries a text layer. NEVER a standalone
    "text exists => bad" alarm. Page-level co-occurrence ONLY (design 1.4): no
    cross-engine bbox math.

    ``detail`` carries the page + char count + which trigger fired (publishable
    facts); the extracted text itself is NOT surfaced (a normal page's full text is
    not evidence -- the char count + co-occurrence is the lead). If a future
    variant surfaces a snippet it MUST go in ``local_evidence``."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTChar, LTFigure, LTImage, LTTextContainer

    signal_pages = set(signal_pages or ())
    findings: list[RedactionFinding] = []
    for pageno, layout in enumerate(extract_pages(str(path)), start=1):
        char_count, has_image = _page_text_and_image(
            layout, LTChar, LTImage, LTTextContainer, LTFigure
        )
        if char_count <= 0:
            continue  # nothing extractable on this page -> no text-layer lead
        on_signal_page = pageno in signal_pages
        # (iii) image-bearing page that ALSO carries a hidden text layer.
        image_only_with_text = has_image
        if not (on_signal_page or image_only_with_text):
            continue  # extractable text but no co-occurrence -> not a lead
        trigger = "redaction_signal" if on_signal_page else "image_with_text_layer"
        findings.append(
            RedactionFinding(
                check="text_layer",
                severity="medium",
                page=pageno,
                summary=(
                    f"page {pageno} carries {char_count} extractable text char(s) "
                    f"co-occurring with a redaction signal ({trigger}) -- the text "
                    f"layer may be recoverable (a lead, page-level only)"
                ),
                detail={
                    "char_count": char_count,
                    "trigger": trigger,
                    "has_image": has_image,
                },
            )
        )
    return findings


def _page_text_and_image(layout, LTChar, LTImage, LTTextContainer, LTFigure):
    """Walk a pdfminer page ``layout`` (descending text containers + figures) and
    return ``(extractable_char_count, has_image)``."""
    char_count = 0
    has_image = False

    def _walk(obj) -> None:
        nonlocal char_count, has_image
        for el in obj:
            if isinstance(el, LTChar):
                char_count += 1
            elif isinstance(el, LTImage):
                has_image = True
            if isinstance(el, (LTTextContainer, LTFigure)):
                _walk(el)

    _walk(layout)
    return char_count, has_image


# --------------------------------------------------------------------------- #
# Orchestrator (Task 3i / design 1.2, 1.6). Runs all available checks over ONE
# PDF and assembles a RedactionReport. The safety-critical parts are (a) the
# signal_pages wiring (box_over_text + unapplied_redact feed text_layer) and
# (b) the FAIL-CLOSED pre-publish safe_to_publish gate.
# --------------------------------------------------------------------------- #


# PINNED pre-publish severity map (plan Task 3i). In ``pre-publish`` mode each
# finding's severity is set from this map -- so the publish GATE cannot be
# silently weakened without tripping the TDD severity test. Checks that expose
# third-party CONTENT are HIGH (= blocking); document-property / revision leads
# are MEDIUM.
_PREPUBLISH_SEVERITY = {
    "box_over_text":   "high",   # we drew a box over still-live text
    "text_layer":      "high",   # extractable text under a redaction signal
    "unapplied_redact":"high",   # marked but never applied -> text still present
    "embedded_files":  "high",   # an attachment can carry un-redacted source
    "acroform_values": "high",   # a form field holds data behind a flat-looking page
    "annotation_text": "high",   # a comment can carry PII
    "metadata":        "medium", # document properties (often our own author/tool)
    "incremental_save":"medium", # prior revision exists -> a lead, not a leak proof
}

# The honesty footer (design 1.5) -- redaction-failure classes with no reliable
# FOSS auto-detector, ALWAYS emitted so a clean report is NEVER read as "fully
# redacted". OCG/optional-content layer analysis is deferred this phase.
_CANNOT_CATCH = [
    "glyph-position / off-page / white-on-white text (extractable but not under a box)",
    "pixelation / blur / mosaic (mathematically reversible raster redaction)",
    "cross-version reconstruction (content recoverable from a prior revision; we "
    "flag that revisions exist, we do not diff or reconstruct)",
    "proportional-font / kerning side-channel reconstruction",
    "semantic reconstruction (inferring hidden content from surrounding context)",
    "OCG / optional-content (layer) hidden content (deferred this phase)",
]

_SHA256_CHUNK = 1 << 20  # 1 MiB streamed read


def _streamed_sha256(path) -> str:
    """Chunked sha256 of a file's bytes (streamed, never loading the whole file).
    Re-implemented INLINE here -- design 5 forbids Phase-7 code from importing
    scripts.ingest / scripts.recipe, so this trivial helper is duplicated to keep
    the modules decoupled (it is NOT imported from ingest)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_SHA256_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_check(name, fn, *, findings, checks_run, checks_unavailable, warnings):
    """Run ONE check ``fn`` (already a zero-arg callable), append its findings, and
    record it. DEGRADE-DON'T-CRASH: a ``CheckUnavailable`` OR any other exception
    -> a ``checks_unavailable`` entry + a warning; the check is NOT added to
    ``checks_run`` and the OTHER checks still run. Returns the check's findings
    (so the caller can derive signal_pages from box_over_text / unapplied_redact)."""
    try:
        got = fn()
    except CheckUnavailable as exc:
        checks_unavailable.append(str(exc))
        warnings.append(f"{name} check could not run: {exc}")
        return []
    except Exception as exc:  # noqa: BLE001 - one failing check must not sink the rest
        reason = f"{name}: {type(exc).__name__}: {exc}"
        checks_unavailable.append(reason)
        warnings.append(f"{name} check raised and was skipped ({reason})")
        return []
    checks_run.append(name)
    findings.extend(got)
    return got


def check_redactions(pdf_path, *, mode, vault_roots=()) -> RedactionReport:
    """Run all available checks over ONE PDF and assemble a ``RedactionReport``.

    Run order (the ONLY cross-check dependency): ``box_over_text`` +
    ``unapplied_redact`` run FIRST; the set of pages they flag becomes
    ``signal_pages``; ``text_layer`` then runs with that set (page-level
    co-occurrence, design 1.4); finally the remaining pikepdf / byte checks run.
    Each check is wrapped (a failure -> ``checks_unavailable`` + a warning; the
    others still run).

    ``mode``: ``"pre-publish"`` (inspect OUR output before release; sets
    ``safe_to_publish``) or ``"received"`` (inspect a response WE got;
    ``safe_to_publish`` is None). In ``pre-publish`` each finding's severity is set
    from the pinned ``_PREPUBLISH_SEVERITY`` map.

    FAIL-CLOSED (design 1.2/1.6): ``safe_to_publish`` is True ONLY when NO finding
    is ``"high"`` AND ``checks_unavailable`` is empty. ANY un-run check forces
    False with a ``"cannot certify: <check> did not run"`` warning -- a check that
    did not run never certifies the absence of what it checks for.

    ``vault_roots`` is accepted for interface parity with redact-output (the
    redaction-check report itself writes nothing here); reserved for callers that
    route the LOCAL report.
    """
    path = Path(pdf_path)
    findings: list[RedactionFinding] = []
    checks_run: list[str] = []
    checks_unavailable: list[str] = []
    warnings: list[str] = []

    # --- Phase 1: the signal-producing checks (box_over_text + unapplied_redact). ---
    # Looked up via the module namespace (not a captured local) so tests can
    # monkeypatch scripts.redaction_check.check_box_over_text.
    box_findings = _run_check(
        "box_over_text",
        lambda: check_box_over_text(path),
        findings=findings,
        checks_run=checks_run,
        checks_unavailable=checks_unavailable,
        warnings=warnings,
    )
    redact_findings = _run_check(
        "unapplied_redact",
        lambda: check_unapplied_redact(path),
        findings=findings,
        checks_run=checks_run,
        checks_unavailable=checks_unavailable,
        warnings=warnings,
    )
    # signal_pages = the union of pages flagged by box_over_text OR unapplied_redact
    # (page-level co-occurrence only; never cross-engine bbox math).
    signal_pages = {
        f.page
        for f in (*box_findings, *redact_findings)
        if f.page is not None
    }

    # --- Phase 2: text_layer, gated on the co-occurrence signal. ---
    _run_check(
        "text_layer",
        lambda: check_text_layer(path, signal_pages=signal_pages),
        findings=findings,
        checks_run=checks_run,
        checks_unavailable=checks_unavailable,
        warnings=warnings,
    )

    # --- Phase 3: the remaining independent checks (order-insensitive). ---
    for name, fn in (
        ("metadata", lambda: check_metadata(path)),
        ("incremental_save", lambda: check_incremental_save(path)),
        ("embedded_files", lambda: check_embedded_files(path)),
        ("acroform_values", lambda: check_acroform_values(path)),
        ("annotation_text", lambda: check_annotation_text(path)),
    ):
        _run_check(
            name,
            fn,
            findings=findings,
            checks_run=checks_run,
            checks_unavailable=checks_unavailable,
            warnings=warnings,
        )

    # --- pre-publish: pin each finding's severity from the map. ---
    if mode == "pre-publish":
        findings = [
            replace(f, severity=_PREPUBLISH_SEVERITY.get(f.check, f.severity))
            for f in findings
        ]

    # --- safe_to_publish (pre-publish only) -- FAIL-CLOSED. ---
    safe_to_publish: bool | None
    if mode == "pre-publish":
        any_high = any(f.severity == "high" for f in findings)
        if checks_unavailable:
            safe_to_publish = False
            for entry in checks_unavailable:
                # name the un-run check (entry is "<check>: <reason>").
                check_name = entry.split(":", 1)[0].strip()
                warnings.append(
                    f"cannot certify: {check_name} did not run "
                    f"(fail-closed; safe_to_publish forced False)"
                )
        else:
            safe_to_publish = not any_high
    else:
        safe_to_publish = None  # received mode has no pass/fail disposition

    return RedactionReport(
        source_path=str(path),
        source_sha256=_streamed_sha256(path),
        mode=mode,
        checks_run=checks_run,
        checks_unavailable=checks_unavailable,
        findings=findings,
        n_findings=len(findings),
        safe_to_publish=safe_to_publish,
        warnings=warnings,
        cannot_catch=list(_CANNOT_CATCH),
    )

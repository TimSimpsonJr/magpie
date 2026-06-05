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

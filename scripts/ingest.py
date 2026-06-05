"""The Docling EDGE of the Phase 6 ``ingest`` skill -- the ONLY docling importer.

Turns a PDF into a ``DoclingDocument`` JSON kept internally (never Markdown, so
every extracted element keeps its ``{page_no, bbox, charspan}`` provenance for
the Phase-8 citation anchor). A pure text-layer quality gate
(``scripts.ingest_gate``) decides native-vs-re-OCR BEFORE any OCR runs; degraded
/ handwriting pages are flagged for humans, never silently OCR'd as fact.

Engine at the edge (mirrors ``pii_sweep``'s lazy spaCy edge): docling is imported
LAZILY inside ``ingest()`` so importing this module stays cheap. ``sha256_file``
and ``IngestResult`` are PURE (no docling) -- the source-identity foundation.

The gate functions are imported INTO this module's namespace
(``from scripts.ingest_gate import diagnose_page, decide_doc``) so
``scripts.ingest.decide_doc`` is the monkeypatch target the review-doc test pins.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from scripts.ingest_gate import decide_doc, diagnose_page, DocDecision, PageDiagnosis

__all__ = ["sha256_file", "IngestResult", "ingest"]


# --------------------------------------------------------------------------- #
# Source identity (PURE -- no docling). Part of provenance (design §2.6): a
# citation must tie back to WHICH file produced the geometry.
# --------------------------------------------------------------------------- #

_SHA_CHUNK = 1 << 20  # 1 MiB streamed read -- never slurp a huge PDF into memory


def sha256_file(path: str | Path) -> str:
    """Streamed SHA-256 hex digest of a file's bytes (artifact identity).

    Reads in ``_SHA_CHUNK``-sized blocks so a multi-hundred-MB PDF hashes without
    being loaded whole into memory.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_SHA_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Output contract (design §5). JSON-able summary returned alongside the written
# DoclingDocument JSON. PURE dataclass -- no docling types leak into it.
# --------------------------------------------------------------------------- #


@dataclass
class IngestResult:
    """Provenance summary of one ingested PDF (design §5 output contract).

    ``to_dict()`` is JSON-able (plain str/int/bool/list/dict). ``per_page`` and
    ``bates`` are lists of plain dicts (already JSON-able), so a single
    ``asdict`` round-trips cleanly. ``docling_json_path`` is ALWAYS written --
    including on a ``review`` decision (the JSON is evidence for human
    inspection; ``trustworthy_for_extraction`` is then False).
    """

    source_path: str
    source_sha256: str                 # artifact identity (design §2.6)
    docling_json_path: str             # ALWAYS written, incl. on `review`
    schema_name: str                   # "DoclingDocument"
    schema_version: str                # e.g. "1.10.0"
    n_pages: int
    doc_decision: str                  # native|ocr_images|force_full_doc_ocr|review
    trustworthy_for_extraction: bool   # FALSE iff doc_decision == review
    ocr_engine_used: str               # "none" | "rapidocr" (Phase-6 scope)
    per_page: list[dict[str, Any]] = field(default_factory=list)
    bates: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-able dict of every field (no docling objects inside)."""
        return asdict(self)

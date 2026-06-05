"""Magpie Phase 8 -- the pure citation-anchor engine.

PURE: stdlib only (hashlib / json / dataclasses / re / typing). NO docling,
spaCy, pandas, numpy, or scripts.* sibling import. Deterministic -- it never
calls the clock / random / network; the record timestamp is an injected
parameter (default "") so the core stays golden-testable.

It carries the finding RECORD (``CitationRecord``), the v1 quote-contract
builder (``build_anchor``), and the fallback-chain RESOLVER (``resolve_anchor``)
that resolves over a plain ``json.load``-ed DoclingDocument dict. Mirrors
``ingest_gate``'s pure core. Source of truth:
``docs/plans/2026-06-05-magpie-phase8-investigate-design.md`` sections 2.1-2.4.
"""
from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# This record's OWN namespaced schema identity (design 2.1) -- not a bare
# ``version=1``. ``doc_schema_name``/``doc_schema_version`` carry the SOURCE
# DoclingDocument schema separately.
SCHEMA_NAME = "magpie-citation"
SCHEMA_VERSION = "1"

# Fixed-width context window (chars of .text) captured on each side of the quote
# at build time; required again at relocation so a short quote cannot relocate
# into the interior of a larger token elsewhere (design 2.2/2.4).
CONTEXT_WINDOW = 32

# The EXACTLY-10 public anchor keys (design section 3). An explicit ALLOWLIST
# literal -- never a denylist -- so a future raw field stays absent by default and
# char_start/char_end/n_prov/timestamp remain in to_dict() but NOT on the
# published surface.
_PUBLIC_ANCHOR_KEYS = (
    "doc_id",
    "page_no",
    "block_index",
    "block_self_ref",
    "text_hash",
    "bbox",
    "checker_level",
    "verifier_result",
    "schema_name",
    "schema_version",
)


def sha256_text(s: str) -> str:
    """Full, untruncated, UN-stripped sha256 hex of ``s`` (utf-8).

    A content-integrity + relocation hash. Deliberately different from
    ``pii_sweep.text_id`` (which strips + truncates to [:16] as a local join
    key): this is an INTEGRITY hash, so it is neither stripped nor truncated.
    """
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def block_index_of(self_ref: str) -> int:
    """``'#/texts/12' -> 12`` -- the int index of a DoclingDocument texts[] item."""
    return int(self_ref.rsplit("/", 1)[1])


@dataclass
class CitationRecord:
    """The finding record + citation anchor (design 2.1 fields, in order).

    Native-typed and JSON-able. ``claim_text`` / ``verbatim_quote`` /
    ``context_prefix`` / ``context_suffix`` are LOCAL-only raw (never published);
    ``public_anchor()`` exposes only the non-raw anchor + status surface.
    """

    claim_text: str
    verbatim_quote: str
    context_prefix: str
    context_suffix: str
    doc_id: str
    doc_schema_name: str
    doc_schema_version: str
    page_no: int
    block_index: int
    block_self_ref: str
    char_start: int
    char_end: int
    text_hash: str
    bbox: Dict[str, Any]
    n_prov: int
    verifier_result: str
    verifier_confidence: Optional[float]
    checker_level: str
    extractor_model: str
    prompt_version: str
    schema_name: str = SCHEMA_NAME
    schema_version: str = SCHEMA_VERSION
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """JSON-able dict of every field (raw + public), round-trips."""
        return dataclasses.asdict(self)

    def public_anchor(self) -> Dict[str, Any]:
        """EXACTLY the 10 approved public keys (design section 3), via an explicit
        allowlist -- carries NO raw (claim_text / verbatim_quote / context_*) and
        does NOT widen to char_start/char_end/n_prov/timestamp."""
        full = self.to_dict()
        return {k: full[k] for k in _PUBLIC_ANCHOR_KEYS}

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


class QuoteContractError(ValueError):
    """Raised by ``build_anchor`` when a ``verbatim_quote`` violates the v1 quote
    contract (design 2.4): empty/blank, multi-prov block, not present, not unique
    in the block, or not word-boundary-aligned at both edges."""


def _word_boundary_aligned(text: str, quote: str, start: int, end: int) -> bool:
    """True when [start, end) sits on token boundaries in ``text``.

    A boundary is OK on a side when either the quote's edge char or the adjacent
    .text char is a non-alphanumeric separator (or we are at the string edge).
    This stops a short alnum quote from matching the INTERIOR of a larger token
    (the ICE/polICE trap, design 2.2/2.4). Symmetric on left and right.
    """
    if quote == "":
        return False
    left_ok = start == 0 or not (text[start - 1].isalnum() and quote[0].isalnum())
    right_ok = end >= len(text) or not (text[end].isalnum() and quote[-1].isalnum())
    return left_ok and right_ok


def _find_all(text: str, sub: str) -> List[int]:
    """Every start index of ``sub`` in ``text`` (non-overlapping left-to-right)."""
    starts: List[int] = []
    if sub == "":
        return starts
    i = text.find(sub)
    while i != -1:
        starts.append(i)
        i = text.find(sub, i + len(sub))
    return starts


def build_anchor(
    block: Dict[str, Any],
    *,
    verbatim_quote: str,
    claim_text: str,
    doc_id: str,
    doc_schema_name: str,
    doc_schema_version: str,
    extractor_model: str = "",
    prompt_version: str = "",
    timestamp: str = "",
    context_window: int = CONTEXT_WINDOW,
) -> CitationRecord:
    """Stamp a ``CitationRecord`` for ``verbatim_quote`` in ``block``, enforcing the
    v1 quote contract (design 2.4).

    The quote must be non-empty/non-whitespace, sit in a SINGLE-prov block
    (``len(prov) == 1``), be a word-boundary-aligned UNIQUE substring of
    ``block["text"]``, and the offsets are computed into ``.text`` (NEVER
    ``prov.charspan`` -- the early Greenville validation showed 6/189 blocks had
    ``prov.charspan != [0, len(text))``). Geometry comes straight from
    ``prov[0]``. Raises ``QuoteContractError`` on any violation.
    """
    # 1. non-empty / non-whitespace.
    if not verbatim_quote.strip():
        raise QuoteContractError("empty/blank quote")

    # 2. single-prov block (design 2.3): geometry is faithful only for n_prov == 1.
    prov = block.get("prov") or []
    if len(prov) != 1:
        raise QuoteContractError("block is not single-prov")

    # 3. present + unique in this block's .text (NOT .orig, NOT prov.charspan).
    text = block["text"]
    starts = _find_all(text, verbatim_quote)
    if len(starts) == 0:
        raise QuoteContractError("quote not in block")
    if len(starts) > 1:
        raise QuoteContractError("quote not unique in block")

    # 4. word-boundary-aligned at both edges (the ICE/polICE guard).
    start = starts[0]
    end = start + len(verbatim_quote)
    if not _word_boundary_aligned(text, verbatim_quote, start, end):
        raise QuoteContractError("not word-boundary aligned")

    # 5. fixed-width context windows (the W3C TextQuoteSelector prefix/suffix),
    #    required again at relocation (design 2.2).
    context_prefix = text[max(0, start - context_window):start]
    context_suffix = text[end:end + context_window]

    # 6. integrity hash of the exact span + single-prov geometry from prov[0].
    prov0 = prov[0]
    return CitationRecord(
        claim_text=claim_text,
        verbatim_quote=verbatim_quote,
        context_prefix=context_prefix,
        context_suffix=context_suffix,
        doc_id=doc_id,
        doc_schema_name=doc_schema_name,
        doc_schema_version=doc_schema_version,
        page_no=prov0["page_no"],
        block_index=block_index_of(block["self_ref"]),
        block_self_ref=block["self_ref"],
        char_start=start,
        char_end=end,
        text_hash=sha256_text(verbatim_quote),
        bbox=prov0["bbox"],
        n_prov=1,
        verifier_result="indeterminate",
        verifier_confidence=None,
        checker_level="",
        extractor_model=extractor_model,
        prompt_version=prompt_version,
        schema_name=SCHEMA_NAME,
        schema_version=SCHEMA_VERSION,
        timestamp=timestamp,
    )


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

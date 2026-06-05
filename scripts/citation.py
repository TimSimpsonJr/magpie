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
    """Every start index of ``sub`` in ``text``, INCLUDING overlapping repeats.

    Advances the search by ONE (``start + 1``), not by ``len(sub)``: overlapping
    positions are genuinely distinct anchor positions, so they each count toward
    the uniqueness/ambiguity contract. ``"A A"`` in ``"A A A"`` therefore yields
    BOTH index 0 and index 2 (two occurrences -> not unique-in-block at build
    time; ambiguous at relocation time), never a single faux-unique hit.
    """
    starts: List[int] = []
    if sub == "":
        return starts
    i = text.find(sub)
    while i != -1:
        starts.append(i)
        i = text.find(sub, i + 1)
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


@dataclass
class ResolvedAnchor:
    """The outcome of resolving a ``CitationRecord`` against a (possibly
    re-ingested) DoclingDocument dict. ``level`` is the resolution tier; each tier
    DEGRADES precision (design 2.2). Geometry/offsets are populated only at the
    tier that can faithfully supply them, else ``None``."""

    level: str
    page_no: Optional[int]
    block_index: Optional[int]
    char_start: Optional[int]
    char_end: Optional[int]
    matched_text: Optional[str]
    bbox: Optional[Dict[str, Any]]
    n_matches: int = 0


def _geom(blk: Dict[str, Any]):
    """Single-prov geometry guard (design 2.3). Faithful ``(page_no, bbox)`` ONLY
    when the matched block is single-prov; a multi-prov block yields ``(None,
    None)`` (and its caller degrades the level) rather than a faux-precise
    ``prov[0]``."""
    prov = blk.get("prov") or []
    if len(prov) == 1:
        return prov[0].get("page_no"), prov[0].get("bbox")
    return None, None


def _is_single_prov(blk: Dict[str, Any]) -> bool:
    return len(blk.get("prov") or []) == 1


def _context_matches(text: str, start: int, end: int,
                     context_prefix: str, context_suffix: str) -> bool:
    """The stored windowed context must bound the candidate: the chars preceding
    the match END WITH ``context_prefix`` and the chars following it BEGIN WITH
    ``context_suffix`` (W3C TextQuoteSelector). An empty stored window matches
    trivially (str.endswith('')/startswith('') are True)."""
    return text[:start].endswith(context_prefix) and text[end:].startswith(context_suffix)


def resolve_anchor(record: CitationRecord, docling_json: Dict[str, Any]) -> ResolvedAnchor:
    """Resolve ``record`` against ``docling_json`` via the ordered fallback chain
    (design 2.2), STOPPING at the first hit:
    exact -> relocated/ambiguous -> block -> page -> unresolved.

    The single-prov geometry guard (design 2.3) applies on BOTH the exact and the
    relocated branches: a matched target block that is multi-prov degrades the
    level to ``block`` with page/bbox ``None`` rather than reporting a faux-precise
    ``prov[0]``. The relocated branch requires the stored context to match even
    for a LONE candidate.
    """
    texts: List[Dict[str, Any]] = docling_json.get("texts", [])
    bi = record.block_index

    # --- exact: stored block_index + offsets still slice the stored quote. ------
    if 0 <= bi < len(texts):
        blk = texts[bi]
        slice_ = blk.get("text", "")[record.char_start:record.char_end]
        if sha256_text(slice_) == record.text_hash:
            if _is_single_prov(blk):
                page_no, bbox = _geom(blk)
                return ResolvedAnchor(
                    level="exact", page_no=page_no, block_index=bi,
                    char_start=record.char_start, char_end=record.char_end,
                    matched_text=slice_, bbox=bbox, n_matches=1,
                )
            # multi-prov target: text matches but faithful geometry is impossible.
            return ResolvedAnchor(
                level="block", page_no=None, block_index=bi,
                char_start=None, char_end=None, matched_text=None,
                bbox=None, n_matches=1,
            )

    # --- relocated / ambiguous: search every block for a boundary-aligned, ------
    #     context-confirmed occurrence of the stored quote.
    quote = record.verbatim_quote
    candidates = []  # (block_index, start, end, block)
    for idx, blk in enumerate(texts):
        btext = blk.get("text", "")
        for start in _find_all(btext, quote):
            end = start + len(quote)
            if not _word_boundary_aligned(btext, quote, start, end):
                continue
            if not _context_matches(btext, start, end,
                                    record.context_prefix, record.context_suffix):
                continue
            if sha256_text(btext[start:end]) != record.text_hash:
                continue
            candidates.append((idx, start, end, blk))

    if len(candidates) == 1:
        idx, start, end, blk = candidates[0]
        if _is_single_prov(blk):
            page_no, bbox = _geom(blk)
            return ResolvedAnchor(
                level="relocated", page_no=page_no, block_index=idx,
                char_start=start, char_end=end,
                matched_text=blk.get("text", "")[start:end], bbox=bbox, n_matches=1,
            )
        # the unique relocated target is multi-prov: degrade, drop geometry.
        return ResolvedAnchor(
            level="block", page_no=None, block_index=idx,
            char_start=None, char_end=None, matched_text=None,
            bbox=None, n_matches=1,
        )
    if len(candidates) > 1:
        return ResolvedAnchor(
            level="ambiguous", page_no=None, block_index=None,
            char_start=None, char_end=None, matched_text=None,
            bbox=None, n_matches=len(candidates),
        )

    # --- block: stored block_index still in range, single-prov, page agrees. ----
    if 0 <= bi < len(texts):
        blk = texts[bi]
        if _is_single_prov(blk) and (blk.get("prov") or [])[0].get("page_no") == record.page_no:
            page_no, bbox = _geom(blk)
            return ResolvedAnchor(
                level="block", page_no=page_no, block_index=bi,
                char_start=None, char_end=None, matched_text=None,
                bbox=bbox, n_matches=0,
            )

    # --- page: the page_no still exists in the doc (compare as str AND int). -----
    pages = docling_json.get("pages", {}) or {}
    if record.page_no in pages or str(record.page_no) in pages:
        return ResolvedAnchor(
            level="page", page_no=record.page_no, block_index=None,
            char_start=None, char_end=None, matched_text=None,
            bbox=None, n_matches=0,
        )

    # --- unresolved: nothing localizes. -----------------------------------------
    return ResolvedAnchor(
        level="unresolved", page_no=None, block_index=None,
        char_start=None, char_end=None, matched_text=None,
        bbox=None, n_matches=0,
    )


def is_clean_citation(resolved: ResolvedAnchor) -> bool:
    """The clean-citation gate (design 2.2/4): passes ONLY at ``exact`` or unique
    ``relocated`` (single-prov, text-matched, UNIQUE). ``ambiguous`` / ``block`` /
    ``page`` / ``unresolved`` are degraded anchors -- never an auto-pass."""
    return resolved.level in ("exact", "relocated")

from __future__ import annotations

import hashlib
import dataclasses
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Task 2: Windowing + Span Dedup
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Span:
    """A named entity span with char offsets into the page text."""
    text: str
    label: str
    char_start: int
    char_end: int  # exclusive
    score: float


@dataclass(frozen=True)
class Window:
    """A sliding window into the page text with its absolute start offset."""
    text: str
    char_base: int  # absolute start offset in the page


def plan_windows(
    page_text: str,
    *,
    max_chars: int = 1400,
    overlap: int = 200,
) -> list[Window]:
    """Slide a window of up to max_chars across page_text with overlap chars shared.

    INVARIANT: for every returned Window w,
        page_text[w.char_base : w.char_base + len(w.text)] == w.text
    """
    if not page_text or not page_text.strip():
        return []

    n = len(page_text)

    if n <= max_chars:
        return [Window(text=page_text, char_base=0)]

    windows: list[Window] = []
    start = 0

    while start < n:
        # Determine raw end for this window
        raw_end = min(start + max_chars, n)

        if raw_end == n:
            # Last window: always take to the end
            end = n
        else:
            # Try to snap to a whitespace boundary in the last `overlap` chars
            # of the window, so we don't cut mid-word.
            search_start = max(start, raw_end - overlap)
            best = raw_end
            for i in range(raw_end, search_start - 1, -1):
                if i > start and page_text[i - 1] in (" ", "\n", "\t", "\r"):
                    best = i
                    break
            end = best

        window_text = page_text[start:end]
        windows.append(Window(text=window_text, char_base=start))

        if end == n:
            break

        # Advance: next window starts (end - overlap) chars ahead
        next_start = end - overlap
        # Guard against infinite loop
        if next_start <= start:
            next_start = start + 1
        start = next_start

    return windows


def dedup_spans(spans: list[Span]) -> list[Span]:
    """Deduplicate spans with conflict resolution by label.

    Rules:
    - Drop exact duplicates (same char_start, char_end, label).
    - For two spans with the SAME label whose char ranges overlap, keep the
      longer (char_end - char_start); tie -> higher score.
    - Spans with DIFFERENT labels that overlap are BOTH kept.
    - Return stable-sorted by (char_start, char_end, label).
    """
    if not spans:
        return []

    # Deduplicate exact triples first (char_start, char_end, label) keeping
    # the one with the highest score when scores differ.
    seen: dict[tuple[int, int, str], Span] = {}
    for s in spans:
        key = (s.char_start, s.char_end, s.label)
        if key not in seen or s.score > seen[key].score:
            seen[key] = s
    unique = list(seen.values())

    # Now resolve overlapping spans with the SAME label.
    # Group by label, then greedily remove dominated spans.
    def _overlaps(a: Span, b: Span) -> bool:
        """True iff a and b share at least one character position."""
        return a.char_start < b.char_end and b.char_start < a.char_end

    def _better(candidate: Span, existing: Span) -> bool:
        """True if candidate should replace existing."""
        clen = candidate.char_end - candidate.char_start
        elen = existing.char_end - existing.char_start
        if clen != elen:
            return clen > elen
        return candidate.score > existing.score

    # Collect spans grouped by label
    by_label: dict[str, list[Span]] = {}
    for s in unique:
        by_label.setdefault(s.label, []).append(s)

    survivors: list[Span] = []
    for label, group in by_label.items():
        # Iteratively resolve conflicts within this label
        # Use a simple O(n^2) approach (span counts are small in practice).
        resolved: list[Span] = []
        for candidate in group:
            dominated = False
            new_resolved: list[Span] = []
            for existing in resolved:
                if _overlaps(candidate, existing):
                    # Keep the better one
                    if _better(candidate, existing):
                        # candidate wins: drop existing, don't mark dominated
                        pass  # existing is dropped by not adding to new_resolved
                    else:
                        # existing wins
                        dominated = True
                        new_resolved.append(existing)
                else:
                    new_resolved.append(existing)
            if not dominated:
                new_resolved.append(candidate)
            resolved = new_resolved
        survivors.extend(resolved)

    # Stable sort by (char_start, char_end, label)
    survivors.sort(key=lambda s: (s.char_start, s.char_end, s.label))
    return survivors


# ---------------------------------------------------------------------------
# Task 3: Deterministic intermediate model + FtM-shaped mapping
# ---------------------------------------------------------------------------

def stable_id(*parts) -> str:
    """SHA-256 of pipe-joined string parts, first 40 hex chars."""
    payload = "|".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:40]


@dataclass(frozen=True)
class Node:
    """Intermediate entity node (FtM-shaped, no followthemoney import)."""
    id: str
    schema: str
    name: str
    label: str


@dataclass(frozen=True)
class Edge:
    """Intermediate relation edge (FtM-shaped, no followthemoney import)."""
    id: str
    schema: str
    head_id: str
    tail_id: str
    role: Optional[str]
    label: str


def make_node(span: Span, doc_id: str, namespace: str, taxonomy) -> Node:
    """Build a deterministic Node from a Span.

    Same name+schema in the same doc_id -> same id (within-doc dedup).
    Different doc_ids -> different ids (no cross-doc merge).
    """
    schema = taxonomy.ftm_schema_for(span.label)
    name = span.text.strip()
    id_ = stable_id(namespace, doc_id, schema, name.casefold())
    return Node(id=id_, schema=schema, name=name, label=span.label)


def make_edge(
    rel_label: str,
    head: Node,
    tail: Node,
    span_key: str,
    namespace: str,
    taxonomy,
) -> Optional[Edge]:
    """Build a deterministic Edge, or None if the pair is type-incompatible.

    Uses taxonomy.allowed() as the type-compatibility filter.
    """
    if not taxonomy.allowed(rel_label, head.label, tail.label):
        return None

    spec = taxonomy.relation_for(rel_label)
    if spec is not None:
        schema = spec.ftm_edge
        role = spec.role
    else:
        # Defensive: a label not in the taxonomy at all
        schema = "UnknownLink"
        role = rel_label

    id_ = stable_id(namespace, schema, head.id, tail.id, span_key)
    return Edge(
        id=id_,
        schema=schema,
        head_id=head.id,
        tail_id=tail.id,
        role=role,
        label=rel_label,
    )

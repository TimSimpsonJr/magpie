from __future__ import annotations

import hashlib
import dataclasses
import json
from dataclasses import dataclass, field
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


# ---------------------------------------------------------------------------
# Task 4: Statements, provenance, review queue, intermediate bundle
# ---------------------------------------------------------------------------

def statement_id(namespace, doc_id, page, char_start, char_end, target_id, prop) -> str:
    """Unique per mention: same args -> same id; differ by page -> different ids."""
    return stable_id(namespace, doc_id, page, char_start, char_end, target_id, prop)


@dataclass(frozen=True)
class Mention:
    """A raw extracted claim, pre-review."""
    target_id: str
    target_kind: str          # "entity" or "edge"
    schema: str
    prop: str
    value: str
    doc_id: str
    page: int
    char_start: int
    char_end: int
    model: str
    confidence: float


@dataclass(frozen=True)
class Statement:
    """An immutable reviewed (or pending) claim. Mutate via dataclasses.replace."""
    statement_id: str
    kind: str                 # "entity" or "relation"
    target_id: str
    target_kind: str
    schema: str
    prop: str
    value: str
    doc_id: str
    page: int
    char_start: int
    char_end: int
    model: str
    confidence: float
    decision: str = "pending"  # "pending" | "accepted" | "rejected" | "edited"
    reviewer: Optional[str] = None
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None


def build_statements(mentions: list, namespace: str) -> list:
    """Build one pending Statement per Mention."""
    results = []
    for m in mentions:
        sid = statement_id(namespace, m.doc_id, m.page, m.char_start, m.char_end, m.target_id, m.prop)
        kind = "entity" if m.target_kind == "entity" else "relation"
        results.append(Statement(
            statement_id=sid,
            kind=kind,
            target_id=m.target_id,
            target_kind=m.target_kind,
            schema=m.schema,
            prop=m.prop,
            value=m.value,
            doc_id=m.doc_id,
            page=m.page,
            char_start=m.char_start,
            char_end=m.char_end,
            model=m.model,
            confidence=m.confidence,
            decision="pending",
        ))
    return results


_VALID_DECIDE = {"accepted", "rejected"}


class ReviewQueue:
    """Ordered, mutable collection of Statements with review operations."""

    def __init__(self, statements: list) -> None:
        self._statements: list = list(statements)

    # --- read views ---

    def all_statements(self) -> list:
        return list(self._statements)

    def pending(self) -> list:
        return [s for s in self._statements if s.decision == "pending"]

    def accepted(self) -> list:
        return [s for s in self._statements if s.decision == "accepted"]

    def get(self, sid: str) -> Optional[Statement]:
        for s in self._statements:
            if s.statement_id == sid:
                return s
        return None

    # --- mutations ---

    def decide(self, sid: str, decision: str, reviewer: Optional[str] = None) -> None:
        if decision not in _VALID_DECIDE:
            raise ValueError("decision must be 'accepted' or 'rejected', got: %r" % decision)
        for i, s in enumerate(self._statements):
            if s.statement_id == sid:
                self._statements[i] = dataclasses.replace(s, decision=decision, reviewer=reviewer)
                return
        raise KeyError(sid)

    def edit(self, sid: str, new_value: str, reviewer: Optional[str] = None) -> Statement:
        # Find original (raises KeyError if missing)
        orig = self.get(sid)
        if orig is None:
            raise KeyError(sid)

        # Ordinal = 1 + number of existing statements whose supersedes == sid
        ordinal = 1 + sum(1 for s in self._statements if s.supersedes == sid)
        new_id = stable_id(sid, "edit", ordinal)

        # Mark original as edited
        for i, s in enumerate(self._statements):
            if s.statement_id == sid:
                self._statements[i] = dataclasses.replace(s, decision="edited", superseded_by=new_id)
                break

        # Build and append the replacement
        replacement = dataclasses.replace(
            orig,
            statement_id=new_id,
            value=new_value,
            decision="accepted",
            reviewer=reviewer,
            supersedes=sid,
            superseded_by=None,
        )
        self._statements.append(replacement)
        return replacement

    # --- serialization ---

    def to_jsonl(self) -> str:
        lines = [json.dumps(dataclasses.asdict(s)) for s in self._statements]
        return "\n".join(lines)

    @classmethod
    def from_jsonl(cls, text: str) -> "ReviewQueue":
        statements = []
        for line in text.splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            statements.append(Statement(**obj))
        return cls(statements)


def build_intermediate(
    queue: ReviewQueue,
    nodes: list,
    edges: list,
    *,
    namespace: str,
    source_doc_ids: list,
    schema_version: str = "1.0",
    created_with: Optional[dict] = None,
) -> tuple:
    """Build a reviewed intermediate bundle with graph-closure enforcement.

    Returns (bundle_dict, warnings_list).
    """
    accepted_stmts = queue.accepted()
    accepted_ids = {s.target_id for s in accepted_stmts}

    # Accepted nodes
    accepted_nodes = [n for n in nodes if n.id in accepted_ids]
    node_ids = {n.id for n in accepted_nodes}

    # Accepted edges with closure check
    warnings = []
    accepted_edges = []
    for edge in edges:
        if edge.id not in accepted_ids:
            continue
        if edge.head_id in node_ids and edge.tail_id in node_ids:
            accepted_edges.append(edge)
        else:
            warnings.append(
                "dropped edge %s (%s): endpoint not accepted" % (edge.id, edge.label)
            )

    # Provenance rows
    provenance = []
    for s in accepted_stmts:
        provenance.append({
            "statement_id": s.statement_id,
            "target_id": s.target_id,
            "target_kind": s.target_kind,
            "prop": s.prop,
            "value": s.value,
            "doc_id": s.doc_id,
            "page": s.page,
            "char_start": s.char_start,
            "char_end": s.char_end,
            "model": s.model,
            "confidence": s.confidence,
            "reviewed": True,
        })

    bundle = {
        "schema_version": schema_version,
        "dataset_namespace": namespace,
        "source_doc_ids": list(source_doc_ids),
        "nodes": [dataclasses.asdict(n) for n in accepted_nodes],
        "edges": [dataclasses.asdict(e) for e in accepted_edges],
        "provenance": provenance,
        "counts": {
            "nodes": len(accepted_nodes),
            "edges": len(accepted_edges),
            "provenance": len(provenance),
        },
        "created_with": created_with or {},
    }
    return (bundle, warnings)

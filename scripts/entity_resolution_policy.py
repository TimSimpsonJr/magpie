"""Phase 13a entity-resolution policy + id layer (pure stdlib core).

ASCII only. Windows-golden-testable: imports NO nomenklatura, neo4j, or
followthemoney. This is the deterministic policy + id layer Tasks 3 and 4
consume verbatim -- score buckets, threshold config, the stable canonical_id /
edge_id derivations, and the candidate / verdict dataclasses.

It reuses the Phase-12 id convention by importing the pure `stable_id` helper
from scripts.entity_extract. That module is itself pure stdlib (hashlib /
dataclasses / json / typing), so importing it keeps THIS module pure. This is a
plan-authorized exception to the usual "scripts import no neighbors" rule: it
guarantees the id convention matches Phase-12 byte-for-byte (a re-implemented
sha256 helper could silently drift). Reviewers: this single neighbor import is
intentional and load-bearing -- do not flag it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from scripts.entity_extract import stable_id


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolutionConfig:
    """Resolution thresholds + matcher selection (logged in run metadata).

    Defaults are CONSERVATIVE and config-overridable (design D8): auto-merge at
    >= 0.98, an actively reviewed band of [0.70, 0.98), keep-distinct below 0.70.
    These are placeholders pending calibration on real bundles -- config, not
    truth.
    """

    algorithm: str = "logic-v2"
    auto_threshold: float = 0.98
    review_floor: float = 0.70


# ---------------------------------------------------------------------------
# Stable, content-addressed ids (design D2)
# ---------------------------------------------------------------------------

def canonical_id(member_ids: list[str]) -> str:
    """Stable canonical id for a resolved cluster (design D2).

    = sha256("|".join(sorted(set(member_ids)))).hexdigest()[:40]

    Order-independent (sorted) and dedup-safe (set). A singleton cluster hashes
    its one member, so every node has a canonical_id immediately. Two different
    member-sets always yield different ids; a membership change yields a NEW id
    (a genuinely different cluster).

    Delegates to entity_extract.stable_id, which is the authoritative formula;
    the equation above documents its current behavior (kept honest by the
    singleton golden test, which pins the exact hex).
    """
    members = sorted(set(member_ids))
    if not members:
        raise ValueError("canonical_id requires at least one member id")
    return stable_id(*members)


def edge_id(
    schema: str,
    head_canonical: str,
    tail_canonical: str,
    role: Optional[str],
) -> str:
    """Stable id for a resolved edge (design D3).

    = sha256("|".join([schema, head_canonical, tail_canonical, role or ""]))[:40]

    role=None and role="" produce the SAME id, so edges without a role coalesce
    deterministically and MERGE stays idempotent.
    """
    return stable_id(schema, head_canonical, tail_canonical, role or "")


# ---------------------------------------------------------------------------
# Score bucketing (design D8)
# ---------------------------------------------------------------------------

def bucket(score: float, config: ResolutionConfig) -> str:
    """Map a match score to "auto" | "review" | "distinct".

    Both thresholds are INCLUSIVE: score >= auto_threshold -> "auto";
    score >= review_floor -> "review"; otherwise "distinct". Exactly the
    auto_threshold is "auto"; exactly the review_floor is "review". A NaN score
    is a pipeline bug (an uninitialized matcher float) -> raise rather than
    silently bucket it "distinct" (nan >= x is always False).
    """
    if math.isnan(score):
        raise ValueError("bucket() received a NaN score")
    if score >= config.auto_threshold:
        return "auto"
    if score >= config.review_floor:
        return "review"
    return "distinct"


# ---------------------------------------------------------------------------
# Candidate model (provenance-rich; design D6)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Mention:
    """A provenance ref to one source mention.

    Phase-12 offsets are PAGE-LOCAL, so page is required. `text` is the exact
    mention substring; the review packet uses it to <mark> the entity inside the
    hydrated snippet. char_end is exclusive.
    """

    doc_id: str
    page: int
    char_start: int
    char_end: int
    text: str = ""


@dataclass
class CandidateSide:
    """One side of a candidate pair (the left or right entity).

    Not frozen: it holds mutable lists/dicts. `mentions` is the FULL list (not
    just the top one) so the packet can surface more evidence on demand (D6);
    `properties` carries dict[str, list[str]] disambiguators (address / dob /
    badge / ...).
    """

    id: str
    caption: str
    schema: str
    aliases: list[str] = field(default_factory=list)
    properties: dict[str, list[str]] = field(default_factory=dict)
    mentions: list[Mention] = field(default_factory=list)


@dataclass
class Candidate:
    """A scored candidate pair (two sides + the match score)."""

    left: CandidateSide
    right: CandidateSide
    score: float

    @property
    def left_id(self) -> str:
        """The left entity's node id (read-only convenience)."""
        return self.left.id

    @property
    def right_id(self) -> str:
        """The right entity's node id (read-only convenience)."""
        return self.right.id


# ---------------------------------------------------------------------------
# Verdict (the HITL handback unit; design D6)
# ---------------------------------------------------------------------------

VALID_VERDICTS = {"merge", "distinct", "unsure"}


@dataclass(frozen=True)
class Verdict:
    """A human verdict on one candidate pair.

    verdict must be one of VALID_VERDICTS; junk is rejected at construction.
    """

    left_id: str
    right_id: str
    verdict: str

    def __post_init__(self) -> None:
        if self.verdict not in VALID_VERDICTS:
            raise ValueError(
                "verdict must be one of "
                + ", ".join(sorted(VALID_VERDICTS))
                + "; got "
                + repr(self.verdict)
            )

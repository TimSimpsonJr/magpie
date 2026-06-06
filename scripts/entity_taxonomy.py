"""Entity taxonomy config for Phase 12 entity extraction.

Pure stdlib module (no external dependencies). Defines entity types and
relation specs that map to FollowTheMoney (FtM) schemas.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EntityType:
    label: str
    ftm_schema: str


@dataclass(frozen=True)
class RelationSpec:
    label: str
    ftm_edge: str
    head_prop: str
    tail_prop: str
    allowed_head: frozenset
    allowed_tail: frozenset
    role: str | None = None


@dataclass
class Taxonomy:
    name: str
    entity_types: list
    relations: list

    def entity_labels(self) -> list[str]:
        return [et.label for et in self.entity_types]

    def ftm_schema_for(self, label: str) -> str:
        for et in self.entity_types:
            if et.label == label:
                return et.ftm_schema
        return "LegalEntity"

    def relation_for(self, label: str) -> RelationSpec | None:
        for rel in self.relations:
            if rel.label == label:
                return rel
        return None

    def allowed(self, rel_label: str, head_label: str, tail_label: str) -> bool:
        rel = self.relation_for(rel_label)
        if rel is None:
            return False
        return head_label in rel.allowed_head and tail_label in rel.allowed_tail


# Module-level label sets

NODE_LABELS = (
    "person",
    "government official",
    "organization",
    "government agency",
    "company",
    "attorney/legal counsel",
    "product/system/technology",
    "address",
    "jurisdiction",
    "vehicle",
)

ANNOTATION_LABELS = (
    "date",
    "phone",
    "email",
    "monetary amount",
    "case/docket number",
    "permit/license/contract number",
    "statute/regulation",
    "position/title",
)

# 18 EntityType definitions (NODE_LABELS + ANNOTATION_LABELS)
_ENTITY_TYPES = [
    EntityType("person", "Person"),
    EntityType("government official", "Person"),
    EntityType("organization", "Organization"),
    EntityType("government agency", "Organization"),
    EntityType("company", "Company"),
    EntityType("attorney/legal counsel", "Person"),
    EntityType("product/system/technology", "Thing"),
    EntityType("address", "Address"),
    EntityType("jurisdiction", "Address"),
    EntityType("vehicle", "Vehicle"),
    EntityType("date", "Thing"),
    EntityType("phone", "Thing"),
    EntityType("email", "Thing"),
    EntityType("monetary amount", "Thing"),
    EntityType("case/docket number", "Thing"),
    EntityType("permit/license/contract number", "Thing"),
    EntityType("statute/regulation", "Thing"),
    EntityType("position/title", "Thing"),
]

# Relations for GENERIC_TAXONOMY
_GENERIC_RELATIONS = [
    RelationSpec(
        label="employed by",
        ftm_edge="Employment",
        head_prop="employee",
        tail_prop="employer",
        allowed_head=frozenset({"person", "government official"}),
        allowed_tail=frozenset({"organization", "government agency", "company"}),
        role=None,
    ),
    RelationSpec(
        label="member of",
        ftm_edge="Membership",
        head_prop="member",
        tail_prop="organization",
        allowed_head=frozenset({"person", "government official"}),
        allowed_tail=frozenset({"organization", "government agency"}),
        role=None,
    ),
    RelationSpec(
        label="director/officer of",
        ftm_edge="Directorship",
        head_prop="director",
        tail_prop="organization",
        allowed_head=frozenset({"person", "government official"}),
        allowed_tail=frozenset({"organization", "government agency", "company"}),
        role=None,
    ),
    RelationSpec(
        label="owns/subsidiary of",
        ftm_edge="Ownership",
        head_prop="owner",
        tail_prop="asset",
        allowed_head=frozenset({"person", "company", "organization"}),
        allowed_tail=frozenset({"company"}),
        role=None,
    ),
    RelationSpec(
        label="represents/counsel for",
        ftm_edge="Representation",
        head_prop="agent",
        tail_prop="client",
        allowed_head=frozenset({"person", "attorney/legal counsel", "company"}),
        allowed_tail=frozenset({"person", "organization", "company", "government agency"}),
        role=None,
    ),
    RelationSpec(
        label="family of",
        ftm_edge="Family",
        head_prop="person",
        tail_prop="relative",
        allowed_head=frozenset({"person"}),
        allowed_tail=frozenset({"person"}),
        role=None,
    ),
    RelationSpec(
        label="associate of",
        ftm_edge="Associate",
        head_prop="person",
        tail_prop="associate",
        allowed_head=frozenset({"person"}),
        allowed_tail=frozenset({"person"}),
        role=None,
    ),
    RelationSpec(
        label="party to contract/procurement",
        ftm_edge="ContractAward",
        head_prop="authority",
        tail_prop="supplier",
        allowed_head=frozenset({"government agency", "organization"}),
        allowed_tail=frozenset({"company", "organization"}),
        role=None,
    ),
    RelationSpec(
        label="affiliated/linked",
        ftm_edge="UnknownLink",
        head_prop="subject",
        tail_prop="object",
        allowed_head=frozenset(NODE_LABELS),
        allowed_tail=frozenset(NODE_LABELS),
        role=None,
    ),
]

GENERIC_TAXONOMY = Taxonomy(
    name="generic",
    entity_types=list(_ENTITY_TYPES),
    relations=list(_GENERIC_RELATIONS),
)

_FLOCK_EXTRA_RELATION = RelationSpec(
    label="shares data with",
    ftm_edge="UnknownLink",
    head_prop="subject",
    tail_prop="object",
    allowed_head=frozenset({"government agency"}),
    allowed_tail=frozenset({"government agency"}),
    role="data-sharing",
)

FLOCK_PRESET = Taxonomy(
    name="surveillance/flock",
    entity_types=list(_ENTITY_TYPES),
    relations=list(_GENERIC_RELATIONS) + [_FLOCK_EXTRA_RELATION],
)


def resolve(name: str = "generic") -> Taxonomy:
    """Return the Taxonomy for the given name.

    Recognized names:
      "generic"             -> GENERIC_TAXONOMY
      "surveillance/flock"  -> FLOCK_PRESET
      "flock"               -> FLOCK_PRESET (alias)

    Raises ValueError for any unrecognized name.
    """
    if name == "generic":
        return GENERIC_TAXONOMY
    if name in ("surveillance/flock", "flock"):
        return FLOCK_PRESET
    raise ValueError(f"Unknown taxonomy name: {name!r}")

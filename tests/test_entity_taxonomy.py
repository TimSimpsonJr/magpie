"""Tests for scripts/entity_taxonomy.py -- Task 1, Phase 12."""
import pytest
from scripts.entity_taxonomy import (
    GENERIC_TAXONOMY,
    FLOCK_PRESET,
    resolve,
)


class TestEntityLabels:
    def test_length(self):
        assert len(GENERIC_TAXONOMY.entity_labels()) == 18

    def test_contains_person(self):
        assert "person" in GENERIC_TAXONOMY.entity_labels()

    def test_contains_government_agency(self):
        assert "government agency" in GENERIC_TAXONOMY.entity_labels()

    def test_contains_product_system_technology(self):
        assert "product/system/technology" in GENERIC_TAXONOMY.entity_labels()

    def test_contains_vehicle(self):
        assert "vehicle" in GENERIC_TAXONOMY.entity_labels()


class TestFtmSchemaFor:
    def test_person(self):
        assert GENERIC_TAXONOMY.ftm_schema_for("person") == "Person"

    def test_government_official(self):
        assert GENERIC_TAXONOMY.ftm_schema_for("government official") == "Person"

    def test_company(self):
        assert GENERIC_TAXONOMY.ftm_schema_for("company") == "Company"

    def test_government_agency(self):
        assert GENERIC_TAXONOMY.ftm_schema_for("government agency") == "Organization"

    def test_organization(self):
        assert GENERIC_TAXONOMY.ftm_schema_for("organization") == "Organization"

    def test_vehicle(self):
        assert GENERIC_TAXONOMY.ftm_schema_for("vehicle") == "Vehicle"

    def test_unknown_falls_back_to_legal_entity(self):
        assert GENERIC_TAXONOMY.ftm_schema_for("alien spacecraft") == "LegalEntity"


class TestAllowed:
    def test_member_of_person_government_agency_true(self):
        assert GENERIC_TAXONOMY.allowed("member of", "person", "government agency") is True

    def test_member_of_reversed_false(self):
        assert GENERIC_TAXONOMY.allowed("member of", "government agency", "person") is False

    def test_party_to_contract_true(self):
        assert GENERIC_TAXONOMY.allowed(
            "party to contract/procurement", "government agency", "company"
        ) is True

    def test_owns_subsidiary_company_to_gov_agency_false(self):
        assert GENERIC_TAXONOMY.allowed("owns/subsidiary of", "company", "government agency") is False

    def test_affiliated_linked_person_company_true(self):
        assert GENERIC_TAXONOMY.allowed("affiliated/linked", "person", "company") is True

    def test_nonexistent_relation_false(self):
        assert GENERIC_TAXONOMY.allowed("nonexistent relation", "person", "company") is False


class TestRelationFor:
    def test_member_of_ftm_edge(self):
        rel = GENERIC_TAXONOMY.relation_for("member of")
        assert rel is not None
        assert rel.ftm_edge == "Membership"

    def test_member_of_head_prop(self):
        rel = GENERIC_TAXONOMY.relation_for("member of")
        assert rel.head_prop == "member"

    def test_member_of_tail_prop(self):
        rel = GENERIC_TAXONOMY.relation_for("member of")
        assert rel.tail_prop == "organization"


class TestFlockPreset:
    def test_resolve_surveillance_flock(self):
        t = resolve("surveillance/flock")
        rel = t.relation_for("shares data with")
        assert rel is not None
        assert rel.role == "data-sharing"
        assert rel.ftm_edge == "UnknownLink"

    def test_resolve_flock_alias(self):
        t = resolve("flock")
        rel = t.relation_for("shares data with")
        assert rel is not None
        assert rel.role == "data-sharing"

    def test_generic_does_not_have_shares_data_with(self):
        assert GENERIC_TAXONOMY.relation_for("shares data with") is None

    def test_resolve_generic(self):
        t = resolve("generic")
        assert t.name == "generic"

    def test_resolve_unknown_raises(self):
        with pytest.raises(ValueError):
            resolve("nope")

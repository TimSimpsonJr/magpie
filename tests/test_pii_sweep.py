import pandas as pd
from scripts.pii_sweep import distinct_texts, text_id
from scripts.pii_sweep import DEFAULT_PII_PATTERNS, BROAD_ONLY_PATTERN_NAMES, _regex_hit
from scripts.pii_sweep import PersonFlags, sweep


class FakePersonClassifier:
    """Marker-driven: '<<OFFICIAL>>' -> official, '<<PERSON>>' -> unknown_role."""
    def __call__(self, texts):
        return [PersonFlags(official="<<OFFICIAL>>" in t,
                            unknown_role="<<PERSON>>" in t) for t in texts]


def test_distinct_texts_strips_outer_whitespace_and_drops_blanks():
    s = pd.Series(["John ", "John", "  ", "", None, "Mary", "Mary"])
    texts, counts = distinct_texts(s)
    by = dict(zip(texts, counts))
    assert by == {"John": 2, "Mary": 2}        # "John " collapses into "John"
    assert "" not in texts and "  " not in texts


def test_distinct_texts_preserves_case():
    texts, _ = distinct_texts(pd.Series(["ICE", "ice"]))
    assert set(texts) == {"ICE", "ice"}        # NER is case-sensitive; do NOT lowercase


def test_text_id_is_stable_truncated_and_strip_consistent():
    a, b = text_id("John Smith"), text_id("John Smith")
    assert a == b and len(a) == 16 and a != text_id("Jane Smith")
    assert text_id("John ") == text_id("John")   # strips like distinct_texts (join-safe)


def test_each_pattern_matches_positive_and_rejects_negative():
    pos = {
        "phone": "call 864-555-1212", "ssn": "ssn 123-45-6789",
        "email": "x@y.org", "dob_kw": "see DOB below",
        "alien_num": "A123456789", "driver_lic": "OLN# AB1234567",
        "race_sex": "susp B/M", "possible_birthdate": "04/12/1989",
    }
    for name, text in pos.items():
        assert _regex_hit(DEFAULT_PII_PATTERNS[name], text), name
    assert not _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "PARA1234567")  # word-boundary
    assert not _regex_hit(DEFAULT_PII_PATTERNS["ssn"], "order 12345 6789")


def test_alien_num_is_8_or_9_digits_not_more():
    assert _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "A12345678")        # 8
    assert _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "A123456789")       # 9
    assert not _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "A1234567")     # 7
    assert not _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "A1234567890")  # 10


def test_possible_birthdate_is_the_only_broad_only_pattern():
    # a bare date is also an incident date -> broad-only; every OTHER default
    # pattern is high-precision PII (counts toward the strict headline).
    assert BROAD_ONLY_PATTERN_NAMES == {"possible_birthdate"}
    assert {"ssn", "phone", "email", "alien_num", "dob_kw", "race_sex",
            "driver_lic"}.isdisjoint(BROAD_ONLY_PATTERN_NAMES)


def test_sweep_weights_distinct_by_counts():
    s = pd.Series(["ssn 123-45-6789"] * 50 + ["nothing here"] * 3)
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["categories"]["ssn"] == {"weighted": 50, "distinct": 1}
    assert r["n_rows"] == 53 and r["n_nonblank_rows"] == 53
    assert r["n_distinct_texts"] == 2


def test_sweep_classifies_official_vs_unknown_role():
    s = pd.Series(["<<OFFICIAL>> Sgt called", "<<PERSON>> a subject", "plain"])
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["categories"]["person_official"]["distinct"] == 1
    assert r["categories"]["person_unknown_role"]["distinct"] == 1

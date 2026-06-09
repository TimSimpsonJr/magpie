import json

import numpy as np
import pandas as pd
import pytest
from scripts.pii_sweep import distinct_texts, text_id
from scripts.pii_sweep import DEFAULT_PII_PATTERNS, BROAD_ONLY_PATTERN_NAMES, _regex_hit
from scripts.pii_sweep import PersonFlags, sweep
from scripts.recipe import _DEFAULT_PII_PATTERNS as RECIPE_PII  # Phase 4 defaults (regex STRINGS)


class FakePersonClassifier:
    """Marker-driven: '<<OFFICIAL>>' -> official, '<<PERSON>>' -> unknown_role."""
    def __call__(self, texts):
        return [PersonFlags(official="<<OFFICIAL>>" in t,
                            unknown_role="<<PERSON>>" in t) for t in texts]


class RecordingFakeClassifier:
    """Records the texts it was called with, so a test can PROVE NER ran over
    DISTINCT texts (n_distinct calls), not every row."""
    def __init__(self):
        self.seen = None
    def __call__(self, texts):
        self.seen = list(texts)
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
        "phone_compact": "5551234567",   # run-together 10-digit (broad-only lead)
    }
    for name, text in pos.items():
        assert _regex_hit(DEFAULT_PII_PATTERNS[name], text), name
    assert not _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "PARA1234567")  # word-boundary
    assert not _regex_hit(DEFAULT_PII_PATTERNS["ssn"], "order 12345 6789")


def test_driver_lic_requires_a_digit_in_the_body():
    # POSITIVES: a real license body always carries >=1 digit.
    for text in ("OLN# AB1234567", "DL 1234567", "OL A1234567"):
        assert _regex_hit(DEFAULT_PII_PATTERNS["driver_lic"], text), text
    # NEGATIVES: a bare 2-char prefix + an all-caps WORD (no digit) is prose,
    # not a license -- must not inflate the strict headline.
    for text in ("OL HENDERSON", "a DL ABCDEFG", "DL HENDERSON"):
        assert not _regex_hit(DEFAULT_PII_PATTERNS["driver_lic"], text), text


def test_alien_num_is_8_or_9_digits_not_more():
    assert _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "A12345678")        # 8
    assert _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "A123456789")       # 9
    assert not _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "A1234567")     # 7
    assert not _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], "A1234567890")  # 10


def test_alien_num_tolerates_separators_and_hash():
    # Real A-numbers in the corpus carry an optional '#' and a space/hyphen
    # separator (issue #19) -- the authoritative tally must catch them, not only
    # the run-together form. Width stays at the deliberate 8-9 digits.
    for text in ("A# 123456789", "A-123456789", "A 12345678", "A-12345678"):
        assert _regex_hit(DEFAULT_PII_PATTERNS["alien_num"], text), text


def test_broad_only_patterns_are_birthdate_race_sex_and_phone_compact():
    # a bare date is also an incident date, a 2-char demographic ratio collides
    # with prose ("H/M ratio"), and a bare 10-digit run is also a case #/badge ID
    # -> all three are broad-only leads. Every OTHER default pattern is
    # high-precision PII (counts toward the headline). phone_compact is NOT in the
    # strict set, so the disjoint guard still holds.
    assert BROAD_ONLY_PATTERN_NAMES == {"possible_birthdate", "race_sex", "phone_compact"}
    assert {"ssn", "phone", "email", "alien_num", "dob_kw",
            "driver_lic"}.isdisjoint(BROAD_ONLY_PATTERN_NAMES)


def test_race_sex_is_a_broad_lead_not_the_strict_headline():
    # a demographic ratio still FIRES as a category, but it is a medium-precision
    # lead -> it must land in broad, never strict.
    r = sweep(pd.Series(["susp B/M"]), person_classifier=FakePersonClassifier())
    assert r["categories"]["race_sex"]["distinct"] == 1
    assert r["exposure"]["strict"]["distinct"] == 0
    assert r["exposure"]["broad"]["distinct"] == 1


def test_phone_matches_parenthesized_area_code():
    # the STRICT phone pattern gained parenthesized area codes (high precision),
    # but a bare run-together 10-digit run must NOT match it -- that ambiguous
    # form is handled separately by the broad-only phone_compact lead.
    assert _regex_hit(DEFAULT_PII_PATTERNS["phone"], "(555) 123-4567") is True
    assert _regex_hit(DEFAULT_PII_PATTERNS["phone"], "(555)123-4567") is True
    assert _regex_hit(DEFAULT_PII_PATTERNS["phone"], "5551234567") is False


def test_phone_compact_is_a_broad_lead_not_strict():
    # a bare 10-digit run still FIRES as a category, but it is ambiguous (case
    # #/badge ID), so it lands in broad, never the strict headline.
    r = sweep(pd.Series(["5551234567"]), person_classifier=FakePersonClassifier())
    assert r["categories"]["phone_compact"]["distinct"] == 1
    assert r["exposure"]["strict"]["distinct"] == 0
    assert r["exposure"]["broad"]["distinct"] == 1


def test_phone_compact_is_a_lead_but_not_a_redaction_target():
    # phone_compact is redaction-EXEMPT: a bare 10-digit run (as likely a case
    # number as a phone) counts toward the broad exposure lead but must NOT, on
    # its own, become a redact-output target -- over-redacting accountability data
    # is the wrong direction. It enters local_texts only if the SAME text also
    # carries a real redaction trigger (here, an SSN).
    r = sweep(
        pd.Series(["call 5551234567", "ssn 123-45-6789 and 5551234567"]),
        person_classifier=FakePersonClassifier(),
        collect_local_texts=True,
    )
    assert r["exposure"]["broad"]["distinct"] == 2     # both rows are leads
    assert r["exposure"]["strict"]["distinct"] == 1    # only the ssn row is the headline
    redacted = {v["text"] for v in r["local_texts"].values()}
    assert "call 5551234567" not in redacted           # phone_compact-only -> NOT redacted
    assert "ssn 123-45-6789 and 5551234567" in redacted  # ssn (strict) -> IS a redaction target


def test_sweep_weights_distinct_by_counts():
    s = pd.Series(["ssn 123-45-6789"] * 50 + ["nothing here"] * 3)
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["categories"]["ssn"] == {"weighted": 50, "distinct": 1}
    assert r["n_rows"] == 53 and r["n_nonblank_rows"] == 53
    assert r["n_distinct_texts"] == 2


def test_sweep_excludes_blanks_from_the_weighting_base():
    # n_rows counts EVERY row; the weighting base (n_nonblank_rows) drops blank /
    # whitespace-only / None cells so they never dilute the exposure tally.
    s = pd.Series(["ssn 123-45-6789", "", "   ", None])
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["n_rows"] == 4
    assert r["n_nonblank_rows"] == 1


def test_sweep_classifies_official_vs_unknown_role():
    s = pd.Series(["<<OFFICIAL>> Sgt called", "<<PERSON>> a subject", "plain"])
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["categories"]["person_official"]["distinct"] == 1
    assert r["categories"]["person_unknown_role"]["distinct"] == 1


def test_exposure_strict_excludes_birthdate_and_officials():
    s = pd.Series([
        "ssn 123-45-6789",          # strict + broad
        "04/12/1989",               # BARE date -> possible_birthdate, broad ONLY
        "<<PERSON>> a name",        # unknown_role -> broad only
        "<<OFFICIAL>> Sgt Doe",     # official -> neither exposure metric
    ])
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["exposure"]["strict"]["distinct"] == 1            # only the ssn row
    assert r["exposure"]["broad"]["distinct"] == 3             # ssn + bare date + name
    assert r["categories"]["possible_birthdate"]["distinct"] == 1
    assert r["categories"]["person_official"]["distinct"] == 1  # reported, NOT exposure


def test_dob_keyword_is_strict_but_bare_date_is_not():
    # explicit "DOB" label = high-precision PII (strict); a bare date = a lead.
    r = sweep(pd.Series(["see DOB on file", "stopped 04/12/1989"]),
              person_classifier=FakePersonClassifier())
    assert r["categories"]["dob_kw"]["distinct"] == 1
    assert r["exposure"]["strict"]["distinct"] == 1            # the DOB-label row only
    assert r["exposure"]["broad"]["distinct"] == 2            # + the bare-date row


def test_mixed_official_plus_ssn_hits_strict_headline():
    s = pd.Series(["<<OFFICIAL>> Sgt Doe ssn 123-45-6789"])
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["exposure"]["strict"]["distinct"] == 1            # SSN is exposure regardless
    assert r["categories"]["person_official"]["distinct"] == 1


def test_classifier_runs_over_distinct_texts_not_every_row():
    rec = RecordingFakeClassifier()
    sweep(pd.Series(["ssn 123-45-6789"] * 50 + ["clean"] * 3), person_classifier=rec)
    assert rec.seen is not None and len(rec.seen) == 2         # n_distinct, NOT 53


def test_exposure_is_a_per_text_union_not_a_sum_of_categories():
    # one text matches TWO strict patterns: the row counts ONCE in exposure but
    # in BOTH categories -> sum(category weighted) != exposure weighted.
    s = pd.Series(["ssn 123-45-6789 ph 864-555-1212"] * 10)
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["categories"]["ssn"]["weighted"] == 10 and r["categories"]["phone"]["weighted"] == 10
    assert r["exposure"]["strict"]["weighted"] == 10           # union (10), NOT the sum (20)
    assert r["exposure"]["strict"]["distinct"] == 1


def test_weighting_matches_naive_per_row_scan():
    s = pd.Series(["A123456789"] * 7 + ["clean"] * 2 + ["x@y.org"] * 4)
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["exposure"]["broad"]["weighted"] == 11            # 7 A# rows + 4 email rows
    assert r["exposure"]["strict"]["weighted"] == 11
    for cat in r["categories"].values():
        assert cat["weighted"] >= cat["distinct"]


def test_efficiency_ratio():
    s = pd.Series(["dup"] * 6 + ["uniq"])
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["efficiency_ratio"] == 7 / 2                      # 7 nonblank rows / 2 distinct


def test_local_texts_off_by_default():
    r = sweep(pd.Series(["ssn 123-45-6789"]), person_classifier=FakePersonClassifier())
    assert "local_texts" not in r                          # raw PII never returned unless asked


def test_local_texts_opt_in_is_keyed_by_text_id_and_excludes_official_only():
    s = pd.Series(["ssn 123-45-6789", "<<OFFICIAL>> Sgt Doe"])
    r = sweep(s, person_classifier=FakePersonClassifier(), collect_local_texts=True)
    assert len(r["local_texts"]) == 1                      # official-only row is NOT a redaction target
    (tid, entry), = r["local_texts"].items()
    assert tid == text_id("ssn 123-45-6789")
    assert "ssn" in entry["categories"] and entry["count"] == 1


def test_text_id_collision_in_local_texts_raises_loudly(monkeypatch):
    # the local_texts builder must FAIL LOUD if two distinct PII-bearing texts
    # collide on the truncated text_id (a silent overwrite would drop a redaction
    # target). Force the collision by stubbing text_id to a constant.
    monkeypatch.setattr("scripts.pii_sweep.text_id", lambda t: "COLLIDE")
    s = pd.Series(["ssn 123-45-6789", "call 864-555-1212"])   # two distinct PII-bearing texts
    with pytest.raises(ValueError, match="collision"):
        sweep(s, person_classifier=FakePersonClassifier(), collect_local_texts=True)


def test_empty_input_is_safe_and_json_able():
    r = sweep(pd.Series([], dtype=object), person_classifier=FakePersonClassifier())
    assert r["efficiency_ratio"] is None                  # no fake 0
    assert r["exposure"]["strict"] == {"weighted": 0, "distinct": 0}
    json.dumps(r)                                          # native types only


def test_internal_bool_lists_do_not_leak_into_the_result():
    # _strict_bool / _broad_bool ARE json-serializable, so json.dumps() would not
    # catch a leak -- assert the keys are absent explicitly.
    r = sweep(pd.Series(["ssn 123-45-6789"]), person_classifier=FakePersonClassifier())
    assert "_strict_bool" not in r and "_broad_bool" not in r


def test_official_and_unknown_role_can_both_appear_in_one_text():
    r = sweep(pd.Series(["<<OFFICIAL>> Sgt Doe stopped <<PERSON>>"]),
              person_classifier=FakePersonClassifier())
    assert r["categories"]["person_official"]["distinct"] == 1
    assert r["categories"]["person_unknown_role"]["distinct"] == 1
    assert r["exposure"]["broad"]["distinct"] == 1         # unknown_role -> broad
    assert r["exposure"]["strict"]["distinct"] == 0        # no structured ID present


def test_non_string_cells_do_not_crash_and_carry_no_pii():
    s = pd.Series(["ssn 123-45-6789", 42, 3.14, None], dtype=object)
    r = sweep(s, person_classifier=FakePersonClassifier())
    assert r["n_nonblank_rows"] == 3                        # None dropped; 42/3.14 kept as text
    assert r["categories"]["ssn"]["distinct"] == 1
    assert r["exposure"]["strict"]["distinct"] == 1


def test_custom_broad_only_names_marks_a_custom_pattern_as_a_lead():
    # a custom `patterns` map marks its OWN ambiguous pattern broad-only via the
    # broad_only_names param (also how the Phase 11 compat profile works).
    import re as _re
    pats = {"ssn": DEFAULT_PII_PATTERNS["ssn"], "mycode": _re.compile(r"\bMC\d{4}\b")}
    s = pd.Series(["MC1234", "ssn 123-45-6789"])
    r = sweep(s, person_classifier=FakePersonClassifier(), patterns=pats,
              broad_only_names=frozenset({"mycode"}))
    assert r["exposure"]["strict"]["distinct"] == 1        # ssn row only (mycode is broad-only)
    assert r["exposure"]["broad"]["distinct"] == 2         # + the mycode lead
    assert r["categories"]["mycode"]["distinct"] == 1


def test_overlap_patterns_stay_consistent_with_recipe_check_pii():
    """Decoupled modules (no imports either way); this tripwire fires if the
    SHARED-INTENT patterns silently diverge. recipe stores regex STRINGS,
    pii_sweep COMPILED -> compare `.pattern` to the string. recipe's `a_number`
    is pii_sweep's `alien_num` (different key, same concept; both the same
    separator-tolerant 8-9-digit A-number pattern)."""
    overlap = {"ssn": "ssn", "phone": "phone", "email": "email", "alien_num": "a_number"}
    for sweep_key, recipe_key in overlap.items():
        assert DEFAULT_PII_PATTERNS[sweep_key].pattern == RECIPE_PII[recipe_key], sweep_key


@pytest.fixture(scope="module")
def spacy_classifier():
    pytest.importorskip("en_core_web_lg")     # CI without the 400MB model skips cleanly
    from scripts.pii_sweep import SpacyPersonClassifier
    return SpacyPersonClassifier


@pytest.mark.spacy
def test_real_ner_finds_person_presence(spacy_classifier):
    flags = spacy_classifier()(["John Smith was pulled over", "vehicle of interest"])
    assert flags[0].unknown_role and not flags[0].official     # bare name -> unknown_role
    assert not flags[1].unknown_role and not flags[1].official # no person


@pytest.mark.spacy
def test_title_prefix_marks_official(spacy_classifier):
    (f,) = spacy_classifier()(["Officer Ramirez requested backup"])
    assert f.official and not f.unknown_role                   # title prefix -> official


@pytest.mark.spacy
def test_official_names_lexicon_marks_untitled_official(spacy_classifier):
    clf = spacy_classifier(official_names={"dana wheeler"})  # built from the searcher field
    (f,) = clf(["Dana Wheeler ran the plate"])
    assert f.official


def test_norm_name_tokens_keeps_internal_punctuation_drops_badge():
    from scripts.pii_sweep import _norm_name_tokens            # PURE -- no model needed
    assert _norm_name_tokens("O'Brien") == frozenset({"o'brien"})
    assert _norm_name_tokens("Anne-Marie Diaz, #4471") == frozenset({"anne-marie", "diaz"})


def test_empty_official_names_are_filtered_from_the_lexicon():
    # PURE -- constructing the classifier does NOT load the model. The `if toks`
    # guard is load-bearing: a badge-only / empty official name normalizes to
    # frozenset(), and frozenset() <= span_tokens is ALWAYS True, which would mark
    # EVERY person official and silently zero the exposure headline.
    from scripts.pii_sweep import SpacyPersonClassifier
    assert SpacyPersonClassifier(official_names=["#4471", ""])._lexicon == frozenset()


@pytest.mark.spacy
def test_sweep_wires_official_names_through_to_default_classifier(spacy_classifier):
    # the LAZY default path: sweep() must build SpacyPersonClassifier(official_names=...)
    r = sweep(pd.Series(["Dana Wheeler ran the plate"]), official_names={"dana wheeler"})
    assert r["categories"]["person_official"]["distinct"] == 1
    assert r["categories"]["person_unknown_role"]["distinct"] == 0


@pytest.mark.spacy
def test_sweep_lazy_default_accepts_pandas_numpy_official_names(spacy_classifier):
    # SKILL.md tells callers to pass the structured searcher/user column's DISTINCT
    # values as official_names (e.g. series.unique()). The lazy default path must
    # NOT evaluate the truthiness of that container: a raw pd.Series (any length)
    # and a multi-element numpy ndarray both raise "truth value ... is ambiguous"
    # under the old `frozenset(official_names or ())`. Both must wire the lexicon
    # through cleanly so the untitled official is attributed, not counted as PII.
    text = pd.Series(["Dana Wheeler ran the plate"])
    # (a) a raw pd.Series of distinct names -- ALWAYS raises on the old code.
    r_series = sweep(text, official_names=pd.Series(["dana wheeler"]))
    assert r_series["categories"]["person_official"]["distinct"] == 1
    # (b) a numpy ndarray of distinct names (Series(...).unique() shape) with >1
    #     element -- raises on the old code; a true distinct-value input.
    names = pd.Series(["dana wheeler", "amy adams"], dtype=object).unique()
    assert isinstance(names, np.ndarray)        # guard: this IS the ndarray path
    r_ndarray = sweep(text, official_names=names)
    assert r_ndarray["categories"]["person_official"]["distinct"] == 1


from scripts.pii_sweep import person_role_in_span, OFFICIAL_TITLES

def test_person_role_official_by_lexicon():
    officials = frozenset({frozenset({"dana", "wheeler"})})
    assert person_role_in_span("Dana Wheeler", [], officials=officials) == "official"

def test_person_role_official_by_title_prefix():
    assert person_role_in_span("Ramirez", ["officer"], officials=frozenset()) == "official"

def test_person_role_involved_keep_list():
    involved = frozenset({frozenset({"john", "doe"})})
    assert person_role_in_span("John Doe", [], officials=frozenset(),
                               involved=involved) == "involved"

def test_person_role_uninvolved_default():
    assert person_role_in_span("Some Bystander", [], officials=frozenset()) == "uninvolved"

def test_person_role_official_beats_involved():
    toks = frozenset({frozenset({"pat", "lee"})})
    assert person_role_in_span("Pat Lee", [], officials=toks, involved=toks) == "official"

# --- Boundary DRIFT guards: pin the CLASSIFIER semantics the refactor must keep.
# Model-free (a fake nlp/doc), so they run offline. They PASS both BEFORE and AFTER
# the refactor; if the refactor widens/narrows the <=2 lookback window or drops the
# caller-side len<=2 skip, they fail.
class _FakeTok:
    def __init__(self, text): self.text = text
class _FakeEnt:
    def __init__(self, text, start, label="PERSON"):
        self.text, self.start, self.label_ = text, start, label
class _FakeDoc:
    def __init__(self, toks, ents):
        self._toks = [_FakeTok(t) for t in toks]; self.ents = ents
    def __getitem__(self, i): return self._toks[i]

def _flags_for(doc):
    from scripts.pii_sweep import SpacyPersonClassifier
    c = SpacyPersonClassifier(official_names=())
    c._nlp = type("N", (), {"pipe": lambda self, t, batch_size=256: [doc]})()  # bypass _load
    return c([" "])[0]

def test_title_lookback_window_is_at_most_two_tokens():
    from scripts.pii_sweep import PersonFlags
    ok = _FakeDoc(["the", "sgt", "x", "Ramirez"], [_FakeEnt("Ramirez", 3)])
    assert _flags_for(ok) == PersonFlags(official=True, unknown_role=False)
    far = _FakeDoc(["sgt", "on", "duty", "Ramirez"], [_FakeEnt("Ramirez", 3)])
    assert _flags_for(far) == PersonFlags(official=False, unknown_role=True)

def test_caller_side_len_le_2_person_is_skipped():
    from scripts.pii_sweep import PersonFlags
    doc = _FakeDoc(["Li", "ran"], [_FakeEnt("Li", 0)])     # 2-char PERSON ent
    assert _flags_for(doc) == PersonFlags(official=False, unknown_role=False)

"""TDD for scripts.redact_output -- the redact-output skill (Phase 7).

redact-output redacts UNINVOLVED third-party PII (PERSON names -> initials;
structured PII -> typed placeholders) for PUBLISHED artifacts, while the FULL
un-redacted exhibit is written to a LOCAL non-vault path. It consumes Phase-5
``pii_sweep``'s opt-in ``local_texts`` ({text_id: {text, count, categories}}).

PUBLISH-PATH CONTRACT (the load-bearing safety invariant): only ``redact_text``
and ``redact_note`` produce PUBLISHED strings, and neither carries a ``text_id``
or a raw matched text. ``redact_local_texts``'s map and ``write_local_exhibit``'s
CSV are the ONLY surfaces that carry text_id / raw text.

Importing ``scripts.redact_output`` must stay ML-free: spaCy loads only when the
default ``SpacyPersonSpans`` actually runs (a lazy edge), never at import.

The core is golden-tested with a FAKE person-span provider (no 400MB model), the
same discipline as pii_sweep's fake-classifier tests. Every fixture / string is
ASCII-only (SDD content-filter rule).
"""
import csv
import sys

import pytest

from scripts.redact_output import redact_text


class FakeSpans:
    """Injectable ``person_spans`` seam: returns the PERSON ent spans for a text
    as ``(span_text, start, end, preceding_token_texts)`` tuples -- the same
    shape the lazy default ``SpacyPersonSpans`` yields, but model-free."""

    def __init__(self, ents):
        self.ents = ents

    def __call__(self, text):
        return self.ents


# --------------------------------------------------------------------------- #
# Task 5: redact_text core (NER + regex, policy, spans).
# --------------------------------------------------------------------------- #


def test_redacts_uninvolved_name_to_initials():
    ents = [("John Q Public", 0, 13, [])]
    out = redact_text("John Q Public stopped here", person_spans=FakeSpans(ents))
    assert out.startswith("J.Q.P.")
    assert "John" not in out


def test_keeps_official_and_involved():
    ents = [("Officer Ramirez", 0, 15, ["Officer"])]  # title-prefixed -> official
    out = redact_text("Officer Ramirez ran the plate", person_spans=FakeSpans(ents))
    assert "Ramirez" in out
    invo = [("Jane Subject", 0, 12, [])]
    out2 = redact_text(
        "Jane Subject paid", person_spans=FakeSpans(invo), keep_names=["Jane Subject"]
    )
    assert "Jane Subject" in out2


def test_masks_structured_pii_typed():
    out = redact_text("call 555-123-4567 ssn 123-45-6789", person_spans=FakeSpans([]))
    assert "[PHONE]" in out and "[SSN]" in out


def test_short_name_is_redacted_no_len_skip():
    out = redact_text("Li was here", person_spans=FakeSpans([("Li", 0, 2, [])]))
    assert "L." in out and "Li " not in out


def test_overlap_longer_person_span_wins_contained_pii_dropped():
    # PERSON span "Sam 01/02/1990" (0..14) CONTAINS the date; longer span wins,
    # the contained date is NOT separately masked. Initials = "S." (Sam's S; the
    # date token has no alpha char -> contributes nothing). EXACT output pins it.
    ents = [("Sam 01/02/1990", 0, 14, [])]
    out = redact_text("Sam 01/02/1990 noted", person_spans=FakeSpans(ents))
    assert out == "S. noted"  # no [DOB]; contained span dropped; offsets stable


def test_right_to_left_offset_stability_multi_span():
    # two non-overlapping spans (name early, phone late): right-to-left application
    # must replace BOTH correctly without the early replacement shifting the late
    # offset. EXACT output pins it.
    ents = [("John Public", 0, 11, [])]
    out = redact_text("John Public at 555-123-4567 today", person_spans=FakeSpans(ents))
    assert out == "J.P. at [PHONE] today"


def test_typed_placeholders_cover_each_kind():
    # Each structured-PII kind gets a TYPED placeholder (not a blanket [REDACTED]).
    cases = {
        "[SSN]": "ssn 123-45-6789",
        "[PHONE]": "call 555-123-4567",
        "[EMAIL]": "mail a@b.org",
        "[DOB]": "see DOB here",
        "[A-NUMBER]": "alien A123456789",
        "[DL]": "lic OLN# AB1234567",
    }
    for placeholder, text in cases.items():
        out = redact_text(text, person_spans=FakeSpans([]))
        assert placeholder in out, (placeholder, out)


def test_multiple_uninvolved_names_all_initialed():
    ents = [("Anne Marie", 0, 10, []), ("Bob Cobb", 15, 23, [])]
    out = redact_text("Anne Marie and Bob Cobb left", person_spans=FakeSpans(ents))
    assert out == "A.M. and B.C. left"


# --------------------------------------------------------------------------- #
# Import-purity guard: importing redact_output must NOT pull spaCy.
# --------------------------------------------------------------------------- #


def test_import_redact_output_does_not_import_spacy():
    # The default person-span provider is a LAZY spaCy edge; merely importing the
    # module (and running the fake-span core) must not load spaCy.
    assert "scripts.redact_output" in sys.modules
    assert "spacy" not in sys.modules

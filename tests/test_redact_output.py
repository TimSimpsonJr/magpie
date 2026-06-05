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

from scripts.redact_output import (
    redact_local_texts,
    redact_note,
    redact_text,
    write_local_exhibit,
)


class FakeSpans:
    """Injectable ``person_spans`` seam: returns the PERSON ent spans for a text
    as ``(span_text, start, end, preceding_token_texts)`` tuples -- the same
    shape the lazy default ``SpacyPersonSpans`` yields, but model-free."""

    def __init__(self, ents):
        self.ents = ents

    def __call__(self, text):
        return self.ents


class NameFinder:
    """Model-free ``person_spans`` for the multi-text paths (redact_local_texts /
    redact_note). Configured with PERSON name strings; on each call it scans the
    GIVEN text for every configured name and reports its occurrences as PERSON
    ent spans (empty preceding tokens). Works no matter which sub-text
    redact_text/redact_note hands it."""

    def __init__(self, names):
        self.names = list(names)

    def __call__(self, text):
        ents = []
        for name in self.names:
            start = text.find(name)
            while start != -1:
                ents.append((name, start, start + len(name), []))
                start = text.find(name, start + 1)
        return ents


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
    # module must not load spaCy. Checked in a FRESH subprocess: in the shared
    # full-suite interpreter an earlier (spacy-marked) test loads spaCy, which
    # would pollute sys.modules and make an in-process check a false failure. A
    # clean subprocess proves the IMPORT itself stays ML-free.
    import subprocess
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    code = (
        "import sys; import scripts.redact_output; "
        "sys.exit(1 if 'spacy' in sys.modules else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(repo_root),
        capture_output=True,
    )
    assert result.returncode == 0, (
        "importing scripts.redact_output pulled spaCy at import time "
        f"(stderr: {result.stderr.decode('utf-8', 'replace')})"
    )


# --------------------------------------------------------------------------- #
# Task 6: redact_local_texts / redact_note / write_local_exhibit + vault guard.
# --------------------------------------------------------------------------- #


def test_redact_local_texts_is_local_only_map():
    lt = {
        "abc123": {
            "text": "John Doe stopped",
            "count": 3,
            "categories": ["person_unknown_role"],
        }
    }
    m = redact_local_texts(lt, person_spans=NameFinder(["John Doe"]))
    assert set(m.keys()) == {"abc123"}  # text_id key is LOCAL (feeds exhibit only)
    assert "John" not in m["abc123"]
    assert m["abc123"].startswith("J.D.")


def test_redact_note_replaces_known_text_no_textid_no_raw():
    lt = {
        "abc123": {
            "text": "John Doe stopped",
            "count": 1,
            "categories": ["person_unknown_role"],
        }
    }
    note = "Per the log, John Doe stopped near 5th St. Officer Ruiz responded."
    out = redact_note(note, lt, person_spans=NameFinder(["John Doe", "Ruiz"]))
    assert "abc123" not in out and "John Doe" not in out
    # analyst narrative untouched: redact_note only replaces KNOWN flagged texts,
    # so "Officer Ruiz" (never a flagged local_text) and "5th St" survive.
    assert "Officer Ruiz" in out and "5th St" in out


def test_redact_note_overlapping_known_texts_longest_first_no_raw_suffix():
    # two known flagged texts where one is a prefix of the other in the note;
    # longest-match-first + right-to-left must leave NO raw PII suffix.
    lt = {
        "a": {"text": "John Doe", "count": 1, "categories": ["person_unknown_role"]},
        "b": {
            "text": "John Doe Jr DOB on file",
            "count": 1,
            "categories": ["person_unknown_role", "dob_kw"],
        },
    }
    note = "Subject John Doe Jr DOB on file was logged."
    out = redact_note(note, lt, person_spans=NameFinder(["John Doe Jr", "John Doe"]))
    assert "John Doe" not in out and "DOB on file" not in out  # no raw suffix survives


def test_redact_note_emits_no_textid_and_no_raw_flagged_substring():
    # PUBLISH-PATH CONTRACT: a published note carries neither a text_id key nor
    # any original flagged substring.
    lt = {
        "deadbeefdeadbeef": {
            "text": "Jane Roe SSN 123-45-6789",
            "count": 2,
            "categories": ["person_unknown_role", "ssn"],
        }
    }
    note = "Filed by analyst. Jane Roe SSN 123-45-6789 was in the reason field."
    out = redact_note(note, lt, person_spans=NameFinder(["Jane Roe"]))
    assert "deadbeefdeadbeef" not in out
    assert "Jane Roe" not in out and "123-45-6789" not in out
    assert "[SSN]" in out  # structured PII still typed-masked


def test_redact_text_output_carries_no_textid_no_raw_name():
    # The CORE published surface likewise leaks neither the id nor the raw name.
    raw = "Sam Spade"
    tid = "0123456789abcdef"
    out = redact_text(raw + " here", person_spans=NameFinder([raw]))
    assert tid not in out and raw not in out and "Sam" not in out


def _exhibit_lt():
    return {
        "abc123": {
            "text": "John Doe stopped",
            "count": 3,
            "categories": ["person_unknown_role"],
        },
        "def456": {
            "text": "call 555-123-4567",
            "count": 1,
            "categories": ["phone"],
        },
    }


def test_write_local_exhibit_outside_vault(tmp_path):
    exhibit_dir = tmp_path / "exhibits"
    exhibit_dir.mkdir()
    p = write_local_exhibit(
        _exhibit_lt(), exhibit_dir, vault_roots=[tmp_path / "vault"]
    )
    assert p.exists()
    body = p.read_text(encoding="utf-8")
    assert "John Doe" in body  # FULL un-redacted, LOCAL
    assert "abc123" in body  # the text_id lives here (local CSV only)
    # CSV is parseable with the documented header.
    rows = list(csv.DictReader(body.splitlines()))
    assert {"text_id", "text", "count", "categories"} <= set(rows[0].keys())


def test_write_local_exhibit_redacted_view(tmp_path):
    exhibit_dir = tmp_path / "exhibits"
    exhibit_dir.mkdir()
    p = write_local_exhibit(
        _exhibit_lt(),
        exhibit_dir,
        vault_roots=[tmp_path / "vault"],
        redacted=True,
        person_spans=NameFinder(["John Doe"]),
    )
    body = p.read_text(encoding="utf-8")
    assert "John Doe" not in body  # redacted view: names initialed
    assert "J.D." in body and "[PHONE]" in body


def test_write_local_exhibit_raises_inside_vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / "ex").mkdir(parents=True)
    with pytest.raises(ValueError):
        write_local_exhibit(_exhibit_lt(), vault / "ex", vault_roots=[vault])


def test_write_local_exhibit_raises_on_symlink_into_vault(tmp_path):
    # a link that RESOLVES into the vault must be rejected (Path.resolve()
    # collapses the symlink before the containment check).
    vault = tmp_path / "vault"
    vault.mkdir()
    link = tmp_path / "exhibits"  # looks outside the vault by name...
    try:
        link.symlink_to(vault, target_is_directory=True)  # ...but resolves inside it
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform/run")
    with pytest.raises(ValueError):
        write_local_exhibit(_exhibit_lt(), link, vault_roots=[vault])

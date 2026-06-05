"""TDD for scripts.ingest_gate -- the PURE text-layer quality gate.

The gate is golden-testable with NO docling/PDF/model: it imports only stdlib.
Tests inject a tiny synthetic wordlist (``SMALL_WL``) so the pure suite never
reads the bundled big file (the one exception, ``load_default_wordlist``, is
exercised separately and is the only place the file is touched).
"""
import pytest

from scripts.ingest_gate import (
    PageDiagnosis, DocDecision,
    alphabetic_tokens, wordlist_hit_rate, char_density_ok, load_default_wordlist,
)

SMALL_WL = frozenset({"the", "police", "department", "search", "reason", "vehicle",
                      "officer", "requested", "record", "this", "contains", "data"})


def test_alphabetic_tokens_splits_and_lowercases():
    toks = alphabetic_tokens("The Police, Dept-99 searched 2 cars!")
    # numbers and pure-digit tokens dropped; words lowercased; hyphen splits
    assert toks == ["the", "police", "dept", "searched", "cars"]


def test_wordlist_hit_rate_fraction_in_list():
    # 3 of 4 alpha tokens are in SMALL_WL
    rate = wordlist_hit_rate("the police vehicle xqzklmn", SMALL_WL)
    assert rate == pytest.approx(3 / 4)


def test_wordlist_hit_rate_none_when_no_tokens():
    assert wordlist_hit_rate("123 456 !!!", SMALL_WL) is None  # no alpha tokens


def test_char_density_flags_symbol_garbage():
    assert char_density_ok("Normal readable sentence with words.") is True
    # guard 1 (letter-ratio floor): the EXACT latin-1 garbled_pdf fixture text --
    # a wall of symbols/digits with essentially no letters -- MUST fail (ties the
    # density constants to the Task-6 fixture so the garbled e2e is deterministic).
    assert char_density_ok(";;;;;;;;;;;;;;;; ################ %%%%%%%%%%%% 8492037184920371 @@@@@@@@@@@@ ") is False
    # guard 2 (run-length): good letters but one absurd non-letter run
    assert char_density_ok("the police ;;;;;;;;;;;;;;;;;;;;;;;;;;;; department record") is False


def test_enums_have_the_documented_members():
    assert {d.value for d in PageDiagnosis} == {
        "native_ok", "image_only", "garbled_text", "uncertain_review"}
    assert {d.value for d in DocDecision} == {
        "native", "ocr_images", "force_full_doc_ocr", "review"}


def test_default_wordlist_loads_and_is_lowercased():
    wl = load_default_wordlist()
    assert "the" in wl and len(wl) > 200 and all(w == w.lower() for w in wl)


# --------------------------------------------------------------------------- #
# Task 2: diagnose_page -- per-page diagnosis (golden; inject SMALL_WL).
# --------------------------------------------------------------------------- #

from scripts.ingest_gate import diagnose_page, PageDiagnosis as PD

def D(text, **kw): return diagnose_page(text, wordlist=SMALL_WL, **kw)

def test_clean_native_text_is_native_ok():
    assert D("The police department search reason vehicle record.") == PD.native_ok

def test_empty_or_near_empty_is_image_only():
    assert D("") == PD.image_only
    assert D("   \n  ") == PD.image_only

def test_present_but_garbled_is_garbled_text():
    # enough tokens, low hit-rate, AND density anomaly => garbled
    assert D("xqzklm zzzz vvvv bbbb ;;;;;;;;;;;;;;;; ############ qwzx lkjhg nnnn") == PD.garbled_text

def test_sparse_below_token_floor_is_uncertain_not_garbled():
    # too few alpha tokens to judge a hit-rate => uncertain_review (NOT garbled)
    assert D("Ref: 88-A") == PD.uncertain_review

def test_native_table_page_low_hitrate_stays_native_ok():
    # numeric/short-token table content: low wordlist hit-rate but normal density
    assert D("2026 2026 14 18 SC OOS 49417 1792 2943 1979 SVPD ALPR") == PD.native_ok

def test_all_caps_legal_page_stays_native_ok():
    assert D("FOIA RESPONSE CONFIDENTIAL RECORDS DIVISION CASE NUMBER REDACTED") == PD.native_ok

def test_multi_column_runon_stays_native_ok_when_density_normal():
    assert D("the police department the search reason the vehicle the officer record") == PD.native_ok

def test_repeated_boilerplate_does_not_force_or_avoid_ocr_spuriously():
    # a Bates/letterhead-only page is sparse text => uncertain_review (flag), not native_ok-trusted nor garbled
    assert D("SVPD-000123") == PD.uncertain_review

def test_low_parse_score_downgrades_otherwise_native_to_uncertain():
    # parse_score MUST be consumed: Docling's own low-confidence parse contradicts
    # otherwise-acceptable text -> uncertain_review (the contradictory-signal hook).
    text = "the police department search reason record vehicle officer"
    assert D(text) == PD.native_ok
    assert D(text, parse_score=0.02) == PD.uncertain_review


# --------------------------------------------------------------------------- #
# Task 3: decide_doc -- conservative doc rollup.
# --------------------------------------------------------------------------- #

from scripts.ingest_gate import decide_doc, DocDecision as DD

def test_all_native_is_native():
    assert decide_doc([PD.native_ok]*5) == DD.native

def test_all_image_only_is_ocr_images():
    assert decide_doc([PD.image_only]*5) == DD.ocr_images

def test_substantial_garbled_escalates_force_full_doc_ocr():
    assert decide_doc([PD.garbled_text]*4 + [PD.native_ok]) == DD.force_full_doc_ocr

def test_mostly_native_few_bad_stays_native_and_flags():
    # 200-page brief with 2 bad pages must NOT flip to full OCR (the load-bearing rule)
    diag = [PD.native_ok]*198 + [PD.garbled_text, PD.image_only]
    assert decide_doc(diag) == DD.native

def test_uncertain_dominant_is_review():
    assert decide_doc([PD.uncertain_review]*4 + [PD.native_ok]) == DD.review

def test_empty_pagelist_is_review():
    assert decide_doc([]) == DD.review

def test_combined_image_and_garbled_escalates_to_force_full_doc_ocr():
    # neither share alone dominant, but combined bad is high with garbled present
    # => force_full_doc_ocr (the safe superset; pins the prose rule)
    assert decide_doc([PD.image_only]*2 + [PD.garbled_text]*2 + [PD.native_ok]) == DD.force_full_doc_ocr

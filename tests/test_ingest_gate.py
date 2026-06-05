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

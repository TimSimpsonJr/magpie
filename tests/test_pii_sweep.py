import pandas as pd
from scripts.pii_sweep import distinct_texts, text_id


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

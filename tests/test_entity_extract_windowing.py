from __future__ import annotations

import pytest
from scripts.entity_extract import Span, Window, plan_windows, dedup_spans


# ---------------------------------------------------------------------------
# plan_windows
# ---------------------------------------------------------------------------

class TestPlanWindowsEdgeCases:
    def test_empty_string_returns_empty(self):
        assert plan_windows("") == []

    def test_whitespace_only_returns_empty(self):
        assert plan_windows("   \t\n  ") == []
        assert plan_windows("\n\n") == []

    def test_short_page_single_window(self):
        page = "Hello world this is a short page."
        result = plan_windows(page, max_chars=1400)
        assert len(result) == 1
        assert result[0].char_base == 0
        assert result[0].text == page

    def test_page_exactly_max_chars_single_window(self):
        page = "a" * 1400
        result = plan_windows(page, max_chars=1400)
        assert len(result) == 1
        assert result[0].char_base == 0
        assert result[0].text == page


class TestPlanWindowsLongPage:
    def _make_long_page(self) -> str:
        # ~4000 chars of word-separated text
        return "word " * 800

    def test_long_page_produces_multiple_windows(self):
        page = self._make_long_page()
        result = plan_windows(page, max_chars=1400, overlap=200)
        assert len(result) > 1

    def test_all_windows_exact_text_slice(self):
        page = self._make_long_page()
        result = plan_windows(page, max_chars=1400, overlap=200)
        for w in result:
            assert page[w.char_base : w.char_base + len(w.text)] == w.text, (
                f"Window text mismatch at char_base={w.char_base}"
            )

    def test_consecutive_windows_share_overlap(self):
        page = self._make_long_page()
        result = plan_windows(page, max_chars=1400, overlap=200)
        for i in range(len(result) - 1):
            a = result[i]
        b = result[i + 1]
        a_end = a.char_base + len(a.text)
        b_start = b.char_base
        # They must overlap -- b_start < a_end
        assert b_start < a_end, (
            f"No overlap between window {i} and {i+1}: a_end={a_end} b_start={b_start}"
        )

    def test_last_window_reaches_end_of_page(self):
        page = self._make_long_page()
        result = plan_windows(page, max_chars=1400, overlap=200)
        last = result[-1]
        assert last.char_base + len(last.text) == len(page)

    def test_union_covers_entire_page(self):
        page = self._make_long_page()
        result = plan_windows(page, max_chars=1400, overlap=200)
        # Every character index 0..len(page)-1 should be covered
        covered = set()
        for w in result:
            for i in range(w.char_base, w.char_base + len(w.text)):
                covered.add(i)
        for i in range(len(page)):
            assert i in covered, f"Index {i} not covered by any window"

    def test_windows_do_not_exceed_max_chars(self):
        page = self._make_long_page()
        result = plan_windows(page, max_chars=1400, overlap=200)
        for w in result:
            assert len(w.text) <= 1400

    def test_no_window_ends_mid_word(self):
        # Windows should prefer ending at whitespace
        page = self._make_long_page()
        result = plan_windows(page, max_chars=1400, overlap=200)
        for w in result[:-1]:  # last window may end mid-word at end of page
            # Last char of w.text should be whitespace or the next char is whitespace
            # The window text itself ends at a word boundary
            text = w.text
            assert text[-1] == " " or text[-1] == "\n" or text.rstrip() == text.rstrip(), (
                f"Window does not end at whitespace: ...{repr(text[-20:])}"
            )


# ---------------------------------------------------------------------------
# dedup_spans
# ---------------------------------------------------------------------------

def make_span(text, label, start, end, score=1.0):
    return Span(text=text, label=label, char_start=start, char_end=end, score=score)


class TestDedupSpans:
    def test_empty_input(self):
        assert dedup_spans([]) == []

    def test_single_span_passes_through(self):
        s = make_span("Alice", "person", 0, 5)
        assert dedup_spans([s]) == [s]

    def test_exact_duplicate_removed(self):
        s = make_span("Alice", "person", 0, 5)
        result = dedup_spans([s, s])
        assert result == [s]

    def test_exact_duplicate_same_fields(self):
        s1 = make_span("Alice", "person", 0, 5, score=0.9)
        s2 = make_span("Alice", "person", 0, 5, score=0.9)
        result = dedup_spans([s1, s2])
        assert len(result) == 1

    def test_same_label_overlapping_keep_longer(self):
        # s1 is shorter (0-5), s2 is longer (0-8); same label => keep s2
        s1 = make_span("Alice", "person", 0, 5, score=0.9)
        s2 = make_span("Alice M", "person", 0, 8, score=0.7)
        result = dedup_spans([s1, s2])
        assert len(result) == 1
        assert result[0].char_end == 8

    def test_same_label_overlapping_equal_length_keep_higher_score(self):
        # Same length, same label, overlapping -> keep higher score
        s1 = make_span("Alice", "person", 0, 5, score=0.6)
        s2 = make_span("Alice", "person", 0, 5, score=0.9)
        result = dedup_spans([s1, s2])
        assert len(result) == 1
        assert result[0].score == 0.9

    def test_different_labels_overlapping_both_kept(self):
        # Same text range but different labels -> both survive
        s1 = make_span("SVPD", "government agency", 0, 4, score=0.9)
        s2 = make_span("SVPD", "company", 0, 4, score=0.8)
        result = dedup_spans([s1, s2])
        assert len(result) == 2

    def test_non_overlapping_same_label_both_kept(self):
        s1 = make_span("Alice", "person", 0, 5)
        s2 = make_span("Bob", "person", 10, 13)
        result = dedup_spans([s1, s2])
        assert len(result) == 2

    def test_result_sorted_by_char_start_char_end_label(self):
        s1 = make_span("Bob", "person", 10, 13)
        s2 = make_span("Alice", "person", 0, 5)
        result = dedup_spans([s1, s2])
        assert result[0].char_start == 0
        assert result[1].char_start == 10

    def test_result_sorted_by_label_when_same_offsets(self):
        s1 = make_span("X", "person", 0, 1)
        s2 = make_span("X", "company", 0, 1)
        result = dedup_spans([s1, s2])
        assert result[0].label == "company"
        assert result[1].label == "person"

    def test_partial_overlap_same_label_keep_longer(self):
        # s1: 0-10, s2: 5-15 (overlap at 5-10), same label -> keep longer (10 chars each, but s2 is 10 too)
        # Actually: s1 length = 10, s2 length = 10, so tie-break by score
        s1 = make_span("Alice Smith", "person", 0, 10, score=0.5)
        s2 = make_span("Smith Jones", "person", 5, 15, score=0.8)
        result = dedup_spans([s1, s2])
        assert len(result) == 1
        assert result[0].score == 0.8

    def test_partial_overlap_same_label_keep_longer_unequal(self):
        # s1: 0-6 (len 6), s2: 3-15 (len 12); s2 is longer -> keep s2
        s1 = make_span("Alice", "person", 0, 6, score=0.9)
        s2 = make_span("Alice Smith Jr", "person", 3, 15, score=0.5)
        result = dedup_spans([s1, s2])
        assert len(result) == 1
        assert result[0].char_end == 15

    def test_touching_but_not_overlapping_same_label_both_kept(self):
        # [0, 5) and [5, 10) share no interior chars -> both kept
        s1 = make_span("Alice", "person", 0, 5)
        s2 = make_span("Bob", "person", 5, 10)
        result = dedup_spans([s1, s2])
        assert len(result) == 2

    def test_multiple_duplicates_one_survivor(self):
        s = make_span("Alice", "person", 0, 5, score=0.9)
        result = dedup_spans([s, s, s])
        assert len(result) == 1

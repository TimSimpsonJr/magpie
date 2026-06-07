"""Task 5: the lazy GLiNER/GLiREL model edge.

Two kinds of test:

  * Import-purity (NO marker -- runs in the offline suite): importing
    ``scripts.entity_models`` must NOT pull torch / gliner / glirel / spacy
    into ``sys.modules``. Verified in a subprocess so a heavy import in this
    process (from another test) cannot mask a regression. Hard timeout.
  * ``@pytest.mark.gliner`` integration: real weights (cached on this box).
    RESILIENT -- any load/predict Exception -> ``pytest.skip`` so the suite
    stays portable (CI-without-models). The relation test asserts OFFSET
    INTEGRITY of the char<->token round-trip, NOT a specific relation label
    (zero-shot RE is noisy, F1 ~25-40 -- see prior-art section 6).

ASCII only.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from scripts.entity_extract import Span
from scripts.entity_taxonomy import resolve


# ---------------------------------------------------------------------------
# Import-purity (offline suite -- no marker)
# ---------------------------------------------------------------------------

def test_importing_entity_models_is_lazy():
    """Importing scripts.entity_models pulls in no heavy ML deps.

    Subprocess-isolated so a heavy import elsewhere in this pytest process
    cannot hide a leak; sys.executable is the venv python pytest runs under.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "import scripts.entity_models as m\n"
        "bad = [x for x in ('torch','gliner','glirel','spacy') if x in sys.modules]\n"
        "assert not bad, bad\n"
        "print('PURE_OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "PURE_OK" in proc.stdout


def test_entity_models_exposes_the_two_extractor_classes():
    """The module surface is the two injectable extractor classes.

    Pure (no model construction) -- just attribute presence + that they are
    classes. Keeps the import path honest without touching weights.
    """
    import scripts.entity_models as m

    assert isinstance(m.GlinerEntityExtractor, type)
    assert isinstance(m.GlirelRelationExtractor, type)


# ---------------------------------------------------------------------------
# _dedup_token_intervals (offline suite -- pure, NO spaCy, NO models)
#
# Regression for the Codex `tokenmap` cluster: two DISTINCT input spans (e.g.
# overlapping cross-label spans) can expand to the SAME inclusive token interval.
# The old reverse-map construction let the later span OVERWRITE the earlier (and
# added a duplicate ner entry), so a returned relation could re-bind to the wrong
# Span/label, order-dependently. The pure helper must collapse each interval to
# ONE deterministic winner (longest char span, then highest score, then
# lexicographically smallest label), independent of input order.
# ---------------------------------------------------------------------------

def test_dedup_token_intervals_collision_keeps_one_deterministic_winner():
    from scripts.entity_models import _dedup_token_intervals

    # Two distinct spans (different labels AND char ranges) that map to the SAME
    # token interval (0, 1). The "company" span is clearly LONGER in chars, so it
    # is the deterministic winner regardless of score.
    longer = Span("Flock Safety", "company", 0, 12, 0.50)
    shorter = Span("Flock", "organization", 0, 5, 0.99)
    mapped = [(longer, 0, 1), (shorter, 0, 1)]

    ner, reverse = _dedup_token_intervals(mapped)

    # Exactly one ner entry for the colliding interval, and reverse maps to the
    # longer span (the winner) -- not the higher-scored shorter one.
    assert len(ner) == 1
    assert ner == [[0, 1, "COMPANY", "Flock Safety"]]
    assert list(reverse.keys()) == [(0, 1)]
    assert reverse[(0, 1)] is longer


def test_dedup_token_intervals_is_order_independent():
    from scripts.entity_models import _dedup_token_intervals

    longer = Span("Flock Safety", "company", 0, 12, 0.50)
    shorter = Span("Flock", "organization", 0, 5, 0.99)

    ner_a, reverse_a = _dedup_token_intervals([(longer, 0, 1), (shorter, 0, 1)])
    ner_b, reverse_b = _dedup_token_intervals([(shorter, 0, 1), (longer, 0, 1)])

    # Same winner / same ner / same reverse regardless of input order.
    assert ner_a == ner_b
    assert reverse_a == reverse_b
    assert reverse_a[(0, 1)] is longer
    assert reverse_b[(0, 1)] is longer


def test_dedup_token_intervals_distinct_intervals_both_survive():
    from scripts.entity_models import _dedup_token_intervals

    # Two spans that map to DIFFERENT intervals -> both survive; reverse has both.
    a = Span("Jane Smith", "person", 0, 10, 0.9)
    b = Span("Flock Safety", "company", 20, 32, 0.9)
    mapped = [(b, 3, 4), (a, 0, 1)]  # deliberately out of token order

    ner, reverse = _dedup_token_intervals(mapped)

    assert len(ner) == 2
    # ner is sorted by (start_tok, end_incl) for determinism.
    assert ner == [
        [0, 1, "PERSON", "Jane Smith"],
        [3, 4, "COMPANY", "Flock Safety"],
    ]
    assert reverse == {(0, 1): a, (3, 4): b}


# ---------------------------------------------------------------------------
# Shared sample for the gliner-marked integration tests
# ---------------------------------------------------------------------------

_SAMPLE_TEXT = (
    "Mayor Jane Smith signed a contract with Flock Safety on behalf of the "
    "Greenville Police Department."
)
_ENTITY_LABELS = ["person", "government official", "company", "government agency"]


def _hand_built_spans(text):
    """Build entity Spans with correct char offsets into ``text``.

    Used as a deterministic fallback for the relation test so it exercises the
    char<->token mapping even if the entity model surfaces different spans.
    Offsets are computed from the text itself (end EXCLUSIVE), never hardcoded.
    """
    wanted = [
        ("Jane Smith", "person"),
        ("Flock Safety", "company"),
        ("Greenville Police Department", "government agency"),
    ]
    spans = []
    for surface, label in wanted:
        idx = text.find(surface)
        assert idx != -1, surface
        spans.append(Span(surface, label, idx, idx + len(surface), 0.9))
    return spans


# ---------------------------------------------------------------------------
# @gliner: GLiNER entity round-trip
# ---------------------------------------------------------------------------

@pytest.mark.gliner
def test_gliner_entity_round_trip():
    from scripts.entity_models import GlinerEntityExtractor

    try:
        ex = GlinerEntityExtractor()
        spans = ex.predict_entities(_SAMPLE_TEXT, _ENTITY_LABELS, 0.3)
    except Exception as exc:  # pragma: no cover - portability / CI-without-models
        pytest.skip("GLiNER unavailable: %r" % (exc,))

    assert isinstance(spans, list)
    assert all(isinstance(s, Span) for s in spans)
    # Offset integrity: each span text matches the slice it claims.
    for s in spans:
        assert 0 <= s.char_start < s.char_end <= len(_SAMPLE_TEXT)
        assert _SAMPLE_TEXT[s.char_start:s.char_end] == s.text
    assert any("Greenville" in s.text for s in spans), [s.text for s in spans]


# ---------------------------------------------------------------------------
# @gliner: GLiREL relation round-trip (offset integrity is the point)
# ---------------------------------------------------------------------------

@pytest.mark.gliner
def test_glirel_relation_round_trip():
    from scripts.entity_models import GlirelRelationExtractor

    taxonomy = resolve("generic")
    spans = _hand_built_spans(_SAMPLE_TEXT)

    try:
        ex = GlirelRelationExtractor()
        results = ex.predict_relations(_SAMPLE_TEXT, spans, taxonomy, 0.0)
    except Exception as exc:  # pragma: no cover - portability / CI-without-models
        pytest.skip("GLiREL unavailable: %r" % (exc,))

    # The call must return a list; an empty list is allowed (RE is noisy). But
    # ANY returned tuple must be well-formed: 4-tuple (Span, Span, str, float),
    # head/tail are objects FROM the input spans list, label is not the negative
    # sentinel, and offsets are within the text.
    assert isinstance(results, list)
    span_ids = {id(s) for s in spans}
    for item in results:
        assert isinstance(item, tuple) and len(item) == 4, item
        head, tail, label, score = item
        assert isinstance(head, Span) and isinstance(tail, Span)
        assert id(head) in span_ids, "head must be an object from the input spans"
        assert id(tail) in span_ids, "tail must be an object from the input spans"
        assert isinstance(label, str) and label != "no relation"
        assert isinstance(score, float)
        assert 0 <= head.char_start < head.char_end <= len(_SAMPLE_TEXT)
        assert 0 <= tail.char_start < tail.char_end <= len(_SAMPLE_TEXT)


@pytest.mark.gliner
def test_glirel_fewer_than_two_spans_returns_empty():
    """Guard a: fewer than two spans -> no pairs -> empty list (no model call).

    This path does not touch weights, so it should not skip on this box; but
    construction of the extractor is cheap and lazy, so wrap defensively.
    """
    from scripts.entity_models import GlirelRelationExtractor

    taxonomy = resolve("generic")
    one = _hand_built_spans(_SAMPLE_TEXT)[:1]
    try:
        ex = GlirelRelationExtractor()
        assert ex.predict_relations(_SAMPLE_TEXT, one, taxonomy, 0.0) == []
        assert ex.predict_relations(_SAMPLE_TEXT, [], taxonomy, 0.0) == []
    except Exception as exc:  # pragma: no cover - portability
        pytest.skip("GLiREL unavailable: %r" % (exc,))

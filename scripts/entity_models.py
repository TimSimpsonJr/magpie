"""The lazy GLiNER/GLiREL model edge for Phase 12 entity extraction.

This is the ONLY module that touches the heavy ML stack (torch / gliner /
glirel / spacy). All of those imports are deferred into instance methods so that
``import scripts.entity_models`` stays cheap and dependency-free: the pure core
(``entity_extract`` + ``entity_taxonomy``) and the offline test suite import this
module without pulling in any weights. The two classes here are the injectable
extractors the ``extract()`` orchestrator calls; their call signatures match the
fakes in ``tests/conftest_entity.py`` exactly.

The char-offset <-> token-index glue (GLiNER speaks char offsets; GLiREL speaks
inclusive token indices) lives HERE, via a lazily-built ``spacy.blank("en")``
tokenizer, so the pure core never imports spaCy.

ASCII only. See ``skills/entity-extract/references/prior-art.md`` for the
source-verified GLiNER/GLiREL API facts the algorithm below depends on.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from scripts.entity_extract import Span


# ---------------------------------------------------------------------------
# Pure char<->token glue helper (stdlib only -- NO spaCy, NO model).
# Kept module-level + pure so it is offline-testable without weights.
# ---------------------------------------------------------------------------

def _dedup_token_intervals(mapped):
    """mapped: list of (span, start_tok, end_incl). Collapse duplicate
    (start_tok, end_incl) intervals to ONE deterministic winner so the
    reverse map is unambiguous and order-independent.

    Winner rule (deterministic, input-order-independent): for a given
    interval, prefer the LONGEST char span (span.char_end - span.char_start),
    then the HIGHEST score, then the lexicographically smallest label -- a
    total order so ties never depend on input order.
    Returns (ner, reverse): ner = [[start_tok, end_incl, label.upper(), text], ...]
    one entry per surviving interval (sorted by (start_tok, end_incl) for
    determinism); reverse = {(start_tok, end_incl): span}.
    """
    # Group the mapped tuples by their token interval.
    by_interval: dict = {}
    for span, start_tok, end_incl in mapped:
        by_interval.setdefault((start_tok, end_incl), []).append(span)

    def _key(span):
        # Total order. Negate length + score so that "largest" sorts FIRST under
        # ascending min(); label is the final ascending lexicographic tiebreak.
        char_len = span.char_end - span.char_start
        return (-char_len, -span.score, span.label)

    ner = []
    reverse = {}
    for interval in sorted(by_interval):  # sort by (start_tok, end_incl)
        start_tok, end_incl = interval
        winner = min(by_interval[interval], key=_key)
        ner.append([start_tok, end_incl, winner.label.upper(), winner.text])
        reverse[interval] = winner
    return ner, reverse


# ---------------------------------------------------------------------------
# Documentation-only Protocols (the classes need not inherit them; they make
# the injection contract explicit and let type checkers verify the fakes).
# ---------------------------------------------------------------------------

@runtime_checkable
class EntityExtractor(Protocol):
    def predict_entities(self, text: str, labels, threshold: float) -> list:
        ...


@runtime_checkable
class RelationExtractor(Protocol):
    def predict_relations(self, text: str, spans: list, taxonomy, threshold: float) -> list:
        ...


# ---------------------------------------------------------------------------
# GLiNER entity extractor (lazy)
# ---------------------------------------------------------------------------

class GlinerEntityExtractor:
    """Zero-shot multi-type NER via GLiNER, loaded lazily on first predict.

    Returns ``Span`` objects with char offsets (end exclusive) into the text
    passed in -- matching ``FakeEntityExtractor.predict_entities``.
    """

    def __init__(self, model_name: str = "urchade/gliner_medium-v2.1") -> None:
        self._model_name = model_name
        self._model = None  # cached after first _load()

    def _load(self):
        """Import + construct the GLiNER model exactly once.

        The ``from gliner import GLiNER`` lives here so module import stays
        torch/gliner-free.
        """
        if self._model is None:
            from gliner import GLiNER

            self._model = GLiNER.from_pretrained(self._model_name)
        return self._model

    def predict_entities(self, text: str, labels, threshold: float) -> list:
        raw = self._load().predict_entities(text, list(labels), threshold=threshold)
        # raw items: {"text","label","start","end","score"} (char offsets, end excl)
        return [
            Span(d["text"], d["label"], d["start"], d["end"], float(d["score"]))
            for d in raw
        ]


# ---------------------------------------------------------------------------
# GLiREL relation extractor (lazy model + lazy spaCy tokenizer)
# ---------------------------------------------------------------------------

class GlirelRelationExtractor:
    """Zero-shot relation extraction via GLiREL, loaded lazily on first predict.

    Uses a lazily-built ``spacy.blank("en")`` tokenizer (tokenizer only -- no
    trained model) to convert GLiNER char-spans into the INCLUSIVE token indices
    GLiREL's ``ner`` argument expects, then maps the EXCLUSIVE-ended output token
    positions back to the original ``Span`` objects.

    Returns ``(head_span, tail_span, rel_label, score)`` tuples where the spans
    are the SAME objects passed in -- matching ``FakeRelationExtractor`` so the
    orchestrator can look them up by ``(char_start, char_end, label)``. Pair-type
    filtering is NOT done here; the pure core's ``make_edge`` owns that rule.
    """

    def __init__(
        self,
        model_name: str = "jackboyla/glirel-large-v0",
        no_relation_label: str = "no relation",
    ) -> None:
        self._model_name = model_name
        self._no_relation_label = no_relation_label
        self._model = None       # cached after first _load()
        self._nlp = None         # cached spacy.blank("en") tokenizer

    def _load(self):
        """Import + construct the GLiREL model exactly once."""
        if self._model is None:
            from glirel import GLiREL

            self._model = GLiREL.from_pretrained(self._model_name)
        return self._model

    def _tokenizer(self):
        """Build + cache a blank-English spaCy tokenizer exactly once.

        ``spacy.blank("en")`` is a tokenizer-only pipeline -- it loads NO trained
        weights, so this stays light. The ``import spacy`` lives here.
        """
        if self._nlp is None:
            import spacy

            self._nlp = spacy.blank("en")
        return self._nlp

    def predict_relations(self, text: str, spans: list, taxonomy, threshold: float) -> list:
        # a. Need at least two entities to form an ordered pair.
        if len(spans) < 2:
            return []

        # b. Tokenize once; GLiREL aligns by token index against this token list.
        doc = self._tokenizer()(text)
        tokens = [t.text for t in doc]

        # c. Map each span to its INCLUSIVE token interval, then resolve duplicate
        #    intervals deterministically (a pure helper) so the reverse map is
        #    unambiguous and order-independent. Two distinct cross-label spans can
        #    expand to the SAME token interval; _dedup_token_intervals picks ONE
        #    winner per interval (no overwrite, no duplicate ner entry).
        mapped = []
        for s in spans:
            sp = doc.char_span(s.char_start, s.char_end, alignment_mode="expand")
            if sp is None:
                # Span does not align to token boundaries -> skip for relations.
                continue
            # spaCy end is EXCLUSIVE; GLiREL ner end is INCLUSIVE -> sp.end - 1.
            mapped.append((s, sp.start, sp.end - 1))
        ner, reverse = _dedup_token_intervals(mapped)
        if len(ner) < 2:
            return []

        # d. FLAT list of relation-label strings + the negative sentinel.
        #    (Do NOT wrap in {"glirel_labels": {...}} -- that is the spaCy-pipeline
        #    context shape and collapses every prediction to one class.)
        rel_labels = [r.label for r in taxonomy.relations] + [self._no_relation_label]

        # e. Direct GLiREL call (tokens list, flat labels, inclusive-ended ner).
        raw = self._load().predict_relations(
            tokens, rel_labels, threshold=threshold, ner=ner, top_k=1
        )
        # raw items: {"head_pos":[start, end_EXCLUSIVE], "tail_pos":[start, end_EXCL],
        #   "head_text":[...], "tail_text":[...], "label":str, "score":float}

        # f. Map each non-sentinel relation back to the input Span objects.
        results = []
        for r in raw:
            label = r["label"]
            if label == self._no_relation_label:
                continue
            score = float(r["score"])
            if score < threshold:  # belt-and-suspenders (model already thresholds)
                continue
            h0, h1x = r["head_pos"]
            t0, t1x = r["tail_pos"]
            # Output end is EXCLUSIVE; ner/reverse keys are INCLUSIVE -> subtract 1.
            head = reverse.get((h0, h1x - 1))
            tail = reverse.get((t0, t1x - 1))
            if head is None or tail is None:
                continue
            results.append((head, tail, label, score))
        return results

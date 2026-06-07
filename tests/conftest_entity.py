"""Reusable fake extractors for entity-extract pipeline tests.

NOT pytest fixtures -- plain classes imported directly by tests.
ASCII only.
"""
from __future__ import annotations

from scripts.entity_extract import Span


class FakeEntityExtractor:
    """Returns canned spans for substrings found in the text."""

    def __init__(self, entities):
        # entities: list of (substring, label, score)
        self._entities = entities

    def predict_entities(self, text: str, labels: list, threshold: float) -> list:
        results = []
        for (sub, label, score) in self._entities:
            if label not in labels:
                continue
            if score < threshold:
                continue
            idx = text.find(sub)
            if idx == -1:
                continue
            results.append(Span(sub, label, idx, idx + len(sub), score))
        return results


class FakeRelationExtractor:
    """Returns canned relation triples when both head and tail spans are present."""

    def __init__(self, relations):
        # relations: list of (head_text, tail_text, rel_label, score)
        self._relations = relations

    def predict_relations(self, text: str, spans: list, taxonomy, threshold: float) -> list:
        results = []
        for (h, t, rel, score) in self._relations:
            if score < threshold:
                continue
            head_span = next((s for s in spans if s.text == h), None)
            tail_span = next((s for s in spans if s.text == t), None)
            if head_span is None or tail_span is None:
                continue
            results.append((head_span, tail_span, rel, score))
        return results

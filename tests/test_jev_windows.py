"""ASCII only. Offline tests for window packing + the single split in scripts/jev_client.py
(Task 3). ``ask_fn`` is a fake that answers or raises JevUnavailable per call index; nothing
touches the network."""
from __future__ import annotations

import json
import math

from scripts import jev_client as jc
from scripts.jev_client import (
    JevResult,
    JevUnavailable,
    WindowedResult,
    ask_windowed,
    estimate_tokens,
    pack_windows,
)

STATE_KEY = "claims"
# ~20k chars of payload per item -> ~5k estimated tokens; two fit in 14k, three do not.
FIVE_K_CHARS = 20_000


def _item(chars: int = 10) -> dict:
    return {"span": "x" * chars}


def _items(n: int, chars: int = 10) -> dict[str, dict]:
    return {f"K{i:02d}": _item(chars) for i in range(1, n + 1)}


def _questions_for(key: str) -> dict[str, dict]:
    return {f"presence_{key}": jc.noul_question("p?", "yes", "no"),
            f"entail_{key}": jc.noul_question("e?", "yes", "no")}


class FakeAsk:
    """Answers every requested question with noul 0.9, unless the outcome for this call index
    is a reason string, in which case it raises JevUnavailable(reason)."""

    def __init__(self, outcomes: dict[int, str] | None = None, models: list[str] | None = None,
                 usage: dict | None = None):
        self.outcomes = outcomes or {}
        self.models = models or []
        self.usage = usage
        self.calls: list[tuple[dict, dict]] = []

    def __call__(self, state: dict, questions: dict) -> JevResult:
        idx = len(self.calls)
        self.calls.append((state, questions))
        reason = self.outcomes.get(idx)
        if reason is not None:
            raise JevUnavailable(reason)
        model = self.models[idx] if idx < len(self.models) else "m1"
        usage = dict(self.usage) if self.usage is not None else {
            "input_tokens": 1, "output_tokens": 1, "cost": 0.0}
        answers = {qid: {"type": "noul", "noul": 0.9} for qid in questions}
        return JevResult(answers=answers, model=model, usage=usage, latency_ms=5)

    def sent_keys(self, idx: int) -> list[str]:
        return list(self.calls[idx][0][STATE_KEY].keys())


def _one_window(n: int) -> dict[str, dict]:
    """n small items that fit in one window."""
    return _items(n)


# --- estimate_tokens + pack_windows -------------------------------------------------------

def test_estimate_tokens_is_ceil_of_json_chars_over_four():
    obj = {"a": "x" * 400}
    assert estimate_tokens(obj) == math.ceil(len(json.dumps(obj, ensure_ascii=False)) / 4)


def test_estimate_tokens_counts_non_ascii_as_characters():
    obj = {"a": "\u00e9" * 8}
    assert estimate_tokens(obj) == math.ceil(len(json.dumps(obj, ensure_ascii=False)) / 4)


def test_constants():
    assert jc.CHARS_PER_TOKEN == 4
    assert jc.WINDOW_TOKEN_BUDGET == 14_000


def test_pack_five_items_of_five_k_tokens_into_pairs():
    items = _items(5, FIVE_K_CHARS)
    windows, oversize = pack_windows(items, state_key=STATE_KEY, budget=14_000)
    assert windows == [["K01", "K02"], ["K03", "K04"], ["K05"]]
    assert oversize == []


def test_pack_default_budget_is_window_token_budget():
    items = _items(5, FIVE_K_CHARS)
    assert pack_windows(items, state_key=STATE_KEY) == pack_windows(
        items, state_key=STATE_KEY, budget=jc.WINDOW_TOKEN_BUDGET)


def test_pack_every_window_estimate_within_budget():
    items = _items(5, FIVE_K_CHARS)
    windows, _ = pack_windows(items, state_key=STATE_KEY, budget=14_000)
    for w in windows:
        assert estimate_tokens({STATE_KEY: {k: items[k] for k in w}}) <= 14_000


def test_pack_oversize_item_is_excluded_and_order_preserved():
    items = {"K01": _item(), "K02": _item(60_000), "K03": _item(), "K04": _item()}
    windows, oversize = pack_windows(items, state_key=STATE_KEY, budget=14_000)
    assert oversize == ["K02"]
    flat = [k for w in windows for k in w]
    assert "K02" not in flat
    assert flat == ["K01", "K03", "K04"]


def test_pack_empty_items():
    assert pack_windows({}, state_key=STATE_KEY) == ([], [])


# --- ask_windowed ---------------------------------------------------------------------------

def test_all_windows_succeed():
    items = _items(5, FIVE_K_CHARS)
    fake = FakeAsk()
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake, budget=14_000)
    assert isinstance(res, WindowedResult)
    assert set(res.answers) == set(items)
    for k in items:
        assert set(res.answers[k]) == {f"presence_{k}", f"entail_{k}"}
        assert res.answers[k][f"presence_{k}"]["noul"] == 0.9
    assert res.failures == {}
    assert res.calls == 3
    assert res.models == ["m1"]
    assert res.latency_ms == 15


def test_request_state_and_questions_cover_exactly_the_window():
    items = _items(5, FIVE_K_CHARS)
    fake = FakeAsk()
    ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake, budget=14_000)
    state, questions = fake.calls[0]
    assert state == {STATE_KEY: {"K01": items["K01"], "K02": items["K02"]}}
    assert set(questions) == {"presence_K01", "entail_K01", "presence_K02", "entail_K02"}


def test_too_large_splits_once_and_both_halves_succeed():
    items = _one_window(4)
    fake = FakeAsk({0: "too_large"})
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake)
    assert res.calls == 3
    assert set(res.answers) == set(items)
    assert res.failures == {}
    assert fake.sent_keys(1) == ["K01", "K02"]
    assert fake.sent_keys(2) == ["K03", "K04"]


def test_split_first_half_is_ceil_of_half():
    items = _one_window(5)
    fake = FakeAsk({0: "too_large"})
    ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake)
    assert fake.sent_keys(1) == ["K01", "K02", "K03"]
    assert fake.sent_keys(2) == ["K04", "K05"]


def test_too_large_second_half_fails_without_further_split():
    items = _one_window(4)
    fake = FakeAsk({0: "too_large", 2: "too_large"})
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake)
    assert res.calls == 3
    assert set(res.answers) == {"K01", "K02"}
    assert res.failures == {"K03": "too_large", "K04": "too_large"}


def test_waf_blocked_second_half_fails_with_waf_blocked():
    items = _one_window(4)
    fake = FakeAsk({0: "waf_blocked", 2: "waf_blocked"})
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake)
    assert res.calls == 3
    assert set(res.answers) == {"K01", "K02"}
    assert res.failures == {"K03": "waf_blocked", "K04": "waf_blocked"}


def test_half_failing_with_other_reason_marks_that_reason():
    items = _one_window(4)
    fake = FakeAsk({0: "too_large", 1: "http_500"})
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake)
    assert res.calls == 3
    assert res.failures == {"K01": "http_500", "K02": "http_500"}
    assert set(res.answers) == {"K03", "K04"}


def test_single_item_window_too_large_is_not_split():
    items = _one_window(1)
    fake = FakeAsk({0: "too_large"})
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake)
    assert res.failures == {"K01": "too_large"}
    assert res.answers == {}
    assert res.calls == 1


def test_non_split_reason_marks_window_and_later_windows_continue():
    items = _items(4, FIVE_K_CHARS)
    fake = FakeAsk({0: "http_500"})
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake, budget=14_000)
    assert res.calls == 2
    assert res.failures == {"K01": "http_500", "K02": "http_500"}
    assert set(res.answers) == {"K03", "K04"}


def test_abort_reason_stops_all_remaining_windows():
    items = _items(5, FIVE_K_CHARS)
    fake = FakeAsk({0: "http_401"})
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake, budget=14_000)
    assert res.calls == 1
    assert res.failures == {k: "http_401" for k in items}
    assert res.answers == {}


def test_abort_reason_in_a_later_window_keeps_earlier_answers():
    items = _items(5, FIVE_K_CHARS)
    fake = FakeAsk({1: "missing_key"})
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake, budget=14_000)
    assert res.calls == 2
    assert set(res.answers) == {"K01", "K02"}
    assert res.failures == {"K03": "missing_key", "K04": "missing_key", "K05": "missing_key"}


def test_abort_reason_in_first_half_stops_second_half_and_later_windows():
    # K01-K04 ~2k tokens together; K05 ~12.5k alone fits a window but not beside them.
    items = _items(4, 2_000)
    items["K05"] = _item(50_000)
    fake = FakeAsk({0: "too_large", 1: "http_403"})
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake, budget=14_000)
    assert res.calls == 2
    assert res.failures == {k: "http_403" for k in items}
    assert res.answers == {}


def test_every_abort_reason_stops_the_run():
    for reason in jc.ABORT_REASONS:
        items = _items(4, FIVE_K_CHARS)
        fake = FakeAsk({0: reason})
        res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake, budget=14_000)
        assert res.calls == 1, reason
        assert res.failures == {k: reason for k in items}, reason


def test_oversize_item_is_too_large_with_zero_calls_for_it():
    items = {"K01": _item(), "K02": _item(60_000), "K03": _item()}
    fake = FakeAsk()
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake, budget=14_000)
    assert res.failures == {"K02": "too_large"}
    assert set(res.answers) == {"K01", "K03"}
    assert res.calls == 1
    assert all("K02" not in state[STATE_KEY] for state, _ in fake.calls)


def test_only_oversize_items_means_no_calls():
    items = {"K01": _item(60_000)}
    fake = FakeAsk()
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake, budget=14_000)
    assert res.calls == 0
    assert res.failures == {"K01": "too_large"}
    assert res.models == []


def test_empty_items_makes_no_calls():
    fake = FakeAsk()
    res = ask_windowed({}, _questions_for, state_key=STATE_KEY, ask_fn=fake)
    assert res.calls == 0
    assert res.answers == {} and res.failures == {} and res.models == []
    assert res.usage == {"input_tokens": 0, "output_tokens": 0, "cost": 0}


def test_models_are_distinct_in_first_seen_order():
    items = _items(5, FIVE_K_CHARS)
    fake = FakeAsk(models=["m1", "m2", "m1"])
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake, budget=14_000)
    assert res.models == ["m1", "m2"]


def test_usage_is_summed_with_none_as_zero():
    items = _items(4, FIVE_K_CHARS)

    class UsageAsk(FakeAsk):
        def __call__(self, state, questions):
            res = super().__call__(state, questions)
            idx = len(self.calls) - 1
            res.usage = ({"input_tokens": 10, "output_tokens": None, "cost": 0.25} if idx == 0
                         else {"input_tokens": 20, "output_tokens": 2, "cost": None})
            return res

    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=UsageAsk(),
                       budget=14_000)
    assert res.usage == {"input_tokens": 30, "output_tokens": 2, "cost": 0.25}


def test_failed_calls_still_count_toward_calls():
    items = _one_window(4)
    fake = FakeAsk({0: "too_large", 1: "too_large", 2: "too_large"})
    res = ask_windowed(items, _questions_for, state_key=STATE_KEY, ask_fn=fake)
    assert res.calls == 3
    assert res.failures == {k: "too_large" for k in items}

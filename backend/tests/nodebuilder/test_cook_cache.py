"""The editor's cook cache (F435 W4 item 4.A, plan D6).

- the key: eval_hash ignores what cannot change a value; the frame
  fingerprint changes when the fetched frame gains a bar;
- limits: entry count, age, memory cap (LRU eviction);
- a cache hit after a backtest does zero recompute;
- live bots never import the cache.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import nodebuilder.evaluator as evaluator_mod
from nodebuilder.cook_cache import (
    COOK_CACHE,
    CookCache,
    cook_id_for,
    eval_hash,
    frame_fingerprint,
    make_key,
)
from nodebuilder.models import Graph
from tests.nodebuilder.test_inspect import (  # noqa: F401  (client, frame are fixtures)
    WINDOW,
    _backtest,
    _daily,
    _graph,
    client,
    frame,
)

INSPECT = "/api/nodebuilder/inspect"
PREVIEW = "/api/nodebuilder/preview"
BACKEND = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# eval_hash
# ---------------------------------------------------------------------------

def _g(mutate=None) -> Graph:
    data = copy.deepcopy(_graph())
    if mutate:
        mutate(data)
    return Graph.model_validate(data)


def test_eval_hash_ignores_layout_names_flags_and_notes():
    base = eval_hash(_g())

    def layout(d):
        d["nodes"]["/rsi"]["position"] = [400, 120]
        d["nodes"]["/rsi"]["name"] = "fast_rsi"
        d["nodes"]["/rsi"]["display"] = True
        d["meta"] = {"title": "renamed"}
        d["annotations"] = {"notes": [{"id": "n1", "text": "hello"}], "boxes": []}
        d["wires"][0]["id"] = "w_renamed"

    assert eval_hash(_g(layout)) == base


@pytest.mark.parametrize("mutate", [
    lambda d: d["nodes"]["/rsi"]["params"].__setitem__("period", 7),
    lambda d: d["nodes"]["/sma"].__setitem__("bypass", True),
    lambda d: d["wires"].pop(),
])
def test_eval_hash_changes_with_what_the_cook_reads(mutate):
    assert eval_hash(_g(mutate)) != eval_hash(_g())


# ---------------------------------------------------------------------------
# Frame fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_changes_when_the_frame_gains_a_bar():
    df = _daily(120)
    assert frame_fingerprint("SYN", "1d", df) == frame_fingerprint("SYN", "1d", df.copy())
    assert frame_fingerprint("SYN", "1d", _daily(121)) != frame_fingerprint("SYN", "1d", df)


def test_fingerprint_changes_when_the_forming_bar_changes():
    df = _daily(120)
    moved = df.copy()
    moved.iloc[-1, moved.columns.get_loc("Close")] += 0.25
    assert frame_fingerprint("SYN", "1d", moved) != frame_fingerprint("SYN", "1d", df)


def test_fingerprint_with_a_nan_bar_still_matches_itself():
    df = _daily(120)
    df.iloc[-1, df.columns.get_loc("Volume")] = np.nan
    assert frame_fingerprint("SYN", "1d", df) == frame_fingerprint("SYN", "1d", df.copy())


def test_key_and_cook_id_follow_the_fingerprint():
    h = eval_hash(_g())
    k1 = make_key(h, (frame_fingerprint("SYN", "1d", _daily(120)),), WINDOW)
    k2 = make_key(h, (frame_fingerprint("SYN", "1d", _daily(121)),), WINDOW)
    assert k1 != k2 and cook_id_for(k1) != cook_id_for(k2)
    assert cook_id_for(k1) == cook_id_for(make_key(h, (frame_fingerprint("SYN", "1d", _daily(120)),), WINDOW))


def test_new_bar_gives_a_new_cook_through_the_route(client, frame):
    first = _backtest(client)["cook_id"]
    body = {"cook_id": first, "graph": _graph(), "window": WINDOW, "target": {"node_id": "/rsi"}}
    same = client.post(INSPECT, json=body).json()
    assert same["cache"] == "hit" and same["cook_id"] == first
    frame["df"] = _daily(121)                        # the next fetch has one more bar
    fresh = client.post(INSPECT, json=body).json()
    assert fresh["cache"] == "miss"
    assert fresh["cook_id"] != first
    assert fresh["total"] == 121


# ---------------------------------------------------------------------------
# Zero recompute on a hit
# ---------------------------------------------------------------------------

@pytest.fixture
def cook_spy(monkeypatch):
    calls = []
    real = evaluator_mod.cook_program

    def spy(*a, **k):
        calls.append(k.get("keep_all"))
        return real(*a, **k)

    monkeypatch.setattr(evaluator_mod, "cook_program", spy)
    return calls


def test_cache_hit_after_a_backtest_does_zero_recompute(client, cook_spy):
    cook_id = _backtest(client)["cook_id"]
    assert cook_spy == [True]              # the backtest cooked once, keeping every stream
    cook_spy.clear()

    by_id = client.post(INSPECT, json={"cook_id": cook_id, "target": {"wire_id": "w3"}})
    with_graph = client.post(INSPECT, json={
        "cook_id": cook_id, "graph": _graph(), "window": WINDOW, "target": {"node_id": "/below"},
        "filter": {"attr": "@below", "op": "is_true", "value": None},
    })
    page = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/rsi"}, "offset": 50})
    preview = client.post(PREVIEW, json={"cook_id": cook_id, "graph": _graph(), "window": WINDOW})
    for resp in (by_id, with_graph, page, preview):
        assert resp.status_code == 200, resp.text
    assert by_id.json()["cache"] == with_graph.json()["cache"] == "hit"
    assert cook_spy == []                  # nothing cooked again


def test_miss_cooks_once_then_hits(client, cook_spy):
    body = {"graph": _graph(), "window": WINDOW, "target": {"node_id": "/rsi"}}
    assert client.post(INSPECT, json=body).json()["cache"] == "miss"
    assert client.post(INSPECT, json=body).json()["cache"] == "hit"
    assert cook_spy == [True]


def test_bot_backtest_path_keeps_the_lean_cook(cook_spy):
    """run_graph_backtest (bot code) keeps only the terminals and never
    touches the cache."""
    from nodebuilder.api_models import GraphBacktestRequest
    from nodebuilder.run import run_graph_backtest

    COOK_CACHE.clear()
    req = GraphBacktestRequest.model_validate({"graph": _graph(), **WINDOW})
    resp = run_graph_backtest(req, df=_daily())
    assert cook_spy == [False]
    assert "cook_id" not in resp.model_dump()
    assert len(COOK_CACHE) == 0


# ---------------------------------------------------------------------------
# Limits and eviction
# ---------------------------------------------------------------------------

class _Store:
    def __init__(self, nbytes: int):
        self._cols = {0: np.zeros(nbytes // 8, dtype=np.float64)}
        self.length = 1


class _Result:
    def __init__(self, nbytes: int):
        self.store = _Store(nbytes)
        self.index = pd.RangeIndex(0)
        self.streams = {}


def _put(cache: CookCache, name: str, nbytes: int = 800):
    key = (name, (), "1d", "a", "b", "yahoo")
    return cache.put(key=key, graph=None, program=None, result=_Result(nbytes), window=WINDOW)


def test_lru_evicts_past_the_entry_count():
    cache = CookCache(max_entries=3)
    a, b, c = (_put(cache, x) for x in "abc")
    assert cache.get(a.cook_id) is not None      # a is now the most recent
    d = _put(cache, "d")
    assert len(cache) == 3
    assert b.cook_id not in cache                # b was the least recently used
    assert a.cook_id in cache and c.cook_id in cache and d.cook_id in cache


def test_memory_cap_evicts_least_recently_used():
    mb = 1024 * 1024
    cache = CookCache(max_entries=8, max_bytes=10 * mb)
    a = _put(cache, "a", 4 * mb)
    b = _put(cache, "b", 4 * mb)
    assert cache.total_bytes() <= 10 * mb and len(cache) == 2
    c = _put(cache, "c", 4 * mb)                 # 12 MB > 10 MB: a goes
    assert a.cook_id not in cache
    assert b.cook_id in cache and c.cook_id in cache
    assert cache.total_bytes() <= 10 * mb


def test_entry_bigger_than_the_cap_is_not_kept():
    mb = 1024 * 1024
    cache = CookCache(max_bytes=10 * mb)
    small = _put(cache, "small", mb)
    big = _put(cache, "big", 11 * mb)
    assert big.cook_id not in cache
    assert small.cook_id in cache                # nothing else was evicted for it


def test_lazy_columns_count_once_built():
    mb = 1024 * 1024
    cache = CookCache(max_bytes=10 * mb)
    a = _put(cache, "a", 4 * mb)
    b = _put(cache, "b", 4 * mb)
    # A lazy column of b gets built (6 MB more): the next look evicts a.
    b.result.store._cols[1] = np.zeros(6 * mb // 8)
    assert cache.get(b.cook_id) is b
    assert a.cook_id not in cache


def test_entries_expire_after_the_ttl_without_a_read():
    now = [1000.0]
    cache = CookCache(ttl_seconds=600, clock=lambda: now[0])
    a = _put(cache, "a")
    now[0] += 601                                # 10 minutes and 1 s without a read
    assert cache.get(a.cook_id) is None
    assert len(cache) == 0


def test_a_read_keeps_an_entry_until_the_age_cap():
    """CC-4: a Data Sheet still paging a cook keeps it past 10 minutes,
    but never past the 30-minute cap (a cook_id-only read never fetches
    the frame again, so the cap bounds how old its data can be)."""
    now = [1000.0]
    cache = CookCache(ttl_seconds=600, max_age_seconds=1800, clock=lambda: now[0])
    a = _put(cache, "a")
    for _ in range(3):                           # read every 9 minutes: 27 minutes in all
        now[0] += 540
        assert cache.get(a.cook_id) is a
    now[0] += 179                                # 29 min 59 s old, read 2 min 59 s ago
    assert cache.get(a.cook_id) is a
    now[0] += 2                                  # 30 min 1 s old: gone, read or not
    assert cache.get(a.cook_id) is None


def test_expired_cook_id_is_410_through_the_route(client, monkeypatch):
    cook_id = _backtest(client)["cook_id"]
    monkeypatch.setattr(COOK_CACHE, "ttl_seconds", -1.0)
    resp = client.post(INSPECT, json={"cook_id": cook_id, "target": {"node_id": "/rsi"}})
    assert resp.status_code == 410
    assert resp.json() == {"detail": {"code": "cook_expired"}}


# ---------------------------------------------------------------------------
# Review fixes (CC-1 .. CC-7)
# ---------------------------------------------------------------------------

def test_fingerprint_changes_when_a_finished_recent_bar_is_corrected():
    """CC-2: a late-print correction of a finished bar, the last bar unchanged."""
    df = _daily(120)
    fixed = df.copy()
    fixed.iloc[-10, fixed.columns.get_loc("Close")] += 0.25
    assert frame_fingerprint("SYN", "1d", fixed) != frame_fingerprint("SYN", "1d", df)


def test_fingerprint_changes_when_the_history_is_back_adjusted():
    """CC-2: a split or dividend correction rescales the older bars only."""
    df = _daily(120)
    adjusted = df.copy()
    adjusted.iloc[:60, :4] = adjusted.iloc[:60, :4] * 0.5
    assert frame_fingerprint("SYN", "1d", adjusted) != frame_fingerprint("SYN", "1d", df)


def test_fingerprint_with_nan_gaps_still_matches_itself():
    df = _daily(120)
    df.iloc[5:15, df.columns.get_loc("Close")] = np.nan
    assert frame_fingerprint("SYN", "1d", df) == frame_fingerprint("SYN", "1d", df.copy())


def test_a_corrected_bar_gives_a_new_cook_through_the_route(client, frame):
    first = _backtest(client)["cook_id"]
    body = {"cook_id": first, "graph": _graph(), "window": WINDOW, "target": {"node_id": "/rsi"}}
    assert client.post(INSPECT, json=body).json()["cache"] == "hit"
    fixed = frame["df"].copy()
    fixed.iloc[-5, fixed.columns.get_loc("Close")] += 1.0
    frame["df"] = fixed
    fresh = client.post(INSPECT, json=body).json()
    assert fresh["cache"] == "miss" and fresh["cook_id"] != first


def test_key_covers_every_frame_the_cook_read():
    """CC-3: one helper keys every cook, over all of its frames."""
    from nodebuilder.cook_cache import key_for
    df, ref = _daily(120), _daily(121)
    main_only = key_for(_g(), (("SYN", "1d", df),), WINDOW)
    assert main_only == make_key(eval_hash(_g()), (frame_fingerprint("SYN", "1d", df),), WINDOW)
    with_ref = key_for(_g(), (("SYN", "1d", df), ("SPY", "1d", ref)), WINDOW)
    assert with_ref != main_only
    with pytest.raises(ValueError):
        key_for(_g(), (), WINDOW)


def test_both_cook_paths_record_the_frames_they_read(frame):
    from nodebuilder.api_models import GraphBacktestRequest
    from nodebuilder.run import cook_graph_window, run_graph_backtest_cooked

    df = frame["df"]
    cook = cook_graph_window(Graph.model_validate(_graph()), df=df, **WINDOW)
    assert [(s, i) for s, i, _ in cook.frames] == [("SYN", "1d")] and cook.frames[0][2] is df
    req = GraphBacktestRequest.model_validate({"graph": _graph(), **WINDOW})
    _resp, cook = run_graph_backtest_cooked(req, df)
    assert [(s, i) for s, i, _ in cook.frames] == [("SYN", "1d")] and cook.frames[0][2] is df


def test_a_cook_too_big_to_keep_says_so(client, monkeypatch):
    """CC-1: the cook_id of a cook that was not kept is marked kept=false,
    so the client sends the graph and window instead of taking a 410."""
    body = {"cook_id": None, "graph": _graph(), "window": WINDOW}
    kept = client.post(INSPECT, json={**body, "target": {"node_id": "/rsi"}}).json()
    assert kept["kept"] is True and kept["stale_data"] is False
    COOK_CACHE.clear()
    monkeypatch.setattr(COOK_CACHE, "max_bytes", 1)
    resp = client.post(INSPECT, json={**body, "target": {"node_id": "/rsi"}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["kept"] is False and resp.json()["cook_id"] not in COOK_CACHE
    prev = client.post(PREVIEW, json=body).json()
    assert prev["kept"] is False and prev["stale_data"] is False


@pytest.mark.parametrize("url,extra", [
    (INSPECT, {"target": {"node_id": "/rsi"}}),
    (PREVIEW, {}),
])
def test_neither_cook_id_nor_graph_is_422_not_410(client, url, extra):
    """CC-5: 410 stays for a cook_id that was given and is gone."""
    resp = client.post(url, json={"cook_id": None, "graph": None, **extra})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "cook_or_graph_required"


def _fetch_fails(monkeypatch, exc=None):
    import shared

    def boom(*a, **k):
        raise exc or ConnectionError("provider down")

    monkeypatch.setattr(shared, "_fetch", boom)


@pytest.mark.parametrize("send_cook_id", [True, False])
def test_fetch_failure_serves_the_last_good_cook_marked_stale(client, monkeypatch, send_cook_id):
    """CC-6: a provider outage does not blank the sheet or the sparklines."""
    cook_id = _backtest(client)["cook_id"]
    _fetch_fails(monkeypatch)
    body = {"cook_id": cook_id if send_cook_id else None, "graph": _graph(), "window": WINDOW}
    resp = client.post(INSPECT, json={**body, "target": {"node_id": "/rsi"}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["cook_id"] == cook_id and resp.json()["stale_data"] is True
    prev = client.post(PREVIEW, json=body)
    assert prev.status_code == 200 and prev.json()["stale_data"] is True


def test_fetch_failure_never_serves_another_graphs_cook(client, monkeypatch):
    cook_id = _backtest(client)["cook_id"]
    _fetch_fails(monkeypatch)
    edited = _graph()
    edited["nodes"]["/rsi"]["params"]["period"] = 7
    resp = client.post(PREVIEW, json={"cook_id": cook_id, "graph": edited, "window": WINDOW})
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "data_unavailable"


def test_fetch_failure_with_no_live_cook_is_a_coded_502(client, monkeypatch):
    _fetch_fails(monkeypatch)
    resp = client.post(INSPECT, json={
        "cook_id": None, "graph": _graph(), "window": WINDOW, "target": {"node_id": "/rsi"},
    })
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "data_unavailable"


def test_no_data_with_no_live_cook_keeps_the_400_graph_error(client, frame):
    frame["df"] = _daily(120).iloc[0:0]
    resp = client.post(PREVIEW, json={"cook_id": None, "graph": _graph(), "window": WINDOW})
    assert resp.status_code == 400
    assert resp.json()["code"] == "request_invalid"


def test_concurrent_misses_on_one_key_cook_once():
    """CC-7: single flight."""
    import threading
    import time as _time

    cache = CookCache()
    key = ("g", (), "1d", "a", "b", "yahoo")
    calls = []

    def cook():
        calls.append(1)
        _time.sleep(0.2)
        return cache.put(key=key, graph=None, program=None, result=_Result(800), window=WINDOW)

    out = []
    threads = [threading.Thread(target=lambda: out.append(cache.get_or_cook(key, cook))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(calls) == 1
    assert len(out) == 4 and len({e.cook_id for e, _ in out}) == 1
    assert sorted(state for _, state in out) == ["hit", "hit", "hit", "miss"]


def test_a_failed_first_cook_lets_the_waiting_request_cook():
    import threading
    import time as _time

    cache = CookCache()
    key = ("g", (), "1d", "a", "b", "yahoo")
    calls = []

    def cook():
        calls.append(1)
        _time.sleep(0.1)
        if len(calls) == 1:
            raise RuntimeError("first cook fails")
        return cache.put(key=key, graph=None, program=None, result=_Result(800), window=WINDOW)

    results, errors = [], []

    def run():
        try:
            results.append(cache.get_or_cook(key, cook))
        except RuntimeError as e:
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
        _time.sleep(0.02)                        # the first thread leads
    for t in threads:
        t.join(5)
    assert len(errors) == 1 and len(results) == 1 and results[0][1] == "miss"
    assert len(calls) == 2
    assert cache._inflight == {}


def test_a_cook_too_big_to_keep_is_cooked_again_by_a_waiter():
    cache = CookCache(max_bytes=100)
    key = ("g", (), "1d", "a", "b", "yahoo")
    calls = []

    def cook():
        calls.append(1)
        return cache.put(key=key, graph=None, program=None, result=_Result(800), window=WINDOW)

    for _ in range(2):
        entry, state = cache.get_or_cook(key, cook)
        assert state == "miss" and entry.cook_id not in cache
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Live bots never touch the cache (plan D6 acceptance)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module", ["bot_runner.py", "bot_manager.py", "nodebuilder/run.py"])
def test_bot_code_never_imports_the_cook_cache(module):
    source = (BACKEND / module).read_text()
    assert not re.search(r"cook_cache", source), f"{module} mentions cook_cache"

"""W2 item 2.B: the stream semantics, end to end (plan D4, section 3).

- Rules drawn by auto_render give the rule engine's answer on every bar,
  not only the same trades: eval_rules bar by bar, including negated rules
  and the bar-0 guard, for every indicator x condition x negated.
- /validate returns each node's output stream (plan 3.3 form).
- Operand order comes from the a / b params: deleting wires and adding
  them back in another order never swaps operands.
- A name clash is an error when the name is read, a warning when not.
"""
from __future__ import annotations

import copy
from typing import get_args

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from models import StrategyRequest
from nodebuilder.compile import compile as compile_graph
from nodebuilder.evaluator import cook_signals
from nodebuilder.from_rules import auto_render
from nodebuilder.kernel import registry
from nodebuilder.kernel.stream import STREAM_SCHEMA_VERSION, is_attr_name
from nodebuilder.models import Graph
from routes.nodebuilder import router
from signal_engine import (
    Rule, RuleCondition, RuleIndicator, compute_indicators, eval_rules, migrate_rule,
)
from tests.nodebuilder.test_rule_coverage import _DF, _PARTNER_SELL, _VARIANTS, _make_rule

_APP = FastAPI()
_APP.include_router(router)
_CLIENT = TestClient(_APP)


def _validate(graph: dict) -> dict:
    response = _CLIENT.post("/api/nodebuilder/validate", json={"graph": graph})
    assert response.status_code == 200
    return response.json()


# ---------------------------------------------------------------------------
# Bar for bar: the graph's entry column equals eval_rules
# ---------------------------------------------------------------------------

_INDICATORS_CACHE: dict = {}
_INDICATORS_CACHE_B: dict = {}   # one cache per frame: the cache is not keyed by data


def _rule_column(rule: Rule, df=None, cache=None) -> np.ndarray:
    """eval_rules([rule]) on every bar, as the rule backtest computes it
    (run_backtest migrates legacy rules such as ema20 before computing)."""
    if df is None:
        df, cache = _DF, _INDICATORS_CACHE
    ind = compute_indicators(df["Close"], high=df["High"], low=df["Low"],
                             volume=df["Volume"], rules=[migrate_rule(rule)],
                             cache=cache)
    return np.array([eval_rules([rule], "AND", ind, i) for i in range(len(df))], dtype=bool)


def _graph_column(rule: Rule, df=None) -> np.ndarray:
    req = StrategyRequest(ticker="SYN", buy_rules=[rule], sell_rules=[_PARTNER_SELL])
    entry, _exit = cook_signals(compile_graph(auto_render(req)), _DF if df is None else df)
    return entry


def _varies(x: np.ndarray) -> bool:
    """True when the column is neither all False nor all True after bar 0."""
    return bool(x[1:].any() and not x[1:].all())


# (indicator, condition) pairs whose rule column is constant after bar 0 on
# _DF, plain and negated alike (F435 W2 KC-3; measured, exact).  On those a
# graph with the wrong operand or period would still match, so each must
# vary on _FRAME_B below or be listed in _CONSTANT_EVERYWHERE with a reason.
# The sweep fails when a pair here starts to vary (drop it) or a pair not
# here is constant (a new blind spot).
_CONSTANT_ON_DF = frozenset({
    ("rsi", "is_below_signal"), ("ma", "is_above_signal"), ("ma", "is_below_signal"),
    ("bb", "is_below_signal"), ("volume", "is_above_signal"), ("volume", "is_below_signal"),
    ("stochastic", "is_below_signal"), ("adx", "is_above_signal"), ("adx", "is_below_signal"),
    ("price", "is_above_signal"), ("price", "is_below_signal"),
    ("ema20", "is_above_signal"), ("ema20", "is_below_signal"),
    ("ema50", "crossover_up"), ("ema50", "crosses_above"),
    ("ema50", "is_above_signal"), ("ema50", "is_below_signal"),
    ("ema200", "crossover_up"), ("ema200", "crosses_above"),
    ("ema200", "is_above_signal"), ("ema200", "is_below_signal"),
    ("ma8", "is_below_signal"), ("ma21", "is_below_signal"),
})

# Constant on _FRAME_B too, by construction: is_above_signal /
# is_below_signal compare the indicator with the default MACD's signal line
# (signal_engine eval_rule).  A price-level average (MA, EMA, the upper
# Bollinger band) sits far above a signal line that swings around 0 on any
# positive price series, and stochastic / ADX stay above it on both frames.
_CONSTANT_EVERYWHERE = frozenset({
    ("ma", "is_above_signal"), ("ma", "is_below_signal"), ("bb", "is_below_signal"),
    ("stochastic", "is_below_signal"), ("adx", "is_above_signal"), ("adx", "is_below_signal"),
    ("ema20", "is_above_signal"), ("ema20", "is_below_signal"),
    ("ema50", "is_above_signal"), ("ema50", "is_below_signal"),
    ("ma8", "is_below_signal"), ("ma21", "is_below_signal"),
})


def _frame_b(n: int = 600, seed: int = 3):
    """A second frame on which most of _CONSTANT_ON_DF fires.

    Sawtooth closes: slow climbs from about 5 to 500 then a one-bar crash, so
    the lagging MACD signal line sits above the price for a few bars after
    each crash (price / RSI / EMA200 vs the signal line), the EMA50 and
    EMA200 cross the rule threshold, and a small volume (about 0 to 60)
    meets the signal line.
    """
    import pandas as pd

    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = np.maximum(5 + 495 * ((t % 120) / 119.0) + rng.normal(0, 1.0, n), 1.0)
    open_ = close + rng.normal(0, 0.5, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.8, n))
    low = np.maximum(np.minimum(open_, close) - np.abs(rng.normal(0, 0.8, n)), 0.5)
    volume = (30 + 30 * np.sin(t / 9.0) + rng.normal(0, 3, n)).clip(1).astype("int64")
    idx = pd.date_range("2020-01-02", periods=n, freq="B", tz="America/New_York", name="Date")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                         "Volume": volume, "Dividends": 0.0, "Stock Splits": 0.0}, index=idx)


_FRAME_B = _frame_b()
_ON_FRAME_B = sorted(_CONSTANT_ON_DF - _CONSTANT_EVERYWHERE)


_SWEEP = [(i, c, n) for i in get_args(RuleIndicator) for c in get_args(RuleCondition)
          for n in (False, True)]


@pytest.mark.parametrize("indicator,condition,negated", _SWEEP,
                         ids=[f"{i}-{c}-{'not' if n else 'plain'}" for i, c, n in _SWEEP])
def test_every_rule_matches_eval_rules_on_every_bar(indicator, condition, negated):
    rule = _make_rule(indicator, condition, negated)
    want = _rule_column(rule)
    got = _graph_column(rule)
    assert not got[0], "bar 0 is always False (the rule engine's i < 1 guard)"
    diff = np.flatnonzero(got != want)
    assert diff.size == 0, f"first different bar {diff[:5]}: graph {got[diff[:5]]}, rules {want[diff[:5]]}"
    # A constant column would match any constant graph (KC-3).
    if (indicator, condition) in _CONSTANT_ON_DF:
        assert not _varies(want), "now varies on _DF: drop it from _CONSTANT_ON_DF"
    else:
        assert _varies(want), "constant on _DF: add it to _CONSTANT_ON_DF and cover it"


_B_CASES = [(i, c, n) for i, c in _ON_FRAME_B for n in (False, True)]


@pytest.mark.parametrize("indicator,condition,negated", _B_CASES,
                         ids=[f"{i}-{c}-{'not' if n else 'plain'}" for i, c, n in _B_CASES])
def test_rules_constant_on_the_main_frame_match_on_a_second_frame(indicator, condition, negated):
    """The pairs the main sweep cannot tell apart from a constant graph,
    bar for bar on a frame where they fire (F435 W2 KC-3)."""
    rule = _make_rule(indicator, condition, negated)
    want = _rule_column(rule, _FRAME_B, _INDICATORS_CACHE_B)
    assert _varies(want), "constant on _FRAME_B too: move it to _CONSTANT_EVERYWHERE"
    got = _graph_column(rule, _FRAME_B)
    diff = np.flatnonzero(got != want)
    assert diff.size == 0, f"first different bar {diff[:5]}: graph {got[diff[:5]]}, rules {want[diff[:5]]}"


@pytest.mark.parametrize("indicator,condition", sorted(_CONSTANT_EVERYWHERE))
def test_rules_constant_on_both_frames_still_match(indicator, condition):
    """The allow-listed pairs: constant by construction, still equal."""
    for negated in (False, True):
        rule = _make_rule(indicator, condition, negated)
        want = _rule_column(rule, _FRAME_B, _INDICATORS_CACHE_B)
        assert not _varies(want), "now varies on _FRAME_B: drop it from _CONSTANT_EVERYWHERE"
        assert np.array_equal(_graph_column(rule, _FRAME_B), want)


@pytest.mark.parametrize("name,_expect,rule", _VARIANTS, ids=[v[0] for v in _VARIANTS])
def test_variants_match_eval_rules_on_every_bar(name, _expect, rule):
    assert np.array_equal(_graph_column(rule), _rule_column(rule)), name


def test_negated_never_rule_is_true_from_bar_1():
    """A rule the engine never fires, negated: False on bar 0, True after."""
    rule = Rule(indicator="price", condition="above", param="close", negated=True)
    got = _graph_column(rule)
    assert not got[0] and got[1:].all()


# ---------------------------------------------------------------------------
# What auto_render writes is v3
# ---------------------------------------------------------------------------


def _all_rules_request() -> StrategyRequest:
    rules = [_make_rule(i, c, False) for i, c, _n in _SWEEP[::7]]
    return StrategyRequest(ticker="SYN", buy_rules=rules[:40], sell_rules=rules[40:],
                           buy_logic="OR", sell_logic="OR")


def test_auto_render_names_every_read_and_write():
    graph = auto_render(_all_rules_request())
    assert graph.version == 3
    assert all(w.attr is None for w in graph.wires)
    for node in graph.nodes.values():
        nt = registry.get(node.type)
        assert nt is not None, node.type
        for spec in nt.write_params():
            assert is_attr_name(node.params.get(spec.name)), (node.id, spec.name)
        if node.type == "ticker":
            assert "source" not in node.params
    # Write names are unique in the graph.
    writes = [node.params[s.name] for node in graph.nodes.values()
              for s in registry.get(node.type).write_params()]
    assert len(writes) == len(set(writes))
    compile_graph(graph)


# ---------------------------------------------------------------------------
# /validate streams
# ---------------------------------------------------------------------------


def test_validate_returns_each_nodes_output_stream():
    rule = Rule(indicator="macd", condition="crosses_above", param="signal")
    req = StrategyRequest(ticker="SYN", buy_rules=[rule], sell_rules=[_PARTNER_SELL])
    graph = auto_render(req).model_dump(by_alias=True)
    body = _validate(graph)
    assert body["ok"] is True
    assert body["stream_schema"] == STREAM_SCHEMA_VERSION
    streams = body["streams"]
    for node_id, schema in streams.items():
        assert schema["stream_schema"] == STREAM_SCHEMA_VERSION, node_id
        assert schema["prims"] == []
        for info in schema["points"] + schema["detail"]:
            assert set(info) == {"name", "dtype", "written_by"}
            assert is_attr_name(info["name"])

    def names(node_id, kind="points"):
        return {i["name"]: (i["dtype"], i["written_by"]) for i in streams[node_id][kind]}

    ticker = "/ticker_syn_1d_yahoo"
    macd = "/macd_fast_12_signal_9_slow_26"
    assert names(ticker)["@close"] == ("float", ticker)
    assert {"@open", "@high", "@low", "@close", "@volume", "@time", "@index"} <= set(names(ticker))
    # A node's output is its input plus what it writes.
    assert names(macd)["@macd_signal"] == ("float", macd)
    assert names(macd)["@close"] == ("float", ticker)
    assert names("/cmp_buy_0")["@xa"] == ("bool", "/cmp_buy_0")
    assert "@macd_signal" in names("/cmp_buy_0")
    # Settings write detail values.
    assert names("/setting_slippage", "detail")["@slippage_bps"] == ("float", "/setting_slippage")


def test_validate_leaves_out_a_broken_node_and_what_reads_it():
    nodes = {
        "/t": {"id": "/t", "type": "ticker", "params": {"symbol": "SYN"}},
        "/r": {"id": "/r", "type": "rsi", "params": {"source": "@nope"}},
        "/a": {"id": "/a", "type": "above", "params": {"threshold": 50}},
        "/e": {"id": "/e", "type": "entry", "params": {}},
    }
    wires = [
        {"id": "w1", "from": "/t", "to": "/r", "to_port": "in0"},
        {"id": "w2", "from": "/r", "to": "/a", "to_port": "in0"},
        {"id": "w3", "from": "/a", "to": "/e", "to_port": "in0"},
    ]
    body = _validate({"_version": 3, "nodes": nodes, "wires": wires})
    assert body["ok"] is False
    assert [(d["code"], d["node_id"], d["param"]) for d in body["diagnostics"]
            if d["severity"] == "error"] == [("attr_missing", "/r", "source")]
    assert "/t" in body["streams"]
    assert "/r" not in body["streams"] and "/a" not in body["streams"]


def test_validate_of_a_graph_that_does_not_parse_has_no_streams():
    body = _validate({"_version": 3, "nodes": [], "wires": []})
    assert body["ok"] is False and body["streams"] == {}


# ---------------------------------------------------------------------------
# Operand order comes from a / b, never from the wires
# ---------------------------------------------------------------------------


def _fast_slow_graph() -> dict:
    """EMA(5) crosses above SMA(30), named by params; the slow line on in0."""
    nodes = {
        "/t": {"id": "/t", "type": "ticker", "params": {"symbol": "SYN", "interval": "1d"}},
        "/f": {"id": "/f", "type": "ema", "params": {"period": 5}},
        "/s": {"id": "/s", "type": "sma", "params": {"period": 30}},
        "/x": {"id": "/x", "type": "crosses_above", "params": {"a": "@ema", "b": "@sma"}},
        "/e": {"id": "/e", "type": "entry", "params": {}},
    }
    wires = [
        {"id": "w1", "from": "/t", "to": "/f", "to_port": "in0"},
        {"id": "w2", "from": "/t", "to": "/s", "to_port": "in0"},
        {"id": "w3", "from": "/s", "to": "/x", "to_port": "in0"},
        {"id": "w4", "from": "/f", "to": "/x", "to_port": "in1"},
        {"id": "w5", "from": "/x", "to": "/e", "to_port": "in0"},
    ]
    return {"_version": 3, "nodes": nodes, "wires": wires}


def _entry(data: dict) -> np.ndarray:
    entry, _exit = cook_signals(compile_graph(Graph.model_validate(copy.deepcopy(data))), _DF)
    return entry


def test_rewiring_never_swaps_operands():
    base = _fast_slow_graph()
    want = _entry(base)
    assert want.any()

    # Delete both comparison inputs and add them back the other way round.
    rewired = copy.deepcopy(base)
    rewired["wires"] = [w for w in rewired["wires"] if w["to"] != "/x"]
    rewired["wires"] += [
        {"id": "w6", "from": "/f", "to": "/x", "to_port": "in0"},
        {"id": "w7", "from": "/s", "to": "/x", "to_port": "in1"},
    ]
    assert np.array_equal(_entry(rewired), want)

    # The same with the wires listed in reverse.
    reordered = copy.deepcopy(base)
    reordered["wires"].reverse()
    assert np.array_equal(_entry(reordered), want)

    # Only the params decide: swapping them changes the answer.
    swapped = copy.deepcopy(base)
    swapped["nodes"]["/x"]["params"].update(a="@sma", b="@ema")
    assert not np.array_equal(_entry(swapped), want)


def test_migrated_graph_keeps_operands_after_rewiring():
    """A v2 graph whose operands came from wire order keeps them once
    migrated, even after its wires are swapped."""
    v2 = _fast_slow_graph()
    v2["_version"] = 2
    v2["nodes"]["/x"]["params"] = {}
    for w in v2["wires"]:
        if w["to"] == "/x":
            w["attr"] = "@sma" if w["from"] == "/s" else "@ema"
    migrated = Graph.model_validate(copy.deepcopy(v2)).model_dump(by_alias=True)
    assert migrated["nodes"]["/x"]["params"]["a"] == "@sma"
    want = _entry(migrated)
    for w in migrated["wires"]:
        if w["to"] == "/x":
            w["to_port"] = "in1" if w["to_port"] == "in0" else "in0"
    assert np.array_equal(_entry(migrated), want)


# ---------------------------------------------------------------------------
# Name clash: an error when read, a warning when not
# ---------------------------------------------------------------------------


def _clash_graph(read_it: bool) -> dict:
    """Two RSI nodes both write @rsi and meet at an AND."""
    graph = _fast_slow_graph()
    nodes = graph["nodes"]
    nodes["/r1"] = {"id": "/r1", "type": "rsi", "params": {"period": 14, "out": "@rsi"}}
    nodes["/r2"] = {"id": "/r2", "type": "rsi", "params": {"period": 7, "out": "@rsi"}}
    nodes["/lo"] = {"id": "/lo", "type": "below", "params": {"a": "@rsi", "threshold": 40}}
    nodes["/hi"] = {"id": "/hi", "type": "above", "params": {"a": "@rsi", "threshold": 60}}
    nodes["/and"] = {"id": "/and", "type": "and", "params": {"terms": ["@below", "@above"]}}
    graph["wires"] += [
        {"id": "c1", "from": "/t", "to": "/r1", "to_port": "in0"},
        {"id": "c2", "from": "/t", "to": "/r2", "to_port": "in0"},
        {"id": "c3", "from": "/r1", "to": "/lo", "to_port": "in0"},
        {"id": "c4", "from": "/r2", "to": "/hi", "to_port": "in0"},
        {"id": "c5", "from": "/lo", "to": "/and", "to_port": "in0"},
        {"id": "c6", "from": "/hi", "to": "/and", "to_port": "in1"},
    ]
    if read_it:
        nodes["/top"] = {"id": "/top", "type": "above", "params": {"a": "@rsi", "threshold": 50}}
        graph["wires"].append({"id": "c7", "from": "/and", "to": "/top", "to_port": "in0"})
    return graph


def test_clash_is_a_warning_when_nothing_reads_the_name():
    body = _validate(_clash_graph(read_it=False))
    # The kernel names an unread clash attr_shadowed (a warning, plan 4.2).
    clashes = [(d["code"], d["severity"], d["node_id"]) for d in body["diagnostics"]
               if d["code"] in ("attr_clash", "attr_shadowed")]
    assert body["ok"] is True
    assert clashes == [("attr_shadowed", "warning", "/and")]
    # The clashing name is hidden below the meeting point.
    assert "@rsi" not in {i["name"] for i in body["streams"]["/and"]["points"]}


def test_clash_is_an_error_when_the_name_is_read():
    body = _validate(_clash_graph(read_it=True))
    errors = [d for d in body["diagnostics"] if d["severity"] == "error"]
    assert body["ok"] is False
    assert [(d["code"], d["node_id"]) for d in errors] == [("attr_clash", "/top")]


# ---------------------------------------------------------------------------
# Long rule lists: AND / OR nodes take 16 signals, a rule list up to 100
# ---------------------------------------------------------------------------

_NEVER = Rule(indicator="price", condition="above", param="close")   # never fires
_ALWAYS = _NEVER.model_copy(update={"negated": True})                  # True from bar 1
_REAL = Rule(indicator="rsi", condition="below", value=45, params={"period": 14, "type": "wilder"})
_REAL_2 = Rule(indicator="ma", condition="rising", params={"period": 10, "type": "sma"})


def _muted(rule: Rule) -> Rule:
    return rule.model_copy(update={"muted": True})


_LONG_LISTS = {
    "or-40": ([_NEVER] * 39 + [_REAL], "OR"),
    "and-40": ([_ALWAYS] * 39 + [_REAL], "AND"),
    "and-100": ([_REAL_2] + [_ALWAYS] * 98 + [_REAL], "AND"),
    # A whole first part muted: those blockers are skipped, not "off = False".
    "and-first-part-muted": ([_muted(_NEVER)] * 16 + [_ALWAYS] * 10 + [_REAL], "AND"),
    "or-muted-mix": ([_muted(_ALWAYS)] * 20 + [_NEVER] * 5 + [_REAL_2], "OR"),
    "and-all-muted": ([_muted(_REAL)] * 20, "AND"),
}


@pytest.mark.parametrize("name", sorted(_LONG_LISTS))
def test_long_rule_lists_match_eval_rules(name):
    rules, logic = _LONG_LISTS[name]
    req = StrategyRequest(ticker="SYN", buy_rules=rules, buy_logic=logic,
                          sell_rules=[_PARTNER_SELL])
    graph = auto_render(req)
    if len(rules) > 16:
        parts = [n for n in graph.nodes if n.startswith("/logic_buy_part")]
        assert parts, "a list over 16 rules is split into parts"
    entry, _exit = cook_signals(compile_graph(graph), _DF)
    migrated = [migrate_rule(r) for r in rules]
    ind = compute_indicators(_DF["Close"], high=_DF["High"], low=_DF["Low"],
                             volume=_DF["Volume"], rules=migrated)
    want = np.array([eval_rules(migrated, logic, ind, i) for i in range(len(_DF))], dtype=bool)
    assert np.array_equal(entry, want)
    if name != "and-all-muted":
        assert want.any() and not want.all()

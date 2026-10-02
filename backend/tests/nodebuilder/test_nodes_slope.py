"""Slope nodes against the rule engine (F435 W2 item 2.C).

Every slope node must give, bar for bar, what signal_engine.eval_rules gives
for the same rule, plain and negated (a NOT node after it).  The series are
synthetic (no network) and built to hit the edges: flat steps (> against
>=), NaN gaps (the rule engine lets a NaN step pass in turns_up), values of
exactly zero (the min-move guard), indicator warmup NaN, bar 0.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nodebuilder.compile import compile as nb_compile, compile_with_diagnostics
from nodebuilder.evaluator import cook_signals
from nodebuilder.models import Graph
from signal_engine import Rule, compute_indicators, eval_rules

N = 300


def _frame(close: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=len(close), freq="B", tz="America/New_York")
    high = close + 0.5
    low = close - 0.5
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": np.full(len(close), 1000.0)}, index=idx)


def _walk(seed: int, centre: float = 100.0, nan_gap: bool = True) -> pd.DataFrame:
    """A rounded random walk: many flat steps, a few NaN bars."""
    rng = np.random.default_rng(seed)
    close = centre + np.round(np.cumsum(rng.normal(0, 1.0, N)))
    if nan_gap:
        close[120:123] = np.nan
        close[200] = np.nan
    return _frame(close)


FRAMES = {
    "walk": _walk(3),
    "walk_nan_free": _walk(5, nan_gap=False),
    # Crosses zero again and again, with exact zeros: the min-move guard.
    "around_zero": _walk(7, centre=0.0),
}


def _graph(nodes: dict, wires: list) -> Graph:
    return Graph.model_validate({
        "_version": 2,
        "nodes": {nid: {"id": nid, "type": t, "params": p} for nid, (t, p) in nodes.items()},
        "wires": [{"id": f"w{i}", "from": a, "to": b, "to_port": port}
                  for i, (a, b, port) in enumerate(wires)],
    })


def _slope_graph(node_type: str, params: dict, negated: bool, rsi: dict | None = None) -> Graph:
    """Ticker [-> RSI] -> slope node [-> NOT] -> Entry."""
    nodes = {"/t": ("ticker", {})}
    wires = []
    src = "/t"
    if rsi is not None:
        nodes["/rsi"] = ("rsi", rsi)
        wires.append(("/t", "/rsi", "in0"))
        src = "/rsi"
    else:
        params = {"a": "@close", **params}
    nodes["/s"] = (node_type, params)
    wires.append((src, "/s", "in0"))
    last = "/s"
    if negated:
        nodes["/not"] = ("not", {})
        wires.append(("/s", "/not", "in0"))
        last = "/not"
    nodes["/e"] = ("entry", {})
    wires.append((last, "/e", "in0"))
    return _graph(nodes, wires)


def _rule_signal(df: pd.DataFrame, rule: Rule) -> np.ndarray:
    ind = compute_indicators(df["Close"], df["High"], df["Low"], df["Volume"], rules=[rule])
    return np.array([eval_rules([rule], "AND", ind, i) for i in range(len(df))], dtype=bool)


def _node_signal(df: pd.DataFrame, graph: Graph) -> np.ndarray:
    entry, _exit = cook_signals(nb_compile(graph), df)
    return np.asarray(entry, dtype=bool)


# (condition, rule extras, node params).  The node params are what the v3
# migration writes for the rule: value -> bars (clamped as the rule engine
# clamps it) or threshold, rule.threshold -> min_pct.
CASES = [
    ("rising", {}, {}),
    ("falling", {}, {}),
    ("rising_over", {"value": 3}, {"bars": 3}),
    ("rising_over", {}, {"bars": 10}),
    ("rising_over", {"value": 0}, {"bars": 0}),
    ("falling_over", {"value": 3}, {"bars": 3}),
    ("falling_over", {"value": 17}, {"bars": 17}),
    ("turns_up", {}, {"bars": 1}),
    ("turns_up", {"value": 2}, {"bars": 2}),
    ("turns_up", {"value": 3}, {"bars": 3}),
    ("turns_up", {"value": 1, "threshold": 0.5}, {"bars": 1, "min_pct": 0.5}),
    ("turns_up", {"value": 2, "threshold": 3.0}, {"bars": 2, "min_pct": 3.0}),
    ("turns_up", {"threshold": 0.0}, {"bars": 1, "min_pct": 0.0}),
    ("turns_down", {}, {"bars": 1}),
    ("turns_down", {"value": 2}, {"bars": 2}),
    ("turns_down", {"value": 1, "threshold": 0.5}, {"bars": 1, "min_pct": 0.5}),
    ("turns_down", {"value": 3, "threshold": 2.0}, {"bars": 3, "min_pct": 2.0}),
    ("turns_up_below", {"value": 100.0}, {"threshold": 100.0}),
    ("turns_up_below", {"value": 0.0}, {"threshold": 0.0}),
    ("turns_down_above", {"value": 100.0}, {"threshold": 100.0}),
    ("turns_down_above", {"value": 0.0}, {"threshold": 0.0}),
    ("accelerating", {}, {}),
    ("decelerating", {}, {}),
]


def _case_id(case) -> str:
    cond, extra, _p = case
    return cond + "".join(f"-{k}{v}" for k, v in extra.items())


@pytest.mark.parametrize("frame", list(FRAMES))
@pytest.mark.parametrize("negated", [False, True], ids=["plain", "not"])
@pytest.mark.parametrize("case", CASES, ids=[_case_id(c) for c in CASES])
def test_slope_on_price_matches_rule_engine(case, negated, frame):
    cond, extra, params = case
    df = FRAMES[frame]
    rule = Rule(indicator="price", condition=cond, negated=negated, **extra)
    want = _rule_signal(df, rule)
    got = _node_signal(df, _slope_graph(cond, params, negated))
    np.testing.assert_array_equal(got, want)


RSI_CASES = [c for c in CASES if c[0] not in ("turns_up_below", "turns_down_above")] + [
    ("turns_up_below", {"value": 40.0}, {"threshold": 40.0}),
    ("turns_down_above", {"value": 60.0}, {"threshold": 60.0}),
]


@pytest.mark.parametrize("negated", [False, True], ids=["plain", "not"])
@pytest.mark.parametrize("case", RSI_CASES, ids=[_case_id(c) for c in RSI_CASES])
def test_slope_on_rsi_warmup_matches_rule_engine(case, negated):
    """RSI has NaN warmup bars; the rule engine's NaN handling must carry over."""
    cond, extra, params = case
    df = FRAMES["walk_nan_free"]
    rsi = {"period": 14, "type": "sma"}
    rule = Rule(indicator="rsi", condition=cond, negated=negated, params=rsi, **extra)
    want = _rule_signal(df, rule)
    got = _node_signal(df, _slope_graph(cond, params, negated, rsi=rsi))
    np.testing.assert_array_equal(got, want)


def test_cases_are_not_vacuous():
    """The sweep would pass on all-False columns; make sure it does not."""
    for cond, extra, params in CASES:
        if cond in ("rising_over", "falling_over") and params.get("bars") == 0:
            continue
        got = np.concatenate([_node_signal(df, _slope_graph(cond, params, False))
                              for df in FRAMES.values()])
        assert got.any(), (cond, params)
        assert not got.all(), (cond, params)


def test_turns_up_lets_a_nan_step_pass_like_the_rule_engine():
    """A quirk kept on purpose: during NaN bars turns_up is True in both."""
    close = np.array([5.0, 4.0, np.nan, np.nan, np.nan, 6.0, 7.0])
    df = _frame(close)
    rule = Rule(indicator="price", condition="turns_up")
    want = _rule_signal(df, rule)
    got = _node_signal(df, _slope_graph("turns_up", {"bars": 1}, False))
    np.testing.assert_array_equal(got, want)
    assert got[3] and got[4]


def test_min_pct_suppresses_small_turns():
    df = FRAMES["walk_nan_free"]
    loose = _node_signal(df, _slope_graph("turns_up", {"bars": 1}, False))
    tight = _node_signal(df, _slope_graph("turns_up", {"bars": 1, "min_pct": 1.5}, False))
    assert tight.sum() < loose.sum()
    assert not (tight & ~loose).any()


def test_zero_pivot_never_passes_a_min_move():
    close = np.array([3.0, 2.0, 1.0, 0.0, 1.0, 2.0])
    df = _frame(close)
    got = _node_signal(df, _slope_graph("turns_up", {"bars": 1, "min_pct": 0.1}, False))
    assert not got[4]          # turns up from exactly 0: refused, as in the rule engine
    np.testing.assert_array_equal(got, _rule_signal(
        df, Rule(indicator="price", condition="turns_up", threshold=0.1)))


@pytest.mark.parametrize("cond,params", [(c, p) for c, _e, p in CASES])
@pytest.mark.parametrize("length", [1, 2, 3])
def test_short_frames_and_bar_zero(cond, params, length):
    df = _frame(np.array([1.0, 3.0, 2.0])[:length])
    got = _node_signal(df, _slope_graph(cond, params, False))
    assert got.shape == (length,)
    assert not got[0]
    negated = _node_signal(df, _slope_graph(cond, params, True))
    assert not negated[0]      # NOT keeps bar 0 False, like eval_rules


def test_bool_input_counts_as_zero_and_one():
    """A slope of a signal (signals of signals): rising = it just turned on."""
    close = np.array([1.0, 3.0, 2.0, 4.0, 5.0, 1.0, 6.0])
    df = _frame(close)
    g = _graph({"/t": ("ticker", {}),
                "/up": ("rising", {"a": "@close", "out": "@up"}),
                "/on": ("rising", {"a": "@up", "out": "@on"}),
                "/e": ("entry", {"signal": "@on"})},
               [("/t", "/up", "in0"), ("/up", "/on", "in0"), ("/on", "/e", "in0")])
    got = _node_signal(df, g)
    up = np.r_[False, close[1:] > close[:-1]]
    on = np.r_[False, up[1:] & ~up[:-1]]
    np.testing.assert_array_equal(got, on)


def test_lookbacks():
    def need(cond, params):
        return nb_compile(_slope_graph(cond, params, False)).required_lookback_bars
    assert need("rising", {}) == 1
    assert need("rising_over", {"bars": 7}) == 7
    assert need("turns_up", {"bars": 3}) == 4
    assert need("accelerating", {}) == 2


@pytest.mark.parametrize("cond,params,code", [
    ("turns_up_below", {}, "param_invalid"),               # threshold is required
    ("rising_over", {"bars": 501}, "param_out_of_range"),
    ("turns_up", {"bars": 0}, "param_out_of_range"),
    ("turns_up", {"min_pct": -1}, "param_out_of_range"),
    ("rising", {"a": "@nope"}, "attr_missing"),
])
def test_bad_params_are_refused(cond, params, code):
    _program, diags = compile_with_diagnostics(_slope_graph(cond, params, False))
    assert code in {d.code for d in diags}, diags

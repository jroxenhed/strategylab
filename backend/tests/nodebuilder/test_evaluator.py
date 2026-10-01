"""Running a compiled graph: cook_program, cook_signals, the evaluate_graph
adapter, and the legacy compute_indicators_from_specs (F435 W2, plan D5)."""
from __future__ import annotations

import os
import sys

# Ensure backend/ is on sys.path
_BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import numpy as np
import pandas as pd
import pytest

from indicators import OHLCVSeries, compute_instance
from nodebuilder.compile import compile as nb_compile
from nodebuilder.evaluator import (
    NO_EXIT_ATTR,
    IndicatorSpec,
    compute_indicators_from_specs,
    cook_program,
    cook_signals,
    evaluate_graph,
)
from nodebuilder.models import Graph, Node, Wire


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def ohlcv_100() -> OHLCVSeries:
    """100-bar daily OHLCV fixture with deterministic values."""
    rng = np.random.default_rng(42)
    close = pd.Series(rng.standard_normal(100).cumsum() + 100, name="close")
    high = close + rng.uniform(0.5, 2.0, size=100)
    low = close - rng.uniform(0.5, 2.0, size=100)
    volume = pd.Series(np.full(100, 1_000_000.0))
    return OHLCVSeries(close=close, high=pd.Series(high), low=pd.Series(low), volume=volume)


def _frame(o: OHLCVSeries) -> pd.DataFrame:
    return pd.DataFrame({"Open": o.close, "High": o.high, "Low": o.low, "Close": o.close,
                         "Volume": o.volume})


def _attrs(o: OHLCVSeries) -> dict:
    return {"@open": o.close, "@high": o.high, "@low": o.low, "@close": o.close,
            "@volume": o.volume}


def _node(path: str, node_type: str, params: dict | None = None, bypass: bool = False) -> Node:
    return Node(id=path, type=node_type, params=params or {}, bypass=bypass)


def _wire(wire_id: str, from_path: str, to_path: str) -> Wire:
    return Wire(**{"id": wire_id, "from": from_path, "to": to_path})


def _rsi_below_graph(threshold: float = 30.0, period: int = 14) -> Graph:
    """Ticker → RSI(period) → Below(threshold) → Entry."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/rsi": _node("/rsi", "rsi", {"period": period, "type": "sma"}),
        "/below": _node("/below", "below", {"threshold": threshold}),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        _wire("w1", "/ticker", "/rsi"),
        _wire("w2", "/rsi", "/below"),
        _wire("w3", "/below", "/entry"),
    ]
    return Graph(nodes=nodes, wires=wires)


# ---------------------------------------------------------------------------
# Legacy dispatcher (kept for callers of the Wave 0 API)
# ---------------------------------------------------------------------------

def test_compute_indicators_from_specs_rsi(ohlcv_100):
    spec = IndicatorSpec(catalog_name="rsi", params={"period": 14, "type": "sma"},
                         write_attr="@rsi", node_path="/rsi")
    result = compute_indicators_from_specs([spec], ohlcv_100)
    expected = compute_instance("rsi", {"period": 14, "type": "sma"}, ohlcv_100)["rsi"]
    pd.testing.assert_series_equal(result["@rsi"], expected)


def test_compute_indicators_from_specs_macd_multi_output(ohlcv_100):
    spec = IndicatorSpec(catalog_name="macd", params={"fast": 12, "slow": 26, "signal": 9},
                         write_attr="@macd_line", node_path="/macd")
    result = compute_indicators_from_specs([spec], ohlcv_100)
    assert {"@macd_line", "@macd_signal", "@macd_histogram"} <= set(result)


def test_programs_carry_no_specs():
    """Indicators are nodes of the column program now."""
    prog = nb_compile(_rsi_below_graph())
    assert prog.indicator_specs == () and prog.per_bar_program == ()
    assert compute_indicators_from_specs(prog.indicator_specs, None) == {}


# ---------------------------------------------------------------------------
# Cooking
# ---------------------------------------------------------------------------

def test_cook_signals_match_eval_rules(ohlcv_100):
    """RSI<30 as a graph and as a rule give the same signal on every bar."""
    from signal_engine import Rule, compute_indicators, eval_rules

    prog = nb_compile(_rsi_below_graph(threshold=30.0))
    entry, exit_ = cook_signals(prog, _frame(ohlcv_100))

    rule = Rule(indicator="rsi", condition="below", value=30.0, params={"period": 14, "type": "sma"})
    ind = compute_indicators(close=ohlcv_100.close, high=ohlcv_100.high, low=ohlcv_100.low,
                             rules=[rule])
    rule_entry = [eval_rules([rule], "AND", ind, i) for i in range(len(entry))]
    assert entry.tolist() == rule_entry
    assert not exit_.any() and prog.exit_attr == NO_EXIT_ATTR


def test_comparison_on_known_values():
    """Close below 30 on bar 5 only: Entry fires on bar 5 only; bar 0 never fires."""
    n = 20
    close = np.full(n, 50.0)
    close[5] = 25.0
    close[0] = 10.0  # below 30, but bar 0 is always False
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1.0})
    graph = Graph(nodes={
        "/ticker": _node("/ticker", "ticker"),
        "/below": _node("/below", "below", {"threshold": 30.0}),
        "/entry": _node("/entry", "entry"),
    }, wires=[_wire("w1", "/ticker", "/below"), _wire("w2", "/below", "/entry")])
    entry, _exit = cook_signals(nb_compile(graph), df)
    assert np.flatnonzero(entry).tolist() == [5]


def test_cook_keeps_only_what_is_asked(ohlcv_100):
    prog = nb_compile(_rsi_below_graph())
    df = _frame(ohlcv_100)
    lean = cook_program(prog, df)
    assert set(lean.streams) == {"/entry"}
    full = cook_program(prog, df, keep_all=True)
    assert set(full.streams) == {"/ticker", "/rsi", "/below", "/entry"}
    expected = compute_instance("rsi", {"period": 14, "type": "sma"}, ohlcv_100)["rsi"].to_numpy()
    np.testing.assert_array_equal(full.column("/rsi", "@rsi"), expected)
    # The RSI column was freed in the lean cook once the comparison had read it.
    assert len(lean.store) == 1


# ---------------------------------------------------------------------------
# evaluate_graph: the per-bar adapter
# ---------------------------------------------------------------------------

def test_evaluate_graph_returns_entry_exit_at_every_bar(ohlcv_100):
    prog = nb_compile(_rsi_below_graph())
    attrs = _attrs(ohlcv_100)
    entry, _ = cook_signals(prog, _frame(ohlcv_100))
    for i in range(len(entry)):
        out = evaluate_graph(prog, attrs, i)
        assert set(out) == {"entry", "exit"}
        assert isinstance(out["entry"], bool) and isinstance(out["exit"], bool)
        assert out["entry"] == entry[i]


def test_evaluate_graph_cooks_once_per_attrs(ohlcv_100, monkeypatch):
    import nodebuilder.evaluator as evaluator

    prog = nb_compile(_rsi_below_graph())
    calls = []
    real = evaluator.cook_signals

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(evaluator, "cook_signals", spy)
    attrs = _attrs(ohlcv_100)
    for i in range(10):
        evaluate_graph(prog, attrs, i)
    assert len(calls) == 1
    # A new program (a recompile) cooks again.
    evaluate_graph(nb_compile(_rsi_below_graph(threshold=40.0)), attrs, 3)
    assert len(calls) == 2


def test_evaluate_graph_needs_only_close():
    """Wave 0 callers sometimes seeded only @close; the rest is NaN."""
    close = pd.Series(np.linspace(10, 50, 30))
    graph = Graph(nodes={
        "/ticker": _node("/ticker", "ticker"),
        "/above": _node("/above", "above", {"threshold": 30.0}),
        "/entry": _node("/entry", "entry"),
    }, wires=[_wire("w1", "/ticker", "/above"), _wire("w2", "/above", "/entry")])
    prog = nb_compile(graph)
    attrs = {"@close": close}
    assert evaluate_graph(prog, attrs, 29)["entry"] is True
    assert evaluate_graph(prog, attrs, 1)["entry"] is False


def test_live_last_bar_reads_the_last_value(ohlcv_100):
    """The bot reads bar -1 (len - 1); a negative index works too."""
    prog = nb_compile(_rsi_below_graph(threshold=60.0))
    attrs = _attrs(ohlcv_100)
    n = len(ohlcv_100.close)
    assert evaluate_graph(prog, attrs, n - 1) == evaluate_graph(prog, attrs, -1)

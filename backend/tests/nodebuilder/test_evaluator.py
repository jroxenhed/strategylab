"""Unit 7a tests — evaluate_graph, compute_indicators_from_specs."""
from __future__ import annotations

import sys
import os

# Ensure backend/ is on sys.path
_BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch

from indicators import OHLCVSeries, compute_instance
from nodebuilder.evaluator import (
    CompiledProgram,
    IndicatorSpec,
    PerBarOp,
    SimulatorSetting,
    compute_indicators_from_specs,
    evaluate_graph,
)
from nodebuilder.compile import compile as nb_compile
from nodebuilder.models import Graph, Node, Wire


# ---------------------------------------------------------------------------
# Fixture: 100-bar OHLCV data
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node(path: str, node_type: str, params: dict | None = None, bypass: bool = False) -> Node:
    return Node(id=path, type=node_type, params=params or {}, bypass=bypass)


def _wire(wire_id: str, from_path: str, to_path: str) -> Wire:
    return Wire(**{"id": wire_id, "from": from_path, "to": to_path})


def _make_graph(nodes: dict[str, Node], wires: list[Wire]) -> Graph:
    return Graph(nodes=nodes, wires=wires)


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
    return _make_graph(nodes, wires)


def _init_attrs_for_program(program: CompiledProgram, num_bars: int, indicator_attrs: dict) -> dict:
    """Pre-allocate NaN/False Series for each PerBarOp write target."""
    attrs = dict(indicator_attrs)  # copy in indicator Series
    seen_writes = set()
    for op in program.per_bar_program:
        if op.writes not in seen_writes:
            attrs[op.writes] = pd.Series(np.zeros(num_bars, dtype=float))
            seen_writes.add(op.writes)
    return attrs


# ---------------------------------------------------------------------------
# test_compute_indicators_from_specs_rsi
# ---------------------------------------------------------------------------

def test_compute_indicators_from_specs_rsi(ohlcv_100):
    """IndicatorSpec(rsi, period=14, type=sma) → @rsi matches compute_instance directly."""
    spec = IndicatorSpec(
        catalog_name="rsi",
        params={"period": 14, "type": "sma"},
        write_attr="@rsi",
        node_path="/rsi",
    )
    result = compute_indicators_from_specs([spec], ohlcv_100)
    assert "@rsi" in result

    # Must match direct compute_instance call
    expected = compute_instance("rsi", {"period": 14, "type": "sma"}, ohlcv_100)["rsi"]
    pd.testing.assert_series_equal(result["@rsi"], expected, check_names=False)


# ---------------------------------------------------------------------------
# test_compute_indicators_from_specs_macd_multi_output
# ---------------------------------------------------------------------------

def test_compute_indicators_from_specs_macd_multi_output(ohlcv_100):
    """MACD spec → @macd_line, @macd_signal, @macd_histogram matching compute_instance."""
    spec = IndicatorSpec(
        catalog_name="macd",
        params={"fast": 12, "slow": 26, "signal": 9},
        write_attr="@macd_line",   # primary
        node_path="/macd",
    )
    result = compute_indicators_from_specs([spec], ohlcv_100)

    assert "@macd_line" in result
    assert "@macd_signal" in result
    assert "@macd_histogram" in result

    ref = compute_instance("macd", {"fast": 12, "slow": 26, "signal": 9}, ohlcv_100)
    pd.testing.assert_series_equal(result["@macd_line"], ref["macd"], check_names=False)
    pd.testing.assert_series_equal(result["@macd_signal"], ref["signal"], check_names=False)
    pd.testing.assert_series_equal(result["@macd_histogram"], ref["histogram"], check_names=False)


# ---------------------------------------------------------------------------
# test_compute_indicators_from_specs_dedup_cache
# ---------------------------------------------------------------------------

def test_compute_indicators_from_specs_dedup_cache(ohlcv_100):
    """Two specs with identical params → compute_instance called once (cache hit)."""
    spec_a = IndicatorSpec(
        catalog_name="rsi",
        params={"period": 14, "type": "sma"},
        write_attr="@rsi",
        node_path="/rsi_a",
    )
    spec_b = IndicatorSpec(
        catalog_name="rsi",
        params={"period": 14, "type": "sma"},
        write_attr="@rsi",
        node_path="/rsi_b",
    )
    cache: dict = {}
    # Call once — populates cache
    compute_indicators_from_specs([spec_a], ohlcv_100, cache=cache)
    assert len(cache) == 1

    # Call again with same spec — cache hit, result unchanged
    compute_indicators_from_specs([spec_b], ohlcv_100, cache=cache)
    # Cache should still have exactly 1 entry (no duplicate)
    assert len(cache) == 1


# ---------------------------------------------------------------------------
# test_evaluate_graph_per_bar_returns_entry_exit
# ---------------------------------------------------------------------------

def test_evaluate_graph_per_bar_returns_entry_exit(ohlcv_100):
    """evaluate_graph returns {"entry": bool, "exit": bool} at every bar."""
    g = _rsi_below_graph()
    prog = nb_compile(g)
    n = len(ohlcv_100.close)

    indicator_attrs = compute_indicators_from_specs(prog.indicator_specs, ohlcv_100)
    # The indicator spec write_attr is @rsi; PerBarOp reads it.
    attrs = _init_attrs_for_program(prog, n, indicator_attrs)
    # Also seed raw OHLCV attrs (consumed by ticker node downstream)
    attrs["@close"] = ohlcv_100.close
    attrs["@high"] = ohlcv_100.high
    attrs["@low"] = ohlcv_100.low
    attrs["@volume"] = ohlcv_100.volume
    attrs.setdefault("@always_false", pd.Series(np.zeros(n, dtype=float)))

    for i in range(n):
        out = evaluate_graph(prog, attrs, i)
        assert "entry" in out
        assert "exit" in out
        assert isinstance(out["entry"], bool)
        assert isinstance(out["exit"], bool)


# ---------------------------------------------------------------------------
# test_evaluate_graph_matches_eval_rules
# ---------------------------------------------------------------------------

def test_evaluate_graph_matches_eval_rules(ohlcv_100):
    """RSI<30 graph mode vs RSI<30 rule mode produce identical boolean signals bar-by-bar."""
    from signal_engine import Rule, eval_rules, compute_indicators

    # --- Graph path ---
    g = _rsi_below_graph(threshold=30.0)
    prog = nb_compile(g)
    n = len(ohlcv_100.close)

    indicator_attrs = compute_indicators_from_specs(prog.indicator_specs, ohlcv_100)
    attrs = _init_attrs_for_program(prog, n, indicator_attrs)
    attrs["@close"] = ohlcv_100.close
    attrs.setdefault("@always_false", pd.Series(np.zeros(n, dtype=float)))

    graph_entry = []
    for i in range(n):
        out = evaluate_graph(prog, attrs, i)
        graph_entry.append(out["entry"])

    # --- Rule path ---
    rule = Rule(indicator="rsi", condition="below", value=30.0,
                params={"period": 14, "type": "sma"})
    rule_indicators = compute_indicators(
        close=ohlcv_100.close,
        high=ohlcv_100.high,
        low=ohlcv_100.low,
        rules=[rule],
    )
    rule_entry = [eval_rules([rule], "AND", rule_indicators, i) for i in range(n)]

    # Must match exactly
    assert graph_entry == rule_entry, (
        f"Graph and rule entry signals disagree: "
        f"graph={sum(graph_entry)} trues, rule={sum(rule_entry)} trues"
    )


# ---------------------------------------------------------------------------
# test_per_bar_program_uses_correct_attrs
# ---------------------------------------------------------------------------

def test_per_bar_program_uses_correct_attrs():
    """RSI series with known values around 30 → below comparison fires True on expected bar."""
    # Construct a synthetic RSI-like series where bar 5 dips below 30
    n = 20
    rsi_values = [50.0] * n
    rsi_values[5] = 25.0   # bar 5 is below 30

    g = _rsi_below_graph(threshold=30.0)
    prog = nb_compile(g)

    # Override indicator attrs with our synthetic RSI series
    rsi_series = pd.Series(rsi_values)
    # The spec write_attr is "@rsi"
    indicator_attrs = {"@rsi": rsi_series}
    attrs = _init_attrs_for_program(prog, n, indicator_attrs)
    attrs["@close"] = pd.Series(rsi_values)   # not used by the below comparison
    attrs.setdefault("@always_false", pd.Series(np.zeros(n, dtype=float)))

    results = []
    for i in range(n):
        out = evaluate_graph(prog, attrs, i)
        results.append(out["entry"])

    # Bar 0 → False (i < 1 guard in comparison fn)
    assert results[0] is False

    # Bar 5 → True (25.0 < 30)
    assert results[5] is True, f"Expected True at bar 5, got {results[5]}"

    # Other bars (except bar 5) → False since rsi_values = 50.0 > 30
    for i in range(1, n):
        if i == 5:
            continue
        assert results[i] is False, f"Expected False at bar {i}, got {results[i]}"

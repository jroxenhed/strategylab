"""Unit 7a tests — compile() and related errors."""
from __future__ import annotations

import sys
import os

# Ensure backend/ is on sys.path
_BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import pytest

from nodebuilder.models import CyclicGraphError, Graph, Node, Wire
from nodebuilder.compile import compile as nb_compile
from nodebuilder.evaluator import (
    CompiledProgram,
    FamilyCapExceededError,
    IndicatorSpec,
    MissingTerminalError,
    PerBarOp,
    RegimeUnsupportedError,
    SimulatorSetting,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node(path: str, node_type: str, params: dict | None = None, bypass: bool = False) -> Node:
    return Node(id=path, type=node_type, params=params or {}, bypass=bypass)


def _wire(wire_id: str, from_path: str, to_path: str) -> Wire:
    return Wire(**{"id": wire_id, "from": from_path, "to": to_path})


def _make_graph(nodes: dict[str, Node], wires: list[Wire]) -> Graph:
    return Graph(nodes=nodes, wires=wires)


def _rsi_entry_graph(rsi_params: dict | None = None) -> Graph:
    """Ticker → RSI → Below(threshold=30) → Entry  (minimal 1-rule graph)."""
    params = rsi_params or {"period": 14, "type": "sma"}
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/rsi": _node("/rsi", "rsi", params),
        "/below": _node("/below", "below", {"threshold": 30.0}),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        _wire("w1", "/ticker", "/rsi"),
        _wire("w2", "/rsi", "/below"),
        _wire("w3", "/below", "/entry"),
    ]
    return _make_graph(nodes, wires)


# ---------------------------------------------------------------------------
# test_compile_simple_rsi_long
# ---------------------------------------------------------------------------

def test_compile_simple_rsi_long():
    """Ticker → RSI → Below → Entry produces 1 IndicatorSpec, 1 PerBarOp (below), entry_attr set."""
    g = _rsi_entry_graph()
    prog = nb_compile(g)

    assert isinstance(prog, CompiledProgram)
    assert len(prog.indicator_specs) == 1
    spec = prog.indicator_specs[0]
    assert spec.catalog_name == "rsi"
    assert spec.params.get("period") == 14

    # The below comparison becomes 1 PerBarOp
    assert len(prog.per_bar_program) == 1
    op = prog.per_bar_program[0]
    assert op.node_path == "/below"

    assert prog.entry_attr.startswith("@")
    assert prog.exit_attr == "@always_false"


# ---------------------------------------------------------------------------
# test_indicator_dedup_across_compares
# ---------------------------------------------------------------------------

def test_indicator_dedup_across_compares():
    """RSI<30 buy + RSI>70 sell → exactly 1 IndicatorSpec, 2 comparison PerBarOps."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/rsi": _node("/rsi", "rsi", {"period": 14, "type": "sma"}),
        "/below": _node("/below", "below", {"threshold": 30.0}),
        "/above": _node("/above", "above", {"threshold": 70.0}),
        "/entry": _node("/entry", "entry"),
        "/exit": _node("/exit", "exit"),
    }
    wires = [
        _wire("w1", "/ticker", "/rsi"),
        _wire("w2", "/rsi", "/below"),
        _wire("w3", "/rsi", "/above"),
        _wire("w4", "/below", "/entry"),
        _wire("w5", "/above", "/exit"),
    ]
    g = _make_graph(nodes, wires)
    prog = nb_compile(g)

    assert len(prog.indicator_specs) == 1, "Only one RSI spec expected (dedup)"
    assert len(prog.per_bar_program) == 2, "below + above comparison ops"
    assert prog.entry_attr.startswith("@")
    assert prog.exit_attr.startswith("@")
    assert prog.exit_attr != "@always_false"


# ---------------------------------------------------------------------------
# test_cycle_via_validation
# ---------------------------------------------------------------------------

def test_cycle_via_validation():
    """Graph with a→b + b→a wires raises CyclicGraphError at Graph construction."""
    nodes = {
        "/a": _node("/a", "rsi"),
        "/b": _node("/b", "above"),
    }
    wires = [
        _wire("w1", "/a", "/b"),
        _wire("w2", "/b", "/a"),
    ]
    with pytest.raises(CyclicGraphError):
        Graph(nodes=nodes, wires=wires)


# ---------------------------------------------------------------------------
# test_regime_path_raises
# ---------------------------------------------------------------------------

def test_regime_path_raises():
    """Graph with a /regime/ node raises RegimeUnsupportedError at compile."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/regime/trend": _node("/regime/trend", "rsi"),
        "/rsi": _node("/rsi", "rsi", {"period": 14, "type": "sma"}),
        "/below": _node("/below", "below", {"threshold": 30.0}),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        _wire("w1", "/ticker", "/rsi"),
        _wire("w2", "/rsi", "/below"),
        _wire("w3", "/below", "/entry"),
    ]
    g = _make_graph(nodes, wires)
    with pytest.raises(RegimeUnsupportedError):
        nb_compile(g)


# ---------------------------------------------------------------------------
# test_missing_entry_raises
# ---------------------------------------------------------------------------

def test_missing_entry_raises():
    """Graph without an Entry terminal raises MissingTerminalError at compile."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/rsi": _node("/rsi", "rsi", {"period": 14, "type": "sma"}),
        "/below": _node("/below", "below", {"threshold": 30.0}),
        # No /entry node
    }
    wires = [
        _wire("w1", "/ticker", "/rsi"),
        _wire("w2", "/rsi", "/below"),
    ]
    g = _make_graph(nodes, wires)
    with pytest.raises(MissingTerminalError):
        nb_compile(g)


# ---------------------------------------------------------------------------
# test_entry_non_bool_input_raises
# ---------------------------------------------------------------------------

def test_entry_non_bool_input_raises():
    """Wiring @close (from ticker) directly into Entry raises TypeError."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        _wire("w1", "/ticker", "/entry"),
    ]
    g = _make_graph(nodes, wires)
    with pytest.raises(TypeError):
        nb_compile(g)


# ---------------------------------------------------------------------------
# test_bypassed_node_skipped
# ---------------------------------------------------------------------------

def test_bypassed_node_skipped():
    """Node with bypass=True has NO PerBarOp in the compiled program."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/rsi": _node("/rsi", "rsi", {"period": 14, "type": "sma"}),
        "/below": _node("/below", "below", {"threshold": 30.0}, bypass=True),  # bypassed
        # We still need an entry — wire something from a non-bypassed node
        # For simplicity, add a non-bypassed below2 that is the entry source
        "/below2": _node("/below2", "below", {"threshold": 40.0}),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        _wire("w1", "/ticker", "/rsi"),
        _wire("w2", "/rsi", "/below"),   # bypassed node
        _wire("w3", "/rsi", "/below2"),
        _wire("w4", "/below2", "/entry"),
    ]
    g = _make_graph(nodes, wires)
    prog = nb_compile(g)

    # /below is bypassed — only /below2's op should appear
    op_paths = [op.node_path for op in prog.per_bar_program]
    assert "/below" not in op_paths, "Bypassed node must not appear in per_bar_program"
    assert "/below2" in op_paths


# ---------------------------------------------------------------------------
# test_size_stop_terminals_no_op
# ---------------------------------------------------------------------------

def test_size_stop_terminals_no_op():
    """Graph with unwired Size and Stop output terminals compiles without error."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/rsi": _node("/rsi", "rsi", {"period": 14, "type": "sma"}),
        "/below": _node("/below", "below", {"threshold": 30.0}),
        "/entry": _node("/entry", "entry"),
        "/size": _node("/size", "size"),   # compile_active=False; unwired
        "/stop": _node("/stop", "stop"),   # compile_active=False; unwired
    }
    wires = [
        _wire("w1", "/ticker", "/rsi"),
        _wire("w2", "/rsi", "/below"),
        _wire("w3", "/below", "/entry"),
    ]
    g = _make_graph(nodes, wires)
    prog = nb_compile(g)

    # Should compile cleanly
    assert prog.entry_attr.startswith("@")

    # No SimulatorSettings from size/stop (they're catalog-only at T2)
    setting_keys = {s.key for s in prog.simulator_settings}
    assert "size" not in setting_keys
    assert "stop" not in setting_keys


# ---------------------------------------------------------------------------
# test_family_cap_at_compile_or_dispatch
# ---------------------------------------------------------------------------

def test_family_cap_at_compile_or_dispatch():
    """21 distinct RSI specs → FamilyCapExceededError at compute_indicators_from_specs."""
    from nodebuilder.evaluator import compute_indicators_from_specs, IndicatorSpec
    from indicators import OHLCVSeries
    import pandas as pd
    import numpy as np

    # Build 21 distinct RSI IndicatorSpecs
    specs = [
        IndicatorSpec(
            catalog_name="rsi",
            params={"period": 2 + i, "type": "sma"},
            write_attr=f"@rsi_{2 + i}",
            node_path=f"/rsi_{2 + i}",
        )
        for i in range(21)
    ]

    close = pd.Series(np.random.randn(100).cumsum() + 100)
    ohlcv = OHLCVSeries(close=close, high=close + 1, low=close - 1,
                        volume=pd.Series(1_000_000, index=close.index, dtype=float))

    with pytest.raises(FamilyCapExceededError):
        compute_indicators_from_specs(specs, ohlcv)


# ---------------------------------------------------------------------------
# test_settings_extracted
# ---------------------------------------------------------------------------

def test_settings_extracted():
    """Graph with position_size, stop_loss, and slippage nodes → 3 SimulatorSettings."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/rsi": _node("/rsi", "rsi", {"period": 14, "type": "sma"}),
        "/below": _node("/below", "below", {"threshold": 30.0}),
        "/entry": _node("/entry", "entry"),
        "/pos_size": _node("/pos_size", "position_size", {"size": 0.5}),
        "/stop_loss": _node("/stop_loss", "stop_loss", {"pct": 5.0}),
        "/slippage": _node("/slippage", "slippage", {"bps": 3.0}),
    }
    wires = [
        _wire("w1", "/ticker", "/rsi"),
        _wire("w2", "/rsi", "/below"),
        _wire("w3", "/below", "/entry"),
    ]
    g = _make_graph(nodes, wires)
    prog = nb_compile(g)

    setting_map = {s.key: s.value for s in prog.simulator_settings}
    assert setting_map.get("position_size") == pytest.approx(0.5)
    assert setting_map.get("stop_loss") == pytest.approx(5.0)
    assert setting_map.get("slippage_bps") == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# F1 / F2 review findings — wire.attr resolution + crossover guard
# ---------------------------------------------------------------------------

def test_logic_node_honors_multi_output_wire_attr():
    """A NOT/AND wired from MACD.@macd_signal must read @macd_signal, not @macd_line.

    (Review finding F1: prior to fix, _inbound_attrs always resolved to the
    upstream node's primary write attribute, silently swallowing port-level
    selection on multi-output indicators.)
    """
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/macd": _node("/macd", "macd", {"fast": 12, "slow": 26, "signal": 9}),
        "/not_macd_signal": _node("/not_macd_signal", "not"),
        "/cmp": _node("/cmp", "below", {"threshold": 0.0}),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        _wire("w1", "/ticker", "/macd"),
        # Critical: this wire carries @macd_signal, not the primary @macd_line.
        Wire(**{"id": "w2", "from": "/macd", "to": "/not_macd_signal",
                "attr": "@macd_signal"}),
        # The NOT op writes a derived @bool_N — feed it through a comparison
        # before Entry so the boolean-input invariant is satisfied.
        _wire("w3", "/not_macd_signal", "/cmp"),
        _wire("w4", "/cmp", "/entry"),
    ]
    g = _make_graph(nodes, wires)
    prog = nb_compile(g)

    # Find the NOT op in the program; its reads should include @macd_signal.
    not_op = next(op for op in prog.per_bar_program if op.node_path == "/not_macd_signal")
    assert "@macd_signal" in not_op.reads, (
        f"Expected NOT to read @macd_signal (the named wire.attr), got {not_op.reads}"
    )


def test_crossover_on_derived_signal_rejected():
    """Crossovers need iloc[i-1]; per-bar derived attrs only get iloc[i] populated.

    (Review finding F2: silently never-firing crossovers on op outputs.)
    """
    # Build Ticker → RSI → Below(30) → [crosses_above on the resulting bool] → Entry
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/rsi": _node("/rsi", "rsi", {"period": 14, "type": "sma"}),
        "/below": _node("/below", "below", {"threshold": 30.0}),
        "/cross": _node("/cross", "crosses_above", {"threshold": 0.5}),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        _wire("w1", "/ticker", "/rsi"),
        _wire("w2", "/rsi", "/below"),
        _wire("w3", "/below", "/cross"),  # @bool_N → crosses_above (FORBIDDEN)
        _wire("w4", "/cross", "/entry"),
    ]
    g = _make_graph(nodes, wires)
    with pytest.raises(TypeError, match="derived signal"):
        nb_compile(g)

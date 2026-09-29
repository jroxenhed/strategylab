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

def test_comparison_honors_multi_output_wire_attr():
    """A comparison wired from MACD.@macd_signal must read @macd_signal, not @macd_line.

    (Review finding F1: prior to fix, _inbound_attrs always resolved to the
    upstream node's primary write attribute, silently swallowing port-level
    selection on multi-output indicators.  A NOT fed by MACD is now refused,
    see test_logic_node_refuses_non_boolean_input.)
    """
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/macd": _node("/macd", "macd", {"fast": 12, "slow": 26, "signal": 9}),
        "/cmp": _node("/cmp", "below", {"threshold": 0.0}),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        _wire("w1", "/ticker", "/macd"),
        # Critical: this wire carries @macd_signal, not the primary @macd_line.
        Wire(**{"id": "w2", "from": "/macd", "to": "/cmp", "attr": "@macd_signal"}),
        _wire("w4", "/cmp", "/entry"),
    ]
    prog = nb_compile(_make_graph(nodes, wires))
    cmp_op = next(op for op in prog.per_bar_program if op.node_path == "/cmp")
    assert cmp_op.reads == ("@macd_signal",)


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


# ---------------------------------------------------------------------------
# F435 W0: distinct specs get distinct attrs; Ticker wires honour their field
# ---------------------------------------------------------------------------

def _two_indicator_graph(node_type: str, params_a: dict, params_b: dict,
                         attr_a: str | None = None, attr_b: str | None = None) -> Graph:
    """Ticker -> A, Ticker -> B, (A vs B) -> above -> Entry."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/a": _node("/a", node_type, params_a),
        "/b": _node("/b", node_type, params_b),
        "/above": _node("/above", "above"),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        _wire("w1", "/ticker", "/a"),
        _wire("w2", "/ticker", "/b"),
        Wire(**{"id": "w3", "from": "/a", "to": "/above", "attr": attr_a}),
        Wire(**{"id": "w4", "from": "/b", "to": "/above", "attr": attr_b}),
        _wire("w5", "/above", "/entry"),
    ]
    return _make_graph(nodes, wires)


def test_two_rsis_with_different_params_do_not_share_an_attr():
    """Before the fix both wrote @rsi and the second overwrote the first."""
    prog = nb_compile(_two_indicator_graph(
        "rsi", {"period": 14, "type": "sma"}, {"period": 14, "type": "wilder"}))
    attrs = [s.write_attr for s in prog.indicator_specs]
    assert attrs == ["@rsi", "@rsi_2"]
    assert prog.per_bar_program[0].reads == ("@rsi", "@rsi_2")


def test_same_params_still_dedup_to_one_spec():
    prog = nb_compile(_two_indicator_graph(
        "ema", {"period": 20}, {"period": 20}))
    assert [s.write_attr for s in prog.indicator_specs] == ["@ema"]


def test_two_macds_keep_their_own_sub_outputs():
    prog = nb_compile(_two_indicator_graph(
        "macd", {"fast": 12, "slow": 26, "signal": 9}, {"fast": 5, "slow": 35, "signal": 5},
        attr_a="@macd_line", attr_b="@macd_signal"))
    assert [s.attr_suffix for s in prog.indicator_specs] == ["", "_2"]
    assert prog.per_bar_program[0].reads == ("@macd_line", "@macd_signal_2")

    import numpy as np
    import pandas as pd
    from indicators import OHLCVSeries
    from nodebuilder.evaluator import compute_indicators_from_specs
    close = pd.Series(100 + np.cumsum(np.random.default_rng(3).normal(0, 1, 120)))
    ohlcv = OHLCVSeries(close=close, high=close, low=close, volume=close * 0)
    out = compute_indicators_from_specs(prog.indicator_specs, ohlcv)
    assert {"@macd_line", "@macd_signal_2"} <= set(out)
    assert not out["@macd_signal"].equals(out["@macd_signal_2"])


def test_ticker_wire_reads_the_named_field():
    """A Ticker @volume wire used to be read as @close."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/above": _node("/above", "above", {"threshold": 1_000_000.0}),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        Wire(**{"id": "w1", "from": "/ticker", "to": "/above", "attr": "@volume"}),
        _wire("w2", "/above", "/entry"),
    ]
    prog = nb_compile(_make_graph(nodes, wires))
    assert prog.per_bar_program[0].reads == ("@volume",)


def test_ticker_wire_without_field_label_reads_close():
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/above": _node("/above", "above", {"threshold": 100.0}),
        "/entry": _node("/entry", "entry"),
    }
    wires = [_wire("w1", "/ticker", "/above"), _wire("w2", "/above", "/entry")]
    prog = nb_compile(_make_graph(nodes, wires))
    assert prog.per_bar_program[0].reads == ("@close",)


def test_ticker_volume_into_entry_raises():
    nodes = {"/ticker": _node("/ticker", "ticker"), "/entry": _node("/entry", "entry")}
    wires = [Wire(**{"id": "w1", "from": "/ticker", "to": "/entry", "attr": "@volume"})]
    with pytest.raises(TypeError) as exc_info:
        nb_compile(_make_graph(nodes, wires))
    assert exc_info.value.node_id == "/ticker"


def test_indicator_into_exit_raises():
    """RSI wired straight into Exit used to read as 'always exit'."""
    g = _rsi_entry_graph()
    nodes = dict(g.nodes)
    nodes["/exit"] = _node("/exit", "exit")
    wires = list(g.wires) + [_wire("wx", "/rsi", "/exit")]
    with pytest.raises(TypeError) as exc_info:
        nb_compile(_make_graph(nodes, wires))
    assert exc_info.value.node_id == "/rsi"


# ---------------------------------------------------------------------------
# F435 0.H: compile refuses what it cannot run; trailing_stop is a setting
# ---------------------------------------------------------------------------

from models import TrailingStopConfig  # noqa: E402
from nodebuilder.evaluator import UnsupportedNodeError  # noqa: E402
from nodebuilder.models import GraphValidationError  # noqa: E402


def _with_extra(extra_nodes: dict, extra_wires: list | None = None) -> Graph:
    """The minimal RSI entry graph plus some extra nodes and wires."""
    g = _rsi_entry_graph()
    nodes = dict(g.nodes)
    nodes.update(extra_nodes)
    return _make_graph(nodes, list(g.wires) + list(extra_wires or []))


def test_trailing_stop_node_emits_config_setting():
    params = {"type": "atr", "value": 2.5, "source": "close",
              "activate_on_profit": True, "activate_pct": 1.5}
    g = _with_extra({"/trail": _node("/trail", "trailing_stop", params)})
    prog = nb_compile(g)
    [setting] = [s for s in prog.simulator_settings if s.key == "trailing_stop"]
    assert setting.value == TrailingStopConfig(**params)


def test_trailing_stop_missing_params_take_config_defaults():
    g = _with_extra({"/trail": _node("/trail", "trailing_stop", {"value": 3})})
    prog = nb_compile(g)
    [setting] = [s for s in prog.simulator_settings if s.key == "trailing_stop"]
    assert setting.value == TrailingStopConfig(value=3.0)


def test_trailing_stop_text_booleans_from_a_select_are_read():
    """The canvas select stores activate_on_profit as the text 'true'/'false'."""
    g = _with_extra({"/trail": _node("/trail", "trailing_stop", {"activate_on_profit": "true"})})
    [setting] = [s for s in nb_compile(g).simulator_settings if s.key == "trailing_stop"]
    assert setting.value.activate_on_profit is True


@pytest.mark.parametrize("params", [
    {"type": "chandelier"},
    {"source": "low"},
    {"value": "five"},
])
def test_trailing_stop_bad_params_raise_with_node_id(params):
    g = _with_extra({"/trail": _node("/trail", "trailing_stop", params)})
    with pytest.raises(GraphValidationError) as info:
        nb_compile(g)
    assert info.value.node_id == "/trail"


@pytest.mark.parametrize("node_type,params", [
    ("stop_loss", {"pct": 3.0, "direction": "long"}),
    ("trailing_stop", {"type": "pct", "value": 5.0, "direction": "short"}),
])
def test_per_direction_settings_are_refused(node_type, params):
    g = _with_extra({"/set": _node("/set", node_type, params)})
    with pytest.raises(UnsupportedNodeError) as info:
        nb_compile(g)
    assert info.value.node_id == "/set"


@pytest.mark.parametrize("terminal", ["size", "stop"])
def test_wired_size_stop_terminal_is_refused(terminal):
    """An unwired one is ignored (test_size_stop_terminals_no_op); a wired one
    would look like it sizes or stops trades while doing nothing."""
    g = _with_extra(
        {f"/{terminal}": _node(f"/{terminal}", terminal)},
        [_wire("wt", "/below", f"/{terminal}")],
    )
    with pytest.raises(UnsupportedNodeError) as info:
        nb_compile(g)
    assert info.value.node_id == f"/{terminal}"


@pytest.mark.parametrize("node_type", ["rising", "turns_up", "stochastic", "adx", "no_such_node"])
@pytest.mark.parametrize("bypass", [False, True])
def test_non_compile_active_types_are_refused(node_type, bypass):
    """Unknown types used to be skipped, so an unsupported Exit never fired."""
    g = _with_extra({"/odd": _node("/odd", node_type, bypass=bypass)})
    with pytest.raises(UnsupportedNodeError) as info:
        nb_compile(g)
    assert info.value.node_id == "/odd"


def test_unsupported_node_feeding_exit_is_refused_not_never_exit():
    g = _with_extra(
        {"/rising": _node("/rising", "rising"), "/exit": _node("/exit", "exit")},
        [_wire("wr", "/rsi", "/rising"), _wire("wx", "/rising", "/exit")],
    )
    with pytest.raises(UnsupportedNodeError) as info:
        nb_compile(g)
    assert info.value.node_id == "/rising"


# ---------------------------------------------------------------------------
# F435 wave 0 review fixes: compile refuses graphs it would run wrongly
# ---------------------------------------------------------------------------

from nodebuilder.evaluator import GraphTypeError  # noqa: E402


def _rsi_logic_graph(logic: str, *, extra_nodes=None, extra_wires=None) -> Graph:
    """Ticker -> RSI -> <logic> -> Entry, with RSI wired straight into the logic node."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/rsi": _node("/rsi", "rsi", {"period": 14, "type": "sma"}),
        "/logic": _node("/logic", logic),
        "/entry": _node("/entry", "entry"),
    }
    nodes.update(extra_nodes or {})
    wires = [
        _wire("w1", "/ticker", "/rsi"),
        _wire("w2", "/rsi", "/logic"),
        _wire("w3", "/logic", "/entry"),
    ] + list(extra_wires or [])
    return _make_graph(nodes, wires)


@pytest.mark.parametrize("logic", ["and", "or", "not"])
def test_logic_node_refuses_non_boolean_input(logic):
    """BC-1: RSI into AND used to fire whenever RSI was non-zero (NaN too)."""
    with pytest.raises(GraphTypeError) as info:
        nb_compile(_rsi_logic_graph(logic))
    assert info.value.node_id == "/rsi"


def test_not_of_ticker_into_exit_is_refused():
    """BC-1: Ticker -> NOT -> Exit read bool(@close)."""
    g = _with_extra(
        {"/not": _node("/not", "not"), "/exit": _node("/exit", "exit")},
        [_wire("wn", "/ticker", "/not"), _wire("wx", "/not", "/exit")],
    )
    with pytest.raises(GraphTypeError) as info:
        nb_compile(g)
    assert info.value.node_id == "/ticker"


def test_logic_node_refuses_a_macd_sub_output():
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/macd": _node("/macd", "macd"),
        "/not": _node("/not", "not"),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        _wire("w1", "/ticker", "/macd"),
        Wire(**{"id": "w2", "from": "/macd", "to": "/not", "attr": "@macd_signal"}),
        _wire("w3", "/not", "/entry"),
    ]
    with pytest.raises(GraphTypeError) as info:
        nb_compile(_make_graph(nodes, wires))
    assert info.value.node_id == "/macd"


def test_comparison_without_threshold_or_second_input_is_refused():
    """BC-2: an RSI Above with no threshold was dropped and AND ignored it."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/rsi": _node("/rsi", "rsi", {"period": 14, "type": "sma"}),
        "/above": _node("/above", "above", {"threshold": None}),
        "/below": _node("/below", "below", {"threshold": 30.0}),
        "/and": _node("/and", "and"),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        _wire("w1", "/ticker", "/rsi"),
        _wire("w2", "/rsi", "/above"),
        _wire("w3", "/rsi", "/below"),
        _wire("w4", "/above", "/and"),
        _wire("w5", "/below", "/and"),
        _wire("w6", "/and", "/entry"),
    ]
    with pytest.raises(GraphValidationError) as info:
        nb_compile(_make_graph(nodes, wires))
    assert info.value.node_id == "/above"


@pytest.mark.parametrize("threshold", [None, ""])
def test_comparison_with_no_inputs_is_refused(threshold):
    g = _with_extra({"/lonely": _node("/lonely", "above", {"threshold": threshold})})
    with pytest.raises(GraphValidationError) as info:
        nb_compile(g)
    assert info.value.node_id == "/lonely"


def test_comparison_with_bad_threshold_is_refused():
    g = _rsi_entry_graph()
    g.nodes["/below"].params["threshold"] = "thirty"
    with pytest.raises(GraphValidationError) as info:
        nb_compile(g)
    assert info.value.node_id == "/below"


def test_comparison_threshold_given_as_text_is_read():
    g = _rsi_entry_graph()
    g.nodes["/below"].params["threshold"] = "30"
    prog = nb_compile(g)
    assert prog.per_bar_program[0].reads == ("@rsi",)


def test_logic_node_with_no_inputs_is_refused():
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/and": _node("/and", "and"),
        "/entry": _node("/entry", "entry"),
    }
    with pytest.raises(GraphValidationError) as info:
        nb_compile(_make_graph(nodes, [_wire("w1", "/and", "/entry")]))
    assert info.value.node_id == "/and"


def test_comparison_with_three_inputs_is_refused():
    """BC-7: the third input was silently dropped."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/rsi": _node("/rsi", "rsi", {"period": 14, "type": "sma"}),
        "/sma": _node("/sma", "sma", {"period": 20}),
        "/ema": _node("/ema", "ema", {"period": 20}),
        "/above": _node("/above", "above"),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        _wire("w1", "/ticker", "/rsi"), _wire("w2", "/ticker", "/sma"), _wire("w3", "/ticker", "/ema"),
        _wire("w4", "/rsi", "/above"), _wire("w5", "/sma", "/above"), _wire("w6", "/ema", "/above"),
        _wire("w7", "/above", "/entry"),
    ]
    with pytest.raises(GraphValidationError) as info:
        nb_compile(_make_graph(nodes, wires))
    assert info.value.node_id == "/above"


def test_not_with_two_inputs_is_refused():
    g = _with_extra(
        {"/b2": _node("/b2", "below", {"threshold": 20.0}), "/not": _node("/not", "not"),
         "/exit": _node("/exit", "exit")},
        [_wire("wa", "/rsi", "/b2"), _wire("wb", "/below", "/not"), _wire("wc", "/b2", "/not"),
         _wire("wd", "/not", "/exit")],
    )
    with pytest.raises(GraphValidationError) as info:
        nb_compile(g)
    assert info.value.node_id == "/not"


def test_entry_with_two_inputs_is_refused():
    g = _with_extra(
        {"/b2": _node("/b2", "below", {"threshold": 20.0})},
        [_wire("wa", "/rsi", "/b2"), _wire("wb", "/b2", "/entry")],
    )
    with pytest.raises(GraphValidationError) as info:
        nb_compile(g)
    assert info.value.node_id == "/entry"


def test_second_entry_is_refused():
    """BC-7: with two Entry terminals the last one silently won."""
    g = _with_extra({"/entry2": _node("/entry2", "entry")}, [_wire("wb", "/below", "/entry2")])
    with pytest.raises(GraphValidationError) as info:
        nb_compile(g)
    assert info.value.node_id == "/entry2"


def test_second_exit_is_refused():
    g = _with_extra(
        {"/exit1": _node("/exit1", "exit"), "/exit2": _node("/exit2", "exit")},
        [_wire("wa", "/below", "/exit1"), _wire("wb", "/below", "/exit2")],
    )
    with pytest.raises(GraphValidationError) as info:
        nb_compile(g)
    assert info.value.node_id == "/exit2"


def test_bypassed_macd_sub_output_is_left_out():
    """BC-6: bypass had no effect on a hand-drawn MACD sub-output wire."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/rsi": _node("/rsi", "rsi", {"period": 14, "type": "sma"}),
        "/macd": _node("/macd", "macd", bypass=True),
        "/rsi_low": _node("/rsi_low", "below", {"threshold": 30.0}),
        "/macd_neg": _node("/macd_neg", "below", {"threshold": 0.0}),
        "/and": _node("/and", "and"),
        "/entry": _node("/entry", "entry"),
    }
    wires = [
        _wire("w1", "/ticker", "/rsi"), _wire("w2", "/ticker", "/macd"),
        _wire("w3", "/rsi", "/rsi_low"),
        Wire(**{"id": "w4", "from": "/macd", "to": "/macd_neg", "attr": "@macd_line"}),
        _wire("w5", "/rsi_low", "/and"), _wire("w6", "/macd_neg", "/and"),
        _wire("w7", "/and", "/entry"),
    ]
    prog = nb_compile(_make_graph(nodes, wires))
    paths = [op.node_path for op in prog.per_bar_program]
    assert "/macd_neg" not in paths
    and_op = next(op for op in prog.per_bar_program if op.node_path == "/and")
    assert len(and_op.reads) == 1


def test_entry_fed_only_by_bypassed_node_names_the_entry():
    g = _rsi_entry_graph()
    g.nodes["/below"].bypass = True
    with pytest.raises(MissingTerminalError) as info:
        nb_compile(g)
    assert info.value.node_id == "/entry"
    assert "bypassed" in str(info.value)


@pytest.mark.parametrize("node_type,params", [
    ("slippage", {"bps": -1}),
    ("slippage", {"bps": None}),
    ("slippage", {"bps": ""}),
    ("position_size", {"size": None}),
    ("position_size", {"size": ""}),
    ("position_size", {"size": 0}),
    ("stop_loss", {"pct": None}),
    ("stop_loss", {"pct": -5}),
    ("stop_loss", {"pct": "abc"}),
    ("commission", {"per_share_rate": -0.01}),
    ("commission", {"min_per_order": float("inf")}),
    ("trailing_stop", {"value": -2}),
    ("trailing_stop", {"value": 0}),
    ("trailing_stop", {"activate_pct": -1}),
])
def test_bad_settings_values_are_refused_with_node_id(node_type, params):
    """BC-4/BC-8/LT-4: these passed deploy and then failed on every tick."""
    g = _with_extra({"/set": _node("/set", node_type, params)})
    with pytest.raises(GraphValidationError) as info:
        nb_compile(g)
    assert info.value.node_id == "/set"


def test_settings_numbers_given_as_text_are_read():
    g = _with_extra({
        "/size": _node("/size", "position_size", {"size": "0.5"}),
        "/stop": _node("/stop", "stop_loss", {"pct": "3"}),
    })
    values = {s.key: s.value for s in nb_compile(g).simulator_settings}
    assert values == {"position_size": 0.5, "stop_loss": 3.0}


def test_bypassed_settings_node_does_not_apply():
    g = _with_extra({"/stop": _node("/stop", "stop_loss", {"pct": 3.0}, bypass=True)})
    assert nb_compile(g).simulator_settings == []


def test_wire_into_settings_node_is_refused():
    """FC-4: a comparison wired into Stop Loss looked like a conditional stop."""
    g = _with_extra(
        {"/stop": _node("/stop", "stop_loss", {"pct": 3.0})},
        [_wire("ws", "/below", "/stop")],
    )
    with pytest.raises(GraphTypeError) as info:
        nb_compile(g)
    assert info.value.node_id == "/stop"


def test_wire_out_of_settings_node_is_refused():
    g = _with_extra(
        {"/stop": _node("/stop", "stop_loss", {"pct": 3.0}), "/exit": _node("/exit", "exit")},
        [_wire("ws", "/stop", "/exit")],
    )
    with pytest.raises(GraphTypeError) as info:
        nb_compile(g)
    assert info.value.node_id == "/stop"


def test_wire_out_of_entry_is_refused():
    """A hidden Entry -> RSI wire (UXP-1) now fails loudly instead of doing nothing."""
    g = _with_extra(
        {"/b2": _node("/b2", "below", {"threshold": 20.0}), "/exit": _node("/exit", "exit")},
        [_wire("we", "/entry", "/b2"), _wire("wr", "/rsi", "/b2"), _wire("wx", "/b2", "/exit")],
    )
    with pytest.raises(GraphTypeError) as info:
        nb_compile(g)
    assert info.value.node_id == "/entry"


def test_wire_into_ticker_is_refused():
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/t2": _node("/t2", "ticker"),
        "/below": _node("/below", "below", {"threshold": 30.0}),
        "/entry": _node("/entry", "entry"),
    }
    wires = [_wire("w0", "/t2", "/ticker"), _wire("w1", "/ticker", "/below"), _wire("w2", "/below", "/entry")]
    with pytest.raises(GraphTypeError) as info:
        nb_compile(_make_graph(nodes, wires))
    assert info.value.node_id == "/ticker"


def test_indicator_fed_by_another_indicator_is_refused():
    g = _with_extra(
        {"/sma": _node("/sma", "sma", {"period": 5}),
         "/b2": _node("/b2", "below", {"threshold": 20.0}), "/exit": _node("/exit", "exit")},
        [_wire("wa", "/rsi", "/sma"), _wire("wb", "/sma", "/b2"), _wire("wc", "/b2", "/exit")],
    )
    with pytest.raises(GraphTypeError) as info:
        nb_compile(g)
    assert info.value.node_id == "/sma"


@pytest.mark.parametrize("rsi_type", ["ema", "SMA", None])
def test_rsi_type_outside_options_is_refused(rsi_type):
    """BC-11: an unknown type ran as a rolling mean."""
    with pytest.raises(GraphValidationError) as info:
        nb_compile(_rsi_entry_graph({"period": 14, "type": rsi_type}))
    assert info.value.node_id == "/rsi"

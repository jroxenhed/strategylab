"""W5 item 5.A: terminals, the borrow_rate settings node, the simulator
bridge (nodebuilder.trading.sim_bridge) and the new _run_simulation inputs.

The rule-versus-terminal parity checks live in test_terminal_rule_parity.py.
No test here fetches data, starts a bot or places an order.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from models import Rule, StrategyRequest, TrailingStopConfig  # noqa: E402
from nodebuilder.api_models import GraphBacktestRequest  # noqa: E402
from nodebuilder.compile import compile as nb_compile  # noqa: E402
from nodebuilder.compile import compile_with_diagnostics  # noqa: E402
from nodebuilder.from_rules import auto_render  # noqa: E402
from nodebuilder.kernel import registry  # noqa: E402
from nodebuilder.models import Graph, GraphValidationError  # noqa: E402
from nodebuilder.trading import sim_bridge as sb  # noqa: E402
from routes.backtest import _run_simulation, series_entry_block, series_entry_size  # noqa: E402
from shared import _format_time_index  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = StrategyRequest(
    ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d", source="yahoo",
    buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
    sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
)
_RSI = "/rsi_period_14_type_sma"
_TICKER = "/ticker_aapl_1d_yahoo"


def _graph(nodes: dict | None = None, wires: tuple = (), drop: tuple = (), req=_BASE,
           bypass: tuple = ()) -> Graph:
    """The auto-rendered RSI graph, with nodes added ({id: (type, params)}),
    wires added ((from, to) pairs into in0) and nodes dropped."""
    d = auto_render(req).model_dump(by_alias=True)
    d["readOnly"] = False
    for nid in drop:
        d["nodes"].pop(nid)
        d["wires"] = [w for w in d["wires"] if nid not in (w["from"], w["to"])]
    for nid, (ntype, params) in (nodes or {}).items():
        d["nodes"][nid] = {"id": nid, "type": ntype, "params": dict(params),
                           "position": [0.0, 0.0], "bypass": nid in bypass}
    for nid in bypass:
        d["nodes"][nid]["bypass"] = True
    for a, b in wires:
        d["wires"].append({"id": f"w_{a.strip('/')}_{b.strip('/')}", "from": a, "to": b,
                           "from_port": "out", "to_port": "in0"})
    return Graph.model_validate(d)


def _codes(graph: Graph) -> list[tuple[str, str, str | None]]:
    _prog, diags = compile_with_diagnostics(graph)
    return [(d.code, d.severity, d.node_id) for d in diags]


def _error(graph: Graph) -> GraphValidationError:
    with pytest.raises(GraphValidationError) as info:
        nb_compile(graph)
    return info.value


def _plan(graph: Graph, **kw) -> sb.GroupPlan:
    return sb.plan_group(nb_compile(graph), **kw)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_terminal_types_and_params():
    want = {
        "entry": ["signal", "side"],
        "exit": ["signal", "side"],
        "size": ["value", "constant", "side"],
        "stop": ["value", "constant", "side"],
        "trailing_stop": ["type", "value", "source", "activate_on_profit", "activate_pct",
                          "side", "out"],
        "time_stop": ["max_bars", "side", "out"],
        "regime": ["signal", "on_flip"],
    }
    for name, params in want.items():
        nt = registry.get(name)
        assert nt is not None, name
        assert nt.entry.cat == "output", name
        assert nt.compile_active, name
        assert not nt.has_output, name
        assert [p.name for p in nt.params] == params, name
    assert registry.get("regime").param("on_flip").options == ("hold", "close_only", "close_and_reverse")
    assert registry.get("entry").param("side").options == ("long", "short")
    # Size, stop, trailing stop and time stop: both sides by default (F435 W5
    # DI-01..03, a regime_switch group's per-side terminals).
    for name in ("size", "stop", "trailing_stop", "time_stop"):
        side = registry.get(name).param("side")
        assert side.options == ("both", "long", "short") and side.default == "both", name


def test_trailing_stop_terminal_keeps_every_trailing_stop_config_field():
    nt = registry.get("trailing_stop")
    defaults = {p.name: p.default for p in nt.params if p.name not in ("out", "side")}
    assert defaults == TrailingStopConfig().model_dump()
    # Still bypassable: a stored graph with a bypassed trailing stop must not
    # start trailing after the settings node became a terminal.
    assert nt.bypassable


def test_borrow_rate_settings_node():
    nt = registry.get("borrow_rate")
    assert nt.entry.cat == "settings"
    assert nt.entry.defaults["setting_key"] == "borrow_rate_annual"
    assert nt.param("rate").default == 0.5


# ---------------------------------------------------------------------------
# Entry and Exit side
# ---------------------------------------------------------------------------


def test_entry_side_defaults_to_long_and_rejects_others():
    prog = nb_compile(_graph())
    assert prog.step("/entry").params["side"] == "long"
    g = _graph()
    d = g.model_dump(by_alias=True)
    d["nodes"]["/entry"]["params"]["side"] = "sideways"
    err = _error(Graph.model_validate(d))
    assert err.code == "param_invalid" and err.node_id == "/entry" and err.param == "side"


# ---------------------------------------------------------------------------
# Size and Stop terminals
# ---------------------------------------------------------------------------


def test_empty_size_and_stop_terminals_set_nothing():
    plan = _plan(_graph({"/size_t": ("size", {}), "/stop_t": ("stop", {})}))
    assert "stop_loss_pct" not in plan.fields
    # position_size comes from the auto-rendered settings node only.
    assert plan.sources["position_size"] == "/setting_position_size"
    assert plan.size is None and plan.stop is None and plan.shadowed == ()


def test_size_constant_wins_over_position_size_setting():
    plan = _plan(_graph({"/size_t": ("size", {"constant": 0.5})}))
    assert plan.fields["position_size"] == 0.5
    assert plan.sources["position_size"] == "/size_t"
    assert plan.shadowed == (("/setting_position_size", "/size_t"),)


def test_size_constant_above_one_warns():
    codes = _codes(_graph({"/size_t": ("size", {"constant": 50})}))
    assert ("size_unit_suspect", "warning", "/size_t") in codes


@pytest.mark.parametrize("constant", [0, -0.5, "abc"])
def test_size_constant_must_be_above_zero(constant):
    err = _error(_graph({"/size_t": ("size", {"constant": constant})}))
    assert err.node_id == "/size_t" and err.param == "constant"
    assert err.code in ("param_invalid", "param_out_of_range")


def test_stop_constant_zero_means_no_stop_and_wins():
    req = _BASE.model_copy(update={"stop_loss_pct": 5.0})
    plan = _plan(_graph({"/stop_t": ("stop", {"constant": 0})}, req=req))
    assert plan.fields["stop_loss_pct"] == 0.0
    assert plan.shadowed == (("/setting_stop_loss", "/stop_t"),)


def test_wired_size_reads_a_column_and_drops_the_setting():
    g = _graph({"/half": ("constant", {"value": 0.5}), "/size_t": ("size", {})},
               wires=(("/half", "/size_t"),))
    plan = _plan(g)
    assert plan.size == sb.ColumnRead("/size_t", "@const")
    assert "position_size" not in plan.fields
    assert plan.shadowed == (("/setting_position_size", "/size_t"),)
    assert "/size_t" in plan.keep_ids()


def test_wired_value_wins_over_constant():
    g = _graph({"/half": ("constant", {"value": 0.5}), "/size_t": ("size", {"constant": 0.25})},
               wires=(("/half", "/size_t"),))
    plan = _plan(g)
    assert plan.size is not None and "position_size" not in plan.fields


def test_wired_bool_into_size_is_refused_on_the_terminal():
    err = _error(_graph({"/size_t": ("size", {})}, wires=(("/cmp_buy_0", "/size_t"),)))
    assert err.code == "attr_type" and err.node_id == "/size_t"


def test_named_value_with_nothing_wired_is_refused():
    err = _error(_graph({"/stop_t": ("stop", {"value": "@stop_pct", "constant": 2})}))
    assert err.code == "missing_input" and err.node_id == "/stop_t"


def test_value_from_a_bypassed_node_is_refused_not_swapped_for_the_constant():
    g = _graph({"/three": ("constant", {"value": 3.0}), "/stop_t": ("stop", {"constant": 9})},
               wires=(("/three", "/stop_t"),), bypass=("/three",))
    err = _error(g)
    assert err.code == "missing_input" and err.node_id == "/stop_t"


def test_constant_terminals_land_in_the_group_plan_fields():
    # W5: program.simulator_settings is gone; the group plan holds the
    # simulator fields by request field name (5.B Needs 2).
    prog = nb_compile(_graph({"/size_t": ("size", {"constant": 0.5}),
                              "/stop_t": ("stop", {"constant": 2.0}),
                              "/time_t": ("time_stop", {"max_bars": 7})}))
    fields = prog.groups[0].plan.fields
    assert (fields["position_size"], fields["stop_loss_pct"], fields["max_bars_held"]) == (0.5, 2.0, 7)


# ---------------------------------------------------------------------------
# Trailing stop, time stop, borrow rate
# ---------------------------------------------------------------------------


def test_trailing_stop_terminal_plans_its_config():
    params = {"type": "pct", "value": 4.0, "source": "close",
              "activate_on_profit": "true", "activate_pct": 2.5}
    plan = _plan(_graph({"/trail": ("trailing_stop", params)}))
    assert plan.fields["trailing_stop"] == TrailingStopConfig(
        type="pct", value=4.0, source="close", activate_on_profit=True, activate_pct=2.5)


def test_trailing_stop_pct_param_is_refused_not_ignored():
    err = _error(_graph({"/trail": ("trailing_stop", {"pct": 2.5})}))
    assert err.code == "param_invalid" and err.param == "pct"


def test_bypassed_trailing_stop_does_not_apply():
    plan = _plan(_graph({"/trail": ("trailing_stop", {"value": 3.0})}, bypass=("/trail",)))
    assert "trailing_stop" not in plan.fields


@pytest.mark.parametrize("max_bars,code", [(0, "param_out_of_range"), (2.5, "param_invalid"),
                                           ("x", "param_invalid")])
def test_time_stop_needs_a_whole_number_of_bars(max_bars, code):
    err = _error(_graph({"/time_t": ("time_stop", {"max_bars": max_bars})}))
    assert err.code == code and err.node_id == "/time_t" and err.param == "max_bars"


def test_time_stop_plans_max_bars_held_and_shows_it_as_detail():
    g = _graph({"/time_t": ("time_stop", {"max_bars": "12"})})
    plan = _plan(g)
    assert plan.fields["max_bars_held"] == 12
    empty = _plan(_graph({"/time_t": ("time_stop", {})}))
    assert "max_bars_held" not in empty.fields
    # The detail write shows in the stream (data sheet).
    prog = nb_compile(g)
    df = _frame([100.0] * 30)
    result = sb.cook(prog, _attrs(prog, df), {"/time_t"})
    assert result.stream("/time_t").value("@max_bars") == 12.0


def test_borrow_rate_plans_and_refuses_negative():
    plan = _plan(_graph({"/borrow": ("borrow_rate", {"rate": 3.0})}))
    assert plan.fields["borrow_rate_annual"] == 3.0
    err = _error(_graph({"/borrow": ("borrow_rate", {"rate": -1})}))
    assert err.code == "param_out_of_range" and err.node_id == "/borrow"


def test_later_settings_node_wins():
    plan = _plan(_graph({"/slip_a": ("slippage", {"bps": 7.0})}))
    # The auto-rendered slippage node comes first in step order or not; the
    # plan follows step order, as compile's settings list does.
    prog = nb_compile(_graph({"/slip_a": ("slippage", {"bps": 7.0})}))
    order = [s.node_id for s in prog.steps if s.type == "slippage"]
    assert plan.sources["slippage_bps"] == order[-1]
    pinned = sb.plan_group(prog, settings_nodes=["/slip_a", "/setting_slippage"])
    assert pinned.fields["slippage_bps"] == 2.0
    pinned = sb.plan_group(prog, settings_nodes=["/setting_slippage", "/slip_a"])
    assert pinned.fields["slippage_bps"] == 7.0


def test_a_wire_into_trailing_or_time_stop_reads_nothing():
    # Every terminal has one input port (S32b); these two read nothing from it.
    nodes = {"/trail": ("trailing_stop", {"value": 3.0}), "/time_t": ("time_stop", {"max_bars": 5})}
    plain = _plan(_graph(nodes))
    wired = _plan(_graph(nodes, wires=((_RSI, "/trail"), (_RSI, "/time_t"))))
    assert wired == plain


# ---------------------------------------------------------------------------
# Regime terminal
# ---------------------------------------------------------------------------


def _regime_graph(on_flip="hold", *, bypass=()):
    nodes = {"/sma": ("sma", {"period": 50}),
             "/up": ("above", {"a": "@close", "b": "@sma", "out": "@uptrend"}),
             "/reg": ("regime", {"on_flip": on_flip})}
    return _graph(nodes, wires=((_TICKER, "/sma"), ("/sma", "/up"), ("/up", "/reg")), bypass=bypass)


def test_regime_terminal_plans_its_signal():
    plan = _plan(_regime_graph("close_only"))
    assert plan.regime == sb.ColumnRead("/reg", "@uptrend")
    assert plan.on_flip == "close_only"
    assert _plan(_graph()).on_flip == "hold"


def test_regime_unwired_or_bypassed_is_refused():
    err = _error(_graph({"/reg": ("regime", {})}))
    assert err.code == "missing_input" and err.node_id == "/reg"
    err = _error(_regime_graph(bypass=("/up",)))
    assert err.code == "missing_input" and err.node_id == "/reg"


def test_regime_on_flip_must_be_known():
    err = _error(_regime_graph("flip_flop"))
    assert err.code == "param_invalid" and err.param == "on_flip"


# ---------------------------------------------------------------------------
# GroupTerminals and plan_group rules
# ---------------------------------------------------------------------------


def test_second_single_terminal_is_refused():
    # W5 (D7): compile refuses a second time_stop in a group (5.B Needs 4).
    with pytest.raises(GraphValidationError) as info:
        nb_compile(_graph({"/t1": ("time_stop", {"max_bars": 3}),
                           "/t2": ("time_stop", {"max_bars": 4})}))
    assert info.value.code == "duplicate_terminal" and info.value.node_id == "/t2"


def test_terminals_limited_to_node_ids():
    from tests.nodebuilder.test_terminal_rule_parity import (
        _program_without_whole_graph_terminal_rules,
    )

    # Compile refuses the second time_stop now, so build the program without
    # compile's group rules (5.B Needs 4).
    prog = _program_without_whole_graph_terminal_rules(
        _graph({"/t1": ("time_stop", {"max_bars": 3}),
                "/t2": ("time_stop", {"max_bars": 4})}))
    with pytest.raises(GraphValidationError) as info:
        sb.GroupTerminals.from_program(prog)
    assert info.value.code == "duplicate_terminal"
    t = sb.GroupTerminals.from_program(prog, ["/entry", "/exit", "/t2"])
    assert t.time_stop == "/t2" and t.entries == ("/entry",)


def test_unwired_entry_is_refused_by_name():
    # compile refuses it first; a program built without compile's terminal
    # rules still gets a named error from the bridge.
    prog = nb_compile(_graph(req=_BASE.model_copy(update={"sell_rules": []})))
    t = sb.GroupTerminals.from_program(prog)
    assert prog.step("/exit").params["signal"] is None
    exit_only = sb.GroupTerminals(entries=t.exits, exits=())
    with pytest.raises(GraphValidationError) as info:
        sb.plan_group(prog, exit_only)
    assert info.value.code == "missing_input" and info.value.node_id == "/exit"


def test_regime_switch_needs_regime_and_both_entries():
    prog = nb_compile(_graph())
    with pytest.raises(GraphValidationError) as info:
        sb.plan_group(prog, direction="regime_switch")
    assert info.value.code == "group_invalid"
    prog = nb_compile(_regime_graph())
    with pytest.raises(GraphValidationError) as info:
        sb.plan_group(prog, direction="regime_switch")
    assert info.value.code == "missing_terminal"  # no short entry
    with pytest.raises(GraphValidationError):
        sb.plan_group(prog, direction="sideways")


# ---------------------------------------------------------------------------
# _run_simulation: the new inputs on a small synthetic frame
# ---------------------------------------------------------------------------


def _frame(closes, lows=None, highs=None) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "Open": closes, "High": highs if highs is not None else closes,
        "Low": lows if lows is not None else closes, "Close": closes,
        "Volume": [1000.0] * n,
    }, index=idx)


def _attrs(program, df):
    from nodebuilder.prepare import build_graph_attrs
    return build_graph_attrs(program, df)


def _req(**kw) -> StrategyRequest:
    base = dict(ticker="TEST", start="2024-01-01", end="2024-12-31", interval="1d",
                buy_rules=[], sell_rules=[], slippage_bps=0.0, borrow_rate_annual=0.0)
    base.update(kw)
    return StrategyRequest(**base)


def _sim(df, entries, exits, req, **kw):
    def buy(i, active):
        return bool(entries[i]), [], req.direction

    def sell(i, direction, active):
        return bool(exits[i]), []

    return _run_simulation(df=df, indicators={}, buy_signal_fn=buy, sell_signal_fn=sell,
                           req=req, b23_mode=False, **kw)


def _flags(n, *on):
    out = np.zeros(n, dtype=bool)
    out[list(on)] = True
    return out


_CLOSES = [100.0, 101, 102, 103, 104, 105, 104, 103, 102, 101, 100, 99, 100, 101, 102, 103]


def test_no_new_inputs_matches_the_old_positional_call():
    df = _frame(_CLOSES)
    n = len(df)
    req = _req(position_size=0.5, stop_loss_pct=2.0)
    entries, exits = _flags(n, 1, 9), _flags(n, 5, 14)
    date_strs = _format_time_index(df.index, req.interval)

    def buy(i, active):
        return bool(entries[i]), [], "long"

    def sell(i, d, active):
        return bool(exits[i]), []

    old = _run_simulation(df, {}, buy, sell, req, False, None, "hold", date_strs)
    new = _run_simulation(df=df, indicators={}, buy_signal_fn=buy, sell_signal_fn=sell,
                          req=req, b23_mode=False)
    assert old == new and len(old["trades"]) == 4


def test_constant_size_series_equals_position_size():
    df = _frame(_CLOSES)
    n = len(df)
    entries, exits = _flags(n, 1, 9), _flags(n, 5, 14)
    want = _sim(df, entries, exits, _req(position_size=0.5))
    got = _sim(df, entries, exits, _req(), size_series=np.full(n, 0.5))
    assert got == want


def test_size_series_is_read_at_the_entry_bar():
    df = _frame(_CLOSES)
    n = len(df)
    size = np.full(n, 0.9)
    size[1] = 0.2
    out = _sim(df, _flags(n, 1), _flags(n, 5), _req(), size_series=size)
    buy = out["trades"][0]
    assert buy["shares"] == pytest.approx(10000 * 0.2 / 101.0, abs=1e-4)


def test_size_series_is_clamped_like_position_size():
    df = _frame(_CLOSES)
    n = len(df)
    big = _sim(df, _flags(n, 1), _flags(n, 5), _req(), size_series=np.full(n, 7.0))
    full = _sim(df, _flags(n, 1), _flags(n, 5), _req(position_size=1.0))
    assert big == full
    tiny = _sim(df, _flags(n, 1), _flags(n, 5), _req(), size_series=np.full(n, 0.001))
    assert tiny["trades"][0]["shares"] == pytest.approx(10000 * 0.01 / 101.0, abs=1e-4)


@pytest.mark.parametrize("bad", [np.nan, 0.0, -0.3, np.inf])
def test_size_series_with_no_usable_value_blocks_the_entry(bad):
    df = _frame(_CLOSES)
    n = len(df)
    size = np.full(n, 0.5)
    size[1] = bad
    out = _sim(df, _flags(n, 1, 3), _flags(n, 5), _req(debug=True), size_series=size)
    # The bar-1 entry is skipped; the bar-3 entry opens.
    assert out["trades"][0]["type"] == "buy"
    assert out["trades"][0]["date"] == _format_time_index(df.index, "1d")[3]
    actions = [t["action"] for t in out["signal_trace"]]
    assert "SKIPPED (no size value)" in actions


def test_stop_series_is_fixed_at_the_entry_bar():
    # Entry at bar 1 (101).  The low on bar 3 is 3% under the entry.
    closes = [100.0, 101, 101, 101, 101, 101]
    lows = [100.0, 101, 101, 101 * 0.97, 101, 101]
    df = _frame(closes, lows=lows)
    n = len(df)
    stop = np.full(n, 50.0)
    stop[1] = 2.0  # the trade's stop is the value at its entry bar
    out = _sim(df, _flags(n, 1), _flags(n), _req(), stop_series=stop)
    exit_trade = out["trades"][1]
    assert exit_trade["stop_loss"] is True
    assert exit_trade["price"] == pytest.approx(101 * 0.98, abs=1e-4)
    # The same as a constant stop_loss_pct of 2.
    assert out == _sim(df, _flags(n, 1), _flags(n), _req(stop_loss_pct=2.0))


def test_stop_series_nan_blocks_and_zero_means_no_stop():
    closes = [100.0, 101, 101, 101, 101, 101]
    lows = [100.0, 101, 101, 101 * 0.97, 101, 101]
    df = _frame(closes, lows=lows)
    n = len(df)
    blocked = _sim(df, _flags(n, 1), _flags(n), _req(), stop_series=np.full(n, np.nan))
    assert blocked["trades"] == []
    no_stop = _sim(df, _flags(n, 1), _flags(n), _req(stop_loss_pct=2.0),
                   stop_series=np.zeros(n))
    assert len(no_stop["trades"]) == 1  # the 2% request stop does not apply


def test_regime_series_may_be_a_plain_array():
    df = _frame(_CLOSES)
    n = len(df)
    regime = np.array([i % 5 < 3 for i in range(n)])
    entries, exits = _flags(n, 1, 6, 11), _flags(n, 4, 9, 14)
    as_series = _sim(df, entries, exits, _req(), regime_active_series=pd.Series(regime, index=df.index),
                     on_flip="close_only")
    as_array = _sim(df, entries, exits, _req(), regime_active_series=regime, on_flip="close_only")
    assert as_series == as_array
    assert any(t.get("exit_reason") == "regime_flip" for t in as_array["trades"])


def test_series_of_the_wrong_length_raises():
    df = _frame(_CLOSES)
    with pytest.raises(ValueError):
        _sim(df, _flags(len(df), 1), _flags(len(df)), _req(), size_series=np.ones(3))


def test_dynamic_sizing_scales_the_series_size():
    from models import DynamicSizingConfig
    # Stop out on the first trade, then the second entry uses half the series size.
    closes = [100.0, 100, 100, 100, 100, 100, 100, 100]
    lows = [100.0, 100, 90, 100, 100, 100, 100, 100]
    df = _frame(closes, lows=lows)
    n = len(df)
    ds = DynamicSizingConfig(enabled=True, consec_sls=1, reduced_pct=50.0)
    out = _sim(df, _flags(n, 1, 4), _flags(n), _req(stop_loss_pct=5.0, dynamic_sizing=ds),
               size_series=np.full(n, 0.4))
    second = [t for t in out["trades"] if t["type"] == "buy"][1]
    capital_before = out["trades"][1]["pnl"] + 10000
    assert second["shares"] == pytest.approx(capital_before * 0.4 * 0.5 / 100.0, abs=1e-3)


def test_reverse_entry_uses_the_series_and_can_be_blocked():
    df = _frame(_CLOSES)
    n = len(df)
    regime = np.array([i < 6 for i in range(n)])  # flips off at bar 6
    size = np.full(n, 0.5)
    out = _sim(df, _flags(n, 1), _flags(n), _req(), regime_active_series=regime,
               on_flip="close_and_reverse", size_series=size)
    reverse = [t for t in out["trades"] if t.get("rules") == ["regime flip reverse"]]
    assert len(reverse) == 1 and reverse[0]["direction"] == "short"
    size[6] = np.nan
    out = _sim(df, _flags(n, 1), _flags(n), _req(debug=True), regime_active_series=regime,
               on_flip="close_and_reverse", size_series=size)
    assert not [t for t in out["trades"] if t.get("rules") == ["regime flip reverse"]]
    assert "REVERSE SKIPPED (no size value)" in [t["action"] for t in out["signal_trace"]]


def test_series_entry_helpers():
    assert series_entry_block(None, None) is None
    assert series_entry_block(0.5, 2.0) is None
    assert series_entry_block(math.nan, 2.0) == "size"
    assert series_entry_block(0.0, None) == "size"
    assert series_entry_block(None, math.nan) == "stop"
    assert series_entry_block(None, -1.0) is None  # no stop, allowed
    assert series_entry_size(3.0) == 1.0 and series_entry_size(0.001) == 0.01


# ---------------------------------------------------------------------------
# The bridge end to end, and the live-bot helpers
# ---------------------------------------------------------------------------


def _graph_request(graph: Graph, **kw) -> GraphBacktestRequest:
    return GraphBacktestRequest(graph=graph, ticker="AAPL", start="2022-01-01", end="2024-01-01",
                                **kw)


def _rsi_frame() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    df = _frame(list(closes), lows=list(closes * 0.99), highs=list(closes * 1.01))
    return df


def test_run_group_with_regime_gates_entries():
    g = _regime_graph("hold")
    prog = nb_compile(g)
    df = _rsi_frame()
    run = sb.run_group(prog, df, _graph_request(g))
    regime = np.asarray(run.result.column("/reg", "@uptrend"), dtype=bool)
    dates = _format_time_index(df.index, "1d")
    for t in run.sim["trades"]:
        if t["type"] == "buy":
            assert regime[dates.index(t["date"])]
    plain = sb.run_group(nb_compile(_graph()), df, _graph_request(_graph()))
    n_buys = sum(t["type"] == "buy" for t in run.sim["trades"])
    # The fixture trades, and the regime blocks some of the plain entries.
    assert 0 < n_buys < sum(t["type"] == "buy" for t in plain.sim["trades"])
    # An always-true regime gives the plain graph's result.
    open_g = _graph({"/on": ("constant", {"value": 1.0}),
                     "/gt": ("above", {"a": "@const", "threshold": 0.5}),
                     "/reg": ("regime", {"on_flip": "close_only"})},
                    wires=(("/on", "/gt"), ("/gt", "/reg")))
    gated = sb.run_group(nb_compile(open_g), df, _graph_request(open_g))
    assert gated.sim == plain.sim


def test_run_group_close_and_reverse_trades_both_sides():
    g = _regime_graph("close_and_reverse")
    run = sb.run_group(nb_compile(g), _rsi_frame(), _graph_request(g))
    sides = {t["direction"] for t in run.sim["trades"]}
    assert sides == {"long", "short"}


def test_entry_sample_uses_the_simulator_rules():
    g = _graph({"/half": ("constant", {"value": 0.5}), "/size_t": ("size", {}),
                "/three": ("constant", {"value": 3.0, "out": "@stop_pct"}), "/stop_t": ("stop", {})},
               wires=(("/half", "/size_t"), ("/three", "/stop_t")))
    prog = nb_compile(g)
    plan = sb.plan_group(prog)
    df = _rsi_frame()
    result = sb.cook(prog, _attrs(prog, df), plan.keep_ids())
    sample = sb.entry_sample(plan, result)
    assert sample == sb.EntrySample(size=0.5, stop_pct=3.0, blocked=None)
    no_wires = sb.plan_group(nb_compile(_graph()))
    assert sb.entry_sample(no_wires, sb.cook(nb_compile(_graph()), _attrs(prog, df))) == \
        sb.EntrySample(None, None, None)


def test_apply_to_bot_config_sets_graph_fields_and_clamps():
    from bot_manager import BotConfig

    g = _graph({"/size_t": ("size", {"constant": 50}), "/time_t": ("time_stop", {"max_bars": 9}),
                "/borrow": ("borrow_rate", {"rate": 2.0}),
                "/trail": ("trailing_stop", {"value": 3.0, "activate_on_profit": True,
                                             "activate_pct": 1.0})})
    plan = _plan(g)
    cfg = BotConfig(strategy_name="t", symbol="AAPL", interval="1d", buy_rules=[], sell_rules=[],
                    allocated_capital=1000.0, long_stop_loss_pct=4.0, max_bars_held=2)
    out = sb.apply_to_bot_config(cfg, plan)
    assert out.position_size == 1.0  # 50 clamps to 1.0, never 50x
    assert out.max_bars_held == 9 and out.borrow_rate_annual == 2.0
    assert out.trailing_stop == TrailingStopConfig(value=3.0, activate_on_profit=True,
                                                   activate_pct=1.0)
    assert out.long_stop_loss_pct is None

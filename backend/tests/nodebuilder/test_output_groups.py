"""Output Groups (F435 W5 item 5.B, plan D7).

- A two-group backtest equals the two standalone single-group backtests.
- The combined exposure_pct and gross_deployed_pct match a hand-computed
  fixture.
- A one-group graph's legacy fields equal today's response.
- Group rules: implicit "main", terminals outside a group, group params,
  scoped cost constants, setting_shadowed, a group's reference Ticker (plan
  D8; test_reference_tickers.py has the rest), and the route's response
  shape.

No test fetches data or touches a bot: frames are synthetic and passed in.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import shared
from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.compile import check_graph
from nodebuilder.compile import compile as nb_compile
from nodebuilder.models import Graph, GraphValidationError
from nodebuilder.run import run_graph_backtest, run_graph_backtest_cooked
from nodebuilder.trading import nodes_groups as ng
from nodebuilder.trading import sim_bridge as sb

N = 260
START, END = "2023-01-02", "2023-12-29"


def _frame(seed: int, start: str = "2023-01-02", n: int = N) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100 + 9 * np.sin(t / (9 + seed)) + np.cumsum(rng.normal(0, 0.8, n))
    idx = pd.date_range(start, periods=n, freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": close, "High": close + 0.6, "Low": close - 0.6,
                         "Close": close, "Volume": rng.integers(1e5, 1e6, n)}, index=idx)


AAPL = _frame(1)
MSFT = _frame(2)
FRAMES = {("AAPL", "1d"): AAPL, ("MSFT", "1d"): MSFT}


def _node(nid, typ, params=None, parent=None, name=None, bypass=False):
    return {"id": nid, "type": typ, "name": name or nid, "parent": parent,
            "params": params or {}, "bypass": bypass}


def _leg(p: str, symbol: str, parent=None, lo=38, hi=62, extra=()):
    """One leg: Ticker -> RSI -> below/above -> Entry/Exit, ids prefixed *p*."""
    nodes = [
        _node(f"{p}t", "ticker", {"symbol": symbol, "interval": "1d"}, parent, name="tick"),
        _node(f"{p}rsi", "rsi", {"period": 14}, parent, name="rsi"),
        _node(f"{p}lo", "below", {"threshold": lo, "out": f"@lo_{p}"}, parent, name="lo"),
        _node(f"{p}hi", "above", {"threshold": hi, "out": f"@hi_{p}"}, parent, name="hi"),
        _node(f"{p}entry", "entry", {}, parent, name="entry"),
        _node(f"{p}exit", "exit", {}, parent, name="exit"),
        *extra,
    ]
    wires = [
        (f"{p}w1", f"{p}t", f"{p}rsi", "in0"),
        (f"{p}w2", f"{p}rsi", f"{p}lo", "in0"),
        (f"{p}w3", f"{p}rsi", f"{p}hi", "in0"),
        (f"{p}w4", f"{p}lo", f"{p}entry", "in0"),
        (f"{p}w5", f"{p}hi", f"{p}exit", "in0"),
    ]
    return nodes, wires


def _data(nodes, wires) -> dict:
    return {
        "_version": 3,
        "nodes": {n["id"]: n for n in nodes},
        "wires": [{"id": w, "from": a, "to": b, "to_port": port} for w, a, b, port in wires],
    }


def _graph(nodes, wires) -> Graph:
    return Graph.model_validate(_data(nodes, wires))


def _group(gid, direction, weight=1.0, ticker="tick", parent=None):
    return _node(gid, "output_group",
                 {"direction": direction, "ticker": ticker, "capital_weight": weight}, parent)


def _pair(weight_a=1.0, weight_b=1.0, root=(), a_extra=(), b_extra=()):
    a_nodes, a_wires = _leg("a_", "AAPL", "long_leg", extra=a_extra)
    b_nodes, b_wires = _leg("b_", "MSFT", "short_leg", lo=60, hi=40, extra=b_extra)
    nodes = [_group("long_leg", "long", weight_a), _group("short_leg", "short", weight_b),
             *a_nodes, *b_nodes, *root]
    return _graph(nodes, a_wires + b_wires)


def _req(graph, **kw) -> GraphBacktestRequest:
    fields = {"ticker": "AAPL", "start": START, "end": END, "interval": "1d",
              "source": "yahoo", "initial_capital": 10000.0, **kw}
    return GraphBacktestRequest(graph=graph, **fields)


def _standalone(prefix, symbol, direction, capital, lo, hi, df, extra=(), **kw):
    nodes, wires = _leg(prefix, symbol, None, lo=lo, hi=hi, extra=extra)
    req = _req(_graph(nodes, wires), ticker=symbol, direction=direction,
               initial_capital=capital, **kw)
    return run_graph_backtest(req, df=df)


def _codes(graph) -> list[tuple]:
    return [(d.code, d.node_id) for d in check_graph(graph).diagnostics]


# ---------------------------------------------------------------------------
# Compile: CompiledProgram.groups
# ---------------------------------------------------------------------------


def test_graph_without_groups_is_one_implicit_main_group():
    nodes, wires = _leg("", "AAPL")
    prog = nb_compile(_graph(nodes, wires))
    [g] = prog.groups
    assert (g.name, g.node_id, g.path, g.implicit) == ("main", None, "/", True)
    assert g.direction is None and g.symbol is None and g.weight == 1.0
    assert g.resolve("MSFT", "1h", "short") == ("MSFT", "1h", "short")
    assert g.plan_for("short").direction == "short"
    assert g.entry_attrs == ("@lo_",) and g.exit_attrs == ("@hi_",)
    assert (prog.entry_attr, prog.exit_attr) == ("@lo_", "@hi_")
    assert not hasattr(prog, "simulator_settings")


def test_two_groups_compile_in_file_order():
    prog = nb_compile(_pair(weight_a=3, weight_b=1))
    a, b = prog.groups
    assert (a.name, a.node_id, a.path, a.direction, a.symbol, a.interval, a.weight) == \
           ("long_leg", "long_leg", "/long_leg", "long", "AAPL", "1d", 3.0)
    assert (b.name, b.direction, b.symbol, b.primary_ticker_id) == \
           ("short_leg", "short", "MSFT", "b_t")
    assert b.plan_for("long").direction == "short"   # an explicit group keeps its own
    assert a.step_ids == {"a_t", "a_rsi", "a_lo", "a_hi", "a_entry", "a_exit"}
    assert ng.group_named(prog, "short_leg") is b
    with pytest.raises(KeyError):
        ng.group_named(prog, None)
    # The legacy program fields name the first group's signals.
    assert (prog.entry_node, prog.exit_node) == ("a_entry", "a_exit")


def test_terminal_outside_every_group_is_refused():
    extra = [_node("stray", "time_stop", {"max_bars": 3})]
    codes = _codes(_pair(root=extra))
    assert ("group_terminal_outside", "stray") in codes


def test_group_params_are_checked():
    g = _pair()
    bad = g.model_copy(deep=True)
    bad.nodes["long_leg"].params["capital_weight"] = -1
    bad.nodes["short_leg"].params["ticker"] = "nowhere"
    codes = _codes(bad)
    assert ("param_invalid", "long_leg") in codes
    # A ticker path that points at no Ticker is the plan's ticker_missing (DI-05).
    assert ("ticker_missing", "short_leg") in codes


def test_two_groups_with_one_name_are_refused():
    g = _pair().model_copy(deep=True)
    g.nodes["short_leg"].name = "long_leg"
    assert ("group_invalid", "short_leg") in _codes(g)


def test_group_without_entry_is_refused():
    a_nodes, a_wires = _leg("a_", "AAPL", "long_leg")
    nodes = [_group("long_leg", "long"), _group("empty", "short", ticker="/long_leg/tick"),
             *a_nodes]
    codes = _codes(_graph(nodes, a_wires))
    assert ("missing_terminal", "empty") in codes


def test_group_without_exit_warns_on_the_group():
    a_nodes, a_wires = _leg("a_", "AAPL", "long_leg")
    a_nodes = [n for n in a_nodes if n["id"] != "a_exit"]
    a_wires = [w for w in a_wires if w[2] != "a_exit"]
    result = check_graph(_graph([_group("long_leg", "long"), *a_nodes], a_wires))
    assert result.program is not None
    assert ("exit_unconnected", "long_leg") in [(d.code, d.node_id) for d in result.diagnostics]
    assert result.program.groups[0].exit_connected is False


def test_regime_switch_group_needs_a_regime_terminal():
    a_nodes, a_wires = _leg("a_", "AAPL", "sw")
    short_entry = _node("a_sentry", "entry", {"side": "short"}, "sw", name="sentry")
    nodes = [_group("sw", "regime_switch"), *a_nodes, short_entry]
    wires = a_wires + [("a_w6", "a_hi", "a_sentry", "in0")]
    result = check_graph(_graph(nodes, wires))
    assert result.program is None
    assert "group_invalid" in [d.code for d in result.diagnostics]


def test_reference_ticker_reads_its_own_frame():
    """A group whose signal reads a second Ticker (W5 5.C, plan D8): the
    Ticker reads its own symbol's bars, never the group's.  Here SPY's
    frame is MSFT's synthetic bars, so the group's Entry must be RSI(MSFT)
    below 38 while it trades AAPL."""
    a_nodes, a_wires = _leg("a_", "AAPL", "long_leg")
    spy = _node("a_spy", "ticker", {"symbol": "SPY", "interval": "1d"}, "long_leg", name="spy")
    a_wires = [w if w[0] != "a_w1" else ("a_w1", "a_spy", "a_rsi", "in0") for w in a_wires]
    graph = _graph([_group("long_leg", "long"), *a_nodes, spy], a_wires)
    assert [c for c in _codes(graph) if c[0] != "exit_unconnected"] == []
    [group] = nb_compile(graph).groups
    assert [(r.node_id, r.key) for r in group.references] == [("a_spy", ("SPY", "1d"))]

    req = GraphBacktestRequest(graph=graph, ticker="AAPL", start=START, end=END,
                               interval="1d", source="yahoo", initial_capital=10_000.0)
    response, cook = run_graph_backtest_cooked(
        req, frames={("AAPL", "1d"): AAPL, ("SPY", "1d"): MSFT})
    from indicators import OHLCVSeries, compute_instance

    # The RSI node's default type is Wilder.
    rsi = compute_instance("rsi", {"period": 14, "type": "wilder"}, OHLCVSeries(
        close=MSFT["Close"], high=MSFT["High"], low=MSFT["Low"], volume=MSFT["Volume"]))["rsi"]
    want = (rsi < 38).to_numpy(copy=True)
    want[0] = False
    got = np.asarray(cook.result.column("a_entry", "@lo_a_"), dtype=bool)
    assert got.tolist() == want.tolist()
    assert response.groups[0].trades, "the fixture should trade"
    assert {f[0] for f in cook.frames} == {"AAPL", "SPY"}


# ---------------------------------------------------------------------------
# Scoped cost constants and setting_shadowed
# ---------------------------------------------------------------------------


def test_cost_settings_are_scoped_and_the_nearest_wins():
    root = [_node("slip_root", "slippage", {"bps": 10.0}),
            _node("borrow_root", "borrow_rate", {"rate": 3.0})]
    inner = [_node("a_slip", "slippage", {"bps": 0.0}, "long_leg", name="slip")]
    prog = nb_compile(_pair(root=root, a_extra=inner))
    a, b = prog.groups
    assert a.costs == {"slippage_bps": 0.0, "borrow_rate_annual": 3.0}
    assert b.costs == {"slippage_bps": 10.0, "borrow_rate_annual": 3.0}
    assert a.settings == ("slip_root", "borrow_root", "a_slip") or \
        a.settings == ("borrow_root", "slip_root", "a_slip")


def test_size_terminal_over_a_settings_node_warns_setting_shadowed():
    inner = [_node("a_ps", "position_size", {"size": 0.5}, "long_leg", name="ps"),
             _node("a_size", "size", {"constant": 0.25}, "long_leg", name="size")]
    result = check_graph(_pair(a_extra=inner))
    assert result.program is not None
    [shadow] = [d for d in result.diagnostics if d.code == "setting_shadowed"]
    assert (shadow.node_id, shadow.severity) == ("a_ps", "warning")
    assert result.program.groups[0].plan.fields["position_size"] == 0.25


def test_settings_inside_one_group_do_not_reach_another():
    inner = [_node("b_ps", "position_size", {"size": 0.3}, "short_leg", name="ps")]
    a, b = nb_compile(_pair(b_extra=inner)).groups
    assert "position_size" not in a.plan.fields
    assert b.plan.fields["position_size"] == 0.3


# ---------------------------------------------------------------------------
# Backtest: one simulation per group
# ---------------------------------------------------------------------------


def _same_run(group_result, standalone) -> None:
    assert group_result.trades == standalone.trades
    assert group_result.equity_curve == standalone.equity_curve
    assert group_result.baseline_curve == standalone.baseline_curve
    assert group_result.summary == standalone.summary


def test_two_group_backtest_equals_two_standalone_backtests():
    resp, _cook = run_graph_backtest_cooked(_req(_pair()), keep_all=False, frames=FRAMES)
    a, b = resp.groups
    assert (a.name, a.symbol, a.direction, a.capital, a.weight) == \
           ("long_leg", "AAPL", "long", 5000.0, 1.0)
    assert (b.name, b.symbol, b.direction, b.capital) == ("short_leg", "MSFT", "short", 5000.0)
    alone_a = _standalone("a_", "AAPL", "long", 5000.0, 38, 62, AAPL)
    alone_b = _standalone("b_", "MSFT", "short", 5000.0, 60, 40, MSFT)
    _same_run(a, alone_a)
    _same_run(b, alone_b)
    assert alone_a.summary["num_trades"] > 0 and alone_b.summary["num_trades"] > 0
    assert any(t["type"] == "short" for t in b.trades)
    assert a.path == "/long_leg" and a.node_id == "long_leg"


def test_capital_is_split_by_weight():
    resp, _ = run_graph_backtest_cooked(_req(_pair(weight_a=3, weight_b=1)),
                                        keep_all=False, frames=FRAMES)
    a, b = resp.groups
    assert (a.capital, b.capital) == (7500.0, 2500.0)
    _same_run(a, _standalone("a_", "AAPL", "long", 7500.0, 38, 62, AAPL))
    _same_run(b, _standalone("b_", "MSFT", "short", 2500.0, 60, 40, MSFT))
    assert resp.combined.summary["initial_capital"] == 10000.0


def test_group_costs_reach_its_simulation():
    root = [_node("slip_root", "slippage", {"bps": 25.0})]
    inner = [_node("a_slip", "slippage", {"bps": 0.0}, "long_leg", name="slip")]
    resp, _ = run_graph_backtest_cooked(_req(_pair(root=root, a_extra=inner)),
                                        keep_all=False, frames=FRAMES)
    a, b = resp.groups
    _same_run(a, _standalone("a_", "AAPL", "long", 5000.0, 38, 62, AAPL, slippage_bps=0.0))
    _same_run(b, _standalone("b_", "MSFT", "short", 5000.0, 60, 40, MSFT, slippage_bps=25.0))


def test_combined_is_the_sum_of_the_groups():
    resp, _ = run_graph_backtest_cooked(_req(_pair()), keep_all=False, frames=FRAMES)
    a, b = resp.groups
    combined = resp.combined
    assert len(combined.equity_curve) == len(a.equity_curve) == len(b.equity_curve)
    for pa, pb, pc in zip(a.equity_curve, b.equity_curve, combined.equity_curve):
        assert pa["time"] == pb["time"] == pc["time"]
        assert pc["value"] == pytest.approx(pa["value"] + pb["value"], abs=0.011)
    s = combined.summary
    assert s["final_value"] == pytest.approx(a.summary["final_value"] + b.summary["final_value"],
                                             abs=0.011)
    assert s["num_trades"] == a.summary["num_trades"] + b.summary["num_trades"]
    assert 0 < s["exposure_pct"] <= 100 and 0 < s["gross_deployed_pct"] <= 100
    # Several groups: the legacy fields carry the combined result, no trades.
    assert resp.summary == s and resp.trades == []
    assert resp.equity_curve == combined.equity_curve
    assert resp.baseline_curve == combined.baseline_curve


def test_groups_on_one_frame_share_one_cook():
    """Two groups on the same symbol: one cook, every node kept."""
    a_nodes, a_wires = _leg("a_", "AAPL", "g1")
    b_nodes, b_wires = _leg("b_", "AAPL", "g2", lo=30, hi=70)
    graph = _graph([_group("g1", "long"), _group("g2", "short"), *a_nodes, *b_nodes],
                   a_wires + b_wires)
    resp, cook = run_graph_backtest_cooked(_req(graph), df=AAPL)
    assert [g.symbol for g in resp.groups] == ["AAPL", "AAPL"]
    assert cook.program.steps == nb_compile(graph).steps
    assert [(s, i) for s, i, _df in cook.frames] == [("AAPL", "1d")]
    _same_run(resp.groups[1], _standalone("b_", "AAPL", "short", 5000.0, 30, 70, AAPL))


def test_groups_on_two_frames_cook_their_own_nodes(monkeypatch):
    calls = []
    monkeypatch.setattr(shared, "_fetch", lambda sym, *a, **k: calls.append(sym) or FRAMES[(sym, "1d")])
    resp, cook = run_graph_backtest_cooked(_req(_pair()), keep_all=True)
    assert calls == ["AAPL", "MSFT"]
    assert {s.node_id for s in cook.program.steps} == set(nb_compile(_pair()).groups[0].step_ids)
    assert [(s, i) for s, i, _df in cook.frames] == [("AAPL", "1d")]
    assert len(resp.groups) == 2


# ---------------------------------------------------------------------------
# One group: the legacy fields are today's response
# ---------------------------------------------------------------------------


def _wave4_backtest(req: GraphBacktestRequest, df: pd.DataFrame) -> dict:
    """The Wave 4 run.py algorithm for a graph with no settings nodes: one
    cook, the Entry/Exit columns, the request's fields, one simulation."""
    from nodebuilder import evaluator
    from nodebuilder.prepare import NO_EXIT_ATTR, build_graph_attrs
    from nodebuilder.run import (
        _build_baseline_curve,
        _open_position,
        _settings_to_strategy_request,
        cook_attrs,
    )
    from routes.backtest import _run_simulation
    from shared import _format_time_index

    program = nb_compile(req.graph)
    settings = sb.request_fields(req)
    attrs = build_graph_attrs(program, df, settings.get("trailing_stop"))
    result = cook_attrs(program, attrs)
    entry, exit_ = evaluator.signal_columns(program, result)
    direction = settings["direction"]
    date_strs = _format_time_index(df.index, req.interval)
    sim = _run_simulation(
        df=df, indicators=attrs,
        buy_signal_fn=lambda i, _r: (bool(entry[i]), [], direction),
        sell_signal_fn=lambda i, _p, _r: (bool(exit_[i]), []),
        req=_settings_to_strategy_request(settings, req), b23_mode=False,
        regime_active_series=None, on_flip="hold", date_strs=date_strs,
    )
    summary = dict(sim["summary"])
    summary["open_position"] = _open_position(sim["trades"], float(df["Close"].iloc[-1]))
    summary["exit_connected"] = program.exit_attr != NO_EXIT_ATTR
    return {"summary": summary, "trades": sim["trades"], "equity_curve": sim["equity_curve"],
            "baseline_curve": _build_baseline_curve(df, req.initial_capital, date_strs)}


@pytest.mark.parametrize("direction", ["long", "short"])
def test_one_group_legacy_fields_equal_todays_response(direction):
    nodes, wires = _leg("", "AAPL")
    req = _req(_graph(nodes, wires), direction=direction, slippage_bps=3.0,
               stop_loss_pct=4.0, max_bars_held=12)
    old = _wave4_backtest(req, AAPL)
    resp = run_graph_backtest(req, df=AAPL)
    assert set(resp.model_dump()) == {"summary", "trades", "equity_curve", "baseline_curve"}
    assert resp.trades == old["trades"] and resp.trades
    assert resp.equity_curve == old["equity_curve"]
    assert resp.baseline_curve == old["baseline_curve"]
    assert resp.summary == old["summary"]

    grouped, _ = run_graph_backtest_cooked(req, df=AAPL)
    [g] = grouped.groups
    assert (g.name, g.node_id, g.path, g.symbol, g.direction, g.capital) == \
           ("main", None, "/", "AAPL", direction, 10000.0)
    assert g.summary == grouped.summary and g.trades == grouped.trades
    assert g.equity_curve == grouped.equity_curve
    # One group: the combined equity is the group's.
    assert grouped.combined.equity_curve == grouped.equity_curve
    assert grouped.combined.summary["final_value"] == grouped.summary["final_value"]
    assert grouped.combined.summary["win_rate_pct"] == grouped.summary["win_rate_pct"]
    assert grouped.combined.summary["max_drawdown_pct"] == grouped.summary["max_drawdown_pct"]
    assert grouped.combined.summary["sharpe_ratio"] == grouped.summary["sharpe_ratio"]


def test_one_explicit_group_equals_the_implicit_graph():
    nodes, wires = _leg("a_", "AAPL")
    implicit = run_graph_backtest(_req(_graph(nodes, wires)), df=AAPL)
    g_nodes, g_wires = _leg("a_", "AAPL", "long_leg")
    explicit = run_graph_backtest(_req(_graph([_group("long_leg", "long", 2.0), *g_nodes],
                                              g_wires)), df=AAPL)
    assert explicit.model_dump() == implicit.model_dump()


def test_explicit_group_direction_wins_over_the_request():
    g_nodes, g_wires = _leg("a_", "AAPL", "leg")
    graph = _graph([_group("leg", "short"), *g_nodes], g_wires)
    resp, _ = run_graph_backtest_cooked(_req(graph, direction="long"), df=AAPL)
    assert resp.groups[0].direction == "short"
    assert {t["type"] for t in resp.trades} <= {"short", "cover"} and resp.trades


def test_explicit_group_symbol_wins_over_the_request(monkeypatch):
    monkeypatch.setattr(shared, "_fetch", lambda sym, *a, **k: FRAMES[(sym, "1d")])
    g_nodes, g_wires = _leg("b_", "MSFT", "leg")
    graph = _graph([_group("leg", "long"), *g_nodes], g_wires)
    resp, _ = run_graph_backtest_cooked(_req(graph, ticker="AAPL"))
    assert resp.groups[0].symbol == "MSFT"
    alone = _standalone("b_", "MSFT", "long", 10000.0, 38, 62, MSFT)
    assert resp.trades == alone.trades and resp.summary == alone.summary


# ---------------------------------------------------------------------------
# Combined metrics on a hand-computed fixture
# ---------------------------------------------------------------------------


def _days(*names):
    return pd.DatetimeIndex([pd.Timestamp(d) for d in names])


def test_position_track_holds_from_entry_to_the_bar_before_exit():
    keys = ["d0", "d1", "d2", "d3", "d4"]
    close = np.array([10.0, 10.0, 20.0, 20.0, 30.0])
    trades = [{"type": "buy", "date": "d1", "shares": 10},
              {"type": "sell", "date": "d3", "shares": 10},
              {"type": "short", "date": "d3", "shares": 2}]
    in_pos, notional = ng.position_track(trades, keys, close)
    assert in_pos.tolist() == [False, True, True, True, True]
    assert notional.tolist() == [0.0, 100.0, 200.0, 40.0, 60.0]


def test_combined_exposure_and_gross_deployed_hand_computed():
    """Leg A trades d0..d3 (long 10 shares held d1, d2); leg B trades
    d2..d5 (short 5 shares from d4, still open).  Union: d0..d5.

    any position : d1 d2 d4 d5            -> 4 / 6 = 66.67 %
    equity       : 2000 2000 2100 2100 2100 2150 (B is 1000 before d2)
    notional     : 0 100 200 0 200 200
    deployed     : 0, .05, .0952381, 0, .0952381, .0930233 -> mean 5.56 %
    """
    d = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    a_close = np.array([10.0, 10.0, 20.0, 20.0])
    a_trades = [{"type": "buy", "date": d[1], "shares": 10, "pnl": 0},
                {"type": "sell", "date": d[3], "shares": 10, "pnl": 100.0}]
    a_pos, a_not = ng.position_track(a_trades, d[:4], a_close)
    leg_a = ng.LegTrack(times=_days(*d[:4]), keys=d[:4],
                        equity=np.array([1000.0, 1000.0, 1100.0, 1100.0]),
                        baseline=np.array([1000.0, 1000.0, 2000.0, 2000.0]),
                        in_pos=a_pos, notional=a_not, capital=1000.0,
                        final_value=1100.0, num_trades=1, trades=a_trades)
    b_close = np.array([50.0, 50.0, 40.0, 40.0])
    b_trades = [{"type": "short", "date": d[4], "shares": 5}]
    b_pos, b_not = ng.position_track(b_trades, d[2:], b_close)
    leg_b = ng.LegTrack(times=_days(*d[2:]), keys=d[2:],
                        equity=np.array([1000.0, 1000.0, 1000.0, 1050.0]),
                        baseline=np.array([1000.0, 1000.0, 800.0, 800.0]),
                        in_pos=b_pos, notional=b_not, capital=1000.0,
                        final_value=1050.0, num_trades=0, trades=b_trades)

    out = ng.combine([leg_a, leg_b], 2000.0)
    s = out["summary"]
    assert [p["time"] for p in out["equity_curve"]] == d
    assert [p["value"] for p in out["equity_curve"]] == [2000, 2000, 2100, 2100, 2100, 2150]
    assert [p["value"] for p in out["baseline_curve"]] == [2000, 2000, 3000, 3000, 2800, 2800]
    assert s["exposure_pct"] == 66.67
    expected = np.mean([0, 100 / 2000, 200 / 2100, 0, 200 / 2100, 200 / 2150]) * 100
    assert s["gross_deployed_pct"] == round(expected, 2) == 5.56
    assert s["final_value"] == 2150.0 and s["total_return_pct"] == 7.5
    assert s["num_trades"] == 1 and s["win_rate_pct"] == 100.0
    assert s["buy_hold_return_pct"] == 40.0
    assert s["max_drawdown_pct"] == 0.0


def test_combined_drawdown_uses_the_summed_curve():
    d = ["2024-01-01", "2024-01-02", "2024-01-03"]
    flat = np.zeros(3, dtype=bool)
    legs = [ng.LegTrack(times=_days(*d), keys=d, equity=np.array(eq), baseline=np.array(eq),
                        in_pos=flat, notional=np.zeros(3), capital=eq[0], final_value=eq[-1],
                        num_trades=0)
            for eq in ([100.0, 120.0, 90.0], [100.0, 80.0, 110.0])]
    s = ng.combine(legs, 200.0)["summary"]
    # Summed: 200, 200, 200 -> no drawdown, though each leg has one.
    assert s["max_drawdown_pct"] == 0.0 and s["exposure_pct"] == 0.0
    assert s["gross_deployed_pct"] == 0.0


def test_combined_on_intraday_keys_uses_unix_seconds():
    idx = pd.DatetimeIndex(["2024-01-02 14:30", "2024-01-02 14:35"])
    keys = [int(t.timestamp()) for t in idx.tz_localize("UTC")]
    leg = ng.LegTrack(times=idx, keys=keys, equity=np.array([100.0, 101.0]),
                      baseline=np.array([100.0, 100.0]), in_pos=np.array([True, True]),
                      notional=np.array([50.0, 50.0]), capital=100.0, final_value=101.0,
                      num_trades=0)
    out = ng.combine([leg], 100.0)
    assert [p["time"] for p in out["equity_curve"]] == keys
    assert out["summary"]["exposure_pct"] == 100.0


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


def test_route_returns_groups_combined_and_cook_id(monkeypatch):
    from routes.nodebuilder import router

    monkeypatch.setattr(shared, "_fetch", lambda sym, *a, **k: FRAMES[(sym, "1d")])
    app = FastAPI()
    app.include_router(router)
    body = _req(_pair()).model_dump(mode="json", by_alias=True)
    res = TestClient(app).post("/api/nodebuilder/backtest", json=body)
    assert res.status_code == 200, res.text
    out = res.json()
    assert set(out) == {"summary", "trades", "equity_curve", "baseline_curve", "groups",
                        "combined", "cook_id"}
    assert [g["name"] for g in out["groups"]] == ["long_leg", "short_leg"]
    g = out["groups"][0]
    assert set(g) >= {"name", "node_id", "path", "symbol", "interval", "direction", "weight",
                      "capital", "summary", "trades", "equity_curve"}
    assert {"open_position", "exit_connected"} <= set(g["summary"])
    assert {"exposure_pct", "gross_deployed_pct", "initial_capital", "final_value",
            "total_return_pct", "max_drawdown_pct", "sharpe_ratio", "num_trades"} \
        <= set(out["combined"]["summary"])
    assert out["cook_id"]


def test_route_reports_a_group_error_as_400():
    from routes.nodebuilder import router

    app = FastAPI()
    app.include_router(router)
    body = _req(_pair(root=[_node("stray", "time_stop", {"max_bars": 3})])).model_dump(
        mode="json", by_alias=True)
    res = TestClient(app).post("/api/nodebuilder/backtest", json=body)
    assert res.status_code == 400
    assert res.json()["code"] == "group_terminal_outside"


def test_unknown_group_name_is_a_key_error():
    prog = nb_compile(_pair())
    with pytest.raises(KeyError):
        ng.group_named(prog, "nope")
    with pytest.raises(GraphValidationError):
        nb_compile(_pair(root=[_node("stray", "entry")]))

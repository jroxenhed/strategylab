"""W5 item 5.A (critic 26): a graph's terminals trade exactly as the rule
backtest's matching settings.

  - trailing_stop terminal (with activate_on_profit / activate_pct) versus
    StrategyRequest.trailing_stop;
  - time_stop terminal versus max_bars_held;
  - size and stop terminals (constant, wired point, wired detail) versus
    position_size and stop_loss_pct;
  - the borrow_rate settings node versus borrow_rate_annual;
  - a regime terminal on a long group, and a regime_switch group, versus the
    rule backtest's regime mode.

Each test runs the rule backtest (routes.backtest.run_backtest) and the
graph through the simulator bridge (sim_bridge.run_group) on the same
pickled frame.  The rule regime normally comes from a higher-timeframe
fetch; here both sides get the same regime column (the graph's), so the
tests check the simulator wiring and not the regime indicator (W5 5.C
covers that).  No network, no bot, no order.
"""
from __future__ import annotations

import os
import pickle
import sys

import pandas as pd
import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import routes.backtest as backtest_mod  # noqa: E402
from models import RegimeConfig, Rule, StrategyRequest, TrailingStopConfig  # noqa: E402
from nodebuilder import trading as _trading  # noqa: E402,F401  (registers node types)
from nodebuilder.compile import _unknown_error  # noqa: E402
from nodebuilder.compile import compile as nb_compile  # noqa: E402
from nodebuilder.evaluator import NO_EXIT_ATTR, CompiledProgram  # noqa: E402
from nodebuilder.from_rules import auto_render  # noqa: E402
from nodebuilder.kernel import schema as kschema  # noqa: E402
from nodebuilder.kernel.evaluate import build_steps  # noqa: E402
from nodebuilder.models import Graph  # noqa: E402
from nodebuilder.trading import sim_bridge as sb  # noqa: E402
from routes.backtest import run_backtest  # noqa: E402
from tests.nodebuilder._snapshot_helpers import assert_equal_within_tolerance  # noqa: E402

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "run_backtest_snapshots")

# The same tolerance as the parity trio: the two paths compute their
# indicators with different code.
_REL = 1e-4

_AAPL = dict(ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d", source="yahoo")
_SPY = dict(ticker="SPY", start="2021-01-01", end="2024-01-01", interval="1d", source="yahoo")

_RSI_LONG = dict(buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                 sell_rules=[Rule(indicator="rsi", condition="above", value=70)])
_RSI_SHORT = dict(direction="short",
                  buy_rules=[Rule(indicator="rsi", condition="above", value=70)],
                  sell_rules=[Rule(indicator="rsi", condition="below", value=30)])
_MACD = dict(buy_rules=[Rule(indicator="macd", condition="crosses_above", param="signal")],
             sell_rules=[Rule(indicator="macd", condition="crosses_below", param="signal")])

# Which pickled frame each base strategy runs on.
_AAPL_DF = "simple_long_rsi"
_SPY_DF = "macd_crossover"


def _load_df(name: str) -> pd.DataFrame:
    with open(os.path.join(_FIXTURES, f"{name}_df.pkl"), "rb") as fh:
        return pickle.load(fh)


# ---------------------------------------------------------------------------
# Building and running the two sides
# ---------------------------------------------------------------------------


def _add(d: dict, nodes: dict, wires=()) -> dict:
    for nid, (ntype, params) in nodes.items():
        d["nodes"][nid] = {"id": nid, "type": ntype, "params": dict(params), "position": [0.0, 0.0]}
    for a, b in wires:
        d["wires"].append({"id": f"w_{a.strip('/').replace('/', '_')}_{b.strip('/')}",
                           "from": a, "to": b, "from_port": "out", "to_port": "in0"})
    return d


def _graph_dict(req: StrategyRequest) -> dict:
    d = auto_render(req).model_dump(by_alias=True)
    d["readOnly"] = False
    return d


def _graph(req: StrategyRequest, nodes: dict, wires=()) -> Graph:
    """auto_render(req) plus the given terminals and nodes."""
    return Graph.model_validate(_add(_graph_dict(req), nodes, wires))


def _ticker(d: dict) -> str:
    return next(nid for nid, n in d["nodes"].items() if n["type"] == "ticker")


def _rule(req: StrategyRequest, df: pd.DataFrame) -> dict:
    return run_backtest(req, include_spy_correlation=False, df=df)


def _graph_side(program, df: pd.DataFrame, req: StrategyRequest, plan=None) -> dict:
    """The bridge's run for *req*'s window and simulator fields."""
    fields = {**sb.request_fields(req), "ticker": req.ticker, "start": req.start, "end": req.end,
              "interval": req.interval, "source": req.source}
    run = sb.run_group(program, df, fields, plan=plan)
    return {"summary": run.sim["summary"], "trades": run.sim["trades"],
            "equity_curve": run.sim["equity_curve"], "run": run}


# Trade "rules" labels the simulator writes itself.  Any other label is a
# rule description, which only the rule path has (graph trades carry none).
_SIM_LABELS = (["stop loss"], ["trailing stop"], ["time stop"], ["regime flip"],
               ["regime flip reverse"])


def _label(trade: dict):
    rules = trade.get("rules")
    return rules if rules in _SIM_LABELS else "signal"


def _same(rule: dict, graph: dict, name: str) -> None:
    gs = graph["summary"]
    for key, value in gs.items():
        assert_equal_within_tolerance(value, rule["summary"][key], rel=_REL, path=f"{name}.{key}")
    rt, gt = rule["trades"], graph["trades"]
    assert len(rt) == len(gt), f"{name}: rule {len(rt)} trades, graph {len(gt)}"
    for i, (r, g) in enumerate(zip(rt, gt)):
        for key in ("type", "date", "direction", "stop_loss", "trailing_stop", "exit_reason"):
            assert r.get(key) == g.get(key), f"{name}: trade[{i}].{key}: {r.get(key)} vs {g.get(key)}"
        assert _label(r) == _label(g), f"{name}: trade[{i}] exit kind: {_label(r)} vs {_label(g)}"
        for key in ("price", "shares", "pnl", "borrow_cost", "commission", "slippage"):
            if r.get(key) is not None:
                assert_equal_within_tolerance(g[key], r[key], rel=_REL, path=f"{name}.trade[{i}].{key}")
    assert len(rule["equity_curve"]) == len(graph["equity_curve"])
    for i, (r, g) in enumerate(zip(rule["equity_curve"], graph["equity_curve"])):
        assert_equal_within_tolerance(g["value"], r["value"], rel=_REL, path=f"{name}.equity[{i}]")


def _exits(result: dict, reason_key: str) -> int:
    return sum(1 for t in result["trades"] if t.get(reason_key))


# ---------------------------------------------------------------------------
# Trailing stop terminal versus the rule trailing stop
# ---------------------------------------------------------------------------

_TRAILS = [
    ("pct_close_activate", _SPY, _MACD, _SPY_DF,
     TrailingStopConfig(type="pct", value=2.0, source="close", activate_on_profit=True, activate_pct=1.5)),
    ("pct_high_activate", _SPY, _MACD, _SPY_DF,
     TrailingStopConfig(type="pct", value=1.5, source="high", activate_on_profit=True, activate_pct=0.5)),
    ("atr_activate", _SPY, _MACD, _SPY_DF,
     TrailingStopConfig(type="atr", value=1.5, source="high", activate_on_profit=True, activate_pct=1.0)),
    ("short_pct_activate", _AAPL, _RSI_SHORT, _AAPL_DF,
     TrailingStopConfig(type="pct", value=3.0, source="high", activate_on_profit=True, activate_pct=2.0)),
]


@pytest.mark.parametrize("name,window,rules,df_name,trail", _TRAILS, ids=[t[0] for t in _TRAILS])
def test_trailing_stop_terminal_matches_rule_trailing_stop(name, window, rules, df_name, trail):
    df = _load_df(df_name)
    base = StrategyRequest(**window, **rules)
    rule = _rule(base.model_copy(update={"trailing_stop": trail}), df)
    graph = _graph(base, {"/trail": ("trailing_stop", trail.model_dump())})
    got = _graph_side(nb_compile(graph), df, base)
    _same(rule, got, name)
    assert _exits(rule, "trailing_stop") > 0, f"{name}: the fixture must hit the trailing stop"


def test_trailing_stop_rendered_by_from_rules_matches():
    df = _load_df(_SPY_DF)
    trail = TrailingStopConfig(type="pct", value=2.0, source="close", activate_on_profit=True,
                               activate_pct=1.5)
    req = StrategyRequest(**_SPY, **_MACD, trailing_stop=trail)
    program = nb_compile(auto_render(req))
    # The request carries no trailing stop: the rendered terminal sets it.
    _same(_rule(req, df), _graph_side(program, df, req.model_copy(update={"trailing_stop": None})),
          "from_rules")


# ---------------------------------------------------------------------------
# Time stop terminal versus max_bars_held
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("window,rules,df_name,bars", [
    (_SPY, _MACD, _SPY_DF, 5),
    (_AAPL, _RSI_LONG, _AAPL_DF, 10),
    (_AAPL, _RSI_SHORT, _AAPL_DF, 7),
])
def test_time_stop_terminal_matches_max_bars_held(window, rules, df_name, bars):
    df = _load_df(df_name)
    base = StrategyRequest(**window, **rules)
    rule = _rule(base.model_copy(update={"max_bars_held": bars}), df)
    got = _graph_side(nb_compile(_graph(base, {"/time": ("time_stop", {"max_bars": bars})})), df, base)
    _same(rule, got, f"time_stop_{bars}")
    assert any(t.get("rules") == ["time stop"] for t in rule["trades"])


# ---------------------------------------------------------------------------
# Size and stop terminals versus position_size and stop_loss_pct
# ---------------------------------------------------------------------------


def _value_nodes(kind: str, value: float, mode: str) -> tuple[dict, tuple]:
    """A size or stop terminal holding *value*: as its constant, or wired
    from a constant node (a point column, or one detail value)."""
    term = f"/{kind}_t"
    if mode == "constant":
        return {term: (kind, {"constant": value})}, ()
    as_detail = mode == "detail"
    return ({f"/{kind}_src": ("constant", {"value": value, "as_detail": as_detail,
                                           "out": f"@{kind}_in"}),
             term: (kind, {})},
            ((f"/{kind}_src", term),))


@pytest.mark.parametrize("mode", ["constant", "point", "detail"])
@pytest.mark.parametrize("window,rules,df_name", [(_SPY, _MACD, _SPY_DF), (_AAPL, _RSI_SHORT, _AAPL_DF)],
                         ids=["macd_long", "rsi_short"])
def test_size_terminal_matches_position_size(mode, window, rules, df_name):
    df = _load_df(df_name)
    base = StrategyRequest(**window, **rules)
    nodes, wires = _value_nodes("size", 0.4, mode)
    program = nb_compile(_graph(base, nodes, wires))
    plan = sb.plan_group(program, direction=base.direction)
    assert (plan.size is None) == (mode == "constant")
    rule = _rule(base.model_copy(update={"position_size": 0.4}), df)
    _same(rule, _graph_side(program, df, base, plan), f"size_{mode}")


@pytest.mark.parametrize("mode", ["constant", "point", "detail"])
@pytest.mark.parametrize("window,rules,df_name,pct", [
    (_SPY, _MACD, _SPY_DF, 1.5),
    (_AAPL, _RSI_LONG, _AAPL_DF, 3.0),
    (_AAPL, _RSI_SHORT, _AAPL_DF, 2.0),
], ids=["macd_long", "rsi_long", "rsi_short"])
def test_stop_terminal_matches_stop_loss_pct(mode, window, rules, df_name, pct):
    df = _load_df(df_name)
    base = StrategyRequest(**window, **rules)
    nodes, wires = _value_nodes("stop", pct, mode)
    program = nb_compile(_graph(base, nodes, wires))
    rule = _rule(base.model_copy(update={"stop_loss_pct": pct}), df)
    _same(rule, _graph_side(program, df, base), f"stop_{mode}")
    assert _exits(rule, "stop_loss") > 0, "the fixture must hit the stop"


def test_stop_terminal_wins_over_stop_loss_setting():
    df = _load_df(_SPY_DF)
    # The rendered graph has a 9% stop_loss settings node; the terminal's
    # 1.5% wins (plan D7), as if the rule asked for 1.5%.
    base = StrategyRequest(**_SPY, **_MACD, stop_loss_pct=9.0)
    program = nb_compile(_graph(base, {"/stop_t": ("stop", {"constant": 1.5})}))
    rule = _rule(base.model_copy(update={"stop_loss_pct": 1.5}), df)
    _same(rule, _graph_side(program, df, base), "stop_shadows_setting")


def test_all_terminals_and_borrow_rate_together_on_a_short():
    df = _load_df(_AAPL_DF)
    trail = TrailingStopConfig(type="pct", value=3.0, source="close", activate_on_profit=True,
                               activate_pct=1.0)
    base = StrategyRequest(**_AAPL, **_RSI_SHORT, per_share_rate=0.0035, min_per_order=0.35,
                           slippage_bps=5.0)
    rule_req = base.model_copy(update={
        "position_size": 0.6, "stop_loss_pct": 4.0, "trailing_stop": trail, "max_bars_held": 12,
        "borrow_rate_annual": 3.0,
    })
    size_nodes, size_wires = _value_nodes("size", 0.6, "point")
    stop_nodes, stop_wires = _value_nodes("stop", 4.0, "detail")
    graph = _graph(base, {
        **size_nodes, **stop_nodes,
        "/trail": ("trailing_stop", trail.model_dump()),
        "/time": ("time_stop", {"max_bars": 12}),
        "/borrow": ("borrow_rate", {"rate": 3.0}),
    }, size_wires + stop_wires)
    rule = _rule(rule_req, df)
    _same(rule, _graph_side(nb_compile(graph), df, base), "combined_short")
    assert sum(t.get("borrow_cost", 0) for t in rule["trades"]) > 0


# ---------------------------------------------------------------------------
# Regime: a long group with a regime terminal, and a regime_switch group,
# versus the rule backtest's regime mode
# ---------------------------------------------------------------------------


def _regime_nodes(d: dict, *, period: int = 50) -> dict:
    """Add close > SMA(period) as @uptrend and a regime terminal reading it."""
    tick = _ticker(d)
    return _add(d, {"/reg_sma": ("sma", {"period": period}),
                    "/reg_up": ("above", {"a": "@close", "b": "@sma", "out": "@uptrend"}),
                    "/reg_t": ("regime", {})},
                ((tick, "/reg_sma"), ("/reg_sma", "/reg_up"), ("/reg_up", "/reg_t")))


def _rule_with_regime(monkeypatch, req: StrategyRequest, df, regime: pd.Series) -> dict:
    # The rule regime normally comes from a higher-timeframe fetch.  Hand it
    # the graph's regime column instead.
    monkeypatch.setattr(backtest_mod, "_compute_regime_series",
                        lambda _req, _df: regime.astype(bool))
    return _rule(req, df)


def _regime_column(program, df, plan) -> pd.Series:
    from nodebuilder.prepare import build_graph_attrs

    attrs = build_graph_attrs(program, df)
    result = sb.cook(program, attrs, plan.keep_ids())
    return pd.Series(result.column(plan.regime.node_id, plan.regime.attr).astype(bool), index=df.index)


def _regime_req(window, long_rules, short_rules, on_flip) -> StrategyRequest:
    return StrategyRequest(
        **window, buy_rules=[], sell_rules=[],
        long_buy_rules=long_rules["buy_rules"], long_sell_rules=long_rules["sell_rules"],
        short_buy_rules=short_rules["buy_rules"] if short_rules else [],
        short_sell_rules=short_rules["sell_rules"] if short_rules else [],
        regime=RegimeConfig(enabled=True, on_flip=on_flip,
                            rules=[Rule(indicator="rsi", condition="above", value=50)]),
    )


@pytest.mark.parametrize("on_flip", ["hold", "close_only", "close_and_reverse"])
@pytest.mark.parametrize("window,rules,df_name", [(_SPY, _MACD, _SPY_DF), (_AAPL, _RSI_LONG, _AAPL_DF)],
                         ids=["macd", "rsi"])
def test_regime_terminal_on_a_long_group_matches_rule_regime(monkeypatch, on_flip, window, rules,
                                                             df_name):
    """A long group gated by a regime trades as a rule regime strategy whose
    long side has the group's rules.  Its short side is empty (hold,
    close_only: no entries off-regime) or the same rules (close_and_reverse:
    the side follows the regime)."""
    df = _load_df(df_name)
    d = _regime_nodes(_graph_dict(StrategyRequest(**window, **rules)))
    d["nodes"]["/reg_t"]["params"]["on_flip"] = on_flip
    program = nb_compile(Graph.model_validate(d))
    plan = sb.plan_group(program, direction="long")
    assert plan.regime is not None and plan.on_flip == on_flip
    regime = _regime_column(program, df, plan)
    assert 2 <= int(regime.astype(int).diff().abs().sum()), "the regime must flip"

    short_side = rules if on_flip == "close_and_reverse" else None
    rule = _rule_with_regime(monkeypatch, _regime_req(window, rules, short_side, on_flip), df, regime)
    got = _graph_side(program, df, StrategyRequest(**window, **rules), plan)
    _same(rule, got, f"regime_long_{on_flip}")
    if on_flip != "hold":
        assert any(t.get("exit_reason") == "regime_flip" for t in rule["trades"])


def _program_without_whole_graph_terminal_rules(graph: Graph) -> CompiledProgram:
    """The compiled steps without compile.py's one-Entry rule.

    A regime_switch group has an entry and an exit per side.  Until Output
    Groups (W5 5.B) move compile's terminal rules into each group, compile
    refuses a second Entry, so this builds the program from the kernel
    analysis the same way compile does.
    """
    analysis = kschema.analyze(graph, unknown_error=_unknown_error)
    assert not analysis.has_errors(), [d for d, e in analysis.found if e is not None]
    return CompiledProgram(steps=build_steps(analysis), entry_attr="@unused",
                           exit_attr=NO_EXIT_ATTR,
                           required_lookback_bars=analysis.required_lookback_bars())


def _switch_graph(window, long_rules, short_rules, on_flip) -> Graph:
    """Two rendered rule graphs (long side, short side) in one graph, with
    their entry and exit terminals marked by side, plus the regime."""
    d_long = _graph_dict(StrategyRequest(**window, **long_rules))
    d_short = _graph_dict(StrategyRequest(**window, **short_rules, direction="short"))
    merged = {**d_long, "nodes": {}, "wires": []}
    for prefix, d, side in (("/lng", d_long, "long"), ("/sht", d_short, "short")):
        for nid, node in d["nodes"].items():
            if prefix == "/sht" and node["type"] in sb.SETTING_TYPES:
                continue  # one set of settings nodes is enough
            new = f"{prefix}{nid}"
            node = {**node, "id": new, "name": new.strip("/").replace("/", "_")}
            if node["type"] in ("entry", "exit"):
                node["params"] = {**node["params"], "side": side}
            merged["nodes"][new] = node
        for w in d["wires"]:
            if f"{prefix}{w['to']}" in merged["nodes"]:
                merged["wires"].append({**w, "id": f"{prefix.strip('/')}_{w['id']}",
                                        "from": f"{prefix}{w['from']}", "to": f"{prefix}{w['to']}"})
    merged = _regime_nodes(merged)
    merged["nodes"]["/reg_t"]["params"]["on_flip"] = on_flip
    return Graph.model_validate(merged)


@pytest.mark.parametrize("on_flip", ["hold", "close_only", "close_and_reverse"])
@pytest.mark.parametrize("window,long_rules,short_rules,df_name", [
    (_AAPL,
     dict(buy_rules=[Rule(indicator="rsi", condition="below", value=35)],
          sell_rules=[Rule(indicator="rsi", condition="above", value=65)]),
     dict(buy_rules=[Rule(indicator="rsi", condition="above", value=65)],
          sell_rules=[Rule(indicator="rsi", condition="below", value=35)]),
     "regime_mode"),
    (_SPY,
     dict(buy_rules=[Rule(indicator="rsi", condition="below", value=40)],
          sell_rules=[Rule(indicator="rsi", condition="above", value=60)]),
     dict(buy_rules=[Rule(indicator="rsi", condition="above", value=65)],
          sell_rules=[Rule(indicator="rsi", condition="below", value=45)]),
     "per_direction_b23"),
], ids=["aapl", "spy"])
def test_regime_switch_group_matches_rule_regime(monkeypatch, on_flip, window, long_rules,
                                                 short_rules, df_name):
    df = _load_df(df_name)
    program = _program_without_whole_graph_terminal_rules(
        _switch_graph(window, long_rules, short_rules, on_flip))
    plan = sb.plan_group(program, direction="regime_switch")
    assert plan.b23_mode and plan.entry_long and plan.entry_short
    regime = _regime_column(program, df, plan)
    rule = _rule_with_regime(monkeypatch, _regime_req(window, long_rules, short_rules, on_flip),
                             df, regime)
    got = _graph_side(program, df, StrategyRequest(**window, **long_rules), plan)
    _same(rule, got, f"switch_{on_flip}")
    assert {t["direction"] for t in rule["trades"]} == {"long", "short"}


# ---------------------------------------------------------------------------
# No regression: the bridge gives the Wave 4 graph backtest, exactly
# ---------------------------------------------------------------------------


def _wave4_strategies():
    from tests.nodebuilder.test_backtest_parity import _REGIME_NAMES, _STRATEGIES

    return [(n, r) for n, r in _STRATEGIES if n not in _REGIME_NAMES]


@pytest.mark.parametrize("name,req", _wave4_strategies(), ids=[n for n, _r in _wave4_strategies()])
def test_run_group_equals_wave4_graph_backtest(name, req):
    """A graph with no size, stop, time stop or regime terminal runs through
    the bridge exactly as run_graph_backtest ran it in Wave 4 (same trades,
    same equity, same summary, to the last digit).  5.B moves run.py onto the
    bridge; this pins that the move cannot change a result."""
    from nodebuilder.api_models import GraphBacktestRequest
    from nodebuilder.run import GRAPH_ONLY_SUMMARY_KEYS, run_graph_backtest

    df = _load_df(name)
    graph = auto_render(req)
    fields = {k: getattr(req, k) for k in sb.SIM_FIELDS if k in GraphBacktestRequest.model_fields}
    greq = GraphBacktestRequest(graph=graph, ticker=req.ticker, start=req.start, end=req.end,
                                interval=req.interval, source=req.source, **fields)
    old = run_graph_backtest(greq, df=df)
    run = sb.run_group(nb_compile(graph), df, greq)
    assert run.sim["trades"] == old.trades
    assert run.sim["equity_curve"] == old.equity_curve
    summary = {k: v for k, v in old.summary.items() if k not in GRAPH_ONLY_SUMMARY_KEYS}
    assert run.sim["summary"] == summary

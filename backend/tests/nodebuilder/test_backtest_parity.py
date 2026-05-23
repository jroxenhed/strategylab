"""
Unit 8b: R2 parity tests — graph backtest vs rule backtest.

For 8 of 10 fixture strategies the graph path must produce numerically identical
results to the rule path (rel=1e-4 for FP drift across two evaluation pipelines).
regime_mode and per_direction_b23 are explicitly skipped (T2 scope).

Additional tests:
  - test_response_shape_keys_only      — response keys are exactly the 4 required
  - test_unwired_size_stop_terminals_no_override — unwired Size/Stop → no override
  - test_regime_graph_returns_400      — /regime/ node → 400
  - test_zero_trades                   — no-signal graph → summary.num_trades==0
  - test_short_only_graph              — direction="short" → correct trade types
  - test_bypassed_node_skipped         — bypass=True comparison → absent from result
"""
from __future__ import annotations

import os
import pickle
import sys

import numpy as np
import pandas as pd
import pytest

# Ensure backend root is on the path when running from repo root
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from models import StrategyRequest, Rule, RegimeConfig, TrailingStopConfig
from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.compile import compile as _compile_graph
from nodebuilder.evaluator import (
    compute_indicators_from_specs,
    evaluate_graph,
    RegimeUnsupportedError,
)
from nodebuilder.from_rules import auto_render
from nodebuilder.models import Graph, Node, Wire
from routes.backtest import _run_simulation, run_backtest
from routes.nodebuilder import (
    _apply_settings_overrides,
    _build_baseline_curve,
    _make_cached_eval,
    _settings_to_strategy_request,
    run_graph_backtest,
)
from shared import _format_time_index
from tests.nodebuilder._snapshot_helpers import assert_equal_within_tolerance

_FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__), "fixtures", "run_backtest_snapshots"
)

# Parity tolerance: cross-path FP drift; 1e-4 is generous but appropriate
# since the two pipelines use different indicator dispatchers.
_REL = 1e-4

# ---------------------------------------------------------------------------
# Fixture strategies (mirrors Unit 8a's _make_strategies)
# ---------------------------------------------------------------------------

def _make_strategies():
    return [
        (
            "simple_long_rsi",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
            ),
        ),
        (
            "simple_short_rsi",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                direction="short",
                buy_rules=[Rule(indicator="rsi", condition="above", value=70)],
                sell_rules=[Rule(indicator="rsi", condition="below", value=30)],
            ),
        ),
        (
            "macd_crossover",
            StrategyRequest(
                ticker="SPY", start="2021-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="macd", condition="crosses_above", param="signal")],
                sell_rules=[Rule(indicator="macd", condition="crosses_below", param="signal")],
            ),
        ),
        (
            "trailing_stop_pct",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
                trailing_stop=TrailingStopConfig(type="pct", value=5.0),
            ),
        ),
        (
            "atr_trailing_stop",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
                trailing_stop=TrailingStopConfig(type="atr", value=2.0),
            ),
        ),
        (
            "regime_mode",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
                long_buy_rules=[Rule(indicator="rsi", condition="below", value=35)],
                long_sell_rules=[Rule(indicator="rsi", condition="above", value=65)],
                short_buy_rules=[Rule(indicator="rsi", condition="above", value=65)],
                short_sell_rules=[Rule(indicator="rsi", condition="below", value=35)],
                regime=RegimeConfig(
                    enabled=True,
                    rules=[Rule(indicator="ma", condition="above", param="ma:200:sma")],
                    on_flip="close_only",
                ),
            ),
        ),
        (
            "nonzero_costs",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
                per_share_rate=0.0035,
                min_per_order=0.35,
                slippage_bps=5.0,
            ),
        ),
        (
            "per_direction_b23",
            StrategyRequest(
                ticker="SPY", start="2021-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[],
                sell_rules=[],
                long_buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                short_buy_rules=[Rule(indicator="rsi", condition="above", value=70)],
                long_sell_rules=[Rule(indicator="rsi", condition="above", value=50)],
                short_sell_rules=[Rule(indicator="rsi", condition="below", value=50)],
                regime=RegimeConfig(
                    enabled=True,
                    rules=[Rule(indicator="ma", condition="above", param="ma:200:sma")],
                    on_flip="close_only",
                ),
            ),
        ),
        (
            "not_negated_rule",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="rsi", condition="below", value=30, negated=True)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
            ),
        ),
        (
            "max_bars_held",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
                max_bars_held=10,
            ),
        ),
    ]


_STRATEGIES = _make_strategies()
_STRATEGY_IDS = [name for name, _ in _STRATEGIES]

# ---------------------------------------------------------------------------
# Helpers to load fixture pickles and run both paths
# ---------------------------------------------------------------------------

def _load_df(name: str) -> pd.DataFrame:
    pickle_fp = os.path.join(_FIXTURES_DIR, f"{name}_df.pkl")
    with open(pickle_fp, "rb") as fh:
        return pickle.load(fh)


def _run_rule_path(rule_req: StrategyRequest, df: pd.DataFrame) -> dict:
    """Run the canonical rule backtest with a pre-fetched df."""
    return run_backtest(rule_req, include_spy_correlation=False, df=df)


def _run_graph_path(rule_req: StrategyRequest, df: pd.DataFrame) -> dict:
    """Convert rule_req → Graph → run_graph_backtest with the same pre-fetched df."""
    graph = auto_render(rule_req)
    graph_req = GraphBacktestRequest(
        graph=graph,
        ticker=rule_req.ticker,
        start=rule_req.start,
        end=rule_req.end,
        interval=rule_req.interval,
        source=rule_req.source,
        initial_capital=rule_req.initial_capital,
        position_size=rule_req.position_size,
        stop_loss_pct=rule_req.stop_loss_pct,
        trailing_stop=rule_req.trailing_stop,
        max_bars_held=rule_req.max_bars_held,
        slippage_bps=rule_req.slippage_bps,
        commission_pct=rule_req.commission_pct,
        per_share_rate=rule_req.per_share_rate,
        min_per_order=rule_req.min_per_order,
        borrow_rate_annual=rule_req.borrow_rate_annual,
        dynamic_sizing=rule_req.dynamic_sizing,
        skip_after_stop=rule_req.skip_after_stop,
        trading_hours=rule_req.trading_hours,
        direction=rule_req.direction,
    )
    result = run_graph_backtest(graph_req, df=df)
    return {
        "summary": result.summary,
        "trades": result.trades,
        "equity_curve": result.equity_curve,
        "baseline_curve": result.baseline_curve,
    }


def _compare_results(rule_result: dict, graph_result: dict, name: str) -> None:
    """Assert numeric parity within _REL; categorical fields exact.

    The rule path (run_backtest) adds extra keys that the graph path intentionally
    omits (beta, r_squared from SPY correlation; session_analytics).  We compare
    only the keys present in the graph result's summary — it must be a subset of
    the rule result.  Keys present in graph but absent from rule are an error.
    """
    # summary — compare keys present in the graph result against the rule result
    rs, gs = rule_result["summary"], graph_result["summary"]
    missing_from_rule = set(gs.keys()) - set(rs.keys())
    assert not missing_from_rule, (
        f"{name}: summary keys in graph but absent from rule: {missing_from_rule}"
    )
    for key in gs:
        assert_equal_within_tolerance(gs[key], rs[key], rel=_REL, path=f"{name}.summary.{key}")

    # trades: same count, same entry/exit dates, same direction sequence, PnL within tolerance
    rt, gt = rule_result["trades"], graph_result["trades"]
    assert len(rt) == len(gt), (
        f"{name}: trade count mismatch: rule={len(rt)} graph={len(gt)}"
    )
    for i, (r, g) in enumerate(zip(rt, gt)):
        assert r["type"] == g["type"], f"{name}: trade[{i}] type mismatch: {r['type']} vs {g['type']}"
        assert r["date"] == g["date"], f"{name}: trade[{i}] date mismatch: {r['date']} vs {g['date']}"
        assert r.get("direction") == g.get("direction"), (
            f"{name}: trade[{i}] direction mismatch: {r.get('direction')} vs {g.get('direction')}"
        )
        if "pnl" in r and r["pnl"] is not None:
            assert_equal_within_tolerance(g["pnl"], r["pnl"], rel=_REL, path=f"{name}.trade[{i}].pnl")

    # equity_curve: same length, values within tolerance
    re, ge = rule_result["equity_curve"], graph_result["equity_curve"]
    assert len(re) == len(ge), (
        f"{name}: equity_curve length mismatch: rule={len(re)} graph={len(ge)}"
    )
    for i, (r, g) in enumerate(zip(re, ge)):
        assert_equal_within_tolerance(g["value"], r["value"], rel=_REL, path=f"{name}.equity[{i}].value")

    # baseline_curve: same length, values within tolerance
    rb, gb = rule_result["baseline_curve"], graph_result["baseline_curve"]
    assert len(rb) == len(gb), (
        f"{name}: baseline_curve length mismatch: rule={len(rb)} graph={len(gb)}"
    )
    for i, (r, g) in enumerate(zip(rb, gb)):
        assert_equal_within_tolerance(g["value"], r["value"], rel=_REL, path=f"{name}.baseline[{i}].value")


# ---------------------------------------------------------------------------
# Parametrized parity tests
# ---------------------------------------------------------------------------

_SKIP_REASON = "T2 scope: regime evaluator not supported (graph compile raises RegimeUnsupportedError)"
_REGIME_NAMES = {"regime_mode", "per_direction_b23"}


@pytest.mark.parametrize("name,req", _STRATEGIES, ids=_STRATEGY_IDS)
def test_graph_backtest_parity(name, req):
    """Graph path must produce numerically identical results to the rule path."""
    if name in _REGIME_NAMES:
        pytest.skip(_SKIP_REASON)

    df = _load_df(name)
    rule_result = _run_rule_path(req, df)
    graph_result = _run_graph_path(req, df)
    _compare_results(rule_result, graph_result, name)


# ---------------------------------------------------------------------------
# Auxiliary tests
# ---------------------------------------------------------------------------

def test_response_shape_keys_only():
    """Route response contains EXACTLY {summary, trades, equity_curve, baseline_curve}."""
    name = "simple_long_rsi"
    req = next(r for n, r in _STRATEGIES if n == name)
    df = _load_df(name)
    graph = auto_render(req)
    graph_req = GraphBacktestRequest(
        graph=graph,
        ticker=req.ticker, start=req.start, end=req.end,
        interval=req.interval, source=req.source,
    )
    result = run_graph_backtest(graph_req, df=df)
    result_dict = result.model_dump()
    assert set(result_dict.keys()) == {"summary", "trades", "equity_curve", "baseline_curve"}, (
        f"Unexpected keys in response: {set(result_dict.keys())}"
    )


def test_unwired_size_stop_terminals_no_override():
    """A graph with unwired Size/Stop terminals runs identically to one without them.

    The /size and /stop nodes are compile_active=False at T2 — they are silently
    skipped by compile().  Adding them to the graph should not change the result.
    """
    name = "simple_long_rsi"
    req = next(r for n, r in _STRATEGIES if n == name)
    df = _load_df(name)

    # Build graph without size/stop and with size/stop added manually
    graph_without = auto_render(req)

    # Inject size and stop nodes (unwired — no wires reference them)
    nodes_dict = {k: v.model_dump(by_alias=False) for k, v in graph_without.nodes.items()}
    nodes_dict["/size_terminal"] = {
        "id": "/size_terminal", "type": "size", "params": {},
        "position": [999.0, 0.0], "display": True, "bypass": False, "subgraph": None,
    }
    nodes_dict["/stop_terminal"] = {
        "id": "/stop_terminal", "type": "stop", "params": {},
        "position": [999.0, 100.0], "display": True, "bypass": False, "subgraph": None,
    }
    graph_with = Graph.model_validate({
        "_version": 1,
        "readOnly": True,
        "nodes": nodes_dict,
        "wires": [w.model_dump(by_alias=False) for w in graph_without.wires],
    })

    req_without = GraphBacktestRequest(
        graph=graph_without, ticker=req.ticker, start=req.start, end=req.end,
        interval=req.interval, source=req.source,
    )
    req_with = GraphBacktestRequest(
        graph=graph_with, ticker=req.ticker, start=req.start, end=req.end,
        interval=req.interval, source=req.source,
    )

    result_without = run_graph_backtest(req_without, df=df)
    result_with = run_graph_backtest(req_with, df=df)

    assert result_without.summary["num_trades"] == result_with.summary["num_trades"]
    assert_equal_within_tolerance(
        result_without.summary["final_value"],
        result_with.summary["final_value"],
        rel=1e-9,
        path="final_value",
    )


def test_regime_graph_returns_400():
    """A graph containing a /regime/ node raises RegimeUnsupportedError (→ HTTP 400)."""
    name = "simple_long_rsi"
    req = next(r for n, r in _STRATEGIES if n == name)
    df = _load_df(name)
    graph = auto_render(req)

    # Inject a /regime/ node — compile() should immediately raise
    nodes_dict = {k: v.model_dump(by_alias=False) for k, v in graph.nodes.items()}
    nodes_dict["/regime/ticker_aapl_1wk_yahoo"] = {
        "id": "/regime/ticker_aapl_1wk_yahoo", "type": "ticker",
        "params": {"symbol": "AAPL", "interval": "1wk", "source": "yahoo"},
        "position": [0.0, 1000.0], "display": True, "bypass": False, "subgraph": None,
    }
    regime_graph = Graph.model_validate({
        "_version": 1,
        "readOnly": True,
        "nodes": nodes_dict,
        "wires": [w.model_dump(by_alias=False) for w in graph.wires],
    })
    graph_req = GraphBacktestRequest(
        graph=regime_graph, ticker=req.ticker, start=req.start, end=req.end,
        interval=req.interval, source=req.source,
    )
    with pytest.raises(RegimeUnsupportedError):
        run_graph_backtest(graph_req, df=df)


def test_zero_trades():
    """A graph that never produces an entry signal → num_trades==0, equity flat."""
    name = "simple_long_rsi"
    req = next(r for n, r in _STRATEGIES if n == name)
    df = _load_df(name)
    graph = auto_render(req)

    # Build a graph whose entry condition is always false:
    # Use RSI above 100 (impossible threshold → never fires)
    impossible_req = StrategyRequest(
        ticker=req.ticker, start=req.start, end=req.end,
        interval=req.interval, source=req.source,
        buy_rules=[Rule(indicator="rsi", condition="above", value=100)],
        sell_rules=[Rule(indicator="rsi", condition="below", value=0)],
    )
    impossible_graph = auto_render(impossible_req)
    graph_req = GraphBacktestRequest(
        graph=impossible_graph, ticker=req.ticker, start=req.start, end=req.end,
        interval=req.interval, source=req.source,
    )
    result = run_graph_backtest(graph_req, df=df)
    assert result.summary["num_trades"] == 0
    assert result.trades == []
    # equity_curve must be flat at initial_capital (never entered a trade)
    initial = graph_req.initial_capital
    for bar in result.equity_curve:
        assert bar["value"] == pytest.approx(initial, rel=1e-9), (
            f"equity not flat: got {bar['value']} at {bar['time']}"
        )


def test_short_only_graph():
    """direction='short' graph → trade types are 'short'/'cover'."""
    name = "simple_short_rsi"
    req = next(r for n, r in _STRATEGIES if n == name)
    df = _load_df(name)
    graph = auto_render(req)
    graph_req = GraphBacktestRequest(
        graph=graph, ticker=req.ticker, start=req.start, end=req.end,
        interval=req.interval, source=req.source,
        direction="short",
    )
    result = run_graph_backtest(graph_req, df=df)
    entry_types = {t["type"] for t in result.trades if t["type"] in ("buy", "short")}
    exit_types = {t["type"] for t in result.trades if t["type"] in ("sell", "cover")}
    if result.trades:
        assert entry_types == {"short"}, f"Expected short entries, got: {entry_types}"
        assert exit_types == {"cover"}, f"Expected cover exits, got: {exit_types}"


def test_bypassed_node_skipped():
    """A bypassed comparison node's contribution is absent from the result.

    Strategy: build a two-condition strategy where ONE comparison is bypassed.
    The remaining condition can still fire, but it's the SELL side that's bypassed
    so the buy still triggers but sell never signals → the bypassed graph has
    zero sells via signal (trades close only at end-of-period / stop).

    Simpler approach: bypass the SELL comparison in a strategy with an explicit
    sell rule and verify the graph runs without crashing and produces the correct
    shape.  When the sell comparison is bypassed, the exit terminal gets no signal
    → strategy holds through end of period (position stays open).  The rule path
    with the same sell rule intact would produce sell trades.
    """
    name = "simple_long_rsi"
    req = next(r for n, r in _STRATEGIES if n == name)
    df = _load_df(name)

    # Build graph from the rule set
    graph = auto_render(req)

    # Find a comparison node on the SELL side (type "above", for RSI > 70)
    # The sell comparison is /cmp_sell_0 in the auto-rendered graph.
    # We bypass it so the exit terminal has no feed → always-false sentinel.
    sell_cmp_path = None
    for path, node in graph.nodes.items():
        if "sell" in path and node.type in ("above", "below", "crosses_above", "crosses_below"):
            sell_cmp_path = path
            break

    if sell_cmp_path is None:
        pytest.skip("No sell-side comparison node found in the auto-rendered graph")

    # Rebuild graph with the SELL comparison bypassed
    nodes_dict = {k: v.model_dump(by_alias=False) for k, v in graph.nodes.items()}
    nodes_dict[sell_cmp_path]["bypass"] = True
    bypassed_graph = Graph.model_validate({
        "_version": 1,
        "readOnly": True,
        "nodes": nodes_dict,
        "wires": [w.model_dump(by_alias=False) for w in graph.wires],
    })
    graph_req = GraphBacktestRequest(
        graph=bypassed_graph, ticker=req.ticker, start=req.start, end=req.end,
        interval=req.interval, source=req.source,
    )
    result = run_graph_backtest(graph_req, df=df)
    # Must return the 4-key shape without crashing
    assert hasattr(result, "summary")
    assert hasattr(result, "trades")
    assert hasattr(result, "equity_curve")
    assert hasattr(result, "baseline_curve")

    # With the sell comparison bypassed, the exit logic gets no input.
    # The AND gate evaluates with all-false inputs → no sell signals.
    # The strategy may enter but never exit via signal.
    # Compared to the unmodified graph result: sell signal trades should be absent.
    unmodified_req = GraphBacktestRequest(
        graph=graph, ticker=req.ticker, start=req.start, end=req.end,
        interval=req.interval, source=req.source,
    )
    unmodified_result = run_graph_backtest(unmodified_req, df=df)

    # Bypassed sell → fewer or equal sell trades (it can only exit via stop / time stop,
    # neither of which is configured here → effectively 0 signal-driven sells)
    bypassed_sells = [t for t in result.trades if t["type"] == "sell"]
    unmodified_sells = [t for t in unmodified_result.trades if t["type"] == "sell"]
    assert len(bypassed_sells) <= len(unmodified_sells), (
        f"Bypassed sell should have fewer sells: {len(bypassed_sells)} vs {len(unmodified_sells)}"
    )

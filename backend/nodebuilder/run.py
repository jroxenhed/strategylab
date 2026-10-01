"""Graph backtest core.

run_graph_backtest() compiles a graph, computes its indicators and runs the
shared simulator.  It lives here, not in the routes module, so the API route
and the bot code can both call it.  The route in routes/nodebuilder.py is a
thin wrapper that turns errors into HTTP responses.

The graph cooks once, before the simulation: every node runs over the whole
frame, and the simulator reads the Entry and Exit columns bar by bar.  The
editor's backtest keeps every node's stream from that cook (keep_all) so
the wire inspector can read it from the cook cache (plan D6) without
cooking again; the bot path keeps only the terminals.  This module never
imports the cook cache: the route does.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from models import StrategyRequest
from nodebuilder import evaluator as _evaluator
from nodebuilder.api_models import GraphBacktestRequest, GraphBacktestResponse
from nodebuilder.compile import compile as _compile_graph
from nodebuilder.evaluator import evaluate_graph
from nodebuilder.prepare import NO_EXIT_ATTR, build_graph_attrs
from nodebuilder.sim_settings import settings_overrides

# The exit sentinel compile() uses when nothing is wired into Exit.
_NO_EXIT_ATTR = NO_EXIT_ATTR

# Summary keys only the graph backtest returns.  The rule backtest response
# does not have them, so parity checks skip these keys.
GRAPH_ONLY_SUMMARY_KEYS: frozenset[str] = frozenset({"open_position", "exit_connected"})


def _apply_settings_overrides(req: GraphBacktestRequest, simulator_settings: list) -> dict:
    """Build a dict of simulator fields, applying graph SimulatorSettings as overrides.

    The GRAPH WINS: if the same field is specified in both the request and the graph,
    the graph's compile-time SimulatorSetting takes precedence.  This reflects the
    design intent: the graph is the authoritative specification at backtest time.
    The key mapping lives in nodebuilder/sim_settings.py, shared with the bot runner.
    """
    overrides: dict = {
        "initial_capital": req.initial_capital,
        "position_size": req.position_size,
        "stop_loss_pct": req.stop_loss_pct,
        "trailing_stop": req.trailing_stop,
        "max_bars_held": req.max_bars_held,
        "slippage_bps": req.slippage_bps,
        "commission_pct": req.commission_pct,
        "per_share_rate": req.per_share_rate,
        "min_per_order": req.min_per_order,
        "borrow_rate_annual": req.borrow_rate_annual,
        "dynamic_sizing": req.dynamic_sizing,
        "skip_after_stop": req.skip_after_stop,
        "trading_hours": req.trading_hours,
        "direction": req.direction,
    }
    overrides.update(settings_overrides(simulator_settings))
    return overrides


def _settings_to_strategy_request(settings: dict, req: GraphBacktestRequest) -> StrategyRequest:
    """Build a minimal StrategyRequest from the GraphBacktestRequest + settings dict.

    The buy_rules / sell_rules are empty — _run_simulation reads simulator-level
    fields, not rules.  b23_mode is False (graph mode never uses the dual-rule path).
    """
    return StrategyRequest(
        ticker=req.ticker,
        start=req.start,
        end=req.end,
        interval=req.interval,
        source=req.source,
        buy_rules=[],
        sell_rules=[],
        initial_capital=settings["initial_capital"],
        position_size=settings["position_size"],
        stop_loss_pct=settings["stop_loss_pct"],
        trailing_stop=settings["trailing_stop"],
        max_bars_held=settings["max_bars_held"],
        slippage_bps=settings["slippage_bps"],
        commission_pct=settings["commission_pct"],
        per_share_rate=settings["per_share_rate"],
        min_per_order=settings["min_per_order"],
        borrow_rate_annual=settings["borrow_rate_annual"],
        dynamic_sizing=settings["dynamic_sizing"],
        skip_after_stop=settings["skip_after_stop"],
        trading_hours=settings["trading_hours"],
        direction=settings["direction"],
    )


def _make_cached_eval(program, attrs):
    """Return a callable that evaluates the graph at bar i, memoised per bar.

    evaluate_graph mutates attrs in-place.  Calling it twice per bar (once for
    the buy fn and once for the sell fn) is safe — the second call overwrites
    with identical values — but is wasteful.  The cache avoids the double call.
    """
    cache: dict[int, dict] = {}

    def call(i: int) -> dict:
        if i not in cache:
            cache[i] = evaluate_graph(program, attrs, i)
        return cache[i]

    return call


def _build_baseline_curve(df: pd.DataFrame, initial_capital: float, date_strs: list) -> list[dict]:
    """Buy-and-hold baseline: initial_capital * close[i] / close[0]."""
    close_arr = df["Close"].to_numpy(dtype=float, copy=False)
    first_close = float(close_arr[0])
    return [
        {"time": date_strs[i], "value": round(initial_capital * close_arr[i] / first_close, 2)}
        for i in range(len(df))
    ]


def _open_position(trades: list[dict], last_close: float) -> dict | None:
    """Describe the position still open at the last bar, or None if flat.

    The simulator counts only closed trades in num_trades, but final_value
    includes an open position at the last close.  This tells the UI why the
    return can be non-zero with zero trades.
    """
    if not trades or trades[-1].get("type") not in ("buy", "short"):
        return None
    entry = trades[-1]
    direction = entry.get("direction") or ("short" if entry["type"] == "short" else "long")
    entry_price = float(entry["price"])
    if entry_price <= 0:
        unrealized_pct = 0.0
    elif direction == "short":
        unrealized_pct = (entry_price - last_close) / entry_price * 100
    else:
        unrealized_pct = (last_close - entry_price) / entry_price * 100
    return {
        "direction": direction,
        "entry_price": round(entry_price, 4),
        "unrealized_pct": round(unrealized_pct, 2),
    }


@dataclass
class GraphCook:
    """One cook of a graph over one fetched frame.

    program : the CompiledProgram.
    result  : the kernel CookResult (every node's stream when keep_all).
    df      : the window's main frame.
    frames  : ``((symbol, interval, df), ...)`` for EVERY frame the cook
              read.  The editor's cache key fingerprints all of them (plan
              D6), so code that makes a cook read another frame (W5
              reference tickers, HTF frames) must add it here.
    """
    program: Any
    result: Any
    df: pd.DataFrame
    frames: tuple = ()


def cook_attrs(program, attrs: dict, *, keep_all: bool = False):
    """Cook *program* over the bars in *attrs* (as build_graph_attrs makes
    them) and return the kernel CookResult.

    The bars are read exactly as evaluate_graph reads them, so a backtest
    cooked here gives the signals the per-bar adapter gave.  keep_all keeps
    every node's output stream (the editor's inspector cook); otherwise
    only the terminals' streams are kept.  CPU work: never call this on an
    event loop.
    """
    index, bars = _evaluator.bars_from_attrs(attrs)
    return _evaluator.cook_program(program, index=index, bars=bars, keep_all=keep_all)


def cook_graph_window(
    graph,
    *,
    ticker: str,
    start: str,
    end: str,
    interval: str,
    source: str,
    df: Optional[pd.DataFrame] = None,
) -> GraphCook:
    """Compile *graph*, fetch its frame (unless *df* is given) and cook it
    with every node's stream kept.  The same cook a backtest makes, without
    the simulation: what the inspector does on a cache miss (plan D6).

    Raises GraphValidationError for a graph that does not compile, and
    ValueError or HTTPException(400) for a bad source or missing data.
    """
    from shared import _fetch, require_valid_source

    src = require_valid_source(source)
    program = _compile_graph(graph)
    if df is None:
        df = _fetch(ticker, start, end, interval, source=src)
    if df is None or len(df) == 0:
        raise ValueError(f"No data for {ticker} in {start}..{end} ({interval}).")
    attrs = build_graph_attrs(program, df)
    return GraphCook(program=program, result=cook_attrs(program, attrs, keep_all=True), df=df,
                     frames=((ticker, interval, df),))


def run_graph_backtest(
    req: GraphBacktestRequest,
    df: pd.DataFrame | None = None,
) -> GraphBacktestResponse:
    """Core graph backtest logic — callable from the route, bot code and tests.

    Args:
        req: GraphBacktestRequest with graph + simulator settings.
        df: Optional pre-fetched DataFrame (bypasses _fetch; used in parity tests).

    Raises:
        GraphValidationError (and subclasses): the graph cannot be compiled or run.
            Each carries node_id naming the node at fault, or None.
        ValueError: invalid source or other data issues.
        HTTPException: re-raised from _run_simulation.
    """
    response, _cook = run_graph_backtest_cooked(req, df, keep_all=False)
    return response


def run_graph_backtest_cooked(
    req: GraphBacktestRequest,
    df: pd.DataFrame | None = None,
    *,
    keep_all: bool = True,
) -> tuple[GraphBacktestResponse, GraphCook]:
    """run_graph_backtest, plus the cook it made (for the editor's cook
    cache).  keep_all (the default here) keeps every node's stream so the
    wire inspector can read it; run_graph_backtest passes False."""
    from routes.backtest import _run_simulation
    from shared import _fetch, _format_time_index, require_valid_source

    # 1. Validate source
    source = require_valid_source(req.source)

    # 2. Compile graph (raises RegimeUnsupportedError, MissingTerminalError, etc.)
    program = _compile_graph(req.graph)

    # 3. Apply settings-node overrides
    settings = _apply_settings_overrides(req, program.simulator_settings)

    # 4. Fetch OHLCV (or use the pre-fetched df passed in from tests)
    if df is None:
        df = _fetch(req.ticker, req.start, req.end, req.interval, source=source)

    # 5-7. The attrs the graph cooks from: the bar series, the always-false
    # exit sentinel, and ATR(14) when the trailing stop is ATR based (without
    # it the ATR reads 0 and the trail fires at once).  nodebuilder.prepare
    # builds them for the live bot too, so both paths see the same series.
    indicator_attrs = build_graph_attrs(program, df, settings.get("trailing_stop"))

    # 8. Cook the graph once over the whole frame, then build the signal
    #    callables _run_simulation expects, reading the Entry/Exit columns:
    #    buy_signal_fn(i, curr_regime_active) -> (fired, rules, direction)
    #    sell_signal_fn(i, position_direction, curr_regime_active) -> (fired, rules)
    result = cook_attrs(program, indicator_attrs, keep_all=keep_all)
    entry_col, exit_col = _evaluator.signal_columns(program, result)

    direction = settings["direction"]

    def buy_signal_fn(i: int, curr_regime_active: bool):
        return bool(entry_col[i]), [], direction

    def sell_signal_fn(i: int, position_direction, curr_regime_active: bool):
        return bool(exit_col[i]), []

    # 9. Build a StrategyRequest-shaped object for _run_simulation
    sim_req = _settings_to_strategy_request(settings, req)

    # 10. Build date_strs (required by _run_simulation)
    date_strs = _format_time_index(df.index, req.interval)

    # 11. Run the simulation loop
    sim_result = _run_simulation(
        df=df,
        indicators=indicator_attrs,
        buy_signal_fn=buy_signal_fn,
        sell_signal_fn=sell_signal_fn,
        req=sim_req,
        b23_mode=False,
        regime_active_series=None,
        on_flip="hold",
        date_strs=date_strs,
    )

    # 12. Build baseline_curve
    baseline_curve = _build_baseline_curve(df, settings["initial_capital"], date_strs)

    # 13. Graph-only summary fields: an open position at the end, and whether
    # anything is wired into Exit.  The rule backtest response is unchanged.
    summary = dict(sim_result["summary"])
    summary["open_position"] = _open_position(sim_result["trades"], float(df["Close"].iloc[-1]))
    summary["exit_connected"] = program.exit_attr != _NO_EXIT_ATTR

    response = GraphBacktestResponse(
        summary=summary,
        trades=sim_result["trades"],
        equity_curve=sim_result["equity_curve"],
        baseline_curve=baseline_curve,
    )
    return response, GraphCook(program=program, result=result, df=df,
                               frames=((req.ticker, req.interval, df),))

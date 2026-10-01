"""Graph backtest core.

run_graph_backtest() compiles a graph, computes its indicators and runs the
shared simulator.  It lives here, not in the routes module, so the API route
and the bot code can both call it.  The route in routes/nodebuilder.py is a
thin wrapper that turns errors into HTTP responses.
"""
from __future__ import annotations

import pandas as pd

from models import StrategyRequest
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

    # 8. Build memoising signal callables that match _run_simulation's signature:
    #    buy_signal_fn(i, curr_regime_active) -> (fired, rules, direction)
    #    sell_signal_fn(i, position_direction, curr_regime_active) -> (fired, rules)
    cached_eval = _make_cached_eval(program, indicator_attrs)

    direction = settings["direction"]

    def buy_signal_fn(i: int, curr_regime_active: bool):
        sigs = cached_eval(i)
        fired = sigs["entry"]
        return bool(fired), [], direction

    def sell_signal_fn(i: int, position_direction, curr_regime_active: bool):
        sigs = cached_eval(i)
        fired = sigs["exit"]
        return bool(fired), []

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

    return GraphBacktestResponse(
        summary=summary,
        trades=sim_result["trades"],
        equity_curve=sim_result["equity_curve"],
        baseline_curve=baseline_curve,
    )

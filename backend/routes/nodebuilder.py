"""Node-builder API routes.

POST /api/nodebuilder/auto_render  — Unit 3
POST /api/nodebuilder/backtest     — Unit 8b
POST /api/nodebuilder/validate     — Unit 8b
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from models import StrategyRequest, TrailingStopConfig
from nodebuilder.api_models import AutoRenderResponse, GraphBacktestRequest, GraphBacktestResponse
from nodebuilder.compile import compile as _compile_graph
from nodebuilder.evaluator import (
    RegimeUnsupportedError,
    compute_indicators_from_specs,
    evaluate_graph,
)
from nodebuilder.from_rules import auto_render

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/nodebuilder", tags=["nodebuilder"])


@router.post("/auto_render", response_model=AutoRenderResponse, response_model_by_alias=True)
def post_auto_render(req: StrategyRequest) -> AutoRenderResponse:
    """Translate a StrategyRequest into a read-only Graph for the T1 viewer."""
    graph = auto_render(req)
    return AutoRenderResponse(graph=graph)


# ---------------------------------------------------------------------------
# POST /api/nodebuilder/backtest  — Unit 8b
# ---------------------------------------------------------------------------

def _apply_settings_overrides(req: GraphBacktestRequest, simulator_settings: list) -> dict:
    """Build a dict of simulator fields, applying graph SimulatorSettings as overrides.

    The GRAPH WINS: if the same field is specified in both the request and the graph,
    the graph's compile-time SimulatorSetting takes precedence.  This reflects the
    design intent: the graph is the authoritative specification at backtest time.
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
    for setting in simulator_settings:
        key = setting.key
        val = setting.value
        if key == "position_size":
            overrides["position_size"] = float(val)
        elif key == "stop_loss":
            overrides["stop_loss_pct"] = float(val)
        elif key == "slippage_bps":
            overrides["slippage_bps"] = float(val)
        elif key == "per_share_rate":
            overrides["per_share_rate"] = float(val)
        elif key == "min_per_order":
            overrides["min_per_order"] = float(val)
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


def run_graph_backtest(
    req: GraphBacktestRequest,
    df: pd.DataFrame | None = None,
) -> GraphBacktestResponse:
    """Core graph backtest logic — callable from both the route and tests.

    Args:
        req: GraphBacktestRequest with graph + simulator settings.
        df: Optional pre-fetched DataFrame (bypasses _fetch; used in parity tests).

    Raises:
        RegimeUnsupportedError: propagated from compile() when graph has /regime/ nodes.
        ValueError: invalid source or other data issues.
        HTTPException: re-raised from _run_simulation.
    """
    from indicators import OHLCVSeries
    from routes.backtest import _run_simulation
    from shared import _fetch, _format_time_index, _INTRADAY_INTERVALS, require_valid_source

    # 1. Validate source
    source = require_valid_source(req.source)

    # 2. Compile graph (raises RegimeUnsupportedError, MissingTerminalError, etc.)
    program = _compile_graph(req.graph)

    # 3. Apply settings-node overrides
    settings = _apply_settings_overrides(req, program.simulator_settings)

    # 4. Fetch OHLCV (or use the pre-fetched df passed in from tests)
    if df is None:
        df = _fetch(req.ticker, req.start, req.end, req.interval, source=source)

    # 5. Build OHLCVSeries and compute indicators from graph specs
    vol_series = df["Volume"] if "Volume" in df.columns else pd.Series(0, index=df.index)
    ohlcv = OHLCVSeries(
        close=df["Close"],
        high=df["High"],
        low=df["Low"],
        volume=vol_series,
    )
    indicator_attrs = compute_indicators_from_specs(program.indicator_specs, ohlcv)

    # 6. Seed raw OHLCV attrs so comparisons reading @close/@volume work
    indicator_attrs["@close"] = df["Close"]
    indicator_attrs["@open"] = df["Open"]
    indicator_attrs["@high"] = df["High"]
    indicator_attrs["@low"] = df["Low"]
    indicator_attrs["@volume"] = vol_series
    # Seed the always-false sentinel (used when no exit terminal is wired)
    indicator_attrs["@always_false"] = pd.Series(0.0, index=df.index, dtype="float64")

    # 6b. If trailing_stop is ATR-based and the graph has no explicit ATR node,
    # compute ATR (period=14) so _run_simulation can use it for the trailing stop.
    # Without this, indicators.get("atr") returns None and the ATR value is 0
    # (trail_stop_price = trail_peak + 0 = trail_peak, triggering immediately).
    ts_config = settings.get("trailing_stop")
    if ts_config is not None and getattr(ts_config, "type", None) == "atr" and "atr" not in indicator_attrs:
        from indicators import compute_instance
        atr_result = compute_instance("atr", {"period": 14}, ohlcv)
        indicator_attrs["atr"] = atr_result["atr"]

    # 7. Pre-allocate per-op output series as float64 (default NaN)
    for op in program.per_bar_program:
        if op.writes not in indicator_attrs:
            indicator_attrs[op.writes] = pd.Series(np.nan, index=df.index, dtype="float64")

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

    return GraphBacktestResponse(
        summary=sim_result["summary"],
        trades=sim_result["trades"],
        equity_curve=sim_result["equity_curve"],
        baseline_curve=baseline_curve,
    )


@router.post("/backtest", response_model=GraphBacktestResponse)
def post_graph_backtest(req: GraphBacktestRequest) -> GraphBacktestResponse:
    """Run a backtest using a compiled node graph.

    Returns {summary, trades, equity_curve, baseline_curve}.
    Rule-only debug fields (signal_trace, rule_signals, ema_overlays, regime_series)
    are intentionally absent from the graph backtest response.
    """
    try:
        return run_graph_backtest(req)
    except RegimeUnsupportedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception:
        logger.exception("/api/nodebuilder/backtest failed")
        raise HTTPException(status_code=500, detail="graph backtest failed")

"""API request/response models for nodebuilder routes.

Unit 3: AutoRenderResponse
Unit 8b: GraphBacktestRequest / GraphBacktestResponse
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from models import TrailingStopConfig, DynamicSizingConfig, SkipAfterStopConfig, TradingHoursConfig
from nodebuilder.models import Graph


class AutoRenderResponse(BaseModel):
    """Response from POST /api/nodebuilder/auto_render."""
    graph: Graph


class GraphBacktestRequest(BaseModel):
    """Request body for POST /api/nodebuilder/backtest.

    Mirrors the simulator-level fields of StrategyRequest verbatim plus a graph field.
    Settings nodes inside the graph can override these at compile time (graph wins).
    """
    graph: Graph
    # Data / window fields (identical names to StrategyRequest)
    ticker: str
    start: str
    end: str
    interval: str = "1d"
    source: str = "yahoo"
    # Simulator config fields (identical names and defaults to StrategyRequest)
    initial_capital: float = 10000.0
    position_size: float = 1.0
    stop_loss_pct: Optional[float] = None
    trailing_stop: Optional[TrailingStopConfig] = None
    max_bars_held: Optional[int] = None
    slippage_bps: float = 2.0
    commission_pct: float = 0.0
    per_share_rate: float = 0.0
    min_per_order: float = 0.0
    borrow_rate_annual: float = 0.5
    dynamic_sizing: Optional[DynamicSizingConfig] = None
    skip_after_stop: Optional[SkipAfterStopConfig] = None
    trading_hours: Optional[TradingHoursConfig] = None
    direction: str = "long"


class GraphBacktestResponse(BaseModel):
    """Response from POST /api/nodebuilder/backtest.

    Rule-only debug fields (signal_trace, rule_signals, ema_overlays,
    regime_series) are intentionally absent — they are not part of the
    graph backtest surface (R2 scope contract).
    """
    summary: dict
    trades: list[dict]
    equity_curve: list[dict]
    baseline_curve: list[dict]

"""API request/response models for nodebuilder routes.

Unit 3: AutoRenderResponse
Unit 8b: GraphBacktestRequest / GraphBacktestResponse
W4 4.A: InspectRequest / InspectResponse, PreviewRequest / PreviewResponse
W5 5.B: GraphGroupResult, GraphCombinedResult, GraphBacktestGroupedResponse
"""
from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

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


class GraphGroupResult(BaseModel):
    """One Output Group's backtest (plan D7, W5 contract).

    node_id is None and path "/" for the implicit group "main" (a graph
    with no output_group).  capital is the group's share of the initial
    capital (initial_capital * weight / sum of weights).  The summary also
    carries open_position and exit_connected.  baseline_curve is the
    group's buy-and-hold on its own capital.
    """
    name: str
    node_id: Optional[str] = None
    path: str
    symbol: str
    interval: str
    direction: Literal["long", "short", "regime_switch"]
    weight: float
    capital: float
    summary: dict
    trades: list[dict]
    equity_curve: list[dict]
    baseline_curve: list[dict] = Field(default_factory=list)


class GraphCombinedResult(BaseModel):
    """Every group together (plan D7): the summed equity on the union of
    the groups' bars (each group forward-filled).

    The summary has the usual keys (initial_capital, final_value,
    total_return_pct, buy_hold_return_pct, max_drawdown_pct, sharpe_ratio,
    num_trades, win_rate_pct and the gain/loss stats over every group's
    trades) plus:
      exposure_pct       : share of bars where any group holds a position;
      gross_deployed_pct : average share of the combined equity held in
                           positions (shares times close, longs and shorts
                           alike).
    """
    summary: dict
    equity_curve: list[dict]
    baseline_curve: list[dict] = Field(default_factory=list)


class GraphBacktestGroupedResponse(GraphBacktestResponse):
    """A graph backtest with its per-group and combined results (W5).

    The four legacy keys are kept: with exactly one group they are that
    group's results; with more, summary / equity_curve / baseline_curve are
    the combined ones and trades is empty (each group's trades are in
    groups[i].trades).  run_graph_backtest still returns the plain
    four-key GraphBacktestResponse (bot code and the parity tests use it).
    """
    groups: list[GraphGroupResult] = Field(default_factory=list)
    combined: Optional[GraphCombinedResult] = None


class GraphBacktestRouteResponse(GraphBacktestGroupedResponse):
    """What POST /api/nodebuilder/backtest sends: the grouped backtest plus
    the id of its cook in the editor's cook cache, for /inspect and
    /preview (plan D6).  cook_id is None when the cook was not kept (too
    big for the cache)."""
    cook_id: Optional[str] = None


# ---------------------------------------------------------------------------
# W4 4.A: the wire inspector and node previews (plan Wave 4 contracts)
# ---------------------------------------------------------------------------


class CookWindow(BaseModel):
    """The data a cook runs over: the sidebar's ticker, dates, interval and
    source (D11).  Used to cook again when the cook cache has no entry."""
    ticker: str
    start: str
    end: str
    interval: str = "1d"
    source: str = "yahoo"


class InspectTarget(BaseModel):
    """What to inspect: a node (its output stream) or a wire (the whole
    output stream of the wire's source node).  Exactly one is set."""
    node_id: Optional[str] = None
    wire_id: Optional[str] = None

    @model_validator(mode="after")
    def _one_of(self) -> "InspectTarget":
        if (self.node_id is None) == (self.wire_id is None):
            raise ValueError("target needs exactly one of node_id or wire_id")
        return self


class InspectFilter(BaseModel):
    """Only rows where *attr* passes *op* (gt and lt compare with value)."""
    attr: str
    op: Literal["is_true", "is_false", "gt", "lt", "not_nan"]
    value: Optional[float] = None

    @model_validator(mode="after")
    def _value_for_compare(self) -> "InspectFilter":
        if self.op in ("gt", "lt") and self.value is None:
            raise ValueError(f"filter op {self.op} needs a value")
        return self


class InspectRequest(BaseModel):
    """Body of POST /api/nodebuilder/inspect.

    graph is a plain dict here: the route builds the Graph itself, so a bad
    graph gets the plan 4.4 400 shape rather than a 422.
    """
    cook_id: Optional[str] = None
    graph: Optional[dict[str, Any]] = None
    window: Optional[CookWindow] = None
    target: InspectTarget
    attrs: Optional[list[str]] = None
    offset: int = Field(0, ge=0)
    limit: int = Field(500, ge=1, le=2000)
    around_time: Optional[Union[int, float, str]] = None
    filter: Optional[InspectFilter] = None


class InspectColumn(BaseModel):
    name: str
    dtype: str            # "time" | "float" | "bool"
    written_by: Optional[str] = None


class InspectDetail(BaseModel):
    name: str
    dtype: str
    value: Union[bool, int, float, str, None]
    written_by: Optional[str] = None


class InspectResponse(BaseModel):
    """Response of POST /api/nodebuilder/inspect (documentation model: the
    route sends the same shape as a plain JSON response)."""
    cook_id: str
    cache: Literal["hit", "miss"]
    stream_schema: int
    columns: list[InspectColumn]
    detail: list[InspectDetail]
    prims: list[Any]
    read_by_consumer: Optional[list[str]] = None
    time: list[Union[str, int]]
    rows: list[list[Union[bool, float, None]]]
    total: int
    # Every bar of the cook, before the filter; equals total when no filter.
    total_unfiltered: int
    offset: int
    stats: dict[str, dict[str, Any]]
    # The cook_id names a live cache entry (False: the cook was too big to
    # keep, so send the graph and window with every request).
    kept: bool = True
    # The fetch failed: this is the last good cook of the same graph and window.
    stale_data: bool = False


class PreviewRequest(BaseModel):
    """Body of POST /api/nodebuilder/preview."""
    cook_id: Optional[str] = None
    graph: Optional[dict[str, Any]] = None
    window: Optional[CookWindow] = None
    node_ids: Optional[list[str]] = None
    points: int = Field(96, ge=8, le=512)


class PreviewNode(BaseModel):
    attr: str
    kind: Literal["line", "bool"]
    min: Optional[float] = None
    max: Optional[float] = None
    nan_count: Optional[int] = None
    true_pct: Optional[float] = None
    values: list[Optional[float]]


class PreviewResponse(BaseModel):
    """Response of POST /api/nodebuilder/preview (documentation model)."""
    cook_id: str
    nodes: dict[str, PreviewNode]
    kept: bool = True
    stale_data: bool = False

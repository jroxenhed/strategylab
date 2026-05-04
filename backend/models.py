"""Shared Pydantic models used across backtest, bot_manager, and trading routes."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator
from signal_engine import Rule


class TrailingStopConfig(BaseModel):
    type: str = "pct"               # "pct" | "atr"
    value: float = 5.0              # % below peak (pct), or ATR multiplier (atr)
    source: str = "high"            # "high" | "close" — which price updates the peak
    activate_on_profit: bool = False  # only start trailing once profit threshold is reached
    activate_pct: float = 0.0        # min profit % required before trailing starts (0 = any profit)


class DynamicSizingConfig(BaseModel):
    enabled: bool = False
    consec_sls: int = 2             # number of consecutive qualifying stops before reducing size
    reduced_pct: float = 25.0       # position size % to use when triggered
    trigger: str = "sl"             # "sl" | "tsl" | "both" — which exit reason(s) increment the counter


class SkipAfterStopConfig(BaseModel):
    enabled: bool = False
    count: int = 1                  # number of entries to skip after a qualifying stop
    trigger: str = "sl"             # "sl" | "tsl" | "both"


class TradingHoursConfig(BaseModel):
    enabled: bool = False
    start_time: str = "09:30"
    end_time: str = "16:00"
    skip_ranges: list[str] = []     # ET time ranges to skip, e.g. ["12:00-13:00", "15:45-16:00"]


class RegimeConfig(BaseModel):
    enabled: bool = False
    timeframe: str = "1d"
    indicator: str = "ma"
    indicator_params: dict = Field(default_factory=lambda: {"period": 200, "type": "sma"})
    condition: str = "above"        # above | below | rising | falling
    min_bars: int = 3               # consecutive bars required before regime flips
    on_flip: str = "close_only"     # close_only | close_and_reverse | hold
    # B28: full rule set path (when non-empty, overrides single-indicator path above)
    rules: list[Rule] = Field(default_factory=list)
    logic: str = "AND"              # AND | OR


class StrategyRequest(BaseModel):
    ticker: str
    start: str = "2023-01-01"
    end: str = "2024-01-01"
    interval: str = "1d"
    buy_rules: list[Rule]
    sell_rules: list[Rule]
    buy_logic: str = "AND"   # AND | OR
    sell_logic: str = "AND"
    # B23: dual rule sets for regime active (long) vs inactive (short)
    long_buy_rules: Optional[list[Rule]] = None
    long_sell_rules: Optional[list[Rule]] = None
    long_buy_logic: str = "AND"
    long_sell_logic: str = "AND"
    short_buy_rules: Optional[list[Rule]] = None
    short_sell_rules: Optional[list[Rule]] = None
    short_buy_logic: str = "AND"
    short_sell_logic: str = "AND"
    initial_capital: float = 10000.0
    position_size: float = 1.0   # fraction of capital per trade (0.01–1.0)
    stop_loss_pct: Optional[float] = None  # e.g. 5.0 means sell if price drops 5% from entry
    trailing_stop: Optional[TrailingStopConfig] = None
    max_bars_held: Optional[int] = None
    slippage_bps: float = Field(default=2.0, ge=0.0)   # unsigned cost per leg, bps
    commission_pct: float = 0.0  # e.g. 0.1 means 0.1% fee per trade
    per_share_rate: float = 0.0   # commission-free default (Alpaca US equities); set 0.0035 for IBKR Fixed
    min_per_order: float = 0.0    # commission-free default (Alpaca US equities); set 0.35 for IBKR Fixed
    borrow_rate_annual: float = 0.5  # % per year, only applied when direction == "short"
    dynamic_sizing: Optional[DynamicSizingConfig] = None
    skip_after_stop: Optional[SkipAfterStopConfig] = None
    trading_hours: Optional[TradingHoursConfig] = None
    source: str = "yahoo"
    direction: str = "long"  # "long" | "short"
    debug: bool = False
    extended_hours: bool = False
    regime: Optional[RegimeConfig] = None
    # B25: per-direction settings (only used when b23_mode is active)
    long_stop_loss_pct: Optional[float] = None
    short_stop_loss_pct: Optional[float] = None
    long_trailing_stop: Optional[TrailingStopConfig] = None
    short_trailing_stop: Optional[TrailingStopConfig] = None
    long_max_bars_held: Optional[int] = None
    short_max_bars_held: Optional[int] = None
    long_position_size: Optional[float] = None
    short_position_size: Optional[float] = None

    @field_validator('position_size')
    @classmethod
    def clamp_position_size(cls, v: float) -> float:
        return max(0.01, min(1.0, v))

    @field_validator('long_position_size', 'short_position_size', mode='before')
    @classmethod
    def clamp_dir_position_size(cls, v):
        if v is None:
            return v
        return max(0.01, min(1.0, v))

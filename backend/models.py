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


class StrategyRequest(BaseModel):
    ticker: str
    start: str = "2023-01-01"
    end: str = "2024-01-01"
    interval: str = "1d"
    buy_rules: list[Rule]
    sell_rules: list[Rule]
    buy_logic: str = "AND"   # AND | OR
    sell_logic: str = "AND"
    initial_capital: float = 10000.0
    position_size: float = 1.0   # fraction of capital per trade (0.01–1.0)
    stop_loss_pct: Optional[float] = None  # e.g. 5.0 means sell if price drops 5% from entry
    trailing_stop: Optional[TrailingStopConfig] = None
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

    @field_validator('position_size')
    @classmethod
    def clamp_position_size(cls, v: float) -> float:
        return max(0.01, min(1.0, v))

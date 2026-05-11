"""Shared Pydantic models used across backtest, bot_manager, and trading routes."""

import re
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field, field_validator, BeforeValidator
from signal_engine import Rule

LogicField = Annotated[Literal['AND', 'OR'], BeforeValidator(lambda v: v.upper() if isinstance(v, str) else v)]
DirectionField = Annotated[Literal['long', 'short'], BeforeValidator(lambda v: v.lower().strip() if isinstance(v, str) else v)]

_RULE_LIST_CAP = 100  # F128: O(n_rules × n_bars) guard — same cap for all primary rule lists
BoundedRuleList = Annotated[list[Rule], Field(max_length=_RULE_LIST_CAP)]
OptionalBoundedRuleList = Annotated[Optional[list[Rule]], Field(default=None, max_length=_RULE_LIST_CAP)]

# Allowlist: must start with an alphanumeric, then up to 19 chars from
# [A-Z0-9.-]. Covers BRK.B, BF-B, AAPL while rejecting '..', '.env', '-A',
# and anything containing whitespace, control chars, or shell metacharacters
# (F38 log-injection, F85 character allowlist). Index symbols like ^GSPC are
# intentionally excluded — the codebase only deals with equity / ETF tickers.
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,19}$")


def normalize_symbol(v: object) -> str:
    """Strict per-symbol normalize+validate. Strips, uppercases, regex-checks.

    Raises ValueError on empty / oversized / disallowed-characters input.
    Use via SymbolField in Pydantic models, or call directly for path params.
    """
    if not isinstance(v, str):
        raise ValueError("symbol must be a string")
    s = v.strip().upper()
    if not s:
        raise ValueError("symbol must not be empty")
    if len(s) > 20:
        raise ValueError(f"symbol too long (max 20 chars): {s[:20]!r}...")
    if not _SYMBOL_RE.fullmatch(s):
        raise ValueError(f"invalid symbol characters: {s!r}")
    return s


SymbolField = Annotated[str, BeforeValidator(normalize_symbol)]


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
    # F128: cap at 50 — regime filter, smaller budget than primary strategy (100)
    rules: list[Rule] = Field(default_factory=list, max_length=50)
    logic: LogicField = "AND"        # AND | OR


class StrategyRequest(BaseModel):
    ticker: SymbolField
    start: str = "2023-01-01"
    end: str = "2024-01-01"
    interval: str = "1d"
    # F128: bound O(n_rules × n_bars) per backtest — mirrors QuickBacktestRequest cap (F102)
    buy_rules: BoundedRuleList
    sell_rules: BoundedRuleList
    buy_logic: LogicField = "AND"   # AND | OR
    sell_logic: LogicField = "AND"
    # B23: dual rule sets for regime active (long) vs inactive (short)
    long_buy_rules: OptionalBoundedRuleList
    long_sell_rules: OptionalBoundedRuleList
    long_buy_logic: LogicField = "AND"
    long_sell_logic: LogicField = "AND"
    short_buy_rules: OptionalBoundedRuleList
    short_sell_rules: OptionalBoundedRuleList
    short_buy_logic: LogicField = "AND"
    short_sell_logic: LogicField = "AND"
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
    direction: DirectionField = "long"
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

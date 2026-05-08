"""Slippage cost/bias helpers + modeled-value policy.

One module owns the sign and unit convention. Every caller (journal writer,
/api/slippage endpoint, bot runner, backtester) routes through these helpers.
Units: basis points everywhere (1 bp = 0.01%).

Conventions:
- slippage_cost_bps: unsigned cost to the trader, always >= 0. Favorable → 0.
- fill_bias_bps: signed deviation, positive = favorable. Diagnostic only.

Side inversion:
- buy / cover: worse when fill > expected.
- sell / short: worse when fill < expected.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Literal

# Tunable policy constants.
SLIPPAGE_DEFAULT_BPS: float = 2.0
SLIPPAGE_MAX_SPREAD_BPS: float = 50.0
SLIPPAGE_MIN_FILLS:   int   = 20
SLIPPAGE_WINDOW:      int   = 50

from journal import JOURNAL_PATH  # journal file lives at backend/data/trade_journal.json

_WORSE_WHEN_ABOVE = {"buy", "cover"}
_WORSE_WHEN_BELOW = {"sell", "short"}


def _raw_bps(side: str, expected: float, fill: float) -> float | None:
    """Signed raw bps where positive = unfavorable (cost).
    Returns None if side unknown or expected is zero/negative."""
    if expected <= 0:
        return None
    s = side.lower()
    if s in _WORSE_WHEN_ABOVE:
        return (fill - expected) / expected * 1e4
    if s in _WORSE_WHEN_BELOW:
        return (expected - fill) / expected * 1e4
    return None


def slippage_cost_bps(side: str, expected: float, fill: float) -> float:
    """Unsigned cost in bps. Always >= 0. Favorable fills return 0."""
    raw = _raw_bps(side, expected, fill)
    if raw is None:
        return 0.0
    return max(0.0, raw)


def fill_bias_bps(side: str, expected: float, fill: float) -> float:
    """Signed deviation in bps. Positive = favorable, negative = unfavorable."""
    raw = _raw_bps(side, expected, fill)
    if raw is None:
        return 0.0
    return -raw  # invert so positive = favorable


@dataclass(frozen=True)
class Fill:
    side: str
    expected: float
    fill: float


@dataclass(frozen=True)
class ModeledSlippage:
    modeled_bps:    float
    measured_bps:   float | None
    fill_bias_bps:  float | None
    fill_count:     int
    source:         Literal["default", "empirical", "spread-derived"]


def _recent_fills(symbol: str, limit: int) -> list[Fill]:
    """Read the trade journal and return up to `limit` most-recent fills for
    `symbol` that have both expected_price and price. Newest last."""
    if not JOURNAL_PATH.exists():
        return []
    try:
        rows = json.loads(JOURNAL_PATH.read_text() or '{"trades":[]}').get("trades", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return []
    sym = symbol.upper()
    out: list[Fill] = []
    for row in rows:
        if (row.get("symbol") or "").upper() != sym:
            continue
        exp = row.get("expected_price")
        px  = row.get("price")
        side = row.get("side")
        if exp is None or px is None or side is None:
            continue
        out.append(Fill(side=side, expected=float(exp), fill=float(px)))
    return out[-limit:]


def _spread_derived_bps(symbol: str) -> float | None:
    """Return half-spread in bps from the active broker's live quote.
    Returns None when: no broker configured, market closed, quote stale
    (outside 09:30-16:00 ET), or spread exceeds the safety cap."""
    try:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        now_et = _dt.now(ZoneInfo("America/New_York"))
        hour_min = now_et.hour * 100 + now_et.minute
        if hour_min < 930 or hour_min >= 1600 or now_et.weekday() >= 5:
            return None
        from broker import get_trading_provider
        provider = get_trading_provider()
        bid, ask = provider.get_latest_quote(symbol)
        if bid > 0 and ask > bid:
            mid = (bid + ask) / 2
            half_spread_bps = (ask - bid) / (2 * mid) * 10_000
            return min(half_spread_bps, SLIPPAGE_MAX_SPREAD_BPS)
    except Exception:
        pass
    return None


def decide_modeled_bps(symbol: str) -> ModeledSlippage:
    """Return the modeled cost the backtester should use for `symbol`,
    plus the diagnostic aggregates over the same window.

    Policy: empirical can make the model WORSE, never better than default."""
    fills = _recent_fills(symbol, limit=SLIPPAGE_WINDOW)
    n = len(fills)

    if n == 0:
        spread = _spread_derived_bps(symbol)
        if spread is not None:
            return ModeledSlippage(
                modeled_bps=max(SLIPPAGE_DEFAULT_BPS, spread),
                measured_bps=None,
                fill_bias_bps=None,
                fill_count=0,
                source="spread-derived",
            )
        return ModeledSlippage(
            modeled_bps=SLIPPAGE_DEFAULT_BPS,
            measured_bps=None,
            fill_bias_bps=None,
            fill_count=0,
            source="default",
        )

    measured = mean(slippage_cost_bps(f.side, f.expected, f.fill) for f in fills)
    bias     = mean(fill_bias_bps(f.side, f.expected, f.fill)    for f in fills)

    if n < SLIPPAGE_MIN_FILLS:
        spread = _spread_derived_bps(symbol)
        if spread is not None:
            return ModeledSlippage(
                modeled_bps=max(SLIPPAGE_DEFAULT_BPS, spread),
                measured_bps=measured,
                fill_bias_bps=bias,
                fill_count=n,
                source="spread-derived",
            )
        return ModeledSlippage(
            modeled_bps=SLIPPAGE_DEFAULT_BPS,
            measured_bps=measured,
            fill_bias_bps=bias,
            fill_count=n,
            source="default",
        )

    return ModeledSlippage(
        modeled_bps=max(SLIPPAGE_DEFAULT_BPS, measured),
        measured_bps=measured,
        fill_bias_bps=bias,
        fill_count=n,
        source="empirical",
    )

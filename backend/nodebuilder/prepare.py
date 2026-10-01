"""Shared data prep for a graph cook: the backtest and the live bot.

Both paths turn a fetched OHLCV frame into the same attrs dict
(``build_graph_attrs``), so a live graph bot sees exactly the series its
backtest saw.  The live bot also needs to know how much history to fetch:
``live_fetch_start`` turns the compiled program's ``required_lookback_bars``
into a calendar start date (plan D5, critic 9).

Pure functions, no I/O.  Building attrs is CPU work: the bot runner calls it
through ``_run_in_executor`` so it never blocks the polling loop.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Optional

import pandas as pd

# The exit sentinel compile() uses when nothing is wired into Exit.
NO_EXIT_ATTR = "@always_false"

# The ATR period the simulator uses for an ATR trailing stop when the graph
# has no ATR of its own (the same default as the rule backtest).
ATR_TRAIL_PERIOD = 14

# Every bot fetched 30 days before W2.  A graph bot never fetches less, so a
# graph with a short lookback behaves exactly as it did.
MIN_LIVE_WINDOW_DAYS = 30

# The live window holds this many times required_lookback_bars.  The
# lookback counts recursive indicators (EMA family, Wilder RSI) at
# RECURSIVE_FACTOR (10) times their period, which on its own leaves a Wilder
# RSI 14 within about 0.012 points of the full-history value.  The extra 1.5x
# keeps the window for a recursive indicator at 15x its period, where it was
# when the lookback used factor 3 and this window factor 5 (measured then on
# SPY daily, Wilder RSI 14: factor 3x3 left 0.011 points, 3x4 7e-4, 3x5
# 4e-5; a cook over exactly the old 3x lookback flipped the last-bar signal
# on 34 of 710 bars).  Rolling indicators match exactly at any factor, so a
# rolling-only graph now fetches 1.5x its lookback instead of 5x.  Fetching
# more bars is cheap; a backtest/live mismatch is not.
LIVE_LOOKBACK_FACTOR = 1.5

# Regular-session bars per trading day, rounded down so the window errs on
# the long side (Yahoo's 1h has 7 bars a day, other feeds 6 or 7).
_BARS_PER_SESSION: dict[str, float] = {
    "1m": 390, "2m": 195, "5m": 78, "15m": 26, "30m": 13,
    "60m": 6, "1h": 6, "90m": 4,
    "1d": 1, "1wk": 1 / 5, "1mo": 1 / 21,
}


def build_graph_attrs(program: Any, df: pd.DataFrame, trailing_stop: Any = None) -> dict:
    """The attrs dict a compiled graph cooks from, built from fetched bars.

    Holds the five bar series (@open @high @low @close @volume; zeros when
    the provider sent no Volume), the always-false exit sentinel, and
    ``atr`` (ATR 14) when the trailing stop is ATR based, because the exit
    checks read attrs["atr"] and would otherwise see 0.

    *program* is not read yet; it stays in the signature so reference
    tickers and HTF frames (W5) can be added here for both paths at once.
    """
    vol_series = df["Volume"] if "Volume" in df.columns else pd.Series(0, index=df.index)
    attrs: dict = {
        "@close": df["Close"],
        "@open": df["Open"],
        "@high": df["High"],
        "@low": df["Low"],
        "@volume": vol_series,
        NO_EXIT_ATTR: pd.Series(0.0, index=df.index, dtype="float64"),
    }
    if trailing_stop is not None and getattr(trailing_stop, "type", None) == "atr":
        from indicators import OHLCVSeries, compute_instance

        ohlcv = OHLCVSeries(close=df["Close"], high=df["High"], low=df["Low"], volume=vol_series)
        attrs["atr"] = compute_instance("atr", {"period": ATR_TRAIL_PERIOD}, ohlcv)["atr"]
    return attrs


def graph_lookback_bars(program: Any, trailing_stop: Any = None) -> int:
    """Bars of history the bot needs before the graph's last bar is good.

    The program's required_lookback_bars, raised to cover the ATR(14) an ATR
    trailing stop reads.
    """
    bars = int(getattr(program, "required_lookback_bars", 0) or 0)
    if trailing_stop is not None and getattr(trailing_stop, "type", None) == "atr":
        bars = max(bars, ATR_TRAIL_PERIOD + 1)
    return bars


def live_window_days(lookback_bars: int, interval: str) -> int:
    """Calendar days to fetch so the frame holds LIVE_LOOKBACK_FACTOR times
    *lookback_bars* bars of *interval*, and never fewer than
    MIN_LIVE_WINDOW_DAYS.

    Bars become trading sessions (plus one for today's partial session),
    sessions become calendar days (7 for every 5, plus 5% for holidays and
    a week of slack).  An unknown interval counts as one bar a session, the
    longest window.
    """
    return max(MIN_LIVE_WINDOW_DAYS, factor_window_days(lookback_bars, interval))


def factor_window_days(lookback_bars: int, interval: str) -> int:
    """Calendar days that hold LIVE_LOOKBACK_FACTOR times *lookback_bars* bars
    of *interval*, with no 30-day floor (0 for no lookback).  The window rule
    live_window_days and the provider-limit check share it."""
    want = LIVE_LOOKBACK_FACTOR * max(int(lookback_bars), 0)
    if want <= 0:
        return 0
    per_session = _BARS_PER_SESSION.get(interval, 1)
    sessions = math.ceil(want / per_session) + 1
    return math.ceil(sessions * 7 / 5 * 1.05) + 7


def short_history_bars(lookback_bars: int) -> int:
    """Fewer bars than this and the live signal may differ from the backtest:
    the same LIVE_LOOKBACK_FACTOR rule as the fetch window (F435 W2 LT-5)."""
    return math.ceil(LIVE_LOOKBACK_FACTOR * max(int(lookback_bars), 0))


def live_fetch_start(
    program: Any,
    interval: str,
    trailing_stop: Any = None,
    today: Optional[date] = None,
    max_days: Optional[int] = None,
) -> str:
    """The first day ("YYYY-MM-DD") of the live bot's fetch window.

    A date, not a bar count: the window then always starts at a session
    open, so time-of-day and session nodes see whole sessions.  *max_days*
    is the most the bot's data provider serves for *interval*
    (shared.provider_max_days); the window never asks for more (F435 W2
    LT-4).  The bot warns when the frame comes back shorter than the graph
    needs.
    """
    today = today or date.today()
    days = live_window_days(graph_lookback_bars(program, trailing_stop), interval)
    if max_days is not None and max_days > 0:
        days = min(days, int(max_days))
    return (today - timedelta(days=days)).isoformat()


def window_cut_by_provider(program: Any, interval: str, trailing_stop: Any,
                           max_days: Optional[int]) -> bool:
    """True when the provider's limit (*max_days*) is shorter than the window
    that holds LIVE_LOOKBACK_FACTOR times the graph's lookback."""
    if max_days is None or max_days <= 0:
        return False
    return factor_window_days(graph_lookback_bars(program, trailing_stop), interval) > max_days

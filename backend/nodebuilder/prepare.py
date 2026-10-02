"""Shared data prep for a graph cook: the backtest and the live bot.

Both paths turn a fetched OHLCV frame into the same attrs dict
(``build_graph_attrs``), so a live graph bot sees exactly the series its
backtest saw.  The live bot also needs to know how much history to fetch:
``live_fetch_start`` turns the compiled program's ``required_lookback_bars``
into a calendar start date (plan D5, critic 9).

Reference Tickers (plan D8): ``build_graph_attrs`` also takes the frames
of the Tickers a group reads besides its primary one, keyed by
(SYMBOL, interval), and keeps them in the attrs dict for the cook
(nodebuilder.trading.align).  ``reference_fetches`` says which frames a
backtest must fetch and from which day, with the lookback padding
``shared.htf_lookback_days`` gives (never less than the bars the frame's
nodes need at its interval).

Pure functions, no I/O.  Building attrs is CPU work: the bot runner calls it
through ``_run_in_executor`` so it never blocks the polling loop.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Optional

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


def build_graph_attrs(
    program: Any,
    df: pd.DataFrame,
    trailing_stop: Any = None,
    frames: Optional[Mapping[tuple, pd.DataFrame]] = None,
    *,
    interval: Optional[str] = None,
) -> dict:
    """The attrs dict a compiled graph cooks from, built from fetched bars.

    Holds the five bar series (@open @high @low @close @volume; zeros when
    the provider sent no Volume), the always-false exit sentinel, and
    ``atr`` (ATR 14) when the trailing stop is ATR based, because the exit
    checks read attrs["atr"] and would otherwise see 0.

    When *program* reads reference Tickers (plan D8), attrs also carries
    their frames under ``align.REFS_KEY``.  *frames* maps (SYMBOL, interval)
    to a fetched frame for each of them; a missing one raises ValueError
    (fetch it first: run.fetch_reference_frames for a backtest, the bot
    runner's gather for a live tick).  *interval* is *df*'s interval when
    the caller knows it; else it is read from the bar spacing.
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
    refs = reference_frames(program, frames, interval=interval)
    if refs is not None:
        from nodebuilder.trading.align import REFS_KEY

        attrs[REFS_KEY] = refs
    return attrs


def reference_frames(program: Any, frames: Optional[Mapping[tuple, pd.DataFrame]],
                     *, interval: Optional[str] = None):
    """The align.ReferenceFrames a cook of *program* needs, from *frames*
    ((SYMBOL, interval) -> DataFrame), or None when the program reads no
    reference Ticker and no Ticker has a prefix.  Raises ValueError for a
    reference frame that is missing or empty."""
    if program is None or not hasattr(program, "steps"):
        return None
    from nodebuilder.trading import align

    roles = align.ticker_roles(program)
    if not roles.needs_domains:
        return None
    given = {align.frame_key(sym, itv): df for (sym, itv), df in (frames or {}).items()}
    out = align.ReferenceFrames(primary_interval=interval)
    for key in roles.keys():
        df = given.get(key)
        if df is None or len(df) == 0:
            who = next(r for r in roles.references if r.key == key)
            raise ValueError(
                f"Reference Ticker {who.node_id!r} reads {key[0]} {key[1]}, but no bars were "
                f"fetched for it." if df is None else
                f"No data for reference Ticker {who.node_id!r} ({key[0]} {key[1]}).")
        out.frames[key] = align.ref_frame(key, df)
    return out


@dataclass(frozen=True)
class ReferenceFetch:
    """One reference frame a backtest fetches: its key, and the first and
    last day of the fetch (the first day padded for the lookback)."""
    symbol: str
    interval: str
    start: str
    end: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.symbol, self.interval)


def reference_padding_days(lookback_bars: int, interval: str) -> int:
    """Calendar days of history to fetch before the backtest's first bar
    for a reference frame whose nodes need *lookback_bars* bars.

    Reuses ``shared.htf_lookback_days`` (the rule regime's padding), so a
    daily reference is padded at least as far as the rule backtest pads its
    regime frame, and never less than the live window rule for that many
    bars at *interval* (a weekly frame needs weeks, not days)."""
    from shared import htf_lookback_days

    bars = max(int(lookback_bars), 1)
    return max(htf_lookback_days("ma", {"period": bars}), factor_window_days(bars, interval))


def reference_fetches(program: Any, start: str, end: str) -> list[ReferenceFetch]:
    """The reference frames a backtest of *program* over start..end needs,
    each once, with its padded first day."""
    from nodebuilder.trading import align

    roles = align.ticker_roles(program)
    if not roles.references:
        return []
    needs = align.reference_needs(program, roles)
    first = datetime.fromisoformat(str(start)[:10])
    out = []
    for key in roles.keys():
        pad = reference_padding_days(needs.get(key, 1), key[1])
        out.append(ReferenceFetch(symbol=key[0], interval=key[1],
                                  start=(first - timedelta(days=pad)).strftime("%Y-%m-%d"),
                                  end=end))
    return out


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

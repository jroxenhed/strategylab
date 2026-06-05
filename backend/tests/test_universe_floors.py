"""Tests for backend/research/universe_floors.py — UNIVERSE_V2 floor conformance.

Charter conformance (NOT tuning): both research configs and the harness's
source-mode null-aggregates path pre-registered universe-v2 (min_price 5.0,
min_avg_volume 500_000 share volume). These tests pin the point-in-time floor
helper that both paths share.

All tests are offline/synthetic — no network, no EDGAR, no real prices.

Helper contract (research.universe_floors.floor_status):
  floor_status(df, as_of) -> one of:
    "ok"            — last close >= 5.0 AND trailing-63td mean volume >= 500_000
                       AND no >10x bar-over-bar ratio in trailing 252td window
    "below_floor"   — last close < 5.0 OR trailing-63td mean volume < 500_000
    "corrupt_frame" — a >10x single-bar price ratio anywhere in trailing 252td
                       window (split-corruption signature, e.g. GXXM $51M entry)

All evaluated strictly from bars <= as_of (no look-ahead).
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from os.path import dirname, abspath

sys.path.insert(0, dirname(dirname(abspath(__file__))))

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _make_df(
    start: date,
    end: date,
    price: float = 50.0,
    volume: int = 1_000_000,
) -> pd.DataFrame:
    """Flat-price, flat-volume daily OHLCV frame over business days."""
    dates = pd.date_range(start, end, freq="B")
    n = len(dates)
    closes = [price] * n
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.001 for c in closes],
            "Low": [c * 0.999 for c in closes],
            "Close": closes,
            "Volume": [volume] * n,
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# min_price floor
# ---------------------------------------------------------------------------

def test_sub5_price_is_below_floor():
    from research.universe_floors import floor_status

    as_of = date(2019, 6, 15)
    df = _make_df(as_of - timedelta(days=600), as_of, price=4.50)
    assert floor_status(df, as_of) == "below_floor"


def test_at_5_price_passes_floor():
    from research.universe_floors import floor_status

    as_of = date(2019, 6, 15)
    df = _make_df(as_of - timedelta(days=600), as_of, price=5.00)
    assert floor_status(df, as_of) == "ok"


def test_penny_entry_is_below_floor():
    """The $0.0112-entry deterioration leak must be rejected."""
    from research.universe_floors import floor_status

    as_of = date(2019, 6, 15)
    df = _make_df(as_of - timedelta(days=600), as_of, price=0.0112)
    assert floor_status(df, as_of) == "below_floor"


# ---------------------------------------------------------------------------
# min_avg_volume floor (share volume, trailing 63td mean)
# ---------------------------------------------------------------------------

def test_thin_volume_is_below_floor():
    from research.universe_floors import floor_status

    as_of = date(2019, 6, 15)
    df = _make_df(as_of - timedelta(days=600), as_of, price=50.0, volume=100_000)
    assert floor_status(df, as_of) == "below_floor"


def test_volume_at_floor_passes():
    from research.universe_floors import floor_status

    as_of = date(2019, 6, 15)
    df = _make_df(as_of - timedelta(days=600), as_of, price=50.0, volume=500_000)
    assert floor_status(df, as_of) == "ok"


def test_volume_evaluated_on_trailing_63td_only():
    """Old thin volume far back must not sink a name that is liquid in the last
    63 trading days — and look-ahead bars (> as_of) must be ignored."""
    from research.universe_floors import floor_status

    as_of = date(2019, 6, 15)
    # Build a frame that is thin early, liquid in the trailing 63td, and has
    # future bars (> as_of) that are thin — those must be ignored.
    early = _make_df(as_of - timedelta(days=600), as_of - timedelta(days=120),
                     price=50.0, volume=10_000)
    recent = _make_df(as_of - timedelta(days=119), as_of, price=50.0, volume=900_000)
    future = _make_df(as_of + timedelta(days=1), as_of + timedelta(days=120),
                      price=50.0, volume=1_000)
    df = pd.concat([early, recent, future])
    assert floor_status(df, as_of) == "ok"


# ---------------------------------------------------------------------------
# corrupt-frame guard (>10x bar-over-bar ratio in trailing 252td window)
# ---------------------------------------------------------------------------

def test_split_corrupt_frame_excluded():
    """A >10x single-bar jump pre-as_of (split-corruption, GXXM $51M signature)
    is excluded as corrupt_frame — reads only PRE-as_of bars."""
    from research.universe_floors import floor_status

    as_of = date(2019, 6, 15)
    df = _make_df(as_of - timedelta(days=600), as_of, price=50.0)
    # Inject a 50x jump ~30 trading days before as_of (within the 252td window).
    closes = df["Close"].tolist()
    spike_idx = len(closes) - 30
    closes[spike_idx] = closes[spike_idx - 1] * 50.0
    df["Close"] = closes
    assert floor_status(df, as_of) == "corrupt_frame"


def test_corrupt_jump_outside_252td_window_ignored():
    """A >10x jump OLDER than the trailing 252td window does not flag the frame."""
    from research.universe_floors import floor_status

    as_of = date(2019, 6, 15)
    df = _make_df(as_of - timedelta(days=900), as_of, price=50.0)
    closes = df["Close"].tolist()
    # Jump near the very start of the (long) frame — well outside trailing 252td.
    closes[5] = closes[4] * 50.0
    df["Close"] = closes
    assert floor_status(df, as_of) == "ok"


def test_corrupt_jump_after_as_of_ignored():
    """A >10x jump AFTER as_of (look-ahead) cannot flag the frame — only pre-as_of
    bars are read."""
    from research.universe_floors import floor_status

    as_of = date(2019, 6, 15)
    df = _make_df(as_of - timedelta(days=400), as_of + timedelta(days=200), price=50.0)
    dates = [d.date() if hasattr(d, "date") else d for d in df.index]
    closes = df["Close"].tolist()
    # Jump well after as_of.
    future_idx = next(i for i, d in enumerate(dates) if d > as_of) + 20
    closes[future_idx] = closes[future_idx - 1] * 50.0
    df["Close"] = closes
    assert floor_status(df, as_of) == "ok"


# ---------------------------------------------------------------------------
# point-in-time: a name below $5 at one as_of but above later appears only later
# ---------------------------------------------------------------------------

def test_floor_point_in_time_price_recovery():
    """A name trading below $5 at an early as_of but above $5 at a later as_of is
    below_floor early and ok later (floors evaluated point-in-time)."""
    from research.universe_floors import floor_status

    early_as_of = date(2018, 6, 15)
    late_as_of = date(2019, 6, 15)

    # Below $5 through early_as_of, then recovers above $5 by late_as_of.
    cheap = _make_df(early_as_of - timedelta(days=600), early_as_of, price=3.0)
    rich = _make_df(early_as_of + timedelta(days=1), late_as_of, price=12.0)
    df = pd.concat([cheap, rich])

    assert floor_status(df, early_as_of) == "below_floor"
    assert floor_status(df, late_as_of) == "ok"


# ---------------------------------------------------------------------------
# Edge: empty / no-bars-before-as_of frame
# ---------------------------------------------------------------------------

def test_empty_frame_below_floor():
    from research.universe_floors import floor_status

    as_of = date(2019, 6, 15)
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    assert floor_status(empty, as_of) == "below_floor"


def test_no_bars_before_as_of_below_floor():
    from research.universe_floors import floor_status

    as_of = date(2010, 1, 1)
    df = _make_df(date(2019, 1, 1), date(2019, 12, 31), price=50.0)
    # as_of precedes all bars → no point-in-time data → below_floor (excluded).
    assert floor_status(df, as_of) == "below_floor"

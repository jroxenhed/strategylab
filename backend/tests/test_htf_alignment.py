"""Tests for multi-TF data foundation in shared.py:
align_htf_to_ltf() and htf_lookback_days().
"""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import math
import pandas as pd
import pytest
from shared import align_htf_to_ltf, htf_lookback_days


# ---------------------------------------------------------------------------
# Test 1: Anti-lookahead — Jan 15 intraday must see Jan 14's value
# ---------------------------------------------------------------------------

def test_no_lookahead_same_day():
    # Daily bar for Jan 15 at midnight ET (= 05:00 UTC) carries value 100
    # Daily bar for Jan 14 at midnight ET (= 05:00 UTC) carries value 90
    # Intraday bars on Jan 15 at 14:30, 14:31 UTC should get 90 (Jan 14's value), not 100
    htf_idx = pd.DatetimeIndex([
        pd.Timestamp('2024-01-14', tz='America/New_York'),
        pd.Timestamp('2024-01-15', tz='America/New_York'),
    ])
    htf = pd.Series([90.0, 100.0], index=htf_idx)

    ltf_idx = pd.DatetimeIndex([
        pd.Timestamp('2024-01-15 14:30:00', tz='UTC'),
        pd.Timestamp('2024-01-15 14:31:00', tz='UTC'),
    ])

    result = align_htf_to_ltf(htf, ltf_idx)

    # Jan 15 intraday must see Jan 14's value (90), not Jan 15's (100)
    assert result.iloc[0] == 90.0
    assert result.iloc[1] == 90.0


# ---------------------------------------------------------------------------
# Test 2: Normal forward mapping — Jan 16 intraday sees Jan 15's value
# ---------------------------------------------------------------------------

def test_next_day_gets_current_daily():
    htf_idx = pd.DatetimeIndex([
        pd.Timestamp('2024-01-15', tz='America/New_York'),
        pd.Timestamp('2024-01-16', tz='America/New_York'),
    ])
    htf = pd.Series([100.0, 105.0], index=htf_idx)

    ltf_idx = pd.DatetimeIndex([
        pd.Timestamp('2024-01-16 14:30:00', tz='UTC'),
    ])

    result = align_htf_to_ltf(htf, ltf_idx)
    # Jan 16 intraday should see Jan 15's value (100)
    assert result.iloc[0] == 100.0


# ---------------------------------------------------------------------------
# Test 3: Weekend gap — Monday intraday gets Friday's value
# ---------------------------------------------------------------------------

def test_weekend_gap():
    # Two daily bars: Fri Jan 12 (value 85), Mon Jan 15 (value 90)
    # No bars on Sat/Sun
    htf_idx = pd.DatetimeIndex([
        pd.Timestamp('2024-01-12', tz='America/New_York'),  # Friday
        pd.Timestamp('2024-01-15', tz='America/New_York'),  # Monday
    ])
    htf = pd.Series([85.0, 90.0], index=htf_idx)

    # Monday Jan 15 intraday at 14:30 UTC
    ltf_idx = pd.DatetimeIndex([pd.Timestamp('2024-01-15 14:30:00', tz='UTC')])

    result = align_htf_to_ltf(htf, ltf_idx)
    # With shift(1): Jan 15 bar carries Fri Jan 12's value (85)
    # merge_asof for Mon 14:30 UTC picks Jan 15 bar (05:00 UTC Jan 15 < 14:30 UTC Jan 15)
    # Jan 15 bar has value 85 (shifted from Jan 12) → Mon intraday gets 85
    assert result.iloc[0] == 85.0


# ---------------------------------------------------------------------------
# Test 4: Empty HTF series returns all NaN
# ---------------------------------------------------------------------------

def test_empty_htf():
    ltf_idx = pd.DatetimeIndex([pd.Timestamp('2024-01-15 14:30:00', tz='UTC')])
    result = align_htf_to_ltf(pd.Series([], dtype=float), ltf_idx)
    assert len(result) == 1
    assert pd.isna(result.iloc[0])


# ---------------------------------------------------------------------------
# Test 5: LTF bars before first HTF bar get NaN (warmup period)
# ---------------------------------------------------------------------------

def test_warmup_period_is_nan():
    # HTF data starts Jan 15; LTF has a bar on Jan 10 (before HTF)
    htf_idx = pd.DatetimeIndex([pd.Timestamp('2024-01-15', tz='America/New_York')])
    htf = pd.Series([100.0], index=htf_idx)

    ltf_idx = pd.DatetimeIndex([pd.Timestamp('2024-01-10 14:30:00', tz='UTC')])
    result = align_htf_to_ltf(htf, ltf_idx)
    assert pd.isna(result.iloc[0])


# ---------------------------------------------------------------------------
# Test 6: htf_lookback_days returns correct values
# ---------------------------------------------------------------------------

def test_htf_lookback_days():
    # 200-period MA: int(200 * 1.5 * 365/252 + 30)
    days = htf_lookback_days("ma", {"period": 200})
    assert days == int(200 * 1.5 * 365 / 252 + 30)

    # Default period 20
    days_default = htf_lookback_days("rsi", {})
    assert days_default == int(20 * 1.5 * 365 / 252 + 30)

    # MACD: max(12, 26, 9) = 26
    days_macd = htf_lookback_days("macd", {})
    assert days_macd == int(26 * 1.5 * 365 / 252 + 30)

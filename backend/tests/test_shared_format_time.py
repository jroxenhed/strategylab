"""Regression tests for shared._format_time_index.

Round-trip equivalence with the scalar _format_time across the datetime64 unit
matrix that yfinance ([s]), Alpaca ([us]), and legacy ([ns]) produce. Caught
in F162 review: pandas 2.0+ preserves source resolution, so a naive
`view('int64') // 1_000_000_000` corrupts every intraday timestamp.
"""
import pandas as pd
import pytest

from shared import _format_time, _format_time_index


def _assert_round_trip(idx: pd.DatetimeIndex, interval: str):
    expected = [_format_time(idx[i], interval) for i in range(len(idx))]
    actual = _format_time_index(idx, interval)
    assert actual == expected, (
        f"_format_time_index diverged from per-bar _format_time at interval={interval}: "
        f"expected {expected[:3]}..., got {actual[:3]}..."
    )


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_format_time_index_intraday_matches_scalar_all_units(unit: str):
    """Intraday: vectorized output must equal per-bar scalar output regardless of
    the underlying datetime64 unit. Reproduces the F162 P0 bug where naive
    integer-view assumed ns resolution and silently corrupted [s]/[us] indexes."""
    base = pd.DatetimeIndex(
        ["2024-03-15 09:30:00", "2024-03-15 09:35:00", "2024-03-15 14:30:00"],
        tz="US/Eastern",
    ).as_unit(unit)
    _assert_round_trip(base, "5m")


def test_format_time_index_intraday_tz_naive():
    """Tz-naive intraday index — both paths must agree."""
    idx = pd.DatetimeIndex(["2024-03-15 14:30:00", "2024-03-15 14:35:00"])
    _assert_round_trip(idx, "1m")


def test_format_time_index_daily_matches_scalar():
    """Daily: returns 'YYYY-MM-DD' strings; both paths must agree."""
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    _assert_round_trip(idx, "1d")


def test_format_time_index_empty():
    """Empty index — must return empty list, not crash."""
    idx = pd.DatetimeIndex([], tz="UTC")
    assert _format_time_index(idx, "5m") == []
    idx_d = pd.DatetimeIndex([])
    assert _format_time_index(idx_d, "1d") == []

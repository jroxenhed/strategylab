"""Tests for exit logic in exits.py — stop-loss and trailing-stop paths.

Covers _evaluate_exit_reason() which handles stop-loss trigger evaluation
and trailing-stop peak/trough tracking + stop-out detection.
"""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import asyncio
import pytest
import pandas as pd
import numpy as np

from bot_manager import BotConfig, BotState
from models import TrailingStopConfig
from signal_engine import Rule
from exits import ExitsMixin


# ---------------------------------------------------------------------------
# Minimal harness: concrete class that mixes in ExitsMixin
# ---------------------------------------------------------------------------

class _ExitHarness(ExitsMixin):
    """Thin concrete class providing only what _evaluate_exit_reason needs."""

    async def _run_in_executor(self, fn, *args):
        """Direct executor — no thread pool."""
        return fn(*args)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    """Return a minimal BotConfig for exit tests."""
    defaults = dict(
        bot_id="test-bot",
        strategy_name="Test",
        symbol="AAPL",
        interval="1d",
        buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
        sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
        allocated_capital=10_000.0,
        broker="alpaca",
        data_source="yahoo",
        direction="long",
    )
    defaults.update(overrides)
    return BotConfig(**defaults)


def _make_state(**overrides):
    """Return a minimal BotState for exit tests."""
    s = BotState()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _make_df(high=101.0, low=99.0, close=100.0, n=2):
    """Return a minimal OHLCV DataFrame with n rows."""
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({
        "Open": [100.0] * n,
        "High": [high] * n,
        "Low": [low] * n,
        "Close": [close] * n,
        "Volume": [1000] * n,
    }, index=idx)


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Stop-loss tests
# ---------------------------------------------------------------------------

def test_stop_loss_long_triggers_when_low_breaches_threshold():
    """Long position: stop_loss_pct=2 triggers when price <= entry * 0.98."""
    harness = _ExitHarness()
    cfg = _make_config(stop_loss_pct=2.0)
    state = _make_state(
        entry_price=100.0,
        entry_bar_count=0,
        position_direction="long",
    )
    df = _make_df(high=99.0, low=97.9, close=97.9)
    # price = 97.9 = entry * 0.979 < entry * 0.98 → triggers
    reason = _run(harness._evaluate_exit_reason(
        cfg, state, price=97.9, df=df, i=1,
        pos_is_short=False, indicators={}, sell_rules=[], is_regime=False,
    ))
    assert reason == "stop_loss", f"Expected 'stop_loss', got {reason!r}"


def test_stop_loss_short_triggers_when_high_breaches_threshold():
    """Short position: stop_loss_pct=2 triggers when price >= entry * 1.02."""
    harness = _ExitHarness()
    cfg = _make_config(stop_loss_pct=2.0, direction="short")
    state = _make_state(
        entry_price=100.0,
        entry_bar_count=0,
        position_direction="short",
    )
    df = _make_df(high=102.5, low=100.0, close=102.5)
    # price = 102.5 >= entry * 1.02 = 102.0 → triggers
    reason = _run(harness._evaluate_exit_reason(
        cfg, state, price=102.5, df=df, i=1,
        pos_is_short=True, indicators={}, sell_rules=[], is_regime=False,
    ))
    assert reason == "stop_loss", f"Expected 'stop_loss', got {reason!r}"


def test_stop_loss_short_circuits_when_disabled():
    """stop_loss_pct=None → stop-loss never triggers even when price drops far."""
    harness = _ExitHarness()
    cfg = _make_config(stop_loss_pct=None)
    state = _make_state(
        entry_price=100.0,
        entry_bar_count=0,
        position_direction="long",
    )
    df = _make_df(high=80.0, low=50.0, close=50.0)  # extreme drop
    reason = _run(harness._evaluate_exit_reason(
        cfg, state, price=50.0, df=df, i=1,
        pos_is_short=False, indicators={}, sell_rules=[], is_regime=False,
    ))
    # No stop-loss, no trailing, no max_bars_held, no sell rules → None
    assert reason is None, f"Expected None when stop_loss disabled, got {reason!r}"


# ---------------------------------------------------------------------------
# Trailing-stop tests
# ---------------------------------------------------------------------------

def test_trailing_stop_long_activates_only_when_pct_threshold_crossed():
    """activate_on_profit=True, activate_pct=1: trail inactive below threshold."""
    harness = _ExitHarness()
    ts = TrailingStopConfig(
        type="pct",
        value=2.0,
        source="close",
        activate_on_profit=True,
        activate_pct=1.0,
    )
    cfg = _make_config(trailing_stop=ts)
    state = _make_state(
        entry_price=100.0,
        entry_bar_count=0,
        trail_peak=None,
        trail_stop_price=None,
        position_direction="long",
    )
    # price = 100.5 < entry * 1.01 = 101.0 → NOT activated
    df = _make_df(high=100.5, low=99.5, close=100.5)
    _run(harness._evaluate_exit_reason(
        cfg, state, price=100.5, df=df, i=1,
        pos_is_short=False, indicators={}, sell_rules=[], is_regime=False,
    ))
    assert state.trail_peak is None, (
        f"trail_peak should be None when activation threshold not crossed, "
        f"got {state.trail_peak}"
    )
    assert state.trail_stop_price is None, (
        f"trail_stop_price should be None when not activated, "
        f"got {state.trail_stop_price}"
    )


def test_trailing_stop_long_tracks_peak():
    """After activation, trail_peak updates as price climbs; stop-out fires on retrace."""
    harness = _ExitHarness()
    ts = TrailingStopConfig(
        type="pct",
        value=2.0,
        source="high",
        activate_on_profit=True,
        activate_pct=1.0,
    )
    cfg = _make_config(trailing_stop=ts)
    state = _make_state(
        entry_price=100.0,
        entry_bar_count=0,
        trail_peak=None,
        trail_stop_price=None,
        position_direction="long",
    )

    # Tick 1: high=103 > entry * 1.01 → activated, trail_peak=103,
    # trail_stop_price = 103 * (1 - 0.02) = 100.94
    df1 = _make_df(high=103.0, low=101.0, close=103.0)
    _run(harness._evaluate_exit_reason(
        cfg, state, price=103.0, df=df1, i=1,
        pos_is_short=False, indicators={}, sell_rules=[], is_regime=False,
    ))
    assert state.trail_peak == 103.0, f"Expected trail_peak=103.0, got {state.trail_peak}"
    expected_stop = 103.0 * 0.98
    assert abs(state.trail_stop_price - expected_stop) < 0.001, (
        f"trail_stop_price={state.trail_stop_price}, expected ~{expected_stop}"
    )

    # Tick 2: price drops to 100.5 < trail_stop_price → stop-out
    df2 = _make_df(high=100.5, low=100.5, close=100.5)
    reason = _run(harness._evaluate_exit_reason(
        cfg, state, price=100.5, df=df2, i=1,
        pos_is_short=False, indicators={}, sell_rules=[], is_regime=False,
    ))
    assert reason == "trailing_stop", f"Expected 'trailing_stop', got {reason!r}"


def test_trailing_stop_short_tracks_trough():
    """Short side: trail_peak tracks lowest low; stop-out fires when price climbs by trail_pct."""
    harness = _ExitHarness()
    ts = TrailingStopConfig(
        type="pct",
        value=2.0,
        source="low",    # source="low" for shorts
        activate_on_profit=False,  # activates immediately
        activate_pct=0.0,
    )
    cfg = _make_config(trailing_stop=ts, direction="short")
    state = _make_state(
        entry_price=100.0,
        entry_bar_count=0,
        trail_peak=None,
        trail_stop_price=None,
        position_direction="short",
    )

    # Tick 1: low=95 (trough), trail_peak=95, trail_stop=95 * 1.02 = 96.9
    df1 = _make_df(high=100.0, low=95.0, close=95.0)
    _run(harness._evaluate_exit_reason(
        cfg, state, price=95.0, df=df1, i=1,
        pos_is_short=True, indicators={}, sell_rules=[], is_regime=False,
    ))
    assert state.trail_peak == 95.0, f"Expected trough trail_peak=95.0, got {state.trail_peak}"
    expected_stop = 95.0 * 1.02
    assert abs(state.trail_stop_price - expected_stop) < 0.001, (
        f"trail_stop_price={state.trail_stop_price}, expected ~{expected_stop}"
    )

    # Tick 2: price=97.5 >= trail_stop_price ~96.9 → stop-out
    df2 = _make_df(high=97.5, low=97.5, close=97.5)
    reason = _run(harness._evaluate_exit_reason(
        cfg, state, price=97.5, df=df2, i=1,
        pos_is_short=True, indicators={}, sell_rules=[], is_regime=False,
    ))
    assert reason == "trailing_stop", f"Expected 'trailing_stop', got {reason!r}"

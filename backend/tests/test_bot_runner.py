"""Tests for bot_runner.py _tick() state transitions.

Covers entry, exit, stop-loss, time-stop, sell-signal, and cooldown paths.
All external dependencies are mocked; asyncio.run() drives the async calls.
"""

from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import asyncio
import unittest
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_df(closes=None, last_bar="2026-01-10"):
    """Build a minimal OHLCV DataFrame with a UTC DatetimeIndex."""
    if closes is None:
        closes = [100.0, 100.0]
    n = len(closes)
    idx = pd.date_range(end=last_bar, periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "Close": closes,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Volume": [1000] * n,
        },
        index=idx,
    )


def make_config(**overrides):
    """Return a minimal valid BotConfig."""
    from bot_manager import BotConfig
    from signal_engine import Rule

    defaults = dict(
        bot_id="bot-test-1",
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


def make_state(**overrides):
    """Return a fresh BotState."""
    from bot_manager import BotState

    state = BotState()
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


class MockManager:
    """Minimal BotManager stub that records save() calls."""

    def __init__(self):
        self.save_calls = 0

    def save(self):
        self.save_calls += 1


def make_order_result(price=100.0, qty=10, order_id="test-order-1", symbol="AAPL", side="buy"):
    from broker import OrderResult

    return OrderResult(
        order_id=order_id,
        symbol=symbol,
        qty=qty,
        side=side,
        status="filled",
        filled_avg_price=price,
        filled_qty=qty,
    )


class MockProvider:
    """Broker protocol stub for tests.

    get_positions call sequence for a successful exit tick:
      call 1 — initial has_position check  → returns _positions
      call 2 — pre-close safety verify     → returns _positions
      call 3+ — post-close confirmation    → returns [] when _post_close_empty=True

    Set _post_close_empty=True for any test that exercises the exit path so the
    post-close verification loop exits immediately.
    """

    def __init__(self, positions=None, order_price=100.0, order_qty=10):
        self._positions = positions if positions is not None else []
        self._order_price = order_price
        self._order_qty = order_qty
        self.submit_order = MagicMock(
            return_value=make_order_result(
                price=order_price, qty=order_qty, symbol="AAPL", side="buy"
            )
        )
        self.get_order = MagicMock(
            return_value=make_order_result(price=order_price, qty=order_qty)
        )
        self.get_latest_quote = MagicMock(return_value=(99.9, 100.1))
        self.close_position = MagicMock(
            return_value=make_order_result(
                price=order_price, qty=order_qty, order_id="close-order-1", side="sell"
            )
        )
        self.get_orders = MagicMock(return_value=[])
        self.cancel_order = MagicMock(return_value=None)
        self._get_positions_call_count = 0
        # When True, call 3+ returns [] so the post-close verify loop exits.
        self._post_close_empty = False

    def get_positions(self):
        self._get_positions_call_count += 1
        # Calls 1 and 2: initial check + pre-close safety verify → real positions.
        # Call 3+: post-close confirmation loop → empty (position cleared).
        if self._post_close_empty and self._get_positions_call_count > 2:
            return []
        return list(self._positions)


# ---------------------------------------------------------------------------
# Context manager helpers that patch all bot_runner module-level dependencies.
# ---------------------------------------------------------------------------

def _base_patches(df, eval_side_effect, provider):
    """Return a list of patch context managers used by every test."""
    return [
        patch("bot_runner._fetch", return_value=df),
        patch("bot_runner.get_trading_provider", return_value=provider),
        patch("bot_runner.eval_rules", side_effect=eval_side_effect),
        patch("bot_runner.compute_indicators", return_value={}),
        patch("bot_runner._log_trade"),
        patch("bot_runner.compute_realized_pnl", return_value=0.0),
        patch("bot_runner.compute_bidirectional_pnl", return_value=0.0),
        patch("bot_runner.notify_entry", new_callable=AsyncMock),
        patch("bot_runner.notify_exit", new_callable=AsyncMock),
        patch("bot_runner.notify_error", new_callable=AsyncMock),
        # Speed up tests: skip all asyncio.sleep delays
        patch("asyncio.sleep", new_callable=AsyncMock),
    ]


async def _run_tick_with_patches(runner, patches):
    """Apply a list of context-manager patches and run runner._tick()."""
    # Stack all context managers
    stack = []
    try:
        for p in patches:
            stack.append(p.__enter__())
        await runner._tick()
    finally:
        for i, p in enumerate(reversed(patches)):
            try:
                p.__exit__(None, None, None)
            except Exception:
                pass


def run_tick(runner, df, eval_side_effect, provider, extra_patches=None):
    """Synchronous wrapper: apply patches and run one _tick()."""
    patches = _base_patches(df, eval_side_effect, provider)
    if extra_patches:
        patches.extend(extra_patches)
    asyncio.run(_run_tick_with_patches(runner, patches))


# Use a direct executor to avoid needing a real thread pool.
async def _direct_executor(self, fn, *args):
    return fn(*args)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTickStateTransitions(unittest.TestCase):

    def _make_runner(self, config=None, state=None):
        from bot_runner import BotRunner

        cfg = config or make_config()
        st = state or make_state()
        mgr = MockManager()
        runner = BotRunner(cfg, st, mgr)
        return runner

    # ------------------------------------------------------------------
    # 1. No entry when outside trading hours and no position
    # ------------------------------------------------------------------
    def test_no_entry_outside_hours(self):
        from bot_runner import BotRunner

        provider = MockProvider()
        runner = self._make_runner()

        with patch.object(BotRunner, "_run_in_executor", _direct_executor):
            with patch.object(runner, "_in_trading_hours", return_value=False):
                df = make_df([100.0, 100.0])
                patches = _base_patches(df, lambda *a, **kw: True, provider)
                asyncio.run(_run_tick_with_patches(runner, patches))

        self.assertIsNone(runner.state.entry_price)
        provider.submit_order.assert_not_called()

    # ------------------------------------------------------------------
    # 2. Entry on buy signal
    # ------------------------------------------------------------------
    def test_entry_on_buy_signal(self):
        from bot_runner import BotRunner

        provider = MockProvider(positions=[], order_price=100.0, order_qty=10)
        runner = self._make_runner()

        # eval_rules returns True (buy signal)
        def eval_side_effect(*args, **kwargs):
            return True

        df = make_df([100.0, 100.0], last_bar="2026-01-10")

        with patch.object(BotRunner, "_run_in_executor", _direct_executor):
            with patch.object(runner, "_in_trading_hours", return_value=True):
                patches = _base_patches(df, eval_side_effect, provider)
                asyncio.run(_run_tick_with_patches(runner, patches))

        self.assertIsNotNone(runner.state.entry_price)
        self.assertEqual(runner.state.entry_price, 100.0)
        self.assertEqual(runner.state.trades_count, 1)
        provider.submit_order.assert_called_once()

    # ------------------------------------------------------------------
    # 3. No second entry when already positioned
    # ------------------------------------------------------------------
    def test_no_entry_when_already_positioned(self):
        from bot_runner import BotRunner

        existing_positions = [
            {"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": 95.0}
        ]
        provider = MockProvider(positions=existing_positions, order_price=100.0)
        # Pre-set entry_price so the bot thinks it has a position
        state = make_state(entry_price=95.0, entry_bar_count=1)
        runner = self._make_runner(state=state)

        # eval_rules always returns False so no exit triggers
        def eval_side_effect(*args, **kwargs):
            return False

        df = make_df([100.0, 100.0], last_bar="2026-01-10")

        with patch.object(BotRunner, "_run_in_executor", _direct_executor):
            with patch.object(runner, "_in_trading_hours", return_value=True):
                patches = _base_patches(df, eval_side_effect, provider)
                asyncio.run(_run_tick_with_patches(runner, patches))

        provider.submit_order.assert_not_called()
        # entry_price should remain (not cleared by a sell)
        self.assertIsNotNone(runner.state.entry_price)

    # ------------------------------------------------------------------
    # 4. Stop-loss exits a long position
    # ------------------------------------------------------------------
    def test_stop_loss_exit_long(self):
        from bot_runner import BotRunner

        # Price at 94 — below stop at 95 (5% below 100 entry)
        closes = [100.0, 94.0]
        df = make_df(closes, last_bar="2026-01-10")

        existing_positions = [
            {"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": 100.0}
        ]
        provider = MockProvider(positions=existing_positions, order_price=94.0)
        # After close_position, get_positions must return empty so post-close
        # verification loop exits.
        provider._post_close_empty = True

        state = make_state(entry_price=100.0, entry_bar_count=1)
        cfg = make_config(stop_loss_pct=5.0)
        runner = self._make_runner(config=cfg, state=state)

        # No sell signal — only stop-loss should fire
        def eval_side_effect(*args, **kwargs):
            return False

        with patch.object(BotRunner, "_run_in_executor", _direct_executor):
            with patch.object(runner, "_in_trading_hours", return_value=True):
                patches = _base_patches(df, eval_side_effect, provider)
                asyncio.run(_run_tick_with_patches(runner, patches))

        provider.close_position.assert_called_once()
        self.assertIsNone(runner.state.entry_price)

    # ------------------------------------------------------------------
    # 5. Sell signal exits a long position
    # ------------------------------------------------------------------
    def test_sell_signal_exit(self):
        from bot_runner import BotRunner

        closes = [100.0, 110.0]
        df = make_df(closes, last_bar="2026-01-11")

        existing_positions = [
            {"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": 100.0}
        ]
        provider = MockProvider(positions=existing_positions, order_price=110.0)
        provider._post_close_empty = True

        state = make_state(entry_price=100.0, entry_bar_count=1)
        runner = self._make_runner(state=state)

        # First eval_rules call is for buy (not called when has_position).
        # The single call in has_position path is sell_rules evaluation.
        # eval_rules is called once for sell — return True to trigger exit.
        eval_calls = []

        def eval_side_effect(*args, **kwargs):
            eval_calls.append(args)
            return True  # any call → sell signal

        with patch.object(BotRunner, "_run_in_executor", _direct_executor):
            with patch.object(runner, "_in_trading_hours", return_value=True):
                patches = _base_patches(df, eval_side_effect, provider)
                asyncio.run(_run_tick_with_patches(runner, patches))

        provider.close_position.assert_called_once()
        self.assertIsNone(runner.state.entry_price)

    # ------------------------------------------------------------------
    # 6. Time-stop exits when max_bars_held reached
    # ------------------------------------------------------------------
    def test_time_stop_exit(self):
        from bot_runner import BotRunner

        closes = [100.0, 100.0]
        df = make_df(closes, last_bar="2026-01-12")

        existing_positions = [
            {"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": 100.0}
        ]
        provider = MockProvider(positions=existing_positions, order_price=100.0)
        provider._post_close_empty = True

        # entry_bar_count starts at 4 → after +1 increment it equals max_bars_held=5
        state = make_state(entry_price=100.0, entry_bar_count=4)
        cfg = make_config(max_bars_held=5)
        runner = self._make_runner(config=cfg, state=state)

        # No sell signal — time-stop only
        def eval_side_effect(*args, **kwargs):
            return False

        with patch.object(BotRunner, "_run_in_executor", _direct_executor):
            with patch.object(runner, "_in_trading_hours", return_value=True):
                patches = _base_patches(df, eval_side_effect, provider)
                asyncio.run(_run_tick_with_patches(runner, patches))

        provider.close_position.assert_called_once()
        self.assertIsNone(runner.state.entry_price)

    # ------------------------------------------------------------------
    # 7. Skip entry during post-stop cooldown
    # ------------------------------------------------------------------
    def test_skip_entry_post_stop_cooldown(self):
        from bot_runner import BotRunner

        provider = MockProvider(positions=[], order_price=100.0)
        state = make_state(skip_remaining=2)
        runner = self._make_runner(state=state)

        # Buy signal fires
        def eval_side_effect(*args, **kwargs):
            return True

        df = make_df([100.0, 100.0], last_bar="2026-01-13")

        with patch.object(BotRunner, "_run_in_executor", _direct_executor):
            with patch.object(runner, "_in_trading_hours", return_value=True):
                patches = _base_patches(df, eval_side_effect, provider)
                asyncio.run(_run_tick_with_patches(runner, patches))

        provider.submit_order.assert_not_called()
        self.assertEqual(runner.state.skip_remaining, 1)


class TestFetchOhlcvAsyncDedup(unittest.TestCase):
    """Verify fetch_ohlcv_async deduplicates concurrent calls for the same key."""

    def test_concurrent_calls_share_one_fetch(self):
        """Two simultaneous fetch_ohlcv_async calls for the same key invoke _fetch only once."""
        import time
        from shared import fetch_ohlcv_async

        call_count = 0

        def slow_fetch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            time.sleep(0.05)  # ensure the Future is still pending when the second coroutine runs
            return make_df()

        async def run():
            with patch("shared._fetch", side_effect=slow_fetch):
                args = ("AAPL", "2026-01-01", "2026-01-10", "1d", "yahoo", False)
                r1, r2 = await asyncio.gather(
                    fetch_ohlcv_async(*args),
                    fetch_ohlcv_async(*args),
                )
            return r1, r2

        r1, r2 = asyncio.run(run())
        self.assertEqual(call_count, 1, "concurrent calls must share one _fetch Future")
        self.assertFalse(r1.empty)
        self.assertFalse(r2.empty)

    def test_sequential_calls_each_fetch(self):
        """Sequential calls (after first Future resolves) each invoke _fetch independently."""
        import time
        from shared import fetch_ohlcv_async

        call_count = 0

        def counting_fetch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return make_df()

        async def run():
            with patch("shared._fetch", side_effect=counting_fetch):
                args = ("AAPL", "2026-01-01", "2026-01-10", "1d", "yahoo", False)
                await fetch_ohlcv_async(*args)
                await fetch_ohlcv_async(*args)  # second call after first completes

        asyncio.run(run())
        self.assertEqual(call_count, 2, "sequential calls must each invoke _fetch")


if __name__ == "__main__":
    unittest.main()

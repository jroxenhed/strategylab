"""Timeout -> failed cook -> bot pause (F435 W7 item 7.C, design note 4.7,
plan W7 "Tests to add").

A Wrangle that sleeps 1.2 s under a bot guard of 0.2 s:
- the tick returns after the guard, not after the sleep: the cook runs in
  the executor under asyncio.wait_for, never on the event loop;
- no order is placed (the broker fails the test on any order);
- the bot pauses with a pause_reason that starts with ``code_timeout`` and
  names the Wrangle;
- another bot's tick on the same loop completes on time, and the loop
  never stalls;
- leaked_cooks rises by 1 while the sleep runs, and falls back when it
  ends by itself (the suite leaks nothing);
- the late result is dropped: when the sleeping cook finally returns, the
  bot does nothing with it.
A bot that holds a position when its cook times out still exits on its
stop (the OPEN POSITION RULE), then pauses.

Money safety (plan 8.4): no bot is started; brokers are fakes; journal and
notifications are mocks; nothing is written.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

import bot_runner
from bot_manager import BotState
from bot_runner import BotRunner
from nodebuilder.code import leaked_cooks
from nodebuilder.code import runtime as code_runtime
from tests.nodebuilder.code_graphs import code_bot, plain_bot_data, wrangle_bot_data
from tests.test_bot_bidirectional_graph import FakeBroker, _Manager
from tests.test_graph_spawn import frame

SLEEP_S = 1.2
GUARD_S = 0.2
SLEEPY = f"import time\ntime.sleep({SLEEP_S})\n@sig: bool = @close > 0\n"

OWN = frame(3)
OTHER = frame(5)


class NoOrders(FakeBroker):
    """A fake broker that fails the test on any order."""

    def submit_order(self, req):
        raise AssertionError("no order may be placed")

    def close_position(self, symbol):
        raise AssertionError("no order may be placed")


@pytest.fixture(autouse=True)
def short_guard(monkeypatch):
    monkeypatch.setattr(code_runtime, "BOT_COOK_TIMEOUT_S", GUARD_S)


def _wait_for_no_leaks(before: int, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while leaked_cooks() > before and time.monotonic() < deadline:
        time.sleep(0.05)


def test_a_slow_wrangle_times_out_pauses_its_bot_and_leaves_the_other_bot_alone():
    slow_cfg = code_bot(wrangle_bot_data(SLEEPY), bot_id="bot-slow")
    slow_cfg.graph.nodes["w"].name = "sleepy"
    fast_cfg = code_bot(plain_bot_data("MSFT"), bot_id="bot-fast", symbol="MSFT")
    slow_state, fast_state = BotState(), BotState()
    slow = BotRunner(slow_cfg, slow_state, _Manager())
    fast = BotRunner(fast_cfg, fast_state, _Manager())
    broker = NoOrders()
    notify_error = AsyncMock()
    returned: list[int] = []
    real_tagged = bot_runner.cook_graph_bar_tagged

    def spy_tagged(tick_id, *args):
        out = real_tagged(tick_id, *args)
        returned.append(tick_id)  # the late result, once the sleep ends
        return out

    before = leaked_cooks()
    seen: dict = {}

    async def fetch(symbol, *a, **k):
        return OWN if symbol == "AAPL" else OTHER

    async def go():
        done: dict[str, float] = {}
        beats: list[float] = []
        t0 = time.perf_counter()

        async def tick(name, runner):
            await runner._tick()
            done[name] = time.perf_counter() - t0

        async def heartbeat():
            while len(done) < 2:
                beats.append(time.perf_counter())
                await asyncio.sleep(0.02)

        await asyncio.gather(tick("slow", slow), tick("fast", fast), heartbeat())
        await asyncio.sleep(0)  # let the alert task run
        seen["leaked_after_tick"] = leaked_cooks()
        seen["done"] = done
        seen["max_gap"] = float(np.diff(beats).max()) if len(beats) > 1 else 0.0

    with patch("bot_runner.fetch_ohlcv_async", side_effect=fetch), \
            patch("bot_runner.get_trading_provider", return_value=broker), \
            patch("bot_runner.notify_error", notify_error), \
            patch("bot_runner.notify_entry", AsyncMock()), \
            patch("bot_runner.notify_exit", AsyncMock()), \
            patch("bot_runner._log_trade", MagicMock()), \
            patch("bot_runner.compute_realized_pnl", return_value=0.0), \
            patch("bot_runner.cook_graph_bar_tagged", spy_tagged):
        asyncio.run(go())  # waits for the sleeping executor thread on close

    # The slow bot's tick ended at the guard, not after the sleep.
    assert seen["done"]["slow"] < SLEEP_S - 0.3, seen["done"]
    # The other bot's tick completed on time; the loop never stalled.
    assert seen["done"]["fast"] < 0.8, seen["done"]
    assert seen["max_gap"] < 0.3, seen["max_gap"]
    # Paused with the code_timeout reason; no order; one alert.
    assert slow_state.status == "error"
    assert slow_state.pause_reason == f"code_timeout: sleepy ran longer than {GUARD_S:g} s"
    assert broker.submitted == [] and broker.closed == []
    assert notify_error.call_count == 1
    assert fast_state.status != "error" and fast_state.pause_reason is None
    # One leak while the sleep ran; none once it ended by itself.
    assert seen["leaked_after_tick"] == before + 1
    _wait_for_no_leaks(before)
    assert leaked_cooks() == before
    # The late result came back and was dropped: nothing changed.
    assert returned == [1]
    assert slow._graph_sample is None and slow_state.last_signal == BotState().last_signal
    assert slow_state.pause_reason.startswith("code_timeout")


def test_a_timeout_while_holding_a_position_still_runs_its_stop():
    price = float(OWN["Close"].iloc[-1])
    state = BotState(entry_price=price * 1.05, entry_bar_count=0, trail_peak=price * 1.05,
                     position_direction="long")
    state.last_bar_time = str(OWN.index[-2])
    broker = FakeBroker(price=price)
    broker.position = {"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": price * 1.05}
    runner = BotRunner(code_bot(wrangle_bot_data(SLEEPY)), state, _Manager())
    log_trade = MagicMock()
    before = leaked_cooks()

    async def go():
        await runner._tick()

    with patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=OWN)), \
            patch("bot_runner.get_trading_provider", return_value=broker), \
            patch("bot_runner.notify_error", AsyncMock()), \
            patch("bot_runner.notify_entry", AsyncMock()), \
            patch("bot_runner.notify_exit", AsyncMock()), \
            patch("bot_runner._log_trade", log_trade), \
            patch("bot_runner.compute_realized_pnl", return_value=0.0), \
            patch("bot_runner.BotRunner._get_fill_price_provider",
                  AsyncMock(return_value=price)):
        asyncio.run(go())

    assert broker.closed == [("AAPL", "long")] and broker.submitted == []
    assert log_trade.call_args.kwargs["reason"] == "stop_loss"
    assert state.status == "error" and state.pause_reason.startswith("code_timeout")
    _wait_for_no_leaks(before)
    assert leaked_cooks() == before


def test_a_cook_inside_the_guard_is_not_touched():
    """A Wrangle that is quick enough cooks normally under the guard."""
    state = BotState()
    broker = FakeBroker(price=float(OWN["Close"].iloc[-1]))
    runner = BotRunner(code_bot(wrangle_bot_data("@sig: bool = @close < 0\n")), state,
                       _Manager())

    async def go():
        await runner._tick()

    with patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=OWN)), \
            patch("bot_runner.get_trading_provider", return_value=broker), \
            patch("bot_runner.notify_error", AsyncMock()):
        asyncio.run(go())
    assert state.status != "error" and runner._code_failure is None
    assert runner._tick_seq == 1 and state.last_bar_time == str(OWN.index[-1])

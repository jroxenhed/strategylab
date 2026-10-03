"""A graph bot whose code fails (F435 W7 item 7.C, decisions-pre "Bot safety
rules", OPEN POSITION RULE agreed with John 2026-10-03).

When the bot's code fails (or times out, test_code_timeout.py):
- no entry and no signal exit is placed, on that bar or later;
- one alert goes out (create_task, never await);
- a flat bot pauses at once with its pause_reason
  (``code_runtime: <node name> line 2: ZeroDivisionError: ...``);
- a bot that holds a position keeps running the price exits (stop,
  trailing stop, time stop) of that position on every tick, from the last
  good plan, until it closes; then it pauses with its pause_reason.

A long and a short with a failing Wrangle both still exit on their stop.

Money safety (plan 8.4): no bot is started (ticks are awaited directly);
the broker is an in-memory fake; journal and notifications are mocks;
nothing is written to disk.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

import bot_runner
from bot_manager import BotState
from bot_runner import BotRunner
from tests.nodebuilder.code_graphs import code_bot, wrangle_bot_data
from tests.test_bot_bidirectional_graph import FakeBroker, _Manager
from tests.test_graph_spawn import frame

OWN = frame(3)
FAILS = "@sig: bool = @close > 0\nx = 1 / 0\n"   # the entry signal would be true


def _runner(code=FAILS, *, direction="long", state=None, **graph_kw):
    cfg = code_bot(wrangle_bot_data(code, **graph_kw), direction=direction)
    return BotRunner(cfg, state or BotState(), _Manager())


def holding(direction: str, entry: float, *, bar_count: int = 0, qty: int = 10):
    """(state, broker) for a position the bot already tracks."""
    state = BotState(entry_price=entry, entry_bar_count=bar_count, trail_peak=entry,
                     position_direction=direction)
    state.last_bar_time = str(OWN.index[-2])
    broker = FakeBroker(price=entry)
    broker.position = {"symbol": "AAPL", "side": direction, "qty": qty, "avg_entry": entry}
    return state, broker


class Tick:
    """Runs ticks with a mocked fetch, broker, journal and notifications,
    and keeps the mocks so a test can count alerts, trades and cooks."""

    def __init__(self, broker):
        self.broker = broker
        self.log_trade = MagicMock()
        self.notify_error = AsyncMock()
        self.cooks = 0

    def __call__(self, runner, df=OWN):
        real_cook = bot_runner.cook_graph_bar

        def counting_cook(*args):
            self.cooks += 1
            return real_cook(*args)

        async def go():
            patches = [
                patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=df)),
                patch("bot_runner.get_trading_provider", return_value=self.broker),
                patch("bot_runner.notify_entry", AsyncMock()),
                patch("bot_runner.notify_exit", AsyncMock()),
                patch("bot_runner.notify_error", self.notify_error),
                patch("bot_runner._log_trade", self.log_trade),
                patch("bot_runner.compute_realized_pnl", return_value=0.0),
                patch("bot_runner.cook_graph_bar", counting_cook),
                patch("asyncio.sleep", AsyncMock()),
            ]
            for p in patches:
                p.start()
            try:
                await runner._tick()
                await asyncio.sleep(0)  # let the alert task run
            finally:
                for p in reversed(patches):
                    p.stop()

        asyncio.run(go())


def _close(df=OWN) -> float:
    return float(df["Close"].iloc[-1])


def _with_bar(df: pd.DataFrame, close: float) -> pd.DataFrame:
    """*df* plus one more daily bar closing at *close*."""
    nxt = df.index[-1] + pd.offsets.BDay(1)
    row = pd.DataFrame({"Open": [close], "High": [close + 0.6], "Low": [close - 0.6],
                        "Close": [close], "Volume": [500_000]}, index=[nxt])
    return pd.concat([df, row])


# ---------------------------------------------------------------------------
# A position keeps its price exits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction, entry_factor", [("long", 1.05), ("short", 0.95)])
def test_a_failing_wrangle_still_exits_on_its_stop(direction, entry_factor):
    """Long: the bar closes ~4.8% under entry; short: ~5% over it.  The 2%
    stop fires; nothing is entered; then the bot pauses."""
    state, broker = holding(direction, _close() * entry_factor)
    broker.price = _close()
    runner = _runner(direction=direction, state=state)
    tick = Tick(broker)
    tick(runner)
    assert broker.closed == [("AAPL", direction)]
    assert broker.submitted == []  # no entry, ever
    assert tick.log_trade.call_args.kwargs["reason"] == "stop_loss"
    assert tick.log_trade.call_args.args[1] == ("sell" if direction == "long" else "cover")
    assert state.entry_price is None
    assert state.status == "error"
    assert state.pause_reason == ("code_runtime: signal_code line 2: ZeroDivisionError: "
                                  "division by zero")
    assert tick.notify_error.call_count == 1


def test_a_position_whose_stop_holds_keeps_its_exits_until_it_closes():
    entry = _close() * 0.999  # just under the close: the 2% stop is far
    state, broker = holding("long", entry)
    runner = _runner(state=state)
    tick = Tick(broker)

    tick(runner)
    assert tick.cooks == 1  # the cook that failed
    assert broker.closed == [] and broker.submitted == []
    assert state.status != "error" and state.pause_reason is None
    # Not in error_message: a restart must auto-resume this bot (its open
    # position needs its stops); resume_was_running skips a bot that has one.
    assert state.error_message is None
    assert any(e["level"] == "WARN" and "price exits of the open position run" in e["msg"]
               for e in state.activity_log)
    assert runner._code_failure is not None
    assert state.entry_bar_count == 1  # the bar counted once toward a time stop

    # The same bar again: nothing new (exits run once per bar).
    tick(runner)
    assert state.entry_bar_count == 1 and broker.closed == []

    # The next bar drops 5% under entry: the stop closes it, then the pause.
    nxt = _with_bar(OWN, entry * 0.95)
    broker.price = entry * 0.95
    tick(runner, nxt)
    assert tick.cooks == 1  # the code never runs again
    assert broker.closed == [("AAPL", "long")] and broker.submitted == []
    assert tick.log_trade.call_args.kwargs["reason"] == "stop_loss"
    assert state.status == "error"
    assert state.pause_reason.startswith("code_runtime: signal_code line 2")
    assert tick.notify_error.call_count == 1  # one alert for the whole episode


def test_a_failing_wrangle_still_runs_the_time_stop():
    state, broker = holding("long", _close() * 0.999, bar_count=1)
    runner = _runner(state=state, stop=None, max_bars=2)
    tick = Tick(broker)
    tick(runner)
    assert broker.closed == [("AAPL", "long")]
    assert tick.log_trade.call_args.kwargs["reason"] == "time_stop"
    assert state.status == "error"


def test_a_failing_wrangle_still_runs_the_trailing_stop():
    from models import TrailingStopConfig

    entry = _close() * 0.999
    state, broker = holding("long", entry)
    state.trail_peak = entry * 1.10  # the trade was 10% up; 3% trail sits at ~6.7% up
    runner = _runner(state=state, stop=None)
    runner.config = runner.config.model_copy(update={
        "trailing_stop": TrailingStopConfig(type="pct", value=3.0)})
    tick = Tick(broker)
    tick(runner)
    assert broker.closed == [("AAPL", "long")]
    assert tick.log_trade.call_args.kwargs["reason"] == "trailing_stop"


# ---------------------------------------------------------------------------
# A flat bot pauses at once
# ---------------------------------------------------------------------------


def test_a_flat_bot_with_failing_code_pauses_at_once_and_enters_nothing():
    state = BotState()
    broker = FakeBroker(price=_close())
    runner = _runner(state=state)
    tick = Tick(broker)
    tick(runner)
    assert broker.submitted == [] and broker.closed == []
    assert state.status == "error"
    assert state.pause_reason == ("code_runtime: signal_code line 2: ZeroDivisionError: "
                                  "division by zero")
    assert tick.notify_error.call_count == 1
    assert any("Code failed" in e["msg"] and "line 2, column 4" in e["msg"]
               for e in state.activity_log)


def test_a_failing_expression_pauses_the_bot_like_failing_code():
    """Level 1 code fails the same way (the RSI's period expression)."""
    from tests.nodebuilder.code_graphs import graph_data, node, wire

    data = graph_data([
        node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
        node("rsi", "rsi", {"period": {"expr": "int('x')"}}, name="fast_rsi"),
        node("lo", "below", {"a": "@rsi", "threshold": 101, "out": "@lo"}),
        node("entry", "entry", {}),
    ], [wire("w1", "t", "rsi"), wire("w2", "rsi", "lo"), wire("w3", "lo", "entry")])
    state = BotState()
    runner = BotRunner(code_bot(data), state, _Manager())
    broker = FakeBroker(price=_close())
    Tick(broker)(runner)
    assert broker.submitted == []
    assert state.pause_reason.startswith("code_runtime: fast_rsi line 1: ValueError")


def test_a_bot_whose_broker_cannot_be_asked_waits_instead_of_pausing():
    """No position check, no flat to confirm: the bot stays in the failed
    state (no entries) and looks again next tick."""
    state = BotState()
    broker = FakeBroker(price=_close())
    broker.get_positions = MagicMock(side_effect=RuntimeError("gateway down"))
    runner = _runner(state=state)
    tick = Tick(broker)
    tick(runner)
    assert state.status != "error" and runner._code_failure is not None
    assert broker.submitted == []
    broker.get_positions = MagicMock(return_value=[])
    tick(runner, _with_bar(OWN, _close()))
    assert state.status == "error" and state.pause_reason.startswith("code_runtime")
    assert tick.notify_error.call_count == 1


def test_working_code_trades_normally():
    """Control: the same bot without the failure enters on its signal."""
    state = BotState()
    broker = FakeBroker(price=_close())
    runner = _runner("@sig: bool = @close > 0\n", state=state)
    tick = Tick(broker)
    tick(runner)
    assert broker.submitted == [("AAPL", "buy", broker.submitted[0][2])]
    assert state.status != "error" and runner._code_failure is None

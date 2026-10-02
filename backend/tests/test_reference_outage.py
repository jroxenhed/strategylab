"""A graph bot whose reference frame is down or behind (F435 W5 LM-1, LM-4,
LM-8, LM-9).

A group can read other Tickers (SPY next to AAPL, plan D8).  When one of
those frames fails to load, comes back empty, or lags the bot's own newest
bar:
- the signal work (entries, the signal exit, the regime) waits: the bar is
  not marked done, so the next tick retries it;
- an open position still gets its price exits (stop, trailing stop, time
  stop) on the bot's own bars, once per bar;
- the tick raises ReferenceUnavailableError, so run() counts it toward
  MAX_CONSEC_ERRORS and alerts (create_task, never await).
Before the fix the tick returned quietly before the exit checks: a short
(no broker stop) held while SPY was down had no stop at all, and nobody was
told.

Also: reference_behind (the freshness rule), the per-reference
short-history and provider-limit warnings, and atr_trailing_stop.

Money safety (plan 8.4): no bot is started (ticks are awaited directly, and
the run() test cancels itself); the broker is an in-memory fake; journal and
notifications are mocks; nothing is written to disk.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

import bot_runner
from bot_manager import BotConfig, BotState
from bot_runner import BotRunner, ReferenceUnavailableError, reference_behind
from models import TrailingStopConfig
from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.models import Graph
from tests.test_bot_bidirectional_graph import FakeBroker, _Manager
from tests.test_graph_spawn import frame, graph_data, group, leg, node

OWN = frame(3)   # AAPL's bars
SPY = frame(4)   # SPY's bars: same dates, other prices


def ref_graph(stop=2.0, max_bars=None) -> Graph:
    """A long group on AAPL whose RSI reads SPY (a reference Ticker), with a
    constant Stop and, optionally, a Time Stop."""
    nodes, wires = leg("a_", "AAPL", "long_leg")
    nodes.append(node("a_spy", "ticker", {"symbol": "SPY", "interval": "1d"}, "long_leg",
                      name="spy"))
    wires = [w if w[0] != "a_w1" else ("a_w1", "a_spy", "a_rsi") for w in wires]
    if stop:
        nodes.append(node("a_stop", "stop", {"constant": stop}, "long_leg", name="stop"))
    if max_bars:
        nodes.append(node("a_ts", "time_stop", {"max_bars": max_bars}, "long_leg", name="tstop"))
    return Graph.model_validate(graph_data([group("long_leg", "long"), *nodes], wires))


def ref_bot(graph=None, **extra) -> BotConfig:
    fields = dict(
        strategy_name="ref", symbol="AAPL", interval="1d", buy_rules=[], sell_rules=[],
        long_buy_rules=None, long_sell_rules=None, short_buy_rules=None, short_sell_rules=None,
        allocated_capital=1000.0, kind="graph", graph=graph or ref_graph(),
        graph_id="g_000000000001", graph_rev=1, graph_group="long_leg",
        graph_direction_mode="long", bot_id="bot-ref",
    )
    fields.update(extra)
    return BotConfig(**fields)


def in_long(entry: float, qty: int = 10, bar_count: int = 0):
    """(state, broker) for a long the bot already tracks."""
    state = BotState(entry_price=entry, entry_bar_count=bar_count, trail_peak=entry)
    state.last_bar_time = str(OWN.index[-2])
    broker = FakeBroker(price=entry)
    broker.position = {"symbol": "AAPL", "side": "long", "qty": qty, "avg_entry": entry}
    return state, broker


def tick(runner, broker, *, own=OWN, spy=SPY, spy_fails=False, cook=None):
    """One _tick.  Returns (raised exception or None, _log_trade mock,
    notify_error mock)."""
    log_trade = MagicMock()
    notify_error = AsyncMock()

    async def fetch(symbol, *a, **k):
        if symbol == "SPY":
            if spy_fails:
                raise RuntimeError("provider down")
            return spy
        return own

    async def go():
        patches = [
            patch("bot_runner.fetch_ohlcv_async", side_effect=fetch),
            patch("bot_runner.get_trading_provider", return_value=broker),
            patch("bot_runner.notify_entry", AsyncMock()),
            patch("bot_runner.notify_exit", AsyncMock()),
            patch("bot_runner.notify_error", notify_error),
            patch("bot_runner._log_trade", log_trade),
            patch("bot_runner.compute_realized_pnl", return_value=0.0),
            patch("asyncio.sleep", AsyncMock()),
        ]
        if cook is not None:
            patches.append(patch("bot_runner.cook_graph_bar", cook))
        for p in patches:
            p.start()
        try:
            await runner._tick()
        except ReferenceUnavailableError as exc:
            return exc
        finally:
            for p in reversed(patches):
                p.stop()
        return None

    return asyncio.run(go()), log_trade, notify_error


def no_cook():
    return MagicMock(side_effect=AssertionError("no cook without the reference frame"))


# ---------------------------------------------------------------------------
# LM-1: a failed reference fetch still runs the price exits
# ---------------------------------------------------------------------------


def test_a_failed_reference_still_runs_the_stop_of_an_open_long():
    price = float(OWN["Close"].iloc[-1])
    state, broker = in_long(entry=price * 1.05)  # the bar is ~4.8% under entry: stop 2% hit
    broker.price = price
    runner = BotRunner(ref_bot(), state, _Manager())
    runner._last_broker_qty = 10
    exc, log_trade, _ = tick(runner, broker, spy_fails=True, cook=no_cook())
    assert isinstance(exc, ReferenceUnavailableError)  # counted toward MAX_CONSEC_ERRORS
    assert "Reference fetch failed for SPY 1d" in str(exc)
    assert broker.closed == [("AAPL", "long")]
    assert log_trade.call_args.args[1] == "sell"
    assert log_trade.call_args.kwargs["reason"] == "stop_loss"
    assert state.entry_price is None
    # An exit ends the bar's work, as on a normal tick.
    assert state.last_bar_time == str(OWN.index[-1])


def test_a_failed_reference_still_runs_the_time_stop():
    price = float(OWN["Close"].iloc[-1])
    state, broker = in_long(entry=price, bar_count=1)
    runner = BotRunner(ref_bot(ref_graph(stop=None, max_bars=2)), state, _Manager())
    runner._last_broker_qty = 10
    exc, log_trade, _ = tick(runner, broker, spy_fails=True, cook=no_cook())
    assert isinstance(exc, ReferenceUnavailableError)
    assert broker.closed == [("AAPL", "long")]
    assert log_trade.call_args.kwargs["reason"] == "time_stop"


def test_price_exits_run_once_per_bar_and_the_bar_is_retried():
    """No exit is hit: every tick of the bar raises and leaves the bar
    open, but the bar counts once toward the time stop; when SPY is back,
    the same bar gets its signal work and is not counted again."""
    price = float(OWN["Close"].iloc[-1])
    state, broker = in_long(entry=price * 0.999)  # above entry: no stop
    runner = BotRunner(ref_bot(), state, _Manager())
    runner._last_broker_qty = 10
    before = state.last_bar_time

    exc, _, _ = tick(runner, broker, spy_fails=True, cook=no_cook())
    assert isinstance(exc, ReferenceUnavailableError)
    assert "stop" in str(exc) and state.entry_bar_count == 1
    assert state.last_bar_time == before and broker.closed == []

    exc, _, _ = tick(runner, broker, spy_fails=True, cook=no_cook())
    assert isinstance(exc, ReferenceUnavailableError)
    assert state.entry_bar_count == 1  # not counted twice
    assert state.last_bar_time == before

    cook = MagicMock(return_value=({}, {"entry": False, "exit": False}))
    exc, _, _ = tick(runner, broker, cook=cook)
    assert exc is None
    assert cook.call_count == 1
    assert state.last_bar_time == str(OWN.index[-1])
    assert state.entry_bar_count == 1
    assert broker.closed == [] and state.entry_price is not None


def test_a_failed_reference_with_no_position_raises_and_retries_the_bar():
    state = BotState()
    broker = FakeBroker()
    runner = BotRunner(ref_bot(), state, _Manager())
    exc, _, _ = tick(runner, broker, spy_fails=True, cook=no_cook())
    assert isinstance(exc, ReferenceUnavailableError)
    assert state.last_bar_time is None
    assert broker.submitted == [] and broker.closed == []


def test_an_empty_reference_frame_counts_as_a_failure():
    state = BotState()
    runner = BotRunner(ref_bot(), state, _Manager())
    exc, _, _ = tick(runner, FakeBroker(), spy=SPY.iloc[0:0], cook=no_cook())
    assert isinstance(exc, ReferenceUnavailableError)
    assert "no bars" in str(exc)


def test_a_same_bar_tick_needs_no_reference():
    """Once the bar is done, a reference that fails on a later tick of the
    same bar is not an error: nothing on that bar needs it."""
    state = BotState()
    state.last_bar_time = str(OWN.index[-1])
    runner = BotRunner(ref_bot(), state, _Manager())
    exc, _, _ = tick(runner, FakeBroker(), spy_fails=True, cook=no_cook())
    assert exc is None


def test_reference_failures_reach_the_alert():
    """run() counts each failing bar; at MAX_CONSEC_ERRORS it alerts through
    create_task (never await) and backs off."""
    state = BotState()
    runner = BotRunner(ref_bot(), state, _Manager())
    notify = AsyncMock()
    sleeps: list[float] = []

    async def fake_sleep(secs, *a, **k):
        sleeps.append(secs)
        if notify.called or len(sleeps) > 20:
            raise asyncio.CancelledError

    async def fetch(symbol, *a, **k):
        if symbol == "SPY":
            raise RuntimeError("provider down")
        return OWN

    async def go():
        patches = [
            patch("bot_runner.fetch_ohlcv_async", side_effect=fetch),
            patch("bot_runner.get_trading_provider", return_value=FakeBroker()),
            patch("bot_runner.cook_graph_bar", no_cook()),
            patch("bot_runner.notify_error", notify),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ]
        for p in patches:
            p.start()
        try:
            with pytest.raises(asyncio.CancelledError):
                await runner.run()
            await asyncio.gather(*[t for t in asyncio.all_tasks()
                                   if t is not asyncio.current_task()], return_exceptions=True)
        finally:
            for p in reversed(patches):
                p.stop()

    asyncio.run(go())
    notify.assert_called_once()
    msg = notify.call_args.kwargs["error_msg"]
    assert "5 consecutive tick failures" in msg and "Reference fetch failed for SPY 1d" in msg


# ---------------------------------------------------------------------------
# LM-4: a reference frame behind the bot's newest bar
# ---------------------------------------------------------------------------


def test_a_reference_one_bar_behind_counts_as_a_failure():
    price = float(OWN["Close"].iloc[-1])
    state, broker = in_long(entry=price * 1.05)
    broker.price = price
    runner = BotRunner(ref_bot(), state, _Manager())
    runner._last_broker_qty = 10
    exc, log_trade, _ = tick(runner, broker, spy=SPY.iloc[:-1], cook=no_cook())
    assert isinstance(exc, ReferenceUnavailableError)
    assert "not up to date" in str(exc)
    # The stop still ran on the bot's own bars.
    assert log_trade.call_args.kwargs["reason"] == "stop_loss"


def test_an_up_to_date_reference_is_cooked_with_the_bots_interval():
    state = BotState()
    runner = BotRunner(ref_bot(), state, _Manager())
    cook = MagicMock(return_value=({}, {"entry": False, "exit": False}))
    exc, _, _ = tick(runner, FakeBroker(), cook=cook)
    assert exc is None
    args = cook.call_args.args
    assert set(args[4]) == {("SPY", "1d")}
    assert args[5] == "1d"  # LM-8: the alignment knows the interval names
    assert state.last_bar_time == str(OWN.index[-1])


def _frame(index) -> pd.DataFrame:
    n = len(index)
    return pd.DataFrame({"Open": [1.0] * n, "High": [1.0] * n, "Low": [1.0] * n,
                         "Close": [1.0] * n, "Volume": [1] * n}, index=index)


def test_reference_behind_same_and_finer_intervals():
    """The exact join: the reference must hold the newest primary bar's own
    period."""
    five = pd.date_range("2024-03-04 09:30", periods=6, freq="5min", tz="America/New_York")
    prim = _frame(five)
    # The review's example: the same frame cut to its first 4 bars.
    assert reference_behind(_frame(five[:4]), "5m", prim, "5m") is not None
    assert reference_behind(_frame(five), "5m", prim, "5m") is None
    # A finer reference (1m) must reach the primary bar's own period.
    one = pd.date_range("2024-03-04 09:30", periods=26, freq="1min", tz="America/New_York")
    assert reference_behind(_frame(one), "1m", prim, "5m") is None          # up to 09:55
    assert reference_behind(_frame(one[:20]), "1m", prim, "5m") is not None  # up to 09:49
    # Daily on one calendar (both stamped 00:00 New York): the same day.
    days = pd.date_range("2024-03-04", "2024-03-08", freq="B", tz="America/New_York")
    assert reference_behind(_frame(days), "1d", _frame(days), "1d") is None
    assert reference_behind(_frame(days[:-1]), "1d", _frame(days), "1d") is not None


def _sessions(days, per_day=7) -> pd.DatetimeIndex:
    """Hourly bars 09:30..15:30 New York on each of *days* (yfinance style)."""
    out = []
    for d in days:
        out += list(pd.date_range(f"{d} 09:30", periods=per_day, freq="1h", tz="America/New_York"))
    return pd.DatetimeIndex(out)


def test_reference_behind_coarser_intervals_use_the_last_ended_bar():
    """The end-time rule: the newest primary bar reads the last reference
    bar that has ended, so the forming bar is not needed; a whole missing
    reference bar is."""
    # Hourly AAPL: all of Tuesday, Wednesday up to 11:30.
    hourly = _frame(_sessions(["2024-03-05"]).append(_sessions(["2024-03-06"], per_day=3)))
    days = pd.date_range("2024-03-01", "2024-03-06", freq="B", tz="America/New_York")
    assert reference_behind(_frame(days), "1d", hourly, "1h") is None       # holds Wednesday
    assert reference_behind(_frame(days[:-1]), "1d", hourly, "1h") is None  # Tuesday ended: fine
    assert reference_behind(_frame(days[:-2]), "1d", hourly, "1h") is not None  # Tuesday missing
    # A UTC-stamped, seven-day daily reference (BTC-like) under ET bars.
    btc = pd.date_range("2024-03-01", "2024-03-06", freq="D", tz="UTC")
    assert reference_behind(_frame(btc), "1d", hourly, "1h") is None
    assert reference_behind(_frame(btc[:-1]), "1d", hourly, "1h") is None   # Tue (UTC) ended
    assert reference_behind(_frame(btc[:-2]), "1d", hourly, "1h") is not None
    # Monday morning: Friday's daily bar is the last one ended (the weekend
    # is no missing bar: the primary's own bars are the calendar).
    monday = _frame(_sessions(["2024-03-01"]).append(_sessions(["2024-03-04"], per_day=2)))
    fri = pd.date_range("2024-02-26", "2024-03-01", freq="B", tz="America/New_York")
    assert reference_behind(_frame(fri), "1d", monday, "1h") is None
    assert reference_behind(_frame(fri[:-1]), "1d", monday, "1h") is not None
    # Weekly under daily.
    daily = _frame(pd.date_range("2024-02-26", "2024-03-08", freq="B", tz="America/New_York"))
    weeks = pd.date_range("2024-02-05", "2024-03-04", freq="W-MON", tz="America/New_York")
    assert reference_behind(_frame(weeks), "1wk", daily, "1d") is None
    assert reference_behind(_frame(weeks[:-1]), "1wk", daily, "1d") is None   # last week ended
    assert reference_behind(_frame(weeks[:-2]), "1wk", daily, "1d") is not None
    # Monthly: calendar months.
    october = _frame(pd.date_range("2024-10-01", "2024-11-01", freq="B", tz="America/New_York"))
    months = pd.DatetimeIndex([pd.Timestamp("2024-09-01", tz="America/New_York"),
                               pd.Timestamp("2024-10-01", tz="America/New_York")])
    assert reference_behind(_frame(months), "1mo", october, "1d") is None
    assert reference_behind(_frame(months[:1]), "1mo", october, "1d") is not None


def test_reference_behind_cannot_tell_without_bar_times():
    plain = pd.DataFrame({"Close": [1.0, 2.0]})
    assert reference_behind(plain, "1d", plain) is None


# ---------------------------------------------------------------------------
# LM-9: warnings for a reference's own window
# ---------------------------------------------------------------------------


def _warns(runner, text):
    return [e for e in runner.state.activity_log if e["level"] == "WARN" and text in e["msg"]]


def test_a_short_reference_frame_warns_once():
    state = BotState()
    runner = BotRunner(ref_bot(), state, _Manager())
    cook = MagicMock(return_value=({}, {"entry": False, "exit": False}))
    tick(runner, FakeBroker(), own=OWN.iloc[:-1], spy=SPY.iloc[-30:-1], cook=cook)
    tick(runner, FakeBroker(), own=OWN, spy=SPY.iloc[-30:], cook=cook)
    assert len(_warns(runner, "Reference SPY 1d wants")) == 1


def test_a_provider_limit_on_a_reference_warns_once():
    state = BotState()
    runner = BotRunner(ref_bot(), state, _Manager())
    cook = MagicMock(return_value=({}, {"entry": False, "exit": False}))
    with patch("bot_runner.provider_max_days", return_value=5):
        tick(runner, FakeBroker(), own=OWN.iloc[:-1], spy=SPY.iloc[:-1], cook=cook)
        tick(runner, FakeBroker(), cook=cook)
    assert len(_warns(runner, "less than reference SPY 1d needs")) == 1


# ---------------------------------------------------------------------------
# The ATR a per-direction ATR trailing stop reads
# ---------------------------------------------------------------------------


def test_atr_trailing_stop_finds_a_per_direction_atr_trail():
    atr = TrailingStopConfig(type="atr", value=2.0)
    pct = TrailingStopConfig(type="pct", value=3.0)
    assert bot_runner.atr_trailing_stop(ref_bot(trailing_stop=pct)) == pct
    assert bot_runner.atr_trailing_stop(ref_bot(trailing_stop=pct, short_trailing_stop=atr)) == atr
    assert bot_runner.atr_trailing_stop(ref_bot()) is None


def test_the_freshness_check_runs_off_the_event_loop():
    """Key Bugs Fixed: CPU work never runs on the polling loop."""
    import threading

    threads: list[int] = []
    real = bot_runner.references_behind

    def spy(*a, **k):
        threads.append(threading.get_ident())
        return real(*a, **k)

    loop_thread: list[int] = []
    real_tick = BotRunner._tick

    async def tick_on_loop(self):
        loop_thread.append(threading.get_ident())
        return await real_tick(self)

    runner = BotRunner(ref_bot(), BotState(), _Manager())
    cook = MagicMock(return_value=({}, {"entry": False, "exit": False}))
    with patch("bot_runner.references_behind", side_effect=spy), \
         patch.object(BotRunner, "_tick", tick_on_loop):
        exc, _, _ = tick(runner, FakeBroker(), cook=cook)
    assert exc is None
    assert threads and loop_thread and threads[0] != loop_thread[0]

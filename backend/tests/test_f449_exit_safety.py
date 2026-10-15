"""F449: two live exit-safety gaps.

Gap 1 (all brokers): when the broker position check failed on a new bar,
the tick logged "Position check failed" and returned after marking the bar
done.  That bar's stop, trailing stop and time stop never ran, the bar was
never retried, and nothing counted toward MAX_CONSEC_ERRORS.  Now:
- a position the bot tracks still gets its price exits from its own state;
  the exit goes through _execute_exit, which sends no close order unless the
  broker confirms the position (so it can never open a reverse position);
- no entry is placed;
- the bar is retried next tick unless the position closed;
- the tick raises PositionCheckError, so run() counts it and alerts.

Review fixes (live money):
- LM-1: a close that went out but is not confirmed is not cancelled and sent
  again on every tick: the bar is done after one close.
- LM-2: the alert says only what is true (a due stop that was not sent is
  never called "checked").
- LM-3: one empty or odd positions reply never books a P&L row, drops a
  tracked position or lets an entry through.  "Gone" needs a fill that
  belongs to the position, or two replies in a row, each from an account
  that holds other positions, that lack it.
- LM-4: the exit journals the qty the broker just confirmed.
- LM-5: one urgent alert per outage per bot; a flat bot only counts.

Gap 2 (IBKR): an entry that carries a stop went out as one bare stop order
on the entry side (a buy stop below the market), so the broker never held a
protective stop.  That order is gone.  With IBKR_BRACKET_STOPS off (the
default) the entry is a plain market order on a SMART contract and the stop
is polled, like an Alpaca short.  With it on, the entry is a bracket: a
market parent with transmit=False and a child stop on the other side with
parentId set and transmit=True, always on a SMART contract.

Money safety: no bot is started (ticks are awaited directly; the run()
test cancels itself); brokers are in-memory fakes or a MagicMock IB; the
journal and notifications are mocks; STRATEGYLAB_DATA_DIR is a temp dir.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot_manager import BotState
from bot_runner import BotRunner, PositionCheckError, ReferenceUnavailableError
from broker import OrderRequest
from tests.test_bot_bidirectional_graph import FakeBroker, _Manager
from tests.test_broker import _make_ibkr_provider
from tests.test_graph_spawn import frame, rule_config
from tests.test_reference_outage import OWN, in_long, no_cook, ref_bot, tick as ref_tick

LAST = str(OWN.index[-1])
PREV = str(OWN.index[-2])
PRICE = float(OWN["Close"].iloc[-1])


@pytest.fixture(autouse=True)
def _temp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))


class FlakyBroker(FakeBroker):
    """A FakeBroker whose get_positions raises for its first `fails` calls
    (every call when fails is None)."""

    def __init__(self, price=PRICE, fails=None):
        super().__init__(price=price)
        self.fails = fails
        self.position_calls = 0

    def get_positions(self):
        self.position_calls += 1
        if self.fails is None or self.position_calls <= self.fails:
            raise ConnectionError("broker down")
        return super().get_positions()


def rule_bot(direction="long", **extra):
    return rule_config(bot_id="bot-f449", direction=direction, stop_loss_pct=2.0, **extra)


def tracked(direction: str, entry: float, broker, qty: int = 10, bar_count: int = 0):
    """A BotState tracking a position, and the same position at the broker."""
    state = BotState(entry_price=entry, entry_bar_count=bar_count, trail_peak=entry,
                     position_direction=direction)
    state.last_bar_time = PREV
    broker.position = {"symbol": "AAPL", "side": direction, "qty": qty, "avg_entry": entry}
    return state


def rule_tick(runner, broker, *, fire=False, df=OWN, in_hours=True):
    """One _tick of a rule bot.  Returns (raised PositionCheckError or None,
    _log_trade mock)."""
    log_trade = MagicMock()

    async def go():
        patches = [
            patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=df)),
            patch("bot_runner.get_trading_provider", return_value=broker),
            patch("bot_runner.compute_indicators", return_value={}),
            patch("bot_runner.eval_rules", return_value=fire),
            patch("bot_runner.notify_entry", AsyncMock()),
            patch("bot_runner.notify_exit", AsyncMock()),
            patch("bot_runner.notify_error", AsyncMock()),
            patch("bot_runner._log_trade", log_trade),
            patch("bot_runner.compute_realized_pnl", return_value=0.0),
            patch.object(BotRunner, "_in_trading_hours", return_value=in_hours),
            patch("asyncio.sleep", AsyncMock()),
        ]
        for p in patches:
            p.start()
        try:
            await runner._tick()
        except PositionCheckError as exc:
            return exc
        finally:
            for p in reversed(patches):
                p.stop()
        return None

    return asyncio.run(go()), log_trade


# The bar's close is ~5% through a 2% stop for each side.
THROUGH_STOP = {"long": PRICE * 1.05, "short": PRICE / 1.05}
CLOSE_SIDE = {"long": "sell", "short": "cover"}


# ---------------------------------------------------------------------------
# Gap 1: a failed position check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction", ["long", "short"])
def test_failed_check_still_runs_the_stop_and_the_exit_confirms_at_the_broker(direction):
    """The check fails once; _execute_exit asks again, the broker confirms,
    the stop exit goes out.  No entry; the tick still raises (counted); the
    exit ends the bar's work, as on a normal tick."""
    broker = FlakyBroker(fails=1)
    state = tracked(direction, THROUGH_STOP[direction], broker)
    runner = BotRunner(rule_bot(direction), state, _Manager())
    runner._last_broker_qty = 10
    exc, log_trade = rule_tick(runner, broker, fire=True)
    assert isinstance(exc, PositionCheckError)
    assert "broker down" in str(exc)
    assert broker.closed == [("AAPL", direction)]
    assert broker.submitted == []  # no entry
    log_trade.assert_called_once()
    assert log_trade.call_args.args[1] == CLOSE_SIDE[direction]
    assert log_trade.call_args.kwargs["reason"] == "stop_loss"
    assert state.entry_price is None
    assert state.last_bar_time == LAST


@pytest.mark.parametrize("direction", ["long", "short"])
def test_failed_check_with_broker_down_sends_no_order_and_retries_the_bar(direction):
    """The broker stays down: the stop is decided but _execute_exit cannot
    confirm the position, so no close order goes out (no reverse position).
    The bar stays open, counts once toward the time stop, and the decided
    exit is sent when the broker is back."""
    broker = FlakyBroker(fails=None)
    state = tracked(direction, THROUGH_STOP[direction], broker)
    runner = BotRunner(rule_bot(direction), state, _Manager())
    runner._last_broker_qty = 10

    exc, log_trade = rule_tick(runner, broker, fire=True)
    assert isinstance(exc, PositionCheckError)
    # LM-2: the text is true: the stop is due and was NOT sent.
    assert "a stop_loss exit IS DUE BUT ITS CLOSE WAS NOT SENT" in str(exc)
    assert "still checked" not in str(exc)
    assert broker.position_calls == 2  # the check, then the exit's own check
    assert broker.closed == [] and broker.submitted == []
    log_trade.assert_not_called()
    assert state.entry_price is not None
    assert state.last_bar_time == PREV  # the bar is retried
    assert state.entry_bar_count == 1
    assert any("Exit stop_loss while the position check is down" in e["msg"]
               for e in state.activity_log)

    exc, _ = rule_tick(runner, broker, fire=True)
    assert isinstance(exc, PositionCheckError)
    assert broker.position_calls == 4  # the exit is tried again
    assert state.entry_bar_count == 1  # not counted twice
    assert state.last_bar_time == PREV

    broker.fails = 0  # the broker is back
    exc, log_trade = rule_tick(runner, broker, fire=False)
    assert exc is None
    assert broker.closed == [("AAPL", direction)]
    assert broker.submitted == []
    log_trade.assert_called_once()
    assert log_trade.call_args.kwargs["reason"] == "stop_loss"
    assert state.entry_price is None
    assert state.last_bar_time == LAST


def test_failed_check_on_a_flat_bot_places_no_entry_then_recovers():
    broker = FlakyBroker(fails=None)
    state = BotState()
    state.last_bar_time = PREV
    runner = BotRunner(rule_bot(), state, _Manager())
    exc, _ = rule_tick(runner, broker, fire=True)
    assert isinstance(exc, PositionCheckError)
    assert "open position" not in str(exc)
    assert broker.submitted == []
    assert state.last_bar_time == PREV

    broker.fails = 0  # recovery: the next good tick runs normally
    exc, log_trade = rule_tick(runner, broker, fire=True)
    assert exc is None
    assert len(broker.submitted) == 1 and broker.submitted[0][1] == "buy"
    assert state.entry_price is not None
    assert state.last_bar_time == LAST
    assert log_trade.call_args.kwargs["reason"] == "entry"


def test_failed_check_without_an_exit_leaves_only_the_signal_work_for_the_retry():
    """No stop is hit: the retried bar does not count again toward the time
    stop and runs normally once the broker is back."""
    broker = FlakyBroker(fails=1)
    state = tracked("long", PRICE * 0.999, broker)  # above entry: no stop
    runner = BotRunner(rule_bot(), state, _Manager())
    runner._last_broker_qty = 10
    exc, _ = rule_tick(runner, broker)
    assert isinstance(exc, PositionCheckError)
    assert "no stop, trailing stop or time stop was due on this bar" in str(exc)
    assert state.entry_bar_count == 1 and state.last_bar_time == PREV

    exc, _ = rule_tick(runner, broker, fire=False)
    assert exc is None
    assert state.entry_bar_count == 1
    assert state.last_bar_time == LAST
    assert broker.closed == [] and state.entry_price is not None


def _run_until(runner, broker, notify, max_sleeps):
    """Drive runner.run() with a failing broker until max_sleeps sleeps.
    Returns the list of sleep lengths."""
    sleeps: list[float] = []

    async def fake_sleep(secs, *a, **k):
        sleeps.append(secs)
        if len(sleeps) >= max_sleeps:
            raise asyncio.CancelledError

    async def go():
        patches = [
            patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=OWN)),
            patch("bot_runner.get_trading_provider", return_value=broker),
            patch("bot_runner.compute_indicators", return_value={}),
            patch("bot_runner.eval_rules", return_value=True),
            patch("bot_runner.notify_error", notify),
            patch("bot_runner.notify_entry", AsyncMock()),
            patch("bot_runner._log_trade", MagicMock()),
            patch.object(BotRunner, "_in_trading_hours", return_value=True),
            patch("asyncio.sleep", side_effect=fake_sleep),
            # A 15 s poll, so it is told apart from the 30 s back-off.
            patch("bot_runner._POLL_MS", 15000),
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
    return sleeps


def test_flat_bot_in_a_long_outage_alerts_once_at_the_limit():
    """LM-5: a flat bot only counts; one alert at MAX_CONSEC_ERRORS, and no
    more for the rest of the outage (three back-off rounds here).  No
    entry is ever placed."""
    broker = FlakyBroker(fails=None)
    state = BotState()
    state.last_bar_time = PREV
    runner = BotRunner(rule_bot(), state, _Manager())
    notify = AsyncMock()
    sleeps = _run_until(runner, broker, notify, max_sleeps=15)
    assert sleeps.count(30) == 3  # three rounds of 5 failures and a back-off
    notify.assert_called_once()
    msg = notify.call_args.kwargs["error_msg"]
    assert "5 consecutive tick failures" in msg and "Position check failed" in msg
    assert "open position" not in msg
    assert broker.submitted == [] and broker.closed == []
    assert state.last_bar_time == PREV


def test_held_bot_with_a_due_stop_alerts_at_once_and_only_once():
    """LM-2 + LM-5: the stop is due but cannot be sent.  The bot alerts on
    the first failing tick with the true text, never again in this outage,
    and keeps polling at the normal pace (no 30 s back-off)."""
    broker = FlakyBroker(fails=None)
    state = tracked("long", THROUGH_STOP["long"], broker)
    runner = BotRunner(rule_bot(), state, _Manager())
    notify = AsyncMock()
    sleeps = _run_until(runner, broker, notify, max_sleeps=15)
    notify.assert_called_once()
    msg = notify.call_args.kwargs["error_msg"]
    assert "IS DUE BUT ITS CLOSE WAS NOT SENT" in msg and "by hand" in msg
    assert 30 not in sleeps
    assert broker.submitted == [] and broker.closed == []
    assert state.entry_price is not None and state.last_bar_time == PREV


def test_a_good_broker_check_ends_the_outage_so_the_next_one_alerts_again():
    """LM-5: the one-alert latch is cleared only by a tick that got a good
    broker position check."""
    script = (["fail"] * 5 + ["ok"] + ["fail"] * 5 + ["same_bar"] + ["fail"] * 5)
    runner = BotRunner(rule_bot(), BotState(), _Manager())
    calls = {"n": 0}

    async def fake_tick():
        step = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        runner._broker_ok_this_tick = False
        if step == "fail":
            raise PositionCheckError("Position check failed: broker down")
        if step == "ok":
            runner._broker_ok_this_tick = True

    notify = AsyncMock()

    async def fake_sleep(secs, *a, **k):
        if calls["n"] >= len(script):
            raise asyncio.CancelledError

    async def go():
        with patch.object(runner, "_tick", fake_tick), \
                patch("bot_runner.notify_error", notify), \
                patch("asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await runner.run()
            await asyncio.gather(*[t for t in asyncio.all_tasks()
                                   if t is not asyncio.current_task()], return_exceptions=True)

    asyncio.run(go())
    # Outage 1 alerts; the good check ends it; outage 2 alerts; a "same
    # bar" tick (no broker check) does not end outage 2, so outage 3 is
    # still the same outage and stays quiet.
    assert notify.call_count == 2


# ---------------------------------------------------------------------------
# Gap 1, second call site: a reference outage and a failed position check
# ---------------------------------------------------------------------------


def test_reference_down_and_check_failing_still_runs_the_stop():
    state, _ = in_long(entry=PRICE * 1.05)
    broker = FlakyBroker(fails=1)
    broker.position = {"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": PRICE * 1.05}
    runner = BotRunner(ref_bot(), state, _Manager())
    runner._last_broker_qty = 10
    exc, log_trade, _ = ref_tick(runner, broker, spy_fails=True, cook=no_cook())
    assert isinstance(exc, ReferenceUnavailableError)
    assert broker.closed == [("AAPL", "long")]
    assert log_trade.call_args.kwargs["reason"] == "stop_loss"
    assert state.last_bar_time == LAST


def test_reference_down_and_broker_down_retries_the_decided_exit():
    state, _ = in_long(entry=PRICE * 1.05)
    broker = FlakyBroker(fails=None)
    broker.position = {"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": PRICE * 1.05}
    runner = BotRunner(ref_bot(), state, _Manager())
    runner._last_broker_qty = 10
    exc, _, _ = ref_tick(runner, broker, spy_fails=True, cook=no_cook())
    assert isinstance(exc, ReferenceUnavailableError)
    assert broker.closed == [] and state.entry_bar_count == 1
    assert state.last_bar_time == PREV

    broker.fails = 0  # broker back, reference still down
    exc, log_trade, _ = ref_tick(runner, broker, spy_fails=True, cook=no_cook())
    assert isinstance(exc, ReferenceUnavailableError)
    assert broker.closed == [("AAPL", "long")]
    assert log_trade.call_args.kwargs["reason"] == "stop_loss"
    assert state.last_bar_time == LAST


# ---------------------------------------------------------------------------
# LM-3: one empty or odd positions reply never books or drops a position
# ---------------------------------------------------------------------------

ENTRY_TIME = "2026-10-16T14:00:00+00:00"
OLD_FILL = {"id": "old-1", "side": "sell", "type": "stop", "qty": "25",
            "filled_avg_price": "50.0", "filled_at": "2026-10-15 15:00:00+00:00"}
NEW_FILL = {"id": "stop-7", "side": "sell", "type": "stop", "qty": "10",
            "filled_avg_price": "97.9", "filled_at": "2026-10-16 14:30:00+00:00"}
OTHER = {"symbol": "MSFT", "side": "long", "qty": 3, "avg_entry": 400.0}


class ScriptedBroker(FakeBroker):
    """get_positions follows a script, one entry per call: "pos" (the
    tracked position, plus any other holdings), "empty" (an empty account),
    "others" (other holdings only, not the tracked one).  The last entry
    repeats.  closed_orders is what get_orders("closed") lists."""

    def __init__(self, script, closed_orders=(), price=PRICE):
        super().__init__(price=price)
        self.script = list(script)
        self.closed_orders = list(closed_orders)
        self.calls = 0

    def get_positions(self):
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if step == "pos":
            return super().get_positions() + [dict(OTHER)]
        if step == "others":
            return [dict(OTHER)]
        return []

    def get_orders(self, status="all", symbols=None, limit=50):
        return list(self.closed_orders) if status == "closed" else []


def held_long(broker, entry=PRICE * 0.999):
    """A tracked long with no stop due, entered at ENTRY_TIME."""
    state = tracked("long", entry, broker)
    state.entry_time = ENTRY_TIME
    return state


def test_one_empty_positions_reply_books_nothing_keeps_the_position_and_counts():
    """The LM-3 probe: the account comes back empty once while the bot holds
    a long, and the closed orders hold an old, unrelated stop fill.  No P&L
    row, the position is kept, no entry, the tick raises (counted) and the
    bar is retried.  The next good reply shows the position: nothing to
    book, nothing lost."""
    broker = ScriptedBroker(["empty", "pos"], closed_orders=[OLD_FILL])
    state = held_long(broker)
    runner = BotRunner(rule_bot(), state, _Manager())
    runner._last_broker_qty = 10

    exc, log_trade = rule_tick(runner, broker, fire=True)
    assert isinstance(exc, PositionCheckError)
    assert "not confirmed" in str(exc) and "no closing fill" in str(exc)
    log_trade.assert_not_called()
    assert state.entry_price == pytest.approx(PRICE * 0.999)
    assert state.position_direction == "long" and state.entry_time == ENTRY_TIME
    assert broker.submitted == [] and broker.closed == []
    assert state.last_bar_time == PREV

    exc, log_trade = rule_tick(runner, broker, fire=False)
    assert exc is None
    log_trade.assert_not_called()
    assert state.entry_price is not None and state.last_bar_time == LAST


def test_an_empty_account_never_confirms_by_count():
    """Three empty replies in a row and no fill of this position: still
    nothing booked and nothing dropped (an empty reply proves nothing)."""
    broker = ScriptedBroker(["empty"], closed_orders=[OLD_FILL])
    state = held_long(broker)
    runner = BotRunner(rule_bot(), state, _Manager())
    for _ in range(3):
        exc, log_trade = rule_tick(runner, broker, fire=True)
        assert isinstance(exc, PositionCheckError)
        log_trade.assert_not_called()
    assert state.entry_price is not None and broker.submitted == []


def test_two_non_empty_replies_without_the_position_confirm_it_gone():
    """Two replies in a row from an account that holds other positions,
    both without the tracked one: it is gone.  Booked as "external" at the
    bar price (the old fill of an earlier trade is not used), then the bot
    is flat."""
    broker = ScriptedBroker(["others"], closed_orders=[OLD_FILL])
    state = held_long(broker)
    runner = BotRunner(rule_bot(), state, _Manager())
    runner._last_broker_qty = 10

    exc, log_trade = rule_tick(runner, broker)
    assert isinstance(exc, PositionCheckError)
    log_trade.assert_not_called()

    exc, log_trade = rule_tick(runner, broker, in_hours=False)
    assert exc is None
    log_trade.assert_called_once()
    assert log_trade.call_args.kwargs["reason"] == "external"
    assert log_trade.call_args.args[3] == PRICE  # not the old fill's 50.0
    assert state.entry_price is None


def test_a_fill_of_this_position_confirms_it_gone_at_once():
    """An empty account, but the closed orders hold a stop fill made after
    this position's entry: booked at once as stop_loss at that fill."""
    broker = ScriptedBroker(["empty"], closed_orders=[NEW_FILL, OLD_FILL])
    state = held_long(broker)
    runner = BotRunner(rule_bot(), state, _Manager())
    exc, log_trade = rule_tick(runner, broker, in_hours=False)
    assert exc is None
    log_trade.assert_called_once()
    args, kwargs = log_trade.call_args
    assert (args[1], args[2], args[3]) == ("sell", 10.0, 97.9)
    assert kwargs["reason"] == "stop_loss"
    assert state.entry_price is None and broker.closed == []


def test_exit_finding_no_position_sends_nothing_books_nothing_and_retries():
    """The tick's check sees the long and the stop is due, but the exit's
    own check gets an empty reply.  Pre-F449 this cleared the position with
    no row; the first F449 draft booked it from any old fill.  Now: no
    order, no row, the position kept, the tick raises (counted) and the bar
    is retried with the same exit (no second time-stop count).  When the
    stop's own fill shows up, it is booked once."""
    broker = ScriptedBroker(["pos", "empty"], closed_orders=[OLD_FILL])
    state = held_long(broker, entry=THROUGH_STOP["long"])
    runner = BotRunner(rule_bot(), state, _Manager())
    runner._last_broker_qty = 10

    exc, log_trade = rule_tick(runner, broker)
    assert isinstance(exc, PositionCheckError)
    assert "Exit not sent" in str(exc) and "IS DUE BUT ITS CLOSE WAS NOT SENT" in str(exc)
    log_trade.assert_not_called()
    assert broker.closed == [] and state.entry_price is not None
    assert state.last_bar_time == PREV and state.entry_bar_count == 1

    broker.closed_orders = [NEW_FILL, OLD_FILL]  # the broker stop's fill appears
    exc, log_trade = rule_tick(runner, broker, in_hours=False)
    assert exc is None
    log_trade.assert_called_once()
    assert log_trade.call_args.kwargs["reason"] == "stop_loss"
    assert log_trade.call_args.args[3] == 97.9
    assert broker.closed == [] and state.entry_price is None


# ---------------------------------------------------------------------------
# LM-1: a close that went out is not resent on every tick
# ---------------------------------------------------------------------------


class UnfilledCloseBroker(FlakyBroker):
    """close_position sends an order that does not fill (a halted symbol or
    a close queued outside regular hours): the position stays."""

    def close_position(self, symbol):
        self.closed.append((symbol, self.position["side"]))
        return SimpleNamespace(order_id=f"c{len(self.closed)}")


def test_failed_check_close_sent_but_unfilled_is_sent_once_per_bar():
    broker = UnfilledCloseBroker(fails=1)
    state = tracked("long", THROUGH_STOP["long"], broker)
    runner = BotRunner(rule_bot(), state, _Manager())
    runner._last_broker_qty = 10

    exc, log_trade = rule_tick(runner, broker)
    assert isinstance(exc, PositionCheckError)
    assert "stop_loss close order c1 sent, its fill is not confirmed yet" in str(exc)
    assert broker.closed == [("AAPL", "long")]
    log_trade.assert_not_called()
    assert state.pending_close_order_id == "c1" and state.entry_price is not None
    assert state.last_bar_time == LAST  # the bar is done: no resend on it

    for _ in range(2):
        exc, _ = rule_tick(runner, broker)
        assert exc is None
    assert broker.closed == [("AAPL", "long")]  # still one close


def test_reference_down_close_sent_but_unfilled_is_sent_once_per_bar():
    state, _ = in_long(entry=PRICE * 1.05)
    broker = UnfilledCloseBroker(fails=0)
    broker.position = {"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": PRICE * 1.05}
    runner = BotRunner(ref_bot(), state, _Manager())
    runner._last_broker_qty = 10
    for _ in range(3):
        exc, _, _ = ref_tick(runner, broker, spy_fails=True, cook=no_cook())
    assert broker.closed == [("AAPL", "long")]
    assert state.last_bar_time == LAST and state.entry_price is not None


# ---------------------------------------------------------------------------
# LM-4: the exit journals the qty the broker just confirmed
# ---------------------------------------------------------------------------


def test_failed_check_exit_journals_the_broker_confirmed_qty():
    broker = FlakyBroker(fails=1)
    state = tracked("long", THROUGH_STOP["long"], broker, qty=6)
    runner = BotRunner(rule_bot(), state, _Manager())
    runner._last_broker_qty = 10  # stale: the broker now holds 6
    exc, log_trade = rule_tick(runner, broker)
    assert isinstance(exc, PositionCheckError)
    log_trade.assert_called_once()
    assert log_trade.call_args.args[2] == 6


# ---------------------------------------------------------------------------
# Gap 2: IBKR bracket entries (mocked ib_insync IB)
# ---------------------------------------------------------------------------


def _ib_trade(order_id=0, status="Submitted"):
    t = MagicMock()
    t.order.orderId = order_id
    t.orderStatus.status = status
    t.orderStatus.avgFillPrice = 0.0
    t.orderStatus.filled = 0.0
    return t


def _bracket_ib():
    ib = MagicMock()
    ib.client.getReqId.side_effect = [101, 102]
    placed = []

    def place(contract, order):
        placed.append((contract, order))
        return _ib_trade(order.orderId)

    ib.placeOrder.side_effect = place
    return ib, placed


def _assert_smart(contract):
    assert (contract.symbol, contract.exchange, contract.currency) == ("AAPL", "SMART", "USD")


@pytest.fixture
def brackets_on(monkeypatch):
    monkeypatch.setenv("IBKR_BRACKET_STOPS", "1")


@pytest.fixture
def brackets_off(monkeypatch):
    monkeypatch.delenv("IBKR_BRACKET_STOPS", raising=False)


@pytest.mark.parametrize("side,stop", [("buy", 98.0), ("sell", 102.0)])
def test_ibkr_entry_with_stop_and_flag_off_is_one_plain_market_order(brackets_off, side, stop):
    """IBKR_BRACKET_STOPS off (the default): the entry is one market order
    on a SMART contract.  No stop order of any kind goes to the broker (the
    old bare stop on the entry side is gone); the bot polls the stop."""
    ib, placed = _bracket_ib()
    provider = _make_ibkr_provider(ib)
    assert provider.supports_short_stop_bracket is False
    result = provider.submit_order(OrderRequest(symbol="AAPL", qty=10, side=side,
                                                order_type="stop", stop_price=stop))
    assert len(placed) == 1
    contract, order = placed[0]
    _assert_smart(contract)
    assert (order.orderType, order.action, order.totalQuantity) == (
        "MKT", "BUY" if side == "buy" else "SELL", 10)
    assert not any(o.orderType == "STP" for _, o in placed)
    ib.client.getReqId.assert_not_called()
    assert result.order_id == str(order.orderId)


def test_ibkr_flag_values(monkeypatch):
    from broker import ibkr_bracket_stops_enabled
    for value, on in [("", False), ("0", False), ("off", False), ("1", True),
                      ("true", True), ("ON", True)]:
        monkeypatch.setenv("IBKR_BRACKET_STOPS", value)
        assert ibkr_bracket_stops_enabled() is on
    monkeypatch.delenv("IBKR_BRACKET_STOPS")
    assert ibkr_bracket_stops_enabled() is False


def test_ibkr_close_with_flag_off_cancels_nothing_and_routes_smart(brackets_off):
    """Flag off: no child stops exist, so the close goes out as before:
    no cancel step, one SMART market order for the position's qty."""
    ib = MagicMock()
    ib.positions.return_value = [_ib_position(10.0)]
    ib.placeOrder.side_effect = lambda contract, order: _ib_trade(201)
    provider = _make_ibkr_provider(ib)
    provider.close_position("AAPL")
    ib.cancelOrder.assert_not_called()
    ib.openTrades.assert_not_called()
    contract, order = ib.placeOrder.call_args.args
    _assert_smart(contract)
    assert (order.orderType, order.action, order.totalQuantity) == ("MKT", "SELL", 10.0)


def test_bot_long_entry_on_ibkr_with_flag_off_is_a_plain_market_order(brackets_off):
    """End to end: a rule bot on IBKR with a stop enters with one SMART
    market order and no stop order."""
    ib, placed = _bracket_ib()
    ib.portfolio.return_value = []
    ib.positions.return_value = []
    provider = _make_ibkr_provider(ib)
    provider._cache_ttl = 0
    state = BotState()
    state.last_bar_time = PREV
    runner = BotRunner(rule_bot(broker="ibkr"), state, _Manager())
    rule_tick(runner, provider, fire=True)
    assert len(placed) == 1
    contract, order = placed[0]
    _assert_smart(contract)
    assert (order.orderType, order.action) == ("MKT", "BUY")


def test_ibkr_long_entry_with_stop_is_a_bracket(brackets_on):
    ib, placed = _bracket_ib()
    provider = _make_ibkr_provider(ib)
    result = provider.submit_order(OrderRequest(symbol="AAPL", qty=10, side="buy",
                                                order_type="stop", stop_price=98.0))
    assert result.order_id == "101"
    assert len(placed) == 2
    (c1, parent), (c2, stop) = placed
    _assert_smart(c1)
    _assert_smart(c2)
    assert (parent.orderType, parent.action, parent.totalQuantity) == ("MKT", "BUY", 10)
    assert parent.orderId == 101 and parent.transmit is False
    assert (stop.orderType, stop.action, stop.totalQuantity, stop.auxPrice) == ("STP", "SELL", 10, 98.0)
    assert stop.orderId == 102 and stop.parentId == 101 and stop.transmit is True
    assert parent.tif == stop.tif == "DAY"


def test_ibkr_short_entry_with_stop_is_a_bracket_with_a_buy_stop_above(brackets_on):
    ib, placed = _bracket_ib()
    provider = _make_ibkr_provider(ib)
    assert provider.supports_short_stop_bracket is True
    provider.submit_order(OrderRequest(symbol="AAPL", qty=5, side="sell",
                                       order_type="stop", stop_price=102.0))
    (c1, parent), (c2, stop) = placed
    _assert_smart(c1)
    _assert_smart(c2)
    assert (parent.orderType, parent.action, parent.transmit) == ("MKT", "SELL", False)
    assert (stop.orderType, stop.action, stop.auxPrice) == ("STP", "BUY", 102.0)
    assert stop.parentId == parent.orderId and stop.transmit is True


def test_ibkr_market_entry_without_stop_is_one_market_order():
    ib, placed = _bracket_ib()
    provider = _make_ibkr_provider(ib)
    provider.submit_order(OrderRequest(symbol="AAPL", qty=10, side="buy"))
    assert len(placed) == 1
    _assert_smart(placed[0][0])
    assert placed[0][1].orderType == "MKT"


def test_ibkr_bracket_cancels_the_parent_when_the_stop_cannot_be_placed(brackets_on):
    ib, placed = _bracket_ib()

    def place(contract, order):
        if order.orderType == "STP":
            raise RuntimeError("Gateway refused the stop")
        placed.append((contract, order))
        return _ib_trade(order.orderId)

    ib.placeOrder.side_effect = place
    provider = _make_ibkr_provider(ib)
    with pytest.raises(RuntimeError):
        provider.submit_order(OrderRequest(symbol="AAPL", qty=10, side="buy",
                                           order_type="stop", stop_price=98.0))
    ib.cancelOrder.assert_called_once_with(placed[0][1])  # the untransmitted parent


def _filled(order_id, action, order_type, price, qty=10):
    t = MagicMock()
    t.contract.symbol = "AAPL"
    t.order.orderId = order_id
    t.order.action = action
    t.order.orderType = order_type
    t.order.totalQuantity = qty
    t.orderStatus.status = "Filled"
    t.orderStatus.avgFillPrice = price
    return t


def test_ibkr_orders_keep_their_order_and_ibkr_type_names():
    """The type-name mapping and newest-first order were held back with
    the bracket (they only matter for booking a filled child stop)."""
    ib = MagicMock()
    ib.reqAllOpenOrdersAsync = AsyncMock()
    ib.trades.return_value = [_filled(101, "BUY", "MKT", 100.0), _filled(102, "SELL", "STP", 97.9)]
    provider = _make_ibkr_provider(ib)
    orders = provider.get_orders("closed", ["AAPL"], 5)
    assert [o["id"] for o in orders] == ["101", "102"]
    assert [o["type"] for o in orders] == ["mkt", "stp"]


def _ib_position(qty):
    p = MagicMock()
    p.contract.symbol = "AAPL"
    p.contract.exchange = "NYSE"  # a position's own contract: never used for orders
    p.position = qty
    return p


def _open_child(action="SELL"):
    child = MagicMock()
    child.contract.symbol = "AAPL"
    child.order.orderId = 102
    child.order.action = action
    done = {"v": False}
    child.isDone.side_effect = lambda: done["v"]
    return child, done


def test_ibkr_close_cancels_the_child_stop_first_and_routes_smart(brackets_on):
    ib = MagicMock()
    ib.positions.return_value = [_ib_position(10.0)]
    child, done = _open_child()
    ib.openTrades.return_value = [child]
    ib.cancelOrder.side_effect = lambda order: done.update(v=True)
    ib.placeOrder.side_effect = lambda contract, order: _ib_trade(201)
    provider = _make_ibkr_provider(ib)
    result = provider.close_position("AAPL")
    ib.cancelOrder.assert_called_once_with(child.order)
    contract, order = ib.placeOrder.call_args.args
    _assert_smart(contract)
    assert (order.orderType, order.action, order.totalQuantity) == ("MKT", "SELL", 10.0)
    assert result.order_id == "201"


def test_ibkr_close_sends_nothing_when_the_child_stop_filled_meanwhile(brackets_on):
    """No double exit: the stop filled while it was being cancelled, so the
    position is gone and no close order goes out (it would open a short)."""
    ib = MagicMock()
    ib.positions.side_effect = [[_ib_position(10.0)], []]
    child, done = _open_child()
    ib.openTrades.return_value = [child]
    ib.cancelOrder.side_effect = lambda order: done.update(v=True)
    provider = _make_ibkr_provider(ib)
    with pytest.raises(ValueError, match="No position"):
        provider.close_position("AAPL")
    ib.placeOrder.assert_not_called()


def test_ibkr_close_all_cancels_child_stops_first(brackets_on):
    ib = MagicMock()
    ib.positions.return_value = [_ib_position(10.0)]
    child, done = _open_child()
    ib.openTrades.return_value = [child]
    ib.cancelOrder.side_effect = lambda order: done.update(v=True)
    provider = _make_ibkr_provider(ib)
    provider.close_all_positions()
    ib.cancelOrder.assert_called_once_with(child.order)
    contract, order = ib.placeOrder.call_args.args
    _assert_smart(contract)
    assert (order.action, order.totalQuantity) == ("SELL", 10.0)


class CapturingBroker(FakeBroker):
    def __init__(self, price=PRICE, short_bracket=False):
        super().__init__(price=price)
        self.requests = []
        if short_bracket:
            self.supports_short_stop_bracket = True

    def submit_order(self, req):
        self.requests.append(req)
        return super().submit_order(req)


@pytest.mark.parametrize("short_bracket", [False, True])
def test_bot_short_entry_sends_a_stop_only_where_the_broker_holds_short_brackets(short_bracket):
    """IBKR (supports_short_stop_bracket): the short carries a buy stop
    above entry.  Alpaca: a plain market short, stops by polling."""
    broker = CapturingBroker(short_bracket=short_bracket)
    state = BotState()
    state.last_bar_time = PREV
    runner = BotRunner(rule_bot("short"), state, _Manager())
    exc, _ = rule_tick(runner, broker, fire=True)
    assert exc is None
    (req,) = broker.requests
    assert req.side == "sell"
    if short_bracket:
        assert req.order_type == "stop"
        assert req.stop_price == round(PRICE * 1.02, 2)
    else:
        assert req.order_type == "market" and req.stop_price is None


def test_bot_long_entry_sends_a_stop_below_entry():
    broker = CapturingBroker()
    state = BotState()
    state.last_bar_time = PREV
    runner = BotRunner(rule_bot("long"), state, _Manager())
    rule_tick(runner, broker, fire=True)
    (req,) = broker.requests
    assert (req.side, req.order_type, req.stop_price) == ("buy", "stop", round(PRICE * 0.98, 2))


def test_ibkr_vanished_position_is_kept_on_one_empty_reply(brackets_on):
    """LM-3 on IBKR: the account comes back empty and IBKR lists no fill
    time, so nothing proves the stop fill belongs to this position.  No
    row, no order, the position is kept and the tick counts.  (Booking a
    filled child stop as stop_loss is one of the conditions for turning
    IBKR_BRACKET_STOPS on.)"""
    ib = MagicMock()
    ib.reqAllOpenOrdersAsync = AsyncMock()
    ib.portfolio.return_value = []  # the position is gone, or the reply is odd
    ib.positions.return_value = []
    ib.trades.return_value = [_filled(101, "BUY", "MKT", 100.0), _filled(102, "SELL", "STP", 97.9)]
    provider = _make_ibkr_provider(ib)
    provider._cache_ttl = 0
    entry = 100.0
    state = BotState(entry_price=entry, trail_peak=entry, position_direction="long",
                     entry_time=ENTRY_TIME)
    state.last_bar_time = str(OWN.index[-3])
    runner = BotRunner(rule_bot(broker="ibkr"), state, _Manager())
    runner._last_broker_qty = 10

    exc, log_trade = rule_tick(runner, provider, df=OWN.iloc[:-1], in_hours=False)
    assert isinstance(exc, PositionCheckError)
    log_trade.assert_not_called()
    assert state.entry_price == entry and state.position_direction == "long"
    ib.placeOrder.assert_not_called()


def test_ibkr_polling_stop_when_the_position_vanished_at_the_exit_sends_nothing(brackets_on):
    """The tick's check still sees the long and the polling stop fires, but
    the exit's own check finds no position: no close order (it could open
    a short), no row, the position kept until a check confirms it."""
    ib = MagicMock()
    ib.reqAllOpenOrdersAsync = AsyncMock()
    pos = MagicMock()
    pos.contract.symbol = "AAPL"
    pos.position = 10.0
    pos.averageCost = 100.0
    pos.marketPrice = pos.marketValue = pos.unrealizedPNL = 0.0
    ib.portfolio.side_effect = [[pos], []]
    ib.positions.return_value = []
    ib.openTrades.return_value = []
    ib.trades.return_value = [_filled(101, "BUY", "MKT", 100.0), _filled(102, "SELL", "STP", 97.9)]
    provider = _make_ibkr_provider(ib)
    provider._cache_ttl = 0
    state = BotState(entry_price=PRICE * 1.05, trail_peak=PRICE * 1.05, position_direction="long")
    state.last_bar_time = PREV
    runner = BotRunner(rule_bot(broker="ibkr"), state, _Manager())
    runner._last_broker_qty = 10
    exc, log_trade = rule_tick(runner, provider)
    assert isinstance(exc, PositionCheckError)
    log_trade.assert_not_called()
    ib.placeOrder.assert_not_called()
    assert state.entry_price is not None and state.last_bar_time == PREV

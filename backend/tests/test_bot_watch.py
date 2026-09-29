"""F445: bot_watch alerts on bots that ran recently but stand still in the session.
It skips bots the user stopped and paused bots, and catches runners that died or
stopped ticking."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import bot_manager as _bot_manager_mod
import bot_watch
from bot_manager import BotConfig, BotManager, BotState

NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 ET


def _config(**kw):
    defaults = dict(bot_id="b", strategy_name="Test", symbol="AAPL", interval="5m",
                    buy_rules=[], sell_rules=[], allocated_capital=100.0)
    defaults.update(kw)
    return BotConfig(**defaults)


def _state(status="stopped", last_tick=NOW - timedelta(days=2), **kw):
    s = BotState()
    s.status = status
    s.last_tick = last_tick.isoformat() if last_tick else None
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class _Task:
    """Stand-in for asyncio.Task: only done() is read."""
    def __init__(self, done):
        self._done = done

    def done(self):
        return self._done


def _manager(**bots):
    """name -> (state, alive): alive True/False gives a live/dead task, None no task."""
    m = SimpleNamespace(bots={}, tasks={})
    for name, (state, alive) in bots.items():
        m.bots[name] = (_config(bot_id=name, symbol=name), state)
        if alive is not None:
            m.tasks[name] = _Task(done=not alive)
    return m


def test_stalled_bots_flags_only_bots_that_should_work():
    m = _manager(
        RUN=(_state("running", last_tick=NOW - timedelta(seconds=30)), True),
        USER=(_state(user_stopped=True), None),
        PAUSED=(_state("error", pause_reason="Auto-paused: drawdown"), None),
        OLD=(_state(last_tick=NOW - timedelta(days=30)), None),
        NEVER=(_state(last_tick=None), None),
        LOST=(_state(), None),
        DIED=(_state("running"), False),
        HUNG=(_state("running", last_tick=NOW - timedelta(minutes=45)), True),
        ERR=(_state(error_message="Runner stopped: IBKR not registered"), None),
    )
    assert bot_watch.stalled_bots(m, NOW) == {
        "LOST": "Test (LOST long 5m): stopped",
        "DIED": "Test (DIED long 5m): runner died",
        "HUNG": "Test (HUNG long 5m): no tick for 45 min",
        "ERR": "Test (ERR long 5m): Runner stopped: IBKR not registered",
    }


def test_naive_last_tick_is_read_as_utc():
    naive = (NOW - timedelta(days=1)).replace(tzinfo=None)
    m = _manager(NAIVE=(_state(last_tick=naive), None))
    assert bot_watch.stalled_bots(m, NOW) == {"NAIVE": "Test (NAIVE long 5m): stopped"}


def test_in_window_is_weekday_session_only():
    at = lambda d, h, m: datetime(2026, 9, d, h, m, tzinfo=bot_watch.ET)
    assert bot_watch.in_window(at(28, 9, 35)) is True    # Monday 09:35 ET
    assert bot_watch.in_window(at(28, 9, 34)) is False
    assert bot_watch.in_window(at(28, 16, 0)) is False
    assert bot_watch.in_window(at(27, 10, 0)) is False   # Sunday


def _capture(monkeypatch):
    import notifications
    sent = []

    async def fake_notify(**kw):
        sent.append(("notify", kw["message"]))

    async def fake_slack(text):
        sent.append(("slack", text))

    monkeypatch.setattr(notifications, "notify", fake_notify)
    monkeypatch.setattr(notifications, "slack", fake_slack)
    return sent


async def test_check_once_reports_each_bot_once_per_et_day(monkeypatch):
    sent = _capture(monkeypatch)
    later = NOW + timedelta(hours=1)
    m = _manager(LOST=(_state(), None),
                 RUN=(_state("running", last_tick=later - timedelta(minutes=1)), True))
    reported = {}

    assert bot_watch.check_once(m, NOW, reported) == ["Test (LOST long 5m): stopped"]
    await asyncio.sleep(0)
    assert sorted(kind for kind, _ in sent) == ["notify", "slack"]
    assert all("LOST long 5m" in text for _, text in sent)

    assert bot_watch.check_once(m, later, reported) == []   # same ET day: quiet
    m.bots["RUN"][1].status = "stopped"
    m.tasks["RUN"] = _Task(done=True)
    assert bot_watch.check_once(m, later, reported) == ["Test (RUN long 5m): stopped"]
    assert bot_watch.check_once(m, NOW + timedelta(days=1), reported) != []  # next day again
    await asyncio.sleep(0)


async def test_check_once_is_silent_when_all_work(monkeypatch):
    sent = _capture(monkeypatch)
    m = _manager(RUN=(_state("running", last_tick=NOW), True))
    assert bot_watch.check_once(m, NOW, {}) == []
    await asyncio.sleep(0)
    assert sent == []


async def test_watch_loop_sleeps_first_and_survives_errors(monkeypatch):
    calls = []

    def flaky(manager, now, reported):
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError("bad state")

    monkeypatch.setattr(bot_watch, "in_window", lambda now: True)
    monkeypatch.setattr(bot_watch, "check_once", flaky)

    monkeypatch.setattr(bot_watch, "POLL_SECS", 3600)
    task = asyncio.create_task(bot_watch.watch_loop(SimpleNamespace()))
    await asyncio.sleep(0.05)
    assert calls == []  # nothing before the first sleep ends
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    monkeypatch.setattr(bot_watch, "POLL_SECS", 0)
    task = asyncio.create_task(bot_watch.watch_loop(SimpleNamespace()))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2.0
    while len(calls) < 3 and loop.time() < deadline:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 3  # the first iteration raised, the loop kept going


async def test_watch_loop_opt_out(monkeypatch):
    monkeypatch.setenv("BOT_WATCH_ALERTS", "0")
    await asyncio.wait_for(bot_watch.watch_loop(SimpleNamespace()), timeout=1)


def _load(tmp_path, monkeypatch, states):
    f = tmp_path / "bots.json"
    f.write_text(json.dumps({"bot_fund": 1000.0, "bots": [
        {"config": {"bot_id": bid, "strategy_name": "Test", "symbol": "AAPL", "interval": "5m",
                    "buy_rules": [], "sell_rules": [], "allocated_capital": 100.0},
         "state": st}
        for bid, st in states.items()
    ]}))
    monkeypatch.setattr(_bot_manager_mod, "DATA_PATH", str(f))
    mgr = BotManager()
    mgr.load()
    return mgr


def test_user_stopped_is_set_by_stop_and_survives_a_reload(tmp_path, monkeypatch):
    mgr = _load(tmp_path, monkeypatch, {"a": {"status": "running", "user_stopped": False}})
    assert mgr.bots["a"][1].user_stopped is False
    mgr.stop_bot("a")
    assert mgr.bots["a"][1].user_stopped is True
    again = BotManager()
    again.load()
    assert again.bots["a"][1].user_stopped is True


def test_legacy_rows_without_the_key(tmp_path, monkeypatch):
    mgr = _load(tmp_path, monkeypatch, {"off": {"status": "stopped"}, "on": {"status": "running"}})
    assert mgr.bots["off"][1].user_stopped is True    # most likely stopped by hand
    assert mgr.bots["on"][1].user_stopped is False


async def test_runner_start_failure_ends_stopped_with_reason(monkeypatch):
    """IBKR not registered yet (Gateway still logging in): the runner must not die
    with status "running", or bot_watch cannot see it."""
    import bot_runner

    def not_registered(name):
        raise RuntimeError("IBKR not registered")

    monkeypatch.setattr(bot_runner, "get_trading_provider", not_registered)
    state = BotState()
    await bot_runner.BotRunner(_config(broker="ibkr"), state, SimpleNamespace(save=lambda: None)).run()
    assert state.status == "stopped"
    assert "IBKR not registered" in state.error_message


async def test_runner_start_clears_user_stopped_and_old_error(monkeypatch):
    import bot_runner
    state = BotState()
    state.user_stopped = True
    state.error_message = "Runner stopped: old"
    saved = []
    runner = bot_runner.BotRunner(
        _config(), state,
        SimpleNamespace(save=lambda: saved.append((state.status, state.user_stopped, state.error_message))))

    async def stop_now():
        raise asyncio.CancelledError

    monkeypatch.setattr(runner, "_tick", stop_now)
    with pytest.raises(asyncio.CancelledError):
        await runner.run()
    assert saved[0] == ("running", False, None)
    assert state.status == "stopped"

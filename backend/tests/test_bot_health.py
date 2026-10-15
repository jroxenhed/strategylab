"""GET /internal/bot-health: the Zabbix probe. Loopback only, flat 0/1 fields,
and bot counts that agree with bot_watch on the same bots."""
import os
import tempfile
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))
os.environ.setdefault("STRATEGYLAB_DATA_DIR", tempfile.mkdtemp(prefix="sl-bot-health-"))

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import bot_watch
import broker
import broker_health_singleton
import gateway
import routes.bots as bots_module
from bot_manager import BotConfig, BotState
from routes import bot_health

NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 ET
LOGGED_IN = {"state": "logged_in"}

FIELDS = {
    "ok", "generated_at", "market_open", "check_window", "bots_total",
    "bots_running", "bots_expected", "bots_stalled", "bots_paused",
    "stalled_symbols", "oldest_tick_age_s", "gateway_logged_in",
    "gateway_state", "ibkr_healthy", "ibkr_bots", "backend_uptime_s",
}


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
    """name -> (state, alive[, broker]): alive True/False gives a live/dead task, None no task."""
    m = SimpleNamespace(bots={}, tasks={})
    for name, spec in bots.items():
        state, alive = spec[0], spec[1]
        brk = spec[2] if len(spec) > 2 else "alpaca"
        m.bots[name] = (_config(bot_id=name, symbol=name, broker=brk), state)
        if alive is not None:
            m.tasks[name] = _Task(done=not alive)
    return m


def _fleet():
    """One bot in every state bot_watch tells apart, plus a fresh bot with no tick yet."""
    return _manager(
        RUN=(_state("running", last_tick=NOW - timedelta(seconds=30)), True, "ibkr"),
        USER=(_state(user_stopped=True), None),
        PAUSED=(_state("error", pause_reason="Auto-paused: drawdown"), None),
        OLD=(_state(last_tick=NOW - timedelta(days=30)), None),
        NEVER=(_state(last_tick=None), None),
        LOST=(_state(), None),
        DIED=(_state("running"), False),
        HUNG=(_state("running", last_tick=NOW - timedelta(minutes=45)), True, "ibkr"),
        ERR=(_state(error_message="Runner stopped: IBKR not registered"), None),
        NEW=(_state("running", last_tick=None), True),
    )


# --- bot counts ---------------------------------------------------------------

def test_counts_on_a_fleet_in_every_state():
    r = bot_health.build_report(_fleet(), NOW, LOGGED_IN, ["alpaca", "ibkr"], {})
    assert r["bots_total"] == 10
    assert r["bots_running"] == 3            # RUN, HUNG, NEW
    assert r["bots_expected"] == 5           # RUN, LOST, DIED, HUNG, ERR
    assert r["bots_stalled"] == 4            # LOST, DIED, HUNG, ERR
    assert r["bots_paused"] == 1             # PAUSED
    assert r["stalled_symbols"] == "DIED,ERR,HUNG,LOST"
    assert r["oldest_tick_age_s"] == 45 * 60  # HUNG; NEW has no tick
    assert r["ibkr_bots"] == 2
    assert r["ok"] == 0


def test_stalled_agrees_with_bot_watch():
    m = _fleet()
    for now in (NOW, NOW + timedelta(minutes=20), NOW + timedelta(days=6)):
        r = bot_health.build_report(m, now, LOGGED_IN, ["ibkr"], {})
        watch = bot_watch.stalled_bots(m, now)
        assert r["bots_stalled"] == len(watch)
        assert r["stalled_symbols"] == ",".join(sorted(m.bots[b][0].symbol for b in watch))


def test_expected_matches_bot_watch_candidates():
    """Every bot bot_watch could flag is expected; every expected bot that is
    not working is flagged."""
    m = _fleet()
    expected = {b for b, (_, s) in m.bots.items() if bot_watch.should_be_working(s, NOW)}
    assert set(bot_watch.stalled_bots(m, NOW)) <= expected
    working = {b for b in expected
               if b in bot_watch.alive_ids(m) and m.bots[b][1].status == "running"
               and NOW - bot_watch._parse(m.bots[b][1].last_tick) <= bot_watch.HUNG_AFTER}
    assert expected - working == set(bot_watch.stalled_bots(m, NOW))


def test_healthy_fleet_is_ok():
    m = _manager(RUN=(_state("running", last_tick=NOW - timedelta(seconds=30)), True, "ibkr"),
                 USER=(_state(user_stopped=True), None))
    r = bot_health.build_report(m, NOW, LOGGED_IN, ["ibkr"], {"ibkr": {"healthy": True}})
    assert (r["ok"], r["bots_stalled"], r["bots_expected"], r["bots_running"]) == (1, 0, 1, 1)
    assert r["stalled_symbols"] == ""
    assert r["oldest_tick_age_s"] == 30


def test_empty_fleet():
    r = bot_health.build_report(_manager(), NOW, {"state": "unknown"}, [], {})
    assert r["bots_total"] == 0 and r["oldest_tick_age_s"] == -1 and r["ok"] == 1
    assert r["ibkr_bots"] == 0 and r["gateway_logged_in"] == 0


def test_stalled_symbols_are_cut_short():
    bots = {f"SYM{i:03d}": (_state(), None) for i in range(100)}
    r = bot_health.build_report(_manager(**bots), NOW, LOGGED_IN, [], {})
    assert len(r["stalled_symbols"]) == bot_health.SYMBOLS_MAX_CHARS
    assert r["stalled_symbols"].endswith("...")


def test_stalled_outside_window_does_not_clear_ok_alone():
    """A stopped bot after the close is not a problem by itself (bot_watch is
    quiet too); ok only drops for it inside the check window."""
    m = _manager(LOST=(_state(), None))
    evening = datetime(2026, 9, 28, 22, 0, tzinfo=timezone.utc)  # 18:00 ET
    assert bot_health.build_report(m, evening, {}, [], {})["ok"] == 1
    assert bot_health.build_report(m, NOW, {}, [], {})["ok"] == 0


# --- market clock -------------------------------------------------------------

@pytest.mark.parametrize("utc, market, window", [
    (datetime(2026, 9, 28, 14, 0), 1, 1),    # Mon 10:00 EDT
    (datetime(2026, 9, 28, 13, 32), 1, 0),   # Mon 09:32 EDT: open, before bot_watch starts
    (datetime(2026, 9, 28, 13, 29), 0, 0),   # Mon 09:29 EDT
    (datetime(2026, 9, 28, 20, 0), 0, 0),    # Mon 16:00 EDT: closed
    (datetime(2026, 9, 26, 15, 0), 0, 0),    # Saturday
    (datetime(2026, 12, 7, 14, 35), 1, 1),   # Mon 09:35 EST (winter offset)
    (datetime(2026, 12, 7, 14, 25), 0, 0),   # Mon 09:25 EST: not yet open
])
def test_market_open_and_check_window(utc, market, window):
    now = utc.replace(tzinfo=timezone.utc)
    r = bot_health.build_report(_manager(), now, {}, [], {})
    assert r["market_open"] == market
    assert r["check_window"] == window == int(bot_watch.in_window(now))


def test_market_open_winter_edges():
    assert bot_health.market_open(datetime(2026, 12, 7, 14, 30, tzinfo=timezone.utc))  # 09:30 EST
    assert not bot_health.market_open(datetime(2026, 12, 7, 14, 29, tzinfo=timezone.utc))
    assert not bot_health.market_open(datetime(2026, 12, 7, 21, 0, tzinfo=timezone.utc))  # 16:00 EST


# --- gateway and broker flags ---------------------------------------------------

def test_gateway_logged_out_with_ibkr_bots_is_not_ok():
    m = _manager(RUN=(_state("running", last_tick=NOW - timedelta(seconds=30)), True, "ibkr"))
    r = bot_health.build_report(m, NOW, {"state": "awaiting_2fa"}, ["ibkr"], {"ibkr": {"healthy": True}})
    assert (r["gateway_logged_in"], r["gateway_state"], r["ibkr_bots"], r["ok"]) == (0, "awaiting_2fa", 1, 0)


def test_gateway_logged_out_without_ibkr_bots_is_ok():
    m = _manager(RUN=(_state("running", last_tick=NOW - timedelta(seconds=30)), True, "alpaca"))
    r = bot_health.build_report(m, NOW, {"state": "down"}, [], {})
    assert (r["gateway_logged_in"], r["ibkr_bots"], r["ok"]) == (0, 0, 1)


def test_ibkr_healthy_flag():
    f = bot_health.broker_flags
    assert f(["ibkr"], {"ibkr": {"healthy": True}}) == {"ibkr_healthy": 1}
    assert f(["ibkr"], {"ibkr": {"healthy": False}}) == {"ibkr_healthy": 0}
    assert f(["ibkr"], {}) == {"ibkr_healthy": 1}            # no heartbeat yet
    assert f(["alpaca"], {"ibkr": {"healthy": True}}) == {"ibkr_healthy": 0}  # not registered
    assert f([], {"alpaca": {"healthy": False}}) == {"ibkr_healthy": 0, "alpaca_healthy": 0}


def test_unhealthy_ibkr_with_ibkr_bots_is_not_ok():
    m = _manager(RUN=(_state("running", last_tick=NOW - timedelta(seconds=30)), True, "ibkr"))
    r = bot_health.build_report(m, NOW, LOGGED_IN, ["ibkr"], {"ibkr": {"healthy": False}})
    assert (r["ibkr_healthy"], r["ok"]) == (0, 0)


def test_gateway_cached_state_uses_fresh_cache_and_rereads_stale(monkeypatch):
    calls = []

    async def fake_read():
        calls.append(1)
        return {"state": "fresh"}

    monkeypatch.setattr(gateway, "read_state", fake_read)
    monkeypatch.setattr(gateway, "_last_info", {"state": "logged_in"})
    monkeypatch.setattr(gateway, "_last_info_ts", time.time())
    assert asyncio.run(gateway.cached_state())["state"] == "logged_in"
    assert calls == []
    monkeypatch.setattr(gateway, "_last_info_ts", time.time() - 600)
    assert asyncio.run(gateway.cached_state())["state"] == "fresh"
    monkeypatch.setattr(gateway, "_last_info", None)
    assert asyncio.run(gateway.cached_state())["state"] == "fresh"
    assert len(calls) == 2


# --- HTTP: loopback guard and response shape --------------------------------------

class _Monitor:
    def __init__(self, health):
        self._h = health

    def all_health(self):
        return dict(self._h)


@pytest.fixture
def wired(monkeypatch):
    """The router on a bare app, with a fake fleet, broker registry, heartbeat
    cache and gateway state. No lifespan, no broker connection, no orders."""
    monkeypatch.setattr(bots_module, "bot_manager", _fleet())
    monkeypatch.setattr(broker, "get_available_brokers", lambda: ["alpaca", "ibkr"])
    monkeypatch.setattr(broker_health_singleton, "_monitor", _Monitor({"ibkr": {"healthy": True}}))

    async def fake_cached(max_age=0):
        return {"state": "logged_in"}

    monkeypatch.setattr(gateway, "cached_state", fake_cached)
    app = FastAPI()
    app.include_router(bot_health.router)
    return app


def _get(app, host, headers=None):
    with TestClient(app, client=(host, 50000)) as c:
        return c.get("/internal/bot-health", headers=headers or {})


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_loopback_gets_full_report(wired, host):
    r = _get(wired, host)
    assert r.status_code == 200
    body = r.json()
    assert FIELDS <= set(body)
    assert "alpaca_healthy" not in body        # no heartbeat value for Alpaca
    for k in FIELDS - {"stalled_symbols", "gateway_state"}:
        assert isinstance(body[k], int), k
    assert body["gateway_logged_in"] == 1 and body["ibkr_healthy"] == 1
    assert body["bots_total"] == 10


@pytest.mark.parametrize("host", ["10.0.0.5", "192.168.1.20", "testclient", "8.8.8.8"])
def test_non_loopback_is_404(wired, host):
    assert _get(wired, host).status_code == 404


@pytest.mark.parametrize("hdr", [{"X-Forwarded-For": "203.0.113.9"}, {"X-Real-IP": "203.0.113.9"},
                                 {"x-forwarded-for": "127.0.0.1"}])
def test_loopback_through_a_proxy_is_404(wired, hdr):
    assert _get(wired, "127.0.0.1", hdr).status_code == 404


def test_503_before_manager_exists(wired, monkeypatch):
    monkeypatch.setattr(bots_module, "bot_manager", None)
    assert _get(wired, "127.0.0.1").status_code == 503


def test_main_app_serves_the_route_outside_api():
    from main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/internal/bot-health" in paths

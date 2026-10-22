import asyncio
import pytest
from broker_health import HeartbeatMonitor


class FakeProvider:
    def __init__(self, name="fake"):
        self.name = name
        self.calls = 0
    async def ping(self):
        self.calls += 1


@pytest.mark.asyncio
async def test_tick_marks_healthy_on_success():
    p = FakeProvider()
    mon = HeartbeatMonitor(registry={"fake": p}, interval=0.01, timeout=1.0)
    await mon._tick()
    h = mon.get_health("fake")
    assert h["healthy"] is True
    assert h["last_ok_ts"] is not None
    assert h["last_error"] is None
    assert p.calls == 1


class TimeoutProvider:
    name = "slow"
    async def ping(self):
        await asyncio.sleep(5.0)


class RaisingProvider:
    name = "bad"
    async def ping(self):
        raise RuntimeError("socket dead")


class NoPingProvider:
    name = "alpaca"
    # no ping() — should be skipped silently


@pytest.mark.asyncio
async def test_tick_marks_unhealthy_on_timeout():
    p = TimeoutProvider()
    mon = HeartbeatMonitor(registry={"slow": p}, interval=0.01, timeout=0.05)
    await mon._tick()
    h = mon.get_health("slow")
    assert h["healthy"] is False
    assert "TimeoutError" in h["last_error"]
    assert h["last_ok_ts"] is None


@pytest.mark.asyncio
async def test_tick_preserves_last_ok_ts_on_failure():
    class Flaky:
        name = "flaky"
        def __init__(self):
            self.fail = False
        async def ping(self):
            if self.fail:
                raise RuntimeError("boom")

    p = Flaky()
    mon = HeartbeatMonitor(registry={"flaky": p}, interval=0.01, timeout=1.0)
    await mon._tick()
    first_ok = mon.get_health("flaky")["last_ok_ts"]
    assert first_ok is not None
    p.fail = True
    await mon._tick()
    h = mon.get_health("flaky")
    assert h["healthy"] is False
    assert h["last_ok_ts"] == first_ok


@pytest.mark.asyncio
async def test_tick_records_exception_details():
    p = RaisingProvider()
    mon = HeartbeatMonitor(registry={"bad": p}, interval=0.01, timeout=1.0)
    await mon._tick()
    h = mon.get_health("bad")
    assert h["healthy"] is False
    assert "socket dead" in h["last_error"]


@pytest.mark.asyncio
async def test_tick_skips_providers_without_ping():
    p = NoPingProvider()
    mon = HeartbeatMonitor(registry={"alpaca": p}, interval=0.01, timeout=1.0)
    await mon._tick()
    assert mon.get_health("alpaca") is None
    assert mon.is_healthy("alpaca") is True


@pytest.mark.asyncio
async def test_tick_reconnects_on_ping_failure():
    class DeadSocket:
        name = "ibkr"
        def __init__(self):
            self.connected = False
            self.reconnect_calls = 0
        async def ping(self):
            if not self.connected:
                raise RuntimeError("socket dead")
        async def reconnect(self):
            self.reconnect_calls += 1
            self.connected = True

    p = DeadSocket()
    mon = HeartbeatMonitor(registry={"ibkr": p}, interval=0.01, timeout=1.0)
    await mon._tick()
    h = mon.get_health("ibkr")
    assert h["healthy"] is True
    assert p.reconnect_calls == 1


@pytest.mark.asyncio
async def test_tick_marks_unhealthy_when_reconnect_also_fails():
    class HardDown:
        name = "ibkr"
        async def ping(self):
            raise RuntimeError("socket dead")
        async def reconnect(self):
            raise RuntimeError("gateway down")

    mon = HeartbeatMonitor(registry={"ibkr": HardDown()}, interval=0.01, timeout=1.0)
    await mon._tick()
    h = mon.get_health("ibkr")
    assert h["healthy"] is False
    assert "gateway down" in h["last_error"]


@pytest.mark.asyncio
async def test_run_loop_survives_tick_exception(monkeypatch):
    mon = HeartbeatMonitor(registry={}, interval=0.01, timeout=1.0, warmup=0.0)
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tick exploded")

    monkeypatch.setattr(mon, "_tick", boom)
    mon.start()
    # Poll to a deadline: a fixed 50 ms sleep sometimes ended before the second
    # tick in a loaded full-suite run (F446).
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2.0
    while calls["n"] < 2 and loop.time() < deadline:
        await asyncio.sleep(0.01)
    await mon.stop()
    assert calls["n"] >= 2


# --- Alpaca ping (GET /v2/clock on its own thread, observe-only) ---------------

import threading

from broker import AlpacaTradingProvider


class FakeClock:
    """Stands in for AlpacaTradingProvider._clock_request."""
    def __init__(self, fail=None, gate=None):
        self.calls = 0
        self.threads = []
        self._fail = fail
        self._gate = gate

    def __call__(self):
        self.calls += 1
        self.threads.append(threading.current_thread())
        if self._gate is not None:
            self._gate.wait(5)
        if self._fail is not None:
            raise self._fail


class NoTradingClient:
    """Any call on the trading client from ping() fails the test."""
    def __getattr__(self, name):
        raise AssertionError(f"ping must not call the trading client ({name})")


def _alpaca(clock):
    p = AlpacaTradingProvider(NoTradingClient())
    p._clock_request = clock
    return p


@pytest.mark.asyncio
async def test_alpaca_ping_marks_healthy_in_observed_only():
    clock = FakeClock()
    mon = HeartbeatMonitor(registry={"alpaca": _alpaca(clock)}, interval=0.01, timeout=1.0)
    await mon._tick()
    assert mon.observed_health()["alpaca"]["healthy"] is True
    assert mon.all_health() == {}                      # the trading page sees nothing new
    assert clock.calls == 1
    t = clock.threads[0]
    assert t.name == "alpaca-ping" and t.daemon       # not the bots' default executor


@pytest.mark.asyncio
async def test_alpaca_ping_failure_never_marks_alpaca_unhealthy_for_trading():
    p = _alpaca(FakeClock(fail=RuntimeError("401 unauthorized")))
    assert not hasattr(p, "reconnect")
    mon = HeartbeatMonitor(registry={"alpaca": p}, interval=0.01, timeout=1.0)
    await mon._tick()
    h = mon.observed_health()["alpaca"]
    assert h["healthy"] is False and "401 unauthorized" in h["last_error"]
    assert mon.is_healthy("alpaca") is True            # unchanged: no entry in _health
    assert "alpaca" not in mon.all_health()


@pytest.mark.asyncio
async def test_failed_alpaca_ping_still_returns_alpaca_positions(monkeypatch):
    import broker_aggregate
    import broker_health_singleton
    p = _alpaca(FakeClock(fail=RuntimeError("503")))
    p.get_positions = lambda: [{"symbol": "AAPL"}]
    mon = HeartbeatMonitor(registry={"alpaca": p}, interval=0.01, timeout=1.0)
    await mon._tick()
    monkeypatch.setattr(broker_health_singleton, "_monitor", mon)
    out = await broker_aggregate.aggregate_from_brokers({"alpaca": p}, "get_positions", "all")
    assert out == {"rows": [{"symbol": "AAPL", "broker": "alpaca"}], "stale_brokers": []}


@pytest.mark.asyncio
async def test_alpaca_hung_ping_holds_one_thread_and_recovers():
    gate = threading.Event()
    clock = FakeClock(gate=gate)
    p = _alpaca(clock)
    mon = HeartbeatMonitor(registry={"alpaca": p}, interval=0.01, timeout=0.05)
    await mon._tick()                                   # hangs past the timeout
    assert "TimeoutError" in mon.observed_health()["alpaca"]["last_error"]
    await mon._tick()                                   # still stuck: no second thread
    assert "still running" in mon.observed_health()["alpaca"]["last_error"]
    assert clock.calls == 1
    gate.set()
    p._ping_thread.join(2)
    await mon._tick()
    assert mon.observed_health()["alpaca"]["healthy"] is True
    assert clock.calls == 2


@pytest.mark.asyncio
async def test_observe_only_ping_runs_after_ibkr():
    order = []

    class Ibkr:
        async def ping(self):
            order.append("ibkr")

    p = _alpaca(lambda: order.append("alpaca"))
    # Alpaca registers first in production (shared.py at import, IBKR in lifespan)
    mon = HeartbeatMonitor(registry={"alpaca": p, "ibkr": Ibkr()}, interval=0.01, timeout=1.0)
    await mon._tick()
    assert order == ["ibkr", "alpaca"]


def test_alpaca_clock_request_uses_client_auth_and_a_timeout(monkeypatch):
    """Pins the alpaca-py internals _clock_request relies on."""
    import requests
    from alpaca.trading.client import TradingClient
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        seen["url"] = url
        seen.update(kw)
        return Resp()

    monkeypatch.setattr(requests, "get", fake_get)
    AlpacaTradingProvider(TradingClient("KEY", "SECRET", paper=True))._clock_request()
    assert seen["url"] == "https://paper-api.alpaca.markets/v2/clock"
    assert seen["headers"]["APCA-API-KEY-ID"] == "KEY"
    assert seen["headers"]["APCA-API-SECRET-KEY"] == "SECRET"
    assert seen["timeout"] == AlpacaTradingProvider.PING_TIMEOUT
    assert sum(seen["timeout"]) < 8

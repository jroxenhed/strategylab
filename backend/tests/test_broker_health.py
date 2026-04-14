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
async def test_run_loop_survives_tick_exception(monkeypatch):
    mon = HeartbeatMonitor(registry={}, interval=0.01, timeout=1.0, warmup=0.0)
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tick exploded")

    monkeypatch.setattr(mon, "_tick", boom)
    mon.start()
    await asyncio.sleep(0.05)
    await mon.stop()
    assert calls["n"] >= 2

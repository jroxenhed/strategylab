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

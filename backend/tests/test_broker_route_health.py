import pytest
from fastapi.testclient import TestClient
from broker_health import HeartbeatMonitor
import broker_health_singleton
from broker import register_trading_provider, _trading_providers


class StubIBKR:
    name = "ibkr"
    async def ping(self):
        pass
    def get_account(self): return {}
    def get_positions(self): return []
    def get_orders(self, **kw): return []
    def submit_order(self, o): ...
    def get_order(self, i): ...
    def cancel_order(self, i): ...
    def close_position(self, s): ...
    def close_all_positions(self): ...
    def cancel_all_orders(self): ...
    def get_latest_price(self, s): return 0.0


@pytest.mark.asyncio
async def test_broker_route_exposes_health():
    _trading_providers.clear()
    register_trading_provider("ibkr", StubIBKR())
    mon = HeartbeatMonitor(registry=_trading_providers, warmup=0.0)
    await mon._tick()
    broker_health_singleton.set_monitor(mon)

    from main import app
    client = TestClient(app)
    r = client.get("/api/broker")
    assert r.status_code == 200
    body = r.json()
    assert "health" in body
    assert body["health"]["ibkr"]["healthy"] is True
    assert "heartbeat_warmup" in body

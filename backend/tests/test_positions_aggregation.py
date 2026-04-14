import pytest
from broker_aggregate import aggregate_from_brokers
from broker_health import HeartbeatMonitor
import broker_health_singleton


class FakeBroker:
    def __init__(self, name, positions=None, raises=False):
        self.name = name
        self._positions = positions or []
        self._raises = raises
    def get_positions(self):
        if self._raises:
            raise RuntimeError("blew up")
        return self._positions


@pytest.mark.asyncio
async def test_all_healthy_returns_union_with_broker_tag():
    registry = {
        "alpaca": FakeBroker("alpaca", [{"symbol": "AAPL", "qty": 10}]),
        "ibkr":   FakeBroker("ibkr",   [{"symbol": "MSFT", "qty": 5}]),
    }
    mon = HeartbeatMonitor(registry=registry, warmup=0.0)
    mon._health["ibkr"] = {"healthy": True, "last_ok_ts": 1, "last_error": None}
    broker_health_singleton.set_monitor(mon)

    result = await aggregate_from_brokers(registry, "get_positions", "all")
    brokers_seen = {row["broker"] for row in result["rows"]}
    assert brokers_seen == {"alpaca", "ibkr"}
    assert result["stale_brokers"] == []


@pytest.mark.asyncio
async def test_unhealthy_broker_skipped_and_listed_stale():
    registry = {
        "alpaca": FakeBroker("alpaca", [{"symbol": "AAPL", "qty": 10}]),
        "ibkr":   FakeBroker("ibkr",   [{"symbol": "MSFT", "qty": 5}]),
    }
    mon = HeartbeatMonitor(registry=registry, warmup=0.0)
    mon._health["ibkr"] = {"healthy": False, "last_ok_ts": None, "last_error": "dead"}
    broker_health_singleton.set_monitor(mon)

    result = await aggregate_from_brokers(registry, "get_positions", "all")
    assert all(row["broker"] == "alpaca" for row in result["rows"])
    assert result["stale_brokers"] == ["ibkr"]


@pytest.mark.asyncio
async def test_raising_broker_caught_and_listed_stale():
    registry = {
        "alpaca": FakeBroker("alpaca", [{"symbol": "AAPL", "qty": 10}]),
        "ibkr":   FakeBroker("ibkr",   raises=True),
    }
    mon = HeartbeatMonitor(registry=registry, warmup=0.0)
    mon._health["ibkr"] = {"healthy": True, "last_ok_ts": 1, "last_error": None}
    broker_health_singleton.set_monitor(mon)

    result = await aggregate_from_brokers(registry, "get_positions", "all")
    assert all(row["broker"] == "alpaca" for row in result["rows"])
    assert "ibkr" in result["stale_brokers"]


@pytest.mark.asyncio
async def test_specific_broker_filter_returns_only_that_broker():
    registry = {
        "alpaca": FakeBroker("alpaca", [{"symbol": "AAPL", "qty": 10}]),
        "ibkr":   FakeBroker("ibkr",   [{"symbol": "MSFT", "qty": 5}]),
    }
    mon = HeartbeatMonitor(registry=registry, warmup=0.0)
    mon._health["ibkr"] = {"healthy": True, "last_ok_ts": 1, "last_error": None}
    broker_health_singleton.set_monitor(mon)

    result = await aggregate_from_brokers(registry, "get_positions", "ibkr")
    assert all(row["broker"] == "ibkr" for row in result["rows"])
    assert result["stale_brokers"] == []

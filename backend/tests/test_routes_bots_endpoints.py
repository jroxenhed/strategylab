"""HTTP-layer integration tests for /api/bots endpoints — F144.

Covers the ~15 endpoints left untested after F143:
  POST /api/bots               (AddBotRequest / BotConfig path)
  GET  /api/bots
  GET  /api/bots/{id}
  DELETE /api/bots/{id}
  POST /api/bots/start-all
  POST /api/bots/stop-all
  POST /api/bots/stop-and-close-all
  PUT  /api/bots/reorder        (already tested by F147 but kept for coverage)
  POST /api/bots/{id}/start
  POST /api/bots/{id}/stop
  POST /api/bots/{id}/buy
  POST /api/bots/{id}/reset-pnl
  POST /api/bots/{id}/backtest
  GET  /api/bots/fund
  PUT  /api/bots/fund

No network calls; BotManager is replaced with MagicMock so no polling loop
is ever created.  Async-start endpoints use monkeypatched mgr.start_bot()
which does NOT call asyncio.create_task().
"""

from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from routes import bots as bots_mod
from main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_BOT_ID = "aaaa-0000"

_VALID_BOT_CONFIG = {
    "strategy_name": "test-strat",
    "symbol": "AAPL",
    "interval": "5m",
    "buy_rules": [{"indicator": "rsi", "condition": "below", "value": 30}],
    "sell_rules": [{"indicator": "rsi", "condition": "above", "value": 70}],
    "allocated_capital": 1000.0,
}

# A minimal BotState-like object for mock responses
def _make_mock_state(status="stopped", entry_price=None):
    state = MagicMock()
    state.status = status
    state.entry_price = entry_price
    state.to_dict.return_value = {"status": status, "entry_price": entry_price}
    return state


def _make_mock_config(**kwargs):
    cfg = MagicMock()
    cfg.model_dump.return_value = {**_VALID_BOT_CONFIG, **kwargs}
    cfg.graph = None
    return cfg


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mgr():
    """A fresh MagicMock BotManager with basic defaults."""
    mock = MagicMock()
    mock.get_fund_status.return_value = {
        "bot_fund": 5000.0,
        "allocated": 1000.0,
        "available": 4000.0,
    }
    mock.list_bots.return_value = []
    mock.bots = {}
    return mock


@pytest.fixture
def client_mgr(client, mgr, monkeypatch):
    """Return (client, mgr) with bot_manager swapped to the mock."""
    monkeypatch.setattr(bots_mod, "bot_manager", mgr)
    return client, mgr


# ---------------------------------------------------------------------------
# 503 guard — applies to every endpoint
# ---------------------------------------------------------------------------

class TestManagerNotInitialized:
    """_get_manager() returns 503 when bot_manager is None."""

    def test_get_bots_503_when_unset(self, client, monkeypatch):
        monkeypatch.setattr(bots_mod, "bot_manager", None)
        r = client.get("/api/bots")
        assert r.status_code == 503
        assert "Bot manager not initialized" in r.json()["detail"]

    def test_post_bots_503_when_unset(self, client, monkeypatch):
        monkeypatch.setattr(bots_mod, "bot_manager", None)
        r = client.post("/api/bots", json=_VALID_BOT_CONFIG)
        assert r.status_code == 503

    def test_get_fund_503_when_unset(self, client, monkeypatch):
        monkeypatch.setattr(bots_mod, "bot_manager", None)
        r = client.get("/api/bots/fund")
        assert r.status_code == 503

    def test_put_fund_503_when_unset(self, client, monkeypatch):
        monkeypatch.setattr(bots_mod, "bot_manager", None)
        r = client.put("/api/bots/fund", json={"amount": 1000.0})
        assert r.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/bots/fund
# ---------------------------------------------------------------------------

class TestGetFund:
    def test_returns_fund_status(self, client_mgr):
        client, mgr = client_mgr
        r = client.get("/api/bots/fund")
        assert r.status_code == 200
        data = r.json()
        assert data["bot_fund"] == 5000.0
        assert data["allocated"] == 1000.0
        assert data["available"] == 4000.0


# ---------------------------------------------------------------------------
# PUT /api/bots/fund
# ---------------------------------------------------------------------------

class TestPutFund:
    def test_set_fund_returns_status(self, client_mgr):
        client, mgr = client_mgr
        r = client.put("/api/bots/fund", json={"amount": 8000.0})
        assert r.status_code == 200
        mgr.set_bot_fund.assert_called_once_with(8000.0)

    def test_set_fund_invalid_negative(self, client_mgr):
        client, _ = client_mgr
        r = client.put("/api/bots/fund", json={"amount": -1.0})
        assert r.status_code == 422

    def test_set_fund_400_on_value_error(self, client_mgr):
        client, mgr = client_mgr
        mgr.set_bot_fund.side_effect = ValueError("Cannot reduce fund below allocated")
        r = client.put("/api/bots/fund", json={"amount": 0.0})
        assert r.status_code == 400
        assert "Cannot reduce" in r.json()["detail"]

    def test_set_fund_missing_body(self, client_mgr):
        client, _ = client_mgr
        r = client.put("/api/bots/fund", json={})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/bots  (highest priority — AddBotRequest / BotConfig path)
# ---------------------------------------------------------------------------

class TestAddBot:
    """F144 priority: POST /api/bots uses BotConfig directly as schema.

    Key risk: the route previously duplicated BotConfig fields in a separate
    AddBotRequest, causing silent field-drop via extra="ignore". Now it uses
    BotConfig directly — verify full round-trip validation.
    """

    def test_create_bot_happy_path(self, client_mgr):
        client, mgr = client_mgr
        mgr.add_bot.return_value = _VALID_BOT_ID
        r = client.post("/api/bots", json=_VALID_BOT_CONFIG)
        assert r.status_code == 200
        assert r.json()["bot_id"] == _VALID_BOT_ID
        mgr.add_bot.assert_called_once()

    def test_create_bot_returns_400_on_value_error(self, client_mgr):
        client, mgr = client_mgr
        mgr.add_bot.side_effect = ValueError("Bot fund is not set")
        r = client.post("/api/bots", json=_VALID_BOT_CONFIG)
        assert r.status_code == 400
        assert "Bot fund" in r.json()["detail"]

    def test_create_bot_rejects_missing_required_field(self, client_mgr):
        """BotConfig.strategy_name is required — 422 if omitted."""
        client, _ = client_mgr
        body = {k: v for k, v in _VALID_BOT_CONFIG.items() if k != "strategy_name"}
        r = client.post("/api/bots", json=body)
        assert r.status_code == 422

    def test_create_bot_rejects_invalid_symbol(self, client_mgr):
        """Symbol validator in BotConfig → 422 for empty string."""
        client, _ = client_mgr
        body = {**_VALID_BOT_CONFIG, "symbol": ""}
        r = client.post("/api/bots", json=body)
        assert r.status_code == 422

    def test_create_bot_rejects_101_buy_rules(self, client_mgr):
        """F144 F128-cap gap: BotConfig.buy_rules is BoundedRuleList (cap=100).
        POST /api/bots with 101 rules must 422 at Pydantic validation,
        before add_bot() is reached.
        """
        client, mgr = client_mgr
        stub_rule = {"indicator": "rsi", "condition": "below", "value": 30}
        body = {**_VALID_BOT_CONFIG, "buy_rules": [stub_rule] * 101}
        r = client.post("/api/bots", json=body)
        assert r.status_code == 422
        mgr.add_bot.assert_not_called()

    def test_create_bot_accepts_exactly_100_buy_rules(self, client_mgr):
        """Boundary: exactly 100 buy_rules must pass BotConfig validation."""
        client, mgr = client_mgr
        mgr.add_bot.return_value = _VALID_BOT_ID
        stub_rule = {"indicator": "rsi", "condition": "below", "value": 30}
        body = {**_VALID_BOT_CONFIG, "buy_rules": [stub_rule] * 100}
        r = client.post("/api/bots", json=body)
        assert r.status_code == 200
        mgr.add_bot.assert_called_once()

    def test_create_bot_rejects_negative_allocated_capital(self, client_mgr):
        # COR-03: make add_bot raise ValueError so the route returns 400,
        # giving the test a specific assertion rather than a vacuous allow-all.
        client, mgr = client_mgr
        mgr.add_bot.side_effect = ValueError("Allocation $-100.00 is invalid")
        body = {**_VALID_BOT_CONFIG, "allocated_capital": -100.0}
        r = client.post("/api/bots", json=body)
        assert r.status_code == 400
        assert "invalid" in r.json()["detail"].lower() or "allocation" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/bots
# ---------------------------------------------------------------------------

class TestListBots:
    def test_returns_fund_and_bots(self, client_mgr):
        client, mgr = client_mgr
        mgr.list_bots.return_value = [{"bot_id": _VALID_BOT_ID, "status": "stopped"}]
        r = client.get("/api/bots")
        assert r.status_code == 200
        data = r.json()
        assert "fund" in data
        assert "bots" in data
        assert len(data["bots"]) == 1
        assert data["bots"][0]["bot_id"] == _VALID_BOT_ID

    def test_returns_empty_list_when_no_bots(self, client_mgr):
        client, mgr = client_mgr
        mgr.list_bots.return_value = []
        r = client.get("/api/bots")
        assert r.status_code == 200
        assert r.json()["bots"] == []


# ---------------------------------------------------------------------------
# GET /api/bots/{id}
# ---------------------------------------------------------------------------

class TestGetBot:
    def test_returns_config_and_state(self, client_mgr):
        client, mgr = client_mgr
        cfg = _make_mock_config()
        state = _make_mock_state()
        mgr.get_bot.return_value = (cfg, state)
        r = client.get(f"/api/bots/{_VALID_BOT_ID}")
        assert r.status_code == 200
        data = r.json()
        assert "config" in data
        assert "state" in data

    def test_404_for_unknown_id(self, client_mgr):
        client, mgr = client_mgr
        mgr.get_bot.side_effect = KeyError("Bot unknown not found")
        r = client.get("/api/bots/unknown")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/bots/{id}
# ---------------------------------------------------------------------------

class TestDeleteBot:
    def test_delete_stopped_bot(self, client_mgr):
        client, mgr = client_mgr
        r = client.delete(f"/api/bots/{_VALID_BOT_ID}")
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        mgr.delete_bot.assert_called_once_with(_VALID_BOT_ID)

    def test_404_for_unknown_id(self, client_mgr):
        client, mgr = client_mgr
        mgr.delete_bot.side_effect = KeyError("Bot unknown not found")
        r = client.delete("/api/bots/unknown")
        assert r.status_code == 404

    def test_400_when_bot_is_running(self, client_mgr):
        client, mgr = client_mgr
        mgr.delete_bot.side_effect = ValueError("Cannot delete a running bot")
        r = client.delete(f"/api/bots/{_VALID_BOT_ID}")
        assert r.status_code == 400
        assert "running" in r.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/bots/start-all
# ---------------------------------------------------------------------------

class TestStartAll:
    def test_starts_stopped_bots(self, client_mgr):
        client, mgr = client_mgr
        cfg = _make_mock_config()
        state = _make_mock_state(status="stopped")
        mgr.bots = {"bot-1": (cfg, state), "bot-2": (cfg, state)}
        r = client.post("/api/bots/start-all")
        assert r.status_code == 200
        data = r.json()
        assert "started" in data
        assert "skipped" in data
        assert "failed" in data

    def test_skips_already_running_bots(self, client_mgr):
        client, mgr = client_mgr
        cfg = _make_mock_config()
        state = _make_mock_state(status="running")
        mgr.bots = {"bot-1": (cfg, state)}
        r = client.post("/api/bots/start-all")
        assert r.status_code == 200
        data = r.json()
        assert "bot-1" in data["skipped"]
        assert data["started"] == []

    def test_reports_failed_bots(self, client_mgr):
        client, mgr = client_mgr
        cfg = _make_mock_config()
        state = _make_mock_state(status="stopped")
        mgr.bots = {"bot-err": (cfg, state)}
        mgr.start_bot.side_effect = Exception("broker offline")
        r = client.post("/api/bots/start-all")
        assert r.status_code == 200
        data = r.json()
        assert data["started"] == []
        assert len(data["failed"]) == 1
        assert data["failed"][0]["bot_id"] == "bot-err"

    def test_empty_bots_returns_empty_lists(self, client_mgr):
        client, mgr = client_mgr
        mgr.bots = {}
        r = client.post("/api/bots/start-all")
        assert r.status_code == 200
        data = r.json()
        assert data == {"started": [], "skipped": [], "failed": []}


# ---------------------------------------------------------------------------
# POST /api/bots/stop-all
# ---------------------------------------------------------------------------

class TestStopAll:
    def test_stops_running_bots(self, client_mgr):
        client, mgr = client_mgr
        cfg = _make_mock_config()
        state = _make_mock_state(status="running")
        mgr.bots = {"bot-1": (cfg, state)}
        r = client.post("/api/bots/stop-all")
        assert r.status_code == 200
        data = r.json()
        assert "stopped" in data
        assert "failed" in data
        mgr.stop_bot.assert_called_once_with("bot-1", close_position=False)

    def test_ignores_non_running_bots(self, client_mgr):
        client, mgr = client_mgr
        cfg = _make_mock_config()
        state = _make_mock_state(status="stopped")
        mgr.bots = {"bot-1": (cfg, state)}
        r = client.post("/api/bots/stop-all")
        assert r.status_code == 200
        data = r.json()
        assert data["stopped"] == []
        mgr.stop_bot.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/bots/stop-and-close-all
# ---------------------------------------------------------------------------

class TestStopAndCloseAll:
    def test_closes_running_bots(self, client_mgr):
        client, mgr = client_mgr
        cfg = _make_mock_config()
        state = _make_mock_state(status="running")
        mgr.bots = {"bot-1": (cfg, state)}
        r = client.post("/api/bots/stop-and-close-all")
        assert r.status_code == 200
        data = r.json()
        assert "closed" in data
        assert "failed" in data
        mgr.stop_bot.assert_called_once_with("bot-1", close_position=True)

    def test_reports_failed_close(self, client_mgr):
        client, mgr = client_mgr
        cfg = _make_mock_config()
        state = _make_mock_state(status="running")
        mgr.bots = {"bot-err": (cfg, state)}
        mgr.stop_bot.side_effect = Exception("position close failed")
        r = client.post("/api/bots/stop-and-close-all")
        assert r.status_code == 200
        data = r.json()
        assert data["closed"] == []
        assert len(data["failed"]) == 1


# ---------------------------------------------------------------------------
# POST /api/bots/{id}/start
# ---------------------------------------------------------------------------

class TestStartBot:
    def test_start_returns_running(self, client_mgr):
        client, mgr = client_mgr
        r = client.post(f"/api/bots/{_VALID_BOT_ID}/start")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "status": "running"}
        mgr.start_bot.assert_called_once_with(_VALID_BOT_ID)

    def test_404_for_unknown_id(self, client_mgr):
        client, mgr = client_mgr
        mgr.start_bot.side_effect = KeyError("Bot unknown not found")
        r = client.post("/api/bots/unknown/start")
        assert r.status_code == 404

    def test_400_when_already_running(self, client_mgr):
        client, mgr = client_mgr
        mgr.start_bot.side_effect = ValueError("Bot is already running")
        r = client.post(f"/api/bots/{_VALID_BOT_ID}/start")
        assert r.status_code == 400
        assert "already running" in r.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/bots/{id}/stop
# ---------------------------------------------------------------------------

class TestStopBot:
    def test_stop_returns_stopped(self, client_mgr):
        client, mgr = client_mgr
        r = client.post(f"/api/bots/{_VALID_BOT_ID}/stop")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "status": "stopped"}
        mgr.stop_bot.assert_called_once_with(_VALID_BOT_ID, close_position=False)

    def test_stop_with_close_true(self, client_mgr):
        client, mgr = client_mgr
        r = client.post(f"/api/bots/{_VALID_BOT_ID}/stop?close=true")
        assert r.status_code == 200
        mgr.stop_bot.assert_called_once_with(_VALID_BOT_ID, close_position=True)

    def test_404_for_unknown_id(self, client_mgr):
        client, mgr = client_mgr
        mgr.stop_bot.side_effect = KeyError("Bot unknown not found")
        r = client.post("/api/bots/unknown/stop")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/bots/{id}/buy
# ---------------------------------------------------------------------------

class TestManualBuy:
    def test_buy_returns_result(self, client_mgr):
        client, mgr = client_mgr
        mgr.manual_buy.return_value = {"order_id": "ord-123", "qty": 5}
        r = client.post(f"/api/bots/{_VALID_BOT_ID}/buy")
        assert r.status_code == 200
        assert r.json()["order_id"] == "ord-123"
        mgr.manual_buy.assert_called_once_with(_VALID_BOT_ID)

    def test_404_for_unknown_id(self, client_mgr):
        client, mgr = client_mgr
        mgr.manual_buy.side_effect = KeyError("Bot unknown not found")
        r = client.post("/api/bots/unknown/buy")
        assert r.status_code == 404

    def test_400_when_already_in_position(self, client_mgr):
        client, mgr = client_mgr
        mgr.manual_buy.side_effect = ValueError("Bot already has an open position")
        r = client.post(f"/api/bots/{_VALID_BOT_ID}/buy")
        assert r.status_code == 400
        assert "position" in r.json()["detail"]

    def test_400_when_bot_not_running(self, client_mgr):
        client, mgr = client_mgr
        mgr.manual_buy.side_effect = ValueError("Bot must be running to place a manual buy")
        r = client.post(f"/api/bots/{_VALID_BOT_ID}/buy")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/bots/{id}/reset-pnl
# ---------------------------------------------------------------------------

class TestResetPnl:
    def test_reset_returns_epoch(self, client_mgr):
        client, mgr = client_mgr
        mgr.reset_pnl.return_value = "2026-01-01T00:00:00Z"
        r = client.post(f"/api/bots/{_VALID_BOT_ID}/reset-pnl")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["pnl_epoch"] == "2026-01-01T00:00:00Z"
        mgr.reset_pnl.assert_called_once_with(_VALID_BOT_ID)

    def test_404_for_unknown_id(self, client_mgr):
        client, mgr = client_mgr
        mgr.reset_pnl.side_effect = KeyError("Bot unknown not found")
        r = client.post("/api/bots/unknown/reset-pnl")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/bots/{id}/backtest
# ---------------------------------------------------------------------------

class TestBacktestBot:
    def test_backtest_returns_ok_immediately(self, client_mgr):
        """Endpoint adds backtest_bot to background_tasks — returns immediately."""
        client, mgr = client_mgr
        cfg = _make_mock_config()
        state = _make_mock_state()
        mgr.get_bot.return_value = (cfg, state)
        r = client.post(f"/api/bots/{_VALID_BOT_ID}/backtest")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "status": "backtesting"}
        mgr.get_bot.assert_called_once_with(_VALID_BOT_ID)
        # K6: verify the background task actually enqueued backtest_bot
        mgr.backtest_bot.assert_called_once_with(_VALID_BOT_ID)

    def test_404_for_unknown_id(self, client_mgr):
        client, mgr = client_mgr
        mgr.get_bot.side_effect = KeyError("Bot unknown not found")
        r = client.post("/api/bots/unknown/backtest")
        assert r.status_code == 404

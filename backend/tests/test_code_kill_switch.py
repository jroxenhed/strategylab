"""The kill switch SL_CODE_NODES=0 (F435 W7 item 7.C, design note 4.9).

With SL_CODE_NODES=0:
- validate, backtest, inspect, preview and parse_code refuse code with
  code_disabled (one diagnostic per node that holds code), spawn and
  graph_update refuse a graph with code with detail code code_disabled;
- saving a graph with code still works (it is data);
- code_capabilities says enabled: false;
- a bot start is refused with a clear detail and pause_reason
  "code_disabled";
- an existing code-bearing bot pauses on its next tick with pause_reason
  "code_disabled" and does not crash-loop; one that holds a position runs
  its price exits until the position closes, then pauses (no code runs);
- a graph without code is not affected.

Money safety (plan 8.4): STRATEGYLAB_DATA_DIR and bots.json point at a
temporary folder; no bot is started (the start route's task is a no-op
mock); brokers are fakes or mocks that fail on any call; nothing trades.
"""
from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot_runner
import notifications
import routes.nodebuilder as nb_route
import shared
from bot_manager import BotState
from bot_runner import BotRunner
from tests.nodebuilder.code_graphs import (code_bot, daily_frame, graph_data, node,
                                           vision_data, wire, wrangle_bot_data)
from tests.test_bot_bidirectional_graph import FakeBroker, _Manager
from tests.test_graph_spawn import frame, leg_body, make_world

FRAME = daily_frame(400, seed=11)
OWN = frame(3)
WINDOW = {"ticker": "AAPL", "start": "2018-01-01", "end": "2020-01-01", "interval": "1d",
          "source": "yahoo"}


@pytest.fixture
def switch_off(monkeypatch):
    monkeypatch.setenv("SL_CODE_NODES", "0")


@pytest.fixture
def nb(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(shared, "_fetch", lambda *a, **k: FRAME)
    app = FastAPI()
    app.include_router(nb_route.router)
    return TestClient(app)


def _plain_graph() -> dict:
    data = copy.deepcopy(vision_data())
    data["nodes"]["rsi"]["params"]["period"] = 14
    data["nodes"].pop("vol")
    data["nodes"]["both"]["params"]["terms"] = ["@rsi_low"]
    data["wires"] = [w for w in data["wires"] if w["id"] not in ("w1", "w6")]
    return data


# ---------------------------------------------------------------------------
# The editor routes
# ---------------------------------------------------------------------------


def test_validate_marks_every_node_that_holds_code(nb, switch_off):
    body = nb.post("/api/nodebuilder/validate", json={"graph": vision_data()}).json()
    assert body["ok"] is False
    off = [d for d in body["diagnostics"] if d["code"] == "code_disabled"]
    assert sorted(d["node_id"] for d in off) == ["rsi", "vol"]  # an expression, a Wrangle
    assert body["diagnostics"][0]["code"] == "code_disabled"


def test_an_empty_wrangle_counts_as_code(nb, switch_off):
    data = vision_data()
    data["nodes"]["vol"].pop("code")
    body = nb.post("/api/nodebuilder/validate", json={"graph": data}).json()
    assert "vol" in [d["node_id"] for d in body["diagnostics"] if d["code"] == "code_disabled"]


def test_a_graph_without_code_is_not_affected(nb, switch_off):
    assert nb.post("/api/nodebuilder/validate", json={"graph": _plain_graph()}).json()["ok"]
    r = nb.post("/api/nodebuilder/backtest", json={"graph": _plain_graph(), **WINDOW})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("path, extra", [
    ("/api/nodebuilder/backtest", None),
    ("/api/nodebuilder/inspect", {"target": {"node_id": "vol"}}),
    ("/api/nodebuilder/preview", {}),
])
def test_cooking_routes_refuse_code(nb, switch_off, path, extra):
    if extra is None:
        body = {"graph": vision_data(), **WINDOW}
    else:
        body = {"graph": vision_data(), "window": WINDOW, **extra}
    r = nb.post(path, json=body)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "code_disabled"


def test_parse_code_and_capabilities_say_disabled(nb, switch_off):
    body = nb.post("/api/nodebuilder/parse_code", json={
        "code": "@z = 1.0", "context": "wrangle", "expected": None, "graph": None,
        "node_id": "w"}).json()
    assert [d["code"] for d in body["diagnostics"]] == ["code_disabled"]
    assert nb.get("/api/nodebuilder/code_capabilities").json()["enabled"] is False


# ---------------------------------------------------------------------------
# Save, spawn, graph_update, start
# ---------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(notifications, "notify_error", AsyncMock())
    return make_world(tmp_path, monkeypatch)


def _save(world, data, name="code graph"):
    r = world.client.post("/api/graphs", json={"name": name, "graph": data})
    assert r.status_code == 201, r.text
    return r.json()


def test_save_still_works(world, switch_off):
    env = _save(world, vision_data())
    r = world.client.put(f"/api/graphs/{env['id']}", json={"rev": 1, "graph": vision_data(2.5)})
    assert r.status_code == 200, r.text
    assert r.json()["rev"] == 2


def test_spawn_refuses_a_graph_with_code(world, switch_off):
    env = _save(world, vision_data())
    r = world.client.post(f"/api/graphs/{env['id']}/spawn",
                          json={"rev": 1, "legs": [leg_body("main", direction="long")]})
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "code_disabled"
    assert sorted(detail["node_ids"]) == ["rsi", "vol"]
    assert "SL_CODE_NODES=0" in detail["message"]
    assert world.mgr.bots == {}


def test_graph_update_refuses_a_revision_with_code(world, monkeypatch):
    env = _save(world, vision_data())
    r = world.client.post(f"/api/graphs/{env['id']}/spawn",
                          json={"rev": 1, "legs": [leg_body("main", direction="long")]})
    assert r.status_code == 201, r.text
    (bot_id,) = world.mgr.bots
    r = world.client.put(f"/api/graphs/{env['id']}", json={"rev": 1, "graph": vision_data(2.5)})
    assert r.status_code == 200
    monkeypatch.setenv("SL_CODE_NODES", "0")
    r = world.client.post(f"/api/bots/{bot_id}/graph_update",
                          json={"graph_id": env["id"], "rev": 2})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "code_disabled"
    assert world.mgr.bots[bot_id][0].graph_rev == 1


def test_start_is_refused_with_a_clear_detail(world, monkeypatch):
    env = _save(world, vision_data())
    r = world.client.post(f"/api/graphs/{env['id']}/spawn",
                          json={"rev": 1, "legs": [leg_body("main", direction="long")]})
    (bot_id,) = world.mgr.bots
    monkeypatch.setenv("SL_CODE_NODES", "0")
    r = world.client.post(f"/api/bots/{bot_id}/start")
    assert r.status_code == 400, r.text
    assert "SL_CODE_NODES=0" in r.json()["detail"]
    state = world.mgr.bots[bot_id][1]
    assert state.pause_reason == "code_disabled" and state.status == "error"
    assert world.mgr.tasks == {}


# ---------------------------------------------------------------------------
# A running bot
# ---------------------------------------------------------------------------


def _tick(runner, broker, df=OWN, cook=None):
    notify_error = AsyncMock()
    log_trade = MagicMock()

    async def go():
        patches = [
            patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=df)),
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
            await asyncio.sleep(0)
        finally:
            for p in reversed(patches):
                p.stop()

    asyncio.run(go())
    return notify_error, log_trade


SIGNAL = "@sig: bool = @close > 0\n"


def test_an_existing_bot_pauses_with_code_disabled_and_does_not_crash_loop(monkeypatch):
    monkeypatch.setenv("SL_CODE_NODES", "0")
    state = BotState()
    runner = BotRunner(code_bot(wrangle_bot_data(SIGNAL)), state, _Manager())
    broker = FakeBroker(price=float(OWN["Close"].iloc[-1]))
    ticks = []
    real_tick = runner._tick

    async def counting_tick():
        ticks.append(1)
        if len(ticks) > 3:
            raise AssertionError("crash loop")
        await real_tick()

    runner._tick = counting_tick
    cook = MagicMock(side_effect=AssertionError("no code may cook while disabled"))

    async def go():
        with patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=OWN)), \
                patch("bot_runner.get_trading_provider", return_value=broker), \
                patch("bot_runner.notify_error", AsyncMock()), \
                patch("bot_runner.cook_graph_bar", cook), \
                patch("asyncio.sleep", AsyncMock()):
            await asyncio.wait_for(runner.run(), 10)

    asyncio.run(go())
    assert ticks == [1]
    assert state.pause_reason == "code_disabled"
    assert state.status == "stopped"  # run() ended: paused, not looping
    assert broker.submitted == []


def test_a_running_bot_pauses_when_the_switch_turns_off(monkeypatch):
    """The program is cached from the first tick; on the next bar the cook
    finds the switch off and refuses to run the code."""
    state = BotState()
    runner = BotRunner(code_bot(wrangle_bot_data("@sig: bool = @close < 0\n")), state, _Manager())
    broker = FakeBroker(price=float(OWN["Close"].iloc[-2]))
    _tick(runner, broker, df=OWN.iloc[:-1])
    assert state.status != "error"
    monkeypatch.setenv("SL_CODE_NODES", "0")
    _tick(runner, broker)
    assert state.status == "error" and state.pause_reason == "code_disabled"
    assert broker.submitted == []


def test_a_bot_holding_a_position_runs_its_stop_then_pauses(monkeypatch):
    monkeypatch.setenv("SL_CODE_NODES", "0")
    price = float(OWN["Close"].iloc[-1])
    entry = price * 1.05
    state = BotState(entry_price=entry, trail_peak=entry, position_direction="long")
    state.last_bar_time = str(OWN.index[-2])
    broker = FakeBroker(price=price)
    broker.position = {"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": entry}
    runner = BotRunner(code_bot(wrangle_bot_data(SIGNAL)), state, _Manager())
    cook = MagicMock(side_effect=AssertionError("no code may cook while disabled"))
    _notify, log_trade = _tick(runner, broker, cook=cook)
    assert broker.closed == [("AAPL", "long")] and broker.submitted == []
    assert log_trade.call_args.kwargs["reason"] == "stop_loss"
    assert state.status == "error" and state.pause_reason == "code_disabled"


@pytest.mark.parametrize("value", ["0", "false", "OFF", " no "])
def test_the_switch_values(monkeypatch, value):
    from nodebuilder.code import code_enabled

    monkeypatch.setenv("SL_CODE_NODES", value)
    assert code_enabled() is False
    monkeypatch.delenv("SL_CODE_NODES")
    assert code_enabled() is True

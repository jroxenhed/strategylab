"""The code audit trail (F435 W7 item 7.C, design note 4.10).

One ``code_audit`` INFO line per code snippet on the logger
``strategylab.code_audit``:
- on every save of a graph that holds code (POST and PUT /api/graphs, the
  seed import, a library asset save);
- on every bot start (plus one summary line in the bot's own log), spawn
  and graph_update;
with the sha256 of the user's original source and the X-Forwarded-Email
user.  A graph without code logs nothing.  Nothing is refused.

Money safety (plan 8.4): STRATEGYLAB_DATA_DIR and bots.json point at a
temporary folder; the bot "started" here runs a no-op stub, never a
runner; the broker is a mock that fails on any call.
"""
from __future__ import annotations

import hashlib
import logging
import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import bot_manager as bot_manager_mod
import notifications
import routes.graph_library as library_route
from nodebuilder.code import AUDIT_LOGGER_NAME
from tests.nodebuilder.code_graphs import VISION_PERIOD, VOL_CODE, vision_data
from tests.test_graph_spawn import implicit_data, leg_body, make_world

EMAIL = "john@example.test"
LINE = re.compile(
    r"^code_audit event=(?P<event>\S+) (?P<fields>.*?) ?node_id=(?P<node_id>\S+) "
    r"name=(?P<name>\S+) level=(?P<level>\S+) sha256=(?P<sha256>[0-9a-f]{64}) "
    r"bytes=(?P<bytes>\d+) email=(?P<email>\S+)$")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(caplog) -> list[dict]:
    out = []
    for record in caplog.records:
        if record.name != AUDIT_LOGGER_NAME:
            continue
        m = LINE.match(record.getMessage())
        assert m, record.getMessage()
        fields = dict(f.split("=", 1) for f in m.group("fields").split()) if m.group("fields") else {}
        out.append({**m.groupdict(), **fields})
    return out


@pytest.fixture
def world(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(notifications, "notify_error", AsyncMock())
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    w = make_world(tmp_path, monkeypatch)
    w.client.headers["X-Forwarded-Email"] = EMAIL
    return w


def _save(world, data, name="audited"):
    r = world.client.post("/api/graphs", json={"name": name, "graph": data})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_graph_save_logs_one_line_per_snippet(world, caplog):
    env = _save(world, vision_data())
    lines = _lines(caplog)
    assert [(l["event"], l["node_id"], l["level"]) for l in lines] == [
        ("graph_save", "vol", "wrangle"), ("graph_save", "rsi", "expr:period")]
    vol, rsi = lines
    assert vol["sha256"] == _sha(VOL_CODE) and int(vol["bytes"]) == len(VOL_CODE.encode())
    assert rsi["sha256"] == _sha(VISION_PERIOD)
    assert (vol["graph_id"], vol["rev"], vol["email"]) == (env["id"], "1", EMAIL)

    caplog.clear()
    r = world.client.put(f"/api/graphs/{env['id']}", json={"rev": 1, "graph": vision_data(2.5)})
    assert r.status_code == 200
    assert [(l["event"], l["rev"]) for l in _lines(caplog)] == [("graph_save", "2")] * 2


def test_a_graph_without_code_logs_nothing(world, caplog):
    _save(world, implicit_data())
    assert _lines(caplog) == []


def test_the_seed_import_logs_its_code(world, caplog):
    r = world.client.post("/api/graphs/seed", json={"legacy": {"seeded": vision_data()}})
    assert r.status_code == 200 and len(r.json()["imported"]) == 1
    lines = _lines(caplog)
    assert [l["event"] for l in lines] == ["graph_save", "graph_save"]
    assert lines[0]["graph_id"] == r.json()["imported"][0]


def test_a_library_asset_save_logs_its_code(world, caplog):
    app = FastAPI()
    app.include_router(library_route.router)
    client = TestClient(app, headers={"X-Forwarded-Email": EMAIL})
    network = {"nodes": {"w": {"id": "w", "type": "wrangle", "name": "spread", "parent": None,
                               "params": {}, "code": "@z = @close * 2\n"}}, "wires": []}
    r = client.post("/api/graph_library", json={"name": "code_asset", "description": "",
                                                "network": network, "promoted": []})
    assert r.status_code == 201, r.text
    (line,) = _lines(caplog)
    assert (line["event"], line["asset"], line["version"], line["node_id"], line["email"]) == (
        "asset_save", "code_asset", "1", "w", EMAIL)
    assert line["sha256"] == _sha("@z = @close * 2\n")


def _spawn(world, env) -> str:
    r = world.client.post(f"/api/graphs/{env['id']}/spawn",
                          json={"rev": env["rev"], "legs": [leg_body("main", direction="long")]})
    assert r.status_code == 201, r.text
    return r.json()["bots"][0]["bot_id"]


def test_spawn_and_graph_update_log_their_code(world, caplog):
    env = _save(world, vision_data())
    caplog.clear()
    bot_id = _spawn(world, env)
    lines = _lines(caplog)
    assert [(l["event"], l["bot_id"], l["graph_id"], l["rev"]) for l in lines] == [
        ("spawn", bot_id, env["id"], "1")] * 2

    world.client.put(f"/api/graphs/{env['id']}", json={"rev": 1, "graph": vision_data(2.5)})
    world.broker.side_effect = None
    world.broker.return_value = MagicMock(get_positions=MagicMock(return_value=[]))
    caplog.clear()
    r = world.client.post(f"/api/bots/{bot_id}/graph_update", json={"graph_id": env["id"], "rev": 2})
    assert r.status_code == 200, r.text
    lines = _lines(caplog)
    assert [(l["event"], l["bot_id"], l["rev"], l["email"]) for l in lines] == [
        ("graph_update", bot_id, "2", EMAIL)] * 2


def test_a_bot_start_logs_its_code_and_a_summary_in_the_bot_log(world, caplog, monkeypatch):
    env = _save(world, vision_data())
    bot_id = _spawn(world, env)

    class StubRunner:
        def __init__(self, config, state, manager):
            pass

        async def run(self):
            return None

    monkeypatch.setattr(bot_manager_mod, "BotRunner", StubRunner)
    caplog.clear()
    r = world.client.post(f"/api/bots/{bot_id}/start")
    assert r.status_code == 200, r.text
    lines = _lines(caplog)
    assert [(l["event"], l["bot_id"], l["node_id"]) for l in lines] == [
        ("bot_start", bot_id, "vol"), ("bot_start", bot_id, "rsi")]
    assert lines[0]["sha256"] == _sha(VOL_CODE)
    summary = [e["msg"] for e in world.mgr.bots[bot_id][1].activity_log
               if e["msg"].startswith("Code audit")]
    assert len(summary) == 1 and "2 snippet(s)" in summary[0]
    assert _sha(VOL_CODE)[:12] in summary[0]

"""Loading bots.json with code (F435 W7 item 7.C, design note 4.11).

On load, BotManager runs prepare() (parse, scan, compile; never run) on
every code snippet of each graph bot:
- a snippet that does not prepare loads that bot paused (status error,
  pause_reason ``code_syntax: <node name> line 3``), never auto-resumed;
- the other bots load normally, and the manager never raises, even when
  the check itself fails;
- with SL_CODE_NODES=0 a bot whose graph holds code loads paused with
  ``code_disabled``;
- loading writes no code_audit line (starting does);
- one alert names every bot paused this way.

Money safety (plan 8.4): bots.json and STRATEGYLAB_DATA_DIR are in a
temporary folder; no bot is started (start_bot is a recording stub); no
broker is called.
"""
from __future__ import annotations

import json
import logging

import pytest

import bot_manager as bot_manager_mod
from bot_manager import BotManager
from nodebuilder.trading import nodes_code
from tests.nodebuilder.code_graphs import code_bot, plain_bot_data, wrangle_bot_data
from tests.test_graph_spawn import rule_config

GOOD = "@sig: bool = @close > 0\n"
BAD = "x = 1\ny = 2\nz = (y +\n@sig: bool = @close > 0\n"   # never closed: line 3


def _row(config, status="running") -> dict:
    return {"config": config.model_dump(mode="json"),
            "state": {"status": status, "user_stopped": False}}


def _bad_expression_bot():
    from tests.nodebuilder.code_graphs import graph_data, node, wire

    data = graph_data([
        node("t", "ticker", {"symbol": "NVDA", "interval": "1d"}),
        node("rsi", "rsi", {"period": {"expr": "7 if"}}, name="fast_rsi"),
        node("lo", "below", {"a": "@rsi", "threshold": 30, "out": "@lo"}),
        node("entry", "entry", {}),
    ], [wire("w1", "t", "rsi"), wire("w2", "rsi", "lo"), wire("w3", "lo", "entry")])
    return code_bot(data, bot_id="bot-expr", symbol="NVDA")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bot_manager_mod, "DATA_PATH", str(tmp_path / "bots.json"))
    monkeypatch.delenv("SL_CODE_NODES", raising=False)
    bad = code_bot(wrangle_bot_data(BAD), bot_id="bot-bad")
    bad.graph.nodes["w"].name = "spread_z"
    rows = [
        _row(rule_config(symbol="SPY", bot_id="bot-rule")),
        _row(bad),
        _row(code_bot(wrangle_bot_data(GOOD, symbol="MSFT"), bot_id="bot-good", symbol="MSFT")),
        _row(_bad_expression_bot(), status="stopped"),
        _row(code_bot(plain_bot_data("TSLA"), bot_id="bot-plain", symbol="TSLA")),
    ]
    (tmp_path / "bots.json").write_text(json.dumps({"bot_fund": 10_000, "bots": rows}))
    return tmp_path


def _load() -> BotManager:
    mgr = BotManager()
    mgr.load()
    return mgr


def test_a_syntax_error_loads_only_that_bot_paused(world):
    mgr = _load()
    assert set(mgr.bots) == {"bot-rule", "bot-bad", "bot-good", "bot-expr", "bot-plain"}
    assert mgr._unloaded == []
    _cfg, bad = mgr.bots["bot-bad"]
    assert bad.status == "error"
    assert bad.pause_reason == "code_syntax: spread_z line 3"
    _cfg, expr = mgr.bots["bot-expr"]
    assert expr.status == "error" and expr.pause_reason == "code_syntax: fast_rsi line 1"
    for bot_id in ("bot-rule", "bot-good", "bot-plain"):
        state = mgr.bots[bot_id][1]
        assert state.status == "stopped" and state.pause_reason is None, bot_id
    # The rows stay in bots.json, with the pause recorded.
    saved = {r["config"]["bot_id"]: r for r in json.loads((world / "bots.json").read_text())["bots"]}
    assert saved["bot-bad"]["state"]["pause_reason"] == "code_syntax: spread_z line 3"
    assert saved["bot-bad"]["config"]["graph"]["nodes"]["w"]["code"] == BAD


def test_a_paused_bot_is_not_auto_resumed(world, monkeypatch):
    mgr = _load()
    started: list[str] = []
    monkeypatch.setattr(mgr, "start_bot", lambda bot_id: started.append(bot_id))
    result = mgr.resume_was_running()
    assert "bot-bad" in result["skipped"] and "bot-bad" not in started
    assert sorted(started) == ["bot-good", "bot-plain", "bot-rule"]


def test_with_the_switch_off_every_code_bot_loads_paused(world, monkeypatch):
    monkeypatch.setenv("SL_CODE_NODES", "0")
    mgr = _load()
    for bot_id in ("bot-bad", "bot-good", "bot-expr"):
        state = mgr.bots[bot_id][1]
        assert (state.status, state.pause_reason) == ("error", "code_disabled"), bot_id
    for bot_id in ("bot-rule", "bot-plain"):
        assert mgr.bots[bot_id][1].pause_reason is None


def test_loading_writes_no_audit_line(world, caplog):
    with caplog.at_level(logging.INFO, logger="strategylab.code_audit"):
        _load()
    assert [r for r in caplog.records if r.name == "strategylab.code_audit"] == []


def test_a_check_that_fails_pauses_that_bot_and_nothing_else(world, monkeypatch):
    real = nodes_code.first_code_problem

    def flaky(graph):
        if "MSFT" in json.dumps(graph.model_dump(mode="json")):
            raise RuntimeError("boom")
        return real(graph)

    monkeypatch.setattr(nodes_code, "first_code_problem", flaky)
    mgr = _load()  # never raises
    good = mgr.bots["bot-good"][1]
    assert good.status == "error" and good.pause_reason.startswith("code_syntax:")
    assert mgr.bots["bot-plain"][1].pause_reason is None
    assert mgr.bots["bot-rule"][1].pause_reason is None


def test_one_alert_names_every_paused_bot(world, caplog):
    with caplog.at_level(logging.ERROR, logger="bot_manager"):
        _load()  # no event loop: the alert is a log line
    lines = [r.getMessage() for r in caplog.records if "loaded paused because of their code"
             in r.getMessage()]
    assert len(lines) == 1
    assert "bot-bad: code_syntax: spread_z line 3" in lines[0] and "bot-expr" in lines[0]


def test_a_paused_bot_starts_once_its_graph_is_fixed(world, monkeypatch):
    """The pause is a state, not a ban: a graph whose code prepares passes
    the start checks again."""
    mgr = _load()
    cfg, _state = mgr.bots["bot-bad"]
    fixed = cfg.model_copy(update={"graph": code_bot(wrangle_bot_data(GOOD)).graph})
    assert BotManager._check_graph(fixed) is not None


# ---------------------------------------------------------------------------
# Every other path that gives a bot a graph prepares its code too
# (decisions-pre "Bot safety rules": spawn, add, PATCH, graph_update)
# ---------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path, monkeypatch):
    import notifications
    from unittest.mock import AsyncMock

    from tests.test_graph_spawn import make_world

    monkeypatch.setattr(notifications, "notify_error", AsyncMock())
    monkeypatch.delenv("SL_CODE_NODES", raising=False)
    return make_world(tmp_path, monkeypatch)


def _inline_body(code: str, bot_id_symbol: str = "AAPL") -> dict:
    cfg = code_bot(wrangle_bot_data(code, symbol=bot_id_symbol), symbol=bot_id_symbol)
    body = cfg.model_dump(mode="json")
    body.pop("bot_id")
    return body


def test_add_and_patch_refuse_code_that_does_not_prepare(api):
    r = api.client.post("/api/bots", json=_inline_body(BAD))
    assert r.status_code == 400, r.text
    assert r.json()["node_id"] == "w" and "never closed" in r.json()["detail"]
    assert api.mgr.bots == {}

    r = api.client.post("/api/bots", json=_inline_body(GOOD))
    assert r.status_code == 200, r.text
    bot_id = r.json()["bot_id"]
    bad_graph = code_bot(wrangle_bot_data(BAD)).graph.model_dump(mode="json")
    r = api.client.patch(f"/api/bots/{bot_id}", json={"graph": bad_graph})
    assert r.status_code == 400, r.text
    assert r.json()["node_id"] == "w"
    assert api.mgr.bots[bot_id][0].graph.nodes["w"].code == GOOD


def test_spawn_and_graph_update_refuse_code_that_does_not_prepare(api):
    from tests.test_graph_spawn import leg_body

    bad = api.client.post("/api/graphs", json={"name": "bad", "graph": wrangle_bot_data(BAD)})
    assert bad.status_code == 201  # saving is fine: it is data
    r = api.client.post(f"/api/graphs/{bad.json()['id']}/spawn",
                        json={"rev": 1, "legs": [leg_body("main", direction="long")]})
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "graph_invalid" and detail["node_id"] == "w"
    assert "code_syntax" in [d["code"] for d in detail["diagnostics"]]

    good = api.client.post("/api/graphs", json={"name": "good", "graph": wrangle_bot_data(GOOD)})
    r = api.client.post(f"/api/graphs/{good.json()['id']}/spawn",
                        json={"rev": 1, "legs": [leg_body("main", direction="long")]})
    assert r.status_code == 201, r.text
    bot_id = r.json()["bots"][0]["bot_id"]
    api.client.put(f"/api/graphs/{good.json()['id']}",
                   json={"rev": 1, "graph": wrangle_bot_data(BAD)})
    r = api.client.post(f"/api/bots/{bot_id}/graph_update",
                        json={"graph_id": good.json()["id"], "rev": 2})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "graph_invalid"
    assert api.mgr.bots[bot_id][0].graph_rev == 1


def test_a_start_refused_for_its_code_gets_the_code_pause_reason(api):
    r = api.client.post("/api/bots", json=_inline_body(GOOD))
    bot_id = r.json()["bot_id"]
    cfg, state = api.mgr.bots[bot_id]
    broken = code_bot(wrangle_bot_data(BAD)).graph
    broken.nodes["w"].name = "spread_z"
    api.mgr.bots[bot_id] = (cfg.model_copy(update={"graph": broken}), state)
    r = api.client.post(f"/api/bots/{bot_id}/start")
    assert r.status_code == 400, r.text
    assert api.mgr.bots[bot_id][1].pause_reason == "code_syntax: spread_z line 3"
    assert api.mgr.tasks == {}

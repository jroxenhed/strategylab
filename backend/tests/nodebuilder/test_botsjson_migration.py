"""W1 item 1.A: a v1 graph snapshot in bots.json migrates when BotManager loads it.

Uses a temporary STRATEGYLAB_DATA_DIR and points bot_manager.DATA_PATH at a
file inside it.  No bot is started and no broker is touched: load() always
leaves bots stopped.
"""
from __future__ import annotations

import json
import os

import pytest

import bot_manager as _bot_manager_mod
from bot_manager import BotManager
from nodebuilder.models import Graph

_HERE = os.path.dirname(__file__)
with open(os.path.join(_HERE, "vectors", "v1_autorender.json")) as _fh:
    _V1 = json.load(_fh)


def _graph_bot_entry(bot_id: str, graph: dict) -> dict:
    return {
        "config": {
            "bot_id": bot_id,
            "strategy_name": "Graph test",
            "symbol": "AAPL",
            "interval": "1d",
            "buy_rules": [],
            "sell_rules": [],
            "allocated_capital": 100.0,
            "kind": "graph",
            "graph": graph,
        },
        "state": {"status": "running"},
    }


@pytest.fixture
def bots_file(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))
    path = tmp_path / "bots.json"
    monkeypatch.setattr(_bot_manager_mod, "DATA_PATH", str(path))
    return path


@pytest.mark.parametrize("name", sorted(_V1["botsjson"]))
def test_botsjson_v1_graph_migrates_on_load(bots_file, name):
    raw = _V1["botsjson"][name]
    assert raw["version"] == 1  # the shape BotManager.save wrote before v2
    bots_file.write_text(json.dumps({"bot_fund": 1000.0, "bots": [_graph_bot_entry("g-1", raw)]}))

    mgr = BotManager()
    mgr.load()

    assert "g-1" in mgr.bots, "a v1 graph bot must not be dropped on load"
    config, state = mgr.bots["g-1"]
    assert state.status == "stopped"
    assert not mgr.tasks
    g = config.graph
    assert g is not None and g.version == 2
    assert all(n.name and n.parent is None for n in g.nodes.values())
    assert all(w.to_port and w.to_port.startswith("in") for w in g.wires)
    # Same graph as migrating the API-shaped copy directly.
    assert g.model_dump(mode="json", by_alias=True) == Graph.model_validate(
        _V1["api"][name]
    ).model_dump(mode="json", by_alias=True)


def test_botsjson_is_rewritten_as_v2_and_reloads(bots_file):
    raw = _V1["botsjson"]["simple_long_rsi"]
    bots_file.write_text(json.dumps({"bot_fund": 1000.0, "bots": [_graph_bot_entry("g-1", raw)]}))

    first = BotManager()
    first.load()  # load() saves back after a successful load

    saved = json.loads(bots_file.read_text())
    stored = saved["bots"][0]["config"]["graph"]
    assert stored["version"] == 2
    assert all("name" in n and "subgraph" not in n for n in stored["nodes"].values())
    assert all(w["to_port"] for w in stored["wires"])

    second = BotManager()
    second.load()
    assert (
        second.bots["g-1"][0].graph.model_dump(mode="json")
        == first.bots["g-1"][0].graph.model_dump(mode="json")
    )


# ---------------------------------------------------------------------------
# DI-02: a row that does not load never leaves bots.json
# ---------------------------------------------------------------------------


def _bad_graph_rows() -> dict:
    """Graphs that cannot load, each for a different reason."""
    base = _V1["botsjson"]["simple_long_rsi"]
    newer = {**base, "version": 3, "promoted": {"x": 1}}  # a field W1 does not know
    bad_name = json.loads(json.dumps(base))
    first = next(iter(bad_name["nodes"]))
    bad_name["nodes"][first]["name"] = "Bad Name"
    too_old = {**base, "version": 0}
    return {"newer": newer, "bad_name": bad_name, "too_old": too_old}


@pytest.mark.parametrize("reason", sorted(_bad_graph_rows()))
def test_unloadable_graph_bot_is_kept_byte_identical(bots_file, caplog, reason):
    good = _V1["botsjson"]["simple_long_rsi"]
    bad_entry = _graph_bot_entry("bad-1", _bad_graph_rows()[reason])
    rows = [_graph_bot_entry("g-1", good), bad_entry, _graph_bot_entry("g-2", good)]
    bots_file.write_text(json.dumps({"bot_fund": 1000.0, "bots": rows}, indent=2))

    mgr = BotManager()
    with caplog.at_level("ERROR", logger="bot_manager"):
        mgr.load()  # saves back right away
    assert set(mgr.bots) == {"g-1", "g-2"}
    assert any(
        "bad-1" in r.getMessage() and r.levelname == "ERROR" for r in caplog.records
    ), "each skip is logged at ERROR with the bot id"

    mgr.save()
    saved = json.loads(bots_file.read_text())
    ids = [row["config"]["bot_id"] for row in saved["bots"]]
    assert ids == ["g-1", "bad-1", "g-2"], "the bad row keeps its place"
    assert json.dumps(saved["bots"][1], indent=2) == json.dumps(bad_entry, indent=2)

    # It never starts: not in bots, so resume and start cannot reach it.
    assert "bad-1" not in mgr.bots
    assert not mgr.tasks

    # Deleting the bot it followed keeps it (at the end), and a second
    # manager loading the saved file keeps it again.
    mgr.delete_bot("g-1")
    saved = json.loads(bots_file.read_text())
    assert [row["config"]["bot_id"] for row in saved["bots"]] == ["g-2", "bad-1"]
    again = BotManager()
    again.load()
    again.save()
    rows_after = json.loads(bots_file.read_text())["bots"]
    kept = [row for row in rows_after if row["config"]["bot_id"] == "bad-1"]
    assert kept and json.dumps(kept[0], indent=2) == json.dumps(bad_entry, indent=2)


def test_graph_bot_wires_are_saved_in_port_order(bots_file):
    """DI-01: bots.json (through BotConfig) stores each node's inputs in port
    order, so Wave 0 code reads the same comparison after a rollback."""
    graph = {
        "_version": 2,
        "nodes": {
            "/t": {"id": "/t", "type": "ticker"},
            "/r": {"id": "/r", "type": "rsi"},
            "/s": {"id": "/s", "type": "sma"},
            "/c": {"id": "/c", "type": "above"},
            "/e": {"id": "/e", "type": "entry"},
        },
        "wires": [
            {"id": "w1", "from": "/t", "to": "/r", "to_port": "in0"},
            {"id": "w2", "from": "/t", "to": "/s", "to_port": "in0"},
            {"id": "right", "from": "/s", "to": "/c", "to_port": "in1"},
            {"id": "left", "from": "/r", "to": "/c", "to_port": "in0"},
            {"id": "we", "from": "/c", "to": "/e", "to_port": "in0"},
        ],
    }
    bots_file.write_text(json.dumps({"bot_fund": 1000.0, "bots": [_graph_bot_entry("g-1", graph)]}))
    mgr = BotManager()
    mgr.load()
    stored = json.loads(bots_file.read_text())["bots"][0]["config"]["graph"]
    assert [w["id"] for w in stored["wires"]] == ["w1", "w2", "left", "right", "we"]

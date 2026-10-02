"""A bot never reads the library (F435 W6, plan D7).

Every route that gives a bot a graph bakes its library assets in: spawn,
add by graph_id and graph_update go through load_graph_snapshot (covered in
test_graph_library.py); add with an inline graph and PATCH with a graph are
covered here.  compile_bot_graph, the one compile path of a bot (add, start,
tick), refuses a graph that still holds a locked instance instead of
resolving it from the library.

Fix pass (fix-B2): every snapshot path writes the promoted values into
their targets (BS-01), and bots.json is copied once to bots.json.pre-w6; a
graph the editor refuses (an interface_mismatch) never becomes a bot
(BS-02); a switched-off locked instance compiles with no library lookup
(BS-03).
"""
from __future__ import annotations

import copy
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import bot_manager as bot_manager_mod
import routes.graph_library as library_route
from bot_manager import BotConfig, BotManager
from bot_runner import compile_bot_graph
from nodebuilder.kernel import assets as kernel_assets
from nodebuilder.models import Graph, GraphValidationError
from tests.test_graph_library import (INTERFACE, PROMOTED, asset_body, asset_network, backtest,
                                     hand_graph, instance_graph, node, trades_of, wires)
from tests.test_graph_spawn import _broker_with, leg_body, make_world


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    app = FastAPI()
    app.include_router(library_route.router)
    w.library = TestClient(app)
    return w


def _inline_body(graph: dict) -> dict:
    return {"strategy_name": "inline", "symbol": "AAPL", "interval": "1d", "buy_rules": [],
            "sell_rules": [], "allocated_capital": 1000.0, "kind": "graph", "graph": graph,
            "graph_group": "main", "data_source": "alpaca-iex", "broker": "alpaca"}


def _asset(world, **over):
    r = world.library.post("/api/graph_library", json=asset_body(**over))
    assert r.status_code == 201, r.text
    return r.json()


def _assert_baked(graph, version):
    inst = graph.nodes["rf"]
    assert inst.locked is False
    assert (inst.asset_ref.name, inst.asset_ref.version) == ("regime_filter", version)
    children = {n.type for n in graph.nodes.values() if n.parent == "rf"}
    assert children >= {"sma", "above", "subnet_input", "subnet_output"}
    assert not any(n.locked for n in graph.nodes.values())


# ---------------------------------------------------------------------------
# add_bot with an inline graph
# ---------------------------------------------------------------------------


def test_inline_add_bakes_the_library_in(world):
    _asset(world)
    r = world.client.post("/api/bots", json=_inline_body(instance_graph(30)))
    assert r.status_code == 200, r.text
    cfg, _ = world.mgr.bots[r.json()["bot_id"]]
    _assert_baked(cfg.graph, 1)
    before = compile_bot_graph(cfg.graph).steps
    # A later library delete changes nothing for the bot.
    assert world.library.delete("/api/graph_library/regime_filter/1").status_code == 204
    assert compile_bot_graph(world.mgr.bots[r.json()["bot_id"]][0].graph).steps == before


def test_inline_add_with_a_missing_asset_is_400_asset_missing(world):
    r = world.client.post("/api/bots", json=_inline_body(instance_graph(30, inst_name="mine")))
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "asset_missing"
    assert (detail["node_id"], detail["name"]) == ("rf", "mine")
    assert (detail["asset"], detail["version"]) == ("regime_filter", 1)
    assert world.mgr.bots == {}


# ---------------------------------------------------------------------------
# PATCH /api/bots/{id} with a graph
# ---------------------------------------------------------------------------


def _inline_bot(world) -> str:
    _asset(world)
    r = world.client.post("/api/bots", json=_inline_body(instance_graph(30)))
    assert r.status_code == 200, r.text
    return r.json()["bot_id"]


def test_patch_with_a_graph_bakes_the_library_in(world):
    bot_id = _inline_bot(world)
    _asset(world, network=asset_network(period=3), promoted=[])  # version 2
    _broker_with(world)  # flat: a changed graph asks the broker (LM-3)
    r = world.client.patch(f"/api/bots/{bot_id}", json={"graph": instance_graph(version=2)})
    assert r.status_code == 200, r.text
    graph = world.mgr.bots[bot_id][0].graph
    _assert_baked(graph, 2)
    assert graph.nodes["rf::sma"].params["period"] == 3


def test_patch_with_the_same_locked_graph_is_unchanged_and_never_asks_the_broker(world):
    # The bot's stored graph is baked; the editor's copy is locked.  Baked
    # before the compare, they are the same graph, so the broker (which
    # raises on every call here) is never asked.
    bot_id = _inline_bot(world)
    before = world.mgr.bots[bot_id][0].graph.model_dump(mode="json")
    r = world.client.patch(f"/api/bots/{bot_id}", json={"graph": instance_graph(30)})
    assert r.status_code == 200, r.text
    assert world.mgr.bots[bot_id][0].graph.model_dump(mode="json") == before


def test_patch_with_a_missing_asset_is_400_before_the_broker_is_asked(world):
    bot_id = _inline_bot(world)
    before = world.mgr.bots[bot_id][0].graph.model_dump(mode="json")
    # world.broker raises on every call: the refusal must come first.
    r = world.client.patch(f"/api/bots/{bot_id}", json={"graph": instance_graph(30, version=9)})
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "asset_missing"
    assert (detail["asset"], detail["version"]) == ("regime_filter", 9)
    assert world.mgr.bots[bot_id][0].graph.model_dump(mode="json") == before


# ---------------------------------------------------------------------------
# compile_bot_graph never resolves the library
# ---------------------------------------------------------------------------


def _locked_config() -> BotConfig:
    return BotConfig(strategy_name="x", symbol="AAPL", interval="1d", buy_rules=[],
                     sell_rules=[], allocated_capital=1000.0, kind="graph",
                     graph=Graph.model_validate(instance_graph(30)), graph_group="main",
                     data_source="alpaca-iex", broker="alpaca")


def test_compile_bot_graph_refuses_a_locked_instance_even_when_the_library_has_it(
        world, monkeypatch):
    _asset(world)
    asked = []
    real = kernel_assets.default_resolver()
    monkeypatch.setattr(kernel_assets, "_default_resolver",
                        lambda n, v: asked.append((n, v)) or real(n, v))
    graph = Graph.model_validate(instance_graph(30))
    with pytest.raises(GraphValidationError) as info:
        compile_bot_graph(graph)
    assert info.value.node_id == "rf"
    assert getattr(info.value, "code", None) == "asset_unbaked"
    assert asked == []
    # The same refusal on BotManager's check (add, start) and so the tick.
    with pytest.raises(GraphValidationError):
        BotManager._check_graph(_locked_config())
    assert asked == []


def test_a_baked_graph_compiles_without_asking_the_library(world, monkeypatch):
    from routes.graphs import bake_in_library

    _asset(world)
    baked = bake_in_library(Graph.model_validate(instance_graph(30)))
    asked = []
    monkeypatch.setattr(kernel_assets, "_default_resolver",
                        lambda n, v: asked.append((n, v)))
    assert compile_bot_graph(copy.deepcopy(baked)).steps
    assert asked == []


# ---------------------------------------------------------------------------
# BS-01: every bot snapshot carries its promoted values
# ---------------------------------------------------------------------------

W6_NODE_FIELDS = ("promoted", "asset_ref", "locked", "meta")


def plain_promoted_graph(lookback=20, period=50) -> dict:
    """The regime strategy in a plain subnet (no library asset): the subnet
    promotes lookback -> sma/period; sma's own stored period is *period*."""
    net = asset_network(period)
    nodes = [
        node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}, name="tick"),
        node("net", "subnet", {"lookback": lookback}, name="regime",
             promoted=[dict(PROMOTED[0])]),
        *[{**n, "parent": "net"} for n in net["nodes"].values()],
        node("entry", "entry", {"signal": "@regime_on"}),
        node("off", "not", {"signal": "@regime_on", "out": "@off"}),
        node("exit", "exit", {"signal": "@off"}),
    ]
    return {"_version": 3, "nodes": {n["id"]: n for n in nodes},
            "wires": [*net["wires"], *wires([("o1", "t", "net", "in0"), ("o2", "net", "entry", "in0"),
                                             ("o3", "net", "off", "in0"),
                                             ("o4", "off", "exit", "in0")])]}


def _drop(graph: Graph, fields) -> Graph:
    """*graph* as a reader that does not know *fields* loads it."""
    data = graph.model_dump(mode="json", by_alias=True)
    for n in data["nodes"].values():
        for key in fields:
            n.pop(key, None)
    return Graph.model_validate(data)


def _spawn(world, graph_id, rev):
    return world.client.post(f"/api/graphs/{graph_id}/spawn",
                             json={"rev": rev, "legs": [leg_body("main", 2000.0)]})


def _bot_graph_via(world, how: str, data: dict) -> Graph:
    """The graph a bot stores when *data* reaches it by route *how*."""
    if how == "inline":
        r = world.client.post("/api/bots", json=_inline_body(data))
        assert r.status_code == 200, r.text
        return world.mgr.bots[r.json()["bot_id"]][0].graph
    if how == "patch":
        r = world.client.post("/api/bots", json=_inline_body(hand_graph(50)))
        assert r.status_code == 200, r.text
        bot_id = r.json()["bot_id"]
        _broker_with(world)  # flat: a changed graph asks the broker (LM-3)
        r = world.client.patch(f"/api/bots/{bot_id}", json={"graph": data})
        assert r.status_code == 200, r.text
        return world.mgr.bots[bot_id][0].graph
    env = world.store.create(f"g {how}", data if how != "graph_update" else hand_graph(50))
    if how == "graph_id":
        body = {**_inline_body(data), "graph": None, "graph_id": env["id"], "graph_rev": 1}
        r = world.client.post("/api/bots", json=body)
        assert r.status_code == 200, r.text
        return world.mgr.bots[r.json()["bot_id"]][0].graph
    r = _spawn(world, env["id"], 1)
    assert r.status_code == 201, r.text
    bot_id = r.json()["bots"][0]["bot_id"]
    if how == "graph_update":
        world.store.update(env["id"], 1, data)
        _broker_with(world)
        r = world.client.post(f"/api/bots/{bot_id}/graph_update",
                              json={"graph_id": env["id"], "rev": 2})
        assert r.status_code == 200, r.text
    return world.mgr.bots[bot_id][0].graph


@pytest.mark.parametrize("how", ["spawn", "graph_id", "graph_update", "inline", "patch"])
def test_every_snapshot_path_writes_the_promoted_value_into_its_target(world, how):
    """A Wave 5 reader drops promoted (rollback): it must still compute
    lookback 20, not sma's own stored 50."""
    graph = _bot_graph_via(world, how, plain_promoted_graph(lookback=20, period=50))
    assert graph.nodes["sma"].params["period"] == 20
    w5 = _drop(graph, W6_NODE_FIELDS)
    assert "promoted" not in w5.nodes["net"].model_dump(mode="json")
    assert compile_bot_graph(w5).steps == compile_bot_graph(graph).steps
    assert trades_of(backtest(w5.model_dump(mode="json", by_alias=True))) == \
        trades_of(backtest(hand_graph(20)))


def test_a_library_instance_snapshot_carries_its_promoted_value(world):
    """The instance sets lookback 30; the asset's sma stores 50 and its
    default is 50.  A reader that ignores promoted gets 30."""
    _asset(world)
    graph = _bot_graph_via(world, "inline", instance_graph(30))
    assert graph.nodes["rf::sma"].params["period"] == 30
    no_promoted = _drop(graph, ("promoted",))
    assert compile_bot_graph(no_promoted).steps == compile_bot_graph(graph).steps
    assert trades_of(backtest(no_promoted.model_dump(mode="json", by_alias=True))) == \
        trades_of(backtest(hand_graph(30)))


def _w5_row(graph: dict) -> dict:
    cfg = json.loads(_locked_config().model_dump_json())
    cfg.update(graph=graph, bot_id="bot-w5", graph_id=None, graph_rev=None, graph_group="main",
               graph_direction_mode=None)
    return {"config": cfg, "state": {"status": "stopped"}}


def test_a_bots_json_from_before_wave_6_is_copied_once(tmp_path, monkeypatch):
    path = tmp_path / "bots.json"
    monkeypatch.setattr(bot_manager_mod, "DATA_PATH", str(path))
    monkeypatch.setattr(bot_manager_mod, "_load_trades", lambda: [])
    raw = json.dumps({"bot_fund": 1000.0, "bots": [_w5_row(hand_graph(20))]}, indent=2)
    path.write_text(raw)
    BotManager().load()
    copy_path = tmp_path / "bots.json.pre-w6"
    assert copy_path.read_text() == raw  # the file as Wave 5 wrote it
    # Never overwritten, whatever bots.json holds later.
    path.write_text(json.dumps({"bot_fund": 5.0, "bots": []}))
    BotManager().load()
    assert copy_path.read_text() == raw


def test_a_bots_json_with_wave_6_graphs_gets_no_pre_w6_copy(tmp_path, monkeypatch):
    path = tmp_path / "bots.json"
    monkeypatch.setattr(bot_manager_mod, "DATA_PATH", str(path))
    monkeypatch.setattr(bot_manager_mod, "_load_trades", lambda: [])
    path.write_text(json.dumps({"bot_fund": 1000.0,
                                "bots": [_w5_row(plain_promoted_graph(20))]}))
    BotManager().load()
    assert not (tmp_path / "bots.json.pre-w6").exists()


# ---------------------------------------------------------------------------
# BS-02: a graph the editor refuses never becomes a bot
# ---------------------------------------------------------------------------

BAD_INTERFACE = {"reads": INTERFACE["reads"],
                 "writes": [*INTERFACE["writes"],
                            {"name": "@nope", "class": "point", "dtype": "bool"}]}


def _assert_interface_refusal(r):
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "graph_invalid"
    assert detail["node_id"] == "rf"  # the editor's id, not a baked rf::child
    assert any(d["code"] == "interface_mismatch" and d["node_id"] == "rf"
               for d in detail["diagnostics"])


def test_an_interface_mismatch_is_refused_on_every_bot_path(world):
    from nodebuilder.diagnostics import validate_graph_data

    _asset(world, interface=BAD_INTERFACE)  # declares a write its network never makes
    data = instance_graph(30)
    assert any(d.code == "interface_mismatch" and d.node_id == "rf"
               for d in validate_graph_data(data))  # the editor refuses it
    env = world.store.create("bad", data)
    _assert_interface_refusal(_spawn(world, env["id"], 1))
    body = {**_inline_body(data), "graph": None, "graph_id": env["id"], "graph_rev": 1}
    _assert_interface_refusal(world.client.post("/api/bots", json=body))
    _assert_interface_refusal(world.client.post("/api/bots", json=_inline_body(data)))
    assert world.mgr.bots == {}
    # graph_update and PATCH too (the broker is never asked: it raises here).
    good = world.store.create("good", hand_graph(30))
    r = _spawn(world, good["id"], 1)
    assert r.status_code == 201, r.text
    bot_id = r.json()["bots"][0]["bot_id"]
    before = world.mgr.bots[bot_id][0].graph.model_dump(mode="json")
    world.store.update(good["id"], 1, data)
    _assert_interface_refusal(world.client.post(
        f"/api/bots/{bot_id}/graph_update", json={"graph_id": good["id"], "rev": 2}))
    r = world.client.post("/api/bots", json=_inline_body(hand_graph(30)))
    inline_id = r.json()["bot_id"]
    _assert_interface_refusal(world.client.patch(f"/api/bots/{inline_id}", json={"graph": data}))
    assert world.mgr.bots[bot_id][0].graph.model_dump(mode="json") == before


# ---------------------------------------------------------------------------
# BS-03: a switched-off locked instance needs no library
# ---------------------------------------------------------------------------


def _with_bypassed_instance(base: dict) -> dict:
    """*base* plus a bypassed locked instance of a missing asset that nothing reads."""
    data = copy.deepcopy(base)
    data["nodes"]["rf"] = node("rf", "subnet", {}, name="unused", bypass=True,
                               asset_ref={"name": "regime_filter", "version": 7}, locked=True)
    data["wires"].append({"id": "w_rf", "from": "t", "to": "rf", "to_port": "in0"})
    return data


def test_a_bypassed_locked_instance_compiles_with_no_library_lookup(world, monkeypatch):
    asked = []
    monkeypatch.setattr(kernel_assets, "_default_resolver",
                        lambda n, v: asked.append((n, v)))
    graph = Graph.model_validate(_with_bypassed_instance(hand_graph(20)))
    assert compile_bot_graph(graph).steps == compile_bot_graph(
        Graph.model_validate(hand_graph(20))).steps
    assert asked == []
    # The inline add keeps it (its asset cannot be baked; it computes nothing).
    r = world.client.post("/api/bots", json=_inline_body(_with_bypassed_instance(hand_graph(20))))
    assert r.status_code == 200, r.text
    assert world.mgr.bots[r.json()["bot_id"]][0].graph.nodes["rf"].locked is True
    # Switched back on, the same graph is refused (asset_missing at the bake).
    on = _with_bypassed_instance(hand_graph(20))
    on["nodes"]["rf"]["bypass"] = False
    r = world.client.post("/api/bots", json=_inline_body(on))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "asset_missing", r.text

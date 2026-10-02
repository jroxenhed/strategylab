"""Graph library: asset storage, routes, used_by and bot bake-in (F435 W6 6.B).

- POST /api/graph_library saves version latest + 1; each version file is
  written once and never changes; a deleted number is never given out again.
- Asset names match ^[a-z_][a-z0-9_]{0,63}$ (400 name_invalid otherwise).
- used_by comes from an in-memory index over saved graphs: it follows a
  graph save, rename and delete, and is rebuilt from disk after a restart.
- DELETE of a version a graph uses makes that graph show asset_missing.
- Spawn bakes the asset's nodes into the bot's own graph, so a later library
  edit or delete never changes the bot; a missing asset at spawn is a 400
  asset_missing naming the instance.
- Acceptance (plan W6): one "regime_filter" asset dropped into 3 graphs
  with lookback promoted to 3 values backtests to 3 correct, different
  results (each equal to the same graph built by hand).

Money safety (plan 8.4): STRATEGYLAB_DATA_DIR and bots.json point at a
temporary folder; no bot is started; the broker is a mock that fails the
test if anything calls it; no order is placed.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.graph_library as library_route
import routes.graphs as graphs_route
from nodebuilder import storage as storage_mod
from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.diagnostics import validate_graph_data
from nodebuilder.models import Graph
from nodebuilder.run import run_graph_backtest_cooked
from nodebuilder.storage import (
    AssetLibrary,
    GraphStore,
    get_library,
    get_store,
    graph_dirs,
    resolve_asset,
)
from tests.test_graph_spawn import frame, leg_body, make_world, pair_data


# ---------------------------------------------------------------------------
# Graph and asset builders
# ---------------------------------------------------------------------------


def node(nid, typ, params=None, parent=None, name=None, **extra):
    return {"id": nid, "type": typ, "name": name or nid, "parent": parent,
            "params": params or {}, **extra}


def wires(rows):
    return [{"id": w, "from": a, "to": b, "to_port": p} for w, a, b, p in rows]


PROMOTED = [{"name": "lookback", "label": "Lookback", "target": "sma/period",
             "type": "int", "default": 50}]
INTERFACE = {"reads": [{"name": "@close", "class": "point", "dtype": "float"}],
             "writes": [{"name": "@regime_on", "class": "point", "dtype": "bool"}]}
PALETTE = {"category": "rules", "label": "Regime Filter", "glyph": "R"}


def asset_network(period=50) -> dict:
    """The regime_filter network: close above its SMA writes @regime_on."""
    return {
        "nodes": {n["id"]: n for n in [
            node("in0", "subnet_input", {"port": 0}),
            node("sma", "sma", {"period": period, "source": "@close", "out": "@sma_rf"}),
            node("above", "above", {"a": "@close", "b": "@sma_rf", "out": "@regime_on"}),
            node("out", "subnet_output", {}),
        ]},
        "wires": wires([("w1", "in0", "sma", "in0"), ("w2", "sma", "above", "in0"),
                        ("w3", "above", "out", "in0")]),
    }


def asset_body(name="regime_filter", **over) -> dict:
    body = {"name": name, "description": "Close above its SMA", "network": asset_network(),
            "promoted": PROMOTED, "interface": INTERFACE, "palette": PALETTE}
    body.update(over)
    return body


def instance_graph(lookback=None, version=1, symbol="AAPL", asset="regime_filter",
                   inst_name="regime") -> dict:
    """Ticker -> locked asset instance -> Entry (on @regime_on), and
    Not(@regime_on) -> Exit.  ``lookback`` None leaves the promoted param
    at its default."""
    params = {} if lookback is None else {"lookback": lookback}
    nodes = [
        node("t", "ticker", {"symbol": symbol, "interval": "1d"}, name="tick"),
        node("rf", "subnet", params, name=inst_name,
             asset_ref={"name": asset, "version": version}, locked=True),
        node("entry", "entry", {"signal": "@regime_on"}),
        node("off", "not", {"signal": "@regime_on", "out": "@off"}),
        node("exit", "exit", {"signal": "@off"}),
    ]
    return {"_version": 3, "nodes": {n["id"]: n for n in nodes},
            "wires": wires([("w1", "t", "rf", "in0"), ("w2", "rf", "entry", "in0"),
                            ("w3", "rf", "off", "in0"), ("w4", "off", "exit", "in0")])}


def hand_graph(period, symbol="AAPL") -> dict:
    """The same strategy as instance_graph, built by hand with no asset."""
    nodes = [
        node("t", "ticker", {"symbol": symbol, "interval": "1d"}, name="tick"),
        node("sma", "sma", {"period": period, "source": "@close", "out": "@sma_rf"}),
        node("above", "above", {"a": "@close", "b": "@sma_rf", "out": "@regime_on"}),
        node("entry", "entry", {"signal": "@regime_on"}),
        node("off", "not", {"signal": "@regime_on", "out": "@off"}),
        node("exit", "exit", {"signal": "@off"}),
    ]
    return {"_version": 3, "nodes": {n["id"]: n for n in nodes},
            "wires": wires([("w1", "t", "sma", "in0"), ("w2", "sma", "above", "in0"),
                            ("w3", "above", "entry", "in0"), ("w4", "above", "off", "in0"),
                            ("w5", "off", "exit", "in0")])}


def backtest(graph_data: dict, seed: int = 3):
    req = GraphBacktestRequest(graph=Graph.model_validate(graph_data), ticker="AAPL",
                               start="2023-01-02", end="2023-12-29", interval="1d",
                               source="yahoo", initial_capital=10_000.0)
    response, _cook = run_graph_backtest_cooked(
        req, frames={("AAPL", "1d"): frame(seed)}, keep_all=False)
    return response


def trades_of(response) -> list[tuple]:
    return [(t.get("type") if isinstance(t, dict) else getattr(t, "type", None),
             t.get("date") if isinstance(t, dict) else getattr(t, "date", None),
             round(float(t.get("price") if isinstance(t, dict) else getattr(t, "price")), 6))
            for t in (response.trades or [])]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    """The library and graph routes on a temporary data dir."""
    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(library_route.router)
    app.include_router(graphs_route.router)
    client = TestClient(app)
    client.tmp = tmp_path
    return client


def _post(client, body):
    return client.post("/api/graph_library", json=body)


def _list(client) -> dict:
    r = client.get("/api/graph_library")
    assert r.status_code == 200, r.text
    return {a["name"]: a for a in r.json()["assets"]}


# ---------------------------------------------------------------------------
# Versions and files
# ---------------------------------------------------------------------------


def test_post_gives_version_latest_plus_one_and_the_contract_shape(env):
    r = _post(env, asset_body())
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["version"] == 1
    assert set(first) == {"name", "version", "description", "stream_schema", "interface",
                          "promoted", "palette", "network", "created_at"}
    assert first["name"] == "regime_filter" and first["stream_schema"] == 1
    assert first["interface"] == INTERFACE and first["palette"] == PALETTE
    assert first["promoted"] == PROMOTED
    assert set(first["network"]) == {"nodes", "wires"}
    assert set(first["network"]["nodes"]) == {"in0", "sma", "above", "out"}
    assert first["created_at"].endswith("Z")

    r = _post(env, asset_body(network=asset_network(period=30)))
    assert r.status_code == 201 and r.json()["version"] == 2
    item = _list(env)["regime_filter"]
    assert item["versions"] == [1, 2] and item["latest"] == 2
    assert item["palette"] == PALETTE and item["interface"] == INTERFACE
    assert item["used_by"] == []

    # Each version is its own file and reads back as saved.
    folder = env.tmp / "library" / "regime_filter"
    assert sorted(p.name for p in folder.iterdir()) == ["1.json", "2.json"]
    got = env.get("/api/graph_library/regime_filter/1")
    assert got.status_code == 200 and got.json() == first
    assert env.get("/api/graph_library/regime_filter/2").json()["network"]["nodes"]["sma"][
        "params"]["period"] == 30


def test_version_files_are_immutable(env, monkeypatch):
    _post(env, asset_body())
    path = env.tmp / "library" / "regime_filter" / "1.json"
    before = path.read_bytes()
    for period in (10, 20, 30):
        assert _post(env, asset_body(network=asset_network(period))).status_code == 201
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.bak"))  # no backup copies: a file never changes
    # Even if the store picked a number that is taken, it refuses to replace it.
    lib = get_library()
    monkeypatch.setattr(lib, "_high_water", lambda name: 0)
    with pytest.raises(RuntimeError):
        lib.create("regime_filter", asset_network(), PROMOTED)
    assert path.read_bytes() == before


def test_a_deleted_version_number_is_never_given_out_again(env):
    for _ in range(3):
        _post(env, asset_body())
    assert env.delete("/api/graph_library/regime_filter/3").status_code == 204
    assert env.get("/api/graph_library/regime_filter/3").status_code == 404
    assert _list(env)["regime_filter"]["versions"] == [1, 2]
    assert _post(env, asset_body()).json()["version"] == 4
    # Deleting every version takes the asset off the list; the numbers stay taken.
    for v in (1, 2, 4):
        assert env.delete(f"/api/graph_library/regime_filter/{v}").status_code == 204
    assert "regime_filter" not in _list(env)
    assert _post(env, asset_body()).json()["version"] == 5
    # A fresh library on the same folder (a restart) agrees.
    assert AssetLibrary(env.tmp)._high_water("regime_filter") == 5


def test_delete_and_get_of_a_missing_version_are_404(env):
    assert env.delete("/api/graph_library/regime_filter/1").status_code == 404
    assert env.get("/api/graph_library/regime_filter/1").status_code == 404
    assert env.get("/api/graph_library/Not_Valid/1").status_code == 404
    assert env.get("/api/graph_library/regime_filter/0").status_code == 404
    assert env.get("/api/graph_library/regime_filter/x").status_code == 422


def test_a_damaged_version_file_is_409_and_resolves_to_none(env, monkeypatch):
    _post(env, asset_body())
    _post(env, asset_body())
    path = env.tmp / "library" / "regime_filter" / "2.json"
    path.write_text("{not json")
    # A fresh library (no cached copy) reads the damaged file.
    monkeypatch.setattr(storage_mod, "_libraries", {})
    r = env.get("/api/graph_library/regime_filter/2")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "asset_corrupt"
    assert resolve_asset("regime_filter", 2) is None
    assert resolve_asset("regime_filter", 1) is not None
    # The list still shows the asset, with the newest readable version's data.
    item = _list(env)["regime_filter"]
    assert item["versions"] == [1, 2] and item["palette"] == PALETTE
    # A damaged version can be deleted.
    assert env.delete("/api/graph_library/regime_filter/2").status_code == 204


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Regime", "1abc", "a-b", "a b", "", "x" * 65, "é"])
def test_bad_asset_names_are_400_name_invalid(env, name):
    r = _post(env, asset_body(name=name))
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "name_invalid"
    assert set(body) == {"detail", "node_id", "code", "diagnostics"}
    assert body["diagnostics"][0]["code"] == "name_invalid"
    assert not (env.tmp / "library").exists() or not any((env.tmp / "library").iterdir())


@pytest.mark.parametrize("name", ["_x", "a", "a" * 64, "rsi_2_filter"])
def test_good_asset_names_are_accepted(env, name):
    assert _post(env, asset_body(name=name)).status_code == 201


@pytest.mark.parametrize("promoted", [
    [{"name": "lookback", "label": "L", "target": "nope/period", "type": "int", "default": 5}],
    [{"name": "lookback", "label": "L", "target": "period", "type": "int", "default": 5}],
    [{"name": "lookback", "label": "L", "target": "sma/period", "type": "int", "default": "5"}],
    [{"name": "lookback", "label": "L", "target": "sma/period", "type": "int", "default": True}],
    [{"name": "lookback", "label": "L", "target": "sma/period", "type": "weird", "default": 5}],
    [{"name": "Look", "label": "L", "target": "sma/period", "type": "int", "default": 5}],
    [PROMOTED[0], PROMOTED[0]],
])
def test_bad_promoted_params_are_400_promoted_invalid(env, promoted):
    r = _post(env, asset_body(promoted=promoted))
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "promoted_invalid"


def test_promoted_target_may_reach_into_a_nested_network(env):
    net = asset_network()
    net["nodes"]["inner"] = node("inner", "subnet")
    net["nodes"]["deep"] = node("deep", "sma", {"period": 5}, parent="inner")
    body = asset_body(network=net, promoted=[
        *PROMOTED,
        {"name": "fast", "label": "Fast", "target": "inner/deep/period", "type": "int",
         "default": 5}])
    assert _post(env, body).status_code == 201, _post(env, body).text


def test_badly_shaped_or_unparsable_bodies(env):
    # A bad palette or interface is a 422 (shape).
    assert _post(env, asset_body(palette={"category": "math", "label": "x",
                                          "glyph": "R"})).status_code == 422
    assert _post(env, asset_body(interface={"reads": [{"name": "@c", "class": "row",
                                                       "dtype": "float"}]})).status_code == 422
    assert _post(env, {**asset_body(), "extra": 1}).status_code == 422
    # No palette and no interface: stored as null and an empty interface.
    body = asset_body()
    del body["palette"], body["interface"]
    data = _post(env, body).json()
    assert data["palette"] is None and data["interface"] == {"reads": [], "writes": []}
    # An empty network is a 400.
    r = _post(env, asset_body(name="empty", network={"nodes": {}, "wires": []}, promoted=[]))
    assert r.status_code == 400 and r.json()["code"] == "request_invalid"
    # A network that does not parse (a reserved '::' id) is a 400 in the 4.4 shape.
    net = asset_network()
    net["nodes"]["a::b"] = node("a::b", "sma")
    r = _post(env, asset_body(name="bad_net", network=net))
    assert r.status_code == 400, r.text
    assert set(r.json()) == {"detail", "node_id", "code", "diagnostics"}
    assert "bad_net" not in _list(env)


def test_routes_do_file_work_in_the_thread_pool(env, monkeypatch):
    calls = []
    real = library_route.run_in_threadpool

    async def spy(fn, *args, **kw):
        calls.append(getattr(fn, "__name__", repr(fn)))
        return await real(fn, *args, **kw)

    monkeypatch.setattr(library_route, "run_in_threadpool", spy)
    assert _post(env, asset_body()).status_code == 201
    assert env.get("/api/graph_library").status_code == 200
    assert env.get("/api/graph_library/regime_filter/1").status_code == 200
    assert env.delete("/api/graph_library/regime_filter/1").status_code == 204
    assert calls == ["create", "asset_list", "get", "delete"]


def test_graph_dirs_lists_each_asset_folder_for_tmp_cleanup(env):
    _post(env, asset_body())
    _post(env, asset_body(name="other"))
    tmp = env.tmp
    assert graph_dirs(tmp) == [tmp / "graphs", tmp / "library", tmp / "library" / "other",
                               tmp / "library" / "regime_filter"]


def test_storage_registers_its_resolver_with_the_kernel(env):
    from nodebuilder.kernel import assets as kernel_assets
    assert kernel_assets.default_resolver() is resolve_asset
    _post(env, asset_body())
    got = resolve_asset("regime_filter", 1)
    assert got["version"] == 1 and got["promoted"] == PROMOTED
    got["network"]["nodes"].clear()  # callers get their own copy
    assert resolve_asset("regime_filter", 1)["network"]["nodes"]
    assert resolve_asset("regime_filter", 9) is None
    assert resolve_asset("Bad Name", 1) is None


# ---------------------------------------------------------------------------
# used_by
# ---------------------------------------------------------------------------


def test_used_by_follows_graph_save_rename_and_delete(env):
    _post(env, asset_body())
    _post(env, asset_body(name="other"))
    store = get_store()
    a = store.create("Alpha", instance_graph(20))
    b = store.create("beta", instance_graph(30))
    store.create("plain", hand_graph(20))
    users = _list(env)["regime_filter"]["used_by"]
    assert users == [{"graph_id": a["id"], "name": "Alpha"},
                     {"graph_id": b["id"], "name": "beta"}]
    assert _list(env)["other"]["used_by"] == []

    # Rename shows the new name; a save that drops the instance drops the graph.
    store.update(a["id"], 1, instance_graph(20), name="Alpha 2")
    store.update(b["id"], 1, hand_graph(30))
    assert _list(env)["regime_filter"]["used_by"] == [{"graph_id": a["id"], "name": "Alpha 2"}]
    # A graph that now uses the other asset moves over.
    store.update(b["id"], 2, instance_graph(30, asset="other"))
    assert _list(env)["other"]["used_by"] == [{"graph_id": b["id"], "name": "beta"}]

    # Delete drops it.
    env.delete(f"/api/graphs/{a['id']}?rev=2")
    assert _list(env)["regime_filter"]["used_by"] == []


def test_used_by_counts_an_unlocked_copy_too(env):
    """An unlocked instance keeps asset_ref for provenance; "where used" lists it."""
    _post(env, asset_body())
    data = instance_graph(20)
    inst = data["nodes"]["rf"]
    inst["locked"] = False
    for nid, child in asset_network()["nodes"].items():
        data["nodes"][f"c_{nid}"] = {**child, "id": f"c_{nid}", "parent": "rf"}
    for w in asset_network()["wires"]:
        data["wires"].append({**w, "id": f"c_{w['id']}", "from": f"c_{w['from']}",
                              "to": f"c_{w['to']}"})
    g = get_store().create("copy", data)
    assert _list(env)["regime_filter"]["used_by"] == [{"graph_id": g["id"], "name": "copy"}]


def test_used_by_is_rebuilt_after_a_restart(env):
    _post(env, asset_body())
    a = get_store().create("Alpha", instance_graph(20))
    fresh = GraphStore(env.tmp)  # a restart: the index is built from the files
    assert fresh.asset_users() == {"regime_filter": [{"graph_id": a["id"], "name": "Alpha"}]}


def test_the_asset_list_reads_no_graph_file(env, monkeypatch):
    _post(env, asset_body())
    store = get_store()
    for i in range(5):
        store.create(f"g{i}", instance_graph(20 + i))
    opened = []
    real_read = type(env.tmp).read_text

    def spy(self, *a, **kw):
        if "graphs" in self.parts:
            opened.append(self)
        return real_read(self, *a, **kw)

    monkeypatch.setattr(type(env.tmp), "read_text", spy)
    assert len(_list(env)["regime_filter"]["used_by"]) == 5
    assert opened == []


# ---------------------------------------------------------------------------
# Compile: asset_missing after a DELETE
# ---------------------------------------------------------------------------


def test_delete_then_a_referencing_graph_shows_asset_missing(env):
    _post(env, asset_body())
    data = instance_graph(20)
    assert not [d for d in validate_graph_data(data) if d.severity == "error"]
    assert env.delete("/api/graph_library/regime_filter/1").status_code == 204
    found = [d for d in validate_graph_data(data) if d.code == "asset_missing"]
    assert found and found[0].node_id == "rf"
    # The graph itself still loads and saves (no crash).
    assert get_store().create("still here", data)["rev"] == 1


def test_an_instance_pins_its_version(env):
    """A library version bump does not change an existing instance."""
    _post(env, asset_body())                                   # v1: SMA period from lookback
    _post(env, asset_body(network=asset_network(period=7),     # v2: nothing promoted
                          promoted=[]))
    v1 = trades_of(backtest(instance_graph(20, version=1)))
    assert v1 == trades_of(backtest(hand_graph(20)))
    assert trades_of(backtest(instance_graph(None, version=2))) == trades_of(backtest(hand_graph(7)))
    assert v1 != trades_of(backtest(hand_graph(7)))


# ---------------------------------------------------------------------------
# Acceptance (plan W6)
# ---------------------------------------------------------------------------


def test_one_asset_in_three_graphs_with_three_lookbacks(env):
    """Save "regime_filter" once, drop it into 3 graphs with lookback 10, 30
    and 60, backtest all 3: each equals its hand-built twin, and the 3 differ."""
    assert _post(env, asset_body()).json()["version"] == 1
    store = get_store()
    results = {}
    for lookback in (10, 30, 60):
        saved = store.create(f"regime {lookback}", instance_graph(lookback))
        loaded = store.get(saved["id"])["graph"]
        assert loaded["nodes"]["rf"]["locked"] is True
        assert loaded["nodes"]["rf"]["params"] == {"lookback": lookback}
        got = backtest(loaded)
        want = backtest(hand_graph(lookback))
        assert trades_of(got) == trades_of(want), lookback
        assert got.summary == want.summary
        assert trades_of(got), f"lookback {lookback} made no trades"
        results[lookback] = trades_of(got)
    assert len({tuple(t) for t in results.values()}) == 3
    users = _list(env)["regime_filter"]["used_by"]
    assert sorted(u["name"] for u in users) == ["regime 10", "regime 30", "regime 60"]


def test_the_default_lookback_is_the_promoted_default(env):
    _post(env, asset_body())
    assert trades_of(backtest(instance_graph(None))) == trades_of(backtest(hand_graph(50)))


# ---------------------------------------------------------------------------
# Bots: bake-in at spawn
# ---------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    app = FastAPI()
    app.include_router(library_route.router)
    w.library = TestClient(app)
    return w


def _spawn(world, graph_id, rev, legs):
    return world.client.post(f"/api/graphs/{graph_id}/spawn", json={"rev": rev, "legs": legs})


def test_a_spawned_bot_is_immune_to_a_later_library_edit_or_delete(world):
    from bot_manager import BotManager
    from bot_runner import compile_bot_graph

    assert world.library.post("/api/graph_library", json=asset_body()).status_code == 201
    env = world.store.create("regime bot", instance_graph(30))
    r = _spawn(world, env["id"], 1, [leg_body("main", 2000.0)])
    assert r.status_code == 201, r.text
    [b] = r.json()["bots"]
    cfg, state = world.mgr.bots[b["bot_id"]]
    assert state.status == "stopped" and world.mgr.tasks == {}
    world.broker.assert_not_called()

    # The bot's graph holds the asset's nodes itself: nothing is locked, the
    # instance keeps asset_ref for provenance.
    inst = cfg.graph.nodes["rf"]
    assert inst.locked is False
    assert (inst.asset_ref.name, inst.asset_ref.version) == ("regime_filter", 1)
    children = [n for n in cfg.graph.nodes.values() if n.parent == "rf"]
    assert {n.type for n in children} >= {"sma", "above", "subnet_input", "subnet_output"}
    before = compile_bot_graph(cfg.graph)

    # Edit the library: delete version 1 and save a different version 2.
    assert world.library.delete("/api/graph_library/regime_filter/1").status_code == 204
    assert world.library.post("/api/graph_library", json=asset_body(
        network=asset_network(period=3), promoted=[])).status_code == 201
    assert resolve_asset("regime_filter", 1) is None

    # The bot still compiles to the same program, now and after a reload
    # from bots.json.
    after = compile_bot_graph(world.mgr.bots[b["bot_id"]][0].graph)
    assert after.steps == before.steps
    reloaded = BotManager()
    reloaded.load()
    cfg2, _ = reloaded.bots[b["bot_id"]]
    assert compile_bot_graph(cfg2.graph).steps == before.steps
    # And it still trades like the hand-built lookback-30 graph (the
    # promoted value is baked in too), with the library's version 1 gone.
    baked = cfg2.graph.model_dump(mode="json", by_alias=True)
    assert trades_of(backtest(baked)) == trades_of(backtest(hand_graph(30)))
    saved = json.loads((world.tmp / "bots.json").read_text())
    assert not any(n.get("locked") for row in saved["bots"]
                   for n in row["config"]["graph"]["nodes"].values())


def test_spawn_with_a_missing_asset_is_400_asset_missing(world):
    env = world.store.create("orphan", instance_graph(30, inst_name="my_regime"))
    r = _spawn(world, env["id"], 1, [leg_body("main", 2000.0)])
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "asset_missing"
    assert detail["name"] == "my_regime" and detail["node_id"] == "rf"
    assert (detail["asset"], detail["version"]) == ("regime_filter", 1)
    assert "my_regime" in detail["message"]
    assert world.mgr.bots == {} and world.saves == []


def test_add_by_graph_id_also_bakes_and_refuses_a_missing_asset(world):
    env = world.store.create("orphan", instance_graph(30))
    body = {"strategy_name": "x", "symbol": "AAPL", "interval": "1d", "buy_rules": [],
            "sell_rules": [], "allocated_capital": 1000.0, "kind": "graph",
            "graph_id": env["id"], "graph_rev": 1, "graph_group": "main",
            "data_source": "alpaca-iex", "broker": "alpaca"}
    r = world.client.post("/api/bots", json=body)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "asset_missing"
    world.library.post("/api/graph_library", json=asset_body())
    r = world.client.post("/api/bots", json=body)
    assert r.status_code == 200, r.text
    cfg, _ = world.mgr.bots[r.json()["bot_id"]]
    assert cfg.graph.nodes["rf"].locked is False


def test_a_graph_with_no_asset_is_not_touched_by_the_bake(world):
    graph = Graph.model_validate(pair_data())
    assert graphs_route.bake_in_library(graph) is graph


# ---------------------------------------------------------------------------
# W6 fix pass (fix-B2): cache, promoted checks, damaged files, writes
# ---------------------------------------------------------------------------


def test_a_delete_wins_over_a_read_in_flight(env, monkeypatch):
    """LD-01 / BS-05: a read that parsed the file before a DELETE must not put
    the deleted version back in the cache, or it would keep resolving (and
    baking into bots) until a restart."""
    _post(env, asset_body())
    lib = get_library()
    lib._cache.clear()  # the read below must go to the file
    real = storage_mod.asset_file_problem
    fired = []

    def racing(data, name, version):
        # The file text is read; the DELETE lands before it is cached.
        if not fired:
            fired.append(1)
            lib.delete(name, version)
        return real(data, name, version)

    monkeypatch.setattr(storage_mod, "asset_file_problem", racing)
    assert lib.get("regime_filter", 1)["version"] == 1  # the read in flight gets what it read
    assert fired
    assert lib.versions("regime_filter") == []
    assert resolve_asset("regime_filter", 1) is None
    assert env.get("/api/graph_library/regime_filter/1").status_code == 404
    found = [d for d in validate_graph_data(instance_graph(20)) if d.code == "asset_missing"]
    assert found and found[0].node_id == "rf"


def test_the_parsed_asset_cache_is_bounded(env, monkeypatch):
    """LD-06: at most CACHE_MAX parsed files, least recently used out first."""
    lib = get_library()
    monkeypatch.setattr(lib, "CACHE_MAX", 3, raising=False)
    for period in (5, 6, 7, 8, 9):
        assert _post(env, asset_body(network=asset_network(period))).status_code == 201
    assert len(lib._cache) == 3
    for version in (1, 2, 3, 4, 5):  # every version still reads, from the file
        assert lib.get("regime_filter", version)["version"] == version
    assert list(lib._cache) == [("regime_filter", 3), ("regime_filter", 4), ("regime_filter", 5)]
    lib.get("regime_filter", 3)  # a hit is now the most recently used
    assert list(lib._cache)[-1] == ("regime_filter", 3)


def _damage(env_tmp, version, monkeypatch):
    (env_tmp / "library" / "regime_filter" / f"{version}.json").write_text("{not json")
    monkeypatch.setattr(storage_mod, "_libraries", {})  # a fresh library: no cached copy


def test_latest_is_the_newest_readable_version(env, monkeypatch):
    """LD-04: the Tab menu places "latest", so it must be a version that reads."""
    _post(env, asset_body())
    _post(env, asset_body(network=asset_network(30)))
    _damage(env.tmp, 2, monkeypatch)
    item = _list(env)["regime_filter"]
    assert item["versions"] == [1, 2]  # still listed, so it can be deleted
    assert item["latest"] == 1


def test_a_damaged_file_answer_carries_its_sentence_under_message(env, monkeypatch):
    """LD-05: the client shows detail.message."""
    _post(env, asset_body())
    _damage(env.tmp, 1, monkeypatch)
    r = env.get("/api/graph_library/regime_filter/1")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "asset_corrupt"
    assert "damaged" in detail["message"] and detail["detail"] == detail["message"]


@pytest.mark.parametrize("promoted, says", [
    ([{"name": "lookback", "target": "sma/no_such_param", "type": "int", "default": 5}],
     "no param 'no_such_param'"),
    ([{"name": "lookback", "target": "sma/period", "type": "bool", "default": None}],
     "but sma/period is int"),
    ([PROMOTED[0], {**PROMOTED[0], "name": "lookback_2"}], "already set by promoted param"),
    ([{"name": "lookback", "target": "../sma/period", "type": "int", "default": 5}], None),
])
def test_promoted_params_compile_refuses_are_refused_at_save(env, promoted, says):
    """LD-02: the library checks promoted params by the compile rules, so it
    never stores an immutable version whose every instance fails to compile."""
    r = _post(env, asset_body(promoted=promoted))
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "promoted_invalid"
    if says:
        assert says in body["detail"], body["detail"]
    assert "regime_filter" not in _list(env)


def test_promoted_targets_and_defaults_compile_accepts_are_saved(env):
    """LD-02 / KA-9: what compile accepts, the library stores: a "./" target,
    an int default written as 50.0, a multi-select list.  A numeric string
    for a number type stays refused (test_bad_promoted_params...)."""
    body = asset_body(promoted=[{"name": "lookback", "label": "L", "target": "./sma/period",
                                 "type": "int", "default": 50.0}])
    r = _post(env, body)
    assert r.status_code == 201, r.text
    assert trades_of(backtest(instance_graph(20))) == trades_of(backtest(hand_graph(20)))


def test_promoting_a_nested_asset_instances_promoted_param_is_saved(env):
    """The compile check expands nested library instances first, as compile
    does, so promoting an inner instance's own promoted param is accepted
    (and a name it does not promote is refused)."""
    assert _post(env, asset_body()).status_code == 201  # regime_filter v1
    net = {"nodes": {
        "in0": node("in0", "subnet_input", {"port": 0}),
        "inner": node("inner", "subnet", {}, asset_ref={"name": "regime_filter", "version": 1},
                      locked=True),
        "out": node("out", "subnet_output", {}),
    }, "wires": wires([("w1", "in0", "inner", "in0"), ("w2", "inner", "out", "in0")])}
    ok = [{"name": "lb", "label": "LB", "target": "inner/lookback", "type": "int", "default": 20}]
    r = _post(env, asset_body(name="wrapper", network=net, promoted=ok))
    assert r.status_code == 201, r.text
    bad = [{**ok[0], "target": "inner/nope"}]
    r = _post(env, asset_body(name="wrapper", network=net, promoted=bad))
    assert r.status_code == 400 and r.json()["code"] == "promoted_invalid", r.text


def test_a_version_file_is_never_replaced_even_past_the_exists_check(env, monkeypatch):
    """LD-07: the write itself refuses an existing file (no check-then-replace),
    and the clash is a 409 with a sentence, not a 500."""
    _post(env, asset_body())
    path = env.tmp / "library" / "regime_filter" / "1.json"
    before = path.read_bytes()
    lib = get_library()
    monkeypatch.setattr(lib, "_high_water", lambda name: 0)  # picks the taken number 1
    real_exists = type(path).exists
    # Another writer's file appears after any check: exists() says no.
    monkeypatch.setattr(type(path), "exists",
                        lambda self, *a, **k: False if self == path else real_exists(self, *a, **k))
    r = _post(env, asset_body(network=asset_network(period=3)))
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "asset_version_taken" and "Save again" in detail["message"]
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp"))


def test_a_file_error_is_a_500_with_a_sentence(env, monkeypatch):
    """LD-07: an OSError on save or delete is answered with a sentence."""
    def no_disk(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    with monkeypatch.context() as m:
        m.setattr(storage_mod, "_write_new_file", no_disk)
        r = _post(env, asset_body())
    assert r.status_code == 500, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "library_io_error" and "Permission denied" in detail["message"]
    assert _post(env, asset_body()).status_code == 201
    path = env.tmp / "library" / "regime_filter" / "1.json"
    real_unlink = type(path).unlink
    monkeypatch.setattr(type(path), "unlink", lambda self, *a, **k: no_disk() if self == path
                        else real_unlink(self, *a, **k))
    r = env.delete("/api/graph_library/regime_filter/1")
    assert r.status_code == 500 and r.json()["detail"]["code"] == "library_io_error"
    assert path.exists()


def test_an_unlocked_copy_saves_as_a_new_version_with_plain_ids(env):
    """KA-2 (storage side): a network taken from an unlocked copy has ids like
    rf::sma; the library stores them plain, and the new version compiles."""
    from routes.graphs import bake_in_library

    _post(env, asset_body())
    baked = bake_in_library(Graph.model_validate(instance_graph(20)))
    dumped = baked.model_dump(mode="json", by_alias=True)
    children = {nid: {**n, "parent": None if n["parent"] == "rf" else n["parent"]}
                for nid, n in dumped["nodes"].items() if nid.startswith("rf::")}
    net = {"nodes": children,
           "wires": [w for w in dumped["wires"] if w["from"].startswith("rf::")]}
    r = _post(env, asset_body(network=net))
    assert r.status_code == 201, r.text
    assert set(r.json()["network"]["nodes"]) == {"in0", "sma", "above", "out"}
    assert trades_of(backtest(instance_graph(20, version=2))) == \
        trades_of(backtest(hand_graph(20)))


# ---------------------------------------------------------------------------
# Bots: a damaged asset file at spawn (LD-03)
# ---------------------------------------------------------------------------


def test_spawn_with_a_damaged_asset_file_is_409_asset_corrupt(world, monkeypatch):
    assert world.library.post("/api/graph_library", json=asset_body()).status_code == 201
    env = world.store.create("damaged", instance_graph(30, inst_name="my_regime"))
    _damage(world.tmp, 1, monkeypatch)
    r = _spawn(world, env["id"], 1, [leg_body("main", 2000.0)])
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "asset_corrupt"
    assert (detail["node_id"], detail["name"]) == ("rf", "my_regime")
    assert (detail["asset"], detail["version"]) == ("regime_filter", 1)
    assert "damaged" in detail["message"] and "not in the library" not in detail["message"]
    assert world.mgr.bots == {} and world.saves == []

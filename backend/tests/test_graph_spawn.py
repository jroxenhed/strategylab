"""Bot spawn, add-by-graph-id and graph_update (F435 W5 5.D, plan D7).

- POST /api/graphs/{id}/spawn makes one STOPPED bot per leg through one
  BotManager.add_bots call and one save; all or nothing.
- 409 rev_conflict on a stale rev; 400 same_symbol_same_direction for two
  legs (or a leg and an existing bot) the exclusive-symbol guard refuses;
  400 group_unknown, graph_invalid, fund, leg_invalid.
- A synthetic AAPL-long / MSFT-short pair graph backtests to two group
  results plus combined, then spawns two stopped bots with the right symbol,
  direction and capital.
- POST /api/bots with graph_id, graph_rev and graph_group (no graph) loads
  the saved revision server-side.
- POST /api/bots/{id}/graph_update sets graph and graph_rev together, and
  refuses in position, a symbol change, a direction change, a missing group
  and a stale rev.

Money safety (plan 8.4): STRATEGYLAB_DATA_DIR and bots.json point at a
temporary folder; no bot is started; the broker is a mock that fails the
test if anything calls it; no order is placed.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import bot_manager as bot_manager_mod
import bot_runner as bot_runner_mod
import routes.bots as bots_route
import routes.graphs as graphs_route
from bot_manager import BotConfig, BotManager
from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.models import Graph
from nodebuilder.run import run_graph_backtest_cooked
from nodebuilder.storage import get_store


# ---------------------------------------------------------------------------
# Graph builders (shared with test_bot_summary_graph_rev.py and
# test_bot_bidirectional_graph.py)
# ---------------------------------------------------------------------------


def node(nid, typ, params=None, parent=None, name=None):
    return {"id": nid, "type": typ, "name": name or nid, "parent": parent,
            "params": params or {}}


def leg(p: str, symbol: str, parent, lo=38, hi=62, interval="1d"):
    """Ticker -> RSI -> below/above -> Entry/Exit, ids prefixed *p*."""
    nodes = [
        node(f"{p}t", "ticker", {"symbol": symbol, "interval": interval}, parent, name="tick"),
        node(f"{p}rsi", "rsi", {"period": 14}, parent, name="rsi"),
        node(f"{p}lo", "below", {"threshold": lo, "out": f"@lo_{p}"}, parent, name="lo"),
        node(f"{p}hi", "above", {"threshold": hi, "out": f"@hi_{p}"}, parent, name="hi"),
        node(f"{p}entry", "entry", {}, parent, name="entry"),
        node(f"{p}exit", "exit", {}, parent, name="exit"),
    ]
    wires = [
        (f"{p}w1", f"{p}t", f"{p}rsi"),
        (f"{p}w2", f"{p}rsi", f"{p}lo"),
        (f"{p}w3", f"{p}rsi", f"{p}hi"),
        (f"{p}w4", f"{p}lo", f"{p}entry"),
        (f"{p}w5", f"{p}hi", f"{p}exit"),
    ]
    return nodes, wires


def switch_leg(p: str, symbol: str, parent, on_flip="hold", volume_above=550_000):
    """A regime_switch group's children: long Entry/Exit on RSI below/above,
    short Entry/Exit the other way round, and a Regime terminal on
    volume > *volume_above* (independent of the RSI signals, so every mix
    of regime and entry turns up on a synthetic frame)."""
    nodes, wires = leg(p, symbol, parent)
    nodes += [
        node(f"{p}sentry", "entry", {"side": "short"}, parent, name="sentry"),
        node(f"{p}sexit", "exit", {"side": "short"}, parent, name="sexit"),
        node(f"{p}up", "above", {"a": "@volume", "threshold": volume_above, "out": f"@up_{p}"},
             parent, name="up"),
        node(f"{p}reg", "regime", {"on_flip": on_flip}, parent, name="regime"),
    ]
    wires += [
        (f"{p}w6", f"{p}hi", f"{p}sentry"),
        (f"{p}w7", f"{p}lo", f"{p}sexit"),
        (f"{p}w8", f"{p}t", f"{p}up"),
        (f"{p}w9", f"{p}up", f"{p}reg"),
    ]
    return nodes, wires


def group(gid, direction, weight=1.0, ticker="tick"):
    return node(gid, "output_group",
                {"direction": direction, "ticker": ticker, "capital_weight": weight})


def graph_data(nodes, wires) -> dict:
    return {
        "_version": 3,
        "nodes": {n["id"]: n for n in nodes},
        "wires": [{"id": w, "from": a, "to": b, "to_port": "in0"} for w, a, b in wires],
    }


def pair_data(a_symbol="AAPL", b_symbol="MSFT", a_dir="long", b_dir="short",
              a_name="long_leg", b_name="short_leg") -> dict:
    a_nodes, a_wires = leg("a_", a_symbol, a_name)
    b_nodes, b_wires = leg("b_", b_symbol, b_name, lo=60, hi=40)
    return graph_data([group(a_name, a_dir), group(b_name, b_dir), *a_nodes, *b_nodes],
                      a_wires + b_wires)


def implicit_data(symbol="AAPL") -> dict:
    nodes, wires = leg("", symbol, None)
    return graph_data(nodes, wires)


def switch_data(symbol="AAPL", name="sw", on_flip="hold") -> dict:
    nodes, wires = switch_leg("s_", symbol, name, on_flip=on_flip)
    return graph_data([group(name, "regime_switch"), *nodes], wires)


def frame(seed: int, n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100 + 9 * np.sin(t / (9 + seed)) + np.cumsum(rng.normal(0, 0.8, n))
    idx = pd.date_range("2023-01-02", periods=n, freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": close, "High": close + 0.6, "Low": close - 0.6,
                         "Close": close, "Volume": rng.integers(100_000, 1_000_000, n)}, index=idx)


def make_world(tmp_path, monkeypatch, fund: float = 10_000.0):
    """A BotManager on temporary files behind the graph and bot routes."""
    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bot_manager_mod, "DATA_PATH", str(tmp_path / "bots.json"))
    monkeypatch.setattr(bot_manager_mod, "_load_trades", lambda: [])
    broker = MagicMock(side_effect=AssertionError("no broker call is allowed here"))
    monkeypatch.setattr(bot_manager_mod, "get_trading_provider", broker)
    monkeypatch.setattr(bot_runner_mod, "get_trading_provider", broker)

    mgr = BotManager()
    mgr.bot_fund = fund
    saves: list[int] = []
    real_save = mgr.save

    def counting_save():
        saves.append(1)
        real_save()

    mgr.save = counting_save
    adds: list[int] = []
    real_add = mgr.add_bots

    def spy_add(configs, **kw):
        adds.append(len(configs))
        return real_add(configs, **kw)

    mgr.add_bots = spy_add
    monkeypatch.setattr(bots_route, "bot_manager", mgr)
    app = FastAPI()
    app.include_router(graphs_route.router)
    app.include_router(bots_route.router)
    return SimpleNamespace(client=TestClient(app), mgr=mgr, saves=saves, adds=adds,
                           broker=broker, store=get_store(), tmp=tmp_path)


def leg_body(group_name, capital=5000.0, **extra):
    return {"group": group_name, "allocated_capital": capital, "broker": "alpaca",
            "data_source": "alpaca-iex", "interval_override": None, "strategy_name": None,
            **extra}


def rule_config(symbol="AAPL", direction="long", **extra) -> BotConfig:
    return BotConfig(strategy_name="rule bot", symbol=symbol, interval="1d",
                     buy_rules=[], sell_rules=[], long_buy_rules=None, long_sell_rules=None,
                     short_buy_rules=None, short_sell_rules=None, allocated_capital=1000.0,
                     direction=direction, **extra)


@pytest.fixture
def world(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch)


def _spawn(world, graph_id, rev, legs):
    return world.client.post(f"/api/graphs/{graph_id}/spawn", json={"rev": rev, "legs": legs})


def _assert_nothing_added(world, saves_before=0):
    assert world.mgr.bots == {}
    assert len(world.saves) == saves_before
    assert world.mgr.tasks == {}


# ---------------------------------------------------------------------------
# Spawn
# ---------------------------------------------------------------------------


def test_pair_backtests_to_two_groups_and_spawns_two_stopped_bots(world):
    data = pair_data()
    env = world.store.create("pair_aapl_msft", data)

    # The backtest: two group results plus combined.
    req = GraphBacktestRequest(graph=Graph.model_validate(data), ticker="AAPL",
                               start="2023-01-02", end="2023-12-29", interval="1d",
                               source="yahoo", initial_capital=10_000.0)
    response, _cook = run_graph_backtest_cooked(
        req, frames={("AAPL", "1d"): frame(1), ("MSFT", "1d"): frame(2)}, keep_all=False)
    assert [g.name for g in response.groups] == ["long_leg", "short_leg"]
    assert [(g.symbol, g.direction, g.capital) for g in response.groups] == \
        [("AAPL", "long", 5000.0), ("MSFT", "short", 5000.0)]
    assert response.combined is not None
    assert response.combined.summary["initial_capital"] == 10_000.0

    # The spawn: one add_bots call, one save, two stopped bots.
    r = _spawn(world, env["id"], env["rev"], [leg_body("long_leg", 5000.0),
                                              leg_body("short_leg", 5000.0)])
    assert r.status_code == 201, r.text
    bots = r.json()["bots"]
    assert [(b["group"], b["symbol"], b["direction"], b["running"]) for b in bots] == \
        [("long_leg", "AAPL", "long", False), ("short_leg", "MSFT", "short", False)]
    assert world.adds == [2]
    assert len(world.saves) == 1
    assert world.mgr.tasks == {}
    world.broker.assert_not_called()

    for b, (sym, direction, grp) in zip(bots, [("AAPL", "long", "long_leg"),
                                               ("MSFT", "short", "short_leg")]):
        cfg, state = world.mgr.bots[b["bot_id"]]
        assert state.status == "stopped"
        assert (cfg.kind, cfg.symbol, cfg.direction, cfg.interval) == ("graph", sym, direction, "1d")
        assert (cfg.graph_id, cfg.graph_rev, cfg.graph_group, cfg.graph_direction_mode) == \
            (env["id"], 1, grp, direction)
        assert cfg.allocated_capital == 5000.0
        assert (cfg.broker, cfg.data_source) == ("alpaca", "alpaca-iex")
        assert cfg.strategy_name == f"pair_aapl_msft ▸ {grp}"
        assert cfg.graph is not None and set(cfg.graph.nodes) == set(data["nodes"])
        assert not cfg.is_bidirectional

    # The one save wrote both bots to the temporary bots.json, stopped.
    saved = json.loads((world.tmp / "bots.json").read_text())
    assert [row["state"]["status"] for row in saved["bots"]] == ["stopped", "stopped"]
    assert {row["config"]["graph_group"] for row in saved["bots"]} == {"long_leg", "short_leg"}


def test_spawn_leg_options_name_interval_and_broker(world):
    env = world.store.create("pair", pair_data())
    r = _spawn(world, env["id"], 1, [leg_body("long_leg", 2500.0, broker="ibkr", data_source="ibkr",
                                              interval_override="1h", strategy_name="mine")])
    assert r.status_code == 201, r.text
    [b] = r.json()["bots"]
    cfg, _ = world.mgr.bots[b["bot_id"]]
    assert (cfg.strategy_name, cfg.interval, cfg.broker, cfg.data_source, cfg.allocated_capital) == \
        ("mine", "1h", "ibkr", "ibkr", 2500.0)


def test_stale_rev_is_409_and_adds_nothing(world):
    env = world.store.create("pair", pair_data())
    world.store.update(env["id"], 1, pair_data(a_symbol="NVDA"))  # now rev 2
    r = _spawn(world, env["id"], 1, [leg_body("long_leg")])
    assert r.status_code == 409
    assert r.json() == {"detail": {"code": "rev_conflict", "current_rev": 2}}
    assert world.adds == []
    _assert_nothing_added(world)


def test_two_legs_same_symbol_and_direction_are_refused(world):
    env = world.store.create("twins", pair_data(b_symbol="AAPL", b_dir="long",
                                                b_name="long_leg_2"))
    r = _spawn(world, env["id"], 1, [leg_body("long_leg"), leg_body("long_leg_2")])
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "same_symbol_same_direction"
    assert "long_leg" in detail["message"] and "long_leg_2" in detail["message"]
    assert set(detail["groups"]) == {"long_leg", "long_leg_2"}
    _assert_nothing_added(world)


def test_the_same_group_twice_is_refused(world):
    env = world.store.create("pair", pair_data())
    r = _spawn(world, env["id"], 1, [leg_body("long_leg", 1000.0), leg_body("long_leg", 1000.0)])
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "same_symbol_same_direction"
    _assert_nothing_added(world)


def test_a_leg_colliding_with_an_existing_bot_is_refused(world):
    world.mgr.add_bot(rule_config("MSFT", "short"))
    saves = len(world.saves)
    env = world.store.create("pair", pair_data())
    r = _spawn(world, env["id"], 1, [leg_body("long_leg"), leg_body("short_leg")])
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "same_symbol_same_direction"
    assert "short_leg" in detail["message"] and "MSFT" in detail["message"]
    assert len(world.mgr.bots) == 1 and len(world.saves) == saves


def test_a_regime_switch_leg_needs_its_symbol_to_itself(world):
    """A regime_switch bot trades both sides, so any bot on its symbol
    collides, whatever its direction."""
    world.mgr.add_bot(rule_config("AAPL", "short"))
    env = world.store.create("switch", switch_data("AAPL"))
    r = _spawn(world, env["id"], 1, [leg_body("sw")])
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "same_symbol_same_direction"
    assert len(world.mgr.bots) == 1


def test_unknown_group_is_refused(world):
    env = world.store.create("pair", pair_data())
    r = _spawn(world, env["id"], 1, [leg_body("long_leg"), leg_body("nope")])
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "group_unknown" and detail["groups"] == ["nope"]
    assert "nope" in detail["message"]
    _assert_nothing_added(world)


def test_a_graph_that_does_not_compile_is_graph_invalid_with_diagnostics(world):
    data = pair_data()
    data["nodes"].pop("a_entry")
    data["wires"] = [w for w in data["wires"] if w["to"] != "a_entry"]
    env = world.store.create("broken", data)
    r = _spawn(world, env["id"], 1, [leg_body("short_leg")])
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "graph_invalid"
    assert detail["message"] and detail["diagnostics"]
    assert any(d["severity"] == "error" for d in detail["diagnostics"])
    _assert_nothing_added(world)


def test_over_the_fund_is_refused_whole(world):
    env = world.store.create("pair", pair_data())
    r = _spawn(world, env["id"], 1, [leg_body("long_leg", 6000.0), leg_body("short_leg", 6000.0)])
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "fund"
    _assert_nothing_added(world)


def test_unknown_graph_is_404(world):
    r = _spawn(world, "g_000000000000", 1, [leg_body("main")])
    assert r.status_code == 404


def test_bad_leg_body_is_422(world):
    env = world.store.create("pair", pair_data())
    r = _spawn(world, env["id"], 1, [{**leg_body("long_leg"), "allocated_capital": 0}])
    assert r.status_code == 422
    r = _spawn(world, env["id"], 1, [{**leg_body("long_leg"), "running": True}])
    assert r.status_code == 422  # there is no way to ask for a running bot
    _assert_nothing_added(world)


def test_implicit_main_group_takes_its_ticker_and_the_leg_direction(world):
    env = world.store.create("plain", implicit_data("TSLA"))
    r = _spawn(world, env["id"], 1, [leg_body("main", direction="short")])
    assert r.status_code == 201, r.text
    [b] = r.json()["bots"]
    cfg, _ = world.mgr.bots[b["bot_id"]]
    assert (cfg.symbol, cfg.direction, cfg.graph_direction_mode, cfg.graph_group) == \
        ("TSLA", "short", "short", "main")


def test_implicit_main_group_defaults_to_long(world):
    env = world.store.create("plain", implicit_data("TSLA"))
    r = _spawn(world, env["id"], 1, [leg_body("main")])
    assert r.status_code == 201
    [b] = r.json()["bots"]
    assert world.mgr.bots[b["bot_id"]][0].direction == "long"


def test_a_leg_direction_on_an_explicit_group_is_refused(world):
    env = world.store.create("pair", pair_data())
    r = _spawn(world, env["id"], 1, [leg_body("long_leg", direction="short")])
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "leg_invalid"
    _assert_nothing_added(world)


def test_regime_switch_group_spawns_a_bidirectional_bot(world):
    env = world.store.create("switch", switch_data("AAPL"))
    r = _spawn(world, env["id"], 1, [leg_body("sw")])
    assert r.status_code == 201, r.text
    [b] = r.json()["bots"]
    assert b["direction"] == "regime_switch"
    cfg, _ = world.mgr.bots[b["bot_id"]]
    assert (cfg.direction, cfg.graph_direction_mode) == ("long", "regime_switch")
    assert cfg.is_bidirectional


# ---------------------------------------------------------------------------
# POST /api/bots with graph_id (the AddBotBar graph mode, S36)
# ---------------------------------------------------------------------------


def _add_body(graph_id, rev, group_name, **extra):
    return {"strategy_name": "x", "symbol": "ZZZ", "interval": "1d", "kind": "graph",
            "graph_id": graph_id, "graph_rev": rev, "graph_group": group_name,
            "buy_rules": [], "sell_rules": [], "allocated_capital": 1000.0, "direction": "long",
            **extra}


def test_add_bot_by_graph_id_loads_the_saved_revision(world):
    env = world.store.create("pair", pair_data())
    r = world.client.post("/api/bots", json=_add_body(env["id"], 1, "short_leg"))
    assert r.status_code == 200, r.text
    cfg, state = world.mgr.bots[r.json()["bot_id"]]
    # The group decides symbol and direction; the saved graph is the snapshot.
    assert (cfg.symbol, cfg.direction, cfg.graph_direction_mode) == ("MSFT", "short", "short")
    assert (cfg.graph_id, cfg.graph_rev, cfg.graph_group) == (env["id"], 1, "short_leg")
    assert cfg.graph is not None and "b_entry" in cfg.graph.nodes
    assert state.status == "stopped" and world.mgr.tasks == {}


def test_add_bot_by_graph_id_stale_rev_is_409(world):
    env = world.store.create("pair", pair_data())
    world.store.update(env["id"], 1, pair_data())
    r = world.client.post("/api/bots", json=_add_body(env["id"], 1, "long_leg"))
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "rev_conflict", "current_rev": 2}
    assert world.mgr.bots == {}


def test_add_bot_by_graph_id_unknown_group_is_400(world):
    env = world.store.create("pair", pair_data())
    r = world.client.post("/api/bots", json=_add_body(env["id"], 1, "nope"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "group_unknown"
    assert world.mgr.bots == {}


def test_add_bot_by_graph_id_with_an_inline_graph_too_is_refused(world):
    env = world.store.create("pair", pair_data())
    r = world.client.post("/api/bots", json=_add_body(env["id"], 1, "long_leg",
                                                      graph=pair_data()))
    assert r.status_code == 400
    assert world.mgr.bots == {}


# ---------------------------------------------------------------------------
# POST /api/bots/{id}/graph_update
# ---------------------------------------------------------------------------


def _spawned(world, data=None, group_name="long_leg"):
    env = world.store.create("pair", data or pair_data())
    r = _spawn(world, env["id"], 1, [leg_body(group_name)])
    assert r.status_code == 201, r.text
    bot_id = r.json()["bots"][0]["bot_id"]
    return env, bot_id


def _update(world, bot_id, graph_id, rev):
    return world.client.post(f"/api/bots/{bot_id}/graph_update",
                             json={"graph_id": graph_id, "rev": rev})


class _PositionsOnlyBroker:
    """A broker graph_update may ask for positions (F435 W5 LM-3), and
    nothing else: any order call fails the test."""

    def __init__(self, positions=()):
        self.positions = list(positions)
        self.asked = 0

    def get_positions(self):
        self.asked += 1
        return [dict(p) for p in self.positions]

    def __getattr__(self, name):
        raise AssertionError(f"graph_update must not call broker.{name}")


def _broker_with(world, positions=()):
    broker = _PositionsOnlyBroker(positions)
    world.broker.side_effect = None
    world.broker.return_value = broker
    return broker


def _with_threshold(data: dict, value: float) -> dict:
    data = copy.deepcopy(data)
    data["nodes"]["a_lo"]["params"]["threshold"] = value
    return data


def test_graph_update_sets_graph_and_rev_together(world):
    env, bot_id = _spawned(world)
    cfg_before, _ = world.mgr.bots[bot_id]
    world.store.update(env["id"], 1, _with_threshold(pair_data(), 31))
    saves = len(world.saves)
    broker = _broker_with(world)  # flat: graph_update asks it (LM-3)
    r = _update(world, bot_id, env["id"], 2)
    assert r.status_code == 200, r.text
    assert r.json() == {"bot_id": bot_id, "graph_rev": 2}
    cfg, _ = world.mgr.bots[bot_id]
    assert cfg is cfg_before  # in place: a running bot's runner holds this object
    assert cfg.graph_rev == 2
    assert cfg.graph.nodes["a_lo"].params["threshold"] == 31
    assert len(world.saves) == saves + 1
    assert broker.asked == 1  # positions only, no order


def test_graph_update_refuses_in_position(world):
    env, bot_id = _spawned(world)
    world.store.update(env["id"], 1, _with_threshold(pair_data(), 31))
    cfg, state = world.mgr.bots[bot_id]
    state.entry_price = 101.0
    r = _update(world, bot_id, env["id"], 2)
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "in_position"}
    assert cfg.graph_rev == 1 and cfg.graph.nodes["a_lo"].params["threshold"] == 38


def test_graph_update_refuses_a_symbol_change(world):
    env, bot_id = _spawned(world)
    world.store.update(env["id"], 1, pair_data(a_symbol="NVDA"))
    r = _update(world, bot_id, env["id"], 2)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "symbol_changed"
    cfg, _ = world.mgr.bots[bot_id]
    assert cfg.graph_rev == 1 and cfg.graph.nodes["a_t"].params["symbol"] == "AAPL"


def test_graph_update_refuses_a_direction_change(world):
    env, bot_id = _spawned(world)
    world.store.update(env["id"], 1, pair_data(a_dir="short", b_dir="long"))
    r = _update(world, bot_id, env["id"], 2)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "direction_changed"
    assert world.mgr.bots[bot_id][0].graph_rev == 1


def test_graph_update_refuses_a_missing_group(world):
    env, bot_id = _spawned(world)
    world.store.update(env["id"], 1, pair_data(a_name="renamed_leg"))
    r = _update(world, bot_id, env["id"], 2)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "group_missing"
    assert world.mgr.bots[bot_id][0].graph_rev == 1


def test_graph_update_stale_rev_is_409(world):
    env, bot_id = _spawned(world)
    world.store.update(env["id"], 1, pair_data())
    world.store.update(env["id"], 2, pair_data())
    r = _update(world, bot_id, env["id"], 2)
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "rev_conflict", "current_rev": 3}


def test_graph_update_refuses_another_graph(world):
    env, bot_id = _spawned(world)
    other = world.store.create("other", pair_data())
    r = _update(world, bot_id, other["id"], 1)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "graph_mismatch"


def test_patch_cannot_swap_the_graph_of_a_saved_graph_bot(world):
    """graph and graph_rev move together, so the generic edit refuses a
    graph (or a direction) for a bot made from a saved graph."""
    env, bot_id = _spawned(world)
    r = world.client.patch(f"/api/bots/{bot_id}", json={"graph": _with_threshold(pair_data(), 31)})
    assert r.status_code == 400
    assert "graph_update" in r.json()["detail"]
    r = world.client.patch(f"/api/bots/{bot_id}", json={"direction": "short"})
    assert r.status_code == 400
    cfg, _ = world.mgr.bots[bot_id]
    assert cfg.graph_rev == 1 and cfg.direction == "long"
    # Fields that are not the graph's still edit as before.
    r = world.client.patch(f"/api/bots/{bot_id}", json={"strategy_name": "renamed"})
    assert r.status_code == 200, r.text
    assert world.mgr.bots[bot_id][0].strategy_name == "renamed"


def test_every_snapshot_path_goes_through_the_bake_in_hook(world, monkeypatch):
    """The library bake-in hook (a no-op until W6) runs for spawn, for an
    add by graph_id and for graph_update."""
    seen: list = []
    real = graphs_route.bake_in_library

    def spy(graph):
        seen.append(graph)
        return real(graph)

    monkeypatch.setattr(graphs_route, "bake_in_library", spy)
    env = world.store.create("pair", pair_data())
    assert _spawn(world, env["id"], 1, [leg_body("long_leg")]).status_code == 201
    assert len(seen) == 1
    r = world.client.post("/api/bots", json=_add_body(env["id"], 1, "short_leg"))
    assert r.status_code == 200, r.text
    assert len(seen) == 2
    bot_id = r.json()["bot_id"]
    world.store.update(env["id"], 1, _with_threshold(pair_data(), 31))
    _broker_with(world)  # flat: graph_update asks it (LM-3)
    assert _update(world, bot_id, env["id"], 2).status_code == 200
    assert len(seen) == 3

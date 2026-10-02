"""Spawn, add, start and graph_update guards (F435 W5 review: LM-1, LM-3,
LM-5, LM-7, LM-10, DI-04, DI-07).

- LM-1: spawn, add and start fetch each reference frame a bot's group reads
  once, on the bot's data source, and refuse with code
  reference_unavailable when one does not load.  A start of a bot that
  holds a position is never refused for it (its ticks run the price exits
  while a reference is down).
- LM-3: graph_update asks the broker and refuses 409 in_position when it
  holds the bot's position (a Stop that kept it), 503 broker_unavailable
  when the broker cannot be asked, and 409 while an entry is in flight.
- LM-5: spawn takes trading_hours, skip_after_stop and dynamic_sizing and
  gives them to every leg.
- LM-7: graph_update refuses a rev whose group moved to another interval.
- LM-10: the start route compiles in the thread pool, not on the loop.
- DI-04: GET /api/graphs lists each graph's real Output Group names.
- DI-07: POST /api/bots refuses a rule bot with graph_* fields.

Money safety (plan 8.4): bots.json and the graph store are temporary; the
broker is a mock (a positions-only fake where graph_update asks it); the
market data fetch is replaced; BotRunner.run is replaced where a start
happens, so no bot runs.
"""
from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

import bot_manager as bot_manager_mod
from bot_runner import BotRunner
from nodebuilder import trading  # noqa: F401  (registers every node type)
from tests.test_graph_spawn import (_broker_with, _spawn, _spawned, _update, frame, graph_data,
                                    group, leg, leg_body, make_world, node, pair_data)


@pytest.fixture
def world(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch)


def ref_pair_data(a_itv="1d") -> dict:
    """AAPL long and MSFT short, each group's RSI reading SPY (a reference
    Ticker)."""
    groups = [group("long_leg", "long"), group("short_leg", "short")]
    nodes, wires = [], []
    for p, sym, name, lo, hi, itv in (("a_", "AAPL", "long_leg", 38, 62, a_itv),
                                      ("b_", "MSFT", "short_leg", 60, 40, "1d")):
        n, w = leg(p, sym, name, lo=lo, hi=hi, interval=itv)
        n.append(node(f"{p}spy", "ticker", {"symbol": "SPY", "interval": "1d"}, name, name="spy"))
        w = [x if x[0] != f"{p}w1" else (f"{p}w1", f"{p}spy", f"{p}rsi") for x in w]
        nodes += n
        wires += w
    return graph_data([*groups, *nodes], wires)


class FakeFetch:
    """shared._fetch as bot_manager calls it: SPY fails or loads."""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls: list[tuple] = []

    def __call__(self, symbol, start, end, interval, source="yahoo", *a, **k):
        self.calls.append((symbol, interval, source))
        if self.fail:
            raise RuntimeError("symbol not served")
        return frame(4)


@pytest.fixture
def fetch(monkeypatch):
    f = FakeFetch()
    monkeypatch.setattr(bot_manager_mod, "_fetch", f)
    return f


# ---------------------------------------------------------------------------
# LM-1: reference frames are checked at spawn, add and start
# ---------------------------------------------------------------------------


def test_spawn_refuses_a_reference_the_data_source_does_not_serve(world, fetch):
    fetch.fail = True
    env = world.store.create("refs", ref_pair_data())
    r = _spawn(world, env["id"], 1, [leg_body("long_leg")])
    assert r.status_code == 400, r.text
    d = r.json()["detail"]
    assert d["code"] == "reference_unavailable"
    assert (d["symbol"], d["interval"], d["data_source"], d["group"], d["groups"]) == \
        ("SPY", "1d", "alpaca-iex", "long_leg", ["long_leg"])
    assert "symbol not served" in d["message"]
    assert world.mgr.bots == {} and world.saves == []


def test_spawn_fetches_each_reference_once(world, fetch):
    env = world.store.create("refs", ref_pair_data())
    r = _spawn(world, env["id"], 1, [leg_body("long_leg"), leg_body("short_leg")])
    assert r.status_code == 201, r.text
    assert fetch.calls == [("SPY", "1d", "alpaca-iex")]
    assert len(world.mgr.bots) == 2


def test_spawn_without_references_fetches_nothing(world, fetch):
    env = world.store.create("pair", pair_data())
    assert _spawn(world, env["id"], 1, [leg_body("long_leg")]).status_code == 201
    assert fetch.calls == []


def _inline_add_body(data) -> dict:
    return {"strategy_name": "inline", "symbol": "AAPL", "interval": "1d", "buy_rules": [],
            "sell_rules": [], "allocated_capital": 1000.0, "kind": "graph", "graph": data,
            "graph_group": "long_leg", "graph_direction_mode": "long",
            "data_source": "alpaca-iex"}


def test_add_refuses_a_reference_that_does_not_load(world, fetch):
    fetch.fail = True
    r = world.client.post("/api/bots", json=_inline_add_body(ref_pair_data()))
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "reference_unavailable"
    assert world.mgr.bots == {}


def test_add_by_graph_id_refuses_a_reference_that_does_not_load(world, fetch):
    fetch.fail = True
    env = world.store.create("refs", ref_pair_data())
    body = {"strategy_name": "by id", "symbol": "AAPL", "interval": "1d", "buy_rules": [],
            "sell_rules": [], "allocated_capital": 1000.0, "kind": "graph",
            "graph_id": env["id"], "graph_rev": 1, "graph_group": "short_leg"}
    r = world.client.post("/api/bots", json=body)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["group"] == "short_leg"


async def _idle_run(self):
    return None


def _spawn_ref_bot(world, fetch) -> str:
    env = world.store.create("refs", ref_pair_data())
    r = _spawn(world, env["id"], 1, [leg_body("long_leg")])
    assert r.status_code == 201, r.text
    return r.json()["bots"][0]["bot_id"]


def test_start_refuses_a_flat_bot_whose_reference_does_not_load(world, fetch):
    bot_id = _spawn_ref_bot(world, fetch)
    fetch.fail = True
    with patch.object(BotRunner, "run", _idle_run):
        r = world.client.post(f"/api/bots/{bot_id}/start")
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "reference_unavailable"
    assert bot_id not in world.mgr.tasks


def test_start_never_refuses_a_bot_that_holds_a_position(world, fetch):
    """Its ticks manage the stops while the reference is down (LM-1);
    refusing the start would leave the position with no management."""
    bot_id = _spawn_ref_bot(world, fetch)
    fetch.fail = True
    world.mgr.bots[bot_id][1].entry_price = 101.0
    with patch.object(BotRunner, "run", _idle_run):
        r = world.client.post(f"/api/bots/{bot_id}/start")
    assert r.status_code == 200, r.text
    assert bot_id in world.mgr.tasks


def test_start_all_reports_a_reference_that_does_not_load(world, fetch):
    bot_id = _spawn_ref_bot(world, fetch)
    fetch.fail = True
    with patch.object(BotRunner, "run", _idle_run):
        r = world.client.post("/api/bots/start-all")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["started"] == [] and [f["bot_id"] for f in out["failed"]] == [bot_id]
    assert "SPY" in out["failed"][0]["error"]


# ---------------------------------------------------------------------------
# LM-10: the start route compiles in the thread pool
# ---------------------------------------------------------------------------


def test_start_route_compiles_off_the_event_loop(world):
    env = world.store.create("pair", pair_data())
    r = _spawn(world, env["id"], 1, [leg_body("long_leg")])
    bot_id = r.json()["bots"][0]["bot_id"]
    compiles: list[int] = []
    starts: list[int] = []
    real_compile = bot_manager_mod.compile_bot_graph
    real_start = world.mgr.start_bot

    def compile_spy(*a, **k):
        compiles.append(threading.get_ident())
        return real_compile(*a, **k)

    def start_spy(bid):
        starts.append(threading.get_ident())  # start_bot runs on the event loop
        return real_start(bid)

    world.mgr.start_bot = start_spy
    with patch("bot_manager.compile_bot_graph", side_effect=compile_spy), \
         patch.object(BotRunner, "run", _idle_run):
        r = world.client.post(f"/api/bots/{bot_id}/start")
    assert r.status_code == 200, r.text
    assert len(compiles) == 1 and len(starts) == 1
    assert compiles[0] != starts[0]


# ---------------------------------------------------------------------------
# LM-3: graph_update asks the broker
# ---------------------------------------------------------------------------


def _new_rev(world, env, value=31):
    data = pair_data()
    data["nodes"]["a_lo"]["params"]["threshold"] = value
    world.store.update(env["id"], 1, data)


def test_graph_update_refuses_when_the_broker_holds_the_bots_position(world):
    """After a Stop that kept the position, entry_price is None; the broker
    still holds the long, and Start would resume it under the new graph."""
    env, bot_id = _spawned(world)
    _new_rev(world, env)
    broker = _broker_with(world, [{"symbol": "AAPL", "side": "long", "qty": 3, "avg_entry": 10.0}])
    r = _update(world, bot_id, env["id"], 2)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "in_position"
    assert broker.asked == 1
    cfg, _ = world.mgr.bots[bot_id]
    assert cfg.graph_rev == 1 and cfg.graph.nodes["a_lo"].params["threshold"] == 38


def test_graph_update_ignores_another_bots_side(world):
    env, bot_id = _spawned(world)  # a long bot on AAPL
    _new_rev(world, env)
    _broker_with(world, [{"symbol": "AAPL", "side": "short", "qty": 3, "avg_entry": 10.0}])
    r = _update(world, bot_id, env["id"], 2)
    assert r.status_code == 200, r.text


def test_graph_update_refuses_when_the_broker_cannot_be_asked(world):
    env, bot_id = _spawned(world)
    _new_rev(world, env)
    # world.broker still raises on every call.
    r = _update(world, bot_id, env["id"], 2)
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "broker_unavailable"
    assert world.mgr.bots[bot_id][0].graph_rev == 1


def test_graph_update_refuses_while_an_entry_is_in_flight(world):
    env, bot_id = _spawned(world)
    _new_rev(world, env)
    world.mgr.bots[bot_id][1].entry_in_flight = True
    r = _update(world, bot_id, env["id"], 2)
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "in_position"}


# ---------------------------------------------------------------------------
# LM-7: graph_update refuses a group that moved to another interval
# ---------------------------------------------------------------------------


def _itv_data(a_itv: str) -> dict:
    a_nodes, a_wires = leg("a_", "AAPL", "long_leg", interval=a_itv)
    b_nodes, b_wires = leg("b_", "MSFT", "short_leg", lo=60, hi=40)
    return graph_data([group("long_leg", "long"), group("short_leg", "short"),
                       *a_nodes, *b_nodes], a_wires + b_wires)


def test_graph_update_refuses_an_interval_change(world):
    env = world.store.create("itv", _itv_data("1d"))
    bot_id = _spawn(world, env["id"], 1, [leg_body("long_leg")]).json()["bots"][0]["bot_id"]
    world.store.update(env["id"], 1, _itv_data("1h"))
    _broker_with(world)
    r = _update(world, bot_id, env["id"], 2)
    assert r.status_code == 400, r.text
    d = r.json()["detail"]
    assert d["code"] == "interval_changed" and "1h" in d["message"]
    assert world.mgr.bots[bot_id][0].graph_rev == 1


def test_graph_update_keeps_a_spawn_interval_override(world):
    """The bot runs 1h by its own choice; the group stays 1d in the new rev."""
    env = world.store.create("itv", _itv_data("1d"))
    r = _spawn(world, env["id"], 1, [leg_body("long_leg", interval_override="1h")])
    bot_id = r.json()["bots"][0]["bot_id"]
    data = _itv_data("1d")
    data["nodes"]["a_lo"]["params"]["threshold"] = 30
    world.store.update(env["id"], 1, data)
    _broker_with(world)
    r = _update(world, bot_id, env["id"], 2)
    assert r.status_code == 200, r.text
    assert world.mgr.bots[bot_id][0].interval == "1h"


# ---------------------------------------------------------------------------
# LM-5: the sidebar gates reach every spawned bot
# ---------------------------------------------------------------------------


def test_spawn_gives_every_leg_the_sidebar_gates(world):
    env = world.store.create("pair", pair_data())
    gates = {
        "trading_hours": {"enabled": True, "start_time": "10:00", "end_time": "15:30",
                          "skip_ranges": ["12:00-13:00"]},
        "skip_after_stop": {"enabled": True, "count": 2, "trigger": "sl"},
        "dynamic_sizing": {"enabled": True, "consec_sls": 3, "reduced_pct": 50.0, "trigger": "sl"},
    }
    r = world.client.post(f"/api/graphs/{env['id']}/spawn",
                          json={"rev": 1, "legs": [leg_body("long_leg"), leg_body("short_leg")],
                                **gates})
    assert r.status_code == 201, r.text
    configs = [world.mgr.bots[b["bot_id"]][0] for b in r.json()["bots"]]
    for cfg in configs:
        assert cfg.trading_hours.model_dump() == gates["trading_hours"]
        assert cfg.skip_after_stop.model_dump() == gates["skip_after_stop"]
        assert cfg.dynamic_sizing.model_dump() == gates["dynamic_sizing"]
    assert configs[0].trading_hours is not configs[1].trading_hours  # each leg its own


def test_spawn_without_gates_leaves_them_off(world):
    env = world.store.create("pair", pair_data())
    r = _spawn(world, env["id"], 1, [leg_body("long_leg")])
    cfg = world.mgr.bots[r.json()["bots"][0]["bot_id"]][0]
    assert (cfg.trading_hours, cfg.skip_after_stop, cfg.dynamic_sizing) == (None, None, None)


def test_spawn_refuses_a_badly_shaped_gate(world):
    env = world.store.create("pair", pair_data())
    r = world.client.post(f"/api/graphs/{env['id']}/spawn",
                          json={"rev": 1, "legs": [leg_body("long_leg")],
                                "trading_hours": {"enabled": "maybe"}})
    assert r.status_code == 422
    assert world.mgr.bots == {}


# ---------------------------------------------------------------------------
# DI-07: a rule bot carries no graph link
# ---------------------------------------------------------------------------


def test_a_rule_bot_with_graph_fields_is_refused(world):
    body = {"strategy_name": "rule", "symbol": "AAPL", "interval": "1d", "buy_rules": [],
            "sell_rules": [], "allocated_capital": 1000.0, "graph_id": "g_000000000001",
            "graph_rev": 1}
    r = world.client.post("/api/bots", json=body)
    assert r.status_code == 400, r.text
    d = r.json()["detail"]
    assert d["code"] == "leg_invalid" and "graph_id" in d["message"]
    assert world.mgr.bots == {}


# ---------------------------------------------------------------------------
# Weight 0: a group that is never simulated is never spawned or added
# ---------------------------------------------------------------------------


def _weight_zero_pair() -> dict:
    data = pair_data()
    data["nodes"]["short_leg"]["params"]["capital_weight"] = 0
    return data


def test_spawn_refuses_a_weight_zero_leg(world):
    env = world.store.create("w0", _weight_zero_pair())
    r = _spawn(world, env["id"], 1, [leg_body("long_leg"), leg_body("short_leg")])
    assert r.status_code == 400, r.text
    d = r.json()["detail"]
    assert d["code"] == "leg_invalid" and d["groups"] == ["short_leg"]
    assert "capital weight 0" in d["message"]
    assert world.mgr.bots == {} and world.saves == []
    # The other leg alone still spawns.
    assert _spawn(world, env["id"], 1, [leg_body("long_leg")]).status_code == 201


def test_add_by_graph_id_refuses_a_weight_zero_group(world):
    env = world.store.create("w0", _weight_zero_pair())
    body = {"strategy_name": "by id", "symbol": "MSFT", "interval": "1d", "buy_rules": [],
            "sell_rules": [], "allocated_capital": 1000.0, "kind": "graph",
            "graph_id": env["id"], "graph_rev": 1, "graph_group": "short_leg"}
    r = world.client.post("/api/bots", json=body)
    assert r.status_code == 400, r.text
    d = r.json()["detail"]
    assert d["code"] == "leg_invalid" and d["groups"] == ["short_leg"]
    assert world.mgr.bots == {}


# ---------------------------------------------------------------------------
# PATCH with a graph on a legacy graph bot (no graph_id): the graph_update
# in-position guard (entry in flight, the broker's position)
# ---------------------------------------------------------------------------


def _legacy_bot(world) -> str:
    r = world.client.post("/api/bots", json=_inline_add_body(pair_data()))
    assert r.status_code == 200, r.text
    bot_id = r.json()["bot_id"]
    assert world.mgr.bots[bot_id][0].graph_id is None
    return bot_id


def _patch_graph(world, bot_id, value=31):
    data = pair_data()
    data["nodes"]["a_lo"]["params"]["threshold"] = value
    return world.client.patch(f"/api/bots/{bot_id}", json={"graph": data})


def _threshold(world, bot_id):
    return world.mgr.bots[bot_id][0].graph.nodes["a_lo"].params["threshold"]


def test_patch_graph_refuses_while_an_entry_is_in_flight(world):
    bot_id = _legacy_bot(world)
    broker = _broker_with(world, [])
    world.mgr.bots[bot_id][1].entry_in_flight = True
    r = _patch_graph(world, bot_id)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "in_position"
    assert broker.asked == 0
    assert _threshold(world, bot_id) == 38


def test_patch_graph_refuses_when_the_broker_holds_the_bots_position(world):
    bot_id = _legacy_bot(world)
    broker = _broker_with(world, [{"symbol": "AAPL", "side": "long", "qty": 3, "avg_entry": 10.0}])
    r = _patch_graph(world, bot_id)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "in_position"
    assert broker.asked == 1
    assert _threshold(world, bot_id) == 38


def test_patch_graph_refuses_when_the_broker_cannot_be_asked(world):
    bot_id = _legacy_bot(world)
    # world.broker raises on every call.
    r = _patch_graph(world, bot_id)
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "broker_unavailable"
    assert _threshold(world, bot_id) == 38


def test_patch_graph_applies_when_flat_at_the_broker(world):
    bot_id = _legacy_bot(world)
    _broker_with(world, [{"symbol": "AAPL", "side": "short", "qty": 3, "avg_entry": 10.0}])
    r = _patch_graph(world, bot_id)
    assert r.status_code == 200, r.text
    assert _threshold(world, bot_id) == 31


def test_patch_with_a_bad_graph_is_400_before_the_broker_is_asked(world):
    bot_id = _legacy_bot(world)
    data = pair_data()
    data["wires"] = [w for w in data["wires"] if w["to"] != "a_entry"]
    # world.broker raises on every call: the bad graph must be refused first.
    r = world.client.patch(f"/api/bots/{bot_id}", json={"graph": data})
    assert r.status_code == 400, r.text
    assert _threshold(world, bot_id) == 38


def test_patch_with_the_same_graph_never_asks_the_broker(world):
    bot_id = _legacy_bot(world)
    # world.broker raises on every call: an unchanged graph must not reach it.
    r = world.client.patch(f"/api/bots/{bot_id}", json={"graph": pair_data()})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# DI-04: the graph list names the real groups
# ---------------------------------------------------------------------------


def test_graph_list_names_the_output_groups(world):
    world.store.create("pair", pair_data())
    world.store.create("plain", graph_data(*leg("", "AAPL", None)))
    r = world.client.get("/api/graphs")
    assert r.status_code == 200
    groups = {g["name"]: g["groups"] for g in r.json()["graphs"]}
    assert groups == {"pair": ["long_leg", "short_leg"], "plain": ["main"]}


"""The bot summary's graph fields (F435 W5 5.D, orchestrator decision
2026-09-30).

GET /api/bots gives each bot graph_id, graph_rev, graph_group,
graph_direction_mode, and, from the graph store's in-memory head index,
graph_name and graph_latest_rev.  BotCard reads them and never fetches a
graph.

- The fields equal the stored envelope's rev and name.
- After a PUT the next summary shows the new rev (and a new name).
- After a DELETE graph_name and graph_latest_rev are both None.
- Building the summary for 10 graph bots reads no graph file.

Money safety (plan 8.4): temporary STRATEGYLAB_DATA_DIR and bots.json, no
bot started, the broker is a mock that fails the test if called.
"""
from __future__ import annotations

import pathlib

import pytest

from nodebuilder import storage as storage_mod
from nodebuilder.storage import GraphStore, graph_head
from tests.test_graph_spawn import (implicit_data, leg_body, make_world, pair_data,
                                    rule_config)

GRAPH_KEYS = ("graph_id", "graph_rev", "graph_group", "graph_direction_mode",
              "graph_name", "graph_latest_rev")


@pytest.fixture
def world(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch, fund=100_000.0)


def _spawn_pair(world, name="pair_aapl_msft", symbols=("AAPL", "MSFT")):
    env = world.store.create(name, pair_data(a_symbol=symbols[0], b_symbol=symbols[1]))
    r = world.client.post(f"/api/graphs/{env['id']}/spawn",
                          json={"rev": env["rev"], "legs": [leg_body("long_leg"),
                                                            leg_body("short_leg")]})
    assert r.status_code == 201, r.text
    return env, [b["bot_id"] for b in r.json()["bots"]]


def _summaries(world) -> dict:
    r = world.client.get("/api/bots")
    assert r.status_code == 200, r.text
    return {b["bot_id"]: b for b in r.json()["bots"]}


def test_summary_carries_the_graph_fields_from_the_store(world):
    env, (long_id, short_id) = _spawn_pair(world)
    bots = _summaries(world)
    assert {k: bots[long_id][k] for k in GRAPH_KEYS} == {
        "graph_id": env["id"], "graph_rev": 1, "graph_group": "long_leg",
        "graph_direction_mode": "long", "graph_name": "pair_aapl_msft", "graph_latest_rev": 1,
    }
    assert bots[short_id]["graph_group"] == "short_leg"
    assert bots[short_id]["graph_direction_mode"] == "short"
    assert graph_head(env["id"]) == (1, "pair_aapl_msft")


def test_a_rule_bot_has_no_graph_fields(world):
    bot_id = world.mgr.add_bot(rule_config())
    bot = _summaries(world)[bot_id]
    assert all(bot[k] is None for k in GRAPH_KEYS)


def test_after_a_put_the_summary_shows_the_new_rev_and_name(world):
    env, (long_id, _short_id) = _spawn_pair(world)
    r = world.client.put(f"/api/graphs/{env['id']}",
                         json={"rev": 1, "graph": pair_data(), "name": "pair_v2"})
    assert r.status_code == 200, r.text
    bot = _summaries(world)[long_id]
    assert (bot["graph_latest_rev"], bot["graph_name"], bot["graph_rev"]) == (2, "pair_v2", 1)


def test_after_a_delete_name_and_latest_rev_are_none(world):
    env, (long_id, _short_id) = _spawn_pair(world)
    r = world.client.delete(f"/api/graphs/{env['id']}", params={"rev": 1})
    assert r.status_code == 204
    bot = _summaries(world)[long_id]
    assert bot["graph_id"] == env["id"] and bot["graph_rev"] == 1
    assert bot["graph_name"] is None and bot["graph_latest_rev"] is None


def test_the_head_index_is_filled_from_disk_at_start(world, tmp_path):
    env = world.store.create("seeded", implicit_data())
    world.store.update(env["id"], 1, implicit_data(), name="seeded_v2")
    fresh = GraphStore(tmp_path)  # a new process: the index comes from the files
    assert fresh.head(env["id"]) == (2, "seeded_v2")
    assert fresh.head("g_000000000000") is None


def test_ten_bot_summaries_read_no_graph_file(world, monkeypatch):
    for n in range(5):
        _spawn_pair(world, name=f"pair_{n}", symbols=(f"LA{n}", f"SB{n}"))
    assert len(world.mgr.bots) == 10
    graphs_dir = (world.tmp / "graphs").resolve()
    reads: list[str] = []

    real_read_text = pathlib.Path.read_text
    real_open = pathlib.Path.open

    def spy_read_text(self, *a, **k):
        if graphs_dir in pathlib.Path(self).resolve().parents:
            reads.append(str(self))
        return real_read_text(self, *a, **k)

    def spy_open(self, *a, **k):
        if graphs_dir in pathlib.Path(self).resolve().parents:
            reads.append(str(self))
        return real_open(self, *a, **k)

    def no_store_read(*_a, **_k):
        raise AssertionError("the bot summary read a graph file")

    monkeypatch.setattr(pathlib.Path, "read_text", spy_read_text)
    monkeypatch.setattr(pathlib.Path, "open", spy_open)
    monkeypatch.setattr(storage_mod.GraphStore, "_read", no_store_read)
    monkeypatch.setattr(storage_mod.GraphStore, "_all", no_store_read)

    bots = _summaries(world)
    assert len(bots) == 10
    assert all(b["graph_latest_rev"] == 1 and b["graph_name"].startswith("pair_")
               for b in bots.values())
    assert reads == []

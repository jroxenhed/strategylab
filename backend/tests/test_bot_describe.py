"""GET /api/bots/{id}/describe: a bot's strategy as plain English text (F454).

- A rule bot: its rules drawn as a graph, then described.
- A graph bot: its own graph on the bot's symbol and interval, only its own
  group, and its own stop and size where the graph sets none.
- Trading hours show for both kinds; an unknown bot is a 404.
- A describe never saves bots.json and never starts a bot.

Money safety (plan 8.4): temporary STRATEGYLAB_DATA_DIR and bots.json, no
bot started, the broker is a mock that fails the test if called.
"""
from __future__ import annotations

import copy

import pytest

from bot_manager import BotConfig
from models import TradingHoursConfig
from nodebuilder.models import Graph
from tests.nodebuilder.test_describe import G1, G1_LINES, rule_bot
from tests.test_graph_spawn import leg_body, make_world, pair_data


@pytest.fixture
def world(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch, fund=100_000.0)


def graph_bot(graph: dict, symbol="SPY", interval="5m", **extra) -> BotConfig:
    fields = dict(strategy_name="g1", symbol=symbol, interval=interval, buy_rules=[],
                  sell_rules=[], long_buy_rules=None, long_sell_rules=None,
                  short_buy_rules=None, short_sell_rules=None, allocated_capital=1000.0,
                  kind="graph", graph=Graph.model_validate(copy.deepcopy(graph)),
                  graph_group="main", data_source="alpaca-iex", broker="alpaca")
    fields.update(extra)
    return BotConfig(**fields)


def describe(world, bot_id):
    saves = len(world.saves)
    r = world.client.get(f"/api/bots/{bot_id}/describe")
    assert len(world.saves) == saves, "a describe must not save bots.json"
    assert world.mgr.tasks == {}, "a describe must not start a bot"
    return r


def test_rule_bot_text(world):
    bot_id = world.mgr.add_bot(rule_bot())
    r = describe(world, bot_id)
    assert r.status_code == 200, r.text
    assert r.json()["lines"] == [
        "Buy AAPL (1d) when RSI(14) is below 30.",
        "Sell when RSI(14) is above 70.",
        "Stop: 2 % below the entry price.",
        "Size: 100 % of the bot's capital.",
    ]


def test_graph_bot_text_is_its_graph_plus_its_own_size(world):
    bot_id = world.mgr.add_bot(graph_bot(G1))
    r = describe(world, bot_id)
    assert r.status_code == 200, r.text
    assert r.json()["lines"] == G1_LINES + ["Size: 100 % of the bot's capital."]
    assert r.json()["warnings"] == []


def test_graph_bot_trades_its_own_symbol_and_interval(world):
    bot_id = world.mgr.add_bot(graph_bot(G1, symbol="QQQ", interval="15m"))
    assert describe(world, bot_id).json()["lines"][0] == "Buy QQQ (15m) when RSI(14) is below 40."


def test_graph_bot_shows_its_own_stop_where_the_graph_has_none(world):
    g = copy.deepcopy(G1)
    del g["nodes"]["stop"]
    th = TradingHoursConfig(enabled=True, start_time="09:45", end_time="15:45",
                            skip_ranges=["12:00-13:00"])
    bot_id = world.mgr.add_bot(graph_bot(g, stop_loss_pct=1.5, position_size=0.5,
                                         trading_hours=th))
    assert describe(world, bot_id).json()["lines"] == [
        "Buy SPY (5m) when RSI(14) is below 40.",
        "Sell when RSI(14) is above 60.",
        "Stop: 1.5 % below the entry price (bot setting).",
        "Size: 50 % of the bot's capital.",
        "Only opens trades from 09:45 to 15:45 ET, except 12:00-13:00.",
    ]


def test_graph_stop_wins_over_the_bot_setting(world):
    bot_id = world.mgr.add_bot(graph_bot(G1, stop_loss_pct=3.0))
    lines = describe(world, bot_id).json()["lines"]
    assert "Stop: 0.5 % below the entry price." in lines
    assert not any("3 %" in line for line in lines)


def test_spawned_graph_bot_shows_only_its_own_group(world):
    env = world.store.create("pair_aapl_msft", pair_data())
    r = world.client.post(f"/api/graphs/{env['id']}/spawn",
                          json={"rev": env["rev"], "legs": [leg_body("long_leg"),
                                                            leg_body("short_leg")]})
    assert r.status_code == 201, r.text
    long_id, short_id = (b["bot_id"] for b in r.json()["bots"])
    body = describe(world, short_id).json()
    assert [g["name"] for g in body["groups"]] == ["short_leg"]
    assert body["lines"][:2] == ["Sell short MSFT (1d) when RSI(14) is below 60.",
                                 "Buy back when RSI(14) is above 40."]
    assert describe(world, long_id).json()["lines"][0] == "Buy AAPL (1d) when RSI(14) is below 38."


def test_unknown_bot_is_a_404(world):
    assert describe(world, "no-such-bot").status_code == 404


def test_in3_get_describe_declares_the_shared_response_model(world):
    schema = world.client.app.openapi()
    ok = schema["paths"]["/api/bots/{bot_id}/describe"]["get"]["responses"]["200"]
    assert ok["content"]["application/json"]["schema"]["$ref"].endswith("/DescribeResponse")


def test_in4_get_describe_500_detail_is_plain_english(world, monkeypatch):
    import nodebuilder.describe as describe_mod

    def boom(config):
        raise RuntimeError("internal")

    monkeypatch.setattr(describe_mod, "describe_rule_bot", boom)
    bot_id = world.mgr.add_bot(rule_bot())
    r = describe(world, bot_id)
    assert r.status_code == 500
    assert r.json()["detail"] == "Could not describe this strategy."


def test_db08_graph_bot_with_a_missing_group_says_it_cannot_trade(world):
    bot_id = world.mgr.add_bot(graph_bot(pair_data(), symbol="AAPL", interval="1d",
                                         graph_group="long_leg"))
    # add_bot refuses a missing group; a stored bot gets here when its group
    # is gone later.  Put that state in place directly (no save).
    config, state = world.mgr.bots[bot_id]
    world.mgr.bots[bot_id] = (config.model_copy(update={"graph_group": "gone"}), state)
    r = describe(world, bot_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lines"] == [] and body["groups"] == []
    assert body["warnings"] == ["This bot trades group 'gone', which is not in its graph "
                                "(groups: 'long_leg', 'short_leg'), so it cannot trade."]

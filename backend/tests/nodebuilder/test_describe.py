"""The strategy as plain English text (F454, nodebuilder/describe.py).

- Exact text: the G1 paper-gate graph, a short graph, a two-group graph, a
  regime_switch group and a rule bot (RSI below 30 buy, above 70 sell,
  stop 2 %).
- Every catalog node type gives a phrase without an exception.
- Phrase rules: expressions show their text, a Wrangle shows "custom
  Python" plus one warning, a reference Ticker shows its symbol, brackets
  only where needed, a bypassed (muted) term is left out, an asset
  instance shows its asset name.
- The describe never runs code and never fetches data, and is fast.
- POST /api/nodebuilder/describe: 200, 400 for a graph that does not
  parse, 422 without a graph, and the Ticker owns its symbol.
"""
from __future__ import annotations

import copy
import statistics
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import nodebuilder.code as code_pkg
import routes.nodebuilder as nb_route
import shared
from bot_manager import BotConfig
from models import RegimeConfig, TradingHoursConfig
from nodebuilder import describe as describe_mod
from nodebuilder import nodes as catalog
from nodebuilder.describe import describe_graph, describe_rule_bot
from nodebuilder.models import Graph
from signal_engine import Rule
from tests.nodebuilder.test_asset_instances import Library, regime_filter, with_instance
from tests.test_graph_spawn import pair_data, switch_data


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------


def node(nid, typ, params=None, parent=None, **extra):
    return {"id": nid, "type": typ, "name": nid, "parent": parent, "params": params or {},
            **extra}


def data(nodes, wires) -> dict:
    """wires: (from, to) or (from, to, port)."""
    out = []
    for k, w in enumerate(wires):
        port = w[2] if len(w) > 2 else "in0"
        out.append({"id": f"w{k}", "from": w[0], "to": w[1], "to_port": port})
    return {"_version": 3, "nodes": {n["id"]: n for n in nodes}, "wires": out}


def graph(nodes, wires) -> Graph:
    return Graph.model_validate(data(nodes, wires))


# The production graph of the G1 paper bot (.run/F453/g1-prod.json), as
# the API stored it: empty attr params, so every read comes from the wire.
G1 = {
    "_version": 3, "stream_schema": 1, "readOnly": False, "meta": {},
    "nodes": {
        "tick": node("tick", "ticker", {"symbol": "SPY", "interval": "5m"}),
        "rsi": node("rsi", "rsi", {"period": 14}),
        "lo": node("lo", "below", {"threshold": 40, "out": "@lo"}),
        "hi": node("hi", "above", {"threshold": 60, "out": "@hi"}),
        "entry": node("entry", "entry", {}),
        "exit": node("exit", "exit", {}),
        "stop": node("stop", "stop", {"constant": 0.5}),
    },
    "wires": [
        {"id": "w1", "from": "tick", "to": "rsi", "from_port": "out", "to_port": "in0", "attr": None},
        {"id": "w2", "from": "rsi", "to": "lo", "from_port": "out", "to_port": "in0", "attr": None},
        {"id": "w3", "from": "rsi", "to": "hi", "from_port": "out", "to_port": "in0", "attr": None},
        {"id": "w4", "from": "lo", "to": "entry", "from_port": "out", "to_port": "in0", "attr": None},
        {"id": "w5", "from": "hi", "to": "exit", "from_port": "out", "to_port": "in0", "attr": None},
    ],
    "annotations": {"boxes": [], "notes": []},
}

G1_LINES = [
    "Buy SPY (5m) when RSI(14) is below 40.",
    "Sell when RSI(14) is above 60.",
    "Stop: 0.5 % below the entry price.",
]


def rsi_entry(period=14, symbol="AAPL", interval="1d", threshold=30, **ticker_extra):
    """Ticker -> RSI -> below *threshold* -> Entry."""
    return graph(
        [node("t", "ticker", {"symbol": symbol, "interval": interval, **ticker_extra}),
         node("rsi", "rsi", {"period": period}),
         node("lo", "below", {"threshold": threshold, "out": "@lo"}),
         node("entry", "entry", {})],
        [("t", "rsi"), ("rsi", "lo"), ("lo", "entry")],
    )


def rule_bot(**extra) -> BotConfig:
    fields = dict(strategy_name="rsi bot", symbol="AAPL", interval="1d",
                  buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                  sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
                  allocated_capital=1000.0, stop_loss_pct=2.0)
    fields.update(extra)
    return BotConfig(**fields)


# ---------------------------------------------------------------------------
# Exact text
# ---------------------------------------------------------------------------


def test_g1_graph_gives_the_three_sentences():
    result = describe_graph(Graph.model_validate(copy.deepcopy(G1)))
    assert result["lines"] == G1_LINES
    assert result["warnings"] == []
    assert result["groups"] == [{
        "name": "main", "symbol": "SPY", "interval": "5m", "direction": None,
        "entry": "RSI(14) is below 40", "exit": "RSI(14) is above 60",
        "stops": ["Stop: 0.5 % below the entry price"], "sizing": None, "other": [],
    }]


def test_short_graph_with_trailing_stop_time_stop_and_size():
    g = graph(
        [node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
         node("sma", "sma", {"period": 50}),
         node("up", "crosses_above", {"a": "@close", "b": "@sma", "out": "@up"}),
         node("dn", "crosses_below", {"a": "@close", "b": "@sma", "out": "@dn"}),
         node("entry", "entry", {}), node("exit", "exit", {}),
         node("trail", "trailing_stop", {"value": 3, "source": "close",
                                         "activate_on_profit": True, "activate_pct": 1}),
         node("tstop", "time_stop", {"max_bars": 10}),
         node("size", "position_size", {"size": 0.5})],
        [("t", "sma"), ("sma", "up"), ("sma", "dn"), ("up", "entry"), ("dn", "exit")],
    )
    assert describe_graph(g)["lines"] == [
        "Buy AAPL (1d) when close crosses above SMA(50) of close.",
        "Sell when close crosses below SMA(50) of close.",
        "Trailing stop: 3 % below the highest close since entry, once the trade is up 1 %.",
        "Time stop: the trade closes after 10 bars.",
        # In the editor there is no bot (DB-12).
        "Size: 50 % of the capital.",
    ]


def test_two_group_graph_gets_a_header_per_group():
    result = describe_graph(Graph.model_validate(pair_data()))
    assert result["lines"] == [
        "Group 'long_leg' (50 % of the capital):",
        "Buy AAPL (1d) when RSI(14) is below 38.",
        "Sell when RSI(14) is above 62.",
        "Group 'short_leg' (50 % of the capital):",
        "Sell short MSFT (1d) when RSI(14) is below 60.",
        "Buy back when RSI(14) is above 40.",
    ]
    assert [(g["name"], g["symbol"], g["direction"]) for g in result["groups"]] == [
        ("long_leg", "AAPL", "long"), ("short_leg", "MSFT", "short")]


def test_regime_switch_group_reads_per_side_and_reports_long_short():
    result = describe_graph(Graph.model_validate(switch_data(on_flip="close_only")))
    assert result["lines"] == [
        "While the regime is on, buy AAPL (1d) when RSI(14) is below 38.",
        "Sell when RSI(14) is above 62.",
        "While the regime is off, sell short AAPL (1d) when RSI(14) is above 62.",
        "Buy back when RSI(14) is below 38.",
        "The regime is on when volume is above 550000.",
        "When the regime flips, an open trade is closed.",
    ]
    group = result["groups"][0]
    assert group["direction"] == "long_short"
    assert group["entry"] == "long: RSI(14) is below 38; short: RSI(14) is above 62"


def test_rule_bot_rsi_30_70_stop_2():
    result = describe_rule_bot(rule_bot())
    assert result["lines"] == [
        "Buy AAPL (1d) when RSI(14) is below 30.",
        "Sell when RSI(14) is above 70.",
        "Stop: 2 % below the entry price.",
        "Size: 100 % of the bot's capital.",
    ]
    assert result["warnings"] == []
    group = result["groups"][0]
    assert (group["entry"], group["exit"], group["direction"]) == (
        "RSI(14) is below 30", "RSI(14) is above 70", "long")


def test_rule_bot_with_regime_and_trading_hours():
    cfg = rule_bot(
        symbol="SPY", interval="1h", buy_rules=[], sell_rules=[], stop_loss_pct=None,
        regime=RegimeConfig(enabled=True, timeframe="1d", indicator="ma", min_bars=3,
                            on_flip="close_only"),
        long_buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
        long_sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
        short_buy_rules=[], short_sell_rules=[],
        trading_hours=TradingHoursConfig(enabled=True, start_time="09:45", end_time="15:45"),
    )
    lines = describe_rule_bot(cfg)["lines"]
    assert "While the regime is on, buy SPY (1h) when RSI(14) is below 30." in lines
    assert ("The regime is on when SPY (1d) close is above SMA(200) of SPY (1d) close "
            "on each of the last 3 bars.") in lines
    assert lines[-1] == "Only opens trades from 09:45 to 15:45 ET."


def test_rule_bot_falls_back_to_the_rules_when_they_cannot_be_drawn(monkeypatch):
    import nodebuilder.from_rules as from_rules

    def boom(req):
        raise ValueError("cannot draw")

    monkeypatch.setattr(from_rules, "auto_render", boom)
    result = describe_rule_bot(rule_bot())
    assert result["lines"] == [
        "Buy AAPL (1d) when RSI(14) is below 30.",
        "Sell when RSI(14) is above 70.",
        "Stop: 2 % below the entry price.",
        "Size: 100 % of the bot's capital.",
    ]
    assert len(result["warnings"]) == 1


# ---------------------------------------------------------------------------
# Every catalog node type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", catalog.catalog_entries(), ids=lambda e: e.name)
def test_every_catalog_node_type_gives_a_phrase(entry):
    """Ticker -> node -> Entry (reading the node's first write): the
    describe never raises, and a node that writes something gives the Entry
    a non-empty phrase."""
    nodes = [node("t", "ticker", {"symbol": "SPY", "interval": "5m"}), node("n", entry.name)]
    wires = []
    if entry.inputs.ports or entry.inputs.dynamic:
        wires.append(("t", "n"))
    writes = [p for p in entry.params if p.type == "write"]
    feeds_entry = bool(writes) and entry.cat not in ("output", "settings", "network")
    if feeds_entry:
        nodes.append(node("e", "entry", {"signal": writes[0].default}))
        wires.append(("n", "e"))
    result = describe_graph(graph(nodes, wires))
    assert result["lines"] and all(isinstance(x, str) and x for x in result["lines"])
    if feeds_entry:
        phrase = result["groups"][0]["entry"]
        assert phrase and phrase.strip(), entry.name


def test_every_catalog_type_has_its_own_phrase_or_is_a_known_fallback():
    """A new node type gets a phrase, or is added here on purpose (it then
    shows its type name and params)."""
    # Terminals and settings are phrased by the group (stops, size, costs);
    # merge and the network nodes write nothing of their own.
    terminal_or_structure = {"merge", "position_size", "stop_loss", "slippage", "commission",
                             "borrow_rate", "entry", "exit", "size", "stop", "trailing_stop",
                             "time_stop", "regime", "subnet", "regime_net", "subnet_input",
                             "subnet_output", "output_group"}
    for e in catalog.catalog_entries():
        has_own = hasattr(describe_mod._Describer, f"_t_{e.name}")
        assert has_own or e.name in terminal_or_structure, e.name


def test_unknown_node_type_falls_back_to_its_name_with_a_warning():
    g = graph([node("t", "ticker", {"symbol": "SPY", "interval": "5m"}),
               node("m", "mystery", {"period": 7}),
               node("e", "entry", {"signal": "@mystery"})],
              [("t", "m"), ("m", "e")])
    result = describe_graph(g)
    assert any("unknown type 'mystery'" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Phrase rules
# ---------------------------------------------------------------------------


def test_an_expression_param_shows_its_text():
    g = graph([node("t", "ticker", {"symbol": "SPY", "interval": "5m"}),
               node("rsi", "rsi", {"period": {"expr": "10 + 5"}}),
               node("lo", "below", {"threshold": 40, "out": "@lo"}),
               node("e", "entry", {})],
              [("t", "rsi"), ("rsi", "lo"), ("lo", "e")])
    assert describe_graph(g)["lines"][0] == "Buy SPY (5m) when RSI(= 10 + 5) is below 40."


def test_a_wrangle_shows_custom_python_and_one_warning():
    g = graph([node("t", "ticker", {"symbol": "SPY", "interval": "5m"}),
               node("wr1", "wrangle", {"lookback_bars": 5}, code="@sig = @close > 100"),
               node("e", "entry", {"signal": "@sig"}),
               node("x", "exit", {"signal": "@sig"})],
              [("t", "wr1"), ("wr1", "e"), ("wr1", "x")])
    result = describe_graph(g)
    assert result["lines"][:2] == ["Buy SPY (5m) when custom Python (node 'wr1').",
                                   "Sell when custom Python (node 'wr1')."]
    assert result["warnings"] == ["Node 'wr1' runs Python code; the text shows only its name."]


def test_a_reference_ticker_shows_its_symbol_and_other_interval():
    def ref_graph(interval):
        return graph([node("t", "ticker", {"symbol": "SPY", "interval": "5m"}),
                      node("q", "ticker", {"symbol": "QQQ", "interval": interval,
                                           "prefix": "qqq"}),
                      node("rsi", "rsi", {"period": 14, "source": "@qqq_close"}),
                      node("lo", "below", {"threshold": 30, "out": "@lo"}),
                      node("e", "entry", {})],
                     [("q", "rsi"), ("rsi", "lo"), ("lo", "e")])

    assert describe_graph(ref_graph("5m"))["lines"][0] == "Buy SPY (5m) when QQQ RSI(14) is below 30."
    assert describe_graph(ref_graph("1d"))["lines"][0] == \
        "Buy SPY (5m) when QQQ (1d) RSI(14) is below 30."


def test_logic_gets_brackets_only_where_needed():
    g = graph([node("t", "ticker", {"symbol": "SPY", "interval": "5m"}),
               node("rsi", "rsi", {"period": 14}),
               node("lo", "below", {"threshold": 30, "out": "@lo"}),
               node("hi", "above", {"threshold": 70, "out": "@hi"}),
               node("ts", "time_of_day", {"range": "09:45-15:45"}),
               node("o", "or", {}), node("a", "and", {}), node("a2", "and", {}),
               node("n", "not", {}), node("e", "entry", {})],
              [("t", "rsi"), ("rsi", "lo"), ("rsi", "hi"), ("t", "ts"),
               ("hi", "o", "in0"), ("ts", "o", "in1"),
               ("lo", "a", "in0"), ("o", "a", "in1"),
               ("a", "a2", "in0"), ("lo", "a2", "in1"),
               ("a2", "n"), ("n", "e")])
    assert describe_graph(g)["groups"][0]["entry"] == (
        "not (RSI(14) is below 30 and (RSI(14) is above 70 or the time is 09:45 to 15:45 ET) "
        "and RSI(14) is below 30)")


def test_a_bypassed_term_is_left_out_like_compile_leaves_it_out():
    g = graph([node("t", "ticker", {"symbol": "SPY", "interval": "5m"}),
               node("rsi", "rsi", {"period": 14}),
               node("lo", "below", {"threshold": 30, "out": "@lo"}),
               {**node("hi", "above", {"threshold": 70, "out": "@hi"}), "bypass": True},
               node("a", "and", {}), node("e", "entry", {})],
              [("t", "rsi"), ("rsi", "lo"), ("lo", "hi"), ("hi", "a", "in0"),
               ("lo", "a", "in1"), ("a", "e")])
    assert describe_graph(g)["groups"][0]["entry"] == "RSI(14) is below 30"


def test_an_asset_instance_shows_its_asset_name():
    lib = Library()
    lib.add(regime_filter())
    result = describe_graph(with_instance(), resolve=lib)
    assert result["lines"] == ["Buy AAPL (1d) when regime_filter.",
                               "Sell when close is below regime_filter."]


def test_a_missing_asset_is_a_warning_not_an_error():
    result = describe_graph(with_instance(), resolve=Library())
    assert any("regime_filter" in w for w in result["warnings"])
    assert result["lines"]


def test_an_unwired_entry_is_null_and_says_so():
    g = graph([node("t", "ticker", {"symbol": "SPY", "interval": "5m"}),
               node("e", "entry", {}), node("s", "stop", {"constant": 1})], [])
    result = describe_graph(g)
    assert result["groups"][0]["entry"] is None
    assert result["lines"] == ["No entry rule is wired, so no trade opens.",
                               "No exit rule: a trade closes only through its stops.",
                               "Stop: 1 % below the entry price."]


def test_a_short_bot_direction_flips_the_verbs_and_the_stop_side():
    result = describe_graph(Graph.model_validate(copy.deepcopy(G1)), direction="short")
    assert result["lines"] == ["Sell short SPY (5m) when RSI(14) is below 40.",
                               "Buy back when RSI(14) is above 60.",
                               "Stop: 0.5 % above the entry price."]


# ---------------------------------------------------------------------------
# Reads only, and fast
# ---------------------------------------------------------------------------


def test_describe_never_runs_code_and_never_fetches(monkeypatch):
    def no_code(*a, **k):
        raise AssertionError("describe ran user code")

    def no_fetch(*a, **k):
        raise AssertionError("describe fetched data")

    monkeypatch.setattr(code_pkg, "run", no_code)
    monkeypatch.setattr(shared, "_fetch", no_fetch)
    g = graph([node("t", "ticker", {"symbol": "SPY", "interval": "5m"}),
               node("sma", "sma", {"period": {"expr": "10 + 5"}}),
               node("wr1", "wrangle", {"lookback_bars": 5}, code="@sig = @close > @sma"),
               node("e", "entry", {"signal": "@sig"})],
              [("t", "sma"), ("sma", "wr1"), ("wr1", "e")])
    assert describe_graph(g)["lines"][0] == "Buy SPY (5m) when custom Python (node 'wr1')."


def _big_graph(n_chains: int = 16) -> Graph:
    """51 nodes: n_chains of RSI -> SMA -> below, all ANDed into Entry."""
    nodes = [node("t", "ticker", {"symbol": "SPY", "interval": "5m"})]
    wires = []
    for k in range(n_chains):
        nodes += [node(f"r{k}", "rsi", {"period": 10 + k}),
                  node(f"s{k}", "sma", {"period": 5 + k, "source": f"@rsi_{k}"}),
                  node(f"c{k}", "below", {"a": f"@rsi_{k}", "b": f"@sma_{k}", "out": f"@c{k}"})]
        nodes[-3]["params"]["out"] = f"@rsi_{k}"
        nodes[-2]["params"]["out"] = f"@sma_{k}"
        wires += [("t", f"r{k}"), (f"r{k}", f"s{k}"), (f"s{k}", f"c{k}"),
                  (f"c{k}", "all", f"in{k}")]
    nodes += [node("all", "and", {}), node("e", "entry", {}), node("st", "stop", {"constant": 1})]
    wires.append(("all", "e"))
    return graph(nodes, wires)


def test_a_fifty_node_graph_is_described_quickly():
    g = _big_graph()
    assert len(g.nodes) >= 50
    describe_graph(g)  # warm up imports
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        result = describe_graph(g)
        times.append(time.perf_counter() - t0)
    assert "RSI(21) is below SMA(16) of RSI(21)" in result["groups"][0]["entry"]
    # Target 50 ms (decisions.md); the bound leaves room for a busy machine.
    assert statistics.median(times) < 0.25


# ---------------------------------------------------------------------------
# POST /api/nodebuilder/describe
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(nb_route.router)
    return TestClient(app)


def test_route_describes_the_g1_graph(client):
    r = client.post("/api/nodebuilder/describe", json={"graph": copy.deepcopy(G1)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lines"] == G1_LINES
    assert body["warnings"] == []
    assert body["groups"][0]["stops"] == ["Stop: 0.5 % below the entry price"]


def test_route_ticker_and_interval_fill_only_an_empty_ticker(client):
    g = copy.deepcopy(G1)
    r = client.post("/api/nodebuilder/describe",
                    json={"graph": g, "ticker": "RIVN", "interval": "30m"})
    assert r.json()["lines"][0] == "Buy SPY (5m) when RSI(14) is below 40."
    g["nodes"]["tick"]["params"] = {"symbol": "", "interval": ""}
    r = client.post("/api/nodebuilder/describe",
                    json={"graph": g, "ticker": "rivn", "interval": "30m"})
    assert r.json()["lines"][0] == "Buy RIVN (30m) when RSI(14) is below 40."


def test_route_bad_graph_is_a_400_in_the_usual_shape(client):
    g = data([node("a", "rsi"), node("b", "rsi")], [("a", "b"), ("b", "a")])
    r = client.post("/api/nodebuilder/describe", json={"graph": g})
    assert r.status_code == 400, r.text
    assert "detail" in r.json() and "code" in r.json()
    g = data([node("a", "rsi")], [("a", "ghost")])
    r = client.post("/api/nodebuilder/describe", json={"graph": g})
    assert r.status_code == 400, r.text


def test_route_without_graph_is_a_422(client):
    assert client.post("/api/nodebuilder/describe", json={}).status_code == 422
    r = client.post("/api/nodebuilder/describe", json={"graph": copy.deepcopy(G1), "ticker": 5})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Review fixes (F453 fix pass): each test names its finding
# ---------------------------------------------------------------------------


def g1(**ticker_params) -> dict:
    g = copy.deepcopy(G1)
    if ticker_params:
        g["nodes"]["tick"]["params"] = ticker_params
    return g


def test_fl1_own_symbol_with_no_interval_runs_on_1d_like_the_editor_run():
    """graphTicker.graphOwnTicker: an own symbol with an empty interval
    runs on "1d", whatever the sidebar says."""
    result = describe_graph(Graph.model_validate(g1(symbol="SPY", interval="")),
                            ticker="RIVN", interval="30m")
    assert result["lines"][0] == "Buy SPY (1d) when RSI(14) is below 40."
    assert (result["groups"][0]["symbol"], result["groups"][0]["interval"]) == ("SPY", "1d")


def test_fl1_no_symbol_takes_the_sidebar_symbol_and_interval_as_a_pair():
    """An empty symbol: the run uses the sidebar's symbol AND interval; the
    Ticker's leftover interval ("1d") is not used."""
    result = describe_graph(Graph.model_validate(g1(symbol="", interval="1d")),
                            ticker="RIVN", interval="30m")
    assert result["lines"][0] == "Buy RIVN (30m) when RSI(14) is below 40."


def test_db01_an_empty_stop_terminal_lets_the_bot_stop_show():
    from tests.test_bot_describe import graph_bot
    from nodebuilder.describe import describe_graph_bot

    g = copy.deepcopy(G1)
    g["nodes"]["stop"]["params"] = {}
    g["nodes"]["size"] = node("size", "size", {})
    g["nodes"]["tstop"] = node("tstop", "time_stop", {})
    lines = describe_graph_bot(graph_bot(g, stop_loss_pct=2.0, max_bars_held=10,
                                         position_size=0.5))["lines"]
    assert lines[2:] == ["Stop: 2 % below the entry price (bot setting).",
                         "Time stop: the trade closes after 10 bars (bot setting).",
                         "Size: 50 % of the bot's capital."]
    assert not any("no value set" in line or "no bar limit" in line for line in lines)


def test_db01_an_empty_stop_terminal_lets_a_settings_node_show():
    g = copy.deepcopy(G1)
    g["nodes"]["stop"]["params"] = {}
    g["nodes"]["sl"] = node("sl", "stop_loss", {"pct": 1.5})
    assert describe_graph(Graph.model_validate(g))["lines"][2] == \
        "Stop: 1.5 % below the entry price."


def test_db01_a_one_sided_stop_keeps_the_shared_stop_for_the_other_side():
    d = switch_data(on_flip="close_only")
    d["nodes"]["lstop"] = node("lstop", "stop", {"side": "long", "constant": 1}, parent="sw")
    d["nodes"]["sl"] = node("sl", "stop_loss", {"pct": 2}, parent="sw")
    stops = describe_graph(Graph.model_validate(d))["groups"][0]["stops"]
    assert stops == ["Stop for long trades: 1 % below the entry price",
                     "Stop for short trades: 2 % above the entry price"]


@pytest.mark.parametrize("where", ["terminal", "setting", "bot"])
def test_db02_a_zero_stop_is_no_stop(where):
    from tests.test_bot_describe import graph_bot
    from nodebuilder.describe import describe_graph_bot

    g = copy.deepcopy(G1)
    bot = {}
    if where == "terminal":
        g["nodes"]["stop"]["params"] = {"constant": 0}
        bot = {"stop_loss_pct": 2.0}  # the terminal's 0 wins: no stop at all
    elif where == "setting":
        del g["nodes"]["stop"]
        g["nodes"]["sl"] = node("sl", "stop_loss", {"pct": 0})
    else:
        del g["nodes"]["stop"]
        bot = {"stop_loss_pct": 0.0}
    result = describe_graph_bot(graph_bot(g, **bot))
    assert result["groups"][0]["stops"] == []
    assert not any(line.startswith("Stop") for line in result["lines"])


def test_db02_a_rule_bot_with_a_zero_stop_has_no_stop_line():
    lines = describe_rule_bot(rule_bot(stop_loss_pct=0.0))["lines"]
    assert not any(line.startswith("Stop") for line in lines)


def _reverse_graph(on_flip: str) -> Graph:
    return graph(
        [node("t", "ticker", {"symbol": "SPY", "interval": "5m"}),
         node("rsi", "rsi", {"period": 14}),
         node("lo", "below", {"threshold": 40, "out": "@lo"}),
         node("hi", "above", {"threshold": 60, "out": "@hi"}),
         node("sma", "sma", {"period": 50}),
         node("up", "above", {"a": "@close", "b": "@sma", "out": "@up"}),
         node("entry", "entry", {}), node("exit", "exit", {}),
         node("reg", "regime", {"on_flip": on_flip}),
         node("stop", "stop", {"constant": 1})],
        [("t", "rsi"), ("rsi", "lo"), ("rsi", "hi"), ("lo", "entry"), ("hi", "exit"),
         ("t", "sma"), ("sma", "up"), ("up", "reg")],
    )


def test_db03_close_and_reverse_is_not_an_entry_gate():
    assert describe_graph(_reverse_graph("close_and_reverse"))["lines"] == [
        "Buy SPY (5m) when RSI(14) is below 40, while the regime is on.",
        "Close the trade when RSI(14) is above 60.",
        "Stop: 1 % below the entry price on long trades and above it on short trades.",
        "The regime is on when close is above SMA(50) of close.",
        "While the regime is off, the same entry opens a short trade instead.",
        "When the regime flips, an open trade is closed and the other side opens.",
    ]


def test_db03_hold_and_close_only_still_gate_the_entry():
    for flip in ("hold", "close_only"):
        lines = describe_graph(_reverse_graph(flip))["lines"]
        assert "Only opens trades while close is above SMA(50) of close." in lines
        assert lines[0] == "Buy SPY (5m) when RSI(14) is below 40."


def test_db05_a_higher_timeframe_rule_reads_as_the_rule_with_a_warning():
    bot = rule_bot(interval="5m", buy_rules=[
        Rule(indicator="rsi", condition="below", value=30),
        Rule(indicator="ma", condition="rising", params={"period": 50, "type": "sma"},
             timeframe="1d")])
    result = describe_rule_bot(bot)
    assert result["lines"][0] == ("Buy AAPL (5m) when RSI(14) is below 30 and SMA(50) of close "
                                  "is rising (set to 1d bars).")
    assert not any("cannot run" in line for line in result["lines"])
    assert result["warnings"] == [
        "Some rules are set to another timeframe (1d). Backtests check them on those bars "
        "and join them to the other rules with AND, but the live bot checks them on its "
        "own 5m bars like the other rules."]


def test_db05_a_placeholder_in_the_editor_gets_a_warning():
    g = graph([node("t", "ticker", {"symbol": "SPY", "interval": "5m"}),
               node("x", "above", {"condition_extra": "rising on 1d bars", "a": "@close"}),
               node("e", "entry", {})], [("t", "x"), ("x", "e")])
    result = describe_graph(g)
    assert result["lines"][0] == "Buy SPY (5m) when a rule the graph cannot run (rising on 1d bars)."
    assert any("cannot run" in w for w in result["warnings"])


def test_db06_direct_rules_of_a_regime_bot_read_the_long_and_short_rules(monkeypatch):
    import nodebuilder.from_rules as from_rules

    def boom(req):
        raise ValueError("cannot draw")

    monkeypatch.setattr(from_rules, "auto_render", boom)
    cfg = rule_bot(
        symbol="SPY", interval="1h", stop_loss_pct=2.0,
        regime=RegimeConfig(enabled=True, timeframe="1d", indicator="ma", min_bars=3,
                            on_flip="close_only"),
        long_buy_rules=[Rule(indicator="rsi", condition="below", value=25)],
        long_sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
        short_buy_rules=[Rule(indicator="rsi", condition="above", value=80)],
        short_sell_rules=[Rule(indicator="rsi", condition="below", value=50)],
        long_stop_loss_pct=1.0, short_stop_loss_pct=3.0)
    result = describe_rule_bot(cfg)
    assert result["lines"] == [
        "While the regime is on, buy SPY (1h) when RSI(14) is below 25.",
        "Sell when RSI(14) is above 70.",
        "While the regime is off, sell short SPY (1h) when RSI(14) is above 80.",
        "Buy back when RSI(14) is below 50.",
        "Stop for long trades: 1 % below the entry price.",
        "Stop for short trades: 3 % above the entry price.",
        "Size: 100 % of the bot's capital.",
        "The regime is on when close is above SMA(200) of close, on 1d bars, "
        "for 3 bars in a row.",
        "When the regime flips, an open trade is closed.",
    ]
    assert result["groups"][0]["direction"] == "long_short"
    # The buy rules (RSI below 30) are not used with the regime on.
    assert not any("below 30" in line for line in result["lines"])


def test_db06_direct_rules_name_a_reference_series_in_plain_words(monkeypatch):
    import nodebuilder.from_rules as from_rules

    monkeypatch.setattr(from_rules, "auto_render", lambda req: 1 / 0)
    cfg = rule_bot(buy_rules=[Rule(indicator="ma", condition="crosses_above",
                                   params={"period": 20, "type": "ema"}, param="ma:50:sma")])
    assert describe_rule_bot(cfg)["lines"][0] == \
        "Buy AAPL (1d) when EMA(20) of close crosses above SMA(50) of close."


def test_db07_the_bypass_flag_on_a_terminal_is_ignored_like_compile_ignores_it():
    g = copy.deepcopy(G1)
    g["nodes"]["entry"]["bypass"] = True
    g["nodes"]["stop"]["bypass"] = True
    assert describe_graph(Graph.model_validate(g))["lines"] == G1_LINES


def test_db07_a_bypassed_trailing_stop_is_still_left_out():
    g = copy.deepcopy(G1)
    g["nodes"]["trail"] = node("trail", "trailing_stop", {"value": 3}, bypass=True)
    assert describe_graph(Graph.model_validate(g))["lines"] == G1_LINES


def test_db08_a_graph_bot_whose_group_is_missing_gets_a_warning_and_no_text():
    from tests.test_bot_describe import graph_bot
    from nodebuilder.describe import describe_graph_bot

    for name in (None, "main", "nope"):
        result = describe_graph_bot(graph_bot(pair_data(), graph_group=name))
        assert result["lines"] == [] and result["groups"] == []
        assert len(result["warnings"]) == 1 and "cannot trade" in result["warnings"][0]
    result = describe_graph_bot(graph_bot(pair_data(), graph_group="short_leg"))
    assert [g["name"] for g in result["groups"]] == ["short_leg"]
    assert result["warnings"] == []


def test_db08_a_graph_with_one_group_is_picked_with_no_name():
    from tests.test_bot_describe import graph_bot
    from nodebuilder.describe import describe_graph_bot

    result = describe_graph_bot(graph_bot(switch_data(), graph_group=None, symbol="AAPL",
                                          interval="1d"))
    assert [g["name"] for g in result["groups"]] == ["sw"]


def test_db10_bot_extras_read_as_plain_english():
    from types import SimpleNamespace as NS
    from nodebuilder.describe import bot_extras

    cfg = NS(trading_hours=None,
             dynamic_sizing=NS(enabled=True, consec_sls=1, trigger="sl", reduced_pct=75),
             skip_after_stop=NS(enabled=True, count=1, trigger="both"))
    assert bot_extras(cfg) == ["After a stop, trades use 75 % of the normal size",
                               "After a stop or trailing stop, skips the next entry signal"]
    cfg = NS(trading_hours=None,
             dynamic_sizing=NS(enabled=True, consec_sls=3, trigger="tsl", reduced_pct=50),
             skip_after_stop=NS(enabled=True, count=2, trigger="sl"))
    assert bot_extras(cfg) == [
        "After 3 trailing stops in a row, trades use 50 % of the normal size",
        "After a stop, skips the next 2 entry signals"]


def test_db11_never_terms_drop_out_of_or_and_decide_an_and():
    def entry_of(op, negate=False):
        nodes = [node("t", "ticker", {"symbol": "SPY", "interval": "5m"}),
                 node("rsi", "rsi", {"period": 14}),
                 node("lo", "below", {"threshold": 30, "out": "@lo"}),
                 node("nv", "below", {"a": "@index", "threshold": 0, "out": "@nv"}),
                 node("lg", op, {}), node("e", "entry", {})]
        wires = [("t", "rsi"), ("rsi", "lo"), ("t", "nv"),
                 ("lo", "lg", "in0"), ("nv", "lg", "in1"), ("lg", "e")]
        if negate:
            nodes.insert(4, node("n", "not", {}))
            wires = [("t", "rsi"), ("rsi", "lo"), ("t", "nv"), ("nv", "n"),
                     ("lo", "lg", "in0"), ("n", "lg", "in1"), ("lg", "e")]
        return describe_graph(graph(nodes, wires))["groups"][0]["entry"]

    assert entry_of("or") == "RSI(14) is below 30"
    assert entry_of("and") == "never"
    assert entry_of("and", negate=True) == "RSI(14) is below 30"   # not never = always
    assert entry_of("or", negate=True) == "always"


def test_db11_a_reference_close_below_0_is_never():
    g = graph([node("t", "ticker", {"symbol": "SPY", "interval": "5m"}),
               node("q", "ticker", {"symbol": "QQQ", "interval": "1d", "prefix": "qqq"}),
               node("nv", "below", {"a": "@qqq_close", "threshold": 0, "out": "@nv"}),
               node("e", "entry", {})], [("q", "nv"), ("nv", "e")])
    assert describe_graph(g)["lines"][0] == "Never opens a trade: the entry rule is never true."


def test_db12_size_names_whose_capital_it_is():
    g = copy.deepcopy(G1)
    g["nodes"]["ps"] = node("ps", "position_size", {"size": 0.5})
    assert "Size: 50 % of the capital." in describe_graph(Graph.model_validate(g))["lines"]
    d = pair_data()
    d["nodes"]["ps"] = node("ps", "position_size", {"size": 0.5})
    lines = describe_graph(Graph.model_validate(d))["lines"]
    assert lines.count("Size: 50 % of this group's capital.") == 2


# -- the POST route -----------------------------------------------------------


def test_db04_route_direction_short_flips_an_implicit_group(client):
    r = client.post("/api/nodebuilder/describe",
                    json={"graph": copy.deepcopy(G1), "direction": "short"})
    assert r.status_code == 200, r.text
    assert r.json()["lines"] == ["Sell short SPY (5m) when RSI(14) is below 40.",
                                 "Buy back when RSI(14) is above 60.",
                                 "Stop: 0.5 % above the entry price."]
    assert r.json()["groups"][0]["direction"] == "short"


def test_db04_route_direction_does_not_touch_output_groups(client):
    r = client.post("/api/nodebuilder/describe", json={"graph": pair_data(), "direction": "short"})
    assert r.json()["lines"][1] == "Buy AAPL (1d) when RSI(14) is below 38."


def test_db04_route_refuses_another_direction(client):
    for bad in ("sideways", 1, "regime_switch"):
        r = client.post("/api/nodebuilder/describe",
                        json={"graph": copy.deepcopy(G1), "direction": bad})
        assert r.status_code == 422, (bad, r.text)


def test_db09_route_error_path_never_runs_code(client, monkeypatch):
    calls = []
    real = code_pkg.run

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(code_pkg, "run", spy)
    g = copy.deepcopy(G1)
    g["nodes"]["rsi"]["params"]["period"] = {"expr": "10 + 4"}
    g["wires"].append({"id": "w9", "from": "rsi", "to": "ghost", "from_port": "out",
                       "to_port": "in0", "attr": None})
    r = client.post("/api/nodebuilder/describe", json={"graph": g})
    assert r.status_code == 400, r.text
    # Only the error that stopped the request; no compile ran for the rest.
    assert [d["code"] for d in r.json()["diagnostics"]] == ["dangling_wire"]
    assert calls == []


def test_in4_route_500_detail_is_plain_english(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("internal")

    monkeypatch.setattr(nb_route, "describe_graph", boom)
    r = client.post("/api/nodebuilder/describe", json={"graph": copy.deepcopy(G1)})
    assert r.status_code == 500
    assert r.json()["detail"] == "Could not describe this strategy."


def test_db11_direct_rules_drop_a_rule_that_never_fires(monkeypatch):
    import nodebuilder.from_rules as from_rules

    monkeypatch.setattr(from_rules, "auto_render", lambda req: 1 / 0)
    never = Rule(indicator="rsi", condition="above")  # no value, no series: never fires
    lines = describe_rule_bot(rule_bot(buy_rules=[Rule(indicator="rsi", condition="below",
                                                        value=30), never],
                                       buy_logic="OR"))["lines"]
    assert lines[0] == "Buy AAPL (1d) when RSI(14) is below 30."
    lines = describe_rule_bot(rule_bot(buy_rules=[Rule(indicator="rsi", condition="below",
                                                        value=30), never]))["lines"]
    assert lines[0] == "Never opens a trade: the entry rule is never true."

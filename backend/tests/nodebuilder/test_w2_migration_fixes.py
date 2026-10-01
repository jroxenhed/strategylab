"""The v2 -> v3 migration keeps Wave 1 graphs compiling (F435 W2 fix pass).

LT-2 / MD-04 unwired and orphan indicators, MD-03 logic over 16 inputs,
MD-09 non-numbered ports, MD-07 the dropped Ticker source, MD-08 labels on
wires out of unknown types, MD-06 bots.json after load.  The signal-level
check against the frozen Wave 1 engine is test_w1_goldens.py; this file
pins the shape of what the migration writes and where it must not act.
"""
from __future__ import annotations

import copy
import json
import os

import pytest

import bot_manager as _bot_manager_mod
from bot_manager import BotManager
from nodebuilder.compile import compile as compile_graph
from nodebuilder.migrate import CURRENT_GRAPH_VERSION, migrate_graph_data
from nodebuilder.models import Graph, GraphValidationError

_HERE = os.path.dirname(__file__)
_GOLDEN = {c["id"]: c for c in json.load(open(os.path.join(_HERE, "fixtures", "w1_goldens.json")))["cases"]}


def _hand(name: str) -> dict:
    return copy.deepcopy(_GOLDEN[f"hand:{name}"]["graph"])


def _wires_into(graph: Graph, node_id: str) -> list:
    return [w for w in graph.wires if w.to_path == node_id]


# ---------------------------------------------------------------------------
# LT-2 / MD-04: unwired and orphan indicators
# ---------------------------------------------------------------------------

def test_unwired_indicator_is_wired_from_the_ticker():
    g = Graph.model_validate(_hand("unwired_rsi"))
    (wire,) = _wires_into(g, "rsi")
    assert wire.from_path == "t" and wire.to_port == "in0"
    assert g.nodes["rsi"].params["source"] == "@close"
    compile_graph(g)


def test_unwired_atr_keeps_its_high_low_defaults():
    g = Graph.model_validate(_hand("unwired_atr"))
    p = g.nodes["a"].params
    # Unset reads take their catalog defaults (@high, @low, @close), which is
    # what Wave 1 read; the migration must not point them all at @close.
    assert p.get("high") in (None, "@high") and p.get("low") in (None, "@low")
    assert p.get("close") in (None, "@close")
    step = compile_graph(g).step("a")
    assert set(step.reads) == {"@high", "@low", "@close"}


def test_orphan_indicator_no_longer_blocks_the_graph():
    """A leftover unwired SMA that feeds nothing: Wave 1 compiled the graph,
    Wave 2 refused it, so a bot of this shape could not resume after deploy."""
    g = Graph.model_validate(_hand("orphan_sma"))
    assert [w.from_path for w in _wires_into(g, "orph")] == ["t"]
    compile_graph(g)


def test_no_ticker_gets_one():
    g = Graph.model_validate(_hand("no_ticker"))
    tickers = [n for n in g.nodes.values() if n.type == "ticker"]
    assert len(tickers) == 1 and tickers[0].parent is None
    assert [w.from_path for w in _wires_into(g, "r")] == [tickers[0].id]
    compile_graph(g)


def test_two_tickers_use_the_first_by_id():
    g = Graph.model_validate(_hand("two_tickers_unwired_rsi"))
    assert [w.from_path for w in _wires_into(g, "r")] == ["t"]


def test_v3_graph_with_an_unwired_indicator_is_left_alone():
    """On a v3 graph an unwired indicator may be on purpose (mid-edit); the
    diagnostic is the right answer there, not a silent wire."""
    data = Graph.model_validate(_hand("unwired_rsi")).model_dump(by_alias=True)
    data["wires"] = [w for w in data["wires"] if w["to"] != "rsi"]
    g = Graph.model_validate(data)
    assert g.version == CURRENT_GRAPH_VERSION
    assert _wires_into(g, "rsi") == []
    with pytest.raises(GraphValidationError):
        compile_graph(g)


def test_graph_without_entry_gets_no_wire():
    data = _hand("unwired_rsi")
    del data["nodes"]["entry"]
    data["wires"] = [w for w in data["wires"] if w["to"] != "entry"]
    g = Graph.model_validate(data)
    assert _wires_into(g, "rsi") == []


def test_unwired_bypassed_indicator_stays_off():
    g = Graph.model_validate(_hand("unwired_bypassed_rsi"))
    assert g.nodes["r"].bypass is True
    assert [w.from_path for w in _wires_into(g, "r")] == ["t"]
    # It compiles; its signals equal Wave 1's (the bypassed RSI's reader is
    # off) in test_w1_goldens.py.
    compile_graph(g)


# ---------------------------------------------------------------------------
# MD-03: logic over 16 inputs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,parts", [("and_20", 2), ("or_20", 2), ("or_40", 3)])
def test_wide_logic_is_split_into_parts(name, parts):
    g = Graph.model_validate(_hand(name))
    lg = g.nodes["lg"]
    into = _wires_into(g, "lg")
    assert len(into) == parts
    assert len(lg.params["terms"]) == parts
    part_ids = [w.from_path for w in into]
    for pid in part_ids:
        assert g.nodes[pid].type == lg.type and g.nodes[pid].parent is None
        assert 1 <= len(_wires_into(g, pid)) <= 16
    total = sum(len(_wires_into(g, pid)) for pid in part_ids)
    assert total == int(name.split("_")[1])
    # Wires out of the original are untouched.
    assert [w.to_path for w in g.wires if w.from_path == "lg"] == ["entry"]
    compile_graph(g)


def test_wave1_autorender_20_rules_compiles():
    compile_graph(Graph.model_validate(_hand("w1_autorender_20_rules")))


def test_v3_logic_over_16_is_not_split():
    data = Graph.model_validate(_hand("and_20")).model_dump(by_alias=True)
    # Rebuild a flat 20-input AND in v3 form: still refused (the editor
    # cannot draw it; only an API body can).
    flat = _hand("and_20")
    flat["_version"] = CURRENT_GRAPH_VERSION
    for w in flat["wires"]:
        w.pop("attr", None)
    flat["nodes"]["lg"]["params"] = {"terms": [f"@c{k:02d}" for k in range(20)]}
    for k in range(20):
        flat["nodes"][f"c{k:02d}"]["params"]["out"] = f"@c{k:02d}"
        flat["nodes"][f"c{k:02d}"]["params"]["a"] = "@rsi"
    g = Graph.model_validate(flat)
    assert len(_wires_into(g, "lg")) == 20
    with pytest.raises(GraphValidationError):
        compile_graph(g)
    assert data["_version"] == CURRENT_GRAPH_VERSION


# ---------------------------------------------------------------------------
# MD-09: non-numbered port ids
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,node,port", [
    ("odd_port_not", "n", "in0"), ("odd_port_entry", "entry", "in0"), ("odd_port_rsi", "r", "in0"),
])
def test_single_input_odd_port_becomes_in0(name, node, port):
    g = Graph.model_validate(_hand(name))
    assert [w.to_port for w in _wires_into(g, node)] == [port]


def test_logic_odd_port_follows_the_numbered_ones():
    g = Graph.model_validate(_hand("odd_port_and"))
    ports = {w.from_path: w.to_port for w in _wires_into(g, "and")}
    assert ports == {"c1": "in0", "c0": "in1"}


def test_comparison_odd_port_stays_refused():
    """Wave 1 refused a comparison wire on a non-numbered port; so does W2."""
    data = _hand("odd_port_not")
    for w in data["wires"]:
        if w["to"] == "c0":
            w["to_port"] = "a"
    with pytest.raises(GraphValidationError):
        compile_graph(Graph.model_validate(data))


# ---------------------------------------------------------------------------
# MD-07 / MD-08
# ---------------------------------------------------------------------------

def test_dropped_ticker_source_is_kept_in_meta():
    data = _hand("unwired_rsi")
    data["nodes"]["t"]["params"]["source"] = "ibkr"
    g = Graph.model_validate(data)
    assert "source" not in g.nodes["t"].params
    assert g.meta["legacy_source"] == "ibkr"
    # A second load (v3) does not touch it.
    again = Graph.model_validate(g.model_dump(by_alias=True))
    assert again.meta == g.meta


def test_legacy_source_never_overflows_meta():
    data = _hand("unwired_rsi")
    data["meta"] = {f"k{i}": i for i in range(32)}
    g = Graph.model_validate(data)
    assert "legacy_source" not in g.meta and len(g.meta) == 32


def test_label_out_of_an_unknown_type_is_kept():
    """MD-08: the graph is refused either way, but the label must survive so
    the choice is there when the type is registered."""
    data = {"_version": 2, "nodes": {
        "t": {"id": "t", "type": "ticker", "params": {}},
        "x": {"id": "x", "type": "stochastic_future", "params": {}},
        "c": {"id": "c", "type": "above", "params": {"threshold": 20}},
        "entry": {"id": "entry", "type": "entry", "params": {}},
    }, "wires": [
        {"id": "w0", "from": "t", "to": "x", "to_port": "in0", "attr": "@close"},
        {"id": "w1", "from": "x", "to": "c", "to_port": "in0", "attr": "@stoch_k"},
        {"id": "w2", "from": "c", "to": "entry", "to_port": "in0", "attr": "@bool"},
    ]}
    out = migrate_graph_data(copy.deepcopy(data))
    labels = {w["id"]: w.get("attr") for w in out["wires"]}
    assert labels["w1"] == "@stoch_k"


# ---------------------------------------------------------------------------
# MD-06: what BotManager.load() writes back
# ---------------------------------------------------------------------------

def test_botsjson_after_load_is_v3_and_a_second_load_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))
    path = tmp_path / "bots.json"
    monkeypatch.setattr(_bot_manager_mod, "DATA_PATH", str(path))
    rows = []
    for i, name in enumerate(("orphan_sma", "and_20", "odd_port_not", "settings_all")):
        rows.append({"config": {
            "bot_id": f"g-{i}", "strategy_name": name, "symbol": "AAPL", "interval": "1d",
            "buy_rules": [], "sell_rules": [], "allocated_capital": 100.0,
            "kind": "graph", "graph": _hand(name)}, "state": {"status": "stopped"}})
    path.write_text(json.dumps({"bot_fund": 1000.0, "bots": rows}, indent=2))

    BotManager().load()
    first = path.read_text()
    saved = json.loads(first)
    assert len(saved["bots"]) == 4
    for row in saved["bots"]:
        graph = row["config"]["graph"]
        assert graph["version"] == CURRENT_GRAPH_VERSION
        compile_graph(Graph.model_validate(graph))

    BotManager().load()
    assert json.loads(path.read_text())["bots"] == saved["bots"]

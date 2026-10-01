"""W2 item 2.B: graph migration v2 -> v3 (plan D4, section 4.1).

v2 picked what a node read through each wire's ``attr`` label; v3 keeps that
choice in the node's read params and stores every write name.  The rule this
file holds the migration to: a graph computes exactly what it did before,
however it was stored (API body, Wave 1 graph file, bots.json snapshot, a v1
graph from before Wave 1).

"Before" is the same graph loaded with the v2 -> v3 step switched off: the
kernel still reads ``wire.attr`` for an empty param, which is how it ran v2
graphs (the temporary Wave 2 legacy-equivalence suite showed that equals the
Wave 1 engine: 1121 cases passed before it was deleted at wave end).

fixtures/v2_autorender_corpus.json is the Wave 1 auto_render output for the
rule sweep, its variants and the parity strategies, frozen before from_rules
moved to v3.  Do not regenerate it.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import numpy as np
import pytest

import nodebuilder.migrate as migrate_mod
from nodebuilder.compile import compile as compile_graph
from nodebuilder.evaluator import cook_signals
from nodebuilder.migrate import CURRENT_GRAPH_VERSION, migrate_graph_data
from nodebuilder.models import (
    CyclicGraphError,
    DanglingWireError,
    Graph,
    GraphValidationError,
    UnsupportedGraphVersionError,
)
from tests.nodebuilder.test_rule_coverage import _DF

_HERE = Path(os.path.dirname(__file__))
_CORPUS = json.loads((_HERE / "fixtures" / "v2_autorender_corpus.json").read_text())["cases"]
_V1 = json.loads((_HERE / "vectors" / "v1_autorender.json").read_text())
_BENCH = json.loads((_HERE / "fixtures" / "bench_graph_30.json").read_text())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_v2(data: dict) -> Graph:
    """*data* loaded the v2 way: names and ports filled, attr labels kept."""
    real = migrate_mod.migrate_v2_to_v3
    migrate_mod.migrate_v2_to_v3 = lambda d: d
    try:
        graph = Graph.model_validate(copy.deepcopy(data))
    finally:
        migrate_mod.migrate_v2_to_v3 = real
    assert graph.version <= 2
    return graph


def _outcome(graph: Graph) -> tuple:
    """What the graph computes on the synthetic frame, or why it is refused."""
    try:
        program = compile_graph(graph)
    except GraphValidationError as exc:
        return ("refused", getattr(exc, "code", type(exc).__name__), exc.node_id)
    entry, exit_ = cook_signals(program, _DF)
    settings = tuple((s.key, s.value) for s in program.simulator_settings)
    return ("ok", entry.tobytes(), exit_.tobytes(), settings, program.exit_attr is None)


def _assert_v3_shape(graph: Graph) -> None:
    assert graph.version == 3 == CURRENT_GRAPH_VERSION
    for wire in graph.wires:
        assert wire.attr is None, f"wire {wire.id} kept attr {wire.attr!r}"
    for node in graph.nodes.values():
        if node.type == "ticker":
            assert "source" not in node.params


def _node(node_id: str, node_type: str, params: dict | None = None) -> dict:
    return {"id": node_id, "type": node_type, "params": params or {}}


def _wire(src: str, dst: str, port: str, attr: str | None = None) -> dict:
    w = {"id": f"{src}->{dst}:{port}", "from": src, "to": dst, "to_port": port}
    if attr is not None:
        w["attr"] = attr
    return w


def _graph(nodes: list[dict], wires: list[dict], version: int = 2) -> dict:
    return {"_version": version, "nodes": {n["id"]: n for n in nodes}, "wires": wires}


_TICKER = _node("/t", "ticker", {"symbol": "SYN", "interval": "1d", "source": "yahoo"})


# ---------------------------------------------------------------------------
# Every stored v2 graph computes the same after migration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _CORPUS, ids=[c["id"] for c in _CORPUS])
def test_wave1_autorender_graph_computes_the_same(case):
    before = _outcome(_load_v2(case["graph"]))
    after_graph = Graph.model_validate(copy.deepcopy(case["graph"]))
    _assert_v3_shape(after_graph)
    assert _outcome(after_graph) == before
    if case["compiles"]:
        assert before[0] == "ok"


def test_corpus_covers_both_outcomes():
    assert sum(c["compiles"] for c in _CORPUS) >= 60
    assert any(not c["compiles"] for c in _CORPUS)


def test_bench_graph_computes_the_same():
    """The 30-node benchmark graph (fan-out, MACD slots, logic) is v2."""
    before = _outcome(_load_v2(_BENCH))
    after = Graph.model_validate(copy.deepcopy(_BENCH))
    _assert_v3_shape(after)
    assert before[0] == "ok"
    assert _outcome(after) == before


@pytest.mark.parametrize("group", ["api", "botsjson"])
def test_v1_graphs_go_all_the_way_to_v3(group):
    for name, raw in _V1[group].items():
        before = _outcome(_load_v2(raw))
        after = Graph.model_validate(copy.deepcopy(raw))
        _assert_v3_shape(after)
        assert _outcome(after) == before, name


def test_v3_load_is_idempotent():
    """Loading a migrated graph again changes nothing."""
    for case in _CORPUS:
        once = Graph.model_validate(copy.deepcopy(case["graph"]))
        twice = Graph.model_validate(once.model_dump(by_alias=True))
        assert twice.model_dump(by_alias=True) == once.model_dump(by_alias=True)


def test_migration_does_not_change_the_callers_dict():
    raw = copy.deepcopy(_CORPUS[0]["graph"])
    kept = copy.deepcopy(raw)
    Graph.model_validate(raw)
    migrate_graph_data(raw)
    assert raw == kept


# ---------------------------------------------------------------------------
# Every load path
# ---------------------------------------------------------------------------


def test_botsjson_dump_shape_loads(tmp_path, monkeypatch):
    """bots.json holds Graph.model_dump(): key "version", wires with
    from_path / to_path.  BotManager.load migrates it; no bot starts."""
    import bot_manager as bot_manager_mod
    from bot_manager import BotManager

    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))
    path = tmp_path / "bots.json"
    monkeypatch.setattr(bot_manager_mod, "DATA_PATH", str(path))

    case = next(c for c in _CORPUS if c["id"] == "parity-macd_crossover")
    dumped = _load_v2(case["graph"]).model_dump()  # by_alias=False, as bots.json
    assert dumped["version"] == 2 and "from_path" in dumped["wires"][0]
    entry = {
        "config": {
            "bot_id": "g-1", "strategy_name": "Graph test", "symbol": "SYN", "interval": "1d",
            "buy_rules": [], "sell_rules": [], "allocated_capital": 100.0,
            "kind": "graph", "graph": dumped,
        },
        "state": {"status": "running"},
    }
    path.write_text(json.dumps({"bot_fund": 1000.0, "bots": [entry]}))
    mgr = BotManager()
    mgr.load()
    config, state = mgr.bots["g-1"]
    assert state.status == "stopped"
    _assert_v3_shape(config.graph)
    assert _outcome(config.graph) == _outcome(_load_v2(case["graph"]))


def test_wave1_graph_file_loads_and_saves_as_v3(tmp_path):
    """A graph file Wave 1 storage wrote (v2 inside the envelope) reads back
    as v3 (MD-02: the server always returns the current version; the file
    is rewritten on the next save), with the same result, and saves as v3."""
    from nodebuilder.storage import GraphStore

    store = GraphStore(tmp_path)
    case = next(c for c in _CORPUS if c["id"] == "parity-not_negated_rule")
    env = store.create("old graph", graph=case["graph"])
    # Put the v2 graph back in the file, as Wave 1 wrote it.
    file = tmp_path / "graphs" / f"{env['id']}.json"
    on_disk = json.loads(file.read_text())
    on_disk["graph"] = case["graph"]
    file.write_text(json.dumps(on_disk))

    raw = store.get(env["id"])["graph"]
    assert raw["_version"] == 3
    assert json.loads(file.read_text())["graph"]["_version"] == 2  # not rewritten by a read
    graph = Graph.model_validate(raw)
    _assert_v3_shape(graph)
    assert _outcome(graph) == _outcome(_load_v2(case["graph"]))

    saved = store.update(env["id"], env["rev"], raw)
    assert saved["graph"]["_version"] == 3
    # The Wire model still has the field, so a dump carries attr: null.
    assert all(w.get("attr") is None for w in saved["graph"]["wires"])


def test_validate_reads_a_v2_graph():
    from nodebuilder.diagnostics import validate_graph_full

    case = next(c for c in _CORPUS if c["id"] == "parity-simple_long_rsi")
    result = validate_graph_full(copy.deepcopy(case["graph"]))
    assert [d.code for d in result.diagnostics if d.severity == "error"] == []
    assert set(result.streams) == set(case["graph"]["nodes"])


# ---------------------------------------------------------------------------
# The rules, one at a time
# ---------------------------------------------------------------------------


def _migrated(nodes: list[dict], wires: list[dict]) -> Graph:
    return Graph.model_validate(_graph(nodes, wires))


def test_write_names_are_stored_and_unique():
    g = _migrated(
        [_TICKER, _node("/r1", "rsi"), _node("/r2", "rsi", {"period": 7}),
         _node("/m", "macd", {"out_line": "@my_line"})],
        [_wire("/t", "/r1", "in0", "@close"), _wire("/t", "/r2", "in0", "@close"),
         _wire("/t", "/m", "in0", "@close")],
    )
    assert g.nodes["/r1"].params["out"] == "@rsi"
    assert g.nodes["/r2"].params["out"] == "@rsi_2"
    # A name the graph already had is kept.
    assert g.nodes["/m"].params["out_line"] == "@my_line"
    assert g.nodes["/m"].params["out_signal"] == "@macd_signal"


def test_labels_become_read_params():
    g = _migrated(
        [_TICKER, _node("/m", "macd"), _node("/m2", "macd", {"fast": 5}),
         _node("/x", "crosses_above"), _node("/hi", "above", {"threshold": 260}),
         _node("/r", "rsi"), _node("/b", "below", {"threshold": 30})],
        [_wire("/t", "/m", "in0", "@close"), _wire("/t", "/m2", "in0", "@close"),
         # A MACD output label names that slot, under the slot's final name.
         _wire("/m", "/x", "in0", "@macd_line"), _wire("/m2", "/x", "in1", "@macd_signal"),
         # A Ticker field label names that field.
         _wire("/t", "/hi", "in0", "@high"),
         # Any other label reads the source's primary write.
         _wire("/t", "/r", "in0", "@close"), _wire("/r", "/b", "in0", "@bool")],
    )
    assert g.nodes["/x"].params["a"] == "@macd_line"
    assert g.nodes["/x"].params["b"] == "@macd_signal_2"
    assert g.nodes["/hi"].params["a"] == "@high"
    assert g.nodes["/b"].params["a"] == "@rsi"
    assert "b" not in g.nodes["/b"].params  # nothing on in1: the threshold is used
    # An indicator's source ignores the label: the Ticker's primary, @close.
    assert g.nodes["/m"].params["source"] == "@close"


def test_indicator_source_ignores_a_ticker_field_label():
    g = _migrated([_TICKER, _node("/s", "sma")], [_wire("/t", "/s", "in0", "@high")])
    assert g.nodes["/s"].params["source"] == "@close"


def test_ticker_source_param_is_dropped():
    g = _migrated([_TICKER], [])
    assert g.nodes["/t"].params == {"symbol": "SYN", "interval": "1d"}


def test_atr_triple_wire_collapses_to_one():
    """Wave 0 auto_render wired the Ticker into ATR on in0, in1 and in2."""
    g = _migrated(
        [_TICKER, _node("/atr", "atr")],
        [_wire("/t", "/atr", "in0", "@high"), _wire("/t", "/atr", "in1", "@low"),
         _wire("/t", "/atr", "in2", "@close")],
    )
    assert [(w.from_path, w.to_port) for w in g.wires] == [("/t", "in0")]
    params = g.nodes["/atr"].params
    assert params["source"] == "@close"
    assert "high" not in params and "low" not in params  # their own defaults, @high/@low


def test_extra_wire_from_another_node_is_kept_for_compile_to_refuse():
    g = _migrated(
        [_TICKER, _node("/r", "rsi"), _node("/s", "sma")],
        [_wire("/t", "/s", "in0", "@close"), _wire("/r", "/s", "in1", "@rsi"),
         _wire("/t", "/r", "in0", "@close")],
    )
    assert len(g.wires) == 3
    with pytest.raises(GraphValidationError) as info:
        compile_graph(g)
    assert getattr(info.value, "code", None) == "port_unknown"


def test_logic_terms_follow_port_order_not_list_order():
    nodes = [_TICKER, _node("/r", "rsi"),
             _node("/lo", "below", {"threshold": 40}), _node("/hi", "above", {"threshold": 60}),
             _node("/or", "or"), _node("/e", "entry")]
    wires = [_wire("/t", "/r", "in0", "@close"),
             _wire("/r", "/lo", "in0", "@rsi"), _wire("/r", "/hi", "in0", "@rsi"),
             _wire("/hi", "/or", "in1", "@bool"), _wire("/lo", "/or", "in0", "@bool"),
             _wire("/or", "/e", "in0", "@bool")]
    g = _migrated(nodes, wires)
    assert g.nodes["/or"].params["terms"] == ["@below", "@above"]
    assert g.nodes["/e"].params["signal"] == "@or"


def test_params_already_named_are_kept():
    g = _migrated(
        [_TICKER, _node("/a", "above", {"a": "@low", "threshold": 1})],
        [_wire("/t", "/a", "in0", "@high")],
    )
    assert g.nodes["/a"].params["a"] == "@low"
    assert g.wires[0].attr is None


def test_frontend_v2_graph_without_wire_attrs_is_left_alone():
    """The W2 editor saved v2 graphs (before GRAPH_VERSION became 3) whose
    wires carry no attr and whose consumer params already hold the operand.
    The migration must keep every param it finds, even one that is not the
    source's primary write (here a reversed MACD pair on one wire), and add
    no wire attr."""
    nodes = [_TICKER,
             _node("/m", "macd", {"source": "@close", "out_line": "@macd_line",
                                  "out_signal": "@macd_signal", "out_hist": "@macd_histogram"}),
             _node("/x", "crosses_above", {"a": "@macd_signal", "b": "@macd_line", "out": "@xa"}),
             _node("/e", "entry", {"signal": "@xa"})]
    wires = [_wire("/t", "/m", "in0"), _wire("/m", "/x", "in0"), _wire("/x", "/e", "in0")]
    g = _migrated(copy.deepcopy(nodes), copy.deepcopy(wires))
    assert g.version == CURRENT_GRAPH_VERSION
    for n in nodes[1:]:
        for key, value in n["params"].items():
            assert g.nodes[n["id"]].params[key] == value, (n["id"], key)
    assert [(w.from_path, w.to_path, w.to_port, w.attr) for w in g.wires] == [
        ("/t", "/m", "in0", None), ("/m", "/x", "in0", None), ("/x", "/e", "in0", None)]
    # It cooks as written: signal crossing above the line, not the reverse.
    reversed_ = copy.deepcopy(nodes)
    reversed_[2]["params"].update({"a": "@macd_line", "b": "@macd_signal"})
    e1, _ = cook_signals(compile_graph(g), _DF)
    e2, _ = cook_signals(compile_graph(_migrated(reversed_, copy.deepcopy(wires))), _DF)
    assert e1.any() and e2.any() and not np.array_equal(e1, e2)


def test_label_of_another_type_stays_an_error():
    """A wire out of an RSI labelled @macd_signal was an attr_type error in
    v2.  The label is kept (the param stays empty), so it still is."""
    nodes = [_TICKER, _node("/r", "rsi"), _node("/a", "above", {"threshold": 50}),
             _node("/e", "entry")]
    wires = [_wire("/t", "/r", "in0", "@close"), _wire("/r", "/a", "in0", "@macd_signal"),
             _wire("/a", "/e", "in0", "@bool")]
    before = _outcome(_load_v2(_graph(nodes, wires)))
    g = _migrated(nodes, wires)
    assert "a" not in g.nodes["/a"].params
    assert next(w for w in g.wires if w.to_path == "/a").attr == "@macd_signal"
    assert before[:2] == ("refused", "attr_type")
    assert _outcome(g) == before


def test_wire_into_an_unknown_type_keeps_its_label():
    g = _migrated(
        [_TICKER, _node("/z", "no_such_node")],
        [_wire("/t", "/z", "in0", "@high")],
    )
    assert g.wires[0].attr == "@high"


def test_bypassed_source_still_gets_its_name():
    """A read of a bypassed node's write is stored like any other; compile
    then turns the reader off exactly as before."""
    nodes = [_TICKER, _node("/r", "rsi"), _node("/a", "above", {"threshold": 50}),
             _node("/b", "below", {"threshold": 40}), _node("/and", "and"), _node("/e", "entry")]
    nodes[2]["bypass"] = True
    wires = [_wire("/t", "/r", "in0", "@close"), _wire("/r", "/a", "in0", "@rsi"),
             _wire("/r", "/b", "in0", "@rsi"), _wire("/a", "/and", "in0", "@bool"),
             _wire("/b", "/and", "in1", "@bool"), _wire("/and", "/e", "in0", "@bool")]
    before = _outcome(_load_v2(_graph(nodes, wires)))
    g = _migrated(nodes, wires)
    assert g.nodes["/and"].params["terms"] == ["@above", "@below"]
    assert before[0] == "ok"
    assert _outcome(g) == before


# ---------------------------------------------------------------------------
# Bad input: never a crash inside the migration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("data, error", [
    (_graph([_TICKER, _node("/a", "above")], [_wire("/t", "/gone", "in0", "@close")]),
     DanglingWireError),
    (_graph([_node("/x", "and"), _node("/y", "and")],
            [_wire("/x", "/y", "in0"), _wire("/y", "/x", "in0")]), CyclicGraphError),
])
def test_broken_graphs_reach_the_model_checks(data, error):
    migrated = migrate_graph_data(copy.deepcopy(data))
    assert migrated["_version"] == 3
    with pytest.raises(error):
        Graph.model_validate(copy.deepcopy(data))


def test_malformed_parts_are_left_for_the_model():
    data = {
        "_version": 2,
        "nodes": {
            "/t": dict(_TICKER),
            "/p": {"id": "/p", "type": "rsi", "params": "not a dict"},
            "/q": {"id": 5, "type": "rsi"},
            "/s": "not a node",
        },
        "wires": [_wire("/t", "/p", "in0", "@close"), "not a wire", {"id": "w", "to": ["x"]}],
    }
    migrated = migrate_graph_data(data)
    assert migrated["_version"] == 3
    assert migrated["nodes"]["/p"]["params"] == "not a dict"
    assert migrated["nodes"]["/s"] == "not a node"


def test_a_newer_version_is_refused():
    with pytest.raises(UnsupportedGraphVersionError):
        migrate_graph_data({"_version": CURRENT_GRAPH_VERSION + 1, "nodes": {}, "wires": []})


def test_empty_graph_is_v3():
    g = Graph.model_validate({})
    assert g.version == 3 and g.nodes == {} and g.wires == []


def test_signals_really_differ_by_operand():
    """Guard for the equality tests above: swapping a and b changes the result,
    so equal outcomes are not an accident of the data."""
    nodes = [_TICKER, _node("/f", "ema", {"period": 5}), _node("/s", "sma", {"period": 30}),
             _node("/x", "crosses_above"), _node("/e", "entry")]
    wires = [_wire("/t", "/f", "in0", "@close"), _wire("/t", "/s", "in0", "@close"),
             _wire("/f", "/x", "in0", "@ema"), _wire("/s", "/x", "in1", "@sma"),
             _wire("/x", "/e", "in0", "@bool")]
    g = _migrated(nodes, wires)
    assert (g.nodes["/x"].params["a"], g.nodes["/x"].params["b"]) == ("@ema", "@sma")
    swapped = g.model_copy(deep=True)
    swapped.nodes["/x"].params.update(a="@sma", b="@ema")
    e1, _ = cook_signals(compile_graph(g), _DF)
    e2, _ = cook_signals(compile_graph(swapped), _DF)
    assert e1.any() and e2.any() and not np.array_equal(e1, e2)

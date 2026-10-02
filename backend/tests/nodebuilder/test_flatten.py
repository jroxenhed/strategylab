"""Network core: flatten, boundary nodes and sibling-only wires (F435 W5 item 5.0).

A nested graph must compile to exactly what the same graph drawn flat by
hand compiles to (plan D7).  Wires that cross networks are refused, and
every flatten problem is a diagnostic on the node the user sees.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.compile import check_graph
from nodebuilder.diagnostics import validate_graph_full
from nodebuilder.evaluator import cook_signals
from nodebuilder.kernel import registry
from nodebuilder.kernel.evaluate import analyze_graph, build_steps
from nodebuilder.kernel.flatten import composite_id, flatten, source_of_flat_id
from nodebuilder.kernel.schema import analyze
from nodebuilder.models import (
    Graph,
    ReservedNodeIdError,
    WireCrossesNetworkError,
    network_wire_issues,
)

N = 300


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    rng = np.random.default_rng(5)
    t = np.arange(N)
    close = 100 + 8 * np.sin(t / 12) + np.cumsum(rng.normal(0, 0.7, N))
    idx = pd.date_range("2023-01-02", periods=N, freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": close, "High": close + 0.5, "Low": close - 0.5,
                         "Close": close, "Volume": rng.integers(1e5, 1e6, N)}, index=idx)


def _node(nid, typ, params=None, parent=None, bypass=False):
    return {"id": nid, "type": typ, "name": nid, "parent": parent,
            "params": params or {}, "bypass": bypass}


def _data(nodes: list[dict], wires: list[tuple]) -> dict:
    """wires: [(id, from, to, port)]."""
    return {
        "_version": 3,
        "nodes": {n["id"]: n for n in nodes},
        "wires": [{"id": wid, "from": a, "to": b, "to_port": p} for wid, a, b, p in wires],
    }


def _graph(nodes, wires) -> Graph:
    return Graph.model_validate(_data(nodes, wires))


def _unchecked(nodes, wires) -> Graph:
    """A graph built without the model's checks (as /validate's fallback does)."""
    g = Graph.model_validate(_data([{**n, "parent": None} for n in nodes], []))
    raw = _data(nodes, wires)
    from nodebuilder.models import Node, Wire
    return g.model_copy(update={
        "nodes": {k: Node.model_validate(v) for k, v in raw["nodes"].items()},
        "wires": [Wire.model_validate(w) for w in raw["wires"]],
    })


def _codes(found) -> list[tuple]:
    return [(d.code, d.node_id) for d, _e in found]


def _same_program(nested: Graph, hand: Graph) -> None:
    a, flat = analyze_graph(nested)
    b = analyze(hand)
    assert build_steps(a) == build_steps(b)
    assert {k: v.to_json() for k, v in a.schemas().items()} == \
           {k: v.to_json() for k, v in b.schemas().items()}
    assert _codes(a.found) == _codes(b.found)
    assert a.required_lookback_bars() == b.required_lookback_bars()
    assert a.order == b.order


# ---------------------------------------------------------------------------
# One level: x -> subnet(in0) ... subnet -> entry
# ---------------------------------------------------------------------------


def _one_level():
    nested = _graph(
        [_node("t", "ticker"),
         _node("net", "subnet"),
         _node("in0", "subnet_input", {"port": 0}, "net"),
         _node("rsi", "rsi", {"period": 14}, "net"),
         _node("lo", "below", {"threshold": 35, "out": "@lo"}, "net"),
         _node("out", "subnet_output", {}, "net"),
         _node("entry", "entry")],
        [("w1", "t", "net", "in0"),
         ("w2", "in0", "rsi", "in0"),
         ("w3", "rsi", "lo", "in0"),
         ("w4", "lo", "out", "in0"),
         ("w5", "net", "entry", "in0")],
    )
    hand = _graph(
        [_node("t", "ticker"), _node("rsi", "rsi", {"period": 14}),
         _node("lo", "below", {"threshold": 35, "out": "@lo"}), _node("entry", "entry")],
        [("w2", "t", "rsi", "in0"), ("w3", "rsi", "lo", "in0"), ("w5", "lo", "entry", "in0")],
    )
    return nested, hand


def test_nested_graph_compiles_like_hand_flattened(df):
    nested, hand = _one_level()
    _same_program(nested, hand)
    flat = flatten(nested)
    assert set(flat.graph.nodes) == set(hand.nodes)
    assert [(w.id, w.from_path, w.to_path, w.to_port) for w in flat.graph.wires] == \
           [(w.id, w.from_path, w.to_path, w.to_port) for w in hand.wires]
    # The flat graph through today's compile gives the same program and signals.
    p_flat, p_hand = check_graph(flat.graph).program, check_graph(hand).program
    assert p_flat is not None and p_hand is not None
    assert p_flat.steps == p_hand.steps
    assert (p_flat.entry_attr, p_flat.exit_attr) == (p_hand.entry_attr, p_hand.exit_attr)
    e1, x1 = cook_signals(p_flat, df)
    e2, x2 = cook_signals(p_hand, df)
    assert np.array_equal(e1, e2) and np.array_equal(x1, x2)
    assert e1.any()


def test_flat_ids_map_to_themselves_for_inline_networks():
    nested, _hand = _one_level()
    flat = flatten(nested)
    assert flat.flat_to_source == {nid: nid for nid in ("t", "rsi", "lo", "entry")}
    assert set(flat.networks) == {"net"}
    assert set(flat.boundaries) == {"in0", "out"}
    assert flat.network_of("rsi") == "net"
    assert flat.network_of("entry") is None


def test_compile_flattens_nested_graph():
    nested, hand = _one_level()
    a, b = check_graph(nested), check_graph(hand)
    assert a.program is not None and b.program is not None
    assert a.program.steps == b.program.steps
    # /validate then lists every crossing wire, not only the first.
    nodes, wires = _crossing()
    codes = {(d.code, d.node_id) for d in validate_graph_full(_data(nodes, wires)).diagnostics}
    assert ("wire_crosses_network", "entry") in codes


# ---------------------------------------------------------------------------
# Nested twice, two inputs, a pass-through network, network to network
# ---------------------------------------------------------------------------


def _two_levels():
    nested = _graph(
        [_node("t", "ticker"),
         _node("outer", "subnet"),
         _node("oi0", "subnet_input", {"port": 0}, "outer"),
         _node("inner", "subnet", None, "outer"),
         _node("ii0", "subnet_input", {"port": 0}, "inner"),
         _node("rsi_a", "rsi", {"period": 14}, "inner"),
         _node("lo", "below", {"threshold": 40, "out": "@lo"}, "inner"),
         _node("io", "subnet_output", {}, "inner"),
         _node("rsi_b", "rsi", {"period": 7}, "outer"),
         _node("hi", "above", {"threshold": 60, "out": "@hi"}, "outer"),
         _node("either", "or", {"out": "@either"}, "outer"),
         _node("oo", "subnet_output", {}, "outer"),
         # A network that only passes its input on, fed by another network.
         _node("relay", "subnet"),
         _node("ri1", "subnet_input", {"port": 1}, "relay"),
         _node("ro", "subnet_output", {}, "relay"),
         _node("entry", "entry"),
         _node("rsi_c", "rsi", {"period": 21}),
         _node("top", "above", {"threshold": 75, "out": "@top"}),
         _node("exit", "exit")],
        [("w1", "t", "outer", "in0"),
         ("w2", "oi0", "inner", "in0"),
         ("w3", "ii0", "rsi_a", "in0"),
         ("w4", "rsi_a", "lo", "in0"),
         ("w5", "lo", "io", "in0"),
         ("w6", "oi0", "rsi_b", "in0"),
         ("w7", "rsi_b", "hi", "in0"),
         ("w8", "inner", "either", "in0"),
         ("w9", "hi", "either", "in1"),
         ("w10", "either", "oo", "in0"),
         ("w11", "outer", "relay", "in1"),
         ("w12", "ri1", "ro", "in0"),
         ("w13", "relay", "entry", "in0"),
         ("w14", "t", "rsi_c", "in0"),
         ("w15", "rsi_c", "top", "in0"),
         ("w16", "top", "exit", "in0")],
    )
    hand = _graph(
        [_node("t", "ticker"),
         _node("rsi_a", "rsi", {"period": 14}),
         _node("lo", "below", {"threshold": 40, "out": "@lo"}),
         _node("rsi_b", "rsi", {"period": 7}),
         _node("hi", "above", {"threshold": 60, "out": "@hi"}),
         _node("either", "or", {"out": "@either"}),
         _node("entry", "entry"),
         _node("rsi_c", "rsi", {"period": 21}),
         _node("top", "above", {"threshold": 75, "out": "@top"}),
         _node("exit", "exit")],
        [("w3", "t", "rsi_a", "in0"),
         ("w4", "rsi_a", "lo", "in0"),
         ("w6", "t", "rsi_b", "in0"),
         ("w7", "rsi_b", "hi", "in0"),
         ("w8", "lo", "either", "in0"),
         ("w9", "hi", "either", "in1"),
         ("w13", "either", "entry", "in0"),
         ("w14", "t", "rsi_c", "in0"),
         ("w15", "rsi_c", "top", "in0"),
         ("w16", "top", "exit", "in0")],
    )
    return nested, hand


def test_nested_twice_compiles_like_hand_flattened(df):
    nested, hand = _two_levels()
    _same_program(nested, hand)
    flat = flatten(nested)
    p_flat, p_hand = check_graph(flat.graph).program, check_graph(hand).program
    assert p_flat is not None and p_hand is not None
    e1, x1 = cook_signals(p_flat, df)
    e2, x2 = cook_signals(p_hand, df)
    assert np.array_equal(e1, e2) and np.array_equal(x1, x2)
    assert e1.any() and x1.any()


def test_ancestors_nearest_first():
    nested, _hand = _two_levels()
    flat = flatten(nested)
    assert flat.ancestors("rsi_a") == ("inner", "outer")
    assert flat.ancestors("rsi_b") == ("outer",)
    assert flat.ancestors("entry") == ()


# ---------------------------------------------------------------------------
# Bypass, unwired ports, the no-network fast path
# ---------------------------------------------------------------------------


def test_bypassed_subnet_passes_in0_through():
    nested = _graph(
        [_node("t", "ticker"),
         _node("rsi", "rsi", {"period": 14}),
         _node("net", "subnet", bypass=True),
         _node("in0", "subnet_input", {"port": 0}, "net"),
         _node("ema", "ema", {"period": 5}, "net"),
         _node("out", "subnet_output", {}, "net"),
         _node("lo", "below", {"threshold": 30, "out": "@lo"}),
         _node("entry", "entry")],
        [("w1", "t", "rsi", "in0"), ("w2", "rsi", "net", "in0"),
         ("w3", "in0", "ema", "in0"), ("w4", "ema", "out", "in0"),
         ("w5", "net", "lo", "in0"), ("w6", "lo", "entry", "in0")],
    )
    flat = flatten(nested)
    assert ("w5", "rsi", "lo") in [(w.id, w.from_path, w.to_path) for w in flat.graph.wires]
    a, _ = analyze_graph(nested)
    assert a.nodes["lo"].reads["a"].name == "@rsi"


def test_unwired_network_port_leaves_the_consumer_without_input():
    nested = _graph(
        [_node("net", "subnet"),
         _node("in0", "subnet_input", {"port": 0}, "net"),
         _node("rsi", "rsi", {"period": 14}, "net"),
         _node("entry", "entry")],
        [("w1", "in0", "rsi", "in0")],
    )
    flat = flatten(nested)
    assert flat.graph.wires == []
    assert flat.found == []
    a, _ = analyze_graph(nested)
    assert a.has_errors()  # the RSI gets nothing to read, as an unwired RSI would


def test_graph_without_networks_is_returned_as_is():
    _nested, hand = _one_level()
    flat = flatten(hand)
    assert flat.graph is hand
    assert flat.found == [] and not flat.nested
    a, _ = analyze_graph(hand)
    b = analyze(hand)
    assert build_steps(a) == build_steps(b)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def _crossing():
    nodes = [_node("t", "ticker"),
             _node("net", "subnet"),
             _node("rsi", "rsi", {"period": 14}, "net"),
             _node("entry", "entry")]
    wires = [("w1", "t", "rsi", "in0"), ("w2", "rsi", "entry", "in0")]
    return nodes, wires


def test_wire_across_networks_is_refused_on_load():
    nodes, wires = _crossing()
    with pytest.raises(WireCrossesNetworkError) as exc:
        _graph(nodes, wires)
    assert exc.value.code == "wire_crosses_network"
    assert exc.value.node_id == "rsi" and exc.value.port == "in0"


def test_network_wire_issues_lists_every_crossing_wire():
    nodes, wires = _crossing()
    g = _unchecked(nodes, wires)
    issues = network_wire_issues(g.nodes, g.wires)
    assert [(i["wire_id"], i["node_id"], i["port"]) for i in issues] == [
        ("w1", "rsi", "in0"), ("w2", "entry", "in0")]
    assert all(i["code"] == "wire_crosses_network" for i in issues)


def test_flatten_reports_and_drops_crossing_wires():
    nodes, wires = _crossing()
    flat = flatten(_unchecked(nodes, wires))
    assert _codes(flat.found) == [("wire_crosses_network", "rsi"),
                                  ("wire_crosses_network", "entry")]
    assert flat.graph.wires == []


def test_validate_lists_crossing_wire():
    nodes, wires = _crossing()
    result = validate_graph_full(_data(nodes, wires))
    codes = {(d.code, d.node_id) for d in result.diagnostics}
    assert ("wire_crosses_network", "rsi") in codes


def test_validate_lists_each_crossing_wire_once():
    # The Graph model's load error and flatten inside compile both report
    # the first crossing wire; /validate lists it once (5.0 Needs 6).
    nodes, wires = _crossing()
    result = validate_graph_full(_data(nodes, wires))
    crossing = [(d.node_id, d.port, d.severity) for d in result.diagnostics
                if d.code == "wire_crosses_network"]
    assert crossing == [("rsi", "in0", "error"), ("entry", "in0", "error")]


def test_flatten_is_idempotent():
    nested, _hand = _one_level()
    flat = flatten(nested)
    assert all(n.parent is None for n in flat.graph.nodes.values())
    again = flatten(flat.graph)
    assert again.graph is flat.graph and not again.found


def test_composite_ids_are_reserved():
    with pytest.raises(ReservedNodeIdError) as exc:
        _graph([{**_node("a::b", "ticker"), "name": "ab"}], [])
    assert exc.value.code == "graph_invalid"
    assert composite_id("inst", "rsi") == "inst::rsi"
    assert source_of_flat_id("inst::rsi") == "inst"
    assert source_of_flat_id("inst::sub::rsi") == "inst"
    assert source_of_flat_id("rsi") == "rsi"


def test_boundary_node_outside_a_network():
    g = _graph([_node("in0", "subnet_input", {"port": 0}), _node("entry", "entry")], [])
    assert ("boundary_invalid", "in0") in _codes(flatten(g).found)


def test_network_with_two_outputs():
    g = _graph([_node("net", "subnet"),
                _node("o1", "subnet_output", {}, "net"),
                _node("o2", "subnet_output", {}, "net")], [])
    assert _codes(flatten(g).found) == [("boundary_invalid", "o2")]


def test_two_inputs_on_one_port():
    g = _graph([_node("net", "subnet"),
                _node("a", "subnet_input", {"port": 0}, "net"),
                _node("b", "subnet_input", {"port": 0}, "net")], [])
    found = flatten(g).found
    assert _codes(found) == [("boundary_invalid", "b")]
    assert found[0][0].param == "port"


def test_bad_port_param():
    g = _graph([_node("net", "subnet"),
                _node("a", "subnet_input", {"port": -1}, "net")], [])
    assert _codes(flatten(g).found) == [("param_invalid", "a")]


def test_wire_into_a_missing_network_port():
    g = _graph([_node("t", "ticker"), _node("net", "subnet"),
                _node("in0", "subnet_input", {"port": 0}, "net")],
               [("w1", "t", "net", "in3")])
    found = flatten(g).found
    assert _codes(found) == [("port_unknown", "net")]
    assert found[0][0].port == "in3"


def test_wire_out_of_a_network_without_output():
    g = _graph([_node("t", "ticker"), _node("net", "subnet"), _node("entry", "entry")],
               [("w1", "net", "entry", "in0")])
    assert ("boundary_invalid", "net") in _codes(flatten(g).found)


def test_wire_into_a_network_input_or_out_of_an_output():
    g = _graph([_node("net", "subnet"),
                _node("rsi", "rsi", {}, "net"),
                _node("in0", "subnet_input", {"port": 0}, "net"),
                _node("out", "subnet_output", {}, "net"),
                _node("rsi2", "rsi", {}, "net")],
               [("w1", "rsi", "in0", "in0"), ("w2", "out", "rsi2", "in0")])
    assert _codes(flatten(g).found) == [("boundary_invalid", "in0"),
                                        ("boundary_invalid", "out")]


def test_child_of_a_node_that_is_not_a_network():
    g = _graph([_node("t", "ticker"), _node("rsi", "rsi", {}, "t")], [])
    assert _codes(flatten(g).found) == [("boundary_invalid", "rsi")]


def test_diagnostics_inside_a_network_point_at_the_inner_node():
    nested = _graph(
        [_node("t", "ticker"),
         _node("net", "subnet"),
         _node("in0", "subnet_input", {"port": 0}, "net"),
         _node("rsi", "rsi", {"period": 1}, "net"),
         _node("out", "subnet_output", {}, "net"),
         _node("entry", "entry")],
        [("w1", "t", "net", "in0"), ("w2", "in0", "rsi", "in0"),
         ("w3", "rsi", "out", "in0"), ("w4", "net", "entry", "in0")],
    )
    a, _ = analyze_graph(nested)
    assert ("rsi" in {d.node_id for d, _e in a.found})


def test_remap_turns_composite_ids_into_the_instance():
    from nodebuilder.diagnostics import make
    from nodebuilder.models import GraphValidationError

    nested, _hand = _one_level()
    flat = flatten(nested)
    exc = GraphValidationError("x", node_id="inst::rsi")
    (d, e), = flat.remap([(make("param_invalid", "x", node_id="inst::rsi"), exc)])
    assert d.node_id == "inst" and e.node_id == "inst"


def test_unflattened_network_is_refused_by_the_node_check():
    nested, _hand = _one_level()
    a = analyze(nested)  # skipping flatten on purpose
    assert ("boundary_invalid", "net") in _codes(a.found)


def test_network_types_are_registered_with_their_meta():
    assert registry.get("subnet").meta.get("network") is True
    assert registry.get("subnet_input").meta.get("boundary") == "input"
    assert registry.get("subnet_output").meta.get("boundary") == "output"
    assert registry.get("subnet_input").param("port").default == 0

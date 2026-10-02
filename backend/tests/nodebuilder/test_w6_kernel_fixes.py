"""Wave 6 review fixes in the kernel (F435 W6, fixer B1).

BS-01  a bot snapshot carries each promoted param's value in its target, so a
       reader that ignores ``promoted`` (Wave 5 after a rollback) computes the
       same strategy.
KA-1   a bypassed (or switched-off) asset instance skips the interface check.
KA-2   an unlocked copy saves as a new asset version (plain_network_ids).
KA-3   a promoted param with no value and default None leaves the target alone.
KA-4   a network cannot promote into a locked instance's hidden children.
KA-5   messages and the family-cap node name what the user sees.
KA-6   wire ids with "::" are reserved like node ids.
KA-7   bypass rescues an instance whose asset is missing.
KA-8   nesting depth is capped; baked ids map to the node the editor has.
KA-9   one value check for promoted values and library defaults.
"""
from __future__ import annotations

import copy

import numpy as np
import pytest

from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.compile import check_graph
from nodebuilder.diagnostics import validate_graph_full
from nodebuilder.evaluator import _INDICATOR_FAMILY_CAP
from nodebuilder.kernel.assets import (
    MAX_ASSET_DEPTH,
    bake_assets,
    expand_assets,
    plain_network_ids,
)
from nodebuilder.kernel.flatten import editor_id, flatten, value_fits
from nodebuilder.models import Graph, ReservedWireIdError
from tests.nodebuilder.test_asset_instances import (
    Library,
    _data,
    _instance,
    _n,
    _outer_asset,
    _signals,
    _w,
    hand_built,
    regime_filter,
    with_instance,
)


def _codes(result) -> list[tuple]:
    return [(d.code, d.node_id, d.severity) for d in result.diagnostics]


def _errors(result) -> list[tuple]:
    return [(d.code, d.node_id, d.message) for d in result.diagnostics if d.severity == "error"]


# ---------------------------------------------------------------------------
# BS-01: effective promoted values in a bot snapshot
# ---------------------------------------------------------------------------


def _drop_promoted(graph: Graph) -> Graph:
    """What a Wave 5 reader sees: the Node model ignores ``promoted``."""
    data = graph.model_dump(by_alias=True)
    for node in data["nodes"].values():
        node.pop("promoted", None)
    return Graph.model_validate(data)


def plain_promoted(lookback=20, child_period=50) -> Graph:
    """A plain subnet (no asset) that promotes its MA period as lookback."""
    net = _n("net", "subnet", {"lookback": lookback} if lookback is not None else {},
             promoted=[{"name": "lookback", "target": "sma/period", "type": "int",
                        "default": 30}])
    return Graph.model_validate(_data(
        [_n("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
         net,
         _n("in0", "subnet_input", {"port": 0}, "net"),
         _n("sma", "sma", {"period": child_period, "out": "@rf_ma"}, "net"),
         _n("on", "above", {"a": "@close", "b": "@rf_ma", "out": "@regime_on"}, "net"),
         _n("out", "subnet_output", None, "net"),
         _n("off", "below", {"a": "@close", "b": "@rf_ma", "out": "@regime_off"}),
         _n("entry", "entry", {"signal": "@regime_on"}),
         _n("exit", "exit", {"signal": "@regime_off"})],
        [_w("w1", "t", "net"), _w("w2", "in0", "sma"), _w("w3", "sma", "on"),
         _w("w4", "on", "out"), _w("w5", "net", "entry"), _w("w6", "net", "off"),
         _w("w7", "off", "exit")],
    ))


def _same_signals(a: Graph, b: Graph, resolve=None) -> None:
    _p1, e1, x1 = _signals(a, resolve=resolve)
    _p2, e2, x2 = _signals(b, resolve=resolve)
    assert np.array_equal(e1, e2) and np.array_equal(x1, x2)
    assert e1.any()


@pytest.mark.parametrize("lookback", [20, None])
def test_bake_writes_promoted_values_of_a_plain_subnet(lookback):
    graph = plain_promoted(lookback=lookback)
    baked = bake_assets(graph, Library())
    want = 20 if lookback is not None else 30  # the value, else the default
    assert baked.nodes["sma"].params["period"] == want
    assert graph.nodes["sma"].params["period"] == 50  # the input is unchanged
    # A reader that drops `promoted` computes what Wave 6 computes.
    _same_signals(_drop_promoted(baked), graph)
    _same_signals(baked, graph)
    # Baking again changes nothing (flatten puts the same value in).
    assert bake_assets(baked, Library()) is baked


def test_bake_writes_promoted_values_through_an_asset_and_a_nested_chain():
    lib = Library()
    lib.add(regime_filter())
    lib.add(_outer_asset())
    graph = with_instance(lookback=20)
    baked = bake_assets(graph, lib)
    assert baked.nodes["inst::sma"].params["period"] == 20  # asset default is 50
    stripped = _drop_promoted(baked)
    _same_signals(stripped, hand_built(20))
    # Nested: outer's lb (25) -> rf's lookback -> rf's sma period.
    nested = Graph.model_validate(_data(
        [_n("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
         _instance("inst", lookback=None, name="outer"),
         _n("entry", "entry", {"signal": "@regime_on"})],
        [_w("w1", "t", "inst"), _w("w2", "inst", "entry")],
    ))
    nested.nodes["inst"].params["lb"] = 25
    baked = bake_assets(nested, lib)
    assert baked.nodes["inst::rf"].params["lookback"] == 25
    assert baked.nodes["inst::rf::sma"].params["period"] == 25
    _p_w5, e1, _x = _signals(_drop_promoted(baked), resolve=Library())
    _p, e2, _x2 = _signals(hand_built(25))
    assert np.array_equal(e1, e2)


def test_bake_leaves_an_invalid_promoted_value_alone():
    graph = plain_promoted(lookback="slow")
    baked = bake_assets(graph, Library())
    assert baked is graph  # nothing valid to write; compile reports it
    assert any(d.code == "promoted_invalid" for d in check_graph(baked).diagnostics)


# ---------------------------------------------------------------------------
# KA-1 / KA-7: switched-off instances
# ---------------------------------------------------------------------------


def _through(middle_nodes, middle_in, middle_out, extra_wires=()) -> Graph:
    """ticker -> (middle) -> sma2 -> above -> entry; below -> exit."""
    return Graph.model_validate(_data(
        [_n("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
         *middle_nodes,
         _n("sma2", "sma", {"period": 20, "out": "@m2"}),
         _n("up", "above", {"a": "@close", "b": "@m2", "out": "@sig"}),
         _n("dn", "below", {"a": "@close", "b": "@m2", "out": "@off"}),
         _n("entry", "entry", {"signal": "@sig"}),
         _n("exit", "exit", {"signal": "@off"})],
        [*([_w("wa", "t", middle_in), _w("wb", middle_out, "sma2")] if middle_in else
           [_w("wa", "t", "sma2")]),
         *extra_wires,
         _w("wc", "sma2", "up"), _w("wd", "up", "entry"), _w("we", "up", "dn"),
         _w("wf", "dn", "exit")],
    ))


def _bypassed_instance() -> Graph:
    return _through([{**_instance(), "bypass": True}], "inst", "inst")


def _instance_in_bypassed_net() -> Graph:
    return _through(
        [_n("net", "subnet", None, None, bypass=True),
         _n("i", "subnet_input", {"port": 0}, "net"),
         _instance(parent="net"),
         _n("o", "subnet_output", None, "net")],
        "net", "net",
        extra_wires=[_w("x1", "i", "inst"), _w("x2", "inst", "o")],
    )


@pytest.mark.parametrize("build", [_bypassed_instance, _instance_in_bypassed_net])
def test_a_switched_off_instance_skips_the_interface_check(build):
    lib = Library()
    lib.add(regime_filter())  # declares writes @regime_on
    graph = build()
    result = check_graph(graph, resolve=lib)
    assert result.program is not None, _errors(result)
    assert not [c for c in _codes(result) if c[0] == "interface_mismatch"]
    _same_signals(graph, _through([], None, None), resolve=lib)


def test_an_instance_whose_expansion_failed_gets_no_interface_noise():
    lib = Library()
    lib.add(regime_filter(name="a", extra_nodes=[_instance("loop", name="a")]))
    result = check_graph(with_instance(name="a"), resolve=lib)
    codes = {c for c, _n, _s in _codes(result)}
    assert "asset_cycle" in codes
    assert "interface_mismatch" not in codes
    assert "boundary_invalid" not in codes and "port_unknown" not in codes


@pytest.mark.parametrize("build", [_bypassed_instance, _instance_in_bypassed_net])
def test_bypass_rescues_an_instance_whose_asset_is_missing(build):
    graph = build()
    result = check_graph(graph, resolve=Library())  # the asset is gone
    assert result.program is not None, _errors(result)
    warned = [d for d in result.diagnostics if d.code == "asset_missing"]
    assert warned and warned[0].severity == "warning" and warned[0].node_id == "inst"
    _same_signals(graph, _through([], None, None), resolve=Library())
    # A bot snapshot of it bakes: the switched-off instance stays as it is.
    baked = bake_assets(graph, Library())
    assert baked.nodes["inst"].locked
    assert check_graph(baked, resolve=Library()).program is not None


def test_a_missing_asset_on_a_live_instance_is_still_an_error():
    result = check_graph(with_instance(), resolve=Library())
    assert ("asset_missing", "inst", "error") in _codes(result)
    assert result.program is None


# ---------------------------------------------------------------------------
# KA-2: an unlocked copy saves as a new asset version
# ---------------------------------------------------------------------------


def _network_of(graph: Graph, instance: str) -> dict:
    """What the client sends as the network of the subnet *instance* (its
    descendants; top-level children's parent set to null)."""
    data = graph.model_dump(by_alias=True)
    inside: set[str] = set()
    changed = True
    while changed:
        changed = False
        for nid, n in data["nodes"].items():
            if nid not in inside and (n["parent"] == instance or n["parent"] in inside):
                inside.add(nid)
                changed = True
    nodes = {nid: {**data["nodes"][nid]} for nid in inside}
    for n in nodes.values():
        if n["parent"] == instance:
            n["parent"] = None
    wires = [w for w in data["wires"] if w["from"] in inside and w["to"] in inside]
    return {"nodes": nodes, "wires": wires}


def _save_as_v2(lib: Library, asset: dict, network: dict) -> None:
    from nodebuilder.storage import canonical_network

    stored = canonical_network(plain_network_ids(network))
    lib.add({**copy.deepcopy(asset), "version": 2, "network": stored})


def test_an_unlocked_copy_saves_as_a_new_version_with_plain_ids():
    lib = Library()
    lib.add(regime_filter())
    baked = bake_assets(with_instance(lookback=20), lib)
    network = _network_of(baked, "inst")
    assert any("::" in k for k in network["nodes"])
    plain = plain_network_ids(network)
    assert set(plain["nodes"]) == {"in0", "sma", "on", "out"}
    assert {w["id"] for w in plain["wires"]} == {"w1", "w2", "w3"}
    assert all("::" not in w["from"] + w["to"] for w in plain["wires"])
    _save_as_v2(lib, regime_filter(), network)
    v2 = with_instance(lookback=20, version=2)
    _same_signals(v2, with_instance(lookback=20), resolve=lib)


def test_a_nested_unlocked_copy_keeps_its_own_composite_ids():
    lib = Library()
    lib.add(regime_filter())
    lib.add(_outer_asset())
    graph = Graph.model_validate(_data(
        [_n("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
         _instance("inst", lookback=None, name="outer"),
         _n("entry", "entry", {"signal": "@regime_on"})],
        [_w("w1", "t", "inst"), _w("w2", "inst", "entry")],
    ))
    baked = bake_assets(graph, lib)
    plain = plain_network_ids(_network_of(baked, "inst"))
    assert {"i", "rf", "o", "rf::sma", "rf::in0"} <= set(plain["nodes"])
    assert plain["nodes"]["rf::sma"]["parent"] == "rf"
    _save_as_v2(lib, _outer_asset(), _network_of(baked, "inst"))
    v2 = copy.deepcopy(graph)
    v2.nodes["inst"].asset_ref.version = 2
    _same_signals(v2, graph, resolve=lib)


def test_plain_network_ids_leaves_a_plain_network_alone():
    network = regime_filter()["network"]
    assert plain_network_ids(network) == network


# ---------------------------------------------------------------------------
# KA-3: default None
# ---------------------------------------------------------------------------


def test_a_promoted_param_with_no_value_and_default_none_keeps_the_target_value():
    lib = Library()
    asset = regime_filter()
    asset["promoted"][0]["default"] = None
    lib.add(asset)
    graph = with_instance()  # no lookback value
    result = check_graph(graph, resolve=lib)
    assert result.program is not None, _errors(result)
    expanded, _p = expand_assets(graph, lib)
    assert flatten(expanded).graph.nodes["inst::sma"].params["period"] == 50
    _same_signals(graph, hand_built(50), resolve=lib)
    # A value still overrides it.
    _same_signals(with_instance(lookback=10), hand_built(10), resolve=lib)


def test_default_none_on_a_plain_subnet_keeps_the_child_value():
    data = plain_promoted(lookback=None).model_dump(by_alias=True)
    data["nodes"]["net"]["promoted"][0]["default"] = None
    graph = Graph.model_validate(data)
    assert flatten(graph).graph.nodes["sma"].params["period"] == 50
    assert check_graph(graph).program is not None


# ---------------------------------------------------------------------------
# KA-4: no promoting into a locked instance's hidden children
# ---------------------------------------------------------------------------


def _net_around_instance(target: str) -> Graph:
    net = _n("net", "subnet", {"p": 15},
             promoted=[{"name": "p", "target": target, "type": "int", "default": 15}])
    return Graph.model_validate(_data(
        [_n("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
         net,
         _n("i", "subnet_input", {"port": 0}, "net"),
         _instance(parent="net"),
         _n("o", "subnet_output", None, "net"),
         _n("entry", "entry", {"signal": "@regime_on"})],
        [_w("w1", "t", "net"), _w("w2", "i", "inst"), _w("w3", "inst", "o"),
         _w("w4", "net", "entry")],
    ))


def test_a_network_cannot_promote_into_a_locked_instance():
    lib = Library()
    lib.add(regime_filter())
    result = check_graph(_net_around_instance("inst/sma/period"), resolve=lib)
    bad = [d for d in result.diagnostics if d.code == "promoted_invalid"]
    assert [(d.node_id, d.param) for d in bad] == [("net", "p")]
    assert "library asset" in bad[0].message
    # The instance's own promoted param can be promoted, and it works.
    graph = _net_around_instance("inst/lookback")
    result = check_graph(graph, resolve=lib)
    assert result.program is not None, _errors(result)
    _p, e1, _x = _signals(graph, resolve=lib)
    _p2, e2, _x2 = _signals(hand_built(15))
    assert np.array_equal(e1, e2)


# ---------------------------------------------------------------------------
# KA-5: names the user can find
# ---------------------------------------------------------------------------


def test_messages_about_hidden_nodes_name_them_by_name():
    lib = Library()
    broken = regime_filter()
    broken["network"]["wires"] = [w for w in broken["network"]["wires"] if w["id"] != "w1"]
    lib.add(broken)
    result = check_graph(with_instance(), resolve=lib)
    missing = [d for d in result.diagnostics if d.code == "missing_input"]
    assert missing and missing[0].node_id == "inst"
    assert "::" not in missing[0].message
    assert "'sma' (inside 'inst')" in missing[0].message
    assert all("::" not in d.message for d in result.diagnostics)
    assert all("::" not in str(e) for e in result.errors)


def test_the_family_cap_names_the_instance():
    lib = Library()
    n = _INDICATOR_FAMILY_CAP + 1
    extra = [_n(f"atr{k}", "atr", {"period": k + 2, "out": f"@atr{k}"}) for k in range(n)]
    asset = regime_filter(extra_nodes=extra)
    asset["network"]["wires"] += [_w(f"x{k}", "in0", f"atr{k}") for k in range(n)]
    lib.add(asset)
    result = check_graph(with_instance(), resolve=lib)
    cap = [d for d in result.diagnostics if d.code == "family_cap"]
    assert cap, _codes(result)
    assert cap[0].node_id == "inst"


# ---------------------------------------------------------------------------
# KA-6: reserved wire ids
# ---------------------------------------------------------------------------


def test_a_user_wire_may_not_take_an_expanded_asset_wire_id():
    data = with_instance().model_dump(by_alias=True)
    data["wires"][0]["id"] = "inst::w1"  # t -> inst, at the root
    with pytest.raises(ReservedWireIdError):
        Graph.model_validate(data)
    diags = validate_graph_full(data).diagnostics
    assert any(d.code == "graph_invalid" and "Wire id" in d.message for d in diags)
    # Under a plain node prefix too.
    data["wires"][0]["id"] = "t::w1"
    with pytest.raises(ReservedWireIdError):
        Graph.model_validate(data)


def test_wires_inside_an_unlocked_copy_keep_their_composite_ids():
    lib = Library()
    lib.add(regime_filter())
    lib.add(_outer_asset())
    graph = Graph.model_validate(_data(
        [_n("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
         _instance("inst", lookback=None, name="outer"),
         _n("entry", "entry", {"signal": "@regime_on"})],
        [_w("w1", "t", "inst"), _w("w2", "inst", "entry")],
    ))
    baked = bake_assets(graph, lib)
    assert {"inst::a", "inst::rf::w1"} <= {w.id for w in baked.wires}
    again = Graph.model_validate(baked.model_dump(by_alias=True))
    assert again.model_dump(by_alias=True) == baked.model_dump(by_alias=True)


# ---------------------------------------------------------------------------
# KA-8: depth cap and editor ids
# ---------------------------------------------------------------------------


def test_deep_asset_nesting_stops_with_a_diagnostic():
    lib = Library()
    lib.add(regime_filter(name="deep", version=1))
    depth = MAX_ASSET_DEPTH + 5
    for v in range(2, depth + 1):
        lib.add(regime_filter(name="deep", version=v,
                              extra_nodes=[_instance("prev", name="deep", version=v - 1)]))
    graph = with_instance(name="deep", version=depth)
    _expanded, problems = expand_assets(graph, lib)
    assert [p.code for p in problems] == ["graph_invalid"]
    assert problems[0].node_id == "inst" and "deep" in str(problems[0])
    result = check_graph(graph, resolve=lib)
    assert ("graph_invalid", "inst", "error") in _codes(result)


def test_editor_id_maps_to_the_node_the_editor_has():
    nodes = {"U": 1, "U::L": 1, "U::a": 1, "plain": 1}
    assert editor_id(nodes, "U::L::sma") == "U::L"
    assert editor_id(nodes, "U::L::rf::sma") == "U::L"
    assert editor_id(nodes, "U::a") == "U::a"
    assert editor_id(nodes, "plain") == "plain"
    assert editor_id(nodes, "inst::sma") == "inst"
    assert editor_id(nodes, None) is None


# ---------------------------------------------------------------------------
# KA-9: one value check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind,value,ok", [
    ("int", None, True), ("int", 5, True), ("int", 5.0, True), ("int", 5.5, False),
    ("int", True, False), ("number", 2.5, True), ("bool", 1, False),
    ("select", "sma", True), ("select", ["a", "b"], True), ("select", [1], False),
    ("time_range", {"start": "09:30", "end": "16:00"}, True),
    ("attr_list", ["@a"], True), ("string", 3, False),
])
def test_value_fits_is_the_shared_rule(kind, value, ok):
    assert value_fits(kind, value) is ok

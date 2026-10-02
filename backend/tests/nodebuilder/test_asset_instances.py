"""Library asset instances (F435 W6 item 6.A, kernel/assets.py).

A locked instance is a subnet with asset_ref and locked: true and no stored
children.  Compile fills its children in from the library with composite
ids (``inst::sma``), so it must compute exactly what the same nodes drawn
by hand compute.  An instance pins its version, a missing or looping asset
is a diagnostic (never a crash), and a baked graph (a bot snapshot) no
longer needs the library.

Every test uses an in-memory library (6.B builds the real one); none
touches the data folder.
"""
from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest

from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.compile import check_graph, compile as compile_graph
from nodebuilder.diagnostics import validate_graph_full
from nodebuilder.evaluator import cook_signals
from nodebuilder.kernel import assets
from nodebuilder.kernel.assets import AssetError, bake_assets, expand_assets
from nodebuilder.kernel.flatten import flatten
from nodebuilder.models import (
    Graph,
    GraphValidationError,
    LockedInstanceChildError,
    ReservedNodeIdError,
)
from nodebuilder.run import run_graph_backtest

N = 300
START, END = "2023-01-02", "2024-02-23"


def _frame(seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = np.arange(N)
    close = 100 + 10 * np.sin(t / 15) + np.cumsum(rng.normal(0, 0.8, N))
    idx = pd.date_range("2023-01-02", periods=N, freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": close, "High": close + 0.5, "Low": close - 0.5,
                         "Close": close, "Volume": rng.integers(1e5, 1e6, N)}, index=idx)


DF = _frame()


# ---------------------------------------------------------------------------
# An in-memory library
# ---------------------------------------------------------------------------


class Library:
    """A Resolver over a dict; it counts lookups."""

    def __init__(self) -> None:
        self.files: dict[tuple[str, int], dict] = {}
        self.calls: list[tuple[str, int]] = []

    def add(self, asset: dict) -> dict:
        self.files[(asset["name"], asset["version"])] = asset
        return asset

    def __call__(self, name: str, version: int):
        self.calls.append((name, version))
        found = self.files.get((name, version))
        return copy.deepcopy(found) if found is not None else None


def _n(nid, typ, params=None, parent=None, **extra):
    return {"id": nid, "type": typ, "name": nid, "parent": parent, "params": params or {}, **extra}


def _w(wid, a, b, port="in0"):
    return {"id": wid, "from": a, "to": b, "to_port": port}


IFACE = {"reads": [{"name": "@close", "class": "point", "dtype": "float"}],
         "writes": [{"name": "@regime_on", "class": "point", "dtype": "bool"}]}


def regime_filter(version: int = 1, period: int = 50, ma: str = "sma", interface=None,
                  stream_schema: int = 1, extra_nodes=(), name: str = "regime_filter") -> dict:
    """"close above its moving average", with the MA period promoted as
    ``lookback``.  The network's top-level nodes have parent null."""
    nodes = [
        _n("in0", "subnet_input", {"port": 0}),
        _n("sma", ma, {"period": period, "out": "@rf_ma"}),
        _n("on", "above", {"a": "@close", "b": "@rf_ma", "out": "@regime_on"}),
        _n("out", "subnet_output"),
        *extra_nodes,
    ]
    return {
        "name": name, "version": version, "description": "close above its MA",
        "stream_schema": stream_schema,
        "interface": IFACE if interface is None else interface,
        "promoted": [{"name": "lookback", "label": "Lookback", "target": "sma/period",
                      "type": "int", "default": period}],
        "palette": {"category": "rules", "label": "Regime Filter", "glyph": "R"},
        "network": {"nodes": {n["id"]: n for n in nodes},
                    "wires": [_w("w1", "in0", "sma"), _w("w2", "sma", "on"), _w("w3", "on", "out")]},
        "created_at": "2026-10-02T09:00:00Z",
    }


@pytest.fixture
def library(monkeypatch) -> Library:
    """A fresh library, registered as the default resolver for this test."""
    lib = Library()
    monkeypatch.setattr(assets, "_default_resolver", lib)
    return lib


@pytest.fixture
def no_library(monkeypatch):
    monkeypatch.setattr(assets, "_default_resolver", None)


# ---------------------------------------------------------------------------
# Graphs
# ---------------------------------------------------------------------------


def _instance(nid="inst", lookback=None, version=1, name="regime_filter", parent=None,
              locked=True, node_name=None):
    params = {} if lookback is None else {"lookback": lookback}
    node = _n(nid, "subnet", params, parent, asset_ref={"name": name, "version": version},
              locked=locked)
    if node_name:
        node["name"] = node_name
    return node


def _data(nodes, wires) -> dict:
    return {"_version": 3, "nodes": {n["id"]: n for n in nodes}, "wires": wires}


def with_instance(lookback=None, version=1, name="regime_filter") -> Graph:
    """Ticker -> regime_filter instance -> Entry; close below the MA -> Exit."""
    return Graph.model_validate(_data(
        [_n("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
         _instance(lookback=lookback, version=version, name=name),
         _n("off", "below", {"a": "@close", "b": "@rf_ma", "out": "@regime_off"}),
         _n("entry", "entry", {"signal": "@regime_on"}),
         _n("exit", "exit", {"signal": "@regime_off"})],
        [_w("w1", "t", "inst"), _w("w2", "inst", "entry"), _w("w3", "inst", "off"),
         _w("w4", "off", "exit")],
    ))


def hand_built(period: int, ma: str = "sma") -> Graph:
    """The same strategy drawn flat by hand."""
    return Graph.model_validate(_data(
        [_n("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
         _n("sma", ma, {"period": period, "out": "@rf_ma"}),
         _n("on", "above", {"a": "@close", "b": "@rf_ma", "out": "@regime_on"}),
         _n("off", "below", {"a": "@close", "b": "@rf_ma", "out": "@regime_off"}),
         _n("entry", "entry", {"signal": "@regime_on"}),
         _n("exit", "exit", {"signal": "@regime_off"})],
        [_w("w1", "t", "sma"), _w("w2", "sma", "on"), _w("w3", "on", "entry"),
         _w("w4", "on", "off"), _w("w5", "off", "exit")],
    ))


def _signals(graph: Graph, resolve=None):
    program = compile_graph(graph, resolve=resolve)
    entries, exits = cook_signals(program, DF)
    return program, entries, exits


def _backtest(graph: Graph):
    req = GraphBacktestRequest(graph=graph, ticker="AAPL", start=START, end=END, interval="1d",
                               source="yahoo", initial_capital=10000.0)
    return run_graph_backtest(req, df=DF)


def _shape(program) -> list[tuple]:
    """The steps without their node ids (composite ids differ from hand ids)."""
    return sorted((s.type, s.mode, repr(s.params), s.reads, s.writes) for s in program.steps)


def _codes(graph, resolve=None) -> list[tuple]:
    return [(d.code, d.node_id) for d in check_graph(graph, resolve=resolve).diagnostics]


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------


def test_locked_instance_expands_with_composite_ids():
    lib = Library()
    lib.add(regime_filter())
    graph = with_instance(lookback=30)
    expanded, problems = expand_assets(graph, lib)
    assert problems == []
    assert graph.nodes.keys() == {"t", "inst", "off", "entry", "exit"}  # the input is unchanged
    inside = {k: n for k, n in expanded.nodes.items() if k.startswith("inst::")}
    assert set(inside) == {"inst::in0", "inst::sma", "inst::on", "inst::out"}
    assert all(n.parent == "inst" for n in inside.values())
    assert inside["inst::sma"].name == "sma" and inside["inst::sma"].params["period"] == 50
    assert {w.id for w in expanded.wires} >= {"inst::w1", "inst::w2", "inst::w3"}
    w2 = next(w for w in expanded.wires if w.id == "inst::w2")
    assert (w2.from_path, w2.to_path) == ("inst::sma", "inst::on")
    inst = expanded.nodes["inst"]
    assert inst.locked and inst.asset_ref.name == "regime_filter"
    assert [p.name for p in inst.promoted] == ["lookback"]  # from the asset

    flat = flatten(expanded)
    assert set(flat.graph.nodes) == {"t", "inst::sma", "inst::on", "off", "entry", "exit"}
    # The children are not stored, so the user sees the instance.
    assert flat.to_source("inst::sma") == "inst"
    assert flat.flat_to_source["inst::on"] == "inst"
    assert flat.flat_to_source["t"] == "t"
    # The promoted value went into the target.
    assert flat.graph.nodes["inst::sma"].params["period"] == 30
    assert flat.promoted_from[("inst::sma", "period")] == ("inst", "lookback")


def test_graph_without_instances_is_returned_as_is():
    graph = hand_built(50)
    expanded, problems = expand_assets(graph, Library())
    assert expanded is graph and problems == []
    assert bake_assets(graph, Library()) is graph


def test_locked_instance_equals_the_hand_built_graph(library):
    library.add(regime_filter())
    p_inst, e1, x1 = _signals(with_instance())
    p_hand, e2, x2 = _signals(hand_built(50))
    assert _shape(p_inst) == _shape(p_hand)
    assert (p_inst.entry_attr, p_inst.exit_attr) == (p_hand.entry_attr, p_hand.exit_attr)
    assert p_inst.required_lookback_bars == p_hand.required_lookback_bars
    assert np.array_equal(e1, e2) and np.array_equal(x1, x2)
    assert e1.any() and x1.any()
    a, b = _backtest(with_instance()), _backtest(hand_built(50))
    assert a.summary == b.summary
    assert a.trades == b.trades and len(a.trades) > 0


def test_a_promoted_override_changes_the_backtest(library):
    library.add(regime_filter())
    default = _backtest(with_instance())
    fast = _backtest(with_instance(lookback=10))
    assert fast.trades != default.trades
    assert fast.trades == _backtest(hand_built(10)).trades
    assert fast.summary == _backtest(hand_built(10)).summary


def test_one_asset_in_three_graphs_with_three_lookbacks(library):
    """The T3 exit criterion, in synthetic form: one asset saved once, placed
    in three graphs with its lookback promoted to three values, and each
    backtests like its hand-built twin."""
    library.add(regime_filter())
    results = []
    for lookback in (10, 30, 60):
        got = _backtest(with_instance(lookback=lookback))
        want = _backtest(hand_built(lookback))
        assert got.summary == want.summary and got.trades == want.trades
        results.append(json.dumps(got.summary, sort_keys=True, default=str))
    assert len(set(results)) == 3  # three different results


def test_an_instance_pins_its_version(library):
    library.add(regime_filter(version=1, period=50))
    before = _signals(with_instance(version=1))
    # A newer version (another MA type and default) does not change v1 users.
    library.add(regime_filter(version=2, period=20, ma="ema"))
    after = _signals(with_instance(version=1))
    assert _shape(before[0]) == _shape(after[0])
    assert np.array_equal(before[1], after[1]) and np.array_equal(before[2], after[2])
    v2 = _signals(with_instance(version=2))
    assert _shape(v2[0]) == _shape(_signals(hand_built(20, ma="ema"))[0])
    assert not np.array_equal(before[1], v2[1])


def test_each_asset_version_is_looked_up_once_per_compile(library):
    library.add(regime_filter())
    check_graph(with_instance())
    assert library.calls == [("regime_filter", 1)]


# ---------------------------------------------------------------------------
# Problems: missing, unreadable, looping
# ---------------------------------------------------------------------------


def test_a_missing_asset_is_a_diagnostic_not_a_crash(no_library):
    graph = with_instance()
    result = check_graph(graph)
    assert result.program is None
    assert ("asset_missing", "inst") in [(d.code, d.node_id) for d in result.diagnostics]
    with pytest.raises(GraphValidationError) as exc:
        compile_graph(graph)
    assert exc.value.code == "asset_missing" and exc.value.node_id == "inst"
    # /validate lists it with the instance's path.
    diags = validate_graph_full(graph.model_dump(by_alias=True)).diagnostics
    missing = [d for d in diags if d.code == "asset_missing"]
    assert missing and missing[0].node_id == "inst" and missing[0].path == "/inst"
    assert missing[0].severity == "error"


def test_a_missing_asset_gives_one_error_even_with_promoted_params_stored(no_library):
    data = with_instance(lookback=20).model_dump(by_alias=True)
    data["nodes"]["inst"]["promoted"] = regime_filter()["promoted"]  # a stored copy
    codes = {c for c, _n in _codes(Graph.model_validate(data))}
    assert "asset_missing" in codes and "promoted_invalid" not in codes


def test_a_deleted_version_is_missing(library):
    library.add(regime_filter(version=1))
    assert _codes(with_instance(version=2))[0] == ("asset_missing", "inst")


def test_a_lookup_that_raises_is_missing():
    def broken(name, version):
        raise OSError("disk on fire")

    codes = _codes(with_instance(), resolve=broken)
    assert ("asset_missing", "inst") in codes


def test_an_unreadable_asset_is_missing():
    lib = Library()
    bad = regime_filter()
    bad["network"]["wires"].append(_w("w9", "on", "nowhere"))  # a dangling wire
    lib.add(bad)
    result = check_graph(with_instance(), resolve=lib)
    diag = next(d for d in result.diagnostics if d.code == "asset_missing")
    assert diag.node_id == "inst" and "could not be read" in diag.message


def test_an_asset_that_contains_itself_is_a_cycle():
    lib = Library()
    # a v1 holds a locked b v1, which holds a locked a v1.
    lib.add(regime_filter(name="a", extra_nodes=[_instance("loop", name="b")]))
    lib.add(regime_filter(name="b", extra_nodes=[_instance("loop", name="a")]))
    graph = with_instance(name="a")
    expanded, problems = expand_assets(graph, lib)
    assert [p.code for p in problems] == ["asset_cycle"]
    assert problems[0].node_id == "inst"  # the instance the user sees
    assert "a v1 -> b v1 -> a v1" in str(problems[0])
    codes = _codes(graph, resolve=lib)
    assert ("asset_cycle", "inst") in codes
    # A self-loop too.
    lib.add(regime_filter(name="selfish", extra_nodes=[_instance("me", name="selfish")]))
    assert ("asset_cycle", "inst") in _codes(with_instance(name="selfish"), resolve=lib)


def test_no_resolver_registered_means_missing(no_library):
    assert assets.default_resolver()("regime_filter", 1) is None
    assert ("asset_missing", "inst") in _codes(with_instance())


def test_set_default_resolver_is_what_compile_uses(monkeypatch):
    lib = Library()
    lib.add(regime_filter())
    monkeypatch.setattr(assets, "_default_resolver", None)
    assets.set_default_resolver(lib)
    try:
        assert assets.default_resolver() is lib
        assert check_graph(with_instance()).program is not None
    finally:
        assets.set_default_resolver(None)


# ---------------------------------------------------------------------------
# Diagnostics inside an instance land on the instance
# ---------------------------------------------------------------------------


def test_a_bad_promoted_value_shows_on_the_promoted_param(library):
    library.add(regime_filter())
    result = check_graph(with_instance(lookback=0))  # the MA period must be at least 1 or 2
    diag = next(d for d in result.diagnostics if d.severity == "error")
    assert diag.code == "param_out_of_range"
    assert (diag.node_id, diag.param) == ("inst", "lookback")


def test_a_promoted_value_of_the_wrong_type(library):
    library.add(regime_filter())
    result = check_graph(with_instance(lookback="slow"))
    diag = next(d for d in result.diagnostics if d.code == "promoted_invalid")
    assert (diag.node_id, diag.param) == ("inst", "lookback")


def test_an_error_in_the_asset_maps_to_the_instance():
    lib = Library()
    bad = regime_filter()
    bad["network"]["nodes"]["on"]["params"]["a"] = "@nope"  # reads a name nothing writes
    lib.add(bad)
    result = check_graph(with_instance(), resolve=lib)
    diag = next(d for d in result.diagnostics if d.code == "attr_missing")
    assert diag.node_id == "inst" and diag.param is None


# ---------------------------------------------------------------------------
# Declared interface
# ---------------------------------------------------------------------------


def test_a_matching_interface_is_fine(library):
    library.add(regime_filter())
    assert not [c for c in _codes(with_instance()) if c[0] == "interface_mismatch"]


@pytest.mark.parametrize("interface,needle", [
    ({"reads": [], "writes": [{"name": "@nope", "class": "point", "dtype": "bool"}]},
     "writes @nope"),
    ({"reads": [], "writes": [{"name": "@regime_on", "class": "point", "dtype": "float"}]},
     "@regime_on is float, but it is bool"),
    ({"reads": [], "writes": [{"name": "@regime_on", "class": "detail", "dtype": "bool"}]},
     "detail attribute"),
    ({"reads": [{"name": "@spread", "class": "point", "dtype": "float"}], "writes": []},
     "reads @spread"),
])
def test_an_interface_that_does_not_match(interface, needle):
    lib = Library()
    lib.add(regime_filter(interface=interface))
    result = check_graph(with_instance(), resolve=lib)
    mismatch = [d for d in result.diagnostics if d.code == "interface_mismatch"]
    assert len(mismatch) == 1 and mismatch[0].node_id == "inst"
    assert needle in mismatch[0].message
    assert result.program is None  # an error


def test_an_asset_for_another_stream_schema_is_a_mismatch():
    lib = Library()
    lib.add(regime_filter(stream_schema=99))
    msgs = [d.message for d in check_graph(with_instance(), resolve=lib).diagnostics
            if d.code == "interface_mismatch"]
    assert msgs and "stream schema 99" in msgs[0]


# ---------------------------------------------------------------------------
# Nested assets
# ---------------------------------------------------------------------------


def _outer_asset() -> dict:
    """An asset "outer" that holds a locked regime_filter v1 and promotes
    its lookback again as ``lb``."""
    nodes = [
        _n("i", "subnet_input", {"port": 0}),
        _instance("rf", lookback=None),
        _n("o", "subnet_output"),
    ]
    return {
        "name": "outer", "version": 1, "description": "", "stream_schema": 1,
        "interface": {"reads": [], "writes": [{"name": "@regime_on", "class": "point",
                                                "dtype": "bool"}]},
        "promoted": [{"name": "lb", "label": "LB", "target": "rf/lookback", "type": "int",
                      "default": 40}],
        "palette": None,
        "network": {"nodes": {n["id"]: n for n in nodes},
                    "wires": [_w("a", "i", "rf"), _w("b", "rf", "o")]},
        "created_at": "2026-10-02T09:00:00Z",
    }


def test_nested_assets_expand_and_promote_through_both_levels(library):
    library.add(regime_filter())
    library.add(_outer_asset())
    graph = Graph.model_validate(_data(
        [_n("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
         _instance("inst", lookback=None, name="outer"),
         _n("entry", "entry", {"signal": "@regime_on"})],
        [_w("w1", "t", "inst"), _w("w2", "inst", "entry")],
    ))
    graph.nodes["inst"].params["lb"] = 25
    expanded, problems = expand_assets(graph, library)
    assert problems == []
    assert expanded.nodes["inst::rf::sma"].parent == "inst::rf"
    assert expanded.nodes["inst::rf"].parent == "inst"
    flat = flatten(expanded)
    assert flat.graph.nodes["inst::rf::sma"].params["period"] == 25
    assert flat.to_source("inst::rf::sma") == "inst"
    assert flat.promoted_owner("inst::rf::sma", "period") == ("inst", "lb")
    p_nested, e1, _x = _signals(graph)
    hand = hand_built(25)
    p_hand, e2, _x2 = _signals(hand)
    assert np.array_equal(e1, e2)
    # A problem deep inside shows on the outermost instance.
    del library.files[("regime_filter", 1)]
    diag = next(d for d in check_graph(graph).diagnostics if d.code == "asset_missing")
    assert diag.node_id == "inst" and "regime_filter v1" in diag.message
    assert "used inside outer v1" in diag.message


def test_an_instance_inside_an_output_group(library):
    library.add(regime_filter())
    graph = Graph.model_validate(_data(
        [_n("g", "output_group", {"direction": "long", "ticker": "t"}),
         _n("t", "ticker", {"symbol": "AAPL", "interval": "1d"}, "g"),
         _instance(parent="g"),
         _n("entry", "entry", {"signal": "@regime_on"}, "g")],
        [_w("w1", "t", "inst"), _w("w2", "inst", "entry")],
    ))
    result = check_graph(graph)
    assert result.program is not None, [(d.code, d.message) for d in result.diagnostics]
    assert [g.name for g in result.program.groups] == ["g"]


# ---------------------------------------------------------------------------
# Baking (bot snapshots)
# ---------------------------------------------------------------------------


def test_bake_unlocks_and_stores_the_children(library):
    library.add(regime_filter())
    graph = with_instance(lookback=20)
    baked = bake_assets(graph, library)
    inst = baked.nodes["inst"]
    assert inst.locked is False
    assert (inst.asset_ref.name, inst.asset_ref.version) == ("regime_filter", 1)
    assert inst.params == {"lookback": 20}
    assert [p.target for p in inst.promoted] == ["sma/period"]
    assert {"inst::sma", "inst::on", "inst::in0", "inst::out"} <= set(baked.nodes)
    # It reloads as a stored graph would (bots.json, the graph store).
    again = Graph.model_validate(baked.model_dump(by_alias=True))
    assert again.model_dump(by_alias=True) == baked.model_dump(by_alias=True)
    # The input graph is unchanged.
    assert graph.nodes["inst"].locked and "inst::sma" not in graph.nodes


def test_a_baked_graph_compiles_like_the_locked_one_and_ignores_the_library(library):
    library.add(regime_filter())
    graph = with_instance(lookback=20)
    locked = check_graph(graph).program
    baked = bake_assets(graph, library)
    # The library changes: the version is deleted and a v2 appears.
    library.files.clear()
    library.add(regime_filter(version=2, period=5, ma="ema"))
    assert library.calls  # sanity: the locked compile used it
    library.calls.clear()
    program = check_graph(baked).program
    assert program is not None
    assert library.calls == []  # a baked graph never asks the library
    assert program.steps == locked.steps  # the same ids, params and order
    assert (program.entry_attr, program.exit_attr) == (locked.entry_attr, locked.exit_attr)
    a = _backtest(baked)
    assert a.trades == _backtest(hand_built(20)).trades


def test_bake_of_a_missing_asset_raises_asset_missing():
    with pytest.raises(AssetError) as exc:
        bake_assets(with_instance(), Library())
    assert exc.value.code == "asset_missing"
    assert exc.value.node_id == "inst"
    assert (exc.value.asset_name, exc.value.asset_version) == ("regime_filter", 1)
    assert isinstance(exc.value, GraphValidationError)


def test_bake_of_nested_assets_unlocks_every_level(library):
    library.add(regime_filter())
    library.add(_outer_asset())
    graph = Graph.model_validate(_data(
        [_n("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
         _instance("inst", name="outer"),
         _n("entry", "entry", {"signal": "@regime_on"})],
        [_w("w1", "t", "inst"), _w("w2", "inst", "entry")],
    ))
    baked = bake_assets(graph, library)
    assert not any(n.locked for n in baked.nodes.values())
    assert baked.nodes["inst::rf"].asset_ref.name == "regime_filter"
    assert "inst::rf::sma" in baked.nodes
    library.files.clear()
    assert check_graph(baked).program is not None


# ---------------------------------------------------------------------------
# Stored ids and locked children (models.py)
# ---------------------------------------------------------------------------


def test_composite_ids_are_still_reserved_outside_an_unlocked_instance():
    # Under a plain subnet (no asset_ref).
    with pytest.raises(ReservedNodeIdError):
        Graph.model_validate(_data([_n("net", "subnet"), {**_n("net::x", "rsi", None, "net"), "name": "x"}], []))
    # Under a locked instance (it may not store children at all).
    with pytest.raises((ReservedNodeIdError, LockedInstanceChildError)):
        Graph.model_validate(_data([_instance(), {**_n("inst::x", "rsi", None, "inst"), "name": "x"}], []))
    # Prefix names an instance the node is not inside.
    with pytest.raises(ReservedNodeIdError):
        Graph.model_validate(_data([_instance(locked=False), {**_n("inst::x", "rsi"), "name": "x"}], []))
    # Inside an unlocked instance: allowed (a baked or local copy).
    g = Graph.model_validate(_data([_instance(locked=False), {**_n("inst::x", "rsi", None, "inst"), "name": "x"}], []))
    assert "inst::x" in g.nodes


def test_a_locked_instance_may_not_store_children():
    with pytest.raises(LockedInstanceChildError) as exc:
        Graph.model_validate(_data([_instance(), _n("x", "rsi", None, "inst")], []))
    assert exc.value.node_id == "x" and exc.value.code == "graph_invalid"


def test_asset_fields_belong_on_a_subnet_only():
    with pytest.raises(Exception, match="Only a subnet"):
        Graph.model_validate(_data([_n("r", "rsi", None, None, locked=True,
                                       asset_ref={"name": "a", "version": 1})], []))
    with pytest.raises(Exception, match="needs asset_ref"):
        Graph.model_validate(_data([_n("s", "subnet", None, None, locked=True)], []))
    with pytest.raises(Exception):
        Graph.model_validate(_data([_instance(name="Bad-Name")], []))
    with pytest.raises(Exception):
        Graph.model_validate(_data([_instance(version=0)], []))
    # /validate lists the bad node instead of failing.
    diags = validate_graph_full(_data([_n("s", "subnet", None, None, locked=True)], [])).diagnostics
    assert ("graph_invalid", "s") in [(d.code, d.node_id) for d in diags]


def test_a_locked_instance_round_trips_through_save_and_load():
    graph = with_instance(lookback=12)
    dumped = graph.model_dump(by_alias=True)
    assert dumped["nodes"]["inst"]["asset_ref"] == {"name": "regime_filter", "version": 1}
    assert dumped["nodes"]["inst"]["locked"] is True
    assert "asset_ref" not in dumped["nodes"]["t"] and "locked" not in dumped["nodes"]["t"]
    assert Graph.model_validate(dumped).model_dump(by_alias=True) == dumped

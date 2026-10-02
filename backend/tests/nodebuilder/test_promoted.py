"""Promoted params and node meta (F435 W6 item 6.A).

A network node can promote params of the nodes inside it (S40).  The value
lives on the network (``params[name]``, else the promoted ``default``) and
kernel/flatten.py puts it into the target before compile, so a graph with a
promoted param computes what the same graph with the value typed into the
child computes.  A bad name, target or value is ``promoted_invalid``.
``rename_node`` keeps targets pointing at the same param (shared vectors in
vectors/paths.json, section rewrite_promoted).

``Node.meta`` is editor view state: kept through save, load, migrate and
copy, and never read by compile.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import pytest

from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.compile import check_graph
from nodebuilder.compile import compile as compile_graph
from nodebuilder.diagnostics import validate_graph_full
from nodebuilder.evaluator import cook_signals
from nodebuilder.kernel.flatten import flatten
from nodebuilder.migrate import rename_node
from nodebuilder.models import Graph

N = 300
_HERE = os.path.dirname(__file__)
with open(os.path.join(_HERE, "vectors", "paths.json")) as _fh:
    _PATHS = json.load(_fh)


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    rng = np.random.default_rng(11)
    t = np.arange(N)
    close = 100 + 8 * np.sin(t / 10) + np.cumsum(rng.normal(0, 0.7, N))
    idx = pd.date_range("2023-01-02", periods=N, freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": close, "High": close + 0.5, "Low": close - 0.5,
                         "Close": close, "Volume": rng.integers(1e5, 1e6, N)}, index=idx)


def _n(nid, typ, params=None, parent=None, **extra):
    return {"id": nid, "type": typ, "name": nid, "parent": parent, "params": params or {}, **extra}


def _w(wid, a, b, port="in0"):
    return {"id": wid, "from": a, "to": b, "to_port": port}


def _data(nodes, wires) -> dict:
    return {"_version": 3, "nodes": {n["id"]: n for n in nodes}, "wires": wires}


def _pp(name, target, type_="int", default=14, label=None):
    return {"name": name, "label": label or name, "target": target, "type": type_,
            "default": default}


def mom(values=None, promoted=None, child_period=99) -> Graph:
    """Ticker -> subnet "mom" (rsi -> below) -> Entry, with the RSI period
    promoted as ``rsi_period``.  The child's own value (99) is what a
    missing substitution would leave behind."""
    promoted = [_pp("rsi_period", "rsi/period")] if promoted is None else promoted
    return Graph.model_validate(_data(
        [_n("t", "ticker"),
         _n("mom", "subnet", values or {}, None, promoted=promoted),
         _n("in0", "subnet_input", {"port": 0}, "mom"),
         _n("rsi", "rsi", {"period": child_period}, "mom"),
         _n("lo", "below", {"threshold": 35, "out": "@lo"}, "mom"),
         _n("out", "subnet_output", {}, "mom"),
         _n("entry", "entry")],
        [_w("w1", "t", "mom"), _w("w2", "in0", "rsi"), _w("w3", "rsi", "lo"),
         _w("w4", "lo", "out"), _w("w5", "mom", "entry")],
    ))


def flat_rsi(period: int) -> Graph:
    return Graph.model_validate(_data(
        [_n("t", "ticker"), _n("rsi", "rsi", {"period": period}),
         _n("lo", "below", {"threshold": 35, "out": "@lo"}), _n("entry", "entry")],
        [_w("w2", "t", "rsi"), _w("w3", "rsi", "lo"), _w("w5", "lo", "entry")],
    ))


def _diags(graph: Graph, code: str = "promoted_invalid") -> list:
    return [d for d in check_graph(graph).diagnostics if d.code == code]


# ---------------------------------------------------------------------------
# Substitution
# ---------------------------------------------------------------------------


def test_the_promoted_value_goes_into_the_target(df):
    graph = mom({"rsi_period": 7})
    flat = flatten(graph)
    assert flat.graph.nodes["rsi"].params["period"] == 7
    assert flat.promoted_from[("rsi", "period")] == ("mom", "rsi_period")
    assert graph.nodes["rsi"].params["period"] == 99  # the input is unchanged
    p, hand = compile_graph(graph), compile_graph(flat_rsi(7))
    assert p.steps == hand.steps
    e1, _ = cook_signals(p, df)
    e2, _ = cook_signals(hand, df)
    assert np.array_equal(e1, e2) and e1.any()


def test_a_missing_value_uses_the_default():
    flat = flatten(mom({}, promoted=[_pp("rsi_period", "rsi/period", default=21)]))
    assert flat.graph.nodes["rsi"].params["period"] == 21


def test_a_promoted_override_changes_the_signals(df):
    a, _ = cook_signals(compile_graph(mom({"rsi_period": 5})), df)
    b, _ = cook_signals(compile_graph(mom({"rsi_period": 30})), df)
    assert not np.array_equal(a, b)


def test_without_promoted_params_flatten_is_untouched():
    graph = flat_rsi(14)
    assert flatten(graph).graph is graph  # the fast path still applies


def test_an_outer_network_promotes_an_inner_promoted_param(df):
    graph = Graph.model_validate(_data(
        [_n("t", "ticker"),
         _n("outer", "subnet", {"len": 9}, None, promoted=[_pp("len", "inner/rsi_period")]),
         _n("oi", "subnet_input", {"port": 0}, "outer"),
         _n("inner", "subnet", {"rsi_period": 50}, "outer",
            promoted=[_pp("rsi_period", "rsi/period")]),
         _n("ii", "subnet_input", {"port": 0}, "inner"),
         _n("rsi", "rsi", {"period": 99}, "inner"),
         _n("lo", "below", {"threshold": 35, "out": "@lo"}, "inner"),
         _n("io", "subnet_output", {}, "inner"),
         _n("oo", "subnet_output", {}, "outer"),
         _n("entry", "entry")],
        [_w("w1", "t", "outer"), _w("a", "oi", "inner"), _w("w2", "ii", "rsi"),
         _w("w3", "rsi", "lo"), _w("w4", "lo", "io"), _w("b", "inner", "oo"),
         _w("w5", "outer", "entry")],
    ))
    flat = flatten(graph)
    assert not flat.found
    assert flat.graph.nodes["rsi"].params["period"] == 9  # the outer value wins
    assert flat.promoted_owner("rsi", "period") == ("outer", "len")
    p, hand = compile_graph(graph), compile_graph(flat_rsi(9))
    assert p.steps == hand.steps


def test_an_out_of_range_value_shows_on_the_promoted_param():
    diag = next(d for d in check_graph(mom({"rsi_period": 0})).diagnostics
                if d.severity == "error")
    assert diag.code == "param_out_of_range"
    assert (diag.node_id, diag.param) == ("mom", "rsi_period")


def test_a_write_param_can_be_promoted():
    graph = mom({"rsi_period": 14, "flag": "@buy_zone"},
                promoted=[_pp("rsi_period", "rsi/period"),
                          _pp("flag", "lo/out", type_="write", default="@lo")])
    result = check_graph(graph)
    assert result.program is not None
    assert result.program.entry_attr == "@buy_zone"


# ---------------------------------------------------------------------------
# promoted_invalid
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("promoted,values,needle", [
    ([_pp("rsi_period", "gone/period")], {}, "does not exist"),
    ([_pp("rsi_period", "rsi/periodd")], {}, "has no param 'periodd'"),
    ([_pp("rsi_period", "rsi")], {}, "must name a node and a param"),
    ([_pp("rsi_period", "/mom/rsi/period")], {}, "must be relative"),
    ([_pp("rsi_period", "../t/symbol", type_="string", default="X")], {}, "inside the network"),
    ([_pp("Bad", "rsi/period")], {}, "name is not valid"),
    ([_pp("rsi_period", "rsi/period"), _pp("rsi_period", "lo/threshold", type_="number")], {},
     "same name"),
    ([_pp("rsi_period", "rsi/period", type_="bool", default=True)], {}, "it is bool, but rsi/period is int"),
    ([_pp("rsi_period", "rsi/period", type_="colour")], {}, "is not a param type"),
    ([_pp("rsi_period", "rsi/period")], {"rsi_period": "fast"}, "but its value is 'fast'"),
    ([_pp("rsi_period", "rsi/period")], {"rsi_period": 7.5}, "but its value is 7.5"),
    ([_pp("rsi_period", "rsi/period")], {"rsi_period": True}, "but its value is True"),
    ([_pp("a", "rsi/period"), _pp("b", "rsi/period")], {}, "already set by promoted param 'a'"),
    ([_pp("p", "in0/port")], {}, "network port node"),
])
def test_bad_promoted_params(promoted, values, needle):
    found = _diags(mom(values, promoted=promoted))
    assert found, "expected promoted_invalid"
    assert found[0].node_id == "mom" and found[0].severity == "error"
    assert any(needle in d.message for d in found), [d.message for d in found]
    assert check_graph(mom(values, promoted=promoted)).program is None


def test_a_bad_promoted_param_names_the_promoted_param():
    d = _diags(mom({"rsi_period": "fast"}))[0]
    assert d.param == "rsi_period"


def test_promoted_params_on_a_plain_node_are_invalid():
    graph = Graph.model_validate(_data(
        [_n("t", "ticker"), _n("rsi", "rsi", {}, None, promoted=[_pp("p", "x/period")]),
         _n("entry", "entry")],
        [_w("w1", "t", "rsi"), _w("w2", "rsi", "entry")],
    ))
    found = _diags(graph)
    assert found and found[0].node_id == "rsi" and "only a network" in found[0].message


def test_a_name_that_is_a_param_of_the_network_type_is_invalid():
    graph = Graph.model_validate(_data(
        [_n("g", "output_group", {"direction": "long", "ticker": "t"}, None,
            promoted=[_pp("direction", "rsi/period")]),
         _n("t", "ticker", {}, "g"), _n("rsi", "rsi", {}, "g"),
         _n("entry", "entry", {}, "g")],
        [_w("w1", "t", "rsi"), _w("w2", "rsi", "entry")],
    ))
    found = _diags(graph)
    assert found and "already a param of output_group" in found[0].message


def test_an_inner_network_param_that_is_not_promoted_cannot_be_promoted():
    graph = Graph.model_validate(_data(
        [_n("outer", "subnet", {}, None,
            promoted=[_pp("w", "g/capital_weight", type_="number", default=1.0)]),
         _n("g", "output_group", {"direction": "long", "ticker": "t"}, "outer"),
         _n("t", "ticker", {}, "g")],
        [],
    ))
    found = _diags(graph)
    assert found and "only a network's promoted params" in found[0].message


def test_validate_lists_promoted_invalid_with_its_path():
    data = mom({"rsi_period": "fast"}).model_dump(by_alias=True)
    diags = [d for d in validate_graph_full(data).diagnostics if d.code == "promoted_invalid"]
    assert diags and diags[0].path == "/mom" and diags[0].param == "rsi_period"


def test_promoted_params_load_with_any_shape_problem_left_to_diagnostics():
    # Semantic problems never stop a graph from loading (the editor can show them).
    graph = mom({}, promoted=[_pp("Not Valid", "nowhere", type_="whatever")])
    assert graph.nodes["mom"].promoted[0].name == "Not Valid"
    # A promoted entry that is not an object is a load error, though.
    with pytest.raises(Exception):
        Graph.model_validate(_data([_n("s", "subnet", {}, None, promoted=["x"])], []))


# ---------------------------------------------------------------------------
# Renames (shared vectors and compile)
# ---------------------------------------------------------------------------


def _vector_graph(key: str) -> Graph:
    return Graph.model_validate({"_version": 3, "wires": [], **_PATHS["graphs"][key]})


@pytest.mark.parametrize("case", _PATHS["rewrite_promoted"], ids=lambda c: c["why"][:48])
def test_rename_rewrites_promoted_targets(case):
    graph = _vector_graph(case["graph"])
    before = graph.model_dump(by_alias=True)
    renamed = rename_node(graph, case["id"], case["new_name"])
    got = {nid: [p.target for p in n.promoted] for nid, n in renamed.nodes.items() if n.promoted}
    assert got == case["expect"]["targets"]
    assert graph.model_dump(by_alias=True) == before  # the input is never changed


def test_promoted_params_survive_a_rename_of_the_target(df):
    graph = mom({"rsi_period": 7})
    renamed = rename_node(graph, "rsi", "rsi_fast")
    assert renamed.nodes["mom"].promoted[0].target == "rsi_fast/period"
    assert not _diags(renamed)
    a, b = compile_graph(graph), compile_graph(renamed)
    assert a.steps == b.steps
    # Renaming the network keeps it working too.
    again = rename_node(renamed, "mom", "momentum")
    assert again.nodes["mom"].promoted[0].target == "rsi_fast/period"
    assert compile_graph(again).steps == a.steps


def test_without_the_rewrite_a_rename_would_break_the_target():
    """The rename vector's premise: the old string no longer resolves."""
    graph = mom({"rsi_period": 7})
    moved = graph.model_copy(deep=True)
    moved.nodes["rsi"] = moved.nodes["rsi"].model_copy(update={"name": "rsi_fast"})
    assert _diags(moved) and "does not exist" in _diags(moved)[0].message


# ---------------------------------------------------------------------------
# Node meta (FA1)
# ---------------------------------------------------------------------------


META = {"view": "card", "note": "Only trade when momentum is weak.", "color": "#aabbcc"}


def _with_meta() -> Graph:
    data = mom({"rsi_period": 7}).model_dump(by_alias=True)
    data["nodes"]["mom"]["meta"] = dict(META)
    data["nodes"]["rsi"]["meta"] = {"note": "fast"}
    return Graph.model_validate(data)


def test_meta_survives_save_load_and_copy():
    graph = _with_meta()
    dumped = graph.model_dump(by_alias=True)
    assert dumped["nodes"]["mom"]["meta"] == META
    assert "meta" not in dumped["nodes"]["t"]  # an empty meta is not written
    loaded = Graph.model_validate(json.loads(json.dumps(dumped)))
    assert loaded.nodes["mom"].meta == META and loaded.nodes["rsi"].meta == {"note": "fast"}
    copied = graph.model_copy(deep=True)
    copied.nodes["mom"].meta["note"] = "changed"
    assert graph.nodes["mom"].meta["note"] == META["note"]  # a deep copy
    renamed = rename_node(graph, "rsi", "rsi_fast")
    assert renamed.nodes["mom"].meta == META and renamed.nodes["rsi"].meta == {"note": "fast"}


def test_meta_survives_migration_from_older_versions():
    v2 = {
        "_version": 2,
        "nodes": {
            "t": {"id": "t", "type": "ticker", "name": "t", "params": {"source": "yahoo"},
                  "meta": {"note": "the traded symbol"}},
            "r": {"id": "r", "type": "rsi", "name": "r", "params": {}, "meta": {"view": "frame"}},
        },
        "wires": [{"id": "w", "from": "t", "to": "r", "to_port": "in0", "attr": "close"}],
    }
    graph = Graph.model_validate(v2)
    assert graph.version == 3
    assert graph.nodes["t"].meta == {"note": "the traded symbol"}
    assert graph.nodes["r"].meta == {"view": "frame"}
    v1 = {"_version": 1, "nodes": {"/r": {"id": "/r", "type": "rsi", "meta": {"note": "x"}}},
          "wires": []}
    assert Graph.model_validate(v1).nodes["/r"].meta == {"note": "x"}


def test_meta_survives_the_graph_store(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))
    from nodebuilder.storage import get_store

    store = get_store()
    env = store.create("meta_check", _with_meta().model_dump(by_alias=True))
    got = store.get(env["id"])["graph"]
    assert got["nodes"]["mom"]["meta"] == META
    assert got["nodes"]["mom"]["promoted"][0]["target"] == "rsi/period"
    assert Graph.model_validate(got).nodes["mom"].meta == META


def test_compile_ignores_meta():
    plain, with_meta = mom({"rsi_period": 7}), _with_meta()
    a, b = check_graph(plain), check_graph(with_meta)
    assert a.program is not None and b.program is not None
    assert a.program.steps == b.program.steps
    assert [d.code for d in a.diagnostics] == [d.code for d in b.diagnostics]
    from nodebuilder.cook_cache import eval_hash

    assert eval_hash(plain) == eval_hash(with_meta)


@pytest.mark.parametrize("meta", [{"view": "collapsed"}, {"note": 3},
                                  {f"k{i}": i for i in range(33)}])
def test_bad_meta_is_refused(meta):
    data = mom().model_dump(by_alias=True)
    data["nodes"]["rsi"]["meta"] = meta
    with pytest.raises(Exception):
        Graph.model_validate(data)
    diags = validate_graph_full(data).diagnostics
    assert ("graph_invalid", "rsi") in [(d.code, d.node_id) for d in diags]


def test_a_graph_without_w6_fields_saves_exactly_as_before():
    graph = flat_rsi(14)
    for node in graph.model_dump(by_alias=True)["nodes"].values():
        assert set(node) == {"id", "type", "name", "parent", "params", "position", "display",
                             "bypass"}

"""ch() references, expression params and their cook order (F435 W7, item 7.B).

- The shared vectors (vectors/ch_refs.json, also run by the frontend's
  chRefs.vectors.test.ts): where a ch() path points, and how rename_node
  rewrites paths in code and expressions.
- kernel/params.py: references are resolved against the graph before
  flatten (so they work inside asset instances), broken ones are
  ref_broken, loops are ch_cycle, and /validate gets param_deps.
- At cook time each expression is evaluated before its node runs, in the
  order its references need (kernel/evaluate.py), and the node's checks
  run again on the value.
- The model fields (Node.code, Node.spare_params, {"expr": ...} values),
  the analysis hook for code IO, the W7 diagnostic codes and the cook-cache
  hash.

Tests never start a bot and never touch backend/data.
"""
from __future__ import annotations

import copy
import json
import os

import numpy as np
import pandas as pd
import pytest

from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.code import CodeError, prepare, run as run_code
from nodebuilder.cook_cache import eval_hash
from nodebuilder.diagnostics import CODES, SEVERITY_BY_CODE, from_error
from nodebuilder.evaluator import bars_from_frame
from nodebuilder.kernel import registry
from nodebuilder.kernel.assets import expand_assets
from nodebuilder.kernel.evaluate import analyze_graph, build_steps, cook
from nodebuilder.kernel.flatten import flatten
from nodebuilder.kernel.params import (
    SCOPE_KEY,
    plan_params,
    resolve_path,
    resolver_for,
)
from nodebuilder.kernel.registry import ParamSpec, PortSpec, PortsSpec
from nodebuilder.kernel.schema import ExtraIO, recheck
from nodebuilder.migrate import rename_node
from nodebuilder.models import Graph, GraphValidationError

_HERE = os.path.dirname(__file__)
with open(os.path.join(_HERE, "vectors", "ch_refs.json"), encoding="utf-8") as _fh:
    VECTORS = json.load(_fh)

N = 200


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    t = np.arange(N)
    close = 100 + 6 * np.sin(t / 9) + np.cumsum(rng.normal(0, 0.6, N))
    idx = pd.date_range("2023-01-02", periods=N, freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": close - 0.2, "High": close + 0.5, "Low": close - 0.5,
                         "Close": close, "Volume": rng.integers(1e5, 1e6, N)}, index=idx)


def _n(nid, typ, params=None, parent=None, **extra):
    return {"id": nid, "type": typ, "name": nid, "parent": parent, "params": params or {}, **extra}


def _w(wid, a, b, port="in0"):
    return {"id": wid, "from": a, "to": b, "to_port": port}


from nodebuilder.trading.nodes_code import extra_io as code_extra_io  # noqa: E402


def _graph(nodes, wires=()) -> Graph:
    return Graph.model_validate({"_version": 3, "nodes": {n["id"]: n for n in nodes},
                                 "wires": list(wires)})


def _expr(src: str) -> dict:
    return {"expr": src}


def _plan(graph: Graph, extra_io=None):
    analysis, flat = analyze_graph(graph, extra_io=extra_io)
    return analysis, flat, plan_params(flat, analysis)


def _cook(graph: Graph, df: pd.DataFrame, extra_io=None):
    analysis, flat, plan = _plan(graph, extra_io)
    errors = [d for d, e in list(analysis.found) + list(plan.found) if d.severity == "error"]
    assert not errors, [(d.code, d.node_id, d.message) for d in errors]
    steps = build_steps(analysis, plan)
    return cook(steps, df.index, {"bars": bars_from_frame(df)}, keep=None), plan, steps


def _codes(found, code):
    return [d for d, _e in found if d.code == code]


# ---------------------------------------------------------------------------
# Shared vectors
# ---------------------------------------------------------------------------


def _vector_graph() -> Graph:
    return Graph.model_validate({"_version": 3, "nodes": copy.deepcopy(VECTORS["graph"]["nodes"]),
                                 "wires": []})


@pytest.mark.parametrize("vec", VECTORS["resolve"], ids=lambda v: v["name"])
def test_resolve_vectors(vec):
    graph = _vector_graph()
    got = resolve_path(graph.nodes, vec["from"], vec["path"])
    want = None if vec["expect"] is None else (vec["expect"]["node_id"], vec["expect"]["target"])
    assert got == want


@pytest.mark.parametrize("vec", VECTORS["rename"], ids=lambda v: v["name"])
def test_rename_vectors(vec):
    graph = _vector_graph()
    reader = graph.nodes[vec["node_id"]]
    field = vec["field"]
    before = reader.code if field == "code" else reader.params[field.split(".", 1)[1]]["expr"]
    assert before == vec["before"]
    renamed = rename_node(graph, vec["rename"]["node_id"], vec["rename"]["new_name"])
    reader = renamed.nodes[vec["node_id"]]
    after = reader.code if field == "code" else reader.params[field.split(".", 1)[1]]["expr"]
    assert after == vec["after"]
    # And the rewritten path still points at the same node.
    for (path_before, path_after) in zip(_paths(before), _paths(after)):
        old = resolve_path(graph.nodes, vec["node_id"], path_before)
        new = resolve_path(renamed.nodes, vec["node_id"], path_after)
        assert old == new


def _paths(source: str) -> list[str]:
    prepared = prepare(source, "expr" if "\n" not in source and "=" not in source.split("(")[0]
                       else "node_code")
    return [r.target for r in prepared.refs if r.is_path]


def test_rename_keeps_quote_style_and_crlf():
    graph = _graph([
        _n("vol", "constant", {"value": 2.0}),
        _n("r", "rsi", {"period": _expr("chi('../vol/value') + 5")},
           code="a = 1\r\nb = chf('../vol/value')  # '../vol/value'\r\n"),
    ])
    out = rename_node(graph, "vol", "lvl")
    assert out.nodes["r"].params["period"] == {"expr": "chi('../lvl/value') + 5"}
    assert out.nodes["r"].code == "a = 1\r\nb = chf('../lvl/value')  # '../vol/value'\r\n"
    assert graph.nodes["r"].params["period"] == {"expr": "chi('../vol/value') + 5"}  # input unchanged


# ---------------------------------------------------------------------------
# The plan: references, param_deps, ref_broken
# ---------------------------------------------------------------------------


def _ref_graph(c_params=None, rsi_period=None, extra=(), extra_wires=()):
    return _graph([
        _n("t", "ticker"),
        _n("c", "constant", c_params or {"value": 3.0, "as_detail": True, "out": "@lvl"}),
        _n("rsi", "rsi", {"period": rsi_period or _expr('7 if chf("../c/value") > 2 else 21'),
                          "out": "@rsi"}),
        *extra,
    ], [_w("w1", "t", "rsi"), *extra_wires])


def test_param_deps_one_edge_per_reference():
    graph = _ref_graph(extra=[
        _n("r2", "rsi", {"period": _expr('int(ch("../c/@lvl")) * 5'), "out": "@r2"}),
    ], extra_wires=[_w("w2", "t", "r2")])
    _a, _f, plan = _plan(graph)
    assert plan.found == []
    assert plan.param_deps() == [
        {"reader_id": "rsi", "reader_param": "period", "target_id": "c", "target": "value"},
        {"reader_id": "r2", "reader_param": "period", "target_id": "c", "target": "@lvl"},
    ]
    # Only the @attr read needs an order: c cooks before r2.  The static
    # param read needs none.
    assert plan.after == {"r2": ("c",)}
    assert plan.expr_order == {"rsi": ("period",), "r2": ("period",)}


@pytest.mark.parametrize("src, why", [
    ('chf("../nope/value")', "points at no node"),
    ('chf("../c/nope")', "has no param 'nope'"),
    ('ch("../c/@nope")', "does not write @nope"),
    ('chf("../../c/value")', "points at no node"),
])
def test_broken_reference_is_ref_broken_at_the_call(src, why):
    graph = _ref_graph(rsi_period=_expr(f"14 + 0 * {src}"))
    _a, _f, plan = _plan(graph)
    (d,) = _codes(plan.found, "ref_broken")
    assert d.node_id == "rsi" and d.param == "period" and d.severity == "error"
    assert why in d.message
    assert (d.line, d.col) == (1, 9)  # the ch call, 0-based column in the user's text
    assert plan.param_deps() == []


def test_reading_the_expression_of_a_bypassed_node_is_ref_broken():
    graph = _graph([
        _n("t", "ticker"),
        _n("a", "rsi", {"period": _expr("9"), "out": "@a"}, bypass=True),
        _n("b", "rsi", {"period": _expr('chi("../a/period")'), "out": "@b"}),
    ], [_w("w1", "t", "a"), _w("w2", "a", "b")])
    _a, _f, plan = _plan(graph)
    (d,) = _codes(plan.found, "ref_broken")
    assert d.node_id == "b" and "does not cook" in d.message


def test_reading_a_plain_param_of_a_downstream_node_is_fine(df):
    """A static value needs no cook order, so no loop either."""
    graph = _graph([
        _n("t", "ticker"),
        _n("a", "rsi", {"period": _expr('chi("../b/period") - 3'), "out": "@a"}),
        _n("b", "sma", {"period": 10, "source": "@a", "out": "@b"}),
        _n("ref", "rsi", {"period": 7, "out": "@ref"}),
    ], [_w("w1", "t", "a"), _w("w2", "a", "b"), _w("w3", "t", "ref")])
    result, plan, _steps = _cook(graph, df)
    assert plan.after == {}
    np.testing.assert_array_equal(result.column("a", "@a"), result.column("ref", "@ref"))


# ---------------------------------------------------------------------------
# ch_cycle
# ---------------------------------------------------------------------------


def test_two_expressions_reading_each_other_are_ch_cycle():
    graph = _graph([
        _n("t", "ticker"),
        _n("a", "rsi", {"period": _expr('chi("../b/period")'), "out": "@a"}),
        _n("b", "rsi", {"period": _expr('chi("../a/period")'), "out": "@b"}),
    ], [_w("w1", "t", "a"), _w("w2", "t", "b")])
    _a, _f, plan = _plan(graph)
    cyc = _codes(plan.found, "ch_cycle")
    assert sorted(d.node_id for d in cyc) == ["a", "b"]
    assert all(d.severity == "error" for d in cyc)
    assert plan.after == {}


def test_params_of_one_node_in_a_loop_are_ch_cycle():
    graph = _graph([
        _n("t", "ticker"),
        _n("r", "rsi", {"period": _expr('chi("k") + 1'), "k": _expr('chi("period") - 1'),
                        "out": "@r"}),
    ], [_w("w1", "t", "r")])
    _a, _f, plan = _plan(graph)
    cyc = _codes(plan.found, "ch_cycle")
    assert sorted((d.node_id, d.param) for d in cyc) == [("r", "k"), ("r", "period")]


def test_a_loop_through_a_wire_is_ch_cycle():
    """a's period reads b's output, but b reads a's output: neither can go
    first.  (b is a Wrangle: its write may be one value, which an expression
    can read; a node's per-bar output would be code_type first.)"""
    graph = _graph([
        _n("t", "ticker"),
        _n("a", "rsi", {"period": _expr('int(ch("../b/@b"))'), "out": "@a"}),
        _n("b", "wrangle", code="@b = float(@a.iloc[-1])"),
    ], [_w("w1", "t", "a"), _w("w2", "a", "b")])
    _a, _f, plan = _plan(graph, extra_io=code_extra_io)
    assert sorted(d.node_id for d in _codes(plan.found, "ch_cycle")) == ["a", "b"]


def test_reading_its_own_output_is_ch_cycle():
    """A Wrangle reading its own write through a path: a loop, and the
    message says to use @name."""
    graph = _graph([
        _n("t", "ticker"),
        _n("w", "wrangle", code='@m = 1.0\n@n = ch("../w/@m") + 1'),
    ], [_w("w1", "t", "w")])
    _a, _f, plan = _plan(graph, extra_io=code_extra_io)
    found = _codes(plan.found, "ch_cycle")
    assert [d.node_id for d in found] == ["w"]
    assert "@m (the @name sugar)" in found[0].message


def test_an_expression_reading_its_own_nodes_per_bar_output_is_code_type():
    graph = _graph([
        _n("t", "ticker"),
        _n("a", "rsi", {"period": _expr('int(ch("../a/@a").iloc[-1])'), "out": "@a"}),
    ], [_w("w1", "t", "a")])
    _a, _f, plan = _plan(graph)
    assert [(d.node_id, d.param, d.code) for d, _e in plan.found] == [("a", "period", "code_type")]


# ---------------------------------------------------------------------------
# Cook: expressions are evaluated before their node, in reference order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value, period", [(3.0, 7), (1.0, 21)])
def test_vision_expression_gives_the_same_rsi_as_the_plain_value(df, value, period):
    """John's example: RSI period = 7 if chf("../vol/threshold") > 2 else 21."""
    graph = _ref_graph(c_params={"value": value, "out": "@lvl"}, extra=[
        _n("plain", "rsi", {"period": period, "out": "@plain"}),
    ], extra_wires=[_w("w2", "t", "plain")])
    result, _plan_, steps = _cook(graph, df)
    np.testing.assert_array_equal(result.column("rsi", "@rsi"), result.column("plain", "@plain"))
    # Compile still sees the stand-in (the catalog default) until the cook.
    assert next(s for s in steps if s.node_id == "rsi").params["period"] == 14


def test_a_detail_read_cooks_its_source_first(df):
    """Node a sorts before zc, and both take no input, so only the ch()
    edge puts zc first."""
    graph = _graph([
        _n("a", "constant", {"value": _expr('ch("../zc/@lvl") * 2'), "out": "@twice"}),
        _n("zc", "constant", {"value": 3.0, "as_detail": True, "out": "@lvl"}),
    ])
    analysis, _flat, plan = _plan(graph)
    assert [nid for nid in analysis.order] == ["a", "zc"]
    assert plan.after == {"a": ("zc",)}
    steps = build_steps(analysis, plan)
    assert [s.node_id for s in steps] == ["zc", "a"]
    assert steps[1].depends_on() == ("zc",)
    result = cook(steps, df.index, {"bars": bars_from_frame(df)}, keep={"a"})
    assert np.all(result.column("a", "@twice") == 6.0)


def test_a_point_attribute_in_an_expression_is_code_type(df):
    """Refused at compile (W7 fix PC-7), at the ch() call, not at the cook."""
    graph = _ref_graph(rsi_period=_expr('ch("../t/@close")'))
    _analysis, _flat, plan = _plan(graph)
    found = [d for d, _e in plan.found]
    assert [(d.node_id, d.param, d.code) for d in found] == [("rsi", "period", "code_type")]
    assert "Wrangle" in found[0].message and found[0].line == 1


def test_an_expression_may_read_a_wrangles_one_value_write(df):
    """A code write is listed as a point, but the code may write one value
    (a detail): compile lets an expression read it, the cook decides."""
    from nodebuilder.compile import check_graph
    from nodebuilder.evaluator import cook_program

    graph = _graph([
        _n("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
        _n("w", "wrangle", code="@level = 9.0\n@sig: bool = @close > 0"),
        _n("rsi", "rsi", {"period": _expr('chi("../w/@level")'), "out": "@rsi"}),
        _n("plain", "rsi", {"period": 9, "out": "@plain"}),
        _n("entry", "entry", {"signal": "@sig"}),
    ], [_w("w1", "t", "w"), _w("w2", "t", "rsi"), _w("w3", "t", "plain"), _w("w4", "w", "entry")])
    check = check_graph(graph)
    assert check.program is not None, [(d.code, d.node_id, d.message) for d in check.diagnostics]
    result = cook_program(check.program, df, keep_all=True)
    np.testing.assert_array_equal(np.asarray(result.streams["rsi"].column("@rsi")),
                                  np.asarray(result.streams["plain"].column("@plain")))


def test_an_expression_value_out_of_range_fails_the_cook(df):
    graph = _ref_graph(rsi_period=_expr("1"))  # RSI period is 2 to 500
    analysis, _flat, plan = _plan(graph)
    steps = build_steps(analysis, plan)
    with pytest.raises(CodeError) as err:
        cook(steps, df.index, {"bars": bars_from_frame(df)})
    assert err.value.code == "param_out_of_range"
    assert (err.value.node_id, err.value.param) == ("rsi", "period")


def test_an_expression_runs_the_node_check_again(df):
    """A comparison parses its threshold in its check; the evaluated value
    goes through the same check."""
    graph = _graph([
        _n("t", "ticker"),
        _n("c", "constant", {"value": 3.0}),
        _n("r", "rsi", {"period": 14, "out": "@r"}),
        _n("hi", "above", {"a": "@r", "threshold": _expr('chf("../c/value") * 10 + 20'),
                           "out": "@hi"}),
        _n("plain", "above", {"a": "@r", "threshold": 50, "out": "@plain"}),
    ], [_w("w1", "t", "r"), _w("w2", "r", "hi"), _w("w3", "r", "plain")])
    result, _p, _s = _cook(graph, df)
    np.testing.assert_array_equal(result.column("hi", "@hi"), result.column("plain", "@plain"))


def test_expressions_on_one_node_run_in_reference_order(df):
    graph = _graph([
        _n("t", "ticker"),
        _n("r", "rsi", {"period": _expr('chi("k") + 1'), "k": _expr("6"), "out": "@r"}),
        _n("plain", "rsi", {"period": 7, "out": "@plain"}),
    ], [_w("w1", "t", "r"), _w("w2", "t", "plain")])
    result, plan, _s = _cook(graph, df)
    assert plan.expr_order["r"] == ("k", "period")
    np.testing.assert_array_equal(result.column("r", "@r"), result.column("plain", "@plain"))


def test_static_resolver_needs_no_cook():
    graph = _ref_graph()
    _a, _f, plan = _plan(graph)
    resolve = plan.resolver("rsi")
    assert resolve("../c/value") == 3.0
    with pytest.raises(KeyError):
        resolve("../c/never_scanned")


# ---------------------------------------------------------------------------
# Where expressions are refused (compile, kernel/schema.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("node, param", [
    (_n("t", "ticker", {"symbol": _expr('"MSFT"')}), "symbol"),          # code_able False
    (_n("r", "rsi", {"out": _expr('"@x"')}), "out"),                     # a write name
    (_n("r", "rsi", {"source": _expr('"@close"')}), "source"),          # an attr read
    (_n("e", "entry", {"side": _expr('"long"')}), "side"),              # read before the cook
    (_n("c", "constant", {"as_detail": _expr("True")}), "as_detail"),   # decides the writes
])
def test_expression_refused_where_it_cannot_run(node, param):
    nodes = [node] if node["type"] == "ticker" else [_n("t", "ticker"), node]
    wires = [] if node["type"] in ("ticker", "constant") else [_w("w1", "t", node["id"])]
    analysis, _flat = analyze_graph(_graph(nodes, wires))
    # A param read before the cook (code_able False, or a type with no cook
    # step) is the shared param_not_codeable rule; the rest param_invalid.
    want = "param_not_codeable" if param in ("symbol", "side") else "param_invalid"
    bad = [d for d, _e in analysis.found if d.code in ("param_invalid", "param_not_codeable")]
    assert [(d.node_id, d.param, d.code) for d in bad] == [(node["id"], param, want)]


def test_expression_on_a_network_param_is_refused():
    graph = _graph([
        _n("t", "ticker"),
        _n("sub", "subnet", {"look": _expr("30")},
           promoted=[{"name": "look", "label": "look", "target": "s/period", "type": "int",
                      "default": 20}]),
        _n("in0", "subnet_input", {"port": 0}, "sub"),
        _n("s", "sma", {"period": 20, "out": "@s"}, "sub"),
        _n("out", "subnet_output", {}, "sub"),
    ], [_w("w1", "t", "sub"), _w("w2", "in0", "s"), _w("w3", "s", "out")])
    _a, _f, plan = _plan(graph)
    (d,) = _codes(plan.found, "param_invalid")
    assert (d.node_id, d.param) == ("sub", "look")
    assert ("s", "period") not in plan.snippets  # the copy flatten made is not code of s


def test_recheck_refuses_a_value_that_changes_the_writes():
    graph = _graph([_n("c", "constant", {"value": 1.0})])
    analysis, flat = analyze_graph(graph)
    res = analysis.nodes["c"]
    with pytest.raises(GraphValidationError) as err:
        recheck(res, flat.graph.nodes["c"], {"as_detail": True})
    assert err.value.code == "code_type"
    assert recheck(res, flat.graph.nodes["c"], {"value": 2})["value"] == 2.0


# ---------------------------------------------------------------------------
# Promoted params and asset instances (pre-flatten resolution)
# ---------------------------------------------------------------------------


def _mom(values) -> Graph:
    return _graph([
        _n("t", "ticker"),
        _n("mom", "subnet", values,
           promoted=[{"name": "rsi_period", "label": "rsi_period", "target": "rsi/period",
                      "type": "int", "default": 14}]),
        _n("in0", "subnet_input", {"port": 0}, "mom"),
        _n("rsi", "rsi", {"period": 99}, "mom"),
        _n("lo", "below", {"threshold": _expr('chf("../rsi_period") + 28'), "out": "@lo"}, "mom"),
        _n("out", "subnet_output", {}, "mom"),
        _n("plain", "below", {"a": "@rsi", "threshold": 35, "out": "@plain"}),
    ], [_w("w1", "t", "mom"), _w("w2", "in0", "rsi"), _w("w3", "rsi", "lo"), _w("w4", "lo", "out"),
        _w("w5", "mom", "plain")])


@pytest.mark.parametrize("values", [{"rsi_period": 7}, {}])
def test_dot_dot_name_reads_the_promoted_param_of_the_enclosing_network(df, values):
    graph = _mom(values)
    flat = flatten(graph)
    assert flat.network_params["mom"] == values
    result, plan, _s = _cook(graph, df)
    ref = next(r for r in plan.refs if r.path == "../rsi_period")
    assert (ref.target_id, ref.target) == ("mom", "rsi_period")
    want = (values.get("rsi_period", 14)) + 28
    assert plan.resolver("lo")("../rsi_period") == values.get("rsi_period", 14)
    lo = result.column("lo", "@lo")
    rsi = result.column("rsi", "@rsi")
    with np.errstate(invalid="ignore"):
        np.testing.assert_array_equal(lo, rsi < want)


class _Library:
    def __init__(self, *assets):
        self.files = {(a["name"], a["version"]): a for a in assets}

    def __call__(self, name, version):
        found = self.files.get((name, version))
        return copy.deepcopy(found) if found is not None else None


def _asset() -> dict:
    nodes = [
        _n("in0", "subnet_input", {"port": 0}),
        _n("sma", "sma", {"period": 50, "out": "@rf_ma"}),
        _n("half", "sma", {"period": _expr('chi("../sma/period") // 2'), "out": "@rf_half"}),
        _n("look", "constant", {"value": _expr('chf("../lookback")'), "out": "@rf_look"}),
        _n("on", "above", {"a": "@close", "b": "@rf_ma", "out": "@regime_on"}),
        _n("out", "subnet_output"),
    ]
    return {
        "name": "regime_filter", "version": 1, "description": "", "stream_schema": 1,
        "interface": {"reads": [{"name": "@close", "class": "point", "dtype": "float"}],
                      "writes": [{"name": "@regime_on", "class": "point", "dtype": "bool"}]},
        "promoted": [{"name": "lookback", "label": "Lookback", "target": "sma/period",
                      "type": "int", "default": 50}],
        "palette": {"category": "rules", "label": "Regime Filter", "glyph": "R"},
        "network": {"nodes": {n["id"]: n for n in nodes},
                    "wires": [_w("w1", "in0", "sma"), _w("w2", "in0", "half"),
                              _w("w3", "sma", "on"), _w("w4", "on", "out")]},
        "created_at": "2026-10-02T09:00:00Z",
    }


def test_paths_resolve_inside_a_locked_asset_instance(df):
    graph = _graph([
        _n("t", "ticker"),
        _n("rf", "subnet", {"lookback": 30}, asset_ref={"name": "regime_filter", "version": 1},
           locked=True),
        _n("plain", "sma", {"period": 15, "out": "@plain"}),
    ], [_w("w1", "t", "rf"), _w("w2", "t", "plain")])
    expanded, problems = expand_assets(graph, _Library(_asset()))
    assert problems == []
    result, plan, _s = _cook(expanded, df)
    half = next(r for r in plan.refs if r.path == "../sma/period")
    assert (half.reader_id, half.target_id) == ("rf::half", "rf::sma")
    np.testing.assert_array_equal(result.column("rf::half", "@rf_half"),
                                  result.column("plain", "@plain"))
    assert np.all(result.column("rf::look", "@rf_look") == 30.0)
    # The editor cannot see inside a locked instance: no edges to show.
    assert plan.param_deps() == []


def test_errors_inside_a_locked_instance_show_on_the_instance(df):
    asset = _asset()
    asset["network"]["nodes"]["half"]["params"]["period"] = _expr('chi("../nope/period")')
    graph = _graph([
        _n("t", "ticker"),
        _n("rf", "subnet", {}, asset_ref={"name": "regime_filter", "version": 1}, locked=True),
    ], [_w("w1", "t", "rf")])
    expanded, _p = expand_assets(graph, _Library(asset))
    _a, _f, plan = _plan(expanded)
    (d,) = _codes(plan.found, "ref_broken")
    assert d.node_id == "rf" and d.param is None


# ---------------------------------------------------------------------------
# Code IO hook (schema.ExtraIO) and resolver_for, with a stand-in code node
# ---------------------------------------------------------------------------


CODE_TYPE = "zz_code_7b"


@pytest.fixture
def code_node():
    """A node type that runs its node.code the way a Wrangle will (7.C)."""
    def _impl(inputs, params):
        snip = params.env[SCOPE_KEY].plan.code_of(params.node_id)
        return run_code(snip.prepared, inputs, params=params, resolve=resolver_for(params)).stream

    registry.register_node(
        name=CODE_TYPE, cat="code", desc="test stand-in for a Wrangle",
        params=(), inputs=PortsSpec(ports=(PortSpec("in0"),), dynamic=False, min=1, max=1),
        impl=_impl,
    )
    yield CODE_TYPE
    registry.unregister(CODE_TYPE)


def _extra_io(shadow_ok=False):
    def hook(node, nt):
        if node.type != CODE_TYPE or not node.code:
            return None
        p = prepare(node.code, "wrangle", node)
        return ExtraIO(writes=tuple((f"@{n}", p.write_dtypes.get(n, "any")) for n in p.writes),
                       reads_any=True, lookback=p.effective_lookback(node.params) or 0,
                       shadow_ok=shadow_ok)
    return hook


def test_code_writes_enter_the_schema_and_ch_reads_work(df, code_node):
    graph = _graph([
        _n("t", "ticker"),
        _n("c", "constant", {"value": 3.0}),
        _n("w", code_node, {}, code='@z: float = @close * chf("../c/value")'),
        _n("hi", "above", {"a": "@z", "threshold": 0, "out": "@hi"}),
    ], [_w("w1", "t", "w"), _w("w2", "w", "hi")])
    analysis, _flat, plan = _plan(graph, extra_io=_extra_io())
    assert analysis.nodes["w"].out_schema.points["@z"].dtype == "float"
    assert analysis.nodes["w"].lookback == 500  # lookback_bars default
    steps = build_steps(analysis, plan)
    w_step = next(s for s in steps if s.node_id == "w")
    assert ("t", "@close") in w_step.read_from  # reads_any keeps the input columns alive
    result = cook(steps, df.index, {"bars": bars_from_frame(df)}, keep={"hi"})
    np.testing.assert_allclose(result.column("hi", "@z"), df["Close"].to_numpy() * 3.0)


def test_a_code_write_of_an_upstream_name_is_attr_clash(code_node):
    graph = _graph([
        _n("t", "ticker"),
        _n("w", code_node, {}, code="@close = @close * 2"),
    ], [_w("w1", "t", "w")])
    analysis, _flat = analyze_graph(graph, extra_io=_extra_io())
    assert [d.code for d, _e in analysis.found] == ["attr_clash"]
    analysis, _flat = analyze_graph(graph, extra_io=_extra_io(shadow_ok=True))
    assert [d.code for d, _e in analysis.found] == ["attr_shadowed"]


def test_a_bypassed_code_node_disables_its_writes(code_node):
    graph = _graph([
        _n("t", "ticker"),
        _n("w", code_node, {}, code="@z = @close", bypass=True),
        _n("hi", "above", {"a": "@z", "threshold": 0, "out": "@hi"}),
    ], [_w("w1", "t", "w"), _w("w2", "w", "hi")])
    analysis, _flat = analyze_graph(graph, extra_io=_extra_io())
    assert "@z" in analysis.nodes["w"].out_schema.disabled
    assert analysis.nodes["hi"].status == "pass"  # its read is off, so it turns off


# ---------------------------------------------------------------------------
# Model fields
# ---------------------------------------------------------------------------


def test_an_older_graph_loads_and_saves_without_the_new_fields():
    data = {"_version": 3, "nodes": {"t": _n("t", "ticker"), "r": _n("r", "rsi", {"period": 9})},
            "wires": [_w("w1", "t", "r")]}
    dumped = Graph.model_validate(data).model_dump(by_alias=True)
    for node in dumped["nodes"].values():
        assert "code" not in node and "spare_params" not in node


def test_code_fields_round_trip():
    spare = {"name": "th", "type": "float", "default": 2.0, "min": 0.0, "max": 5.0,
             "options": None, "label": "threshold"}
    data = {"_version": 3, "nodes": {"r": _n("r", "rsi", {"period": _expr("7"), "th": 2.5},
                                             code="@s = sl.ema(@rsi, 3)", spare_params=[spare])},
            "wires": []}
    dumped = Graph.model_validate(data).model_dump(by_alias=True)
    node = dumped["nodes"]["r"]
    assert node["code"] == "@s = sl.ema(@rsi, 3)"
    assert node["spare_params"] == [spare]
    assert node["params"]["period"] == {"expr": "7"}
    assert Graph.model_validate(dumped).model_dump(by_alias=True) == dumped


@pytest.mark.parametrize("value", [{"expr": 7}, {"expr": "7", "x": 1}])
def test_a_malformed_expression_value_is_refused(value):
    with pytest.raises(ValueError):
        Graph.model_validate({"_version": 3, "nodes": {"r": _n("r", "rsi", {"period": value})},
                              "wires": []})


def test_a_spare_param_type_is_checked():
    with pytest.raises(ValueError):
        Graph.model_validate({"_version": 3, "nodes": {"r": _n(
            "r", "rsi", {}, spare_params=[{"name": "x", "type": "matrix"}])}, "wires": []})


# ---------------------------------------------------------------------------
# Diagnostic codes and the cook-cache hash
# ---------------------------------------------------------------------------


def test_w7_codes_are_registered_as_errors():
    for code in ("code_syntax", "code_limit", "code_runtime", "code_timeout", "code_type",
                 "ch_dynamic", "attr_dynamic", "ch_cycle", "code_disabled",
                 "ticker_param_not_codeable", "param_not_codeable", "ref_broken"):
        assert code in CODES and SEVERITY_BY_CODE[code] == "error"
    from nodebuilder.code import CODES as RUNTIME_CODES
    assert set(RUNTIME_CODES) <= set(CODES)


def test_from_error_keeps_the_place_in_the_code():
    err = CodeError("code_runtime", "ZeroDivisionError: division by zero", line=3, col=4,
                    end_line=3, end_col=9, node_id="w", param=None)
    d = from_error(err)
    assert (d.code, d.node_id, d.line, d.col, d.end_line, d.end_col) == (
        "code_runtime", "w", 3, 4, 3, 9)


def test_eval_hash_covers_code_spares_expressions_and_names_with_code():
    def g(**kw):
        node = _n("r", "rsi", kw.pop("params", {"period": 9}), **kw)
        return _graph([_n("vol", "constant", {"value": 2.0}), node])

    base = eval_hash(g())
    assert eval_hash(g(code="@s = @rsi")) != base
    assert eval_hash(g(code="@s = @rsi")) != eval_hash(g(code="@s = @rsi * 2"))
    spare = [{"name": "th", "type": "float", "default": 2.0}]
    assert eval_hash(g(spare_params=spare)) != base
    e1 = eval_hash(g(params={"period": _expr('chi("../vol/value") + 5')}))
    assert e1 != eval_hash(g(params={"period": _expr('chi("../vol/value") + 6')}))
    # With code, a name decides what ch() finds, so a rename changes the hash ...
    with_code = g(params={"period": _expr('chi("../vol/value") + 5')})
    renamed = with_code.model_copy(deep=True)
    renamed.nodes["vol"] = renamed.nodes["vol"].model_copy(update={"name": "lvl"})
    assert eval_hash(renamed) != eval_hash(with_code)
    # ... and without code it still does not.
    plain = g()
    plain_renamed = plain.model_copy(deep=True)
    plain_renamed.nodes["vol"] = plain_renamed.nodes["vol"].model_copy(update={"name": "lvl"})
    assert eval_hash(plain_renamed) == eval_hash(plain)


def test_code_diagnostics_lists_what_prepare_found():
    graph = _ref_graph(rsi_period=_expr("7 +"))
    _a, _f, plan = _plan(graph)
    assert plan.found == []  # syntax is not a reference problem
    (d,) = [d for d, _e in plan.code_diagnostics()]
    assert (d.code, d.node_id, d.param, d.line) == ("code_syntax", "rsi", "period", 1)


def test_a_graph_without_code_builds_the_same_steps_with_a_plan():
    graph = _graph([_n("t", "ticker"), _n("r", "rsi", {"period": 9})], [_w("w1", "t", "r")])
    analysis, _flat, plan = _plan(graph)
    assert build_steps(analysis, plan) == build_steps(analysis)
    assert all(s.plan is None and s.param_hook is None for s in build_steps(analysis, plan))

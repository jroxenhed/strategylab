"""W1 item 1.A: graph migration v1 -> v2 and the shared path helper vectors.

vectors/v1_autorender.json holds graphs exactly as auto_render produced them
before schema v2, in both stored shapes (API and bots.json), plus the bars
where Entry and Exit fired for each on its parity fixture df.  Migration must
not change a single one of those bars.
"""
from __future__ import annotations

import copy
import json
import os
import pickle

import numpy as np
import pandas as pd
import pytest

from nodebuilder import migrate
from nodebuilder.compile import compile as compile_graph
from nodebuilder.evaluator import compute_indicators_from_specs, evaluate_graph
from nodebuilder.from_rules import auto_render
from nodebuilder.migrate import (
    NodeNotFoundError,
    default_name,
    find_by_path,
    is_valid_name,
    migrate_graph_data,
    migrate_v1_to_v2,
    node_path,
    rename_node,
    sanitize_name,
    unique_name,
)
from nodebuilder.models import (
    DuplicateNodeNameError,
    Graph,
    IncompatibleGraphVersionError,
    InvalidNodeNameError,
    Node,
    Wire,
)
from nodebuilder.run import _NO_EXIT_ATTR
from tests.nodebuilder.test_backtest_parity import _STRATEGIES

_HERE = os.path.dirname(__file__)
_FIXTURES_DIR = os.path.join(_HERE, "fixtures", "run_backtest_snapshots")

with open(os.path.join(_HERE, "vectors", "v1_autorender.json")) as _fh:
    _V1 = json.load(_fh)
with open(os.path.join(_HERE, "vectors", "paths.json")) as _fh:
    _PATHS = json.load(_fh)

_NAMES = [name for name, _ in _STRATEGIES]


def _load_df(name: str) -> pd.DataFrame:
    with open(os.path.join(_FIXTURES_DIR, f"{name}_df.pkl"), "rb") as fh:
        return pickle.load(fh)


def _signals(graph: Graph, df: pd.DataFrame) -> dict:
    """Compile a graph and return the bars where Entry and Exit fire.

    Same steps as run_graph_backtest up to the signals (the recipe that made
    the goldens in v1_autorender.json).
    """
    from indicators import OHLCVSeries

    try:
        program = compile_graph(graph)
    except Exception as exc:  # noqa: BLE001 (the golden records the class name)
        return {"error": type(exc).__name__}
    vol = df["Volume"] if "Volume" in df.columns else pd.Series(0, index=df.index)
    ohlcv = OHLCVSeries(close=df["Close"], high=df["High"], low=df["Low"], volume=vol)
    attrs = compute_indicators_from_specs(program.indicator_specs, ohlcv)
    for col, key in (("Close", "@close"), ("Open", "@open"), ("High", "@high"), ("Low", "@low")):
        attrs[key] = df[col]
    attrs["@volume"] = vol
    attrs[_NO_EXIT_ATTR] = pd.Series(0.0, index=df.index, dtype="float64")
    for op in program.per_bar_program:
        if op.writes not in attrs:
            attrs[op.writes] = pd.Series(np.nan, index=df.index, dtype="float64")
    entries, exits = [], []
    for i in range(len(df)):
        s = evaluate_graph(program, attrs, i)
        if s["entry"]:
            entries.append(i)
        if s["exit"]:
            exits.append(i)
    settings = sorted(repr((s.key, s.value)) for s in program.simulator_settings)
    return {"entries": entries, "exits": exits, "settings": settings}


# ---------------------------------------------------------------------------
# v1 auto-render graphs survive migration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", _NAMES)
def test_v1_autorender_graph_migrates(name):
    raw = _V1["api"][name]
    assert raw["_version"] == 1
    g = Graph.model_validate(raw)
    assert g.version == migrate.CURRENT_GRAPH_VERSION
    assert g.stream_schema == 1
    assert set(g.nodes) == set(raw["nodes"])
    assert [w.id for w in g.wires] == [w["id"] for w in raw["wires"]]
    for node_id, node in g.nodes.items():
        assert node.name == sanitize_name(node_id.lstrip("/"))
        assert node.parent is None
    # to_port = in<k> by each consumer's wire order
    seen: dict[str, int] = {}
    for w in g.wires:
        k = seen.get(w.to_path, 0)
        assert w.to_port == f"in{k}"
        assert w.from_port == "out"
        seen[w.to_path] = k + 1


@pytest.mark.parametrize("name", _NAMES)
def test_migrated_v1_matches_fresh_autorender(name):
    """from_rules names nodes as the migration does, and both cook the same.

    Since W2 the two are no longer byte-equal: from_rules draws one wire per
    (source, consumer) pair and v3 reads (a / b / terms params), while a
    migrated v1 graph keeps its wires.  So compare what matters: every node
    from_rules draws exists in the migrated graph with the same type and name,
    and both fire on the same bars with the same settings.
    """
    req = dict(_STRATEGIES)[name]
    migrated = Graph.model_validate(_V1["api"][name])
    fresh = auto_render(req)
    assert migrated.version == fresh.version == migrate.CURRENT_GRAPH_VERSION
    for node_id, node in fresh.nodes.items():
        if node_id.startswith("/regime/"):
            # W2 from_rules draws a regime rule with no series (ma without
            # params) as a "never" node, as eval_rules reads it; v1 drew an
            # EMA 20 there.  Regime graphs do not compile until W5 either way.
            continue
        assert node_id in migrated.nodes, node_id
        assert (migrated.nodes[node_id].type, migrated.nodes[node_id].name) == (node.type, node.name)
    df = _load_df(name)
    assert _signals(migrated, df) == _signals(fresh, df)


@pytest.mark.parametrize("name", _NAMES)
def test_parity_fixture_signals_identical_after_migration(name):
    """Every parity-fixture graph fires on exactly the same bars as before v2."""
    golden = _V1["signals"][name]
    df = _load_df(name)
    migrated = Graph.model_validate(_V1["api"][name])
    assert _signals(migrated, df) == golden
    assert _signals(auto_render(dict(_STRATEGIES)[name]), df) == golden


@pytest.mark.parametrize("name", sorted(_V1["botsjson"]))
def test_botsjson_shape_migrates(name):
    """bots.json stored model_dump(): "version", from_path/to_path, subgraph."""
    raw = _V1["botsjson"][name]
    assert raw["version"] == 1 and "from_path" in raw["wires"][0]
    g = Graph.model_validate(raw)
    assert g.model_dump(mode="json", by_alias=True) == Graph.model_validate(
        _V1["api"][name]
    ).model_dump(mode="json", by_alias=True)


def test_migration_is_idempotent():
    once = Graph.model_validate(_V1["api"]["macd_crossover"]).model_dump(mode="json", by_alias=True)
    twice = Graph.model_validate(once).model_dump(mode="json", by_alias=True)
    assert once == twice


def test_migration_does_not_mutate_input():
    raw = copy.deepcopy(_V1["api"]["simple_long_rsi"])
    before = copy.deepcopy(raw)
    Graph.model_validate(raw)
    assert raw == before


# ---------------------------------------------------------------------------
# migrate_v1_to_v2 details
# ---------------------------------------------------------------------------


def _uuid(i: int) -> str:
    return f"{i:08x}-1111-2222-3333-444455556666"


def test_v1_uuid_ids_get_type_names():
    nodes = {
        _uuid(1): {"id": _uuid(1), "type": "rsi", "subgraph": None},
        "/rsi1": {"id": "/rsi1", "type": "rsi"},  # wants rsi1, but it is already taken
        _uuid(2): {"id": _uuid(2), "type": "rsi"},
        _uuid(3): {"id": _uuid(3), "type": "crosses_above"},
    }
    g = Graph.model_validate({"_version": 1, "nodes": nodes, "wires": []})
    names = {k: n.name for k, n in g.nodes.items()}
    # Names are handed out in node order.
    assert names == {_uuid(1): "rsi1", "/rsi1": "rsi2", _uuid(2): "rsi3", _uuid(3): "crosses_above1"}


def test_v1_names_collide_after_sanitizing():
    nodes = {
        "/a.b": {"id": "/a.b", "type": "rsi"},
        "/a_b": {"id": "/a_b", "type": "rsi"},
    }
    g = Graph.model_validate({"_version": 1, "nodes": nodes, "wires": []})
    assert [n.name for n in g.nodes.values()] == ["a_b", "a_b1"]


def test_v1_subgraph_is_dropped_and_parent_is_root():
    out = migrate_v1_to_v2({
        "_version": 1,
        "nodes": {"/a": {"id": "/a", "type": "rsi", "subgraph": "/somewhere"}},
        "wires": [],
    })
    assert "subgraph" not in out["nodes"]["/a"]
    assert out["nodes"]["/a"]["parent"] is None
    assert out["_version"] == 2


def test_v1_ports_follow_wire_order_per_consumer():
    nodes = {p: {"id": p, "type": t} for p, t in
             [("/a", "rsi"), ("/b", "rsi"), ("/c", "rsi"), ("/and", "and"), ("/x", "above")]}
    wires = [
        {"id": "w1", "from": "/b", "to": "/and"},
        {"id": "w2", "from": "/a", "to": "/x"},
        {"id": "w3", "from": "/a", "to": "/and"},
        {"id": "w4", "from": "/c", "to": "/and"},
    ]
    g = Graph.model_validate({"_version": 1, "nodes": nodes, "wires": wires})
    assert [(w.id, w.to_port) for w in g.wires] == [
        ("w1", "in0"), ("w2", "in0"), ("w3", "in1"), ("w4", "in2")
    ]


def test_v1_wire_attr_becomes_the_consumer_param():
    """v1/v2 kept the read on the wire; v3 moves it into the consumer's read
    param (plan 2.B) and drops wire.attr."""
    g = Graph.model_validate({
        "_version": 1,
        "nodes": {"/a": {"id": "/a", "type": "ticker"}, "/b": {"id": "/b", "type": "rsi"}},
        "wires": [{"id": "w", "from": "/a", "to": "/b", "attr": "@close"}],
    })
    assert g.wires[0].attr is None
    assert g.nodes["/b"].params["source"] == "@close"


def test_graph_built_from_model_instances_migrates():
    """Tests and code build Graph(nodes={..Node..}, wires=[..Wire..]) with no _version."""
    g = Graph(
        nodes={"/a": Node(id="/a", type="rsi"), "/b": Node(id="/b", type="and")},
        wires=[Wire(**{"id": "w", "from": "/a", "to": "/b"})],
    )
    assert g.version == migrate.CURRENT_GRAPH_VERSION
    assert g.nodes["/a"].name == "a"
    assert g.wires[0].to_port == "in0"


def test_version_zero_is_rejected():
    with pytest.raises(IncompatibleGraphVersionError):
        migrate_graph_data({"_version": 0, "nodes": {}, "wires": []})


def test_future_version_is_refused_not_stripped():
    """BC-10 / DI-04: a graph newer than this code is refused (graph_invalid),
    never loaded with its unknown fields dropped."""
    from nodebuilder.diagnostics import code_for_error
    from nodebuilder.models import UnsupportedGraphVersionError

    for version in (migrate.CURRENT_GRAPH_VERSION + 1, 99):
        with pytest.raises(UnsupportedGraphVersionError, match="newer StrategyLab") as info:
            Graph.model_validate({"_version": version, "nodes": {}, "wires": [], "promoted": {}})
        assert code_for_error(info.value) == "graph_invalid"


@pytest.mark.parametrize("version", [True, False, 1.0, 2.0, "1", "2", None, [2]])
def test_non_int_version_is_refused(version):
    """BC-07 / DI-04: a version that is not a whole number is refused, not
    coerced to 1 (which skipped the name and port fill)."""
    from nodebuilder.models import UnsupportedGraphVersionError

    entry = {"id": "/a", "type": "rsi", "params": {}}
    with pytest.raises(UnsupportedGraphVersionError, match="whole number"):
        Graph.model_validate({"_version": version, "nodes": {"/a": entry}, "wires": []})


@pytest.mark.parametrize("patch", [
    {"nodes": [1, 2]},
    {"nodes": "abc"},
    {"wires": 5},
    {"wires": {"w1": {}}},
    {"wires": "abc"},
])
@pytest.mark.parametrize("version", [1, 2])
def test_malformed_nodes_or_wires_are_a_field_error(patch, version):
    """BC-01: garbage shapes reach pydantic (ValidationError, so graph_invalid
    everywhere) instead of a raw AttributeError or TypeError."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Graph.model_validate({"_version": version, "nodes": {}, "wires": [], **patch})


def test_unhashable_parent_or_target_is_a_field_error():
    """A list where a parent id or a wire target goes must not crash the fill."""
    from pydantic import ValidationError

    nodes = {"/a": {"id": "/a", "type": "rsi", "parent": ["x"]}}
    with pytest.raises(ValidationError):
        Graph.model_validate({"_version": 2, "nodes": nodes, "wires": []})
    nodes = {"/a": {"id": "/a", "type": "rsi", "name": ["x"]}}
    with pytest.raises(ValidationError):
        Graph.model_validate({"_version": 2, "nodes": nodes, "wires": []})
    wires = [{"id": "w", "from": "/a", "to": ["x"]}, {"id": "w2", "from": "/a", "to": "/a", "to_port": ["y"]}]
    with pytest.raises(ValidationError):
        Graph.model_validate({"_version": 2, "nodes": {"/a": {"id": "/a", "type": "rsi"}}, "wires": wires})


def test_non_dict_passes_through():
    assert migrate_graph_data(None) is None
    g = Graph()
    assert migrate_graph_data(g) is g


# ---------------------------------------------------------------------------
# Shared path vectors (vitest runs the same file: paths.vectors.test.ts)
# ---------------------------------------------------------------------------


def _vector_graph(key: str) -> Graph:
    return Graph.model_validate({"_version": 2, "wires": [], **_PATHS["graphs"][key]})


@pytest.mark.parametrize("case", _PATHS["is_valid_name"], ids=lambda c: repr(c["input"])[:20])
def test_vector_is_valid_name(case):
    assert is_valid_name(case["input"]) is case["expect"]


@pytest.mark.parametrize("case", _PATHS["sanitize_name"], ids=lambda c: repr(c["input"])[:20])
def test_vector_sanitize_name(case):
    assert sanitize_name(case["input"]) == case["expect"]
    assert is_valid_name(case["expect"])


@pytest.mark.parametrize("case", _PATHS["default_name"], ids=lambda c: c["id"][:20])
def test_vector_default_name(case):
    assert default_name(case["id"], case["type"]) == case["expect"]


@pytest.mark.parametrize("case", _PATHS["unique_name"], ids=lambda c: f"{c['base'][:12]}-{len(c['taken'])}")
def test_vector_unique_name(case):
    assert unique_name(case["base"], case["taken"]) == case["expect"]


@pytest.mark.parametrize("case", _PATHS["node_path"], ids=lambda c: c["id"])
def test_vector_node_path(case):
    assert node_path(_vector_graph(case["graph"]), case["id"]) == case["expect"]


@pytest.mark.parametrize("case", _PATHS["find_by_path"], ids=lambda c: c["why"])
def test_vector_find_by_path(case):
    g = _vector_graph(case["graph"])
    assert find_by_path(g, case["path"], relative_to=case["relative_to"]) == case["expect"]


@pytest.mark.parametrize("case", _PATHS["rename_node"], ids=lambda c: c["why"])
def test_vector_rename_node(case):
    g = _vector_graph(case["graph"])
    before = g.model_dump()
    expect = case["expect"]
    if "error" in expect:
        errors = {
            "name_invalid": InvalidNodeNameError,
            "name_duplicate": DuplicateNodeNameError,
            "node_not_found": NodeNotFoundError,
        }
        with pytest.raises(errors[expect["error"]]) as exc:
            rename_node(g, case["id"], case["new_name"])
        assert exc.value.code == expect["error"]
    else:
        renamed = rename_node(g, case["id"], case["new_name"])
        assert {i: node_path(renamed, i) for i in renamed.nodes} == expect["paths"]
        # The result is still a valid graph.
        Graph.model_validate(renamed.model_dump(by_alias=True))
    assert g.model_dump() == before, "rename_node must not change its input"


def test_rewrite_path_refs_is_a_stub_until_w7():
    """W6/W7 fill it; for now it must exist and change nothing."""
    g = _vector_graph("tree")
    before = g.model_dump()
    migrate._rewrite_path_refs(g, "/rsi", "/rsi_fast")
    assert g.model_dump() == before

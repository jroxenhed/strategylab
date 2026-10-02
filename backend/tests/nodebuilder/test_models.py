"""Unit 1 tests — Graph, Node, Wire models, path resolver, topological sort."""

import pytest

from pydantic import ValidationError

from nodebuilder.migrate import CURRENT_GRAPH_VERSION
from nodebuilder.models import (
    MIN_SUPPORTED_VERSION,
    STREAM_SCHEMA_VERSION,
    CyclicGraphError,
    DanglingWireError,
    DuplicateNodeNameError,
    Graph,
    IncompatibleGraphVersionError,
    InvalidNodeNameError,
    InvalidParentError,
    Node,
    Wire,
    resolve,
    topological_sort,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_node(path: str, node_type: str = "rsi") -> Node:
    return Node(id=path, type=node_type)


def make_wire(wire_id: str, from_path: str, to_path: str) -> Wire:
    return Wire(**{"id": wire_id, "from": from_path, "to": to_path})


def make_graph(**kwargs) -> Graph:
    """Convenience wrapper — avoids repeating Field alias dance in tests."""
    return Graph(**kwargs)


# ---------------------------------------------------------------------------
# Basic validation
# ---------------------------------------------------------------------------


def test_three_node_graph_validates():
    """Ticker → Indicator → Comparison: simple linear chain validates."""
    ticker = make_node("/ticker", "ticker")
    indicator = make_node("/rsi", "rsi")
    comparison = make_node("/above", "above")

    w1 = make_wire("w1", "/ticker", "/rsi")
    w2 = make_wire("w2", "/rsi", "/above")

    g = make_graph(
        nodes={"/ticker": ticker, "/rsi": indicator, "/above": comparison},
        wires=[w1, w2],
    )
    assert len(g.nodes) == 3
    assert len(g.wires) == 2


def test_empty_graph_validates():
    """Graph with no nodes and no wires is valid."""
    g = make_graph()
    assert g.nodes == {}
    assert g.wires == []


def test_single_node_graph_validates():
    """One node, no wires."""
    node = make_node("/ticker", "ticker")
    g = make_graph(nodes={"/ticker": node})
    assert len(g.nodes) == 1
    assert g.wires == []


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


def test_cycle_detection_raises():
    """A→B→A must raise CyclicGraphError mentioning both node IDs."""
    a = make_node("/a")
    b = make_node("/b")
    w1 = make_wire("w1", "/a", "/b")
    w2 = make_wire("w2", "/b", "/a")

    with pytest.raises(CyclicGraphError) as exc_info:
        make_graph(nodes={"/a": a, "/b": b}, wires=[w1, w2])

    msg = str(exc_info.value)
    assert "/a" in msg
    assert "/b" in msg


def test_cycle_error_names_a_node_on_the_cycle():
    """BC-10: '/a_entry' is only fed by the /m <-> /n cycle and sorts first,
    but the highlighted node must be on the cycle itself."""
    nodes = {p: make_node(p) for p in ("/a_entry", "/m", "/n")}
    wires = [
        make_wire("w1", "/m", "/n"),
        make_wire("w2", "/n", "/m"),
        make_wire("w3", "/n", "/a_entry"),
    ]
    with pytest.raises(CyclicGraphError) as exc_info:
        make_graph(nodes=nodes, wires=wires)
    assert exc_info.value.node_id in ("/m", "/n")


# ---------------------------------------------------------------------------
# Dangling wire
# ---------------------------------------------------------------------------


def test_dangling_wire_raises():
    """Wire to a nonexistent node raises DanglingWireError."""
    a = make_node("/a")
    w = make_wire("w1", "/a", "/nonexistent")

    with pytest.raises(DanglingWireError):
        make_graph(nodes={"/a": a}, wires=[w])


# ---------------------------------------------------------------------------
# Node id ↔ key consistency
# ---------------------------------------------------------------------------


def test_node_id_mismatch_raises():
    """nodes['/a'] = Node(id='/b') must raise a ValueError."""
    node_with_wrong_id = make_node("/b")  # id="/b" but stored under key "/a"

    with pytest.raises(Exception):  # ValueError from Pydantic validator
        make_graph(nodes={"/a": node_with_wrong_id})


# ---------------------------------------------------------------------------
# Version checks
# ---------------------------------------------------------------------------


def test_version_below_min_raises():
    """_version=0 raises IncompatibleGraphVersionError with both numbers."""
    data = {"_version": 0, "nodes": {}, "wires": []}

    with pytest.raises(IncompatibleGraphVersionError) as exc_info:
        Graph.load(data)

    msg = str(exc_info.value)
    assert "0" in msg
    assert str(MIN_SUPPORTED_VERSION) in msg


def test_version_at_min_loads():
    """_version == MIN_SUPPORTED_VERSION loads, and is migrated up to the current version."""
    data = {"_version": MIN_SUPPORTED_VERSION, "nodes": {}, "wires": []}
    g = Graph.load(data)
    assert g.version == CURRENT_GRAPH_VERSION
    assert g.stream_schema == STREAM_SCHEMA_VERSION


def test_version_below_min_raises_on_every_load_path():
    """The version floor lives in the migration hook, so model_validate checks it too."""
    with pytest.raises(IncompatibleGraphVersionError):
        Graph.model_validate({"_version": 0, "nodes": {}, "wires": []})


def test_version_newer_than_this_code_is_refused():
    """A future _version is refused on every load path (BC-10): loading it
    would drop the fields this code does not know."""
    from nodebuilder.models import UnsupportedGraphVersionError

    data = {"_version": 99, "nodes": {}, "wires": []}
    with pytest.raises(UnsupportedGraphVersionError):
        Graph.load(data)
    with pytest.raises(UnsupportedGraphVersionError):
        Graph.model_validate(data)


# ---------------------------------------------------------------------------
# One wire per input port (DI-05 / BC-05)
# ---------------------------------------------------------------------------


def _two_into(port_a: str, port_b: str) -> dict:
    return {
        "_version": 2,
        "nodes": {
            "/a": {"id": "/a", "type": "ticker"},
            "/b": {"id": "/b", "type": "ticker"},
            "/c": {"id": "/c", "type": "above"},
        },
        "wires": [
            {"id": "w1", "from": "/a", "to": "/c", "to_port": port_a},
            {"id": "w2", "from": "/b", "to": "/c", "to_port": port_b},
        ],
    }


def test_two_wires_on_one_port_are_refused():
    from nodebuilder.diagnostics import code_for_error
    from nodebuilder.models import DuplicatePortError

    with pytest.raises(DuplicatePortError) as info:
        Graph.model_validate(_two_into("in0", "in0"))
    assert info.value.node_id == "/c" and info.value.port == "in0"
    assert code_for_error(info.value) == "port_duplicate"
    Graph.model_validate(_two_into("in0", "in1"))  # distinct ports are fine


# ---------------------------------------------------------------------------
# Wires are written in port order (DI-01: a rollback reads list order)
# ---------------------------------------------------------------------------


def test_wires_are_written_in_port_order_per_consumer():
    data = _two_into("in1", "in0")
    data["nodes"]["/e"] = {"id": "/e", "type": "entry"}
    data["wires"].insert(0, {"id": "w0", "from": "/c", "to": "/e", "to_port": "in0"})
    g = Graph.model_validate(data)
    # In memory the list is untouched; compile reads by port anyway.
    assert [w.id for w in g.wires] == ["w0", "w1", "w2"]
    dumped = g.model_dump(mode="json", by_alias=True)
    # Only the two wires into /c swap; the wire into /e keeps its slot.
    assert [w["id"] for w in dumped["wires"]] == ["w0", "w2", "w1"]
    assert [w["to_port"] for w in dumped["wires"]] == ["in0", "in0", "in1"]
    # model_dump() (the bots.json form) is ordered the same way, and a
    # second round trip changes nothing.
    assert [w["id"] for w in g.model_dump()["wires"]] == ["w0", "w2", "w1"]
    again = Graph.model_validate(dumped).model_dump(mode="json", by_alias=True)
    assert again == dumped


def test_numbered_ports_are_written_as_numbers():
    from nodebuilder.models import port_ordered

    class W:
        def __init__(self, to_path, to_port):
            self.to_path, self.to_port = to_path, to_port

    wires = [W("/or", "in10"), W("/x", "in0"), W("/or", "in2"), W("/or", "in0"), W("/or", "odd")]
    out = port_ordered(wires)
    assert [(w.to_path, w.to_port) for w in out] == [
        ("/or", "in0"), ("/x", "in0"), ("/or", "in2"), ("/or", "in10"), ("/or", "odd"),
    ]


# ---------------------------------------------------------------------------
# Path resolver
# ---------------------------------------------------------------------------

_RESOLVE_CASES = [
    # (from_path, ref, expected)
    ("/a/b/c", "../d", "/a/d"),
    ("/a/b/c", "./d", "/a/b/d"),
    ("/a/b/c", "/x", "/x"),
    ("/a/b/c", "d", "/a/b/d"),
    # chained ../
    ("/a/b/c/d", "../../e", "/a/e"),
    # bare name at root child
    ("/ticker", "rsi", "/rsi"),
    # absolute with redundant segments (normalise)
    ("/a/b/c", "/x/./y", "/x/y"),
]


@pytest.mark.parametrize("from_path,ref,expected", _RESOLVE_CASES)
def test_path_resolver(from_path: str, ref: str, expected: str):
    assert resolve(from_path, ref) == expected


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------


def test_topological_sort_basic():
    """4-node DAG: /src → /mid1, /src → /mid2, /mid1 → /sink, /mid2 → /sink."""
    src = make_node("/src", "ticker")
    mid1 = make_node("/mid1", "rsi")
    mid2 = make_node("/mid2", "sma")
    sink = make_node("/sink", "above")

    g = make_graph(
        nodes={
            "/src": src,
            "/mid1": mid1,
            "/mid2": mid2,
            "/sink": sink,
        },
        wires=[
            make_wire("w1", "/src", "/mid1"),
            make_wire("w2", "/src", "/mid2"),
            make_wire("w3", "/mid1", "/sink"),
            make_wire("w4", "/mid2", "/sink"),
        ],
    )

    order = topological_sort(g)
    ids = [n.id for n in order]

    # /src must come before /mid1 and /mid2; both must come before /sink
    assert ids.index("/src") < ids.index("/mid1")
    assert ids.index("/src") < ids.index("/mid2")
    assert ids.index("/mid1") < ids.index("/sink")
    assert ids.index("/mid2") < ids.index("/sink")


def test_topological_sort_stable_order():
    """Two parallel siblings (no edges between them) ordered by id."""
    a = make_node("/alpha", "rsi")
    b = make_node("/beta", "sma")
    root = make_node("/root", "ticker")

    g = make_graph(
        nodes={"/root": root, "/alpha": a, "/beta": b},
        wires=[
            make_wire("w1", "/root", "/alpha"),
            make_wire("w2", "/root", "/beta"),
        ],
    )

    order = topological_sort(g)
    ids = [n.id for n in order]

    # Both alpha and beta come after root; alpha < beta lexicographically
    assert ids[0] == "/root"
    assert ids.index("/alpha") < ids.index("/beta")


# ---------------------------------------------------------------------------
# Round-trip by alias
# ---------------------------------------------------------------------------


def test_round_trip_by_alias():
    """model_dump(by_alias=True) → Graph(**...) → model_dump(by_alias=True) is identity."""
    ticker = make_node("/ticker", "ticker")
    rsi = make_node("/rsi", "rsi")
    w = make_wire("w1", "/ticker", "/rsi")

    g = make_graph(nodes={"/ticker": ticker, "/rsi": rsi}, wires=[w])

    dumped = g.model_dump(by_alias=True)
    g2 = Graph(**dumped)
    assert g2.model_dump(by_alias=True) == dumped


# ---------------------------------------------------------------------------
# Wire from/to alias interchangeability
# ---------------------------------------------------------------------------


def test_wire_from_to_aliases():
    """Wire built via alias {'from':..., 'to':...} and via Python names are equal."""
    via_alias = Wire(**{"id": "w1", "from": "/a", "to": "/b"})
    via_names = Wire(id="w1", from_path="/a", to_path="/b")

    assert via_alias.from_path == via_names.from_path == "/a"
    assert via_alias.to_path == via_names.to_path == "/b"
    assert via_alias.model_dump(by_alias=True) == via_names.model_dump(by_alias=True)


# ---------------------------------------------------------------------------
# Schema v2 fields (W1 item 1.A)
# ---------------------------------------------------------------------------


def _v2(nodes: dict, wires: list | None = None, **extra) -> dict:
    return {"_version": 2, "nodes": nodes, "wires": wires or [], **extra}


def _n(node_id: str, name: str, parent: str | None = None, node_type: str = "rsi") -> dict:
    return {"id": node_id, "type": node_type, "name": name, "parent": parent}


def test_v2_defaults_on_a_bare_graph():
    g = Graph()
    assert g.version == CURRENT_GRAPH_VERSION
    assert g.stream_schema == STREAM_SCHEMA_VERSION == 1
    assert g.meta == {}
    assert g.annotations.boxes == [] and g.annotations.notes == []


def test_v2_dump_shape_matches_contract():
    """Section 4.1: the by-alias dump carries every v2 key."""
    g = Graph.model_validate(_v2(
        {"n_t": _n("n_t", "aapl", node_type="ticker"), "n_r": _n("n_r", "rsi")},
        [{"id": "w1", "from": "n_t", "to": "n_r", "from_port": "out", "to_port": "in0"}],
    ))
    d = g.model_dump(by_alias=True)
    assert set(d) == {"_version", "stream_schema", "readOnly", "meta", "nodes", "wires", "annotations"}
    assert set(d["nodes"]["n_r"]) == {"id", "type", "name", "parent", "params", "position", "display", "bypass"}
    assert d["wires"][0]["from_port"] == "out" and d["wires"][0]["to_port"] == "in0"
    assert "subgraph" not in d["nodes"]["n_r"]


def test_v2_round_trips_through_both_dump_shapes():
    """by_alias (API) and plain (bots.json) dumps both load back unchanged."""
    g = Graph.model_validate(_v2(
        {"n_t": _n("n_t", "aapl", node_type="ticker"), "n_r": _n("n_r", "rsi")},
        [{"id": "w1", "from": "n_t", "to": "n_r"}],
        meta={"notes": "hi", "n": 3, "flag": True, "x": 1.5},
        annotations={"boxes": [{"id": "b1", "label": "REGIME", "color": "blue",
                                "rect": [1, 2, 3, 4], "members": ["n_t"], "parent": None}],
                     "notes": [{"id": "s1", "text": "note", "rect": [0, 0, 10, 10], "color": "amber"}]},
    ))
    api = g.model_dump(mode="json", by_alias=True)
    assert Graph.model_validate(api).model_dump(mode="json", by_alias=True) == api
    plain = g.model_dump(mode="json")
    assert "version" in plain  # the bots.json shape
    assert Graph.model_validate(plain).model_dump(mode="json", by_alias=True) == api
    assert api["meta"] == {"notes": "hi", "n": 3, "flag": True, "x": 1.5}


def test_meta_key_limit():
    with pytest.raises(ValidationError, match="at most 32"):
        Graph.model_validate(_v2({}, meta={f"k{i}": i for i in range(33)}))
    Graph.model_validate(_v2({}, meta={f"k{i}": i for i in range(32)}))


def test_meta_rejects_nested_values():
    with pytest.raises(ValidationError):
        Graph.model_validate(_v2({}, meta={"a": {"b": 1}}))


def test_from_port_is_always_out():
    with pytest.raises(ValidationError):
        Wire(**{"id": "w", "from": "a", "to": "b", "from_port": "in0"})


@pytest.mark.parametrize("bad", ["RSI", "1rsi", "rsi-fast", "a" * 65, "ok name"])
def test_invalid_name_raises(bad):
    with pytest.raises(InvalidNodeNameError) as exc:
        Graph.model_validate(_v2({"n_a": _n("n_a", bad)}))
    assert exc.value.node_id == "n_a"
    assert exc.value.code == "name_invalid"


def test_duplicate_sibling_name_raises():
    with pytest.raises(DuplicateNodeNameError) as exc:
        Graph.model_validate(_v2({"n_a": _n("n_a", "rsi"), "n_b": _n("n_b", "rsi")}))
    assert exc.value.node_id == "n_b"
    assert exc.value.code == "name_duplicate"


def test_same_name_under_different_parents_is_fine():
    g = Graph.model_validate(_v2({
        "n_net1": _n("n_net1", "net1", node_type="subnet"),
        "n_net2": _n("n_net2", "net2", node_type="subnet"),
        "n_a": _n("n_a", "rsi", parent="n_net1"),
        "n_b": _n("n_b", "rsi", parent="n_net2"),
    }))
    assert g.nodes["n_a"].name == g.nodes["n_b"].name == "rsi"


def test_missing_parent_raises():
    with pytest.raises(InvalidParentError) as exc:
        Graph.model_validate(_v2({"n_a": _n("n_a", "rsi", parent="n_gone")}))
    assert exc.value.node_id == "n_a"


def test_parent_cycle_raises():
    with pytest.raises(InvalidParentError):
        Graph.model_validate(_v2({
            "n_a": _n("n_a", "a", parent="n_b"),
            "n_b": _n("n_b", "b", parent="n_a"),
        }))


def test_v2_missing_names_and_ports_are_filled():
    """A v2 graph from the editor with a blank name or port still loads."""
    g = Graph.model_validate(_v2(
        {"n_k3x9a0bq": {"id": "n_k3x9a0bq", "type": "and"},
         "n_a": _n("n_a", "and1"),
         "n_b": _n("n_b", "cmp")},
        [{"id": "w1", "from": "n_b", "to": "n_k3x9a0bq", "to_port": "in0"},
         {"id": "w2", "from": "n_a", "to": "n_k3x9a0bq"}],
    ))
    assert g.nodes["n_k3x9a0bq"].name == "and2"  # and1 is taken
    assert [w.to_port for w in g.wires] == ["in0", "in1"]


def test_python_built_graph_keeps_explicit_names_and_parents():
    """Graph(nodes=...) built in code (no _version) keeps names and parents it was given."""
    g = Graph(nodes={
        "net": Node(id="net", type="subnet", name="net"),
        "x": Node(id="x", type="rsi", name="fast", parent="net"),
        "y": Node(id="y", type="rsi"),
    })
    assert g.nodes["x"].name == "fast" and g.nodes["x"].parent == "net"
    assert g.nodes["y"].name == "y"

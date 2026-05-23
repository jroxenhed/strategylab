"""Unit 1 tests — Graph, Node, Wire models, path resolver, topological sort."""

import pytest

from nodebuilder.models import (
    MIN_SUPPORTED_VERSION,
    CyclicGraphError,
    DanglingWireError,
    Graph,
    IncompatibleGraphVersionError,
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
    """_version == MIN_SUPPORTED_VERSION loads without error."""
    data = {"_version": MIN_SUPPORTED_VERSION, "nodes": {}, "wires": []}
    g = Graph.load(data)
    assert g.version == MIN_SUPPORTED_VERSION


def test_version_above_min_loads():
    """Future _version=99 loads fine (additive-field tolerance)."""
    data = {"_version": 99, "nodes": {}, "wires": []}
    g = Graph.load(data)
    assert g.version == 99


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

"""Graph, Node, Wire Pydantic models + path resolver + topological sort.

Unit 1 — pure data types, no evaluator, no compiler, no indicator deps.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Literal, Optional, Union

from pydantic import (
    ConfigDict,
    Field,
    StrictInt,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Version floor
# ---------------------------------------------------------------------------

MIN_SUPPORTED_VERSION: int = 1

# Version of the stream format (plan section 3).  The source of truth is
# nodebuilder/kernel/stream.py; it is re-exported here for older imports.
from nodebuilder.kernel.stream import STREAM_SCHEMA_VERSION  # noqa: E402,F401

_META_MAX_KEYS = 32


# ---------------------------------------------------------------------------
# Custom error hierarchy
# ---------------------------------------------------------------------------


class GraphValidationError(Exception):
    """Base class for all graph validation errors.

    node_id names the node the error is about, so the editor can highlight it.
    It is None when the error is about the graph as a whole.
    """

    def __init__(self, message: str = "", node_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.node_id = node_id


class CyclicGraphError(GraphValidationError):
    """Raised when the graph contains one or more directed cycles."""


class DanglingWireError(GraphValidationError):
    """Raised when a wire references a node path that does not exist."""


class IncompatibleGraphVersionError(GraphValidationError):
    """Raised when the stored _version is below MIN_SUPPORTED_VERSION."""

    def __init__(self, actual: int, minimum: int) -> None:
        super().__init__(
            f"Graph _version={actual} < MIN_SUPPORTED_VERSION={minimum}"
        )
        self.actual = actual
        self.minimum = minimum


class UnsupportedGraphVersionError(GraphValidationError):
    """The _version is not a whole number, or is newer than this code knows.

    A newer graph is refused, never loaded: loading it would drop the fields
    this code does not know, and a save would then lose them for good.
    """

    code = "graph_invalid"


class DuplicatePortError(GraphValidationError):
    """Two wires go into the same input port of one node."""

    code = "port_duplicate"

    def __init__(self, message: str, node_id: str, port: str) -> None:
        super().__init__(message, node_id=node_id)
        self.port = port


class ReadOnlyGraphError(GraphValidationError):
    """Raised when a mutation is attempted on a readOnly graph (Unit 5)."""


class InvalidNodeNameError(GraphValidationError):
    """A node name does not match ^[a-z_][a-z0-9_]{0,63}$."""

    code = "name_invalid"


class DuplicateNodeNameError(GraphValidationError):
    """Two nodes with the same parent share a name."""

    code = "name_duplicate"


class InvalidParentError(GraphValidationError):
    """A node's parent does not exist, or its parents loop back to it."""


class WireCrossesNetworkError(GraphValidationError):
    """A wire joins two nodes that sit in different networks (plan D7).

    Wires connect siblings only.  A stream enters a network through the
    network node's input ports and leaves through its output.  node_id is
    the node the wire goes into, port its input port.
    """

    code = "wire_crosses_network"

    def __init__(self, message: str, node_id: str, port: Optional[str]) -> None:
        super().__init__(message, node_id=node_id)
        self.port = port


class ReservedNodeIdError(GraphValidationError):
    """A stored node id contains ``::`` outside an asset instance's copy."""

    code = "graph_invalid"


class ReservedWireIdError(GraphValidationError):
    """A stored wire id contains ``::`` outside an asset instance's copy.
    Such ids belong to the wires of an expanded asset; a second wire with
    the same id would be dropped or inspected in its place (KA-6)."""

    code = "graph_invalid"


class LockedInstanceChildError(GraphValidationError):
    """A node sits inside a locked asset instance.  A locked instance stores
    no children: they come from the library when the graph compiles."""

    code = "graph_invalid"


# Separator of composite ids: an asset instance's children get the id
# ``outerId::innerId`` when the instance is expanded (plan D3, D7, W6).  A
# stored node id may contain it only inside an unlocked asset instance
# (a baked bot snapshot, or a local copy), where the instance is the part
# before the last ``::`` (see _composite_id_ok).
COMPOSITE_ID_SEP = "::"

SUBNET_TYPE = "subnet"
"""The only node type that can be a library asset instance (W6)."""

ASSET_NAME_PATTERN = r"^[a-z_][a-z0-9_]{0,63}$"
"""A library asset name (plan W6 contracts)."""

_NODE_META_MAX_KEYS = 32
_NODE_VIEWS = ("frame", "card")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class PromotedParam(BaseModel):
    """A param of a node inside a network, shown and edited on the network
    node itself (W6, Houdini's promoted parameters).

    The value lives on the network node in ``params[name]``; when it is
    missing, ``default`` is used.  ``target`` is the path of the inner param,
    relative to the network node: ``sma/period`` is param ``period`` of the
    child ``sma``.  kernel/flatten.py puts the value into the target before
    compile.  Bad names, targets and values are ``promoted_invalid``
    diagnostics, not load errors, so the graph still opens.
    """

    name: str
    label: str = ""
    target: str
    type: str
    default: Any = None


class AssetRef(BaseModel):
    """Which library asset, and which version of it, an instance uses."""

    name: str = Field(pattern=ASSET_NAME_PATTERN)
    version: StrictInt = Field(ge=1)


class Node(BaseModel):
    """A single node in the strategy graph."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    """Stable, opaque id, e.g. /ticker_aapl_1d or n_k3x9a0bq.  Wires use it."""

    type: str
    """Catalog node name, e.g. 'rsi'."""

    name: str = ""
    """Leaf of the node's path, unique among its siblings.  Empty only on a
    bare Node; Graph fills it in on load (see migrate.py)."""

    parent: Optional[str] = None
    """Id of the network node this node sits in; None means root.  Replaces
    the unused v1 field subgraph.  Wires connect only nodes with the same
    parent (plan D7); kernel/flatten.py turns the nested graph into one flat
    graph before compile."""

    params: dict[str, Any] = Field(default_factory=dict)
    position: tuple[float, float] = (0.0, 0.0)
    display: bool = False
    bypass: bool = False

    meta: dict[str, Any] = Field(default_factory=dict)
    """Editor view state (W6, FA1).  Compile, flatten and the cook never read
    it; save, load, migrate and copy keep it as it is.  Known keys: ``view``
    ("frame" or "card"; missing means "frame") and ``note`` (the node's note
    in the Inspector)."""

    promoted: list[PromotedParam] = Field(default_factory=list)
    """Promoted params of a network node (W6).  Empty on other nodes."""

    asset_ref: Optional[AssetRef] = None
    """On a subnet that is a library asset instance: the asset and the
    version it pins.  A newer library version never changes it."""

    locked: bool = False
    """A locked instance stores no children; compile takes them from the
    library (kernel/assets.py).  An unlocked instance stores its children as
    a local copy and keeps asset_ref to say where they came from."""

    @field_validator("meta")
    @classmethod
    def _meta_shape(cls, v: dict) -> dict:
        if len(v) > _NODE_META_MAX_KEYS:
            raise ValueError(f"meta has {len(v)} keys; at most {_NODE_META_MAX_KEYS} are allowed")
        if "view" in v and v["view"] not in _NODE_VIEWS:
            raise ValueError(f"meta.view must be 'frame' or 'card', got {v['view']!r}")
        if "note" in v and not isinstance(v["note"], str):
            raise ValueError("meta.note must be text")
        return v

    @model_validator(mode="after")
    def _asset_fields(self) -> "Node":
        if (self.asset_ref is not None or self.locked) and self.type != SUBNET_TYPE:
            raise ValueError(
                f"Only a subnet can be a library asset instance; {self.type!r} has "
                "asset_ref or locked set."
            )
        if self.locked and self.asset_ref is None:
            raise ValueError("A locked asset instance needs asset_ref (the asset and version).")
        return self

    @model_serializer(mode="wrap")
    def _leave_out_empty_w6_fields(self, handler):
        """Leave the W6 fields out while they hold their defaults, so a graph
        that does not use them saves exactly as it did before W6."""
        data = handler(self)
        if isinstance(data, dict):
            for key, empty in (("meta", {}), ("promoted", []), ("asset_ref", None),
                               ("locked", False)):
                if key in data and data[key] == empty:
                    del data[key]
        return data


class Wire(BaseModel):
    """A directed edge from one node's output to an input port of another.

    A wire carries the whole stream of its source node (plan D4).  It does
    not pick an attribute: the consumer's ``attr`` / ``attr_list`` params do.
    An empty read param defaults to the primary write of the node on the
    matching port, so the port matters only for those defaults; once a param
    names its operand, moving or redrawing the wire never changes it.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str

    from_path: str = Field(alias="from")
    """Source node path."""

    to_path: str = Field(alias="to")
    """Destination node path."""

    from_port: Literal["out"] = "out"
    """Every node has one output."""

    to_port: Optional[str] = None
    """Input port on the destination, 'in0', 'in1'...  None only on a bare
    Wire; Graph fills it in on load in wire order (see migrate.py)."""

    attr: Optional[str] = None
    """Legacy (v1/v2) attribute label.  The v3 migration turns it into the
    consumer's read param.  Until a graph is migrated, compile reads it only
    for a consumer param that is empty: a label the source node knows (a
    Ticker field, a MACD or Bollinger output) picks that output; any other
    label reads the source's primary write."""


class NetworkBox(BaseModel):
    """A labelled box drawn around a group of nodes."""

    id: str
    label: str = ""
    color: str = ""
    rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    members: list[str] = Field(default_factory=list)
    parent: Optional[str] = None


class StickyNote(BaseModel):
    """A free text note on the canvas."""

    id: str
    text: str = ""
    rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    color: str = ""
    parent: Optional[str] = None


class Annotations(BaseModel):
    """Canvas-only extras.  They never change what the graph computes."""

    boxes: list[NetworkBox] = Field(default_factory=list)
    notes: list[StickyNote] = Field(default_factory=list)


class Graph(BaseModel):
    """The top-level strategy graph."""

    model_config = ConfigDict(populate_by_name=True)

    version: int = Field(alias="_version", default=2)
    stream_schema: int = STREAM_SCHEMA_VERSION
    readOnly: bool = False
    meta: dict[str, Union[bool, int, float, str]] = Field(default_factory=dict)
    """Free notes about the graph (at most 32 keys)."""
    nodes: dict[str, Node] = Field(default_factory=dict)
    wires: list[Wire] = Field(default_factory=list)
    annotations: Annotations = Field(default_factory=Annotations)

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _migrate(cls, data: Any) -> Any:
        """Upgrade older stored formats before any field is read (plan D2)."""
        from nodebuilder.migrate import migrate_graph_data

        return migrate_graph_data(data)

    @field_validator("meta")
    @classmethod
    def _meta_size(cls, v: dict) -> dict:
        if len(v) > _META_MAX_KEYS:
            raise ValueError(f"meta has {len(v)} keys; at most {_META_MAX_KEYS} are allowed")
        return v

    @model_validator(mode="after")
    def _validate_graph(self) -> "Graph":
        from nodebuilder.migrate import name_issues

        # 1. node.id must match its dict key
        for key, node in self.nodes.items():
            if node.id != key:
                raise ValueError(
                    f"Node id mismatch: nodes[{key!r}].id == {node.id!r}"
                )

        # 1a. "::" is kept for the ids of nodes inside library asset
        # instances (W6).  A stored one is allowed only in an unlocked
        # instance's copy (a baked bot snapshot or a local copy).
        for key in self.nodes:
            if COMPOSITE_ID_SEP in key and not _composite_id_ok(self.nodes, key):
                raise ReservedNodeIdError(
                    f"Node id {key!r} contains {COMPOSITE_ID_SEP!r}, which is kept "
                    "for the ids of nodes inside library assets.",
                    node_id=key,
                )

        # 1b. Names are valid and unique among siblings; parents exist and do
        # not loop.  /validate lists every issue; loading stops at the first.
        issues = name_issues(self.nodes)
        if issues:
            first = issues[0]
            error_cls = {
                "name_invalid": InvalidNodeNameError,
                "name_duplicate": DuplicateNodeNameError,
            }.get(first["code"], InvalidParentError)
            raise error_cls(first["message"], node_id=first["node_id"])

        # 1c. A locked asset instance stores no children (W6).
        for key, node in self.nodes.items():
            parent = self.nodes.get(node.parent) if node.parent is not None else None
            if parent is not None and parent.locked:
                raise LockedInstanceChildError(
                    f"Node {key!r} sits inside the locked asset instance {parent.id!r}.  "
                    "A locked instance stores no children; unlock it to edit a local copy.",
                    node_id=key,
                )

        node_paths = set(self.nodes.keys())

        # 2. No dangling wires
        for wire in self.wires:
            missing = []
            if wire.from_path not in node_paths:
                missing.append(f"from_path={wire.from_path!r}")
            if wire.to_path not in node_paths:
                missing.append(f"to_path={wire.to_path!r}")
            if missing:
                # Point at the end of the wire that does exist, if any.
                known_end = next(
                    (p for p in (wire.from_path, wire.to_path) if p in node_paths), None
                )
                raise DanglingWireError(
                    f"Wire {wire.id!r} references unknown node(s): {', '.join(missing)}",
                    node_id=known_end,
                )

        # 2a. "::" in a wire id is kept for the wires of an expanded asset
        # (W6, KA-6): allowed only on a wire inside the unlocked instance
        # its prefix names.
        for wire in self.wires:
            if COMPOSITE_ID_SEP in wire.id and not _composite_wire_id_ok(self.nodes, wire):
                raise ReservedWireIdError(
                    f"Wire id {wire.id!r} contains {COMPOSITE_ID_SEP!r}, which is kept "
                    "for the wires inside library assets.",
                    node_id=wire.to_path,
                )

        # 2b. One wire per input port.  With two wires on one port, which one
        # is the left side of a comparison would depend on list order.
        seen_ports: set[tuple[str, str]] = set()
        for wire in self.wires:
            if not wire.to_port:
                continue
            key = (wire.to_path, wire.to_port)
            if key in seen_ports:
                raise DuplicatePortError(
                    f"Input {wire.to_port} of {wire.to_path!r} has more than one wire.",
                    node_id=wire.to_path,
                    port=wire.to_port,
                )
            seen_ports.add(key)

        # 2c. Wires connect siblings only (plan D7).
        crossing = network_wire_issues(self.nodes, self.wires)
        if crossing:
            first = crossing[0]
            raise WireCrossesNetworkError(first["message"], first["node_id"], first["port"])

        # 3. No cycles
        _assert_acyclic(self)

        return self

    @field_serializer("wires", mode="wrap")
    def _wires_in_port_order(self, wires: list, handler):
        """Write each node's input wires in port order (in0 before in1).

        Compile reads inputs by port, but Wave 0 code read them by list
        order.  Writing them in port order means a rollback computes the
        same comparison.  Only wires into the same node swap places; every
        other wire keeps its slot.
        """
        return handler(port_ordered(wires))

    # ------------------------------------------------------------------
    # Factory: load with version check
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, data: dict) -> "Graph":
        """Deserialise from a dict.  The migration hook checks the version
        (floor, type and newest known)."""
        return cls.model_validate(data)


# ---------------------------------------------------------------------------
# Wire order
# ---------------------------------------------------------------------------


def port_index(port: Optional[str]) -> Optional[int]:
    """``in3`` -> 3.  None for a missing or non-numbered port."""
    if isinstance(port, str) and port.startswith("in") and port[2:].isdigit():
        return int(port[2:])
    return None


def port_ordered(wires: list) -> list:
    """*wires* with each node's inputs sorted by port, in the slots they held.

    Numbered ports come first in port order, then any other port in list
    order (the same order compile reads them in).  Wires into different
    nodes never move relative to each other, so a graph already in port
    order comes back unchanged.
    """
    slots: dict[Any, list[int]] = {}
    for i, wire in enumerate(wires):
        slots.setdefault(getattr(wire, "to_path", None), []).append(i)
    out = list(wires)
    for indexes in slots.values():
        if len(indexes) < 2:
            continue

        def _key(i: int) -> tuple:
            k = port_index(getattr(wires[i], "to_port", None))
            return (k is None, k if k is not None else 0, i)

        for slot, src in zip(indexes, sorted(indexes, key=_key)):
            out[slot] = wires[src]
    return out


# ---------------------------------------------------------------------------
# Networks
# ---------------------------------------------------------------------------


CROSSING_HINT = "Wires connect nodes in the same network. Route through a network port."


def network_wire_issues(nodes: dict, wires: list) -> list[dict]:
    """Every wire that joins two nodes in different networks, in wire order.

    Each item is ``{"code": "wire_crosses_network", "node_id", "port",
    "wire_id", "message"}``; node_id is the node the wire goes into.  Two
    nodes are in the same network when they have the same ``parent``.  A
    wire with an end that does not exist is skipped (that is a dangling
    wire, reported on its own).  *nodes* maps id to anything with a
    ``parent`` attribute.
    """
    issues: list[dict] = []
    for wire in wires:
        src = nodes.get(wire.from_path)
        dst = nodes.get(wire.to_path)
        if src is None or dst is None or src.parent == dst.parent:
            continue
        issues.append({
            "code": "wire_crosses_network",
            "node_id": wire.to_path,
            "port": wire.to_port,
            "wire_id": wire.id,
            "message": (
                f"Wire {wire.id!r} goes from {wire.from_path!r} ({_where(src.parent)}) "
                f"to {wire.to_path!r} ({_where(dst.parent)}).  {CROSSING_HINT}"
            ),
        })
    return issues


def _where(parent: Optional[str]) -> str:
    return "at the root" if parent is None else f"inside {parent!r}"


def _composite_id_ok(nodes: dict, node_id: str) -> bool:
    """True when a stored id with ``::`` belongs to an unlocked asset copy.

    That is the case when the part before the last ``::`` is the id of an
    unlocked subnet with asset_ref, and that subnet holds the node (it is
    one of the node's parents, at any depth).  kernel/assets.py gives the
    children of an instance exactly such ids, so a baked bot snapshot
    stores and reloads the same ids the backtest compiled.
    """
    owner_id = node_id.rsplit(COMPOSITE_ID_SEP, 1)[0]
    owner = nodes.get(owner_id)
    if owner is None or owner.type != SUBNET_TYPE or owner.asset_ref is None or owner.locked:
        return False
    return _inside(nodes, node_id, owner_id)


def _composite_wire_id_ok(nodes: dict, wire: Any) -> bool:
    """True when a stored wire id with ``::`` belongs to an unlocked asset
    copy: the part before the last ``::`` is an unlocked subnet with
    asset_ref, and both ends of the wire sit inside it (at any depth)."""
    owner_id = wire.id.rsplit(COMPOSITE_ID_SEP, 1)[0]
    owner = nodes.get(owner_id)
    if owner is None or owner.type != SUBNET_TYPE or owner.asset_ref is None or owner.locked:
        return False
    return all(_inside(nodes, end, owner_id) for end in (wire.from_path, wire.to_path))


def _inside(nodes: dict, node_id: str, owner_id: str) -> bool:
    """True when *owner_id* is one of *node_id*'s parents, at any depth."""
    if node_id not in nodes:
        return False
    seen: set[str] = {node_id}
    current = nodes[node_id].parent
    while current is not None and current not in seen and current in nodes:
        if current == owner_id:
            return True
        seen.add(current)
        current = nodes[current].parent
    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _assert_acyclic(graph: Graph) -> None:
    """Kahn's topological sort to detect cycles; raises CyclicGraphError."""
    # Build adjacency and in-degree maps
    in_degree: dict[str, int] = {path: 0 for path in graph.nodes}
    adj: dict[str, list[str]] = {path: [] for path in graph.nodes}

    for wire in graph.wires:
        adj[wire.from_path].append(wire.to_path)
        in_degree[wire.to_path] += 1

    queue: deque[str] = deque(
        sorted(path for path, deg in in_degree.items() if deg == 0)
    )
    visited: set[str] = set()

    while queue:
        path = queue.popleft()
        visited.add(path)
        for neighbour in sorted(adj[path]):
            in_degree[neighbour] -= 1
            if in_degree[neighbour] == 0:
                queue.append(neighbour)

    remaining = set(graph.nodes.keys()) - visited
    if remaining:
        raise CyclicGraphError(
            f"Graph contains a cycle involving node(s): {sorted(remaining)}",
            node_id=_node_on_cycle(graph, remaining),
        )


def _node_on_cycle(graph: Graph, remaining: set[str]) -> str:
    """Return a node that sits on a cycle.

    Kahn's leftover set also holds nodes that are only fed by a cycle (an
    Entry below it, say).  Every leftover node has a leftover input, so
    walking inputs backwards must come round to a node seen before, and that
    node is on the cycle.
    """
    preds: dict[str, list[str]] = {p: [] for p in remaining}
    for wire in graph.wires:
        if wire.to_path in remaining and wire.from_path in remaining:
            preds[wire.to_path].append(wire.from_path)
    node = sorted(remaining)[0]
    seen: set[str] = set()
    while node not in seen:
        seen.add(node)
        node = sorted(preds[node])[0]
    return node


# ---------------------------------------------------------------------------
# Public topological sort
# ---------------------------------------------------------------------------


def topological_sort(graph: Graph) -> list[Node]:
    """Return nodes in topological order (Kahn's algorithm).

    Nodes within the same layer are ordered by node.id for stability.
    The graph must already be validated (no cycles, no dangling wires).
    """
    in_degree: dict[str, int] = {path: 0 for path in graph.nodes}
    adj: dict[str, list[str]] = {path: [] for path in graph.nodes}

    for wire in graph.wires:
        adj[wire.from_path].append(wire.to_path)
        in_degree[wire.to_path] += 1

    queue: deque[str] = deque(
        sorted(path for path, deg in in_degree.items() if deg == 0)
    )
    result: list[Node] = []

    while queue:
        path = queue.popleft()
        result.append(graph.nodes[path])
        for neighbour in sorted(adj[path]):
            in_degree[neighbour] -= 1
            if in_degree[neighbour] == 0:
                queue.append(neighbour)

    return result


# ---------------------------------------------------------------------------
# Path resolver
# ---------------------------------------------------------------------------


def resolve(from_path: str, ref: str) -> str:
    """Resolve *ref* relative to *from_path*.

    Rules
    -----
    - Absolute ref (starts with ``/``) → return normalised ref.
    - ``./name``  → same directory as *from_path*.
    - ``../name`` → parent directory of *from_path* (chainable).
    - Bare ``name`` (no slash prefix) → treated as ``./name``.

    The result is always normalised: leading ``/``, no trailing ``/``,
    no redundant ``..`` segments.

    Examples
    --------
    >>> resolve("/a/b/c", "../d")
    '/a/d'
    >>> resolve("/a/b/c", "./d")
    '/a/b/d'
    >>> resolve("/a/b/c", "/x")
    '/x'
    >>> resolve("/a/b/c", "d")
    '/a/b/d'
    """
    if ref.startswith("/"):
        return _normalise(ref)

    # Split from_path into directory segments (drop the node's own name)
    # e.g. "/a/b/c" → ["a", "b"]
    parts = [p for p in from_path.split("/") if p]
    if parts:
        parts = parts[:-1]  # remove the leaf (the node name itself)

    # Resolve ../ and ./ prefixes
    remaining = ref
    while remaining.startswith("../"):
        remaining = remaining[3:]
        if parts:
            parts.pop()
    if remaining.startswith("./"):
        remaining = remaining[2:]

    # Bare name is equivalent to ./name (no path separator inside remaining)
    parts.append(remaining)

    return "/" + "/".join(parts)


def _normalise(path: str) -> str:
    """Collapse redundant segments in an absolute path."""
    segments: list[str] = []
    for part in path.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if segments:
                segments.pop()
        else:
            segments.append(part)
    return "/" + "/".join(segments)

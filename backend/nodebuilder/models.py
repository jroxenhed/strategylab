"""Graph, Node, Wire Pydantic models + path resolver + topological sort.

Unit 1 — pure data types, no evaluator, no compiler, no indicator deps.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Optional

from pydantic import ConfigDict, Field, model_validator
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Version floor
# ---------------------------------------------------------------------------

MIN_SUPPORTED_VERSION: int = 1


# ---------------------------------------------------------------------------
# Custom error hierarchy
# ---------------------------------------------------------------------------


class GraphValidationError(Exception):
    """Base class for all graph validation errors."""


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


class ReadOnlyGraphError(GraphValidationError):
    """Raised when a mutation is attempted on a readOnly graph (Unit 5)."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class Node(BaseModel):
    """A single node in the strategy graph."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    """Path string, e.g. /ticker_aapl_1d"""

    type: str
    """Catalog node name, e.g. 'rsi'."""

    params: dict[str, Any] = Field(default_factory=dict)
    position: tuple[float, float] = (0.0, 0.0)
    display: bool = False
    bypass: bool = False
    subgraph: Optional[str] = None
    """Parent subgraph path; None means root."""


class Wire(BaseModel):
    """A directed edge between two node output and input ports."""

    model_config = ConfigDict(populate_by_name=True)

    id: str

    from_path: str = Field(alias="from")
    """Source node path."""

    to_path: str = Field(alias="to")
    """Destination node path."""

    attr: Optional[str] = None
    """Attribute label rendered on the wire; may be derived later."""


class Graph(BaseModel):
    """The top-level strategy graph."""

    model_config = ConfigDict(populate_by_name=True)

    version: int = Field(alias="_version", default=1)
    readOnly: bool = False
    nodes: dict[str, Node] = Field(default_factory=dict)
    wires: list[Wire] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_graph(self) -> "Graph":
        # 1. node.id must match its dict key
        for key, node in self.nodes.items():
            if node.id != key:
                raise ValueError(
                    f"Node id mismatch: nodes[{key!r}].id == {node.id!r}"
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
                raise DanglingWireError(
                    f"Wire {wire.id!r} references unknown node(s): {', '.join(missing)}"
                )

        # 3. No cycles
        _assert_acyclic(self)

        return self

    # ------------------------------------------------------------------
    # Factory: load with version check
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, data: dict) -> "Graph":
        """Deserialise from a dict, enforcing the version floor."""
        actual_version = data.get("_version", 1)
        if actual_version < MIN_SUPPORTED_VERSION:
            raise IncompatibleGraphVersionError(actual_version, MIN_SUPPORTED_VERSION)
        return cls.model_validate(data)


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
            f"Graph contains a cycle involving node(s): {sorted(remaining)}"
        )


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

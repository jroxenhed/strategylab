"""Graph format migration and node path helpers (plan D2, D3).

Migration
---------
``migrate_graph_data`` is the one entry point.  ``Graph`` calls it from a
``model_validator(mode="before")``, so every load path (API body, bots.json,
the graphs folder, library assets, seeds) upgrades a stored graph the same way.

- v1 -> v2 adds ``Node.name``, ``Node.parent`` (replaces the unused
  ``subgraph``), ``Wire.from_port`` / ``Wire.to_port``, ``stream_schema``,
  ``meta`` and ``annotations``.
- A v2 graph that is missing a node name or a wire port gets it filled in by
  the same rules.  This keeps a half-built graph from the editor loadable.

Paths
-----
A node's path is its parent's path plus ``/`` plus its name.  Root is ``/``.
Wires point at ids, not paths, so a rename never breaks a wire.

Relative paths follow Houdini: they start at the node given as
``relative_to``.  ``..`` goes up one level (so ``../rsi`` is a sibling
called ``rsi``), ``.`` stays put, and a bare name goes down into a child.

The TypeScript copy of these helpers is
``frontend/src/features/nodebuilder/paths.ts``.  Both run the same vectors in
``backend/tests/nodebuilder/vectors/paths.json``; change them together.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterable, Optional

from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover
    from nodebuilder.models import Graph

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CURRENT_GRAPH_VERSION: int = 2
"""The ``_version`` every loaded graph ends up at."""

NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
"""A valid node name (the leaf of its path)."""

_NAME_MAX = 64

_UUID_RE = re.compile(
    r"^/?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_NEW_ID_RE = re.compile(r"^n_[a-z0-9]{8}$")
_PORT_RE = re.compile(r"^in(\d+)$")
_TRAILING_DIGITS_RE = re.compile(r"^(.*?)(\d+)$")


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def is_valid_name(name: Any) -> bool:
    return isinstance(name, str) and NAME_RE.match(name) is not None


def sanitize_name(raw: str) -> str:
    """Turn any string into a valid node name.

    Lower-case, every character outside ``[a-z0-9_]`` becomes ``_``, a leading
    digit gets an ``n_`` in front, and the result is cut to 64 characters.
    """
    s = re.sub(r"[^a-z0-9_]", "_", str(raw).lower())
    if not s:
        s = "node"
    if s[0].isdigit():
        s = "n_" + s
    return s[:_NAME_MAX]


def is_opaque_id(node_id: str) -> bool:
    """True for ids that say nothing about the node (UUIDs, ``n_xxxxxxxx``).

    Such nodes are named after their type instead (``rsi1``, ``rsi2``...).
    """
    return bool(_UUID_RE.match(node_id) or _NEW_ID_RE.match(node_id))


def unique_name(base: str, taken: Iterable[str]) -> str:
    """Return *base*, or the next free numbered form of it.

    ``rsi`` taken gives ``rsi1``; ``rsi1`` taken gives ``rsi2``; ``cmp_buy_0``
    taken gives ``cmp_buy_1``.  *base* must already be a valid name.  The
    result is never longer than 64 characters.
    """
    # A set is used as is (it is only read); copying it on every call made
    # naming a big graph quadratic.
    taken_set = taken if isinstance(taken, (set, frozenset)) else set(taken)
    if base not in taken_set:
        return base
    m = _TRAILING_DIGITS_RE.match(base)
    stem, n = (m.group(1), int(m.group(2)) + 1) if m and m.group(1) else (base, 1)
    while True:
        suffix = str(n)
        candidate = stem[: _NAME_MAX - len(suffix)] + suffix
        if candidate not in taken_set:
            return candidate
        n += 1


def default_name(node_id: str, node_type: str) -> str:
    """The name a node gets when it has none (plan D3)."""
    if is_opaque_id(node_id):
        return sanitize_name(node_type or "node")[: _NAME_MAX - 1] + "1"
    return sanitize_name(node_id.lstrip("/"))


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def _as_dict(value: Any, *, by_alias: bool) -> Any:
    """Model instances become plain dicts so migration can treat both alike."""
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=by_alias)
    if isinstance(value, dict):
        return dict(value)
    return value


def _wire_target(wire: dict) -> Any:
    return wire.get("to", wire.get("to_path"))


def _hkey(value: Any) -> Any:
    """*value* as a dict key.  A malformed (unhashable) value gets a stand-in
    so migration never crashes; the model then reports the bad field."""
    try:
        hash(value)
    except TypeError:
        return ("<unhashable>", repr(value))
    return value


def _fill_names(nodes: dict) -> None:
    """Give every node without a name a free name among its siblings.

    Existing names are kept.  Blank ones are filled in node order, so the
    result does not depend on anything but the graph itself.
    """
    taken: dict[Any, set[str]] = {}
    for node in nodes.values():
        if isinstance(node, dict) and isinstance(node.get("name"), str) and node["name"]:
            taken.setdefault(_hkey(node.get("parent")), set()).add(node["name"])
    for key, node in nodes.items():
        if not isinstance(node, dict) or node.get("name"):
            continue
        siblings = taken.setdefault(_hkey(node.get("parent")), set())
        name = unique_name(
            default_name(str(node.get("id", key)), str(node.get("type", ""))), siblings
        )
        node["name"] = name
        siblings.add(name)


def _fill_ports(wires: list) -> None:
    """Give every wire without a ``to_port`` the next free ``in<k>`` port.

    Ports are handed out per consumer in wire order, which is the order
    compile reads a node's inputs in today.  Existing ports are kept.
    """
    used: dict[Any, set[Any]] = {}
    for wire in wires:
        if isinstance(wire, dict) and wire.get("to_port"):
            used.setdefault(_hkey(_wire_target(wire)), set()).add(_hkey(wire["to_port"]))
    for wire in wires:
        if not isinstance(wire, dict):
            continue
        wire.setdefault("from_port", "out")
        if wire.get("to_port"):
            continue
        ports = used.setdefault(_hkey(_wire_target(wire)), set())
        k = 0
        while f"in{k}" in ports:
            k += 1
        wire["to_port"] = f"in{k}"
        ports.add(wire["to_port"])


def migrate_v1_to_v2(data: dict) -> dict:
    """Upgrade a v1 graph dict to v2.  Returns a new dict.

    ``subgraph`` was never used, so it is dropped and ``parent`` starts empty
    (root).  Names come from the id (``/rsi_ab12`` -> ``rsi_ab12``), or from
    the type for opaque ids (``rsi1``).  Each consumer's input wires get
    ``in0``, ``in1``... in their current order.
    """
    out = dict(data)
    nodes = {}
    for key, node in (out.get("nodes") or {}).items():
        node = _as_dict(node, by_alias=False)
        if isinstance(node, dict):
            node.pop("subgraph", None)
            node.setdefault("parent", None)
        nodes[key] = node
    out["nodes"] = nodes
    out["wires"] = [_as_dict(w, by_alias=True) for w in (out.get("wires") or [])]
    _fill_names(nodes)
    _fill_ports(out["wires"])
    out.pop("version", None)
    out["_version"] = 2
    return out


def migrate_graph_data(data: Any) -> Any:
    """Run the migration chain on raw graph input.  Anything not a dict passes through.

    Raises IncompatibleGraphVersionError for a ``_version`` below the floor,
    and UnsupportedGraphVersionError (code graph_invalid) for a ``_version``
    that is not a whole number or is newer than CURRENT_GRAPH_VERSION.  A
    newer graph is refused rather than loaded, because loading it would drop
    the fields this code does not know.

    ``nodes`` that is not an object, or ``wires`` that is not a list, is
    passed through untouched so the model reports it as a field error.
    """
    if not isinstance(data, dict):
        return data
    from nodebuilder.models import (
        MIN_SUPPORTED_VERSION,
        IncompatibleGraphVersionError,
        UnsupportedGraphVersionError,
    )

    # bots.json stores model_dump() output, where the key is "version".
    version = data.get("_version", data.get("version", 1))
    if isinstance(version, bool) or not isinstance(version, int):
        raise UnsupportedGraphVersionError(
            f"Graph _version must be a whole number, got {version!r}."
        )
    if version < MIN_SUPPORTED_VERSION:
        raise IncompatibleGraphVersionError(version, MIN_SUPPORTED_VERSION)
    if version > CURRENT_GRAPH_VERSION:
        raise UnsupportedGraphVersionError(
            f"This graph was saved by a newer StrategyLab (graph version {version}; "
            f"this server reads up to {CURRENT_GRAPH_VERSION}).  Update StrategyLab "
            "to open it."
        )

    nodes = data.get("nodes")
    wires = data.get("wires")
    if not isinstance(nodes, (dict, type(None))) or not isinstance(wires, (list, type(None))):
        return data  # let pydantic report the bad field

    if version < 2:
        return migrate_v1_to_v2(data)

    # v2: fill in any names or ports the sender left out.
    out = dict(data)
    out["nodes"] = {k: _as_dict(n, by_alias=False) for k, n in (out.get("nodes") or {}).items()}
    out["wires"] = [_as_dict(w, by_alias=True) for w in (out.get("wires") or [])]
    _fill_names(out["nodes"])
    _fill_ports(out["wires"])
    if "_version" not in out:
        out["_version"] = out.pop("version")
    else:
        out.pop("version", None)
    return out


# ---------------------------------------------------------------------------
# Name and parent checks (used by Graph's validator and by /validate)
# ---------------------------------------------------------------------------


def name_issues(nodes: dict) -> list[dict]:
    """Every problem with node names and parents, in node order.

    Each item is ``{"code", "node_id", "message"}``.  Codes: ``name_invalid``,
    ``name_duplicate``, ``parent_missing``, ``parent_cycle``.  *nodes* maps id
    to anything with ``name`` and ``parent`` attributes.
    """
    issues: list[dict] = []
    seen: dict[tuple[Optional[str], str], str] = {}
    for node_id, node in nodes.items():
        if not is_valid_name(node.name):
            issues.append({
                "code": "name_invalid", "node_id": node_id,
                "message": f"Name {node.name!r} is not valid. Use lower-case letters, "
                           "digits and _, starting with a letter or _ (at most 64).",
            })
        else:
            key = (node.parent, node.name)
            if key in seen:
                issues.append({
                    "code": "name_duplicate", "node_id": node_id,
                    "message": f"Another node here is already called {node.name!r}.",
                })
            else:
                seen[key] = node_id
        if node.parent is not None and node.parent not in nodes:
            issues.append({
                "code": "parent_missing", "node_id": node_id,
                "message": f"Parent {node.parent!r} does not exist.",
            })
    for node_id in nodes:
        if _parent_cycle(nodes, node_id):
            issues.append({
                "code": "parent_cycle", "node_id": node_id,
                "message": "This node is inside itself (its parents loop back to it).",
            })
    return issues


def _parent_cycle(nodes: dict, node_id: str) -> bool:
    seen = {node_id}
    parent = nodes[node_id].parent
    while parent is not None and parent in nodes:
        if parent in seen:
            return True
        seen.add(parent)
        parent = nodes[parent].parent
    return False


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def node_path(graph: "Graph", node_id: str) -> str:
    """The absolute path of a node, e.g. ``/regime/spy_sma``.

    Raises KeyError when the node does not exist.  Assumes a validated graph
    (no parent loops).
    """
    names: list[str] = []
    current: Optional[str] = node_id
    while current is not None:
        node = graph.nodes[current]
        names.append(node.name)
        current = node.parent
    return "/" + "/".join(reversed(names))


def _child_by_name(graph: "Graph", parent: Optional[str], name: str) -> Optional[str]:
    for node_id, node in graph.nodes.items():
        if node.parent == parent and node.name == name:
            return node_id
    return None


def find_by_path(graph: "Graph", path: str, relative_to: Optional[str] = None) -> Optional[str]:
    """Return the id of the node at *path*, or None when nothing is there.

    An absolute path starts with ``/``.  Anything else is relative to the node
    whose id is *relative_to* (or to the root when that is None).  ``..`` goes
    up one level, ``.`` stays, a name goes down into that child.  Going above
    the root, or a path that ends at the root itself, finds nothing.
    """
    if not isinstance(path, str) or path == "":
        return None
    if path.startswith("/") or relative_to is None:
        current: Optional[str] = None  # the root
    else:
        if relative_to not in graph.nodes:
            return None
        current = relative_to
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if current is None:
                return None
            current = graph.nodes[current].parent
            continue
        current = _child_by_name(graph, current, part)
        if current is None:
            return None
    return current


class NodeNotFoundError(Exception):
    """rename_node was given an id that is not in the graph."""

    code = "node_not_found"

    def __init__(self, node_id: str) -> None:
        super().__init__(f"No node with id {node_id!r}.")
        self.node_id = node_id


def rename_node(graph: "Graph", node_id: str, new_name: str) -> "Graph":
    """Return a copy of *graph* with the node renamed.  The input is not changed.

    Wires use ids, so they need nothing.  Stored path strings that pointed at
    the old path are rewritten (see ``_rewrite_path_refs``).

    Raises NodeNotFoundError, InvalidNodeNameError (``name_invalid``) or
    DuplicateNodeNameError (``name_duplicate``).  It does not check
    ``readOnly``; the caller decides whether the graph may change.
    """
    from nodebuilder.models import DuplicateNodeNameError, InvalidNodeNameError

    if node_id not in graph.nodes:
        raise NodeNotFoundError(node_id)
    node = graph.nodes[node_id]
    if not is_valid_name(new_name):
        raise InvalidNodeNameError(
            f"Name {new_name!r} is not valid. Use lower-case letters, digits and _, "
            "starting with a letter or _ (at most 64).",
            node_id=node_id,
        )
    if new_name == node.name:
        return graph.model_copy(deep=True)
    if _child_by_name(graph, node.parent, new_name) is not None:
        raise DuplicateNodeNameError(
            f"Another node here is already called {new_name!r}.", node_id=node_id
        )
    old_path = node_path(graph, node_id)
    new_graph = graph.model_copy(deep=True)
    new_graph.nodes[node_id] = new_graph.nodes[node_id].model_copy(update={"name": new_name})
    _rewrite_path_refs(new_graph, old_path, node_path(new_graph, node_id))
    return new_graph


def _rewrite_path_refs(graph: "Graph", old_path: str, new_path: str) -> None:
    """Point stored path strings at a renamed node's new path (in place).

    Nothing stores paths yet.  W6 adds promoted-param targets and W7 adds
    ``ch()`` strings; both get rewritten here.  A path under *old_path*
    (a child of a renamed network) must move too.
    """
    return None

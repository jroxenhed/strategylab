"""Library asset instances: expand them into their networks (plan W6).

A library asset is a saved network (nodes and wires) with a name and a
version.  An *instance* of it is a ``subnet`` node with ``asset_ref``
(``{name, version}``).  A *locked* instance stores no children: they come
from the library each time the graph compiles.  An unlocked instance stores
its children as a local copy and keeps ``asset_ref`` only to say where they
came from.

``expand_assets`` gives every locked instance its children.  A child keeps
its name; its id becomes ``<instance id>::<child id>`` (a composite id) and
a top-level child gets the instance as its parent.  An asset that holds
another locked instance is expanded again inside, so ids can read
``a::b::c``.  The instance's promoted params come from the asset (a locked
instance can only change their values).  Problems (an asset not in the
library, an asset that contains itself) are returned, never raised, and
the instance is then left empty.

``bake_assets`` does the same and returns a graph to store: every instance
it expanded is unlocked, so the graph no longer needs the library.  A bot's
graph is baked, so a later library change can never change a running bot.
It also writes each promoted param's value into its target's params, so a
reader that ignores ``promoted`` (Wave 5 code after a rollback) computes the
same strategy (BS-01).

A switched-off instance (bypassed, or inside a bypassed network) cannot
change what the graph computes, so a problem expanding it is a warning,
not an error (``AssetError.severity``): bypass is how a graph whose asset
version was deleted runs again.

The library itself lives elsewhere (nodebuilder/storage.py).  It registers
its lookup with ``set_default_resolver`` when it is imported; compile uses
``default_resolver()`` when no resolver is passed.  With none registered,
every locked instance gives ``asset_missing``.

No storage or trading imports in this module.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from pydantic import BaseModel

from nodebuilder.kernel.flatten import (
    composite_id,
    editor_id,
    is_locked_instance,
    switched_off,
)
from nodebuilder.models import (
    COMPOSITE_ID_SEP,
    Graph,
    GraphValidationError,
    Node,
    PromotedParam,
)

logger = logging.getLogger(__name__)

Resolver = Callable[[str, int], Optional[dict]]
"""(asset name, version) -> the AssetFile dict, or None when there is none."""

MAX_ASSET_DEPTH = 32
"""Most levels of assets inside assets.  Each name/version pair is its own
asset, so a chain of versions could nest without a cycle; past this depth
the expansion stops with a graph_invalid problem."""

MAX_EXPANDED_NODES = 20_000
"""Most nodes all instances of one graph may add.  An asset that holds many
instances of an asset that holds many more grows very fast; past this cap
the expansion stops with a graph_invalid problem instead of eating memory."""


class AssetError(GraphValidationError):
    """One problem with an asset instance.

    code is ``asset_missing``, ``asset_cycle`` or ``graph_invalid`` (the
    expansion grew too big, or a baked graph would not load).  node_id is
    the instance the user sees (the
    outermost locked instance when the problem is deeper inside it).
    asset_name and asset_version say which asset could not be used (None
    when the problem is not about one asset).  severity is "warning" when
    the instance is switched off (bypassed, or inside a bypassed network),
    else "error".
    """

    def __init__(self, code: str, message: str, node_id: Optional[str], *,
                 asset_name: Optional[str] = None,
                 asset_version: Optional[int] = None,
                 severity: str = "error") -> None:
        super().__init__(message, node_id=node_id)
        self.code = code
        self.param = None
        self.port = None
        self.asset_name = asset_name
        self.asset_version = asset_version
        self.severity = severity


# ---------------------------------------------------------------------------
# The default resolver
# ---------------------------------------------------------------------------


def _no_library(name: str, version: int) -> Optional[dict]:
    return None


_default_resolver: Optional[Resolver] = None


def set_default_resolver(fn: Optional[Resolver]) -> None:
    """Make *fn* the lookup compile uses when it is given none (None clears it)."""
    global _default_resolver
    _default_resolver = fn


def default_resolver() -> Resolver:
    """The registered lookup, or one that finds nothing."""
    return _default_resolver if _default_resolver is not None else _no_library


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def expand_assets(graph: Graph, resolve: Optional[Resolver] = None
                  ) -> tuple[Graph, list[AssetError]]:
    """*graph* with every locked instance's children filled in from the
    library, and the problems found (see the module doc).

    The input is not changed.  A graph with no locked instance comes back
    as the same object.  The result is not checked by the Graph model (its
    ids contain ``::``); compile reads it as it is.  The instances stay
    locked, so flatten can tell their children are not stored.
    """
    return _Expander(graph, resolve or default_resolver(), bake=False).run()


def bake_assets(graph: Graph, resolve: Optional[Resolver] = None) -> Graph:
    """*graph* with every locked instance replaced by an unlocked copy of its
    asset, and every promoted param's value written into its target, ready
    to store (a bot snapshot).

    asset_ref and promoted stay on each instance; flatten then puts the same
    values in again, so the result compiles the same.  A switched-off
    instance whose asset cannot be expanded stays locked (it computes
    nothing).  Raises AssetError (code ``asset_missing``, ``asset_cycle`` or
    ``graph_invalid``, with the instance and the asset name) for the first
    error.  A graph with nothing to bake comes back as the same object.
    """
    expanded, problems = _Expander(graph, resolve or default_resolver(), bake=True).run()
    errors = [p for p in problems if p.severity == "error"]
    if errors:
        raise errors[0]
    baked = bake_promoted_values(expanded)
    if baked is graph:
        return graph
    # Check the result the way a later load will (names, wires, the ids
    # with "::" now stored inside unlocked instances).
    try:
        return Graph.model_validate(baked.model_dump(by_alias=True))
    except Exception as exc:  # noqa: BLE001 (one error type for the caller)
        node_id = getattr(exc, "node_id", None)
        shown = editor_id(graph.nodes, node_id) if isinstance(node_id, str) else None
        raise AssetError("graph_invalid", f"The graph with its assets copied in does not "
                         f"load: {exc}", shown) from exc


def bake_promoted_values(graph: Graph) -> Graph:
    """*graph* with each promoted param's value (``params[name]``, else its
    default, nested chains followed down) written into its target's params.

    flatten would put the same values in, so the graph compiles the same;
    a reader that ignores ``promoted`` now does too.  A promoted param that
    is invalid, or whose value is None, leaves its target alone (compile
    reports the invalid one).  Nodes inside a locked instance are not
    stored, so they are not touched.  Returns *graph* itself when no value
    changes.
    """
    if not any(n.promoted for n in graph.nodes.values()):
        return graph
    from nodebuilder.kernel.flatten import _Flattener, boundary_kind, is_network_type

    kinds: dict[str, str] = {}
    for nid, node in graph.nodes.items():
        if is_network_type(node.type):
            kinds[nid] = "network"
        else:
            b = boundary_kind(node.type)
            if b is not None:
                kinds[nid] = b
    overrides = _Flattener(graph, kinds)._promote()
    nodes = dict(graph.nodes)
    changed = False
    for target, values in overrides.items():
        node = nodes.get(target)
        if node is None or _inside_locked_instance(nodes, target):
            continue
        params = node.params or {}
        if all(k in params and params[k] == v and type(params[k]) is type(v)
               for k, v in values.items()):
            continue
        nodes[target] = node.model_copy(update={"params": {**params, **values}})
        changed = True
    if not changed:
        return graph
    return graph.model_copy(update={"nodes": nodes})


def _inside_locked_instance(nodes: dict, nid: str) -> bool:
    """True when *nid* sits inside a locked instance (its copy is not stored)."""
    seen = {nid}
    current = nodes[nid].parent if nid in nodes else None
    while current is not None and current in nodes and current not in seen:
        if is_locked_instance(nodes[current]):
            return True
        seen.add(current)
        current = nodes[current].parent
    return False


def plain_network_ids(network: Any) -> Any:
    """An asset network (``{nodes, wires}`` dict) with the composite-id
    prefix of the copy it was taken from removed (KA-2).

    A network saved from an unlocked copy (``Unlock``, edit, ``Save as
    asset``) holds ids like ``inst::sma`` whose owner ``inst`` is not in the
    network.  Every id whose first ``::`` segment is not a node of the
    network loses that segment, in node ids, parents, wire ends and wire
    ids, so the asset stores plain ids and a nested unlocked copy keeps its
    own (``inst::rf::sma`` becomes ``rf::sma`` under ``rf``).  Repeats
    until nothing changes.  Anything that is not that shape, or a renaming
    that would make two ids equal, comes back as it is (the Graph model
    then reports the problem).
    """
    if not isinstance(network, dict) or not isinstance(network.get("nodes"), dict):
        return network
    nodes: dict = network["nodes"]
    wires = network.get("wires") if isinstance(network.get("wires"), list) else []
    for _ in range(MAX_ASSET_DEPTH):
        def strip(value: Any) -> Any:
            if isinstance(value, str) and COMPOSITE_ID_SEP in value:
                head, rest = value.split(COMPOSITE_ID_SEP, 1)
                if head not in nodes:
                    return rest
            return value

        mapping = {k: strip(k) for k in nodes}
        if all(k == v for k, v in mapping.items()):
            break
        if len(set(mapping.values())) != len(mapping):
            return network
        new_nodes = {}
        for k, n in nodes.items():
            if not isinstance(n, dict):
                return network
            nk = mapping[k]
            parent = n.get("parent")
            new_nodes[nk] = {**n, "id": nk,
                             "parent": mapping.get(parent, parent) if parent is not None else None}
        new_wires = []
        for w in wires:
            if not isinstance(w, dict):
                return network
            nw = dict(w)
            for key in ("from", "to", "from_path", "to_path"):
                if key in nw:
                    nw[key] = mapping.get(nw[key], nw[key])
            if "id" in nw:
                nw["id"] = strip(nw["id"])
            new_wires.append(nw)
        if len({w.get("id") for w in new_wires}) != len({w.get("id") for w in wires}):
            return network
        nodes, wires = new_nodes, new_wires
    return {**network, "nodes": nodes, "wires": wires}


@dataclass(frozen=True)
class AssetInterface:
    """What an asset says it reads and writes (its AssetFile ``interface``)."""
    reads: tuple[dict, ...]
    writes: tuple[dict, ...]
    stream_schema: Optional[int]


def asset_interface(asset: Any) -> AssetInterface:
    """The declared interface of an AssetFile (dict or model).  Entries that
    are not ``{name, class, dtype}`` objects are skipped."""
    raw = _plain(asset) if asset is not None else {}
    iface = raw.get("interface") if isinstance(raw, dict) else None
    iface = iface if isinstance(iface, dict) else {}

    def decls(key: str) -> tuple[dict, ...]:
        items = iface.get(key)
        if not isinstance(items, list):
            return ()
        return tuple(d for d in items if isinstance(d, dict) and isinstance(d.get("name"), str))

    schema = raw.get("stream_schema") if isinstance(raw, dict) else None
    return AssetInterface(decls("reads"), decls("writes"),
                          schema if isinstance(schema, int) and not isinstance(schema, bool) else None)


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------


@dataclass
class _Asset:
    network: Graph
    promoted: list[PromotedParam]


def _plain(value: Any) -> Any:
    return value.model_dump(by_alias=True) if isinstance(value, BaseModel) else value


def _label(key: tuple[str, int]) -> str:
    return f"{key[0]} v{key[1]}"


class _Expander:
    def __init__(self, graph: Graph, resolve: Resolver, *, bake: bool) -> None:
        self.graph = graph
        self.resolve = resolve
        self.bake = bake
        self.problems: list[AssetError] = []
        self.cache: dict[tuple[str, int], Any] = {}
        self.added = 0
        self.nodes: dict[str, Node] = {}
        self.wires: list = []

    def run(self) -> tuple[Graph, list[AssetError]]:
        src = self.graph.nodes
        locked = [nid for nid, n in src.items() if is_locked_instance(n)]
        if not locked:
            return self.graph, []
        # Anything stored inside a locked instance is left out: the
        # library's copy replaces it.  (The Graph model refuses such nodes;
        # only /validate's unchecked fallback can pass them in.)
        inside = {nid for nid in src if self._inside_locked(nid)}
        self.nodes = {nid: n for nid, n in src.items() if nid not in inside}
        self.wires = [w for w in self.graph.wires
                      if w.from_path not in inside and w.to_path not in inside]
        for nid in locked:
            if nid not in inside:
                self._expand(nid, visible=nid, stack=(), off=switched_off(src, nid))
        out = self.graph.model_copy(update={"nodes": self.nodes, "wires": self.wires})
        return out, self.problems

    def _inside_locked(self, nid: str) -> bool:
        src = self.graph.nodes
        seen = {nid}
        current = src[nid].parent
        while current is not None and current in src and current not in seen:
            if is_locked_instance(src[current]):
                return True
            seen.add(current)
            current = src[current].parent
        return False

    def _problem(self, code: str, message: str, visible: str, key: tuple[str, int],
                 off: bool = False) -> None:
        if off:
            message += "  The node is switched off (bypassed), so the graph runs without it."
        self.problems.append(AssetError(code, message, visible,
                                        asset_name=key[0], asset_version=key[1],
                                        severity="warning" if off else "error"))

    def _where(self, stack: tuple) -> str:
        if not stack:
            return ""
        return " (used inside " + " inside ".join(_label(k) for k in reversed(stack)) + ")"

    def _load(self, key: tuple[str, int], visible: str, stack: tuple,
              off: bool) -> Optional[_Asset]:
        if key not in self.cache:
            self.cache[key] = self._read(key)
        got = self.cache[key]
        if isinstance(got, _Asset):
            return got
        self._problem("asset_missing", f"Asset {_label(key)}{self._where(stack)} {got}",
                      visible, key, off)
        return None

    def _read(self, key: tuple[str, int]) -> Any:
        """The parsed asset, or the reason it cannot be used (a string)."""
        name, version = key
        try:
            raw = self.resolve(name, version)
        except Exception as exc:  # noqa: BLE001 (a broken library never crashes compile)
            logger.warning("asset lookup %s v%s failed: %s", name, version, exc)
            return f"could not be read from the library ({exc})."
        if raw is None:
            return ("is not in the library.  Restore it, or replace this node with "
                    "another version.")
        try:
            raw = _plain(raw)
            network = _plain(raw.get("network")) or {}
            nodes = {k: dict(_plain(v)) for k, v in (network.get("nodes") or {}).items()}
            # A top-level child points at the subnet it was saved from, or
            # at nothing; either way it sits at the root of the asset.
            for node in nodes.values():
                if node.get("parent") not in nodes:
                    node["parent"] = None
            from nodebuilder.migrate import CURRENT_GRAPH_VERSION

            graph = Graph.model_validate({
                "_version": network.get("_version", CURRENT_GRAPH_VERSION),
                "nodes": nodes,
                "wires": [_plain(w) for w in (network.get("wires") or [])],
            })
            promoted = [PromotedParam.model_validate(_plain(p)) for p in (raw.get("promoted") or [])]
        except Exception as exc:  # noqa: BLE001
            return f"could not be read: {exc}"
        return _Asset(graph, promoted)

    def _expand(self, instance_id: str, *, visible: str, stack: tuple, off: bool) -> None:
        """Fill in *instance_id*'s children.  *off*: it is switched off
        (bypassed, or inside a bypassed network), so problems only warn."""
        node = self.nodes[instance_id]
        ref = node.asset_ref
        key = (ref.name, ref.version)
        if key in stack:
            chain = " -> ".join(_label(k) for k in (*stack, key))
            self._problem("asset_cycle", f"Asset {_label(key)} contains itself ({chain}).",
                          visible, key, off)
            return
        if len(stack) >= MAX_ASSET_DEPTH:
            self._problem("graph_invalid",
                          f"Asset {_label(key)}{self._where(stack)} is more than "
                          f"{MAX_ASSET_DEPTH} assets deep.", visible, key, off)
            return
        asset = self._load(key, visible, stack, off)
        if asset is None:
            return
        size = len(asset.network.nodes)
        if self.added + size > MAX_EXPANDED_NODES:
            self._problem("graph_invalid",
                          f"Asset {_label(key)}{self._where(stack)} would make the graph bigger "
                          f"than {MAX_EXPANDED_NODES} nodes.", visible, key, off)
            return
        self.added += size

        update: dict[str, Any] = {"promoted": [p.model_copy(deep=True) for p in asset.promoted]}
        if self.bake:
            update["locked"] = False
        self.nodes[instance_id] = node.model_copy(update=update)

        def cid(inner: str) -> str:
            return composite_id(instance_id, inner)

        for child_id, child in asset.network.nodes.items():
            parent = instance_id if child.parent is None else cid(child.parent)
            self.nodes[cid(child_id)] = child.model_copy(
                update={"id": cid(child_id), "parent": parent}, deep=True)
        for w in asset.network.wires:
            self.wires.append(w.model_copy(update={
                "id": cid(w.id), "from_path": cid(w.from_path), "to_path": cid(w.to_path),
            }))
        for child_id, child in asset.network.nodes.items():
            if is_locked_instance(child):
                inner = cid(child_id)
                self._expand(inner, visible=visible, stack=(*stack, key),
                             off=off or switched_off(self.nodes, inner))

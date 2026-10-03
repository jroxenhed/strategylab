"""Flatten a nested graph into one flat evaluation graph (plan D7).

A network is a node whose children point to it through ``parent``.  Wires
connect siblings only.  A stream enters a network through the network
node's input port ``in<k>`` and shows up inside on the boundary-input node
whose ``port`` param is ``k``.  It leaves through the boundary-output node,
whose input becomes the network node's output.

Compile and the evaluator only know flat graphs, so ``flatten`` removes the
network and boundary nodes and splices the wires through them:

    outside:  x --> net(in0)          net --> y
    inside:   input(port 0) --> a     b --> output
    flat:     x --> a                 b --> y

Each spliced wire keeps the inside wire's id and input port, so the node
that reads it sees the same thing as in a graph drawn flat by hand.  A
bypassed network passes the stream on its in0 through, as a bypassed node
does, and everything inside it is switched off with it: its nodes (its
sinks included: a terminal or a settings node inside a bypassed network
no longer applies) are left out of the flat graph.

Ids.  Nodes of inline networks keep their ids.  Library asset instances
(W6, kernel/assets.py) give their children composite ids
``outerId::innerId``, so one asset can appear many times.  A locked
instance's children are not stored, so ``flat_to_source`` maps each of them
to the outermost locked instance around it (the node the user sees), and
diagnostics are mapped through it.  Children of an unlocked instance (a
local copy or a baked bot graph) are stored and map to themselves.

Promoted params (W6).  A network node may promote params of the nodes
inside it: ``promoted`` lists ``{name, target, type, default}``, the value
is the network's ``params[name]`` (or ``default``), and ``target`` is the
inner param's path relative to the network (``sma/period``).  flatten puts
each value into its target's params, outer networks first, so a network
can promote a param that an inner network promoted.  A bad name, target or
value is a ``promoted_invalid`` problem, and that value is not used.  A
diagnostic about a param that got its value this way is moved to the
promoted param on the network (``FlatGraph.remap``).

Which types are networks or boundary nodes is said by the registry: a type
whose ``meta["network"]`` is true is a network; ``meta["boundary"]`` is
``"input"`` or ``"output"`` for the boundary nodes.  The domain layer
registers the types (nodebuilder.trading.nodes_network and later the group
and regime networks).

No trading words in this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from nodebuilder.kernel import registry as _registry
from nodebuilder.migrate import NAME_RE
from nodebuilder.models import (
    COMPOSITE_ID_SEP,
    Graph,
    GraphValidationError,
    Node,
    network_wire_issues,
    port_index,
)

NETWORK_META = "network"
BOUNDARY_META = "boundary"
BOUNDARY_INPUT = "input"
BOUNDARY_OUTPUT = "output"
PORT_PARAM = "port"
"""The boundary-input param that says which input port of its network it is."""

PROMOTED_INVALID = "promoted_invalid"


# ---------------------------------------------------------------------------
# Ids
# ---------------------------------------------------------------------------


def composite_id(outer: str, inner: str) -> str:
    """The flat id of node *inner* inside the asset instance *outer* (W6)."""
    return f"{outer}{COMPOSITE_ID_SEP}{inner}"


def source_of_flat_id(flat_id: str) -> str:
    """The node the user sees for a flat id: the outermost instance for a
    composite id, the id itself otherwise."""
    return flat_id.split(COMPOSITE_ID_SEP, 1)[0]


def editor_id(nodes: Any, flat_id: Optional[str]) -> Optional[str]:
    """The id in *nodes* (the graph the editor has) that *flat_id* belongs to.

    That is *flat_id* itself when it is stored, else its longest ``::``
    prefix that is stored: ``U::L::sma`` maps to the locked instance ``U::L``
    inside the unlocked copy ``U``, where source_of_flat_id would say ``U``.
    Use it to show an error found in an expanded or baked graph on the
    graph the user edits.
    """
    if flat_id is None or flat_id in nodes:
        return flat_id
    parts = flat_id.split(COMPOSITE_ID_SEP)
    for end in range(len(parts) - 1, 0, -1):
        prefix = COMPOSITE_ID_SEP.join(parts[:end])
        if prefix in nodes:
            return prefix
    return source_of_flat_id(flat_id)


# ---------------------------------------------------------------------------
# Node kinds
# ---------------------------------------------------------------------------


def is_network_type(type_name: str) -> bool:
    nt = _registry.get(type_name)
    return bool(nt is not None and nt.meta.get(NETWORK_META))


def boundary_kind(type_name: str) -> Optional[str]:
    """"input", "output" or None."""
    nt = _registry.get(type_name)
    if nt is None:
        return None
    kind = nt.meta.get(BOUNDARY_META)
    return kind if kind in (BOUNDARY_INPUT, BOUNDARY_OUTPUT) else None


def is_locked_instance(node: Any) -> bool:
    """True for a locked library asset instance (its children are not stored)."""
    return bool(getattr(node, "locked", False)) and getattr(node, "asset_ref", None) is not None


def is_bypassed_network(node: Any) -> bool:
    """True for a network node whose bypass is on (and means something)."""
    if node is None or not getattr(node, "bypass", False):
        return False
    nt = _registry.get(node.type)
    return bool(nt is not None and nt.bypassable and nt.meta.get(NETWORK_META))


def switched_off(nodes: Any, nid: str) -> bool:
    """True when network *nid* is bypassed, or sits inside a bypassed network
    at any depth.  Everything in it is left out of the flat graph, so its
    contents cannot change what the graph computes."""
    seen: set[str] = set()
    current: Optional[str] = nid
    while current is not None and current not in seen and current in nodes:
        seen.add(current)
        if is_bypassed_network(nodes[current]):
            return True
        current = nodes[current].parent
    return False


# ---------------------------------------------------------------------------
# The result
# ---------------------------------------------------------------------------


@dataclass
class FlatGraph:
    """What ``flatten`` returns.

    graph          : the flat graph compile checks (the input graph itself
                     when it has no networks).  Its nodes have ``parent``
                     cleared, so flattening it again returns it as it is;
                     ``network_of`` / ``ancestors`` read ``source``.
    flat_to_source : flat node id -> the id of the node the user sees.
    networks       : the network nodes that were taken out, by id.
    boundaries     : the boundary nodes that were taken out, by id.
    found          : (Diagnostic, error or None) pairs for problems with
                     networks, boundaries, crossing wires and promoted
                     params, with source ids.
    outputs        : network id -> the flat node whose output stream is the
                     network's output (None when nothing reaches it).
    inputs         : network id -> {port k: the flat node that feeds in<k>}.
    promoted_from  : (flat id, param) -> (network id, promoted name) for
                     every param that got its value from a promoted param.
    visible_of     : every node id of the input graph (networks and boundary
                     nodes too) -> the id the user sees, for ids that differ
                     (nodes inside a locked asset instance).
    network_params : network id -> its stored params, with any value an
                     outer network promoted into it laid over.  ch("../name")
                     from inside a network reads these (W7, kernel/params.py;
                     a promoted param is read from its target, where flatten
                     put its value); ``source`` keeps the pre-flatten paths
                     ch() resolves against.
    """
    graph: Graph
    flat_to_source: dict[str, str]
    networks: dict[str, Node] = field(default_factory=dict)
    boundaries: dict[str, Node] = field(default_factory=dict)
    found: list[tuple[Any, Optional[GraphValidationError]]] = field(default_factory=list)
    source: Optional[Graph] = None
    outputs: dict[str, Optional[str]] = field(default_factory=dict)
    inputs: dict[str, dict[int, Optional[str]]] = field(default_factory=dict)
    promoted_from: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    visible_of: dict[str, str] = field(default_factory=dict)
    network_params: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def nested(self) -> bool:
        """True when the input graph had any network or boundary node."""
        return bool(self.networks or self.boundaries)

    def to_source(self, flat_id: Optional[str]) -> Optional[str]:
        if flat_id is None:
            return None
        if flat_id in self.flat_to_source:
            return self.flat_to_source[flat_id]
        src = self.source if self.source is not None else self.graph
        if flat_id in src.nodes:  # a network or boundary node
            return self.visible_of.get(flat_id, flat_id)
        return source_of_flat_id(flat_id)

    def network_of(self, flat_id: str) -> Optional[str]:
        """The id of the network a flat node sits in (None at the root)."""
        src = self.source if self.source is not None else self.graph
        node = src.nodes.get(self.to_source(flat_id) or "")
        return node.parent if node is not None else None

    def ancestors(self, flat_id: str) -> tuple[str, ...]:
        """The networks around a flat node, nearest first."""
        src = self.source if self.source is not None else self.graph
        out: list[str] = []
        current = self.network_of(flat_id)
        while current is not None and current not in out:
            out.append(current)
            node = src.nodes.get(current)
            current = node.parent if node is not None else None
        return tuple(out)

    def promoted_owner(self, node_id: str, param: Optional[str]) -> tuple[str, Optional[str]]:
        """Where the value of (*node_id*, *param*) is set: the network and
        promoted name it came from, followed up through every level, or the
        pair itself when it was not promoted."""
        seen: set[tuple[str, Optional[str]]] = set()
        key: tuple[str, Optional[str]] = (node_id, param)
        while key in self.promoted_from and key not in seen:
            seen.add(key)
            key = self.promoted_from[key]  # type: ignore[index]
        return key

    def _user_ref(self, node_id: str, param: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        node_id, param = self.promoted_owner(node_id, param) if param else (node_id, param)
        shown = self.to_source(node_id)
        # A param of a node the user cannot see (inside a locked instance)
        # names no row on the node they do see.
        return shown, (param if shown == node_id else None)

    def remap(self, found: Iterable[tuple[Any, Optional[GraphValidationError]]]) -> list:
        """*found* with every node id turned into the id the user sees.

        A problem with a param whose value is promoted moves to the promoted
        param on the network, where the user edits that value.  A message
        that names a node the user cannot see (inside a locked asset
        instance) names it by its name inside the instance instead.
        """
        src = self.source if self.source is not None else self.graph
        out = []
        for diag, exc in found:
            for item in (diag, exc):
                if item is None:
                    continue
                if getattr(item, "node_id", None) is not None:
                    node_id, param = self._user_ref(item.node_id, getattr(item, "param", None))
                    item.node_id = node_id
                    if hasattr(item, "param"):
                        item.param = param
                if self.visible_of:
                    _rewrite_message(item, lambda m: readable_ids(m, src.nodes, self.visible_of))
            out.append((diag, exc))
        return out


# ---------------------------------------------------------------------------
# flatten
# ---------------------------------------------------------------------------


def flatten(graph: Graph) -> FlatGraph:
    """Turn *graph* into one flat graph for compile (see the module doc).

    Never raises for a bad network: every problem is listed in ``found``
    (and the wire or node it is about is left out), so /validate can show
    them all.  A graph with no networks comes back as it is.
    """
    nodes = graph.nodes
    kinds: dict[str, str] = {}  # id -> "network" | "input" | "output"
    for nid, node in nodes.items():
        if is_network_type(node.type):
            kinds[nid] = NETWORK_META
        else:
            b = boundary_kind(node.type)
            if b is not None:
                kinds[nid] = b
    if not kinds and all(n.parent is None and not n.promoted for n in nodes.values()):
        return FlatGraph(graph, {nid: nid for nid in nodes}, source=graph)

    return _Flattener(graph, kinds).run()


class _Flattener:
    def __init__(self, graph: Graph, kinds: dict[str, str]) -> None:
        self.graph = graph
        self.nodes = graph.nodes
        self.kinds = kinds
        self.found: list[tuple[Any, Optional[GraphValidationError]]] = []
        self.dropped: set[str] = set()          # wire ids left out
        self.into: dict[tuple[str, Optional[str]], Any] = {}   # (to, port) -> wire
        self.inputs_of: dict[str, dict[int, str]] = {}   # network -> port -> input id
        self.output_of: dict[str, str] = {}               # network -> output id
        self._memo: dict[str, Optional[str]] = {}
        self._visible_memo: dict[str, str] = {}
        self.promoted_from: dict[tuple[str, str], tuple[str, str]] = {}
        # Locked asset instances that hold nothing: their asset could not be
        # expanded (that error, or warning when the instance is switched
        # off, says it all), so their ports are not checked here.
        has_children = {n.parent for n in self.nodes.values()}
        self.empty_instances = {nid for nid, n in self.nodes.items()
                                if is_locked_instance(n) and nid not in has_children}

    # -- problems ---------------------------------------------------------

    def _error(self, code: str, message: str, node_id: Optional[str], *,
               port: Optional[str] = None, param: Optional[str] = None) -> None:
        from nodebuilder.diagnostics import from_error
        from nodebuilder.kernel.schema import coded

        if node_id is not None and node_id in self.nodes:
            shown = self._visible(node_id)
            if shown != node_id:  # inside a locked asset instance
                node_id, port, param = shown, None, None
        if COMPOSITE_ID_SEP in message:
            message = readable_ids(message, self.nodes, _HiddenIds(self))
        exc = coded(GraphValidationError(message, node_id=node_id), code, port=port, param=param)
        self.found.append((from_error(exc), exc))

    def _visible(self, nid: str) -> str:
        """The node the user sees for *nid*: the outermost locked asset
        instance around it (whose children are not stored), else *nid*."""
        if nid in self._visible_memo:
            return self._visible_memo[nid]
        out = nid
        seen = {nid}
        current = self.nodes[nid].parent if nid in self.nodes else None
        while current is not None and current in self.nodes and current not in seen:
            seen.add(current)
            if is_locked_instance(self.nodes[current]):
                out = current
            current = self.nodes[current].parent
        self._visible_memo[nid] = out
        return out

    def _kind(self, nid: str) -> Optional[str]:
        return self.kinds.get(nid)

    def _is_network(self, nid: Optional[str]) -> bool:
        return nid is not None and self.kinds.get(nid) == NETWORK_META

    # -- checks -----------------------------------------------------------

    def _check_nodes(self) -> None:
        for nid, node in self.nodes.items():
            kind = self._kind(nid)
            parent = node.parent
            parent_node = self.nodes.get(parent) if parent is not None else None
            if kind in (BOUNDARY_INPUT, BOUNDARY_OUTPUT):
                if parent is None or not self._is_network(parent):
                    where = "at the root" if parent is None else f"inside {parent!r}, which is not a network"
                    self._error("boundary_invalid",
                                f"{node.type} {nid!r} sits {where}.  Boundary nodes "
                                "belong inside a network.", nid)
                    continue
                if kind == BOUNDARY_OUTPUT:
                    if parent in self.output_of:
                        self._error("boundary_invalid",
                                    f"Network {parent!r} has more than one output "
                                    f"({self.output_of[parent]!r} and {nid!r}).  "
                                    "A network has one output.", nid)
                    else:
                        self.output_of[parent] = nid
                    continue
                port = _port_param(node.params.get(PORT_PARAM, 0))
                if port is None:
                    self._error("param_invalid",
                                f"{node.type} {nid!r} has port "
                                f"{node.params.get(PORT_PARAM)!r}; it must be a whole "
                                "number 0 or above.", nid, param=PORT_PARAM)
                    continue
                taken = self.inputs_of.setdefault(parent, {})
                if port in taken:
                    self._error("boundary_invalid",
                                f"{nid!r} and {taken[port]!r} are both input in{port} of "
                                f"network {parent!r}.  Give each input its own port.",
                                nid, param=PORT_PARAM)
                    continue
                taken[port] = nid
            elif parent is not None and parent_node is not None and not self._is_network(parent):
                # A parent of a known type that is not a network.  (A parent
                # of an unknown type is reported as unknown_node_type.)
                if _registry.get(parent_node.type) is not None:
                    self._error("boundary_invalid",
                                f"{nid!r} sits inside {parent!r} ({parent_node.type}), "
                                "which is not a network.", nid)

    def _check_wires(self) -> None:
        for issue in network_wire_issues(self.nodes, self.graph.wires):
            self._error("wire_crosses_network", issue["message"], issue["node_id"],
                        port=issue["port"])
            self.dropped.add(issue["wire_id"])
        for w in self.graph.wires:
            if w.id in self.dropped or w.from_path not in self.nodes or w.to_path not in self.nodes:
                continue
            src_kind = self._kind(w.from_path)
            dst_kind = self._kind(w.to_path)
            if dst_kind == BOUNDARY_INPUT:
                self._error("boundary_invalid",
                            f"Wire {w.id!r} goes into network input {w.to_path!r}.  "
                            "A network input is fed through the network's port on "
                            "the outside.", w.to_path, port=w.to_port)
                self.dropped.add(w.id)
                continue
            if src_kind == BOUNDARY_OUTPUT:
                self._error("boundary_invalid",
                            f"Wire {w.id!r} comes out of network output "
                            f"{w.from_path!r}.  Wire the network node itself instead.",
                            w.from_path)
                self.dropped.add(w.id)
                continue
            if dst_kind == NETWORK_META and w.to_path not in self.empty_instances:
                k = port_index(w.to_port)
                if k is None or k not in self.inputs_of.get(w.to_path, {}):
                    self._error("port_unknown",
                                f"Wire {w.id!r} goes into {w.to_port or 'an unnamed port'} "
                                f"of network {w.to_path!r}, which has no input there.  "
                                "Add a Subnet input inside it.", w.to_path, port=w.to_port)
                    self.dropped.add(w.id)
                    continue
            if src_kind == NETWORK_META and not self._bypassed(w.from_path) \
                    and w.from_path not in self.output_of \
                    and w.from_path not in self.empty_instances:
                self._error("boundary_invalid",
                            f"Network {w.from_path!r} is wired to {w.to_path!r}, but it "
                            "has no output.  Add a Subnet output inside it.", w.from_path)
                self.dropped.add(w.id)
                continue
            self.into[(w.to_path, w.to_port)] = w

    def _bypassed(self, nid: str) -> bool:
        node = self.nodes[nid]
        nt = _registry.get(node.type)
        return bool(node.bypass and nt is not None and nt.bypassable)

    def _inside_bypassed(self, nid: str) -> bool:
        """True when a network around *nid* (at any depth) is bypassed."""
        seen: set[str] = set()
        current = self.nodes[nid].parent
        while current is not None and current not in seen and current in self.nodes:
            seen.add(current)
            if self._is_network(current) and self._bypassed(current):
                return True
            current = self.nodes[current].parent
        return False

    # -- promoted params (W6) ---------------------------------------------

    def _depth(self, nid: str) -> int:
        depth, seen = 0, {nid}
        current = self.nodes[nid].parent
        while current is not None and current in self.nodes and current not in seen:
            seen.add(current)
            depth += 1
            current = self.nodes[current].parent
        return depth

    def _promote(self) -> dict[str, dict[str, Any]]:
        """The value of every promoted param, put into its target.

        Returns {node id: {param: value}} to lay over the stored params.
        Outer networks go first, so when an outer network promotes a param
        that an inner network promoted, the outer value passes on down.
        """
        holders = [nid for nid, n in self.nodes.items() if n.promoted]
        if not holders:
            return {}
        children: dict[tuple[Optional[str], str], str] = {}
        for nid, n in self.nodes.items():
            children.setdefault((n.parent, n.name), nid)
        has_children = {n.parent for n in self.nodes.values()}
        overrides: dict[str, dict[str, Any]] = {}
        for nid in sorted(holders, key=self._depth):  # stable within a depth
            node = self.nodes[nid]
            if is_locked_instance(node) and nid not in has_children:
                continue  # its asset could not be expanded; that error says it all
            if not self._is_network(nid):
                self._error(PROMOTED_INVALID,
                            f"{node.type} {node.name!r} has promoted params, but only a network "
                            "node can promote params.", nid)
                continue
            values = {**(node.params or {}), **overrides.get(nid, {})}
            taken: set[str] = set()
            for p in node.promoted:
                got = self._check_promoted(nid, p, taken, children)
                if isinstance(got, str):
                    self._error(PROMOTED_INVALID,
                                f"Promoted param {p.name!r} on {node.name!r}: {got}", nid,
                                param=p.name)
                    continue
                target, param = got
                value = values[p.name] if p.name in values else p.default
                if value is None:
                    # No value and no default: the target keeps its own
                    # value (the library's rule too).  A problem with it
                    # still shows on the promoted param.
                    self.promoted_from[(target, param)] = (nid, p.name)
                    continue
                if not value_fits(p.type, value):
                    self._error(PROMOTED_INVALID,
                                f"Promoted param {p.name!r} on {node.name!r} is {p.type}, "
                                f"but its value is {value!r}.", nid, param=p.name)
                    continue
                overrides.setdefault(target, {})[param] = value
                self.promoted_from[(target, param)] = (nid, p.name)
        return overrides

    def _check_promoted(self, nid: str, p: Any, taken: set[str],
                        children: dict) -> Any:
        """(target id, param) for promoted param *p* of network *nid*, or
        what is wrong with it (a string)."""
        node = self.nodes[nid]
        if not isinstance(p.name, str) or not NAME_RE.match(p.name):
            return ("the name is not valid (lower-case letters, digits and _, starting "
                    "with a letter or _).")
        if p.name in taken:
            return "another promoted param on this network has the same name."
        taken.add(p.name)
        nt = _registry.get(node.type)
        if nt is not None and nt.param(p.name) is not None:
            return f"the name is already a param of {node.type}."
        if p.type not in _registry.PARAM_TYPES:
            return f"type {p.type!r} is not a param type."
        target = p.target if isinstance(p.target, str) else ""
        if target.startswith("/"):
            return f"target {target!r} must be relative to the network (like sma/period)."
        node_part, _, param = target.rpartition("/")
        if not node_part or not param:
            return f"target {target!r} must name a node and a param (like sma/period)."
        current = nid
        for part in node_part.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                return f"target {target!r} must point inside the network."
            if current != nid and is_locked_instance(self.nodes[current]):
                return (f"target {target!r} goes inside the library asset "
                        f"{self.nodes[current].name!r}; only its promoted params can be "
                        "promoted.")
            nxt = children.get((current, part))
            if nxt is None:
                return f"target {target!r} does not exist."
            current = nxt
        if current == nid:
            return f"target {target!r} must point at a node inside the network."
        tnode = self.nodes[current]
        if (current, param) in self.promoted_from:
            other, other_name = self.promoted_from[(current, param)]
            return (f"{target} is already set by promoted param {other_name!r} on "
                    f"{self.nodes[other].name!r}.")
        kind = self._kind(current)
        if kind in (BOUNDARY_INPUT, BOUNDARY_OUTPUT):
            return f"{target} belongs to a network port node, which cannot be promoted."
        if kind == NETWORK_META:
            inner = next((q for q in tnode.promoted if q.name == param), None)
            if inner is None:
                return (f"{tnode.name!r} has no promoted param {param!r} (only a network's "
                        "promoted params can be promoted again).")
            want: Optional[str] = inner.type
        else:
            tnt = _registry.get(tnode.type)
            spec = tnt.param(param) if tnt is not None else None
            if tnt is not None and spec is None:
                return f"{tnode.type} {tnode.name!r} has no param {param!r}."
            want = spec.type if spec is not None else None
        if want is not None and want != p.type and not (
                want in _NUMBER_TYPES and p.type in _NUMBER_TYPES):
            return f"it is {p.type}, but {target} is {want}."
        return current, param

    # -- splicing ---------------------------------------------------------

    def _fed_by(self, nid: str, port: str) -> Optional[str]:
        w = self.into.get((nid, port))
        return w.from_path if w is not None else None

    def _resolve(self, nid: Optional[str], seen: frozenset = frozenset()) -> Optional[str]:
        """The real (flat) node whose output stream *nid*'s output is.

        None when nothing feeds it (an unwired port, a network without an
        output, a loop through networks).
        """
        if nid is None or nid in seen:
            return None
        if nid in self._memo:
            return self._memo[nid]
        kind = self._kind(nid)
        seen = seen | {nid}
        if kind is None:
            out: Optional[str] = nid
        elif kind == BOUNDARY_INPUT:
            node = self.nodes[nid]
            network = node.parent
            port = _port_param(node.params.get(PORT_PARAM, 0))
            if network is None or port is None or self.inputs_of.get(network, {}).get(port) != nid:
                out = None
            else:
                out = self._resolve(self._fed_by(network, f"in{port}"), seen)
        elif kind == NETWORK_META:
            if self._bypassed(nid):
                out = self._resolve(self._fed_by(nid, "in0"), seen)
            else:
                inner = self.output_of.get(nid)
                out = self._resolve(self._fed_by(inner, "in0"), seen) if inner else None
        else:  # a boundary output is never a source
            out = None
        self._memo[nid] = out
        return out

    def run(self) -> FlatGraph:
        self._check_nodes()
        self._check_wires()
        overrides = self._promote()

        # The flat graph has no networks, so no node has a parent: clear it,
        # or compiling the flat graph again would see crossing wires.  A node
        # inside a bypassed network is left out: bypassing a network switches
        # off everything in it (KA-8), not only the stream through it.
        # Promoted values are laid over the stored params.
        flat_nodes = {}
        for nid, n in self.nodes.items():
            if nid in self.kinds or self._inside_bypassed(nid):
                continue
            update: dict[str, Any] = {}
            if n.parent is not None:
                update["parent"] = None
            if nid in overrides:
                update["params"] = {**(n.params or {}), **overrides[nid]}
            flat_nodes[nid] = n.model_copy(update=update) if update else n
        flat_wires = []
        for w in self.graph.wires:
            if w.id in self.dropped or w.to_path not in flat_nodes:
                continue
            if w.from_path not in self.nodes:
                flat_wires.append(w)  # dangling: compile reports it as before
                continue
            src = self._resolve(w.from_path)
            if src is None:
                continue  # nothing arrives; the consumer reports its missing input
            flat_wires.append(w if src == w.from_path else w.model_copy(update={"from_path": src}))

        flat = self.graph.model_copy(update={"nodes": flat_nodes, "wires": flat_wires})
        networks = {nid: self.nodes[nid] for nid, k in self.kinds.items() if k == NETWORK_META}
        visible = {nid: self._visible(nid) for nid in self.nodes}
        network_params = {nid: {**(net.params or {}), **overrides.get(nid, {})}
                          for nid, net in networks.items()}
        return FlatGraph(
            graph=flat,
            flat_to_source={nid: visible[nid] for nid in flat_nodes},
            networks=networks,
            boundaries={nid: self.nodes[nid] for nid, k in self.kinds.items() if k != NETWORK_META},
            found=self.found,
            source=self.graph,
            outputs={nid: self._resolve(nid) for nid in networks},
            inputs={nid: {k: self._resolve(self._fed_by(nid, f"in{k}"))
                          for k in sorted(self.inputs_of.get(nid, {}))}
                    for nid in networks},
            promoted_from=dict(self.promoted_from),
            visible_of={nid: v for nid, v in visible.items() if v != nid},
            network_params=network_params,
        )


_NUMBER_TYPES = ("int", "number")


def value_fits(kind: str, value: Any) -> bool:
    """True when *value* has the right shape for a param of type *kind*.

    Only the shape is checked here: limits (min, max, options) are checked
    on the target node as usual, and the problem is then shown on the
    promoted param.  A code value ({"expr": ...}, W7) is left to the target
    node too.  None fits every type: a promoted param whose value is None
    leaves the target's own value.  The library checks a promoted default
    with this same rule (storage.check_promoted).
    """
    if value is None or (isinstance(value, dict) and "expr" in value):
        return True
    if kind in _NUMBER_TYPES:
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return kind == "number" or float(value).is_integer()
        if isinstance(value, str):
            try:
                int(value.strip()) if kind == "int" else float(value)
            except ValueError:
                return False
            return True
        return False
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "attr_list":
        return isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value)
    if kind == "select":
        return isinstance(value, (str, int, float)) or (
            isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value))
    if kind in ("string", "attr", "write", "path"):
        return isinstance(value, str)
    return True  # time_range: the target node checks it


_value_fits = value_fits  # the name older code imports


# ---------------------------------------------------------------------------
# Readable ids in messages (nodes inside a locked asset instance)
# ---------------------------------------------------------------------------


_ID_TOKEN = re.compile(r"'([^'\s]*::[^'\s]*)'|([^\s'\"(),]+::[^\s'\"(),]+)")


class _HiddenIds:
    """visible_of for a _Flattener, computed on demand."""

    def __init__(self, flattener: "_Flattener") -> None:
        self.f = flattener

    def get(self, nid: str, default: Any = None) -> Any:
        if nid not in self.f.nodes:
            return default
        shown = self.f._visible(nid)
        return shown if shown != nid else default


def readable_ids(message: str, nodes: Any, visible_of: Any) -> str:
    """*message* with each id of a node the user cannot see (one *visible_of*
    maps to another node) replaced by its name and the instance around it:
    ``sma node 'inst::sma'`` becomes ``sma node 'sma' (inside 'regime')``."""
    if COMPOSITE_ID_SEP not in message:
        return message

    def label(token: str) -> Optional[str]:
        for cand in (token, token.rstrip(".:;")):
            shown = visible_of.get(cand)
            if shown is not None and cand in nodes:
                inst = nodes.get(shown)
                inst_name = inst.name if inst is not None and inst.name else shown
                return f"{nodes[cand].name or cand!r} (inside {inst_name!r})" + token[len(cand):]
        return None

    def sub(m: "re.Match") -> str:
        got = label(m.group(1) if m.group(1) is not None else m.group(2))
        return got if got is not None else m.group(0)

    return _ID_TOKEN.sub(sub, message)


def _rewrite_message(item: Any, fn: Any) -> None:
    """Apply *fn* to a Diagnostic's message or an exception's message."""
    if isinstance(item, BaseException):
        args = item.args
        if args and isinstance(args[0], str) and str(item) == args[0] \
                and COMPOSITE_ID_SEP in args[0]:
            item.args = (fn(args[0]), *args[1:])
        return
    message = getattr(item, "message", None)
    if isinstance(message, str) and COMPOSITE_ID_SEP in message:
        try:
            item.message = fn(message)
        except Exception:  # noqa: BLE001 (a frozen model keeps its text)
            pass


def _port_param(value: Any) -> Optional[int]:
    """A boundary input's port as a whole number 0 or above, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None

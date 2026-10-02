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
(W6) will give their children composite ids ``outerId::innerId``, so one
asset can appear many times; ``flat_to_source`` maps each flat id back to
the node the user sees, and diagnostics are mapped through it.

Which types are networks or boundary nodes is said by the registry: a type
whose ``meta["network"]`` is true is a network; ``meta["boundary"]`` is
``"input"`` or ``"output"`` for the boundary nodes.  The domain layer
registers the types (nodebuilder.trading.nodes_network and later the group
and regime networks).

No trading words in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from nodebuilder.kernel import registry as _registry
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
                     networks, boundaries and crossing wires, with source ids.
    """
    graph: Graph
    flat_to_source: dict[str, str]
    networks: dict[str, Node] = field(default_factory=dict)
    boundaries: dict[str, Node] = field(default_factory=dict)
    found: list[tuple[Any, Optional[GraphValidationError]]] = field(default_factory=list)
    source: Optional[Graph] = None

    @property
    def nested(self) -> bool:
        """True when the input graph had any network or boundary node."""
        return bool(self.networks or self.boundaries)

    def to_source(self, flat_id: Optional[str]) -> Optional[str]:
        if flat_id is None:
            return None
        return self.flat_to_source.get(flat_id, source_of_flat_id(flat_id))

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

    def remap(self, found: Iterable[tuple[Any, Optional[GraphValidationError]]]) -> list:
        """*found* with every node id turned into the id the user sees."""
        out = []
        for diag, exc in found:
            if diag is not None and getattr(diag, "node_id", None) is not None:
                diag.node_id = self.to_source(diag.node_id)
            if exc is not None and getattr(exc, "node_id", None) is not None:
                exc.node_id = self.to_source(exc.node_id)
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
    if not kinds and all(n.parent is None for n in nodes.values()):
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

    # -- problems ---------------------------------------------------------

    def _error(self, code: str, message: str, node_id: Optional[str], *,
               port: Optional[str] = None, param: Optional[str] = None) -> None:
        from nodebuilder.diagnostics import from_error
        from nodebuilder.kernel.schema import coded

        exc = coded(GraphValidationError(message, node_id=node_id), code, port=port, param=param)
        self.found.append((from_error(exc), exc))

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
            if dst_kind == NETWORK_META:
                k = port_index(w.to_port)
                if k is None or k not in self.inputs_of.get(w.to_path, {}):
                    self._error("port_unknown",
                                f"Wire {w.id!r} goes into {w.to_port or 'an unnamed port'} "
                                f"of network {w.to_path!r}, which has no input there.  "
                                "Add a Subnet input inside it.", w.to_path, port=w.to_port)
                    self.dropped.add(w.id)
                    continue
            if src_kind == NETWORK_META and not self._bypassed(w.from_path) \
                    and w.from_path not in self.output_of:
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

        # The flat graph has no networks, so no node has a parent: clear it,
        # or compiling the flat graph again would see crossing wires.  A node
        # inside a bypassed network is left out: bypassing a network switches
        # off everything in it (KA-8), not only the stream through it.
        flat_nodes = {
            nid: (n if n.parent is None else n.model_copy(update={"parent": None}))
            for nid, n in self.nodes.items()
            if nid not in self.kinds and not self._inside_bypassed(nid)
        }
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
        return FlatGraph(
            graph=flat,
            flat_to_source={nid: nid for nid in flat_nodes},
            networks={nid: self.nodes[nid] for nid, k in self.kinds.items() if k == NETWORK_META},
            boundaries={nid: self.nodes[nid] for nid, k in self.kinds.items() if k != NETWORK_META},
            found=self.found,
            source=self.graph,
        )


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

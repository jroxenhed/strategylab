"""Stream schemas and the static check of a graph (plan D4, section 3).

``analyze(graph)`` walks the nodes in topological order without any market
data and works out, for every node:

- which wires feed which input ports (and refuses wires into ports a node
  does not have, into nodes that take no input, or out of nodes with no
  output);
- its input stream schema: the union of the output schemas wired into it,
  with the clash rules of plan D4;
- what each of its ``attr`` / ``attr_list`` params reads, checked for
  presence and type;
- the names its ``write`` params write;
- its output stream schema.

Bypass is pass-through (plan D5): a bypassed node's output is the stream on
its in0.  The names it would have written become *disabled* names.  A node
that reads a disabled name gets nothing there: by default a required read
turns the node off (it then passes in0 on too, and its own writes are
disabled), and a list read drops that entry.  This is the "soft disable that
flows downstream" the Wave 0 engine had, now stated on attributes.

The node-specific rules (a threshold that must parse, a comparison whose b
may be a threshold) live in each node type's ``check`` hook, which gets a
NodeCheck.  The domain layer (nodebuilder.compile) adds its own whole-graph
rules on top.

No trading words in this module.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, NoReturn, Optional

from nodebuilder.kernel import registry as _registry
from nodebuilder.kernel.registry import NodeType, ParamSpec
from nodebuilder.kernel.stream import (
    DETAIL,
    POINT,
    STREAM_SCHEMA_VERSION,
    is_attr_name,
    prim_kind_of,
)
from nodebuilder.models import Graph, GraphValidationError, Node, port_index, topological_sort

LIVE = "live"
DISABLED = "disabled"
NONE = "none"


# ---------------------------------------------------------------------------
# Errors (re-exported by nodebuilder.evaluator under their Wave 0 names)
# ---------------------------------------------------------------------------


class GraphTypeError(GraphValidationError, TypeError):
    """A wire or a read carries the wrong kind of value, for example a number
    where a true/false signal is needed.  Still a TypeError so older callers keep
    working.  Code ``port_unknown`` for a wire into a node that takes no
    input (or out of one with no output)."""

    code = "attr_type"


class UnsupportedNodeError(GraphValidationError):
    """A node type compile cannot run.

    The read-only viewer still shows these nodes.  Compile refuses them so a
    graph never runs differently from what the canvas shows.
    """

    code = "unsupported_node"

    def __init__(self, node_id: str, node_type: str) -> None:
        super().__init__(
            f"Node {node_id!r} has type {node_type!r}, which the graph backtest "
            f"cannot run yet. Remove it or replace it with a supported node.",
            node_id=node_id,
        )
        self.node_type = node_type


class UnknownNodeTypeError(UnsupportedNodeError):
    """A node type that is not registered at all (a typo, or a node from a
    newer version).  Still an UnsupportedNodeError for older callers."""

    code = "unknown_node_type"


def coded(exc: GraphValidationError, code: str, *, param: Optional[str] = None,
          port: Optional[str] = None) -> GraphValidationError:
    """Tag an error with its diagnostic code (plan 4.2) and the param or
    input port it is about, so /validate can point at the exact field."""
    exc.code = code
    exc.param = param
    exc.port = port
    return exc


# ---------------------------------------------------------------------------
# Stream schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttrInfo:
    """One attribute on a stream."""
    name: str
    dtype: str
    written_by: Optional[str]
    kind: str = POINT

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "dtype": self.dtype, "written_by": self.written_by}


@dataclass(frozen=True)
class Hidden:
    """A name hidden by a clash: where the clash formed and who wrote it."""
    origin: Optional[str]
    writers: tuple[Optional[str], ...]


class StreamSchema:
    """What a stream carries, without the values.

    points / detail : live attributes, in stream order.
    prims           : reserved, always empty until W8.
    disabled        : names only a bypassed (or off) node writes; reading one
                      reads nothing.  Internal: not serialized.
    hidden          : names hidden by a clash.  Internal: not serialized.
    """

    __slots__ = ("points", "detail", "prims", "disabled", "hidden")

    def __init__(
        self,
        points: Optional[dict[str, AttrInfo]] = None,
        detail: Optional[dict[str, AttrInfo]] = None,
        prims: Optional[dict[str, Any]] = None,
        disabled: Optional[dict[str, AttrInfo]] = None,
        hidden: Optional[dict[str, Any]] = None,
    ) -> None:
        self.points = points if points is not None else {}
        self.detail = detail if detail is not None else {}
        self.prims = prims if prims is not None else {}
        self.disabled = disabled if disabled is not None else {}
        self.hidden = hidden if hidden is not None else {}

    @classmethod
    def empty(cls) -> "StreamSchema":
        return cls()

    def copy(self) -> "StreamSchema":
        return StreamSchema(dict(self.points), dict(self.detail), dict(self.prims),
                            dict(self.disabled), dict(self.hidden))

    def lookup(self, name: str) -> Optional[AttrInfo]:
        return self.points.get(name) or self.detail.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self.points or name in self.detail

    @property
    def written_by(self) -> dict[str, Optional[str]]:
        out = {n: a.written_by for n, a in self.points.items()}
        out.update({n: a.written_by for n, a in self.detail.items()})
        return out

    def to_json(self) -> dict[str, Any]:
        """The serialized form of plan section 3.3."""
        return {
            "stream_schema": STREAM_SCHEMA_VERSION,
            "points": [a.to_json() for a in self.points.values()],
            "detail": [a.to_json() for a in self.detail.values()],
            "prims": [],
        }

    def __repr__(self) -> str:  # pragma: no cover (debug aid)
        return f"StreamSchema(points={list(self.points)}, detail={list(self.detail)})"


def merge_schemas(
    parts: list[StreamSchema], origin: Optional[str] = None,
) -> tuple[StreamSchema, dict[str, tuple]]:
    """The union of *parts*, in port order, and the clashes it creates.

    Same rules as kernel.stream.merge_streams: one name from one writer is
    one attribute; one name from two writers is hidden.  A live name wins
    over a disabled one.  Returns (schema, {name: writers}) for the clashes
    formed here.
    """
    if not parts:
        return StreamSchema(), {}
    if len(parts) == 1:
        return parts[0].copy(), {}
    points: dict[str, AttrInfo] = {}
    detail: dict[str, AttrInfo] = {}
    disabled: dict[str, AttrInfo] = {}
    hidden: dict[str, Hidden] = {}
    clashes: dict[str, tuple] = {}

    def _hide(name: str, writer: Optional[str], prev: Optional[AttrInfo]) -> None:
        old = hidden.get(name)
        writers = list(old.writers) if old else []
        for w in ((prev.written_by,) if prev else ()) + (writer,):
            if w not in writers:
                writers.append(w)
        hidden[name] = Hidden(old.origin if old else origin, tuple(writers))
        if old is None:
            clashes[name] = tuple(writers)
        elif name in clashes:
            clashes[name] = tuple(writers)
        points.pop(name, None)
        detail.pop(name, None)

    for part in parts:
        for name, h in part.hidden.items():
            if name in hidden:
                continue
            hidden[name] = h
            points.pop(name, None)
            detail.pop(name, None)
        for table, out in ((part.points, points), (part.detail, detail)):
            for name, info in table.items():
                if name in hidden:
                    if name in clashes:
                        _hide(name, info.written_by, None)
                    continue
                prev = points.get(name) or detail.get(name)
                if prev is None:
                    out[name] = info
                elif prev.written_by != info.written_by or prev.kind != info.kind:
                    _hide(name, info.written_by, prev)
        for name, info in part.disabled.items():
            disabled.setdefault(name, info)
    for name in list(disabled):
        if name in points or name in detail or name in hidden:
            del disabled[name]
    return StreamSchema(points, detail, {}, disabled, hidden), clashes


# ---------------------------------------------------------------------------
# Params handed to impls and checks
# ---------------------------------------------------------------------------


class Params(dict):
    """A node's params, defaults filled in and reads/writes resolved.

    ``node_id`` names the node (use it as the writer of every attribute the
    node writes).  ``env`` is the cook environment (empty at compile time).
    """

    def __init__(self, values: Mapping[str, Any], node_id: str,
                 env: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(values)
        self.node_id = node_id
        self.env: Mapping[str, Any] = env if env is not None else {}

    def with_env(self, env: Mapping[str, Any]) -> "Params":
        return Params(self, self.node_id, env)


@dataclass
class ReadInfo:
    """What one read resolved to.

    status: ``live`` (on the input stream), ``disabled`` (only a bypassed or
    off node writes it) or ``none`` (nothing to read: the param is empty and
    its port is not wired).  explicit is True when the param (or its catalog
    default) names the attribute; False when the wire decided it.
    """
    param: str
    name: Optional[str]
    status: str
    explicit: bool
    port: Optional[str] = None
    writer: Optional[str] = None
    dtype: Optional[str] = None
    kind: Optional[str] = None


# ---------------------------------------------------------------------------
# NodeCheck: what a node type's check hook gets
# ---------------------------------------------------------------------------


class NodeCheck:
    """The compile-time view of one node, for its type's ``check`` hook.

    A hook may:
    - read ``ctx.reads[param]`` (a ReadInfo, or a list for attr_list),
    - change ``ctx.params`` (store a parsed number, say),
    - raise with ``ctx.fail(code, message, param=..., port=...)``,
    - add a warning with ``ctx.warn(code, message, param=...)``,
    - turn the node off with ``ctx.set_off(reason)`` (it then passes its
      in0 on and its writes are disabled),
    - mark params as handled (``ctx.handled.add("a")``) so the generic rules
      for an empty or disabled read are skipped for them,
    - leave notes for the domain layer in ``ctx.annotations``.
    """

    def __init__(self, node: Node, node_type: NodeType, params: Params,
                 wires: list, inputs: StreamSchema) -> None:
        self.node = node
        self.node_type = node_type
        self.params = params
        self.wires = wires
        self.inputs = inputs
        self.reads: dict[str, Any] = {}
        self.handled: set[str] = set()
        self.off_reason: Optional[str] = None
        self.annotations: dict[str, Any] = {}
        self.warnings: list[tuple[str, str, Optional[str]]] = []

    @property
    def node_id(self) -> str:
        return self.node.id

    @property
    def raw(self) -> dict[str, Any]:
        """The params as stored on the node (no defaults)."""
        return self.node.params or {}

    @property
    def label(self) -> str:
        return self.node.type

    def wired_ports(self) -> list[int]:
        return [k for k, _w in self.wires]

    def fail(self, code: str, message: str, *, param: Optional[str] = None,
             port: Optional[str] = None, cls: type = GraphValidationError,
             node_id: Optional[str] = None) -> NoReturn:
        raise coded(cls(message, node_id=node_id or self.node.id), code, param=param, port=port)

    def warn(self, code: str, message: str, *, param: Optional[str] = None) -> None:
        self.warnings.append((code, message, param))

    def set_off(self, reason: str) -> None:
        self.off_reason = reason

    def number_params(self) -> None:
        """Check every int and number param against its spec, and store it
        as a plain int or float.

        int params must be whole numbers, number params finite numbers, both
        within min..max.  Raises param_invalid or param_out_of_range with
        ``param`` set.
        """
        nt = self.node_type
        for spec in nt.params:
            if spec.type not in ("int", "number"):
                continue
            value = self.params.get(spec.name)
            if value is None and spec.optional:
                continue
            number = _as_number(value, spec.type)
            if number is None or not math.isfinite(number) or (
                spec.type == "int" and number != int(number)
            ):
                kind = "a whole number" if spec.type == "int" else "a number"
                self.fail("param_invalid",
                          f"{nt.name} {self.node.id!r} {spec.name} needs {kind}, got {value!r}.",
                          param=spec.name)
            lo, hi = spec.min, spec.max
            if (lo is not None and number < lo) or (hi is not None and number > hi):
                bounds = (
                    f"between {lo:g} and {hi:g}" if lo is not None and hi is not None
                    else f">= {lo:g}" if lo is not None else f"<= {hi:g}"
                )
                self.fail("param_out_of_range",
                          f"{nt.name} {self.node.id!r} {spec.name} must be {bounds}, got {value!r}.",
                          param=spec.name)
            self.params[spec.name] = int(number) if spec.type == "int" else float(number)


def _as_number(value: Any, kind: str) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        if kind == "int" and isinstance(value, str):
            return float(int(value.strip()))
        if isinstance(value, (str, int, float)):
            return float(value)
    except (TypeError, ValueError):
        return None
    return None


# ---------------------------------------------------------------------------
# Analysis results
# ---------------------------------------------------------------------------

RUN = "run"            # the node runs its impl
PASS = "pass"          # bypassed or off: its output is the stream on in0
INACTIVE = "inactive"  # a catalog-only node left unwired: no output
BROKEN = "broken"      # the node has an error
SKIPPED = "skipped"    # an input has an error, so the node was not checked


@dataclass
class NodeResult:
    node_id: str
    type: str
    status: str
    params: Optional[Params] = None
    reads: dict[str, Any] = field(default_factory=dict)
    inputs: tuple[str, ...] = ()          # upstream node ids, in port order
    pass_from: Optional[str] = None       # the upstream on in0 (PASS)
    off_reason: Optional[str] = None
    in_schema: Optional[StreamSchema] = None
    out_schema: Optional[StreamSchema] = None
    write_names: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)
    lookback: int = 0                     # this node's own lookback
    need: int = 0                         # lookback along its longest input path
    node_type: Optional[NodeType] = None

    @property
    def read_names(self) -> tuple[str, ...]:
        """Every attribute this node reads, in param order."""
        out: list[str] = []
        for r in self.reads.values():
            for item in (r if isinstance(r, list) else [r]):
                if item.status == LIVE and item.name is not None:
                    out.append(item.name)
        return tuple(out)

    @property
    def writes(self) -> tuple[str, ...]:
        names = tuple(self.write_names.values())
        if self.node_type is not None:
            names += tuple(n for n, _d in self.node_type.fixed_writes_for(self.params))
        return names


@dataclass
class Analysis:
    graph: Graph
    order: list[str]
    nodes: dict[str, NodeResult]
    found: list[tuple[Any, Optional[GraphValidationError]]]
    write_names: dict[str, dict[str, str]]

    def schemas(self) -> dict[str, StreamSchema]:
        """Each checked node's output schema, by node id."""
        return {
            nid: res.out_schema for nid, res in self.nodes.items()
            if res.out_schema is not None and res.status in (RUN, PASS, INACTIVE)
        }

    def has_errors(self) -> bool:
        return any(exc is not None for _d, exc in self.found)

    def required_lookback_bars(self) -> int:
        return max((r.need for r in self.nodes.values()), default=0)


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------


def wires_into_index(graph: Graph) -> dict[str, list]:
    """Wires into each node, ordered by input port (in0, in1, ... in10).

    The port, not the wire's place in the list, decides the order.  Wires
    without a numbered port come last, in list order.
    """
    index: dict[str, list[tuple[int, Any]]] = {}
    for i, w in enumerate(graph.wires):
        index.setdefault(w.to_path, []).append((i, w))

    def _key(item: tuple) -> tuple:
        i, w = item
        k = port_index(w.to_port)
        return (k is None, k if k is not None else 0, i)

    return {node: [w for _i, w in sorted(items, key=_key)] for node, items in index.items()}


def primary_write(node: Node, node_type: Optional[NodeType],
                  write_names: Mapping[str, Mapping[str, str]]) -> Optional[str]:
    """The node's primary write: its first write param, else its fixed primary."""
    if node_type is None:
        return None
    wps = node_type.write_params()
    if wps:
        return write_names.get(node.id, {}).get(wps[0].name, wps[0].default)
    primary = node_type.primary_for(node.params)
    if primary:
        return primary
    fixed = node_type.fixed_writes_for(node.params)
    if fixed:
        return fixed[0][0]
    return None


def assign_write_names(graph: Graph, order: Optional[list[Node]] = None) -> dict[str, dict[str, str]]:
    """The final name of every write param of every node.

    A write param that holds a valid name keeps it.  An empty one gets the
    catalog default, made unique in the graph in topological order (the
    first node writes ``@out``, the next ``@out_2``), so two unnamed nodes
    never clash.  This is what the v3 migration stores for old graphs.
    """
    if order is None:
        order = topological_sort(graph)
    taken: set[str] = set()
    for node in graph.nodes.values():
        nt = _registry.get(node.type)
        if nt is None:
            continue
        taken.update(n for n, _d in nt.fixed_writes_for(node.params))
        for spec in nt.write_params():
            value = (node.params or {}).get(spec.name)
            if is_attr_name(value):
                taken.add(value)
    out: dict[str, dict[str, str]] = {}
    for node in order:
        nt = _registry.get(node.type)
        if nt is None:
            continue
        names: dict[str, str] = {}
        for spec in nt.write_params():
            value = (node.params or {}).get(spec.name)
            if is_attr_name(value):
                names[spec.name] = value
                continue
            base = spec.default if is_attr_name(spec.default) else f"@{spec.name}"
            name, n = base, 2
            while name in taken:
                name = f"{base}_{n}"
                n += 1
            taken.add(name)
            names[spec.name] = name
        out[node.id] = names
    return out


def _list_value(value: Any) -> Optional[list]:
    """An attr_list value as a list of strings, or None when it is not one."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [v for v in value.replace(",", " ").split() if v]
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return [v for v in value if v]
    return None


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _type_ok(want: Optional[str], info: AttrInfo) -> bool:
    if want in (None, "any"):
        return True
    if want == "bool":
        return info.dtype == "bool"
    if want == "float":
        return info.dtype in ("float", "int", "bool")
    return info.dtype == want


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def analyze(
    graph: Graph,
    *,
    unknown_error: Optional[Callable[[Node], GraphValidationError]] = None,
    preset_broken: Iterable[str] = (),
) -> Analysis:
    """Check *graph* and describe every node's streams (no data needed).

    Every problem is collected, not only the first: ``Analysis.found`` holds
    (Diagnostic, error or None) pairs in walk order; warnings have no error.
    A node with an error is BROKEN, and nodes that read from a broken node
    are SKIPPED without a diagnostic of their own, so one mistake does not
    show up as a chain of errors below it.  Nodes that take no input are
    always checked: any wire into them is wrong.

    unknown_error builds the error for a node type nobody registered.
    preset_broken lists nodes the caller already reported: they count as
    broken and are not checked again.
    """
    from nodebuilder.diagnostics import from_error
    from nodebuilder.diagnostics import make as make_diagnostic

    order_nodes = topological_sort(graph)
    order = [n.id for n in order_nodes]
    inbound = wires_into_index(graph)
    write_names = assign_write_names(graph, order_nodes)
    strict_labels = _registry.strict_legacy_labels()

    results: dict[str, NodeResult] = {}
    found: list[tuple[Any, Optional[GraphValidationError]]] = []
    broken: set[str] = set()
    # (origin, name) -> writers, for clashes nothing has read (yet).
    pending_clashes: dict[tuple, tuple] = {}
    read_clashes: set[tuple] = set()

    def _record(exc: GraphValidationError) -> None:
        found.append((from_error(exc), exc))

    def _warn(code: str, message: str, node_id: Optional[str], param: Optional[str] = None) -> None:
        found.append((make_diagnostic(code, message, node_id=node_id, param=param), None))

    for nid in preset_broken:
        if nid in graph.nodes:
            broken.add(nid)
            results[nid] = NodeResult(nid, graph.nodes[nid].type, BROKEN,
                                      node_type=_registry.get(graph.nodes[nid].type))

    for node in order_nodes:
        if node.id in results:
            continue
        nt = _registry.get(node.type)
        wires = inbound.get(node.id, [])
        if nt is not None and nt.inputs.max > 0 and any(w.from_path in broken for w in wires):
            broken.add(node.id)
            results[node.id] = NodeResult(node.id, node.type, SKIPPED, node_type=nt)
            continue
        try:
            try:
                res, warnings = _analyze_node(
                    graph, node, nt, wires, results, write_names, strict_labels,
                    unknown_error, pending_clashes, read_clashes,
                )
            except GraphValidationError:
                raise  # GraphTypeError is a TypeError too; keep its own code
            except (TypeError, ValueError) as raw:
                # A value the check could not read (a list where a number
                # goes, say).  Name the node instead of failing with a 500.
                raise coded(GraphValidationError(
                    f"{node.type} node {node.id!r} has a param compile cannot read: {raw}",
                    node_id=node.id,
                ), "param_invalid") from raw
        except GraphValidationError as exc:
            _record(exc)
            broken.add(node.id)
            results[node.id] = NodeResult(node.id, node.type, BROKEN, node_type=nt)
            continue
        results[node.id] = res
        for code, message, param in warnings:
            _warn(code, message, node.id, param)

    for (origin, name), writers in pending_clashes.items():
        if (origin, name) in read_clashes or origin in broken:
            continue
        who = " and ".join(repr(w) for w in writers)
        _warn(
            "attr_shadowed",
            f"{origin!r} gets {name} from {who}, so {name} is hidden below it.  "
            f"Nothing reads it, so the result does not change; rename one of them.",
            origin,
        )

    return Analysis(graph, order, results, found, write_names)


def _analyze_node(
    graph: Graph,
    node: Node,
    nt: Optional[NodeType],
    wires: list,
    results: dict[str, NodeResult],
    write_names: dict[str, dict[str, str]],
    strict_labels: frozenset[str],
    unknown_error: Optional[Callable[[Node], GraphValidationError]],
    pending_clashes: dict[tuple, tuple],
    read_clashes: set[tuple],
) -> tuple[NodeResult, list]:
    """Check one node.  Raises GraphValidationError on its first problem."""
    if nt is None:
        raise (unknown_error(node) if unknown_error else UnknownNodeTypeError(node.id, node.type))

    if not nt.compile_active:
        # Shown on the canvas, not run.  Unwired it changes nothing; wired it
        # would look like it does something, so refuse it.
        if wires:
            raise coded(UnsupportedNodeError(node.id, f"{node.type} (wired)"), "unsupported_node")
        return NodeResult(node.id, node.type, INACTIVE, out_schema=StreamSchema(),
                          node_type=nt), []

    # A node that takes no input: a wire into it carries nothing.
    if nt.inputs.max == 0 and wires:
        w = wires[0]
        raise coded(GraphTypeError(
            f"{node.type} node {node.id!r} takes no input, but {w.from_path!r} is wired "
            f"into it.  Delete the wire.",
            node_id=node.id,
        ), "port_unknown", port=w.to_port)

    # A wire out of a node with no output carries nothing.
    for w in wires:
        src = graph.nodes.get(w.from_path)
        st = _registry.get(src.type) if src is not None else None
        if st is not None and not st.has_output:
            raise coded(GraphTypeError(
                f"Wire {w.id!r} comes out of {w.from_path!r} (type={src.type!r}), which "
                f"has no output.  Delete the wire.",
                node_id=w.from_path,
            ), "port_unknown")

    used = _ports_in_use(node, nt, wires)
    _check_param_values(node, nt)

    names = write_names.get(node.id, {})
    params = Params(_param_values(node, nt, names), node.id)
    in0 = next((w.from_path for k, w in used if k == 0), None)

    pass_warnings: list[tuple[str, str, Optional[str]]] = []

    def _passed_on(reason: str) -> NodeResult:
        base = results[in0].out_schema if in0 is not None else StreamSchema()
        out = base.copy()
        for name, kind, dtype in _write_list(nt, names, params):
            if name not in out and name not in out.hidden:
                out.disabled[name] = AttrInfo(name, dtype, node.id, kind)
            elif name in out:
                # An upstream node writes the same name.  A reader wired to
                # this node by default still turns off (_resolve_reads), but
                # an explicit read of the name now gets the upstream column:
                # say so, as the active node did (F435 W2 KC-2).
                prev = out.lookup(name)
                param = next((p for p, n in names.items() if n == name), None)
                pass_warnings.append((
                    "attr_shadowed",
                    f"{node.type} {node.id!r} is {'bypassed' if reason == 'bypassed' else 'off'}, "
                    f"so a node that names {name} reads the {name} written by "
                    f"{prev.written_by!r} upstream instead.",
                    param,
                ))
        need = results[in0].need if in0 is not None else 0
        return NodeResult(node.id, node.type, PASS, params=params, pass_from=in0,
                          off_reason=reason, out_schema=out, write_names=dict(names),
                          need=need, node_type=nt)

    if node.bypass and nt.bypassable:
        return _passed_on("bypassed"), pass_warnings

    upstream = [w.from_path for _k, w in used]
    merged, clashes = merge_schemas([results[u].out_schema for u in upstream], origin=node.id)
    for name, writers in clashes.items():
        pending_clashes[(node.id, name)] = writers

    ctx = NodeCheck(node, nt, params, used, merged)
    _resolve_reads(graph, node, nt, ctx, used, merged, results, write_names, strict_labels,
                   read_clashes)
    if nt.check is not None:
        nt.check(ctx)
    _finish_reads(ctx)

    if ctx.off_reason is not None:
        res = _passed_on(ctx.off_reason)
        res.reads = ctx.reads
        res.annotations = ctx.annotations
        res.inputs = tuple(upstream)
        return res, list(ctx.warnings) + pass_warnings

    out = merged.copy()
    warnings = list(ctx.warnings)
    for name, kind, dtype in _write_list(nt, names, params):
        prev = out.lookup(name)
        if prev is not None and prev.written_by != node.id:
            param = next((p for p, n in names.items() if n == name), None)
            warnings.append((
                "attr_shadowed",
                f"{node.type} {node.id!r} writes {name}, which replaces the {name} "
                f"written by {prev.written_by!r} upstream.",
                param,
            ))
            out.points.pop(name, None)
            out.detail.pop(name, None)
        out.hidden.pop(name, None)
        out.disabled.pop(name, None)
        info = AttrInfo(name, dtype, node.id, kind)
        if kind == DETAIL:
            out.detail[name] = info
        else:
            out.points[name] = info

    own = int(nt.lookback(params)) if nt.lookback is not None else 0
    need = own + max((results[u].need for u in upstream), default=0)
    return NodeResult(
        node.id, node.type, RUN, params=params, reads=ctx.reads, inputs=tuple(upstream),
        in_schema=merged, out_schema=out, write_names=dict(names),
        annotations=ctx.annotations, lookback=own, need=need, node_type=nt,
    ), warnings


def _flag_on(value: Any) -> bool:
    """A bool param as stored: True, or the editor's text "true"."""
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _write_list(nt: NodeType, names: Mapping[str, str],
                params: Optional[Mapping[str, Any]] = None) -> list[tuple[str, str, str]]:
    """(name, kind, dtype) for everything the node writes.

    A write is detail when the type lists it in ``detail_writes``, or when its
    ``meta["detail_if"]`` names a bool param that is on for this node (the
    constant node's ``as_detail``).
    """
    detail_if = nt.meta.get("detail_if", {}) if nt.meta else {}
    out = [(n, POINT, d) for n, d in nt.fixed_writes_for(params)]
    for spec in nt.write_params():
        flag = detail_if.get(spec.name)
        on = flag is not None and params is not None and _flag_on(params.get(flag))
        kind = DETAIL if spec.name in nt.detail_writes or on else POINT
        out.append((names.get(spec.name, spec.default), kind, spec.dtype or "float"))
    return out


def _ports_in_use(node: Node, nt: NodeType, wires: list) -> list[tuple[int, Any]]:
    """(port index, wire) for each wire on a port the node has.

    A wire on a port the node does not have is refused (port_unknown).  The
    one exception: some v1/v2 graphs wired one node into another three times
    (in0, in1, in2).  A wire from the same node as a wire on a real port
    adds nothing, so it is left out instead.
    """
    limit = nt.inputs.max
    valid: list[tuple[int, Any]] = []
    extra: list = []
    for w in wires:
        k = port_index(w.to_port)
        if k is None or k >= limit:
            extra.append(w)
        else:
            valid.append((k, w))
    sources = {w.from_path for _k, w in valid}
    for w in extra:
        if w.from_path in sources:
            continue
        labels = [p.label for p in nt.inputs.ports]
        takes = (
            f"it takes in0 to in{limit - 1}" if nt.inputs.dynamic
            else f"it takes {', '.join(f'in{i} ({lbl})' for i, lbl in enumerate(labels))}"
        )
        raise coded(GraphValidationError(
            f"{node.type} node {node.id!r} has no input {w.to_port!r}; {takes}.",
            node_id=node.id,
        ), "port_unknown", port=w.to_port)
    return valid


def _check_param_values(node: Node, nt: NodeType) -> None:
    """Every stored param must be a value its kind can hold."""
    for name, value in (node.params or {}).items():
        spec = nt.param(name)
        kind = spec.type if spec is not None else None
        bad = False
        if kind == "attr":
            if value not in (None, ""):
                if prim_kind_of(value):
                    raise coded(GraphValidationError(
                        f"{node.type} {node.id!r} reads {value}, but nothing produces "
                        f"{prim_kind_of(value)} primitives yet.",
                        node_id=node.id,
                    ), "prims_no_producer", param=name)
                bad = not is_attr_name(value)
        elif kind == "attr_list":
            items = _list_value(value)
            if items is None:
                bad = True
            else:
                for item in items:
                    if prim_kind_of(item):
                        raise coded(GraphValidationError(
                            f"{node.type} {node.id!r} reads {item}, but nothing produces "
                            f"{prim_kind_of(item)} primitives yet.",
                            node_id=node.id,
                        ), "prims_no_producer", param=name)
                bad = not all(is_attr_name(v) for v in items)
        elif kind == "write":
            bad = value not in (None, "") and not is_attr_name(value)
        elif kind == "select":
            bad = not (_is_scalar(value) or (
                isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value)))
        else:
            bad = not _is_scalar(value)
        if bad:
            what = {
                "attr": "an attribute name like @name",
                "attr_list": "a list of attribute names",
                "write": "an attribute name like @name",
            }.get(kind or "", "a plain value")
            raise coded(GraphValidationError(
                f"{node.type} {node.id!r} param {name!r} must be {what}, got {value!r}.",
                node_id=node.id,
            ), "param_invalid", param=name)


def _param_values(node: Node, nt: NodeType, names: Mapping[str, str]) -> dict[str, Any]:
    """Defaults, overlaid with the stored params, writes set to final names."""
    values: dict[str, Any] = {p.name: p.default for p in nt.params}
    for k, v in (node.params or {}).items():
        values[k] = v
    for spec in nt.params:
        if spec.type == "write":
            values[spec.name] = names.get(spec.name, spec.default)
        elif spec.type == "attr" and values.get(spec.name) == "":
            values[spec.name] = None
    return values


def _resolve_reads(graph, node, nt, ctx, used, merged, results, write_names,
                   strict_labels, read_clashes) -> None:
    wired = {k: w for k, w in used}

    def _param_port(spec: ParamSpec) -> Optional[str]:
        """The port whose wire would feed *spec* (for messages)."""
        span = max(len(nt.inputs.ports), max(wired, default=-1) + 1)
        for k in range(span):
            p = nt.param_for_port(k)
            if p is not None and p.name == spec.name:
                return f"in{k}"
        return None

    def _default_name(wire, param: str) -> Optional[str]:
        src = graph.nodes[wire.from_path]
        st = _registry.get(src.type)
        label = wire.attr if param not in nt.legacy_ignore else None
        if label and st is not None:
            target = st.legacy_reads.get(label)
            if target is not None:
                return write_names.get(src.id, {}).get(target, target)
            if label in strict_labels:
                raise coded(GraphTypeError(
                    f"Wire {wire.id!r} reads {label!r} from {src.id!r}, but a {src.type!r} "
                    f"node does not produce {label!r}.",
                    node_id=node.id,
                ), "attr_type", port=wire.to_port)
        return primary_write(src, st, write_names)

    def _through_off_node(spec: ParamSpec, wire, name: str) -> Optional[ReadInfo]:
        """A wire-decided read of a name the wired node writes itself, when
        that node is bypassed or off: DISABLED, even if an upstream node
        writes the same name and it is live on the stream (F435 W2 KC-2).
        Bypassing a node must turn its default readers off, never quietly
        point them at another node's column."""
        src = results.get(wire.from_path)
        if src is None or src.status != PASS or src.node_type is None:
            return None
        for wname, kind, dtype in _write_list(src.node_type, src.write_names, src.params):
            if wname == name:
                return ReadInfo(spec.name, name, DISABLED, False, wire.to_port, src.node_id,
                                dtype, kind)
        return None

    def _lookup(spec: ParamSpec, name: str, explicit: bool, port: Optional[str]) -> ReadInfo:
        if prim_kind_of(name):
            raise coded(GraphValidationError(
                f"{node.type} {node.id!r} reads {name}, but nothing produces "
                f"{prim_kind_of(name)} primitives yet.",
                node_id=node.id,
            ), "prims_no_producer", param=spec.name)
        info = merged.lookup(name)
        if info is not None:
            if not _type_ok(spec.dtype, info):
                have = f"a {info.dtype}" + (" detail value" if info.kind == DETAIL else "")
                want = {"bool": "a true/false signal", "float": "a number"}.get(
                    spec.dtype or "", f"a {spec.dtype}")
                raise coded(GraphTypeError(
                    f"{node.type} {node.id!r} reads {name} for {spec.name}, which needs "
                    f"{want}, but {name} (written by {info.written_by!r}) is {have}.",
                    node_id=node.id if explicit else (info.written_by or node.id),
                ), "attr_type", param=spec.name if explicit else None,
                    port=port if explicit else None)
            return ReadInfo(spec.name, name, LIVE, explicit, port, info.written_by,
                            info.dtype, info.kind)
        hidden = merged.hidden.get(name)
        if hidden is not None:
            read_clashes.add((hidden.origin, name))
            who = " and ".join(repr(w) for w in hidden.writers)
            where = "" if hidden.origin == node.id else f" (they meet at {hidden.origin!r})"
            raise coded(GraphValidationError(
                f"{node.type} {node.id!r} reads {name}, but {name} comes from {who}{where}.  "
                f"Rename one of them.",
                node_id=node.id,
            ), "attr_clash", param=spec.name, port=port)
        off = merged.disabled.get(name)
        if off is not None:
            return ReadInfo(spec.name, name, DISABLED, explicit, port, off.written_by,
                            off.dtype, off.kind)
        if not merged.points and not merged.detail and not used:
            return ReadInfo(spec.name, None, NONE, explicit, port or _param_port(spec))
        raise coded(GraphValidationError(
            f"{node.type} {node.id!r} reads {name}, but no input provides it.",
            node_id=node.id,
        ), "attr_missing", param=spec.name, port=port)

    for spec in nt.read_params():
        raw = (node.params or {}).get(spec.name)
        if spec.type == "attr":
            if raw not in (None, ""):
                ctx.reads[spec.name] = _lookup(spec, raw, True, None)
                continue
            if isinstance(spec.default, str) and spec.default:
                ctx.reads[spec.name] = _lookup(spec, spec.default, True, None)
                continue
            ports = nt.ports_for_param(spec.name, sorted(wired))
            if not ports:
                ctx.reads[spec.name] = ReadInfo(spec.name, None, NONE, False, _param_port(spec))
                continue
            wire = wired[ports[0]]
            name = _default_name(wire, spec.name)
            ctx.reads[spec.name] = (
                (_through_off_node(spec, wire, name) or _lookup(spec, name, False, wire.to_port))
                if name is not None
                else ReadInfo(spec.name, None, NONE, False, wire.to_port)
            )
        else:
            items = _list_value(raw) or []
            if items:
                ctx.reads[spec.name] = [_lookup(spec, n, True, None) for n in items]
                continue
            out = []
            for k in nt.ports_for_param(spec.name, sorted(wired)):
                wire = wired[k]
                name = _default_name(wire, spec.name)
                if name is not None:
                    out.append(_through_off_node(spec, wire, name)
                               or _lookup(spec, name, False, wire.to_port))
            ctx.reads[spec.name] = out


def _finish_reads(ctx: NodeCheck) -> None:
    """The generic rules for reads the type's check did not handle.

    A required read with nothing to read is missing_input.  A required read
    of a disabled name turns the node off.  An optional one resolves to
    None.  A list read drops disabled names; with nothing left, the node is
    off (some were disabled) or missing_input (none were wired).
    Then every read param in ctx.params is set to the resolved name(s).
    """
    nt = ctx.node_type
    for spec in nt.read_params():
        info = ctx.reads.get(spec.name)
        if spec.type == "attr":
            if spec.name not in ctx.handled and info is not None:
                if info.status == NONE and not spec.optional:
                    if not ctx.wires:
                        ctx.fail("missing_input", f"{nt.name} node {ctx.node_id!r} has no inputs.",
                                 port=info.port or "in0")
                    ctx.fail("missing_input",
                             f"{nt.name} node {ctx.node_id!r} has nothing to read for "
                             f"{spec.name}: wire {info.port or 'its input'} or name an attribute.",
                             param=spec.name, port=info.port)
                if info.status == DISABLED and not spec.optional and ctx.off_reason is None:
                    ctx.set_off(f"{spec.name} reads {info.name}, which a bypassed node writes")
            live = info is not None and info.status == LIVE
            ctx.params[spec.name] = info.name if live else None
        else:
            items = info or []
            live = [r.name for r in items if r.status == LIVE]
            if spec.name not in ctx.handled and not live and not spec.optional:
                if any(r.status == DISABLED for r in items):
                    if ctx.off_reason is None:
                        ctx.set_off(f"every {spec.name} input comes from a bypassed node")
                else:
                    ctx.fail("missing_input", f"{nt.name} node {ctx.node_id!r} has no inputs.",
                             port="in0")
            ctx.params[spec.name] = live

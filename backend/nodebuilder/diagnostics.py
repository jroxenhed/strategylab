"""Diagnostics: every problem a graph has, in one shape (plan section 4.2).

``validate_graph_data`` is the core of POST /api/nodebuilder/validate.  It
reads the graph only: it never fetches market data and never runs a
backtest.  Graph routes also use it to fill the ``diagnostics`` list of a
400 reply (plan section 4.4).

Pure functions, no I/O.
"""
from __future__ import annotations

from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel, ValidationError

from nodebuilder.models import (
    CyclicGraphError,
    DanglingWireError,
    Graph,
    GraphValidationError,
    IncompatibleGraphVersionError,
    InvalidParentError,
    Node,
    Wire,
)

Severity = Literal["error", "warning", "info"]

# ---------------------------------------------------------------------------
# Codes.  Each wave adds its codes here (plan 4.2 table).
# ---------------------------------------------------------------------------

SEVERITY_BY_CODE: dict[str, Severity] = {
    # W1 errors (plan table)
    "missing_terminal": "error",
    "unsupported_node": "error",
    "unknown_node_type": "error",
    "dangling_wire": "error",
    "cycle": "error",
    "missing_input": "error",
    "param_invalid": "error",
    "param_out_of_range": "error",
    "family_cap": "error",
    "name_invalid": "error",
    "name_duplicate": "error",
    # W1 errors the table does not name.  graph_invalid: the body does not
    # parse (bad field types, unsupported version).  parent_missing and
    # parent_cycle come from migrate.name_issues.  port_duplicate: two wires
    # into one input port.  duplicate_terminal: a second Entry or Exit.
    # request_invalid: a 400 that is not about the graph (bad source, no data).
    "graph_invalid": "error",
    "parent_missing": "error",
    "parent_cycle": "error",
    "port_duplicate": "error",
    "duplicate_terminal": "error",
    "request_invalid": "error",
    # W2 codes W1 already uses: attr_type for a wire that carries the wrong
    # kind of value (a price into Entry), port_unknown for a wire into a port
    # that does not exist (a third input on a comparison, anything into a
    # Ticker) or out of a node with no output.
    "attr_type": "error",
    "port_unknown": "error",
    # W2 attribute checks (plan D4).  attr_missing: a param reads a name no
    # input provides.  attr_clash: a param reads a name two different nodes
    # write.  prims_no_producer: a read of a primitive attribute
    # (@trade.pnl), which nothing produces before W8.
    "attr_missing": "error",
    "attr_clash": "error",
    "prims_no_producer": "error",
    # W1 warnings
    "exit_unconnected": "warning",
    "size_unit_suspect": "warning",
    # W2 warning: a clash nothing reads (the name is hidden below the node),
    # or a node writing a name that replaces one from upstream.
    "attr_shadowed": "warning",
    # W5 errors (plan table).  group_invalid: a bad output group (nested,
    # duplicate name, no primary Ticker).  group_terminal_outside: a terminal
    # outside every group in a graph that has groups.  wire_crosses_network:
    # a wire that leaves its network other than through a boundary node.
    # boundary_invalid: a bad subnet_input / subnet_output.
    # group_duplicate_terminal: a second terminal of one kind (per side) in
    # an Output Group, on each of the two.  ticker_missing: an Output Group
    # whose ticker path is empty or does not point at a Ticker.  (A graph
    # with no group keeps duplicate_terminal for a second Entry or Exit.)
    "group_invalid": "error",
    "group_terminal_outside": "error",
    "group_duplicate_terminal": "error",
    "ticker_missing": "error",
    "wire_crosses_network": "error",
    "boundary_invalid": "error",
    # W5 warnings.  setting_shadowed: a root settings node that a group
    # terminal overrides.  group_weight_zero: an Output Group with capital
    # weight 0 (it compiles but gets no capital and does not trade, S32a).
    # setting_unscoped: a settings node no group's scope reaches (one inside
    # a Subnet within a group, plan D7).
    "setting_shadowed": "warning",
    "group_weight_zero": "warning",
    "setting_unscoped": "warning",
}

CODES: frozenset[str] = frozenset(SEVERITY_BY_CODE)


class Diagnostic(BaseModel):
    """One problem, tied to a node (and a param or port) when it can be."""

    node_id: Optional[str] = None
    path: Optional[str] = None
    severity: Severity = "error"
    code: str
    message: str
    param: Optional[str] = None
    port: Optional[str] = None
    line: Optional[int] = None
    col: Optional[int] = None
    end_line: Optional[int] = None
    end_col: Optional[int] = None


def make(
    code: str,
    message: str,
    *,
    node_id: Optional[str] = None,
    param: Optional[str] = None,
    port: Optional[str] = None,
    severity: Optional[Severity] = None,
) -> Diagnostic:
    """Build a Diagnostic.  The severity comes from the code unless given."""
    return Diagnostic(
        node_id=node_id,
        severity=severity or SEVERITY_BY_CODE.get(code, "error"),
        code=code,
        message=message,
        param=param,
        port=port,
    )


def code_for_error(exc: BaseException) -> str:
    """The diagnostic code for an exception.

    Compile tags the errors it raises with ``.code``.  Errors raised while
    the Graph model is built are mapped by class.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    if isinstance(exc, CyclicGraphError):
        return "cycle"
    if isinstance(exc, DanglingWireError):
        return "dangling_wire"
    if isinstance(exc, InvalidParentError):
        return "parent_cycle" if "inside itself" in str(exc) else "parent_missing"
    if isinstance(exc, (IncompatibleGraphVersionError, ValidationError)):
        return "graph_invalid"
    if isinstance(exc, GraphValidationError):
        return "graph_invalid"
    return "request_invalid"


def error_message(exc: BaseException) -> str:
    """A short readable message; pydantic errors name the failing field."""
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            first = errors[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            return f"{where}: {first.get('msg')}" if where else str(first.get("msg"))
    return str(exc) or exc.__class__.__name__


def from_error(exc: BaseException) -> Diagnostic:
    """A Diagnostic for one raised error (code, node, param and port kept)."""
    return make(
        code_for_error(exc),
        error_message(exc),
        node_id=getattr(exc, "node_id", None),
        param=getattr(exc, "param", None),
        port=getattr(exc, "port", None),
    )


def has_errors(diagnostics: Iterable[Diagnostic]) -> bool:
    return any(d.severity == "error" for d in diagnostics)


def error_body(exc: BaseException, diagnostics: list[Diagnostic]) -> dict[str, Any]:
    """The plan 4.4 400 body: the raised error on top, every diagnostic below.

    The raised error is always in the list, so the editor can badge its node
    even when the full check did not reach it (a data error during Run).
    """
    top = from_error(exc)
    listed = list(diagnostics)
    if top.node_id is None:
        # A parse error names no node; the full check often can.
        match = next((d for d in listed if d.code == top.code and d.node_id), None)
        if match is not None:
            top.node_id = match.node_id
    # Already listed when a diagnostic has the same code and node (a parse
    # error has no node of its own, so any entry with its code counts).
    if not any(
        d.code == top.code and (top.node_id is None or d.node_id == top.node_id)
        for d in listed
    ):
        listed.insert(0, top)
    return {
        "detail": top.message,
        "node_id": top.node_id,
        "code": top.code,
        "diagnostics": [d.model_dump() for d in listed],
    }


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


class ValidateResult(BaseModel):
    """What /validate reports: every problem, and each node's output stream.

    streams maps node id to the StreamSchema of that node's OUTPUT (plan
    3.3 form).  A node missing from it could not be checked (it has an
    error, or reads from a node that has one).
    """

    diagnostics: list[Diagnostic]
    streams: dict[str, dict[str, Any]] = {}


def validate_graph_data(data: Any) -> list[Diagnostic]:
    """Every problem in a raw graph (a dict as the API receives it).

    See validate_graph_full, which also returns the node streams.
    """
    return validate_graph_full(data).diagnostics


def validate_graph_full(data: Any) -> ValidateResult:
    """Every problem in a raw graph, plus each node's output stream.

    Steps: run the migration chain; check the structure piece by piece (so a
    bad name, a dangling wire and a cycle are all listed, not only the
    first); then compile, which collects its own diagnostics.  Compile is
    skipped when the graph has a cycle (nothing can be ordered) or a node
    that does not parse (compile would report a missing Entry that is only
    missing because it was left out).
    Never fetches market data.
    """
    from nodebuilder.migrate import migrate_graph_data

    if not isinstance(data, dict):
        return ValidateResult(diagnostics=[make("graph_invalid", "The graph must be a JSON object.")])
    try:
        migrated = migrate_graph_data(data)
    except GraphValidationError as exc:
        return ValidateResult(diagnostics=[from_error(exc)])
    except Exception as exc:  # garbage shapes (nodes as a list, say)
        return ValidateResult(diagnostics=[
            make("graph_invalid", f"The graph does not parse: {error_message(exc)}")
        ])

    graph: Optional[Graph] = None
    parse_error: Optional[BaseException] = None
    try:
        graph = Graph.model_validate(migrated)
    except (GraphValidationError, ValidationError, ValueError, TypeError) as exc:
        parse_error = exc

    diagnostics, nodes, wires, can_compile = _check_structure(migrated)
    if parse_error is not None and not has_errors(diagnostics):
        # The piece-by-piece check missed what the model refused; say so.
        diagnostics.append(from_error(parse_error))

    streams: dict[str, dict[str, Any]] = {}
    if can_compile:
        from nodebuilder.compile import check_graph

        target = graph if graph is not None else Graph.model_construct(nodes=nodes, wires=wires)
        result = check_graph(target)
        streams = result.streams_json()
        # Compile checks ports too (so every path agrees); the structure
        # check above already listed those, so list each one once.  A wire
        # that crosses a network is reported by both the Graph model (the
        # parse error above) and flatten inside compile: list it once too.
        once = ("port_duplicate", "wire_crosses_network")
        listed = {(d.code, d.node_id, d.port) for d in diagnostics if d.code in once}
        for d in result.diagnostics:
            key = (d.code, d.node_id, d.port)
            if d.code in once and key in listed:
                continue
            if d.code in once:
                listed.add(key)
            diagnostics.append(d)

    _fill_paths(diagnostics, nodes or {})
    return ValidateResult(diagnostics=diagnostics, streams=streams)


def _check_structure(
    data: dict,
) -> tuple[list[Diagnostic], Optional[dict[str, Node]], list[Wire], bool]:
    """Check fields, nodes, wires, names, parents, ports and cycles.

    Returns (diagnostics, the nodes that parsed, the wires safe to compile,
    whether compile can run on them).  Nodes is None when the node map
    itself is not an object.
    """
    from nodebuilder.migrate import name_issues

    diags: list[Diagnostic] = []

    # Top-level fields (meta, version, stream_schema, annotations...).
    top = {k: v for k, v in data.items() if k not in ("nodes", "wires")}
    try:
        Graph.model_validate({**top, "nodes": {}, "wires": []})
    except ValidationError as exc:
        diags.extend(_pydantic_diags(exc))
    except GraphValidationError as exc:
        diags.append(from_error(exc))

    raw_nodes = data.get("nodes", {})
    if not isinstance(raw_nodes, dict):
        diags.append(make("graph_invalid", "nodes must be an object keyed by node id."))
        return diags, None, [], False

    nodes: dict[str, Node] = {}
    all_nodes_parsed = True
    for key, raw in raw_nodes.items():
        try:
            node = Node.model_validate(raw)
        except ValidationError as exc:
            diags.extend(_pydantic_diags(exc, node_id=str(key)))
            all_nodes_parsed = False
            continue
        if node.id != key:
            diags.append(make(
                "graph_invalid",
                f"Node id {node.id!r} does not match its key {key!r}.",
                node_id=str(key),
            ))
            all_nodes_parsed = False
            continue
        nodes[key] = node

    for issue in name_issues(nodes):
        diags.append(make(issue["code"], issue["message"], node_id=issue["node_id"]))

    raw_wires = data.get("wires", [])
    if not isinstance(raw_wires, list):
        diags.append(make("graph_invalid", "wires must be a list."))
        raw_wires = []

    wires: list[Wire] = []
    for index, raw in enumerate(raw_wires):
        try:
            wire = Wire.model_validate(raw)
        except ValidationError as exc:
            diags.append(make("graph_invalid", f"Wire {index}: {error_message(exc)}"))
            continue
        missing = [
            f"{end}={path!r}"
            for end, path in (("from", wire.from_path), ("to", wire.to_path))
            if path not in nodes
        ]
        if missing:
            known_end = next((p for p in (wire.from_path, wire.to_path) if p in nodes), None)
            diags.append(make(
                "dangling_wire",
                f"Wire {wire.id!r} points at a node that does not exist ({', '.join(missing)}).",
                node_id=known_end,
                port=wire.to_port if known_end == wire.to_path else None,
            ))
            continue
        wires.append(wire)

    # One wire per input port.  Two wires on one port make the input order
    # (left or right of a comparison) a guess.
    seen_ports: set[tuple[str, str]] = set()
    for wire in wires:
        key = (wire.to_path, wire.to_port or "")
        if wire.to_port and key in seen_ports:
            diags.append(make(
                "port_duplicate",
                f"Input {wire.to_port} of {wire.to_path!r} has more than one wire.",
                node_id=wire.to_path,
                port=wire.to_port,
            ))
        seen_ports.add(key)

    on_cycle = _nodes_on_cycles(nodes, wires)
    for node_id in on_cycle:
        diags.append(make(
            "cycle",
            f"{node_id!r} sits on a loop of wires.  A graph must flow one way.",
            node_id=node_id,
        ))
    return diags, nodes, wires, all_nodes_parsed and not on_cycle


def _nodes_on_cycles(nodes: dict[str, Node], wires: list[Wire]) -> list[str]:
    """Every node that lies on a loop of wires, sorted.

    One pass of Tarjan's strongly connected components (iterative, so a long
    chain cannot hit the recursion limit).  A node is on a loop when its
    component has more than one node, or it is wired into itself.  Linear in
    nodes plus wires; the old walk from every node was quadratic.
    """
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    self_loops: set[str] = set()
    for w in wires:
        adj[w.from_path].append(w.to_path)
        if w.from_path == w.to_path:
            self_loops.add(w.from_path)

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    on_cycle: set[str] = set()
    counter = 0
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(adj[root]))]
        while work:
            v, children = work[-1]
            descended = False
            for w in children:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(adj[w])))
                    descended = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index[w])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
            if low[v] == index[v]:
                component: list[str] = []
                while True:
                    x = stack.pop()
                    on_stack.discard(x)
                    component.append(x)
                    if x == v:
                        break
                if len(component) > 1 or v in self_loops:
                    on_cycle.update(component)
    return sorted(on_cycle)


def _pydantic_diags(exc: ValidationError, node_id: Optional[str] = None) -> list[Diagnostic]:
    """One graph_invalid diagnostic per pydantic error."""
    out: list[Diagnostic] = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ())]
        where = ".".join(loc)
        message = f"{where}: {err.get('msg')}" if where else str(err.get("msg"))
        param = loc[1] if node_id is not None and len(loc) >= 2 and loc[0] == "params" else None
        out.append(make("graph_invalid", message, node_id=node_id, param=param))
    return out


def _fill_paths(diagnostics: list[Diagnostic], nodes: dict[str, Node]) -> None:
    """Set ``path`` on each diagnostic whose node has a clean parent chain."""
    for d in diagnostics:
        if d.node_id is not None and d.path is None:
            d.path = _safe_path(nodes, d.node_id)


def _safe_path(nodes: dict[str, Node], node_id: str) -> Optional[str]:
    """``/parent/name`` for a node, or None when a parent is missing or loops."""
    names: list[str] = []
    seen: set[str] = set()
    current: Optional[str] = node_id
    while current is not None:
        if current in seen or current not in nodes:
            return None
        seen.add(current)
        node = nodes[current]
        if not node.name:
            return None
        names.append(node.name)
        current = node.parent
    return "/" + "/".join(reversed(names))

"""Graph -> CompiledProgram (plan D4, D5).

The kernel (nodebuilder.kernel.schema.analyze) checks every node: ports,
attribute reads and writes, stream merges and clashes, types, bypass.  This
module adds the trading rules that are about the whole graph and builds the
program:

1. a /regime/ node is refused (until W5);
2. two wires on one input port are refused;
3. the kernel walk, in topological order;
4. one Entry, which must get a signal; one Exit, whose missing signal is a
   warning;
5. the indicator family cap.

compile() raises the first problem; compile_with_diagnostics() lists every
problem; check_graph() also returns each node's stream schema for
/validate.  Pure functions, no I/O.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from nodebuilder import trading as _trading  # noqa: F401  (registers every node type)
from nodebuilder.diagnostics import Diagnostic, from_error
from nodebuilder.diagnostics import make as make_diagnostic
from nodebuilder.evaluator import (
    _INDICATOR_FAMILY_CAP,
    NO_EXIT_ATTR,
    CompiledProgram,
    FamilyCapExceededError,
    GraphTypeError,  # noqa: F401  (re-exported for older imports)
    MissingTerminalError,
    RegimeUnsupportedError,
    SimulatorSetting,
    UnknownNodeTypeError,
    UnsupportedNodeError,
)
from nodebuilder.kernel import schema as kschema
from nodebuilder.kernel.evaluate import build_steps
from nodebuilder.kernel.schema import BROKEN, DISABLED, LIVE, RUN, SKIPPED, coded
from nodebuilder.kernel.stream import STREAM_SCHEMA_VERSION
from nodebuilder.models import Graph, GraphValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers kept for older imports
# ---------------------------------------------------------------------------


def _wires_into(graph: Graph, node_path: str) -> list:
    """The wires into *node_path*, ordered by input port (in0, in1, ... in10).

    The port, not the wire's place in the list, decides the order.  Wires
    without a numbered port come last, in list order.
    """
    return kschema.wires_into_index(graph).get(node_path, [])


def _is_rule_type(node_type: str) -> bool:
    """True for a rule indicator or condition name (stochastic, rising...).

    auto_render turns rules into nodes of these types.  When no node module
    registers them they are known ideas the graph backtest cannot run yet
    (unsupported_node), not typos (unknown_node_type).
    """
    from typing import get_args

    from signal_engine import RuleCondition, RuleIndicator

    return node_type in get_args(RuleIndicator) or node_type in get_args(RuleCondition)


def _unknown_error(node) -> GraphValidationError:
    if _is_rule_type(node.type):
        return UnsupportedNodeError(node.id, node.type)
    return UnknownNodeTypeError(node.id, node.type)


def assign_write_names(graph: Graph) -> dict[str, dict[str, str]]:
    """The name every write param of every node writes (defaults made unique).

    The v3 migration stores these on old graphs so they keep their names.
    """
    return kschema.assign_write_names(graph)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


@dataclass
class GraphCheck:
    """Everything one compile pass learns about a graph.

    program     : the CompiledProgram, or None when there is an error.
    diagnostics : every problem, in walk order.
    streams     : each checked node's OUTPUT StreamSchema, by node id (a node
                  missing here could not be checked).  ``streams_json()``
                  gives the /validate form (plan 3.3).
    """
    program: Optional[CompiledProgram]
    diagnostics: list[Diagnostic]
    streams: dict[str, Any]
    errors: list[GraphValidationError]

    def streams_json(self) -> dict[str, dict]:
        return {nid: s.to_json() for nid, s in self.streams.items()}


def compile(graph: Graph) -> CompiledProgram:  # noqa: A001 (shadows builtin "compile" intentionally)
    """Compile a Graph into a CompiledProgram.

    Raises the first error compile finds, in this order: a /regime/ node
    first, then a second wire on one input port, then nodes in topological
    order, then the Entry and Exit checks, then the indicator family cap.
    Every raised error carries ``.code`` (plan 4.2).  Use
    compile_with_diagnostics to get every problem at once.
    """
    result = check_graph(graph)
    if result.errors:
        raise result.errors[0]
    assert result.program is not None
    return result.program


def compile_with_diagnostics(graph: Graph):
    """Compile and collect every problem instead of stopping at the first.

    Returns ``(program or None, diagnostics)``.  The program is None when any
    diagnostic is an error.  Warnings (exit_unconnected, size_unit_suspect,
    attr_shadowed) do not stop the compile.  Pure: no data is fetched.
    """
    result = check_graph(graph)
    return result.program, result.diagnostics


def stream_schemas(graph: Graph) -> dict[str, dict]:
    """Each node's output stream in the /validate form, by node id."""
    return check_graph(graph).streams_json()


def check_graph(graph: Graph) -> GraphCheck:
    """One compile pass: program (or None), diagnostics and node streams."""
    found: list[tuple[Diagnostic, Optional[GraphValidationError]]] = []

    def _record(exc: GraphValidationError) -> None:
        found.append((from_error(exc), exc))

    def _warn(code: str, message: str, node_id: Optional[str]) -> None:
        found.append((make_diagnostic(code, message, node_id=node_id), None))

    # 1. Regime nodes (W5 brings regime into the graph).
    regime_nodes = [nid for nid in graph.nodes if nid.startswith("/regime/")]
    for nid in regime_nodes:
        _record(RegimeUnsupportedError(
            f"Graph contains a /regime/ node ({nid!r}). "
            "Regime is not supported in the graph evaluator yet.",
            node_id=nid,
        ))

    # 2. One wire per input port.  The Graph model refuses a second wire on a
    # port, so this only fires for a graph built without validation
    # (/validate's fallback).  It keeps compile, storage, Run and bot deploy
    # agreeing with /validate on which graphs are valid.
    seen_ports: set[tuple[str, str]] = set()
    for wire in graph.wires:
        if not wire.to_port:
            continue
        key = (wire.to_path, wire.to_port)
        if key in seen_ports:
            _record(coded(GraphValidationError(
                f"Input {wire.to_port} of {wire.to_path!r} has more than one wire.",
                node_id=wire.to_path,
            ), "port_duplicate", port=wire.to_port))
        seen_ports.add(key)

    # 3. The kernel walk.
    analysis = kschema.analyze(graph, unknown_error=_unknown_error,
                               preset_broken=regime_nodes)
    found.extend(analysis.found)
    nodes = analysis.nodes

    # 4. Terminals.
    entries = [nid for nid in analysis.order if graph.nodes[nid].type == "entry"]
    exits = [nid for nid in analysis.order if graph.nodes[nid].type == "exit"]
    for label, terminals in (("Entry", entries), ("Exit", exits)):
        for nid in terminals[1:]:
            _record(coded(GraphValidationError(
                f"The graph has more than one {label} ({terminals[0]!r} and {nid!r}).  "
                f"Join the signals with OR into one {label}.",
                node_id=nid,
            ), "duplicate_terminal"))

    entry_attr: Optional[str] = None
    entry_node: Optional[str] = None
    if not entries:
        _record(MissingTerminalError("Graph has no Entry terminal (no 'entry' node found)."))
    else:
        nid = entries[0]
        res = nodes.get(nid)
        if res is not None and res.status not in (BROKEN, SKIPPED):
            info = res.reads.get("signal")
            if info is not None and info.status == LIVE:
                entry_attr, entry_node = info.name, nid
            elif info is not None and info.status == DISABLED:
                _record(coded(MissingTerminalError(
                    f"Entry terminal {nid!r} gets no signal: its input is bypassed.",
                    node_id=nid,
                ), "missing_input", port="in0"))
            else:
                _record(coded(MissingTerminalError(
                    f"Entry terminal {nid!r} is not wired to a signal.",
                    node_id=nid,
                ), "missing_input", port="in0"))

    exit_attr: Optional[str] = None
    exit_node: Optional[str] = None
    if not exits:
        # Not an error: stops, trailing stops or the end of the data close
        # the trade.  But a strategy with no exit signal is often a mistake.
        _warn("exit_unconnected", "The graph has no Exit, so only a stop or "
              "the end of the data closes a trade.", None)
    else:
        nid = exits[0]
        res = nodes.get(nid)
        if res is not None and res.status not in (BROKEN, SKIPPED):
            info = res.reads.get("signal")
            if info is not None and info.status == LIVE:
                exit_attr, exit_node = info.name, nid
            else:
                _warn("exit_unconnected", f"Exit {nid!r} gets no signal, so only "
                      "a stop or the end of the data closes a trade.", nid)

    # 5. Indicator family cap: distinct (type, params, inputs) per family.
    distinct: dict[str, list[tuple[tuple, str]]] = {}
    for nid in analysis.order:
        res = nodes.get(nid)
        if res is None or res.status != RUN or res.node_type is None:
            continue
        family = res.node_type.meta.get("family")
        if not family:
            continue
        spec = tuple((k, res.params.get(k)) for k in res.node_type.meta.get("spec", ()))
        key = (res.type, spec, res.read_names)
        seen = distinct.setdefault(family, [])
        if all(k != key for k, _n in seen):
            seen.append((key, nid))
    for family, specs in distinct.items():
        if len(specs) > _INDICATOR_FAMILY_CAP:
            _record(FamilyCapExceededError(
                f"Too many distinct {family!r} specs ({len(specs)}); "
                f"max {_INDICATOR_FAMILY_CAP} per request",
                node_id=specs[_INDICATOR_FAMILY_CAP][1],
            ))

    errors = [exc for _d, exc in found if exc is not None]
    diagnostics = [d for d, _exc in found]
    streams = analysis.schemas()
    if errors:
        return GraphCheck(None, diagnostics, streams, errors)

    # The settings nodes' values, as the Wave 0 overlay code reads them.
    simulator_settings = [
        SimulatorSetting(key=key, value=value)
        for nid in analysis.order
        if (res := nodes.get(nid)) is not None and res.status == RUN
        for key, value in res.annotations.get("settings", ())
    ]
    assert entry_attr is not None
    program = CompiledProgram(
        steps=build_steps(analysis),
        entry_attr=entry_attr,
        exit_attr=exit_attr if exit_attr is not None else NO_EXIT_ATTR,
        simulator_settings=simulator_settings,
        entry_node=entry_node,
        exit_node=exit_node,
        required_lookback_bars=analysis.required_lookback_bars(),
        stream_schema=STREAM_SCHEMA_VERSION,
        schemas=streams,
    )
    return GraphCheck(program, diagnostics, streams, errors)

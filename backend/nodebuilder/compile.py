"""Graph -> CompiledProgram (plan D4, D5).

The kernel (nodebuilder.kernel.schema.analyze) checks every node: ports,
attribute reads and writes, stream merges and clashes, types, bypass.  This
module adds the trading rules that are about the whole graph and builds the
program:

0. library asset instances get their children from the library
   (kernel/assets.py, W6), before anything reads the nodes (a problem
   with a switched-off instance only warns);
2. two wires on one input port are refused;
3. the kernel walk over the flattened graph (networks taken out, plan D7),
   in topological order; then each locked asset instance's declared
   interface is checked against the streams it really reads and writes;
4. the terminals, per Output Group (W5): each group has one Entry, which
   must get a signal, and one Exit, whose missing signal is a warning
   (a regime_switch group has one of each per side).  A graph with no
   Output Group is one implicit group, "main", with today's rules;
5. the indicator family cap.

(Step 1, the refusal of /regime/ nodes, is gone since W5: regime is a
network and a Regime terminal in the graph, plan D8.  The numbers stay so
the steps keep their names.)

Code (W7, design note docs/plans/2026-09-29-node-builder-code-nodes-design.md):
a param read before any cook never holds code (param_not_codeable: a
Ticker's symbol, interval and prefix, every param the simulator plan or the
bot reads, a code node's lookback_bars); what each code block or Wrangle
writes enters
the stream schema (nodes_code.extra_io); every snippet is prepared, never
run (code_syntax, code_limit, ch_dynamic...); ch() references are checked
(ref_broken, ch_cycle) and order the cook (kernel/params.py); a code block
where none can run is param_invalid.  One exception to "never run": an
expression on a param of a node with a lookback that reads only params (no
@attr, no stream data) has the same value at every cook, so compile
evaluates it with the cook's own code and sizes the node's window from that
value (kernel.params.static_windows); one that fails gives the error the
cook would give.  With SL_CODE_NODES=0 (or code_switch False) nothing runs,
and every node that
holds code is code_disabled, listed first so compile() raises it.  The
steps of nodes with code get impls that run it (nodes_code.wrap_code_steps).

After the program is built, each group is planned (sim_bridge.plan_group),
which gives CompiledProgram.groups (nodes_groups.build_group_programs).

compile() raises the first problem; compile_with_diagnostics() lists every
problem; check_graph() also returns each node's stream schema and the ch()
reference edges (param_deps) for /validate.  Pure functions, no I/O.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, Optional

from nodebuilder import trading as _trading  # noqa: F401  (registers every node type)
from nodebuilder.diagnostics import Diagnostic, from_error
from nodebuilder.diagnostics import make as make_diagnostic
from nodebuilder.evaluator import (
    _INDICATOR_FAMILY_CAP,
    NO_EXIT_ATTR,
    CompiledProgram,
    FamilyCapExceededError,
    GraphTypeError,  # noqa: F401  (re-exported for older imports)
    MissingTerminalError,  # noqa: F401  (re-exported for older imports)
    UnknownNodeTypeError,
    UnsupportedNodeError,
)
from nodebuilder.kernel import assets as _assets
from nodebuilder.kernel import schema as kschema
from nodebuilder.kernel.evaluate import analyze_graph, build_steps
from nodebuilder.kernel.params import plan_params, static_windows
from nodebuilder.kernel.schema import RUN, coded
from nodebuilder.kernel.stream import STREAM_SCHEMA_VERSION
from nodebuilder.models import Graph, GraphValidationError
from nodebuilder.trading import nodes_code as _code
from nodebuilder.trading import nodes_groups as _groups

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


def _check_interfaces(flat, analysis, lookup, record) -> None:
    """interface_mismatch for each locked asset instance whose declared
    interface (the asset file's ``interface``) does not match its streams.

    writes: each declared name must be on the instance's output stream, of
    the declared class and dtype.  reads: each declared name must be on a
    stream wired into the instance.  A side whose stream could not be
    checked (an error inside, nothing wired) is skipped: its own error says
    more.  An asset saved for another stream schema version is a mismatch.
    A switched-off instance (bypassed, or inside a bypassed network) passes
    its input through and computes nothing, and an instance whose asset
    could not be expanded holds nothing: neither is checked (KA-1).
    """
    if not flat.networks:
        return
    from nodebuilder.kernel.flatten import switched_off

    schemas = analysis.schemas()
    src = flat.source if flat.source is not None else flat.graph
    filled = {n.parent for n in src.nodes.values()}
    for nid, node in flat.networks.items():
        if not _assets.is_locked_instance(node):
            continue
        if nid not in filled or switched_off(src.nodes, nid):
            continue
        ref = node.asset_ref
        try:
            asset = lookup(ref.name, ref.version)
        except Exception:  # noqa: BLE001 (expand already reported it)
            continue
        if asset is None:
            continue
        iface = _assets.asset_interface(asset)
        problems: list[str] = []
        if iface.stream_schema is not None and iface.stream_schema != STREAM_SCHEMA_VERSION:
            problems.append(f"it was saved for stream schema {iface.stream_schema}; this "
                            f"server uses {STREAM_SCHEMA_VERSION}.")
        out_id = flat.outputs.get(nid)
        out_schema = schemas.get(out_id) if out_id is not None else None
        if out_schema is not None:
            for decl in iface.writes:
                name = decl["name"]
                info = out_schema.lookup(name)
                if info is None:
                    problems.append(f"it says it writes {name}, but its output has no {name}.")
                    continue
                cls, dtype = decl.get("class"), decl.get("dtype")
                if cls in ("point", "detail") and cls != info.kind:
                    problems.append(f"it says {name} is a {cls} attribute, but it is a "
                                    f"{info.kind} attribute.")
                elif dtype not in (None, "any") and info.dtype != "any" and dtype != info.dtype:
                    problems.append(f"it says {name} is {dtype}, but it is {info.dtype}.")
        feeds = [schemas.get(f) for f in flat.inputs.get(nid, {}).values() if f is not None]
        feeds = [s for s in feeds if s is not None]
        if feeds:
            for decl in iface.reads:
                name = decl["name"]
                if not any(name in s for s in feeds):
                    problems.append(f"it says it reads {name}, but nothing wired into it "
                                    f"has {name}.")
        for message in problems:
            record(coded(GraphValidationError(
                f"Asset {ref.name} v{ref.version}: {message}", node_id=flat.to_source(nid),
            ), "interface_mismatch"))


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
    param_deps  : the ch() reference edges for /validate (W7 contract), one
                  per literal reference that points somewhere.
    analysis, flat, plan : the kernel check, the flat graph and the
                  kernel.params.ParamPlan this pass built (parse_code reads
                  a node's input stream and resolves paths with them).
    """
    program: Optional[CompiledProgram]
    diagnostics: list[Diagnostic]
    streams: dict[str, Any]
    errors: list[GraphValidationError]
    param_deps: list[dict] = field(default_factory=list)
    analysis: Any = None
    flat: Any = None
    plan: Any = None

    def streams_json(self) -> dict[str, dict]:
        return {nid: s.to_json() for nid, s in self.streams.items()}


# Every check_graph pass inside a ``capture_checks()`` block is collected,
# so a caller of a helper that compiles (diagnostics.validate_graph_full)
# can read what that pass found without compiling a second time.
_CAPTURED: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar(
    "nodebuilder_compile_checks", default=None)


@contextlib.contextmanager
def capture_checks() -> Iterator[list]:
    """Collect the GraphCheck of every check_graph call made inside the
    block (this thread and context only)."""
    bucket: list = []
    token = _CAPTURED.set(bucket)
    try:
        yield bucket
    finally:
        _CAPTURED.reset(token)


def _published(check: "GraphCheck") -> "GraphCheck":
    bucket = _CAPTURED.get()
    if bucket is not None:
        bucket.append(check)
    return check


def _as_graph_error(exc: Any) -> GraphValidationError:
    """A code diagnostic's error (a nodebuilder.code.CodeError) as a coded
    GraphValidationError with the same code, node, param and place in the
    code, so every caller that refuses a bad graph (compile, the bot routes,
    bot ticks) catches it the usual way."""
    if isinstance(exc, GraphValidationError):
        return exc
    err = coded(GraphValidationError(str(exc), node_id=getattr(exc, "node_id", None)),
                getattr(exc, "code", None) or "code_syntax", param=getattr(exc, "param", None))
    for key in ("line", "col", "end_line", "end_col"):
        setattr(err, key, getattr(exc, key, None))
    return err


def compile(graph: Graph, *, resolve: Optional[_assets.Resolver] = None,  # noqa: A001 (shadows builtin "compile" intentionally)
            code_switch: bool = True) -> CompiledProgram:
    """Compile a Graph into a CompiledProgram.

    Raises the first error compile finds, in this order: code_disabled
    (SL_CODE_NODES=0 and the graph holds code), an asset instance that
    cannot be expanded, a second wire on one input port, then nodes in
    topological order, then the Entry and Exit checks, then the indicator
    family cap, then the group plans.  Every raised error carries ``.code``
    (plan 4.2).  Use compile_with_diagnostics to get every problem at once.
    *resolve* looks up library assets (default: the registered library).
    *code_switch* False skips the kill switch: only for a caller that never
    cooks the program (a bot that runs only the price exits of an open
    position); such a compile runs no code at all.  Otherwise compile runs
    one kind of code: a param-only expression on a param of a node with a
    lookback (kernel.params.static_windows), to size its window; an error
    there carries ``code_failure`` True, a code failure like the cook's.
    """
    result = check_graph(graph, resolve=resolve, code_switch=code_switch)
    if result.errors:
        raise result.errors[0]
    assert result.program is not None
    return result.program


def compile_with_diagnostics(graph: Graph, *, resolve: Optional[_assets.Resolver] = None):
    """Compile and collect every problem instead of stopping at the first.

    Returns ``(program or None, diagnostics)``.  The program is None when any
    diagnostic is an error.  Warnings (exit_unconnected, size_unit_suspect,
    attr_shadowed) do not stop the compile.  Pure: no data is fetched.
    """
    result = check_graph(graph, resolve=resolve)
    return result.program, result.diagnostics


def stream_schemas(graph: Graph) -> dict[str, dict]:
    """Each node's output stream in the /validate form, by node id."""
    return check_graph(graph).streams_json()


def _remembering(resolve: _assets.Resolver) -> _assets.Resolver:
    """*resolve*, asked at most once per asset version in one compile."""
    cache: dict[tuple[str, int], Any] = {}

    def lookup(name: str, version: int):
        key = (name, version)
        if key not in cache:
            cache[key] = resolve(name, version)
        return cache[key]

    return lookup


def check_graph(graph: Graph, *, resolve: Optional[_assets.Resolver] = None,
                code_switch: bool = True) -> GraphCheck:
    """One compile pass: program (or None), diagnostics and node streams.

    *resolve* looks up library assets for locked instances; by default the
    one the library registered (kernel.assets.default_resolver).
    *code_switch* False skips the SL_CODE_NODES check (see compile()).
    """
    return _published(_check_graph(graph, resolve=resolve, code_switch=code_switch))


def _check_graph(graph: Graph, *, resolve: Optional[_assets.Resolver],
                 code_switch: bool) -> GraphCheck:
    found: list[tuple[Diagnostic, Optional[GraphValidationError]]] = []

    def _record(exc: GraphValidationError) -> None:
        found.append((from_error(exc), exc))

    def _warn(code: str, message: str, node_id: Optional[str]) -> None:
        # Always a warning, even for a code diagnostics.SEVERITY_BY_CODE
        # does not list yet (setting_shadowed, W5).
        found.append((make_diagnostic(code, message, node_id=node_id, severity="warning"), None))

    # 0. Library asset instances (W6): each locked instance gets its
    # children from the library, with composite ids.  From here on `graph`
    # is that expanded graph (the same object when there is no instance).
    # An asset that is missing or contains itself is an error on the
    # instance; the instance is then empty.
    lookup = _remembering(resolve or _assets.default_resolver())
    graph, asset_problems = _assets.expand_assets(graph, lookup)
    for problem in asset_problems:
        if problem.severity == "warning":  # a switched-off instance (KA-7)
            _warn(problem.code, str(problem), problem.node_id)
        else:
            _record(problem)

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

    # 3. The kernel walk, over the flat graph (networks taken out).  Node
    # ids in the flat graph are the ids the user sees (W5; W6 adds
    # composite ids for library assets, so read node types from
    # analysis.graph, never from the source graph).  W7: each node's code
    # adds its writes and lookback to the schema (nodes_code.extra_io), and
    # a node with an expression on a param read before any cook (a Ticker's
    # symbol, a time stop's max_bars, a code node's lookback_bars...) is
    # reported here (param_not_codeable) and left out of the kernel walk.
    # A node whose code does not prepare (a syntax error...) is reported
    # by the plan below; the kernel skips the nodes under it, which would
    # only report what is missing because that code is broken.
    ticker_found, ticker_ids = _code.not_codeable_problems(graph)
    preset = list(ticker_ids) + [n for n in _code.broken_code_nodes(graph) if n not in ticker_ids]
    analysis, flat = analyze_graph(graph, unknown_error=_unknown_error,
                                   extra_io=_code.extra_io, preset_broken=preset)

    # 3b. Code (W7): every snippet prepared (never run), the ch()
    # references checked (kernel/params.py), and code blocks where none can
    # run.  With SL_CODE_NODES=0 every node holding code is code_disabled,
    # first in the list so compile() raises that before anything else.
    plan = plan_params(flat, analysis)
    # 3c. Windows of param-only expressions (W7): evaluated now, with the
    # cook's code, so the node's lookback counts the exact value, not the
    # param's max.  Not for code_switch False: that program never cooks.
    window_errors = static_windows(plan) if code_switch else []
    if ticker_found:
        found.extend(flat.remap(ticker_found))
    found.extend((d, _as_graph_error(exc)) for d, exc in plan.code_diagnostics())
    found.extend(analysis.found)
    nodes = analysis.nodes
    _check_interfaces(flat, analysis, lookup, _record)
    found.extend(plan.found)
    for exc in window_errors:
        err = _as_graph_error(exc)
        err.code_failure = True  # type: ignore[attr-defined]  (the cook fails the same way)
        err.code_error = exc     # type: ignore[attr-defined]  (the bot words it as the cook's)
        found.append((from_error(exc), err))
    misplaced = _code.misplaced_code(flat)
    if misplaced:
        found.extend(flat.remap(misplaced))
    silent = _code.silent_code(flat)
    if silent:
        found.extend(flat.remap(silent))
    if code_switch:
        from nodebuilder.code import code_enabled

        if not code_enabled():
            found[:0] = _code.disabled_found(graph, flat)

    # 4. Terminals, sorted into Output Groups (plan D7).  A graph with no
    # group is one implicit group, "main", with the rules and messages
    # compile has had since W1 (nodes_groups.layout_groups).
    layout = _groups.layout_groups(graph, flat, analysis, _record, _warn)
    entry_attr, entry_node = layout.entry_attr, layout.entry_node
    exit_attr, exit_node = layout.exit_attr, layout.exit_node

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
                node_id=flat.to_source(specs[_INDICATOR_FAMILY_CAP][1]),
            ))

    errors = [exc for _d, exc in found if exc is not None]
    diagnostics = [d for d, _exc in found]
    streams = analysis.schemas()
    extra = dict(param_deps=plan.param_deps(), analysis=analysis, flat=flat, plan=plan)
    if errors:
        return GraphCheck(None, diagnostics, streams, errors, **extra)

    assert entry_attr is not None
    program = CompiledProgram(
        steps=_code.wrap_code_steps(build_steps(analysis, plan), plan, analysis),
        entry_attr=entry_attr,
        exit_attr=exit_attr if exit_attr is not None else NO_EXIT_ATTR,
        entry_node=entry_node,
        exit_node=exit_node,
        required_lookback_bars=analysis.required_lookback_bars(),
        stream_schema=STREAM_SCHEMA_VERSION,
        schemas=streams,
    )

    # 6. Plan every group: the simulator's inputs (sim_bridge.plan_group).
    # A problem here is about a whole group (a second trailing stop, a
    # regime_switch group with no regime terminal), so the graph does not
    # compile.  A size or stop terminal over a settings node only warns.
    groups = _groups.build_group_programs(program, layout, _record, _warn)
    # 7. A ch() of an expression param across Ticker domains (W7): each
    # domain cooks on its own bars, so the value never reaches the reader.
    crossed = _code.cross_domain_refs(replace(program, groups=groups), plan)
    if crossed:
        found.extend(flat.remap(crossed))
    errors = [exc for _d, exc in found if exc is not None]
    diagnostics = [d for d, _exc in found]
    if errors:
        return GraphCheck(None, diagnostics, streams, errors, **extra)
    program = replace(program, groups=groups)
    return GraphCheck(program, diagnostics, streams, errors, **extra)

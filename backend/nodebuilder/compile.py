"""Graph -> (indicator_specs, per_bar_program, simulator_settings) compile step.

Unit 7a — pure functions, no I/O, no side effects.  compile() raises the
first problem; compile_with_diagnostics() lists every problem (W1 item 1.C).
"""
from __future__ import annotations

import contextvars
import logging
import math
from typing import Any

import pandas as pd

from nodebuilder.models import Graph, GraphValidationError, port_index, topological_sort
from nodebuilder.nodes import (
    NODE_CATALOG,
    RSI_TYPE_OPTIONS,
    NodeCatalogEntry,
    get_node,
    trailing_stop_impl,
)
from nodebuilder.diagnostics import Diagnostic, from_error
from nodebuilder.diagnostics import make as make_diagnostic
from nodebuilder.evaluator import (
    _CATALOG_TO_REGISTRY,
    _INDICATOR_FAMILY_CAP,
    CompiledProgram,
    FamilyCapExceededError,
    GraphTypeError,
    HTFGraphNotSupportedError,
    IndicatorSpec,
    MissingTerminalError,
    PerBarOp,
    RegimeUnsupportedError,
    SimulatorSetting,
    UnknownNodeTypeError,
    UnsupportedNodeError,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_CATALOG_INDEX: dict[str, NodeCatalogEntry] = {e.name: e for e in NODE_CATALOG}


def _indicator_spec_key(catalog_name: str, params: dict) -> tuple:
    """Stable dedup key for an indicator (catalog_name, params) pair.

    _check_indicator_params has made sure every value is a plain scalar, so
    the key is hashable.
    """
    return (catalog_name, frozenset(params.items()))


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _check_indicator_params(node_path: str, entry: NodeCatalogEntry, params: dict) -> None:
    """Check an indicator's params against its ParamSpecs before Run.

    int params must be whole numbers, number params finite numbers, both
    within min..max.  Any other param must be a plain value.  The Run-time
    impls refuse the same values, but without naming the node or param.
    Raises param_invalid or param_out_of_range with ``param`` set.
    """
    specs = {p.name: p for p in entry.params}
    for name, value in params.items():
        spec = specs.get(name)
        if spec is None or spec.type not in ("int", "number"):
            if not _is_scalar(value):
                raise _coded(GraphValidationError(
                    f"{entry.name} {node_path!r} param {name!r} must be a plain value, "
                    f"got {value!r}.",
                    node_id=node_path,
                ), "param_invalid", param=name)
            continue
        number: float | None = None
        if not isinstance(value, bool):
            try:
                if spec.type == "int" and isinstance(value, str):
                    number = float(int(value.strip()))  # Run reads it with int()
                elif isinstance(value, (str, int, float)):
                    number = float(value)
            except (TypeError, ValueError):
                number = None
        if number is None or not math.isfinite(number) or (
            spec.type == "int" and number != int(number)
        ):
            kind = "a whole number" if spec.type == "int" else "a number"
            raise _coded(GraphValidationError(
                f"{entry.name} {node_path!r} {name} needs {kind}, got {value!r}.",
                node_id=node_path,
            ), "param_invalid", param=name)
        lo, hi = spec.min, spec.max
        if (lo is not None and number < lo) or (hi is not None and number > hi):
            bounds = (
                f"between {lo:g} and {hi:g}" if lo is not None and hi is not None
                else f">= {lo:g}" if lo is not None else f"<= {hi:g}"
            )
            raise _coded(GraphValidationError(
                f"{entry.name} {node_path!r} {name} must be {bounds}, got {value!r}.",
                node_id=node_path,
            ), "param_out_of_range", param=name)


def _make_comparison_fn(condition: str, left_attr: str, right_attr: str | None, threshold: float | None):
    """Return a per-bar callable for a comparison node.

    For series vs series (right_attr is not None), both attrs are read from
    the attrs dict.  For series vs scalar (threshold is not None), the right
    side is the scalar value embedded in the closure.
    """
    def _fn(attrs: dict, i: int) -> bool:
        if i < 1:
            return False
        s = attrs.get(left_attr)
        if s is None:
            return False
        v_now = s.iloc[i]
        v_prev = s.iloc[i - 1]

        if right_attr is not None:
            r = attrs.get(right_attr)
            if r is None:
                return False
            r_now = r.iloc[i]
            r_prev = r.iloc[i - 1]

            if condition == "above":
                return bool(v_now > r_now)
            elif condition == "below":
                return bool(v_now < r_now)
            elif condition == "crosses_above":
                return bool(v_prev < r_prev and v_now >= r_now)
            elif condition == "crosses_below":
                return bool(v_prev > r_prev and v_now <= r_now)
        elif threshold is not None:
            if condition == "above":
                return bool(v_now > threshold)
            elif condition == "below":
                return bool(v_now < threshold)
            elif condition == "crosses_above":
                return bool(v_prev < threshold <= v_now)
            elif condition == "crosses_below":
                return bool(v_prev > threshold >= v_now)

        return False

    return _fn


def _make_and_fn(input_attrs: list[str]):
    def _fn(attrs: dict, i: int) -> bool:
        if i < 1:
            return False
        return all(bool(attrs[a].iloc[i]) for a in input_attrs if a in attrs)
    return _fn


def _make_or_fn(input_attrs: list[str]):
    def _fn(attrs: dict, i: int) -> bool:
        if i < 1:
            return False
        return any(bool(attrs[a].iloc[i]) for a in input_attrs if a in attrs)
    return _fn


def _make_not_fn(input_attr: str):
    def _fn(attrs: dict, i: int) -> bool:
        if i < 1:
            return False
        s = attrs.get(input_attr)
        if s is None:
            return False
        return not bool(s.iloc[i])
    return _fn


# ---------------------------------------------------------------------------
# Wire resolution helpers
# ---------------------------------------------------------------------------

# Multi-output indicator sub-attrs that callers may select via wire.attr
# instead of getting the primary @write. Wire labels matching these names
# are treated as explicit port selectors. Other wire.attr values (e.g.
# "@bool", "@rsi", "@close") are generic labels and we fall back to
# attr_written_by, since the per-bar op output keys are numbered
# (@bool_1, @bool_2, ...) and don't match those labels.
_MULTI_OUTPUT_SUBATTRS = frozenset({
    "@macd_line", "@macd_signal", "@macd_histogram",
    "@bb_upper", "@bb_middle", "@bb_lower",
})

# Which node type produces each multi-output sub-attr.  A wire that names a
# sub-attr its source node does not produce would read a missing (or another
# node's) series, so compile refuses it.
_SUBATTR_PRODUCER = {
    "@macd_line": "macd", "@macd_signal": "macd", "@macd_histogram": "macd",
    "@bb_upper": "bollinger", "@bb_middle": "bollinger", "@bb_lower": "bollinger",
}

# The raw price fields a Ticker writes.  A wire out of a Ticker that names
# one of these reads that field; any other label falls back to @close.
_TICKER_ATTRS = frozenset({"@open", "@high", "@low", "@close", "@volume"})


# Settings nodes set a simulator value.  They take no input and give no
# output: compile never reads a wire into or out of one.
_SETTINGS_TYPES = frozenset({
    "position_size", "stop_loss", "slippage", "commission", "trailing_stop",
})

# Node types with no output.  A wire out of one of these carries nothing, so
# compile refuses it rather than let the reader silently lose that input.
_NO_OUTPUT_TYPES = _SETTINGS_TYPES | {"entry", "exit", "size", "stop"}


def _coded(exc: GraphValidationError, code: str, *, param: str | None = None,
           port: str | None = None) -> GraphValidationError:
    """Tag an error with its diagnostic code (plan 4.2) and the param or
    input port it is about, so /validate can point at the exact field."""
    exc.code = code
    exc.param = param
    exc.port = port
    return exc


def _port_index(port: str | None) -> int | None:
    """``in3`` -> 3.  None for a missing or non-numbered port."""
    return port_index(port)


def _wires_into(graph: Graph, node_path: str) -> list:
    """The wires into *node_path*, ordered by input port (in0, in1, ...).

    The port, not the wire's place in the list, decides which input is the
    left one of a comparison.  Redrawing the left wire appends it to the end
    of the list, and that must not flip "RSI above SMA".  Wires without a
    numbered port come last, in list order.
    """
    indexed = _indexed_wires_into(graph, node_path)

    def _key(item: tuple) -> tuple:
        i, w = item
        k = _port_index(w.to_port)
        return (k is None, k if k is not None else 0, i)

    return [w for _i, w in sorted(indexed, key=_key)]


def _wire_count(graph: Graph, node_path: str) -> int:
    """How many wires go into *node_path* (bypassed sources included)."""
    return len(_indexed_wires_into(graph, node_path))


# While _compile runs, the wires into each node are looked up in an index
# built once for that graph, instead of scanning every wire for every node
# (quadratic on a big graph).  A ContextVar keeps it per thread, and it is
# only used for the very graph object it was built from.
_INBOUND_INDEX: contextvars.ContextVar = contextvars.ContextVar(
    "nodebuilder_inbound_index", default=None
)


def _indexed_wires_into(graph: Graph, node_path: str) -> list[tuple[int, Any]]:
    """(list index, wire) for each wire into *node_path*, in list order."""
    current = _INBOUND_INDEX.get()
    if current is not None and current[0] is graph:
        return current[1].get(node_path, [])
    return [(i, w) for i, w in enumerate(graph.wires) if w.to_path == node_path]


def _build_inbound_index(graph: Graph) -> dict[str, list[tuple[int, Any]]]:
    index: dict[str, list[tuple[int, Any]]] = {}
    for i, w in enumerate(graph.wires):
        index.setdefault(w.to_path, []).append((i, w))
    return index


def _inbound_attrs(
    graph: Graph,
    node_path: str,
    attr_written_by: dict[str, str],
    attr_suffix_by_node: dict[str, str] | None = None,
) -> list[str]:
    """Return the @-attr names flowing INTO *node_path*, in input-port order."""
    return [attr for _src, attr in _inbound_pairs(
        graph, node_path, attr_written_by, attr_suffix_by_node
    )]


def _inbound_pairs(
    graph: Graph,
    node_path: str,
    attr_written_by: dict[str, str],
    attr_suffix_by_node: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Return (source node, @-attr) for each wire flowing INTO *node_path*.

    Inputs come in port order (in0 first), see _wires_into.

    Wires whose ``wire.attr`` selects a specific sub-output of a multi-output
    indicator (e.g. ``@macd_signal``, ``@bb_upper``) honor that selection so
    downstream nodes can compare against the non-primary output. Wires with
    generic labels (``@close``, ``@rsi``, ``@bool``) fall back to the
    upstream node's recorded write attribute — necessary because per-bar
    op outputs are stored under numbered keys (``@bool_1``, ``@bool_2``)
    that don't match the wire label.

    This resolution mirrors the ComparisonNode dispatcher (historically the
    only consumer of wire.attr); applying it uniformly here prevents a user
    from wiring ``MACD.@macd_signal`` into NOT/AND/OR/Entry and silently
    receiving ``@macd_line``.

    attr_suffix_by_node maps an indicator node to its spec's attr suffix, so
    a sub-output of a second MACD resolves to ``@macd_signal_2``.

    A wire out of a node with no output (Entry, Exit, a Settings node)
    raises GraphTypeError.  A bypassed source gives nothing, so its wire is
    left out (bypass is a soft disable that flows downstream).  The Ticker is
    the exception: indicators read its bars directly, so bypassing it has no
    meaning and its wires always resolve.
    """
    return [
        (wire.from_path, attr)
        for wire, attr in _resolve_inbound(graph, node_path, attr_written_by, attr_suffix_by_node)
        if attr is not None
    ]


def _resolve_inbound(
    graph: Graph,
    node_path: str,
    attr_written_by: dict[str, str],
    attr_suffix_by_node: dict[str, str] | None = None,
) -> list[tuple[Any, str | None]]:
    """(wire, @-attr or None) for each wire into *node_path*, in port order.

    The attr is None when the wire gives nothing (a bypassed source, or a
    source with no recorded output).  See _inbound_pairs for the rules.
    """
    suffixes = attr_suffix_by_node or {}
    result: list[tuple[Any, str | None]] = []
    for wire in _wires_into(graph, node_path):
        src = wire.from_path
        src_node = graph.nodes.get(src)
        if src_node is None:
            continue
        if src_node.type in _NO_OUTPUT_TYPES:
            raise _coded(GraphTypeError(
                f"Wire {wire.id!r} comes out of {src!r} (type={src_node.type!r}), "
                f"which has no output.  Delete the wire.",
                node_id=src,
            ), "port_unknown")
        if src_node.bypass and src_node.type != "ticker":
            result.append((wire, None))
            continue
        if wire.attr in _MULTI_OUTPUT_SUBATTRS:
            if src_node.type != _SUBATTR_PRODUCER[wire.attr]:
                raise _coded(GraphTypeError(
                    f"Wire {wire.id!r} reads {wire.attr!r} from {src!r}, but a "
                    f"{src_node.type!r} node does not produce {wire.attr!r}.",
                    node_id=node_path,
                ), "attr_type", port=wire.to_port)
            result.append((wire, wire.attr + suffixes.get(src, "")))
        elif src_node.type == "ticker" and wire.attr in _TICKER_ATTRS:
            # A Ticker writes several fields; honour the one the wire
            # names (e.g. @volume) instead of always reading @close.
            result.append((wire, wire.attr))
        else:
            result.append((wire, attr_written_by.get(src) or None))
    return result


def _primary_inbound_attr(
    graph: Graph,
    node_path: str,
    attr_written_by: dict[str, str],
    attr_suffix_by_node: dict[str, str] | None = None,
) -> str | None:
    """Return the single @-attr written by the first upstream node (for single-input nodes)."""
    attrs = _inbound_attrs(graph, node_path, attr_written_by, attr_suffix_by_node)
    return attrs[0] if attrs else None


def _require_bool_input(graph: Graph, label: str, terminal: str, attr: str) -> None:
    """Raise GraphTypeError unless *attr* is a boolean signal (@bool_N)."""
    if attr.startswith("@bool_"):
        return
    first = next(iter(_wires_into(graph, terminal)), None)
    src = first.from_path if first is not None else None
    src_node = graph.nodes.get(src) if src else None
    src_type = src_node.type if src_node else None
    raise _coded(GraphTypeError(
        f"{label} terminal expects a boolean input, but the wired node "
        f"{src!r} (type={src_type!r}) gives {attr!r}, not '@bool'.",
        node_id=src or terminal,
    ), "attr_type")


def _number(node_path: str, label: str, value: Any, *, minimum: float, allow_min: bool,
            param: str | None = None) -> float:
    """Read a Settings param as a finite number no lower than *minimum*.

    Raises GraphValidationError naming the node for a blank, non-numeric,
    infinite or too-small value, so the editor can point at the bad node
    instead of the bot failing on every tick.
    """
    try:
        if value is None or isinstance(value, bool) or (isinstance(value, str) and not value.strip()):
            raise ValueError
        number = float(value)
    except (TypeError, ValueError):
        raise _coded(GraphValidationError(
            f"{label} on {node_path!r} needs a number, got {value!r}.",
            node_id=node_path,
        ), "param_invalid", param=param) from None
    too_small = number < minimum if allow_min else number <= minimum
    if not math.isfinite(number) or too_small:
        bound = f">= {minimum:g}" if allow_min else f"> {minimum:g}"
        raise _coded(GraphValidationError(
            f"{label} on {node_path!r} must be {bound}, got {value!r}.",
            node_id=node_path,
        ), "param_out_of_range" if math.isfinite(number) else "param_invalid", param=param)
    return number


def _optional_threshold(node_path: str, value: Any) -> float | None:
    """A comparison's threshold as a float, or None when it is blank."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = math.nan
    if not math.isfinite(number):
        raise _coded(GraphValidationError(
            f"Threshold on {node_path!r} needs a number, got {value!r}.",
            node_id=node_path,
        ), "param_invalid", param="threshold")
    return number


def _is_rule_type(node_type: str) -> bool:
    """True for a rule indicator or condition name (stochastic, rising...).

    auto_render turns rules into nodes of these types.  They are known
    ideas the graph backtest cannot run yet (unsupported_node), not typos
    (unknown_node_type).  Imported here so compile only loads the rule
    engine when it meets such a node.
    """
    from typing import get_args

    from signal_engine import RuleCondition, RuleIndicator

    return node_type in get_args(RuleIndicator) or node_type in get_args(RuleCondition)


# Node types that read their inputs.  When one of their inputs comes from a
# node that already has an error, they are skipped without a diagnostic of
# their own, so one mistake does not show up as a chain of errors below it.
# Ticker and Settings nodes are always checked: any wire into them is wrong.
_READS_INPUTS = frozenset({
    "rsi", "macd", "sma", "ema", "bollinger", "atr",
    "above", "below", "crosses_above", "crosses_below",
    "and", "or", "not", "entry", "exit", "size", "stop",
})


# ---------------------------------------------------------------------------
# Public compile() entry point
# ---------------------------------------------------------------------------

def compile(graph: Graph) -> CompiledProgram:  # noqa: A001 (shadows builtin "compile" intentionally)
    """Compile a Graph into a CompiledProgram.

    Raises the first error compile finds, in this order: a /regime/ node
    first, then a second wire on one input port, then nodes in topological
    order, then the Entry and Exit checks, then the indicator family cap.
    Every raised error carries ``.code`` (plan 4.2).  Use
    compile_with_diagnostics to get every problem at once.

    Since W1, compile also refuses some graphs it used to accept and that
    only failed at Run: a graph over the indicator family cap
    (FamilyCapExceededError, which Run raised before), and an indicator param
    outside its catalog limits (param_invalid / param_out_of_range).  So
    add_bot and the bot runner refuse those graphs up front too.
    """
    program, found = _compile(graph)
    for _diag, exc in found:
        if exc is not None:
            raise exc
    assert program is not None
    return program


def compile_with_diagnostics(graph: Graph):
    """Compile and collect every problem instead of stopping at the first.

    Returns ``(program or None, diagnostics)``.  The program is None when any
    diagnostic is an error.  Warnings (exit_unconnected, size_unit_suspect)
    do not stop the compile.  Pure: no data is fetched.
    """
    program, found = _compile(graph)
    return program, [diag for diag, _exc in found]


def _compile(graph: Graph):
    """_compile_walk with the inbound-wire index set for this graph."""
    token = _INBOUND_INDEX.set((graph, _build_inbound_index(graph)))
    try:
        return _compile_walk(graph)
    finally:
        _INBOUND_INDEX.reset(token)


def _compile_walk(graph: Graph):
    """The compile walk.  Returns (program or None, [(Diagnostic, error or None)]).

    Steps:
    1. Detect /regime/ nodes → RegimeUnsupportedError
    2. Topological sort (the caller makes sure there is no cycle)
    3. Walk nodes in topo order, emitting IndicatorSpecs, PerBarOps, SimulatorSettings.
       A node compile cannot run raises UnsupportedNodeError (node types that
       are not compile-active, per-direction settings, a wired size/stop
       terminal, a comparison carrying condition_extra).
    4. Require Entry terminal → MissingTerminalError if absent
    5. Verify Entry's input attr is @bool → TypeError if not
    6. Indicator family cap → FamilyCapExceededError

    An error on one node is recorded and the walk goes on.  Nodes that read
    from a node with an error are skipped (see _READS_INPUTS).  An error has
    its exception kept next to its diagnostic, so compile() can raise the
    first one exactly as before.

    Bypassed nodes: node.bypass=True causes the PerBarOp to be skipped, and
    wires out of a bypassed node are left out of the reader's inputs.  A
    comparison or logic node whose inputs are all bypassed is off as well, and
    an Exit fed only by bypassed nodes never fires.  A bypassed Settings node
    does not apply.  This is a documented trade-off: bypass is a "soft
    disable" with no explicit pass-through value.
    """
    found: list[tuple[Diagnostic, GraphValidationError | None]] = []
    broken: set[str] = set()

    def _record(exc: GraphValidationError) -> None:
        found.append((from_error(exc), exc))

    def _warn(code: str, message: str, node_id: str | None, param: str | None = None) -> None:
        found.append((make_diagnostic(code, message, node_id=node_id, param=param), None))

    # 1. Regime check
    for node_path in graph.nodes:
        if node_path.startswith("/regime/"):
            _record(RegimeUnsupportedError(
                f"Graph contains a /regime/ node ({node_path!r}). "
                "Regime is not supported in the graph evaluator at T2.",
                node_id=node_path,
            ))
            broken.add(node_path)

    # 1b. One wire per input port.  The Graph model refuses a second wire on
    # a port, so this only fires for a graph built without validation
    # (/validate's fallback).  It keeps compile, storage, Run and bot deploy
    # agreeing with /validate on which graphs are valid.
    seen_ports: set[tuple[str, str]] = set()
    for wire in graph.wires:
        if not wire.to_port:
            continue
        key = (wire.to_path, wire.to_port)
        if key in seen_ports:
            _record(_coded(GraphValidationError(
                f"Input {wire.to_port} of {wire.to_path!r} has more than one wire.",
                node_id=wire.to_path,
            ), "port_duplicate", port=wire.to_port))
        seen_ports.add(key)

    # 2. Topo sort (the Graph model or /validate made sure there is no cycle)
    ordered_nodes = topological_sort(graph)

    indicator_specs: list[IndicatorSpec] = []
    per_bar_program: list[PerBarOp] = []
    simulator_settings: list[SimulatorSetting] = []

    # Track which unique indicator (catalog_name, params) specs we've emitted.
    # Maps spec_key → write_attr so we can reuse the same attr for dedup'ed specs.
    indicator_key_to_attr: dict[tuple, str] = {}
    # Suffix per distinct spec ("" for the first of a node type, then "_2"...),
    # and per indicator node, so specs never share an output attribute.
    indicator_key_to_suffix: dict[tuple, str] = {}
    specs_per_type: dict[str, int] = {}
    attr_suffix_by_node: dict[str, str] = {}

    # Maps node_path → the @-attr name that node writes (for wire resolution)
    attr_written_by: dict[str, str] = {}

    entry_attr: str | None = None
    exit_attr: str | None = None
    # The first Entry node seen, so an unwired Entry can be named in the error.
    entry_node_id: str | None = None
    entry_wired = False
    exit_node_id: str | None = None
    # True when a terminal's input has an error of its own; the terminal
    # checks then stay quiet (that error already says what is wrong).
    entry_blocked = False
    exit_blocked = False
    # The terminal whose wire supplied entry_attr / exit_attr.
    entry_terminal: str = ""
    exit_terminal: str = ""

    # Assign unique @-attr names for derived (bool) nodes
    _op_counter: dict[str, int] = {}

    def _next_attr(base: str) -> str:
        _op_counter[base] = _op_counter.get(base, 0) + 1
        return f"@{base}_{_op_counter[base]}"

    def _compile_node(node) -> None:
        """Compile one node.  Raises GraphValidationError on a problem."""
        nonlocal entry_attr, exit_attr, entry_node_id, entry_wired, exit_node_id
        nonlocal entry_terminal, exit_terminal

        node_type = node.type
        node_path = node.id

        # Ticker: source node — provides raw OHLCV attrs; no spec or op needed.
        # The OHLCV attrs (@open, @high, @low, @close, @volume) are provided
        # externally in the attrs dict before evaluate_graph is called.
        if node_type == "ticker":
            # A Ticker is a source: a wire into it carries nothing.
            inbound_wire = next((w for w in graph.wires if w.to_path == node_path), None)
            if inbound_wire is not None:
                raise _coded(GraphTypeError(
                    f"Ticker {node_path!r} takes no input, but {inbound_wire.from_path!r} "
                    f"is wired into it.  Delete the wire.",
                    node_id=node_path,
                ), "port_unknown", port=inbound_wire.to_port)
            # Record what attrs this node writes so downstream wires resolve
            # correctly.  We mark the primary "close" output as the wire attr.
            # In practice the caller seeds these, but we record @close so that
            # a wire from ticker → rsi resolves to "@close".
            attr_written_by[node_path] = "@close"
            return

        # --- Indicator nodes ---
        if node_type in ("rsi", "macd", "sma", "ema", "bollinger", "atr"):
            catalog_entry = _CATALOG_INDEX.get(node_type)
            if catalog_entry is None:
                return

            # Indicators are computed from the Ticker's bars.  A wire from any
            # other node (say RSI into SMA) would be ignored, so the SMA would
            # quietly run on the close.  Refuse it.
            for w in _wires_into(graph, node_path):
                src_node = graph.nodes.get(w.from_path)
                if src_node is not None and src_node.type != "ticker":
                    raise _coded(GraphTypeError(
                        f"{node_type} node {node_path!r} reads the Ticker's bars, but "
                        f"{w.from_path!r} (type={src_node.type!r}) is wired into it.  "
                        f"Indicators of indicators are not supported yet.",
                        node_id=node_path,
                    ), "attr_type", port=w.to_port)

            params = dict(node.params) if node.params else {}
            # Fill in defaults for any missing params
            default_params = catalog_entry.defaults.get("params", {})
            for k, v in default_params.items():
                params.setdefault(k, v)

            # The RSI indicator runs any unknown type as a plain rolling mean,
            # so a typo would quietly change the strategy.  Refuse it.
            if node_type == "rsi" and params.get("type") not in RSI_TYPE_OPTIONS:
                raise _coded(GraphValidationError(
                    f"RSI {node_path!r} has type {params.get('type')!r}; "
                    f"use one of {list(RSI_TYPE_OPTIONS)}.",
                    node_id=node_path,
                ), "param_invalid", param="type")

            # Period and friends: a bad value would pass here and fail at
            # Run with no node named.  Check them now so the editor can show
            # the problem on the param before Run.
            _check_indicator_params(node_path, catalog_entry, params)

            spec_key = _indicator_spec_key(node_type, params)
            if spec_key not in indicator_key_to_attr:
                specs_per_type[node_type] = specs_per_type.get(node_type, 0) + 1
                n = specs_per_type[node_type]
                suffix = "" if n == 1 else f"_{n}"
                # Determine write_attr (the primary output attr name)
                base_write = catalog_entry.writes[0] if catalog_entry.writes else f"@{node_type}"
                primary_write = base_write + suffix
                indicator_key_to_attr[spec_key] = primary_write
                indicator_key_to_suffix[spec_key] = suffix
                indicator_specs.append(
                    IndicatorSpec(
                        catalog_name=node_type,
                        params=params,
                        write_attr=primary_write,
                        node_path=node_path,
                        attr_suffix=suffix,
                    )
                )
            attr_suffix_by_node[node_path] = indicator_key_to_suffix[spec_key]

            # This node "writes" the primary output attr (or macd-specific one)
            attr_written_by[node_path] = indicator_key_to_attr[spec_key]

            # Bypassed indicator: downstream sees no value (attr absent from attrs)
            # Compile still records the spec so compute_indicators_from_specs will
            # compute it — but we skip registering the node's write_attr.
            if node.bypass:
                attr_written_by.pop(node_path, None)

            return

        # --- Comparison nodes ---
        if node_type in ("above", "below", "crosses_above", "crosses_below"):
            if node.bypass:
                return

            # Collect the two inbound attrs through the shared wire resolver:
            # multi-output sub-attrs (e.g. @macd_signal, @bb_upper) and Ticker
            # fields (e.g. @volume) are honored as port selectors; other labels
            # fall back to attr_written_by so per-bar op outputs (numbered
            # @bool_N keys) resolve correctly.  in0 is the left side, in1 the
            # right side, whatever order the wires were drawn in.
            resolved = _resolve_inbound(graph, node_path, attr_written_by, attr_suffix_by_node)
            inbound = [attr for _w, attr in resolved if attr is not None]

            params = dict(node.params) if node.params else {}

            # auto_render stores rule details it cannot express as nodes in
            # extra params (e.g. condition_extra="atr_pct" for an ATR% rule).
            # Compile does not implement them, so running would silently
            # compare the wrong series.  Refuse instead.
            extra = params.get("condition_extra")
            if extra is not None:
                raise _coded(
                    UnsupportedNodeError(node_path, f"{node_type} ({extra})"),
                    "unsupported_node", param="condition_extra",
                )

            # A comparison reads two series, or one series and a threshold.
            # Too few inputs used to drop the node quietly (and the AND above
            # it then ignored that branch); too many used only the first two.
            # Both change the strategy without a word, so refuse them.
            threshold = _optional_threshold(node_path, params.get("threshold"))
            n_wires = _wire_count(graph, node_path)
            if n_wires > 2:
                raise _coded(GraphValidationError(
                    f"{node_type} node {node_path!r} has {n_wires} inputs; it takes "
                    f"two, or one and a threshold.",
                    node_id=node_path,
                ), "port_unknown", port=_wires_into(graph, node_path)[2].to_port)
            # The port decides the side: in0 is a (left), in1 is b (right).
            # A wire on any other port has no side to go to.
            ports = set()
            for w in _wires_into(graph, node_path):
                k = _port_index(w.to_port)
                if k not in (0, 1):
                    raise _coded(GraphValidationError(
                        f"{node_type} node {node_path!r} has no input {w.to_port!r}; "
                        f"it takes in0 (a) and in1 (b).",
                        node_id=node_path,
                    ), "port_unknown", port=w.to_port)
                ports.add(k)
            if n_wires == 0 or (0 in ports and 1 not in ports and threshold is None):
                raise _coded(GraphValidationError(
                    f"{node_type} node {node_path!r} needs two inputs, or one input "
                    f"and a threshold.",
                    node_id=node_path,
                ), "missing_input", port="in0" if n_wires == 0 else "in1")
            if 0 not in ports:
                # Only b is wired.  Reading it as a would compare the wrong
                # side, so ask for a.
                raise _coded(GraphValidationError(
                    f"{node_type} node {node_path!r} has nothing on in0 (a), the "
                    f"left side.  Wire a, or move the wire from in1 to in0.",
                    node_id=node_path,
                ), "missing_input", port="in0")

            # T2 constraint: crossover comparisons need history (iloc[i-1]).
            # Indicator/raw attrs (@close, @rsi, @macd_line ...) are full-length
            # Series; per-bar derived attrs (@bool_N, written by other comparison
            # /logic ops) only get iloc[i] populated at run time, so iloc[i-1] is
            # NaN every tick → crossover silently never fires. Reject at compile.
            if node_type in ("crosses_above", "crosses_below"):
                for a in inbound:
                    if a.startswith("@bool_"):
                        raise _coded(GraphTypeError(
                            f"Crossover node {node_path!r} reads from a derived "
                            f"signal ({a!r}). Crossovers require indicator or raw "
                            f"OHLCV inputs at T2; comparing per-bar derived booleans "
                            f"is unsupported (no history). Use AND/OR over plain "
                            f"comparisons, or land Signal Processing nodes in T3.",
                            node_id=node_path,
                        ), "attr_type")

            a_attr = next((a for w, a in resolved if _port_index(w.to_port) == 0), None)
            b_attr = next((a for w, a in resolved if _port_index(w.to_port) == 1), None)
            if a_attr is not None and b_attr is not None:
                fn = _make_comparison_fn(node_type, a_attr, b_attr, None)
                reads = (a_attr, b_attr)
            elif a_attr is not None and threshold is not None:
                # b is unwired or bypassed: compare a to the threshold.
                fn = _make_comparison_fn(node_type, a_attr, None, threshold)
                reads = (a_attr,)
            else:
                # The wires are there, but a bypassed input gives nothing, so
                # this comparison is off too (bypass flows downstream).  A
                # bypassed a is never replaced by b.
                return

            write_attr = _next_attr("bool")
            attr_written_by[node_path] = write_attr
            per_bar_program.append(
                PerBarOp(
                    node_path=node_path,
                    reads=tuple(reads),
                    writes=write_attr,
                    fn=fn,
                )
            )
            return

        # --- Logic nodes ---
        if node_type in ("and", "or", "not"):
            if node.bypass:
                return

            n_wires = _wire_count(graph, node_path)
            if n_wires == 0:
                raise _coded(GraphValidationError(
                    f"{node_type.upper()} node {node_path!r} has no inputs.",
                    node_id=node_path,
                ), "missing_input", port="in0")
            if node_type == "not" and n_wires > 1:
                raise _coded(GraphValidationError(
                    f"NOT node {node_path!r} has {n_wires} inputs; it takes one.",
                    node_id=node_path,
                ), "port_unknown", port=_wires_into(graph, node_path)[1].to_port)

            pairs = _inbound_pairs(graph, node_path, attr_written_by, attr_suffix_by_node)
            # AND/OR/NOT combine signals.  An indicator or price fed in here
            # would count as "true" whenever it is non-zero (NaN included), so
            # refuse anything that is not a comparison or logic output.
            for src, attr in pairs:
                if not attr.startswith("@bool_"):
                    src_node = graph.nodes.get(src)
                    raise _coded(GraphTypeError(
                        f"{node_type.upper()} node {node_path!r} expects boolean "
                        f"inputs, but {src!r} (type={src_node.type if src_node else None!r}) "
                        f"gives {attr!r}, not '@bool'.",
                        node_id=src,
                    ), "attr_type")
            inbound = [attr for _src, attr in pairs]

            if not inbound:
                # Every input is bypassed, so this node is off too.
                return

            if node_type == "not":
                fn = _make_not_fn(inbound[0])
                reads = (inbound[0],)
            elif node_type == "and":
                fn = _make_and_fn(inbound)
                reads = tuple(inbound)
            else:  # or
                fn = _make_or_fn(inbound)
                reads = tuple(inbound)

            write_attr = _next_attr("bool")
            attr_written_by[node_path] = write_attr
            per_bar_program.append(
                PerBarOp(
                    node_path=node_path,
                    reads=reads,
                    writes=write_attr,
                    fn=fn,
                )
            )
            return

        # --- Settings nodes ---
        if node_type in _SETTINGS_TYPES:
            params = dict(node.params) if node.params else {}

            # A wire into a Settings node would look like a conditional stop
            # or size, but the setting always applies.  Refuse it.
            inbound_wire = next((w for w in graph.wires if w.to_path == node_path), None)
            if inbound_wire is not None:
                raise _coded(GraphTypeError(
                    f"{node_type} node {node_path!r} takes no input, but "
                    f"{inbound_wire.from_path!r} is wired into it.  A setting always "
                    f"applies; delete the wire.",
                    node_id=node_path,
                ), "port_unknown", port=inbound_wire.to_port)

            # A bypassed Settings node is off: the bot config's value stays.
            if node.bypass:
                return

            # A per-direction stop or trailing stop (from a b23 long/short
            # strategy) cannot be expressed yet: the simulator settings have
            # one value for both sides.  Applying it to both would trade
            # differently from the strategy, so refuse it.
            if "direction" in params:
                raise _coded(
                    UnsupportedNodeError(node_path, f"{node_type} (direction={params['direction']})"),
                    "unsupported_node", param="direction",
                )

            if node_type == "trailing_stop":
                try:
                    result = trailing_stop_impl(params)
                except ValueError as exc:
                    raise _coded(GraphValidationError(
                        f"Trailing stop {node_path!r} has invalid params: {exc}",
                        node_id=node_path,
                    ), "param_invalid") from exc
                ts_cfg = result.value
                _number(node_path, "Trailing stop value", ts_cfg.value, minimum=0.0,
                        allow_min=False, param="value")
                _number(node_path, "Trailing stop activate_pct", ts_cfg.activate_pct, minimum=0.0,
                        allow_min=True, param="activate_pct")
                simulator_settings.append(SimulatorSetting(key="trailing_stop", value=ts_cfg))
            # Values are checked here, with the node named, so a bad one is a
            # 400 on Run or on deploy rather than a failure on every bot tick.
            # A stop of 0 means "no stop" on both backtest and live.
            elif node_type == "position_size":
                size = _number(node_path, "Size", params.get("size", 1.0), minimum=0.0,
                               allow_min=False, param="size")
                if size > 1.0:
                    # Size is a fraction of capital (1 = 100%).  A value like
                    # 50 was most likely meant as 50%.
                    _warn(
                        "size_unit_suspect",
                        f"Size on {node_path!r} is {size:g}, but size is a fraction "
                        f"of capital (1 = 100%, 0.5 = 50%).",
                        node_path, param="size",
                    )
                simulator_settings.append(SimulatorSetting(key="position_size", value=size))
            elif node_type == "stop_loss":
                pct = _number(node_path, "Stop loss pct", params.get("pct", 5.0), minimum=0.0,
                              allow_min=True, param="pct")
                simulator_settings.append(SimulatorSetting(key="stop_loss", value=pct))
            elif node_type == "slippage":
                bps = _number(node_path, "Slippage bps", params.get("bps", 2.0), minimum=0.0,
                              allow_min=True, param="bps")
                simulator_settings.append(SimulatorSetting(key="slippage_bps", value=bps))
            elif node_type == "commission":
                rate = _number(node_path, "Per-share rate", params.get("per_share_rate", 0.0),
                               minimum=0.0, allow_min=True, param="per_share_rate")
                min_order = _number(node_path, "Min per order", params.get("min_per_order", 0.0),
                                    minimum=0.0, allow_min=True, param="min_per_order")
                simulator_settings.append(SimulatorSetting(key="per_share_rate", value=rate))
                simulator_settings.append(SimulatorSetting(key="min_per_order", value=min_order))
            return

        # --- Output terminals ---
        if node_type in ("entry", "exit"):
            label = node_type.capitalize()
            # One Entry and one Exit, each with one input.  A second terminal
            # or a second wire used to be dropped without a word.
            if node_type == "entry" and entry_node_id is not None:
                raise _coded(GraphValidationError(
                    f"The graph has more than one Entry ({entry_node_id!r} and "
                    f"{node_path!r}).  Join the signals with OR into one Entry.",
                    node_id=node_path,
                ), "duplicate_terminal")
            if node_type == "exit" and exit_node_id is not None:
                raise _coded(GraphValidationError(
                    f"The graph has more than one Exit ({exit_node_id!r} and "
                    f"{node_path!r}).  Join the signals with OR into one Exit.",
                    node_id=node_path,
                ), "duplicate_terminal")
            if node_type == "entry":
                entry_node_id = node_path
            else:
                exit_node_id = node_path
            n_wires = _wire_count(graph, node_path)
            if n_wires > 1:
                raise _coded(GraphValidationError(
                    f"{label} {node_path!r} has {n_wires} inputs; it takes one.  "
                    f"Join the signals with AND or OR first.",
                    node_id=node_path,
                ), "port_unknown", port=_wires_into(graph, node_path)[1].to_port)
            src_attr = _primary_inbound_attr(graph, node_path, attr_written_by, attr_suffix_by_node)
            if node_type == "entry":
                entry_wired = n_wires > 0
                if src_attr is not None:
                    entry_attr = src_attr
                    entry_terminal = node_path
            else:
                if src_attr is not None:
                    exit_attr = src_attr
                    exit_terminal = node_path
            return

        # size / stop terminals are not run yet (compile_active=False).  An
        # unwired one carries nothing, so it cannot change the result and is
        # ignored.  A wired one would look like it sizes or stops trades while
        # doing nothing, so refuse it.
        if node_type in ("size", "stop"):
            if any(w.to_path == node_path for w in graph.wires):
                raise _coded(UnsupportedNodeError(node_path, f"{node_type} (wired)"), "unsupported_node")
            return

        # Any other node type is one compile cannot run (slope conditions,
        # stochastic, adx, a type missing from the catalog...).  Skipping it
        # would silently drop part of the strategy, for example an Exit that
        # never fires.  Refuse instead, even when the node is bypassed.  The
        # read-only viewer still shows these nodes.
        if node_type not in _CATALOG_INDEX and not _is_rule_type(node_type):
            raise UnknownNodeTypeError(node_path, node_type)
        raise UnsupportedNodeError(node_path, node_type)

    for node in ordered_nodes:
        if node.id in broken:
            continue
        if node.type in _READS_INPUTS and any(
            w.from_path in broken for _i, w in _indexed_wires_into(graph, node.id)
        ):
            # An input already has an error.  Remember the terminal so the
            # checks below do not add a second, misleading error.
            broken.add(node.id)
            if node.type == "entry":
                entry_node_id = entry_node_id or node.id
                entry_blocked = True
            elif node.type == "exit":
                exit_node_id = exit_node_id or node.id
                exit_blocked = True
            continue
        try:
            try:
                _compile_node(node)
            except GraphValidationError:
                raise  # GraphTypeError is a TypeError too; keep its own code
            except (TypeError, ValueError) as raw:
                # A value compile could not read (a list where a number goes,
                # say).  Name the node instead of failing the whole request
                # with a 500.
                logger.warning("compile: node %r raised %r", node.id, raw, exc_info=True)
                raise _coded(GraphValidationError(
                    f"{node.type} node {node.id!r} has a param compile cannot read: {raw}",
                    node_id=node.id,
                ), "param_invalid") from raw
        except GraphValidationError as exc:
            _record(exc)
            broken.add(node.id)
            if node.type == "entry":
                entry_blocked = True
            elif node.type == "exit":
                exit_blocked = True

    # 3. Require Entry terminal
    if entry_attr is None and not entry_blocked:
        if entry_node_id is not None and entry_wired:
            _record(_coded(MissingTerminalError(
                f"Entry terminal {entry_node_id!r} gets no signal: its input is bypassed.",
                node_id=entry_node_id,
            ), "missing_input", port="in0"))
        elif entry_node_id is not None:
            _record(_coded(MissingTerminalError(
                f"Entry terminal {entry_node_id!r} is not wired to a signal.",
                node_id=entry_node_id,
            ), "missing_input", port="in0"))
        else:
            _record(MissingTerminalError("Graph has no Entry terminal (no 'entry' node found)."))

    # 4. Verify the terminals get a boolean.  Every comparison and logic op
    # writes an @bool_N attr and nothing else does, so any other attr (an
    # indicator, a Ticker field, a MACD sub-output) is not a signal.  Without
    # this, Exit fed by RSI would read "RSI is non-zero" as always true.
    if entry_attr is not None:
        try:
            _require_bool_input(graph, "Entry", entry_terminal, entry_attr)
        except GraphValidationError as exc:
            _record(exc)
    if exit_attr is not None:
        try:
            _require_bool_input(graph, "Exit", exit_terminal, exit_attr)
        except GraphValidationError as exc:
            _record(exc)
    elif not exit_blocked:
        # Not an error: stops, trailing stops or the end of the data close
        # the trade.  But a strategy with no exit signal is often a mistake.
        if exit_node_id is None:
            _warn("exit_unconnected", "The graph has no Exit, so only a stop or "
                  "the end of the data closes a trade.", None)
        else:
            _warn("exit_unconnected", f"Exit {exit_node_id!r} gets no signal, so only "
                  "a stop or the end of the data closes a trade.", exit_node_id, None)

    # 6. Indicator family cap, checked here so /validate reports it too.  Run
    # would raise the same error from compute_indicators_from_specs.
    family_counts: dict[str, int] = {}
    for spec in indicator_specs:
        family = _CATALOG_TO_REGISTRY.get(spec.catalog_name, spec.catalog_name)
        family_counts[family] = family_counts.get(family, 0) + 1
    for family, count in family_counts.items():
        if count > _INDICATOR_FAMILY_CAP:
            over = [
                s.node_path for s in indicator_specs
                if _CATALOG_TO_REGISTRY.get(s.catalog_name, s.catalog_name) == family
            ][_INDICATOR_FAMILY_CAP]
            _record(FamilyCapExceededError(
                f"Too many distinct {family!r} specs ({count}); "
                f"max {_INDICATOR_FAMILY_CAP} per request",
                node_id=over,
            ))

    if any(exc is not None for _diag, exc in found):
        return None, found

    # 5. Default exit attr
    if exit_attr is None:
        exit_attr = "@always_false"

    return CompiledProgram(
        indicator_specs=indicator_specs,
        per_bar_program=per_bar_program,
        simulator_settings=simulator_settings,
        entry_attr=entry_attr,
        exit_attr=exit_attr,
    ), found

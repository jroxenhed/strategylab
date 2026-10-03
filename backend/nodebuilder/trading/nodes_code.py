"""Code nodes on the trading side (F435 Wave 7, item 7.C).

The code runtime (nodebuilder.code) and the ch() references
(kernel/params.py) know nothing about trading.  This module plugs them into
the trading graph:

- ``wrangle`` (category Code): a node whose whole body is code.  Inputs
  in0 to in3 are merged by the normal stream rules; the code reads the
  merged stream and writes one or more attributes (design note 3.3).
- ``extra_io(node, nt)``: what a node's code adds to its stream schema
  (kernel.schema.ExtraIO): its writes, its lookback_bars, and that it may
  read any column.  compile gives it to analyze_graph.
- ``wrap_code_steps(...)``: gives every step whose node carries code an
  impl that runs that code (a Wrangle: on its merged input; a built-in
  node's code block: after the node's own compute, on its output stream).
  ``code.run`` stays the only place user code runs.
- compile's code rules: ``not_codeable_problems`` (a param read before
  any cook never holds code: a Ticker's symbol, interval and prefix, every
  param the simulator plan or the bot reads, a code node's lookback_bars),
  ``misplaced_code`` (a code block on a node that cannot run one),
  ``silent_code`` (code that writes no attribute, a warning),
  ``cross_domain_refs`` (a ch() of an expression across Ticker domains),
  ``disabled_found`` (the kill switch).
- ``code_node_ids`` (what counts as code for the kill switch) and
  ``first_code_problem`` (the bots.json load check: prepare only, never
  run).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Iterable, Mapping, Optional

from nodebuilder.kernel.registry import PortSpec, PortsSpec, register_node
from nodebuilder.models import GraphValidationError, is_expr_value

WRANGLE_TYPE = "wrangle"

# A Wrangle merges up to four streams (S46: in0..in3, never more).
WRANGLE_INPUTS = PortsSpec(ports=(PortSpec("in0"), PortSpec("in1", optional=True)),
                           dynamic=True, min=1, max=4)

# A Ticker's params decide which bars are fetched before any cook, and a
# bot trades one fixed symbol, so they never hold code (divergence V5).
# The catalog says so (code_able False, nodes_data).
TICKER_TYPE = "ticker"
TICKER_NOT_CODEABLE = ("symbol", "interval", "prefix")

PARAM_NOT_CODEABLE = "param_not_codeable"

# Reads that need a number and refuse a true/false column (the Size and Stop
# terminals' value; their compile check says the same for a known dtype).
NUMBER_ONLY = "number"
NUMBER_ONLY_READS = frozenset({("size", "value"), ("stop", "value")})


# ---------------------------------------------------------------------------
# Running code in a step
# ---------------------------------------------------------------------------


def _shown_error(exc: Any, plan: Any) -> Any:
    """*exc* with its node id turned into the id the user sees: a node
    inside a locked asset instance shows as the instance, with no param."""
    nid = getattr(exc, "node_id", None)
    flat = getattr(plan, "flat", None)
    if nid is not None and flat is not None:
        shown = flat.to_source(nid)
        if shown != nid:
            exc.node_id = shown
            exc.param = None
    return exc


def _shown(node_id: str, plan: Any, name: Optional[str]) -> tuple[str, Optional[str]]:
    """(id, name) the user sees for flat node *node_id*: a node inside a
    locked asset instance shows as the instance, under its name."""
    flat = getattr(plan, "flat", None)
    if flat is None:
        return node_id, name
    shown = flat.to_source(node_id)
    if shown is None or shown == node_id:
        return node_id, name
    src = flat.source if flat.source is not None else flat.graph
    node = src.nodes.get(shown)
    return shown, (getattr(node, "name", None) or shown)


def _check_needs(written: Iterable[Any], needs: Mapping[str, str], node_id: str) -> None:
    """attr_type on the writer when a write whose dtype was unknown before
    the cook ("any") is not what a node below reads it as (design note
    4.3): a true/false reader needs bool, a number reader a number."""
    from nodebuilder.code import CodeError

    for w in written:
        want = needs.get(w.name)
        if want == "bool" and w.dtype != "bool":
            raise CodeError(
                "attr_type",
                f"{w.name} is read as a true/false signal below, but the code wrote "
                f"{w.dtype} values.  Write a comparison, or annotate it: {w.name}: bool = ...",
                node_id=node_id)
        if want == "float" and w.dtype not in ("bool", "float", "int"):
            raise CodeError(
                "attr_type",
                f"{w.name} is read as a number below, but the code wrote {w.dtype} values.",
                node_id=node_id)
        if want == NUMBER_ONLY and w.dtype not in ("float", "int"):
            raise CodeError(
                "attr_type",
                f"{w.name} is read as a size or a stop below, which needs a number, but the "
                f"code wrote {w.dtype} values.",
                node_id=node_id)


def run_snippet(snippet: Any, plan: Any, stream: Any, params: Any, *,
                name: Optional[str] = None, needs: Optional[Mapping[str, str]] = None) -> Any:
    """Run a node's code block (or Wrangle body) on *stream* and return the
    stream with its writes.  Every failure raises nodebuilder.code.CodeError
    with the id the user sees; a partial result is never returned."""
    from nodebuilder.code import CodeError
    from nodebuilder.code import run as run_code
    from nodebuilder.kernel.params import resolver_for

    shown, shown_name = _shown(params.node_id, plan, name)
    try:
        result = run_code(snippet.prepared, stream, params=params,
                          resolve=resolver_for(params, plan, own=stream), writer=params.node_id,
                          node_name=shown_name, shown_node=shown)
        if needs:
            _check_needs(result.written, needs, params.node_id)
    except CodeError as exc:
        raise _shown_error(exc, plan) from None
    return result.stream


def code_step_impl(base: Optional[Callable[..., Any]], snippet: Any, plan: Any, *,
                   name: Optional[str] = None,
                   needs: Optional[Mapping[str, str]] = None) -> Callable[..., Any]:
    """The impl of a step whose node carries code.

    base is the node type's own impl (a code block runs after it, on its
    output stream: the input stream plus the node's own outputs), or None
    for a Wrangle (the code runs on the merged input stream)."""
    def impl(inputs: Any, params: Any) -> Any:
        stream = base(inputs, params) if base is not None else inputs
        return run_snippet(snippet, plan, stream, params, name=name, needs=needs)

    impl.__name__ = f"code_{getattr(base, '__name__', WRANGLE_TYPE)}"
    return impl


def _wrangle_impl(inputs: Any, params: Any) -> Any:
    """A Wrangle's cook: its code on the merged input stream.

    compile gives every code step its impl through wrap_code_steps (with
    the node's name and the checks of what nodes below read); this one
    serves a cook built straight from the kernel.  A Wrangle with no code
    passes its merged input on."""
    from nodebuilder.kernel.params import SCOPE_KEY

    scope = (getattr(params, "env", None) or {}).get(SCOPE_KEY)
    plan = getattr(scope, "plan", None)
    snippet = plan.code_of(params.node_id) if plan is not None else None
    if snippet is None:
        return inputs
    return run_snippet(snippet, plan, inputs, params)


register_node(
    name=WRANGLE_TYPE, cat="code",
    desc="Code that reads the merged input stream and writes new attributes.",
    params=(), inputs=WRANGLE_INPUTS, impl=_wrangle_impl,
    reads=(), writes=(), subtitle="wrangle", ins=2, outs=0, module=__name__,
)


# ---------------------------------------------------------------------------
# What code adds to the schema, and the steps that run it
# ---------------------------------------------------------------------------


def _code_text(node: Any) -> Optional[str]:
    code = getattr(node, "code", None)
    return code if isinstance(code, str) and code.strip() else None


def _prepared_code(node: Any, node_id: Optional[str] = None) -> Any:
    """The node's code block (or Wrangle body) prepared, or None.  The same
    call kernel.params.plan_params makes, so the cache serves both."""
    source = _code_text(node)
    if source is None:
        return None
    from nodebuilder.code import prepare
    from nodebuilder.kernel.params import code_context

    return prepare(source, code_context(node), node,
                   node_id=node_id if node_id is not None else getattr(node, "id", None))


def own_write_dtypes(node: Any, nt: Any) -> dict[str, str]:
    """{@name: dtype} of what the node type itself writes (its write params,
    under their stored or default names, and its fixed writes).  A code
    block that overwrites one of these without an annotation keeps that
    dtype, and the cook holds it to it (wrap_code_steps)."""
    if nt is None:
        return {}
    params = getattr(node, "params", None) or {}
    out: dict[str, str] = {}
    for spec in nt.write_params():
        name = params.get(spec.name) or spec.default
        if isinstance(name, str) and name:
            out[name] = spec.dtype or "float"
    for name, dtype in nt.fixed_writes_for(params):
        out.setdefault(name, dtype)
    return out


def extra_io(node: Any, nt: Any) -> Any:
    """kernel.schema.ExtraIO for a node's code block or Wrangle body, or None
    when it has none.  prepare() only (cached); the code never runs here.

    Every write is a point attribute of dtype bool or float (from its
    annotation) or any; a code block that overwrites one of its node's own
    outputs without an annotation keeps that output's dtype.  The code may
    read any column of the stream it sees, and needs lookback_bars of
    history.  Writing a name a node upstream wrote is attr_clash (design
    note 3.2)."""
    prepared = _prepared_code(node)
    if prepared is None:
        return None
    from nodebuilder.kernel.schema import ExtraIO

    own = own_write_dtypes(node, nt) if node.type != WRANGLE_TYPE else {}
    writes = []
    for n in prepared.writes:
        dtype = prepared.write_dtypes.get(n)
        if dtype is None:
            dtype = own.get(f"@{n}") if own.get(f"@{n}") in ("bool", "float") else "any"
        writes.append((f"@{n}", dtype))
    return ExtraIO(
        writes=tuple(writes),
        reads_any=True,
        lookback=max(int(prepared.effective_lookback(node.params) or 0), 0),
        shadow_ok=False,
    )


def broken_code_nodes(graph: Any) -> list[str]:
    """Ids of nodes whose code does not prepare (a syntax error, an
    oversized snippet, a dynamic ch() name...).  compile tells the kernel
    they are already reported, so the nodes below them are skipped instead
    of reporting what is missing only because the code is broken (its
    writes are unknown).  prepare() only; cached."""
    from nodebuilder.code import prepare
    from nodebuilder.models import expr_text

    out: list[str] = []
    for nid, node in graph.nodes.items():
        snippets = []
        block = _prepared_code(node, nid)
        if block is not None:
            snippets.append(block)
        for pname, value in (node.params or {}).items():
            text = expr_text(value)
            if text is not None:
                snippets.append(prepare(text, "expr", node, node_id=nid, param=pname))
        if any(not p.ok for p in snippets):
            out.append(nid)
    return out


def consumer_needs(analysis: Any, plan: Any) -> dict[str, dict[str, str]]:
    """writer id -> {attribute -> "bool" | "float"}: what the nodes below
    read a code write as, for each write whose dtype is not known before
    the cook ("any").  The cook checks the real value (attr_type)."""
    writers = {nid for (nid, param) in getattr(plan, "snippets", {}) if param is None}
    out: dict[str, dict[str, str]] = {}
    if not writers:
        return out
    for res in analysis.nodes.values():
        if res.status != "run" or res.node_type is None:
            continue
        for read in res.reads.values():
            for item in (read if isinstance(read, list) else [read]):
                if item.status != "live" or item.writer not in writers or item.dtype != "any":
                    continue
                spec = res.node_type.param(item.param)
                want = getattr(spec, "dtype", None)
                if (res.node_type.name, item.param) in NUMBER_ONLY_READS:
                    want = NUMBER_ONLY
                if want not in ("bool", "float", NUMBER_ONLY):
                    continue
                mine = out.setdefault(item.writer, {})
                mine[item.name] = _stricter(mine.get(item.name), want)
    return out


_NEED_RANK = {"float": 0, NUMBER_ONLY: 1, "bool": 2}


def _stricter(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """The stricter of two needs: bool, then a number only, then float."""
    if a is None:
        return b
    if b is None:
        return a
    return a if _NEED_RANK.get(a, -1) >= _NEED_RANK.get(b, -1) else b


def wrap_code_steps(steps: tuple, plan: Any, analysis: Any = None) -> tuple:
    """*steps* with every running step that carries code given an impl that
    runs it (code_step_impl).  A graph without code comes back unchanged."""
    if plan is None or not any(param is None for (_nid, param) in plan.snippets):
        return steps
    needs = consumer_needs(analysis, plan) if analysis is not None else {}
    nodes = plan.flat.graph.nodes
    out = []
    for step in steps:
        snippet = plan.code_of(step.node_id) if step.mode == "run" else None
        if snippet is None:
            out.append(step)
            continue
        node = nodes.get(step.node_id)
        base = None if step.type == WRANGLE_TYPE else step.impl
        mine = dict(needs.get(step.node_id, {}))
        if base is not None and analysis is not None:
            # An unannotated overwrite of the node's own output kept that
            # output's dtype in the schema (extra_io): hold the cook to it.
            res = analysis.nodes.get(step.node_id)
            own = own_write_dtypes(node, getattr(res, "node_type", None))
            prepared = snippet.prepared
            for name in prepared.writes:
                dtype = own.get(f"@{name}")
                if name not in prepared.write_dtypes and dtype in ("bool", "float"):
                    mine[f"@{name}"] = _stricter(mine.get(f"@{name}"), dtype)
        out.append(replace(step, impl=code_step_impl(
            base, snippet, plan, name=(getattr(node, "name", None) or step.node_id),
            needs=mine or None)))
    return tuple(out)


# ---------------------------------------------------------------------------
# compile's code rules
# ---------------------------------------------------------------------------


def _coded(message: str, code: str, node_id: Optional[str], param: Optional[str] = None
           ) -> GraphValidationError:
    from nodebuilder.kernel.schema import coded
    return coded(GraphValidationError(message, node_id=node_id), code, param=param)


def _found(errors: Iterable[GraphValidationError]) -> list:
    from nodebuilder.diagnostics import from_error
    return [(from_error(exc), exc) for exc in errors]


def _has_code(node: Any) -> bool:
    return node.type == WRANGLE_TYPE or _code_text(node) is not None


def _why_read_before_cook(node: Any, param: str) -> str:
    """The second sentence of a param_not_codeable message."""
    from nodebuilder.code import LOOKBACK_PARAM
    from nodebuilder.trading.sim_bridge import PLAN_READ_PARAMS

    if node.type == TICKER_TYPE:
        return ("A Ticker's symbol, interval and prefix decide which bars are fetched, and a "
                "bot trades one fixed symbol.")
    if param in PLAN_READ_PARAMS.get(node.type, ()):
        return "The backtest and the bot read it before any bar is cooked."
    if param == LOOKBACK_PARAM:
        return "It decides how many bars are fetched before the code runs."
    return ""


def not_codeable_problems(graph: Any) -> tuple[list, list[str]]:
    """``(found, node ids)``: param_not_codeable on every param that holds an
    expression although it is read before any cook (one shared rule):

    - a param whose spec says ``code_able=False``: a Ticker's symbol,
      interval and prefix, and every param the simulator plan and the bot
      config read (sim_bridge.PLAN_READ_PARAMS, marked at import below);
    - a code node's ``lookback_bars`` (it sizes the fetch).

    Network and boundary nodes are left to kernel.params (their params
    never reach the kernel walk).  compile passes the ids to the kernel as
    already reported, so it does not report them again."""
    from nodebuilder.code import LOOKBACK_PARAM
    from nodebuilder.kernel import registry
    from nodebuilder.kernel.flatten import boundary_kind, is_network_type
    from nodebuilder.kernel.schema import not_codeable_error

    errors: list[GraphValidationError] = []
    ids: list[str] = []
    for nid, node in graph.nodes.items():
        params = node.params or {}
        exprs = [p for p, v in params.items() if is_expr_value(v)]
        if not exprs or is_network_type(node.type) or boundary_kind(node.type) is not None:
            continue
        nt = registry.get(node.type)
        bad = []
        for pname in exprs:
            spec = nt.param(pname) if nt is not None else None
            if spec is not None and not spec.code_able:
                bad.append(pname)
            elif spec is None and pname == LOOKBACK_PARAM and _has_code(node):
                bad.append(pname)
        for pname in bad:
            errors.append(not_codeable_error(node, pname, _why_read_before_cook(node, pname)))
        if bad:
            ids.append(nid)
    return _found(errors), ids


def _mark_plan_reads() -> None:
    """code_able False in the catalog for every param the simulator plan or
    the bot config reads before any cook (sim_bridge.PLAN_READ_PARAMS), so
    the editor offers no expression there and compile refuses one with
    param_not_codeable.  Runs once, when this module (the last in
    trading.MODULE_ORDER) registers."""
    from nodebuilder.kernel import registry
    from nodebuilder.trading.sim_bridge import PLAN_READ_PARAMS

    for type_name, names in PLAN_READ_PARAMS.items():
        if registry.get(type_name) is not None:
            registry.mark_not_codeable(type_name, names)


_mark_plan_reads()


def cross_domain_refs(program: Any, plan: Any) -> list:
    """ref_broken (on the reader, at the ch() call) for a ch() of another
    node's expression param when the two cook on different bars: the
    target in a reference Ticker's domain, the reader outside it.  Each
    domain cooks on its own (align.cook_domains), so the reader's cook
    never sees the evaluated value; compile says so instead of every cook
    failing.  Checked for the whole program and for each group's own cook.
    Node ids are the flat graph's; the caller maps them (flat.remap)."""
    from nodebuilder.trading import align
    from nodebuilder.trading.nodes_groups import program_for_steps

    refs = [r for r in getattr(plan, "refs", ()) if r.dyn is not None and r.problem is None
            and r.dyn[0] != r.reader_id]
    if not refs:
        return []
    cuts = [program] + [program_for_steps(program, g.step_ids)
                        for g in (getattr(program, "groups", ()) or ())]
    bad: dict[tuple, Any] = {}
    for cut in cuts:
        roles = align.ticker_roles(cut)
        if not roles.needs_domains:
            continue
        domains = align.step_domains(cut.steps, roles)
        for r in refs:
            if r.reader_id not in domains or r.dyn[0] not in domains:
                continue
            target_dom, reader_dom = domains[r.dyn[0]], domains[r.reader_id]
            if target_dom is None or target_dom == align._PRIMARY or target_dom == reader_dom:
                continue
            bad.setdefault((r.reader_id, r.reader_param, r.path), (r, target_dom))
    errors = []
    for r, dom in bad.values():
        err = _coded(
            f"{r.func}({r.path!r}): {plan._name(r.dyn[0])!r} cooks on the {dom[0]} {dom[1]} "
            f"bars of a reference Ticker, and this node cooks on other bars, so the "
            f"expression on its {r.dyn[1]} has no value here.  Read an attribute it writes "
            f"(ch('../{plan._name(r.dyn[0])}/@name')), or give the param a plain value.",
            "ref_broken", r.reader_id, r.reader_param)
        for key in ("line", "col", "end_line", "end_col"):
            setattr(err, key, getattr(r, key, None))
        errors.append(err)
    return _found(errors)


def misplaced_code(flat: Any) -> list:
    """param_invalid for a code block where none can run: on a network or a
    boundary node (flatten takes them out, so they never cook), and on a
    node with no output (a terminal: nothing below could read its writes).
    Node ids are the flat graph's; the caller maps them (flat.remap)."""
    from nodebuilder.kernel import registry

    errors: list[GraphValidationError] = []
    taken_out = list(flat.networks.items()) + list(flat.boundaries.items())
    for nid, node in taken_out:
        if _code_text(node) is not None:
            errors.append(_coded(
                f"{node.type} {node.name or nid!r} is a network node: it runs no code of its "
                f"own.  Put the code on a node inside it, or in a Wrangle.", "param_invalid", nid))
    for nid, node in flat.graph.nodes.items():
        if _code_text(node) is None or node.type == WRANGLE_TYPE:
            continue
        nt = registry.get(node.type)
        if nt is not None and not nt.has_output:
            errors.append(_coded(
                f"{node.type} {node.name or nid!r} has no output, so nothing could read what its "
                f"code block writes.  Put the code in a node above it, or in a Wrangle.",
                "param_invalid", nid))
    return _found(errors)


def silent_code(flat: Any) -> list:
    """code_writes_nothing (a warning, S46) for code that writes no
    attribute: a Wrangle whose body is empty or sets no @name, and a code
    block on a node that sets no @name (it cannot change what the node
    hands below).  Code that does not prepare is left out (it has its own
    error), and so is a block where no code can run (misplaced_code).
    prepare() only; cached.  Node ids are the flat graph's; the caller
    maps them (flat.remap)."""
    from nodebuilder.diagnostics import make
    from nodebuilder.kernel import registry

    found: list = []
    for nid, node in flat.graph.nodes.items():
        is_wrangle = node.type == WRANGLE_TYPE
        if not is_wrangle:
            if _code_text(node) is None:
                continue
            nt = registry.get(node.type)
            if nt is None or not nt.has_output:
                continue
        prepared = _prepared_code(node, nid)
        if prepared is not None and (not prepared.ok or prepared.writes):
            continue
        name = node.name or nid
        if is_wrangle:
            message = (f"Wrangle {name!r} writes nothing: its code sets no @attribute, so the "
                       f"nodes below see only its merged input.  Add a line like @out = @close.")
        else:
            message = (f"The code block on {node.type} {name!r} writes nothing: it sets no "
                       f"@attribute, so it changes nothing below.  Add a line like "
                       f"@name = ..., or clear the code.")
        found.append((make("code_writes_nothing", message, node_id=nid, severity="warning"), None))
    return found


def code_node_ids(graph: Any) -> list[str]:
    """Every node that holds code, in node order: a Wrangle (with or without
    code, as the editor counts it, S49), a node with a code block, or a
    param that holds an expression.  The kill switch's view of a graph."""
    nodes = graph.get("nodes", {}) if isinstance(graph, Mapping) else getattr(graph, "nodes", {})
    out: list[str] = []
    for nid, node in (nodes or {}).items():
        get = node.get if isinstance(node, Mapping) else (lambda k, n=node: getattr(n, k, None))
        code = get("code")
        params = get("params") or {}
        if (get("type") == WRANGLE_TYPE or (isinstance(code, str) and code.strip())
                or any(is_expr_value(v) for v in (params.values() if isinstance(params, Mapping)
                                                  else ()))):
            out.append(nid)
    return out


def disabled_found(graph: Any, flat: Any = None) -> list:
    """code_disabled, one per node that holds code, while SL_CODE_NODES=0
    (design note 4.9).  Ids are the ones the user sees."""
    from nodebuilder.code import ENV_SWITCH

    errors = []
    seen: set[str] = set()
    for nid in code_node_ids(graph):
        shown = flat.to_source(nid) if flat is not None else nid
        if shown in seen:
            continue
        seen.add(shown)
        node = graph.nodes.get(nid)
        name = (getattr(node, "name", None) or nid) if node is not None else nid
        errors.append(_coded(
            f"Code nodes are turned off on this server ({ENV_SWITCH}=0), so {name!r} cannot "
            f"cook.  The graph can still be saved.", "code_disabled", shown))
    return _found(errors)


def first_code_problem(graph: Any) -> Optional[tuple[Any, str]]:
    """``(diagnostic, node name)`` for the first code snippet in *graph* that
    does not prepare (a syntax error, an oversized snippet, a dynamic ch()
    name...), or None.  prepare() only: nothing runs.  The bots.json load
    check (design note 4.11)."""
    from nodebuilder.code import iter_code_snippets, prepare

    nodes = graph.nodes
    for snip in iter_code_snippets(graph):
        node = nodes.get(snip.node_id)
        prepared = prepare(snip.source, snip.context, node, node_id=snip.node_id,
                           param=snip.param)
        bad = next((d for d in prepared.diagnostics if d.severity == "error"), None)
        if bad is not None:
            return bad, snip.name
    return None

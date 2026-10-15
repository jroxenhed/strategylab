"""ch() references and the values of params set by code (F435 Wave 7, item 7.B).

Code reads params with ``ch``, ``chf``, ``chi``, ``chs``, ``chb`` and
``chv`` (nodebuilder.code).  A name without ``/`` is the calling node's own
param.  A name with ``/`` is a path, read the Houdini way, with the calling
node as the starting folder:

- ``../other/param`` is a sibling's param, ``../other/@attr`` that node's
  output attribute;
- ``../name`` is a param of the network the node sits in (a promoted param
  at a subnet root, say);
- ``/shared/spread/@spread`` starts at the root.

Paths are resolved against the graph *before* flatten (``FlatGraph.source``,
with library assets already expanded), so they work inside asset instances.
The frontend resolves them the same way (code/chPaths.ts); the shared
vectors in tests/nodebuilder/vectors/ch_refs.json hold both to it.

``plan_params`` reads every snippet once (``prepare``, never run) and builds
a ParamPlan:

- every literal reference (``refs``) and the /validate edges
  (``param_deps``);
- ``ref_broken`` for a path that points at nothing, and ``ch_cycle`` on
  every node (or param) in a loop;
- the extra cook-order edges (``after``): a node that reads another node's
  ``@attr``, or a param of it that holds an expression, cooks after it;
- the order in which each node's expression params are evaluated.

At cook time the evaluator (kernel/evaluate.py) calls the plan's hook for a
node before its impl runs: each expression is evaluated (``code.run``, eval
mode, detail attributes only, a scalar of the param's type), then the
node's checks run again on the values (``schema.recheck``).  The impl gets
the evaluated params.  Code blocks and Wrangles read references through
``resolver_for(params)``.

Window sizing (``static_windows``): an expression that reads only params
has the same value at every cook, so compile evaluates it with the same
code and sizes the node's lookback from that value instead of the param's
max; one that reads data keeps the max, and the cook checks it.

No trading words in this module.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

from nodebuilder.kernel import registry as _registry
from nodebuilder.models import GraphValidationError, expr_text, is_expr_value

WRANGLE_TYPE = "wrangle"
"""The node type whose whole body is code (its context is ``wrangle``)."""

SCOPE_KEY = "param_scope"
"""Where the cook keeps its ParamScope in the cook environment (params.env)."""

CH_CYCLE = "ch_cycle"
REF_BROKEN = "ref_broken"
PARAM_INVALID = "param_invalid"
PARAM_OUT_OF_RANGE = "param_out_of_range"
PARAM_NOT_CODEABLE = "param_not_codeable"
CODE_TYPE = "code_type"

OWN = "own"      # a bare name: the calling node's own param
PARAM = "param"  # a path to another node's (or a network's) param
ATTR = "attr"    # a path to a node's output @attribute


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def child_index(nodes: Mapping[str, Any]) -> dict[tuple[Optional[str], str], str]:
    """(parent id or None, name) -> node id, for walking paths."""
    out: dict[tuple[Optional[str], str], str] = {}
    for nid, node in nodes.items():
        out.setdefault((node.parent, node.name), nid)
    return out


def resolve_path(nodes: Mapping[str, Any], from_id: str, path: str,
                 children: Optional[Mapping[tuple[Optional[str], str], str]] = None
                 ) -> Optional[tuple[str, str]]:
    """Where a ch() name points from node *from_id*: ``(node id, target)``,
    where target is a param name or an ``@attr``.  None when it leads
    nowhere (a missing node, above the root, or the root itself).

    A bare name is the calling node's own.  Otherwise the calling node is
    the starting folder: ``..`` goes up to the network it sits in, a name
    steps into the child with that name, and a leading ``/`` starts at the
    root.  The last segment is the target.  The same rules as the
    frontend's ``resolveChPath`` (code/chPaths.ts).
    """
    if not isinstance(path, str) or path == "" or from_id not in nodes:
        return None
    if "/" not in path:
        return from_id, path
    absolute = path.startswith("/")
    segments = (path[1:] if absolute else path).split("/")
    target = segments.pop()
    if not target:
        return None
    if children is None:
        children = child_index(nodes)
    cursor: Optional[str] = None if absolute else from_id
    at_node = not absolute
    for seg in segments:
        if seg in ("", "."):
            continue
        if seg == "..":
            if not at_node:
                return None  # above the root
            cursor = nodes[cursor].parent
            at_node = cursor is not None
            continue
        child = children.get((cursor, seg))
        if child is None:
            return None
        cursor, at_node = child, True
    if cursor is None:
        return None
    return cursor, target


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Snippet:
    """One piece of code on a node that cooks."""
    node_id: str               # flat id (also its id in the pre-flatten graph)
    param: Optional[str]       # the param an expression sits on; None for code
    context: str               # expr | node_code | wrangle
    prepared: Any              # nodebuilder.code.PreparedCode


@dataclass(frozen=True)
class Ref:
    """One literal ch*() call and what it points at.

    kind      : ``own`` (a bare name), ``param`` or ``attr`` (a path).
    target_id : the node it points at, as an id of the pre-flatten graph (a
                network node for ``../name``); None when it points nowhere.
    target    : the param name, or the attribute with its ``@``.
    problem   : why it is broken, else None.
    problem_code: the diagnostic code of *problem* (None: ref_broken).
    """
    reader_id: str
    reader_param: Optional[str]
    func: str
    path: str
    kind: str
    target_id: Optional[str]
    target: str
    line: Optional[int] = None
    col: Optional[int] = None
    end_line: Optional[int] = None
    end_col: Optional[int] = None
    problem: Optional[str] = None
    # Filled for a working reference that is read at cook time: the flat
    # node whose evaluated param (``dyn``) or output stream (``stream``)
    # it needs.
    dyn: Optional[tuple[str, str]] = None
    stream: Optional[str] = None
    problem_code: Optional[str] = None


@dataclass(eq=False)
class ParamPlan:
    """Every ch() reference of a graph, checked, with the cook order they need.

    found       : (Diagnostic, error) pairs: ref_broken, ch_cycle, and
                  param_invalid for code on a network or boundary node's
                  params.  Node ids are the ids the user sees.
    refs        : every literal ch*() call, in node order.
    snippets    : (flat id, param or None) -> Snippet, for every node that
                  cooks.
    after       : flat id -> flat ids that must cook first (ch() edges).
    expr_order  : flat id -> its expression params, in evaluation order.
    attr_reads  : flat id -> (writer, name) columns its ch("@attr") reads
                  keep alive until it cooks.
    """
    flat: Any
    analysis: Any = None
    found: list = field(default_factory=list)
    refs: list[Ref] = field(default_factory=list)
    snippets: dict[tuple[str, Optional[str]], Snippet] = field(default_factory=dict)
    after: dict[str, tuple[str, ...]] = field(default_factory=dict)
    expr_order: dict[str, tuple[str, ...]] = field(default_factory=dict)
    attr_reads: dict[str, tuple[tuple[Optional[str], str], ...]] = field(default_factory=dict)
    _by_reader: dict[tuple[str, str], Ref] = field(default_factory=dict)
    _cyclic: set[str] = field(default_factory=set)

    # -- what other modules read -----------------------------------------

    @property
    def has_code(self) -> bool:
        return bool(self.snippets)

    def code_of(self, node_id: str) -> Optional[Snippet]:
        """The node's code block or Wrangle body, if it has one."""
        return self.snippets.get((node_id, None))

    def code_diagnostics(self) -> list:
        """What prepare() found in every snippet (code_syntax, code_limit,
        ch_dynamic, attr_dynamic, code_type...), as (Diagnostic, error)
        pairs with the ids the user sees.  Not part of ``found``: compile
        decides where these are reported."""
        from nodebuilder.code import CodeError
        from nodebuilder.diagnostics import from_error

        out = []
        for snip in self.snippets.values():
            for cd in snip.prepared.diagnostics:
                exc = CodeError.from_diagnostic(cd)
                out.append((from_error(exc), exc))
        return self.flat.remap(out) if out else []

    def param_deps(self) -> list[dict[str, Any]]:
        """The /validate edges (plan W7 contracts): one per literal ch()
        reference that points somewhere, with the ids the user sees.

        ``reader_param`` names the param whose expression holds the call,
        or is None for a call in a code block or a Wrangle body.  A bare
        name read by a code block or a Wrangle is its own param (or the
        param it declares), not a reference, so it has no edge.
        """
        out: list[dict[str, Any]] = []
        seen: set[tuple] = set()
        for r in self.refs:
            if r.target_id is None or r.problem is not None:
                continue
            if r.kind == OWN and r.reader_param is None:
                continue
            reader = self._shown(r.reader_id)
            target_id = self._shown(r.target_id)
            target = r.target
            if target_id != r.target_id and r.kind == PARAM:
                # A param inside a locked asset: shown on the instance only
                # when the instance promotes it.
                continue
            if reader != r.reader_id and reader == target_id:
                continue  # both inside one locked asset instance
            reader_param = r.reader_param if reader == r.reader_id else None
            key = (reader, reader_param, target_id, target)
            if key in seen:
                continue
            seen.add(key)
            out.append({"reader_id": reader, "reader_param": reader_param,
                        "target_id": target_id, "target": target})
        return out

    def resolver(self, node_id: str, scope: Optional["ParamScope"] = None,
                 own: Any = None) -> Callable[[str], Any]:
        """``resolve(path)`` for ``code.run``: the value a ch() path of node
        *node_id* reads.  Static values need no scope; an expression's value
        or an ``@attr`` needs the cook's ParamScope.  *own* is the stream a
        code block runs on (its node's output): a path to one of the node's
        own outputs reads it there.  Raises KeyError for a path the plan
        does not know (run() turns it into ref_broken) and a CodeError for a
        broken one."""
        def resolve(path: str) -> Any:
            ref = self._by_reader.get((node_id, path))
            if ref is None:
                raise KeyError(path)
            return self._value(ref, scope, own)
        return resolve

    def hook(self, node_id: str) -> Optional[Callable[..., Any]]:
        """The evaluator's param hook for *node_id* (None when it has no
        expression): ``hook(inputs, params, scope) -> Params``."""
        exprs = self.expr_order.get(node_id)
        if not exprs or self.analysis is None:
            return None
        res = self.analysis.nodes.get(node_id)
        node = self.flat.graph.nodes.get(node_id)
        if res is None or node is None:
            return None

        def hook(inputs: Any, params: Any, scope: "ParamScope") -> Any:
            return self._evaluate(node, res, exprs, inputs, params, scope)

        return hook

    # -- cook time ----------------------------------------------------------

    def _evaluate(self, node: Any, res: Any, exprs: tuple[str, ...], inputs: Any, params: Any,
                  scope: "ParamScope") -> Any:
        checked = self._evaluate_values(node, res, exprs, inputs, params, scope)
        self._check_window(node, res, exprs, checked)
        return checked.with_env(params.env)

    def _evaluate_values(self, node: Any, res: Any, exprs: tuple[str, ...], inputs: Any,
                         params: Any, scope: "ParamScope",
                         only: Optional[tuple[str, ...]] = None) -> Any:
        """The node's params with its expressions evaluated and checked
        (``schema.recheck``).  The cook's hook and compile's window sizing
        (``static_windows``) both run this.  *only*: evaluate just these
        expressions (in *exprs* order); the others keep their stored
        expression, so recheck counts them at their stand-in."""
        from nodebuilder.code import CodeError, run as run_code
        from nodebuilder.kernel.schema import recheck

        current = dict(params)
        for p in exprs:
            current[p] = node.params[p]  # an early read is a code_type error, never a stand-in
        values: dict[str, Any] = {}
        resolve = self.resolver(node.id, scope)
        shown = self._shown(node.id)
        for p in exprs:
            if only is not None and p not in only:
                continue
            snip = self.snippets[(node.id, p)]
            expected, options = self._expected(node, res.node_type, p)
            try:
                result = run_code(snip.prepared, inputs, params=current, resolve=resolve,
                                  expected=expected, options=options,
                                  node_name=self._name(shown), shown_node=shown)
            except CodeError as exc:
                raise self._shown_error(exc) from None
            value = result.value
            problem = self._spare_problem(node.id, p, value)
            if problem is not None:
                raise self._shown_error(CodeError(problem[0], problem[1], node_id=node.id,
                                                  param=p)) from None
            values[p] = value
            current[p] = value
        try:
            checked = recheck(res, node, values)
        except GraphValidationError as exc:
            raise self._shown_error(CodeError(
                getattr(exc, "code", None) or PARAM_INVALID, str(exc),
                node_id=node.id, param=getattr(exc, "param", None))) from None
        return checked

    def _check_window(self, node: Any, res: Any, exprs: tuple[str, ...], checked: Any) -> None:
        """code_type when the evaluated params need more rows of history than
        compile planned (an expression that reads only params counts at its
        exact value, ``static_windows``; one that reads data counts at the
        param's max, or with no max at its default).  The live fetch window
        was sized from the plan, so a longer window would give the bot
        different values than the backtest: never silent."""
        from nodebuilder.code import CodeError

        nt = getattr(res, "node_type", None)
        if nt is None or nt.lookback is None:
            return
        try:
            need = int(nt.lookback(checked))
        except (TypeError, ValueError, KeyError, OverflowError):
            return
        extra = getattr(res, "extra", None)
        if extra is not None:
            need += max(int(extra.lookback), 0)
        if need <= res.lookback:
            return
        param = next((p for p in exprs if nt.param(p) is not None), exprs[0] if exprs else None)
        spec = nt.param(param) if param is not None else None
        why = (f" ({param} has no max, so the window was sized for its default)"
               if spec is not None and spec.max is None else "")
        raise self._shown_error(CodeError(
            CODE_TYPE,
            f"{self._name(node.id)!r}: the expression gives {param} = {checked.get(param)!r}, "
            f"which needs {need} bars of history, more than the {res.lookback} the fetch "
            f"window was sized for{why}.  Use a plain value, or keep the expression at or "
            f"below the default.", node_id=node.id, param=param)) from None

    def _value(self, ref: Ref, scope: Optional["ParamScope"], own: Any = None) -> Any:
        from nodebuilder.code import CodeError

        if ref.problem is not None or ref.target_id is None:
            raise CodeError(ref.problem_code or REF_BROKEN,
                            ref.problem or f"{ref.path!r} points at nothing.")
        if ref.kind == ATTR:
            if own is not None and ref.stream == ref.reader_id and ref.reader_param is None:
                stream = own   # a code block reading its own node's output
            else:
                stream = scope.stream(ref.stream) if (scope is not None and ref.stream) else None
            if stream is None:
                raise CodeError(REF_BROKEN, f"{ref.path!r}: {self._name(ref.target_id)!r} "
                                            "has no stream in this cook.")
            if ref.target in stream.detail:
                return stream.value(ref.target)
            if ref.target in stream.points:
                return stream.series(ref.target).copy()
            raise CodeError(REF_BROKEN, f"{ref.path!r}: {self._name(ref.target_id)!r} does not "
                                        f"write {ref.target}.")
        if ref.dyn is not None:
            fid, p = ref.dyn
            got = scope.params_of(fid) if scope is not None else None
            if got is None or p not in got:
                raise CodeError(REF_BROKEN, f"{ref.path!r}: the expression on "
                                            f"{self._name(fid)!r} {p} has no value yet.")
            return got[p]
        return self._static(ref.target_id, ref.target)[1]

    # -- static values --------------------------------------------------------

    def _static(self, nid: str, name: str, seen: Optional[set] = None
                ) -> tuple[Optional[tuple[str, str]], Any]:
        """``(dyn, value)`` for param *name* of node *nid* (pre-flatten id).

        dyn is ``(flat id, param)`` when the value is an expression the cook
        evaluates (value is then None).  A promoted param is read from its
        target, where flatten put its value.
        """
        seen = set() if seen is None else seen
        if (nid, name) in seen:
            return None, None
        seen.add((nid, name))
        flat = self.flat
        if nid in flat.networks:
            target = self._promoted_targets.get((nid, name))
            if target is not None:
                return self._static(target[0], target[1], seen)
            values = flat.network_params.get(nid, {})
            if name in values:
                return None, values[name]
            return None, _registry_default(flat.networks[nid].type, name)
        node = flat.graph.nodes.get(nid)
        if node is None:  # a boundary node, or a node in a bypassed network
            src = self._source.nodes.get(nid)
            value = (src.params or {}).get(name) if src is not None else None
            if is_expr_value(value):
                return (nid, name), None  # never evaluated: the node does not cook
            return None, value if value is not None else _registry_default(
                src.type if src is not None else "", name)
        stored = node.params or {}
        if name in stored:
            if is_expr_value(stored[name]):
                return (nid, name), None
            return None, stored[name]
        spec_default = _registry_default(node.type, name)
        if spec_default is not None:
            return None, spec_default
        spare = self._spare_spec(nid, name)
        return None, spare.default if spare is not None else None

    # -- helpers --------------------------------------------------------------

    @property
    def _source(self) -> Any:
        return self.flat.source if self.flat.source is not None else self.flat.graph

    @property
    def _promoted_targets(self) -> dict[tuple[str, str], tuple[str, str]]:
        cached = self.__dict__.get("_pt")
        if cached is None:
            cached = {owner: target for target, owner in self.flat.promoted_from.items()}
            self.__dict__["_pt"] = cached
        return cached

    def _shown(self, nid: Optional[str]) -> Optional[str]:
        return self.flat.to_source(nid) if nid is not None else None

    def _name(self, nid: Optional[str]) -> str:
        node = self._source.nodes.get(nid or "")
        return (node.name or nid) if node is not None else (nid or "?")

    def _spare_spec(self, nid: str, name: str) -> Any:
        snip = self.snippets.get((nid, None))
        if snip is not None:
            for s in snip.prepared.spare_params:
                if s.name == name:
                    return s
        node = self._source.nodes.get(nid)
        for s in (getattr(node, "spare_params", None) or []):
            if s.name == name:
                return s
        return None

    def _expected(self, node: Any, nt: Any, param: str) -> tuple[Optional[str], Any]:
        spec = nt.param(param) if nt is not None else None
        if spec is not None:
            kind = {"int": "int", "number": "float", "bool": "bool", "string": "string",
                    "select": "select"}.get(spec.type)
            return kind, (list(spec.options) if spec.options is not None else None)
        spare = self._spare_spec(node.id, param)
        if spare is not None:
            if spare.type == "string" and spare.options:
                return "select", list(spare.options)
            return {"float": "float", "int": "int", "string": "string",
                    "bool": "bool"}.get(spare.type), None
        return None, None

    def _spare_problem(self, nid: str, name: str, value: Any) -> Optional[tuple[str, str]]:
        spare = self._spare_spec(nid, name)
        return spare_value_problem(spare, value) if spare is not None else None

    def _shown_error(self, exc: Any) -> Any:
        """*exc* with its node id turned into the id the user sees (a node
        inside a locked asset shows as the instance, with no param)."""
        nid = getattr(exc, "node_id", None)
        if nid is not None:
            shown = self._shown(nid)
            if shown != nid:
                exc.node_id = shown
                exc.param = None
        return exc


def spare_value_problem(spec: Any, value: Any) -> Optional[tuple[str, str]]:
    """(code, message) when *value* does not fit spare param *spec*: its
    type (param_invalid) or its min, max or options (param_out_of_range)."""
    if value is None:
        return None
    kind = getattr(spec, "type", None)
    name = getattr(spec, "name", "?")
    if kind in ("float", "int"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            try:
                value = float(str(value).strip())
            except ValueError:
                return PARAM_INVALID, f"{name} needs a number, got {value!r}."
        if not math.isfinite(float(value)):
            return PARAM_INVALID, f"{name} needs a finite number, got {value!r}."
        if kind == "int" and float(value) != int(float(value)):
            return PARAM_INVALID, f"{name} needs a whole number, got {value!r}."
        lo, hi = getattr(spec, "min", None), getattr(spec, "max", None)
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            return PARAM_OUT_OF_RANGE, f"{name} must be between {lo} and {hi}, got {value!r}."
        return None
    if kind == "bool":
        if isinstance(value, bool) or (isinstance(value, str) and value.lower() in ("true", "false")):
            return None
        return PARAM_INVALID, f"{name} needs true or false, got {value!r}."
    if kind == "string":
        if not isinstance(value, str):
            return PARAM_INVALID, f"{name} needs text, got {value!r}."
        options = getattr(spec, "options", None)
        if options and value not in options:
            return PARAM_OUT_OF_RANGE, f"{name} must be one of {list(options)}, got {value!r}."
        return None
    return None


def _registry_default(node_type: str, name: str) -> Any:
    nt = _registry.get(node_type)
    spec = nt.param(name) if nt is not None else None
    return spec.default if spec is not None else None


def _has_param(plan: ParamPlan, nid: str, name: str) -> bool:
    """True when node *nid* (pre-flatten id) has a param called *name*."""
    src = plan._source.nodes.get(nid)
    if src is None:
        return False
    nt = _registry.get(src.type)
    if nt is not None and nt.param(name) is not None:
        return True
    if any(p.name == name for p in (src.promoted or [])):
        return True
    if plan._spare_spec(nid, name) is not None:
        return True
    return name in (src.params or {})


# ---------------------------------------------------------------------------
# Cook-time state
# ---------------------------------------------------------------------------


class ParamScope:
    """What one cook knows for ch(): each node's output stream and its
    evaluated params.  The evaluator makes one per cook and keeps it in
    ``env[SCOPE_KEY]``."""

    def __init__(self, streams: Mapping[str, Any], plan: Optional[ParamPlan] = None) -> None:
        self._streams = streams
        self._params: dict[str, Mapping[str, Any]] = {}
        self.plan = plan

    def stream(self, node_id: Optional[str]) -> Any:
        return self._streams.get(node_id) if node_id is not None else None

    def params_of(self, node_id: str) -> Optional[Mapping[str, Any]]:
        return self._params.get(node_id)

    def set_params(self, node_id: str, params: Mapping[str, Any]) -> None:
        self._params[node_id] = params


def resolver_for(params: Any, plan: Optional[ParamPlan] = None, own: Any = None
                 ) -> Callable[[str], Any]:
    """The ch() path resolver for the node whose *params* an impl was given
    (``params.node_id`` and ``params.env``).  Code blocks and Wrangles pass
    it to ``code.run(resolve=...)``.  *plan* defaults to the plan the cook
    was built with; *own* is the stream the code runs on (see
    ParamPlan.resolver)."""
    env = getattr(params, "env", None) or {}
    scope = env.get(SCOPE_KEY)
    plan = plan if plan is not None else getattr(scope, "plan", None)
    if plan is None:
        def broken(path: str) -> Any:
            raise KeyError(path)
        return broken
    return plan.resolver(params.node_id, scope, own)


# ---------------------------------------------------------------------------
# Compile-time values of param-only expressions (window sizing)
# ---------------------------------------------------------------------------

COMPILE_EVAL_TIMEOUT_S = 2.0
"""Longest compile waits for the param-only expressions it evaluates to size
windows.  Past it (or with both compile_pool threads held by leaked
evaluations) compile keeps the widest-value rule and the cook's own guard
reports the timeout."""

_STATIC_BUILTINS = frozenset({
    "abs", "min", "max", "round", "int", "float", "bool", "str", "len", "divmod",
})
_STATIC_NP = frozenset({
    "clip", "floor", "ceil", "round", "trunc", "sqrt", "log", "log2", "log10", "exp", "abs",
    "minimum", "maximum", "sign", "isnan", "isfinite", "nan", "inf", "pi", "e",
    "int64", "float64",
})


def _static_names() -> frozenset[str]:
    cached = _static_names.__dict__.get("names")
    if cached is None:
        import math as _math

        from nodebuilder.code.promote import CH_FUNCS

        cached = (frozenset(CH_FUNCS) | _STATIC_BUILTINS | _STATIC_NP | {"math", "np"}
                  | {n for n in dir(_math) if not n.startswith("_")})
        _static_names.__dict__["names"] = cached
    return cached


def _only_static_names(code: Any) -> bool:
    """True when every global and attribute name the compiled expression
    uses (nested code objects too) is a ch*() function, a plain builtin or a
    math/numpy function with no state.  Anything else (``stream``, an
    ``@attr``, ``np.random``, ``__import__``, a walrus target) is not known
    before the cook, or may differ between two runs."""
    allowed = _static_names()
    stack = [code]
    while stack:
        co = stack.pop()
        if any(name not in allowed for name in co.co_names):
            return False
        stack.extend(c for c in co.co_consts if hasattr(c, "co_names"))
    return True


def static_windows(plan: ParamPlan) -> list:
    """Size the windows of nodes whose window params hold param-only
    expressions from their exact values, and return the cook errors those
    expressions give.

    An expression that reads only params (literals, ch*() of plain params,
    the node's own spare params, promoted params, or of other param-only
    expressions) and no stream data (no ``@attr``, no detail attribute) has
    the same value at every cook.  For each node with a lookback, compile
    evaluates them here with the cook's own code (``_evaluate_values``: the
    same runtime, resolver and recheck), and the node's lookback counts that
    value instead of the param's widest one (``schema.own_lookback``).  An
    expression that reads data keeps the widest-value rule, and the cook's
    check (``ParamPlan._check_window``) stays for it.

    Returns the CodeErrors (ids the user sees) of nodes whose expressions
    all read only params and fail: the cook raises the same error.  The
    analysis' ``lookback`` and ``need`` are updated in place.  Runs nothing
    when code is switched off (SL_CODE_NODES=0).  The code runs on a guarded
    thread of its own pool (``code.runtime.compile_pool``, limit
    ``COMPILE_EVAL_TIMEOUT_S``); a timeout or a full pool keeps the
    widest-value sizing and reports nothing (the cook's own guard does).
    """
    from nodebuilder.code import code_enabled
    from nodebuilder.code import runtime as _runtime

    if plan.analysis is None or not plan.expr_order or not code_enabled():
        return []
    candidates = _window_candidates(plan)
    if not candidates:
        return []
    try:
        if _runtime.current_guard() is not None:
            # Already on a guarded thread (a guarded cook compiles): its limit holds.
            values, errors = _static_values(plan, candidates)
        else:
            values, errors = _runtime.call_guarded(_static_values, plan, candidates,
                                                   timeout_s=COMPILE_EVAL_TIMEOUT_S,
                                                   label="compile",
                                                   executor=_runtime.compile_pool())
    except _runtime.CodeTimeout:
        return []
    _apply_windows(plan, values, candidates)
    return errors


def _refs_by_snippet(plan: ParamPlan) -> dict[tuple[str, Optional[str]], list[Ref]]:
    """(reader flat id, reader param) -> its references."""
    out: dict[tuple[str, Optional[str]], list[Ref]] = {}
    for r in plan.refs:
        out.setdefault((r.reader_id, r.reader_param), []).append(r)
    return out


class _Purity:
    """Which expressions read only params (see static_windows), memoized."""

    def __init__(self, plan: ParamPlan) -> None:
        self.plan = plan
        self.refs = _refs_by_snippet(plan)
        self.memo: dict[tuple[str, Optional[str]], bool] = {}
        self.visiting: set[tuple[str, Optional[str]]] = set()

    def snippet(self, nid: str, p: str) -> bool:
        """True when expression *p* of node *nid* reads only params."""
        return self._memo((nid, p), lambda: self._snippet(nid, p))

    def node(self, nid: str) -> bool:
        """True when node *nid* cooks and every expression on it reads only
        params: compile can hand its evaluated params to a reader."""
        def check() -> bool:
            plan = self.plan
            res = plan.analysis.nodes.get(nid)
            exprs = plan.expr_order.get(nid, ())
            return (res is not None and res.status == "run" and nid not in plan._cyclic
                    and bool(exprs) and all(self.snippet(nid, p) for p in exprs))
        return self._memo((nid, None), check)

    def _memo(self, key: tuple[str, Optional[str]], check: Callable[[], bool]) -> bool:
        if key in self.memo:
            return self.memo[key]
        if key in self.visiting:
            return False  # a loop: ch_cycle reports it, nothing is evaluated
        self.visiting.add(key)
        try:
            ok = check()
        finally:
            self.visiting.discard(key)
        self.memo[key] = ok
        return ok

    def _snippet(self, nid: str, p: str) -> bool:
        snip = self.plan.snippets.get((nid, p))
        if snip is None or snip.context != "expr":
            return False
        prepared = snip.prepared
        if not prepared.ok or prepared.reads or not _only_static_names(prepared.code_obj):
            return False
        for r in self.refs.get((nid, p), ()):
            if r.problem is not None or r.target_id is None or r.kind == ATTR:
                return False
            if r.dyn is None:
                continue  # a plain value
            fid, q = r.dyn
            if not (self.snippet(nid, q) if fid == nid else self.node(fid)):
                return False
        return True


def _window_candidates(plan: ParamPlan) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """flat id -> (param-only expressions, the others) for every running
    node with a lookback that has at least one param-only expression."""
    purity = _Purity(plan)
    out: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for nid in plan.analysis.order:
        exprs = plan.expr_order.get(nid)
        res = plan.analysis.nodes.get(nid)
        nt = getattr(res, "node_type", None)
        if (not exprs or res is None or res.status != "run" or nid in plan._cyclic
                or nt is None or nt.lookback is None):
            continue
        pure = tuple(p for p in exprs if purity.snippet(nid, p))
        if pure:
            out[nid] = (pure, tuple(p for p in exprs if p not in pure))
    return out


def _static_values(plan: ParamPlan, candidates: Mapping[str, tuple]) -> tuple[dict, list]:
    """Evaluate the candidates' param-only expressions (and the param-only
    nodes they read) as the cook would.  Returns ``(values, errors)``:
    flat id -> checked params, and the CodeErrors of nodes whose
    expressions all read only params (the cook raises the same).  A node
    that also has expressions reading data is evaluated in part; a problem
    there only keeps the widest-value rule (another expression of the node
    may fail first at the cook).  A timeout propagates."""
    from nodebuilder.code import CodeError, CodeTimeout

    scope = ParamScope({}, plan)
    refs = _refs_by_snippet(plan)
    full: dict[str, Any] = {}       # flat id -> checked params, None when it failed
    errors: list = []
    values: dict[str, Any] = {}

    def deps_ok(nid: str, exprs: Iterable[str]) -> bool:
        return all(evaluate_full(r.dyn[0]) is not None
                   for p in exprs for r in refs.get((nid, p), ())
                   if r.dyn is not None and r.dyn[0] != nid)

    def evaluate(nid: str, only: Optional[tuple[str, ...]] = None) -> Any:
        node = plan.flat.graph.nodes[nid]
        res = plan.analysis.nodes[nid]
        return plan._evaluate_values(node, res, plan.expr_order[nid], None, res.params, scope,
                                     only=only)

    def evaluate_full(fid: str) -> Any:
        if fid in full:
            return full[fid]
        full[fid] = None
        if not deps_ok(fid, plan.expr_order.get(fid, ())):
            return None
        try:
            checked = evaluate(fid)
        except CodeTimeout:
            raise
        except CodeError as exc:
            errors.append(exc)
            return None
        except Exception:  # noqa: BLE001  (the cook reports it; compile never 500s)
            return None
        scope.set_params(fid, checked)
        full[fid] = checked
        return checked

    for nid, (pure, rest) in candidates.items():
        if not rest:
            got = evaluate_full(nid)
            if got is not None:
                values[nid] = got
            continue
        if not deps_ok(nid, pure):
            continue
        try:
            values[nid] = evaluate(nid, only=pure)
        except CodeTimeout:
            raise
        except Exception:  # noqa: BLE001  (see the docstring)
            continue
    return values, errors


def _apply_windows(plan: ParamPlan, values: Mapping[str, Any],
                   candidates: Mapping[str, tuple]) -> None:
    """Each evaluated node's lookback from its exact values (its other
    expressions at their widest), then every ``need`` again."""
    from nodebuilder.kernel.schema import own_lookback

    nodes = plan.analysis.nodes
    changed = False
    for nid, checked in values.items():
        res = nodes[nid]
        try:
            own = own_lookback(res.node_type, checked, candidates[nid][1])
        except (TypeError, ValueError, KeyError, OverflowError):
            continue
        if res.extra is not None:
            own += max(int(res.extra.lookback), 0)
        if own != res.lookback:
            res.lookback = own
            changed = True
    if not changed:
        return
    for nid in plan.analysis.order:
        res = nodes.get(nid)
        if res is None:
            continue
        if res.status == "run":
            res.need = res.lookback + max((nodes[u].need for u in res.inputs if u in nodes),
                                          default=0)
        elif res.status == "pass":
            res.need = nodes[res.pass_from].need if res.pass_from in nodes else 0


# ---------------------------------------------------------------------------
# Building the plan
# ---------------------------------------------------------------------------


def code_context(node: Any) -> str:
    """The prepare() context of a node's code: ``wrangle`` for a Wrangle
    node (the code is the node), else ``node_code``."""
    return "wrangle" if getattr(node, "type", None) == WRANGLE_TYPE else "node_code"


def plan_params(flat: Any, analysis: Any = None) -> ParamPlan:
    """Read every snippet of the flat graph's nodes and build the plan.

    *flat* is the kernel.flatten.FlatGraph (its ``source`` is the graph the
    paths resolve against).  *analysis* is the kernel check of ``flat.graph``
    (schema.analyze); without it, attribute targets and node states are not
    checked and no hook can be built.  Never raises for bad user code: a
    snippet that does not parse simply has no references (its own
    diagnostics come from prepare()).
    """
    from nodebuilder.code import prepare

    plan = ParamPlan(flat=flat, analysis=analysis)
    src = plan._source
    fnodes = flat.graph.nodes
    children = child_index(src.nodes)
    raw: list = []

    def _diag(code: str, message: str, nid: str, param: Optional[str] = None,
              ref: Optional[Ref] = None) -> None:
        exc = GraphValidationError(message, node_id=nid)
        exc.code = code  # type: ignore[attr-defined]
        exc.param = param  # type: ignore[attr-defined]
        for key in ("line", "col", "end_line", "end_col"):
            setattr(exc, key, getattr(ref, key, None) if ref is not None else None)
        raw.append(exc)

    # 1. Code on network and boundary nodes never runs (flatten takes those
    # nodes out), and their params are read before the cook.  A param that
    # can never hold code (an Output Group's direction, ticker and weight)
    # gets the shared param_not_codeable rule.
    from nodebuilder.kernel.schema import READ_BEFORE_COOK

    for nid, node in list(flat.networks.items()) + list(flat.boundaries.items()):
        nt = _registry.get(node.type)
        for pname, value in (node.params or {}).items():
            if not is_expr_value(value):
                continue
            spec = nt.param(pname) if nt is not None else None
            if spec is not None and not spec.code_able:
                _diag(PARAM_NOT_CODEABLE,
                      f"{node.type} {node.name!r} param {pname!r}: {READ_BEFORE_COOK}.", nid, pname)
            else:
                _diag(PARAM_INVALID,
                      f"{node.type} {node.name!r} param {pname!r}: a network's params are read "
                      "before the cook, so they cannot hold code yet.  Put the expression on "
                      "the param inside the network.", nid, pname)

    # 2. Every snippet on a node that cooks.
    for nid, node in fnodes.items():
        code = getattr(node, "code", None)
        if isinstance(code, str) and code.strip():
            ctx = code_context(node)
            snip = Snippet(nid, None, ctx, prepare(code, ctx, node, node_id=nid))
            plan.snippets[(nid, None)] = snip
            _check_spare_values(node, snip.prepared, _diag)
        own = (src.nodes[nid].params or {}) if nid in src.nodes else {}
        for pname, value in (node.params or {}).items():
            text = expr_text(value)
            if text is None:
                continue
            if (nid, pname) in flat.promoted_from and own.get(pname) != value:
                _promoted_expr(plan, node, pname, _diag)
                continue
            spare = plan._spare_spec(nid, pname)
            if spare is not None and spare.type == "vector":
                _diag(PARAM_INVALID,
                      f"{node.type} {node.name or nid!r} param {pname!r} is a vector of 2 to 4 "
                      "numbers, which an expression cannot give yet.  Use plain numbers.",
                      nid, pname)
                continue
            plan.snippets[(nid, pname)] = Snippet(
                nid, pname, "expr", prepare(text, "expr", node, node_id=nid, param=pname))

    # 3. References.
    for (nid, pname), snip in plan.snippets.items():
        for cr in snip.prepared.refs:
            ref = _build_ref(plan, nid, pname, cr, children)
            plan.refs.append(ref)
            plan._by_reader.setdefault((nid, cr.target), ref)
            if ref.problem is not None:
                _diag(ref.problem_code or REF_BROKEN, ref.problem, nid, pname, ref)

    # 4. Loops, the cook order and the expression order.
    _order(plan, _diag)

    plan.found = flat.remap([(_to_diag(e), e) for e in raw]) if raw else []
    return plan


def _to_diag(exc: Any) -> Any:
    from nodebuilder.diagnostics import from_error
    return from_error(exc)


def _check_spare_values(node: Any, prepared: Any, diag: Callable[..., None]) -> None:
    """param_invalid / param_out_of_range for a stored value of a spare param
    of the node's code that does not fit its spec (type, min, max, finite):
    refused at compile, never a cook failure or a 500.  An expression is
    checked when the cook evaluates it."""
    stored = node.params or {}
    for spec in prepared.spare_params:
        value = stored.get(spec.name)
        if value is None or is_expr_value(value):
            continue
        problem = spare_value_problem(spec, value)
        if problem is not None:
            diag(problem[0], f"{node.type} {node.name or node.id!r}: {problem[1]}", node.id,
                 spec.name)


def _promoted_expr(plan: ParamPlan, node: Any, pname: str, diag: Callable[..., None]) -> None:
    """A target param whose expression came from a network's promoted param.
    A network's own value that is an expression is reported in step 1; one
    that came from the promoted param's default is reported here (flat.remap
    moves it to the promoted param on the network), with the target's rule:
    param_not_codeable when the target is read before the cook, else
    param_invalid (a network's params cannot hold code yet)."""
    from nodebuilder.kernel.schema import READ_BEFORE_COOK

    flat = plan.flat
    owner, oname = flat.promoted_owner(node.id, pname)
    net = flat.networks.get(owner)
    if net is None or is_expr_value((net.params or {}).get(oname)):
        return  # step 1 reported the network's own value
    nt = _registry.get(node.type)
    spec = nt.param(pname) if nt is not None else None
    where = (f"it gets an expression from the default of promoted param {oname!r} on "
             f"{plan._name(owner)!r}")
    if spec is not None and not spec.code_able:
        diag(PARAM_NOT_CODEABLE,
             f"{node.type} {node.name or node.id!r} param {pname!r}: {READ_BEFORE_COOK}; "
             f"{where}.  Give the promoted param a plain default.", node.id, pname)
        return
    diag(PARAM_INVALID,
         f"{node.type} {node.name or node.id!r} param {pname!r}: {where}, and a network's params "
         "are read before the cook, so they cannot hold code yet.  Give the promoted param a "
         "plain default, or put the expression on the param inside the network.", node.id, pname)


def _build_ref(plan: ParamPlan, nid: str, pname: Optional[str], cr: Any,
               children: Mapping) -> Ref:
    flat = plan.flat
    src = plan._source
    where = dict(line=cr.line, col=cr.col, end_line=cr.end_line, end_col=cr.end_col)
    base = dict(reader_id=nid, reader_param=pname, func=cr.func, path=cr.target, **where)
    if not cr.is_path:
        dyn, _v = plan._static(nid, cr.target)
        return Ref(kind=OWN, target_id=nid, target=cr.target, dyn=dyn, **base)
    found = resolve_path(src.nodes, nid, cr.target, children)
    if found is None:
        return Ref(kind=PARAM, target_id=None, target=cr.target.rpartition("/")[2],
                   problem=f"{cr.func}({cr.target!r}) points at no node.  Paths start at "
                           "this node: '../other/param' is a sibling's param, '../name' a "
                           "param of the network around it, '/a/b/@x' starts at the root.",
                   **base)
    tid, target = found
    tnode = src.nodes[tid]
    tname = tnode.name or tid
    if target.startswith("@"):
        stream_id, problem = _attr_source(plan, tid, target)
        if problem is not None:
            return Ref(kind=ATTR, target_id=tid, target=target,
                       problem=f"{cr.func}({cr.target!r}): {problem}", **base)
        if pname is not None and _is_point(plan, stream_id, target):
            return Ref(kind=ATTR, target_id=tid, target=target, stream=stream_id,
                       problem=f"{cr.func}({cr.target!r}): {target} is a per-bar attribute; "
                               "parameter expressions read detail attributes only.  Use a "
                               "Wrangle for per-bar logic.",
                       problem_code=CODE_TYPE, **base)
        return Ref(kind=ATTR, target_id=tid, target=target, stream=stream_id, **base)
    if not _has_param(plan, tid, target):
        return Ref(kind=PARAM, target_id=tid, target=target,
                   problem=f"{cr.func}({cr.target!r}): {tname!r} has no param {target!r}.", **base)
    dyn, _value = plan._static(tid, target)
    if dyn is not None and plan.analysis is not None:
        res = plan.analysis.nodes.get(dyn[0])
        if res is not None and res.status not in ("run", "broken", "skipped"):
            return Ref(kind=PARAM, target_id=tid, target=target,
                       problem=f"{cr.func}({cr.target!r}): {plan._name(dyn[0])!r} does not cook "
                               f"(it is {res.off_reason or res.status}), so the expression on "
                               f"{target} has no value.", **base)
    if dyn is not None and dyn[0] not in flat.graph.nodes:
        return Ref(kind=PARAM, target_id=tid, target=target,
                   problem=f"{cr.func}({cr.target!r}): {plan._name(dyn[0])!r} is switched off, so "
                           f"the expression on {target} has no value.", **base)
    return Ref(kind=PARAM, target_id=tid, target=target, dyn=dyn, **base)


def _is_point(plan: ParamPlan, stream_id: Optional[str], target: str) -> bool:
    """True when *target* is surely a per-bar (point) attribute on
    *stream_id*'s output.  A write of a node's code is not: compile lists
    it as a point, but the code may write one value (a detail), which an
    expression can read; the cook decides."""
    if plan.analysis is None or stream_id is None:
        return False
    res = plan.analysis.nodes.get(stream_id)
    schema = getattr(res, "out_schema", None)
    info = schema.points.get(target) if schema is not None else None
    if info is None:
        return False
    writer = plan.analysis.nodes.get(info.written_by) if info.written_by else None
    extra = getattr(writer, "extra", None)
    if extra is not None and any(name == target for name, _d in extra.writes):
        return False
    return True


def _own_output(plan: ParamPlan, r: Ref) -> bool:
    """True when code block *r* reads one of its own node's outputs (one the
    node type writes, not its code) through a path.  The code block runs
    after the node's own compute, on its output stream, so this is no
    loop; the resolver reads it from that stream."""
    if r.reader_param is not None or plan.analysis is None:
        return False
    node = plan.flat.graph.nodes.get(r.reader_id)
    if node is None or node.type == WRANGLE_TYPE:
        return False
    res = plan.analysis.nodes.get(r.reader_id)
    nt = getattr(res, "node_type", None)
    if res is None or nt is None:
        return False
    own = set(res.write_names.values()) | {n for n, _d in nt.fixed_writes_for(res.params)}
    return r.target in own


def _attr_source(plan: ParamPlan, tid: str, target: str) -> tuple[Optional[str], Optional[str]]:
    """The flat node whose output stream holds node *tid*'s ``@attr``, or
    the reason there is none."""
    flat = plan.flat
    name = plan._name(tid)
    if tid in flat.networks:
        stream_id = flat.outputs.get(tid)
        if stream_id is None:
            return None, f"network {name!r} has no output."
    elif tid in flat.boundaries:
        node = flat.boundaries[tid]
        port = (node.params or {}).get("port", 0)
        try:
            k = int(port)
        except (TypeError, ValueError):
            k = -1
        stream_id = flat.inputs.get(node.parent, {}).get(k)
        if stream_id is None:
            return None, f"{name!r} is not fed by anything."
    elif tid in flat.graph.nodes:
        stream_id = tid
    else:
        return None, f"{name!r} is inside a bypassed network, so it does not cook."
    if plan.analysis is not None:
        res = plan.analysis.nodes.get(stream_id)
        if res is not None and res.status in ("broken", "skipped"):
            return stream_id, None  # its own error says more
        if res is None or res.status == "inactive" or res.out_schema is None:
            return None, f"{name!r} has no output stream."
        schema = res.out_schema
        if target not in schema.points and target not in schema.detail:
            return None, f"{name!r} does not write {target}."
    return stream_id, None


def _order(plan: ParamPlan, diag: Callable[..., None]) -> None:
    """ch_cycle for loops; fill ``after``, ``attr_reads`` and ``expr_order``."""
    flat = plan.flat
    fnodes = flat.graph.nodes

    # Within a node: expression p reads the node's own expression q.
    inner: dict[str, dict[str, set[str]]] = {}
    for (nid, pname), snip in plan.snippets.items():
        if pname is None:
            continue
        deps = inner.setdefault(nid, {}).setdefault(pname, set())
        for r in plan.refs:
            if r.reader_id == nid and r.reader_param == pname and r.dyn is not None \
                    and r.dyn[0] == nid and r.problem is None:
                deps.add(r.dyn[1])
    for nid, graph in inner.items():
        loops = _strongly_connected(graph)
        bad = {p for comp in loops for p in comp}
        for comp in loops:
            names = " -> ".join(sorted(comp))
            for p in sorted(comp):
                diag(CH_CYCLE, f"{plan._name(nid)!r} params read each other in a loop ({names}).  "
                               "An expression cannot read a value that depends on itself.", nid, p)
        if bad:
            plan._cyclic.add(nid)
        order = _topo(graph, [p for p in (fnodes[nid].params or {}) if p in graph])
        plan.expr_order[nid] = tuple(p for p in order if p not in bad)

    # Between nodes: the wires, plus an edge from each node a reference
    # needs cooked first.
    edges: dict[str, set[str]] = {nid: set() for nid in fnodes}
    for w in flat.graph.wires:
        if w.from_path in edges and w.to_path in edges:
            edges[w.from_path].add(w.to_path)
    need: dict[str, set[str]] = {}
    reads: dict[str, list] = {}
    self_reads: dict[str, str] = {}
    for r in plan.refs:
        if r.problem is not None:
            continue
        first = r.stream if r.kind == ATTR else (r.dyn[0] if r.dyn is not None else None)
        if first is None or first not in edges or r.reader_id not in edges:
            continue
        if first == r.reader_id:
            if r.kind == ATTR and not _own_output(plan, r):
                # A node cannot read its own output before it cooks (an
                # expression on its param, or a Wrangle: use @name there).
                edges[first].add(r.reader_id)
                self_reads.setdefault(r.reader_id, r.target)
            continue
        edges[first].add(r.reader_id)
        need.setdefault(r.reader_id, set()).add(first)
        if r.kind == ATTR and plan.analysis is not None:
            res = plan.analysis.nodes.get(first)
            info = res.out_schema.lookup(r.target) if (res is not None and res.out_schema) else None
            if info is not None and info.kind == "point":
                reads.setdefault(r.reader_id, []).append((info.written_by, r.target))
    loops = _strongly_connected({k: v for k, v in edges.items()})
    for comp in loops:
        names = " -> ".join(sorted(plan._name(n) for n in comp))
        only = next(iter(comp)) if len(comp) == 1 else None
        for nid in sorted(comp):
            plan._cyclic.add(nid)
            if only is not None and only in self_reads:
                attr = self_reads[only]
                diag(CH_CYCLE, f"{plan._name(only)!r} reads its own {attr} through a ch() path, "
                               "which needs the node cooked before it runs.  In the node's code "
                               f"read it as {attr} (the @name sugar) instead.", nid)
                continue
            diag(CH_CYCLE, f"ch() references and wires form a loop ({names}).  A node cannot "
                           "read a value that depends on its own result.", nid)
    for reader, firsts in need.items():
        if reader in plan._cyclic:
            continue
        plan.after[reader] = tuple(sorted(f for f in firsts if f not in plan._cyclic))
    for reader, cols in reads.items():
        plan.attr_reads[reader] = tuple(dict.fromkeys(cols))


def _strongly_connected(graph: Mapping[str, Iterable[str]]) -> list[set[str]]:
    """The loops of *graph*: strongly connected parts with more than one
    vertex, or one vertex with an edge to itself (Tarjan, iterative)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    out: list[set[str]] = []
    counter = 0
    for start in sorted(graph):
        if start in index:
            continue
        work = [(start, iter(sorted(graph.get(start, ()))))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on.add(start)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in graph:
                    continue
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(graph.get(w, ())))))
                    advanced = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
            if low[v] == index[v]:
                comp: set[str] = set()
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.add(w)
                    if w == v:
                        break
                if len(comp) > 1 or v in graph.get(v, ()):
                    out.append(comp)
    return out


def _topo(graph: Mapping[str, Iterable[str]], order: list[str]) -> list[str]:
    """*order* rearranged so each vertex comes after the ones it depends on
    (graph[v] = what v depends on).  Vertices in loops keep their place."""
    done: set[str] = set()
    out: list[str] = []
    visiting: set[str] = set()

    def visit(v: str) -> None:
        if v in done or v in visiting:
            return
        visiting.add(v)
        for d in sorted(graph.get(v, ())):
            if d in graph:
                visit(d)
        visiting.discard(v)
        done.add(v)
        out.append(v)

    for v in order:
        visit(v)
    for v in sorted(graph):
        visit(v)
    return out

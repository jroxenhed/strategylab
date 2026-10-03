"""The static scan of user code (design note 4.3).  It reads the tree only;
it never runs it.

``scan(tree, ...)`` finds:

- every ``ch``, ``chf``, ``chi``, ``chs``, ``chb`` and ``chv`` call by bare
  name.  The name (first argument or ``name=``) and the keywords
  ``default``, ``min``, ``max``, ``options`` and ``label`` must be
  literals, else ``ch_dynamic``.
- spare params: a typed call (``chf`` float, ``chi`` int, ``chs`` string,
  ``chb`` bool, ``chv`` vector) on a bare name that is not a built-in param
  of the node declares one.  The first call defines the spec; a later call
  with another type, or with other keywords, is ``code_type``.  A later
  call with no keywords, or an untyped ``ch()``, just reads it.  An untyped
  ``ch()`` on a name nothing declares is ``ref_broken``.
- references: a name with ``/`` in it is a path to another node's param or
  attribute (``../vol/threshold``, ``../vol/@atr_pct``, ``/shared/x``).
  Item 7.B resolves them; the scan only lists them.
- attribute reads and writes: ``stream["literal"]`` loads and stores,
  whether the ``@name`` sugar wrote them or the user did.  A store with a
  non-literal key is ``attr_dynamic``.  ``@x: bool = ...`` or
  ``@x: float = ...`` fixes the write's dtype; without one it is ``any``.
- the reserved name ``stream``: binding it anywhere is ``code_syntax``.

Positions in the tree are in the rewritten text, in UTF-8 bytes; the scan
reports them in the user's text, in characters (``SugarResult``).
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from nodebuilder.code.errors import (
    ATTR_DYNAMIC,
    CH_DYNAMIC,
    CODE_SYNTAX,
    CODE_TYPE,
    REF_BROKEN,
    CodeDiagnostic,
)
from nodebuilder.code.sugar import ATTR_RE, SugarResult

CH_FUNCS: tuple[str, ...] = ("ch", "chf", "chi", "chs", "chb", "chv")
"""The channel functions, by bare name."""

CH_TYPES: dict[str, str] = {
    "chf": "float", "chi": "int", "chs": "string", "chb": "bool", "chv": "vector",
}
"""The spare-param type each typed channel function declares."""

CH_KEYWORDS: tuple[str, ...] = ("default", "min", "max", "options", "label")

PARAM_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
"""A spare param name: lowercase letters, digits and _."""

RESERVED = "stream"

LOOKBACK_PARAM = "lookback_bars"
LOOKBACK_DEFAULT = 500
LOOKBACK_MIN = 1
LOOKBACK_MAX = 100_000

WRITE_DTYPES: tuple[str, ...] = ("bool", "float")
"""Annotations that fix a written attribute's dtype."""

_TYPE_DEFAULTS: dict[str, Any] = {
    "float": 0.0, "int": 0, "string": "", "bool": False, "vector": [0.0, 0.0, 0.0],
}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpareParam:
    """One auto-promoted param (Node.spare_params).  Values live in
    ``Node.params[name]``; on first appearance the value is ``default``."""
    name: str
    type: str                       # float | int | string | bool | vector
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    options: Optional[tuple[str, ...]] = None
    label: str = ""

    def to_json(self) -> dict[str, Any]:
        """The parse_code ``params`` shape (frontend SpareParamSpec)."""
        default = list(self.default) if isinstance(self.default, tuple) else self.default
        return {
            "name": self.name, "type": self.type, "default": default,
            "min": self.min, "max": self.max,
            "label": self.label or self.name,
            "options": list(self.options) if self.options is not None else None,
        }


def lookback_spec() -> SpareParam:
    """The ``lookback_bars`` param every code-bearing node has (4.3)."""
    return SpareParam(LOOKBACK_PARAM, "int", LOOKBACK_DEFAULT, LOOKBACK_MIN, LOOKBACK_MAX,
                      None, "lookback bars")


@dataclass(frozen=True)
class ChRef:
    """One literal ``ch*()`` call.

    kind: ``builtin`` (a bare name the node has as a built-in param),
    ``spare`` (a bare name this code declares or reads as a spare param) or
    ``path`` (a name with ``/``: another node's param or ``@attr``, or a
    param of the enclosing network).  Positions are in the user's text.
    """
    func: str
    target: str
    kind: str
    line: Optional[int] = None
    col: Optional[int] = None
    end_line: Optional[int] = None
    end_col: Optional[int] = None

    @property
    def is_path(self) -> bool:
        return self.kind == "path"


@dataclass
class ScanResult:
    spare_params: list[SpareParam] = field(default_factory=list)
    refs: list[ChRef] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    write_dtypes: dict[str, str] = field(default_factory=dict)
    diagnostics: list[CodeDiagnostic] = field(default_factory=list)
    # name -> (line, col, end_line, end_col) of its first write, in the
    # user's text.
    write_positions: dict[str, tuple] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------


def scan(tree: ast.AST, sugar: SugarResult, *, builtin_params: Iterable[str] = (),
         add_lookback: bool = False) -> ScanResult:
    """Scan *tree* (parsed from ``sugar.text``).

    builtin_params: the node type's own param names; a bare ``ch*()`` on one
    of them reads it and declares nothing.
    add_lookback: put the ``lookback_bars`` spec first in ``spare_params``
    unless the code declares it or it is a built-in param (a Wrangle or a
    node with a code block).
    """
    s = _Scanner(sugar, frozenset(builtin_params))
    s.visit(tree)
    return s.finish(add_lookback)


@dataclass
class _Decl:
    """The first typed call that declared a spare param."""
    func: str
    keywords: dict[str, Any]
    spec: SpareParam
    line: Optional[int]


class _Scanner(ast.NodeVisitor):
    def __init__(self, sugar: SugarResult, builtins: frozenset[str]) -> None:
        self.sugar = sugar
        self.builtins = builtins
        self.result = ScanResult()
        self.decls: dict[str, _Decl] = {}
        self.untyped: list[tuple[str, ast.AST]] = []    # ch("x") reads, checked at the end
        self.reads: dict[str, tuple[int, int]] = {}
        self.writes: dict[str, tuple[int, int]] = {}

    # --- positions --------------------------------------------------------

    def pos(self, node: ast.AST) -> dict[str, Optional[int]]:
        line = getattr(node, "lineno", None)
        end_line = getattr(node, "end_lineno", None)
        col = self.sugar.user_position(line, getattr(node, "col_offset", None), "byte")
        end_col = self.sugar.user_position(end_line, getattr(node, "end_col_offset", None), "byte")
        return {"line": line, "col": col, "end_line": end_line, "end_col": end_col}

    def err(self, code: str, message: str, node: ast.AST) -> None:
        self.result.diagnostics.append(CodeDiagnostic(code, message, **self.pos(node)))

    # --- the reserved name ------------------------------------------------

    def _reserved(self, name: Optional[str], node: ast.AST) -> None:
        if name == RESERVED:
            self.err(CODE_SYNTAX,
                     "stream is reserved for the attribute stream (@name); pick another name.",
                     node)

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            self._reserved(node.id, node)

    def _def(self, node) -> None:
        self._reserved(node.name, node)
        self.generic_visit(node)

    visit_FunctionDef = _def
    visit_AsyncFunctionDef = _def
    visit_ClassDef = _def

    def visit_arg(self, node: ast.arg) -> None:
        self._reserved(node.arg, node)
        self.generic_visit(node)

    def visit_alias(self, node: ast.alias) -> None:
        bound = node.asname or node.name.split(".")[0]
        self._reserved(bound, node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._reserved(node.name, node)
        self.generic_visit(node)

    def _names(self, node) -> None:
        for name in node.names:
            self._reserved(name, node)

    visit_Global = _names
    visit_Nonlocal = _names

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        self._reserved(node.name, node)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        self._reserved(node.name, node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        self._reserved(node.rest, node)
        self.generic_visit(node)

    def _type_param(self, node) -> None:
        # def f[stream]() / class C[*stream] / type A[**stream] = ... (PEP 695)
        self._reserved(getattr(node, "name", None), node)
        self.generic_visit(node)

    visit_TypeVar = _type_param
    visit_ParamSpec = _type_param
    visit_TypeVarTuple = _type_param

    # --- attribute reads and writes --------------------------------------

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.value, ast.Name) and node.value.id == RESERVED:
            self._stream_item(node, node.ctx)
            self.visit(node.slice)
            return
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        # stream["x"] += 1 reads x as well as writing it.
        t = node.target
        if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == RESERVED:
            self._stream_item(t, ast.Load())
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        t = node.target
        if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == RESERVED:
            name = _literal_key(t.slice)
            dtype = _annotation_dtype(node.annotation)
            if dtype is None:
                self.err(CODE_TYPE,
                         "Annotate a written attribute with bool or float, for example "
                         "@signal: bool = ...", node.annotation)
            elif name is not None:
                prev = self.result.write_dtypes.get(name)
                if prev is not None and prev != dtype:
                    self.err(CODE_TYPE,
                             f"@{name} is annotated both {prev} and {dtype}.", node.annotation)
                else:
                    self.result.write_dtypes[name] = dtype
            if node.value is None and name is not None:
                # A bare annotation declares the write.
                self._note(self.writes, name, t)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in CH_FUNCS:
            self._ch(node, func.id)
        elif (isinstance(func, ast.Attribute) and func.attr == "get"
              and isinstance(func.value, ast.Name) and func.value.id == RESERVED and node.args):
            name = _literal_key(node.args[0])
            if name is not None:
                self._note(self.reads, name, node)
        self.generic_visit(node)

    def _stream_item(self, node: ast.Subscript, ctx: ast.expr_context) -> None:
        name = _literal_key(node.slice)
        if isinstance(ctx, ast.Store):
            if name is None:
                self.err(ATTR_DYNAMIC,
                         "Write attributes with a literal name (@name = ...) so later nodes "
                         "can see them before the cook.", node)
                return
            if not ATTR_RE.match(name):
                self.err(CODE_SYNTAX,
                         f"{name!r} is not an attribute name: use lowercase letters, digits "
                         "and _ (at most 64), starting with a letter or _.", node)
                return
            self._note(self.writes, name, node)
        elif isinstance(ctx, ast.Load) and name is not None:
            self._note(self.reads, name, node)

    def _note(self, table: dict[str, tuple[int, int]], name: str, node: ast.AST) -> None:
        where = (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))
        if name not in table or where < table[name]:
            table[name] = where
            if table is self.writes:
                p = self.pos(node)
                self.result.write_positions[name] = (p["line"], p["col"], p["end_line"],
                                                     p["end_col"])

    # --- ch*() calls ------------------------------------------------------

    def _ch(self, node: ast.Call, func: str) -> None:
        dynamic = ("ch() names must be literal strings, so parameters and their links are "
                   "known before the code runs.")
        if any(isinstance(a, ast.Starred) for a in node.args) or any(
                k.arg is None for k in node.keywords):
            self.err(CH_DYNAMIC, dynamic, node)
            return
        name_node: Optional[ast.AST] = node.args[0] if node.args else None
        keywords: dict[str, Any] = {}
        kw_nodes: dict[str, ast.AST] = {}
        for k in node.keywords:
            if k.arg == "name":
                if name_node is not None:
                    self.err(CODE_TYPE, f"{func}() got the name twice.", node)
                    return
                name_node = k.value
                continue
            if k.arg not in CH_KEYWORDS:
                self.err(CODE_TYPE,
                         f"{func}() takes name, default, min, max, options and label; "
                         f"not {k.arg}.", k)
                return
            kw_nodes[k.arg] = k.value
        if len(node.args) > 2:
            self.err(CODE_TYPE, f"{func}() takes the name and at most one default by position.",
                     node)
            return
        if len(node.args) == 2:
            if "default" in kw_nodes:
                self.err(CODE_TYPE, f"{func}() got the default twice.", node)
                return
            kw_nodes["default"] = node.args[1]
        if not (isinstance(name_node, ast.Constant) and isinstance(name_node.value, str)):
            self.err(CH_DYNAMIC, dynamic, node)
            return
        for key, value_node in kw_nodes.items():
            try:
                keywords[key] = ast.literal_eval(value_node)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                self.err(CH_DYNAMIC,
                         f"{func}() {key}= must be a literal value (a number, string, bool "
                         "or list of them), so the parameter is known before the code runs.",
                         value_node)
                return
        name = name_node.value
        where = self.pos(node)
        if "/" in name:
            self.result.refs.append(ChRef(func, name, "path", **where))
            return
        if name.startswith("@") or not PARAM_NAME_RE.match(name):
            self.err(CODE_TYPE,
                     f"{func}({name!r}): a parameter name uses lowercase letters, digits and _; "
                     "another node's param or @attribute is a path like '../node/param'.",
                     node)
            return
        if name in self.builtins:
            self.result.refs.append(ChRef(func, name, "builtin", **where))
            return
        self.result.refs.append(ChRef(func, name, "spare", **where))
        if func == "ch":
            self.untyped.append((name, node))
            return
        self._declare(name, func, keywords, node)

    def _declare(self, name: str, func: str, keywords: dict[str, Any], node: ast.Call) -> None:
        prev = self.decls.get(name)
        if prev is not None:
            if func != prev.func:
                self.err(CODE_TYPE,
                         f"{func}({name!r}) conflicts with {prev.func}({name!r}) on line "
                         f"{prev.line}: one parameter has one type.", node)
            elif keywords and keywords != prev.keywords:
                self.err(CODE_TYPE,
                         f"{func}({name!r}) gives other settings than its first call on line "
                         f"{prev.line}; set them once.", node)
            return
        problem, spec = _spec(name, func, keywords)
        if problem is not None:
            self.err(CODE_TYPE, problem, node)
            return
        self.decls[name] = _Decl(func, keywords, spec, getattr(node, "lineno", None))
        self.result.spare_params.append(spec)

    # --- finish -----------------------------------------------------------

    def finish(self, add_lookback: bool) -> ScanResult:
        r = self.result
        for name, node in self.untyped:
            if name == LOOKBACK_PARAM and add_lookback:
                continue  # every code block and Wrangle has lookback_bars (4.3)
            if name not in self.decls:
                self.err(REF_BROKEN,
                         f"ch({name!r}) reads a parameter this node does not have.  Declare it "
                         f"with a type, like chf({name!r}, default=1.0).", node)
        if (add_lookback and LOOKBACK_PARAM not in self.decls
                and LOOKBACK_PARAM not in self.builtins):
            r.spare_params.insert(0, lookback_spec())
        r.reads = [n for n, _w in sorted(self.reads.items(), key=lambda kv: kv[1])]
        r.writes = [n for n, _w in sorted(self.writes.items(), key=lambda kv: kv[1])]
        r.refs.sort(key=lambda ref: (ref.line or 0, ref.col or 0))
        r.diagnostics.sort(key=lambda d: (d.line or 0, d.col or 0))
        return r


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _literal_key(node: ast.AST) -> Optional[str]:
    """The attribute name of a ``stream[...]`` key, without the sigil, when it
    is a string literal; else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        name = node.value
        return name[1:] if name.startswith("@") else name
    return None


def _annotation_dtype(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name) and node.id in WRITE_DTYPES:
        return node.id
    if isinstance(node, ast.Constant) and node.value in WRITE_DTYPES:
        return node.value
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _spec(name: str, func: str, kw: dict[str, Any]) -> tuple[Optional[str], Optional[SpareParam]]:
    """The spare-param spec a typed call declares, or (problem, None)."""
    ptype = CH_TYPES[func]
    call = f"{func}({name!r})"
    label = kw.get("label", "lookback bars" if name == LOOKBACK_PARAM else name)
    if not isinstance(label, str):
        return f"{call} label must be a string.", None
    lo, hi = kw.get("min"), kw.get("max")
    for key, value in (("min", lo), ("max", hi)):
        if value is None:
            continue
        if ptype in ("string", "bool"):
            return f"{call} takes no {key}.", None
        if not _is_number(value) or not math.isfinite(value):
            return f"{call} {key} must be a number, got {value!r}.", None
    if lo is not None and hi is not None and lo > hi:
        return f"{call} min {lo} is above max {hi}.", None
    options = kw.get("options")
    if options is not None:
        if ptype != "string":
            return f"{call} takes no options (only chs() has options).", None
        if (not isinstance(options, (list, tuple)) or not options
                or not all(isinstance(o, str) for o in options)):
            return f"{call} options must be a non-empty list of strings.", None
        options = tuple(options)

    if name == LOOKBACK_PARAM:
        if func != "chi":
            return f"{LOOKBACK_PARAM} is a whole number of bars: declare it with chi().", None
        lo = LOOKBACK_MIN if lo is None else lo
        hi = LOOKBACK_MAX if hi is None else hi
        if lo < LOOKBACK_MIN or hi > LOOKBACK_MAX:
            return (f"{LOOKBACK_PARAM} must stay between {LOOKBACK_MIN} and {LOOKBACK_MAX}.",
                    None)
        default = kw.get("default", LOOKBACK_DEFAULT)
    elif "default" in kw:
        default = kw["default"]
    elif ptype == "string" and options:
        default = options[0]
    else:
        default = _TYPE_DEFAULTS[ptype]
        if ptype in ("int", "float"):
            if lo is not None and default < lo:
                default = lo
            if hi is not None and default > hi:
                default = hi

    problem, default = _check_default(call, ptype, default, lo, hi, options)
    if problem is not None:
        return problem, None
    return None, SpareParam(name, ptype, default, lo, hi, options, label)


def _check_default(call: str, ptype: str, default: Any, lo: Any, hi: Any,
                   options: Optional[tuple[str, ...]]) -> tuple[Optional[str], Any]:
    if ptype == "float":
        if not _is_number(default) or not math.isfinite(default):
            return f"{call} default must be a number, got {default!r}.", None
        default = float(default)
    elif ptype == "int":
        if isinstance(default, float) and default.is_integer():
            default = int(default)
        if not isinstance(default, int) or isinstance(default, bool):
            return f"{call} default must be a whole number, got {default!r}.", None
    elif ptype == "string":
        if not isinstance(default, str):
            return f"{call} default must be a string, got {default!r}.", None
        if options is not None and default not in options:
            return f"{call} default {default!r} is not one of {list(options)}.", None
        return None, default
    elif ptype == "bool":
        if not isinstance(default, bool):
            return f"{call} default must be True or False, got {default!r}.", None
        return None, default
    elif ptype == "vector":
        if (not isinstance(default, (list, tuple)) or not 2 <= len(default) <= 4
                or not all(_is_number(v) and math.isfinite(v) for v in default)):
            return f"{call} default must be a list of 2 to 4 numbers, got {default!r}.", None
        values = [float(v) for v in default]
        for v in values:
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                return f"{call} default {values} is outside min {lo} and max {hi}.", None
        return None, values
    if lo is not None and default < lo:
        return f"{call} default {default} is below min {lo}.", None
    if hi is not None and default > hi:
        return f"{call} default {default} is above max {hi}.", None
    return None, default

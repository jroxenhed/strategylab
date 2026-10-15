"""The code runtime (design note 4.1, 4.4 to 4.7).

This is the only module that executes user code.  Everything else
(compile, the routes, the bot) reaches user code through ``run()``.

- ``prepare(source, context, node)``: size check, ``@name`` sugar, parse,
  static scan, ``compile()``.  It never runs the code.  The result is cached.
- ``run(prepared, stream, ...)``: runs one prepared snippet in a fresh
  namespace (``np``, ``pd``, ``math``, ``sl``, ``ta``, ``stream``, ``ch*``,
  full builtins: no sandbox, by decision), checks what it wrote or
  returned, and turns every exception into a ``CodeError`` with a
  diagnostic at the user's line and column.
- The wall-clock guard: ``call_guarded`` (from a worker thread or a sync
  route) and ``await_guarded`` (from async code, such as the bot runner)
  wait for a whole cook with a time limit, counted from the moment a
  thread starts it.  Bot cooks run on ``CODE_EXECUTOR`` (4 daemon threads,
  ``sl-code``), route cooks on their own pool: never the event loop's
  default executor.  A timed-out cook keeps its thread until it ends by
  itself or the backend restarts (Python cannot stop a thread from
  outside); ``leaked_cooks()`` counts those threads, and when they hold
  every thread of a pool a new cook fails at once (the pool is full).
- ``code_enabled()`` reads the kill switch ``SL_CODE_NODES`` (unset or 1:
  on, 0: off).

Positions: ``line`` is 1-based and ``col`` 0-based, in characters of the
user's original text (see errors.py).
"""
from __future__ import annotations

import ast
import asyncio
import builtins
import concurrent.futures
import hashlib
import itertools
import logging
import math
import os
import queue
import threading
import time
import types
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional, Sequence, Union

import numpy as np
import pandas as pd

from nodebuilder.code import sl as _sl
from nodebuilder.code.errors import (
    ATTR_CLASH,
    ATTR_DYNAMIC,
    ATTR_MISSING,
    CH_DYNAMIC,
    CODE_DISABLED,
    CODE_LIMIT,
    CODE_RUNTIME,
    CODE_SYNTAX,
    CODE_TIMEOUT,
    CODE_TYPE,
    PARAM_INVALID,
    REF_BROKEN,
    AttrMissingError,
    CodeDiagnostic,
    CodeError,
    CodeTimeout,
)
from nodebuilder.code.promote import (
    CH_FUNCS,
    LOOKBACK_MAX,
    LOOKBACK_MIN,
    LOOKBACK_PARAM,
    ChRef,
    SpareParam,
    scan,
)
from nodebuilder.code.sugar import SugarResult, normalize_newlines, rewrite, split_lines
from nodebuilder.kernel.stream import DETAIL, POINT, Stream

logger = logging.getLogger(__name__)

# pandas_ta is in requirements.txt; it is imported once, here.  talib is
# not installed; it joins the namespace only if its import works.
try:
    with warnings.catch_warnings():
        # pandas_ta sets a pandas option pandas 3 deprecates; the warning is noise.
        warnings.simplefilter("ignore")
        import pandas_ta as ta  # type: ignore
except Exception:  # pragma: no cover (a broken install)
    ta = None
try:
    import talib  # type: ignore
except Exception:
    talib = None

# ---------------------------------------------------------------------------
# Limits (code_capabilities.limits)
# ---------------------------------------------------------------------------

MAX_SOURCE_BYTES = 8192
"""Largest source, in UTF-8 bytes.  Keeps graph files and bots.json small;
not a security limit."""

DEFAULT_LOOKBACK_BARS = 500

BOT_COOK_TIMEOUT_S = 10
"""How long a bot tick waits for its cook."""

ROUTE_COOK_TIMEOUT_S = 60
"""How long /backtest, /inspect and /preview wait for a cook."""

CONTEXTS: tuple[str, ...] = ("expr", "node_code", "wrangle")
"""expr: a parameter expression (one Python expression, a scalar result).
node_code: a built-in node's code block (runs after the node).
wrangle: a Wrangle node's body."""

ENV_SWITCH = "SL_CODE_NODES"


def code_enabled() -> bool:
    """The kill switch.  ``SL_CODE_NODES`` unset or empty means on (John's
    decision); 0, false, no or off means off."""
    value = os.environ.get(ENV_SWITCH, "").strip().lower()
    return value not in ("0", "false", "no", "off")


def disabled_diagnostic(node_id: Optional[str] = None) -> CodeDiagnostic:
    return CodeDiagnostic(
        CODE_DISABLED,
        f"Code nodes are turned off on this server ({ENV_SWITCH}=0).",
        node_id=node_id,
    )


def source_sha256(source: str) -> str:
    """The fingerprint of a snippet: sha256 of the user's original text as
    UTF-8, before the sugar rewrite (the same on the Mac and the VM)."""
    return hashlib.sha256(source.encode("utf-8", errors="surrogatepass")).hexdigest()


# ---------------------------------------------------------------------------
# prepare()
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class PreparedCode:
    """One snippet, checked and compiled.  ``ok`` is False when it has an
    error diagnostic; then ``code_obj`` is None and ``run()`` refuses it."""
    node_id: Optional[str]
    context: str
    param: Optional[str]
    source: str
    sha256: str
    filename: str
    code_obj: Optional[types.CodeType]
    sugar: SugarResult
    spare_params: tuple[SpareParam, ...]
    refs: tuple[ChRef, ...]
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    write_dtypes: Mapping[str, str]
    lookback_bars: Optional[int]
    diagnostics: tuple[CodeDiagnostic, ...]
    # name -> (line, col, end_line, end_col) of its first write, in the
    # user's text (a declared write the run did not make is reported there).
    write_positions: Mapping[str, tuple] = field(default_factory=lambda: types.MappingProxyType({}))

    @property
    def ok(self) -> bool:
        return self.code_obj is not None and not any(d.severity == "error" for d in self.diagnostics)

    @property
    def colmap(self):
        return self.sugar.colmap

    @property
    def nbytes(self) -> int:
        return len(self.source.encode("utf-8", errors="surrogatepass"))

    @property
    def paths(self) -> tuple[ChRef, ...]:
        """The references to other nodes or networks (names with /)."""
        return tuple(r for r in self.refs if r.is_path)

    @property
    def own_params(self) -> tuple[str, ...]:
        """This node's own params the code reads with ch*() (built-in or spare)."""
        seen: dict[str, None] = {}
        for r in self.refs:
            if not r.is_path:
                seen.setdefault(r.target, None)
        return tuple(seen)

    def effective_lookback(self, params: Optional[Mapping[str, Any]] = None) -> Optional[int]:
        """``lookback_bars`` from the node's params, else its default, held to
        the spec range (1 to LOOKBACK_MAX).  None for a parameter expression
        (it reads scalars only).  Never raises: a value that is not a finite
        number (compile reports it, kernel.params) counts as the default."""
        if self.context == "expr":
            return None
        value = (params or {}).get(LOOKBACK_PARAM, self.lookback_bars)
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return self.lookback_bars
        if not math.isfinite(number):
            return self.lookback_bars
        return min(max(int(number), LOOKBACK_MIN), LOOKBACK_MAX)

    def params_json(self) -> list[dict[str, Any]]:
        return [s.to_json() for s in self.spare_params]

    def reads_json(self, lookup: Optional[Callable[[str], Optional[tuple[str, str]]]] = None
                   ) -> list[dict[str, Any]]:
        """``[{"name": "@close", "class": ..., "dtype": ...}]``.  *lookup*
        gives (class, dtype) for a name from the node's input stream; without
        it a read is a point of dtype any."""
        out = []
        for name in self.reads:
            found = lookup(name) if lookup is not None else None
            cls, dtype = found if found is not None else (POINT, "any")
            out.append({"name": f"@{name}", "class": cls, "dtype": dtype})
        return out

    def writes_json(self) -> list[dict[str, Any]]:
        """Every write is reported as a point; dtype from its annotation or any
        (design note 4.3: the scan cannot tell a per-bar write from a scalar)."""
        return [{"name": f"@{n}", "class": POINT, "dtype": self.write_dtypes.get(n, "any")}
                for n in self.writes]

    def to_parse_result(self, lookup=None) -> dict[str, Any]:
        """The body of a parse_code reply (``result_type`` is always None in W7)."""
        return {
            "ok": self.ok,
            "params": self.params_json(),
            "reads": self.reads_json(lookup),
            "writes": self.writes_json(),
            "result_type": None,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


_CACHE_SIZE = 1024
_cache: "OrderedDict[tuple, PreparedCode]" = OrderedDict()
_cache_lock = threading.Lock()


def _node_info(node: Any) -> tuple[Optional[str], Optional[str]]:
    """(id, type) of *node*: a models.Node, a node dict, an id string or None."""
    if node is None:
        return None, None
    if isinstance(node, str):
        return node, None
    if isinstance(node, Mapping):
        nid, ntype = node.get("id"), node.get("type")
    else:
        nid, ntype = getattr(node, "id", None), getattr(node, "type", None)
    # A half-edited node dict may hold anything; only strings name things.
    return (nid if isinstance(nid, str) else None), (ntype if isinstance(ntype, str) else None)


def builtin_params_of(node_type: Optional[str]) -> tuple[str, ...]:
    """The built-in param names of a registered node type (empty if unknown)."""
    if not node_type:
        return ()
    from nodebuilder.kernel import registry

    nt = registry.get(node_type)
    if nt is None:
        import nodebuilder.trading  # noqa: F401  (registers the node types)

        nt = registry.get(node_type)
    return tuple(p.name for p in nt.params) if nt is not None else ()


def code_filename(node_id: Optional[str], context: str, param: Optional[str] = None) -> str:
    """The compile() filename, unique per node (per param for an expression),
    so a traceback frame can be matched to its node."""
    nid = node_id or "?"
    if context == "expr" and param:
        return f"<code:{nid}:{param}>"
    return f"<code:{nid}>"


def prepare(source: Optional[str], context: str, node: Any = None, *,
            node_id: Optional[str] = None, param: Optional[str] = None,
            builtin_params: Optional[Iterable[str]] = None) -> PreparedCode:
    """Check and compile one snippet.  Never runs it; never raises for bad
    user code (problems are in ``diagnostics``).

    context        : "expr", "node_code" or "wrangle".
    node           : the node the code belongs to (a models.Node, a node dict
                     or its id).  Its type gives the built-in param names a
                     bare ch*() reads instead of promoting.
    node_id        : overrides the node's id.
    param          : for "expr", the param the expression sits on.
    builtin_params : overrides the node type's param names.
    """
    if context not in CONTEXTS:
        raise ValueError(f"unknown code context {context!r}; use one of {CONTEXTS}")
    source = "" if source is None else source
    nid, ntype = _node_info(node)
    nid = node_id if node_id is not None else nid
    names = frozenset(builtin_params if builtin_params is not None else builtin_params_of(ntype))
    digest = source_sha256(source)
    key = (digest, context, nid, param, names)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    prepared = _prepare(source, context, nid, param, names, digest)
    if any(d.code == CODE_LIMIT for d in prepared.diagnostics):
        # Never cached: an oversized paste edited key by key would keep one
        # whole copy per keystroke (parse_code runs on every change).
        return prepared
    with _cache_lock:
        _cache[key] = prepared
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return prepared


def _prepare(source: str, context: str, nid: Optional[str], param: Optional[str],
             names: frozenset[str], digest: str) -> PreparedCode:
    filename = code_filename(nid, context, param)

    def result(sugar: SugarResult, diags: Iterable[CodeDiagnostic], code_obj=None,
               found=None) -> PreparedCode:
        diags = tuple(d.with_node(nid, param if context == "expr" else None) for d in diags)
        lookback = None
        spares: tuple[SpareParam, ...] = ()
        if found is not None:
            spares = tuple(found.spare_params)
            for s in spares:
                if s.name == LOOKBACK_PARAM and context != "expr":
                    lookback = int(s.default)
        return PreparedCode(
            node_id=nid, context=context, param=param, source=source, sha256=digest,
            filename=filename, code_obj=code_obj if not diags else None, sugar=sugar,
            spare_params=spares,
            refs=tuple(found.refs) if found is not None else (),
            reads=tuple(found.reads) if found is not None else (),
            writes=tuple(found.writes) if found is not None else (),
            write_dtypes=types.MappingProxyType(dict(found.write_dtypes)) if found is not None else
            types.MappingProxyType({}),
            lookback_bars=lookback, diagnostics=diags,
            write_positions=types.MappingProxyType(dict(found.write_positions))
            if found is not None else types.MappingProxyType({}),
        )

    # 1. Size.
    try:
        nbytes = len(source.encode("utf-8"))
    except UnicodeEncodeError:
        return result(rewrite(""), [CodeDiagnostic(
            CODE_SYNTAX, "The code is not valid UTF-8 text.", line=1, col=0)])
    if nbytes > MAX_SOURCE_BYTES:
        return result(rewrite(""), [CodeDiagnostic(
            CODE_LIMIT, f"The code is {nbytes:,} bytes; the limit is {MAX_SOURCE_BYTES:,} bytes.",
            line=1, col=0)])

    # 2. Sugar.
    sugar = rewrite(source)
    if sugar.errors:
        return result(sugar, sugar.errors)

    # 3. Parse.
    mode = "eval" if context == "expr" else "exec"
    try:
        tree = ast.parse(sugar.text, filename=filename, mode=mode)
    except SyntaxError as exc:
        return result(sugar, [_syntax_diagnostic(exc, sugar, "char")])
    except (ValueError, RecursionError, MemoryError) as exc:
        return result(sugar, [CodeDiagnostic(CODE_SYNTAX, f"The code cannot be parsed: {exc}",
                                             line=1, col=0)])

    # 4. Scan.
    found = scan(tree, sugar, builtin_params=names, add_lookback=context != "expr")
    diags = list(found.diagnostics)

    # 5. Compile.  Errors found here (return outside a function, say) carry
    # UTF-8 byte offsets, unlike the parser's character offsets.
    code_obj = None
    try:
        code_obj = compile(tree, filename, mode, dont_inherit=True)
    except SyntaxError as exc:
        diags.append(_syntax_diagnostic(exc, sugar, "byte"))
    except (ValueError, RecursionError, MemoryError) as exc:
        diags.append(CodeDiagnostic(CODE_SYNTAX, f"The code cannot be compiled: {exc}",
                                    line=1, col=0))
    diags.sort(key=lambda d: (d.line or 0, d.col or 0))
    return result(sugar, diags, code_obj, found)


def _syntax_diagnostic(exc: SyntaxError, sugar: SugarResult, unit: str) -> CodeDiagnostic:
    """A SyntaxError as code_syntax at Python's line and ``offset - 1``,
    mapped back through the sugar column map."""
    line = exc.lineno or 1
    col0 = exc.offset - 1 if exc.offset and exc.offset > 0 else 0
    col = sugar.user_position(line, col0, unit)
    end_line = exc.end_lineno or None
    end_col = None
    if end_line is not None and exc.end_offset and exc.end_offset > 0:
        end_col = sugar.user_position(end_line, exc.end_offset - 1, unit)
        if end_line == line and end_col is not None and col is not None and end_col < col:
            end_col = None
    return CodeDiagnostic(CODE_SYNTAX, exc.msg or "invalid syntax", line=line, col=col,
                          end_line=end_line if end_col is not None else None, end_col=end_col)


# ---------------------------------------------------------------------------
# The stream proxy (4.6)
# ---------------------------------------------------------------------------

_DETAIL_ONLY_HINT = ("parameter expressions read detail attributes only; use a Wrangle for "
                     "per-bar logic")


@dataclass(frozen=True)
class Written:
    """One attribute the code wrote: a point column or a detail value."""
    name: str            # with the sigil: @x
    kind: str            # point | detail
    dtype: str
    value: Any           # numpy column for a point, the value for a detail


def _read_only(arr: np.ndarray) -> np.ndarray:
    view = arr.view()
    view.flags.writeable = False
    return view


class StreamProxy:
    """What user code sees as ``stream`` (and ``@name``).

    Reading gives a point attribute as a pandas Series on the cook's bar
    index, or a detail attribute as its value.  An in-place edit of a read
    Series changes only the code's copy (pandas copy-on-write over a
    read-only view): the cook's columns stay intact.  Writing is allowed
    only for the names the scan found; see ``_normalize`` for what may be
    written.
    """

    __slots__ = ("_stream", "_index", "_allowed", "_dtypes", "_writer", "_detail_only",
                 "_allow_shadow", "_written", "_bases")

    def __init__(self, stream: Optional[Stream], *, writes: Iterable[str] = (),
                 write_dtypes: Optional[Mapping[str, str]] = None, writer: Optional[str] = None,
                 detail_only: bool = False, allow_shadow: bool = False) -> None:
        self._stream = stream
        self._index = stream.index if stream is not None else None
        self._allowed = frozenset(writes)
        self._dtypes = dict(write_dtypes or {})
        self._writer = writer
        self._detail_only = detail_only
        self._allow_shadow = allow_shadow
        self._written: dict[str, Written] = {}
        self._bases: dict[str, pd.Series] = {}

    @staticmethod
    def _key(name: Any) -> str:
        if not isinstance(name, str):
            raise TypeError(f"stream keys are attribute names (str), got {type(name).__name__}")
        return name[1:] if name.startswith("@") else name

    def _series(self, key: str, values: np.ndarray) -> pd.Series:
        base = self._bases.get(key)
        if base is None:
            base = pd.Series(_read_only(values), index=self._index, copy=False)
            self._bases[key] = base
        return base.copy(deep=False)

    def __getitem__(self, name: Any) -> Any:
        key = self._key(name)
        w = self._written.get(key)
        if w is not None:
            return self._series(key, w.value) if w.kind == POINT else w.value
        stream = self._stream
        attr = f"@{key}"
        if stream is not None and attr in stream.hidden:
            raise CodeError(ATTR_CLASH,
                            f"@{key} is written by two different nodes upstream, so it is hidden; "
                            "rename one of them.")
        kind = stream.kind(attr) if stream is not None else None
        if kind is None:
            raise AttrMissingError(f"@{key} is not on this node's input stream.")
        if kind == POINT:
            if self._detail_only:
                raise AttrMissingError(f"@{key} is a per-bar attribute; {_DETAIL_ONLY_HINT}.")
            return self._series(key, stream.column(attr))
        return stream.value(attr)

    def get(self, name: Any, default: Any = None) -> Any:
        try:
            return self[name]
        except AttrMissingError:
            return default

    def __contains__(self, name: Any) -> bool:
        try:
            key = self._key(name)
        except TypeError:
            return False
        if key in self._written:
            return True
        stream = self._stream
        if stream is None:
            return False
        kind = stream.kind(f"@{key}")
        return kind == DETAIL or (kind == POINT and not self._detail_only)

    def keys(self) -> list[str]:
        """The attribute names the code can read, without the sigil."""
        out: list[str] = []
        if self._stream is not None:
            for n in self._stream.names():
                if self._detail_only and self._stream.kind(n) == POINT:
                    continue
                out.append(n[1:])
        out.extend(k for k in self._written if k not in out)
        return out

    def __iter__(self):
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self.keys())

    def __delitem__(self, name: Any) -> None:
        raise TypeError("stream attributes cannot be deleted")

    def __repr__(self) -> str:
        return f"<stream {', '.join('@' + k for k in self.keys())}>"

    def __setitem__(self, name: Any, value: Any) -> None:
        key = self._key(name)
        if key not in self._allowed:
            raise CodeError(ATTR_DYNAMIC,
                            f"@{key} is not written with a literal name (@{key} = ...), so it "
                            "cannot be written; later nodes must see writes before the cook.")
        attr = f"@{key}"
        stream = self._stream
        if stream is not None and not self._allow_shadow and key not in self._written:
            upstream = stream.written_by.get(attr, None) if attr in stream else None
            if attr in stream.hidden or (attr in stream and upstream != self._writer):
                raise CodeError(ATTR_CLASH,
                                f"@{key} is already written by {upstream or 'a node upstream'}; "
                                "write a new name.")
        self._written[key] = self._normalize(key, value)
        self._bases.pop(key, None)

    # --- write checks ---------------------------------------------------

    def _normalize(self, key: str, value: Any) -> Written:
        declared = self._dtypes.get(key)
        attr = f"@{key}"
        if isinstance(value, pd.DataFrame):
            raise CodeError(CODE_TYPE, f"{attr}: write one column (a Series), not a DataFrame.")
        if isinstance(value, pd.Series):
            if self._index is None or not (value.index is self._index or value.index.equals(self._index)):
                raise CodeError(CODE_TYPE,
                                f"{attr}: a written series must be aligned to the bar index.")
            return self._column(key, value, declared)
        if isinstance(value, np.ndarray):
            if value.ndim == 0:
                value = value.item()
            else:
                n = len(self._index) if self._index is not None else 0
                if value.ndim != 1 or len(value) != n:
                    raise CodeError(CODE_TYPE,
                                    f"{attr}: a written array must be 1-D with one value per bar "
                                    f"({n}); got shape {value.shape}.")
                return self._column(key, value, declared)
        return self._detail(key, value, declared)

    def _column(self, key: str, values: Any, declared: Optional[str]) -> Written:
        attr = f"@{key}"
        dtype = values.dtype
        if isinstance(dtype, pd.BooleanDtype):
            if bool(pd.isna(values).any()):
                raise CodeError(CODE_TYPE, f"{attr}: a true/false column cannot hold missing values.")
            arr = np.array(values, dtype=bool, copy=True)
        elif dtype == np.bool_:
            arr = np.array(values, dtype=bool, copy=True)
        elif _is_numeric(dtype):
            arr = np.array(_as_float_array(values), dtype=np.float64, copy=True)
        else:
            raise CodeError(CODE_TYPE,
                            f"{attr}: per-bar values must be numbers or true/false; got {dtype}.")
        if declared == "bool" and arr.dtype != np.bool_:
            raise CodeError(CODE_TYPE, f"{attr} is declared bool but the code wrote numbers.")
        if declared == "float" and arr.dtype == np.bool_:
            arr = arr.astype(np.float64)
        return Written(attr, POINT, "bool" if arr.dtype == np.bool_ else "float", arr)

    def _detail(self, key: str, value: Any, declared: Optional[str]) -> Written:
        attr = f"@{key}"
        if isinstance(value, (bool, np.bool_)):
            out, dtype = bool(value), "bool"
        elif isinstance(value, (int, np.integer)):
            out, dtype = int(value), "int"
        elif isinstance(value, (float, np.floating)):
            out, dtype = float(value), "float"
        elif isinstance(value, str):
            out, dtype = value, "str"
        else:
            raise CodeError(CODE_TYPE,
                            f"{attr}: write a Series, a numpy array or one number, bool or string; "
                            f"got {type(value).__name__}.")
        if declared == "bool" and dtype != "bool":
            raise CodeError(CODE_TYPE, f"{attr} is declared bool but the code wrote {dtype}.")
        if declared == "float":
            if dtype == "str":
                raise CodeError(CODE_TYPE, f"{attr} is declared float but the code wrote a string.")
            out, dtype = float(out), "float"
        return Written(attr, DETAIL, dtype, out)

    # --- results --------------------------------------------------------

    def written(self) -> tuple[Written, ...]:
        return tuple(self._written.values())

    def wrote(self, key: str) -> bool:
        """True when the code wrote attribute *key* (no sigil)."""
        return key in self._written

    def apply(self) -> Optional[Stream]:
        """The input stream with every write added (the writer is the node)."""
        out = self._stream
        if out is None:
            return None
        for w in self._written.values():
            if w.kind == POINT:
                out = out.with_point(w.name, w.value, self._writer, w.dtype)
            else:
                out = out.with_detail(w.name, w.value, self._writer, w.dtype)
        return out


def _is_numeric(dtype: Any) -> bool:
    """Ints and floats, numpy or pandas nullable (not bool, not complex)."""
    from pandas.api import types as pdt

    return (pdt.is_numeric_dtype(dtype) and not pdt.is_bool_dtype(dtype)
            and not pdt.is_complex_dtype(dtype))


def _as_float_array(values: Any) -> np.ndarray:
    if isinstance(values, pd.Series):
        return values.to_numpy(dtype=np.float64, na_value=np.nan)
    return np.asarray(values, dtype=np.float64)


# ---------------------------------------------------------------------------
# ch*() at run time
# ---------------------------------------------------------------------------

Resolver = Union[Callable[[str], Any], Mapping[str, Any]]


class _Channels:
    """The ch* functions for one run.  Each one answers only the names and
    paths the scan found; anything else (an alias call with a computed
    name) is ch_dynamic, so the static list is always complete."""

    def __init__(self, prepared: PreparedCode, params: Mapping[str, Any],
                 resolve: Optional[Resolver]) -> None:
        self.known = frozenset(r.target for r in prepared.refs)
        self.spares = {s.name: s for s in prepared.spare_params}
        self.params = params
        self.resolve = resolve

    def make(self, func: str) -> Callable[..., Any]:
        def channel(name=None, default=None, *, min=None, max=None, options=None, label=None):
            return self.read(func, name)
        channel.__name__ = channel.__qualname__ = func
        return channel

    def read(self, func: str, name: Any) -> Any:
        if not isinstance(name, str) or name not in self.known:
            raise CodeError(CH_DYNAMIC,
                            f"{func}({name!r}) is not a call the code check found.  ch() names "
                            "must be literal strings, so parameters and their links are known "
                            "before the code runs.")
        if "/" in name:
            value = self._resolve(func, name)
        elif name in self.params:
            value = self.params[name]
        elif name in self.spares:
            value = self.spares[name].default
        else:
            raise CodeError(REF_BROKEN, f"{func}({name!r}): this node has no param {name!r}.")
        return coerce_channel(func, name, value)

    def _resolve(self, func: str, name: str) -> Any:
        if self.resolve is None:
            raise CodeError(REF_BROKEN, f"{func}({name!r}): references cannot be read here.")
        try:
            if isinstance(self.resolve, Mapping):
                return self.resolve[name]
            return self.resolve(name)
        except CodeError:
            raise
        except KeyError:
            raise CodeError(REF_BROKEN, f"{func}({name!r}) points at nothing.") from None


def _number_value(func: str, name: str, value: Any) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise CodeError(CODE_TYPE, f"{func}({name!r}) needs a number, got {value!r}.")
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    raise CodeError(CODE_TYPE, f"{func}({name!r}) needs a number, got {value!r}.")


def coerce_channel(func: str, name: str, value: Any) -> Any:
    """*value* as the type *func* reads: chf float, chi int, chs str, chb
    bool, chv a tuple of 2 to 4 floats; ch as it is."""
    if func == "ch":
        return value
    if isinstance(value, Mapping) and "expr" in value:
        raise CodeError(CODE_TYPE, f"{func}({name!r}) got an expression that was not evaluated.")
    if func == "chf":
        return _number_value(func, name, value)
    if func == "chi":
        number = _number_value(func, name, value)
        if not math.isfinite(number) or not number.is_integer():
            raise CodeError(CODE_TYPE, f"{func}({name!r}) needs a whole number, got {value!r}.")
        return int(number)
    if func == "chs":
        if isinstance(value, str):
            return value
        raise CodeError(CODE_TYPE, f"{func}({name!r}) needs a string, got {value!r}.")
    if func == "chb":
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise CodeError(CODE_TYPE, f"{func}({name!r}) needs true or false, got {value!r}.")
    if func == "chv":
        if isinstance(value, (list, tuple, np.ndarray)) and 2 <= len(value) <= 4:
            return tuple(_number_value(func, name, v) for v in value)
        raise CodeError(CODE_TYPE, f"{func}({name!r}) needs 2 to 4 numbers, got {value!r}.")
    raise CodeError(CH_DYNAMIC, f"unknown channel function {func}")


# ---------------------------------------------------------------------------
# Parameter expression results (3.1)
# ---------------------------------------------------------------------------

_PER_BAR_HINT = "use a Wrangle for per-bar logic"


def check_expr_result(value: Any, expected: Optional[str],
                      options: Optional[Sequence[str]] = None) -> Any:
    """The value of a parameter expression as one scalar of the param's type.

    expected: int, float (or number), bool, string (or str), select (a
    string that must be one of *options*), or None (any scalar).
    """
    if isinstance(value, (pd.Series, pd.DataFrame, pd.Index)) or (
            isinstance(value, np.ndarray) and value.ndim > 0):
        raise CodeError(CODE_TYPE,
                        f"A parameter expression must give one value, not a {type(value).__name__}; "
                        f"{_PER_BAR_HINT}.")
    if isinstance(value, np.ndarray):
        value = value.item()
    if isinstance(value, np.generic):
        value = value.item()
    kind = {"number": "float", "str": "string"}.get(expected or "", expected)
    got = type(value).__name__
    if kind is None:
        if isinstance(value, (bool, int, float, str)):
            return value
        raise CodeError(CODE_TYPE, f"A parameter expression must give one number, bool or string, "
                                   f"not {got}.")
    if kind == "int":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            return int(value)
        raise CodeError(CODE_TYPE, f"This param needs a whole number; the expression gave {value!r}.")
    if kind == "float":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value):
                raise CodeError(CODE_TYPE, f"This param needs a finite number; the expression "
                                           f"gave {value!r}.")
            return float(value)
        raise CodeError(CODE_TYPE, f"This param needs a number; the expression gave {got} {value!r}.")
    if kind == "bool":
        if isinstance(value, bool):
            return value
        raise CodeError(CODE_TYPE, f"This param needs True or False; the expression gave {value!r}.")
    if kind in ("string", "select"):
        if not isinstance(value, str):
            raise CodeError(CODE_TYPE, f"This param needs a string; the expression gave {value!r}.")
        if kind == "select" and options is not None and value not in options:
            raise CodeError(PARAM_INVALID, f"{value!r} is not one of {list(options)}.")
        return value
    raise ValueError(f"unknown expected param type {expected!r}")


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodeResult:
    """What one run produced.

    value  : a parameter expression's checked scalar (None otherwise).
    stream : for node_code and wrangle, the stream given to run() with every
             write added (the writer is the node).
    written: the attributes written, in order.
    """
    value: Any = None
    stream: Optional[Stream] = None
    written: tuple[Written, ...] = field(default_factory=tuple)


def _namespace(proxy: StreamProxy, channels: _Channels) -> dict[str, Any]:
    """A fresh globals dict per run: nothing carries over between cooks or
    bots through code globals."""
    g: dict[str, Any] = {
        "__builtins__": builtins,
        "__name__": "__strategylab_code__",
        "np": np,
        "pd": pd,
        "math": math,
        "sl": types.SimpleNamespace(**_sl.helpers()),
        "stream": proxy,
    }
    if ta is not None:
        g["ta"] = ta
    if talib is not None:
        g["talib"] = talib
    for func in CH_FUNCS:
        g[func] = channels.make(func)
    return g


def run(prepared: PreparedCode, stream: Optional[Stream] = None, *,
        params: Optional[Mapping[str, Any]] = None, resolve: Optional[Resolver] = None,
        expected: Optional[str] = None, options: Optional[Sequence[str]] = None,
        writer: Optional[str] = None, node_name: Optional[str] = None,
        allow_shadow: bool = False, shown_node: Optional[str] = None) -> CodeResult:
    """Run one prepared snippet.  Raises CodeError (with ``.diagnostic``) for
    every failure; never returns a partial result.

    stream      : the kernel Stream the code sees: the merged input stream for
                  a Wrangle, the node's output stream for a code block, the
                  input stream for an expression (detail attributes only).
                  Required for node_code and wrangle.
    params      : the node's resolved param values (built-in and spare); a
                  bare ch*() reads them, falling back to a spare's default.
    resolve     : the value of a ch*() path (a callable or a mapping by the
                  literal path string).  Item 7.B builds it.
    expected    : for an expression, the param type (int, float, bool,
                  string, select) and *options* for a select.
    writer      : the node id recorded as the writer of every write
                  (default: the prepared node id).
    node_name   : the node's display name, for the timeout message.
    allow_shadow: let a write replace an attribute an upstream node wrote
                  (default False: attr_clash).
    shown_node  : the id the user sees for this node (a node inside a locked
                  asset shows as the instance); a timeout names it.  Default:
                  the prepared node id.
    """
    if not prepared.ok:
        bad = next((d for d in prepared.diagnostics if d.severity == "error"), None)
        if bad is None:
            bad = CodeDiagnostic(CODE_SYNTAX, "The code did not compile.",
                                 node_id=prepared.node_id)
        raise CodeError.from_diagnostic(bad)
    if not code_enabled():
        raise CodeError.from_diagnostic(disabled_diagnostic(prepared.node_id))
    ctx = prepared.context
    if ctx != "expr" and stream is None:
        raise ValueError(f"run() needs the stream for a {ctx} snippet")

    guard = current_guard()
    if guard is not None and guard.abandoned:
        # This cook already timed out: stop at the next code node.
        raise guard.timeout_error()
    proxy = StreamProxy(
        stream, writes=prepared.writes, write_dtypes=prepared.write_dtypes,
        writer=writer or prepared.node_id, detail_only=ctx == "expr", allow_shadow=allow_shadow,
    )
    namespace = _namespace(proxy, _Channels(prepared, params or {}, resolve))

    failure: Optional[CodeError] = None
    value = None
    previous = (guard.enter(shown_node or prepared.node_id, node_name)
                if guard is not None else None)
    try:
        if ctx == "expr":
            value = eval(prepared.code_obj, namespace)
        else:
            exec(prepared.code_obj, namespace)
    except BaseException as exc:  # every exception from user code (design 4.7)
        if isinstance(exc, KeyboardInterrupt) and threading.current_thread() is threading.main_thread():
            raise  # Ctrl+C on a script's own thread stops the script, not the snippet
        # SystemExit, GeneratorExit and a CancelledError raised by the code
        # are the code's failure too: a bot thread never sees a signal, and a
        # CancelledError reaching an awaiting task would read as the task's
        # own cancellation.
        failure = _to_code_error(exc, prepared)
    finally:
        if guard is not None:
            guard.leave(previous)
    # Raised outside the except block, so the user's frames (and the
    # columns they hold) are not kept alive through __context__.
    if failure is not None:
        raise failure
    if ctx == "expr":
        try:
            checked = check_expr_result(value, expected, options)
        except CodeError as exc:
            raise _at_whole_source(exc, prepared) from None
        return CodeResult(value=checked)
    missing = [n for n in prepared.writes if not proxy.wrote(n)]
    if missing:
        raise _missing_write(prepared, missing[0])
    return CodeResult(stream=proxy.apply(), written=proxy.written())


def _missing_write(prepared: PreparedCode, name: str) -> CodeError:
    """attr_missing on the writer, at the line of the write: the code writes
    @name only on some paths, and this run took none of them.  Nodes below
    read @name, so it must be there after every run."""
    line, col, end_line, end_col = prepared.write_positions.get(name, (None, None, None, None))
    example = "False" if prepared.write_dtypes.get(name) == "bool" else "np.nan"
    where = f" on line {line}" if line is not None else ""
    err = CodeError(
        ATTR_MISSING,
        f"@{name} is written{where}, but this run did not write it.  Write it on every path, "
        f"for example @{name} = {example} first.",
        line=line, col=col, end_line=end_line, end_col=end_col, node_id=prepared.node_id)
    return err


def _to_code_error(exc: BaseException, prepared: PreparedCode) -> CodeError:
    """An exception from user code as a CodeError at the user's line and
    column: the last traceback frame that is this snippet's own code (so an
    error deep inside numpy points at the user's line that called numpy)."""
    pos = _frame_position(exc.__traceback__, prepared)
    param = prepared.param if prepared.context == "expr" else None
    if isinstance(exc, CodeError):
        code, message = exc.code, exc.message
    else:
        code = CODE_RUNTIME
        text = str(exc)
        message = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    err = AttrMissingError(message) if isinstance(exc, AttrMissingError) else CodeError(code, message)
    err.node_id = prepared.node_id
    err.param = param
    err.line, err.col, err.end_line, err.end_col = pos
    return err


def _frame_position(tb, prepared: PreparedCode) -> tuple[Optional[int], ...]:
    """(line, col, end_line, end_col) in the user's text of the last frame
    of *tb* that runs this snippet's code (its own filename, which nested
    defs, lambdas and comprehensions share)."""
    last = None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == prepared.filename:
            last = tb
        tb = tb.tb_next
    if last is None:
        return None, None, None, None
    line, end_line, colno, end_colno = _instruction_position(last.tb_frame.f_code, last.tb_lasti)
    line = line or last.tb_lineno
    sugar = prepared.sugar
    # co_positions() columns are UTF-8 byte offsets.
    col = sugar.user_position(line, colno, "byte")
    end_col = sugar.user_position(end_line, end_colno, "byte")
    if end_col is None:
        end_line = None
    return line, col, end_line, end_col


def _instruction_position(code: types.CodeType, lasti: int) -> tuple[Optional[int], ...]:
    """(line, end_line, col, end_col) of the instruction at *lasti* (a byte
    index; instructions are 2 bytes), as the traceback module reads it."""
    if lasti < 0:
        return None, None, None, None
    try:
        return next(itertools.islice(code.co_positions(), lasti // 2, None))
    except StopIteration:
        return None, None, None, None


def _at_whole_source(exc: CodeError, prepared: PreparedCode) -> CodeError:
    """A result problem of an expression, placed on the whole expression."""
    lines = split_lines(normalize_newlines(prepared.source))
    exc.node_id = prepared.node_id
    exc.param = prepared.param
    exc.line, exc.col = 1, 0
    exc.end_line, exc.end_col = len(lines), len(lines[-1])
    return exc


def pause_reason(error: Union[CodeError, CodeDiagnostic], node_name: Optional[str] = None) -> str:
    """The bot ``pause_reason`` for a code failure (design note 4.7, 4.9, 4.11):

    code_timeout: <node> ran longer than 10 s
    code_runtime: <node> line 3: ZeroDivisionError: division by zero
    code_syntax: <node> line 3
    code_disabled
    """
    diag = error.diagnostic if isinstance(error, CodeError) else error
    name = node_name or getattr(error, "node_name", None) or diag.node_id or "code"
    if diag.code == CODE_DISABLED:
        return CODE_DISABLED
    if diag.code == CODE_TIMEOUT:
        if isinstance(error, CodeTimeout) and not getattr(error, "pool_full", False):
            return f"{CODE_TIMEOUT}: {name} ran longer than {_seconds(error.timeout_s)}"
        return f"{CODE_TIMEOUT}: {diag.message}"
    where = f" line {diag.line}" if diag.line is not None else ""
    if diag.code == CODE_SYNTAX:
        return f"{CODE_SYNTAX}: {name}{where}"
    return f"{diag.code}: {name}{where}: {diag.message}"


# ---------------------------------------------------------------------------
# The wall-clock guard and the leaked-cook counter (4.7)
# ---------------------------------------------------------------------------

# A guard's states.  pending: waiting for a thread.  running: on a thread.
# done: finished in time.  abandoned: timed out while running (a leak).
# skipped: given up while still waiting (it never starts; not a leak).
# ended: a leaked cook that has finished since.
_PENDING, _RUNNING, _DONE = "pending", "running", "done"
_ABANDONED, _SKIPPED, _ENDED = "abandoned", "skipped", "ended"

_leak_lock = threading.Lock()
_leaked = 0
_leaked_by_pool: dict[int, int] = {}   # id(pool) -> leaked cooks holding one of its threads
_active = threading.local()

QUEUE_POLL_S = 0.05
"""How often a cook still waiting for a thread checks whether every thread
of its pool is held by a leaked cook (then it gives up: the pool is full)."""


def leaked_cooks() -> int:
    """Timed-out cooks whose threads have not ended yet (they end by
    themselves or at a backend restart)."""
    return _leaked


def leaked_on(pool: Any) -> int:
    """Leaked cooks that hold a thread of *pool*."""
    with _leak_lock:
        return _leaked_by_pool.get(id(pool), 0)


def current_guard() -> Optional["CookGuard"]:
    """The guard of the cook running on this thread, if any."""
    return getattr(_active, "guard", None)


def _seconds(value: float) -> str:
    return f"{value:g} s"


class CookGuard:
    """One guarded cook: its time limit, the code node running now (``run()``
    records it), and the leak bookkeeping.  ``tick_id`` is free for the
    caller (a bot drops a result whose tick is over).

    The time limit counts from the moment a thread starts the cook
    (``started_at``), never from the time it waited in the queue."""

    def __init__(self, timeout_s: float, *, label: str = "", tick_id: Any = None) -> None:
        self.timeout_s = float(timeout_s)
        self.label = label
        self.tick_id = tick_id
        self.state = _PENDING
        self.current_node: Optional[str] = None
        self.current_name: Optional[str] = None
        self.started_at: Optional[float] = None
        self.pool_key: Optional[int] = None
        self.on_start: Optional[Callable[[], Any]] = None
        self._abandoned = False

    @property
    def abandoned(self) -> bool:
        """True once the waiter gave up on this cook."""
        return self._abandoned

    @property
    def started(self) -> bool:
        """True once a thread has started the cook."""
        return self.started_at is not None

    def remaining(self) -> float:
        """Seconds left before the limit (the full limit until it starts)."""
        if self.started_at is None:
            return self.timeout_s
        return self.started_at + self.timeout_s - time.monotonic()

    # --- the worker side ------------------------------------------------

    def enter(self, node_id: Optional[str], name: Optional[str]) -> tuple:
        previous = (self.current_node, self.current_name)
        self.current_node, self.current_name = node_id, name
        return previous

    def leave(self, previous: tuple) -> None:
        self.current_node, self.current_name = previous

    def wrap(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """*fn* as the function to hand to a thread pool: it binds this guard
        to the thread for run(), starts the clock and keeps the leak count
        right."""
        def worker(*args: Any, **kwargs: Any) -> Any:
            with _leak_lock:
                if self.state == _SKIPPED:
                    # Given up while it waited for a thread: never start it.
                    return None
                self.state = _RUNNING
                self.started_at = time.monotonic()
                notify = self.on_start
            if notify is not None:
                try:
                    notify()
                except Exception:  # the waiter's loop is gone; the clock still runs
                    pass
            previous = getattr(_active, "guard", None)
            _active.guard = self
            try:
                return fn(*args, **kwargs)
            finally:
                _active.guard = previous
                self._finish()
        worker.__name__ = getattr(fn, "__name__", "cook")
        return worker

    def _finish(self) -> None:
        global _leaked
        ended = False
        with _leak_lock:
            if self.state == _ABANDONED:
                self.state = _ENDED
                _leaked -= 1
                if self.pool_key is not None:
                    left_here = _leaked_by_pool.get(self.pool_key, 0) - 1
                    if left_here > 0:
                        _leaked_by_pool[self.pool_key] = left_here
                    else:
                        _leaked_by_pool.pop(self.pool_key, None)
                ended = True
                left = _leaked
            elif self.state == _RUNNING:
                self.state = _DONE
        if ended:
            logger.info("leaked code cook ended: %s (leaked_cooks=%d)", self.label or "cook", left)

    # --- the waiting side -----------------------------------------------

    def skip(self) -> bool:
        """Give up on a cook that is still waiting for a thread, so it never
        starts (not a leak).  False when it has already started."""
        with _leak_lock:
            if self.state != _PENDING:
                return False
            self.state = _SKIPPED
            self._abandoned = True
            return True

    def abandon(self) -> bool:
        """The waiter gave up.  A cook still waiting for a thread is marked so
        it never starts.  A running one is a leak: counted, and logged at
        WARNING.  Returns False when the cook had already finished."""
        global _leaked
        with _leak_lock:
            if self.state == _PENDING:
                self.state = _SKIPPED
                self._abandoned = True
                return True
            if self.state != _RUNNING:
                return False
            self.state = _ABANDONED
            self._abandoned = True
            _leaked += 1
            if self.pool_key is not None:
                _leaked_by_pool[self.pool_key] = _leaked_by_pool.get(self.pool_key, 0) + 1
            count = _leaked
        logger.warning(
            "code cook timed out and leaked: %s, node %s, limit %s (leaked_cooks=%d). Its thread "
            "runs on until it ends by itself or the backend restarts.",
            self.label or "cook", self.current_name or self.current_node or "-",
            _seconds(self.timeout_s), count,
        )
        return True

    def timeout_error(self) -> CodeTimeout:
        who = self.current_name or self.current_node or "the cook"
        return CodeTimeout(f"{who} ran longer than {_seconds(self.timeout_s)}",
                           timeout_s=self.timeout_s, node_id=self.current_node,
                           node_name=self.current_name)


def pool_full_error(pool: Any, timeout_s: float) -> Optional[CodeTimeout]:
    """code_timeout when every thread of *pool* is held by a leaked cook
    (a new cook would wait for ever), else None.  Its pause reason says the
    code pool is full."""
    workers = getattr(pool, "_max_workers", None)
    if not workers:
        return None
    held = leaked_on(pool)
    if held < workers:
        return None
    err = CodeTimeout(
        f"the code pool is full: {held} cooks that ran past their time limit still hold "
        f"all {workers} code threads; they end by themselves or at a backend restart",
        timeout_s=timeout_s)
    err.pool_full = True
    return err


class _DaemonPool(concurrent.futures.Executor):
    """A bounded thread pool whose threads are daemons.

    A leaked cook (a Wrangle that never ends) holds its thread; with a
    ThreadPoolExecutor the interpreter would wait for that thread at exit,
    so the backend could not restart.  Daemon threads end with the process.
    Threads start on demand, up to *max_workers*; a cancelled future that
    is still queued never runs."""

    def __init__(self, max_workers: int, thread_name_prefix: str) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be greater than 0")
        self._max_workers = max_workers
        self._prefix = thread_name_prefix
        self._queue: "queue.SimpleQueue" = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._idle = 0
        self._shutdown = False

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any
               ) -> concurrent.futures.Future:
        with self._lock:
            if self._shutdown:
                raise RuntimeError("cannot schedule new futures after shutdown")
            future: concurrent.futures.Future = concurrent.futures.Future()
            self._queue.put((future, fn, args, kwargs))
            if self._idle > 0:
                self._idle -= 1
            elif len(self._threads) < self._max_workers:
                t = threading.Thread(target=self._work, daemon=True,
                                     name=f"{self._prefix}_{len(self._threads)}")
                self._threads.append(t)
                t.start()
            return future

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            future, fn, args, kwargs = item
            del item
            if future.set_running_or_notify_cancel():
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:  # the future carries it to the waiter
                    future.set_exception(exc)
                else:
                    future.set_result(result)
                    del result
            del future, fn, args, kwargs
            with self._lock:
                self._idle += 1

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            self._shutdown = True
            threads = list(self._threads)
        for _t in threads:
            self._queue.put(None)
        if wait:
            for t in threads:
                t.join()


CODE_POOL_WORKERS = 4
"""Threads the bot code pool has.  A hung Wrangle holds one until it ends by
itself or the backend restarts; when all of them are held, a new code cook
fails at once with code_timeout (the code pool is full)."""

CODE_EXECUTOR = _DaemonPool(CODE_POOL_WORKERS, "sl-code")
"""The pool bot code cooks run on (``await_guarded``'s default executor).
Never the event loop's default executor: broker calls, data fetches and
saves of every bot keep their threads whatever user code does."""

_pool: Optional[_DaemonPool] = None
_pool_lock = threading.Lock()


def _route_pool() -> _DaemonPool:
    """The pool call_guarded runs route cooks on (/backtest, /inspect,
    /preview); leaked cooks keep their thread."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = _DaemonPool(min(32, (os.cpu_count() or 1) + 4), "code-cook")
        return _pool


COMPILE_POOL_WORKERS = 2
_compile_pool: Optional[_DaemonPool] = None


def compile_pool() -> _DaemonPool:
    """The pool compile runs param-only window expressions on
    (kernel.params.static_windows).  Its own, never the cooks' pools, so a
    compile (a /validate, a bot's graph change) never waits behind a long
    backtest cook; when leaked evaluations hold both threads, compile keeps
    the widest-value window at once (the pool is full)."""
    global _compile_pool
    with _pool_lock:
        if _compile_pool is None:
            _compile_pool = _DaemonPool(COMPILE_POOL_WORKERS, "sl-compile")
        return _compile_pool


def call_guarded(fn: Callable[..., Any], *args: Any, timeout_s: float, label: str = "",
                 tick_id: Any = None,
                 executor: Optional[concurrent.futures.Executor] = None) -> Any:
    """Run ``fn(*args)`` on a pool thread and wait for it: at most
    *timeout_s* from the moment a thread starts it (time spent waiting for a
    free thread does not count).

    For a sync route or any worker thread (never on the event loop: it
    blocks while it waits).  On timeout it raises CodeTimeout naming the
    code node that was running, counts the leak and leaves the thread to
    end by itself.  A cook that finished right at the limit returns its
    result.  When every thread of the pool is held by a leaked cook it
    raises CodeTimeout at once (the pool is full).  fn's own exceptions
    pass through.
    """
    guard = CookGuard(timeout_s, label=label, tick_id=tick_id)
    pool = executor or _route_pool()
    guard.pool_key = id(pool)
    full = pool_full_error(pool, timeout_s)
    if full is not None:
        raise full
    started = threading.Event()
    guard.on_start = started.set
    future = pool.submit(guard.wrap(fn), *args)
    while not guard.started and not future.done():
        if started.wait(QUEUE_POLL_S) or future.done():
            break
        full = pool_full_error(pool, timeout_s)
        if full is not None and guard.skip():
            future.cancel()
            raise full
    try:
        return future.result(timeout=max(guard.remaining(), 0.0))
    except concurrent.futures.TimeoutError:
        if not guard.abandon():
            # It finished right at the limit: the result is there.
            return future.result()
        future.cancel()   # frees the queue slot of a cook that never started
        raise guard.timeout_error() from None


async def await_guarded(fn: Callable[..., Any], *args: Any, timeout_s: float,
                        executor: Optional[concurrent.futures.Executor] = None,
                        submit: Optional[Callable[..., Awaitable[Any]]] = None,
                        label: str = "", tick_id: Any = None,
                        guard: Optional[CookGuard] = None) -> Any:
    """Run ``fn(*args)`` on a code thread and await it, so the event loop
    never blocks: at most *timeout_s* from the moment a thread starts it.
    Time spent waiting for a free thread does not count, and a cook that
    finished is never reported as a timeout.

    executor: the pool to run on; default CODE_EXECUTOR (the bounded code
    pool, never the loop's default executor).
    submit: instead of *executor*, how to run a function in a thread, as
    ``submit(fn, *args)`` returning an awaitable.  Do not pass Starlette's
    ``run_in_threadpool``: it waits for the thread even when cancelled.

    On timeout: raises CodeTimeout naming the code node that was running,
    counts the leak, and the late result is dropped.  When every thread of
    the pool is held by a leaked cook: raises CodeTimeout at once, with a
    message that says the code pool is full.  If the waiting task is
    cancelled, a cook that has not started never starts.
    """
    guard = guard or CookGuard(timeout_s, label=label, tick_id=tick_id)
    loop = asyncio.get_running_loop()
    pool = None
    if submit is None:
        pool = executor if executor is not None else CODE_EXECUTOR
        guard.pool_key = id(pool)
        full = pool_full_error(pool, timeout_s)
        if full is not None:
            raise full
    started = asyncio.Event()

    def _started() -> None:
        loop.call_soon_threadsafe(started.set)

    guard.on_start = _started
    work = guard.wrap(fn)
    if submit is None:
        task = asyncio.ensure_future(loop.run_in_executor(pool, work, *args))
    else:
        task = asyncio.ensure_future(submit(work, *args))
    start_wait = asyncio.ensure_future(started.wait())
    try:
        # 1. Waiting for a thread: the limit has not started yet.
        while not guard.started and not task.done():
            await asyncio.wait({task, start_wait}, timeout=QUEUE_POLL_S,
                               return_when=asyncio.FIRST_COMPLETED)
            if guard.started or task.done() or pool is None:
                continue
            full = pool_full_error(pool, timeout_s)
            if full is not None and guard.skip():
                task.cancel()
                raise full
        # 2. Running: the limit counts from the start.
        if not task.done():
            remaining = guard.remaining()
            if remaining > 0:
                await asyncio.wait({task}, timeout=remaining)
        if task.done():
            return task.result()
        if not guard.abandon():
            # It finished right at the limit: its result is on the way.
            return await task
        task.cancel()
        raise guard.timeout_error() from None
    except asyncio.CancelledError:
        guard.skip()      # a cook that never started never will
        task.cancel()
        raise
    finally:
        start_wait.cancel()
        guard.on_start = None


# ---------------------------------------------------------------------------
# code_capabilities
# ---------------------------------------------------------------------------


def modules() -> list[str]:
    out = ["np", "pd", "math", "sl"]
    if ta is not None:
        out.append("ta")
    if talib is not None:
        out.append("talib")
    return out


def capabilities() -> dict[str, Any]:
    """The body of GET /api/nodebuilder/code_capabilities."""
    return {
        "enabled": code_enabled(),
        "language": "python",
        "limits": {
            "max_source_bytes": MAX_SOURCE_BYTES,
            "default_lookback_bars": DEFAULT_LOOKBACK_BARS,
            "cook_timeout_s": {"bot": BOT_COOK_TIMEOUT_S, "backtest": ROUTE_COOK_TIMEOUT_S},
        },
        "modules": modules(),
        "functions": _sl.describe(),
        "leaked_cooks": leaked_cooks(),
    }

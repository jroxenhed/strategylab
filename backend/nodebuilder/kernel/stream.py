"""The universal stream (plan section 3).

A Stream is what flows along a wire.  It holds:

- points:     per-bar columns, by attribute name, aligned to the cook's index
- detail:     one value per cook (a setting, say)
- prims:      RESERVED.  No node produces primitives until W8; they are
              carried through untouched.
- written_by: which node wrote each point and detail attribute

Columns live in one ColumnStore per cook.  A Stream only holds keys into
that store, so adding an attribute to a stream copies no column.

Merging (plan D4): a node's input stream is the union of the streams on its
input ports, in port order.  The same name from the same writer is one
attribute (a diamond from one source is fine).  The same name from two
different writers is a clash: the name is hidden from the merged stream.
The static check (kernel.schema) refuses any read of a hidden name, so a
clash never changes a result.

This module must stay free of imports from the rest of nodebuilder:
nodebuilder.models imports STREAM_SCHEMA_VERSION from here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

STREAM_SCHEMA_VERSION: int = 1
"""Version of the stream format.  A change ships a migration (plan 3.1)."""

ATTR_NAME_RE = re.compile(r"^@[a-z_][a-z0-9_]{0,63}$")
"""A valid attribute name, sigil included."""

POINT_DTYPES: tuple[str, ...] = ("float", "bool")
DETAIL_DTYPES: tuple[str, ...] = ("float", "int", "bool", "str")

PRIM_KINDS: tuple[str, ...] = ("trade", "session", "regime_period")
"""Reserved primitive kinds.  Reading one gives prims_no_producer until W8."""

POINT = "point"
DETAIL = "detail"


def is_attr_name(value: Any) -> bool:
    """True for a string like ``@out``."""
    return isinstance(value, str) and ATTR_NAME_RE.match(value) is not None


def prim_kind_of(value: Any) -> Optional[str]:
    """The primitive kind a read names (``@trade.pnl`` -> ``trade``), else None.

    Primitive attributes are addressed as ``@<kind>.<attr>``.  Nothing
    produces them yet (plan V8), so any such read is refused.
    """
    if not isinstance(value, str):
        return None
    text = value[1:] if value.startswith("@") else value
    kind, dot, _rest = text.partition(".")
    return kind if dot and kind in PRIM_KINDS else None


def point_dtype(values: np.ndarray) -> str:
    """``bool`` for a boolean column, ``float`` for anything numeric."""
    return "bool" if values.dtype == np.bool_ else "float"


def detail_dtype(value: Any) -> str:
    if isinstance(value, (bool, np.bool_)):
        return "bool"
    if isinstance(value, (int, np.integer)):
        return "int"
    if isinstance(value, str):
        return "str"
    return "float"


# ---------------------------------------------------------------------------
# Column store
# ---------------------------------------------------------------------------


class ColumnStore:
    """Every column of one cook, by integer key.

    A column is a 1-D numpy array as long as the index.  A lazy column is a
    function that builds the array on first read (a column nobody
    reads costs nothing).
    """

    def __init__(self, index: pd.Index) -> None:
        self.index = index
        self.length = len(index)
        self._cols: dict[int, Any] = {}
        self._next = 0

    def put(self, values: Any) -> int:
        """Store a column and return its key.  No copy for a numpy array."""
        arr = np.asarray(values)
        if arr.ndim != 1 or len(arr) != self.length:
            raise ValueError(
                f"a column must have {self.length} values, got shape {arr.shape}"
            )
        key = self._next
        self._next += 1
        self._cols[key] = arr
        return key

    def put_lazy(self, build: Callable[[], np.ndarray]) -> int:
        key = self._next
        self._next += 1
        self._cols[key] = build
        return key

    def get(self, key: int) -> np.ndarray:
        col = self._cols[key]
        if callable(col):
            col = np.asarray(col())
            self._cols[key] = col
        return col

    def drop(self, key: int) -> None:
        self._cols.pop(key, None)

    def keys(self) -> set[int]:
        return set(self._cols)

    def __contains__(self, key: object) -> bool:
        return key in self._cols

    def __len__(self) -> int:
        return len(self._cols)


# ---------------------------------------------------------------------------
# Stream
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PointRef:
    """A point attribute: its column key, dtype and writer."""
    key: int
    dtype: str
    writer: Optional[str]


@dataclass(frozen=True)
class DetailValue:
    """A detail attribute: its value, dtype and writer."""
    value: Any
    dtype: str
    writer: Optional[str]


@dataclass(frozen=True)
class PrimTable:
    """Reserved (plan 3.2): ``start``/``end`` are bar positions in the index."""
    start: np.ndarray
    end: np.ndarray
    attrs: Mapping[str, np.ndarray]


class Stream:
    """One node's output.  Treat it as immutable: every ``with_*`` method
    returns a new Stream that shares the store and the columns."""

    __slots__ = ("store", "_points", "_detail", "_prims", "_hidden")

    def __init__(
        self,
        store: ColumnStore,
        points: Optional[dict[str, PointRef]] = None,
        detail: Optional[dict[str, DetailValue]] = None,
        prims: Optional[dict[str, PrimTable]] = None,
        hidden: frozenset[str] = frozenset(),
    ) -> None:
        self.store = store
        self._points = points if points is not None else {}
        self._detail = detail if detail is not None else {}
        self._prims = prims if prims is not None else {}
        self._hidden = hidden

    @classmethod
    def empty(cls, store: ColumnStore) -> "Stream":
        return cls(store)

    # --- reading ----------------------------------------------------------

    @property
    def points(self) -> Mapping[str, PointRef]:
        return self._points

    @property
    def detail(self) -> Mapping[str, DetailValue]:
        return self._detail

    @property
    def prims(self) -> Mapping[str, PrimTable]:
        return self._prims

    @property
    def hidden(self) -> frozenset[str]:
        """Names hidden by a clash upstream (never readable)."""
        return self._hidden

    @property
    def written_by(self) -> dict[str, Optional[str]]:
        out: dict[str, Optional[str]] = {n: r.writer for n, r in self._points.items()}
        out.update({n: d.writer for n, d in self._detail.items()})
        return out

    @property
    def index(self) -> pd.Index:
        return self.store.index

    def __contains__(self, name: object) -> bool:
        return name in self._points or name in self._detail

    def names(self) -> list[str]:
        return list(self._points) + list(self._detail)

    def kind(self, name: str) -> Optional[str]:
        if name in self._points:
            return POINT
        if name in self._detail:
            return DETAIL
        return None

    def dtype(self, name: str) -> Optional[str]:
        if name in self._points:
            return self._points[name].dtype
        if name in self._detail:
            return self._detail[name].dtype
        return None

    def column(self, name: str) -> np.ndarray:
        """The per-bar values of *name*.  A detail value is broadcast to a
        full column, so a node can read a point or a detail the same way."""
        ref = self._points.get(name)
        if ref is not None:
            return self.store.get(ref.key)
        d = self._detail.get(name)
        if d is not None:
            return np.full(self.store.length, d.value)
        raise KeyError(f"{name} is not on this stream")

    def value(self, name: str) -> Any:
        """A detail attribute's value."""
        d = self._detail.get(name)
        if d is None:
            raise KeyError(f"{name} is not a detail attribute on this stream")
        return d.value

    def series(self, name: str, dtype: Any = None) -> pd.Series:
        """*name* as a pandas Series on the cook's index (no copy when it can)."""
        values = self.column(name)
        if dtype is not None:
            values = np.asarray(values, dtype=dtype)
        return pd.Series(values, index=self.store.index, copy=False)

    def __getitem__(self, name: str) -> Any:
        """``stream["@x"]``: a column for a point, the value for a detail."""
        if name in self._detail:
            return self._detail[name].value
        return self.column(name)

    def column_key(self, name: str) -> Optional[int]:
        ref = self._points.get(name)
        return ref.key if ref is not None else None

    # --- writing ----------------------------------------------------------

    def with_point(
        self, name: str, values: Any, writer: Optional[str], dtype: Optional[str] = None
    ) -> "Stream":
        """A new stream with *name* set to *values* (a column)."""
        arr = np.asarray(values)
        key = self.store.put(arr)
        return self.with_point_key(name, key, dtype or point_dtype(arr), writer)

    def with_lazy_point(
        self, name: str, build: Callable[[], np.ndarray], dtype: str, writer: Optional[str]
    ) -> "Stream":
        return self.with_point_key(name, self.store.put_lazy(build), dtype, writer)

    def with_point_key(self, name: str, key: int, dtype: str, writer: Optional[str]) -> "Stream":
        """A new stream with *name* pointing at a column already in the store."""
        points = dict(self._points)
        points[name] = PointRef(key, dtype, writer)
        detail = self._detail
        if name in detail:
            detail = {k: v for k, v in detail.items() if k != name}
        return Stream(self.store, points, detail, self._prims, self._hidden - {name})

    def with_detail(
        self, name: str, value: Any, writer: Optional[str], dtype: Optional[str] = None
    ) -> "Stream":
        """A new stream with the detail attribute *name* set to *value*."""
        detail = dict(self._detail)
        detail[name] = DetailValue(value, dtype or detail_dtype(value), writer)
        points = self._points
        if name in points:
            points = {k: v for k, v in points.items() if k != name}
        return Stream(self.store, points, detail, self._prims, self._hidden - {name})

    def without(self, names: Iterable[str]) -> "Stream":
        drop = set(names)
        return Stream(
            self.store,
            {k: v for k, v in self._points.items() if k not in drop},
            {k: v for k, v in self._detail.items() if k not in drop},
            self._prims,
            self._hidden,
        )

    def column_keys(self) -> set[int]:
        return {r.key for r in self._points.values()}

    # --- description ------------------------------------------------------

    def schema(self):
        """This stream's StreamSchema (kernel.schema)."""
        from nodebuilder.kernel.schema import AttrInfo, StreamSchema

        return StreamSchema(
            points={n: AttrInfo(n, r.dtype, r.writer, POINT) for n, r in self._points.items()},
            detail={n: AttrInfo(n, d.dtype, d.writer, DETAIL) for n, d in self._detail.items()},
            hidden={n: () for n in self._hidden},
        )

    def __repr__(self) -> str:  # pragma: no cover (debug aid)
        return f"Stream(points={list(self._points)}, detail={list(self._detail)})"


def merge_streams(streams: Sequence[Stream]) -> Stream:
    """The union of *streams*, in order (plan D4).

    Same name and same writer: kept once.  Same name from different writers:
    a clash, so the name is hidden.  kernel.schema.merge_schemas applies the
    same rules at compile time.
    """
    if len(streams) == 1:
        return streams[0]
    if not streams:
        raise ValueError("merge_streams needs at least one stream")
    store = streams[0].store
    points: dict[str, PointRef] = {}
    detail: dict[str, DetailValue] = {}
    prims: dict[str, PrimTable] = {}
    hidden: set[str] = set()
    for s in streams:
        hidden.update(s.hidden)
        for name, ref in s.points.items():
            if name in hidden:
                continue
            prev = points.get(name) or detail.get(name)
            if prev is None:
                points[name] = ref
            elif prev.writer != ref.writer or name in detail:
                hidden.add(name)
        for name, d in s.detail.items():
            if name in hidden:
                continue
            prev = points.get(name) or detail.get(name)
            if prev is None:
                detail[name] = d
            elif prev.writer != d.writer or name in points:
                hidden.add(name)
        for kind, table in s.prims.items():
            prims.setdefault(kind, table)
    for name in hidden:
        points.pop(name, None)
        detail.pop(name, None)
    return Stream(store, points, detail, prims, frozenset(hidden))

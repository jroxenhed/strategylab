"""The editor's cook cache (plan D6).

A cook is one run of every node of a graph over one fetched frame, with
every node's output stream kept (an inspector cook).  The wire inspector
(``POST /api/nodebuilder/inspect``) and the node sparklines
(``POST /api/nodebuilder/preview``) read cooks from here, so paging the
Data Sheet or redrawing sparklines never re-runs the graph.

- Key: ``(eval_hash(graph), frames_fingerprint, interval, start, end, source)``.
  ``eval_hash`` ignores everything that cannot change a value (positions,
  names, notes, boxes, the display flag).  ``frames_fingerprint`` changes
  when a fetched frame gains a bar, so a cached cook never shows bars that
  differ from a newer fetch.
- Limits: 8 entries, about 200 MB in total (counted with numpy
  ``nbytes``), and an age limit: an entry goes after 10 minutes without a
  read, or 30 minutes after it was made, whichever comes first (a Data
  Sheet still paging a cook keeps it).  The least recently used entry goes
  first.
- Single flight: two requests that miss the same key at once cook it once;
  the second waits for the first (``get_or_cook``).
- The cook id is derived from the key, so the same graph over the same
  frame always gets the same id.

This cache is for the editor only.  Live bots never read or write it, and
``bot_runner.py`` must never import this module (plan D6).

Thread safe: the routes are sync functions that FastAPI runs on its thread
pool, so two requests can touch the cache at once.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

# Plan D6 limits.  TTL_SECONDS is idle time (since the last read);
# MAX_AGE_SECONDS caps an entry's life however often it is read.
MAX_ENTRIES = 8
TTL_SECONDS = 600.0
MAX_AGE_SECONDS = 1800.0
MAX_BYTES = 200 * 1024 * 1024
# How long a request waits for another request's cook of the same key
# before cooking it itself.
SINGLE_FLIGHT_WAIT_SECONDS = 120.0


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def eval_hash(graph: Any) -> str:
    """A hash of the parts of *graph* that can change what a cook computes.

    Kept: the format versions, each node's id, type, parent, params and
    bypass flag, its promoted params, asset_ref and locked flag (W6: a
    promoted value or a pinned asset version changes what compiles), its
    code and spare params (W7; expression values are params already), and
    every wire's ends, ports and attr (in list order, since a wire without a
    port is placed by its order).  Node names are kept only when the graph
    has code or promoted params: ch() paths and promoted targets find nodes
    by name, so there a rename can change a value.  Left out: positions,
    the display flag, meta, readOnly and annotations (boxes, notes), and
    wire ids, none of which the cook reads.
    """
    nodes = []
    names_matter = False
    for nid in sorted(graph.nodes):
        n = graph.nodes[nid]
        row = [
            n.id, n.type, n.parent, n.params, bool(n.bypass),
            [p.model_dump() for p in n.promoted],
            n.asset_ref.model_dump() if n.asset_ref is not None else None,
            bool(n.locked),
        ]
        code = getattr(n, "code", None) or ""
        spares = getattr(n, "spare_params", None) or []
        if code or spares:
            row += [code, [sp.model_dump() for sp in spares]]
        nodes.append(row)
        if code or spares or n.promoted or any(
                isinstance(v, dict) and "expr" in v for v in (n.params or {}).values()):
            names_matter = True
    wires = [
        [w.from_path, w.to_path, w.from_port, w.to_port, w.attr]
        for w in graph.wires
    ]
    body = {
        "version": graph.version,
        "stream_schema": graph.stream_schema,
        "nodes": nodes,
        "wires": wires,
    }
    if names_matter:
        body["names"] = {nid: graph.nodes[nid].name for nid in sorted(graph.nodes)}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def _plain(value: Any) -> Any:
    """A numpy scalar as a plain Python value (NaN stays NaN)."""
    return value.item() if hasattr(value, "item") else value


_OHLCV = ("Open", "High", "Low", "Close", "Volume")
# The bars the fingerprint digest reads: the last FINGERPRINT_TAIL bars plus
# FINGERPRINT_SPREAD bars spread evenly over the whole frame (the first and
# last included).  A few hundred values: microseconds, whatever the frame size.
FINGERPRINT_TAIL = 64
FINGERPRINT_SPREAD = 256


def _ts_text(value: Any) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC")
    return ts.isoformat()


def _rows_digest(df: pd.DataFrame) -> str:
    """A digest of the OHLCV values (and times) of a sample of bars: the
    last FINGERPRINT_TAIL bars and FINGERPRINT_SPREAD bars spread over the
    frame.  It changes when the forming bar moves, when a recent finished
    bar is corrected (a late print), and when a back-adjustment (a split or
    dividend correction) rescales the older bars."""
    n = len(df)
    rows = np.union1d(
        np.linspace(0, n - 1, num=min(n, FINGERPRINT_SPREAD)).astype(np.int64),
        np.arange(max(0, n - FINGERPRINT_TAIL), n, dtype=np.int64),
    )
    h = hashlib.blake2b(digest_size=16)
    h.update(rows.tobytes())
    for col in _OHLCV:
        if col not in df.columns:
            h.update(b"-")
            continue
        # One column at a time, so a large frame is never copied whole.
        vals = np.asarray(df[col].to_numpy()[rows], dtype=np.float64)
        # One NaN bit pattern, so a frame with gaps still matches itself.
        vals = np.where(np.isnan(vals), np.nan, vals)
        h.update(np.ascontiguousarray(vals).tobytes())
    h.update("|".join(_ts_text(t) for t in df.index[rows]).encode())
    return h.hexdigest()


def frame_fingerprint(symbol: str, interval: str, df: pd.DataFrame) -> tuple:
    """``(symbol, interval, row_count, first_bar_time, last_bar_time,
    rows_digest)`` for one fetched frame.

    The plan names symbol, interval, row count and last bar time.  The
    digest (see ``_rows_digest``) is added because a refresh can change
    bars without adding one: the forming bar, a corrected recent bar, or a
    back-adjusted history.  Without it the cache would keep showing the
    older values while a new backtest cooks the revised frame.
    """
    n = len(df)
    if n == 0:
        return (symbol.upper(), interval, 0, None, None, "")
    return (symbol.upper(), interval, n, _ts_text(df.index[0]), _ts_text(df.index[-1]),
            _rows_digest(df))


def make_key(graph_hash: str, frames: tuple, window: dict) -> tuple:
    """The cache key (plan D6)."""
    return (
        graph_hash,
        frames,
        window["interval"],
        window["start"],
        window["end"],
        window["source"],
    )


def key_for(graph: Any, frames: Any, window: dict) -> tuple:
    """The cache key for *graph* cooked over *frames* in *window*.

    *frames* is ``((symbol, interval, df), ...)``: every frame the cook
    read (``GraphCook.frames``).  Every route builds its key here, so a
    cook that reads a new kind of frame is fingerprinted in one place.
    """
    frames = tuple(frames)
    if not frames:
        raise ValueError("A cook key needs the frames the cook read.")
    return make_key(
        eval_hash(graph),
        tuple(frame_fingerprint(sym, iv, df) for sym, iv, df in frames),
        window,
    )


def cook_id_for(key: tuple) -> str:
    """A short, stable id for *key*: the same key always gets the same id."""
    return "ck_" + hashlib.sha256(repr(key).encode()).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


@dataclass
class CookEntry:
    """One cached cook.

    graph   : the Graph that was cooked (wire targets resolve against it
              when the request carries no graph).
    program : its CompiledProgram (each node's Step).
    result  : the kernel CookResult with every node's stream kept.
    window  : {ticker, start, end, interval, source}.
    """
    cook_id: str
    key: tuple
    graph: Any
    program: Any
    result: Any
    window: dict
    created: float
    nbytes: int = 0
    # The clock time of the last read (set to `created` by put).
    last_used: float = 0.0
    # Lazily built helpers, shared by every request on this entry.
    time_keys: Optional[np.ndarray] = None
    stats: dict = field(default_factory=dict)

    @property
    def index(self) -> pd.Index:
        return self.result.index


def estimate_nbytes(result: Any, extra: Optional[np.ndarray] = None) -> int:
    """About how much memory a cook holds: every built column in its store,
    plus the index.  A lazy column that nothing has read yet costs 0 until
    it is built; ``CookCache.get`` measures again after reads."""
    total = 0
    store = getattr(result, "store", None)
    cols = getattr(store, "_cols", {}) if store is not None else {}
    for col in list(cols.values()):
        if isinstance(col, np.ndarray):
            total += int(col.nbytes)
    index = getattr(result, "index", None)
    if index is not None:
        try:
            total += int(index.nbytes)
        except (AttributeError, TypeError):
            pass
    if extra is not None:
        total += int(extra.nbytes)
    return total


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------


class CookCache:
    """An in-process LRU of CookEntry, by cook id."""

    def __init__(
        self,
        max_entries: int = MAX_ENTRIES,
        ttl_seconds: float = TTL_SECONDS,
        max_bytes: int = MAX_BYTES,
        clock: Callable[[], float] = time.monotonic,
        max_age_seconds: float = MAX_AGE_SECONDS,
    ) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.max_age_seconds = max_age_seconds
        self.max_bytes = max_bytes
        self.clock = clock
        self._entries: "OrderedDict[str, CookEntry]" = OrderedDict()
        self._lock = threading.Lock()
        # Keys being cooked right now (single flight), each with the event
        # its cook sets when it ends.
        self._inflight: dict[tuple, threading.Event] = {}

    # --- reading ----------------------------------------------------------

    def get(self, cook_id: Optional[str]) -> Optional[CookEntry]:
        """The live entry for *cook_id*, or None (unknown, evicted or too old).
        Marks it as most recently used."""
        if not cook_id:
            return None
        with self._lock:
            self._purge_expired()
            entry = self._entries.get(cook_id)
            if entry is None:
                return None
            self._entries.move_to_end(cook_id)
            entry.last_used = self.clock()
            # Lazy columns read since the last look make the entry bigger.
            entry.nbytes = estimate_nbytes(entry.result, entry.time_keys)
            self._enforce_limits(keep=cook_id)
            return entry

    def get_by_key(self, key: tuple) -> Optional[CookEntry]:
        """The live entry for *key*, or None."""
        entry = self.get(cook_id_for(key))
        if entry is not None and entry.key != key:  # a hash collision
            return None
        return entry

    def find_live(self, graph_hash: str, window: dict) -> Optional[CookEntry]:
        """The most recently used live entry that cooked a graph with
        *graph_hash* over *window* (any frame fingerprint), or None.  Used
        when the data cannot be fetched: the last good cook of the same
        graph and window is the best answer there is."""
        want = window_id(window)
        with self._lock:
            self._purge_expired()
            for cid in reversed(self._entries):
                e = self._entries[cid]
                if e.key[0] == graph_hash and window_id(e.window) == want:
                    break
            else:
                return None
        return self.get(cid)

    def get_or_cook(
        self,
        key: tuple,
        cook: Callable[[], CookEntry],
        wait_seconds: float = SINGLE_FLIGHT_WAIT_SECONDS,
    ) -> tuple[CookEntry, str]:
        """``(entry, "hit" | "miss")`` for *key*: the live entry, else the
        entry *cook* makes (it cooks and puts).

        Single flight: while one request cooks a key, a second request for
        the same key waits for it and then reads its entry, instead of
        cooking the same thing again (an aborted preview's cook is then
        not wasted).  When the first cook fails, or makes an entry too big
        to keep, the waiting request cooks itself.  A cook that takes
        longer than *wait_seconds* is not waited for.
        """
        while True:
            hit = self.get_by_key(key)
            if hit is not None:
                return hit, "hit"
            with self._lock:
                event = self._inflight.get(key)
                leader = event is None
                if leader:
                    event = self._inflight[key] = threading.Event()
            if not leader:
                if event.wait(wait_seconds):
                    # The first cook ended: look again.  A hit, or (it
                    # failed or was not kept) this request cooks next.
                    continue
                return cook(), "miss"
            try:
                # Another request may have put it between the miss and the lock.
                hit = self.get_by_key(key)
                if hit is not None:
                    return hit, "hit"
                return cook(), "miss"
            finally:
                with self._lock:
                    if self._inflight.get(key) is event:
                        del self._inflight[key]
                event.set()

    # --- writing ----------------------------------------------------------

    def put(self, *, key: tuple, graph: Any, program: Any, result: Any, window: dict) -> CookEntry:
        """Store a cook and return its entry.

        O(1) in the cook's size: only references are kept.  An entry bigger
        than the whole memory cap is returned but not kept, so its cook id
        reads as expired on the next request (the editor then sends the
        graph again and it is cooked again).
        """
        cook_id = cook_id_for(key)
        entry = CookEntry(
            cook_id=cook_id, key=key, graph=graph, program=program, result=result,
            window=dict(window), created=self.clock(),
            nbytes=estimate_nbytes(result),
        )
        entry.last_used = entry.created
        with self._lock:
            self._purge_expired()
            self._entries.pop(cook_id, None)
            if entry.nbytes > self.max_bytes:
                return entry
            self._entries[cook_id] = entry
            self._enforce_limits(keep=cook_id)
        return entry

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    # --- diagnostics ------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired()
            return len(self._entries)

    def __contains__(self, cook_id: object) -> bool:
        with self._lock:
            self._purge_expired()
            return cook_id in self._entries

    def total_bytes(self) -> int:
        with self._lock:
            return sum(e.nbytes for e in self._entries.values())

    # --- internals (call with the lock held) -------------------------------

    def _purge_expired(self) -> None:
        """Drop entries idle longer than ttl_seconds, or older than
        max_age_seconds however recently read.  The age cap bounds how old
        the data a cook_id-only request reads can be: such a request never
        fetches the frame again."""
        now = self.clock()
        for cid in [
            c for c, e in self._entries.items()
            if now - e.last_used > self.ttl_seconds or now - e.created > self.max_age_seconds
        ]:
            del self._entries[cid]

    def _enforce_limits(self, keep: Optional[str] = None) -> None:
        """Drop least recently used entries until both limits hold.  *keep*
        (the entry in use) goes last."""
        def over() -> bool:
            return (len(self._entries) > self.max_entries
                    or sum(e.nbytes for e in self._entries.values()) > self.max_bytes)

        while over():
            victim = next((c for c in self._entries if c != keep), None)
            if victim is None:
                break
            del self._entries[victim]


def window_id(window: dict) -> tuple:
    """A window as a comparable tuple (the ticker upper-cased, as the
    frame fingerprint has it)."""
    return (
        str(window.get("ticker", "")).upper(), window.get("start"), window.get("end"),
        window.get("interval"), window.get("source"),
    )


# The process-wide cache the routes use.  Server restart clears it.
COOK_CACHE = CookCache()


# ---------------------------------------------------------------------------
# Reading a cook: the /inspect and /preview bodies (plan Wave 4 contracts)
# ---------------------------------------------------------------------------

HIST_BINS = 20
TIME_COLUMN = "@time"


class InspectError(Exception):
    """A request the cook cannot answer.  The route turns it into
    HTTP *status* with ``{"detail": {"code", "message"}}``."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def time_keys(entry: CookEntry) -> np.ndarray:
    """Every bar's time as the chart writes it: "YYYY-MM-DD" for daily and
    longer bars, unix seconds (UTC) for intraday (shared._format_time_index).
    Built once per entry."""
    if entry.time_keys is None:
        from shared import _format_time_index

        entry.time_keys = np.asarray(_format_time_index(entry.index, entry.window["interval"]))
    return entry.time_keys


def _time_key_of(value: Any, intraday: bool) -> Any:
    """*value* (a date string, an ISO time or unix seconds) in the form of
    time_keys, so it can be searched.  A time without a zone is New York
    time, the market's clock."""
    if isinstance(value, bool):
        raise InspectError(422, "around_time_invalid", "around_time must be a date or a unix time")
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            ts = pd.Timestamp(int(float(value)), unit="s", tz="UTC")
        else:
            ts = pd.Timestamp(str(value))
            if ts.tzinfo is None:
                ts = ts.tz_localize("America/New_York")
    except (ValueError, TypeError, OverflowError) as exc:
        raise InspectError(422, "around_time_invalid", f"around_time {value!r} is not a time") from exc
    if intraday:
        return int(ts.tz_convert("UTC").timestamp())
    return ts.tz_convert("America/New_York").strftime("%Y-%m-%d")


def bar_position(entry: CookEntry, around_time: Any) -> int:
    """The position of the first bar at or after *around_time*, kept inside
    the frame."""
    keys = time_keys(entry)
    n = len(keys)
    if n == 0:
        return 0
    intraday = keys.dtype.kind in "iu"
    pos = int(np.searchsorted(keys, _time_key_of(around_time, intraday), side="left"))
    return min(max(pos, 0), n - 1)


def _as_float(col: np.ndarray) -> np.ndarray:
    return np.asarray(col, dtype=np.float64)


def _truth(col: np.ndarray) -> np.ndarray:
    """A column as true/false: a bool column as is; a number is true when it
    is not 0 and not NaN."""
    if col.dtype == np.bool_:
        return col
    f = _as_float(col)
    return (f != 0) & ~np.isnan(f)


def filter_mask(stream: Any, flt: Any) -> np.ndarray:
    """The rows where the filter passes (*flt* has attr, op, value)."""
    if flt.attr not in stream:
        raise InspectError(422, "attr_unknown", f"{flt.attr} is not on this stream")
    col = np.asarray(stream.column(flt.attr))
    if flt.op == "is_true":
        return _truth(col)
    if flt.op == "is_false":
        if col.dtype == np.bool_:
            return ~col
        f = _as_float(col)
        return f == 0
    f = _as_float(col)
    if flt.op == "not_nan":
        return ~np.isnan(f)
    with np.errstate(invalid="ignore"):
        if flt.op == "gt":
            return f > float(flt.value)
        return f < float(flt.value)


def column_stats(entry: CookEntry, stream: Any, name: str) -> dict:
    """Header stats for a point column over the whole cook: min, max,
    nan_count and a 20-bin histogram for numbers; true_count for bools.
    Kept on the entry by column key, so a column shared by many streams is
    measured once."""
    ref = stream.points[name]
    cached = entry.stats.get(ref.key)
    if cached is not None:
        return cached
    col = np.asarray(stream.column(name))
    if ref.dtype == "bool":
        stats: dict = {"true_count": int(np.count_nonzero(_truth(col)))}
    else:
        f = _as_float(col)
        stats = {"nan_count": int(np.count_nonzero(np.isnan(f)))}
        finite = f[np.isfinite(f)]
        if finite.size:
            lo, hi = float(finite.min()), float(finite.max())
            span = (lo, hi) if hi > lo else (lo - 0.5, hi + 0.5)
            counts, edges = np.histogram(finite, bins=HIST_BINS, range=span)
            stats.update({
                "min": lo, "max": hi,
                "hist": {"edges": edges.tolist(), "counts": counts.tolist()},
            })
    entry.stats[ref.key] = stats
    return stats


def _cells(col: np.ndarray, dtype: str, idx: np.ndarray) -> list:
    """The values of *col* at the rows *idx*, as JSON-ready Python values
    (NaN and infinity become null)."""
    v = np.asarray(col)[idx]
    if dtype == "bool":
        if v.dtype == np.bool_:
            return v.tolist()
        f = _as_float(v)
        return [None if x != x else bool(x) for x in f.tolist()]
    f = _as_float(v)
    out = f.astype(object)
    out[~np.isfinite(f)] = None
    return out.tolist()


def _plain_detail(value: Any) -> Any:
    value = _plain(value)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def resolve_target(entry: CookEntry, graph: Any, target: Any) -> tuple[str, Any, Optional[list]]:
    """(node id, stream, read_by_consumer) for a node or wire target.

    A wire shows its source node's whole output stream; read_by_consumer
    lists the names the consumer reads from that stream (None for a node
    target).  A bypassed node's stream is its input stream: the cook passes
    in0 through unchanged, so nothing extra is needed here.
    """
    streams = entry.result.streams
    if target.wire_id is not None:
        wire = next((w for w in graph.wires if w.id == target.wire_id), None)
        if wire is None:
            raise InspectError(404, "target_not_found", f"no wire {target.wire_id!r} in the graph")
        node_id = wire.from_path
        stream = streams.get(node_id)
        if stream is None:
            raise InspectError(404, "target_not_found", f"wire {target.wire_id!r} has no cooked source")
        return node_id, stream, _consumer_reads(entry.program, wire.to_path, stream)
    node_id = target.node_id
    if node_id not in graph.nodes or node_id not in streams:
        raise InspectError(404, "target_not_found", f"no cooked node {node_id!r} in the graph")
    return node_id, streams[node_id], None


def _consumer_reads(program: Any, consumer_id: str, stream: Any) -> list:
    """The names *consumer_id* reads that come from *stream*: a read counts
    when the stream carries that name from the same writer."""
    try:
        step = program.step(consumer_id)
    except KeyError:
        return []
    writers = stream.written_by
    out: list[str] = []
    for writer, name in step.read_from:
        if name in writers and writers[name] == writer and name not in out:
            out.append(name)
    return out


def inspect_body(
    entry: CookEntry,
    *,
    graph: Any,
    target: Any,
    attrs: Optional[list],
    offset: int,
    limit: int,
    around_time: Any,
    flt: Any,
    cache_state: str,
) -> dict:
    """The /inspect response for one cook (plan Wave 4 contract)."""
    from nodebuilder.models import STREAM_SCHEMA_VERSION

    _node_id, stream, read_by = resolve_target(entry, graph, target)
    n = entry.result.store.length

    # "@time" is always the first column, built from the bar index (the
    # Ticker's own @time point would repeat it as unix seconds).
    wanted = None if attrs is None else list(dict.fromkeys(attrs))
    if wanted is None:
        point_names = [a for a in stream.points if a != TIME_COLUMN]
        detail_names = list(stream.detail)
    else:
        point_names = [a for a in wanted if a in stream.points and a != TIME_COLUMN]
        detail_names = [a for a in wanted if a in stream.detail]

    # Rows: every bar, or the bars the filter keeps.
    if flt is not None:
        rows_idx = np.flatnonzero(filter_mask(stream, flt))
        total = int(rows_idx.size)
    else:
        rows_idx = None
        total = n

    if around_time is not None and total:
        pos = bar_position(entry, around_time)
        at = int(np.searchsorted(rows_idx, pos)) if rows_idx is not None else pos
        start = max(0, min(at - limit // 2, total - limit))
    else:
        start = min(offset, total)
    stop = min(total, start + limit)
    idx = rows_idx[start:stop] if rows_idx is not None else np.arange(start, stop)

    columns = [{"name": TIME_COLUMN, "dtype": "time", "written_by": None}]
    cols: list[list] = []
    stats: dict[str, dict] = {}
    for name in point_names:
        ref = stream.points[name]
        columns.append({"name": name, "dtype": ref.dtype, "written_by": ref.writer})
        cols.append(_cells(stream.column(name), ref.dtype, idx))
        stats[name] = column_stats(entry, stream, name)
    rows = [list(r) for r in zip(*cols)] if cols else [[] for _ in range(len(idx))]

    detail = [
        {"name": name, "dtype": d.dtype, "value": _plain_detail(d.value), "written_by": d.writer}
        for name in detail_names
        for d in (stream.detail[name],)
    ]

    body = {
        "cook_id": entry.cook_id,
        "cache": cache_state,
        "stream_schema": STREAM_SCHEMA_VERSION,
        "columns": columns,
        "detail": detail,
        "prims": [],
        "time": time_keys(entry)[idx].tolist() if len(idx) else [],
        "rows": rows,
        "total": total,
        # Every bar of the cook, before the filter (the sheet's "X of Y").
        "total_unfiltered": n,
        "offset": start,
        "stats": stats,
    }
    if read_by is not None:
        body["read_by_consumer"] = read_by
    return body


def _buckets(n: int, points: int) -> list[tuple[int, int]]:
    """*points* [start, stop) slices over n bars.  With fewer bars than
    points, a bucket holds the nearest bar, so there are always *points*."""
    edges = (np.arange(points + 1) * n) // points
    out = []
    for i in range(points):
        a, b = int(edges[i]), int(edges[i + 1])
        a = min(a, n - 1)
        b = max(b, a + 1)
        out.append((a, b))
    return out


def decimate_line(col: np.ndarray, points: int) -> list:
    """Min/max decimation to *points* values: each bucket gives its min or
    its max, whichever is further from the value before it, so a spike stays
    visible.  A bucket with no number is null."""
    f = _as_float(col)
    if f.size == 0:
        return [None] * points
    out: list = []
    prev: Optional[float] = None
    for a, b in _buckets(f.size, points):
        seg = f[a:b]
        seg = seg[np.isfinite(seg)]
        if seg.size == 0:
            out.append(None)
            continue
        lo, hi = float(seg.min()), float(seg.max())
        ref = float(seg[0]) if prev is None else prev
        value = hi if abs(hi - ref) >= abs(lo - ref) else lo
        out.append(value)
        prev = value
    return out


def decimate_bool(col: np.ndarray, points: int) -> list:
    """Each bucket's share of true bars, 0 to 1."""
    t = _truth(np.asarray(col))
    if t.size == 0:
        return [None] * points
    return [float(np.count_nonzero(t[a:b])) / (b - a) for a, b in _buckets(t.size, points)]


def preview_body(entry: CookEntry, *, graph: Any, node_ids: Optional[list], points: int) -> dict:
    """The /preview response: each node's primary write, decimated to
    *points* values (plan Wave 4 contract).  A node is left out when its
    primary write is not a column it wrote itself (a bypassed node, a
    terminal, a node that writes only detail values)."""
    from nodebuilder.compile import assign_write_names
    from nodebuilder.kernel import registry
    from nodebuilder.kernel.schema import primary_write

    write_names = assign_write_names(graph)
    streams = entry.result.streams
    ids = list(graph.nodes) if node_ids is None else list(dict.fromkeys(node_ids))
    n = entry.result.store.length
    nodes: dict[str, dict] = {}
    for nid in ids:
        node = graph.nodes.get(nid)
        stream = streams.get(nid)
        if node is None or stream is None:
            continue
        attr = primary_write(node, registry.get(node.type), write_names)
        ref = stream.points.get(attr) if attr else None
        if ref is None or ref.writer != nid:
            continue
        col = stream.column(attr)
        stats = column_stats(entry, stream, attr)
        if ref.dtype == "bool":
            true_pct = 100.0 * stats["true_count"] / n if n else 0.0
            nodes[nid] = {"attr": attr, "kind": "bool", "true_pct": round(true_pct, 4),
                          "values": decimate_bool(col, points)}
        else:
            nodes[nid] = {"attr": attr, "kind": "line", "min": stats.get("min"),
                          "max": stats.get("max"), "nan_count": stats["nan_count"],
                          "values": decimate_line(col, points)}
    return {"cook_id": entry.cook_id, "nodes": nodes}

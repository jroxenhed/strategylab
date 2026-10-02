"""Reference tickers: their own bars, aligned onto the primary's index
(plan D8, W5 item 5.C).

A group's primary Ticker defines its bar index.  Any other Ticker the group
reads is a reference Ticker (SPY next to AAPL, the regime's daily bars under
an hourly strategy).  A reference Ticker is fetched on its own symbol and
interval, with its own lookback padding, and every node that reads only that
reference cooks over the reference's own bars: an SMA 200 of weekly SPY runs
over weekly bars, with the history before the backtest window.  Where such a
stream meets the primary's bars (a node that also reads the primary, or a
terminal), its columns are aligned onto the primary's index:

- a coarser reference interval (1d under 1h): the value of the last
  reference bar that has ENDED by the primary bar's time.  A bar ends at
  the next bar's label or at its label plus one interval (calendar aware:
  a daily bar ends at the next midnight of its own time zone, a monthly
  bar at the next month's first day), whichever comes first.  On complete
  data this is ``shared.align_htf_to_ltf`` (shift one reference bar, then
  the last bar at or before the primary bar's time).  It is measured by
  time, not by position (KA-6), so a reference that has no bar yet for the
  current period (a holiday on its calendar, a live fetch that does not
  have today's daily bar) still gives the last bar that closed, never one
  period older;
- the same interval, intraday: the last reference bar at or before the
  primary bar's time (an exact join plus forward fill).  Two bars of the
  same length that start in that order also end in that order;
- the same interval at daily or longer (KA-1): an exact join only when
  both frames are on ONE calendar: the same time zone and the same label
  time of day on every bar (AAPL and SPY daily, both stamped 00:00 New
  York).  Otherwise the reference is treated as coarser (the end-time rule
  above).  A daily label says when the bar's day starts, not when its
  session ends: BTC-USD's day D (stamped 00:00 UTC) ends at 00:00 UTC on
  D+1, hours after AAPL's day D closed, and a weekly crypto bar holds the
  weekend.  Futures stamped 00:00 New York share AAPL's calendar by this
  rule although their session ends at 17:00; that hour is accepted;
- a finer interval: the exact join (a finer bar that starts no later than
  the primary bar also ends no later than it).

So no primary bar ever sees a reference value from a bar that closes after
it, and moving a later reference bar never changes an earlier value.

Vocabulary:
- **domain**: the frame a node cooks over.  A node whose Ticker ancestors
  are all reference Tickers of one frame (symbol, interval) is in that
  reference's domain; everything else (the primary's nodes, nodes mixing
  frames, every terminal) is in the primary domain.  Nodes with no Ticker
  ancestor (a constant) are free: they cook wherever they are needed.
- **boundary**: a reference-domain node that a primary-domain node reads.
  The primary cook gets the boundary's stream aligned onto its index, under
  the same node id and the same writers, so the kernel cook and every
  reader stay unchanged.

Which Tickers are primary in one cook (``ticker_roles``): in a graph with no
Output Group, every Ticker without a prefix reads the request's frame (plan
D11) and a prefixed Ticker is a reference.  With Output Groups, the primary
Tickers of the groups cooked here read the frame; every other Ticker is a
reference.

The domain cook is CPU work: never call it on an event loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

import numpy as np
import pandas as pd

from nodebuilder.kernel import registry as _registry
from nodebuilder.kernel.evaluate import CookResult, Step, cook
from nodebuilder.kernel.schema import PASS, RUN, Params
from nodebuilder.kernel.stream import PointRef, Stream
from nodebuilder.trading.nodes_data import TICKER_TYPE, ticker_prefix

# Where build_graph_attrs keeps the reference frames in the attrs dict.
REFS_KEY = "__nodebuilder_refs__"

# Bar length of each interval, in seconds (months as 30 days).
INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "90m": 5400,
    "1h": 3600, "1d": 86_400, "5d": 432_000, "1wk": 604_800, "1mo": 2_592_000,
    "3mo": 7_776_000,
}

_PRIMARY = "__primary__"


def frame_key(symbol: Any, interval: Any) -> tuple[str, str]:
    """(SYMBOL, interval): how frames are keyed (the same as run._frame_key)."""
    return (str(symbol).strip().upper(), str(interval))


# ---------------------------------------------------------------------------
# Which Ticker reads which frame
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceTicker:
    """A Ticker that reads its own symbol's bars, not the cook's frame."""
    node_id: str
    symbol: str          # upper case
    interval: str
    prefix: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.symbol, self.interval)


@dataclass(frozen=True)
class TickerRoles:
    """primary: the Ticker ids that read the cook's own frame.
    references: the reference Tickers, in step order.
    prefixed_primary: True when a primary Ticker has a prefix (it then
    needs its bars under its own id, see nodes_data._ticker)."""
    primary: frozenset
    references: tuple
    prefixed_primary: bool = False

    @property
    def needs_domains(self) -> bool:
        return bool(self.references) or self.prefixed_primary

    def keys(self) -> list[tuple[str, str]]:
        """The reference frame keys, each once, in step order."""
        out: list[tuple[str, str]] = []
        for r in self.references:
            if r.key not in out:
                out.append(r.key)
        return out


def _ticker_steps(steps: Iterable[Step]) -> list[Step]:
    return [s for s in steps if s.type == TICKER_TYPE and s.mode == RUN]


def reference_of(step: Step) -> ReferenceTicker:
    params = step.params or {}
    sym, itv = frame_key(params.get("symbol") or "", params.get("interval") or "")
    return ReferenceTicker(node_id=step.node_id, symbol=sym, interval=itv,
                           prefix=ticker_prefix(params))


def _group_in_cook(group, step_ids: set) -> bool:
    terminals = getattr(group, "terminals", None)
    ids = terminals.node_ids() if terminals is not None else ()
    return bool(ids) and set(ids) <= step_ids


def ticker_roles(program) -> TickerRoles:
    """Which Tickers of *program* (a compiled program, or one cut down to
    some groups' steps) read the cook's frame, and which are references."""
    steps = tuple(program.steps)
    tickers = _ticker_steps(steps)
    if not tickers:
        return TickerRoles(frozenset(), ())
    groups = tuple(getattr(program, "groups", ()) or ())
    if not groups or any(getattr(g, "implicit", False) for g in groups):
        primary = {s.node_id for s in tickers if not ticker_prefix(s.params or {})}
    else:
        step_ids = {s.node_id for s in steps}
        primary = {g.primary_ticker_id for g in groups
                   if g.primary_ticker_id and _group_in_cook(g, step_ids)}
    primary &= {s.node_id for s in tickers}
    refs = tuple(reference_of(s) for s in tickers if s.node_id not in primary)
    prefixed = any(ticker_prefix(s.params or {}) for s in tickers if s.node_id in primary)
    return TickerRoles(frozenset(primary), refs, prefixed)


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


def _has_output(step: Step) -> bool:
    nt = _registry.get(step.type)
    return nt is None or nt.has_output


def step_domains(steps: Iterable[Step], roles: TickerRoles) -> dict[str, Any]:
    """node id -> its domain: a reference frame key, _PRIMARY, or None (free).

    *steps* must be in topological order.  A node with no output (a
    terminal) is always primary: the simulator reads it on the primary's
    bars."""
    steps = list(steps)
    ref_key = {r.node_id: r.key for r in roles.references}
    sources: dict[str, frozenset] = {}
    domain: dict[str, Any] = {}
    for s in steps:
        if s.type == TICKER_TYPE and s.mode == RUN:
            src = frozenset({ref_key.get(s.node_id, _PRIMARY)})
        else:
            src = frozenset().union(*(sources.get(u, frozenset()) for u in s.depends_on()))
        sources[s.node_id] = src
        if not src:
            domain[s.node_id] = None
        elif len(src) == 1 and _PRIMARY not in src and _has_output(s):
            domain[s.node_id] = next(iter(src))
        else:
            domain[s.node_id] = _PRIMARY
    return domain


def step_needs(steps: Iterable[Step]) -> dict[str, int]:
    """Each node's lookback along its longest input path, in bars of the
    frame it cooks over (the kernel's ``need``, worked out from the steps)."""
    need: dict[str, int] = {}
    for s in steps:
        if s.mode == RUN:
            nt = _registry.get(s.type)
            own = int(nt.lookback(s.params)) if (nt is not None and nt.lookback is not None
                                                 and s.params is not None) else 0
            need[s.node_id] = own + max((need.get(u, 0) for u in s.inputs), default=0)
        elif s.mode == PASS and s.pass_from is not None:
            need[s.node_id] = need.get(s.pass_from, 0)
        else:
            need[s.node_id] = 0
    return need


def reference_needs(program, roles: Optional[TickerRoles] = None) -> dict[tuple[str, str], int]:
    """Bars of history each reference frame needs before the backtest's
    first bar: the largest lookback among the nodes cooked over it (plus
    one bar for the shift a coarser frame gets)."""
    roles = roles or ticker_roles(program)
    domains = step_domains(program.steps, roles)
    needs = step_needs(program.steps)
    out: dict[tuple[str, str], int] = {key: 0 for key in roles.keys()}
    for nid, dom in domains.items():
        if dom is not None and dom != _PRIMARY:
            out[dom] = max(out.get(dom, 0), needs.get(nid, 0))
    return {key: n + 1 for key, n in out.items()}


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def _utc_ns(index: pd.Index, what: str) -> np.ndarray:
    """Bar times as int64 nanoseconds since 1970 UTC.  A frame's index may be
    stored in seconds or microseconds (pandas 2 keeps the unit it was
    built with), so it is converted to nanoseconds before two frames are
    compared; a naive index counts as UTC."""
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError(f"{what} has no bar times, so a reference Ticker cannot be "
                         "aligned to it.")
    idx = index.tz_convert("UTC") if index.tz is not None else index.tz_localize("UTC")
    return idx.as_unit("ns").asi8


def bar_seconds(index: pd.Index) -> Optional[float]:
    """The usual bar length of a frame, in seconds (the median gap between
    bars), or None when it cannot be told."""
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        return None
    gaps = np.diff(_utc_ns(index, "the frame")) / 1e9
    gaps = gaps[gaps > 0]
    return float(np.median(gaps)) if len(gaps) else None


def is_coarser(ref_interval: Optional[str], ref_index: pd.Index,
               primary_interval: Optional[str], primary_index: pd.Index) -> bool:
    """True when the reference's bars are longer than the primary's (the
    shift-one-bar rule applies).  From the interval names when both are
    known, else from the frames' bar spacing; when neither tells, True
    (shifting is the side that can never look ahead)."""
    r = INTERVAL_SECONDS.get(ref_interval or "")
    p = INTERVAL_SECONDS.get(primary_interval or "")
    if r is not None and p is not None:
        return r > p
    r = r or bar_seconds(ref_index)
    p = p or bar_seconds(primary_index)
    if r is None or p is None:
        return True
    return r > 1.5 * p


# Calendar offsets for intervals of a day or longer: a daily bar ends at the
# next midnight of its own time zone (24 hours is wrong across a DST change).
_CALENDAR_OFFSETS: dict[str, Any] = {
    "1d": pd.DateOffset(days=1), "5d": pd.DateOffset(days=5), "1wk": pd.DateOffset(weeks=1),
    "1mo": pd.DateOffset(months=1), "3mo": pd.DateOffset(months=3),
}
_DAY_SECONDS = 86_400
_NO_END = np.iinfo(np.int64).max


def _seconds_of(interval: Optional[str], index: pd.Index) -> Optional[float]:
    s = INTERVAL_SECONDS.get(interval or "")
    return float(s) if s is not None else bar_seconds(index)


def one_calendar(a: pd.Index, b: pd.Index) -> bool:
    """True when two daily-or-longer frames are on one calendar: the same
    time zone and one label time of day shared by every bar of both (AAPL
    and SPY daily, both stamped 00:00 New York).  Then a bar labelled day D
    in one frame ends when day D ends in the other."""
    if not (isinstance(a, pd.DatetimeIndex) and isinstance(b, pd.DatetimeIndex)):
        return False
    if str(a.tz) != str(b.tz):
        return False
    if not len(a) or not len(b):
        return True

    def times(idx: pd.DatetimeIndex) -> np.ndarray:
        return np.unique(np.asarray(idx.hour) * 3600 + np.asarray(idx.minute) * 60
                         + np.asarray(idx.second))

    ta, tb = times(a), times(b)
    return len(ta) == 1 and len(tb) == 1 and int(ta[0]) == int(tb[0])


def shifts(ref_interval: Optional[str], ref_index: pd.Index,
           primary_interval: Optional[str], primary_index: pd.Index) -> bool:
    """True when a reference aligns by the end-time rule (the value of the
    last reference bar that has ended), False for the exact join.

    Coarser references shift.  A same-interval reference at daily or longer
    shifts unless both frames are on one calendar (KA-1, see the module
    doc).  Intraday same-interval and finer references join exactly."""
    if is_coarser(ref_interval, ref_index, primary_interval, primary_index):
        return True
    r = _seconds_of(ref_interval, ref_index)
    p = _seconds_of(primary_interval, primary_index)
    if r is None or p is None:
        return True
    names = INTERVAL_SECONDS.get(ref_interval or "") is not None and \
        INTERVAL_SECONDS.get(primary_interval or "") is not None
    same = (r == p) if names else (r * 1.5 > p)
    if not same or r < _DAY_SECONDS:
        return False
    return not one_calendar(ref_index, primary_index)


def bar_ends(index: pd.Index, interval: Optional[str]) -> np.ndarray:
    """When each bar of a sorted frame has ended, as int64 ns UTC: the next
    later bar's label, or the label plus one *interval* (calendar aware for
    a day or longer), whichever comes first.  With an unknown interval only
    the next label counts (the last bar never ends), which is exactly the
    old shift-one-bar-by-position rule."""
    labels = _utc_ns(index, "the reference frame")
    n = len(labels)
    nxt_at = np.searchsorted(labels, labels, side="right")
    nxt = np.full(n, _NO_END, dtype=np.int64)
    has = nxt_at < n
    nxt[has] = labels[nxt_at[has]]
    offset = _CALENDAR_OFFSETS.get(interval or "")
    seconds = INTERVAL_SECONDS.get(interval or "")
    nominal = None
    if offset is not None:
        idx = pd.DatetimeIndex(index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        try:
            nominal = _utc_ns(idx + offset, "the reference frame")
        except (ValueError, OverflowError):
            # A wall time that does not exist in the frame's zone (a DST
            # change at midnight): fall back to whole days of seconds.
            nominal = None
    if nominal is None and seconds is not None:
        nominal = labels + np.int64(seconds) * np.int64(1_000_000_000)
    if nominal is None:
        return nxt
    return np.minimum(nominal, nxt)


def align_positions(ref_index: pd.Index, primary_index: pd.Index, coarser: bool,
                    ref_interval: Optional[str] = None) -> np.ndarray:
    """For each primary bar, the reference row whose value it gets (-1:
    none).

    coarser False: the last reference bar at or before the primary bar's
    time (the exact join).  coarser True (the end-time rule, see
    ``shifts``): the last reference bar that has ended by the primary bar's
    time (``bar_ends``), so by time, not by position (KA-6).  On complete
    data that is the bar before the exact join's (shared.align_htf_to_ltf).
    The reference index must be sorted."""
    prim_ts = _utc_ns(primary_index, "the primary frame")
    if not coarser:
        ref_ts = _utc_ns(ref_index, "the reference frame")
        return np.searchsorted(ref_ts, prim_ts, side="right") - 1
    ends = bar_ends(ref_index, ref_interval)
    return np.searchsorted(ends, prim_ts, side="right") - 1


def align_values(values: np.ndarray, pos: np.ndarray, dtype: str) -> np.ndarray:
    """*values* (one reference column) on the primary's index.  A primary
    bar with no reference bar gets NaN, or False for a bool column (as the
    rule regime's ``fillna(0).astype(bool)``)."""
    ok = pos >= 0
    src = pos[ok]
    if dtype == "bool":
        out = np.zeros(len(pos), dtype=bool)
        out[ok] = np.asarray(values, dtype=bool)[src]
        return out
    out = np.full(len(pos), np.nan, dtype=np.float64)
    out[ok] = np.asarray(values, dtype=np.float64)[src]
    return out


# ---------------------------------------------------------------------------
# The frames one cook reads
# ---------------------------------------------------------------------------


@dataclass
class RefFrame:
    """One reference frame: its key, sorted index and bars."""
    key: tuple[str, str]
    index: pd.Index
    bars: dict


@dataclass
class ReferenceFrames:
    """What a cook needs beyond the primary's bars (kept in attrs[REFS_KEY]
    by nodebuilder.prepare.build_graph_attrs).

    frames           : reference frame key -> RefFrame.
    primary_interval : the primary frame's interval when the caller knows it
                       (else it is read from the bar spacing).
    """
    frames: dict = field(default_factory=dict)
    primary_interval: Optional[str] = None

    def missing(self, roles: TickerRoles) -> list[ReferenceTicker]:
        """The references of *roles* that have no frame here."""
        return [r for r in roles.references if r.key not in self.frames]


def ref_frame(key: tuple[str, str], df: pd.DataFrame) -> RefFrame:
    """A RefFrame from a fetched OHLCV frame (sorted by time)."""
    from nodebuilder.evaluator import bars_from_frame

    if not df.index.is_monotonic_increasing:
        df = df.sort_index(kind="stable")
    return RefFrame(key=key, index=df.index, bars=bars_from_frame(df))


# ---------------------------------------------------------------------------
# The domain cook
# ---------------------------------------------------------------------------


def _aligned_stream(src: Stream, store, pos: np.ndarray, cache: dict, dom,
                    free: frozenset = frozenset()) -> Stream:
    """*src* (a reference-domain stream) on the primary *store*: every point
    column aligned (built lazily, once per reference column), details and
    hidden names as they are, writers unchanged.

    A column written by a free node (no Ticker upstream: a constant, or math
    on constants) is the same on every bar, so it is carried over whole,
    without the reference's warmup gap (NaN before its first bar)."""
    points = {}
    whole = np.where(pos < 0, 0, pos) if len(pos) else pos
    for name, ref in src.points.items():
        ck = (dom, ref.key)
        key = cache.get(ck)
        if key is None:
            ref_store, dtype, rkey = src.store, ref.dtype, ref.key
            at = whole if (ref.writer in free and src.store.length) else pos
            key = store.put_lazy(
                lambda s=ref_store, k=rkey, d=dtype, p=at: align_values(s.get(k), p, d))
            cache[ck] = key
        points[name] = PointRef(key, ref.dtype, ref.writer)
    return Stream(store, points, dict(src.detail), {}, src.hidden)


def cook_domains(
    program,
    index: pd.Index,
    bars: Mapping[str, Any],
    refs: Optional[ReferenceFrames] = None,
    keep: Optional[set] = None,
) -> CookResult:
    """Cook *program* with each reference frame on its own bars.

    index, bars : the primary frame.
    refs        : the reference frames (missing ones raise ValueError).
    keep        : node ids whose streams the result holds (None: all, the
                  inspector cook, with every reference-domain stream
                  aligned onto the primary index).

    Returns a kernel CookResult on the primary's index, as cook_program
    gives, so every reader (the simulator bridge, the inspector) is the
    same with or without reference Tickers.
    """
    roles = ticker_roles(program)
    steps = list(program.steps)
    if refs is None:
        refs = ReferenceFrames()
    missing = refs.missing(roles)
    if missing:
        r = missing[0]
        raise ValueError(
            f"Reference Ticker {r.node_id!r} reads {r.symbol} {r.interval}, but no bars "
            "were fetched for it.")

    domains = step_domains(steps, roles)
    free = frozenset(nid for nid, d in domains.items() if d is None)
    by_id = {s.node_id: s for s in steps}
    primary_frames = {tid: bars for tid in roles.primary}
    env_primary = {"bars": bars, "frames": primary_frames}

    # Reference-domain nodes the primary side reads.
    boundaries: dict[str, Any] = {}
    for s in steps:
        if domains.get(s.node_id) != _PRIMARY:
            continue
        for u in s.depends_on():
            dom = domains.get(u)
            if dom is not None and dom != _PRIMARY:
                boundaries[u] = dom

    # 1. One cook per reference frame, every stream kept (a primary node
    # may read any column a boundary stream carries).
    ref_results: dict[tuple, CookResult] = {}
    positions: dict[tuple, np.ndarray] = {}
    for key in roles.keys():
        frame = refs.frames[key]
        mine = {nid for nid, d in domains.items() if d == key}
        if not mine:
            continue
        # Free nodes the reference nodes read (constants...).
        stack = [u for nid in mine for u in by_id[nid].depends_on()]
        while stack:
            u = stack.pop()
            if u in mine or domains.get(u) is not None:
                continue
            mine.add(u)
            stack.extend(by_id[u].depends_on())
        ref_steps = [s for s in steps if s.node_id in mine]
        ref_ids = {r.node_id for r in roles.references if r.key == key}
        env = {"bars": frame.bars, "frames": {tid: frame.bars for tid in ref_ids}}
        ref_results[key] = cook(ref_steps, frame.index, env, keep=None)
        shift = shifts(key[1], frame.index, refs.primary_interval, index)
        positions[key] = align_positions(frame.index, index, shift, ref_interval=key[1])

    # 2. The primary cook: primary and free nodes, with each boundary node
    # replaced by its stream aligned onto the primary's index.
    cache: dict = {}

    def _inject(node_id: str, dom) -> Step:
        src = ref_results[dom].streams[node_id]
        pos = positions[dom]

        def _impl(inputs, params, _src=src, _pos=pos, _dom=dom):
            return _aligned_stream(_src, inputs.store, _pos, cache, _dom, free)

        return Step(node_id=node_id, type=by_id[node_id].type, mode=RUN, impl=_impl,
                    params=Params({}, node_id), inputs=(), pass_from=None,
                    reads=(), writes=(), read_from=(), weight=0)

    primary_steps: list[Step] = []
    for s in steps:
        dom = domains.get(s.node_id)
        if dom == _PRIMARY or dom is None:
            primary_steps.append(s)
        elif s.node_id in boundaries:
            primary_steps.append(_inject(s.node_id, dom))
    result = cook(primary_steps, index, env_primary, keep=keep)

    # 3. Reference-domain streams the caller keeps (every one for the
    # inspector cook), aligned onto the primary's index.
    for key, res in ref_results.items():
        for nid, stream in res.streams.items():
            if domains.get(nid) != key or nid in result.streams:
                continue
            if keep is None or nid in keep:
                result.streams[nid] = _aligned_stream(
                    stream, result.store, positions[key], cache, key, free)
    return result

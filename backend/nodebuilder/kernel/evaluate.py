"""The column evaluator (plan D5).

A compiled graph is a list of Steps, one per node.  ``cook`` runs each
step once over the full index: a running node gets the merge of the streams
on its input ports and returns its output stream; a bypassed (or off) node
passes the stream on its in0 along unchanged.

Cook order is a topological order that runs consumers soon after their
producers.  A stream carries everything upstream of it, so columns are
freed by reads, not by references: compile knows which (writer, name) each
step reads, and a column is dropped right after its last reader runs.  So a
big frame does not keep every intermediate column alive at once.  An impl
may therefore read only the attributes its params name.  The values never
depend on the order: every impl is a pure function of its inputs.

No trading words in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

import numpy as np
import pandas as pd

from nodebuilder.kernel.schema import INACTIVE, PASS, RUN, Analysis, Params
from nodebuilder.kernel.stream import ColumnStore, Stream, merge_streams

EMPTY = "empty"  # a node with no output (an unwired catalog-only node)


@dataclass(frozen=True)
class Step:
    """One node of a compiled graph.

    mode      : "run" (call impl), "pass" (output = the stream on in0) or
                "empty" (no output).
    inputs    : upstream node ids, in port order (merged for a "run" step).
    pass_from : the upstream on in0, for a "pass" step (None: empty stream).
    reads     : the attribute names the node reads.
    writes    : the attribute names the node writes.
    read_from : (writer node id, name) for each read, so the cook knows
                when a column is no longer needed.
    """
    node_id: str
    type: str
    mode: str
    impl: Optional[Callable[..., Any]]
    params: Optional[Params]
    inputs: tuple[str, ...]
    pass_from: Optional[str]
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    read_from: tuple[tuple[Optional[str], str], ...] = ()
    weight: int = 0             # how many non-bool columns it reads (cook order hint)

    @property
    def node_path(self) -> str:
        """The node id (the Wave 0 name of this field)."""
        return self.node_id

    def depends_on(self) -> tuple[str, ...]:
        if self.mode == RUN:
            return self.inputs
        if self.mode == PASS and self.pass_from is not None:
            return (self.pass_from,)
        return ()


def build_steps(analysis: Analysis) -> tuple[Step, ...]:
    """One Step per checked node, in topological order."""
    steps: list[Step] = []
    for nid in analysis.order:
        res = analysis.nodes.get(nid)
        if res is None or res.status not in (RUN, PASS, INACTIVE):
            continue
        mode = {RUN: RUN, PASS: PASS, INACTIVE: EMPTY}[res.status]
        impl = res.node_type.impl if (res.node_type is not None and mode == RUN) else None
        steps.append(Step(
            node_id=nid, type=res.type, mode=mode, impl=impl, params=res.params,
            inputs=res.inputs if mode == RUN else (), pass_from=res.pass_from,
            reads=res.read_names if mode == RUN else (),
            writes=res.writes if mode == RUN else (),
            read_from=_read_sources(res) if mode == RUN else (),
            weight=_weight(res) if mode == RUN else 0,
        ))
    return tuple(steps)


def _weight(res) -> int:
    n = 0
    for r in res.reads.values():
        for item in (r if isinstance(r, list) else [r]):
            if item.status == "live" and item.dtype != "bool":
                n += 1
    return n


def _read_sources(res) -> tuple[tuple[Optional[str], str], ...]:
    out = []
    for r in res.reads.values():
        for item in (r if isinstance(r, list) else [r]):
            if item.status == "live" and item.name is not None:
                out.append((item.writer, item.name))
    return tuple(out)


def cook_order(steps: Iterable[Step]) -> tuple[Step, ...]:
    """A topological order that runs each consumer chain right after its
    producers (depth first from the sinks), so fewer columns are alive at
    once.

    Among a node's inputs, the branch holding the node that reads the most
    number columns goes first: such a node (one that reads three) tends to
    build the biggest temporaries, so it runs while little else is alive.  Any
    topological order gives the same values; this only lowers peak memory.
    *steps* must be in topological order.
    """
    steps = list(steps)
    by_id = {s.node_id: s for s in steps}
    consumed: set[str] = set()
    heavy: dict[str, int] = {}
    for s in steps:
        deps = [u for u in s.depends_on() if u in by_id]
        consumed.update(deps)
        heavy[s.node_id] = max([s.weight] + [heavy[u] for u in deps])
    sinks = [s.node_id for s in steps if s.node_id not in consumed]
    done: set[str] = set()
    out: list[Step] = []
    for sink in sinks:
        stack: list[tuple[str, int]] = [(sink, 0)]
        while stack:
            nid, i = stack.pop()
            if nid in done:
                continue
            deps = sorted((u for u in by_id[nid].depends_on() if u in by_id),
                          key=lambda u: -heavy[u])
            while i < len(deps) and deps[i] in done:
                i += 1
            if i < len(deps):
                stack.append((nid, i + 1))
                stack.append((deps[i], 0))
                continue
            done.add(nid)
            out.append(by_id[nid])
    # Anything not reached (cannot happen in a DAG) keeps its place.
    out.extend(s for s in steps if s.node_id not in done)
    return tuple(out)


class CookResult:
    """The streams one cook produced (only the ones asked to be kept)."""

    def __init__(self, store: ColumnStore, streams: dict[str, Stream]) -> None:
        self.store = store
        self.streams = streams

    @property
    def index(self) -> pd.Index:
        return self.store.index

    def stream(self, node_id: str) -> Stream:
        return self.streams[node_id]

    def column(self, node_id: str, name: str) -> np.ndarray:
        return self.streams[node_id].column(name)


def cook(
    steps: Iterable[Step],
    index: pd.Index,
    env: Optional[Mapping[str, Any]] = None,
    keep: Optional[Iterable[str]] = None,
) -> CookResult:
    """Run *steps* once over *index*.

    env  : passed to every impl as ``params.env`` (the domain layer puts its
           input frames there).  The cook adds ``memo``, a per-cook cache an
           impl may use through ``memo_columns``.
    keep : node ids whose output streams the result must hold.  None keeps
           every stream and column (for an inspector).  A set keeps those
           streams and the columns those nodes read and write; every other
           stream and column is freed once nothing below needs it.
    """
    order = cook_order(steps)
    store = ColumnStore(index)
    run_env = dict(env or {})
    run_env.setdefault("memo", {})
    keep_all = keep is None
    keep_set = set(keep or ())

    remaining: dict[str, int] = {}
    for s in order:
        for u in s.depends_on():
            remaining[u] = remaining.get(u, 0) + 1

    # When each (writer, name) column is read for the last time.  Take the
    # max over every reader: a kept node (a terminal) may read a column that
    # a later step reads too, and that later read must not shorten the kept
    # read's "forever" (F435 W2 KC-1: it freed Entry's column, KeyError).
    forever = len(order)
    last_use: dict[tuple, int] = {}
    for i, s in enumerate(order):
        use = forever if s.node_id in keep_set else i
        for src in s.read_from:
            last_use[src] = max(last_use.get(src, -1), use)
    key_last: dict[int, int] = {}

    streams: dict[str, Stream] = {}
    for i, s in enumerate(order):
        if s.mode == RUN:
            ins = [streams[u] for u in s.inputs]
            merged = merge_streams(ins) if ins else Stream.empty(store)
            params = s.params.with_env(run_env) if s.params is not None else Params({}, s.node_id, run_env)
            out = s.impl(merged, params) if s.impl is not None else merged
        elif s.mode == PASS and s.pass_from is not None:
            out = streams[s.pass_from]
        else:
            out = Stream.empty(store)
        streams[s.node_id] = out
        if keep_all:
            continue
        # Streams nothing below needs any more.
        for u in s.depends_on():
            remaining[u] -= 1
            if remaining[u] == 0 and u not in keep_set:
                streams.pop(u, None)
        if remaining.get(s.node_id, 0) == 0 and s.node_id not in keep_set:
            streams.pop(s.node_id, None)
        # Columns: note when the ones this step wrote are last read, then
        # drop every column whose last reader has run.
        if s.mode == RUN:
            for name in s.writes:
                ref = out.points.get(name)
                if ref is not None and ref.writer == s.node_id:
                    last = forever if s.node_id in keep_set else last_use.get((s.node_id, name), -1)
                    key_last[ref.key] = max(key_last.get(ref.key, -1), last)
        for key in [k for k, last in key_last.items() if last <= i]:
            store.drop(key)
            del key_last[key]
    return CookResult(store, streams)


def memo_columns(
    inputs: Stream, params: Params, key: tuple, build: Callable[[], tuple],
) -> tuple[int, ...]:
    """Store the columns *build* returns, once per cook for the same *key*.

    Two nodes asking for the same computation (two identical nodes on one
    input) share one set of columns.  Returns the column keys; add them to a
    stream with ``Stream.with_point_key``.
    """
    store = inputs.store
    cache = params.env.get("memo") if params.env else None
    if cache is not None:
        keys = cache.get(key)
        if keys is not None and all(k in store for k in keys):
            return keys
    keys = tuple(store.put(a) for a in build())
    if cache is not None:
        cache[key] = keys
    return keys

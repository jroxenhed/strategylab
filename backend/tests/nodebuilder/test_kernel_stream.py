"""The kernel: Stream, ColumnStore, merge rules, StreamSchema, and the cook
mechanics (F435 W2, plan D4, D5, section 3).  Nothing here is about trading."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nodebuilder.kernel.evaluate import Step, cook, cook_order, memo_columns
from nodebuilder.kernel.schema import AttrInfo, Hidden, Params, StreamSchema, merge_schemas
from nodebuilder.kernel.stream import (
    PRIM_KINDS,
    STREAM_SCHEMA_VERSION,
    ColumnStore,
    Stream,
    is_attr_name,
    merge_streams,
    prim_kind_of,
)


def _store(n: int = 4) -> ColumnStore:
    return ColumnStore(pd.RangeIndex(n))


# ---------------------------------------------------------------------------
# Names and versions
# ---------------------------------------------------------------------------

def test_version_and_reserved_kinds():
    from nodebuilder.models import STREAM_SCHEMA_VERSION as models_version

    assert STREAM_SCHEMA_VERSION == 1 and models_version == STREAM_SCHEMA_VERSION
    assert PRIM_KINDS == ("trade", "session", "regime_period")


@pytest.mark.parametrize("name,ok", [
    ("@close", True), ("@rsi_2", True), ("@_x", True), ("@" + "a" * 64, True),
    ("close", False), ("@Close", False), ("@2x", False), ("@" + "a" * 65, False), (None, False),
])
def test_attr_names(name, ok):
    assert is_attr_name(name) is ok


def test_prim_reads_are_recognised():
    assert prim_kind_of("@trade.pnl") == "trade"
    assert prim_kind_of("@session.open") == "session"
    assert prim_kind_of("@trade_pnl") is None and prim_kind_of("@close") is None


# ---------------------------------------------------------------------------
# Stream and store
# ---------------------------------------------------------------------------

def test_adding_an_attribute_copies_no_column():
    store = _store()
    base = np.array([1.0, 2.0, 3.0, 4.0])
    s1 = Stream.empty(store).with_point("@close", base, "/t")
    s2 = s1.with_point("@x", base * 2, "/n")
    assert s2.column("@close") is s1.column("@close")
    assert np.shares_memory(s1.column("@close"), base)
    assert "@x" not in s1 and s2.names() == ["@close", "@x"]
    assert s2.written_by == {"@close": "/t", "@x": "/n"}


def test_points_detail_and_dtypes():
    s = Stream.empty(_store(3)).with_point("@sig", np.array([True, False, True]), "/a")
    s = s.with_detail("@stop_pct", 2.5, "/s").with_detail("@n", 3, "/s").with_detail("@tag", "x", "/s")
    assert (s.dtype("@sig"), s.kind("@sig")) == ("bool", "point")
    assert (s.dtype("@stop_pct"), s.dtype("@n"), s.dtype("@tag")) == ("float", "int", "str")
    assert s["@stop_pct"] == 2.5 and s["@sig"].tolist() == [True, False, True]
    assert s.column("@stop_pct").tolist() == [2.5, 2.5, 2.5]  # detail broadcast
    # One name lives in one class: writing a point replaces the detail.
    s2 = s.with_point("@n", np.zeros(3), "/b")
    assert s2.kind("@n") == "point" and "@n" not in s2.detail


def test_a_column_must_fit_the_index():
    with pytest.raises(ValueError):
        Stream.empty(_store(4)).with_point("@x", np.zeros(3), "/a")


def test_lazy_columns_build_on_first_read():
    built = []
    store = _store(3)
    s = Stream.empty(store).with_lazy_point(
        "@index", lambda: built.append(1) or np.arange(3.0), "float", "/t")
    assert built == []
    assert s.column("@index").tolist() == [0.0, 1.0, 2.0] and built == [1]
    s.column("@index")
    assert built == [1]


def test_stream_schema_serializes_as_plan_3_3():
    s = (Stream.empty(_store(2))
         .with_point("@close", np.zeros(2), "n_t1")
         .with_point("@xb_rsi", np.zeros(2, dtype=bool), "n_xb")
         .with_detail("@stop_pct", 2.0, "n_sl"))
    assert s.schema().to_json() == {
        "stream_schema": 1,
        "points": [
            {"name": "@close", "dtype": "float", "written_by": "n_t1"},
            {"name": "@xb_rsi", "dtype": "bool", "written_by": "n_xb"},
        ],
        "detail": [{"name": "@stop_pct", "dtype": "float", "written_by": "n_sl"}],
        "prims": [],
    }


# ---------------------------------------------------------------------------
# Merge rules (plan D4): same writer is one attribute, two writers clash
# ---------------------------------------------------------------------------

def test_merge_keeps_one_attribute_from_one_writer():
    store = _store()
    t = Stream.empty(store).with_point("@close", np.zeros(4), "/t")
    left = t.with_point("@a", np.ones(4), "/a")
    right = t.with_point("@b", np.ones(4), "/b")
    merged = merge_streams([left, right])
    assert merged.names() == ["@close", "@a", "@b"]  # port order
    assert not merged.hidden


def test_merge_hides_a_name_from_two_writers():
    store = _store()
    left = Stream.empty(store).with_point("@r", np.zeros(4), "/r1")
    right = Stream.empty(store).with_point("@r", np.ones(4), "/r2")
    merged = merge_streams([left, right])
    assert "@r" not in merged and merged.hidden == {"@r"}
    # A node below can write the name again.
    assert "@r" in merged.with_point("@r", np.ones(4), "/n")


def test_schema_merge_matches_stream_merge():
    def info(name, writer):
        return AttrInfo(name, "float", writer)

    a = StreamSchema(points={"@close": info("@close", "/t"), "@r": info("@r", "/r1")})
    b = StreamSchema(points={"@close": info("@close", "/t"), "@r": info("@r", "/r2")},
                     disabled={"@x": info("@x", "/off")})
    merged, clashes = merge_schemas([a, b], origin="/n")
    assert list(merged.points) == ["@close"]
    assert merged.hidden["@r"] == Hidden("/n", ("/r1", "/r2"))
    assert clashes == {"@r": ("/r1", "/r2")}
    assert "@x" in merged.disabled
    # A live name wins over a disabled one.
    c = StreamSchema(points={"@x": info("@x", "/live")})
    merged2, _ = merge_schemas([b, c])
    assert "@x" in merged2.points and "@x" not in merged2.disabled


# ---------------------------------------------------------------------------
# Cook mechanics (with toy impls: the kernel knows nothing of trading)
# ---------------------------------------------------------------------------

def _source(inputs, p):
    return inputs.with_point("@v", np.asarray(p.env["values"], dtype=float), p.node_id)


def _plus(k):
    def impl(inputs, p):
        return inputs.with_point(p["out"], inputs.column(p["a"]) + k, p.node_id)
    return impl


def _step(nid, impl, inputs=(), reads=(), writes=(), read_from=(), mode="run", pass_from=None,
          weight=0, **params):
    return Step(nid, "toy", mode, impl, Params(params, nid), tuple(inputs), pass_from,
                tuple(reads), tuple(writes), tuple(read_from), weight)


def _chain():
    return (
        _step("/src", _source, writes=("@v",)),
        _step("/p1", _plus(1), inputs=("/src",), reads=("@v",), writes=("@p1",),
              read_from=(("/src", "@v"),), a="@v", out="@p1"),
        _step("/p2", _plus(10), inputs=("/p1",), reads=("@p1",), writes=("@p2",),
              read_from=(("/p1", "@p1"),), a="@p1", out="@p2"),
    )


def test_cook_runs_every_step_once_over_the_index():
    result = cook(_chain(), pd.RangeIndex(3), {"values": [1, 2, 3]}, keep=None)
    assert result.column("/p2", "@p2").tolist() == [12.0, 13.0, 14.0]
    assert set(result.streams) == {"/src", "/p1", "/p2"}


def test_cook_frees_columns_after_their_last_reader():
    result = cook(_chain(), pd.RangeIndex(3), {"values": [1, 2, 3]}, keep={"/p2"})
    assert set(result.streams) == {"/p2"}
    # @v was freed after /p1 read it; the kept node's read (@p1) and write
    # (@p2) stay.
    assert len(result.store) == 2
    assert result.column("/p2", "@p1").tolist() == [2.0, 3.0, 4.0]
    assert result.column("/p2", "@p2").tolist() == [12.0, 13.0, 14.0]
    with pytest.raises(KeyError):
        result.column("/p2", "@v")


def test_pass_steps_hand_their_in0_stream_on():
    steps = _chain()[:2] + (
        _step("/byp", None, mode="pass", pass_from="/p1"),
    )
    result = cook(steps, pd.RangeIndex(2), {"values": [5, 6]})
    assert result.stream("/byp") is result.stream("/p1")


def test_cook_order_is_topological_and_runs_heavy_branches_first():
    light = _step("/light", _plus(1), inputs=("/src",), weight=1)
    heavy = _step("/heavy", _plus(1), inputs=("/src",), weight=3)
    join = _step("/join", None, inputs=("/light", "/heavy"))
    order = [s.node_id for s in cook_order([_step("/src", _source), light, heavy, join])]
    assert order == ["/src", "/heavy", "/light", "/join"]


def test_memo_shares_one_computation_per_cook():
    store = _store(2)
    inputs = Stream.empty(store)
    params = Params({}, "/n", {"memo": {}})
    calls = []

    def build():
        calls.append(1)
        return (np.zeros(2),)

    k1 = memo_columns(inputs, params, ("x", 1), build)
    k2 = memo_columns(inputs, params, ("x", 1), build)
    assert k1 == k2 and calls == [1]
    store.drop(k1[0])  # freed: the next ask builds it again
    memo_columns(inputs, params, ("x", 1), build)
    assert calls == [1, 1]

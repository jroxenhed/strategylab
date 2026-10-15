"""sl helper parity (F435 W7 item 7.A, design note 4.5).

Each helper must equal its node's output exactly, NaN warmup included
(``assert_series_equal(check_exact=True)``), on a real cached frame and on
a synthetic frame with gaps (missing days and NaN closes).  The node side
is a real compiled graph cooked by the evaluator; the helper side gets the
Ticker's own @close column, as user code would.
"""
from __future__ import annotations

import inspect
import os
import pickle

import numpy as np
import pandas as pd
import pytest

from nodebuilder.code import sl
from nodebuilder.compile import compile as nb_compile
from nodebuilder.evaluator import cook_program
from nodebuilder.kernel import registry
from nodebuilder.models import Graph

_FIXTURES = os.path.join(os.path.dirname(__file__), os.pardir, "fixtures", "run_backtest_snapshots")


def _real_frame() -> pd.DataFrame:
    with open(os.path.join(_FIXTURES, "macd_crossover_df.pkl"), "rb") as fh:
        return pickle.load(fh)


def _gappy_frame() -> pd.DataFrame:
    rng = np.random.default_rng(11)
    idx = pd.bdate_range("2024-01-01", periods=260, tz="America/New_York")
    idx = idx.delete([5, 6, 7, 40, 41, 120])           # missing days
    n = len(idx)
    close = 50 + np.cumsum(rng.normal(0, 1, n))
    close[[30, 31, 90, 200]] = np.nan                 # missing closes
    return pd.DataFrame({"Open": close + rng.normal(0, 0.5, n), "High": close + 1.5,
                         "Low": close - 1.5, "Close": close, "Volume": rng.integers(1, 9, n)},
                        index=idx)


FRAMES = {"real": _real_frame, "gaps": _gappy_frame}


@pytest.fixture(params=list(FRAMES), scope="module")
def frame(request) -> pd.DataFrame:
    return FRAMES[request.param]()


def _cook(df: pd.DataFrame, chain: list[tuple[str, str, dict]], last_out: str):
    """Ticker -> chain (each node wired from the one before) -> above 0 -> Entry.
    Returns (cook result, index)."""
    nodes = {"/t": {"id": "/t", "type": "ticker", "params": {}}}
    wires = []
    prev = "/t"
    for nid, ntype, params in chain:
        nodes[nid] = {"id": nid, "type": ntype, "params": params}
        wires.append({"id": f"w{len(wires)}", "from": prev, "to": nid, "to_port": "in0"})
        prev = nid
    nodes["/c"] = {"id": "/c", "type": "above", "params": {"a": last_out, "threshold": 0}}
    nodes["/e"] = {"id": "/e", "type": "entry", "params": {}}
    wires.append({"id": "wc", "from": prev, "to": "/c", "to_port": "in0"})
    wires.append({"id": "we", "from": "/c", "to": "/e", "to_port": "in0"})
    graph = Graph.model_validate({"_version": 2, "nodes": nodes, "wires": wires})
    keep = {"/t"} | {nid for nid, _t, _p in chain}
    return cook_program(nb_compile(graph), df, keep=keep)


def _node(df, chain, nid, out) -> pd.Series:
    result = _cook(df, chain, out)
    return pd.Series(result.column(nid, out), index=result.index)


def _close(df) -> pd.Series:
    result = _cook(df, [], "@close")
    return pd.Series(result.column("/t", "@close"), index=result.index)


def _col(df, name) -> pd.Series:
    result = _cook(df, [], "@close")
    return pd.Series(result.column("/t", name), index=result.index)


def _same(got: pd.Series, want: pd.Series, warmup: int = 0) -> None:
    pd.testing.assert_series_equal(got, want, check_exact=True, check_names=False)
    if warmup:
        assert want.iloc[:warmup].isna().all(), "the node should have a NaN warmup here"


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("period", [5, 20])
def test_sma_and_ema_equal_the_ma_sma_and_ema_nodes(frame, period):
    close = _close(frame)
    for kind, fn in (("sma", sl.sma), ("ema", sl.ema)):
        got = fn(close, period)
        _same(got, _node(frame, [("/n", "ma", {"period": period, "type": kind, "out": "@m"})],
                         "/n", "@m"), warmup=period - 1 if kind == "sma" else 0)
        _same(got, _node(frame, [("/n", kind, {"period": period, "out": "@m"})], "/n", "@m"))


def test_rsi_equals_the_rsi_node_with_defaults_and_both_types(frame):
    close = _close(frame)
    _same(sl.rsi(close), _node(frame, [("/n", "rsi", {})], "/n", "@rsi"), warmup=1)
    for kind in ("sma", "wilder"):
        got = sl.rsi(close, 9, type=kind)
        _same(got, _node(frame, [("/n", "rsi", {"period": 9, "type": kind})], "/n", "@rsi"),
              warmup=1)


def test_atr_equals_the_atr_node(frame):
    # The true range has a value on bar 0 (high - low), so the warmup is
    # period - 1 bars.
    got = sl.atr(_col(frame, "@high"), _col(frame, "@low"), _close(frame), 10)
    _same(got, _node(frame, [("/n", "atr", {"period": 10})], "/n", "@atr"), warmup=9)
    got = sl.atr(_col(frame, "@high"), _col(frame, "@low"), _close(frame))
    _same(got, _node(frame, [("/n", "atr", {})], "/n", "@atr"), warmup=13)


def test_macd_equals_the_macd_node(frame):
    line, signal, hist = sl.macd(_close(frame), 8, 21, 5)
    chain = [("/n", "macd", {"fast": 8, "slow": 21, "signal": 5})]
    _same(line, _node(frame, chain, "/n", "@macd_line"))
    _same(signal, _node(frame, chain, "/n", "@macd_signal"))
    _same(hist, _node(frame, chain, "/n", "@macd_histogram"))
    assert sl.macd(_close(frame)).line.equals(_node(frame, [("/n", "macd", {})], "/n", "@macd_line"))


def test_bb_equals_the_bollinger_node(frame):
    out = sl.bb(_close(frame), 15, 1.5)
    chain = [("/n", "bollinger", {"period": 15, "stddev": 1.5})]
    _same(out.upper, _node(frame, chain, "/n", "@bb_upper"), warmup=14)
    _same(out.middle, _node(frame, chain, "/n", "@bb_middle"), warmup=14)
    _same(out.lower, _node(frame, chain, "/n", "@bb_lower"), warmup=14)


def test_an_indicator_of_an_indicator_equals_the_node_chain(frame):
    """EMA of RSI: sl on a derived series equals the wired nodes."""
    got = sl.ema(sl.rsi(_close(frame), 14), 10)
    want = _node(frame, [("/r", "rsi", {}), ("/n", "ema", {"period": 10, "source": "@rsi",
                                                           "out": "@e"})], "/n", "@e")
    _same(got, want, warmup=1)


# ---------------------------------------------------------------------------
# Math
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["mean", "min", "max", "std", "sum"])
def test_rolling_equals_the_rolling_node(frame, op):
    got = sl.rolling(_close(frame), 7, op)
    _same(got, _node(frame, [("/n", "rolling", {"a": "@close", "op": op, "window": 7,
                                                 "out": "@r"})], "/n", "@r"), warmup=6)


def test_zscore_equals_the_rolling_and_math_nodes(frame):
    chain = [
        ("/m", "rolling", {"a": "@close", "op": "mean", "window": 12, "out": "@mean"}),
        ("/s", "rolling", {"a": "@close", "op": "std", "window": 12, "out": "@std"}),
        ("/d", "math", {"op": "sub", "a": "@close", "b": "@mean", "out": "@dev"}),
        ("/n", "math", {"op": "div", "a": "@dev", "b": "@std", "out": "@z"}),
    ]
    _same(sl.zscore(_close(frame), 12), _node(frame, chain, "/n", "@z"), warmup=11)


def test_zscore_is_nan_where_the_std_is_zero():
    flat = pd.Series([5.0] * 10)
    assert sl.zscore(flat, 3).iloc[2:].isna().all()


@pytest.mark.parametrize("n", [1, 3])
def test_shift_equals_the_shift_node(frame, n):
    _same(sl.shift(_close(frame), n),
          _node(frame, [("/n", "shift", {"a": "@close", "bars": n, "out": "@s"})], "/n", "@s"),
          warmup=n)


def test_a_negative_shift_raises():
    with pytest.raises(ValueError, match="future"):
        sl.shift(pd.Series([1.0, 2.0, 3.0]), -1)
    with pytest.raises(ValueError, match="future"):
        sl.shift(pd.Series([1.0, 2.0, 3.0]), -0.5)


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def test_crosses_equal_the_crosses_nodes_with_a_series_and_a_number(frame):
    close, open_ = _close(frame), _col(frame, "@open")
    level = float(np.nanmedian(close))
    for name, fn in (("crosses_above", sl.crosses_above), ("crosses_below", sl.crosses_below)):
        _same(fn(close, open_),
              _node(frame, [("/n", name, {"a": "@close", "b": "@open", "out": "@x"})], "/n", "@x"))
        _same(fn(close, level),
              _node(frame, [("/n", name, {"a": "@close", "threshold": level, "out": "@x"})],
                    "/n", "@x"))
    assert sl.crosses_above(close, open_).iloc[0] is np.False_


@pytest.mark.parametrize("n", [1, 4])
def test_rising_and_falling_equal_the_slope_nodes(frame, n):
    close = _close(frame)
    for up, fn in ((True, sl.rising), (False, sl.falling)):
        if n == 1:
            node, params = ("rising" if up else "falling"), {}
        else:
            node, params = ("rising_over" if up else "falling_over"), {"bars": n}
        _same(fn(close, n),
              _node(frame, [("/n", node, {"a": "@close", "out": "@s", **params})], "/n", "@s"))


def test_bars_since():
    cond = pd.Series([False, False, True, False, False, True, True, False])
    got = sl.bars_since(cond)
    want = pd.Series([np.nan, np.nan, 0.0, 1.0, 2.0, 0.0, 0.0, 1.0])
    pd.testing.assert_series_equal(got, want, check_exact=True)
    # A float condition: non-zero is true, NaN is false.
    got = sl.bars_since(pd.Series([np.nan, 1.0, 0.0, np.nan]))
    pd.testing.assert_series_equal(got, pd.Series([np.nan, 0.0, 1.0, 2.0]), check_exact=True)


# ---------------------------------------------------------------------------
# Defaults, inputs and refusals
# ---------------------------------------------------------------------------


def _default(fn, name):
    return inspect.signature(fn).parameters[name].default


def _catalog(node, param):
    return registry.get(node).param(param).default


def test_defaults_equal_the_node_catalog_defaults():
    assert _default(sl.rsi, "period") == _catalog("rsi", "period")
    assert _default(sl.rsi, "type") is None          # None means: read the catalog
    assert sl.signature("rsi") == f"sl.rsi(x, period=14, type={_catalog('rsi', 'type')!r})"
    assert _default(sl.atr, "period") == _catalog("atr", "period")
    for arg in ("fast", "slow", "signal"):
        assert _default(sl.macd, arg) == _catalog("macd", arg)
    assert _default(sl.bb, "period") == _catalog("bollinger", "period")
    assert _default(sl.bb, "std") == _catalog("bollinger", "stddev")
    assert _default(sl.rolling, "op") == _catalog("rolling", "op")
    assert _default(sl.shift, "n") == _catalog("shift", "bars")


def test_outputs_keep_the_inputs_index_and_arrays_work():
    idx = pd.date_range("2024-01-01", periods=30, freq="D")
    x = pd.Series(np.arange(30, dtype=float), index=idx)
    assert sl.sma(x, 3).index is idx
    from_array = sl.sma(np.arange(30, dtype=float), 3)
    assert isinstance(from_array.index, pd.RangeIndex)
    np.testing.assert_array_equal(from_array.to_numpy(), sl.sma(x, 3).to_numpy())


@pytest.mark.parametrize("call,exc", [
    (lambda x: sl.rsi(x, 14.5), TypeError),
    (lambda x: sl.rsi(x, True), TypeError),
    (lambda x: sl.rsi(x, 14, type="ema"), ValueError),
    (lambda x: sl.rsi(x, 1), ValueError),                  # compute_instance's range check
    (lambda x: sl.rolling(x, 5, "median"), ValueError),
    (lambda x: sl.crosses_above(x, x.iloc[1:]), ValueError),
    (lambda x: sl.sma(pd.DataFrame({"a": x}), 3), TypeError),
    (lambda x: sl.sma(pd.Series(["a"] * len(x)), 3), TypeError),
])
def test_bad_arguments_raise(call, exc):
    with pytest.raises(exc):
        call(pd.Series(np.arange(40, dtype=float)))

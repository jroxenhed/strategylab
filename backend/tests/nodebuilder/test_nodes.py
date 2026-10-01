"""The trading node types, one impl at a time (F435 W2, plan D5).

Each node type is registered by a module in nodebuilder/trading/.  These
tests call a type's impl directly on a hand-made stream, with params as
compile resolves them, and check the column it writes.  The semantics must
match signal_engine.eval_rule: bar 0 False for every comparison and logic
node, a NaN on either side is False, crossovers fire on the crossing bar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from indicators import OHLCVSeries, compute_instance
from nodebuilder.kernel import registry
from nodebuilder.kernel.schema import Params
from nodebuilder.kernel.stream import ColumnStore, Stream
from nodebuilder.nodes import NODE_CATALOG

NAN = float("nan")


def _stream(n: int | None = None, **columns) -> Stream:
    first = next(iter(columns.values()))
    n = n if n is not None else len(first)
    store = ColumnStore(pd.RangeIndex(n))
    out = Stream.empty(store)
    for name, values in columns.items():
        out = out.with_point(f"@{name}", np.asarray(values), "/src")
    return out


def _run(node_type: str, inputs: Stream, env: dict | None = None, **params) -> Stream:
    nt = registry.get(node_type)
    values = {p.name: p.default for p in nt.params}
    values.update(params)
    return nt.impl(inputs, Params(values, "/n", env or {"memo": {}}))


def _col(stream: Stream, name: str) -> list:
    return stream.column(name).tolist()


# ---------------------------------------------------------------------------
# Indicators: the same values as indicators.compute_instance
# ---------------------------------------------------------------------------

@pytest.fixture
def bars():
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1, 120))
    return {"close": close, "high": close + 1.0, "low": close - 1.0}


def _ohlcv(close, high=None, low=None) -> OHLCVSeries:
    c = pd.Series(close)
    return OHLCVSeries(close=c, high=pd.Series(high if high is not None else close),
                       low=pd.Series(low if low is not None else close), volume=c)


@pytest.mark.parametrize("node_type,params,family,args,key,out", [
    ("rsi", {"period": 14, "type": "wilder"}, "rsi", {"period": 14, "type": "wilder"}, "rsi", "@rsi"),
    ("rsi", {"period": 9, "type": "sma"}, "rsi", {"period": 9, "type": "sma"}, "rsi", "@rsi"),
    ("sma", {"period": 20}, "ma", {"period": 20, "type": "sma"}, "ma", "@sma"),
    ("ema", {"period": 20}, "ma", {"period": 20, "type": "ema"}, "ma", "@ema"),
])
def test_single_output_indicators_match_compute_instance(bars, node_type, params, family, args, key, out):
    s = _run(node_type, _stream(close=bars["close"]), source="@close", **params)
    expected = compute_instance(family, args, _ohlcv(bars["close"]))[key].to_numpy()
    np.testing.assert_array_equal(s.column(out), expected)
    assert s.written_by[out] == "/n" and s.dtype(out) == "float"
    assert "@close" in s  # the input flows on


def test_macd_writes_three_named_outputs(bars):
    s = _run("macd", _stream(close=bars["close"]), source="@close")
    expected = compute_instance("macd", {"fast": 12, "slow": 26, "signal": 9}, _ohlcv(bars["close"]))
    np.testing.assert_array_equal(s.column("@macd_line"), expected["macd"].to_numpy())
    np.testing.assert_array_equal(s.column("@macd_signal"), expected["signal"].to_numpy())
    np.testing.assert_array_equal(s.column("@macd_histogram"), expected["histogram"].to_numpy())


def test_bollinger_writes_three_bands(bars):
    s = _run("bollinger", _stream(close=bars["close"]), source="@close", stddev=2.5)
    expected = compute_instance("bb", {"period": 20, "stddev": 2.5}, _ohlcv(bars["close"]))
    for name, key in (("@bb_upper", "upper"), ("@bb_middle", "middle"), ("@bb_lower", "lower")):
        np.testing.assert_array_equal(s.column(name), expected[key].to_numpy())


def test_atr_reads_high_and_low(bars):
    s = _run("atr", _stream(**bars), source="@close")
    expected = compute_instance("atr", {"period": 14},
                                _ohlcv(bars["close"], bars["high"], bars["low"]))["atr"]
    np.testing.assert_array_equal(s.column("@atr"), expected.to_numpy())


def test_indicator_of_an_indicator(bars):
    """EMA of RSI: the EMA's source is the RSI column."""
    rsi = _run("rsi", _stream(close=bars["close"]), source="@close", period=14, type="wilder")
    ema = _run("ema", rsi, source="@rsi", period=5)
    expected = compute_instance("ma", {"period": 5, "type": "ema"}, _ohlcv(rsi.column("@rsi")))["ma"]
    np.testing.assert_array_equal(ema.column("@ema"), expected.to_numpy())


def test_named_write_and_memo(bars):
    """A renamed write lands under its name; the same computation twice in
    one cook shares one column."""
    env = {"memo": {}}
    inputs = _stream(close=bars["close"])
    a = _run("ema", inputs, env=env, source="@close", period=10, out="@fast")
    b = _run("ema", a, env=env, source="@close", period=10, out="@fast_too")
    assert "@fast" in b and b.column_key("@fast") == b.column_key("@fast_too")


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------

def test_below_threshold():
    s = _run("below", _stream(x=[10.0, 20.0, 40.0, NAN, 5.0]), a="@x", threshold=30.0)
    assert _col(s, "@below") == [False, True, False, False, True]  # bar 0 always False
    assert s.dtype("@below") == "bool"


def test_above_two_series():
    s = _run("above", _stream(x=[1.0, 5.0, 2.0, 9.0], y=[0.0, 3.0, 3.0, NAN]), a="@x", b="@y")
    assert _col(s, "@above") == [False, True, False, False]


def test_crosses_above_threshold_fires_on_the_crossing_bar():
    s = _run("crosses_above", _stream(x=[20.0, 25.0, 31.0, 35.0, 28.0, 30.0]), a="@x", threshold=30.0)
    assert _col(s, "@xa") == [False, False, True, False, False, True]


def test_crosses_above_two_series():
    s = _run("crosses_above", _stream(x=[1.0, 1.0, 3.0, 4.0], y=[2.0, 2.0, 2.0, 2.0]), a="@x", b="@y")
    assert _col(s, "@xa") == [False, False, True, False]


def test_crosses_below_two_series_and_nan():
    s = _run("crosses_below", _stream(x=[5.0, 5.0, 1.0, NAN, 1.0], y=[3.0, 3.0, 3.0, 3.0, 3.0]),
             a="@x", b="@y")
    assert _col(s, "@xb") == [False, False, True, False, False]


def test_crossover_of_a_bool_signal():
    """A derived signal is a column, so it has history: 0 -> 1 crosses 0.5."""
    s = _run("crosses_above", _stream(sig=np.array([True, False, True, True, False, True])),
             a="@sig", threshold=0.5)
    assert _col(s, "@xa") == [False, False, True, False, False, True]


def test_bar_zero_is_false_for_every_comparison():
    for node_type in ("above", "below", "crosses_above", "crosses_below"):
        s = _run(node_type, _stream(x=[100.0, 100.0]), a="@x", threshold=0.0 if "above" in node_type else 1e9)
        assert s.column(registry.get(node_type).write_params()[0].default)[0] == False  # noqa: E712


# ---------------------------------------------------------------------------
# Logic
# ---------------------------------------------------------------------------

_A = [True, True, False, True]
_B = [True, False, False, True]


def test_and_or_not():
    inputs = _stream(a=np.array(_A), b=np.array(_B))
    assert _col(_run("and", inputs, terms=["@a", "@b"]), "@and") == [False, False, False, True]
    assert _col(_run("or", inputs, terms=["@a", "@b"]), "@or") == [False, True, False, True]
    assert _col(_run("not", inputs, signal="@a"), "@not") == [False, False, True, False]


def test_logic_with_one_term_still_guards_bar_zero():
    inputs = _stream(a=np.array(_A))
    assert _col(_run("and", inputs, terms=["@a"]), "@and") == [False, True, False, True]


# ---------------------------------------------------------------------------
# Settings and terminals
# ---------------------------------------------------------------------------

def test_settings_write_detail_values():
    empty = Stream.empty(ColumnStore(pd.RangeIndex(3)))
    s = _run("stop_loss", empty, pct=2.5)
    assert s.value("@stop_pct") == 2.5 and s.kind("@stop_pct") == "detail"
    c = _run("commission", empty, per_share_rate=0.0035, min_per_order=0.35)
    assert (c.value("@per_share_rate"), c.value("@min_per_order")) == (0.0035, 0.35)
    # A detail value reads as a full column too.
    assert s.column("@stop_pct").tolist() == [2.5, 2.5, 2.5]


def test_ticker_puts_the_bars_on_the_stream():
    idx = pd.date_range("2024-01-02", periods=3, freq="D", tz="UTC")
    store = ColumnStore(idx)
    bars = {name: np.array([1.0, 2.0, 3.0]) for name in ("@open", "@high", "@low", "@close", "@volume")}
    s = registry.get("ticker").impl(Stream.empty(store), Params({}, "/t", {"bars": bars}))
    assert s.names()[:5] == ["@open", "@high", "@low", "@close", "@volume"]
    assert s.column("@index").tolist() == [0.0, 1.0, 2.0]
    assert s.column("@time")[1] - s.column("@time")[0] == 86400.0
    assert set(s.written_by.values()) == {"/t"}


def test_terminals_write_nothing():
    for name in ("entry", "exit"):
        t = registry.get(name)
        assert t.impl is None and not t.has_output and not t.write_params()


# ---------------------------------------------------------------------------
# Registry coverage
# ---------------------------------------------------------------------------

def test_every_catalog_entry_is_a_registered_type():
    for entry in NODE_CATALOG:
        t = registry.get(entry.name)
        assert t is not None and t.entry is entry
        if entry.compile_active and t.has_output:
            assert t.impl is not None, f"{entry.name} has no impl"


def test_below_fires_exactly_once_on_a_cooked_rsi():
    """Smoke: a noisy series that sells off once: RSI crosses under 30 once."""
    wiggle = np.tile([1.0, -1.0], 30)
    close = 100 + np.concatenate([wiggle, np.linspace(0, -12, 12) + np.tile([0.3, -0.3], 6), wiggle])
    rsi = _run("rsi", _stream(close=close), source="@close", period=14, type="sma")
    below = _run("crosses_below", rsi, a="@rsi", threshold=30.0)
    rule_like = [(rsi.column("@rsi")[i - 1] > 30.0 >= rsi.column("@rsi")[i]) for i in range(1, len(close))]
    assert int(below.column("@xb").sum()) == sum(rule_like) == 1

"""Math and stream nodes (F435 W2 item 2.C): constant, math, shift, rolling,
xor, merge.  Plus a check that every 2.C type reached the catalog through
the registry alone.  Synthetic data only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nodebuilder import nodes
from nodebuilder.compile import compile as nb_compile, compile_with_diagnostics
from nodebuilder.evaluator import cook_program, cook_signals
from nodebuilder.kernel import registry
from nodebuilder.models import Graph

N = 120

NEW_TYPES = (
    "rising", "falling", "rising_over", "falling_over", "turns_up", "turns_down",
    "turns_up_below", "turns_down_above", "accelerating", "decelerating",
    "stochastic", "adx", "atr_pct", "volume", "ma", "price",
    "xor", "constant", "math", "shift", "rolling", "merge",
    "time_of_day", "day_of_week", "session_bar",
)


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    rng = np.random.default_rng(4)
    close = 50 + np.round(np.cumsum(rng.normal(0, 1, N)), 1)
    close[30] = 0.0                      # a zero to divide by
    idx = pd.date_range("2024-03-01", periods=N, freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": close + 1, "High": close + 2, "Low": close - 2,
                         "Close": close, "Volume": np.arange(N, dtype=float)}, index=idx)


def _graph(nodes_: dict, wires: list, bypass: tuple = ()) -> Graph:
    return Graph.model_validate({
        "_version": 2,
        "nodes": {nid: {"id": nid, "type": t, "params": p, "bypass": nid in bypass}
                  for nid, (t, p) in nodes_.items()},
        "wires": [{"id": f"w{i}", "from": a, "to": b, "to_port": port}
                  for i, (a, b, port) in enumerate(wires)],
    })


def _one(df, node_type: str, params: dict, name: str, extra: dict | None = None,
         extra_wires: list | None = None) -> np.ndarray:
    """Ticker -> node -> (above 0) -> Entry; returns the node's column *name*."""
    g = _graph({"/t": ("ticker", {}), "/n": (node_type, params), **(extra or {}),
                "/c": ("above", {"a": name, "threshold": 0}), "/e": ("entry", {})},
               [("/t", "/n", "in0"), *(extra_wires or []), ("/n", "/c", "in0"),
                ("/c", "/e", "in0")])
    return cook_program(nb_compile(g), df, keep={"/n"}).column("/n", name)


def _codes(g: Graph) -> set[str]:
    _program, diags = compile_with_diagnostics(g)
    return {d.code for d in diags}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_every_new_type_is_in_the_catalog():
    names = {e.name for e in nodes.catalog_entries()}
    missing = [t for t in NEW_TYPES if t not in names]
    assert not missing
    for t in NEW_TYPES:
        nt = registry.get(t)
        assert nt.module.startswith("nodebuilder.trading.nodes_"), t
        assert nt.entry.cat in nodes.NODE_CATEGORIES, t
        for p in nt.params:
            assert p.unit is None or p.unit in nodes.PARAM_UNITS, (t, p.name)
        nt.entry.to_json()  # serialisable


# ---------------------------------------------------------------------------
# constant
# ---------------------------------------------------------------------------


def test_constant_as_column(df):
    col = _one(df, "constant", {"value": 2.5}, "@const")
    np.testing.assert_array_equal(col, np.full(N, 2.5))


def test_constant_as_detail_is_a_detail_value(df):
    g = _graph({"/t": ("ticker", {}), "/k": ("constant", {"value": 70, "as_detail": True}),
                "/c": ("above", {"a": "@close", "b": "@const"}), "/e": ("entry", {})},
               [("/t", "/k", "in0"), ("/k", "/c", "in0"), ("/c", "/e", "in0")])
    result = cook_program(nb_compile(g), df, keep={"/k"})
    stream = result.stream("/k")
    assert stream.kind("@const") == "detail"
    assert stream.value("@const") == 70.0
    entry, _ = cook_signals(nb_compile(g), df)
    want = df["Close"].to_numpy() > 70
    want[0] = False
    np.testing.assert_array_equal(entry, want)


@pytest.mark.parametrize("flag", [True, "true", "TRUE"])
def test_constant_as_detail_shows_as_detail_in_the_schema(df, flag):
    """The static schema (/validate streams) agrees with the cook: @const is a
    detail value when as_detail is on, a point otherwise.  The editor stores a
    bool as the text "true", which reads the same."""
    from nodebuilder.compile import check_graph

    def schema(as_detail):
        g = _graph({"/t": ("ticker", {}), "/k": ("constant", {"value": 70, "as_detail": as_detail}),
                    "/c": ("above", {"a": "@close", "b": "@const"}), "/e": ("entry", {})},
                   [("/t", "/k", "in0"), ("/k", "/c", "in0"), ("/c", "/e", "in0")])
        check = check_graph(g)
        assert not [d for d in check.diagnostics if d.severity == "error"], check.diagnostics
        return g, check.streams_json()["/k"]

    g, on = schema(flag)
    assert "@const" in {a["name"] for a in on["detail"]}
    assert "@const" not in {a["name"] for a in on["points"]}
    entry, _ = cook_signals(nb_compile(g), df)
    want = df["Close"].to_numpy() > 70
    want[0] = False
    np.testing.assert_array_equal(entry, want)

    _g, off = schema("false")
    assert "@const" in {a["name"] for a in off["points"]}
    assert "@const" not in {a["name"] for a in off["detail"]}


def test_constant_without_input_is_a_source(df):
    """Unwired, a constant is a source; wired into b it feeds a comparison."""
    g = _graph({"/t": ("ticker", {}), "/k": ("constant", {"value": 55}),
                "/c": ("below", {"a": "@close", "b": "@const"}), "/e": ("entry", {})},
               [("/t", "/c", "in0"), ("/k", "/c", "in1"), ("/c", "/e", "in0")])
    entry, _ = cook_signals(nb_compile(g), df)
    want = df["Close"].to_numpy() < 55
    want[0] = False
    np.testing.assert_array_equal(entry, want)


def test_constant_bad_params(df):
    for params, code in (({"value": "x"}, "param_invalid"),
                         ({"as_detail": "yes"}, "param_invalid")):
        g = _graph({"/k": ("constant", params), "/c": ("above", {"a": "@const", "threshold": 0}),
                    "/e": ("entry", {})}, [("/k", "/c", "in0"), ("/c", "/e", "in0")])
        assert code in _codes(g)


# ---------------------------------------------------------------------------
# math
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op,fn", [
    ("add", np.add), ("sub", np.subtract), ("mul", np.multiply),
    ("min", np.minimum), ("max", np.maximum),
])
def test_math_binary(df, op, fn):
    col = _one(df, "math", {"op": op, "a": "@high", "b": "@close"}, "@math")
    np.testing.assert_array_equal(col, fn(df["High"].to_numpy(), df["Close"].to_numpy()))


def test_math_operand_order_comes_from_params(df):
    col = _one(df, "math", {"op": "sub", "a": "@close", "b": "@high"}, "@math")
    np.testing.assert_array_equal(col, df["Close"].to_numpy() - df["High"].to_numpy())


def test_math_divide_by_zero_is_nan(df):
    col = _one(df, "math", {"op": "div", "a": "@high", "b": "@close"}, "@math")
    close = df["Close"].to_numpy()
    assert close[30] == 0
    assert np.isnan(col[30])
    ok = close != 0
    np.testing.assert_array_equal(col[ok], df["High"].to_numpy()[ok] / close[ok])
    assert np.isfinite(col[ok]).all()


def test_math_zero_over_zero_is_nan(df):
    g = _graph({"/t": ("ticker", {}), "/z": ("constant", {"value": 0, "out": "@z"}),
                "/n": ("math", {"op": "div", "a": "@z", "b": "@z"}),
                "/c": ("above", {"a": "@math", "threshold": 0}), "/e": ("entry", {})},
               [("/t", "/z", "in0"), ("/z", "/n", "in0"), ("/n", "/c", "in0"),
                ("/c", "/e", "in0")])
    col = cook_program(nb_compile(g), df, keep={"/n"}).column("/n", "@math")
    assert np.isnan(col).all()


def test_math_nan_stays_nan_in_min(df):
    g = _graph({"/t": ("ticker", {}), "/s": ("sma", {"period": 5}),
                "/n": ("math", {"op": "min", "a": "@sma", "b": "@close"}),
                "/c": ("above", {"a": "@math", "threshold": 0}), "/e": ("entry", {})},
               [("/t", "/s", "in0"), ("/s", "/n", "in0"), ("/n", "/c", "in0"),
                ("/c", "/e", "in0")])
    col = cook_program(nb_compile(g), df, keep={"/n"}).column("/n", "@math")
    assert np.isnan(col[:4]).all() and not np.isnan(col[4:]).any()


@pytest.mark.parametrize("op,fn", [("abs", np.abs), ("neg", np.negative)])
def test_math_unary_needs_no_b(df, op, fn):
    col = _one(df, "math", {"op": op, "a": "@close"}, "@math")
    np.testing.assert_array_equal(col, fn(df["Close"].to_numpy()))


def test_math_binary_without_b_is_missing_input():
    g = _graph({"/t": ("ticker", {}), "/n": ("math", {"op": "add"}),
                "/c": ("above", {"a": "@math", "threshold": 0}), "/e": ("entry", {})},
               [("/t", "/n", "in0"), ("/n", "/c", "in0"), ("/c", "/e", "in0")])
    assert "missing_input" in _codes(g)


def test_math_unknown_op_is_refused():
    g = _graph({"/t": ("ticker", {}), "/n": ("math", {"op": "pow", "a": "@close", "b": "@high"}),
                "/c": ("above", {"a": "@math", "threshold": 0}), "/e": ("entry", {})},
               [("/t", "/n", "in0"), ("/n", "/c", "in0"), ("/c", "/e", "in0")])
    assert "param_invalid" in _codes(g)


def test_math_with_bypassed_b_is_off(df):
    """b from a bypassed node turns math off: it passes its input on."""
    g = _graph({"/t": ("ticker", {}), "/s": ("sma", {"period": 5}),
                "/n": ("math", {"op": "add", "a": "@close", "b": "@sma"}),
                "/c": ("above", {"a": "@close", "threshold": 0}), "/e": ("entry", {})},
               [("/t", "/s", "in0"), ("/s", "/n", "in0"), ("/n", "/c", "in0"),
                ("/c", "/e", "in0")], bypass=("/s",))
    program = nb_compile(g)
    assert program.step("/n").mode == "pass"


def test_math_default_reads_follow_ports(df):
    """Empty a and b read what the nodes on in0 and in1 write first."""
    g = _graph({"/t": ("ticker", {}), "/s": ("sma", {"period": 3}),
                "/k": ("constant", {"value": 2}),
                "/n": ("math", {"op": "mul"}),
                "/c": ("above", {"a": "@math", "threshold": 0}), "/e": ("entry", {})},
               [("/t", "/s", "in0"), ("/s", "/n", "in0"), ("/k", "/n", "in1"),
                ("/n", "/c", "in0"), ("/c", "/e", "in0")])
    result = cook_program(nb_compile(g), df, keep={"/n"})
    sma = pd.Series(df["Close"].to_numpy()).rolling(3).mean().to_numpy()
    np.testing.assert_array_equal(result.column("/n", "@math"), sma * 2)


# ---------------------------------------------------------------------------
# shift and rolling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bars", [1, 3, 500])
def test_shift(df, bars):
    col = _one(df, "shift", {"a": "@close", "bars": bars}, "@shift")
    want = pd.Series(df["Close"].to_numpy()).shift(bars).to_numpy()
    np.testing.assert_array_equal(col, want)


def test_shift_refuses_zero_and_lookahead():
    for bars in (0, -1):
        g = _graph({"/t": ("ticker", {}), "/n": ("shift", {"bars": bars}),
                    "/c": ("above", {"a": "@shift", "threshold": 0}), "/e": ("entry", {})},
                   [("/t", "/n", "in0"), ("/n", "/c", "in0"), ("/c", "/e", "in0")])
        assert "param_out_of_range" in _codes(g)


@pytest.mark.parametrize("op", ["mean", "min", "max", "std", "sum"])
def test_rolling(df, op):
    col = _one(df, "rolling", {"a": "@close", "op": op, "window": 7}, "@rolling")
    want = getattr(pd.Series(df["Close"].to_numpy()).rolling(7), op)().to_numpy()
    np.testing.assert_array_equal(col, want)
    assert np.isnan(col[:6]).all()


def test_rolling_std_matches_bollinger_width(df):
    """rolling std is the sample std Bollinger uses: upper - middle = 2 * std."""
    g = _graph({"/t": ("ticker", {}), "/b": ("bollinger", {"period": 20, "stddev": 2}),
                "/n": ("rolling", {"a": "@close", "op": "std", "window": 20}),
                "/c": ("above", {"a": "@rolling", "threshold": 0}), "/e": ("entry", {})},
               [("/t", "/b", "in0"), ("/b", "/n", "in0"), ("/n", "/c", "in0"),
                ("/c", "/e", "in0")])
    result = cook_program(nb_compile(g), df, keep_all=True)
    std = result.column("/n", "@rolling")
    width = result.column("/n", "@bb_upper") - result.column("/n", "@bb_middle")
    np.testing.assert_allclose(width, 2 * std, rtol=1e-12, equal_nan=True)


def test_shift_and_rolling_lookback():
    g = _graph({"/t": ("ticker", {}), "/s": ("shift", {"bars": 5}),
                "/r": ("rolling", {"a": "@shift", "window": 30}),
                "/c": ("above", {"a": "@rolling", "threshold": 0}), "/e": ("entry", {})},
               [("/t", "/s", "in0"), ("/s", "/r", "in0"), ("/r", "/c", "in0"),
                ("/c", "/e", "in0")])
    assert nb_compile(g).required_lookback_bars == 35


# ---------------------------------------------------------------------------
# xor
# ---------------------------------------------------------------------------


def _xor_graph(n_terms: int) -> Graph:
    thresholds = [45, 50, 55][:n_terms]
    nodes_ = {"/t": ("ticker", {})}
    wires = []
    for k, t in enumerate(thresholds):
        nodes_[f"/c{k}"] = ("above", {"a": "@close", "threshold": t, "out": f"@c{k}"})
        wires += [("/t", f"/c{k}", "in0"), (f"/c{k}", "/x", f"in{k}")]
    nodes_["/x"] = ("xor", {})
    nodes_["/e"] = ("entry", {})
    wires.append(("/x", "/e", "in0"))
    return _graph(nodes_, wires)


@pytest.mark.parametrize("n_terms", [2, 3])
def test_xor_is_odd_parity(df, n_terms):
    close = df["Close"].to_numpy()
    terms = [close > t for t in [45, 50, 55][:n_terms]]
    want = np.logical_xor.reduce(terms)
    want[0] = False
    entry, _ = cook_signals(nb_compile(_xor_graph(n_terms)), df)
    np.testing.assert_array_equal(entry, want)
    assert entry.any() and not entry.all()


def test_xor_refuses_a_number():
    g = _graph({"/t": ("ticker", {}), "/x": ("xor", {"terms": ["@close"]}), "/e": ("entry", {})},
               [("/t", "/x", "in0"), ("/x", "/e", "in0")])
    assert "attr_type" in _codes(g)


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def test_merge_joins_two_branches(df):
    """Two indicator branches meet in a merge; one comparison below reads both."""
    g = _graph({"/t": ("ticker", {}), "/f": ("ema", {"period": 5, "out": "@fast"}),
                "/s": ("ema", {"period": 20, "out": "@slow"}), "/m": ("merge", {}),
                "/c": ("crosses_above", {"a": "@fast", "b": "@slow"}), "/e": ("entry", {})},
               [("/t", "/f", "in0"), ("/t", "/s", "in0"), ("/f", "/m", "in0"),
                ("/s", "/m", "in1"), ("/m", "/c", "in0"), ("/c", "/e", "in0")])
    program = nb_compile(g)
    names = {a["name"] for a in program.stream_schemas_json()["/m"]["points"]}
    assert {"@fast", "@slow", "@close"} <= names
    entry, _ = cook_signals(program, df)
    close = pd.Series(df["Close"].to_numpy())
    fast = close.ewm(span=5, adjust=False).mean().to_numpy()
    slow = close.ewm(span=20, adjust=False).mean().to_numpy()
    want = np.r_[False, (fast[:-1] < slow[:-1]) & (fast[1:] >= slow[1:])]
    np.testing.assert_array_equal(entry, want)
    assert entry.any()


def test_merge_clash_is_an_error_when_read():
    g = _graph({"/t": ("ticker", {}), "/a": ("sma", {"period": 5, "out": "@x"}),
                "/b": ("sma", {"period": 9, "out": "@x"}), "/m": ("merge", {}),
                "/c": ("above", {"a": "@x", "threshold": 0}), "/e": ("entry", {})},
               [("/t", "/a", "in0"), ("/t", "/b", "in0"), ("/a", "/m", "in0"),
                ("/b", "/m", "in1"), ("/m", "/c", "in0"), ("/c", "/e", "in0")])
    assert "attr_clash" in _codes(g)


def test_merge_has_no_default_read():
    """A merge writes nothing, so a read from it must name the attribute."""
    g = _graph({"/t": ("ticker", {}), "/m": ("merge", {}),
                "/c": ("above", {"threshold": 0}), "/e": ("entry", {})},
               [("/t", "/m", "in0"), ("/m", "/c", "in0"), ("/c", "/e", "in0")])
    assert "missing_input" in _codes(g)

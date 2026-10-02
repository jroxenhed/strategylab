"""F435 Wave 2 review fixes KC-2, KC-4, KC-6 (kernel correctness).

KC-3 lives in test_stream_semantics.py, KC-5 in test_nodes_time.py.
"""
from __future__ import annotations

import pkgutil

import numpy as np
import pandas as pd
import pytest

import nodebuilder.trading as trading
from indicators import OHLCVSeries, compute_instance
from nodebuilder.compile import compile as nb_compile, compile_with_diagnostics
from nodebuilder.evaluator import cook_signals
from nodebuilder.kernel import registry
from nodebuilder.models import Graph
from nodebuilder.nodes import node_types

N = 300


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    rng = np.random.default_rng(5)
    t = np.arange(N)
    close = 100 + 8 * np.sin(t / 20) + np.cumsum(rng.normal(0, 0.5, N))
    idx = pd.date_range("2023-01-02", periods=N, freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": close, "High": close + 0.5, "Low": close - 0.5,
                         "Close": close, "Volume": np.full(N, 1e6)}, index=idx)


def _graph(nodes: dict, wires: list, bypass: tuple = ()) -> Graph:
    return Graph.model_validate({
        "_version": 3,
        "nodes": {nid: {"id": nid, "type": t, "params": p, "bypass": nid in bypass}
                  for nid, (t, p) in nodes.items()},
        "wires": [{"id": f"w{i}", "from": a, "to": b, "to_port": port}
                  for i, (a, b, port) in enumerate(wires)],
    })


def _sma(df, period):
    o = OHLCVSeries(close=df["Close"], high=df["High"], low=df["Low"], volume=df["Volume"])
    return compute_instance("ma", {"period": period, "type": "sma"}, o)["ma"].to_numpy()


# ---------------------------------------------------------------------------
# KC-2: bypassing a node that shadows an upstream name
# ---------------------------------------------------------------------------

def _shadow_graph(bypass: tuple = (), threshold_params: dict | None = None) -> Graph:
    """/a SMA10 and /b SMA50 both write @m; /c reads by default from /b."""
    return _graph(
        {"/t": ("ticker", {}),
         "/a": ("sma", {"period": 10, "out": "@m"}),
         "/b": ("sma", {"period": 50, "source": "@close", "out": "@m"}),
         "/c": ("above", {"threshold": 100, "out": "@up", **(threshold_params or {})}),
         "/e": ("entry", {})},
        [("/t", "/a", "in0"), ("/a", "/b", "in0"), ("/b", "/c", "in0"), ("/c", "/e", "in0")],
        bypass)


def _codes(g: Graph) -> list[tuple[str, str | None]]:
    _p, diags = compile_with_diagnostics(g)
    return [(d.code, d.node_id) for d in diags]


def test_active_shadowing_node_reads_its_own_column(df):
    entry, _ = cook_signals(nb_compile(_shadow_graph()), df)
    sma50 = _sma(df, 50)
    want = np.nan_to_num(sma50, nan=-np.inf) > 100
    want[0] = False
    np.testing.assert_array_equal(entry, want)
    assert ("attr_shadowed", "/b") in _codes(_shadow_graph())


def test_bypassed_shadowing_node_turns_its_default_reader_off():
    """Before the fix the default read of @m fell through to /a's SMA10,
    silently.  A bypassed node's default readers turn off instead."""
    g = _shadow_graph(bypass=("/b",))
    _program, diags = compile_with_diagnostics(g)
    codes = {d.code for d in diags}
    # /c is off, so Entry gets no signal: the graph refuses with the
    # bypassed-input error instead of cooking SMA10 > 100.
    assert "missing_input" in codes, diags
    e = next(d for d in diags if d.code == "missing_input")
    assert e.node_id == "/e"


def test_bypassed_shadowing_node_warns_about_explicit_readers(df):
    """An explicit read of @m now gets /a's column (the stream carries it):
    allowed, but the bypassed node says so (it warned while active too)."""
    g = _shadow_graph(bypass=("/b",), threshold_params={"a": "@m"})
    entry, _ = cook_signals(nb_compile(g), df)
    sma10 = _sma(df, 10)
    want = np.nan_to_num(sma10, nan=-np.inf) > 100
    want[0] = False
    np.testing.assert_array_equal(entry, want)
    assert ("attr_shadowed", "/b") in _codes(g)


def test_bypass_without_shadowing_has_no_warning():
    g = _graph({"/t": ("ticker", {}), "/a": ("sma", {"period": 10}),
                "/c": ("above", {"threshold": 100}), "/e": ("entry", {})},
               [("/t", "/a", "in0"), ("/a", "/c", "in0"), ("/c", "/e", "in0")],
               bypass=("/a",))
    assert not any(code == "attr_shadowed" for code, _n in _codes(g))


# ---------------------------------------------------------------------------
# KC-4: ATR is a rolling mean, not a recursive smoother
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("node_type", ["atr", "atr_pct"])
def test_atr_lookback_is_period_plus_one(node_type, df):
    g = _graph({"/t": ("ticker", {}), "/i": (node_type, {"period": 14}),
                "/c": ("above", {"threshold": 0}), "/e": ("entry", {})},
               [("/t", "/i", "in0"), ("/i", "/c", "in0"), ("/c", "/e", "in0")])
    program = nb_compile(g)
    assert program.required_lookback_bars == 15
    # And 15 bars really give the full-history value on the last bar.
    full, _ = cook_signals(program, df)
    tail, _ = cook_signals(program, df.iloc[-16:])
    assert full[-1] == tail[-1]


# ---------------------------------------------------------------------------
# KC-6: registration (catalog) order is explicit
# ---------------------------------------------------------------------------

def test_every_node_module_is_placed_in_module_order():
    present = sorted(m.name for m in pkgutil.iter_modules(trading.__path__)
                     if m.name.startswith("nodes_"))
    assert sorted(trading.MODULE_ORDER) == present
    assert trading.NODE_MODULES == list(trading.MODULE_ORDER)


def test_registration_follows_module_order():
    """Types register grouped by module, in MODULE_ORDER, whatever helper
    imports the modules make of each other."""
    first_seen: list[str] = []
    for t in registry.all_types():
        mod = (t.module or "").rsplit(".", 1)[-1]
        if mod.startswith("nodes_") and mod not in first_seen:
            first_seen.append(mod)
    assert first_seen == [m for m in trading.MODULE_ORDER if m in first_seen]
    # Grouped: once a later module has registered, no earlier one does again.
    rank = {m: i for i, m in enumerate(trading.MODULE_ORDER)}
    ranks = [rank[(t.module or "").rsplit(".", 1)[-1]] for t in registry.all_types()
             if (t.module or "").rsplit(".", 1)[-1] in rank]
    assert ranks == sorted(ranks)
    assert node_types()  # the catalog still builds

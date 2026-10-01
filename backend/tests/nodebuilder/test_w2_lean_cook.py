"""The lean cook frees nothing a terminal still needs (F435 W2, KC-1).

cook() drops a column right after its last reader.  A kept node (Entry,
Exit) reads its column "forever"; a later step that reads the same column
must not shorten that.  Before the fix the later reader overwrote the kept
read, the column was dropped, and signal_columns() raised KeyError on the
backtest path and on every bot tick (cook_graph_bar).

Each shape is drawn twice, with node ids that make cook_order visit the
other sink first, because whether the bug bit depended on that order.

The property test cooks every graph of the test corpora twice, once lean
(only the terminals kept) and once keeping everything, and asserts the
signals are identical.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bot_runner import cook_graph_bar
from nodebuilder.compile import compile as nb_compile
from nodebuilder.evaluator import cook_program, cook_signals, signal_columns
from nodebuilder.models import Graph, GraphValidationError
from tests.nodebuilder.test_handbuilt_graphs import _guard, _rsi
from tests.nodebuilder.test_rule_coverage import _DF

_HERE = Path(os.path.dirname(__file__))
N = 300


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    t = np.arange(N)
    close = 100 + 8 * np.sin(t / 12) + np.cumsum(rng.normal(0, 0.7, N))
    high = close + np.abs(rng.normal(0, 0.5, N))
    low = close - np.abs(rng.normal(0, 0.5, N))
    idx = pd.date_range("2023-01-02", periods=N, freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": rng.integers(1e5, 1e6, N)}, index=idx)


def _graph(nodes: dict, wires: list) -> Graph:
    return Graph.model_validate({
        "_version": 2,
        "nodes": {nid: {"id": nid, "type": t, "params": p} for nid, (t, p) in nodes.items()},
        "wires": [{"id": f"w{i}", "from": a, "to": b, "to_port": port}
                  for i, (a, b, port) in enumerate(wires)],
    })


def _ids(order: str) -> dict:
    """Node ids; "e_first" sorts Entry before Exit, "x_first" the reverse."""
    if order == "e_first":
        return {"e": "/a_entry", "x": "/z_exit", "n": "/m_not", "and": "/m_and", "or": "/m_or"}
    return {"e": "/z_entry", "x": "/a_exit", "n": "/b_not", "and": "/b_and", "or": "/b_or"}


def _shape(shape: str, order: str) -> tuple[Graph, callable]:
    """(graph, expected(df) -> (entry, exit)) for the four KC-1 shapes."""
    i = _ids(order)
    base = {"/t": ("ticker", {}), "/rsi": ("rsi", {"period": 14, "type": "wilder"}),
            "/lo": ("below", {"a": "@rsi", "threshold": 40, "out": "@lo"})}
    w0 = [("/t", "/rsi", "in0"), ("/rsi", "/lo", "in0")]
    if shape == "a":  # cmp -> Entry, cmp -> NOT -> Exit
        g = _graph({**base, i["n"]: ("not", {"a": "@lo", "out": "@nlo"}),
                    i["e"]: ("entry", {"signal": "@lo"}), i["x"]: ("exit", {"signal": "@nlo"})},
                   w0 + [("/lo", i["e"], "in0"), ("/lo", i["n"], "in0"), (i["n"], i["x"], "in0")])

        def want(d):
            lo = _guard(_rsi(d) < 40)
            return lo, _guard(~lo)
    elif shape == "b":  # cmp -> NOT -> Entry, cmp -> Exit
        g = _graph({**base, i["n"]: ("not", {"a": "@lo", "out": "@nlo"}),
                    i["e"]: ("entry", {"signal": "@nlo"}), i["x"]: ("exit", {"signal": "@lo"})},
                   w0 + [("/lo", i["n"], "in0"), (i["n"], i["e"], "in0"), ("/lo", i["x"], "in0")])

        def want(d):
            lo = _guard(_rsi(d) < 40)
            return _guard(~lo), lo
    elif shape == "c":  # cmp -> Entry, AND(cmp, other) -> Exit
        g = _graph({**base, "/hi": ("above", {"a": "@rsi", "threshold": 30, "out": "@hi"}),
                    i["and"]: ("and", {"terms": ["@lo", "@hi"], "out": "@both"}),
                    i["e"]: ("entry", {"signal": "@lo"}), i["x"]: ("exit", {"signal": "@both"})},
                   w0 + [("/rsi", "/hi", "in0"), ("/lo", i["e"], "in0"),
                         ("/lo", i["and"], "in0"), ("/hi", i["and"], "in1"), (i["and"], i["x"], "in0")])

        def want(d):
            r = _rsi(d)
            lo, hi = _guard(r < 40), _guard(r > 30)
            return lo, lo & hi
    else:  # "d": OR(lo, hi) -> Entry, hi -> Exit
        g = _graph({**base, "/hi": ("above", {"a": "@rsi", "threshold": 60, "out": "@hi"}),
                    i["or"]: ("or", {"terms": ["@lo", "@hi"], "out": "@any"}),
                    i["e"]: ("entry", {"signal": "@any"}), i["x"]: ("exit", {"signal": "@hi"})},
                   w0 + [("/rsi", "/hi", "in0"), ("/lo", i["or"], "in0"), ("/hi", i["or"], "in1"),
                         (i["or"], i["e"], "in0"), ("/hi", i["x"], "in0")])

        def want(d):
            r = _rsi(d)
            lo, hi = _guard(r < 40), _guard(r > 60)
            return lo | hi, hi
    return g, want


_CASES = [(s, o) for s in "abcd" for o in ("e_first", "x_first")]


@pytest.mark.parametrize("shape,order", _CASES)
def test_terminal_column_read_by_a_later_step_survives_the_backtest_cook(df, shape, order):
    g, want = _shape(shape, order)
    prog = nb_compile(g)
    entry, exit_ = cook_signals(prog, df)   # lean: only the terminals kept
    w_entry, w_exit = want(df)
    np.testing.assert_array_equal(entry, w_entry)
    np.testing.assert_array_equal(exit_, w_exit)
    assert entry.any() and exit_.any()


@pytest.mark.parametrize("shape,order", _CASES)
def test_terminal_column_read_by_a_later_step_survives_the_bot_tick(df, shape, order):
    g, want = _shape(shape, order)
    _attrs, sigs = cook_graph_bar(nb_compile(g), df, None)
    w_entry, w_exit = want(df)
    assert sigs == {"entry": bool(w_entry[-1]), "exit": bool(w_exit[-1])}
    # Every bar of the live window, as a bot would see it bar by bar.
    for end in range(N - 40, N):
        _a, s = cook_graph_bar(nb_compile(g), df.iloc[: end + 1], None)
        we, wx = want(df.iloc[: end + 1])
        assert s == {"entry": bool(we[-1]), "exit": bool(wx[-1])}


# ---------------------------------------------------------------------------
# Property: lean cook == cook that frees nothing, on every corpus graph
# ---------------------------------------------------------------------------


def _corpus_graphs() -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    corpus = json.loads((_HERE / "fixtures" / "v2_autorender_corpus.json").read_text())["cases"]
    out += [(f"v2:{c['id']}", c["graph"]) for c in corpus]
    out.append(("bench_graph_30", json.loads((_HERE / "fixtures" / "bench_graph_30.json").read_text())))
    golden = json.loads((_HERE / "fixtures" / "w1_goldens.json").read_text())["cases"]
    out += [(f"w1:{c['id']}", c["graph"]) for c in golden if c["source"] == "inline"]
    for s, o in _CASES:
        g, _ = _shape(s, o)
        out.append((f"kc1:{s}:{o}", g.model_dump(by_alias=True)))
    return out


_CORPUS = _corpus_graphs()


@pytest.mark.parametrize("case_id,data", _CORPUS, ids=[c[0] for c in _CORPUS])
def test_lean_cook_equals_keep_everything(case_id, data):
    try:
        prog = nb_compile(Graph.model_validate(copy.deepcopy(data)))
    except GraphValidationError:
        pytest.skip("graph is refused (the refusal is tested elsewhere)")
    lean = signal_columns(prog, cook_program(prog, _DF))
    full = signal_columns(prog, cook_program(prog, _DF, keep_all=True))
    np.testing.assert_array_equal(lean[0], full[0])
    np.testing.assert_array_equal(lean[1], full[1])

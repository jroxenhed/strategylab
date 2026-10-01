"""Hand-built graphs on the column engine (F435 W2, plan D4/D5, critic 26).

Each graph is drawn the way the editor stores it from W2 on: operands named
by params (``a``, ``b``, ``terms``, ``source``), writes named by ``out``
params.  Every expected signal is computed straight from the indicator
functions with numpy, so a test fails if the graph engine reads the wrong
column, swaps operands or loses a bar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from indicators import OHLCVSeries, compute_instance
from nodebuilder.compile import check_graph, compile as nb_compile, compile_with_diagnostics
from nodebuilder.diagnostics import validate_graph_full
from nodebuilder.evaluator import cook_program, cook_signals
from nodebuilder.models import Graph

N = 400


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    rng = np.random.default_rng(11)
    t = np.arange(N)
    close = 100 + 6 * np.sin(t / 15) + np.cumsum(rng.normal(0, 0.6, N))
    high = close + np.abs(rng.normal(0, 0.5, N))
    low = close - np.abs(rng.normal(0, 0.5, N))
    idx = pd.date_range("2023-01-02", periods=N, freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": rng.integers(1e5, 1e6, N)}, index=idx)


def _graph(nodes: dict, wires: list, bypass: tuple = ()) -> Graph:
    """nodes: {id: (type, params)}; wires: [(from, to, port)]."""
    return Graph.model_validate({
        "_version": 2,
        "nodes": {nid: {"id": nid, "type": t, "params": p, "bypass": nid in bypass}
                  for nid, (t, p) in nodes.items()},
        "wires": [{"id": f"w{i}", "from": a, "to": b, "to_port": port}
                  for i, (a, b, port) in enumerate(wires)],
    })


def _series(df, family, args, close=None):
    c = pd.Series(df["Close"].to_numpy() if close is None else close, index=df.index)
    o = OHLCVSeries(close=c, high=df["High"], low=df["Low"], volume=df["Volume"])
    return compute_instance(family, args, o)


def _prev(x):
    out = np.empty(len(x))
    out[0] = np.nan
    out[1:] = x[:-1]
    return out


def _guard(x):
    x = np.array(x, dtype=bool)
    x[0] = False
    return x


def _rsi(df, period=14):
    return _series(df, "rsi", {"period": period, "type": "wilder"})["rsi"].to_numpy()


_TICKER = {"/t": ("ticker", {})}


# ---------------------------------------------------------------------------
# The plan's list
# ---------------------------------------------------------------------------

def test_or_of_two_comparisons(df):
    g = _graph({**_TICKER,
                "/rsi": ("rsi", {"period": 14, "type": "wilder"}),
                "/lo": ("below", {"a": "@rsi", "threshold": 35, "out": "@lo"}),
                "/hi": ("above", {"a": "@rsi", "threshold": 65, "out": "@hi"}),
                "/or": ("or", {"terms": ["@lo", "@hi"]}),
                "/e": ("entry", {"signal": "@or"})},
               [("/t", "/rsi", "in0"), ("/rsi", "/lo", "in0"), ("/rsi", "/hi", "in0"),
                ("/lo", "/or", "in0"), ("/hi", "/or", "in1"), ("/or", "/e", "in0")])
    entry, _ = cook_signals(nb_compile(g), df)
    rsi = _rsi(df)
    np.testing.assert_array_equal(entry, _guard((rsi < 35) | (rsi > 65)))
    assert entry.any()


def test_not_of_a_comparison(df):
    g = _graph({**_TICKER,
                "/rsi": ("rsi", {"period": 14, "type": "wilder"}),
                "/lo": ("below", {"threshold": 50}),
                "/not": ("not", {}),
                "/e": ("entry", {})},
               [("/t", "/rsi", "in0"), ("/rsi", "/lo", "in0"), ("/lo", "/not", "in0"),
                ("/not", "/e", "in0")])
    entry, _ = cook_signals(nb_compile(g), df)
    np.testing.assert_array_equal(entry, _guard(~(_rsi(df) < 50)))


def test_nested_logic(df):
    """(RSI < 40 AND close above SMA20) OR NOT (RSI < 70)."""
    g = _graph({**_TICKER,
                "/rsi": ("rsi", {"period": 14, "type": "wilder"}),
                "/sma": ("sma", {"period": 20}),
                "/c1": ("below", {"a": "@rsi", "threshold": 40, "out": "@c1"}),
                "/c2": ("above", {"a": "@close", "b": "@sma", "out": "@c2"}),
                "/c3": ("below", {"a": "@rsi", "threshold": 70, "out": "@c3"}),
                "/and": ("and", {"terms": ["@c1", "@c2"]}),
                "/not": ("not", {"signal": "@c3"}),
                "/or": ("or", {"terms": ["@and", "@not"]}),
                "/e": ("entry", {"signal": "@or"})},
               [("/t", "/rsi", "in0"), ("/t", "/sma", "in0"),
                ("/rsi", "/c1", "in0"), ("/sma", "/c2", "in0"), ("/rsi", "/c3", "in0"),
                ("/c1", "/and", "in0"), ("/c2", "/and", "in1"), ("/c3", "/not", "in0"),
                ("/and", "/or", "in0"), ("/not", "/or", "in1"), ("/or", "/e", "in0")])
    entry, _ = cook_signals(nb_compile(g), df)
    rsi, close = _rsi(df), df["Close"].to_numpy()
    sma = _series(df, "ma", {"period": 20, "type": "sma"})["ma"].to_numpy()
    expected = (_guard(_guard(rsi < 40) & _guard(close > sma)) | _guard(~_guard(rsi < 70)))
    np.testing.assert_array_equal(entry, _guard(expected))
    assert entry.any()


def test_fan_out_from_one_indicator(df):
    """One RSI feeds the Entry and the Exit comparisons; it is computed once."""
    g = _graph({**_TICKER,
                "/rsi": ("rsi", {"period": 14, "type": "wilder"}),
                "/buy": ("crosses_above", {"threshold": 30}),
                "/sell": ("crosses_below", {"threshold": 70}),
                "/e": ("entry", {}), "/x": ("exit", {})},
               [("/t", "/rsi", "in0"), ("/rsi", "/buy", "in0"), ("/rsi", "/sell", "in0"),
                ("/buy", "/e", "in0"), ("/sell", "/x", "in0")])
    prog = nb_compile(g)
    entry, exit_ = cook_signals(prog, df)
    rsi = _rsi(df)
    np.testing.assert_array_equal(entry, _guard((_prev(rsi) < 30) & (rsi >= 30)))
    np.testing.assert_array_equal(exit_, _guard((_prev(rsi) > 70) & (rsi <= 70)))
    assert [s.node_id for s in prog.steps if s.type == "rsi"] == ["/rsi"]


def test_bypass_is_pass_through(df):
    """A bypassed EMA passes the Ticker's stream on: an RSI below it that
    reads @close explicitly runs on the close, as if the EMA were not there.
    An RSI that reads the EMA's own write (its default) is off."""
    nodes = {**_TICKER,
             "/ema": ("ema", {"period": 10}),
             "/rsi": ("rsi", {"period": 14, "type": "wilder", "source": "@close"}),
             "/lo": ("below", {"threshold": 45}),
             "/e": ("entry", {})}
    wires = [("/t", "/ema", "in0"), ("/ema", "/rsi", "in0"), ("/rsi", "/lo", "in0"),
             ("/lo", "/e", "in0")]
    prog = nb_compile(_graph(nodes, wires, bypass=("/ema",)))
    assert prog.step("/ema").mode == "pass"
    entry, _ = cook_signals(prog, df)
    np.testing.assert_array_equal(entry, _guard(_rsi(df) < 45))
    # The output stream of the bypassed node is its input stream.
    schemas = check_graph(_graph(nodes, wires, bypass=("/ema",))).streams_json()
    assert schemas["/ema"] == schemas["/t"]

    # Default source = the EMA's @ema, which a bypassed node does not write.
    nodes["/rsi"] = ("rsi", {"period": 14, "type": "wilder"})
    _prog, diags = compile_with_diagnostics(_graph(nodes, wires, bypass=("/ema",)))
    [d] = [d for d in diags if d.severity == "error"]
    assert (d.code, d.node_id) == ("missing_input", "/e") and "bypassed" in d.message


def test_crossover_of_a_derived_bool(df):
    """Entry on the bar the 'RSI below 40' signal switches on."""
    g = _graph({**_TICKER,
                "/rsi": ("rsi", {"period": 14, "type": "wilder"}),
                "/lo": ("below", {"threshold": 40}),
                "/x": ("crosses_above", {"a": "@below", "threshold": 0.5}),
                "/e": ("entry", {})},
               [("/t", "/rsi", "in0"), ("/rsi", "/lo", "in0"), ("/lo", "/x", "in0"),
                ("/x", "/e", "in0")])
    entry, _ = cook_signals(nb_compile(g), df)
    sig = _guard(_rsi(df) < 40).astype(float)
    np.testing.assert_array_equal(entry, _guard((_prev(sig) < 0.5) & (sig >= 0.5)))
    assert 0 < entry.sum() < sig.sum()


def test_ema_of_rsi(df):
    g = _graph({**_TICKER,
                "/rsi": ("rsi", {"period": 14, "type": "wilder"}),
                "/ema": ("ema", {"period": 9}),
                "/x": ("crosses_above", {"a": "@rsi", "b": "@ema"}),
                "/e": ("entry", {})},
               [("/t", "/rsi", "in0"), ("/rsi", "/ema", "in0"), ("/ema", "/x", "in0"),
                ("/x", "/e", "in0")])
    prog = nb_compile(g)
    assert prog.step("/ema").reads == ("@rsi",)
    entry, _ = cook_signals(prog, df)
    rsi = _rsi(df)
    ema = _series(df, "ma", {"period": 9, "type": "ema"}, close=rsi)["ma"].to_numpy()
    np.testing.assert_array_equal(entry, _guard((_prev(rsi) < _prev(ema)) & (rsi >= ema)))
    assert entry.any()


def test_macd_crosses_its_signal(df):
    g = _graph({**_TICKER,
                "/m": ("macd", {}),
                "/x": ("crosses_above", {"a": "@macd_line", "b": "@macd_signal"}),
                "/y": ("crosses_below", {"a": "@macd_line", "b": "@macd_signal"}),
                "/e": ("entry", {}), "/q": ("exit", {})},
               [("/t", "/m", "in0"), ("/m", "/x", "in0"), ("/m", "/y", "in0"),
                ("/x", "/e", "in0"), ("/y", "/q", "in0")])
    entry, exit_ = cook_signals(nb_compile(g), df)
    m = _series(df, "macd", {"fast": 12, "slow": 26, "signal": 9})
    line, sig = m["macd"].to_numpy(), m["signal"].to_numpy()
    np.testing.assert_array_equal(entry, _guard((_prev(line) < _prev(sig)) & (line >= sig)))
    np.testing.assert_array_equal(exit_, _guard((_prev(line) > _prev(sig)) & (line <= sig)))
    assert entry.any() and exit_.any()


# ---------------------------------------------------------------------------
# Attribute semantics
# ---------------------------------------------------------------------------

def _two_rsis(read: str) -> Graph:
    """Two RSIs that both write @r, merged into one comparison of *read*
    against @close (b is named, so the wire on in1 decides nothing)."""
    params = {"a": read, "b": "@close", "out": "@hit"}
    return _graph({**_TICKER,
                   "/r1": ("rsi", {"period": 14, "type": "wilder", "out": "@r"}),
                   "/r2": ("rsi", {"period": 7, "type": "wilder", "out": "@r"}),
                   "/s": ("sma", {"period": 5, "source": "@close"}),
                   "/c": ("above", params),
                   "/e": ("entry", {"signal": "@hit"})},
                  [("/t", "/r1", "in0"), ("/t", "/r2", "in0"), ("/r1", "/s", "in0"),
                   ("/s", "/c", "in0"), ("/r2", "/c", "in1"), ("/c", "/e", "in0")])


def test_name_clash_is_an_error_when_read():
    _prog, diags = compile_with_diagnostics(_two_rsis("@r"))
    [d] = [d for d in diags if d.severity == "error"]
    assert (d.code, d.node_id, d.param) == ("attr_clash", "/c", "a")
    assert "'/r1'" in d.message and "'/r2'" in d.message


def test_name_clash_is_a_warning_when_not_read(df):
    prog, diags = compile_with_diagnostics(_two_rsis("@sma"))
    assert prog is not None
    [d] = [d for d in diags if d.code == "attr_shadowed"]
    assert d.severity == "warning" and d.node_id == "/c"
    entry, _ = cook_signals(prog, df)
    sma = _series(df, "ma", {"period": 5, "type": "sma"})["ma"].to_numpy()
    np.testing.assert_array_equal(entry, _guard(sma > df["Close"].to_numpy()))
    assert entry.any()


def test_a_clash_read_further_down_names_where_it_formed():
    g = _two_rsis("@sma")
    data = g.model_dump(by_alias=True)
    data["nodes"]["/n"] = {"id": "/n", "type": "below", "params": {"a": "@r", "threshold": 1}}
    data["wires"].append({"id": "wn", "from": "/c", "to": "/n", "to_port": "in0"})
    _prog, diags = compile_with_diagnostics(Graph.model_validate(data))
    [d] = [d for d in diags if d.code == "attr_clash"]
    assert d.node_id == "/n" and "'/c'" in d.message
    assert not [d for d in diags if d.code == "attr_shadowed"]


def _rsi_vs_sma(a=None, b=None, swap_ports=False, wire_order=("left", "right")) -> Graph:
    params = {}
    if a:
        params["a"] = a
    if b:
        params["b"] = b
    ports = {"left": "in1" if swap_ports else "in0", "right": "in0" if swap_ports else "in1"}
    wires = {"left": ("/rsi", "/c", ports["left"]), "right": ("/sma", "/c", ports["right"])}
    return _graph({**_TICKER,
                   "/rsi": ("rsi", {"period": 14, "type": "wilder"}),
                   "/sma": ("sma", {"period": 20, "source": "@close"}),
                   "/c": ("above", params),
                   "/e": ("entry", {})},
                  [("/t", "/rsi", "in0"), ("/rsi", "/sma", "in0")]
                  + [wires[w] for w in wire_order] + [("/c", "/e", "in0")])


def test_operand_order_comes_from_the_params():
    """With a and b named, redrawing the wires (new list order, ports
    swapped) never swaps the operands."""
    base = nb_compile(_rsi_vs_sma("@rsi", "@sma")).step("/c").reads
    assert base == ("@rsi", "@sma")
    assert nb_compile(_rsi_vs_sma("@rsi", "@sma", wire_order=("right", "left"))).step("/c").reads == base
    assert nb_compile(_rsi_vs_sma("@rsi", "@sma", swap_ports=True)).step("/c").reads == base
    # Without names, the defaults follow the ports (in0 is a).
    assert nb_compile(_rsi_vs_sma()).step("/c").reads == ("@rsi", "@sma")
    assert nb_compile(_rsi_vs_sma(swap_ports=True)).step("/c").reads == ("@sma", "@rsi")


def test_missing_and_mistyped_reads():
    def diag(params):
        g = _graph({**_TICKER, "/c": ("above", params), "/e": ("entry", {})},
                   [("/t", "/c", "in0"), ("/c", "/e", "in0")])
        _p, diags = compile_with_diagnostics(g)
        return [(d.code, d.node_id, d.param) for d in diags if d.severity == "error"]

    assert diag({"a": "@nope", "threshold": 1}) == [("attr_missing", "/c", "a")]
    assert diag({"a": "@trade.pnl", "threshold": 1}) == [("prims_no_producer", "/c", "a")]
    assert diag({"a": "Close", "threshold": 1}) == [("param_invalid", "/c", "a")]
    # A bool param fed a number: the explicit read is blamed on the reader.
    g = _graph({**_TICKER, "/e": ("entry", {"signal": "@close"})}, [("/t", "/e", "in0")])
    _p, diags = compile_with_diagnostics(g)
    assert [(d.code, d.node_id, d.param) for d in diags if d.severity == "error"] == [
        ("attr_type", "/e", "signal")]


def test_unknown_port_is_refused():
    g = _graph({**_TICKER, "/rsi": ("rsi", {}), "/sma": ("sma", {}), "/e": ("entry", {})},
               [("/t", "/rsi", "in0"), ("/t", "/sma", "in0"), ("/rsi", "/sma", "in1")])
    _p, diags = compile_with_diagnostics(g)
    assert ("port_unknown", "/sma", "in1") in {(d.code, d.node_id, d.port) for d in diags}


def test_settings_write_detail_and_validate_lists_streams():
    g = _graph({**_TICKER,
                "/rsi": ("rsi", {}),
                "/c": ("below", {"threshold": 30}),
                "/sl": ("stop_loss", {"pct": 2.5}),
                "/e": ("entry", {})},
               [("/t", "/rsi", "in0"), ("/rsi", "/c", "in0"), ("/c", "/e", "in0")])
    result = validate_graph_full(g.model_dump(by_alias=True))
    assert [d.code for d in result.diagnostics] == ["exit_unconnected"]
    assert result.streams["/sl"] == {
        "stream_schema": 1, "points": [],
        "detail": [{"name": "@stop_pct", "dtype": "float", "written_by": "/sl"}], "prims": [],
    }
    c = result.streams["/c"]
    assert [p["name"] for p in c["points"]][-2:] == ["@rsi", "@below"]
    assert c["points"][-1] == {"name": "@below", "dtype": "bool", "written_by": "/c"}
    assert set(result.streams) == {"/t", "/rsi", "/c", "/sl", "/e"}


def test_unnamed_writes_get_unique_names():
    g = _graph({**_TICKER, "/a": ("rsi", {}), "/b": ("rsi", {"period": 7}),
                "/c": ("rsi", {"period": 9, "out": "@rsi_2"}), "/e": ("entry", {})},
               [("/t", "/a", "in0"), ("/t", "/b", "in0"), ("/t", "/c", "in0")])
    from nodebuilder.compile import assign_write_names

    names = assign_write_names(g)
    assert (names["/a"]["out"], names["/b"]["out"], names["/c"]["out"]) == ("@rsi", "@rsi_3", "@rsi_2")


def test_required_lookback_follows_the_longest_chain():
    g = _graph({**_TICKER,
                "/rsi": ("rsi", {"period": 14, "type": "wilder"}),   # F*14 + 1
                "/ema": ("ema", {"period": 10}),                      # + F*10
                "/sma": ("sma", {"period": 50, "source": "@close"}),  # 50 on its own
                "/x": ("crosses_above", {"a": "@ema", "threshold": 50}),  # + 1
                "/e": ("entry", {})},
               [("/t", "/rsi", "in0"), ("/rsi", "/ema", "in0"), ("/t", "/sma", "in0"),
                ("/ema", "/x", "in0"), ("/x", "/e", "in0")])
    from nodebuilder.trading.nodes_indicators import RECURSIVE_FACTOR as F

    assert nb_compile(g).required_lookback_bars == (F * 14 + 1) + F * 10 + 1


def test_lean_cook_frees_intermediate_columns(df):
    g = _graph({**_TICKER,
                "/rsi": ("rsi", {}), "/ema": ("ema", {"period": 9}),
                "/x": ("crosses_above", {"a": "@rsi", "b": "@ema"}), "/e": ("entry", {})},
               [("/t", "/rsi", "in0"), ("/rsi", "/ema", "in0"), ("/ema", "/x", "in0"),
                ("/x", "/e", "in0")])
    prog = nb_compile(g)
    lean = cook_program(prog, df)
    full = cook_program(prog, df, keep_all=True)
    np.testing.assert_array_equal(lean.column("/e", "@xa"), full.column("/e", "@xa"))
    assert len(lean.store) == 1 and len(full.store) > 5

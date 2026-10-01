"""Regenerate w1_goldens.json from the frozen Wave 1 engine (F435 W2, MD-05/06).

NOT a test, and not run by the suite.  It needs the committed Wave 1 backend,
exported outside the repo (never check it out in the working tree):

    mkdir -p $SCRATCH/w1 && git archive 83d5ba1 backend | tar -x -C $SCRATCH/w1
    backend/venv/bin/python backend/tests/nodebuilder/fixtures/make_w1_goldens.py \\
        $SCRATCH/w1/backend backend/tests/nodebuilder/fixtures/w1_goldens.json

It runs the Wave 1 graph engine (compute_indicators_from_specs plus the
per-bar evaluate_graph, as Wave 1's run.py did) on
tests.nodebuilder.test_rule_coverage._DF (400 bars, seed 7) for:

- every case of v2_autorender_corpus.json, the v1 auto_render vectors and
  bench_graph_30.json (referenced by source and key, not copied), and
- the hand-built Wave 1 graph shapes below, stored inline with the wire
  ``attr`` labels the Wave 1 editor wrote: unwired and orphan indicators,
  no Ticker, two Tickers, AND/OR over 16 inputs (hand-built and Wave 1
  auto_render of 20 rules), non-numbered port ids, fan-out, NOT, nested
  logic, bypass, settings nodes, Ticker field labels, and the KC-1 shapes.

Each case stores the bar numbers where Entry and Exit are True, or the
Wave 1 refusal code.  test_w1_goldens.py checks Wave 2 reproduces every
case Wave 1 computed.  Do not regenerate once committed: the point is that
the goldens come from code that no longer exists in the tree.
"""
from __future__ import annotations

import copy
import json
import os
import sys

# Everything below runs only as a script: python make_w1_goldens.py <w1_root> <out_json>.
# Importing this module (the backend smoke check does) must do nothing.
if __name__ == "__main__":
    W1_ROOT, OUT = sys.argv[1], sys.argv[2]
    HERE = os.path.dirname(os.path.abspath(__file__))
    TESTS = os.path.dirname(HERE)
    sys.path.insert(0, W1_ROOT)
    os.chdir(W1_ROOT)

    import numpy as np  # noqa: E402
    import pandas as pd  # noqa: E402


    def _synthetic_df(n: int = 400, seed: int = 7) -> pd.DataFrame:
        """Copy of tests.nodebuilder.test_rule_coverage._synthetic_df (Wave 2
        tree); the test asserts the two frames are equal."""
        rng = np.random.default_rng(seed)
        t = np.arange(n)
        drift = 8.0 * np.sin(t / 18.0) + 3.0 * np.sin(t / 5.0)
        close = 250.0 + drift + np.cumsum(rng.normal(0.0, 0.8, n))
        open_ = close + rng.normal(0.0, 0.4, n)
        high = np.maximum(open_, close) + np.abs(rng.normal(0.0, 0.6, n))
        low = np.minimum(open_, close) - np.abs(rng.normal(0.0, 0.6, n))
        volume = (1_000_000 + 400_000 * np.sin(t / 7.0) + rng.normal(0, 50_000, n)).astype("int64")
        idx = pd.date_range("2022-01-03", periods=n, freq="B", tz="America/New_York", name="Date")
        return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                             "Volume": volume, "Dividends": 0.0, "Stock Splits": 0.0}, index=idx)


    DF = _synthetic_df()

    # ---------------------------------------------------------------------------
    # Hand-built Wave 1 shapes (graph _version 2, wire attr labels)
    # ---------------------------------------------------------------------------


    def N(i, t, p=None, bypass=False):
        return {"id": i, "type": t, "params": p or {}, "bypass": bypass}


    def W(s, d, port, attr=None):
        w = {"id": f"{s}-{d}-{port}", "from": s, "to": d, "to_port": port}
        if attr is not None:
            w["attr"] = attr
        return w


    def G(nodes, wires):
        return {"_version": 2, "nodes": {n["id"]: n for n in nodes}, "wires": wires}


    T = N("t", "ticker", {"symbol": "SYN", "interval": "1d", "source": "yahoo"})
    E, X = N("entry", "entry"), N("exit", "exit")
    RSI = {"period": 14, "type": "wilder"}
    H: dict[str, dict] = {}

    # Unwired / orphan indicators (LT-2, MD-04)
    H["unwired_rsi"] = G([T, N("rsi", "rsi", RSI), N("c", "below", {"threshold": 40}),
                          N("c2", "above", {"threshold": 60}), E, X],
                         [W("rsi", "c", "in0", "@rsi"), W("c", "entry", "in0", "@bool"),
                          W("rsi", "c2", "in0", "@rsi"), W("c2", "exit", "in0", "@bool")])
    H["unwired_atr"] = G([T, N("a", "atr", {"period": 14}), N("c", "above", {"threshold": 1.0}), E],
                         [W("a", "c", "in0", "@atr"), W("c", "entry", "in0", "@bool")])
    H["unwired_macd"] = G([T, N("m", "macd", {"fast": 12, "slow": 26, "signal": 9}),
                           N("c", "crosses_above"), E],
                          [W("m", "c", "in0", "@macd_line"), W("m", "c", "in1", "@macd_signal"),
                           W("c", "entry", "in0", "@bool")])
    H["unwired_bollinger"] = G([T, N("b", "bollinger", {"period": 20, "stddev": 2.0}),
                                N("c", "below"), E],
                               [W("t", "c", "in0", "@close"), W("b", "c", "in1", "@bb_lower"),
                                W("c", "entry", "in0", "@bool")])
    H["unwired_sma_cross"] = G([T, N("s1", "sma", {"period": 10}), N("s2", "sma", {"period": 30}),
                                N("c", "crosses_above"), E],
                               [W("s1", "c", "in0", "@sma"), W("s2", "c", "in1", "@sma"),
                                W("c", "entry", "in0", "@bool")])
    H["unwired_ema_vs_close"] = G([T, N("e", "ema", {"period": 20}), N("c", "above"), E],
                                  [W("t", "c", "in0", "@close"), W("e", "c", "in1", "@ema"),
                                   W("c", "entry", "in0", "@bool")])
    H["no_ticker"] = G([N("r", "rsi", RSI), N("c", "below", {"threshold": 40}), E],
                       [W("r", "c", "in0", "@rsi"), W("c", "entry", "in0", "@bool")])
    H["two_tickers_unwired_rsi"] = G(
        [T, N("t2", "ticker", {"symbol": "SPY", "interval": "1d", "source": "yahoo"}),
         N("r", "rsi", RSI), N("c", "below", {"threshold": 40}), E],
        [W("r", "c", "in0", "@rsi"), W("c", "entry", "in0", "@bool")])
    H["two_tickers_wired"] = G(
        [T, N("t2", "ticker", {"symbol": "SPY", "interval": "1d", "source": "yahoo"}),
         N("r", "rsi", RSI), N("c", "below", {"threshold": 40}), E],
        [W("t2", "r", "in0", "@close"), W("r", "c", "in0", "@rsi"), W("c", "entry", "in0", "@bool")])
    H["orphan_sma"] = G([T, N("r", "rsi", RSI), N("c", "below", {"threshold": 40}), E, X,
                         N("orph", "sma", {"period": 50})],
                        [W("t", "r", "in0", "@close"), W("r", "c", "in0", "@rsi"),
                         W("c", "entry", "in0", "@bool")])
    H["orphan_wired_sma"] = G([T, N("r", "rsi", RSI), N("c", "below", {"threshold": 40}), E,
                               N("orph", "sma", {"period": 50})],
                              [W("t", "r", "in0", "@close"), W("t", "orph", "in0", "@close"),
                               W("r", "c", "in0", "@rsi"), W("c", "entry", "in0", "@bool")])
    H["unwired_bypassed_rsi"] = G([T, N("r", "rsi", RSI, bypass=True), N("c", "below", {"threshold": 40}),
                                   N("c3", "above", {"threshold": 250}), N("or", "or"), E],
                                  [W("r", "c", "in0", "@rsi"), W("t", "c3", "in0", "@close"),
                                   W("c", "or", "in0", "@bool"), W("c3", "or", "in1", "@bool"),
                                   W("or", "entry", "in0", "@bool")])

    # Logic over 16 inputs (MD-03)
    for op in ("and", "or"):
        nodes = [T, N("lg", op), E, N("r", "rsi", RSI)]
        wires = [W("t", "r", "in0", "@close"), W("lg", "entry", "in0", "@bool")]
        for k in range(20):
            thr = 10 + k if op == "and" else 70 + k
            nodes.append(N(f"c{k:02d}", "above", {"threshold": thr}))
            wires += [W("r", f"c{k:02d}", "in0", "@rsi"), W(f"c{k:02d}", "lg", f"in{k}", "@bool")]
        H[f"{op}_20"] = G(nodes, wires)
    nodes = [T, N("lg", "or"), E, N("r", "rsi", RSI)]
    wires = [W("t", "r", "in0", "@close"), W("lg", "entry", "in0", "@bool")]
    for k in range(40):
        nodes.append(N(f"c{k:02d}", "above", {"threshold": 50 + k}))
        wires += [W("r", f"c{k:02d}", "in0", "@rsi"), W(f"c{k:02d}", "lg", f"in{k}", "@bool")]
    H["or_40"] = G(nodes, wires)

    # Non-numbered port ids (MD-09)
    H["odd_port_not"] = G([T, N("r", "rsi", RSI), N("c0", "below", {"threshold": 45}), N("n", "not"), E],
                          [W("t", "r", "in0", "@close"), W("r", "c0", "in0", "@rsi"),
                           W("c0", "n", "a", "@bool"), W("n", "entry", "in0", "@bool")])
    H["odd_port_entry"] = G([T, N("r", "rsi", RSI), N("c0", "below", {"threshold": 45}), E],
                            [W("t", "r", "in0", "@close"), W("r", "c0", "in0", "@rsi"),
                             W("c0", "entry", "signal", "@bool")])
    H["odd_port_rsi"] = G([T, N("r", "rsi", RSI), N("c0", "below", {"threshold": 45}), E],
                          [W("t", "r", "source", "@close"), W("r", "c0", "in0", "@rsi"),
                           W("c0", "entry", "in0", "@bool")])
    H["odd_port_and"] = G([T, N("r", "rsi", RSI), N("c0", "below", {"threshold": 55}),
                           N("c1", "above", {"threshold": 30}), N("and", "and"), E],
                          [W("t", "r", "in0", "@close"), W("r", "c0", "in0", "@rsi"),
                           W("r", "c1", "in0", "@rsi"), W("c0", "and", "x", "@bool"),
                           W("c1", "and", "in0", "@bool"), W("and", "entry", "in0", "@bool")])

    # Fan-out, NOT, nested logic, KC-1 shapes
    H["fanout_rsi"] = G([T, N("r", "rsi", RSI), N("lo", "below", {"threshold": 35}),
                         N("hi", "above", {"threshold": 65}), N("mid", "above", {"threshold": 50}),
                         N("or", "or"), E, X],
                        [W("t", "r", "in0", "@close"), W("r", "lo", "in0", "@rsi"), W("r", "hi", "in0", "@rsi"),
                         W("r", "mid", "in0", "@rsi"), W("lo", "or", "in0", "@bool"), W("hi", "or", "in1", "@bool"),
                         W("or", "entry", "in0", "@bool"), W("mid", "exit", "in0", "@bool")])
    H["kc1_cmp_entry_not_exit"] = G([T, N("r", "rsi", RSI), N("lo", "below", {"threshold": 40}),
                                     N("n", "not"), E, X],
                                    [W("t", "r", "in0", "@close"), W("r", "lo", "in0", "@rsi"),
                                     W("lo", "entry", "in0", "@bool"), W("lo", "n", "in0", "@bool"),
                                     W("n", "exit", "in0", "@bool")])
    H["kc1_or_shared_exit"] = G([T, N("r", "rsi", RSI), N("lo", "below", {"threshold": 40}),
                                 N("hi", "above", {"threshold": 60}), N("aor", "or"), E, X],
                                [W("t", "r", "in0", "@close"), W("r", "lo", "in0", "@rsi"),
                                 W("r", "hi", "in0", "@rsi"), W("lo", "aor", "in0", "@bool"),
                                 W("hi", "aor", "in1", "@bool"), W("aor", "entry", "in0", "@bool"),
                                 W("hi", "exit", "in0", "@bool")])
    H["nested_logic"] = G([T, N("r", "rsi", RSI), N("s", "sma", {"period": 20}),
                           N("lo", "below", {"threshold": 45}), N("up", "above"),
                           N("hi", "above", {"threshold": 70}), N("n", "not"),
                           N("or", "or"), N("and", "and"), E, X],
                          [W("t", "r", "in0", "@close"), W("t", "s", "in0", "@close"),
                           W("r", "lo", "in0", "@rsi"), W("t", "up", "in0", "@close"), W("s", "up", "in1", "@sma"),
                           W("r", "hi", "in0", "@rsi"), W("hi", "n", "in0", "@bool"),
                           W("lo", "or", "in0", "@bool"), W("up", "or", "in1", "@bool"),
                           W("or", "and", "in0", "@bool"), W("n", "and", "in1", "@bool"),
                           W("and", "entry", "in0", "@bool"), W("hi", "exit", "in0", "@bool")])

    # Bypass
    H["bypass_rsi"] = G([T, N("rsi", "rsi", RSI, bypass=True), N("c", "below", {"threshold": 40}),
                         N("c3", "above", {"threshold": 250}), N("and", "and"), E],
                        [W("t", "rsi", "in0", "@close"), W("rsi", "c", "in0", "@rsi"), W("t", "c3", "in0", "@close"),
                         W("c", "and", "in0", "@bool"), W("c3", "and", "in1", "@bool"), W("and", "entry", "in0", "@bool")])
    H["bypass_cmp_or"] = G([T, N("r", "rsi", RSI), N("c0", "below", {"threshold": 40}),
                            N("c1", "above", {"threshold": 60}, bypass=True), N("or", "or"), E],
                           [W("t", "r", "in0", "@close"), W("r", "c0", "in0", "@rsi"), W("r", "c1", "in0", "@rsi"),
                            W("c0", "or", "in0", "@bool"), W("c1", "or", "in1", "@bool"), W("or", "entry", "in0", "@bool")])
    H["bypass_ticker"] = G([dict(T, bypass=True), N("r", "rsi", RSI), N("c", "below", {"threshold": 40}), E],
                           [W("t", "r", "in0", "@close"), W("r", "c", "in0", "@rsi"), W("c", "entry", "in0", "@bool")])
    H["bypass_not"] = G([T, N("r", "rsi", RSI), N("c", "below", {"threshold": 40}), N("n", "not", bypass=True),
                         N("c2", "above", {"threshold": 60}), N("or", "or"), E],
                        [W("t", "r", "in0", "@close"), W("r", "c", "in0", "@rsi"), W("c", "n", "in0", "@bool"),
                         W("r", "c2", "in0", "@rsi"), W("n", "or", "in0", "@bool"), W("c2", "or", "in1", "@bool"),
                         W("or", "entry", "in0", "@bool")])

    # Settings nodes
    H["settings_all"] = G([T, N("r", "rsi", RSI), N("c0", "below", {"threshold": 40}),
                           N("c1", "above", {"threshold": 60}),
                           N("sl", "stop_loss", {"pct": 3}), N("ps", "position_size", {"pct": 50}),
                           N("ts", "trailing_stop", {"type": "atr", "value": 2.0, "source": "close"}),
                           N("slip", "slippage", {"bps": 5}), E, X],
                          [W("t", "r", "in0", "@close"), W("r", "c0", "in0", "@rsi"),
                           W("r", "c1", "in0", "@rsi"),
                           W("c0", "entry", "in0", "@bool"), W("c1", "exit", "in0", "@bool")])

    # Labels
    H["ticker_fields"] = G([T, N("c", "above"), N("c2", "below"), E, X],
                           [W("t", "c", "in0", "@high"), W("t", "c", "in1", "@open"), W("c", "entry", "in0", "@bool"),
                            W("t", "c2", "in0", "@low"), W("t", "c2", "in1", "@open"), W("c2", "exit", "in0", "@bool")])
    H["macd_hist_bb_bands"] = G(
        [T, N("m", "macd", {"fast": 12, "slow": 26, "signal": 9}), N("c", "above", {"threshold": 0}),
         N("b1", "bollinger", {"period": 20, "stddev": 2}), N("b2", "bollinger", {"period": 10, "stddev": 1.5}),
         N("c2", "crosses_below"), E, X],
        [W("t", "m", "in0", "@close"), W("m", "c", "in0", "@macd_histogram"), W("c", "entry", "in0", "@bool"),
         W("t", "b1", "in0", "@close"), W("t", "b2", "in0", "@close"), W("b2", "c2", "in0", "@bb_middle"),
         W("b1", "c2", "in1", "@bb_lower"), W("c2", "exit", "in0", "@bool")])
    H["two_rsi_same_spec"] = G(
        [T, N("r1", "rsi", RSI), N("r2", "rsi", RSI), N("r3", "rsi", {"period": 7, "type": "wilder"}),
         N("c", "crosses_above", {"threshold": 50}), N("c2", "crosses_above"), E, X],
        [W("t", "r1", "in0", "@close"), W("t", "r2", "in0", "@close"), W("t", "r3", "in0", "@close"),
         W("r2", "c", "in0", "@rsi"), W("r1", "c", "in1", "@rsi"), W("r3", "c2", "in0", "@rsi"),
         W("r1", "c2", "in1", "@rsi"), W("c", "entry", "in0", "@bool"), W("c2", "exit", "in0", "@bool")])
    H["label_mismatch"] = G([T, N("e", "ema", {"period": 20}), N("c", "above"), E],
                            [W("t", "e", "in0", "@close"), W("t", "c", "in0", "@close"),
                             W("e", "c", "in1", "@rsi"), W("c", "entry", "in0", "@bool")])
    H["ind_label_high"] = G([T, N("s", "sma", {"period": 10}), N("c", "above"), E],
                            [W("t", "s", "in0", "@high"), W("t", "c", "in0", "@close"),
                             W("s", "c", "in1", "@sma"), W("c", "entry", "in0", "@bool")])
    H["atr_triple"] = G([T, N("a", "atr", {"period": 14}), N("c", "above", {"threshold": 1.0}), E],
                        [W("t", "a", "in0", "@high"), W("t", "a", "in1", "@low"), W("t", "a", "in2", "@close"),
                         W("a", "c", "in0", "@atr"), W("c", "entry", "in0", "@bool")])
    H["volume_threshold"] = G([T, N("c", "above", {"threshold": 1100000}), E],
                              [W("t", "c", "in0", "@volume"), W("c", "entry", "in0", "@bool")])
    H["two_macd"] = G([T, N("m1", "macd", {"fast": 12, "slow": 26, "signal": 9}),
                       N("m2", "macd", {"fast": 5, "slow": 35, "signal": 5}), N("c", "crosses_above"), E],
                      [W("t", "m1", "in0", "@close"), W("t", "m2", "in0", "@close"), W("m2", "c", "in0", "@macd_line"),
                       W("m2", "c", "in1", "@macd_signal"), W("c", "entry", "in0", "@bool")])
    H["and_gap"] = G([T, N("r", "rsi", RSI), N("c0", "below", {"threshold": 45}), N("c1", "above", {"threshold": 20}),
                      N("and", "and"), E],
                     [W("t", "r", "in0"), W("r", "c0", "in0"), W("r", "c1", "in0"), W("c0", "and", "in2"),
                      W("c1", "and", "in0"), W("and", "entry", "in0")])

    # ---------------------------------------------------------------------------
    # Referenced fixtures and the Wave 1 auto_render of 20 rules
    # ---------------------------------------------------------------------------

    REFS: list[tuple[str, str, dict]] = []
    corpus = json.load(open(os.path.join(HERE, "v2_autorender_corpus.json")))["cases"]
    REFS += [(f"corpus:{c['id']}", f"v2_autorender_corpus:{c['id']}", c["graph"]) for c in corpus]
    v1 = json.load(open(os.path.join(TESTS, "vectors", "v1_autorender.json")))
    for grp in ("api", "botsjson"):
        REFS += [(f"v1:{grp}:{k}", f"v1_autorender:{grp}:{k}", g) for k, g in v1[grp].items()]
    REFS.append(("bench_graph_30", "bench_graph_30", json.load(open(os.path.join(HERE, "bench_graph_30.json")))))


    def _w1_autorender_20() -> dict:
        """Wave 1 auto_render of a strategy with 20 RSI buy rules (one AND)."""
        from models import StrategyRequest
        from nodebuilder.from_rules import auto_render
        from signal_engine import Rule

        rules = [Rule(indicator="rsi", condition="above", value=float(10 + k)) for k in range(20)]
        req = StrategyRequest(ticker="SYN", start="2022-01-03", end="2023-07-01", interval="1d",
                              buy_rules=rules, sell_rules=[Rule(indicator="rsi", condition="above", value=70.0)])
        return auto_render(req).model_dump(by_alias=True)


    H["w1_autorender_20_rules"] = _w1_autorender_20()

    # ---------------------------------------------------------------------------
    # Run the Wave 1 engine
    # ---------------------------------------------------------------------------

    from indicators import OHLCVSeries  # noqa: E402
    from nodebuilder.compile import compile as w1_compile  # noqa: E402
    from nodebuilder.evaluator import compute_indicators_from_specs, evaluate_graph  # noqa: E402
    from nodebuilder.models import Graph  # noqa: E402
    from nodebuilder.run import _NO_EXIT_ATTR  # noqa: E402


    def run_w1(data: dict) -> dict:
        try:
            prog = w1_compile(Graph.model_validate(copy.deepcopy(data)))
        except Exception as exc:  # a Wave 1 refusal
            return {"ok": False, "code": getattr(exc, "code", type(exc).__name__),
                    "error": str(exc)[:200]}
        ohlcv = OHLCVSeries(close=DF["Close"], high=DF["High"], low=DF["Low"], volume=DF["Volume"])
        attrs = compute_indicators_from_specs(prog.indicator_specs, ohlcv)
        for c in ("Close", "Open", "High", "Low", "Volume"):
            attrs["@" + c.lower()] = DF[c]
        attrs[_NO_EXIT_ATTR] = pd.Series(0.0, index=DF.index)
        for op in prog.per_bar_program:
            if op.writes not in attrs:
                attrs[op.writes] = pd.Series(np.nan, index=DF.index, dtype="float64")
        entry, exit_ = [], []
        for i in range(len(DF)):
            r = evaluate_graph(prog, attrs, i)
            if r["entry"]:
                entry.append(i)
            if r["exit"]:
                exit_.append(i)
        return {"ok": True, "entry": entry, "exit": exit_}


    cases = []
    for cid, source, g in REFS:
        cases.append({"id": cid, "source": source, "w1": run_w1(g)})
    for cid, g in H.items():
        cases.append({"id": f"hand:{cid}", "source": "inline", "graph": g, "w1": run_w1(g)})

    out = {
        "_about": ("Frozen Wave 1 (commit 83d5ba1) entry/exit bars on test_rule_coverage._DF, made by "
                   "make_w1_goldens.py from the git-archived Wave 1 engine.  Bars are the indices "
                   "where the signal is True.  Do not regenerate."),
        "bars": len(DF),
        "cases": cases,
    }
    with open(OUT, "w") as f:  # one case per line, so a diff names the case
        f.write("{\n" + f' "_about": {json.dumps(out["_about"])},\n' + f' "bars": {out["bars"]},\n'
                + ' "cases": [\n'
                + ",\n".join("  " + json.dumps(c, separators=(",", ":")) for c in cases)
                + "\n ]\n}\n")
    ok = sum(c["w1"]["ok"] for c in cases)
    print(f"{len(cases)} cases, {ok} computed by Wave 1, {len(cases) - ok} refused")

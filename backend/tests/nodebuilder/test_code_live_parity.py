"""Backtest/live parity with code (F435 W7 item 7.C, plan W7 acceptance,
design note 4.7 "One execution path").

John's vision example: an RSI whose period is
``7 if chf("../vol/threshold") > 2 else 21`` next to a Wrangle that writes
ATR percent and ``@vol_regime``, ANDed into the Entry.  And the adaptive
case as a Wrangle:
``@rsi_adaptive = np.where(@atr_pct > chf("th", default=2.0), sl.rsi(@close, 7), sl.rsi(@close, 21))``.

For each graph:
- the values are what the code says (the period follows the Wrangle's
  spare param; the adaptive column equals the two RSI nodes picked by the
  ATR condition, bar for bar);
- it backtests (trades happen);
- the bot path over the live window (bot_runner.cook_graph_bar on the
  bot's own group, the window from prepare.live_fetch_start, which counts
  lookback_bars) gives the same last-bar Entry and Exit as the full cook,
  for many end bars, as if the bot ran on each of those days;
- the guarded cook a bot tick makes (BotRunner._guarded_cook, executor and
  wall-clock guard) gives the same signals.

Synthetic frames, mocked nothing that trades: no bot is started, no order.
"""
from __future__ import annotations

import asyncio
from datetime import date

import numpy as np
import pandas as pd
import pytest

from bot_manager import BotState
from bot_runner import BotRunner, cook_graph_bar, graph_bot_live
from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.compile import compile as nb_compile
from nodebuilder.evaluator import cook_program, cook_signals
from nodebuilder.models import Graph
from nodebuilder.prepare import live_fetch_start
from nodebuilder.run import run_graph_backtest
from tests.nodebuilder.code_graphs import (adaptive_data, code_bot, daily_frame, graph_data,
                                           node, vision_data, wire)

N_BARS = 1500
MAX_END_BARS = 30


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    return daily_frame(N_BARS, seed=9)


def _program(data: dict):
    return nb_compile(Graph.model_validate(data))


def _column(result, nid: str, name: str) -> np.ndarray:
    return np.asarray(result.streams[nid].column(name), dtype=float)


def _reference(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """RSI 7, RSI 21 and ATR(14) percent from plain nodes (no code)."""
    data = graph_data([
        node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
        node("r7", "rsi", {"period": 7, "out": "@r7"}),
        node("r21", "rsi", {"period": 21, "out": "@r21"}),
        node("atr", "atr", {"period": 14, "source": "@close", "out": "@atr"}),
        node("entry", "entry", {"signal": None}),
        node("lo", "below", {"a": "@r7", "threshold": 0, "out": "@lo"}),
    ], [wire("w1", "t", "r7"), wire("w2", "t", "r21"), wire("w3", "t", "atr"),
        wire("w4", "r7", "lo"), wire("w5", "lo", "entry")])
    result = cook_program(_program(data), df, keep_all=True)
    close = df["Close"].to_numpy(dtype=float)
    return {"r7": _column(result, "r7", "@r7"), "r21": _column(result, "r21", "@r21"),
            "atr_pct": _column(result, "atr", "@atr") / close * 100}


def _assert_live_parity(data: dict, df: pd.DataFrame) -> int:
    """The bot path over each end bar's live window gives the full cook's
    Entry and Exit at that bar.  Returns how many end bars were checked."""
    program = _program(data)
    live = graph_bot_live(program, code_bot(data))
    full_entry, full_exit = cook_signals(live.program, df)
    days = np.array([ts.date() for ts in df.index])
    ends = [e for e in range(len(df))
            if date.fromisoformat(live_fetch_start(program, "1d", None, today=days[e])) > days[0]]
    assert ends, "the frame is too short for one live window"
    step = max(1, len(ends) // MAX_END_BARS)
    checked = 0
    for e in ends[::step]:
        start = date.fromisoformat(live_fetch_start(program, "1d", None, today=days[e]))
        window = df.iloc[int(np.searchsorted(days, start)): e + 1]
        assert len(window) >= 1.5 * program.required_lookback_bars
        _attrs, sigs = cook_graph_bar(live.program, window, None, live.plan, None, "1d")
        assert sigs["entry"] == bool(full_entry[e]), f"entry differs at bar {e}"
        assert sigs["exit"] == bool(full_exit[e]), f"exit differs at bar {e}"
        checked += 1
    assert full_entry[ends].any(), "no entry in the checked span: the test proves little"
    return checked


def _backtest(data: dict, df: pd.DataFrame):
    req = GraphBacktestRequest(graph=data, ticker="AAPL", start=str(df.index[0].date()),
                               end=str(df.index[-1].date()), interval="1d", source="yahoo")
    return run_graph_backtest(req, df=df)


# ---------------------------------------------------------------------------
# John's vision example
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("threshold, period", [(None, 21), (2.0, 21), (2.5, 7)])
def test_the_rsi_period_follows_the_wrangles_threshold(df, threshold, period):
    result = cook_program(_program(vision_data(threshold)), df, keep_all=True)
    ref = _reference(df)
    np.testing.assert_array_equal(_column(result, "rsi", "@rsi"), ref[f"r{period}"])
    np.testing.assert_allclose(_column(result, "vol", "@atr_pct"), ref["atr_pct"], equal_nan=True)
    regime = np.asarray(result.streams["vol"].column("@vol_regime"))
    want = ref["atr_pct"] > (threshold if threshold is not None else 2.0)
    np.testing.assert_array_equal(regime, want)


def test_the_vision_example_backtests(df):
    out = _backtest(vision_data(), df)
    assert out.summary["num_trades"] >= 1
    assert len(out.equity_curve) == len(df)


def test_the_vision_example_keeps_live_parity(df):
    """The RSI period reads only params, so compile knows it (21) and the
    live window is the Wrangle's 500 bars, not a Wilder RSI of the period's
    max (5001 bars, before the compile-time evaluation)."""
    assert _assert_live_parity(vision_data(), df) >= 20


def test_the_vision_example_keeps_live_parity_on_the_short_period(df):
    """threshold 2.5: the expression gives 7 at compile and at the cook."""
    assert _assert_live_parity(vision_data(2.5, lookback_bars=60), df) >= 20


def _plain_period(data: dict, period: int = 21) -> dict:
    data["nodes"]["rsi"]["params"]["period"] = period
    return data


def test_the_lookback_bars_of_the_wrangle_sizes_the_live_window():
    assert _program(_plain_period(vision_data())).required_lookback_bars == 500
    assert _program(_plain_period(vision_data(lookback_bars=260))).required_lookback_bars == 260


def _rsi_window(data: dict) -> int:
    from nodebuilder.compile import check_graph

    return check_graph(Graph.model_validate(data)).analysis.nodes["rsi"].lookback


@pytest.mark.parametrize("threshold, period", [(None, 21), (2.0, 21), (2.5, 7)])
def test_a_param_only_expression_on_the_rsi_period_sizes_the_window_from_its_value(
        threshold, period):
    """``7 if chf("../vol/threshold") > 2 else 21`` reads only params, so
    compile evaluates it with the cook's code: the RSI's window is a Wilder
    RSI of that period (10 x period + 1), not of the period's max (5001)."""
    assert _rsi_window(vision_data(threshold)) == 10 * period + 1
    # The Wrangle's lookback_bars (default 500) is the longest window...
    assert _program(vision_data(threshold)).required_lookback_bars == 500
    # ...and with a short one the RSI's own window is what the bot fetches for.
    assert _program(vision_data(threshold, lookback_bars=60)).required_lookback_bars == \
        10 * period + 1


# ---------------------------------------------------------------------------
# The adaptive Wrangle
# ---------------------------------------------------------------------------


def test_the_adaptive_wrangle_picks_the_rsi_bar_by_bar(df):
    result = cook_program(_program(adaptive_data()), df, keep_all=True)
    ref = _reference(df)
    want = np.where(ref["atr_pct"] > 2.0, ref["r7"], ref["r21"])
    np.testing.assert_array_equal(_column(result, "adapt", "@rsi_adaptive"), want)
    assert (ref["atr_pct"] > 2.0).any() and (ref["atr_pct"] <= 2.0).any()  # both branches used


def test_the_adaptive_wrangle_backtests(df):
    out = _backtest(adaptive_data(), df)
    assert out.summary["num_trades"] >= 1


def test_the_acceptance_line_is_verbatim():
    from tests.nodebuilder.code_graphs import ADAPTIVE_LINE

    assert ADAPTIVE_LINE == ('@rsi_adaptive = np.where(@atr_pct > chf("th", default=2.0), '
                             'sl.rsi(@close, 7), sl.rsi(@close, 21))')


def test_a_comparison_over_the_unannotated_column_once_any_is_accepted(df, monkeypatch):
    """The plan's natural wiring: an ``above`` node reads the unannotated
    @rsi_adaptive.  The kernel refuses a read of dtype "any" today (7.C
    report, Needs from others); with that read accepted, the graph compiles,
    backtests with the same trades as the annotated signal, and keeps live
    parity."""
    from nodebuilder.kernel import schema as kschema

    real = kschema._type_ok
    monkeypatch.setattr(kschema, "_type_ok",
                        lambda want, info: info.dtype == "any" or real(want, info))
    a = _backtest(adaptive_data(exit_node=True), df)
    b = _backtest(adaptive_data(), df)
    assert a.summary["num_trades"] == b.summary["num_trades"] >= 1
    assert [t["price"] for t in a.trades] == [t["price"] for t in b.trades]
    assert _assert_live_parity(adaptive_data(260, exit_node=True), df) >= 20


def test_the_adaptive_wrangle_keeps_live_parity(df):
    # Two Wrangles in a chain add their lookback_bars: 2 x 260 bars.
    assert _assert_live_parity(adaptive_data(lookback_bars=260), df) >= 20


# ---------------------------------------------------------------------------
# The guarded cook of a bot tick
# ---------------------------------------------------------------------------


class _Manager:
    def save(self):
        return None


@pytest.mark.parametrize("make", [vision_data, adaptive_data])
def test_the_guarded_bot_cook_gives_the_same_signals(df, make):
    data = make()
    cfg = code_bot(data)
    live = graph_bot_live(_program(data), cfg)
    assert live.program.has_code
    window = df.iloc[-800:]
    _attrs, plain = cook_graph_bar(live.program, window, None, live.plan, None, "1d")
    runner = BotRunner(cfg, BotState(), _Manager())

    async def go():
        return await runner._guarded_cook(cfg, live, window, None)

    _attrs, guarded = asyncio.run(go())
    assert guarded == plain
    assert runner._tick_seq == 1

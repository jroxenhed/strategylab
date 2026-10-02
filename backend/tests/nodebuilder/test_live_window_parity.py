"""A live bot's cook over its fetch window matches a cook over full history
(F435 W2 item 2.D, plan D5, critic 9).

The live bot fetches only a window of bars: ``nodebuilder.prepare.
live_fetch_start`` turns the program's ``required_lookback_bars`` into a
start date.  For every fixture, a cook over that window (done the way the
bot does it, through ``bot_runner.cook_graph_bar``) must give the same
last-bar Entry and Exit as a cook over the full history, and the same last
value for every float attribute the graph computes.

Fixtures: the backtest-parity strategies (``from_rules`` graphs on their
pickled daily frames) plus hand-built graphs with long or recursive
lookbacks (Wilder RSI, SMA 200, EMA of RSI, MACD crossing its signal), on a
daily fixture frame and on a synthetic 1h frame.  Each case checks many end
bars, as if the bot ran on each of those days.
"""
from __future__ import annotations

import asyncio
import os
import pickle
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from bot_manager import BotConfig, BotState
from bot_runner import cook_graph_bar
from models import Rule, StrategyRequest, TrailingStopConfig
from nodebuilder.compile import compile as nb_compile
from nodebuilder.evaluator import cook_program, cook_signals
from nodebuilder.from_rules import auto_render
from nodebuilder.models import Graph
from nodebuilder.prepare import (
    LIVE_LOOKBACK_FACTOR,
    MIN_LIVE_WINDOW_DAYS,
    graph_lookback_bars,
    live_fetch_start,
    live_window_days,
)

_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "run_backtest_snapshots")

# End bars checked per case.  Spread over the part of the frame where the
# whole live window fits inside the fixture.
_MAX_END_BARS = 50

# Float drift allowed between the window cook and the full cook.  Rolling
# indicators match exactly; recursive ones (EMA, Wilder RSI) keep a tiny
# trace of their seed: at most 4e-5 RSI points on these fixtures.
_RTOL = 1e-4
_ATOL = 1e-4


def _load_df(name: str) -> pd.DataFrame:
    with open(os.path.join(_FIXTURES_DIR, f"{name}_df.pkl"), "rb") as fh:
        return pickle.load(fh)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _parity_strategies() -> list[tuple[str, StrategyRequest]]:
    """The backtest-parity strategies (same rules as test_backtest_parity.py)."""
    rsi_buy = [Rule(indicator="rsi", condition="below", value=30)]
    rsi_sell = [Rule(indicator="rsi", condition="above", value=70)]
    aapl = dict(ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d", source="yahoo")
    spy = dict(ticker="SPY", start="2021-01-01", end="2024-01-01", interval="1d", source="yahoo")
    return [
        ("simple_long_rsi", StrategyRequest(**aapl, buy_rules=rsi_buy, sell_rules=rsi_sell)),
        ("simple_short_rsi", StrategyRequest(**aapl, direction="short", buy_rules=rsi_sell,
                                             sell_rules=rsi_buy)),
        ("macd_crossover", StrategyRequest(
            **spy,
            buy_rules=[Rule(indicator="macd", condition="crosses_above", param="signal")],
            sell_rules=[Rule(indicator="macd", condition="crosses_below", param="signal")])),
        ("trailing_stop_pct", StrategyRequest(**aapl, buy_rules=rsi_buy, sell_rules=rsi_sell,
                                              trailing_stop=TrailingStopConfig(type="pct", value=5.0))),
        ("atr_trailing_stop", StrategyRequest(**aapl, buy_rules=rsi_buy, sell_rules=rsi_sell,
                                              trailing_stop=TrailingStopConfig(type="atr", value=2.0))),
        ("nonzero_costs", StrategyRequest(**aapl, buy_rules=rsi_buy, sell_rules=rsi_sell,
                                          per_share_rate=0.0035, min_per_order=0.35, slippage_bps=5.0)),
        ("not_negated_rule", StrategyRequest(
            **aapl, buy_rules=[Rule(indicator="rsi", condition="below", value=30, negated=True)],
            sell_rules=rsi_sell)),
        ("max_bars_held", StrategyRequest(**aapl, buy_rules=rsi_buy, sell_rules=rsi_sell,
                                          max_bars_held=10)),
    ]


def _graph(nodes: dict, wires: list) -> Graph:
    """nodes: {id: (type, params)}; wires: [(from, to, port)]."""
    return Graph.model_validate({
        "_version": 2,
        "nodes": {nid: {"id": nid, "type": t, "params": p} for nid, (t, p) in nodes.items()},
        "wires": [{"id": f"w{i}", "from": a, "to": b, "to_port": port}
                  for i, (a, b, port) in enumerate(wires)],
    })


_T = {"/t": ("ticker", {})}


def _handbuilt_graphs() -> dict[str, Graph]:
    return {
        # Wilder RSI: the slowest-decaying seed of the engine's indicators.
        "wilder_rsi": _graph(
            {**_T, "/rsi": ("rsi", {"period": 14, "type": "wilder"}),
             "/lo": ("below", {"a": "@rsi", "threshold": 30, "out": "@lo"}),
             "/hi": ("above", {"a": "@rsi", "threshold": 70, "out": "@hi"}),
             "/entry": ("entry", {}), "/exit": ("exit", {})},
            [("/t", "/rsi", "in0"), ("/rsi", "/lo", "in0"), ("/rsi", "/hi", "in0"),
             ("/lo", "/entry", "in0"), ("/hi", "/exit", "in0")]),
        # A long rolling window: needs more than the 30-day floor.
        "sma200_cross": _graph(
            {**_T, "/ma": ("sma", {"period": 200}),
             "/x": ("crosses_above", {"a": "@close", "b": "@sma", "out": "@x"}),
             "/y": ("crosses_below", {"a": "@close", "b": "@sma", "out": "@y"}),
             "/entry": ("entry", {}), "/exit": ("exit", {})},
            [("/t", "/ma", "in0"), ("/ma", "/x", "in0"), ("/ma", "/y", "in0"),
             ("/x", "/entry", "in0"), ("/y", "/exit", "in0")]),
        # A recursive indicator over a recursive indicator (lookbacks add up).
        "ema_of_rsi": _graph(
            {**_T, "/rsi": ("rsi", {"period": 14, "type": "wilder"}),
             "/ema": ("ema", {"period": 20, "source": "@rsi"}),
             "/x": ("crosses_above", {"a": "@rsi", "b": "@ema", "out": "@x"}),
             "/entry": ("entry", {})},
            [("/t", "/rsi", "in0"), ("/rsi", "/ema", "in0"), ("/ema", "/x", "in0"),
             ("/x", "/entry", "in0")]),
        "macd_signal_cross": _graph(
            {**_T, "/macd": ("macd", {}),
             "/x": ("crosses_above", {"a": "@macd_line", "b": "@macd_signal", "out": "@x"}),
             "/y": ("crosses_below", {"a": "@macd_line", "b": "@macd_signal", "out": "@y"}),
             "/entry": ("entry", {}), "/exit": ("exit", {})},
            [("/t", "/macd", "in0"), ("/macd", "/x", "in0"), ("/macd", "/y", "in0"),
             ("/x", "/entry", "in0"), ("/y", "/exit", "in0")]),
    }


def _hourly_df(sessions: int = 420, seed: int = 5) -> pd.DataFrame:
    """Synthetic regular-session 1h bars (7 a day, 9:30 to 15:30 ET)."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2024-01-02", periods=sessions)
    stamps = [pd.Timestamp(f"{d.date()} {h:02d}:30", tz="America/New_York")
              for d in days for h in range(9, 16)]
    n = len(stamps)
    t = np.arange(n)
    close = 100 + 5 * np.sin(t / 40) + np.cumsum(rng.normal(0, 0.4, n))
    return pd.DataFrame({
        "Open": close + rng.normal(0, 0.1, n),
        "High": close + np.abs(rng.normal(0, 0.4, n)),
        "Low": close - np.abs(rng.normal(0, 0.4, n)),
        "Close": close,
        "Volume": rng.integers(10_000, 100_000, n).astype(float),
    }, index=pd.DatetimeIndex(stamps))


def _daily_df(n: int = 1800, seed: int = 9) -> pd.DataFrame:
    """Synthetic daily bars, long enough for an SMA 200 live window."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100 + 8 * np.sin(t / 30) + np.cumsum(rng.normal(0, 0.8, n))
    idx = pd.bdate_range("2018-01-02", periods=n, tz="America/New_York")
    return pd.DataFrame({
        "Open": close + rng.normal(0, 0.2, n),
        "High": close + np.abs(rng.normal(0, 0.8, n)),
        "Low": close - np.abs(rng.normal(0, 0.8, n)),
        "Close": close,
        "Volume": rng.integers(1e6, 5e6, n).astype(float),
    }, index=idx)


def _cases():
    cases = []
    for name, req in _parity_strategies():
        cases.append(pytest.param(name, auto_render(req), "1d", req.trailing_stop, id=f"parity-{name}"))
    for name, graph in _handbuilt_graphs().items():
        # SMA 200's live window (1.5 x 201 bars since RECURSIVE_FACTOR 10)
        # now fits in the 3-year fixture, so every hand-built graph runs on it.
        cases.append(pytest.param("macd_crossover", graph, "1d", None, id=f"fixture-{name}"))
        cases.append(pytest.param("@daily", graph, "1d", None, id=f"daily-{name}"))
        cases.append(pytest.param("@hourly", graph, "1h", None, id=f"hourly-{name}"))
    return cases


def _frame(name: str) -> pd.DataFrame:
    if name == "@hourly":
        return _hourly_df()
    if name == "@daily":
        return _daily_df()
    return _load_df(name)


# ---------------------------------------------------------------------------
# The window the bot would fetch on the day of bar e
# ---------------------------------------------------------------------------

def _live_window(df: pd.DataFrame, days: np.ndarray, e: int, program, interval: str,
                 trailing_stop) -> pd.DataFrame | None:
    """The bars the bot sees when bar e is the newest: from live_fetch_start
    (with "today" = bar e's date) up to bar e.  None when that window would
    start before the fixture does (the fixture cannot show it).  *days* is
    the date of each bar."""
    start = date.fromisoformat(live_fetch_start(program, interval, trailing_stop, today=days[e]))
    if start <= days[0]:
        return None
    first = int(np.searchsorted(days, start))  # bars are in time order
    return df.iloc[first: e + 1]


def _float_columns(program, df: pd.DataFrame) -> dict[tuple[str, str], np.ndarray]:
    """Every float point column the graph's nodes compute (bar fields excluded)."""
    result = cook_program(program, df, keep_all=True)
    out = {}
    for nid, stream in result.streams.items():
        for name in stream.names():
            if name in ("@open", "@high", "@low", "@close", "@volume", "@time", "@index"):
                continue
            if stream.kind(name) != "point" or stream.dtype(name) != "float":
                continue
            out[(nid, name)] = np.asarray(stream.column(name), dtype=float)
    return out


@pytest.mark.parametrize("frame, graph, interval, trailing_stop", _cases())
def test_cook_over_live_window_matches_full_history(frame, graph, interval, trailing_stop):
    # The regime fixtures are not listed: their regime frame is a reference
    # Ticker, whose own live window test_reference_tickers.py covers.
    program = nb_compile(graph)
    df = _frame(frame)
    full_entry, full_exit = cook_signals(program, df)
    full_floats = _float_columns(program, df)
    need = graph_lookback_bars(program, trailing_stop)

    days = np.array([ts.date() for ts in df.index])
    ends = [e for e in range(len(df))
            if date.fromisoformat(live_fetch_start(program, interval, trailing_stop, today=days[e])) > days[0]]
    assert ends, "the fixture is too short to hold one live window"
    step = max(1, len(ends) // _MAX_END_BARS)
    checked = 0
    for e in ends[::step]:
        window = _live_window(df, days, e, program, interval, trailing_stop)
        # The calendar window holds the factor times the lookback in bars.
        assert len(window) >= LIVE_LOOKBACK_FACTOR * need, (e, len(window), need)

        attrs, sigs = cook_graph_bar(program, window, trailing_stop)
        assert sigs["entry"] == bool(full_entry[e]), f"entry differs at bar {e} ({df.index[e]})"
        assert sigs["exit"] == bool(full_exit[e]), f"exit differs at bar {e} ({df.index[e]})"
        if trailing_stop is not None and trailing_stop.type == "atr":
            assert np.isfinite(attrs["atr"].iloc[-1])

        for key, column in _float_columns(program, window).items():
            got, want = column[-1], full_floats[key][e]
            assert (np.isnan(got) and np.isnan(want)) or np.isclose(got, want, rtol=_RTOL, atol=_ATOL), (
                f"{key} at bar {e}: window {got} vs full {want}")
        checked += 1
    assert checked >= min(len(ends), 20)


# ---------------------------------------------------------------------------
# The window arithmetic
# ---------------------------------------------------------------------------

def test_window_never_shorter_than_the_old_30_days():
    assert live_window_days(0, "1d") == MIN_LIVE_WINDOW_DAYS
    assert live_window_days(2, "5m") == MIN_LIVE_WINDOW_DAYS
    assert live_window_days(15, "1h") == MIN_LIVE_WINDOW_DAYS


def test_window_grows_with_the_lookback_and_slower_intervals():
    assert live_window_days(200, "1d") > live_window_days(50, "1d") > MIN_LIVE_WINDOW_DAYS
    assert live_window_days(600, "1d") > live_window_days(600, "1h") > live_window_days(600, "5m")
    # An interval the table does not know counts as one bar a session.
    assert live_window_days(200, "3h") == live_window_days(200, "1d")


# ---------------------------------------------------------------------------
# required_lookback_bars on its own (RECURSIVE_FACTOR)
# ---------------------------------------------------------------------------

# (graph nodes, attr, abs tolerance, rel tolerance).  Measured worst cases at
# RECURSIVE_FACTOR 10: Wilder RSI 0.012 points, EMA 4e-8, MACD 5e-11.
_LOOKBACK_CASES = {
    "rsi_wilder_14": ({"/i": ("rsi", {"period": 14, "type": "wilder"})}, "@rsi", 0.05, None),
    "ema_50": ({"/i": ("ema", {"period": 50})}, "@ema", None, 1e-6),
    "macd": ({"/i": ("macd", {})}, "@macd_line", None, 1e-6),
}


def _lookback_drift(kind: str, step: int = 5) -> tuple[float, float, int]:
    """Worst (abs, rel) gap between the last value of a cook over exactly
    required_lookback_bars bars and the full-history value, over the parity
    fixtures, plus the number of windows checked."""
    nodes, attr, _abs, _rel = _LOOKBACK_CASES[kind]
    program = nb_compile(_graph(
        {**_T, **nodes, "/c": ("below", {"a": attr, "threshold": 0}), "/entry": ("entry", {})},
        [("/t", "/i", "in0"), ("/i", "/c", "in0"), ("/c", "/entry", "in0")]))
    need = program.required_lookback_bars
    worst_abs = worst_rel = 0.0
    checked = 0
    for name in ("simple_long_rsi", "macd_crossover"):
        df = _load_df(name)
        full = cook_program(program, df, keep={"/i"}).column("/i", attr)
        for end in range(need - 1, len(df), step):
            window = df.iloc[end - need + 1:end + 1]
            last = cook_program(program, window, keep={"/i"}).column("/i", attr)[-1]
            gap = abs(last - full[end])
            worst_abs = max(worst_abs, gap)
            worst_rel = max(worst_rel, gap / max(abs(full[end]), 1e-9))
            checked += 1
    return worst_abs, worst_rel, checked


@pytest.mark.parametrize("kind", sorted(_LOOKBACK_CASES))
def test_lookback_alone_matches_full_history(kind):
    """required_lookback_bars means what it says: a cook over exactly that
    many bars gives the full-history value (RECURSIVE_FACTOR covers the seed
    of recursive smoothers), before the live window adds its margin."""
    _nodes, _attr, abs_tol, rel_tol = _LOOKBACK_CASES[kind]
    worst_abs, worst_rel, checked = _lookback_drift(kind)
    assert checked >= 40  # EMA 50 needs 500 bars, so fewer windows fit
    if abs_tol is not None:
        assert worst_abs <= abs_tol, (kind, worst_abs)
    if rel_tol is not None:
        assert worst_rel <= rel_tol, (kind, worst_rel)


def test_old_recursive_factor_3_was_too_small(monkeypatch):
    """The evidence for raising RECURSIVE_FACTOR: at 3, a Wilder RSI 14 over
    exactly its lookback was several RSI points off the full history."""
    import nodebuilder.trading.nodes_indicators as nodes_indicators

    monkeypatch.setattr(nodes_indicators, "RECURSIVE_FACTOR", 3)
    worst_abs, _rel, _n = _lookback_drift("rsi_wilder_14")
    assert worst_abs > 1.0


def test_live_window_for_a_recursive_indicator_is_15x_its_period():
    """RECURSIVE_FACTOR x LIVE_LOOKBACK_FACTOR keeps a Wilder RSI's live window
    where 2.D measured it (15x the period, 4e-5 RSI points of drift)."""
    from nodebuilder.trading.nodes_indicators import RECURSIVE_FACTOR

    assert RECURSIVE_FACTOR * LIVE_LOOKBACK_FACTOR >= 15


def test_atr_trailing_stop_raises_the_lookback():
    program = nb_compile(_graph(
        {**_T, "/a": ("above", {"a": "@close", "threshold": 0, "out": "@a"}), "/entry": ("entry", {})},
        [("/t", "/a", "in0"), ("/a", "/entry", "in0")]))
    assert graph_lookback_bars(program, TrailingStopConfig(type="atr", value=2.0)) >= 15
    assert graph_lookback_bars(program, TrailingStopConfig(type="pct", value=2.0)) == program.required_lookback_bars


# ---------------------------------------------------------------------------
# The bot fetches that window
# ---------------------------------------------------------------------------

class _Manager:
    def save(self):
        pass


def _bot(graph: Graph, interval: str) -> BotConfig:
    return BotConfig(bot_id="bot-window", strategy_name="window", symbol="AAPL", interval=interval,
                     buy_rules=[], sell_rules=[], allocated_capital=10_000.0, broker="alpaca",
                     data_source="yahoo", direction="long", kind="graph", graph=graph)


@pytest.mark.parametrize("interval", ["1d", "1h", "5m"])
def test_graph_bot_fetches_from_live_fetch_start(interval):
    from bot_runner import BotRunner

    graph = _handbuilt_graphs()["sma200_cross"]
    program = nb_compile(graph)
    df = _hourly_df(sessions=60)
    fetch = AsyncMock(return_value=df)
    provider = MagicMock()
    provider.get_positions = MagicMock(return_value=[])
    runner = BotRunner(_bot(graph, interval), BotState(), _Manager())

    async def go():
        with patch("bot_runner.fetch_ohlcv_async", fetch), \
             patch("bot_runner.get_trading_provider", return_value=provider), \
             patch("bot_runner.notify_error", new_callable=AsyncMock):
            await runner._tick()

    asyncio.run(go())
    fetch.assert_awaited_once()
    _symbol, start, end, got_interval, _source = fetch.await_args.args
    assert got_interval == interval
    assert start == live_fetch_start(program, interval, None)
    assert end == date.today().isoformat()
    days = (date.today() - date.fromisoformat(start)).days
    if interval == "5m":
        # 5 x 201 bars of 5m fit in 30 days: the old floor still holds.
        assert days == MIN_LIVE_WINDOW_DAYS
    else:
        # SMA 200 needs more than the 30 days every bot fetched before W2.
        assert days > MIN_LIVE_WINDOW_DAYS
    provider.submit_order.assert_not_called()


def test_rule_bot_still_fetches_30_days():
    from bot_runner import BotRunner

    cfg = BotConfig(bot_id="bot-rule", strategy_name="rule", symbol="AAPL", interval="1d",
                    buy_rules=[Rule(indicator="rsi", condition="below", value=-1)],
                    sell_rules=[], allocated_capital=10_000.0, broker="alpaca",
                    data_source="yahoo", direction="long")
    fetch = AsyncMock(return_value=_hourly_df(sessions=60))
    provider = MagicMock()
    provider.get_positions = MagicMock(return_value=[])
    runner = BotRunner(cfg, BotState(), _Manager())

    async def go():
        with patch("bot_runner.fetch_ohlcv_async", fetch), \
             patch("bot_runner.get_trading_provider", return_value=provider):
            await runner._tick()

    asyncio.run(go())
    start = fetch.await_args.args[1]
    assert (date.today() - date.fromisoformat(start)).days == 30
    provider.submit_order.assert_not_called()


def test_short_fetch_warns_once_per_graph():
    """A provider that clamps history (Yahoo 1m keeps 7 days) gives fewer bars
    than the graph needs: the bot says so once, not on every bar."""
    from bot_runner import BotRunner

    graph = _handbuilt_graphs()["sma200_cross"]
    provider = MagicMock()
    provider.get_positions = MagicMock(return_value=[])
    state = BotState()
    runner = BotRunner(_bot(graph, "1h"), state, _Manager())
    short = _hourly_df(sessions=10)

    async def go():
        with patch("bot_runner.get_trading_provider", return_value=provider), \
             patch("bot_runner.notify_error", new_callable=AsyncMock):
            for n in (60, 61, 62):  # three new bars
                with patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=short.iloc[:n])):
                    await runner._tick()

    asyncio.run(go())
    warnings = [e for e in state.activity_log if "bars of history" in e["msg"]]
    assert len(warnings) == 1
    assert "Graph wants" in warnings[0]["msg"]


def _tick_bot(cfg: BotConfig, state: BotState, frames: list[pd.DataFrame]) -> list:
    """Run one tick per frame; return the fetch mock's await args."""
    from bot_runner import BotRunner

    provider = MagicMock()
    provider.get_positions = MagicMock(return_value=[])
    runner = BotRunner(cfg, state, _Manager())
    calls: list = []

    async def go():
        with patch("bot_runner.get_trading_provider", return_value=provider), \
             patch("bot_runner.notify_error", new_callable=AsyncMock):
            for f in frames:
                fetch = AsyncMock(return_value=f)
                with patch("bot_runner.fetch_ohlcv_async", fetch):
                    await runner._tick()
                calls.append(fetch.await_args.args)

    asyncio.run(go())
    provider.submit_order.assert_not_called()
    return calls


def test_short_history_warning_uses_the_window_factor():
    """F435 W2 LT-5: more bars than the lookback but fewer than
    LIVE_LOOKBACK_FACTOR x it used to pass without a word, though recursive
    indicators may not have settled to the backtest's values there."""
    from nodebuilder.prepare import LIVE_LOOKBACK_FACTOR, graph_lookback_bars

    graph = _handbuilt_graphs()["sma200_cross"]
    need = graph_lookback_bars(nb_compile(graph))
    n = int(need * (1 + LIVE_LOOKBACK_FACTOR) / 2)   # between 1x and the factor
    assert need < n < LIVE_LOOKBACK_FACTOR * need
    df = _hourly_df(sessions=120)
    assert len(df) > n + 3
    state = BotState()
    _tick_bot(_bot(graph, "1h"), state, [df.iloc[: n + k] for k in range(3)])
    warnings = [e["msg"] for e in state.activity_log if "bars of history" in e["msg"]]
    assert len(warnings) == 1
    assert "may differ from the backtest" in warnings[0]


def test_provider_max_days():
    from shared import _IBKR_INTRADAY_MAX_DAYS, _INTERVAL_MAX_DAYS, provider_max_days

    assert provider_max_days("yahoo", "1m") == 7 == _INTERVAL_MAX_DAYS["1m"]
    assert provider_max_days("yahoo", "1h") == 730
    assert provider_max_days("yahoo", "1d") is None
    for iv in ("1m", "5m", "15m", "30m", "1h"):
        assert provider_max_days("ibkr", iv) == _IBKR_INTRADAY_MAX_DAYS == 30
    assert provider_max_days("ibkr", "1d") is None
    assert provider_max_days("alpaca", "1m") is None
    assert provider_max_days("alpaca-iex", "5m") is None


def test_live_fetch_start_never_asks_past_the_provider_limit():
    program = nb_compile(_handbuilt_graphs()["sma200_cross"])
    today = date(2026, 6, 1)
    free = (today - date.fromisoformat(live_fetch_start(program, "1h", None, today=today))).days
    assert free > 30
    capped = live_fetch_start(program, "1h", None, today=today, max_days=30)
    assert (today - date.fromisoformat(capped)).days == 30
    # A limit wider than the window changes nothing.
    assert live_fetch_start(program, "1h", None, today=today, max_days=730) == \
        live_fetch_start(program, "1h", None, today=today)


def test_ibkr_intraday_graph_bot_window_is_capped_and_warns_once():
    """F435 W2 LT-4: a 1h SMA 200 graph on IBKR used to ask the Gateway for
    about 80 days of hourly bars; the bot now asks for at most 30 and says
    once that the provider cuts the window."""
    graph = _handbuilt_graphs()["sma200_cross"]
    cfg = _bot(graph, "1h").model_copy(update={"data_source": "ibkr"})
    state = BotState()
    df = _hourly_df(sessions=120)
    calls = _tick_bot(cfg, state, [df.iloc[: len(df) - 2 + k] for k in range(3)])
    for args in calls:
        start = args[1]
        assert (date.today() - date.fromisoformat(start)).days == 30
        assert args[4] == "ibkr"
    warnings = [e["msg"] for e in state.activity_log if "serves at most" in e["msg"]]
    assert len(warnings) == 1 and "ibkr" in warnings[0] and "30 days" in warnings[0]


def test_yahoo_graph_bot_within_limit_does_not_warn():
    graph = _handbuilt_graphs()["sma200_cross"]
    state = BotState()
    _tick_bot(_bot(graph, "1h"), state, [_hourly_df(sessions=120)])
    assert not [e for e in state.activity_log if "serves at most" in e["msg"]]


def test_ibkr_daily_duration_rounds_years_up_and_trims_to_start():
    """F435 W2 LT-4: 788 days became "2 Y" (about 504 bars, fewer than asked).
    Now "3 Y", trimmed back to the asked start."""
    import concurrent.futures
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    import shared

    end = datetime(2026, 6, 1)
    bars = [SimpleNamespace(open=1.0, high=1.0, low=1.0, close=1.0, volume=1,
                            date=(end - timedelta(days=d)).date())
            for d in range(3 * 365, -1, -1)]
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.reqHistoricalDataAsync = MagicMock(return_value="coro")

    def fake_threadsafe(coro, loop):
        f = concurrent.futures.Future()
        f.set_result(bars)
        return f

    prov = shared.IBKRDataProvider(ib, loop=None)
    start = (end - timedelta(days=788)).strftime("%Y-%m-%d")
    with patch("asyncio.run_coroutine_threadsafe", fake_threadsafe):
        df = prov.fetch("AAPL", start, end.strftime("%Y-%m-%d"), "1d")
    assert ib.reqHistoricalDataAsync.call_args.kwargs["durationStr"] == "3 Y"
    assert df.index[0] >= pd.Timestamp(start)
    assert (df.index[0] - pd.Timestamp(start)).days <= 1
    assert len(df) == 789

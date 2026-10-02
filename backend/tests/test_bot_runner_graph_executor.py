"""A graph bot's cook never blocks the polling loop (F435 W2 item 2.D, critic 30).

Two graph bots tick on the same event loop.  One has a deliberately slow
cook (or a slow compile); the other bot's tick must still finish on time,
and a heartbeat coroutine on the loop must keep running while the slow cook
is in progress.  The slow part sleeps in its worker thread: if it ran on the
loop thread instead, the sleep would freeze the loop and both checks fail.

Brokers are stubs; no bot is started and no order is placed.
"""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

import bot_runner
from bot_manager import BotConfig, BotState
from nodebuilder.models import Graph

SLOW_SECS = 1.2
# A tick that does not wait on the slow bot finishes well inside this.
ON_TIME_SECS = 0.6
# The loop must never stall longer than this while the slow cook runs.
MAX_LOOP_GAP_SECS = 0.25

# The slow bot's bars end at this close, so the spies can tell the bots apart.
SLOW_MARK = 123.0


def _graph() -> Graph:
    """RSI below 0 -> Entry: never fires, so a tick places no order."""
    return Graph.model_validate({
        "_version": 2,
        "nodes": {
            "/t": {"id": "/t", "type": "ticker", "params": {}},
            "/rsi": {"id": "/rsi", "type": "rsi", "params": {"period": 14, "type": "wilder"}},
            "/lo": {"id": "/lo", "type": "below", "params": {"a": "@rsi", "threshold": 0, "out": "@lo"}},
            "/entry": {"id": "/entry", "type": "entry", "params": {}},
        },
        "wires": [
            {"id": "w1", "from": "/t", "to": "/rsi", "to_port": "in0"},
            {"id": "w2", "from": "/rsi", "to": "/lo", "to_port": "in0"},
            {"id": "w3", "from": "/lo", "to": "/entry", "to_port": "in0"},
        ],
    })


def _df(last_close: float, n: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    close[-1] = last_close
    idx = pd.date_range(end="2026-01-10", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1,
                         "Close": close, "Volume": np.full(n, 1e6)}, index=idx)


def _config(bot_id: str, symbol: str) -> BotConfig:
    return BotConfig(bot_id=bot_id, strategy_name=bot_id, symbol=symbol, interval="1d",
                     buy_rules=[], sell_rules=[], allocated_capital=10_000.0, broker="alpaca",
                     data_source="yahoo", direction="long", kind="graph", graph=_graph())


class _Manager:
    def save(self):
        pass


class _Provider:
    """Broker stub: no positions; any order call fails the test."""

    def __init__(self):
        self.submit_order = MagicMock(side_effect=AssertionError("no order may be placed"))
        self.close_position = MagicMock(side_effect=AssertionError("no order may be placed"))

    def get_positions(self):
        return []


_FRAMES = {"SLOW": _df(SLOW_MARK), "FAST": _df(100.0)}


async def _fetch(symbol, start, end, interval, source):
    return _FRAMES[symbol]


def _is_slow(attrs_or_df) -> bool:
    close = attrs_or_df["@close"] if isinstance(attrs_or_df, dict) else attrs_or_df["Close"]
    return float(close.iloc[-1]) == SLOW_MARK


async def _race(slow_patch):
    """Tick the slow bot and the fast bot together, with a loop heartbeat.

    Returns (fast_secs, slow_secs, max_gap, runners)."""
    slow = bot_runner.BotRunner(_config("bot-slow", "SLOW"), BotState(), _Manager())
    fast = bot_runner.BotRunner(_config("bot-fast", "FAST"), BotState(), _Manager())
    done: dict[str, float] = {}
    beats: list[float] = []
    t0 = time.perf_counter()

    async def tick(name, runner):
        await runner._tick()
        done[name] = time.perf_counter() - t0

    async def heartbeat():
        while "slow" not in done:
            beats.append(time.perf_counter())
            await asyncio.sleep(0.02)

    with patch("bot_runner.fetch_ohlcv_async", side_effect=_fetch), \
         patch("bot_runner.get_trading_provider", return_value=_Provider()), \
         patch("bot_runner.notify_error", new_callable=AsyncMock), \
         patch("bot_runner.notify_entry", new_callable=AsyncMock), \
         patch("bot_runner.notify_exit", new_callable=AsyncMock), \
         slow_patch:
        await asyncio.gather(tick("slow", slow), tick("fast", fast), heartbeat())

    gaps = np.diff(beats) if len(beats) > 1 else np.array([0.0])
    return done["fast"], done["slow"], float(gaps.max()), (slow, fast)


def test_slow_cook_does_not_delay_the_other_bot():
    from nodebuilder import evaluator

    real_eval = evaluator.evaluate_graph
    threads: dict[str, int] = {}

    def eval_spy(program, attrs, i):
        if _is_slow(attrs):
            threads["slow"] = threading.get_ident()
            time.sleep(SLOW_SECS)  # stands in for a heavy cook
        else:
            threads["fast"] = threading.get_ident()
        return real_eval(program, attrs, i)

    loop_thread = threading.get_ident()
    fast_secs, slow_secs, max_gap, (slow, fast) = asyncio.run(
        _race(patch("nodebuilder.evaluator.evaluate_graph", side_effect=eval_spy)))

    assert slow_secs >= SLOW_SECS
    assert fast_secs < ON_TIME_SECS, f"the fast bot waited {fast_secs:.2f}s on the slow cook"
    assert max_gap < MAX_LOOP_GAP_SECS, f"the event loop stalled {max_gap:.2f}s"
    assert threads["slow"] != loop_thread and threads["fast"] != loop_thread
    # Both ticks really cooked their new bar.
    assert slow.state.last_bar_time == str(_FRAMES["SLOW"].index[-1])
    assert fast.state.last_bar_time == str(_FRAMES["FAST"].index[-1])
    assert slow.state.status != "error" and fast.state.status != "error"


def test_slow_indicator_work_does_not_delay_the_other_bot():
    """The bar prep (indicator inputs, ATR) is part of the same executor call."""
    real_build = bot_runner.build_graph_attrs

    def build_spy(program, df, trailing_stop):
        if _is_slow(df):
            time.sleep(SLOW_SECS)
        return real_build(program, df, trailing_stop)

    fast_secs, slow_secs, max_gap, _ = asyncio.run(
        _race(patch("bot_runner.build_graph_attrs", side_effect=build_spy)))
    assert slow_secs >= SLOW_SECS
    assert fast_secs < ON_TIME_SECS
    assert max_gap < MAX_LOOP_GAP_SECS


def test_slow_compile_does_not_delay_the_other_bot():
    """Compiling (on a graph change) runs in the executor too."""
    real_compile = bot_runner.compile_bot_graph

    def compile_spy(graph, bot_id=""):
        if bot_id == "bot-slow":
            time.sleep(SLOW_SECS)
        return real_compile(graph, bot_id)

    fast_secs, slow_secs, max_gap, _ = asyncio.run(
        _race(patch("bot_runner.compile_bot_graph", side_effect=compile_spy)))
    assert slow_secs >= SLOW_SECS
    assert fast_secs < ON_TIME_SECS
    assert max_gap < MAX_LOOP_GAP_SECS


def _tick(runner, df, provider, extra=()):
    async def go():
        patches = [
            patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=df)),
            patch("bot_runner.get_trading_provider", return_value=provider),
            patch("bot_runner.notify_error", new_callable=AsyncMock),
            *extra,
        ]
        for p in patches:
            p.start()
        try:
            await runner._tick()
        finally:
            for p in reversed(patches):
                p.stop()
    asyncio.run(go())


def test_compiles_once_while_the_graph_is_unchanged():
    calls = []
    real_compile = bot_runner.compile_bot_graph

    def compile_spy(graph, bot_id=""):
        calls.append(bot_id)
        return real_compile(graph, bot_id)

    runner = bot_runner.BotRunner(_config("bot-a", "FAST"), BotState(), _Manager())
    provider = _Provider()
    df = _FRAMES["FAST"]
    spy = patch("bot_runner.compile_bot_graph", side_effect=compile_spy)
    for n in (100, 101, 102):  # three new bars, same graph
        _tick(runner, df.iloc[:n], provider, extra=[spy])
    assert calls == ["bot-a"]

    # A new graph (another threshold) compiles again.
    data = runner.config.graph.model_dump(by_alias=True)
    data["nodes"]["/lo"]["params"]["threshold"] = -5
    runner.config = runner.config.model_copy(update={"graph": Graph.model_validate(data)})
    _tick(runner, df.iloc[:103], provider, extra=[spy])
    assert calls == ["bot-a", "bot-a"]


def test_cook_error_retries_the_bar_and_places_no_order():
    """An error inside the cook (not only in the bar prep) leaves the bar
    for the next tick, so its exit checks are not skipped as "same bar".
    F435 W2 LT-1: the tick raises GraphCookError so run() counts it toward
    MAX_CONSEC_ERRORS (it used to log a WARN and return)."""
    runner = bot_runner.BotRunner(_config("bot-a", "FAST"), BotState(), _Manager())
    runner.state.last_bar_time = "earlier"
    provider = _Provider()
    with pytest.raises(bot_runner.GraphCookError, match="Graph cook failed: boom"):
        _tick(runner, _FRAMES["FAST"], provider,
              extra=[patch("nodebuilder.evaluator.evaluate_graph", side_effect=RuntimeError("boom"))])
    assert runner.state.last_bar_time == "earlier"
    provider.submit_order.assert_not_called()


def test_evaluate_graph_is_only_called_from_the_executor_function():
    """Plan W2 acceptance: the only evaluate_graph( call in bot_runner.py
    sits inside cook_graph_bar, which _tick passes to _run_in_executor."""
    import inspect
    import re

    source = inspect.getsource(bot_runner)
    calls = [m.start() for m in re.finditer(r"evaluate_graph\(", source)]
    assert calls, "bot_runner no longer cooks through evaluate_graph"
    cook_src = inspect.getsource(bot_runner.cook_graph_bar)
    start = source.index(cook_src)
    assert all(start <= c < start + len(cook_src) for c in calls)
    assert "_run_in_executor(\n                    cook_graph_bar," in inspect.getsource(
        bot_runner.BotRunner._tick)

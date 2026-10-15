"""The wall-clock guard, the leaked-cook counter, the kill switch and
code_capabilities (F435 W7 item 7.A, design note 4.7 and 4.9).

The slow cooks here sleep for a fraction of a second and end by themselves,
so the suite leaks nothing: each test waits for its leaked thread to end.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time

import numpy as np
import pandas as pd
import pytest

from nodebuilder.code import (
    BOT_COOK_TIMEOUT_S,
    ROUTE_COOK_TIMEOUT_S,
    CodeTimeout,
    await_guarded,
    call_guarded,
    capabilities,
    code_enabled,
    current_guard,
    leaked_cooks,
    prepare,
    run,
)
from nodebuilder.code.errors import CODE_TIMEOUT
from nodebuilder.kernel.stream import ColumnStore, Stream

# The design note's bot test uses a 0.2 s guard; a slow cook sleeps well past it.
# The margin keeps the cook on its thread (not still queued) when the guard fires.
SLOW_S = 0.7
LIMIT_S = 0.2


def _stream() -> Stream:
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    return Stream.empty(ColumnStore(idx)).with_point("@close", np.arange(5.0), "/t", "float")


def _wait_for_leaks(target: int, deadline_s: float = 5.0) -> None:
    end = time.monotonic() + deadline_s
    while leaked_cooks() != target and time.monotonic() < end:
        time.sleep(0.01)
    assert leaked_cooks() == target


def _slow_cook(seconds: float = SLOW_S):
    """A cook whose one Wrangle sleeps (the design note's timeout test shape)."""
    prepared = prepare(f"import time\ntime.sleep({seconds})\n@done = 1", "wrangle", "n_slow")
    assert prepared.ok, prepared.diagnostics
    stream = _stream()

    def cook():
        return run(prepared, stream, node_name="slow_wrangle")
    return cook


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_the_limits_are_10_s_for_a_bot_and_60_s_for_routes():
    assert (BOT_COOK_TIMEOUT_S, ROUTE_COOK_TIMEOUT_S) == (10, 60)
    limits = capabilities()["limits"]
    assert limits == {"max_source_bytes": 8192, "default_lookback_bars": 500,
                      "cook_timeout_s": {"bot": 10, "backtest": 60}}


def test_a_cook_inside_the_limit_returns_its_result():
    assert call_guarded(lambda a, b: a + b, 2, 3, timeout_s=5) == 5


def test_a_cooks_own_exception_passes_through():
    def boom():
        raise KeyError("x")
    with pytest.raises(KeyError):
        call_guarded(boom, timeout_s=5)


def test_a_slow_cook_times_out_naming_its_code_node_and_leaks_one_thread(caplog):
    before = leaked_cooks()
    caplog.set_level(logging.WARNING, logger="nodebuilder.code.runtime")
    started = time.monotonic()
    with pytest.raises(CodeTimeout) as info:
        call_guarded(_slow_cook(), timeout_s=LIMIT_S, label="bot b1 tick 7")
    assert time.monotonic() - started < SLOW_S          # did not wait for the sleep
    err = info.value
    assert err.code == CODE_TIMEOUT
    assert err.node_id == "n_slow" and err.node_name == "slow_wrangle"
    assert err.message == f"slow_wrangle ran longer than {LIMIT_S:g} s"
    assert leaked_cooks() == before + 1
    assert any("leaked" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
    # The thread ends by itself; the counter comes back down.
    _wait_for_leaks(before)


def test_a_timeout_outside_any_code_node_names_the_cook():
    before = leaked_cooks()
    with pytest.raises(CodeTimeout) as info:
        call_guarded(time.sleep, SLOW_S, timeout_s=LIMIT_S)
    assert info.value.node_id is None
    assert info.value.message == f"the cook ran longer than {LIMIT_S:g} s"
    _wait_for_leaks(before)


async def test_await_guarded_times_out_without_blocking_the_event_loop():
    before = leaked_cooks()
    ticks = 0

    async def other_bot():
        nonlocal ticks
        for _ in range(5):
            await asyncio.sleep(0.01)
            ticks += 1

    other = asyncio.create_task(other_bot())
    with pytest.raises(CodeTimeout) as info:
        await await_guarded(_slow_cook(), timeout_s=LIMIT_S, label="bot b1")
    await other
    assert ticks == 5                       # the other "bot" kept ticking
    assert info.value.node_id == "n_slow"
    assert leaked_cooks() == before + 1
    _wait_for_leaks(before)


async def test_await_guarded_uses_the_given_submit_and_returns_the_result():
    calls = []

    async def submit(fn, *args):
        calls.append(fn.__name__)
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    def cook(x):
        assert current_guard() is not None   # run() can see the guard
        return x * 2

    assert await await_guarded(cook, 21, timeout_s=5, submit=submit) == 42
    assert calls == ["cook"]
    assert current_guard() is None


def test_time_waiting_for_a_thread_does_not_count_against_the_limit():
    """W7 fix BS-03 / CR-5: the limit counts from the moment a thread starts
    the cook.  A cook queued behind a busy pool for longer than its limit
    still runs, and is not a timeout (the old guard said it ran too long)."""
    before = leaked_cooks()
    ran = threading.Event()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        blocker = pool.submit(time.sleep, LIMIT_S + 0.3)   # the only worker is busy
        assert call_guarded(ran.set, timeout_s=LIMIT_S, executor=pool) is None
        blocker.result()
    assert ran.is_set()
    assert leaked_cooks() == before


async def test_await_guarded_does_not_count_the_queue_wait():
    """The asyncio path: a cook queued behind a busy pool runs once a thread
    is free, with its whole limit."""
    before = leaked_cooks()
    ran = threading.Event()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        blocker = pool.submit(time.sleep, LIMIT_S + 0.3)

        async def submit(fn, *args):
            return await asyncio.get_running_loop().run_in_executor(pool, fn, *args)

        assert await await_guarded(ran.set, timeout_s=LIMIT_S, submit=submit) is None
        assert ran.is_set()
        ran.clear()
        blocker = pool.submit(time.sleep, LIMIT_S + 0.3)
        assert await await_guarded(ran.set, timeout_s=LIMIT_S, executor=pool) is None
        assert ran.is_set()
        blocker.result()
    finally:
        pool.shutdown(wait=True)
    assert leaked_cooks() == before


async def test_await_guarded_never_reports_a_finished_cook_as_a_timeout():
    """W7 fix CR-6 / BS-07: the thread finished in time, but its result
    reached the waiter late.  The result is returned, not code_timeout."""
    before = leaked_cooks()

    async def late_submit(fn, *args):
        result = await asyncio.get_running_loop().run_in_executor(None, fn, *args)
        await asyncio.sleep(LIMIT_S * 2)    # the cook is done; its result is slow
        return result

    assert await await_guarded(lambda: 42, timeout_s=LIMIT_S, submit=late_submit) == 42
    assert leaked_cooks() == before


async def test_a_timeout_error_raised_by_the_cook_itself_is_not_code_timeout():
    """A TimeoutError that the (non-user) cook raises passes through as it is."""
    def cook():
        raise TimeoutError("the data provider timed out")
    with pytest.raises(TimeoutError) as err:
        await await_guarded(cook, timeout_s=5)
    assert not isinstance(err.value, CodeTimeout)


async def test_bot_cooks_run_on_the_bounded_code_pool_not_the_default_executor():
    """W7 fix BS-02: code cooks run on CODE_EXECUTOR (4 daemon threads named
    sl-code), never on the loop's default executor that broker calls,
    fetches and saves use."""
    from nodebuilder.code.runtime import CODE_EXECUTOR, CODE_POOL_WORKERS

    def where():
        t = threading.current_thread()
        return t.name, t.daemon
    name, daemon = await await_guarded(where, timeout_s=5)
    assert name.startswith("sl-code") and daemon
    assert CODE_POOL_WORKERS == 4 and CODE_EXECUTOR._max_workers == 4


async def test_a_hung_code_cook_does_not_hold_the_default_executor():
    """A leaked code cook keeps a code thread, not a default-executor
    thread: plain executor work (a broker call) still runs at once."""
    before = leaked_cooks()
    with pytest.raises(CodeTimeout):
        await await_guarded(_slow_cook(), timeout_s=LIMIT_S, label="bot b1")
    loop = asyncio.get_running_loop()
    started = time.monotonic()
    assert await loop.run_in_executor(None, lambda: 1) == 1
    assert time.monotonic() - started < 0.2
    _wait_for_leaks(before)


async def test_a_full_code_pool_fails_at_once_with_a_clear_reason():
    """W7 fix BS-02: when every thread of the pool is held by a leaked cook,
    a new cook fails at once with code_timeout, and the reason says the code
    pool is full (not that this cook ran too long)."""
    from nodebuilder.code import pause_reason
    from nodebuilder.code.runtime import _DaemonPool

    before = leaked_cooks()
    pool = _DaemonPool(1, "test-full")
    ran = threading.Event()
    with pytest.raises(CodeTimeout):
        await await_guarded(_slow_cook(), timeout_s=LIMIT_S, executor=pool)
    started = time.monotonic()
    with pytest.raises(CodeTimeout) as err:
        await await_guarded(ran.set, timeout_s=LIMIT_S, executor=pool)
    assert time.monotonic() - started < 0.1        # no wait at all
    assert err.value.code == CODE_TIMEOUT and err.value.pool_full
    assert "code pool is full" in err.value.message
    assert pause_reason(err.value, "signal_code").startswith("code_timeout: the code pool is full")
    _wait_for_leaks(before)
    # The leak ended by itself: the pool takes cooks again.
    assert await await_guarded(lambda: 7, timeout_s=5, executor=pool) == 7
    assert not ran.is_set()
    pool.shutdown()


async def test_a_queued_cook_gives_up_when_the_pool_fills_with_leaks():
    """A cook waiting behind a cook that then leaks gives up as soon as every
    thread is held by a leak, and never starts later."""
    from nodebuilder.code.runtime import _DaemonPool

    before = leaked_cooks()
    pool = _DaemonPool(1, "test-fill")
    ran = threading.Event()
    slow = asyncio.ensure_future(await_guarded(_slow_cook(), timeout_s=LIMIT_S, executor=pool))
    await asyncio.sleep(0.05)
    started = time.monotonic()
    with pytest.raises(CodeTimeout) as err:
        await await_guarded(ran.set, timeout_s=LIMIT_S, executor=pool)
    assert err.value.pool_full
    assert time.monotonic() - started < SLOW_S - 0.1   # did not wait for the leak to end
    with pytest.raises(CodeTimeout):
        await slow
    _wait_for_leaks(before)
    time.sleep(0.05)
    assert not ran.is_set()
    pool.shutdown()


def test_call_guarded_reports_a_full_pool_too():
    from nodebuilder.code.runtime import _DaemonPool

    before = leaked_cooks()
    pool = _DaemonPool(1, "test-full-sync")
    with pytest.raises(CodeTimeout):
        call_guarded(_slow_cook(), timeout_s=LIMIT_S, executor=pool)
    with pytest.raises(CodeTimeout) as err:
        call_guarded(lambda: 1, timeout_s=LIMIT_S, executor=pool)
    assert err.value.pool_full
    _wait_for_leaks(before)
    pool.shutdown()


async def test_cancelling_the_waiter_keeps_a_queued_cook_from_starting():
    from nodebuilder.code.runtime import _DaemonPool

    pool = _DaemonPool(1, "test-cancel")
    ran = threading.Event()
    blocker = pool.submit(time.sleep, 0.3)
    waiter = asyncio.ensure_future(await_guarded(ran.set, timeout_s=5, executor=pool))
    await asyncio.sleep(0.05)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    blocker.result()
    time.sleep(0.05)
    assert not ran.is_set()
    pool.shutdown()


def test_a_timeout_names_the_node_the_user_sees():
    """W7 fix CR-7: run(shown_node=...) is what a timeout names (a node
    inside a locked asset shows as the instance)."""
    before = leaked_cooks()
    prepared = prepare(f"import time\ntime.sleep({SLOW_S})\n@done = 1", "wrangle", "inst::w")
    stream = _stream()

    def cook():
        return run(prepared, stream, node_name="my_asset", shown_node="inst")

    with pytest.raises(CodeTimeout) as err:
        call_guarded(cook, timeout_s=LIMIT_S)
    assert (err.value.node_id, err.value.node_name) == ("inst", "my_asset")
    _wait_for_leaks(before)


def test_an_abandoned_cook_stops_at_its_next_code_node():
    """After the timeout, the leaked thread runs no further code node."""
    before = leaked_cooks()
    fast = prepare("@x = 1", "wrangle", "n_after")
    stream = _stream()
    reached = []

    def cook():
        _slow_cook()()
        try:
            run(fast, stream)
            reached.append("ran")
        except CodeTimeout:
            reached.append("stopped")

    with pytest.raises(CodeTimeout):
        call_guarded(cook, timeout_s=LIMIT_S)
    _wait_for_leaks(before)
    assert reached == ["stopped"]


# ---------------------------------------------------------------------------
# The kill switch and code_capabilities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,enabled", [
    (None, True), ("", True), ("1", True), ("true", True), ("yes", True),
    ("0", False), ("false", False), ("off", False), ("no", False), (" 0 ", False),
])
def test_code_enabled_reads_sl_code_nodes(monkeypatch, value, enabled):
    if value is None:
        monkeypatch.delenv("SL_CODE_NODES", raising=False)
    else:
        monkeypatch.setenv("SL_CODE_NODES", value)
    assert code_enabled() is enabled
    assert capabilities()["enabled"] is enabled


def test_capabilities_shape():
    caps = capabilities()
    assert caps["language"] == "python"
    assert caps["modules"] == ["np", "pd", "math", "sl", "ta"]
    assert isinstance(caps["leaked_cooks"], int)
    names = [f["name"] for f in caps["functions"]]
    assert names == ["sl.sma", "sl.ema", "sl.rsi", "sl.atr", "sl.macd", "sl.bb", "sl.zscore",
                     "sl.crosses_above", "sl.crosses_below", "sl.rising", "sl.falling",
                     "sl.bars_since", "sl.shift", "sl.rolling"]
    rsi = caps["functions"][2]
    assert rsi["signature"] == "sl.rsi(x, period=14, type='wilder')"
    assert rsi["returns"] == "series_float" and rsi["doc"]
    assert all(f["doc"] and "\n" not in f["doc"] for f in caps["functions"])

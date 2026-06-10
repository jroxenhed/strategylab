"""F279 — ProcessPoolExecutor force-kill teardown tests.

Verifies that _force_kill_executor() terminates worker processes that are
stuck (e.g. in a C call) after shutdown() returns, and that shutdown_all_executors()
cleans _LIVE_EXECUTORS. Uses a real ProcessPool with a sleeping worker; no heavy
mocking so the kill path is exercised end-to-end.

Wall-clock cost: ~2-4s (spawn + kill + 1s join timeout).
Marked @pytest.mark.slow — deselect with -m 'not slow'.
"""
import multiprocessing as mp
import sys
import time
from os.path import abspath, dirname

sys.path.insert(0, dirname(dirname(abspath(__file__))))

import pytest
from concurrent.futures import ProcessPoolExecutor


# --- Module-level worker function (must be top-level for spawn pickling) ---

def _sleep_forever():
    """Worker function that sleeps ~30s — simulates a stuck C call."""
    time.sleep(30)
    return "done"


# --- Tests ---

@pytest.mark.slow
class TestForceKillExecutor:
    """F279 — _force_kill_executor correctly kills live workers."""

    def test_kills_stuck_worker(self):
        """Real ProcessPool with a sleeping worker; _force_kill_executor kills it.

        Steps:
        1. Spawn a pool with one worker running _sleep_forever (~30s).
        2. Capture the Process objects from _processes before killing.
        3. Call ex.shutdown(wait=False) then _force_kill_executor(ex).
        4. Assert all captured workers are no longer alive within ~2s.
        """
        from routes.wfa_pool import _force_kill_executor

        ctx = mp.get_context("spawn")
        ex = ProcessPoolExecutor(max_workers=1, mp_context=ctx)

        # Submit the sleeper — this forces the worker process to spawn.
        future = ex.submit(_sleep_forever)

        # Wait briefly for the worker to actually start (subprocess spawn).
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            procs = getattr(ex, "_processes", {})
            if procs and any(p.is_alive() for p in procs.values()):
                break
            time.sleep(0.1)

        procs = getattr(ex, "_processes", {})
        assert procs, "No worker processes found in _processes — spawn failed or pool not started"
        captured = list(procs.values())
        assert any(p.is_alive() for p in captured), "Worker not alive before kill — unexpected"

        # Force-kill BEFORE shutdown() — shutdown() sets _processes=None
        # unconditionally, so _force_kill_executor must run while _processes
        # is still populated. This mirrors the order in the wfa_pool finally block.
        _force_kill_executor(ex)

        # Cooperative shutdown (non-blocking; after kill so _processes still readable above).
        ex.shutdown(wait=False, cancel_futures=True)

        # All workers should be dead within 2s (join(timeout=1) is inside _force_kill_executor).
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not any(p.is_alive() for p in captured):
                break
            time.sleep(0.1)

        still_alive = [p for p in captured if p.is_alive()]
        assert not still_alive, (
            f"Expected 0 live workers after _force_kill_executor; "
            f"{len(still_alive)} still alive: pids={[p.pid for p in still_alive]}"
        )

    def test_no_op_on_missing_processes_attr(self):
        """_force_kill_executor is a no-op when _processes attr is absent.

        Exercises the getattr guard: a dummy object without _processes should
        not raise and should return cleanly.
        """
        from routes.wfa_pool import _force_kill_executor

        class DummyExecutor:
            pass  # no _processes attribute

        # Must not raise.
        _force_kill_executor(DummyExecutor())

    def test_no_op_on_empty_processes(self):
        """_force_kill_executor is a no-op when _processes is an empty dict."""
        from routes.wfa_pool import _force_kill_executor

        class EmptyExecutor:
            _processes = {}

        _force_kill_executor(EmptyExecutor())  # Must not raise.


@pytest.mark.slow
class TestShutdownAllExecutors:
    """F279 — shutdown_all_executors() drains _LIVE_EXECUTORS."""

    def test_drains_registry(self):
        """shutdown_all_executors() discards all entries from _LIVE_EXECUTORS."""
        import routes.wfa_pool as wfa_pool_mod
        from routes.wfa_pool import shutdown_all_executors

        # Inject a dummy executor that has no live processes (no-op kill path).
        class FakeExecutor:
            _processes = {}

        fake = FakeExecutor()
        wfa_pool_mod._LIVE_EXECUTORS.add(fake)

        try:
            shutdown_all_executors()
            assert fake not in wfa_pool_mod._LIVE_EXECUTORS, (
                "FakeExecutor should have been removed from _LIVE_EXECUTORS by shutdown_all_executors()"
            )
        finally:
            # shutdown_all_executors() drains ALL entries (it kills + discards every
            # live executor), so any pre-existing entries are already spent — clear
            # rather than restore them.
            wfa_pool_mod._LIVE_EXECUTORS.clear()

    def test_registry_populated_and_discarded_by_run_windows_parallel(self):
        """run_windows_parallel registers the executor and removes it on teardown.

        Uses _FORCE_SERIAL=False (real pool path) with n < _MIN_WINDOWS_FOR_POOL=4
        — wait, we need n >= 4 to hit the pool. Use n=4 (minimum).

        Checks that _LIVE_EXECUTORS is empty after the call completes.
        """
        import routes.wfa_pool as wfa_pool_mod
        from routes.wfa_pool import run_windows_parallel, _MIN_WINDOWS_FOR_POOL
        from models import StrategyRequest
        from routes.walk_forward import WalkForwardParam
        import numpy as np
        import pandas as pd

        n_windows = _MIN_WINDOWS_FOR_POOL  # 4 — guarantees pool path
        is_bars = 15
        oos_bars = 15
        total_bars = n_windows * (is_bars + oos_bars) + 10  # 130

        dates = pd.date_range("2020-01-01", periods=total_bars, freq="B")
        close = np.linspace(100.0, 120.0, total_bars)
        spread = 0.5
        df = pd.DataFrame(
            {
                "Open": close - spread,
                "High": close + spread,
                "Low": close - spread,
                "Close": close,
                "Volume": np.full(total_bars, 10_000, dtype=float),
            },
            index=dates,
        )

        windows = []
        step = is_bars + oos_bars
        for i in range(n_windows):
            is_s = i * step
            is_e = is_s + is_bars - 1
            oos_s = is_e + 1
            oos_e = oos_s + oos_bars - 1
            windows.append((is_s, is_e, oos_s, oos_e))

        base = StrategyRequest(
            ticker="AAPL",
            start=dates[0].strftime("%Y-%m-%d"),
            end=dates[-1].strftime("%Y-%m-%d"),
            interval="1d",
            buy_rules=[{"indicator": "rsi", "condition": "below", "value": 20}],
            sell_rules=[{"indicator": "rsi", "condition": "above", "value": 80}],
            min_per_order=0.0,
            per_share_rate=0.0,
        )
        params = [WalkForwardParam(path="stop_loss_pct", values=[3.0, 5.0])]

        # Snapshot before
        before = set(wfa_pool_mod._LIVE_EXECUTORS)

        run_windows_parallel(
            full_df=df,
            windows=windows,
            base=base,
            params=params,
            interval="1d",
            metric="sharpe_ratio",
            min_trades_is=0,
            timeout_secs=60.0,
        )

        # After completion the executor must have been discarded.
        after = set(wfa_pool_mod._LIVE_EXECUTORS)
        # Any executors added during the call should be gone now.
        new_entries = after - before
        assert not new_entries, (
            f"_LIVE_EXECUTORS still contains {len(new_entries)} executor(s) after "
            f"run_windows_parallel returned — teardown did not discard them."
        )

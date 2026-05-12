"""F176 — Parallel-path progress test for routes/wfa_pool.run_windows_parallel.

This file is intentionally separate from test_walk_forward.py. That file has
an autouse fixture that sets _FORCE_SERIAL=True to make monkeypatching work
for its mocked run_backtest calls. Tests here need the real ProcessPool path
(_FORCE_SERIAL=False, the default), so they must not share that fixture.

Wall-clock cost: ~2-5s on macOS (spawn context overhead ~100ms/worker +
4 trivial backtests). Marked @pytest.mark.slow — deselect with -m 'not slow'.
"""
import sys
from os.path import dirname, abspath

sys.path.insert(0, dirname(dirname(abspath(__file__))))

import pytest
import numpy as np
import pandas as pd

from models import StrategyRequest
from routes.wfa_pool import run_windows_parallel, _MIN_WINDOWS_FOR_POOL
from routes.walk_forward import WalkForwardParam


def _make_synthetic_df(n: int = 120, start: str = "2020-01-01") -> pd.DataFrame:
    """Build a simple synthetic daily OHLCV DataFrame sufficient for a real backtest.

    Prices follow a gentle linear uptrend so the strategy can form valid
    candlesticks without any NaN. RSI-based rules applied in the backtest
    won't trade (never cross RSI<20/RSI>80 on smooth prices) but they
    complete cleanly without raising.
    """
    dates = pd.date_range(start, periods=n, freq="B")  # business days
    close = np.linspace(100.0, 120.0, n)               # gentle uptrend
    spread = 0.5
    return pd.DataFrame(
        {
            "Open": close - spread,
            "High": close + spread,
            "Low": close - spread,
            "Close": close,
            "Volume": np.full(n, 10_000, dtype=float),
        },
        index=dates,
    )


def _make_base_request(start: str, end: str) -> StrategyRequest:
    """Build a minimal StrategyRequest with a trivial rule set."""
    return StrategyRequest(
        ticker="AAPL",
        start=start,
        end=end,
        interval="1d",
        buy_rules=[{"indicator": "rsi", "condition": "below", "value": 20}],
        sell_rules=[{"indicator": "rsi", "condition": "above", "value": 80}],
        min_per_order=0.0,
        per_share_rate=0.0,
    )


def _make_wfa_param():
    """One param with two values — small combo space for fast IS grids."""
    return WalkForwardParam(path="stop_loss_pct", values=[3.0, 5.0])


def _build_windows(n_windows: int, is_bars: int, oos_bars: int) -> list[tuple[int, int, int, int]]:  # (is_start, is_end, oos_start, oos_end)
    """Construct n_windows sequential non-overlapping (is_s, is_e, oos_s, oos_e) tuples."""
    windows = []
    step = is_bars + oos_bars
    for i in range(n_windows):
        is_s = i * step
        is_e = is_s + is_bars - 1
        oos_s = is_e + 1
        oos_e = oos_s + oos_bars - 1
        windows.append((is_s, is_e, oos_s, oos_e))
    return windows


@pytest.mark.slow
class TestRunWindowsParallel:
    """F176 — exercises the real ProcessPool dispatch path in run_windows_parallel.

    _FORCE_SERIAL is NOT set here (stays False by default). The tests only
    run when n >= _MIN_WINDOWS_FOR_POOL (=4) so the pool is actually used.
    ProcessPool workers import routes.backtest.run_backtest directly; we pass
    a synthetic df so the real backtester completes quickly without network I/O.
    """

    def test_progress_callback_fires_per_window(self):
        """F176 core: progress_callback is called once per completed window.

        Assertions:
        - len(calls) == n_windows
        - completed values form the set {1, 2, ..., n_windows}
        - final call is (n_windows, n_windows)
        - run_windows_parallel returns (results dict, timed_out=False)
        - len(results) == n_windows

        Wall-clock: ~2-5s (spawn 4 workers + 4 trivial backtests).
        """
        import routes.wfa_pool as wfa_pool_mod
        assert not wfa_pool_mod._FORCE_SERIAL, (
            "_FORCE_SERIAL is True — parallel path won't be tested. "
            "Check for fixture leakage from another test module."
        )

        n_windows = _MIN_WINDOWS_FOR_POOL  # exactly 4 — guarantees parallel path
        is_bars = 20
        oos_bars = 20
        total_bars_needed = n_windows * (is_bars + oos_bars)  # = 160

        df = _make_synthetic_df(n=total_bars_needed + 10, start="2020-01-01")
        windows = _build_windows(n_windows, is_bars, oos_bars)

        # Boundary dates for the base request must span the full df range.
        start_str = df.index[0].strftime("%Y-%m-%d")
        end_str = df.index[-1].strftime("%Y-%m-%d")
        base = _make_base_request(start_str, end_str)

        params = [_make_wfa_param()]
        calls: list[tuple[int, int]] = []

        results, timed_out = run_windows_parallel(
            full_df=df,
            windows=windows,
            base=base,
            params=params,
            interval="1d",
            metric="sharpe_ratio",
            min_trades_is=0,    # accept zero-trade IS results — trivial strategy won't trade
            timeout_secs=60.0,
            progress_callback=lambda c, t: calls.append((c, t)),
        )

        # Not timed out
        assert timed_out is False, f"Expected timed_out=False but got True; results={results.keys()}"

        # All windows completed
        assert len(results) == n_windows, (
            f"Expected {n_windows} results, got {len(results)}: {list(results.keys())}"
        )

        # Progress callback fired once per window
        assert len(calls) == n_windows, (
            f"Expected {n_windows} progress calls, got {len(calls)}: {calls}"
        )

        # completed values cover {1, 2, ..., n_windows} — order may vary (as_completed)
        completed_values = {c for c, _t in calls}
        assert completed_values == set(range(1, n_windows + 1)), (
            f"Expected completed values {set(range(1, n_windows + 1))}, got {completed_values}"
        )

        # final call carries (n_windows, n_windows) — last window reported
        # Note: with as_completed, the last call in `calls` is the last to arrive.
        # Sort by completed count and check the maximum.
        max_call = max(calls, key=lambda x: x[0])
        assert max_call == (n_windows, n_windows), (
            f"Expected final progress call ({n_windows}, {n_windows}), got max_call={max_call}"
        )

        # total is always n_windows in every call
        assert all(t == n_windows for _c, t in calls), (
            f"All calls should have total={n_windows}, got: {calls}"
        )

    def test_broken_pool_raises_http_500(self, monkeypatch):
        """F172(b): corrupted df pickle causes BrokenProcessPool → HTTPException(500).

        Strategy: monkeypatch routes.wfa_pool.pickle.dumps so that when it
        serialises a pd.DataFrame it returns invalid bytes. The pool initializer
        (_init_worker) receives those bytes in the subprocess and raises
        UnpicklingError, which marks the pool as broken. The parallel path
        catches BrokenProcessPool and re-raises as HTTPException(status_code=500).

        Regression guard: the pre-fix behaviour was a silent empty-result return.
        """
        import pickle as real_pickle
        import routes.wfa_pool as wfa_pool_mod

        assert not wfa_pool_mod._FORCE_SERIAL, (
            "_FORCE_SERIAL is True — parallel path won't be tested."
        )

        original_dumps = real_pickle.dumps

        def patched_dumps(obj, *args, **kwargs):
            # Corrupt only the DataFrame serialisation (initargs payload).
            if isinstance(obj, pd.DataFrame):
                return b'\x00\x01\x02\x03'  # invalid pickle — loads() will raise
            return original_dumps(obj, *args, **kwargs)

        monkeypatch.setattr(wfa_pool_mod.pickle, "dumps", patched_dumps)

        n_windows = _MIN_WINDOWS_FOR_POOL
        is_bars, oos_bars = 20, 20
        total_bars = n_windows * (is_bars + oos_bars)
        df = _make_synthetic_df(n=total_bars + 10)
        windows = _build_windows(n_windows, is_bars, oos_bars)
        start_str = df.index[0].strftime("%Y-%m-%d")
        end_str = df.index[-1].strftime("%Y-%m-%d")
        base = _make_base_request(start_str, end_str)
        params = [_make_wfa_param()]

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            run_windows_parallel(
                full_df=df,
                windows=windows,
                base=base,
                params=params,
                interval="1d",
                metric="sharpe_ratio",
                min_trades_is=0,
                timeout_secs=30.0,
            )

        assert exc_info.value.status_code == 500, (
            f"Expected HTTPException(500), got status_code={exc_info.value.status_code}"
        )
        assert "crashed" in exc_info.value.detail.lower() or "worker" in exc_info.value.detail.lower(), (
            f"Unexpected detail: {exc_info.value.detail}"
        )

    def test_overall_timeout_returns_partial_results_and_timed_out_true(self):
        """F172(c): sub-ms timeout causes FuturesTimeout → timed_out=True, len(results) < n_windows.

        Strategy: set timeout_secs=0.001 (1 ms). Spawn overhead alone (~100 ms)
        means no worker can complete before the deadline. The parallel path detects
        remaining <= 0 after submitting futures (or FuturesTimeout fires immediately)
        and returns (results, True) where results is empty or partial.

        Assertions:
        - timed_out is True
        - len(results) < n_windows   (reliable: no worker finishes in 1 ms)
        - len(results) >= 0          (invariant: no negative result count)
        """
        import routes.wfa_pool as wfa_pool_mod

        assert not wfa_pool_mod._FORCE_SERIAL, (
            "_FORCE_SERIAL is True — parallel path won't be tested."
        )

        n_windows = _MIN_WINDOWS_FOR_POOL
        is_bars, oos_bars = 20, 20
        total_bars = n_windows * (is_bars + oos_bars)
        df = _make_synthetic_df(n=total_bars + 10)
        windows = _build_windows(n_windows, is_bars, oos_bars)
        start_str = df.index[0].strftime("%Y-%m-%d")
        end_str = df.index[-1].strftime("%Y-%m-%d")
        base = _make_base_request(start_str, end_str)
        params = [_make_wfa_param()]

        results, timed_out = run_windows_parallel(
            full_df=df,
            windows=windows,
            base=base,
            params=params,
            interval="1d",
            metric="sharpe_ratio",
            min_trades_is=0,
            timeout_secs=0.001,  # 1 ms — no worker completes before deadline
        )

        assert timed_out is True, (
            "Expected timed_out=True for sub-ms timeout, got False"
        )
        assert len(results) < n_windows, (
            f"Expected fewer than {n_windows} results with 1 ms timeout, got {len(results)}"
        )
        assert len(results) >= 0  # invariant

    def test_biased_partial_drop_equivalence_serial_vs_serial(self, monkeypatch):
        """F172(d): biased_partial windows are dropped equivalently in serial and parallel paths.

        Regression: F166 C-01 — the serial path added the biased_partial drop
        signal but the parallel path did not. This test verifies the DROP CONTRACT
        is identical: a WindowOutput with biased_partial=True is included in the
        returned dict (so the caller can detect it) and the set of biased windows
        matches between paths.

        Subprocess isolation means we cannot inject synthetic behavior into the
        parallel workers. Instead, we test the DROP CONTRACT via two serial runs:
        one using _run_windows_serial directly, and one using run_windows_parallel
        with _FORCE_SERIAL=True (monkeypatched). Both use the same input that
        triggers real IS-grid timeouts (large combo space, tiny IS budget).

        The biased_partial trigger: use 2 params × 10 values = 100 IS combos per
        window, with a 500-bar IS df so each combo takes ~5 ms. timeout_secs=0.12
        (120 ms) aborts after ~24 combos — never 0, never all 100 — on any machine.

        Note: both calls here exercise the serial code path (run_windows_parallel
        delegates to _run_windows_serial when _FORCE_SERIAL=True). The parallel
        test for this contract is validated by test_progress_callback_fires_per_window
        (which confirms the parallel path returns results) + the code review noting
        that _process_window_in_worker contains identical biased_partial logic.
        """
        from routes.wfa_pool import _run_windows_serial, WindowOutput
        import routes.wfa_pool as wfa_pool_mod

        # Large combo space: 2 params × 10 values = 100 IS combos per window.
        # Large IS df (500 bars) makes each combo take ~5 ms → 100 combos ≈ 500 ms.
        # timeout_secs=0.12 aborts after ~24 combos (not 0, not 100) on any machine.
        large_params = [
            WalkForwardParam(path="stop_loss_pct", values=[float(v) for v in range(1, 11)]),
            WalkForwardParam(path="buy_rule_0_value", values=[float(v) for v in range(15, 25)]),
        ]
        n_windows = _MIN_WINDOWS_FOR_POOL  # 4 windows
        is_bars, oos_bars = 500, 100
        total_bars = n_windows * (is_bars + oos_bars)  # 4 * 600 = 2400 bars
        df = _make_synthetic_df(n=total_bars + 10, start="2000-01-01")
        windows = _build_windows(n_windows, is_bars, oos_bars)
        start_str = df.index[0].strftime("%Y-%m-%d")
        end_str = df.index[-1].strftime("%Y-%m-%d")
        base = _make_base_request(start_str, end_str)

        common_kwargs = dict(
            full_df=df,
            windows=windows,
            base=base,
            params=large_params,
            interval="1d",
            metric="sharpe_ratio",
            min_trades_is=0,
            timeout_secs=0.12,  # 120 ms — IS grid aborts mid-way through 100-combo space
        )

        # Run 1: _run_windows_serial directly (baseline).
        results_serial, _timed_out_serial = _run_windows_serial(**common_kwargs)

        # Run 2: run_windows_parallel with _FORCE_SERIAL=True (monkeypatched).
        monkeypatch.setattr(wfa_pool_mod, "_FORCE_SERIAL", True)
        results_via_parallel, _timed_out_parallel = run_windows_parallel(**common_kwargs)
        monkeypatch.setattr(wfa_pool_mod, "_FORCE_SERIAL", False)

        # Both runs must have produced at least one biased_partial window to be
        # a meaningful test — otherwise the premise failed (IS grid finished too fast).
        biased_serial = {
            idx for idx, w in results_serial.items()
            if isinstance(w, WindowOutput) and w.biased_partial
        }
        biased_via_parallel = {
            idx for idx, w in results_via_parallel.items()
            if isinstance(w, WindowOutput) and w.biased_partial
        }

        assert len(biased_serial) > 0, (
            "Test premise failed: no biased_partial windows in serial run. "
            "IS grid finished all 100 combos in 120 ms — increase combo count or "
            "decrease timeout_secs."
        )
        assert len(biased_via_parallel) > 0, (
            "Test premise failed: no biased_partial windows in via-parallel run. "
            "IS grid finished all 100 combos in 120 ms."
        )

        # The DROP CONTRACT: biased window indices match between both runs.
        assert biased_serial == biased_via_parallel, (
            f"biased_partial window index mismatch:\n"
            f"  serial:       {sorted(biased_serial)}\n"
            f"  via-parallel: {sorted(biased_via_parallel)}\n"
            "The two paths dropped different windows — parity broken."
        )

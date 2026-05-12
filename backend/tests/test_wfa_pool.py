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

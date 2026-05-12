"""
routes/wfa_pool.py — per-request ProcessPool that parallelizes WFA windows.

Each worker owns one complete window (IS grid + OOS backtest). The pool
initializer ships the full df via pickle bytes once at startup; workers
slice the df in-process per window — no re-fetch, no cross-process df pickling
per call.

Design rationale: F163 prototyped per-IS-grid parallelism and lost to
serial-with-cache because 100ms/worker spawn + cold _fetch + cache rebuild
dominated wall-clock when each grid was ~250ms of work. F166 moves the
parallelism boundary to the WINDOW level, where each worker has ~5-10s
of work — spawn cost amortizes cleanly.

Public contract:
  run_windows_parallel(full_df, windows, base, params, interval, metric,
                       min_trades_is, timeout_secs, max_workers=None)
    -> tuple[dict[int, WindowOutput], bool]
  Returns (window_outputs_by_index, timed_out).
  window_outputs_by_index: dict mapping window_index -> WindowOutput, only
    for windows that completed. Missing indices = dropped (timeout or worker
    error). Caller MUST iterate windows in order, skipping missing indices.

Test seam:
  _FORCE_SERIAL = False  # tests set True to bypass the pool and run inline.
"""
import logging
import multiprocessing as mp
import os
import pickle
from concurrent.futures import ProcessPoolExecutor, wait, ALL_COMPLETED
from concurrent.futures.process import BrokenProcessPool
from time import monotonic
from typing import NamedTuple, Optional

import numpy as np
import pandas as pd
from fastapi import HTTPException

from models import StrategyRequest

logger = logging.getLogger(__name__)

# Test seam: when True, run_windows_parallel skips the pool and uses an
# inline serial loop. Required for monkeypatch-based tests that need to
# intercept run_backtest (subprocesses don't see those patches).
_FORCE_SERIAL = False

# Skip ProcessPool overhead for tiny WFAs. Observed spawn cost on macOS
# spawn context is ~100ms per worker; for 8 workers that's ~800ms of
# fixed dispatch overhead. Below 4 windows the serial-with-cache path
# (run_windows_serial) is competitive or faster. Tune downward if a
# smaller workload becomes common.
_MIN_WINDOWS_FOR_POOL = 4

# Worker process module-globals. Populated once per process by _init_worker.
_WORKER_DF: Optional[pd.DataFrame] = None


class WindowOutput(NamedTuple):
    """Raw per-window output produced by a worker. The route post-processes
    these into WindowResult + stitched_equity in index order."""
    window_index: int
    is_start_date: str
    is_end_date: str
    oos_start_date: str
    oos_end_date: str
    best_combo: dict        # winning IS params (empty dict if no_is_trades)
    is_summary: dict        # full summary from run_backtest()["summary"]
    oos_summary: dict       # ditto for OOS
    oos_equity_curve: list  # raw OOS equity points (pre-rescale)
    stability_tag: str      # one of StabilityTag literals
    is_combo_count: int     # how many IS combos evaluated
    is_timed_out: bool      # whether IS grid timed out mid-window
    is_combos_total: int    # total expected IS combos (for biased-partial-drop check)
    low_trades_is: bool     # whether IS winner had < min_trades_is trades
    skipped_for_no_is_trades: bool  # IS grid returned zero successful combos
    biased_partial: bool = False  # IS timed out mid-grid (winner from deterministic prefix → drop)


def _init_worker(df_pickled: bytes) -> None:
    """Pool initializer — runs once per worker process. Deserializes the
    full df into a module global so each window's worker call slices it
    in-process instead of paying pickling cost per call."""
    global _WORKER_DF
    _WORKER_DF = pickle.loads(df_pickled)


def _process_window_in_worker(args: tuple) -> WindowOutput:
    """Worker entrypoint. args is a fully-serialized window descriptor.
    Reads df from module global _WORKER_DF, runs IS grid + OOS, returns
    a WindowOutput."""
    # Import inside worker to avoid loading these in the main pickling path
    from routes.grid_runner import run_grid
    from routes.backtest import run_backtest
    from routes.walk_forward import (
        _format_boundary, _combo_key, _get_neighbor_keys,
        _STABILITY_THRESHOLD,
    )
    from routes.backtest_sweep import _apply_param
    from fastapi import HTTPException

    (
        window_index, is_s, is_e, oos_s, oos_e,
        base_pickled, params_pickled,
        interval, metric, min_trades_is,
        remaining_budget,
        total_combos_per_window,
    ) = args

    base = pickle.loads(base_pickled)
    params = pickle.loads(params_pickled)

    df = _WORKER_DF
    if df is None:
        raise RuntimeError(
            "Worker _WORKER_DF not initialized — initializer didn't run"
        )

    is_df = df.iloc[is_s : is_e + 1]
    oos_df = df.iloc[oos_s : oos_e + 1]

    is_start_date = _format_boundary(df.index[is_s], interval)
    is_end_date = _format_boundary(df.index[is_e], interval)
    oos_start_date = _format_boundary(df.index[oos_s], interval)
    oos_end_date = _format_boundary(df.index[oos_e], interval)

    is_req_template = base.model_copy(update={"start": is_start_date, "end": is_end_date})

    is_combos, is_timed_out, _is_skipped = run_grid(
        is_req_template,
        params,
        timeout_secs=remaining_budget,
        df=is_df,
    )

    # Biased-partial-drop: IS timed out with a subset of combos evaluated.
    # The winner was picked from a deterministic prefix → biased. Drop via
    # structured signal so the caller can set timed_out=True and skip this window.
    if is_timed_out and 0 < len(is_combos) < total_combos_per_window:
        return WindowOutput(
            window_index=window_index,
            is_start_date=is_start_date,
            is_end_date=is_end_date,
            oos_start_date=oos_start_date,
            oos_end_date=oos_end_date,
            best_combo={},
            is_summary={},
            oos_summary={"num_trades": 0},
            oos_equity_curve=[],
            stability_tag="no_is_trades",
            is_combo_count=len(is_combos),
            is_timed_out=True,
            is_combos_total=total_combos_per_window,
            low_trades_is=False,
            skipped_for_no_is_trades=False,
            biased_partial=True,
        )

    if len(is_combos) == 0:
        # IS grid produced zero successful backtests — degenerate window.
        return WindowOutput(
            window_index=window_index,
            is_start_date=is_start_date,
            is_end_date=is_end_date,
            oos_start_date=oos_start_date,
            oos_end_date=oos_end_date,
            best_combo={},
            is_summary={},
            oos_summary={"num_trades": 0},
            oos_equity_curve=[],
            stability_tag="no_is_trades",
            is_combo_count=0,
            is_timed_out=is_timed_out,
            is_combos_total=total_combos_per_window,
            low_trades_is=False,
            skipped_for_no_is_trades=True,
        )

    # Pick IS winner
    is_combos.sort(key=lambda x: x[1].get(metric, 0), reverse=True)
    best_combo, best_is_summary = is_combos[0]

    # Low-trades IS check
    if best_is_summary.get("num_trades", 0) < min_trades_is:
        low_trades_is = True
        stability_tag = "low_trades_is"
    else:
        low_trades_is = False
        # Neighborhood stability tag
        all_sharpes = [s.get("sharpe_ratio", 0.0) for _, s in is_combos]
        q75 = float(np.percentile(all_sharpes, 75))
        neighbor_keys = _get_neighbor_keys(best_combo, params)
        neighbor_results = [
            s for c, s in is_combos if _combo_key(c) in neighbor_keys
        ]
        if len(neighbor_results) == 0:
            stability_tag = "spike"
        else:
            top_q_neighbors = sum(
                1 for s in neighbor_results if s.get("sharpe_ratio", 0.0) >= q75
            )
            stability_tag = (
                "stable_plateau"
                if top_q_neighbors / len(neighbor_results) >= _STABILITY_THRESHOLD
                else "spike"
            )

    # OOS evaluation
    oos_req = base.model_copy(update={"start": oos_start_date, "end": oos_end_date})
    for path, value in best_combo.items():
        oos_req = _apply_param(oos_req, path, value)

    try:
        oos_result = run_backtest(oos_req, df=oos_df, include_spy_correlation=False)
    except HTTPException as exc:
        if exc.status_code >= 500:
            raise
        logger.warning("WFA window %d OOS skipped (4xx): %s", window_index, exc.detail)
        oos_result = {"summary": {"num_trades": 0}, "equity_curve": []}
    except Exception as exc:
        logger.warning("WFA window %d OOS raised: %s", window_index, exc)
        oos_result = {"summary": {"num_trades": 0}, "equity_curve": []}

    # Only overwrite to "no_oos_trades" if IS-side check didn't already flag low_trades_is
    if oos_result["summary"].get("num_trades", 0) == 0 and stability_tag != "low_trades_is":
        stability_tag = "no_oos_trades"

    return WindowOutput(
        window_index=window_index,
        is_start_date=is_start_date,
        is_end_date=is_end_date,
        oos_start_date=oos_start_date,
        oos_end_date=oos_end_date,
        best_combo=best_combo,
        is_summary=best_is_summary,
        oos_summary=oos_result["summary"],
        oos_equity_curve=oos_result.get("equity_curve", []),
        stability_tag=stability_tag,
        is_combo_count=len(is_combos),
        is_timed_out=is_timed_out,
        is_combos_total=total_combos_per_window,
        low_trades_is=low_trades_is,
        skipped_for_no_is_trades=False,
    )


def run_windows_parallel(
    full_df: pd.DataFrame,
    windows: list,
    base: StrategyRequest,
    params: list,
    interval: str,
    metric: str,
    min_trades_is: int,
    timeout_secs: float,
    max_workers: Optional[int] = None,
    _run_backtest_fn=None,
) -> tuple:
    """Dispatch one worker per window. Returns (results_by_idx, timed_out).

    See module docstring for the full contract.

    results_by_idx is a dict mapping window_index (int) -> WindowOutput.
    Missing indices = dropped (timeout or worker error).
    timed_out is True if the overall budget was exhausted.

    _run_backtest_fn: optional callable for OOS backtests. Only used in the
    serial path (_FORCE_SERIAL=True or n < _MIN_WINDOWS_FOR_POOL). Parallel
    workers import routes.backtest.run_backtest directly and cannot be
    monkeypatched from the test process.
    """
    n = len(windows)

    if _FORCE_SERIAL or n < _MIN_WINDOWS_FOR_POOL:
        return _run_windows_serial(
            full_df, windows, base, params, interval, metric,
            min_trades_is, timeout_secs,
            _run_backtest_fn=_run_backtest_fn,
        )

    # Budget already exhausted (caller's clock returned 9999 in tests, or
    # genuine pre-dispatch timeout).
    if timeout_secs <= 0:
        return {}, True

    if max_workers is None:
        max_workers = min(os.cpu_count() or 4, n)

    # Pre-compute total combos once (same value for every window)
    total_combos_per_window = 1
    for p in params:
        total_combos_per_window *= len(p.values)

    df_pickled = pickle.dumps(full_df)
    base_pickled = pickle.dumps(base)
    params_pickled = pickle.dumps(params)

    ctx = mp.get_context("spawn")
    results: dict = {}
    timed_out = False

    deadline = monotonic() + timeout_secs

    ex = ProcessPoolExecutor(
        max_workers=max_workers,
        mp_context=ctx,
        initializer=_init_worker,
        initargs=(df_pickled,),
    )
    try:
        future_to_idx = {}
        for w_idx, (is_s, is_e, oos_s, oos_e) in enumerate(windows):
            # Shrinking deadline budget: workers submitted last see a smaller
            # timeout, matching the parallel-dispatch reality where the wall
            # clock has already advanced since the first submission.
            worker_budget = max(0.0, deadline - monotonic())
            args = (
                w_idx, is_s, is_e, oos_s, oos_e,
                base_pickled, params_pickled,
                interval, metric, min_trades_is,
                worker_budget,
                total_combos_per_window,
            )
            future_to_idx[ex.submit(_process_window_in_worker, args)] = w_idx

        remaining = deadline - monotonic()
        if remaining <= 0:
            timed_out = True
            for f in future_to_idx:
                f.cancel()
            return results, timed_out

        done, not_done = wait(
            list(future_to_idx.keys()),
            timeout=max(0.0, remaining),
            return_when=ALL_COMPLETED,
        )

        if not_done:
            timed_out = True
            for f in not_done:
                f.cancel()

        for f in done:
            try:
                out = f.result()
                results[out.window_index] = out
            except HTTPException as exc:
                if exc.status_code >= 500:
                    raise  # 5xx must surface — system is broken
                w_idx = future_to_idx[f]
                logger.warning("WFA window %d worker 4xx: %s", w_idx, exc.detail)
            except BrokenProcessPool as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"WFA worker pool crashed: {exc}",
                )
            except Exception as exc:
                w_idx = future_to_idx[f]
                logger.warning("WFA window %d worker raised: %s", w_idx, exc)
                # Drop the window; main process skips this index.

        return results, timed_out
    finally:
        # Non-blocking shutdown — cancels pending futures, lets running ones
        # complete in the background. The main process returns immediately.
        # Note: spawned worker subprocesses may continue running until their
        # internal timeout fires; the per-worker deadline (Fix 4) bounds this.
        ex.shutdown(wait=False, cancel_futures=True)


def _run_windows_serial(
    full_df: pd.DataFrame,
    windows: list,
    base: StrategyRequest,
    params: list,
    interval: str,
    metric: str,
    min_trades_is: int,
    timeout_secs: float,
    _run_backtest_fn=None,
) -> tuple:
    """Serial path for n < _MIN_WINDOWS_FOR_POOL or _FORCE_SERIAL=True.

    In-process equivalent of the parallel workers — so monkeypatches work.
    Produces the IDENTICAL WindowOutput shape as the parallel path.

    Note: this serial path uses its own time budget (timeout_secs from call time).
    The outer walk_forward route also checks monotonic() per window; the serial
    path here is a faithful port of the original loop body, so behavior is
    unchanged from the pre-F166 serial implementation.

    _run_backtest_fn: optional callable to use for OOS run_backtest calls.
    walk_forward.py passes its own module-level `run_backtest` reference so
    tests that monkeypatch `wf_mod.run_backtest` continue to work unchanged.
    Defaults to `routes.backtest.run_backtest` if not provided.
    """
    # Import at call time (not module level) so this path mirrors the worker
    # import pattern and tests can monkeypatch these at the routes.* level.
    from routes.grid_runner import run_grid
    from routes.backtest import run_backtest as _default_run_backtest
    from routes.walk_forward import (
        _format_boundary, _combo_key, _get_neighbor_keys,
        _STABILITY_THRESHOLD,
    )
    from routes.backtest_sweep import _apply_param
    from fastapi import HTTPException

    # Resolve the OOS run_backtest callable. Callers (e.g. walk_forward.py) may
    # pass their own module-level reference so that test monkeypatches on
    # `routes.walk_forward.run_backtest` are honoured here in the serial path.
    _oos_run_backtest = _run_backtest_fn if _run_backtest_fn is not None else _default_run_backtest

    total_combos_per_window = 1
    for p in params:
        total_combos_per_window *= len(p.values)

    results: dict = {}
    timed_out = False

    # If the caller's budget is already exhausted (e.g. test mocks returning 9999),
    # immediately report timed_out without running any windows.
    if timeout_secs <= 0:
        return results, True

    start = monotonic()

    for w_idx, (is_s, is_e, oos_s, oos_e) in enumerate(windows):
        if monotonic() - start > timeout_secs:
            timed_out = True
            break

        is_start_date = _format_boundary(full_df.index[is_s], interval)
        is_end_date = _format_boundary(full_df.index[is_e], interval)
        oos_start_date = _format_boundary(full_df.index[oos_s], interval)
        oos_end_date = _format_boundary(full_df.index[oos_e], interval)

        remaining_budget = timeout_secs - (monotonic() - start)
        if remaining_budget <= 0:
            timed_out = True
            break

        is_req_template = base.model_copy(
            update={"start": is_start_date, "end": is_end_date}
        )
        is_df = full_df.iloc[is_s : is_e + 1]
        oos_df = full_df.iloc[oos_s : oos_e + 1]

        is_combos, is_timed_out, _is_skipped = run_grid(
            is_req_template,
            params,
            timeout_secs=remaining_budget,
            df=is_df,
        )

        if is_timed_out:
            timed_out = True
            if 0 < len(is_combos) < total_combos_per_window:
                # Biased partial IS grid — winner from deterministic prefix → drop window.
                # Use structured signal (biased_partial=True) and continue so the caller
                # can detect it. Parity with parallel path which can't break mid-dispatch.
                results[w_idx] = WindowOutput(
                    window_index=w_idx,
                    is_start_date=is_start_date,
                    is_end_date=is_end_date,
                    oos_start_date=oos_start_date,
                    oos_end_date=oos_end_date,
                    best_combo={},
                    is_summary={},
                    oos_summary={"num_trades": 0},
                    oos_equity_curve=[],
                    stability_tag="no_is_trades",
                    is_combo_count=len(is_combos),
                    is_timed_out=True,
                    is_combos_total=total_combos_per_window,
                    low_trades_is=False,
                    skipped_for_no_is_trades=False,
                    biased_partial=True,
                )
                continue

        if len(is_combos) == 0:
            results[w_idx] = WindowOutput(
                window_index=w_idx,
                is_start_date=is_start_date,
                is_end_date=is_end_date,
                oos_start_date=oos_start_date,
                oos_end_date=oos_end_date,
                best_combo={},
                is_summary={},
                oos_summary={"num_trades": 0},
                oos_equity_curve=[],
                stability_tag="no_is_trades",
                is_combo_count=0,
                is_timed_out=is_timed_out,
                is_combos_total=total_combos_per_window,
                low_trades_is=False,
                skipped_for_no_is_trades=True,
            )
            continue

        # Pick IS winner
        is_combos.sort(key=lambda x: x[1].get(metric, 0), reverse=True)
        best_combo, best_is_summary = is_combos[0]

        # Low-trades IS check
        if best_is_summary.get("num_trades", 0) < min_trades_is:
            low_trades_is = True
            stability_tag = "low_trades_is"
        else:
            low_trades_is = False
            all_sharpes = [s.get("sharpe_ratio", 0.0) for _, s in is_combos]
            q75 = float(np.percentile(all_sharpes, 75))
            neighbor_keys = _get_neighbor_keys(best_combo, params)
            neighbor_results = [
                s for c, s in is_combos if _combo_key(c) in neighbor_keys
            ]
            if len(neighbor_results) == 0:
                stability_tag = "spike"
            else:
                top_q_neighbors = sum(
                    1 for s in neighbor_results
                    if s.get("sharpe_ratio", 0.0) >= q75
                )
                stability_tag = (
                    "stable_plateau"
                    if top_q_neighbors / len(neighbor_results) >= _STABILITY_THRESHOLD
                    else "spike"
                )

        # OOS evaluation
        oos_req = base.model_copy(
            update={"start": oos_start_date, "end": oos_end_date}
        )
        for path, value in best_combo.items():
            oos_req = _apply_param(oos_req, path, value)

        try:
            oos_result = _oos_run_backtest(
                oos_req, df=oos_df, include_spy_correlation=False
            )
        except HTTPException as exc:
            if exc.status_code >= 500:
                raise
            logger.warning(
                "WFA window %d OOS skipped (4xx): %s", w_idx, exc.detail
            )
            oos_result = {"summary": {"num_trades": 0}, "equity_curve": []}
        except Exception as exc:
            logger.warning("WFA window %d OOS raised: %s", w_idx, exc)
            oos_result = {"summary": {"num_trades": 0}, "equity_curve": []}

        if (
            oos_result["summary"].get("num_trades", 0) == 0
            and stability_tag != "low_trades_is"
        ):
            stability_tag = "no_oos_trades"

        results[w_idx] = WindowOutput(
            window_index=w_idx,
            is_start_date=is_start_date,
            is_end_date=is_end_date,
            oos_start_date=oos_start_date,
            oos_end_date=oos_end_date,
            best_combo=best_combo,
            is_summary=best_is_summary,
            oos_summary=oos_result["summary"],
            oos_equity_curve=oos_result.get("equity_curve", []),
            stability_tag=stability_tag,
            is_combo_count=len(is_combos),
            is_timed_out=is_timed_out,
            is_combos_total=total_combos_per_window,
            low_trades_is=low_trades_is,
            skipped_for_no_is_trades=False,
        )

    return results, timed_out


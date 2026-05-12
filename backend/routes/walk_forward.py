"""
routes/walk_forward.py — Walk-Forward Analysis for backtests.

POST /api/backtest/walk_forward — Partition history into rolling (or anchored) IS windows
where parameters are selected by a chosen metric, then evaluate each IS winner on the
adjacent held-out OOS window and roll forward. Returns per-window IS vs OOS metrics,
a rescaled OOS equity curve that is never in the optimizer's view, Walk-Forward Efficiency
(WFE), and parameter stability CV across windows.

POST /api/backtest/walk_forward/stream — SSE variant that streams per-window progress
events while the WFA runs, then emits a final result event with the full response.

WFE = mean(per-window OOS Sharpe) / mean(per-window IS-winner Sharpe). Mean-of-Sharpes form.

Reuses _apply_param from backtest_sweep.py and constants from backtest_optimizer.py.

Window computation modes
------------------------
Rolling (expand_train=False):
  IS is a fixed-width window of is_bars that slides forward each step.

Anchored / expanding (expand_train=True):
  IS is anchored at the start of the dataset and grows each step as OOS slides forward.
  is_bars is the *minimum* IS size — it determines where the first OOS window starts.
  Subsequent windows have IS that spans [0, oos_start - 1 - gap_bars], i.e. the IS
  window grows monotonically with each step.
"""

import asyncio
import json
import logging
import math
import threading
from statistics import mean, stdev
from time import monotonic
from typing import Literal, NamedTuple, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from models import StrategyRequest
from routes.backtest import run_backtest
from routes.backtest_optimizer import _MAX_PARAMS, _VALID_METRICS
from routes.backtest_sweep import _apply_param
from routes.grid_runner import run_grid
from routes.wfa_pool import run_windows_parallel
from shared import _INTERVAL_MAX_DAYS, _INTRADAY_INTERVALS, _fetch

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants (per plan)
# F174: WORKER-SAFE — all plain numeric/Literal constants below; no I/O.
# ---------------------------------------------------------------------------
_WFA_TIMEOUT_SECS = 600  # raised from 120s after F162/F166 backend speedup + F175 stream
                          # progress (user can see runs in flight and abort if needed).
_STABILITY_THRESHOLD = 0.60
_MAX_VALUES_PER_PARAM = 10  # mirror backtest_optimizer.py
# WFA per-IS-window combo cap. Distinct from the optimizer's _MAX_COMBOS=200 — WFA's
# indicator cache + per-window parallelism amortize cost so larger grids are reasonable.
# Hard ceiling protects against accidental abuse; the timeout above is the real safety net.
_MAX_COMBOS_PER_WINDOW = 1000

StabilityTag = Literal[
    "stable_plateau", "spike", "low_trades_is", "no_oos_trades", "no_is_trades"
]

router = APIRouter()


# ---------------------------------------------------------------------------
# Named return type for _setup_walk_forward
# ---------------------------------------------------------------------------

class WalkForwardSetup(NamedTuple):
    """Return value of _setup_walk_forward — prevents positional-index drift."""
    wfa_start: float
    full_df: "pd.DataFrame"
    windows: list
    base: "StrategyRequest"
    total_combos_per_window: int
    low_windows_warn: bool


# ---------------------------------------------------------------------------
# Pydantic models (verbatim field names from plan)
# ---------------------------------------------------------------------------

class WalkForwardParam(BaseModel):
    path: str
    # is_bars acts as the *minimum* IS window size in anchored mode; fixed size in rolling.
    values: list[float] = Field(..., min_length=1, max_length=_MAX_VALUES_PER_PARAM)

    @field_validator("values")
    @classmethod
    def _finite_values(cls, v: list[float]) -> list[float]:
        if not all(math.isfinite(x) for x in v):
            raise ValueError("values must be finite (no NaN or Infinity)")
        return v


class WalkForwardRequest(BaseModel):
    base: StrategyRequest
    params: list[WalkForwardParam] = Field(..., min_length=1, max_length=_MAX_PARAMS)
    # Rolling: is_bars is the fixed IS window size.
    # Anchored (expand_train=True): is_bars is the *minimum* IS size; it determines where
    # the first OOS window starts. Subsequent windows' IS grows to fill [0, oos_start-1-gap_bars].
    is_bars: int = Field(..., gt=0)
    oos_bars: int = Field(..., gt=0)
    gap_bars: int = Field(0, ge=0)
    step_bars: int = Field(0, ge=0)
    expand_train: bool = False       # False = rolling, True = anchored
    metric: str = "sharpe_ratio"
    min_trades_is: int = Field(30, ge=0)
    # No disable_regime field — regime is always stripped in v1 (locked decision 13).
    # No interval field — base.interval is read directly; intraday is rejected at the
    # route boundary (locked decision 14).


class BacktestSummary(BaseModel):
    """Typed mirror of run_backtest()["summary"]. Fields from WFA-relevant subset;
    extra keys from edge_stats / spy_corr are preserved via model_config extra='allow'.

    All numeric fields default to 0.0 / None so that no_is_trades and no_oos_trades
    stub dicts (e.g. {"num_trades": 0}) validate without error.
    """

    model_config = {"extra": "allow"}

    num_trades: int = 0
    sharpe_ratio: Optional[float] = None    # None when num_trades == 0
    total_return_pct: float = 0.0
    win_rate_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    final_value: float = 0.0
    initial_capital: Optional[float] = None
    buy_hold_return_pct: Optional[float] = None
    # edge_stats fields (optional — absent in no_is_trades / no_oos_trades stubs)
    gross_profit: Optional[float] = None
    gross_loss: Optional[float] = None
    ev_per_trade: Optional[float] = None
    profit_factor: Optional[float] = None
    # spy correlation fields
    beta: Optional[float] = None
    r_squared: Optional[float] = None


class WfaEquityPoint(BaseModel):
    """One point in the stitched OOS equity curve."""
    time: str | int
    value: float


class WindowResult(BaseModel):
    window_index: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    best_params: dict[str, float]
    is_sharpe: float
    is_metrics: BacktestSummary        # full summary dict from run_backtest()["summary"]
    oos_metrics: BacktestSummary
    stability_tag: StabilityTag
    is_combo_count: int            # how many IS combos were evaluated (0 when no_is_trades)
    scale_factor: float            # rescaling multiplier applied to this window's OOS curve (1.0 for no_is_trades)
    # Note: per-window equity curves are stitched server-side into WalkForwardResponse.stitched_equity.
    # Not exposed per-window in the response to keep payload small.


class WalkForwardResponse(BaseModel):
    windows: list[WindowResult]
    stitched_equity: list[WfaEquityPoint]  # typed equity points
    wfe: Optional[float]              # None when no OOS trades at all
    param_cv: dict[str, float]        # {param_path: CV} — std/mean of best_params per window
    total_combos: int                 # sum of IS combos across all windows
    total_oos_trades: int
    low_trades_is_count: int          # windows where IS trades < min_trades_is
    low_windows_warn: bool            # True when 2 ≤ len(windows) < 6 (results are statistically thin)
    timed_out: bool


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _combo_key(combo: dict) -> frozenset:
    """Order-independent dict key for O(1) lookup."""
    return frozenset(combo.items())


def _get_neighbor_keys(best_combo: dict, params: list[WalkForwardParam]) -> set:
    """
    For a best_combo {path: value} and the param grid, enumerate combos that differ
    in exactly one dimension by exactly one step (preceding or following value in p.values).
    Return a set of frozenset-style keys.
    """
    neighbor_keys = set()
    for i, p in enumerate(params):
        if p.path not in best_combo:
            continue
        cur_val = best_combo[p.path]
        try:
            cur_idx = p.values.index(cur_val)
        except ValueError:
            # value not found in grid (shouldn't happen, but guard)
            continue
        for delta in (-1, +1):
            neighbor_idx = cur_idx + delta
            if 0 <= neighbor_idx < len(p.values):
                neighbor_combo = dict(best_combo)
                neighbor_combo[p.path] = p.values[neighbor_idx]
                neighbor_keys.add(_combo_key(neighbor_combo))
    return neighbor_keys


def _deduplicate_by_time(points: list[dict]) -> list[dict]:
    """
    Remove duplicate timestamps in the stitched curve (can occur with overlapping step_bars).
    Keep the last occurrence for each time value (later window takes precedence at seam).
    Preserves order.
    """
    seen: dict = {}
    for pt in points:
        seen[pt["time"]] = pt
    # Reconstruct in original order (dict preserves insertion order; last write wins)
    return list(seen.values())


def _format_boundary(ts, interval: str) -> str:
    """
    Format a window boundary timestamp for round-tripping into run_backtest's
    start/end. Intraday intervals need datetime precision so adjacent IS-end and
    OOS-start bars on the same calendar day don't collide into the same string
    (which would cause data leakage via provider re-fetch of the full day).
    """
    if interval in _INTRADAY_INTERVALS:
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return ts.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Shared setup helper
# ---------------------------------------------------------------------------

def _setup_walk_forward(
    req: WalkForwardRequest,
) -> WalkForwardSetup:
    """Validate, fetch, and compute windows. Returns a WalkForwardSetup NamedTuple:
      (wfa_start, full_df, windows, base, total_combos_per_window, low_windows_warn)

    Raises HTTPException on validation or data errors.
    Called from both the sync and streaming endpoints before any parallel work.
    """
    _wfa_start = monotonic()

    # ------------------------------------------------------------------
    # VALIDATE
    # ------------------------------------------------------------------
    if req.base.initial_capital <= 0:
        raise HTTPException(
            status_code=400, detail="base.initial_capital must be > 0"
        )
    if req.metric not in _VALID_METRICS:
        raise HTTPException(
            status_code=400,
            detail=f"metric must be one of: {sorted(_VALID_METRICS)}",
        )

    step = req.step_bars if req.step_bars > 0 else req.oos_bars
    if step < req.oos_bars:
        raise HTTPException(
            status_code=400,
            detail=(
                f"step_bars ({req.step_bars}) < oos_bars ({req.oos_bars}) produces "
                "overlapping OOS windows; not supported in v1. Use step_bars >= oos_bars."
            ),
        )

    total_combos_per_window = 1
    for p in req.params:
        total_combos_per_window *= len(p.values)
    if total_combos_per_window > _MAX_COMBOS_PER_WINDOW:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Too many combinations ({total_combos_per_window}). "
                f"Max {_MAX_COMBOS_PER_WINDOW} per IS window. Reduce values or param count."
            ),
        )

    # ------------------------------------------------------------------
    # FETCH BARS
    # ------------------------------------------------------------------
    base = req.base.model_copy(deep=True, update={"regime": None})
    df = _fetch(base.ticker, base.start, base.end, base.interval, source=base.source)
    total_bars = len(df)
    min_required = req.is_bars + req.oos_bars + req.gap_bars
    if total_bars < min_required:
        detail = (
            f"Not enough bars for one window: dataset has {total_bars} bars "
            f"but is_bars + oos_bars + gap_bars = {min_required}."
        )
        max_days = _INTERVAL_MAX_DAYS.get(base.interval)
        if max_days is not None:
            detail += (
                f" Provider limits {base.interval} to {max_days} days of history; "
                "shorten is_bars/oos_bars or use a longer interval."
            )
        raise HTTPException(status_code=400, detail=detail)

    # ------------------------------------------------------------------
    # COMPUTE WINDOWS
    # ------------------------------------------------------------------
    windows: list[tuple[int, int, int, int]] = []
    oos_end_idx = req.is_bars + req.gap_bars + req.oos_bars - 1
    while oos_end_idx < total_bars:
        if req.expand_train:
            oos_start_idx = oos_end_idx - req.oos_bars + 1
            is_end_idx = oos_start_idx - 1 - req.gap_bars
            is_start_idx = 0
        else:
            is_start_idx = oos_end_idx - req.oos_bars - req.gap_bars - req.is_bars + 1
            is_end_idx = is_start_idx + req.is_bars - 1
            oos_start_idx = is_end_idx + 1 + req.gap_bars
        oos_end_idx_actual = oos_start_idx + req.oos_bars - 1
        if oos_end_idx_actual >= total_bars:
            break
        if is_end_idx < is_start_idx or is_end_idx < 0:
            oos_end_idx += step
            continue
        windows.append((is_start_idx, is_end_idx, oos_start_idx, oos_end_idx_actual))
        oos_end_idx += step

    if len(windows) < 2:
        raise HTTPException(
            status_code=400,
            detail=(
                "Need at least 2 windows for walk-forward analysis "
                "(WFE undefined below this). Increase date range or reduce is_bars/oos_bars."
            ),
        )

    low_windows_warn = len(windows) < 6

    return WalkForwardSetup(
        wfa_start=_wfa_start,
        full_df=df,
        windows=windows,
        base=base,
        total_combos_per_window=total_combos_per_window,
        low_windows_warn=low_windows_warn,
    )


# ---------------------------------------------------------------------------
# Shared response assembly helper
# ---------------------------------------------------------------------------

def _assemble_walk_forward_response(
    *,
    window_outputs: dict,
    windows: list,
    base: StrategyRequest,
    req: WalkForwardRequest,
    full_df: pd.DataFrame,
    timed_out_parallel: bool,
    low_windows_warn: bool,
    total_combos_per_window: int,
) -> WalkForwardResponse:
    """Post-process raw WindowOutput dict into the final WalkForwardResponse.

    Called by both the sync and streaming endpoints after parallel/serial
    dispatch completes. Behavior is identical for both callers.
    """
    results: list[WindowResult] = []
    window_stitched: list[list[dict]] = []
    prev_final_equity = base.initial_capital
    total_oos_trades = 0
    low_trades_is_count = 0
    timed_out = timed_out_parallel

    for w_idx, _ in enumerate(windows):
        if w_idx not in window_outputs:
            continue

        out = window_outputs[w_idx]

        if out.biased_partial:
            timed_out = True
            continue

        if out.skipped_for_no_is_trades:
            def _make_empty_summary() -> BacktestSummary:
                return BacktestSummary(
                    num_trades=0,
                    sharpe_ratio=None,
                    total_return_pct=0.0,
                    win_rate_pct=0.0,
                    max_drawdown_pct=0.0,
                    final_value=0.0,
                )
            results.append(WindowResult(
                window_index=w_idx,
                is_start=out.is_start_date,
                is_end=out.is_end_date,
                oos_start=out.oos_start_date,
                oos_end=out.oos_end_date,
                best_params={},
                is_sharpe=0.0,
                is_metrics=_make_empty_summary(),
                oos_metrics=_make_empty_summary(),
                stability_tag="no_is_trades",
                is_combo_count=0,
                scale_factor=1.0,
            ))
            window_stitched.append([])
            continue

        if out.low_trades_is:
            low_trades_is_count += 1

        raw_curve = out.oos_equity_curve
        scale_factor = prev_final_equity / base.initial_capital
        rescaled_curve = [
            {"time": pt["time"], "value": pt["value"] * scale_factor}
            for pt in raw_curve
        ]
        if rescaled_curve:
            prev_final_equity = rescaled_curve[-1]["value"]
        window_stitched.append(rescaled_curve)

        total_oos_trades += out.oos_summary.get("num_trades", 0)
        results.append(WindowResult(
            window_index=w_idx,
            is_start=out.is_start_date,
            is_end=out.is_end_date,
            oos_start=out.oos_start_date,
            oos_end=out.oos_end_date,
            best_params=out.best_combo,
            is_sharpe=round(out.is_summary.get("sharpe_ratio", 0.0), 3),
            is_metrics=BacktestSummary(**out.is_summary),
            oos_metrics=BacktestSummary(**out.oos_summary),
            stability_tag=out.stability_tag,
            is_combo_count=out.is_combo_count,
            scale_factor=scale_factor,
        ))

    # ------------------------------------------------------------------
    # AGGREGATE
    # ------------------------------------------------------------------
    stitched_dicts = [pt for curve in window_stitched for pt in curve]
    stitched_dicts = _deduplicate_by_time(stitched_dicts)
    stitched = [WfaEquityPoint(time=pt["time"], value=pt["value"]) for pt in stitched_dicts]

    contributing = [
        w for w in results
        if w.stability_tag != "no_is_trades" and w.oos_metrics.num_trades > 0
    ]
    oos_sharpes = [w.oos_metrics.sharpe_ratio or 0.0 for w in contributing]
    is_sharpes = [w.is_sharpe for w in contributing]
    if oos_sharpes and is_sharpes and mean(is_sharpes) != 0:
        wfe: Optional[float] = round(mean(oos_sharpes) / mean(is_sharpes), 3)
    else:
        wfe = None

    contributing_for_cv = [w for w in results if w.stability_tag != "no_is_trades"]
    param_cv: dict[str, float] = {}
    for p in req.params:
        vals = [w.best_params.get(p.path, 0.0) for w in contributing_for_cv]
        if len(vals) >= 2 and mean(vals) != 0:
            param_cv[p.path] = round(stdev(vals) / mean(vals), 3)
        else:
            param_cv[p.path] = 0.0

    return WalkForwardResponse(
        windows=results,
        stitched_equity=stitched,
        wfe=wfe,
        param_cv=param_cv,
        total_combos=sum(w.is_combo_count for w in results),
        total_oos_trades=total_oos_trades,
        low_trades_is_count=low_trades_is_count,
        low_windows_warn=low_windows_warn,
        timed_out=timed_out,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/backtest/walk_forward")
def run_walk_forward(req: WalkForwardRequest) -> WalkForwardResponse:
    """Synchronous WFA endpoint. Unchanged from pre-F175 behavior."""
    setup = _setup_walk_forward(req)

    # Subtract elapsed time (fetch + window construction) from the budget.
    # wfa_start was captured at the very top of _setup_walk_forward so the
    # budget is accurate. Tests that monkeypatch wf_mod.monotonic to return 9999
    # on the second call yield a large-negative remaining_budget, which
    # _run_windows_serial treats as already-expired → timed_out=True.
    remaining_budget = max(0.0, _WFA_TIMEOUT_SECS - (monotonic() - setup.wfa_start))

    window_outputs, timed_out_parallel = run_windows_parallel(
        full_df=setup.full_df,
        windows=setup.windows,
        base=setup.base,
        params=req.params,
        interval=setup.base.interval,
        metric=req.metric,
        min_trades_is=req.min_trades_is,
        timeout_secs=remaining_budget,
        # Pass this module's run_backtest reference so monkeypatches on
        # routes.walk_forward.run_backtest are honoured in the serial path
        # (tests that set wf_mod.run_backtest).
        _run_backtest_fn=run_backtest,
    )

    return _assemble_walk_forward_response(
        window_outputs=window_outputs,
        windows=setup.windows,
        base=setup.base,
        req=req,
        full_df=setup.full_df,
        timed_out_parallel=timed_out_parallel,
        low_windows_warn=setup.low_windows_warn,
        total_combos_per_window=setup.total_combos_per_window,
    )


@router.post("/api/backtest/walk_forward/stream")
async def run_walk_forward_stream(req: WalkForwardRequest) -> StreamingResponse:
    """SSE variant of POST /api/backtest/walk_forward.

    Streams `started` + per-window `progress` events while the WFA runs,
    then a final `result` event carrying the full WalkForwardResponse body.

    The synchronous setup phase (validation, _fetch, window construction)
    runs in a thread via asyncio.to_thread so the event loop is never blocked
    by the I/O-bound _fetch call. HTTPException raised during setup surfaces
    as a proper HTTP 4xx/5xx before the StreamingResponse opens.
    """
    # Run synchronous setup off the event loop so _fetch I/O doesn't block
    # the main thread. HTTPException propagates up here → FastAPI returns 4xx.
    setup = await asyncio.to_thread(_setup_walk_forward, req)

    async def event_stream():
        # REL-001: cancel_event lets the async generator signal the worker thread
        # that the client has disconnected. The thread checks it in the progress
        # callback and stops enqueuing; the WFA work itself runs to completion
        # (we can't forcibly kill the thread), but at least we stop feeding a dead queue.
        cancel_event = threading.Event()
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def make_progress_callback():
            def cb(completed: int, total: int) -> None:
                # Check cancel before enqueuing — client may have disconnected.
                if cancel_event.is_set():
                    return
                # progress callback runs on a worker thread; asyncio.Queue methods
                # are not thread-safe. call_soon_threadsafe is the supported
                # asyncio bridge for scheduling queue.put_nowait from a non-async thread.
                try:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        # REL-003/KP-02: use json.dumps for all SSE payloads (no hand-rolled f-strings).
                        "data: " + json.dumps(
                            {"type": "progress", "completed": completed, "total": total},
                            allow_nan=False,
                        ) + "\n\n",
                    )
                except RuntimeError:
                    # REL-002: loop may be closed if client disconnected mid-run.
                    pass
            return cb

        def run_in_thread() -> None:
            try:
                remaining_budget = max(0.0, _WFA_TIMEOUT_SECS - (monotonic() - setup.wfa_start))
                window_outputs, timed_out_parallel = run_windows_parallel(
                    full_df=setup.full_df,
                    windows=setup.windows,
                    base=setup.base,
                    params=req.params,
                    interval=setup.base.interval,
                    metric=req.metric,
                    min_trades_is=req.min_trades_is,
                    timeout_secs=remaining_budget,
                    # Pass this module's run_backtest reference so monkeypatches on
                    # routes.walk_forward.run_backtest are honoured in the serial path.
                    _run_backtest_fn=run_backtest,
                    progress_callback=make_progress_callback(),
                )
                response = _assemble_walk_forward_response(
                    window_outputs=window_outputs,
                    windows=setup.windows,
                    base=setup.base,
                    req=req,
                    full_df=setup.full_df,
                    timed_out_parallel=timed_out_parallel,
                    low_windows_warn=setup.low_windows_warn,
                    total_combos_per_window=setup.total_combos_per_window,
                )
                # REL-003/KP-02: json.dumps with allow_nan=False — WFA metrics
                # can contain NaN (e.g. Sharpe on zero-variance returns). Catch
                # serialisation failure and emit an error event instead of breaking
                # the stream with invalid JSON.
                try:
                    payload = json.dumps(
                        {"type": "result", **response.model_dump()},
                        allow_nan=False,
                    )
                except (ValueError, OverflowError) as json_err:
                    err_payload = json.dumps(
                        {"type": "error", "detail": f"Result serialisation failed: {json_err}", "status": 500},
                        allow_nan=False,
                    )
                    try:
                        loop.call_soon_threadsafe(queue.put_nowait, f"data: {err_payload}\n\n")
                    except RuntimeError:
                        pass
                    return
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, f"data: {payload}\n\n")
                except RuntimeError:
                    # REL-002: loop closed (client disconnected).
                    pass
            except HTTPException as exc:
                err_payload = json.dumps(
                    {"type": "error", "detail": str(exc.detail), "status": exc.status_code},
                    allow_nan=False,
                )
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, f"data: {err_payload}\n\n")
                except RuntimeError:
                    pass
            except Exception as exc:
                err_payload = json.dumps(
                    {"type": "error", "detail": str(exc), "status": 500},
                    allow_nan=False,
                )
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, f"data: {err_payload}\n\n")
                except RuntimeError:
                    pass
            finally:
                # REL-002: sentinel push — wrap in try/except in case the event
                # loop is already closed (client disconnected / request cancelled).
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, None)
                except RuntimeError:
                    pass  # loop closed — generator already exited via cancel_event

        try:
            # Emit the initial "started" event before kicking off work.
            # REL-003/KP-02: use json.dumps for all SSE payloads.
            yield "data: " + json.dumps(
                {"type": "started", "total": len(setup.windows)},
                allow_nan=False,
            ) + "\n\n"

            # Launch the work on a daemon thread and stream queue events until sentinel.
            threading.Thread(target=run_in_thread, daemon=True).start()

            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            # REL-001: signal the worker thread that the client is gone.
            # The progress callback checks cancel_event before enqueuing,
            # avoiding put_nowait on a generator that's no longer consuming.
            cancel_event.set()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx: disable response buffering
            "Connection": "keep-alive",
        },
    )

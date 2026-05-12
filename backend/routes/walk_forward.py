"""
routes/walk_forward.py — Walk-Forward Analysis for backtests.

POST /api/backtest/walk_forward — Partition history into rolling (or anchored) IS windows
where parameters are selected by a chosen metric, then evaluate each IS winner on the
adjacent held-out OOS window and roll forward. Returns per-window IS vs OOS metrics,
a rescaled OOS equity curve that is never in the optimizer's view, Walk-Forward Efficiency
(WFE), and parameter stability CV across windows.

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

import logging
import math
from statistics import mean, stdev
from time import monotonic
from typing import Literal, Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from models import StrategyRequest
from routes.backtest import run_backtest
from routes.backtest_optimizer import _MAX_COMBOS, _MAX_PARAMS, _VALID_METRICS
from routes.backtest_sweep import _apply_param
from routes.grid_runner import run_grid
from shared import _INTERVAL_MAX_DAYS, _INTRADAY_INTERVALS, _fetch

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants (per plan)
# ---------------------------------------------------------------------------
_WFA_TIMEOUT_SECS = 120
_STABILITY_THRESHOLD = 0.60
_MAX_VALUES_PER_PARAM = 10  # mirror backtest_optimizer.py

StabilityTag = Literal[
    "stable_plateau", "spike", "low_trades_is", "no_oos_trades", "no_is_trades"
]

router = APIRouter()


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


class WindowResult(BaseModel):
    window_index: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    best_params: dict[str, float]
    is_sharpe: float
    is_metrics: dict               # full summary dict from run_backtest()["summary"]
    oos_metrics: dict
    stability_tag: StabilityTag
    is_combo_count: int            # how many IS combos were evaluated (0 when no_is_trades)
    scale_factor: float            # rescaling multiplier applied to this window's OOS curve (1.0 for no_is_trades)
    # Note: per-window equity curves are stitched server-side into WalkForwardResponse.stitched_equity.
    # Not exposed per-window in the response to keep payload small.


class WalkForwardResponse(BaseModel):
    windows: list[WindowResult]
    stitched_equity: list[dict]       # [{"time": str|int, "value": float}]
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
    seen = {}
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
# Route
# ---------------------------------------------------------------------------

@router.post("/api/backtest/walk_forward")
def run_walk_forward(req: WalkForwardRequest) -> WalkForwardResponse:
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
    # (param count, is_bars/oos_bars positivity, gap_bars non-negativity, values cap,
    # NaN/Inf rejection are all enforced by Pydantic Field constraints above.)

    step = req.step_bars if req.step_bars > 0 else req.oos_bars
    if step < req.oos_bars:
        # Overlapping OOS produces stitched-equity sawtooths and biased WFE; not supported in v1.
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
    if total_combos_per_window > _MAX_COMBOS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Too many combinations ({total_combos_per_window}). "
                f"Max {_MAX_COMBOS}. Reduce values or param count."
            ),
        )

    # ------------------------------------------------------------------
    # FETCH BARS
    # Strip regime unconditionally (locked decision 13). Deep copy so we
    # don't mutate the caller's request object.
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
            # Anchored/expanding: IS is fixed at data start and grows each step.
            # oos_start is derived from the cursor; IS fills everything before it.
            oos_start_idx = oos_end_idx - req.oos_bars + 1
            is_end_idx = oos_start_idx - 1 - req.gap_bars
            is_start_idx = 0
        else:
            # Rolling: fixed-width IS slides forward
            is_start_idx = oos_end_idx - req.oos_bars - req.gap_bars - req.is_bars + 1
            is_end_idx = is_start_idx + req.is_bars - 1
            oos_start_idx = is_end_idx + 1 + req.gap_bars
        oos_end_idx_actual = oos_start_idx + req.oos_bars - 1
        if oos_end_idx_actual >= total_bars:
            break
        if is_end_idx < is_start_idx or is_end_idx < 0:
            # Degenerate anchored window before first IS bar reached — skip cursor forward
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

    low_windows_warn = len(windows) < 6  # inline literal per plan

    # ------------------------------------------------------------------
    # WALK-FORWARD LOOP
    # ------------------------------------------------------------------
    results: list[WindowResult] = []
    window_stitched: list[list[dict]] = []  # per-window rescaled curves; flattened in AGGREGATE
    prev_final_equity = base.initial_capital
    total_oos_trades = 0
    low_trades_is_count = 0
    timed_out = False
    start_time = monotonic()

    for w_idx, (is_s, is_e, oos_s, oos_e) in enumerate(windows):
        if monotonic() - start_time > _WFA_TIMEOUT_SECS:
            timed_out = True
            break

        is_start_date = _format_boundary(df.index[is_s], base.interval)
        is_end_date = _format_boundary(df.index[is_e], base.interval)
        oos_start_date = _format_boundary(df.index[oos_s], base.interval)
        oos_end_date = _format_boundary(df.index[oos_e], base.interval)
        # -- IS grid search (via shared run_grid; serial with cross-combo indicator cache) --
        remaining_budget = _WFA_TIMEOUT_SECS - (monotonic() - start_time)
        if remaining_budget <= 0:
            timed_out = True
            break

        is_req_template = base.model_copy(update={"start": is_start_date, "end": is_end_date})
        is_df = df.iloc[is_s : is_e + 1]
        is_combos, is_timed_out, _is_skipped = run_grid(
            is_req_template,
            req.params,
            timeout_secs=remaining_budget,
            df=is_df,
        )

        if is_timed_out:
            timed_out = True
            if len(is_combos) < total_combos_per_window:
                # Partial IS grid → biased winner → drop the window entirely
                break

        if len(is_combos) == 0:
            # IS grid search produced zero successful backtests — append degenerate window row
            results.append(WindowResult(
                window_index=w_idx,
                is_start=is_start_date,
                is_end=is_end_date,
                oos_start=oos_start_date,
                oos_end=oos_end_date,
                best_params={},
                is_sharpe=0.0,
                is_metrics={},
                oos_metrics={"num_trades": 0},
                stability_tag="no_is_trades",
                is_combo_count=0,
                scale_factor=1.0,
            ))
            window_stitched.append([])  # no contribution to stitched equity
            continue

        # -- Pick IS winner --
        is_combos.sort(key=lambda x: x[1].get(req.metric, 0), reverse=True)
        best_combo, best_is_summary = is_combos[0]

        # -- Low-trades IS check --
        if best_is_summary.get("num_trades", 0) < req.min_trades_is:
            low_trades_is_count += 1
            stability_tag = "low_trades_is"
        else:
            # -- Neighborhood stability tag --
            # Build lookup of combo_key → summary for all IS results
            all_sharpes = [s.get("sharpe_ratio", 0.0) for _, s in is_combos]
            q75 = float(np.percentile(all_sharpes, 75))  # inline literal: 75th percentile
            neighbor_keys = _get_neighbor_keys(best_combo, req.params)
            neighbor_results = [
                s for c, s in is_combos if _combo_key(c) in neighbor_keys
            ]
            if len(neighbor_results) == 0:
                stability_tag = "spike"  # no neighbors available (edge of 1-value grid)
            else:
                top_q_neighbors = sum(
                    1 for s in neighbor_results if s.get("sharpe_ratio", 0.0) >= q75
                )
                stability_tag = (
                    "stable_plateau"
                    if top_q_neighbors / len(neighbor_results) >= _STABILITY_THRESHOLD
                    else "spike"
                )

        # -- OOS evaluation --
        oos_req = base.model_copy(update={"start": oos_start_date, "end": oos_end_date})
        for path, value in best_combo.items():
            oos_req = _apply_param(oos_req, path, value)
        oos_df = df.iloc[oos_s : oos_e + 1]
        try:
            oos_result = run_backtest(oos_req, df=oos_df, include_spy_correlation=False)
        except HTTPException as exc:
            if exc.status_code >= 500:
                raise
            logger.warning("WFA window %d OOS skipped (4xx): %s", w_idx, exc.detail)
            oos_result = {"summary": {"num_trades": 0}, "equity_curve": []}
        except Exception as exc:
            logger.warning("WFA window %d OOS raised: %s", w_idx, exc)
            oos_result = {"summary": {"num_trades": 0}, "equity_curve": []}

        # Only overwrite to "no_oos_trades" if IS-side check didn't already flag low_trades_is —
        # low_trades_is is more actionable (optimizer had too little IS signal to trust);
        # no_oos_trades is often a downstream symptom of that.
        if oos_result["summary"].get("num_trades", 0) == 0 and stability_tag != "low_trades_is":
            stability_tag = "no_oos_trades"

        # -- Equity rescaling --
        raw_curve = oos_result.get("equity_curve", [])
        scale_factor = prev_final_equity / base.initial_capital
        rescaled_curve = [
            {"time": pt["time"], "value": pt["value"] * scale_factor}
            for pt in raw_curve
        ]
        if rescaled_curve:
            prev_final_equity = rescaled_curve[-1]["value"]
        window_stitched.append(rescaled_curve)

        total_oos_trades += oos_result["summary"].get("num_trades", 0)
        results.append(WindowResult(
            window_index=w_idx,
            is_start=is_start_date,
            is_end=is_end_date,
            oos_start=oos_start_date,
            oos_end=oos_end_date,
            best_params=best_combo,
            is_sharpe=round(best_is_summary.get("sharpe_ratio", 0.0), 3),
            is_metrics=best_is_summary,
            oos_metrics=oos_result["summary"],
            stability_tag=stability_tag,
            is_combo_count=len(is_combos),
            scale_factor=scale_factor,
        ))

    # ------------------------------------------------------------------
    # AGGREGATE
    # ------------------------------------------------------------------
    stitched = [pt for curve in window_stitched for pt in curve]
    stitched = _deduplicate_by_time(stitched)

    # WFE = mean(per-window OOS Sharpe) / mean(per-window IS-winner Sharpe).
    # Excludes "no_is_trades" windows (IS Sharpe is 0 by construction, would dilute denominator).
    contributing = [
        w for w in results
        if w.stability_tag != "no_is_trades" and w.oos_metrics.get("num_trades", 0) > 0
    ]
    oos_sharpes = [w.oos_metrics.get("sharpe_ratio", 0.0) for w in contributing]
    is_sharpes = [w.is_sharpe for w in contributing]
    if oos_sharpes and is_sharpes and mean(is_sharpes) != 0:
        wfe: Optional[float] = round(mean(oos_sharpes) / mean(is_sharpes), 3)
    else:
        wfe = None

    # Param CV per dimension — exclude only no_is_trades windows (empty best_params).
    # no_oos_trades and low_trades_is DO contribute: IS-winner params are real.
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

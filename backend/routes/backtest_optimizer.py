"""
routes/backtest_optimizer.py — Multi-parameter grid-search optimizer for backtests.

POST /api/backtest/optimize — Run all combinations of up to 3 parameters (max 200
total backtests), return top-N results ranked by a chosen metric. Answers "what
combination of RSI period, stop loss, and MA period maximizes Sharpe?"

Reuses _apply_param() from backtest_sweep.py for parameter mutation logic.
"""

import time
from itertools import product
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models import StrategyRequest
from routes.backtest import run_backtest
from routes.backtest_sweep import _apply_param

_TIMEOUT_SECS = 60

router = APIRouter()

_VALID_METRICS = {"sharpe_ratio", "total_return_pct", "win_rate_pct"}
_MAX_COMBOS = 200
_MAX_VALUES_PER_PARAM = 10
_MAX_PARAMS = 3


class OptimizeParam(BaseModel):
    path: str
    values: list[float]  # 1–10 values to try for this param


class OptimizeRequest(BaseModel):
    base: StrategyRequest
    params: list[OptimizeParam]  # 1–3 params
    metric: str = "sharpe_ratio"
    top_n: int = 10


class OptimizerCombo(BaseModel):
    param_values: dict[str, float]
    num_trades: int
    total_return_pct: float
    sharpe_ratio: float
    win_rate_pct: float
    max_drawdown_pct: float
    ev_per_trade: Optional[float] = None


class OptimizeResponse(BaseModel):
    results: list[OptimizerCombo]
    total_combos: int
    completed: int
    skipped: int
    timed_out: bool = False


@router.post("/api/backtest/optimize")
def optimize_backtest(req: OptimizeRequest) -> OptimizeResponse:
    if not req.params:
        raise HTTPException(status_code=400, detail="At least one param required")
    if len(req.params) > _MAX_PARAMS:
        raise HTTPException(status_code=400, detail=f"Max {_MAX_PARAMS} params")
    if req.metric not in _VALID_METRICS:
        raise HTTPException(status_code=400, detail=f"metric must be one of: {sorted(_VALID_METRICS)}")
    if req.top_n < 1 or req.top_n > 50:
        raise HTTPException(status_code=400, detail="top_n must be between 1 and 50")

    for p in req.params:
        if len(p.values) == 0:
            raise HTTPException(status_code=400, detail=f"values for '{p.path}' must not be empty")
        if len(p.values) > _MAX_VALUES_PER_PARAM:
            raise HTTPException(status_code=400, detail=f"Max {_MAX_VALUES_PER_PARAM} values per param")

    total_combos = 1
    for p in req.params:
        total_combos *= len(p.values)
    if total_combos > _MAX_COMBOS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many combinations ({total_combos}). Max {_MAX_COMBOS}. Reduce values or param count.",
        )

    results: list[OptimizerCombo] = []
    skipped = 0
    start = time.monotonic()
    indicator_cache: dict[tuple, object] = {}

    for combo in product(*[p.values for p in req.params]):
        param_values = {p.path: v for p, v in zip(req.params, combo)}
        try:
            modified = req.base
            for path, value in param_values.items():
                modified = _apply_param(modified, path, value)
        except HTTPException:
            raise  # invalid param_path — fail fast

        try:
            result = run_backtest(modified, include_spy_correlation=False, indicator_cache=indicator_cache)
            s = result["summary"]
            results.append(OptimizerCombo(
                param_values=param_values,
                num_trades=s.get("num_trades", 0),
                total_return_pct=round(s.get("total_return_pct", 0.0), 2),
                sharpe_ratio=round(s.get("sharpe_ratio", 0.0), 3),
                win_rate_pct=s.get("win_rate_pct", 0.0),
                max_drawdown_pct=round(s.get("max_drawdown_pct", 0.0), 2),
                ev_per_trade=s.get("ev_per_trade"),
            ))
        except HTTPException as exc:
            if exc.status_code >= 500:
                raise  # data/server failure — surface to caller
            skipped += 1  # 4xx = invalid param value for this combo
        except Exception:
            skipped += 1  # non-HTTP error (pandas ValueError etc) — isolate per-combo

        if time.monotonic() - start > _TIMEOUT_SECS:
            break

    results.sort(key=lambda r: getattr(r, req.metric), reverse=True)

    return OptimizeResponse(
        results=results[:req.top_n],
        total_combos=total_combos,
        completed=len(results),
        skipped=skipped,
        timed_out=len(results) + skipped < total_combos,
    )

"""
routes/backtest_optimizer.py — Multi-parameter grid-search optimizer for backtests.

POST /api/backtest/optimize — Run all combinations of up to 3 parameters (max 200
total backtests), return top-N results ranked by a chosen metric. Answers "what
combination of RSI period, stop loss, and MA period maximizes Sharpe?"

Reuses _apply_param() from backtest_sweep.py for parameter mutation logic.
Grid execution runs through routes.grid_runner.run_grid, which amortizes
indicator computation across combos via a shared indicator_cache.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models import StrategyRequest
from routes.grid_runner import run_grid
from shared import _fetch

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

    # Pre-fetch the OHLCV slice ONCE and pass it through to every combo so
    # run_backtest skips _fetch entirely (mirrors the WFA setup at
    # routes/walk_forward.py:312). Without this, each combo paid a cache
    # lookup + a defensive DataFrame copy per call — ~100ms × 100 = 10s
    # on a 5-yr daily window. HTTPException surfacing data errors (e.g.
    # 404 No data) is propagated unchanged.
    df = _fetch(
        req.base.ticker,
        req.base.start,
        req.base.end,
        req.base.interval,
        source=req.base.source,
        extended_hours=req.base.extended_hours,
    )

    grid_results, timed_out, skipped = run_grid(
        req.base,
        req.params,
        timeout_secs=_TIMEOUT_SECS,
        df=df,
    )

    results: list[OptimizerCombo] = []
    for combo, summary in grid_results:
        results.append(OptimizerCombo(
            param_values=combo,
            num_trades=summary.get("num_trades", 0),
            total_return_pct=round(summary.get("total_return_pct", 0.0), 2),
            sharpe_ratio=round(summary.get("sharpe_ratio", 0.0), 3),
            win_rate_pct=summary.get("win_rate_pct", 0.0),
            max_drawdown_pct=round(summary.get("max_drawdown_pct", 0.0), 2),
            ev_per_trade=summary.get("ev_per_trade"),
        ))

    results.sort(key=lambda r: getattr(r, req.metric), reverse=True)

    return OptimizeResponse(
        results=results[:req.top_n],
        total_combos=total_combos,
        completed=len(results),
        skipped=skipped,
        timed_out=timed_out,
    )

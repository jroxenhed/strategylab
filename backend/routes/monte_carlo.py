from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import random
from typing import List

router = APIRouter()

class MonteCarloRequest(BaseModel):
    pnls: List[float]
    initial_capital: float
    n_simulations: int = 1000

def _percentiles(values: list) -> dict:
    s = sorted(values)
    n = len(s)
    return {
        "p5":  s[max(0, int(n * 0.05))],
        "p25": s[max(0, int(n * 0.25))],
        "p50": s[max(0, int(n * 0.50))],
        "p75": s[min(n-1, int(n * 0.75))],
        "p95": s[min(n-1, int(n * 0.95))],
    }

@router.post("/api/backtest/montecarlo")
def run_monte_carlo(req: MonteCarloRequest):
    if len(req.pnls) < 2:
        raise HTTPException(status_code=422, detail="Need at least 2 trades for Monte Carlo")

    n_sim = min(req.n_simulations, 5000)
    n_trades = len(req.pnls)

    # all_curves[sim_i] = list of n_trades+1 equity values
    all_curves: list[list[float]] = []
    max_drawdowns: list[float] = []
    final_values: list[float] = []
    min_equities: list[float] = []
    ruin_count = 0

    pnls = list(req.pnls)

    for _ in range(n_sim):
        random.shuffle(pnls)
        equity = req.initial_capital
        peak = equity
        max_dd = 0.0
        min_eq = req.initial_capital
        curve = [round(equity, 2)]
        for pnl in pnls:
            equity += pnl
            if equity > peak:
                peak = equity
            if peak > 0:
                dd = (peak - equity) / peak * 100
                if dd > max_dd:
                    max_dd = dd
            if equity < min_eq:
                min_eq = equity
            curve.append(round(equity, 2))
        all_curves.append(curve)
        max_drawdowns.append(max_dd)
        final_values.append(equity)
        min_equities.append(min_eq)
        if equity <= 0:
            ruin_count += 1

    # Build percentile curves by transposing
    steps = n_trades + 1
    pct_curves: dict[str, list[float]] = {"p5": [], "p25": [], "p50": [], "p75": [], "p95": []}
    for i in range(steps):
        step_values = [all_curves[s][i] for s in range(n_sim)]
        p = _percentiles(step_values)
        for key in pct_curves:
            pct_curves[key].append(round(p[key], 2))

    return {
        "num_simulations": n_sim,
        "num_trades": n_trades,
        "curves": pct_curves,
        "min_equity": _percentiles(min_equities),
        "max_drawdown_pct": _percentiles(max_drawdowns),
        "ruin_probability": round(ruin_count / n_sim * 100, 2),
    }

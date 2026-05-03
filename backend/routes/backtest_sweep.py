"""
routes/backtest_sweep.py — Parameter sensitivity sweep for backtests.

POST /api/backtest/sweep — Run N backtest variants with one parameter varied,
return summary stats for each. Used by the frontend Sensitivity tab to answer
"how fragile is this edge?"

Supported param_path values:
  "stop_loss_pct"           — stop-loss percentage
  "trailing_stop_value"     — trailing stop value (pct or ATR multiplier)
  "slippage_bps"            — transaction cost assumption
  "buy_rule_{i}_value"      — .value field of buy rule at index i
  "sell_rule_{i}_value"     — .value field of sell rule at index i
"""

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models import StrategyRequest
from routes.backtest import run_backtest

router = APIRouter()


class SweepRequest(BaseModel):
    base: StrategyRequest
    param_path: str     # identifies which param to vary
    values: list[float]  # values to try (max 25)


class SweepPoint(BaseModel):
    param_value: float
    num_trades: int
    total_return_pct: float
    sharpe_ratio: float
    win_rate_pct: float
    max_drawdown_pct: float
    ev_per_trade: Optional[float] = None


def _apply_param(base: StrategyRequest, param_path: str, value: float) -> StrategyRequest:
    """Return a deep copy of base with param_path set to value."""
    modified = base.model_copy(deep=True)

    if param_path == "stop_loss_pct":
        modified = modified.model_copy(update={"stop_loss_pct": max(0.0, value)})

    elif param_path == "trailing_stop_value":
        if modified.trailing_stop is None:
            raise HTTPException(status_code=400, detail="trailing_stop is not configured in base request")
        modified = modified.model_copy(
            update={"trailing_stop": modified.trailing_stop.model_copy(update={"value": max(0.0, value)})}
        )

    elif param_path == "slippage_bps":
        modified = modified.model_copy(update={"slippage_bps": max(0.0, value)})

    elif param_path.startswith("buy_rule_") and param_path.endswith("_value"):
        try:
            idx = int(param_path.split("_")[2])
        except (IndexError, ValueError):
            raise HTTPException(status_code=400, detail=f"Invalid param_path: {param_path}")
        if idx < 0 or idx >= len(modified.buy_rules):
            raise HTTPException(status_code=400, detail=f"buy_rule index {idx} out of range")
        rules = list(modified.buy_rules)
        rules[idx] = rules[idx].model_copy(update={"value": value})
        modified = modified.model_copy(update={"buy_rules": rules})

    elif param_path.startswith("sell_rule_") and param_path.endswith("_value"):
        try:
            idx = int(param_path.split("_")[2])
        except (IndexError, ValueError):
            raise HTTPException(status_code=400, detail=f"Invalid param_path: {param_path}")
        if idx < 0 or idx >= len(modified.sell_rules):
            raise HTTPException(status_code=400, detail=f"sell_rule index {idx} out of range")
        rules = list(modified.sell_rules)
        rules[idx] = rules[idx].model_copy(update={"value": value})
        modified = modified.model_copy(update={"sell_rules": rules})

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported param_path: {param_path!r}")

    return modified


@router.post("/api/backtest/sweep")
def sweep_backtest(req: SweepRequest) -> list[SweepPoint]:
    if len(req.values) == 0:
        raise HTTPException(status_code=400, detail="values must not be empty")
    if len(req.values) > 25:
        raise HTTPException(status_code=400, detail="Max 25 sweep values per request")

    results: list[SweepPoint] = []
    for v in req.values:
        try:
            modified = _apply_param(req.base, req.param_path, v)
        except HTTPException:
            raise

        try:
            result = run_backtest(modified)
            s = result["summary"]
            results.append(SweepPoint(
                param_value=v,
                num_trades=s.get("num_trades", 0),
                total_return_pct=round(s.get("total_return_pct", 0.0), 2),
                sharpe_ratio=round(s.get("sharpe_ratio", 0.0), 3),
                win_rate_pct=round(s.get("win_rate_pct", 0.0), 1),
                max_drawdown_pct=round(s.get("max_drawdown_pct", 0.0), 2),
                ev_per_trade=s.get("ev_per_trade"),
            ))
        except HTTPException:
            # Propagate 4xx/5xx from the base backtest on errors
            results.append(SweepPoint(
                param_value=v,
                num_trades=0,
                total_return_pct=0.0,
                sharpe_ratio=0.0,
                win_rate_pct=0.0,
                max_drawdown_pct=0.0,
                ev_per_trade=None,
            ))

    return results

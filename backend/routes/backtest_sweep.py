"""
routes/backtest_sweep.py — Parameter sensitivity sweep for backtests.

POST /api/backtest/sweep — Run N backtest variants with one parameter varied,
return summary stats for each. Used by the frontend Sensitivity tab to answer
"how fragile is this edge?"

Supported param_path values:
  "stop_loss_pct"                — stop-loss percentage
  "trailing_stop_value"          — trailing stop value (pct or ATR multiplier)
  "slippage_bps"                 — transaction cost assumption
  "buy_rule_{i}_value"           — .value field of buy rule at index i
  "sell_rule_{i}_value"          — .value field of sell rule at index i
  "buy_rule_{i}_params_{key}"    — named param (e.g. period) in buy rule at index i
  "sell_rule_{i}_params_{key}"   — named param (e.g. period) in sell rule at index i
"""

import re
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


class SweepResponse(BaseModel):
    results: list[SweepPoint]
    requested: int
    completed: int
    skipped: int


_RULE_LIST_RE = re.compile(
    r"^(long_|short_)?(buy|sell)_rule_(\d+)_(value|params_(.+))$"
)


def _apply_param(base: StrategyRequest, param_path: str, value: float) -> StrategyRequest:
    """Return a deep copy of base with param_path set to value.

    Rule paths accept an optional regime-mode prefix:
      buy_rule_<i>_value                       — base.buy_rules[i].value
      long_buy_rule_<i>_params_<key>           — base.long_buy_rules[i].params[key]
      short_sell_rule_<i>_value                — base.short_sell_rules[i].value
      …etc. The prefixed lists are what the engine consumes in regime mode;
      the bare buy_rules/sell_rules are UI symmetry only.
    """
    modified = base.model_copy(deep=True)

    if param_path == "stop_loss_pct":
        return modified.model_copy(update={"stop_loss_pct": max(0.0, value)})

    if param_path == "trailing_stop_value":
        if modified.trailing_stop is None:
            raise HTTPException(status_code=400, detail="trailing_stop is not configured in base request")
        return modified.model_copy(
            update={"trailing_stop": modified.trailing_stop.model_copy(update={"value": max(0.0, value)})}
        )

    if param_path == "slippage_bps":
        return modified.model_copy(update={"slippage_bps": max(0.0, value)})

    m = _RULE_LIST_RE.match(param_path)
    if m:
        prefix = m.group(1) or ""        # 'long_' | 'short_' | ''
        side = m.group(2)                # 'buy' | 'sell'
        idx = int(m.group(3))
        tail = m.group(4)                # 'value' | 'params_<key>'
        field_name = f"{prefix}{side}_rules"
        rules = getattr(modified, field_name, None)
        if rules is None:
            raise HTTPException(status_code=400, detail=f"{field_name} is not present on the request")
        if idx < 0 or idx >= len(rules):
            raise HTTPException(status_code=400, detail=f"{field_name} index {idx} out of range")
        rules = list(rules)
        if tail == "value":
            rules[idx] = rules[idx].model_copy(update={"value": value})
        else:
            param_key = tail[len("params_"):]
            existing_params = dict(rules[idx].params) if rules[idx].params else {}
            existing_params[param_key] = int(round(value)) if value == int(value) else value
            rules[idx] = rules[idx].model_copy(update={"params": existing_params})
        return modified.model_copy(update={field_name: rules})

    raise HTTPException(status_code=400, detail=f"Unsupported param_path: {param_path!r}")


@router.post("/api/backtest/sweep")
def sweep_backtest(req: SweepRequest) -> SweepResponse:
    if len(req.values) == 0:
        raise HTTPException(status_code=400, detail="values must not be empty")
    if len(req.values) > 25:
        raise HTTPException(status_code=400, detail="Max 25 sweep values per request")

    requested = len(req.values)
    results: list[SweepPoint] = []
    skipped = 0
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
            skipped += 1  # skip invalid parameter values (e.g. RSI period=1)

    return SweepResponse(
        results=results,
        requested=requested,
        completed=len(results),
        skipped=skipped,
    )

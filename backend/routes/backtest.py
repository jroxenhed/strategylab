from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
import numpy as np
import pandas as pd
from typing import Optional
from shared import _fetch, _format_time

router = APIRouter()


class Rule(BaseModel):
    indicator: str       # "macd", "rsi", "price", "ema"
    condition: str       # "crossover_up", "crossover_down", "above", "below", "crosses_above", "crosses_below"
    value: Optional[float] = None   # threshold (e.g. RSI < 30)
    param: Optional[str] = None     # e.g. "signal", "ema20"

class StrategyRequest(BaseModel):
    ticker: str
    start: str = "2023-01-01"
    end: str = "2024-01-01"
    interval: str = "1d"
    buy_rules: list[Rule]
    sell_rules: list[Rule]
    buy_logic: str = "AND"   # AND | OR
    sell_logic: str = "AND"
    initial_capital: float = 10000.0
    position_size: float = 1.0   # fraction of capital per trade (0.01–1.0)

    @field_validator('position_size')
    @classmethod
    def clamp_position_size(cls, v: float) -> float:
        return max(0.01, min(1.0, v))


@router.post("/api/backtest")
def run_backtest(req: StrategyRequest):
    try:
        df = _fetch(req.ticker, req.start, req.end, req.interval)

        close = df["Close"]

        # Precompute all indicators
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()

        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))

        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()

        indicators = {
            "macd": macd_line,
            "signal": signal_line,
            "rsi": rsi,
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "close": close,
        }

        def eval_rule(rule: Rule, i: int) -> bool:
            if i < 1:
                return False
            ind = rule.indicator.lower()
            cond = rule.condition.lower()

            series_map = {
                "macd": indicators["macd"],
                "rsi": indicators["rsi"],
                "price": indicators["close"],
                "ema20": indicators["ema20"],
                "ema50": indicators["ema50"],
                "ema200": indicators["ema200"],
            }
            ref_map = {
                "signal": indicators["signal"],
                "ema20": indicators["ema20"],
                "ema50": indicators["ema50"],
                "ema200": indicators["ema200"],
                "close": indicators["close"],
            }

            s = series_map.get(ind)
            if s is None:
                return False

            v_now = s.iloc[i]
            v_prev = s.iloc[i - 1]

            if cond in ("crossover_up", "crosses_above"):
                if rule.param and rule.param in ref_map:
                    ref = ref_map[rule.param]
                    return v_prev < ref.iloc[i - 1] and v_now >= ref.iloc[i]
                elif rule.value is not None:
                    return v_prev < rule.value <= v_now
            elif cond in ("crossover_down", "crosses_below"):
                if rule.param and rule.param in ref_map:
                    ref = ref_map[rule.param]
                    return v_prev > ref.iloc[i - 1] and v_now <= ref.iloc[i]
                elif rule.value is not None:
                    return v_prev > rule.value >= v_now
            elif cond == "above":
                if rule.param and rule.param in ref_map:
                    return v_now > ref_map[rule.param].iloc[i]
                elif rule.value is not None:
                    return v_now > rule.value
            elif cond == "below":
                if rule.param and rule.param in ref_map:
                    return v_now < ref_map[rule.param].iloc[i]
                elif rule.value is not None:
                    return v_now < rule.value
            return False

        def eval_rules(rules: list[Rule], logic: str, i: int) -> bool:
            results = [eval_rule(r, i) for r in rules]
            if not results:
                return False
            return all(results) if logic == "AND" else any(results)

        # Simulate
        capital = req.initial_capital
        position = 0.0
        entry_price = 0.0
        trades = []
        equity = []

        for i in range(len(df)):
            price = close.iloc[i]
            date = _format_time(df.index[i], req.interval)

            if position == 0 and eval_rules(req.buy_rules, req.buy_logic, i):
                shares = (capital * req.position_size) / price
                position = shares
                entry_price = price
                capital -= shares * price
                trades.append({"type": "buy", "date": date, "price": round(price, 4), "shares": round(shares, 4)})

            elif position > 0 and eval_rules(req.sell_rules, req.sell_logic, i):
                proceeds = position * price
                pnl = proceeds - position * entry_price
                trades.append({
                    "type": "sell",
                    "date": date,
                    "price": round(price, 4),
                    "shares": round(position, 4),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl / (position * entry_price) * 100, 2),
                })
                capital += proceeds
                position = 0.0

            total_value = capital + (position * price if position > 0 else 0)
            equity.append({"time": date, "value": round(total_value, 2)})

        # Close open position at last price
        final_price = close.iloc[-1]
        final_value = capital + position * final_price

        total_return = (final_value - req.initial_capital) / req.initial_capital * 100
        buy_hold_return = (close.iloc[-1] - close.iloc[0]) / close.iloc[0] * 100

        # Sharpe ratio (annualized, daily returns)
        eq_values = [e["value"] for e in equity]
        eq_series = pd.Series(eq_values)
        daily_returns = eq_series.pct_change().dropna()
        sharpe = float((daily_returns.mean() / daily_returns.std()) * np.sqrt(252)) if daily_returns.std() > 0 else 0

        # Max drawdown
        peak = eq_series.cummax()
        drawdown = (eq_series - peak) / peak
        max_drawdown = float(drawdown.min() * 100)

        sell_trades = [t for t in trades if t["type"] == "sell"]
        winning = [t for t in sell_trades if t.get("pnl", 0) > 0]
        win_rate = len(winning) / len(sell_trades) * 100 if sell_trades else 0

        return {
            "summary": {
                "initial_capital": req.initial_capital,
                "final_value": round(final_value, 2),
                "total_return_pct": round(total_return, 2),
                "buy_hold_return_pct": round(buy_hold_return, 2),
                "num_trades": len(sell_trades),
                "win_rate_pct": round(win_rate, 2),
                "sharpe_ratio": round(sharpe, 3),
                "max_drawdown_pct": round(max_drawdown, 2),
            },
            "trades": trades,
            "equity_curve": equity,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

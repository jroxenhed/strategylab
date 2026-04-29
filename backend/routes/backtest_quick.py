from fastapi import APIRouter
from typing import Optional
import numpy as np
import pandas as pd
from datetime import date, timedelta
from pydantic import BaseModel

from shared import _fetch, _format_time
from signal_engine import Rule, compute_indicators, eval_rules, migrate_rule

router = APIRouter()


class QuickBacktestRequest(BaseModel):
    ticker: str
    interval: str = "1d"
    lookback_days: int = 90
    buy_rules: list[Rule]
    sell_rules: list[Rule]
    buy_logic: str = "AND"
    sell_logic: str = "AND"
    direction: str = "long"
    initial_capital: float = 10000.0
    stop_loss_pct: float = 0.0
    trailing_stop: Optional[dict] = None


class QuickBacktestResult(BaseModel):
    ticker: str
    return_pct: Optional[float] = None
    sharpe: Optional[float] = None
    win_rate_pct: Optional[float] = None
    num_trades: Optional[int] = None
    max_drawdown_pct: Optional[float] = None
    signal_now: Optional[bool] = None
    last_signal_date: Optional[str] = None
    error: Optional[str] = None


class BatchQuickBacktestRequest(BaseModel):
    symbols: list[str]
    interval: str = "1d"
    lookback_days: int = 90
    buy_rules: list[Rule]
    sell_rules: list[Rule]
    buy_logic: str = "AND"
    sell_logic: str = "AND"
    direction: str = "long"
    initial_capital: float = 10000.0
    stop_loss_pct: float = 0.0
    trailing_stop: Optional[dict] = None


def _run_quick(req: QuickBacktestRequest) -> QuickBacktestResult:
    """Core quick backtest logic. Returns a QuickBacktestResult."""
    ticker = req.ticker.upper()
    end_date = date.today()
    start_date = end_date - timedelta(days=req.lookback_days)
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()

    try:
        df = _fetch(ticker, start_str, end_str, req.interval)
    except Exception as e:
        return QuickBacktestResult(ticker=ticker, error=f"No data for {ticker}: {str(e)}")

    if df is None or len(df) < 2:
        return QuickBacktestResult(ticker=ticker, error=f"No data for {ticker}")

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"] if "Volume" in df.columns else None

    buy_rules = [migrate_rule(r) for r in req.buy_rules]
    sell_rules = [migrate_rule(r) for r in req.sell_rules]
    all_rules = buy_rules + sell_rules
    indicators = compute_indicators(close, high=high, low=low, volume=volume, rules=all_rules)

    is_short = req.direction == "short"
    capital = req.initial_capital
    position = 0.0
    entry_price = 0.0
    trades = []
    equity = []

    for i in range(len(df)):
        price = float(close.iloc[i])

        buy_fires = position == 0 and eval_rules(buy_rules, req.buy_logic, indicators, i)
        if buy_fires:
            fill_price = price
            shares = capital / fill_price
            position = shares
            entry_price = fill_price
            capital -= shares * fill_price

        elif position > 0:
            stop_hit = False
            if req.stop_loss_pct and req.stop_loss_pct > 0:
                if is_short:
                    stop_hit = float(high.iloc[i]) >= entry_price * (1 + req.stop_loss_pct / 100)
                else:
                    stop_hit = float(low.iloc[i]) <= entry_price * (1 - req.stop_loss_pct / 100)

            sell_fired = eval_rules(sell_rules, req.sell_logic, indicators, i)
            if stop_hit or sell_fired:
                exit_price = price
                if is_short:
                    pnl = position * (entry_price - exit_price)
                    capital += position * entry_price + pnl
                else:
                    capital += position * exit_price
                    pnl = position * (exit_price - entry_price)
                trades.append({"pnl": round(pnl, 2)})
                position = 0.0
                entry_price = 0.0

        if is_short and position > 0:
            unrealized = position * (entry_price - price)
            total_value = capital + position * entry_price + unrealized
        else:
            total_value = capital + (position * price if position > 0 else 0)
        equity.append(total_value)

    # Close open position at last bar
    final_price = float(close.iloc[-1])
    if is_short and position > 0:
        unrealized = position * (entry_price - final_price)
        final_value = capital + position * entry_price + unrealized
    else:
        final_value = capital + position * final_price

    total_return = (final_value - req.initial_capital) / req.initial_capital * 100

    # Sharpe ratio (annualized)
    eq_series = pd.Series(equity)
    daily_returns = eq_series.pct_change().dropna()
    sharpe = float((daily_returns.mean() / daily_returns.std()) * np.sqrt(252)) if daily_returns.std() > 0 else 0.0

    # Max drawdown
    peak = eq_series.cummax()
    drawdown = (eq_series - peak) / peak
    max_drawdown = float(drawdown.min() * 100)

    # Win rate
    winning = [t for t in trades if t["pnl"] > 0]
    win_rate = len(winning) / len(trades) * 100 if trades else 0.0

    # signal_now: does the buy rule fire on the very last bar?
    last_i = len(df) - 1
    signal_now = bool(eval_rules(buy_rules, req.buy_logic, indicators, last_i))

    # last_signal_date: most recent bar where buy rules fired
    last_signal_date: Optional[str] = None
    for i in range(last_i, -1, -1):
        if eval_rules(buy_rules, req.buy_logic, indicators, i):
            last_signal_date = _format_time(df.index[i], req.interval)
            # _format_time returns unix int for intraday — convert to date string
            if isinstance(last_signal_date, int):
                last_signal_date = str(date.fromtimestamp(last_signal_date))
            break

    return QuickBacktestResult(
        ticker=ticker,
        return_pct=round(total_return, 2),
        sharpe=round(sharpe, 3),
        win_rate_pct=round(win_rate, 2),
        num_trades=len(trades),
        max_drawdown_pct=round(max_drawdown, 2),
        signal_now=signal_now,
        last_signal_date=last_signal_date,
        error=None,
    )


@router.post("/api/backtest/quick", response_model=QuickBacktestResult)
def quick_backtest(req: QuickBacktestRequest):
    """Fast per-ticker backtest — summary stats only, no equity curve or trade list."""
    return _run_quick(req)


@router.post("/api/backtest/quick/batch")
def quick_backtest_batch(req: BatchQuickBacktestRequest):
    """Batch quick backtest over a list of symbols. Runs sequentially to respect rate limits."""
    results = []
    for symbol in req.symbols:
        single_req = QuickBacktestRequest(
            ticker=symbol,
            interval=req.interval,
            lookback_days=req.lookback_days,
            buy_rules=req.buy_rules,
            sell_rules=req.sell_rules,
            buy_logic=req.buy_logic,
            sell_logic=req.sell_logic,
            direction=req.direction,
            initial_capital=req.initial_capital,
            stop_loss_pct=req.stop_loss_pct,
            trailing_stop=req.trailing_stop,
        )
        results.append(_run_quick(single_req))
    return {"results": results}

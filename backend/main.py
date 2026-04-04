from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
import yfinance as yf
import pandas as pd
import numpy as np
from typing import Optional
import warnings
warnings.filterwarnings("ignore")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


_INTRADAY_INTERVALS = {'1m', '2m', '5m', '15m', '30m', '60m', '90m', '1h'}

# yfinance max lookback per interval (days)
_INTERVAL_MAX_DAYS = {
    '1m': 7, '2m': 60, '5m': 60, '15m': 60, '30m': 60,
    '60m': 730, '90m': 60, '1h': 730,
}


def _fetch(ticker: str, start: str, end: str, interval: str) -> pd.DataFrame:
    """Thread-safe data fetch using yf.Ticker instead of yf.download.

    yf.download uses shared global state that corrupts data when called
    concurrently from FastAPI's thread pool.
    """
    # Clamp date range to yfinance limits for intraday intervals
    max_days = _INTERVAL_MAX_DAYS.get(interval)
    if max_days is not None:
        from datetime import datetime, timedelta
        end_dt = datetime.strptime(end, '%Y-%m-%d')
        earliest = end_dt - timedelta(days=max_days)
        start_dt = datetime.strptime(start, '%Y-%m-%d')
        if start_dt < earliest:
            start = earliest.strftime('%Y-%m-%d')

    df = yf.Ticker(ticker).history(start=start, end=end, interval=interval, auto_adjust=True)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data for {ticker}")
    return df.dropna()


def _format_time(idx, interval: str):
    """Return lightweight-charts compatible time: unix seconds for intraday, YYYY-MM-DD for daily+."""
    if interval in _INTRADAY_INTERVALS:
        ts = pd.Timestamp(idx)
        if ts.tzinfo is not None:
            ts = ts.tz_convert('UTC')
        return int(ts.timestamp())
    return str(idx)[:10]


# ── Data endpoint ─────────────────────────────────────────────────────────────

@app.get("/api/ohlcv/{ticker}")
def get_ohlcv(ticker: str, start: str = "2023-01-01", end: str = "2024-01-01", interval: str = "1d"):
    try:
        df = _fetch(ticker, start, end, interval)
        return {
            "ticker": ticker,
            "data": [
                {
                    "time": _format_time(idx, interval),
                    "open": round(float(row["Open"]), 4),
                    "high": round(float(row["High"]), 4),
                    "low": round(float(row["Low"]), 4),
                    "close": round(float(row["Close"]), 4),
                    "volume": int(row["Volume"]),
                }
                for idx, row in df.iterrows()
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Indicators endpoint ────────────────────────────────────────────────────────

@app.get("/api/indicators/{ticker}")
def get_indicators(
    ticker: str,
    start: str = "2023-01-01",
    end: str = "2024-01-01",
    interval: str = "1d",
    indicators: str = "macd,rsi",
):
    try:
        df = _fetch(ticker, start, end, interval)

        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        result = {}
        requested = [i.strip().lower() for i in indicators.split(",")]

        if "macd" in requested:
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            histogram = macd_line - signal_line
            result["macd"] = {
                "macd": _series_to_list(df.index, interval,macd_line),
                "signal": _series_to_list(df.index, interval,signal_line),
                "histogram": _series_to_list(df.index, interval,histogram),
            }

        if "rsi" in requested:
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))
            result["rsi"] = _series_to_list(df.index, interval,rsi)

        if "ema" in requested:
            result["ema"] = {
                "ema20": _series_to_list(df.index, interval,close.ewm(span=20, adjust=False).mean()),
                "ema50": _series_to_list(df.index, interval,close.ewm(span=50, adjust=False).mean()),
                "ema200": _series_to_list(df.index, interval,close.ewm(span=200, adjust=False).mean()),
            }

        if "bb" in requested:
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            result["bb"] = {
                "upper": _series_to_list(df.index, interval,sma20 + 2 * std20),
                "middle": _series_to_list(df.index, interval,sma20),
                "lower": _series_to_list(df.index, interval,sma20 - 2 * std20),
            }

        if "orb" in requested:
            # Opening Range Breakout — first 30 min high/low extended as daily levels
            # For daily data, use first candle of each day (same as day open)
            result["orb"] = {
                "high": _series_to_list(df.index, interval,high),
                "low": _series_to_list(df.index, interval,low),
            }

        if "volume" in requested:
            result["volume"] = _series_to_list(df.index, interval,df["Volume"])

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _series_to_list(index, interval, series):
    return [
        {"time": _format_time(t, interval), "value": round(float(v), 4) if pd.notna(v) else None}
        for t, v in zip(index, series)
    ]


# ── Backtest endpoint ──────────────────────────────────────────────────────────

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


@app.post("/api/backtest")
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


# ── Ticker search ──────────────────────────────────────────────────────────────

@app.get("/api/search")
def search_ticker(q: str):
    try:
        results = yf.Search(q, max_results=8)
        quotes = results.quotes if results.quotes else []
        return [
            {
                "symbol": r.get("symbol", ""),
                "name": r.get("longname") or r.get("shortname") or r.get("symbol", ""),
                "type": r.get("quoteType", ""),
            }
            for r in quotes
            if r.get("symbol")
        ]
    except Exception:
        return []

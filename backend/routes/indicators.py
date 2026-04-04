from fastapi import APIRouter, HTTPException
import numpy as np
import pandas as pd
from shared import _fetch, _format_time

router = APIRouter()


def _series_to_list(index, interval, series):
    return [
        {"time": _format_time(t, interval), "value": round(float(v), 4) if pd.notna(v) else None}
        for t, v in zip(index, series)
    ]


@router.get("/api/indicators/{ticker}")
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

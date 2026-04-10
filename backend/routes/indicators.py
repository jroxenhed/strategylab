from fastapi import APIRouter, HTTPException
import pandas as pd
from shared import _fetch, _format_time
from signal_engine import compute_indicators

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
    source: str = "yahoo",
):
    try:
        df = _fetch(ticker, start, end, interval, source=source)

        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        result = {}
        requested = [i.strip().lower() for i in indicators.split(",")]

        needs_computed = any(k in requested for k in ("macd", "rsi", "ema"))
        ind = compute_indicators(close, high, low) if needs_computed else {}

        if "macd" in requested:
            histogram = ind["macd"] - ind["signal"]
            result["macd"] = {
                "macd": _series_to_list(df.index, interval, ind["macd"]),
                "signal": _series_to_list(df.index, interval, ind["signal"]),
                "histogram": _series_to_list(df.index, interval, histogram),
            }

        if "rsi" in requested:
            result["rsi"] = _series_to_list(df.index, interval, ind["rsi"])

        if "ema" in requested:
            result["ema"] = {
                "ema20": _series_to_list(df.index, interval, ind["ema20"]),
                "ema50": _series_to_list(df.index, interval, ind["ema50"]),
                "ema200": _series_to_list(df.index, interval, ind["ema200"]),
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

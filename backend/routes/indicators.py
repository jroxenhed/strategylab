from fastapi import APIRouter, HTTPException
import numpy as np
import pandas as pd
from shared import _fetch, _format_time
from signal_engine import compute_indicators, _apply_sg, _apply_sg_predictive

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
    ma_type: str = "ema",
    sg8_window: int = 7,
    sg8_poly: int = 2,
    sg21_window: int = 7,
    sg21_poly: int = 2,
    predictive_sg: bool = False,
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

        if "ma" in requested:
            # Compute MA8 and MA21 based on selected type
            ma_type_lower = ma_type.lower()
            if ma_type_lower == "sma":
                ma8 = close.rolling(8).mean()
                ma21 = close.rolling(21).mean()
            elif ma_type_lower == "rma":
                ma8 = close.ewm(alpha=1/8, adjust=False).mean()
                ma21 = close.ewm(alpha=1/21, adjust=False).mean()
            else:  # default ema
                ma8 = close.ewm(span=8, adjust=False).mean()
                ma21 = close.ewm(span=21, adjust=False).mean()
                ma_type_lower = "ema"

            # Savitzky-Golay smoothed MA8 and MA21 (independent params)
            sg8_w = max(sg8_window, sg8_poly + 1)
            if sg8_w % 2 == 0:
                sg8_w += 1
            sg21_w = max(sg21_window, sg21_poly + 1)
            if sg21_w % 2 == 0:
                sg21_w += 1
            if predictive_sg:
                ma8_sg = _apply_sg_predictive(ma8, sg8_window, sg8_poly)
                ma21_sg = _apply_sg_predictive(ma21, sg21_window, sg21_poly)
            else:
                ma8_sg = _apply_sg(ma8, sg8_window, sg8_poly, causal=True)
                ma21_sg = _apply_sg(ma21, sg21_window, sg21_poly, causal=True)

            result["ma"] = {
                "ma8": _series_to_list(df.index, interval, ma8),
                "ma21": _series_to_list(df.index, interval, ma21),
                "ma8_sg": _series_to_list(df.index, interval, ma8_sg),
                "ma21_sg": _series_to_list(df.index, interval, ma21_sg),
                "ma_type": ma_type_lower,
                "sg8_window": sg8_w,
                "sg8_poly": sg8_poly,
                "sg21_window": sg21_w,
                "sg21_poly": sg21_poly,
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

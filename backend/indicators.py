from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


@dataclass
class OHLCVSeries:
    close: pd.Series
    high: pd.Series
    low: pd.Series
    volume: pd.Series


def compute_rsi(ohlcv: OHLCVSeries, params: dict) -> dict[str, pd.Series]:
    period = int(params.get("period", 14))
    delta = ohlcv.close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return {"rsi": rsi}


def compute_macd(ohlcv: OHLCVSeries, params: dict) -> dict[str, pd.Series]:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    signal_period = int(params.get("signal", 9))
    ema_fast = ohlcv.close.ewm(span=fast, adjust=False).mean()
    ema_slow = ohlcv.close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def compute_bb(ohlcv: OHLCVSeries, params: dict) -> dict[str, pd.Series]:
    period = int(params.get("period", 20))
    stddev = float(params.get("stddev", 2))
    sma = ohlcv.close.rolling(period).mean()
    std = ohlcv.close.rolling(period).std()
    return {
        "upper": sma + stddev * std,
        "middle": sma,
        "lower": sma - stddev * std,
    }


def compute_atr(ohlcv: OHLCVSeries, params: dict) -> dict[str, pd.Series]:
    period = int(params.get("period", 14))
    prev_close = ohlcv.close.shift(1)
    tr = pd.concat([
        ohlcv.high - ohlcv.low,
        (ohlcv.high - prev_close).abs(),
        (ohlcv.low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return {"atr": tr.rolling(period).mean()}


def compute_ma(ohlcv: OHLCVSeries, params: dict) -> dict[str, pd.Series]:
    period = int(params.get("period", 20))
    ma_type = str(params.get("type", "ema")).lower()
    if ma_type == "sma":
        ma = ohlcv.close.rolling(period).mean()
    elif ma_type == "rma":
        ma = ohlcv.close.ewm(alpha=1 / period, adjust=False).mean()
    else:
        ma = ohlcv.close.ewm(span=period, adjust=False).mean()
    return {"ma": ma}


def compute_ema(ohlcv: OHLCVSeries, params: dict) -> dict[str, pd.Series]:
    """Compute multiple EMA periods at once (used by signal_engine's compute_indicators)."""
    periods = params.get("periods", [20])
    result = {}
    for p in periods:
        result[f"ema{p}"] = ohlcv.close.ewm(span=int(p), adjust=False).mean()
    return result


def compute_volume(ohlcv: OHLCVSeries, params: dict) -> dict[str, pd.Series]:
    return {"volume": ohlcv.volume}


INDICATOR_REGISTRY: dict[str, Callable[[OHLCVSeries, dict], dict[str, pd.Series]]] = {
    "rsi": compute_rsi,
    "macd": compute_macd,
    "bb": compute_bb,
    "atr": compute_atr,
    "ma": compute_ma,
    "ema": compute_ema,
    "volume": compute_volume,
}


PARAM_CONSTRAINTS: dict[str, dict[str, tuple[float, float]]] = {
    "rsi":   {"period": (2, 500)},
    "macd":  {"fast": (2, 500), "slow": (2, 500), "signal": (2, 500)},
    "bb":    {"period": (2, 500), "stddev": (0.5, 5)},
    "atr":   {"period": (2, 500)},
    "ma":    {"period": (2, 500)},
}


def _validate_params(indicator_type: str, params: dict) -> None:
    constraints = PARAM_CONSTRAINTS.get(indicator_type, {})
    for key, (lo, hi) in constraints.items():
        val = params.get(key)
        if val is not None and not (lo <= float(val) <= hi):
            raise ValueError(f"{indicator_type}.{key} must be between {lo} and {hi}, got {val}")


def compute_instance(
    indicator_type: str,
    params: dict,
    ohlcv: OHLCVSeries,
) -> dict[str, pd.Series]:
    fn = INDICATOR_REGISTRY.get(indicator_type)
    if not fn:
        raise ValueError(f"Unknown indicator type: {indicator_type}")
    _validate_params(indicator_type, params)
    return fn(ohlcv, params)

from typing import Literal, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import pandas as pd
from shared import _fetch, _format_time, fetch_higher_tf, align_htf_to_ltf, htf_lookback_days, _INTRADAY_INTERVALS
from indicators import compute_instance, OHLCVSeries

router = APIRouter()


def _series_to_list(index, interval, series):
    return [
        {"time": _format_time(t, interval), "value": round(float(v), 4) if pd.notna(v) else None}
        for t, v in zip(index, series)
    ]


_PANDAS_FREQ_MAP = {
    "1m": "1min", "2m": "2min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "60m": "1h", "90m": "90min",
    "1d": "1D", "1wk": "1W", "1mo": "1ME",
}


IndicatorTypeLiteral = Literal["rsi", "macd", "bb", "atr", "ma", "volume", "stochastic", "vwap", "adx"]


class InstanceRequest(BaseModel):
    id: str
    type: IndicatorTypeLiteral
    params: dict = {}


class IndicatorsPostRequest(BaseModel):
    start: str = "2023-01-01"
    end: str = "2024-01-01"
    interval: str = "1d"
    source: str = "yahoo"
    extended_hours: bool = False
    instances: list[InstanceRequest] = Field(max_length=20)
    htf_interval: Optional[str] = None
    view_interval: Optional[str] = None


@router.post("/api/indicators/{ticker}")
def post_indicators(ticker: str, body: IndicatorsPostRequest):
    try:
        df = _fetch(ticker, body.start, body.end, body.interval, source=body.source, extended_hours=body.extended_hours)

        if body.htf_interval:
            # Compute indicator at higher timeframe, then align to LTF index
            max_lookback = max(
                (htf_lookback_days(inst.type, inst.params) for inst in body.instances),
                default=30,
            )
            extended_start = (
                pd.Timestamp(body.start) - pd.Timedelta(days=max_lookback)
            ).strftime("%Y-%m-%d")
            df_htf = fetch_higher_tf(ticker, extended_start, body.end, body.htf_interval, source=body.source)
            ohlcv_htf = OHLCVSeries(
                close=df_htf["Close"], high=df_htf["High"],
                low=df_htf["Low"], volume=df_htf["Volume"],
            )

            result = {}
            for inst in body.instances:
                try:
                    series_dict = compute_instance(inst.type, inst.params, ohlcv_htf)
                    result[inst.id] = {
                        key: _series_to_list(
                            df.index, body.interval,
                            align_htf_to_ltf(series, df.index),
                        )
                        for key, series in series_dict.items()
                    }
                except ValueError as e:
                    result[inst.id] = {"error": "invalid_params", "detail": str(e)}
                except Exception:
                    result[inst.id] = {"error": "compute_failed"}
            return result

        ohlcv = OHLCVSeries(
            close=df["Close"], high=df["High"],
            low=df["Low"], volume=df["Volume"],
        )

        needs_resample = (
            body.view_interval
            and body.view_interval != body.interval
            and body.view_interval in _PANDAS_FREQ_MAP
        )

        result = {}
        for inst in body.instances:
            try:
                series_dict = compute_instance(inst.type, inst.params, ohlcv)
                if needs_resample:
                    freq = _PANDAS_FREQ_MAP[body.view_interval]
                    resample_kwargs = {}
                    resampled = {}
                    for key, series in series_dict.items():
                        rs = series.resample(freq, **resample_kwargs).last().dropna()
                        resampled[key] = _series_to_list(rs.index, body.view_interval, rs)
                    result[inst.id] = resampled
                else:
                    result[inst.id] = {
                        key: _series_to_list(df.index, body.interval, series)
                        for key, series in series_dict.items()
                    }
            except ValueError as e:
                result[inst.id] = {"error": "invalid_params", "detail": str(e)}
            except Exception:
                result[inst.id] = {"error": "compute_failed"}
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import pandas as pd
from shared import _fetch, _format_time
from indicators import compute_instance, OHLCVSeries

router = APIRouter()


def _series_to_list(index, interval, series):
    return [
        {"time": _format_time(t, interval), "value": round(float(v), 4) if pd.notna(v) else None}
        for t, v in zip(index, series)
    ]


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


@router.post("/api/indicators/{ticker}")
def post_indicators(ticker: str, body: IndicatorsPostRequest):
    try:
        df = _fetch(ticker, body.start, body.end, body.interval, source=body.source, extended_hours=body.extended_hours)
        ohlcv = OHLCVSeries(
            close=df["Close"], high=df["High"],
            low=df["Low"], volume=df["Volume"],
        )

        result = {}
        for inst in body.instances:
            try:
                series_dict = compute_instance(inst.type, inst.params, ohlcv)
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

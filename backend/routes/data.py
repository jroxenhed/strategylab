from fastapi import APIRouter, HTTPException
from shared import _fetch, _format_time

router = APIRouter()


@router.get("/api/ohlcv/{ticker}")
def get_ohlcv(ticker: str, start: str = "2023-01-01", end: str = "2024-01-01", interval: str = "1d", source: str = "yahoo"):
    try:
        df = _fetch(ticker, start, end, interval, source=source)
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

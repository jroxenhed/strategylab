import logging

from fastapi import APIRouter, HTTPException
from shared import _fetch, _format_time, require_valid_source
from models import Interval, IntervalField

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/ohlcv/{ticker}", responses={400: {"description": "Invalid source"}})
def get_ohlcv(ticker: str, start: str = "2023-01-01", end: str = "2024-01-01", interval: IntervalField = "1d", source: str = "yahoo", extended_hours: bool = False):
    # F94: shared allowlist + case-normalize. Mirrors F37 (quote routes).
    source = require_valid_source(source)
    try:
        df = _fetch(ticker, start, end, interval, source=source, extended_hours=extended_hours)
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
    except Exception:
        logger.exception("/api/ohlcv failed for %s [%s]", ticker, source)
        raise HTTPException(status_code=500, detail="data fetch failed")

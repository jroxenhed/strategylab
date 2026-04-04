from fastapi import HTTPException
import pandas as pd
import yfinance as yf


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

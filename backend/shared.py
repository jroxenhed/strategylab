from typing import Protocol
from fastapi import HTTPException
import os
import pandas as pd
import yfinance as yf


_INTRADAY_INTERVALS = {'1m', '2m', '5m', '15m', '30m', '60m', '90m', '1h'}

# yfinance max lookback per interval (days)
_INTERVAL_MAX_DAYS = {
    '1m': 7, '2m': 60, '5m': 60, '15m': 60, '30m': 60,
    '60m': 730, '90m': 60, '1h': 730,
}


class DataProvider(Protocol):
    def fetch(self, ticker: str, start: str, end: str, interval: str) -> pd.DataFrame:
        """Return DataFrame with columns: Open, High, Low, Close, Volume and DatetimeIndex."""
        ...


class YahooProvider:
    def fetch(self, ticker: str, start: str, end: str, interval: str) -> pd.DataFrame:
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


# Provider registry
_providers: dict[str, DataProvider] = {"yahoo": YahooProvider()}


def register_provider(name: str, provider: DataProvider) -> None:
    _providers[name] = provider


def get_available_providers() -> list[str]:
    return list(_providers.keys())


def _fetch(ticker: str, start: str, end: str, interval: str, source: str = "yahoo") -> pd.DataFrame:
    """Fetch OHLCV data from the specified provider.

    yf.download uses shared global state that corrupts data when called
    concurrently from FastAPI's thread pool — YahooProvider uses yf.Ticker instead.
    """
    provider = _providers.get(source)
    if provider is None:
        raise HTTPException(status_code=400, detail=f"Unknown data source: {source}")
    return provider.fetch(ticker, start, end, interval)


def _format_time(idx, interval: str):
    """Return lightweight-charts compatible time: unix seconds for intraday, YYYY-MM-DD for daily+."""
    if interval in _INTRADAY_INTERVALS:
        ts = pd.Timestamp(idx)
        if ts.tzinfo is not None:
            ts = ts.tz_convert('UTC')
        return int(ts.timestamp())
    return str(idx)[:10]

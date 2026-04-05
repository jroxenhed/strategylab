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


# Alpaca interval mapping
_ALPACA_INTERVAL_MAP: dict[str, tuple] | None = None

def _get_alpaca_interval_map():
    global _ALPACA_INTERVAL_MAP
    if _ALPACA_INTERVAL_MAP is None:
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        _ALPACA_INTERVAL_MAP = {
            '1m': TimeFrame.Minute,
            '5m': TimeFrame(5, TimeFrameUnit.Minute),
            '15m': TimeFrame(15, TimeFrameUnit.Minute),
            '30m': TimeFrame(30, TimeFrameUnit.Minute),
            '1h': TimeFrame.Hour,
            '60m': TimeFrame.Hour,
            '1d': TimeFrame.Day,
            '1wk': TimeFrame.Week,
            '1mo': TimeFrame.Month,
        }
    return _ALPACA_INTERVAL_MAP

_ALPACA_UNSUPPORTED = {'2m', '90m'}


class AlpacaProvider:
    def __init__(self, client):
        self._client = client

    def fetch(self, ticker: str, start: str, end: str, interval: str) -> pd.DataFrame:
        if interval in _ALPACA_UNSUPPORTED:
            raise HTTPException(
                status_code=400,
                detail=f"Interval {interval} not supported by Alpaca"
            )

        from alpaca.data.requests import StockBarsRequest
        interval_map = _get_alpaca_interval_map()
        timeframe = interval_map.get(interval)
        if timeframe is None:
            raise HTTPException(
                status_code=400,
                detail=f"Interval {interval} not supported by Alpaca"
            )

        request = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=timeframe,
            start=pd.Timestamp(start, tz='UTC'),
            end=pd.Timestamp(end, tz='UTC'),
        )

        bars = self._client.get_stock_bars(request)
        try:
            bar_list = bars[ticker]
        except (KeyError, IndexError):
            bar_list = []
        if not bar_list:
            raise HTTPException(status_code=404, detail=f"No data for {ticker}")

        rows = []
        for bar in bar_list:
            rows.append({
                "Open": bar.open,
                "High": bar.high,
                "Low": bar.low,
                "Close": bar.close,
                "Volume": bar.volume,
                "timestamp": bar.timestamp,
            })

        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df.pop("timestamp"))
        return df


def _create_alpaca_client():
    """Create an Alpaca StockHistoricalDataClient from env vars. Returns None if no keys."""
    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    if not api_key or not secret_key:
        return None
    from alpaca.data.historical import StockHistoricalDataClient
    return StockHistoricalDataClient(api_key, secret_key)


def _create_trading_client():
    """Create an Alpaca TradingClient for paper trading. Returns None if no keys."""
    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    if not api_key or not secret_key:
        return None
    from alpaca.trading.client import TradingClient
    return TradingClient(api_key, secret_key, paper=True)


_trading_client = _create_trading_client()


def get_trading_client():
    if _trading_client is None:
        raise HTTPException(status_code=503, detail="Alpaca trading not configured")
    return _trading_client


# Provider registry
_providers: dict[str, DataProvider] = {"yahoo": YahooProvider()}

_alpaca_client = _create_alpaca_client()
if _alpaca_client is not None:
    _providers["alpaca"] = AlpacaProvider(_alpaca_client)


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

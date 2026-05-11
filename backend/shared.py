from typing import Protocol
from collections import deque
from fastapi import HTTPException
import asyncio
import logging
import os
import threading
import time
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class DataRateCounter:
    """Sliding-window counter for data API calls (60s window)."""

    def __init__(self, window_secs: float = 60.0):
        self._window = window_secs
        self._calls: deque[float] = deque()

    def record(self):
        self._calls.append(time.monotonic())

    def calls_per_minute(self) -> int:
        cutoff = time.monotonic() - self._window
        while self._calls and self._calls[0] < cutoff:
            self._calls.popleft()
        return len(self._calls)


_data_rate_counter = DataRateCounter()


def get_data_rate_counter() -> DataRateCounter:
    return _data_rate_counter


_INTRADAY_INTERVALS = {'1m', '2m', '5m', '15m', '30m', '60m', '90m', '1h'}

# yfinance max lookback per interval (days)
_INTERVAL_MAX_DAYS = {
    '1m': 7, '2m': 60, '5m': 60, '15m': 60, '30m': 60,
    '60m': 730, '90m': 60, '1h': 730,
}


class DataProvider(Protocol):
    def fetch(self, ticker: str, start: str, end: str, interval: str, extended_hours: bool = False) -> pd.DataFrame:
        """Return DataFrame with columns: Open, High, Low, Close, Volume and DatetimeIndex."""
        ...


class YahooProvider:
    def fetch(self, ticker: str, start: str, end: str, interval: str, extended_hours: bool = False) -> pd.DataFrame:
        _data_rate_counter.record()
        # Clamp date range to yfinance limits for intraday intervals
        max_days = _INTERVAL_MAX_DAYS.get(interval)
        if max_days is not None:
            from datetime import datetime, timedelta
            end_dt = datetime.strptime(end, '%Y-%m-%d')
            earliest = end_dt - timedelta(days=max_days)
            start_dt = datetime.strptime(start, '%Y-%m-%d')
            if start_dt < earliest:
                start = earliest.strftime('%Y-%m-%d')

        df = yf.Ticker(ticker).history(start=start, end=end, interval=interval, auto_adjust=True, prepost=extended_hours)
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
    def __init__(self, client, feed: str = 'sip'):
        self._client = client
        self._feed = feed

    def fetch(self, ticker: str, start: str, end: str, interval: str, extended_hours: bool = False) -> pd.DataFrame:
        _data_rate_counter.record()
        if interval in _ALPACA_UNSUPPORTED:
            raise HTTPException(
                status_code=400,
                detail=f"Interval {interval} not supported by Alpaca"
            )

        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.enums import Adjustment
        interval_map = _get_alpaca_interval_map()
        timeframe = interval_map.get(interval)
        if timeframe is None:
            raise HTTPException(
                status_code=400,
                detail=f"Interval {interval} not supported by Alpaca"
            )

        now = pd.Timestamp.now(tz='UTC')
        end_ts = pd.Timestamp(end, tz='UTC')
        # If end is today or in the future, use now so intraday bars aren't cut off at midnight UTC
        if end_ts.date() >= now.date():
            end_ts = now

        request = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=timeframe,
            start=pd.Timestamp(start, tz='UTC'),
            end=end_ts,
            feed=self._feed,
            adjustment=Adjustment.SPLIT,
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

        # Alpaca always returns extended-hours bars for intraday — filter to RTH when not requested
        if not extended_hours and interval in _INTRADAY_INTERVALS:
            idx = df.index.tz_localize("UTC") if df.index.tz is None else df.index
            et = idx.tz_convert("America/New_York")
            rth = (et.time >= pd.Timestamp("09:30").time()) & (et.time < pd.Timestamp("16:00").time())
            df = df.loc[rth]
            if df.empty:
                raise HTTPException(status_code=404, detail=f"No RTH data for {ticker}")

        return df


# ---------------------------------------------------------------------------
# IBKR data provider
# ---------------------------------------------------------------------------

_IBKR_INTERVAL_MAP = {
    "1m": "1 min", "5m": "5 mins", "15m": "15 mins", "30m": "30 mins",
    "1h": "1 hour", "1d": "1 day", "1wk": "1 week", "1mo": "1 month",
}

_IBKR_UNSUPPORTED = {"2m", "60m", "90m"}


class IBKRDataProvider:
    """DataProvider backed by ib_insync reqHistoricalDataAsync.

    Sync FastAPI route handlers run in AnyIO worker threads where there is no
    running event loop. ib_insync requires the main loop, so we schedule the
    async call onto it via run_coroutine_threadsafe.

    IBKR imposes strict pacing on historical data requests — concurrent calls
    trigger Error 162 ("API historical data query cancelled"). A threading lock
    serializes all requests through a single IB connection.
    """

    def __init__(self, ib, loop):
        self._ib = ib
        self._loop = loop
        self._lock = threading.Lock()

    def fetch(self, ticker: str, start: str, end: str, interval: str, extended_hours: bool = False) -> pd.DataFrame:
        _data_rate_counter.record()
        if interval in _IBKR_UNSUPPORTED:
            raise HTTPException(
                status_code=400,
                detail=f"Interval {interval} not supported by IBKR"
            )

        bar_size = _IBKR_INTERVAL_MAP.get(interval)
        if bar_size is None:
            raise HTTPException(
                status_code=400,
                detail=f"Interval {interval} not supported by IBKR"
            )

        import asyncio
        from ib_insync import Stock
        from datetime import datetime

        contract = Stock(ticker, "SMART", "USD")
        end_dt = datetime.strptime(end, "%Y-%m-%d")

        start_dt = datetime.strptime(start, "%Y-%m-%d")
        days = (end_dt - start_dt).days
        if days <= 1:
            duration = "1 D"
        elif days <= 365:
            duration = f"{days} D"
        else:
            years = max(1, days // 365)
            duration = f"{years} Y"

        if not self._ib.isConnected():
            raise HTTPException(status_code=503, detail="IBKR Gateway not connected")

        with self._lock:
            coro = self._ib.reqHistoricalDataAsync(
                contract,
                endDateTime=end_dt.strftime("%Y%m%d %H:%M:%S"),
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=not extended_hours,
            )
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            try:
                bars = future.result(timeout=30)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"IBKR fetch failed: {e}")

        if not bars:
            raise HTTPException(status_code=404, detail=f"No IBKR data for {ticker}")

        rows = []
        for bar in bars:
            rows.append({
                "Open": bar.open,
                "High": bar.high,
                "Low": bar.low,
                "Close": bar.close,
                "Volume": int(bar.volume),
                "timestamp": bar.date,
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



# Provider registry
_providers: dict[str, DataProvider] = {"yahoo": YahooProvider()}

_alpaca_client = _create_alpaca_client()
if _alpaca_client is not None:
    _providers["alpaca"] = AlpacaProvider(_alpaca_client, feed='sip')
    _providers["alpaca-iex"] = AlpacaProvider(_alpaca_client, feed='iex')


# Register Alpaca as a trading provider
if _alpaca_client is not None:
    _alpaca_api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    _alpaca_secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    if _alpaca_api_key and _alpaca_secret_key:
        try:
            from alpaca.trading.client import TradingClient
            _alpaca_trading_client = TradingClient(_alpaca_api_key, _alpaca_secret_key, paper=True)
            from broker import AlpacaTradingProvider, register_trading_provider
            register_trading_provider("alpaca", AlpacaTradingProvider(_alpaca_trading_client, _alpaca_client))
        except Exception as e:
            logger.warning("Alpaca trading provider registration failed: %s", e)


# ---------------------------------------------------------------------------
# IBKR connection (shared between data + trading providers)
# ---------------------------------------------------------------------------

_ibkr_ib = None  # ib_insync.IB instance, set if Gateway is reachable
_ibkr_loop = None  # asyncio loop ib_insync is bound to (FastAPI's main loop)


async def _create_ibkr_connection():
    """Connect to IBKR Gateway. Returns IB instance or None if unavailable.

    If a prior process crashed without disconnecting, Gateway can still hold
    the previous `clientId` for ~60s — reusing it raises Error 326 and
    connectAsync hangs until timeout. Try a small range of ids so a cold
    start survives an orphaned slot.
    """
    host = os.environ.get("IBKR_HOST", "").strip()
    port = os.environ.get("IBKR_PORT", "").strip()
    if not host and not port:
        return None
    host = host or "127.0.0.1"
    port = int(port or "4002")
    base_client_id = int(os.environ.get("IBKR_CLIENT_ID", "1"))

    import asyncio
    asyncio.set_event_loop(asyncio.get_running_loop())
    from ib_insync import IB

    last_err: Exception | None = None
    for offset in range(8):
        client_id = base_client_id + offset
        ib = IB()
        try:
            await asyncio.wait_for(
                ib.connectAsync(host, port, clientId=client_id),
                timeout=10.0,
            )
            logger.info("IBKR connected to Gateway at %s:%s (clientId=%s)", host, port, client_id)
            return ib
        except Exception as e:
            last_err = e
            try:
                ib.disconnect()
            except Exception:
                pass
            logger.debug("IBKR clientId=%s failed (%s: %s); trying next", client_id, type(e).__name__, e)
    logger.warning("IBKR Gateway not available (%s:%s): %s", host, port, last_err)
    return None


def get_ibkr_connection():
    """Return the shared IB instance (may be None)."""
    return _ibkr_ib


def get_ibkr_loop():
    """Return the asyncio loop ib_insync is bound to (FastAPI's main loop)."""
    return _ibkr_loop


async def init_ibkr():
    """Initialize IBKR connection and register providers. Called from main.py lifespan."""
    global _ibkr_ib, _ibkr_loop
    import asyncio
    _ibkr_loop = asyncio.get_running_loop()
    _ibkr_ib = await _create_ibkr_connection()
    if _ibkr_ib is not None:
        # Register data provider
        _providers["ibkr"] = IBKRDataProvider(_ibkr_ib, _ibkr_loop)
        # Register trading provider
        from broker import IBKRTradingProvider, register_trading_provider
        register_trading_provider("ibkr", IBKRTradingProvider(_ibkr_ib, _ibkr_loop))
        logger.info("IBKR data + trading providers registered")


def register_provider(name: str, provider: DataProvider) -> None:
    _providers[name] = provider


def get_available_providers() -> list[str]:
    return list(_providers.keys())


def require_valid_source(source: str) -> str:
    """F94 boundary check shared across route modules.

    Lowercases the input (provider keys are stored lowercase, so 'YAHOO'
    would otherwise slip past the in-set test and surface as a different
    'Unknown data source' error from `_fetch`) and raises HTTPException(400)
    on unknown providers. Returns the normalized source for callers to use.
    """
    if not isinstance(source, str):
        raise HTTPException(status_code=400, detail="Invalid source")
    normalized = source.strip().lower()
    if normalized not in _providers:
        raise HTTPException(status_code=400, detail="Invalid source")
    return normalized


# ---------------------------------------------------------------------------
# TTL cache for _fetch()
# Historical data (end < today): 1 hour TTL — won't change.
# Live intraday (end >= today):  2 min TTL — data is still moving.
# Live daily+ (end >= today):    5 min TTL — regime direction must not lag > 1 bar.
# ---------------------------------------------------------------------------
_fetch_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}
_fetch_dedup_locks: dict[tuple, threading.Lock] = {}
_fetch_dedup_locks_meta = threading.Lock()  # protects _fetch_dedup_locks dict
_CACHE_MAX = 100
_TTL_HISTORICAL = 3600.0
_TTL_LIVE = 120.0
_TTL_DAILY_LIVE = 300.0


def _fetch_ttl(end: str, interval: str) -> float:
    from datetime import datetime, timezone
    if end >= datetime.now(timezone.utc).date().isoformat():
        if interval in _INTRADAY_INTERVALS:
            return _TTL_LIVE
        return _TTL_DAILY_LIVE
    return _TTL_HISTORICAL


def _evict_cache() -> None:
    """Remove expired entries; if still over limit, drop the oldest.

    Also drops dedup locks for (ticker, interval, source, extended_hours) tuples
    that no longer have any cache entry, bounding _fetch_dedup_locks growth
    over long uptime with many tickers.
    """
    now = time.monotonic()
    expired = [k for k, (ts, _) in _fetch_cache.items() if now - ts > _fetch_ttl(k[2], k[3])]
    for k in expired:
        del _fetch_cache[k]
    while len(_fetch_cache) >= _CACHE_MAX:
        oldest = min(_fetch_cache, key=lambda k: _fetch_cache[k][0])
        del _fetch_cache[oldest]
    in_use = {(k[0], k[3], k[4], k[5]) for k in _fetch_cache}
    with _fetch_dedup_locks_meta:
        stale = [k for k in _fetch_dedup_locks if k not in in_use]
        for k in stale:
            del _fetch_dedup_locks[k]


def _fetch(ticker: str, start: str, end: str, interval: str, source: str = "yahoo", extended_hours: bool = False) -> pd.DataFrame:
    """Fetch OHLCV data from the specified provider, with TTL caching.

    yf.download uses shared global state that corrupts data when called
    concurrently from FastAPI's thread pool — YahooProvider uses yf.Ticker instead.
    """
    provider = _providers.get(source)
    if provider is None:
        raise HTTPException(status_code=400, detail="Unknown data source")

    key = (ticker.upper(), start, end, interval, source, extended_hours)
    now = time.monotonic()
    ttl = _fetch_ttl(end, interval)

    # Serialize concurrent requests for the same (symbol, interval) so that a
    # second caller waiting on the lock hits the cache the first caller populated,
    # instead of firing a duplicate HTTP request.
    dedup_key = (ticker.upper(), interval, source, extended_hours)
    with _fetch_dedup_locks_meta:
        if dedup_key not in _fetch_dedup_locks:
            _fetch_dedup_locks[dedup_key] = threading.Lock()
        _lock = _fetch_dedup_locks[dedup_key]

    with _lock:
        cached = _fetch_cache.get(key)
        if cached and (now - cached[0]) < ttl:
            if logger.isEnabledFor(logging.DEBUG):
                age = round(now - cached[0])
                logger.debug("cache HIT  %s %s %s→%s [%s] age=%ss", ticker, interval, start, end, source, age)
            return cached[1]

        logger.debug("cache MISS %s %s %s→%s [%s]", ticker, interval, start, end, source)
        # Alpaca HTTP keep-alive sockets go stale between bot polls; retry once on
        # RemoteDisconnected / ConnectionAborted so a dead socket doesn't swallow a tick.
        try:
            df = provider.fetch(ticker, start, end, interval, extended_hours=extended_hours)
        except Exception as e:
            if is_retryable_error(e):
                df = provider.fetch(ticker, start, end, interval, extended_hours=extended_hours)
            else:
                raise

        if len(_fetch_cache) >= _CACHE_MAX:
            _evict_cache()
        _fetch_cache[key] = (now, df)
        return df


# Async-level task dedup: multiple concurrent bot coroutines awaiting the same
# (symbol, start, end, interval, source) share one executor Future instead of
# each dispatching an independent run_in_executor call.
_async_ohlcv_futures: dict[tuple, "asyncio.Future[pd.DataFrame]"] = {}


async def fetch_ohlcv_async(
    ticker: str, start: str, end: str, interval: str,
    source: str = "yahoo", extended_hours: bool = False,
) -> pd.DataFrame:
    """Async wrapper around _fetch() with Future-level dedup for concurrent callers.

    Safe under asyncio cooperative scheduling: no await between the dict check
    and the dict store, so no other coroutine can interleave and create a duplicate.
    """
    key = (ticker.upper(), start, end, interval, source, extended_hours)
    existing = _async_ohlcv_futures.get(key)
    if existing is not None and not existing.done():
        return await existing
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[pd.DataFrame] = loop.run_in_executor(
        None, _fetch, ticker, start, end, interval, source, extended_hours
    )
    _async_ohlcv_futures[key] = fut
    try:
        return await fut
    finally:
        _async_ohlcv_futures.pop(key, None)


def cache_info() -> dict:
    """Return current cache stats — exposed via /api/cache for debugging."""
    now = time.monotonic()
    entries = []
    for key, (ts, df) in _fetch_cache.items():
        ticker, start, end, interval, source, *rest = key
        ext = rest[0] if rest else False
        ttl = _fetch_ttl(end, interval)
        entries.append({
            "key": f"{ticker} {interval} {start}→{end} [{source}]{' +ext' if ext else ''}",
            "rows": len(df),
            "age_s": round(now - ts),
            "ttl_s": int(ttl),
            "expires_in_s": max(0, round(ttl - (now - ts))),
        })
    entries.sort(key=lambda e: e["age_s"])
    return {"count": len(entries), "max": _CACHE_MAX, "entries": entries}


def _format_time(idx, interval: str):
    """Return lightweight-charts compatible time: unix seconds for intraday, YYYY-MM-DD for daily+."""
    if interval in _INTRADAY_INTERVALS:
        ts = pd.Timestamp(idx)
        if ts.tzinfo is not None:
            ts = ts.tz_convert('UTC')
        return int(ts.timestamp())
    return str(idx)[:10]


def is_retryable_error(e: Exception) -> bool:
    """Check if an Alpaca API error is a stale connection that should be retried."""
    msg = str(e)
    return ("Connection aborted" in msg
            or "RemoteDisconnected" in msg
            or "ConnectionError" in type(e).__name__)


# ---------------------------------------------------------------------------
# Multi-timeframe (HTF) helpers
# ---------------------------------------------------------------------------

def htf_lookback_days(indicator: str, params: dict) -> int:
    """Return the number of calendar days of HTF data needed to warm up an indicator.

    Formula: int(max_period * 1.5 * 365/252 + 30)
    Converts a trading-day period to calendar days with a 1.5x buffer and 30-day extra.
    """
    if indicator in {"ma", "rsi", "bb", "atr", "vwap"}:
        max_period = params.get("period", 20)
    elif indicator == "macd":
        max_period = max(params.get("fast", 12), params.get("slow", 26), params.get("signal", 9))
    elif indicator in {"stochastic"}:
        max_period = params.get("k_period", 14)
    elif indicator in {"adx"}:
        max_period = params.get("period", 14)
    else:
        max_period = params.get("period", 20)

    return int(max_period * 1.5 * 365 / 252 + 30)


def fetch_higher_tf(ticker: str, start: str, end: str, htf_interval: str, source: str = "yahoo") -> pd.DataFrame:
    """Thin wrapper over _fetch() for higher-timeframe data.

    The caller is responsible for extending `start` by htf_lookback_days()
    to ensure enough warmup data is fetched.
    """
    return _fetch(ticker, start, end, htf_interval, source=source)


def align_htf_to_ltf(htf_series: pd.Series, ltf_index: pd.DatetimeIndex) -> pd.Series:
    """Align HTF values to LTF bar index with strict anti-lookahead.

    Day D's HTF close maps to day D+1's intraday bars (never to day D's own bars).
    This is enforced via shift(1) on the HTF series before merging.

    Both sides are normalized to UTC for cross-timezone comparison.
    yfinance daily bars are already tz-aware (America/New_York), so tz_convert is used,
    not tz_localize.

    NaN is returned for LTF bars before the first HTF bar (warmup period).
    """
    if htf_series.empty:
        return pd.Series([float('nan')] * len(ltf_index), index=ltf_index, dtype=float)

    # Anti-lookahead: shift by 1 so day D's value only maps to day D+1's intraday bars
    shifted = htf_series.shift(1)

    # Normalize both sides to UTC for cross-timezone comparison
    # Use tz_convert (not tz_localize) — yfinance daily is already tz-aware
    htf_idx = shifted.index
    if getattr(htf_idx, 'tz', None) is not None:
        htf_idx = htf_idx.tz_convert('UTC')

    ltf_idx = ltf_index
    if getattr(ltf_idx, 'tz', None) is not None:
        ltf_idx = ltf_idx.tz_convert('UTC')

    htf_df = pd.DataFrame({'ts': htf_idx, 'val': shifted.values})
    htf_df = htf_df.sort_values('ts', ignore_index=True)
    ltf_df = pd.DataFrame({'ts': ltf_idx})

    merged = pd.merge_asof(
        ltf_df,
        htf_df,
        on='ts',
        direction='backward'
    )

    return pd.Series(merged['val'].values, index=ltf_index, dtype=float)

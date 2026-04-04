# Alpaca Data Source Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Alpaca as an alternative market data provider alongside Yahoo Finance, switchable via a sidebar toggle.

**Architecture:** Introduce a `DataProvider` protocol in `shared.py` with `YahooProvider` and `AlpacaProvider` implementations. The existing `_fetch()` function gains a `source` parameter and delegates to the appropriate provider. Routes pass through the `source` param. Frontend adds a data source toggle in the sidebar that sends `source=alpaca|yahoo` on all data requests.

**Tech Stack:** Python (`alpaca-py`, `python-dotenv`), FastAPI, React + TypeScript + TanStack Query

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/shared.py` | Modify | DataProvider protocol, YahooProvider, AlpacaProvider, provider registry, refactored `_fetch()` |
| `backend/routes/data.py` | Modify | Add `source` query param |
| `backend/routes/indicators.py` | Modify | Add `source` query param |
| `backend/routes/backtest.py` | Modify | Add `source` field to `StrategyRequest` |
| `backend/routes/providers.py` | Create | `GET /api/providers` endpoint |
| `backend/main.py` | Modify | Load dotenv, mount providers router, register AlpacaProvider |
| `backend/requirements.txt` | Modify | Add `alpaca-py`, `python-dotenv` |
| `backend/tests/test_providers.py` | Create | Tests for provider abstraction |
| `frontend/src/shared/types/index.ts` | Modify | Add `DataSource` type |
| `frontend/src/shared/hooks/useOHLCV.ts` | Modify | Add `source` param to hooks, add `useProviders` |
| `frontend/src/features/sidebar/Sidebar.tsx` | Modify | Data source toggle UI |
| `frontend/src/App.tsx` | Modify | `dataSource` state, wire through hooks and components |
| `frontend/src/features/strategy/StrategyBuilder.tsx` | Modify | Pass `source` in backtest POST |
| `.env.example` | Create | Placeholder Alpaca credentials |

---

### Task 1: Dependencies and Configuration

**Files:**
- Modify: `backend/requirements.txt`
- Create: `.env.example`
- Modify: `backend/main.py`

- [ ] **Step 1: Add dependencies to requirements.txt**

Add `alpaca-py` and `python-dotenv` to `backend/requirements.txt`:

```
fastapi
uvicorn[standard]
yfinance
pandas
pandas-ta
numpy
python-multipart
httpx
pytest
alpaca-py
python-dotenv
```

- [ ] **Step 2: Install dependencies**

Run:
```bash
cd /home/john/test-claude-project/backend && source venv/bin/activate && pip install alpaca-py python-dotenv
```

Expected: Successfully installed packages, no errors.

- [ ] **Step 3: Create `.env.example`**

Create `/home/john/test-claude-project/.env.example`:

```
# Alpaca Market Data API credentials
# Sign up at https://alpaca.markets to get your keys
# Leave blank or remove to disable Alpaca data source
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
```

- [ ] **Step 4: Add dotenv loading to `main.py`**

Add at the very top of `backend/main.py`, before all other imports:

```python
from dotenv import load_dotenv
load_dotenv()
```

The full file becomes:

```python
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import warnings
warnings.filterwarnings("ignore")

from routes.data import router as data_router
from routes.indicators import router as indicators_router
from routes.backtest import router as backtest_router
from routes.search import router as search_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(data_router)
app.include_router(indicators_router)
app.include_router(backtest_router)
app.include_router(search_router)
```

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt .env.example backend/main.py
git commit -m "feat: add alpaca-py and python-dotenv dependencies, .env.example"
```

---

### Task 2: DataProvider Protocol and YahooProvider

**Files:**
- Modify: `backend/shared.py`
- Create: `backend/tests/test_providers.py`

- [ ] **Step 1: Write failing test for YahooProvider**

Create `backend/tests/test_providers.py`:

```python
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pandas as pd


def test_yahoo_provider_returns_dataframe(monkeypatch):
    """YahooProvider.fetch() returns a DataFrame with the expected columns."""
    fake_df = pd.DataFrame({
        "Open": [100.0], "High": [105.0], "Low": [99.0],
        "Close": [103.0], "Volume": [1000000],
    }, index=pd.to_datetime(["2024-01-02"]))

    import yfinance as yf
    class FakeTicker:
        def history(self, **kwargs):
            return fake_df
    monkeypatch.setattr(yf, "Ticker", lambda symbol: FakeTicker())

    from shared import YahooProvider
    provider = YahooProvider()
    result = provider.fetch("AAPL", "2024-01-01", "2024-01-03", "1d")

    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(result) == 1
    assert result["Close"].iloc[0] == 103.0


def test_yahoo_provider_raises_on_empty(monkeypatch):
    """YahooProvider.fetch() raises HTTPException when no data returned."""
    import yfinance as yf
    class FakeTicker:
        def history(self, **kwargs):
            return pd.DataFrame()
    monkeypatch.setattr(yf, "Ticker", lambda symbol: FakeTicker())

    from shared import YahooProvider
    from fastapi import HTTPException
    import pytest

    provider = YahooProvider()
    with pytest.raises(HTTPException) as exc_info:
        provider.fetch("FAKE", "2024-01-01", "2024-01-03", "1d")
    assert exc_info.value.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/john/test-claude-project/backend && source venv/bin/activate && python -m pytest tests/test_providers.py -v
```

Expected: FAIL — `ImportError: cannot import name 'YahooProvider' from 'shared'`

- [ ] **Step 3: Refactor `shared.py` — extract DataProvider protocol and YahooProvider**

Replace the entire content of `backend/shared.py` with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/john/test-claude-project/backend && source venv/bin/activate && python -m pytest tests/test_providers.py tests/test_models.py -v
```

Expected: All tests PASS — both the new provider tests and the existing model tests.

- [ ] **Step 5: Commit**

```bash
git add backend/shared.py backend/tests/test_providers.py
git commit -m "feat: extract DataProvider protocol and YahooProvider from _fetch()"
```

---

### Task 3: AlpacaProvider

**Files:**
- Modify: `backend/shared.py`
- Modify: `backend/tests/test_providers.py`

- [ ] **Step 1: Write failing tests for AlpacaProvider**

Append to `backend/tests/test_providers.py`:

```python
def test_alpaca_provider_returns_dataframe(monkeypatch):
    """AlpacaProvider.fetch() returns a DataFrame with the expected columns."""
    fake_bars = {
        "AAPL": [
            type("Bar", (), {
                "timestamp": pd.Timestamp("2024-01-02", tz="UTC"),
                "open": 100.0, "high": 105.0, "low": 99.0,
                "close": 103.0, "volume": 1000000,
            })()
        ]
    }

    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")

    import shared
    # Mock the Alpaca client
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def get_stock_bars(self, request):
            return fake_bars

    monkeypatch.setattr(shared, "_create_alpaca_client", lambda: FakeClient())

    from shared import AlpacaProvider
    provider = AlpacaProvider(FakeClient())
    result = provider.fetch("AAPL", "2024-01-01", "2024-01-03", "1d")

    assert isinstance(result, pd.DataFrame)
    assert set(result.columns) == {"Open", "High", "Low", "Close", "Volume"}
    assert len(result) == 1
    assert result["Close"].iloc[0] == 103.0


def test_alpaca_provider_raises_on_empty(monkeypatch):
    """AlpacaProvider.fetch() raises HTTPException when no data returned."""
    class FakeClient:
        def get_stock_bars(self, request):
            return {"AAPL": []}

    from shared import AlpacaProvider
    from fastapi import HTTPException
    import pytest

    provider = AlpacaProvider(FakeClient())
    with pytest.raises(HTTPException) as exc_info:
        provider.fetch("FAKE", "2024-01-01", "2024-01-03", "1d")
    assert exc_info.value.status_code == 404


def test_alpaca_provider_rejects_unsupported_interval():
    """AlpacaProvider.fetch() raises 400 for intervals Alpaca doesn't support."""
    class FakeClient:
        pass

    from shared import AlpacaProvider
    from fastapi import HTTPException
    import pytest

    provider = AlpacaProvider(FakeClient())
    with pytest.raises(HTTPException) as exc_info:
        provider.fetch("AAPL", "2024-01-01", "2024-01-03", "2m")
    assert exc_info.value.status_code == 400
    assert "not supported by Alpaca" in exc_info.value.detail


def test_fetch_rejects_unknown_source():
    """_fetch() raises 400 for an unknown source name."""
    from shared import _fetch
    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as exc_info:
        _fetch("AAPL", "2024-01-01", "2024-01-03", "1d", source="nonexistent")
    assert exc_info.value.status_code == 400
    assert "Unknown data source" in exc_info.value.detail
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/john/test-claude-project/backend && source venv/bin/activate && python -m pytest tests/test_providers.py -v
```

Expected: FAIL — `ImportError: cannot import name 'AlpacaProvider' from 'shared'`

- [ ] **Step 3: Implement AlpacaProvider in `shared.py`**

Add the following after the `YahooProvider` class in `backend/shared.py` (before the `# Provider registry` comment):

```python
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
        bar_list = bars.get(ticker, [])
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
```

Then update the provider registry section to:

```python
# Provider registry
_providers: dict[str, DataProvider] = {"yahoo": YahooProvider()}

_alpaca_client = _create_alpaca_client()
if _alpaca_client is not None:
    _providers["alpaca"] = AlpacaProvider(_alpaca_client)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/john/test-claude-project/backend && source venv/bin/activate && python -m pytest tests/test_providers.py tests/test_models.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/shared.py backend/tests/test_providers.py
git commit -m "feat: add AlpacaProvider with interval mapping and error handling"
```

---

### Task 4: Providers Endpoint and Route Changes

**Files:**
- Create: `backend/routes/providers.py`
- Modify: `backend/main.py`
- Modify: `backend/routes/data.py`
- Modify: `backend/routes/indicators.py`
- Modify: `backend/routes/backtest.py`

- [ ] **Step 1: Write failing test for `/api/providers` endpoint**

Append to `backend/tests/test_providers.py`:

```python
from fastapi.testclient import TestClient


def test_providers_endpoint_includes_yahoo():
    """GET /api/providers always includes yahoo."""
    from main import app
    client = TestClient(app)
    resp = client.get("/api/providers")
    assert resp.status_code == 200
    assert "yahoo" in resp.json()["providers"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /home/john/test-claude-project/backend && source venv/bin/activate && python -m pytest tests/test_providers.py::test_providers_endpoint_includes_yahoo -v
```

Expected: FAIL — 404 (route does not exist yet).

- [ ] **Step 3: Create `backend/routes/providers.py`**

```python
from fastapi import APIRouter
from shared import get_available_providers

router = APIRouter()


@router.get("/api/providers")
def list_providers():
    return {"providers": get_available_providers()}
```

- [ ] **Step 4: Mount providers router in `main.py`**

Add to `backend/main.py` after the existing router imports:

```python
from routes.providers import router as providers_router
```

And add after the existing `app.include_router` calls:

```python
app.include_router(providers_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run:
```bash
cd /home/john/test-claude-project/backend && source venv/bin/activate && python -m pytest tests/test_providers.py::test_providers_endpoint_includes_yahoo -v
```

Expected: PASS.

- [ ] **Step 6: Add `source` param to route files**

**`backend/routes/data.py`** — full updated file:

```python
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
```

**`backend/routes/indicators.py`** — change only the function signature on line 17 and the `_fetch` call on line 25:

Line 17 becomes:
```python
def get_indicators(
    ticker: str,
    start: str = "2023-01-01",
    end: str = "2024-01-01",
    interval: str = "1d",
    indicators: str = "macd,rsi",
    source: str = "yahoo",
):
```

Line 25 becomes:
```python
        df = _fetch(ticker, start, end, interval, source=source)
```

**`backend/routes/backtest.py`** — add `source` field to `StrategyRequest` and pass it to `_fetch`:

Add to the `StrategyRequest` class (after `position_size`):
```python
    source: str = "yahoo"
```

Change line 38 to:
```python
        df = _fetch(req.ticker, req.start, req.end, req.interval, source=req.source)
```

- [ ] **Step 7: Run all backend tests**

Run:
```bash
cd /home/john/test-claude-project/backend && source venv/bin/activate && python -m pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/routes/providers.py backend/main.py backend/routes/data.py backend/routes/indicators.py backend/routes/backtest.py
git commit -m "feat: add /api/providers endpoint and source param to all data routes"
```

---

### Task 5: Frontend — Types and Hooks

**Files:**
- Modify: `frontend/src/shared/types/index.ts`
- Modify: `frontend/src/shared/hooks/useOHLCV.ts`

- [ ] **Step 1: Add `DataSource` type**

Add at the end of `frontend/src/shared/types/index.ts` (before the closing of file, after `AppState`):

```typescript
export type DataSource = 'yahoo' | 'alpaca'
```

- [ ] **Step 2: Update hooks to accept `source` parameter**

Replace the full content of `frontend/src/shared/hooks/useOHLCV.ts`:

```typescript
import { useQuery } from '@tanstack/react-query'
import axios from 'axios'
import type { OHLCVBar, DataSource } from '../types'

const API = 'http://localhost:8000'

export function useOHLCV(ticker: string, start: string, end: string, interval: string, source: DataSource = 'yahoo') {
  return useQuery<OHLCVBar[]>({
    queryKey: ['ohlcv', ticker, start, end, interval, source],
    queryFn: async () => {
      const { data } = await axios.get(`${API}/api/ohlcv/${ticker}`, { params: { start, end, interval, source } })
      return data.data
    },
    enabled: !!ticker,
    staleTime: 5 * 60 * 1000,
  })
}

export function useIndicators(ticker: string, start: string, end: string, interval: string, indicators: string[], source: DataSource = 'yahoo') {
  return useQuery({
    queryKey: ['indicators', ticker, start, end, interval, indicators.join(','), source],
    queryFn: async () => {
      const { data } = await axios.get(`${API}/api/indicators/${ticker}`, {
        params: { start, end, interval, indicators: indicators.join(','), source }
      })
      return data
    },
    enabled: !!ticker && indicators.length > 0,
    staleTime: 5 * 60 * 1000,
  })
}

export function useProviders() {
  return useQuery<string[]>({
    queryKey: ['providers'],
    queryFn: async () => {
      const { data } = await axios.get(`${API}/api/providers`)
      return data.providers
    },
    staleTime: 60 * 1000,
  })
}

export function useSearch(q: string) {
  return useQuery({
    queryKey: ['search', q],
    queryFn: async () => {
      const { data } = await axios.get(`${API}/api/search`, { params: { q } })
      return data
    },
    enabled: q.length > 1,
    staleTime: 60 * 1000,
  })
}
```

- [ ] **Step 3: Commit**

```bash
cd /home/john/test-claude-project && git add frontend/src/shared/types/index.ts frontend/src/shared/hooks/useOHLCV.ts
git commit -m "feat: add DataSource type, source param to hooks, useProviders hook"
```

---

### Task 6: Frontend — App State and Sidebar Toggle

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/features/sidebar/Sidebar.tsx`
- Modify: `frontend/src/features/strategy/StrategyBuilder.tsx`

- [ ] **Step 1: Add `dataSource` state to `App.tsx`**

In `App.tsx`, add the import for `DataSource`:

Change line 2 from:
```typescript
import type { BacktestResult, IndicatorKey } from './shared/types'
```
to:
```typescript
import type { BacktestResult, IndicatorKey, DataSource } from './shared/types'
```

Add state after the `showQqq` state (line 29):
```typescript
  const [dataSource, setDataSource] = useState<DataSource>((saved?.dataSource as DataSource) ?? 'yahoo')
```

Update the `useEffect` that persists settings — change the `JSON.stringify` call to include `dataSource`:
```typescript
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      ticker, start, end, interval, activeIndicators, showSpy, showQqq, dataSource,
    }))
  }, [ticker, start, end, interval, activeIndicators, showSpy, showQqq, dataSource])
```

Update the hook calls to pass `dataSource`:
```typescript
  const { data: ohlcv = [] } = useOHLCV(ticker, start, end, interval, dataSource)
  const { data: spyData } = useOHLCV('SPY', start, end, interval, dataSource)
  const { data: qqqData } = useOHLCV('QQQ', start, end, interval, dataSource)
```

```typescript
  const { data: indicatorData = {} } = useIndicators(ticker, start, end, interval, indicatorKeys, dataSource)
```

Pass `dataSource` and `onDataSourceChange` to `Sidebar`:
```typescript
        <Sidebar
          ticker={ticker}
          start={start}
          end={end}
          interval={interval}
          activeIndicators={activeIndicators}
          showSpy={showSpy}
          showQqq={showQqq}
          dataSource={dataSource}
          onTickerChange={t => { setTicker(t); setBacktestResult(null) }}
          onStartChange={setStart}
          onEndChange={setEnd}
          onIntervalChange={setInterval}
          onToggleIndicator={toggleIndicator}
          onToggleSpy={() => setShowSpy(v => !v)}
          onToggleQqq={() => setShowQqq(v => !v)}
          onDataSourceChange={setDataSource}
        />
```

Pass `dataSource` to `StrategyBuilder`:
```typescript
          <StrategyBuilder
            ticker={ticker}
            start={start}
            end={end}
            interval={interval}
            dataSource={dataSource}
            onResult={setBacktestResult}
          />
```

- [ ] **Step 2: Add data source toggle to `Sidebar.tsx`**

Add imports at line 3:
```typescript
import { useProviders } from '../../shared/hooks/useOHLCV'
import type { IndicatorKey, DataSource } from '../../shared/types'
```

(Remove the existing `import type { IndicatorKey } from '../../shared/types'` line.)

Update the `SidebarProps` interface to add:
```typescript
  dataSource: DataSource
  onDataSourceChange: (s: DataSource) => void
```

Update the destructured props to include `dataSource, onDataSourceChange`.

Inside the component, add the providers query:
```typescript
  const { data: providers = ['yahoo'] } = useProviders()
```

Add a new section in the JSX, right after the closing `</div>` of the Ticker section (after line 91) and before the Date Range section:

```tsx
      <div style={styles.section}>
        <div style={styles.sectionTitle}>Data Source</div>
        <div style={{ display: 'flex', borderRadius: 6, overflow: 'hidden', border: '1px solid #30363d' }}>
          {(['yahoo', 'alpaca'] as const).map(src => {
            const available = providers.includes(src)
            const active = dataSource === src
            return (
              <button
                key={src}
                onClick={() => available && onDataSourceChange(src)}
                disabled={!available}
                title={!available ? 'Set ALPACA_API_KEY in .env to enable' : undefined}
                style={{
                  flex: 1,
                  padding: '5px 0',
                  fontSize: 12,
                  fontWeight: 600,
                  background: active ? '#58a6ff' : '#0d1117',
                  color: active ? '#000' : available ? '#e6edf3' : '#484f58',
                  border: 'none',
                  cursor: available ? 'pointer' : 'not-allowed',
                  opacity: available ? 1 : 0.5,
                }}
              >
                {src.charAt(0).toUpperCase() + src.slice(1)}
              </button>
            )
          })}
        </div>
      </div>
```

The full updated function signature:

```typescript
export default function Sidebar({
  ticker, start, end, interval, activeIndicators, showSpy, showQqq,
  dataSource, onTickerChange, onStartChange, onEndChange, onIntervalChange,
  onToggleIndicator, onToggleSpy, onToggleQqq, onDataSourceChange,
}: SidebarProps) {
```

- [ ] **Step 3: Update `StrategyBuilder.tsx` to pass `source`**

Update the `Props` interface and imports:

```typescript
import type { Rule, StrategyRequest, BacktestResult, DataSource } from '../../shared/types'
```

```typescript
interface Props {
  ticker: string
  start: string
  end: string
  interval: string
  dataSource: DataSource
  onResult: (r: BacktestResult | null) => void
}
```

Update the destructured props:
```typescript
export default function StrategyBuilder({ ticker, start, end, interval, dataSource, onResult }: Props) {
```

Update the `StrategyRequest` type in `frontend/src/shared/types/index.ts` to include `source`:
```typescript
export interface StrategyRequest {
  ticker: string
  start: string
  end: string
  interval: string
  buy_rules: Rule[]
  sell_rules: Rule[]
  buy_logic: 'AND' | 'OR'
  sell_logic: 'AND' | 'OR'
  initial_capital: number
  position_size: number
  source: DataSource
}
```

Update the request construction in `StrategyBuilder.tsx` `runBacktest()` to include `source`:
```typescript
      const req: StrategyRequest = {
        ticker, start, end, interval,
        buy_rules: buyRules, sell_rules: sellRules,
        buy_logic: buyLogic, sell_logic: sellLogic,
        initial_capital: capital, position_size: posSize / 100,
        source: dataSource,
      }
```

- [ ] **Step 4: Verify the frontend compiles**

Run:
```bash
cd /home/john/test-claude-project/frontend && npx tsc --noEmit
```

Expected: No type errors.

- [ ] **Step 5: Commit**

```bash
cd /home/john/test-claude-project && git add frontend/src/App.tsx frontend/src/features/sidebar/Sidebar.tsx frontend/src/features/strategy/StrategyBuilder.tsx frontend/src/shared/types/index.ts
git commit -m "feat: add data source toggle to sidebar, wire source through App and StrategyBuilder"
```

---

### Task 7: Validate Source Fallback and Final Integration Test

**Files:**
- Modify: `backend/tests/test_providers.py`

- [ ] **Step 1: Write test for source fallback via `/api/ohlcv`**

Append to `backend/tests/test_providers.py`:

```python
def test_ohlcv_rejects_unknown_source():
    """GET /api/ohlcv returns 400 for unknown source."""
    from main import app
    client = TestClient(app)
    resp = client.get("/api/ohlcv/AAPL", params={"source": "nonexistent"})
    assert resp.status_code == 400
    assert "Unknown data source" in resp.json()["detail"]


def test_ohlcv_defaults_to_yahoo(monkeypatch):
    """GET /api/ohlcv without source param uses yahoo (no error)."""
    import pandas as pd
    import yfinance as yf

    fake_df = pd.DataFrame({
        "Open": [100.0], "High": [105.0], "Low": [99.0],
        "Close": [103.0], "Volume": [1000000],
    }, index=pd.to_datetime(["2024-01-02"]))

    class FakeTicker:
        def history(self, **kwargs):
            return fake_df
    monkeypatch.setattr(yf, "Ticker", lambda symbol: FakeTicker())

    from main import app
    client = TestClient(app)
    resp = client.get("/api/ohlcv/AAPL", params={"start": "2024-01-01", "end": "2024-01-03"})
    assert resp.status_code == 200
    assert resp.json()["ticker"] == "AAPL"
    assert len(resp.json()["data"]) == 1
```

- [ ] **Step 2: Run all tests**

Run:
```bash
cd /home/john/test-claude-project/backend && source venv/bin/activate && python -m pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_providers.py
git commit -m "test: add integration tests for source param on ohlcv endpoint"
```

---

### Task 8: Manual Smoke Test

- [ ] **Step 1: Start the app**

Run:
```bash
cd /home/john/test-claude-project && ./start.sh
```

- [ ] **Step 2: Verify Yahoo still works**

Open `http://localhost:5173`. Confirm:
- Chart loads with AAPL (or last saved ticker)
- Data Source toggle shows "Yahoo" selected, "Alpaca" disabled/grayed
- Switching intervals, changing tickers, running backtest all work as before

- [ ] **Step 3: Verify `/api/providers` endpoint**

Run:
```bash
curl http://localhost:8000/api/providers
```

Expected: `{"providers":["yahoo"]}` (no Alpaca keys configured).

- [ ] **Step 4: (Optional) Test with Alpaca keys**

If you have Alpaca credentials, create `.env` in the project root:
```
ALPACA_API_KEY=your_real_key
ALPACA_SECRET_KEY=your_real_secret
```

Restart the backend. Verify:
- `curl http://localhost:8000/api/providers` returns `{"providers":["yahoo","alpaca"]}`
- The Alpaca button in the sidebar becomes enabled
- Switching to Alpaca loads data
- Switching back to Yahoo works

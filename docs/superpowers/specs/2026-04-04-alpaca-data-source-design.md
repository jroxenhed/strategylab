# Alpaca Data Source Integration

**Date:** 2026-04-04
**Status:** Approved

## Overview

Add Alpaca as an alternative market data source alongside Yahoo Finance. Users switch between providers via a sidebar toggle. All charts, indicators, and backtests use the selected source. Ticker search always uses Yahoo.

## Motivation

Yahoo Finance (yfinance) is free and convenient but unofficial — it has rate limits, occasional breakage, and no SLA. Alpaca provides a proper API with reliable data, real-time quotes (paid tier), and official support. Supporting both gives the user flexibility and a fallback.

## Backend

### Data Provider Abstraction (`shared.py`)

Replace the monolithic `_fetch()` with a provider pattern:

```python
class DataProvider(Protocol):
    def fetch(self, ticker: str, start: str, end: str, interval: str) -> pd.DataFrame:
        """Return DataFrame with columns: Open, High, Low, Close, Volume and DatetimeIndex."""
        ...
```

Two implementations:

**`YahooProvider`** — wraps existing `yf.Ticker().history()` logic, including intraday date clamping (`_INTERVAL_MAX_DAYS`).

**`AlpacaProvider`** — uses `alpaca-py` (`StockHistoricalDataClient`):
- Maps interval strings to Alpaca `TimeFrame` objects:
  - `1m` → `TimeFrame.Minute`, `5m` → `TimeFrame(5, TimeFrameUnit.Minute)`, etc.
  - `1d` → `TimeFrame.Day`, `1wk` → `TimeFrame.Week`, `1mo` → `TimeFrame.Month`
- Intervals not supported by Alpaca (`2m`, `90m`) return a 400 error with a clear message.
- Converts Alpaca `BarSet` response to the standard DataFrame format (rename columns to match: Open, High, Low, Close, Volume).
- Credentials loaded from environment variables: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`.

**`_fetch()` signature change:**

```python
def _fetch(ticker: str, start: str, end: str, interval: str, source: str = "yahoo") -> pd.DataFrame:
```

Selects provider based on `source` param. Default `"yahoo"` preserves all existing behavior.

### Route Changes

All three data-consuming routes gain a `source` query/body parameter:

- **`GET /api/ohlcv/{ticker}`** — adds `source: str = "yahoo"` query param, passes to `_fetch()`.
- **`GET /api/indicators/{ticker}`** — same: adds `source` query param, passes to `_fetch()`.
- **`POST /api/backtest`** — adds `source: str = "yahoo"` field to `StrategyRequest` model, passes to `_fetch()`.

Each route change is a one-line addition to the function signature and one-line change to the `_fetch()` call.

### New Endpoint: `GET /api/providers`

Returns available data providers based on configured credentials:

```json
{"providers": ["yahoo", "alpaca"]}
```

If `ALPACA_API_KEY` is not set, returns only `["yahoo"]`. Lives in a new `routes/providers.py` for consistency with the existing route structure.

### Provider Availability

At startup, `main.py` checks for Alpaca env vars. If present, instantiates `AlpacaProvider` and registers it. The provider registry is a simple dict in `shared.py`:

```python
_providers: dict[str, DataProvider] = {"yahoo": YahooProvider()}
```

Alpaca is added to this dict only if credentials are present.

## Frontend

### Sidebar Toggle (`Sidebar.tsx`)

- Segmented control or dropdown labeled "Data Source" with options: **Yahoo**, **Alpaca**.
- On mount, fetch `GET /api/providers` to determine available providers.
- If Alpaca is not available, its option is disabled with a tooltip: "Set ALPACA_API_KEY in .env to enable".
- Selected source persisted to `localStorage` (key: `dataSource`, default: `"yahoo"`).
- If a persisted source is no longer available (e.g., env vars removed), fall back to `"yahoo"`.

### State & Data Flow (`App.tsx`)

- New state: `dataSource` (string, `"yahoo"` | `"alpaca"`), initialized from localStorage.
- Passed to `useOHLCV`, `useIndicators` hooks and included in backtest POST body.
- Passed to `Sidebar.tsx` as current value + setter.

### Hook Changes (`useOHLCV.ts`)

- `useOHLCV` and `useIndicators` gain a `source` parameter.
- Appended as `&source={source}` query param to API calls.
- Added to `queryKey` arrays — switching source auto-refetches via React Query cache invalidation.
- `useSearch` unchanged — always hits Yahoo.

### New Hook: `useProviders`

```typescript
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
```

### Chart

No changes. Chart.tsx consumes the same OHLCV and indicator data shape regardless of source.

## Configuration

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ALPACA_API_KEY` | No | Alpaca API key ID |
| `ALPACA_SECRET_KEY` | No | Alpaca API secret key |

### Files

- **`.env.example`** — added to project root with placeholder values.
- **`.env`** — added to `.gitignore`.

### Dependencies

- `alpaca-py` — added to `requirements.txt`.
- `python-dotenv` — added to `requirements.txt` (if not already present).

## Error Handling

| Scenario | Behavior |
|---|---|
| Ticker not found on Alpaca | 404: "No data for {ticker}" (same as Yahoo) |
| Unsupported interval for Alpaca | 400: "Interval {interval} not supported by Alpaca" |
| Invalid/missing Alpaca credentials | Alpaca omitted from `/api/providers`, toggle disabled |
| Alpaca API timeout/rate limit | 502: "Alpaca API error: {detail}" |

## Scope Boundaries

**In scope:**
- Provider abstraction in `shared.py`
- `source` param on data/indicators/backtest routes
- `/api/providers` endpoint
- Sidebar data source toggle
- `.env` support for Alpaca credentials

**Out of scope:**
- Real-time/streaming data
- Alpaca trading/order placement
- Alpaca-based ticker search (Yahoo search used always)
- Paid tier features (SIP feed detection, etc.)
- Additional providers beyond Yahoo and Alpaca

## Files Changed

| File | Change |
|---|---|
| `backend/shared.py` | Provider protocol, YahooProvider, AlpacaProvider, refactored `_fetch()` |
| `backend/routes/data.py` | Add `source` param |
| `backend/routes/indicators.py` | Add `source` param |
| `backend/routes/backtest.py` | Add `source` field to `StrategyRequest` |
| `backend/routes/providers.py` | New — `/api/providers` endpoint |
| `backend/main.py` | Mount providers router, load dotenv |
| `backend/requirements.txt` | Add `alpaca-py`, `python-dotenv` |
| `frontend/src/shared/hooks/useOHLCV.ts` | Add `source` param to hooks, add `useProviders` |
| `frontend/src/features/sidebar/Sidebar.tsx` | Data source toggle |
| `frontend/src/App.tsx` | `dataSource` state, pass to hooks/components |
| `frontend/src/shared/types/index.ts` | Add `DataSource` type if needed |
| `.env.example` | New — placeholder Alpaca credentials |
| `.gitignore` | Ensure `.env` is listed |

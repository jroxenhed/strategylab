# Equity Curve Macro Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a zoomed-out equity curve view that aggregates raw per-bar equity data into daily/weekly/monthly/quarterly/yearly buckets with a baseline-colored close line, stepped high/low band, trade density ticks, and period-level summary stats.

**Architecture:** Backend caches the most recent backtest result (single-entry, keyed by request hash). A separate macro endpoint re-aggregates the cached data into the requested bucket size using pandas groupby. Frontend adds a bucket selector to the Results tab bar that switches between "Detail" (current behavior) and macro mode, with a dedicated chart component for macro rendering.

**Tech Stack:** Python (FastAPI, pandas, hashlib), TypeScript/React (lightweight-charts v5 BaselineSeries/LineSeries/HistogramSeries, TanStack Query)

**Spec:** `docs/superpowers/specs/2026-04-12-equity-curve-macro-mode-design.md`

---

## File Structure

### Backend

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `backend/routes/backtest.py` | Add `_request_hash()`, `_backtest_cache` dict, populate cache at end of `run_backtest()` |
| Create | `backend/routes/backtest_macro.py` | `aggregate_macro()` pure function + `POST /api/backtest/macro` endpoint |
| Modify | `backend/main.py` | Import + register macro router |
| Create | `backend/tests/test_backtest_macro.py` | Unit tests for `aggregate_macro()` |

### Frontend

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `frontend/src/shared/types/index.ts` | Add `MacroCurvePoint`, `PeriodStats`, `MacroResponse` interfaces |
| Create | `frontend/src/shared/hooks/useMacro.ts` | `useMacro()` React Query hook for macro endpoint |
| Modify | `frontend/src/App.tsx` | Lift `resultsTab` + `macroBucket` state above Results; store `lastRequest` |
| Modify | `frontend/src/features/strategy/StrategyBuilder.tsx` | Pass built `StrategyRequest` up alongside result |
| Modify | `frontend/src/features/strategy/Results.tsx` | Bucket selector in tab bar, period stats section, detail trade ticks, conditional macro/detail chart |
| Create | `frontend/src/features/strategy/MacroEquityChart.tsx` | Macro mode chart: baseline close line, stepped high/low lines, trade density ticks, tooltip |

---

## Task 1: Backend — Aggregation function (TDD)

**Files:**
- Create: `backend/routes/backtest_macro.py`
- Create: `backend/tests/test_backtest_macro.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_backtest_macro.py`:

```python
from routes.backtest_macro import aggregate_macro


def test_aggregate_weekly_basic():
    """Weekly buckets from 2 weeks of daily equity data."""
    equity = [
        {"time": "2024-01-02", "value": 10000.0},
        {"time": "2024-01-03", "value": 10100.0},
        {"time": "2024-01-04", "value": 10050.0},
        {"time": "2024-01-05", "value": 10200.0},
        {"time": "2024-01-08", "value": 10300.0},
        {"time": "2024-01-09", "value": 10250.0},
        {"time": "2024-01-10", "value": 10400.0},
        {"time": "2024-01-11", "value": 10350.0},
        {"time": "2024-01-12", "value": 10500.0},
    ]
    trades = [
        {"type": "sell", "date": "2024-01-04", "pnl": 50.0},
        {"type": "buy", "date": "2024-01-03", "price": 100.0},
        {"type": "sell", "date": "2024-01-10", "pnl": -25.0},
    ]
    result = aggregate_macro(equity, trades, "W", 10000.0)

    assert result["bucket"] == "W"
    curve = result["macro_curve"]
    assert len(curve) == 2  # 2 calendar weeks

    # Week 1 (Jan 2-5): open=10000, high=10200, low=10000, close=10200
    w1 = curve[0]
    assert w1["open"] == 10000.0
    assert w1["high"] == 10200.0
    assert w1["low"] == 10000.0
    assert w1["close"] == 10200.0
    assert len(w1["trades"]) == 1  # only the sell on Jan 4
    assert w1["trades"][0]["pnl"] == 50.0

    # Week 2 (Jan 8-12): open=10300, high=10500, low=10250, close=10500
    w2 = curve[1]
    assert w2["open"] == 10300.0
    assert w2["high"] == 10500.0
    assert w2["low"] == 10250.0
    assert w2["close"] == 10500.0
    assert len(w2["trades"]) == 1
    assert w2["trades"][0]["pnl"] == -25.0


def test_aggregate_weekly_drawdown():
    """Drawdown tracks running peak across buckets."""
    equity = [
        {"time": "2024-01-02", "value": 10000.0},
        {"time": "2024-01-03", "value": 10500.0},  # new peak
        {"time": "2024-01-08", "value": 10200.0},  # below peak
        {"time": "2024-01-09", "value": 10100.0},  # deeper below peak
    ]
    result = aggregate_macro(equity, [], "W", 10000.0)
    curve = result["macro_curve"]

    # Week 1: running_peak = max(10000, 10500) = 10500
    # dd = (10000 - 10500) / 10500 * 100 = -4.76
    assert curve[0]["drawdown_pct"] == round((10000 - 10500) / 10500 * 100, 2)

    # Week 2: running_peak stays 10500 (high=10200 < 10500)
    # dd = (10100 - 10500) / 10500 * 100 = -3.81
    assert curve[1]["drawdown_pct"] == round((10100 - 10500) / 10500 * 100, 2)


def test_aggregate_period_stats():
    """Period stats computed from bucket returns."""
    equity = [
        {"time": "2024-01-02", "value": 10000.0},
        {"time": "2024-01-05", "value": 10200.0},  # W1: +2%
        {"time": "2024-01-08", "value": 10200.0},
        {"time": "2024-01-12", "value": 10000.0},  # W2: -1.96%
        {"time": "2024-01-15", "value": 10000.0},
        {"time": "2024-01-19", "value": 10300.0},  # W3: +3%
    ]
    trades = [
        {"type": "sell", "date": "2024-01-04", "pnl": 50.0},
        {"type": "sell", "date": "2024-01-10", "pnl": -20.0},
    ]
    result = aggregate_macro(equity, trades, "W", 10000.0)
    ps = result["period_stats"]

    assert ps["label"] == "Weekly"
    assert ps["winning_pct"] == round(2 / 3 * 100, 1)  # 2 of 3 weeks positive
    assert ps["best_return_pct"] == round((10300 - 10000) / 10000 * 100, 2)
    assert ps["worst_return_pct"] == round((10000 - 10200) / 10200 * 100, 2)


def test_aggregate_monthly():
    """Monthly buckets group correctly."""
    equity = [
        {"time": "2024-01-15", "value": 10000.0},
        {"time": "2024-01-31", "value": 10500.0},
        {"time": "2024-02-15", "value": 10300.0},
        {"time": "2024-02-28", "value": 10800.0},
    ]
    result = aggregate_macro(equity, [], "M", 10000.0)
    assert len(result["macro_curve"]) == 2
    assert result["period_stats"]["label"] == "Monthly"


def test_aggregate_intraday_timestamps():
    """Unix timestamps (intraday data) are handled correctly."""
    # 1704200400 = 2024-01-02 13:00 UTC, 1704204000 = 14:00, etc.
    equity = [
        {"time": 1704200400, "value": 10000.0},
        {"time": 1704204000, "value": 10050.0},
        {"time": 1704207600, "value": 10020.0},
        {"time": 1704286800, "value": 10100.0},  # next day
        {"time": 1704290400, "value": 10150.0},
    ]
    trades = [
        {"type": "sell", "date": 1704207600, "pnl": 20.0},
    ]
    result = aggregate_macro(equity, trades, "D", 10000.0)
    assert len(result["macro_curve"]) == 2  # 2 days
    assert result["macro_curve"][0]["trades"][0]["pnl"] == 20.0


def test_aggregate_empty_equity():
    """Empty equity curve returns empty result."""
    result = aggregate_macro([], [], "W", 10000.0)
    assert result["macro_curve"] == []
    assert result["period_stats"]["winning_pct"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_backtest_macro.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'routes.backtest_macro'`

- [ ] **Step 3: Implement aggregate_macro()**

Create `backend/routes/backtest_macro.py`:

```python
import pandas as pd
from fastapi import APIRouter

router = APIRouter()

_FREQ_MAP = {"D": "D", "W": "W", "M": "ME", "Q": "QE", "Y": "YE"}
_LABELS = {"D": "Daily", "W": "Weekly", "M": "Monthly", "Q": "Quarterly", "Y": "Yearly"}


def aggregate_macro(
    equity_curve: list[dict],
    trades: list[dict],
    bucket: str,
    initial_capital: float,
) -> dict:
    """Aggregate raw equity curve + trades into macro buckets.

    Args:
        equity_curve: [{"time": str|int, "value": float}, ...]
        trades: full trades list (buys + sells); only sells/covers with "pnl" are used
        bucket: one of "D", "W", "M", "Q", "Y"
        initial_capital: starting capital for drawdown tracking
    """
    if not equity_curve:
        return {
            "macro_curve": [],
            "bucket": bucket,
            "period_stats": _empty_period_stats(bucket),
        }

    # Build equity DataFrame with datetime index
    df = pd.DataFrame(equity_curve)
    if isinstance(df["time"].iloc[0], (int, float)):
        df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True)
    else:
        df["dt"] = pd.to_datetime(df["time"])
    df = df.set_index("dt")

    # Build trades DataFrame (sells/covers only)
    sell_trades = [t for t in trades if t.get("pnl") is not None]
    if sell_trades:
        tdf = pd.DataFrame(sell_trades)
        if isinstance(tdf["date"].iloc[0], (int, float)):
            tdf["dt"] = pd.to_datetime(tdf["date"], unit="s", utc=True)
        else:
            tdf["dt"] = pd.to_datetime(tdf["date"])
        tdf = tdf.set_index("dt")
    else:
        tdf = pd.DataFrame()

    freq = _FREQ_MAP[bucket]

    # Group equity by bucket
    macro_curve = []
    running_peak = initial_capital

    for name, group in df.groupby(pd.Grouper(freq=freq)):
        if group.empty:
            continue
        o = float(group["value"].iloc[0])
        h = float(group["value"].max())
        l = float(group["value"].min())
        c = float(group["value"].iloc[-1])

        running_peak = max(running_peak, h)
        dd_pct = round((l - running_peak) / running_peak * 100, 2) if running_peak > 0 else 0.0

        # Match trades to this bucket
        bucket_trades = []
        if not tdf.empty:
            mask = tdf.index.to_series().groupby(pd.Grouper(freq=freq)).ngroup()
            bucket_num = df.index.to_series().groupby(pd.Grouper(freq=freq)).ngroup()
            # Simpler: filter trades whose dt falls within this bucket's range
            bucket_start = group.index.min()
            bucket_end = group.index.max()
            for _, trade in tdf.iterrows():
                if bucket_start <= trade.name <= bucket_end:
                    bucket_trades.append({"pnl": round(float(trade["pnl"]), 2)})

        macro_curve.append({
            "time": name.strftime("%Y-%m-%d"),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "drawdown_pct": dd_pct,
            "trades": bucket_trades,
        })

    # Period stats
    period_stats = _compute_period_stats(macro_curve, bucket)

    return {
        "macro_curve": macro_curve,
        "bucket": bucket,
        "period_stats": period_stats,
    }


def _compute_period_stats(macro_curve: list[dict], bucket: str) -> dict:
    """Compute period-level summary stats from macro buckets."""
    if not macro_curve:
        return _empty_period_stats(bucket)

    returns = []
    trade_counts = []
    for b in macro_curve:
        if b["open"] != 0:
            returns.append(round((b["close"] - b["open"]) / b["open"] * 100, 2))
        trade_counts.append(len(b["trades"]))

    winning = [r for r in returns if r > 0]

    return {
        "label": _LABELS[bucket],
        "winning_pct": round(len(winning) / len(returns) * 100, 1) if returns else 0,
        "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else 0,
        "best_return_pct": round(max(returns), 2) if returns else 0,
        "worst_return_pct": round(min(returns), 2) if returns else 0,
        "avg_trades": round(sum(trade_counts) / len(trade_counts), 1) if trade_counts else 0,
    }


def _empty_period_stats(bucket: str) -> dict:
    return {
        "label": _LABELS.get(bucket, bucket),
        "winning_pct": 0,
        "avg_return_pct": 0,
        "best_return_pct": 0,
        "worst_return_pct": 0,
        "avg_trades": 0,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_backtest_macro.py -v`
Expected: all 6 tests PASS

If any test fails, debug by checking pandas grouper behavior with the specific dates. The `freq="W"` default groups Sun–Sat. Adjust test expectations or use `freq="W-FRI"` if needed to match trading week boundaries. Fix iteratively until all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/routes/backtest_macro.py backend/tests/test_backtest_macro.py
git commit -m "feat(macro): add aggregate_macro function with tests"
```

---

## Task 2: Backend — Backtest result caching

**Files:**
- Modify: `backend/routes/backtest.py:1-10` (imports), `backend/routes/backtest.py:358-381` (after result is built)

- [ ] **Step 1: Add hash function and cache dict to backtest.py**

At the top of `backend/routes/backtest.py`, after the existing imports (line 8), add:

```python
import hashlib
import json
```

After the `router = APIRouter()` line (line 10), add the cache dict and hash function:

```python
# Single-entry cache for macro endpoint re-aggregation.
# Stores the most recent backtest's raw equity + trades, keyed by request hash.
_backtest_cache: dict = {}


def _request_hash(req) -> str:
    """Deterministic hash of a StrategyRequest for cache keying."""
    d = req.model_dump(exclude={"debug"})
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()
```

- [ ] **Step 2: Populate cache at end of run_backtest()**

In `backend/routes/backtest.py`, find the line `result = {` (line 358). Just before that line, add cache population:

```python
        # Cache raw data for macro endpoint re-aggregation
        _backtest_cache.clear()
        _backtest_cache.update({
            "hash": _request_hash(req),
            "equity_curve": equity,
            "trades": trades,
        })
```

This caches the raw `equity` list and `trades` list (Python dicts, not the final response). The cache is populated before the result dict is built so it's available even if the response serialization changes later.

- [ ] **Step 3: Run existing tests to verify nothing broke**

Run: `cd backend && python -m pytest tests/ -v`
Expected: all existing tests PASS (the cache additions don't affect test behavior since they're side effects of the route handler, and existing tests import helper functions directly)

- [ ] **Step 4: Commit**

```bash
git add backend/routes/backtest.py
git commit -m "feat(macro): add single-entry backtest result cache for macro re-aggregation"
```

---

## Task 3: Backend — Macro endpoint + router registration

**Files:**
- Modify: `backend/routes/backtest_macro.py:1-5` (add endpoint after existing code)
- Modify: `backend/main.py:14,44` (import + register router)

- [ ] **Step 1: Add the macro endpoint to backtest_macro.py**

At the bottom of `backend/routes/backtest_macro.py`, after the `_empty_period_stats` function, add:

```python
from fastapi import HTTPException
from models import StrategyRequest
from routes.backtest import _backtest_cache, _request_hash


@router.post("/api/backtest/macro")
def macro_backtest(req: StrategyRequest, macro_bucket: str = "W"):
    if macro_bucket not in _FREQ_MAP:
        raise HTTPException(status_code=400, detail=f"Invalid bucket: {macro_bucket}. Must be one of D, W, M, Q, Y")

    req_hash = _request_hash(req)

    # Check cache — if miss, run full backtest to populate it
    if _backtest_cache.get("hash") != req_hash:
        from routes.backtest import run_backtest
        run_backtest(req)  # populates _backtest_cache as side effect

    equity_curve = _backtest_cache["equity_curve"]
    trades = _backtest_cache["trades"]

    result = aggregate_macro(equity_curve, trades, macro_bucket, req.initial_capital)

    # Include the full summary from the backtest (same as regular endpoint)
    # Re-run gives us cached data, so extract summary from a fresh run if needed
    # Actually, summary is in the backtest response — but we only cached equity + trades.
    # For simplicity, compute summary stats we need from the macro data itself.
    # The frontend already has the full summary from the original backtest call.

    return result
```

Note: The macro endpoint does NOT return the full summary — the frontend already has it from the original `POST /api/backtest` call. The macro response only contains `macro_curve`, `bucket`, and `period_stats`.

- [ ] **Step 2: Register the macro router in main.py**

In `backend/main.py`, after line 13 (`from routes.backtest import router as backtest_router`), add:

```python
from routes.backtest_macro import router as backtest_macro_router
```

After line 43 (`app.include_router(backtest_router)`), add:

```python
app.include_router(backtest_macro_router)
```

- [ ] **Step 3: Smoke test the endpoint**

Start the backend:
```bash
cd backend && python -m uvicorn main:app --port 8000 &
```

Test with curl (this will run a full backtest since cache is empty, then aggregate):
```bash
curl -s -X POST http://localhost:8000/api/backtest/macro?macro_bucket=W \
  -H "Content-Type: application/json" \
  -d '{"ticker":"AAPL","start":"2024-01-01","end":"2024-06-01","interval":"1d","buy_rules":[{"indicator":"macd","condition":"crossover_up"}],"sell_rules":[{"indicator":"macd","condition":"crossover_down"}],"initial_capital":10000}' \
  | python -m json.tool | head -30
```

Expected: JSON response with `macro_curve` (array of bucket objects), `bucket: "W"`, and `period_stats` object.

Kill the test server after verification.

- [ ] **Step 4: Run all backend tests**

Run: `cd backend && python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routes/backtest_macro.py backend/main.py
git commit -m "feat(macro): add POST /api/backtest/macro endpoint with router registration"
```

---

## Task 4: Frontend — Types

**Files:**
- Modify: `frontend/src/shared/types/index.ts:186` (after BacktestResult interface)

- [ ] **Step 1: Add macro-related TypeScript interfaces**

In `frontend/src/shared/types/index.ts`, after the `BacktestResult` interface (after line 186), add:

```typescript
export interface MacroCurvePoint {
  time: string
  open: number
  high: number
  low: number
  close: number
  drawdown_pct: number
  trades: { pnl: number }[]
}

export interface PeriodStats {
  label: string
  winning_pct: number
  avg_return_pct: number
  best_return_pct: number
  worst_return_pct: number
  avg_trades: number
}

export interface MacroResponse {
  macro_curve: MacroCurvePoint[]
  bucket: string
  period_stats: PeriodStats
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors (new types are standalone, not yet consumed)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/shared/types/index.ts
git commit -m "feat(macro): add MacroCurvePoint, PeriodStats, MacroResponse types"
```

---

## Task 5: Frontend — State management

Lift `activeTab` and bucket state from Results into App so they persist across backtest re-runs. Store the last `StrategyRequest` so the macro hook can reuse it.

**Files:**
- Modify: `frontend/src/App.tsx:1-4` (imports), `59` (state), `138-139` (StrategyBuilder props), `207` (Results props)
- Modify: `frontend/src/features/strategy/StrategyBuilder.tsx:9-10` (Props interface), `129-164` (runBacktest), `42` (component signature)
- Modify: `frontend/src/features/strategy/Results.tsx:8-9` (Tab type), `15-18` (Props), `20-22` (component), `122-135` (tab bar)

- [ ] **Step 1: Update StrategyBuilder to pass request up alongside result**

In `frontend/src/features/strategy/StrategyBuilder.tsx`, change the `Props` interface (line 9-10):

```typescript
interface Props {
  ticker: string
  start: string
  end: string
  interval: string
  onResult: (r: BacktestResult | null, req?: StrategyRequest) => void
  dataSource: DataSource
  settingsPortalId?: string
  maSettings?: MASettings
}
```

In the `runBacktest` function (around line 157), change `onResult(data)` to:

```typescript
      onResult(data, req)
```

The `onResult(null)` call at line 133 stays unchanged (no request during loading).

- [ ] **Step 2: Add lifted state to App.tsx**

In `frontend/src/App.tsx`, add to the imports (line 2):

```typescript
import { useState, useCallback, useEffect, useMemo } from 'react'
```

(Already imported — no change needed.)

After `const [backtestResult, setBacktestResult] = useState<BacktestResult | null>(null)` (line 59), add:

```typescript
  const [lastRequest, setLastRequest] = useState<StrategyRequest | null>(null)
  const [resultsTab, setResultsTab] = useState<'summary' | 'equity' | 'trades' | 'trace'>('summary')
  const [macroBucket, setMacroBucket] = useState<string | null>(null)
```

Add `StrategyRequest` to the type import on line 3:

```typescript
import type { BacktestResult, IndicatorKey, DataSource, MAType, StrategyRequest } from './shared/types'
```

- [ ] **Step 3: Update the onResult handler in App.tsx**

Change the StrategyBuilder's `onResult` prop (line 202, inside the JSX):

```typescript
                      <StrategyBuilder
                        ticker={ticker}
                        start={start}
                        end={end}
                        interval={interval}
                        onResult={(result, req) => {
                          setBacktestResult(result)
                          if (req) setLastRequest(req)
                        }}
                        dataSource={dataSource}
                        settingsPortalId="strategy-settings-portal"
                        maSettings={maSettings}
                      />
```

- [ ] **Step 4: Pass new props to Results**

Change the Results rendering (line 207):

```typescript
                      {backtestResult && (
                        <Results
                          result={backtestResult}
                          mainChart={mainChart}
                          activeTab={resultsTab}
                          onTabChange={setResultsTab}
                          bucket={macroBucket}
                          onBucketChange={setMacroBucket}
                          lastRequest={lastRequest}
                        />
                      )}
```

- [ ] **Step 5: Update Results.tsx Props interface and component**

In `frontend/src/features/strategy/Results.tsx`, change the `Tab` type and `Props` interface (lines 8-18):

```typescript
export type ResultsTab = 'summary' | 'equity' | 'trades' | 'trace'

interface Props {
  result: BacktestResult
  mainChart?: IChartApi | null
  activeTab: ResultsTab
  onTabChange: (tab: ResultsTab) => void
  bucket: string | null
  onBucketChange: (bucket: string | null) => void
  lastRequest: import('../../shared/types').StrategyRequest | null
}
```

Update the component signature (line 20) to destructure the new props:

```typescript
export default function Results({ result, mainChart, activeTab, onTabChange, bucket, onBucketChange, lastRequest }: Props) {
```

Remove the local `activeTab` state (line 22):

```typescript
  // REMOVE: const [activeTab, setActiveTab] = useState<Tab>('summary')
```

Replace all `setActiveTab` calls with `onTabChange` in the JSX. In the tab bar buttons (line 129):

```typescript
            onClick={() => onTabChange(tab)}
```

And update the tab type reference in the tab bar map (line 126):

```typescript
        {(['summary', 'equity', 'trades', ...(signal_trace ? ['trace'] : [])] as ResultsTab[]).map(tab => (
```

- [ ] **Step 6: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 7: Verify app loads in browser**

Run: `cd /path/to/project && ./start.sh`
Open http://localhost:5173. Run a backtest. Verify:
- Tab switching still works (Summary / Equity Curve / Trades)
- Tab selection persists if you run backtest again (previously always reset to Summary)

- [ ] **Step 8: Commit**

```bash
git add frontend/src/App.tsx frontend/src/features/strategy/StrategyBuilder.tsx frontend/src/features/strategy/Results.tsx
git commit -m "feat(macro): lift tab + bucket state to App, pass request up from StrategyBuilder"
```

---

## Task 6: Frontend — Bucket selector UI + macro hook

**Files:**
- Create: `frontend/src/shared/hooks/useMacro.ts`
- Modify: `frontend/src/features/strategy/Results.tsx` (tab bar, hook integration)

- [ ] **Step 1: Create the macro API hook**

Create `frontend/src/shared/hooks/useMacro.ts`:

```typescript
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { MacroResponse, StrategyRequest } from '../types'

export function useMacro(request: StrategyRequest | null, bucket: string | null) {
  return useQuery<MacroResponse>({
    queryKey: ['macro', bucket, request ? JSON.stringify(request) : ''],
    queryFn: async () => {
      const { data } = await api.post(`/api/backtest/macro?macro_bucket=${bucket}`, request)
      return data
    },
    enabled: !!request && !!bucket,
    staleTime: Infinity,
  })
}
```

- [ ] **Step 2: Add bucket selector buttons to the Results tab bar**

In `frontend/src/features/strategy/Results.tsx`, add the import for the hook (after line 6):

```typescript
import { useMacro } from '../../shared/hooks/useMacro'
import type { MacroResponse, StrategyRequest } from '../../shared/types'
```

Inside the component function, after the `sells` line, add the macro hook call:

```typescript
  const { data: macroData, isLoading: macroLoading } = useMacro(lastRequest, bucket)
```

Replace the entire tab bar `<div style={styles.tabBar}>` section with:

```typescript
      <div style={styles.tabBar}>
        <div style={{ display: 'flex' }}>
          {(['summary', 'equity', 'trades', ...(signal_trace ? ['trace'] : [])] as ResultsTab[]).map(tab => (
            <button
              key={tab}
              onClick={() => onTabChange(tab)}
              style={{ ...styles.tab, ...(activeTab === tab ? styles.tabActive : {}) }}
            >
              {tab === 'summary' ? 'Summary' : tab === 'equity' ? 'Equity Curve' : tab === 'trades' ? `Trades (${sells.length})` : `Signal Trace (${signal_trace!.length})`}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', marginLeft: 'auto', gap: 2, alignItems: 'center' }}>
          {(['Detail', 'D', 'W', 'M', 'Q', 'Y'] as const).map(b => {
            const isDetail = b === 'Detail'
            const isActive = isDetail ? bucket === null : bucket === b
            return (
              <button
                key={b}
                onClick={() => onBucketChange(isDetail ? null : b)}
                style={{
                  padding: '4px 8px',
                  fontSize: 11,
                  fontWeight: 600,
                  color: isActive ? '#58a6ff' : '#8b949e',
                  background: isActive ? 'rgba(88, 166, 255, 0.1)' : 'none',
                  border: 'none',
                  borderRadius: 3,
                  cursor: 'pointer',
                }}
              >
                {b}
                {!isDetail && macroLoading && bucket === b && ' ...'}
              </button>
            )
          })}
        </div>
      </div>
```

- [ ] **Step 3: Verify TypeScript compiles and bucket buttons render**

Run: `cd frontend && npx tsc --noEmit`

Open http://localhost:5173. Run a backtest. Verify:
- Tab bar now shows "Detail | D | W | M | Q | Y" on the right side
- "Detail" is highlighted blue by default
- Clicking "W" highlights it and shows "..." briefly while the macro endpoint loads
- Clicking "Detail" returns to normal state

- [ ] **Step 4: Commit**

```bash
git add frontend/src/shared/hooks/useMacro.ts frontend/src/features/strategy/Results.tsx
git commit -m "feat(macro): add bucket selector UI and useMacro hook"
```

---

## Task 7: Frontend — Period stats on Summary tab

**Files:**
- Modify: `frontend/src/features/strategy/Results.tsx` (summary tab section)

- [ ] **Step 1: Add the PeriodStats strip to the summary tab**

In `frontend/src/features/strategy/Results.tsx`, find the `{activeTab === 'summary' && (` block. After the `metricsGrid` div (after the closing `</div>` of the metrics grid, before the P&L distribution section), add:

```typescript
          {macroData?.period_stats && bucket && (
            <div style={{
              display: 'flex', flexWrap: 'wrap', gap: 0, padding: '10px 16px',
              background: '#0d1117', borderTop: '1px solid #21262d', borderBottom: '1px solid #21262d',
            }}>
              <div style={{ width: '100%', marginBottom: 6 }}>
                <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                  {macroData.period_stats.label} Stats
                </span>
              </div>
              {[
                { label: `Winning ${macroData.period_stats.label.replace('ly', '')}s`, value: `${macroData.period_stats.winning_pct}%`, color: macroData.period_stats.winning_pct >= 50 ? '#26a641' : '#f85149' },
                { label: 'Avg Return', value: `${macroData.period_stats.avg_return_pct > 0 ? '+' : ''}${macroData.period_stats.avg_return_pct}%`, color: macroData.period_stats.avg_return_pct >= 0 ? '#26a641' : '#f85149' },
                { label: `Best ${macroData.period_stats.label.replace('ly', '')}`, value: `+${macroData.period_stats.best_return_pct}%`, color: '#26a641' },
                { label: `Worst ${macroData.period_stats.label.replace('ly', '')}`, value: `${macroData.period_stats.worst_return_pct}%`, color: '#f85149' },
                { label: `Trades/${macroData.period_stats.label.charAt(0)}`, value: macroData.period_stats.avg_trades.toFixed(1), color: '#e6edf3' },
              ].map(({ label, value, color }) => (
                <div key={label} style={{ padding: '4px 20px 4px 0', minWidth: 100 }}>
                  <div style={{ fontSize: 10, color: '#8b949e', marginBottom: 2 }}>{label}</div>
                  <div style={{ fontSize: 13, fontWeight: 700, color }}>{value}</div>
                </div>
              ))}
            </div>
          )}
```

The label derivation (`label.replace('ly', '') + 's'`) produces: "Weekly" → "Weeks", "Monthly" → "Months", etc. For "Daily" it produces "Dais" which is wrong. Fix with a lookup:

Actually, let me use a simpler approach — a map:

```typescript
          {macroData?.period_stats && bucket && (() => {
            const ps = macroData.period_stats
            const periodName: Record<string, string> = { Daily: 'Day', Weekly: 'Week', Monthly: 'Month', Quarterly: 'Quarter', Yearly: 'Year' }
            const pn = periodName[ps.label] ?? ps.label
            return (
              <div style={{
                display: 'flex', flexWrap: 'wrap', gap: 0, padding: '10px 16px',
                background: '#0d1117', borderTop: '1px solid #21262d', borderBottom: '1px solid #21262d',
              }}>
                <div style={{ width: '100%', marginBottom: 6 }}>
                  <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                    {ps.label} Stats
                  </span>
                </div>
                {[
                  { label: `Winning ${pn}s`, value: `${ps.winning_pct}%`, color: ps.winning_pct >= 50 ? '#26a641' : '#f85149' },
                  { label: 'Avg Return', value: `${ps.avg_return_pct > 0 ? '+' : ''}${ps.avg_return_pct}%`, color: ps.avg_return_pct >= 0 ? '#26a641' : '#f85149' },
                  { label: `Best ${pn}`, value: `+${ps.best_return_pct}%`, color: '#26a641' },
                  { label: `Worst ${pn}`, value: `${ps.worst_return_pct}%`, color: '#f85149' },
                  { label: `Trades/${pn.charAt(0)}`, value: ps.avg_trades.toFixed(1), color: '#e6edf3' },
                ].map(({ label, value, color }) => (
                  <div key={label} style={{ padding: '4px 20px 4px 0', minWidth: 100 }}>
                    <div style={{ fontSize: 10, color: '#8b949e', marginBottom: 2 }}>{label}</div>
                    <div style={{ fontSize: 13, fontWeight: 700, color }}>{value}</div>
                  </div>
                ))}
              </div>
            )
          })()}
```

- [ ] **Step 2: Verify in browser**

Open http://localhost:5173. Run a backtest. Click "W" bucket. Switch to Summary tab. Verify:
- A "Weekly Stats" section appears between the top metrics and the P&L Distribution
- Shows: Winning Weeks, Avg Return, Best Week, Worst Week, Trades/W
- Darker background (#0d1117) visually separates it from global stats
- Click "Detail" — period stats disappear
- Click "M" — labels change to "Monthly Stats", "Winning Months", etc.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/strategy/Results.tsx
git commit -m "feat(macro): add period stats strip to summary tab when macro bucket active"
```

---

## Task 8: Frontend — Macro equity chart component

**Files:**
- Create: `frontend/src/features/strategy/MacroEquityChart.tsx`
- Modify: `frontend/src/features/strategy/Results.tsx` (conditional render in equity tab)

- [ ] **Step 1: Create MacroEquityChart.tsx**

Create `frontend/src/features/strategy/MacroEquityChart.tsx`:

```typescript
import { useEffect, useRef } from 'react'
import { createChart, BaselineSeries, LineSeries, HistogramSeries, ColorType, LineType } from 'lightweight-charts'
import type { MacroCurvePoint } from '../../shared/types'

interface Props {
  macroCurve: MacroCurvePoint[]
  initialCapital: number
}

export default function MacroEquityChart({ macroCurve, initialCapital }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current || macroCurve.length === 0) return

    const chart = createChart(containerRef.current, {
      height: containerRef.current.clientHeight || 250,
      layout: { background: { type: ColorType.Solid, color: '#0d1117' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#1c2128' }, horzLines: { color: '#1c2128' } },
      timeScale: { borderColor: '#30363d' },
      rightPriceScale: { borderColor: '#30363d' },
      crosshair: { mode: 0 },
    })

    // 1. Close line — BaselineSeries (green above initial capital, red below)
    const closeSeries = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: initialCapital },
      topLineColor: '#26a641',
      bottomLineColor: '#f85149',
      topFillColor1: 'rgba(38, 166, 65, 0.1)',
      topFillColor2: 'rgba(38, 166, 65, 0)',
      bottomFillColor1: 'rgba(248, 81, 73, 0)',
      bottomFillColor2: 'rgba(248, 81, 73, 0.1)',
      lineWidth: 2,
      priceScaleId: 'right',
    })
    closeSeries.setData(
      macroCurve.map(b => ({ time: b.time as any, value: b.close }))
    )

    // 2. High/low stepped lines
    const ddColor = (pct: number): string => {
      // Blue (calm) → red (deep drawdown) based on drawdown_pct
      const severity = Math.min(1, Math.abs(pct) / 20) // 20% dd = full red
      const r = Math.round(88 + (248 - 88) * severity)
      const g = Math.round(166 + (81 - 166) * severity)
      const b = Math.round(255 + (73 - 255) * severity)
      return `rgba(${r}, ${g}, ${b}, 0.5)`
    }

    const highSeries = chart.addSeries(LineSeries, {
      lineWidth: 1,
      lineType: LineType.WithSteps,
      priceScaleId: 'right',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    highSeries.setData(
      macroCurve.map(b => ({ time: b.time as any, value: b.high, color: ddColor(b.drawdown_pct) }))
    )

    const lowSeries = chart.addSeries(LineSeries, {
      lineWidth: 1,
      lineType: LineType.WithSteps,
      priceScaleId: 'right',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    lowSeries.setData(
      macroCurve.map(b => ({ time: b.time as any, value: b.low, color: ddColor(b.drawdown_pct) }))
    )

    // 3. Trade density ticks — histogram at chart bottom
    const allPnls = macroCurve.flatMap(b => b.trades.map(t => t.pnl))
    const maxPnl = Math.max(...allPnls.map(Math.abs), 1)

    // Flatten: for each bucket, emit one tick per trade at the bucket time.
    // Since multiple trades share a time, use a single aggregated bar per bucket.
    // Color = net P&L of the bucket's trades.
    const tickData = macroCurve
      .filter(b => b.trades.length > 0)
      .map(b => {
        const netPnl = b.trades.reduce((sum, t) => sum + t.pnl, 0)
        const intensity = 0.3 + 0.7 * Math.min(1, Math.abs(netPnl) / maxPnl)
        return {
          time: b.time as any,
          value: b.trades.length,
          color: netPnl >= 0
            ? `rgba(38, 166, 65, ${intensity})`
            : `rgba(248, 81, 73, ${intensity})`,
        }
      })

    if (tickData.length > 0) {
      const tickSeries = chart.addSeries(HistogramSeries, {
        priceScaleId: 'trade-ticks',
        base: 0,
        lastValueVisible: false,
        priceLineVisible: false,
      })
      chart.priceScale('trade-ticks').applyOptions({
        visible: false,
        scaleMargins: { top: 0.9, bottom: 0 },
      })
      tickSeries.setData(tickData)
    }

    // Fit full range — no sync with main chart
    chart.timeScale().fitContent()

    // 4. Crosshair tooltip
    const tooltip = tooltipRef.current
    chart.subscribeCrosshairMove(param => {
      if (!tooltip) return
      if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        tooltip.style.display = 'none'
        return
      }
      const bucket = macroCurve.find(b => b.time === param.time)
      if (!bucket) {
        tooltip.style.display = 'none'
        return
      }
      const returnPct = bucket.open !== 0
        ? ((bucket.close - bucket.open) / bucket.open * 100).toFixed(2)
        : '0.00'
      const returnColor = Number(returnPct) >= 0 ? '#26a641' : '#f85149'

      tooltip.style.display = 'block'
      tooltip.style.left = `${param.point.x + 16}px`
      tooltip.style.top = `${param.point.y - 10}px`
      tooltip.innerHTML = `
        <div style="font-weight:600;margin-bottom:4px">${bucket.time}</div>
        <div>Open: $${bucket.open.toLocaleString()}</div>
        <div>Close: $${bucket.close.toLocaleString()}</div>
        <div>High: $${bucket.high.toLocaleString()}</div>
        <div>Low: $${bucket.low.toLocaleString()}</div>
        <div style="color:${returnColor}">Return: ${Number(returnPct) > 0 ? '+' : ''}${returnPct}%</div>
        <div>DD: ${bucket.drawdown_pct.toFixed(2)}%</div>
        <div>Trades: ${bucket.trades.length}</div>
      `
    })

    // Resize observer
    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      chart.remove()
      ro.disconnect()
    }
  }, [macroCurve, initialCapital])

  return (
    <div style={{ position: 'relative', width: '100%', height: 250, minHeight: 100, maxHeight: 600, resize: 'vertical', overflow: 'hidden' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      <div
        ref={tooltipRef}
        style={{
          display: 'none',
          position: 'absolute',
          top: 0,
          left: 0,
          padding: '8px 12px',
          background: 'rgba(22, 27, 34, 0.95)',
          border: '1px solid #30363d',
          borderRadius: 6,
          color: '#e6edf3',
          fontSize: 11,
          lineHeight: 1.5,
          pointerEvents: 'none',
          zIndex: 10,
          whiteSpace: 'nowrap',
        }}
      />
    </div>
  )
}
```

- [ ] **Step 2: Wire MacroEquityChart into Results.tsx equity tab**

In `frontend/src/features/strategy/Results.tsx`, add the import (at the top with other imports):

```typescript
import MacroEquityChart from './MacroEquityChart'
```

Replace the `{activeTab === 'equity' && (` block with conditional rendering:

```typescript
      {activeTab === 'equity' && (
        bucket && macroData ? (
          <MacroEquityChart
            macroCurve={macroData.macro_curve}
            initialCapital={summary.initial_capital}
          />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', fontSize: 11, color: '#8b949e', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={showBaseline}
                onChange={e => setShowBaseline(e.target.checked)}
              />
              Show buy &amp; hold baseline
            </label>
            <div ref={chartRef} style={{ width: '100%', height: 200, minHeight: 100, maxHeight: 600, resize: 'vertical', overflow: 'hidden' }} />
          </div>
        )
      )}
```

Also update the detail equity useEffect dependency array — add a guard so it only runs when `bucket` is null (detail mode). Find the `useEffect` that starts with `if (activeTab !== 'equity'` (around line 27) and change the condition:

```typescript
    if (activeTab !== 'equity' || bucket !== null || !chartRef.current || equity_curve.length === 0) return
```

And add `bucket` to its dependency array (the array at the end of the useEffect):

```typescript
  }, [activeTab, bucket, equity_curve, summary.total_return_pct, mainChart, showBaseline, result.baseline_curve])
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 4: Test in browser**

Open http://localhost:5173. Run a backtest. Then:
1. Click "Equity Curve" tab — should show the normal detail chart
2. Click "W" — equity chart switches to macro view (green/red baseline, stepped high/low lines)
3. Hover over the chart — tooltip shows bucket details
4. Click "Detail" — returns to normal synced equity chart
5. Click "M" — shows monthly aggregation (fewer, wider buckets)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/strategy/MacroEquityChart.tsx frontend/src/features/strategy/Results.tsx
git commit -m "feat(macro): add MacroEquityChart component with baseline line, high/low band, trade ticks, tooltip"
```

---

## Task 9: Frontend — Detail mode trade density ticks + auto-default bucket

**Files:**
- Modify: `frontend/src/features/strategy/Results.tsx` (detail equity useEffect, auto-default helper)

- [ ] **Step 1: Add trade density ticks to the detail equity chart**

In `frontend/src/features/strategy/Results.tsx`, add `HistogramSeries` to the lightweight-charts import:

```typescript
import { createChart, BaselineSeries, LineSeries, HistogramSeries, ColorType } from 'lightweight-charts'
```

In the detail equity chart `useEffect`, after the baseline series block (after `baselineSeries.setData(...)`) and before the `// Initial alignment` comment, add:

```typescript
    // Trade density ticks at exact bar positions
    if (sells.length > 0) {
      const maxPnl = Math.max(...sells.map(s => Math.abs(s.pnl ?? 0)), 1)
      const tickSeries = chart.addSeries(HistogramSeries, {
        priceScaleId: 'trade-ticks',
        base: 0,
        lastValueVisible: false,
        priceLineVisible: false,
      })
      chart.priceScale('trade-ticks').applyOptions({
        visible: false,
        scaleMargins: { top: 0.92, bottom: 0 },
      })
      tickSeries.setData(
        sells.map(s => {
          const pnl = s.pnl ?? 0
          const intensity = 0.3 + 0.7 * Math.min(1, Math.abs(pnl) / maxPnl)
          return {
            time: s.date as any,
            value: 1,
            color: pnl >= 0
              ? `rgba(38, 166, 65, ${intensity})`
              : `rgba(248, 81, 73, ${intensity})`,
          }
        })
      )
    }
```

- [ ] **Step 2: Add auto-default bucket helper**

In `frontend/src/features/strategy/Results.tsx`, add a helper function before the component (above the `export default function Results` line):

```typescript
function autoDefaultBucket(equityLength: number): string {
  if (equityLength < 500) return 'W'
  if (equityLength <= 5000) return 'D'
  if (equityLength <= 50000) return 'W'
  return 'M'
}
```

This function isn't auto-applied — it's available for the user to see which bucket is recommended. For now, add a subtle visual indicator on the recommended bucket. In the bucket selector buttons, update the style for the recommended button:

In the bucket selector `map`, add a computed variable before the return:

```typescript
          {(['Detail', 'D', 'W', 'M', 'Q', 'Y'] as const).map(b => {
            const isDetail = b === 'Detail'
            const isActive = isDetail ? bucket === null : bucket === b
            const isRecommended = !isDetail && bucket === null && b === autoDefaultBucket(equity_curve.length)
            return (
              <button
                key={b}
                onClick={() => onBucketChange(isDetail ? null : b)}
                style={{
                  padding: '4px 8px',
                  fontSize: 11,
                  fontWeight: 600,
                  color: isActive ? '#58a6ff' : isRecommended ? '#58a6ff' : '#8b949e',
                  background: isActive ? 'rgba(88, 166, 255, 0.1)' : 'none',
                  border: 'none',
                  borderBottom: isRecommended && !isActive ? '2px solid rgba(88, 166, 255, 0.3)' : '2px solid transparent',
                  borderRadius: 3,
                  cursor: 'pointer',
                }}
              >
                {b}
                {!isDetail && macroLoading && bucket === b && ' ...'}
              </button>
            )
          })}
```

The recommended bucket gets a subtle blue underline when the user is in Detail mode, guiding them toward the best bucket size.

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 4: Test in browser**

Open http://localhost:5173.

**Detail trade ticks:**
1. Run a backtest (e.g. AAPL daily, 2023-2024, MACD crossover rules)
2. Click "Equity Curve" tab (should be in Detail mode)
3. Verify thin green/red ticks at the bottom of the chart at trade exit positions
4. Green ticks = winning trades, red = losing, brighter = larger P&L

**Auto-default bucket hint:**
5. While in Detail mode, check the bucket buttons on the right
6. One of D/W/M/Q/Y should have a subtle blue underline (the recommended bucket)
7. For ~250 trading days, "W" should be recommended

**Macro mode:**
8. Click the recommended bucket
9. Equity chart switches to macro view
10. Summary tab shows period stats strip
11. Click "Detail" to return

**Persistence across backtests:**
12. Select "W" bucket, then click "Run Backtest" again
13. After loading, "W" should still be selected and macro data auto-fetched

- [ ] **Step 5: Run all backend tests**

Run: `cd backend && python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/strategy/Results.tsx
git commit -m "feat(macro): add detail trade density ticks and auto-default bucket recommendation"
```

---

## Self-Review Checklist

### Spec coverage

| Spec requirement | Task |
|---|---|
| `POST /api/backtest/macro` endpoint | Task 3 |
| Single-entry backtest cache | Task 2 |
| Bucket aggregation (OHLC, drawdown, trades) | Task 1 |
| Period stats (winning %, avg return, best/worst, avg trades) | Task 1 (backend), Task 7 (frontend) |
| Shared bucket selector in tab bar | Task 6 |
| Tab + bucket persistence across backtests | Task 5 |
| Macro chart: baseline close line | Task 8 |
| Macro chart: stepped high/low band | Task 8 |
| Macro chart: trade density ticks | Task 8 |
| Macro chart: crosshair tooltip | Task 8 |
| Macro chart: fitContent, no main chart sync | Task 8 |
| Detail mode: trade density ticks | Task 9 |
| Auto-default bucket | Task 9 |
| Summary tab: period stats section | Task 7 |
| Band drawdown coloring (blue → red) | Task 8 (per-point color on high/low lines) |

### Type consistency

- `MacroCurvePoint` — defined in Task 4, consumed in Task 8 (`MacroEquityChart.tsx` props)
- `PeriodStats` — defined in Task 4, consumed in Task 7 (summary tab rendering)
- `MacroResponse` — defined in Task 4, returned by `useMacro()` in Task 6, consumed in Tasks 7/8
- `ResultsTab` — defined in Task 5 (Results.tsx), used in App.tsx state
- `aggregate_macro()` — defined in Task 1 (backtest_macro.py), called in Task 3 (endpoint)
- `_request_hash()` / `_backtest_cache` — defined in Task 2 (backtest.py), imported in Task 3 (backtest_macro.py)
- `autoDefaultBucket()` — defined and used within Task 9 (Results.tsx)

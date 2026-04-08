# Project Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the monolith backend and flat frontend into domain-based modules so features can be developed in isolated branches without merge conflicts.

**Architecture:** Extract backend endpoints into FastAPI APIRouter modules grouped by domain (data, indicators, backtest, search), with shared helpers in a common module. Move frontend components into feature folders (chart, strategy, sidebar) with shared hooks/types.

**Tech Stack:** Python FastAPI APIRouter, React/TypeScript, Vite

**Spec:** `docs/superpowers/specs/2026-04-04-project-restructure-design.md`

---

### Task 1: Create backend shared module

**Files:**
- Create: `backend/shared.py`

- [ ] **Step 1: Create `backend/shared.py`**

Extract the shared helpers and constants from `backend/main.py` (lines 21-59) into a new file:

```python
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
```

- [ ] **Step 2: Verify the module imports cleanly**

Run:
```bash
cd backend && ./venv/bin/python3 -c "from shared import _fetch, _format_time; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/shared.py
git commit -m "refactor: extract shared helpers to backend/shared.py"
```

---

### Task 2: Create backend route modules

**Files:**
- Create: `backend/routes/__init__.py`
- Create: `backend/routes/data.py`
- Create: `backend/routes/indicators.py`
- Create: `backend/routes/backtest.py`
- Create: `backend/routes/search.py`

- [ ] **Step 1: Create `backend/routes/__init__.py`**

```python
```

Empty file — just makes `routes` a Python package.

- [ ] **Step 2: Create `backend/routes/data.py`**

Extract the OHLCV endpoint (current `main.py` lines 64-85):

```python
from fastapi import APIRouter, HTTPException
from shared import _fetch, _format_time

router = APIRouter()


@router.get("/api/ohlcv/{ticker}")
def get_ohlcv(ticker: str, start: str = "2023-01-01", end: str = "2024-01-01", interval: str = "1d"):
    try:
        df = _fetch(ticker, start, end, interval)
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

- [ ] **Step 3: Create `backend/routes/indicators.py`**

Extract the indicators endpoint and its `_series_to_list` helper (current `main.py` lines 90-166):

```python
from fastapi import APIRouter, HTTPException
import numpy as np
import pandas as pd
from shared import _fetch, _format_time

router = APIRouter()


def _series_to_list(index, interval, series):
    return [
        {"time": _format_time(t, interval), "value": round(float(v), 4) if pd.notna(v) else None}
        for t, v in zip(index, series)
    ]


@router.get("/api/indicators/{ticker}")
def get_indicators(
    ticker: str,
    start: str = "2023-01-01",
    end: str = "2024-01-01",
    interval: str = "1d",
    indicators: str = "macd,rsi",
):
    try:
        df = _fetch(ticker, start, end, interval)

        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        result = {}
        requested = [i.strip().lower() for i in indicators.split(",")]

        if "macd" in requested:
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            histogram = macd_line - signal_line
            result["macd"] = {
                "macd": _series_to_list(df.index, interval, macd_line),
                "signal": _series_to_list(df.index, interval, signal_line),
                "histogram": _series_to_list(df.index, interval, histogram),
            }

        if "rsi" in requested:
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))
            result["rsi"] = _series_to_list(df.index, interval, rsi)

        if "ema" in requested:
            result["ema"] = {
                "ema20": _series_to_list(df.index, interval, close.ewm(span=20, adjust=False).mean()),
                "ema50": _series_to_list(df.index, interval, close.ewm(span=50, adjust=False).mean()),
                "ema200": _series_to_list(df.index, interval, close.ewm(span=200, adjust=False).mean()),
            }

        if "bb" in requested:
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            result["bb"] = {
                "upper": _series_to_list(df.index, interval, sma20 + 2 * std20),
                "middle": _series_to_list(df.index, interval, sma20),
                "lower": _series_to_list(df.index, interval, sma20 - 2 * std20),
            }

        if "orb" in requested:
            result["orb"] = {
                "high": _series_to_list(df.index, interval, high),
                "low": _series_to_list(df.index, interval, low),
            }

        if "volume" in requested:
            result["volume"] = _series_to_list(df.index, interval, df["Volume"])

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 4: Create `backend/routes/backtest.py`**

Extract models and backtest endpoint (current `main.py` lines 171-361):

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
import pandas as pd
import numpy as np
from typing import Optional
from shared import _fetch, _format_time

router = APIRouter()


class Rule(BaseModel):
    indicator: str
    condition: str
    value: Optional[float] = None
    param: Optional[str] = None


class StrategyRequest(BaseModel):
    ticker: str
    start: str = "2023-01-01"
    end: str = "2024-01-01"
    interval: str = "1d"
    buy_rules: list[Rule]
    sell_rules: list[Rule]
    buy_logic: str = "AND"
    sell_logic: str = "AND"
    initial_capital: float = 10000.0
    position_size: float = 1.0

    @field_validator('position_size')
    @classmethod
    def clamp_position_size(cls, v: float) -> float:
        return max(0.01, min(1.0, v))


@router.post("/api/backtest")
def run_backtest(req: StrategyRequest):
    try:
        df = _fetch(req.ticker, req.start, req.end, req.interval)

        close = df["Close"]

        # Precompute all indicators
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()

        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))

        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()

        indicators = {
            "macd": macd_line,
            "signal": signal_line,
            "rsi": rsi,
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "close": close,
        }

        def eval_rule(rule: Rule, i: int) -> bool:
            if i < 1:
                return False
            ind = rule.indicator.lower()
            cond = rule.condition.lower()

            series_map = {
                "macd": indicators["macd"],
                "rsi": indicators["rsi"],
                "price": indicators["close"],
                "ema20": indicators["ema20"],
                "ema50": indicators["ema50"],
                "ema200": indicators["ema200"],
            }
            ref_map = {
                "signal": indicators["signal"],
                "ema20": indicators["ema20"],
                "ema50": indicators["ema50"],
                "ema200": indicators["ema200"],
                "close": indicators["close"],
            }

            s = series_map.get(ind)
            if s is None:
                return False

            v_now = s.iloc[i]
            v_prev = s.iloc[i - 1]

            if cond in ("crossover_up", "crosses_above"):
                if rule.param and rule.param in ref_map:
                    ref = ref_map[rule.param]
                    return v_prev < ref.iloc[i - 1] and v_now >= ref.iloc[i]
                elif rule.value is not None:
                    return v_prev < rule.value <= v_now
            elif cond in ("crossover_down", "crosses_below"):
                if rule.param and rule.param in ref_map:
                    ref = ref_map[rule.param]
                    return v_prev > ref.iloc[i - 1] and v_now <= ref.iloc[i]
                elif rule.value is not None:
                    return v_prev > rule.value >= v_now
            elif cond == "above":
                if rule.param and rule.param in ref_map:
                    return v_now > ref_map[rule.param].iloc[i]
                elif rule.value is not None:
                    return v_now > rule.value
            elif cond == "below":
                if rule.param and rule.param in ref_map:
                    return v_now < ref_map[rule.param].iloc[i]
                elif rule.value is not None:
                    return v_now < rule.value
            return False

        def eval_rules(rules: list[Rule], logic: str, i: int) -> bool:
            results = [eval_rule(r, i) for r in rules]
            if not results:
                return False
            return all(results) if logic == "AND" else any(results)

        # Simulate
        capital = req.initial_capital
        position = 0.0
        entry_price = 0.0
        trades = []
        equity = []

        for i in range(len(df)):
            price = close.iloc[i]
            date = _format_time(df.index[i], req.interval)

            if position == 0 and eval_rules(req.buy_rules, req.buy_logic, i):
                shares = (capital * req.position_size) / price
                position = shares
                entry_price = price
                capital -= shares * price
                trades.append({"type": "buy", "date": date, "price": round(price, 4), "shares": round(shares, 4)})

            elif position > 0 and eval_rules(req.sell_rules, req.sell_logic, i):
                proceeds = position * price
                pnl = proceeds - position * entry_price
                trades.append({
                    "type": "sell",
                    "date": date,
                    "price": round(price, 4),
                    "shares": round(position, 4),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl / (position * entry_price) * 100, 2),
                })
                capital += proceeds
                position = 0.0

            total_value = capital + (position * price if position > 0 else 0)
            equity.append({"time": date, "value": round(total_value, 2)})

        # Close open position at last price
        final_price = close.iloc[-1]
        final_value = capital + position * final_price

        total_return = (final_value - req.initial_capital) / req.initial_capital * 100
        buy_hold_return = (close.iloc[-1] - close.iloc[0]) / close.iloc[0] * 100

        # Sharpe ratio (annualized, daily returns)
        eq_values = [e["value"] for e in equity]
        eq_series = pd.Series(eq_values)
        daily_returns = eq_series.pct_change().dropna()
        sharpe = float((daily_returns.mean() / daily_returns.std()) * np.sqrt(252)) if daily_returns.std() > 0 else 0

        # Max drawdown
        peak = eq_series.cummax()
        drawdown = (eq_series - peak) / peak
        max_drawdown = float(drawdown.min() * 100)

        sell_trades = [t for t in trades if t["type"] == "sell"]
        winning = [t for t in sell_trades if t.get("pnl", 0) > 0]
        win_rate = len(winning) / len(sell_trades) * 100 if sell_trades else 0

        return {
            "summary": {
                "initial_capital": req.initial_capital,
                "final_value": round(final_value, 2),
                "total_return_pct": round(total_return, 2),
                "buy_hold_return_pct": round(buy_hold_return, 2),
                "num_trades": len(sell_trades),
                "win_rate_pct": round(win_rate, 2),
                "sharpe_ratio": round(sharpe, 3),
                "max_drawdown_pct": round(max_drawdown, 2),
            },
            "trades": trades,
            "equity_curve": equity,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 5: Create `backend/routes/search.py`**

Extract the search endpoint (current `main.py` lines 366-381):

```python
from fastapi import APIRouter
import yfinance as yf

router = APIRouter()


@router.get("/api/search")
def search_ticker(q: str):
    try:
        results = yf.Search(q, max_results=8)
        quotes = results.quotes if results.quotes else []
        return [
            {
                "symbol": r.get("symbol", ""),
                "name": r.get("longname") or r.get("shortname") or r.get("symbol", ""),
                "type": r.get("quoteType", ""),
            }
            for r in quotes
            if r.get("symbol")
        ]
    except Exception:
        return []
```

- [ ] **Step 6: Commit route files**

```bash
git add backend/routes/
git commit -m "refactor: extract backend endpoints into route modules"
```

---

### Task 3: Rewrite backend main.py as thin app shell

**Files:**
- Modify: `backend/main.py` (replace entire contents)

- [ ] **Step 1: Replace `backend/main.py` with router imports**

Replace the entire file with:

```python
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

- [ ] **Step 2: Update test import**

In `backend/tests/test_models.py`, change line 4 from:

```python
from main import StrategyRequest
```

to:

```python
from routes.backtest import StrategyRequest
```

- [ ] **Step 3: Run tests**

Run:
```bash
cd backend && ./venv/bin/python3 -m pytest tests/ -v
```
Expected: All 7 tests pass.

- [ ] **Step 4: Start the backend and smoke test**

Run:
```bash
cd backend && ./venv/bin/uvicorn main:app --port 8000 &
sleep 2
curl -s http://localhost:8000/api/ohlcv/AAPL?start=2025-03-01\&end=2025-04-01\&interval=1d | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'OHLCV: {len(d[\"data\"])} bars')"
curl -s http://localhost:8000/api/indicators/AAPL?start=2025-03-01\&end=2025-04-01\&interval=1d\&indicators=rsi | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'RSI: {len(d[\"rsi\"])} points')"
curl -s "http://localhost:8000/api/search?q=AAPL" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Search: {len(d)} results')"
kill %1 2>/dev/null
```
Expected: All three endpoints return data.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_models.py
git commit -m "refactor: slim backend main.py to app shell with router imports"
```

---

### Task 4: Restructure frontend into feature folders

**Files:**
- Create: `frontend/src/features/chart/Chart.tsx` (move from `components/`)
- Create: `frontend/src/features/strategy/StrategyBuilder.tsx` (move from `components/`)
- Create: `frontend/src/features/strategy/Results.tsx` (move from `components/`)
- Create: `frontend/src/features/sidebar/Sidebar.tsx` (move from `components/`)
- Create: `frontend/src/shared/hooks/useOHLCV.ts` (move from `hooks/`)
- Create: `frontend/src/shared/types/index.ts` (move from `types/`)
- Modify: `frontend/src/App.tsx` (update imports)
- Delete: `frontend/src/components/` (after moves)
- Delete: `frontend/src/hooks/` (after moves)
- Delete: `frontend/src/types/` (after moves)

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p frontend/src/features/chart frontend/src/features/strategy frontend/src/features/sidebar frontend/src/shared/hooks frontend/src/shared/types
```

- [ ] **Step 2: Move files with git mv**

```bash
cd frontend/src
git mv components/Chart.tsx features/chart/Chart.tsx
git mv components/StrategyBuilder.tsx features/strategy/StrategyBuilder.tsx
git mv components/Results.tsx features/strategy/Results.tsx
git mv components/Sidebar.tsx features/sidebar/Sidebar.tsx
git mv hooks/useOHLCV.ts shared/hooks/useOHLCV.ts
git mv types/index.ts shared/types/index.ts
rmdir components hooks types
```

Using `git mv` preserves git history for each file.

- [ ] **Step 3: Update imports in `frontend/src/App.tsx`**

Change lines 2-7 from:

```typescript
import type { BacktestResult, IndicatorKey } from './types'
import { useOHLCV, useIndicators } from './hooks/useOHLCV'
import Sidebar from './components/Sidebar'
import Chart from './components/Chart'
import StrategyBuilder from './components/StrategyBuilder'
import Results from './components/Results'
```

to:

```typescript
import type { BacktestResult, IndicatorKey } from './shared/types'
import { useOHLCV, useIndicators } from './shared/hooks/useOHLCV'
import Sidebar from './features/sidebar/Sidebar'
import Chart from './features/chart/Chart'
import StrategyBuilder from './features/strategy/StrategyBuilder'
import Results from './features/strategy/Results'
```

- [ ] **Step 4: Update imports in moved component files**

In `frontend/src/features/chart/Chart.tsx`, change the types import (line 11) from:

```typescript
import type { OHLCVBar, IndicatorData, IndicatorKey, TimeValue } from '../types'
```

to:

```typescript
import type { OHLCVBar, IndicatorData, IndicatorKey, TimeValue } from '../../shared/types'
```

In `frontend/src/features/strategy/StrategyBuilder.tsx`, update its imports. Check the file for any `../types` or `../hooks` imports and change:
- `'../types'` → `'../../shared/types'`
- `'../hooks/useOHLCV'` → `'../../shared/hooks/useOHLCV'`

In `frontend/src/features/strategy/Results.tsx`, change:
- `'../types'` → `'../../shared/types'`

In `frontend/src/features/sidebar/Sidebar.tsx`, change:
- `'../hooks/useOHLCV'` → `'../../shared/hooks/useOHLCV'`
- `'../types'` → `'../../shared/types'`

- [ ] **Step 5: Run type-check**

Run:
```bash
cd frontend && npx tsc --noEmit
```
Expected: No errors.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: restructure frontend into feature folders"
```

---

### Task 5: Update CLAUDE.md and verify full app

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md file structure section**

Replace the file structure block in CLAUDE.md with the new structure:

```
frontend/src/
  App.tsx              — state, data fetching, layout (central hub)
  features/
    chart/
      Chart.tsx        — the complex one (see below)
    strategy/
      StrategyBuilder.tsx — buy/sell rule builder, backtest trigger
      Results.tsx      — tabbed: Summary / Equity Curve / Trades
    sidebar/
      Sidebar.tsx      — ticker search, date range, indicators, compare
  shared/
    hooks/useOHLCV.ts  — useOHLCV, useIndicators, useSearch (React Query)
    types/index.ts     — all shared TypeScript types

backend/
  main.py              — app setup, CORS, mounts routers (~25 lines)
  shared.py            — _fetch(), _format_time(), interval constants
  routes/
    data.py            — GET /api/ohlcv/{ticker}
    indicators.py      — GET /api/indicators/{ticker}
    backtest.py        — POST /api/backtest + models
    search.py          — GET /api/search
  tests/
start.sh               — starts both servers
```

Also update the Backend Notes section — replace the `_series_to_list()` mention:

> `_series_to_list()` lives in `routes/indicators.py` (only used there)

- [ ] **Step 2: Start full app and verify**

```bash
# Kill any running servers
pkill -f uvicorn 2>/dev/null; pkill -f vite 2>/dev/null
sleep 1

# Start backend
cd backend && ./venv/bin/uvicorn main:app --reload --port 8000 &
sleep 2

# Start frontend
cd frontend && npm run dev &
sleep 3

# Smoke test backend
curl -s http://localhost:8000/api/ohlcv/AAPL?start=2025-03-01\&end=2025-04-01\&interval=1d | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'OK: {len(d[\"data\"])} bars')"
```

Expected: Backend returns data, frontend loads at localhost:5173 (or 5174).

- [ ] **Step 3: Run all checks**

```bash
cd backend && ./venv/bin/python3 -m pytest tests/ -v
cd frontend && npx tsc --noEmit
```

Expected: All tests pass, no type errors.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md to reflect restructured project layout"
```

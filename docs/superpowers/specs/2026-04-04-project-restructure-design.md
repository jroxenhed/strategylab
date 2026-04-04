# Project Restructure: Domain-Based Module Split

**Date:** 2026-04-04
**Status:** Approved
**Goal:** Split backend and frontend into domain-based modules so features can be developed in isolation without merge conflicts.

## Current State

```
backend/
  main.py              ← 381 lines, ALL backend logic in one file
  tests/

frontend/src/
  App.tsx              ← 112 lines, central state hub
  components/          ← flat folder, all components mixed
    Chart.tsx          ← 347 lines
    Results.tsx        ← 117 lines
    Sidebar.tsx        ← 178 lines
    StrategyBuilder.tsx ← 190 lines
  hooks/useOHLCV.ts    ← 43 lines
  types/index.ts       ← 95 lines
```

**Problem:** All backend logic lives in one file. Working on backtesting and indicators in parallel causes merge conflicts. Frontend components are in a flat folder with no grouping by feature.

## Target Structure

### Backend

```
backend/
  main.py              ← app setup, CORS, mount routers (~20 lines)
  shared.py            ← _fetch(), _format_time(), _INTRADAY_INTERVALS, _INTERVAL_MAX_DAYS
  routes/
    __init__.py
    data.py            ← GET /api/ohlcv/{ticker}
    indicators.py      ← GET /api/indicators/{ticker} + _series_to_list()
    backtest.py        ← POST /api/backtest + Rule, StrategyRequest models + engine
    search.py          ← GET /api/search
  tests/
    __init__.py
    test_models.py
```

**How it works:**

- Each route file defines a `router = APIRouter()` and decorates endpoints with `@router.get(...)` / `@router.post(...)`
- `main.py` imports each router and calls `app.include_router(router)`
- `shared.py` contains helpers used by multiple routes: `_fetch()`, `_format_time()`, interval constants
- `_series_to_list()` stays in `indicators.py` (only used there)
- `Rule` and `StrategyRequest` models stay in `backtest.py` (only used there)

**File mapping (what moves where):**

| Current `main.py` lines | Target file |
|---|---|
| App setup, CORS | `main.py` |
| `_INTRADAY_INTERVALS`, `_INTERVAL_MAX_DAYS`, `_fetch()`, `_format_time()` | `shared.py` |
| `get_ohlcv()` | `routes/data.py` |
| `get_indicators()`, `_series_to_list()` | `routes/indicators.py` |
| `Rule`, `StrategyRequest`, `run_backtest()` | `routes/backtest.py` |
| `search_ticker()` | `routes/search.py` |

### Frontend

```
frontend/src/
  App.tsx              ← unchanged (central orchestrator)
  main.tsx             ← unchanged
  features/
    chart/
      Chart.tsx
    strategy/
      StrategyBuilder.tsx
      Results.tsx
    sidebar/
      Sidebar.tsx
  shared/
    hooks/
      useOHLCV.ts
    types/
      index.ts
```

**What changes:**

- `components/` folder replaced by `features/` with domain subfolders
- `hooks/` and `types/` move under `shared/` since they're used across features
- Import paths in `App.tsx` update to new locations
- No logic changes to any file

**Feature-to-folder mapping (what you'd branch on):**

| Work area | Backend | Frontend |
|---|---|---|
| Chart / visualization | — | `features/chart/` |
| Backtesting | `routes/backtest.py` | `features/strategy/` |
| Indicators | `routes/indicators.py` | `features/chart/` |
| Ticker search / sidebar | `routes/search.py` | `features/sidebar/` |
| Data fetching | `routes/data.py`, `shared.py` | `shared/hooks/` |

## What Does NOT Change

- All API endpoints (paths, methods, request/response formats)
- `App.tsx` remains the state hub — no state migration
- `start.sh`, `package.json`, `vite.config.ts`, tsconfig files
- Test structure (existing tests just update imports)
- Git branches (can be rebased onto restructured main)

## Constraints

- **Pure move + re-import.** No logic changes, no refactoring, no feature work in this PR.
- **One commit for backend, one for frontend.** If something breaks, easy to bisect.
- **Update CLAUDE.md** to reflect new structure after the move.
- **Run type-check and start the app** to verify nothing broke.

## Future Opportunities (not in scope)

- Split `Chart.tsx` into `MainChart.tsx` + `IndicatorPane.tsx` when it grows past ~500 lines
- Move `Rule`/`StrategyRequest` models to a shared `models.py` if other routes need them
- Add per-domain test files (`tests/test_backtest.py`, `tests/test_indicators.py`)

# StrategyLab — Claude Context

Interactive trading strategy backtester. Read this before touching anything.

## Stack

- **Frontend**: React + TypeScript + Vite, lightweight-charts v5 (TradingView), TanStack Query
- **Backend**: Python FastAPI, yfinance, pandas, numpy

```
frontend/src/
  App.tsx              — state, data fetching, layout
  components/
    Chart.tsx          — the complex one (see below)
    Sidebar.tsx        — ticker search, date range, indicators, compare
    StrategyBuilder.tsx — buy/sell rule builder, backtest trigger
    Results.tsx        — tabbed: Summary / Equity Curve / Trades
  hooks/useOHLCV.ts    — useOHLCV, useIndicators, useSearch (React Query)
  types/index.ts       — all shared TypeScript types

backend/main.py        — FastAPI; /api/ohlcv/{ticker}, /api/indicators/{ticker},
                         /api/backtest, /api/search
start.sh               — starts both servers
```

## Chart.tsx Architecture

This is the most complex file. Read it before editing.

Three separate `IChartApi` instances rendered as a flex column:
- **Main chart** (`containerRef`) — candlesticks, SPY/QQQ overlays, EMA, BB, Volume
- **MACD pane** (`macdContainerRef`) — histogram + MACD/Signal lines
- **RSI pane** (`rsiContainerRef`) — RSI line + 70/30 reference lines

### Pane synchronization

Pan/zoom: `subscribeVisibleTimeRangeChange` on the main chart → `setVisibleRange()` on MACD/RSI. Time-based (not logical/bar-index) because RSI's `rolling(14)` warmup means RSI's bar-0 is 14 days later than the main chart's bar-0. Logical range sync would offset the RSI pane by 14 bars.

Price scale alignment: `syncWidths()` equalises `rightPriceScale.minimumWidth` across all three charts. Also mirrors the main chart's left axis width (visible when SPY/QQQ are shown) onto MACD/RSI as invisible left axes — otherwise MACD/RSI plot areas start further left than the main chart. Called on every range change AND via `setTimeout(100)` on initial mount (fires after MACD/RSI effects have had time to run).

Crosshair sync: `subscribeCrosshairMove` on each chart → `setCrosshairPosition(NaN, param.time, seriesRef)` on the other two. Requires series refs (`candleSeriesRef`, `macdSeriesRef`, `rsiSeriesRef`).

### Series priceScaleId rules (lightweight-charts v5)

In v5, `addSeries()` without an explicit `priceScaleId` creates an **independent** scale rather than sharing 'right'. Always set explicitly:
- Candlesticks, EMA, BB → `priceScaleId: 'right'`
- SPY/QQQ % lines → `priceScaleId: 'left'` (visible left axis)
- Volume → `priceScaleId: 'volume'` (hidden, `scaleMargins: { top: 0.75, bottom: 0 }`)

### SPY/QQQ overlay

Fetched in App.tsx always (even when hidden) to avoid loading delay. Passed to Chart only when `showSpy`/`showQqq` is true. Normalized to % change from first close: `((close - base) / base) * 100`. Displayed on a visible left axis. The left axis is hidden (`visible: false` in chartOptions) by default and made visible when either is active.

### Indicator pane height split

| Active panes | Main | Sub |
|---|---|---|
| Neither | 100% | — |
| One | 65% | 35% |
| Both | 50% | 25% each |

## Backend Notes

- `yf.download(ticker, ...)` with `auto_adjust=True` — prices are adjusted for splits/dividends
- Intraday data limits (yfinance): 1m=7d, 5m/15m/30m=60d, 1h=730d
- Sidebar shows an orange warning when the selected date range exceeds the interval limit
- Backend passes interval string directly to yfinance — no mapping needed

## Known Open Issues (as of 2026-04-04)

1. **RSI / MACD alignment** — mostly fixed (switched to time-based range sync). May still be slightly off in edge cases; root cause is that MACD/RSI are separate chart instances, not lightweight-charts native panes. The `syncWidths()` + `setTimeout(100)` approach works for most situations but is inherently racy on first load.

2. **SPY/QQQ visual similarity** — the lines look very correlated because QQQ and SPY both track large-cap US equities. The data IS fetched separately (verified by different queryKeys in React Query). Label precision is 2 decimal places so similar values like 30.28 vs 30.35 are distinguishable.

## Branches

- `main` — primary working branch
- `feature/more-indicators` — planned (ATR, Stochastic, VWAP, etc.)
- `feature/more-strategy-rules` — planned
- `feature/chart-timeframe-buttons` — planned (1W / 1M / 3M / 1Y buttons)
- `feature/watchlist` — planned

## Running

```bash
./start.sh
# Frontend: http://localhost:5173
# Backend:  http://localhost:8000
```

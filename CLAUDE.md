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

Pan/zoom: `subscribeVisibleLogicalRangeChange` on the main chart → `setVisibleLogicalRange()` on MACD/RSI. Uses logical (bar-index) sync. Indicator data uses **whitespace entries** (`{ time }` with no `value`) for warmup bars (e.g. RSI's first 14 points) so all charts have the same bar count and stay aligned.

MACD/RSI effects sync to the main chart's logical range on mount via `getVisibleLogicalRange()`.

Price scale alignment: `syncWidths()` equalises `rightPriceScale.minimumWidth` across all three charts. Also mirrors the main chart's left axis width onto MACD/RSI as invisible left axes — otherwise MACD/RSI plot areas start further left than the main chart. Called on every range change AND via `setTimeout(100)` on initial mount.

Crosshair sync: `subscribeCrosshairMove` on each chart → `setCrosshairPosition(NaN, param.time, seriesRef)` on the other two. Requires series refs (`candleSeriesRef`, `macdSeriesRef`, `rsiSeriesRef`).

### Series priceScaleId rules (lightweight-charts v5)

In v5, `addSeries()` without an explicit `priceScaleId` creates an **independent** scale rather than sharing 'right'. Always set explicitly:
- Candlesticks, EMA, BB → `priceScaleId: 'right'`
- SPY → `priceScaleId: 'spy-scale'` (hidden, real close prices)
- QQQ → `priceScaleId: 'qqq-scale'` (hidden, real close prices)
- Volume → `priceScaleId: 'volume'` (hidden, `scaleMargins: { top: 0.75, bottom: 0 }`)

### SPY/QQQ overlay

Fetched in App.tsx always (even when hidden) to avoid loading delay. Passed to Chart only when `showSpy`/`showQqq` is true. Displayed as real close prices on independent hidden scales (`spy-scale`, `qqq-scale`), so each line auto-scales independently and the crosshair tooltip shows actual dollar values.

### Indicator pane height split

| Active panes | Main | Sub |
|---|---|---|
| Neither | 100% | — |
| One | 65% | 35% |
| Both | 50% | 25% each |

## Backend Notes

- **CRITICAL: Never use `yf.download()`** — it shares global state and returns wrong data under concurrent requests. Always use `yf.Ticker(symbol).history()` via the `_fetch()` helper.
- `_fetch()` auto-clamps date ranges to yfinance limits for intraday intervals (1m=7d, 5m/15m/30m=60d, 1h=730d)
- `_format_time()` returns `"YYYY-MM-DD"` strings for daily+ intervals and **unix timestamps** (seconds, UTC) for intraday — lightweight-charts requires unique timestamps per bar
- `_series_to_list()` preserves null values (for indicator warmup periods) so the frontend can use whitespace data for bar alignment

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

# StrategyLab — Claude Context

Interactive trading strategy backtester. Read this before touching anything.

## Stack

- **Frontend**: React + TypeScript + Vite, lightweight-charts v5 (TradingView), TanStack Query
- **Backend**: Python FastAPI, yfinance, pandas, numpy

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
- `_series_to_list()` lives in `routes/indicators.py`; preserves null values (for indicator warmup periods) so the frontend can use whitespace data for bar alignment

### Data providers

Three providers are registered in `shared.py`:
- `yahoo` — yfinance, always available
- `alpaca` — Alpaca SIP feed (requires `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` in `backend/.env`), paid subscription for recent intraday
- `alpaca-iex` — Alpaca IEX feed, real-time, free tier, narrower coverage (no OTC)

Both Alpaca providers use `Adjustment.SPLIT` so historical prices are always split-adjusted.

When Alpaca `end` date is today or future, the provider substitutes `now` so intraday bars aren't cut off at midnight UTC.

### `_fetch()` TTL cache

`shared.py` has an in-memory TTL cache on `_fetch()`:
- **2 min TTL** for live intraday (interval in `_INTRADAY_INTERVALS` and end ≥ today)
- **1 hour TTL** for fully historical data
- Max 100 entries; evicts expired first, then oldest on overflow
- Logs `[cache HIT]` / `[cache MISS]` to stdout for debugging
- `GET /api/cache` returns current cache state (count, entries, ages, TTLs)
- **Note:** cache is in-process memory — server restart (including `--reload` on file change) clears it

### Timezone handling in Chart.tsx

lightweight-charts v5 has **no `localization.timeZone` support**. All unix timestamps are shifted to ET wall-clock time via `toET()` before being passed to any series. `toET()` uses `Intl.DateTimeFormat` with `America/New_York` to reconstruct the timestamp as UTC so the chart displays 9:30–16:00 for NYSE hours. Daily date strings pass through unchanged.

### Rule negation (NOT)

`Rule` has a `negated: boolean` field. Applied in `eval_rules()` — if `negated` and `i >= 1`, the rule result is inverted. Guard condition (`i < 1`) always returns False regardless of negation. UI: small **NOT** button on each rule row in RuleRow.tsx, orange when active.

### Short selling (direction field)

`StrategyRequest` and `BotConfig` both have `direction: str = "long"` (accepts `"long"` or `"short"`, defaults to `"long"` for backwards compatibility).

The rule engine (`eval_rules`) is **direction-agnostic**. All inversion happens at execution boundaries:

- **Entry**: short fills lower (slippage against seller), `OrderSide.SELL` for Alpaca
- **Stop-loss**: triggers above entry for shorts (`high >= entry * (1 + pct)`)
- **Trailing stop**: tracks trough (lowest price) instead of peak; `source: "high"` maps to Low for shorts
- **PnL**: `(entry - exit) * shares` for shorts
- **Equity while holding**: `capital + shares * (entry - price)` unrealized
- **Trade types**: `"short"` / `"cover"` instead of `"buy"` / `"sell"`
- **Chart markers**: short entry = arrow down (above bar), cover = arrow up (below bar), same yellow/green/red colors

Bot runner for shorts: **no OTO brackets** — all stops managed via polling (Alpaca OTO doesn't cleanly support stops above entry). Same-symbol guard allows one long + one short bot simultaneously.

Frontend: direction toggle in strategy builder (labels swap to "Entry Rules" / "Exit Rules"), direction dropdown in AddBotBar, direction badge + subtle background tint on bot cards.

### Trailing stop — profit activation threshold

`TrailingStopConfig` has `activate_pct: float = 0.0`. When `activate_on_profit` is true, trailing only starts once `source_price >= entry_price * (1 + activate_pct / 100)`. Set to e.g. 2.0 to give a position room to breathe before the trailing stop starts following.

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

# StrategyLab — Claude Context

Interactive trading strategy backtester + live paper trading platform. Read this before touching anything.

## Working Style

- Work in small, focused steps. Report progress after each one.
- Use Grep over Read when searching for a specific line or pattern.
- If hitting an error or blocker, STOP and report immediately — don't retry in a loop.
- Narrate section-by-section during long writes — don't go silent for >60s.
- Output reasoning progressively to avoid API stream idle timeouts.

## Stack

- **Frontend**: React + TypeScript + Vite, lightweight-charts v5 (TradingView), TanStack Query
- **Backend**: Python FastAPI, yfinance, pandas, numpy, scipy (S-G filters)

```
frontend/src/
  App.tsx                — state, data fetching, layout (central hub)
  main.tsx               — React entry point
  api/
    client.ts            — axios instance
    trading.ts           — trading API calls (account, positions, orders)
    bots.ts              — bot CRUD API calls
  features/
    chart/
      Chart.tsx          — the complex one (see below)
    strategy/
      StrategyBuilder.tsx — buy/sell rule builder, backtest trigger
      Results.tsx        — tabbed: Summary / Equity Curve / Trades
      RuleRow.tsx        — single rule row with NOT toggle, mute, conditions
      PnlHistogram.tsx   — P&L distribution histogram
      MacroEquityChart.tsx — resampled equity for long timescales
    sidebar/
      Sidebar.tsx        — ticker, date range, intervals, data source, indicators
    trading/
      PaperTrading.tsx   — container: AccountBar → Bots → Positions → Journal → Orders
      AccountBar.tsx     — equity/cash/buying power metrics bar
      BotControlCenter.tsx — bot list with sparklines, start/stop all
      BotCard.tsx        — individual bot card (editable config, sparkline)
      AddBotBar.tsx      — create new bot (ticker, strategy, direction, allocation)
      PositionsTable.tsx — live positions from broker
      TradeJournal.tsx   — trade history with filters, CSV export
      OrderHistory.tsx   — raw order history
      MiniSparkline.tsx  — inline SVG sparkline for bot cards
    discovery/
      Discovery.tsx      — container: SignalScanner + PerformanceComparison
      SignalScanner.tsx   — scan tickers for strategy signals
      PerformanceComparison.tsx — compare strategy performance across tickers
  shared/
    hooks/
      useOHLCV.ts        — useOHLCV, useIndicators, useProviders, useSearch
      useLocalStorage.ts — persistent state hook
      useMacro.ts        — macro equity chart data hook
    types/index.ts       — all shared TypeScript types
    utils/
      time.ts            — toET() timezone helper
      format.ts          — number/currency formatting
      colors.ts          — shared color constants

backend/
  main.py              — FastAPI app setup, lifespan (BotManager), CORS, routers (~54 lines)
  shared.py            — DataProvider protocol, providers (Yahoo/Alpaca), _fetch() + TTL cache
  models.py            — StrategyRequest, TrailingStopConfig, DynamicSizingConfig, TradingHoursConfig
  signal_engine.py     — Rule model, eval_rules(), S-G filters (causal/centered/predictive)
  bot_manager.py       — BotConfig, BotState, BotManager singleton, bot persistence
  bot_runner.py        — async polling loop, entry/exit logic, position/fill management
  journal.py           — trade journal: log_trade(), compute_realized_pnl(), JSON storage
  routes/
    data.py            — GET /api/ohlcv/{ticker}
    indicators.py      — GET /api/indicators/{ticker}
    backtest.py        — POST /api/backtest + trade simulation engine
    backtest_macro.py  — POST /api/backtest/macro — resampled equity (D/W/M/Q/Y)
    search.py          — GET /api/search
    providers.py       — GET /api/providers
    trading.py         — trading endpoints (account, positions, orders, buy, sell, scan)
    bots.py            — bot CRUD, delegates to BotManager
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

## Signal Engine & S-G Filters

`signal_engine.py` — rule evaluation + Savitzky-Golay smoothing for MA indicators.

### Rule model

`Rule` has fields: `indicator`, `condition`, `value`, `param`, `threshold`, `muted`, `negated`. Conditions include crossover, above/below, crosses_above/below, turns_up/turns_down (slope change detection).

### Savitzky-Golay filters

Three variants in `signal_engine.py`, all using `scipy.signal.savgol_filter`:

- **Causal** (`_apply_sg(causal=True)`) — uses only past data, safe for backtesting. Each output bar is the S-G fit of the window ending at that bar.
- **Centered** (`_apply_sg(causal=False)`) — symmetric window, for chart display only. Would cause lookahead bias in backtests.
- **Predictive** (`_apply_sg_predictive()`) — fits polynomial to past `window` bars, evaluates it `(window-1)//2` bars ahead. Compensates causal lag using genuine prediction, not lookahead.

Parameters per MA: `sg8_window`, `sg8_poly`, `sg21_window`, `sg21_poly`, `predictive_sg` (all on `StrategyRequest`).

### Dead zone for turns_up/turns_down

`eps = abs(v_now) * 3e-5` when S-G smoothing is active. Prevents micro-oscillations near flat regions from generating false slope-change signals. Without this, S-G smoothed curves that are nearly flat trigger spurious turns_up/turns_down.

### Rule negation (NOT)

`Rule.negated: bool`. Applied in `eval_rules()` — if `negated` and `i >= 1`, the rule result is inverted. Guard condition (`i < 1`) always returns False regardless of negation. UI: small **NOT** button on each rule row in RuleRow.tsx, orange when active.

## Backtester Cost Model

`StrategyRequest` has `slippage_pct` and `commission_pct` (both default 0.0):
- **Slippage**: applied directionally — longs fill higher on entry / lower on exit, shorts the inverse
- **Commission**: percentage of trade value, deducted from capital on both entry and exit
- Both tracked per-trade in the trade list (`slippage` and `commission` fields)

## Short Selling (direction field)

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

## Bot System

### Architecture

- `bot_manager.py` — `BotManager` singleton manages lifecycle. `BotConfig` (Pydantic) for settings, `BotState` (dataclass) for runtime. Persists to `backend/data/bots.json`. Loaded at startup via FastAPI lifespan.
- `bot_runner.py` — async `_tick()` loop per bot. Evaluates strategy rules against live bars, manages entry/exit, polls for fills, handles stop-losses. Currently Alpaca-coupled (direct SDK imports).
- `journal.py` — trade journal stored as JSON at `backend/data/trade_journal.json`. `_log_trade()` records entries/exits with fill prices, slippage, timestamps. `compute_realized_pnl()` calculates running P&L.
- `routes/bots.py` — CRUD API, delegates to BotManager. No direct broker imports.
- `routes/trading.py` — account/positions/orders endpoints, buy/sell actions, signal scan. Currently Alpaca-coupled with `_alpaca_call()` retry wrapper.

### Bot runner details

- Allocation compounds: `allocated_capital + total_pnl`, matching backtest behavior
- Position size hardcoded to 100% of allocation
- Trailing stops polled every tick (no server-side OTO for shorts)
- Fill detection: polls Alpaca fill price, logs expected vs actual slippage in journal

## Discovery Page

`features/discovery/Discovery.tsx` — container for signal scanning and performance comparison tools.

- `SignalScanner.tsx` — scans multiple tickers against a strategy's rules, shows which have active signals
- `PerformanceComparison.tsx` — backtests a strategy across multiple tickers, compares returns

## Key Bugs Fixed

These document **why** certain patterns exist in the code:

- **S-G lookahead bias**: centered S-G filter was being used in backtests, seeing future data. Fixed by adding causal mode (`_apply_sg(causal=True)`) that only uses past bars.
- **S-G dead zone**: S-G smoothed flat curves triggered false turns_up/turns_down signals. Fixed with `eps = abs(v_now) * 3e-5` threshold.
- **yf.download() concurrency**: `yfinance.download()` shares global state, returns wrong data under concurrent requests. All code uses `yf.Ticker(symbol).history()` via `_fetch()`.

## Running

```bash
./start.sh
# Frontend: http://localhost:5173
# Backend:  http://localhost:8000
```

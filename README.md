# StrategyLab

Interactive trading strategy backtester and paper trading platform. Build strategies with technical indicators, backtest against historical data, and execute trades via Alpaca or Interactive Brokers paper trading.

## Stack

- **Frontend**: React + TypeScript + Vite, lightweight-charts v5 (TradingView), TanStack Query
- **Backend**: Python FastAPI, yfinance, pandas, numpy
- **Trading**: Alpaca API + Interactive Brokers (via `ib_insync`), paper trading

## Features

### Charting
- Candlestick chart with MACD, RSI, EMA (20/50/200), Bollinger Bands, Volume
- SPY/QQQ comparison overlays with independent scaling
- Synchronized pan/zoom and crosshair across main, MACD, and RSI panes
- Resizable panel layout with collapsible sidebars
- All intraday + daily intervals (1m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo)

### Strategy & Backtesting
- Rule builder with AND/OR logic (MACD crossover, RSI thresholds, price/EMA conditions, rule negation)
- **Long and short strategies** — direction toggle swaps entry/exit labels, inverts all execution math
- Fixed stop loss and trailing stops (percentage or ATR-based, with profit activation threshold)
- Dynamic position sizing (reduce size after consecutive stop losses)
- Trading hours filter with skip ranges (e.g. skip lunch, last 15 min)
- Realistic cost model — commission-free by default (Alpaca US equities); IBKR Fixed per-share opt-in (`$0.0035/share`, `$0.35` min), modeled slippage in bps (unsigned, default 2 bps; per-symbol empirical can floor *up* from the default but never below it), short borrow cost based on annual rate × hold days
- Backtest metrics: total return, Sharpe ratio, max drawdown, win rate, trade log
- Equity curve synced with main chart (baseline coloring: green above, red below initial capital)
- Buy & hold baseline overlay toggle (dashed line over equity curve for quick benchmark comparison)
- P&L distribution block: EV/trade + profit factor headline numbers, 3-row decomposition waterfall (Wins / Losses / Net with proportional bars), min/max/avg per side with inline mean+median, inline histogram
- Strategy save/load/delete (localStorage)
- Chart disable toggle for lightweight backtesting of large datasets

### Live Trading Bots (Alpaca + IBKR)
- Automated strategy execution with fund management and per-bot capital allocation
- **Per-bot broker selection** — each bot picks Alpaca or IBKR at creation (can run different bots on different brokers simultaneously, e.g. long bot on IBKR ISK + short bot on IBKR margin)
- **Per-bot data source selection** — bot rules evaluate on the selected feed (IEX / Alpaca SIP / IBKR / Yahoo), independent from the broker that executes orders
- **Long and short bots** — run both directions on the same ticker simultaneously
- Trailing stop management, OTO bracket orders (Alpaca long only), stop-loss polling (shorts + all IBKR trades)
- Slippage tracking (expected vs actual fill price)
- Per-bot P&L scoping — trade journal tags each trade with `bot_id`; deleting a bot and recreating on the same symbol starts fresh
- Bot cards with direction badges (LONG/SHORT), broker badge (`via Alpaca` / `via IBKR`), always-visible equity sparkline
- Sparkline timescale toggle: local-per-card or aligned across all bots for cross-bot timing comparison
- Manual entry button, in-place config editing (allocation, strategy, data source)
- Bulk actions: Start All, Stop All, Stop and Close (with confirmation)

### Discovery
- Signal scanner: scan watchlist against strategy rules, one-click execution
- Performance comparison across tickers
- Dedicated page separated from live bot management (bot army pipeline planned)

### Manual Trading (Alpaca + IBKR)
- Account overview (equity, cash, buying power, PDT status) — toggle between Alpaca/IBKR in the AccountBar
- Manual buy/sell with OTO bracket orders on Alpaca; polled stop-loss management on IBKR
- Positions table, order history, trade journal

### Data Providers
- **Yahoo Finance** — always available, free
- **Alpaca SIP** — paid real-time feed (requires API keys)
- **Alpaca IEX** — free real-time feed, narrower coverage
- **IBKR** — via `ib_insync` / IB Gateway (requires `IBKR_HOST`, `IBKR_PORT` env vars)
- In-memory TTL cache (2 min for live intraday, 1 hour for historical)

## Running

```bash
./start.sh
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000

For Alpaca paper trading, add API keys to `backend/.env`:
```
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
```

For IBKR, run IB Gateway (paper account) and set:
```
IBKR_HOST=127.0.0.1
IBKR_PORT=4002
IBKR_CLIENT_ID=1
ACTIVE_BROKER=alpaca        # default broker for AccountBar; each bot picks its own
```
Uncheck **Read-Only API** in Gateway → Configure → Settings → API, otherwise order submission returns Error 321.

## Project Structure

```
backend/
  main.py              — app setup, CORS, mounts routers
  shared.py            — _fetch(), data providers (Yahoo/Alpaca/IBKR), TTL cache
  broker.py            — TradingProvider protocol + Alpaca/IBKR implementations, broker registry
  models.py            — shared Pydantic models (StrategyRequest, etc.)
  journal.py           — trade journal logger (bot_id-scoped P&L, caches slippage_bps)
  slippage.py          — sign/unit conventions for slippage (cost ≥ 0, signed bias, modeled vs measured policy)
  signal_engine.py     — indicator computation, rule evaluation
  bot_manager.py       — bot lifecycle (add/start/stop/delete)
  bot_runner.py        — bot execution loop (polling, orders, stops)
  routes/
    data.py            — GET /api/ohlcv/{ticker}
    indicators.py      — GET /api/indicators/{ticker}
    backtest.py        — POST /api/backtest
    backtest_macro.py  — POST /api/backtest/macro (resampled equity D/W/M/Q/Y)
    trading.py         — manual trading, positions, journal
    bots.py            — bot CRUD
    providers.py       — GET /api/providers, GET/PUT /api/broker
    slippage.py        — GET /api/slippage/{symbol}
    search.py          — GET /api/search

frontend/src/
  App.tsx              — state, data fetching, layout
  api/
    client.ts          — shared axios instance (configurable baseURL)
    trading.ts         — trading API functions
    bots.ts            — bot API functions
  features/
    chart/Chart.tsx    — three-pane chart (candlesticks, MACD, RSI)
    strategy/          — StrategyBuilder, Results
    trading/           — BotControlCenter, BotCard, AddBotBar, MiniSparkline
    discovery/         — Discovery, SignalScanner, PerformanceComparison
    sidebar/Sidebar.tsx
  shared/
    hooks/             — useOHLCV, useLocalStorage
    utils/             — format, colors, time
    types/index.ts     — all shared TypeScript types
```

## Planned

See `TODO.md` for the full themed roadmap. Highlights:

- **Charts & Indicators** — portfolio equity chart (combined P&L across bots), more indicators (ATR, Stochastic, VWAP), watchlist
- **Strategy Engine** — pre-market / extended hours, per-rule signal visualization toggles, borrow cost estimation for live shorts, spread-derived slippage default, cost model v2 (margin interest, IBKR Tiered, hard-to-borrow rates, FX)
- **Strategy Summary** — inline min↔max ranges, Alpha-vs-B&H metric, histogram polish
- **Bots** — browser-local timezone in bot log, reordering/grouping, IBKR silent order reject surfacing
- **Discovery (research)** — candidate scanning, batch backtesting, AI-assisted parameter tuning, pipeline to spawn a bot army

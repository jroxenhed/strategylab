# StrategyLab

Interactive trading strategy backtester and paper trading platform. Build strategies with technical indicators, backtest against historical data, and execute trades via Alpaca paper trading.

## Stack

- **Frontend**: React + TypeScript + Vite, lightweight-charts v5 (TradingView), TanStack Query
- **Backend**: Python FastAPI, yfinance, pandas, numpy
- **Trading**: Alpaca API (paper trading)

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
- Slippage and commission modeling
- Backtest metrics: total return, Sharpe ratio, max drawdown, win rate, trade log
- Equity curve synced with main chart (baseline coloring: green above, red below initial capital)
- Buy & hold baseline overlay toggle (dashed line over equity curve for quick benchmark comparison)
- P&L distribution block: min/max/mean/median per side (gains vs losses), mean↔median toggle, inline histogram
- Strategy save/load/delete (localStorage)
- Chart disable toggle for lightweight backtesting of large datasets

### Live Trading Bots (Alpaca)
- Automated strategy execution with fund management and per-bot capital allocation
- **Long and short bots** — run both directions on the same ticker simultaneously
- Trailing stop management, OTO bracket orders, stop-loss detection
- Slippage tracking (expected vs actual fill price)
- Bot cards with direction badges (LONG/SHORT), background tint, always-visible equity sparkline
- Sparkline timescale toggle: local-per-card or aligned across all bots for cross-bot timing comparison
- Manual entry button, in-place config editing (allocation, strategy, data source)
- Bulk actions: Start All, Stop All, Stop and Close (with confirmation)

### Discovery
- Signal scanner: scan watchlist against strategy rules, one-click execution
- Performance comparison across tickers
- Dedicated page separated from live bot management (bot army pipeline planned)

### Manual Trading (Alpaca)
- Account overview (equity, cash, buying power, PDT status)
- Manual buy/sell with OTO bracket orders (stop loss)
- Positions table, order history, trade journal

### Data Providers
- **Yahoo Finance** — always available, free
- **Alpaca SIP** — paid real-time feed (requires API keys)
- **Alpaca IEX** — free real-time feed, narrower coverage
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

## Project Structure

```
backend/
  main.py              — app setup, CORS, mounts routers
  shared.py            — _fetch(), data providers, TTL cache
  models.py            — shared Pydantic models (StrategyRequest, etc.)
  journal.py           — trade journal logger
  signal_engine.py     — indicator computation, rule evaluation
  bot_manager.py       — bot lifecycle (add/start/stop/delete)
  bot_runner.py        — bot execution loop (polling, orders, stops)
  routes/
    data.py            — GET /api/ohlcv/{ticker}
    indicators.py      — GET /api/indicators/{ticker}
    backtest.py        — POST /api/backtest
    trading.py         — manual trading, positions, journal
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

- **Charts & Indicators** — portfolio equity chart (combined P&L across bots), equity curve macro mode, more indicators (ATR, Stochastic, VWAP), chart timeframe buttons, watchlist
- **Strategy Engine** — skip N trades after SL, pre-market / extended hours, more rule conditions, borrow cost estimation for live shorts
- **Strategy Summary** — expected value / trade + profit factor (avoids "avg loss > avg win looks like losing" misread)
- **Bots** — browser-local timezone in bot log, bot reordering/grouping
- **Discovery (research)** — candidate scanning, batch backtesting, AI-assisted parameter tuning, pipeline to spawn a bot army

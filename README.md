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
- Fixed stop loss and trailing stops (percentage or ATR-based, with profit activation threshold)
- Dynamic position sizing (reduce size after consecutive stop losses)
- Trading hours filter with skip ranges (e.g. skip lunch, last 15 min)
- Slippage and commission modeling
- Backtest metrics: total return, Sharpe ratio, max drawdown, win rate, trade log
- Equity curve synced with main chart (baseline coloring: green above, red below initial capital)
- Strategy save/load/delete (localStorage)
- Chart disable toggle for lightweight backtesting of large datasets

### Paper Trading (Alpaca)
- Account overview (equity, cash, buying power, PDT status)
- Manual buy/sell with OTO bracket orders (stop loss)
- Signal scanner: scan watchlist against strategy rules, one-click execution
- Positions table, order history, trade journal
- Performance comparison (actual trades vs backtest)

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

## In Progress

- **Live trading bot** — automated strategy execution with fund management, per-bot capital allocation, trailing stop management, and portfolio equity tracking. See [spec](docs/superpowers/specs/2026-04-08-live-trading-bot-spec.md) and [implementation plan](docs/superpowers/plans/2026-04-09-bot-implementation-plan.md).

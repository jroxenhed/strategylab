# StrategyLab

Interactive trading strategy backtester. Pick a ticker, date range, and build buy/sell rules using technical indicators — then run a backtest against historical data.

## Stack

- **Frontend**: React + TypeScript + Vite, lightweight-charts (TradingView), TanStack Query
- **Backend**: Python FastAPI, yfinance, pandas

## Features

- Candlestick chart with MACD, RSI, EMA (20/50/200), Bollinger Bands, Volume indicators
- SPY/QQQ comparison overlays (% change from start, left axis)
- Intraday intervals: 1m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo — with range warning for yfinance limits
- Synchronized pan/zoom and crosshair across main, MACD, and RSI panes
- Strategy rule builder with AND/OR logic (MACD crossover, RSI thresholds, price/EMA conditions)
- Backtest engine: total return, Sharpe ratio, max drawdown, win rate, trade log, equity curve
- Ticker search

## Running

```bash
./start.sh
```

Opens:
- Frontend: http://localhost:5173
- Backend API: http://localhost:8000

## Planned

- Preset timeframe buttons (1W / 1M / 3M / 1Y)
- More indicators (ATR, Stochastic, VWAP)
- More strategy rule conditions
- Watchlist

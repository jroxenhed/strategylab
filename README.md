# StrategyLab

Interactive trading strategy backtester. Pick a ticker, date range, and build buy/sell rules using technical indicators — then run a backtest against historical data.

## Stack

- **Frontend**: React + TypeScript + Vite, lightweight-charts (TradingView), TanStack Query
- **Backend**: Python FastAPI, yfinance, pandas

## Features

- Candlestick chart with MACD, RSI, EMA (20/50/200), Bollinger Bands indicators
- SPY/QQQ comparison overlays (% change from start)
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

## Known Issues

- SPY/QQQ overlay replaces ticker chart instead of overlaying — needs fix
- No intraday timeframes (1m, 5m, 15m, 30m) — needs adding
- Interactive chart timeframe adjustment (zoom/pan or preset buttons) — planned
- Volume indicator not wired to chart — needs fix

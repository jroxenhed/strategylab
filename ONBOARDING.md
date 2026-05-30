# Onboarding

First-time setup for StrategyLab. Should take ~5 minutes. After this, `./start.sh` is all you need.

## Prerequisites

- **Python 3.12** (older 3.x may work but isn't tested)
- **Node 20+** (developed on v25)
- **macOS or Linux** (Windows: use WSL)

Optional, only for live paper trading:
- **Alpaca** account → https://alpaca.markets (free, paper keys instant)
- **Interactive Brokers** paper account + IB Gateway running on `127.0.0.1:4002`

## First-time setup

```bash
git clone https://github.com/jroxenhed/strategylab.git
cd strategylab

# Backend: create venv + install Python deps
python3.12 -m venv backend/venv
backend/venv/bin/pip install -r backend/requirements.txt

# Frontend: install npm deps
cd frontend && npm install && cd ..

# Env file (only needed if using Alpaca or IBKR — Yahoo works without it)
cp .env.example backend/.env
# then edit backend/.env and fill in the keys you actually want
```

## Run it

```bash
./start.sh
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000 (Swagger at `/docs`)

`./start.sh` kills anything already on ports 8000/5173, so re-running it is safe.

## First things to try

1. **Default chart loads with AAPL daily.** Pan/zoom should sync across the price, MACD, and RSI panes.
2. **Run a backtest.** Sidebar → Add Rule → `RSI < 35` (buy) and `RSI > 65` (sell). Click **Backtest**. You should see ~29 trades on AAPL 2020-present.
3. **Try the node editor.** Open the Node Builder tab. Drag nodes from the palette, connect them, hit **Backtest**. (This is in active development — feedback welcome.)
4. **Live paper trading** (optional). Add Alpaca paper keys to `backend/.env`, restart, then **Add Bot** in the Live Trading tab.

## Data providers

Out of the box you get **Yahoo (yfinance)** — free, no setup, slightly delayed.

Add `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` to `backend/.env` to enable:
- `alpaca-iex` — real-time IEX feed, free tier
- `alpaca` — Alpaca SIP feed (requires paid Alpaca data subscription)

For IBKR data + execution, run IB Gateway and set `IBKR_HOST`/`IBKR_PORT`/`IBKR_CLIENT_ID`. **Uncheck "Read-Only API"** in Gateway → Configure → Settings → API or order submission returns Error 321.

## Troubleshooting

- **Backend hangs / 8000 not responding:** kill it and re-run `./start.sh`. The `--reload` flag occasionally wedges after long uptime or after a crashed optimizer/walk-forward worker leaves orphans behind.
- **`yfinance` returns empty bars on intraday intervals:** Yahoo enforces lookback limits — 1m=7d, 5m/15m/30m=60d, 1h=730d. `_fetch()` auto-clamps date ranges to fit, but very old intraday requests will still come back empty.
- **Chart panes misaligned vertically:** make sure all three charts have the same number of bars. Indicator warmup periods need whitespace entries (`{ time }` without `value`) — see `frontend/src/features/chart/Chart.tsx`.
- **IBKR `Error 162`:** usually means the requested historical bar size isn't available for that contract type. Try a different interval.

## Project layout

See the **Project Structure** section in [README.md](README.md). Key entry points:

- `backend/main.py` — FastAPI app, route mounts
- `backend/signal_engine.py` — rule evaluation (used by backtester + bot runner)
- `backend/routes/backtest.py` — backtest endpoint, cost model
- `frontend/src/App.tsx` — central state hub
- `frontend/src/features/chart/Chart.tsx` — main chart + pane sync (read before editing)
- `frontend/src/features/nodebuilder/` — node-based strategy editor (xyflow/react)

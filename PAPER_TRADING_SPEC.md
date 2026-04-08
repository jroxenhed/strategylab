# StrategyLab — Alpaca Paper Trading Integration Spec

## Overview

Connect StrategyLab to Alpaca's paper trading API so the app can:
1. View account balance and positions
2. Run the existing RSI/EMA strategy against live data
3. Submit paper orders when signals fire
4. Track performance vs backtest expectations

**Alpaca paper trading base URL:** `https://paper-api.alpaca.markets`
**Auth:** Existing env vars `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`
**SDK:** `alpaca-py` (already in requirements.txt)

---

## Architecture Changes

### New SDK client needed

The existing codebase uses `StockHistoricalDataClient` (read-only data). Paper trading requires a `TradingClient` for orders and account info:

```python
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus

trading_client = TradingClient(api_key, secret_key, paper=True)
```

### Signal logic extraction

Currently `eval_rule()` and `eval_rules()` live inside `routes/backtest.py`. These need to be extracted to a shared module so both the backtester and the live scanner use identical logic. This is critical — any divergence means backtest results won't match live behavior.

### New files to create

```
backend/
  shared.py              ← (existing) add TradingClient creation
  signal_engine.py       ← (new) extracted signal logic
  routes/
    backtest.py          ← (modify) import from signal_engine
    trading.py           ← (new) paper trading endpoints
frontend/
  src/
    components/
      PaperTrading.tsx   ← (new) paper trading dashboard tab
    api/
      trading.ts         ← (new) API client for trading endpoints
```

---

## Phase 1: Backend Foundation (~30 min Claude Code session)

**Goal:** Extract signal logic, add TradingClient, create account/positions endpoints.

### Task 1.1: Extract signal logic to `signal_engine.py`

Move these from `backtest.py` into a new `backend/signal_engine.py`:
- Indicator computation (RSI, MACD, EMA calculations)
- `eval_rule()` function
- `eval_rules()` function

The backtest route should then import from `signal_engine` instead of defining them inline. Run existing tests to confirm nothing breaks.

```python
# backend/signal_engine.py

import numpy as np
import pandas as pd
from typing import Optional
from pydantic import BaseModel

class Rule(BaseModel):
    indicator: str
    condition: str
    value: Optional[float] = None
    param: Optional[str] = None

def compute_indicators(close: pd.Series) -> dict[str, pd.Series]:
    """Compute all indicators from a close price series. Returns dict of named series."""
    # ... move indicator computation here ...

def eval_rule(rule: Rule, indicators: dict[str, pd.Series], i: int) -> bool:
    """Evaluate a single rule at bar index i."""
    # ... move eval_rule here ...

def eval_rules(rules: list[Rule], logic: str, indicators: dict[str, pd.Series], i: int) -> bool:
    """Evaluate a list of rules with AND/OR logic."""
    # ... move eval_rules here ...
```

Then update `backtest.py` to import from it.

### Task 1.2: Add TradingClient to `shared.py`

Add a function to create and expose the TradingClient:

```python
from alpaca.trading.client import TradingClient

def _create_trading_client():
    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    if not api_key or not secret_key:
        return None
    return TradingClient(api_key, secret_key, paper=True)

_trading_client = _create_trading_client()

def get_trading_client():
    if _trading_client is None:
        raise HTTPException(status_code=503, detail="Alpaca trading not configured")
    return _trading_client
```

### Task 1.3: Create `routes/trading.py` with account endpoints

```
GET  /api/trading/account     → account balance, buying power, equity
GET  /api/trading/positions   → open positions with unrealized P&L
GET  /api/trading/orders      → recent order history
```

Implementation sketch:

```python
from fastapi import APIRouter
from shared import get_trading_client

router = APIRouter(prefix="/api/trading")

@router.get("/account")
def get_account():
    client = get_trading_client()
    account = client.get_account()
    return {
        "equity": float(account.equity),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "portfolio_value": float(account.portfolio_value),
        "day_trade_count": account.daytrade_count,
        "pattern_day_trader": account.pattern_day_trader,
        "trading_blocked": account.trading_blocked,
        "account_blocked": account.account_blocked,
    }

@router.get("/positions")
def get_positions():
    client = get_trading_client()
    positions = client.get_all_positions()
    return [
        {
            "symbol": p.symbol,
            "qty": float(p.qty),
            "side": p.side.value,
            "avg_entry": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "market_value": float(p.market_value),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_pl_pct": float(p.unrealized_plpc) * 100,
        }
        for p in positions
    ]

@router.get("/orders")
def get_orders():
    client = get_trading_client()
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, limit=50))
    return [
        {
            "id": str(o.id),
            "symbol": o.symbol,
            "side": o.side.value,
            "qty": str(o.qty),
            "type": o.type.value,
            "status": o.status.value,
            "filled_avg_price": str(o.filled_avg_price) if o.filled_avg_price else None,
            "submitted_at": str(o.submitted_at),
            "filled_at": str(o.filled_at) if o.filled_at else None,
        }
        for o in orders
    ]
```

Register the router in `main.py`:
```python
from routes.trading import router as trading_router
app.include_router(trading_router)
```

### Task 1.4: Test it

```bash
# Quick smoke test from terminal
curl http://localhost:8000/api/trading/account
curl http://localhost:8000/api/trading/positions
curl http://localhost:8000/api/trading/orders
```

Should return your $100k paper account with no positions.

### Definition of Done — Phase 1
- [ ] `signal_engine.py` exists with extracted logic
- [ ] `backtest.py` imports from `signal_engine.py` and all existing backtests still work
- [ ] `TradingClient` created in `shared.py`
- [ ] `/api/trading/account` returns paper account info
- [ ] `/api/trading/positions` returns empty list
- [ ] `/api/trading/orders` returns empty list
- [ ] All committed to git

---

## Phase 2: Order Execution (~30 min Claude Code session)

**Goal:** Submit buy/sell paper orders and manage stop losses.

### Task 2.1: Buy endpoint

```
POST /api/trading/buy
Body: { "symbol": "AAPL", "qty": 10, "stop_loss_pct": 2.0 }
```

Implementation:
- Submit a market buy order
- If `stop_loss_pct` is provided, immediately submit a stop loss order after the buy fills
- Return order confirmation with fill price

```python
from alpaca.trading.requests import MarketOrderRequest, StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

@router.post("/buy")
def place_buy(symbol: str, qty: float, stop_loss_pct: float = None):
    client = get_trading_client()

    # Market buy
    order = client.submit_order(
        MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
    )

    result = {
        "order_id": str(order.id),
        "symbol": order.symbol,
        "qty": str(order.qty),
        "side": "buy",
        "status": order.status.value,
    }

    # Attach stop loss if requested
    if stop_loss_pct and stop_loss_pct > 0:
        # Note: in paper trading, market orders fill nearly instantly
        # For production, you'd wait for fill confirmation
        # For now, fetch current price as stop reference
        from shared import _fetch
        import pandas as pd
        current_price = float(
            _fetch(symbol, 
                   pd.Timestamp.now().strftime('%Y-%m-%d'),
                   pd.Timestamp.now().strftime('%Y-%m-%d'),
                   '1m', source='alpaca')['Close'].iloc[-1]
        )
        stop_price = round(current_price * (1 - stop_loss_pct / 100), 2)
        
        stop_order = client.submit_order(
            StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                stop_price=stop_price,
            )
        )
        result["stop_loss"] = {
            "order_id": str(stop_order.id),
            "stop_price": stop_price,
        }

    return result
```

### Task 2.2: Sell endpoint

```
POST /api/trading/sell
Body: { "symbol": "AAPL", "qty": 10 }
```

Also cancels any open stop loss orders for that symbol.

```python
@router.post("/sell")
def place_sell(symbol: str, qty: float = None):
    client = get_trading_client()

    # If no qty specified, close entire position
    if qty is None:
        client.close_position(symbol)
        return {"symbol": symbol, "action": "position_closed"}

    order = client.submit_order(
        MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
    )

    # Cancel any open stop loss orders for this symbol
    open_orders = client.get_orders(GetOrdersRequest(
        status=QueryOrderStatus.OPEN,
        symbols=[symbol],
    ))
    for o in open_orders:
        if o.side == OrderSide.SELL and o.type.value == "stop":
            client.cancel_order_by_id(o.id)

    return {
        "order_id": str(order.id),
        "symbol": symbol,
        "qty": str(order.qty),
        "side": "sell",
        "status": order.status.value,
    }
```

### Task 2.3: Cancel all / close all endpoints

```
POST /api/trading/close-all    → close all positions
POST /api/trading/cancel-all   → cancel all open orders
```

Useful safety valves.

### Task 2.4: Test with a manual paper trade

```bash
# Buy 10 shares of AAPL with 2% stop loss
curl -X POST "http://localhost:8000/api/trading/buy?symbol=AAPL&qty=10&stop_loss_pct=2"

# Check positions
curl http://localhost:8000/api/trading/positions

# Sell
curl -X POST "http://localhost:8000/api/trading/sell?symbol=AAPL&qty=10"
```

### Definition of Done — Phase 2
- [ ] `/api/trading/buy` submits market orders with optional stop loss
- [ ] `/api/trading/sell` sells and cancels associated stop losses
- [ ] `/api/trading/close-all` and `/api/trading/cancel-all` work
- [ ] Successfully executed a round-trip paper trade (buy → verify position → sell)
- [ ] All committed to git

---

## Phase 3: Live Signal Scanner (~45 min Claude Code session)

**Goal:** Scan tickers in real-time using the existing strategy logic and report which are signaling.

### Task 3.1: Scan endpoint

```
POST /api/trading/scan
Body: {
    "symbols": ["AAPL", "ENPH", "TSLA", "NVDA", "AMD"],
    "interval": "15m",
    "buy_rules": [...],    ← same Rule format as backtest
    "sell_rules": [...],
    "buy_logic": "AND",
    "sell_logic": "AND"
}
```

For each symbol:
1. Fetch recent bars from Alpaca (enough history to compute indicators — ~200 bars)
2. Run `compute_indicators()` on the close prices
3. Evaluate buy/sell rules on the most recent bar
4. Return signal status per symbol

```python
@router.post("/scan")
def scan_signals(req: ScanRequest):
    results = []
    for symbol in req.symbols:
        try:
            # Fetch enough bars for indicator warm-up
            end = pd.Timestamp.now(tz='UTC')
            start = end - pd.Timedelta(days=30)  # ~200 bars for 15m
            
            df = _fetch(symbol, start.strftime('%Y-%m-%d'), 
                       end.strftime('%Y-%m-%d'), req.interval, source='alpaca')
            
            indicators = compute_indicators(df["Close"])
            i = len(df) - 1  # evaluate on most recent bar
            
            buy_signal = eval_rules(req.buy_rules, req.buy_logic, indicators, i)
            sell_signal = eval_rules(req.sell_rules, req.sell_logic, indicators, i)
            
            results.append({
                "symbol": symbol,
                "signal": "BUY" if buy_signal else ("SELL" if sell_signal else "NONE"),
                "price": float(df["Close"].iloc[-1]),
                "rsi": float(indicators["rsi"].iloc[-1]),
                "ema50": float(indicators["ema50"].iloc[-1]),
                "last_bar": str(df.index[-1]),
            })
        except Exception as e:
            results.append({"symbol": symbol, "signal": "ERROR", "error": str(e)})
    
    return {"signals": results, "scanned_at": str(pd.Timestamp.now(tz='UTC'))}
```

### Task 3.2: Auto-execute mode (optional, off by default)

Add a flag to the scan that auto-submits orders when signals fire:

```
POST /api/trading/scan
Body: { ..., "auto_execute": true, "position_size_usd": 5000, "stop_loss_pct": 2.0 }
```

When `auto_execute` is true:
- BUY signal + no existing position → submit buy order
- SELL signal + existing position → submit sell order
- Log every auto-trade for audit

**Important safety guardrails:**
- Only works on paper trading (check `account.status`)
- Max position size capped
- Won't buy if already in a position for that symbol
- Returns what it did alongside the signals

### Task 3.3: Watchlist management

```
GET  /api/trading/watchlist           → get saved watchlist
POST /api/trading/watchlist           → save watchlist
Body: { "symbols": ["AAPL", "ENPH", ...] }
```

Store in a simple JSON file (`backend/data/watchlist.json`) so it persists between server restarts.

### Definition of Done — Phase 3
- [ ] `/api/trading/scan` evaluates live signals for multiple tickers
- [ ] Signal results include current RSI, price, EMA values
- [ ] Auto-execute mode places paper orders on signals (with guardrails)
- [ ] Watchlist save/load works
- [ ] All committed to git

---

## Phase 4: Frontend Dashboard (~45 min Claude Code session)

**Goal:** Add a "Paper Trading" tab to the StrategyLab UI.

### Task 4.1: API client (`frontend/src/api/trading.ts`)

Create typed fetch wrappers for all trading endpoints.

### Task 4.2: Paper Trading tab component

Add a new tab/page to the existing UI with these sections:

**Account Overview (top bar)**
- Equity, cash, buying power
- Daily P&L
- Auto-refresh every 30 seconds

**Open Positions (table)**
- Symbol, qty, avg entry, current price, unrealized P&L, P&L %
- "Close" button per position
- Color-coded P&L (green/red)

**Signal Scanner (main panel)**
- Watchlist input (comma-separated tickers or saved list)
- Interval selector (5m, 15m, 1h)
- Strategy rules selector (reuse the existing strategy builder or a preset dropdown)
- "Scan Now" button
- Results table: symbol, signal (BUY/SELL/NONE), price, RSI, EMA50
- BUY signals highlighted in green, SELL in red
- "Execute" button next to BUY/SELL signals for manual one-click trading

**Recent Orders (table)**
- Order history with status, fill price, timestamps
- Filterable by symbol

### Task 4.3: Wire it up

- Add the new tab to the main navigation
- Auto-refresh account and positions on a 30-second interval
- Scanner results should be one-click actionable

### Definition of Done — Phase 4
- [ ] Paper Trading tab visible in the app
- [ ] Account info displays correctly
- [ ] Positions table shows open positions with live P&L
- [ ] Scanner runs and displays signals
- [ ] Can execute trades from the UI
- [ ] Order history displays
- [ ] All committed to git

---

## Phase 5: Trade Journal & Performance Tracking (~30 min Claude Code session)

**Goal:** Log every paper trade and compare against backtest expectations.

### Task 5.1: Trade journal storage

Create `backend/data/trade_journal.json` — append-only log of every paper trade:

```json
{
    "trades": [
        {
            "id": "uuid",
            "timestamp": "2026-04-05T14:30:00Z",
            "symbol": "ENPH",
            "side": "buy",
            "qty": 50,
            "fill_price": 98.50,
            "signal": {
                "rsi": 21.5,
                "ema50_rising": true,
                "rule": "turns_up_below_22"
            },
            "stop_loss_price": 96.53,
            "source": "auto" | "manual"
        }
    ]
}
```

### Task 5.2: Performance comparison endpoint

```
GET /api/trading/performance?symbol=ENPH&start=2026-04-01
```

Returns:
- Paper trading actual P&L over the period
- Backtest expected P&L over the same period (run backtest on the fly)
- Comparison metrics: return difference, win rate difference, trade count

### Task 5.3: Performance view in frontend

Add to the Paper Trading tab:
- Paper P&L equity curve chart
- Table comparing paper vs backtest results per symbol
- Running totals

### Definition of Done — Phase 5
- [ ] Every trade (auto and manual) logged to journal
- [ ] Performance comparison endpoint works
- [ ] Frontend shows paper vs backtest comparison
- [ ] All committed to git

---

## Session Tips for Claude Code

1. **Start each session** by telling Claude Code: "Read PAPER_TRADING_SPEC.md, we're working on Phase X"
2. **One phase per session** keeps token usage manageable (~30-45 min each)
3. **Test after each phase** before moving on — each phase is independently useful
4. **Commit after each phase** with a clear message like `feat: phase 1 - trading foundation`
5. **If a session runs long**, it's fine to split a phase — the tasks within each phase are independent

## Important Notes

- **Paper trading only** — all code should explicitly set `paper=True` on TradingClient
- **Market hours** — Alpaca paper trading only works during US market hours (9:30 AM - 4:00 PM ET) for market orders. Extended hours available with `extended_hours=True`
- **Rate limits** — Alpaca free tier allows 200 requests/min. The scanner should batch requests sensibly
- **Your timezone** — Stockholm is UTC+2 (CEST), US market opens at 15:30 your time
- **Don't skip the signal extraction in Phase 1** — having backtest and live trading use different signal code is the #1 source of "it worked in backtest but not live" bugs

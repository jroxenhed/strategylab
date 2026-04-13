# IBKR Broker Integration

**Date:** 2026-04-13
**Status:** Approved

## Goal

Add Interactive Brokers as a full provider (data + trading) alongside existing Alpaca/Yahoo integrations. Data source and trading broker are independently selectable — data source per-request (existing pattern), broker globally.

## Architecture

### TradingProvider Protocol (`backend/broker.py`)

New file containing the broker abstraction:

```python
@dataclass
class OrderRequest:
    symbol: str
    qty: int
    side: str              # "buy" | "sell"
    time_in_force: str     # "day" | "gtc"
    order_type: str        # "market" | "stop"
    stop_price: float | None = None

@dataclass
class OrderResult:
    order_id: str
    symbol: str
    qty: float
    side: str
    status: str
    filled_avg_price: float | None = None
    filled_qty: float | None = None

class TradingProvider(Protocol):
    def get_account(self) -> dict
    def get_positions(self) -> list[dict]
    def get_orders(self, status: str = "all", symbols: list[str] | None = None, limit: int = 50) -> list[dict]
    def submit_order(self, order: OrderRequest) -> OrderResult
    def get_order(self, order_id: str) -> OrderResult
    def cancel_order(self, order_id: str) -> None
    def close_position(self, symbol: str) -> OrderResult
    def close_all_positions(self) -> None
    def cancel_all_orders(self) -> None
    def get_latest_price(self, symbol: str) -> float
```

Global broker state:
- `_active_broker: str` — `"alpaca"` or `"ibkr"`, defaults from `ACTIVE_BROKER` env var (fallback `"alpaca"`)
- `_trading_providers: dict[str, TradingProvider]` — registered at startup
- `get_trading_provider() -> TradingProvider` — returns the active broker
- `set_active_broker(name: str)` — runtime switch
- `get_available_brokers() -> list[str]` — what's configured

### AlpacaTradingProvider (`backend/broker.py`)

Wraps existing Alpaca trading code into the protocol. Translates Alpaca SDK objects to/from the normalized types. All `from alpaca.*` imports live inside this class only. Includes the existing retry-on-stale-connection logic.

### IBKRTradingProvider (`backend/broker.py`)

Uses `ib_insync` to implement the protocol:
- Connection: `IB().connect(host, port, clientId)` — single shared instance
- Orders: `ib.placeOrder()` with `MarketOrder` / `StopOrder`
- Positions: `ib.positions()`, normalizes qty sign to side string
- Account: `ib.accountSummary()`
- Contracts: `Stock(symbol, 'SMART', 'USD')` for US equities
- Reconnection: wrap calls with reconnect-on-failure

### IBKRDataProvider (`backend/shared.py`)

Implements existing `DataProvider` protocol using `ib.reqHistoricalData()`:
- Interval mapping: `"1m"` -> `"1 min"`, `"5m"` -> `"5 mins"`, `"1h"` -> `"1 hour"`, `"1d"` -> `"1 day"`, etc.
- Returns standard DataFrame (Open/High/Low/Close/Volume, DatetimeIndex)
- Shares the same `ib_insync.IB()` connection as the trading provider

### IBKR Connection Management

Single `ib_insync.IB()` instance, created at startup if IBKR env vars are present:
- `IBKR_HOST` — default `127.0.0.1`
- `IBKR_PORT` — default `4002` (paper Gateway)
- `IBKR_CLIENT_ID` — default `1`

If Gateway isn't running or env vars absent, IBKR providers are not registered (same pattern as Alpaca).

### Stop-Loss Handling

IBKR doesn't have Alpaca's OTO bracket class. Stop-losses are placed as separate orders after entry fills. The bot runner already manages stops via polling for shorts — same approach used for all IBKR trades regardless of direction.

## Consumer Refactoring

### `bot_runner.py`
- Replace `get_trading_client()` with `get_trading_provider()`
- Replace all Alpaca SDK calls (`MarketOrderRequest`, `OrderSide`, etc.) with `OrderRequest` / `OrderResult`
- Remove `from alpaca.*` imports
- The `_ALPACA_AVAILABLE` guard becomes `provider is not None` check

### `routes/trading.py`
- Same treatment — all endpoints call through `TradingProvider`
- `_alpaca_call()` retry wrapper removed (retry logic moves into providers)
- `_wait_for_fill()` uses `provider.get_order()` instead of Alpaca SDK

### `bot_manager.py`
- `get_trading_client()` -> `get_trading_provider()`
- `client.close_position()` -> `provider.close_position()`
- Alpaca imports removed

## API Changes

### New Endpoints
- `GET /api/broker` — `{broker: "alpaca", available: ["alpaca", "ibkr"]}`
- `PUT /api/broker` — `{broker: "ibkr"}` — switches active broker at runtime

### Modified Endpoints
- `GET /api/providers` — now also returns `"ibkr"` in the data providers list when configured

## Frontend Changes

- `DataSource` type: add `'ibkr'`
- **Chart page sidebar** — add IBKR to existing "Data Source" toggle (Yahoo / Alpaca / IBKR). Controls chart and backtest data source. Already exists, just extend.
- **Bot page AccountBar** — add "Broker" selector (Alpaca / IBKR). Controls where orders are placed. Calls `PUT /api/broker`. Account metrics (Equity, Cash, etc.) re-fetch from the newly selected broker on change.

## IBKR-Specific Notes

- **Position side:** positive qty = long, negative qty = short. Provider normalizes.
- **Fill info:** `ib_insync` has event-driven fills, cleaner than Alpaca polling.
- **Reconnection:** Gateway drops connections occasionally. Provider reconnects automatically.
- **Market data:** Paper accounts have delayed data unless subscribed. User will use Yahoo/Alpaca for charting, IBKR primarily for order execution.
- **Interval mapping:** `{"1m": "1 min", "5m": "5 mins", "15m": "15 mins", "30m": "30 mins", "1h": "1 hour", "1d": "1 day", "1wk": "1 week", "1mo": "1 month"}`

## Configuration

All via `backend/.env`:
```
IBKR_HOST=127.0.0.1
IBKR_PORT=4002
IBKR_CLIENT_ID=1
ACTIVE_BROKER=alpaca
```

## Dependencies

- `ib_insync` added to requirements

# IBKR Broker Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add IBKR as a full data + trading provider behind a `TradingProvider` abstraction, with independent data source and broker selection.

**Architecture:** New `broker.py` defines `TradingProvider` protocol with normalized types (`OrderRequest`, `OrderResult`). `AlpacaTradingProvider` wraps existing Alpaca code, `IBKRTradingProvider` uses `ib_insync`. All consumers (`bot_runner.py`, `routes/trading.py`, `bot_manager.py`) call through the abstraction. Frontend gets IBKR in data source toggle and a broker selector on the bot page.

**Tech Stack:** Python, `ib_insync`, FastAPI, React/TypeScript

**Spec:** `docs/superpowers/specs/2026-04-13-ibkr-broker-integration-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `backend/broker.py` | `OrderRequest`, `OrderResult`, `TradingProvider` protocol, `AlpacaTradingProvider`, `IBKRTradingProvider`, global broker registry |
| Create | `backend/tests/test_broker.py` | Tests for broker abstraction and providers |
| Modify | `backend/shared.py` | Add `IBKRDataProvider`, IBKR connection setup, register in provider dict |
| Modify | `backend/bot_runner.py` | Replace Alpaca SDK calls with `TradingProvider` |
| Modify | `backend/bot_manager.py` | Replace `get_trading_client()` with `get_trading_provider()` |
| Modify | `backend/routes/trading.py` | Replace Alpaca SDK calls with `TradingProvider` |
| Modify | `backend/routes/providers.py` | Add `GET /api/broker`, `PUT /api/broker` endpoints |
| Modify | `backend/routes/bots.py` | No direct changes (calls through bot_manager) |
| Modify | `backend/main.py` | Initialize IBKR connection in lifespan, register providers |
| Modify | `backend/requirements.txt` | Add `ib_insync` |
| Modify | `frontend/src/shared/types/index.ts` | Add `'ibkr'` to `DataSource` |
| Modify | `frontend/src/features/sidebar/Sidebar.tsx` | Add IBKR to data source toggle |
| Modify | `frontend/src/features/trading/AccountBar.tsx` | Add broker selector |
| Modify | `frontend/src/shared/hooks/useOHLCV.ts` | Add `useBroker` hook |
| Modify | `frontend/src/api/trading.ts` | Add `fetchBroker`, `setBroker` API calls |
| Modify | `backend/tests/test_providers.py` | Add IBKR data provider tests |

---

## Tasks

### Task 1: Core Types and TradingProvider Protocol

**Files:**
- Create: `backend/broker.py`
- Create: `backend/tests/test_broker.py`

- [ ] **Step 1: Add `ib_insync` to requirements**

```
# append to backend/requirements.txt
ib_insync==0.9.86
```

Run: `cd backend && pip install -r requirements.txt`

- [ ] **Step 2: Write test for OrderRequest and OrderResult**

```python
# backend/tests/test_broker.py
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))


def test_order_request_defaults():
    from broker import OrderRequest
    req = OrderRequest(symbol="AAPL", qty=10, side="buy")
    assert req.time_in_force == "day"
    assert req.order_type == "market"
    assert req.stop_price is None
    assert req.account_id is None


def test_order_result_fields():
    from broker import OrderResult
    res = OrderResult(order_id="123", symbol="AAPL", qty=10, side="buy", status="filled",
                      filled_avg_price=150.0, filled_qty=10)
    assert res.filled_avg_price == 150.0
    assert res.status == "filled"
```

Run: `cd backend && python -m pytest tests/test_broker.py -v`
Expected: FAIL — `broker` module doesn't exist yet

- [ ] **Step 3: Implement OrderRequest, OrderResult, TradingProvider protocol**

```python
# backend/broker.py
"""
broker.py — Trading provider abstraction.

Defines the TradingProvider protocol and normalized order types.
Concrete implementations: AlpacaTradingProvider, IBKRTradingProvider.
Global broker registry with runtime switching.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


@dataclass
class OrderRequest:
    symbol: str
    qty: int
    side: str                            # "buy" | "sell"
    time_in_force: str = "day"           # "day" | "gtc"
    order_type: str = "market"           # "market" | "stop"
    stop_price: float | None = None
    account_id: str | None = None        # None = default account


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
    def get_account(self) -> dict: ...
    def get_positions(self) -> list[dict]: ...
    def get_orders(self, status: str = "all", symbols: list[str] | None = None, limit: int = 50) -> list[dict]: ...
    def submit_order(self, order: OrderRequest) -> OrderResult: ...
    def get_order(self, order_id: str) -> OrderResult: ...
    def cancel_order(self, order_id: str) -> None: ...
    def close_position(self, symbol: str) -> OrderResult: ...
    def close_all_positions(self) -> None: ...
    def cancel_all_orders(self) -> None: ...
    def get_latest_price(self, symbol: str) -> float: ...


# ---------------------------------------------------------------------------
# Global broker registry
# ---------------------------------------------------------------------------

_trading_providers: dict[str, TradingProvider] = {}
_active_broker: str = os.environ.get("ACTIVE_BROKER", "alpaca")


def register_trading_provider(name: str, provider: TradingProvider) -> None:
    _trading_providers[name] = provider


def get_trading_provider() -> TradingProvider:
    provider = _trading_providers.get(_active_broker)
    if provider is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=f"Broker '{_active_broker}' not configured")
    return provider


def get_available_brokers() -> list[str]:
    return list(_trading_providers.keys())


def get_active_broker() -> str:
    return _active_broker


def set_active_broker(name: str) -> None:
    global _active_broker
    if name not in _trading_providers:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Unknown broker: {name}. Available: {list(_trading_providers.keys())}")
    _active_broker = name
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_broker.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Write test for broker registry**

Append to `backend/tests/test_broker.py`:

```python
def test_broker_registry():
    from broker import (
        register_trading_provider, get_trading_provider,
        get_available_brokers, get_active_broker, set_active_broker,
        _trading_providers, OrderRequest, OrderResult,
    )
    import broker

    # Clear state
    _trading_providers.clear()

    class FakeProvider:
        def get_account(self): return {"equity": 100000}
        def get_positions(self): return []
        def get_orders(self, status="all", symbols=None, limit=50): return []
        def submit_order(self, order): return OrderResult("1", order.symbol, order.qty, order.side, "filled")
        def get_order(self, order_id): return OrderResult(order_id, "AAPL", 10, "buy", "filled")
        def cancel_order(self, order_id): pass
        def close_position(self, symbol): return OrderResult("2", symbol, 10, "sell", "filled")
        def close_all_positions(self): pass
        def cancel_all_orders(self): pass
        def get_latest_price(self, symbol): return 150.0

    register_trading_provider("fake", FakeProvider())
    assert "fake" in get_available_brokers()

    broker._active_broker = "fake"
    provider = get_trading_provider()
    assert provider.get_account() == {"equity": 100000}

    # Unknown broker raises
    from fastapi import HTTPException
    import pytest
    broker._active_broker = "nonexistent"
    with pytest.raises(HTTPException) as exc_info:
        get_trading_provider()
    assert exc_info.value.status_code == 503
```

Run: `cd backend && python -m pytest tests/test_broker.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/broker.py backend/tests/test_broker.py backend/requirements.txt
git commit -m "feat: add TradingProvider protocol and broker registry"
```

### Task 2: AlpacaTradingProvider

Wraps existing Alpaca trading code into the TradingProvider protocol. All Alpaca SDK imports stay contained inside this class.

**Files:**
- Modify: `backend/broker.py` (append class)
- Modify: `backend/tests/test_broker.py` (append tests)

- [ ] **Step 1: Write test for AlpacaTradingProvider**

Append to `backend/tests/test_broker.py`:

```python
def test_alpaca_provider_submit_order():
    """AlpacaTradingProvider.submit_order() translates OrderRequest to Alpaca SDK."""
    from broker import AlpacaTradingProvider, OrderRequest, OrderResult

    class FakeOrder:
        id = "order-123"
        symbol = "AAPL"
        qty = "10"
        side = type("S", (), {"value": "buy"})()
        status = type("S", (), {"value": "accepted"})()
        filled_avg_price = None
        filled_qty = None

    class FakeClient:
        def submit_order(self, req):
            return FakeOrder()

    provider = AlpacaTradingProvider(FakeClient(), FakeClient())
    result = provider.submit_order(OrderRequest(symbol="AAPL", qty=10, side="buy"))
    assert isinstance(result, OrderResult)
    assert result.order_id == "order-123"
    assert result.side == "buy"


def test_alpaca_provider_get_positions():
    from broker import AlpacaTradingProvider

    class FakePos:
        symbol = "AAPL"
        qty = "10"
        side = type("S", (), {"value": "long"})()
        avg_entry_price = "150.00"
        current_price = "155.00"
        market_value = "1550.00"
        unrealized_pl = "50.00"
        unrealized_plpc = "0.0333"

    class FakeClient:
        def get_all_positions(self):
            return [FakePos()]

    provider = AlpacaTradingProvider(FakeClient(), FakeClient())
    positions = provider.get_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"
    assert positions[0]["side"] == "long"
    assert positions[0]["qty"] == 10.0


def test_alpaca_provider_get_account():
    from broker import AlpacaTradingProvider

    class FakeAccount:
        equity = "100000.00"
        cash = "50000.00"
        buying_power = "200000.00"
        portfolio_value = "100000.00"
        daytrade_count = 0
        pattern_day_trader = False
        trading_blocked = False
        account_blocked = False

    class FakeClient:
        def get_account(self):
            return FakeAccount()

    provider = AlpacaTradingProvider(FakeClient(), FakeClient())
    account = provider.get_account()
    assert account["equity"] == 100000.0
    assert account["cash"] == 50000.0
```

Run: `cd backend && python -m pytest tests/test_broker.py -v`
Expected: FAIL — `AlpacaTradingProvider` doesn't exist yet

- [ ] **Step 2: Implement AlpacaTradingProvider**

Append to `backend/broker.py`:

```python
# ---------------------------------------------------------------------------
# Alpaca implementation
# ---------------------------------------------------------------------------

class AlpacaTradingProvider:
    """TradingProvider backed by Alpaca SDK."""

    def __init__(self, trading_client, data_client=None):
        self._client = trading_client
        self._data_client = data_client  # StockHistoricalDataClient for get_latest_price

    def _retry(self, fn, *args, **kwargs):
        """Retry once on stale connection errors."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            from shared import is_retryable_error
            if is_retryable_error(e):
                return fn(*args, **kwargs)
            raise

    def get_account(self) -> dict:
        account = self._retry(self._client.get_account)
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

    def get_positions(self) -> list[dict]:
        positions = self._retry(self._client.get_all_positions)
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

    def get_orders(self, status: str = "all", symbols: list[str] | None = None, limit: int = 50) -> list[dict]:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        status_map = {
            "all": QueryOrderStatus.ALL,
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
        }
        req = GetOrdersRequest(
            status=status_map.get(status, QueryOrderStatus.ALL),
            symbols=symbols,
            limit=limit,
        )
        orders = self._retry(self._client.get_orders, req)
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

    def submit_order(self, order: OrderRequest) -> OrderResult:
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

        side = OrderSide.BUY if order.side == "buy" else OrderSide.SELL
        tif = TimeInForce.DAY if order.time_in_force == "day" else TimeInForce.GTC

        kwargs = dict(
            symbol=order.symbol,
            qty=order.qty,
            side=side,
            time_in_force=tif,
        )

        if order.order_type == "stop" and order.stop_price:
            # Place as OTO bracket (market entry + stop-loss leg)
            kwargs["order_class"] = OrderClass.OTO
            kwargs["stop_loss"] = StopLossRequest(stop_price=order.stop_price)

        o = self._retry(self._client.submit_order, MarketOrderRequest(**kwargs))
        return OrderResult(
            order_id=str(o.id),
            symbol=o.symbol,
            qty=float(o.qty),
            side=o.side.value,
            status=o.status.value,
            filled_avg_price=float(o.filled_avg_price) if o.filled_avg_price else None,
            filled_qty=float(o.filled_qty) if o.filled_qty else None,
        )

    def get_order(self, order_id: str) -> OrderResult:
        o = self._retry(self._client.get_order_by_id, order_id)
        return OrderResult(
            order_id=str(o.id),
            symbol=o.symbol,
            qty=float(o.qty),
            side=o.side.value,
            status=o.status.value,
            filled_avg_price=float(o.filled_avg_price) if o.filled_avg_price else None,
            filled_qty=float(o.filled_qty) if o.filled_qty else None,
        )

    def cancel_order(self, order_id: str) -> None:
        self._retry(self._client.cancel_order_by_id, order_id)

    def close_position(self, symbol: str) -> OrderResult:
        resp = self._retry(self._client.close_position, symbol)
        order_id = str(getattr(resp, 'id', ''))
        return OrderResult(
            order_id=order_id,
            symbol=symbol,
            qty=0,  # filled qty unknown until polled
            side="sell",
            status="pending",
        )

    def close_all_positions(self) -> None:
        self._retry(self._client.close_all_positions, cancel_orders=True)

    def cancel_all_orders(self) -> None:
        self._retry(self._client.cancel_orders)

    def get_latest_price(self, symbol: str) -> float:
        if self._data_client is None:
            raise ValueError("Alpaca data client not configured")
        from alpaca.data.requests import StockLatestTradeRequest
        latest = self._data_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol)
        )
        return float(latest[symbol].price)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_broker.py -v`
Expected: PASS (6 tests)

- [ ] **Step 4: Commit**

```bash
git add backend/broker.py backend/tests/test_broker.py
git commit -m "feat: add AlpacaTradingProvider implementation"
```

### Task 3: IBKRTradingProvider

Implements `TradingProvider` using `ib_insync`. Single shared `IB()` connection with reconnect-on-failure.

**Files:**
- Modify: `backend/broker.py` (append class)
- Modify: `backend/tests/test_broker.py` (append tests)

- [ ] **Step 1: Write tests for IBKRTradingProvider**

Append to `backend/tests/test_broker.py`:

```python
def test_ibkr_provider_submit_market_order():
    """IBKRTradingProvider.submit_order() translates to ib_insync MarketOrder."""
    from broker import IBKRTradingProvider, OrderRequest, OrderResult
    from unittest.mock import MagicMock

    ib = MagicMock()
    trade = MagicMock()
    trade.order.orderId = 42
    trade.order.action = "BUY"
    trade.order.totalQuantity = 10
    trade.orderStatus.status = "Submitted"
    trade.orderStatus.avgFillPrice = 0.0
    trade.orderStatus.filled = 0.0
    ib.placeOrder.return_value = trade

    provider = IBKRTradingProvider(ib)
    result = provider.submit_order(OrderRequest(symbol="AAPL", qty=10, side="buy"))
    assert isinstance(result, OrderResult)
    assert result.order_id == "42"
    assert result.side == "buy"
    ib.placeOrder.assert_called_once()


def test_ibkr_provider_get_positions():
    from broker import IBKRTradingProvider
    from unittest.mock import MagicMock

    ib = MagicMock()
    pos = MagicMock()
    pos.contract.symbol = "AAPL"
    pos.position = 10.0  # positive = long
    pos.avgCost = 150.0
    pos.account = "DU12345"
    ib.positions.return_value = [pos]

    # Mock reqMktData for current price
    ticker_mock = MagicMock()
    ticker_mock.marketPrice.return_value = 155.0
    ib.reqMktData.return_value = ticker_mock

    provider = IBKRTradingProvider(ib)
    positions = provider.get_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"
    assert positions[0]["side"] == "long"
    assert positions[0]["qty"] == 10.0
    assert positions[0]["avg_entry"] == 150.0


def test_ibkr_provider_get_account():
    from broker import IBKRTradingProvider
    from unittest.mock import MagicMock

    ib = MagicMock()
    # accountSummary returns list of AccountValue objects
    av = lambda tag, val: MagicMock(tag=tag, value=str(val))
    ib.accountSummary.return_value = [
        av("NetLiquidation", 100000),
        av("TotalCashValue", 50000),
        av("BuyingPower", 200000),
    ]

    provider = IBKRTradingProvider(ib)
    account = provider.get_account()
    assert account["equity"] == 100000.0
    assert account["cash"] == 50000.0
    assert account["buying_power"] == 200000.0


def test_ibkr_provider_position_side_from_sign():
    """Negative qty = short, positive = long."""
    from broker import IBKRTradingProvider
    from unittest.mock import MagicMock

    ib = MagicMock()
    long_pos = MagicMock()
    long_pos.contract.symbol = "AAPL"
    long_pos.position = 10.0
    long_pos.avgCost = 150.0
    long_pos.account = "DU12345"
    short_pos = MagicMock()
    short_pos.contract.symbol = "TSLA"
    short_pos.position = -5.0
    short_pos.avgCost = 200.0
    short_pos.account = "DU12345"
    ib.positions.return_value = [long_pos, short_pos]

    provider = IBKRTradingProvider(ib)
    positions = provider.get_positions()
    assert positions[0]["side"] == "long"
    assert positions[1]["side"] == "short"
    assert positions[1]["qty"] == 5.0  # absolute value
```

Run: `cd backend && python -m pytest tests/test_broker.py -v -k ibkr`
Expected: FAIL — `IBKRTradingProvider` doesn't exist yet

- [ ] **Step 2: Implement IBKRTradingProvider**

Append to `backend/broker.py`:

```python
# ---------------------------------------------------------------------------
# IBKR implementation
# ---------------------------------------------------------------------------

class IBKRTradingProvider:
    """TradingProvider backed by ib_insync."""

    def __init__(self, ib, default_account: str | None = None):
        self._ib = ib
        self._default_account = default_account or os.environ.get("IBKR_DEFAULT_ACCOUNT", "").strip() or None

    def _ensure_connected(self):
        """Reconnect if Gateway dropped the connection."""
        if not self._ib.isConnected():
            host = os.environ.get("IBKR_HOST", "127.0.0.1")
            port = int(os.environ.get("IBKR_PORT", "4002"))
            client_id = int(os.environ.get("IBKR_CLIENT_ID", "1"))
            self._ib.connect(host, port, clientId=client_id)

    def _contract(self, symbol: str):
        from ib_insync import Stock
        return Stock(symbol, "SMART", "USD")

    def _resolve_account(self, account_id: str | None = None) -> str | None:
        return account_id or self._default_account

    def get_account(self, account_id: str | None = None) -> dict:
        self._ensure_connected()
        acct = self._resolve_account(account_id)
        summary = self._ib.accountSummary(acct) if acct else self._ib.accountSummary()
        values = {item.tag: item.value for item in summary}
        return {
            "equity": float(values.get("NetLiquidation", 0)),
            "cash": float(values.get("TotalCashValue", 0)),
            "buying_power": float(values.get("BuyingPower", 0)),
            "portfolio_value": float(values.get("NetLiquidation", 0)),
            "day_trade_count": 0,
            "pattern_day_trader": False,
            "trading_blocked": False,
            "account_blocked": False,
        }

    def get_positions(self, account_id: str | None = None) -> list[dict]:
        self._ensure_connected()
        acct = self._resolve_account(account_id)
        positions = self._ib.positions(acct) if acct else self._ib.positions()
        result = []
        for p in positions:
            qty = float(p.position)
            side = "long" if qty > 0 else "short"
            result.append({
                "symbol": p.contract.symbol,
                "qty": abs(qty),
                "side": side,
                "avg_entry": float(p.avgCost),
                "current_price": 0.0,  # filled by caller if needed
                "market_value": 0.0,
                "unrealized_pl": 0.0,
                "unrealized_pl_pct": 0.0,
            })
        return result

    def get_orders(self, status: str = "all", symbols: list[str] | None = None, limit: int = 50) -> list[dict]:
        self._ensure_connected()
        if status == "open":
            orders = self._ib.openOrders()
        else:
            orders = self._ib.reqAllOpenOrders()
        result = []
        for trade in self._ib.trades():
            o = trade.order
            s = trade.orderStatus
            sym = trade.contract.symbol
            if symbols and sym not in symbols:
                continue
            if status == "open" and s.status in ("Filled", "Cancelled", "Inactive"):
                continue
            if status == "closed" and s.status not in ("Filled",):
                continue
            result.append({
                "id": str(o.orderId),
                "symbol": sym,
                "side": "buy" if o.action == "BUY" else "sell",
                "qty": str(int(o.totalQuantity)),
                "type": o.orderType.lower(),
                "status": s.status.lower(),
                "filled_avg_price": str(s.avgFillPrice) if s.avgFillPrice else None,
                "submitted_at": "",
                "filled_at": "",
            })
            if len(result) >= limit:
                break
        return result

    def submit_order(self, order: OrderRequest) -> OrderResult:
        from ib_insync import MarketOrder, StopOrder
        self._ensure_connected()

        contract = self._contract(order.symbol)
        action = "BUY" if order.side == "buy" else "SELL"

        if order.order_type == "stop" and order.stop_price:
            ib_order = StopOrder(action, order.qty, order.stop_price)
        else:
            ib_order = MarketOrder(action, order.qty)

        acct = self._resolve_account(order.account_id)
        if acct:
            ib_order.account = acct

        trade = self._ib.placeOrder(contract, ib_order)
        return OrderResult(
            order_id=str(trade.order.orderId),
            symbol=order.symbol,
            qty=float(order.qty),
            side=order.side,
            status=trade.orderStatus.status.lower(),
            filled_avg_price=float(trade.orderStatus.avgFillPrice) if trade.orderStatus.avgFillPrice else None,
            filled_qty=float(trade.orderStatus.filled) if trade.orderStatus.filled else None,
        )

    def get_order(self, order_id: str) -> OrderResult:
        self._ensure_connected()
        for trade in self._ib.trades():
            if str(trade.order.orderId) == order_id:
                o = trade.order
                s = trade.orderStatus
                return OrderResult(
                    order_id=order_id,
                    symbol=trade.contract.symbol,
                    qty=float(o.totalQuantity),
                    side="buy" if o.action == "BUY" else "sell",
                    status=s.status.lower(),
                    filled_avg_price=float(s.avgFillPrice) if s.avgFillPrice else None,
                    filled_qty=float(s.filled) if s.filled else None,
                )
        raise ValueError(f"Order {order_id} not found")

    def cancel_order(self, order_id: str) -> None:
        self._ensure_connected()
        for trade in self._ib.trades():
            if str(trade.order.orderId) == order_id:
                self._ib.cancelOrder(trade.order)
                return
        raise ValueError(f"Order {order_id} not found")

    def close_position(self, symbol: str) -> OrderResult:
        self._ensure_connected()
        for p in self._ib.positions():
            if p.contract.symbol == symbol:
                qty = float(p.position)
                side = "SELL" if qty > 0 else "BUY"
                from ib_insync import MarketOrder
                order = MarketOrder(side, abs(qty))
                acct = self._resolve_account()
                if acct:
                    order.account = acct
                trade = self._ib.placeOrder(p.contract, order)
                return OrderResult(
                    order_id=str(trade.order.orderId),
                    symbol=symbol,
                    qty=abs(qty),
                    side=side.lower(),
                    status="pending",
                )
        raise ValueError(f"No position for {symbol}")

    def close_all_positions(self) -> None:
        self._ensure_connected()
        for p in self._ib.positions():
            qty = float(p.position)
            if qty == 0:
                continue
            side = "SELL" if qty > 0 else "BUY"
            from ib_insync import MarketOrder
            order = MarketOrder(side, abs(qty))
            self._ib.placeOrder(p.contract, order)

    def cancel_all_orders(self) -> None:
        self._ensure_connected()
        self._ib.reqGlobalCancel()

    def get_latest_price(self, symbol: str) -> float:
        self._ensure_connected()
        contract = self._contract(symbol)
        ticker = self._ib.reqMktData(contract, '', False, False)
        self._ib.sleep(1)  # wait for data
        price = ticker.marketPrice()
        self._ib.cancelMktData(contract)
        if price != price:  # NaN check
            raise ValueError(f"No market data for {symbol}")
        return float(price)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_broker.py -v -k ibkr`
Expected: PASS (4 tests)

- [ ] **Step 4: Commit**

```bash
git add backend/broker.py backend/tests/test_broker.py
git commit -m "feat: add IBKRTradingProvider implementation"
```

### Task 4: IBKRDataProvider + Connection Management + Provider Registration

Adds `IBKRDataProvider` to `shared.py` (implements existing `DataProvider` protocol), IBKR connection setup, and registers both data + trading providers at startup.

**Files:**
- Modify: `backend/shared.py` (add IBKRDataProvider class, IBKR connection init, register in provider dicts)
- Modify: `backend/main.py` (initialize IBKR in lifespan)

- [ ] **Step 1: Add IBKRDataProvider to shared.py**

Insert after the `AlpacaProvider` class (after line ~123):

```python
# ---------------------------------------------------------------------------
# IBKR data provider
# ---------------------------------------------------------------------------

_IBKR_INTERVAL_MAP = {
    "1m": "1 min", "5m": "5 mins", "15m": "15 mins", "30m": "30 mins",
    "1h": "1 hour", "1d": "1 day", "1wk": "1 week", "1mo": "1 month",
}

_IBKR_UNSUPPORTED = {"2m", "60m", "90m"}


class IBKRDataProvider:
    """DataProvider backed by ib_insync reqHistoricalData."""

    def __init__(self, ib):
        self._ib = ib

    def fetch(self, ticker: str, start: str, end: str, interval: str) -> pd.DataFrame:
        if interval in _IBKR_UNSUPPORTED:
            raise HTTPException(
                status_code=400,
                detail=f"Interval {interval} not supported by IBKR"
            )

        bar_size = _IBKR_INTERVAL_MAP.get(interval)
        if bar_size is None:
            raise HTTPException(
                status_code=400,
                detail=f"Interval {interval} not supported by IBKR"
            )

        from ib_insync import Stock
        from datetime import datetime

        contract = Stock(ticker, "SMART", "USD")
        end_dt = datetime.strptime(end, "%Y-%m-%d")

        # Calculate duration string from start/end
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        days = (end_dt - start_dt).days
        if days <= 1:
            duration = "1 D"
        elif days <= 365:
            duration = f"{days} D"
        else:
            years = max(1, days // 365)
            duration = f"{years} Y"

        if not self._ib.isConnected():
            raise HTTPException(status_code=503, detail="IBKR Gateway not connected")

        bars = self._ib.reqHistoricalData(
            contract,
            endDateTime=end_dt.strftime("%Y%m%d %H:%M:%S"),
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
        )

        if not bars:
            raise HTTPException(status_code=404, detail=f"No IBKR data for {ticker}")

        rows = []
        for bar in bars:
            rows.append({
                "Open": bar.open,
                "High": bar.high,
                "Low": bar.low,
                "Close": bar.close,
                "Volume": int(bar.volume),
                "timestamp": bar.date,
            })

        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df.pop("timestamp"))
        return df
```

- [ ] **Step 2: Add IBKR connection initialization to shared.py**

Insert after the Alpaca provider registration block (after line ~161):

```python
# ---------------------------------------------------------------------------
# IBKR connection (shared between data + trading providers)
# ---------------------------------------------------------------------------

_ibkr_ib = None  # ib_insync.IB instance, set if Gateway is reachable


def _create_ibkr_connection():
    """Connect to IBKR Gateway. Returns IB instance or None if unavailable."""
    host = os.environ.get("IBKR_HOST", "").strip()
    port = os.environ.get("IBKR_PORT", "").strip()
    # Only attempt if at least one IBKR env var is explicitly set
    if not host and not port:
        return None
    host = host or "127.0.0.1"
    port = int(port or "4002")
    client_id = int(os.environ.get("IBKR_CLIENT_ID", "1"))
    try:
        from ib_insync import IB
        ib = IB()
        ib.connect(host, port, clientId=client_id)
        print(f"[IBKR] Connected to Gateway at {host}:{port}")
        return ib
    except Exception as e:
        print(f"[IBKR] Gateway not available ({host}:{port}): {e}")
        return None


def get_ibkr_connection():
    """Return the shared IB instance (may be None)."""
    return _ibkr_ib


def init_ibkr():
    """Initialize IBKR connection and register providers. Called from main.py lifespan."""
    global _ibkr_ib
    _ibkr_ib = _create_ibkr_connection()
    if _ibkr_ib is not None:
        # Register data provider
        _providers["ibkr"] = IBKRDataProvider(_ibkr_ib)
        # Register trading provider
        from broker import IBKRTradingProvider, register_trading_provider
        register_trading_provider("ibkr", IBKRTradingProvider(_ibkr_ib))
        print(f"[IBKR] Data + trading providers registered")
```

- [ ] **Step 3: Register Alpaca trading provider in shared.py**

Insert after the Alpaca provider registration block (where `_providers["alpaca"]` and `_providers["alpaca-iex"]` are set):

```python
# Register Alpaca as a trading provider too
if _trading_client is not None:
    from broker import AlpacaTradingProvider, register_trading_provider
    register_trading_provider("alpaca", AlpacaTradingProvider(_trading_client, _alpaca_client))
```

- [ ] **Step 4: Call init_ibkr() from main.py lifespan**

Modify `backend/main.py` lifespan function:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    from shared import init_ibkr
    init_ibkr()
    manager = BotManager()
    manager.load()
    bots_module.bot_manager = manager
    yield
    await manager.shutdown()
```

- [ ] **Step 5: Run tests to verify nothing is broken**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add backend/shared.py backend/main.py
git commit -m "feat: add IBKRDataProvider and connection management"
```

### Task 5: Refactor bot_runner.py

Heaviest refactoring. Replace all Alpaca SDK imports and calls with `TradingProvider` methods. The `_ALPACA_AVAILABLE` guard becomes a `provider is not None` check.

**Files:**
- Modify: `backend/bot_runner.py`

- [ ] **Step 1: Replace imports**

Replace lines 19-28 (the old imports):

```python
# Old:
from shared import _fetch, get_trading_client, is_retryable_error
...
try:
    from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, OrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, OrderType
    _ALPACA_AVAILABLE = True
except ImportError:
    _ALPACA_AVAILABLE = False
```

With:

```python
from shared import _fetch
from broker import get_trading_provider, OrderRequest as BrokerOrderRequest, OrderResult
```

- [ ] **Step 2: Replace position check (section 5 of _tick)**

Replace the Alpaca position check block (lines ~146-168). Old code gets `client = get_trading_client()` and calls `client.get_all_positions()` directly.

New code:

```python
        # 5. Check broker for existing position (source of truth)
        has_position = False
        broker_qty = 0
        try:
            provider = get_trading_provider()
            positions = await self._run_in_executor(provider.get_positions)
            for pos in positions:
                if pos["symbol"] == cfg.symbol.upper():
                    if pos["side"] != cfg.direction:
                        continue
                    has_position = True
                    broker_qty = pos["qty"]
                    if state.entry_price is None:
                        state.entry_price = pos["avg_entry"]
                        state.trail_peak = price
                        self._log("INFO", f"Resumed tracking position: entry={state.entry_price:.2f}")
                    break
        except Exception as e:
            self._log("WARN", f"Position check failed: {e}")
            return
```

- [ ] **Step 3: Replace externally-closed position detection (section 6)**

Replace the block that imports `from alpaca.trading.requests import GetOrdersRequest` and queries filled orders (lines ~178-201).

New code:

```python
                try:
                    provider = get_trading_provider()
                    filled_orders = await self._run_in_executor(
                        provider.get_orders, "closed", [cfg.symbol.upper()], 5
                    )
                    close_side = "buy" if is_short else "sell"
                    for o in filled_orders:
                        if o["side"] == close_side and o.get("filled_avg_price"):
                            exit_price = float(o["filled_avg_price"])
                            exit_qty = float(o.get("qty", 0))
                            order_type = o.get("type", "")
                            if order_type in ("stop", "stop_limit"):
                                exit_reason = "stop_loss"
                            elif order_type == "trailing_stop":
                                exit_reason = "trailing_stop"
                            else:
                                exit_reason = "external"
                            break
                except Exception as e:
                    self._log("WARN", f"Failed to query filled orders: {e}")
```

- [ ] **Step 4: Replace opposite-direction safety check (line ~247)**

Old code: `_client = await self._run_in_executor(get_trading_client)` then `_client.get_all_positions()`.

New code:

```python
                try:
                    provider = get_trading_provider()
                    _positions = await self._run_in_executor(provider.get_positions)
                    for _pos in _positions:
                        if _pos["symbol"] == cfg.symbol.upper():
                            if _pos["side"] != cfg.direction:
                                self._log("WARN", f"Skipping entry — opposite position ({_pos['side']}) exists")
                                return
                            break
                except Exception:
                    pass
```

- [ ] **Step 5: Replace order submission (lines ~273-323)**

Old code builds `MarketOrderRequest` with various `OrderClass`/`StopLossRequest` combinations.

New code — uses `BrokerOrderRequest` for all cases. OTO brackets are Alpaca-specific; for IBKR, stops are placed separately after fill (the bot already manages trailing stops via polling):

```python
                try:
                    provider = get_trading_provider()

                    if is_short:
                        order_req = BrokerOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side="sell",
                        )
                    elif cfg.stop_loss_pct and not cfg.trailing_stop:
                        # OTO bracket: provider handles if supported (Alpaca), else plain market
                        stop_price = round(price * (1 - cfg.stop_loss_pct / 100), 2)
                        order_req = BrokerOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side="buy",
                            order_type="stop",
                            stop_price=stop_price,
                        )
                    else:
                        order_req = BrokerOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side="buy",
                        )

                    result = await self._run_in_executor(provider.submit_order, order_req)
                except Exception as e:
                    self._log("ERROR", f"Buy order failed: {e}")
                    return

                # Get actual fill price
                fill_price = await self._get_fill_price_provider(provider, result.order_id, price)
```

- [ ] **Step 6: Replace _get_fill_price to use provider**

Add a new method `_get_fill_price_provider` alongside or replacing the old `_get_fill_price`:

```python
    async def _get_fill_price_provider(self, provider, order_id: str, expected: float) -> float:
        """Poll provider for fill price, fall back to expected."""
        for _ in range(5):
            await asyncio.sleep(0.5)
            try:
                result = await self._run_in_executor(provider.get_order, order_id)
                if result.filled_avg_price is not None:
                    return result.filled_avg_price
            except Exception:
                break
        return expected
```

- [ ] **Step 7: Replace exit/close logic (lines ~420-460)**

Old code: `client = get_trading_client()`, queries open orders with Alpaca SDK, cancels SL legs, then `client.close_position()`.

New code:

```python
                try:
                    provider = get_trading_provider()
                    # Verify position side matches bot direction
                    try:
                        positions = await self._run_in_executor(provider.get_positions)
                        pos_match = False
                        for pos in positions:
                            if pos["symbol"] == cfg.symbol.upper():
                                if pos["side"] == cfg.direction:
                                    pos_match = True
                                break
                        if not pos_match:
                            self._log("WARN", f"Position side mismatch — clearing stale state")
                            state.entry_price = None
                            state.trail_peak = None
                            state.trail_stop_price = None
                            self.manager.save()
                            return
                    except Exception as e:
                        self._log("WARN", f"Position verify failed: {e}")
                        return

                    # Cancel pending stop-loss orders for this symbol
                    try:
                        orders = await self._run_in_executor(
                            provider.get_orders, "open", [cfg.symbol.upper()], 50
                        )
                        cancel_side = "buy" if is_short else "sell"
                        for o in orders:
                            if o["side"] == cancel_side:
                                await self._run_in_executor(provider.cancel_order, o["id"])
                                self._log("INFO", f"Cancelled pending {o['type']} order {o['id']}")
                    except Exception as e:
                        self._log("WARN", f"Cancel orders failed: {e}")

                    close_result = await self._run_in_executor(provider.close_position, cfg.symbol.upper())
                except Exception as e:
                    self._log("ERROR", f"Close position failed: {e}")
                    return

                # Get actual fill price
                order_id = close_result.order_id
                sell_fill = await self._get_fill_price_provider(provider, order_id, price) if order_id else price
```

- [ ] **Step 8: Remove `_ALPACA_AVAILABLE` guard**

The old `_ALPACA_AVAILABLE` check in `_tick()` becomes:

```python
        try:
            provider = get_trading_provider()
        except Exception:
            return  # no broker configured
```

Move this to the top of `_tick()` as the first check.

- [ ] **Step 9: Remove old `_get_fill_price` method if unused**

Delete the old `_get_fill_price(self, client, order_id, expected)` method that used Alpaca SDK directly. It's replaced by `_get_fill_price_provider`.

- [ ] **Step 10: Run tests**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 11: Commit**

```bash
git add backend/bot_runner.py
git commit -m "refactor: bot_runner uses TradingProvider instead of Alpaca SDK"
```

### Task 6: Refactor routes/trading.py

Replace all Alpaca SDK calls with `TradingProvider`. Remove `_alpaca_call()` retry wrapper (retry logic now lives in providers). Replace `_wait_for_fill()` with provider-based polling.

**Files:**
- Modify: `backend/routes/trading.py`

- [ ] **Step 1: Replace imports**

Replace line 9:

```python
# Old:
from shared import get_trading_client, _fetch, _alpaca_client, is_retryable_error
```

With:

```python
from shared import _fetch
from broker import get_trading_provider, OrderRequest as BrokerOrderRequest
```

- [ ] **Step 2: Remove `_alpaca_call()` function**

Delete the `_alpaca_call` function (lines 20-28). Provider implementations handle retries internally.

- [ ] **Step 3: Replace `get_account` endpoint**

Replace the entire endpoint body:

```python
@router.get("/account")
def get_account():
    from fastapi import HTTPException as _HTTPException
    try:
        provider = get_trading_provider()
        return provider.get_account()
    except Exception as e:
        print(f"[Broker ERROR] get_account: {type(e).__name__}: {e}")
        raise _HTTPException(status_code=502, detail=f"Broker API error: {e}")
```

- [ ] **Step 4: Replace `get_positions` endpoint**

```python
@router.get("/positions")
def get_positions():
    from fastapi import HTTPException as _HTTPException
    try:
        provider = get_trading_provider()
        return provider.get_positions()
    except Exception as e:
        print(f"[Broker ERROR] get_positions: {type(e).__name__}: {e}")
        raise _HTTPException(status_code=502, detail=f"Broker API error: {e}")
```

- [ ] **Step 5: Replace `get_orders` endpoint**

```python
@router.get("/orders")
def get_orders():
    from fastapi import HTTPException as _HTTPException
    try:
        provider = get_trading_provider()
        return provider.get_orders()
    except Exception as e:
        print(f"[Broker ERROR] get_orders: {type(e).__name__}: {e}")
        raise _HTTPException(status_code=502, detail=f"Broker API error: {e}")
```

- [ ] **Step 6: Replace `place_buy` endpoint**

```python
@router.post("/buy")
def place_buy(req: BuyRequest):
    provider = get_trading_provider()

    # Get latest price for journal and optional stop loss
    current_price = None
    try:
        current_price = provider.get_latest_price(req.symbol)
    except Exception:
        pass

    stop_price = None
    order_type = "market"
    if req.stop_loss_pct and req.stop_loss_pct > 0 and current_price:
        stop_price = round(current_price * (1 - req.stop_loss_pct / 100), 2)
        order_type = "stop"

    result = provider.submit_order(BrokerOrderRequest(
        symbol=req.symbol,
        qty=int(req.qty),
        side="buy",
        order_type=order_type,
        stop_price=stop_price,
    ))

    _log_trade(req.symbol, "buy", req.qty, price=current_price,
               source="manual", stop_loss_price=stop_price, reason="entry")

    resp = {
        "order_id": result.order_id,
        "symbol": result.symbol,
        "qty": str(int(result.qty)),
        "side": "buy",
        "status": result.status,
    }
    if stop_price is not None:
        resp["stop_loss"] = {"stop_price": stop_price}
    return resp
```

- [ ] **Step 7: Replace `_wait_for_fill` and `place_sell` endpoint**

Replace `_wait_for_fill` with provider-based version:

```python
def _wait_for_fill(provider, order_id: str, timeout: float = 2.0) -> tuple[float | None, float | None]:
    """Poll order until filled or timeout. Returns (fill_price, fill_qty)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = provider.get_order(order_id)
            if result.filled_avg_price is not None:
                return result.filled_avg_price, result.filled_qty
        except Exception:
            break
        time.sleep(0.1)
    return None, None
```

Replace `place_sell`:

```python
@router.post("/sell")
def place_sell(req: SellRequest):
    provider = get_trading_provider()
    from fastapi import HTTPException as _HTTPException

    # Cancel pending stop-loss orders first
    try:
        open_orders = provider.get_orders("open", [req.symbol])
        for o in open_orders:
            if o["side"] == "sell":
                provider.cancel_order(o["id"])
    except Exception:
        pass

    if req.qty is None:
        try:
            result = provider.close_position(req.symbol)
        except Exception as e:
            raise _HTTPException(status_code=404, detail=f"No open position for {req.symbol}")

        fill_price, fill_qty = _wait_for_fill(provider, result.order_id) if result.order_id else (None, None)
        _log_trade(req.symbol, "sell", fill_qty or 0, price=fill_price,
                   source="manual", reason="manual")
        _clear_bot_entry_state(req.symbol)
        return {"symbol": req.symbol, "action": "position_closed",
                "fill_price": fill_price, "fill_qty": fill_qty}

    result = provider.submit_order(BrokerOrderRequest(
        symbol=req.symbol,
        qty=int(req.qty),
        side="sell",
    ))

    fill_price, fill_qty = _wait_for_fill(provider, result.order_id)
    _log_trade(req.symbol, "sell", fill_qty or req.qty, price=fill_price,
               source="manual", reason="manual")
    _clear_bot_entry_state(req.symbol)

    return {
        "order_id": result.order_id,
        "symbol": req.symbol,
        "qty": str(int(fill_qty or result.qty)),
        "side": "sell",
        "status": "filled" if fill_price else result.status,
        "fill_price": fill_price,
    }
```

- [ ] **Step 8: Replace `close_all_positions` and `cancel_all_orders`**

```python
@router.post("/close-all")
def close_all_positions():
    provider = get_trading_provider()
    provider.close_all_positions()
    return {"action": "all_positions_closed"}


@router.post("/cancel-all")
def cancel_all_orders():
    provider = get_trading_provider()
    provider.cancel_all_orders()
    return {"action": "all_orders_cancelled"}
```

- [ ] **Step 9: Replace `scan_signals` auto-execute**

Replace the scan endpoint's auto-execute block (lines ~282-358). Key change: replace `client.get_all_positions()`, `client.get_orders()`, `client.submit_order()`, `client.close_position()` with provider equivalents.

```python
@router.post("/scan")
def scan_signals(req: ScanRequest):
    provider = get_trading_provider()

    existing_positions = set()
    if req.auto_execute:
        for p in provider.get_positions():
            existing_positions.add(p["symbol"])
        for o in provider.get_orders("open"):
            if o["side"] == "buy":
                existing_positions.add(o["symbol"])

    results = []
    actions = []

    for symbol in req.symbols:
        try:
            end = pd.Timestamp.now(tz='UTC')
            start = end - pd.Timedelta(days=30)

            df = _fetch(symbol, start.strftime('%Y-%m-%d'),
                        end.strftime('%Y-%m-%d'), req.interval, source='alpaca')

            indicators = compute_indicators(df["Close"], high=df["High"], low=df["Low"])
            i = len(df) - 1

            buy_signal = eval_rules(req.buy_rules, req.buy_logic, indicators, i)
            sell_signal = eval_rules(req.sell_rules, req.sell_logic, indicators, i)

            signal = "BUY" if buy_signal else ("SELL" if sell_signal else "NONE")

            rsi_val = float(indicators["rsi"].iloc[i])
            ema50_val = float(indicators["ema50"].iloc[i])
            price = float(df["Close"].iloc[i])

            result = {
                "symbol": symbol,
                "signal": signal,
                "price": price,
                "rsi": round(rsi_val, 2),
                "ema50": round(ema50_val, 2),
                "last_bar": str(df.index[i]),
            }

            if req.auto_execute:
                if signal == "BUY" and symbol not in existing_positions:
                    qty = math.floor(req.position_size_usd / price)
                    if qty > 0:
                        stop_price = None
                        order_type = "market"
                        if req.stop_loss_pct and req.stop_loss_pct > 0:
                            stop_price = round(price * (1 - req.stop_loss_pct / 100), 2)
                            order_type = "stop"

                        order_result = provider.submit_order(BrokerOrderRequest(
                            symbol=symbol,
                            qty=qty,
                            side="buy",
                            order_type=order_type,
                            stop_price=stop_price,
                        ))
                        _log_trade(symbol, "buy", qty, price=price,
                                   source="auto", stop_loss_price=stop_price, reason="entry")
                        action = {
                            "symbol": symbol, "action": "BUY",
                            "qty": qty, "order_id": order_result.order_id,
                        }
                        if stop_price:
                            action["stop_price"] = stop_price
                        actions.append(action)
                        existing_positions.add(symbol)

                elif signal == "SELL" and symbol in existing_positions:
                    try:
                        provider.close_position(symbol)
                        _log_trade(symbol, "sell", 0, price=price, source="auto", reason="signal")
                        actions.append({
                            "symbol": symbol, "action": "SELL",
                            "detail": "position_closed",
                        })
                        existing_positions.discard(symbol)
                    except Exception:
                        actions.append({
                            "symbol": symbol, "action": "SELL_FAILED",
                        })

            results.append(result)
        except Exception as e:
            results.append({"symbol": symbol, "signal": "ERROR", "error": str(e)})

    response = {"signals": results, "scanned_at": str(pd.Timestamp.now(tz='UTC'))}
    if req.auto_execute:
        response["actions"] = actions
    return response
```

- [ ] **Step 10: Remove `ScanRequest.stop_loss_pct` Alpaca dependency note**

The `ScanRequest` model (lines 42-50) uses `stop_loss_pct` which is fine as-is — it's a plain float, no Alpaca types. Leave it.

- [ ] **Step 11: Run tests**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 12: Commit**

```bash
git add backend/routes/trading.py
git commit -m "refactor: routes/trading uses TradingProvider instead of Alpaca SDK"
```

### Task 7: Refactor bot_manager.py

Replace `get_trading_client()` and Alpaca SDK calls in `manual_buy()` and `stop_bot()`.

**Files:**
- Modify: `backend/bot_manager.py`

- [ ] **Step 1: Replace imports**

Replace:

```python
from shared import _fetch, get_trading_client
```

With:

```python
from shared import _fetch
from broker import get_trading_provider, OrderRequest as BrokerOrderRequest
```

- [ ] **Step 2: Remove Alpaca SDK imports in manual_buy**

Remove the Alpaca import block at the top of `manual_buy()` (lines ~32-33):

```python
# Remove:
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
```

- [ ] **Step 3: Replace manual_buy() order submission**

Replace lines ~300-337 (the `client = get_trading_client()` block through the fill polling loop).

New code:

```python
        provider = get_trading_provider()

        # Submit order
        is_short = config.direction == "short"
        result = provider.submit_order(BrokerOrderRequest(
            symbol=config.symbol.upper(),
            qty=qty,
            side="sell" if is_short else "buy",
        ))

        # Get fill price (blocking poll)
        import time
        fill_price = price
        for _ in range(5):
            time.sleep(0.5)
            try:
                o = provider.get_order(result.order_id)
                if o.filled_avg_price is not None:
                    fill_price = o.filled_avg_price
                    break
            except Exception:
                break
```

The rest of `manual_buy()` (state updates, logging) stays the same — it only uses `fill_price`, `qty`, `price`, and state fields.

- [ ] **Step 4: Replace stop_bot() close_position call**

Replace lines ~219-223:

```python
# Old:
        if close_position:
            try:
                client = get_trading_client()
                client.close_position(config.symbol.upper())
            except Exception:
                pass
```

With:

```python
        if close_position:
            try:
                provider = get_trading_provider()
                provider.close_position(config.symbol.upper())
            except Exception:
                pass
```

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add backend/bot_manager.py
git commit -m "refactor: bot_manager uses TradingProvider instead of Alpaca SDK"
```

### Task 8: Broker API Endpoints

Add `GET /api/broker` and `PUT /api/broker` to `routes/providers.py`.

**Files:**
- Modify: `backend/routes/providers.py`

- [ ] **Step 1: Add broker endpoints**

Replace the entire file:

```python
from fastapi import APIRouter
from pydantic import BaseModel
from shared import get_available_providers
from broker import get_active_broker, get_available_brokers, set_active_broker

router = APIRouter()


@router.get("/api/providers")
def list_providers():
    return {"providers": get_available_providers()}


class BrokerSwitch(BaseModel):
    broker: str


@router.get("/api/broker")
def get_broker():
    return {
        "broker": get_active_broker(),
        "available": get_available_brokers(),
    }


@router.put("/api/broker")
def switch_broker(req: BrokerSwitch):
    set_active_broker(req.broker)
    return {
        "broker": get_active_broker(),
        "available": get_available_brokers(),
    }
```

- [ ] **Step 2: Run tests**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add backend/routes/providers.py
git commit -m "feat: add GET/PUT /api/broker endpoints for runtime broker switching"
```

### Task 9: Frontend — Types, API, and Hooks

Add `'ibkr'` to `DataSource`, add broker API calls, add `useBroker` hook.

**Files:**
- Modify: `frontend/src/shared/types/index.ts`
- Modify: `frontend/src/api/trading.ts`
- Modify: `frontend/src/shared/hooks/useOHLCV.ts`

- [ ] **Step 1: Add 'ibkr' to DataSource type**

In `frontend/src/shared/types/index.ts`, change line 247:

```typescript
// Old:
export type DataSource = 'yahoo' | 'alpaca' | 'alpaca-iex'

// New:
export type DataSource = 'yahoo' | 'alpaca' | 'alpaca-iex' | 'ibkr'
```

- [ ] **Step 2: Add broker API calls to trading.ts**

Append to `frontend/src/api/trading.ts`:

```typescript
// --- Broker ---

export interface BrokerInfo {
  broker: string
  available: string[]
}

export async function fetchBroker(): Promise<BrokerInfo> {
  const { data } = await api.get('/api/broker')
  return data
}

export async function setBroker(broker: string): Promise<BrokerInfo> {
  const { data } = await api.put('/api/broker', { broker })
  return data
}
```

- [ ] **Step 3: Add useBroker hook to useOHLCV.ts**

Append to `frontend/src/shared/hooks/useOHLCV.ts`:

```typescript
import { fetchBroker, setBroker as setBrokerApi, type BrokerInfo } from '../../api/trading'

export function useBroker() {
  const query = useQuery<BrokerInfo>({
    queryKey: ['broker'],
    queryFn: fetchBroker,
    staleTime: 30_000,
  })

  const switchBroker = async (broker: string) => {
    const result = await setBrokerApi(broker)
    queryClient.setQueryData(['broker'], result)
    // Invalidate account/positions/orders so they refetch from new broker
    queryClient.invalidateQueries({ queryKey: ['account'] })
    queryClient.invalidateQueries({ queryKey: ['positions'] })
    queryClient.invalidateQueries({ queryKey: ['orders'] })
    return result
  }

  return {
    broker: query.data?.broker ?? 'alpaca',
    available: query.data?.available ?? [],
    isLoading: query.isLoading,
    switchBroker,
  }
}
```

Note: `useOHLCV.ts` already imports `useQuery` and has access to `queryClient`. Check the existing imports at the top and add `fetchBroker`/`setBrokerApi` to whatever import block exists for the trading API.

- [ ] **Step 4: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/shared/types/index.ts frontend/src/api/trading.ts frontend/src/shared/hooks/useOHLCV.ts
git commit -m "feat: add IBKR to DataSource type and broker API hooks"
```

### Task 10: Frontend — Sidebar Data Source + AccountBar Broker Selector

Add IBKR to the chart sidebar data source toggle. Add broker selector to AccountBar on the bot page.

**Files:**
- Modify: `frontend/src/features/sidebar/Sidebar.tsx`
- Modify: `frontend/src/features/trading/AccountBar.tsx`

- [ ] **Step 1: Add IBKR to Sidebar data source toggle**

In `frontend/src/features/sidebar/Sidebar.tsx`, find the data source toggle (line ~237):

```typescript
// Old:
{(['yahoo', 'alpaca'] as const).map(src => {
```

Change to:

```typescript
{(['yahoo', 'alpaca', 'ibkr'] as const).map(src => {
```

Also update the IEX checkbox condition (line ~266) to not show for IBKR:

```typescript
// Old:
{(dataSource === 'alpaca' || dataSource === 'alpaca-iex') && (
```

No change needed — IBKR won't trigger this condition.

The `available` check on line 238 (`providers.includes(src)`) already works — IBKR will only appear enabled when the backend reports it in `GET /api/providers`.

- [ ] **Step 2: Add broker selector to AccountBar**

In `frontend/src/features/trading/AccountBar.tsx`, add the broker selector. This needs to import and use the `useBroker` hook.

Replace the entire file:

```typescript
import { useEffect, useState } from 'react'
import { fetchAccount, type Account } from '../../api/trading'
import { useBroker } from '../../shared/hooks/useOHLCV'

export default function AccountBar() {
  const [account, setAccount] = useState<Account | null>(null)
  const [error, setError] = useState<string | null>(null)
  const hasLoaded = useState({ value: false })[0]
  const { broker, available, switchBroker } = useBroker()

  useEffect(() => {
    const load = () => {
      fetchAccount().then(a => { setAccount(a); setError(null); hasLoaded.value = true }).catch(e => {
        if (!hasLoaded.value) setError(e.message)
      })
    }
    load()
    const id = window.setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [broker])  // refetch when broker changes

  if (error) return <div style={styles.bar}><span style={{ color: '#f85149' }}>Account error: {error}</span></div>
  if (!account) return <div style={styles.bar}><span style={{ color: '#8b949e' }}>Loading account...</span></div>

  const metrics = [
    { label: 'Equity', value: `$${account.equity.toLocaleString(undefined, { minimumFractionDigits: 2 })}` },
    { label: 'Cash', value: `$${account.cash.toLocaleString(undefined, { minimumFractionDigits: 2 })}` },
    { label: 'Buying Power', value: `$${account.buying_power.toLocaleString(undefined, { minimumFractionDigits: 2 })}` },
    { label: 'Day Trades', value: account.day_trade_count },
  ]

  return (
    <div style={styles.bar}>
      {available.length > 1 && (
        <div style={styles.brokerSelector}>
          <span style={styles.brokerLabel}>Broker</span>
          <div style={styles.brokerToggle}>
            {available.map(b => (
              <button
                key={b}
                onClick={() => switchBroker(b)}
                style={{
                  padding: '3px 10px',
                  fontSize: 11,
                  fontWeight: 600,
                  background: broker === b ? '#30363d' : 'transparent',
                  color: broker === b ? '#e6edf3' : '#8b949e',
                  border: 'none',
                  borderRadius: 4,
                  cursor: 'pointer',
                  textTransform: 'uppercase' as const,
                }}
              >
                {b}
              </button>
            ))}
          </div>
        </div>
      )}
      {metrics.map(m => (
        <div key={m.label} style={styles.metric}>
          <span style={styles.label}>{m.label}</span>
          <span style={styles.value}>{m.value}</span>
        </div>
      ))}
      {account.trading_blocked && <span style={{ color: '#f85149', fontSize: 12 }}>TRADING BLOCKED</span>}
      {account.pattern_day_trader && <span style={{ color: '#f0883e', fontSize: 12 }}>PDT</span>}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  bar: {
    display: 'flex', alignItems: 'center', gap: 24,
    padding: '10px 16px',
    background: '#161b22', borderBottom: '1px solid #30363d',
    flexShrink: 0,
  },
  metric: { display: 'flex', flexDirection: 'column', gap: 2 },
  label: { fontSize: 10, color: '#8b949e', textTransform: 'uppercase' as const, letterSpacing: '0.05em' },
  value: { fontSize: 15, fontWeight: 600, color: '#e6edf3' },
  brokerSelector: { display: 'flex', flexDirection: 'column', gap: 2, marginRight: 8 },
  brokerLabel: { fontSize: 10, color: '#8b949e', textTransform: 'uppercase' as const, letterSpacing: '0.05em' },
  brokerToggle: {
    display: 'flex', gap: 2,
    background: '#0d1117', borderRadius: 4, padding: 2,
  },
}
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Start dev server and visually verify**

Run: `cd frontend && npm run dev`

Verify:
1. Chart page sidebar shows Yahoo / Alpaca / IBKR toggle (IBKR disabled if Gateway not running)
2. Bot page AccountBar shows broker selector when multiple brokers available
3. Switching broker refetches account metrics

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/sidebar/Sidebar.tsx frontend/src/features/trading/AccountBar.tsx
git commit -m "feat: add IBKR to data source toggle and broker selector to AccountBar"
```

### Task 11: Cleanup shared.py + Integration Smoke Test

Remove dead `get_trading_client()` from `shared.py`. Clean up any remaining Alpaca imports in refactored files. Verify the full stack works.

**Files:**
- Modify: `backend/shared.py` (remove `get_trading_client`, `_create_trading_client`, `_trading_client`)

- [ ] **Step 1: Remove dead trading client code from shared.py**

Delete these blocks:

```python
# Delete: _create_trading_client function (lines ~136-143)
def _create_trading_client():
    ...

# Delete: module-level call (line ~146)
_trading_client = _create_trading_client()

# Delete: get_trading_client function (lines ~149-152)
def get_trading_client():
    ...
```

These are replaced by `get_trading_provider()` from `broker.py`.

Note: Keep `_create_alpaca_client()` — it's still used for the data provider.

- [ ] **Step 2: Move Alpaca trading provider registration**

The Alpaca trading provider registration (from Task 4 Step 3) needs to create its own `TradingClient`. Update the registration block in `shared.py`:

```python
# Register Alpaca as a trading provider
_alpaca_api_key = os.environ.get("ALPACA_API_KEY", "").strip()
_alpaca_secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()
if _alpaca_api_key and _alpaca_secret_key:
    try:
        from alpaca.trading.client import TradingClient
        _alpaca_trading_client = TradingClient(_alpaca_api_key, _alpaca_secret_key, paper=True)
        from broker import AlpacaTradingProvider, register_trading_provider
        register_trading_provider("alpaca", AlpacaTradingProvider(_alpaca_trading_client, _alpaca_client))
    except Exception as e:
        print(f"[Alpaca] Trading provider registration failed: {e}")
```

- [ ] **Step 3: Grep for stale imports**

Verify no file still imports `get_trading_client`:

Run: `cd backend && grep -rn "get_trading_client" --include="*.py" | grep -v __pycache__`
Expected: No results (or only in test files if any)

Verify no consumer file still has direct `from alpaca.*` imports:

Run: `cd backend && grep -rn "from alpaca" bot_runner.py bot_manager.py routes/trading.py`
Expected: No results

- [ ] **Step 4: Run all backend tests**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Start full stack and smoke test**

Run: `./start.sh`

Smoke test checklist:
1. `GET /api/providers` — returns `["yahoo", "alpaca", "alpaca-iex"]` (and `"ibkr"` if Gateway running)
2. `GET /api/broker` — returns `{"broker": "alpaca", "available": ["alpaca"]}` (or includes `"ibkr"`)
3. `GET /api/trading/account` — returns account info from active broker
4. `GET /api/trading/positions` — returns positions list
5. Frontend chart page sidebar shows data sources correctly
6. Frontend bot page shows broker selector if IBKR is available
7. Switching broker via `PUT /api/broker {"broker": "ibkr"}` changes the active broker
8. Account metrics refresh after broker switch

- [ ] **Step 6: Commit**

```bash
git add backend/shared.py
git commit -m "chore: remove dead get_trading_client, clean up Alpaca trading provider init"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Core types + TradingProvider protocol + registry | `broker.py`, `tests/test_broker.py`, `requirements.txt` |
| 2 | AlpacaTradingProvider | `broker.py`, `tests/test_broker.py` |
| 3 | IBKRTradingProvider | `broker.py`, `tests/test_broker.py` |
| 4 | IBKRDataProvider + connection management | `shared.py`, `main.py` |
| 5 | Refactor bot_runner.py | `bot_runner.py` |
| 6 | Refactor routes/trading.py | `routes/trading.py` |
| 7 | Refactor bot_manager.py | `bot_manager.py` |
| 8 | Broker API endpoints | `routes/providers.py` |
| 9 | Frontend types, API, hooks | `types/index.ts`, `trading.ts`, `useOHLCV.ts` |
| 10 | Frontend sidebar + AccountBar UI | `Sidebar.tsx`, `AccountBar.tsx` |
| 11 | Cleanup + integration smoke test | `shared.py` |

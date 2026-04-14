"""
broker.py — Trading provider abstraction.

Defines the TradingProvider protocol and normalized order types.
Concrete implementations: AlpacaTradingProvider, IBKRTradingProvider.
Global broker registry with runtime switching.
"""

from __future__ import annotations

import asyncio
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


def get_trading_provider(name: str | None = None) -> TradingProvider:
    """Return a trading provider by name, or the globally active one if name is None.

    Bots pass their own `broker` so they can trade on different brokers simultaneously.
    Account/positions views without an explicit name use the global `_active_broker`.
    """
    key = name or _active_broker
    provider = _trading_providers.get(key)
    if provider is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=f"Broker '{key}' not configured")
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


# ---------------------------------------------------------------------------
# Alpaca implementation
# ---------------------------------------------------------------------------

class AlpacaTradingProvider:
    """TradingProvider backed by Alpaca SDK."""

    name = "alpaca"

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


# ---------------------------------------------------------------------------
# IBKR implementation
# ---------------------------------------------------------------------------

class IBKRTradingProvider:
    """TradingProvider backed by ib_insync.

    All ib_insync calls must run on the main FastAPI event loop. Sync route
    handlers and background bot tasks call into us from other contexts, so we
    marshal onto the main loop via run_coroutine_threadsafe.
    """

    name = "ibkr"

    def __init__(self, ib, loop, default_account: str | None = None):
        self._ib = ib
        self._loop = loop
        self._default_account = default_account or os.environ.get("IBKR_DEFAULT_ACCOUNT", "").strip() or None

    async def ping(self) -> None:
        """Liveness probe for HeartbeatMonitor. Must not trigger reconnect —
        a failed ping is the signal the monitor uses to mark us unhealthy.

        Monitor runs on the same loop as `self._loop`, so we can await the
        ib_insync Future directly; no cross-thread scheduling needed.
        """
        await self._ib.reqCurrentTimeAsync()

    async def reconnect(self) -> None:
        """Invoked by HeartbeatMonitor after a failed ping. The shared IB
        instance is reused, so both data and trading providers recover."""
        await self._reconnect_async()

    def _run(self, coro, timeout: float = 30.0):
        """Schedule a coroutine on the main loop, block until done."""
        import asyncio
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def _ensure_connected_async(self):
        if not self._ib.isConnected():
            host = os.environ.get("IBKR_HOST", "127.0.0.1")
            port = int(os.environ.get("IBKR_PORT", "4002"))
            client_id = int(os.environ.get("IBKR_CLIENT_ID", "1"))
            await self._ib.connectAsync(host, port, clientId=client_id)

    async def _reconnect_async(self):
        try:
            if self._ib.isConnected():
                self._ib.disconnect()
        except Exception:
            pass
        await self._ensure_connected_async()

    def _ensure_connected(self):
        """Verify Gateway session is alive; reconnect if stale.

        `ib_insync.isConnected()` can return True after the Gateway has silently
        dropped the TCP session (common after long idle / overnight reauth). A
        cheap `reqCurrentTime` ping on every call catches that case and forces a
        reconnect before the real call hangs. Gateway is local so latency is
        negligible.
        """
        if not self._ib.isConnected():
            self._run(self._reconnect_async())
            return
        try:
            self._run(self._ib.reqCurrentTimeAsync(), timeout=5.0)
        except Exception:
            self._run(self._reconnect_async())

    def _contract(self, symbol: str):
        from ib_insync import Stock
        return Stock(symbol, "SMART", "USD")

    def _resolve_account(self, account_id: str | None = None) -> str | None:
        return account_id or self._default_account

    def get_account(self, account_id: str | None = None) -> dict:
        self._ensure_connected()
        acct = self._resolve_account(account_id)
        summary = self._run(self._ib.accountSummaryAsync(acct or ''))
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
        # positions() reads cached state — safe to marshal via a no-op coro
        async def _get():
            return self._ib.positions(acct) if acct else self._ib.positions()
        positions = self._run(_get())
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
        async def _refresh_and_list():
            if status != "open":
                await self._ib.reqAllOpenOrdersAsync()
            return list(self._ib.trades())
        trades = self._run(_refresh_and_list())
        result = []
        for trade in trades:
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

        async def _place():
            return self._ib.placeOrder(contract, ib_order)
        trade = self._run(_place())
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
        async def _list():
            return list(self._ib.trades())
        for trade in self._run(_list()):
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
        async def _cancel():
            for trade in self._ib.trades():
                if str(trade.order.orderId) == order_id:
                    self._ib.cancelOrder(trade.order)
                    return True
            return False
        if not self._run(_cancel()):
            raise ValueError(f"Order {order_id} not found")

    def close_position(self, symbol: str) -> OrderResult:
        from ib_insync import MarketOrder
        self._ensure_connected()
        acct = self._resolve_account()

        async def _close():
            for p in self._ib.positions():
                if p.contract.symbol == symbol:
                    qty = float(p.position)
                    side = "SELL" if qty > 0 else "BUY"
                    order = MarketOrder(side, abs(qty))
                    if acct:
                        order.account = acct
                    trade = self._ib.placeOrder(p.contract, order)
                    return (trade, qty, side)
            return None

        result = self._run(_close())
        if result is None:
            raise ValueError(f"No position for {symbol}")
        trade, qty, side = result
        return OrderResult(
            order_id=str(trade.order.orderId),
            symbol=symbol,
            qty=abs(qty),
            side=side.lower(),
            status="pending",
        )

    def close_all_positions(self) -> None:
        from ib_insync import MarketOrder
        self._ensure_connected()
        async def _close_all():
            for p in self._ib.positions():
                qty = float(p.position)
                if qty == 0:
                    continue
                side = "SELL" if qty > 0 else "BUY"
                order = MarketOrder(side, abs(qty))
                self._ib.placeOrder(p.contract, order)
        self._run(_close_all())

    def cancel_all_orders(self) -> None:
        self._ensure_connected()
        async def _cancel_all():
            self._ib.reqGlobalCancel()
        self._run(_cancel_all())

    def get_latest_price(self, symbol: str) -> float:
        import asyncio as _aio
        self._ensure_connected()
        contract = self._contract(symbol)

        async def _price():
            ticker = self._ib.reqMktData(contract, '', False, False)
            await _aio.sleep(1)  # wait for data
            p = ticker.marketPrice()
            self._ib.cancelMktData(contract)
            return p

        price = self._run(_price())
        if price != price:  # NaN check
            raise ValueError(f"No market data for {symbol}")
        return float(price)

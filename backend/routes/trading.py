import json
import math
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import pandas as pd
from shared import get_trading_client, _fetch, _alpaca_client
from signal_engine import Rule, compute_indicators, eval_rules

router = APIRouter(prefix="/api/trading")

WATCHLIST_PATH = Path(__file__).resolve().parent.parent / "data" / "watchlist.json"


class BuyRequest(BaseModel):
    symbol: str
    qty: float
    stop_loss_pct: Optional[float] = None


class SellRequest(BaseModel):
    symbol: str
    qty: Optional[float] = None


class ScanRequest(BaseModel):
    symbols: list[str]
    interval: str = "15m"
    buy_rules: list[Rule]
    sell_rules: list[Rule]
    buy_logic: str = "AND"
    sell_logic: str = "AND"
    auto_execute: bool = False
    position_size_usd: float = 5000.0
    stop_loss_pct: Optional[float] = None


class WatchlistRequest(BaseModel):
    symbols: list[str]


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


@router.post("/buy")
def place_buy(req: BuyRequest):
    client = get_trading_client()
    from alpaca.trading.requests import MarketOrderRequest, StopLossRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

    order_kwargs = dict(
        symbol=req.symbol,
        qty=req.qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )

    stop_price = None
    if req.stop_loss_pct and req.stop_loss_pct > 0:
        # Get latest trade price for stop calculation
        from alpaca.data.requests import StockLatestTradeRequest
        from shared import _alpaca_client
        latest = _alpaca_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=req.symbol)
        )
        current_price = float(latest[req.symbol].price)
        stop_price = round(current_price * (1 - req.stop_loss_pct / 100), 2)
        order_kwargs["order_class"] = OrderClass.OTO
        order_kwargs["stop_loss"] = StopLossRequest(stop_price=stop_price)

    order = client.submit_order(MarketOrderRequest(**order_kwargs))

    result = {
        "order_id": str(order.id),
        "symbol": order.symbol,
        "qty": str(order.qty),
        "side": "buy",
        "status": order.status.value,
    }

    if stop_price is not None:
        result["stop_loss"] = {"stop_price": stop_price}

    return result


@router.post("/sell")
def place_sell(req: SellRequest):
    client = get_trading_client()
    from fastapi import HTTPException as _HTTPException
    from alpaca.common.exceptions import APIError
    from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

    if req.qty is None:
        try:
            client.close_position(req.symbol)
        except APIError as e:
            raise _HTTPException(status_code=404, detail=f"No open position for {req.symbol}")
        return {"symbol": req.symbol, "action": "position_closed"}

    order = client.submit_order(
        MarketOrderRequest(
            symbol=req.symbol,
            qty=req.qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
    )

    # Cancel any open stop loss orders for this symbol
    open_orders = client.get_orders(GetOrdersRequest(
        status=QueryOrderStatus.OPEN,
        symbols=[req.symbol],
    ))
    cancelled_stops = []
    for o in open_orders:
        if o.side == OrderSide.SELL and o.type.value == "stop":
            client.cancel_order_by_id(o.id)
            cancelled_stops.append(str(o.id))

    return {
        "order_id": str(order.id),
        "symbol": req.symbol,
        "qty": str(order.qty),
        "side": "sell",
        "status": order.status.value,
        "cancelled_stops": cancelled_stops,
    }


@router.post("/close-all")
def close_all_positions():
    client = get_trading_client()
    client.close_all_positions(cancel_orders=True)
    return {"action": "all_positions_closed"}


@router.post("/cancel-all")
def cancel_all_orders():
    client = get_trading_client()
    client.cancel_orders()
    return {"action": "all_orders_cancelled"}


@router.post("/scan")
def scan_signals(req: ScanRequest):
    client = get_trading_client()

    # If auto-executing, get existing positions AND pending buy orders to avoid duplicates
    existing_positions = set()
    if req.auto_execute:
        for p in client.get_all_positions():
            existing_positions.add(p.symbol)
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus, OrderSide
        open_orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
        for o in open_orders:
            if o.side == OrderSide.BUY:
                existing_positions.add(o.symbol)

    results = []
    actions = []

    for symbol in req.symbols:
        try:
            end = pd.Timestamp.now(tz='UTC')
            start = end - pd.Timedelta(days=30)

            df = _fetch(symbol, start.strftime('%Y-%m-%d'),
                        end.strftime('%Y-%m-%d'), req.interval, source='alpaca')

            indicators = compute_indicators(df["Close"])
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

            # Auto-execute with guardrails
            if req.auto_execute:
                if signal == "BUY" and symbol not in existing_positions:
                    qty = math.floor(req.position_size_usd / price)
                    if qty > 0:
                        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest
                        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

                        order_kwargs = dict(
                            symbol=symbol,
                            qty=qty,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.DAY,
                        )
                        if req.stop_loss_pct and req.stop_loss_pct > 0:
                            stop_price = round(price * (1 - req.stop_loss_pct / 100), 2)
                            order_kwargs["order_class"] = OrderClass.OTO
                            order_kwargs["stop_loss"] = StopLossRequest(stop_price=stop_price)

                        order = client.submit_order(MarketOrderRequest(**order_kwargs))
                        action = {
                            "symbol": symbol, "action": "BUY",
                            "qty": qty, "order_id": str(order.id),
                        }
                        if req.stop_loss_pct:
                            action["stop_price"] = round(price * (1 - req.stop_loss_pct / 100), 2)
                        actions.append(action)
                        existing_positions.add(symbol)

                elif signal == "SELL" and symbol in existing_positions:
                    try:
                        client.close_position(symbol)
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


@router.get("/watchlist")
def get_watchlist():
    if WATCHLIST_PATH.exists():
        return json.loads(WATCHLIST_PATH.read_text())
    return {"symbols": []}


@router.post("/watchlist")
def save_watchlist(req: WatchlistRequest):
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_PATH.write_text(json.dumps({"symbols": req.symbols}, indent=2))
    return {"symbols": req.symbols}

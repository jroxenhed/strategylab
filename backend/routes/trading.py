import json
import math
import time
from datetime import datetime, timezone
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import pandas as pd
from shared import get_trading_client, _fetch, _alpaca_client, is_retryable_error
from signal_engine import Rule, compute_indicators, eval_rules
from journal import _log_trade, DATA_DIR, JOURNAL_PATH
from models import StrategyRequest
from routes.backtest import run_backtest

WATCHLIST_PATH = DATA_DIR / "watchlist.json"

router = APIRouter(prefix="/api/trading")


def _alpaca_call(fn, *args, **kwargs):
    """Call an Alpaca SDK function, retrying once on stale connection errors."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        if is_retryable_error(e):
            print(f"[Alpaca] Stale connection, retrying {fn.__name__}...")
            return fn(*args, **kwargs)
        raise


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
    from fastapi import HTTPException as _HTTPException
    client = get_trading_client()
    try:
        account = _alpaca_call(client.get_account)
    except Exception as e:
        print(f"[Alpaca ERROR] get_account: {type(e).__name__}: {e}")
        raise _HTTPException(status_code=502, detail=f"Alpaca API error: {e}")
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
    from fastapi import HTTPException as _HTTPException
    client = get_trading_client()
    try:
        positions = _alpaca_call(client.get_all_positions)
    except Exception as e:
        print(f"[Alpaca ERROR] get_all_positions: {type(e).__name__}: {e}")
        raise _HTTPException(status_code=502, detail=f"Alpaca API error: {e}")
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
    from fastapi import HTTPException as _HTTPException
    client = get_trading_client()
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    try:
        orders = _alpaca_call(client.get_orders, GetOrdersRequest(status=QueryOrderStatus.ALL, limit=50))
    except Exception as e:
        print(f"[Alpaca ERROR] get_orders: {type(e).__name__}: {e}")
        raise _HTTPException(status_code=502, detail=f"Alpaca API error: {e}")
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

    # Get latest trade price for journal and optional stop loss
    from alpaca.data.requests import StockLatestTradeRequest
    current_price = None
    try:
        latest = _alpaca_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=req.symbol)
        )
        current_price = float(latest[req.symbol].price)
    except Exception:
        pass

    stop_price = None
    if req.stop_loss_pct and req.stop_loss_pct > 0 and current_price:
        stop_price = round(current_price * (1 - req.stop_loss_pct / 100), 2)
        order_kwargs["order_class"] = OrderClass.OTO
        order_kwargs["stop_loss"] = StopLossRequest(stop_price=stop_price)

    order = client.submit_order(MarketOrderRequest(**order_kwargs))

    _log_trade(req.symbol, "buy", req.qty, price=current_price,
               source="manual", stop_loss_price=stop_price, reason="entry")

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


def _wait_for_fill(client, order_id: str, timeout: float = 2.0) -> tuple[float | None, float | None]:
    """Poll order until filled or timeout. Returns (fill_price, fill_qty)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        order = client.get_order_by_id(order_id)
        if order.filled_avg_price is not None:
            return float(order.filled_avg_price), float(order.filled_qty)
        time.sleep(0.1)
    return None, None


def _clear_bot_entry_state(symbol: str):
    """Tell bot manager to clear entry state for this symbol so it won't double-log."""
    try:
        from routes.bots import bot_manager
        if bot_manager is None:
            return
        for bid, (cfg, state) in bot_manager.bots.items():
            if cfg.symbol.upper() == symbol.upper() and state.entry_price is not None:
                state.entry_price = None
                state.trail_peak = None
                state.trail_stop_price = None
        bot_manager.save()
    except Exception:
        pass


@router.post("/sell")
def place_sell(req: SellRequest):
    client = get_trading_client()
    from fastapi import HTTPException as _HTTPException
    from alpaca.common.exceptions import APIError
    from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

    # Cancel pending stop-loss orders first (OTO bracket legs hold shares)
    open_orders = client.get_orders(GetOrdersRequest(
        status=QueryOrderStatus.OPEN,
        symbols=[req.symbol],
    ))
    for o in open_orders:
        if o.side == OrderSide.SELL:
            client.cancel_order_by_id(o.id)

    if req.qty is None:
        try:
            resp = client.close_position(req.symbol)
        except APIError as e:
            raise _HTTPException(status_code=404, detail=f"No open position for {req.symbol}")

        # Wait for fill data instead of logging empty values
        fill_price, fill_qty = None, None
        order_id = getattr(resp, 'id', None)
        if order_id:
            fill_price, fill_qty = _wait_for_fill(client, str(order_id))

        _log_trade(req.symbol, "sell", fill_qty or 0, price=fill_price,
                   source="manual", reason="manual")
        _clear_bot_entry_state(req.symbol)
        return {"symbol": req.symbol, "action": "position_closed",
                "fill_price": fill_price, "fill_qty": fill_qty}

    order = client.submit_order(
        MarketOrderRequest(
            symbol=req.symbol,
            qty=req.qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
    )

    fill_price, fill_qty = _wait_for_fill(client, str(order.id))

    _log_trade(req.symbol, "sell", fill_qty or req.qty, price=fill_price,
               source="manual", reason="manual")
    _clear_bot_entry_state(req.symbol)

    return {
        "order_id": str(order.id),
        "symbol": req.symbol,
        "qty": str(fill_qty or order.qty),
        "side": "sell",
        "status": "filled" if fill_price else order.status.value,
        "fill_price": fill_price,
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
                        sl_price = round(price * (1 - req.stop_loss_pct / 100), 2) if req.stop_loss_pct else None
                        _log_trade(symbol, "buy", qty, price=price,
                                   source="auto", stop_loss_price=sl_price, reason="entry")
                        action = {
                            "symbol": symbol, "action": "BUY",
                            "qty": qty, "order_id": str(order.id),
                        }
                        if sl_price:
                            action["stop_price"] = sl_price
                        actions.append(action)
                        existing_positions.add(symbol)

                elif signal == "SELL" and symbol in existing_positions:
                    try:
                        client.close_position(symbol)
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


class PerformanceRequest(BaseModel):
    symbol: str
    start: str
    end: Optional[str] = None
    interval: str = "15m"
    buy_rules: list[Rule]
    sell_rules: list[Rule]
    buy_logic: str = "AND"
    sell_logic: str = "AND"


@router.post("/performance")
def get_performance(req: PerformanceRequest):
    end = req.end or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --- Journal (actual) trades ---
    journal_trades = []
    if JOURNAL_PATH.exists():
        journal = json.loads(JOURNAL_PATH.read_text())
        for t in journal.get("trades", []):
            if t["symbol"].upper() != req.symbol.upper():
                continue
            ts = t["timestamp"][:10]  # YYYY-MM-DD
            if ts < req.start or ts > end:
                continue
            journal_trades.append(t)

    buys = [t for t in journal_trades if t["side"] == "buy"]
    sells = [t for t in journal_trades if t["side"] == "sell"]

    # Pair buy→sell for P&L (simple sequential pairing)
    paired_pnl = []
    for i, buy in enumerate(buys):
        if i < len(sells) and buy.get("price") and sells[i].get("price"):
            pnl = (sells[i]["price"] - buy["price"]) * buy["qty"]
            paired_pnl.append(pnl)

    actual_total_pnl = sum(paired_pnl)
    actual_wins = sum(1 for p in paired_pnl if p > 0)
    actual_win_rate = (actual_wins / len(paired_pnl) * 100) if paired_pnl else 0

    # Build paper equity curve from paired trades (cumulative P&L)
    paper_equity = []
    cumulative = 0.0
    for i, buy in enumerate(buys):
        if i < len(paired_pnl):
            cumulative += paired_pnl[i]
            paper_equity.append({
                "time": sells[i]["timestamp"][:10],
                "value": round(cumulative, 2),
            })

    # --- Backtest (expected) ---
    try:
        bt_result = run_backtest(StrategyRequest(
            ticker=req.symbol,
            start=req.start,
            end=end,
            interval=req.interval,
            buy_rules=req.buy_rules,
            sell_rules=req.sell_rules,
            buy_logic=req.buy_logic,
            sell_logic=req.sell_logic,
            source="alpaca",
        ))
        bt_summary = bt_result["summary"]
    except Exception:
        bt_summary = None

    return {
        "symbol": req.symbol,
        "period": {"start": req.start, "end": end},
        "actual": {
            "trade_count": len(buys),
            "completed_trades": len(paired_pnl),
            "total_pnl": round(actual_total_pnl, 2),
            "win_rate_pct": round(actual_win_rate, 2),
            "equity_curve": paper_equity,
        },
        "backtest": {
            "trade_count": bt_summary["num_trades"] if bt_summary else None,
            "total_return_pct": bt_summary["total_return_pct"] if bt_summary else None,
            "win_rate_pct": bt_summary["win_rate_pct"] if bt_summary else None,
            "sharpe_ratio": bt_summary["sharpe_ratio"] if bt_summary else None,
            "equity_curve": bt_result.get("equity_curve"),
        } if bt_summary else None,
    }


@router.get("/journal")
def get_journal(symbol: Optional[str] = None):
    if not JOURNAL_PATH.exists():
        return {"trades": []}
    journal = json.loads(JOURNAL_PATH.read_text())
    trades = journal.get("trades", [])
    if symbol:
        trades = [t for t in trades if t["symbol"].upper() == symbol.upper()]
    return {"trades": trades}


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

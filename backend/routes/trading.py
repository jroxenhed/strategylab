import json
import math
import time
from datetime import datetime, timezone
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import pandas as pd
from shared import _fetch
from broker import get_trading_provider, OrderRequest as BrokerOrderRequest, _trading_providers
from broker_aggregate import aggregate_from_brokers
from signal_engine import Rule, compute_indicators, eval_rules
from journal import _log_trade, DATA_DIR, JOURNAL_PATH
from models import StrategyRequest
from routes.backtest import run_backtest

WATCHLIST_PATH = DATA_DIR / "watchlist.json"

router = APIRouter(prefix="/api/trading")


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
    try:
        provider = get_trading_provider()
        return provider.get_account()
    except Exception as e:
        print(f"[Broker ERROR] get_account: {type(e).__name__}: {e}")
        raise _HTTPException(status_code=502, detail=f"Broker API error: {e}")


@router.get("/positions")
async def get_positions(broker: str = "all"):
    result = await aggregate_from_brokers(_trading_providers, "get_positions", broker)
    return {"positions": result["rows"], "stale_brokers": result["stale_brokers"]}


@router.get("/orders")
async def get_orders(broker: str = "all"):
    result = await aggregate_from_brokers(_trading_providers, "get_orders", broker)
    return {"orders": result["rows"], "stale_brokers": result["stale_brokers"]}


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

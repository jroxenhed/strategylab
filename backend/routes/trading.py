import json
import logging
import math
import os
import time
from datetime import datetime, timezone

from fileutil import atomic_write_text
from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)
from shared import _fetch
from broker import get_trading_provider, get_active_broker, OrderRequest as BrokerOrderRequest, _trading_providers
from broker_aggregate import aggregate_from_brokers
from signal_engine import Rule, compute_indicators, eval_rules, migrate_rule
from journal import _log_trade, DATA_DIR, JOURNAL_PATH
from models import StrategyRequest, LogicField, SymbolField, SymbolList, normalize_symbol, BoundedRuleList
from routes.backtest import run_backtest

WATCHLIST_PATH = DATA_DIR / "watchlist.json"

router = APIRouter(prefix="/api/trading")


class BuyRequest(BaseModel):
    # SymbolField applies the strict normalize+regex check (F38/F85) so newlines
    # in `symbol` can't ride into _log_trade or HTTPException details.
    symbol: SymbolField
    qty: float = Field(..., gt=0)
    stop_loss_pct: Optional[float] = Field(default=None, ge=0, le=100)


class SellRequest(BaseModel):
    symbol: SymbolField
    qty: Optional[float] = Field(default=None, gt=0)
    broker: Optional[str] = None


class ScanRequest(BaseModel):
    # F149: list-level cap parity with WatchlistRequest (500) and
    # BatchQuickBacktestRequest (500). Closes the residual amplification
    # vector — a 5000-symbol POST to /scan would otherwise fan out
    # synchronously through _fetch per symbol. Declarative Field cap
    # matches the BatchQuickBacktestRequest pattern; the Watchlist
    # inline-validator pattern is preserved separately for its custom
    # error-message contract (test_watchlist_validation_caps_length).
    symbols: SymbolList = Field(min_length=1, max_length=500)
    interval: str = "15m"
    buy_rules: BoundedRuleList
    sell_rules: BoundedRuleList
    buy_logic: LogicField = "AND"
    sell_logic: LogicField = "AND"
    auto_execute: bool = False
    position_size_usd: float = 5000.0
    stop_loss_pct: Optional[float] = None

    @field_validator('symbols')
    @classmethod
    def _dedup_symbols(cls, v: list[str]) -> list[str]:
        # F93 pattern: dedup post-normalize while preserving first-occurrence order.
        return list(dict.fromkeys(v))


def _normalize_ticker_list(v) -> list:
    """Normalize a list of ticker strings: strip, uppercase, validate chars and length.

    Silently drops empty/whitespace entries. Raises ValueError for entries
    with invalid characters (outside [A-Z0-9.-]) or exceeding 10 chars.
    Reuses normalize_symbol for character validation (F38/F85 log-injection guard).
    """
    if not isinstance(v, list):
        return v
    result = []
    for t in v:
        if not isinstance(t, str) or not t.strip():
            continue
        # normalize_symbol raises ValueError on invalid chars — propagates as 422
        normalized = normalize_symbol(t)
        if len(normalized) > 10:
            raise ValueError(f"ticker too long: {normalized!r} (max 10 chars)")
        result.append(normalized)
    return result


class WatchlistGroup(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=80)
    tickers: list[str] = Field(default_factory=list, max_length=200)
    collapsed: bool = False

    @field_validator('tickers', mode='before')
    @classmethod
    def _normalize_tickers(cls, v):
        return _normalize_ticker_list(v)


class WatchlistState(BaseModel):
    groups: list[WatchlistGroup] = Field(default_factory=list, max_length=50)
    ungrouped: list[str] = Field(default_factory=list, max_length=500)

    @field_validator('ungrouped', mode='before')
    @classmethod
    def _normalize_ungrouped(cls, v):
        return _normalize_ticker_list(v)


@router.get("/account")
def get_account():
    from fastapi import HTTPException as _HTTPException
    try:
        provider = get_trading_provider()
        return provider.get_account()
    except Exception:
        logger.exception("get_account failed")
        raise _HTTPException(status_code=502, detail="Broker API unavailable")


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
        logger.exception("get_latest_price failed in /buy for %s", req.symbol)

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
               source="manual", stop_loss_price=stop_price, reason="entry",
               broker=get_active_broker())

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
        logger.exception("_clear_bot_entry_state failed for %s", symbol)


def _find_owning_bot(symbol: str, direction: str) -> str | None:
    """Return bot_id of the bot currently holding this (symbol, direction), if any.

    Manual closes pass this so the journal row attributes P&L to the bot —
    compute_realized_pnl picks it up via bot_id even though source != "bot".
    Regime (bidirectional) bots match either direction.
    """
    try:
        from routes.bots import bot_manager
        if bot_manager is None:
            return None
        sym = symbol.upper()
        for bid, (cfg, state) in bot_manager.bots.items():
            if cfg.symbol.upper() != sym:
                continue
            if state.entry_price is None:
                continue
            cfg_dir = getattr(cfg, "direction", "long")
            if cfg_dir == "regime" or cfg_dir == direction:
                return bid
        return None
    except Exception:
        logger.exception("_find_owning_bot failed for %s", symbol)
        return None


@router.post("/sell")
def place_sell(req: SellRequest):
    provider = get_trading_provider(req.broker)
    from fastapi import HTTPException as _HTTPException

    # Determine position side so cancel and journal use the correct side strings
    pos_is_short = False
    try:
        positions = provider.get_positions()
        for pos in positions:
            if pos["symbol"] == req.symbol.upper():
                pos_is_short = pos["side"] == "short"
                break
    except Exception:
        logger.exception("get_positions failed in /sell %s; defaulting to long", req.symbol)
    cancel_side = "buy" if pos_is_short else "sell"
    log_side = "cover" if pos_is_short else "sell"
    order_side = "buy" if pos_is_short else "sell"
    direction = "short" if pos_is_short else "long"
    broker_name = req.broker or get_active_broker()
    owning_bot_id = _find_owning_bot(req.symbol, direction)

    # Cancel pending stop-loss orders first
    try:
        open_orders = provider.get_orders("open", [req.symbol])
        for o in open_orders:
            if o["side"] == cancel_side:
                provider.cancel_order(o["id"])
    except Exception:
        logger.exception("cancel pending stop-loss failed before /sell %s", req.symbol)

    if req.qty is None:
        try:
            result = provider.close_position(req.symbol)
        except Exception as e:
            raise _HTTPException(status_code=404, detail=f"No open position for {req.symbol}")

        fill_price, fill_qty = _wait_for_fill(provider, result.order_id) if result.order_id else (None, None)
        _log_trade(req.symbol, log_side, fill_qty or 0, price=fill_price,
                   source="manual", reason="manual", direction=direction,
                   bot_id=owning_bot_id, broker=broker_name)
        _clear_bot_entry_state(req.symbol)
        return {"symbol": req.symbol, "action": "position_closed",
                "fill_price": fill_price, "fill_qty": fill_qty}

    result = provider.submit_order(BrokerOrderRequest(
        symbol=req.symbol,
        qty=int(req.qty),
        side=order_side,
    ))

    fill_price, fill_qty = _wait_for_fill(provider, result.order_id)

    _log_trade(req.symbol, log_side, fill_qty or req.qty, price=fill_price,
               source="manual", reason="manual", direction=direction,
               bot_id=owning_bot_id, broker=broker_name)
    _clear_bot_entry_state(req.symbol)

    return {
        "order_id": result.order_id,
        "symbol": req.symbol,
        "qty": str(int(fill_qty or result.qty)),
        "side": log_side,
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
                        end.strftime('%Y-%m-%d'), req.interval, source='alpaca-iex')

            buy_rules = [migrate_rule(r) for r in req.buy_rules]
            sell_rules = [migrate_rule(r) for r in req.sell_rules]
            all_rules = buy_rules + sell_rules
            vol = df["Volume"] if "Volume" in df.columns else None
            indicators = compute_indicators(df["Close"], high=df["High"], low=df["Low"], volume=vol, rules=all_rules)
            i = len(df) - 1

            buy_signal = eval_rules(buy_rules, req.buy_logic, indicators, i)
            sell_signal = eval_rules(sell_rules, req.sell_logic, indicators, i)

            signal = "BUY" if buy_signal else ("SELL" if sell_signal else "NONE")

            rsi_val = float(indicators["rsi_14_sma"].iloc[i])
            from indicators import compute_instance, OHLCVSeries
            _ohlcv = OHLCVSeries(close=df["Close"], high=df["High"], low=df["Low"], volume=pd.Series(dtype=float))
            ema50_val = float(compute_instance("ma", {"period": 50, "type": "ema"}, _ohlcv)["ma"].iloc[i])
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
                        _log_trade(symbol, "buy", qty, price=price, direction="long",
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
                        _log_trade(symbol, "sell", 0, price=price, direction="long", source="auto", reason="signal")
                        actions.append({
                            "symbol": symbol, "action": "SELL",
                            "detail": "position_closed",
                        })
                        existing_positions.discard(symbol)
                    except Exception:
                        logger.exception("scan auto-execute close_position failed for %s", symbol)
                        actions.append({
                            "symbol": symbol, "action": "SELL_FAILED",
                        })

            results.append(result)
        except Exception as e:
            logger.exception("scan failed for %s", symbol)
            results.append({"symbol": symbol, "signal": "ERROR", "error": "scan failed"})

    response = {"signals": results, "scanned_at": str(pd.Timestamp.now(tz='UTC'))}
    if req.auto_execute:
        response["actions"] = actions
    return response


class PerformanceRequest(BaseModel):
    symbol: SymbolField
    start: str
    end: Optional[str] = None
    interval: str = "15m"
    buy_rules: BoundedRuleList
    sell_rules: BoundedRuleList
    buy_logic: LogicField = "AND"
    sell_logic: LogicField = "AND"


@router.post("/performance")
def get_performance(req: PerformanceRequest):
    end = req.end or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --- Journal (actual) trades ---
    journal_trades = []
    if JOURNAL_PATH.exists():
        journal = json.loads(JOURNAL_PATH.read_text())
        for t in journal.get("trades", []):
            if t["symbol"].upper() != req.symbol:
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
        logger.exception("/performance backtest fallback for %s", req.symbol)
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
def get_journal(symbol: Optional[str] = None, broker: str = "all", limit: Optional[int] = None):
    if not JOURNAL_PATH.exists():
        return {"trades": []}
    journal = json.loads(JOURNAL_PATH.read_text())
    trades = journal.get("trades", [])
    if symbol:
        trades = [t for t in trades if t["symbol"].upper() == symbol.upper()]
    if broker != "all":
        trades = [t for t in trades if t.get("broker") == broker]
    if limit is not None:
        trades = trades[-limit:]
    return {"trades": trades}


def _empty_watchlist_state() -> dict:
    return {"groups": [], "ungrouped": []}


def _read_watchlist_raw() -> dict:
    """Read watchlist.json, auto-migrating legacy {symbols: [...]} shape.

    Returns a dict with keys 'groups' and 'ungrouped'. Migrates and persists
    the new shape on first read if legacy format is detected. On corrupt JSON,
    logs a warning, backs up the file, and returns empty state.
    """
    if not WATCHLIST_PATH.exists():
        return _empty_watchlist_state()
    try:
        data = json.loads(WATCHLIST_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("watchlist.json is unparseable (%s); backing up and returning empty state", exc)
        bak_path = WATCHLIST_PATH.with_suffix(".json.bak")
        try:
            import shutil
            shutil.copy2(str(WATCHLIST_PATH), str(bak_path))
        except OSError as bak_exc:
            logger.warning("could not create watchlist backup: %s", bak_exc)
        atomic_write_text(WATCHLIST_PATH, json.dumps(_empty_watchlist_state(), indent=2))
        return _empty_watchlist_state()

    # Legacy migration: {symbols: [...]} → new schema
    if "symbols" in data and "groups" not in data:
        symbols = data.get("symbols", [])
        migrated = {"groups": [], "ungrouped": [s for s in symbols if isinstance(s, str) and s.strip()]}
        logger.info("watchlist.json: migrated legacy shape (%d symbols → ungrouped)", len(migrated["ungrouped"]))
        atomic_write_text(WATCHLIST_PATH, json.dumps(migrated, indent=2))
        return migrated

    return data


def _dedup_watchlist(state: WatchlistState) -> WatchlistState:
    """Deduplicate tickers across groups + ungrouped, first occurrence wins.

    Processes groups in declaration order then ungrouped. Case-insensitive
    comparison; canonical form is whatever was in the payload (already
    normalized to uppercase by field_validator).
    """
    seen: set[str] = set()
    new_groups = []
    for group in state.groups:
        deduped = []
        for t in group.tickers:
            key = t.upper()
            if key not in seen:
                seen.add(key)
                deduped.append(t)
        new_groups.append(WatchlistGroup(
            id=group.id,
            name=group.name,
            tickers=deduped,
            collapsed=group.collapsed,
        ))
    new_ungrouped = []
    for t in state.ungrouped:
        key = t.upper()
        if key not in seen:
            seen.add(key)
            new_ungrouped.append(t)
    return WatchlistState(groups=new_groups, ungrouped=new_ungrouped)


@router.get("/watchlist")
def get_watchlist():
    return _read_watchlist_raw()


@router.post("/watchlist")
def save_watchlist(req: WatchlistState):
    deduped = _dedup_watchlist(req)
    content = json.dumps(deduped.model_dump(), indent=2)
    atomic_write_text(WATCHLIST_PATH, content)
    return deduped.model_dump()


@router.post("/watchlist/seed")
def seed_watchlist(req: WatchlistState):
    """Idempotent first-time sync from browser localStorage.

    Only writes if the on-disk file is currently empty or missing.
    Returns {seeded: true} if written, {seeded: false, reason: "already_populated"} otherwise.
    """
    existing = _read_watchlist_raw()
    has_data = bool(existing.get("groups")) or bool(existing.get("ungrouped"))
    if has_data:
        return {"seeded": False, "reason": "already_populated"}
    deduped = _dedup_watchlist(req)
    content = json.dumps(deduped.model_dump(), indent=2)
    atomic_write_text(WATCHLIST_PATH, content)
    return {"seeded": True}

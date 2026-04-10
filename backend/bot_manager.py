"""
bot_manager.py — Live trading bot engine.

Classes:
  BotConfig   — Pydantic config for one bot (what to trade, how to size, stop rules)
  BotState    — Mutable runtime state (status, trades, equity snapshots, activity log)
  BotRunner   — Async polling loop: fetch bars → eval rules → place orders
  BotManager  — Singleton managing all bots + global fund cap + persistence
"""

import asyncio
import json
import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, field_validator

from routes.backtest import (
    TrailingStopConfig,
    DynamicSizingConfig,
    TradingHoursConfig,
    StrategyRequest,
    run_backtest,
)
from signal_engine import Rule, compute_indicators, eval_rules
from shared import _fetch, get_trading_client

# ---------------------------------------------------------------------------
# Alpaca order helpers (imported lazily to avoid hard dep if Alpaca not set up)
# ---------------------------------------------------------------------------
try:
    from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest, OrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, OrderType
    _ALPACA_AVAILABLE = True
except ImportError:
    _ALPACA_AVAILABLE = False

# Poll interval per bar cadence (seconds)
POLL_INTERVALS = {"1m": 10, "5m": 15, "15m": 20, "30m": 30, "1h": 60}

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "bots.json")

# ---------------------------------------------------------------------------
# BotConfig
# ---------------------------------------------------------------------------

class BotConfig(BaseModel):
    bot_id: str = ""
    strategy_name: str
    symbol: str
    interval: str
    buy_rules: list[Rule]
    sell_rules: list[Rule]
    buy_logic: str = "AND"
    sell_logic: str = "AND"
    allocated_capital: float          # dollar slice of the bot fund for this bot
    position_size: float = 1.0        # fraction of allocated_capital per trade (0.01–1.0)

    @field_validator('position_size')
    @classmethod
    def clamp_position_size(cls, v: float) -> float:
        return max(0.01, min(1.0, v))

    stop_loss_pct: Optional[float] = None
    trailing_stop: Optional[TrailingStopConfig] = None
    dynamic_sizing: Optional[DynamicSizingConfig] = None
    trading_hours: Optional[TradingHoursConfig] = None
    slippage_pct: float = 0.0
    data_source: str = "alpaca-iex"    # yahoo | alpaca | alpaca-iex


# ---------------------------------------------------------------------------
# BotState
# ---------------------------------------------------------------------------

@dataclass
class BotState:
    status: str = "stopped"           # stopped | backtesting | running | error
    started_at: Optional[str] = None
    last_scan_at: Optional[str] = None
    last_bar_time: Optional[str] = None
    last_signal: str = "none"
    last_price: float = 0.0

    # Position tracking (mirrors backtest.py trailing stop state)
    entry_price: Optional[float] = None
    trail_peak: Optional[float] = None
    trail_stop_price: Optional[float] = None

    # Dynamic sizing state
    consec_sl_count: int = 0

    # Aggregate stats
    scans_count: int = 0
    trades_count: int = 0
    total_pnl: float = 0.0
    slippage_pcts: list = field(default_factory=list)  # list of slippage % per fill

    # History
    equity_snapshots: list = field(default_factory=list)  # [{time, value}]
    backtest_result: Optional[dict] = None
    activity_log: list = field(default_factory=list)      # [{time, msg, level}], newest first
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "started_at": self.started_at,
            "last_scan_at": self.last_scan_at,
            "last_bar_time": self.last_bar_time,
            "last_signal": self.last_signal,
            "last_price": self.last_price,
            "entry_price": self.entry_price,
            "trail_peak": self.trail_peak,
            "trail_stop_price": self.trail_stop_price,
            "consec_sl_count": self.consec_sl_count,
            "scans_count": self.scans_count,
            "trades_count": self.trades_count,
            "total_pnl": self.total_pnl,
            "slippage_pcts": self.slippage_pcts,
            "equity_snapshots": self.equity_snapshots,
            "backtest_result": self.backtest_result,
            "activity_log": self.activity_log,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BotState":
        s = cls()
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s


# ---------------------------------------------------------------------------
# BotRunner — async polling loop for one bot
# ---------------------------------------------------------------------------

class BotRunner:
    def __init__(self, config: BotConfig, state: BotState, manager: "BotManager"):
        self.config = config
        self.state = state
        self.manager = manager

    def _log(self, level: str, msg: str):
        entry = {"time": datetime.now(timezone.utc).isoformat(), "msg": msg, "level": level}
        self.state.activity_log.insert(0, entry)
        if len(self.state.activity_log) > 200:
            self.state.activity_log.pop()

    def _now_et_hhmm(self) -> str:
        """Return current ET wall-clock time as HH:MM string."""
        import zoneinfo
        et = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
        return et.strftime("%H:%M")

    def _in_trading_hours(self) -> bool:
        th = self.config.trading_hours
        if not th or not th.enabled:
            return True
        now = self._now_et_hhmm()
        if now < th.start_time or now >= th.end_time:
            return False
        for rng in th.skip_ranges:
            parts = rng.split("-")
            if len(parts) == 2 and parts[0] <= now < parts[1]:
                return False
        return True

    async def _run_in_executor(self, fn, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn, *args)

    async def _run_with_retry(self, fn, *args):
        """Run in executor, retrying once on stale connection errors."""
        try:
            return await self._run_in_executor(fn, *args)
        except Exception as e:
            if "Connection aborted" in str(e) or "RemoteDisconnected" in str(e):
                return await self._run_in_executor(fn, *args)
            raise

    async def _get_fill_price(self, client, order_id, fallback: float) -> float:
        """Poll Alpaca order briefly to get the actual fill price."""
        if not order_id:
            return fallback
        for _ in range(5):
            await asyncio.sleep(0.5)
            try:
                order = await self._run_in_executor(client.get_order_by_id, str(order_id))
                if order.filled_avg_price is not None:
                    return float(order.filled_avg_price)
            except Exception:
                break
        return fallback

    async def _tick(self):
        cfg = self.config
        state = self.state
        loop = asyncio.get_event_loop()

        state.last_scan_at = datetime.now(timezone.utc).isoformat()
        state.scans_count += 1

        # 1. Trading hours check (entries only)
        in_hours = self._in_trading_hours()

        # Skip fetch entirely when outside trading hours and no open position
        if not in_hours and state.entry_price is None:
            return

        # 2. Fetch bars (30 days back for indicator warmup)
        from datetime import timedelta, date
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=30)).isoformat()

        try:
            df = await self._run_with_retry(
                _fetch, cfg.symbol, start_date, end_date, cfg.interval, cfg.data_source
            )
        except Exception as e:
            self._log("WARN", f"Fetch failed: {e}")
            return

        if df is None or len(df) < 2:
            self._log("WARN", "Not enough bars returned")
            return

        # 3. New bar detection
        last_bar = str(df.index[-1])
        if last_bar == state.last_bar_time:
            return  # same bar, nothing to do
        state.last_bar_time = last_bar
        self._log("INFO", f"New bar: {last_bar} | close={df['Close'].iloc[-1]:.2f}")

        # 4. Compute indicators
        try:
            indicators = await self._run_in_executor(
                compute_indicators, df["Close"], df["High"], df["Low"]
            )
        except Exception as e:
            self._log("WARN", f"Indicator error: {e}")
            return

        i = len(df) - 1
        price = float(df["Close"].iloc[-1])
        state.last_price = price

        # 5. Check Alpaca for existing position (source of truth)
        has_position = False
        alpaca_qty = 0
        try:
            client = await self._run_in_executor(get_trading_client)
            positions = await self._run_with_retry(client.get_all_positions)
            for pos in positions:
                if pos.symbol == cfg.symbol.upper():
                    has_position = True
                    alpaca_qty = float(pos.qty)
                    # Resume tracking if re-started with open position
                    if state.entry_price is None:
                        state.entry_price = float(pos.avg_entry_price)
                        state.trail_peak = price
                        self._log("INFO", f"Resumed tracking position: entry={state.entry_price:.2f}")
                    break
        except Exception as e:
            self._log("WARN", f"Alpaca positions check failed: {e}")
            return

        # ---------------------------------------------------------------
        # 6. No position → evaluate buy rules
        # ---------------------------------------------------------------
        if not has_position:
            # Detect externally-closed position (e.g. Alpaca SL fill)
            if state.entry_price is not None:
                exit_price = price  # fallback to current price
                exit_qty = 0
                exit_reason = "external"
                try:
                    from alpaca.trading.requests import GetOrdersRequest
                    from alpaca.trading.enums import QueryOrderStatus
                    filled_orders = await self._run_with_retry(
                        client.get_orders,
                        GetOrdersRequest(
                            status=QueryOrderStatus.CLOSED,
                            symbols=[cfg.symbol.upper()],
                            limit=5,
                        ),
                    )
                    for o in filled_orders:
                        if o.side == OrderSide.SELL and o.filled_avg_price is not None:
                            exit_price = float(o.filled_avg_price)
                            exit_qty = float(o.filled_qty)
                            if o.type in (OrderType.STOP, OrderType.STOP_LIMIT):
                                exit_reason = "stop_loss"
                            elif o.type == OrderType.TRAILING_STOP:
                                exit_reason = "trailing_stop"
                            else:
                                exit_reason = "external"
                            break
                except Exception as e:
                    self._log("WARN", f"Failed to query filled orders: {e}")

                sell_qty = exit_qty or alpaca_qty or 0
                pnl = (exit_price - state.entry_price) * sell_qty if sell_qty else 0
                state.total_pnl += pnl

                if exit_reason in ("stop_loss", "trailing_stop"):
                    state.consec_sl_count += 1
                else:
                    state.consec_sl_count = 0

                state.equity_snapshots.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "value": round(state.total_pnl, 2),
                })

                self._log("TRADE", f"SELL {cfg.symbol} @ {exit_price:.2f} | PnL={pnl:+.2f} | reason={exit_reason} (detected)")

                try:
                    from routes.trading import _log_trade
                    _log_trade(cfg.symbol, "sell", sell_qty, exit_price, source="bot", reason=exit_reason)
                except Exception:
                    pass

                self.manager.save()

            state.entry_price = None
            state.trail_peak = None
            state.trail_stop_price = None

            if not in_hours:
                self._log("INFO", "Outside trading hours — skipping entry")
                return

            buy_signal = await self._run_in_executor(
                eval_rules, cfg.buy_rules, cfg.buy_logic, indicators, i
            )

            if buy_signal:
                # Compute effective position size (compounds P&L like backtest)
                current_capital = cfg.allocated_capital + state.total_pnl
                effective_size = max(current_capital, 0) * cfg.position_size
                if cfg.dynamic_sizing and cfg.dynamic_sizing.enabled:
                    if state.consec_sl_count >= cfg.dynamic_sizing.consec_sls:
                        effective_size *= (cfg.dynamic_sizing.reduced_pct / 100.0)
                        self._log("INFO", f"Dynamic sizing active: reduced to {cfg.dynamic_sizing.reduced_pct}%")

                qty = math.floor(effective_size / price)
                if qty < 1:
                    self._log("WARN", f"Position too small: {effective_size:.2f} / {price:.2f} = {qty} shares")
                    return

                try:
                    client = await self._run_in_executor(get_trading_client)

                    if cfg.trailing_stop and cfg.stop_loss_pct:
                        # Both trailing stop AND fixed stop: place OTO bracket as hard floor
                        # (server-side safety if bot dies), bot still manages trailing exit via polling
                        stop_price = round(price * (1 - cfg.stop_loss_pct / 100), 2)
                        order_req = MarketOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.DAY,
                            order_class=OrderClass.OTO,
                            stop_loss=StopLossRequest(stop_price=stop_price),
                        )
                    elif cfg.trailing_stop:
                        # Trailing stop only — plain market order, bot manages exit via polling
                        order_req = MarketOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.DAY,
                        )
                    elif cfg.stop_loss_pct:
                        # OTO bracket: Alpaca watches the stop server-side
                        stop_price = round(price * (1 - cfg.stop_loss_pct / 100), 2)
                        order_req = MarketOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.DAY,
                            order_class=OrderClass.OTO,
                            stop_loss=StopLossRequest(stop_price=stop_price),
                        )
                    else:
                        order_req = MarketOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.DAY,
                        )

                    order = await self._run_in_executor(client.submit_order, order_req)

                except Exception as e:
                    self._log("ERROR", f"Buy order failed: {e}")
                    return

                # Get actual fill price from Alpaca
                fill_price = await self._get_fill_price(client, order.id, price)

                state.entry_price = fill_price
                state.trail_peak = fill_price
                state.trades_count += 1
                state.last_signal = "BUY"
                slippage = fill_price - price
                slippage_pct = (slippage / price) * 100 if price else 0
                state.slippage_pcts.append(round(slippage_pct, 4))
                self._log("TRADE", f"BUY {qty} {cfg.symbol} @ {fill_price:.2f} (expected={price:.2f}, slippage={slippage_pct:+.4f}%)")

                # Log to trade journal
                try:
                    from routes.trading import _log_trade
                    _log_trade(cfg.symbol, "buy", qty, fill_price, source="bot", reason="entry",
                               expected_price=price)
                except Exception:
                    pass

                self.manager.save()

        # ---------------------------------------------------------------
        # 7. Has position → evaluate exits
        # ---------------------------------------------------------------
        else:
            exit_reason = None

            # Update trailing peak
            if cfg.trailing_stop and state.entry_price is not None:
                ts = cfg.trailing_stop
                source_price = float(df["High"].iloc[-1]) if ts.source == "high" else price
                activated = (not ts.activate_on_profit) or (
                    source_price >= state.entry_price * (1 + ts.activate_pct / 100)
                )
                if activated:
                    if state.trail_peak is None or source_price > state.trail_peak:
                        state.trail_peak = source_price

                    # Compute trail stop price
                    atr_val = float(indicators.get("atr", {}).get(i, 0) or 0)
                    if ts.type == "pct":
                        state.trail_stop_price = state.trail_peak * (1 - ts.value / 100)
                    elif ts.type == "atr" and atr_val:
                        state.trail_stop_price = state.trail_peak - ts.value * atr_val

            # Check exits in priority order
            if cfg.stop_loss_pct and state.entry_price and not cfg.trailing_stop:
                # Fixed stop managed by bot (not OTO) — shouldn't normally happen
                # but handle gracefully
                if price <= state.entry_price * (1 - cfg.stop_loss_pct / 100):
                    exit_reason = "stop_loss"

            if exit_reason is None and cfg.trailing_stop and state.trail_stop_price:
                if price <= state.trail_stop_price:
                    exit_reason = "trailing_stop"

            if exit_reason is None:
                sell_signal = await self._run_in_executor(
                    eval_rules, cfg.sell_rules, cfg.sell_logic, indicators, i
                )
                if sell_signal:
                    exit_reason = "signal"

            if exit_reason:
                try:
                    client = await self._run_in_executor(get_trading_client)
                    # Cancel pending stop-loss orders for this symbol first
                    # (OTO bracket legs hold shares, blocking close_position)
                    try:
                        from alpaca.trading.requests import GetOrdersRequest
                        from alpaca.trading.enums import QueryOrderStatus
                        orders = await self._run_with_retry(
                            client.get_orders,
                            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[cfg.symbol.upper()]),
                        )
                        for o in orders:
                            if o.side == OrderSide.SELL:
                                await self._run_with_retry(client.cancel_order_by_id, o.id)
                                self._log("INFO", f"Cancelled pending {o.type.value} order {o.id}")
                    except Exception as e:
                        self._log("WARN", f"Cancel orders failed: {e}")
                    close_resp = await self._run_with_retry(client.close_position, cfg.symbol.upper())
                except Exception as e:
                    self._log("ERROR", f"Close position failed: {e}")
                    return

                # Get actual fill price
                order_id = getattr(close_resp, 'id', None) or (close_resp.get('id') if isinstance(close_resp, dict) else None)
                sell_fill = await self._get_fill_price(client, order_id, price) if order_id else price

                pnl = (sell_fill - state.entry_price) * alpaca_qty if state.entry_price else 0
                state.total_pnl += pnl
                state.last_signal = f"SELL ({exit_reason})"

                # Update dynamic sizing counter
                if exit_reason in ("stop_loss", "trailing_stop"):
                    state.consec_sl_count += 1
                else:
                    state.consec_sl_count = 0

                state.equity_snapshots.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "value": round(state.total_pnl, 2),
                })

                slippage = sell_fill - price
                slippage_pct = (slippage / price) * 100 if price else 0
                state.slippage_pcts.append(round(slippage_pct, 4))
                self._log("TRADE", f"SELL {cfg.symbol} @ {sell_fill:.2f} | PnL={pnl:+.2f} | reason={exit_reason} (expected={price:.2f}, slippage={slippage_pct:+.4f}%)")

                try:
                    from routes.trading import _log_trade
                    _log_trade(cfg.symbol, "sell", alpaca_qty, sell_fill, source="bot", reason=exit_reason,
                               expected_price=price)
                except Exception:
                    pass

                state.entry_price = None
                state.trail_peak = None
                state.trail_stop_price = None
                self.manager.save()

    async def run(self):
        self.state.status = "running"
        self.state.started_at = datetime.now(timezone.utc).isoformat()
        self._log("INFO", f"Bot started: {self.config.symbol} {self.config.interval}")
        self.manager.save()

        interval_secs = POLL_INTERVALS.get(self.config.interval, 30)
        while True:
            try:
                await self._tick()
            except Exception as e:
                self.state.status = "error"
                self.state.error_message = str(e)
                self._log("ERROR", f"Fatal error: {e}")
                self.manager.save()
                break
            await asyncio.sleep(interval_secs)


# ---------------------------------------------------------------------------
# BotManager — singleton
# ---------------------------------------------------------------------------

class BotManager:
    def __init__(self):
        self.bot_fund: float = 0.0
        self.bots: dict[str, tuple[BotConfig, BotState]] = {}  # bot_id → (config, state)
        self.tasks: dict[str, asyncio.Task] = {}               # bot_id → running Task

    # -- Fund management ----------------------------------------------------

    def set_bot_fund(self, amount: float):
        allocated = sum(c.allocated_capital for c, _ in self.bots.values())
        if amount < allocated:
            raise ValueError(
                f"Cannot set bot fund to {amount:.2f} — {allocated:.2f} is already allocated"
            )
        self.bot_fund = amount
        self.save()

    def get_fund_status(self) -> dict:
        allocated = sum(c.allocated_capital for c, _ in self.bots.values())
        return {
            "bot_fund": self.bot_fund,
            "allocated": round(allocated, 2),
            "available": round(self.bot_fund - allocated, 2),
        }

    def _validate_allocation(self, amount: float, exclude_bot_id: str = ""):
        allocated = sum(
            c.allocated_capital for bid, (c, _) in self.bots.items()
            if bid != exclude_bot_id
        )
        if allocated + amount > self.bot_fund:
            available = self.bot_fund - allocated
            raise ValueError(
                f"Allocation ${amount:.2f} exceeds available fund ${available:.2f}"
            )

    # -- Bot lifecycle -------------------------------------------------------

    def add_bot(self, config: BotConfig) -> str:
        if self.bot_fund == 0:
            raise ValueError("Bot fund is not set. Set a bot fund before adding bots.")
        self._validate_allocation(config.allocated_capital)
        bot_id = str(uuid.uuid4())
        config.bot_id = bot_id
        self.bots[bot_id] = (config, BotState())
        self.save()
        return bot_id

    def start_bot(self, bot_id: str):
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        config, state = self.bots[bot_id]
        if bot_id in self.tasks and not self.tasks[bot_id].done():
            raise ValueError(f"Bot {bot_id} is already running")
        # Guard: no two bots on the same symbol
        for bid, task in self.tasks.items():
            if bid != bot_id and not task.done():
                other_cfg, _ = self.bots[bid]
                if other_cfg.symbol == config.symbol:
                    raise ValueError(
                        f"Bot {bid} is already running on {config.symbol}"
                    )
        runner = BotRunner(config, state, self)
        task = asyncio.create_task(runner.run())
        self.tasks[bot_id] = task

    def stop_bot(self, bot_id: str, close_position: bool = False):
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        if bot_id in self.tasks:
            self.tasks[bot_id].cancel()
            del self.tasks[bot_id]
        config, state = self.bots[bot_id]
        state.status = "stopped"
        state.entry_price = None
        state.trail_peak = None
        state.trail_stop_price = None

        if close_position:
            try:
                client = get_trading_client()
                client.close_position(config.symbol.upper())
            except Exception:
                pass

        self.save()

    def backtest_bot(self, bot_id: str):
        """Run a synchronous backtest using the bot's config; caches result on state."""
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        config, state = self.bots[bot_id]
        state.status = "backtesting"
        self.save()

        from datetime import date, timedelta
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=365)).isoformat()

        req = StrategyRequest(
            ticker=config.symbol,
            start=start,
            end=end,
            interval=config.interval,
            buy_rules=config.buy_rules,
            sell_rules=config.sell_rules,
            buy_logic=config.buy_logic,
            sell_logic=config.sell_logic,
            initial_capital=config.allocated_capital,
            position_size=config.position_size,
            stop_loss_pct=config.stop_loss_pct,
            trailing_stop=config.trailing_stop,
            dynamic_sizing=config.dynamic_sizing,
            trading_hours=config.trading_hours,
            slippage_pct=config.slippage_pct,
            source=config.data_source,
        )

        try:
            result = run_backtest(req)
            state.backtest_result = {
                "summary": result.get("summary", {}),
                "equity_curve": result.get("equity_curve", []),
            }
        except Exception as e:
            state.backtest_result = {"error": str(e)}
        finally:
            state.status = "stopped"
            self.save()

    def get_bot(self, bot_id: str) -> tuple[BotConfig, BotState]:
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        return self.bots[bot_id]

    def manual_buy(self, bot_id: str) -> dict:
        """Place a manual buy for a bot using its allocation config."""
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        config, state = self.bots[bot_id]

        if state.entry_price is not None:
            raise ValueError("Bot already has an open position")
        if state.status != "running":
            raise ValueError("Bot must be running to place a manual buy")

        client = get_trading_client()

        # Get current price
        from shared import _fetch
        from datetime import date, timedelta
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=5)).isoformat()
        df = _fetch(config.symbol, start_date, end_date, config.interval, config.data_source)
        price = float(df["Close"].iloc[-1])

        # Calculate qty
        current_capital = config.allocated_capital + state.total_pnl
        effective_size = max(current_capital, 0) * config.position_size
        qty = math.floor(effective_size / price)
        if qty < 1:
            raise ValueError(f"Position too small: ${effective_size:.2f} / ${price:.2f}")

        # Submit order
        order = client.submit_order(MarketOrderRequest(
            symbol=config.symbol.upper(),
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        ))

        # Get fill price (blocking poll)
        import time
        fill_price = price
        for _ in range(5):
            time.sleep(0.5)
            try:
                o = client.get_order_by_id(str(order.id))
                if o.filled_avg_price is not None:
                    fill_price = float(o.filled_avg_price)
                    break
            except Exception:
                break

        # Update bot state
        slippage_pct = ((fill_price - price) / price) * 100 if price else 0
        state.slippage_pcts.append(round(slippage_pct, 4))
        state.entry_price = fill_price
        state.trail_peak = fill_price
        state.trades_count += 1
        state.last_signal = "BUY (manual)"

        # Log
        runner = BotRunner(config, state, self)
        runner._log("TRADE", f"BUY {qty} {config.symbol} @ {fill_price:.2f} (manual, expected={price:.2f}, slippage={slippage_pct:+.4f}%)")

        try:
            from routes.trading import _log_trade
            _log_trade(config.symbol, "buy", qty, fill_price, source="bot", reason="manual_entry",
                       expected_price=price)
        except Exception:
            pass

        self.save()
        return {"qty": qty, "fill_price": fill_price, "slippage_pct": round(slippage_pct, 4)}

    def list_bots(self) -> list[dict]:
        result = []
        for bot_id, (config, state) in self.bots.items():
            result.append({
                "bot_id": bot_id,
                "strategy_name": config.strategy_name,
                "symbol": config.symbol,
                "interval": config.interval,
                "allocated_capital": config.allocated_capital,
                "status": state.status,
                "trades_count": state.trades_count,
                "total_pnl": round(state.total_pnl, 2),
                "backtest_summary": state.backtest_result.get("summary") if state.backtest_result else None,
                "data_source": config.data_source,
                "avg_slippage_pct": round(sum(state.slippage_pcts) / len(state.slippage_pcts), 4) if state.slippage_pcts else None,
                "has_position": state.entry_price is not None,
            })
        return result

    def delete_bot(self, bot_id: str):
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        if bot_id in self.tasks and not self.tasks[bot_id].done():
            raise ValueError("Stop the bot before deleting it")
        del self.bots[bot_id]
        self.tasks.pop(bot_id, None)
        self.save()

    # -- Persistence ---------------------------------------------------------

    def save(self):
        data = {
            "bot_fund": self.bot_fund,
            "bots": [
                {"config": config.model_dump(), "state": state.to_dict()}
                for config, state in self.bots.values()
            ],
        }
        os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
        with open(DATA_PATH, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def load(self):
        if not os.path.exists(DATA_PATH):
            return
        try:
            with open(DATA_PATH) as f:
                data = json.load(f)
            self.bot_fund = data.get("bot_fund", 0.0)
            for entry in data.get("bots", []):
                config = BotConfig(**entry["config"])
                state = BotState.from_dict(entry.get("state", {}))
                state.status = "stopped"  # always start stopped after server restart
                self.bots[config.bot_id] = (config, state)
        except Exception as e:
            print(f"[BotManager] Failed to load bots.json: {e}")

    async def shutdown(self):
        for bot_id, task in list(self.tasks.items()):
            task.cancel()
        self.save()

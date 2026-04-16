"""
bot_manager.py — Live trading bot engine.

Classes:
  BotConfig   — Pydantic config for one bot (what to trade, how to size, stop rules)
  BotState    — Mutable runtime state (status, trades, equity snapshots, activity log)
  BotManager  — Singleton managing all bots + global fund cap + persistence
"""

import asyncio
import json
import logging
import math
import os
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from slippage import slippage_cost_bps, fill_bias_bps

from models import TrailingStopConfig, DynamicSizingConfig, SkipAfterStopConfig, TradingHoursConfig, StrategyRequest
from routes.backtest import run_backtest
from signal_engine import Rule
from shared import _fetch
from broker import get_trading_provider, OrderRequest as BrokerOrderRequest
from journal import _log_trade, compute_realized_pnl, first_bot_entry_time, compute_bot_avg_cost_bps
from bot_runner import BotRunner


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
    skip_after_stop: Optional[SkipAfterStopConfig] = None
    trading_hours: Optional[TradingHoursConfig] = None
    slippage_bps: float = Field(default=2.0, ge=0.0)
    max_spread_bps: Optional[float] = None  # skip entries when bid/ask spread exceeds this; None = disabled
    pnl_epoch: Optional[str] = None          # ISO timestamp; only journal rows >= this count toward displayed P&L
    data_source: str = "alpaca-iex"    # yahoo | alpaca | alpaca-iex | ibkr
    direction: str = "long"            # "long" | "short"
    broker: str = "alpaca"             # "alpaca" | "ibkr" — which broker executes orders


# ---------------------------------------------------------------------------
# BotState
# ---------------------------------------------------------------------------

@dataclass
class BotState:
    status: str = "stopped"           # stopped | backtesting | running | error
    started_at: Optional[str] = None
    last_scan_at: Optional[str] = None
    last_tick: Optional[str] = None
    last_bar_time: Optional[str] = None
    last_signal: str = "none"
    last_price: float = 0.0

    # Position tracking (mirrors backtest.py trailing stop state)
    entry_price: Optional[float] = None
    trail_peak: Optional[float] = None
    trail_stop_price: Optional[float] = None

    # Pending close: set when the bot submits an exit order, consumed once the
    # fill is observed. Lets a later "externally-closed" tick recognize its own
    # order and label it with the real reason instead of "external".
    pending_close_order_id: Optional[str] = None
    pending_close_reason: Optional[str] = None

    # Dynamic sizing state
    consec_sl_count: int = 0
    skip_remaining: int = 0           # entries remaining to skip after a qualifying stop

    # Aggregate stats
    scans_count: int = 0
    trades_count: int = 0
    total_pnl: float = 0.0
    slippage_bps: list = field(default_factory=list)  # list of unsigned cost (bps) per fill

    # History
    equity_snapshots: list = field(default_factory=list)  # [{time, value}]
    backtest_result: Optional[dict] = None
    activity_log: list = field(default_factory=list)      # [{time, msg, level}], newest first
    error_message: Optional[str] = None
    pause_reason: Optional[str] = None   # set by IBKR error handler on structural rejects

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "started_at": self.started_at,
            "last_scan_at": self.last_scan_at,
            "last_tick": self.last_tick,
            "last_bar_time": self.last_bar_time,
            "last_signal": self.last_signal,
            "last_price": self.last_price,
            "entry_price": self.entry_price,
            "trail_peak": self.trail_peak,
            "trail_stop_price": self.trail_stop_price,
            "pending_close_order_id": self.pending_close_order_id,
            "pending_close_reason": self.pending_close_reason,
            "consec_sl_count": self.consec_sl_count,
            "skip_remaining": self.skip_remaining,
            "scans_count": self.scans_count,
            "trades_count": self.trades_count,
            "total_pnl": self.total_pnl,
            "slippage_bps": self.slippage_bps,
            "equity_snapshots": self.equity_snapshots,
            "backtest_result": self.backtest_result,
            "activity_log": self.activity_log,
            "error_message": self.error_message,
            "pause_reason": self.pause_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BotState":
        s = cls()
        # Lazy migration: legacy bots.json has slippage_pcts (signed %); scale to bps (unsigned).
        # max(0, ...) retroactively applies the new "cost >= 0" rule to stored favorable values.
        if "slippage_pcts" in d and "slippage_bps" not in d:
            d = {**d, "slippage_bps": [max(0.0, v) * 100 for v in d["slippage_pcts"] or []]}
            d.pop("slippage_pcts", None)
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s


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
        # Guard: no two bots on the same symbol AND direction
        for bid, task in self.tasks.items():
            if bid != bot_id and not task.done():
                other_cfg, _ = self.bots[bid]
                if other_cfg.symbol == config.symbol and other_cfg.direction == config.direction:
                    raise ValueError(
                        f"Bot {bid} is already running {config.direction} on {config.symbol}"
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
                provider = get_trading_provider(config.broker)
                provider.close_position(config.symbol.upper())
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
            skip_after_stop=config.skip_after_stop,
            trading_hours=config.trading_hours,
            slippage_bps=config.slippage_bps,
            source=config.data_source,
            direction=config.direction,
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

    def update_bot(self, bot_id: str, updates: dict):
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        config, state = self.bots[bot_id]
        if state.status == "running":
            raise ValueError("Stop the bot before editing its config")
        # Apply updates to config
        config_dict = config.model_dump()
        config_dict.update(updates)
        new_config = BotConfig(**config_dict)
        self.bots[bot_id] = (new_config, state)
        self.save()

    def manual_buy(self, bot_id: str) -> dict:
        """Place a manual buy for a bot using its allocation config."""
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        config, state = self.bots[bot_id]

        if state.entry_price is not None:
            raise ValueError("Bot already has an open position")
        if state.status != "running":
            raise ValueError("Bot must be running to place a manual buy")

        provider = get_trading_provider(config.broker)

        # Get current price
        from datetime import date, timedelta
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=5)).isoformat()
        df = _fetch(config.symbol, start_date, end_date, config.interval, config.data_source)
        price = float(df["Close"].iloc[-1])

        # Calculate qty
        current_capital = config.allocated_capital + compute_realized_pnl(config.symbol, config.direction, bot_id=config.bot_id)
        effective_size = max(current_capital, 0) * config.position_size
        qty = math.floor(effective_size / price)
        if qty < 1:
            raise ValueError(f"Position too small: ${effective_size:.2f} / ${price:.2f}")

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

        # Update bot state
        side_key = "short" if is_short else "buy"
        cost_bps = slippage_cost_bps(side_key, expected=price, fill=fill_price)
        bias_bps = fill_bias_bps(side_key, expected=price, fill=fill_price)
        state.slippage_bps.append(round(cost_bps, 2))
        state.entry_price = fill_price
        state.trail_peak = fill_price
        state.trades_count += 1
        side_label = "SHORT" if is_short else "BUY"
        state.last_signal = f"{side_label} (manual)"

        # Log
        runner = BotRunner(config, state, self)
        runner._log(
            "TRADE",
            f"{side_label} {qty} {config.symbol} @ {fill_price:.2f} "
            f"(manual, expected={price:.2f}, cost={cost_bps:.1f}bps, bias={bias_bps:+.1f}bps)",
        )

        try:
            _log_trade(config.symbol, "short" if is_short else "buy", qty, fill_price,
                       source="bot", reason="manual_entry", expected_price=price,
                       direction=config.direction, bot_id=config.bot_id)
        except Exception:
            pass

        self.save()
        return {"qty": qty, "fill_price": fill_price, "slippage_bps": round(cost_bps, 2)}

    def list_bots(self) -> list[dict]:
        result = []
        for bot_id, (config, state) in self.bots.items():
            epoch = config.pnl_epoch
            first_trade_time = first_bot_entry_time(config.symbol, config.direction, bot_id=bot_id, since=epoch)
            if first_trade_time is None and state.equity_snapshots:
                first_trade_time = state.equity_snapshots[0]["time"]
            result.append({
                "bot_id": bot_id,
                "strategy_name": config.strategy_name,
                "symbol": config.symbol,
                "interval": config.interval,
                "allocated_capital": config.allocated_capital,
                "status": state.status,
                "trades_count": state.trades_count,
                "total_pnl": round(compute_realized_pnl(config.symbol, config.direction, bot_id=bot_id, since=epoch), 2),
                "backtest_summary": state.backtest_result.get("summary") if state.backtest_result else None,
                "data_source": config.data_source,
                "direction": config.direction,
                "broker": config.broker,
                "avg_cost_bps": compute_bot_avg_cost_bps(config.symbol, bot_id=bot_id, since=epoch)[0],
                "has_position": state.entry_price is not None,
                "first_trade_time": first_trade_time,
                "pnl_epoch": epoch,
                "last_tick": state.last_tick,
                "pause_reason": state.pause_reason,
                "equity_snapshots": state.equity_snapshots,
            })
        return result

    def reset_pnl(self, bot_id: str) -> str:
        """Bump pnl_epoch to now so displayed P&L/trades/slippage start fresh.
        Journal rows aren't touched — they remain for audit/export.
        """
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        config, state = self.bots[bot_id]
        epoch = datetime.now(timezone.utc).isoformat()
        config.pnl_epoch = epoch
        state.trades_count = 0
        state.slippage_bps = []
        state.equity_snapshots = []
        self.save()
        return epoch

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
                cfg_dict = entry["config"]
                # Lazy migration: old key 'slippage_pct' (percent) → 'slippage_bps' (bps).
                # max(0, ...) retroactively applies the "cost >= 0" rule.
                if "slippage_pct" in cfg_dict and "slippage_bps" not in cfg_dict:
                    cfg_dict = {**cfg_dict, "slippage_bps": max(0.0, cfg_dict["slippage_pct"]) * 100}
                    cfg_dict.pop("slippage_pct", None)
                config = BotConfig(**cfg_dict)
                state = BotState.from_dict(entry.get("state", {}))
                state.status = "stopped"  # always start stopped after server restart
                self.bots[config.bot_id] = (config, state)
        except Exception:
            logger.exception("Failed to load bots.json")

    async def shutdown(self):
        for bot_id, task in list(self.tasks.items()):
            task.cancel()
        self.save()

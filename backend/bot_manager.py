"""
bot_manager.py — Live trading bot engine.

Classes:
  BotConfig   — Pydantic config for one bot (what to trade, how to size, stop rules)
  BotState    — Mutable runtime state (status, trades, equity snapshots, activity log)
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

from models import TrailingStopConfig, DynamicSizingConfig, TradingHoursConfig, StrategyRequest
from routes.backtest import run_backtest
from signal_engine import Rule
from shared import _fetch, get_trading_client
from journal import _log_trade
from bot_runner import BotRunner

# ---------------------------------------------------------------------------
# Alpaca order helpers (imported lazily to avoid hard dep if Alpaca not set up)
# ---------------------------------------------------------------------------
try:
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    _ALPACA_AVAILABLE = True
except ImportError:
    _ALPACA_AVAILABLE = False

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
    direction: str = "long"            # "long" | "short"


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
        is_short = config.direction == "short"
        order = client.submit_order(MarketOrderRequest(
            symbol=config.symbol.upper(),
            qty=qty,
            side=OrderSide.SELL if is_short else OrderSide.BUY,
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
        if is_short:
            slippage = price - fill_price  # lower fill is worse for short seller
        else:
            slippage = fill_price - price  # higher fill is worse for buyer
        slippage_pct = (slippage / price) * 100 if price else 0
        state.slippage_pcts.append(round(slippage_pct, 4))
        state.entry_price = fill_price
        state.trail_peak = fill_price
        state.trades_count += 1
        side_label = "SHORT" if is_short else "BUY"
        state.last_signal = f"{side_label} (manual)"

        # Log
        runner = BotRunner(config, state, self)
        runner._log("TRADE", f"{side_label} {qty} {config.symbol} @ {fill_price:.2f} (manual, expected={price:.2f}, slippage={slippage_pct:+.4f}%)")

        try:
            _log_trade(config.symbol, "short" if is_short else "buy", qty, fill_price,
                       source="bot", reason="manual_entry", expected_price=price,
                       direction=config.direction)
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
                "direction": config.direction,
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

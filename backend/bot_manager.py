"""
bot_manager.py — Live trading bot engine.

Classes:
  BotConfig   — Pydantic config for one bot (what to trade, how to size, stop rules)
  BotState    — Mutable runtime state (status, trades, equity snapshots, activity log)
  BotManager  — Singleton managing all bots + global fund cap + persistence
"""

import asyncio
import copy
import json
import logging
import math
import os
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from fileutil import atomic_write_text
from slippage import slippage_cost_bps, fill_bias_bps

from models import TrailingStopConfig, DynamicSizingConfig, SkipAfterStopConfig, TradingHoursConfig, StrategyRequest, RegimeConfig, LogicField, DirectionField, BoundedRuleList, OptionalBoundedRuleList, SymbolField, normalize_symbol, Interval, IntervalField
from nodebuilder.models import Graph, GraphValidationError
from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.run import run_graph_backtest
from nodebuilder.trading.sim_bridge import apply_to_bot_config
from routes.backtest import run_backtest
from signal_engine import migrate_rule, Rule
from shared import _fetch
from broker import get_trading_provider, OrderRequest as BrokerOrderRequest
from journal import (_log_trade, _load_trades, compute_realized_pnl, first_bot_entry_time,
                     compute_bidirectional_pnl, first_bot_bidirectional_entry_time,
                     compute_bot_avg_cost_bps, DATA_DIR)
from bot_runner import BotRunner, compile_bot_graph, graph_bot_live


DATA_PATH = str(DATA_DIR / "bots.json")


class SymbolConflictError(ValueError):
    """Two bots would trade the same symbol in a way the exclusive-symbol
    guard refuses (same direction, or either one trades both sides).
    ``names`` holds the spawn group names involved, for the route."""

    def __init__(self, message: str, names: tuple = ()) -> None:
        super().__init__(message)
        self.names = tuple(names)


class InPositionError(ValueError):
    """The bot holds a position, so its graph cannot be changed."""


class ReferenceUnavailableError(Exception):
    """A reference Ticker a graph bot's group reads cannot be fetched on the
    bot's data source (F435 W5 LM-1).  ``detail`` is the API refusal body:
    code ``reference_unavailable``, message, symbol, interval, data_source,
    group and groups."""

    def __init__(self, detail: dict) -> None:
        super().__init__(detail.get("message", "reference_unavailable"))
        self.detail = detail


def probe_references(config: "BotConfig", program=None, *, seen: Optional[dict] = None) -> None:
    """Fetch each reference frame a graph bot's group reads, once, on the
    bot's data source, over the window the bot's ticks fetch (F435 W5 LM-1).

    Raises ReferenceUnavailableError for the first one that fails or comes
    back empty, so a symbol the source does not serve (an index on
    alpaca-iex, say) is refused at spawn, add and start instead of leaving a
    bot that can never work out a signal.  Nothing for a rule bot or a
    group with no reference.  *program* skips the compile; *seen* shares the
    results ((symbol, interval, source) -> problem or None) across the legs
    of one spawn.  Network I/O and maybe a compile: thread pool only, never
    the event loop."""
    if config.kind != "graph" or config.graph is None:
        return
    from datetime import date
    from nodebuilder.prepare import live_fetch_start
    from shared import provider_max_days

    if program is None:
        program = compile_bot_graph(config.graph, config.bot_id)
    live = graph_bot_live(program, config)
    own = (str(config.symbol).strip().upper(), config.interval)
    source = config.data_source
    for sym, itv in live.references:
        if (sym, itv) == own:
            continue
        key = (sym, itv, source)
        if seen is not None and key in seen:
            why = seen[key]
        else:
            why = None
            try:
                start = live_fetch_start(live.program, itv, config.trailing_stop,
                                         max_days=provider_max_days(source, itv))
                df = _fetch(sym, start, date.today().isoformat(), itv, source)
                if df is None or len(df) == 0:
                    why = "no bars came back"
            except Exception as exc:  # any provider error: the bot could not run
                why = str(getattr(exc, "detail", None) or exc) or type(exc).__name__
            if seen is not None:
                seen[key] = why
        if why is not None:
            group = config.graph_group or getattr(live.group, "name", None)
            raise ReferenceUnavailableError({
                "code": "reference_unavailable",
                "message": (f"Reference ticker {sym} ({itv}) could not be loaded from "
                            f"{source}: {why}."),
                "symbol": sym, "interval": itv, "data_source": source,
                "group": group, "groups": [group] if group else [],
            })


def symbols_conflict(a: "BotConfig", b: "BotConfig") -> bool:
    """True when the exclusive-symbol guard keeps *a* and *b* from running
    together: the same symbol, and the same direction or either one a bot
    that trades both sides (is_bidirectional)."""
    if normalize_symbol(a.symbol) != normalize_symbol(b.symbol):
        return False
    return a.is_bidirectional or b.is_bidirectional or a.direction == b.direction


def _side_label(cfg: "BotConfig") -> str:
    return "both sides" if cfg.is_bidirectional else cfg.direction

# ---------------------------------------------------------------------------
# BotConfig
# ---------------------------------------------------------------------------

class BotConfig(BaseModel):
    bot_id: str = ""
    strategy_name: str
    symbol: SymbolField
    interval: IntervalField
    # F128: bound O(n_rules × n_bars) per tick — same cap as backtest (F102).
    buy_rules: BoundedRuleList
    sell_rules: BoundedRuleList
    buy_logic: LogicField = "AND"
    sell_logic: LogicField = "AND"
    allocated_capital: float          # dollar slice of the bot fund for this bot
    position_size: float = 1.0        # fraction of allocated_capital per trade (0.01–1.0)

    @field_validator('position_size')
    @classmethod
    def clamp_position_size(cls, v: float) -> float:
        return max(0.01, min(1.0, v))

    stop_loss_pct: Optional[float] = None
    trailing_stop: Optional[TrailingStopConfig] = None
    max_bars_held: Optional[int] = None
    dynamic_sizing: Optional[DynamicSizingConfig] = None
    skip_after_stop: Optional[SkipAfterStopConfig] = None
    trading_hours: Optional[TradingHoursConfig] = None
    slippage_bps: float = Field(default=2.0, ge=0.0)
    max_spread_bps: Optional[float] = None  # skip entries when bid/ask spread exceeds this; None = disabled
    drawdown_threshold_pct: Optional[float] = None  # auto-pause if peak-to-trough exceeds this % of allocated_capital
    pnl_epoch: Optional[str] = None          # ISO timestamp; only journal rows >= this count toward displayed P&L
    data_source: str = "alpaca-iex"    # yahoo | alpaca | alpaca-iex | ibkr
    direction: DirectionField = "long"
    broker: str = "alpaca"             # "alpaca" | "ibkr" — which broker executes orders
    regime: Optional[RegimeConfig] = None
    # B23/D24: dual rule sets for regime bots (None = use buy_rules/sell_rules)
    long_buy_rules: OptionalBoundedRuleList
    long_sell_rules: OptionalBoundedRuleList
    long_buy_logic: LogicField = "AND"
    long_sell_logic: LogicField = "AND"
    short_buy_rules: OptionalBoundedRuleList
    short_sell_rules: OptionalBoundedRuleList
    short_buy_logic: LogicField = "AND"
    short_sell_logic: LogicField = "AND"
    # B25: per-direction settings (only used when regime is active)
    long_stop_loss_pct: Optional[float] = None
    short_stop_loss_pct: Optional[float] = None
    long_trailing_stop: Optional[TrailingStopConfig] = None
    short_trailing_stop: Optional[TrailingStopConfig] = None
    long_max_bars_held: Optional[int] = None
    short_max_bars_held: Optional[int] = None
    long_position_size: Optional[float] = None
    short_position_size: Optional[float] = None
    borrow_rate_annual: float = Field(default=0.5, ge=0)
    kind: Literal["rule", "graph"] = "rule"  # default "rule" for existing bots.json safety
    graph: Optional[Graph] = None
    # F435 W5 (plan D7): which saved graph, revision and Output Group this
    # graph bot runs.  `graph` is the bot's own snapshot of that revision, so
    # a later save of the graph never changes a live bot; graph_update moves
    # graph and graph_rev together.  graph_direction_mode is the group's
    # direction ("regime_switch" trades both sides; `direction` is then
    # "long").  All None for rule bots and for graph bots made before W5.
    graph_id: Optional[str] = None
    graph_rev: Optional[int] = None
    graph_group: Optional[str] = None
    graph_direction_mode: Optional[Literal["long", "short", "regime_switch"]] = None

    @property
    def is_bidirectional(self) -> bool:
        """True when the bot can hold either side: a rule bot with its regime
        on, or a graph bot running a regime_switch group.  Such a bot sizes
        from the P&L of both sides and needs its symbol to itself."""
        return bool(self.regime and self.regime.enabled) or (
            self.kind == "graph" and self.graph_direction_mode == "regime_switch")

    @field_validator('long_position_size', 'short_position_size', mode='before')
    @classmethod
    def clamp_dir_position_size(cls, v):
        if v is None:
            return v
        return max(0.01, min(1.0, v))

    @model_validator(mode='after')
    def _validate_graph_kind(self):
        # Soft validation: WARN, do not raise. Required so stop+edit+start
        # transitions don't hard-fail.
        if self.kind == "graph" and self.graph is None:
            logger.warning("BotConfig kind=graph but graph is None (id=%s)", getattr(self, 'bot_id', '<unknown>'))
        return self


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
    entry_time: Optional[str] = None
    entry_bar_count: int = 0
    trail_peak: Optional[float] = None
    trail_stop_price: Optional[float] = None
    # A graph bot with a wired Stop terminal: the stop (%) read at the entry
    # bar, fixed until the exit, as the backtest does (None: no such stop).
    entry_stop_pct: Optional[float] = None

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
    slippage_bps: list = field(default_factory=list)  # list of unsigned cost (bps) per fill

    # History
    equity_snapshots: list = field(default_factory=list)  # [{time, value}]
    backtest_summary: Optional[dict] = None               # summary stats only (equity curve not persisted)
    activity_log: list = field(default_factory=list)      # [{time, msg, level}], newest first
    error_message: Optional[str] = None
    pause_reason: Optional[str] = None   # set by IBKR error handler on structural rejects

    # D24: regime live bot state
    regime_direction: Optional[str] = None   # "long" | "short" | "flat" — current evaluated regime
    position_direction: Optional[str] = None  # direction of current open position (None when flat)
    pending_regime_flip: bool = False          # True = close failed last tick, retry next tick
    was_running: bool = False                  # True if bot was running when server last restarted
    user_stopped: bool = False                 # F445: set by an explicit Stop; bot_watch skips these

    # U9: graph-mode runtime cache (NOT persisted to bots.json)
    graph_hash: Optional[str] = None          # SHA-256 of last compiled graph; triggers recompile on change
    compiled_program: Optional[Any] = None    # CompiledProgram — Any to avoid circular import; rebuilt on first tick
    # F435 W5 LM-3: True from an entry order until its fill sets entry_price,
    # so graph_update refuses while a position is being opened.  Runtime
    # only (NOT persisted): after a restart no order is in flight.
    entry_in_flight: bool = False

    def append_slippage_bps(self, bps: float) -> None:
        """Append a slippage sample and cap the list at 1000 to prevent unbounded growth."""
        # single-coroutine-per-bot: do not insert await between append and slice-cap.
        self.slippage_bps.append(round(bps, 2))
        if len(self.slippage_bps) > 1000:
            self.slippage_bps = self.slippage_bps[-1000:]

    def append_equity_snapshot(self, value: float) -> None:
        """Append a {time, value} snapshot and cap at 500 entries."""
        # single-coroutine-per-bot: do not insert await between append and slice-cap.
        self.equity_snapshots.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "value": round(value, 2),
        })
        if len(self.equity_snapshots) > 500:
            self.equity_snapshots = self.equity_snapshots[-500:]

    def append_activity_log(self, entry: dict) -> None:
        """Insert at head (newest-first) and cap the list at 200 entries."""
        # single-coroutine-per-bot: do not insert await between insert and pop.
        self.activity_log.insert(0, entry)
        if len(self.activity_log) > 200:
            self.activity_log.pop()

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
            "entry_time": self.entry_time,
            "trail_peak": self.trail_peak,
            "trail_stop_price": self.trail_stop_price,
            "entry_stop_pct": self.entry_stop_pct,
            "pending_close_order_id": self.pending_close_order_id,
            "pending_close_reason": self.pending_close_reason,
            "consec_sl_count": self.consec_sl_count,
            "skip_remaining": self.skip_remaining,
            "scans_count": self.scans_count,
            "trades_count": self.trades_count,
            "slippage_bps": self.slippage_bps,
            "equity_snapshots": self.equity_snapshots,
            "backtest_summary": self.backtest_summary,
            "activity_log": self.activity_log,
            "error_message": self.error_message,
            "pause_reason": self.pause_reason,
            "regime_direction": self.regime_direction,
            "position_direction": self.position_direction,
            "pending_regime_flip": self.pending_regime_flip,
            "was_running": self.was_running,
            "user_stopped": self.user_stopped,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BotState":
        s = cls()
        # Lazy migration: legacy bots.json has slippage_pcts (signed %); scale to bps (unsigned).
        # max(0, ...) retroactively applies the new "cost >= 0" rule to stored favorable values.
        if "slippage_pcts" in d and "slippage_bps" not in d:
            d = {**d, "slippage_bps": [max(0.0, v) * 100 for v in d["slippage_pcts"] or []]}
            d.pop("slippage_pcts", None)
        # Lazy migration: legacy bots.json stored the full backtest equity_curve
        # under backtest_result — huge on disk. Keep summary only under its new name.
        if "backtest_result" in d and "backtest_summary" not in d:
            br = d.pop("backtest_result") or {}
            d["backtest_summary"] = br.get("summary") if isinstance(br, dict) else None
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
        # See save().  An RLock: an add holds it across insert, save and
        # rollback (_insert_and_save), and save() takes it again inside.
        self._save_lock = threading.RLock()
        # Held while an add checks the fund and inserts, so two adds (say two
        # spawns) cannot both pass the fund or symbol check.  Routes run in
        # the thread pool, so adds can overlap.
        self._add_lock = threading.RLock()
        # bots.json rows load() could not read: (bot_id of the loaded bot
        # they followed, or None, raw row).  save() writes them back as is.
        self._unloaded: list[tuple[Optional[str], dict]] = []
        # bot_id -> (config, graph) prepare_start checked in the thread pool,
        # so start_bot does not compile that same config again on the loop.
        self._start_checked: dict[str, tuple] = {}

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

    @staticmethod
    def _check_graph(config: BotConfig, program=None):
        """Compile a graph bot's graph and return the program.

        Raises a GraphValidationError (with node_id) for a bad graph, so the
        route can answer 400 instead of the bot failing on its first tick.
        Checks the bot's own Output Group the way a tick does
        (bot_runner.graph_bot_live: the group exists, trades the bot's symbol
        and direction, has no rule regime), and runs the group's settings
        overlay, so a value the bot config refuses fails here and not on
        every tick.  *program* skips the compile when the caller has one.
        Returns None for a rule bot.
        """
        if config.kind != "graph":
            return None
        if config.graph is None:
            raise GraphValidationError("A graph bot needs a graph.", node_id=None)
        if program is None:
            program = compile_bot_graph(config.graph, config.bot_id)
        live = graph_bot_live(program, config)
        try:
            apply_to_bot_config(config, live.plan)
        except (ValidationError, TypeError, ValueError) as exc:
            raise GraphValidationError(
                f"The graph's Settings nodes give a bot config value that is not "
                f"allowed: {exc}",
                node_id=None,
            ) from exc
        return program

    def add_bot(self, config: BotConfig) -> str:
        self._check_graph(config)
        with self._add_lock:
            if self.bot_fund == 0:
                raise ValueError("Bot fund is not set. Set a bot fund before adding bots.")
            self._validate_allocation(config.allocated_capital)
            bot_id = str(uuid.uuid4())
            config.bot_id = bot_id
            self._insert_and_save({bot_id: (config, BotState())})
        return bot_id

    def add_bots(self, configs: list[BotConfig], *, programs: Optional[list] = None) -> list[str]:
        """Add several bots at once, all stopped: all of them or none.

        Every config is checked first (graph, group, settings overlay), then,
        under one lock, the exclusive-symbol guard (against each other and
        every existing bot, running or not), the fund (their capital
        together) and the insert, then one save.  Raises ValueError (fund),
        SymbolConflictError, or GraphValidationError, and adds nothing.
        *programs* (one compiled program per config, or None) skips the
        compiles.  Used by POST /api/graphs/{id}/spawn (plan D7).
        """
        if not configs:
            raise ValueError("No bots to add.")
        for n, config in enumerate(configs):
            self._check_graph(config, programs[n] if programs else None)
        with self._add_lock:
            for n, a in enumerate(configs):
                for b in configs[n + 1:]:
                    if symbols_conflict(a, b):
                        raise SymbolConflictError(
                            f"{a.graph_group or a.strategy_name} and "
                            f"{b.graph_group or b.strategy_name} both trade "
                            f"{normalize_symbol(a.symbol)} ({_side_label(a)} and "
                            f"{_side_label(b)}).  One bot per symbol and direction.",
                            names=tuple(x for x in (a.graph_group, b.graph_group) if x))
                for bid, (other, _state) in self.bots.items():
                    if symbols_conflict(a, other):
                        raise SymbolConflictError(
                            f"{a.graph_group or a.strategy_name} trades "
                            f"{normalize_symbol(a.symbol)} {_side_label(a)}, and bot "
                            f"{other.strategy_name!r} ({bid}) already trades "
                            f"{normalize_symbol(other.symbol)} {_side_label(other)}.  "
                            f"One bot per symbol and direction.",
                            names=tuple(x for x in (a.graph_group,) if x))
            if self.bot_fund == 0:
                raise ValueError("Bot fund is not set. Set a bot fund before adding bots.")
            self._validate_allocation(sum(c.allocated_capital for c in configs))
            new: dict[str, tuple[BotConfig, BotState]] = {}
            for config in configs:
                config.bot_id = str(uuid.uuid4())
                new[config.bot_id] = (config, BotState())
            # One write with every leg, then the legs go live in memory: a
            # failed write adds none, on disk or in memory (LM-11).
            self._insert_and_save(new)
        return list(new)

    def start_bot(self, bot_id: str):
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        config, state = self.bots[bot_id]
        # What prepare_start checked in the thread pool, if it ran (LM-10).
        checked = self._start_checked.pop(bot_id, None)
        if bot_id in self.tasks and not self.tasks[bot_id].done():
            raise ValueError(f"Bot {bot_id} is already running")
        # Guard: a bot that trades both sides (a rule regime bot or a
        # regime_switch graph bot) needs its symbol to itself; other bots
        # block the same direction.
        for bid, task in self.tasks.items():
            if bid != bot_id and not task.done():
                other_cfg, _ = self.bots[bid]
                if other_cfg.symbol == config.symbol:
                    if config.is_bidirectional:
                        raise ValueError(
                            f"Regime bot on {config.symbol} requires exclusive symbol access "
                            f"(bot {bid} is already running on {config.symbol})"
                        )
                    if other_cfg.is_bidirectional:
                        raise ValueError(
                            f"Bot {bid} trades both sides of {config.symbol} — "
                            f"regime bots require exclusive symbol access"
                        )
                    if other_cfg.direction == config.direction:
                        raise ValueError(
                            f"Bot {bid} is already running {config.direction} on {config.symbol}"
                        )
        # A stored graph that no longer compiles (a node type since refused, a
        # bad setting) would fail on every tick, before any exit check, and
        # leave an open position unmanaged while the bot looks "running".
        # Refuse to start instead, and say why on the card and in an alert.
        # The start routes compile in the thread pool first (prepare_start);
        # this config is then not compiled again here, on the event loop.
        if checked is None or checked[0] is not config or checked[1] is not config.graph:
            try:
                self._check_graph(config)
            except GraphValidationError as exc:
                self.refuse_start(bot_id, exc)
                raise
        runner = BotRunner(config, state, self)
        task = asyncio.create_task(runner.run())
        self.tasks[bot_id] = task

    def prepare_start(self, bot_id: str) -> None:
        """The slow part of starting a bot, for the thread pool (F435 W5
        LM-10): compile the graph and check the bot's group and settings
        (_check_graph), then fetch each reference frame once on the bot's
        data source (probe_references, LM-1).  start_bot, called next on the
        event loop, then skips its own compile of this config, so start and
        start-all never compile on the loop that runs every bot's ticks.

        The probe runs only for a bot with no tracked position: a bot that
        holds one must start even while a reference is down, because its
        ticks still run the price exits (LM-1).

        Raises KeyError, ValueError (already running), GraphValidationError
        (the caller then calls refuse_start on the event loop) or
        ReferenceUnavailableError.  Thread pool only."""
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        config, state = self.bots[bot_id]
        task = self.tasks.get(bot_id)
        if task is not None and not task.done():
            raise ValueError(f"Bot {bot_id} is already running")
        program = self._check_graph(config)
        if program is not None and state.entry_price is None:
            probe_references(config, program)
        self._start_checked[bot_id] = (config, config.graph)

    def refuse_start(self, bot_id: str, exc: GraphValidationError) -> None:
        """A start refused because the bot's graph no longer compiles: say
        why on the card and alert (create_task, never await).  Event loop
        only."""
        config, state = self.bots[bot_id]
        state.status = "error"
        state.pause_reason = f"Graph does not compile: {exc}"
        state.error_message = state.pause_reason
        self.save()
        from notifications import notify_error
        asyncio.create_task(notify_error(
            symbol=config.symbol,
            error_msg=state.pause_reason,
            bot_id=bot_id,
        ))

    def stop_bot(self, bot_id: str, close_position: bool = False):
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        if bot_id in self.tasks:
            self.tasks[bot_id].cancel()
            del self.tasks[bot_id]
        config, state = self.bots[bot_id]
        state.status = "stopped"
        state.user_stopped = True
        state.entry_price = None
        state.trail_peak = None
        state.trail_stop_price = None
        state.position_direction = None
        state.pending_regime_flip = False

        if close_position:
            # The trade's wired stop goes with the position (LM-6).  A Stop
            # that keeps the position keeps it too, so Start resumes the
            # position with the stop it was entered with.
            state.entry_stop_pct = None
            try:
                provider = get_trading_provider(config.broker)
                provider.close_position(config.symbol)
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

        if config.kind == "graph":
            self._backtest_graph_bot(config, state, start, end)
            return

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
            max_bars_held=config.max_bars_held,
            trailing_stop=config.trailing_stop,
            dynamic_sizing=config.dynamic_sizing,
            skip_after_stop=config.skip_after_stop,
            trading_hours=config.trading_hours,
            slippage_bps=config.slippage_bps,
            source=config.data_source,
            direction=config.direction,
            regime=config.regime,
            long_buy_rules=config.long_buy_rules,
            long_sell_rules=config.long_sell_rules,
            long_buy_logic=config.long_buy_logic,
            long_sell_logic=config.long_sell_logic,
            short_buy_rules=config.short_buy_rules,
            short_sell_rules=config.short_sell_rules,
            short_buy_logic=config.short_buy_logic,
            short_sell_logic=config.short_sell_logic,
            long_stop_loss_pct=getattr(config, 'long_stop_loss_pct', None),
            short_stop_loss_pct=getattr(config, 'short_stop_loss_pct', None),
            long_trailing_stop=getattr(config, 'long_trailing_stop', None),
            short_trailing_stop=getattr(config, 'short_trailing_stop', None),
            long_max_bars_held=getattr(config, 'long_max_bars_held', None),
            short_max_bars_held=getattr(config, 'short_max_bars_held', None),
            long_position_size=getattr(config, 'long_position_size', None),
            short_position_size=getattr(config, 'short_position_size', None),
        )

        try:
            result = run_backtest(req)
            state.backtest_summary = result.get("summary", {})
        except Exception as e:
            state.backtest_summary = {"error": str(e)}
        finally:
            state.status = "stopped"
            self.save()

    def _backtest_graph_bot(self, config: BotConfig, state: BotState, start: str, end: str):
        """Backtest a graph bot with the graph backtest, not the empty rule lists.

        The request carries the bot config's plain fields.  The graph's
        terminals and Settings nodes then win, the same overlay a live tick
        applies (sim_bridge.apply_to_bot_config).  Per-direction fields are
        not sent: the graph backtest never reads them, and a live graph bot
        clears them.  A graph with several Output Groups backtests only the
        bot's own group, on the bot's own capital.
        """
        try:
            if config.graph is None:
                raise GraphValidationError("A graph bot needs a graph.", node_id=None)
            req = GraphBacktestRequest(
                graph=config.graph,
                ticker=config.symbol,
                start=start,
                end=end,
                interval=config.interval,
                source=config.data_source,
                initial_capital=config.allocated_capital,
                position_size=config.position_size,
                stop_loss_pct=config.stop_loss_pct,
                trailing_stop=config.trailing_stop,
                max_bars_held=config.max_bars_held,
                slippage_bps=config.slippage_bps,
                borrow_rate_annual=config.borrow_rate_annual,
                dynamic_sizing=config.dynamic_sizing,
                skip_after_stop=config.skip_after_stop,
                trading_hours=config.trading_hours,
                direction=config.direction,
            )
            program = self._check_graph(config)
            live = graph_bot_live(program, config)
            if live.group.implicit:
                result = run_graph_backtest(req)
                state.backtest_summary = dict(result.summary)
            else:
                state.backtest_summary = self._backtest_own_group(config, live, req)
        except Exception as e:
            state.backtest_summary = {"error": str(e)}
        finally:
            state.status = "stopped"
            self.save()

    @staticmethod
    def _backtest_own_group(config: BotConfig, live, req) -> dict:
        """The bot's Output Group backtested the way the bot trades it: the
        group's own program and plan, on the bot's symbol, interval (which
        the spawn dialog can override) and capital (sim_bridge.run_group, the
        bridge run.py simulates each group with)."""
        from nodebuilder.run import _open_position
        from nodebuilder.trading import sim_bridge
        from shared import _fetch, require_valid_source

        source = require_valid_source(config.data_source)
        df = _fetch(config.symbol, req.start, req.end, config.interval, source=source)
        if df is None or len(df) == 0:
            raise ValueError(f"No data for {config.symbol} in {req.start}..{req.end} ({config.interval}).")
        run = sim_bridge.run_group(live.program, df, req, plan=live.plan,
                                   ticker=config.symbol, interval=config.interval)
        summary = dict(run.sim["summary"])
        summary["open_position"] = _open_position(run.sim["trades"], float(df["Close"].iloc[-1]))
        summary["exit_connected"] = bool(live.group.exit_connected)
        return summary

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
        # A bot made from a saved graph keeps graph and graph_rev together,
        # and its direction comes from its group: a new revision goes
        # through graph_update, never this edit.
        if config.graph_id and {"graph", "direction"} & updates.keys():
            raise ValueError(
                f"This bot runs saved graph {config.graph_id}; its graph and direction "
                f"change only through POST /api/bots/{bot_id}/graph_update.")
        # Apply updates to config
        config_dict = config.model_dump()
        config_dict.update(updates)
        new_config = BotConfig(**config_dict)
        if {"graph", "regime", "kind", "direction"} & updates.keys():
            # Refuse a bad graph (or a regime on a graph bot, or a direction
            # its group does not trade) before it replaces the working config.
            self._check_graph(new_config)
        self.bots[bot_id] = (new_config, state)
        self.save()

    def update_graph(self, bot_id: str, graph: Graph, rev: int, program=None) -> BotConfig:
        """Move a graph bot to revision *rev* of its saved graph (plan D7).

        Checks the new graph first (_check_graph: compile, group, symbol,
        direction, settings), then apply_graph_update.  Refuses with
        InPositionError while the bot holds a position.  The route
        (POST /api/bots/{id}/graph_update) runs the check in the thread pool
        and only apply_graph_update on the event loop.
        """
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        config, state = self.bots[bot_id]
        if state.entry_price is not None or state.entry_in_flight:
            raise InPositionError("The bot is in a position; close it before updating its graph.")
        candidate = BotConfig.model_validate(
            {**config.model_dump(exclude={"graph"}), "graph": graph, "graph_rev": rev})
        self._check_graph(candidate, program)
        return self.apply_graph_update(bot_id, candidate.graph, rev)

    def apply_graph_update(self, bot_id: str, graph: Graph, rev: int) -> BotConfig:
        """Set a checked graph and its rev on the bot, together and in place,
        so a running bot picks the new graph up on its next tick (its runner
        holds this config object; it recompiles when the graph hash
        changes).  Refuses with InPositionError while the bot holds a
        position or an entry order is in flight (entry_in_flight: ordered,
        not yet filled, LM-3).  No I/O: the caller saves.  Call from the
        event loop with no await between the caller's own checks and this
        call."""
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        config, state = self.bots[bot_id]
        if state.entry_price is not None or state.entry_in_flight:
            raise InPositionError("The bot is in a position; close it before updating its graph.")
        # Under _save_lock (DI-06): a save in a worker thread (runners save
        # through asyncio.to_thread) dumps graph and graph_rev in two steps,
        # and the GIL can switch between them, so without the lock it could
        # write the old graph with the new rev.  With it a save sees the old
        # pair or the new pair.  The lock is held only for the length of a
        # save, which is brief.
        with self._save_lock:
            config.__dict__.update(graph=graph, graph_rev=rev)
            config.__pydantic_fields_set__.update(("graph", "graph_rev"))
        return config

    def manual_buy(self, bot_id: str) -> dict:
        """Place a manual buy for a bot using its allocation config."""
        if bot_id not in self.bots:
            raise KeyError(f"Bot {bot_id} not found")
        config, state = self.bots[bot_id]

        if state.entry_price is not None:
            raise ValueError("Bot already has an open position")
        if state.status != "running":
            raise ValueError("Bot must be running to place a manual buy")

        # A graph bot sizes from its group's Size terminal or Position Size
        # node, as its ticks do.  A wired Size or Stop is read from the cook
        # at each entry, which a manual buy does not run, so it is refused.
        if config.kind == "graph" and config.graph is not None:
            program = self._check_graph(config)
            live = graph_bot_live(program, config)
            if live.plan.size is not None or live.plan.stop is not None:
                raise ValueError(
                    "This graph sets its size or stop from a wired value on each bar; "
                    "a manual buy cannot read it.  Let the bot enter on its signal.")
            config = apply_to_bot_config(config, live.plan)

        provider = get_trading_provider(config.broker)

        # Get current price
        from datetime import date, timedelta
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=5)).isoformat()
        df = _fetch(config.symbol, start_date, end_date, config.interval, config.data_source)
        price = float(df["Close"].iloc[-1])

        # Calculate qty (a bot that trades both sides sizes from both sides' P&L)
        if config.is_bidirectional:
            current_capital = config.allocated_capital + compute_bidirectional_pnl(config.symbol, config.bot_id, since=config.pnl_epoch)
        else:
            current_capital = config.allocated_capital + compute_realized_pnl(config.symbol, config.direction, bot_id=config.bot_id, since=config.pnl_epoch)
        effective_size = max(current_capital, 0) * config.position_size
        qty = math.floor(effective_size / price)
        if qty < 1:
            raise ValueError(f"Position too small: ${effective_size:.2f} / ${price:.2f}")

        # Submit order
        is_short = config.direction == "short"
        result = provider.submit_order(BrokerOrderRequest(
            symbol=config.symbol,
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
        state.append_slippage_bps(cost_bps)
        state.entry_price = fill_price
        state.entry_time = datetime.now(timezone.utc).isoformat()
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
        except Exception as e:
            runner._log("ERROR", f"Journal write failed: {e}")

        self.save()
        return {"qty": qty, "fill_price": fill_price, "slippage_bps": round(cost_bps, 2)}

    def list_bots(self) -> list[dict]:
        from nodebuilder.storage import graph_head

        all_trades = _load_trades()
        result = []
        for bot_id, (config, state) in self.bots.items():
            epoch = config.pnl_epoch
            if config.is_bidirectional:
                total_pnl = round(compute_bidirectional_pnl(config.symbol, bot_id, since=epoch, trades=all_trades), 2)
                first_trade_time = first_bot_bidirectional_entry_time(config.symbol, bot_id, since=epoch, trades=all_trades)
            else:
                total_pnl = round(compute_realized_pnl(config.symbol, config.direction, bot_id=bot_id, since=epoch, trades=all_trades), 2)
                first_trade_time = first_bot_entry_time(config.symbol, config.direction, bot_id=bot_id, since=epoch, trades=all_trades)
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
                "total_pnl": total_pnl,
                "backtest_summary": state.backtest_summary,
                "data_source": config.data_source,
                "direction": config.direction,
                "broker": config.broker,
                "avg_cost_bps": compute_bot_avg_cost_bps(config.symbol, bot_id=bot_id, since=epoch, trades=all_trades)[0],
                "max_spread_bps": config.max_spread_bps,
                "drawdown_threshold_pct": config.drawdown_threshold_pct,
                "has_position": state.entry_price is not None,
                "first_trade_time": first_trade_time,
                "pnl_epoch": epoch,
                "last_tick": state.last_tick,
                "pause_reason": state.pause_reason,
                "equity_snapshots": state.equity_snapshots,
                "regime_direction": state.regime_direction,
                "position_direction": state.position_direction,
                "pending_regime_flip": state.pending_regime_flip,
                "was_running": state.was_running,
                "kind": config.kind,
                **self._graph_summary(config, graph_head),
            })
        return result

    @staticmethod
    def _graph_summary(config: BotConfig, graph_head) -> dict:
        """The graph fields of a bot summary (plan W5 5.D).  graph_name and
        graph_latest_rev come from the graph store's in-memory head index
        (graph_head), never from a graph file; both are None when the bot has
        no graph_id or its graph was deleted."""
        head = graph_head(config.graph_id) if config.graph_id else None
        return {
            "graph_id": config.graph_id,
            "graph_rev": config.graph_rev,
            "graph_group": config.graph_group,
            "graph_direction_mode": config.graph_direction_mode,
            "graph_name": head[1] if head is not None else None,
            "graph_latest_rev": head[0] if head is not None else None,
        }

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

    def reorder(self, ids: list[str]):
        """Reorder bots to match the given ID list.

        IDs not in the list are appended at the end (handles races where a new
        bot was added between the frontend read and the reorder call).  IDs in
        the list that don't exist are silently ignored.
        """
        new_bots: dict[str, tuple[BotConfig, BotState]] = {}
        for bid in ids:
            if bid in self.bots:
                new_bots[bid] = self.bots[bid]
        # Append any bots not mentioned (newly added)
        for bid in self.bots:
            if bid not in new_bots:
                new_bots[bid] = self.bots[bid]
        self.bots = new_bots
        self.save()

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
        # F444: runners call save via asyncio.to_thread, so saves overlap (11 at
        # once on boot). Without the lock an older snapshot could land last and
        # leave running bots saved as stopped. Snapshot + write under one lock.
        with self._save_lock:
            data = {
                "bot_fund": self.bot_fund,
                "bots": self._rows_to_save(),
            }
            # DI-06: explicit depth=1 — bots.json is high-value config; one backup
            # is worth the per-save shutil.copy2 at current file sizes.
            atomic_write_text(DATA_PATH, json.dumps(data, indent=2, default=str), backup_depth=1)

    def _insert_and_save(self, new: dict) -> None:
        """Add the bots in *new* (bot_id -> (config, state)) and save, all or
        nothing (F435 W5 LM-11).

        The insert, the save and a rollback all happen under _save_lock (an
        RLock, so save() takes it again on this thread).  A runner's
        concurrent save (asyncio.to_thread) either ran before the insert or
        waits until the bots are saved or rolled back, so bots.json never
        holds some of the new bots, and a failed save leaves none of them,
        in memory or on disk."""
        with self._save_lock:
            self.bots.update(new)
            try:
                self.save()
            except Exception:
                for bot_id in new:
                    self.bots.pop(bot_id, None)
                raise

    def _rows_to_save(self) -> list[dict]:
        """The bots.json rows: every loaded bot, plus every row load() could
        not read, written back unchanged in its old place (after the loaded
        bot it followed; at the end when that bot has since been deleted)."""
        pending: dict[Optional[str], list[dict]] = {}
        for anchor, raw in self._unloaded:
            pending.setdefault(anchor, []).append(raw)
        rows: list[dict] = list(pending.pop(None, []))
        for bot_id, (config, state) in list(self.bots.items()):
            rows.append({"config": config.model_dump(), "state": state.to_dict()})
            rows.extend(pending.pop(bot_id, []))
        for leftover in pending.values():
            rows.extend(leftover)
        return rows

    def load(self):
        """Load bots.json.  Every bot starts stopped.

        A row that does not load (a graph that fails migration or
        validation, a bad symbol, any other error) is logged at ERROR and
        kept: its raw row is written back unchanged by every save(), in its
        old place, so a bad row never leaves bots.json.  It is not in
        self.bots, so it never starts.
        """
        if not os.path.exists(DATA_PATH):
            return
        try:
            with open(DATA_PATH) as f:
                raw_text = f.read()
            data = json.loads(raw_text)
            # F435 W2 (MD-01): the save below rewrites every graph as v3,
            # which Wave 1 code refuses.  Keep the file as it was, once, so
            # a rollback has a copy it can read.
            self._write_pre_w2_copy(data, raw_text)
            # F435 W5 (DI-08): likewise before W5 adds its graph_* fields.
            self._write_pre_w5_copy(data, raw_text)
            self.bot_fund = data.get("bot_fund", 0.0)
            self._unloaded = []
            previous: Optional[str] = None  # the last bot that loaded
            for entry in data.get("bots", []):
                raw_entry = copy.deepcopy(entry)
                try:
                    config, state = self._load_entry(entry)
                except Exception as e:
                    cfg = entry.get("config") if isinstance(entry, dict) else None
                    bot_id = cfg.get("bot_id", "unknown") if isinstance(cfg, dict) else "unknown"
                    expected = isinstance(e, (ValueError, GraphValidationError))  # incl. ValidationError
                    logger.error(
                        "skipped bot %r: invalid config (%s: %s); row kept in bots.json "
                        "unchanged, bot not started",
                        bot_id, type(e).__name__, e,
                        exc_info=not expected,
                    )
                    self._unloaded.append((previous, raw_entry))
                    continue
                self.bots[config.bot_id] = (config, state)
                previous = config.bot_id
            if self.bots:
                self.save()
            self._alert_unloaded()
        except Exception:
            logger.exception("Failed to load bots.json")

    @staticmethod
    def _write_pre_w2_copy(data: Any, raw_text: str) -> None:
        """Copy bots.json to bots.json.pre-w2 when it holds a graph saved
        before graph version 3 (F435 W2, MD-01 / LT-3).

        Written once: an existing copy is never overwritten, so it stays the
        last file Wave 1 code wrote.  To roll back to Wave 1, stop the server
        and put this copy back as bots.json (state written since is lost).
        """
        from nodebuilder.migrate import CURRENT_GRAPH_VERSION

        path = DATA_PATH + ".pre-w2"
        if os.path.exists(path):
            return
        rows = data.get("bots", []) if isinstance(data, dict) else []
        old = False
        for row in rows if isinstance(rows, list) else []:
            cfg = row.get("config") if isinstance(row, dict) else None
            graph = cfg.get("graph") if isinstance(cfg, dict) else None
            if not isinstance(graph, dict):
                continue
            version = graph.get("_version", graph.get("version", 1))
            if not isinstance(version, int) or isinstance(version, bool) or version < CURRENT_GRAPH_VERSION:
                old = True
                break
        if not old:
            return
        try:
            atomic_write_text(path, raw_text, backup_depth=0)
            logger.info("Wrote %s before rewriting graphs as version %d", path, CURRENT_GRAPH_VERSION)
        except Exception:
            logger.exception("Could not write %s", path)

    # The fields W5 adds to every bots.json row (plan D7).  A row without
    # them was written by code from before Wave 5.
    W5_CONFIG_FIELDS = ("graph_id", "graph_rev", "graph_group", "graph_direction_mode")

    @classmethod
    def _write_pre_w5_copy(cls, data: Any, raw_text: str) -> None:
        """Copy bots.json to bots.json.pre-w5 when it was written by code
        from before Wave 5 (F435 W5, DI-08): a row whose config lacks the W5
        graph_* fields.

        Written once, like the pre-w2 copy: an existing copy is never
        overwritten, so it stays the last file Wave 4 code wrote.  Wave 4
        code reads a W5 file but drops the graph_* fields on its first save,
        so after rolling forward again a regime_switch bot would be refused
        on every tick and every spawned bot would lose its graph link.  To
        roll back to Wave 4: stop the server, put this copy back as
        bots.json, then start the Wave 4 build (bots and state written since
        are lost, including bots spawned from saved graphs)."""
        path = DATA_PATH + ".pre-w5"
        if os.path.exists(path):
            return
        rows = data.get("bots", []) if isinstance(data, dict) else []
        old = False
        for row in rows if isinstance(rows, list) else []:
            cfg = row.get("config") if isinstance(row, dict) else None
            if isinstance(cfg, dict) and any(k not in cfg for k in cls.W5_CONFIG_FIELDS):
                old = True
                break
        if not old:
            return
        try:
            atomic_write_text(path, raw_text, backup_depth=0)
            logger.info("Wrote %s before rewriting bots.json with the Wave 5 fields", path)
        except Exception:
            logger.exception("Could not write %s", path)

    def _alert_unloaded(self) -> None:
        """One alert naming every bots.json row load() could not read
        (F435 W2, LT-3).  Such a bot is not listed, not resumed and not
        watched, so without this a refused row (a rollback, a bad graph) is
        silent.  Fire-and-forget: create_task when a loop runs, else log."""
        if not self._unloaded:
            return
        ids = []
        for _anchor, raw in self._unloaded:
            cfg = raw.get("config") if isinstance(raw, dict) else None
            ids.append(str(cfg.get("bot_id", "unknown")) if isinstance(cfg, dict) else "unknown")
        msg = (f"{len(ids)} bot(s) in bots.json did not load and are not running: "
               f"{', '.join(ids)}.  Their rows are kept unchanged; see the server log.")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.error("%s (no event loop, no alert sent)", msg)
            return
        from notifications import notify_error
        asyncio.create_task(notify_error(symbol="bots.json", error_msg=msg, bot_id=",".join(ids)))

    @staticmethod
    def _load_entry(entry: dict) -> tuple["BotConfig", "BotState"]:
        """One bots.json row -> (config, state).  Raises on a row that does not load."""
        cfg_dict = entry["config"]
        # Lazy migration: old key 'slippage_pct' (percent) → 'slippage_bps' (bps).
        # max(0, ...) retroactively applies the "cost >= 0" rule.
        if "slippage_pct" in cfg_dict and "slippage_bps" not in cfg_dict:
            cfg_dict = {**cfg_dict, "slippage_bps": max(0.0, cfg_dict["slippage_pct"]) * 100}
            cfg_dict.pop("slippage_pct", None)
        raw_symbol = cfg_dict.get("symbol")
        if raw_symbol is not None:
            cfg_dict["symbol"] = normalize_symbol(raw_symbol)
        for key in ("buy_rules", "sell_rules"):
            if key in cfg_dict and cfg_dict[key]:
                cfg_dict[key] = [migrate_rule(Rule(**r)).model_dump() for r in cfg_dict[key]]
        config = BotConfig(**cfg_dict)
        state = BotState.from_dict(entry.get("state", {}))
        state.was_running = state.status == "running"
        # F445: a row saved before user_stopped existed and not running was
        # most likely stopped by hand; keep bot_watch from alerting on it.
        if "user_stopped" not in entry.get("state", {}) and not state.was_running:
            state.user_stopped = True
        state.status = "stopped"  # always start stopped after server restart
        return config, state

    def resume_was_running(self) -> dict[str, list]:
        """F430: auto-resume bots that were running when the server last went away.

        `load()` sets `state.was_running` for bots whose persisted status was
        "running" and forces them to "stopped". An explicit Stop persists
        "stopped", so was_running is only True after a crash/restart/deploy —
        never after the user turned a bot off. Bots carrying a pause_reason or
        error_message are left alone (they stopped for a reason a restart does
        not clear). Opt out with BOTS_AUTORESUME=0.
        """
        result: dict[str, list] = {"resumed": [], "skipped": [], "failed": []}
        if os.environ.get("BOTS_AUTORESUME", "1") == "0":
            return result
        for bot_id, (config, state) in list(self.bots.items()):
            if not state.was_running:
                continue
            if state.pause_reason or state.error_message:
                result["skipped"].append(bot_id)
                state.was_running = False
                continue
            try:
                self.start_bot(bot_id)
                result["resumed"].append(bot_id)
                logger.info("F430 auto-resumed bot %s (%s %s) after restart",
                            bot_id, config.symbol, config.interval)
            except Exception as e:
                result["failed"].append({"bot_id": bot_id, "error": str(e)})
                state.was_running = False
                logger.warning("F430 auto-resume failed for bot %s: %s", bot_id, e)
        if result["resumed"] or result["skipped"] or result["failed"]:
            self.save()
        return result

    async def shutdown(self):
        # F430: a cancelled runner's `finally` sets status="stopped" and saves. That
        # late save overwrote "running", so the next boot resumed nothing (2026-09-26
        # office move). Record live statuses, let the runners finish, write them back.
        live = {bid: self.bots[bid][1].status
                for bid, task in self.tasks.items() if not task.done() and bid in self.bots}
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=10)
        for bid, status in live.items():
            self.bots[bid][1].status = status
        self.save()

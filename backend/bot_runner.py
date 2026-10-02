"""
bot_runner.py — Async polling loop for a single live trading bot.

Classes:
  BotRunner — Fetches bars, evaluates signals, and places/manages orders.
"""

from __future__ import annotations

import asyncio
import math
import os as _os
from dataclasses import dataclass, replace as _replace
from datetime import datetime, timezone
from typing import Any, Optional

from slippage import slippage_cost_bps, fill_bias_bps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot_manager import BotConfig, BotState, BotManager

from signal_engine import compute_indicators, eval_rules, migrate_rule
from shared import _fetch, fetch_ohlcv_async, provider_max_days
from broker import get_trading_provider, OrderRequest as BrokerOrderRequest, OrderResult
from journal import _log_trade, compute_realized_pnl, compute_bidirectional_pnl
from post_loss import is_post_loss_trigger
from notifications import notify_entry, notify_exit, notify_error
from pydantic import ValidationError
from nodebuilder.models import GraphValidationError
from nodebuilder.prepare import build_graph_attrs, live_fetch_start, window_cut_by_provider
from regime import RegimeMixin
from exits import ExitsMixin

# Poll interval per bar cadence (seconds)
POLL_INTERVALS = {"1m": 10, "5m": 15, "15m": 20, "30m": 30, "1h": 60}

# Global override poll interval (milliseconds). 0 = use per-interval defaults.
_POLL_MS: int = int(_os.environ.get("BOT_POLL_MS", "0"))


def get_poll_ms() -> int:
    return _POLL_MS


def set_poll_ms(ms: int):
    global _POLL_MS
    _POLL_MS = ms


def compile_bot_graph(graph, bot_id: str = ""):
    """Compile a graph bot's graph into a CompiledProgram.

    Used by the runner on each graph change and by BotManager when a graph
    bot is added or its graph is replaced, so a bad graph is refused up front.
    Raises a GraphValidationError subclass carrying node_id: whatever
    compile() raises (MissingTerminalError, a group error, ...).
    Since W1 that includes FamilyCapExceededError (too many distinct specs of
    one indicator family, which used to fail only at Run) and indicator params
    outside their catalog limits, so such a graph is refused at deploy.
    Other timeframes are reference Tickers since W5 (plan D8): the tick
    fetches their bars next to its own (GraphLive.references), so the old
    HTF refusal is gone.
    """
    from nodebuilder.compile import compile as nb_compile
    return nb_compile(graph)


def graph_hash(graph) -> str:
    """A stable hash of a graph, so the runner recompiles only on a change."""
    import hashlib
    import json as _json
    dump = _json.dumps(graph.model_dump(mode='json'), sort_keys=True)
    return hashlib.sha256(dump.encode()).hexdigest()


class GraphCookError(RuntimeError):
    """A graph bot's cook raised on a tick.  The tick restores the bar and
    re-raises this, so run() counts it toward MAX_CONSEC_ERRORS."""


class ReferenceUnavailableError(RuntimeError):
    """A reference frame the bot's group reads failed to load, came back
    empty, or is behind the bot's own bars (F435 W5 LM-1, LM-4).

    The tick still runs the price exits of an open position (stop, trailing
    stop, time stop) on the bot's own bars, leaves the bar not done so the
    signal work is retried on the next tick, and raises this, so run()
    counts it toward MAX_CONSEC_ERRORS and alerts (create_task)."""


# ---------------------------------------------------------------------------
# Graph bots: the bot's own Output Group (F435 W5, plan D7 and D8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphLive:
    """What one tick of a graph bot needs from its compiled graph.

    group      : the bot's Output Group (nodes_groups.GroupProgram).
    plan       : that group's simulator plan (sim_bridge.GroupPlan), the same
                 plan the graph backtest runs the group with.
    program    : the compiled program cut down to the nodes the group reads,
                 so the bot never cooks another group's nodes.
    references : (SYMBOL, interval) of every other Ticker the group reads
                 (reference tickers, plan D8).  The tick fetches them next to
                 its own bars.
    reference_needs : ((SYMBOL, interval), bars) pairs: the bars of history
                 each reference frame needs (align.reference_needs), for the
                 short-history and provider-limit warnings (LM-9).
    """
    group: Any
    plan: Any
    program: Any
    references: tuple = ()
    reference_needs: tuple = ()


def _group_error(message: str, code: str, node_id: Optional[str] = None) -> GraphValidationError:
    from nodebuilder.kernel.schema import coded
    return coded(GraphValidationError(message, node_id=node_id), code)


def graph_bot_live(program, cfg) -> GraphLive:
    """The bot's own group of a compiled graph, checked against the bot.

    The group is cfg.graph_group (None: the graph's only group).  Raises a
    GraphValidationError with a code when the bot cannot run it:
      group_missing     : the graph has no group by that name;
      symbol_changed    : the group trades another symbol than the bot;
      direction_changed : the group's direction is not the bot's (a
                          regime_switch group needs graph_direction_mode
                          "regime_switch", or its P&L would count one side);
      group_invalid     : a rule regime on a graph bot (the graph's regime
                          terminal is the regime), or a long or short group
                          whose regime terminal flips with close_and_reverse
                          (that trades both sides, which only a regime_switch
                          group may do live).
    Cheap (no data, no cook), but run it with the compile in the executor.
    """
    from nodebuilder.trading import nodes_groups as _groups

    if cfg.regime is not None and (cfg.is_bidirectional or cfg.graph_direction_mode == "regime_switch"):
        raise _group_error(
            "A graph bot takes its regime from the graph's Regime terminal.  "
            "Turn the bot's rule regime off.", "group_invalid")
    try:
        group = _groups.group_named(program, cfg.graph_group)
    except KeyError:
        names = ", ".join(repr(g.name) for g in (program.groups or ()))
        wanted = cfg.graph_group if cfg.graph_group else "(one group)"
        raise _group_error(
            f"The graph has no Output Group {wanted!r} for this bot (groups: {names}).",
            "group_missing") from None
    plan = group.plan_for(cfg.direction)
    symbol = str(cfg.symbol or "").strip().upper()
    if not group.implicit:
        if group.symbol and symbol != group.symbol:
            raise _group_error(
                f"Output Group {group.name!r} trades {group.symbol}, but this bot trades {symbol}.",
                "symbol_changed", group.node_id)
        mode = group.direction
    else:
        mode = cfg.direction
    if mode == "regime_switch":
        ok = cfg.graph_direction_mode == "regime_switch"
    else:
        ok = cfg.direction == mode and cfg.graph_direction_mode in (None, mode)
    if not ok:
        bot_mode = cfg.graph_direction_mode or cfg.direction
        raise _group_error(
            f"Output Group {group.name!r} is {mode}, but this bot was made for {bot_mode}.",
            "direction_changed", group.node_id)
    if plan.direction in ("long", "short") and plan.regime is not None \
            and plan.on_flip == "close_and_reverse":
        raise _group_error(
            f"Output Group {group.name!r} is {plan.direction} with a Regime terminal that "
            "flips with close_and_reverse, which trades both sides.  A bot can only do "
            "that for a regime_switch group: make the group regime_switch, or set "
            "on_flip to close_only or hold.", "group_invalid", plan.regime.node_id)
    gprog = _groups.program_for_steps(program, group.step_ids)
    # The reference frames the group's cook needs (plan D8), exactly as
    # prepare.build_graph_attrs will ask for them: every Ticker but the
    # group's primary one; in the implicit group, every prefixed Ticker.
    from nodebuilder.trading.align import reference_needs, ticker_roles
    roles = ticker_roles(gprog)
    refs = [key for key in roles.keys() if key[0] and key[1]]
    needs = reference_needs(gprog, roles) if refs else {}
    return GraphLive(group=group, plan=plan, program=gprog, references=tuple(refs),
                     reference_needs=tuple((key, int(needs[key])) for key in refs if key in needs))


def _utc_timestamp(value):
    """*value* as a UTC pandas Timestamp (a naive time counts as UTC, as in
    align._utc_ns), or None when it is not a time."""
    import pandas as pd
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if ts is pd.NaT:
        return None
    return ts.tz_convert("UTC") if ts.tzinfo is not None else ts.tz_localize("UTC")


def _bar_end(start, interval: str, index):
    """When a bar that starts at *start* (UTC) ends: its interval's length
    (calendar months for 1mo/3mo), else the frame's usual bar spacing.
    None when neither tells."""
    import pandas as pd
    from nodebuilder.trading.align import INTERVAL_SECONDS, bar_seconds

    months = {"1mo": 1, "3mo": 3}.get(interval)
    if months:
        return start + pd.DateOffset(months=months)
    secs = INTERVAL_SECONDS.get(interval) or bar_seconds(index)
    if not secs:
        return None
    return start + pd.Timedelta(seconds=float(secs))


# Bars per regular session of each interval, to count how many of the
# bot's own bars make up one reference bar (a daily bar is 78 five-minute
# bars, a weekly bar 5 daily bars).
_SESSION_BARS: dict[str, float] = {
    "1m": 390, "2m": 195, "5m": 78, "15m": 26, "30m": 13, "60m": 6.5, "1h": 6.5,
    "90m": 13 / 3, "1d": 1, "5d": 0.2, "1wk": 0.2, "1mo": 1 / 21, "3mo": 1 / 63,
}


def _bars_per_reference_bar(primary_interval, ref_interval) -> Optional[int]:
    """How many of the primary's bars one reference bar spans (at least 1),
    or None when an interval is unknown."""
    import math
    from nodebuilder.trading.align import INTERVAL_SECONDS

    p, r = _SESSION_BARS.get(str(primary_interval)), _SESSION_BARS.get(str(ref_interval))
    if p and r:
        return max(1, math.ceil(p / r - 1e-9))
    ps, rs = INTERVAL_SECONDS.get(str(primary_interval)), INTERVAL_SECONDS.get(str(ref_interval))
    if ps and rs:
        return max(1, math.ceil(rs / ps - 1e-9))
    return None


def _aligns_by_end_time(ref_df, ref_interval, primary_df, primary_interval) -> bool:
    """True when the cook aligns this reference by the end-time rule (the
    last reference bar that has ended), False for the exact join: the same
    decision align makes (align.shifts; align.is_coarser before KA-1)."""
    from nodebuilder.trading import align

    decide = getattr(align, "shifts", None) or align.is_coarser
    return bool(decide(ref_interval, ref_df.index, primary_interval, primary_df.index))


def reference_behind(ref_df, ref_interval: str, primary_df,
                     primary_interval: Optional[str] = None) -> Optional[str]:
    """Why a reference frame is too old for the bot's newest bar, or None
    (F435 W5 LM-4).  It follows the alignment rule the cook uses:

    - exact join (the same interval on one calendar, or a finer one): the
      newest primary bar reads the reference bar at or before its time, so
      the reference must hold the bar whose span reaches that time (its
      last bar must end after the primary's last bar starts).  A reference
      one bar behind (a provider that publishes its bar later, a cache
      entry fetched earlier) would give the new bar an older value than the
      backtest sees;
    - end-time rule (a coarser reference, or a daily one on another
      calendar): the newest primary bar reads the last reference bar that
      has ended, so the forming bar is not needed.  The frame is behind
      when a whole reference bar's worth of the primary's own bars started
      after its last bar ended: a reference bar is missing.  The primary's
      bars serve as the calendar, so weekends and holidays never count.

    None when a frame has no bar times, or the alignment cannot be told."""
    try:
        ref_last = _utc_timestamp(ref_df.index[-1])
        prim_last = _utc_timestamp(primary_df.index[-1])
    except (AttributeError, IndexError):
        return None
    if ref_last is None or prim_last is None:
        return None
    end = _bar_end(ref_last, str(ref_interval), ref_df.index)
    if end is None or end > prim_last:
        return None  # it holds the bar that covers the newest primary bar
    behind = (f"its newest bar ({ref_df.index[-1]}) is older than the bar "
              f"{primary_df.index[-1]} needs")
    try:
        end_time_rule = _aligns_by_end_time(ref_df, ref_interval, primary_df, primary_interval)
    except Exception:  # an index the alignment cannot read: nothing to tell
        return None
    if not end_time_rule:
        return behind
    per_bar = _bars_per_reference_bar(primary_interval, ref_interval)
    if per_bar is None:
        return None
    import numpy as np
    import pandas as pd

    idx = pd.DatetimeIndex(primary_df.index)
    idx = idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")
    starts = idx.as_unit("ns").asi8
    after = int(np.count_nonzero((starts >= end.value) & (starts < prim_last.value)))
    return behind if after >= per_bar else None


def references_behind(got: dict, primary_df, primary_interval: Optional[str]) -> dict:
    """{(SYMBOL, interval): reason} for every fetched reference frame that is
    behind the primary's newest bar (reference_behind).  Failed and empty
    fetches are left to the caller.  CPU work: run it in the executor."""
    out = {}
    for key, ref_df in got.items():
        if isinstance(ref_df, BaseException) or ref_df is None or len(ref_df) == 0:
            continue
        why = reference_behind(ref_df, key[1], primary_df, primary_interval)
        if why is not None:
            out[key] = why
    return out


def atr_trailing_stop(cfg):
    """The trailing stop the bar prep builds ATR for: the bot's own, or a
    per-direction one when only that one is ATR based, so a per-side ATR
    trailing stop never reads ATR 0 (no trail)."""
    for ts in (cfg.trailing_stop, getattr(cfg, "long_trailing_stop", None),
               getattr(cfg, "short_trailing_stop", None)):
        if ts is not None and getattr(ts, "type", None) == "atr":
            return ts
    return cfg.trailing_stop


def graph_tick_config(cfg, plan):
    """The bot config one tick runs with: the group's graph fields on top
    (sim_bridge.apply_to_bot_config: the graph wins, per-direction fields
    cleared, BotConfig's clamps run).  With a Regime terminal, a rule
    regime stub carries the terminal's on_flip, because the shared flip
    handler (regime.py) reads cfg.regime.on_flip.  The stub stays disabled,
    so is_bidirectional still comes from the group.  Never saved."""
    from models import RegimeConfig
    from nodebuilder.trading.sim_bridge import apply_to_bot_config

    eff = apply_to_bot_config(cfg, plan)
    if plan.regime is not None:
        eff = eff.model_copy(update={"regime": RegimeConfig(enabled=False, on_flip=plan.on_flip)})
    return eff


def _reads_columns(plan) -> bool:
    """True when the bot needs more than the group's Entry and Exit columns:
    a regime_switch group, a Regime terminal, or a wired Size or Stop."""
    return (plan.direction == "regime_switch" or plan.regime is not None
            or plan.size is not None or plan.stop is not None)


def _signal_program(program, plan):
    """*program* with its Entry and Exit pointed at the group's own
    terminals, for the per-bar reader (a graph with several groups names
    only the first group's terminals at program level)."""
    if plan is None or plan.entry is None:
        return program
    from nodebuilder.evaluator import NO_EXIT_ATTR
    return _replace(
        program,
        entry_node=plan.entry.node_id, entry_attr=plan.entry.attr,
        exit_node=plan.exit.node_id if plan.exit is not None else None,
        exit_attr=plan.exit.attr if plan.exit is not None else NO_EXIT_ATTR,
    )


def group_bar_signals(plan, result, i: int) -> dict:
    """The group's columns at bar *i* of a cook, read the way the backtest
    reads them (sim_bridge: a column cast to bool).  Keys: entry, exit (a
    long or short group), entry_long, entry_short, exit_long, exit_short (a
    regime_switch group), regime (None without a Regime terminal) and
    sample (sim_bridge.EntrySample for a wired Size or Stop, else None)."""
    import numpy as np
    from nodebuilder.trading import sim_bridge as _sb

    def at(read) -> bool:
        if read is None:
            return False
        return bool(np.asarray(result.column(read.node_id, read.attr), dtype=bool)[i])

    return {
        "entry": at(plan.entry), "exit": at(plan.exit),
        "entry_long": at(plan.entry_long), "entry_short": at(plan.entry_short),
        "exit_long": at(plan.exit_long), "exit_short": at(plan.exit_short),
        "regime": at(plan.regime) if plan.regime is not None else None,
        "sample": (_sb.entry_sample(plan, result, i)
                   if (plan.size is not None or plan.stop is not None) else None),
    }


def graph_entry_direction(plan, sigs: dict, direction: str) -> str:
    """The side a graph bot may enter on this bar: "long", "short" or "flat".
    regime_switch: long while the regime is true, short while it is false.
    A long or short group with a Regime terminal enters only while the
    regime is true (the backtest's gated entry)."""
    if plan.direction == "regime_switch":
        return "long" if sigs.get("regime") else "short"
    if plan.regime is not None:
        return direction if sigs.get("regime") else "flat"
    return direction


def graph_buy_signal(plan, sigs: dict, entry_dir: str) -> bool:
    """The group's entry signal for the side it may enter."""
    if plan.direction == "regime_switch":
        return bool(sigs.get("entry_long") if entry_dir == "long" else sigs.get("entry_short"))
    return bool(sigs.get("entry"))


def graph_sell_signal(plan, sigs: dict, position_direction: Optional[str]) -> bool:
    """The group's exit signal for an open position (a regime_switch
    position exits on its own side's Exit)."""
    if plan.direction == "regime_switch":
        if position_direction == "long":
            return bool(sigs.get("exit_long"))
        if position_direction == "short":
            return bool(sigs.get("exit_short"))
        return False
    return bool(sigs.get("exit"))


def cook_graph_bar(program, df, trailing_stop, plan=None, frames=None, interval=None):
    """Cook a compiled graph over the fetched bars and read its last bar.

    Returns (attrs, signals).  attrs is the dict the exit helpers read (ATR
    for an ATR trailing stop).  With no *plan* (or a group that reads only
    its Entry and Exit), signals is {"entry": bool, "exit": bool}; a group
    that needs more columns gets group_bar_signals' dict.  *program* is the
    group's program (GraphLive.program) and *frames* maps (SYMBOL, interval)
    to the reference frames the group reads.  *interval* is the bot's own
    interval: the reference alignment then decides coarser-or-same from the
    interval names, as the backtest does (sim_bridge.run_group), never from
    the bar spacing of a short live frame (F435 W5 LM-8, KA-5).  The whole
    cook (indicator work and evaluation) is CPU work: the runner passes
    this function to _run_in_executor, so it never runs on the polling loop
    (Key Bugs Fixed).  Bar data comes from nodebuilder.prepare, the same
    prep the graph backtest uses.
    """
    from nodebuilder import evaluator as _evaluator
    if frames:
        attrs = build_graph_attrs(program, df, trailing_stop, frames=frames, interval=interval)
    else:
        attrs = build_graph_attrs(program, df, trailing_stop)
    i = len(df) - 1
    if plan is None or not _reads_columns(plan):
        sigs = _evaluator.evaluate_graph(_signal_program(program, plan), attrs, i)
        return attrs, sigs
    from nodebuilder.trading import sim_bridge as _sb
    result = _sb.cook(program, attrs, plan.keep_ids())
    return attrs, group_bar_signals(plan, result, i)


class BotRunner(RegimeMixin, ExitsMixin):
    def __init__(self, config: BotConfig, state: BotState, manager: BotManager):
        self.config = config
        self.state = state
        self.manager = manager
        self._error_listener = None  # bound IBKR error callback
        self._active_order_ids: set[str] = set()  # order IDs placed by this bot
        self._last_broker_qty: int | None = None  # for partial-position reconciliation
        self._loop: asyncio.AbstractEventLoop | None = None  # set in run() for thread-safe scheduling
        self._short_window_warned: str | None = None  # graph hash already warned about a short fetch
        self._window_cut_warned: str | None = None  # graph hash already warned about a provider limit
        # This tick's wired Size / Stop values (sim_bridge.EntrySample) for a
        # graph bot, or None.  Set by _tick after the cook; _enter_position
        # reads it, also for a regime flip's reverse entry on the same bar.
        self._graph_sample = None
        # The bar whose price exits (stop, trailing stop, time stop) already
        # ran while a reference frame was down (LM-1).  Exits run once per
        # bar (_evaluate_exit_reason counts the bar toward the time stop), so
        # neither the retries of that bar nor its later signal pass run them
        # again.
        self._exits_bar: str | None = None
        # (graph hash, reference key, kind) already warned about (LM-9).
        self._ref_warned: set = set()
        # A resumed position under a wired Stop whose bar had no stop value:
        # its stop is taken from the next bar that has one (LM-6).
        self._stop_pending = False

    def _log(self, level: str, msg: str):
        entry = {"time": datetime.now(timezone.utc).isoformat(), "msg": msg, "level": level}
        self.state.append_activity_log(entry)

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

    def _bot_pnl(self, cfg, state) -> float:
        """Realized P&L, over both sides for a bot that trades both (a rule
        regime bot or a regime_switch graph bot)."""
        if cfg.is_bidirectional:
            return compute_bidirectional_pnl(cfg.symbol, cfg.bot_id, since=cfg.pnl_epoch)
        return compute_realized_pnl(cfg.symbol, cfg.direction, bot_id=cfg.bot_id, since=cfg.pnl_epoch)

    def _entry_blocked(self) -> Optional[str]:
        """"size" or "stop" when this tick's wired Size or Stop has no usable
        value, so the entry must not open (the backtest's rule), else None."""
        sample = self._graph_sample
        return sample.blocked if sample is not None else None

    async def _enter_position(self, cfg, state, direction: str, price: float, indicators: dict, i: int):
        """Submit entry order in the given direction and update state."""
        entry_is_short = direction == "short"

        # A graph bot whose wired Size or Stop has no value on this bar does
        # not enter (also the reverse entry of a regime flip).
        sample = self._graph_sample if cfg.kind == "graph" else None
        blocked = sample.blocked if sample is not None else None
        if blocked is not None:
            self._log("INFO", f"Skipping entry: the graph's {blocked} has no value on this bar")
            return

        # Compute effective capital (bidirectional for regime bots)
        current_capital = cfg.allocated_capital + self._bot_pnl(cfg, state)
        # B25: use per-direction position_size when available
        _dir_ps = (
            (cfg.long_position_size if direction == 'long' else cfg.short_position_size)
            if hasattr(cfg, 'long_position_size') else None
        ) or cfg.position_size
        # A wired Size terminal: the value at the entry bar, clamped as the
        # backtest clamps it (sim_bridge.entry_sample).
        if sample is not None and sample.size is not None:
            _dir_ps = sample.size
        effective_size = max(current_capital, 0) * _dir_ps
        if cfg.dynamic_sizing and cfg.dynamic_sizing.enabled:
            if state.consec_sl_count >= cfg.dynamic_sizing.consec_sls:
                effective_size *= (cfg.dynamic_sizing.reduced_pct / 100.0)
                self._log("INFO", f"Dynamic sizing active: reduced to {cfg.dynamic_sizing.reduced_pct}%")

        qty = math.floor(effective_size / price)
        if qty < 1:
            self._log("WARN", f"Position too small: {effective_size:.2f} / {price:.2f} = {qty} shares")
            return

        # Resolve per-direction stop for OTO bracket
        _sl = None
        if hasattr(cfg, 'long_stop_loss_pct') and direction == 'long' and cfg.long_stop_loss_pct is not None:
            _sl = cfg.long_stop_loss_pct
        elif hasattr(cfg, 'short_stop_loss_pct') and direction == 'short' and cfg.short_stop_loss_pct is not None:
            _sl = cfg.short_stop_loss_pct
        else:
            _sl = cfg.stop_loss_pct
        # A wired Stop terminal: the value at the entry bar is the trade's
        # stop until the exit; 0 or less means no stop (the backtest's rule).
        wired_stop = sample is not None and sample.stop_pct is not None
        if wired_stop:
            _sl = sample.stop_pct if sample.stop_pct > 0 else None

        # Resolve per-direction trailing stop for OTO condition
        _ts = None
        if hasattr(cfg, 'long_trailing_stop') and direction == 'long' and cfg.long_trailing_stop is not None:
            _ts = cfg.long_trailing_stop
        elif hasattr(cfg, 'short_trailing_stop') and direction == 'short' and cfg.short_trailing_stop is not None:
            _ts = cfg.short_trailing_stop
        else:
            _ts = cfg.trailing_stop

        # From the order until entry_price is set (the fill poll takes up to
        # 2.5 s) the bot holds a position that entry_price does not show yet:
        # graph_update refuses while this is set (F435 W5 LM-3).
        state.entry_in_flight = True
        try:
            try:
                provider = get_trading_provider(cfg.broker)
                if entry_is_short:
                    order_req = BrokerOrderRequest(symbol=cfg.symbol.upper(), qty=qty, side="sell")
                elif _sl and not _ts:
                    stop_price = round(price * (1 - _sl / 100), 2)
                    order_req = BrokerOrderRequest(
                        symbol=cfg.symbol.upper(), qty=qty, side="buy",
                        order_type="stop", stop_price=stop_price,
                    )
                else:
                    order_req = BrokerOrderRequest(symbol=cfg.symbol.upper(), qty=qty, side="buy")

                result = await self._run_in_executor(provider.submit_order, order_req)
                self._active_order_ids.add(result.order_id)
            except Exception as e:
                self._log("ERROR", f"Entry order failed: {e}")
                return

            fill_price = await self._get_fill_price_provider(provider, result.order_id, price)

            state.entry_price = fill_price
        finally:
            state.entry_in_flight = False
        state.entry_time = datetime.now(timezone.utc).isoformat()
        state.entry_bar_count = 0
        state.trail_peak = fill_price
        state.position_direction = direction
        state.entry_stop_pct = _sl if wired_stop else None
        self._stop_pending = False
        self._last_broker_qty = qty
        state.trades_count += 1
        side_label = "SHORT" if entry_is_short else "BUY"
        state.last_signal = side_label
        side_key = "short" if entry_is_short else "buy"
        cost_bps = slippage_cost_bps(side_key, expected=price, fill=fill_price)
        bias_bps = fill_bias_bps(side_key, expected=price, fill=fill_price)
        state.append_slippage_bps(cost_bps)
        self._log(
            "TRADE",
            f"{side_label} {qty} {cfg.symbol} @ {fill_price:.2f} "
            f"(expected={price:.2f}, cost={cost_bps:.1f}bps, bias={bias_bps:+.1f}bps)",
        )

        try:
            _log_trade(cfg.symbol, "short" if entry_is_short else "buy", qty, fill_price,
                       source="bot", reason="entry", expected_price=price,
                       direction=direction, bot_id=cfg.bot_id, broker=cfg.broker)
        except Exception as e:
            self._log("ERROR", f"Journal write failed: {e}")

        asyncio.create_task(notify_entry(
            symbol=cfg.symbol,
            direction=direction,
            qty=qty,
            price=fill_price,
            strategy_name=cfg.strategy_name,
            bot_id=cfg.bot_id,
        ))

        await asyncio.to_thread(self.manager.save)

    def _compile_graph_program(self, cfg):
        """Compile cfg.graph into a CompiledProgram.

        Raises whatever compile() raises for a graph that cannot run.
        """
        return compile_bot_graph(cfg.graph, cfg.bot_id)

    def _graph_program(self, cfg):
        """(program, hash, live) for cfg.graph; compiles only when the hash
        changed.  live is the bot's own group (graph_bot_live).

        Runs in the executor (_tick passes it to _run_in_executor): hashing
        and compiling are CPU work.  It only reads state; _tick stores the
        result back on the loop.  Compile and group errors propagate to _tick.
        """
        current_hash = graph_hash(cfg.graph)
        cached = self.state.compiled_program
        if cached is not None and current_hash == self.state.graph_hash:
            program = cached
        else:
            program = self._compile_graph_program(cfg)
        return program, current_hash, graph_bot_live(program, cfg)

    def _reference_start(self, program, interval: str, cfg) -> str:
        """First day of a reference frame's fetch window: the same window
        rule as the bot's own bars, worked out for the reference's interval
        and its provider's limit."""
        return live_fetch_start(program, interval, atr_trailing_stop(cfg),
                                max_days=provider_max_days(cfg.data_source, interval))

    def _reference_frames(self, cfg, live: GraphLive, own_key, df, got: dict, behind: dict):
        """(frames, problem) for the group's reference Tickers.

        frames maps (SYMBOL, interval) to each reference frame (the bot's own
        frame for a reference with the bot's own key).  problem is None, or
        why the signals cannot be worked out on this bar: a reference fetch
        that failed or came back empty (LM-1), or a frame that is behind the
        bot's newest bar (LM-4; *behind* is references_behind's result).
        Warns once per graph when a reference's provider cuts its window or
        its frame is short (LM-9)."""
        frames = {}
        for key in live.references:
            ref_df = df if key == own_key else got.get(key)
            if isinstance(ref_df, BaseException) or ref_df is None or len(ref_df) == 0:
                why = f": {ref_df}" if isinstance(ref_df, BaseException) else ": no bars"
                return None, f"Reference fetch failed for {key[0]} {key[1]}{why}"
            if key != own_key:
                if key in behind:
                    return None, f"Reference {key[0]} {key[1]} is not up to date: {behind[key]}"
                self._warn_reference_window(cfg, live, key, ref_df)
            frames[key] = ref_df
        return frames, None

    def _warn_reference_window(self, cfg, live: GraphLive, key, ref_df) -> None:
        """The primary frame's two warnings, for one reference frame: its
        provider serves less history than the reference's nodes need, or its
        frame came back shorter than that.  Once per graph and reference."""
        from nodebuilder.prepare import factor_window_days, short_history_bars

        need = dict(live.reference_needs).get(key)
        if not need:
            return
        sym, itv = key
        h = self.state.graph_hash
        max_days = provider_max_days(cfg.data_source, itv)
        if (max_days and factor_window_days(need, itv) > max_days
                and (h, key, "cut") not in self._ref_warned):
            self._ref_warned.add((h, key, "cut"))
            self._log(
                "WARN",
                f"{cfg.data_source} serves at most {max_days} days of {itv} bars, less "
                f"than reference {sym} {itv} needs; the live signal may differ from "
                f"the backtest.",
            )
        want = short_history_bars(need)
        if len(ref_df) < want and (h, key, "short") not in self._ref_warned:
            self._ref_warned.add((h, key, "short"))
            self._log(
                "WARN",
                f"Reference {sym} {itv} wants {want} bars of history but the fetch "
                f"returned {len(ref_df)} ({cfg.data_source}); the live signal may "
                f"differ from the backtest.",
            )

    async def _broker_position(self, cfg, state, price: float, is_regime: bool,
                               wired_stop: bool) -> tuple[bool, float]:
        """(has_position, broker_qty): the broker's position on the bot's
        symbol, the source of truth.  Raises when the broker cannot be asked.

        A bot that trades one side matches its own side.  A bot that can
        hold either side matches the side it tracks; when it tracks none (a
        Stop that kept the position, a restart), it adopts whichever side
        the broker holds and manages it (F435 W5 LM-2): skipping a short
        there left it with no stop at all and let the bot stack a second
        one.  A position found with no entry_price is resumed."""
        bidirectional = cfg.is_bidirectional
        if state.position_direction and (is_regime or bidirectional):
            check_dir = state.position_direction
        elif bidirectional:
            check_dir = None  # either side is this bot's
        else:
            check_dir = cfg.direction
        provider = get_trading_provider(cfg.broker)
        positions = await self._run_in_executor(provider.get_positions)
        for pos in positions:
            if pos["symbol"] != cfg.symbol.upper():
                continue
            if check_dir is not None and pos["side"] != check_dir:
                continue
            if state.entry_price is None:
                self._resume_position(cfg, state, pos, price,
                                      adopt_side=is_regime or bidirectional,
                                      wired_stop=wired_stop)
            return True, abs(pos["qty"])
        return False, 0

    def _resume_position(self, cfg, state, pos: dict, price: float, *, adopt_side: bool,
                         wired_stop: bool) -> None:
        """Track a broker position the bot has no entry for."""
        state.entry_price = pos["avg_entry"]
        state.trail_peak = price
        if adopt_side and not state.position_direction:
            state.position_direction = pos["side"]
            if cfg.is_bidirectional:
                self._log("INFO", f"Adopted the broker's {pos['side']} position on {cfg.symbol}")
        if wired_stop:
            self._resume_wired_stop(cfg, state)
        if state.entry_time is None:
            state.entry_time = datetime.now(timezone.utc).isoformat()
            self._log("INFO", "Resumed position tracking; entry_time set to current (borrow accrual begins now)")
        self._log("INFO", f"Resumed tracking position: entry={state.entry_price:.2f}")

    def _resume_wired_stop(self, cfg, state) -> None:
        """The stop of a resumed position under a wired Stop terminal (LM-6).

        A stop kept from this trade (a Stop that left the position open)
        stays: the backtest fixes the stop at the entry bar.  Else this
        bar's value is taken (0 or less: no stop).  With no value on this
        bar the position is not resumed silently: the bot alerts, and the
        next bar with a value sets the stop."""
        if state.entry_stop_pct is not None:
            return
        sample = self._graph_sample
        if sample is not None and sample.stop_pct is not None:
            state.entry_stop_pct = sample.stop_pct if sample.stop_pct > 0 else None
            return
        self._stop_pending = True
        msg = ("Resumed a position with no stop: the graph's wired Stop has no value "
               "on this bar.  The next bar with a value sets the stop.")
        self._log("WARN", msg)
        asyncio.create_task(notify_error(symbol=cfg.symbol, error_msg=msg, bot_id=cfg.bot_id))

    def _take_pending_stop(self, state, has_position: bool) -> None:
        """Set a resumed position's stop from this bar's wired Stop, once it
        has a value (see _resume_wired_stop)."""
        if not self._stop_pending:
            return
        if not has_position:
            self._stop_pending = False
            return
        sample = self._graph_sample
        if sample is not None and sample.stop_pct is not None:
            state.entry_stop_pct = sample.stop_pct if sample.stop_pct > 0 else None
            self._stop_pending = False
            self._log("INFO", f"Stop for the resumed position set from this bar: "
                              f"{state.entry_stop_pct}")

    async def _manage_without_signals(self, cfg, state, df, live: GraphLive, is_regime: bool,
                                      in_hours: bool, bar: str) -> None:
        """A reference frame is missing on a new bar (LM-1, LM-4): no
        signal, regime or entry can be worked out, but an open position
        keeps its price exits.  Find the position (resume or adopt it), book
        an external close, and run the stop, trailing stop and time stop on
        the bot's own bars, once per bar (_exits_bar).  An exit marks the
        bar done (nothing else happens on an exit bar); otherwise the bar
        stays open for the signal work.  Rule bots never get here."""
        if self._exits_bar == bar:
            return
        price = float(df["Close"].iloc[-1])
        i = len(df) - 1
        state.last_price = price
        wired_stop = live.plan.stop is not None
        try:
            has_position, broker_qty = await self._broker_position(
                cfg, state, price, is_regime, wired_stop)
        except Exception as e:
            self._log("WARN", f"Position check failed: {e}")
            return
        self._take_pending_stop(state, has_position)
        pos_is_short = (state.position_direction == "short" if state.position_direction
                        else cfg.direction == "short")
        if not has_position:
            if state.entry_price is not None:
                attrs = await self._run_in_executor(build_graph_attrs, None, df, atr_trailing_stop(cfg))
                if await self._detect_external_close(cfg, state, has_position, pos_is_short,
                                                     broker_qty, price, df, i, in_hours, attrs):
                    return
                state.entry_price = None
                state.entry_bar_count = 0
                state.trail_peak = None
                state.trail_stop_price = None
                state.entry_stop_pct = None
                state.pending_close_order_id = None
                state.pending_close_reason = None
                state.position_direction = None
                self._last_broker_qty = None
            # Flat: nothing ran for this bar, so the next tick looks again
            # (a position that shows up meanwhile gets its exits).
            return
        self._last_broker_qty = broker_qty
        # ATR for an ATR trailing stop, from the bot's own bars only (no
        # reference frame is read).
        attrs = await self._run_in_executor(build_graph_attrs, None, df, atr_trailing_stop(cfg))
        if wired_stop:
            cfg = cfg.model_copy(update={"stop_loss_pct": state.entry_stop_pct})
        exit_reason = await self._evaluate_exit_reason(
            cfg, state, price, df, i, pos_is_short, attrs, [], is_regime)
        self._exits_bar = bar
        if exit_reason:
            self._log("INFO", f"Exit {exit_reason} while a reference is down")
            await self._execute_exit(cfg, state, exit_reason, price, broker_qty, pos_is_short,
                                     df, i, in_hours, attrs)
            state.last_bar_time = bar

    async def _tick(self):
        cfg = self.config
        state = self.state
        # Rule bots: the regime gate is the rule regime (is_bidirectional).
        # Graph bots: the gate is the group's Regime terminal, set below
        # once the graph is compiled.
        is_regime = cfg.is_bidirectional
        self._graph_sample = None

        # Graph-mode: compute buy_rules/sell_rules lists only for rule-mode bots
        if cfg.kind != "graph":
            buy_rules = [migrate_rule(r) for r in cfg.buy_rules]
            sell_rules = [migrate_rule(r) for r in cfg.sell_rules]
            all_rules = buy_rules + sell_rules
            # Include dual rule sets so their indicators are computed
            for extra in (cfg.long_buy_rules, cfg.long_sell_rules, cfg.short_buy_rules, cfg.short_sell_rules):
                if extra:
                    all_rules = all_rules + [migrate_rule(r) for r in extra]
        else:
            buy_rules = []
            sell_rules = []
            all_rules = []
        loop = asyncio.get_event_loop()

        state.last_tick = datetime.now(timezone.utc).isoformat()
        state.last_scan_at = datetime.now(timezone.utc).isoformat()
        state.scans_count += 1

        # 1. Trading hours check (entries only)
        in_hours = self._in_trading_hours()

        # Skip fetch entirely when outside trading hours and no open position
        if not in_hours and state.entry_price is None:
            return

        # 2. Fetch bars.  Rule bots fetch 30 days back for indicator warmup.
        # A graph bot compiles first (only when its graph changed) and
        # fetches the window its program needs (plan D5, critic 9).
        from datetime import timedelta, date
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=30)).isoformat()
        program = None
        live: Optional[GraphLive] = None

        if cfg.kind == "graph":
            if cfg.graph is None:
                # Permanent error: a kind=graph bot with no graph cannot
                # recover by retrying. Mirror the structural-IBKR-error path:
                # set status=error + pause_reason so the supervisor loop
                # surfaces a pause rather than burning MAX_CONSEC_ERRORS
                # retries against an unresolvable config. (Soft-warn at
                # BotConfig validation is preserved so stop/edit/start can
                # transiently observe graph=None.)
                self.state.status = "error"
                self.state.pause_reason = "invalid config: kind=graph but graph is None"
                self.state.error_message = self.state.pause_reason
                self.manager.save()
                return
            # A graph that does not compile, or whose settings the bot config
            # refuses, cannot recover by retrying.  Every later tick would
            # skip the exit checks without a word, so pause the bot and
            # alert, as for graph=None.
            try:
                # Hashing, compiling and finding the bot's own Output Group
                # run in the executor, never on the loop.
                program, current_hash, live = await self._run_in_executor(
                    self._graph_program, cfg
                )
                state.compiled_program = program
                state.graph_hash = current_hash

                # The graph wins: the group's terminals and Settings nodes
                # (size, stop, trailing stop, time stop, slippage, borrow
                # rate) replace the bot config's values for the rest of this
                # tick, the same way the graph backtest applies them.  Rebuilt
                # every tick so it always follows the compiled program.
                cfg = graph_tick_config(cfg, live.plan)
            except (GraphValidationError, ValidationError) as e:
                state.compiled_program = None
                state.graph_hash = None
                self.state.status = "error"
                self.state.pause_reason = f"Graph does not compile: {e}"
                self.state.error_message = self.state.pause_reason
                self._log("ERROR", self.state.pause_reason)
                if state.entry_price is not None:
                    self._log("WARN", "Bot paused with an open position: stops are not managed")
                asyncio.create_task(notify_error(
                    symbol=cfg.symbol,
                    error_msg=self.state.pause_reason
                    + (" (open position, stops not managed)" if state.entry_price is not None else ""),
                    bot_id=cfg.bot_id,
                ))
                self.manager.save()
                return
            # Anything else (a compile bug, an executor error) propagates.  No
            # bar has been marked done yet, so the next tick retries this bar
            # and repeated failures reach MAX_CONSEC_ERRORS and alert.

            # The regime gate of a graph bot is its group's Regime terminal.
            is_regime = live.plan.regime is not None

            # The window holds the program's required_lookback_bars (with
            # margin), and never less than the 30 days every bot used before,
            # but never more than the bot's data provider serves for this
            # interval (F435 W2 LT-4).  Warn once per graph when that limit
            # cuts the window.
            max_days = provider_max_days(cfg.data_source, cfg.interval)
            start_date = live_fetch_start(program, cfg.interval, atr_trailing_stop(cfg),
                                          max_days=max_days)
            if (self._window_cut_warned != state.graph_hash
                    and window_cut_by_provider(program, cfg.interval, atr_trailing_stop(cfg), max_days)):
                self._window_cut_warned = state.graph_hash
                self._log(
                    "WARN",
                    f"{cfg.data_source} serves at most {max_days} days of {cfg.interval} bars, "
                    f"less than this graph's live window; the live signal may differ "
                    f"from the backtest.",
                )

        # A graph group can read other Tickers (reference tickers, plan D8).
        # Their frames are fetched next to the bot's own bars, all at once
        # (asyncio.gather), through the same TTL-cached fetch path.
        own_key = (str(cfg.symbol).strip().upper(), cfg.interval)
        ref_keys = [k for k in (live.references if live is not None else ()) if k != own_key]
        try:
            if ref_keys:
                fetched = await asyncio.gather(
                    fetch_ohlcv_async(cfg.symbol, start_date, end_date, cfg.interval, cfg.data_source),
                    *(fetch_ohlcv_async(sym, self._reference_start(program, itv, cfg), end_date,
                                        itv, cfg.data_source)
                      for sym, itv in ref_keys),
                    return_exceptions=True,
                )
                if isinstance(fetched[0], BaseException):
                    raise fetched[0]
                df = fetched[0]
            else:
                df = await fetch_ohlcv_async(
                    cfg.symbol, start_date, end_date, cfg.interval, cfg.data_source
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

        frames = None
        if live is not None and live.references:
            # Every reference frame is needed for the group's signals.  When
            # one failed, came back empty or lags the bot's newest bar
            # (F435 W5 LM-1, LM-4), an open position still gets its price
            # exits on the bot's own bars; the bar is not marked done, so the
            # signal work is retried next tick; and the tick raises, so
            # run() counts every such bar toward MAX_CONSEC_ERRORS and
            # alerts.  Returning quietly here left every stop of an open
            # position unchecked for as long as the reference was down.
            got = dict(zip(ref_keys, fetched[1:])) if ref_keys else {}
            # The freshness check reads every bar time: CPU work, so it runs
            # in the executor like the cook (Key Bugs Fixed).
            behind = await self._run_in_executor(references_behind, got, df, cfg.interval)
            frames, problem = self._reference_frames(cfg, live, own_key, df, got, behind)
            if problem is not None:
                self._log("WARN", problem)
                await self._manage_without_signals(cfg, state, df, live, is_regime, in_hours, last_bar)
                held = (" (open position: stop, trailing stop and time stop still checked; "
                        "signal exits wait)" if state.entry_price is not None else "")
                raise ReferenceUnavailableError(f"{problem}{held}")

        prev_bar_time = state.last_bar_time
        state.last_bar_time = last_bar
        self._log("INFO", f"New bar: {last_bar} | close={df['Close'].iloc[-1]:.2f}")

        # 4. Compute indicators (rule-mode) OR cook the graph (graph-mode)
        i = len(df) - 1
        price = float(df["Close"].iloc[-1])
        state.last_price = price

        # Pre-initialize signal variables; graph mode computes them in section 4,
        # rule mode computes them lazily in sections 6 and 7.
        buy_signal: bool = False
        sell_signal: bool = False

        if cfg.kind == "graph":
            # The frame can come back shorter than the graph needs (the
            # provider clamps intraday history).  Warn once per graph.
            # Same rule as the fetch window (F435 W2 LT-5): fewer than
            # LIVE_LOOKBACK_FACTOR x the lookback and recursive indicators
            # (Wilder RSI, EMA) may not have settled to the backtest's values.
            from nodebuilder.prepare import graph_lookback_bars, short_history_bars
            need = short_history_bars(graph_lookback_bars(program, atr_trailing_stop(cfg)))
            if len(df) < need and self._short_window_warned != state.graph_hash:
                self._short_window_warned = state.graph_hash
                self._log(
                    "WARN",
                    f"Graph wants {need} bars of history but the fetch returned {len(df)} "
                    f"({cfg.interval}, {cfg.data_source}); the live signal may differ "
                    f"from the backtest.",
                )

            # F6: graph compares against @volume become silent zeros if the
            # provider didn't return a Volume column. Warn so the operator
            # notices instead of trading on bogus data.
            if 'Volume' not in df.columns and program.reads_attr("@volume"):
                self._log(
                    "WARN",
                    f"Graph references @volume but DataFrame lacks Volume column "
                    f"(provider={cfg.data_source}); zero-filling — comparisons against "
                    f"@volume will be False.",
                )
            # Key Bugs Fixed: never block the polling loop.  The whole cook
            # (indicator work and evaluation) runs in the executor.
            try:
                indicators, sigs = await self._run_in_executor(
                    cook_graph_bar, live.program, df, atr_trailing_stop(cfg), live.plan, frames,
                    cfg.interval,
                )
            except Exception as e:
                # Retry this bar on the next tick instead of skipping it (and
                # its exit checks) as "same bar".
                state.last_bar_time = prev_bar_time
                # Every cook failure counts (F435 W2 LT-1): raise, so run()'s
                # consecutive-error counter sees it and alerts (create_task)
                # at MAX_CONSEC_ERRORS.  Returning quietly here hid a cook
                # that failed on every tick, with exits and stops unmanaged.
                held = " (open position, stops not managed)" if state.entry_price is not None else ""
                raise GraphCookError(f"Graph cook failed: {e}{held}") from e
            buy_signal = sigs['entry']
            sell_signal = sigs['exit']
            # A wired Size or Stop at this bar, for any entry this tick.
            self._graph_sample = sigs.get("sample")
        else:
            try:
                vol = df["Volume"] if "Volume" in df.columns else None
                indicators = await self._run_in_executor(
                    lambda: compute_indicators(df["Close"], high=df["High"], low=df["Low"],
                                               volume=vol, rules=all_rules)
                )
            except Exception as e:
                self._log("WARN", f"Indicator error: {e}")
                return

        # 4b. Regime evaluation — determine entry direction for this tick
        entry_dir = cfg.direction  # default for non-regime bots
        if is_regime:
            if live is not None:
                # A graph bot's regime is its Regime terminal's column at
                # this bar (cooked above, in the executor).
                entry_dir = graph_entry_direction(live.plan, sigs, cfg.direction)
            else:
                try:
                    entry_dir = await self._eval_regime_direction(cfg, df)
                except Exception as e:
                    self._log("WARN", f"Regime eval failed, gate closed: {e}")
                    entry_dir = "flat"
            state.regime_direction = entry_dir
        on_flip = cfg.regime.on_flip if (is_regime and cfg.regime is not None) else "hold"

        entry_is_short = entry_dir == "short"

        # 5. Check broker for existing position (source of truth).  A bot
        # that can hold either side adopts whichever side the broker holds
        # when it tracks none (LM-2); a resumed position under a wired Stop
        # keeps its own stop or waits for a bar with one (LM-6).
        wired_stop = live is not None and live.plan.stop is not None
        try:
            has_position, broker_qty = await self._broker_position(
                cfg, state, price, is_regime, wired_stop)
        except Exception as e:
            self._log("WARN", f"Position check failed: {e}")
            return
        self._take_pending_stop(state, has_position)

        # 5b. Partial-position reconciliation: detect external shrinkage
        if has_position and state.entry_price is not None and not state.pending_close_order_id:
            if self._last_broker_qty is not None and broker_qty < self._last_broker_qty:
                delta = self._last_broker_qty - broker_qty
                self._log(
                    "WARN",
                    f"External: position reduced {self._last_broker_qty} → {broker_qty} ({delta} shares)",
                )
        if has_position:
            self._last_broker_qty = broker_qty

        # A wired Stop terminal: the open trade keeps the stop it was entered
        # with (state.entry_stop_pct), as the backtest does.
        if live is not None and live.plan.stop is not None:
            cfg = cfg.model_copy(update={"stop_loss_pct": state.entry_stop_pct})

        # 5c. Regime: handle pending flip retry (position not cleared last tick)
        if is_regime and state.pending_regime_flip:
            if has_position:
                # Still open — retry close
                self._log("INFO", "Retrying pending regime flip close")
                await self._handle_regime_flip(cfg, state, entry_dir, price, broker_qty, in_hours, indicators, i)
                return
            else:
                # Position cleared between ticks — clean up state, optionally enter new direction
                self._log("INFO", "Pending regime flip resolved — position cleared between ticks")
                state.pending_regime_flip = False
                state.entry_price = None
                state.entry_bar_count = 0
                state.trail_peak = None
                state.trail_stop_price = None
                state.entry_stop_pct = None
                state.position_direction = None
                state.pending_close_order_id = None
                state.pending_close_reason = None
                self._last_broker_qty = None
                has_position = False
                await asyncio.to_thread(self.manager.save)
                # If close_and_reverse and new direction is valid: enter, then return
                if on_flip == "close_and_reverse" and entry_dir not in ("flat", None) and in_hours:
                    if state.skip_remaining > 0:
                        state.skip_remaining -= 1
                        self._log("INFO", f"Skipping regime re-entry (post-stop cooldown, {state.skip_remaining} left)")
                    else:
                        await self._enter_position(cfg, state, entry_dir, price, indicators, i)
                return

        # 5d. Regime: detect direction flip while positioned (new flip this tick)
        if is_regime and has_position and state.position_direction is not None:
            if entry_dir != state.position_direction and on_flip != "hold":
                await self._handle_regime_flip(cfg, state, entry_dir, price, broker_qty, in_hours, indicators, i)
                return
            # on_flip == "hold" or no flip: fall through to normal exit checks

        # ---------------------------------------------------------------
        # 6. No position → evaluate buy rules
        # ---------------------------------------------------------------
        # Use state.position_direction as direction reference for externally-closed detection
        pos_is_short = state.position_direction == "short" if state.position_direction else cfg.direction == "short"

        if not has_position:
            # Detect externally-closed position (e.g. broker SL fill)
            if state.entry_price is not None:
                should_return = await self._detect_external_close(
                    cfg, state, has_position, pos_is_short, broker_qty, price, df, i, in_hours, indicators
                )
                if should_return:
                    return

            state.entry_price = None
            state.entry_bar_count = 0
            state.trail_peak = None
            state.trail_stop_price = None
            state.entry_stop_pct = None
            state.pending_close_order_id = None
            state.pending_close_reason = None
            state.position_direction = None
            self._last_broker_qty = None

            if not in_hours:
                self._log("INFO", "Outside trading hours — skipping entry")
                return

            # Regime gate: skip entry if regime says flat
            if is_regime and entry_dir == "flat":
                return

            # Select buy rules based on direction (dual rule sets for regime bots)
            # Graph mode: the group's columns were cooked in section 4; skip eval_rules.
            if cfg.kind == "graph":
                buy_signal = graph_buy_signal(live.plan, sigs, entry_dir)
            elif is_regime and entry_is_short and cfg.short_buy_rules:
                active_buy_rules = [migrate_rule(r) for r in cfg.short_buy_rules]
                active_buy_logic = cfg.short_buy_logic
                buy_signal = await self._run_in_executor(
                    eval_rules, active_buy_rules, active_buy_logic, indicators, i
                )
            elif is_regime and not entry_is_short and cfg.long_buy_rules:
                active_buy_rules = [migrate_rule(r) for r in cfg.long_buy_rules]
                active_buy_logic = cfg.long_buy_logic
                buy_signal = await self._run_in_executor(
                    eval_rules, active_buy_rules, active_buy_logic, indicators, i
                )
            else:
                active_buy_rules = buy_rules
                active_buy_logic = cfg.buy_logic
                buy_signal = await self._run_in_executor(
                    eval_rules, active_buy_rules, active_buy_logic, indicators, i
                )

            if buy_signal:
                # A wired Size or Stop with no value blocks the entry before
                # the post-stop skip is counted (the backtest's order).
                blocked = self._entry_blocked()
                if blocked is not None:
                    self._log("INFO", f"Skipping entry: the graph's {blocked} has no value on this bar")
                    return
                if state.skip_remaining > 0:
                    state.skip_remaining -= 1
                    self._log("INFO", f"Skipping entry (post-stop cooldown, {state.skip_remaining} left)")
                    return
                # Safety: skip entry if opposite-direction position exists.
                # A bot that can hold either side owns every position on its
                # symbol, so it refuses any (LM-2): entering next to one
                # stacked a second short on a short it was not tracking.
                try:
                    provider = get_trading_provider(cfg.broker)
                    _positions = await self._run_in_executor(provider.get_positions)
                    for _pos in _positions:
                        if _pos["symbol"] == cfg.symbol.upper():
                            if cfg.is_bidirectional:
                                self._log("WARN", f"Skipping entry — a {_pos['side']} position on "
                                                  f"{cfg.symbol.upper()} exists")
                                return
                            if _pos["side"] != entry_dir:
                                self._log("WARN", f"Skipping entry — opposite position ({_pos['side']}) exists")
                                return
                            break
                except Exception as e:
                    self._log("WARN", f"Skipping entry — position check failed: {e}")
                    return

                # Spread gate: skip entries when bid/ask spread exceeds the configured cap.
                if cfg.max_spread_bps is not None and cfg.max_spread_bps > 0:
                    try:
                        provider = get_trading_provider(cfg.broker)
                        bid, ask = await self._run_in_executor(provider.get_latest_quote, cfg.symbol.upper())
                        if bid > 0 and ask > 0 and ask >= bid:
                            mid = (bid + ask) / 2
                            spread_bps = (ask - bid) / mid * 10000
                            if spread_bps > cfg.max_spread_bps:
                                self._log("INFO", f"Skipping entry — spread {spread_bps:.1f}bps > cap {cfg.max_spread_bps:.1f}bps (bid={bid:.4f}, ask={ask:.4f})")
                                return
                        else:
                            self._log("WARN", f"Skipping entry — invalid quote (bid={bid}, ask={ask})")
                            return
                    except Exception as e:
                        self._log("WARN", f"Spread check failed ({e}) — skipping entry to stay conservative")
                        return

                await self._enter_position(cfg, state, entry_dir, price, indicators, i)

        # ---------------------------------------------------------------
        # 7. Has position → evaluate exits
        # ---------------------------------------------------------------
        else:
            # Re-derive pos_is_short from actual position direction
            pos_is_short = state.position_direction == "short" if state.position_direction else cfg.direction == "short"

            if cfg.kind == "graph":
                # Graph mode: sell_signal already evaluated in section 4.
                # _evaluate_exit_reason handles stop-loss/trailing/time-stop;
                # pass empty sell_rules so it won't call eval_rules (which would
                # return False for an empty list), then override the signal exit
                # with the pre-computed sell_signal from the graph evaluator.
                # A bar whose price exits already ran while a reference was
                # down (LM-1) only gets its signal exit now: running them
                # again would count the bar twice toward the time stop.
                if self._exits_bar == last_bar:
                    exit_reason = None
                else:
                    exit_reason = await self._evaluate_exit_reason(
                        cfg, state, price, df, i, pos_is_short, indicators, [], is_regime
                    )
                sell_signal = graph_sell_signal(
                    live.plan, sigs, state.position_direction or cfg.direction)
                if exit_reason is None and sell_signal:
                    exit_reason = "signal"
            else:
                exit_reason = await self._evaluate_exit_reason(
                    cfg, state, price, df, i, pos_is_short, indicators, sell_rules, is_regime
                )

            if exit_reason:
                await self._execute_exit(
                    cfg, state, exit_reason, price, broker_qty, pos_is_short, df, i, in_hours, indicators
                )

    def _on_ibkr_error(self, reqId, errorCode, errorString, is_structural):
        """Called by IBKRTradingProvider on async IBKR errors.

        Connection-level errors (reqId <= 0) affect all bots.
        Order-specific errors (reqId > 0) only affect the bot that placed them.
        """
        # Filter: order-specific errors only matter if this bot placed the order
        if reqId > 0 and str(reqId) not in self._active_order_ids:
            return
        if is_structural:
            self._log("ERROR", f"IBKR reject code={errorCode}: {errorString}")
            self.state.status = "error"
            self.state.pause_reason = f"IBKR reject: {errorString} (code {errorCode})"
            self.state.error_message = self.state.pause_reason
            self.manager.save()
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(notify_error(
                    symbol=self.config.symbol,
                    error_msg=self.state.pause_reason,
                    bot_id=self.config.bot_id,
                ), self._loop)
        else:
            self._log("WARN", f"IBKR transient code={errorCode}: {errorString}")

    def _register_error_listener(self):
        """Subscribe to IBKR error events if this bot uses the ibkr broker."""
        if self.config.broker != "ibkr":
            return
        provider = get_trading_provider(self.config.broker)
        if hasattr(provider, "add_error_listener"):
            self._error_listener = self._on_ibkr_error
            provider.add_error_listener(self._error_listener)

    def _unregister_error_listener(self):
        if self._error_listener is None:
            return
        try:
            provider = get_trading_provider(self.config.broker)
            if hasattr(provider, "remove_error_listener"):
                provider.remove_error_listener(self._error_listener)
        except Exception:
            pass
        self._error_listener = None

    async def run(self):
        self._loop = asyncio.get_running_loop()
        self.state.status = "running"
        self.state.was_running = False
        self.state.user_stopped = False
        self.state.pause_reason = None
        self.state.error_message = None
        self.state.started_at = datetime.now(timezone.utc).isoformat()
        self._log("INFO", f"Bot started: {self.config.symbol} {self.config.interval}")

        if _POLL_MS > 0:
            interval_secs = _POLL_MS / 1000.0
        else:
            interval_secs = POLL_INTERVALS.get(self.config.interval, 30)
        consec_errors = 0
        MAX_CONSEC_ERRORS = 5
        RECOVERY_WAIT = 30  # seconds to wait before retrying after transient failures
        try:
            # Inside the try (F445): with IBKR not registered yet (Gateway still
            # logging in after a reboot) this raises, and the task used to die
            # with status "running", invisible to bot_watch.
            self._register_error_listener()
            await asyncio.to_thread(self.manager.save)
            while True:
                # Check if paused by IBKR structural error — permanent stop
                if self.state.status == "error" and self.state.pause_reason:
                    self._log("WARN", f"Bot paused: {self.state.pause_reason}")
                    break
                try:
                    await self._tick()
                    consec_errors = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    consec_errors += 1
                    self._log("WARN", f"Tick failed ({consec_errors}/{MAX_CONSEC_ERRORS}): {e}")
                    await asyncio.to_thread(self.manager.save)
                    if consec_errors >= MAX_CONSEC_ERRORS:
                        # Transient failures: backoff and retry instead of dying
                        self._log("WARN", f"Backing off {RECOVERY_WAIT}s after {MAX_CONSEC_ERRORS} consecutive failures")
                        self.state.error_message = f"Recovering: {e}"
                        await asyncio.to_thread(self.manager.save)
                        asyncio.create_task(notify_error(
                            symbol=self.config.symbol,
                            error_msg=f"{MAX_CONSEC_ERRORS} consecutive tick failures: {e}",
                            bot_id=self.config.bot_id,
                        ))
                        await asyncio.sleep(RECOVERY_WAIT)
                        consec_errors = 0
                        self._log("INFO", "Resuming after recovery backoff")
                        self.state.error_message = None
                        self.state.status = "running"
                        await asyncio.to_thread(self.manager.save)
                        continue
                await asyncio.sleep(interval_secs)
        except Exception as e:
            # Keep the reason on the card and in the bot_watch alert.
            self.state.error_message = f"Runner stopped: {e}"
            self._log("WARN", f"Runner stopped by an unexpected error: {e}")
        finally:
            self._unregister_error_listener()
            self.state.status = "stopped"
            self.state.started_at = None
            await asyncio.to_thread(self.manager.save)

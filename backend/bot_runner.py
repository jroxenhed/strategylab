"""
bot_runner.py — Async polling loop for a single live trading bot.

Classes:
  BotRunner — Fetches bars, evaluates signals, and places/manages orders.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone

from slippage import slippage_cost_bps, fill_bias_bps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot_manager import BotConfig, BotState, BotManager

from signal_engine import compute_indicators, eval_rules, migrate_rule
from shared import _fetch
from broker import get_trading_provider, OrderRequest as BrokerOrderRequest, OrderResult
from journal import _log_trade, compute_realized_pnl, compute_bidirectional_pnl
from post_loss import is_post_loss_trigger
from notifications import notify_entry, notify_exit, notify_error
from regime import RegimeMixin
from exits import ExitsMixin

# Poll interval per bar cadence (seconds)
POLL_INTERVALS = {"1m": 10, "5m": 15, "15m": 20, "30m": 30, "1h": 60}


class BotRunner(RegimeMixin, ExitsMixin):
    def __init__(self, config: BotConfig, state: BotState, manager: BotManager):
        self.config = config
        self.state = state
        self.manager = manager
        self._error_listener = None  # bound IBKR error callback
        self._active_order_ids: set[str] = set()  # order IDs placed by this bot
        self._last_broker_qty: int | None = None  # for partial-position reconciliation
        self._loop: asyncio.AbstractEventLoop | None = None  # set in run() for thread-safe scheduling

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
        """Compute realized P&L using bidirectional helper for regime bots."""
        is_regime = bool(cfg.regime and cfg.regime.enabled)
        if is_regime:
            return compute_bidirectional_pnl(cfg.symbol, cfg.bot_id, since=cfg.pnl_epoch)
        return compute_realized_pnl(cfg.symbol, cfg.direction, bot_id=cfg.bot_id, since=cfg.pnl_epoch)

    async def _enter_position(self, cfg, state, direction: str, price: float, indicators: dict, i: int):
        """Submit entry order in the given direction and update state."""
        entry_is_short = direction == "short"

        # Compute effective capital (bidirectional for regime bots)
        current_capital = cfg.allocated_capital + self._bot_pnl(cfg, state)
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
            provider = get_trading_provider(cfg.broker)
            if entry_is_short:
                order_req = BrokerOrderRequest(symbol=cfg.symbol.upper(), qty=qty, side="sell")
            elif cfg.stop_loss_pct and not cfg.trailing_stop:
                stop_price = round(price * (1 - cfg.stop_loss_pct / 100), 2)
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
        state.entry_bar_count = 0
        state.trail_peak = fill_price
        state.position_direction = direction
        self._last_broker_qty = qty
        state.trades_count += 1
        side_label = "SHORT" if entry_is_short else "BUY"
        state.last_signal = side_label
        side_key = "short" if entry_is_short else "buy"
        cost_bps = slippage_cost_bps(side_key, expected=price, fill=fill_price)
        bias_bps = fill_bias_bps(side_key, expected=price, fill=fill_price)
        state.slippage_bps.append(round(cost_bps, 2))
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

        self.manager.save()

    async def _tick(self):
        cfg = self.config
        state = self.state
        is_regime = bool(cfg.regime and cfg.regime.enabled)

        buy_rules = [migrate_rule(r) for r in cfg.buy_rules]
        sell_rules = [migrate_rule(r) for r in cfg.sell_rules]
        all_rules = buy_rules + sell_rules
        # Include dual rule sets so their indicators are computed
        for extra in (cfg.long_buy_rules, cfg.long_sell_rules, cfg.short_buy_rules, cfg.short_sell_rules):
            if extra:
                all_rules = all_rules + [migrate_rule(r) for r in extra]
        loop = asyncio.get_event_loop()

        state.last_tick = datetime.now(timezone.utc).isoformat()
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
            df = await self._run_in_executor(
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
            vol = df["Volume"] if "Volume" in df.columns else None
            indicators = await self._run_in_executor(
                lambda: compute_indicators(df["Close"], high=df["High"], low=df["Low"],
                                           volume=vol, rules=all_rules)
            )
        except Exception as e:
            self._log("WARN", f"Indicator error: {e}")
            return

        i = len(df) - 1
        price = float(df["Close"].iloc[-1])
        state.last_price = price

        # 4b. Regime evaluation — determine entry direction for this tick
        entry_dir = cfg.direction  # default for non-regime bots
        if is_regime:
            try:
                entry_dir = await self._eval_regime_direction(cfg, df)
            except Exception as e:
                self._log("WARN", f"Regime eval failed, gate closed: {e}")
                entry_dir = "flat"
            state.regime_direction = entry_dir

        entry_is_short = entry_dir == "short"

        # 5. Check broker for existing position (source of truth)
        # For regime bots, match against current tracked position direction (if known)
        check_dir = state.position_direction if (is_regime and state.position_direction) else cfg.direction
        has_position = False
        broker_qty = 0
        try:
            provider = get_trading_provider(cfg.broker)
            positions = await self._run_in_executor(provider.get_positions)
            for pos in positions:
                if pos["symbol"] == cfg.symbol.upper():
                    if pos["side"] != check_dir:
                        continue
                    has_position = True
                    broker_qty = abs(pos["qty"])
                    if state.entry_price is None:
                        state.entry_price = pos["avg_entry"]
                        state.trail_peak = price
                        if is_regime and not state.position_direction:
                            state.position_direction = pos["side"]
                        self._log("INFO", f"Resumed tracking position: entry={state.entry_price:.2f}")
                    break
        except Exception as e:
            self._log("WARN", f"Position check failed: {e}")
            return

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
                state.position_direction = None
                state.pending_close_order_id = None
                state.pending_close_reason = None
                self._last_broker_qty = None
                has_position = False
                self.manager.save()
                # If close_and_reverse and new direction is valid: enter, then return
                if cfg.regime.on_flip == "close_and_reverse" and entry_dir not in ("flat", None) and in_hours:
                    if state.skip_remaining > 0:
                        state.skip_remaining -= 1
                        self._log("INFO", f"Skipping regime re-entry (post-stop cooldown, {state.skip_remaining} left)")
                    else:
                        await self._enter_position(cfg, state, entry_dir, price, indicators, i)
                return

        # 5d. Regime: detect direction flip while positioned (new flip this tick)
        if is_regime and has_position and state.position_direction is not None:
            if entry_dir != state.position_direction and cfg.regime.on_flip != "hold":
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
            if is_regime and entry_is_short and cfg.short_buy_rules:
                active_buy_rules = [migrate_rule(r) for r in cfg.short_buy_rules]
                active_buy_logic = cfg.short_buy_logic
            elif is_regime and not entry_is_short and cfg.long_buy_rules:
                active_buy_rules = [migrate_rule(r) for r in cfg.long_buy_rules]
                active_buy_logic = cfg.long_buy_logic
            else:
                active_buy_rules = buy_rules
                active_buy_logic = cfg.buy_logic

            buy_signal = await self._run_in_executor(
                eval_rules, active_buy_rules, active_buy_logic, indicators, i
            )

            if buy_signal:
                if state.skip_remaining > 0:
                    state.skip_remaining -= 1
                    self._log("INFO", f"Skipping entry (post-stop cooldown, {state.skip_remaining} left)")
                    return
                # Safety: skip entry if opposite-direction position exists
                try:
                    provider = get_trading_provider(cfg.broker)
                    _positions = await self._run_in_executor(provider.get_positions)
                    for _pos in _positions:
                        if _pos["symbol"] == cfg.symbol.upper():
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
        self.state.pause_reason = None
        self.state.started_at = datetime.now(timezone.utc).isoformat()
        self._log("INFO", f"Bot started: {self.config.symbol} {self.config.interval}")
        self._register_error_listener()
        self.manager.save()

        interval_secs = POLL_INTERVALS.get(self.config.interval, 30)
        consec_errors = 0
        MAX_CONSEC_ERRORS = 5
        RECOVERY_WAIT = 30  # seconds to wait before retrying after transient failures
        try:
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
                    self.manager.save()
                    if consec_errors >= MAX_CONSEC_ERRORS:
                        # Transient failures: backoff and retry instead of dying
                        self._log("WARN", f"Backing off {RECOVERY_WAIT}s after {MAX_CONSEC_ERRORS} consecutive failures")
                        self.state.error_message = f"Recovering: {e}"
                        self.manager.save()
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
                        self.manager.save()
                        continue
                await asyncio.sleep(interval_secs)
        finally:
            self._unregister_error_listener()
            self.state.status = "stopped"
            self.state.started_at = None
            self.manager.save()

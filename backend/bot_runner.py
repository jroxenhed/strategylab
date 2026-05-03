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

# Poll interval per bar cadence (seconds)
POLL_INTERVALS = {"1m": 10, "5m": 15, "15m": 20, "30m": 30, "1h": 60}


class BotRunner:
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

    async def _eval_regime_direction(self, cfg, df) -> str:
        """Evaluate current regime direction. Returns 'long', 'short', or 'flat'.
        Conservative default: 'flat' on any error.
        """
        from indicators import compute_instance, OHLCVSeries as _OHLCVSeries
        from shared import fetch_higher_tf, align_htf_to_ltf, htf_lookback_days

        rc = cfg.regime
        from datetime import date, timedelta
        end_date = date.today().isoformat()
        lookback = htf_lookback_days(rc.indicator, rc.indicator_params)
        htf_start = (date.today() - timedelta(days=lookback)).isoformat()

        try:
            htf_df = await asyncio.wait_for(
                self._run_in_executor(fetch_higher_tf, cfg.symbol, htf_start, end_date, rc.timeframe, cfg.data_source),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            self._log("WARN", "Regime: HTF fetch timed out after 15s, gate closed")
            return "flat"
        if htf_df is None or htf_df.empty:
            self._log("WARN", f"Regime: no HTF data for {rc.timeframe}, gate closed")
            return "flat"

        vol_col = htf_df["Volume"] if "Volume" in htf_df.columns else htf_df["Close"] * 0
        ohlcv = _OHLCVSeries(
            close=htf_df["Close"], high=htf_df["High"], low=htf_df["Low"], volume=vol_col
        )
        result = compute_instance(rc.indicator, rc.indicator_params, ohlcv)
        indicator_key = next(iter(result))
        indicator_series = result[indicator_key]

        close = htf_df["Close"]
        if rc.condition == "above":
            raw_bool = close > indicator_series
        elif rc.condition == "below":
            raw_bool = close < indicator_series
        elif rc.condition == "rising":
            raw_bool = indicator_series.diff() > 0
        elif rc.condition == "falling":
            raw_bool = indicator_series.diff() < 0
        else:
            self._log("WARN", f"Unsupported regime condition: {rc.condition!r}")
            return "flat"

        raw_bool = raw_bool.fillna(False)

        if rc.min_bars > 1:
            smoothed = raw_bool.astype(int).rolling(rc.min_bars, min_periods=rc.min_bars).min().fillna(0).astype(bool)
        else:
            smoothed = raw_bool.astype(bool)

        from shared import align_htf_to_ltf
        aligned = align_htf_to_ltf(smoothed.astype(float), df.index)
        aligned = aligned.fillna(0).astype(bool)
        regime_active = bool(aligned.iloc[-1]) if not aligned.empty else False

        has_dual = bool(cfg.long_buy_rules and cfg.short_buy_rules)
        if regime_active:
            return "long"
        elif has_dual:
            return "short"
        else:
            return "flat"

    async def _handle_regime_flip(self, cfg, state, new_dir: str, price: float,
                                   broker_qty: int, in_hours: bool, indicators: dict, i: int):
        """Close current position for a regime flip. Optionally enters the new direction."""
        old_dir = state.position_direction
        pos_is_short_now = old_dir == "short"
        on_flip = cfg.regime.on_flip

        self._log("INFO", f"Regime flip: {old_dir} → {new_dir} ({on_flip})")

        try:
            provider = get_trading_provider(cfg.broker)

            # Cancel pending stop orders for this symbol
            try:
                orders = await self._run_in_executor(
                    provider.get_orders, "open", [cfg.symbol.upper()], 50
                )
                cancel_side = "buy" if pos_is_short_now else "sell"
                for o in orders:
                    if o["side"] == cancel_side:
                        await self._run_in_executor(provider.cancel_order, o["id"])
                        self._log("INFO", f"Cancelled pending {o['type']} order {o['id']}")
            except Exception as e:
                self._log("WARN", f"Cancel orders failed: {e}")

            close_result = await self._run_in_executor(provider.close_position, cfg.symbol.upper())
        except Exception as e:
            self._log("ERROR", f"Regime flip close failed: {e}")
            state.pending_regime_flip = True
            self.manager.save()
            return

        order_id = close_result.order_id
        self._active_order_ids.add(order_id)
        state.pending_close_order_id = order_id
        state.pending_close_reason = "regime_flip"
        self.manager.save()

        sell_fill = await self._get_fill_price_provider(provider, order_id, price) if order_id else price

        # Wait for position to actually clear (up to 3s)
        still_open = True
        for _ in range(6):
            try:
                post_positions = await self._run_in_executor(provider.get_positions)
                still_open = any(
                    p["symbol"] == cfg.symbol.upper() and p["side"] == old_dir
                    for p in post_positions
                )
            except Exception as e:
                self._log("WARN", f"Regime flip post-close check failed: {e}")
                state.pending_regime_flip = True
                self.manager.save()
                return
            if not still_open:
                break
            await asyncio.sleep(0.5)

        if still_open:
            self._log("ERROR", f"Regime flip: position not cleared after 3s — setting pending_regime_flip")
            state.pending_regime_flip = True
            self.manager.save()
            return

        # Close confirmed — log and update state
        pnl = (state.entry_price - sell_fill) * broker_qty if pos_is_short_now else (sell_fill - state.entry_price) * broker_qty
        exit_label = "COVER" if pos_is_short_now else "SELL"
        side_key = "cover" if pos_is_short_now else "sell"
        cost_bps = slippage_cost_bps(side_key, expected=price, fill=sell_fill)
        bias_bps = fill_bias_bps(side_key, expected=price, fill=sell_fill)
        state.slippage_bps.append(round(cost_bps, 2))
        state.last_signal = f"{exit_label} (regime_flip)"

        state.consec_sl_count = 0

        self._log(
            "TRADE",
            f"{exit_label} {cfg.symbol} @ {sell_fill:.2f} | PnL={pnl:+.2f} | reason=regime_flip "
            f"(expected={price:.2f}, cost={cost_bps:.1f}bps, bias={bias_bps:+.1f}bps)",
        )

        try:
            _log_trade(cfg.symbol, "cover" if pos_is_short_now else "sell", broker_qty, sell_fill,
                       source="bot", reason="regime_flip", expected_price=price,
                       direction=old_dir, bot_id=cfg.bot_id, broker=cfg.broker)
        except Exception as e:
            self._log("ERROR", f"Journal write failed: {e}")

        asyncio.create_task(notify_exit(
            symbol=cfg.symbol,
            direction=old_dir,
            qty=broker_qty,
            price=sell_fill,
            pnl=pnl,
            reason="regime_flip",
            bot_id=cfg.bot_id,
        ))

        state.equity_snapshots.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "value": round(self._bot_pnl(cfg, state), 2),
        })
        if len(state.equity_snapshots) > 500:
            state.equity_snapshots = state.equity_snapshots[-500:]

        # Clear position state
        state.entry_price = None
        state.entry_bar_count = 0
        state.trail_peak = None
        state.trail_stop_price = None
        state.pending_close_order_id = None
        state.pending_close_reason = None
        state.position_direction = None
        state.pending_regime_flip = False
        self._last_broker_qty = None
        self._active_order_ids.clear()
        self.manager.save()

        # If close_and_reverse and new direction is not flat: immediately enter
        if on_flip == "close_and_reverse" and new_dir not in ("flat", None) and in_hours:
            if state.skip_remaining > 0:
                state.skip_remaining -= 1
                self._log("INFO", f"Skipping regime re-entry (post-stop cooldown, {state.skip_remaining} left)")
            else:
                await self._enter_position(cfg, state, new_dir, price, indicators, i)

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
                exit_price = price  # fallback to current price
                exit_qty = 0
                exit_reason = "external"
                try:
                    provider = get_trading_provider(cfg.broker)
                    filled_orders = await self._run_in_executor(
                        provider.get_orders, "closed", [cfg.symbol.upper()], 5
                    )
                    close_side = "buy" if pos_is_short else "sell"
                    for o in filled_orders:
                        if o["side"] == close_side and o.get("filled_avg_price"):
                            exit_price = float(o["filled_avg_price"])
                            exit_qty = float(o.get("qty", 0))
                            order_type = o.get("type", "")
                            # If this fill matches the bot's own pending close, recover real reason
                            if state.pending_close_order_id and o.get("id") == state.pending_close_order_id and state.pending_close_reason:
                                exit_reason = state.pending_close_reason
                            elif order_type in ("stop", "stop_limit"):
                                exit_reason = "stop_loss"
                            elif order_type == "trailing_stop":
                                exit_reason = "trailing_stop"
                            else:
                                exit_reason = "external"
                            break
                except Exception as e:
                    self._log("WARN", f"Failed to query filled orders: {e}")

                sell_qty = exit_qty or broker_qty or 0
                if pos_is_short:
                    pnl = (state.entry_price - exit_price) * sell_qty if sell_qty else 0
                else:
                    pnl = (exit_price - state.entry_price) * sell_qty if sell_qty else 0

                ds_trigger = cfg.dynamic_sizing.trigger if cfg.dynamic_sizing else "sl"
                if is_post_loss_trigger(exit_reason, ds_trigger):
                    state.consec_sl_count += 1
                else:
                    state.consec_sl_count = 0

                if cfg.skip_after_stop and cfg.skip_after_stop.enabled and \
                        is_post_loss_trigger(exit_reason, cfg.skip_after_stop.trigger):
                    state.skip_remaining = cfg.skip_after_stop.count

                side_label = "COVER" if pos_is_short else "SELL"
                self._log("TRADE", f"{side_label} {cfg.symbol} @ {exit_price:.2f} | PnL={pnl:+.2f} | reason={exit_reason} (detected)")

                expected_exit: float | None = None
                if exit_reason == "stop_loss" and cfg.stop_loss_pct and state.entry_price:
                    sl_mult = (1 + cfg.stop_loss_pct / 100) if pos_is_short else (1 - cfg.stop_loss_pct / 100)
                    expected_exit = state.entry_price * sl_mult
                elif exit_reason == "trailing_stop" and state.trail_stop_price:
                    expected_exit = state.trail_stop_price

                logged_dir = state.position_direction or cfg.direction
                try:
                    _log_trade(cfg.symbol, "cover" if pos_is_short else "sell", sell_qty, exit_price,
                               source="bot", reason=exit_reason, expected_price=expected_exit,
                               direction=logged_dir, bot_id=cfg.bot_id, broker=cfg.broker)
                except Exception as e:
                    self._log("ERROR", f"Journal write failed: {e}")

                asyncio.create_task(notify_exit(
                    symbol=cfg.symbol,
                    direction=logged_dir,
                    qty=sell_qty,
                    price=exit_price,
                    pnl=pnl,
                    reason=exit_reason,
                    bot_id=cfg.bot_id,
                ))

                state.equity_snapshots.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "value": round(self._bot_pnl(cfg, state), 2),
                })
                if len(state.equity_snapshots) > 500:
                    state.equity_snapshots = state.equity_snapshots[-500:]

                if cfg.drawdown_threshold_pct and state.equity_snapshots:
                    snaps = [s["value"] for s in state.equity_snapshots]
                    peak_pnl = max(snaps)
                    current_pnl = snaps[-1]
                    drawdown_pct = (peak_pnl - current_pnl) / cfg.allocated_capital * 100
                    if drawdown_pct >= cfg.drawdown_threshold_pct:
                        state.status = "error"
                        state.pause_reason = f"Auto-paused: drawdown {drawdown_pct:.1f}% exceeded threshold {cfg.drawdown_threshold_pct:.1f}%"
                        self._log("WARN", state.pause_reason)
                        asyncio.create_task(notify_error(
                            symbol=cfg.symbol,
                            error_msg=state.pause_reason,
                            bot_id=cfg.bot_id,
                        ))
                        state.entry_price = None
                        state.entry_bar_count = 0
                        state.trail_peak = None
                        state.trail_stop_price = None
                        state.pending_close_order_id = None
                        state.pending_close_reason = None
                        state.position_direction = None
                        self._last_broker_qty = None
                        self.manager.save()
                        return

                self.manager.save()

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

            state.entry_bar_count += 1
            exit_reason = None

            # Update trailing peak/trough
            if cfg.trailing_stop and state.entry_price is not None:
                ts = cfg.trailing_stop
                if pos_is_short:
                    source_price = float(df["Low"].iloc[-1]) if ts.source == "high" else price
                    activated = (not ts.activate_on_profit) or (
                        source_price <= state.entry_price * (1 - ts.activate_pct / 100)
                    )
                    if activated:
                        if state.trail_peak is None or source_price < state.trail_peak:
                            state.trail_peak = source_price

                        # Compute trail stop price (above trough for shorts)
                        atr_val = float(indicators.get("atr", {}).get(i, 0) or 0)
                        if ts.type == "pct":
                            state.trail_stop_price = state.trail_peak * (1 + ts.value / 100)
                        elif ts.type == "atr" and atr_val:
                            state.trail_stop_price = state.trail_peak + ts.value * atr_val
                else:
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
            if pos_is_short:
                if cfg.stop_loss_pct and state.entry_price:
                    if price >= state.entry_price * (1 + cfg.stop_loss_pct / 100):
                        exit_reason = "stop_loss"
                if exit_reason is None and cfg.trailing_stop and state.trail_stop_price:
                    if price >= state.trail_stop_price:
                        exit_reason = "trailing_stop"
            else:
                if cfg.stop_loss_pct and state.entry_price and not cfg.trailing_stop:
                    if price <= state.entry_price * (1 - cfg.stop_loss_pct / 100):
                        exit_reason = "stop_loss"
                if exit_reason is None and cfg.trailing_stop and state.trail_stop_price:
                    if price <= state.trail_stop_price:
                        exit_reason = "trailing_stop"

            if exit_reason is None and cfg.max_bars_held and state.entry_bar_count >= cfg.max_bars_held:
                exit_reason = "time_stop"

            if exit_reason is None:
                # Select sell rules based on position direction (dual rule sets for regime bots)
                if is_regime and pos_is_short and cfg.short_sell_rules:
                    active_sell_rules = [migrate_rule(r) for r in cfg.short_sell_rules]
                    active_sell_logic = cfg.short_sell_logic
                elif is_regime and not pos_is_short and cfg.long_sell_rules:
                    active_sell_rules = [migrate_rule(r) for r in cfg.long_sell_rules]
                    active_sell_logic = cfg.long_sell_logic
                else:
                    active_sell_rules = sell_rules
                    active_sell_logic = cfg.sell_logic

                sell_signal = await self._run_in_executor(
                    eval_rules, active_sell_rules, active_sell_logic, indicators, i
                )
                if sell_signal:
                    exit_reason = "signal"

            if exit_reason:
                try:
                    provider = get_trading_provider(cfg.broker)
                    # Safety: verify position side matches tracked direction
                    pos_dir_ref = state.position_direction or cfg.direction
                    try:
                        positions = await self._run_in_executor(provider.get_positions)
                        pos_match = False
                        for pos in positions:
                            if pos["symbol"] == cfg.symbol.upper():
                                if pos["side"] == pos_dir_ref:
                                    pos_match = True
                                break
                        if not pos_match:
                            self._log("WARN", f"Position side mismatch — clearing stale state")
                            state.entry_price = None
                            state.entry_bar_count = 0
                            state.trail_peak = None
                            state.trail_stop_price = None
                            state.position_direction = None
                            self._last_broker_qty = None
                            self.manager.save()
                            return
                    except Exception as e:
                        self._log("WARN", f"Position verify failed: {e}")
                        return

                    # Cancel pending stop-loss orders for this symbol first
                    try:
                        orders = await self._run_in_executor(
                            provider.get_orders, "open", [cfg.symbol.upper()], 50
                        )
                        cancel_side = "buy" if pos_is_short else "sell"
                        for o in orders:
                            if o["side"] == cancel_side:
                                await self._run_in_executor(provider.cancel_order, o["id"])
                                self._log("INFO", f"Cancelled pending {o['type']} order {o['id']}")
                    except Exception as e:
                        self._log("WARN", f"Cancel orders failed: {e}")

                    close_result = await self._run_in_executor(provider.close_position, cfg.symbol.upper())
                except Exception as e:
                    self._log("ERROR", f"Close position failed: {e}")
                    return

                # Get actual fill price
                order_id = close_result.order_id
                self._active_order_ids.add(order_id)
                state.pending_close_order_id = order_id
                state.pending_close_reason = exit_reason
                self.manager.save()

                sell_fill = await self._get_fill_price_provider(provider, order_id, price) if order_id else price

                # Verify the position actually closed before clearing state or journaling.
                pos_dir_ref = state.position_direction or cfg.direction
                still_open = True
                for _ in range(6):
                    try:
                        post_positions = await self._run_in_executor(provider.get_positions)
                        still_open = any(
                            p["symbol"] == cfg.symbol.upper() and p["side"] == pos_dir_ref
                            for p in post_positions
                        )
                    except Exception as e:
                        self._log("WARN", f"Post-close position check failed: {e}")
                        return
                    if not still_open:
                        break
                    await asyncio.sleep(0.5)
                if still_open:
                    self._log("ERROR", f"Close order {order_id} did not reduce position after 3s — leaving state intact")
                    return

                if pos_is_short:
                    pnl = (state.entry_price - sell_fill) * broker_qty if state.entry_price else 0
                else:
                    pnl = (sell_fill - state.entry_price) * broker_qty if state.entry_price else 0
                exit_label = "COVER" if pos_is_short else "SELL"
                state.last_signal = f"{exit_label} ({exit_reason})"

                # Update dynamic sizing counter + skip-after-stop
                ds_trigger = cfg.dynamic_sizing.trigger if cfg.dynamic_sizing else "sl"
                if is_post_loss_trigger(exit_reason, ds_trigger):
                    state.consec_sl_count += 1
                else:
                    state.consec_sl_count = 0

                if cfg.skip_after_stop and cfg.skip_after_stop.enabled and \
                        is_post_loss_trigger(exit_reason, cfg.skip_after_stop.trigger):
                    state.skip_remaining = cfg.skip_after_stop.count

                side_key = "cover" if pos_is_short else "sell"
                cost_bps = slippage_cost_bps(side_key, expected=price, fill=sell_fill)
                bias_bps = fill_bias_bps(side_key, expected=price, fill=sell_fill)
                state.slippage_bps.append(round(cost_bps, 2))
                self._log(
                    "TRADE",
                    f"{exit_label} {cfg.symbol} @ {sell_fill:.2f} | PnL={pnl:+.2f} | reason={exit_reason} "
                    f"(expected={price:.2f}, cost={cost_bps:.1f}bps, bias={bias_bps:+.1f}bps)",
                )

                logged_dir = state.position_direction or cfg.direction
                try:
                    _log_trade(cfg.symbol, "cover" if pos_is_short else "sell", broker_qty, sell_fill,
                               source="bot", reason=exit_reason, expected_price=price,
                               direction=logged_dir, bot_id=cfg.bot_id, broker=cfg.broker)
                except Exception as e:
                    self._log("ERROR", f"Journal write failed: {e}")

                asyncio.create_task(notify_exit(
                    symbol=cfg.symbol,
                    direction=logged_dir,
                    qty=broker_qty,
                    price=sell_fill,
                    pnl=pnl,
                    reason=exit_reason,
                    bot_id=cfg.bot_id,
                ))

                state.equity_snapshots.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "value": round(self._bot_pnl(cfg, state), 2),
                })
                if len(state.equity_snapshots) > 500:
                    state.equity_snapshots = state.equity_snapshots[-500:]

                if cfg.drawdown_threshold_pct and state.equity_snapshots:
                    snaps = [s["value"] for s in state.equity_snapshots]
                    peak_pnl = max(snaps)
                    current_pnl = snaps[-1]
                    drawdown_pct = (peak_pnl - current_pnl) / cfg.allocated_capital * 100
                    if drawdown_pct >= cfg.drawdown_threshold_pct:
                        state.status = "error"
                        state.pause_reason = f"Auto-paused: drawdown {drawdown_pct:.1f}% exceeded threshold {cfg.drawdown_threshold_pct:.1f}%"
                        self._log("WARN", state.pause_reason)
                        asyncio.create_task(notify_error(
                            symbol=cfg.symbol,
                            error_msg=state.pause_reason,
                            bot_id=cfg.bot_id,
                        ))
                        state.entry_price = None
                        state.entry_bar_count = 0
                        state.trail_peak = None
                        state.trail_stop_price = None
                        state.pending_close_order_id = None
                        state.pending_close_reason = None
                        state.position_direction = None
                        self._last_broker_qty = None
                        self._active_order_ids.clear()
                        self.manager.save()
                        return

                state.entry_price = None
                state.entry_bar_count = 0
                state.trail_peak = None
                state.trail_stop_price = None
                state.pending_close_order_id = None
                state.pending_close_reason = None
                state.position_direction = None
                self._last_broker_qty = None
                self._active_order_ids.clear()
                self.manager.save()

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

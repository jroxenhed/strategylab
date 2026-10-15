"""Exit logic for BotRunner: external close detection, exit reason evaluation,
and exit execution. Extracted from bot_runner.py as a mixin.

NOTE: Functions that are test-patched via patch("bot_runner.<name>") are accessed
through sys.modules['bot_runner'] at call time so patches take effect. This is
required because unit tests patch module-level names in bot_runner, not in exits.
"""
from __future__ import annotations

import asyncio
import math
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import pandas as pd

if TYPE_CHECKING:
    from bot_manager import BotConfig, BotState

from slippage import slippage_cost_bps, fill_bias_bps
from post_loss import is_post_loss_trigger
from signal_engine import migrate_rule


def _br():
    """Return the bot_runner module (looked up at call time so test patches apply)."""
    return sys.modules["bot_runner"]


def _compute_borrow_cost(pos_is_short: bool, entry_price: Optional[float], entry_time: Optional[str], borrow_rate_annual: float, broker_qty: float) -> float:
    """Compute short borrow cost: rate × notional × hold_days. Returns 0.0 when not applicable."""
    if not pos_is_short or not entry_price or not entry_time:
        return 0.0
    try:
        entry_dt = datetime.fromisoformat(entry_time)
        hold_days = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 86400
        return round(broker_qty * entry_price * (borrow_rate_annual / 100 / 365) * hold_days, 2)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Borrow cost calc skipped: {e}")
        return 0.0


def _atr_at(indicators: dict, i: int) -> float:
    """ATR on bar i, or 0.0 when it is missing or still warming up.

    Read by position (iloc).  Series.get(i) on a date index looks i up as a
    label, finds nothing and returned the default 0, so the live ATR trailing
    stop was never set.
    """
    series = indicators.get("atr") if indicators else None
    if series is None:
        return 0.0
    try:
        value = series.iloc[i] if hasattr(series, "iloc") else series[i]
        value = float(value)
    except (IndexError, KeyError, TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _parse_time(value) -> Optional[datetime]:
    """An ISO time string (or datetime) as an aware UTC datetime, or None
    when it is missing or cannot be read.  A time with no zone is UTC."""
    if not value:
        return None
    try:
        t = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


# A closing fill may carry a time a few seconds before the bot's entry_time
# (the bot stamps entry_time after it has waited for the entry fill, and the
# two clocks differ a little).  An earlier trade's close is at least a bar
# older, so this slack never lets one through.
_FILL_TIME_SLACK_SECS = 5

# _detect_external_close looks the closing fill up itself unless one is passed.
_LOOK_UP = object()


class ExitsMixin:

    async def _closing_fill(self, cfg: BotConfig, state: BotState, pos_is_short: bool) -> Optional[dict]:
        """The broker fill that closed the tracked position, or None.

        Only a fill that belongs to this position counts (F449 LM-3): the
        bot's own close order (pending_close_order_id), or a closing-side
        fill whose filled_at is not before the position's entry_time.  An
        older fill from an earlier trade, or a fill with no time (IBKR
        lists none), never counts.  Raises when the broker cannot be asked."""
        br = _br()
        provider = br.get_trading_provider(cfg.broker)
        filled_orders = await self._run_in_executor(
            provider.get_orders, "closed", [cfg.symbol.upper()], 5
        )
        close_side = "buy" if pos_is_short else "sell"
        entry_at = _parse_time(getattr(state, "entry_time", None))
        for o in filled_orders:
            if o.get("side") != close_side or not o.get("filled_avg_price"):
                continue
            if state.pending_close_order_id and o.get("id") == state.pending_close_order_id:
                return o
            filled_at = _parse_time(o.get("filled_at"))
            if entry_at is None or filled_at is None:
                continue
            if (filled_at - entry_at).total_seconds() >= -_FILL_TIME_SLACK_SECS:
                return o
        return None

    async def _position_gone_confirmed(self, cfg: BotConfig, state: BotState,
                                       pos_is_short: bool) -> tuple[bool, Optional[dict]]:
        """The broker's position list lacks a position the bot tracks.  Is
        it really gone?  (F449 LM-3.)  One empty or odd answer must never
        book a P&L row or drop the position.  It is gone when:
          - a fill that belongs to it is found (_closing_fill), or
          - two position checks in a row, each with other positions in the
            account (so not an empty reply), both lack it.
        Returns (confirmed, fill).  fill is None when no fill was found."""
        try:
            fill = await self._closing_fill(cfg, state, pos_is_short)
        except Exception as e:
            self._log("WARN", f"Failed to query filled orders: {e}")
            fill = None
        if fill is not None:
            self._missing_checks = 0
            self._gone_unconfirmed = False
            return True, fill
        if not self._last_account_empty:
            self._missing_checks += 1
        if self._missing_checks >= 2:
            self._missing_checks = 0
            self._gone_unconfirmed = False
            return True, None
        self._gone_unconfirmed = True
        self._log("WARN", "The broker shows no position, but nothing confirms the close yet "
                          "(no closing fill found); the position is kept and checked again")
        return False, None

    async def _detect_external_close(self, cfg: BotConfig, state: BotState, has_position: bool, pos_is_short: bool,
                                      broker_qty: float, price: float, df: pd.DataFrame, i: int, in_hours: bool, indicators: dict,
                                      *, fill=_LOOK_UP) -> bool:
        """Detect externally-closed position (e.g. broker SL fill).

        Called when not has_position but state.entry_price is not None, and
        only after _position_gone_confirmed said yes.  fill is the closing
        fill it found (None: none found); left out, it is looked up here.
        Only a fill that belongs to this position is used (_closing_fill).
        Logs, journals, and optionally pauses bot on drawdown breach.

        Returns True if _tick() should return early (drawdown breach), False otherwise.
        """
        br = _br()
        exit_price = price  # fallback to current price
        exit_qty = 0
        exit_reason = "external"
        if fill is _LOOK_UP:
            try:
                fill = await self._closing_fill(cfg, state, pos_is_short)
            except Exception as e:
                self._log("WARN", f"Failed to query filled orders: {e}")
                fill = None
        if fill is not None:
            o = fill
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
        # B25: resolve per-direction stop_loss_pct for expected_price calculation
        _sl_pct_ext = (
            (cfg.long_stop_loss_pct if state.position_direction == 'long' else cfg.short_stop_loss_pct)
            if state.position_direction and hasattr(cfg, 'long_stop_loss_pct') else None
        ) or cfg.stop_loss_pct
        if exit_reason == "stop_loss" and _sl_pct_ext and state.entry_price:
            sl_mult = (1 + _sl_pct_ext / 100) if pos_is_short else (1 - _sl_pct_ext / 100)
            expected_exit = state.entry_price * sl_mult
        elif exit_reason == "trailing_stop" and state.trail_stop_price:
            expected_exit = state.trail_stop_price

        logged_dir = state.position_direction or cfg.direction
        borrow_cost = _compute_borrow_cost(
            pos_is_short, state.entry_price, getattr(state, 'entry_time', None),
            cfg.borrow_rate_annual, sell_qty
        )
        try:
            br._log_trade(cfg.symbol, "cover" if pos_is_short else "sell", sell_qty, exit_price,
                          source="bot", reason=exit_reason, expected_price=expected_exit,
                          direction=logged_dir, bot_id=cfg.bot_id, broker=cfg.broker,
                          borrow_cost=borrow_cost if pos_is_short else None)
        except Exception as e:
            self._log("ERROR", f"Journal write failed: {e}")
            state.error_message = f"Journal write failed: {e}"
            asyncio.create_task(br.notify_error(
                symbol=cfg.symbol,
                error_msg=f"Journal write failed: {e}",
                bot_id=cfg.bot_id,
            ))

        asyncio.create_task(br.notify_exit(
            symbol=cfg.symbol,
            direction=logged_dir,
            qty=sell_qty,
            price=exit_price,
            pnl=pnl,
            reason=exit_reason,
            bot_id=cfg.bot_id,
        ))

        state.append_equity_snapshot(self._bot_pnl(cfg, state))

        if cfg.drawdown_threshold_pct and state.equity_snapshots:
            snaps = [s["value"] for s in state.equity_snapshots]
            peak_pnl = max(snaps)
            current_pnl = snaps[-1]
            drawdown_pct = (peak_pnl - current_pnl) / cfg.allocated_capital * 100
            if drawdown_pct >= cfg.drawdown_threshold_pct:
                state.status = "error"
                state.pause_reason = f"Auto-paused: drawdown {drawdown_pct:.1f}% exceeded threshold {cfg.drawdown_threshold_pct:.1f}%"
                self._log("WARN", state.pause_reason)
                asyncio.create_task(br.notify_error(
                    symbol=cfg.symbol,
                    error_msg=state.pause_reason,
                    bot_id=cfg.bot_id,
                ))
                state.entry_price = None
                state.entry_time = None
                state.entry_bar_count = 0
                state.trail_peak = None
                state.trail_stop_price = None
                state.pending_close_order_id = None
                state.pending_close_reason = None
                state.position_direction = None
                self._last_broker_qty = None
                self.manager.save()
                return True

        self.manager.save()
        return False

    async def _evaluate_exit_reason(self, cfg: BotConfig, state: BotState, price: float, df: pd.DataFrame, i: int, pos_is_short: bool,
                                     indicators: dict, sell_rules: list, is_regime: bool, *,
                                     price_exits: bool = True,
                                     signal_exits: bool = True) -> Optional[str]:
        """Update trailing stop state and evaluate exit reason.

        Increments entry_bar_count, updates trail_peak/trail_stop_price,
        and checks all exit conditions in priority order.

        price_exits=False skips the bar count, the trailing update and the
        stop, trailing stop and time stop checks (they already ran on this
        bar, F449).  signal_exits=False skips the sell rules (the broker
        position check failed, so only price exits run, F449).

        Returns exit_reason string, or None if no exit.
        """
        br = _br()
        exit_reason = None
        if price_exits:
            exit_reason = self._price_exit_reason(cfg, state, price, df, i, pos_is_short, indicators)

        if exit_reason is None and signal_exits:
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
                br.eval_rules, active_sell_rules, active_sell_logic, indicators, i
            )
            if sell_signal:
                exit_reason = "signal"

        return exit_reason

    def _price_exit_reason(self, cfg: BotConfig, state: BotState, price: float, df: pd.DataFrame, i: int,
                           pos_is_short: bool, indicators: dict) -> Optional[str]:
        """Count the bar, update the trailing stop, and check the stop,
        trailing stop and time stop.  Returns the reason or None."""
        state.entry_bar_count += 1
        exit_reason = None

        # B25: resolve per-direction stop/trailing/mbh values
        _pos_dir = state.position_direction
        _has_dir_fields = hasattr(cfg, 'long_stop_loss_pct')
        if _pos_dir and _has_dir_fields:
            sl_pct = (
                (cfg.long_stop_loss_pct if _pos_dir == 'long' else cfg.short_stop_loss_pct)
                or cfg.stop_loss_pct
            )
            ts = (
                (cfg.long_trailing_stop if _pos_dir == 'long' else cfg.short_trailing_stop)
                or cfg.trailing_stop
            )
            mbh = (
                (cfg.long_max_bars_held if _pos_dir == 'long' else cfg.short_max_bars_held)
                or cfg.max_bars_held
            )
        else:
            sl_pct = cfg.stop_loss_pct
            ts = cfg.trailing_stop
            mbh = cfg.max_bars_held

        # Update trailing peak/trough the way the backtest does
        # (routes/backtest.py): the peak starts at the entry price, only the
        # peak update waits for activate_on_profit, and the trail stop is
        # computed from the peak on every bar.  Live used to set no trail at
        # all before activation, which left a long with no stop.
        if ts and state.entry_price is not None:
            if state.trail_peak is None:
                state.trail_peak = state.entry_price
            atr_val = _atr_at(indicators, i)
            if pos_is_short:
                source_price = float(df["Low"].iloc[-1]) if ts.source == "high" else price
                activated = (not ts.activate_on_profit) or (
                    source_price <= state.entry_price * (1 - ts.activate_pct / 100)
                )
                if activated and source_price < state.trail_peak:
                    state.trail_peak = source_price
                # Trail stop sits above the trough for shorts
                if ts.type == "pct":
                    state.trail_stop_price = state.trail_peak * (1 + ts.value / 100)
                elif ts.type == "atr" and atr_val:
                    state.trail_stop_price = state.trail_peak + ts.value * atr_val
            else:
                source_price = float(df["High"].iloc[-1]) if ts.source == "high" else price
                activated = (not ts.activate_on_profit) or (
                    source_price >= state.entry_price * (1 + ts.activate_pct / 100)
                )
                if activated and source_price > state.trail_peak:
                    state.trail_peak = source_price
                if ts.type == "pct":
                    state.trail_stop_price = state.trail_peak * (1 - ts.value / 100)
                elif ts.type == "atr" and atr_val:
                    state.trail_stop_price = state.trail_peak - ts.value * atr_val

        # Check exits in priority order (B25: use resolved sl_pct, ts, mbh)
        if pos_is_short:
            if sl_pct and state.entry_price:
                if price >= state.entry_price * (1 + sl_pct / 100):
                    exit_reason = "stop_loss"
            if exit_reason is None and ts and state.trail_stop_price:
                if price >= state.trail_stop_price:
                    exit_reason = "trailing_stop"
        else:
            # The fixed stop applies with or without a trailing stop, as in the
            # backtest.  It used to be skipped whenever a trailing stop was set,
            # and the broker stop leg is only sent without one, so a long with
            # both had no fixed stop at all.
            if sl_pct and state.entry_price:
                if price <= state.entry_price * (1 - sl_pct / 100):
                    exit_reason = "stop_loss"
            if exit_reason is None and ts and state.trail_stop_price:
                if price <= state.trail_stop_price:
                    exit_reason = "trailing_stop"

        if exit_reason is None and mbh and state.entry_bar_count >= mbh:
            exit_reason = "time_stop"

        return exit_reason

    async def _execute_exit(self, cfg: BotConfig, state: BotState, exit_reason: str, price: float, broker_qty: float,
                             pos_is_short: bool, df: pd.DataFrame, i: int, in_hours: bool, indicators: dict) -> bool:
        """Execute exit order, verify close, journal, and clear position state.

        Returns True if exit was executed (including early-return paths).
        """
        br = _br()
        # What happened, for the caller (F449): "closed" (state cleared),
        # "sent" (a close went out but is not confirmed yet), "close_failed"
        # (the broker refused the close) or "not_sent" (the position could
        # not be confirmed, so no order went out; safe to retry).
        self._exit_outcome = "not_sent"
        try:
            provider = br.get_trading_provider(cfg.broker)
            # Safety: verify position side matches tracked direction
            pos_dir_ref = state.position_direction or cfg.direction
            try:
                positions = await self._run_in_executor(provider.get_positions)
                pos_match = False
                symbol_held = False
                for pos in positions:
                    if pos["symbol"] == cfg.symbol.upper():
                        symbol_held = True
                        if pos["side"] == pos_dir_ref:
                            pos_match = True
                            # Journal the qty the broker just confirmed (F449
                            # LM-4): the caller's qty can be stale, and the
                            # close below closes the broker's whole position.
                            broker_qty = abs(pos["qty"])
                        break
                if not symbol_held:
                    # The broker lists no position on the symbol.  That may
                    # be a broker stop that filled, or an empty or odd
                    # reply.  One reply is not proof (F449 LM-3): send no
                    # order, book nothing, keep the position.  The next
                    # position check confirms it (_position_gone_confirmed)
                    # before anything is booked.
                    self._log("WARN", "Position not found at the broker when exiting; "
                                      "kept until a position check confirms it is gone")
                    return True
                if not pos_match:
                    self._log("WARN", f"Position side mismatch — clearing stale state")
                    self._exit_outcome = "closed"
                    state.entry_price = None
                    state.entry_time = None
                    state.entry_bar_count = 0
                    state.trail_peak = None
                    state.trail_stop_price = None
                    state.position_direction = None
                    self._last_broker_qty = None
                    self.manager.save()
                    return True
            except Exception as e:
                self._log("WARN", f"Position verify failed: {e}")
                return True

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
            self._exit_outcome = "close_failed"
            return True

        # Get actual fill price
        order_id = close_result.order_id
        self._active_order_ids.add(order_id)
        state.pending_close_order_id = order_id
        state.pending_close_reason = exit_reason
        self._exit_outcome = "sent"
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
                return True
            if not still_open:
                break
            await asyncio.sleep(0.5)
        if still_open:
            self._log("ERROR", f"Close order {order_id} did not reduce position after 3s — leaving state intact")
            return True

        if pos_is_short:
            pnl = (state.entry_price - sell_fill) * broker_qty if state.entry_price else 0
        else:
            pnl = (sell_fill - state.entry_price) * broker_qty if state.entry_price else 0

        # Borrow cost for short positions: rate × notional × hold_days
        borrow_cost: float = _compute_borrow_cost(
            pos_is_short, state.entry_price, getattr(state, 'entry_time', None),
            cfg.borrow_rate_annual, broker_qty
        )

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
        state.append_slippage_bps(cost_bps)
        self._log(
            "TRADE",
            f"{exit_label} {cfg.symbol} @ {sell_fill:.2f} | PnL={pnl:+.2f} | reason={exit_reason} "
            f"(expected={price:.2f}, cost={cost_bps:.1f}bps, bias={bias_bps:+.1f}bps)",
        )

        logged_dir = state.position_direction or cfg.direction
        try:
            br._log_trade(cfg.symbol, "cover" if pos_is_short else "sell", broker_qty, sell_fill,
                          source="bot", reason=exit_reason, expected_price=price,
                          direction=logged_dir, bot_id=cfg.bot_id, broker=cfg.broker,
                          borrow_cost=borrow_cost if pos_is_short else None)
        except Exception as e:
            self._log("ERROR", f"Journal write failed: {e}")
            state.error_message = f"Journal write failed: {e}"
            asyncio.create_task(br.notify_error(
                symbol=cfg.symbol,
                error_msg=f"Journal write failed: {e}",
                bot_id=cfg.bot_id,
            ))

        asyncio.create_task(br.notify_exit(
            symbol=cfg.symbol,
            direction=logged_dir,
            qty=broker_qty,
            price=sell_fill,
            pnl=pnl,
            reason=exit_reason,
            bot_id=cfg.bot_id,
        ))

        state.append_equity_snapshot(self._bot_pnl(cfg, state))

        if cfg.drawdown_threshold_pct and state.equity_snapshots:
            snaps = [s["value"] for s in state.equity_snapshots]
            peak_pnl = max(snaps)
            current_pnl = snaps[-1]
            drawdown_pct = (peak_pnl - current_pnl) / cfg.allocated_capital * 100
            if drawdown_pct >= cfg.drawdown_threshold_pct:
                state.status = "error"
                state.pause_reason = f"Auto-paused: drawdown {drawdown_pct:.1f}% exceeded threshold {cfg.drawdown_threshold_pct:.1f}%"
                self._log("WARN", state.pause_reason)
                asyncio.create_task(br.notify_error(
                    symbol=cfg.symbol,
                    error_msg=state.pause_reason,
                    bot_id=cfg.bot_id,
                ))
                state.entry_price = None
                state.entry_time = None
                state.entry_bar_count = 0
                state.trail_peak = None
                state.trail_stop_price = None
                state.pending_close_order_id = None
                state.pending_close_reason = None
                state.position_direction = None
                self._last_broker_qty = None
                self._active_order_ids.clear()
                self._exit_outcome = "closed"
                self.manager.save()
                return True

        state.entry_price = None
        state.entry_time = None
        state.entry_bar_count = 0
        state.trail_peak = None
        state.trail_stop_price = None
        state.pending_close_order_id = None
        state.pending_close_reason = None
        state.position_direction = None
        self._last_broker_qty = None
        self._active_order_ids.clear()
        self._exit_outcome = "closed"
        self.manager.save()
        return True

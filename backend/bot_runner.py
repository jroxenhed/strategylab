"""
bot_runner.py — Async polling loop for a single live trading bot.

Classes:
  BotRunner — Fetches bars, evaluates signals, and places/manages orders.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot_manager import BotConfig, BotState, BotManager

from signal_engine import compute_indicators, eval_rules
from shared import _fetch
from broker import get_trading_provider, OrderRequest as BrokerOrderRequest, OrderResult
from journal import _log_trade, compute_realized_pnl
from post_loss import is_post_loss_trigger

# Poll interval per bar cadence (seconds)
POLL_INTERVALS = {"1m": 10, "5m": 15, "15m": 20, "30m": 30, "1h": 60}


class BotRunner:
    def __init__(self, config: BotConfig, state: BotState, manager: BotManager):
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

    async def _tick(self):
        cfg = self.config
        state = self.state
        is_short = cfg.direction == "short"
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
            indicators = await self._run_in_executor(
                compute_indicators, df["Close"], df["High"], df["Low"]
            )
        except Exception as e:
            self._log("WARN", f"Indicator error: {e}")
            return

        i = len(df) - 1
        price = float(df["Close"].iloc[-1])
        state.last_price = price

        # 5. Check broker for existing position (source of truth)
        has_position = False
        broker_qty = 0
        try:
            provider = get_trading_provider(cfg.broker)
            positions = await self._run_in_executor(provider.get_positions)
            for pos in positions:
                if pos["symbol"] == cfg.symbol.upper():
                    if pos["side"] != cfg.direction:
                        continue
                    has_position = True
                    broker_qty = pos["qty"]
                    if state.entry_price is None:
                        state.entry_price = pos["avg_entry"]
                        state.trail_peak = price
                        self._log("INFO", f"Resumed tracking position: entry={state.entry_price:.2f}")
                    break
        except Exception as e:
            self._log("WARN", f"Position check failed: {e}")
            return

        # ---------------------------------------------------------------
        # 6. No position → evaluate buy rules
        # ---------------------------------------------------------------
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
                    close_side = "buy" if is_short else "sell"
                    for o in filled_orders:
                        if o["side"] == close_side and o.get("filled_avg_price"):
                            exit_price = float(o["filled_avg_price"])
                            exit_qty = float(o.get("qty", 0))
                            order_type = o.get("type", "")
                            if order_type in ("stop", "stop_limit"):
                                exit_reason = "stop_loss"
                            elif order_type == "trailing_stop":
                                exit_reason = "trailing_stop"
                            else:
                                exit_reason = "external"
                            break
                except Exception as e:
                    self._log("WARN", f"Failed to query filled orders: {e}")

                sell_qty = exit_qty or broker_qty or 0
                if is_short:
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

                side_label = "COVER" if is_short else "SELL"
                self._log("TRADE", f"{side_label} {cfg.symbol} @ {exit_price:.2f} | PnL={pnl:+.2f} | reason={exit_reason} (detected)")

                try:
                    _log_trade(cfg.symbol, "cover" if is_short else "sell", sell_qty, exit_price,
                               source="bot", reason=exit_reason, direction=cfg.direction,
                               bot_id=cfg.bot_id, broker=cfg.broker)
                except Exception:
                    pass

                state.equity_snapshots.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "value": round(compute_realized_pnl(cfg.symbol, cfg.direction, bot_id=cfg.bot_id), 2),
                })

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
                if state.skip_remaining > 0:
                    state.skip_remaining -= 1
                    self._log("INFO", f"Skipping entry (post-stop cooldown, {state.skip_remaining} left)")
                    return
                # Safety: skip entry if opposite-direction position exists
                # (prevents long BUY from netting against short bot's position)
                try:
                    provider = get_trading_provider(cfg.broker)
                    _positions = await self._run_in_executor(provider.get_positions)
                    for _pos in _positions:
                        if _pos["symbol"] == cfg.symbol.upper():
                            if _pos["side"] != cfg.direction:
                                self._log("WARN", f"Skipping entry — opposite position ({_pos['side']}) exists")
                                return
                            break
                except Exception:
                    pass  # if check fails, proceed cautiously

                # Compute effective position size (compounds P&L like backtest)
                current_capital = cfg.allocated_capital + compute_realized_pnl(cfg.symbol, cfg.direction, bot_id=cfg.bot_id)
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

                    if is_short:
                        order_req = BrokerOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side="sell",
                        )
                    elif cfg.stop_loss_pct and not cfg.trailing_stop:
                        # OTO bracket: provider handles if supported (Alpaca), else plain market
                        stop_price = round(price * (1 - cfg.stop_loss_pct / 100), 2)
                        order_req = BrokerOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side="buy",
                            order_type="stop",
                            stop_price=stop_price,
                        )
                    else:
                        order_req = BrokerOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side="buy",
                        )

                    result = await self._run_in_executor(provider.submit_order, order_req)
                except Exception as e:
                    self._log("ERROR", f"Buy order failed: {e}")
                    return

                # Get actual fill price
                fill_price = await self._get_fill_price_provider(provider, result.order_id, price)

                state.entry_price = fill_price
                state.trail_peak = fill_price
                state.trades_count += 1
                side_label = "SHORT" if is_short else "BUY"
                state.last_signal = side_label
                if is_short:
                    slippage = price - fill_price  # lower fill is worse for short seller
                else:
                    slippage = fill_price - price  # higher fill is worse for buyer
                slippage_pct = (slippage / price) * 100 if price else 0
                state.slippage_pcts.append(round(slippage_pct, 4))
                self._log("TRADE", f"{side_label} {qty} {cfg.symbol} @ {fill_price:.2f} (expected={price:.2f}, slippage={slippage_pct:+.4f}%)")

                # Log to trade journal
                try:
                    _log_trade(cfg.symbol, "short" if is_short else "buy", qty, fill_price,
                               source="bot", reason="entry", expected_price=price,
                               direction=cfg.direction, bot_id=cfg.bot_id, broker=cfg.broker)
                except Exception:
                    pass

                self.manager.save()

        # ---------------------------------------------------------------
        # 7. Has position → evaluate exits
        # ---------------------------------------------------------------
        else:
            exit_reason = None

            # Update trailing peak/trough
            if cfg.trailing_stop and state.entry_price is not None:
                ts = cfg.trailing_stop
                if is_short:
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
            if is_short:
                if cfg.stop_loss_pct and state.entry_price:
                    if price >= state.entry_price * (1 + cfg.stop_loss_pct / 100):
                        exit_reason = "stop_loss"
                if exit_reason is None and cfg.trailing_stop and state.trail_stop_price:
                    if price >= state.trail_stop_price:
                        exit_reason = "trailing_stop"
            else:
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
                    provider = get_trading_provider(cfg.broker)
                    # Safety: verify position side matches bot direction
                    try:
                        positions = await self._run_in_executor(provider.get_positions)
                        pos_match = False
                        for pos in positions:
                            if pos["symbol"] == cfg.symbol.upper():
                                if pos["side"] == cfg.direction:
                                    pos_match = True
                                break
                        if not pos_match:
                            self._log("WARN", f"Position side mismatch — clearing stale state")
                            state.entry_price = None
                            state.trail_peak = None
                            state.trail_stop_price = None
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
                        cancel_side = "buy" if is_short else "sell"
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
                sell_fill = await self._get_fill_price_provider(provider, order_id, price) if order_id else price

                if is_short:
                    pnl = (state.entry_price - sell_fill) * broker_qty if state.entry_price else 0
                else:
                    pnl = (sell_fill - state.entry_price) * broker_qty if state.entry_price else 0
                exit_label = "COVER" if is_short else "SELL"
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

                if is_short:
                    slippage = sell_fill - price  # higher cover fill is worse
                else:
                    slippage = sell_fill - price
                slippage_pct = (slippage / price) * 100 if price else 0
                state.slippage_pcts.append(round(slippage_pct, 4))
                self._log("TRADE", f"{exit_label} {cfg.symbol} @ {sell_fill:.2f} | PnL={pnl:+.2f} | reason={exit_reason} (expected={price:.2f}, slippage={slippage_pct:+.4f}%)")

                try:
                    _log_trade(cfg.symbol, "cover" if is_short else "sell", broker_qty, sell_fill,
                               source="bot", reason=exit_reason, expected_price=price,
                               direction=cfg.direction, bot_id=cfg.bot_id, broker=cfg.broker)
                except Exception:
                    pass

                state.equity_snapshots.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "value": round(compute_realized_pnl(cfg.symbol, cfg.direction, bot_id=cfg.bot_id), 2),
                })

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

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
from shared import _fetch, get_trading_client, is_retryable_error
from journal import _log_trade, compute_realized_pnl

# Alpaca order helpers (imported lazily to avoid hard dep if Alpaca not set up)
try:
    from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, OrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, OrderType
    _ALPACA_AVAILABLE = True
except ImportError:
    _ALPACA_AVAILABLE = False

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

    async def _run_with_retry(self, fn, *args):
        """Run in executor, retrying once on stale connection errors."""
        try:
            return await self._run_in_executor(fn, *args)
        except Exception as e:
            if is_retryable_error(e):
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
                    # Only claim positions whose side matches this bot's direction
                    pos_side = getattr(pos.side, 'value', str(pos.side)).lower()
                    if pos_side != cfg.direction:
                        continue
                    has_position = True
                    alpaca_qty = abs(float(pos.qty))
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
                    close_side = OrderSide.BUY if is_short else OrderSide.SELL
                    for o in filled_orders:
                        if o.side == close_side and o.filled_avg_price is not None:
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
                if is_short:
                    pnl = (state.entry_price - exit_price) * sell_qty if sell_qty else 0
                else:
                    pnl = (exit_price - state.entry_price) * sell_qty if sell_qty else 0

                if exit_reason in ("stop_loss", "trailing_stop"):
                    state.consec_sl_count += 1
                else:
                    state.consec_sl_count = 0

                side_label = "COVER" if is_short else "SELL"
                self._log("TRADE", f"{side_label} {cfg.symbol} @ {exit_price:.2f} | PnL={pnl:+.2f} | reason={exit_reason} (detected)")

                try:
                    _log_trade(cfg.symbol, "cover" if is_short else "sell", sell_qty, exit_price,
                               source="bot", reason=exit_reason, direction=cfg.direction)
                except Exception:
                    pass

                state.equity_snapshots.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "value": round(compute_realized_pnl(cfg.symbol, cfg.direction), 2),
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
                # Safety: skip entry if opposite-direction position exists on Alpaca
                # (prevents long BUY from netting against short bot's position)
                try:
                    _client = await self._run_in_executor(get_trading_client)
                    _positions = await self._run_with_retry(_client.get_all_positions)
                    for _pos in _positions:
                        if _pos.symbol == cfg.symbol.upper():
                            _ps = getattr(_pos.side, 'value', str(_pos.side)).lower()
                            if _ps != cfg.direction:
                                self._log("WARN", f"Skipping entry — opposite position ({_ps}) exists")
                                return
                            break
                except Exception:
                    pass  # if check fails, proceed cautiously

                # Compute effective position size (compounds P&L like backtest)
                current_capital = cfg.allocated_capital + compute_realized_pnl(cfg.symbol, cfg.direction)
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

                    if is_short:
                        # Short: plain market sell, bot manages all stops via polling
                        order_req = MarketOrderRequest(
                            symbol=cfg.symbol.upper(),
                            qty=qty,
                            side=OrderSide.SELL,
                            time_in_force=TimeInForce.DAY,
                        )
                    elif cfg.trailing_stop and cfg.stop_loss_pct:
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
                               direction=cfg.direction)
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
                    client = await self._run_in_executor(get_trading_client)
                    # Safety: verify Alpaca position side matches bot direction
                    # (prevents long bot from closing short bot's position)
                    try:
                        positions = await self._run_with_retry(client.get_all_positions)
                        pos_match = False
                        for pos in positions:
                            if pos.symbol == cfg.symbol.upper():
                                pos_side = getattr(pos.side, 'value', str(pos.side)).lower()
                                if pos_side == cfg.direction:
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
                    # (OTO bracket legs hold shares, blocking close_position)
                    try:
                        from alpaca.trading.requests import GetOrdersRequest
                        from alpaca.trading.enums import QueryOrderStatus
                        orders = await self._run_with_retry(
                            client.get_orders,
                            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[cfg.symbol.upper()]),
                        )
                        cancel_side = OrderSide.BUY if is_short else OrderSide.SELL
                        for o in orders:
                            if o.side == cancel_side:
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

                if is_short:
                    pnl = (state.entry_price - sell_fill) * alpaca_qty if state.entry_price else 0
                else:
                    pnl = (sell_fill - state.entry_price) * alpaca_qty if state.entry_price else 0
                exit_label = "COVER" if is_short else "SELL"
                state.last_signal = f"{exit_label} ({exit_reason})"

                # Update dynamic sizing counter
                if exit_reason in ("stop_loss", "trailing_stop"):
                    state.consec_sl_count += 1
                else:
                    state.consec_sl_count = 0

                if is_short:
                    slippage = sell_fill - price  # higher cover fill is worse
                else:
                    slippage = sell_fill - price
                slippage_pct = (slippage / price) * 100 if price else 0
                state.slippage_pcts.append(round(slippage_pct, 4))
                self._log("TRADE", f"{exit_label} {cfg.symbol} @ {sell_fill:.2f} | PnL={pnl:+.2f} | reason={exit_reason} (expected={price:.2f}, slippage={slippage_pct:+.4f}%)")

                try:
                    _log_trade(cfg.symbol, "cover" if is_short else "sell", alpaca_qty, sell_fill,
                               source="bot", reason=exit_reason, expected_price=price,
                               direction=cfg.direction)
                except Exception:
                    pass

                state.equity_snapshots.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "value": round(compute_realized_pnl(cfg.symbol, cfg.direction), 2),
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

"""Regime direction evaluation and flip handling for BotRunner.

Extracted from bot_runner.py as a mixin. All methods use self.* state from BotRunner.

NOTE: Functions that are test-patched via patch("bot_runner.<name>") are accessed
through sys.modules['bot_runner'] at call time so patches take effect. This is
required because unit tests patch module-level names in bot_runner, not in regime.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone


def _br():
    """Return the bot_runner module (looked up at call time so test patches apply)."""
    return sys.modules["bot_runner"]


class RegimeMixin:

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
        from slippage import slippage_cost_bps, fill_bias_bps
        from broker import get_trading_provider

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
            _br()._log_trade(cfg.symbol, "cover" if pos_is_short_now else "sell", broker_qty, sell_fill,
                             source="bot", reason="regime_flip", expected_price=price,
                             direction=old_dir, bot_id=cfg.bot_id, broker=cfg.broker)
        except Exception as e:
            self._log("ERROR", f"Journal write failed: {e}")

        asyncio.create_task(_br().notify_exit(
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

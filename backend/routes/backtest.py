from fastapi import APIRouter, HTTPException
import hashlib
import json
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from shared import _fetch, _format_time, _INTRADAY_INTERVALS
from signal_engine import Rule, compute_indicators, eval_rules, eval_rule, migrate_rule, resolve_series
from routes.indicators import _series_to_list
from models import TrailingStopConfig, DynamicSizingConfig, TradingHoursConfig, StrategyRequest
from post_loss import is_post_loss_trigger


def per_leg_commission(shares: float, req) -> float:
    """IBKR Fixed per-share commission with min-per-order floor."""
    return max(shares * req.per_share_rate, req.min_per_order)


def borrow_cost(shares: float, entry_price: float, entry_ts, exit_ts,
                direction: str, req) -> float:
    """Short-borrow cost for the holding period. Zero for longs or rate=0."""
    if direction != "short" or req.borrow_rate_annual <= 0:
        return 0.0
    hold_days = (exit_ts - entry_ts).total_seconds() / 86400
    position_value = shares * entry_price
    return position_value * (req.borrow_rate_annual / 100 / 365) * hold_days


router = APIRouter()

# Single-entry cache for macro endpoint re-aggregation.
# Stores the most recent backtest's raw equity + trades, keyed by request hash.
_backtest_cache: dict = {}


def _request_hash(req) -> str:
    """Deterministic hash of a StrategyRequest for cache keying."""
    d = req.model_dump(exclude={"debug"})
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


def _side_stats(values: list[float]) -> dict:
    """Min/max/mean/median for a list of floats. Empty list → all None."""
    if not values:
        return {"min": None, "max": None, "mean": None, "median": None}
    import statistics
    return {
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "mean": round(sum(values) / len(values), 2),
        "median": round(statistics.median(values), 2),
    }


def _edge_stats(gains: list[float], losses: list[float], num_sells: int) -> dict:
    """Expected value per trade + profit factor.

    gross_profit = sum of winning P&Ls.
    gross_loss   = absolute sum of losing P&Ls (always >= 0).
    ev_per_trade = (gross_profit - gross_loss) / num_sells, or None if num_sells == 0.
    profit_factor = gross_profit / gross_loss, or None if gross_loss == 0 (frontend renders
                    this as ∞ when gross_profit > 0, or — when there are no trades at all).
                    Python cannot serialize float('inf') to JSON, so None is the sentinel.
    """
    gross_profit = round(sum(gains), 2)
    gross_loss = round(abs(sum(losses)), 2)
    ev_per_trade = round((gross_profit - gross_loss) / num_sells, 2) if num_sells > 0 else None
    profit_factor = round(gross_profit / gross_loss, 3) if gross_loss > 0 else None
    return {
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "ev_per_trade": ev_per_trade,
        "profit_factor": profit_factor,
    }


@router.post("/api/backtest")
def run_backtest(req: StrategyRequest):
    try:
        df = _fetch(req.ticker, req.start, req.end, req.interval, source=req.source, extended_hours=req.extended_hours)

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        buy_rules = [migrate_rule(r) for r in req.buy_rules]
        sell_rules = [migrate_rule(r) for r in req.sell_rules]
        all_rules = buy_rules + sell_rules
        indicators = compute_indicators(close, high=high, low=low, rules=all_rules)

        # Simulate
        capital = req.initial_capital
        position = 0.0
        entry_price = 0.0
        entry_ts = None
        trail_peak = 0.0
        trail_stop_price = None
        trades = []
        equity = []

        is_short = req.direction == "short"
        drag = req.slippage_bps / 10_000.0   # bps → fractional
        ts = req.trailing_stop
        atr = indicators.get("atr")
        signal_trace = [] if req.debug else None
        ds = req.dynamic_sizing
        th = req.trading_hours
        consec_sl_count = 0  # track consecutive stop losses for dynamic sizing
        sas = req.skip_after_stop
        skip_remaining = 0  # entries still to skip after a qualifying stop
        is_intraday = req.interval in _INTRADAY_INTERVALS

        def _trace_rules(rules, indicators, i, label):
            """Build per-rule evaluation detail for debug trace."""
            details = []
            for r in rules:
                if r.muted:
                    details.append({"rule": f"{r.indicator} {r.condition} {r.value if r.value is not None else ''}", "muted": True, "result": False})
                    continue
                result = eval_rule(r, indicators, i)
                ind_series = resolve_series(r, indicators) or indicators.get("close")
                v_now = round(float(ind_series.iloc[i]), 4) if ind_series is not None and pd.notna(ind_series.iloc[i]) else None
                v_prev = round(float(ind_series.iloc[i - 1]), 4) if ind_series is not None and i > 0 and pd.notna(ind_series.iloc[i - 1]) else None
                rule_desc = f"{r.indicator} {r.condition} {r.value if r.value is not None else ''}"
                if r.threshold is not None:
                    rule_desc += f" min {r.threshold}%"
                details.append({
                    "rule": rule_desc,
                    "result": bool(result),
                    "v_now": v_now,
                    "v_prev": v_prev,
                })
            return details

        for i in range(len(df)):
            price = close.iloc[i]
            date = _format_time(df.index[i], req.interval)

            # Trading hours filter (only affects entries, not exits)
            hour_ok = True
            if is_intraday and th and th.enabled:
                bar_dt = df.index[i]
                if bar_dt.tzinfo is not None:
                    et_time = bar_dt.astimezone(pd.Timestamp.now(tz="America/New_York").tzinfo)
                else:
                    et_time = pd.Timestamp(bar_dt, tz="UTC").tz_convert("America/New_York")

                et_time_str = et_time.strftime("%H:%M")
                if et_time_str < th.start_time or et_time_str >= th.end_time:
                    hour_ok = False
                for sr in th.skip_ranges:
                    if "-" in sr:
                        parts = sr.split("-", 1)
                        if parts[0].strip() <= et_time_str < parts[1].strip():
                            hour_ok = False
                            break

            buy_fires = position == 0 and hour_ok and eval_rules(buy_rules, req.buy_logic, indicators, i)
            if buy_fires and skip_remaining > 0:
                skip_remaining -= 1
                if signal_trace is not None:
                    signal_trace.append({
                        "date": date, "price": round(price, 4), "position": "flat",
                        "action": f"SKIPPED (post-stop, {skip_remaining} left)",
                    })
                buy_fires = False

            if buy_fires:
                # Dynamic sizing: reduce position after consecutive stop losses
                effective_size = req.position_size
                if ds and ds.enabled and consec_sl_count >= ds.consec_sls:
                    effective_size = req.position_size * (ds.reduced_pct / 100)

                # Slippage: short entry fills lower (worse for seller), long fills higher (worse for buyer)
                if is_short:
                    fill_price = price * (1 - drag)
                else:
                    fill_price = price * (1 + drag)
                shares = (capital * effective_size) / fill_price
                commission = per_leg_commission(shares, req)
                position = shares
                entry_price = fill_price
                entry_ts = df.index[i]
                capital -= shares * fill_price + commission
                trail_peak = fill_price
                trail_stop_price = None
                entry_slippage = abs(shares * (fill_price - price))
                entry_type = "short" if is_short else "buy"
                trades.append({
                    "type": entry_type, "date": date, "price": round(fill_price, 4),
                    "shares": round(shares, 4),
                    "direction": req.direction,
                    "slippage": round(entry_slippage, 2),
                    "commission": round(commission, 2),
                })
                if signal_trace is not None:
                    signal_trace.append({
                        "date": date, "price": round(price, 4), "position": "entered",
                        "action": "SHORT" if is_short else "BUY",
                        "buy_rules": _trace_rules(buy_rules, indicators, i, "buy"),
                    })

            elif position > 0:
                # Update trailing stop peak and compute trail_stop_price
                trail_hit = False
                if ts:
                    if is_short:
                        # Short: track trough (mirror high→low)
                        source_price = low.iloc[i] if ts.source == "high" else price
                        threshold = entry_price * (1 - ts.activate_pct / 100)
                        if not ts.activate_on_profit or source_price <= threshold:
                            trail_peak = min(trail_peak, source_price)
                        if ts.type == "pct":
                            trail_stop_price = trail_peak * (1 + ts.value / 100)
                        else:  # atr
                            atr_val = atr.iloc[i] if atr is not None and not pd.isna(atr.iloc[i]) else 0.0
                            trail_stop_price = trail_peak + ts.value * atr_val
                        trail_hit = high.iloc[i] >= trail_stop_price
                    else:
                        source_price = high.iloc[i] if ts.source == "high" else price
                        threshold = entry_price * (1 + ts.activate_pct / 100)
                        if not ts.activate_on_profit or source_price >= threshold:
                            trail_peak = max(trail_peak, source_price)
                        if ts.type == "pct":
                            trail_stop_price = trail_peak * (1 - ts.value / 100)
                        else:  # atr
                            atr_val = atr.iloc[i] if atr is not None and not pd.isna(atr.iloc[i]) else 0.0
                            trail_stop_price = trail_peak - ts.value * atr_val
                        trail_hit = low.iloc[i] <= trail_stop_price

                # Check fixed stop loss
                if is_short:
                    stop_price_limit = entry_price * (1 + req.stop_loss_pct / 100) if (req.stop_loss_pct and req.stop_loss_pct > 0) else None
                    stop_hit = stop_price_limit is not None and high.iloc[i] >= stop_price_limit
                else:
                    stop_price_limit = entry_price * (1 - req.stop_loss_pct / 100) if (req.stop_loss_pct and req.stop_loss_pct > 0) else None
                    stop_hit = stop_price_limit is not None and low.iloc[i] <= stop_price_limit

                # Exit priority: fixed stop beats trailing stop (it's the harder floor)
                if stop_hit:
                    raw_exit = stop_price_limit
                    exit_reason = "stop_loss"
                elif trail_hit:
                    raw_exit = trail_stop_price
                    exit_reason = "trailing_stop"
                else:
                    raw_exit = price
                    exit_reason = "signal"

                # Slippage: short covers at higher price (worse), long sells at lower price (worse)
                if is_short:
                    exit_price = raw_exit * (1 + drag)
                else:
                    exit_price = raw_exit * (1 - drag)
                sell_fired = eval_rules(sell_rules, req.sell_logic, indicators, i)
                if stop_hit or trail_hit or sell_fired:
                    exit_slippage = abs(position * (raw_exit - exit_price))
                    commission = per_leg_commission(position, req)
                    bcost = borrow_cost(position, entry_price, entry_ts, df.index[i],
                                        req.direction, req)
                    if is_short:
                        pnl = position * (entry_price - exit_price) - commission - bcost
                        capital += position * entry_price + pnl
                    else:
                        proceeds = position * exit_price
                        pnl = (proceeds - commission) - position * entry_price
                        capital += proceeds - commission
                    exit_type = "cover" if is_short else "sell"
                    trades.append({
                        "type": exit_type,
                        "date": date,
                        "price": round(exit_price, 4),
                        "shares": round(position, 4),
                        "direction": req.direction,
                        "pnl": round(pnl, 2),
                        "pnl_pct": round(pnl / (position * entry_price) * 100, 2),
                        "stop_loss": exit_reason == "stop_loss",
                        "trailing_stop": exit_reason == "trailing_stop",
                        "slippage": round(exit_slippage, 2),
                        "commission": round(commission, 2),
                        "borrow_cost": round(bcost, 2),
                    })
                    position = 0.0
                    entry_ts = None
                    trail_peak = 0.0
                    trail_stop_price = None
                    ds_trigger = ds.trigger if ds else "sl"
                    if is_post_loss_trigger(exit_reason, ds_trigger):
                        consec_sl_count += 1
                    else:
                        consec_sl_count = 0

                    if sas and sas.enabled and is_post_loss_trigger(exit_reason, sas.trigger):
                        skip_remaining = sas.count
                    if signal_trace is not None:
                        action = "STOP_LOSS" if exit_reason == "stop_loss" else "TRAIL_STOP" if exit_reason == "trailing_stop" else ("COVER" if is_short else "SELL")
                        signal_trace.append({
                            "date": date, "price": round(price, 4), "position": "exited",
                            "action": action,
                            "sell_rules": _trace_rules(sell_rules, indicators, i, "sell"),
                        })
                elif signal_trace is not None:
                    # In position but no sell — trace any bar where at least one sell rule fires
                    sell_details = _trace_rules(sell_rules, indicators, i, "sell")
                    if any(d["result"] for d in sell_details if not d.get("muted")):
                        signal_trace.append({
                            "date": date, "price": round(price, 4), "position": "holding",
                            "action": "SELL_PARTIAL (AND not met)",
                            "sell_rules": sell_details,
                        })

            elif signal_trace is not None and position == 0:
                # Not in position — trace if sell rules WOULD have fired
                active_sell = [r for r in sell_rules if not r.muted]
                if active_sell and i > 0:
                    sell_details = _trace_rules(sell_rules, indicators, i, "sell")
                    if any(d["result"] for d in sell_details if not d.get("muted")):
                        signal_trace.append({
                            "date": date, "price": round(price, 4), "position": "flat",
                            "action": "MISSED (no position)",
                            "sell_rules": sell_details,
                        })

            if is_short and position > 0:
                unrealized = position * (entry_price - price)
                total_value = capital + position * entry_price + unrealized
            else:
                total_value = capital + (position * price if position > 0 else 0)
            equity.append({"time": date, "value": round(total_value, 2)})

        # Close open position at last price
        final_price = close.iloc[-1]
        final_value = capital + position * final_price

        total_return = (final_value - req.initial_capital) / req.initial_capital * 100
        buy_hold_return = (close.iloc[-1] - close.iloc[0]) / close.iloc[0] * 100

        # Sharpe ratio (annualized, daily returns)
        eq_values = [e["value"] for e in equity]
        eq_series = pd.Series(eq_values)
        daily_returns = eq_series.pct_change().dropna()
        sharpe = float((daily_returns.mean() / daily_returns.std()) * np.sqrt(252)) if daily_returns.std() > 0 else 0

        # Max drawdown
        peak = eq_series.cummax()
        drawdown = (eq_series - peak) / peak
        max_drawdown = float(drawdown.min() * 100)

        exit_type = "cover" if is_short else "sell"
        sell_trades = [t for t in trades if t["type"] == exit_type]
        winning = [t for t in sell_trades if t.get("pnl", 0) > 0]
        win_rate = len(winning) / len(sell_trades) * 100 if sell_trades else 0

        gains = [float(t["pnl"]) for t in sell_trades if t.get("pnl", 0) > 0]
        losses = [float(t["pnl"]) for t in sell_trades if t.get("pnl", 0) < 0]
        gain_stats = _side_stats(gains)
        loss_stats = _side_stats(losses)
        pnl_distribution = [round(float(t.get("pnl", 0)), 2) for t in sell_trades]
        edge_stats = _edge_stats(gains, losses, len(sell_trades))

        # Build EMA overlay for rising_over/falling_over conditions in buy rules
        ema_overlays = []
        for rule in buy_rules:
            if rule.condition in ("rising_over", "falling_over") and rule.indicator == "ma" and rule.params:
                ema_series = resolve_series(rule, indicators)
                if ema_series is not None:
                    lookback = int(rule.value) if rule.value is not None else 10
                    active = []
                    for i in range(len(ema_series)):
                        if i < lookback:
                            active.append(False)
                        elif rule.condition == "rising_over":
                            active.append(bool(ema_series.iloc[i] > ema_series.iloc[i - lookback]))
                        else:
                            active.append(bool(ema_series.iloc[i] < ema_series.iloc[i - lookback]))
                    ema_overlays.append({
                        "indicator": f"ma_{rule.params['period']}_{rule.params.get('type', 'ema')}",
                        "condition": rule.condition,
                        "lookback": lookback,
                        "series": _series_to_list(df.index, req.interval, ema_series),
                        "active": active,
                        "side": "buy",
                    })

        # Buy & hold baseline curve (always long, even for short strategies)
        first_close = float(close.iloc[0])
        baseline_curve = [
            {
                "time": _format_time(df.index[i], req.interval),
                "value": round(req.initial_capital * float(close.iloc[i]) / first_close, 2),
            }
            for i in range(len(df))
        ]

        # Cache raw data for macro endpoint re-aggregation
        _backtest_cache.clear()
        _backtest_cache.update({
            "hash": _request_hash(req),
            "equity_curve": equity,
            "trades": trades,
        })

        result = {
            "summary": {
                "initial_capital": req.initial_capital,
                "final_value": round(final_value, 2),
                "total_return_pct": round(total_return, 2),
                "buy_hold_return_pct": round(buy_hold_return, 2),
                "num_trades": len(sell_trades),
                "win_rate_pct": round(win_rate, 2),
                "sharpe_ratio": round(sharpe, 3),
                "max_drawdown_pct": round(max_drawdown, 2),
                "gain_stats": gain_stats,
                "loss_stats": loss_stats,
                "pnl_distribution": pnl_distribution,
                **edge_stats,
            },
            "trades": trades,
            "equity_curve": equity,
            "baseline_curve": baseline_curve,
        }
        if ema_overlays:
            result["ema_overlays"] = ema_overlays
        if signal_trace is not None:
            result["signal_trace"] = signal_trace
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

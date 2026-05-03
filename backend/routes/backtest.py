from fastapi import APIRouter, HTTPException
import hashlib
import json
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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


_DAILY_INTERVALS = {'1d', '1wk', '1mo'}
_ET = ZoneInfo('America/New_York')
_SESSION_BUCKETS = [
    "09:30", "10:00", "10:30", "11:00", "11:30",
    "12:00", "12:30", "13:00", "13:30", "14:00",
    "14:30", "15:00", "15:30",
]


def compute_session_analytics(trades: list, interval: str) -> list | None:
    """Break down trade performance by 30-min ET time-of-day bucket.

    Trades alternate buy/sell (even indices = entries, odd = exits).
    Only computed for intraday intervals; returns None for daily+.
    """
    if interval in _DAILY_INTERVALS:
        return None

    # Build a dict keyed by bucket label with running totals
    buckets: dict[str, dict] = {
        b: {"trade_count": 0, "wins": 0, "losses": 0, "total_pnl": 0.0, "total_pnl_pct": 0.0}
        for b in _SESSION_BUCKETS
    }

    # Pair consecutive entry (even index) + exit (odd index) trades
    i = 0
    while i + 1 < len(trades):
        entry = trades[i]
        exit_ = trades[i + 1]
        i += 2

        ts = entry.get("date")
        pnl = exit_.get("pnl", 0) or 0
        pnl_pct = exit_.get("pnl_pct", 0) or 0

        if ts is None or not isinstance(ts, (int, float)):
            continue

        dt_et = datetime.fromtimestamp(ts, tz=_ET)
        # Compute 30-min bucket: floor minutes to 0 or 30
        minute_bucket = (dt_et.minute // 30) * 30
        label = f"{dt_et.hour:02d}:{minute_bucket:02d}"

        if label not in buckets:
            continue  # outside standard session hours

        b = buckets[label]
        b["trade_count"] += 1
        b["total_pnl"] += pnl
        b["total_pnl_pct"] += pnl_pct
        if pnl > 0:
            b["wins"] += 1
        elif pnl < 0:
            b["losses"] += 1

    result = []
    for label in _SESSION_BUCKETS:
        b = buckets[label]
        count = b["trade_count"]
        wins = b["wins"]
        win_rate = round(wins / count * 100, 1) if count > 0 else 0.0
        avg_pnl = round(b["total_pnl"] / count, 2) if count > 0 else 0.0
        avg_pnl_pct = round(b["total_pnl_pct"] / count, 2) if count > 0 else 0.0
        result.append({
            "bucket": label,
            "trade_count": count,
            "wins": wins,
            "losses": b["losses"],
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "total_pnl": round(b["total_pnl"], 2),
            "avg_pnl_pct": avg_pnl_pct,
        })
    return result


def _compute_spy_correlation(equity: list, start: str, end: str) -> dict:
    """Compute beta and R-squared of daily strategy returns vs SPY."""
    if not equity or len(equity) < 3:
        return {"beta": None, "r_squared": None}

    # Group equity values by ET date (last value per day)
    eq_by_date: dict[str, float] = {}
    for pt in equity:
        t = pt.get("time")
        v = pt.get("value")
        if t is None or v is None:
            continue
        if isinstance(t, (int, float)):
            date_str = datetime.fromtimestamp(t, tz=ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        else:
            date_str = str(t)[:10]
        eq_by_date[date_str] = v

    if len(eq_by_date) < 3:
        return {"beta": None, "r_squared": None}

    sorted_dates = sorted(eq_by_date)
    eq_vals = [eq_by_date[d] for d in sorted_dates]
    prev_vals = eq_vals[:-1]
    eq_rets_arr = np.array(
        [(eq_vals[i] - prev_vals[i]) / prev_vals[i] if prev_vals[i] != 0 else 0.0
         for i in range(len(prev_vals))]
    )
    ret_dates = sorted_dates[1:]  # returns correspond to the later date

    if len(eq_rets_arr) < 3:
        return {"beta": None, "r_squared": None}

    try:
        spy_df = _fetch("SPY", start, end, "1d")
        spy_strs = [str(t)[:10] for t in spy_df.index.astype(str)]
        spy_close = spy_df["Close"].values.astype(float)
        spy_ret_by_date = {
            spy_strs[i]: (spy_close[i] - spy_close[i - 1]) / spy_close[i - 1]
            for i in range(1, len(spy_strs))
            if spy_close[i - 1] != 0
        }
    except Exception:
        return {"beta": None, "r_squared": None}

    ret_date_idx = {d: i for i, d in enumerate(ret_dates)}
    common = [d for d in ret_dates if d in spy_ret_by_date]
    if len(common) < 3:
        return {"beta": None, "r_squared": None}

    strat_arr = np.array([eq_rets_arr[ret_date_idx[d]] for d in common])
    spy_arr = np.array([spy_ret_by_date[d] for d in common])

    var_spy = float(np.var(spy_arr))
    if var_spy < 1e-10:
        return {"beta": None, "r_squared": None}

    cov = float(np.cov(strat_arr, spy_arr)[0, 1])
    beta = cov / var_spy

    corr = float(np.corrcoef(strat_arr, spy_arr)[0, 1])
    r_squared = corr ** 2 if not np.isnan(corr) else None

    return {
        "beta": round(beta, 3),
        "r_squared": round(r_squared, 4) if r_squared is not None else None,
    }


@router.post("/api/backtest")
def run_backtest(req: StrategyRequest):
    try:
        df = _fetch(req.ticker, req.start, req.end, req.interval, source=req.source, extended_hours=req.extended_hours)

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"] if "Volume" in df.columns else None
        buy_rules = [migrate_rule(r) for r in req.buy_rules]
        sell_rules = [migrate_rule(r) for r in req.sell_rules]
        all_rules = buy_rules + sell_rules
        indicators = compute_indicators(close, high=high, low=low, volume=volume, rules=all_rules)

        # Simulate
        capital = req.initial_capital
        position = 0.0
        entry_price = 0.0
        entry_ts = None
        entry_bar_idx = 0
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

        def _rule_desc(r):
            """Short human-readable description of a rule."""
            desc = f"{r.indicator} {r.condition}"
            if r.value is not None:
                desc += f" {r.value}"
            if r.param:
                desc += f" ({r.param})"
            if r.threshold is not None:
                desc += f" min {r.threshold}%"
            return desc.strip()

        def _fired_rules(rules, indicators, i):
            """Return list of rule descriptions for non-muted rules that fired."""
            return [_rule_desc(r) for r in rules if not r.muted and eval_rule(r, indicators, i)]

        def _trace_rules(rules, indicators, i, label):
            """Build per-rule evaluation detail for debug trace."""
            details = []
            for r in rules:
                if r.muted:
                    details.append({"rule": _rule_desc(r), "muted": True, "result": False})
                    continue
                result = eval_rule(r, indicators, i)
                ind_series = resolve_series(r, indicators) or indicators.get("close")
                v_now = round(float(ind_series.iloc[i]), 4) if ind_series is not None and pd.notna(ind_series.iloc[i]) else None
                v_prev = round(float(ind_series.iloc[i - 1]), 4) if ind_series is not None and i > 0 and pd.notna(ind_series.iloc[i - 1]) else None
                details.append({
                    "rule": _rule_desc(r),
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
                entry_bar_idx = i
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
                    "rules": _fired_rules(buy_rules, indicators, i),
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

                # Time stop: exit after N bars held
                time_stop_hit = req.max_bars_held is not None and (i - entry_bar_idx) >= req.max_bars_held

                # Exit priority: fixed stop beats trailing stop beats time stop
                if stop_hit:
                    raw_exit = stop_price_limit
                    exit_reason = "stop_loss"
                elif trail_hit:
                    raw_exit = trail_stop_price
                    exit_reason = "trailing_stop"
                elif time_stop_hit:
                    raw_exit = price
                    exit_reason = "time_stop"
                else:
                    raw_exit = price
                    exit_reason = "signal"

                # Slippage: short covers at higher price (worse), long sells at lower price (worse)
                if is_short:
                    exit_price = raw_exit * (1 + drag)
                else:
                    exit_price = raw_exit * (1 - drag)
                sell_fired = eval_rules(sell_rules, req.sell_logic, indicators, i)
                if stop_hit or trail_hit or time_stop_hit or sell_fired:
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
                    exit_rules: list[str] = []
                    if exit_reason == "stop_loss":
                        exit_rules = ["stop loss"]
                    elif exit_reason == "trailing_stop":
                        exit_rules = ["trailing stop"]
                    elif time_stop_hit:
                        exit_rules = ["time stop"]
                    else:
                        exit_rules = _fired_rules(sell_rules, indicators, i)
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
                        "rules": exit_rules,
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
        if is_short and position > 0:
            unrealized = position * (entry_price - final_price)
            final_value = capital + position * entry_price + unrealized
        else:
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

        # Per-rule signal tracking for visualized rules
        rule_signals = []
        viz_rules = [
            ("buy", i, r) for i, r in enumerate(buy_rules) if r.visualize and not r.muted
        ] + [
            ("sell", len(buy_rules) + i, r) for i, r in enumerate(sell_rules) if r.visualize and not r.muted
        ]
        if viz_rules:
            for side, rule_index, rule in viz_rules:
                signals = []
                for bar_idx in range(1, len(df)):
                    raw = eval_rule(rule, indicators, bar_idx)
                    fired = (not raw) if (rule.negated and bar_idx >= 1) else raw
                    if fired:
                        signals.append({
                            "time": _format_time(df.index[bar_idx], req.interval),
                            "price": round(float(close.iloc[bar_idx]), 4),
                        })
                rule_signals.append({
                    "rule_index": rule_index,
                    "label": _rule_desc(rule),
                    "side": side,
                    "signals": signals,
                })

        # Cache raw data for macro endpoint re-aggregation
        _backtest_cache.clear()
        _backtest_cache.update({
            "hash": _request_hash(req),
            "equity_curve": equity,
            "trades": trades,
        })

        spy_corr = _compute_spy_correlation(equity, req.start, req.end)

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
                **spy_corr,
            },
            "trades": trades,
            "equity_curve": equity,
            "baseline_curve": baseline_curve,
            "session_analytics": compute_session_analytics(trades, req.interval),
        }
        if ema_overlays:
            result["ema_overlays"] = ema_overlays
        if signal_trace is not None:
            result["signal_trace"] = signal_trace
        if rule_signals:
            result["rule_signals"] = rule_signals
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

import logging

from fastapi import APIRouter, HTTPException
import hashlib
import json
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from shared import _fetch, _format_time, _format_time_index, _INTRADAY_INTERVALS, fetch_higher_tf, align_htf_to_ltf, htf_lookback_days, require_valid_source
from signal_engine import Rule, compute_indicators, eval_rules, eval_rule, migrate_rule, resolve_series
from routes.indicators import _series_to_list
from models import TrailingStopConfig, DynamicSizingConfig, TradingHoursConfig, StrategyRequest, RegimeConfig
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


logger = logging.getLogger(__name__)
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


def _compute_spy_correlation(trades: list, start: str, end: str) -> dict:
    """Compute beta and R-squared of per-trade returns vs SPY over the same holding periods.

    Uses trade-level returns (not daily equity returns) to avoid the zero-return problem
    when the strategy is flat for most of the period.
    """
    entry_trades = [t for t in trades if t.get("type") in ("buy", "short")]
    exit_trades = [t for t in trades if t.get("type") in ("sell", "cover") and t.get("pnl") is not None]
    pairs = list(zip(entry_trades, exit_trades))
    if len(pairs) < 3:
        return {"beta": None, "r_squared": None}

    try:
        spy_df = _fetch("SPY", start, end, "1d")
        spy_strs = [str(t)[:10] for t in spy_df.index.astype(str)]
        spy_close_by_date = {spy_strs[i]: float(spy_df["Close"].values[i]) for i in range(len(spy_strs))}
    except Exception:
        return {"beta": None, "r_squared": None}

    def _to_date(d):
        if isinstance(d, (int, float)):
            return datetime.fromtimestamp(d, tz=ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        return str(d)[:10]

    strat_returns, spy_returns = [], []
    for entry, exit_t in pairs:
        entry_value = entry["price"] * entry["shares"]
        if entry_value == 0:
            continue
        entry_date = _to_date(entry["date"])
        exit_date = _to_date(exit_t["date"])
        if entry_date not in spy_close_by_date or exit_date not in spy_close_by_date:
            continue
        spy_entry = spy_close_by_date[entry_date]
        spy_exit = spy_close_by_date[exit_date]
        if spy_entry == 0:
            continue
        strat_returns.append(exit_t["pnl"] / entry_value)
        spy_returns.append((spy_exit - spy_entry) / spy_entry)

    if len(strat_returns) < 3:
        return {"beta": None, "r_squared": None}

    strat_arr = np.array(strat_returns)
    spy_arr = np.array(spy_returns)

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


def _compute_regime_series(req: StrategyRequest, ltf_df: pd.DataFrame) -> "pd.Series | None":
    """Compute per-bar regime_active boolean from RegimeConfig.

    Returns a boolean pd.Series indexed on ltf_df.index where True = regime allows entries.
    Returns None when regime is disabled (gate always open).
    Raises ValueError when regime is enabled but computation fails.

    Dual path:
    - rules path (rc.rules non-empty): calls eval_rules() over HTF bars using full rule set.
    - legacy path (rc.rules empty): single-indicator + condition check (above/below/rising/falling).
    """
    rc = req.regime
    if not rc or not rc.enabled:
        return None

    from indicators import compute_instance, OHLCVSeries as _OHLCVSeries  # local import avoids circular dep

    if rc.rules:
        # B28: rules-based path — evaluate a full rule set on HTF bars
        migrated_rules = [migrate_rule(r) for r in rc.rules]
        lookback = max((htf_lookback_days(r.indicator, r.params or {}) for r in migrated_rules), default=20)
        htf_start = (datetime.fromisoformat(req.start) - timedelta(days=lookback)).strftime("%Y-%m-%d")
        htf_df = fetch_higher_tf(req.ticker, htf_start, req.end, rc.timeframe, source=req.source)
        if htf_df.empty:
            raise ValueError(f"No HTF data for regime filter ({rc.timeframe})")

        htf_close = htf_df["Close"]
        htf_high = htf_df["High"]
        htf_low = htf_df["Low"]
        htf_vol = htf_df["Volume"] if "Volume" in htf_df.columns else htf_df["Close"] * 0

        htf_indicators = compute_indicators(htf_close, high=htf_high, low=htf_low, volume=htf_vol, rules=migrated_rules)

        raw_values = [
            eval_rules(migrated_rules, rc.logic, htf_indicators, i)
            for i in range(len(htf_df))
        ]
        raw_bool = pd.Series(raw_values, index=htf_df.index, dtype=bool)
    else:
        # Legacy path: single-indicator + condition
        lookback = htf_lookback_days(rc.indicator, rc.indicator_params)
        htf_start = (datetime.fromisoformat(req.start) - timedelta(days=lookback)).strftime("%Y-%m-%d")
        htf_df = fetch_higher_tf(req.ticker, htf_start, req.end, rc.timeframe, source=req.source)
        if htf_df.empty:
            raise ValueError(f"No HTF data for regime filter ({rc.timeframe})")

        vol_col = htf_df["Volume"] if "Volume" in htf_df.columns else htf_df["Close"] * 0
        ohlcv = _OHLCVSeries(
            close=htf_df["Close"],
            high=htf_df["High"],
            low=htf_df["Low"],
            volume=vol_col,
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
            raise ValueError(f"Unsupported regime condition for Stage 3: {rc.condition!r}. Use above/below/rising/falling.")

    raw_bool = raw_bool.fillna(False)

    if rc.min_bars > 1:
        smoothed = raw_bool.astype(int).rolling(rc.min_bars, min_periods=rc.min_bars).min().fillna(0).astype(bool)
    else:
        smoothed = raw_bool.astype(bool)

    aligned = align_htf_to_ltf(smoothed.astype(float), ltf_df.index)
    return aligned.fillna(0).astype(bool)


@router.post("/api/backtest")
def backtest_endpoint(req: StrategyRequest):
    # FastAPI route wrapper. Keep the public HTTP signature one-arg so Pydantic
    # doesn't wrap the body into {"req": ..., "indicator_cache": ...}. Internal
    # callers (optimizer, walk_forward) import run_backtest directly to pass
    # the performance kwargs.
    return run_backtest(req)


def run_backtest(
    req: StrategyRequest,
    *,
    include_spy_correlation: bool = True,
    indicator_cache: dict | None = None,
    df: pd.DataFrame | None = None,
):
    """Run a single backtest.

    Args:
        req: StrategyRequest describing the backtest parameters.
        include_spy_correlation: Whether to compute SPY correlation in the summary.
        indicator_cache: Optional shared cache for indicator series across combos.
        df: Optional pre-fetched DataFrame to use instead of calling _fetch().
            When provided, bypasses _fetch() entirely — caller is responsible
            for ensuring the df matches req.ticker / req.start / req.end /
            req.interval. df must have Open, High, Low, Close columns and a
            DatetimeIndex. Do NOT pass bars outside req.start/req.end, since
            downstream code uses df.index[0] / df.index[-1] for trade date
            stamping. When None (default), _fetch() is called as normal.
    """
    # F94: shared allowlist + case-normalize at the route boundary. The
    # regime path below also calls fetch_higher_tf(source=req.source) and
    # ad-hoc _fetch calls; bouncing unknown providers up front keeps the
    # failure mode uniform (400 Invalid source) rather than per-helper.
    req.source = require_valid_source(req.source)
    try:
        # Use pre-fetched slice when provided (WFA window path); otherwise fetch.
        if df is None:
            df = _fetch(req.ticker, req.start, req.end, req.interval, source=req.source, extended_hours=req.extended_hours)

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"] if "Volume" in df.columns else None
        buy_rules = [migrate_rule(r) for r in req.buy_rules]
        sell_rules = [migrate_rule(r) for r in req.sell_rules]
        all_rules = buy_rules + sell_rules

        # B23: dual rule sets when regime is enabled.
        # Previously required both long_buy_rules and short_buy_rules to be non-empty,
        # which silently fell back to the unified buy_rules when only one tab was filled.
        # Fix: activate b23_mode on regime.enabled alone — empty rule lists produce no
        # entries for that direction (eval_rules([]) returns False), which is the correct
        # behavior for "user only configured one side of the regime strategy."
        b23_mode = bool(req.regime and req.regime.enabled)
        if b23_mode:
            long_buy_rules = [migrate_rule(r) for r in (req.long_buy_rules or [])]
            long_sell_rules = [migrate_rule(r) for r in (req.long_sell_rules or [])]
            short_buy_rules = [migrate_rule(r) for r in (req.short_buy_rules or [])]
            short_sell_rules = [migrate_rule(r) for r in (req.short_sell_rules or [])]
            # all_rules already contains unified buy + sell rules (see line above).
            # In b23 mode, append direction-specific rules so their indicators are pre-computed too.
            # Duplicates are harmless — compute_indicators caches by (indicator, params).
            all_rules = all_rules + long_buy_rules + long_sell_rules + short_buy_rules + short_sell_rules
        else:
            long_buy_rules = []
            long_sell_rules = []
            short_buy_rules = []
            short_sell_rules = []

        indicators = compute_indicators(close, high=high, low=low, volume=volume, rules=all_rules, cache=indicator_cache)

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

        position_direction: str | None = None  # set at entry, cleared at exit
        drag = req.slippage_bps / 10_000.0   # bps → fractional
        ts = req.trailing_stop
        atr = indicators.get("atr")
        signal_trace = [] if req.debug else None
        ds = req.dynamic_sizing
        th = req.trading_hours
        sas = req.skip_after_stop
        is_intraday = req.interval in _INTRADAY_INTERVALS
        regime_active_series = _compute_regime_series(req, df)
        on_flip = req.regime.on_flip if (req.regime and req.regime.enabled) else "hold"
        prev_regime_active: bool = False

        # B25: per-direction counters in b23_mode; single counters otherwise
        if b23_mode:
            consec_sl_count_by_dir: dict[str, int] = {'long': 0, 'short': 0}
            skip_remaining_by_dir: dict[str, int] = {'long': 0, 'short': 0}
        else:
            consec_sl_count = 0  # track consecutive stop losses for dynamic sizing
            skip_remaining = 0   # entries still to skip after a qualifying stop

        # B25: per-direction helper closures
        def _dir_stop(direction: str):
            """Return per-direction stop_loss_pct when b23_mode, else global."""
            if not b23_mode:
                return req.stop_loss_pct
            v = req.long_stop_loss_pct if direction == 'long' else req.short_stop_loss_pct
            return v if v is not None else req.stop_loss_pct

        def _dir_ts(direction: str):
            """Return per-direction trailing_stop when b23_mode, else global.
            Merges per-direction type+value into global config's source/activate fields.
            """
            if not b23_mode:
                return req.trailing_stop
            base = req.trailing_stop
            override = req.long_trailing_stop if direction == 'long' else req.short_trailing_stop
            if override is None:
                return base
            if base is None:
                return override
            return TrailingStopConfig(
                type=override.type, value=override.value,
                source=base.source, activate_on_profit=base.activate_on_profit,
                activate_pct=base.activate_pct,
            )

        def _dir_mbh(direction: str):
            """Return per-direction max_bars_held when b23_mode, else global."""
            if not b23_mode:
                return req.max_bars_held
            v = req.long_max_bars_held if direction == 'long' else req.short_max_bars_held
            return v if v is not None else req.max_bars_held

        def _dir_size(direction: str) -> float:
            """Return per-direction position_size when b23_mode, else global."""
            if not b23_mode:
                return req.position_size
            v = req.long_position_size if direction == 'long' else req.short_position_size
            return v if v is not None else req.position_size

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

        close_arr = close.to_numpy(dtype=float, copy=False)
        # Pre-format every bar's timestamp once (vectorized)
        date_strs = _format_time_index(df.index, req.interval)

        for i in range(len(df)):
            price = close_arr[i]
            date = date_strs[i]

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

            curr_regime_active = bool(regime_active_series.iloc[i]) if regime_active_series is not None else True

            # Regime flip: forced exit (and optional reversal) when on_flip != "hold"
            if i > 0 and regime_active_series is not None and on_flip != "hold":
                if curr_regime_active != prev_regime_active and position > 0:
                    raw_exit = price
                    if position_direction == "short":
                        exit_price_rf = raw_exit * (1 + drag)
                    else:
                        exit_price_rf = raw_exit * (1 - drag)
                    exit_slippage_rf = abs(position * (raw_exit - exit_price_rf))
                    commission_rf = per_leg_commission(position, req)
                    bcost_rf = borrow_cost(position, entry_price, entry_ts, df.index[i],
                                           position_direction, req)
                    if position_direction == "short":
                        pnl_rf = position * (entry_price - exit_price_rf) - commission_rf - bcost_rf
                        capital += position * entry_price + pnl_rf
                    else:
                        proceeds_rf = position * exit_price_rf
                        pnl_rf = (proceeds_rf - commission_rf) - position * entry_price
                        capital += proceeds_rf - commission_rf
                    exit_type_rf = "cover" if position_direction == "short" else "sell"
                    rf_old_direction = position_direction
                    trades.append({
                        "type": exit_type_rf, "date": date, "price": round(exit_price_rf, 4),
                        "shares": round(position, 4), "direction": rf_old_direction,
                        "pnl": round(pnl_rf, 2),
                        "pnl_pct": round(pnl_rf / (position * entry_price) * 100, 2),
                        "stop_loss": False, "trailing_stop": False,
                        "slippage": round(exit_slippage_rf, 2), "commission": round(commission_rf, 2),
                        "borrow_cost": round(bcost_rf, 2), "rules": ["regime flip"],
                        "exit_reason": "regime_flip",
                    })
                    if signal_trace is not None:
                        signal_trace.append({
                            "date": date, "price": round(price, 4), "position": "exited",
                            "action": "REGIME_FLIP_EXIT",
                        })
                    position = 0.0
                    position_direction = None
                    entry_ts = None
                    trail_peak = 0.0
                    trail_stop_price = None
                    # Regime flip is not a stop-loss; don't modify consec_sl_count

                    if on_flip == "close_and_reverse":
                        new_dir = "short" if rf_old_direction == "long" else "long"
                        if new_dir == "short":
                            fill_price_rf = price * (1 - drag)
                        else:
                            fill_price_rf = price * (1 + drag)
                        # B25: rebind ts for new direction before trail_peak usage
                        ts = _dir_ts(new_dir)
                        shares_rf = (capital * _dir_size(new_dir)) / fill_price_rf
                        commission_rf2 = per_leg_commission(shares_rf, req)
                        entry_slippage_rf = abs(shares_rf * (fill_price_rf - price))
                        position = shares_rf
                        position_direction = new_dir
                        entry_price = fill_price_rf
                        entry_ts = df.index[i]
                        entry_bar_idx = i
                        capital -= shares_rf * fill_price_rf + commission_rf2
                        trail_peak = fill_price_rf
                        trail_stop_price = None
                        trades.append({
                            "type": "short" if new_dir == "short" else "buy",
                            "date": date, "price": round(fill_price_rf, 4),
                            "shares": round(shares_rf, 4), "direction": new_dir,
                            "slippage": round(entry_slippage_rf, 2),
                            "commission": round(commission_rf2, 2),
                            "rules": ["regime flip reverse"],
                        })
                        if signal_trace is not None:
                            signal_trace.append({
                                "date": date, "price": round(price, 4), "position": "entered",
                                "action": "REGIME_FLIP_REVERSE",
                            })

            if regime_active_series is not None:
                prev_regime_active = curr_regime_active

            if regime_active_series is None:
                regime_ok = True
            elif on_flip == "close_and_reverse":
                regime_ok = True
            else:
                regime_ok = curr_regime_active

            if b23_mode:
                if curr_regime_active:
                    active_buy = long_buy_rules
                    active_buy_logic = req.long_buy_logic
                else:
                    active_buy = short_buy_rules
                    active_buy_logic = req.short_buy_logic
                buy_fires = position == 0 and hour_ok and eval_rules(active_buy, active_buy_logic, indicators, i)
            else:
                buy_fires = position == 0 and hour_ok and regime_ok and eval_rules(buy_rules, req.buy_logic, indicators, i)
            if b23_mode:
                sr_key = 'long' if curr_regime_active else 'short'
                if buy_fires and skip_remaining_by_dir[sr_key] > 0:
                    skip_remaining_by_dir[sr_key] -= 1
                    if signal_trace is not None:
                        signal_trace.append({
                            "date": date, "price": round(price, 4), "position": "flat",
                            "action": f"SKIPPED (post-stop, {skip_remaining_by_dir[sr_key]} left) [{sr_key}]",
                        })
                    buy_fires = False
            else:
                if buy_fires and skip_remaining > 0:
                    skip_remaining -= 1
                    if signal_trace is not None:
                        signal_trace.append({
                            "date": date, "price": round(price, 4), "position": "flat",
                            "action": f"SKIPPED (post-stop, {skip_remaining} left)",
                        })
                    buy_fires = False

            if buy_fires:
                # Direction follows regime when close_and_reverse is active
                if b23_mode:
                    position_direction = 'long' if curr_regime_active else 'short'
                elif on_flip == "close_and_reverse" and regime_active_series is not None:
                    position_direction = req.direction if curr_regime_active else ("short" if req.direction == "long" else "long")
                else:
                    position_direction = req.direction

                # B25: bind per-direction trailing stop AFTER position_direction is known
                ts = _dir_ts(position_direction)

                # Dynamic sizing: reduce position after consecutive stop losses
                effective_size = _dir_size(position_direction)
                if b23_mode:
                    csl = consec_sl_count_by_dir[position_direction]
                else:
                    csl = consec_sl_count
                if ds and ds.enabled and csl >= ds.consec_sls:
                    effective_size = _dir_size(position_direction) * (ds.reduced_pct / 100)

                # Slippage: short entry fills lower (worse for seller), long fills higher (worse for buyer)
                if position_direction == "short":
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
                entry_type = "short" if position_direction == "short" else "buy"
                # In b23_mode, active_buy holds the direction-specific rule list bound
                # above; using buy_rules here would show the hidden unified rules in
                # trade tooltips even though they never drove the entry signal.
                display_buy_rules = active_buy if b23_mode else buy_rules
                trades.append({
                    "type": entry_type, "date": date, "price": round(fill_price, 4),
                    "shares": round(shares, 4),
                    "direction": position_direction,
                    "slippage": round(entry_slippage, 2),
                    "commission": round(commission, 2),
                    "rules": _fired_rules(display_buy_rules, indicators, i),
                })
                if signal_trace is not None:
                    signal_trace.append({
                        "date": date, "price": round(price, 4), "position": "entered",
                        "action": "SHORT" if position_direction == "short" else "BUY",
                        "buy_rules": _trace_rules(display_buy_rules, indicators, i, "buy"),
                    })

            elif position > 0:
                # Update trailing stop peak and compute trail_stop_price
                trail_hit = False
                if ts:
                    if position_direction == "short":
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

                # Check fixed stop loss (B25: use per-direction stop when b23_mode)
                stop_loss_pct_eff = _dir_stop(position_direction)
                if position_direction == "short":
                    stop_price_limit = entry_price * (1 + stop_loss_pct_eff / 100) if (stop_loss_pct_eff and stop_loss_pct_eff > 0) else None
                    stop_hit = stop_price_limit is not None and high.iloc[i] >= stop_price_limit
                else:
                    stop_price_limit = entry_price * (1 - stop_loss_pct_eff / 100) if (stop_loss_pct_eff and stop_loss_pct_eff > 0) else None
                    stop_hit = stop_price_limit is not None and low.iloc[i] <= stop_price_limit

                # Time stop: exit after N bars held (B25: use per-direction max_bars_held when b23_mode)
                mbh_eff = _dir_mbh(position_direction)
                time_stop_hit = mbh_eff is not None and (i - entry_bar_idx) >= mbh_eff

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
                if position_direction == "short":
                    exit_price = raw_exit * (1 + drag)
                else:
                    exit_price = raw_exit * (1 - drag)
                if b23_mode and position_direction is not None:
                    active_sell = long_sell_rules if position_direction == 'long' else short_sell_rules
                    active_sell_logic = req.long_sell_logic if position_direction == 'long' else req.short_sell_logic
                    sell_fired = eval_rules(active_sell, active_sell_logic, indicators, i) if active_sell else False
                else:
                    sell_fired = eval_rules(sell_rules, req.sell_logic, indicators, i)
                display_sell_rules = active_sell if b23_mode and position_direction is not None else sell_rules
                if stop_hit or trail_hit or time_stop_hit or sell_fired:
                    exit_slippage = abs(position * (raw_exit - exit_price))
                    commission = per_leg_commission(position, req)
                    bcost = borrow_cost(position, entry_price, entry_ts, df.index[i],
                                        position_direction, req)
                    if position_direction == "short":
                        pnl = position * (entry_price - exit_price) - commission - bcost
                        capital += position * entry_price + pnl
                    else:
                        proceeds = position * exit_price
                        pnl = (proceeds - commission) - position * entry_price
                        capital += proceeds - commission
                    exit_type = "cover" if position_direction == "short" else "sell"
                    exit_rules: list[str] = []
                    if exit_reason == "stop_loss":
                        exit_rules = ["stop loss"]
                    elif exit_reason == "trailing_stop":
                        exit_rules = ["trailing stop"]
                    elif time_stop_hit:
                        exit_rules = ["time stop"]
                    else:
                        exit_rules = _fired_rules(display_sell_rules, indicators, i)
                    trades.append({
                        "type": exit_type,
                        "date": date,
                        "price": round(exit_price, 4),
                        "shares": round(position, 4),
                        "direction": position_direction,
                        "pnl": round(pnl, 2),
                        "pnl_pct": round(pnl / (position * entry_price) * 100, 2),
                        "stop_loss": exit_reason == "stop_loss",
                        "trailing_stop": exit_reason == "trailing_stop",
                        "slippage": round(exit_slippage, 2),
                        "commission": round(commission, 2),
                        "borrow_cost": round(bcost, 2),
                        "rules": exit_rules,
                    })
                    if signal_trace is not None:
                        action = "STOP_LOSS" if exit_reason == "stop_loss" else "TRAIL_STOP" if exit_reason == "trailing_stop" else ("COVER" if position_direction == "short" else "SELL")
                    # B25: capture exited_direction BEFORE clearing position_direction
                    exited_direction = position_direction
                    position = 0.0
                    position_direction = None
                    entry_ts = None
                    trail_peak = 0.0
                    trail_stop_price = None
                    ds_trigger = ds.trigger if ds else "sl"
                    if b23_mode:
                        if is_post_loss_trigger(exit_reason, ds_trigger):
                            consec_sl_count_by_dir[exited_direction] += 1
                        else:
                            consec_sl_count_by_dir[exited_direction] = 0
                        if sas and sas.enabled and is_post_loss_trigger(exit_reason, sas.trigger):
                            skip_remaining_by_dir[exited_direction] = sas.count
                    else:
                        if is_post_loss_trigger(exit_reason, ds_trigger):
                            consec_sl_count += 1
                        else:
                            consec_sl_count = 0
                        if sas and sas.enabled and is_post_loss_trigger(exit_reason, sas.trigger):
                            skip_remaining = sas.count
                    if signal_trace is not None:
                        signal_trace.append({
                            "date": date, "price": round(price, 4), "position": "exited",
                            "action": action,
                            "sell_rules": _trace_rules(display_sell_rules, indicators, i, "sell"),
                        })
                elif signal_trace is not None:
                    # In position but no sell — trace any bar where at least one sell rule fires
                    sell_details = _trace_rules(display_sell_rules, indicators, i, "sell")
                    if any(d["result"] for d in sell_details if not d.get("muted")):
                        signal_trace.append({
                            "date": date, "price": round(price, 4), "position": "holding",
                            "action": "SELL_PARTIAL (AND not met)",
                            "sell_rules": sell_details,
                        })

            elif signal_trace is not None and position == 0 and not b23_mode:
                # Not in position — trace if sell rules WOULD have fired.
                # Suppressed in b23_mode: unified sell_rules are hidden from the user;
                # showing them in a flat-position trace would leak the hidden rule set.
                active_sell = [r for r in sell_rules if not r.muted]
                if active_sell and i > 0:
                    sell_details = _trace_rules(sell_rules, indicators, i, "sell")
                    if any(d["result"] for d in sell_details if not d.get("muted")):
                        signal_trace.append({
                            "date": date, "price": round(price, 4), "position": "flat",
                            "action": "MISSED (no position)",
                            "sell_rules": sell_details,
                        })

            if position_direction == "short" and position > 0:
                unrealized = position * (entry_price - price)
                total_value = capital + position * entry_price + unrealized
            else:
                total_value = capital + (position * price if position > 0 else 0)
            equity.append({"time": date, "value": round(total_value, 2)})

        # Close open position at last price
        final_price = close.iloc[-1]
        if position_direction == "short" and position > 0:
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

        # Include both exit types so regime close_and_reverse (mixed long/short) counts correctly
        sell_trades = [t for t in trades if t["type"] in ("sell", "cover")]
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
                "time": date_strs[i],
                "value": round(req.initial_capital * close_arr[i] / first_close, 2),
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
                            "time": date_strs[bar_idx],
                            "price": round(close_arr[bar_idx], 4),
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

        if include_spy_correlation:
            spy_corr = _compute_spy_correlation(trades, req.start, req.end)
        else:
            spy_corr = {"beta": None, "r_squared": None}

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
        if regime_active_series is not None:
            result["regime_series"] = [
                {
                    "time": date_strs[i],
                    "direction": (
                        req.direction if bool(regime_active_series.iloc[i])
                        else ("short" if req.direction == "long" else "long")
                    ) if on_flip == "close_and_reverse" else (
                        req.direction if bool(regime_active_series.iloc[i]) else "flat"
                    ),
                }
                for i in range(len(df))
            ]
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("/api/backtest failed")
        raise HTTPException(status_code=500, detail="backtest failed")

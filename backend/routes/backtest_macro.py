from fastapi import APIRouter
import pandas as pd

router = APIRouter()

_FREQ_MAP = {"D": "D", "W": "W", "M": "ME", "Q": "QE", "Y": "YE"}
_LABELS = {
    "D": "Daily",
    "W": "Weekly",
    "M": "Monthly",
    "Q": "Quarterly",
    "Y": "Yearly",
}


def _parse_times(times):
    """Return a DatetimeIndex from a list of string dates or unix timestamps."""
    if not times:
        return pd.DatetimeIndex([])
    if isinstance(times[0], (int, float)):
        return pd.to_datetime(times, unit="s", utc=True).tz_localize(None)
    return pd.to_datetime(times)


def aggregate_macro(equity_curve, trades, bucket, initial_capital):
    """Aggregate a daily equity curve into OHLC macro buckets.

    Parameters
    ----------
    equity_curve : list[dict]  — each dict has "time" (str date or unix int) and "value"
    trades       : list[dict]  — each dict has "type", "date", and optionally "pnl"
    bucket       : str         — one of D / W / M / Q / Y
    initial_capital : float

    Returns
    -------
    dict with keys:
        bucket       : str
        macro_curve  : list[dict]  — OHLC + drawdown_pct + trades per bucket
        period_stats : dict        — aggregate performance stats
    """
    empty_stats = {
        "label": _LABELS.get(bucket, bucket),
        "winning_pct": 0,
        "avg_return_pct": 0.0,
        "best_return_pct": 0.0,
        "worst_return_pct": 0.0,
        "avg_trades": 0.0,
    }

    if not equity_curve:
        return {"bucket": bucket, "macro_curve": [], "period_stats": empty_stats}

    freq = _FREQ_MAP.get(bucket, bucket)

    # ---- Build equity DataFrame ----
    times = [e["time"] for e in equity_curve]
    values = [e["value"] for e in equity_curve]
    index = _parse_times(times)
    eq_df = pd.DataFrame({"value": values}, index=index)
    eq_df.index.name = "time"

    # ---- Group equity by bucket ----
    grouped = eq_df.groupby(pd.Grouper(freq=freq))

    # ---- Parse trade dates for matching ----
    sell_trades = [t for t in trades if t.get("type") in ("sell", "cover") and "pnl" in t]
    if sell_trades:
        trade_times_raw = [t["date"] for t in sell_trades]
        trade_index = _parse_times(trade_times_raw)
    else:
        trade_index = None

    macro_curve = []
    running_peak = initial_capital

    for period_start, group in grouped:
        if group.empty:
            continue

        open_val = group["value"].iloc[0]
        high_val = group["value"].max()
        low_val = group["value"].min()
        close_val = group["value"].iloc[-1]

        running_peak = max(running_peak, high_val)
        dd_pct = round((low_val - running_peak) / running_peak * 100, 2)

        # Determine bucket time bounds
        bucket_start = group.index.min()
        bucket_end = group.index.max()

        # Match sell/cover trades to this bucket
        bucket_trades = []
        if trade_index is not None:
            for i, ti in enumerate(trade_index):
                if bucket_start <= ti <= bucket_end:
                    bucket_trades.append(sell_trades[i])

        # Time label: use ISO date string
        if isinstance(times[0], (int, float)):
            time_label = bucket_start.strftime("%Y-%m-%d")
        else:
            time_label = bucket_start.strftime("%Y-%m-%d")

        macro_curve.append(
            {
                "time": time_label,
                "open": open_val,
                "high": high_val,
                "low": low_val,
                "close": close_val,
                "drawdown_pct": dd_pct,
                "trades": bucket_trades,
            }
        )

    # ---- Period stats ----
    if not macro_curve:
        return {"bucket": bucket, "macro_curve": [], "period_stats": empty_stats}

    returns = []
    prev_close = initial_capital
    for b in macro_curve:
        ret = (b["close"] - prev_close) / prev_close * 100 if prev_close != 0 else 0.0
        returns.append(ret)
        prev_close = b["close"]

    winning = sum(1 for r in returns if r > 0)
    total = len(returns)
    winning_pct = round(winning / total * 100, 1) if total > 0 else 0

    best_return_pct = round(max(returns), 2) if returns else 0.0
    worst_return_pct = round(min(returns), 2) if returns else 0.0
    avg_return_pct = round(sum(returns) / len(returns), 2) if returns else 0.0

    trade_counts = [len(b["trades"]) for b in macro_curve]
    avg_trades = round(sum(trade_counts) / len(trade_counts), 2) if trade_counts else 0.0

    period_stats = {
        "label": _LABELS.get(bucket, bucket),
        "winning_pct": winning_pct,
        "avg_return_pct": avg_return_pct,
        "best_return_pct": best_return_pct,
        "worst_return_pct": worst_return_pct,
        "avg_trades": avg_trades,
    }

    return {"bucket": bucket, "macro_curve": macro_curve, "period_stats": period_stats}

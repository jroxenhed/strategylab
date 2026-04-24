import numpy as np
import pandas as pd
from typing import Optional
from pydantic import BaseModel

class Rule(BaseModel):
    indicator: str       # "macd", "rsi", "price", "ema"
    condition: str       # "crossover_up", "crossover_down", "above", "below", "crosses_above", "crosses_below", "turns_up_below", "turns_down_above"
    value: Optional[float] = None   # threshold (e.g. RSI < 30)
    param: Optional[str] = None     # e.g. "signal", "ema20"
    threshold: Optional[float] = None  # min move % for turns_up/turns_down
    muted: bool = False
    negated: bool = False
    params: Optional[dict] = None


_MA_MIGRATION: dict[str, dict] = {
    "ema20":  {"period": 20,  "type": "ema"},
    "ema50":  {"period": 50,  "type": "ema"},
    "ema200": {"period": 200, "type": "ema"},
    "ma8":    {"period": 8,   "type": "sma"},
    "ma21":   {"period": 21,  "type": "sma"},
}

_PARAM_MIGRATION: dict[str, str] = {
    "ema20":  "ma:20:ema",
    "ema50":  "ma:50:ema",
    "ema200": "ma:200:ema",
    "ma8":    "ma:8:sma",
    "ma21":   "ma:21:sma",
}


def migrate_rule(rule: Rule) -> Rule:
    """Convert legacy hardcoded MA indicators to generic ma(period, type). Idempotent."""
    data = rule.model_dump()
    ma_spec = _MA_MIGRATION.get(rule.indicator)
    if ma_spec:
        data["indicator"] = "ma"
        data["params"] = ma_spec
    if rule.param and rule.param in _PARAM_MIGRATION:
        data["param"] = _PARAM_MIGRATION[rule.param]
    return Rule(**data)



def compute_indicators(close: pd.Series, high: pd.Series = None, low: pd.Series = None,
                       rules: list[Rule] = None) -> dict[str, pd.Series]:
    """Compute indicators based on what rules require. MACD/RSI always included."""
    from indicators import compute_instance, OHLCVSeries

    if rules is None:
        rules = []

    ohlcv = OHLCVSeries(close=close, high=high if high is not None else close,
                        low=low if low is not None else close, volume=pd.Series(dtype=float))

    macd_result = compute_instance("macd", {"fast": 12, "slow": 26, "signal": 9}, ohlcv)

    result = {
        "macd": macd_result["macd"],
        "signal": macd_result["signal"],
        "histogram": macd_result["histogram"],
        "close": close,
    }

    rsi_specs: set[tuple[int, str]] = set()
    for rule in rules:
        if rule.indicator == "rsi":
            p = rule.params.get("period", 14) if rule.params else 14
            t = rule.params.get("type", "sma") if rule.params else "sma"
            rsi_specs.add((p, t))

    if not rsi_specs:
        rsi_specs.add((14, "sma"))

    for period, rsi_type in rsi_specs:
        rsi_result = compute_instance("rsi", {"period": period, "type": rsi_type}, ohlcv)
        result[f"rsi_{period}_{rsi_type}"] = rsi_result["rsi"]

    if high is not None and low is not None:
        atr_result = compute_instance("atr", {"period": 14}, ohlcv)
        result["atr"] = atr_result["atr"]

    ma_specs: set[tuple[int, str]] = set()
    for rule in rules:
        if rule.indicator == "ma" and rule.params:
            ma_specs.add((rule.params["period"], rule.params.get("type", "ema")))
        if rule.param and rule.param.startswith("ma:"):
            parts = rule.param.split(":", 2)
            if len(parts) != 3:
                continue
            _, period, ma_type = parts
            ma_specs.add((int(period), ma_type))

    for period, ma_type in ma_specs:
        key = f"ma_{period}_{ma_type}"
        ma_result = compute_instance("ma", {"period": period, "type": ma_type}, ohlcv)
        result[key] = ma_result["ma"]

    return result


def resolve_series(rule: Rule, indicators: dict[str, pd.Series]) -> pd.Series | None:
    """Resolve the primary series for a rule's indicator."""
    if rule.indicator == "ma" and rule.params:
        key = f"ma_{rule.params['period']}_{rule.params.get('type', 'ema')}"
        return indicators.get(key)
    if rule.indicator == "rsi":
        period = rule.params.get("period", 14) if rule.params else 14
        rsi_type = rule.params.get("type", "sma") if rule.params else "sma"
        return indicators.get(f"rsi_{period}_{rsi_type}")
    fixed = {"macd": "macd", "price": "close"}
    return indicators.get(fixed.get(rule.indicator, rule.indicator))


def resolve_ref(rule: Rule, indicators: dict[str, pd.Series]) -> pd.Series | None:
    """Resolve the cross-reference series for a rule's param."""
    if not rule.param:
        return None
    if rule.param == "signal":
        return indicators.get("signal")
    if rule.param == "close":
        return indicators.get("close")
    if rule.param.startswith("ma:"):
        parts = rule.param.split(":", 2)
        if len(parts) == 3:
            _, period, ma_type = parts
            try:
                key = f"ma_{int(period)}_{ma_type}"
                return indicators.get(key)
            except ValueError:
                return None
    return None


def eval_rule(rule: Rule, indicators: dict[str, pd.Series], i: int) -> bool:
    """Evaluate a single rule at bar index i."""
    if i < 1:
        return False
    cond = rule.condition.lower()

    s = resolve_series(rule, indicators)
    if s is None:
        return False

    v_now = s.iloc[i]
    v_prev = s.iloc[i - 1]

    if cond in ("crossover_up", "crosses_above"):
        ref = resolve_ref(rule, indicators) if rule.param else None
        if ref is not None:
            return v_prev < ref.iloc[i - 1] and v_now >= ref.iloc[i]
        elif rule.value is not None:
            return v_prev < rule.value <= v_now
    elif cond in ("crossover_down", "crosses_below"):
        ref = resolve_ref(rule, indicators) if rule.param else None
        if ref is not None:
            return v_prev > ref.iloc[i - 1] and v_now <= ref.iloc[i]
        elif rule.value is not None:
            return v_prev > rule.value >= v_now
    elif cond == "above":
        ref = resolve_ref(rule, indicators) if rule.param else None
        if ref is not None:
            return v_now > ref.iloc[i]
        elif rule.value is not None:
            return v_now > rule.value
    elif cond == "below":
        ref = resolve_ref(rule, indicators) if rule.param else None
        if ref is not None:
            return v_now < ref.iloc[i]
        elif rule.value is not None:
            return v_now < rule.value
    elif cond == "rising":
        return v_now > v_prev
    elif cond == "falling":
        return v_now < v_prev
    elif cond == "rising_over":
        lookback = int(rule.value) if rule.value is not None else 10
        if i < lookback:
            return False
        return v_now > s.iloc[i - lookback]
    elif cond == "falling_over":
        lookback = int(rule.value) if rule.value is not None else 10
        if i < lookback:
            return False
        return v_now < s.iloc[i - lookback]
    elif cond == "turns_up_below":
        if rule.value is not None:
            return v_prev < rule.value and v_now > v_prev
    elif cond == "turns_down_above":
        if rule.value is not None:
            return v_prev > rule.value and v_now < v_prev
    elif cond in ("turns_up", "turns_down"):
        lookback = max(1, int(rule.value)) if rule.value is not None else 1
        if i < lookback + 1:
            return False
        if cond == "turns_up":
            # Last `lookback` bars must all be rising, and bar before that falling
            for k in range(lookback):
                if s.iloc[i - k] - s.iloc[i - k - 1] <= 0:
                    return False
            if s.iloc[i - lookback] - s.iloc[i - lookback - 1] >= 0:
                return False
            # Min move %: must have risen at least threshold% from true trough
            if rule.threshold is not None and rule.threshold > 0:
                trough = float(s.iloc[i - lookback])
                for j in range(i - lookback - 1, -1, -1):
                    v = float(s.iloc[j])
                    if v < trough:
                        trough = v
                    else:
                        break
                if abs(trough) < 1e-12:
                    return False
                if (v_now - trough) / abs(trough) * 100 < rule.threshold:
                    return False
            return True
        else:
            # Last `lookback` bars must all be falling, and bar before that rising
            for k in range(lookback):
                if s.iloc[i - k] - s.iloc[i - k - 1] >= 0:
                    return False
            if s.iloc[i - lookback] - s.iloc[i - lookback - 1] <= 0:
                return False
            # Min move %: must have fallen at least threshold% from true peak
            if rule.threshold is not None and rule.threshold > 0:
                peak = float(s.iloc[i - lookback])
                for j in range(i - lookback - 1, -1, -1):
                    v = float(s.iloc[j])
                    if v > peak:
                        peak = v
                    else:
                        break
                if abs(peak) < 1e-12:
                    return False
                if (peak - v_now) / abs(peak) * 100 < rule.threshold:
                    return False
            return True
    elif cond == "decelerating":
        if i < 2:
            return False
        d_now = v_now - v_prev
        d_prev = v_prev - s.iloc[i - 2]
        return d_now - d_prev < 0
    elif cond == "accelerating":
        if i < 2:
            return False
        d_now = v_now - v_prev
        d_prev = v_prev - s.iloc[i - 2]
        return d_now - d_prev > 0
    return False


def eval_rules(rules: list[Rule], logic: str, indicators: dict[str, pd.Series], i: int) -> bool:
    """Evaluate a list of rules with AND/OR logic. Negated rules are inverted,
    except when i < 1 (no prior bar) where we always return False."""
    results = []
    for r in rules:
        if r.muted:
            continue
        raw = eval_rule(r, indicators, i)
        result = (not raw) if (r.negated and i >= 1) else raw
        results.append(result)
    if not results:
        return False
    return all(results) if logic == "AND" else any(results)

import numpy as np
import pandas as pd
from typing import Optional
from pydantic import BaseModel


class Rule(BaseModel):
    indicator: str       # "macd", "rsi", "price", "ema"
    condition: str       # "crossover_up", "crossover_down", "above", "below", "crosses_above", "crosses_below", "turns_up_below", "turns_down_above"
    value: Optional[float] = None   # threshold (e.g. RSI < 30)
    param: Optional[str] = None     # e.g. "signal", "ema20"
    muted: bool = False
    negated: bool = False


def compute_indicators(close: pd.Series, high: pd.Series = None, low: pd.Series = None) -> dict[str, pd.Series]:
    """Compute all indicators from a close price series. Returns dict of named series."""
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    result = {
        "macd": macd_line,
        "signal": signal_line,
        "rsi": rsi,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "close": close,
    }

    if high is not None and low is not None:
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        result["atr"] = tr.rolling(14).mean()

    return result


def eval_rule(rule: Rule, indicators: dict[str, pd.Series], i: int) -> bool:
    """Evaluate a single rule at bar index i."""
    if i < 1:
        return False
    ind = rule.indicator.lower()
    cond = rule.condition.lower()

    series_map = {
        "macd": indicators["macd"],
        "rsi": indicators["rsi"],
        "price": indicators["close"],
        "ema20": indicators["ema20"],
        "ema50": indicators["ema50"],
        "ema200": indicators["ema200"],
    }
    ref_map = {
        "signal": indicators["signal"],
        "ema20": indicators["ema20"],
        "ema50": indicators["ema50"],
        "ema200": indicators["ema200"],
        "close": indicators["close"],
    }

    s = series_map.get(ind)
    if s is None:
        return False

    v_now = s.iloc[i]
    v_prev = s.iloc[i - 1]

    if cond in ("crossover_up", "crosses_above"):
        if rule.param and rule.param in ref_map:
            ref = ref_map[rule.param]
            return v_prev < ref.iloc[i - 1] and v_now >= ref.iloc[i]
        elif rule.value is not None:
            return v_prev < rule.value <= v_now
    elif cond in ("crossover_down", "crosses_below"):
        if rule.param and rule.param in ref_map:
            ref = ref_map[rule.param]
            return v_prev > ref.iloc[i - 1] and v_now <= ref.iloc[i]
        elif rule.value is not None:
            return v_prev > rule.value >= v_now
    elif cond == "above":
        if rule.param and rule.param in ref_map:
            return v_now > ref_map[rule.param].iloc[i]
        elif rule.value is not None:
            return v_now > rule.value
    elif cond == "below":
        if rule.param and rule.param in ref_map:
            return v_now < ref_map[rule.param].iloc[i]
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

import numpy as np
import pandas as pd
from typing import Optional
from pydantic import BaseModel
from scipy.signal import savgol_filter


class Rule(BaseModel):
    indicator: str       # "macd", "rsi", "price", "ema"
    condition: str       # "crossover_up", "crossover_down", "above", "below", "crosses_above", "crosses_below", "turns_up_below", "turns_down_above"
    value: Optional[float] = None   # threshold (e.g. RSI < 30)
    param: Optional[str] = None     # e.g. "signal", "ema20"
    threshold: Optional[float] = None  # min move % for turns_up/turns_down
    muted: bool = False
    negated: bool = False


def _sg_predictive_coeffs(window: int, poly: int):
    """Compute convolution coefficients for an extrapolating S-G filter.

    Fits a polynomial of degree `poly` to the last `w` bars, then evaluates
    it (w-1)//2 bars *ahead* of the most recent data point. This compensates
    the causal filter's lag using only past data — genuine prediction, not
    lookahead.  Returns (coefficients, adjusted_window).
    """
    w = max(window, poly + 1)
    if w % 2 == 0:
        w += 1
    k = (w - 1) // 2
    x = np.arange(w, dtype=float)
    eval_pos = float(w - 1 + k)          # extrapolate k bars beyond window
    V = np.vander(x, poly + 1, increasing=True)
    pinv = np.linalg.pinv(V)
    eval_vec = np.array([eval_pos ** p for p in range(poly + 1)])
    return eval_vec @ pinv, w


def _apply_sg_predictive(series: pd.Series, window: int, poly: int) -> pd.Series:
    """S-G with forward extrapolation by (w-1)//2 bars.

    Uses only past data but estimates where the smoothed value *would* be
    at the current bar, compensating for the causal lag. Noisier than the
    causal filter but fires turns earlier.
    """
    coeffs, w = _sg_predictive_coeffs(window, poly)
    valid = series.dropna()
    if len(valid) < w:
        return pd.Series(np.nan, index=series.index)
    vals = valid.values
    conv = np.convolve(vals, coeffs[::-1], mode='full')
    out = np.full(len(vals), np.nan)
    out[w - 1:] = conv[w - 1: len(vals)]
    result = pd.Series(np.nan, index=series.index)
    result.loc[valid.index] = out
    return result


def _apply_sg(series: pd.Series, window: int, poly: int, causal: bool = False) -> pd.Series:
    """Apply Savitzky-Golay smoothing to a series, skipping NaN warmup.

    causal=True shifts the output forward by (w-1)//2 bars so each value
    only depends on past data (no lookahead). Use for backtesting.
    causal=False (default) uses the standard centered filter. Use for chart display.
    """
    w = max(window, poly + 1)
    if w % 2 == 0:
        w += 1
    valid = series.dropna()
    if len(valid) >= w:
        vals = savgol_filter(valid.values, window_length=w, polyorder=poly, mode="nearest")
        out = pd.Series(np.nan, index=series.index)
        out.loc[valid.index] = vals
        if causal:
            out = out.shift((w - 1) // 2)
        return out
    return pd.Series(np.nan, index=series.index)


def compute_indicators(close: pd.Series, high: pd.Series = None, low: pd.Series = None,
                       ma_type: str = "ema", sg8_window: int = 7, sg8_poly: int = 2,
                       sg21_window: int = 7, sg21_poly: int = 2,
                       predictive_sg: bool = False,
                       use_sg8: bool = True, use_sg21: bool = True) -> dict[str, pd.Series]:
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

    # MA8 / MA21 + Savitzky-Golay smoothed variants
    mt = ma_type.lower()
    if mt == "sma":
        ma8 = close.rolling(8).mean()
        ma21 = close.rolling(21).mean()
    elif mt == "rma":
        ma8 = close.ewm(alpha=1/8, adjust=False).mean()
        ma21 = close.ewm(alpha=1/21, adjust=False).mean()
    else:
        ma8 = close.ewm(span=8, adjust=False).mean()
        ma21 = close.ewm(span=21, adjust=False).mean()

    result["ma8"] = ma8
    result["ma21"] = ma21
    sg_fn = _apply_sg_predictive if predictive_sg else lambda s, w, p: _apply_sg(s, w, p, causal=True)
    result["ma8_sg"] = sg_fn(ma8, sg8_window, sg8_poly) if use_sg8 else ma8
    result["ma21_sg"] = sg_fn(ma21, sg21_window, sg21_poly) if use_sg21 else ma21
    result["_sg_active"] = {"ma8": use_sg8, "ma21": use_sg21}

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
        "ma8": indicators["ma8_sg"],
        "ma21": indicators["ma21_sg"],
    }
    ref_map = {
        "signal": indicators["signal"],
        "ema20": indicators["ema20"],
        "ema50": indicators["ema50"],
        "ema200": indicators["ema200"],
        "close": indicators["close"],
        "ma8": indicators["ma8_sg"],
        "ma21": indicators["ma21_sg"],
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
    elif cond in ("turns_up", "turns_down"):
        lookback = int(rule.value) if rule.value is not None else 1
        if i < lookback + 1:
            return False
        # Dead zone only when S-G is active — filters causal micro-oscillations.
        # Raw MAs turn gradually and don't need it.
        sg_active = indicators.get("_sg_active", {})
        has_sg = sg_active.get(ind, True) if isinstance(sg_active, dict) else True
        eps = abs(v_now) * 3e-5 if has_sg else 0
        if cond == "turns_up":
            # Last `lookback` bars must all be rising, and bar before that falling
            for k in range(lookback):
                if s.iloc[i - k] - s.iloc[i - k - 1] < eps:
                    return False
            if s.iloc[i - lookback] - s.iloc[i - lookback - 1] >= -eps:
                return False
            # Min move %: MA must have risen at least threshold% from turn point
            if rule.threshold is not None and rule.threshold > 0:
                trough = s.iloc[i - lookback]
                if abs(trough) < 1e-12:
                    return False
                if (v_now - trough) / abs(trough) * 100 < rule.threshold:
                    return False
            return True
        else:
            # Last `lookback` bars must all be falling, and bar before that rising
            for k in range(lookback):
                if s.iloc[i - k] - s.iloc[i - k - 1] > -eps:
                    return False
            if s.iloc[i - lookback] - s.iloc[i - lookback - 1] <= eps:
                return False
            # Min move %: MA must have fallen at least threshold% from turn point
            if rule.threshold is not None and rule.threshold > 0:
                peak = s.iloc[i - lookback]
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

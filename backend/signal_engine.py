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
                       volume: pd.Series = None,
                       rules: list[Rule] = None) -> dict[str, pd.Series]:
    """Compute indicators based on what rules require. MACD/RSI always included."""
    from indicators import compute_instance, OHLCVSeries

    if rules is None:
        rules = []

    ohlcv = OHLCVSeries(close=close, high=high if high is not None else close,
                        low=low if low is not None else close,
                        volume=volume if volume is not None else pd.Series(0, index=close.index, dtype=float))

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

    # --- Bollinger Bands ---
    bb_specs: set[tuple[int, float]] = set()
    for rule in rules:
        if rule.indicator == "bb":
            p = rule.params.get("period", 20) if rule.params else 20
            s = rule.params.get("std", 2) if rule.params else 2
            bb_specs.add((int(p), float(s)))
        # Also check param refs like bb:20:2:upper
        if rule.param and rule.param.startswith("bb:"):
            parts = rule.param.split(":")
            if len(parts) >= 3:
                try:
                    bb_specs.add((int(parts[1]), float(parts[2])))
                except ValueError:
                    pass

    for bb_period, bb_std in bb_specs:
        bb_result = compute_instance("bb", {"period": bb_period, "stddev": bb_std}, ohlcv)
        prefix = f"bb_{bb_period}_{bb_std}"
        result[f"{prefix}_upper"] = bb_result["upper"]
        result[f"{prefix}_lower"] = bb_result["lower"]
        result[f"{prefix}_middle"] = bb_result["middle"]
        # Bandwidth = (upper - lower) / middle
        result[f"{prefix}_bandwidth"] = (bb_result["upper"] - bb_result["lower"]) / bb_result["middle"]
        # %B = (close - lower) / (upper - lower)
        band_width = bb_result["upper"] - bb_result["lower"]
        result[f"{prefix}_pctb"] = (close - bb_result["lower"]) / band_width.replace(0, np.nan)

    # --- ATR (rule-specific periods beyond default 14) ---
    atr_specs: set[int] = set()
    for rule in rules:
        if rule.indicator in ("atr", "atr_pct"):
            p = rule.params.get("period", 14) if rule.params else 14
            atr_specs.add(int(p))
        if rule.param and rule.param.startswith("atr:"):
            parts = rule.param.split(":")
            if len(parts) >= 2:
                try:
                    atr_specs.add(int(parts[1]))
                except ValueError:
                    pass

    for atr_period in atr_specs:
        key = f"atr_{atr_period}"
        if key not in result:
            atr_r = compute_instance("atr", {"period": atr_period}, ohlcv)
            result[key] = atr_r["atr"]
        # ATR as % of close
        result[f"atr_pct_{atr_period}"] = result[key] / close * 100

    # Also store default ATR with keyed name for consistency
    if "atr" in result and "atr_14" not in result:
        result["atr_14"] = result["atr"]
        result["atr_pct_14"] = result["atr"] / close * 100

    # --- Volume ---
    vol_needed = any(r.indicator == "volume" for r in rules)
    vol_ref = any(r.param and r.param.startswith("volume") for r in rules)
    if vol_needed or vol_ref:
        result["volume_raw"] = ohlcv.volume
        # Compute volume SMA for each requested period
        vol_sma_periods: set[int] = set()
        for rule in rules:
            if rule.indicator == "volume" and rule.param == "sma":
                p = rule.params.get("period", 20) if rule.params else 20
                vol_sma_periods.add(int(p))
            if rule.param and rule.param.startswith("volume_sma:"):
                parts = rule.param.split(":")
                if len(parts) >= 2:
                    try:
                        vol_sma_periods.add(int(parts[1]))
                    except ValueError:
                        pass
        for vp in vol_sma_periods:
            result[f"volume_sma_{vp}"] = ohlcv.volume.rolling(vp).mean()

    # --- Stochastic ---
    stoch_specs: set[tuple[int, int, int]] = set()
    for rule in rules:
        if rule.indicator == "stochastic":
            kp = rule.params.get("k_period", 14) if rule.params else 14
            dp = rule.params.get("d_period", 3) if rule.params else 3
            sk = rule.params.get("smooth_k", 3) if rule.params else 3
            stoch_specs.add((int(kp), int(dp), int(sk)))
        if rule.param and rule.param.startswith("stoch:"):
            parts = rule.param.split(":")
            if len(parts) >= 4:
                try:
                    stoch_specs.add((int(parts[1]), int(parts[2]), int(parts[3])))
                except ValueError:
                    pass

    for kp, dp, sk in stoch_specs:
        stoch_result = compute_instance("stochastic", {"k_period": kp, "d_period": dp, "smooth_k": sk}, ohlcv)
        prefix = f"stoch_{kp}_{dp}_{sk}"
        result[f"{prefix}_k"] = stoch_result["k"]
        result[f"{prefix}_d"] = stoch_result["d"]

    # --- ADX ---
    adx_specs: set[int] = set()
    for rule in rules:
        if rule.indicator == "adx":
            p = rule.params.get("period", 14) if rule.params else 14
            adx_specs.add(int(p))
        if rule.param and rule.param.startswith("adx:"):
            parts = rule.param.split(":")
            if len(parts) >= 2:
                try:
                    adx_specs.add(int(parts[1]))
                except ValueError:
                    pass

    for adx_period in adx_specs:
        adx_result = compute_instance("adx", {"period": adx_period}, ohlcv)
        result[f"adx_{adx_period}"] = adx_result["adx"]
        result[f"adx_{adx_period}_plus_di"] = adx_result["plus_di"]
        result[f"adx_{adx_period}_minus_di"] = adx_result["minus_di"]

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
    if rule.indicator == "bb":
        p = rule.params.get("period", 20) if rule.params else 20
        s = rule.params.get("std", 2) if rule.params else 2
        band = rule.param or "upper"
        return indicators.get(f"bb_{p}_{float(s)}_{band}")
    if rule.indicator == "atr":
        p = rule.params.get("period", 14) if rule.params else 14
        return indicators.get(f"atr_{p}")
    if rule.indicator == "atr_pct":
        p = rule.params.get("period", 14) if rule.params else 14
        return indicators.get(f"atr_pct_{p}")
    if rule.indicator == "volume":
        param = rule.param or "raw"
        if param == "sma":
            p = rule.params.get("period", 20) if rule.params else 20
            return indicators.get(f"volume_sma_{p}")
        return indicators.get("volume_raw")
    if rule.indicator == "stochastic":
        kp = rule.params.get("k_period", 14) if rule.params else 14
        dp = rule.params.get("d_period", 3) if rule.params else 3
        sk = rule.params.get("smooth_k", 3) if rule.params else 3
        return indicators.get(f"stoch_{kp}_{dp}_{sk}_k")
    if rule.indicator == "adx":
        p = rule.params.get("period", 14) if rule.params else 14
        component = rule.param or "adx"
        if component == "adx":
            return indicators.get(f"adx_{p}")
        elif component == "plus_di":
            return indicators.get(f"adx_{p}_plus_di")
        elif component == "minus_di":
            return indicators.get(f"adx_{p}_minus_di")
        return indicators.get(f"adx_{p}")
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
    if rule.param == "d" and rule.indicator == "stochastic":
        kp = rule.params.get("k_period", 14) if rule.params else 14
        dp = rule.params.get("d_period", 3) if rule.params else 3
        sk = rule.params.get("smooth_k", 3) if rule.params else 3
        return indicators.get(f"stoch_{kp}_{dp}_{sk}_d")
    if rule.param.startswith("ma:"):
        parts = rule.param.split(":", 2)
        if len(parts) == 3:
            _, period, ma_type = parts
            try:
                key = f"ma_{int(period)}_{ma_type}"
                return indicators.get(key)
            except ValueError:
                return None
    # BB band as reference: bb:period:std:band
    if rule.param.startswith("bb:"):
        parts = rule.param.split(":")
        if len(parts) >= 4:
            try:
                key = f"bb_{int(parts[1])}_{float(parts[2])}_{parts[3]}"
                return indicators.get(key)
            except (ValueError, IndexError):
                return None
    # ATR as reference: atr:period
    if rule.param.startswith("atr:"):
        parts = rule.param.split(":")
        if len(parts) >= 2:
            try:
                return indicators.get(f"atr_{int(parts[1])}")
            except ValueError:
                return None
    # Volume SMA as reference: volume_sma:period
    if rule.param.startswith("volume_sma:"):
        parts = rule.param.split(":")
        if len(parts) >= 2:
            try:
                return indicators.get(f"volume_sma_{int(parts[1])}")
            except ValueError:
                return None
    # Stochastic as reference: stoch:kp:dp:sk:component
    if rule.param.startswith("stoch:"):
        parts = rule.param.split(":")
        if len(parts) >= 5:
            try:
                return indicators.get(f"stoch_{int(parts[1])}_{int(parts[2])}_{int(parts[3])}_{parts[4]}")
            except (ValueError, IndexError):
                return None
    # ADX as reference: adx:period:component
    if rule.param.startswith("adx:"):
        parts = rule.param.split(":")
        if len(parts) >= 3:
            try:
                component = parts[2]
                if component in ("plus_di", "minus_di"):
                    return indicators.get(f"adx_{int(parts[1])}_{component}")
                return indicators.get(f"adx_{int(parts[1])}")
            except (ValueError, IndexError):
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

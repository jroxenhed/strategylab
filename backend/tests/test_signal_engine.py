import pytest
import pandas as pd
from signal_engine import Rule, migrate_rule, compute_indicators, resolve_series, resolve_ref, _clamp_lookback, eval_rule, _INDICATOR_FAMILY_CAP


def test_rule_params_field_default_none():
    rule = Rule(indicator="ma", condition="turns_up")
    assert rule.params is None


def test_rule_params_field_with_ma_config():
    rule = Rule(indicator="ma", condition="turns_up", params={"period": 20, "type": "ema"})
    assert rule.params == {"period": 20, "type": "ema"}


def test_rule_params_field_ignored_for_non_ma():
    rule = Rule(indicator="rsi", condition="above", value=70)
    assert rule.params is None


def test_migrate_rule_ema20():
    old = Rule(indicator="ema20", condition="above", value=150)
    new = migrate_rule(old)
    assert new.indicator == "ma"
    assert new.params == {"period": 20, "type": "ema"}
    assert new.condition == "above"
    assert new.value == 150


def test_migrate_rule_ma8():
    old = Rule(indicator="ma8", condition="turns_up")
    new = migrate_rule(old)
    assert new.indicator == "ma"
    assert new.params == {"period": 8, "type": "sma"}


def test_migrate_rule_ema200():
    old = Rule(indicator="ema200", condition="rising")
    new = migrate_rule(old)
    assert new.indicator == "ma"
    assert new.params == {"period": 200, "type": "ema"}


def test_migrate_rule_param_ema50():
    old = Rule(indicator="price", condition="crosses_above", param="ema50")
    new = migrate_rule(old)
    assert new.indicator == "price"
    assert new.param == "ma:50:ema"


def test_migrate_rule_param_ma21():
    old = Rule(indicator="ma8", condition="above", param="ma21")
    new = migrate_rule(old)
    assert new.indicator == "ma"
    assert new.params == {"period": 8, "type": "sma"}
    assert new.param == "ma:21:sma"


def test_migrate_rule_idempotent():
    already_new = Rule(indicator="ma", condition="turns_up", params={"period": 20, "type": "ema"})
    result = migrate_rule(already_new)
    assert result.indicator == "ma"
    assert result.params == {"period": 20, "type": "ema"}


def test_migrate_rule_non_ma_unchanged():
    rule = Rule(indicator="rsi", condition="above", value=70)
    result = migrate_rule(rule)
    assert result.indicator == "rsi"
    assert result.params is None
    assert result.param is None


def test_migrate_rule_param_close_unchanged():
    rule = Rule(indicator="price", condition="above", param="close")
    result = migrate_rule(rule)
    assert result.param == "close"


def test_migrate_rule_param_signal_unchanged():
    rule = Rule(indicator="macd", condition="crossover_up", param="signal")
    result = migrate_rule(rule)
    assert result.param == "signal"


def _make_close(n=100):
    """Generate a simple close price series for testing."""
    return pd.Series([100.0 + i * 0.1 for i in range(n)])


def test_compute_indicators_with_ma_rules():
    close = _make_close()
    rules = [Rule(indicator="ma", condition="turns_up", params={"period": 20, "type": "ema"})]
    indicators = compute_indicators(close, rules=rules)
    assert "ma_20_ema" in indicators
    assert len(indicators["ma_20_ema"]) == len(close)


def test_compute_indicators_with_param_ref():
    close = _make_close()
    rules = [Rule(indicator="price", condition="crosses_above", param="ma:50:sma")]
    indicators = compute_indicators(close, rules=rules)
    assert "ma_50_sma" in indicators


def test_compute_indicators_deduplicates_ma_specs():
    close = _make_close()
    rules = [
        Rule(indicator="ma", condition="turns_up", params={"period": 20, "type": "ema"}),
        Rule(indicator="ma", condition="above", params={"period": 20, "type": "ema"}, param="ma:50:ema"),
    ]
    indicators = compute_indicators(close, rules=rules)
    assert "ma_20_ema" in indicators
    assert "ma_50_ema" in indicators


def test_compute_indicators_always_has_macd_rsi():
    close = _make_close()
    indicators = compute_indicators(close, rules=[])
    assert "macd" in indicators
    assert "signal" in indicators
    assert "rsi_14_sma" in indicators
    assert "close" in indicators


def test_resolve_series_ma():
    rule = Rule(indicator="ma", condition="turns_up", params={"period": 20, "type": "ema"})
    indicators = {"ma_20_ema": pd.Series([1, 2, 3])}
    result = resolve_series(rule, indicators)
    assert list(result) == [1, 2, 3]


def test_resolve_series_fixed():
    for ind, key in [("macd", "macd"), ("rsi", "rsi_14_sma"), ("price", "close")]:
        rule = Rule(indicator=ind, condition="above", value=50)
        indicators = {key: pd.Series([10, 20])}
        result = resolve_series(rule, indicators)
        assert result is not None


def test_resolve_ref_ma_encoded():
    rule = Rule(indicator="price", condition="above", param="ma:50:ema")
    indicators = {"ma_50_ema": pd.Series([100, 200])}
    result = resolve_ref(rule, indicators)
    assert list(result) == [100, 200]


def test_resolve_ref_signal():
    rule = Rule(indicator="macd", condition="crossover_up", param="signal")
    indicators = {"signal": pd.Series([1, 2])}
    assert resolve_ref(rule, indicators) is not None


def test_resolve_ref_close():
    rule = Rule(indicator="ma", condition="above", params={"period": 20, "type": "ema"}, param="close")
    indicators = {"close": pd.Series([100])}
    assert resolve_ref(rule, indicators) is not None


def test_resolve_ref_none_when_no_param():
    rule = Rule(indicator="rsi", condition="above", value=70)
    assert resolve_ref(rule, {}) is None


def test_compute_indicators_rsi_custom_period():
    close = _make_close()
    rules = [Rule(indicator="rsi", condition="above", value=70, params={"period": 21, "type": "wilder"})]
    indicators = compute_indicators(close, rules=rules)
    assert "rsi_21_wilder" in indicators
    assert "rsi_14_sma" not in indicators


def test_resolve_series_rsi_with_params():
    rule = Rule(indicator="rsi", condition="above", value=70, params={"period": 21, "type": "wilder"})
    indicators = {"rsi_21_wilder": pd.Series([50, 60])}
    result = resolve_series(rule, indicators)
    assert list(result) == [50, 60]


def test_resolve_series_rsi_default_period():
    rule = Rule(indicator="rsi", condition="above", value=70)
    indicators = {"rsi_14_sma": pd.Series([50, 60])}
    result = resolve_series(rule, indicators)
    assert list(result) == [50, 60]


def test_no_sg_active_in_indicators():
    close = _make_close()
    rules = [Rule(indicator="ma", condition="turns_up", params={"period": 8, "type": "sma"})]
    indicators = compute_indicators(close, rules=rules)
    assert "_sg_active" not in indicators
    assert "ma8_sg" not in indicators
    assert "ma21_sg" not in indicators


def test_migrate_rules_in_bot_config_dict():
    """Simulate what BotManager.load() does with legacy bot configs."""
    legacy_rules = [
        {"indicator": "ema20", "condition": "rising"},
        {"indicator": "ma8", "condition": "turns_up", "param": "ma21"},
    ]
    migrated = [migrate_rule(Rule(**r)) for r in legacy_rules]
    assert migrated[0].indicator == "ma"
    assert migrated[0].params == {"period": 20, "type": "ema"}
    assert migrated[1].indicator == "ma"
    assert migrated[1].params == {"period": 8, "type": "sma"}
    assert migrated[1].param == "ma:21:sma"


# ---------------------------------------------------------------------------
# F129 — _clamp_lookback helper
# ---------------------------------------------------------------------------

def test_clamp_lookback_caps_at_500():
    assert _clamp_lookback(1000, 10) == 500


def test_clamp_lookback_passes_through_small_value():
    assert _clamp_lookback(50, 10) == 50


def test_clamp_lookback_returns_default_for_none():
    assert _clamp_lookback(None, 10) == 10


def test_clamp_lookback_exact_boundary():
    assert _clamp_lookback(500, 10) == 500
    assert _clamp_lookback(501, 10) == 500


def test_eval_rule_rising_over_oversized_lookback_no_oob():
    """F129: value=1000 on a 50-bar series must not raise IndexError."""
    close = pd.Series(range(50), dtype=float)
    indicators = compute_indicators(close, rules=[])
    rule = Rule(indicator="price", condition="rising_over", value=1000)
    # i=49 (last bar): clamped lookback=500 > i=49, so guard triggers → False
    result = eval_rule(rule, indicators, 49)
    assert isinstance(result, bool)
    assert result is False


def test_eval_rule_turns_up_oversized_lookback_no_oob():
    """F129: turns_up with value=1000 on a 50-bar series must not raise."""
    close = pd.Series(list(range(25)) + list(range(25, 0, -1)), dtype=float)
    indicators = compute_indicators(close, rules=[])
    rule = Rule(indicator="price", condition="turns_up", value=1000)
    result = eval_rule(rule, indicators, 49)
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# F130 — per-family indicator cap in compute_indicators
# ---------------------------------------------------------------------------

def _make_close(n: int = 100) -> pd.Series:
    return pd.Series(range(1, n + 1), dtype=float)


def test_compute_indicators_rejects_21_distinct_ma_specs():
    close = _make_close()
    rules = [
        Rule(indicator="ma", condition="above", value=1, params={"period": p, "type": "ema"})
        for p in range(1, 22)  # 21 distinct periods
    ]
    with pytest.raises(ValueError, match="Too many distinct MA"):
        compute_indicators(close, rules=rules)


def test_compute_indicators_accepts_exactly_20_distinct_ma_specs():
    close = _make_close()
    rules = [
        Rule(indicator="ma", condition="above", value=1, params={"period": p, "type": "ema"})
        for p in range(2, 22)  # exactly 20 (periods 2..21; period 1 is below indicators.py min)
    ]
    result = compute_indicators(close, rules=rules)
    ma_keys = [k for k in result if k.startswith("ma_")]
    assert len(ma_keys) == 20


def test_compute_indicators_rejects_21_distinct_rsi_specs():
    close = _make_close()
    rules = [
        Rule(indicator="rsi", condition="above", value=50, params={"period": p, "type": "sma"})
        for p in range(2, 23)  # 21 distinct periods
    ]
    with pytest.raises(ValueError, match="Too many distinct RSI"):
        compute_indicators(close, rules=rules)


def test_compute_indicators_rejects_21_distinct_bb_specs():
    close = _make_close()
    rules = [
        Rule(indicator="bb", condition="above", value=1, params={"period": p, "std": 2})
        for p in range(5, 26)  # 21 distinct periods
    ]
    with pytest.raises(ValueError, match="Too many distinct BB"):
        compute_indicators(close, rules=rules)


def test_clamp_lookback_floors_at_zero():
    """Fix 2 (F129 review): negative value must clamp to 0, not pass through."""
    assert _clamp_lookback(-5, 10) == 0
    assert _clamp_lookback(-1, 10) == 0


# ---------------------------------------------------------------------------
# F130 — missing family coverage: ATR, stochastic, ADX, volume SMA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("indicator,build_rule", [
    (
        "atr",
        lambda p: Rule(indicator="atr", condition="above", value=1, params={"period": p}),
    ),
    (
        "adx",
        lambda p: Rule(indicator="adx", condition="above", value=25, params={"period": p}),
    ),
])
def test_compute_indicators_rejects_21_distinct_specs_parametrized(indicator, build_rule):
    """ATR and ADX families: 21 distinct periods must raise ValueError."""
    close = _make_close(n=300)
    high = close + 1
    low = close - 1
    rules = [build_rule(p) for p in range(2, 23)]  # 21 distinct periods
    with pytest.raises(ValueError, match="Too many distinct"):
        compute_indicators(close, high=high, low=low, rules=rules)


def test_compute_indicators_rejects_21_distinct_stochastic_specs():
    """21 distinct (k_period, d_period, smooth_k) tuples must raise ValueError."""
    close = _make_close(n=300)
    high = close + 1
    low = close - 1
    # vary k_period to produce 21 unique tuples
    rules = [
        Rule(indicator="stochastic", condition="above", value=50,
             params={"k_period": p, "d_period": 3, "smooth_k": 3})
        for p in range(2, 23)
    ]
    with pytest.raises(ValueError, match="Too many distinct stochastic"):
        compute_indicators(close, high=high, low=low, rules=rules)


def test_compute_indicators_rejects_21_distinct_volume_sma_specs():
    """21 distinct volume SMA periods must raise ValueError."""
    close = _make_close(n=300)
    volume = pd.Series([1_000_000.0] * 300)
    # param="sma" triggers vol_sma_periods accumulation
    rules = [
        Rule(indicator="volume", condition="above", value=1,
             param="sma", params={"period": p})
        for p in range(5, 26)
    ]
    with pytest.raises(ValueError, match="Too many distinct volume SMA"):
        compute_indicators(close, volume=volume, rules=rules)


# ---------------------------------------------------------------------------
# Fix 3 / F-COR-1 — eval_rule auto-migrates legacy indicators at dispatch time
# ---------------------------------------------------------------------------

def _make_close(n: int = 100, base: float = 100.0) -> pd.Series:
    import numpy as np
    rng = np.random.default_rng(42)
    prices = base + rng.normal(0, 1, n).cumsum()
    return pd.Series(prices, dtype=float)


def test_eval_rule_legacy_ema20_migrates_transparently():
    """eval_rule with indicator='ema20' (legacy) must produce the same result
    as the canonical Rule(indicator='ma', params={'period': 20, 'type': 'ema'})."""
    n = 50
    close = _make_close(n=n, base=100.0)

    # Populate the indicators dict with the canonical key that resolve_series expects
    ema_series = close.ewm(span=20, adjust=False).mean()
    indicators = {"ma_20_ema": ema_series}

    # Legacy form — migrate_rule will turn indicator="ema20" → indicator="ma", params={...}
    legacy_rule = Rule(indicator="ema20", condition="above", value=0)
    # Canonical form for comparison
    canonical_rule = Rule(indicator="ma", condition="above", value=0, params={"period": 20, "type": "ema"})

    i = 30  # well past warmup
    result_legacy = eval_rule(legacy_rule, indicators, i=i)
    result_canonical = eval_rule(canonical_rule, indicators, i=i)

    assert result_legacy == result_canonical, (
        f"Legacy ema20 rule ({result_legacy}) must match canonical ma rule ({result_canonical}) at bar {i}"
    )

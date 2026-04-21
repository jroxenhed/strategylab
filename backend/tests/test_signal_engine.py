import pytest
import pandas as pd
from signal_engine import Rule, migrate_rule, compute_indicators, resolve_series, resolve_ref


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
    assert "rsi" in indicators
    assert "close" in indicators


def test_resolve_series_ma():
    rule = Rule(indicator="ma", condition="turns_up", params={"period": 20, "type": "ema"})
    indicators = {"ma_20_ema": pd.Series([1, 2, 3])}
    result = resolve_series(rule, indicators)
    assert list(result) == [1, 2, 3]


def test_resolve_series_fixed():
    for ind, key in [("macd", "macd"), ("rsi", "rsi"), ("price", "close")]:
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

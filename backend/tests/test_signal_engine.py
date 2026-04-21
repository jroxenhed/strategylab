import pytest
from signal_engine import Rule, migrate_rule


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

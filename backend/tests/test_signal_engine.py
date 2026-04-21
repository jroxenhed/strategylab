import pytest
from signal_engine import Rule


def test_rule_params_field_default_none():
    rule = Rule(indicator="ma", condition="turns_up")
    assert rule.params is None


def test_rule_params_field_with_ma_config():
    rule = Rule(indicator="ma", condition="turns_up", params={"period": 20, "type": "ema"})
    assert rule.params == {"period": 20, "type": "ema"}


def test_rule_params_field_ignored_for_non_ma():
    rule = Rule(indicator="rsi", condition="above", value=70)
    assert rule.params is None

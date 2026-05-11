from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))
import pytest
from pydantic import ValidationError
from models import StrategyRequest, RegimeConfig
from bot_manager import BotConfig


def make_req(**kwargs):
    defaults = dict(
        ticker="AAPL",
        buy_rules=[],
        sell_rules=[],
    )
    return StrategyRequest(**{**defaults, **kwargs})


def test_position_size_clamps_large_value():
    req = make_req(position_size=10000)
    assert req.position_size == 1.0


def test_position_size_clamps_above_one():
    req = make_req(position_size=1.5)
    assert req.position_size == 1.0


def test_position_size_clamps_negative():
    req = make_req(position_size=-1)
    assert req.position_size == 0.01


def test_position_size_clamps_zero():
    req = make_req(position_size=0)
    assert req.position_size == 0.01


def test_position_size_accepts_one():
    req = make_req(position_size=1.0)
    assert req.position_size == 1.0


def test_position_size_accepts_fraction():
    req = make_req(position_size=0.5)
    assert req.position_size == 0.5


def test_position_size_default_is_one():
    req = make_req()
    assert req.position_size == 1.0


def test_strategy_request_direction_defaults_long():
    req = make_req()
    assert req.direction == "long"


def test_strategy_request_direction_accepts_short():
    req = make_req(direction="short")
    assert req.direction == "short"


def make_bot_config(**kwargs):
    defaults = dict(
        strategy_name="Test Bot",
        symbol="AAPL",
        interval="5m",
        buy_rules=[],
        sell_rules=[],
        allocated_capital=1000.0,
    )
    return BotConfig(**{**defaults, **kwargs})


def test_bot_config_direction_defaults_long():
    cfg = make_bot_config()
    assert cfg.direction == "long"


def test_bot_config_direction_accepts_short():
    cfg = make_bot_config(direction="short")
    assert cfg.direction == "short"


# ---------------------------------------------------------------------------
# F128: StrategyRequest + RegimeConfig rule-list caps
# ---------------------------------------------------------------------------

_STUB_RULE = {"indicator": "price", "condition": "above", "value": 1}


@pytest.mark.parametrize("field", ["buy_rules", "sell_rules"])
def test_strategy_request_rejects_101_rules(field):
    """F128: >100 rules on primary buy/sell lists → ValidationError."""
    with pytest.raises(ValidationError, match="too_long"):
        make_req(**{field: [_STUB_RULE] * 101})


@pytest.mark.parametrize("field", ["buy_rules", "sell_rules"])
def test_strategy_request_accepts_exactly_100_rules(field):
    """F128 boundary: exactly 100 rules is accepted (cap is inclusive)."""
    rules = [_STUB_RULE] * 100
    req = make_req(**{field: rules})
    actual = getattr(req, field)
    assert len(actual) == 100
    assert actual[0].indicator == "price"
    assert actual[-1].value == 1


@pytest.mark.parametrize("field", [
    "long_buy_rules", "long_sell_rules",
    "short_buy_rules", "short_sell_rules",
])
def test_strategy_request_optional_rule_lists_accept_none(field):
    """F128: optional regime rule lists default to None and accept None explicitly."""
    req = make_req(**{field: None})
    assert getattr(req, field) is None


@pytest.mark.parametrize("field", [
    "long_buy_rules", "long_sell_rules",
    "short_buy_rules", "short_sell_rules",
])
def test_strategy_request_optional_rule_lists_reject_101(field):
    """F128: optional regime rule lists enforce same 100-rule cap when non-None."""
    with pytest.raises(ValidationError, match="too_long"):
        make_req(**{field: [_STUB_RULE] * 101})


@pytest.mark.parametrize("field", [
    "long_buy_rules", "long_sell_rules",
    "short_buy_rules", "short_sell_rules",
])
def test_strategy_request_optional_rule_lists_accept_exactly_100(field):
    """F128 boundary: exactly 100 rules on optional lists is accepted."""
    rules = [_STUB_RULE] * 100
    req = make_req(**{field: rules})
    actual = getattr(req, field)
    assert actual is not None
    assert len(actual) == 100
    assert actual[0].indicator == "price"
    assert actual[-1].value == 1


def test_regime_config_rejects_51_rules():
    """F128: RegimeConfig.rules capped at 50 (filter budget, smaller than primary)."""
    with pytest.raises(ValidationError, match="too_long"):
        RegimeConfig(rules=[_STUB_RULE] * 51)


def test_regime_config_accepts_exactly_50_rules():
    """F128 boundary: exactly 50 rules is accepted."""
    rules = [_STUB_RULE] * 50
    cfg = RegimeConfig(rules=rules)
    assert len(cfg.rules) == 50
    assert cfg.rules[0].indicator == "price"
    assert cfg.rules[-1].value == 1

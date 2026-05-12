from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))
import pytest
from pydantic import ValidationError
from models import StrategyRequest, RegimeConfig
from signal_engine import Rule
from bot_manager import BotConfig
from tests.conftest import _STUB_RULE  # noqa: F401 — canonical stub (F142)


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
# StrategyRequest + RegimeConfig rule-list caps
# ---------------------------------------------------------------------------

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
    assert actual[0].indicator == "rsi"
    assert actual[-1].value == 50


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
    assert actual[0].indicator == "rsi"
    assert actual[-1].value == 50


def test_regime_config_rejects_51_rules():
    """F128: RegimeConfig.rules capped at 50 (filter budget, smaller than primary)."""
    with pytest.raises(ValidationError, match="too_long"):
        RegimeConfig(rules=[_STUB_RULE] * 51)


def test_regime_config_accepts_exactly_50_rules():
    """F128 boundary: exactly 50 rules is accepted."""
    rules = [_STUB_RULE] * 50
    cfg = RegimeConfig(rules=rules)
    assert len(cfg.rules) == 50
    assert cfg.rules[0].indicator == "rsi"
    assert cfg.rules[-1].value == 50


# ---------------------------------------------------------------------------
# SymbolField on StrategyRequest.ticker and BotConfig.symbol
# ---------------------------------------------------------------------------

def test_strategy_request_normalizes_ticker():
    """F95: lowercase + whitespace ticker is normalized to uppercase."""
    req = make_req(ticker="  aapl  ")
    assert req.ticker == "AAPL"


def test_strategy_request_rejects_invalid_ticker():
    """F95: ticker with disallowed characters raises ValidationError."""
    with pytest.raises(ValidationError):
        make_req(ticker="AA@PL")


def test_bot_config_normalizes_symbol():
    """F95: lowercase + whitespace symbol is normalized to uppercase."""
    cfg = make_bot_config(symbol="  msft  ")
    assert cfg.symbol == "MSFT"


def test_bot_config_rejects_invalid_symbol():
    """F95: symbol with disallowed characters raises ValidationError."""
    with pytest.raises(ValidationError):
        make_bot_config(symbol="AA@PL")


# ---------------------------------------------------------------------------
# F184: Interval "60m" → "1h" normalisation
# ---------------------------------------------------------------------------

def test_strategy_request_normalizes_60m_to_1h():
    """F184: interval='60m' is silently normalised to '1h' on StrategyRequest."""
    req = make_req(interval="60m")
    assert req.interval == "1h"


def test_strategy_request_preserves_1h():
    """F184: interval='1h' is preserved as-is."""
    req = make_req(interval="1h")
    assert req.interval == "1h"


def test_bot_config_normalizes_60m_to_1h():
    """F184: interval='60m' is silently normalised to '1h' on BotConfig."""
    cfg = make_bot_config(interval="60m")
    assert cfg.interval == "1h"


# ---------------------------------------------------------------------------
# F106: Rule.indicator and Rule.condition allowlist (Literal types)
# ---------------------------------------------------------------------------

def test_rule_accepts_valid_indicator_and_condition():
    """F106: a canonical indicator + condition pair is accepted without error."""
    rule = Rule(indicator="rsi", condition="above", value=50)
    assert rule.indicator == "rsi"
    assert rule.condition == "above"


def test_rule_rejects_invalid_indicator():
    """F106: an unrecognised indicator raises ValidationError at construction time."""
    with pytest.raises(ValidationError):
        Rule(indicator="bogus", condition="above", value=50)


def test_rule_rejects_invalid_condition():
    """F106: an unrecognised condition raises ValidationError at construction time."""
    with pytest.raises(ValidationError):
        Rule(indicator="rsi", condition="zig_zag", value=50)


def test_rule_all_canonical_indicators_accepted():
    """F106: every indicator in the allowlist is accepted by Pydantic."""
    indicators = [
        "rsi", "macd", "ma", "bb", "atr", "atr_pct",
        "volume", "stochastic", "adx", "price",
    ]
    for ind in indicators:
        rule = Rule(indicator=ind, condition="above", value=1)
        assert rule.indicator == ind


def test_rule_all_canonical_conditions_accepted():
    """F106: every condition in the allowlist is accepted by Pydantic."""
    conditions = [
        "above", "below",
        "crossover_up", "crossover_down",
        "crosses_above", "crosses_below",
        "rising", "falling",
        "rising_over", "falling_over",
        "turns_up", "turns_down",
        "turns_up_below", "turns_down_above",
        "decelerating", "accelerating",
    ]
    for cond in conditions:
        rule = Rule(indicator="rsi", condition=cond, value=50)
        assert rule.condition == cond

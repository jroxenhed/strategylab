"""Unit tests for _apply_param in routes/backtest_sweep.py.

Covers all supported param paths, immutability, and failure modes.
Regression for F187: param-substitution correctness in regime and non-regime mode.
"""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pytest
from fastapi import HTTPException

from models import StrategyRequest, TrailingStopConfig
from signal_engine import Rule
from routes.backtest_sweep import _apply_param


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_base(
    buy_rules=None,
    sell_rules=None,
    long_buy_rules=None,
    long_sell_rules=None,
    short_buy_rules=None,
    short_sell_rules=None,
) -> StrategyRequest:
    """Return a minimal StrategyRequest for _apply_param tests."""
    return StrategyRequest(
        ticker="AAPL",
        start="2023-01-01",
        end="2024-01-01",
        interval="1d",
        buy_rules=buy_rules or [Rule(indicator="rsi", condition="below", value=30.0)],
        sell_rules=sell_rules or [Rule(indicator="rsi", condition="above", value=70.0)],
        long_buy_rules=long_buy_rules,
        long_sell_rules=long_sell_rules,
        short_buy_rules=short_buy_rules,
        short_sell_rules=short_sell_rules,
    )


# ---------------------------------------------------------------------------
# Non-regime rule paths
# ---------------------------------------------------------------------------

def test_apply_param_buy_rule_value():
    """buy_rule_0_value updates buy_rules[0].value."""
    base = _make_base()
    result = _apply_param(base, "buy_rule_0_value", 55.0)
    assert result.buy_rules[0].value == 55.0


def test_apply_param_sell_rule_value():
    """sell_rule_0_value updates sell_rules[0].value."""
    base = _make_base()
    result = _apply_param(base, "sell_rule_0_value", 42.0)
    assert result.sell_rules[0].value == 42.0


def test_apply_param_buy_rule_params():
    """buy_rule_0_params_period updates buy_rules[0].params['period']."""
    buy_rules = [Rule(indicator="rsi", condition="below", value=30.0, params={"period": 14})]
    base = _make_base(buy_rules=buy_rules)
    result = _apply_param(base, "buy_rule_0_params_period", 20.0)
    assert result.buy_rules[0].params["period"] == 20


def test_apply_param_sell_rule_params():
    """sell_rule_0_params_period updates sell_rules[0].params['period']."""
    sell_rules = [Rule(indicator="rsi", condition="above", value=70.0, params={"period": 14})]
    base = _make_base(sell_rules=sell_rules)
    result = _apply_param(base, "sell_rule_0_params_period", 10.0)
    assert result.sell_rules[0].params["period"] == 10


# ---------------------------------------------------------------------------
# Regime-prefixed paths
# ---------------------------------------------------------------------------

def test_apply_param_long_buy_rule_value():
    """long_buy_rule_0_value updates long_buy_rules[0].value (regime path)."""
    long_buy = [Rule(indicator="rsi", condition="below", value=30.0)]
    base = _make_base(long_buy_rules=long_buy)
    result = _apply_param(base, "long_buy_rule_0_value", 45.0)
    assert result.long_buy_rules[0].value == 45.0


def test_apply_param_short_sell_rule_value():
    """short_sell_rule_0_value updates short_sell_rules[0].value (regime path)."""
    short_sell = [Rule(indicator="rsi", condition="above", value=70.0)]
    base = _make_base(short_sell_rules=short_sell)
    result = _apply_param(base, "short_sell_rule_0_value", 60.0)
    assert result.short_sell_rules[0].value == 60.0


def test_apply_param_long_buy_rule_params():
    """long_buy_rule_0_params_period updates long_buy_rules[0].params['period'] (regime path)."""
    long_buy = [Rule(indicator="rsi", condition="below", value=30.0, params={"period": 14})]
    base = _make_base(long_buy_rules=long_buy)
    result = _apply_param(base, "long_buy_rule_0_params_period", 7.0)
    assert result.long_buy_rules[0].params["period"] == 7


# ---------------------------------------------------------------------------
# Top-level scalar paths
# ---------------------------------------------------------------------------

def test_apply_param_slippage_bps():
    """slippage_bps top-level path is updated correctly."""
    base = _make_base()
    result = _apply_param(base, "slippage_bps", 5.0)
    assert result.slippage_bps == 5.0


def test_apply_param_stop_loss_pct():
    """stop_loss_pct top-level path is updated correctly."""
    base = _make_base()
    result = _apply_param(base, "stop_loss_pct", 3.0)
    assert result.stop_loss_pct == 3.0


# ---------------------------------------------------------------------------
# Index > 0
# ---------------------------------------------------------------------------

def test_apply_param_buy_rule_index_1():
    """buy_rule_1_value targets the second buy rule (index 1)."""
    buy_rules = [
        Rule(indicator="rsi", condition="below", value=30.0),
        Rule(indicator="rsi", condition="below", value=50.0),
    ]
    base = _make_base(buy_rules=buy_rules)
    result = _apply_param(base, "buy_rule_1_value", 25.0)
    assert result.buy_rules[1].value == 25.0
    # First rule must be untouched
    assert result.buy_rules[0].value == 30.0


# ---------------------------------------------------------------------------
# Immutability — base must never be mutated
# ---------------------------------------------------------------------------

def test_apply_param_buy_rule_does_not_mutate_base():
    """Applying buy_rule_0_value must not mutate the original StrategyRequest."""
    base = _make_base()
    original_value = base.buy_rules[0].value
    _apply_param(base, "buy_rule_0_value", 99.0)
    assert base.buy_rules[0].value == original_value


def test_apply_param_sell_rule_does_not_mutate_base():
    """Applying sell_rule_0_value must not mutate the original StrategyRequest."""
    base = _make_base()
    original_value = base.sell_rules[0].value
    _apply_param(base, "sell_rule_0_value", 11.0)
    assert base.sell_rules[0].value == original_value


def test_apply_param_long_buy_rule_does_not_mutate_base():
    """Applying long_buy_rule_0_value must not mutate the original StrategyRequest."""
    long_buy = [Rule(indicator="rsi", condition="below", value=30.0)]
    base = _make_base(long_buy_rules=long_buy)
    original_value = base.long_buy_rules[0].value
    _apply_param(base, "long_buy_rule_0_value", 77.0)
    assert base.long_buy_rules[0].value == original_value


def test_apply_param_stop_loss_does_not_mutate_base():
    """Applying stop_loss_pct must not mutate the original StrategyRequest."""
    base = _make_base()
    _apply_param(base, "stop_loss_pct", 8.0)
    assert base.stop_loss_pct is None


def test_apply_param_slippage_does_not_mutate_base():
    """Applying slippage_bps must not mutate the original StrategyRequest."""
    base = _make_base()
    original = base.slippage_bps
    _apply_param(base, "slippage_bps", 10.0)
    assert base.slippage_bps == original


# ---------------------------------------------------------------------------
# params=None rule — dict is created from scratch
# ---------------------------------------------------------------------------

def test_apply_param_params_none_rule_creates_dict():
    """A rule with params=None gets a new params dict when params_<key> is applied."""
    buy_rules = [Rule(indicator="rsi", condition="below", value=30.0, params=None)]
    base = _make_base(buy_rules=buy_rules)
    result = _apply_param(base, "buy_rule_0_params_period", 21.0)
    assert result.buy_rules[0].params == {"period": 21}


# ---------------------------------------------------------------------------
# Failure modes — must raise HTTPException(400)
# ---------------------------------------------------------------------------

def test_apply_param_out_of_bounds_index_raises():
    """An index >= len(rules) must raise HTTPException(400)."""
    base = _make_base()  # buy_rules has 1 rule (index 0 only)
    with pytest.raises(HTTPException) as exc_info:
        _apply_param(base, "buy_rule_1_value", 50.0)
    assert exc_info.value.status_code == 400


def test_apply_param_malformed_path_raises():
    """An unsupported/malformed param_path must raise HTTPException(400)."""
    base = _make_base()
    with pytest.raises(HTTPException) as exc_info:
        _apply_param(base, "totally_unsupported_path", 1.0)
    assert exc_info.value.status_code == 400


def test_apply_param_regime_list_none_raises():
    """long_buy_rule_0_value when long_buy_rules is None must raise HTTPException(400)."""
    base = _make_base()  # long_buy_rules defaults to None
    with pytest.raises(HTTPException) as exc_info:
        _apply_param(base, "long_buy_rule_0_value", 40.0)
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Non-integer float params (Item 4 — F187 review fix)
# ---------------------------------------------------------------------------

def test_apply_param_buy_rule_params_non_integer_float():
    """buy_rule_0_params_period with a non-integer float stays as float (else branch in _apply_param)."""
    buy_rules = [Rule(indicator="rsi", condition="below", value=30.0, params={"period": 14})]
    base = _make_base(buy_rules=buy_rules)
    result = _apply_param(base, "buy_rule_0_params_period", 14.5)
    assert result.buy_rules[0].params["period"] == 14.5


# ---------------------------------------------------------------------------
# trailing_stop_value paths (Item 5 — F187 review fix)
# ---------------------------------------------------------------------------

def test_apply_param_trailing_stop_value_happy_path():
    """trailing_stop_value updates trailing_stop.value when trailing_stop is configured."""
    base = _make_base()
    base_with_ts = base.model_copy(
        update={"trailing_stop": TrailingStopConfig(value=2.0)}
    )
    result = _apply_param(base_with_ts, "trailing_stop_value", 5.0)
    assert result.trailing_stop is not None
    assert result.trailing_stop.value == 5.0


def test_apply_param_trailing_stop_value_none_raises():
    """trailing_stop_value raises HTTPException(400) when trailing_stop is None."""
    base = _make_base()  # trailing_stop defaults to None
    with pytest.raises(HTTPException) as exc_info:
        _apply_param(base, "trailing_stop_value", 3.0)
    assert exc_info.value.status_code == 400

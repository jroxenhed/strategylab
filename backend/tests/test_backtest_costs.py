"""Unit tests for per-leg commission and borrow-cost helpers."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

from datetime import datetime
from models import StrategyRequest
from routes.backtest import per_leg_commission, borrow_cost
from signal_engine import Rule


def _req(**kw) -> StrategyRequest:
    defaults = dict(
        ticker="X", buy_rules=[Rule(indicator="price", condition="below", value=1)],
        sell_rules=[Rule(indicator="price", condition="above", value=1)],
    )
    return StrategyRequest(**{**defaults, **kw})


def test_commission_uses_per_share_when_above_min():
    assert abs(per_leg_commission(200, _req()) - 0.70) < 1e-9


def test_commission_clamps_to_min_when_below():
    assert per_leg_commission(10, _req()) == 0.35


def test_commission_exact_boundary():
    assert abs(per_leg_commission(100, _req()) - 0.35) < 1e-9


def test_borrow_zero_for_long():
    entry = datetime(2024, 1, 1)
    exit_ = datetime(2024, 1, 6)
    assert borrow_cost(100, 50.0, entry, exit_, "long", _req()) == 0.0


def test_borrow_zero_when_rate_is_zero():
    entry = datetime(2024, 1, 1)
    exit_ = datetime(2024, 1, 6)
    assert borrow_cost(100, 50.0, entry, exit_, "short", _req(borrow_rate_annual=0.0)) == 0.0


def test_borrow_short_5_day_hold():
    entry = datetime(2024, 1, 1)
    exit_ = datetime(2024, 1, 6)
    expected = 5000 * (0.5 / 100 / 365) * 5
    assert abs(borrow_cost(100, 50.0, entry, exit_, "short", _req()) - expected) < 1e-9


def test_borrow_fractional_intraday_hold():
    entry = datetime(2024, 1, 1, 10, 0, 0)
    exit_ = datetime(2024, 1, 1, 10, 30, 0)
    expected = (100 * 50.0) * (0.5 / 100 / 365) * (30 * 60 / 86400)
    assert abs(borrow_cost(100, 50.0, entry, exit_, "short", _req()) - expected) < 1e-9

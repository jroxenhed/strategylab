from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))
from routes.backtest import StrategyRequest
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

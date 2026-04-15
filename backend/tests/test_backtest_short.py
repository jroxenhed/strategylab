"""Tests for short-direction backtesting logic."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pandas as pd
import numpy as np
from unittest.mock import patch
from models import StrategyRequest
from routes.backtest import run_backtest
from signal_engine import Rule


def _make_df(prices: list[float]) -> pd.DataFrame:
    """Create a minimal OHLCV DataFrame from close prices."""
    dates = pd.date_range("2024-01-01", periods=len(prices), freq="D")
    return pd.DataFrame({
        "Open": prices,
        "High": [p * 1.01 for p in prices],
        "Low": [p * 0.99 for p in prices],
        "Close": prices,
        "Volume": [1000000] * len(prices),
    }, index=dates)


def _req_short(**kwargs) -> StrategyRequest:
    defaults = dict(
        ticker="TEST", direction="short",
        buy_rules=[Rule(indicator="price", condition="below", value=999)],
        sell_rules=[Rule(indicator="price", condition="below", value=91)],
        initial_capital=10000.0, position_size=1.0,
    )
    return StrategyRequest(**{**defaults, **kwargs})


@patch("routes.backtest._fetch")
def test_short_trade_types_are_short_and_cover(mock_fetch):
    """Short trades should have type 'short' for entry and 'cover' for exit."""
    mock_fetch.return_value = _make_df([100, 100, 90, 90])
    result = run_backtest(_req_short())
    types = [t["type"] for t in result["trades"]]
    assert "short" in types, f"Expected 'short' in trade types, got {types}"
    assert "cover" in types, f"Expected 'cover' in trade types, got {types}"
    assert "buy" not in types
    assert "sell" not in types


@patch("routes.backtest._fetch")
def test_short_pnl_positive_when_price_drops(mock_fetch):
    """Short at ~100, price drops to 90, PnL should be positive."""
    mock_fetch.return_value = _make_df([100, 100, 90, 90])
    result = run_backtest(_req_short())
    cover_trades = [t for t in result["trades"] if t["type"] == "cover"]
    assert len(cover_trades) == 1
    assert cover_trades[0]["pnl"] > 0, f"Expected positive PnL, got {cover_trades[0]['pnl']}"


@patch("routes.backtest._fetch")
def test_short_pnl_negative_when_price_rises(mock_fetch):
    """Short at ~100, price rises to 110, PnL should be negative."""
    # sell_rules: close less_than 91 won't trigger at 110, so use a rule that triggers at 110
    req = _req_short(
        sell_rules=[Rule(indicator="price", condition="above", value=109)],
    )
    mock_fetch.return_value = _make_df([100, 100, 110, 110])
    result = run_backtest(req)
    cover_trades = [t for t in result["trades"] if t["type"] == "cover"]
    assert len(cover_trades) == 1
    assert cover_trades[0]["pnl"] < 0, f"Expected negative PnL, got {cover_trades[0]['pnl']}"


@patch("routes.backtest._fetch")
def test_short_stop_loss_triggers_above_entry(mock_fetch):
    """Stop loss for shorts should trigger when price rises above entry by stop_loss_pct."""
    # Entry at ~100, stop_loss_pct=3 means stop at 103. High = price * 1.01.
    # At price=103, high = 103*1.01 = 104.03 which is above 103 stop. Should trigger.
    # Need: entry bar, then a bar where high >= entry * 1.03
    # Entry fill_price = 100 (no slippage). Stop at 100 * 1.03 = 103.
    # Bar at price=103: high = 103 * 1.01 = 104.03 >= 103 → triggers.
    req = _req_short(
        stop_loss_pct=3.0,
        sell_rules=[Rule(indicator="price", condition="below", value=1)],  # never triggers
    )
    mock_fetch.return_value = _make_df([100, 100, 103, 103])
    result = run_backtest(req)
    cover_trades = [t for t in result["trades"] if t["type"] == "cover"]
    assert len(cover_trades) == 1
    assert cover_trades[0]["stop_loss"] is True, "Expected stop_loss flag to be True"


@patch("routes.backtest._fetch")
def test_short_slippage_direction(mock_fetch):
    """Short entry fill should be lower (worse for seller), exit fill should be higher (worse for buyer)."""
    req = _req_short(slippage_bps=100.0)   # 100 bps = 1% — preserves original test magnitude
    mock_fetch.return_value = _make_df([100, 100, 90, 90])
    result = run_backtest(req)
    short_trade = [t for t in result["trades"] if t["type"] == "short"][0]
    cover_trade = [t for t in result["trades"] if t["type"] == "cover"][0]
    # Entry: fill < market price (slippage works against short seller)
    assert short_trade["price"] < 100, f"Short entry fill {short_trade['price']} should be < 100"
    # Exit: fill > market exit price (slippage works against cover buyer)
    # The raw exit price is around 90, cover fill should be higher
    assert cover_trade["price"] > 90, f"Cover exit fill {cover_trade['price']} should be > 90"


@patch("routes.backtest._fetch")
def test_long_still_works(mock_fetch):
    """Regression test: long direction should still work as before."""
    req = StrategyRequest(
        ticker="TEST", direction="long",
        buy_rules=[Rule(indicator="price", condition="below", value=999)],
        sell_rules=[Rule(indicator="price", condition="above", value=109)],
        initial_capital=10000.0, position_size=1.0,
    )
    mock_fetch.return_value = _make_df([100, 100, 110, 110])
    result = run_backtest(req)
    types = [t["type"] for t in result["trades"]]
    assert "buy" in types, f"Expected 'buy' in trade types, got {types}"
    assert "sell" in types, f"Expected 'sell' in trade types, got {types}"
    sell_trades = [t for t in result["trades"] if t["type"] == "sell"]
    assert len(sell_trades) == 1
    assert sell_trades[0]["pnl"] > 0, "Long: buy at 100, sell at 110 should be profitable"


from fastapi.testclient import TestClient
from main import app


def test_short_backtest_api_endpoint():
    """Test the /api/backtest endpoint with direction=short."""
    client = TestClient(app)
    resp = client.post("/api/backtest", json={
        "ticker": "AAPL",
        "start": "2024-01-01",
        "end": "2024-06-01",
        "interval": "1d",
        "buy_rules": [{"indicator": "rsi", "condition": "crosses_above", "value": 70}],
        "sell_rules": [{"indicator": "rsi", "condition": "crosses_below", "value": 50}],
        "direction": "short",
        "initial_capital": 10000,
        "position_size": 1.0,
        "source": "yahoo",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "trades" in data
    # All trades should be short/cover type
    for t in data["trades"]:
        assert t["type"] in ("short", "cover"), f"Unexpected trade type: {t['type']}"

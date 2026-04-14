from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

from post_loss import is_post_loss_trigger


def test_sl_only_counts_hard_stop():
    assert is_post_loss_trigger("stop_loss", "sl") is True
    assert is_post_loss_trigger("trailing_stop", "sl") is False
    assert is_post_loss_trigger("signal", "sl") is False


def test_tsl_only_counts_trailing():
    assert is_post_loss_trigger("stop_loss", "tsl") is False
    assert is_post_loss_trigger("trailing_stop", "tsl") is True
    assert is_post_loss_trigger("signal", "tsl") is False


def test_both_counts_either_stop():
    assert is_post_loss_trigger("stop_loss", "both") is True
    assert is_post_loss_trigger("trailing_stop", "both") is True
    assert is_post_loss_trigger("signal", "both") is False


def test_unknown_trigger_defaults_to_sl():
    assert is_post_loss_trigger("stop_loss", "garbage") is True
    assert is_post_loss_trigger("trailing_stop", "garbage") is False


# ---------- Backtest integration ----------

import pandas as pd
from unittest.mock import patch
from models import StrategyRequest, SkipAfterStopConfig
from routes.backtest import run_backtest
from signal_engine import Rule


def _make_df(prices):
    dates = pd.date_range("2024-01-01", periods=len(prices), freq="D")
    return pd.DataFrame({
        "Open": prices,
        "High": [p * 1.01 for p in prices],
        "Low": [p * 0.99 for p in prices],
        "Close": prices,
        "Volume": [1_000_000] * len(prices),
    }, index=dates)


def _req(**kw):
    # Tight 0.5% stop + flat prices: _make_df gives Low=price*0.99, so every bar
    # after entry triggers the stop. This creates a stop→re-entry→stop loop that
    # isolates the skip-after-stop behavior.
    defaults = dict(
        ticker="TEST",
        buy_rules=[Rule(indicator="price", condition="above", value=0)],
        sell_rules=[Rule(indicator="price", condition="below", value=0)],
        initial_capital=10_000.0,
        position_size=1.0,
        stop_loss_pct=0.5,
    )
    return StrategyRequest(**{**defaults, **kw})


@patch("routes.backtest._fetch")
def test_skip_after_stop_disabled_enters_every_other_bar(mock_fetch):
    # 6 bars of flat price: entries at bars 0, 2, 4 (stop on intervening bars)
    mock_fetch.return_value = _make_df([100] * 6)
    result = run_backtest(_req())
    entries = [t for t in result["trades"] if t["type"] == "buy"]
    assert len(entries) == 3, f"expected 3 entries without skip, got {len(entries)}"


@patch("routes.backtest._fetch")
def test_skip_after_stop_blocks_one_entry(mock_fetch):
    # skip=1: entries at bars 0, 3 (bar 2 skipped); bar 4 stops bar-3 entry, bar 5 skipped
    mock_fetch.return_value = _make_df([100] * 6)
    req = _req(skip_after_stop=SkipAfterStopConfig(enabled=True, count=1, trigger="sl"))
    result = run_backtest(req)
    entries = [t for t in result["trades"] if t["type"] == "buy"]
    assert len(entries) == 2, f"expected 2 entries with skip=1, got {len(entries)}"


@patch("routes.backtest._fetch")
def test_skip_trigger_tsl_ignores_hard_stop(mock_fetch):
    # Hard stop fires; trigger='tsl' must NOT activate skip → behaves like disabled
    mock_fetch.return_value = _make_df([100] * 6)
    req = _req(skip_after_stop=SkipAfterStopConfig(enabled=True, count=2, trigger="tsl"))
    result = run_backtest(req)
    entries = [t for t in result["trades"] if t["type"] == "buy"]
    assert len(entries) == 3, f"tsl trigger should ignore hard stop, got {len(entries)}"


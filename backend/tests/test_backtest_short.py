"""Tests for short-direction backtesting logic."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pytest
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


@patch("routes.backtest._fetch")
def test_short_backtest_api_endpoint(mock_fetch):
    """Test the /api/backtest endpoint with direction=short.

    Price series engineered to trigger RSI-based short/cover trades:
    - 15-bar warmup (alternating 100/10x) gives RSI = 50 at bar 14
    - Three rising bars (108, 116, 124) push RSI to ~62
    - Big jump to 150 forces RSI > 70 at bar 18 (crosses_above 70 → short entry)
    - Kept at 148 (bar 19) so position is open
    - Four falling bars (130, 110, 90, 70) pull RSI < 50 at bar 22
      (crosses_below 50 → cover exit)
    """
    mock_fetch.return_value = _make_df([
        100, 102, 100, 103, 100, 104, 100, 105, 100, 106, 100, 107, 100, 108, 100,
        108, 116, 124, 150, 148,
        130, 110, 90, 70,
    ])
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
    # F148 morning review: also assert trades actually fired so a future
    # RSI implementation change that produces zero trades fails loudly
    # instead of trivially passing the type-membership loop below.
    trade_types = [t["type"] for t in data["trades"]]
    assert "short" in trade_types, f"Expected at least one short entry, got {trade_types}"
    assert "cover" in trade_types, f"Expected at least one cover exit, got {trade_types}"
    # All trades should be short/cover type
    for t in data["trades"]:
        assert t["type"] in ("short", "cover"), f"Unexpected trade type: {t['type']}"


# ---------------------------------------------------------------------------
# Regime / b23_mode tests (silent-fallback bug fix)
#
# Bug: when regime.enabled=True but only one direction's rule list was filled,
# b23_mode was False, so the hidden unified buy_rules silently drove entries.
# Fix: b23_mode activates on regime.enabled alone; empty list = no entries.
# ---------------------------------------------------------------------------

from models import RegimeConfig


def _make_df_long(n: int = 10, base: float = 100.0) -> pd.DataFrame:
    """Synthetic daily OHLCV, constant price — simple baseline for regime tests."""
    prices = [base] * n
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "Open": prices,
        "High": [p * 1.005 for p in prices],
        "Low": [p * 0.995 for p in prices],
        "Close": prices,
        "Volume": [1_000_000] * n,
    }, index=dates)


def _regime_always_long() -> RegimeConfig:
    """Regime rule that is always True (price > 0) — all bars long-active."""
    return RegimeConfig(
        enabled=True,
        min_bars=1,
        rules=[Rule(indicator="price", condition="above", value=0)],
        logic="AND",
    )


def _regime_always_short() -> RegimeConfig:
    """Regime rule that is always False (price > 999999) — all bars short-active."""
    return RegimeConfig(
        enabled=True,
        min_bars=1,
        rules=[Rule(indicator="price", condition="above", value=999999)],
        logic="AND",
    )


@patch("routes.backtest.fetch_higher_tf")
@patch("routes.backtest._fetch")
def test_regime_long_rules_only_fires_long_not_unified(mock_fetch, mock_htf):
    """Regime enabled + long rules filled + short rules empty → only long entries fire.

    The unified buy_rules contain a rule (price > 0) that would always fire if
    the old silent-fallback bug were still present.  The long_buy_rules contain
    a rule that fires when price is below 101 (always on our flat-100 series).
    The long_buy_rules label must appear in trade["rules"], not the unified label.
    Zero short entries should be produced.
    """
    df = _make_df_long(n=12)
    mock_fetch.return_value = df
    mock_htf.return_value = df

    # unified buy_rules: always fires — would be used by the old bug
    unified_always_fires = Rule(indicator="price", condition="above", value=0)
    # long-tab rule: fires on our flat-100 series (price below 101)
    long_entry_rule = Rule(indicator="price", condition="below", value=101)
    # sell rule: fires after a few bars (price below 105, always true → sells on next open bar)
    sell_rule = Rule(indicator="price", condition="below", value=105)

    req = StrategyRequest(
        ticker="TEST",
        start="2024-01-01",
        end="2024-12-31",
        interval="1d",
        # unified rules — must NOT drive entries when regime is enabled
        buy_rules=[unified_always_fires],
        sell_rules=[sell_rule],
        long_buy_rules=[long_entry_rule],
        long_sell_rules=[sell_rule],
        short_buy_rules=[],   # intentionally empty — the trigger for the old bug
        short_sell_rules=[],
        regime=_regime_always_long(),
        initial_capital=10000.0,
        position_size=1.0,
    )

    result = run_backtest(req)
    trades = result["trades"]

    # Must have at least one entry
    entry_trades = [t for t in trades if t["type"] == "buy"]
    assert len(entry_trades) >= 1, f"Expected long entries, got {trades}"

    # No short entries should fire (short_buy_rules=[])
    short_entries = [t for t in trades if t["type"] == "short"]
    assert len(short_entries) == 0, f"Unexpected short entries: {short_entries}"

    # Direction of all entries must be 'long'
    for t in entry_trades:
        assert t["direction"] == "long", f"Expected direction=long, got {t['direction']}"

    # Tight rules-display assertion: the unified rule uses condition="above" and the
    # long-specific rule uses condition="below", so _rule_desc produces "price above 0"
    # vs "price below 101" — unambiguous substrings.  If display_buy_rules were
    # reverted to buy_rules (the unfixed bug), the unified "above" label would appear
    # instead of the direction-specific "below" label.
    for t in entry_trades:
        rules_str = " ".join(t["rules"])
        assert "below" in rules_str, (
            f"Expected direction-specific rule label ('below') in trade rules, got {t['rules']}"
        )
        assert "above" not in rules_str, (
            f"Unified rule label ('above') leaked into trade rules: {t['rules']}"
        )


@patch("routes.backtest.fetch_higher_tf")
@patch("routes.backtest._fetch")
def test_regime_short_rules_only_fires_short_not_unified(mock_fetch, mock_htf):
    """Regime enabled + short rules filled + long rules empty → only short entries fire,
    and trade['rules'] reflects short_buy_rules label, not unified buy_rules label."""
    df = _make_df_long(n=12)
    mock_fetch.return_value = df
    mock_htf.return_value = df

    # unified buy_rules: always fires — condition="above" produces "price above 0"
    unified_always_fires = Rule(indicator="price", condition="above", value=0)
    # short-specific entry rule: also always fires — condition="below" produces "price below 10000"
    short_entry_rule = Rule(indicator="price", condition="below", value=10000)
    sell_rule = Rule(indicator="price", condition="below", value=105)

    req = StrategyRequest(
        ticker="TEST",
        start="2024-01-01",
        end="2024-12-31",
        interval="1d",
        buy_rules=[unified_always_fires],
        sell_rules=[sell_rule],
        long_buy_rules=[],     # intentionally empty
        long_sell_rules=[],
        short_buy_rules=[short_entry_rule],
        short_sell_rules=[sell_rule],
        regime=_regime_always_short(),  # regime always inactive → short path
        initial_capital=10000.0,
        position_size=1.0,
    )

    result = run_backtest(req)
    trades = result["trades"]

    short_entries = [t for t in trades if t["type"] == "short"]
    assert len(short_entries) >= 1, f"Expected short entries, got {trades}"

    long_entries = [t for t in trades if t["type"] == "buy"]
    assert len(long_entries) == 0, f"Unexpected long entries: {long_entries}"

    for t in short_entries:
        assert t["direction"] == "short", f"Expected direction=short, got {t['direction']}"

    # Tight rules-display assertion: unified uses "above", direction-specific uses "below".
    # If display_buy_rules were reverted to buy_rules, "above" would appear instead.
    for t in short_entries:
        rules_str = " ".join(t["rules"])
        assert "below" in rules_str, (
            f"Expected direction-specific rule label ('below') in trade rules, got {t['rules']}"
        )
        assert "above" not in rules_str, (
            f"Unified rule label ('above') leaked into trade rules: {t['rules']}"
        )


@patch("routes.backtest.fetch_higher_tf")
@patch("routes.backtest._fetch")
def test_regime_both_empty_produces_no_trades(mock_fetch, mock_htf):
    """Regime enabled + both long_buy_rules and short_buy_rules empty → zero trades."""
    df = _make_df_long(n=12)
    mock_fetch.return_value = df
    mock_htf.return_value = df

    req = StrategyRequest(
        ticker="TEST",
        start="2024-01-01",
        end="2024-12-31",
        interval="1d",
        # unified buy_rules always fires — must NOT be used when regime enabled
        buy_rules=[Rule(indicator="price", condition="above", value=0)],
        sell_rules=[Rule(indicator="price", condition="below", value=105)],
        long_buy_rules=[],
        long_sell_rules=[],
        short_buy_rules=[],
        short_sell_rules=[],
        regime=_regime_always_long(),
        initial_capital=10000.0,
        position_size=1.0,
    )

    result = run_backtest(req)
    entry_trades = [t for t in result["trades"] if t["type"] in ("buy", "short")]
    assert len(entry_trades) == 0, (
        f"Expected zero entries when both regime rule lists are empty, got {entry_trades}"
    )


@patch("routes.backtest._fetch")
def test_regime_off_unified_buy_rules_still_drive_entries(mock_fetch):
    """Regression: when regime is disabled, unified buy_rules still drive entries normally."""
    df = _make_df_long(n=6)
    mock_fetch.return_value = df

    req = StrategyRequest(
        ticker="TEST",
        start="2024-01-01",
        end="2024-12-31",
        interval="1d",
        buy_rules=[Rule(indicator="price", condition="below", value=101)],
        sell_rules=[Rule(indicator="price", condition="above", value=200)],  # never fires
        long_buy_rules=[],
        long_sell_rules=[],
        short_buy_rules=[],
        short_sell_rules=[],
        regime=None,
        initial_capital=10000.0,
        position_size=1.0,
    )

    result = run_backtest(req)
    entry_trades = [t for t in result["trades"] if t["type"] in ("buy", "short")]
    assert len(entry_trades) >= 1, (
        f"Expected entries driven by unified buy_rules when regime is off, got {result['trades']}"
    )


@patch("routes.backtest.fetch_higher_tf")
@patch("routes.backtest._fetch")
def test_regime_exit_uses_direction_specific_sell_rules(mock_fetch, mock_htf):
    """Regime enabled: exit trade['rules'] must reflect long_sell_rules, not unified sell_rules.

    Entry fires via long_buy_rules.  Exit fires because long_sell_rules fires (price above 0,
    always true).  Unified sell_rules uses condition='below' value=10000 — also always fires —
    but must NOT appear in the exit trade record's rules field when b23_mode is active.
    Verifies the display_sell_rules fix on the exit side (C1 in the review).
    """
    df = _make_df_long(n=6)
    mock_fetch.return_value = df
    mock_htf.return_value = df

    # Entry: long_buy_rules fires first bar (price < 10000 is always true on flat-100)
    long_entry_rule = Rule(indicator="price", condition="below", value=10000)
    # Exit: long_sell_rules fires every bar (price > 0) — "price above 0"
    long_exit_rule = Rule(indicator="price", condition="above", value=0)
    # Unified sell_rules: also fires every bar — "price below 10000" (distinguishable label)
    unified_sell_rule = Rule(indicator="price", condition="below", value=10000)

    req = StrategyRequest(
        ticker="TEST",
        start="2024-01-01",
        end="2024-12-31",
        interval="1d",
        buy_rules=[long_entry_rule],
        sell_rules=[unified_sell_rule],
        long_buy_rules=[long_entry_rule],
        long_sell_rules=[long_exit_rule],
        short_buy_rules=[],
        short_sell_rules=[],
        regime=_regime_always_long(),
        initial_capital=10000.0,
        position_size=1.0,
    )

    result = run_backtest(req)
    trades = result["trades"]

    exit_trades = [t for t in trades if t["type"] == "sell"]
    assert len(exit_trades) >= 1, f"Expected at least one sell exit, got {trades}"

    # Tight label assertion: long_sell_rules produces "price above 0" ("above"),
    # unified sell_rules would produce "price below 10000" ("below").
    # If display_sell_rules were reverted to sell_rules, "below" would appear instead.
    for t in exit_trades:
        rules_str = " ".join(t["rules"])
        assert "above" in rules_str, (
            f"Expected direction-specific sell label ('above') in exit rules, got {t['rules']}"
        )
        assert "below" not in rules_str, (
            f"Unified sell label ('below') leaked into exit trade rules: {t['rules']}"
        )


# ---------------------------------------------------------------------------
# F156 — signal_trace in b23_mode
#
# Two assertions from the F155 review:
# (a) Trace entries on bars where the dual-mode rules fire reference the
#     direction-specific rule labels (e.g. "price below 101"), NOT the unified
#     "buy_rules" labels (e.g. "price above 0").
# (b) The "MISSED (no position)" trace entry is suppressed when b23_mode=True
#     and position=0.
# ---------------------------------------------------------------------------


@patch("routes.backtest.fetch_higher_tf")
@patch("routes.backtest._fetch")
def test_b23_signal_trace_uses_direction_specific_labels(mock_fetch, mock_htf):
    """b23_mode + debug=True: trace buy_rules entries reference long_buy_rules,
    not the hidden unified buy_rules (assertion a of F156).

    Strategy: unified buy_rules uses "price above 0" (always fires),
    long_buy_rules uses "price below 101" (also fires on flat-100 series, but
    the description is distinguishable).  In b23 mode the trace must show the
    "below 101" label, not "above 0".
    """
    df = _make_df_long(n=8)
    mock_fetch.return_value = df
    mock_htf.return_value = df

    # Unified rule — "above 0": always fires; must NOT appear in the trace
    unified_buy = Rule(indicator="price", condition="above", value=0)
    # Direction-specific rule — "below 101": also fires on flat-100; distinguishable label
    long_buy = Rule(indicator="price", condition="below", value=101)
    # Sell rule fires after 1 bar (price below 999, always true → exit next open bar)
    sell_rule = Rule(indicator="price", condition="below", value=999)

    req = StrategyRequest(
        ticker="TEST",
        start="2024-01-01",
        end="2024-12-31",
        interval="1d",
        buy_rules=[unified_buy],
        sell_rules=[sell_rule],
        long_buy_rules=[long_buy],
        long_sell_rules=[sell_rule],
        short_buy_rules=[],
        short_sell_rules=[],
        regime=_regime_always_long(),
        initial_capital=10000.0,
        position_size=1.0,
        debug=True,
    )

    result = run_backtest(req)
    trace = result.get("signal_trace", [])
    assert trace, "Expected non-empty signal_trace with debug=True"

    buy_entries = [e for e in trace if e.get("action") == "BUY"]
    assert buy_entries, f"Expected at least one BUY trace entry, got {trace}"

    for entry in buy_entries:
        rule_descs = [r["rule"] for r in entry.get("buy_rules", [])]
        combined = " ".join(rule_descs)
        assert "below 101" in combined, (
            f"Expected direction-specific rule ('below 101') in trace, got {rule_descs}"
        )
        assert "above 0" not in combined, (
            f"Unified rule label ('above 0') leaked into b23 trace: {rule_descs}"
        )


@patch("routes.backtest.fetch_higher_tf")
@patch("routes.backtest._fetch")
def test_b23_signal_trace_suppresses_missed_no_position(mock_fetch, mock_htf):
    """b23_mode + debug=True: 'MISSED (no position)' trace entries are
    suppressed on flat-position bars (assertion b of F156).

    The MISSED entry is emitted in non-b23 mode when sell_rules fire while
    position == 0 (line 821 guard: `and not b23_mode`).  In b23 mode those
    unified sell_rules are hidden, so no MISSED entry should appear.
    """
    df = _make_df_long(n=10)
    mock_fetch.return_value = df
    mock_htf.return_value = df

    # long_buy_rules: fires on price > 200 — never on flat-100, so position stays 0.
    # Every bar runs through the flat-position branch where MISSED would normally emit.
    no_entry_rule = Rule(indicator="price", condition="above", value=200)
    # sell_rules that fire on every bar (price below 999, always)
    sell_always = Rule(indicator="price", condition="below", value=999)

    req = StrategyRequest(
        ticker="TEST",
        start="2024-01-01",
        end="2024-12-31",
        interval="1d",
        buy_rules=[],
        sell_rules=[sell_always],
        long_buy_rules=[no_entry_rule],
        long_sell_rules=[sell_always],
        short_buy_rules=[],
        short_sell_rules=[],
        regime=_regime_always_long(),
        initial_capital=10000.0,
        position_size=1.0,
        debug=True,
    )

    result = run_backtest(req)
    trace = result.get("signal_trace", [])

    missed = [e for e in trace if e.get("action") == "MISSED (no position)"]
    assert not missed, (
        f"Expected MISSED (no position) suppressed in b23_mode, but got {missed}"
    )

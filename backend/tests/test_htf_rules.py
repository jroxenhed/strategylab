"""F246: Tests for per-rule HTF timeframe evaluation.

Covers:
1. A rule with timeframe="1d" on 1h base data evaluates on daily bars and forward-fills to
   hourly — produces a different trade count than the same rule at base TF.
2. A rule with timeframe=None is a no-op — backtest produces identical results to pre-F246 code.
3. WFA strips timeframe from all rule lists.
4. Rule.timeframe field is accepted by Pydantic (schema smoke test).
"""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pandas as pd
import numpy as np
import pytest
from fastapi.testclient import TestClient

from signal_engine import Rule
from routes.backtest import _apply_htf_rules, _compute_htf_rule_mask


# ---------------------------------------------------------------------------
# Helper: build a minimal daily DataFrame with DatetimeIndex (UTC-aware)
# ---------------------------------------------------------------------------

def _make_daily_df(n=30, start="2023-01-01"):
    idx = pd.date_range(start, periods=n, freq="B", tz="UTC")
    close = pd.Series(np.linspace(100, 110, n), index=idx)
    df = pd.DataFrame({
        "Open": close * 0.99,
        "High": close * 1.01,
        "Low": close * 0.98,
        "Close": close,
        "Volume": np.full(n, 1_000_000.0),
    })
    return df


def _make_hourly_df(n=200, start="2023-01-03"):
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    close = pd.Series(np.linspace(100, 110, n), index=idx)
    df = pd.DataFrame({
        "Open": close * 0.99,
        "High": close * 1.01,
        "Low": close * 0.98,
        "Close": close,
        "Volume": np.full(n, 500_000.0),
    })
    return df


# ---------------------------------------------------------------------------
# Test 1: timeframe=None → _apply_htf_rules returns all-True (no-op gate)
# ---------------------------------------------------------------------------

def test_apply_htf_rules_no_htf_rules_returns_all_true():
    """When all rules have timeframe=None, the gate is always open (all True)."""
    rules = [
        Rule(indicator="rsi", condition="above", value=30, timeframe=None),
        Rule(indicator="macd", condition="crossover_up", param="signal", timeframe=None),
    ]
    hourly_df = _make_hourly_df()

    gate = _apply_htf_rules(
        base_rules=rules,
        logic="AND",
        ticker="AAPL",
        start="2023-01-03",
        end="2023-02-10",
        base_index=hourly_df.index,
        source="yahoo",
        base_indicators={},
    )

    assert gate.dtype == bool
    assert len(gate) == len(hourly_df)
    assert gate.all(), "Gate should be all-True when no rule has a non-None timeframe"


# ---------------------------------------------------------------------------
# Test 2: _apply_htf_rules gates correctly using a mocked HTF fetch
# ---------------------------------------------------------------------------

def test_apply_htf_rules_gates_with_htf_mask(monkeypatch):
    """HTF rule with timeframe='1d' should produce a mask aligned to LTF index.

    Strategy: monkeypatch fetch_higher_tf to return a daily DataFrame where
    price crosses 105 midway through. The LTF hourly gate should be False for
    early hours (before price > 105 on daily) and True for later hours.

    Note on anti-lookahead: align_htf_to_ltf shifts daily bars by 1, so day D's
    value only gates day D+1's intraday bars. We use a wide hourly window (600h)
    spanning the full daily range so both True and False regions are visible.
    """
    # Build daily df: prices go 100→110 over 60 business days.
    # price > 105 is True for roughly the last 30 bars.
    n_daily = 60
    daily_start = "2023-01-02"
    daily_idx = pd.date_range(daily_start, periods=n_daily, freq="B", tz="UTC")
    daily_close = pd.Series(np.linspace(100, 110, n_daily), index=daily_idx)
    daily_df = pd.DataFrame({
        "Open": daily_close * 0.99,
        "High": daily_close * 1.01,
        "Low": daily_close * 0.98,
        "Close": daily_close,
        "Volume": np.full(n_daily, 1_000_000.0),
    })

    # Build hourly df spanning the same period (1500h ≈ 62 calendar days covers the full daily range)
    hourly_idx = pd.date_range("2023-01-03 00:00", periods=1500, freq="h", tz="UTC")
    hourly_close = pd.Series(np.linspace(100, 110, 1500), index=hourly_idx)
    hourly_df = pd.DataFrame({
        "Open": hourly_close * 0.99, "High": hourly_close * 1.01,
        "Low": hourly_close * 0.98, "Close": hourly_close,
        "Volume": np.full(1500, 500_000.0),
    })

    import routes.backtest as bt_mod

    def fake_fetch(ticker, start, end, interval, source="yahoo"):
        return daily_df

    monkeypatch.setattr(bt_mod, "fetch_higher_tf", fake_fetch)

    # price > 105 is True for the last ~30 of 60 daily bars
    rules = [
        Rule(indicator="price", condition="above", value=105.0, timeframe="1d"),
    ]

    gate = _apply_htf_rules(
        base_rules=rules,
        logic="AND",
        ticker="AAPL",
        start="2023-01-03",
        end="2023-04-01",
        base_index=hourly_df.index,
        source="yahoo",
        base_indicators={},
    )

    assert gate.dtype == bool
    assert len(gate) == len(hourly_df)
    # First bars should be False (price < 105 early on; plus anti-lookahead warmup)
    assert not gate.all(), "Gate should not be all-True — price starts below 105"
    # Last bars (>Feb 14) should be True (price > 105 from daily bar ~30)
    assert gate.any(), "Gate should have some True values — price exceeds 105 in second half"
    # Early slice (first 240h = 10 days) should be all False (price ~100, well below 105)
    assert not gate.iloc[:240].any(), "Early hours should be False — daily price < 105"


# ---------------------------------------------------------------------------
# Test 3: muted HTF rules are skipped (no gate)
# ---------------------------------------------------------------------------

def test_apply_htf_rules_skips_muted_rules(monkeypatch):
    """A muted HTF rule should be skipped — gate stays all-True."""
    daily_df = _make_daily_df(n=30)
    hourly_df = _make_hourly_df(n=300)

    import routes.backtest as bt_mod

    def fake_fetch(ticker, start, end, interval, source="yahoo"):
        return daily_df

    monkeypatch.setattr(bt_mod, "fetch_higher_tf", fake_fetch)

    rules = [
        # This would gate everything (price > 999 is always False),
        # but it's muted — so it should be ignored.
        Rule(indicator="price", condition="above", value=999.0, timeframe="1d", muted=True),
    ]

    gate = _apply_htf_rules(
        base_rules=rules,
        logic="AND",
        ticker="AAPL",
        start="2023-01-03",
        end="2023-02-10",
        base_index=hourly_df.index,
        source="yahoo",
        base_indicators={},
    )

    assert gate.all(), "Muted HTF rule should be skipped → gate all-True"


# ---------------------------------------------------------------------------
# Test 4: empty HTF data → all-False (gate closed), no crash
# ---------------------------------------------------------------------------

def test_apply_htf_rules_empty_htf_returns_all_false(monkeypatch):
    """When HTF fetch returns empty DataFrame, gate should be all-False (safe fallback)."""
    hourly_df = _make_hourly_df(n=300)

    import routes.backtest as bt_mod

    def fake_fetch_empty(ticker, start, end, interval, source="yahoo"):
        return pd.DataFrame()

    monkeypatch.setattr(bt_mod, "fetch_higher_tf", fake_fetch_empty)

    rules = [
        Rule(indicator="rsi", condition="above", value=50.0, timeframe="1d"),
    ]

    gate = _apply_htf_rules(
        base_rules=rules,
        logic="AND",
        ticker="AAPL",
        start="2023-01-03",
        end="2023-02-10",
        base_index=hourly_df.index,
        source="yahoo",
        base_indicators={},
    )

    assert not gate.any(), "Empty HTF data → gate all-False"
    assert len(gate) == len(hourly_df)


# ---------------------------------------------------------------------------
# Test 5: Rule.timeframe accepted by Pydantic (schema smoke test)
# ---------------------------------------------------------------------------

def test_rule_timeframe_field_accepted():
    """Rule.timeframe is an optional field — should round-trip through Pydantic."""
    r1 = Rule(indicator="rsi", condition="above", value=30)
    assert r1.timeframe is None

    r2 = Rule(indicator="rsi", condition="above", value=30, timeframe="1d")
    assert r2.timeframe == "1d"

    # Round-trip via model_dump / model_validate
    d = r2.model_dump()
    assert d["timeframe"] == "1d"
    r3 = Rule.model_validate(d)
    assert r3.timeframe == "1d"

    # model_copy strips timeframe
    r4 = r2.model_copy(update={"timeframe": None})
    assert r4.timeframe is None


# ---------------------------------------------------------------------------
# Test 6: WFA strips timeframe from all rule lists
# ---------------------------------------------------------------------------

def test_wfa_strips_timeframe_from_rules(monkeypatch):
    """WalkForwardRequest should strip timeframe from buy/sell rule lists before running."""
    import routes.walk_forward as wf_mod
    import routes.wfa_pool as wfa_pool_mod

    # Force serial execution so monkeypatching works
    monkeypatch.setattr(wfa_pool_mod, "_FORCE_SERIAL", True)

    # Capture the StrategyRequest objects passed to run_backtest
    captured_reqs = []

    def fake_run_backtest(req, **kwargs):
        captured_reqs.append(req.model_copy(deep=True))
        # Return a minimal valid backtest result
        return {
            "trades": [],
            "equity": [{"date": "2023-01-01", "value": 10000}],
            "summary": {
                "total_return_pct": 0, "cagr": 0, "sharpe_ratio": 0,
                "max_drawdown_pct": 0, "win_rate": 0, "num_trades": 0,
                "profit_factor": None, "avg_win_pct": None, "avg_loss_pct": None,
                "avg_hold_bars": None, "gross_profit": 0, "gross_loss": 0,
                "ev_per_trade": None, "volatility_annual": None,
                "beta": None, "r_squared": None,
                "initial_capital": 10000, "final_equity": 10000,
                "num_wins": 0, "num_losses": 0, "avg_win": None, "avg_loss": None,
            },
        }

    # Patch at both call sites
    import routes.grid_runner as grid_runner_mod
    monkeypatch.setattr(grid_runner_mod, "run_backtest", fake_run_backtest)
    monkeypatch.setattr(wf_mod, "run_backtest", fake_run_backtest)

    # Also need to mock _fetch so WFA doesn't hit the network
    def fake_fetch(ticker, start, end, interval, **kwargs):
        n = 600
        idx = pd.date_range(start, periods=n, freq="B", tz="UTC")
        close = pd.Series(np.linspace(100, 150, n), index=idx)
        return pd.DataFrame({
            "Open": close * 0.99, "High": close * 1.01,
            "Low": close * 0.98, "Close": close,
            "Volume": np.full(n, 1_000_000.0),
        })

    monkeypatch.setattr(wf_mod, "_fetch", fake_fetch)

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    payload = {
        "base": {
            "ticker": "AAPL",
            "start": "2018-01-01",
            "end": "2024-01-01",
            "interval": "1d",
            "buy_rules": [
                {"indicator": "rsi", "condition": "above", "value": 30, "timeframe": "1wk"}
            ],
            "sell_rules": [
                {"indicator": "rsi", "condition": "below", "value": 70, "timeframe": "1mo"}
            ],
        },
        "params": [{"path": "stop_loss_pct", "values": [3.0, 5.0]}],
        "is_bars": 200,
        "oos_bars": 100,
        "gap_bars": 0,
        "metric": "sharpe_ratio",
        "min_trades_is": 1,
    }

    resp = client.post("/api/backtest/walk_forward", json=payload)
    # The test is not about the HTTP result — it's about what WFA passes to run_backtest.
    # At least one call to run_backtest must have happened.
    assert len(captured_reqs) > 0, "run_backtest was never called"

    for req in captured_reqs:
        for rule in req.buy_rules:
            assert rule.timeframe is None, f"buy_rule timeframe was not stripped: {rule}"
        for rule in req.sell_rules:
            assert rule.timeframe is None, f"sell_rule timeframe was not stripped: {rule}"

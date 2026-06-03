"""F282 — Sensitivity-route regression parity tests.

Mirrors test_optimizer_regression.py for the /api/backtest/sweep route.

The sweep route drives _apply_param through its own handler (sweep_backtest)
and calls run_backtest directly — a different code path from run_grid. These
tests assert:

  1. Per-step results DIFFER across swept values of buy_rule_0_value, confirming
     _apply_param + run_backtest honour the substituted param end-to-end.
  2. Degenerate-data guard: at least one sweep step must produce >0 trades,
     mirroring the F187 pattern in test_optimizer_regression.py.

No network calls — _fetch in routes.backtest is monkeypatched with a synthetic
sine-wave DataFrame (identical construction to test_optimizer_regression.py).
"""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Synthetic data factory (mirrors test_optimizer_regression._make_synthetic_df)
# ---------------------------------------------------------------------------

def _make_synthetic_df(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """Return a synthetic daily OHLCV DataFrame with meaningful RSI variation.

    Prices follow a sine wave so RSI oscillates across a wide range, making
    threshold sweeps produce clearly different trade counts.

    No network calls — purely synthetic.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    t = np.linspace(0, 8 * np.pi, n)
    base = 100.0 + 20.0 * np.sin(t) + rng.normal(0, 0.5, n)
    close = np.maximum(base, 1.0)
    open_ = close * (1 + rng.uniform(-0.005, 0.005, n))
    high = np.maximum(close, open_) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(close, open_) * (1 - rng.uniform(0, 0.01, n))
    volume = rng.integers(100_000, 1_000_000, n).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_sweep_body(**overrides) -> dict:
    """Minimal valid SweepRequest payload."""
    body = {
        "base": {
            "ticker": "SYNTH",
            "start": "2020-01-01",
            "end": "2022-01-01",
            "interval": "1d",
            "buy_rules": [{"indicator": "rsi", "condition": "below", "value": 30.0}],
            "sell_rules": [{"indicator": "rsi", "condition": "above", "value": 70.0}],
        },
        "param_path": "buy_rule_0_value",
        "values": [5.0, 80.0],
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# 1. buy_rule_0_value sweep produces different per-step results
# ---------------------------------------------------------------------------

def test_sweep_buy_rule_value_produces_different_results(monkeypatch):
    """F282: sweeping buy_rule_0_value via the sweep route must produce distinct results.

    RSI buy threshold:
      - value=5  → RSI rarely drops below 5 → very few (likely 0) trades
      - value=80 → RSI almost always below 80 → many trades

    Different thresholds must produce different trade counts end-to-end through
    sweep_backtest → _apply_param → run_backtest.
    """
    df = _make_synthetic_df()

    import routes.backtest as backtest_mod
    monkeypatch.setattr(backtest_mod, "_fetch", lambda *a, **kw: df)

    resp = client.post("/api/backtest/sweep", json=_base_sweep_body())
    assert resp.status_code == 200, f"sweep returned {resp.status_code}: {resp.text}"

    data = resp.json()
    assert data["completed"] == 2, f"expected 2 completed steps, got {data['completed']}"
    assert data["skipped"] == 0, f"unexpected skipped steps: {data['skipped']}"

    results = data["results"]
    trades_low = results[0]["num_trades"]   # buy_rule_0_value=5.0
    trades_high = results[1]["num_trades"]  # buy_rule_0_value=80.0

    assert trades_high > 0, (
        f"RSI<80 step produced zero trades — synthetic data is degenerate, "
        f"not a param-substitution regression"
    )
    assert trades_low != trades_high, (
        f"F282 regression: buy_rule_0_value=5.0 and =80.0 both produced "
        f"{trades_low} trades through the sweep route — _apply_param substitution "
        f"is not reaching run_backtest"
    )


# ---------------------------------------------------------------------------
# 2. sell_rule_0_value sweep produces different per-step results
# ---------------------------------------------------------------------------

def test_sweep_sell_rule_value_produces_different_results(monkeypatch):
    """F282: sweeping sell_rule_0_value must produce distinct per-step results.

    RSI sell threshold:
      - value=20 → exits quickly (RSI almost always above 20 once long) → more trades
      - value=90 → exits slowly (RSI rarely above 90) → fewer trades, longer holds
    """
    df = _make_synthetic_df()

    import routes.backtest as backtest_mod
    monkeypatch.setattr(backtest_mod, "_fetch", lambda *a, **kw: df)

    body = _base_sweep_body(
        param_path="sell_rule_0_value",
        values=[20.0, 90.0],
    )
    # Use a buy threshold that fires frequently so sells can differ
    body["base"]["buy_rules"] = [{"indicator": "rsi", "condition": "below", "value": 50.0}]

    resp = client.post("/api/backtest/sweep", json=body)
    assert resp.status_code == 200, f"sweep returned {resp.status_code}: {resp.text}"

    data = resp.json()
    assert data["completed"] == 2, f"expected 2 completed steps, got {data['completed']}"

    results = data["results"]
    trades_sell_low = results[0]["num_trades"]   # sell at RSI>20
    trades_sell_high = results[1]["num_trades"]  # sell at RSI>90

    assert trades_sell_low > 0 or trades_sell_high > 0, (
        "Both sell_rule_0_value steps produced zero trades — "
        "synthetic data is degenerate, not a param-substitution regression"
    )
    assert trades_sell_low != trades_sell_high, (
        f"F282 regression: sell_rule_0_value=20 and =90 both produced "
        f"{trades_sell_low} trades — sweep route param substitution is broken"
    )


# ---------------------------------------------------------------------------
# 3. buy_rule_0_params_period sweep produces different per-step results
# ---------------------------------------------------------------------------

def test_sweep_buy_rule_params_period_produces_different_results(monkeypatch):
    """F282: sweeping buy_rule_0_params_period must produce distinct per-step results.

    RSI period:
      - period=5  → fast RSI, oscillates quickly → different trade pattern
      - period=50 → slow RSI, sluggish oscillation → different trade pattern

    At least one step must produce >0 trades (degenerate-data guard).
    """
    df = _make_synthetic_df()

    import routes.backtest as backtest_mod
    monkeypatch.setattr(backtest_mod, "_fetch", lambda *a, **kw: df)

    body = _base_sweep_body(
        param_path="buy_rule_0_params_period",
        values=[5.0, 50.0],
    )
    body["base"]["buy_rules"] = [
        {"indicator": "rsi", "condition": "below", "value": 40.0, "params": {"period": 14}}
    ]

    resp = client.post("/api/backtest/sweep", json=body)
    assert resp.status_code == 200, f"sweep returned {resp.status_code}: {resp.text}"

    data = resp.json()
    assert data["completed"] == 2, f"expected 2 completed steps, got {data['completed']}"

    results = data["results"]
    trades_fast = results[0]["num_trades"]   # period=5
    trades_slow = results[1]["num_trades"]   # period=50

    assert trades_fast > 0 or trades_slow > 0, (
        "Both RSI period steps (5 and 50) produced zero trades — "
        "synthetic data is degenerate, not a param-substitution regression"
    )
    assert trades_fast != trades_slow, (
        f"F282 regression: buy_rule_0_params_period=5 and =50 both produced "
        f"{trades_fast} trades — sweep route params_period substitution is broken"
    )

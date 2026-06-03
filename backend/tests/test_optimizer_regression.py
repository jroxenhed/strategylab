"""Regression tests for the F187 optimizer param-substitution bug.

The original bug: in regime mode, the optimizer swept buy_rule_0_value but
the backtest engine read from long_buy_rules (which was never updated), so
ALL grid combos produced identical results.

These tests call run_grid with the real run_backtest (no mocking) on SYNTHETIC
bar data (no network/yfinance calls) and assert that different param values
produce different backtest results — the test that would have caught F187.
"""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import numpy as np
import pandas as pd
import pytest

from models import StrategyRequest, RegimeConfig
from signal_engine import Rule
from routes.grid_runner import run_grid
from routes.walk_forward import WalkForwardParam


# ---------------------------------------------------------------------------
# Synthetic data factory
# ---------------------------------------------------------------------------

def _make_synthetic_df(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """Return a synthetic daily OHLCV DataFrame that produces meaningful RSI variation.

    Prices follow a sine wave so RSI oscillates across a wide range, making
    threshold sweeps produce clearly different trade counts.

    No network calls — purely synthetic.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    # Base price: sine wave + small noise to keep RSI oscillating
    t = np.linspace(0, 8 * np.pi, n)
    base = 100.0 + 20.0 * np.sin(t) + rng.normal(0, 0.5, n)
    close = np.maximum(base, 1.0)
    open_ = close * (1 + rng.uniform(-0.005, 0.005, n))
    high = np.maximum(close, open_) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(close, open_) * (1 - rng.uniform(0, 0.01, n))
    volume = rng.integers(100_000, 1_000_000, n).astype(float)
    df = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )
    return df


def _make_params(path: str, values: list[float]) -> list[WalkForwardParam]:
    return [WalkForwardParam(path=path, values=values)]


# ---------------------------------------------------------------------------
# 1. Non-regime: buy_rule_0_value sweep produces different results
# ---------------------------------------------------------------------------

def test_nonregime_buy_rule_sweep_produces_different_results():
    """F187 regression (non-regime): sweeping buy_rule_0_value must produce distinct results.

    RSI buy threshold:
      - value=5  → RSI rarely drops below 5 → very few trades (likely 0)
      - value=80 → RSI almost always below 80 → many trades

    Different thresholds must produce different trade counts.
    """
    df = _make_synthetic_df()

    req = StrategyRequest(
        ticker="SYNTH",
        start="2020-01-01",
        end="2022-01-01",
        interval="1d",
        buy_rules=[Rule(indicator="rsi", condition="below", value=30.0)],
        sell_rules=[Rule(indicator="rsi", condition="above", value=70.0)],
    )

    params = _make_params("buy_rule_0_value", [5.0, 80.0])
    results, timed_out, skipped = run_grid(req, params, timeout_secs=60.0, df=df)

    assert not timed_out, "grid timed out — synthetic df may be too large or budget too low"
    assert skipped == 0, f"unexpected skipped combos: {skipped}"
    assert len(results) == 2, f"expected 2 results, got {len(results)}"

    trades_low = results[0][1].get("num_trades", 0)   # buy_rule_0_value=5.0
    trades_high = results[1][1].get("num_trades", 0)  # buy_rule_0_value=80.0

    assert trades_low != trades_high, (
        f"F187 regression (non-regime): buy_rule_0_value=5.0 and =80.0 both produced "
        f"{trades_low} trades — param substitution is broken"
    )


# ---------------------------------------------------------------------------
# 2. Non-regime: sell_rule_0_value sweep produces different results
# ---------------------------------------------------------------------------

def test_nonregime_sell_rule_sweep_produces_different_results():
    """Sweeping sell_rule_0_value must produce distinct results.

    RSI sell threshold:
      - value=20  → almost never fire above RSI 20 once long → hold long time
      - value=90  → almost always above RSI 90 → sell quickly
    """
    df = _make_synthetic_df()

    req = StrategyRequest(
        ticker="SYNTH",
        start="2020-01-01",
        end="2022-01-01",
        interval="1d",
        buy_rules=[Rule(indicator="rsi", condition="below", value=50.0)],
        sell_rules=[Rule(indicator="rsi", condition="above", value=70.0)],
    )

    params = _make_params("sell_rule_0_value", [20.0, 90.0])
    results, timed_out, skipped = run_grid(req, params, timeout_secs=60.0, df=df)

    assert not timed_out
    assert len(results) == 2, f"expected 2 results, got {len(results)}"
    assert skipped == 0, f"unexpected skipped combos: {skipped}"

    trades_low = results[0][1].get("num_trades", 0)   # sell at RSI>20
    trades_high = results[1][1].get("num_trades", 0)  # sell at RSI>90

    assert trades_low != trades_high, (
        f"F187 regression: sell_rule_0_value=20 and =90 both produced "
        f"{trades_low} trades — param substitution is broken"
    )


# ---------------------------------------------------------------------------
# 3. Regime mode: long_buy_rule_0_value sweep produces different results
#    This is the EXACT scenario that was broken in F187.
# ---------------------------------------------------------------------------

def test_regime_mode_long_buy_rule_sweep_produces_different_results(monkeypatch):
    """F187 regression (regime mode): sweeping long_buy_rule_0_value must produce distinct results.

    Before the fix: the optimizer updated buy_rules[0].value (the non-regime path)
    but run_backtest in b23_mode read from long_buy_rules[0].value (unchanged).
    All combos saw the same threshold → identical trade counts.

    After the fix: long_buy_rule_0_value correctly updates long_buy_rules[0].value.
    """
    df = _make_synthetic_df()

    # Monkeypatch fetch_higher_tf in routes.backtest so the regime computation
    # never calls the network. Returns the same synthetic df as "HTF" data.
    import routes.backtest as backtest_mod
    monkeypatch.setattr(backtest_mod, "fetch_higher_tf", lambda *a, **kw: df)

    req = StrategyRequest(
        ticker="SYNTH",
        start="2020-01-01",
        end="2022-01-01",
        interval="1d",
        # Bare buy/sell rules (required by StrategyRequest schema, not consumed in b23_mode)
        buy_rules=[Rule(indicator="rsi", condition="below", value=50.0)],
        sell_rules=[Rule(indicator="rsi", condition="above", value=70.0)],
        # Regime-mode rule sets (these are what b23_mode actually reads)
        long_buy_rules=[Rule(indicator="rsi", condition="below", value=30.0)],
        long_sell_rules=[Rule(indicator="rsi", condition="above", value=70.0)],
        short_buy_rules=[],
        short_sell_rules=[],
        regime=RegimeConfig(
            enabled=True,
            timeframe="1d",
            indicator="ma",
            indicator_params={"period": 5, "type": "sma"},
            condition="above",
            min_bars=1,
        ),
    )

    # Sweep long_buy_rule_0_value: RSI<5 (never fires) vs RSI<80 (fires often)
    params = _make_params("long_buy_rule_0_value", [5.0, 80.0])
    results, timed_out, skipped = run_grid(req, params, timeout_secs=60.0, df=df)

    assert not timed_out, "regime-mode grid timed out"
    assert len(results) == 2, f"expected 2 results, got {len(results)}"

    trades_low = results[0][1].get("num_trades", 0)   # long_buy at RSI<5
    trades_high = results[1][1].get("num_trades", 0)  # long_buy at RSI<80

    assert trades_high > 0, (
        f"RSI<80 combo produced zero trades — synthetic data is degenerate, "
        f"not a param-substitution regression"
    )
    assert trades_low != trades_high, (
        f"F187 regression (regime mode): long_buy_rule_0_value=5.0 and =80.0 both produced "
        f"{trades_low} trades — the F187 bug has regressed: long_buy_rules are not being "
        f"updated by the optimizer param substitution"
    )


# ---------------------------------------------------------------------------
# 4. params_period integration sweep (F187 + indicator_cache guard)
#    NOTE: params_period sweeps must NOT share an indicator_cache across combos.
#    run_grid creates one cache per grid run; if _apply_param silently no-ops on
#    a params_ path, both combos get identical rules and hit the same cache key,
#    hiding the regression. This test catches that path.
# ---------------------------------------------------------------------------

def test_nonregime_buy_rule_params_period_sweep_produces_different_results():
    """F187 regression: sweeping buy_rule_0_params_period must produce distinct results.

    RSI period:
      - period=5  → fast RSI, oscillates quickly → different trade pattern
      - period=50 → slow RSI, sluggish oscillation → different trade pattern

    Different periods must produce different trade counts (or at minimum, results
    must differ). At least one combo must produce >0 trades to confirm the synthetic
    data is not degenerate.
    """
    df = _make_synthetic_df()

    req = StrategyRequest(
        ticker="SYNTH",
        start="2020-01-01",
        end="2022-01-01",
        interval="1d",
        buy_rules=[Rule(indicator="rsi", condition="below", value=40.0, params={"period": 14})],
        sell_rules=[Rule(indicator="rsi", condition="above", value=60.0, params={"period": 14})],
    )

    params = _make_params("buy_rule_0_params_period", [5, 50])
    results, timed_out, skipped = run_grid(req, params, timeout_secs=60.0, df=df)

    assert not timed_out, "grid timed out on params_period sweep"
    assert skipped == 0, f"unexpected skipped combos: {skipped}"
    assert len(results) == 2, f"expected 2 results, got {len(results)}"

    trades_fast = results[0][1].get("num_trades", 0)   # period=5
    trades_slow = results[1][1].get("num_trades", 0)   # period=50

    # At least one combo must produce trades to confirm synthetic data is not degenerate
    assert trades_fast > 0 or trades_slow > 0, (
        "Both RSI period combos (5 and 50) produced zero trades — "
        "synthetic data is degenerate, not a param-substitution regression"
    )
    assert trades_fast != trades_slow, (
        f"F187 regression: buy_rule_0_params_period=5 and =50 both produced "
        f"{trades_fast} trades — params_period substitution is broken"
    )

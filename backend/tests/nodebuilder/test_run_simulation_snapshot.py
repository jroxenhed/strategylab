"""
Unit 8a: Snapshot regression tests for run_backtest().

Step 1: Run once to populate fixtures/run_backtest_snapshots/{name}.json and
        fixtures/run_backtest_snapshots/{name}_df.pkl (base-TF DataFrame pickle).
Step 2: After _run_simulation extraction, rerun — all 10 must pass rel=1e-9.

DO NOT regenerate fixtures to make tests pass. Fix the refactor instead.

Data determinism: the base-TF DataFrame is stored as a pickle fixture alongside
the JSON snapshot. On subsequent runs, the pickle is loaded and passed as df=
to run_backtest(). This guarantees bit-identical prices regardless of yfinance
adjusted-close revisions between CI runs. The pkl + JSON pair is committed
together (Step 1 checkpoint).

Note: regime strategies call fetch_higher_tf() internally for HTF data.
That path uses _fetch() which is TTL-cached within a process; we accept tiny
drift there since regime_series is compared as categorical (direction strings),
not as a numeric.
"""
import os
import sys
import pickle
import pytest
import pandas as pd

# Ensure backend root is on the path when running from repo root
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from models import StrategyRequest, Rule, RegimeConfig, TrailingStopConfig
from routes.backtest import run_backtest
from shared import _fetch
from tests.nodebuilder._snapshot_helpers import dump_snapshot, load_snapshot, assert_equal_within_tolerance

_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "run_backtest_snapshots")

# ---------------------------------------------------------------------------
# Strategy definitions
# ---------------------------------------------------------------------------

def _make_strategies():
    """Return list of (name, StrategyRequest) tuples for the 10 fixture strategies."""
    return [
        (
            "simple_long_rsi",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
            ),
        ),
        (
            "simple_short_rsi",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                direction="short",
                buy_rules=[Rule(indicator="rsi", condition="above", value=70)],
                sell_rules=[Rule(indicator="rsi", condition="below", value=30)],
            ),
        ),
        (
            "macd_crossover",
            StrategyRequest(
                ticker="SPY", start="2021-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="macd", condition="crosses_above", param="signal")],
                sell_rules=[Rule(indicator="macd", condition="crosses_below", param="signal")],
            ),
        ),
        (
            "trailing_stop_pct",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
                trailing_stop=TrailingStopConfig(type="pct", value=5.0),
            ),
        ),
        (
            "atr_trailing_stop",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
                trailing_stop=TrailingStopConfig(type="atr", value=2.0),
            ),
        ),
        (
            "regime_mode",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                # Unified buy/sell rules (used as fallback when regime not active)
                buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
                # Per-direction rules (regime b23 mode)
                long_buy_rules=[Rule(indicator="rsi", condition="below", value=35)],
                long_sell_rules=[Rule(indicator="rsi", condition="above", value=65)],
                short_buy_rules=[Rule(indicator="rsi", condition="above", value=65)],
                short_sell_rules=[Rule(indicator="rsi", condition="below", value=35)],
                regime=RegimeConfig(
                    enabled=True,
                    rules=[Rule(indicator="ma", condition="above", param="ma:200:sma")],
                    on_flip="close_only",
                ),
            ),
        ),
        (
            "nonzero_costs",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
                per_share_rate=0.0035,
                min_per_order=0.35,
                slippage_bps=5.0,
            ),
        ),
        (
            "per_direction_b23",
            StrategyRequest(
                ticker="SPY", start="2021-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[],
                sell_rules=[],
                long_buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                short_buy_rules=[Rule(indicator="rsi", condition="above", value=70)],
                long_sell_rules=[Rule(indicator="rsi", condition="above", value=50)],
                short_sell_rules=[Rule(indicator="rsi", condition="below", value=50)],
                regime=RegimeConfig(
                    enabled=True,
                    rules=[Rule(indicator="ma", condition="above", param="ma:200:sma")],
                    on_flip="close_only",
                ),
            ),
        ),
        (
            "not_negated_rule",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                # negated=True means NOT(RSI < 30), i.e., RSI >= 30 triggers buy
                buy_rules=[Rule(indicator="rsi", condition="below", value=30, negated=True)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
            ),
        ),
        (
            "max_bars_held",
            StrategyRequest(
                ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d",
                source="yahoo",
                buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
                sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
                max_bars_held=10,
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Parametrize
# ---------------------------------------------------------------------------

_STRATEGIES = _make_strategies()
_STRATEGY_IDS = [name for name, _ in _STRATEGIES]


def _pickle_path(name: str) -> str:
    return os.path.join(_FIXTURES_DIR, f"{name}_df.pkl")


def _json_path(name: str) -> str:
    return os.path.join(_FIXTURES_DIR, f"{name}.json")


@pytest.mark.parametrize("name,req", _STRATEGIES, ids=_STRATEGY_IDS)
def test_run_backtest_snapshot(name, req):
    """
    On first run (fixture missing): fetch df, run_backtest, write JSON + parquet, skip.
    On subsequent runs: load parquet df, run_backtest with df=, compare vs JSON
    within rel=1e-9.

    DO NOT regenerate fixtures to fix failures — fix the refactor instead.
    """
    json_fp = _json_path(name)
    pickle_fp = _pickle_path(name)
    fixture_exists = os.path.exists(json_fp) and os.path.exists(pickle_fp)

    if not fixture_exists:
        # First run: fetch live, write fixtures
        os.makedirs(_FIXTURES_DIR, exist_ok=True)
        df = _fetch(req.ticker, req.start, req.end, req.interval, source=req.source)
        actual = run_backtest(req, include_spy_correlation=False, df=df)
        # Write pickle for future deterministic re-runs (bit-identical dtypes)
        with open(pickle_fp, "wb") as fh:
            pickle.dump(df, fh, protocol=pickle.HIGHEST_PROTOCOL)
        dump_snapshot(actual, json_fp)
        num_trades = actual["summary"].get("num_trades", 0)
        equity_len = len(actual.get("equity_curve", []))
        pytest.skip(
            f"[FIXTURE CREATED] {name}: num_trades={num_trades}, "
            f"equity_curve_len={equity_len}, json={json_fp}"
        )

    # Subsequent runs: load pickled df for bit-identical prices, then run
    with open(pickle_fp, "rb") as fh:
        df = pickle.load(fh)
    actual = run_backtest(req, include_spy_correlation=False, df=df)
    expected = load_snapshot(json_fp)
    assert_equal_within_tolerance(actual, expected, rel=1e-9, path=name)

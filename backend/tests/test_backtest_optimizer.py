"""Tests for POST /api/backtest/optimize — multi-parameter grid-search optimizer.

Tests are written against the post-Phase-A interface:
  - 500 HTTPExceptions re-raise (not silently skipped)
  - Non-HTTP exceptions (ValueError, etc.) are caught per-combo and counted as skipped
  - timed_out field present in OptimizeResponse
  - model_copy(deep=True) removed (req.base passed directly; _apply_param deep-copies)
"""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import time
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_summary(**overrides) -> dict:
    """Return a minimal backtest result dict matching the fields the optimizer reads."""
    defaults = {
        "summary": {
            "num_trades": 5,
            "total_return_pct": 10.0,
            "sharpe_ratio": 1.2,
            "win_rate_pct": 60.0,
            "max_drawdown_pct": -5.0,
            "ev_per_trade": 2.0,
        }
    }
    if overrides:
        defaults["summary"].update(overrides)
    return defaults


def _base_request() -> dict:
    """Minimal valid StrategyRequest payload for optimizer tests."""
    return {
        "ticker": "AAPL",
        "start": "2023-01-01",
        "end": "2024-01-01",
        "interval": "1d",
        "buy_rules": [{"indicator": "price", "condition": "below", "value": 9999}],
        "sell_rules": [{"indicator": "price", "condition": "above", "value": 1}],
    }


def _optimize_payload(params=None, metric="sharpe_ratio", top_n=10) -> dict:
    """Assemble a full /api/backtest/optimize request body."""
    if params is None:
        params = [
            {"path": "stop_loss_pct", "values": [3.0, 5.0]},
        ]
    return {
        "base": _base_request(),
        "params": params,
        "metric": metric,
        "top_n": top_n,
    }


# ---------------------------------------------------------------------------
# 1. Happy path — 2-param grid, results ranked by chosen metric
# ---------------------------------------------------------------------------

def test_happy_path_2_param_grid(monkeypatch):
    """2-param grid returns correct total_combos, completed, results ranked by metric."""
    call_count = 0

    def mock_run_backtest(req, **kwargs):
        nonlocal call_count
        call_count += 1
        # Return varying sharpe values so we can verify sort order
        sharpe = call_count * 0.5  # 0.5, 1.0, 1.5, 2.0
        return _minimal_summary(sharpe_ratio=sharpe)

    import routes.backtest_optimizer as opt_mod


    import routes.grid_runner as grid_runner_mod
    monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)

    payload = _optimize_payload(
        params=[
            {"path": "stop_loss_pct", "values": [3.0, 5.0]},
            {"path": "slippage_bps", "values": [1.0, 2.0]},
        ],
        metric="sharpe_ratio",
        top_n=10,
    )
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_combos"] == 4          # 2 × 2
    assert data["completed"] == 4
    assert data["skipped"] == 0
    assert data["timed_out"] is False
    assert len(data["results"]) == 4

    # Results must be sorted descending by sharpe_ratio
    sharpes = [r["sharpe_ratio"] for r in data["results"]]
    assert sharpes == sorted(sharpes, reverse=True)


def test_happy_path_top_n_limits_results(monkeypatch):
    """top_n truncates the returned list."""
    def mock_run_backtest(req, **kwargs):
        return _minimal_summary()

    import routes.backtest_optimizer as opt_mod


    import routes.grid_runner as grid_runner_mod
    monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)

    payload = _optimize_payload(
        params=[{"path": "stop_loss_pct", "values": [1.0, 2.0, 3.0, 4.0, 5.0]}],
        top_n=3,
    )
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_combos"] == 5
    assert data["completed"] == 5
    assert len(data["results"]) == 3


# ---------------------------------------------------------------------------
# 2. Validation guards — all 7 branches must return 400
# ---------------------------------------------------------------------------

def test_validation_empty_params():
    payload = _optimize_payload(params=[])
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 400
    assert "param" in resp.json()["detail"].lower()


def test_validation_too_many_params():
    """More than 3 params → 400."""
    payload = _optimize_payload(params=[
        {"path": "stop_loss_pct", "values": [1.0]},
        {"path": "slippage_bps", "values": [1.0]},
        {"path": "buy_rule_0_value", "values": [1.0]},
        {"path": "sell_rule_0_value", "values": [1.0]},
    ])
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 400


def test_validation_invalid_metric():
    payload = _optimize_payload(metric="not_a_metric")
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 400
    assert "metric" in resp.json()["detail"].lower()


def test_validation_top_n_zero():
    payload = _optimize_payload(top_n=0)
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 400
    assert "top_n" in resp.json()["detail"].lower()


def test_validation_top_n_above_max():
    """top_n=51 exceeds the max of 50."""
    payload = _optimize_payload(top_n=51)
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 400
    assert "top_n" in resp.json()["detail"].lower()


def test_validation_empty_values_list():
    """A param with empty values list → 400."""
    payload = _optimize_payload(params=[
        {"path": "stop_loss_pct", "values": []},
    ])
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 400
    assert "values" in resp.json()["detail"].lower()


def test_validation_too_many_values_per_param():
    """A param with 11 values (>10) → 400."""
    payload = _optimize_payload(params=[
        {"path": "stop_loss_pct", "values": list(range(1, 12))},  # 11 values
    ])
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 400


def test_validation_combos_exceed_max():
    """3 params × 8 values = 512 combos > 200 → 400."""
    payload = _optimize_payload(params=[
        {"path": "stop_loss_pct", "values": [float(v) for v in range(1, 9)]},       # 8
        {"path": "slippage_bps", "values": [float(v) for v in range(1, 9)]},        # 8
        {"path": "buy_rule_0_value", "values": [float(v) for v in range(1, 9)]},    # 8
    ])
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 400
    assert "200" in resp.json()["detail"] or "combination" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. 4xx skip — HTTPException(400) on specific combos increments skipped
# ---------------------------------------------------------------------------

def test_4xx_skip_increments_skipped(monkeypatch):
    """HTTPException(400) from run_backtest counts as skipped; other combos still returned."""
    call_count = 0

    def mock_run_backtest(req, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise HTTPException(status_code=400, detail="invalid param for this combo")
        return _minimal_summary()

    import routes.backtest_optimizer as opt_mod


    import routes.grid_runner as grid_runner_mod
    monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)

    payload = _optimize_payload(params=[
        {"path": "stop_loss_pct", "values": [1.0, 5.0, 10.0]},
    ])
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["skipped"] == 1
    assert data["completed"] == 2
    assert len(data["results"]) == 2


# ---------------------------------------------------------------------------
# 4. 500 re-raise — HTTPException(500) must surface as 500 (not 200)
# ---------------------------------------------------------------------------

def test_500_re_raises(monkeypatch):
    """HTTPException(500) from run_backtest must propagate — not be counted as skipped."""
    def mock_run_backtest(req, **kwargs):
        raise HTTPException(status_code=500, detail="data provider failure")

    import routes.backtest_optimizer as opt_mod


    import routes.grid_runner as grid_runner_mod
    monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)

    payload = _optimize_payload(params=[
        {"path": "stop_loss_pct", "values": [5.0]},
    ])
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# 5. Non-HTTP exception — ValueError counts as skipped, not full abort
# ---------------------------------------------------------------------------

def test_non_http_exception_counts_as_skipped(monkeypatch):
    """ValueError (or similar) from run_backtest is isolated per-combo and counted as skipped."""
    call_count = 0

    def mock_run_backtest(req, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise ValueError("unexpected pandas error")
        return _minimal_summary()

    import routes.backtest_optimizer as opt_mod


    import routes.grid_runner as grid_runner_mod
    monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)

    payload = _optimize_payload(params=[
        {"path": "stop_loss_pct", "values": [1.0, 2.0, 3.0]},
    ])
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["skipped"] == 1
    assert data["completed"] == 2


# ---------------------------------------------------------------------------
# 6. Timeout — timed_out=True when deadline is hit before all combos finish
# ---------------------------------------------------------------------------

def test_timeout_sets_timed_out_flag(monkeypatch):
    """Wall-clock timeout fires before all combos complete → timed_out=True.

    Strategy: set _TIMEOUT_SECS to 0 so the deadline fires after the first combo.
    """
    import routes.backtest_optimizer as opt_mod

    import routes.grid_runner as grid_runner_mod

    monkeypatch.setattr(opt_mod, "_TIMEOUT_SECS", 0)

    def mock_run_backtest(req, **kwargs):
        return _minimal_summary()

    monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)

    payload = _optimize_payload(params=[
        {"path": "stop_loss_pct", "values": [1.0, 2.0, 3.0, 4.0, 5.0]},
    ])
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["timed_out"] is True
    assert data["completed"] < 5


# ---------------------------------------------------------------------------
# 7. Metric sort correctness — verify sort order for each supported metric
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("metric,field,values", [
    ("sharpe_ratio",      "sharpe_ratio",      [0.5, 2.0, 1.5, 1.0]),
    ("total_return_pct",  "total_return_pct",  [5.0, 20.0, 15.0, 10.0]),
    ("win_rate_pct",      "win_rate_pct",       [40.0, 80.0, 60.0, 70.0]),
])
def test_metric_sort_order(monkeypatch, metric, field, values):
    """Results are sorted descending by each supported metric."""
    value_iter = iter(values)

    def mock_run_backtest(req, **kwargs):
        v = next(value_iter)
        return _minimal_summary(**{field: v})

    import routes.backtest_optimizer as opt_mod


    import routes.grid_runner as grid_runner_mod
    monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)

    payload = _optimize_payload(
        params=[{"path": "stop_loss_pct", "values": [1.0, 2.0, 3.0, 4.0]}],
        metric=metric,
    )
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 4

    actual = [r[field] for r in results]
    expected = sorted(values, reverse=True)
    assert actual == pytest.approx(expected, rel=1e-3), (
        f"Results not sorted descending by {metric}: got {actual}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# 8. Response schema — timed_out field always present in response
# ---------------------------------------------------------------------------

def test_response_has_timed_out_field(monkeypatch):
    """timed_out must be present in all successful responses (False when no timeout)."""
    def mock_run_backtest(req, **kwargs):
        return _minimal_summary()

    import routes.backtest_optimizer as opt_mod


    import routes.grid_runner as grid_runner_mod
    monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)

    payload = _optimize_payload()
    resp = client.post("/api/backtest/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "timed_out" in data
    assert data["timed_out"] is False

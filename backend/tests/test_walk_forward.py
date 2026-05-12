"""Tests for POST /api/backtest/walk_forward — walk-forward analysis.

TDD order:
  1. test_rescaling_math               — pure rescaling logic (write before implementing)
  2. test_neighborhood_stability_tag   — pure stability tag logic (write before implementing)
  3. Remaining integration tests
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

def _base_request(interval="1d") -> dict:
    """Minimal valid StrategyRequest payload — daily interval by default."""
    return {
        "ticker": "AAPL",
        "start": "2018-01-01",
        "end": "2024-01-01",
        "interval": interval,
        "buy_rules": [{"indicator": "price", "condition": "below", "value": 9999}],
        "sell_rules": [{"indicator": "price", "condition": "above", "value": 1}],
    }


def _wf_payload(**overrides) -> dict:
    """Minimal valid WalkForwardRequest payload (rolling, 1 param, 2 value combos)."""
    payload = {
        "base": _base_request(),
        "params": [
            {"path": "stop_loss_pct", "values": [3.0, 5.0]},
        ],
        "is_bars": 200,
        "oos_bars": 100,
        "gap_bars": 0,
        "metric": "sharpe_ratio",
        "min_trades_is": 1,
    }
    payload.update(overrides)
    return payload


def _minimal_backtest_result(num_trades=5, sharpe=1.0, equity_start=10000.0, equity_end=11000.0) -> dict:
    """Return a backtest result dict matching what run_backtest() returns."""
    return {
        "summary": {
            "num_trades": num_trades,
            "total_return_pct": (equity_end - equity_start) / equity_start * 100,
            "sharpe_ratio": sharpe,
            "win_rate_pct": 60.0,
            "max_drawdown_pct": -5.0,
            "final_value": equity_end,
            "initial_capital": equity_start,
        },
        "equity_curve": [
            {"time": "2020-01-01", "value": equity_start},
            {"time": "2020-06-01", "value": (equity_start + equity_end) / 2},
            {"time": "2020-12-31", "value": equity_end},
        ],
        "trades": [],
    }


def _make_mock_df(n=400, start="2019-01-01", freq="B"):
    """Return a synthetic n-bar DataFrame for _fetch mocking.

    freq accepts pandas offset aliases — "B" (business day, default) or
    "5min"/"1min" for intraday tests.
    """
    import pandas as pd
    import numpy as np
    dates = pd.date_range(start, periods=n, freq=freq)
    return pd.DataFrame({
        "Open": np.ones(n) * 100,
        "High": np.ones(n) * 101,
        "Low": np.ones(n) * 99,
        "Close": np.ones(n) * 100,
        "Volume": np.ones(n) * 1000,
    }, index=dates)


# ---------------------------------------------------------------------------
# TDD PURE-FUNCTION TESTS (write first, before implementing)
# ---------------------------------------------------------------------------

class TestRescalingMath:
    """
    test_rescaling_math — Two windows, first OOS ends at $12,000 (started at $10,000).
    Second window's scale_factor must equal 1.2.
    The stitched_equity must not have a sawtooth jump back to $10,000.
    """

    def test_rescaling_scale_factor_second_window(self, monkeypatch):
        """
        Second window scale_factor = prev_final_equity / initial_capital = 12000/10000 = 1.2.
        Uses a call-counter dispatcher: IS calls see one result, OOS calls see another.
        """
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        mock_df = _make_mock_df(n=400, start="2019-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        call_state = {"is_done": 0, "oos_calls": 0}

        # 400 bars, is=100, oos=100 → 2 windows
        # Each window: 2 IS combos (stop_loss_pct=[3.0,5.0]) + 1 OOS
        # Call order: IS combo 1, IS combo 2, OOS → IS combo 1, IS combo 2, OOS
        # We track OOS calls by count.

        def mock_run_backtest(req, **kwargs):
            # IS window dates will have start <= bar 100 approximately
            # Simplest: track whether we've produced enough IS results yet
            # OOS window start is always later than IS end
            # Use is_end marker from first window: bars 0-99 → ~2019-05-21
            # Check if this is an OOS window by comparing start vs the first IS end
            # More robustly: count unique (start, end) combos for OOS
            import re
            # IS combos use dates within the IS slice; OOS has a later start date
            # All IS windows have end before OOS start
            # Track by counting how many backtests have run
            call_state["is_done"] += 1
            n = call_state["is_done"]
            # Pattern: for 2 combos per IS window, calls 1-2 are window1-IS,
            # call 3 is window1-OOS, calls 4-5 are window2-IS, call 6 is window2-OOS
            if n == 3:  # First OOS
                call_state["oos_calls"] += 1
                return _minimal_backtest_result(
                    num_trades=5, sharpe=0.8,
                    equity_start=10000.0, equity_end=12000.0,
                )
            elif n == 6:  # Second OOS
                call_state["oos_calls"] += 1
                return _minimal_backtest_result(
                    num_trades=5, sharpe=0.9,
                    equity_start=10000.0, equity_end=11500.0,
                )
            else:
                return _minimal_backtest_result(num_trades=5, sharpe=1.0, equity_end=11000.0)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        resp = client.post("/api/backtest/walk_forward", json=_wf_payload(is_bars=100, oos_bars=100))
        assert resp.status_code == 200
        data = resp.json()

        windows = data["windows"]
        assert len(windows) >= 2

        w0 = windows[0]
        w1 = windows[1]

        # First window scale_factor must be 1.0
        assert abs(w0["scale_factor"] - 1.0) < 1e-6, (
            f"Expected scale_factor=1.0 for window 0, got {w0['scale_factor']}"
        )

        # Second window scale_factor must be 12000/10000 = 1.2 exactly
        assert abs(w1["scale_factor"] - 1.2) < 1e-6, (
            f"Expected scale_factor=1.2 for window 1, got {w1['scale_factor']}"
        )

    def test_stitched_equity_no_sawtooth(self, monkeypatch):
        """
        Stitched equity must not jump back to initial_capital between windows.
        The seam between windows must be continuous to within $1.
        """
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        mock_df = _make_mock_df(n=400, start="2019-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        # Use a rising equity curve for OOS: ends at 1.2x initial each time
        def mock_run_backtest(req, **kwargs):
            return _minimal_backtest_result(
                num_trades=5, sharpe=1.2,
                equity_start=10000.0, equity_end=12000.0,
            )

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        resp = client.post("/api/backtest/walk_forward", json=_wf_payload(is_bars=100, oos_bars=100))
        assert resp.status_code == 200
        data = resp.json()
        equity = data["stitched_equity"]
        assert len(equity) >= 2

        values = [pt["value"] for pt in equity]
        # 3 points per window. First window ends at values[2], second window starts at values[3].
        if len(values) > 3:
            first_window_end = values[2]
            second_window_start = values[3]
            # Must be continuous — no sawtooth reset to 10000
            assert abs(second_window_start - first_window_end) < 1.0, (
                f"Sawtooth detected: second window starts at {second_window_start}, "
                f"first window ended at {first_window_end}"
            )


class TestNeighborhoodStabilityTag:
    """
    test_neighborhood_stability_tag — Test the IS winner stability tag logic.
    Manufacture IS results where top combo neighbors are all below Q75 → "spike".
    Manufacture IS results where ≥60% of neighbors are in Q75 → "stable_plateau".
    """

    def test_spike_when_neighbors_below_q75(self, monkeypatch):
        """
        Deterministic 2-window setup: 3 values, middle is always the winner with
        sharpe=5.0; neighbors always have sharpe=0.1 (below Q75) → all windows spike.
        """
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        mock_df = _make_mock_df(n=400, start="2019-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        sharpes = {3.0: 0.1, 5.0: 5.0, 7.0: 0.1}

        def mock_run_backtest(req, **kwargs):
            slp = req.stop_loss_pct
            s = sharpes.get(round(slp, 1), 1.0)
            return _minimal_backtest_result(num_trades=10, sharpe=s)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        payload = _wf_payload(is_bars=100, oos_bars=100)
        payload["params"] = [{"path": "stop_loss_pct", "values": [3.0, 5.0, 7.0]}]
        payload["min_trades_is"] = 1

        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        windows = data["windows"]
        assert len(windows) >= 2

        # All windows should be spike (isolated peak in the middle)
        tags = [w["stability_tag"] for w in windows]
        assert all(t == "spike" for t in tags), (
            f"Expected all windows to be 'spike' with isolated peak, got: {tags}"
        )

    def test_stable_plateau_when_neighbors_above_q75(self, monkeypatch):
        """
        If ≥60% of top combo's neighbors have Sharpe in Q75 → "stable_plateau".
        Setup: 3 values [3.0, 5.0, 7.0], all have high Sharpe ≥ Q75.
        Winner is 5.0 (middle); neighbors 3.0 and 7.0 also in top quartile.
        """
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        mock_df = _make_mock_df(n=400, start="2019-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        def mock_run_backtest(req, **kwargs):
            # All combos return high sharpe — plateau
            return _minimal_backtest_result(num_trades=10, sharpe=1.8)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        payload = _wf_payload(is_bars=100, oos_bars=100)
        payload["params"] = [{"path": "stop_loss_pct", "values": [3.0, 5.0, 7.0]}]
        payload["min_trades_is"] = 1

        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        windows = data["windows"]
        assert len(windows) >= 1

        # All windows should be "stable_plateau" (all combos are equally good)
        tags = [w["stability_tag"] for w in windows]
        assert "stable_plateau" in tags, (
            f"Expected at least one 'stable_plateau' tag but got: {tags}"
        )


# ---------------------------------------------------------------------------
# Integration tests (after pure-function TDD is passing)
# ---------------------------------------------------------------------------

class TestValidation:
    """Validation guards."""

    def test_insufficient_bars_rejected(self, monkeypatch):
        """is_bars=1000, oos_bars=1000 on a small dataset → 400 'Not enough bars'."""
        import routes.walk_forward as wf_mod

        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: _make_mock_df(n=100))
        payload = _wf_payload(is_bars=1000, oos_bars=1000)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 400
        assert "bars" in resp.json()["detail"].lower() or "window" in resp.json()["detail"].lower()

    def test_single_window_rejected(self, monkeypatch):
        """is_bars=80, oos_bars=20 on exactly 100 bars → 1 window → 400."""
        import routes.walk_forward as wf_mod

        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: _make_mock_df(n=100))
        payload = _wf_payload(is_bars=80, oos_bars=20)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 400
        assert "2 windows" in resp.json()["detail"].lower() or "window" in resp.json()["detail"].lower()

    def test_intraday_5m_window_boundaries_use_datetime_precision(self, monkeypatch):
        """
        Intraday window boundaries must serialize with HH:MM:SS so adjacent IS-end
        and OOS-start bars on the same calendar day don't collide on string equality
        (which would re-fetch the full day and leak IS into OOS).
        """
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod
        # 1500 bars of 5m = ~2 trading days worth of bars
        mock_df = _make_mock_df(n=1500, start="2024-03-01 09:30:00", freq="5min")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)
        monkeypatch.setattr(
            wf_mod, "run_backtest", lambda req, **kwargs: _minimal_backtest_result()
        )

        payload = _wf_payload(is_bars=500, oos_bars=200, min_trades_is=1)
        payload["base"]["interval"] = "5m"
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        # Every window's is_end must include a time component (intraday precision)
        for w in data["windows"]:
            assert " " in w["is_end"], f"is_end missing time component: {w['is_end']}"
            assert ":" in w["is_end"], f"is_end missing HH:MM:SS: {w['is_end']}"
            # IS end and OOS start on different bars must serialize to different strings
            assert w["is_end"] != w["oos_start"], (
                f"is_end == oos_start would cause provider re-fetch leakage: "
                f"{w['is_end']!r}"
            )

    def test_intraday_provider_limit_surfaces_helpful_error(self, monkeypatch):
        """
        When the user asks for more bars than the provider supports for the interval,
        the 400 should mention the provider's max-days limit so the user can fix it.
        """
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod
        # 5m provider limit is 60 days; mock only 100 bars available (provider clamp)
        mock_df = _make_mock_df(n=100, start="2024-03-01 09:30:00", freq="5min")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        payload = _wf_payload(is_bars=500, oos_bars=200)
        payload["base"]["interval"] = "5m"
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 400
        detail = resp.json()["detail"].lower()
        assert "60 days" in detail or "provider" in detail, (
            f"Expected provider-limit hint in error, got: {detail!r}"
        )

    def test_max_combos_cap_per_window(self):
        """3 params × 7 values = 343 combos > 200 → 400 before any backtest runs."""
        payload = _wf_payload()
        payload["params"] = [
            {"path": "stop_loss_pct", "values": [float(i) for i in range(1, 8)]},
            {"path": "slippage_bps", "values": [float(i) for i in range(1, 8)]},
            {"path": "buy_rule_0_value", "values": [float(i) for i in range(1, 8)]},
        ]
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 400
        assert "200" in resp.json()["detail"] or "combination" in resp.json()["detail"].lower()

    def test_invalid_metric_rejected(self):
        """Unknown metric → 400."""
        payload = _wf_payload(metric="not_a_real_metric")
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 400
        assert "metric" in resp.json()["detail"].lower()

    def test_empty_params_rejected(self):
        """Empty params list → 422 (Pydantic Field constraint)."""
        payload = _wf_payload()
        payload["params"] = []
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 422

    def test_zero_is_bars_rejected(self):
        """is_bars=0 → 422 (Pydantic Field gt=0)."""
        payload = _wf_payload(is_bars=0)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 422

    def test_zero_oos_bars_rejected(self):
        """oos_bars=0 → 422 (Pydantic Field gt=0)."""
        payload = _wf_payload(oos_bars=0)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 422

    def test_negative_gap_bars_rejected(self):
        """gap_bars=-1 → 422 (Pydantic Field ge=0)."""
        payload = _wf_payload(gap_bars=-1)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 422

    def test_empty_values_rejected(self):
        """params with empty values list → 422 (Pydantic min_length=1)."""
        payload = _wf_payload()
        payload["params"] = [{"path": "stop_loss_pct", "values": []}]
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 422

    def test_too_many_values_rejected(self):
        """11 values → 422 (Pydantic max_length=10)."""
        payload = _wf_payload()
        payload["params"] = [{"path": "stop_loss_pct", "values": [float(i) for i in range(1, 12)]}]
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 422

    def test_nonfinite_values_rejected(self):
        """values=[inf] and values=[nan] → field_validator raises ValueError.
        Tests the Pydantic model directly rather than via HTTP (the TestClient's response
        serializer can't handle inf in the error payload's input echo).
        """
        import pydantic
        from routes.walk_forward import WalkForwardParam

        for bad_val, label in [(float("inf"), "inf"), (float("nan"), "nan")]:
            with pytest.raises(pydantic.ValidationError) as exc_info:
                WalkForwardParam(path="stop_loss_pct", values=[bad_val])
            errors = exc_info.value.errors()
            assert any("finite" in str(e).lower() or "nan" in str(e).lower() or "infinity" in str(e).lower()
                       for e in errors), (
                f"Expected finite-values error for {label}, got: {errors}"
            )

    def test_zero_initial_capital_rejected(self):
        """base.initial_capital=0 → 400 (route-level guard)."""
        payload = _wf_payload()
        payload["base"]["initial_capital"] = 0
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 400
        assert "initial_capital" in resp.json()["detail"].lower()

    def test_step_smaller_than_oos_rejected(self):
        """step_bars=10, oos_bars=100 → 400 (overlapping OOS)."""
        payload = _wf_payload(step_bars=10, oos_bars=100)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 400
        assert "overlap" in resp.json()["detail"].lower() or "step" in resp.json()["detail"].lower()


class TestWindowLogic:
    """Window computation and WFA loop behavior."""

    def test_f169_df_slice_size_matches_window_config(self, monkeypatch):
        """F169 fence: every df= kwarg passed to run_backtest from the WFA loop
        must contain exactly `is_bars` rows (for IS combos) or `oos_bars` rows
        (for OOS). Catches future regressions to the pre-F169 off-by-one where
        provider exclusive-end semantics shaved one bar per window."""
        import routes.walk_forward as wf_mod
        import routes.grid_runner as grid_runner_mod

        n = 400
        is_bars, oos_bars = 100, 50
        mock_df = _make_mock_df(n=n, start="2020-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        captured_lens = []

        def mock_run_backtest(req, **kwargs):
            df = kwargs.get("df")
            captured_lens.append(len(df) if df is not None else None)
            return _minimal_backtest_result()

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)
        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        resp = client.post(
            "/api/backtest/walk_forward",
            json=_wf_payload(is_bars=is_bars, oos_bars=oos_bars),
        )
        assert resp.status_code == 200, resp.text

        assert captured_lens, "expected run_backtest to be called at least once"
        assert all(
            n is not None for n in captured_lens
        ), f"every WFA call must pass df=; got {captured_lens.count(None)} None entries"
        # Every IS combo passes is_bars-long slice; every OOS passes oos_bars-long.
        # Any other size means an off-by-one slice — the bug F169 fixed.
        assert all(
            n in (is_bars, oos_bars) for n in captured_lens
        ), f"unexpected df lengths {set(captured_lens)} — expected only {{{is_bars}, {oos_bars}}}"

    def test_single_param_two_windows(self, monkeypatch):
        """Small synthetic dataset, 1 param, rolling. Assert 2 windows, non-empty stitched equity,
        time-sorted, no duplicate timestamps at seam."""
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        n = 300
        mock_df = _make_mock_df(n=n, start="2020-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        def mock_run_backtest(req, **kwargs):
            return _minimal_backtest_result(num_trades=5, sharpe=1.0)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        payload = _wf_payload(is_bars=100, oos_bars=100)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["windows"]) == 2

        equity = data["stitched_equity"]
        assert len(equity) > 0

        times = [pt["time"] for pt in equity]
        assert times == sorted(times), f"stitched_equity is not time-sorted: {times}"
        assert len(times) == len(set(times)), f"Duplicate timestamps in stitched_equity: {times}"

    def test_anchored_vs_rolling_window_counts(self, monkeypatch):
        """Rolling and anchored produce the same number of windows;
        anchored windows have constant is_start, growing is_end, and distinct oos_start."""
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        n = 500
        mock_df = _make_mock_df(n=n, start="2019-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        def mock_run_backtest(req, **kwargs):
            return _minimal_backtest_result(num_trades=5, sharpe=1.0)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        payload_rolling = _wf_payload(is_bars=100, oos_bars=100, expand_train=False)
        payload_anchored = _wf_payload(is_bars=100, oos_bars=100, expand_train=True)

        resp_r = client.post("/api/backtest/walk_forward", json=payload_rolling)
        resp_a = client.post("/api/backtest/walk_forward", json=payload_anchored)

        assert resp_r.status_code == 200
        assert resp_a.status_code == 200

        r_windows = resp_r.json()["windows"]
        a_windows = resp_a.json()["windows"]

        assert len(r_windows) >= 2
        assert len(a_windows) >= 2

        # Both modes must produce the same window count on the same data
        assert len(r_windows) == len(a_windows), (
            f"Rolling={len(r_windows)} windows, anchored={len(a_windows)} — must match"
        )

        # Anchored: all windows must have the same is_start (anchored at data start)
        anchored_is_starts = [w["is_start"] for w in a_windows]
        assert len(set(anchored_is_starts)) == 1, (
            f"Anchored windows should all have the same is_start, got: {anchored_is_starts}"
        )

        # Anchored: IS end must grow monotonically across windows
        anchored_is_ends = [w["is_end"] for w in a_windows]
        assert len(set(anchored_is_ends)) > 1, (
            f"Anchored IS end should grow across windows, got: {anchored_is_ends}"
        )
        assert sorted(anchored_is_ends) == anchored_is_ends, (
            f"Anchored IS end is not monotonically increasing: {anchored_is_ends}"
        )

        # Anchored: OOS start must be distinct per window
        anchored_oos_starts = [w["oos_start"] for w in a_windows]
        assert len(set(anchored_oos_starts)) == len(a_windows), (
            f"Anchored windows should each have a distinct oos_start, got: {anchored_oos_starts}"
        )

        # Rolling: windows should have different is_start dates
        rolling_is_starts = [w["is_start"] for w in r_windows]
        assert len(set(rolling_is_starts)) > 1, (
            f"Rolling windows should have different is_start dates, got: {rolling_is_starts}"
        )

    def test_oos_zero_trades(self, monkeypatch):
        """OOS window produces zero trades → stability_tag='no_oos_trades',
        window still appears in response.windows."""
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        mock_df = _make_mock_df(n=400, start="2019-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        def mock_run_backtest(req, **kwargs):
            # IS windows (start before OOS threshold) return trades
            if req.start >= "2019-05-20":
                return _minimal_backtest_result(num_trades=0, sharpe=0.0)
            return _minimal_backtest_result(num_trades=10, sharpe=1.2)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        payload = _wf_payload(is_bars=100, oos_bars=100, min_trades_is=1)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        windows = data["windows"]
        assert len(windows) >= 1

        tags = [w["stability_tag"] for w in windows]
        assert "no_oos_trades" in tags, f"Expected 'no_oos_trades' in tags, got: {tags}"

        no_oos_windows = [w for w in windows if w["stability_tag"] == "no_oos_trades"]
        for w in no_oos_windows:
            assert w["oos_metrics"].get("num_trades", -1) == 0

    def test_timeout_returns_partial(self, monkeypatch):
        """
        Timeout fires at outer loop check before any IS grid runs.
        Uses mocked monotonic: start_time=0, first outer loop check returns 9999 → immediate
        timed_out=True on first window. No biased partial window → len(windows) == 0.
        """
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        n = 600
        mock_df = _make_mock_df(n=n, start="2018-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        def mock_run_backtest(req, **kwargs):
            return _minimal_backtest_result(num_trades=5, sharpe=1.0)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        # Mock monotonic: call 1 = start_time (0.0), call 2 = outer loop check (9999.0)
        # This triggers outer loop timeout before any IS work.
        mono_calls = {"n": 0}

        def mock_monotonic():
            mono_calls["n"] += 1
            if mono_calls["n"] == 1:
                return 0.0
            return 9999.0

        monkeypatch.setattr(wf_mod, "monotonic", mock_monotonic)

        payload = _wf_payload(is_bars=100, oos_bars=100)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["timed_out"] is True
        # Outer loop fired before IS combos ran → no windows at all
        assert len(data["windows"]) <= 1, (
            f"Expected 0 or 1 window with early timeout, got {len(data['windows'])}"
        )

    def test_no_is_trades_window_included(self, monkeypatch):
        """
        Windows where all IS combos error → stability_tag='no_is_trades',
        best_params={}, scale_factor=1.0, excluded from WFE but included in windows list.
        Mixed scenario: 300 bars, is=100, oos=100, step=100 → exactly 2 windows.
        Window 0 good (IS sharpe=2.0, OOS sharpe=1.0); window 1 all-IS-error (no_is_trades).
        WFE should equal 0.5 (OOS/IS = 1.0/2.0) from the good window only.
        """
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        # 300 bars → exactly 2 windows with is=100, oos=100, step=100
        mock_df = _make_mock_df(n=300, start="2019-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        call_counter = {"n": 0}

        # Window 0: IS combo(3.0)=call1, IS combo(5.0)=call2, OOS=call3
        # Window 1: IS combo(3.0)=call4, IS combo(5.0)=call5 → both error → no_is_trades
        def mock_run_backtest(req, **kwargs):
            call_counter["n"] += 1
            n = call_counter["n"]
            if n in (4, 5):  # Window 1 IS combos — all error
                raise HTTPException(status_code=400, detail="no signal in window")
            if n == 3:  # Window 0 OOS — sharpe=1.0
                return _minimal_backtest_result(num_trades=10, sharpe=1.0)
            # Window 0 IS combos — sharpe=2.0
            return _minimal_backtest_result(num_trades=10, sharpe=2.0)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        payload = _wf_payload(is_bars=100, oos_bars=100, min_trades_is=1)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        windows = data["windows"]
        no_is_windows = [w for w in windows if w["stability_tag"] == "no_is_trades"]
        assert len(no_is_windows) >= 1, "no_is_trades scenario did not produce expected tag"

        for w in no_is_windows:
            assert w["best_params"] == {}, f"no_is_trades window must have empty best_params"
            assert abs(w["scale_factor"] - 1.0) < 1e-6, (
                f"no_is_trades window must have scale_factor=1.0"
            )
            assert w["is_combo_count"] == 0

        # WFE should be computed from good window only: OOS/IS = 1.0/2.0 = 0.5
        assert data["wfe"] is not None, "WFE should not be None when at least one good window exists"
        assert abs(data["wfe"] - 0.5) < 1e-3, (
            f"WFE should be 0.5 from the good window (OOS=1.0, IS=2.0), got {data['wfe']}"
        )

    def test_low_windows_warn_flag(self, monkeypatch):
        """When 2 ≤ windows < 6, low_windows_warn=True in response."""
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        n = 300
        mock_df = _make_mock_df(n=n, start="2020-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        def mock_run_backtest(req, **kwargs):
            return _minimal_backtest_result(num_trades=5, sharpe=1.0)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        payload = _wf_payload(is_bars=100, oos_bars=100)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["windows"]) >= 2
        if len(data["windows"]) < 6:
            assert data["low_windows_warn"] is True

    def test_low_trades_is_tag(self, monkeypatch):
        """min_trades_is=100, mock returns num_trades=5 → low_trades_is tag on all windows,
        low_trades_is_count >= 1, windows still appear in response."""
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        mock_df = _make_mock_df(n=400, start="2019-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        def mock_run_backtest(req, **kwargs):
            return _minimal_backtest_result(num_trades=5, sharpe=1.0)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        payload = _wf_payload(is_bars=100, oos_bars=100, min_trades_is=100)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["low_trades_is_count"] >= 1
        low_windows = [w for w in data["windows"] if w["stability_tag"] == "low_trades_is"]
        assert len(low_windows) >= 1
        # These windows must appear in the windows list
        assert all(w in data["windows"] for w in low_windows)

    def test_spike_when_no_neighbors_single_value_grid(self, monkeypatch):
        """params=[{values:[5.0]}] — single value, no neighbors → all windows spike."""
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        mock_df = _make_mock_df(n=400, start="2019-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        def mock_run_backtest(req, **kwargs):
            return _minimal_backtest_result(num_trades=10, sharpe=1.0)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        payload = _wf_payload(is_bars=100, oos_bars=100, min_trades_is=1)
        payload["params"] = [{"path": "stop_loss_pct", "values": [5.0]}]

        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        tags = [w["stability_tag"] for w in data["windows"]]
        assert all(t == "spike" for t in tags), (
            f"Single-value grid should produce all spike tags, got: {tags}"
        )

    def test_wfe_none_when_all_oos_no_trades(self, monkeypatch):
        """When OOS always returns num_trades=0, all windows are no_oos_trades → wfe=None.
        Uses 300 bars, is=100, oos=100, step=100 → exactly 2 windows.
        Calls: 1-2 IS w0, call 3 OOS w0 (0 trades), calls 4-5 IS w1, call 6 OOS w1 (0 trades).
        """
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        # 300 bars → exactly 2 windows
        mock_df = _make_mock_df(n=300, start="2019-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        call_counter = {"n": 0}

        # 2 combos per IS → calls 1-2 IS w0, call 3 OOS w0, calls 4-5 IS w1, call 6 OOS w1
        def mock_run_backtest(req, **kwargs):
            call_counter["n"] += 1
            n = call_counter["n"]
            if n in (3, 6):  # Both OOS calls → 0 trades
                return _minimal_backtest_result(num_trades=0, sharpe=0.0)
            return _minimal_backtest_result(num_trades=10, sharpe=1.0)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        payload = _wf_payload(is_bars=100, oos_bars=100, min_trades_is=1)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["wfe"] is None, (
            f"WFE should be None when all OOS windows have no trades, got {data['wfe']}"
        )

    def test_wfe_none_when_is_sharpes_zero(self, monkeypatch):
        """All IS sharpe=0.0 → denominator is zero → wfe=None."""
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        mock_df = _make_mock_df(n=400, start="2019-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        def mock_run_backtest(req, **kwargs):
            return _minimal_backtest_result(num_trades=10, sharpe=0.0)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        # min_trades_is=0 so low_trades_is path doesn't fire
        payload = _wf_payload(is_bars=100, oos_bars=100, min_trades_is=0)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["wfe"] is None, (
            f"WFE should be None when mean IS sharpe=0, got {data['wfe']}"
        )

    def test_scale_factor_preserved_across_empty_oos_window(self, monkeypatch):
        """
        3-window scenario:
          w0 normal: OOS ends at $12k → prev_final_equity=12000
          w1 no_oos_trades: empty equity_curve → must NOT corrupt prev_final_equity
          w2 normal: scale_factor must equal 1.2 (12000/10000)
        """
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        # Need enough bars for 3 windows: is=100, oos=100 → need 500 bars
        mock_df = _make_mock_df(n=600, start="2018-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        call_counter = {"n": 0}
        # 2 combos per IS → for 3 windows:
        # calls 1-2 IS w0, call 3 OOS w0 (→ 12k)
        # calls 4-5 IS w1, call 6 OOS w1 (→ 0 trades)
        # calls 7-8 IS w2, call 9 OOS w2 (→ normal)

        def mock_run_backtest(req, **kwargs):
            call_counter["n"] += 1
            n = call_counter["n"]
            if n == 3:  # First OOS — ends at $12k
                return _minimal_backtest_result(
                    num_trades=5, sharpe=0.8,
                    equity_start=10000.0, equity_end=12000.0,
                )
            elif n == 6:  # Second OOS — zero trades, empty curve
                return {
                    "summary": {"num_trades": 0, "sharpe_ratio": 0.0, "total_return_pct": 0.0},
                    "equity_curve": [],
                    "trades": [],
                }
            elif n == 9:  # Third OOS — normal
                return _minimal_backtest_result(
                    num_trades=5, sharpe=0.9,
                    equity_start=10000.0, equity_end=11500.0,
                )
            else:
                return _minimal_backtest_result(num_trades=5, sharpe=1.0)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        payload = _wf_payload(is_bars=100, oos_bars=100, min_trades_is=1)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        windows = data["windows"]
        assert len(windows) >= 3, f"Expected >= 3 windows, got {len(windows)}"

        w2 = windows[2]
        assert abs(w2["scale_factor"] - 1.2) < 1e-6, (
            f"Window 2 scale_factor should be 1.2 (12000/10000), got {w2['scale_factor']}"
        )

    def test_gap_bars_positive_window_math(self, monkeypatch):
        """gap_bars=5 → OOS start index is 5 bars after IS end index."""
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        n = 400
        mock_df = _make_mock_df(n=n, start="2019-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        def mock_run_backtest(req, **kwargs):
            return _minimal_backtest_result(num_trades=5, sharpe=1.0)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)


        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        payload = _wf_payload(is_bars=100, oos_bars=100, gap_bars=5)
        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        windows = data["windows"]
        assert len(windows) >= 1

        import pandas as pd
        for w in windows:
            is_end_dt = pd.to_datetime(w["is_end"])
            oos_start_dt = pd.to_datetime(w["oos_start"])
            # OOS start must be strictly after IS end
            assert oos_start_dt > is_end_dt, (
                f"OOS start {w['oos_start']} must be after IS end {w['is_end']}"
            )
            # With gap_bars=5, there should be at least 5 business days gap
            # (pandas business days)
            delta_bdays = len(pd.bdate_range(
                start=is_end_dt + pd.Timedelta(days=1),
                end=oos_start_dt - pd.Timedelta(days=1)
            ))
            assert delta_bdays >= 4, (
                f"Expected >= 4 gap days between IS end and OOS start, got {delta_bdays}"
            )

    def test_timeout_drops_biased_window(self, monkeypatch):
        """
        Timeout that fires mid-IS-grid (after the 1st of 3 combos) must result in the
        incomplete window being dropped from data['windows'].

        After F163 the IS-grid loop runs inside grid_runner._run_grid_serial, so the
        monotonic clock that decides the partial-grid timeout lives in grid_runner,
        not walk_forward. We mock both:
          - wf_mod.monotonic returns 0 (so the outer per-window budget check passes)
          - grid_runner_mod.monotonic returns 0 for combo 1's check and 9999 for combo 2's,
            making run_grid return (1 result, timed_out=True) which WFA then drops.
        """
        import routes.walk_forward as wf_mod

        import routes.grid_runner as grid_runner_mod

        n = 600
        mock_df = _make_mock_df(n=n, start="2018-01-01")
        monkeypatch.setattr(wf_mod, "_fetch", lambda *a, **kw: mock_df)

        def mock_run_backtest(req, **kwargs):
            return _minimal_backtest_result(num_trades=5, sharpe=1.0)

        monkeypatch.setattr(grid_runner_mod, "run_backtest", mock_run_backtest)
        monkeypatch.setattr(wf_mod, "run_backtest", mock_run_backtest)

        # WFA's per-window budget check must not trigger — make wf_mod's clock
        # frozen at 0.
        monkeypatch.setattr(wf_mod, "monotonic", lambda: 0.0)

        # grid_runner's clock: start=0, combo-1 check=0 (runs), combo-2 check=9999 (timeout).
        grid_calls = {"n": 0}

        def mock_grid_monotonic():
            grid_calls["n"] += 1
            return 0.0 if grid_calls["n"] <= 2 else 9999.0

        monkeypatch.setattr(grid_runner_mod, "monotonic", mock_grid_monotonic)

        payload = _wf_payload(is_bars=100, oos_bars=100)
        # Use 3 values so total_combos_per_window=3; timeout after combo 1 → partial → drop
        payload["params"] = [{"path": "stop_loss_pct", "values": [3.0, 5.0, 7.0]}]

        resp = client.post("/api/backtest/walk_forward", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["timed_out"] is True, "Expected timed_out=True with mocked monotonic"
        # Biased partial window (1 of 3 combos) must be dropped
        assert len(data["windows"]) == 0, (
            f"Incomplete window (1 of 3 combos) should be dropped; got {len(data['windows'])} windows"
        )


# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------

class TestPureHelpers:
    """Direct unit tests for _combo_key, _get_neighbor_keys, _deduplicate_by_time."""

    def setup_method(self):
        from routes.walk_forward import _combo_key, _get_neighbor_keys, _deduplicate_by_time
        from routes.walk_forward import WalkForwardParam
        self._combo_key = _combo_key
        self._get_neighbor_keys = _get_neighbor_keys
        self._deduplicate_by_time = _deduplicate_by_time
        self._WalkForwardParam = WalkForwardParam

    def _make_param(self, path, values):
        return self._WalkForwardParam(path=path, values=values)

    def test_combo_key_order_independent(self):
        """Same dict, two key orders → identical frozenset."""
        d1 = {"a": 1.0, "b": 2.0}
        d2 = {"b": 2.0, "a": 1.0}
        assert self._combo_key(d1) == self._combo_key(d2)

    def test_get_neighbor_keys_1d_center(self):
        """Center of 5-value 1D grid → 2 neighbors."""
        params = [self._make_param("p", [1.0, 2.0, 3.0, 4.0, 5.0])]
        best = {"p": 3.0}
        neighbors = self._get_neighbor_keys(best, params)
        assert len(neighbors) == 2
        expected = {
            self._combo_key({"p": 2.0}),
            self._combo_key({"p": 4.0}),
        }
        assert neighbors == expected

    def test_get_neighbor_keys_1d_left_edge(self):
        """Left edge of grid → 1 neighbor."""
        params = [self._make_param("p", [1.0, 2.0, 3.0, 4.0, 5.0])]
        best = {"p": 1.0}
        neighbors = self._get_neighbor_keys(best, params)
        assert len(neighbors) == 1
        assert self._combo_key({"p": 2.0}) in neighbors

    def test_get_neighbor_keys_1d_right_edge(self):
        """Right edge of grid → 1 neighbor."""
        params = [self._make_param("p", [1.0, 2.0, 3.0, 4.0, 5.0])]
        best = {"p": 5.0}
        neighbors = self._get_neighbor_keys(best, params)
        assert len(neighbors) == 1
        assert self._combo_key({"p": 4.0}) in neighbors

    def test_get_neighbor_keys_single_value_grid(self):
        """Single value → no neighbors."""
        params = [self._make_param("p", [5.0])]
        best = {"p": 5.0}
        neighbors = self._get_neighbor_keys(best, params)
        assert len(neighbors) == 0

    def test_get_neighbor_keys_2d_corner(self):
        """2D grid [1,2] × [10,20], best at corner (1,10) → 2 neighbors."""
        params = [
            self._make_param("p1", [1.0, 2.0]),
            self._make_param("p2", [10.0, 20.0]),
        ]
        best = {"p1": 1.0, "p2": 10.0}
        neighbors = self._get_neighbor_keys(best, params)
        # Neighbors that differ in exactly one dimension:
        # (2.0, 10.0) and (1.0, 20.0)
        assert len(neighbors) == 2
        assert self._combo_key({"p1": 2.0, "p2": 10.0}) in neighbors
        assert self._combo_key({"p1": 1.0, "p2": 20.0}) in neighbors

    def test_deduplicate_by_time_no_duplicates(self):
        """No duplicates → preserves order and all entries."""
        points = [
            {"time": "2020-01-01", "value": 100.0},
            {"time": "2020-01-02", "value": 101.0},
            {"time": "2020-01-03", "value": 102.0},
        ]
        result = self._deduplicate_by_time(points)
        assert len(result) == 3
        assert [p["time"] for p in result] == ["2020-01-01", "2020-01-02", "2020-01-03"]

    def test_deduplicate_by_time_last_wins(self):
        """Duplicate timestamp → last occurrence wins."""
        points = [
            {"time": "2020-01-01", "value": 100.0},
            {"time": "2020-01-02", "value": 101.0},
            {"time": "2020-01-02", "value": 999.0},  # duplicate, later value
            {"time": "2020-01-03", "value": 102.0},
        ]
        result = self._deduplicate_by_time(points)
        assert len(result) == 3
        # Find the 2020-01-02 entry — must be 999.0
        mid = next(p for p in result if p["time"] == "2020-01-02")
        assert mid["value"] == 999.0

"""Tests for backend/research/regime_validation.py — Unit 5 (REGIME-TEST).

ALL tests are OFFLINE / synthetic — no network, no regime_states.json read, no
real universe build, and NO outcome artifact (validation_result.json,
null_atlas.json, EDA) is ever touched.  The bars_loader, regime-state map, and
universe pairs are all injected as synthetic fixtures.

Test inventory (plan Unit 5 scenarios + charter §4/§5):

  test_planted_relationship_recovered
      Synthetic data with a planted state→base-rate ordering
      (RISK_ON > NEUTRAL > RISK_OFF >= STRESS) is recovered: H1 contrasts hold
      in the right direction at the correct (separated) effect size.
  test_untestable_under_three_cohorts
      A state with < 3 cohort observations in the window → its contrasts return
      UNTESTABLE (the regime-2020 lesson).
  test_explore_confirm_isolation
      Running the explore window asks the loader ONLY about explore-window
      (2015–2020) as_of dates — never a confirm (2021–2024) date.  Asserted by
      spying every date the loader is asked to resolve.
  test_ledger_exactly_six
      evaluate_ledger emits exactly 6 comparisons, matching the frozen ledger.
  test_reversed_direction
      Planted REVERSED ordering → primary contrast verdict = REVERSED.
  test_charter_sha_in_artifact
      Window artifact records the frozen charter sha + per-state cohort counts +
      CIs + per-cohort direction agreement (via verdicts).
  test_stratified_sample_deterministic
      _stratified_seeded_sample is deterministic and size-bounded.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from os.path import abspath, dirname
from pathlib import Path

import sys

sys.path.insert(0, dirname(dirname(abspath(__file__))))

import pandas as pd
import pytest

import research.regime_validation as rv


# ---------------------------------------------------------------------------
# Synthetic price-frame factory
# ---------------------------------------------------------------------------

def _make_frame(start: date, n_bars: int, daily_step: float) -> pd.DataFrame:
    """A daily-bar frame of n_bars business days starting at `start`, where each
    bar's Close grows by `daily_step` per bar (so the bar-counted forward return
    over h bars is deterministic and monotone in daily_step)."""
    idx = pd.bdate_range(start=start, periods=n_bars)
    closes = [100.0 + daily_step * i for i in range(n_bars)]
    return pd.DataFrame({"Close": closes}, index=idx)


# ---------------------------------------------------------------------------
# Universe + regime-state fixtures
# ---------------------------------------------------------------------------

def _universe(n: int) -> list[tuple[str, str]]:
    """n synthetic (ticker, cik) pairs spread across the alphabet."""
    import string
    pairs = []
    for i in range(n):
        letter = string.ascii_uppercase[i % 26]
        pairs.append((f"{letter}{i:04d}", f"{i:010d}"))
    return sorted(pairs)


def _regime_map(assignments: dict[date, str]) -> dict:
    """Build a regime_states-shaped dict: each as_of's most-recent trading bar
    (the prior business day) carries the assigned state."""
    states = {}
    for as_of, state in assignments.items():
        key = (as_of - timedelta(days=2)).isoformat()  # a recent date <= as_of
        states[key] = {"state": state}
    return {"states": states}


# ---------------------------------------------------------------------------
# Test: planted relationship recovered
# ---------------------------------------------------------------------------

def test_planted_relationship_recovered(monkeypatch, tmp_path):
    """A planted ordering RISK_ON > NEUTRAL > RISK_OFF >= STRESS is recovered as
    CONFIRMED/WEAKENED (direction held) for the H1 contrasts, never REVERSED."""
    ordering = {"RISK_ON": 0.80, "NEUTRAL": 0.60, "RISK_OFF": 0.40, "STRESS": 0.20}

    start_year, end_year = rv.WINDOWS["explore"]
    as_of_dates = rv._quarterly_as_of_dates(start_year, end_year)
    states_cycle = list(ordering.keys())
    assignments = {ao: states_cycle[i % len(states_cycle)]
                   for i, ao in enumerate(as_of_dates)}
    regime_states = _regime_map(assignments)
    pairs = _universe(40)

    # Plant base rates: monkeypatch _forward_return so that, for a cohort in state
    # s, the eval tickers beat the null median with frequency ordering[s].
    # We index tickers by their position to make a deterministic hit pattern.
    ticker_index = {t: i for i, (t, _) in enumerate(pairs)}

    def fake_forward_return(frame, as_of, horizon, *, _idx=ticker_index,
                            _assign=assignments, _ord=ordering):
        # frame carries its ticker via a stashed attribute (set in loader).
        ticker = getattr(frame, "_ticker", None)
        if ticker is None:
            return None
        state = _assign.get(as_of)
        frac = _ord.get(state, 0.5)
        i = _idx[ticker]
        # Null tickers (odd index) all return 0.0 → null median 0.0.
        # Eval tickers (even index) return +1.0 for a `frac` portion, else -1.0.
        if i % 2 == 1:
            return 0.0
        # Deterministic hit pattern: first `frac` of eval tickers hit.
        eval_rank = (i // 2) % 10
        return 1.0 if eval_rank < round(frac * 10) else -1.0

    monkeypatch.setattr(rv, "_forward_return", fake_forward_return)

    def loader(ticker: str):
        loader.requested.append(ticker)
        f = _make_frame(date(2010, 1, 4), 50, 0.1)
        f._ticker = ticker  # stash for fake_forward_return
        return f
    loader.requested = []

    result = rv.run_window(
        "explore",
        regime_states=regime_states,
        pairs=pairs,
        bars_loader=loader,
        out_dir=tmp_path,
        sample_per_side=20,
    )

    aggs = result["state_aggregates"]
    ro = rv._state_mean(aggs, "RISK_ON", 63)
    nu = rv._state_mean(aggs, "NEUTRAL", 63)
    rf = rv._state_mean(aggs, "RISK_OFF", 63)
    st = rv._state_mean(aggs, "STRESS", 63)
    assert ro is not None and nu is not None and rf is not None and st is not None
    # Recovered ordering (point estimate) matches the plant.
    assert ro > nu > rf >= st, (ro, nu, rf, st)

    verdicts = {v["id"]: v for v in result["ledger_verdicts"]}
    # H1 pair contrasts: direction held → CONFIRMED or WEAKENED, never REVERSED.
    for cid in (1, 2, 3):
        assert verdicts[cid]["verdict"] in ("CONFIRMED", "WEAKENED"), verdicts[cid]
    # H2: STRESS lowest → not REVERSED.
    assert verdicts[4]["verdict"] in ("CONFIRMED", "WEAKENED"), verdicts[4]
    # Artifact exists.
    assert (tmp_path / "explore-result.json").exists()


# ---------------------------------------------------------------------------
# Test: < 3 cohort observations → UNTESTABLE
# ---------------------------------------------------------------------------

def test_untestable_under_three_cohorts(monkeypatch, tmp_path):
    """A state appearing in only 2 cohorts → its contrast returns UNTESTABLE."""
    # Make STRESS appear only twice; the rest plentiful.
    start_year, end_year = rv.WINDOWS["explore"]
    as_of_dates = rv._quarterly_as_of_dates(start_year, end_year)
    assignments = {}
    stress_count = 0
    for i, ao in enumerate(as_of_dates):
        if i < 2:
            assignments[ao] = "STRESS"
            stress_count += 1
        elif i % 3 == 0:
            assignments[ao] = "RISK_ON"
        elif i % 3 == 1:
            assignments[ao] = "NEUTRAL"
        else:
            assignments[ao] = "RISK_OFF"
    assert stress_count == 2
    regime_states = _regime_map(assignments)
    pairs = _universe(20)

    def fake_fr(frame, as_of, horizon):
        return 0.5  # uniform → base rate well-defined, irrelevant here
    monkeypatch.setattr(rv, "_forward_return", fake_fr)

    def loader(ticker: str):
        return _make_frame(date(2010, 1, 4), 50, 0.1)

    result = rv.run_window(
        "explore", regime_states=regime_states, pairs=pairs,
        bars_loader=loader, out_dir=tmp_path, sample_per_side=10,
    )
    assert result["per_state_cohort_counts"]["STRESS"] == 2
    verdicts = {v["id"]: v for v in result["ledger_verdicts"]}
    # Contrast #3 (RISK_OFF >= STRESS) requires STRESS → UNTESTABLE.
    assert verdicts[3]["verdict"] == "UNTESTABLE", verdicts[3]
    # H2 (four-way lowest) requires all four → UNTESTABLE.
    assert verdicts[4]["verdict"] == "UNTESTABLE", verdicts[4]


# ---------------------------------------------------------------------------
# Test: explore / confirm isolation — confirm data physically not computed
# ---------------------------------------------------------------------------

def test_explore_confirm_isolation(monkeypatch, tmp_path):
    """An explore invocation asks the loader ONLY about cohorts in 2015–2020 and
    NEVER computes/loads any 2021–2024 (confirm) cohort.

    We spy every date the loader's frames are scored at by recording the as_of
    each _forward_return call sees — and assert none falls in the confirm range.
    """
    seen_as_of: list[date] = []

    real_forward = rv._forward_return

    def spying_forward_return(frame, as_of, horizon):
        seen_as_of.append(as_of)
        return 0.5
    monkeypatch.setattr(rv, "_forward_return", spying_forward_return)

    # Also spy the loader's requested tickers (must be non-empty; isolation is
    # about as_of dates, not ticker identity).
    def loader(ticker: str):
        loader.requested.append(ticker)
        return _make_frame(date(2010, 1, 4), 50, 0.1)
    loader.requested = []

    start_year, end_year = rv.WINDOWS["explore"]
    as_of_dates = rv._quarterly_as_of_dates(start_year, end_year)
    assignments = {ao: ["RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"][i % 4]
                   for i, ao in enumerate(as_of_dates)}
    regime_states = _regime_map(assignments)
    pairs = _universe(20)

    rv.run_window(
        "explore", regime_states=regime_states, pairs=pairs,
        bars_loader=loader, out_dir=tmp_path, sample_per_side=10,
    )

    assert seen_as_of, "loader was never scored"
    confirm_start = date(2021, 1, 1)
    confirm_dates = [d for d in seen_as_of if d >= confirm_start]
    assert not confirm_dates, f"confirm cohorts leaked into explore: {confirm_dates}"
    # And every scored date is inside the explore window.
    for d in seen_as_of:
        assert date(2015, 1, 1) <= d <= date(2020, 12, 31), d
    # Confirm artifact must NOT have been written by the explore run.
    assert not (tmp_path / "confirm-result.json").exists()


# ---------------------------------------------------------------------------
# Test: ledger enforcement — exactly 6 comparisons
# ---------------------------------------------------------------------------

def test_ledger_exactly_six():
    assert len(rv.LEDGER) == 6
    # Verdict evaluation emits exactly 6, matching ledger ids 1..6.
    dummy = {
        "window": "explore",
        "per_state_cohort_counts": {s: 0 for s in rv._REGIME_STATES},
        "state_aggregates": {
            s: {str(h): {"n_cohorts": 0, "mean": None, "ci_low": None,
                         "ci_high": None}
                for h in rv.V2_HORIZONS_TRADING_DAYS}
            for s in rv._REGIME_STATES
        },
        "cohorts": [],
    }
    verdicts = rv.evaluate_ledger(dummy)
    assert len(verdicts) == 6
    assert [v["id"] for v in verdicts] == [1, 2, 3, 4, 5, 6]
    # All UNTESTABLE here (zero cohorts everywhere).
    assert all(v["verdict"] == "UNTESTABLE" for v in verdicts)


# ---------------------------------------------------------------------------
# Test: reversed direction → REVERSED
# ---------------------------------------------------------------------------

def test_reversed_direction(monkeypatch, tmp_path):
    """A planted REVERSED ordering (STRESS highest, RISK_ON lowest) → the H1
    primary contrast verdicts come back REVERSED."""
    ordering = {"RISK_ON": 0.20, "NEUTRAL": 0.40, "RISK_OFF": 0.60, "STRESS": 0.80}
    start_year, end_year = rv.WINDOWS["explore"]
    as_of_dates = rv._quarterly_as_of_dates(start_year, end_year)
    states_cycle = list(ordering.keys())
    assignments = {ao: states_cycle[i % len(states_cycle)]
                   for i, ao in enumerate(as_of_dates)}
    regime_states = _regime_map(assignments)
    pairs = _universe(40)
    ticker_index = {t: i for i, (t, _) in enumerate(pairs)}

    def fake_fr(frame, as_of, horizon, *, _idx=ticker_index,
                _assign=assignments, _ord=ordering):
        ticker = getattr(frame, "_ticker", None)
        if ticker is None:
            return None
        frac = _ord.get(_assign.get(as_of), 0.5)
        i = _idx[ticker]
        if i % 2 == 1:
            return 0.0
        return 1.0 if (i // 2) % 10 < round(frac * 10) else -1.0
    monkeypatch.setattr(rv, "_forward_return", fake_fr)

    def loader(ticker: str):
        f = _make_frame(date(2010, 1, 4), 50, 0.1)
        f._ticker = ticker
        return f

    result = rv.run_window(
        "explore", regime_states=regime_states, pairs=pairs,
        bars_loader=loader, out_dir=tmp_path, sample_per_side=20,
    )
    verdicts = {v["id"]: v for v in result["ledger_verdicts"]}
    # Contrast #1 RISK_ON > NEUTRAL is reversed (RISK_ON planted lowest of the two).
    assert verdicts[1]["verdict"] == "REVERSED", verdicts[1]


# ---------------------------------------------------------------------------
# Test: charter sha + counts + CIs in artifact
# ---------------------------------------------------------------------------

def test_charter_sha_in_artifact(monkeypatch, tmp_path):
    assignments = {ao: ["RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"][i % 4]
                   for i, ao in enumerate(
                       rv._quarterly_as_of_dates(*rv.WINDOWS["explore"]))}
    regime_states = _regime_map(assignments)
    pairs = _universe(20)

    def fake_fr(frame, as_of, horizon):
        return 0.7
    monkeypatch.setattr(rv, "_forward_return", fake_fr)

    def loader(ticker: str):
        return _make_frame(date(2010, 1, 4), 50, 0.1)

    result = rv.run_window(
        "explore", regime_states=regime_states, pairs=pairs,
        bars_loader=loader, out_dir=tmp_path, sample_per_side=10,
    )
    on_disk = json.loads((tmp_path / "explore-result.json").read_text())
    assert on_disk["charter_sha256"] == rv._CHARTER_SHA256
    # per-state cohort counts present
    assert set(on_disk["per_state_cohort_counts"]) >= set(rv._REGIME_STATES)
    # CIs present in aggregates
    cell = on_disk["state_aggregates"]["RISK_ON"]["63"]
    assert "ci_low" in cell and "ci_high" in cell
    # human-readable section present for explore.
    assert "human_readable" in on_disk
    assert "REGIME-TEST EXPLORE" in on_disk["human_readable"]


# ---------------------------------------------------------------------------
# Test: stratified sample determinism + size bound
# ---------------------------------------------------------------------------

def test_stratified_sample_deterministic():
    pairs = _universe(500)
    s1 = rv._stratified_seeded_sample(pairs, 50, seed=123)
    s2 = rv._stratified_seeded_sample(pairs, 50, seed=123)
    assert s1 == s2  # deterministic
    assert len(s1) <= 50
    # Different seed → (very likely) different sample.
    s3 = rv._stratified_seeded_sample(pairs, 50, seed=999)
    assert s3 != s1
    # n >= universe → full sorted universe.
    full = rv._stratified_seeded_sample(pairs, 10_000, seed=1)
    assert full == sorted(pairs)
    # Spread across alphabet (not all 'A').
    first_letters = {t[0] for t, _ in s1}
    assert len(first_letters) > 1

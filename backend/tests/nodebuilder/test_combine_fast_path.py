"""The one-group fast paths of the combined result give the same result.

nodes_groups.combine skips the per-bar union, rounding and dict building
for one group, and run._leg_track reads the simulator's equity curve
straight through when it has one point per bar.  Both exist only for speed
(test_backtest_timing).  These tests pin them to the general path they
replace: the code below is that path, copied from before the change, and
every case must give equal curves (value for value, as JSON too) and an
equal summary.
"""
from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest

from nodebuilder import run as run_mod
from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.trading import nodes_groups as ng
from tests.nodebuilder.test_inspect import WINDOW, _daily, _graph


# ---------------------------------------------------------------------------
# The general path, as it was
# ---------------------------------------------------------------------------

def _general_curves(legs):
    key_of = {}
    for leg in legs:
        for t, k in zip(leg.times, leg.keys):
            key_of.setdefault(t, k)
    union = pd.DatetimeIndex(sorted(key_of))
    keys = [key_of[t] for t in union]
    if not all(isinstance(k, str) for k in keys):
        keys = [ng._key_to_seconds(k) for k in keys]

    def on_union(values, leg, before):
        s = pd.Series(values, index=leg.times)
        s = s[~s.index.duplicated(keep="last")]
        return s.reindex(union).ffill().fillna(before).to_numpy(dtype=float)

    equity = np.zeros(len(union))
    baseline = np.zeros(len(union))
    for leg in legs:
        equity += on_union(leg.equity, leg, leg.capital)
        baseline += on_union(leg.baseline, leg, leg.capital)
    return (
        [{"time": k, "value": round(float(v), 2)} for k, v in zip(keys, equity)],
        [{"time": k, "value": round(float(v), 2)} for k, v in zip(keys, baseline)],
    )


def _general_leg_arrays(leg):
    """run._leg_track's equity and baseline, read through the key lookup."""
    keys = list(leg.date_strs)
    by_key = {p["time"]: p["value"] for p in leg.sim["equity_curve"]}
    equity = np.array([by_key.get(k, np.nan) for k in keys], dtype=float)
    equity = pd.Series(equity).ffill().fillna(leg.capital).to_numpy()
    baseline = np.array([p["value"] for p in leg.baseline], dtype=float)
    return equity, baseline


def _same(a, b):
    assert a == b
    assert json.dumps(a) == json.dumps(b)


def _assert_matches_general(legs):
    out = ng.combine(legs, float(sum(leg.capital for leg in legs)))
    eq, bl = _general_curves(legs)
    _same(out["equity_curve"], eq)
    _same(out["baseline_curve"], bl)
    return out


# ---------------------------------------------------------------------------
# Synthetic one-leg cases
# ---------------------------------------------------------------------------

D = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]


def _leg(times, keys, equity, baseline, capital=1000.0, own=True):
    equity = np.asarray(equity, dtype=float)
    baseline = np.asarray(baseline, dtype=float)
    n = len(keys)
    return ng.LegTrack(
        times=times, keys=list(keys), equity=equity, baseline=baseline,
        in_pos=np.zeros(n, dtype=bool), notional=np.zeros(n), capital=capital,
        final_value=float(equity[-1]) if n else capital, num_trades=0,
        equity_curve=[{"time": k, "value": v} for k, v in zip(keys, equity.tolist())] if own else None,
        baseline_curve=[{"time": k, "value": v} for k, v in zip(keys, baseline.tolist())] if own else None,
    )


def _days(names):
    return pd.DatetimeIndex([pd.Timestamp(d) for d in names])


CASES = {
    "cents": lambda: _leg(_days(D), D, [1000.0, 1010.25, 990.1, 1005.07], [1000.0, 1001.5, 999.99, 1003.0]),
    "cents_no_own_curves": lambda: _leg(_days(D), D, [1000.0, 1010.25, 990.1, 1005.07],
                                        [1000.0, 1001.5, 999.99, 1003.0], own=False),
    "not_cents": lambda: _leg(_days(D), D, [1000.0, 1010.256, 990.1049, 1005.0751],
                              [1000 / 3, 1001.505, 999.995, 1003.0]),
    "nan_gap_filled_with_odd_capital": lambda: _leg(
        _days(D), D, [np.nan, 1010.25, np.nan, 1005.07], [np.nan, 1001.5, 999.99, np.nan],
        capital=1000 / 3),
    "unsorted_times_constant_values": lambda: _leg(
        _days([D[2], D[0], D[3], D[1]]), [D[2], D[0], D[3], D[1]], [1000.0] * 4, [1000.0] * 4),
    "duplicate_times": lambda: _leg(_days([D[0], D[1], D[1], D[2]]), [D[0], D[1], D[1], D[2]],
                                    [1000.0, 1001.0, 1002.0, 1003.0], [1000.0] * 4),
    "intraday_int_keys": lambda: _leg(
        pd.DatetimeIndex(["2024-01-02 14:30", "2024-01-02 14:35", "2024-01-02 14:40"]),
        [1704205800, 1704206100, 1704206400], [100.0, 101.5, 99.25], [100.0, 100.0, 100.01],
        capital=100.0),
    "intraday_numpy_int_keys": lambda: _leg(
        pd.DatetimeIndex(["2024-01-02 14:30", "2024-01-02 14:35"]),
        list(np.array([1704205800, 1704206100], dtype=np.int64)), [100.0, 101.5], [100.0, 99.0],
        capital=100.0),
    "huge_values": lambda: _leg(_days(D[:2]), D[:2], [2e12, 2e12 + 0.25], [2e12, 2e12]),
    "negative_and_zero": lambda: _leg(_days(D[:3]), D[:3], [0.0, -5.5, -0.0], [0.0, 1.0, 2.0]),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_one_leg_combine_matches_the_general_path(name):
    _assert_matches_general([CASES[name]()])


def test_leg_track_keys_a_curve_only_by_the_keys_themselves():
    """The LegTrack contract: an own curve is passed on only when its times
    are the keys (the same objects), so a curve with equal times of another
    type (numpy ints) or a missing point is never reused."""
    keys = [1704205800, 1704206100]
    same = [{"time": k, "value": 1.0} for k in keys]
    assert run_mod._keyed_by(same, keys)
    assert not run_mod._keyed_by([{"time": np.int64(k), "value": 1.0} for k in keys], keys)
    assert not run_mod._keyed_by(same[:1], keys)
    assert not run_mod._keyed_by(list(reversed(same)), keys)


def test_two_legs_keep_the_general_path():
    a = CASES["cents"]()
    b = _leg(_days(D[1:]), D[1:], [500.0, 505.5, 510.0], [500.0, 501.0, 502.0], capital=500.0)
    _assert_matches_general([a, b])


# ---------------------------------------------------------------------------
# A real one-group backtest
# ---------------------------------------------------------------------------

def _capture_leg(monkeypatch):
    seen = []
    real = run_mod._leg_track

    def spy(leg):
        seen.append(leg)
        return real(leg)

    monkeypatch.setattr(run_mod, "_leg_track", spy)
    return seen


@pytest.mark.parametrize("drop_point", [False, True])
def test_one_group_backtest_combined_matches_the_general_path(monkeypatch, drop_point):
    seen = _capture_leg(monkeypatch)
    req = GraphBacktestRequest.model_validate({"graph": _graph(), **WINDOW})
    response, _cook = run_mod.run_graph_backtest_cooked(req, _daily())
    [leg] = seen
    assert len(leg.sim["equity_curve"]) == len(leg.date_strs)

    if drop_point:   # a curve with a gap takes the key lookup and fills it
        leg = copy.copy(leg)
        leg.sim = dict(leg.sim, equity_curve=leg.sim["equity_curve"][:40] + leg.sim["equity_curve"][41:])
    track = run_mod._leg_track(leg)
    equity, baseline = _general_leg_arrays(leg)
    assert np.array_equal(track.equity, equity) and np.array_equal(track.baseline, baseline)
    assert (track.equity_curve is None) == drop_point

    out = _assert_matches_general([track])
    general = ng.combine([ng.LegTrack(**{**track.__dict__, "equity_curve": None,
                                         "baseline_curve": None})], float(req.initial_capital))
    assert out["summary"] == general["summary"]
    if not drop_point:
        # One group: the combined curves are the group's own curves.
        _same(response.combined.equity_curve, response.groups[0].equity_curve)
        _same(response.combined.baseline_curve, response.groups[0].baseline_curve)

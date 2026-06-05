"""Unit 2 (D14): tests for build_null_atlas.py schema_version branching.

Covers:
- v2 cohort cells: per-horizon (21/63/126) median forward return, median
  cohort-relative excess, hit_v2_rate, and per-horizon insufficiency flagging.
- incomplete-horizon exclusion: None-valued horizon cells drop out of n_complete.
- n<30 cohort → insufficient flag (atlas convention preserved from v1).
- v1 path still works (regression: the legacy touch-metric cells unchanged).
- build_atlas() picks the v2 cell builder when events are schema_version=2.

These tests construct event lists directly (no network, no validation run) — the
atlas builder is a pure transform over an events table.
"""
from __future__ import annotations

import sys
from os.path import abspath, dirname, join

# Make backend/research importable.
_BACKEND = dirname(dirname(abspath(__file__)))
sys.path.insert(0, join(_BACKEND, "research"))

import build_null_atlas as bna  # noqa: E402


def _v2_null_event(as_of, i, entry_price=12.0, complete_126=True):
    return {
        "ticker": f"NUL{i}",
        "as_of": as_of,
        "is_null": True,
        "entry_price": entry_price,
        "fwd_return_21d": 1.0 + i * 0.1,
        "fwd_return_63d": 2.0,
        "fwd_return_126d": 3.0 if complete_126 else None,
        "excess_21d": i * 0.1 - 1.5,
        "excess_63d": 0.5,
        "excess_126d": 1.0 if complete_126 else None,
        "hit_v2_21d": (i % 2 == 0),
        "hit_v2_63d": True,
        "hit_v2_126d": (i % 3 == 0) if complete_126 else None,
    }


def test_v2_cohort_stats_horizon_cells_present():
    events = [_v2_null_event("2018-02-15", i) for i in range(35)]
    cell = bna._cohort_stats_v2(events)
    assert cell["n"] == 35
    assert cell.get("insufficient") is None  # n>=30 → not flagged
    for h in ("21d", "63d", "126d"):
        assert h in cell["horizons"]
        hz = cell["horizons"][h]
        assert "median_fwd_return_pct" in hz
        assert "median_excess_pct" in hz
        assert "hit_v2_rate" in hz


def test_v2_incomplete_horizon_excluded_from_n_complete():
    """5 of 35 events have an incomplete 126d horizon (None) → n_complete=30."""
    events = [
        _v2_null_event("2018-02-15", i, complete_126=(i >= 5))
        for i in range(35)
    ]
    cell = bna._cohort_stats_v2(events)
    assert cell["horizons"]["126d"]["n_complete"] == 30
    assert cell["horizons"]["21d"]["n_complete"] == 35  # 21d all complete


def test_v2_small_cohort_flagged_insufficient():
    """n<30 cohort → insufficient flag (same convention as v1)."""
    events = [_v2_null_event("2019-05-15", i) for i in range(10)]
    cell = bna._cohort_stats_v2(events)
    assert cell["n"] == 10
    assert cell.get("insufficient") is True


def test_v2_per_horizon_insufficient_when_too_few_complete():
    """A horizon with <30 completed events is flagged insufficient at the cell level,
    even if the cohort overall has n>=30."""
    # 35 events, but only 10 have a complete 126d horizon.
    events = [
        _v2_null_event("2018-02-15", i, complete_126=(i < 10))
        for i in range(35)
    ]
    cell = bna._cohort_stats_v2(events)
    assert cell["horizons"]["126d"]["n_complete"] == 10
    assert cell["horizons"]["126d"]["insufficient"] is True
    assert cell["horizons"]["21d"]["insufficient"] is False  # 35 >= 30


def test_v2_hit_rate_matches_flags():
    """hit_v2_rate equals the fraction of non-None hit_v2 flags that are True."""
    events = [_v2_null_event("2018-02-15", i) for i in range(30)]
    cell = bna._cohort_stats_v2(events)
    # 63d flag is True for all → rate 1.0
    assert cell["horizons"]["63d"]["hit_v2_rate"] == 1.0
    # 21d flag is (i%2==0) → 15/30 True
    assert abs(cell["horizons"]["21d"]["hit_v2_rate"] - 0.5) < 1e-9


def test_v1_cohort_stats_unchanged_regression():
    """v1 path: the legacy touch-metric cell still produces hit_rate / round_trip_rate.
    Regression guard — v2 work must not break the existing artifact."""
    v1_events = [
        {
            "ticker": f"V{i}", "as_of": "2018-02-15", "is_null": True,
            "entry_price": 8.0, "net_return_pct": 10.0,
            "horizon_end_return_pct": 5.0, "hit": (i % 2 == 0),
        }
        for i in range(30)
    ]
    cell = bna._cohort_stats(v1_events)
    assert cell["n"] == 30
    assert "hit_rate" in cell
    assert "round_trip_rate" in cell
    assert "median_net_return_pct" in cell
    # No v2 horizon block on the v1 cell.
    assert "horizons" not in cell


def test_build_atlas_branches_on_schema_version(tmp_path, monkeypatch):
    """build_atlas() selects the v2 cell builder when raw events are schema_version=2,
    and stamps atlas schema_version=2."""
    import json

    v2_events = [_v2_null_event("2018-02-15", i) for i in range(35)]
    v2_events += [
        {**_v2_null_event("2018-02-15", i), "ticker": f"SIG{i}", "is_null": False}
        for i in range(3)
    ]
    raw = {"schema_version": 2, "events": v2_events}

    val_path = tmp_path / "validation_result.json"
    val_path.write_text(json.dumps(raw))

    # Point the builder at our fixture; force the sector gate off (no universe.json).
    monkeypatch.setattr(bna, "_VALIDATION_PATH", val_path)
    monkeypatch.setattr(bna, "_UNIVERSE_PATH", tmp_path / "missing_universe.json")

    atlas = bna.build_atlas()

    assert atlas["schema_version"] == 2
    assert atlas["meta"]["events_schema_version"] == 2
    cell = atlas["per_cohort"]["2018-02-15"]
    assert "horizons" in cell  # v2 cell shape
    assert "21d" in cell["horizons"]
    # v2 sanity check runs clean
    sanity = bna._sanity_check_v2(atlas)
    assert all(s.startswith("[PASS]") for s in sanity), sanity


def test_build_atlas_v1_still_default(tmp_path, monkeypatch):
    """A schema_version=1 (or missing) events file produces a v1 atlas (touch cells)."""
    import json

    v1_events = [
        {
            "ticker": f"V{i}", "as_of": "2018-02-15", "is_null": True,
            "entry_price": 8.0, "net_return_pct": 10.0,
            "horizon_end_return_pct": 5.0, "hit": (i % 2 == 0),
        }
        for i in range(30)
    ]
    raw = {"events": v1_events}  # no schema_version → defaults to 1

    val_path = tmp_path / "validation_result.json"
    val_path.write_text(json.dumps(raw))
    monkeypatch.setattr(bna, "_VALIDATION_PATH", val_path)
    monkeypatch.setattr(bna, "_UNIVERSE_PATH", tmp_path / "missing_universe.json")

    atlas = bna.build_atlas()
    assert atlas["schema_version"] == 1
    cell = atlas["per_cohort"]["2018-02-15"]
    assert "hit_rate" in cell
    assert "horizons" not in cell

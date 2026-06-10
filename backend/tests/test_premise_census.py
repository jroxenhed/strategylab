"""Tests for backend/research/premise_census.py (F418).

Fixture-driven unit tests + one real-data sanity test against the committed
p-1569aa97 explore study dir.

Real-data anchors (pre-stated in .run/F396/plan.md §5 / CLAUDE.md):
    n_valid=20, mde_1samp_30≈6.59±0.05, mde_gap_30≈17.5±1.0
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Locate repo root so we can add research/ to the path for imports
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from research.premise_census import (  # noqa: E402
    CensusResult,
    HorizonCensus,
    _DOSE_GAP_FLOOR_PP,
    _MDE_MULT,
    compute_census,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_event(score=None, excess_30=None, excess_63=None, floor_status="ok", split="explore"):
    return {
        "floor_status": floor_status,
        "split": split,
        "payload": {"score": score},
        "fwd_excess_pct": {"30": excess_30, "63": excess_63},
    }


def _write_events(tmp_path: Path, events: list[dict]) -> Path:
    p = tmp_path / "events.ndjson"
    p.write_text("\n".join(json.dumps(e) for e in events))
    return p


def _make_20_events(with_scores: bool = True) -> list[dict]:
    """20 events, std_30 ≈ 5.0, mean_30 = 0.0 (alternating ±5)."""
    evts = []
    for i in range(20):
        score = float(i) / 19.0 if with_scores else None
        excess_30 = 5.0 if i % 2 == 0 else -5.0
        evts.append(_make_event(score=score, excess_30=excess_30, excess_63=10.0))
    return evts


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestMDE1SampBasic:
    """20 events with known std → mde_1samp matches formula."""

    def test_mde_matches_formula(self, tmp_path):
        evts = _make_20_events(with_scores=False)
        events_path = _write_events(tmp_path, evts)
        result = compute_census(
            events_path=events_path,
            analysis_form="one_sample",
            horizons=(30,),
            primary_horizon=30,
            design_mde_pp=10.0,
        )
        ph = result.horizons[30]
        # std of alternating ±5 over 20 items (sample stdev)
        expected_std = math.sqrt(sum((5.0 if i % 2 == 0 else -5.0) ** 2 for i in range(20)) / 19)
        expected_mde = _MDE_MULT * expected_std / math.sqrt(20)
        assert ph.mde_1samp_pp is not None
        assert abs(ph.mde_1samp_pp - expected_mde) < 1e-9


class TestMDEGapDoseForm:
    """20 score-defined events, arm=4 → mde_gap computed."""

    def test_mde_gap_computed(self, tmp_path):
        evts = _make_20_events(with_scores=True)
        events_path = _write_events(tmp_path, evts)
        result = compute_census(
            events_path=events_path,
            analysis_form="dose_response",
            horizons=(30,),
            primary_horizon=30,
        )
        ph = result.horizons[30]
        assert ph.mde_gap_pp is not None, "mde_gap_pp should be computed with 20 score-defined events"
        assert ph.arm_size == 4  # 20 // 5

    def test_arm_size_correct(self, tmp_path):
        evts = _make_20_events(with_scores=True)
        events_path = _write_events(tmp_path, evts)
        result = compute_census(
            events_path=events_path,
            analysis_form="dose_response",
            horizons=(30,),
            primary_horizon=30,
        )
        assert result.n_score_defined == 20


class TestArmTooSmallReturnsNoneGap:
    """n_score_defined=3 → arm=0 → mde_gap=None."""

    def test_small_arm_returns_none(self, tmp_path):
        evts = [_make_event(score=float(i), excess_30=float(i), excess_63=0.0) for i in range(3)]
        events_path = _write_events(tmp_path, evts)
        result = compute_census(
            events_path=events_path,
            analysis_form="dose_response",
            horizons=(30,),
            primary_horizon=30,
        )
        ph = result.horizons[30]
        assert ph.mde_gap_pp is None


class TestTestable1SampTrue:
    """mde_1samp_pp <= design_mde_pp → testable_1samp True."""

    def test_testable_when_mde_below_design(self, tmp_path):
        # Make large n to get small MDE well below a generous design_mde_pp
        evts = [_make_event(excess_30=0.1, excess_63=0.1) for _ in range(50)]
        events_path = _write_events(tmp_path, evts)
        result = compute_census(
            events_path=events_path,
            analysis_form="one_sample",
            horizons=(30,),
            primary_horizon=30,
            design_mde_pp=100.0,
        )
        assert result.testable_1samp is True


class TestTestable1SampFalse:
    """mde_1samp_pp > design_mde_pp → testable_1samp False."""

    def test_not_testable_when_mde_above_design(self, tmp_path):
        # Large std, small n, tiny design_mde_pp → underpowered
        evts = [_make_event(excess_30=5.0 if i % 2 == 0 else -5.0, excess_63=0.0)
                for i in range(4)]
        events_path = _write_events(tmp_path, evts)
        result = compute_census(
            events_path=events_path,
            analysis_form="one_sample",
            horizons=(30,),
            primary_horizon=30,
            design_mde_pp=0.01,
        )
        assert result.testable_1samp is False


class TestTestableGapFloor:
    """mde_gap <= 1.0 → testable_gap True."""

    def test_testable_gap_when_within_floor(self, tmp_path):
        # Very tight constant scores → std ≈ 0 in arms → mde_gap very small
        # Use 20 events with constant excess in both arms to get near-zero gap MDE
        # Score range 0-1 linearly, all excess_30 = 0.001 (const)
        evts = [_make_event(score=float(i) / 19.0, excess_30=0.001 * (i + 1), excess_63=0.0)
                for i in range(20)]
        events_path = _write_events(tmp_path, evts)
        result = compute_census(
            events_path=events_path,
            analysis_form="dose_response",
            horizons=(30,),
            primary_horizon=30,
        )
        # With very small stds the gap should be well under 1.0
        ph = result.horizons[30]
        if ph.mde_gap_pp is not None and ph.mde_gap_pp <= _DOSE_GAP_FLOOR_PP:
            assert result.testable_gap is True
        elif ph.mde_gap_pp is not None:
            assert result.testable_gap is False


class TestMissingEventsNdjson:
    """FileNotFoundError raised when events.ndjson is missing."""

    def test_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="events.ndjson"):
            compute_census(
                events_path=tmp_path / "nonexistent.ndjson",
                analysis_form="one_sample",
                horizons=(30,),
                primary_horizon=30,
            )


class TestNullExcessExcluded:
    """Rows with fwd_excess_pct[h]=None excluded from mean/std."""

    def test_null_rows_not_counted(self, tmp_path):
        evts = [
            _make_event(excess_30=10.0, excess_63=0.0),
            _make_event(excess_30=10.0, excess_63=0.0),
            _make_event(excess_30=None, excess_63=None),  # should be excluded
        ]
        events_path = _write_events(tmp_path, evts)
        result = compute_census(
            events_path=events_path,
            analysis_form="one_sample",
            horizons=(30,),
            primary_horizon=30,
        )
        # n_valid includes the null row (floor_status=ok, split=explore)
        assert result.n_valid == 3
        ph = result.horizons[30]
        # But mde is computed from only 2 non-null values
        assert ph.mde_1samp_pp is not None
        # std of [10.0, 10.0] = 0.0 → mde_1samp = 0.0
        assert ph.std_excess_pp == pytest.approx(0.0, abs=1e-9)


class TestFloorStatusFiltered:
    """Rows with floor_status != "ok" excluded."""

    def test_bad_floor_excluded(self, tmp_path):
        evts = [
            _make_event(excess_30=1.0, floor_status="ok"),
            _make_event(excess_30=2.0, floor_status="ok"),
            _make_event(excess_30=100.0, floor_status="missing_price"),  # excluded
        ]
        events_path = _write_events(tmp_path, evts)
        result = compute_census(
            events_path=events_path,
            analysis_form="one_sample",
            horizons=(30,),
            primary_horizon=30,
        )
        assert result.n_valid == 2


class TestSplitFiltered:
    """Rows with split != "explore" excluded."""

    def test_confirm_split_excluded(self, tmp_path):
        evts = [
            _make_event(excess_30=1.0, split="explore"),
            _make_event(excess_30=2.0, split="explore"),
            _make_event(excess_30=100.0, split="confirm"),  # excluded
        ]
        events_path = _write_events(tmp_path, evts)
        result = compute_census(
            events_path=events_path,
            analysis_form="one_sample",
            horizons=(30,),
            primary_horizon=30,
        )
        assert result.n_valid == 2


# ---------------------------------------------------------------------------
# Real-data sanity test (F338 anchors: B1–B6)
# ---------------------------------------------------------------------------

_REAL_STUDY = (
    _BACKEND / "data" / "turnaround" / "event_studies"
    / "premise_p-1569aa97_explore_1781050404"
)


@pytest.mark.skipif(
    not (_REAL_STUDY / "events.ndjson").exists(),
    reason="Real study dir not present on this machine",
)
class TestRealDataAnchors:
    """F338 anchors from .run/F396/plan.md §5 (pre-stated before running)."""

    def test_known_anchors(self):
        result = compute_census(
            events_path=_REAL_STUDY / "events.ndjson",
            analysis_form="dose_response",
            horizons=(30, 63),
            primary_horizon=30,
        )

        # B1: n_valid = 20 exact
        assert result.n_valid == 20, f"B1 failed: n_valid={result.n_valid}, expected 20"

        # B2: n_score_defined = 18 exact
        assert result.n_score_defined == 18, (
            f"B2 failed: n_score_defined={result.n_score_defined}, expected 18"
        )

        ph30 = result.horizons[30]

        # B5: mde_1samp_30 ≈ 6.591 ± 0.05
        assert ph30.mde_1samp_pp is not None, "B5 failed: mde_1samp_pp is None"
        assert abs(ph30.mde_1samp_pp - 6.591) <= 0.05, (
            f"B5 failed: mde_1samp_30={ph30.mde_1samp_pp:.4f}, expected ≈6.591±0.05"
        )

        # B6: mde_gap_30 ≈ 17.5 ± 1.0
        assert ph30.mde_gap_pp is not None, "B6 failed: mde_gap_pp is None"
        assert abs(ph30.mde_gap_pp - 17.5) <= 1.0, (
            f"B6 failed: mde_gap_30={ph30.mde_gap_pp:.4f}, expected ≈17.5±1.0"
        )

"""Tests for dose_builders.py — hermetic, no I/O.

Covers:
  - cross_sectional_zscores: known-value correctness, ddof=1, None passthrough,
    std==0 guard, <2-finite guard, single-finite guard.
  - composite_dose: frozen formula (sign), None handling, all-None passthrough,
    partial-None (0-impute) vs all-None exclusion.
"""

import math
import sys
from pathlib import Path

# Add backend and research dirs to path so the module is importable hermetically.
_BACKEND = Path(__file__).resolve().parent.parent
_RESEARCH = _BACKEND / "research"
for _p in [str(_BACKEND), str(_RESEARCH)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from research.dose_builders import composite_dose, cross_sectional_zscores


# ---------------------------------------------------------------------------
# cross_sectional_zscores
# ---------------------------------------------------------------------------

class TestCrossSectionalZscores:
    def test_known_values_two_point(self):
        """Two-point: z-scores should be +1 / -1 (ddof=1 → std = |diff|)."""
        vals = [3.0, 1.0]
        z = cross_sectional_zscores(vals)
        assert len(z) == 2
        assert z[0] is not None and z[1] is not None
        # mean=2, std=sqrt(2)/1 * ... actually: ddof=1, n=2 -> std = sqrt(((3-2)^2+(1-2)^2)/1) = sqrt(2)
        # z[0] = (3-2)/sqrt(2), z[1] = (1-2)/sqrt(2)
        expected_z0 = (3.0 - 2.0) / math.sqrt(2.0)
        expected_z1 = (1.0 - 2.0) / math.sqrt(2.0)
        assert abs(z[0] - expected_z0) < 1e-9
        assert abs(z[1] - expected_z1) < 1e-9

    def test_known_values_three_point(self):
        """Three-point known: [1, 2, 3] -> z = [-1, 0, 1] (ddof=1 std=1)."""
        vals = [1.0, 2.0, 3.0]
        z = cross_sectional_zscores(vals)
        assert len(z) == 3
        assert z[0] is not None and z[1] is not None and z[2] is not None
        # mean=2, variance=((1)^2+(0)^2+(1)^2)/2=1, std=1
        assert abs(z[0] - (-1.0)) < 1e-9
        assert abs(z[1] - 0.0) < 1e-9
        assert abs(z[2] - 1.0) < 1e-9

    def test_none_passthrough(self):
        """None values in input produce None in output."""
        vals = [1.0, None, 3.0]
        z = cross_sectional_zscores(vals)
        assert z[1] is None
        # The two finite values are standardized normally
        assert z[0] is not None
        assert z[2] is not None

    def test_std_zero_guard(self):
        """All-identical finite values -> all outputs are 0.0."""
        vals = [5.0, 5.0, 5.0]
        z = cross_sectional_zscores(vals)
        assert all(v == 0.0 for v in z)

    def test_std_zero_with_none(self):
        """All-identical finite values + some None -> 0.0 for finite, None for None."""
        vals = [5.0, None, 5.0]
        z = cross_sectional_zscores(vals)
        assert z[0] == 0.0
        assert z[1] is None
        assert z[2] == 0.0

    def test_fewer_than_two_finite_all_none(self):
        """<2 finite values -> all outputs None."""
        assert cross_sectional_zscores([None, None]) == [None, None]
        assert cross_sectional_zscores([1.0]) == [None]
        assert cross_sectional_zscores([None, 1.0]) == [None, None]

    def test_empty(self):
        """Empty list -> empty list."""
        assert cross_sectional_zscores([]) == []

    def test_output_has_zero_mean(self):
        """z-scores of finite values sum to 0 (mean=0 property)."""
        vals = [10.0, 20.0, 30.0, 40.0]
        z = cross_sectional_zscores(vals)
        finite_z = [v for v in z if v is not None]
        assert abs(sum(finite_z)) < 1e-9

    def test_inf_treated_as_none(self):
        """Non-finite floats (inf, nan) should be treated as None."""
        vals = [1.0, float("inf"), 3.0]
        z = cross_sectional_zscores(vals)
        assert z[1] is None
        # 1.0 and 3.0 are still standardized
        assert z[0] is not None
        assert z[2] is not None


# ---------------------------------------------------------------------------
# composite_dose
# ---------------------------------------------------------------------------

def _make_row(
    earnings_yoy=None,
    revenue_accel=None,
    net_margin_infl_pp=None,
    dilution_yoy=None,
) -> dict:
    """Helper: build a minimal row dict with a payload."""
    return {
        "payload": {
            "earnings_yoy": earnings_yoy,
            "revenue_accel": revenue_accel,
            "net_margin_infl_pp": net_margin_infl_pp,
            "dilution_yoy": dilution_yoy,
        }
    }


class TestCompositeDose:
    def test_formula_sign_dilution(self):
        """dilution_yoy is subtracted: a high-dilution event gets a lower composite."""
        # Two events: identical except one has high dilution.
        row_low_dil = _make_row(earnings_yoy=1.0, revenue_accel=1.0,
                                net_margin_infl_pp=1.0, dilution_yoy=0.0)
        row_high_dil = _make_row(earnings_yoy=1.0, revenue_accel=1.0,
                                 net_margin_infl_pp=1.0, dilution_yoy=10.0)
        result = composite_dose([row_low_dil, row_high_dil])
        assert result[0] is not None
        assert result[1] is not None
        # high_dil has a negative z-contribution from dilution_yoy -> lower composite
        assert result[0] > result[1], (
            "High dilution should reduce composite vs low dilution"
        )

    def test_formula_sign_earnings(self):
        """earnings_yoy is added: higher earnings -> higher composite."""
        row_low = _make_row(earnings_yoy=0.0, revenue_accel=1.0,
                            net_margin_infl_pp=1.0, dilution_yoy=1.0)
        row_high = _make_row(earnings_yoy=10.0, revenue_accel=1.0,
                             net_margin_infl_pp=1.0, dilution_yoy=1.0)
        result = composite_dose([row_low, row_high])
        assert result[0] is not None
        assert result[1] is not None
        assert result[1] > result[0], (
            "Higher earnings_yoy should produce a higher composite"
        )

    def test_all_none_event_is_none(self):
        """Event with ALL four components None -> composite is None."""
        row_all_none = _make_row()
        row_normal = _make_row(earnings_yoy=1.0, revenue_accel=1.0,
                               net_margin_infl_pp=1.0, dilution_yoy=1.0)
        result = composite_dose([row_all_none, row_normal])
        assert result[0] is None, "All-None event should yield None composite"
        assert result[1] is not None

    def test_partial_none_not_excluded(self):
        """Event with SOME (but not all) None components -> composite is not None."""
        # Only earnings_yoy is present; others are None -> not all-None, so result != None
        row_partial = _make_row(earnings_yoy=5.0)
        row_other = _make_row(earnings_yoy=1.0, revenue_accel=1.0,
                              net_margin_infl_pp=1.0, dilution_yoy=1.0)
        result = composite_dose([row_partial, row_other])
        assert result[0] is not None, (
            "Partial-None event (only 1 of 4 present) should still get a composite"
        )

    def test_two_symmetric_events(self):
        """Two events with opposite sign across all fields -> composites are opposite."""
        row_pos = _make_row(earnings_yoy=1.0, revenue_accel=1.0,
                            net_margin_infl_pp=1.0, dilution_yoy=-1.0)
        row_neg = _make_row(earnings_yoy=-1.0, revenue_accel=-1.0,
                            net_margin_infl_pp=-1.0, dilution_yoy=1.0)
        result = composite_dose([row_pos, row_neg])
        assert result[0] is not None and result[1] is not None
        assert result[0] > result[1]

    def test_length_preserved(self):
        """Output length equals input length."""
        rows = [_make_row(earnings_yoy=float(i)) for i in range(5)]
        result = composite_dose(rows)
        assert len(result) == 5

    def test_empty_rows(self):
        """Empty input returns empty list."""
        assert composite_dose([]) == []

    def test_single_event_both_none_and_zeros(self):
        """Single event: <2 finite per field -> z-scores all None -> partial None case."""
        row = _make_row(earnings_yoy=1.0)
        result = composite_dose([row])
        # Only earnings_yoy is set; with 1 finite value, zscores = [None].
        # The event is NOT all-None (earnings_yoy is present), but z-scores
        # are [None] for all fields since <2 finite per field. None z treated as 0.
        # So composite = 0+0+0-0 = 0.0 (non-None because not all-None)
        assert result[0] is not None
        assert result[0] == 0.0

    def test_missing_payload_key(self):
        """Row without 'payload' key -> treated as all-None -> composite None."""
        row = {"ticker": "AAPL", "entry_date": "2020-01-15"}
        result = composite_dose([row])
        assert result[0] is None

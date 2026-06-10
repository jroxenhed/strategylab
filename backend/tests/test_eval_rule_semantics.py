"""
F170 — Pre-refactor regression-lock tests for eval_rule / eval_rules.

These tests characterize CURRENT behavior exactly so the future vectorization
refactor (F170) has a safety net.  No production code is touched here.

Coverage added (per the F170 brief "coverage gaps" list):
  1. NaN/warmup bars — None series resolution, NaN at i for each condition branch
  2. crossover & crosses_above / crosses_below at i=0 (guard) and i=1 (boundary)
  3. turns_up / turns_down slope semantics, threshold/param variants, backward scan
  4. decelerating / accelerating at i=2 (minimum valid) and i=1 (guard)
  5. negated-rule interaction with the i<1 guard (guard returns False regardless)
  6. above / below with a ref series (two-array path)
  7. eval_rules _arr_cache kwarg — results identical, cache populated after first call

NOTE on boolean identity: eval_rule returns numpy.bool_ (from pandas .iloc comparisons),
not Python bool.  All assertions use == rather than `is` — `np.True_ is True` is False
in Python and would produce spurious failures.  The `is` idiom is preserved only for
return-False-from-guard paths that explicitly `return False` (pure Python bools).
"""

import math
import pytest
import pandas as pd
import numpy as np
from signal_engine import Rule, eval_rule, eval_rules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _series(*vals: float) -> pd.Series:
    return pd.Series(list(vals), dtype=float)


def _indicators_price(*vals: float) -> dict:
    """Minimal indicators dict with a 'close' series."""
    return {"close": _series(*vals)}


# ---------------------------------------------------------------------------
# 1. NaN / warmup bars
# ---------------------------------------------------------------------------

class TestNaNBars:
    """NaN values in a series must propagate without exceptions and produce
    deterministic (IEEE 754) results.  All comparisons with NaN → False."""

    def _ind(self, *vals):
        return {"close": _series(*vals)}

    def test_nan_above_returns_false(self):
        """NaN > threshold is False (IEEE 754)."""
        ind = self._ind(1.0, float("nan"))
        rule = Rule(indicator="price", condition="above", value=0.5)
        assert eval_rule(rule, ind, i=1) == False  # noqa: E712

    def test_nan_below_returns_false(self):
        """NaN < threshold is False (IEEE 754)."""
        ind = self._ind(1.0, float("nan"))
        rule = Rule(indicator="price", condition="below", value=100.0)
        assert eval_rule(rule, ind, i=1) == False  # noqa: E712

    def test_nan_rises_returns_false(self):
        """NaN > prev is False."""
        ind = self._ind(1.0, float("nan"))
        rule = Rule(indicator="price", condition="rising")
        assert eval_rule(rule, ind, i=1) == False  # noqa: E712

    def test_nan_falls_returns_false(self):
        """NaN < prev is False."""
        ind = self._ind(1.0, float("nan"))
        rule = Rule(indicator="price", condition="falling")
        assert eval_rule(rule, ind, i=1) == False  # noqa: E712

    def test_nan_crossover_up_returns_false_when_now_nan(self):
        """NaN at i for crossover_up — v_now >= threshold is False."""
        ind = self._ind(1.0, float("nan"))
        rule = Rule(indicator="price", condition="crossover_up", value=1.5)
        assert eval_rule(rule, ind, i=1) == False  # noqa: E712

    def test_nan_crossover_up_returns_false_when_prev_nan(self):
        """NaN at i-1 for crossover_up — v_prev < threshold is False, so overall False."""
        ind = self._ind(float("nan"), 2.0)
        rule = Rule(indicator="price", condition="crossover_up", value=1.5)
        # v_prev=NaN < 1.5 is False → crossover False
        assert eval_rule(rule, ind, i=1) == False  # noqa: E712

    def test_nan_crossover_down_returns_false_when_now_nan(self):
        """NaN at i for crossover_down."""
        ind = self._ind(2.0, float("nan"))
        rule = Rule(indicator="price", condition="crossover_down", value=1.5)
        assert eval_rule(rule, ind, i=1) == False  # noqa: E712

    def test_nan_turns_up_returns_false_or_bool_no_exception(self):
        """NaN in the lookback window for turns_up must not raise; result is a bool."""
        # [3, 2, NaN, 4] — NaN in the rising window; current code may produce
        # True or False depending on how NaN propagates through the comparisons,
        # but must never raise an exception.
        ind = self._ind(3.0, 2.0, float("nan"), 4.0)
        rule = Rule(indicator="price", condition="turns_up", value=1)
        result = eval_rule(rule, ind, i=3)
        assert isinstance(bool(result), bool)

    def test_nan_decelerating_returns_false(self):
        """NaN at i for decelerating (i>=2 guard already passed):
        d_now = NaN - prev = NaN; NaN < 0 is False."""
        ind = self._ind(1.0, 3.0, float("nan"))
        rule = Rule(indicator="price", condition="decelerating")
        assert eval_rule(rule, ind, i=2) == False  # noqa: E712

    def test_none_series_returns_false(self):
        """resolve_series returning None (missing indicator key) → False without exception.
        eval_rule explicitly returns False when s is None."""
        indicators: dict = {}
        rule = Rule(indicator="rsi", condition="above", value=50)
        result = eval_rule(rule, indicators, i=1)
        # Explicit `return False` in the guard → Python bool False (not numpy.bool_)
        assert result is False


# ---------------------------------------------------------------------------
# 2. crossover / crosses_above / crosses_below at i=0 and i=1
# ---------------------------------------------------------------------------

class TestCrossoverBoundaries:

    def test_crossover_up_at_i0_returns_false(self):
        """i < 1 guard: crossover_up always returns Python False at i=0."""
        ind = _indicators_price(0.5, 2.0)
        rule = Rule(indicator="price", condition="crossover_up", value=1.0)
        # guard `if i < 1: return False` is an explicit Python return
        assert eval_rule(rule, ind, i=0) is False

    def test_crosses_above_at_i0_returns_false(self):
        ind = _indicators_price(0.5, 2.0)
        rule = Rule(indicator="price", condition="crosses_above", value=1.0)
        assert eval_rule(rule, ind, i=0) is False

    def test_crossover_up_at_i1_true(self):
        """i=1 is the minimum valid index: prev=0.5 < threshold=1.0 <= now=2.0."""
        ind = _indicators_price(0.5, 2.0)
        rule = Rule(indicator="price", condition="crossover_up", value=1.0)
        assert eval_rule(rule, ind, i=1) == True  # noqa: E712

    def test_crossover_up_at_i1_false_when_already_above(self):
        """Crossover requires crossing FROM below: if prev already >= value, False."""
        ind = _indicators_price(1.5, 2.0)
        rule = Rule(indicator="price", condition="crossover_up", value=1.0)
        assert eval_rule(rule, ind, i=1) == False  # noqa: E712

    def test_crosses_above_at_i1_true(self):
        ind = _indicators_price(0.5, 2.0)
        rule = Rule(indicator="price", condition="crosses_above", value=1.0)
        assert eval_rule(rule, ind, i=1) == True  # noqa: E712

    def test_crossover_down_at_i0_returns_false(self):
        ind = _indicators_price(2.0, 0.5)
        rule = Rule(indicator="price", condition="crossover_down", value=1.0)
        assert eval_rule(rule, ind, i=0) is False

    def test_crosses_below_at_i0_returns_false(self):
        ind = _indicators_price(2.0, 0.5)
        rule = Rule(indicator="price", condition="crosses_below", value=1.0)
        assert eval_rule(rule, ind, i=0) is False

    def test_crossover_down_at_i1_true(self):
        """i=1: prev=2.0 > threshold=1.0 >= now=0.5."""
        ind = _indicators_price(2.0, 0.5)
        rule = Rule(indicator="price", condition="crossover_down", value=1.0)
        assert eval_rule(rule, ind, i=1) == True  # noqa: E712

    def test_crosses_below_at_i1_true(self):
        ind = _indicators_price(2.0, 0.5)
        rule = Rule(indicator="price", condition="crosses_below", value=1.0)
        assert eval_rule(rule, ind, i=1) == True  # noqa: E712

    def test_crossover_up_with_ref_series_at_i1(self):
        """crosses_above with param ref series at i=1:
        price goes from 0.5 → 2.0, ref stays at 1.0."""
        indicators = {
            "close": _series(0.5, 2.0),
            "ma_10_sma": _series(1.0, 1.0),
        }
        rule = Rule(indicator="price", condition="crosses_above", param="ma:10:sma")
        assert eval_rule(rule, indicators, i=1) == True  # noqa: E712

    def test_crossover_down_with_ref_series_at_i1(self):
        """crosses_below with param ref series at i=1."""
        indicators = {
            "close": _series(2.0, 0.5),
            "ma_10_sma": _series(1.0, 1.0),
        }
        rule = Rule(indicator="price", condition="crosses_below", param="ma:10:sma")
        assert eval_rule(rule, indicators, i=1) == True  # noqa: E712


# ---------------------------------------------------------------------------
# 3. above / below with a ref series (two-array path)
# ---------------------------------------------------------------------------

class TestAboveBelowWithRef:

    def test_above_with_ref_series_true(self):
        """price > ma at current bar."""
        indicators = {
            "close": _series(1.0, 5.0),
            "ma_20_ema": _series(1.0, 3.0),
        }
        rule = Rule(indicator="price", condition="above", param="ma:20:ema")
        assert eval_rule(rule, indicators, i=1) == True  # noqa: E712

    def test_above_with_ref_series_false(self):
        indicators = {
            "close": _series(1.0, 2.0),
            "ma_20_ema": _series(1.0, 3.0),
        }
        rule = Rule(indicator="price", condition="above", param="ma:20:ema")
        assert eval_rule(rule, indicators, i=1) == False  # noqa: E712

    def test_below_with_ref_series_true(self):
        indicators = {
            "close": _series(1.0, 2.0),
            "ma_20_ema": _series(1.0, 3.0),
        }
        rule = Rule(indicator="price", condition="below", param="ma:20:ema")
        assert eval_rule(rule, indicators, i=1) == True  # noqa: E712

    def test_below_with_ref_series_false(self):
        indicators = {
            "close": _series(1.0, 5.0),
            "ma_20_ema": _series(1.0, 3.0),
        }
        rule = Rule(indicator="price", condition="below", param="ma:20:ema")
        assert eval_rule(rule, indicators, i=1) == False  # noqa: E712

    def test_above_with_ref_equal_returns_false(self):
        """Strict >: equal is not above."""
        indicators = {
            "close": _series(1.0, 3.0),
            "ma_20_ema": _series(1.0, 3.0),
        }
        rule = Rule(indicator="price", condition="above", param="ma:20:ema")
        assert eval_rule(rule, indicators, i=1) == False  # noqa: E712


# ---------------------------------------------------------------------------
# 4. turns_up / turns_down — slope semantics, threshold, backward scan
# ---------------------------------------------------------------------------

class TestTurnsUpDown:

    # --- basic turns_up ---

    def test_turns_up_basic_true(self):
        """[3, 2, 3]: i=2 — falling then rising → turns_up.
        k=0: s[2]-s[1]=1 > 0 ✓; s[1]-s[0]=-1 < 0 ✓."""
        ind = _indicators_price(3.0, 2.0, 3.0)
        rule = Rule(indicator="price", condition="turns_up", value=1)
        assert eval_rule(rule, ind, i=2) == True  # noqa: E712

    def test_turns_up_basic_false_still_rising(self):
        """[1, 2, 3]: continuously rising, no turn — the 'before' bar is also rising."""
        ind = _indicators_price(1.0, 2.0, 3.0)
        rule = Rule(indicator="price", condition="turns_up", value=1)
        assert eval_rule(rule, ind, i=2) == False  # noqa: E712

    def test_turns_up_at_i1_returns_false_due_to_guard(self):
        """i=1 with lookback=1: i < lookback+1 (1 < 2) → guard triggers False."""
        ind = _indicators_price(2.0, 3.0)
        rule = Rule(indicator="price", condition="turns_up", value=1)
        # Internal turns_up guard fires before comparison — explicit `return False`
        assert eval_rule(rule, ind, i=1) is False

    def test_turns_up_multi_bar_lookback(self):
        """lookback=3: last 3 bars all rising, bar before falling.
        Series [5,4,3,4,5,6] — i=5:
          k=0: s[5]-s[4]=1>0; k=1: s[4]-s[3]=1>0; k=2: s[3]-s[2]=1>0;
          s[i-lookback]-s[i-lookback-1] = s[2]-s[1] = 3-4 = -1 < 0 ✓"""
        ind = _indicators_price(5.0, 4.0, 3.0, 4.0, 5.0, 6.0)
        rule = Rule(indicator="price", condition="turns_up", value=3)
        assert eval_rule(rule, ind, i=5) == True  # noqa: E712

    # --- basic turns_down ---

    def test_turns_down_basic_true(self):
        """[1, 2, 1]: rising then falling → turns_down."""
        ind = _indicators_price(1.0, 2.0, 1.0)
        rule = Rule(indicator="price", condition="turns_down", value=1)
        assert eval_rule(rule, ind, i=2) == True  # noqa: E712

    def test_turns_down_basic_false_still_falling(self):
        """[3, 2, 1]: continuously falling, no turn."""
        ind = _indicators_price(3.0, 2.0, 1.0)
        rule = Rule(indicator="price", condition="turns_down", value=1)
        assert eval_rule(rule, ind, i=2) == False  # noqa: E712

    def test_turns_down_at_i1_returns_false_due_to_guard(self):
        ind = _indicators_price(2.0, 1.0)
        rule = Rule(indicator="price", condition="turns_down", value=1)
        assert eval_rule(rule, ind, i=1) is False

    # --- threshold (backward scan) ---

    def test_turns_up_with_threshold_met(self):
        """turns_up with threshold=10% met.
        Series [10, 9, 11, 9, 11] — i=4, lookback=1:
          k=0: s[4]-s[3]=11-9=2>0 ✓
          s[i-lookback]-s[i-lookback-1] = s[3]-s[2] = 9-11 = -2 < 0 ✓ (bar before was falling)
          trough scan: initial trough=s[3]=9; j=2: v=11>9 → break; trough=9
          rise = (11-9)/9*100 = 22.2% >= 10% → True"""
        ind = _indicators_price(10.0, 9.0, 11.0, 9.0, 11.0)
        rule = Rule(indicator="price", condition="turns_up", value=1, threshold=10.0)
        assert eval_rule(rule, ind, i=4) == True  # noqa: E712

    def test_turns_up_with_threshold_not_met(self):
        """Same series, threshold=30%: 22.2% < 30% → False."""
        ind = _indicators_price(10.0, 9.0, 11.0, 9.0, 11.0)
        rule = Rule(indicator="price", condition="turns_up", value=1, threshold=30.0)
        assert eval_rule(rule, ind, i=4) == False  # noqa: E712

    def test_turns_down_with_threshold_met(self):
        """turns_down with threshold=10% met.
        Series [8, 11, 10, 11, 9] — i=4, lookback=1:
          k=0: s[4]-s[3]=9-11=-2<0 ✓
          s[i-lookback]-s[i-lookback-1] = s[3]-s[2] = 11-10 = 1 > 0 ✓ (bar before was rising)
          peak scan: initial peak=s[3]=11; j=2: v=10<11 → break; peak=11
          drop = (11-9)/11*100 = 18.2% >= 10% → True"""
        ind = _indicators_price(8.0, 11.0, 10.0, 11.0, 9.0)
        rule = Rule(indicator="price", condition="turns_down", value=1, threshold=10.0)
        assert eval_rule(rule, ind, i=4) == True  # noqa: E712

    def test_turns_down_with_threshold_not_met(self):
        """Same series, threshold=25%: 18.2% < 25% → False."""
        ind = _indicators_price(8.0, 11.0, 10.0, 11.0, 9.0)
        rule = Rule(indicator="price", condition="turns_down", value=1, threshold=25.0)
        assert eval_rule(rule, ind, i=4) == False  # noqa: E712

    def test_turns_up_with_threshold_zero_trough_returns_false(self):
        """When trough is ~0 (abs < 1e-12), threshold guard returns False (no div-by-zero)."""
        # Build a valid turns_up shape: [0, 0, 0.5, 0, 0.5]
        # s[4]=0.5 > s[3]=0? Not <=0 — but s[3]-s[2]=0-0.5=-0.5<0 so bar before was falling
        # trough=s[3]=0 → abs(0) < 1e-12 → return False
        ind = _indicators_price(1.0, 0.0, 0.5, 0.0, 0.5)
        rule = Rule(indicator="price", condition="turns_up", value=1, threshold=10.0)
        result = eval_rule(rule, ind, i=4)
        # trough=0 so threshold guard fires → False
        assert result == False  # noqa: E712

    def test_turns_up_backward_scan_finds_true_trough(self):
        """Backward trough scan walks past the immediate lookback bar to find the true trough.
        Series [20, 5, 4, 5, 6, 7] — i=5, lookback=3:
          Last 3 bars rising: s[5]-s[4]=1>0, s[4]-s[3]=1>0, s[3]-s[2]=1>0 ✓
          s[i-lookback]-s[i-lookback-1] = s[2]-s[1] = 4-5 = -1 < 0 ✓ (bar before was falling)
          trough scan from j=1 down to 0: initial=s[2]=4; j=1: v=5>4 → break; trough=4
          rise = (7-4)/4*100 = 75% >= 30% → True"""
        ind = _indicators_price(20.0, 5.0, 4.0, 5.0, 6.0, 7.0)
        rule = Rule(indicator="price", condition="turns_up", value=3, threshold=30.0)
        assert eval_rule(rule, ind, i=5) == True  # noqa: E712


# ---------------------------------------------------------------------------
# 5. decelerating / accelerating at i=2 (minimum) and i=1 (guard)
# ---------------------------------------------------------------------------

class TestDeceleratingAccelerating:

    def test_decelerating_at_i1_returns_false(self):
        """i < 2 guard: decelerating False at i=1 — explicit Python `return False`."""
        ind = _indicators_price(1.0, 2.0, 3.0)
        rule = Rule(indicator="price", condition="decelerating")
        assert eval_rule(rule, ind, i=1) is False

    def test_accelerating_at_i1_returns_false(self):
        ind = _indicators_price(1.0, 2.0, 3.0)
        rule = Rule(indicator="price", condition="accelerating")
        assert eval_rule(rule, ind, i=1) is False

    def test_decelerating_at_i2_true(self):
        """Minimum valid i=2: [1, 4, 3] — d_now=3-4=-1, d_prev=4-1=3; d_now-d_prev=-4<0."""
        ind = _indicators_price(1.0, 4.0, 3.0)
        rule = Rule(indicator="price", condition="decelerating")
        assert eval_rule(rule, ind, i=2) == True  # noqa: E712

    def test_decelerating_at_i2_false_when_accelerating(self):
        """[1, 2, 4]: d_now=2, d_prev=1 → d_now-d_prev=1>0 → not decelerating."""
        ind = _indicators_price(1.0, 2.0, 4.0)
        rule = Rule(indicator="price", condition="decelerating")
        assert eval_rule(rule, ind, i=2) == False  # noqa: E712

    def test_accelerating_at_i2_true(self):
        """[1, 2, 4]: d_now=2, d_prev=1 → d_now-d_prev=1>0 → accelerating."""
        ind = _indicators_price(1.0, 2.0, 4.0)
        rule = Rule(indicator="price", condition="accelerating")
        assert eval_rule(rule, ind, i=2) == True  # noqa: E712

    def test_accelerating_at_i2_false(self):
        """[1, 4, 3]: d_now=-1, d_prev=3 → not accelerating."""
        ind = _indicators_price(1.0, 4.0, 3.0)
        rule = Rule(indicator="price", condition="accelerating")
        assert eval_rule(rule, ind, i=2) == False  # noqa: E712

    def test_decelerating_constant_series_returns_false(self):
        """Constant series: d_now=0, d_prev=0 → d_now-d_prev=0, not < 0."""
        ind = _indicators_price(5.0, 5.0, 5.0)
        rule = Rule(indicator="price", condition="decelerating")
        assert eval_rule(rule, ind, i=2) == False  # noqa: E712

    def test_accelerating_constant_series_returns_false(self):
        """Constant series: not accelerating (0 not > 0)."""
        ind = _indicators_price(5.0, 5.0, 5.0)
        rule = Rule(indicator="price", condition="accelerating")
        assert eval_rule(rule, ind, i=2) == False  # noqa: E712

    def test_decelerating_nan_returns_false(self):
        """NaN at i for decelerating: d_now = NaN - prev = NaN; NaN < 0 is False."""
        ind = _indicators_price(1.0, 3.0, float("nan"))
        rule = Rule(indicator="price", condition="decelerating")
        assert eval_rule(rule, ind, i=2) == False  # noqa: E712


# ---------------------------------------------------------------------------
# 6. negated-rule interaction with the i<1 guard
# ---------------------------------------------------------------------------

class TestNegatedRuleGuard:
    """Per CLAUDE.md: 'guard condition (i < 1) always returns False regardless of negation'.
    eval_rule returns False for i=0 unconditionally.  The negation condition in eval_rules
    is `r.negated and i >= 1` — so at i=0, negation is also suppressed: a negated rule
    at i=0 still yields False (not True)."""

    def _simple_above_rule(self, negated=False):
        return Rule(indicator="price", condition="above", value=0.5, negated=negated)

    def test_non_negated_rule_at_i0_returns_false(self):
        ind = _indicators_price(1.0, 2.0)
        rule = self._simple_above_rule(negated=False)
        assert eval_rule(rule, ind, i=0) is False

    def test_negated_rule_at_i0_eval_rule_returns_false(self):
        """eval_rule itself doesn't apply negation — it always returns False at i=0
        via the explicit guard `if i < 1: return False`."""
        ind = _indicators_price(1.0, 2.0)
        rule = self._simple_above_rule(negated=True)
        assert eval_rule(rule, ind, i=0) is False

    def test_negated_rule_at_i0_via_eval_rules_returns_false(self):
        """eval_rules does NOT invert the False returned by the i<1 guard,
        because the condition is `r.negated and i >= 1` — i<1 suppresses inversion.
        This is the CLAUDE.md canonical behavior for the guard interaction."""
        ind = _indicators_price(1.0, 2.0)
        rule = self._simple_above_rule(negated=True)
        # raw=False, negated=True but i=0 so (negated and i>=1)=False → result=False
        assert eval_rules([rule], "AND", ind, i=0) is False

    def test_negated_rule_at_i1_inverts_true_result(self):
        """At i=1, above=True (1.0 > 0.5), negated → False."""
        ind = _indicators_price(0.0, 1.0)
        rule = self._simple_above_rule(negated=True)
        assert eval_rules([rule], "AND", ind, i=1) is False

    def test_negated_rule_at_i1_inverts_false_result(self):
        """At i=1, above=False (0.3 not > 0.5), negated → True."""
        ind = _indicators_price(0.0, 0.3)
        rule = self._simple_above_rule(negated=True)
        assert eval_rules([rule], "AND", ind, i=1) is True

    def test_negated_crossover_at_i0_returns_false_not_true(self):
        """A negated crossover at i=0 still returns False (guard, not inverted)."""
        ind = _indicators_price(0.5, 2.0)
        rule = Rule(indicator="price", condition="crossover_up", value=1.0, negated=True)
        assert eval_rules([rule], "AND", ind, i=0) is False

    def test_negated_turns_up_internal_guard_at_i1_inverted_by_eval_rules(self):
        """turns_up has its own internal guard (i < lookback+1).  At i=1 with lookback=1,
        the internal guard fires (returns Python False).  eval_rules still applies
        negation because i >= 1 is True — so the False is inverted to True."""
        ind = _indicators_price(2.0, 3.0)
        rule = Rule(indicator="price", condition="turns_up", value=1, negated=True)
        # eval_rule returns False (internal guard); eval_rules: negated and i>=1 → invert → True
        assert eval_rules([rule], "AND", ind, i=1) is True

    def test_negated_and_muted_rule_skipped(self):
        """Muted rules are skipped entirely regardless of negated flag."""
        ind = _indicators_price(0.0, 1.0)
        muted_rule = Rule(indicator="price", condition="above", value=0.5,
                          negated=True, muted=True)
        non_muted = Rule(indicator="price", condition="above", value=0.5)
        # muted rule skipped; only non-muted (above=True) → AND result = True
        assert eval_rules([muted_rule, non_muted], "AND", ind, i=1) is True


# ---------------------------------------------------------------------------
# 7. eval_rules _arr_cache kwarg — xfail until F170 vectorization is implemented
# ---------------------------------------------------------------------------

class TestEvalRulesArrCache:
    """These tests characterize the EXPECTED post-refactor behavior.
    They are marked xfail(strict=True) and will become xpass once F170 adds
    the _arr_cache kwarg to eval_rules / eval_rule."""

    def _make_indicators(self):
        rng = np.random.default_rng(0)
        close = pd.Series(100.0 + rng.normal(0, 1, 50).cumsum())
        return {"close": close}

    def _make_rules(self):
        return [
            Rule(indicator="price", condition="above", value=95.0),
            Rule(indicator="price", condition="rising"),
        ]

    @pytest.mark.xfail(
        reason="F170 not yet implemented: _arr_cache kwarg absent from eval_rules",
        strict=True,
    )
    def test_eval_rules_arr_cache_produces_same_results(self):
        """eval_rules with _arr_cache={} must produce identical results to without cache
        for a sample of bar indices."""
        indicators = self._make_indicators()
        rules = self._make_rules()
        for i in range(5, 45):
            result_no_cache = eval_rules(rules, "AND", indicators, i)
            result_with_cache = eval_rules(rules, "AND", indicators, i, _arr_cache={})
            assert result_no_cache == result_with_cache, f"Mismatch at i={i}"

    @pytest.mark.xfail(
        reason="F170 not yet implemented: _arr_cache kwarg absent from eval_rules",
        strict=True,
    )
    def test_eval_rules_arr_cache_populated_after_call(self):
        """After a call with _arr_cache={}, the dict must be non-empty (extraction ran)."""
        indicators = self._make_indicators()
        rules = self._make_rules()
        cache: dict = {}
        eval_rules(rules, "AND", indicators, i=10, _arr_cache=cache)
        assert len(cache) > 0, "Expected numpy arrays in cache after first call"

    @pytest.mark.xfail(
        reason="F170 not yet implemented: _arr_cache kwarg absent from eval_rules",
        strict=True,
    )
    def test_eval_rules_arr_cache_reuse_returns_same_value(self):
        """Calling twice with the same cache object must return the same result."""
        indicators = self._make_indicators()
        rules = self._make_rules()
        cache: dict = {}
        r1 = eval_rules(rules, "AND", indicators, i=20, _arr_cache=cache)
        r2 = eval_rules(rules, "AND", indicators, i=20, _arr_cache=cache)
        assert r1 == r2


# ---------------------------------------------------------------------------
# 8. Additional boundary / regression-lock cases
# ---------------------------------------------------------------------------

class TestAdditionalBoundaries:

    def test_rising_over_at_i0_returns_false(self):
        """i<1 guard: rising_over at i=0 → Python False."""
        ind = _indicators_price(1.0, 2.0)
        rule = Rule(indicator="price", condition="rising_over", value=1)
        assert eval_rule(rule, ind, i=0) is False

    def test_falling_over_at_i0_returns_false(self):
        ind = _indicators_price(2.0, 1.0)
        rule = Rule(indicator="price", condition="falling_over", value=1)
        assert eval_rule(rule, ind, i=0) is False

    def test_rising_over_guard_when_i_less_than_lookback(self):
        """rising_over guard: i < lookback → Python False (explicit return)."""
        ind = _indicators_price(1.0, 2.0, 3.0)
        rule = Rule(indicator="price", condition="rising_over", value=5)  # lookback=5 > i=2
        assert eval_rule(rule, ind, i=2) is False

    def test_turns_up_below_at_i0_returns_false(self):
        ind = _indicators_price(0.3, 0.5)
        rule = Rule(indicator="price", condition="turns_up_below", value=1.0)
        assert eval_rule(rule, ind, i=0) is False

    def test_turns_down_above_at_i0_returns_false(self):
        ind = _indicators_price(1.5, 1.0)
        rule = Rule(indicator="price", condition="turns_down_above", value=0.5)
        assert eval_rule(rule, ind, i=0) is False

    def test_turns_up_below_basic(self):
        """[0.3, 0.5]: i=1 — prev=0.3 < value=1.0 ✓ and now=0.5 > prev=0.3 ✓ → True."""
        ind = _indicators_price(0.3, 0.5)
        rule = Rule(indicator="price", condition="turns_up_below", value=1.0)
        assert eval_rule(rule, ind, i=1) == True  # noqa: E712

    def test_turns_up_below_false_when_above_value(self):
        """prev > value: first condition fails."""
        ind = _indicators_price(1.5, 2.0)
        rule = Rule(indicator="price", condition="turns_up_below", value=1.0)
        assert eval_rule(rule, ind, i=1) == False  # noqa: E712

    def test_turns_down_above_basic(self):
        """[1.5, 1.0]: i=1 — prev=1.5 > value=0.5 ✓ and now=1.0 < prev=1.5 ✓ → True."""
        ind = _indicators_price(1.5, 1.0)
        rule = Rule(indicator="price", condition="turns_down_above", value=0.5)
        assert eval_rule(rule, ind, i=1) == True  # noqa: E712

    def test_eval_rules_empty_list_returns_false(self):
        """Empty non-muted rule list → False regardless of logic."""
        ind = _indicators_price(1.0, 2.0)
        assert eval_rules([], "AND", ind, i=1) is False
        assert eval_rules([], "OR", ind, i=1) is False

    def test_eval_rules_invalid_logic_raises(self):
        ind = _indicators_price(1.0, 2.0)
        with pytest.raises(ValueError, match="logic must be"):
            eval_rules([], "XOR", ind, i=1)

    def test_eval_rules_or_logic_one_true_suffices(self):
        ind = _indicators_price(0.0, 1.0)
        rule_true = Rule(indicator="price", condition="above", value=0.5)
        rule_false = Rule(indicator="price", condition="above", value=5.0)
        assert eval_rules([rule_true, rule_false], "OR", ind, i=1) is True

    def test_eval_rules_and_logic_one_false_fails(self):
        ind = _indicators_price(0.0, 1.0)
        rule_true = Rule(indicator="price", condition="above", value=0.5)
        rule_false = Rule(indicator="price", condition="above", value=5.0)
        assert eval_rules([rule_true, rule_false], "AND", ind, i=1) is False

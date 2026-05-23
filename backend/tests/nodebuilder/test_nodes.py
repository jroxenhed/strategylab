"""Unit 7b — tests for node impl functions in nodebuilder/nodes.py."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from nodebuilder.nodes import (
    NODE_CATALOG,
    NODE_IMPLS,
    IndicatorImplResult,
    PerBarImplResult,
    SimulatorSettingImplResult,
    above_impl,
    and_impl,
    atr_impl,
    below_impl,
    bollinger_impl,
    commission_impl,
    crosses_above_impl,
    crosses_below_impl,
    ema_impl,
    macd_impl,
    not_impl,
    or_impl,
    position_size_impl,
    rsi_impl,
    slippage_impl,
    sma_impl,
    stop_loss_impl,
)


# ---------------------------------------------------------------------------
# Helper — build a single-column attrs dict from a list
# ---------------------------------------------------------------------------

def _attrs(name: str, values: list) -> dict:
    """Wrap a list of values as a pd.Series under the given attribute name."""
    return {name: pd.Series(values, dtype=float)}


def _attrs2(a: str, va: list, b: str, vb: list) -> dict:
    return {
        a: pd.Series(va, dtype=float),
        b: pd.Series(vb, dtype=float),
    }


# ===========================================================================
# Indicator impls
# ===========================================================================


class TestRsiImpl:
    def test_default(self):
        r = rsi_impl({})
        assert isinstance(r, IndicatorImplResult)
        assert r.catalog_name == "rsi"
        assert r.params["period"] == 14
        assert r.params["type"] == "sma"
        assert r.write_attr == "@rsi"

    def test_custom(self):
        r = rsi_impl({"period": 9, "type": "wilder"})
        assert r.params["period"] == 9
        assert r.params["type"] == "wilder"

    def test_period_below_2_raises(self):
        with pytest.raises(ValueError, match="period"):
            rsi_impl({"period": 1})

    def test_period_above_500_raises(self):
        with pytest.raises(ValueError, match="period"):
            rsi_impl({"period": 501})


class TestMacdImpl:
    def test_defaults(self):
        r = macd_impl({})
        assert r.catalog_name == "macd"
        assert r.params["fast"] == 12
        assert r.params["slow"] == 26
        assert r.params["signal"] == 9
        assert r.write_attr == "@macd_line"

    def test_invalid_fast_raises(self):
        with pytest.raises(ValueError, match="fast"):
            macd_impl({"fast": 1})


class TestSmaEmaDistinct:
    def test_sma_catalog_name_and_type(self):
        r = sma_impl({})
        assert r.catalog_name == "sma"
        assert r.params["type"] == "sma"
        assert r.write_attr == "@sma"

    def test_ema_catalog_name_and_type(self):
        r = ema_impl({})
        assert r.catalog_name == "ema"
        assert r.params["type"] == "ema"
        assert r.write_attr == "@ema"

    def test_sma_ema_are_distinct(self):
        sma = sma_impl({})
        ema = ema_impl({})
        assert sma.catalog_name != ema.catalog_name
        assert sma.params["type"] != ema.params["type"]

    def test_sma_period_below_2_raises(self):
        with pytest.raises(ValueError):
            sma_impl({"period": 0})

    def test_ema_period_below_2_raises(self):
        with pytest.raises(ValueError):
            ema_impl({"period": 1})


class TestBollingerImpl:
    def test_defaults(self):
        r = bollinger_impl({})
        assert r.catalog_name == "bollinger"
        assert r.params["period"] == 20
        assert r.params["stddev"] == 2.0
        assert r.write_attr == "@bb_upper"

    def test_invalid_period_raises(self):
        with pytest.raises(ValueError, match="period"):
            bollinger_impl({"period": 1})

    def test_invalid_stddev_raises(self):
        with pytest.raises(ValueError, match="stddev"):
            bollinger_impl({"stddev": 0.1})


class TestAtrImpl:
    def test_default(self):
        r = atr_impl({})
        assert r.catalog_name == "atr"
        assert r.params["period"] == 14
        assert r.write_attr == "@atr"

    def test_invalid_period_raises(self):
        with pytest.raises(ValueError, match="period"):
            atr_impl({"period": 1})


# ===========================================================================
# Comparison impls
# ===========================================================================


class TestBelowImpl:
    def test_threshold_true(self):
        result = below_impl({"threshold": 30}, ("@rsi",))
        attrs = _attrs("@rsi", [25.0])
        assert result.fn(attrs, 0) is True

    def test_threshold_false(self):
        result = below_impl({"threshold": 30}, ("@rsi",))
        attrs = _attrs("@rsi", [35.0])
        assert result.fn(attrs, 0) is False

    def test_two_inputs(self):
        result = below_impl({}, ("@close", "@ema"))
        attrs = _attrs2("@close", [100.0], "@ema", [110.0])
        assert result.fn(attrs, 0) is True

    def test_two_inputs_false(self):
        result = below_impl({}, ("@close", "@ema"))
        attrs = _attrs2("@close", [120.0], "@ema", [110.0])
        assert result.fn(attrs, 0) is False

    def test_nan_returns_false(self):
        result = below_impl({"threshold": 30}, ("@rsi",))
        attrs = _attrs("@rsi", [float("nan")])
        assert result.fn(attrs, 0) is False

    def test_missing_threshold_and_one_attr_raises(self):
        with pytest.raises(ValueError):
            below_impl({}, ("@rsi",))

    def test_writes_bool(self):
        r = below_impl({"threshold": 30}, ("@rsi",))
        assert r.writes == "@bool"


class TestAboveImpl:
    def test_threshold_true(self):
        result = above_impl({"threshold": 70}, ("@rsi",))
        attrs = _attrs("@rsi", [75.0])
        assert result.fn(attrs, 0) is True

    def test_threshold_false(self):
        result = above_impl({"threshold": 70}, ("@rsi",))
        attrs = _attrs("@rsi", [65.0])
        assert result.fn(attrs, 0) is False

    def test_nan_returns_false(self):
        result = above_impl({"threshold": 70}, ("@rsi",))
        attrs = _attrs("@rsi", [float("nan")])
        assert result.fn(attrs, 0) is False


class TestCrossesAboveImpl:
    def test_fires_on_crossover_bar(self):
        # Series: [25, 28, 35] with threshold=30
        # Bar 0: i=0 guard → False
        # Bar 1: prev=25 < 30, now=28 < 30 → False (doesn't cross 30 yet)
        # Bar 2: prev=28 < 30 <= 35 → True
        result = crosses_above_impl({"threshold": 30.0}, ("@rsi",))
        attrs = _attrs("@rsi", [25.0, 28.0, 35.0])
        assert result.fn(attrs, 0) is False
        assert result.fn(attrs, 1) is False
        assert result.fn(attrs, 2) is True

    def test_no_false_trigger_when_already_above(self):
        # Series starts above threshold — no cross
        result = crosses_above_impl({"threshold": 30.0}, ("@rsi",))
        attrs = _attrs("@rsi", [35.0, 37.0, 40.0])
        assert result.fn(attrs, 1) is False
        assert result.fn(attrs, 2) is False

    def test_two_series_crosses_above(self):
        # close crosses above ema: [95, 98, 105] vs ema [100, 100, 100]
        result = crosses_above_impl({}, ("@close", "@ema"))
        attrs = _attrs2("@close", [95.0, 98.0, 105.0], "@ema", [100.0, 100.0, 100.0])
        assert result.fn(attrs, 0) is False
        assert result.fn(attrs, 1) is False   # 98 < 100, still below
        assert result.fn(attrs, 2) is True    # 98 < 100 AND 105 >= 100

    def test_nan_returns_false(self):
        result = crosses_above_impl({"threshold": 30.0}, ("@rsi",))
        attrs = _attrs("@rsi", [25.0, float("nan")])
        assert result.fn(attrs, 1) is False

    def test_guard_i0(self):
        result = crosses_above_impl({"threshold": 30.0}, ("@rsi",))
        attrs = _attrs("@rsi", [35.0])
        assert result.fn(attrs, 0) is False


class TestCrossesBelowImpl:
    def test_fires_on_crossunder_bar(self):
        # Series: [35, 32, 25] with threshold=30
        # Bar 0: i=0 guard → False
        # Bar 1: prev=35 > 30, now=32 > 30 → False
        # Bar 2: prev=32 > 30 >= 25 → True
        result = crosses_below_impl({"threshold": 30.0}, ("@rsi",))
        attrs = _attrs("@rsi", [35.0, 32.0, 25.0])
        assert result.fn(attrs, 0) is False
        assert result.fn(attrs, 1) is False
        assert result.fn(attrs, 2) is True

    def test_two_series_crosses_below(self):
        # close crosses below ema: [105, 102, 95] vs ema [100, 100, 100]
        result = crosses_below_impl({}, ("@close", "@ema"))
        attrs = _attrs2("@close", [105.0, 102.0, 95.0], "@ema", [100.0, 100.0, 100.0])
        assert result.fn(attrs, 0) is False
        assert result.fn(attrs, 1) is False   # 102 > 100, still above
        assert result.fn(attrs, 2) is True    # 102 > 100 AND 95 <= 100

    def test_guard_i0(self):
        result = crosses_below_impl({"threshold": 30.0}, ("@rsi",))
        attrs = _attrs("@rsi", [25.0])
        assert result.fn(attrs, 0) is False


# ===========================================================================
# Logic impls
# ===========================================================================


class TestAndImpl:
    def test_all_true(self):
        result = and_impl({}, ("@a", "@b"))
        attrs = {"@a": pd.Series([True]), "@b": pd.Series([True])}
        assert result.fn(attrs, 0) is True

    def test_one_false(self):
        result = and_impl({}, ("@a", "@b"))
        attrs = {"@a": pd.Series([True]), "@b": pd.Series([False])}
        assert result.fn(attrs, 0) is False

    def test_all_false(self):
        result = and_impl({}, ("@a", "@b"))
        attrs = {"@a": pd.Series([False]), "@b": pd.Series([False])}
        assert result.fn(attrs, 0) is False


class TestOrImpl:
    def test_any_true(self):
        result = or_impl({}, ("@a", "@b"))
        attrs = {"@a": pd.Series([True]), "@b": pd.Series([False])}
        assert result.fn(attrs, 0) is True

    def test_all_false(self):
        result = or_impl({}, ("@a", "@b"))
        attrs = {"@a": pd.Series([False]), "@b": pd.Series([False])}
        assert result.fn(attrs, 0) is False

    def test_all_true(self):
        result = or_impl({}, ("@a", "@b"))
        attrs = {"@a": pd.Series([True]), "@b": pd.Series([True])}
        assert result.fn(attrs, 0) is True


class TestNotImpl:
    def test_inverts_true(self):
        result = not_impl({}, ("@a",))
        attrs = {"@a": pd.Series([True, True])}
        assert result.fn(attrs, 1) is False

    def test_inverts_false(self):
        result = not_impl({}, ("@a",))
        attrs = {"@a": pd.Series([False, False])}
        assert result.fn(attrs, 1) is True

    def test_guard_i0_returns_false(self):
        result = not_impl({}, ("@a",))
        # Even if bar 0 is True, not_impl guard returns False at i=0
        attrs = {"@a": pd.Series([True])}
        assert result.fn(attrs, 0) is False

    def test_no_incoming_raises(self):
        with pytest.raises(ValueError):
            not_impl({}, ())


# ===========================================================================
# Settings impls
# ===========================================================================


class TestPositionSizeImpl:
    def test_default(self):
        r = position_size_impl({})
        assert isinstance(r, SimulatorSettingImplResult)
        assert r.key == "position_size"
        assert r.value == 1.0

    def test_custom(self):
        r = position_size_impl({"size": 0.5})
        assert r.value == 0.5

    def test_zero_raises(self):
        with pytest.raises(ValueError):
            position_size_impl({"size": 0.0})

    def test_above_one_raises(self):
        with pytest.raises(ValueError):
            position_size_impl({"size": 1.5})


class TestStopLossImpl:
    def test_default_pct(self):
        r = stop_loss_impl({"pct": 5.0})
        assert r.key == "stop_loss_pct"
        assert r.value == 5.0

    def test_none_pct(self):
        r = stop_loss_impl({"pct": None})
        assert r.value is None

    def test_missing_pct_is_none(self):
        r = stop_loss_impl({})
        assert r.value is None

    def test_zero_pct_raises(self):
        with pytest.raises(ValueError):
            stop_loss_impl({"pct": 0.0})

    def test_negative_pct_raises(self):
        with pytest.raises(ValueError):
            stop_loss_impl({"pct": -1.0})


class TestSlippageImpl:
    def test_default(self):
        r = slippage_impl({})
        assert r.key == "slippage_bps"
        assert r.value == 2.0

    def test_zero_allowed(self):
        r = slippage_impl({"bps": 0.0})
        assert r.value == 0.0

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            slippage_impl({"bps": -1.0})


class TestCommissionImpl:
    def test_composite_structure(self):
        r = commission_impl({"per_share_rate": 0.0035, "min_per_order": 0.35})
        assert r.key == "commission"
        assert isinstance(r.value, dict)
        assert r.value["per_share_rate"] == pytest.approx(0.0035)
        assert r.value["min_per_order"] == pytest.approx(0.35)

    def test_defaults_are_free(self):
        r = commission_impl({})
        assert r.value["per_share_rate"] == 0.0
        assert r.value["min_per_order"] == 0.0

    def test_negative_per_share_raises(self):
        with pytest.raises(ValueError):
            commission_impl({"per_share_rate": -0.01})


# ===========================================================================
# Registry coverage test
# ===========================================================================


class TestNodeImpsRegistryCoversCatalog:
    def test_registry_covers_compile_active_non_terminal_entries(self):
        """Every compile-active entry that isn't a terminal (output/ticker) must be in NODE_IMPLS."""
        skip_cats = {"output", "ticker"}
        missing = []
        for entry in NODE_CATALOG:
            if entry.compile_active and entry.cat not in skip_cats:
                if entry.name not in NODE_IMPLS:
                    missing.append(entry.name)
        assert missing == [], f"Missing NODE_IMPLS entries: {missing}"


# ===========================================================================
# Integration smoke — RSI < 30 fires exactly once on synthetic data
# ===========================================================================


class TestBelowIntegrationSmoke:
    def test_below_fires_exactly_once(self):
        """Build a 50-bar synthetic series where one bar drops below 30.

        The below_impl fn is checked on every bar; exactly bar 25 should fire.
        """
        # Construct a series: 50..31 (above 30), bar 25 drops to 25, then back up
        values = list(range(50, 30, -1))  # bars 0..19: 50, 49, ..., 31
        values += [25.0]                  # bar 20: below 30
        values += list(range(32, 62))     # bars 21..50: 32..61 (above 30)
        values = values[:50]              # trim to 50 bars

        attrs = _attrs("@rsi", values)
        result = below_impl({"threshold": 30.0}, ("@rsi",))

        fired_bars = [i for i in range(50) if result.fn(attrs, i)]
        assert fired_bars == [20], f"Expected [20], got {fired_bars}"

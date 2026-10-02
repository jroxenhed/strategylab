"""Tests for nodebuilder/sim_settings.py, the shared Settings-node mapping
used by the graph backtest now and the bot runner next (F435 W0)."""
from __future__ import annotations

import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.evaluator import SimulatorSetting
from nodebuilder.models import Graph
from nodebuilder.run import _apply_settings_overrides
from nodebuilder.sim_settings import (
    DIRECTION_SHADOWS,
    GRAPH_OWNED_FIELDS,
    SETTING_FIELDS,
    settings_overrides,
)


def _s(key, value):
    return SimulatorSetting(key=key, value=value)


def test_every_compile_key_maps_to_a_field():
    out = settings_overrides([
        _s("position_size", 0.5), _s("stop_loss", 3), _s("slippage_bps", 4),
        _s("per_share_rate", 0.0035), _s("min_per_order", 0.35),
    ])
    assert out == {
        "position_size": 0.5, "stop_loss_pct": 3.0, "slippage_bps": 4.0,
        "per_share_rate": 0.0035, "min_per_order": 0.35,
    }
    assert all(isinstance(v, float) for v in out.values())


def test_later_setting_wins_and_unknown_keys_are_ignored():
    out = settings_overrides([_s("stop_loss", 2), _s("mystery", 9), _s("stop_loss", 7)])
    assert out == {"stop_loss_pct": 7.0}


def test_constants_are_consistent():
    assert GRAPH_OWNED_FIELDS == frozenset(SETTING_FIELDS.values())
    assert set(DIRECTION_SHADOWS) <= GRAPH_OWNED_FIELDS


def test_backtest_overrides_unchanged_graph_wins():
    """Same result as the old in-route mapping: request values, then graph."""
    req = GraphBacktestRequest(
        graph=Graph(), ticker="X", start="2020-01-01", end="2021-01-01",
        position_size=1.0, stop_loss_pct=10.0, slippage_bps=2.0, direction="short",
    )
    out = _apply_settings_overrides(req, [_s("stop_loss", 5.0), _s("position_size", 0.25)])
    assert out["stop_loss_pct"] == 5.0
    assert out["position_size"] == 0.25
    assert out["slippage_bps"] == 2.0          # not set by the graph: request value
    assert out["direction"] == "short"
    assert out["per_share_rate"] == 0.0
    no_graph = _apply_settings_overrides(req, [])
    assert no_graph["stop_loss_pct"] == 10.0


def test_trailing_stop_passes_through_as_config():
    from models import TrailingStopConfig
    from nodebuilder.sim_settings import OBJECT_FIELDS

    cfg = TrailingStopConfig(type="atr", value=2.0)
    out = settings_overrides([_s("trailing_stop", cfg), _s("stop_loss", 4)])
    assert out["trailing_stop"] is cfg
    assert out["stop_loss_pct"] == 4.0
    assert "trailing_stop" in OBJECT_FIELDS
    # A bot overlay must also set or clear the per-direction trailing stops.
    assert DIRECTION_SHADOWS["trailing_stop"] == ("long_trailing_stop", "short_trailing_stop")

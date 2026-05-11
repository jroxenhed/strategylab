"""Tests for `BotState.append_slippage_bps` (F56/F119) and
`BotState.append_equity_snapshot` (F58/F122) — pins the cap-at-N behaviour
and the rounding/shape contract added when the helpers were extracted.

These were filed as F121 (slippage cap) and F123 (equity snapshot cap)
after the F119 / F122 bundles routed call sites through the helpers without
direct unit coverage. Cap regression would silently let `BotState` grow
unbounded, which is exactly the bug the helpers were created to prevent.
"""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

from datetime import datetime

from bot_manager import BotState


def test_append_slippage_bps_caps_at_1000():
    """F121: 1001 appends → length 1000, first sample dropped, last preserved."""
    state = BotState()
    for i in range(1001):
        state.append_slippage_bps(float(i))
    assert len(state.slippage_bps) == 1000
    # The oldest entry (0.0) was evicted; the newest (1000.0) is the tail.
    assert state.slippage_bps[-1] == 1000.0
    # The first surviving sample is the second one ever appended (1.0 → 1.00).
    assert state.slippage_bps[0] == 1.0


def test_append_slippage_bps_rounds_to_two_decimals():
    """F121: helper rounds to 2 dp so JSON payloads stay compact."""
    state = BotState()
    state.append_slippage_bps(3.14159)
    state.append_slippage_bps(2.71828)
    assert state.slippage_bps == [3.14, 2.72]


def test_append_slippage_bps_below_cap_is_passthrough():
    """Sanity: append until the cap-1 boundary, all entries preserved in order."""
    state = BotState()
    for i in range(500):
        state.append_slippage_bps(float(i))
    assert len(state.slippage_bps) == 500
    assert state.slippage_bps[0] == 0.0
    assert state.slippage_bps[-1] == 499.0


def test_append_equity_snapshot_caps_at_500():
    """F123: 501 appends → length 500, first dropped, last preserved."""
    state = BotState()
    for i in range(501):
        state.append_equity_snapshot(float(i))
    assert len(state.equity_snapshots) == 500
    assert state.equity_snapshots[-1]["value"] == 500.0
    # First-survivor is the second append (value=1.0).
    assert state.equity_snapshots[0]["value"] == 1.0


def test_append_equity_snapshot_shape_and_rounding():
    """F123: each snapshot is `{"time": ISO-8601 UTC, "value": float rounded to 2 dp}`."""
    state = BotState()
    state.append_equity_snapshot(1234.5678)
    snap = state.equity_snapshots[-1]
    assert snap.keys() == {"time", "value"}
    assert snap["value"] == 1234.57
    # ISO-8601 with UTC offset; datetime.fromisoformat parses both ±HH:MM and "+00:00"
    parsed = datetime.fromisoformat(snap["time"])
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == 0.0


def test_append_equity_snapshot_below_cap_preserves_order():
    """Sanity: appends below the cap retain their full history in chronological order."""
    state = BotState()
    for i in range(250):
        state.append_equity_snapshot(float(i))
    assert len(state.equity_snapshots) == 250
    assert [s["value"] for s in state.equity_snapshots[:3]] == [0.0, 1.0, 2.0]
    assert state.equity_snapshots[-1]["value"] == 249.0

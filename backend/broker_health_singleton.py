"""Module-level access to the running HeartbeatMonitor.

The monitor is constructed in FastAPI lifespan (after providers register) and
stashed here so routes can read health without import-order gymnastics.
"""

from __future__ import annotations
from broker_health import HeartbeatMonitor

_monitor: HeartbeatMonitor | None = None


def set_monitor(monitor: HeartbeatMonitor) -> None:
    global _monitor
    _monitor = monitor


def get_monitor() -> HeartbeatMonitor | None:
    return _monitor

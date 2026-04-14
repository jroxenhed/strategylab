"""Multi-broker request aggregation.

Calls a named method (e.g. `get_positions`) on every live broker in parallel
via `asyncio.to_thread` — provider methods are sync today. Unhealthy brokers
(per HeartbeatMonitor) are skipped and listed in `stale_brokers`. Exceptions
from live brokers are caught and also listed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from broker_health_singleton import get_monitor

log = logging.getLogger(__name__)


def _filter_registry(registry: dict[str, Any], broker_filter: str) -> dict[str, Any]:
    if broker_filter == "all":
        return dict(registry)
    if broker_filter in registry:
        return {broker_filter: registry[broker_filter]}
    return {}


async def aggregate_from_brokers(
    registry: dict[str, Any],
    method_name: str,
    broker_filter: str,
    **kwargs,
) -> dict:
    selected = _filter_registry(registry, broker_filter)
    monitor = get_monitor()

    live: list[tuple[str, Any]] = []
    stale: list[str] = []
    for name, provider in selected.items():
        if monitor is not None and not monitor.is_healthy(name):
            stale.append(name)
            continue
        live.append((name, provider))

    async def _call(name: str, provider: Any):
        fn = getattr(provider, method_name)
        return await asyncio.to_thread(fn, **kwargs)

    results = await asyncio.gather(
        *[_call(n, p) for n, p in live],
        return_exceptions=True,
    )

    rows: list[dict] = []
    for (name, _), result in zip(live, results):
        if isinstance(result, Exception):
            log.warning("[aggregate] %s.%s raised: %s", name, method_name, result)
            stale.append(name)
            continue
        for row in result or []:
            rows.append({**row, "broker": name})

    return {"rows": rows, "stale_brokers": stale}

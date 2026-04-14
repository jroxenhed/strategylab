"""Broker heartbeat monitor.

Pings providers that implement `async def ping()` on a fixed interval and
caches per-broker health. Only brokers that need liveness signal beyond a
per-request retry (IBKR: stateful TCP socket into Gateway) implement `ping`.
Alpaca/Yahoo are stateless HTTPS; their absence here is intentional.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

log = logging.getLogger(__name__)


class HeartbeatMonitor:
    def __init__(self, registry: dict[str, Any], interval: float = 30.0,
                 timeout: float = 5.0, warmup: float = 10.0):
        self._registry = registry
        self._interval = interval
        self._timeout = timeout
        self._warmup = warmup
        self._health: dict[str, dict] = {}
        self._task: asyncio.Task | None = None
        self._started_ts: float | None = None

    def get_health(self, name: str) -> dict | None:
        return self._health.get(name)

    def all_health(self) -> dict[str, dict]:
        return dict(self._health)

    def is_healthy(self, name: str) -> bool:
        """Unknown brokers (no ping) are treated as healthy — Alpaca path."""
        h = self._health.get(name)
        if h is None:
            return True
        return bool(h.get("healthy"))

    def is_warming_up(self) -> bool:
        if self._started_ts is None:
            return False
        return (time.time() - self._started_ts) < self._warmup

    async def _tick(self) -> None:
        for name, provider in list(self._registry.items()):
            ping = getattr(provider, "ping", None)
            if ping is None:
                continue
            err: Exception | None = None
            try:
                await asyncio.wait_for(ping(), timeout=self._timeout)
            except Exception as e:
                err = e
                reconnect = getattr(provider, "reconnect", None)
                if reconnect is not None:
                    try:
                        await asyncio.wait_for(reconnect(), timeout=self._timeout)
                        await asyncio.wait_for(ping(), timeout=self._timeout)
                        err = None
                    except Exception as e2:
                        err = e2
            if err is None:
                self._health[name] = {
                    "healthy": True,
                    "last_ok_ts": int(time.time()),
                    "last_error": None,
                }
            else:
                prev = self._health.get(name, {})
                self._health[name] = {
                    "healthy": False,
                    "last_ok_ts": prev.get("last_ok_ts"),
                    "last_error": f"{type(err).__name__}: {err}",
                }

    async def _run(self) -> None:
        self._started_ts = time.time()
        await asyncio.sleep(self._warmup)
        while True:
            try:
                await self._tick()
            except Exception:
                log.exception("[heartbeat] tick failed; continuing")
            await asyncio.sleep(self._interval)

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="broker-heartbeat")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None

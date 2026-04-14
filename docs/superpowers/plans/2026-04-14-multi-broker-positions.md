# Multi-Broker Positions & Broker Heartbeat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship D6 (IBKR heartbeat monitor exposed on `/api/broker`) and D7 (union positions/orders/journal across all registered brokers) — D6 first, D7 consumes it.

**Architecture:** A `HeartbeatMonitor` singleton runs an asyncio task every 30s that pings providers implementing an optional `ping()` method (IBKR only), caching `{healthy, last_ok_ts, last_error}` per broker. Request-path aggregation helper gathers positions/orders from every registered provider concurrently via `asyncio.to_thread` (existing methods are sync), skipping brokers marked unhealthy, tagging each row with its source, and returning a `stale_brokers` list the UI surfaces as a banner.

**Tech Stack:** Python 3.12, FastAPI, asyncio, ib_insync (IBKR), pytest for backend. Frontend: React + TypeScript + TanStack Query.

Spec: `docs/superpowers/specs/2026-04-14-multi-broker-positions-design.md` (approved).

**PR split:**
- **PR 1 — Phase 1 (D6):** Tasks 1–5. Heartbeat monitor + `/api/broker` health surface + IBKR badge dot. Ships standalone.
- **PR 2 — Phase 2 (D7):** Tasks 6–11. Aggregation helper + multi-broker positions/orders/journal + Broker column/filter + stale banner.

---

## Phase 1 — D6: IBKR Heartbeat

### Task 1: `HeartbeatMonitor` core (module + happy-path test)

**Files:**
- Create: `backend/broker_health.py`
- Create: `backend/tests/test_broker_health.py`

- [ ] **Step 1: Write the failing test — happy path**

Create `backend/tests/test_broker_health.py`:

```python
import asyncio
import pytest
from broker_health import HeartbeatMonitor


class FakeProvider:
    def __init__(self, name="fake"):
        self.name = name
        self.calls = 0
    async def ping(self):
        self.calls += 1


@pytest.mark.asyncio
async def test_tick_marks_healthy_on_success():
    p = FakeProvider()
    mon = HeartbeatMonitor(registry={"fake": p}, interval=0.01, timeout=1.0)
    await mon._tick()
    h = mon.get_health("fake")
    assert h["healthy"] is True
    assert h["last_ok_ts"] is not None
    assert h["last_error"] is None
    assert p.calls == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_broker_health.py::test_tick_marks_healthy_on_success -v`
Expected: FAIL — `ModuleNotFoundError: broker_health`.

- [ ] **Step 3: Minimal `HeartbeatMonitor` implementation**

Create `backend/broker_health.py`:

```python
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
            try:
                await asyncio.wait_for(ping(), timeout=self._timeout)
                self._health[name] = {
                    "healthy": True,
                    "last_ok_ts": int(time.time()),
                    "last_error": None,
                }
            except Exception as e:
                prev = self._health.get(name, {})
                self._health[name] = {
                    "healthy": False,
                    "last_ok_ts": prev.get("last_ok_ts"),
                    "last_error": f"{type(e).__name__}: {e}",
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
```

- [ ] **Step 4: Add pytest-asyncio to test config if not present**

Check: `grep -n "asyncio_mode\|pytest-asyncio" backend/pytest.ini backend/pyproject.toml 2>/dev/null || true`

If there's no asyncio mode configured, add to `backend/pytest.ini` (create if missing):

```ini
[pytest]
asyncio_mode = auto
```

If `pytest-asyncio` is not installed: `pip install pytest-asyncio`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_broker_health.py::test_tick_marks_healthy_on_success -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/broker_health.py backend/tests/test_broker_health.py backend/pytest.ini
git commit -m "feat(broker): add HeartbeatMonitor with happy-path tick"
```

---

### Task 2: Heartbeat failure paths (timeout, raise, task-survives)

**Files:**
- Modify: `backend/tests/test_broker_health.py`

- [ ] **Step 1: Add failing tests for timeout, raise, survive-exception, and lifecycle**

Append to `backend/tests/test_broker_health.py`:

```python
class TimeoutProvider:
    name = "slow"
    async def ping(self):
        await asyncio.sleep(5.0)


class RaisingProvider:
    name = "bad"
    async def ping(self):
        raise RuntimeError("socket dead")


class NoPingProvider:
    name = "alpaca"
    # no ping() — should be skipped silently


@pytest.mark.asyncio
async def test_tick_marks_unhealthy_on_timeout():
    p = TimeoutProvider()
    mon = HeartbeatMonitor(registry={"slow": p}, interval=0.01, timeout=0.05)
    await mon._tick()
    h = mon.get_health("slow")
    assert h["healthy"] is False
    assert "TimeoutError" in h["last_error"]
    assert h["last_ok_ts"] is None


@pytest.mark.asyncio
async def test_tick_preserves_last_ok_ts_on_failure():
    class Flaky:
        name = "flaky"
        def __init__(self):
            self.fail = False
        async def ping(self):
            if self.fail:
                raise RuntimeError("boom")

    p = Flaky()
    mon = HeartbeatMonitor(registry={"flaky": p}, interval=0.01, timeout=1.0)
    await mon._tick()
    first_ok = mon.get_health("flaky")["last_ok_ts"]
    assert first_ok is not None
    p.fail = True
    await mon._tick()
    h = mon.get_health("flaky")
    assert h["healthy"] is False
    assert h["last_ok_ts"] == first_ok  # preserved from previous success


@pytest.mark.asyncio
async def test_tick_records_exception_details():
    p = RaisingProvider()
    mon = HeartbeatMonitor(registry={"bad": p}, interval=0.01, timeout=1.0)
    await mon._tick()
    h = mon.get_health("bad")
    assert h["healthy"] is False
    assert "socket dead" in h["last_error"]


@pytest.mark.asyncio
async def test_tick_skips_providers_without_ping():
    p = NoPingProvider()
    mon = HeartbeatMonitor(registry={"alpaca": p}, interval=0.01, timeout=1.0)
    await mon._tick()
    assert mon.get_health("alpaca") is None
    # is_healthy treats unknown as healthy (Alpaca path)
    assert mon.is_healthy("alpaca") is True


@pytest.mark.asyncio
async def test_run_loop_survives_tick_exception(monkeypatch):
    mon = HeartbeatMonitor(registry={}, interval=0.01, timeout=1.0, warmup=0.0)
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tick exploded")

    monkeypatch.setattr(mon, "_tick", boom)
    mon.start()
    await asyncio.sleep(0.05)
    await mon.stop()
    assert calls["n"] >= 2  # loop survived the first exception
```

- [ ] **Step 2: Run — expect PASS (implementation already covers these)**

Run: `cd backend && pytest tests/test_broker_health.py -v`
Expected: all 5 new tests PASS.

If any fail, fix `broker_health.py` — do not weaken tests.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_broker_health.py
git commit -m "test(broker): cover heartbeat failure modes and loop resilience"
```

---

### Task 3: Add `ping()` to `IBKRTradingProvider`; wire monitor into FastAPI lifespan

**Files:**
- Modify: `backend/broker.py` (add `ping` method + `name` attribute on `IBKRTradingProvider`)
- Modify: `backend/main.py` (construct + start/stop monitor in lifespan)
- Create: `backend/broker_health_singleton.py` (module-level getter so routes can access it)

- [ ] **Step 1: Add a `name` attribute and `ping()` to IBKR provider**

Edit `backend/broker.py` — inside `class IBKRTradingProvider:` (around line 249), add after `__init__`:

```python
    name = "ibkr"

    async def ping(self) -> None:
        """Liveness probe for HeartbeatMonitor. Runs on the provider's loop.

        Must not trigger reconnect logic — a failed ping is the signal the
        monitor uses to mark us unhealthy. Reconnect is the request-path's
        responsibility via `_ensure_connected`.
        """
        fut = asyncio.run_coroutine_threadsafe(
            self._ib.reqCurrentTimeAsync(), self._loop
        )
        # Await the concurrent future without blocking the monitor's loop.
        await asyncio.wrap_future(fut)
```

Add `import asyncio` at the top of `backend/broker.py` if not already imported.

Also set `name = "alpaca"` on `AlpacaTradingProvider` (class-level attr, for the aggregation helper in Phase 2). It will not grow a `ping()`.

- [ ] **Step 2: Create singleton accessor**

Create `backend/broker_health_singleton.py`:

```python
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
```

- [ ] **Step 3: Wire monitor into lifespan**

Edit `backend/main.py` — replace the existing `lifespan` function with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    from shared import init_ibkr
    from broker import _trading_providers
    from broker_health import HeartbeatMonitor
    from broker_health_singleton import set_monitor

    await init_ibkr()

    monitor = HeartbeatMonitor(registry=_trading_providers)
    set_monitor(monitor)
    monitor.start()

    manager = BotManager()
    manager.load()
    bots_module.bot_manager = manager
    try:
        yield
    finally:
        await manager.shutdown()
        await monitor.stop()
```

Import note: `_trading_providers` is the live dict in `broker.py`; the monitor holds the reference so providers registered after construction are still picked up.

- [ ] **Step 4: Sanity check backend boots**

Run: `cd backend && python -c "from main import app; print('ok')"`
Expected: `ok`, no import errors.

- [ ] **Step 5: Commit**

```bash
git add backend/broker.py backend/broker_health_singleton.py backend/main.py
git commit -m "feat(broker): IBKR ping() + start heartbeat monitor in lifespan"
```

---

### Task 4: Expose health on `GET /api/broker`

**Files:**
- Modify: `backend/routes/providers.py`
- Create test: `backend/tests/test_broker_route_health.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_broker_route_health.py`:

```python
import pytest
from fastapi.testclient import TestClient
from broker_health import HeartbeatMonitor
import broker_health_singleton
from broker import register_trading_provider, _trading_providers


class StubIBKR:
    name = "ibkr"
    async def ping(self):
        pass
    # minimal surface so registration doesn't choke
    def get_account(self): return {}
    def get_positions(self): return []
    def get_orders(self, **kw): return []
    def submit_order(self, o): ...
    def get_order(self, i): ...
    def cancel_order(self, i): ...
    def close_position(self, s): ...
    def close_all_positions(self): ...
    def cancel_all_orders(self): ...
    def get_latest_price(self, s): return 0.0


@pytest.mark.asyncio
async def test_broker_route_exposes_health():
    _trading_providers.clear()
    register_trading_provider("ibkr", StubIBKR())
    mon = HeartbeatMonitor(registry=_trading_providers, warmup=0.0)
    await mon._tick()
    broker_health_singleton.set_monitor(mon)

    from main import app
    client = TestClient(app)
    r = client.get("/api/broker")
    assert r.status_code == 200
    body = r.json()
    assert "health" in body
    assert body["health"]["ibkr"]["healthy"] is True
    assert "heartbeat_warmup" in body
```

Run: `cd backend && pytest tests/test_broker_route_health.py -v`
Expected: FAIL — `health` key missing.

- [ ] **Step 2: Extend `GET /api/broker`**

Replace `backend/routes/providers.py` contents:

```python
from fastapi import APIRouter
from pydantic import BaseModel
from shared import get_available_providers
from broker import get_available_brokers, get_active_broker, set_active_broker
from broker_health_singleton import get_monitor

router = APIRouter()


@router.get("/api/providers")
def list_providers():
    return {"providers": get_available_providers()}


def _broker_payload():
    monitor = get_monitor()
    health = monitor.all_health() if monitor else {}
    warmup = monitor.is_warming_up() if monitor else False
    return {
        "active": get_active_broker(),
        "available": get_available_brokers(),
        "health": health,
        "heartbeat_warmup": warmup,
    }


@router.get("/api/broker")
def get_broker():
    return _broker_payload()


class SetBrokerRequest(BaseModel):
    broker: str


@router.put("/api/broker")
def set_broker(req: SetBrokerRequest):
    set_active_broker(req.broker)
    return _broker_payload()
```

- [ ] **Step 3: Run test — expect PASS**

Run: `cd backend && pytest tests/test_broker_route_health.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/routes/providers.py backend/tests/test_broker_route_health.py
git commit -m "feat(api): expose broker health + warmup on /api/broker"
```

---

### Task 5: Frontend — surface health dot on the IBKR badge

**Files:**
- Modify: `frontend/src/api/trading.ts` (extend `BrokerInfo`)
- Modify: `frontend/src/shared/hooks/useOHLCV.ts` (extend `useBroker` return)
- Create: `frontend/src/features/trading/BrokerTag.tsx`
- Modify: wherever the "via IBKR" badge is currently rendered (search below)

- [ ] **Step 1: Find the current broker badge**

Run: `grep -rn "via IBKR\|via Alpaca\|broker.*badge\|active.*broker" frontend/src/features/trading`

Record the file + line where the badge is rendered; that's the insertion point.

- [ ] **Step 2: Extend the BrokerInfo type**

Edit `frontend/src/api/trading.ts` — replace the `BrokerInfo` interface at lines 189–192:

```ts
export interface BrokerHealth {
  healthy: boolean
  last_ok_ts: number | null
  last_error: string | null
}

export interface BrokerInfo {
  active: string
  available: string[]
  health: Record<string, BrokerHealth>
  heartbeat_warmup: boolean
}
```

- [ ] **Step 3: Extend `useBroker` return**

Edit `frontend/src/shared/hooks/useOHLCV.ts` — in the `useBroker` function (lines 66–90), change the returned object to include `health` and `heartbeatWarmup`:

```ts
  return {
    broker: query.data?.active ?? 'alpaca',
    available: query.data?.available ?? [],
    health: query.data?.health ?? {},
    heartbeatWarmup: query.data?.heartbeat_warmup ?? false,
    isLoading: query.isLoading,
    switchBroker,
  }
```

Also reduce `staleTime` to `10_000` so the dot reflects heartbeat state within ~10s.

- [ ] **Step 4: Create `BrokerTag` component**

Create `frontend/src/features/trading/BrokerTag.tsx`:

```tsx
import type { BrokerHealth } from '../../api/trading'

interface Props {
  name: string
  health?: BrokerHealth
  warmingUp?: boolean
}

const COLORS: Record<string, string> = {
  alpaca: '#3b82f6',
  ibkr: '#f97316',
}

export function BrokerTag({ name, health, warmingUp }: Props) {
  const color = COLORS[name] ?? '#6b7280'
  const hasHealth = !!health
  const healthy = health?.healthy === true
  const dotColor = warmingUp ? '#9ca3af' : healthy ? '#10b981' : '#ef4444'
  const title = warmingUp
    ? 'Broker heartbeat warming up…'
    : hasHealth
      ? healthy
        ? `OK — last ping ${health!.last_ok_ts ?? '—'}`
        : `Disconnected — ${health!.last_error ?? 'unknown error'}`
      : undefined

  return (
    <span
      title={title}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '2px 8px',
        borderRadius: 999,
        background: `${color}22`,
        color,
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      {hasHealth && (
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: dotColor,
          }}
        />
      )}
      {name.toUpperCase()}
    </span>
  )
}
```

- [ ] **Step 5: Use `BrokerTag` at the existing IBKR badge location**

At the file/line located in Step 1, replace the current inline text (e.g. `via IBKR`) with:

```tsx
<BrokerTag name={broker} health={health[broker]} warmingUp={heartbeatWarmup} />
```

`broker`, `health`, `heartbeatWarmup` come from the extended `useBroker()` hook.

- [ ] **Step 6: Manual smoke test**

Run `./start.sh`. Load app. Check:
- With IBKR registered + healthy: dot is green.
- Stop IB Gateway. Wait ~60s. Dot turns red; hover shows last error.
- On cold start: dot is grey for ~10s (warmup), then settles.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/trading.ts frontend/src/shared/hooks/useOHLCV.ts \
        frontend/src/features/trading/BrokerTag.tsx \
        <the file modified in Step 5>
git commit -m "feat(ui): IBKR badge health dot driven by heartbeat"
```

---

### Phase 1 wrap — open PR 1

- [ ] Run `pytest backend/tests` — all green.
- [ ] Open PR titled `feat(broker): D6 — IBKR heartbeat monitor` pointing at `main`. Body references the spec. Merge before starting Phase 2.

---

## Phase 2 — D7: Multi-Broker Aggregation

### Task 6: `AlpacaTradingProvider.name` + aggregation helper (backend/broker.py + new helper module)

**Files:**
- Modify: `backend/broker.py` (ensure `AlpacaTradingProvider.name = "alpaca"` — done in Task 3; add here if skipped)
- Create: `backend/broker_aggregate.py`
- Create: `backend/tests/test_positions_aggregation.py`

- [ ] **Step 1: Write failing tests for the aggregation helper**

Create `backend/tests/test_positions_aggregation.py`:

```python
import pytest
from broker_aggregate import aggregate_from_brokers
from broker_health import HeartbeatMonitor
import broker_health_singleton


class FakeBroker:
    def __init__(self, name, positions=None, raises=False):
        self.name = name
        self._positions = positions or []
        self._raises = raises
    def get_positions(self):
        if self._raises:
            raise RuntimeError("blew up")
        return self._positions


@pytest.mark.asyncio
async def test_all_healthy_returns_union_with_broker_tag():
    registry = {
        "alpaca": FakeBroker("alpaca", [{"symbol": "AAPL", "qty": 10}]),
        "ibkr":   FakeBroker("ibkr",   [{"symbol": "MSFT", "qty": 5}]),
    }
    mon = HeartbeatMonitor(registry=registry, warmup=0.0)
    mon._health["ibkr"] = {"healthy": True, "last_ok_ts": 1, "last_error": None}
    broker_health_singleton.set_monitor(mon)

    result = await aggregate_from_brokers(registry, "get_positions", "all")
    brokers_seen = {row["broker"] for row in result["rows"]}
    assert brokers_seen == {"alpaca", "ibkr"}
    assert result["stale_brokers"] == []


@pytest.mark.asyncio
async def test_unhealthy_broker_skipped_and_listed_stale():
    registry = {
        "alpaca": FakeBroker("alpaca", [{"symbol": "AAPL", "qty": 10}]),
        "ibkr":   FakeBroker("ibkr",   [{"symbol": "MSFT", "qty": 5}]),
    }
    mon = HeartbeatMonitor(registry=registry, warmup=0.0)
    mon._health["ibkr"] = {"healthy": False, "last_ok_ts": None, "last_error": "dead"}
    broker_health_singleton.set_monitor(mon)

    result = await aggregate_from_brokers(registry, "get_positions", "all")
    assert all(row["broker"] == "alpaca" for row in result["rows"])
    assert result["stale_brokers"] == ["ibkr"]


@pytest.mark.asyncio
async def test_raising_broker_caught_and_listed_stale():
    registry = {
        "alpaca": FakeBroker("alpaca", [{"symbol": "AAPL", "qty": 10}]),
        "ibkr":   FakeBroker("ibkr",   raises=True),
    }
    mon = HeartbeatMonitor(registry=registry, warmup=0.0)
    mon._health["ibkr"] = {"healthy": True, "last_ok_ts": 1, "last_error": None}
    broker_health_singleton.set_monitor(mon)

    result = await aggregate_from_brokers(registry, "get_positions", "all")
    assert all(row["broker"] == "alpaca" for row in result["rows"])
    assert "ibkr" in result["stale_brokers"]


@pytest.mark.asyncio
async def test_specific_broker_filter_returns_only_that_broker():
    registry = {
        "alpaca": FakeBroker("alpaca", [{"symbol": "AAPL", "qty": 10}]),
        "ibkr":   FakeBroker("ibkr",   [{"symbol": "MSFT", "qty": 5}]),
    }
    mon = HeartbeatMonitor(registry=registry, warmup=0.0)
    mon._health["ibkr"] = {"healthy": True, "last_ok_ts": 1, "last_error": None}
    broker_health_singleton.set_monitor(mon)

    result = await aggregate_from_brokers(registry, "get_positions", "ibkr")
    assert all(row["broker"] == "ibkr" for row in result["rows"])
    assert result["stale_brokers"] == []
```

Run: `cd backend && pytest tests/test_positions_aggregation.py -v`
Expected: FAIL — `broker_aggregate` missing.

- [ ] **Step 2: Implement `aggregate_from_brokers`**

Create `backend/broker_aggregate.py`:

```python
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
```

- [ ] **Step 3: Run tests — expect PASS**

Run: `cd backend && pytest tests/test_positions_aggregation.py -v`
Expected: all 4 PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/broker_aggregate.py backend/tests/test_positions_aggregation.py
git commit -m "feat(broker): aggregate_from_brokers helper with health gating"
```

---

### Task 7: Wire `/api/trading/positions` and `/api/trading/orders` through aggregation

**Files:**
- Modify: `backend/routes/trading.py` (replace `get_positions` and `get_orders` handlers)

- [ ] **Step 1: Replace the two route handlers**

Edit `backend/routes/trading.py` — replace the current `get_positions` (lines 59–67) and `get_orders` (lines 70–78) with:

```python
from broker import _trading_providers
from broker_aggregate import aggregate_from_brokers


@router.get("/positions")
async def get_positions(broker: str = "all"):
    result = await aggregate_from_brokers(_trading_providers, "get_positions", broker)
    return {"positions": result["rows"], "stale_brokers": result["stale_brokers"]}


@router.get("/orders")
async def get_orders(broker: str = "all"):
    result = await aggregate_from_brokers(_trading_providers, "get_orders", broker)
    return {"orders": result["rows"], "stale_brokers": result["stale_brokers"]}
```

Note the response shape change: was a bare list, is now `{positions: [...], stale_brokers: [...]}`. Frontend updates in Task 10 handle this — do not ship this task alone.

- [ ] **Step 2: Smoke test the backend**

Run backend (`./start.sh`) and `curl 'http://localhost:8000/api/trading/positions?broker=all'`.
Expected: `{"positions": [...], "stale_brokers": []}` with each row carrying a `broker` field.

- [ ] **Step 3: Commit**

```bash
git add backend/routes/trading.py
git commit -m "feat(api): aggregate positions + orders across brokers"
```

---

### Task 8: Journal — stamp `broker` on new rows, filter on read

**Files:**
- Modify: `backend/journal.py` (extend `_log_trade` signature; extend `compute_realized_pnl`? No — keep scoped by bot_id only)
- Modify: `backend/bot_runner.py` (pass `broker=cfg.broker` into `_log_trade`)
- Modify: `backend/routes/trading.py` (`/journal` accepts `broker=all|alpaca|ibkr`)
- Create: `backend/tests/test_journal_broker_filter.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_journal_broker_filter.py`:

```python
import json
import pytest
from pathlib import Path
from journal import _log_trade, JOURNAL_PATH


@pytest.fixture(autouse=True)
def clean_journal(tmp_path, monkeypatch):
    fake = tmp_path / "trade_journal.json"
    monkeypatch.setattr("journal.JOURNAL_PATH", fake)
    yield


def test_log_trade_stamps_broker():
    _log_trade("AAPL", "buy", 1, 100.0, source="bot",
               direction="long", bot_id="b1", broker="ibkr")
    from journal import JOURNAL_PATH as p
    rows = json.loads(p.read_text())["trades"]
    assert rows[-1]["broker"] == "ibkr"


def test_log_trade_defaults_broker_to_null_for_manual():
    _log_trade("AAPL", "buy", 1, 100.0, source="manual", direction="long")
    from journal import JOURNAL_PATH as p
    rows = json.loads(p.read_text())["trades"]
    assert rows[-1].get("broker") is None
```

Run: `cd backend && pytest tests/test_journal_broker_filter.py -v`
Expected: FAIL — `_log_trade` does not accept `broker`.

- [ ] **Step 2: Extend `_log_trade`**

Edit `backend/journal.py` — change `_log_trade` signature + dict:

```python
def _log_trade(symbol: str, side: str, qty: float, price: float | None,
               source: str, stop_loss_price: float | None = None,
               reason: str | None = None, expected_price: float | None = None,
               direction: str = "long", bot_id: str | None = None,
               broker: str | None = None):
    """Append a trade entry to the journal.

    `broker` tags which broker executed the fill. Bot trades should always set
    it; manual/legacy trades may be None. Absent rows filter as 'All' only.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if JOURNAL_PATH.exists():
        journal = json.loads(JOURNAL_PATH.read_text())
    else:
        journal = {"trades": []}
    journal["trades"].append({
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "stop_loss_price": stop_loss_price,
        "source": source,
        "reason": reason,
        "expected_price": expected_price,
        "direction": direction,
        "bot_id": bot_id,
        "broker": broker,
    })
    JOURNAL_PATH.write_text(json.dumps(journal, indent=2))
```

- [ ] **Step 3: Run test — expect PASS**

Run: `cd backend && pytest tests/test_journal_broker_filter.py -v`
Expected: both PASS.

- [ ] **Step 4: Pass `broker=cfg.broker` from bot_runner**

Edit `backend/bot_runner.py` at each of the 3 `_log_trade(` call sites (grep output pinpointed lines 200, 306, 451; re-grep to confirm current lines):

For every call, append `broker=cfg.broker` as a kwarg. Example for the line-200 call:

```python
_log_trade(cfg.symbol, "cover" if is_short else "sell", sell_qty, exit_price,
           source="bot", reason=reason, expected_price=expected_price,
           direction=cfg.direction, bot_id=cfg.id, broker=cfg.broker)
```

Use grep to confirm the exact signature each call uses before editing. Do not reorder positional args.

- [ ] **Step 5: Extend `/api/trading/journal` with broker filter**

Edit `backend/routes/trading.py` — replace `get_journal` (lines 399–407):

```python
@router.get("/journal")
def get_journal(symbol: Optional[str] = None, broker: str = "all"):
    if not JOURNAL_PATH.exists():
        return {"trades": []}
    journal = json.loads(JOURNAL_PATH.read_text())
    trades = journal.get("trades", [])
    if symbol:
        trades = [t for t in trades if t["symbol"].upper() == symbol.upper()]
    if broker != "all":
        trades = [t for t in trades if t.get("broker") == broker]
    return {"trades": trades}
```

Mixed-tag behavior: "All" returns everything (including null-broker legacy rows); any specific filter excludes null. Matches spec.

- [ ] **Step 6: Commit**

```bash
git add backend/journal.py backend/bot_runner.py backend/routes/trading.py \
        backend/tests/test_journal_broker_filter.py
git commit -m "feat(journal): stamp broker on bot trades, filter /journal by broker"
```

---

### Task 9: Frontend types + API callers

**Files:**
- Modify: `frontend/src/api/trading.ts`

- [ ] **Step 1: Add `broker` to `Position`, `Order`, `JournalTrade`**

Edit `frontend/src/api/trading.ts`:

- `Position` interface (lines 17–26): add `broker: string`.
- `Order` interface (lines 28–38): add `broker: string`.
- `JournalTrade` interface (lines 77–88): add `broker: string | null`.

- [ ] **Step 2: Update fetchers for new response shape + broker filter param**

In the same file, replace `fetchPositions`, `fetchOrders`, `fetchJournal`:

```ts
export interface StaleAware<T> {
  rows: T[]
  stale_brokers: string[]
}

export async function fetchPositions(broker: string = 'all'): Promise<StaleAware<Position>> {
  const { data } = await api.get('/api/trading/positions', { params: { broker } })
  return { rows: data.positions ?? [], stale_brokers: data.stale_brokers ?? [] }
}

export async function fetchOrders(broker: string = 'all'): Promise<StaleAware<Order>> {
  const { data } = await api.get('/api/trading/orders', { params: { broker } })
  return { rows: data.orders ?? [], stale_brokers: data.stale_brokers ?? [] }
}

export async function fetchJournal(symbol?: string, broker: string = 'all'): Promise<JournalTrade[]> {
  const params: Record<string, string> = { broker }
  if (symbol) params.symbol = symbol
  const { data } = await api.get('/api/trading/journal', { params })
  return data.trades ?? []
}
```

- [ ] **Step 3: Compile frontend**

Run: `cd frontend && npx tsc --noEmit`
Expected: errors in `PositionsTable.tsx`, `OrderHistory.tsx`, `TradeJournal.tsx` because callers pass 0 args or use the old return shape. Those are wired up in Task 10 — that's the point of this task sequencing.

Do NOT ignore or suppress; the next task fixes them.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/trading.ts
git commit -m "feat(api): multi-broker response shapes and filter params"
```

---

### Task 10: Frontend — Broker column + filter dropdown in each table; hoist `stale_brokers`

**Files:**
- Modify: `frontend/src/features/trading/PositionsTable.tsx`
- Modify: `frontend/src/features/trading/OrderHistory.tsx`
- Modify: `frontend/src/features/trading/TradeJournal.tsx`
- Modify: `frontend/src/features/trading/PaperTrading.tsx`

- [ ] **Step 1: Map the current table shape**

Run, for each: `wc -l frontend/src/features/trading/{PositionsTable,OrderHistory,TradeJournal,PaperTrading}.tsx` then `grep -n "useQuery\|fetchPositions\|fetchOrders\|fetchJournal" frontend/src/features/trading/*.tsx`.

Record: how each table fetches data today (TanStack Query likely), where the header row is, existing filter bar (TradeJournal's reason filter is the template — spec calls this out).

- [ ] **Step 2: PositionsTable — accept `brokerFilter`, render Broker col, call onStale**

In `PositionsTable.tsx`:
- Props: add `brokerFilter: string`, `onBrokerFilterChange: (v: string) => void`, `availableBrokers: string[]`, `onStale: (list: string[]) => void`.
- Change the query to `queryKey: ['positions', brokerFilter]`, `queryFn: () => fetchPositions(brokerFilter)`.
- In `onSuccess` (or `useEffect` on `query.data`), call `onStale(query.data.stale_brokers)`.
- Above the table, render a select:

```tsx
<select value={brokerFilter} onChange={e => onBrokerFilterChange(e.target.value)}>
  <option value="all">All brokers</option>
  {availableBrokers.map(b => <option key={b} value={b}>{b.toUpperCase()}</option>)}
</select>
```

- Add a `Broker` column rendering `<BrokerTag name={row.broker} health={health[row.broker]} warmingUp={heartbeatWarmup} />`. `health` and `heartbeatWarmup` come via props from `PaperTrading`.

- [ ] **Step 3: OrderHistory — same treatment**

Identical pattern; Broker column shows `<BrokerTag .../>`. No health-dot requirement for orders beyond what BrokerTag decides.

- [ ] **Step 4: TradeJournal — same treatment, null-aware rendering**

In the row renderer, when `row.broker == null` render `—` instead of a tag. When the filter selector is not "all", null rows are already excluded server-side — nothing extra to do.

Reuse the existing reason-filter bar pattern (mirror its markup exactly so visual layout stays consistent).

- [ ] **Step 5: PaperTrading — hoist state, render stale banner**

In `PaperTrading.tsx`:
- Add state: `const [brokerFilter, setBrokerFilter] = useLocalStorage('paper.brokerFilter', 'all')` and `const [stale, setStale] = useState<Record<string, string[]>>({positions: [], orders: [], journal: []})`.
- Derive union: `const staleUnion = Array.from(new Set([...stale.positions, ...stale.orders, ...stale.journal]))`.
- Pass `brokerFilter`, `setBrokerFilter`, `availableBrokers` (from `useBroker().available`), `health`, `heartbeatWarmup` into each of the three tables.
- Each table's `onStale` setter: `(list) => setStale(s => ({...s, positions: list}))` (and analogous for orders/journal).
- Above the three tables, render when `staleUnion.length > 0`:

```tsx
{staleUnion.length > 0 && !dismissed && (
  <div role="alert" style={{ padding: 8, background: '#fee2e2', color: '#991b1b', borderRadius: 6, marginBottom: 8 }}>
    Not showing data from: {staleUnion.join(', ').toUpperCase()}
    <button onClick={() => setDismissed(true)} style={{ marginLeft: 8 }}>Dismiss</button>
  </div>
)}
```

`dismissed` is a local `useState<boolean>(false)` that resets when `staleUnion` changes (use a `useEffect` on `staleUnion.join(',')` to reset).

- [ ] **Step 6: Typecheck + smoke test**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean.

Start app (`./start.sh`), verify:
- Both brokers healthy: positions/orders/journal show rows from both, each tagged. Filter dropdown switches to one broker only.
- Stop IB Gateway, wait 60s: banner appears listing `IBKR`; Alpaca rows still render.
- Click dismiss: banner hides. Bring IBKR back up: banner reappears only on next stale event.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/trading/PositionsTable.tsx \
        frontend/src/features/trading/OrderHistory.tsx \
        frontend/src/features/trading/TradeJournal.tsx \
        frontend/src/features/trading/PaperTrading.tsx
git commit -m "feat(ui): multi-broker table view with filter + stale banner"
```

---

### Task 11: TODO cleanup + PR 2

**Files:**
- Modify: `TODO.md`

- [ ] **Step 1: Move D6 and D7 to Shipped**

Edit `TODO.md`:
- Remove the two bullets at lines 66–67.
- Add under `### Shipped`:
  - `- **D6** IBKR heartbeat monitor — shipped YYYY-MM-DD (use actual ship date).`
  - `- **D7** Union positions/orders/journal across brokers with filter + stale banner — shipped YYYY-MM-DD.`

- [ ] **Step 2: Full backend test pass**

Run: `cd backend && pytest -q`
Expected: all green.

- [ ] **Step 3: Commit + open PR 2**

```bash
git add TODO.md
git commit -m "docs(todo): D6+D7 shipped"
```

Open PR titled `feat(broker): D7 — multi-broker positions, orders, journal` pointing at `main`. Body references the spec and links PR 1.

---

## Risks & operational notes

- **`_trading_providers` is a module global.** Tests in Task 6 mutate it directly — pair each with `clear()` in setup to avoid leaking providers across tests. If this becomes noisy, wrap registry access in a small accessor and monkeypatch it in tests.
- **`asyncio.to_thread` vs IBKR's `_run`.** `IBKRTradingProvider.get_positions` internally schedules on the provider's captured loop via `run_coroutine_threadsafe`. Running it from `to_thread` is fine — no loop conflict, because the thread isn't the event loop. Verified against `broker.py:262`.
- **Response shape break for `/api/trading/positions` and `/orders`.** These were bare lists; now objects. Any external consumer (scripts, Postman collections) breaks. Search with `grep -rn "trading/positions\|trading/orders" --include='*.py' --include='*.ts' --include='*.tsx'` before Task 7; update anything found.
- **Journal file is JSON-rewritten on every log.** D5 (future) addresses caching; this plan does not regress that — adding a field is O(size of file) just like today.
- **Heartbeat interval = 30s.** UI dot color lags reality by up to 30s. Acceptable per spec. Do not tighten without also raising concerns about IB Gateway pacing rules.

## Self-review notes

- Spec coverage: heartbeat monitor ✓ (T1–3), `/api/broker` health ✓ (T4), ping on IBKR only ✓ (T3), aggregation helper ✓ (T6), positions/orders routes ✓ (T7), journal stamp + filter ✓ (T8), BrokerTag + filter dropdowns + stale banner ✓ (T5, T10), tests in the three backend files called out in spec §Testing ✓ (T1/2, T6, T8).
- No `TBD`/placeholder steps. All code blocks are complete.
- Type names used in later tasks (`BrokerHealth`, `BrokerInfo.health`, `StaleAware<T>`) are defined in their introducing task and referenced consistently.
- Method names: `aggregate_from_brokers`, `HeartbeatMonitor.is_healthy`, `all_health`, `is_warming_up`, `get_monitor`/`set_monitor` — used identically across tasks.

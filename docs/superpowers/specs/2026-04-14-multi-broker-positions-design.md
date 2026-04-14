# Multi-Broker Positions & Broker Heartbeat — Design

**Date:** 2026-04-14
**TODO items:** D6 (IBKR heartbeat), D7 (union positions across brokers)
**Status:** Approved design, ready for implementation plan

## Overview

Two features shipped together in one spec, two PRs:

1. **D6 — IBKR heartbeat.** A background task pings `reqCurrentTimeAsync` every 30s and caches the result. Exposed via `/api/broker` so the UI can show a live connection dot on the IBKR badge.
2. **D7 — Multi-broker aggregation.** `/api/positions`, `/api/orders`, and `/api/journal` return data from every registered broker, tagged with source. Each UI panel gets a Broker column and a filter dropdown.

D7 depends on D6: without cached health, D7 must either probe every broker on every request (slow when IBKR is wedged — socket timeouts run 10s+) or tolerate those timeouts. With D6, D7's aggregation skips known-dead brokers instantly.

Ship heartbeat first, then aggregation consumes it.

## Architecture

Two independent loops sharing state through a `HeartbeatMonitor` singleton:

- **Heartbeat loop** — started in FastAPI lifespan after the broker registry is populated. Every 30s, iterates providers that implement `ping()`, calls each with a 5s timeout, updates `health[broker_name]`.
- **Request path** — `/api/positions` et al. read `health[]` to decide which brokers to call, `gather` the live ones with `return_exceptions=True`, tag each row with its source broker, return rows + a `stale_brokers` list.

Heartbeat covers **IBKR only**. Alpaca/Yahoo are stateless HTTPS — failures surface per-request and don't benefit from a cached health signal. The `ping()` method is optional on the `TradingProvider` Protocol; only IBKR implements it.

## Components

### Backend — new files

- **`backend/broker_health.py`** — `HeartbeatMonitor` class. Owns the async task, exposes `start()`, `stop()`, `get_health(name)`, and internal `_tick()`. Singleton lives in its own module because its lifecycle (async task management) is distinct from broker-action concerns in `broker.py`.

### Backend — extended files

- **`backend/broker.py`** — `TradingProvider` Protocol gains optional `async def ping() -> None`. Only `IBKRTradingProvider` implements it (calls `reqCurrentTimeAsync` with a timeout). Structural Protocol: Alpaca needs no stub.
- **`backend/routes/providers.py`** — `GET /api/broker` response extended:
  ```json
  {
    "broker_id": "ibkr",
    "health": {
      "ibkr": {
        "healthy": true,
        "last_ok_ts": 1744653600,
        "last_error": null
      }
    },
    "heartbeat_warmup": false
  }
  ```
  Only brokers that implement `ping()` appear under `health`.
- **`backend/routes/trading.py`** — `/api/positions` and `/api/orders` accept `?broker=all|alpaca|ibkr` (default `all`). Shared helper:
  ```python
  async def aggregate_from_brokers(method_name: str, broker_filter: str) -> dict:
      brokers = filter_registry(broker_filter)
      live = [b for b in brokers if is_healthy(b)]
      stale = [b.name for b in brokers if b not in live]
      results = await asyncio.gather(
          *[getattr(b, method_name)() for b in live],
          return_exceptions=True,
      )
      rows = []
      for broker, result in zip(live, results):
          if isinstance(result, Exception):
              stale.append(broker.name)
              log.warning(...)
          else:
              rows.extend({**row, "broker": broker.name} for row in result)
      return {"rows": rows, "stale_brokers": stale}
  ```
- **`backend/journal.py`** — `_log_trade` stamps `broker` on new rows, read from the bot's configured broker. `load_journal()` accepts a `broker` filter. Untagged legacy rows pass through with `broker: null`.

### Frontend

- **`shared/hooks/useBroker.ts`** — extended to surface `health` and `heartbeat_warmup`.
- **`features/trading/BrokerTag.tsx`** — new, small component. Colored pill (alpaca=blue, ibkr=orange) with optional health dot. Dot only renders when health data is present, so Alpaca rows show a plain tag.
- **`features/trading/PositionsTable.tsx`, `OrderHistory.tsx`, `TradeJournal.tsx`** — each adds a Broker column and a filter dropdown above the table ("All / Alpaca / IBKR"). Filter UI mirrors the existing TradeJournal reason filter.
- **`features/trading/PaperTrading.tsx`** — dismissible warning banner when any panel's response has a non-empty `stale_brokers`.

No refactors of existing files beyond the focused additions above.

## Data flow

### Heartbeat tick (every 30s)

```
for provider in registry:
    if not hasattr(provider, "ping"):
        continue
    try:
        await asyncio.wait_for(provider.ping(), timeout=5.0)
        health[provider.name] = {
            "healthy": True,
            "last_ok_ts": now(),
            "last_error": None,
        }
    except Exception as e:
        prev = health.get(provider.name, {})
        health[provider.name] = {
            "healthy": False,
            "last_ok_ts": prev.get("last_ok_ts"),
            "last_error": str(e),
        }
```

On startup, the first tick waits ~10s so IBKR can finish connecting. During that window `heartbeat_warmup=true` in `/api/broker` so the UI doesn't flash a red dot.

Exceptions inside `_tick` are caught at the loop level — the task must never die. A silently-dead heartbeat would falsely skip a healthy broker forever.

### Positions request

```
GET /api/positions?broker=all
  ↓
aggregate_from_brokers("get_positions", "all")
  ↓
skip IBKR if health["ibkr"].healthy is False (mark stale)
gather(remaining providers, return_exceptions=True)
tag each row with broker
  ↓
{ positions: [...], stale_brokers: [...] }
```

Orders is identical with a different method name. Journal is a local JSON read — no gather, no health check — just filter rows by optional `broker` param.

### Frontend state

`stale_brokers` is hoisted to `PaperTrading.tsx` state. All three panels update it via their query responses. The warning banner reads the union across panels.

## Error handling

- **IBKR session stale mid-day.** Heartbeat detects within 30s → `healthy=False`. Positions path skips IBKR, returns Alpaca rows + `stale_brokers: ["ibkr"]`. Existing `_ensure_connected` reconnect in `IBKRTradingProvider` still runs when a trading action is attempted; D6 doesn't replace it, just gives the read path visibility.
- **One broker raises mid-aggregation.** `return_exceptions=True` means other brokers' rows still land. Raising broker appended to `stale_brokers`, logged at WARN.
- **Heartbeat task crashes.** Per-iteration try/except inside the while-loop. Logs and continues. Never exits.
- **Mixed tagged/untagged journal rows.** "All" filter includes everything. Specific-broker filters exclude null-broker rows.

## Testing

Backend only; frontend has no test harness today.

- **`backend/tests/test_broker_health.py`** — happy path (ping succeeds → healthy), timeout (→ unhealthy, preserves last_ok_ts), provider raises (→ unhealthy, error recorded), task survives `_tick` exceptions, start/stop lifecycle.
- **`backend/tests/test_positions_aggregation.py`** — all healthy returns union, one broker unhealthy is skipped and listed stale, one broker raises is caught and listed stale, `?broker=ibkr` returns only IBKR rows.
- **`backend/tests/test_journal_broker_filter.py`** — new rows get stamped with broker, old rows pass through null, filter by broker works, "all" includes nulls.

## Out of scope

- Heartbeat for Alpaca/Yahoo (stateless HTTPS; per-request errors are sufficient).
- Backfill of `broker` field on historical journal rows (fragile, low value — old rows show "—").
- Health dot in `BrokerTag` for non-IBKR brokers (no heartbeat data to drive it).
- Grouped-by-broker table layouts (rejected in brainstorming in favor of single table + filter).
- Persistent health state across restarts (in-memory only; first tick after restart repopulates).

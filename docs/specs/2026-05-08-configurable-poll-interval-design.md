# Configurable Bot Poll Interval + API Rate Counter

## Problem

Bot polling runs on interval-based timers (10-60s depending on bar cadence). This adds 5-30s of signal-to-execution latency, inflating empirical slippage — e.g., BABA shows 12.99 bps empirical vs 3.6 bps NBBO spread. The gap is execution latency, not market microstructure.

Additionally, there's no visibility into how many API calls per minute the system makes, making it hard to tune the poll interval against Alpaca's 200 req/min rate limit.

## Solution

1. **Global poll interval setting** — configurable in ms (default 500), overrides the current per-bar-interval mapping
2. **API rate counter** — sliding window counter on all broker API calls, exposed via `/api/broker` and displayed in the frontend

## Design

### Global Poll Interval

**Backend:**
- New env var `BOT_POLL_MS` (default: unset, meaning use existing per-bar-interval mapping)
- When set, all bots poll at this interval regardless of their bar cadence
- Read at startup in `bot_runner.py`, stored as module-level global
- Runtime-changeable via `PATCH /api/broker/poll-interval` → updates in-memory global (not .env)
- Existing `POLL_INTERVALS` dict (`1m→10s`, `5m→15s`, etc.) is the fallback when `BOT_POLL_MS` is unset

**API:**
- `GET /api/broker` response gains `poll_interval_ms: int` (current effective value)
- `PATCH /api/broker/poll-interval` accepts `{ "ms": number }`, validates `100 <= ms <= 60000`

**Frontend:**
- Input field in Live Trading settings showing current poll interval in ms
- Editable, calls PATCH endpoint on change

### API Rate Counter

**Backend:**
- `RateCounter` class in `broker.py`:
  - `collections.deque` of `time.monotonic()` timestamps
  - `record()` — append timestamp on every API call
  - `calls_per_minute() -> int` — evict entries older than 60s, return count
- One global `_rate_counter` instance
- Instrumented on all `AlpacaTradingProvider` public methods: `submit_order`, `get_positions`, `get_order`, `cancel_order`, `get_latest_quote`, `get_latest_price`, `close_position`
- Instrumented on `IBKRTradingProvider._run()` (covers all IBKR operations)

**API:**
- `GET /api/broker` response gains `api_calls_per_minute: int`

**Frontend:**
- Display in AccountBar: `"API: 47/200 per min"`
- Color coding: green <150, amber 150-190, red >190
- Updates on existing broker health poll interval (React Query, 30s)
- When IBKR is active broker, show `"API: 12/min"` without the `/200` cap (IBKR has no documented rate limit)

### Safety & Edge Cases

- **No hard enforcement.** Counter is informational. Alpaca returns HTTP 429 on rate limit — the bot misses that tick but recovers next cycle.
- **Counter is per-process.** Restarting backend resets it. Rolling 60s window refills within a minute.
- **Multi-bot math.** 3 bots × 500ms × ~2 calls/tick ≈ 720 calls/min — over Alpaca's 200 limit. Counter flags this immediately so the user can adjust.
- **Startup warning.** Not in v1 — the live counter provides the same feedback loop.

## Files Changed

| File | Change |
|------|--------|
| `backend/bot_runner.py` | Read `BOT_POLL_MS`, use as poll interval override |
| `backend/broker.py` | Add `RateCounter` class, instrument API methods |
| `backend/routes/bots.py` | Add `PATCH /api/broker/poll-interval`, extend `GET /api/broker` response |
| `frontend/src/api/trading.ts` | Extend `BrokerInfo` type with `poll_interval_ms`, `api_calls_per_minute` |
| `frontend/src/features/trading/AccountBar.tsx` | Display rate counter |
| `frontend/src/features/trading/BotControlCenter.tsx` or settings area | Poll interval input |

## Not In Scope

- Per-bot poll interval override (follow-up)
- Hard rate limiting / request queuing
- Startup warning for estimated rate
- Alpaca `X-RateLimit-Remaining` header parsing

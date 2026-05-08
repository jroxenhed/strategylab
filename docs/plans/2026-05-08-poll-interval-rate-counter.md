# Implementation Plan: Configurable Poll Interval + API Rate Counter

**Spec:** [docs/specs/2026-05-08-configurable-poll-interval-design.md](../specs/2026-05-08-configurable-poll-interval-design.md)

## Requirements Trace

| ID | Requirement |
|----|-------------|
| R1 | Global poll interval setting (BOT_POLL_MS env var, runtime PATCH) |
| R2 | RateCounter class with sliding window (60s deque) |
| R3 | Instrument all AlpacaTradingProvider + IBKRTradingProvider API methods |
| R4 | Extend GET /api/broker with poll_interval_ms + api_calls_per_minute |
| R5 | PATCH /api/broker/poll-interval endpoint |
| R6 | Frontend: rate counter display in AccountBar |
| R7 | Frontend: poll interval input in Live Trading |

## Implementation Units

### Phase A: Backend — RateCounter + poll interval global

**Target:** `backend/broker.py`, `backend/bot_runner.py`
No file overlap with Phase B or C.

#### A1: RateCounter class (R2)
File: `backend/broker.py`

Add near the top (after imports):
```python
import time as _time
from collections import deque

class RateCounter:
    def __init__(self, window_secs: float = 60.0):
        self._window = window_secs
        self._calls: deque[float] = deque()

    def record(self):
        self._calls.append(_time.monotonic())

    def calls_per_minute(self) -> int:
        cutoff = _time.monotonic() - self._window
        while self._calls and self._calls[0] < cutoff:
            self._calls.popleft()
        return len(self._calls)

_rate_counter = RateCounter()

def get_rate_counter() -> RateCounter:
    return _rate_counter
```

#### A2: Instrument AlpacaTradingProvider (R3)
File: `backend/broker.py`

Add `_rate_counter.record()` as the first line in each public method:
- `submit_order`
- `get_positions`
- `get_order`
- `cancel_order`
- `close_position`
- `get_latest_price`
- `get_latest_quote`

Each method already exists — just add one line at the top of each.

#### A3: Instrument IBKRTradingProvider (R3)
File: `backend/broker.py`

Add `_rate_counter.record()` inside `_run()` — this is the single gateway for all IBKR operations. One line, covers everything.

#### A4: Global poll interval (R1)
File: `backend/bot_runner.py`

Add module-level global:
```python
import os
_POLL_MS: int = int(os.environ.get("BOT_POLL_MS", "0"))  # 0 = use per-interval defaults

def get_poll_ms() -> int:
    return _POLL_MS

def set_poll_ms(ms: int):
    global _POLL_MS
    _POLL_MS = ms
```

In the main `run()` loop, where `interval_secs` is computed (~line 494):
```python
if _POLL_MS > 0:
    interval_secs = _POLL_MS / 1000.0
else:
    interval_secs = POLL_INTERVALS.get(self.config.interval, 30)
```

---

### Phase B: Backend — API endpoints

**Target:** `backend/routes/bots.py`
No file overlap with Phase A or C.

#### B1: Extend GET /api/broker (R4)
File: `backend/routes/bots.py`

Find the `/api/broker` endpoint. Add to the response dict:
```python
from broker import get_rate_counter
from bot_runner import get_poll_ms

# In the response:
"poll_interval_ms": get_poll_ms() or None,  # None = using per-interval defaults
"api_calls_per_minute": get_rate_counter().calls_per_minute(),
```

#### B2: PATCH /api/broker/poll-interval (R5)
File: `backend/routes/bots.py`

```python
@router.patch("/api/broker/poll-interval")
def set_poll_interval(body: dict):
    ms = body.get("ms")
    if not isinstance(ms, int) or ms < 100 or ms > 60000:
        raise HTTPException(400, "ms must be integer between 100 and 60000")
    from bot_runner import set_poll_ms
    set_poll_ms(ms)
    return {"poll_interval_ms": ms}
```

---

### Phase C: Frontend — display + controls

**Targets:** `frontend/src/api/trading.ts`, `frontend/src/features/trading/AccountBar.tsx`, `frontend/src/features/trading/BotControlCenter.tsx`
No file overlap with Phase A or B.

#### C1: Extend BrokerInfo type (R4)
File: `frontend/src/api/trading.ts`

Add to `BrokerInfo` interface:
```typescript
poll_interval_ms: number | null
api_calls_per_minute: number
```

#### C2: Rate counter display in AccountBar (R6)
File: `frontend/src/features/trading/AccountBar.tsx`

Add after the existing health dot display:
```tsx
// Color: green <150, amber 150-190, red >190
const rpm = brokerInfo.api_calls_per_minute
const rpmColor = rpm > 190 ? '#ef5350' : rpm > 150 ? '#f0883e' : '#3fb950'
// Show "/200" only when Alpaca is active
const rpmLabel = broker === 'ibkr' ? `${rpm}/min` : `${rpm}/200`
```

Display as a compact label: `API: 47/200` with the color.

#### C3: Poll interval input (R7)
File: `frontend/src/features/trading/BotControlCenter.tsx`

Add a small input near the bot controls area:
- Label: "Poll interval (ms)"
- Default value from `brokerInfo.poll_interval_ms` or placeholder "auto"
- On blur/enter: PATCH `/api/broker/poll-interval` with `{ ms: value }`
- Validation: 100-60000, integer only

## Parallelism Map

```
Phase A (broker.py + bot_runner.py)  ──┐
Phase B (routes/bots.py)             ──┤  3 parallel agents, zero file overlap
Phase C (frontend)                   ──┘
```

File ownership:
- **A**: `backend/broker.py`, `backend/bot_runner.py`
- **B**: `backend/routes/bots.py`
- **C**: `frontend/src/api/trading.ts`, `frontend/src/features/trading/AccountBar.tsx`, `frontend/src/features/trading/BotControlCenter.tsx`

## Verification

1. `npm run build` — must pass
2. `python3 -c "import ast; ast.parse(open('backend/broker.py').read())"` — syntax OK
3. `python3 -c "import ast; ast.parse(open('backend/bot_runner.py').read())"` — syntax OK
4. `python3 -c "import ast; ast.parse(open('backend/routes/bots.py').read())"` — syntax OK
5. `curl /api/broker` — verify `poll_interval_ms` and `api_calls_per_minute` present
6. `curl -X PATCH /api/broker/poll-interval -d '{"ms":500}'` — verify 200 response
7. Visual: AccountBar shows rate counter, BotControlCenter shows poll interval input

# Live Trading Bot — Implementation Plan

## Context

StrategyLab has a backtester, saved strategies, and Alpaca paper trading integration (account, positions, orders, manual signal scanner). The missing piece is **automation** — a bot that runs saved strategies in a loop, evaluating rules and placing orders automatically. The full spec lives at `docs/superpowers/specs/2026-04-08-live-trading-bot-plan.md`.

This plan implements the spec across 2 phases: backend first (Steps 1–4), then frontend (Steps 5–8). Each phase ends with a verification checkpoint and commit.

---

## Fund Management

Bots share a single Alpaca paper account. Without guardrails, multiple bots could collectively exceed available buying power. The fund management system prevents this:

### How it works

1. **Bot fund** — A user-configured global cap (e.g. $50,000) representing the maximum total capital all bots combined can use. The rest of the account remains untouched for manual trading. Stored in `data/bots.json` alongside bot configs. Defaults to 0 (must be set before starting any bot).

2. **Per-bot allocation** (`allocated_capital`)  — Each bot gets a slice of the bot fund (e.g. $5,000). This replaces the original `position_size_usd` flat amount. The bot uses `position_size` as a **fraction** of its allocation per trade (same model as the backtester: 0.0–1.0), so a bot with $5,000 allocation and `position_size: 0.5` risks $2,500 per trade.

3. **Enforcement rules:**
   - `sum(all bots' allocated_capital)` can never exceed the bot fund
   - When adding a bot, the UI shows **available = bot_fund - sum(existing allocations)** and caps the input
   - `start_bot()` rejects if allocation would push total over the bot fund
   - The bot fund itself cannot exceed current Alpaca account equity (validated on set, but not continuously — the account value fluctuates)

4. **Where this lives:**
   - **Backend:** `BotManager` stores `bot_fund: float` and exposes `set_bot_fund()`, `get_fund_status()` (returns `{bot_fund, allocated, available}`). `BotConfig` has `allocated_capital: float` and `position_size: float` (fraction, 0.0–1.0).
   - **API:** `GET /api/bots/fund` returns fund status, `PUT /api/bots/fund` sets the bot fund amount. Add bot validates allocation fits.
   - **Frontend:** Bot fund setting in BotControlCenter header. "Available: $X" shown next to allocation input when adding a bot. Fund overview bar showing allocated vs available.

### Example flow

- Account equity: $100,000
- User sets bot fund to $50,000 (other $50k for manual trading)
- Creates Bot A with $20,000 allocation → available drops to $30,000
- Creates Bot B with $25,000 allocation → available drops to $5,000
- Creates Bot C — UI shows "Available: $5,000", allocation input maxes at $5,000
- Bot A uses `position_size: 0.5` → risks $10,000 per trade from its $20,000

---

## Phase 1: Backend (Steps 1–4)

### Step 1: Fix ATR gap in live scans

**File:** `backend/routes/trading.py:275`

**What:** Change `compute_indicators(df["Close"])` → `compute_indicators(df["Close"], high=df["High"], low=df["Low"])` so ATR is available for trailing stops in both the manual scanner and the new bot.

**Why this matters:** Without this fix, any bot using ATR-based trailing stops would get `KeyError: 'atr'` when evaluating bars. The backtest already passes high/low (see `backtest.py:67-69`), so this just brings the scan path into parity.

**Verification:** Start backend, run a scan via curl — response should succeed without errors. No behavior change for existing scans (ATR is computed but not used unless a rule references it).

---

### Step 2: Bot engine — `backend/bot_manager.py` (NEW, ~350 lines)

This is the core file. It contains 4 classes that work together:

#### 2a. `BotConfig` (Pydantic model)

Defines everything needed to run one bot. Fields:
- `bot_id: str` (UUID, auto-generated)
- `strategy_name: str`, `symbol: str`, `interval: str`
- `buy_rules: list[Rule]`, `sell_rules: list[Rule]`, `buy_logic: str`, `sell_logic: str`
- `allocated_capital: float` (dollar amount allocated from bot fund, e.g. $5,000)
- `position_size: float` (fraction of allocated_capital per trade, 0.01–1.0, same as backtester)
- `stop_loss_pct: Optional[float]`
- `trailing_stop: Optional[TrailingStopConfig]` — reuse from `backtest.py:14-19`
- `dynamic_sizing: Optional[DynamicSizingConfig]` — reuse from `backtest.py:22-25`
- `trading_hours: Optional[TradingHoursConfig]` — reuse from `backtest.py:28-32`
- `slippage_pct: float = 0.0`

**Reuse:** Import `TrailingStopConfig`, `DynamicSizingConfig`, `TradingHoursConfig` directly from `routes.backtest`. Import `Rule` from `signal_engine`.

#### 2b. `BotState` (dataclass)

Mutable runtime state for one bot:
- `status: str` — `"stopped"` | `"backtesting"` | `"running"` | `"error"`
- `started_at`, `last_scan_at`, `last_bar_time` — timestamps for lifecycle tracking
- `last_signal: str`, `last_price: float` — most recent evaluation result
- `entry_price`, `trail_peak`, `trail_stop_price` — for trailing stop management (mirrors `backtest.py:141-144,160-172`)
- `consec_sl_count: int` — for dynamic sizing (mirrors `backtest.py:213-216`)
- `scans_count`, `trades_count`, `total_pnl: float` — aggregate stats
- `equity_snapshots: list[dict]` — `{time, value}` entries appended after each sell, where `value` = cumulative realized P&L
- `backtest_result: Optional[dict]` — cached backtest output (summary + equity_curve) so user can compare expected vs actual
- `activity_log: list[dict]` — `{time, msg, level}`, capped at 200 entries, newest first
- `error_message: Optional[str]` — set when status is `"error"`

#### 2c. `BotRunner` — the async polling loop

This is the heart of the bot. One `BotRunner` per active bot, running as an `asyncio.Task`.

**Poll intervals** (match bar cadence — fast enough to catch new bars, slow enough to avoid hammering):
```python
POLL_INTERVALS = {"5m": 15, "15m": 20, "30m": 30, "1h": 60}
```

**The `_tick()` method** — called every poll interval:

1. **Trading hours check** — Port the filter from `backtest.py:111-128`. Convert current ET time to `HH:MM` string, check against `start_time`/`end_time` and `skip_ranges`. If outside window, log and return. Only affects entries (not exits), same as backtest.

2. **Fetch bars** — Call `_fetch(symbol, start, end, interval, source='alpaca-iex')` from `shared.py`. Start = 30 days back (enough history for indicator warmup). End = now. The 2-minute TTL cache means we don't hammer Alpaca on every tick.

3. **New bar detection** — Compare `df.index[-1]` to `state.last_bar_time`. If same bar, skip (avoids duplicate signals). Update `last_bar_time` on new bar.

4. **Compute indicators** — `compute_indicators(df["Close"], high=df["High"], low=df["Low"])` from `signal_engine.py`. This gives us MACD, RSI, EMAs, and ATR.

5. **Check Alpaca positions** — Call `client.get_all_positions()` and check if our symbol is in there. This is the source of truth — handles manual closes, OTO stop fills, etc. **Critical on first tick after start:** if a position exists from a previous session, resume tracking it (set `entry_price` from Alpaca's `avg_entry_price`, start trailing stop tracking).

6. **If NO position → evaluate buy rules:**
   - `eval_rules(config.buy_rules, config.buy_logic, indicators, i)` where `i = len(df) - 1`
   - If signal fires:
     - Compute effective size: `effective_size = config.allocated_capital * config.position_size`
     - Apply dynamic sizing: if `consec_sl_count >= threshold`, reduce further by `reduced_pct` (from `backtest.py:131-134`)
     - Calculate `qty = floor(effective_size / price)`
     - **If trailing stop configured:** Place plain `MarketOrderRequest` (bot manages exits itself via polling)
     - **If only fixed stop_loss:** Place OTO bracket order with `StopLossRequest` — Alpaca watches the price server-side, fires even if bot is down (reuse pattern from `trading.py:301-315`)
     - Log trade via `_log_trade()` from `trading.py` with `source="bot"`
     - Update state: `entry_price`, `trail_peak = price`

7. **If HAS position → evaluate exits:**
   - Update `trail_peak` from source price (high or close, per config) — same logic as `backtest.py:163-166`
   - Compute `trail_stop_price`:
     - PCT: `trail_peak * (1 - value / 100)` — from `backtest.py:167-168`
     - ATR: `trail_peak - value * atr_val` — from `backtest.py:169-171`
   - Check activation threshold: only update trail if `source_price >= entry_price * (1 + activate_pct / 100)` — from `backtest.py:164-165`
   - Check fixed stop: `price <= entry_price * (1 - stop_loss_pct / 100)`
   - Check trailing stop: `price <= trail_stop_price`
   - Check sell rules: `eval_rules(config.sell_rules, ...)`
   - **Exit priority** (same as `backtest.py:178-187`): fixed stop > trailing stop > signal
   - On exit: `MarketOrderRequest` to close via `client.close_position(symbol)`, update `consec_sl_count` (increment for stops, reset for signal exit — `backtest.py:213-216`), calculate P&L, append to `equity_snapshots`, log trade

8. **Log activity** — Append `{time, msg, level}` to `state.activity_log`. Cap at 200 entries.

**Critical implementation detail — async/sync bridge:**
The Alpaca SDK and `_fetch()` are synchronous. Every blocking call inside `_tick()` must be wrapped in `await asyncio.get_event_loop().run_in_executor(None, fn)` or the event loop blocks and FastAPI becomes unresponsive. This applies to: `_fetch()`, `client.get_all_positions()`, `client.submit_order()`, `client.close_position()`, `compute_indicators()`.

**The polling loop:**
```python
async def run(self):
    while True:
        try:
            await self._tick()
        except Exception as e:
            self.state.status = "error"
            self.state.error_message = str(e)
            self._log("ERROR", f"Bot error: {e}")
            break
        await asyncio.sleep(POLL_INTERVALS.get(self.config.interval, 30))
```

#### 2d. `BotManager` — singleton managing all bots

- `bot_fund: float` — global cap on total capital for all bots (persisted)
- `bots: dict[str, tuple[BotConfig, BotState]]` — all bots (running or stopped)
- `tasks: dict[str, asyncio.Task]` — running bot tasks

**Fund management methods:**
- `set_bot_fund(amount)` — set the global bot fund cap. Rejects if `amount < sum(existing allocations)` (can't shrink below what's already allocated).
- `get_fund_status() -> {bot_fund, allocated, available}` — returns the current fund breakdown. `allocated = sum(bot.config.allocated_capital for all bots)`, `available = bot_fund - allocated`.
- `_validate_allocation(amount, exclude_bot_id=None)` — internal helper used by `add_bot()` and when updating a bot. Checks `sum(allocations) + amount <= bot_fund`. The `exclude_bot_id` param allows replacing an existing bot's allocation during edits.

**Bot lifecycle methods:**
- `add_bot(config) -> str` — validate `allocated_capital` fits within available fund. Create bot in "stopped" state, return bot_id. Rejects if allocation would exceed available.
- `start_bot(bot_id)` — reject if symbol already has a running bot. Create `BotRunner`, wrap in `asyncio.Task`, store in `tasks`. On first tick, runner detects existing Alpaca position and resumes.
- `stop_bot(bot_id, close_position=False)` — cancel the asyncio task, set status "stopped". If `close_position=True`, also `client.close_position(symbol)`.
- `backtest_bot(bot_id)` — call `run_backtest()` from `routes/backtest.py` with the bot's config converted to a `StrategyRequest`. Cache result on `state.backtest_result`. Set status "backtesting" during, then back to "stopped".
- `get_bot(bot_id)`, `list_bots()`, `delete_bot(bot_id)` — CRUD operations
- `save()` — serialize bot_fund + configs + states to `data/bots.json` (called after every state change)
- `load()` — deserialize on startup, all bots start as "stopped"
- `shutdown()` — stop all running bots (keep positions open), save state

**Persistence format** (`data/bots.json`):
```json
{
  "bot_fund": 50000.0,
  "bots": [
    {"config": {...}, "state": {...}}
  ]
}
```

---

### Step 3: Bot API endpoints — `backend/routes/bots.py` (NEW, ~120 lines)

REST endpoints that delegate to `BotManager`:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/bots/fund` | GET | Get fund status: `{bot_fund, allocated, available}` |
| `/api/bots/fund` | PUT | Set bot fund amount. Body: `{amount: float}`. Rejects if < current allocations |
| `/api/bots` | POST | Add a new bot (stopped). Body includes `allocated_capital`. Rejects if exceeds available fund. Returns `{bot_id}` |
| `/api/bots` | GET | List all bots with summary + fund status |
| `/api/bots/{id}` | GET | Full bot detail: config + state + backtest_result + activity_log |
| `/api/bots/{id}/start` | POST | Start live polling. Error if symbol already has a running bot |
| `/api/bots/{id}/stop` | POST | Stop polling, keep position open. `?close=true` to also close position |
| `/api/bots/{id}/backtest` | POST | Run backtest with bot's config, cache result on state |
| `/api/bots/{id}` | DELETE | Remove a stopped bot. Error if running. Frees its allocation back to available |

**Note:** Fund endpoints (`/api/bots/fund`) must be registered before the `/{id}` routes to avoid FastAPI treating "fund" as a bot ID.

**Module-level `bot_manager` variable:** The lifespan handler in `main.py` will set `bots.bot_manager = manager` after creating the BotManager. This avoids circular imports. Each endpoint accesses it via `bot_manager` module variable, raising 503 if not initialized.

**Pattern:** Follow the same style as `trading.py` — `APIRouter(prefix="/api/bots")`, Pydantic request models, HTTPException for errors.

---

### Step 4: Wire into FastAPI — `backend/main.py` (MODIFY)

Two changes:

1. **Add `lifespan` context manager:**
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: load bot configs
    from routes import bots as bots_module
    manager = BotManager()
    manager.load()
    bots_module.bot_manager = manager
    yield
    # Shutdown: stop all bots, save state
    await manager.shutdown()
```

2. **Include bots router:**
```python
from routes.bots import router as bots_router
app.include_router(bots_router)
```

3. **Pass lifespan to FastAPI:**
```python
app = FastAPI(lifespan=lifespan)
```

---

### Phase 1 Verification

After completing Steps 1–4, verify the backend works end-to-end with curl:

```bash
# Start backend
cd backend && source .venv/bin/activate && uvicorn main:app --reload

# 1. Set bot fund
curl -X PUT http://localhost:8000/api/bots/fund \
  -H "Content-Type: application/json" \
  -d '{"amount": 50000}'

# 2. Check fund status (should show bot_fund=50000, allocated=0, available=50000)
curl http://localhost:8000/api/bots/fund

# 3. Add a bot with $5000 allocation
curl -X POST http://localhost:8000/api/bots \
  -H "Content-Type: application/json" \
  -d '{"strategy_name":"Test RSI","symbol":"AAPL","interval":"15m",
       "buy_rules":[{"indicator":"rsi","condition":"below","value":30}],
       "sell_rules":[{"indicator":"rsi","condition":"above","value":70}],
       "buy_logic":"AND","sell_logic":"AND",
       "allocated_capital":5000,"position_size":1.0}'

# 4. Check fund status (should show allocated=5000, available=45000)
curl http://localhost:8000/api/bots/fund

# 5. List bots (should show 1 bot, status "stopped")
curl http://localhost:8000/api/bots

# 6. Run backtest on the bot
curl -X POST http://localhost:8000/api/bots/{bot_id}/backtest

# 7. Start the bot
curl -X POST http://localhost:8000/api/bots/{bot_id}/start

# 8. Check bot detail (should show "running", activity log entries)
curl http://localhost:8000/api/bots/{bot_id}

# 9. Stop the bot
curl -X POST http://localhost:8000/api/bots/{bot_id}/stop

# 10. Delete the bot (allocation freed back to available)
curl -X DELETE http://localhost:8000/api/bots/{bot_id}
```

**Commit after Phase 1 passes.**

---

## Phase 2: Frontend (Steps 5–8)

### Step 5 (spec Step 8): Frontend types — `frontend/src/shared/types/index.ts` (MODIFY)

**Do types first** so the API client and UI have type safety from the start.

Add these interfaces (at the end of the file, after existing types):

```typescript
export interface BotConfig {
  bot_id: string
  strategy_name: string
  symbol: string
  interval: string
  buy_rules: Rule[]
  sell_rules: Rule[]
  buy_logic: 'AND' | 'OR'
  sell_logic: 'AND' | 'OR'
  allocated_capital: number
  position_size: number        // fraction of allocated_capital (0.01–1.0)
  stop_loss_pct?: number
  trailing_stop?: TrailingStopConfig
  dynamic_sizing?: DynamicSizingConfig
  trading_hours?: TradingHoursConfig
  slippage_pct?: number
}

export interface BotFundStatus {
  bot_fund: number
  allocated: number
  available: number
}

export interface BotActivityEntry {
  time: string
  msg: string
  level: 'INFO' | 'WARN' | 'ERROR' | 'TRADE'
}

export interface BotState {
  status: 'stopped' | 'backtesting' | 'running' | 'error'
  started_at?: string
  last_scan_at?: string
  last_signal?: string
  last_price?: number
  trades_count: number
  total_pnl: number
  equity_snapshots: { time: string; value: number }[]
  backtest_result?: {
    summary: BacktestResult['summary']
    equity_curve: { time: string; value: number }[]
  }
  activity_log: BotActivityEntry[]
  error_message?: string
}

export interface BotSummary {
  bot_id: string
  strategy_name: string
  symbol: string
  interval: string
  status: string
  trades_count: number
  total_pnl: number
}

export interface BotDetail {
  config: BotConfig
  state: BotState
}
```

---

### Step 6 (spec Step 5): API client — `frontend/src/api/bots.ts` (NEW, ~60 lines)

Axios-based client following the pattern in `api/trading.ts`:

```typescript
import axios from 'axios'
import type { BotSummary, BotDetail } from '../shared/types'

const API = 'http://localhost:8000'

export async function getBotFund(): Promise<BotFundStatus>
export async function setBotFund(amount: number): Promise<BotFundStatus>
export async function addBot(config: Omit<BotConfig, 'bot_id'>): Promise<{ bot_id: string }>
export async function listBots(): Promise<BotSummary[]>
export async function fetchBotDetail(botId: string): Promise<BotDetail>
export async function startBot(botId: string): Promise<void>
export async function stopBot(botId: string, close?: boolean): Promise<void>
export async function backtestBot(botId: string): Promise<void>
export async function deleteBot(botId: string): Promise<void>
```

Each function is a simple axios call to the corresponding `/api/bots` endpoint.

---

### Step 7 (spec Step 6): Bot Control Center UI — `frontend/src/features/trading/BotControlCenter.tsx` (NEW, ~500 lines)

The main UI component. Layout per the spec's ASCII diagram (`docs/misc/bot_design.md`).

**Structure:**
1. **Fund bar** (top) — Shows bot fund status: "Bot Fund: $50,000 | Allocated: $45,000 | Available: $5,000". Editable fund amount (click to change). Visual bar showing allocated/available proportions. If bot fund is 0 (not set), shows a prompt: "Set your bot fund to get started" with an input.
2. **Add bot bar** — Strategy dropdown (reads from `localStorage('strategylab-saved-strategies')`), ticker input, interval select, **allocation input** (capped at available fund, shows "Available: $X" hint), position size slider (0.01–1.0), [+ Add Bot] button. The [+ Add Bot] button is disabled if bot fund is 0 or available is 0.
3. **Bot cards** (one per bot) — Each card shows:
   - Status dot (green=running, gray=stopped, red=error)
   - Strategy name, symbol, interval
   - Trade count and total P&L
   - Mini equity sparkline (lightweight-charts `BaselineSeries`, ~60px tall, green above 0 / red below 0)
   - If backtest was run: show summary metrics (return %, Sharpe, MDD)
   - Expandable activity log section
   - Allocation amount (e.g. "$5,000 allocated")
   - Action buttons: [Backtest], [Start]/[Stop], [Delete] (only when stopped)
4. **Portfolio equity chart** (~180px) — Combined P&L across all bots with per-bot overlay lines

**Polling:** `listBots()` every 5 seconds. When a bot card is expanded, `fetchBotDetail(id)` every 5 seconds for the activity log.

**Strategy selection flow:**
1. User sets bot fund (once) — e.g. $50,000
2. User selects a saved strategy from dropdown
3. Strategy's ticker, interval, and rules are pre-filled
4. User sets allocation amount (UI shows "Available: $X" and caps input)
5. User adjusts position size fraction (defaults from saved strategy's `posSize`)
6. Click [+ Add Bot] → calls `addBot()` → bot appears in list as "stopped", available fund decreases
7. User can [Backtest] first to see expected performance
8. User clicks [Start] to begin live polling

**Mini equity charts:** Use lightweight-charts `createChart()` with `BaselineSeries`. Data from `state.equity_snapshots`. Only render when 2+ data points exist. Baseline at 0 (cumulative P&L). Green above, red below.

**Portfolio chart:** Merge all bots' `equity_snapshots`, sorted by time, compute running totals. Show per-bot lines in distinct colors + combined total as thicker BaselineSeries.

---

### Step 8 (spec Step 7): Integrate into PaperTrading — `PaperTrading.tsx` (MODIFY)

Simple insertion — add `<BotControlCenter />` between `<AccountBar />` and `<PositionsTable />`:

```tsx
import BotControlCenter from './BotControlCenter'

export default function PaperTrading() {
  return (
    <div style={styles.container}>
      <AccountBar />
      <BotControlCenter />
      <PositionsTable />
      <SignalScanner />
      <PerformanceComparison />
      <TradeJournal />
      <OrderHistory />
    </div>
  )
}
```

All existing components stay — they show all Alpaca activity regardless of source (manual, auto-scan, or bot).

---

### Phase 2 Verification

After completing Steps 5–8, verify the full flow in the browser:

1. Start both servers with `./start.sh`
2. Open http://localhost:5173, go to Chart tab
3. Create and save a strategy (e.g. "FSLR RSI 26-64")
4. Switch to Paper Trading tab
5. Verify BotControlCenter appears between AccountBar and PositionsTable
6. Select the saved strategy from dropdown — ticker/interval should pre-fill
7. Click [+ Add Bot] — bot card should appear with "stopped" status (gray dot)
8. Click [Backtest] — should show summary metrics and equity curve
9. Click [Start] — status should change to "running" (green dot)
10. Watch activity log for "New bar detected" entries
11. If a BUY signal fires, verify position appears in PositionsTable below
12. Click [Stop] — status should return to "stopped"
13. Restart the server — bot should load as "stopped" with preserved state

**Commit after Phase 2 passes.**

---

## Files Summary

| # | File | Action | Est. Lines | Description |
|---|------|--------|-----------|-------------|
| 1 | `backend/routes/trading.py:275` | Modify | 1 line | Pass high/low to compute_indicators |
| 2 | `backend/bot_manager.py` | **New** | ~350 | Bot engine: BotConfig, BotState, BotRunner, BotManager |
| 3 | `backend/routes/bots.py` | **New** | ~120 | REST API: add/start/stop/backtest/list/detail/delete |
| 4 | `backend/main.py` | Modify | ~15 | Lifespan handler + bots router |
| 5 | `frontend/src/shared/types/index.ts` | Modify | ~50 | Bot-related TypeScript interfaces |
| 6 | `frontend/src/api/bots.ts` | **New** | ~60 | Axios API client for bot endpoints |
| 7 | `frontend/src/features/trading/BotControlCenter.tsx` | **New** | ~500 | Bot control UI with equity charts |
| 8 | `frontend/src/features/trading/PaperTrading.tsx` | Modify | 2 lines | Import + render BotControlCenter |

## Key Reuse Points

| What | From | Used In |
|------|------|---------|
| `TrailingStopConfig`, `DynamicSizingConfig`, `TradingHoursConfig` | `routes/backtest.py:14-32` | `bot_manager.py` BotConfig |
| `Rule`, `compute_indicators()`, `eval_rules()` | `signal_engine.py` | `bot_manager.py` BotRunner._tick() |
| `_fetch()` | `shared.py` | `bot_manager.py` BotRunner._tick() |
| `get_trading_client()` | `shared.py` | `bot_manager.py` BotRunner._tick() |
| `_log_trade()` | `routes/trading.py:20-38` | `bot_manager.py` BotRunner (with source="bot") |
| OTO bracket order pattern | `routes/trading.py:301-315` | `bot_manager.py` BotRunner buy logic |
| Trailing stop algorithm | `routes/backtest.py:160-172` | `bot_manager.py` BotRunner exit logic |
| Trading hours filter | `routes/backtest.py:111-128` | `bot_manager.py` BotRunner._tick() |
| Dynamic sizing logic | `routes/backtest.py:131-134` | `bot_manager.py` BotRunner buy logic |
| `run_backtest()` | `routes/backtest.py:62` | `bot_manager.py` BotManager.backtest_bot() |
| Axios API pattern | `api/trading.ts` | `api/bots.ts` |
| SavedStrategy localStorage | `StrategyBuilder.tsx` | `BotControlCenter.tsx` strategy dropdown |

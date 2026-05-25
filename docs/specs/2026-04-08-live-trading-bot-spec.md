# Live Trading Bot — Implementation Plan

## Context

StrategyLab has a powerful backtester with trailing stops, dynamic sizing, and trading hours filters, plus a working Alpaca integration for manual trading (account, positions, orders, signal scanner with one-click execution). The missing piece is **automation** — a bot that runs saved strategies on a schedule, evaluating rules and placing orders automatically.

The existing `/api/trading/scan` endpoint with `auto_execute=true` is almost the bot logic — it just needs to run in a loop with state tracking for trailing stops and consecutive SL counts.

---

## Architecture

**In-process async tasks** managed by a `BotManager` singleton. No external dependencies (no Celery, Redis, or scheduler). Each bot is an `asyncio.Task` with a polling loop.

```
BotManager (singleton, in backend process)
├── Bot "abc-123" (FSLR, 15m, RSI strategy)
│   ├── config: BotConfig (rules, stops, sizing, hours)
│   ├── state: BotState (trail_peak, consec_sl, activity_log)
│   └── task: asyncio.Task (polling loop)
├── Bot "def-456" (ENPH, 15m, RSI strategy)
│   └── ...
└── persistence: data/bots.json (configs only, bots start stopped on restart)
```

---

## Fund Management

Bots share a single Alpaca paper account. The fund management system prevents multiple bots from collectively exceeding available capital and separates bot funds from manual trading.

**Bot fund** — A user-configured global cap (e.g. $50,000) representing the maximum total capital all bots combined can use. The rest of the account remains untouched for manual trading. Stored in `data/bots.json` alongside bot configs. Defaults to 0 (must be set before starting any bot).

**Per-bot allocation** (`allocated_capital`) — Each bot gets a slice of the bot fund (e.g. $5,000). The bot uses `position_size` as a **fraction** of its allocation per trade (same model as the backtester: 0.01–1.0), so a bot with $5,000 allocation and `position_size: 0.5` risks $2,500 per trade.

**Enforcement rules:**
- `sum(all bots' allocated_capital)` can never exceed the bot fund
- When adding a bot, the UI shows **available = bot_fund - sum(existing allocations)** and caps the input
- `start_bot()` rejects if allocation would push total over the bot fund
- Deleting a bot frees its allocation back to available
- The bot fund itself cannot exceed current Alpaca account equity (validated on set)

**Example flow:**
- Account equity: $100,000
- User sets bot fund to $50,000 (other $50k for manual trading)
- Creates Bot A with $20,000 allocation → available drops to $30,000
- Creates Bot B with $25,000 allocation → available drops to $5,000
- Creates Bot C — UI shows "Available: $5,000", allocation input maxes at $5,000

**API endpoints:**
- `GET /api/bots/fund` → `{bot_fund, allocated, available}`
- `PUT /api/bots/fund` → set bot fund amount (rejects if < current allocations)

**BotManager methods:**
- `set_bot_fund(amount)` — set global cap, reject if amount < sum(existing allocations)
- `get_fund_status()` → `{bot_fund, allocated, available}`
- `_validate_allocation(amount, exclude_bot_id=None)` — internal check used by add_bot

**Persistence** (`data/bots.json`):
```json
{
  "bot_fund": 50000.0,
  "bots": [
    {"config": {...}, "state": {...}}
  ]
}
```

---

## Implementation Steps

### Step 1: Fix ATR gap in live scans

**File:** `backend/routes/trading.py` line 275

Change `compute_indicators(df["Close"])` → `compute_indicators(df["Close"], high=df["High"], low=df["Low"])` so ATR is available for trailing stops in both the manual scanner and the new bot.

### Step 2: Bot engine — `backend/bot_manager.py` (NEW)

The core file. Contains:

**`BotConfig`** (Pydantic) — strategy config for one bot:
- `bot_id`, `strategy_name`, `symbol`, `interval`
- `buy_rules`, `sell_rules`, `buy_logic`, `sell_logic`
- `allocated_capital` — dollar amount allocated from bot fund (e.g. $5,000)
- `position_size` — fraction of allocated_capital per trade (0.01–1.0, same as backtester)
- `stop_loss_pct`
- `trailing_stop` (reuse `TrailingStopConfig` from backtest.py)
- `dynamic_sizing` (reuse `DynamicSizingConfig`)
- `trading_hours` (reuse `TradingHoursConfig`)

**`BotState`** (dataclass) — mutable runtime state:
- `status`: "stopped" | "backtesting" | "running" | "error"
- `started_at`, `last_scan_at`, `last_bar_time`
- `last_signal`, `last_price`
- `entry_price`, `trail_peak`, `trail_stop_price` — for trailing stop management
- `consec_sl_count` — for dynamic sizing
- `scans_count`, `trades_count`
- `total_pnl`: running P&L in dollars
- `equity_snapshots`: list of `{time, value}` — appended after each completed trade (buy->sell). `value` = cumulative realized P&L. Persisted with bot state.
- `backtest_result`: optional cached backtest output (summary + equity_curve) — persists so you can compare expected vs actual after going live
- `activity_log`: list of `{time, msg, level}` — last 200 entries

**Bot lifecycle:**
```
Add bot -> "stopped" -> [Backtest] -> "backtesting" -> "stopped" (with cached results)
                                                     -> [Start] -> "running" (live)
                                                     -> [Backtest] again to re-test
```
- Backtest runs `run_backtest()` with the bot's config, caches the result on the bot state
- "Start" begins the live polling loop. Backtest results remain visible for comparison.
- Running bots can be stopped at any time -> goes back to "stopped" with live results preserved
- The portfolio equity chart only includes bots with status "running" or "stopped" that have live equity_snapshots

**`BotRunner`** — async loop for one bot:
```
poll_interval = {5m: 15s, 15m: 20s, 30m: 30s, 1h: 60s}

async def _tick():
    1. Trading hours check — if outside window or in skip range, return
    2. Fetch bars via _fetch(symbol, ..., source='alpaca-iex')
    3. New bar detection — compare df.index[-1] to state.last_bar_time, skip if same
    4. Compute indicators WITH high/low (so ATR works)
    5. Check Alpaca positions for this symbol (source of truth)
    6. If NO position -> evaluate buy rules
       - Compute effective_size = allocated_capital * position_size
       - Apply dynamic sizing: if consec_sl >= threshold, reduce further by reduced_pct
       - Calculate qty = floor(effective_size / price)
       - If trailing_stop configured: place plain market buy (bot manages exits)
       - If only fixed stop_loss: place OTO buy with stop (Alpaca manages exit)
    7. If HAS position -> evaluate exits
       - Update trail_peak from bar high/close
       - Compute trail_stop_price (pct or ATR-based)
       - Check: fixed stop hit? (low <= stop_price) -> sell
       - Check: trailing stop hit? (low <= trail_stop_price) -> sell
       - Check: sell rules fired? -> sell
       - On sell: update consec_sl_count (increment for stops, reset for signal)
       - On sell: calculate trade P&L, update total_pnl, append equity_snapshot
    8. Log activity
```

**Key design decisions:**
- **Trailing stop vs Alpaca OTO:** When trailing stop is configured, the bot does NOT use Alpaca OTO orders. It manages exits itself by checking bar lows against the trailing stop price each tick. When only fixed `stop_loss_pct` is set, use Alpaca OTO (existing behavior) — Alpaca watches the price server-side.
- **Position detection via Alpaca API:** On each tick, check `get_all_positions()` for the symbol. This handles manual closes, Alpaca stop fills, etc. The bot adapts to external changes.
- **New bar detection:** Only evaluate rules when `df.index[-1]` changes. Prevents duplicate signals from repeated polls within the same bar.

**`BotManager`** — singleton managing all bots:
- `bot_fund: float` — global cap on total capital for all bots
- `set_bot_fund(amount)`, `get_fund_status()` — fund management (see Fund Management section above)
- `add_bot(config)` — validate allocated_capital fits within available fund, create bot as "stopped"
- `start_bot(bot_id)` — reject if symbol already has a running bot. On first tick, detects existing Alpaca position and resumes managing it (sets entry_price, starts trailing stop tracking).
- `stop_bot(bot_id, close_position=False)` — set status "stopped", cancel asyncio task. If `close_position=True`, also close the Alpaca position for that symbol.
- `get_bot(bot_id)`, `list_bots()`, `delete_bot(bot_id)` — delete frees allocation back to available
- `save()` — persist bot_fund + configs + state to `data/bots.json`
- `load()` — restore on startup (all bots start as "stopped")
- `shutdown()` — stop all bots (keep positions open), save state

**Stop behavior:**
- Default "Stop" keeps the open position — you might restart the bot or manage it manually
- "Stop & Close" is an explicit action that also closes the position via Alpaca
- Stopped bots with open positions show a warning in the UI with a manual [Close] button
- On restart, the bot detects existing Alpaca positions on the first tick and resumes tracking

### Step 3: Bot API endpoints — `backend/routes/bots.py` (NEW)

```
GET  /api/bots/fund         -> fund status: {bot_fund, allocated, available}
PUT  /api/bots/fund         -> set bot fund amount (rejects if < current allocations)
POST /api/bots              -> add a new bot (stopped), validates allocation fits. Returns {bot_id}
POST /api/bots/{id}/start   -> start live polling (detects existing position on first tick)
POST /api/bots/{id}/stop    -> stop polling, keep open position (safe default)
POST /api/bots/{id}/stop?close=true -> stop polling AND close open position
POST /api/bots/{id}/backtest -> run backtest with bot config, cache result on state
GET  /api/bots              -> list all bots with summary state + fund status
GET  /api/bots/{id}         -> full bot detail (config + state + backtest_result + activity log)
DELETE /api/bots/{id}       -> remove a stopped bot, frees allocation
```

Note: Fund endpoints (`/api/bots/fund`) must be registered before `/{id}` routes to avoid FastAPI treating "fund" as a bot ID.

The `/backtest` endpoint calls `run_backtest()` from `routes/backtest.py` with the bot's strategy config, then stores the result on `state.backtest_result`. This is the same backtest engine used in the Chart tab — identical results.

### Step 4: Wire into FastAPI — `backend/main.py` (MODIFY)

- Add `lifespan` context manager: load bot configs on startup, shutdown bots on exit
- Include `bots_router`

### Step 5: Frontend API client — `frontend/src/api/bots.ts` (NEW)

Functions: `startBot()`, `stopBot()`, `fetchBots()`, `fetchBotDetail()`, `deleteBot()`

Types: `BotConfig`, `BotState`, `BotSummary`, `BotActivityEntry`

### Step 6: Bot Control Center UI — `frontend/src/features/trading/BotControlCenter.tsx` (NEW)

Layout:
```
+--------------------------------------------------+
| Bot Control Center                               |
| Bot Fund: [$50,000]  Allocated: $45,000          |
| Available: $5,000  [====:::::]                   |
+--------------------------------------------------+
| [Strategy v] [Ticker] [Interval v]               |
| Allocation: [$____] (max $5,000)  Size: [1.0]   |
| [+ Add Bot]                                      |
+--------------------------------------------------+
| * FSLR "RSI 26-64"  LIVE  3 trades  +$420       |
| +------------ mini equity ------------+ [Log v]  |
| | ............  backtest vs live       | [Stop]   |
| +-------------------------------------+          |
|                                                   |
| * ENPH "RSI 28-60"  LIVE  5 trades  -$180        |
| +------------ mini equity ------------+ [Log v]  |
| | ............  backtest vs live       | [Stop]   |
| +-------------------------------------+          |
|                                                   |
| o SMCI "MACD Cross"  NEW                         |
| +-------- backtest result ------------+           |
| | +312%, 0.24 Sharpe, -28% MDD      | [Backtest]|
| | ............  (backtest equity)     | [Start]   |
| +------------------------------------+ [Delete]  |
+--------------------------------------------------+
| Portfolio Equity (live bots)       Total: +$240  |
| +----------------------------------------------+ |
| | -- FSLR (blue) -- ENPH (orange) -- Combined  | |
| +----------------------------------------------+ |
+--------------------------------------------------+
```

Each bot card shows:
- **New/stopped bots**: Backtest button + cached results (metrics + equity). Start when ready.
- **Live bots**: Real-time status, live P&L, mini equity sparkline. If backtest was run earlier, overlay the backtest equity curve (dashed) alongside the live curve (solid) for expected vs actual comparison.
- **All bots**: expandable activity log, delete button (only when stopped).

- Strategy dropdown reads from `localStorage('strategylab-saved-strategies')`
- Selecting a strategy pre-fills ticker, interval, and shows config summary
- Bot list polls `GET /api/bots` every 5 seconds
- Expanded log section polls `GET /api/bots/{id}` every 5s
- Status dot: green=running, gray=stopped, red=error

**Per-bot mini equity chart:**
- Tiny sparkline (~60px tall) using lightweight-charts `BaselineSeries`
- Data from `state.equity_snapshots` — green above 0, red below 0 (baseline at 0 since it shows cumulative P&L)
- Only renders when the bot has 2+ completed trades

**Portfolio equity chart** (below bot cards):
- Larger chart (~180px) showing combined P&L across all bots
- Per-bot overlay lines in distinct colors (blue, orange, green, purple...)
- Combined total as a thicker `BaselineSeries` (green above 0, red below)
- Data: merge all bots' `equity_snapshots` sorted by time, compute running totals
- Only shows when any bot has completed trades

### Step 7: Integrate into Paper Trading tab — `PaperTrading.tsx` (MODIFY)

Insert `<BotControlCenter />` between `<AccountBar />` and `<PositionsTable />`. All existing components stay — they show all Alpaca activity regardless of source.

### Step 8: Frontend types — `frontend/src/shared/types/index.ts` (MODIFY)

Add `BotConfig`, `BotState`, `BotSummary`, `BotActivityEntry` interfaces.

---

## Files Summary

| File | Action | Description |
|------|--------|-------------|
| `backend/routes/trading.py:275` | Modify | Pass high/low to compute_indicators |
| `backend/bot_manager.py` | **New** | Bot engine: BotConfig, BotState, BotRunner, BotManager |
| `backend/routes/bots.py` | **New** | REST API: start/stop/list/detail/delete |
| `backend/main.py` | Modify | Lifespan handler + bots router |
| `frontend/src/api/bots.ts` | **New** | API client functions + types |
| `frontend/src/features/trading/BotControlCenter.tsx` | **New** | Bot control UI |
| `frontend/src/features/trading/PaperTrading.tsx` | Modify | Add BotControlCenter |
| `frontend/src/shared/types/index.ts` | Modify | Bot-related types |

---

## Order Execution

**All orders are market orders.** This keeps bot behavior consistent with the backtest, which models slippage as a percentage on every fill.

**Buy entries:**
- `MarketOrderRequest` via Alpaca API (same as existing `trading.py` code)
- When only fixed `stop_loss_pct` is set (no trailing): use OTO bracket order — Alpaca attaches a server-side stop loss that fires even if the bot is down
- When `trailing_stop` is configured: plain market buy — the bot manages exits itself via polling (Alpaca doesn't support dynamic trailing stop level updates)

**Sell exits:**
- Signal exit (sell rules fire): `MarketOrderRequest` to close position
- Fixed stop loss (no trailing): handled by Alpaca's OTO stop order — no bot action needed
- Trailing stop: bot detects `low <= trail_stop_price` on each tick -> `MarketOrderRequest` to close
- If Alpaca's OTO stop fires before the bot's next tick, the bot sees no position on next check and logs the exit

**Paper vs live trading:**
- Alpaca paper API is identical to live — same endpoints, same order types, same fill simulation
- Only difference: `paper=True` in client constructor (hardcoded in `shared.py`)
- Going live later = one config change, no code changes

**Why not limit orders:**
- Risk of non-fill defeats the purpose of automated execution
- Adds complexity: retry logic, partial fills, timeout handling
- Backtest assumes fills at market + slippage — market orders match this model
- Can revisit later if slippage on real fills proves problematic

---

## Safety Guardrails

1. **Paper mode only** — `shared.py` has `paper=True` hardcoded. No live trading path.
2. **Fund management** — global bot fund cap prevents bots from consuming all account equity. Per-bot allocations enforce individual limits. Cannot over-allocate.
3. **One bot per symbol** — `BotManager.start_bot()` rejects duplicates.
4. **Bots start stopped on server restart** — no surprise auto-trading after a crash.
5. **Position truth from Alpaca** — bot checks real positions each tick, not internal state.
6. **Activity log** — every action is logged and visible in the UI.
7. **Journal integration** — bot trades logged via existing `_log_trade()` with `source="bot"`.

---

## Verification

1. Start the app with `./start.sh`
2. Save a strategy in the Chart tab (e.g. "FSLR RSI 26-64")
3. Switch to Paper Trading tab
4. Select the saved strategy in Bot Control Center
5. Click Start — bot should begin polling
6. Watch activity log for "New bar detected" entries
7. Verify positions appear in PositionsTable when a BUY fires
8. Verify trades appear in TradeJournal
9. Stop the bot, verify it stops polling
10. Restart the server, verify the bot loads as "stopped"

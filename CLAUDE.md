# StrategyLab — Claude Context

Interactive trading strategy backtester + live paper trading platform. Read this before touching anything.

## Working Style

- **The task description IS the context.** Don't browse to "get oriented" — if the task is unclear, ask.
- **No bulk reads.** No `cat`, no `ls *`, no reading full files to skim. Grep for the distinctive anchor, then Read only the small slice (±20 lines) around it.
- **Grep over Read** whenever searching for a specific line, pattern, or symbol.
- Work in small, focused steps. One-sentence narration before each. Commit per task, don't batch. Always push after committing.
- If hitting an error or blocker, STOP and report immediately — don't retry in a loop.
- Don't trust line numbers in docs/plans — they drift. Grep for the string anchor, then edit.
- Output reasoning progressively to avoid API stream idle timeouts; never go silent for >60s.
- **Key Bugs Fixed is authoritative.** If code appears to invite a "simpler" approach that conflicts with that section, don't take it — those patterns exist for non-obvious runtime reasons.
- **Subagent-first workflow.** Main session is a pure orchestrator — delegate ALL work to subagents. The only exception is single-line edits to tracking files (TODO.md, JOURNAL.md). Never debug, explore, or implement in the main session. The orchestrator's job is judgment (what to build, what to fix, what to defer) and verification (did agents do what was asked).
- **Orchestrator cycle (follow this for every task):**
  1. Pick task from TODO
  2. Explore+Spec (haiku) — map current code AND return a draft implementation brief. Orchestrator reviews/adjusts the brief (2s of judgment), doesn't rewrite from scratch. Saves a full orchestrator turn per task.
  3. Implement (parallel sonnet) — backend + frontend simultaneously when independent. Before dispatching, verify file independence: list target files per agent, confirm zero overlap. If files overlap, sequence those agents.
  4. Verify (orchestrator) — grep key changes, run `npm run build` (not `tsc --noEmit`), spot-check with absolute paths
  5. Review (parallel sonnet) — 4–7 persona agents via ce:review
  6. Synthesize (orchestrator) — merge findings, classify fix vs defer
  7. Fix (sonnet) — dispatch ONE fixer agent with ALL findings. A single fixer can make holistic decisions (e.g., extract a shared module that resolves 3 findings at once). Never dispatch per-finding fixers.
  8. Verify + commit (orchestrator) — run `npm run build`, check fixes, update TODO/JOURNAL atomically, push
  9. Repeat
- **Pipeline parallelism across tasks.** Don't wait for Task A's full cycle before starting Task B. While Task A is in review (the slowest phase), Task B can be in explore/implement — as long as their files don't overlap. The orchestrator tracks multiple tasks at different pipeline stages. Typical overlap: Task A in review + Task B in implement = ~2x wall-clock speedup on multi-task sessions. Use `run_in_background: true` on review agents, then dispatch the next task's explore+implement while reviews run. When review results arrive, synthesize and fix Task A, then move to Task B's review.
- **Model routing for subagents.** haiku for reads/exploration, sonnet for coding/implementation/review, opus only when complexity demands it. Set the `model` parameter on every Agent call.
- **Review dispatch rules.** Pass file paths and intent to reviewers — not diff content. Let reviewers read files themselves. Always use absolute paths in verification commands. Prefer "read these files" over "run git diff" in agent prompts — file reads don't require shell permissions.
- **Review loop for non-trivial work.** Implement → parallel review agents → synthesize → fix agent → verify. Skip for trivial (<10 line) changes. For medium changes, a single reviewer suffices. For large/multi-file changes, run parallel reviewers across correctness, maintainability, project-standards, and domain personas.
- **Anti-patterns to avoid:**
  - "Just check one thing" trap — any investigation in the main session is a delegation failure; dispatch a haiku agent instead
  - Reading full diffs into orchestrator context — wasteful; pass file paths, let reviewers gather evidence
  - Relative paths in verification — always use absolute paths; agents do not maintain working directory
  - "Run git diff" in review prompts — reviewers may lack shell access; use "read these files" instead
  - `tsc --noEmit` as verification — misses `verbatimModuleSyntax` errors that cause blank pages. Always use `npm run build` (runs `tsc -b`).
  - Implementation agents committing — agents follow CLAUDE.md literally and will commit+push. Always include "Do NOT commit or push" in implementation agent prompts. The orchestrator owns commit decisions.
  - System `python` for syntax checks — macOS system Python is 2.7. Always use `python3` explicitly.
- Visually verify UI changes in browser or flag "not visually verified." Journal to `JOURNAL.md` at session end.

## Handoff Contract

Every agent session (interactive or automated) follows this protocol to prevent cross-session drift.

### On Start
1. Read `TODO.md` — identify unchecked items, note the shipped/total count
2. Read `JOURNAL.md` (last entry only) — understand what was just shipped
3. If working on a specific TODO item, check for a linked spec/plan in `docs/superpowers/`

### On End
1. Update `TODO.md` — check off completed items, add new items if work surfaced them
2. Append to `JOURNAL.md` — reuse today's date header if it exists, one bullet per shipped item with bold **[ID]** cross-reference
3. Both updates in the **same commit** as the code changes (atomic)

### Priority tags in TODO.md
- `[next]` — highest-priority unchecked item(s), auto-picked by chain runner
- `[easy]` / `[medium]` / `[hard]` — optional difficulty hint for model routing

### Rules
- Never commit code without updating the tracking files.
- Never update tracking files without corresponding code.
- If all `[next]` items are done, fall back to unchecked items in section order (A before B, etc.).

## Chart.tsx Architecture

Key files (others are standard-named, discoverable by grep):
- `frontend/src/App.tsx` — central hub for state, data fetching, layout
- `frontend/src/features/chart/Chart.tsx` — read this section before editing
- `backend/signal_engine.py` — Rule model, eval_rules()
- `backend/bot_runner.py` — async polling loop, entry/exit/fill management
- `backend/broker.py` — TradingProvider protocol + broker registry
- `backend/journal.py` — log_trade(), compute_realized_pnl()

This is the most complex file. Read it before editing.

Three separate `IChartApi` instances rendered as a flex column:
- **Main chart** (`containerRef`) — candlesticks, SPY/QQQ overlays, EMA, BB, Volume
- **MACD pane** (`macdContainerRef`) — histogram + MACD/Signal lines
- **RSI pane** (`rsiContainerRef`) — RSI line + 70/30 reference lines

### Pane synchronization

Pan/zoom: `subscribeVisibleLogicalRangeChange` on the main chart → `setVisibleLogicalRange()` on MACD/RSI. Uses logical (bar-index) sync. Indicator data uses **whitespace entries** (`{ time }` with no `value`) for warmup bars (e.g. RSI's first 14 points) so all charts have the same bar count and stay aligned.

MACD/RSI effects sync to the main chart's logical range on mount via `getVisibleLogicalRange()`.

Price scale alignment: `syncWidths()` equalises `rightPriceScale.minimumWidth` across all three charts. Also mirrors the main chart's left axis width onto MACD/RSI as invisible left axes — otherwise MACD/RSI plot areas start further left than the main chart. Called on every range change AND via `setTimeout(100)` on initial mount.

Crosshair sync: `subscribeCrosshairMove` on each chart → `setCrosshairPosition(NaN, param.time, seriesRef)` on the other two. Requires series refs (`candleSeriesRef`, `macdSeriesRef`, `rsiSeriesRef`).

### Series priceScaleId rules (lightweight-charts v5)

In v5, `addSeries()` without an explicit `priceScaleId` creates an **independent** scale rather than sharing 'right'. Always set explicitly:
- Candlesticks, EMA, BB → `priceScaleId: 'right'`
- SPY → `priceScaleId: 'spy-scale'` (hidden, real close prices)
- QQQ → `priceScaleId: 'qqq-scale'` (hidden, real close prices)
- Volume → `priceScaleId: 'volume'` (hidden, `scaleMargins: { top: 0.75, bottom: 0 }`)


### Indicator pane height split

| Active panes | Main | Sub |
|---|---|---|
| Neither | 100% | — |
| One | 65% | 35% |
| Both | 50% | 25% each |

## Backend Notes

- **CRITICAL: Never use `yf.download()`** — it shares global state and returns wrong data under concurrent requests. Always use `yf.Ticker(symbol).history()` via the `_fetch()` helper.
- `_fetch()` auto-clamps date ranges to yfinance limits for intraday intervals (1m=7d, 5m/15m/30m=60d, 1h=730d)
- `_format_time()` returns `"YYYY-MM-DD"` strings for daily+ intervals and **unix timestamps** (seconds, UTC) for intraday — lightweight-charts requires unique timestamps per bar
- `_series_to_list()` lives in `routes/indicators.py`; preserves null values (for indicator warmup periods) so the frontend can use whitespace data for bar alignment

### Data providers

Four providers can be registered in `shared.py`:
- `yahoo` — yfinance, always available
- `alpaca` — Alpaca SIP feed (requires `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` in `backend/.env`), paid subscription for recent intraday
- `alpaca-iex` — Alpaca IEX feed, real-time, free tier, narrower coverage (no OTC)
- `ibkr` — IBKR via `ib_insync` (requires `IBKR_HOST` + `IBKR_PORT` env vars and running IB Gateway)

Both Alpaca providers use `Adjustment.SPLIT` so historical prices are always split-adjusted.

When Alpaca `end` date is today or future, the provider substitutes `now` so intraday bars aren't cut off at midnight UTC.

### `_fetch()` TTL cache

`shared.py` has an in-memory TTL cache on `_fetch()`:
- **2 min TTL** for live intraday (interval in `_INTRADAY_INTERVALS` and end ≥ today)
- **1 hour TTL** for fully historical data
- Max 100 entries; evicts expired first, then oldest on overflow
- Logs `[cache HIT]` / `[cache MISS]` to stdout for debugging
- `GET /api/cache` returns current cache state (count, entries, ages, TTLs)
- **Note:** cache is in-process memory — server restart (including `--reload` on file change) clears it

### Timezone handling in Chart.tsx

lightweight-charts v5 has **no `localization.timeZone` support**. All unix timestamps are shifted to ET wall-clock time via `toET()` before being passed to any series. `toET()` uses `Intl.DateTimeFormat` with `America/New_York` to reconstruct the timestamp as UTC so the chart displays 9:30–16:00 for NYSE hours. Daily date strings pass through unchanged.

## Signal Engine

`signal_engine.py` — rule evaluation for backtester + bot runner.

`Rule` fields: `indicator`, `condition`, `value`, `param`, `threshold`, `muted`, `negated`. Conditions include crossover, above/below, crosses_above/below, turns_up/turns_down (slope change detection).

**Rule negation (NOT):** `Rule.negated: bool`. Applied in `eval_rules()` — if `negated` and `i >= 1`, the rule result is inverted. Guard condition (`i < 1`) always returns False regardless of negation. UI: small **NOT** button on each rule row in RuleRow.tsx, orange when active.

S-G (Savitzky-Golay) smoothing for MA8/MA21 exists but is experimental — revisit only on explicit request.

## Backtester Cost Model

`StrategyRequest` cost fields:
- `slippage_bps` — unsigned modeled cost per leg (≥ 0, default 2.0 bps). Applied as `price * (1 ± drag)` directionally (longs worse on entry / better on exit, shorts inverse). All sign/unit conventions live in `backend/slippage.py` — never reinvent them. Helpers: `slippage_cost_bps(side, expected, fill) → ≥0`, `fill_bias_bps(side, expected, fill) → signed` (positive = favorable), `decide_modeled_bps(symbol) → ModeledSlippage` (policy: empirical can only floor *up* from the 2 bps default — favorable empirical never makes the backtest cheaper).
- `per_share_rate` + `min_per_order` — per-leg commission via `per_leg_commission(shares, req)` in `routes/backtest.py`. **Default `0.0` / `0.0`** (commission-free, matches Alpaca US equities). For IBKR Fixed, set `0.0035` / `0.35`.
- `borrow_rate_annual` (default `0.5` %) — annual short borrow rate. `borrow_cost(...)` computes `shares * entry_price * (rate/100/365) * hold_days` and deducts from short PnL. Zero for longs.
- Each trade carries `slippage`, `commission`, and `borrow_cost` fields. Journal rows additionally cache `slippage_bps` (unsigned cost) when `expected_price` is set.

Slippage endpoint: `GET /api/slippage/{symbol}` returns `{modeled_bps, measured_bps, fill_bias_bps, fill_count, source}` where `source` is `'default' | 'empirical'`. Frontend hook: `useSlippage` (`shared/hooks/useSlippage.ts`). StrategyBuilder displays modeled bps with source label; TradeJournal shows unsigned per-fill cost in bps; BotCard surfaces `avg_cost_bps`. Results has a Borrow column + Cost Breakdown summary (commission / borrow / slippage / total drag %).

Deferred to v2 (see TODO): debit-balance margin interest, IBKR Tiered pricing, hard-to-borrow dynamic rates, FX conversion.

## Short Selling (direction field)

`StrategyRequest` and `BotConfig` have `direction: "long" | "short"` (defaults to `"long"`). The rule engine (`eval_rules`) is **direction-agnostic** — all inversion happens at execution boundaries.

Non-obvious bits:
- Stop-loss for shorts triggers **above** entry (`high >= entry * (1 + pct)`); trailing stop tracks trough not peak.
- PnL: `(entry - exit) * shares` for shorts; trade types are `"short"` / `"cover"`.
- **No OTO brackets for shorts** — Alpaca OTO doesn't cleanly support stops above entry, so all short stops managed via polling. Same-symbol guard allows one long + one short bot simultaneously.
- `TrailingStopConfig.activate_pct` — when `activate_on_profit` is true, trailing starts only once `source_price >= entry * (1 + activate_pct/100)`. Gives positions room to breathe.

## Bot System

- `BotManager` singleton persists to `backend/data/bots.json`, loaded at FastAPI lifespan.
- `bot_runner._tick()` async loop per bot; uses `TradingProvider` abstraction — no direct broker SDK imports anywhere.
- Allocation **compounds**: `allocated_capital + total_pnl` (matches backtest). Position size hardcoded 100%.
- Journal rows tagged with `bot_id`; `compute_realized_pnl(symbol, direction, bot_id)` scopes per-bot so delete+recreate starts clean. Legacy untagged rows excluded.
- **IBKR integration (D7) shipped** — details + operational gotchas (Read-Only mode, Error 162, ib_insync rules, Pydantic route-model trap) live in memory, not here.

## Key Bugs Fixed

These document **why** certain patterns exist in the code:

- **yf.download() concurrency**: `yfinance.download()` shares global state, returns wrong data under concurrent requests. All code uses `yf.Ticker(symbol).history()` via `_fetch()`.
- **Bot P&L leak across recreations**: `compute_realized_pnl` filtered journal rows by `(symbol, direction)` only, so a new bot on the same symbol inherited the old (deleted) bot's P&L and sizing. Fixed by tagging every `_log_trade` with `bot_id` and filtering by it.
- **Silent drop of bot config fields**: `AddBotRequest` in `routes/bots.py` duplicated `BotConfig` fields; any field missing from the duplicate was silently dropped by Pydantic's `extra="ignore"` default and replaced by the `BotConfig` default. Fixed by using `BotConfig` directly as the POST body schema.
- **Chart teardown race on ticker change**: when the main chart and sibling panes (MACD/RSI/Results overlay) unmount concurrently, late callbacks can hit an already-removed `IChartApi` and throw from `paneWidgets[0]` internal state, blanking the React tree. Fixed by reading `chartRef.current` dynamically in `syncWidths` (not via closure) + try/catch body, nulling refs *before* `chart.remove()` in every cleanup, and try/catch around `setVisibleLogicalRange` / `unsubscribe*` calls on siblings. Don't "clean up" these guards.
- **Fire-and-forget notifications must use `asyncio.create_task()`, not `await`**: `await notify_*()` inside `bot_runner._tick()` blocks the polling loop — a slow or down ntfy.sh causes the bot to miss ticks (up to 10s per notification call with the httpx timeout). `create_task()` schedules the coroutine without blocking. Never `await` a non-critical side-effect in a polling loop.
- **Sync callbacks need `run_coroutine_threadsafe`, not `ensure_future`**: ib_insync dispatches error callbacks on its EReader thread, which has no running asyncio event loop. `asyncio.ensure_future()` from that thread raises `RuntimeError: no running event loop` and silently drops the notification. Fix: store `self._loop = asyncio.get_running_loop()` in the async `run()` method, then use `asyncio.run_coroutine_threadsafe(coro, self._loop)` from any sync callback.

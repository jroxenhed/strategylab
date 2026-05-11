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
- **Subagent delegation rule.** Dispatch subagents when (a) work can run in parallel, (b) the subagent has a specialized capability (named persona, dedicated agent type), or (c) the orchestrator is a higher model tier than the subagent (Opus orchestrating Sonnet implementations). Don't dispatch when the only benefit would be "follow the rule" — same-tier serial dispatch is pure overhead. The orchestrator always owns judgment (what to build, what to fix, what to defer) and verification (did the work happen as intended).
- **Orchestrator cycle (interactive sessions, Opus orchestrating Sonnet):**
  1. Pick task from TODO
  2. Explore+Spec (haiku) — map current code AND return a draft implementation brief. Orchestrator reviews/adjusts the brief (2s of judgment), doesn't rewrite from scratch.
  3. Implement (parallel sonnet) — backend + frontend simultaneously when independent. Before dispatching, verify file independence: list target files per agent, confirm zero overlap. If files overlap, sequence those agents.
  4. Verify (orchestrator) — grep key changes, run `npm run build` (not `tsc --noEmit`), spot-check with absolute paths
  5. Review (parallel sonnet) — 4–7 persona agents via ce:review
  6. Synthesize (orchestrator) — merge findings, classify fix vs defer
  7. Fix — dispatch one fixer agent when fixes need holistic decisions (extract shared helper, multi-file refactor, harmonize API contracts). Apply directly when fixes are mechanical (rename, regex tighten, single-line guard) and match the reviewer's `suggested_fix` text verbatim. Never dispatch per-finding fixers.
  8. Verify + commit (orchestrator) — run `npm run build`, check fixes, update TODO/JOURNAL atomically, push
  9. Repeat
- **Routine env (overnight builder).** Single Opus session executes end-to-end. Subagent rule applies: dispatch for parallel review (4-6 personas in one message), do sequential single-stream work (read source → edit → verify → fix) directly because there's no tier arbitrage when builder and would-be subagent are both Opus. Single-fixer pattern is optional in routine when fixes are mechanical; required when holistic. See `docs/overnight-builder-prompt-patch.md` for the procedural script.
- **Pipeline parallelism across tasks.** Don't wait for Task A's full cycle before starting Task B. While Task A is in review (the slowest phase), Task B can be in explore/implement — as long as their files don't overlap. The orchestrator tracks multiple tasks at different pipeline stages. Typical overlap: Task A in review + Task B in implement = ~2x wall-clock speedup on multi-task sessions. Use `run_in_background: true` on review agents, then dispatch the next task's explore+implement while reviews run. When review results arrive, synthesize and fix Task A, then move to Task B's review.
- **Model routing.** Orchestrator/judgment work runs Opus 4.7 (xHigh effort when available). Subagents: haiku for fast reads, sonnet for routine implementation/review (cheap parallel personas), opus 4.7 for complex review or synthesis where missing a P1 is expensive. Cost is not the primary constraint — wall-clock and quality are. Set `model` on every Agent call.
- **Review rules.** Pass file paths and intent to reviewers, not diff content. Always use absolute paths. Severity-graded review (see tiers below) — match review depth to actual risk, not item count.
- **Severity-graded review tiers.** Item tag drives default tier; aggregate diff and contract surface can promote a bundle to the next tier. Validated 2026-05-11 against F119 + F122 + F125 (24 `[easy]` items shipped across 3 interactive batches with parallel Sonnet implementers + Opus orchestrator verification + zero persona review + zero regressions — user confirmed "smoother than ever"). Personas earn tokens at the architectural/adversarial tails; Opus orchestrator covers the wide middle.
  - **Tier A — `[easy]` items, bundle diff <100 lines, no contract surface.** No persona review. Orchestrator verifies via AST parse, full test suite, spot-check each agent's claimed change vs actual file state, `npm run build` if frontend touched. Morning calibration pass (2 personas) is the safety net.
  - **Tier B — `[medium]` items OR aggregate bundle 100-300 lines OR bundle touches >5 files.** 1-2 personas: `correctness` always, plus conditional file-type reviewer (`kieran-python` for `.py` changes, `kieran-typescript` for `.ts/.tsx`). 2 max.
  - **Tier C — `[hard]` / architectural / contract changes (response_model, error wording, public API shape, auth, persistence) OR aggregate bundle >300 lines.** Current 4-6 panel (always-on 4 + conditionals based on diff).
  - **Contract-surface override.** Any of these always promote to Tier C regardless of item tag or line count: changing a Pydantic `response_model`, changing public API error wording/shape, changing auth/authz, changing persistence schema, changing TypeScript shared types, removing a public route. The blast radius is callers, not LOC.
- **Two-tier review architecture.** Builder and morning passes run in different environments and use different mechanisms — don't try to consolidate them.
  - **Overnight (builder, coverage role):** manual Task-tool dispatch is canonical. The `ce:review` skill does NOT resolve in the routine env (3 build-22 candidates failed; 11+ manual dispatches across builds 20-22 ran clean). Builder applies the severity-graded tier system above — Tier A items ship with orchestrator verification only; Tier B/C run personas. Builder dispatches happen in parallel within a tier; never run all 4 always-on personas on a Tier A bundle.
  - **Morning (calibration role):** ce:review skill works in the interactive session. 2-persona roster: **agent-native** (only persona the builder doesn't run — consistently produces novel signal on architectural drift, OpenAPI gaps, error-contract issues) + **reliability** (severity-calibration sweep: re-judge the builder's deferrals, debunk overshoot findings from other personas, do not fish for new ones). Trimmed from 4 to 2 on 2026-05-11 after PR #32 evidence — adversarial returned 2 false positives on its second pass and security found mostly pre-existing items, while agent-native produced all 3 in-PR safe fixes and reliability independently debunked one of adversarial's false positives. The builder already runs adversarial + security overnight; running them again on the same diff is duplicate cost without proportional signal. ~5 minutes: review, fix confirmed P1s, optional second-pass verification, merge.
- **Anti-patterns to avoid:**
  - "Just check one thing" trap — any investigation in the main session is a delegation failure; dispatch a haiku agent instead
  - Reading full diffs into orchestrator context — wasteful; pass file paths, let reviewers gather evidence
  - Relative paths in verification — always use absolute paths; agents do not maintain working directory
  - "Run git diff" in review prompts — reviewers may lack shell access; use "read these files" instead
  - `tsc --noEmit` as verification — misses `verbatimModuleSyntax` errors that cause blank pages. Always use `npm run build` (runs `tsc -b`).
  - Implementation agents committing — agents follow CLAUDE.md literally and will commit+push. Always include "Do NOT commit or push" in implementation agent prompts. The orchestrator owns commit decisions.
  - System `python` for syntax checks — macOS system Python is 2.7. Always use `python3` explicitly.
  - Dispatching review agents on wrong branch — always check out the PR branch or extract files to `/tmp` before dispatching. Agents that read main when reviewing a PR produce false positives (~40% false positive rate observed).
- Visually verify UI changes in browser or flag "not visually verified." Journal to `JOURNAL.md` at session end.

## Handoff Contract

Every agent session (interactive or automated) follows this protocol to prevent cross-session drift.

### On Start
1. Read `TODO.md` — identify unchecked items, note the shipped/total count
2. Read `JOURNAL.md` (last entry only) — understand what was just shipped
3. If working on a specific TODO item, check for a linked spec/plan in `docs/superpowers/`

### On End
1. Update `TODO.md` — check off completed items, add new items if work surfaced them. **Every implementation session must surface at least 1–2 new items** — if you found zero, you weren't looking hard enough. Edge cases, missing validation, UX gaps, performance concerns, and natural follow-ups all count. Tag new items with the parent task ID (e.g. B5d from B5c). New F-items must include one bucket tag: `[arch]` / `[hardening]` / `[polish]` / `[testing]` / `[infra]`. The only things that don't belong are speculative feature requests with no connection to the work you just did — those go in `IDEAS.md` (graduate to `TODO.md` once they have a clear what+why+how).
2. Append to `JOURNAL.md` — reuse today's date header if it exists, one bullet per shipped item with bold **[ID]** cross-reference
3. Tag suitable unchecked items `[next]` for the next overnight run (prefer prereqs of in-progress work, then `[easy]` items)
4. Both updates in the **same commit** as the code changes (atomic)
5. Send Slack summary (silently skips if webhook not configured):
   ```
   bash bin/slack-report.sh "Overnight build YYYY-MM-DD
   Shipped: <ID> (<title>), <ID> (<title>), ...
   Review: <P0> P0, <P1> P1, <P2> P2
   Branch: <branch-name>
   Next tagged: <IDs>
   Build: pass | fail (<reason>)"
   ```

### Priority tags in TODO.md
- `[next]` — highest-priority unchecked item(s), auto-picked by chain runner
- `[easy]` / `[medium]` / `[hard]` — optional difficulty hint for model routing

### Rules
- Never commit code without updating the tracking files.
- Never update tracking files without corresponding code.
- If all `[next]` items are done, fall back to unchecked items in section order (A before B, etc.).

### TODO.md tooling
- `bin/sync-todo-index.py` regenerates the index table, anchors, F-bucket H3 grouping, and Critical (P1) section. Idempotent.
- `.githooks/pre-commit` runs the sync automatically when `TODO.md` is staged AND auto-stamps newly added items with `(added YYYY-MM-DD)`. Install once per clone via `bin/install-hooks.sh`.
- Author just types `- [ ] **F94** Title — body. [easy] [hardening]`; the hook handles anchor + date + index regeneration.
- Half-formed ideas without a clear what+why+how go in `IDEAS.md`, not `TODO.md`. Graduate to `TODO.md` once specifics exist.

## Chart.tsx Architecture

Key files (others are standard-named, discoverable by grep):
- `frontend/src/App.tsx` — central hub for state, data fetching, layout
- `frontend/src/features/chart/Chart.tsx` — read this section before editing
- `backend/signal_engine.py` — Rule model, eval_rules()
- `backend/bot_runner.py` — async polling loop, entry/exit/fill management
- `backend/broker.py` — TradingProvider protocol + broker registry
- `backend/journal.py` — log_trade(), compute_realized_pnl()

Three `IChartApi` instances as a flex column (main + sub-panes). Read Chart.tsx before editing.

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

## Backend Notes

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

`shared.py` has an in-memory TTL cache on `_fetch()` (2 min intraday, 1 hour historical). **Cache is in-process memory — server restart clears it.** `GET /api/cache` for diagnostics.

### Timezone handling in Chart.tsx

lightweight-charts v5 has **no `localization.timeZone` support**. All unix timestamps are shifted to ET wall-clock time via `toET()` before being passed to any series. `toET()` uses `Intl.DateTimeFormat` with `America/New_York` to reconstruct the timestamp as UTC so the chart displays 9:30–16:00 for NYSE hours. Daily date strings pass through unchanged.

## Signal Engine

`signal_engine.py` — rule evaluation for backtester + bot runner.

`Rule` fields: `indicator`, `condition`, `value`, `param`, `threshold`, `muted`, `negated`. Conditions include crossover, above/below, crosses_above/below, turns_up/turns_down (slope change detection).

**Rule negation (NOT):** `Rule.negated: bool`. Applied in `eval_rules()` — if `negated` and `i >= 1`, the rule result is inverted. Guard condition (`i < 1`) always returns False regardless of negation. UI: small **NOT** button on each rule row in RuleRow.tsx, orange when active.

## Backtester Cost Model

`StrategyRequest` cost fields:
- `slippage_bps` — unsigned modeled cost per leg (≥ 0, default 2.0 bps). Applied as `price * (1 ± drag)` directionally (longs worse on entry / better on exit, shorts inverse). All sign/unit conventions live in `backend/slippage.py` — never reinvent them. Helpers: `slippage_cost_bps(side, expected, fill) → ≥0`, `fill_bias_bps(side, expected, fill) → signed` (positive = favorable), `decide_modeled_bps(symbol) → ModeledSlippage` (policy: empirical can only floor *up* from the 2 bps default — favorable empirical never makes the backtest cheaper).
- `per_share_rate` + `min_per_order` — per-leg commission via `per_leg_commission(shares, req)` in `routes/backtest.py`. **Default `0.0` / `0.0`** (commission-free, matches Alpaca US equities). For IBKR Fixed, set `0.0035` / `0.35`.
- `borrow_rate_annual` (default `0.5` %) — annual short borrow rate. `borrow_cost(...)` computes `shares * entry_price * (rate/100/365) * hold_days` and deducts from short PnL. Zero for longs.
- Each trade carries `slippage`, `commission`, and `borrow_cost` fields. Journal rows additionally cache `slippage_bps` (unsigned cost) when `expected_price` is set.

Slippage endpoint: `GET /api/slippage/{symbol}` returns `{modeled_bps, measured_bps, fill_bias_bps, fill_count, source}`.

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

# StrategyLab TODO

Open items only. Completed work lives in [TODO-archive.md](TODO-archive.md). F### is a monotonic item counter — bucket tags carry categorization; never add new prefix letters. Ideas without a clear what+why+how live in [IDEAS.md](IDEAS.md).

---

## Critical (P1)

_(none open)_

## Up Next

- [F303](#f303) — [next] Tracking-toolchain self-test [easy]
- [F304](#f304) — [next] close-batch `## New` support for [gated:] items [easy]

## Open Work — 27 items

| Section | Open | IDs |
|---|---|---|
| [Features](#features) | 1 | [B9](#b9) |
| [Architecture](#architecture) | 6 | [A8](#a8), [F25](#f25), [F170](#f170), [F188](#f188), [F199](#f199), [F272](#f272) |
| [Polish](#polish) | 1 | [F299](#f299) |
| [Testing](#testing) | 4 | [D24b](#d24b), [F161](#f161), [F211](#f211), [F303](#f303) |
| [Infra](#infra) | 5 | [F97](#f97), [F300](#f300)–[F302](#f302), [F304](#f304) |
| [Deferred (gated)](#deferred-gated) | 10 | [C29](#c29), [C31](#c31), [F2](#f2)–[F3](#f3), [F7](#f7)–[F8](#f8), [F10](#f10), [F63](#f63), [F205](#f205), [F280](#f280) |

## Features

- [ ] <a id="b9"></a> **B9** Cost model v2 (deferred from B6): [features]
  - Debit-balance-aware margin interest for shorts (charge margin rate only on days net cash is negative)
  - IBKR Tiered pricing (exchange fees, SEC fee, FINRA TAF, clearing pass-throughs)
  - Hard-to-borrow dynamic rate feed
  - FX conversion cost

## Architecture

- [ ] <a id="a8"></a> **A8** Chart performance — large dataset optimizations (100K+ 5-min bars): [arch]
  - [x] Equity curve detail mode downsample: root cause was missing `toDisplayTime()` shift on equity timestamps — raw UTC timestamps didn't match the main chart's ET-shifted timestamps, breaking crosshair sync and bucket alignment. Fixed by adding `toDisplayTime` to `shared/utils/time.ts` (mirrors Chart.tsx `toET`) and applying it to equity/baseline/trade-tick timestamps in Results.tsx before downsampling. `downsampleEquity()` itself was always correct.
  - [x] Equity curve detail mode sync: pixel-perfect alignment with main chart via bar-count matching. Passed OHLCV timestamps (`mainTimestamps` prop) from App.tsx, built equity/baseline/tick data with same bar positions (whitespace entries for missing values), logical-range sync. Five sub-fixes: `timeVisible`, baseline bar-matching, tick snapping, deferred width sync, invisible left axis.
  - [ ] Viewport-only rendering — only pass the visible bar range to indicator series and markers instead of all 100K bars. lightweight-charts handles panning via `subscribeVisibleLogicalRangeChange`; feed data on demand. Estimated 500-700 lines across 5-6 files (Chart.tsx, SubPane.tsx, useOHLCV.ts, chartUtils.ts, backend /api/indicators); multi-session task. [hard]
  - [ ] Off-screen downsampling — when zoomed out to show all bars, aggregate to coarser resolution (e.g. 15m/1h) for rendering, switch to full resolution on zoom-in. Reduces object count 10-50x at wide zoom. [next][medium]

- [ ] <a id="f25"></a> **F25** WebSocket bar streaming — replace `_fetch()` polling with Alpaca's real-time bar/quote WebSocket streams. Bots react on each new bar instantly instead of polling. Eliminates the `_fetch()` network latency bottleneck (~200-500ms) that currently caps effective tick rate regardless of poll interval. [hard] [arch]

- [ ] <a id="f170"></a> **F170** Vectorize `eval_rule` to read from pre-extracted numpy arrays. F162 vectorized the OHLCV reads in `run_backtest`'s outer bar loop but left `eval_rule` (called per-bar per-rule) untouched on purpose — it's also called from `bot_runner`, `signal_trace`, `rule_signals`, and the dual-mode regime path, so the contract is wider than just the backtester. cProfile showed `eval_rule` cumulative time was 20% of a 24-combo daily grid; at 5m × 4000 bars the per-bar pandas-indexing inside it dominates per-backtest cost (~270 ms/backtest on intraday vs ~10 ms after F163's cache win). Approach: precompute a numpy-array view of each indicator series once at the top of `run_backtest`, pass a lightweight `IndicatorArrays` namedtuple to `eval_rule(rule, arrays, i)`, replace internal `series.iloc[i]` / `series[i-1]` reads with `arr[i]` / `arr[i-1]`. Carefully preserve crossover detection (needs `i-1` access; index 0 must short-circuit). Update all four callers (`run_backtest`, `bot_runner._tick`, `signal_trace` builder, dual-mode regime check). Expected per-backtest cost on 5m 4000-bar workload: 270 ms → ~80 ms. Compounds with F166. [hard] [arch] (added 2026-05-12; deferred risk-isolated work — touches the most-called function in the system)

- [ ] <a id="f188"></a> **F188** Bots only support intraday intervals (1m–1h); daily strategies cannot be deployed. `INTERVALS = ['1m', '5m', '15m', '30m', '1h']` is hardcoded in `frontend/src/features/trading/AddBotBar.tsx:8`. Strategies backtested on daily bars (the timeframe where most documented edges — Connors RSI(2), MACD crossovers, etc. — actually work) cannot deploy because the bot polling loop only handles intraday cadence. Reproduced 2026-05-13: KO RSI(2) strategy with Sharpe 0.46 on daily bars collapses to Sharpe 0.05 / 34.5% cost drag on 1h bars (824 round-trips overwhelm the per-trade edge). Two options: (a) extend bot infrastructure to handle daily/EOD scheduling (real architecture work — needs cron-like daily tick instead of polling), or (b) add a clear "Daily strategies — backtest only, not deployable as bot" warning in the strategy save flow so users don't waste cycles tuning a daily strategy they can't ship. (a) is the right long-term answer; (b) closes the gap immediately. [hard] [arch] (added 2026-05-13) (option (b) shipped 2026-06-04 in F-BATCH-0604C — save-flow warning via shared BOT_DEPLOYABLE_INTERVALS; (a) EOD bot scheduling remains the open item)

- [ ] <a id="f199"></a> **F199** Middleware-level request deadline — the architecturally preferred option (c) from F127's design discussion. Today only `/api/backtest/quick/batch` enforces a wall-clock budget; `/api/backtest`, `/api/backtest/walk_forward`, `/api/optimize`, `/api/sensitivity`, `/api/scan`, and the bot-management routes have no upper bound on total response latency. A pure-ASGI middleware (mirroring F86's `BodySizeLimitMiddleware`) could set per-route deadlines via a route-tag → seconds dict, fall back to a sane global default, and respond 504 once exceeded. Trade-offs: (a) inherently can't cancel sync route work mid-call — would need cooperative checkpoints in expensive handlers like `run_backtest`; (b) needs a route-tagging convention so different routes can carry different budgets (intraday quote → 5s, batch backtest → 30s, WFA → 300s); (c) makes the partial-results contract (F127's `error="deadline exceeded"` per-symbol row) inapplicable for non-batch routes — they'd just 504. Worth designing before the next per-route timeout request lands. (from F127 build 28 — option (c) deferred) [hard] [arch] (added 2026-05-13)

- [ ] <a id="f272"></a> **F272** Inspector panel for node params — when a node has >3 params or long values (e.g. multi-line code blocks for Code nodes), inline editing gets cramped. Right-side panel shows selected-node form; selection ring already in place (F265). Defer until F269+F271 expose nodes that actually need it. (added 2026-05-25, from F268 plan §6). [medium] [arch]

## Hardening

_(none open)_

## Polish

- [ ] <a id="f299"></a> **F299** rAF handles discarded in F297 cancel handlers (BotCard cancelResetPnlExpanded, BotControlCenter cancelStopClose) — benign today (focus-only, null-guarded) but cancelAnimationFrame is structurally impossible if the callbacks grow; store handle + cancel on unmount. (from F-BATCH-0604C review TS-02) [easy] [polish]

## Testing

- [ ] <a id="d24b"></a> **D24b** Regime bot visual verification — D24 not visually verified. Need to run a regime bot in paper trading to confirm flip sequence, pending_regime_flip retry, and BotCard regime status display. Manual QA item. [testing]

- [ ] <a id="f161"></a> **F161** Visual smoke verification for C28 walk-forward — manual QA. Run a known overfit strategy (e.g. over-tuned RSI threshold on AAPL daily 2020-2024) and confirm: WFE < 0.5, multiple windows tagged `"spike"`, stitched equity chart renders without sawtooth, per-window table shows IS/OOS Sharpe divergence, `low_windows_warn` callout appears when configured for ≤5 windows. Then run a robust 50/200 MA crossover and confirm WFE > 0.5 with some `"stable_plateau"` tags. C28 shipped with `npm run build` clean + 39 backend + 8 frontend tests but was not visually verified in-session. [easy] [testing] (added 2026-05-12)

- [ ] <a id="f211"></a> **F211** F206 audit found `close_all_positions` (line ~258) delegates to `provider.close_all_positions()` (a broker primitive) with no per-symbol direction probe. Verify the IBKR and Alpaca primitives actually buy-to-cover shorts correctly when called from this endpoint — particularly IBKR's `close_all` if it exists, since the SMART-routing trap previously hid this kind of bug. May require a paper-trading test (open one long + one short on the same broker, then hit the Close-All button, check both close cleanly + journal correctly). (from F206 audit follow-up 2026-05-15) [medium] [hardening]
- [ ] <a id="f303"></a> **F303** [next] Tracking-toolchain self-test — fixture-based test (tmp copies) that runs sync-todo-index + close-batch (Close+New) + archive-todo end-to-end and asserts structure invariants; the TODO-REDESIGN review found a P0 (close-batch targeting a dissolved section) that a fixture test would have caught pre-review. [easy] [testing]

## Infra

- [ ] <a id="f97"></a> **F97** [medium] Provision `backend/venv/` in routine builder container — overnight builds 21/22/23 all hit the same gap: §3.5 backend smoke test originally specified `cd backend && venv/bin/uvicorn …` but the routine container ships without a venv. Spec now codifies AST + import-time check as the substitute. Real fix: the container image includes `backend/venv/` with pinned deps (Pydantic, FastAPI, pytest). Once landed, restore the full uvicorn smoke test path. Container/infra change, not application code. (from build 23 process review) [infra]

- [ ] <a id="f300"></a> **F300** Manifest-mode per-view budget in render-probe.mjs — a many-view manifest or long settleMs/canvas-mutation durations can blow the 120s node deadline, which fires 15s before verify-batch's 135s bash watchdog (zombie window); add per-view timeout + make the two deadlines explicitly coupled. (from F-BATCH-0604C review REL-06) [medium] [infra]

- [ ] <a id="f301"></a> **F301** executeTrigger drag support in render-probe.mjs — manifest triggers are click/input/navigate only, so drag-based panel-resize checks (the original F249c resize-delta) aren't replayable; F-BATCH-0604C had to substitute a collapse/expand round-trip. Add a `drag` trigger (mouse down/move/up between two selectors/offsets). (from F-BATCH-0604C manifest authoring) [medium] [infra]

- [ ] <a id="f302"></a> **F302** Per-reviewer effort cap in dispatch prompts — F-BATCH-0604C's correctness reviewer ran 5m47s / 60k tokens / 55 tool-uses vs siblings' ~2m / ~13-29, and with no agent-messaging tool the orchestrator could only poll (~15 min of wall-clock went to waits). Add a standard cap clause to review dispatch prompts (e.g. "≤25 tool calls / ~3 min; if hit, return partial findings + status token") and record cap-hits in run-state so chronic offenders surface in the report. [easy] [infra]
- [ ] <a id="f304"></a> **F304** [next] close-batch `## New` support for [gated:] items — manifest items carrying a [gated: …] tag should insert under `## Deferred (gated)` instead of their bucket section. [easy] [infra]

## Deferred (gated)

- [ ] <a id="c29"></a> **C29** Cluster-centroid IS selector — upgrade C28's neighborhood-stability tag to true cluster-based parameter selection (k-means on the IS grid with Silhouette Score to pick k, k=1 fallback to median). Practitioner-preferred over Sharpe-peak per Harbourfrontquant / QuantBeckman / BuildAlpha; centroid of the largest high-performing cluster is more robust than the peak point. Justification gate: ship only once C28's `"spike"` tag rate in practice is high enough to warrant the added complexity (k-means + Silhouette + k=1 handling on a 1000-point max grid). Until then C28's cheap neighborhood tag covers the same anti-overfit signal. [medium] (from C28 deferred design decision) (added 2026-05-12) [gated: C28 spike-tag rate proves high in practice]

- [ ] <a id="c31"></a> **C31** Calendar-term window inputs for walk-forward — accept skfolio-style pandas-offset strings (`"3MS"` = 3-month start, `"QS"` = quarter start) in addition to bar-count for `is_bars`/`oos_bars` on C28. Practitioners think in calendar terms ("12 months in, 3 months out"), not bar counts, especially when comparing strategies across timeframes. Defer until bar-count proves awkward in practice — `pd.tseries.offsets` parsing + bar-count conversion per interval is non-trivial validation surface. [easy] (from C28 deferred design decision) (added 2026-05-12) [gated: bar-count windows prove awkward in practice]

- [ ] <a id="f2"></a> **F2** Group backend broker files into a `backend/brokers/` package **and** split `broker.py` (~680 lines) into `brokers/{yahoo,alpaca,ibkr}.py` behind the existing `TradingProvider` protocol. `broker_aggregate.py`, `broker_health.py`, `broker_health_singleton.py` move into the same package. Preserve `broker_health_singleton.py` as a separate module — it exists to break an import cycle, not as ceremony. Low value, moderate risk — only tackle if friction shows up when editing a single provider. [arch] [gated: single-provider editing friction appears]

- [ ] <a id="f3"></a> **F3** Split `frontend/src/features/strategy/Results.tsx` (~745 lines) and `StrategyBuilder.tsx` (~578 lines) into smaller subcomponents (equity/drawdown/scatter/tables for Results; rule sections for StrategyBuilder). Same tradeoff as F2 — defer until a change actually gets painful. [arch] [gated: a Results/StrategyBuilder change actually gets painful]

- [ ] <a id="f7"></a> **F7** Sub-group `frontend/src/features/trading/` (10 files). Natural split: bot-management (`AddBotBar`, `BotCard`, `BotControlCenter`, `MiniSparkline`) vs account-view (`AccountBar`, `PositionsTable`, `OrderHistory`, `TradeJournal`) vs shared (`BrokerTag`), with `PaperTrading.tsx` staying as the feature entry. Defer until the feature grows further — 10 siblings is borderline, not painful yet. [arch] [gated: the trading feature grows further]

- [ ] <a id="f8"></a> **F8** API contract drift watch. `BotConfig`, `StrategyRequest`, etc. are manually mirrored between Pydantic (backend) and `shared/types/index.ts` (frontend). Fine at ~35 types; once drift bites (fields silently dropped via the `AddBotRequest` bug), switch to generating TS types from FastAPI's OpenAPI schema via `openapi-typescript`. Flag only — don't preempt. [arch] [gated: type drift bites again]

- [ ] <a id="f10"></a> **F10** Multi-user overhaul — proper auth + per-user data namespacing, SQLite instead of JSON for `bots` / `trade_journal`, session handling in the frontend. Don't pursue unless there's real demand for running this as a shared service; it's a different product shape from the current personal tool. [arch] [gated: real multi-user demand]

- [ ] <a id="f63"></a> **F63** `bot_manager.py` (603 lines) and `backtest.py` (937 lines) exceed the 500-line guideline — F21 (`bot_runner.py` split) was a prior TODO; status of those splits unclear. `backtest.py` is nearly 2x the soft cap and has no split planned. Re-evaluate whether the splits would meaningfully reduce cognitive load or just shuffle deckchairs. [hard] (from architectural audit 2026-05-10) [arch] [gated: next architectural audit]

- [ ] <a id="f205"></a> **F205** F200's per-symbol watchdog uses `ThreadPoolExecutor(max_workers=1)` — the executor is reused for the whole request but only ever runs symbols serially. For large batches (up to 500 symbols), the wall-clock budget could be spent running symbols *concurrently* with each `submit()` carrying its own timeout. Real work: (a) env-configurable `max_workers` (default still 1 to preserve today's rate-limit-friendly behavior), (b) preserve deterministic result ordering by tracking `(idx, future)` pairs and writing into a pre-sized `results[idx]` slot, (c) decide how `deadline_hit` propagates when futures complete out of order (probably: keep current behavior — once monotonic clock crosses the deadline, drain submitted futures with very short individual timeouts, then short-circuit the rest). Defer until 500-symbol batches are observably slow in practice. (from F200 follow-up 2026-05-15) [hard] [arch] [gated: 500-symbol batches observably slow]

- [ ] <a id="f280"></a> **F280** Validate (or fix) the Slack "Review cost" measurement before deciding overnight review parallel-vs-sequential — the overnight builder defaults to **parallel** persona dispatch; sequencing-within-5-min to save cache writes is only worth adopting if the data shows it pays off, and the empirical signal is the Slack-report "Review cost" line (`bin/slack-report.sh` + the per-persona `usage` token sums). Webhook confirmed working 2026-06-04 (interactive F187 report delivered); token-summing automated 2026-06-04 — `run-state.py add-agent` now takes `--tokens/--tool-uses/--duration-ms` (transcribe the Agent result's usage block verbatim) and `report <id>` emits the per-agent table with a review-role subtotal. Remaining: run a few overnight builds each way to get real numbers. Until then, parallel stays the default — don't serialize reviews on the unvalidated cache-cost hypothesis. (from the 2026-06-03 doc-cleanup; resolves the C1 collision between the old prompt-patch and the playbook.) [infra] [gated: a few overnight builds run each way]


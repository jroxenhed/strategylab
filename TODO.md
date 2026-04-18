# StrategyLab TODO

Themed roadmap. Each section lists active work first, then a **Shipped** block preserving history. Items are indexed **Section Letter + Number** (e.g. B3) for easy reference.

| Section | Topic |
|---------|-------|
| **A** | Charts & Indicators |
| **B** | Strategy Engine & Rules |
| **C** | Strategy Summary & Analytics |
| **D** | Bots (live trading) |
| **E** | Discovery |
| **F** | Architecture & housekeeping |

---

## A — Charts & Indicators

- [ ] **A1** Portfolio equity chart (combined P&L across bots)
- [ ] **A3** Equity curve trend analysis (open-ended — define "trend" first)
- [ ] **A4** More indicators — ATR, Stochastic, VWAP (chart display + backtest rules)
- [ ] **A6** Watchlist — save/switch between tickers quickly

### Shipped
- [x] Equity curve macro mode for long timescales / thousands of trades — resampled equity chart (D/W/M/Q/Y) via `MacroEquityChart.tsx` + `/api/backtest/macro`
- [x] Date range presets (D/W/M/Q/Y) + period stepping arrows (‹ › single, « » 5x skip)
- [x] Equity curve: normalised B&H comparison toggle + log scale toggle
- [x] MA8 / MA21 with SMA/EMA/RMA type selector + S-G smoothed variants (independent window/poly per MA, raw curve toggles, dashed S-G lines)
- [x] Backtest equity curve: baseline (buy & hold) overlay toggle

## B — Strategy Engine & Rules

- [ ] **B2** Pre-market / extended hours option
- [ ] **B4** Per-rule signal visualization toggles — eye icon on each rule row in strategy builder; when enabled, that rule's signals show as markers on the main chart during/after backtest. Replaces current hardcoded signal marker behavior. State stored with rule fields, persists with save/load. No global master toggle.
- [ ] **B5** Borrow cost estimation (for live short positions on real accounts)
- [ ] **B8** Spread-derived slippage default (follow-up to B7) — pull live bid/ask spread from broker (Alpaca quote endpoint / IBKR market data) and default modeled slippage to half-spread for the symbol. More principled than global/empirical fallback, especially for illiquid names. Only viable providers that expose quotes.
- [ ] **B9** Cost model v2 (deferred from B6):
  - Debit-balance-aware margin interest for shorts (charge margin rate only on days net cash is negative)
  - IBKR Tiered pricing (exchange fees, SEC fee, FINRA TAF, clearing pass-throughs)
  - Hard-to-borrow dynamic rate feed
  - FX conversion cost
- [ ] **B10** Skip-on-wide-spread entry gate — frontend wiring. Backend shipped: `BotConfig.max_spread_bps` + pre-entry quote check in `bot_runner` (exits always execute, entries skip when spread exceeds cap). Expose as: (1) an input on bot creation (AddBotBar) with a sensible default, and/or (2) an editable field on BotCard (same affordance as allocation/strategy edit). Goal: change the cap without editing `bots.json`.
- [ ] **B11** Saved-strategy library UX — the flat list in StrategyBuilder (`strategylab-saved-strategies` in localStorage) gets unwieldy as presets accumulate. Add renaming (inline edit on the preset row) plus some organising affordance — folders/tags, pin-to-top, or simple drag-to-reorder. Pick whichever has the lowest blast radius on the existing save/load flow; avoid redesigning the storage schema unless organising demands it.

### Shipped
_Older items predate the numbering scheme; new entries tagged with their letter+number._
- [x] **B1** Skip N trades after SL + configurable DS trigger — `BotConfig.skip_after_stop` + `BotState.skip_remaining`, shared `is_post_loss_trigger` helper, honored by both backtester and bot runner; StrategyBuilder skip-after-stop block + DS trigger selector; AddBotBar passes through from preset.
- [x] **B3** New rule conditions — MA8/MA21 slope (`turns_up`/`turns_down`) + `decelerating` via Savitzky-Golay second derivative; N-bar lookback for slope confirmation + min move % threshold; backtest respects sidebar S-G toggles.
- [x] **B6** Realistic cost model in backtester — IBKR Fixed per-share commission (`per_share_rate` + `min_per_order`), empirical per-symbol slippage via `GET /api/slippage/{symbol}` + `useEmpiricalSlippage` hook, short borrow cost (`borrow_rate_annual`). Results shows Borrow column + Cost Breakdown block.
- [x] **B7** Slippage model redesign — separate *measured* slippage (diagnostics) from *modeled* slippage (backtest assumption). Always ≥ 0 everywhere it surfaces. Floor empirical at default, gate on minimum fill_count, single shared signed-cost helper in `backend/slippage.py`. Fixes journal display (wrong sign for sells/shorts), bot runner log sign drift, and the "favorable empirical auto-carries into Capital & Fees" pitfall. [plan](docs/superpowers/plans/2026-04-15-b7-slippage-redesign.md)
- [x] Implement shorting — direction field, backtest + bot runner, chart markers, bot card refresh

## C — Strategy Summary & Analytics

- [ ] **C1** Merge stat column into waterfall (replace left min/avg/max column with inline min↔max range indicator under each waterfall row)
- [ ] **C2** Show B&H as alpha (single "Alpha vs B&H" metric instead of parallel Return / B&H Return)
- [ ] **C3** Sharpe orange band for 0.5–1; dampen Max DD color when <10%
- [ ] **C4** Histogram zero line + min/max tick labels

### Shipped
- [x] Summary readability pass — dropped `(mean)` suffix on EV/PF, renamed Max/Min gain/loss → Biggest/Smallest win/loss, added size hierarchy to top metrics row (Return + Final Value 22px primary, rest 13px secondary), removed median secondary values from avg rows
- [x] Expected value / trade + profit factor — EV + PF headline numbers, 3-row decomposition waterfall (Wins / Losses / Net) inline with StatRows + histogram, mean/median toggle dropped in favor of inline dual values
- [x] Strategy summary: min/max/avg gain and loss

## D — Bots (live trading)

- [ ] **D1** Bot log timezone: shows ET, user is in Sweden — use browser local time
- [ ] **D2** Bot reordering/grouping (drag vs explicit groups vs tags)
- [ ] **D4** Clean up dead `BotState.total_pnl` field once migration is safe (currently kept for legacy `bots.json` deserialization)
- [ ] **D5** Journal helper call frequency — runs on bot tick (for sizing) and on summary fetch. Journal is JSON-parsed each time. Fine for now, but if it gets slow (thousands of entries) add an mtime-based cache.
- [ ] **D9** Partial-position reconciliation logging — `bot_runner._tick()` only flags `external` when broker qty drops to 0; silent shrinkage (e.g. Apr 16 BABA short went 37 → 1 overnight, likely Alpaca HTB buy-in) is invisible in the journal. Detect `broker_qty` deltas between ticks without a matching bot order and write an `external_partial` row with the delta + timestamp so overnight forced-covers / manual edits are auditable in real time.

### Shipped
_Older items predate the numbering scheme; new entries tagged with their letter+number._
- [x] **D3** Bot lifecycle vs journal: soft reset via `BotConfig.pnl_epoch` — "Reset P&L" button on BotCard bumps epoch to now, journal untouched, displayed P&L/trades/slippage filter by `since=epoch`. TradeJournal marks bot rows with null/deleted `bot_id` as "orphan" so audit history stays visible.
- [x] **D8** IBKR reliability overhaul — subscribe to `ib.errorEvent` with structural vs transient classification (structural rejects auto-pause bots with reason), reconnect deduplication via asyncio.Lock, retry with exponential backoff (3 attempts, 1s/2s/4s), 3s TTL cache on positions/account/orders (invalidated on mutations + reconnect), removed per-call reqCurrentTime ping (HeartbeatMonitor handles liveness). Follow-up: order-ID error filtering (one bot's reject doesn't pause others), auto-resume after transient failures (30s backoff instead of permanent death), adaptive UI polling (10s backoff when broker unhealthy), dismissible broker-unhealthy banner + per-bot pause_reason display.
- [x] IBKR stability pass — heartbeat auto-reconnect on ping failure (rotates clientId to dodge Error 326 stale-slot trap), robust startup with clientId rotation + clean shutdown disconnect, `ib.portfolio()` instead of `ib.positions()` so live price/market value/unrealized P&L populate, ping awaits Future on shared loop. UI: PositionsTable keyed by `symbol|broker|side` to avoid React reconciliation collapsing same-symbol rows, opened-time fallback for pre-broker-tagging journal rows. Bot runner: post-close position re-check prevents phantom SELL rows when IBKR silently rejects an order.
- [x] **D6 + D7** IBKR heartbeat + multi-broker union for positions/orders/journal. HeartbeatMonitor pings providers every 5s; `/api/broker` exposes health + warmup. Positions/orders aggregate across brokers via `aggregate_from_brokers` (skipping unhealthy), journal trades stamped with `broker`. UI: Broker column + BrokerTag health dot, per-table broker filter (persisted), dismissible stale-broker banner. [plan](docs/superpowers/plans/2026-04-14-multi-broker-positions.md) · [spec](docs/superpowers/specs/2026-04-14-multi-broker-positions-design.md)
- [x] IBKR broker integration — full data + trading provider via `ib_insync`. TradingProvider protocol abstracts Alpaca/IBKR behind unified interface. Global broker selector (data source stays per-request). Enables simultaneous long+short on same symbol. [spec](docs/superpowers/specs/2026-04-13-ibkr-broker-integration-design.md)
- [x] Paper trading polish — Journal: reason colors fixed, Expected/Gain% columns, summary row, filter relocated, auto-refresh 5s, CSV export. Bot cards: heartbeat dot. Positions: 5s poll, Opened/Side columns.
- [x] Verify allocation logic — was position_size=10.0, added validator to clamp 0.01–1.0
- [x] Verify SL fill detection — code verified, will confirm on next live SL trigger
- [x] Does algo wait for candle close? — No, and that's fine. OTO SL is server-side (instant), trailing stop benefits from frequent checks
- [x] Buying amount compounds P&L (allocated_capital + total_pnl), matching backtest behavior
- [x] Refresh button on journal
- [x] Increase UI update frequency (5s → 2s for bot list + detail polling)
- [x] Track actual slippage — poll Alpaca fill price, log expected vs actual, show in journal
- [x] Position size: removed slider, hardcoded to 100%
- [x] Manual buy on bot to start a position
- [x] Make allocation and strategy editable in-place on bot card (click when stopped)
- [x] Global start/stop all bots
- [x] Bot sparkline: global toggle for local vs aligned timescale

## E — Discovery

Own multi-session research project. Needs its own design work before implementation.

- [ ] **E1** Scan for good StrategyLab candidates (criteria TBD)
- [ ] **E2** Batch backtesting (efficiency-critical)
- [ ] **E3** AI/ML assisted parameter tweaking
- [ ] **E4** Pipeline: present candidates → spawn bot army
- [ ] **E5** Reframe SignalScanner as research, not execution. Current page duplicates bot logic (manual Buy/Sell, own position size + stop) and skips direction, slippage/cost model, trailing stops, journal scoping, broker/data-source choice. Replace with: pick a saved strategy (no inline rule editing), show live signal + mini backtest stats per ticker (return / Sharpe / win rate over configurable lookback), per-row action becomes **Spawn Bot** that pre-fills AddBotBar (symbol, strategy, broker, data source). Drop Position $ and Stop Loss % inputs entirely. Prereq for E4.

### Shipped
- [x] Clean up bot page, move signal scanner to new Discovery page

## F — Architecture & housekeeping

- [ ] **F1** Rename "Paper Trading" to something cool
- [ ] **F2** Group backend broker files into a `backend/brokers/` package **and** split `broker.py` (~680 lines) into `brokers/{yahoo,alpaca,ibkr}.py` behind the existing `TradingProvider` protocol. `broker_aggregate.py`, `broker_health.py`, `broker_health_singleton.py` move into the same package. Preserve `broker_health_singleton.py` as a separate module — it exists to break an import cycle, not as ceremony. Low value, moderate risk — only tackle if friction shows up when editing a single provider.
- [ ] **F3** Split `frontend/src/features/strategy/Results.tsx` (~745 lines) and `StrategyBuilder.tsx` (~578 lines) into smaller subcomponents (equity/drawdown/scatter/tables for Results; rule sections for StrategyBuilder). Same tradeoff as F2 — defer until a change actually gets painful.
- [ ] **F4** Frontend test harness. Backend has 12 test files, frontend has 0. Given the bug history in Chart.tsx (teardown race, pane sync, mount-once refactor) and the polling code (AbortController fixes, adaptive interval), a small Vitest + React Testing Library setup with smoke tests on `api/client`, `useOHLCV`, and a Chart mount/unmount cycle would catch whole-tree regressions early. Start narrow on the areas that already burned — don't chase coverage.
- [ ] **F6** Split `frontend/src/shared/types/index.ts` (350 lines, 35 exports) by domain: `types/chart.ts`, `types/strategy.ts`, `types/trading.ts`, with `index.ts` as a barrel re-export. Low risk (edit imports), improves greppability when hunting for a specific interface.
- [ ] **F7** Sub-group `frontend/src/features/trading/` (10 files). Natural split: bot-management (`AddBotBar`, `BotCard`, `BotControlCenter`, `MiniSparkline`) vs account-view (`AccountBar`, `PositionsTable`, `OrderHistory`, `TradeJournal`) vs shared (`BrokerTag`), with `PaperTrading.tsx` staying as the feature entry. Defer until the feature grows further — 10 siblings is borderline, not painful yet.
- [ ] **F9** Env-var override for data directory — add a `STRATEGYLAB_DATA_DIR` env var (default `backend/data/`) threaded through `journal.DATA_DIR`, `bot_manager.DATA_PATH`, and `routes/trading.WATCHLIST_PATH`. Lets a single checkout point at `~/.strategylab/` or a synced folder so bots/journal survive `git clean` and move cleanly between machines. Single-user scope only.
- [ ] **F10** Multi-user overhaul — proper auth + per-user data namespacing, SQLite instead of JSON for `bots` / `trade_journal`, session handling in the frontend. Don't pursue unless there's real demand for running this as a shared service; it's a different product shape from the current personal tool.
- [ ] **F8** API contract drift watch. `BotConfig`, `StrategyRequest`, etc. are manually mirrored between Pydantic (backend) and `shared/types/index.ts` (frontend). Fine at ~35 types; once drift bites (fields silently dropped à la the `AddBotRequest` bug), switch to generating TS types from FastAPI's OpenAPI schema via `openapi-typescript`. Flag only — don't preempt.

### Shipped
- [x] Chart mount-once refactor — applied the same pattern used on MiniSparkline to the main Chart.tsx. The three `IChartApi` panes are now created once and kept alive across ticker / interval / indicator changes; data and option updates run in narrow effects keyed on what actually changed. Scrolling and ticker switching are noticeably smoother and idle CPU drops further. Teardown hardened against sibling-pane races: `syncWidths` reads `chartRef.current` dynamically + full try/catch, all cleanups null their refs before `chart.remove()`, range/crosshair handlers guard against already-removed siblings, Results.tsx swallows throws unsubscribing from a destroyed mainChart. Trading pollers (`AccountBar`, `OrderHistory`, `PositionsTable`) gained `AbortController`s so broker switches / unmounts cancel in-flight XHRs — silences Safari's "access control checks" cosmetic noise from aborted requests. `ErrorBoundary` wired in `main.tsx` so future render throws surface on-screen instead of blanking the tree.
- [x] Idle-CPU reduction pass — dev ~107% → ~1.3%, prod ~58% → ~5%. MiniSparkline split into mount-once + signature-guarded data/range effects (no more chart teardown per 5s bot poll); `useBroker.adaptiveInterval` wrapped in `useCallback` so polling timers don't reinstall on every broker refetch; JSON-diff guards around `setTrades` / `setPositions` / `setFund` / `setBots` so identical poll payloads don't re-render tables; PositionsTable's duplicate 30s journal poll relaxed to 60s (entries don't change post-fill). Investigated via Safari Web Inspector Timelines (Timer install/remove churn was the tell).
- [x] `./start.sh --prod` / `-p` flag — builds frontend and serves via `vite preview` on :4173; backend runs without `--reload`. Also cleared pre-existing TS errors (`App.tsx` implicit-any on `useState` callbacks, `Chart.tsx` shared `Trade` type + dead `ticker` prop) so `npm run build` passes `tsc -b` end-to-end.
- [x] Structural refactoring — extract models/journal/bot_runner, split BotControlCenter, shared utils, centralize API client

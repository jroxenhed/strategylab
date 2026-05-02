# StrategyLab TODO

\*\*79 / 101 shipped.\*\* Themed roadmap. Items indexed **Section Letter + Number** (e.g. B3) for reference. Checked = done; journal has shipping details. Items below `### Pre-numbering` predate the addressing scheme.

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

- [x] **A1** Portfolio equity chart (combined P&L across bots) — PortfolioStrip component with staircase-merged sparkline + summary stats (Total P&L $/%, Allocated, Running/Total, Profitable bots). Aligned sparklines via fixed 60% width.
- [x] **A2** Equity curve macro mode for long timescales / thousands of trades — resampled equity chart (D/W/M/Q/Y) via `MacroEquityChart.tsx` + `/api/backtest/macro`
- [ ] **A3** Equity curve trend analysis (open-ended — define "trend" first)
- [x] **A4** Indicator system redesign — replaced hardcoded indicator toggles with instance-based model (`IndicatorInstance[]`, `INDICATOR_DEFS` registry, generic `SubPane` + `PaneRegistry`). Add/remove/configure multiple instances of RSI, MACD, BB, ATR, MA, Volume with inline param editing. POST-based indicator endpoint, collapsible sidebar sections, chart-disabled gating. [Recap](docs/misc/A4-indicator-system-redesign-recap.md) · [Spec](docs/superpowers/specs/2026-04-20-indicator-system-redesign-design.md) · [Plan](docs/superpowers/plans/2026-04-20-indicator-system-redesign.md)
- [x] **A5** Resizable, collapsible, double-click-to-maximize individual chart panes — react-resizable-panels with drag dividers, double-click maximize/restore (TV style), localStorage persistence via autoSaveId.
- [x] **A6** Watchlist — right sidebar panel (TV style) with resizable divider, compact rows (price + daily change %), click-to-switch, batch quote endpoint with 30s polling, localStorage persistence.
- [x] **A7** New indicator types via registry: Stochastic (%K/%D with 80/20 refs), VWAP (main chart overlay), ADX (+DI/-DI with 25 trend ref). Full chart rendering + param editing in sidebar.
- [ ] **A8** Chart performance — large dataset optimizations (100K+ 5-min bars):
  - [x] Equity curve detail mode downsample: root cause was missing `toDisplayTime()` shift on equity timestamps — raw UTC timestamps didn't match the main chart's ET-shifted timestamps, breaking crosshair sync and bucket alignment. Fixed by adding `toDisplayTime` to `shared/utils/time.ts` (mirrors Chart.tsx `toET`) and applying it to equity/baseline/trade-tick timestamps in Results.tsx before downsampling. `downsampleEquity()` itself was always correct.
  - [ ] Viewport-only rendering — only pass the visible bar range to indicator series and markers instead of all 100K bars. lightweight-charts handles panning via `subscribeVisibleLogicalRangeChange`; feed data on demand.
  - [ ] Off-screen downsampling — when zoomed out to show all bars, aggregate to coarser resolution (e.g. 15m/1h) for rendering, switch to full resolution on zoom-in. Reduces object count 10-50x at wide zoom.
- [x] **A8a** Chart performance — 10x load speedup on 100K+ bar datasets. Cached `Intl.DateTimeFormat` in `toET()` (was constructing 100K+ instances per render), consolidated EMA overlay rendering from hundreds of `LineSeries` (one per active/inactive segment) to 2 per overlay using whitespace entries, gated SPY/QQQ fetches on toggle state.
- [x] **A8b** Chart display interval ("View as") — decouple chart display from backtest interval. Dropdown on chart header lets user view at coarser resolution (e.g. 1h, 1D) while backtesting on 5m. Trade markers snap to display bars with aggregation (`"2T"`, `"3T"`) and colored by net PnL. EMA overlays gated when intervals differ. [Spec](docs/superpowers/specs/2026-04-25-a8b-chart-display-interval-design.md) · [Plan](docs/superpowers/plans/2026-04-25-a8b-chart-display-interval.md)
- [x] **A9** Date range presets (D/W/M/Q/Y) + period stepping arrows (single, 5x skip)
- [x] **A10** Equity curve: normalised B&H comparison toggle + log scale toggle
- [x] **A11** MA8 / MA21 with SMA/EMA/RMA type selector + S-G smoothed variants (independent window/poly per MA, raw curve toggles, dashed S-G lines)
- [x] **A12** Backtest equity curve: baseline (buy & hold) overlay toggle
- [x] **A13a** Multi-TF data foundation — `fetch_higher_tf()`, `align_htf_to_ltf()`, `htf_lookback_days()` in `backend/shared.py`. Anti-lookahead alignment: daily MA for day D maps to day D+1's intraday bars (strict `<`, UTC-normalized). Weekend/holiday gap handling (Monday → Friday's close). Exhaustive alignment tests in `backend/tests/test_htf_alignment.py`. Shared prereq for A13b, B21, D24. [medium] [Plan](docs/superpowers/plans/2026-05-01-regime-filter.md)
- [ ] **A13b** Multi-TF indicator overlay — see daily/weekly indicators stepped onto intraday charts. HTF indicator endpoint (`routes/indicators.py` + `htf_interval` param), per-instance timeframe selector in sidebar ("Same"/"1D"/"1W"), stepped overlay rendering via `LineType.WithSteps`, grouped HTF data fetching in `useOHLCV`. Prereq: A13a. [medium] [Plan](docs/superpowers/plans/2026-05-01-regime-filter.md)

## B — Strategy Engine & Rules

- [x] **B1** Skip N trades after SL + configurable DS trigger — `BotConfig.skip_after_stop` + `BotState.skip_remaining`, shared `is_post_loss_trigger` helper, honored by both backtester and bot runner; StrategyBuilder skip-after-stop block + DS trigger selector; AddBotBar passes through from preset.
- [x] **B2** Extended hours — fully wired. Yahoo/IBKR native, Alpaca client-side RTH filter (9:30-16:00 ET). Cache keys already include `extended_hours`.
- [x] **B3** New rule conditions — MA8/MA21 slope (`turns_up`/`turns_down`) + `decelerating` via Savitzky-Golay second derivative; N-bar lookback for slope confirmation + min move % threshold; backtest respects sidebar S-G toggles.
- [x] **B4** Per-rule signal visualization toggles — eye icon on each rule row in strategy builder; when enabled, that rule's signals show as markers on the main chart during/after backtest. Replaces current hardcoded signal marker behavior. State stored with rule fields, persists with save/load. No global master toggle.
- [ ] **B5** Borrow cost estimation (for live short positions on real accounts)
- [x] **B6** Realistic cost model in backtester — IBKR Fixed per-share commission (`per_share_rate` + `min_per_order`), empirical per-symbol slippage via `GET /api/slippage/{symbol}` + `useEmpiricalSlippage` hook, short borrow cost (`borrow_rate_annual`). Results shows Borrow column + Cost Breakdown block.
- [x] **B7** Slippage model redesign — separate *measured* slippage (diagnostics) from *modeled* slippage (backtest assumption). Always ≥ 0 everywhere it surfaces. Floor empirical at default, gate on minimum fill_count, single shared signed-cost helper in `backend/slippage.py`. Fixes journal display (wrong sign for sells/shorts), bot runner log sign drift, and the "favorable empirical auto-carries into Capital & Fees" pitfall. [plan](docs/superpowers/plans/2026-04-15-b7-slippage-redesign.md)
- [ ] **B8** Spread-derived slippage default (follow-up to B7) — pull live bid/ask spread from broker (Alpaca quote endpoint / IBKR market data) and default modeled slippage to half-spread for the symbol. More principled than global/empirical fallback, especially for illiquid names. Only viable providers that expose quotes.
- [ ] **B9** Cost model v2 (deferred from B6):
  - Debit-balance-aware margin interest for shorts (charge margin rate only on days net cash is negative)
  - IBKR Tiered pricing (exchange fees, SEC fee, FINRA TAF, clearing pass-throughs)
  - Hard-to-borrow dynamic rate feed
  - FX conversion cost
- [x] **B10** Skip-on-wide-spread entry gate — frontend wired. AddBotBar: "Max Spread bps" input (default 50), BotCard: inline-editable spread cap (same pattern as allocation). Empty/0 = disabled.
- [x] **B11** Saved-strategy library UX — inline rename + pin-to-top. Pinned strategies sort first (prefix in dropdown). Rename validates non-empty, no duplicates. Backward-compatible with old saves.
- [x] **B12** Parameterized MAs in strategy rules — replace 5 hardcoded MA entries (ma8, ma21, ema20, ema50, ema200) with generic `ma(period, type)`. User picks any period + SMA/EMA. Backend computes on demand. Removes all Savitzky-Golay smoothing code. Foundation for B13/B14. [Ideation](docs/ideas/2026-04-21-strategy-builder-indicators-ideation.md) · [Spec](docs/superpowers/specs/2026-04-21-b12-parameterized-ma-rules-design.md) · [Plan](docs/superpowers/plans/2026-04-21-b12-parameterized-ma-rules.md)
- [x] **B13** BB / ATR / ATR% / Volume as rule indicators — multi-output addressing in signal engine (BB upper/lower/middle/bandwidth/%B, ATR, ATR as % of close, Volume raw + SMA). Cross-reference support (price vs BB band, volume vs SMA). Frontend rule builder with param UIs for each.
- [x] **B14** Stochastic + ADX rule indicators — compute functions in `indicators.py`, signal engine integration (compute_indicators, resolve_series, resolve_ref), RuleRow UI with param editing. Stochastic: %K/%D crossover + overbought/oversold conditions. ADX: component selector (ADX/+DI/-DI) + trend strength conditions.
- [x] **B15** Fix MACD crossover bug — auto-set `param: 'signal'` for MACD crossover conditions in `emptyRule()`, indicator change handler, condition change handler, and `migrateRule()` for existing saved strategies.
- [x] **B16** Ghost trade markers — verified fixed. Interval change clears backtestResult (markers), view interval change recomputes markers via useMemo. No stale markers survive.
- [x] **B17** Hover-to-inspect trade markers — tooltip was already implemented (B18 shipped it). Stripped text labels from main-chart markers; subpane markers retain labels. Arrows colored by P&L outcome. [Spec](docs/superpowers/specs/2026-04-23-b17-hover-trade-markers-design.md)
- [x] **B18** Triggering rules in trade tooltip — extend B17 tooltip to show which buy/sell rules fired for each trade. Backend tags each trade with `rules` field via `_fired_rules()`. Entries show buy rules, exits show sell rules (or "stop loss"/"trailing stop"/"time stop" for mechanical exits).
- [x] **B19** Implement shorting — direction field, backtest + bot runner, chart markers, bot card refresh
- [ ] **B20** Multi-timeframe confirmation — rules evaluate on a single interval today. Add "confirm on higher timeframe" option (e.g., enter on 5m signal only if 1h trend agrees). Requires fetching a second OHLCV series at the confirmation interval, computing indicators on it, and adding a `confirm_interval` + `confirm_rules` field to StrategyRequest. Common quant pattern that expands strategy sophistication significantly.
- [ ] **B21** Regime filter: sit-flat gate + `is_short` refactor — `RegimeConfig` model, refactor backtest `is_short` → `position_direction` (behavioral no-op for single direction), regime gate in backtest (`buy_fires AND regime_active[i]`), regime chart shading (histogram on hidden scale), regime UI section in StrategyBuilder (collapsible, with stop-loss warning), `regime_active` in backtest response, regime in saved strategies, bot runner rejects `regime.enabled` until D24 ships. Prereq: A13a. [large] [Plan](docs/superpowers/plans/2026-05-01-regime-filter.md)
- [ ] **B22** Regime: symmetric direction switching (backtest) — `on_flip` behavior (close_only default / close_and_reverse / hold), per-bar `position_direction` switching driven by regime, per-trade direction in trade records. Uses same rules for both directions (no dual rule sets yet). UI: on_flip dropdown, direction toggle hidden when on_flip != hold. PnL sign correctness tests against known price sequences. Prereq: B21. [medium] [Plan](docs/superpowers/plans/2026-05-01-regime-filter.md)
- [ ] **B23** Regime: dual rule sets — `long_buy_rules`/`long_sell_rules`/`short_buy_rules`/`short_sell_rules` in schema + backtest. Three-state regime (long/short/flat based on dual rule presence). Long/Short tab split in StrategyBuilder — each direction gets independent entry/exit rules with different indicators (e.g. MA-based long, RSI/Stochastic short). Prereq: B22. [medium] [Plan](docs/superpowers/plans/2026-05-01-regime-filter.md)

## C — Strategy Summary & Analytics

- [x] **C1** Inline range bars on waterfall — removed separate stat column, added min-max bar with avg tick under each Wins/Losses row. Handles single-trade, all-wins/all-losses, null stats.
- [x] **C2** "vs B&H" alpha metric replaces raw B&H Return — shows outperformance delta, green/red on sign.
- [x] **C3** Sharpe: green >=1, orange 0.5-1, red <0, gray 0-0.5. Max DD: red >=10%, gray <10%.
- [x] **C4** Histogram zero line (baseline + vertical dashed), min/max/zero dollar labels below bars.
- [x] **C5** Expected value / trade + profit factor — EV + PF headline numbers, 3-row decomposition waterfall (Wins / Losses / Net) inline with StatRows + histogram, mean/median toggle dropped in favor of inline dual values
- [x] **C6** Strategy summary: min/max/avg gain and loss
- [x] **C7** Summary readability pass — dropped `(mean)` suffix on EV/PF, renamed Max/Min gain/loss to Biggest/Smallest win/loss, added size hierarchy to top metrics row (Return + Final Value 22px primary, rest 13px secondary), removed median secondary values from avg rows
- [x] **C8** B&H value in summary was inconsistent for short strategies with open positions at end — `final_value` used long formula (`capital + position * price`) instead of short formula (`capital + position * entry_price + unrealized`). Fixed to match equity curve calculation.
- [ ] **C9** Strategy comparison mode — load 2-3 saved strategies, run them on the same ticker/period, see equity curves overlaid + metric table side-by-side. Mostly frontend composition over existing backtest engine and equity rendering. Makes parameter tuning much faster than running backtests one at a time.
- [ ] **C10** Intraday session analytics — break down strategy performance by time-of-day (30-min buckets). Heatmap or histogram of win rate / EV by session window (open, midday, power hour). Trade timestamps already exist; cheap to compute, actionable for trading hours filters.
- [x] **C11** Monte Carlo simulation — run N random permutations of trade sequence to estimate confidence intervals on returns, max drawdown, and probability of ruin. Critical for small-account sizing where a single bad drawdown sequence matters.
- [x] **C12** Rolling performance window — show Sharpe, win rate, and return over rolling N-trade windows overlaid on equity curve. Reveals regime changes and strategy decay that aggregate stats hide.
- [x] **C13** Monte Carlo bug fixes — (1) final value percentiles all show the same number (should spread); header stats may be pulling from wrong field. (2) Used raw `fetch()` instead of project `api` client (already fixed locally, needs committing).
- [x] **C14** Trade duration histogram — distribution of hold times (bars or hours/days) as SVG histogram in Results. Spots if strategy holds losers too long. Pure frontend from existing trade timestamps. [easy]
- [x] **C15** Win/loss streak analysis — max consecutive wins/losses, average streak length, streak distribution mini-chart. Reveals if strategy clusters wins or has brutal losing runs. Small panel in Summary tab. [easy]
- [x] **C16** Risk-adjusted position sizing calculator — Kelly criterion + fixed-fractional sizing based on backtest win rate and avg win/loss ratio. Shows "optimal" bet size given your edge. Small panel in Summary tab. [easy]
- [ ] **C17** Correlation to benchmark — compute beta and R² vs SPY returns alongside strategy equity curve. SPY data already loads via existing infrastructure. New stats row in Summary. [medium]
- [ ] **C18** Parameter sensitivity sweep — re-run backtest with ±N variations of one indicator param, show results in a table/heatmap. Answers "how fragile is this edge?" [medium]
- [ ] **C19** Backtest result persistence — save/load backtest results to localStorage so you can compare across sessions without re-running. [medium]

## D — Bots (live trading)

- [x] **D1** Global timezone toggle — header button switches all timestamps between ET (EST/EDT) and browser-local time (CET/CEST). Persisted to localStorage. `useSyncExternalStore`-based so formatting functions read the mode directly.
- [x] **D2** Bot drag-to-reorder — @dnd-kit sortable with drag handles, smooth animation, persists order to bots.json via PUT /api/bots/reorder. Handles new/deleted bots gracefully.
- [x] **D3** Bot lifecycle vs journal: soft reset via `BotConfig.pnl_epoch` — "Reset P&L" button on BotCard bumps epoch to now, journal untouched, displayed P&L/trades/slippage filter by `since=epoch`. TradeJournal marks bot rows with null/deleted `bot_id` as "orphan" so audit history stays visible.
- [x] **D4** Removed dead `BotState.total_pnl` — never written by bot_runner, P&L is live-computed via `compute_realized_pnl()`. Old bots.json silently ignores the field via `from_dict()` filtering.
- [x] **D5** Journal helper call frequency — `list_bots` was reading + parsing journal 27 times (3 functions x 9 bots). Added `_load_trades()` helper + optional `trades` param; now reads once and passes through. Live Trading page load went from 3-5s to instant.
- [x] **D6 + D7** IBKR heartbeat + multi-broker union for positions/orders/journal. HeartbeatMonitor pings providers every 5s; `/api/broker` exposes health + warmup. Positions/orders aggregate across brokers via `aggregate_from_brokers` (skipping unhealthy), journal trades stamped with `broker`. UI: Broker column + BrokerTag health dot, per-table broker filter (persisted), dismissible stale-broker banner. [plan](docs/superpowers/plans/2026-04-14-multi-broker-positions.md) · [spec](docs/superpowers/specs/2026-04-14-multi-broker-positions-design.md)
- [x] **D8** IBKR reliability overhaul — subscribe to `ib.errorEvent` with structural vs transient classification (structural rejects auto-pause bots with reason), reconnect deduplication via asyncio.Lock, retry with exponential backoff (3 attempts, 1s/2s/4s), 3s TTL cache on positions/account/orders (invalidated on mutations + reconnect), removed per-call reqCurrentTime ping (HeartbeatMonitor handles liveness). Follow-up: order-ID error filtering (one bot's reject doesn't pause others), auto-resume after transient failures (30s backoff instead of permanent death), adaptive UI polling (10s backoff when broker unhealthy), dismissible broker-unhealthy banner + per-bot pause_reason display.
- [x] **D9** Partial-position reconciliation — tracks `_last_broker_qty` between ticks, logs WARN on external shrinkage. Guards: skips first tick (learns baseline), skips pending close orders, skips full closures (existing path), ignores qty increases.
- [x] **D10** Compact mode for bot cards — toggle between current expanded layout and a condensed single-row view showing only essential info (heartbeat, symbol, strategy, P&L, status). Portfolio summary strip stays as-is. Persist preference to localStorage. Sparkline alignment fix: sparkline is the flex element (takes remaining space), text stays content-width.
- [x] **D11** IBKR broker integration — full data + trading provider via `ib_insync`. TradingProvider protocol abstracts Alpaca/IBKR behind unified interface. Global broker selector (data source stays per-request). Enables simultaneous long+short on same symbol. [spec](docs/superpowers/specs/2026-04-13-ibkr-broker-integration-design.md)
- [x] **D12** IBKR stability pass — heartbeat auto-reconnect on ping failure (rotates clientId to dodge Error 326 stale-slot trap), robust startup with clientId rotation + clean shutdown disconnect, `ib.portfolio()` instead of `ib.positions()` so live price/market value/unrealized P&L populate, ping awaits Future on shared loop. UI: PositionsTable keyed by `symbol|broker|side` to avoid React reconciliation collapsing same-symbol rows, opened-time fallback for pre-broker-tagging journal rows. Bot runner: post-close position re-check prevents phantom SELL rows when IBKR silently rejects an order.
- [x] **D13** Paper trading polish — Journal: reason colors fixed, Expected/Gain% columns, summary row, filter relocated, auto-refresh 5s, CSV export. Bot cards: heartbeat dot. Positions: 5s poll, Opened/Side columns.
- [x] **D14** Track actual slippage — poll Alpaca fill price, log expected vs actual, show in journal
- [x] **D15** Manual buy on bot to start a position
- [x] **D16** Make allocation and strategy editable in-place on bot card (click when stopped)
- [x] **D17** Global start/stop all bots
- [x] **D18** Bot sparkline: global toggle for local vs aligned timescale
- [x] **D20** Bot alerting / notifications — push bot events (entry, exit, stop hit, error) to phone/desktop. Webhook to Pushover/ntfy.sh or Telegram bot. Critical for running US market bots from Sweden — knowing instantly when something fires.
- [x] **D21** Strategy auto-pause on drawdown — automatically pause a bot when cumulative loss from peak exceeds a configurable threshold (e.g., 5% of allocated capital). Safety net for unattended bots during US market hours.
- [x] **D19** Bot card redesign — responsive sparkline (was fixed 60%), columnar stats (label above value), compact mode kebab dropdown (replaces inline buttons), portfolio strip alignment, shared `ui.tsx` for layout primitives. 106 tests.
- [x] **D22** Trade journal CSV export — download button on TradeJournal for tax prep or external analysis in spreadsheets. [easy]
- [ ] **D23** Bot daily P&L summary — small calendar heatmap or daily bar chart on BotCard showing per-day returns. Visual pattern recognition for "which days does this bot print?" [medium]
- [ ] **D24** Regime filter: live bot integration — regime evaluation in `bot_runner._tick()`, `is_short` → `position_direction` refactor in bot_runner, position flip sequence (close → verify → reverse entry on same tick), `pending_regime_flip` retry logic, BotState regime+direction fields, `compute_bidirectional_pnl` in journal.py (no existing callers change), bidirectional same-symbol guard (regime bot gets exclusive symbol access), regime status on bot card (Active/Flat/Pending + position direction), AddBotBar regime passthrough. Prereq: B23. [large] [Plan](docs/superpowers/plans/2026-05-01-regime-filter.md)

### Pre-numbering
- [x] Verify allocation logic — was position_size=10.0, added validator to clamp 0.01-1.0
- [x] Verify SL fill detection — code verified, will confirm on next live SL trigger
- [x] Does algo wait for candle close? — No, and that's fine. OTO SL is server-side (instant), trailing stop benefits from frequent checks
- [x] Buying amount compounds P&L (allocated_capital + total_pnl), matching backtest behavior
- [x] Refresh button on journal
- [x] Increase UI update frequency (5s to 2s for bot list + detail polling)
- [x] Position size: removed slider, hardcoded to 100%

## E — Discovery

Own multi-session research project. Needs its own design work before implementation.

- [ ] **E1** Scan for good StrategyLab candidates (criteria TBD)
- [ ] **E2** Batch backtesting (efficiency-critical)
- [ ] **E3** AI/ML assisted parameter tweaking
- [ ] **E4** Pipeline: present candidates -> spawn bot army
- [x] **E5** Reframe SignalScanner as research, not execution. Current page duplicates bot logic (manual Buy/Sell, own position size + stop) and skips direction, slippage/cost model, trailing stops, journal scoping, broker/data-source choice. Replace with: pick a saved strategy (no inline rule editing), show live signal + mini backtest stats per ticker (return / Sharpe / win rate over configurable lookback), per-row action becomes **Spawn Bot** that pre-fills AddBotBar (symbol, strategy, broker, data source). Drop Position $ and Stop Loss % inputs entirely. Prereq for E4.
  - [x] Backend: `POST /api/backtest/quick` + `POST /api/backtest/quick/batch` — summary stats only (`backend/routes/backtest_quick.py`)
  - [x] Frontend: SignalScanner rewritten — saved strategy dropdown, lookback selector, sortable results table, Spawn Bot pre-fills AddBotBar via localStorage
- [x] **E6** Clean up bot page, move signal scanner to new Discovery page

## F — Architecture & housekeeping

- [x] **F1** Renamed "Paper Trading" tab label to "Live Trading"
- [ ] **F2** Group backend broker files into a `backend/brokers/` package **and** split `broker.py` (~680 lines) into `brokers/{yahoo,alpaca,ibkr}.py` behind the existing `TradingProvider` protocol. `broker_aggregate.py`, `broker_health.py`, `broker_health_singleton.py` move into the same package. Preserve `broker_health_singleton.py` as a separate module — it exists to break an import cycle, not as ceremony. Low value, moderate risk — only tackle if friction shows up when editing a single provider.
- [ ] **F3** Split `frontend/src/features/strategy/Results.tsx` (~745 lines) and `StrategyBuilder.tsx` (~578 lines) into smaller subcomponents (equity/drawdown/scatter/tables for Results; rule sections for StrategyBuilder). Same tradeoff as F2 — defer until a change actually gets painful.
- [x] **F4** Frontend test harness — Vitest + React Testing Library, 27 smoke tests across 3 files: api client (10), useOHLCV hooks (9), Chart mount/unmount lifecycle (8). Targets historically buggy teardown paths. Runs in 1.2s.
- [x] **F5** `./start.sh --prod` / `-p` flag — builds frontend and serves via `vite preview` on :4173; backend runs without `--reload`. Also cleared pre-existing TS errors (`App.tsx` implicit-any on `useState` callbacks, `Chart.tsx` shared `Trade` type + dead `ticker` prop) so `npm run build` passes `tsc -b` end-to-end.
- [ ] **F6** Split `frontend/src/shared/types/index.ts` (350 lines, 35 exports) by domain: `types/chart.ts`, `types/strategy.ts`, `types/trading.ts`, with `index.ts` as a barrel re-export. Low risk (edit imports), improves greppability when hunting for a specific interface.
- [ ] **F7** Sub-group `frontend/src/features/trading/` (10 files). Natural split: bot-management (`AddBotBar`, `BotCard`, `BotControlCenter`, `MiniSparkline`) vs account-view (`AccountBar`, `PositionsTable`, `OrderHistory`, `TradeJournal`) vs shared (`BrokerTag`), with `PaperTrading.tsx` staying as the feature entry. Defer until the feature grows further — 10 siblings is borderline, not painful yet.
- [ ] **F8** API contract drift watch. `BotConfig`, `StrategyRequest`, etc. are manually mirrored between Pydantic (backend) and `shared/types/index.ts` (frontend). Fine at ~35 types; once drift bites (fields silently dropped via the `AddBotRequest` bug), switch to generating TS types from FastAPI's OpenAPI schema via `openapi-typescript`. Flag only — don't preempt.
- [x] **F9** `STRATEGYLAB_DATA_DIR` env var — threads through journal, bot_manager, watchlist. Defaults to `backend/data/`, auto-creates on startup.
- [ ] **F10** Multi-user overhaul — proper auth + per-user data namespacing, SQLite instead of JSON for `bots` / `trade_journal`, session handling in the frontend. Don't pursue unless there's real demand for running this as a shared service; it's a different product shape from the current personal tool.
- [x] **F11** Chart mount-once refactor — applied the same pattern used on MiniSparkline to the main Chart.tsx. The three `IChartApi` panes are now created once and kept alive across ticker / interval / indicator changes; data and option updates run in narrow effects keyed on what actually changed. Scrolling and ticker switching are noticeably smoother and idle CPU drops further. Teardown hardened against sibling-pane races: `syncWidths` reads `chartRef.current` dynamically + full try/catch, all cleanups null their refs before `chart.remove()`, range/crosshair handlers guard against already-removed siblings, Results.tsx swallows throws unsubscribing from a destroyed mainChart. Trading pollers (`AccountBar`, `OrderHistory`, `PositionsTable`) gained `AbortController`s so broker switches / unmounts cancel in-flight XHRs — silences Safari's "access control checks" cosmetic noise from aborted requests. `ErrorBoundary` wired in `main.tsx` so future render throws surface on-screen instead of blanking the tree.
- [x] **F12** Idle-CPU reduction pass — dev ~107% to ~1.3%, prod ~58% to ~5%. MiniSparkline split into mount-once + signature-guarded data/range effects (no more chart teardown per 5s bot poll); `useBroker.adaptiveInterval` wrapped in `useCallback` so polling timers don't reinstall on every broker refetch; JSON-diff guards around `setTrades` / `setPositions` / `setFund` / `setBots` so identical poll payloads don't re-render tables; PositionsTable's duplicate 30s journal poll relaxed to 60s (entries don't change post-fill). Investigated via Safari Web Inspector Timelines (Timer install/remove churn was the tell).
- [x] **F13** Structural refactoring — extract models/journal/bot_runner, split BotControlCenter, shared utils, centralize API client

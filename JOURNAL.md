# Journal

What we've actually shipped. Reverse-chronological, one section per working day. Bold IDs (e.g. **[D10](TODO.md#d--bots-live-trading)**) cross-reference [TODO.md](TODO.md).

> **Maintenance rule (Claude):** append an entry at the end of any session that produces durable work — TODO closures, features, bug fixes, discoveries. Skip routine commits (typo fixes, reformatting). Keep bullets short; link to the commit or doc if more context is worth a click. Don't re-read every TODO to write an entry — just log what happened in the session.

## 2026-05-03 (overnight build 3)

- **[B22](TODO.md#b--strategy-engine--rules)** Regime symmetric direction switching. Added `on_flip: str = "close_only"` field to `RegimeConfig` (backend + TS type). Three behaviors: `hold` (existing gate-only), `close_only` (forced exit on regime flip, no auto re-entry), `close_and_reverse` (forced exit + immediate forced entry in opposite direction at same bar, slippage/commission on both legs). Flip detection via `curr_regime_active != prev_regime_active` each bar. With `close_and_reverse`, signal-driven entries also adopt the regime-determined direction (active=req.direction, inactive=opposite). `regime_series` output shows opposite direction for inactive bars under `close_and_reverse` (not "flat"). Frontend: `on_flip` dropdown (Close only / Close & reverse / Hold) in regime config section; direction toggle hidden when `regimeEnabled && on_flip !== 'hold'`, replaced by informational label showing entry direction and flip target. Build clean. Not visually verified. Review in background.

## 2026-05-03 (overnight build 2)

- **[C17a](TODO.md#c--strategy-summary--analytics)** Fix SPY correlation (beta/R² always 0). Root cause: daily equity-curve returns are 0 for 90%+ of bars when strategy is flat, making covariance meaningless. Fix: rewrote `_compute_spy_correlation(trades, start, end)` to use per-trade returns (pnl / entry_value) paired with SPY's return over each trade's holding period. Returns None gracefully for <3 trades or near-zero SPY variance (intraday same-day trades). P2 cosmetic: intraday strategies with same-day entries/exits will have near-zero SPY variance and correctly show None.

- **[B21](TODO.md#b--strategy-engine--rules)** Regime filter: sit-flat gate + `is_short` refactor. `RegimeConfig` Pydantic model (`indicator`, `indicator_params`, `condition`, `min_bars`) in `models.py`. `_compute_regime_series()` helper in `backtest.py`: fetches HTF data with extended lookback via `fetch_higher_tf()`, computes indicator via `compute_instance()`, evaluates condition (above/below/rising/falling), applies min_bars rolling-window smoothing, aligns to LTF index via `align_htf_to_ltf()`. Main loop: `is_short` local variable replaced by `position_direction` (set at entry, cleared at exit); regime gate added to entry condition (`regime_ok` before `eval_rules`). Trade records use `position_direction` for the `direction` field. `regime_series` (per-bar `{time, direction}`) added to backtest response. Bot runner: guard in `_tick()` raises clear error when `cfg.regime.enabled` is True (live regime not yet supported). `regime: Optional[RegimeConfig] = None` added to `BotConfig` (bot_manager.py) and `UpdateBotRequest` (bots.py — pitfall #4 fix). Frontend: `RegimeConfig` TypeScript interface + `regime?` on `StrategyRequest`, `SavedStrategy`, `BacktestResult`. `StrategyBuilder` regime section (toggle button + timeframe/type/period/condition/min_bars controls + stop-loss warning). Chart.tsx: histogram series on hidden `regime-bg` price scale (#26a64120 green = active long, #f8514920 red = active short). Save/load: snapshot captures regime config, `loadSavedStrategy` restores it. Not visually verified.

- **[A13b](TODO.md#a--charts--indicators)** Multi-TF indicator overlay. Extends `routes/indicators.py` with `htf_interval` param — when set, fetches OHLCV at the higher TF with extended lookback (via `htf_lookback_days`), computes indicator, aligns to LTF index via `align_htf_to_ltf`, returns at LTF timestamps. Frontend: `htfInterval?` field on `IndicatorInstance`; TF selector dropdown ("Same"/"1D"/"1W") in IndicatorList expanded settings for main-pane overlays; `useInstanceIndicators` updated to group HTF instances by `htfInterval` and make parallel API calls via `useQueries`; Chart.tsx renders HTF overlays with `LineType.WithSteps` and includes htfInterval in series title suffix. Prereq A13a.

- **[C10](TODO.md#c--strategy-summary--analytics)** Intraday session analytics. Discovered already shipped in prior session (30-min bucket breakdown of win rate/EV in `compute_session_analytics` backend + `SessionAnalytics` component + Session tab in Results). Checked off.

- **[C17](TODO.md#c--strategy-summary--analytics)** Benchmark correlation (SPY beta/R²). New `_compute_spy_correlation(equity, start, end)` in `backtest.py`: groups equity curve by ET date, computes daily returns, fetches SPY daily (TTL-cached), aligns on common dates, computes beta = cov/var_spy and R² = corr². Returns null for short/empty periods or failed SPY fetches. Added to backtest summary dict; `beta`/`r_squared` added to `BacktestResult.summary` TS type; new "Benchmark Correlation (SPY)" panel in Summary tab showing β value with context label (Amplified/Inverse/Tracking) and R² percentage with fit label.

- **[C19](TODO.md#c--strategy-summary--analytics)** Backtest result persistence. `BACKTEST_CACHE_KEY` localStorage entry stores `{result, request}` on each backtest. On page load, `_cachedBacktest` checks if saved ticker/start/end/interval matches the cache's request — if so, restores last backtest result into state so results are visible without re-running. Clears cache when result is null (ticker/date changes). Quota-exceeded silently skipped.

## 2026-05-02

- **[A13a](TODO.md#a--charts--indicators)** Multi-TF data foundation. Three new functions in `backend/shared.py`: `htf_lookback_days(indicator, params)` computes calendar-day warmup window (`int(period * 1.5 * 365/252 + 30)`); `fetch_higher_tf()` thin wrapper over `_fetch()` for HTF data; `align_htf_to_ltf(htf_series, ltf_index)` aligns daily values to intraday bars with strict anti-lookahead via `shift(1)` + UTC normalization + `pd.merge_asof(direction='backward')`. Handles weekend/holiday gaps and tz-aware/naive inputs. 6 exhaustive tests in `backend/tests/test_htf_alignment.py` (6/6 pass) covering lookahead, forward mapping, weekend gap, empty series, warmup NaN, lookback formula. Shared prereq for A13b, B21, D24.

- **[C15](TODO.md#c--strategy-summary--analytics)** Win/loss streak analysis panel. New `streakUtils.ts` (streak computation: max/avg win+loss streaks) and `StreakPanel.tsx` (UI panel). Inserted into Summary tab in Results.tsx. Shows max consecutive wins/losses (large colored numbers), avg streak lengths, and mini SVG distribution charts (120×36px, shown when ≥2 streaks). Gated on closed trades presence.

- **[D22](TODO.md#d--bots-live-trading)** Trade journal CSV export. Already shipped as part of D13 (verified: `exportCsv()` function at TradeJournal.tsx:140, download button at line 198). Checked off.

- **[C16](TODO.md#c--strategy-summary--analytics)** Kelly position sizing. New `KellySizing.tsx` component embedded in Summary tab when ≥5 completed trades. Computes Kelly criterion (`f* = W − (1−W)/R`) from backtest win rate and avg win/loss ratio. Displays full Kelly, ½ Kelly (recommended), ¼ Kelly fractions. Shows "no edge" warning with 0% size recommendation when f* ≤ 0.

- Summary tab layout fix: removed `maxHeight: 600` cap so content fills viewport. Cost Breakdown, Win/Loss Streaks, and Kelly Sizing now in responsive CSS grid (`auto-fit, minmax(320px, 1fr)`) — three columns on wide screens, stacking on narrow.

## 2026-05-01

- **[C13](TODO.md#c--strategy-summary--analytics)** Monte Carlo bug fixes. (1) `final_value` percentile stats were all identical — replaced with `min_equity` (minimum equity touched during each simulation), which spreads meaningfully across shuffles. Backend: `min_equities` tracked per-sim, returned as `min_equity` in response. Frontend: MonteCarloChart.tsx updated to display min equity stats with correct color semantics (p5=red worst, p95=green best). (2) `fetch()` → `api.post()` fix was already committed in prior session.

- **[C14](TODO.md#c--strategy-summary--analytics)** Trade duration histogram. New `TradeHoldDurationHistogram.tsx` component (207 lines): SVG histogram of hold times, buckets colored by win/loss dominance, summary row showing median/avg-win/avg-loss hold times. Intraday uses hours (unix timestamp diff / 3600), daily uses calendar days. "Hold Time" tab in Results appears when ≥2 completed trades.

- **[D21](TODO.md#d--bots-live-trading)** Strategy auto-pause on drawdown. `BotConfig.drawdown_threshold_pct: Optional[float]`. When set, `_tick()` checks peak-to-trough PnL vs `allocated_capital` after each position closes — pauses bot with `status="error"` + `pause_reason` message and fires `notify_error` (fire-and-forget) if threshold exceeded. Covers both long-exit and short-exit paths; state fields cleared before save. Frontend: AddBotBar "Max DD %" input + BotCard inline-editable field (pattern from allocated_capital).

## 2026-04-30

- **Overnight builder operational.** Push auth resolved — required installing the Claude GitHub App on GitHub (`github.com/apps/claude`) with Contents: Read & write permission. Routine prompt updated to use `claude/` prefixed branches + `gh pr create`. First successful delivery: PR #4 (C11 + C12) merged.

- **[C13](TODO.md#c--strategy-summary--analytics)** Monte Carlo bug fix: overnight builder used raw `fetch()` instead of project `api` client — requests hit Vite dev server (port 5173) instead of backend (8000), failing silently. Fixed to use `api.post()`.

- **[C11](TODO.md#c--strategy-summary--analytics)** Monte Carlo simulation. New `POST /api/backtest/montecarlo` endpoint (`backend/routes/monte_carlo.py`) accepts a list of exit-trade PnLs + initial capital, runs 1,000 random shuffles, and returns percentile curves (p5/p25/p50/p75/p95) over the trade sequence plus final-value percentiles, max-drawdown percentiles, and probability of ruin. New `MonteCarloChart.tsx` SVG component renders shaded percentile bands. New "Monte Carlo" tab in Results appears when ≥ 2 completed trades; auto-fetches on first visit, resets on new backtest run.

- **[C12](TODO.md#c--strategy-summary--analytics)** Rolling performance window. New `RollingWindowChart.tsx` computes rolling win rate, avg PnL, and Sharpe ratio over a sliding N-trade window entirely client-side from the existing trades array. Window selector (5 / 10 / 20 / 50 trades). Three stacked SVG mini-charts with reference lines (50% win rate, $0 PnL, 1.0 Sharpe). New "Rolling" tab in Results appears when ≥ 5 completed trades.

## 2026-04-29

- **[E5](TODO.md#e--discovery)** Quick backtest endpoint. New `POST /api/backtest/quick` returns summary-only stats (return %, Sharpe, win rate, num trades, max drawdown, `signal_now`, `last_signal_date`) without equity curve or trade list. Batch variant `POST /api/backtest/quick/batch` runs sequentially over a symbol list. Registered in `main.py` alongside existing routers. Route file: `backend/routes/backtest_quick.py`.
- **[D20](TODO.md#d--bots-live-trading)** Bot alerting via ntfy.sh. New `backend/notifications.py` with fire-and-forget `notify()` + four typed helpers (`notify_entry`, `notify_exit`, `notify_stop`, `notify_error`). Hooked into `bot_runner.py` at entry fill, detected exit, IBKR structural error, and MAX_CONSEC_ERRORS backoff. Enable by setting `NOTIFY_URL=https://ntfy.sh/your-topic` in `backend/.env`. New endpoints: `GET /api/notifications/status` and `GET /api/notifications/test`.

- **[C10](TODO.md#c--strategy-summary--analytics)** Intraday session analytics. `compute_session_analytics()` in `backtest.py` breaks down trade performance by 30-min time-of-day buckets (09:30–16:00 ET). Frontend: horizontal bar chart in Results summary tab with win-rate-colored bars, trade counts, avg PnL per bucket, best/worst window summary. Only renders for intraday intervals.

- **[C9](TODO.md#c--strategy-summary--analytics)** Strategy comparison mode. New `StrategyComparison.tsx` component: select 2-3 saved strategies, run backtests in parallel via `Promise.all`, overlay equity curves (blue/orange/green) on a single lightweight-charts instance, side-by-side metrics table (Return, Sharpe, Win Rate, Max DD, PF, EV, vs B\&H) with best/worst highlighting. Toggle via `⇄ Compare` button in chart pane header. Shared `savedStrategies.ts` extracted for `migrateRule`/`loadSavedStrategies`.

- D20 review-driven fixes: merged `notify_stop` into `notify_exit` (one notification per event, priority=high for stops), `asyncio.create_task()` for fire-and-forget (was blocking `_tick()`), `run_coroutine_threadsafe` for sync IBKR callback, httpx client lifecycle cleanup, removed NOTIFY_URL leak from API responses, added notifications to bot-managed close path. 14 fixes total from 8-persona parallel review.

- **[B4](TODO.md#b--strategy-engine--rules)** Per-rule signal visualization. Eye icon toggle on each rule row; backend emits `rule_signals` in backtest response (per-bar signal data for visualized rules with negation/muted handling); Chart.tsx merges signals into main markers as colored circles with legend overlay. Review-driven fixes: negation inversion, rule_index offset for sell rules, muted guard, variable shadow, React key collision, lucide-react Eye icon.

- **[C8](TODO.md#c--strategy-summary--analytics)** Fix short strategy final value mismatch. `final_value` used long formula for shorts with open positions, causing wrong Return % and vs B&H. Now matches equity curve calculation.

- **[A7](TODO.md#a--charts--indicators)** New chart indicators: Stochastic (%K/%D lines + 80/20 reference), VWAP (main chart overlay, orange), ADX (ADX/+DI/-DI lines + 25 trend reference). Full sidebar param editing + indicator registry. Three parallel worktree agents for backend compute, frontend rendering, and signal engine.

- **[B14](TODO.md#b--strategy-engine--rules)** Stochastic + ADX as rule indicators. Backend: `compute_stochastic`, `compute_vwap`, `compute_adx` in `indicators.py`. Signal engine: stoch/adx specs, resolve_series/resolve_ref with %K/%D crossover pattern (matching MACD). Frontend: RuleRow with param UIs, NEEDS_PARAM for stochastic crossovers.

- **[D19](TODO.md#d--bots-live-trading)** Bot card redesign — responsive sparkline columns (fixed 60% → flex 35/65 split), columnar stats (label above value with flex-wrap), compact mode kebab dropdown replacing inline buttons, portfolio strip column alignment. Shared `ui.tsx` for layout primitives (`btnStyle`, `StatCell`, `INFO_COLUMN_FLEX`). Fixes: P&L division-by-zero guard, stale detail cleanup on collapse, menuOpen reset on mode toggle. 106 tests across 3 new test files.

- **[A8 equity downsample](TODO.md#a--charts--indicators)** Fixed equity curve timestamp alignment. Root cause: Results.tsx was passing raw UTC timestamps to the equity chart while Chart.tsx applies `toET()` ET-shift to candlestick timestamps. Mismatch meant crosshair sync was broken and `downsampleEquity()` bucket keys didn't align with main chart bars. Fix: added `toDisplayTime()` to `shared/utils/time.ts` (exact mirror of Chart.tsx `toET`) and applied it to equity/baseline/trade-tick timestamps in Results.tsx before downsampling. `downsampleEquity()` logic itself was always correct — the TODO's "doesn't take effect" was caused by the timestamp mismatch, not the function or the effect re-firing.

- **[E5](TODO.md#e--discovery)** SignalScanner reframed as research tool. Rewrote frontend: saved strategy dropdown (no inline rule editing), lookback selector, sortable results table (return %, Sharpe, win rate, max DD, signal_now). Spawn Bot pre-fills AddBotBar via localStorage pending-spawn key. Batch error handling fix in `backtest_quick.py` (individual ticker failures no longer abort the batch). `onSpawnBot` handler wired through App.tsx → Discovery.tsx → SignalScanner.tsx; AddBotBar reads pending-spawn on mount.

## 2026-04-28

- **[D10](TODO.md#d--bots-live-trading)** compact sparkline alignment fix. Multiple iterations. Real root cause: compact row was a flat flex layout where sparkline position depended on variable-width text before it. Fix: restructured to two-column layout mirroring expanded mode — `flex: 1` left column (text/buttons) + `flex: 0 0 60%` right column (sparkline). Also fixed overflow menu z-index (removed `scale: '1'` creating stacking contexts on idle SortableBotCard wrappers) and moved buttons before sparkline.
- **[D10](TODO.md#d--bots-live-trading)** compact cards: inline buttons. Replaced overflow dropdown menu with inline action buttons (Backtest, Stop, Buy, Reset, Delete). Right-aligned via `marginLeft: auto`. Simpler code, better UX, net -29 lines.
- **[A1](TODO.md#a--charts--indicators)** portfolio sparkline alignment. Matched PortfolioStrip horizontal padding and gap to bot card values so sparkline left edges line up vertically across portfolio and bot rows.
- Sparkline instant settle on load. Added `fitContent()` call to MiniSparkline's ResizeObserver — charts were bunching to the right on page load because the initial mount width was stale and `fitContent` wasn't re-called after resize.
- Tab persistence. Active tab (Chart/Live Trading/Discovery) now persists to localStorage across page reloads.
- **[D5](TODO.md#d--bots-live-trading)** `list_bots` perf fix. Journal was read + parsed 27 times per `list_bots` call (3 functions x 9 bots). Added `_load_trades()` helper and optional `trades` parameter — now reads once and passes through. Live Trading page load went from 3-5s to instant.
- Build fixes. Resolved 6 pre-existing `tsc -b` errors in Chart.tsx (unused var, Group ref type) and chart-mount.test.tsx (circular type, unused vars). Prod build now passes clean.

## 2026-04-27

- 15-task blitz session using parallel subagent orchestration. Established the pattern: main session orchestrates (picks tasks, writes specs, dispatches, verifies diffs, commits), subagents do the heavy lifting in their own context windows. Four agents ran simultaneously at peak. Context needle barely moved in the main session despite the volume.

- **[B15](TODO.md#b--strategy-engine--rules)** MACD crossover fix. `crossover_up`/`crossover_down` conditions never fired because the frontend never set `rule.param` to `'signal'`. Auto-set on rule creation (`emptyRule()`), indicator change, condition change, and `migrateRule()` for existing saved strategies.

- **[D1](TODO.md#d--bots-live-trading)** Global timezone toggle. Header button switches all timestamps between ET (EST/EDT) and browser-local time (CET/CEST). `useSyncExternalStore`-based so formatting functions read the mode directly without requiring hooks at every call site. Persisted to localStorage.

- **[C3](TODO.md#c--strategy-summary--analytics)** Sharpe/DD color bands. Sharpe: green >=1, orange 0.5-1, red <0 (underperforms cash), gray 0-0.5. Max DD: red >=10%, gray <10%.

- **[C2](TODO.md#c--strategy-summary--analytics)** Alpha vs B&H metric. Replaced raw "B&H Return" with "vs B&H" showing outperformance delta. Green when strategy beats buy-and-hold, red when it doesn't, regardless of absolute return sign.

- **[B16](TODO.md#b--strategy-engine--rules)** Ghost trade markers verified fixed. Interval change clears `backtestResult` (-> markers), view interval change recomputes markers via useMemo. No stale markers survive. Marked done without code changes.

- **[C4](TODO.md#c--strategy-summary--analytics)** Histogram zero line + labels. Zero baseline, brighter vertical dashed line, min/max/$0 tick labels below bars with `$1.2k` shorthand.

- **[F1](TODO.md#f--architecture--housekeeping)** Paper Trading -> Live Trading. Tab label rename only; filenames/imports left alone.

- **[B10](TODO.md#b--strategy-engine--rules)** Spread gate frontend wiring. AddBotBar: "Max Spread bps" input (default 50). BotCard: inline-editable spread cap (same pattern as allocation). Empty/0 = disabled. Backend was already shipped.

- **[F9](TODO.md#f--architecture--housekeeping)** `STRATEGYLAB_DATA_DIR` env var. Threads through journal, bot_manager, watchlist. Defaults to `backend/data/`, auto-creates on startup. Data now survives `git clean`.

- **[D4](TODO.md#d--bots-live-trading)** Dead `BotState.total_pnl` removed. Never written by bot_runner; P&L is live-computed via `compute_realized_pnl()`. Old bots.json silently ignores the field via `from_dict()` filtering.

- **[C1](TODO.md#c--strategy-summary--analytics)** Inline range bars on waterfall. Removed the separate Biggest/Avg/Smallest stat column. Each Wins/Losses row now has an inline min-max range bar (4px, muted) with a brighter avg tick.

- **[B17](TODO.md#b--strategy-engine--rules)** Minimal trade markers. Tooltip was already implemented (B18 shipped it). Stripped text labels from main-chart markers; subpane markers retain labels. Clean arrows colored by P&L outcome.

- **[B11](TODO.md#b--strategy-engine--rules)** Strategy library UX. Inline rename + pin-to-top for saved strategies. Pinned sort first with star prefix in dropdown. Backward-compatible with old saves.

- **[D9](TODO.md#d--bots-live-trading)** Partial-position reconciliation. Tracks `_last_broker_qty` between ticks, logs WARN on external shrinkage (e.g. broker forced buy-in). Guards against false positives on first tick, pending orders, and full closures.

- **[B2](TODO.md#b--strategy-engine--rules)** Extended hours wiring. Alpaca client-side RTH filter (9:30-16:00 ET) when `extended_hours=False`. Yahoo/IBKR were already wired natively. Cache keys already included `extended_hours`.

- Workflow docs shipped. Parallel subagent orchestration pattern documented in CLAUDE.md + memory. Review workflow: full cycle for logic-touching tasks, skip for trivial (renames, colors, <10 lines).

- **[A1](TODO.md#a--charts--indicators)** Portfolio summary strip (earlier session). Staircase-merged sparkline + summary stats (Total P&L $/%, Allocated, Running/Total, Profitable bots).

- 6-feature parallel blitz (~15 min wall-clock). **[A5](TODO.md#a--charts--indicators)** resizable chart panes, **[A6](TODO.md#a--charts--indicators)** watchlist sidebar, **[B13](TODO.md#b--strategy-engine--rules)** BB/ATR/Volume rules, **[D2](TODO.md#d--bots-live-trading)** bot drag-to-reorder, **[F4](TODO.md#f--architecture--housekeeping)** frontend test harness, sparkline hover tooltip. Six worktree agents dispatched simultaneously, zero code conflicts, all working on first load. [Post-mortem](docs/postmortems/2026-04-27-session-postmortem-2.md)

---

## Pre-journal

_Everything shipped before the journal started (2026-04-27). Grouped by theme._

### Foundation & Architecture

- **[F13](TODO.md#f--architecture--housekeeping)** Structural refactoring — extract models/journal/bot_runner, split BotControlCenter, shared utils, centralize API client
- **[F5](TODO.md#f--architecture--housekeeping)** `./start.sh --prod` flag — builds frontend via `vite preview`, backend without `--reload`, cleared pre-existing TS errors so `npm run build` passes `tsc -b` clean
- **[F11](TODO.md#f--architecture--housekeeping)** Chart mount-once refactor — three `IChartApi` panes created once and kept alive across ticker/interval/indicator changes; teardown hardened against sibling-pane races, `AbortController`s on trading pollers, `ErrorBoundary` in `main.tsx`
- **[F12](TODO.md#f--architecture--housekeeping)** Idle-CPU reduction — dev ~107% to ~1.3%, prod ~58% to ~5%. MiniSparkline mount-once pattern, `useCallback` polling timers, JSON-diff guards on state setters, PositionsTable journal poll relaxed to 60s
- **[E6](TODO.md#e--discovery)** Moved signal scanner from bot page to new Discovery tab

### Charts & Visualization

- **[A4](TODO.md#a--charts--indicators)** Indicator system redesign — instance-based model replacing hardcoded toggles, `INDICATOR_DEFS` registry, `SubPane` + `PaneRegistry`, inline param editing, POST-based indicator endpoint
- **[A8a](TODO.md#a--charts--indicators)** Chart perf 10x — cached `Intl.DateTimeFormat` in `toET()`, consolidated EMA overlays from hundreds of `LineSeries` to 2 per overlay, gated SPY/QQQ fetches on toggle
- **[A8b](TODO.md#a--charts--indicators)** Chart display interval ("View as") — decouple display from backtest interval, trade markers snap to display bars with aggregation
- **[A2](TODO.md#a--charts--indicators)** Equity curve macro mode — resampled equity chart (D/W/M/Q/Y) via `MacroEquityChart.tsx` + `/api/backtest/macro`
- **[A9](TODO.md#a--charts--indicators)** Date range presets (D/W/M/Q/Y) + period stepping arrows
- **[A10](TODO.md#a--charts--indicators)** Equity curve: normalised B&H comparison + log scale toggles
- **[A11](TODO.md#a--charts--indicators)** MA8/MA21 with SMA/EMA/RMA selector + Savitzky-Golay smoothed variants
- **[A12](TODO.md#a--charts--indicators)** Baseline (buy & hold) overlay toggle on equity curve

### Strategy Engine

- **[B19](TODO.md#b--strategy-engine--rules)** Shorting — direction field end-to-end (backtest, bot runner, chart markers, bot cards, stop-loss inversion)
- **[B1](TODO.md#b--strategy-engine--rules)** Skip N trades after stop-loss + configurable downside trigger
- **[B3](TODO.md#b--strategy-engine--rules)** MA slope conditions (`turns_up`/`turns_down`) + `decelerating` via Savitzky-Golay second derivative
- **[B6](TODO.md#b--strategy-engine--rules)** Realistic cost model — IBKR Fixed per-share commission, empirical slippage, short borrow cost
- **[B7](TODO.md#b--strategy-engine--rules)** Slippage model redesign — separate measured vs modeled, always >= 0, floor at default, single signed-cost helper
- **[B12](TODO.md#b--strategy-engine--rules)** Parameterized MAs — generic `ma(period, type)` replacing 5 hardcoded entries, removed all S-G smoothing code
- **[B18](TODO.md#b--strategy-engine--rules)** Triggering rules in trade tooltip — backend tags each trade with fired `rules`, entries show buy rules, exits show sell rules or mechanical stop type

### Analytics

- **[C5](TODO.md#c--strategy-summary--analytics)** Expected value / profit factor — EV + PF headline numbers, 3-row decomposition waterfall
- **[C6](TODO.md#c--strategy-summary--analytics)** Strategy summary: min/max/avg gain and loss
- **[C7](TODO.md#c--strategy-summary--analytics)** Summary readability pass — size hierarchy on metrics row, renamed labels, dropped `(mean)` suffix

### Bot System & IBKR

- **[D11](TODO.md#d--bots-live-trading)** IBKR broker integration — `ib_insync` provider behind `TradingProvider` protocol, global broker selector, simultaneous long+short on same symbol
- **[D12](TODO.md#d--bots-live-trading)** IBKR stability pass — heartbeat auto-reconnect with clientId rotation, `ib.portfolio()` for live data, phantom SELL prevention
- **[D6 + D7](TODO.md#d--bots-live-trading)** IBKR heartbeat + multi-broker union — HeartbeatMonitor 5s pings, aggregate positions/orders across brokers, broker column + health dots + filters
- **[D8](TODO.md#d--bots-live-trading)** IBKR reliability overhaul — error event classification (structural vs transient), reconnect dedup via asyncio.Lock, exponential backoff, 3s TTL cache, adaptive UI polling
- **[D3](TODO.md#d--bots-live-trading)** Bot lifecycle: soft P&L reset via `pnl_epoch`, journal untouched, orphan row marking
- **[D13](TODO.md#d--bots-live-trading)** Paper trading polish — journal reason colors, Expected/Gain% columns, summary row, CSV export, heartbeat dots, position polling
- **[D14](TODO.md#d--bots-live-trading)** Track actual slippage — poll broker fill prices, log expected vs actual, surface in journal
- **[D15](TODO.md#d--bots-live-trading)** Manual buy on bot to start a position
- **[D16](TODO.md#d--bots-live-trading)** Editable allocation and strategy on bot card (click when stopped)
- **[D17](TODO.md#d--bots-live-trading)** Global start/stop all bots
- **[D18](TODO.md#d--bots-live-trading)** Bot sparkline: local vs aligned timescale toggle
- Verify allocation logic (was position_size=10.0, clamped to 0.01-1.0)
- Buying amount compounds P&L (allocated_capital + total_pnl), matching backtest
- Refresh button on journal
- UI update frequency 5s to 2s for bot list + detail polling
- Position size: removed slider, hardcoded 100%

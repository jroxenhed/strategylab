# Journal

What we've actually shipped. Reverse-chronological, one section per working day. Bold IDs (e.g. **[D10](TODO.md#d--bots-live-trading)**) cross-reference [TODO.md](TODO.md).

> **Maintenance rule (Claude):** append an entry at the end of any session that produces durable work — TODO closures, features, bug fixes, discoveries. Skip routine commits (typo fixes, reformatting). Keep bullets short; link to the commit or doc if more context is worth a click. Don't re-read every TODO to write an entry — just log what happened in the session.

## 2026-05-04 (overnight build 10)

- **[C23](TODO.md#c--strategy-summary--analytics)** Sweep error banner — replaced plain `color: red` div with a styled banner (tinted background + red border + ✕ icon) when the sensitivity sweep fails. The `apiErrorDetail()` helper already extracted the error string; only the presentation changed.

- **[C24](TODO.md#c--strategy-summary--analytics)** Direction-aware analytics — (1) `sell_trades` filter in `backtest.py` now includes both `"sell"` and `"cover"` exits (was gated on `req.direction`), fixing `num_trades` and `win_rate` for regime `close_and_reverse` strategies; (2) Trades tab column headers in `Results.tsx` adapt to "Entry"/"Exit" when short or mixed-direction trades detected. Audited all other analytics: streaks, MC, rolling, hold time, Kelly already used direction-agnostic PnL-sign logic.

- **[B26](TODO.md#b--strategy-engine--rules)** Sweep from rule row — TrendingUp icon button on rule rows with a numeric threshold (`needsValue && typeof rule.value === 'number'`). Clicking it: (a) pre-fills the Sensitivity tab with the rule's `param_path` selected and ±50% range around the current value, (b) switches the Results panel to the Sensitivity tab. Prop chain: `RuleRow.onSweep → StrategyBuilder.onSweep → App → setSweepInit + setResultsTab('sensitivity') → Results.sweepInit → SensitivityPanel` applies via `useEffect`. Button gates on `onSweep` being defined so regime rule rows (not yet sweep-supported) stay clean.

## 2026-05-04

- **[A8d](TODO.md#a--chart--data)** Same-TF indicator resample to view interval — when "View as" selects a coarser interval, indicators compute at the backtest interval and resample via backend `view_interval` field. Five follow-up fixes during visual testing: (1) removed `origin='start'` from resample — caused 30min offset vs provider clock-hour boundaries; (2) HTF queries use `viewInterval` for alignment so HTF indicators don't inject backtest-interval timestamps; (3) regime background uses `snapTimestamp` (replaces incomplete `snapRegimeTime`); (4) rule signal markers snapped+deduped when aggregated; (5) EMA overlays un-gated and timestamps snapped for coarser views. Trade tooltip SNAP tolerance ±5 candles when aggregated. [Plan](docs/superpowers/plans/2026-05-04-a8d-same-tf-indicator-resample.md)

## 2026-05-03 (review session 2)

- PR #12 (build 7) review: 3 P1 fixes (await→create_task notify_error, negative sweep guard, was_running in list_bots API).
- PR #13 (build 8) review: clean — 0 P0/P1, merged as-is.
- PR #14 (F21 bot_runner split) review: 1 P1 fix (regime.py inline imports bypassing `_br()` pattern). Tests pass 7/7 post-fix.
- PR #15 (build 9) review: 2 P1 fixes (float→int period params for BB/ATR/Stochastic/ADX sweep, skip invalid values instead of aborting sweep).
- Added C24 (regime/short direction-aware analytics), A8d (indicator resample on coarser view).

## 2026-05-03 (overnight build 9)

- **[C21](TODO.md#c--strategy-summary--analytics)** Sensitivity sweep param bug — (1) fixed error swallowing in sweep loop (HTTPException from run_backtest now propagates instead of returning silent zero-result rows); (2) added `rule.params` sweep support in backend `_apply_param` (`buy_rule_{i}_params_{key}`, `sell_rule_{i}_params_{key}`) and frontend `buildParamOptions` (sweeps MA period, RSI period, Stochastic k/d, ADX period, BB std_dev etc.); (3) three P2 review fixes: max_drawdown color `highIsGood=true`, integer rounding for period linspace values, selectedPath reset on lastRequest change.
- **[C20](TODO.md#c--strategy-summary--analytics)** Equity curve blank — fixed rendering logic in Results.tsx: `bucket && macroData ? <MacroEquityChart> : <div ref={chartRef}>` created a blank state when macro bucket selected but data still loading (chartRef div rendered but effect returned early). Two-level ternary fix: chartRef div now only renders when `bucket === null`; loading/no-data states show a placeholder.

## 2026-05-03 (overnight build 8, part 2)

- **[F21](TODO.md#f--architecture--housekeeping)** Split bot_runner.py (1030 → 511 lines). `RegimeMixin` in `regime.py` holds `_eval_regime_direction` + `_handle_regime_flip`. `ExitsMixin` in `exits.py` holds `_detect_external_close` + `_evaluate_exit_reason` + `_execute_exit`. `BotRunner` inherits from both. `_tick()` is now a thin orchestrator. Both mixin files use a `_br()` helper (`sys.modules["bot_runner"]`) so test patches apply correctly at call time. All 7 F20 tests pass post-split.

## 2026-05-03 (review sessions)

- **[D24](TODO.md#d--bots-live-trading)** PR #10 review: 6 P1 fixes (dual-rule indicators, stale trail_stop, skip_remaining bypass, consec_sl on regime flip, manual_buy PnL, stop_bot regime state).
- **[F14](TODO.md#f--architecture--housekeeping)**/**[F15](TODO.md#f--architecture--housekeeping)**/**[F16](TODO.md#f--architecture--housekeeping)** PR #11 review: 2 P1 hardening fixes (unique tempfile paths for concurrent save(), atomic journal writes so readers never see partial JSON).
- **[F23](TODO.md#f--architecture--housekeeping)** Shipped in PR #12 review: `was_running` added to `list_bots()` response + `BotSummary` TS type.
- PR #12 review: 3 P1 fixes (await→create_task for notify_error, negative sweep guard, was_running in API).
- PR #13 review: clean — 0 P0/P1, merged as-is.
- Architectural audit: added F14-F21 (safety fixes, test harness, bot_runner split). Overnight task limit raised 3→5.
- Market research: StrategyLab's 10-capability combination (no-code builder + regime filter + multi-broker + Monte Carlo + sensitivity sweep + live bot dashboard) has no retail equivalent.

## 2026-05-03 (overnight build 8)

- **[F22](TODO.md#f--architecture--housekeeping)** `was_running` badge on BotCard. Stopped bots with `was_running=True` now show an amber "⚡ Was running" badge in both compact and expanded layouts, prompting the user to restart bots that were live before a server restart. Also fixed a P2: `was_running` is now reset to `False` in `BotRunner.run()` so the badge clears once the bot is started and then manually stopped (no stale badge after the first restart cycle).

- **[C18b](TODO.md#c--strategy-summary--analytics)** Sensitivity sweep sparkline. SVG mini line chart above the results table in `SensitivityPanel` showing `total_return_pct` vs `param_value`. Dot colors teal/red by sign, dashed zero-baseline when range straddles zero, footer labels show param range. Renders when ≥2 sweep points available. Makes cliff-edge vs smooth plateau visible at a glance. `preserveAspectRatio="none"` ensures full-width fill on any container width. Not visually verified.

- **[A8c-htf](TODO.md#a--charts--indicators)** HTF overlay line type fix. `LineType.WithSteps` is no longer applied when `viewInterval === inst.htfInterval` (i.e., the chart is already at the same resolution as the HTF data). Added `viewInterval` to the overlay series effect deps so the lineType decision re-evaluates when the user changes view intervals. Not visually verified.

## 2026-05-03 (overnight build 7)

- **[F6](TODO.md#f--architecture--housekeeping)** Split `shared/types/index.ts` into domain files. Created `chart.ts` (OHLCV, TimeValue, Macro* types), `strategy.ts` (Rule, StrategyRequest, BacktestResult, etc.), `trading.ts` (BotConfig, BotState, BotSummary, etc.). `index.ts` now a barrel re-export — all 30+ external imports unchanged, no consumer updates needed.

- **[D23](TODO.md#d--bots-live-trading)** Bot daily P&L bar chart on BotCard. `DailyPnlChart` component: groups equity_snapshots by ET date, computes per-day P&L as day-over-day delta, renders as SVG bar chart (green/red, last 30 days, zero-line, date labels). Shown in BotCard expanded view when ≥2 snapshots available. Pure frontend, no new API calls.

- **[C18](TODO.md#c--strategy-summary--analytics)** Parameter sensitivity sweep. `POST /api/backtest/sweep` endpoint (`routes/backtest_sweep.py`): accepts base StrategyRequest + `param_path` + `values[]`, runs up to 25 backtest variants with one parameter varied, returns summary stats per variant. Supports `stop_loss_pct`, `trailing_stop_value`, `slippage_bps`, `buy_rule_{i}_value`, `sell_rule_{i}_value`. Frontend: `SensitivityPanel` component with param dropdown, min/max/steps inputs, color-coded results table (green=best, red=worst per column). New "Sensitivity" tab in Results, gated on `lastRequest` being available. Not visually verified.

- **[F17](TODO.md#f--architecture--housekeeping)** Bot auto-resume flag. Added `was_running: bool = False` to `BotState`. `BotManager.load()` now sets `state.was_running = state.status == "running"` before resetting all statuses to "stopped". Persisted to bots.json; `from_dict()` picks it up via the generic setattr loop. UI exposure deferred to F22.

- **[F18](TODO.md#f--architecture--housekeeping)** Cap equity_snapshots growth. All 3 `equity_snapshots.append()` sites in `bot_runner.py` now trim to `[-500:]` after each append. Prevents bots.json from growing unboundedly with active bots.

- **[D24c](TODO.md#d--bots-live-trading)** Regime HTF fetch timeout. Wrapped the `fetch_higher_tf` executor call in `asyncio.wait_for(..., timeout=15.0)`. On timeout logs WARN and returns `"flat"` (conservative gate-closed). Prevents a hanging data provider from blocking `_tick()` and stalling stop-loss checks for open positions.

- **[F20](TODO.md#f--architecture--housekeeping)** bot_runner test harness. `backend/tests/test_bot_runner.py`: 7 tests covering key `_tick()` state transitions — no-entry-outside-hours, entry-on-buy-signal, no-entry-when-positioned, stop-loss-exit (long), sell-signal-exit, time-stop-exit, skip-entry-cooldown. `MockProvider` with call-count-aware `get_positions` (models the 3-call pattern: initial check, pre-close safety check, post-close verification). `_direct_executor` patch skips thread pool in tests. All 7 pass in 0.81s.

## 2026-05-03 (overnight build 6)

- **[F14](TODO.md#f--architecture--housekeeping)** Atomic bots.json writes. `BotManager.save()` now writes to a temp file (`DATA_PATH + ".tmp"`) then calls `os.replace()` (atomic on POSIX) so a crash during write can't corrupt or truncate `bots.json`.

- **[F15](TODO.md#f--architecture--housekeeping)** Log journal write errors. Changed all 5 `except Exception: pass` blocks wrapping `_log_trade()` calls (4 in `bot_runner.py`, 1 in `bot_manager.py`) to `except Exception as e: self._log("ERROR", f"Journal write failed: {e}")`. Trade execution at the broker now always surfaces journal failures in the bot's activity log.

- **[F16](TODO.md#f--architecture--housekeeping)** Journal write lock. Added `_journal_lock = threading.Lock()` in `journal.py`. The entire read-modify-write body of `_log_trade()` is now wrapped in `with _journal_lock:`, preventing two bots closing simultaneously from overwriting each other's entries. Slippage computation moved outside the lock (no shared state).

- **[D24a](TODO.md#d--bots-live-trading)** Regime bot backtest_bot() passthrough. Added the 9 missing fields to the `StrategyRequest` constructor in `backtest_bot()`: `regime`, `long_buy_rules`, `long_sell_rules`, `long_buy_logic`, `long_sell_logic`, `short_buy_rules`, `short_sell_rules`, `short_buy_logic`, `short_sell_logic`. Regime bots now backtest with their actual regime + dual-rule config instead of silently ignoring it.

- **[D25](TODO.md#d--bots-live-trading)** Opposite-direction entry guard: skip on error. The `except Exception: pass` block in the position-check guard before entry now logs a WARN and returns (skips entry) instead of proceeding. A broker check failure during a regime bot entry previously risked opening a position that bypassed the opposite-direction guard entirely.

## 2026-05-03 (review session)

- **[D24](TODO.md#d--bots-live-trading)** PR #10 code review — 4 parallel persona agents (correctness, reliability, adversarial, API contract). Found 6 P1 issues, all fixed in `008e70e`: (1) dual-rule indicators not included in `compute_indicators` call, (2) stale `trail_stop_price`/`trail_peak`/`entry_bar_count` on pending flip resolution, (3) `skip_remaining` cooldown bypassed on `close_and_reverse` re-entry, (4) `consec_sl_count` incorrectly incremented on regime flip, (5) `manual_buy` used unidirectional PnL for regime bots, (6) `stop_bot` didn't clear `position_direction`/`pending_regime_flip`. Added D24c, D24d, D25 to TODO from deferred P2 findings.

## 2026-05-03 (overnight build 5)

- **[D24](TODO.md#d--bots-live-trading)** Regime filter live bot integration. Removed the `regime.enabled` guard that rejected regime bots. `_eval_regime_direction()` async method: fetches HTF bars with `htf_lookback_days` lookback, computes regime indicator via `compute_instance()`, applies condition + `min_bars` rolling smoothing, aligns to LTF index via `align_htf_to_ltf()`, returns "long"/"short"/"flat" (conservative "flat" on any error). `_handle_regime_flip()` method: cancels pending orders, calls `provider.close_position()`, polls fill, waits ≤3s for position clear; if not cleared sets `pending_regime_flip = True` for retry next tick; on success logs trade + equity snapshot; if `close_and_reverse` and direction not flat, calls `_enter_position()` before returning. `_enter_position()` extracted helper: handles OTO bracket / plain order for long/short, polls fill, sets `state.position_direction`. `_bot_pnl()` helper: calls `compute_bidirectional_pnl` for regime bots, `compute_realized_pnl` for others. `is_short` global variable removed from `_tick()`; replaced with `entry_is_short` (entry direction) and `pos_is_short` (position direction, re-derived when entering the has-position branch). `position_direction` tracked per entry; `regime_direction` updated each tick. Same-symbol guard in `start_bot()` updated: regime bots require exclusive symbol access (block both ways). `compute_bidirectional_pnl` + `first_bot_bidirectional_entry_time` added to `journal.py`. `list_bots()` uses bidirectional helpers for regime bots; returns `regime_direction`, `position_direction`, `pending_regime_flip`. Dual rule fields (`long_buy_rules` etc.) added to `BotConfig`, `UpdateBotRequest`, `BotConfig` TS type. `BotSummary` TS type gets `regime_direction`, `position_direction`, `pending_regime_flip`. `AddBotBar` passes regime config + dual rules from `SavedStrategy`. `BotCard` shows "Regime" stat cell: ▲ Long / ▼ Short / ⊘ Flat / ⏳ Pending flip. Not visually verified — live regime bot needs browser/paper-trading QA (D24b).

## 2026-05-03 (overnight build 4)

- **[B21a](TODO.md#b--strategy-engine--rules)** Regime config not restored on page refresh. Root cause: the `localStorage` persistence effect in `StrategyBuilder.tsx` was missing `regimeEnabled`/`regimeConfig` from both the serialized JSON and its dependency array. Fix: added `regime: { ...regimeConfig, enabled: regimeEnabled }` to the JSON and both vars to the dep array. `loadStrategy()` now returns the saved regime config so `useState(saved?.regime?.enabled)` initializes correctly on refresh.

- **[A8c](TODO.md#a--charts--indicators)** "View as" 1D axis confusion fix. Root cause: regime histogram in `Chart.tsx` used raw intraday unix timestamps (`toET()` output) while daily candle series uses YYYY-MM-DD strings — mixed timestamp formats confuse lightweight-charts' time scale, producing thin candles and mixed axis labels. Fix: local `snapRegimeTime` helper in the regime histogram effect converts unix timestamps to `YYYY-MM-DD` when `viewInterval` is non-intraday; Map-based deduplication keeps last direction per day; `viewInterval` added to effect dependency array. Remaining issue (deferred): HTF overlay renders as smooth instead of stepped when `viewInterval === htfInterval` — cosmetic, added as `A8c-htf` to TODO.

- **[B23](TODO.md#b--strategy-engine--rules)** Regime dual rule sets. Backend: 8 optional fields (`long_buy_rules`, `long_sell_rules`, `long_buy_logic`, `long_sell_logic`, + short variants) added to `StrategyRequest` in `models.py`. `b23_mode` detection in `run_backtest`: active when regime enabled + both long and short buy rules non-empty. Main loop: entry routes to `long_buy_rules` when regime active, `short_buy_rules` when inactive; `position_direction` set from regime state (always 'long' or 'short', no `req.direction` indirection); exit routes sell rules by `position_direction`. Frontend: 8 new state vars in `StrategyBuilder.tsx` initialized to `[]` (not `[emptyRule()]` — prevents accidental b23 activation); Single/▲Long/▼Short tab bar renders under regime section; dual rules spread into backtest request when regime enabled and both long+short buy rules non-empty. `SavedStrategy` and `StrategyRequest` TS types updated. Not visually verified.

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

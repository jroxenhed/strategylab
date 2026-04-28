# Journal

What we've actually shipped. Reverse-chronological, one section per working day. Bold IDs (e.g. **[D10](TODO.md#d--bots-live-trading)**) cross-reference [TODO.md](TODO.md).

> **Maintenance rule (Claude):** append an entry at the end of any session that produces durable work — TODO closures, features, bug fixes, discoveries. Skip routine commits (typo fixes, reformatting). Keep bullets short; link to the commit or doc if more context is worth a click. Don't re-read every TODO to write an entry — just log what happened in the session.

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

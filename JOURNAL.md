# Journal

What we've actually shipped. Reverse-chronological, one section per working day. Closed-TODO IDs in parens where they apply.

> **Maintenance rule (Claude):** append an entry at the end of any session that produces durable work — TODO closures, features, bug fixes, discoveries. Skip routine commits (typo fixes, reformatting). Keep bullets short; link to the commit or doc if more context is worth a click. Don't re-read every TODO to write an entry — just log what happened in the session.

## 2026-04-28

- **D10 compact sparkline alignment fix.** Multiple iterations. Real root cause: compact row was a flat flex layout where sparkline position depended on variable-width text before it. Fix: restructured to two-column layout mirroring expanded mode — `flex: 1` left column (text/buttons) + `flex: 0 0 60%` right column (sparkline). Also fixed overflow menu z-index (removed `scale: '1'` creating stacking contexts on idle SortableBotCard wrappers) and moved buttons before sparkline.

## 2026-04-27

- **15-task blitz session using parallel subagent orchestration.** Established the pattern: main session orchestrates (picks tasks, writes specs, dispatches, verifies diffs, commits), subagents do the heavy lifting in their own context windows. Four agents ran simultaneously at peak. Context needle barely moved in the main session despite the volume.

- **B15 — MACD crossover fix.** `crossover_up`/`crossover_down` conditions never fired because the frontend never set `rule.param` to `'signal'`. Auto-set on rule creation (`emptyRule()`), indicator change, condition change, and `migrateRule()` for existing saved strategies.

- **D1 — Global timezone toggle.** Header button switches all timestamps between ET (EST/EDT) and browser-local time (CET/CEST). `useSyncExternalStore`-based so formatting functions read the mode directly without requiring hooks at every call site. Persisted to localStorage.

- **C3 — Sharpe/DD color bands.** Sharpe: green >=1, orange 0.5-1, red <0 (underperforms cash), gray 0-0.5. Max DD: red >=10%, gray <10%.

- **C2 — Alpha vs B&H metric.** Replaced raw "B&H Return" with "vs B&H" showing outperformance delta. Green when strategy beats buy-and-hold, red when it doesn't, regardless of absolute return sign.

- **B16 — Ghost trade markers verified fixed.** Interval change clears `backtestResult` (-> markers), view interval change recomputes markers via useMemo. No stale markers survive. Marked done without code changes.

- **C4 — Histogram zero line + labels.** Zero baseline, brighter vertical dashed line, min/max/$0 tick labels below bars with `$1.2k` shorthand.

- **F1 — Paper Trading -> Live Trading.** Tab label rename only; filenames/imports left alone.

- **B10 — Spread gate frontend wiring.** AddBotBar: "Max Spread bps" input (default 50). BotCard: inline-editable spread cap (same pattern as allocation). Empty/0 = disabled. Backend was already shipped.

- **F9 — `STRATEGYLAB_DATA_DIR` env var.** Threads through journal, bot_manager, watchlist. Defaults to `backend/data/`, auto-creates on startup. Data now survives `git clean`.

- **D4 — Dead `BotState.total_pnl` removed.** Never written by bot_runner; P&L is live-computed via `compute_realized_pnl()`. Old bots.json silently ignores the field via `from_dict()` filtering.

- **C1 — Inline range bars on waterfall.** Removed the separate Biggest/Avg/Smallest stat column. Each Wins/Losses row now has an inline min-max range bar (4px, muted) with a brighter avg tick.

- **B17 — Minimal trade markers.** Tooltip was already implemented (B18 shipped it). Stripped text labels from main-chart markers; subpane markers retain labels. Clean arrows colored by P&L outcome.

- **B11 — Strategy library UX.** Inline rename + pin-to-top for saved strategies. Pinned sort first with star prefix in dropdown. Backward-compatible with old saves.

- **D9 — Partial-position reconciliation.** Tracks `_last_broker_qty` between ticks, logs WARN on external shrinkage (e.g. broker forced buy-in). Guards against false positives on first tick, pending orders, and full closures.

- **B2 — Extended hours wiring.** Alpaca client-side RTH filter (9:30-16:00 ET) when `extended_hours=False`. Yahoo/IBKR were already wired natively. Cache keys already included `extended_hours`.

- **Workflow docs shipped.** Parallel subagent orchestration pattern documented in CLAUDE.md + memory. Review workflow: full cycle for logic-touching tasks, skip for trivial (renames, colors, <10 lines).

- **A1 — Portfolio summary strip** (earlier session). Staircase-merged sparkline + summary stats (Total P&L $/%, Allocated, Running/Total, Profitable bots).

- **6-feature parallel blitz** (~15 min wall-clock). A5 resizable chart panes, A6 watchlist sidebar, B13 BB/ATR/Volume rules, D2 bot drag-to-reorder, F4 frontend test harness, sparkline hover tooltip. Six worktree agents dispatched simultaneously, zero code conflicts, all working on first load. [Post-mortem](docs/postmortems/2026-04-27-session-postmortem-2.md)

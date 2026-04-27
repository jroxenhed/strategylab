# Journal

What we've actually shipped. Reverse-chronological, one section per working day. Closed-TODO IDs in parens where they apply.

> **Maintenance rule (Claude):** append an entry at the end of any session that produces durable work — TODO closures, features, bug fixes, discoveries. Skip routine commits (typo fixes, reformatting). Keep bullets short; link to the commit or doc if more context is worth a click. Don't re-read every TODO to write an entry — just log what happened in the session.

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

---

### Session post-mortem: 6-feature parallel blitz (22:00)

**What happened:** User said "spin up all of them" for 6 independent TODO items. Main session scoped each feature in conversation (Phase 1), then dispatched 6 subagents in parallel worktrees with detailed briefs. All 6 completed, were reviewed, merged, and visually confirmed working on first load. Zero code-level merge conflicts (only package-lock.json, resolved with `npm install`). Total wall-clock ~15 min from dispatch to push.

**Features shipped:**
- **A5** — Resizable chart panes (react-resizable-panels, TV-style double-click maximize/restore, localStorage persist)
- **A6** — Watchlist sidebar (compact price rows, resizable divider, batch quote endpoint, 30s polling)
- **B13** — BB/ATR/ATR%/Volume as rule indicators (multi-output addressing, cross-references, full param UI)
- **D2** — Bot drag-to-reorder (@dnd-kit, backend persist, handles add/delete gracefully)
- **F4** — Frontend test harness (Vitest + RTL, 27 tests in 1.2s targeting historically buggy paths)
- **Sparkline tooltip** — Date/time + equity hover overlay on bot card sparklines (direct DOM via refs)

**What worked:**
1. **Scoping before dispatch.** Quick brainstorm with user nailed down TV-style behaviors, sidebar placement, etc. Agents got unambiguous briefs — no wasted cycles on design decisions.
2. **Worktree isolation.** Six agents editing different files in parallel without stepping on each other. Only conflict was package-lock.json between F4 (vitest) and D2 (@dnd-kit) — trivially resolved.
3. **Targeted pre-dispatch research.** Three quick greps (sidebar layout, existing deps, Chart.tsx height logic) before writing briefs. Just enough context for good briefs without burning main session context.
4. **Review while waiting.** Reviewed each agent's diff as it landed. Caught the pre-existing test failure (test_backtest_costs.py) early and confirmed it wasn't caused by B13.
5. **Agent brief quality.** Each brief included: what to build, current architecture context, CLAUDE.md gotchas to preserve, how to verify. The Chart.tsx brief explicitly warned about teardown race guards and syncWidths — the agent preserved both.

**What could be better:**
1. **Agents don't commit.** Every agent left changes uncommitted in the worktree. Had to commit for each one manually before merging. Could add "commit your work when done" to briefs.
2. **No visual verification by agents.** All six flagged "not visually verified." The user caught success on first load, but a bad merge could have required debugging. Consider spinning up a verification agent post-merge.
3. **Package-lock merge pain.** Two agents independently `npm install`-ed different packages. The lockfile conflict was trivial but could be avoided by having one agent handle all npm deps, or by batching package.json changes into main before dispatching.
4. **Merge commit messages.** Auto-generated "Merge branch 'worktree-agent-xxx'" messages clutter the log. Could squash-merge or use `--no-ff -m "feat: ..."` for cleaner history.

**Pattern validated:** The subagent-first workflow from CLAUDE.md scales well beyond the previous 15-task session. Key enabler is feature independence — when tasks don't share files, parallel worktrees are free parallelism. The bottleneck is Phase 1 (scoping with user), not Phase 3 (implementation).

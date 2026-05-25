# Post-first-live-run polish (Group A + C)

**Date:** 2026-04-11
**Scope:** Quick wins + architectural prep for Discovery page
**Out of scope:** Groups B and D (see `TODO.md` "Post-first-live-run notes" — each gets its own session)

## Context

After the first live paper-trading run, the user compiled a list of fixes, features, and new directions ranging from 10-minute tweaks to a multi-session research project (AI-assisted strategy discovery). The list was decomposed into four groups:

- **Group A** — quick wins, batch into one implementation
- **Group B** — each needs its own small design
- **Group C** — architectural precursor to Discovery (move signal scanner to its own page)
- **Group D** — Discovery page research project (deferred, own multi-session effort)

This spec covers **A + C** — the shippable slice for this session. Group C is included because it unblocks D's natural home.

## 1. Bot sparkline timescale toggle

**Problem.** Each bot card's `MiniSparkline` currently fits its own first→last trade window (local scale). When bots start at different times, you can't visually compare their P&L timing across cards.

**Solution.** Global toggle (`Local` / `Aligned`) in the bot page header above the grid. State persisted in `localStorage` under `sparklineScale`.

- **Local** (default, current behavior): each card calls `chart.timeScale().fitContent()`.
- **Aligned**: every card renders into a shared window `[min(first_trade_time across all bots), now]`. Cards with recent-only activity render as a small blip on the right; cards with longer history fill the width — the visual contrast itself is the signal.

**Window derivation.** Computed once at the `BotControlCenter` level from the bot summaries (already fetched) and passed down as a prop. Bots with zero trades contribute nothing and render empty (same as today).

**Files.**
- `frontend/src/features/trading/MiniSparkline.tsx` — accept optional `alignedRange?: {from: number; to: number}` prop; when provided, use `setVisibleRange()` instead of `fitContent()`.
- `frontend/src/features/trading/BotControlCenter.tsx` — compute aligned range, render the toggle, pass prop down.

**Edge cases.** Zero bots, zero trades, single trade — all fall back to `fitContent()` behavior naturally.

---

## 2. Global start/stop all bots

**Problem.** No way to start or stop all bots at once. End-of-day flatten and panic-stop both require clicking every card.

**Solution.** Three buttons in the bot page header:

- **Start All** — starts every bot currently in `stopped` status. Silently skips bots in `error` state (don't mask real problems — user can still see them and act).
- **Stop All** — stops every `running` bot; leaves positions open in the broker.
- **Stop and Close** — stops every running bot AND submits market-close orders for all open positions. **Requires confirm dialog:** *"Close N open positions at market?"*

**Backend.** Three new endpoints in `backend/routes/bots.py`:

- `POST /api/bots/start-all`
- `POST /api/bots/stop-all`
- `POST /api/bots/stop-and-close-all`

Each iterates `bot_manager.bots` applying the existing per-bot operation inside a try/except so one failure doesn't block the others. Response shape: `{started: string[], skipped: string[], failed: [{bot_id, error}]}`.

**Frontend.** Three button components in the header, each wired via the existing API client. Confirm dialog on the destructive one, then a toast summarizing the result.

---

## 3. Strategy summary: gain/loss distribution stats

**Problem.** The summary tab shows totals and win rate but nothing about the *shape* of the P&L distribution. Can't tell if the strategy is "consistent small wins" vs "one lucky trade carried the whole thing."

**Solution.** Add to the summary tab:

- **Six numbers** — min gain, max gain, avg gain, min loss, max loss, avg loss.
- **Mean / median toggle** for the `avg` values. Backend returns both; frontend renders the selected one. Default = mean (user familiarity); median is robust-to-outliers.
- **Distribution histogram** next to the numbers — bucketed per-trade P&L (green bars for gains, red for losses). Simple SVG or a minimal lightweight-charts histogram series, whichever is easier. Buckets auto-sized from the data range.

**Files.**
- `backend/routes/backtest.py` — extend the summary payload with `gain_stats`, `loss_stats` (each `{min, max, mean, median}`) and `pnl_distribution` (list of per-trade P&L values for the histogram — frontend does the bucketing).
- `frontend/src/features/strategy/Results.tsx` (or the summary sub-component) — add the stats block and histogram.

**Edge cases.** Zero trades → hide the entire block. Zero gains or zero losses → show `—` for that side.

---

## 4. Backtest equity curve: buy & hold overlay

**Problem.** Hard to tell if a strategy actually beat just holding the underlying over the same period.

**Solution.** Checkbox toggle above the equity curve: *"Show buy & hold baseline."* When on, the chart renders a second line computed as:

- Start with the same `initial_capital` as the backtest.
- Buy shares at day-1 open, hold through day-N close.
- **For short-direction strategies, still show long buy & hold.** The question users actually ask is "was shorting worth it vs. just holding?" — inverse-hold is weird and nobody does it in practice.

**Files.**
- `backend/routes/backtest.py` — when `include_baseline=true` in the request, add `baseline_curve: [{time, value}]` to the response. Computation is cheap — reuse the already-fetched OHLCV series.
- `frontend/src/features/strategy/EquityCurve.tsx` — add the toggle + render the second line series. Distinct color (grey or muted blue) so it's clearly a reference, not a primary series.

**Edge cases.** Strategy that never traded → baseline still renders, which is actually the point (shows the opportunity cost). Invalid/no data → hide the toggle.

---

## 5. Discovery page (architectural — Group C)

**Problem.** The Paper Trading page is becoming a kitchen sink: operational cockpit (bots, positions, journal) mixed with research tools (signal scanner, performance comparison). The scanner has no natural home once Group D (candidate discovery, batch backtesting, AI-assisted parameter tuning) lands.

**Solution.** Create a third top-level tab — **Discovery** — and migrate the two research components there.

**New layout:**

- **Paper Trading (operational cockpit):** `AccountBar`, `BotControlCenter`, `PositionsTable`, `TradeJournal`, `OrderHistory`
- **Discovery (research):** `SignalScanner`, `PerformanceComparison`

**Bare move.** No placeholder sections for Group D features. When that work lands, Discovery will be redesigned top-to-bottom — placeholders would rot in the meantime.

**Files.**
- `frontend/src/features/discovery/` — new directory.
- Move `SignalScanner.tsx` → `frontend/src/features/discovery/SignalScanner.tsx`
- Move `PerformanceComparison.tsx` → `frontend/src/features/discovery/PerformanceComparison.tsx`
- New `frontend/src/features/discovery/Discovery.tsx` — plain vertical stack of the two components, same style container as `PaperTrading.tsx`.
- `frontend/src/features/trading/PaperTrading.tsx` — drop the two imports and JSX lines.
- `frontend/src/App.tsx` — add `'discovery'` to the `AppTab` union, add the third tab button, add a conditional render block for it.
- Update any relative imports in the moved files as needed.

**Rearchitecture note.** Both `SignalScanner` and `PerformanceComparison` are flagged for rework when Group D begins. This move is purely a relocation — no internal changes.

---

## Implementation order

1. **Group C first** (Discovery move) — it's a pure refactor, touches App nav, best done alone and committed cleanly before touching feature code.
2. **Section 1** (sparkline toggle) — small, visual, easy to verify.
3. **Section 2** (global start/stop) — backend + frontend, isolated to bot page header.
4. **Section 4** (equity curve baseline) — backend + frontend, isolated to results.
5. **Section 3** (distribution stats + histogram) — largest of the UI pieces, do last.

Each section gets its own commit so if anything needs reverting it's surgical.

## Out of scope (parked in TODO.md)

- Skip N after SL / dynamic sizing scale-back / unification question (Group B)
- Equity curve macro mode for thousands of trades (Group B)
- Equity curve trend analysis (Group B — needs its own brainstorm on what "trend" means here)
- Bot reordering/grouping (Group B)
- Candidate scanning / batch backtest / AI parameter tuning / bot army (Group D)

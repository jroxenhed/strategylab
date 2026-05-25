# A1 — Portfolio Summary Strip: Implementation Plan

**Spec:** [design](../specs/2026-04-27-a1-portfolio-summary-strip-design.md)

## Step 1: PortfolioStrip component — stats only

**Files:** `frontend/src/features/trading/PortfolioStrip.tsx`

- Create `PortfolioStrip` component accepting `{ bots: BotSummary[], alignedRange?: { from: number; to: number } }`.
- Compute stats via `useMemo` from `bots`:
  - `totalPnl` — sum of `bot.total_pnl`
  - `totalAllocated` — sum of `bot.allocated_capital`
  - `pnlPct` — `totalAllocated > 0 ? totalPnl / totalAllocated * 100 : 0`
  - `runningCount` — bots with `status === 'running'`
  - `totalCount` — `bots.length`
  - `profitableCount` — bots with `total_pnl > 0`; `tradedCount` — bots with `trades_count > 0`. Displayed as `"Profitable: N / M bots"` (count ratio, not percentage — too coarse with 2–5 bots)
- Render two-column flex layout matching BotCard proportions: stats left, sparkline placeholder right.
- Style: same card look (dark bg, `#1e2530` border, 6px radius), neutral blue-ish background tint (`rgba(88, 166, 255, 0.05)`), "Portfolio" label top-left.
- Use `fmtUsd` and `fmtPnl` from `shared/utils/format` for dollar values.
- P&L colored with the same green/red as BotCard (`#26a69a` / `#ef5350`).

## Step 2: Combined equity sparkline

**Files:** `frontend/src/features/trading/PortfolioStrip.tsx`

- Add `mergedEquity` via `useMemo`:
  1. Filter `bots` to those with defined, non-empty `equity_snapshots` (guard for `undefined` — the field is optional on `BotSummary`).
  2. Collect `{ time: string, value: number, botId: string }` triples from the filtered bots.
  3. Sort by time (ISO string sort is fine since they're all ISO 8601 UTC).
  4. Walk sorted list, maintaining a `Map<string, number>` of `botId → lastValue`. At each point, update the map and record `{ time, value: sum(map.values()) }`.
  5. Post-walk dedup on the output array: if consecutive entries share the same timestamp, keep the last one (by that point both bot updates have been applied to the map, so the sum is correct).
- Pass `mergedEquity` as `equityData` to the existing `MiniSparkline` component.
- Pass `alignedRange` through to `MiniSparkline`.

## Step 3: Wire into BotControlCenter

**Files:** `frontend/src/features/trading/BotControlCenter.tsx`

- Import `PortfolioStrip`.
- Render `<PortfolioStrip bots={bots} alignedRange={alignedRange} />` between `<AddBotBar>` and the bot card list.
- Gate on visibility: only render when at least 1 bot has `equity_snapshots?.length > 0` (at least one trade closed).
- No new state or effects needed — `bots` and `alignedRange` already exist.

## Step 4: Verify visual alignment

- Start the dev server, create/use bots with existing equity data.
- Confirm the portfolio sparkline and bot sparklines align horizontally when in "Aligned" mode.
- Confirm stats update on the existing 5s poll cycle (they derive from `bots` which already polls).
- Confirm the strip hides when no bots have trades.
- Confirm "Local" mode lets each sparkline fit its own content independently.

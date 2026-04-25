# A8b — Chart Display Interval: Implementation Plan

**Spec:** [design](../specs/2026-04-25-a8b-chart-display-interval-design.md)

## Step 1: State & interval helpers

**Files:** `App.tsx`, `shared/types/index.ts`

- Add `viewInterval` state to `App.tsx`, initialized to `interval` (the backtest interval).
- Reset `viewInterval` to `interval` whenever `interval` changes (useEffect).
- Add `INTERVAL_ORDER` constant (ordered list: `1m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo`) and a helper `getCoarserIntervals(baseInterval)` that returns all intervals >= base. Put these in a new `shared/utils/intervals.ts`.

## Step 2: Wire view interval into data fetching

**Files:** `App.tsx`

- Change the chart's `useOHLCV` call to use `viewInterval` instead of `interval`.
- Change `useInstanceIndicators` to use `viewInterval`.
- Change SPY/QQQ `useOHLCV` calls to use `viewInterval`.
- The backtest (`StrategyBuilder.runBacktest`) continues to use `interval` — no change there.

## Step 3: "View as" dropdown on chart panel

**Files:** `App.tsx` (or a small `ViewIntervalSelector` component)

- Render a compact `<select>` in App.tsx's chart panel header area (where the existing chart toggle lives, ~line 96).
- Populate with `getCoarserIntervals(interval)`.
- Hidden when `interval` is `1d` or coarser (nothing coarser to show).
- Hidden when chart is disabled.
- Wire `onChange` to `setViewInterval`.

## Step 4: Trade marker snapping & aggregation

**Files:** `chartUtils.ts`, `Chart.tsx`

- Add `snapTimestamp(ts, viewInterval)` to `chartUtils.ts`:
  - Handles two time domains: unix timestamps (intraday, already ET-shifted via `toET()`) and `"YYYY-MM-DD"` date strings (daily+).
  - Intraday: modular floor on the ET-shifted timestamp (e.g. `ts - (ts % 3600)` for 1h). Must operate in the same ET domain as chart data — snapping raw UTC would misplace trades near bar boundaries.
  - Daily+: parse date string and floor to day/week/month boundary, return as date string.
- Add `aggregateMarkers(trades, candleTimeIndex, viewInterval, backtestInterval)` to `chartUtils.ts`:
  - If `viewInterval === backtestInterval`, return normal `buildMarkers()` output (no change).
  - Otherwise: snap each trade timestamp via `snapTimestamp`, group trades by snapped time, build summary markers (`"2T"`, `"3T"`) with color based on aggregate PnL.
- Update the `mainMarkers` and `subPaneMarkers` useMemo hooks in `Chart.tsx` to call `aggregateMarkers` when `viewInterval !== backtestInterval`. Pass both intervals as props.

## Step 5: Tooltip support for aggregated markers

**Files:** `Chart.tsx` (TradeTooltip integration)

- The existing `tradeLookup` in Chart.tsx maps bar indices to trades with a ±2 bar fuzzy match (`SNAP=2`). When `viewInterval !== backtestInterval`, disable fuzzy matching — use exact lookup on the snapped timestamp instead, since explicit snapping already handles alignment. This prevents double-shifting.
- Group all trades that snap to the same display bar under that bar's index.
- The existing `TradeTooltip` component already renders a list of trades — no changes needed there, it will naturally show all trades in the group.

## Step 6: EMA overlay gating

**Files:** `Chart.tsx`

- In the EMA overlay useEffect, add early return when `viewInterval !== backtestInterval`.
- Pass `viewInterval` and `backtestInterval` (the sidebar interval) as props to Chart.
- Show a small muted label below the "View as" dropdown: "EMA overlays hidden at this view" when overlays exist but are suppressed.

## Step 7: Update todo.md

- Add A8b to shipped section.
- Add real-time downsampling (#2 from earlier discussion) as a future A8c item.

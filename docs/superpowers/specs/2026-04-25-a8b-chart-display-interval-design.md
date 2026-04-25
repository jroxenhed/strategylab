# A8b — Chart Display Interval ("View As")

**Date:** 2026-04-25
**Status:** Draft
**Related:** A8 (chart performance), B17/B18 (trade marker tooltips)

## Problem

With 5-year 5-min data (100K+ bars), lightweight-charts hits a zoom-out ceiling — the user can't see the full date range without changing the backtest interval. But changing the interval changes the backtest itself, which isn't what the user wants.

## Solution

Decouple the chart display interval from the backtest interval. A small "View as" dropdown on the chart panel lets the user pick a coarser display resolution (e.g. 1h, 1D) while the backtest continues to run on the original 5-min data. Trade markers snap to the nearest display bar, aggregating when multiple trades fall within one bar.

## UI

- Small dropdown in the top-right corner of the chart panel, next to existing chart controls.
- Label: "View as" with a compact `<select>`.
- Options: all intervals coarser than or equal to the current backtest interval. If backtest is `5m`, options are `5m | 15m | 30m | 1h | 1d | 1wk`.
- Default: same as backtest interval (no behavior change for existing users).
- Only visible when chart is enabled.
- Hidden when backtest interval is `1d` or coarser (no coarser options to show).
- State stored in `App.tsx` alongside `chartEnabled`. Not persisted to localStorage — resets on page load to match backtest interval.

## Data Flow

- When view interval == backtest interval: current behavior, no changes.
- When view interval != backtest interval:
  - Chart OHLCV: the existing `useOHLCV` call switches to the view interval. No second fetch needed — the backtest fetches its own data server-side.
  - Indicators (`instanceData`): fetched at the view interval, since they display on the chart.
  - SPY/QQQ overlays: fetched at view interval (already gated on `chartEnabled && showSpy/showQqq`).
- The backtest request is unchanged — it always uses the sidebar interval.

## Trade Marker Snapping & Aggregation

Trade timestamps from the backtest are at the backtest interval (e.g. 5-min). When the chart displays a coarser interval, markers must snap to the enclosing display bar.

**Snapping:** Floor each trade timestamp to the nearest display bar boundary. For time-based intervals this is a simple modular floor (e.g. for 1h: `ts - (ts % 3600)`). For daily+, floor to date. Use the `candleTimeIndex` map (which is built from the display data) to find the matching bar.

**Aggregation:** When multiple trades snap to the same display bar:
- Show a single summary marker with text `"2T"`, `"3T"`, etc.
- Position: `aboveBar`.
- Color: green (`#26a641`) if aggregate PnL >= 0, red (`#f85149`) if negative.
- The existing B17/B18 hover tooltip expands to show all trades in that bar (entry/exit price, PnL, exit reason for each).

**Single trades:** Render as normal (B, S, SL, TSL, COV, etc.) — no change from current behavior.

**Implementation:** A new `snapAndAggregate(trades, candleTimeIndex, viewInterval)` utility in `chartUtils.ts`. Returns marker data ready for `createSeriesMarkers`. Called in the existing marker `useMemo` hooks, only when view interval differs from backtest interval.

## EMA Overlays

EMA overlays from the backtest are computed at backtest resolution (100K points for 5-min). They don't resample cleanly to coarser bars.

**Decision:** Skip EMA overlays when view interval != backtest interval. The user can switch back to native view to see them. A small note under the "View as" dropdown: "EMA overlays hidden at this view" when applicable.

## Sub-Pane Indicators (RSI, MACD, etc.)

Sub-panes follow the chart display interval automatically — they consume `instanceData` which is fetched at the view interval. No special handling needed.

## Scope Exclusions

- No synthetic intervals (3m, 10m, 2h, 4h) — only intervals the active data provider supports.
- No auto-selection of view interval based on data size (could add later).
- View interval not persisted across sessions.
- No changes to the backtest engine or backend.

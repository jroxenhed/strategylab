# Equity Curve Sync — Design Spec

## Goal

Sync the equity curve chart (in the Results bottom pane) with the main candlestick chart so that scroll, zoom, and crosshair stay aligned.

## Approach

Use a callback ref pattern (Option A): `Chart.tsx` fires `onChartReady(chart)` when its main chart is created. `App.tsx` stores the result in state and passes it to `Results.tsx`, which uses it to subscribe to sync events.

## Data Flow

```
App.tsx
  const [mainChart, setMainChart] = useState<IChartApi | null>(null)
       │                                        │
       ▼                                        ▼
  Chart.tsx                              Results.tsx
  onChartReady prop                      mainChart prop
  calls setMainChart(chart) on mount     subscribes when equity tab is active
  calls setMainChart(null) on unmount
```

## Sync Behavior

- **Scroll/zoom: bidirectional.** Scrolling or zooming either chart updates the other. Uses `subscribeVisibleLogicalRangeChange`, the same mechanism already used for MACD/RSI sync.
- **Crosshair: one-way (main → equity).** Hovering the main chart shows a vertical line at the same date on the equity curve. Reverse direction is omitted — it would require exposing the candle series ref out of Chart.tsx.
- **Initial alignment.** When switching to the Equity Curve tab, the chart opens at the main chart's current visible range instead of calling `fitContent()`.

## Files Changed

### `Chart.tsx`
Add one optional prop:
```ts
onChartReady?: (chart: IChartApi | null) => void
```
- Call `onChartReady(chart)` immediately after `createChart()` in the main chart effect.
- Call `onChartReady(null)` in the effect cleanup.
- No other changes to Chart.tsx.

### `App.tsx`
Add one state variable:
```ts
const [mainChart, setMainChart] = useState<IChartApi | null>(null)
```
- Pass `onChartReady={setMainChart}` to `<Chart>`.
- Pass `mainChart={mainChart}` to `<Results>`.

### `Results.tsx`
Add one optional prop:
```ts
mainChart?: IChartApi | null
```
In the equity chart `useEffect`:
1. After `createChart()`, apply the main chart's current visible range immediately via `mainChart.timeScale().getVisibleLogicalRange()`. If the result is `null`, fall back to `fitContent()`.
2. Subscribe: main chart range change → `equityChart.timeScale().setVisibleLogicalRange(range)`.
3. Subscribe: equity chart range change → `mainChart.timeScale().setVisibleLogicalRange(range)` (bidirectional).
4. Subscribe: main chart crosshair move → `equityChart.setCrosshairPosition(NaN, param.time, equitySeries)`. Clear on `param.time` absent.
5. Unsubscribe all four handlers in the effect cleanup.

Add `mainChart` to the effect dependency array so subscriptions are re-established if the chart is recreated (e.g. ticker change).

## Out of Scope

- Crosshair sync from equity → main (requires exposing candle series ref).
- Persisting equity curve scroll position to sessionStorage (main chart already handles this).

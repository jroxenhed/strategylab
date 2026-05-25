# B17: Hover-to-Inspect Trade Markers — Implementation Plan

[Spec](../specs/2026-04-23-b17-hover-trade-markers-design.md)

Two files changed: `frontend/src/features/chart/Chart.tsx` (markers + crosshair + state), new `frontend/src/features/chart/TradeTooltip.tsx` (tooltip rendering). No backend changes, no new dependencies.

## Step 1: Simplify `buildMarkers()` for main chart

Remove text labels from main-chart markers. Exit markers get outcome-based coloring.

**Current** (`Chart.tsx:62–89`):
- Entry markers: `text: 'B $511.79'` or `'SH $511.79'`
- Exit markers: `text: 'S $513.71 +0.88%'` or `'SL $513.71 +0.88%'`

**Change**:
- When `subPane` is false (main chart), set `text: ''` explicitly — empty string renders no label. If lw-charts v5 renders a small gap for empty string, use `' '` (single space) instead.
- Entry color stays `#e5c07b` (gold)
- Exit color stays outcome-based (`UP`/`DOWN` — already correct)
- SubPane path (`subPane = true`) unchanged — keeps short labels (`B`, `S`, `SH`, `COV`, `SL`, `TSL`)

The `showPrice` parameter becomes unused — replace it: `buildMarkers(trades: Trade[], subPane = false)`. When `!subPane`, text is always `''`. When `subPane`, text is the short label (existing behavior from the `!showPrice` branch).

## Step 2: Build trade lookup map + candle time index

New `useMemo` blocks after the existing `mainMarkers` memo (~line 166):

```typescript
// Map<normalized-time, Trade[]> for crosshair → tooltip lookup
const tradeLookup = useMemo(() => {
  if (!trades || trades.length === 0) return null
  const map = new Map<string | number, Trade[]>()
  for (const t of trades) {
    const key = toET(t.date as any)
    const arr = map.get(key)
    if (arr) arr.push(t)
    else map.set(key, [t])
  }
  return map
}, [trades])

// Map<time, barIndex> for O(1) hold-bar calculation in tooltip
const candleTimeIndex = useMemo(() => {
  const map = new Map<string | number, number>()
  for (let i = 0; i < candleData.length; i++) {
    map.set(candleData[i].time, i)
  }
  return map
}, [candleData])
```

### Daily-bar key normalization

lightweight-charts v5 can return `param.time` as either a `"YYYY-MM-DD"` string or a `BusinessDay` object `{year, month, day}` for daily series. The lookup map is keyed by the output of `toET()` which returns date strings unchanged. To handle the `BusinessDay` case, normalize `param.time` in the crosshair handler before the Map lookup:

```typescript
function normalizeTime(t: any): string | number {
  if (typeof t === 'object' && t !== null && 'year' in t)
    return `${t.year}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}`
  return t
}
```

This lives in Chart.tsx alongside `toET()`. Verify empirically with a daily-interval backtest which type lw-charts v5 actually returns — the normalizer handles both.

## Step 3: Add tooltip state + crosshair integration

Add state for the tooltip:

```typescript
const [tooltip, setTooltip] = useState<{
  x: number; y: number; trades: Trade[]
} | null>(null)
```

The `tradeLookup` must be accessed via ref inside the mount-once crosshair handler (stale closure). `setTooltip` does NOT need a ref — `useState` setters are referentially stable for the component lifetime.

```typescript
const tradeLookupRef = useRef(tradeLookup)
useEffect(() => { tradeLookupRef.current = tradeLookup }, [tradeLookup])
```

Extend the existing `crosshairHandler` (~line 238):

```typescript
const crosshairHandler = (param: any) => {
  try {
    if (!param.time) {
      for (const entry of paneRegistryRef.current.values()) entry.chart.clearCrosshairPosition()
      setTooltip(null)  // clear tooltip when mouse leaves chart
      return
    }
    // Show tooltip if trades exist on this bar
    const key = normalizeTime(param.time)
    const tradesOnBar = tradeLookupRef.current?.get(key)
    if (tradesOnBar && param.point) {
      setTooltip({ x: param.point.x, y: param.point.y, trades: tradesOnBar })
    } else {
      setTooltip(null)
    }
    for (const entry of paneRegistryRef.current.values()) {
      try { entry.chart.setCrosshairPosition(NaN, param.time, entry.series) } catch {}
    }
  } catch {}
}
```

Note: `param.point` guard is essential — synthetic crosshair moves from SubPane sync calls (`setCrosshairPosition`) fire the handler but `param.point` is undefined. The `&& param.point` check prevents tooltip from appearing on those events.

### Teardown guards

In the mount-once cleanup (alongside existing `chartRef.current = null`, `mainMarkersPluginRef.current = null`):

```typescript
return () => {
  // ... existing cleanup ...
  tradeLookupRef.current = null
  setTooltip(null)  // clear any visible tooltip before chart.remove()
  // ... chart.remove() ...
}
```

This follows the established pattern of nulling refs before `chart.remove()` to prevent the teardown race documented in CLAUDE.md Key Bugs Fixed.

## Step 4: Render tooltip

Inside the return JSX, wrap the chart container in a `position: relative` div and render the tooltip alongside. The wrapper div must NOT have `overflow: hidden` — the tooltip needs to overflow the chart bounds. The outer flex container keeps its existing `overflow: hidden`.

```tsx
return (
  <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%', overflow: 'hidden' }}>
    <div style={{ position: 'relative', flex: mainFlex, minHeight: 200, width: '100%' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      {tooltip && (
        <TradeTooltip
          x={tooltip.x} y={tooltip.y}
          trades={tooltip.trades}
          allTrades={trades!}
          candleTimeIndex={candleTimeIndex}
          toET={toET}
        />
      )}
    </div>
    {/* SubPanes unchanged */}
  </div>
)
```

The inner `containerRef` div gets `width: 100%; height: 100%` to fill the `position: relative` wrapper. The `ResizeObserver` in the mount-once effect observes `containerRef.current` — this works because the inner div inherits dimensions from the flex-sized wrapper.

## Step 5: `TradeTooltip` component — separate file

New file: `frontend/src/features/chart/TradeTooltip.tsx`

Extracted to its own file because:
- Chart.tsx already carries ~10 useEffect blocks, 8 useRef declarations, 6 useMemo calls
- TradeTooltip is purely presentational with zero chart-API coupling
- Keeps Chart.tsx additions to ~15 lines (ref, memo, state, crosshair extension, JSX)

**Props**:
```typescript
interface TradeTooltipProps {
  x: number
  y: number
  trades: Trade[]
  allTrades: Trade[]
  candleTimeIndex: Map<string | number, number>
  toET: (ts: any) => number
}
```

**Positioning**: Absolutely positioned within the `position: relative` wrapper. `left` = clamped `x` (stay within container). If tooltip would overflow right edge, shift left. `top` chosen based on `y` — above the cursor when near bottom, below when near top. `pointer-events: none` so it doesn't steal mouse events.

**Content rendering** — branch on trade type:
- Entry (`buy`/`short`): type label, price, shares, slippage
- Exit (`sell`/`cover`): type label, price, P&L + P&L%, hold bars, exit reason, slippage + commission + borrow cost
- Multiple trades on same bar separated by a `1px #30363d` divider

**Hold-bar calculation** — robust pairing by type, not index:

```typescript
function holdBars(
  exitTrade: Trade,
  allTrades: Trade[],
  candleTimeIndex: Map<string | number, number>,
  toET: (ts: any) => number,
): number | null {
  // Only compute for exit trades
  if (exitTrade.type === 'buy' || exitTrade.type === 'short') return null

  // Find the paired entry by scanning backward for matching entry type
  const exitIdx = allTrades.indexOf(exitTrade)
  if (exitIdx < 1) return null
  const entryType = exitTrade.type === 'sell' ? 'buy' : 'short'
  let entryTrade: Trade | null = null
  for (let i = exitIdx - 1; i >= 0; i--) {
    if (allTrades[i].type === entryType) {
      entryTrade = allTrades[i]
      break
    }
  }
  if (!entryTrade) return null

  const ei = candleTimeIndex.get(toET(entryTrade.date as any))
  const xi = candleTimeIndex.get(toET(exitTrade.date as any))
  if (ei === undefined || xi === undefined) return null
  return xi - ei
}
```

Key differences from the original plan:
- **Pairs by type, not index** — scans backward from exit to find matching `buy`/`short` entry. Handles open positions (entry with no exit at end of trades array) and any data anomalies.
- **O(1) candle index lookup** via `candleTimeIndex` Map instead of O(n) `findIndex` per call.
- **Guards entry trades** — returns null immediately for `buy`/`short` so hold-bars row is omitted from entry tooltips.

**Styling**: Dark card matching the app theme:
- Background: `#1c2128`, border: `1px solid #30363d`, border-radius: `6px`
- Padding: `8px 10px`, font-size: `11px`, color: `#e6edf3`
- P&L colored green/red (`#26a641`/`#f85149`), labels in `#8b949e`
- Max-width ~250px, entry/exit type labels in bold

## Verification

- Run backtest with many trades (15min, 1+ year range) — confirm arrows render without text clutter
- Hover over a bar with one trade — tooltip appears with correct data
- Hover over a bar with 2+ trades (same bar entry+exit) — stacked tooltip
- Move away from chart — tooltip disappears (tests `!param.time` clear path)
- Pan/zoom chart — no stale tooltip
- Check SubPane still shows labeled circle markers (unchanged)
- **Daily interval backtest** — confirm tooltip appears (tests BusinessDay normalization)
- **Open position** (backtest ending mid-trade) — confirm entry marker shows entry tooltip, no crash from holdBars
- Chart teardown (change ticker while hovering) — no console errors, tooltip clears
- Resize browser while tooltip visible — tooltip repositions or hides

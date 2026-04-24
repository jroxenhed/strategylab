# B17: Hover-to-Inspect Trade Markers

Replace verbose on-chart trade labels (`B $511.79`, `S $513.71 +0.88%`) with minimal colored arrows. Trade details appear in a floating tooltip on hover. Solves the readability problem when trades cluster in tight price ranges.

## Problem

Current markers show entry price + P&L text directly on the chart. With 200+ trades on a 15min timeframe, markers overlap and become illegible (see the $513–517 cluster in the motivating screenshot). Adding more info (hold duration, costs, exit reason) would make it worse.

## Design

### Persistent markers (always visible)

Each trade renders as an arrow — same shapes as today, but **no text label**:

| Trade type | Shape | Position | Color |
|---|---|---|---|
| Buy (long entry) | arrowUp | belowBar | `#e5c07b` (gold) |
| Short entry | arrowDown | aboveBar | `#e5c07b` (gold) |
| Sell (profitable) | arrowDown | aboveBar | `#26a641` (green) |
| Sell (losing) | arrowDown | aboveBar | `#f85149` (red) |
| Cover (profitable) | arrowUp | belowBar | `#26a641` (green) |
| Cover (losing) | arrowUp | belowBar | `#f85149` (red) |

Entry markers stay gold (direction-neutral) since you don't know the outcome yet at entry time. Exit markers are colored green/red by P&L — this gives at-a-glance win/loss scanning without any text.

SubPane markers stay as today: shape `circle`, position `inBar`, short labels (`B`, `S`, `SH`, `COV`), no tooltip (panes are too small).

### Hover tooltip

A `<div>` absolutely positioned relative to the chart container, shown when the crosshair lands on a bar that has trade(s). Hidden when the crosshair moves away.

**Detection**: `subscribeCrosshairMove` already fires on every mouse move. The callback receives `param.time`. We maintain a `Map<time, Trade[]>` lookup built from the trades array. When `param.time` matches a key, show the tooltip near `param.point` (pixel coordinates from the callback). When it doesn't match (or `param.time` is undefined), hide it.

**Positioning**: Tooltip placed at `param.point.x` (clamped to stay within chart bounds), anchored above or below the bar depending on available space. If the tooltip would overflow the right edge, shift it left. Standard CSS `pointer-events: none` so it doesn't interfere with chart interaction.

**Content** — differs by trade type:

Entry tooltip:
```
BUY  $511.7923          (or SHORT $511.7923)
19.4 shares
Slippage: $0.12
```

Exit tooltip:
```
SELL  $513.7172          (or COVER $513.7172)
P&L: +$3.84 (+0.88%)
Held: 14 bars
Exit: Signal              (or SL / TSL / Time Stop)
Slippage: $0.08  Commission: $0.07
```

When multiple trades land on the same bar (same `time` value), show all in one tooltip separated by a thin divider.

**Hold duration**: Computed client-side. The trades array alternates entry/exit. Pair them by index: entries at even indices `[0, 2, 4, ...]`, exits at odd indices `[1, 3, 5, ...]`. For each exit trade, count bars between the paired entry's time and the exit's time using the candlestick data array (indexOf lookup). This is a display-only computation — no backend changes needed.

### Exit reason label

The exit trade already carries `stop_loss: boolean` and `trailing_stop: boolean`. Derive the label:

| `stop_loss` | `trailing_stop` | Label |
|---|---|---|
| true | false | SL |
| false | true | TSL |
| false | false | Signal |

Time stop (`max_bars_held`) exits currently set neither flag — they'll show as "Signal" until the backend tags them distinctly (out of scope for B17; could add `time_stop: boolean` to the Trade type later).

## Implementation

### Frontend only — no backend changes

All data needed is already in the `Trade[]` returned by the backtest endpoint.

### Files to change

**`Chart.tsx`** — the only file with significant changes:

1. **`buildMarkers()`** — remove the `text` field from main-chart markers (keep subPane text as-is). Exit markers get color based on `(t.pnl ?? 0) >= 0`.

2. **Trade lookup map** — `useMemo` that builds `Map<number | string, Trade[]>` keyed by `toET(trade.date)`. Trades sharing a bar timestamp group together.

3. **Tooltip state** — `useState<{ x: number; y: number; trades: Trade[]; barIndex: number } | null>(null)` managed in the existing `crosshairHandler`. When `param.time` is in the lookup map and `param.point` exists, set tooltip state. When not, clear it.

4. **Tooltip component** — inline `<div>` rendered inside the chart container's parent, absolutely positioned. `pointer-events: none`, `z-index: 10`, dark background (`#1c2128`), border `#30363d`, `border-radius: 6px`, `font-size: 11px`. Renders each trade in the group.

5. **Hold duration helper** — small function: given entry time and exit time, find both indices in `candleData` and return the difference. Called at render time inside the tooltip, not precomputed.

**`shared/types/index.ts`** — no changes needed. Existing `Trade` interface has all required fields.

**`SubPane.tsx`** — no changes. SubPane markers keep their current short-label behavior.

### What NOT to do

- No new npm dependencies (no tooltip library — a positioned div is sufficient)
- No changes to the backtest endpoint or Trade model
- No custom lightweight-charts plugins beyond the existing `createSeriesMarkers`
- Don't add hover behavior to SubPane markers (too small, not worth the complexity)
- Don't precompute hold duration in the backend — it's trivially derived client-side

## Edge cases

- **No trades on bar**: tooltip hidden (default state)
- **Multiple trades on same bar**: single tooltip, stacked entries with dividers
- **Chart pan/zoom while tooltip visible**: crosshair moves → tooltip repositions or hides naturally
- **Chart teardown**: tooltip state lives in React state, garbage collected with the component
- **Daily bars**: time keys are date strings not numbers — the lookup map handles both since `Trade.date` can be `string | number`
- **Very first/last bar**: clamp tooltip position so it doesn't overflow the container

## Out of scope

- Triggering-rule details in tooltip (requires backend to tag which rules fired per trade — a signal trace join; good follow-up but adds backend scope)
- Click-to-scroll-to-trade-row in the Results table
- Tooltip on SubPane markers
- Backend `time_stop` flag on Trade

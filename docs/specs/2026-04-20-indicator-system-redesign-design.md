# Indicator System Redesign

Spec for rethinking chart indicator management to support customizable params, multiple instances, and forward-compatibility with B4 (per-rule signal visualization).

Related: [B4 per-rule signal markers](../../ideas/2026-04-18-b4-per-rule-signal-markers.md)

## Scope

**Chart redesign + B4 bridge** — redesign the chart indicator UI and data model now, explicitly designing the seam where B4's rule-driven indicators plug in later. The rule engine stays self-contained; no rule engine refactor.

## Current state

- Sidebar has a flat checkbox list: MACD, RSI, EMA(20/50/200), BB, Volume, plus SPY/QQQ toggles
- MA8/21 is a checkbox with expandable config (type, S-G settings) but hardcoded periods
- Backend `compute_indicators()` hardcodes all periods: RSI(14), EMA(20/50/200), MA(8/21), MACD(12,26,9), ATR(14)
- Rule engine references indicators by hardcoded name strings (`"rsi"`, `"ema20"`, `"ma8"`)
- Chart.tsx has ~10 separate `useEffect` blocks, each hardcoded to one indicator
- `IndicatorKey` is a union type: `'macd' | 'rsi' | 'ema' | 'bb' | 'ma' | 'volume'`

## Data model

### Indicator instance

Each indicator on the chart is an instance with an identity:

```typescript
type IndicatorType = 'rsi' | 'macd' | 'ema' | 'bb' | 'atr' | 'stochastic' | 'vwap' | 'volume' | 'ma'

type IndicatorInstance = {
  id: string              // unique, e.g. "rsi-1", "rsi-2", "ema-3"
  type: IndicatorType
  params: Record<string, number | string>  // type-specific
  enabled: boolean        // toggle on/off without removing
  color?: string          // user-chosen, fallback to type default
  pane: 'main' | 'sub'   // main chart overlay vs own sub-pane
}
```

### Indicator type definitions

Each type has a definition with defaults and param schema:

```typescript
type IndicatorTypeDef = {
  type: IndicatorType
  label: string
  defaultParams: Record<string, number | string>
  pane: 'main' | 'sub'
  paramFields: { key: string; label: string; min?: number; max?: number }[]
}
```

Type defaults:

| Type | Default params | Pane | Param fields |
|------|---------------|------|-------------|
| RSI | `{ period: 14 }` | sub | period |
| MACD | `{ fast: 12, slow: 26, signal: 9 }` | sub | fast, slow, signal |
| EMA | `{ period: 20 }` | main | period |
| BB | `{ period: 20, stddev: 2 }` | main | period, stddev |
| ATR | `{ period: 14 }` | sub | period |
| Stochastic | `{ k: 14, d: 3, smooth: 3 }` | sub | k, d, smooth |
| VWAP | `{}` | main | (none) |
| Volume | `{}` | main | (none) |
| MA | `{ period: 8, type: 'ema' }` | main | period, type (sma/ema/rma). S-G smoothing params deferred — carry forward existing S-G if needed but not required for v1. |

Multiple instances of the same type are allowed (e.g. RSI(14) + RSI(2)).

## Backend

### Indicator registry

Replace monolithic `compute_indicators()` with a registry of per-type compute functions:

```python
INDICATOR_REGISTRY = {
    "rsi": compute_rsi,       # (close, params) -> {"rsi": Series}
    "macd": compute_macd,     # (close, params) -> {"macd": Series, "signal": Series, "histogram": Series}
    "ema": compute_ema,       # (close, params) -> {"ema": Series}
    "bb": compute_bb,         # (close, params) -> {"upper": Series, "middle": Series, "lower": Series}
    "atr": compute_atr,       # (close, high, low, params) -> {"atr": Series}
    "ma": compute_ma,         # (close, params) -> {"ma": Series}
    "volume": compute_volume, # (volume, params) -> {"volume": Series}
}
```

### API

New endpoint — frontend sends the list of instances it needs:

```
POST /api/indicators/{symbol}
{
  "start": "2025-01-01",
  "end": "2025-12-31",
  "interval": "1d",
  "instances": [
    { "id": "rsi-1", "type": "rsi", "params": { "period": 14 } },
    { "id": "rsi-2", "type": "rsi", "params": { "period": 2 } },
    { "id": "ema-1", "type": "ema", "params": { "period": 20 } }
  ]
}
```

Response keyed by instance ID:

```json
{
  "rsi-1": { "rsi": [...] },
  "rsi-2": { "rsi": [...] },
  "ema-1": { "ema": [...] }
}
```

### Migration

- Current `GET /api/indicators/{symbol}` stays during transition
- New POST endpoint is additive
- Old GET removed once frontend switches over
- `compute_indicators()` in signal_engine.py stays unchanged — rule engine computes its own indicators based on rule params

## Frontend state

### App.tsx

Replace scattered state with a single array:

```typescript
// Before
const [activeIndicators, setActiveIndicators] = useState<IndicatorKey[]>(['macd', 'rsi'])
const [maSettings, setMaSettings] = useState<MASettings>({...})

// After
const [indicators, setIndicators] = useState<IndicatorInstance[]>([
  { id: 'macd-1', type: 'macd', params: { fast: 12, slow: 26, signal: 9 }, enabled: true, pane: 'sub' },
  { id: 'rsi-1', type: 'rsi', params: { period: 14 }, enabled: true, pane: 'sub' },
])
```

SPY/QQQ stay as separate state — they're comparison overlays, not indicators.

### Strategy save/load

The `indicators` array serializes into saved strategy JSON and restores on load. Replaces the current `activeIndicators` + `maSettings` fields. Needs migration logic: if a saved strategy has the old format, convert to `IndicatorInstance[]` on load.

## Sidebar UI

### Section layout

Collapsible sections, each remembering expanded/collapsed state:

- **Indicators** — active indicator list with "+ Add" button
- **Compare** — SPY/QQQ toggles (existing)
- (other existing sections unchanged)

### Indicator row

Each active indicator renders as a compact row:

```
[checkbox] RSI          14    [cog] [x]
```

- **Checkbox:** enable/disable without removing
- **Type label + param summary:** e.g. "RSI 14", "MACD 12,26,9", "EMA 20"
- **Cog:** expand/collapse inline settings panel
- **X:** remove from list

### Expanded settings

Clicking the cog expands a settings panel below the row (inline expand pattern, like current MA8/21 but cleaner):

- Param fields specific to the indicator type (period, fast/slow/signal, etc.)
- Color picker (small palette of preset colors)

### "+ Add" button

Simple dropdown menu listing available indicator types. Clicking one appends a new instance with defaults. No search or categories in v1.

## Chart.tsx integration

### Main overlay indicators

One generic effect replaces individual SPY/QQQ/EMA/BB/MA effects:

```typescript
useEffect(() => {
  const chart = chartRef.current
  if (!chart) return
  const created: { id: string; series: ISeriesApi<any>[] }[] = []

  for (const inst of indicators.filter(i => i.enabled && i.pane === 'main')) {
    const data = indicatorData[inst.id]
    if (!data) continue
    const series = renderMainOverlay(chart, inst, data)
    created.push({ id: inst.id, series })
  }

  return () => {
    for (const { series } of created) {
      for (const s of series) { try { chart.removeSeries(s) } catch {} }
    }
  }
}, [indicators, indicatorData])
```

`renderMainOverlay` switches on `inst.type` — EMA adds one line, BB adds three, etc.

### Sub-pane indicators

Generic `<SubPane>` component replaces dedicated MACD/RSI pane effects:

- Takes one or more `IndicatorInstance`s of the same type, plus their data
- Creates its own `IChartApi` instance
- Renders the appropriate series (RSI = one line per instance + 70/30 refs, MACD = histogram + two lines, etc.)
- Handles crosshair sync with the main chart
- Chart.tsx groups sub-pane indicators by type and renders one `<SubPane>` per group

**Same-type pane sharing:** Multiple instances of the same type can share a single sub-pane. E.g. RSI(14) + RSI(2) render as two lines (different colors) in one RSI pane with shared reference lines. This is implemented as a general capability, not per-indicator special-casing.

Each `IndicatorTypeDef` declares pane sharing behavior:

```typescript
type IndicatorTypeDef = {
  // ... existing fields ...
  subPaneSharing: 'shared' | 'isolated'
  // shared: multiple instances render in one pane (RSI, Stochastic, ATR)
  // isolated: each instance gets its own pane (MACD — histogram doesn't stack well)
}
```

The `<SubPane>` component is built to accept an array of instances regardless. The `subPaneSharing` flag only controls the default grouping in Chart.tsx — `'shared'` types are grouped by type into one pane, `'isolated'` types each get their own.

Initial classification:

| Type | Sharing | Rationale |
|------|---------|-----------|
| RSI | shared | Multiple lengths compare naturally on 0-100 scale |
| Stochastic | shared | Same 0-100 scale as RSI |
| ATR | shared | Multiple periods compare naturally |
| MACD | isolated | Histogram + two lines don't stack well with a second instance |

This is a per-type default, easy to change later as we learn which indicators benefit from sharing.

**Separate pane override:** For advanced use cases (MTC, or visual separation), a user could override the default and split an instance into its own pane. Deferred to future work.

### Default height split

| Active sub-panes | Main | Each sub |
|---|---|---|
| 0 | 100% | — |
| 1 | 65% | 35% |
| 2 | 50% | 25% each |
| 3 | 45% | ~18% each |
| 4+ | 40% | split remainder |

### Pane synchronization

Same patterns as today (documented in CLAUDE.md):
- Pan/zoom via `subscribeVisibleLogicalRangeChange` on main → `setVisibleLogicalRange` on sub-panes
- Crosshair sync via `subscribeCrosshairMove` → `setCrosshairPosition`
- Price scale alignment via `syncWidths()`
- Whitespace entries for warmup bars
- Try/catch guards on all cleanup paths

## B4 bridge seam

Rules stay self-contained — a rule carries its own indicator params. The bridge is a pure matching function:

```typescript
function findMatchingIndicator(
  rule: Rule,
  indicators: IndicatorInstance[]
): IndicatorInstance | null {
  return indicators.find(i =>
    i.type === rule.indicator &&
    matchParams(i.params, rule)
  ) ?? null
}
```

What this enables for B4 later:
- **Match exists:** Rule's indicator is already on the chart → B4 draws signal markers on that indicator's pane. "Rendered" classification from B4 doc — default markers OFF.
- **No match:** Rule references an indicator not on the chart → "hidden" classification. B4 can offer "show on chart" to auto-add the indicator.
- **Trade attribution:** Buy/sell markers can reference instance IDs, linking "this trade fired because of rsi-2" to the visible RSI(2) line.

What we build now: nothing — just the data model shape that makes matching possible. `IndicatorInstance.id` and `type + params` are sufficient. Both `indicators[]` and `rules[]` live in the same strategy save blob, which is the seam.

## Collapsible chart section

The entire chart area (main chart + all sub-panes) is collapsible. When collapsed:

- All `IChartApi` instances are destroyed (no rendering cost)
- Indicator data fetching is skipped (no API calls for indicator data)
- The section collapses to a thin header bar with a toggle to re-expand
- State (which indicators are active, their params) is preserved — only rendering is disabled
- Candle data fetching continues so the strategy builder and backtester still work

This follows the same collapsible pattern as sidebar sections. Collapsed state persists across sessions via the existing session save mechanism.

## Future work (not in this spec)

- Resizable, collapsible, double-click-to-maximize chart panes
- Drag-to-reorder indicator list
- Searchable / categorized "+ Add" dropdown
- MA S-G smoothing options (carry forward from current MA8/21 if needed)
- VWAP, Stochastic implementations
- B4 implementation using the bridge seam

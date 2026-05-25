# Chart Page Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add date range presets with period stepping to the sidebar, and add normalised B&H comparison + log scale toggles to the equity curve.

**Architecture:** All changes are frontend-only. Date presets add a new `datePreset` state in App.tsx that drives the sidebar UI; stepping computes new start/end dates. Equity curve changes add data transformation functions (normalise, log) applied before passing data to lightweight-charts, plus two toggle buttons in the Results tab bar.

**Tech Stack:** React, TypeScript, lightweight-charts v5

**Note:** The spec mentions a custom crosshair tooltip showing both strategy and B&H values with % and dollar. The default lightweight-charts tooltip only shows one series at a time. A custom tooltip (like MacroEquityChart's manual tooltip) can be added as a follow-up if the default proves insufficient.

---

### Task 1: Add `DatePreset` type

**Files:**
- Modify: `frontend/src/shared/types/index.ts:247` (after `DataSource` type)

- [ ] **Step 1: Add the type**

In `frontend/src/shared/types/index.ts`, after the `DataSource` type on line 247, add:

```typescript
export type DatePreset = 'D' | 'W' | 'M' | 'Q' | 'Y' | 'custom'
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/shared/types/index.ts
git commit -m "feat: add DatePreset type"
```

---

### Task 2: Add `datePreset` state to App.tsx

**Files:**
- Modify: `frontend/src/App.tsx:1` (import), `frontend/src/App.tsx:51-58` (state), `frontend/src/App.tsx:69-72` (persistence), `frontend/src/App.tsx:133-152` (Sidebar props)

- [ ] **Step 1: Import `DatePreset`**

In `frontend/src/App.tsx` line 3, add `DatePreset` to the import:

```typescript
import type { BacktestResult, IndicatorKey, DataSource, MAType, StrategyRequest, DatePreset } from './shared/types'
```

- [ ] **Step 2: Add state**

After `const [maSettings, setMaSettings]` (line 66), add:

```typescript
const [datePreset, setDatePreset] = useState<DatePreset>((saved?.datePreset as DatePreset) ?? 'Y')
```

- [ ] **Step 3: Persist to localStorage**

In the `useEffect` that calls `localStorage.setItem` (line 69), add `datePreset` to the object:

```typescript
localStorage.setItem(STORAGE_KEY, JSON.stringify({
  ticker, start, end, interval, activeIndicators, showSpy, showQqq, dataSource, maSettings, datePreset,
}))
```

And add `datePreset` to the dependency array.

- [ ] **Step 4: Pass props to Sidebar**

In the `<Sidebar>` JSX (around line 133), add two new props:

```tsx
<Sidebar
  // ... existing props ...
  datePreset={datePreset}
  onDatePresetChange={setDatePreset}
/>
```

- [ ] **Step 5: Verify build**

Run: `cd frontend && npx tsc --noEmit`
Expected: type error in Sidebar (props not yet accepted) — that's fine, confirms App is wired up.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: add datePreset state and persistence to App"
```

---

### Task 3: Implement date range presets + stepping in Sidebar

**Files:**
- Modify: `frontend/src/features/sidebar/Sidebar.tsx`

This is the largest task. The sidebar Date Range section gets a complete rework: preset buttons, arrow stepping, collapsible From/To inputs.

- [ ] **Step 1: Add imports and types to Sidebar**

At the top of `Sidebar.tsx`, add `DatePreset` to the types import (or import it):

```typescript
import type { IndicatorKey, DataSource, MAType, DatePreset } from '../../shared/types'
```

Remove the `MAType` from the `App` import if it was there — it should come from types. Keep importing `MASettings` from `App`.

- [ ] **Step 2: Add new props to the interface**

Add to `SidebarProps`:

```typescript
datePreset: DatePreset
onDatePresetChange: (preset: DatePreset) => void
```

And destructure them in the component function signature.

- [ ] **Step 3: Add preset computation helper**

Before the `Sidebar` component (or inside it), add a helper function that computes start from end + preset:

```typescript
function computePresetStart(end: string, preset: DatePreset): string {
  const endDate = new Date(end + 'T00:00:00')
  let startDate: Date
  switch (preset) {
    case 'D':
      startDate = new Date(endDate)
      startDate.setDate(startDate.getDate() - 1)
      break
    case 'W':
      startDate = new Date(endDate)
      startDate.setDate(startDate.getDate() - 7)
      break
    case 'M':
      startDate = new Date(endDate)
      startDate.setMonth(startDate.getMonth() - 1)
      break
    case 'Q':
      startDate = new Date(endDate)
      startDate.setMonth(startDate.getMonth() - 3)
      break
    case 'Y':
      startDate = new Date(endDate)
      startDate.setFullYear(startDate.getFullYear() - 1)
      break
    default:
      return end // custom — no computation
  }
  return startDate.toISOString().slice(0, 10)
}
```

- [ ] **Step 4: Add stepping helper**

Add a function that shifts start/end by the window duration:

```typescript
function stepRange(
  start: string, end: string, preset: DatePreset, direction: 1 | -1
): { start: string; end: string } {
  const s = new Date(start + 'T00:00:00')
  const e = new Date(end + 'T00:00:00')

  if (preset === 'custom' || preset === 'D') {
    // For custom, shift by the range's duration in days; for D, shift by 1 day
    const days = preset === 'D' ? 1 : Math.round((e.getTime() - s.getTime()) / (1000 * 60 * 60 * 24))
    s.setDate(s.getDate() + days * direction)
    e.setDate(e.getDate() + days * direction)
  } else if (preset === 'W') {
    s.setDate(s.getDate() + 7 * direction)
    e.setDate(e.getDate() + 7 * direction)
  } else if (preset === 'M') {
    s.setMonth(s.getMonth() + 1 * direction)
    e.setMonth(e.getMonth() + 1 * direction)
  } else if (preset === 'Q') {
    s.setMonth(s.getMonth() + 3 * direction)
    e.setMonth(e.getMonth() + 3 * direction)
  } else if (preset === 'Y') {
    s.setFullYear(s.getFullYear() + 1 * direction)
    e.setFullYear(e.getFullYear() + 1 * direction)
  }

  return {
    start: s.toISOString().slice(0, 10),
    end: e.toISOString().slice(0, 10),
  }
}
```

- [ ] **Step 5: Add preset selection handler**

Inside the component, add a handler for selecting a preset:

```typescript
const handlePresetChange = (preset: DatePreset) => {
  onDatePresetChange(preset)
  if (preset !== 'custom') {
    const newStart = computePresetStart(end, preset)
    onStartChange(newStart)
  }
}
```

- [ ] **Step 6: Add step handler (single and multi)**

```typescript
const handleStep = (direction: 1 | -1, multiplier: number = 1) => {
  let newStart = start
  let newEnd = end
  for (let i = 0; i < multiplier; i++) {
    const stepped = stepRange(newStart, newEnd, datePreset, direction)
    newStart = stepped.start
    newEnd = stepped.end
  }
  onStartChange(newStart)
  onEndChange(newEnd)
}

const today = new Date().toISOString().slice(0, 10)
const forwardDisabled = end >= today
```

- [ ] **Step 7: Replace the Date Range section JSX**

Replace the entire `<div style={styles.section}>` block for "Date Range" (lines 178–216) with:

```tsx
<div style={styles.section}>
  <div style={styles.sectionTitle}>Date Range</div>

  {/* Preset row with arrows */}
  <div style={{ display: 'flex', alignItems: 'center', gap: 2, marginBottom: 12 }}>
    <button
      onClick={() => handleStep(-1, 5)}
      style={styles.arrowBtn}
      title="Back 5 periods"
    >
      «
    </button>
    <button
      onClick={() => handleStep(-1)}
      style={styles.arrowBtn}
      title="Previous period"
    >
      ‹
    </button>
    <div style={{ display: 'flex', flex: 1, gap: 2, background: 'var(--bg-input)', borderRadius: 'var(--radius-sm)', padding: 2 }}>
      {(['D', 'W', 'M', 'Q', 'Y', 'custom'] as DatePreset[]).map(p => (
        <button
          key={p}
          onClick={() => handlePresetChange(p)}
          style={{
            flex: 1,
            padding: '5px 0',
            fontSize: 11,
            fontWeight: 600,
            border: 'none',
            borderRadius: 3,
            cursor: 'pointer',
            background: datePreset === p ? 'var(--bg-panel-hover)' : 'transparent',
            color: datePreset === p ? 'var(--text-primary)' : 'var(--text-muted)',
            transition: 'all 0.15s',
          }}
        >
          {p === 'custom' ? '⚙' : p}
        </button>
      ))}
    </div>
    <button
      onClick={() => handleStep(1)}
      disabled={forwardDisabled}
      style={{ ...styles.arrowBtn, opacity: forwardDisabled ? 0.3 : 1, cursor: forwardDisabled ? 'not-allowed' : 'pointer' }}
      title="Next period"
    >
      ›
    </button>
    <button
      onClick={() => handleStep(1, 5)}
      disabled={forwardDisabled}
      style={{ ...styles.arrowBtn, opacity: forwardDisabled ? 0.3 : 1, cursor: forwardDisabled ? 'not-allowed' : 'pointer' }}
      title="Forward 5 periods"
    >
      »
    </button>
  </div>

  {/* Custom From/To — only visible when custom preset */}
  {datePreset === 'custom' && (
    <>
      <div style={styles.field}>
        <label style={styles.label}>From</label>
        <input
          type="date" value={localStart} style={styles.dateInput}
          onChange={e => setLocalStart(e.target.value)}
          onBlur={e => onStartChange(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && onStartChange((e.target as HTMLInputElement).value)}
        />
      </div>
      <div style={styles.field}>
        <label style={styles.label}>To</label>
        <input
          type="date" value={localEnd} style={styles.dateInput}
          onChange={e => setLocalEnd(e.target.value)}
          onBlur={e => onEndChange(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && onEndChange((e.target as HTMLInputElement).value)}
        />
      </div>
    </>
  )}

  {/* Interval — always visible */}
  <div style={styles.field}>
    <label style={styles.label}>Interval</label>
    <select value={interval} onChange={e => onIntervalChange(e.target.value)} style={styles.dateInput}>
      <option value="1m">1 min</option>
      <option value="5m">5 min</option>
      <option value="15m">15 min</option>
      <option value="30m">30 min</option>
      <option value="1h">1 Hour</option>
      <option value="1d">Daily</option>
      <option value="1wk">Weekly</option>
      <option value="1mo">Monthly</option>
    </select>
    {showIntervalWarning && (
      <div style={{ fontSize: 11, color: 'var(--accent-orange)', marginTop: 8, lineHeight: 1.4 }}>
        {interval} data only supports {intervalLimit} days of history. Your range is {daysDiff} days — shorten the From date.
      </div>
    )}
  </div>
</div>
```

- [ ] **Step 8: Add `arrowBtn` style**

Add to the `styles` object at the bottom of the file:

```typescript
arrowBtn: {
  width: 28, height: 28,
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  fontSize: 14, fontWeight: 700,
  background: 'var(--bg-input)', border: '1px solid var(--border-light)',
  borderRadius: 'var(--radius-sm)', color: 'var(--text-secondary)',
  cursor: 'pointer', flexShrink: 0,
},
```

- [ ] **Step 9: Verify build**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS — no type errors.

- [ ] **Step 10: Manual test**

Start the app (`./start.sh`), open `http://localhost:5173`:
1. Click each preset (D/W/M/Q/Y) — verify date range changes in header
2. Click `‹` / `›` single arrows — verify window shifts by one period
3. Click `«` / `»` double arrows — verify window jumps 5 periods
4. Click Custom (⚙) — verify From/To inputs appear
5. Verify `›` and `»` are disabled when end = today
6. Verify stepping works in custom mode (shifts by range duration)

- [ ] **Step 11: Commit**

```bash
git add frontend/src/features/sidebar/Sidebar.tsx
git commit -m "feat: date range presets with period stepping"
```

---

### Task 4: Add B&H and Log toggle buttons to Results tab bar

**Files:**
- Modify: `frontend/src/features/strategy/Results.tsx:36-37` (state), `frontend/src/features/strategy/Results.tsx:179-205` (tab bar JSX)

This task adds the toggle buttons to the UI. The next tasks wire up the data transformation.

- [ ] **Step 1: Add `logScale` state**

In the `Results` component, after `const [showBaseline, setShowBaseline] = useState(false)` (line 36), add:

```typescript
const [logScale, setLogScale] = useState(false)
```

- [ ] **Step 2: Replace the existing B&H checkbox and add toggle buttons**

Remove the existing checkbox label block (lines 276–283):

```tsx
<label style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', fontSize: 11, color: '#8b949e', cursor: 'pointer' }}>
  <input
    type="checkbox"
    checked={showBaseline}
    onChange={e => setShowBaseline(e.target.checked)}
  />
  Show buy &amp; hold baseline
</label>
```

Add two toggle buttons in the tab bar area. Inside the `<div style={styles.tabBar}>`, after the bucket buttons `<div>` (the one with `marginLeft: 'auto'`), add a new row below. Replace the entire `tabBar` div (lines 167–206) with:

```tsx
<div style={{ ...styles.tabBar, flexWrap: 'wrap' }}>
  <div style={{ display: 'flex' }}>
    {(['summary', 'equity', 'trades', ...(signal_trace ? ['trace'] : [])] as ResultsTab[]).map(tab => (
      <button
        key={tab}
        onClick={() => onTabChange(tab)}
        style={{ ...styles.tab, ...(activeTab === tab ? styles.tabActive : {}) }}
      >
        {tab === 'summary' ? 'Summary' : tab === 'equity' ? 'Equity Curve' : tab === 'trades' ? `Trades (${sells.length})` : `Signal Trace (${signal_trace!.length})`}
      </button>
    ))}
  </div>
  <div style={{ display: 'flex', marginLeft: 'auto', gap: 2, alignItems: 'center' }}>
    {(['Detail', 'D', 'W', 'M', 'Q', 'Y'] as const).map(b => {
      const isDetail = b === 'Detail'
      const isActive = isDetail ? bucket === null : bucket === b
      const isRecommended = !isDetail && bucket === null && b === autoDefaultBucket(equity_curve.length)
      return (
        <button
          key={b}
          onClick={() => onBucketChange(isDetail ? null : b)}
          style={{
            padding: '4px 8px',
            fontSize: 11,
            fontWeight: 600,
            color: isActive ? '#58a6ff' : isRecommended ? '#58a6ff' : '#8b949e',
            background: isActive ? 'rgba(88, 166, 255, 0.1)' : 'none',
            border: 'none',
            borderBottom: isRecommended && !isActive ? '2px solid rgba(88, 166, 255, 0.3)' : '2px solid transparent',
            borderRadius: 3,
            cursor: 'pointer',
          }}
        >
          {b}
          {!isDetail && macroLoading && bucket === b && ' ...'}
        </button>
      )
    })}
    {activeTab === 'equity' && (
      <>
        <div style={{ width: 1, height: 16, background: '#30363d', margin: '0 6px' }} />
        <button
          onClick={() => setShowBaseline(v => !v)}
          style={{
            padding: '4px 8px',
            fontSize: 11,
            fontWeight: 600,
            color: showBaseline ? '#58a6ff' : '#8b949e',
            background: showBaseline ? 'rgba(88, 166, 255, 0.1)' : 'none',
            border: 'none',
            borderRadius: 3,
            cursor: 'pointer',
          }}
        >
          B&amp;H
        </button>
        <button
          onClick={() => setLogScale(v => !v)}
          style={{
            padding: '4px 8px',
            fontSize: 11,
            fontWeight: 600,
            color: logScale ? '#58a6ff' : '#8b949e',
            background: logScale ? 'rgba(88, 166, 255, 0.1)' : 'none',
            border: 'none',
            borderRadius: 3,
            cursor: 'pointer',
          }}
        >
          Log
        </button>
      </>
    )}
  </div>
</div>
```

- [ ] **Step 3: Verify build**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/strategy/Results.tsx
git commit -m "feat: add B&H and Log toggle buttons to equity tab bar"
```

---

### Task 5: Implement normalised equity curve (B&H on)

**Files:**
- Modify: `frontend/src/features/strategy/Results.tsx:42-163` (the Detail equity chart `useEffect`)

- [ ] **Step 1: Add normalisation helper**

Above the `Results` component, add:

```typescript
function normaliseToPercent(data: { time: any; value: number }[]): { time: any; value: number; dollar: number }[] {
  if (data.length === 0) return []
  const first = data[0].value
  if (first === 0) return data.map(d => ({ time: d.time, value: 0, dollar: d.value }))
  return data.map(d => ({
    time: d.time,
    value: ((d.value - first) / first) * 100,
    dollar: d.value,
  }))
}
```

- [ ] **Step 2: Add log transform helper**

```typescript
function applyLog(data: { time: any; value: number; dollar?: number }[], isNormalised: boolean): { time: any; value: number; dollar?: number }[] {
  return data.map(d => ({
    ...d,
    value: isNormalised
      ? Math.log10(Math.max(100 + d.value, 0.01))  // offset pct by 100 to avoid log(negative)
      : Math.log10(Math.max(d.value, 0.01)),
  }))
}
```

- [ ] **Step 3: Update the Detail equity chart useEffect**

In the `useEffect` that creates the Detail equity chart (starting around line 42), replace the data preparation and series setup. The key changes are:

1. Filter the raw data, then conditionally normalise and log-transform it
2. Change `baseValue` based on normalisation mode
3. Add `priceFormat` for percentage/log display
4. Handle baseline curve with the same transforms

Replace the body of the useEffect (the part from chart creation through series setup, lines 43–80) with:

```typescript
const chart = createChart(chartRef.current, {
  height: chartRef.current.clientHeight || 185,
  layout: { background: { type: ColorType.Solid, color: '#0d1117' }, textColor: '#8b949e' },
  grid: { vertLines: { color: '#1c2128' }, horzLines: { color: '#1c2128' } },
  timeScale: { borderColor: '#30363d' },
  rightPriceScale: { borderColor: '#30363d' },
})

// Prepare equity data
const rawEquity = equity_curve
  .filter(d => d.value !== null)
  .map(d => ({ time: d.time as any, value: d.value as number }))

let equityData: { time: any; value: number; dollar?: number }[]
let baselineData: { time: any; value: number; dollar?: number }[] | null = null
let baseValue: number

if (showBaseline) {
  // Normalise to percentage
  equityData = normaliseToPercent(rawEquity)
  baseValue = 0

  if (result.baseline_curve && result.baseline_curve.length > 0) {
    const rawBaseline = result.baseline_curve
      .filter(d => d.value !== null)
      .map(d => ({ time: d.time as any, value: d.value as number }))
    baselineData = normaliseToPercent(rawBaseline)
  }
} else {
  equityData = rawEquity
  baseValue = equity_curve.length > 0 && equity_curve[0].value !== null ? equity_curve[0].value : 10000
}

if (logScale) {
  equityData = applyLog(equityData, showBaseline)
  if (baselineData) baselineData = applyLog(baselineData, showBaseline)
  baseValue = showBaseline ? Math.log10(100) : Math.log10(Math.max(baseValue, 0.01))
}

const priceFormat = logScale
  ? {
      type: 'custom' as const,
      formatter: (price: number) => {
        if (showBaseline) {
          const pct = Math.pow(10, price) - 100
          return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
        }
        return `$${Math.pow(10, price).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
      },
    }
  : showBaseline
    ? {
        type: 'custom' as const,
        formatter: (price: number) => `${price >= 0 ? '+' : ''}${price.toFixed(1)}%`,
      }
    : undefined

const series = chart.addSeries(BaselineSeries, {
  baseValue: { type: 'price', price: baseValue },
  topLineColor: '#26a641',
  bottomLineColor: '#f85149',
  topFillColor1: 'rgba(38, 166, 65, 0.1)',
  topFillColor2: 'rgba(38, 166, 65, 0)',
  bottomFillColor1: 'rgba(248, 81, 73, 0)',
  bottomFillColor2: 'rgba(248, 81, 73, 0.1)',
  lineWidth: 2,
  ...(priceFormat ? { priceFormat } : {}),
})
series.setData(equityData.map(d => ({ time: d.time, value: d.value })))

if (showBaseline && baselineData) {
  const baselineSeries = chart.addSeries(LineSeries, {
    color: '#8b949e',
    lineWidth: 1,
    lineStyle: 2,
    priceLineVisible: false,
    lastValueVisible: false,
    ...(priceFormat ? { priceFormat } : {}),
  })
  baselineSeries.setData(baselineData.map(d => ({ time: d.time, value: d.value })))
}
```

- [ ] **Step 4: Update useEffect dependency array**

Add `logScale` to the dependency array (it already has `showBaseline`):

```typescript
}, [activeTab, bucket, equity_curve, summary.total_return_pct, mainChart, showBaseline, result.baseline_curve, logScale])
```

- [ ] **Step 5: Remove the old checkbox from the equity tab JSX**

The equity tab rendering block (around line 268) currently wraps the chart in a div with the checkbox label. Simplify it — remove the label entirely (moved to tab bar in Task 4). Replace:

```tsx
{activeTab === 'equity' && (
  bucket && macroData ? (
    <MacroEquityChart ... />
  ) : (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      <label style={{ ... }}>...</label>
      <div ref={chartRef} style={{ ... }} />
    </div>
  )
)}
```

With:

```tsx
{activeTab === 'equity' && (
  bucket && macroData ? (
    <MacroEquityChart
      macroCurve={macroData.macro_curve}
      initialCapital={summary.initial_capital}
      showBaseline={showBaseline}
      logScale={logScale}
      baselineCurve={result.baseline_curve}
    />
  ) : (
    <div ref={chartRef} style={{ width: '100%', height: 250, minHeight: 100, maxHeight: 600, resize: 'vertical', overflow: 'hidden' }} />
  )
)}
```

- [ ] **Step 6: Verify build**

Run: `cd frontend && npx tsc --noEmit`
Expected: type error in MacroEquityChart (new props not yet accepted) — expected, will fix in Task 6.

- [ ] **Step 7: Manual test (Detail mode)**

Start the app, run a backtest, go to Equity Curve tab:
1. Toggle B&H — curve should normalise to percentage, baseline appears
2. Toggle Log — y-axis should show log-scaled labels
3. Toggle both — combined view
4. Toggle both off — back to original dollar view

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/strategy/Results.tsx
git commit -m "feat: normalised B&H comparison and log scale for equity curve"
```

---

### Task 6: Add B&H and Log support to MacroEquityChart

**Files:**
- Modify: `frontend/src/features/strategy/MacroEquityChart.tsx`

- [ ] **Step 1: Update Props interface**

Replace the Props interface:

```typescript
interface Props {
  macroCurve: MacroCurvePoint[]
  initialCapital: number
  showBaseline: boolean
  logScale: boolean
  baselineCurve?: { time: string | number; value: number | null }[]
}
```

Update the component signature:

```typescript
export default function MacroEquityChart({ macroCurve, initialCapital, showBaseline, logScale, baselineCurve }: Props) {
```

- [ ] **Step 2: Import `LineSeries` (already imported) and add transform helpers**

The same `normaliseToPercent` and `applyLog` helpers from Results.tsx are needed. To avoid duplication, add them as local functions in this file too (they're small, 5 lines each — extracting to a shared util is premature for two consumers):

```typescript
function normaliseToPercent(data: { time: any; value: number }[]): { time: any; value: number; dollar: number }[] {
  if (data.length === 0) return []
  const first = data[0].value
  if (first === 0) return data.map(d => ({ time: d.time, value: 0, dollar: d.value }))
  return data.map(d => ({
    time: d.time,
    value: ((d.value - first) / first) * 100,
    dollar: d.value,
  }))
}

function applyLog(data: { time: any; value: number; dollar?: number }[], isNormalised: boolean): { time: any; value: number; dollar?: number }[] {
  return data.map(d => ({
    ...d,
    value: isNormalised
      ? Math.log10(Math.max(100 + d.value, 0.01))
      : Math.log10(Math.max(d.value, 0.01)),
  }))
}
```

- [ ] **Step 3: Update the chart useEffect — transform close data and add baseline**

Inside the `useEffect`, after chart creation but before the close series setup, add the data transformation. Replace the close series block (lines 27–40) with:

```typescript
// Prepare close data
let closeData: { time: any; value: number; dollar?: number }[] = macroCurve.map(b => ({ time: b.time as any, value: b.close }))
let baseValue = initialCapital

let macroBaselineData: { time: any; value: number; dollar?: number }[] | null = null

if (showBaseline) {
  closeData = normaliseToPercent(closeData as { time: any; value: number }[])
  baseValue = 0

  if (baselineCurve && baselineCurve.length > 0) {
    // Resample baseline to macro bucket boundaries by picking the value at each macro time
    const baselineMap = new Map(
      baselineCurve
        .filter(d => d.value !== null)
        .map(d => [String(d.time), d.value as number])
    )
    // For macro, use the first bar's time to find initial baseline value, then use last available
    const macroBaselineRaw: { time: any; value: number }[] = []
    let lastBaselineValue = initialCapital
    for (const b of macroCurve) {
      const v = baselineMap.get(b.time) ?? lastBaselineValue
      lastBaselineValue = v
      macroBaselineRaw.push({ time: b.time as any, value: v })
    }
    macroBaselineData = normaliseToPercent(macroBaselineRaw)
  }
}

if (logScale) {
  closeData = applyLog(closeData, showBaseline)
  if (macroBaselineData) macroBaselineData = applyLog(macroBaselineData, showBaseline)
  baseValue = showBaseline ? Math.log10(100) : Math.log10(Math.max(baseValue, 0.01))
}

const priceFormat = logScale
  ? {
      type: 'custom' as const,
      formatter: (price: number) => {
        if (showBaseline) {
          const pct = Math.pow(10, price) - 100
          return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
        }
        return `$${Math.pow(10, price).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
      },
    }
  : showBaseline
    ? {
        type: 'custom' as const,
        formatter: (price: number) => `${price >= 0 ? '+' : ''}${price.toFixed(1)}%`,
      }
    : undefined

// 1. Close line — BaselineSeries
const closeSeries = chart.addSeries(BaselineSeries, {
  baseValue: { type: 'price', price: baseValue },
  topLineColor: '#26a641',
  bottomLineColor: '#f85149',
  topFillColor1: 'rgba(38, 166, 65, 0.1)',
  topFillColor2: 'rgba(38, 166, 65, 0)',
  bottomFillColor1: 'rgba(248, 81, 73, 0)',
  bottomFillColor2: 'rgba(248, 81, 73, 0.1)',
  lineWidth: 2,
  priceScaleId: 'right',
  ...(priceFormat ? { priceFormat } : {}),
})
closeSeries.setData(closeData.map(d => ({ time: d.time, value: d.value })))

// Baseline line (only when B&H is on)
if (showBaseline && macroBaselineData) {
  const baselineSeries = chart.addSeries(LineSeries, {
    color: '#8b949e',
    lineWidth: 1,
    lineStyle: 2,
    priceLineVisible: false,
    lastValueVisible: false,
    priceScaleId: 'right',
    ...(priceFormat ? { priceFormat } : {}),
  })
  baselineSeries.setData(macroBaselineData.map(d => ({ time: d.time, value: d.value })))
}
```

- [ ] **Step 4: Update high/low series for normalised/log mode**

The high/low stepped lines also need transformation when in normalised or log mode. After the baseline block, update the high/low series data:

```typescript
// 2. High/low stepped lines (skip in normalised mode — OHLC doesn't apply to % view)
if (!showBaseline) {
  const ddColor = (pct: number): string => {
    const severity = Math.min(1, Math.abs(pct) / 20)
    const r = Math.round(88 + (248 - 88) * severity)
    const g = Math.round(166 + (81 - 166) * severity)
    const bVal = Math.round(255 + (73 - 255) * severity)
    return `rgba(${r}, ${g}, ${bVal}, 0.5)`
  }

  let highData = macroCurve.map(b => ({ time: b.time as any, value: b.high, color: ddColor(b.drawdown_pct) }))
  let lowData = macroCurve.map(b => ({ time: b.time as any, value: b.low, color: ddColor(b.drawdown_pct) }))

  if (logScale) {
    highData = highData.map(d => ({ ...d, value: Math.log10(Math.max(d.value, 0.01)) }))
    lowData = lowData.map(d => ({ ...d, value: Math.log10(Math.max(d.value, 0.01)) }))
  }

  const highSeries = chart.addSeries(LineSeries, {
    lineWidth: 1,
    lineType: LineType.WithSteps,
    priceScaleId: 'right',
    lastValueVisible: false,
    priceLineVisible: false,
    crosshairMarkerVisible: false,
    ...(priceFormat ? { priceFormat } : {}),
  })
  highSeries.setData(highData)

  const lowSeries = chart.addSeries(LineSeries, {
    lineWidth: 1,
    lineType: LineType.WithSteps,
    priceScaleId: 'right',
    lastValueVisible: false,
    priceLineVisible: false,
    crosshairMarkerVisible: false,
    ...(priceFormat ? { priceFormat } : {}),
  })
  lowSeries.setData(lowData)
}
```

- [ ] **Step 5: Update useEffect dependency array**

```typescript
}, [macroCurve, initialCapital, showBaseline, logScale, baselineCurve])
```

- [ ] **Step 6: Verify build**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 7: Manual test (Macro mode)**

Start the app, run a backtest, go to Equity Curve tab:
1. Select a macro bucket (D/W/M/Q/Y)
2. Toggle B&H — close line normalises to %, baseline appears, high/low lines hidden
3. Toggle Log — log-scaled axis
4. Switch back to Detail — verify toggles still work
5. Toggle both off — original dollar OHLC view

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/strategy/MacroEquityChart.tsx frontend/src/features/strategy/Results.tsx
git commit -m "feat: B&H and log scale support in macro equity chart"
```

---

### Task 7: Final integration test

**Files:** None (manual testing only)

- [ ] **Step 1: Full test — Date Range Presets**

1. Open app, verify default preset is Y (1-year range)
2. Click M — range shrinks to 1 month, From/To hidden
3. Click `‹` three times — steps back 3 months
4. Click `›` — steps forward 1 month
5. Click `«` — jumps back 5 months in one click
5. Click Y — range expands to 1 year from current end
6. Click Custom (⚙) — From/To inputs appear, edit them manually
7. Click `<` / `>` in custom mode — shifts by range duration
8. Verify `>` is disabled when end = today
9. Reload page — verify preset persists

- [ ] **Step 2: Full test — Equity Curve B&H + Log**

1. Run a backtest, go to Equity Curve tab
2. Verify default = no baseline, no log (original behaviour)
3. Toggle B&H — both curves normalise to %, baseline visible
4. Toggle Log — y-axis switches to log scale
5. Toggle B&H off, keep Log — dollar values on log scale
6. Switch to macro bucket (W) — verify B&H/Log toggles persist and work
7. Switch back to Detail — verify state preserved
8. Run a backtest with very different strategy vs B&H performance — verify both curves readable on log scale

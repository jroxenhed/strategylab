# Known Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three known issues: SPY/QQQ overlay replaces candlesticks, Volume checkbox does nothing, and intraday timeframes are missing.

**Architecture:** All three fixes are frontend-only except one minor backend consideration for intraday (the backend already passes interval through to yfinance — no code changes needed there). Chart.tsx handles the candlestick + overlay rendering. Sidebar.tsx handles interval options and validation warning.

**Tech Stack:** React + TypeScript, lightweight-charts v5, FastAPI + yfinance (backend — no changes)

---

### Task 1: SPY/QQQ — Overlay on Candlestick Chart

**Files:**
- Modify: `frontend/src/components/Chart.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

Currently, `Chart.tsx` has a `showOverlay` branch that replaces the candlestick chart with a normalized % line chart when SPY/QQQ are checked. This task removes that branch and instead always renders candlesticks, with SPY/QQQ added as overlay line series on a hidden secondary price scale.

- [ ] **Step 1: Remove the three normalizedX useMemo hooks from Chart.tsx**

Delete lines 56–72 (the `normalizedSpy`, `normalizedQqq`, and `normalizedMain` useMemo blocks) entirely — they will no longer be used.

- [ ] **Step 2: Replace the main chart useEffect body in Chart.tsx**

The current `useEffect` at line 83 contains a `showOverlay` conditional that switches chart mode. Replace the entire body of that `useEffect` (everything inside the function, before `return () => { ... }`) with the following:

```typescript
if (!containerRef.current || data.length === 0) return

const chart = createChart(containerRef.current, { ...chartOptions, height: containerRef.current.clientHeight })
chartRef.current = chart

// Always render candlesticks
const candleSeries = chart.addSeries(CandlestickSeries, {
  upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN,
  wickUpColor: UP, wickDownColor: DOWN,
})
candleSeries.setData(data.map(d => ({ ...d, time: d.time as any })))

// SPY/QQQ as overlay lines — hidden secondary scale so no second price axis clutters the chart
if (showSpy && spyData && spyData.length > 0) {
  chart.addSeries(LineSeries, {
    color: '#f0883e', lineWidth: 1, title: 'SPY', priceScaleId: 'overlay',
  }).setData(spyData.map(d => ({ time: d.time as any, value: d.close })))
}
if (showQqq && qqqData && qqqData.length > 0) {
  chart.addSeries(LineSeries, {
    color: '#a371f7', lineWidth: 1, title: 'QQQ', priceScaleId: 'overlay',
  }).setData(qqqData.map(d => ({ time: d.time as any, value: d.close })))
}
if (showSpy || showQqq) {
  chart.priceScale('overlay').applyOptions({ visible: false })
}

// EMA overlays on main chart
if (activeIndicators.includes('ema') && indicatorData.ema) {
  const { ema20, ema50, ema200 } = indicatorData.ema
  chart.addSeries(LineSeries, { color: '#f0883e', lineWidth: 1, title: 'EMA20' }).setData(toLineData(ema20))
  chart.addSeries(LineSeries, { color: '#a371f7', lineWidth: 1, title: 'EMA50' }).setData(toLineData(ema50))
  chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'EMA200' }).setData(toLineData(ema200))
}

// Bollinger Bands on main chart
if (activeIndicators.includes('bb') && indicatorData.bb) {
  const { upper, middle, lower } = indicatorData.bb
  chart.addSeries(LineSeries, { color: '#30363d', lineWidth: 1, title: 'BB Upper' }).setData(toLineData(upper))
  chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'BB Mid' }).setData(toLineData(middle))
  chart.addSeries(LineSeries, { color: '#30363d', lineWidth: 1, title: 'BB Lower' }).setData(toLineData(lower))
}

// Trade markers
if (trades && trades.length > 0) createSeriesMarkers(candleSeries, buildMarkers(trades))

chart.timeScale().fitContent()

const syncHandler = (range: LogicalRange | null) => {
  if (!range) return
  const mainW = chart.priceScale('right').width()
  const macdW = macdChartRef.current?.priceScale('right').width() ?? 0
  const rsiW = rsiChartRef.current?.priceScale('right').width() ?? 0
  const maxW = Math.max(mainW, macdW, rsiW)
  if (maxW > 0) {
    chart.applyOptions({ rightPriceScale: { minimumWidth: maxW } })
    macdChartRef.current?.applyOptions({ rightPriceScale: { minimumWidth: maxW } })
    rsiChartRef.current?.applyOptions({ rightPriceScale: { minimumWidth: maxW } })
  }
  if (macdChartRef.current) macdChartRef.current.timeScale().setVisibleLogicalRange(range)
  if (rsiChartRef.current) rsiChartRef.current.timeScale().setVisibleLogicalRange(range)
}
chart.timeScale().subscribeVisibleLogicalRangeChange(syncHandler)

const ro = new ResizeObserver(() => {
  if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth })
})
ro.observe(containerRef.current)
```

Update the `useEffect` dependency array to:
```typescript
}, [data, spyData, qqqData, showSpy, showQqq, activeIndicators, indicatorData, trades])
```

- [ ] **Step 3: Update the SPY/QQQ hint in Sidebar.tsx**

Find lines 126–128 in `Sidebar.tsx`:
```tsx
{(showSpy || showQqq) && (
  <div style={{ fontSize: 11, color: '#8b949e', marginTop: 4 }}>Showing % change from start</div>
)}
```

Replace with:
```tsx
{(showSpy || showQqq) && (
  <div style={{ fontSize: 11, color: '#8b949e', marginTop: 4 }}>Overlaid on chart</div>
)}
```

- [ ] **Step 4: Verify manually**

Run: `cd /home/john/test-claude-project && ./start.sh`

Check:
- With SPY/QQQ unchecked: candlestick chart shows normally
- Check SPY: an orange line appears overlaid on the candlestick chart, no second price axis visible
- Check QQQ: a purple line also appears
- MACD/RSI/EMA/BB indicators still work normally with comparison active
- Trade markers still appear after running a backtest

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Chart.tsx frontend/src/components/Sidebar.tsx
git commit -m "fix: overlay SPY/QQQ as lines on candlestick chart instead of replacing it"
```

---

### Task 2: Volume — Overlay at Bottom of Main Chart

**Files:**
- Modify: `frontend/src/components/Chart.tsx`

Volume data is already in every `OHLCVBar` (the `volume` field). This task adds a semi-transparent histogram at the bottom 25% of the main chart when `'volume'` is in `activeIndicators`. The Volume checkbox in `Sidebar.tsx` already passes `'volume'` through `onToggleIndicator` correctly — no sidebar changes needed.

- [ ] **Step 1: Add volume series to the main chart useEffect in Chart.tsx**

After the SPY/QQQ overlay block (after the `chart.priceScale('overlay').applyOptions` call) and before the EMA block, insert:

```typescript
// Volume overlay — semi-transparent bars at bottom 25% of chart
if (activeIndicators.includes('volume')) {
  const volSeries = chart.addSeries(HistogramSeries, {
    priceFormat: { type: 'volume' },
    priceScaleId: 'volume',
  })
  volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.75, bottom: 0 } })
  volSeries.setData(data.map(d => ({
    time: d.time as any,
    value: d.volume,
    color: d.close >= d.open ? '#26a64166' : '#f8514966',
  })))
}
```

Note: `'#26a64166'` and `'#f8514966'` are 8-digit hex colors (RRGGBBAA) — the last two digits (`66` = 40% opacity) make the bars semi-transparent so candles remain readable through them.

- [ ] **Step 2: Verify manually**

With the dev server running:
- Check Volume in the sidebar: semi-transparent green/red bars appear at the bottom of the candlestick chart
- Uncheck Volume: bars disappear
- Bars are green on days where close ≥ open, red otherwise
- Candles are clearly visible above the volume bars

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Chart.tsx
git commit -m "fix: wire Volume checkbox to render semi-transparent histogram on main chart"
```

---

### Task 3: Intraday Timeframes with Range Warning

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`

Add `1m`, `5m`, `15m`, `30m` intervals to the dropdown. When the selected interval's max lookback is exceeded by the current date range, show a warning banner with the specific limit and the user's current range.

Backend note: `main.py` already passes the interval string directly to `yfinance.download()`, which accepts all these values. No backend changes needed.

- [ ] **Step 1: Add the INTERVAL_LIMITS constant and warning logic to Sidebar.tsx**

At the top of the `Sidebar` component function (just before the `return` statement), add:

```typescript
const INTERVAL_LIMITS: Record<string, number> = {
  '1m': 7,
  '5m': 60,
  '15m': 60,
  '30m': 60,
  '1h': 730,
}

const daysDiff = Math.round(
  (new Date(end).getTime() - new Date(start).getTime()) / (1000 * 60 * 60 * 24)
)
const intervalLimit = INTERVAL_LIMITS[interval]
const showIntervalWarning = intervalLimit !== undefined && daysDiff > intervalLimit
```

- [ ] **Step 2: Replace the interval dropdown and add the warning banner**

Find the interval `<select>` block in the JSX (lines 92–98):
```tsx
<select value={interval} onChange={e => onIntervalChange(e.target.value)} style={styles.dateInput}>
  <option value="1d">Daily</option>
  <option value="1wk">Weekly</option>
  <option value="1mo">Monthly</option>
  <option value="1h">1 Hour</option>
</select>
```

Replace with:
```tsx
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
  <div style={{ fontSize: 11, color: '#f0883e', marginTop: 6, lineHeight: 1.4 }}>
    {interval} data only supports {intervalLimit} days of history. Your range is {daysDiff} days — shorten the From date.
  </div>
)}
```

- [ ] **Step 3: Verify manually**

With the dev server running:
- Set interval to "5 min" with a date range longer than 60 days: orange warning appears with exact numbers
- Shorten the From date so the range is ≤ 60 days: warning disappears
- Set interval to "Daily": no warning ever appears regardless of range
- Select "1 min" with a 30-day range: warning appears (limit is 7 days)
- Select "1 Hour" with a 2-year range: warning appears (limit is 730 days)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Sidebar.tsx
git commit -m "feat: add intraday timeframe options with date range warning"
```

---

### Task 4: Set Up Branch Structure

After all three fixes are committed to `master`, set up the branch structure for future feature work.

- [ ] **Step 1: Rename master to main and push**

```bash
git branch -m master main
git push -u origin main
```

- [ ] **Step 2: Create the planned feature branches (empty, off main)**

```bash
git checkout -b fix/spy-qqq-overlay main 2>/dev/null || true
git checkout main
git checkout -b fix/volume-indicator main 2>/dev/null || true
git checkout main
git checkout -b fix/intraday-timeframes main 2>/dev/null || true
git checkout main
git checkout -b feature/more-indicators main
git checkout main
git checkout -b feature/more-strategy-rules main
git checkout main
git checkout -b feature/chart-timeframe-buttons main
git checkout main
git checkout -b feature/watchlist main
git checkout main
```

- [ ] **Step 3: Verify branches exist**

Run: `git branch`

Expected output includes:
```
  feature/chart-timeframe-buttons
  feature/more-indicators
  feature/more-strategy-rules
  feature/watchlist
  fix/intraday-timeframes
  fix/spy-qqq-overlay
  fix/volume-indicator
* main
```

- [ ] **Step 4: Commit note about branch strategy**

No commit needed — branches are local until pushed. Future work: when starting a new feature, `git checkout feature/more-indicators` and begin work there. Merge back to `main` when done.

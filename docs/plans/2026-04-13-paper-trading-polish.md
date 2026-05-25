# Paper Trading Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish the Paper Trading page with journal table enhancements (new columns, color fix, summary row, auto-refresh, CSV export), bot card heartbeat indicator, and positions table improvements (poll interval, opened-at column).

**Architecture:** All journal and positions changes are frontend-only. The heartbeat feature adds a `last_tick` field to `BotState` in the backend, updated every bot loop iteration, consumed by the existing `fetchBotDetail` poll on the frontend. Positions "Opened" column cross-references the journal API to find entry timestamps.

**Tech Stack:** React, TypeScript, lightweight-charts (sparkline), Python dataclass (BotState), Alpaca SDK

---

### Task 1: Journal — Reason Column Color Fix

**Files:**
- Modify: `frontend/src/features/trading/TradeJournal.tsx:137-177` (color functions)

- [ ] **Step 1: Update `reasonColor` to accept P&L parameter**

Change the `reasonColor` function signature and logic. The `signal` reason now uses the P&L to determine green/red instead of static blue. `entry` changes from green to orange.

```tsx
const reasonColor = (r: string | null, pnl?: number | null) => {
  if (!r) return '#8b949e'
  if (r === 'entry') return '#e5c07b'             // orange — matches Side column
  if (r === 'stop_loss') return '#f85149'          // red
  if (r === 'trailing_stop') return '#d29922'      // amber
  if (r === 'signal') {
    if (pnl != null) return pnl >= 0 ? '#26a641' : '#f85149'  // green win, red loss
    return '#8b949e'  // no P&L context
  }
  if (r === 'manual') return '#8b949e'
  return '#8b949e'
}
```

- [ ] **Step 2: Update the Reason cell render to pass P&L**

In the row render (around line 123), change:
```tsx
// Before:
<span style={{ ...styles.cell, color: reasonColor(t.reason) }}>

// After:
<span style={{ ...styles.cell, color: reasonColor(t.reason, exitPnl.get(t.id)) }}>
```

- [ ] **Step 3: Verify in browser**

Start the app (`./start.sh`), open the Paper Trading page. Check the journal:
- Entry rows: Reason "entry" should be orange (not green)
- Signal exits: Reason "signal" should be green for wins, red for losses
- Stop loss: still red
- Manual: still grey

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/trading/TradeJournal.tsx
git commit -m "fix(journal): reason column colors match Side column convention"
```

---

### Task 2: Journal — New Columns (Expected Price, Gain %)

**Files:**
- Modify: `frontend/src/features/trading/TradeJournal.tsx:48-133` (P&L computation, header, row render)

- [ ] **Step 1: Extend P&L pairing to also store entry price**

Update the `lastEntry` map and `exitPnl` map to also track entry price per exit, needed for gain % calculation. Around lines 48-69, change:

```tsx
const exitPnl = new Map<string, number>()  // trade id → pnl
const exitEntryPrice = new Map<string, number>()  // trade id → entry price
const lastEntry = new Map<string, { price: number; qty: number }>()
for (const t of trades) {
  if (t.source !== 'bot') continue
  if (t.price == null || !t.qty) continue
  const isEntry = t.side === 'buy' || t.side === 'short'
  const dir = t.side === 'buy' || t.side === 'sell' ? 'long' : 'short'
  const key = `${t.symbol}:${dir}`
  if (isEntry) {
    lastEntry.set(key, { price: t.price, qty: t.qty })
  } else {
    const entry = lastEntry.get(key)
    if (entry != null) {
      const qty = Math.min(entry.qty, t.qty)
      const pnl = dir === 'short'
        ? (entry.price - t.price) * qty
        : (t.price - entry.price) * qty
      exitPnl.set(t.id, pnl)
      exitEntryPrice.set(t.id, entry.price)
      lastEntry.delete(key)
    }
  }
}
```

- [ ] **Step 2: Update column headers**

Change the header array (line 94) from:
```tsx
{['Time', 'Symbol', 'Side', 'Qty', 'Price', 'P&L', 'Slippage', 'Source', 'Reason'].map(h => (
```
to:
```tsx
{['Time', 'Symbol', 'Side', 'Qty', 'Expected', 'Price', 'P&L', 'Gain %', 'Slippage', 'Source', 'Reason'].map(h => (
```

- [ ] **Step 3: Add Expected and Gain % cells to row render**

In the row render (lines 98-127), add the two new cells. Insert the Expected cell after Qty and before Price:

```tsx
<span style={styles.cell}>
  {t.expected_price != null ? `$${t.expected_price.toFixed(2)}` : '—'}
</span>
```

Insert the Gain % cell after P&L and before Slippage:

```tsx
<span style={{ ...styles.cell, color: exitColor(t, exitPnl) }}>
  {(() => {
    const pnl = exitPnl.get(t.id)
    const entryPx = exitEntryPrice.get(t.id)
    if (pnl == null || entryPx == null || !t.qty) return '—'
    const pct = (pnl / (entryPx * t.qty)) * 100
    const sign = pct >= 0 ? '+' : ''
    return `${sign}${pct.toFixed(2)}%`
  })()}
</span>
```

- [ ] **Step 4: Verify in browser**

Check the journal table:
- "Expected" column shows dollar values where available, `—` where null
- "Gain %" column shows percentages on exit rows, `—` on entries
- Colors on Gain % match the P&L column (green/red)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/trading/TradeJournal.tsx
git commit -m "feat(journal): add Expected Price and Gain % columns"
```

---

### Task 3: Journal — Summary Row

**Files:**
- Modify: `frontend/src/features/trading/TradeJournal.tsx:91-133` (between header and data rows)

- [ ] **Step 1: Compute summary values**

Add this computation after the `exitPnl`/`exitEntryPrice` loop, using the `filtered` trade list:

```tsx
const summaryStats = (() => {
  let totalQty = 0
  let totalPnl = 0
  let gainPcts: number[] = []
  let slippages: number[] = []
  for (const t of filtered) {
    totalQty += t.qty || 0
    const pnl = exitPnl.get(t.id)
    if (pnl != null) {
      totalPnl += pnl
      const entryPx = exitEntryPrice.get(t.id)
      if (entryPx != null && t.qty) {
        gainPcts.push((pnl / (entryPx * t.qty)) * 100)
      }
    }
    if (t.expected_price != null && t.price != null) {
      slippages.push((t.price - t.expected_price) / t.expected_price * 100)
    }
  }
  return {
    totalQty,
    totalPnl,
    avgGainPct: gainPcts.length > 0 ? gainPcts.reduce((a, b) => a + b, 0) / gainPcts.length : null,
    avgSlippage: slippages.length > 0 ? slippages.reduce((a, b) => a + b, 0) / slippages.length : null,
  }
})()
```

- [ ] **Step 2: Add summary row between header and data rows**

Insert after the `headRow` div and before the `{[...filtered].reverse().map(...)`:

```tsx
<div style={{ ...styles.row, borderBottom: '1px solid #21262d', background: '#0d1117' }}>
  <span style={styles.summaryCell} />  {/* Time */}
  <span style={styles.summaryCell} />  {/* Symbol */}
  <span style={styles.summaryCell} />  {/* Side */}
  <span style={styles.summaryCell}>{summaryStats.totalQty || ''}</span>
  <span style={styles.summaryCell} />  {/* Expected */}
  <span style={styles.summaryCell} />  {/* Price */}
  <span style={{ ...styles.summaryCell, color: summaryStats.totalPnl >= 0 ? '#26a641' : '#f85149' }}>
    {summaryStats.totalPnl !== 0 ? `${summaryStats.totalPnl >= 0 ? '+' : '-'}$${Math.abs(summaryStats.totalPnl).toFixed(2)}` : ''}
  </span>
  <span style={{ ...styles.summaryCell, color: summaryStats.avgGainPct != null ? (summaryStats.avgGainPct >= 0 ? '#26a641' : '#f85149') : '#8b949e' }}>
    {summaryStats.avgGainPct != null ? `${summaryStats.avgGainPct >= 0 ? '+' : ''}${summaryStats.avgGainPct.toFixed(2)}%` : ''}
  </span>
  <span style={{ ...styles.summaryCell, color: '#8b949e' }}>
    {summaryStats.avgSlippage != null ? `${summaryStats.avgSlippage.toFixed(3)}%` : ''}
  </span>
  <span style={styles.summaryCell} />  {/* Source */}
  <span style={styles.summaryCell} />  {/* Reason */}
</div>
```

- [ ] **Step 3: Add `summaryCell` style**

Add to the `styles` object:

```tsx
summaryCell: {
  fontSize: 11, color: '#8b949e', width: 90, flexShrink: 0,
  fontStyle: 'italic' as const,
},
```

- [ ] **Step 4: Verify in browser**

Check summary row:
- Appears below headers, above trade rows
- Shows total qty, total P&L (colored), avg gain %, avg slippage
- Updates when filter changes

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/trading/TradeJournal.tsx
git commit -m "feat(journal): add summary row with aggregate stats"
```

---

### Task 4: Journal — Filter Relocation, Auto-Refresh, CSV Export

**Files:**
- Modify: `frontend/src/features/trading/TradeJournal.tsx:76-88` (header), `24-38` (useEffect)

- [ ] **Step 1: Move filter, add export button**

Rearrange the header. Remove `marginLeft: 'auto'` from the filter style. Add a download button with `marginLeft: 'auto'` in its place.

Replace the header div (lines 78-88):

```tsx
<div style={styles.header}>
  <span style={styles.title}>Trade Journal</span>
  <span style={styles.count}>{trades.length}</span>
  <button onClick={reload} style={styles.reload} title="Refresh">↻</button>
  <input
    style={styles.filter}
    placeholder="Filter symbol..."
    value={filter}
    onChange={e => setFilter(e.target.value)}
  />
  <button onClick={exportCsv} style={{ ...styles.reload, marginLeft: 'auto' }} title="Export CSV">⬇</button>
</div>
```

- [ ] **Step 2: Add `exportCsv` function**

Add this function before the return statement:

```tsx
const exportCsv = () => {
  const headers = ['Time', 'Symbol', 'Side', 'Qty', 'Expected', 'Price', 'P&L', 'Gain %', 'Slippage', 'Source', 'Reason']
  const rows = [...filtered].reverse().map(t => {
    const pnl = exitPnl.get(t.id)
    const entryPx = exitEntryPrice.get(t.id)
    const gainPct = (pnl != null && entryPx != null && t.qty)
      ? ((pnl / (entryPx * t.qty)) * 100).toFixed(2) + '%'
      : ''
    const slippage = (t.expected_price != null && t.price != null)
      ? ((t.price - t.expected_price) / t.expected_price * 100).toFixed(3) + '%'
      : ''
    return [
      fmtTime(t.timestamp),
      t.symbol,
      t.side.toUpperCase(),
      t.qty || '',
      t.expected_price != null ? t.expected_price.toFixed(2) : '',
      t.price != null ? t.price.toFixed(2) : '',
      pnl != null ? pnl.toFixed(2) : '',
      gainPct,
      slippage,
      t.source,
      t.reason || '',
    ].join(',')
  })
  const csv = [headers.join(','), ...rows].join('\n')
  const blob = new Blob([csv], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  const dateStr = new Date().toISOString().slice(0, 10)
  a.download = `trades-${filter || 'all'}-${dateStr}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
```

- [ ] **Step 3: Add auto-refresh interval**

Update the existing `useEffect` (around line 38) from:

```tsx
useEffect(() => { reload() }, [])
```

to:

```tsx
useEffect(() => {
  reload()
  const id = setInterval(reload, 5000)
  return () => clearInterval(id)
}, [])
```

- [ ] **Step 4: Remove `marginLeft: 'auto'` from filter style**

In the `styles` object, change the `filter` entry (around line 196):

```tsx
filter: {
  fontSize: 12, padding: '3px 8px', borderRadius: 4,
  background: '#161b22', color: '#e6edf3', border: '1px solid #30363d',
  outline: 'none', width: 120,
},
```

(Remove `marginLeft: 'auto'`)

- [ ] **Step 5: Verify in browser**

- Filter field is next to title/count/reload, not right-aligned
- Download button is far-right
- Click download — CSV file downloads with correct columns and data
- Journal auto-refreshes (watch for new trades appearing without manual refresh)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/trading/TradeJournal.tsx
git commit -m "feat(journal): relocate filter, add auto-refresh and CSV export"
```

---

### Task 5: Bot Card Heartbeat — Backend

**Files:**
- Modify: `backend/bot_manager.py:75-123` (BotState dataclass + to_dict)
- Modify: `backend/bot_runner.py:92-99` (tick method)

- [ ] **Step 1: Add `last_tick` field to BotState**

In `backend/bot_manager.py`, add the field after `last_scan_at` (line 78):

```python
last_tick: Optional[str] = None
```

- [ ] **Step 2: Add `last_tick` to `to_dict()`**

In the `to_dict()` method (around line 103-123), add after `"last_scan_at"`:

```python
"last_tick": self.last_tick,
```

- [ ] **Step 3: Update `_tick()` to set `last_tick`**

In `backend/bot_runner.py`, at the top of `_tick()` (line 98), add before `state.last_scan_at`:

```python
state.last_tick = datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 4: Verify backend**

Start the backend, start a bot, and check `GET /api/bots/{id}` — the state should include a `last_tick` ISO timestamp that updates every poll cycle.

- [ ] **Step 5: Commit**

```bash
git add backend/bot_manager.py backend/bot_runner.py
git commit -m "feat(bot): add last_tick heartbeat to BotState"
```

---

### Task 6: Bot Card Heartbeat — Frontend

**Files:**
- Modify: `frontend/src/shared/types/index.ts:285-300` (BotState type)
- Modify: `frontend/src/features/trading/BotCard.tsx:109-115` (header row)

- [ ] **Step 1: Add `last_tick` to BotState type**

In `frontend/src/shared/types/index.ts`, add to the `BotState` interface (after `last_scan_at` around line 288):

```typescript
last_tick?: string
```

- [ ] **Step 2: Add heartbeat dot to BotCard**

In `frontend/src/features/trading/BotCard.tsx`, add a heartbeat dot function before the `BotCard` component:

```tsx
function heartbeatColor(summary: BotSummary, detail: BotDetail | null): string {
  if (summary.status === 'stopped') return '#484f58'  // grey
  if (!detail?.state.last_tick) return '#484f58'
  const elapsed = (Date.now() - new Date(detail.state.last_tick).getTime()) / 1000
  const interval = POLL_SECONDS[summary.interval] ?? 60
  return elapsed <= interval * 2 ? '#26a641' : '#f85149'  // green or red
}
```

Add the poll interval map at the top of the file (after imports):

```tsx
const POLL_SECONDS: Record<string, number> = { '1m': 10, '5m': 15, '15m': 20, '30m': 30, '1h': 60 }
```

- [ ] **Step 3: Render the heartbeat dot**

In the header row (around line 110), add the heartbeat dot after the existing status dot:

```tsx
{/* Status dot */}
<div style={{
  width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
  background: statusColor(summary.status),
  boxShadow: running ? `0 0 6px ${statusColor(summary.status)}` : 'none',
}} />
{/* Heartbeat dot */}
<div
  title={detail?.state.last_tick ? `Last tick: ${fmtTimeET(detail.state.last_tick)}` : 'No tick yet'}
  style={{
    width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
    background: heartbeatColor(summary, detail),
    marginLeft: -4,
  }}
/>
```

- [ ] **Step 4: Verify in browser**

- Running bot: small green dot next to the status dot, tooltip shows last tick time
- Stop a bot: dot turns grey
- If you kill the backend and wait, the dot should turn red (stale)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/shared/types/index.ts frontend/src/features/trading/BotCard.tsx
git commit -m "feat(bot-card): add heartbeat indicator dot"
```

---

### Task 7: Positions Table — Poll Interval + Opened Column

**Files:**
- Modify: `frontend/src/features/trading/PositionsTable.tsx:1-105`

- [ ] **Step 1: Change poll interval**

In `PositionsTable.tsx` line 13, change:

```tsx
const id = window.setInterval(load, 30_000)
```

to:

```tsx
const id = window.setInterval(load, 5_000)
```

- [ ] **Step 2: Add journal fetch for entry timestamps**

Add journal import and state at the top of the component:

```tsx
import { fetchPositions, placeSell, type Position } from '../../api/trading'
import { fetchJournal, type JournalTrade } from '../../api/trading'
import { fmtShortET } from '../../shared/utils/time'
```

(Merge the imports — both come from `../../api/trading`):

```tsx
import { fetchPositions, placeSell, fetchJournal, type Position, type JournalTrade } from '../../api/trading'
import { fmtShortET } from '../../shared/utils/time'
```

Add journal state and load it alongside positions:

```tsx
const [positions, setPositions] = useState<Position[]>([])
const [journal, setJournal] = useState<JournalTrade[]>([])
const [closing, setClosing] = useState<string | null>(null)

const load = () => {
  fetchPositions().then(setPositions).catch(() => {})
  fetchJournal().then(setJournal).catch(() => {})
}
```

- [ ] **Step 3: Build entry time lookup**

After the state declarations, compute the entry time map:

```tsx
const entryTimeMap = new Map<string, string>()
for (const t of journal) {
  if (t.source !== 'bot') continue
  const isEntry = t.side === 'buy' || t.side === 'short'
  if (isEntry) entryTimeMap.set(t.symbol, t.timestamp)
}
```

- [ ] **Step 4: Add "Opened" column**

Update the header array:

```tsx
{['Opened', 'Symbol', 'Qty', 'Avg Entry', 'Current', 'Mkt Value', 'P&L', 'P&L %', ''].map(h => (
```

Add the Opened cell as the first cell in each position row (before Symbol):

```tsx
<span style={styles.cell}>
  {entryTimeMap.get(p.symbol) ? fmtShortET(entryTimeMap.get(p.symbol)!) : '—'}
</span>
```

- [ ] **Step 5: Verify in browser**

- Positions table polls every 5s (check network tab)
- "Opened" column shows entry timestamps matching the journal
- Positions without journal entries show `—`

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/trading/PositionsTable.tsx
git commit -m "feat(positions): 5s poll interval + Opened column from journal"
```

---

### Task 8: Final Verification

- [ ] **Step 1: Full page walkthrough**

Open the Paper Trading page and verify all changes together:

1. **Journal**: 11 columns visible (Time, Symbol, Side, Qty, Expected, Price, P&L, Gain %, Slippage, Source, Reason)
2. **Journal colors**: entry reason=orange, signal reason=green/red, stop_loss=red
3. **Summary row**: visible below headers with totals
4. **Filter**: next to title, not far-right
5. **Export**: download button far-right, produces valid CSV
6. **Auto-refresh**: journal updates without manual clicks
7. **Bot heartbeat**: green dot on running bots, grey on stopped
8. **Positions**: "Opened" column with timestamps, polls every 5s

- [ ] **Step 2: Commit any final tweaks**

If any spacing/alignment issues are found, fix and commit.

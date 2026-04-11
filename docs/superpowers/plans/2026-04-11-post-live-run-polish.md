# Post-first-live-run polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Group A (sparkline toggle, global start/stop, gain/loss stats, buy & hold overlay) and Group C (Discovery page split) from the 2026-04-11 post-live-run polish spec.

**Architecture:** Each section is one commit. Backend changes thread through `routes/bots.py` / `routes/backtest.py` and frontend consumers pick up the new fields. The Discovery move is a pure refactor done first so the rest of the session ships against a clean layout.

**Tech Stack:** Python/FastAPI (backend), React/TypeScript/Vite (frontend), lightweight-charts v5.

**Spec:** `docs/superpowers/specs/2026-04-11-post-live-run-polish-design.md`

**Implementation order:** Task 1 (Discovery refactor) → Task 2 (sparkline toggle) → Task 3 (global start/stop) → Task 4 (buy & hold overlay) → Task 5 (gain/loss stats + histogram).

**Testing philosophy:** Backend logic gets unit tests where practical (matches existing `backend/tests/` coverage — backtest/models/providers). UI changes are verified manually in the running dev server. `./start.sh` runs both halves; backend auto-reloads on save, frontend via Vite HMR.

---

## Task 1: Discovery page — file move + new tab

**Goal:** Move `SignalScanner` and `PerformanceComparison` out of `features/trading/` into a new `features/discovery/` folder, add a third top-level nav tab, drop the imports from `PaperTrading.tsx`. Pure relocation, no behavior changes.

**Files:**
- Create: `frontend/src/features/discovery/Discovery.tsx`
- Move: `frontend/src/features/trading/SignalScanner.tsx` → `frontend/src/features/discovery/SignalScanner.tsx`
- Move: `frontend/src/features/trading/PerformanceComparison.tsx` → `frontend/src/features/discovery/PerformanceComparison.tsx`
- Modify: `frontend/src/features/trading/PaperTrading.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Move the two files with git mv**

```bash
cd /Users/jroxenhed/Documents/strategylab
mkdir -p frontend/src/features/discovery
git mv frontend/src/features/trading/SignalScanner.tsx frontend/src/features/discovery/SignalScanner.tsx
git mv frontend/src/features/trading/PerformanceComparison.tsx frontend/src/features/discovery/PerformanceComparison.tsx
```

- [ ] **Step 2: Fix relative imports in the two moved files**

Both files now live one directory deeper's sibling. Relative imports like `'../../shared/...'` or `'../../api/...'` remain correct (same depth), but imports to siblings in `features/trading/` must be updated. Grep each file for any `from './` or `from '../trading'` imports and repoint them.

```bash
grep -n "from '" frontend/src/features/discovery/SignalScanner.tsx
grep -n "from '" frontend/src/features/discovery/PerformanceComparison.tsx
```

Expected: if any import starts with `./` pointing at a former `trading/` sibling, it needs to become `'../trading/<name>'`. Fix in-place.

- [ ] **Step 3: Create `Discovery.tsx`**

```tsx
// frontend/src/features/discovery/Discovery.tsx
import SignalScanner from './SignalScanner'
import PerformanceComparison from './PerformanceComparison'

export default function Discovery() {
  return (
    <div style={styles.container}>
      <SignalScanner />
      <PerformanceComparison />
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex', flexDirection: 'column',
    height: '100%', overflowY: 'auto', background: '#0d1117',
  },
}
```

- [ ] **Step 4: Remove the two imports/JSX lines from `PaperTrading.tsx`**

Edit `frontend/src/features/trading/PaperTrading.tsx`. Drop the `SignalScanner` and `PerformanceComparison` imports and their JSX lines. After the edit the file should look like:

```tsx
import AccountBar from './AccountBar'
import BotControlCenter from './BotControlCenter'
import PositionsTable from './PositionsTable'
import TradeJournal from './TradeJournal'
import OrderHistory from './OrderHistory'

export default function PaperTrading() {
  return (
    <div style={styles.container}>
      <AccountBar />
      <BotControlCenter />
      <PositionsTable />
      <TradeJournal />
      <OrderHistory />
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex', flexDirection: 'column',
    height: '100%', overflowY: 'auto', background: '#0d1117',
  },
}
```

- [ ] **Step 5: Wire the Discovery tab in `App.tsx`**

Five edits in `frontend/src/App.tsx`:

1. Add import below the `PaperTrading` import:
   ```ts
   import Discovery from './features/discovery/Discovery'
   ```

2. Widen the `AppTab` union:
   ```ts
   type AppTab = 'chart' | 'trading' | 'discovery'
   ```

3. Change the tab list and label mapping inside the header:
   ```tsx
   {(['chart', 'trading', 'discovery'] as const).map(tab => (
     <button
       key={tab}
       onClick={() => setActiveTab(tab)}
       style={{ ...styles.tab, ...(activeTab === tab ? styles.tabActive : {}) }}
     >
       {tab === 'chart' ? 'Chart' : tab === 'trading' ? 'Paper Trading' : 'Discovery'}
     </button>
   ))}
   ```

4. Add a third conditional render block after the existing `trading` block:
   ```tsx
   <div style={{ height: '100%', display: activeTab === 'discovery' ? 'block' : 'none' }}>
     <Discovery />
   </div>
   ```

- [ ] **Step 6: Verify build + app loads**

```bash
cd /Users/jroxenhed/Documents/strategylab/frontend
npm run build
```

Expected: clean build, no TypeScript errors. If there are errors, they will almost always be a missed relative import from Step 2.

Then in the running dev server: click all three tabs. Paper Trading shows AccountBar/BotControlCenter/PositionsTable/TradeJournal/OrderHistory (no scanner). Discovery shows SignalScanner and PerformanceComparison.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: split SignalScanner + PerformanceComparison into Discovery tab

Paper Trading is now the operational cockpit; Discovery is the research
home for scanner + performance tools. Pure relocation — both components
will be rearchitected when Group D (candidate discovery, batch backtest,
AI parameter tuning) lands.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Bot sparkline — global local/aligned timescale toggle

**Goal:** Add a global `Local` / `Aligned` toggle in the bot page header. In `Aligned` mode, all cards render their equity curve into a shared window `[min(first trade across bots), now]`. In `Local` mode (default, current behavior), each card fits its own data.

**Files:**
- Modify: `backend/bot_manager.py` (add `first_trade_time` to `list_bots()` output)
- Modify: `frontend/src/shared/types/index.ts` (extend `BotSummary` with the new field)
- Modify: `frontend/src/features/trading/MiniSparkline.tsx` (accept optional `alignedRange` prop)
- Modify: `frontend/src/features/trading/BotCard.tsx` (pass prop through)
- Modify: `frontend/src/features/trading/BotControlCenter.tsx` (toggle UI + range calc + persist)

- [ ] **Step 1: Backend — expose first trade time per bot**

Edit `backend/bot_manager.py`. In `BotManager.list_bots()` (around line 364), each `result.append({...})` needs a new `first_trade_time` field. The earliest entry in `state.equity_snapshots` is the first trade-equivalent event. If there are no snapshots, return `None`.

Replace the existing list comprehension with the version below (the change is the new `"first_trade_time"` key):

```python
def list_bots(self) -> list[dict]:
    result = []
    for bot_id, (config, state) in self.bots.items():
        first_trade_time = state.equity_snapshots[0]["time"] if state.equity_snapshots else None
        result.append({
            "bot_id": bot_id,
            "strategy_name": config.strategy_name,
            "symbol": config.symbol,
            "interval": config.interval,
            "allocated_capital": config.allocated_capital,
            "status": state.status,
            "trades_count": state.trades_count,
            "total_pnl": round(compute_realized_pnl(config.symbol, config.direction), 2),
            "backtest_summary": state.backtest_result.get("summary") if state.backtest_result else None,
            "data_source": config.data_source,
            "direction": config.direction,
            "avg_slippage_pct": round(sum(state.slippage_pcts) / len(state.slippage_pcts), 4) if state.slippage_pcts else None,
            "has_position": state.entry_price is not None,
            "first_trade_time": first_trade_time,
        })
    return result
```

- [ ] **Step 2: Frontend types — add `first_trade_time` to `BotSummary`**

Edit `frontend/src/shared/types/index.ts`. In the `BotSummary` interface (around line 238), add:

```ts
first_trade_time?: string | null
```

- [ ] **Step 3: `MiniSparkline` — accept optional aligned range**

Rewrite `frontend/src/features/trading/MiniSparkline.tsx`:

```tsx
import { useEffect, useRef } from 'react'
import { createChart, BaselineSeries } from 'lightweight-charts'

interface Props {
  equityData: { time: string; value: number }[]
  alignedRange?: { from: number; to: number }
}

export default function MiniSparkline({ equityData, alignedRange }: Props) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!ref.current || equityData.length < 2) return
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth,
      height: 60,
      layout: { background: { color: 'transparent' }, textColor: '#aaa' },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      leftPriceScale: { visible: false },
      rightPriceScale: { visible: false },
      timeScale: { visible: false, timeVisible: true, secondsVisible: true },
      crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
      handleScroll: false,
      handleScale: false,
    })
    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: 0 },
      topLineColor: '#26a69a',
      topFillColor1: 'rgba(38,166,154,0.2)',
      topFillColor2: 'rgba(38,166,154,0.02)',
      bottomLineColor: '#ef5350',
      bottomFillColor1: 'rgba(239,83,80,0.02)',
      bottomFillColor2: 'rgba(239,83,80,0.2)',
      lineWidth: 1,
      priceScaleId: 'right',
    })
    const mapped = equityData
      .map(d => ({ time: Math.floor(new Date(d.time).getTime() / 1000), value: d.value }))
      .sort((a, b) => a.time - b.time)
      .filter((d, i, arr) => i === 0 || d.time > arr[i - 1].time) as any
    series.setData(mapped)

    const applyRange = () => {
      if (alignedRange && alignedRange.to > alignedRange.from) {
        chart.timeScale().setVisibleRange({
          from: alignedRange.from as any,
          to: alignedRange.to as any,
        })
      } else {
        chart.timeScale().fitContent()
      }
    }
    applyRange()

    const ro = new ResizeObserver(() => {
      if (!ref.current) return
      chart.applyOptions({ width: ref.current.clientWidth })
      applyRange()
    })
    ro.observe(ref.current)

    return () => { ro.disconnect(); chart.remove() }
  }, [equityData, alignedRange?.from, alignedRange?.to])

  if (equityData.length < 2) return null
  return <div ref={ref} style={{ width: '100%', height: 60 }} />
}
```

- [ ] **Step 4: `BotCard` — thread `alignedRange` prop through**

Edit `frontend/src/features/trading/BotCard.tsx`. Add `alignedRange` to the component's prop list and pass it to `MiniSparkline`.

Change the props destructure (around line 57):

```tsx
export default function BotCard({
  summary,
  onStart, onStop, onBacktest, onDelete, onManualBuy, onUpdate,
  alignedRange,
}: {
  summary: BotSummary
  onStart: () => void
  onStop: () => void
  onBacktest: () => void
  onDelete: () => void
  onManualBuy: () => void
  onUpdate: (updates: Record<string, unknown>) => void
  alignedRange?: { from: number; to: number }
}) {
```

Change the `MiniSparkline` JSX (around line 244):

```tsx
<MiniSparkline
  equityData={detail?.state.equity_snapshots ?? []}
  alignedRange={alignedRange}
/>
```

- [ ] **Step 5: `BotControlCenter` — add the toggle, compute range, pass down**

Edit `frontend/src/features/trading/BotControlCenter.tsx`. Three changes:

(a) Add a `sparklineScale` state, persisted in localStorage. Insert near the other `useState` calls at the top of the component:

```tsx
const [sparklineScale, setSparklineScale] = useState<'local' | 'aligned'>(() => {
  const v = localStorage.getItem('sparklineScale')
  return v === 'aligned' ? 'aligned' : 'local'
})
useEffect(() => {
  localStorage.setItem('sparklineScale', sparklineScale)
}, [sparklineScale])
```

(b) Compute the aligned range from the loaded bots (derive from earliest `first_trade_time` across all bots to `now`). Add this just before the `return`:

```tsx
const alignedRange = (() => {
  if (sparklineScale !== 'aligned') return undefined
  const times = bots
    .map(b => b.first_trade_time)
    .filter((t): t is string => !!t)
    .map(t => Math.floor(new Date(t).getTime() / 1000))
  if (times.length === 0) return undefined
  return { from: Math.min(...times), to: Math.floor(Date.now() / 1000) }
})()
```

(c) Add the toggle button next to the "Live Trading Bots" heading, and pass `alignedRange` to each `BotCard`. Replace the existing heading row:

```tsx
<div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '0 2px' }}>
  <div style={{ color: '#e6edf3', fontWeight: 700, fontSize: 14 }}>
    Live Trading Bots
  </div>
  <div style={{ flex: 1 }} />
  <div style={{ display: 'flex', gap: 4, background: '#0d1117', border: '1px solid #1e2530', borderRadius: 4, padding: 2 }}>
    {(['local', 'aligned'] as const).map(mode => (
      <button
        key={mode}
        onClick={() => setSparklineScale(mode)}
        style={{
          fontSize: 11, padding: '3px 10px', borderRadius: 3, border: 'none', cursor: 'pointer',
          background: sparklineScale === mode ? '#1e3a5f' : 'transparent',
          color: sparklineScale === mode ? '#e6edf3' : '#8b949e',
        }}
      >
        {mode === 'local' ? 'Local' : 'Aligned'}
      </button>
    ))}
  </div>
</div>
```

And update the bot card render (around line 172):

```tsx
{bots.map(bot => (
  <BotCard
    key={bot.bot_id}
    summary={bot}
    alignedRange={alignedRange}
    onStart={() => handleStart(bot.bot_id)}
    onStop={() => handleStop(bot.bot_id)}
    onBacktest={() => handleBacktest(bot.bot_id)}
    onDelete={() => handleDelete(bot.bot_id)}
    onManualBuy={() => handleManualBuy(bot.bot_id)}
    onUpdate={(updates) => handleUpdate(bot.bot_id, updates)}
  />
))}
```

- [ ] **Step 6: Verify in the dev server**

With at least two bots that have trades at different times:

1. Default is `Local` — each card fits its own window (current behavior).
2. Click `Aligned` — all cards share the same x-axis from the earliest trade across all bots to now. Cards with recent-only activity show as a small blip on the right.
3. Refresh the browser — `Aligned` persists.
4. Switch back to `Local` — refresh — persistence again.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat: global local/aligned sparkline timescale toggle

Bot page header gets a Local/Aligned toggle. Aligned mode fits every
card into a shared [min(first_trade across bots), now] window so you
can scan timing across bots at a glance. Choice persists in
localStorage. Backend now includes first_trade_time in bot summaries.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Global start/stop all bots

**Goal:** Three header buttons — `Start All`, `Stop All`, `Stop and Close` — each backed by a new endpoint that iterates the bot list applying the existing per-bot operation. `Stop and Close` requires a confirm dialog.

**Files:**
- Modify: `backend/routes/bots.py` (add three endpoints)
- Modify: `frontend/src/api/bots.ts` (add client functions)
- Modify: `frontend/src/features/trading/BotControlCenter.tsx` (header buttons + handlers)

- [ ] **Step 1: Backend — three new endpoints**

Edit `backend/routes/bots.py`. Add below the existing actions section (after `backtest_bot` near line 201):

```python
# ---------------------------------------------------------------------------
# Bulk actions
# ---------------------------------------------------------------------------

@router.post("/start-all")
def start_all_bots():
    """Start every stopped bot. Silently skips bots in error state."""
    mgr = _get_manager()
    started: list[str] = []
    skipped: list[str] = []
    failed: list[dict] = []
    for bot_id, (_, state) in list(mgr.bots.items()):
        if state.status != "stopped":
            skipped.append(bot_id)
            continue
        try:
            mgr.start_bot(bot_id)
            started.append(bot_id)
        except Exception as e:
            failed.append({"bot_id": bot_id, "error": str(e)})
    return {"started": started, "skipped": skipped, "failed": failed}


@router.post("/stop-all")
def stop_all_bots():
    """Stop every running bot. Leaves positions open."""
    mgr = _get_manager()
    stopped: list[str] = []
    failed: list[dict] = []
    for bot_id, (_, state) in list(mgr.bots.items()):
        if state.status != "running":
            continue
        try:
            mgr.stop_bot(bot_id, close_position=False)
            stopped.append(bot_id)
        except Exception as e:
            failed.append({"bot_id": bot_id, "error": str(e)})
    return {"stopped": stopped, "failed": failed}


@router.post("/stop-and-close-all")
def stop_and_close_all_bots():
    """Stop every running bot AND flatten open positions at market."""
    mgr = _get_manager()
    closed: list[str] = []
    failed: list[dict] = []
    for bot_id, (_, state) in list(mgr.bots.items()):
        if state.status != "running":
            continue
        try:
            mgr.stop_bot(bot_id, close_position=True)
            closed.append(bot_id)
        except Exception as e:
            failed.append({"bot_id": bot_id, "error": str(e)})
    return {"closed": closed, "failed": failed}
```

**Note on endpoint path ordering:** These paths (`/start-all`, `/stop-all`, `/stop-and-close-all`) contain hyphens so they won't collide with `/{bot_id}` routes, but to be safe add them *before* the `/{bot_id}`-style routes in the file. The comment block header in the file (`NOTE: /api/bots/fund is registered before /{id} routes...`) already warns about this — follow the same pattern.

Check ordering: move these three definitions up to just after the `Bot CRUD` section so they're registered before `@router.post("/{bot_id}/start")`.

- [ ] **Step 2: Frontend — API client functions**

Edit `frontend/src/api/bots.ts`. Append:

```ts
export async function startAllBots(): Promise<{ started: string[]; skipped: string[]; failed: { bot_id: string; error: string }[] }> {
  const res = await api.post('/api/bots/start-all')
  return res.data
}

export async function stopAllBots(): Promise<{ stopped: string[]; failed: { bot_id: string; error: string }[] }> {
  const res = await api.post('/api/bots/stop-all')
  return res.data
}

export async function stopAndCloseAllBots(): Promise<{ closed: string[]; failed: { bot_id: string; error: string }[] }> {
  const res = await api.post('/api/bots/stop-and-close-all')
  return res.data
}
```

- [ ] **Step 3: Frontend — buttons + handlers in `BotControlCenter.tsx`**

Edit `frontend/src/features/trading/BotControlCenter.tsx`. Two changes:

(a) Update the import line near the top:

```tsx
import {
  listBots, setBotFund, addBot,
  startBot, stopBot, backtestBot, deleteBot, manualBuyBot, updateBot,
  startAllBots, stopAllBots, stopAndCloseAllBots,
} from '../../api/bots'
```

(b) Add three handlers inside the component (near the other `handleX` functions):

```tsx
const handleStartAll = async () => {
  try {
    const r = await startAllBots()
    await loadBots()
    if (r.failed.length) setError(`Started ${r.started.length}, ${r.failed.length} failed`)
  } catch (e: any) {
    setError(e?.response?.data?.detail ?? 'Failed to start all bots')
  }
}

const handleStopAll = async () => {
  try {
    const r = await stopAllBots()
    await loadBots()
    if (r.failed.length) setError(`Stopped ${r.stopped.length}, ${r.failed.length} failed`)
  } catch (e: any) {
    setError(e?.response?.data?.detail ?? 'Failed to stop all bots')
  }
}

const handleStopAndCloseAll = async () => {
  const openCount = bots.filter(b => b.has_position).length
  const running = bots.filter(b => b.status === 'running').length
  if (!window.confirm(`Close ${openCount} open position${openCount === 1 ? '' : 's'} at market and stop ${running} running bot${running === 1 ? '' : 's'}?`)) {
    return
  }
  try {
    const r = await stopAndCloseAllBots()
    await loadBots()
    if (r.failed.length) setError(`Closed ${r.closed.length}, ${r.failed.length} failed`)
  } catch (e: any) {
    setError(e?.response?.data?.detail ?? 'Failed to stop and close all bots')
  }
}
```

(c) Add three buttons in the header row next to the sparkline toggle added in Task 2. Update that header row:

```tsx
<div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '0 2px', flexWrap: 'wrap' }}>
  <div style={{ color: '#e6edf3', fontWeight: 700, fontSize: 14 }}>
    Live Trading Bots
  </div>
  <div style={{ display: 'flex', gap: 6 }}>
    <button onClick={handleStartAll} style={btnStyle('#1a3a2a')}>Start All</button>
    <button onClick={handleStopAll} style={btnStyle('#3a1a1a')}>Stop All</button>
    <button onClick={handleStopAndCloseAll} style={btnStyle('#5a1a1a')}>Stop and Close</button>
  </div>
  <div style={{ flex: 1 }} />
  <div style={{ display: 'flex', gap: 4, background: '#0d1117', border: '1px solid #1e2530', borderRadius: 4, padding: 2 }}>
    {(['local', 'aligned'] as const).map(mode => (
      <button
        key={mode}
        onClick={() => setSparklineScale(mode)}
        style={{
          fontSize: 11, padding: '3px 10px', borderRadius: 3, border: 'none', cursor: 'pointer',
          background: sparklineScale === mode ? '#1e3a5f' : 'transparent',
          color: sparklineScale === mode ? '#e6edf3' : '#8b949e',
        }}
      >
        {mode === 'local' ? 'Local' : 'Aligned'}
      </button>
    ))}
  </div>
</div>
```

- [ ] **Step 4: Verify in dev server**

With at least two bots (mix of stopped + running, ideally one with an open position):

1. Click `Start All` — stopped bots move to running; skipped bots unaffected.
2. Click `Stop All` — running bots move to stopped; positions remain in Alpaca.
3. Click `Stop and Close` — confirm dialog fires with the correct count. Cancel works. Accept → bots stop and positions are market-closed.
4. Verify Alpaca Positions tab reflects the closed positions after step 3.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat: global Start All / Stop All / Stop and Close

Bot page header gets three bulk-action buttons. Start All skips bots
in error state silently. Stop and Close has a confirm dialog with
the count of positions about to be flattened — the rare-use panic
button for weekends and emergencies.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Backtest equity curve — buy & hold overlay

**Goal:** Add a "Show buy & hold" checkbox above the equity curve. When on, a second grey line shows what the same initial capital would be worth if just long-held through the period. For short-direction strategies, still show long buy & hold — the point is "was shorting worth it vs. holding?"

**Files:**
- Modify: `backend/routes/backtest.py` (compute + return `baseline_curve`)
- Modify: `frontend/src/shared/types/index.ts` (add field)
- Modify: `frontend/src/features/strategy/Results.tsx` (toggle + second series)

- [ ] **Step 1: Backend — compute baseline curve**

Edit `backend/routes/backtest.py`. The baseline curve is `initial_capital * close[i] / close[0]` at each bar, always long. Add this computation just before the `result = {...}` block around line 286:

```python
        # Buy & hold baseline curve (always long, even for short strategies)
        first_close = float(close.iloc[0])
        baseline_curve = [
            {
                "time": _format_time(df.index[i], req.interval),
                "value": round(req.initial_capital * float(close.iloc[i]) / first_close, 2),
            }
            for i in range(len(df))
        ]
```

Then add `"baseline_curve": baseline_curve,` to the `result` dict (right after `"equity_curve": equity,`):

```python
        result = {
            "summary": {
                "initial_capital": req.initial_capital,
                "final_value": round(final_value, 2),
                "total_return_pct": round(total_return, 2),
                "buy_hold_return_pct": round(buy_hold_return, 2),
                "num_trades": len(sell_trades),
                "win_rate_pct": round(win_rate, 2),
                "sharpe_ratio": round(sharpe, 3),
                "max_drawdown_pct": round(max_drawdown, 2),
            },
            "trades": trades,
            "equity_curve": equity,
            "baseline_curve": baseline_curve,
        }
```

- [ ] **Step 2: Frontend types — extend `BacktestResult`**

Edit `frontend/src/shared/types/index.ts`. Add to the `BacktestResult` interface:

```ts
baseline_curve?: TimeValue[]
```

Just below `equity_curve: TimeValue[]`.

- [ ] **Step 3: Frontend — toggle + second series in `Results.tsx`**

Edit `frontend/src/features/strategy/Results.tsx`. Three changes:

(a) Add state for the toggle near the top of the component (with the other `useState` calls):

```tsx
const [showBaseline, setShowBaseline] = useState(false)
```

(b) Change the equity curve `useEffect` dependency array to include `showBaseline`, and — inside the effect — add a second line series when the toggle is on. After `series.setData(...)` but before the `if (mainChart)` alignment block, add:

```tsx
if (showBaseline && result.baseline_curve && result.baseline_curve.length > 0) {
  const baselineSeries = chart.addSeries(LineSeries, {
    color: '#8b949e',
    lineWidth: 1,
    lineStyle: 2, // dashed
    priceLineVisible: false,
    lastValueVisible: false,
  })
  baselineSeries.setData(
    result.baseline_curve
      .filter(d => d.value !== null)
      .map(d => ({ time: d.time as any, value: d.value as number }))
  )
}
```

This requires adding `LineSeries` to the top-of-file import:

```tsx
import { createChart, BaselineSeries, LineSeries, ColorType } from 'lightweight-charts'
```

And update the effect dependency list at the bottom from `[activeTab, equity_curve, summary.total_return_pct, mainChart]` to:

```tsx
}, [activeTab, equity_curve, summary.total_return_pct, mainChart, showBaseline, result.baseline_curve])
```

(c) Add the toggle checkbox above the chart area. Replace the equity tab render block:

```tsx
{activeTab === 'equity' && (
  <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
    <label style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', fontSize: 11, color: '#8b949e', cursor: 'pointer' }}>
      <input
        type="checkbox"
        checked={showBaseline}
        onChange={e => setShowBaseline(e.target.checked)}
      />
      Show buy &amp; hold baseline
    </label>
    <div ref={chartRef} style={{ width: '100%', height: 200, minHeight: 100, maxHeight: 600, resize: 'vertical', overflow: 'hidden' }} />
  </div>
)}
```

- [ ] **Step 4: Verify**

Run a backtest on any ticker:

1. Open the `Equity Curve` tab.
2. Toggle `Show buy & hold baseline` on — a dashed grey line appears. It starts at the same initial capital as the strategy, ends at `initial_capital * (last_close / first_close)`.
3. Run a **short** strategy backtest — baseline is still the long buy & hold line, not inverse.
4. Toggle off — dashed line disappears.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat: buy & hold baseline overlay on equity curve

Toggle above the chart adds a dashed grey reference line showing what
the same initial capital would become under simple long-and-hold.
Short strategies still show long B&H — the point is "was shorting
worth it vs. just holding?"

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Strategy summary — gain/loss distribution stats

**Goal:** Add per-trade P&L distribution stats (min/max/mean/median per side) and a small SVG histogram to the summary tab. Mean/median toggle flips the displayed `avg` value.

**Files:**
- Modify: `backend/routes/backtest.py` (compute `gain_stats`, `loss_stats`, `pnl_distribution` in summary)
- Create: `backend/tests/test_backtest_stats.py` (unit test for the stats helper)
- Modify: `frontend/src/shared/types/index.ts` (extend `BacktestResult`)
- Create: `frontend/src/features/strategy/PnlHistogram.tsx` (tiny SVG histogram component)
- Modify: `frontend/src/features/strategy/Results.tsx` (render block in the summary tab)

- [ ] **Step 1: Backend — extract a stats helper**

Edit `backend/routes/backtest.py`. At module level (above `@router.post("/api/backtest")`), add a small helper:

```python
def _side_stats(values: list[float]) -> dict:
    """Min/max/mean/median for a list of floats. Empty list → all None."""
    if not values:
        return {"min": None, "max": None, "mean": None, "median": None}
    import statistics
    return {
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "mean": round(sum(values) / len(values), 2),
        "median": round(statistics.median(values), 2),
    }
```

- [ ] **Step 2: Backend — call the helper in `run_backtest` and return new fields**

Still in `backend/routes/backtest.py`, inside `run_backtest`, just after the existing `win_rate = ...` line (~line 259), compute the distributions:

```python
        gains = [float(t["pnl"]) for t in sell_trades if t.get("pnl", 0) > 0]
        losses = [float(t["pnl"]) for t in sell_trades if t.get("pnl", 0) < 0]
        gain_stats = _side_stats(gains)
        loss_stats = _side_stats(losses)
        pnl_distribution = [round(float(t.get("pnl", 0)), 2) for t in sell_trades]
```

Then extend the `result["summary"]` dict with these three new keys. Replace the summary dict build:

```python
        result = {
            "summary": {
                "initial_capital": req.initial_capital,
                "final_value": round(final_value, 2),
                "total_return_pct": round(total_return, 2),
                "buy_hold_return_pct": round(buy_hold_return, 2),
                "num_trades": len(sell_trades),
                "win_rate_pct": round(win_rate, 2),
                "sharpe_ratio": round(sharpe, 3),
                "max_drawdown_pct": round(max_drawdown, 2),
                "gain_stats": gain_stats,
                "loss_stats": loss_stats,
                "pnl_distribution": pnl_distribution,
            },
            "trades": trades,
            "equity_curve": equity,
            "baseline_curve": baseline_curve,
        }
```

- [ ] **Step 3: Backend — unit test for `_side_stats`**

Create `backend/tests/test_backtest_stats.py`:

```python
from routes.backtest import _side_stats


def test_side_stats_empty():
    result = _side_stats([])
    assert result == {"min": None, "max": None, "mean": None, "median": None}


def test_side_stats_single_value():
    result = _side_stats([42.0])
    assert result == {"min": 42.0, "max": 42.0, "mean": 42.0, "median": 42.0}


def test_side_stats_multiple():
    # mean and median diverge when there's an outlier
    result = _side_stats([10.0, 20.0, 30.0, 40.0, 1000.0])
    assert result["min"] == 10.0
    assert result["max"] == 1000.0
    assert result["mean"] == 220.0   # sum / 5
    assert result["median"] == 30.0  # middle value


def test_side_stats_negatives():
    result = _side_stats([-50.0, -30.0, -10.0])
    assert result["min"] == -50.0
    assert result["max"] == -10.0
    assert result["mean"] == -30.0
    assert result["median"] == -30.0
```

Run it:

```bash
cd /Users/jroxenhed/Documents/strategylab/backend
python -m pytest tests/test_backtest_stats.py -v
```

Expected: 4 tests pass.

- [ ] **Step 4: Frontend types — extend `BacktestResult['summary']`**

Edit `frontend/src/shared/types/index.ts`. Extend the summary object in `BacktestResult`:

```ts
export interface BacktestResult {
  summary: {
    initial_capital: number
    final_value: number
    total_return_pct: number
    buy_hold_return_pct: number
    num_trades: number
    win_rate_pct: number
    sharpe_ratio: number
    max_drawdown_pct: number
    gain_stats?: SideStats
    loss_stats?: SideStats
    pnl_distribution?: number[]
  }
  trades: Trade[]
  equity_curve: TimeValue[]
  baseline_curve?: TimeValue[]
  ema_overlays?: EMAOverlay[]
  signal_trace?: SignalTraceEntry[]
}

export interface SideStats {
  min: number | null
  max: number | null
  mean: number | null
  median: number | null
}
```

- [ ] **Step 5: Create the histogram component**

Create `frontend/src/features/strategy/PnlHistogram.tsx`:

```tsx
interface Props {
  values: number[]
  width?: number
  height?: number
}

export default function PnlHistogram({ values, width = 220, height = 60 }: Props) {
  if (values.length === 0) {
    return <div style={{ width, height, color: '#484f58', fontSize: 10, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>no trades</div>
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const bucketCount = Math.min(20, Math.max(5, Math.floor(Math.sqrt(values.length))))
  const bucketSize = range / bucketCount
  const buckets = new Array(bucketCount).fill(0)
  for (const v of values) {
    const idx = Math.min(bucketCount - 1, Math.floor((v - min) / bucketSize))
    buckets[idx]++
  }
  const tallest = Math.max(...buckets) || 1
  const barWidth = width / bucketCount
  // Zero line position (if 0 is inside [min, max])
  const zeroX = min < 0 && max > 0 ? ((0 - min) / range) * width : null

  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      {buckets.map((count, i) => {
        const bucketStart = min + i * bucketSize
        const bucketEnd = bucketStart + bucketSize
        const isLoss = bucketEnd <= 0
        const isGain = bucketStart >= 0
        const color = isLoss ? '#f85149' : isGain ? '#26a641' : '#8b949e'
        const h = (count / tallest) * (height - 4)
        return (
          <rect
            key={i}
            x={i * barWidth + 0.5}
            y={height - h}
            width={Math.max(1, barWidth - 1)}
            height={h}
            fill={color}
            opacity={0.85}
          />
        )
      })}
      {zeroX != null && (
        <line x1={zeroX} y1={0} x2={zeroX} y2={height} stroke="#30363d" strokeWidth={1} strokeDasharray="2,2" />
      )}
    </svg>
  )
}
```

- [ ] **Step 6: Render stats block + histogram in `Results.tsx`**

Edit `frontend/src/features/strategy/Results.tsx`. Two changes:

(a) Add the import and a mean/median state near the top:

```tsx
import PnlHistogram from './PnlHistogram'
// ...
const [avgMode, setAvgMode] = useState<'mean' | 'median'>('mean')
```

(b) Add a stats block below the existing `metricsGrid` in the `summary` tab render. Replace the summary tab block with:

```tsx
{activeTab === 'summary' && (
  <div style={{ display: 'flex', flexDirection: 'column' }}>
    <div style={styles.metricsGrid}>
      {[
        { label: 'Return', value: `${summary.total_return_pct > 0 ? '+' : ''}${summary.total_return_pct}%`, color: summary.total_return_pct >= 0 ? '#26a641' : '#f85149' },
        { label: 'B&H Return', value: `${summary.buy_hold_return_pct > 0 ? '+' : ''}${summary.buy_hold_return_pct}%`, color: '#8b949e' },
        { label: 'Final Value', value: `$${summary.final_value.toLocaleString()}`, color: '#e6edf3' },
        { label: 'Trades', value: summary.num_trades, color: '#e6edf3' },
        { label: 'Win Rate', value: `${summary.win_rate_pct}%`, color: summary.win_rate_pct >= 50 ? '#26a641' : '#f85149' },
        { label: 'Sharpe', value: summary.sharpe_ratio, color: summary.sharpe_ratio >= 1 ? '#26a641' : '#8b949e' },
        { label: 'Max DD', value: `${summary.max_drawdown_pct}%`, color: '#f85149' },
      ].map(({ label, value, color }) => (
        <div key={label} style={styles.metric}>
          <div style={{ fontSize: 10, color: '#8b949e', marginBottom: 2 }}>{label}</div>
          <div style={{ fontSize: 16, fontWeight: 700, color }}>{value}</div>
        </div>
      ))}
    </div>
    {(summary.gain_stats || summary.loss_stats) && (
      <div style={{ display: 'flex', gap: 24, padding: '12px 16px', borderTop: '1px solid #21262d', alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 180 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>P&amp;L Distribution</span>
            <div style={{ display: 'flex', gap: 2, background: '#0d1117', border: '1px solid #21262d', borderRadius: 3, padding: 1 }}>
              {(['mean', 'median'] as const).map(m => (
                <button
                  key={m}
                  onClick={() => setAvgMode(m)}
                  style={{
                    fontSize: 9, padding: '1px 6px', border: 'none', cursor: 'pointer', borderRadius: 2,
                    background: avgMode === m ? '#1e3a5f' : 'transparent',
                    color: avgMode === m ? '#e6edf3' : '#8b949e',
                  }}
                >{m}</button>
              ))}
            </div>
          </div>
          <StatRow label="Max gain" value={summary.gain_stats?.max} color="#26a641" />
          <StatRow label={`Avg gain (${avgMode})`} value={summary.gain_stats?.[avgMode]} color="#26a641" />
          <StatRow label="Min gain" value={summary.gain_stats?.min} color="#26a641" />
          <StatRow label="Max loss" value={summary.loss_stats?.min} color="#f85149" />
          <StatRow label={`Avg loss (${avgMode})`} value={summary.loss_stats?.[avgMode]} color="#f85149" />
          <StatRow label="Min loss" value={summary.loss_stats?.max} color="#f85149" />
        </div>
        <PnlHistogram values={summary.pnl_distribution ?? []} />
      </div>
    )}
  </div>
)}
```

And add the `StatRow` helper at the bottom of the file, just before the `styles` object:

```tsx
function StatRow({ label, value, color }: { label: string; value: number | null | undefined; color: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
      <span style={{ color: '#8b949e' }}>{label}</span>
      <span style={{ color: value == null ? '#484f58' : color, fontFamily: 'monospace' }}>
        {value == null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}`}
      </span>
    </div>
  )
}
```

Note the intentional swap in the Max/Min loss rows: for losses (negative numbers), the "worst" loss is `loss_stats.min` (most negative) and "smallest" loss is `loss_stats.max` (closest to zero). Labels are user-facing ("Max loss" = the biggest loss you took), so they map to the mathematically-opposite fields.

- [ ] **Step 7: Verify**

1. Run a backtest with at least a handful of gains and losses.
2. Open the Summary tab — stats block appears below the existing metrics grid.
3. Mean/median toggle changes the `Avg gain` and `Avg loss` rows.
4. Histogram shows red bars on the left, green on the right, dashed grey zero line between them.
5. Run a backtest with zero trades — stats block is hidden (the outer `{(summary.gain_stats || summary.loss_stats) && ...}`).
6. Run a backtest with only gains (no losses) — loss rows show `—`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat: P&L distribution stats + histogram on summary tab

Adds min/max/avg per trade side (gains/losses) plus a small SVG
histogram to the Summary tab. Mean/median toggle flips the avg
display. Makes it easy to spot "one lucky trade carried the strategy"
patterns vs consistent small wins.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] **Step 1: Full manual smoke test**

1. `./start.sh`
2. All three tabs (Chart, Paper Trading, Discovery) load without console errors.
3. Run a backtest — summary shows distribution block, equity tab has baseline toggle working.
4. Paper Trading: add a couple of bots, Start All / Stop All / sparkline toggle work, Stop and Close confirms.
5. Discovery: SignalScanner and PerformanceComparison render.

- [ ] **Step 2: Run backend tests**

```bash
cd /Users/jroxenhed/Documents/strategylab/backend
python -m pytest -v
```

Expected: existing tests still pass + new `test_backtest_stats.py` passes.

- [ ] **Step 3: Push**

```bash
git push origin main
```

- [ ] **Step 4: Update TODO.md**

Check off Group A items and the Group C item in `TODO.md`. The file already has the section from this session's earlier edit — just flip the `[ ]` to `[x]` for:

- Bot sparkline local/aligned toggle
- Global start/stop all bots
- Strategy summary min/max/avg gain/loss
- Backtest equity curve baseline overlay
- Clean up bot page, move signal scanner to new Discovery page

Commit that edit as `docs: check off Group A + C items post-implementation`.

# StrategyLab Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four issues: indicator pane sync, strategy math (position size UX + NaN validation), tabbed results panel, and buy/sell markers in overlay mode.

**Architecture:** Backend gets a Pydantic validator to clamp `position_size`. Frontend gets three targeted component edits: `StrategyBuilder` (input relabeling + pre-flight validation), `Results` (tabbed layout), and `Chart` (time scale sync wired inside the existing main chart effect, plus markers in overlay mode).

**Tech Stack:** Python/FastAPI/Pydantic (backend), React/TypeScript/lightweight-charts v5 (frontend)

---

## Files Changed

| File | What changes |
|------|-------------|
| `backend/main.py` | Add `field_validator` to `StrategyRequest` to clamp `position_size` to [0.01, 1.0] |
| `backend/requirements.txt` | Add `pytest`, `pytest-asyncio` |
| `backend/tests/__init__.py` | New (empty) |
| `backend/tests/test_models.py` | New — unit tests for position_size clamping |
| `frontend/src/components/StrategyBuilder.tsx` | % of Capital input (1–100), divide by 100 before send, NaN validation |
| `frontend/src/components/Results.tsx` | Replace fixed layout with 3-tab panel (Summary / Equity Curve / Trades) |
| `frontend/src/components/Chart.tsx` | Indicator pane sync via `subscribeVisibleLogicalRangeChange`; markers in overlay mode |

---

## Task 1: Backend — position_size Pydantic validator

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/requirements.txt`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/test_models.py`

- [ ] **Step 1: Add pytest to requirements**

Open `backend/requirements.txt` and append two lines:

```
pytest
httpx
```

(httpx is already there — just add pytest)

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/__init__.py` (empty file).

Create `backend/tests/test_models.py`:

```python
import pytest
from pydantic import ValidationError
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))
from main import StrategyRequest


def make_req(**kwargs):
    defaults = dict(
        ticker="AAPL",
        buy_rules=[],
        sell_rules=[],
    )
    return StrategyRequest(**{**defaults, **kwargs})


def test_position_size_clamps_large_value():
    req = make_req(position_size=10000)
    assert req.position_size == 1.0


def test_position_size_clamps_above_one():
    req = make_req(position_size=1.5)
    assert req.position_size == 1.0


def test_position_size_clamps_negative():
    req = make_req(position_size=-1)
    assert req.position_size == 0.01


def test_position_size_clamps_zero():
    req = make_req(position_size=0)
    assert req.position_size == 0.01


def test_position_size_accepts_one():
    req = make_req(position_size=1.0)
    assert req.position_size == 1.0


def test_position_size_accepts_fraction():
    req = make_req(position_size=0.5)
    assert req.position_size == 0.5


def test_position_size_default_is_one():
    req = make_req()
    assert req.position_size == 1.0
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/john/test-claude-project/backend
source venv/bin/activate
pip install pytest -q
pytest tests/test_models.py -v
```

Expected: all 7 tests FAIL with something like `assert 10000 == 1.0`

- [ ] **Step 4: Add the validator to main.py**

In `backend/main.py`, add `field_validator` to the import and add the validator to `StrategyRequest`:

Change line 3 from:
```python
from pydantic import BaseModel
```
to:
```python
from pydantic import BaseModel, field_validator
```

Replace the `StrategyRequest` class (lines 148–158) with:

```python
class StrategyRequest(BaseModel):
    ticker: str
    start: str = "2023-01-01"
    end: str = "2024-01-01"
    interval: str = "1d"
    buy_rules: list[Rule]
    sell_rules: list[Rule]
    buy_logic: str = "AND"   # AND | OR
    sell_logic: str = "AND"
    initial_capital: float = 10000.0
    position_size: float = 1.0   # fraction of capital per trade (0.01–1.0)

    @field_validator('position_size')
    @classmethod
    def clamp_position_size(cls, v: float) -> float:
        return max(0.01, min(1.0, v))
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/john/test-claude-project/backend
source venv/bin/activate
pytest tests/test_models.py -v
```

Expected output:
```
tests/test_models.py::test_position_size_clamps_large_value PASSED
tests/test_models.py::test_position_size_clamps_above_one PASSED
tests/test_models.py::test_position_size_clamps_negative PASSED
tests/test_models.py::test_position_size_clamps_zero PASSED
tests/test_models.py::test_position_size_accepts_one PASSED
tests/test_models.py::test_position_size_accepts_fraction PASSED
tests/test_models.py::test_position_size_default_is_one PASSED
7 passed
```

- [ ] **Step 6: Commit**

```bash
cd /home/john/test-claude-project
git add backend/main.py backend/requirements.txt backend/tests/
git commit -m "fix: clamp position_size to [0.01, 1.0] via Pydantic validator"
```

---

## Task 2: StrategyBuilder — % of Capital input + NaN validation

**Files:**
- Modify: `frontend/src/components/StrategyBuilder.tsx`

- [ ] **Step 1: Change posSize state default and add validateRules**

In `StrategyBuilder.tsx`, make these changes:

Change line 70:
```typescript
const [posSize, setPosSize] = useState(1.0)
```
to:
```typescript
const [posSize, setPosSize] = useState(100)
```

Add `validateRules` function directly above the `runBacktest` function (after the state declarations):

```typescript
function validateRules(rules: Rule[], label: string): string | null {
  for (const rule of rules) {
    const needsValue = NEEDS_VALUE.includes(rule.condition) && !NEEDS_PARAM[rule.indicator]?.includes(rule.condition)
    if (needsValue && (rule.value === undefined || rule.value === null || isNaN(rule.value as number))) {
      return `${label} rule "${rule.indicator.toUpperCase()} ${CONDITION_LABELS[rule.condition]}" is missing a value`
    }
  }
  return null
}
```

- [ ] **Step 2: Update runBacktest to validate and divide posSize**

Replace the `runBacktest` function (lines 74–92) with:

```typescript
async function runBacktest() {
  setLoading(true)
  setError('')
  onResult(null)

  const validationError = validateRules(buyRules, 'BUY') || validateRules(sellRules, 'SELL')
  if (validationError) {
    setError(validationError)
    setLoading(false)
    return
  }

  try {
    const req: StrategyRequest = {
      ticker, start, end, interval,
      buy_rules: buyRules, sell_rules: sellRules,
      buy_logic: buyLogic, sell_logic: sellLogic,
      initial_capital: capital, position_size: posSize / 100,
    }
    const { data } = await axios.post('http://localhost:8000/api/backtest', req)
    onResult(data)
  } catch (e: any) {
    setError(e.response?.data?.detail || 'Backtest failed')
  } finally {
    setLoading(false)
  }
}
```

- [ ] **Step 3: Update the position size input label and range**

Replace the position size settings row (lines 140–143):
```tsx
<div style={styles.settingsRow}>
  <label style={styles.settingsLabel}>Position size</label>
  <input type="number" value={posSize} step={0.1} min={0.1} max={1} onChange={e => setPosSize(+e.target.value)} style={styles.settingsInput} />
</div>
```
with:
```tsx
<div style={styles.settingsRow}>
  <label style={styles.settingsLabel}>% of Capital</label>
  <input type="number" value={posSize} step={1} min={1} max={100} onChange={e => setPosSize(+e.target.value)} style={styles.settingsInput} />
</div>
```

- [ ] **Step 4: Verify manually**

Start the app: `./start.sh`

1. Open http://localhost:5173
2. Confirm "Position size" label now reads "% of Capital" and shows `100`
3. Set RSI buy rule but clear the value field → click Run Backtest → should see inline error like `BUY rule "RSI Is below" is missing a value` and no request sent
4. Set RSI buy rule with value `40`, sell rule with value `60`, click Run Backtest → should succeed (no -38978% result)

- [ ] **Step 5: Commit**

```bash
cd /home/john/test-claude-project
git add frontend/src/components/StrategyBuilder.tsx
git commit -m "fix: % of Capital input and NaN rule validation in StrategyBuilder"
```

---

## Task 3: Results.tsx — Tabbed Panel

**Files:**
- Modify: `frontend/src/components/Results.tsx`

- [ ] **Step 1: Replace Results.tsx entirely**

Replace the full contents of `frontend/src/components/Results.tsx` with:

```tsx
import { useState, useEffect, useRef } from 'react'
import { createChart, LineSeries, ColorType } from 'lightweight-charts'
import type { BacktestResult } from '../types'

type Tab = 'summary' | 'equity' | 'trades'

interface Props {
  result: BacktestResult
}

export default function Results({ result }: Props) {
  const { summary, trades, equity_curve } = result
  const [activeTab, setActiveTab] = useState<Tab>('summary')
  const chartRef = useRef<HTMLDivElement>(null)
  const sells = trades.filter(t => t.type === 'sell')

  useEffect(() => {
    if (activeTab !== 'equity' || !chartRef.current || equity_curve.length === 0) return
    const chart = createChart(chartRef.current, {
      height: chartRef.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: '#0d1117' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#1c2128' }, horzLines: { color: '#1c2128' } },
      timeScale: { borderColor: '#30363d' },
      rightPriceScale: { borderColor: '#30363d' },
    })
    const series = chart.addSeries(LineSeries, {
      color: summary.total_return_pct >= 0 ? '#26a641' : '#f85149',
      lineWidth: 2,
    })
    series.setData(
      equity_curve
        .filter(d => d.value !== null)
        .map(d => ({ time: d.time as any, value: d.value as number }))
    )
    chart.timeScale().fitContent()
    const ro = new ResizeObserver(() => {
      if (chartRef.current) chart.applyOptions({ width: chartRef.current.clientWidth })
    })
    ro.observe(chartRef.current)
    return () => { chart.remove(); ro.disconnect() }
  }, [activeTab, equity_curve, summary.total_return_pct])

  return (
    <div style={styles.container}>
      <div style={styles.tabBar}>
        {(['summary', 'equity', 'trades'] as Tab[]).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{ ...styles.tab, ...(activeTab === tab ? styles.tabActive : {}) }}
          >
            {tab === 'summary' ? 'Summary' : tab === 'equity' ? 'Equity Curve' : `Trades (${sells.length})`}
          </button>
        ))}
      </div>

      {activeTab === 'summary' && (
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
      )}

      {activeTab === 'equity' && (
        <div ref={chartRef} style={{ flex: 1, width: '100%' }} />
      )}

      {activeTab === 'trades' && (
        <div style={styles.tradeList}>
          {sells.length === 0 ? (
            <div style={{ color: '#8b949e', fontSize: 12, padding: 8 }}>No completed trades</div>
          ) : (
            sells.map((t, i) => (
              <div key={i} style={styles.tradeRow}>
                <span style={{ color: '#8b949e', fontSize: 11, width: 80 }}>{t.date}</span>
                <span style={{ color: (t.pnl ?? 0) >= 0 ? '#26a641' : '#f85149', fontSize: 12, width: 60 }}>
                  {(t.pnl ?? 0) >= 0 ? '+' : ''}{t.pnl?.toFixed(2)}
                </span>
                <span style={{ color: '#8b949e', fontSize: 11 }}>{t.pnl_pct?.toFixed(1)}%</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex', flexDirection: 'column',
    background: '#161b22', borderTop: '1px solid #30363d',
    height: 220, flexShrink: 0,
  },
  tabBar: { display: 'flex', borderBottom: '1px solid #30363d', flexShrink: 0 },
  tab: {
    padding: '6px 14px', fontSize: 12, color: '#8b949e',
    background: 'none', border: 'none', borderBottom: '2px solid transparent', cursor: 'pointer',
  },
  tabActive: { color: '#58a6ff', borderBottomColor: '#58a6ff' },
  metricsGrid: { display: 'flex', flexWrap: 'wrap', padding: '12px 16px', gap: 0, alignContent: 'flex-start' },
  metric: { padding: '6px 20px 6px 0', minWidth: 110 },
  tradeList: { flex: 1, overflowY: 'auto', padding: '8px 12px' },
  tradeRow: { display: 'flex', gap: 8, alignItems: 'center', padding: '3px 0', borderBottom: '1px solid #21262d' },
}
```

- [ ] **Step 2: Verify manually**

With the app running:
1. Run a backtest (RSI < 40 buy, RSI > 60 sell on AAPL, 1y of daily data)
2. Confirm results panel appears with 3 tabs: Summary, Equity Curve, Trades (N)
3. Summary tab: 7 metrics visible, no cramping
4. Equity Curve tab: chart renders at full panel height (~190px usable)
5. Trades tab: scrollable list of sell trades with date, P&L, P&L%

- [ ] **Step 3: Commit**

```bash
cd /home/john/test-claude-project
git add frontend/src/components/Results.tsx
git commit -m "feat: replace results panel with tabbed Summary/Equity Curve/Trades layout"
```

---

## Task 4: Chart.tsx — Indicator Sync + Overlay Markers

**Files:**
- Modify: `frontend/src/components/Chart.tsx`

- [ ] **Step 1: Add LogicalRange type import**

In `Chart.tsx`, change line 10:
```typescript
import type { IChartApi } from 'lightweight-charts'
```
to:
```typescript
import type { IChartApi, LogicalRange } from 'lightweight-charts'
```

- [ ] **Step 2: Add time scale sync inside the main chart useEffect**

In the main chart `useEffect` (starts at line 73), add the sync subscription after `chart.timeScale().fitContent()` and before the `ResizeObserver` setup:

Find this block (around line 130):
```typescript
    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth })
    })
```

Replace it with:
```typescript
    chart.timeScale().fitContent()

    const syncHandler = (range: LogicalRange | null) => {
      if (!range) return
      if (macdChartRef.current) macdChartRef.current.timeScale().setVisibleLogicalRange(range)
      if (rsiChartRef.current) rsiChartRef.current.timeScale().setVisibleLogicalRange(range)
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(syncHandler)

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth })
    })
```

Also update the cleanup function at the bottom of the main chart effect from:
```typescript
    return () => { chart.remove(); ro.disconnect() }
```
to:
```typescript
    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(syncHandler)
      chart.remove()
      ro.disconnect()
    }
```

- [ ] **Step 3: Add null-clearing to MACD and RSI chart cleanup**

In the MACD chart `useEffect` (around line 164), update the cleanup:
```typescript
    return () => { chart.remove(); ro.disconnect() }
```
to:
```typescript
    return () => { chart.remove(); macdChartRef.current = null; ro.disconnect() }
```

In the RSI chart `useEffect` (around line 189), update the cleanup:
```typescript
    return () => { chart.remove(); ro.disconnect() }
```
to:
```typescript
    return () => { chart.remove(); rsiChartRef.current = null; ro.disconnect() }
```

- [ ] **Step 4: Add markers in overlay mode**

In the main chart `useEffect`, find the `if (showOverlay)` block (around lines 81–93). Add the markers block at the end of that branch, just before the closing `}` of `if (showOverlay)`:

Current end of the overlay block:
```typescript
      if (showQqq && normalizedQqq.length > 0) {
        const qqqSeries = chart.addSeries(LineSeries, { color: '#a371f7', lineWidth: 1, title: 'QQQ' })
        qqqSeries.setData(normalizedQqq)
      }
    } else {
```

Replace with:
```typescript
      if (showQqq && normalizedQqq.length > 0) {
        const qqqSeries = chart.addSeries(LineSeries, { color: '#a371f7', lineWidth: 1, title: 'QQQ' })
        qqqSeries.setData(normalizedQqq)
      }

      if (trades && trades.length > 0) {
        const markers = trades.map(t => ({
          time: t.date as any,
          position: t.type === 'buy' ? 'belowBar' as const : 'aboveBar' as const,
          color: t.type === 'buy' ? UP : DOWN,
          shape: t.type === 'buy' ? 'arrowUp' as const : 'arrowDown' as const,
          text: t.type === 'buy' ? `B $${t.price}` : `S $${t.price}`,
        }))
        createSeriesMarkers(mainSeries, markers)
      }
    } else {
```

- [ ] **Step 5: Verify manually**

With the app running:

**Indicator sync:**
1. Enable MACD and RSI indicators
2. Scroll the main candlestick chart left/right → MACD and RSI panes should scroll in lockstep
3. Zoom the main chart (scroll wheel) → MACD and RSI should zoom to the same range

**Overlay markers:**
1. Run a backtest to get trades
2. Toggle on SPY comparison → chart switches to % change line mode
3. Buy/sell arrows should still appear on the main (blue) line

- [ ] **Step 6: Commit**

```bash
cd /home/john/test-claude-project
git add frontend/src/components/Chart.tsx
git commit -m "fix: sync indicator panes with main chart; show trade markers in overlay mode"
```

---

## Task 5: Update memory

- [ ] **Update open issues memory** to reflect these 4 issues are resolved, and carry forward the remaining known issues (SPY/QQQ overlay replaces candlesticks, volume checkbox, intraday timeframes).

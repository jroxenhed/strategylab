# Indicator System Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded indicator system (flat checkbox list, monolithic backend, per-indicator useEffects) with a unified instance-based model supporting customizable params, multiple instances of the same type, and forward-compatibility with B4 (per-rule signal visualization).

**Architecture:** New `IndicatorInstance` data model with unique IDs and typed params replaces `IndicatorKey` union type. Backend gains a registry of per-type compute functions and a POST endpoint keyed by instance ID. Frontend replaces scattered state with a single `indicators: IndicatorInstance[]` array, a new sidebar UI with inline-expand settings per indicator, and a generic `<SubPane>` component that replaces the hardcoded MACD/RSI pane effects in Chart.tsx. Same-type sub-pane sharing (e.g. RSI(14) + RSI(2) in one pane) is built as a general capability controlled by a per-type `subPaneSharing` flag.

**Tech Stack:** React + TypeScript (frontend), Python/FastAPI (backend), lightweight-charts v5, TanStack Query, existing CSS variables.

**Spec:** [`docs/superpowers/specs/2026-04-20-indicator-system-redesign-design.md`](../specs/2026-04-20-indicator-system-redesign-design.md)

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `frontend/src/shared/types/indicators.ts` | `IndicatorType`, `IndicatorInstance`, `IndicatorTypeDef`, type defaults, ID generator |
| `frontend/src/features/sidebar/IndicatorList.tsx` | Indicator section UI: active list, add dropdown, inline-expand settings rows |
| `frontend/src/features/chart/SubPane.tsx` | Generic sub-pane component replacing hardcoded MACD/RSI pane effects |
| `backend/indicators.py` | Indicator registry: per-type compute functions + `compute_instance()` dispatcher |

### Modified files

| File | What changes |
|------|-------------|
| `frontend/src/shared/types/index.ts` | Remove `IndicatorKey`, `IndicatorData`, `MACDData`, `BBData`, `EMAData`, `MAData`; re-export from `indicators.ts`; add `indicators` field to `SavedStrategy` |
| `frontend/src/shared/hooks/useOHLCV.ts` | Replace `useIndicators` GET hook with POST-based `useInstanceIndicators` |
| `frontend/src/App.tsx` | Replace `activeIndicators`/`maSettings` state with `indicators: IndicatorInstance[]`; update localStorage persistence; update Sidebar/Chart props |
| `frontend/src/features/sidebar/Sidebar.tsx` | Remove `ALL_INDICATORS` checkbox list + MA settings; render `<IndicatorList>` instead |
| `frontend/src/features/chart/Chart.tsx` | Replace per-indicator overlay effects (EMA, BB, MA, Volume) with generic loop; replace MACD/RSI pane creation with `<SubPane>` components; update props |
| `frontend/src/features/strategy/StrategyBuilder.tsx` | Add migration for old `SavedStrategy` format on load |
| `backend/routes/indicators.py` | Add POST endpoint using registry; keep GET during transition |
| `backend/signal_engine.py` | No changes (rule engine stays self-contained) |

---

## Tasks

### Task 1: Indicator types and data model

**Files:**
- Create: `frontend/src/shared/types/indicators.ts`
- Modify: `frontend/src/shared/types/index.ts`

- [ ] **Step 1: Create the indicator types file**

Create `frontend/src/shared/types/indicators.ts` with the core data model:

```typescript
export type IndicatorType = 'rsi' | 'macd' | 'ema' | 'bb' | 'atr' | 'ma' | 'volume'

export type IndicatorInstance = {
  id: string
  type: IndicatorType
  params: Record<string, number | string>
  enabled: boolean
  color?: string
  pane: 'main' | 'sub'
}

export type IndicatorTypeDef = {
  type: IndicatorType
  label: string
  defaultParams: Record<string, number | string>
  pane: 'main' | 'sub'
  paramFields: { key: string; label: string; min?: number; max?: number }[]
  subPaneSharing: 'shared' | 'isolated'
}

let _nextId = 1

export function generateInstanceId(type: IndicatorType): string {
  return `${type}-${_nextId++}`
}

export function createInstance(type: IndicatorType, overrides?: Partial<IndicatorInstance>): IndicatorInstance {
  const def = INDICATOR_DEFS[type]
  return {
    id: generateInstanceId(type),
    type,
    params: { ...def.defaultParams },
    enabled: true,
    pane: def.pane,
    ...overrides,
  }
}

export const INDICATOR_DEFS: Record<IndicatorType, IndicatorTypeDef> = {
  rsi: {
    type: 'rsi', label: 'RSI',
    defaultParams: { period: 14 },
    pane: 'sub',
    paramFields: [{ key: 'period', label: 'Period', min: 2 }],
    subPaneSharing: 'shared',
  },
  macd: {
    type: 'macd', label: 'MACD',
    defaultParams: { fast: 12, slow: 26, signal: 9 },
    pane: 'sub',
    paramFields: [
      { key: 'fast', label: 'Fast', min: 2 },
      { key: 'slow', label: 'Slow', min: 2 },
      { key: 'signal', label: 'Signal', min: 2 },
    ],
    subPaneSharing: 'isolated',
  },
  ema: {
    type: 'ema', label: 'EMA',
    defaultParams: { period: 20 },
    pane: 'main',
    paramFields: [{ key: 'period', label: 'Period', min: 2 }],
    subPaneSharing: 'shared',
  },
  bb: {
    type: 'bb', label: 'Bollinger Bands',
    defaultParams: { period: 20, stddev: 2 },
    pane: 'main',
    paramFields: [
      { key: 'period', label: 'Period', min: 2 },
      { key: 'stddev', label: 'Std Dev', min: 0.5, max: 5 },
    ],
    subPaneSharing: 'shared',
  },
  atr: {
    type: 'atr', label: 'ATR',
    defaultParams: { period: 14 },
    pane: 'sub',
    paramFields: [{ key: 'period', label: 'Period', min: 2 }],
    subPaneSharing: 'shared',
  },
  ma: {
    type: 'ma', label: 'MA',
    defaultParams: { period: 8, type: 'ema' },
    pane: 'main',
    paramFields: [
      { key: 'period', label: 'Period', min: 2 },
      { key: 'type', label: 'Type', min: 0, max: 0 },
    ],
    subPaneSharing: 'shared',
  },
  volume: {
    type: 'volume', label: 'Volume',
    defaultParams: {},
    pane: 'main',
    paramFields: [],
    subPaneSharing: 'shared',
  },
}

export function paramSummary(inst: IndicatorInstance): string {
  const def = INDICATOR_DEFS[inst.type]
  if (def.paramFields.length === 0) return ''
  return def.paramFields.map(f => inst.params[f.key]).join(',')
}

export const DEFAULT_INDICATORS: IndicatorInstance[] = [
  { id: 'macd-default', type: 'macd', params: { fast: 12, slow: 26, signal: 9 }, enabled: true, pane: 'sub' },
  { id: 'rsi-default', type: 'rsi', params: { period: 14 }, enabled: true, pane: 'sub' },
]
```

- [ ] **Step 2: Update `index.ts` to re-export new types**

In `frontend/src/shared/types/index.ts`, add the re-export at the top (after existing imports):

```typescript
export type { IndicatorType, IndicatorInstance, IndicatorTypeDef } from './indicators'
export { INDICATOR_DEFS, DEFAULT_INDICATORS, createInstance, paramSummary } from './indicators'
```

Keep the old types (`IndicatorKey`, `IndicatorData`, `MACDData`, `BBData`, `EMAData`, `MAData`) for now — they're still referenced by Chart.tsx and the GET hook. They'll be removed in later tasks when their consumers are migrated.

- [ ] **Step 3: Add `indicators` field to `SavedStrategy`**

In `frontend/src/shared/types/index.ts`, add the optional field to the `SavedStrategy` interface:

```typescript
// Add after the existing borrowRateAnnual field:
  indicators?: IndicatorInstance[]
```

This is optional so old saved strategies still parse. Migration logic comes in Task 7.

- [ ] **Step 4: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/shared/types/indicators.ts frontend/src/shared/types/index.ts
git commit -m "feat(A4): add IndicatorInstance data model and type definitions"
```

---

### Task 2: Backend indicator registry and POST endpoint

**Files:**
- Create: `backend/indicators.py`
- Modify: `backend/routes/indicators.py`
- Modify: `backend/main.py` (if routes need re-registration — check first)

- [ ] **Step 1: Create the indicator registry**

Create `backend/indicators.py` with per-type compute functions extracted from the current monolithic code in `routes/indicators.py` and `signal_engine.py`:

```python
import numpy as np
import pandas as pd


def compute_rsi(close: pd.Series, params: dict) -> dict[str, pd.Series]:
    period = int(params.get("period", 14))
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return {"rsi": rsi}


def compute_macd(close: pd.Series, params: dict) -> dict[str, pd.Series]:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    signal_period = int(params.get("signal", 9))
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def compute_ema(close: pd.Series, params: dict) -> dict[str, pd.Series]:
    period = int(params.get("period", 20))
    return {"ema": close.ewm(span=period, adjust=False).mean()}


def compute_bb(close: pd.Series, params: dict) -> dict[str, pd.Series]:
    period = int(params.get("period", 20))
    stddev = float(params.get("stddev", 2))
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    return {
        "upper": sma + stddev * std,
        "middle": sma,
        "lower": sma - stddev * std,
    }


def compute_atr(close: pd.Series, params: dict, high: pd.Series = None, low: pd.Series = None) -> dict[str, pd.Series]:
    period = int(params.get("period", 14))
    if high is None or low is None:
        return {"atr": pd.Series(np.nan, index=close.index)}
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return {"atr": tr.rolling(period).mean()}


def compute_ma(close: pd.Series, params: dict) -> dict[str, pd.Series]:
    period = int(params.get("period", 8))
    ma_type = str(params.get("type", "ema")).lower()
    if ma_type == "sma":
        ma = close.rolling(period).mean()
    elif ma_type == "rma":
        ma = close.ewm(alpha=1 / period, adjust=False).mean()
    else:
        ma = close.ewm(span=period, adjust=False).mean()
    return {"ma": ma}


def compute_volume(close: pd.Series, params: dict, volume: pd.Series = None) -> dict[str, pd.Series]:
    if volume is None:
        return {"volume": pd.Series(np.nan, index=close.index)}
    return {"volume": volume}


INDICATOR_REGISTRY: dict[str, callable] = {
    "rsi": compute_rsi,
    "macd": compute_macd,
    "ema": compute_ema,
    "bb": compute_bb,
    "atr": compute_atr,
    "ma": compute_ma,
    "volume": compute_volume,
}


def compute_instance(
    indicator_type: str,
    params: dict,
    close: pd.Series,
    high: pd.Series = None,
    low: pd.Series = None,
    volume: pd.Series = None,
) -> dict[str, pd.Series]:
    fn = INDICATOR_REGISTRY.get(indicator_type)
    if not fn:
        raise ValueError(f"Unknown indicator type: {indicator_type}")
    # ATR and volume need extra series
    if indicator_type == "atr":
        return fn(close, params, high=high, low=low)
    if indicator_type == "volume":
        return fn(close, params, volume=volume)
    return fn(close, params)
```

- [ ] **Step 2: Add POST endpoint to `routes/indicators.py`**

Add below the existing GET endpoint in `backend/routes/indicators.py`:

```python
from pydantic import BaseModel
from indicators import compute_instance

class InstanceRequest(BaseModel):
    id: str
    type: str
    params: dict = {}

class IndicatorsPostRequest(BaseModel):
    start: str = "2023-01-01"
    end: str = "2024-01-01"
    interval: str = "1d"
    source: str = "yahoo"
    instances: list[InstanceRequest]

@router.post("/api/indicators/{ticker}")
def post_indicators(ticker: str, body: IndicatorsPostRequest):
    try:
        df = _fetch(ticker, body.start, body.end, body.interval, source=body.source)
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume_series = df["Volume"]

        result = {}
        for inst in body.instances:
            series_dict = compute_instance(
                inst.type, inst.params,
                close, high=high, low=low, volume=volume_series,
            )
            result[inst.id] = {
                key: _series_to_list(df.index, body.interval, series)
                for key, series in series_dict.items()
            }
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 3: Test the POST endpoint manually**

Start the backend and test with curl:

```bash
curl -X POST http://localhost:8000/api/indicators/AAPL \
  -H 'Content-Type: application/json' \
  -d '{
    "start": "2025-01-01",
    "end": "2025-12-31",
    "interval": "1d",
    "instances": [
      {"id": "rsi-1", "type": "rsi", "params": {"period": 14}},
      {"id": "rsi-2", "type": "rsi", "params": {"period": 2}},
      {"id": "ema-1", "type": "ema", "params": {"period": 20}}
    ]
  }'
```

Expected: JSON response with keys `"rsi-1"`, `"rsi-2"`, `"ema-1"`, each containing their respective series data. RSI values should be arrays of `{time, value}` objects. `rsi-1` and `rsi-2` should have different values (period 14 vs 2).

- [ ] **Step 4: Commit**

```bash
git add backend/indicators.py backend/routes/indicators.py
git commit -m "feat(A4): add indicator registry and POST /api/indicators endpoint"
```

---

### Task 3: Frontend data hook — `useInstanceIndicators`

**Files:**
- Modify: `frontend/src/shared/hooks/useOHLCV.ts`

- [ ] **Step 1: Add the new POST-based hook**

Add below the existing `useIndicators` function in `frontend/src/shared/hooks/useOHLCV.ts`:

```typescript
import type { IndicatorInstance } from '../types'

export function useInstanceIndicators(
  ticker: string,
  start: string,
  end: string,
  interval: string,
  instances: IndicatorInstance[],
  source: DataSource = 'yahoo',
) {
  const enabledInstances = instances.filter(i => i.enabled)
  const instancesKey = JSON.stringify(enabledInstances.map(i => ({ id: i.id, type: i.type, params: i.params })))

  return useQuery<Record<string, Record<string, { time: string; value: number | null }[]>>>({
    queryKey: ['instance-indicators', ticker, start, end, interval, instancesKey, source],
    queryFn: async () => {
      const { data } = await api.post(`/api/indicators/${ticker}`, {
        start,
        end,
        interval,
        source,
        instances: enabledInstances.map(i => ({ id: i.id, type: i.type, params: i.params })),
      })
      return data
    },
    enabled: !!ticker && enabledInstances.length > 0,
    staleTime: 5 * 60 * 1000,
  })
}
```

Keep the old `useIndicators` hook — it's still used by App.tsx until Task 5 migrates the state.

- [ ] **Step 2: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/shared/hooks/useOHLCV.ts
git commit -m "feat(A4): add useInstanceIndicators hook for POST-based indicator fetching"
```

---

### Task 4: Sidebar IndicatorList component

**Files:**
- Create: `frontend/src/features/sidebar/IndicatorList.tsx`

- [ ] **Step 1: Create the IndicatorList component**

Create `frontend/src/features/sidebar/IndicatorList.tsx`. This replaces the `ALL_INDICATORS` checkbox loop and MA settings expand in `Sidebar.tsx`:

```typescript
import { useState } from 'react'
import type { IndicatorInstance, IndicatorType } from '../../shared/types'
import { INDICATOR_DEFS, createInstance, paramSummary } from '../../shared/types/indicators'

interface IndicatorListProps {
  indicators: IndicatorInstance[]
  onChange: (indicators: IndicatorInstance[]) => void
}

const AVAILABLE_TYPES: IndicatorType[] = ['rsi', 'macd', 'ema', 'bb', 'atr', 'ma', 'volume']

export default function IndicatorList({ indicators, onChange }: IndicatorListProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [showAddMenu, setShowAddMenu] = useState(false)

  function toggle(id: string) {
    onChange(indicators.map(i => i.id === id ? { ...i, enabled: !i.enabled } : i))
  }

  function remove(id: string) {
    onChange(indicators.filter(i => i.id !== id))
    if (expandedId === id) setExpandedId(null)
  }

  function updateParam(id: string, key: string, value: number | string) {
    onChange(indicators.map(i => i.id === id ? { ...i, params: { ...i.params, [key]: value } } : i))
  }

  function addIndicator(type: IndicatorType) {
    onChange([...indicators, createInstance(type)])
    setShowAddMenu(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Indicators</span>
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => setShowAddMenu(!showAddMenu)}
            style={{
              background: 'var(--bg-input)', color: 'var(--accent-primary)',
              border: '1px solid var(--border-light)', borderRadius: 4,
              padding: '3px 10px', fontSize: 11, cursor: 'pointer',
            }}
          >
            + Add
          </button>
          {showAddMenu && (
            <div style={{
              position: 'absolute', top: '100%', right: 0, marginTop: 4,
              background: 'var(--bg-panel)', border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-md)', zIndex: 100, minWidth: 140,
              boxShadow: 'var(--shadow-md)', overflow: 'hidden',
            }}>
              {AVAILABLE_TYPES.map(type => (
                <div
                  key={type}
                  onClick={() => addIndicator(type)}
                  style={{
                    padding: '8px 12px', cursor: 'pointer', fontSize: 12,
                    color: 'var(--text-primary)', borderBottom: '1px solid var(--border-light)',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-panel-hover)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  {INDICATOR_DEFS[type].label}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {indicators.map(inst => {
        const def = INDICATOR_DEFS[inst.type]
        const isExpanded = expandedId === inst.id
        const summary = paramSummary(inst)

        return (
          <div key={inst.id} style={{
            background: 'var(--bg-input)', borderRadius: 6, padding: '8px 10px',
            marginBottom: 6,
            border: isExpanded ? '1px solid var(--border-light)' : '1px solid transparent',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="checkbox"
                checked={inst.enabled}
                onChange={() => toggle(inst.id)}
                style={{ accentColor: inst.color ?? 'var(--accent-primary)', margin: 0 }}
              />
              <span style={{ flex: 1, fontSize: 12, color: inst.enabled ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                {def.label}
              </span>
              {summary && (
                <span style={{ fontSize: 10, color: 'var(--text-muted)', marginRight: 4 }}>{summary}</span>
              )}
              {def.paramFields.length > 0 && (
                <span
                  onClick={() => setExpandedId(isExpanded ? null : inst.id)}
                  style={{ cursor: 'pointer', color: isExpanded ? 'var(--accent-primary)' : 'var(--text-muted)', fontSize: 13 }}
                  title="Settings"
                >
                  ⚙
                </span>
              )}
              <span
                onClick={() => remove(inst.id)}
                style={{ cursor: 'pointer', color: 'var(--text-muted)', fontSize: 11 }}
                title="Remove"
              >
                ✕
              </span>
            </div>

            {isExpanded && def.paramFields.length > 0 && (
              <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--border-light)', display: 'flex', flexDirection: 'column', gap: 6 }}>
                {def.paramFields.map(field => {
                  if (field.key === 'type') {
                    return (
                      <div key={field.key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 10, color: 'var(--text-muted)', minWidth: 40 }}>{field.label}</span>
                        <select
                          value={String(inst.params[field.key] ?? 'ema')}
                          onChange={e => updateParam(inst.id, field.key, e.target.value)}
                          style={{ fontSize: 11, background: 'var(--bg-main)', border: '1px solid var(--border-light)', borderRadius: 3, color: 'var(--text-primary)', padding: '2px 6px' }}
                        >
                          <option value="sma">SMA</option>
                          <option value="ema">EMA</option>
                          <option value="rma">RMA</option>
                        </select>
                      </div>
                    )
                  }
                  return (
                    <div key={field.key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontSize: 10, color: 'var(--text-muted)', minWidth: 40 }}>{field.label}</span>
                      <input
                        type="number"
                        value={Number(inst.params[field.key] ?? 0)}
                        min={field.min}
                        max={field.max}
                        onChange={e => {
                          const v = parseFloat(e.target.value)
                          if (!isNaN(v)) updateParam(inst.id, field.key, v)
                        }}
                        style={{
                          width: 48, background: 'var(--bg-main)', border: '1px solid var(--border-light)',
                          borderRadius: 3, color: 'var(--text-primary)', padding: '2px 6px', fontSize: 11,
                        }}
                      />
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 2: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/sidebar/IndicatorList.tsx
git commit -m "feat(A4): add IndicatorList sidebar component with inline-expand settings"
```

---

### Task 5: App.tsx state migration — replace scattered indicator state

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/features/sidebar/Sidebar.tsx`

This is the integration task. Replace `activeIndicators: IndicatorKey[]` + `maSettings: MASettings` with `indicators: IndicatorInstance[]`. Update localStorage persistence, Sidebar props, and data fetching.

- [ ] **Step 1: Replace indicator state in App.tsx**

In `frontend/src/App.tsx`:

1. Add imports:
```typescript
import type { IndicatorInstance } from './shared/types'
import { DEFAULT_INDICATORS } from './shared/types/indicators'
import { useInstanceIndicators } from './shared/hooks/useOHLCV'  // add to existing import
```

2. Replace the `activeIndicators` and `maSettings` state declarations (lines 55, 66):
```typescript
// Remove these:
// const [activeIndicators, setActiveIndicators] = useState<IndicatorKey[]>(saved?.activeIndicators ?? ['macd', 'rsi'])
// const [maSettings, setMaSettings] = useState<MASettings>({ ...DEFAULT_MA_SETTINGS, ...saved?.maSettings })

// Add this:
const [indicators, setIndicators] = useState<IndicatorInstance[]>(saved?.indicators ?? DEFAULT_INDICATORS)
```

3. Update the localStorage persistence effect — replace `activeIndicators` and `maSettings` with `indicators`:
```typescript
useEffect(() => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    ticker, start, end, interval, indicators, showSpy, showQqq, dataSource, datePreset,
  }))
}, [ticker, start, end, interval, indicators, showSpy, showQqq, dataSource, datePreset])
```

4. Replace the old `useIndicators` call with the new `useInstanceIndicators`:
```typescript
// Remove these:
// const indicatorKeys = activeIndicators.filter(k => k !== 'volume')
// const maParams = activeIndicators.includes('ma') ? maSettings : undefined
// const { data: indicatorData = EMPTY_INDICATORS, refetch: refetchIndicators } = useIndicators(...)

// Add this:
const { data: instanceData = {}, refetch: refetchIndicators } = useInstanceIndicators(
  ticker, start, end, interval, indicators, dataSource,
)
```

5. Remove the `toggleIndicator` callback — no longer needed.

6. Remove `MASettings` interface and `DEFAULT_MA_SETTINGS` constant (lines 15-33) — no longer used after this change. The `MASettings` type was also imported by Sidebar — see Step 2.

- [ ] **Step 2: Update Sidebar props**

In `frontend/src/features/sidebar/Sidebar.tsx`:

1. Replace the `SidebarProps` interface — remove indicator-specific props, add `indicators`/`onIndicatorsChange`:
```typescript
// Remove from SidebarProps:
//   activeIndicators: IndicatorKey[]
//   onToggleIndicator: (k: IndicatorKey) => void
//   maSettings: MASettings
//   onMaSettingsChange: (s: MASettings) => void

// Add to SidebarProps:
  indicators: IndicatorInstance[]
  onIndicatorsChange: (indicators: IndicatorInstance[]) => void
```

2. Replace the Indicators section (the `ALL_INDICATORS.map(...)` block at lines 386-475) with:
```typescript
import IndicatorList from './IndicatorList'

// In the render, replace the entire Indicators section:
<div style={styles.section}>
  <IndicatorList indicators={indicators} onChange={onIndicatorsChange} />
</div>
```

3. Remove `ALL_INDICATORS` constant (lines 36-43) and the `import type { MASettings } from '../../App'` line.

4. Update the destructured props in the function signature to match.

- [ ] **Step 3: Update Sidebar usage in App.tsx**

In `frontend/src/App.tsx`, update the `<Sidebar>` props:
```typescript
<Sidebar
  ticker={ticker}
  start={start}
  end={end}
  interval={interval}
  indicators={indicators}
  onIndicatorsChange={setIndicators}
  showSpy={showSpy}
  showQqq={showQqq}
  onTickerChange={t => { setTicker(t); setBacktestResult(null) }}
  onStartChange={d => { if (d > end) { setStart(end); setEnd(d) } else { setStart(d) }; setBacktestResult(null) }}
  onEndChange={d => { if (d < start) { setEnd(start); setStart(d) } else { setEnd(d) }; setBacktestResult(null) }}
  onIntervalChange={v => { setInterval(v); setBacktestResult(null) }}
  onToggleSpy={() => setShowSpy(v => !v)}
  onToggleQqq={() => setShowQqq(v => !v)}
  dataSource={dataSource}
  onDataSourceChange={setDataSource}
  datePreset={datePreset}
  onDatePresetChange={v => { setDatePreset(v); setBacktestResult(null) }}
/>
```

- [ ] **Step 4: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: Errors in Chart.tsx (it still expects old props) — that's expected and fixed in Task 6. All other files should be clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/features/sidebar/Sidebar.tsx
git commit -m "feat(A4): replace scattered indicator state with IndicatorInstance[] in App.tsx"
```

---

### Task 6: Chart.tsx refactor — generic overlays + SubPane component

This is the largest task. It replaces ~300 lines of per-indicator effects in Chart.tsx with a generic overlay loop and a reusable `<SubPane>` component.

**Files:**
- Create: `frontend/src/features/chart/SubPane.tsx`
- Modify: `frontend/src/features/chart/Chart.tsx`

- [ ] **Step 1: Create the SubPane component**

Create `frontend/src/features/chart/SubPane.tsx`. This replaces the hardcoded MACD and RSI pane effects. It creates its own `IChartApi`, renders series for one or more instances of the same type, handles crosshair sync and resize, and manages its own cleanup.

```typescript
import { useEffect, useRef, useMemo } from 'react'
import {
  createChart,
  createSeriesMarkers,
  LineSeries,
  HistogramSeries,
  ColorType,
} from 'lightweight-charts'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'
import type { IndicatorInstance } from '../../shared/types'

interface SubPaneProps {
  instances: IndicatorInstance[]
  instanceData: Record<string, Record<string, { time: string; value: number | null }[]>>
  mainChartRef: React.RefObject<IChartApi | null>
  mainSeriesRef: React.RefObject<ISeriesApi<any> | null>
  siblingPaneRefs: React.RefObject<IChartApi | null>[]
  siblingSeriesRefs: React.RefObject<ISeriesApi<any> | null>[]
  syncWidthsRef: React.RefObject<() => void>
  markers?: any[]
  toET: (time: string | number) => any
  label: string
}

const CHART_BG = '#0d1117'
const GRID = '#1c2128'
const TEXT = '#8b949e'
const UP = '#26a641'
const DOWN = '#f85149'

const SUB_COLORS = ['#a371f7', '#58a6ff', '#f0883e', '#e8ab6a', '#56d4c4', '#f85149']

const chartOptions = {
  layout: { background: { type: ColorType.Solid, color: CHART_BG }, textColor: TEXT },
  grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
  crosshair: { mode: 1 as const },
  timeScale: { borderColor: GRID, timeVisible: true },
  rightPriceScale: { borderColor: GRID },
  leftPriceScale: { visible: false, borderColor: GRID },
}

function toLineData(arr: { time: string; value: number | null }[], toET: (t: any) => any) {
  return arr.map(d => d.value !== null
    ? { time: toET(d.time as any) as any, value: d.value as number }
    : { time: toET(d.time as any) as any }
  )
}

export default function SubPane({
  instances, instanceData, mainChartRef, mainSeriesRef,
  siblingPaneRefs, siblingSeriesRefs, syncWidthsRef,
  markers, toET, label,
}: SubPaneProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const primarySeriesRef = useRef<ISeriesApi<any> | null>(null)
  const markersPluginRef = useRef<ReturnType<typeof createSeriesMarkers> | null>(null)

  const indicatorType = instances[0]?.type

  useEffect(() => {
    if (!containerRef.current || instances.length === 0) return

    const chart = createChart(containerRef.current, {
      ...chartOptions,
      height: containerRef.current.clientHeight,
    })
    chartRef.current = chart
    let firstSeries: ISeriesApi<any> | null = null

    if (indicatorType === 'macd') {
      const inst = instances[0]
      const data = instanceData[inst.id]
      if (data) {
        const histSeries = chart.addSeries(HistogramSeries, {
          color: UP,
          priceFormat: { type: 'price', precision: 4 },
        })
        const histData = (data.histogram ?? []).map(d => d.value !== null
          ? { time: toET(d.time as any) as any, value: d.value as number, color: (d.value as number) >= 0 ? UP : DOWN }
          : { time: toET(d.time as any) as any }
        )
        histSeries.setData(histData)
        firstSeries = histSeries

        chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'MACD' })
          .setData(toLineData(data.macd ?? [], toET))
        chart.addSeries(LineSeries, { color: '#f0883e', lineWidth: 1, title: 'Signal' })
          .setData(toLineData(data.signal ?? [], toET))
      }
    } else {
      instances.forEach((inst, idx) => {
        const data = instanceData[inst.id]
        if (!data) return
        const seriesKey = Object.keys(data)[0]
        if (!seriesKey) return
        const color = inst.color ?? SUB_COLORS[idx % SUB_COLORS.length]
        const paramStr = Object.values(inst.params).join(',')
        const series = chart.addSeries(LineSeries, {
          color,
          lineWidth: 1,
          title: `${inst.type.toUpperCase()}(${paramStr})`,
        })
        series.setData(toLineData(data[seriesKey], toET))
        if (!firstSeries) firstSeries = series
      })

      // RSI reference lines
      if (indicatorType === 'rsi' && instances.length > 0) {
        const firstData = instanceData[instances[0].id]
        const seriesKey = firstData ? Object.keys(firstData)[0] : null
        const arr = seriesKey ? firstData[seriesKey] : []
        if (arr.length > 0) {
          const first = arr[0].time
          const last = arr[arr.length - 1].time
          chart.addSeries(LineSeries, { color: '#f85149', lineWidth: 1, lineStyle: 2 })
            .setData([{ time: toET(first as any) as any, value: 70 }, { time: toET(last as any) as any, value: 70 }])
          chart.addSeries(LineSeries, { color: '#26a641', lineWidth: 1, lineStyle: 2 })
            .setData([{ time: toET(first as any) as any, value: 30 }, { time: toET(last as any) as any, value: 30 }])
        }
      }
    }

    primarySeriesRef.current = firstSeries
    chart.timeScale().fitContent()

    if (mainChartRef.current) {
      const mainRange = mainChartRef.current.timeScale().getVisibleLogicalRange()
      if (mainRange) chart.timeScale().setVisibleLogicalRange(mainRange)
    }

    syncWidthsRef.current()

    const crosshairHandler = (param: any) => {
      try {
        if (!param.time) {
          mainChartRef.current?.clearCrosshairPosition()
          for (const ref of siblingPaneRefs) ref.current?.clearCrosshairPosition()
          return
        }
        if (mainChartRef.current && mainSeriesRef.current)
          mainChartRef.current.setCrosshairPosition(NaN, param.time, mainSeriesRef.current)
        for (let i = 0; i < siblingPaneRefs.length; i++) {
          if (siblingPaneRefs[i].current && siblingSeriesRefs[i]?.current)
            siblingPaneRefs[i].current!.setCrosshairPosition(NaN, param.time, siblingSeriesRefs[i].current!)
        }
      } catch {}
    }
    chart.subscribeCrosshairMove(crosshairHandler)

    const ro = new ResizeObserver(() => {
      if (containerRef.current)
        chart.applyOptions({ width: containerRef.current.clientWidth, height: containerRef.current.clientHeight })
    })
    ro.observe(containerRef.current)

    return () => {
      chartRef.current = null
      primarySeriesRef.current = null
      markersPluginRef.current = null
      chart.unsubscribeCrosshairMove(crosshairHandler)
      chart.remove()
      ro.disconnect()
      syncWidthsRef.current()
    }
  }, [instances, instanceData, indicatorType, toET, mainChartRef, mainSeriesRef, siblingPaneRefs, siblingSeriesRefs, syncWidthsRef])

  // Markers
  useEffect(() => {
    const series = primarySeriesRef.current
    if (!series) return
    const m = markers ?? []
    if (!markersPluginRef.current) {
      markersPluginRef.current = createSeriesMarkers(series, m)
    } else {
      markersPluginRef.current.setMarkers(m)
    }
  }, [markers, instances, instanceData])

  return (
    <div style={{ height: '100%', borderTop: '1px solid #1c2128', position: 'relative' }}>
      <span style={{ position: 'absolute', top: 4, left: 8, fontSize: 10, color: '#8b949e', zIndex: 1 }}>{label}</span>
      <div ref={containerRef} style={{ height: '100%', width: '100%' }} />
    </div>
  )
}
```

Key design points:
- Takes an array of `IndicatorInstance` — multiple RSIs share one pane, each MACD gets its own
- MACD renders histogram + two lines (special case); everything else renders one line per instance
- RSI adds 70/30 reference lines automatically
- Crosshair sync uses ref arrays so it works with any number of sibling panes
- Cleanup nulls refs before `chart.remove()` (same guard pattern as existing code)

- [ ] **Step 2: Update Chart.tsx props**

In `frontend/src/features/chart/Chart.tsx`, replace the `ChartProps` interface (line 13):

```typescript
import type { IndicatorInstance } from '../../shared/types'
import { INDICATOR_DEFS } from '../../shared/types/indicators'
import SubPane from './SubPane'

interface ChartProps {
  data: OHLCVBar[]
  spyData?: OHLCVBar[]
  qqqData?: OHLCVBar[]
  showSpy: boolean
  showQqq: boolean
  indicators: IndicatorInstance[]
  instanceData: Record<string, Record<string, { time: string; value: number | null }[]>>
  trades?: Trade[]
  emaOverlays?: EMAOverlay[]
  onChartReady?: (chart: IChartApi | null) => void
}
```

Remove the old imports of `IndicatorData`, `IndicatorKey` from the types import line. Remove the `maShowRaw8`, `maShowRaw21`, `maShowSg8`, `maShowSg21`, `maCompensateLag` props — those settings now live inside the instance params.

Update the function signature to destructure the new props.

- [ ] **Step 3: Replace per-indicator overlay effects with generic main-overlay loop**

Remove these individual effects from Chart.tsx:
- EMA effect (lines 363-379)
- BB effect (lines 381-397)
- MA8/MA21 + S-G effect (lines 399-445)
- Volume effect (lines 350-361)

Replace with a single generic effect:

```typescript
// ─── Main-chart indicator overlays (generic) ─────────────────────────
useEffect(() => {
  const chart = chartRef.current
  if (!chart) return
  const created: ISeriesApi<any>[] = []

  const mainInstances = indicators.filter(i => i.enabled && i.pane === 'main')

  for (const inst of mainInstances) {
    const data = instanceData[inst.id]
    if (!data) continue

    if (inst.type === 'volume') {
      const volData = (data.volume ?? []).map(d => ({
        time: toET(d.time as any) as any,
        value: d.value,
        color: '#26a64166',
      }))
      const vol = chart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
      })
      vol.priceScale().applyOptions({ scaleMargins: { top: 0.75, bottom: 0 }, visible: false })
      vol.setData(volData)
      created.push(vol)
    } else if (inst.type === 'bb') {
      const colors = { upper: '#30363d', middle: '#58a6ff', lower: '#30363d' }
      for (const key of ['upper', 'middle', 'lower'] as const) {
        if (!data[key]) continue
        const s = chart.addSeries(LineSeries, {
          color: colors[key], lineWidth: 1,
          title: `BB ${key.charAt(0).toUpperCase() + key.slice(1)}`,
          priceScaleId: 'right',
        })
        s.setData(toLineData(data[key]))
        created.push(s)
      }
    } else {
      // EMA, MA — single line per instance
      const seriesKey = Object.keys(data)[0]
      if (!seriesKey || !data[seriesKey]) continue
      const paramStr = Object.values(inst.params).join(',')
      const color = inst.color ?? '#f0883e'
      const s = chart.addSeries(LineSeries, {
        color, lineWidth: 1,
        title: `${inst.type.toUpperCase()}(${paramStr})`,
        priceScaleId: 'right',
      })
      s.setData(toLineData(data[seriesKey]))
      created.push(s)
    }
  }

  return () => { for (const s of created) { try { chart.removeSeries(s) } catch {} } }
}, [indicators, instanceData])
```

Also remove these now-unused variables/booleans:
- `showEma`, `showBb`, `showMa`, `showVolume` (lines 124-127)
- The `showMacd` and `showRsi` variables stay temporarily for the height calculation but are replaced in Step 5

- [ ] **Step 4: Replace MACD/RSI pane effects with SubPane components**

Remove these from Chart.tsx:
- The MACD chart `useEffect` (lines 522-579)
- The MACD markers `useEffect` (lines 581-592)
- The RSI chart `useEffect` (lines 595-651)
- The RSI markers `useEffect` (lines 653-664)
- The refs: `macdChartRef`, `rsiChartRef`, `macdContainerRef`, `rsiContainerRef`, `macdSeriesRef`, `rsiSeriesRef`, `macdMarkersPluginRef`, `rsiMarkersPluginRef`

These are all replaced by `<SubPane>` components rendered in the JSX (Step 5).

Add a `useMemo` to compute the sub-pane groups:

```typescript
const subPaneGroups = useMemo(() => {
  const subInstances = indicators.filter(i => i.enabled && i.pane === 'sub')
  const groups: { key: string; label: string; instances: IndicatorInstance[] }[] = []
  const seen = new Map<string, number>()

  for (const inst of subInstances) {
    const def = INDICATOR_DEFS[inst.type]
    if (def.subPaneSharing === 'shared') {
      const existing = seen.get(inst.type)
      if (existing !== undefined) {
        groups[existing].instances.push(inst)
      } else {
        seen.set(inst.type, groups.length)
        const paramStr = Object.values(inst.params).join(',')
        groups.push({
          key: inst.type,
          label: inst.type.toUpperCase(),
          instances: [inst],
        })
      }
    } else {
      const paramStr = Object.values(inst.params).join(',')
      groups.push({
        key: inst.id,
        label: `${inst.type.toUpperCase()}(${paramStr})`,
        instances: [inst],
      })
    }
  }
  return groups
}, [indicators])
```

For crosshair sync between sub-panes, create a ref array. Each `<SubPane>` needs refs to the other panes so crosshair events propagate. Use a stable ref map:

```typescript
const subPaneChartRefs = useRef<Map<string, React.RefObject<IChartApi | null>>>(new Map())
const subPaneSeriesRefs = useRef<Map<string, React.RefObject<ISeriesApi<any> | null>>>(new Map())

// Ensure refs exist for each group
for (const group of subPaneGroups) {
  if (!subPaneChartRefs.current.has(group.key)) {
    subPaneChartRefs.current.set(group.key, { current: null })
    subPaneSeriesRefs.current.set(group.key, { current: null })
  }
}
```

Update the `syncWidths` function inside the main chart mount effect to iterate over all sub-pane refs instead of hardcoded `macdChartRef`/`rsiChartRef`:

```typescript
function syncWidths() {
  const mainChart = chartRef.current
  if (!mainChart) return
  try {
    let maxRightW = mainChart.priceScale('right').width()
    for (const ref of subPaneChartRefs.current.values()) {
      if (ref.current) maxRightW = Math.max(maxRightW, ref.current.priceScale('right').width())
    }
    if (maxRightW > 0) {
      mainChart.applyOptions({ rightPriceScale: { minimumWidth: maxRightW } })
      for (const ref of subPaneChartRefs.current.values()) {
        ref.current?.applyOptions({ rightPriceScale: { minimumWidth: maxRightW } })
      }
    }
    const mainLeftW = mainChart.priceScale('left').width()
    if (mainLeftW > 0) {
      for (const ref of subPaneChartRefs.current.values()) {
        ref.current?.applyOptions({ leftPriceScale: { minimumWidth: mainLeftW, visible: false } })
      }
    }
  } catch {}
}
```

Update the main chart's `syncHandler` (pan/zoom sync) similarly:

```typescript
const syncHandler = (range: any) => {
  if (!range) return
  for (const ref of subPaneChartRefs.current.values()) {
    try { ref.current?.timeScale().setVisibleLogicalRange(range) } catch {}
  }
  // ... rest of rAF syncWidths and sessionStorage (unchanged)
}
```

Update the main chart's crosshair handler:

```typescript
const crosshairHandler = (param: any) => {
  try {
    if (!param.time) {
      for (const ref of subPaneChartRefs.current.values()) ref.current?.clearCrosshairPosition()
      return
    }
    for (const [key, chartRefEntry] of subPaneChartRefs.current.entries()) {
      const seriesRefEntry = subPaneSeriesRefs.current.get(key)
      if (chartRefEntry.current && seriesRefEntry?.current)
        chartRefEntry.current.setCrosshairPosition(NaN, param.time, seriesRefEntry.current)
    }
  } catch {}
}
```

- [ ] **Step 5: Update the render section — dynamic height + SubPane components**

Replace the height calculation and render (lines 666-687):

```typescript
const subPaneCount = subPaneGroups.length
const mainHeightPct = subPaneCount === 0 ? 100 : subPaneCount === 1 ? 65 : subPaneCount === 2 ? 50 : subPaneCount === 3 ? 45 : 40
const subHeightPct = subPaneCount === 0 ? 0 : Math.floor((100 - mainHeightPct) / subPaneCount)

return (
  <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%' }}>
    <div ref={containerRef} style={{ height: `${mainHeightPct}%`, width: '100%' }} />
    {subPaneGroups.map(group => (
      <div key={group.key} style={{ height: `${subHeightPct}%` }}>
        <SubPane
          instances={group.instances}
          instanceData={instanceData}
          mainChartRef={chartRef}
          mainSeriesRef={candleSeriesRef}
          siblingPaneRefs={subPaneGroups
            .filter(g => g.key !== group.key)
            .map(g => subPaneChartRefs.current.get(g.key)!)
          }
          siblingSeriesRefs={subPaneGroups
            .filter(g => g.key !== group.key)
            .map(g => subPaneSeriesRefs.current.get(g.key)!)
          }
          syncWidthsRef={syncWidthsRef}
          markers={subPaneMarkers ?? undefined}
          toET={toET}
          label={group.label}
        />
      </div>
    ))}
  </div>
)
```

- [ ] **Step 6: Update Chart props in App.tsx**

In `frontend/src/App.tsx`, update the `<Chart>` component props:

```typescript
<Chart
  data={ohlcv}
  spyData={showSpy ? (spyData ?? []) : undefined}
  qqqData={showQqq ? (qqqData ?? []) : undefined}
  showSpy={showSpy}
  showQqq={showQqq}
  indicators={indicators}
  instanceData={instanceData}
  trades={trades}
  emaOverlays={emaOverlays}
  onChartReady={setMainChart}
/>
```

Remove the old props: `indicatorData`, `activeIndicators`, `maShowRaw8`, `maShowRaw21`, `maShowSg8`, `maShowSg21`, `maCompensateLag`.

- [ ] **Step 7: Remove old types**

Now that no file references them, remove from `frontend/src/shared/types/index.ts`:
- `IndicatorKey` type alias (line 56)
- `IndicatorData` interface (lines 47-54)
- `MACDData` interface (lines 15-19)
- `BBData` interface (lines 21-25)
- `EMAData` interface (lines 27-31)
- `MAData` interface (lines 35-45)

Also remove the old `useIndicators` hook from `frontend/src/shared/hooks/useOHLCV.ts` and the `MASettings` interface / `DEFAULT_MA_SETTINGS` from `App.tsx` if not already removed in Task 5.

Update the `AppState` interface to replace `activeIndicators: IndicatorKey[]` with `indicators: IndicatorInstance[]`.

- [ ] **Step 8: Verify it compiles and renders**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors.

Start the dev server, open the app, verify:
1. Default MACD and RSI panes render correctly
2. Adding a second RSI (e.g. RSI(2)) via sidebar shows two lines in one RSI pane
3. Adding an EMA shows it on the main chart
4. Toggling indicators on/off works
5. Crosshair syncs across all panes
6. Pan/zoom on main chart syncs to sub-panes

- [ ] **Step 9: Commit**

```bash
git add frontend/src/features/chart/SubPane.tsx frontend/src/features/chart/Chart.tsx \
  frontend/src/App.tsx frontend/src/shared/types/index.ts frontend/src/shared/hooks/useOHLCV.ts
git commit -m "feat(A4): replace hardcoded indicator panes with generic SubPane component"
```

---

### Task 7: Strategy save/load migration

**Files:**
- Modify: `frontend/src/features/strategy/StrategyBuilder.tsx`

Old saved strategies have no `indicators` field. New ones include it. This task adds migration on load and includes indicators in the save snapshot.

- [ ] **Step 1: Add migration function**

In `frontend/src/features/strategy/StrategyBuilder.tsx`, add a migration helper (near the top, after imports):

```typescript
import type { IndicatorInstance } from '../../shared/types'
import { DEFAULT_INDICATORS } from '../../shared/types/indicators'

function migrateIndicators(s: SavedStrategy): IndicatorInstance[] {
  if (s.indicators) return s.indicators
  return DEFAULT_INDICATORS
}
```

Old strategies don't encode which chart indicators were active (that was stored in `localStorage` under `strategylab-settings`, not in the strategy save). So the migration just falls back to the defaults. This is the right behavior — the strategy blob stores *trading rules*, and indicators are a *chart display* concern.

- [ ] **Step 2: Include indicators in save snapshot**

The `currentSnapshot` function in StrategyBuilder doesn't have access to the `indicators` state (it lives in App.tsx). Two options: (a) pass indicators down as a prop, (b) leave it out of strategy saves for now since indicators are a chart-display concern persisted in localStorage.

**Choice: (b)** — indicators are persisted in `localStorage` via App.tsx's `STORAGE_KEY`. They're restored on app load. Strategy save/load is for trading rules. The `indicators?` field on `SavedStrategy` is there as a forward-compatible hook for when users want strategy-specific indicator configs (B11 saved-strategy library), but doesn't need wiring now.

No code change needed for save. For load, the migration function ensures old strategies don't break:

```typescript
function loadSavedStrategy(s: SavedStrategy) {
  // ... existing field restoration ...
  // indicators migration is a no-op since indicators live in App state,
  // not strategy state. The field is reserved for future B11 use.
}
```

- [ ] **Step 3: Verify save/load still works**

Start the dev server. Save a strategy, reload the page, load it back. Verify no errors and all rule fields restore correctly. Old saved strategies in localStorage should load without errors (they have no `indicators` field, which is optional).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/strategy/StrategyBuilder.tsx
git commit -m "feat(A4): add forward-compatible indicators field to SavedStrategy"
```

---

### Task 8: Collapsible chart section

**Files:**
- Modify: `frontend/src/features/sidebar/Sidebar.tsx`

The sidebar already has a section pattern with `styles.section` and `styles.sectionTitle`. Make the Indicators and Compare sections collapsible.

- [ ] **Step 1: Add collapse state and toggle**

In `frontend/src/features/sidebar/Sidebar.tsx`, add collapse state:

```typescript
const [indicatorsCollapsed, setIndicatorsCollapsed] = useState(false)
const [compareCollapsed, setCompareCollapsed] = useState(false)
```

- [ ] **Step 2: Make the Indicators section collapsible**

Wrap the `<IndicatorList>` in a collapsible container:

```typescript
<div style={styles.section}>
  <div
    onClick={() => setIndicatorsCollapsed(c => !c)}
    style={{ ...styles.sectionTitle, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, userSelect: 'none' }}
  >
    <span style={{ fontSize: 10, transform: indicatorsCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)', transition: 'transform 0.15s' }}>▼</span>
    Indicators
  </div>
  {!indicatorsCollapsed && (
    <IndicatorList indicators={indicators} onChange={onIndicatorsChange} />
  )}
</div>
```

- [ ] **Step 3: Make the Compare section collapsible**

Same pattern for the SPY/QQQ section:

```typescript
<div style={styles.section}>
  <div
    onClick={() => setCompareCollapsed(c => !c)}
    style={{ ...styles.sectionTitle, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, userSelect: 'none' }}
  >
    <span style={{ fontSize: 10, transform: compareCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)', transition: 'transform 0.15s' }}>▼</span>
    Compare
  </div>
  {!compareCollapsed && (
    <>
      <label style={styles.checkRow}>
        <input type="checkbox" checked={showSpy} onChange={onToggleSpy} style={{ accentColor: 'var(--accent-orange)' }} />
        <span style={{ marginLeft: 8, color: 'var(--accent-orange)' }}>SPY</span>
      </label>
      <label style={styles.checkRow}>
        <input type="checkbox" checked={showQqq} onChange={onToggleQqq} style={{ accentColor: 'var(--accent-purple)' }} />
        <span style={{ marginLeft: 8, color: 'var(--accent-purple)' }}>QQQ</span>
      </label>
      {(showSpy || showQqq) && (
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>% change from start</div>
      )}
    </>
  )}
</div>
```

- [ ] **Step 4: Verify in browser**

Start the dev server. Click the section headers — they should collapse/expand with a rotating arrow. Collapsed state doesn't need to persist across sessions (it's minor UI state).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/sidebar/Sidebar.tsx
git commit -m "feat(A4): make Indicators and Compare sidebar sections collapsible"
```

---

### Task 9: Collapsible chart area

**Files:**
- Modify: `frontend/src/App.tsx`

The spec calls for the entire chart area (main chart + all sub-panes) to be collapsible. When collapsed, `IChartApi` instances are destroyed and indicator data fetching is skipped.

- [ ] **Step 1: Gate chart rendering and data fetching on `chartEnabled`**

The `chartEnabled` state already exists in App.tsx (line 65). Currently it gates only the `<Chart>` render. Extend it to also skip indicator data fetching:

In App.tsx, update the `useInstanceIndicators` call to pass an empty array when chart is disabled:

```typescript
const { data: instanceData = {}, refetch: refetchIndicators } = useInstanceIndicators(
  ticker, start, end, interval,
  chartEnabled ? indicators : [],
  dataSource,
)
```

The `useInstanceIndicators` hook already has `enabled: !!ticker && enabledInstances.length > 0`, so passing an empty array skips the fetch. OHLCV data continues fetching (the strategy builder needs it).

- [ ] **Step 2: Verify in browser**

Click "Disable Chart" in the header. Chart should collapse. No indicator API calls should fire (check Network tab). Re-enable — chart and indicators should reappear.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(A4): skip indicator fetching when chart is disabled"
```

---

### Task 10: Remove old GET endpoint (cleanup)

**Files:**
- Modify: `backend/routes/indicators.py`
- Modify: `frontend/src/shared/hooks/useOHLCV.ts`

- [ ] **Step 1: Remove old `useIndicators` hook**

In `frontend/src/shared/hooks/useOHLCV.ts`, delete the `useIndicators` function (lines 19-42). No file should import it after Task 5.

Verify: `cd frontend && npx tsc --noEmit` — no errors.

- [ ] **Step 2: Remove old GET endpoint**

In `backend/routes/indicators.py`, remove the `@router.get("/api/indicators/{ticker}")` function (lines 17-127). Keep the `_series_to_list` helper and the new POST endpoint. Also remove the unused import of `compute_indicators` from `signal_engine`:

```python
# Remove this line:
# from signal_engine import compute_indicators, _apply_sg, _apply_sg_predictive
```

- [ ] **Step 3: Test the backend still starts**

Run: `cd backend && python -c "from routes.indicators import router; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add frontend/src/shared/hooks/useOHLCV.ts backend/routes/indicators.py
git commit -m "chore(A4): remove legacy GET /api/indicators endpoint and useIndicators hook"
```

# A8d: Same-TF Indicator Resample to View Interval

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the user views the chart at a coarser interval than the backtest interval ("View as"), show indicators computed at the backtest interval and resampled to match view-interval candle timestamps — not recomputed at the view interval.

**Architecture:** The backend already has `view_interval` field and resample logic in `indicators.py` (committed in e574d10). The frontend currently passes `chartInterval` (= viewInterval) as the computation interval, so indicators get recomputed at the view interval instead of resampled from the backtest interval. Fix: pass `interval` (backtest) to the hook and add a separate `viewInterval` parameter that becomes `view_interval` in the API request. The backend handles the rest.

**Data truncation:** When the backtest interval has tighter yfinance date limits than the view interval (e.g., 30m → 60 days max vs 1D → unlimited), the indicator will only cover the available data range. This is acceptable — accurate partial data is better than wrong full data. lightweight-charts renders partial series naturally (blank where no data).

**Tech Stack:** React, TypeScript, TanStack Query, FastAPI, pandas

**Scope:** "Same TF" instances only (no `htfInterval`). HTF instances already have their own alignment pipeline via `align_htf_to_ltf` and are unchanged.

---

### Task 1: Write failing tests for view_interval parameter

**Files:**
- Modify: `frontend/src/test/useOHLCV.test.ts`

- [ ] **Step 1: Add test — view_interval sent when viewInterval differs from interval**

Add this test to the existing `useInstanceIndicators` describe block:

```typescript
  it('includes view_interval in API request when viewInterval differs from interval', async () => {
    const instances = [
      { id: 'rsi-1', type: 'rsi' as const, params: { period: 14 }, enabled: true, pane: 'sub' as const },
    ]
    const responseData = {
      'rsi-1': { rsi: [{ time: '2024-01-02', value: 55 }] },
    }
    postSpy.mockResolvedValueOnce(ok(responseData))

    const { result } = renderHook(
      () => useInstanceIndicators('AAPL', '2024-01-01', '2024-01-31', '5m', instances, 'yahoo', false, '1h'),
      { wrapper: createWrapper() },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(postSpy).toHaveBeenCalledWith('/api/indicators/AAPL', expect.objectContaining({
      interval: '5m',
      view_interval: '1h',
      instances: [{ id: 'rsi-1', type: 'rsi', params: { period: 14 } }],
    }))
  })
```

- [ ] **Step 2: Add test — view_interval omitted when viewInterval matches interval**

```typescript
  it('omits view_interval when viewInterval equals interval', async () => {
    const instances = [
      { id: 'rsi-1', type: 'rsi' as const, params: { period: 14 }, enabled: true, pane: 'sub' as const },
    ]
    const responseData = {
      'rsi-1': { rsi: [{ time: '2024-01-02', value: 55 }] },
    }
    postSpy.mockResolvedValueOnce(ok(responseData))

    const { result } = renderHook(
      () => useInstanceIndicators('AAPL', '2024-01-01', '2024-01-31', '1d', instances, 'yahoo', false, '1d'),
      { wrapper: createWrapper() },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const callBody = postSpy.mock.calls[0][1]
    expect(callBody).not.toHaveProperty('view_interval')
  })
```

- [ ] **Step 3: Add test — view_interval omitted when viewInterval is undefined**

```typescript
  it('omits view_interval when viewInterval is undefined', async () => {
    const instances = [
      { id: 'rsi-1', type: 'rsi' as const, params: { period: 14 }, enabled: true, pane: 'sub' as const },
    ]
    const responseData = {
      'rsi-1': { rsi: [{ time: '2024-01-02', value: 55 }] },
    }
    postSpy.mockResolvedValueOnce(ok(responseData))

    const { result } = renderHook(
      () => useInstanceIndicators('AAPL', '2024-01-01', '2024-01-31', '1d', instances),
      { wrapper: createWrapper() },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const callBody = postSpy.mock.calls[0][1]
    expect(callBody).not.toHaveProperty('view_interval')
  })
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/test/useOHLCV.test.ts`

Expected: The `view_interval` test fails because `useInstanceIndicators` doesn't accept a `viewInterval` parameter yet (TypeScript compile error or wrong API call body).

---

### Task 2: Implement useInstanceIndicators change

**Files:**
- Modify: `frontend/src/shared/hooks/useOHLCV.ts`

- [ ] **Step 1: Add viewInterval parameter and wire it**

In `useInstanceIndicators`, add the `viewInterval` parameter and include `view_interval` in the API request when it differs from `interval`:

```typescript
export function useInstanceIndicators(
  ticker: string,
  start: string,
  end: string,
  interval: string,
  instances: IndicatorInstance[],
  source: DataSource = 'yahoo',
  extendedHours: boolean = false,
  viewInterval?: string,
) {
```

Update the `regularQuery` query key (line 45) to include `viewInterval`:

```typescript
    queryKey: ['instance-indicators', ticker, start, end, interval, viewInterval, regularQueryKey, source, extendedHours],
```

Update the `regularQuery` queryFn (lines 47-51) to conditionally include `view_interval`:

```typescript
    queryFn: async () => {
      const body: Record<string, unknown> = {
        start, end, interval, source, extended_hours: extendedHours,
        instances: regularInstances.map(i => ({ id: i.id, type: i.type, params: i.params })),
      }
      if (viewInterval && viewInterval !== interval) {
        body.view_interval = viewInterval
      }
      const { data } = await api.post(`/api/indicators/${ticker}`, body)
      return data
    },
```

No changes to the HTF query path — HTF instances have their own alignment mechanism.

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/test/useOHLCV.test.ts`

Expected: All tests pass, including the three new view_interval tests.

---

### Task 3: Wire App.tsx to pass backtest interval + viewInterval

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Change useInstanceIndicators call**

Current code (lines 115-117):
```typescript
  const { data: instanceData = {}, refetch: refetchIndicators } = useInstanceIndicators(
    ticker, start, end, chartInterval, chartEnabled ? indicators : [], dataSource, extendedHours,
  )
```

Change `chartInterval` → `interval` (backtest interval) and add `viewInterval`:
```typescript
  const { data: instanceData = {}, refetch: refetchIndicators } = useInstanceIndicators(
    ticker, start, end, interval, chartEnabled ? indicators : [], dataSource, extendedHours, viewInterval,
  )
```

This is the only change in App.tsx. The `useOHLCV` calls stay on `chartInterval` (correct — candles display at view interval). Only indicators change to compute at backtest interval and resample.

- [ ] **Step 2: Build verification**

Run: `cd frontend && npm run build`

Expected: Build succeeds with no errors.

---

### Task 4: End-to-end verification

- [ ] **Step 1: Run full test suite**

Run: `cd frontend && npx vitest run`

Expected: All tests pass.

- [ ] **Step 2: Manual verification checklist**

Start the dev server and verify in browser:

1. **Same interval (no resample):** Backtest at 1D, "View as" 1D → indicators render as before, no regression.
2. **Different interval (resample):** Backtest at 5m, "View as" 1h → indicators appear, aligned with 1h candles. Compare RSI values: they should reflect 5m computation (higher granularity) resampled, not 1h-native RSI.
3. **Data truncation:** Backtest at 5m with a >60-day date range, "View as" 1D → indicator appears only for the recent ~60 days (yfinance 5m limit). Chart candles cover the full range. No errors in console.
4. **HTF indicator unaffected:** If a multi-TF overlay exists (htfInterval set), verify it still renders with stepped alignment.
5. **No chart enabled:** Switch to a non-chart view → no errors, no unnecessary fetches.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/shared/hooks/useOHLCV.ts frontend/src/App.tsx frontend/src/test/useOHLCV.test.ts
git commit -m "feat(A8d): resample same-TF indicators to view interval

When 'View as' selects a coarser interval, indicators now compute at
the backtest interval and resample to the view interval via the backend
view_interval field (origin='start', .dropna()). Preserves backtest-
fidelity values instead of recomputing at the display interval.

Truncation when yfinance date limits are tighter than the view range
is accepted — partial accurate data over full inaccurate data."
```

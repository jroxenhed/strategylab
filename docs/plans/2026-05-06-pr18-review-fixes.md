# PR #18 Review Fixes — F19 React Query + C22 Auto-Optimizer

## Context

Code review of PR #18 ("Overnight build 12: F19 React Query migration + C22 auto-optimizer") by 12 parallel reviewers identified 3 P1, 10 P2, and 5 P3 findings. This plan addresses all P1s, all actionable P2s, and straightforward P3s.

Review artifact: `.context/compound-engineering/ce-review/20260506-025729-4eb504db/`

## Requirements Trace

| ID | Finding | Sev | Reviewers |
|----|---------|-----|-----------|
| R1 | 500 errors silently counted as skipped combos | P1 | kieran-python, api-contract, reliability, adversarial |
| R2 | No timeout on 200-combo sync optimizer | P1 | performance, reliability |
| R3 | Zero test coverage for optimizer endpoint | P1 | testing |
| R4 | Drag-reorder lost optimistic visual update | P2 | correctness, adversarial, kieran-typescript |
| R5 | botsError banner has no dismiss path | P2 | maintainability, adversarial |
| R6 | paramRows not reset on strategy change | P2 | adversarial |
| R7 | Redundant model_copy per combo | P2 | kieran-python, performance |
| R8 | buildParamOptions/linspace duplicated | P2 | maintainability |
| R9 | useAccountQuery/useOrdersQuery missing adaptiveMs | P2 | reliability |
| R10 | Inner catch too narrow for non-HTTP exceptions | P2 | testing, correctness |
| R11 | win_rate_pct rounded to 1dp inconsistently | P3 | api-contract |
| R12 | Non-deterministic set in error message | P3 | kieran-python |
| R13 | colColor key type too wide | P3 | kieran-typescript |
| R14 | useBotsQuery drops AbortSignal | P3 | correctness |
| R15 | AccountBar drops error message detail | P3 | kieran-typescript |

## Implementation Units

### Phase A: Backend optimizer fixes

**Target:** `backend/routes/backtest_optimizer.py` (single file)

#### A1: Error handling overhaul (R1 + R10)

Current inner catch (~line 108):
```python
except HTTPException:
    skipped += 1
```

Replace with:
```python
except HTTPException as exc:
    if exc.status_code >= 500:
        raise  # data/server failure — surface to caller
    skipped += 1  # 4xx = invalid param value for this combo
except Exception:
    skipped += 1  # non-HTTP error (pandas ValueError etc) — isolate per-combo
```

This synthesizes two findings: kieran-python's "re-raise 500s" with testing/correctness's "catch non-HTTP exceptions per-combo". The result: 4xx skips gracefully, 5xx surfaces immediately, non-HTTP errors skip gracefully.

#### A2: Wall-clock timeout (R2)

Add `import time` at top. Before combo loop:
```python
start = time.monotonic()
```

After each combo iteration:
```python
if time.monotonic() - start > 60:
    break
```

Add `timed_out: bool = False` field to `OptimizeResponse` model. Set when loop breaks early. 60s budget is conservative — at ~200ms per backtest, 200 combos takes ~40s normally, so timeout only fires on genuinely slow data fetches.

#### A3: Remove redundant model_copy (R7)

Line 90: `modified = req.base.model_copy(deep=True)` → `modified = req.base`

`_apply_param` deep-copies its input on entry (`backtest_sweep.py:53`), so `req.base` is never mutated. Eliminates 200 wasted allocations per optimizer run.

#### A4: Precision + formatting fixes (R11 + R12)

- Line 104: Remove re-rounding — use `s.get("win_rate_pct", 0.0)` directly (matches backtest + sweep precision)
- Line 65: Change error message to use `sorted(_VALID_METRICS)` for deterministic output

---

### Phase B: Backend tests (R3)

**Target:** `backend/tests/test_backtest_optimizer.py` (new file)

Uses `pytest` + `TestClient` with mocked `run_backtest` returning minimal summary dicts.

Test cases:
1. **Happy path** — 2-param grid returns results ranked by chosen metric
2. **Validation guards (7 branches)** — empty params, >3 params, invalid metric, top_n=0, top_n=51, empty values, values>10, combos>200
3. **4xx skip** — Patch `run_backtest` to raise `HTTPException(400)` on one combo → assert `skipped=1`, results still returned
4. **500 re-raise** — Patch `run_backtest` to raise `HTTPException(500)` → assert endpoint returns 500 (not 200 with `skipped=N`)
5. **Non-HTTP exception** — Patch `run_backtest` to raise `ValueError` → assert `skipped=1` (not full abort)
6. **Timeout** — Patch `run_backtest` with delay, set low deadline → assert `timed_out=True` in response
7. **Metric sort correctness** — Verify sort order for each of `sharpe_ratio`, `total_return_pct`, `win_rate_pct`

Note: verify `backend/tests/` directory exists; create if needed.

---

### Phase C: Frontend trading fixes

**Targets:** `BotControlCenter.tsx`, `useTradingQueries.ts`, `AccountBar.tsx`
No file overlap with Phase D.

#### C1: Drag-reorder optimistic update (R4)

File: `frontend/src/features/trading/BotControlCenter.tsx`

The migration removed the `setBots(prev => [...prev])` call that forced React to recompute `orderedBots` from the already-updated `orderRef.current`. Without it, cards snap back to server order until the round-trip completes.

Fix: After setting `orderRef.current = newOrder`, immediately update the React Query cache:
```tsx
const qc = useQueryClient()  // at component top level

// In handleDragEnd, after orderRef.current = newOrder:
qc.setQueryData(['bots'], (old: BotSummary[] | undefined) => {
  if (!old) return old
  const map = new Map(old.map(b => [b.bot_id, b]))
  return newOrder.map(id => map.get(id)).filter(Boolean) as BotSummary[]
})
reorderBots(newOrder).then(() => invalidateBots()).catch(() => {})
```

`qc.setQueryData` triggers an immediate re-render with the reordered list. The `.then(invalidateBots)` syncs with server state after.

#### C2: botsError banner dismiss (R5)

File: `frontend/src/features/trading/BotControlCenter.tsx`

Add state + reset effect:
```tsx
const [botsErrorDismissed, setBotsErrorDismissed] = useState(false)
useEffect(() => { if (!botsError) setBotsErrorDismissed(false) }, [botsError])
```

Gate banner on `{botsError && !botsErrorDismissed && ...}`. Add dismiss button matching the existing `error` banner's pattern.

#### C3: Adaptive interval for account/orders (R9)

File: `frontend/src/shared/hooks/useTradingQueries.ts`

`useAccountQuery` (line 52): add `const qc = useQueryClient()`, change `refetchInterval: 30_000` → `refetchInterval: () => adaptiveMs(qc, 30_000)`

`useOrdersQuery` (line 62): same change.

All 5 hooks will then use `adaptiveMs()`, backing off to `max(normalMs, 10_000)` when any broker is unhealthy.

#### C4: useBotsQuery AbortSignal (R14)

File: `frontend/src/shared/hooks/useTradingQueries.ts`

Change `queryFn: listBots` to `queryFn: ({ signal }) => listBots(signal)`. Verify that `listBots` in the API client accepts an optional `AbortSignal` parameter — if it uses `fetch()` internally, pass `{ signal }` to the fetch options. If it doesn't support signals, skip this fix (P3, narrow impact).

#### C5: AccountBar error detail (R15)

File: `frontend/src/features/trading/AccountBar.tsx`

Destructure `error` from `useAccountQuery()`. Change error display from static `'Account error'` to `Account error: ${(error as Error)?.message ?? 'unknown'}`.

---

### Phase D: Frontend optimizer fixes

**Targets:** `OptimizerPanel.tsx`, `SensitivityPanel.tsx`, new `paramOptions.ts`
No file overlap with Phase C.

#### D1: Extract buildParamOptions to shared module (R8)

New file: `frontend/src/features/strategy/paramOptions.ts`

Move from OptimizerPanel.tsx:
- `ParamOption` interface
- `buildParamOptions(req, defaultSteps?)` — add optional `defaultSteps` param (default `9` for sweep compatibility)
- `linspace(min, max, steps)` function

Update both files:
- `OptimizerPanel.tsx`: import, call `buildParamOptions(lastRequest, 5)`
- `SensitivityPanel.tsx`: import, call `buildParamOptions(lastRequest, 9)` (or just `buildParamOptions(lastRequest)` with default)

#### D2: Reset paramRows on strategy change (R6)

File: `frontend/src/features/strategy/OptimizerPanel.tsx`

Add useEffect after the `paramOptions` memo:
```tsx
useEffect(() => {
  const opts = buildParamOptions(lastRequest, 5)
  setParamRows([{ path: opts[0]?.path ?? '', min: '', max: '', steps: '5' }, null, null])
  setResult(null)
  setError('')
}, [lastRequest])
```

Computes fresh options inline to avoid stale memo ordering issues. Clears results and error too since they belong to the previous strategy.

#### D3: colColor type narrowing (R13)

File: `frontend/src/features/strategy/OptimizerPanel.tsx`

Change signature from:
```tsx
const colColor = (value: number, key: keyof OptimizerCombo) => {
```
to:
```tsx
type MetricKey = 'total_return_pct' | 'sharpe_ratio' | 'win_rate_pct' | 'max_drawdown_pct'
const colColor = (value: number, key: MetricKey) => {
```

Remove `as number` cast from `result.results.map(r => r[key] as number)` — now unnecessary since all `MetricKey` values are `number` fields.

#### D4: Timeout warning in UI (R2 frontend)

File: `frontend/src/features/strategy/OptimizerPanel.tsx`

After the results table, add conditional warning:
```tsx
{result?.timed_out && (
  <div style={{ color: '#f0883e', fontSize: 11, padding: '4px 8px' }}>
    Optimizer timed out after 60s — showing partial results ({result.completed} of {result.total_combos} combos)
  </div>
)}
```

Update the `OptimizeResponse` TypeScript type to include `timed_out?: boolean`.

## Parallelism Map

```
Phase A (backend optimizer)  ──┐
Phase B (backend tests)      ──┤  4 parallel agents, zero file overlap
Phase C (frontend trading)   ──┤
Phase D (frontend optimizer) ──┘
```

File ownership per phase:
- **A**: `backend/routes/backtest_optimizer.py`
- **B**: `backend/tests/test_backtest_optimizer.py` (new)
- **C**: `BotControlCenter.tsx`, `useTradingQueries.ts`, `AccountBar.tsx`
- **D**: `OptimizerPanel.tsx`, `SensitivityPanel.tsx`, `paramOptions.ts` (new)

## Deferred

| Finding | Reason |
|---------|--------|
| P2 #10: `_apply_param` ignores regime rules (`long_buy_rules`/`short_buy_rules`) | Pre-existing from sweep module. Requires design decisions across `backtest_sweep.py`, `backtest_optimizer.py`, `OptimizerPanel.tsx`, `SensitivityPanel.tsx`. Track as new TODO item. |
| P3: Journal shared at 5s vs old 60s | Advisory — intentional dedup tradeoff. No action. |
| P3: handleClose skips journal invalidation | Advisory — 5s cache TTL limits the window. No action. |
| P3: adaptiveMs cold cache default | Correct conservative fallback (polls at normal speed until broker data arrives). No action. |
| P3: NONE_PATH sentinel magic string | Low impact, cosmetic. No action. |

## Verification

After all phases:
1. `npm run build` — must pass (not `tsc --noEmit`)
2. `python3 -c "import ast; ast.parse(open('backend/routes/backtest_optimizer.py').read())"` — syntax OK
3. `cd backend && python3 -m pytest tests/test_backtest_optimizer.py -v` — all tests pass
4. Grep checks:
   - `grep "model_copy" backend/routes/backtest_optimizer.py` — should not appear
   - `grep "adaptiveMs\|refetchInterval" frontend/src/shared/hooks/useTradingQueries.ts` — all 5 hooks use adaptive
   - `grep "buildParamOptions" frontend/src/features/strategy/OptimizerPanel.tsx` — imported, not defined locally
5. Visual verification needed (flag if no browser): drag-reorder snap, error banner dismiss, optimizer param reset on strategy switch, timeout warning

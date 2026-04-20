# A4: Indicator System Redesign — Implementation Recap

**Date:** 2026-04-20
**Sessions:** 4 (planning + review, then 3 implementation sessions)
**Commits:** 9 implementation + 2 docs

## What changed

Replaced the hardcoded indicator system (fixed MACD/RSI/EMA/BB/Volume toggle booleans, per-indicator effects in Chart.tsx, flat GET endpoint) with a unified instance-based model where users can add, remove, configure, and stack multiple instances of any indicator type.

## Architecture before

- `activeIndicators: IndicatorKey[]` — flat union type (`'macd' | 'rsi' | 'ema' | ...`)
- Chart.tsx had ~300 lines of per-indicator effects: separate refs, containers, data memos, and chart lifecycle for MACD, RSI, Volume, EMA, BB, MA
- GET `/api/indicators/{ticker}?indicators=macd,rsi` — monolithic endpoint computing all indicators inline
- Sidebar had hardcoded toggle checkboxes per indicator type

## Architecture after

- `IndicatorInstance[]` — each instance has `{id, type, params, enabled, color, pane}`
- `INDICATOR_DEFS` registry in `shared/types/indicators.ts` — defines params, defaults, pane assignment, and `subPaneSharing` ('shared' vs 'isolated') per type
- `SubPane.tsx` — generic component that creates its own `IChartApi`, registers in a `PaneRegistry`, handles crosshair/range sync and cleanup
- `PaneRegistry` (`Map<string, {chart, series}>`) — sub-panes register on mount, deregister on cleanup; all sync handlers iterate the registry dynamically
- POST `/api/indicators/{ticker}` — receives instance list, delegates to `compute_instance()` registry in `backend/indicators.py`
- `IndicatorList.tsx` — dynamic add/remove/configure UI with inline param editing

## Files changed

| File | Change |
|------|--------|
| `frontend/src/shared/types/indicators.ts` | **New.** `IndicatorType`, `IndicatorInstance`, `INDICATOR_DEFS`, `createInstance()`, `paramSummary()` |
| `frontend/src/shared/types/index.ts` | Removed old types (`IndicatorKey`, `IndicatorData`, `MACDData`, etc.). Updated `AppState` |
| `frontend/src/shared/hooks/useOHLCV.ts` | Added `useInstanceIndicators` (POST-based). Removed old `useIndicators` (GET-based) |
| `frontend/src/features/sidebar/IndicatorList.tsx` | **New.** Dynamic indicator list with add menu, inline param editing, enable/remove per instance |
| `frontend/src/features/sidebar/Sidebar.tsx` | Replaced hardcoded indicator toggles with `<IndicatorList>`. Added collapsible Indicators + Compare sections |
| `frontend/src/features/chart/chartUtils.ts` | **New.** `toLineData()` helper shared by Chart.tsx and SubPane.tsx |
| `frontend/src/features/chart/SubPane.tsx` | **New.** Generic sub-pane: chart lifecycle, series creation (MACD/RSI/line), data application, crosshair sync, markers, cleanup |
| `frontend/src/features/chart/Chart.tsx` | Major refactor: removed ~300 lines of per-indicator effects, added `PaneRegistry`, `subPaneGroups` memo, generic main overlay loop, `<SubPane>` rendering |
| `frontend/src/App.tsx` | Switched from `activeIndicators` to `indicators: IndicatorInstance[]`. Gated indicator fetching on `chartEnabled` |
| `backend/indicators.py` | **New.** `compute_instance()` registry dispatching to type-specific compute functions |
| `backend/routes/indicators.py` | Added POST endpoint. Removed legacy GET endpoint (115 lines) |
| `backend/signal_engine.py` | Extracted reusable compute functions consumed by new `indicators.py` |

## Diffstat

- **Frontend:** 791 insertions, 552 deletions across 9 files
- **Backend:** ~190 insertions, ~150 deletions across 3 files
- **Net:** ~280 lines added (but much more capable — old system was ~6 indicators hardcoded, new system is extensible)

## Commits

```
9e5ce73 feat(A4): add indicator registry, instance types, POST endpoint, and useInstanceIndicators hook
ee3f7a0 feat(A4): replace scattered indicator state with IndicatorInstance[] in App/Sidebar
92dc377 feat(A4): replace hardcoded indicator panes with generic SubPane component
1f05b7f feat(A4): make Indicators and Compare sidebar sections collapsible
c2e84cb feat(A4): skip indicator fetching when chart is disabled
e88e0f7 chore(A4): remove legacy GET /api/indicators endpoint
1067772 fix(A4): sync sub-pane range after data load, not on empty chart
b4b44bf fix(A4): show trade markers on all sub-panes, not just the first
```

## Task breakdown

| # | Task | Session |
|---|------|---------|
| 1 | Define `IndicatorInstance` type and `INDICATOR_DEFS` registry | 1 |
| 2 | Backend `compute_instance()` + POST endpoint | 1 |
| 3 | `useInstanceIndicators` hook | 1 |
| 4 | `IndicatorList.tsx` sidebar component | 2 |
| 5 | Wire App.tsx + Sidebar to new model, remove old state | 2 |
| 6 | Chart.tsx refactor — SubPane + PaneRegistry (largest task) | 3 |
| 7 | Verify strategy save/load compatibility | 4 |
| 8 | Collapsible sidebar sections | 4 |
| 9 | Gate indicator fetching on chartEnabled | 4 |
| 10 | Remove legacy GET endpoint | 4 |

## Bugs found and fixed during implementation

- **`npx tsc --noEmit` checking nothing:** Root `tsconfig.json` has `"files": []` with project references. Must use `npx tsc --noEmit -p tsconfig.app.json`.
- **Sub-pane range desync on param change:** `setVisibleLogicalRange` in Effect 1 fired on an empty chart (no data yet), silently no-oping. Fixed by moving the sync to the end of Effect 2 after `setData()`.
- **Trade markers missing on RSI:** Markers were gated on `idx === 0` (first sub-pane), which was typically MACD. Fixed by passing markers to all sub-panes.
- **`verbatimModuleSyntax` re-export gotcha:** `export type { X } from './y'` doesn't bring X into local scope. Need separate `import type` + `export type`.

## Key design decisions

1. **Two-effect split in SubPane:** Effect 1 creates chart/series (keyed on `instancesKey`), Effect 2 calls `setData()` (keyed on data changes). Avoids teardown flicker when only data changes.
2. **`subPaneSharing: 'shared' | 'isolated'`:** RSI instances share one pane (overlaid), MACD instances each get their own pane. Controlled per-type in `INDICATOR_DEFS`.
3. **`syncWidthsRef` pattern:** Ref-to-function instead of useCallback — immune to dependency creep, all sync handlers read it dynamically.
4. **Kept `MAType` and `MASettings`:** Plan said remove, but StrategyBuilder still uses them for rule configuration.

## Deferred to future work

- Resizable, collapsible, double-click-to-maximize individual chart panes
- Drag-to-reorder indicator list
- Searchable/categorized "+ Add" dropdown
- VWAP, Stochastic indicator implementations
- B4 signal markers using instance ID bridge

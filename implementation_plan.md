# Resizable Workspace Layout — Execution Plan

## Architecture Overview

```
Root (flex column, 100vh)
├── Header (fixed 56px)
└── Body (flex row, flex: 1, overflow: hidden)
    ├── [PanelGroup horizontal]
    │    ├── [Panel] Left Sidebar  ← Collapsible, defaultSize=16
    │    ├── [PanelResizeHandle]
    │    ├── [Panel] Center Column  ← flex: 1
    │    │    └── [PanelGroup vertical]
    │    │         ├── [Panel] Chart Area  ← defaultSize=60, minSize=20
    │    │         ├── [PanelResizeHandle]
    │    │         └── [Panel] Bottom Pane ← defaultSize=40, collapsible
    │    │              ├── BUY rules + SELL rules + Run button (from StrategyBuilder)
    │    │              └── Results tabs (existing Results.tsx)
    │    ├── [PanelResizeHandle]
    │    └── [Panel] Right Sidebar ← Settings only, collapsible, defaultSize=20
```

## What Moves Where

### Right Sidebar (NEW `SettingsPanel.tsx`)
Extract all Settings JSX + state from `StrategyBuilder.tsx`:
- Capital & Fees group
- Risk Management group (Stop Loss, Trailing Stop, Dynamic Sizing)  
- Execution group (Trading Hours)

State remains **inside `StrategyBuilder`** — the Settings UI is "lifted" via props. This means **no data flow changes** to the backtest logic; only the rendering location changes.

### Bottom Pane (existing `StrategyBuilder.tsx`, trimmed)
Keeps only:
- BUY when rules panel
- SELL when rules panel
- Run Backtest button + Signal Trace checkbox + error message

### Results (existing `Results.tsx`, unchanged)
Rendered directly below `StrategyBuilder` in the bottom pane, as now.

## Files Touched

| File | Change |
|---|---|
| `App.tsx` | Replace manual flex layout with `<PanelGroup>` + `<Panel>` structure |
| `StrategyBuilder.tsx` | Remove Settings JSX; pass settings state down via props to new component |
| `SettingsPanel.tsx` [NEW] | Receives settings props, renders the 3-column settings groups |
| `index.css` | Add `.resizeHandle` + `.collapseBtn` CSS classes for drag handles and collapse toggles |

## Collapse Behavior
- Left Sidebar: collapse button `‹` on the resize handle  
- Right Sidebar: collapse button `›` on the resize handle  
- Bottom Pane: collapse button `▾` — hide when you want full chart view  

Each panel uses `collapsible={true}` and `collapsedSize={0}` from `react-resizable-panels`. Collapse state persisted in `localStorage` so it's remembered on page reload.

## Resize Handle Styling

Custom `PanelResizeHandle` rendered as a thin `4px` vertical/horizontal bar with:
- `background: var(--border-light)` at rest
- `background: var(--accent-primary)` on hover/drag
- Collapse arrow buttons overlaid at the midpoint of each handle

## Key Risks / Decisions

> [!IMPORTANT]
> The Settings state (capital, posSize, stopLoss, trailingConfig, etc.) **stays in `StrategyBuilder`** and is passed as props to `SettingsPanel`. This is the simplest change — no refactor of `runBacktest()` or localStorage persistence needed.

> [!NOTE]
> `Results.tsx` currently has a hardcoded `height: 220`. This will be removed and replaced by `flex: 1` so it fills the panel properly when resized.

> [!NOTE]
> The existing `Sidebar.tsx` has a fixed `width: 260, minWidth: 260` in its inline styles. These will be removed so that `react-resizable-panels` controls its width.

## Execution Order
1. Add CSS for resize handles + collapse buttons to `index.css`
2. Create `SettingsPanel.tsx`
3. Modify `StrategyBuilder.tsx` — extract Settings JSX, add SettingsPanel props interface
4. Rewrite `App.tsx` layout with `PanelGroup`
5. Fix `Results.tsx` hardcoded height
6. Fix `Sidebar.tsx` hardcoded width

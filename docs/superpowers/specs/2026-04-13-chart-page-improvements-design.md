# Chart Page Improvements — Design Spec

## Overview

Three enhancements to the chart page: date range presets with period stepping, normalised B&H comparison on the equity curve, and a log scale toggle.

## Feature 1: Date Range Presets + Stepping

### UI

The sidebar Date Range section replaces the always-visible From/To inputs with a preset-first layout:

```
DATE RANGE
┌────────────────────────────┐
│  < │ D W M Q Y Custom │ >  │
└────────────────────────────┘
Interval: [1d ▾]
```

- **Preset row**: segmented toggle — D / W / M / Q / Y / Custom
- **Arrow buttons** `<` `>` flank the preset row
- Selecting a **preset** computes the range relative to the current `end` date:
  - D = 1 calendar day
  - W = 7 days
  - M = 1 calendar month
  - Q = 3 calendar months
  - Y = 1 calendar year
- Selecting **Custom** reveals From/To date inputs below the preset row
- **Interval dropdown** stays always visible below

### Stepping behaviour

- Arrows shift the entire window by its duration (non-overlapping)
  - Example: Jan 1–31 → `>` → Feb 1–28
- Works in both preset and custom mode
  - In custom mode, shift duration = current range's length in days
- `>` is disabled when `end >= today` (can't step into the future)
- After stepping, `start` and `end` update; the active preset stays the same

### State

- New state in App.tsx: `datePreset: 'D' | 'W' | 'M' | 'Q' | 'Y' | 'custom'`
- Default: `'Y'` (matches current 1-year default range)
- `start` and `end` remain the source of truth — presets compute and set them
- Editing From/To inputs in custom mode updates `start`/`end` directly
- Persisted to localStorage alongside existing settings
- Switching interval does not change the preset; switching preset does not change the interval

### Sidebar props

New props on Sidebar:
- `datePreset: DatePreset`
- `onDatePresetChange: (preset: DatePreset) => void`

Step logic lives in the Sidebar component — it calls `onStartChange` / `onEndChange` with the computed dates.

## Feature 2: Equity Curve — Normalised B&H Comparison

### UI

Two small toggle buttons in the equity curve tab bar area, visible whenever the Equity Curve tab is active (both Detail and macro bucket modes):

```
Summary | Equity Curve | Trades (12)          Detail D W M Q Y
                                                [B&H] [Log]
```

Replaces the existing "Show buy & hold baseline" checkbox.

### Normalisation logic (B&H toggle on)

- Both equity curve and baseline curve are rebased to start at 0%:
  `value_pct = ((value - first_value) / first_value) * 100`
- Y-axis shows percentage values (e.g. +12.3%, -5.2%)
- BaselineSeries `baseValue` becomes `0` (breakeven line)
- Crosshair tooltip shows both % and dollar:
  `Strategy: +12.3% ($11,230) | B&H: +8.1% ($10,810)`

### B&H toggle off

Existing behaviour — dollar values, no baseline curve, no normalisation.

### MacroEquityChart

MacroEquityChart receives `showBaseline` and `logScale` props and applies the same normalisation/log logic as the Detail equity chart.

## Feature 3: Log Scale Toggle

### Behaviour

- Independent toggle, works with or without B&H
- All four combinations valid: plain, B&H, log, B&H+log

### Log mode implementation

- **Dollar mode (B&H off)**: apply `Math.log10(value)` to equity values. Y-axis labels reverse the log for display via `priceFormat` formatter.
- **Normalised mode (B&H on + log)**: offset percentage values to avoid log of negatives: `log10(100 + pct)`. Y-axis labels show the original percentage.

### State

- `showBaseline`: already exists in Results, moves from checkbox to toggle button
- `logScale: boolean`: new, default false, local to Results component
- Neither persisted to localStorage (viewing preferences, not settings)

## Files to modify

- `frontend/src/features/sidebar/Sidebar.tsx` — preset row, arrows, collapsible From/To
- `frontend/src/App.tsx` — `datePreset` state, persistence, new Sidebar props
- `frontend/src/features/strategy/Results.tsx` — B&H/Log toggles, normalisation logic, log transform
- `frontend/src/features/strategy/MacroEquityChart.tsx` — accept and apply B&H/log props
- `frontend/src/shared/types/index.ts` — `DatePreset` type

## No backend changes required

All three features are purely frontend. The backend already provides `baseline_curve` and `equity_curve` with the data needed.

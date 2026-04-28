# Post-Mortem: D10 Compact Sparkline Alignment

**Duration:** ~1 hour across 2 sessions (prev session + this one)  
**Commits:** 4 (1 reverted in prev session, 3 in this session)  
**Iterations before fix:** 6+  
**Root cause identification time:** ~55 minutes  
**Actual fix:** 2 lines of structural change

## What happened

The D10 compact mode for bot cards was shipped via a background subagent. It worked but the sparkline charts were misaligned across rows. What should have been a 5-minute CSS fix turned into a multi-session saga.

## The wrong mental model

Every failed attempt shared the same flawed assumption: **the problem was about sizing individual flex items correctly.** Each iteration tweaked which items got fixed widths, flexible widths, or caps:

| Attempt | Change | Result |
|---------|--------|--------|
| 1 | Cap name at 220px | Still misaligned (P&L and status still variable) |
| 2 | Cap name at 280px, no flex stretch | Marginal improvement, still broken |
| 3 | Remove spacer, name flex:1 | Name ate all space, sparkline squeezed to 80px on far right |
| 4 | Fixed-width columns (P&L 140px, status 55px) | Same as #3 — name flex:1 dominated |
| 5 | Sparkline flex:1, text content-width | Sparklines different widths per row |
| 6 | Sparkline flex: 0 0 60% | Still misaligned — 60% basis correct, but START position varied |

## The actual problem

The compact row was a **flat flex container** with all items as siblings:

```
[drag] [dot] [name] [P&L] [status] [buttons] [sparkline]
```

In this layout, the sparkline's x-position = sum of all preceding items' widths + gaps. Since text content varies per row, each sparkline started at a different position. No amount of individual item sizing fixes this — **the structure was wrong.**

## The fix

The expanded mode already solved this with a **two-column layout**:

```
[LEFT COLUMN: flex 1] [RIGHT COLUMN: flex 0 0 60%]
```

The left column absorbs all variable-width content. The right column (sparkline) always starts at exactly 40% of the container width. Applied the same structure to compact mode:

```jsx
{/* Left column — text info */}
<div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
  {/* drag, dot, name, P&L, status, buttons */}
</div>
{/* Right column — sparkline */}
<div style={{ flex: '0 0 60%', height: 24 }}>
  <MiniSparkline ... />
</div>
```

## Why it took so long

1. **Didn't study the working solution first.** Expanded mode had the answer from the start. Instead of asking "what does the working layout do differently?", each attempt tried to invent a new fix from scratch.

2. **Couldn't visually verify.** The sandbox can't take screenshots. Each iteration required the user to refresh, screenshot, and report back. A feedback loop that should be <1 second was ~2 minutes. Six iterations = 12 minutes of just waiting.

3. **Focused on properties, not structure.** Tweaking `flex-shrink`, `flex-basis`, `max-width`, and `gap` on items within a flat flex row. Never questioned whether a flat flex row was the right structure. The fix wasn't a property change — it was wrapping items in a column container.

4. **Planned against the wrong root cause.** The first "plan -> review -> refine" cycle produced a detailed, internally-consistent plan for fixed-width columns. The plan reviewed well because it was logically sound. It was also completely wrong because it addressed symptoms (variable item widths) rather than the structural issue (flat vs nested flex layout).

## Bonus wins along the way

- **Overflow menu z-index fix:** `scale: '1'` on idle SortableBotCard wrappers created stacking contexts that trapped dropdown z-index. Fixed by setting `scale: undefined` when not dragging.
- **Inline buttons:** With the two-column layout providing ample space, the overflow dropdown was replaced with inline action buttons (Backtest, Stop, Buy, Reset, Delete). Simpler code, better UX. Net -29 lines.
- **Direction badge inside name span:** Moved the conditional short "S" badge inside the name container so the flex child count stays constant across rows (eliminates gap-count variation).

## Lessons

1. **When something works elsewhere, study it before inventing.** The expanded mode sparkline alignment was right there. "Match the working pattern" beats "design a new fix."

2. **Structure > properties.** If you're on your third round of tweaking flex properties, step back and ask if the DOM structure is wrong. A wrapping `<div>` is often the entire fix.

3. **CSS alignment across independent containers requires structural guarantees.** You can't align columns across separate flex rows by sizing individual items. You need either a shared grid, a table, or nested containers where the aligned element's position is determined by the parent — not by its siblings.

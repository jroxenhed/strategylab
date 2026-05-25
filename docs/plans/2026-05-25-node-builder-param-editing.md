# 2026-05-25 — NodeBuilder: parameter editing (next session)

**Branch:** `feat/node-strategy-builder-t1-t2`
**Prior context:** JOURNAL 2026-05-25 entries; UX is in a good place (perf,
live drag, cursors, selection ring, wire-to-empty-space → TabMenu). Params are
**displayed** on nodes (RSI shows period etc.) but **not editable** yet.

## Goal

User can edit a node's params and have them flow through the graph backtest.

## Non-goals (defer)

Undo/redo, multi-select batch editing, per-input validation polish, backend
schema changes.

## Decisions to make in-session (recommendations bolded)

**D1 — Inline-on-node vs Inspector panel?**
- **Hybrid (recommend):** short scalars inline (period, threshold), inspector
  for params >2 fields. Mirrors `RuleRow.tsx` which works well for compact
  numeric rows. Inspector can wait — ship inline first.
- Alternative: Inspector-only. Clean but adds a click for every edit; loses
  spatial proximity.

**D2 — Where validation lives?**
- **Component-side coercion (recommend):** Number/trim in the input, store op
  accepts any value, backend graph validation is the real authority (run
  surfaces errors). Matches how the rest of the app's form inputs work.
- Alternative: schema in `operations.ts`. More work, duplicates backend
  knowledge.

**D3 — Auto-rerun on edit, or keep manual "Run Backtest"?**
- **Manual (recommend):** the existing Run Backtest button stays. Auto-rerun
  on multi-second graphs is bad UX.

## Implementation sequence (each step ships independently)

1. **`updateNodeParams(nodeId, partial)` op** in `operations.ts` — mirror
   `moveNode` pattern: readOnly guard, return new Graph, single test in
   `operations.test.ts`.
2. **Wire into `store.ts`** — mirror `moveNode` (call op, `set({graph, graphHash})`).
3. **Inline numeric field on `IndicatorNode`** with RSI period as the canary.
   Number input committed onBlur, ESC reverts; **`className="nodrag"` on every
   input** — React Flow will hijack pointer events otherwise (Sidebar's
   onBlur-commit inputs are precedent).
4. **Verify in browser** that input doesn't fight RF drag. If it does, the per-input
   class isn't enough — wrap the param row in a `<div className="nodrag nopan">`.
5. **Apply per renderer** — `TickerNode` (symbol/interval), `IndicatorNode`
   (per-indicator shapes from catalog), `ComparisonNode` (threshold), etc.
   Derive input types from `NODE_CATALOG[].defaults.params` shape.
6. **Inspector panel (optional, defer until 5 lands)** — selected-node form on
   the right side of the canvas when params don't fit inline.

## Risks / gotchas

- **RF + form inputs:** `.nodrag` / `.nopan` classes are required to keep RF
  from grabbing pointer events. Verify before scaling beyond the first input.
- **Per-node memo sig already includes `JSON.stringify(n.params)`** — param
  changes correctly invalidate the cache for ONLY that node, so editing one
  param re-renders one node. No further perf work needed.
- **Saved graphs persistence:** `store.saveCurrentGraph()` serializes the
  whole `Graph` including `nodes[*].params`. Editing should "just work" for
  save/load; verify with a roundtrip.
- **Catalog `defaults.params` is the source of truth for shape:** don't
  invent param schemas in renderers; read from catalog.

## Open questions for the user

1. Inline-first then inspector? Or jump straight to inspector?
2. Should saved-graph load round-trip preserve params (almost certainly yes —
   verify don't assume)?
3. Any param-validation messaging UX preference (red border? inline message?
   silent revert?), or defer entirely?

## References

- `frontend/src/features/nodebuilder/store.ts:179` — `moveNode` op template
- `frontend/src/features/nodebuilder/operations.ts` — readOnly guard pattern
- `frontend/src/features/nodebuilder/nodes/IndicatorNode.tsx` — start here
- `frontend/src/features/nodebuilder/catalog.ts` — param shape source of truth
- `frontend/src/features/strategy/RuleRow.tsx` — compact param row idiom precedent
- `frontend/src/features/sidebar/Sidebar.tsx` — onBlur-commit input pattern

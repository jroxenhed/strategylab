# Node Strategy Builder — Next-Session Handoff

**Branch:** `feat/node-strategy-builder-t1-t2` (17 commits, not yet merged).

## State at end of 2026-05-23

T1 + T2 implementation is complete and review-passed. All seven correctness-review findings (F1–F7) addressed. Edit-mode perf passes done (memoisation + onMoveEnd + node-renderer React.memo).

**Tests:** 667 backend pass (167 nodebuilder + 500 other), 2 intentional skips (regime + b23 parity per plan). 55 frontend nodebuilder tests pass. `npm run build` clean.

**Plan refs:**
- Plan: `docs/plans/2026-05-22-node-strategy-builder-t1-t2.md`
- Original handoff: `docs/plans/2026-05-22-node-strategy-builder-HANDOFF.md`

## What's left to actually ship T2

Per the plan's exit gates, two structurally user-driven items remain:

1. **T1 affirmation gate.** Spend ≥15 min in Graph View on ≥5 saved strategies (incl. one not edited 30+ days). Affirm in JOURNAL with one sentence per strategy: *"Reading this graph is faster than re-reading the rule list, and I would prefer to author a new strategy like X here rather than in the rule builder."* Both clauses required.
2. **T2 ship gate.** User-built ≥5-node strategy on the canvas + a graph-mode paper bot ran ≥1 trading day with no evaluator errors. Document in JOURNAL.

Until those two are green, the plan says don't start T3.

## Known issues to chase next session (from interactive testing)

These surfaced when the user actually exercised edit mode after Unit 6 + perf-pass-1 shipped:

- **Sluggishness in edit mode persists** even after the Canvas memoisation + `onMoveEnd` + `React.memo` round. Suspects to chase:
  - Zustand store selector granularity — `CanvasInner` subscribes to ~7 slices; consider a single `useShallow` selector or splitting into smaller leaf components.
  - The `data` payload identity is rebuilt by `useMemo` on every `graph.nodes` change — even a single-node move invalidates ALL node data refs because `Object.values(graph.nodes)` returns a new array. Try a finer-grained per-node memo (build `data` per id, cache by id, only invalidate the changed id).
  - React Flow's own internal node store may be doing extra work — try `onNodesChange` / `onEdgesChange` with `applyNodeChanges` instead of recomputing rfNodes from graph every render.
  - Profile via React DevTools Profiler to confirm which components are re-rendering and why.
- **Still some bugs in edit mode** (user-reported, not pinned to a specific repro yet). Next session should drive through the Tab-add → port-drag → delete → backtest flow with a screen recording or systematic test plan, then file specific repros.

## Polish that didn't ship

- **"Edit a copy of this auto-rendered graph" flow.** Currently the read-only viewer and the editable canvas are separate entry points (`View as Graph` vs `New Empty Graph`). Add a button on the auto-rendered view that clones the graph into the store with `readOnly=false` so the user can use auto-render as a starting point.
- **Multi-output port-level wire creation.** At T2, `onConnect` derives wire.attr from `catalog.writes[0]` (the primary). For MACD/Bollinger, the user can't choose `@macd_signal` vs `@macd_line` from the UI yet — they'd have to hand-edit the saved graph JSON. The compile-side F1 fix already honors the named sub-attr at evaluation time; UI just needs port-level handles.
- **Cross-bar comparisons on derived booleans** are compile-rejected (F2) with a clear error, but the UI doesn't surface the error inline yet — it lands as a 400 from `/backtest`. Add a precompile/validate hook that previews errors before the user clicks Run Backtest.

## Deferred follow-ups (review findings, no trading-correctness impact)

None of these block T2 ship; they're tracked here so they don't get lost:

- **Frontend ergonomic polish** the Opus reviewer didn't flag but the user implicitly will: density toggle (Atom/Standard/Rich), the Stream Inspector (T3 per plan), and a Save Graph button in edit mode that writes to `strategylab-saved-graphs` localStorage.

## How to resume next session

```
git checkout feat/node-strategy-builder-t1-t2
./start.sh                # backend on :8000, frontend on :5173
```

1. Read this doc + JOURNAL's 2026-05-23 entry.
2. Pick one of: drive the T1 gate, profile + fix remaining edit-mode perf,
   or file repro-able edit-mode bugs.
3. After T1 + T2 gates are green, this branch is ready to PR + merge.

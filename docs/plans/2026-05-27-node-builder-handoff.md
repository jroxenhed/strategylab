# 2026-05-27 — NodeBuilder handoff: state after the param-editing chain

**Branch:** `feat/node-strategy-builder-t1-t2` (unmerged, ahead of main by ~17 commits)
**Prior handoff:** [2026-05-25 — param editing plan](2026-05-25-node-builder-param-editing.md). Everything in that doc is now shipped — this doc supersedes it for "what next".

## What's done

The inline-editing pipeline is feature-complete and end-to-end verified:

- **F268** — `updateNodeParams(nodeId, partial)` pure op + Zustand store wiring + inline editable rows on `IndicatorNode`. Blur-commits via `e.target.value` (immune to React batching), Enter blurs, ESC reverts.
- **F269 + F270** — `ParamRows`/`ParamRow` extracted to `nodes/ParamRow.tsx`, wired into TickerNode + ComparisonNode + SettingsNode. LogicNode + OutputNode have no params (intentionally skipped).
- **F271** — Typed param schema: optional `paramTypes?: Record<string, ParamTypeSpec>` on `NodeCatalogEntry`. `<select>` rendered for enum-typed keys. Shared `INTERVAL_OPTIONS` + `SOURCE_OPTIONS` lists in `catalog.ts`. Unknown legacy values preserved as the first option on load.
- **F273 + F278** — Invalid numeric input shows red border (`--nb-cat-rules`) + `title="Must be a number"` tooltip. Inputs use `type="text" inputMode="decimal"` (was `type="number"` which clobbered bad chars at the DOM layer before our commit() could see them).
- **F274** — `loadFromAutoRender` strips `/regime/*` nodes + incident wires. T2 graph evaluator rejects them; WFA already does the same at its boundary. **Unblocked the first 200 OK from `/api/nodebuilder/backtest` this branch.**
- **F275** — Empty-string numeric input reverts to initial instead of committing `Number('') === 0`.
- **F276 + F277** — `paramTypes` filled in for every catalog entry whose params include numbers; `catalog.test.ts` enforces 3 drift-guard assertions (every paramTypes key is in defaults.params; every select has non-empty options; every numeric default is explicitly typed). 17/17 catalog tests pass.

Browser-verified through `chrome-devtools-mcp` for each item — the standard "edit via fiber-walked store action → Run Backtest → inspect POST payload + 200" cycle. End-to-end smoke: change ticker symbol, change ticker interval (select), change RSI type (select), change EMA period, change comparison threshold, change settings bps → single backtest POST returns 200 with all six new values reflected.

## Branch state

```
6abfa33 F278  text+inputMode=decimal
6f1f2f5 F273+F276+F277  red border, paramTypes coverage, drift test
8073a40 F271  typed param schema, select inputs
5bf7357 F274+F275  strip /regime/, revert empty numeric
23b9dd0 F269+F270  extract ParamRow, apply to 3 more renderers
d88736d F268  inline param editing on IndicatorNode
7687439 docs: retire docs/superpowers/ + 2026-05-25 handoff
... (earlier work, T1/T2 perf + Houdini UX)
```

Build: `npm run build` clean. Tests: 28/28 operations, 17/17 catalog. No backend changes — frontend-only.

**Unmerged.** Same gates as before apply: T1 affirmation + T2 ≥1 paper-day before merging to main.

## What's next (in priority order)

### 1. **F272 — Inspector panel** (deferred — still no triggering condition)

Plan §6 from the prior handoff called for this *if* a node grew >3 params or had multi-line content. Of all nodes shipped, none triggers it:

- Max param count is 3 (ticker: symbol/interval/source, commission: rate/min, RSI: period/type).
- All values are short scalars or enums.

**Recommendation: keep deferring.** Bring it back the moment a Code-node lands (which is in the broader NodeBuilder roadmap and *will* have multi-line content).

### 2. **F251 follow-ups on `feat/polygon-provider`** (separate branch)

Independent of this branch:
- **F251b** — disk-persistent parquet OHLCV cache for Polygon (Starter tier is 5 req/min, in-memory TTL isn't enough).
- **F251c** — polygon-crypto sibling provider.
- Live verification with a real API key is still pending. The branch hasn't been touched since 2026-05-25.

Pick this up when ready to actually subscribe.

### 3. **NodeBuilder T2 → T3 graph evaluator** (the big one)

Regime support is currently stripped at the auto-render boundary (F274). The "real" fix is wiring regime support into the graph evaluator itself, which is part of T3 scope (multi-timeframe + regime gating). Until then, edit-then-run on regime-using strategies *silently loses the regime*, which is a footgun.

Two threads inside this:
- Add `/regime/` evaluation to the T2 graph evaluator (would supersede F274's strip).
- HTF (higher-timeframe) lookback at intraday boundaries — already documented as a deferred WFA limitation; same constraint applies to graph eval.

This is real work — design + plan + implement, not a one-session item.

### 4. **F-items in `[next]` / `[easy]` that don't need new design**

Skim `TODO.md` for short items unrelated to this branch when looking for batchable work. Stay on this branch for anything NodeBuilder-shaped; switch branches for the rest.

## Open follow-ups filed this session (deferred deliberately)

None except F272 above. Every other item touched ([F268–F278]) shipped before this handoff.

## Traps to avoid

- **`/regime/` strip is in `loadFromAutoRender`, not at the route.** T1 read-only viewer still shows regime nodes intentionally (visualization of the strategy's gate logic). Don't move the strip back-end-side — it would break T1.
- **Param schema lives in two places.** Frontend `catalog.ts` mirrors backend `nodebuilder/nodes.py`. The F277 drift test only catches frontend-internal drift (paramTypes key not in defaults.params). It does NOT cross-check against Python. If you add a backend param, remember to add it to the TS catalog too, then re-add `paramTypes`. (A real backend-driven schema endpoint would solve this — filed as **F277 backend-side** in the journal.)
- **ParamRow uses fiber-walked store action invocation for browser tests.** The dynamic-import path (`import('/src/features/nodebuilder/store.ts')`) does NOT see the same Zustand store instance as the running React tree under Vite HMR. Fiber-walk the `.react-flow` root, find a hook whose `memoizedState` is a function named `updateNodeParams`, invoke it directly. See JOURNAL 2026-05-25 "Verification false start" for the rationale.
- **Auto-render emits 33 nodes; 4 are regime. Edit mode shows 29.** This is correct, not a bug. The 4 missing nodes are the strip.
- **`type="text" inputMode="decimal"` is deliberate over `type="number"`.** F278 explains why. Don't revert.

## References

- `frontend/src/features/nodebuilder/operations.ts` — pure ops, `updateNodeParams` lives here
- `frontend/src/features/nodebuilder/store.ts:217` — `loadFromAutoRender` with `/regime/` strip
- `frontend/src/features/nodebuilder/nodes/ParamRow.tsx` — the inline editor
- `frontend/src/features/nodebuilder/catalog.ts` — `ParamTypeSpec`, INTERVAL_OPTIONS, SOURCE_OPTIONS, full paramTypes coverage
- `frontend/src/features/nodebuilder/__tests__/catalog.test.ts` — F277 drift guard (lines tagged "F277 — drift guard")
- `JOURNAL.md` 2026-05-25 entry — full narrative of every item in this chain
- `docs/plans/2026-05-25-node-builder-param-editing.md` — the original plan (now fully shipped; keep for history)

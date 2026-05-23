# Node Strategy Builder — Implementation Handoff

**For the next session.** Read this first, then `2026-05-22-node-strategy-builder-t1-t2.md`.

## What you're building

A Houdini-style node graph for authoring trading strategies, parallel to (not replacing) the existing rule builder. T1 = read-only viewer that auto-renders existing rule strategies. T2 = editable canvas + backtest parity + ≥1 live trading day on a graph-mode paper bot.

## Where things live

- **Plan:** `docs/plans/2026-05-22-node-strategy-builder-t1-t2.md`
- **Requirements (origin):** `docs/ideation/2026-05-17-node-based-strategy-builder-v2.md`
- **Design handoff bundle:** still at `/tmp/strategyLab_handoff/design_handoff_node_strategy_builder/` — **first thing to do is copy `Node-Editor-v2-Density.html` + `screenshots/` into `docs/design/nodebuilder/`** (Unit 0a). The bundle in `/tmp` will not survive a reboot.

## Start here

Phase 0 first. Both units are file-independent and dispatch in parallel:
- **Unit 0a** (frontend): install `@xyflow/react ^12`, `zustand ^5`, Geist fonts, copy OKLCH tokens from handoff README §"Design Tokens" into `frontend/src/features/nodebuilder/tokens.css`, copy the design assets.
- **Unit 0b** (backend): scaffold `backend/nodebuilder/` with 6 empty module stubs + empty router wired into `main.py`. Add empty pytest dir.

Verify `npm run build` + `pytest backend/tests/nodebuilder/` after Phase 0 before moving on.

## Critical non-obvious decisions baked into the plan

These survived 6 reviewer passes — don't "simplify" them away:

1. **Kernel/domain folder split is DEFERRED to T3.** Through T1+T2, flat layout: `backend/nodebuilder/{models,evaluator,nodes,compile,from_rules,api_models}.py`. The split was a premature abstraction.
2. **Auto-render lives on the backend** (`backend/nodebuilder/from_rules.py`), called by both T1 frontend (via `POST /api/nodebuilder/auto_render`) and the parity test. Single source of truth. Frontend caches via TanStack Query with `staleTime: Infinity` keyed on strategy hash.
3. **Unit 8a is a snapshot-gated refactor.** Write the snapshot test against unmodified `run_backtest` FIRST, commit fixtures, THEN extract `_run_simulation`. Comparison helper handles numpy/NaN/timestamps + `rel=1e-9` on numerics (same code path). Reversing this order is the most likely way to silently regress the rule path.
4. **R2 parity scope = `summary + trades + equity_curve + baseline_curve` only.** Rule-only debug fields (`rule_signals`, `ema_overlays`, `signal_trace`, `regime_series`) are NOT in the graph response. `1e-4` relative tolerance (cross-path), not byte-equal.
5. **`compute_indicators_from_specs` is an independent dispatcher**, not a thin wrapper. Must replicate family-cap checks + `OHLCVSeries` construction from `signal_engine.py::compute_indicators`. Reuses `compute_instance` at the leaf level only.
6. **Graph hash uses `graph.model_dump(mode="json")`** — raw `json.dumps` on a Pydantic model raises TypeError.
7. **`Graph._version >= MIN_SUPPORTED_VERSION`** (minimum floor, not equality) so additive field changes don't break saved graphs.
8. **`/regime/` paths raise `RegimeUnsupportedError` at compile time** — never silent flat-running.
9. **`_tick()` branches at TWO points** (compute_indicators + eval_rules), not one. Graph mode uses `compute_indicators_from_specs(indicator_specs, df)`.
10. **Core 14 nodes at T2**, not 40. Rest is T3.

## Orchestrator workflow reminders (CLAUDE.md)

- Always tell implementation agents "Do NOT commit or push."
- `npm run build` for frontend verification, never `tsc --noEmit`.
- Browser-verify every UI-touching unit. The plan flags which units require it.
- Use absolute paths in agent prompts (agents don't maintain cwd).
- Severity-graded review tiers apply (Tier B/C for most units; Unit 8a + 9 are Tier C).
- Update `TODO.md` + `JOURNAL.md` atomically with each commit.

## Two kill switches

- **T1 → T2 gate:** user must affirm in JOURNAL (per strategy, ≥5 strategies, ≥1 not edited in 30+ days): *"Reading this graph is faster than re-reading the rule list, and I would prefer to author a new strategy like X here rather than in the rule builder."* Both clauses required. If not affirmed, stop the plan.
- **T2 complete gate:** snapshot regression green + parity test green on 8/10 + ≥1 day live paper bot + user-built ≥5-node strategy + two-surface parity green. Any non-negotiable failure → stop, don't start T3 with debt.

## Operational pre-work for Unit 9

Back up `backend/data/bots.json` to `backend/data/bots.json.pre-graph.bak` before adding `BotConfig.kind`/`graph` fields. Pydantic defaults make existing rows safe, but back up anyway.

## What this plan does NOT cover

T3 (sub-graphs + kernel/domain refactor + Stream Inspector real impl + remaining ~26 nodes + regime evaluator), T4 (multi-Output Groups + on-canvas Size/Stop terminals wired), T5 (code surfaces / Wrangle / Monaco). Each gets its own plan when its predecessor's exit gate is green.

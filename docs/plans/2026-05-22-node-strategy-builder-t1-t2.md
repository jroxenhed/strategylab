---
title: Node-Based Strategy Builder — T1+T2 Implementation Plan
type: feat
status: active
date: 2026-05-22
deepened: 2026-05-22
origin: docs/ideation/2026-05-17-node-based-strategy-builder-v2.md
design_handoff: external — Claude Design bundle, copied into repo at Phase 0
reviewed_by: feasibility, scope-guardian, adversarial, coherence (4-persona pass, 2026-05-22)
---

# Node-Based Strategy Builder — T1+T2

## Overview

Add a procedural node-graph authoring surface to StrategyLab as a parallel track to the existing rule builder. Universal-stream + named-attribute data model translated from Houdini's SOP paradigm. This plan covers **T1** (read-only viewer that auto-renders existing rule strategies as stream graphs) and **T2** (editable canvas + minimum-viable node library + single Output Group + backtest parity + bot runner can execute a graph strategy live).

T3 (sub-graphs + full node catalog + kernel/domain refactor), T4 (multi-Output Groups + on-canvas sizing/stops), T5 (code surfaces) are out of scope for this plan but reachable from this foundation.

## Problem Frame

The rule builder works but each new feature (NOT, slope conditions, regime, per-direction overrides, parameter sprawl) makes each rule row denser. The form is starting to hide the strategy. A procedural node graph expresses non-trivial strategies more legibly — spatial structure, attribute names on wires, composable sub-expressions — and unlocks capability the rule builder can't express (multi-input rules, composable metrics, future sub-graph reuse).

Two design artifacts already exist:
- Requirements doc (origin) — architectural primitives, four authoring paths, tier rollout, kernel/domain discipline.
- Design handoff bundle — lead variant ("Stream Workspace + density"), exact tokens, interactions, pitfalls, full node catalog (~40 nodes, used at T3+).

This plan turns those into executable units.

## Requirements Trace

- **R1.** Authoring feels good — graph preferred over rule builder for non-trivial work *(success measured at T2 exit).*
- **R2.** Backtest semantics match the existing engine — no accounting drift on equivalent strategies. **Parity scope = `summary` dict + `trades` list + `equity_curve` + `baseline_curve`.** Rule-only debug fields (`rule_signals`, `ema_overlays`, `signal_trace`, `regime_series`) are absent from graph responses by design. Structural equality (trade count, entry/exit dates, direction sequence, equity-curve length, baseline-curve length) is required; numeric fields within `1e-4` relative tolerance on PnL/equity/baseline to absorb FP drift across Python/NumPy versions.
- **R3.** Live trading is first-class — `bot_runner._tick()` runs a graph strategy with no behavioral drift from backtest *(T2 exit criterion: paper-trading session ≥1 trading day).*
- **R4.** Strategy intent is legible — reading a graph is faster than reading the equivalent rule list *(T1 exit criterion: user evaluates this on a real strategy).*
- **R5.** Platform capability expands beyond the rule builder *(T2 nice-to-have; T3+ scope).*
- **R6.** Kernel/domain separation is a **T3 commitment**, not T1+T2. Through T2 we ship a flat `nodebuilder/` module; the kernel/domain split is performed as a rename-and-move PR at the start of T3 when sub-graphs make the boundary concrete. *(Adversarial-review pushback accepted: theoretical until then.)*
- **R7.** Rule builder is untouched and lives forever — parallel track, separate format, separate evaluator. CI parity check enforces that the rule-builder indicator dropdown options remain a subset of the node catalog (prevents silent drift over time).

## Scope Boundaries

- **Not migrating, replacing, or retiring** the rule builder. Two paradigms, two formats, two editors.
- **No conversion** between rule strategies and graph strategies *except* the T1 one-way auto-render (read-only, viewer-only — does not produce an editable graph).
- **No multi-symbol execution on a single bot** — Kind-2 multi-ticker is T4+ scope.
- **No sub-graph / palette graduation** — T3 scope.
- **No code surfaces** (per-param `=` mode, per-node code blocks, Wrangle nodes) — T5 scope.
- **No on-canvas Size/Stop terminals wired to the simulator at T2** — Position-size and stop-loss live as graph-level Settings nodes; Size/Stop output terminals exist in the catalog visually but compile ignores them at T2 (Unit 7a — explicit test scenario: graph with unwired Size/Stop terminals compiles without error).
- **No regime support in graph evaluator at T2.** A graph containing `/regime/` paths is a compile-time error (`RegimeUnsupportedError`), surfaced as a UI toast. T1 viewer *displays* regime sub-trees when auto-rendering existing rule strategies, but those graphs are read-only and cannot be evaluated.
- **No dynamic-parameter nodes** (e.g., "RSI with period driven by ATR series"). Compile model assumes scalar params resolved at compile time. T5 Wrangle nodes will need a different mechanism — documented in Unit 7a explicitly.
- **No HTF (higher-timeframe) attribution on graph-mode bots.** Mirrors the existing bot-runner limitation; documented in Unit 9. Graph backtest supports HTF the same way the current backtester does, since `compute_indicators` is reused.
- **Stream Inspector real implementation (cursor sync, dataframe windowing, per-bar attribute series, chart panel)** — T3 scope. T2 ships a "Select a node to inspect" placeholder.
- **No 40-node catalog at T2** — Core 14 only (see Unit 2). Rest of catalog deferred to T3.
- **No undo/redo at T2** — deferred to a T2 hardening TODO. Delete-key binding is in scope (Unit 6).

### Deferred to Separate Tasks

- Sub-graphs save/instantiate/promote (T3).
- Kernel/domain folder refactor (T3 — preceded by the rename-and-move PR).
- Stream Inspector real implementation (T3).
- Remaining ~26 catalog nodes including all Signal Processing (T3).
- Multi-Output Groups + on-canvas terminals (T4).
- Code surfaces + Monaco integration (T5).
- Live-mode visualization, Backtest-as-a-node, Regime-in-graph-evaluator (T3+).

## Context & Research

### Relevant Code and Patterns

**Backend (canonical implementations that must NOT be re-invented):**
- `backend/models.py` — `StrategyRequest`, `Rule`, `RegimeConfig`, `TrailingStopConfig`. The auto-render transform consumes this shape; `GraphBacktestRequest` mirrors simulator-level fields verbatim.
- `backend/signal_engine.py` — `eval_rules(rules, logic, indicators, i) -> bool`, `compute_indicators(...)`. The graph evaluator's per-bar interface mirrors `eval_rules`. A new `compute_indicators_from_specs(indicator_specs, df, cache=None) -> dict[str, pd.Series]` companion is added in Unit 7b to feed graph-mode evaluations.
- `backend/indicators.py` — `INDICATOR_REGISTRY` + `compute_instance(...)`. Domain node implementations call into this registry — never reimplement.
- `backend/routes/backtest.py` — `run_backtest(req, *, include_spy_correlation=True, indicator_cache=None, df=None) -> dict`. **Unit 8a extracts a `_run_simulation` helper covering only the simulator loop**; everything outside the loop (regime stripping, `signal_trace`, `rule_signals`, `ema_overlays` assembly) remains in `run_backtest` and is NOT shared with the graph route.
- `backend/bot_runner.py` — `_tick()`. The branch is at the `compute_indicators` + `eval_rules` calls, not just `eval_rules`.
- `backend/bot_manager.py` — `BotConfig` gains `kind: Literal["rule","graph"] = "rule"` and `graph: Optional[Graph] = None`.
- `backend/main.py` — FastAPI router mount.

**Frontend:**
- `frontend/src/App.tsx` — `display: none` tab mounting (CLAUDE.md F152).
- `frontend/src/features/strategy/` — rule builder; **do not touch**.
- `frontend/src/api/client.ts` — shared axios baseURL.
- localStorage convention: `strategylab-saved-graphs` (parallel to `strategylab-saved-strategies`).

**Design handoff (copy into repo at Phase 0):**
- `docs/design/nodebuilder/Node-Editor-v2-Density.html` + screenshots. JSX prototypes are NOT copied — prototype-grade.

### Institutional Learnings

- **CLAUDE.md "Silent drop of bot config fields"** — `BotConfig` already passes new POSTs through directly. Pydantic v2 default `extra="ignore"` means missing `kind`/`graph` on existing `bots.json` rows is safe via field defaults. Verified.
- **Chart-teardown safety + lightweight-charts v5 autoSize** — relevant when the Stream Inspector chart lands (T3, not T2). Not in scope here.
- **WFA capital reset rescaled post-processing** — same constraint applies to the graph backtest path: don't thread capital through; reuse `_run_simulation` cleanly.
- **No agent commits** — every implementation agent prompt includes "Do NOT commit or push."
- **`npm run build`, not `tsc --noEmit`** — catches verbatimModuleSyntax errors.

### External References

- @xyflow/react v12+ — graph engine. Compatible with React 19.2 (peer-dep verified).
- Zustand v5 — transient graph state. Compatible with React 19.2.
- Geist + Geist Mono via Google Fonts.
- OKLCH tokens via plain CSS custom properties (repo does not use Tailwind).

## Key Technical Decisions

| Decision | Rationale |
|---|---|
| **Defer kernel/domain folder split to T3** | Theoretical until sub-graphs exist. Through T2, flatten to `backend/nodebuilder/{models,evaluator,nodes,compile,from_rules}.py` and `frontend/src/features/nodebuilder/`. Refactor to `kernel/` + `domain/` at the start of T3 as a rename-and-move PR (low risk, high clarity once the boundary is real). |
| **Auto-render lives on the backend (`backend/nodebuilder/from_rules.py`)** | Single source of truth. T1 frontend calls `POST /api/nodebuilder/auto_render` to get the graph; parity test calls the same Python function directly. Eliminates the TS↔Python drift risk three reviewers flagged. |
| **R2 parity scope = `summary` + `trades` + `equity_curve` + `baseline_curve`** | Rule-only debug fields (`rule_signals`, `ema_overlays`, `signal_trace`, `regime_series`) cannot exist for graph mode. Parity = structural equality + `1e-4` relative tolerance on numeric fields. "Byte-equal" was an overstatement — explicitly retracted; snapshot comparison uses a tolerance-aware helper (see Unit 8a). |
| **Snapshot-fix the existing `run_backtest` before extracting `_run_simulation`** | Prevents the most likely failure mode of the refactor: a subtle behavior change that the graph parity test misses because it's scoped to non-regime strategies. Unit 8a snapshots run_backtest on the existing 10-strategy fixture (including regime + WFA scenarios) before any code moves. |
| **`@xyflow/react` for graph engine + Zustand for state** | Native pan/zoom/wire-handles; React 19 compatible. |
| **Indicator computation goes through `compute_indicators_from_specs`** (new wrapper in Unit 7b) | `_tick()` for graph mode needs a different driver than the rule path. New function takes the compile-produced indicator-specs list, calls into `compute_instance` per spec, returns the same dict shape rule mode produces. Reuses the canonical implementations. |
| **Core 14 node catalog at T2** | Ticker, RSI, MACD, SMA, EMA, Bollinger Bands, ATR, CrossesAbove, CrossesBelow, Above, Below, AND, OR, Entry+Exit terminals + Position Size + Stop Loss + Slippage + Commission settings nodes. Meets T2 exit gate. Rest of 40-node catalog → T3. |
| **Compile model = scalar params only at T2** | No dynamic-parameter nodes. T5 Wrangle nodes will need a per-bar evaluation pass; documented as an explicit constraint, not an oversight. |
| **`BotConfig.graph` content hash on `BotState`** for hot-reload safety | Recompile only when hash differs. Reject swapping the graph while a bot is in-position (forces stop-and-close via existing path). |
| **Graphs persist client-side at T2** in `strategylab-saved-graphs` localStorage key | Parity with `strategylab-saved-strategies`. No backend endpoint for graph storage; `AddBotBar` reads the key directly via props. (T3+ may add server-side storage with sub-graphs.) |
| **`Graph._version: int` field from day one** | Even at `version: 1`, allows load-time guard to reject older formats with a readable error instead of silently rendering broken graphs. |
| **`Graph.readOnly: bool` is a kernel field**, not a UI wrapper | Unit 1 defines it on the model; Unit 5 operations refuse mutations on `readOnly` graphs. Auto-render produces `readOnly: true`; editable graphs are `readOnly: false`. |
| **`/regime/` paths raise `RegimeUnsupportedError` at compile** | Prevents silent flat-running of regime strategies — the dangerous failure mode. UI surfaces as a toast. |
| **OKLCH tokens via plain CSS custom properties** | Repo doesn't use Tailwind. |
| **CI parity check: rule-builder indicator dropdown ⊆ node catalog** | Hard enforcement of R7 over time. Fails the build when one surface diverges. |
| **Drop the 5-density-preset strip from production UI** | Handoff README recommends. Adaptive default + user-setting dropdown. |

## Open Questions

### Resolved During Planning

- **Where do graphs live in storage?** `strategylab-saved-graphs` localStorage key. Client-side only at T2.
- **Does T1 require backend changes?** Yes — `POST /api/nodebuilder/auto_render` returns the Graph from a `StrategyRequest`. Decision changed from the original draft after the parity-test mechanism question forced a single auto-render implementation.
- **Does the graph evaluator need its own indicator computation?** No — reuses `backend/indicators.py` via the new `compute_indicators_from_specs` wrapper.
- **How does the bot picker know a strategy is graph vs. rule?** `BotConfig.kind: Literal["rule","graph"] = "rule"`. Missing field on existing `bots.json` rows defaults to `"rule"`.
- **Does the graph evaluator handle stops/sizing?** Settings nodes at T2 compile to `SimulatorSetting` values fed into `GraphBacktestRequest` simulator fields; the simulator loop itself is unchanged (no evaluator logic for stops/sizing). Settings nodes are first-class graph nodes that produce output at compile time, not at per-bar evaluation time.
- **What happens when a graph contains `/regime/`?** Compile raises `RegimeUnsupportedError`. UI toast. No silent flat-run.

### Deferred to Implementation

- Exact React Flow custom-node component split (one per category vs. generic+registry-driven) — decide in Unit 4b.
- Exact wire-overlap sampling resolution (handoff uses 24 points; tune at high zoom if needed).
- Whether to memoize the parity-test fixture results (rerunning 10 backtests per CI run may be slow) — measure first.

## Output Structure

```
backend/
  nodebuilder/                  # Flat layout for T1+T2 — kernel/domain refactor at T3
    __init__.py
    models.py                   # Graph, Node, Wire Pydantic models + _version field + readOnly flag
    evaluator.py                # Topological sort, per-bar evaluation, compute_indicators_from_specs
    nodes.py                    # Core 14 node implementations + Settings + terminals
    compile.py                  # Graph -> (indicator_specs, per_bar_program, simulator_settings)
    from_rules.py               # StrategyRequest -> Graph auto-render (canonical Python impl)
    api_models.py               # GraphBacktestRequest
  routes/
    nodebuilder.py              # POST /api/nodebuilder/{backtest,auto_render,validate}
  tests/
    nodebuilder/
      test_models.py
      test_evaluator.py
      test_nodes.py
      test_compile.py
      test_from_rules.py
      test_run_simulation_snapshot.py    # Phase 2 pre-refactor regression gate (Unit 8a)
      test_backtest_parity.py            # R2 parity test (Unit 8b)
      test_bot_runner_graph.py
      test_two_surface_parity.py         # R7 CI check (rule-builder indicators ⊆ node catalog)

frontend/src/
  features/
    nodebuilder/
      NodeBuilder.tsx           # Top-level feature
      Canvas.tsx                # React Flow integration
      StreamInspectorPlaceholder.tsx  # "Select a node to inspect" — real impl is T3
      TabMenu.tsx               # Add-node menu + fuzzy search
      AutoRenderToggle.tsx      # T1: button that fetches the auto-rendered graph
      autoRenderClient.ts       # axios wrapper around POST /api/nodebuilder/auto_render
      store.ts                  # Zustand store
      operations.ts             # Pure graph ops (add/remove/splice/delete-rewire)
      nodes/                    # React Flow custom node renderers
        TickerNode.tsx
        IndicatorNode.tsx
        ComparisonNode.tsx
        LogicNode.tsx
        SettingsNode.tsx
        OutputNode.tsx
      catalog.ts                # Core 14 node definitions (frontend-side mirror)
      categories.ts             # CATS palette
      tokens.css                # OKLCH custom properties
  api/
    nodebuilder.ts              # Axios calls

docs/
  design/
    nodebuilder/
      Node-Editor-v2-Density.html
      screenshots/
```

## High-Level Technical Design

> *Directional guidance for review, not implementation specification.*

### Data flow (T2)

```
Saved graph JSON (with _version, readOnly fields)
   │
   ▼
[Backend] models.Graph validation ──► topological sort
   │
   ▼
compile(graph) ──► (indicator_specs, per_bar_program, simulator_settings)
   │                                                            │
   │  RegimeUnsupportedError raised here if /regime/ present     │
   │                                                            │
   ▼                                                            ▼
compute_indicators_from_specs(indicator_specs, df) ──► indicators dict
   │                                                            │
   └─────► for i in range(num_bars):                            │
              signals = evaluate_graph(per_bar_program, indicators, i)
              # signals = {"entry": bool, "exit": bool}
              ↓
           _run_simulation(indicators, signals, simulator_settings, req)
              # SAME helper rule mode now uses (extracted in Unit 8a)
              # Returns {summary, trades, equity_curve, baseline_curve}
              # Does NOT emit rule_signals / ema_overlays / signal_trace
```

### Bot tick seam

```
BotRunner._tick():
   if bot.config.kind == "graph":
       current_hash = sha256(json.dumps(bot.config.graph.model_dump(mode="json"), sort_keys=True))
       if current_hash != bot.state.graph_hash:
           bot.state.program = compile(bot.config.graph)
           bot.state.graph_hash = current_hash
       indicators = compute_indicators_from_specs(bot.state.program.indicator_specs, df)
       sigs = evaluate_graph(bot.state.program, indicators, last_bar_idx)
       buy_signal, sell_signal = sigs["entry"], sigs["exit"]
   else:
       indicators = compute_indicators(...)           # unchanged
       buy_signal = eval_rules(buy_rules, ...)
       sell_signal = eval_rules(sell_rules, ...)
   # everything below this seam is unchanged
```

## Implementation Units

Phases are gated. **Do not start Phase 2 until the T1 kill-switch is satisfied.** Within a bundle, units are file-independent and can dispatch in parallel.

### Phase 0 — Foundations

- [ ] **Unit 0a: Install dependencies + design assets**

**Goal:** @xyflow/react, zustand, Geist fonts, design handoff HTML.

**Files:**
- Modify: `frontend/package.json` (add `@xyflow/react` ^12, `zustand` ^5)
- Modify: `frontend/src/index.css` (Geist + Geist Mono imports; @xyflow CSS)
- Create: `frontend/src/features/nodebuilder/tokens.css` (OKLCH tokens from handoff §"Design Tokens")
- Create: `docs/design/nodebuilder/Node-Editor-v2-Density.html` (copy from handoff bundle)
- Create: `docs/design/nodebuilder/screenshots/` (copy four screenshots)

**Test scenarios:** none (scaffolding) — verify via `npm run build` exit code 0.

**Verification:** `npm run build` passes. Geist renders in DevTools.

- [ ] **Unit 0b: Backend scaffolding**

**Goal:** Empty `backend/nodebuilder/` module + empty router wired into FastAPI.

**Files:**
- Create: `backend/nodebuilder/__init__.py` + 6 empty module stubs (`models`, `evaluator`, `nodes`, `compile`, `from_rules`, `api_models`)
- Create: `backend/routes/nodebuilder.py` (empty `APIRouter()`)
- Modify: `backend/main.py` (`app.include_router(nodebuilder.router)`)
- Create: `backend/tests/nodebuilder/__init__.py`

**Test scenarios:**
- Integration: FastAPI app starts, `/openapi.json` includes (empty) nodebuilder router.

**Verification:** `pytest backend/tests/nodebuilder/`; FastAPI lifespan clean.

---

### Phase 1 — T1: Read-Only Viewer

- [ ] **Unit 1: Graph model (backend) — pure data types**

**Goal:** Pydantic `Graph`, `Node`, `Wire`, `NodePath`. Includes `_version: int = 1`, `readOnly: bool = False`. Pure data + validation. (`MIN_SUPPORTED_VERSION` constant defaults to `1`; the load-time guard is a minimum-floor check, not equality — `_version >= MIN_SUPPORTED_VERSION` — so additive field changes can bump version without invalidating saved graphs.)

**Files:**
- Modify: `backend/nodebuilder/models.py`
- Create: `backend/tests/nodebuilder/test_models.py`

**Approach:**
- `Graph = { _version: int, readOnly: bool, nodes: dict[str, Node], wires: list[Wire] }` — nested by path string keys.
- Total validation: no cycles, no orphan wires, no duplicate paths, every wire endpoint exists.
- Path resolver (`resolve(from_path, ref) -> path`): absolute (`/abs`), relative (`../sibling`), same-dir (`./local`).

**Test scenarios:**
- Happy path: build a 3-node graph, validation passes, topological sort respects dependencies.
- Edge case: empty graph validates; single-node graph validates.
- Error path: cycle → `CyclicGraphError`; dangling wire → `DanglingWireError`; missing terminal → noted for compile (not model-level).
- Edge case: `_version < MIN_SUPPORTED_VERSION` on load → `IncompatibleGraphVersionError` with version numbers in the message; `_version == MIN_SUPPORTED_VERSION` and `_version > MIN_SUPPORTED_VERSION` (future additive) both load successfully.
- Edge case: path resolver handles `..`, `.`, `/abs`, bare-name correctly.

**Verification:** `pytest backend/tests/nodebuilder/test_models.py`.

- [ ] **Unit 2: Core 14 node catalog (backend metadata only) + frontend mirror**

**Goal:** Static catalog defining the 14 nodes shipped at T2.

**Core 14 (compile-active):** Ticker, RSI, MACD, SMA, EMA, Bollinger Bands, ATR, CrossesAbove, CrossesBelow, Above, Below, AND, OR, Settings category (PositionSize, StopLoss, Slippage, Commission — feed `SimulatorSetting` into `GraphBacktestRequest`).

**Compile-active terminals:** Entry, Exit (wired; their input attribute becomes the buy/sell signal).

**Catalog-only terminals (no compile role at T2):** Size, Stop — visible in the palette and renderable on the canvas so users can place them, but compile ignores them. T4 wires these to the simulator. Unit 7a tests this explicitly: graph with unwired Size/Stop terminals compiles without error and produces no `SimulatorSetting` override from them.

**Files:**
- Modify: `backend/nodebuilder/nodes.py` (catalog metadata: `{name, cat, reads, writes, defaults, impl_ref}`; impl_ref points to functions added in Unit 7b)
- Create: `frontend/src/features/nodebuilder/catalog.ts` (matching shape, no impls)
- Create: `frontend/src/features/nodebuilder/categories.ts` (CATS palette + colors from handoff)
- Create: `backend/tests/nodebuilder/test_catalog_consistency.py` (frontend mirror parity check)
- Test: `frontend/src/features/nodebuilder/__tests__/catalog.test.ts`

**Test scenarios:**
- Happy path: every category in `categories.ts` has at least one node.
- Edge case: no duplicate node names; reads/writes are `@attr`-prefixed strings.
- Integration: frontend catalog file is parseable from backend test via a generated JSON dump (or vice versa) — confirms the two stay in sync.

**Verification:** both test files pass.

- [ ] **Unit 3: Auto-render transform + `POST /api/nodebuilder/auto_render` endpoint**

**Goal:** `from_rules.py::auto_render(req: StrategyRequest) -> Graph`. Produces `readOnly=True` graphs. Frontend fetches via the endpoint; parity test calls the function directly.

**Files:**
- Modify: `backend/nodebuilder/from_rules.py`
- Modify: `backend/routes/nodebuilder.py` (`POST /api/nodebuilder/auto_render`)
- Modify: `backend/nodebuilder/api_models.py` (`AutoRenderResponse = { graph: Graph }`)
- Create: `backend/tests/nodebuilder/test_from_rules.py`
- Create: `frontend/src/api/nodebuilder.ts` (axios call)

**Approach:**
- One Ticker node per (symbol, interval, source); one Indicator node per unique (indicator, params) tuple (memoize across rules); one Comparison node per rule; AND/OR logic nodes per side; NOT for negated rules; Settings nodes for position size + stops + costs; Entry/Exit terminals.
- Regime mode: render `/regime/` sub-tree (viewer must show it; compile will reject if user tries to backtest).
- Layout: deterministic left-to-right; one column per stage.
- Output: `Graph(readOnly=True, _version=1, ...)`.

**Test scenarios:**
- Happy path: simple RSI<30 long → 4-node graph (Ticker → RSI → CrossesBelow → Entry).
- Happy path: buy + sell with shared RSI → RSI memoized (one node, two outgoing wires).
- Happy path: NOT-negated rule → NOT node inserted before AND/OR.
- Edge case: empty strategy → graph with Ticker + Entry/Exit + Settings, no signal wires.
- Edge case: regime-mode strategy → `/regime/` sub-tree present with AND join to direction-specific rules.
- Edge case: per-direction (long_buy_rules + short_buy_rules) → two parallel sub-trees both feeding Entry.
- Integration: endpoint returns 200 with valid Graph for 5 fixture strategies.

**Verification:** test suite; manual curl returns sane Graph for a saved strategy.

- [ ] **Unit 4a: Graph View tab + read-only Canvas (React Flow integration)**

**Goal:** "Graph View" toggle in Chart tab; React Flow canvas mounts; pan/zoom; placeholder boxes for nodes (no custom renderers yet).

**Files:**
- Create: `frontend/src/features/nodebuilder/NodeBuilder.tsx`
- Create: `frontend/src/features/nodebuilder/Canvas.tsx` (React Flow integration, read-only mode, default node renderer)
- Create: `frontend/src/features/nodebuilder/AutoRenderToggle.tsx` (button: "View as Graph" → calls backend, populates Canvas)
- Modify: `frontend/src/App.tsx` (toggle in Chart tab; `display: none` survival)

**Approach:**
- Read-only mode = no Tab menu, no port drag, no edit ops. Canvas just renders nodes + wires from the auto_render response.
- Switching strategies via Sidebar re-fetches auto_render through a TanStack Query call keyed on `hash(StrategyRequest JSON)`. Same strategy hash = served from cache (zero round-trip on revisit). Default `staleTime: Infinity` for the auto_render query since the transform is deterministic on its input.

**Test scenarios:**
- Happy path: toggle "View as Graph" → graph fetched, rendered → user pans/zooms.
- Happy path: switch strategies in Sidebar → graph updates.
- Integration (browser-verified, REQUIRED): seed strategy via localStorage, reload, toggle Graph View, screenshot. Verify pan + zoom work; verify `display: none` survives a Chart-tab roundtrip.

**Verification:** `npm run build`; browser screenshot in JOURNAL.

- [ ] **Unit 4b: Custom node renderers (6 components) + wire labels**

**Goal:** Replace placeholder boxes with per-category node renderers. Adaptive density. Attribute pills on wires.

**Files:**
- Create: `frontend/src/features/nodebuilder/nodes/{Ticker,Indicator,Comparison,Logic,Settings,Output}Node.tsx`
- Create: `frontend/src/features/nodebuilder/edges/AttrEdge.tsx` (custom wire renderer with label chip)
- Modify: `frontend/src/features/nodebuilder/Canvas.tsx` (register `nodeTypes` + `edgeTypes`)

**Approach:**
- Adaptive density: Atom default → Standard on hover → Rich when display-flagged (rich-state is T3 once Stream Inspector lands; Standard suffices at T2).
- Wire label = source node's primary write attribute (e.g., `@rsi`).

**Test scenarios:**
- Happy path: each category renders with the correct OKLCH color stripe + glyph.
- Integration (browser-verified): auto-render a strategy, screenshot of full canvas to confirm visual fidelity matches handoff screenshots.

**Verification:** screenshots committed to JOURNAL.

---

### Gate: T1 → T2 (Kill Switch)

**Do not start Phase 2 until ALL of the following hold.** Capture in JOURNAL.

- [ ] User has spent ≥15 minutes inspecting ≥5 real saved strategies in Graph View, **including at least one strategy not edited in 30+ days** (cold-read requirement).
- [ ] User affirms in JOURNAL with one sentence per strategy: *"Reading this graph is faster than re-reading the rule list, and I would prefer to author a new strategy like X here rather than in the rule builder."* The second clause is the load-bearing one — pure rendering-engine validation isn't enough.
- [ ] If the user cannot affirm both clauses on a real strategy: **stop the plan**. Re-evaluate scope; consider abandoning the node-builder track. The viewer must earn the right to become editable.
- [ ] Browser-verification screenshots of ≥3 auto-rendered strategies (simple, regime-mode, per-direction) committed under `docs/design/nodebuilder/screenshots/auto-render/`.

---

### Phase 2 — T2: Editable Canvas + Live-Trading-Capable Evaluator

#### Bundle 2A — Editable canvas (UI; backend untouched)

- [ ] **Unit 5: Zustand store + edit operations + readOnly enforcement**

**Goal:** Editable graph state. Operations refuse mutations on `readOnly` graphs.

**Files:**
- Create: `frontend/src/features/nodebuilder/store.ts` (Zustand: nodes, wires, selectedId, displayId, bypassed, pan, scale, graphHash)
- Create: `frontend/src/features/nodebuilder/operations.ts` (pure `Graph -> Graph` ops)
- Modify: `frontend/src/features/nodebuilder/Canvas.tsx` (wire to store; switch from read-only to editable when `graph.readOnly === false`)
- Create: `frontend/src/features/nodebuilder/__tests__/operations.test.ts`

**Approach:**
- Every op rejects with `ReadOnlyGraphError` when `graph.readOnly === true`.
- **Delete-rewire**: Cartesian product of incoming × outgoing, dedup, no self-loops.
- **Splice on Alt+drag**: 24-point Bezier sampling, AABB intersection, first-wins.
- **Saves to `strategylab-saved-graphs` localStorage with `_version: 1`.**

**Test scenarios:**
- Happy path: add/drag/connect/delete on an editable graph.
- Happy path: delete-rewire — 3 incoming × 2 outgoing → 6 new wires, dedup correct.
- Edge case: delete with 0 incoming → no rewires.
- Edge case: rewire would create cycle → operation rejected.
- Edge case: splice when dragged node overlaps multiple wires → first-wins.
- Error path: mutation on `readOnly=true` graph → `ReadOnlyGraphError`.
- Edge case: load graph with `_version > 1` → `IncompatibleGraphVersionError` surfaced as toast.

**Verification:** test suite; integration smoke (open canvas, 10 ops, save, reload, identical).

- [ ] **Unit 6: Tab menu + port-drag wire creation + Delete key binding**

**Goal:** Add nodes via Tab; create wires by port drag; Delete key removes selected node/wire.

**Files:**
- Create: `frontend/src/features/nodebuilder/TabMenu.tsx`
- Create: `frontend/src/features/nodebuilder/search.ts` (fuzzy match per design handoff §7 scoring)
- Modify: `frontend/src/features/nodebuilder/Canvas.tsx` (Tab keybind, Delete keybind, port-drag handlers)
- Create: `frontend/src/features/nodebuilder/__tests__/search.test.ts`

**Approach:**
- Tab opens at cursor; second Tab + Esc close.
- Hierarchical browse when query empty; flat scored list otherwise.
- **Autofill suppression**: `autocomplete="off"`, randomized `name`, `data-1p-ignore`, `data-lpignore`, `data-form-type="other"`.
- **Stale-closure**: keyboard handler reads from refs.
- Port drag: mousedown → preview wire → snap to compatible port within 18px.
- Delete/Backspace on selected node → `operations.deleteWithRewire`; on selected wire → `operations.removeWire`.

**Test scenarios:**
- Happy path: `r` → RSI; `cb` → Crosses Below; `crsblw` → Crosses Below (subsequence).
- Happy path: Shift+Enter creates without auto-wiring.
- Edge case: empty query → hierarchical browse; keyboard nav respects column boundaries.
- Error path: Delete on no selection → no-op.
- Integration (browser-verified): drag port-to-port, verify wire labeled with source's primary write; press Delete on a selected node, verify rewire.
- Manual (browser): 1Password autofill suppression — visually verify in screenshot.

**Verification:** test suite; browser-verified screenshots.

#### Bundle 2B — Backend evaluator + `_run_simulation` refactor

- [ ] **Unit 7a: Evaluator + compile (pure functions)**

**Goal:** Topological sort, per-bar program runner, compile function (graph → indicator_specs + per_bar_program + simulator_settings), graph-mode indicator dispatcher.

**Files:**
- Modify: `backend/nodebuilder/evaluator.py`
  - `topological_sort(graph) -> list[Node]`
  - `evaluate_graph(program, indicators, i) -> dict` (returns {entry, exit})
  - `compute_indicators_from_specs(indicator_specs, ohlcv: OHLCVSeries, cache=None) -> dict[str, pd.Series]` — **independent dispatcher, NOT a thin wrapper.** Mirrors `signal_engine.py::compute_indicators` dispatch logic (family-cap validation, OHLCVSeries construction, calling `compute_instance` per spec) but takes a `list[IndicatorSpec]` input instead of `list[Rule]`. Reuses `compute_instance` at the leaf level; the dispatch + cap-check layer is new code.
- Modify: `backend/nodebuilder/compile.py`
  - `compile(graph) -> CompiledProgram` (raises `RegimeUnsupportedError` if `/regime/` present, `MissingTerminalError` if no Entry, `TypeError` if Entry input is non-boolean)
- Create: `backend/tests/nodebuilder/test_evaluator.py` + `test_compile.py`

**Test scenarios:**
- Happy path: 4-node graph compiles to one indicator-spec + one bool op + one terminal.
- Happy path: shared RSI across two Comparisons → indicator-specs deduped.
- Error path: graph with cycle → `CyclicGraphError`.
- Error path: graph with `/regime/` path → `RegimeUnsupportedError`.
- Error path: graph missing Entry terminal → `MissingTerminalError`.
- Error path: Entry's input not boolean → `TypeError`.
- Edge case: bypassed node → skipped in per-bar program (matches rule-mode `muted` flag).
- Edge case: graph contains Size or Stop terminals with no incoming wires → compile succeeds, `simulator_settings` contains no override from those terminals. Confirms the T2 "catalog-only-no-wire" contract for those terminals.
- Edge case: graph with family-cap-exceeding indicator specs → `compute_indicators_from_specs` raises the same `FamilyCapExceededError` (or whatever `signal_engine.py` currently raises) — confirms parity with the rule path on this guard.
- Happy path: per-bar eval on 100-bar fixture matches `eval_rules` output for equivalent Rule.

**Verification:** test suite green.

- [ ] **Unit 7b: Core 14 node implementations**

**Goal:** One Python function per node in the Core 14 catalog. Indicator nodes call into `compute_instance`.

**Files:**
- Modify: `backend/nodebuilder/nodes.py` (impls: `rsi_impl`, `macd_impl`, `crosses_below_impl`, etc.)
- Create: `backend/tests/nodebuilder/test_nodes.py`

**Approach:**
- Indicator node impl shape: `(params: dict) -> IndicatorSpec` (declares what `compute_indicators_from_specs` should compute, plus the write-attribute name).
- Comparison/logic node impl shape: `(params: dict) -> PerBarOp` (a pure per-bar function over the indicators dict).
- Settings node impl shape: `(params: dict) -> SimulatorSetting` (extracted at compile time, fed to `GraphBacktestRequest` simulator fields).
- Terminal node impl shape: marker only; compile uses presence to wire up entry/exit signal sources.

**Test scenarios:**
- Happy path: each Core 14 node's impl produces the expected output given canned inputs.
- Edge case: RSI with `period < 2` → ValueError (matches `compute_instance` validation).
- Integration: indicator-spec output of RSI impl, fed through `compute_indicators_from_specs`, produces a series identical to `compute_instance("rsi", {"period":14}, df)`.

**Verification:** test suite green.

#### Bundle 2C — Backtest integration

- [ ] **Unit 8a: Snapshot-fix `run_backtest` THEN extract `_run_simulation`**

**Goal:** Refactor `run_backtest` to expose `_run_simulation(indicators, signal_callable, req) -> {summary, trades, equity_curve, baseline_curve}`. **Critical: snapshot existing behavior FIRST.** Adversarial-reviewer P0.

**Files:**
- Create: `backend/tests/nodebuilder/test_run_simulation_snapshot.py` (10-strategy fixture: simple long, simple short, MACD crossover, trailing stop, ATR-trailing, regime-mode, costs nonzero, per-direction overrides, NOT-negated, max_bars_held)
- Create: `backend/tests/nodebuilder/fixtures/run_backtest_snapshots/*.json` (committed expected outputs)
- Create: `backend/tests/nodebuilder/_snapshot_helpers.py` (numpy/NaN-aware JSON encoder + tolerance-aware comparison helper — see Approach)
- Modify: `backend/routes/backtest.py` (extract `_run_simulation` helper; `run_backtest` becomes the rule-mode wrapper that handles regime stripping, debug field assembly, and calls `_run_simulation` for the loop)

**Approach:**
- **Serialization:** raw `json.dumps` does not handle `numpy.float64`, `numpy.int64`, `numpy.bool_`, NaN, or pandas Timestamps. The helper uses a custom encoder that converts numpy scalars to Python floats/ints, NaN to a sentinel string `"__NaN__"`, and timestamps to ISO strings. Used for both writing snapshots and reading them back.
- **Comparison:** "byte-identical" is not the right bar — same code can produce sub-`1e-15` FP drift on different machines. Comparison helper walks the dict structure, enforces exact equality on categorical/list-length/key-set fields, and `pytest.approx(..., rel=1e-9)` on numeric fields *within the rule-vs-rule snapshot test* (same code path, so drift should be near zero — `1e-9` is honest here, unlike the cross-path `1e-4` in Unit 8b).
- **Workflow:** write the snapshot test against unmodified `run_backtest`, run it once to populate `fixtures/*.json`, commit fixtures, refactor `_run_simulation`, rerun. Comparison must pass on every fixture.

**Approach:**
- `_run_simulation` accepts: indicators dict, a signal callable `(i) -> (buy_bool, sell_bool)`, simulator-level settings (stop_loss, trailing_stop, sizing, costs, direction, etc.), df.
- `_run_simulation` returns: `{summary, trades, equity_curve, baseline_curve}` only. The rule-only debug fields stay in `run_backtest`.

**Test scenarios:**
- **Critical (regression gate):** all 10 fixture strategies produce equal output dicts (per comparison helper above, `rel=1e-9` on numerics) for `summary` + `trades` + `equity_curve` + `baseline_curve` + `signal_trace` + `rule_signals` + `ema_overlays` + `regime_series` before vs. after refactor.
- Edge case: WFA fixture (which calls `run_backtest` repeatedly) produces stitched output that passes the same comparison helper.

**Verification:** all 10 snapshots match (per helper); CI green; **regression gate is the single most important verification in this plan.**

- [ ] **Unit 8b: Graph backtest route + parity test**

**Goal:** `POST /api/nodebuilder/backtest` returns `{summary, trades, equity_curve, baseline_curve}` (rule-only fields absent). R2 parity test green.

**Files:**
- Modify: `backend/routes/nodebuilder.py` (`POST /api/nodebuilder/backtest`)
- Modify: `backend/nodebuilder/api_models.py` (`GraphBacktestRequest` = Graph + simulator-level fields verbatim from `StrategyRequest`)
- Create: `backend/tests/nodebuilder/test_backtest_parity.py`
- Modify: `frontend/src/features/nodebuilder/NodeBuilder.tsx` (▶ Run Backtest button)
- Modify: `frontend/src/api/nodebuilder.ts` (axios call)

**Approach:**
- Route: compile(graph) → `_run_simulation(indicators, evaluate_graph_callable, simulator_settings, req)`.
- Parity test: for each of 10 fixture strategies, run `run_backtest(rule_req)`, run `auto_render` → `_run_simulation` via graph path, compare:
  - `summary` dict: numeric fields within `1e-4` relative tolerance; categorical fields exact.
  - `trades` list: same count, same dates, same direction sequence; PnL within `1e-4` relative tolerance per trade.
  - Regime + WFA fixtures skipped with `pytest.skip("T2 scope: regime evaluator not supported")`.

**Test scenarios:**
- Happy path: 4-node graph returns valid response.
- Happy path: response has exactly the keys `{summary, trades, equity_curve, baseline_curve}` — no `rule_signals`/`ema_overlays`/`signal_trace`.
- **Critical (R2 gate):** parity test passes on the 10-strategy fixture (regime/WFA skipped).
- Edge case: graph with no trades → `summary.num_trades == 0`.
- Edge case: short-only graph → trade types `short`/`cover`.
- Edge case: bypassed node → its contribution skipped.
- Error path: graph with `/regime/` → 400 with `RegimeUnsupportedError` detail.
- Integration (browser-verified): ▶ Run Backtest from canvas, Results panel populates.

**Verification:** parity test green on 8 of 10 (regime/WFA skipped); browser-verified Results panel match.

#### Bundle 2D — Bot runner

- [ ] **Unit 9: `BotConfig.graph` + `_tick()` branch + graph hash hot-reload**

**Goal:** Graph strategies deploy as bots. R3 enforced by ≥1 trading-day paper-trade gate.

**Files:**
- Modify: `backend/bot_manager.py` (BotConfig gains `kind`, `graph`; BotState gains `graph_hash: Optional[str]`, `compiled_program: Any` runtime cache)
- Modify: `backend/bot_runner.py` (`_tick()` branches at `compute_indicators` AND `eval_rules` calls — both swap for the graph path, per Feasibility-F2)
- Modify: `backend/routes/bots.py` (AddBotRequest accepts `kind`+`graph`; BotStatus surfaces `kind`)
- Modify: `frontend/src/features/trading/AddBotBar.tsx` (radio: "Source: Rules | Graph"; graph list reads `strategylab-saved-graphs` localStorage via a prop)
- Modify: `frontend/src/features/trading/BotCard.tsx` (badge "via Graph" alongside direction + broker)
- Create: `backend/tests/nodebuilder/test_bot_runner_graph.py`

**Approach:**
- `BotConfig` cross-field validator (`model_validator(mode='after')`) **logs a warning, does not raise** when `kind="graph"` and `graph is None` — allows partial-construction during stop/edit/start without hard-failing.
- Graph hash: compute `hashlib.sha256(json.dumps(graph.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()` on each tick. `model_dump(mode="json")` is mandatory — raw `json.dumps` on a Pydantic model raises `TypeError`, and `model_dump(mode="json")` also resolves enums/timestamps to JSON-compatible primitives. Recompile only when the hash differs from `bot.state.graph_hash`.
- **Backup `backend/data/bots.json` to `bots.json.pre-graph.bak` before this unit lands** (operational note).
- `bot.config.graph` swap rejected while bot is in-position — user must "Stop and Close" first.
- HTF-attributed nodes in graphs are not supported at T2 (documented; mirrors the existing bot-runner gap).

**Test scenarios:**
- Happy path: graph-mode RSI<30 bot ticks produce identical signals to rule-mode equivalent on a fixed bar window.
- Happy path: existing rule-mode bots in bots.json load + run unchanged (no `kind`/`graph` fields → defaults apply).
- Edge case: missing `graph` on `kind="graph"` config → warning logged, validation passes (Feasibility-F4).
- Edge case: stop + edit graph + start → bot recompiles (hash differs), runs new program.
- Edge case: mid-position graph swap attempted via UI → blocked at API with explicit error.
- Edge case: graph contains HTF-attributed node → graph backtest accepts it (run_backtest handles HTF); bot startup rejects it with `HTFGraphNotSupportedError` (T2 explicit limitation).
- Error path: `/regime/` in deployed graph → bot startup raises `RegimeUnsupportedError`.
- **Critical (R3 gate):** graph-mode bot runs in Alpaca paper or IBKR paper ≥1 trading day. Journal log + screenshots in JOURNAL.

**Verification:** test suite; live paper-trade session documented in JOURNAL.

#### Bundle 2E — R7 enforcement

- [ ] **Unit 10: CI parity check — rule-builder indicators ⊆ node catalog**

**Goal:** Hard enforcement that the two surfaces don't drift over time. Adversarial-reviewer P2.

**Files:**
- Create: `backend/tests/nodebuilder/test_two_surface_parity.py`

**Approach:**
- Pytest reads the rule-builder's indicator dropdown options (from `backend/signal_engine.py::RuleIndicator` literal type) and the node catalog (from `backend/nodebuilder/nodes.py`).
- **Legacy carve-out:** `RuleIndicator` currently contains migration-only values (`ema20`, `ema50`, `ema200`, `ma8`, `ma21`) that are accepted at validation but converted by `migrate_rule()` to canonical forms (`ma:20:ema`, `ma:8:sma`, etc.). These have no natural node-catalog equivalent and are explicitly excluded from the subsetting check via a named `LEGACY_MIGRATION_INDICATORS` constant in the test file. Comment in the constant explains why each value is excluded.
- Asserts: every non-legacy indicator in `RuleIndicator` has a corresponding node in the catalog with the same indicator backing.
- Failure message names the missing nodes so the fix is mechanical.

**Test scenarios:**
- Happy path: today's non-legacy rule-builder indicators all have node catalog entries.
- Edge case: legacy migration values (`ema20`, `ema50`, `ema200`, `ma8`, `ma21`) are present in `LEGACY_MIGRATION_INDICATORS` and the test passes (does not flag them as drift).
- Error path (synthetic): add a new non-legacy `RuleIndicator` value without a node, run test → fail with clear message naming the missing node.

**Verification:** test passes today; would catch a future divergence.

---

### Gate: T2 Complete (Kill Switch — same severity as T1)

**T2 ships when ALL of the following hold. This gate has the same kill-switch authority as T1 — if a non-negotiable fails, T3 is not started; the plan stops or returns to remediation.**

- [ ] Phase 2 test suite passes (every unit).
- [ ] **Snapshot regression gate (Unit 8a) green** — the `_run_simulation` extraction has not perturbed existing rule-mode behavior on the 10-strategy fixture, including regime + WFA cases. *Non-negotiable.*
- [ ] **Parity test (Unit 8b) green on 8/10 strategies** (regime + WFA skipped with explicit reason). *Non-negotiable for R2.*
- [ ] At least one graph-mode bot has run live (paper) ≥1 trading day with no evaluator-related errors. *Non-negotiable for R3.*
- [ ] User has built one strategy ≥5 nodes end-to-end on the canvas (must include at least one Indicator node + one Logic node). *Non-negotiable for R1.* (Note: "must include a Signal Processing node" requirement removed — Signal Processing nodes are T3 scope.)
- [ ] Two-surface parity test (Unit 10) green. *Non-negotiable for R7.*
- [ ] JOURNAL entry summarizes lessons + flags T3 follow-ups discovered.

If any non-negotiable fails: **stop**. Do not start T3 with debt against R2/R3/R7.

## Future Considerations (out of scope for this plan)

- **T3 opening move:** rename-and-move PR splitting `backend/nodebuilder/` into `kernel/` + `domain/`. Behavior-neutral. Adds the import-isolation pytest at that time, when the boundary is actually load-bearing.
- **T3 — Sub-graphs:** save / instantiate / promote-to-palette; declarative `reads/writes` interface.
- **T3 — Stream Inspector real implementation:** cursor sync, dataframe windowing, per-bar attribute series, chart panel.
- **T3 — Catalog expansion:** remaining ~26 nodes including all Signal Processing (Savitzky-Golay, Kalman, HP, Butterworth, Hampel, FFT, Low-Pass, Z-Score, Rate of Change, Detrend, Lag, plus VWAP, Stochastic, Volume MA, ADX, Slope Up/Down, Between, Decelerating, NOT, XOR, composite Rules, Wrangle).
- **T3 — Regime in graph evaluator:** becomes natural once paths/sub-graphs exist (`/regime/` becomes a sub-graph that gates each direction).
- **T4 — Multi-Output Groups + on-canvas Size/Stop terminals wired to the simulator.**
- **T5 — Code surfaces:** per-param `=` mode, per-node code blocks, Wrangle nodes; Monaco; dynamic parameters (requires compile-model extension — explicitly anticipated in Unit 7a).
- **T2 hardening TODOs:** undo/redo (history stack + inverse ops); copy/paste; select-all.
- **Backtest-as-a-node:** T3+ design question.
- **Live-mode visualization** ("which wire is hot right now").

## System-Wide Impact

- **Interaction graph:** new `/api/nodebuilder/*` endpoints (`backtest`, `auto_render`, `validate`); new tab toggle in `App.tsx`; new localStorage key (`strategylab-saved-graphs`); new optional fields in `BotConfig`. Existing rule-mode code paths: only `run_backtest` is touched (via `_run_simulation` extraction, snapshot-gated).
- **Error propagation:** Pydantic 422 for invalid graphs; explicit errors (`CyclicGraphError`, `DanglingWireError`, `MissingTerminalError`, `RegimeUnsupportedError`, `HTFGraphNotSupportedError`, `IncompatibleGraphVersionError`, `ReadOnlyGraphError`) surfaced as UI toasts.
- **State lifecycle risks:** existing `bots.json` rows load with default `kind="rule"`. Graph hash on `BotState` invalidates compile cache on edit. Mid-position graph swaps blocked.
- **API surface parity:** `GraphBacktestRequest` simulator-level fields use the exact `StrategyRequest` field names (no silent renames).
- **Integration coverage:** R2 parity test (8b) + R7 two-surface parity test (10) are the explicit cross-layer tests mocks alone won't prove.
- **Unchanged invariants:** rule builder, rule-mode bots, existing strategy save format, `StrategyRequest`, `Rule`, `eval_rules`, `compute_indicators`, simulator semantics. Modifications to existing files limited to: (a) `run_backtest` refactored via snapshot-gated `_run_simulation` extraction, (b) `BotConfig` field additions with defaults, (c) `_tick()` branch on `kind`, (d) `App.tsx` tab toggle. Every modification preserves prior behavior.

## Risks & Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| T1 viewer fails the kill-switch (graph not preferred to rule list) | Medium | High — kills the track | T1 is cheap (4 units). Cold-read requirement + authoring-intent affirmation ensure the gate measures the right thing. |
| `_run_simulation` refactor changes existing behavior | High (this is the most likely failure) | Critical — regresses rule path | Unit 8a snapshot-gates the refactor on a 10-strategy fixture *including* regime + WFA. Snapshots commit BEFORE refactor; refactor must produce output equivalent under the snapshot comparison helper (categorical fields exact; numeric fields `rel=1e-9` within the same-code-path comparison). |
| R2 parity false-positive (test passes, real drift hides in regime/WFA) | Medium | High | Regime/WFA explicitly excluded; documented as a known coverage gap. T3 plan must broaden parity to include regime when the evaluator gains it. |
| FP non-determinism flakes parity test in CI | Medium | Medium | `1e-4` relative tolerance + structural-equality split; no `1e-9` claim. CI Python+NumPy pinning is already in repo conventions. |
| Bot runner subtleties surface only in live trading | Medium | High | R3 requires ≥1 trading day live paper before T2 ships. No purely-backtested ship. |
| Dynamic-param node demand at T2 | Low | High | Core 14 has no dynamic-param nodes; explicit constraint. T5 Wrangle will need compile-model extension; anticipated. |
| Saved-graph format churn breaks user graphs | High | Medium | `_version: 1` field from day one; load-time guard surfaces clear error. User accepts re-authoring during T2 dev. |
| Two-surface maintenance drift | Medium → High over time | Medium | CI parity test (Unit 10) hard-enforces rule-builder ⊆ node-catalog. Cannot drift silently. |
| Mid-tick graph swap corruption | Low | High | Hash-based recompile + mid-position swap rejection + bots.json backup before Unit 9. |
| Premature kernel/domain abstraction overhead | Was: medium. Now: removed — kernel/domain split deferred to T3. | — | Resolved by scope reduction. |
| React Flow perf at 50+ nodes | Low | Medium | Profile if it surfaces. xyflow handles much larger scales. |
| "Wow-factor trap" — polish over trading outcome | Medium | High | Each tier gate is a real-strategy-built or live-bot-run, not UI polish. |

## Documentation / Operational Notes

- **CLAUDE.md update**: add a Node Builder section once T2 ships, covering the `_run_simulation` seam, the graph→bot tick branch, and the explicit T2 limitations (no regime evaluator, no HTF nodes in bots, Core 14 only). Don't add it earlier — CLAUDE.md is operational rules, not aspirational architecture.
- **TODO.md**: add `F-NODE-*` items per unit as picked up; tag Phase 0 as `[arch]`, Unit 8a as `[medium] [hardening]` (snapshot regression gate).
- **JOURNAL.md**: per-unit entries with browser-verification screenshots. Critical entries: T1 gate affirmation; Unit 8a snapshots; Unit 9 live-paper-trade day.
- **Operational pre-Unit-9**: back up `backend/data/bots.json` to `backend/data/bots.json.pre-graph.bak`.
- **No live-trading rollout flag needed** — graph-mode bots are opt-in per bot at creation.

## Sources & References

- **Origin requirements:** `docs/ideation/2026-05-17-node-based-strategy-builder-v2.md`
- **Design handoff (external):** `/tmp/strategyLab_handoff/design_handoff_node_strategy_builder/README.md` + JSX. Copied at Phase 0 into `docs/design/nodebuilder/`.
- **Rule strategy schema:** `backend/models.py`, `backend/signal_engine.py`
- **Backtest entry point:** `backend/routes/backtest.py` (`run_backtest`)
- **Bot runner:** `backend/bot_runner.py`
- **Indicator registry:** `backend/indicators.py`
- **Project context:** `CLAUDE.md` (orchestrator workflow, severity-graded review tiers, browser verification, F152 tab-mount pattern, no-agent-commits, no-`tsc --noEmit`)
- **Tab structure (frontend):** `frontend/src/App.tsx`
- **Review pass (2026-05-22):** feasibility, scope-guardian, adversarial, coherence — 4-persona review consumed; findings synthesized into this revision.

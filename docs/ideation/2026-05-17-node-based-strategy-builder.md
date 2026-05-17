# Node-Based Strategy Builder — Requirements

**Date:** 2026-05-17
**Status:** Exploration / requirements (no plan yet)
**Relationship to existing work:** Parallel track alongside the current rule builder. Not a successor (yet).

## The Pitch

A node-graph authoring surface for trading strategies, modeled on Houdini and Nuke. A strategy becomes a single legible spatial artifact instead of a stack of dense form rows. The current rule builder stays untouched; the node editor is a new strategy *type* with its own evaluator that lives alongside.

## Why This Idea Now

The rule builder works, but each feature added to it (NOT, slope conditions, regime filter, parameter sprawl on every indicator) makes each rule row denser. The form is starting to hide the strategy. A node graph trades vertical density for horizontal legibility and gives us three things the form can't:

1. **Spatial layout** — you can see the whole strategy at once instead of scrolling through a column of fields.
2. **Reusable sub-graphs** — "momentum confirm", "volatility filter", etc. as named, parameterized blocks (Houdini HDA analogue).
3. **Unified lifecycle on one canvas** — entry, exit, sizing, and stops live on the same surface with visible data flow between them, instead of being separate sections of the sidebar.

The pain the user explicitly does **not** have: richer boolean logic. The current AND-only flat model is fine. This is about *legibility and composition*, not about *expressive power of conditions*.

## Out of Scope (Explicit Non-Goals)

- Replacing or migrating the existing rule builder. Rule strategies keep working forever.
- Cross-format compatibility (rule ↔ graph conversion). Two strategy types, two storage formats, two editors.
- Multi-user collaboration features. Single-user product.
- Real-time co-editing or version branching of graphs.

## Goals

In rough order of importance:

1. **Authoring feels good.** Dragging, wiring, parameterizing, and running a strategy from the canvas is satisfying enough that the user prefers it for new strategies.
2. **A node strategy backtests with the existing engine semantics.** No new accounting bugs, no behavioral drift from rule strategies on equivalent logic.
3. **The graph survives live trading.** Node strategies must run in the bot runner's polling loop, not just the backtester. This is the non-negotiable constraint.
4. **Reusable sub-graphs work.** You can save a sub-graph as a named block with exposed parameters and drop it into other strategies.
5. **One canvas covers the full lifecycle.** Entry signal, exit signal, position sizing, stop-loss are all output terminals on the same canvas.
6. **Built-in node library with optional code access** (this is the heart of the UX). The mental model has three tiers of code accessibility, but the default path requires zero code:
   - **Tier A — Built-in nodes with hand-crafted UI** (the 80% case). A curated library of purpose-specific nodes (RSI, MACD, BB, Crossover, AND/OR, Stop-Loss, Position-Size, Entry/Exit terminals, etc.) with bespoke parameter widgets where they help (sparkline previews on indicator output, dropdowns for indicator-on-indicator, range pickers for date-window params). User drags, wires, sets values. No code.
   - **Tier B — Per-parameter code mode** (the 15% case). Any parameter on any built-in node has a small toggle (or `=` prefix, Houdini-style) that flips the slider into a code field. RSI period stays `14` by default; flip the toggle and it becomes `chi("atr_period") > 30 ? 7 : 21`. The parameter is folded back to a slider when the user doesn't need it. Importantly: the code mode toggle is *per parameter*, not per node — most params on a node can stay literal while one gets adaptive.
   - **Tier C — Per-node code block** (the 5% case, on built-in nodes). An optional collapsible code section on the node for more complex pre/post logic — multi-parameter derivations, transformations of the node's output, anything that doesn't fit in a single-parameter expression. Optional. Collapsed by default. Empty by default.
   - **Wrangle nodes (separate node type)** — pure code, no curated UI, parameters auto-synthesized by scanning `chf()` / `chi()` / `chs()` / `chv()` / `chb()` calls in the code body. The escape hatch when the built-in library doesn't cover what the user wants to express. Spawning a wrangle is rare but unblocks everything.
   - **Cross-node parameter references** work from any of B, C, or wrangle contexts: `ch("../rsi_1/period")` reads another node's param, `ch("../entry/signal")` reads its output. Same path syntax Houdini uses.

## Node Categories (First-Class UX Concept)

Nodes have a **category** as a first-class property. Categories are visible in the palette (grouped sections), on the canvas (color coding on the node header), and on the wire handles (distinct colors per data type). This isn't decoration — it's how users navigate a growing library and read a graph at a glance. Houdini does this aggressively (SOPs/DOPs/CHOPs/COPs network-level, plus category coloring within each), and it's a big part of why dense graphs stay legible.

Initial categories (not final — captured to make the design concrete):

- **Tickers** *(cyan)* — a Ticker node represents a symbol + interval + provider, with multiple output handles (Open, High, Low, Close, Volume, optionally Bid/Ask). The symbol, interval, and provider are parameters on the node — meaning per-parameter code mode applies (you could have a Ticker node where the symbol is computed via `chs("...")` from a regime check). Multiple Ticker nodes can coexist on one canvas, which is the entry point to multi-asset strategies (see note below).
- **Data** *(blue)* — non-ticker series: time-of-day, day-of-week, account state (equity, cash, position), system state (current_drawdown, bars_since_entry). Sources for ambient context that isn't tied to a specific symbol.
- **Indicators** *(green)* — RSI, MACD, BB, SMA, EMA, ATR, etc. Series + params in → series out. The bread-and-butter of strategy authoring.
- **Comparisons** *(yellow)* — above, below, crossover, crosses_above/below, turns_up/turns_down. Series (or series + scalar) in → boolean series out.
- **Logic** *(orange)* — AND, OR, NOT, XOR. Boolean series in → boolean series out.
- **Rules** *(red)* — higher-level composites that bundle common patterns (e.g. "Momentum Confirm", "Volume Breakout", "Mean Reversion Setup"). These can ship as built-ins or be user-saved sub-graphs promoted into the palette. The distinction between "a sub-graph" and "a Rule node" is just whether the user has chosen to graduate it into a named, palette-visible category.
- **Settings** *(gray)* — config-style scalar sources: position size config, stop-loss config, slippage, commission, borrow rate, trailing-stop activation. Often feed into Output terminals to configure execution semantics rather than into the signal chain.
- **Code / Wrangle** *(purple)* — the escape hatch node type described above. Visually distinct so it's obvious when a graph relies on custom code.
- **Outputs** *(distinct strong color, e.g. white-on-dark)* — Entry Signal, Exit Signal, Position Size, Stop-Loss. Terminal sinks; the evaluator only cares about what's wired into them. Outputs are grouped: each strategy on the canvas is a complete (Entry, Exit, Position-Size, Stop-Loss) bundle with a name. A single graph can contain multiple output groups — see "Multi-Strategy Graphs" below.

**Handle/wire typing.** Wires carry a typed value: `series:float`, `series:bool`, `scalar:float`, `scalar:int`, `config:stop`, etc. Handles are color-coded by type so incompatible connections are visually obvious (and rejected at wire-time). This is the small detail that makes a node graph feel like Houdini instead of a wireframe puzzle.

**Why this matters now (not later).** Category is a property every built-in node and wrangle declares at definition time. If we bolt it on after shipping, every existing node needs a back-fill migration and the palette needs a re-org. Decide the category list before the first built-in node ships, even if the visual treatment evolves later.

### Multi-Strategy Graphs (One Canvas, Many Output Groups)

A graph is not constrained to one strategy. The terminology to be precise:
- **Graph (or Canvas, or Network)** — the visual workspace; the unit you save and edit.
- **Output Group (or Strategy)** — a named bundle of (Entry, Exit, Position-Size, Stop-Loss) terminals. Each output group compiles to one bot.

A graph contains 1..N output groups. Most graphs have one (the simple case). When you need coordinated strategies — pair trade, beta-hedge, sleeve allocation — you add additional output groups on the same canvas.

**Why this matters:**

- **Shared upstream computation runs once.** If both legs of a pair trade depend on a "VIX < 20 AND SPY 200SMA slope up" condition, that condition is computed once in the graph and feeds both output groups. The two-bot pair-trade emulation pattern (described above) becomes strictly cleaner when authored as one graph instead of two duplicate strategies.
- **Authoring coherence.** You see both legs of a hedge on the same canvas. The shared regime/spread/context computation is visually upstream of the divergent (long-leg vs short-leg) downstream wiring. Reading the strategy means reading one artifact, not two coordinated artifacts.
- **Backtest returns N results per run.** Each output group gets its own equity curve, trade list, and metric set. The UI can also surface combined metrics across all output groups in the graph — combined drawdown, combined Sharpe, combined exposure — which is something the two-file pattern couldn't do cleanly. This recovers most of what I'd previously listed as "given up" by avoiding multi-leg execution.
- **Each output group still spawns a single-ticker bot.** Execution layer is unchanged. The graph compiles to N bot configs, each with its own capital allocation, each managing one symbol's position. Bot-spawning UI lists output groups with a per-leg capital input.

**What this still doesn't give you (and that's fine):**
- **Atomic two-leg entries** — Strategy A and Strategy B fire on the same bar but submit orders independently. Small slippage between legs is possible.
- **Cross-bot state coupling at execution time** — Strategy B can't react to "Strategy A just stopped out" without a new inter-bot communication primitive. The graph evaluator only sees market data; bot state is held in the bot runner, not in the graph runtime.

If you ever want those, that's a separate execution-layer project. The Kind 1 + multi-output approach covers everything short of true stat-arb.

**Tier placement.** Multi-output groups slot naturally into **T4** (unified lifecycle on canvas). Once the Output category is canvas-native, allowing multiple bundles is small additional work — primarily a "Strategy" wrapper widget that visually groups its terminals and gives them a name. T2 ships with exactly one output group per graph as a hard constraint; T4 lifts that constraint as part of the same UI pass that adds Position-Size and Stop-Loss as canvas terminals.

### Side effect: ticker-as-node unlocks multi-asset strategies

Currently a strategy is implicitly single-symbol — the Sidebar picks one ticker that applies globally. Moving the ticker into the graph as a first-class node changes that without any new backend abstractions:

- **Pair trading** — two Ticker nodes (AAPL, MSFT) feed a Spread calculation that drives Entry.
- **Sector rotation** — multiple Ticker nodes (XLK, XLF, XLE), a "Strongest" selector node picks one, Entry trades that one. Per-bar dynamic asset selection.
- **Correlation/relative strength** — Ticker(AAPL) and Ticker(SPY) feed a relative-strength indicator that drives signals.
- **Beta-hedged positions** — Ticker(LONG) and Ticker(SHORT) wired to separate Entry/Exit terminals on the same canvas.

There are actually **two very different kinds of multi-ticker strategy**, with very different execution-layer cost:

**Kind 1: One traded symbol, multiple reference tickers (cheap, ship early).** The strategy trades exactly one symbol (the *primary* Ticker), but rules consult other Tickers (*reference* Tickers) as context:

- "Trade AAPL only when SPY 50-day SMA slope is up." Reference: SPY. Primary: AAPL.
- "Trade AAPL only when VIX < 25." Reference: VIX. Primary: AAPL.
- "Long QQQ when SPY breadth > 60% AND VIX < 20." References: SPY, VIX. Primary: QQQ.
- "Short MSTR when Bitcoin futures gap down at open." Reference: BTC. Primary: MSTR.

Bot runner change required: **none beyond fetching the reference series**. Still one position, one symbol, one set of fills. The backtester's existing per-bar loop works unchanged — reference Tickers' bars are just additional columns in the input dataframe.

This subsumes the existing `regime` feature entirely. Today's regime filter is a fixed two-knob feature (SPY/QQQ + lookback + threshold). Under this model it's just a pattern you compose: drag a Ticker(SPY), an SMA(50), a slope-up Comparison, AND it into your existing entry conditions. No special "regime" type needed. The current feature would either become a preset Rule node ("Regime Filter") or get retired in favor of composition.

**Kind 2: Multiple traded symbols (intentionally not built as a backend feature).** Pair trading, sector rotation, beta-hedged long/short. The naive way to support this is to expand the execution layer — multi-symbol order management on a single bot, concurrent-position accounting, multi-leg trade journaling. That's expensive and risk-management-heavy and out of scope.

**The elegant alternative: emulate Kind 2 with multiple output groups on one graph, each spawning its own single-ticker bot.** Pair trading is not, fundamentally, a new execution-layer requirement — it's two strategies that share upstream signal computation and ship to separate bots:

- One canvas with two output groups: "Long Leg" (Entry/Exit/Size/Stop) and "Short Leg" (Entry/Exit/Size/Stop).
- Shared upstream: a Spread node taking Ticker(AAPL) and Ticker(MSFT) as inputs, computing the spread, feeding "below mean" into Long Leg's Entry and "below mean" into Short Leg's Entry.
- The two legs target different primary tickers — Long Leg's primary Ticker (AAPL) reaches its terminals; Short Leg's primary (MSFT) reaches its terminals.
- Run produces two bot configs: one trading AAPL long, one trading MSFT short. Each gets its own capital allocation at bot-spawn time.

Each bot stays single-ticker at the execution layer. The "pair" is emergent at the strategy-authoring level. The cost of pair trading collapses from "build multi-leg execution" to "drop a second output group on the same canvas."

(An earlier draft of this doc suggested two separate strategy files sharing a sub-graph. Multi-output-group on one canvas is strictly better — shared computation runs once, both legs are visible together, combined metrics are reportable, and you don't need cross-canvas sub-graph reuse just to coordinate a pair.)

**What this approach gives up:**
- Atomic two-leg entries (Strategy A and Strategy B fire on the same bar, but submit orders independently — small slippage between legs is possible).
- Tight risk coupling (Strategy A's stop-out doesn't automatically close Strategy B's leg).
- Spread-as-exit (each leg has its own exit logic; the spread itself isn't an exit terminal across legs).

For the kind of pair/hedge trading a retail trader on ~$10k capital actually does (loosely coupled, regime-aware, not stat-arb), these limitations are immaterial. For real tight-coupled stat-arb, the user would need a different platform anyway.

**Implication for sub-graphs (Goal #4).** Sub-graphs were originally pitched as the unlock for cross-strategy coordination (the same regime/spread logic dropped into multiple strategies). With multi-output groups in T4, that role shifts: within-graph coordination is handled natively by multi-output, and sub-graphs become primarily a **cross-canvas reuse** mechanism — saving "Regime Filter" or "Volume Confirm" once and using it across unrelated strategies. Still valuable, but no longer load-bearing for the pair-trading story. T3 is therefore less urgent than I had it; the priority order for T3 vs T4 might want to flip in planning.

**Tier placement recommendation:**
- **T2 ships Kind 1.** Graph allows multiple Ticker nodes; exactly one can reach Entry/Exit/Position-Size/Stop terminals (the "primary"). Others are inputs to indicator/comparison/rule nodes only. Retires the regime feature as a composable pattern.
- **T3 (sub-graphs) is where the Kind 2 trick lands** — once you can save and reuse a spread/regime sub-graph, pair-style strategies become trivially expressible across multiple single-symbol bots.
- **A "true" Kind 2 (multi-symbol single-bot) execution layer is deferred indefinitely** — not because it's impossible, but because the Kind 1 + multi-bot pattern covers what the user actually needs at a small fraction of the engineering cost.

This is the kind of design move that earns its keep: a smart upstream decision (ticker-as-node + sub-graphs) makes a downstream feature (pair trading) free.

## The Authoring Model (Architectural Core)

Four authoring paths, one evaluator underneath. The paths exist as distinct UX affordances so simple work stays simple; the shared evaluator means complexity in one path doesn't fragment the runtime.

### Path 1 — Built-in nodes, literal parameters (the default)

The user drags an "RSI" node, sees a slider labeled "Period" with the value `14`, wires its output into a "Crosses Below" node, sets the threshold to `30`, wires that into the Entry terminal. Done. No code anywhere. This must be the path of least resistance, and it's what 80% of strategy authoring looks like.

Built-in nodes have **hand-crafted UI**. They are not wrangles in disguise. Where it helps, they get bespoke widgets: a sparkline preview of the indicator output, a candle-pattern selector, a session/time-of-day picker, a histogram of value distribution. Investment per built-in node is real but worth it for the common nodes; the long tail of less-common operations goes to wrangles instead.

### Path 2 — Per-parameter code mode

Any parameter on any built-in node has a code toggle. When off (the default), the parameter is a literal value edited via the node's widget. When on, the widget is replaced with a small code field; the code's return value becomes the parameter's value.

```python
# RSI node, "Period" parameter in code mode:
chi("atr_period") > 30 ? 7 : 21
```

The toggle is per-parameter, not per-node. You can have an RSI node where the period is computed and the threshold stays a literal slider. The code field has the standard scope (inputs, indicator library, bar context, `ch()` cross-references). Returns must match the parameter's declared type (int for period, float for threshold, etc.); type errors surface at edit time, not run time.

### Path 3 — Per-node code block (optional drawer)

Each built-in node has an optional collapsible code section, empty by default. This is for the cases that don't fit cleanly into a single-parameter expression — multi-parameter derivations, transforming the node's output before it leaves, computing a derived attribute from several inputs.

Example: an "RSI" node with custom output smoothing:
```python
# RSI's optional code block; runs after the node's main computation
out = pd.Series(out).ewm(span=chi("smooth_span", default=3)).mean()
```

`chi("smooth_span", default=3)` in a code block creates a *new* spare parameter on the node (in addition to the node's hand-crafted ones). This is the auto-promotion behavior that wrangles use, available inside a built-in node as an additive mechanism.

### Path 4 — Wrangle nodes (separate node type)

A Wrangle node has no curated UI. Its body is a code editor. Parameters appear by scanning the code for `ch*()` calls and auto-generating widgets — the same modern-Houdini "spare parameters" mechanism. Inputs/outputs are typed handles named in the code (`in0`, `in_close`, `out`, `out_signal`).

```python
# Wrangle node body — auto-promotes 3 parameters:
atr_p = chi("atr_period", default=14, min=2, max=50)
fast = chi("fast_period", default=7)
slow = chi("slow_period", default=21)
threshold = chf("vol_threshold", default=2.0)

atr_pct = ta.atr(in_high, in_low, in_close, atr_p) / in_close * 100
period_series = np.where(atr_pct > threshold, fast, slow)
out = rsi_variable_period(in_close, period_series)
```

Wrangles are the escape hatch. The bar to spawning one should be: "the built-in library doesn't have what I want, and shoehorning it into per-parameter code mode would be ugly."

### The Shared Layer

**Channel function API (used by paths 2, 3, 4):**
- `chf(name, default=, min=, max=)` — float scalar
- `chi(name, default=, min=, max=)` — int scalar
- `chs(name, default=, options=)` — string (dropdown if options)
- `chb(name, default=)` — bool (checkbox)
- `chv(name, default=)` — vector/tuple
- `ch("../other_node/param")` — cross-node reference (relative or absolute path syntax)
- `ch("../other_node/output")` — reading another node's output series

**Code language: Python.** No DSL. Numpy/pandas/talib in scope via a small curated `sl` module. The user already knows Python; the backend already runs Python. Same language for paths 2, 3, 4.

**Execution model.** Code runs vectorized across the bar range during backtest. The bot runner version evaluates the same code against a rolling window ending at the latest closed bar. Same evaluator, same code, different time window. This is the non-negotiable property that prevents backtest/live drift.

**Sandboxing.** None. Single-user app on the user's own machine. The user can already write arbitrary Python in `backend/`; running it inside a node is the same threat model.

**Param widget types.** Hand-crafted (built-in node, Path 1/2) and auto-generated (Paths 3 spare params, Path 4) widgets render through the same UI library — same slider, same numeric input, same dropdown. The auto-generated path is just "infer widget type from `ch*()` function used + kwargs given." A custom widget on a built-in node is the override.

### What this means for sub-graphs (Goal #4)

Sub-graphs are a saved network of any-mix of these node types (built-in or wrangle) with promoted parameters at the boundary. The sub-graph node itself looks like a built-in node in the palette but expands into its constituent network when opened. No new HDA-ness machinery to build — sub-graph IS-A network with a boundary.

## Open Design Questions (Defer to Planning)

These don't need answers now, but the plan must resolve them before code starts:

- **Evaluation model.** Vectorized (compute full series across history then sample) vs per-bar (advance one bar at a time). Vectorized is faster and matches pandas idiom; per-bar matches how bots think. Likely vectorized for backtest, with a per-bar adapter for the bot runner — but worth specifying.
- **Graph compiler vs interpreter.** Run the graph directly, or compile it to a closed-form callable for backtests? Probably interpret first, optimize later.
- **Code node sandboxing.** Single-user app, user runs their own code on their own machine. Sandboxing is not a security concern; it's only a "did the user crash their own backtest" concern. Probably no sandbox, just clear error surfacing.
- **Graph persistence format.** JSON, schema TBD. Lives in the same `strategies/` directory? Or a separate `graphs/`?
- **UI library.** `@xyflow/react` (formerly react-flow) is the obvious choice — MIT, mature, used by Pipedream/n8n/Make/Langflow. Worth a 1-day spike to confirm it handles ~50 nodes without lag.
- **Coexistence UX.** When the user clicks "New Strategy", how do they choose rule vs node? Single switcher, two top-level buttons, or two separate "New" entries in the sidebar?
- **Bot runner integration.** The existing `bot_runner._tick()` evaluates `Rule[]` against the latest bar. A graph strategy needs an equivalent — likely "evaluate the graph against the latest N bars, read the output terminals, act on the entry/exit signal." Performance budget per tick is small.

## Success Criteria

You'll know the feature is succeeding when:

- You build a non-trivial new strategy (≥5 indicators, with at least one custom code-node calculation) end-to-end on the node canvas without dropping back to the rule builder out of frustration.
- A saved sub-graph ("momentum confirm") gets reused across 3+ strategies.
- A node strategy runs as a live IBKR or Alpaca bot for ≥1 trading day with no evaluator-related errors.
- You can come back to a graph you built a month ago and understand it faster than you can read the equivalent rule list today.

## Major Risks

- **Scope.** A full T4 node editor with code nodes is realistically 10–16 weeks of solo evening/weekend work on top of a platform that already has a working alternative. The honest failure mode is "spent two months on a beautiful canvas that doesn't materially beat the rule builder for the strategies you actually trade." Mitigation: ship in tiers (read-only → editable → sub-graphs → lifecycle → code nodes), with a kill switch after each tier where you can honestly evaluate whether to continue.
- **Bot runner integration surprises.** Live trading semantics (partial fills, broker errors, polling cadence) tend to expose evaluator assumptions only in production. Tier 2 must include "runs as a bot" as exit criteria, not just "runs as a backtest."
- **Two-system maintenance burden.** Every new indicator, every new condition type, now needs to be added in two places (rule builder and node palette). Mitigation: shared backend primitives, two thin UI surfaces over the same Python.
- **The wow-factor trap.** Node editors are visually exciting and demo well, which makes it easy to keep polishing instead of trading. Hold the line on "does this make me make more money / make better strategies."

## Recommended Tiered Rollout

1. **T1 — Read-only viewer (~1–2 weeks).** Auto-render existing rule strategies as a node graph. No editing. Pure validation that spatial layout actually helps.
2. **T2 — Editable canvas with current primitives + backtest + live trading (~3–4 weeks).** Authoring works end-to-end with the same primitives the rule builder has. Output terminals: entry + exit only, sizing/stops still in sidebar. Bot runner can execute a graph strategy.
3. **T3 — Reusable sub-graphs (~2 weeks).** Save/instantiate named blocks with exposed parameters. This is where the paradigm starts to beat the rule builder.
4. **T4 — Unified lifecycle (~2 weeks).** Sizing and stop-loss become output terminals on the canvas. Sidebar config for these collapses or disappears for graph strategies.
5. **T5 — Code at all three optional levels (~4–5 weeks).** Ships together because they share an evaluator:
   - Per-parameter code mode toggle on every built-in node parameter.
   - Per-node code block (optional drawer, auto-promotes spare params from `ch*()` calls).
   - Standalone Wrangle node type with full code surface and auto-promoted params.
   - Monaco editor across all three, edit-time type checking, cross-node `ch()` references.

Total realistic estimate: **11–15 weeks** of focused part-time work. Re-evaluate after each tier.

**Tier sequencing note.** The previous version of this doc flirted with doing the code runtime before the built-in nodes were polished. That was based on a wrong reading of modern Houdini — assuming wrangles had replaced the curated node library. They haven't; they sit alongside it. So the original sequence (T1 → T2 → T3 → T4 → T5) holds: T2's literal-parameter widgets aren't throwaway — they're exactly what built-in nodes look like in steady state, and T5 augments them with code-mode toggles rather than replacing them.

## Decision Pending

This is a requirements doc, not a green light. Next step is a plan (`/ce:plan`) only if the user wants to commit to at least T1+T2. Otherwise this sits as captured thinking.

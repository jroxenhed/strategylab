# Node-Based Strategy Builder — Requirements (v2)

**Date:** 2026-05-17 (v2 rewrite of the same-day v1, reorganized after five refinement passes)
**Status:** Requirements / exploration. No plan, no code, no commitment yet.
**Relationship to existing work:** Parallel track alongside the current rule builder. New strategy type, new evaluator, new storage format. Rule builder is untouched and lives forever.

## What This Is

A **procedural strategies platform that expresses itself through a node editor.** Not the other way around. The architectural decisions follow from "procedural data flow" reasoning — universal data type, attributes, primitives, paths — not from "diagram of rules" reasoning. The node editor is a UI surface over that platform; if we built a different surface tomorrow (a textual DSL, a notebook UI) it would target the same underlying model.

The motivation: the rule builder works, but each new feature (NOT, slope conditions, regime filter, parameter sprawl) makes each rule row denser. The form is starting to hide the strategy. The user is a long-time Houdini user and recognizes both the symptoms (single-paradigm exhaustion) and a known cure (procedural data flow as the organizing principle).

## Goals (in priority order)

1. **Authoring feels good** for new strategies — the user prefers the node graph over the rule builder for non-trivial work.
2. **Backtest semantics match the existing engine** — no accounting drift on equivalent strategies.
3. **Live trading is a first-class target, not an afterthought** — the same evaluator runs in the bot runner's polling loop with no behavioral drift from backtest.
4. **Strategy intent is legible** — reading a strategy is faster than reading the equivalent rule list.
5. **The platform's capability set expands** beyond what the rule builder can express, in directions that compound (multi-input rules, composable metrics, reusable sub-graphs).

## Non-Goals

- Migrating, replacing, or retiring the rule builder.
- Converting between rule strategies and graph strategies. Two paradigms, two formats, two editors.
- Multi-user collaboration, real-time co-editing, version branching of graphs.
- Multi-symbol execution on a single bot (see "Multi-ticker, two flavors" below for why this is not needed).

---

# The Architectural Primitives

These are the foundational decisions. Everything else follows from them.

## 1. Universal Stream as the Wire Type

**One data type flows through every wire**, no exceptions: a stream consisting of bars (points), trades (primitives), and strategy-level scalars (detail attributes).

- **Points = bars.** Each bar carries built-in attributes (`@open`, `@high`, `@low`, `@close`, `@volume`, `@time`, `@index`) and any user-added attributes (`@rsi_14`, `@macd_signal`, `@my_score`).
- **Primitives = trades, sessions, regime periods, detected patterns.** A trade primitive owns the range of bars from entry to exit and carries `@entry_price`, `@exit_price`, `@pnl`, `@duration`, `@mfe`, `@mae`, `@r`. A session primitive owns ~390 1m bars. A regime-up primitive owns all bars where the regime condition held.
- **Detail = strategy-level scalars.** Total PnL, win rate, profit factor, current drawdown, exposure.

Nodes don't have typed handles like "RSI Output" or "Boolean Series." Every wire is a stream. **What nodes care about is which named attributes are present on the stream.** An RSI node reads `@close` and writes `@rsi`. A Crossover node reads two named attributes and writes `@signal`. Downstream nodes validate by attribute presence at edit time, not by handle-type compatibility at wire time.

### Why this matters

Typed-handle node editors hit a quadratic compatibility matrix as the library grows — every new indicator multiplies valid connections to maintain. Houdini avoided this 30 years ago by collapsing to one universal wire type. The cost of doing this now is small; the cost of retrofitting after a node library exists is breaking every saved strategy. Decide before T1 ships.

### What this unlocks (non-exhaustive)

- **Backtest becomes composable.** The Backtest node emits trade primitives onto the same stream. Downstream nodes consume them: `PerTradeStats` reads trade primitives and writes detail attributes (Sharpe, profit factor); `Drawdown` reads bars and writes a `@drawdown` series; custom analysis slots in between. **The metrics dictionary stops existing as a backend output type** — metrics are just attributes on the stream, and the available metrics are whatever you wire in.
- **Timeframe agnosticism.** A "Momentum Confirm" sub-graph that reads `@close` and writes `@momentum_signal` works on any stream that has a `close` attribute. 1m, 1h, daily, any symbol — the sub-graph doesn't know or care.
- **Multi-ticker via attribute namespacing or stream merge.** Ticker(SPY) writes `@spy_close`, `@spy_volume`. Or a `Merge` node combines two streams. Both clean; the second is more Houdini-idiomatic.
- **Debug-any-wire observability for free.** Click any wire → see the DataFrame at that point: which columns exist, their values, which upstream node added each. The current backtester has zero introspection between submit and metrics; this paradigm gives it without designing it.
- **Code paths converge on one mental model.** Per-parameter code, per-node code blocks, and Wrangle nodes all do the same thing: read attributes by name, compute, write attributes by name. No path needs its own typed-I/O API.

### Implementation pattern

The stream is a pandas DataFrame (bars-as-rows, attributes-as-columns) + a trades DataFrame (primitives) + a scalars dict (detail). Wires carry references to these, not copies. Nodes that only *add* columns share underlying storage with their inputs; nodes that *mutate* trigger copy-on-write. Pandas' BlockManager already does column-level sharing — the work is avoiding unnecessary `.copy()` calls in node implementations and not materializing intermediates the evaluator doesn't need. Real engineering work, but known-solved-elsewhere.

## 2. Hierarchical Paths as Namespace and Reference System

**Nodes live in a file-folder hierarchy, not a flat list with parent pointers.** This is one mechanism doing three jobs (namespace + navigation + cross-node references), which is part of why Houdini scales to enormous procedural setups.

- Every node has a path like `/long_leg/regime/spy_sma`. Not a UUID; a meaningful location in a tree.
- A **network** (sub-graph, output group, the canvas itself) is a node that contains other nodes — a directory. "Node" and "network" are the same kind of object at different recursion depths.
- The canvas root is `/`. Drilling into a sub-graph or output group is navigating into a sub-directory. A breadcrumb (`/ > long_leg > regime`) shows the current location.
- References use paths: relative (`../regime/spy_sma_slope`), absolute (`/shared/spread_calc/output`), or same-directory (`./threshold` or just `threshold`).
- Renaming a node renames a path. Existing references follow renames automatically; broken references surface in a UI panel.

### What this unifies

| Mechanism | Without paths | With paths |
|---|---|---|
| Sub-graphs | Need a separate HDA-ness abstraction | Just networks at a path |
| Multi-output groups | Need a separate "strategy slot" concept | Just sub-directories (`/long_leg/`, `/short_leg/`, `/shared/`) |
| Cross-node param refs | Need a separate reference system | Path resolution: `chf("../regime/threshold")` |
| Storage format | Flat list with parent_id pointers | Nested JSON matching the path hierarchy; diffs better in git |
| Palette saved items | Distinct from built-ins | Same kind of thing: a network with a name |

### Practical implication

For non-trivial graphs (~30+ nodes), organizing under `/regime/...`, `/entry_logic/...`, `/exit_logic/...`, `/sizing/...` is dramatically more readable than a flat canvas. The hierarchy IS the high-level structure of the strategy.

## 3. Four Authoring Paths Sharing One Evaluator

Code accessibility is layered so simple work stays simple. The four paths are visual/UX layers; underneath, they share one Python execution backend that operates on streams.

### Path 1 — Built-in nodes, literal parameters (the 80% case)

Drag an "RSI" node, see a slider labeled "Period" with value `14`, wire its output into a "Crosses Below" node, set threshold to `30`, wire into the Entry terminal. Done. No code anywhere.

Built-in nodes have **hand-crafted UI**. Bespoke widgets where useful: sparkline output preview, candle-pattern selector, session/time-of-day picker, value-distribution histogram. Investment per node is real, but worth it for common operations; the long tail goes to wrangles instead.

### Path 2 — Per-parameter code mode (the 15% case)

Any parameter on any built-in node has a code toggle (Houdini `=` prefix style, or an explicit "use code" affordance). Off by default — the slider stays. On — the slider is replaced by a small code field returning a value of the parameter's declared type.

```python
# RSI node, "Period" parameter in code mode:
chi("../atr/value") > 30 ? 7 : 21
```

Per-parameter, not per-node: a node can have one adaptive parameter and three literal ones.

### Path 3 — Per-node code block (the 5% case)

Each built-in node has an optional collapsible code section, empty by default. For multi-parameter derivations, output transformations, anything that doesn't fit cleanly into a single-parameter expression. `chf("spare_param", default=...)` inside the block creates *additional* spare parameters beyond the node's hand-crafted ones — same auto-promotion mechanism wrangles use.

### Path 4 — Wrangle nodes (separate node type)

No curated UI. Code editor is the whole node. Parameters auto-synthesized by scanning `ch*()` calls in the body. The escape hatch when the built-in library doesn't cover what the user wants to express.

```python
# Wrangle body — auto-promotes 4 parameters
atr_p = chi("atr_period", default=14, min=2, max=50)
fast = chi("fast_period", default=7)
slow = chi("slow_period", default=21)
threshold = chf("vol_threshold", default=2.0)

atr = ta.atr(stream["high"], stream["low"], stream["close"], atr_p) / stream["close"] * 100
period = np.where(atr > threshold, fast, slow)
stream["rsi_adaptive"] = rsi_variable_period(stream["close"], period)
```

### Shared layer

- **Channel functions**: `chf(name, default=, min=, max=)`, `chi(...)`, `chs(name, default=, options=)`, `chb(...)`, `chv(...)`, plus `ch("../node/param")` for cross-node references (relative or absolute paths).
- **Code language**: Python. Not a DSL. Numpy / pandas / talib in scope via a curated `sl` module. The backend already speaks Python; the user already knows it; no invention.
- **Sandboxing**: none. Single-user app, user's own machine. Same threat model as the rest of `backend/`.
- **Execution model**: vectorized over the full bar range in backtest; rolling window ending at the latest closed bar in live. Same code, same evaluator, different windowing. Backtest/live consistency is the non-negotiable property.
- **Editor**: Monaco for multi-line (Paths 3, 4); compact single-line input for Path 2.

---

# Surface-Level UX

## Node Categories

Nodes have a `category` as a first-class property: visible in the palette (grouped sections), on the canvas (color-coded header), and on wire handles (typed indicators). Categories aren't decoration — they're how users navigate a growing library and read a graph at a glance.

| Category | Color | Role |
|---|---|---|
| **Tickers** | cyan | Symbol + interval + provider; emits `@open/@high/@low/@close/@volume` and any other per-symbol attributes |
| **Data** | blue | Non-ticker series: time-of-day, day-of-week, account state, system state (drawdown, bars-since-entry) |
| **Indicators** | green | Series → series (RSI, MACD, BB, SMA, EMA, ATR, etc.) |
| **Comparisons** | yellow | Series → boolean series (above/below/crossover/turns_up/turns_down) |
| **Logic** | orange | Boolean → boolean (AND/OR/NOT/XOR) |
| **Rules** | red | Higher-level composites: "Momentum Confirm", "Volume Breakout". Some are built-in; others are user sub-graphs graduated into the palette |
| **Settings** | gray | Config-style scalar sources: position size, stop-loss config, slippage, commission |
| **Code / Wrangle** | purple | Visually distinct so it's obvious when a graph relies on custom code |
| **Outputs** | white-on-dark | Entry, Exit, Position-Size, Stop-Loss terminals (grouped per Output Group) |

Decide the category list before the first built-in node ships. Adding a category later requires a back-fill migration.

## Multi-Strategy Graphs

A graph contains **1..N Output Groups** (a.k.a. Strategies). Each Output Group is a named bundle of (Entry, Exit, Position-Size, Stop-Loss) terminals; each compiles to one bot.

- Most graphs have one Output Group (simple case).
- Pair / hedge / sleeve strategies use multiple. Shared upstream computation lives at `/shared/...`; per-leg logic lives at `/long_leg/...`, `/short_leg/...`.
- Bot-spawning UI lists each Output Group with per-leg capital input.
- Backtest returns one result per Output Group plus combined metrics (combined drawdown, combined Sharpe, combined exposure).

**What this gives up**, on purpose:
- Atomic two-leg entries (each Output Group's bot submits orders independently).
- Cross-bot execution-time coupling (Strategy B can't react to Strategy A's stop-out without new inter-bot communication).

For loose retail pair / hedge trading, these don't matter. For tight stat-arb, a different platform is the right tool anyway.

## Multi-Ticker, Two Flavors

The platform supports **Kind 1** (one traded symbol, multiple reference tickers) from day one. **Kind 2** (multiple traded symbols on one bot) is intentionally not built — it's emulated via multi-output groups instead.

**Kind 1 — context-aware single-ticker strategies.** "Trade AAPL only when VIX < 25." "Long QQQ when SPY breadth > 60% and VIX < 20." "Short MSTR when BTC futures gap down at open." One traded symbol; reference tickers feed indicator and rule nodes. Bot runner change: fetch the reference series. Execution layer unchanged. This subsumes the current `regime` feature as a composable pattern.

**Kind 2 — actively trading multiple symbols.** Pair trading, sector rotation, beta-hedged longs/shorts. **Not a backend feature.** Instead: author two Output Groups on the same canvas, each with its own primary Ticker, sharing upstream computation (`/shared/spread_calc/...`). Each Output Group spawns its own single-ticker bot. Pair trading becomes one canvas, two bots, zero new execution-layer code.

## Sub-Graphs (Networks-at-a-Path)

A sub-graph is a saved network with a declarative interface: `reads: [@close, @volume]; writes: [@momentum_signal]`. Drop it into any context that provides the reads; consume the writes downstream. With paths as the namespace, there's no special HDA-ness mechanism — a sub-graph is just a directory with promoted parameters at its root.

Cross-canvas reuse is the primary value. Within-canvas coordination is already handled by multi-Output Groups, so sub-graphs aren't strictly needed for the pair-trade pattern. T3's priority is therefore lower than the original draft suggested — possibly even swapped with T4 in planning.

---

# Tier Rollout

Each tier ships in a state where the user can honestly evaluate whether to continue. The pipeline is gated, not pre-committed.

| Tier | Scope | Estimate | Exit criterion |
|---|---|---|---|
| **T1** | Read-only viewer; auto-render existing rule strategies as stream graphs with attribute names visible on wires | 1–2 wk | User reads a non-trivial existing strategy and finds the spatial+attribute view more legible than the rule list |
| **T2** | Editable canvas; built-in node library; single Output Group; backtest works; **bot runner can run a graph strategy live for ≥1 trading day** | 4–5 wk | Bot runs a graph strategy as a paper-trading session without evaluator-related errors |
| **T3** | Sub-graphs: save / instantiate / promote-to-palette; declarative reads/writes interface | 1–2 wk | A saved sub-graph is reused across 3+ strategies |
| **T4** | Unified lifecycle on canvas (sizing + stop-loss as terminals) **and** multi-Output Groups | 2–3 wk | A pair-style strategy authored as one canvas + two bots runs end-to-end |
| **T5** | Code at all three levels (per-param, per-node, Wrangle nodes); Monaco editor; edit-time type checking; cross-node `ch()` references | 3–4 wk | User builds a strategy that uses code mode on at least one parameter and one custom Wrangle node |

**Total realistic estimate: 11–16 weeks** of focused part-time work. Re-evaluate after each tier.

**T1 is more important than its scope suggests.** Showing an existing strategy rendered with the stream-attribute model visible — wires labeled with the attributes they carry (`@close`, then `@close + @rsi`, then `@close + @rsi + @entry_signal`), nodes labeled with reads/writes — is how the paradigm becomes intuitive. Documentation about the attribute model is much less effective than seeing it on a real strategy.

**Sequencing notes:**
- T2's bot-runner exit criterion is non-negotiable. If a graph strategy can't run live, we've built a backtester clone with a fancier UI.
- T3 and T4 can swap in planning if the pair-trade story matters more than cross-canvas reuse in early use.
- T5's three code paths ship together because they share an evaluator.

---

# Risks

1. **Scope.** 11–16 weeks of solo evening / weekend work on top of a platform that already has a working alternative. The honest failure mode: two months on a beautiful canvas that doesn't materially beat the rule builder for the strategies the user actually trades.
   *Mitigation:* tier gating with a real kill switch after each tier.

2. **Bot runner integration.** Live trading surfaces evaluator assumptions only in production. Partial fills, broker errors, polling cadence — none of these show up in backtest.
   *Mitigation:* T2 must include live-bot operation as exit criterion, not just backtest success.

3. **Two-system maintenance.** Every new indicator and condition has to land in both the rule builder and the node library.
   *Mitigation:* shared backend primitives; two thin UI surfaces over the same Python evaluators.

4. **The wow-factor trap.** Node editors demo well and feel productive; that's distinct from producing better strategies. Easy to keep polishing instead of trading.
   *Mitigation:* hold the line on "does this make me make better strategies / more money." If T1 doesn't make existing strategies more legible, kill the project.

5. **Paradigm shift cost.** "Everything is attributes on a stream" is unfamiliar to users coming from the rule builder. Houdini's UX for this took decades to refine.
   *Mitigation:* T1 viewer with attribute names visible from first look. Don't explain — show.

6. **Performance under naive implementation.** A 30-node graph over 5 years of 1-minute bars (~500k rows × 50+ columns) is real data; copying it at every node will be slow.
   *Mitigation:* copy-on-write stream wrapper, lean on pandas BlockManager, audit hot paths in T2.

---

# Success Criteria

You'll know the feature is succeeding when:

- A non-trivial new strategy (≥5 indicators, with at least one custom code-node calculation) gets built end-to-end on the canvas without dropping back to the rule builder out of frustration.
- A saved sub-graph ("regime filter" or "momentum confirm") is reused across 3+ strategies.
- A node strategy runs as a live IBKR or Alpaca bot for ≥1 trading day with no evaluator-related errors.
- A pair-style strategy is authored as one canvas + two Output Groups + two bots, with combined metrics legible in the backtest panel.
- Returning to a graph one month later, the strategy is faster to re-understand than reading the equivalent rule list today.

---

# Decision Pending

This is a requirements doc, not a green light. The next step is a plan (`/ce:plan`) only if the user commits to at least T1+T2 (the cheapest two tiers that together prove the paradigm). T3+ can be decided after T2 ships. Otherwise this sits as captured thinking and the rule builder continues to evolve incrementally.

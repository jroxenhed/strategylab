# Node builder finish plan (F435): plan of record

- **Date:** 2026-09-29
- **Branch:** `feat/nodebuilder-t3-t5`
- **Status:** Plan of record. Wave 0 is in progress in another workflow. Waves 1 to 7 are planned here.
- **Inputs:** the gap analysis and its critic addendum (`.run/F435/audit/gap-analysis.md`), the five audit reports next to it, vision v2 (authoritative) and v1, and the mockup (`.run/F435/vision/node-editor-mockup.png`).
- **Companion docs:**
  - Code-node design: `docs/plans/2026-09-29-node-builder-code-nodes-design.md` (decided by John 2026-09-30: real Python everywhere, no sandbox).
  - UI/UX foundation spec: `docs/design/nodebuilder/ui-ux-spec.md`.
  - Surface specs: `docs/design/nodebuilder/surfaces-w1-w4.md` and `docs/design/nodebuilder/surfaces-w5-w7.md`.

All paths in this doc are relative to `/Users/jroxenhed/Documents/strategylab/` unless they start with `/`. Line numbers drift. Grep for the named symbol before you edit.

**How to use this doc.** This is the only plan implementers follow. If this doc and the gap analysis disagree, this doc wins. Each wave lists its items, the files each item owns, the API contracts, the tests and the acceptance checks. An item may edit only the files it owns. If an item needs a file it does not own, stop and ask the orchestrator.

---

## 1. Summary and scope

### 1.1 Plain-English summary

- **Where we are.** You can open any rule strategy as a node graph, edit it and backtest it. The backtest matches the rule backtest. But you cannot save a graph. A node has only one anonymous plug in and out. There is no undo, no side panel and no real results view.
- **What this plan builds.** Seven waves that turn the editor into the Houdini-style tool in the vision:
  - **W1** makes graphs real saved objects on the server, adds undo, and shows errors before you press Run.
  - **W2** rewrites the engine to work on whole columns at once. Nodes get named inputs, and you pick which attribute (for example `@rsi`) a node reads. This lets you build "MACD crosses its signal line" by hand.
  - **W3** adds the Houdini editor feel: the Inspector side panel, flags, copy and paste, framing keys, network boxes, sticky notes, tidy layout and context menus.
  - **W4** lets you see the data: a spreadsheet for any wire, small charts on nodes, auto-cook, and graph results in the app's real chart and Results panel.
  - **W5** adds the trade lifecycle to the canvas: size, stop, trailing stop and time stop terminals, Output Groups (several strategies on one canvas), reference tickers such as SPY, regime inside the graph, and spawning one stopped bot per group.
  - **W6** adds folders of nodes (sub-networks) you can dive into, plus reusable saved assets with promoted parameters.
  - **W7** adds code at three levels (a formula on one parameter, a code block on a node, and a Wrangle node), in real Python, as the vision says.
- **Why the order.** Saving and undo come first because every later feature needs them. The column engine comes before code, the data sheet and signals-of-signals, because all three need whole columns. Lifecycle and Output Groups (vision T4) come before sub-networks (vision T3), because pair trades and regime are what John trades with. The vision allows this swap.
- **Code and safety.** The app runs on a public server that holds broker keys. On 2026-09-30 John decided that code nodes run real Python there anyway, with no sandbox, knowing that a stolen login could then run any code and read the keys. We add only cheap guards that block nothing: an off switch, an audit log, and pauses instead of crashes (see D1 and the code-nodes design note).

### 1.2 Scope

In scope for this build: **W0 (in progress) and W1 to W7.**

Out of scope: **W8.** Its items are follow-ups, listed in section 10:
- position-state Data nodes (`bars_in_trade`, `unrealized_pct`, `entry_price`, `equity_drawdown`);
- backtest-as-node, trades as primitives, metrics as detail attributes;
- a node density toggle (Atom / Standard / Rich);
- the candle-pattern selector widget (there are no pattern nodes yet).

Non-goals from the vision stay non-goals: no rule-to-graph or graph-to-rule conversion beyond the existing one-way auto-render, no multi-user editing, no multi-symbol bot, no atomic multi-leg orders.

---

## 2. Architecture decisions (D1 to D11)

These are decided. Implementers do not reopen them. Each one already includes every critic amendment that touches it.

### D1. Code nodes run real Python, everywhere

- **Decision (John, 2026-09-30).** Code is real Python, on the Mac and on the public VM, as the vision says: numpy, pandas, pandas_ta and a curated `sl` helper module in scope, full builtins, no sandbox. Houdini's `@attr` sigil is rewritten to `stream["attr"]` with `tokenize` before `compile()`. slx, the interpreted formula language recommended on 2026-09-29, is rejected.
- **Risk, accepted.** The backend runs on `strategylab01` behind one Google login. It holds the IBKR and Alpaca keys and runs live bots. A stolen session can run any code there and read the keys. John chose real Python with this risk stated to him. Graph files carry code, so importing a graph means running a program.
- **Where it runs.** In-process, through one function (`run()` in `backend/nodebuilder/code/runtime.py`), in the backtest routes and in the bot runner. In the bot runner it runs inside the executor job (see D5), never on the event loop. Backtest/live parity holds by construction.
- **Robustness, not security.** A wall-clock guard per cook (10 s in a bot tick, 60 s in a backtest) fails the cook, and the bot pauses with a `pause_reason`. A runaway thread cannot be killed in-process; it leaks until the backend restarts. Exceptions become diagnostics with the node id, a 1-based line and a 0-based column.
- **Ticker params are not code-able.** An `{"expr": ...}` value on a Ticker's `symbol`, `interval` or `prefix` is a compile error (`ticker_param_not_codeable`). The vision's `chs()`-computed symbol would break the one-symbol-per-bot model.
- **Kill switch.** `SL_CODE_NODES=0` makes compile reject code-bearing graphs (`code_disabled`). Existing code-bearing bots pause with a `pause_reason`. They do not crash-loop. The default is on everywhere.
- **Audit trail.** The node id and a sha256 of each code snippet are logged on every code-bearing graph save and every bot start. The log blocks nothing.
- **Full design** (sugar rule and vectors, auto-promotion, namespace, `sl`, guards, diagnostics, tests): `docs/plans/2026-09-29-node-builder-code-nodes-design.md`. **Decision status: decided by John 2026-09-30.**

### D2. Graph storage: one JSON file per graph on the server

- **Location.** `$STRATEGYLAB_DATA_DIR/graphs/<graph_id>.json`, one file per graph. Library assets (W6) go in `$STRATEGYLAB_DATA_DIR/graph_library/<name>@<version>.json`.
- **Writes.** `backend/nodebuilder/storage.py` uses the existing `fileutil.atomic_write_text` plus a per-file `threading.Lock`. No new temp-file code. This follows the F444 lesson: an old snapshot must never land last.
- **Orphan temp files.** `main.py` passes `DATA_DIR/graphs` and `DATA_DIR/graph_library` to `cleanup_orphan_tmps`, because that function does not recurse.
- **Envelope.** `{id, rev, name, description, created_at, updated_at, graph}`. `PUT` must carry the caller's `rev`. A mismatch returns 409. A success increments `rev`.
- **Frontend.** The server is the source of truth. The frontend keeps an unsaved-draft autosave per graph in localStorage (`strategylab-graph-draft:<id>`). On reload, a draft newer than the saved `rev` offers a restore.
- **Stored format is a flat node map.** `nodes: {id: Node}`. The nested tree in the vision is a *view* built from `parent` pointers. See divergence V1 in section 2.12.
- **Versioning.** `_version: 2` in W1 (new fields only). `_version: 3` in W2 (attribute semantics). `Graph` has a `model_validator(mode="before")` that runs the migration chain. So every load path (API, bots.json, library, seed) migrates the same way.
- **Seed.** A one-time `POST /api/graphs/seed` imports the old localStorage key. It accepts both the object shape and the array shape. It is idempotent.

### D3. Hierarchical paths on top of stable ids

- **Node ids stay opaque and stable.** That covers today's `/rsi_ab12` ids, UUIDs and new ids. New ids from the frontend are `n_` plus 8 lowercase base-36 characters. Allowed id pattern: `^[A-Za-z0-9_/.:-]{1,128}$`, and an id must not contain `::` (reserved for flattened ids, see D7).
- **New fields.** `Node.name` is the leaf name. It is unique among siblings and matches `^[a-z_][a-z0-9_]{0,63}$`. `Node.parent` is the id of the containing network node, or `null` for root. `parent` replaces the unused `subgraph` field.
- **Path.** `path(node) = path(parent) + "/" + name`. Root is `/`. Wires reference ids, so a rename never breaks a wire.
- **Uses.** Paths address `ch()`, breadcrumbs, promoted-param targets and diagnostics. Lookup is `find_by_path(graph, path, relative_to=None)`. Renaming is `rename_node(graph, id, new_name)`. It rewrites every stored path string that pointed at the old path (promoted-param targets from W6, `ch()` strings from W7). Python and TypeScript use the same algorithm and the same test vectors file.
- **Broken references** show up as `ref_broken` diagnostics from `/validate`.
- **Migration v1 to v2.** `name = sanitize(id.lstrip("/"))`. For UUID-like ids, `name = f"{type}{n}"`. Then `parent = None`. Parity tests keep passing, because they assert backtest equality, not id shapes.

### D4. Universal stream: nodes pick attributes by parameter

This resolves the mockup: one output port per node, stream inputs, and parameter rows such as `source @close` and `a @rsi`.

- **The stream** is defined in section 3. In short: per-bar columns (points), scalars (detail) and reserved primitives.
- **Ports.** Every node has exactly one output port, id `out`. Input port ids are always `in0, in1, ...`. The catalog `PortSpec` gives each port a label (for example "a", "b"), and says whether the port list is dynamic (logic, merge) and how many ports are required. `Wire.to_port` names the input port. `Wire.from_port` is always `"out"`.
- **Merging.** A node's input stream is the union of the streams on its input ports. Two ports carrying the same attribute from the same writer is not a clash (a diamond from one Ticker is fine). The same name from two different writers is a **clash**. A clash is an error (`attr_clash`) only if the node, or any node downstream, reads that name. Otherwise it is a warning, and the name is hidden downstream. So a clash can never silently change a result.
- **Reading.** Nodes read attributes through parameters of type `attr` (one name) or `attr_list` (several names). Examples: indicator `source`, comparison `a` and `b`, logic `terms`. **Operand order comes from these parameters, not from wire order or port order.** This fixes bug 5 for good.
- **Default reads.** When a wire is connected, an empty `attr` param defaults to the **primary write** of the node upstream on that port. The primary write is the node's first `write` param.
- **Writing.** Nodes write under editable names, stored as params of type `write`: `out` for one output, or named slots such as `out_line`, `out_signal`, `out_hist` for MACD. Defaults come from the catalog (`@rsi`). When a new node is created, the frontend makes the name unique in the graph (`@rsi_2`).
- **Wire labels are derived.** A wire's label is the list of attributes that the consumer reads from that wire. The full stream shows on hover and in the Inspector. See divergence V3.
- **Validation.** Presence (the attribute exists) and type (float or bool, point or detail) are checked at validate time. This is the vision's "edit-time attribute-presence check".
- **Performance.** Streams hold references to columns in one per-cook column store. Adding an attribute copies nothing.
- **Migration v2 to v3 (W2).** Each v1/v2 `wire.attr` becomes the matching consumer param, in the current wire-list order. So today's graphs compile to the same result. The Ticker `source` param is dropped (see D11).

### D5. Columnar evaluator with a kernel/domain split

- **Decision.** Replace per-bar closures (`PerBarOp.fn(attrs, i)`) with column ops. Each node type registers a `compile_fn`. At cook time it runs as `fn(inputs: Stream, params) -> Stream` once over the full index. A registry replaces the if-chain in `compile.py`. `NODE_IMPLS` and the 20 `*_impl` functions are folded into the registry or deleted.
- **Parity rules.**
  - Crosses use `prev < r_prev and now >= r_now` via `.shift(1)`, exactly as today.
  - Bar 0 is `False` for every cross and slope test.
  - AND/OR over a missing input is a compile error, not a vacuous `True`.
  - Bypass is pass-through: the output stream equals the input stream on `in0`, as in Houdini.
- **Unknown node types are errors.** Compile raises `UnsupportedNodeError(node_id)` for any node type that is not compile-active. W0 item 0.H adds this. The read-only T1 view still renders such nodes.
- **Settings as detail.** Settings nodes write detail attributes (`@stop_pct`, `@size_frac`, `@slippage_bps`, `@commission_pct`, `@borrow_rate_annual`). Terminals and the simulator bridge read them. The out-of-band `simulator_settings` list is kept only as a derived view for the W0 overlay code, and is removed in W5.
- **Lookback.** Compile computes `CompiledProgram.required_lookback_bars`. It is the largest window among indicator and rolling nodes, times 3 for recursive (EMA-family, Wilder RSI, ATR) nodes, plus HTF and reference-ticker padding. The bot derives its fetch window from it (W2 item 2.D).
- **Live path.** The bot runner computes the whole cook **inside `self._run_in_executor`**, then reads `.iloc[-1]`. It never evaluates on the event loop. This follows the Key Bug "never block the polling loop". Reference tickers are fetched with `fetch_ohlcv_async` under `asyncio.gather`, never a bare `_fetch` and never `yf.download`.
- **Closed bars.** Whether `iloc[-1]` is a closed bar depends on the provider. Graph bots use the same rule as rule bots. Do not invent a second rule.
- **Adapter.** `evaluate_graph(program, attrs, i)` stays as a thin adapter, so `_run_simulation`'s `buy_signal_fn` contract is unchanged.
- **Package layout.**
  - `backend/nodebuilder/kernel/`: `stream.py`, `registry.py`, `evaluate.py`, `schema.py`, later `flatten.py` and `params.py`. No trading words in this folder.
  - `backend/nodebuilder/trading/`: node modules that self-register on import (`nodes_indicators.py`, `nodes_logic.py`, `nodes_compare.py`, `nodes_settings.py`, `nodes_terminals.py`, `nodes_data.py`, `nodes_math.py`, later `nodes_code.py`), plus `sim_bridge.py` and `align.py`.
  - `compile.py` and `evaluator.py` remain as facades, so imports in tests and `bot_runner.py` keep working.

### D6. The wire inspector reads a cook cache, not the backtest path

- **Cook cache.** `backend/nodebuilder/cook_cache.py` is an in-process LRU: 8 entries, 10-minute TTL, about 200 MB total (estimated with `nbytes`).
- **Cache key.** `(eval_hash(graph), frames_fingerprint, interval, start, end, source)`.
  - `eval_hash` ignores positions, names, notes, annotations and every flag except bypass.
  - `frames_fingerprint` is a tuple of `(symbol, interval, row_count, last_bar_timestamp)` for every frame the cook fetched. Without it, a 10-minute cache entry could show bars that differ from a 2-minute intraday `_fetch` refresh.
- **Backtest.** The backtest route stores a reference to its cook in the cache (O(1)). Its response gains `cook_id`.
- **Endpoints.** `POST /api/nodebuilder/inspect` and `POST /api/nodebuilder/preview` (contracts in W4). A cache miss re-cooks. That costs the same as a backtest without the simulation.
- **Live bots never touch the cache.** `bot_runner.py` must not import `cook_cache`.
- **No debug fields on `/backtest`.** Debug data lives only behind `/inspect` and `/preview`.

### D7. Networks, Output Groups and N bots

- **Networks.** A network is a node whose children point to it through `parent`. Network node types: `subnet`, `output_group`, and `regime` (a subnet with a fixed role). Boundary nodes `subnet_input` and `subnet_output` live inside a network. Wires connect only siblings (nodes with the same `parent`). A stream enters a network through the network node's input ports, which appear inside as `subnet_input` outputs. It leaves through `subnet_output`, whose input becomes the network node's output. This is strict Houdini behaviour.
- **Flatten.** `kernel/flatten.py` turns the nested graph into one flat evaluation graph before compile. Inline networks keep their node ids. Library asset instances (W6) get composite ids `outerId::innerId`, so the same asset can appear many times. `flatten` returns a `flat_to_source` map, so diagnostics point at the node the user sees. Network core lands in **W5 item 5.0**, before Output Groups and regime use it.
- **The group node.** `output_group` has params `direction` (`long | short | regime_switch`), `ticker` (a path to the group's primary Ticker node), and `capital_weight` (default 1). Its name is its node `name`.
- **Group children.** Exactly one `entry` and one `exit` (for `regime_switch`: one of each per side, marked by the terminal param `side`), at most one each of `size`, `stop`, `trailing_stop`, `time_stop` and `regime`.
- **Implicit group.** If a graph has no `output_group` and its terminals sit at root, compile builds an implicit group named `main` with direction from the request. So migrated and auto-rendered graphs keep working. Terminals outside a group in a graph that has groups are a compile error (`group_terminal_outside`).
- **Cost settings are scoped constants.** An unwired cost settings node (slippage, commission, borrow rate) applies to every group in its network and below. The nearest one wins. At root this matches today's behaviour.
- **Size and stop.** They come from the group's `size` and `stop` terminals, wired as in the mockup (`@size_frac`, `@stop_pct`). An unwired `position_size` or `stop_loss` settings node at root still works as a scoped constant (so old graphs work). If both exist, the terminal wins and validate warns (`setting_shadowed`).
- **Compile output.** `CompiledProgram.groups: list[GroupProgram]` with `name, node_id, primary_ticker_id, symbol, interval, direction, entry_attr(s), exit_attr(s), size, stop, trailing, time_stop, regime_attr, on_flip, costs`. Upstream columns are cooked once and shared.
- **Backtest.** One `_run_simulation` per group, each with capital `initial_capital * weight / sum(weights)`. The combined result uses the summed equity curve on the union of the group indexes, forward-filled. Combined summary includes return, max drawdown, Sharpe, `exposure_pct` (share of bars with any leg in a position) and `gross_deployed_pct` (average share of total capital in positions).
- **Spawn.** `POST /api/graphs/{id}/spawn` creates **N bots, all stopped**. Each leg is built as a real `BotConfig(...)` and all legs go through one new `BotManager.add_bots(configs)` call: one lock, one save. There is never a parallel request model that duplicates `BotConfig` fields (Key Bug "silent drop of bot config fields").
- **Bot fields.** `kind: "graph"`, `graph` (a snapshot with library assets baked in, so a later library edit cannot change a live bot), `graph_id`, `graph_rev`, `graph_group`, `graph_direction_mode`. `symbol` and `direction` come from the group.
- **Bidirectional bots.** `BotConfig.is_bidirectional` is a derived property: rule regime enabled, **or** a graph bot whose `graph_direction_mode == "regime_switch"`. Every `cfg.regime and cfg.regime.enabled` check in `bot_manager.py`, `bot_runner.py` and the journal/P&L callers switches to it. Otherwise a graph `regime_switch` bot would compound capital from one side's P&L only and skip the exclusive-symbol guard.
- **Keeping bots current.** A dedicated `POST /api/bots/{id}/graph_update {graph_id, rev}` loads the snapshot server-side, sets `graph` and `graph_rev` together, and reuses the in-position 409 guard. It never goes through the generic PATCH, because `UpdateBotRequest` duplicates `BotConfig` fields and would drop the new ones.
- **Out of scope** (as in the vision): atomic multi-leg orders and cross-bot coupling.

### D8. Reference tickers (Kind 1) and regime in the graph

- **Fetch.** Every Ticker node is fetched through the TTL-cached fetch path. In the bot runner, fetches run concurrently with `fetch_ohlcv_async` and `asyncio.gather`.
- **Index.** The primary Ticker of each group defines that group's bar index.
- **Prefix.** A non-primary Ticker has a `prefix` param. It defaults to the lowercase symbol. It writes `@spy_close` and so on. The primary Ticker writes plain `@close`.
- **Alignment.** A coarser reference interval uses the existing no-lookahead `align_htf_to_ltf` (shift by one bar). The same interval uses an exact timestamp join plus forward fill, which is never ahead of the primary bar. Lookback padding reuses `htf_lookback_days`. `HTFGraphNotSupportedError` is retired.
- **Regime.** `from_rules` renders `RegimeConfig` as a `regime` network: HTF ticker, indicator or rules, comparison, and a `held_for` node (the `min_bars` rule). Its output feeds the group's `regime` terminal, which has `on_flip: hold | close_only | close_and_reverse`. The simulator receives `regime_active_series` from that terminal. Per-direction regime rules (b23) map to a group with `direction: regime_switch` and entry/exit terminals per side.
- **Removals.** Delete `RegimeUnsupportedError`, the compile check and the F274 strip in `store.ts`. Un-skip the 2 regime parity tests.

### D9. One catalog source of truth: the backend

- `NodeCatalogEntry` gains `params: list[ParamSpec]` and `inputs: list[PortSpec]` (shapes in section 4.3).
- `backend/scripts/export_nodebuilder_catalog.py` writes `frontend/src/features/nodebuilder/catalog.generated.ts`. `catalog.ts` keeps only UI extras (glyph, colour) keyed by node type name.
- Drift is caught by **extending** the existing `backend/tests/nodebuilder/test_catalog_consistency.py`. It regenerates into a temp file and diffs against the committed file. No second drift test.
- After any wave item changes the catalog, **the orchestrator** regenerates `catalog.generated.ts` once, after the backend items land and before the frontend items merge.
- Library assets (W6) join the palette at runtime from `/api/graph_library`.

### D10. Graph results use the app's Results panel and chart, through their own state

- **Separate state.** App gains a `graphResult` state: `{origin: "graph", graphId, rev, request, response, displayedGroup}`. A graph run is **never** written into `lastRequest` or `backtestResult`. `lastRequest` is persisted and re-sent by the Optimizer, WFA and Sensitivity panels, and it feeds auto-render. Writing a graph run there would corrupt all of them.
- **Displayed result.** `Results.tsx` and the Chart take the displayed result as a prop (rule result or graph result, by view). Rule-only panels show "Not available for graph results".
- **View switch.** Showing a graph result sets the chart's ticker/interval/source view state directly. It never calls `onTickerChange`, because that handler clears `backtestResult`.
- **Split view.** Graph view gets a vertical split (graph above, chart below) with `react-resizable-panels`. Chart rules from Key Bugs Fixed apply:
  - Move the existing Chart component into the split. Do not duplicate it.
  - Use lightweight-charts v5 `autoSize` only. Never add a ResizeObserver plus `applyOptions` (the F218 60 Hz repaint loop).
  - Keep the teardown guards: null refs before `chart.remove()`, read `chartRef.current` dynamically in `syncWidths`, and keep the try/catch guards.
  - A display-flag overlay sub-pane (stretch goal) uses whitespace warmup entries, an explicit `priceScaleId` and `toET()`.

### D11. Ownership: who owns each setting in graph view

The sidebar and strategy settings panel (`frontend/src/features/strategy/StrategyBuilder.tsx`) and the graph must never both decide the same value.

| Setting | Owner | From wave | Graph source |
|---|---|---|---|
| Date range (start, end) | Sidebar | now | none |
| Initial capital | Sidebar | now | none (groups split it by `capital_weight`) |
| Data source (yahoo, alpaca, alpaca-iex, ibkr) | Sidebar (backtest), spawn dialog (bots) | W2 | the Ticker `source` param is removed in the v3 migration |
| Primary symbol and interval | Graph | W5 | group's primary Ticker node. Before W5, the sidebar's symbol and interval, shown read-only on the Ticker node as "from sidebar" |
| Direction | Graph | W5 | `output_group.direction`. Before W5, the request direction |
| Position size | Graph | W4 | `size` terminal or `position_size` setting; engine default 1.0 |
| Stop loss | Graph | W4 | `stop` terminal or `stop_loss` setting; default none |
| Trailing stop | Graph | W5 | `trailing_stop` terminal; default none |
| Time stop (max bars held) | Graph | W5 | `time_stop` terminal; default none |
| Costs (slippage, commission, borrow rate) | Graph | W4 (borrow W5) | cost settings nodes; engine defaults 2.0 bps, 0, 0.5 % |
| Dynamic sizing, skip-after-stop, trading hours | Sidebar, marked "applies to graph" | now | no node yet (follow-up) |
| Bot-only: max spread, drawdown auto-pause | Bot config | now | none |

Rules:
- **Precedence on the backend:** graph node value, then request field, then engine default. The backend keeps accepting the old request fields, so the parity tests do not change.
- **The frontend does not send graph-owned fields** in graph view (W4 item 4.D). So a greyed field can never change a graph result.
- **Greyed fields.** In graph view, graph-owned fields in `StrategyBuilder.tsx` are disabled with the tooltip "Set by graph". The list lives in one constant, `GRAPH_OWNED_FIELDS` in `frontend/src/features/nodebuilder/ownership.ts`.
- **Graphs are listed only in the Graph Browser** (and the AddBotBar graph picker), not in the rule strategy picker. Two paradigms, two lists. The W1 surface spec S02 confirms the wording.

### 2.12 Deliberate divergences from the vision

The vision says "diverge from Houdini (and from this doc) deliberately and explicitly". These are the divergences, each with its reason.

| # | Vision says | We do | Why |
|---|---|---|---|
| V1 | Storage is nested JSON matching the path tree | Flat `nodes: {id: Node}` map with `parent` pointers; the tree is a view | Parity tests, `from_rules`, bot snapshots and the React Flow mapping are all flat. Flat maps diff well when ids are stable. Houdini's `.hip` is not a literal nested tree either. |
| V2 | T3 (sub-graphs) then T4 (lifecycle, groups) | W5 lifecycle and groups, then W6 assets | Pair trades and regime are what John trades with. The vision allows the swap. The network core moved into W5 (item 5.0) so nothing is retrofitted. |
| V3 | Wire labels accumulate (`@close`, then `@close + @rsi`, ...) | A wire label shows only what the consumer reads; the full stream shows on hover and in the Inspector | Accumulated labels grow unreadable on real graphs. The mockup (which John liked) shows consumer reads. |
| V4 | Code is real Python with numpy/pandas/talib, no sandbox | **Withdrawn 2026-09-30: no divergence.** Code is real Python with numpy, pandas, pandas_ta and `sl`, no sandbox. (talib is not installed; it joins once it is added to `backend/requirements.txt`.) | John chose the vision; slx is rejected. See D1. |
| V5 | `chs()` can compute a Ticker symbol | Ticker `symbol` and `interval` cannot take an expression | A computed symbol breaks the one-symbol-per-bot model and the spawn contract. |
| V6 | Data category includes account and system state (bars since entry, drawdown) | Position-state nodes wait for W8; `time_stop` is a terminal evaluated inside the simulator | Position state depends on the simulation, which breaks the pure-column model. Keeping it inside the simulator is the only safe place for now. |
| V7 | Category "Comparisons" and "Indicators" cover series work | The `signal` category is relabelled "Math & Signal" and holds `math`, `shift`, `rolling`, `constant`, `merge` | The vision has no home for plain series math. |
| V8 | Stream = points + primitives + detail, all live | Points and detail are live in W2; primitives are reserved and typed but have no producer until W8 | Deciding the format now avoids a later format change. Producing trades as primitives is W8 work. |
| V9 | Per-parameter code can return a per-bar series (adaptive RSI period) | Parameter expressions return scalars; per-bar logic goes in a Wrangle | Indicator params are constants over a cook. A Wrangle `np.where(...)` over two fixed-period RSIs expresses the adaptive case. |

---

## 3. The stream schema (decided now)

Vision v2 section 1 warns that changing the stream format after saving exists "breaks every saved strategy". So the format is fixed now, before W1 stores anything.

### 3.1 Version

- `STREAM_SCHEMA_VERSION = 1`, defined in `backend/nodebuilder/kernel/stream.py` (W2) and mirrored as a constant in `frontend/src/api/nodebuilder.ts` (W1).
- Every saved graph carries `stream_schema: 1` from W1 on. Library assets (W6) record the version of their interface. `/inspect` and `/validate` responses include it.
- A future format change bumps this number and ships a migration. W8 must be able to add producers of primitives and detail **without** bumping it.

### 3.2 Shape

```text
Stream (runtime, one per node output)
  points:  ordered map  attr_name -> ColumnRef     per-bar values aligned to the group's bar index
  detail:  ordered map  attr_name -> scalar         one value per cook
  prims:   map          prim_kind -> PrimTable      RESERVED: no producer until W8; carried through untouched
  written_by: map       attr_name -> node_id        provenance for every points and detail attribute
```

- **Attribute names** carry the sigil in JSON and in the UI: `^@[a-z_][a-z0-9_]{0,63}$`. One name lives in exactly one class (point or detail) within a stream.
- **Point dtypes:** `float` (float64, NaN allowed for warmup) and `bool` (numpy bool, never NaN; warmup bars are `False`).
- **Detail dtypes:** `float`, `int`, `bool`, `str`.
- **Built-in point attributes** written by a primary Ticker: `@open @high @low @close @volume @time @index`. A reference Ticker writes `@<prefix>_open` and so on.
- **Detail attributes written by settings nodes:** `@size_frac` (0.01 to 1.0), `@stop_pct` (percent, for example 2.5), `@slippage_bps`, `@commission_pct`, `@borrow_rate_annual` (percent). All names are editable `write` params with these defaults.
- **Reserved primitive kinds:** `trade`, `session`, `regime_period`. A `PrimTable` is `{start: int[], end: int[], attrs: {name: column}}`, where `start`/`end` are bar positions in the index. Reading a primitive attribute in W1 to W7 gives the diagnostic `prims_no_producer`.
- **Column store.** One store per cook holds every column. Streams hold references. Adding an attribute copies nothing.

### 3.3 Serialized forms

Stream schema as returned by `/validate` (per node, W2):

```json
{
  "stream_schema": 1,
  "points": [
    {"name": "@close", "dtype": "float", "written_by": "n_t1"},
    {"name": "@xb_rsi", "dtype": "bool", "written_by": "n_xb"}
  ],
  "detail": [
    {"name": "@stop_pct", "dtype": "float", "written_by": "n_sl"}
  ],
  "prims": []
}
```

```ts
// frontend/src/api/nodebuilder.ts
export const STREAM_SCHEMA_VERSION = 1
export type PointDtype = 'float' | 'bool'
export type DetailDtype = 'float' | 'int' | 'bool' | 'str'
export interface AttrInfo { name: string; dtype: PointDtype | DetailDtype; written_by: string | null }
export interface StreamSchema {
  stream_schema: number
  points: AttrInfo[]
  detail: AttrInfo[]
  prims: { kind: string; attrs: AttrInfo[] }[]   // always [] until W8
}
```

---

## 4. Shared contracts

These shapes are fixed. Backend and frontend build against them in parallel.

### 4.1 Graph JSON (v2 in W1, v3 in W2)

```json
{
  "_version": 3,
  "stream_schema": 1,
  "readOnly": false,
  "meta": {"notes": ""},
  "nodes": {
    "n_t1": {"id": "n_t1", "type": "ticker", "name": "aapl", "parent": null,
             "params": {"symbol": "AAPL", "interval": "1d"},
             "position": [120, 80], "display": false, "bypass": false},
    "n_rsi": {"id": "n_rsi", "type": "rsi", "name": "rsi", "parent": null,
              "params": {"period": 14, "type": "wilder", "source": "@close", "out": "@rsi"},
              "position": [120, 260], "display": true, "bypass": false}
  },
  "wires": [
    {"id": "w1", "from": "n_t1", "to": "n_rsi", "from_port": "out", "to_port": "in0"}
  ],
  "annotations": {
    "boxes": [{"id": "b1", "label": "REGIME", "color": "blue", "rect": [900, 40, 420, 760], "members": ["n_spy", "n_sma"], "parent": null}],
    "notes": [{"id": "s1", "text": "pair trade adds /short_leg", "rect": [20, 20, 320, 70], "color": "amber", "parent": null}]
  }
}
```

- `_version: 2` graphs may still have `wire.attr`. The v3 migration turns it into consumer params and drops it.
- `meta` is a free map (at most 32 keys, string/number/bool values).
- W6 adds optional node fields `promoted`, `asset_ref`, `locked`. W7 adds `code`, `spare_params`, and param values of the form `{"expr": "..."}`. Old readers ignore missing optional fields.

```ts
// frontend/src/api/nodebuilder.ts (owned by 1.E in W1; later waves extend it as noted)
export type ParamValue = number | string | boolean | null | { expr: string }   // expr from W7
export interface GraphNode {
  id: string
  type: string
  name: string
  parent: string | null
  params: Record<string, ParamValue>
  position: [number, number]
  display: boolean
  bypass: boolean
  promoted?: PromotedParam[]                  // W6
  asset_ref?: { name: string; version: number } | null   // W6
  locked?: boolean                            // W6
  code?: string | null                        // W7
  spare_params?: SpareParamSpec[]             // W7
}
export interface GraphWire {
  id: string
  from: string
  to: string
  from_port: 'out'
  to_port: string            // 'in0', 'in1', ...
  attr?: string | null       // v1/v2 only; removed by the v3 migration
}
export interface NetworkBox { id: string; label: string; color: string; rect: [number, number, number, number]; members: string[]; parent: string | null }
export interface StickyNote { id: string; text: string; rect: [number, number, number, number]; color: string; parent: string | null }
export interface Graph {
  _version: 2 | 3
  stream_schema: number
  readOnly: boolean
  meta: Record<string, string | number | boolean>
  nodes: Record<string, GraphNode>
  wires: GraphWire[]
  annotations: { boxes: NetworkBox[]; notes: StickyNote[] }
}
```

### 4.2 Diagnostics

```json
{"node_id": "n_rsi", "path": "/rsi", "severity": "error", "code": "attr_missing",
 "message": "RSI reads @close, but no input provides it.",
 "param": "source", "port": null, "line": null, "col": null, "end_line": null, "end_col": null}
```

```ts
export type Severity = 'error' | 'warning' | 'info'
export interface Diagnostic {
  node_id: string | null
  path: string | null
  severity: Severity
  code: DiagnosticCode
  message: string
  param: string | null
  port: string | null
  line: number | null; col: number | null; end_line: number | null; end_col: number | null
}
```

Diagnostic codes (the backend enum in `backend/nodebuilder/diagnostics.py`, created in W1 item 1.C; each wave adds its codes there):

| Code | Severity | Wave |
|---|---|---|
| `missing_terminal`, `unsupported_node`, `unknown_node_type`, `dangling_wire`, `cycle`, `missing_input`, `param_invalid`, `param_out_of_range`, `family_cap`, `name_invalid`, `name_duplicate`, `regime_unsupported` (removed in W5) | error | W1 |
| `exit_unconnected`, `size_unit_suspect` (a size above 1.0 on a fraction field) | warning | W1 |
| `attr_missing`, `attr_type`, `attr_clash`, `prims_no_producer`, `port_unknown` | error | W2 |
| `attr_shadowed` | warning | W2 |
| `group_invalid`, `group_terminal_outside`, `group_duplicate_terminal`, `ticker_missing`, `wire_crosses_network`, `boundary_invalid` | error | W5 |
| `setting_shadowed` | warning | W5 |
| `ref_broken`, `asset_missing`, `asset_interface_mismatch`, `promoted_target_missing` | error | W6 |
| `code_syntax`, `code_limit`, `code_runtime`, `code_timeout`, `code_type`, `ch_dynamic`, `attr_dynamic`, `ch_cycle`, `code_disabled`, `ticker_param_not_codeable` | error | W7 |

**Line and column convention (decided 2026-09-30).** `line` is 1-based and `col` is 0-based, in characters of the user's original text, as Python's `ast` reports them. `end_line` and `end_col` follow the same rule. The frontend converts to Monaco's 1-based column in one function (`toMonacoRange`, W7).

### 4.3 Catalog entry (D9)

```json
{
  "name": "crosses_above", "cat": "comparisons", "desc": "a crosses above b (or threshold)",
  "compile_active": true,
  "inputs": {"ports": [{"label": "a"}, {"label": "b", "optional": true}], "dynamic": false, "min": 1, "max": 2},
  "params": [
    {"name": "a", "type": "attr", "label": "a", "dtype": "float", "default": null},
    {"name": "b", "type": "attr", "label": "b", "dtype": "float", "default": null, "optional": true},
    {"name": "threshold", "type": "number", "label": "threshold", "default": 30},
    {"name": "out", "type": "write", "label": "out", "dtype": "bool", "default": "@xa"}
  ],
  "reads": ["@a", "@b"], "writes": ["@xa"]
}
```

```ts
export type ParamType = 'number' | 'int' | 'string' | 'select' | 'bool'
  | 'attr' | 'attr_list' | 'write' | 'path' | 'time_range'
export interface ParamSpec {
  name: string; type: ParamType; label: string; default: unknown
  min?: number; max?: number; step?: number; unit?: '%' | 'bps' | 'bars' | 'frac' | null
  options?: string[]; dtype?: 'float' | 'bool' | 'any'; optional?: boolean
  code_able?: boolean   // false for ticker symbol/interval (D1)
}
export interface PortsSpec { ports: { label: string; optional?: boolean }[]; dynamic: boolean; min: number; max: number }
```

- A comparison with `b` empty compares `a` to `threshold`.
- `time_range` values are `"HH:MM-HH:MM"` in America/New_York wall-clock time.

### 4.4 Error response for graph routes

W0 introduces the 400 shape with `detail` and `node_id`. From W1, every 400 from a nodebuilder or graphs route also carries `code` and the full `diagnostics` list:

```json
{"detail": "Exit is not connected.", "node_id": "n_exit", "code": "missing_input", "diagnostics": [ /* Diagnostic[] */ ]}
```

---

## 5. Wave 0: Unbreak (in progress)

**Status: in progress in another workflow. Do not redesign it here.** This section records the amended item list, so later waves know what exists. That workflow's own brief is authoritative for details.

**Goal.** Nothing crashes. Nothing silently differs from its backtest. The editor is usable as it is.

| Item | What (amended) |
|---|---|
| 0.A | Error hygiene: every `GraphValidationError` and compile `TypeError` becomes a 400 with `{detail, node_id}`. The stale `/validate` docstring goes. The graph summary gains `open_position: {direction, entry_price, unrealized_pct} \| null` and `exit_connected: bool`. `run_graph_backtest` moves to `backend/nodebuilder/run.py`; the route becomes a thin wrapper. (critic 16) |
| 0.B | Bot parity, **after 0.A**: graph settings apply to graph bots through `BotConfig.model_validate({**cfg.model_dump(), **overrides})`, never `model_copy(update=)`. The key mapping moves to one shared `backend/nodebuilder/sim_settings.py`, used by backtest and bot. For graph bots, the overlay also sets or clears the per-direction stop and size fields. `backtest_bot` runs the graph backtest for `kind == "graph"`. `add_bot` compiles and rejects bad graphs with 400. (critic 16, 28) |
| 0.C | Canvas: multi-select delete and drag; key listener on `.nodebuilder-root`; Tab menu at the cursor, outside-click close, autofocus search; RF node type `output` renamed `nbOutput`; perf fixes P1 to P3. |
| 0.D | Node visuals: no source handle on terminals, no target handle on Ticker; themed Controls (later removed in 3.H, amendment A2); ParamRow border fix; ParamRow Cmd+Z desync guard. |
| 0.E | Shell: show the server `detail`; results strip names symbol and interval, marks itself stale, shows "1 open position", warns "Exit not connected"; "Regime removed" banner; **unsupported-nodes banner** listing nodes compile cannot run (critic 1); confirm on New Empty Graph; spacing ×1.8 on auto-render; `React.memo(NodeBuilder)`; empty-state copy. |
| 0.F | AddBotBar never throws on the saved-graphs key (object shape, array shape or garbage). |
| 0.G | Catalog honesty: remove `polygon`, add `wilder` to RSI `type` in both catalogs, and the **Position Size unit display** fix (moved here from 0.D, critic 16). |
| 0.H | `UnsupportedNodeError(node_id)` (400) for any node type that is not compile-active, plus the sweep test `backend/tests/nodebuilder/test_rule_coverage.py`: for every `RuleIndicator` × `RuleCondition` × `negated`, auto-render then graph backtest either equals the rule backtest or raises `UnsupportedNodeError`. It never silently differs. (critic 1) |

**Tests (amended, critic 25).** HTTP tests in `test_routes_nodebuilder.py`; `test_bot_runner_graph.py` (stop from graph, non-empty `backtest_bot`, overlay precedence with `long_stop_loss_pct` set, size=100 clamped or rejected); AddBotBar saved-graphs shapes; canvas multi-select; results-strip stale, open position and "Exit not connected"; `wilder` present and `polygon` absent in both catalogs; ParamRow Cmd+Z guard; Tab menu at cursor and outside-click close; the rule-coverage sweep.

**What later waves may assume after W0:** `backend/nodebuilder/run.py` exists and holds `run_graph_backtest`; `backend/nodebuilder/sim_settings.py` holds the settings key mapping; `UnsupportedNodeError` exists; the 400 shape has `detail` and `node_id`; the RF terminal node type is `nbOutput`.

**Live gate after W0:** see section 9, gate G1.

---

## 6. Waves 1 to 7

Each wave lists: goal, order, items with exact file ownership, contracts, tests, acceptance checks a script can run, and the gate. "Owns" means only this item edits that file in this wave. "New" files are created by the item and owned by it.

### Standing rules for every implementer

- Do **not** commit or push. The orchestrator owns commits.
- Keep `updateNodeParams` as a named store action (browser verification finds it by name). Implement it through `commit`.
- Numeric inputs stay `type="text" inputMode="decimal"` (F278). Never switch to `type="number"`.
- Never use `yf.download`. Never `await` a side effect in the bot polling loop. Never evaluate a graph on the event loop.
- Never regenerate a parity snapshot unless the wave says so.
- Tests never start a bot and never place an order. Brokers are mocked. Spawn creates stopped bots.
- Use `npm run build` to check the frontend, not `tsc --noEmit`.

---

### Wave 1: Foundations (persistence, schema v2, undo, validate, catalog codegen)

**Goal.** Graphs are durable, named server objects. Every edit can be undone. Errors show before Run. The model has names, parents, ports and a stream schema version.

**Order.**
- Backend: {1.A, 1.B, 1.D} in parallel. Then 1.C (after 1.A, because both touch models and compile).
- Frontend: 1.E first (it owns `store.ts` and the TS types). Then {1.F, 1.G} in parallel.
- Backend and frontend groups run in parallel. 1.D lands before 1.G merges (1.G reads `catalog.generated.ts`).

| Item | What | Owns | New files |
|---|---|---|---|
| 1.A | Schema v2 (fields only): `Node.name`, `Node.parent` (replaces `subgraph`), `Wire.from_port`/`to_port`, `Graph.stream_schema`, `Graph.meta`, `Graph.annotations`. The `model_validator(mode="before")` migration hook. `migrate_v1_to_v2` assigns names and `to_port = in<k>` by each consumer's current wire order. Sibling-name uniqueness. `find_by_path`, `rename_node` (paths now; `ch()` rewrite is a no-op stub that W7 fills). `from_rules` emits `name` and `to_port`. | `backend/nodebuilder/models.py`, `backend/nodebuilder/from_rules.py`, `backend/tests/nodebuilder/test_models.py`, `backend/tests/nodebuilder/test_from_rules.py` | `backend/nodebuilder/migrate.py`, `backend/tests/nodebuilder/test_migrate.py`, `backend/tests/nodebuilder/vectors/paths.json` (shared Py/TS vectors), `backend/tests/nodebuilder/test_botsjson_migration.py` |
| 1.B | Graph storage and routes (D2), using `fileutil.atomic_write_text` and a per-file lock. Register the router. Add `graphs/` and `graph_library/` to `cleanup_orphan_tmps`. | `backend/main.py` (router include and cleanup list only) | `backend/nodebuilder/storage.py`, `backend/routes/graphs.py`, `backend/tests/test_graphs_routes.py`, `backend/tests/nodebuilder/test_storage.py` |
| 1.C | `POST /api/nodebuilder/validate`. Compile collects diagnostics instead of stopping at the first error. Graph routes return the 4.4 error shape. | `backend/routes/nodebuilder.py`, `backend/nodebuilder/compile.py` (diagnostic collection only), `backend/nodebuilder/evaluator.py` (error classes only) | `backend/nodebuilder/diagnostics.py`, `backend/tests/nodebuilder/test_validate.py` |
| 1.D | Catalog codegen (D9): `ParamSpec` and `PortsSpec` on every entry; export script; generated TS; `catalog.ts` keeps only UI extras. | `backend/nodebuilder/nodes.py`, `frontend/src/features/nodebuilder/catalog.ts`, `frontend/src/features/nodebuilder/__tests__/catalog.test.ts`, `backend/tests/nodebuilder/test_catalog_consistency.py` (extend) | `backend/scripts/export_nodebuilder_catalog.py`, `frontend/src/features/nodebuilder/catalog.generated.ts` |
| 1.E | Store core and TS types: `commit(label, recipe)` with `past`/`future` (cap 100), `beginBatch`/`endBatch`, `dirty`, `graphMeta {id, rev, name}`. Delete dead members (`graphHash`, `bypassedNodeIds`, `saveCurrentGraph`, `loadGraph`). Command registry in a `commands/` folder with Cmd+Z, Cmd+Shift+Z and Cmd+Y. Canvas dispatches keys through the registry. Update all graph types (section 4.1) in `api/nodebuilder.ts`. `updateNodeParams` stays a named action. | `frontend/src/features/nodebuilder/store.ts`, `operations.ts`, `Canvas.tsx` (key dispatch only), `frontend/src/api/nodebuilder.ts` | `frontend/src/features/nodebuilder/commands/index.ts`, `commands/history.ts`, `__tests__/store.history.test.ts` |
| 1.F `[SPEC S01-S04, S06]` | Persistence UI: graph toolbar (New, Open, Save, Save As, Rename, Duplicate, Delete, Export JSON, Import JSON), Graph Browser dialog, draft autosave and restore prompt, the 409 conflict dialog, the one-time seed call, and AddBotBar listing graphs from the API. Also every `NodeBuilder.tsx` edit for diagnostics: the toolbar error count and Run disabled on errors. | `frontend/src/features/nodebuilder/NodeBuilder.tsx`, `frontend/src/features/trading/AddBotBar.tsx` | `frontend/src/api/graphs.ts`, `frontend/src/features/nodebuilder/GraphToolbar.tsx`, `GraphBrowser.tsx`, `persistence.ts`, `__tests__/persistence.test.ts`, `__tests__/graphBrowser.test.tsx` |
| 1.G `[SPEC S05]` | Diagnostics: `useDiagnostics()` hook (debounced 300 ms `/validate` after each `commit`), error/warning badge on BaseNode, invalid-field state on ParamRow. Exports the count for 1.F. | `frontend/src/features/nodebuilder/nodes/BaseNode.tsx`, `nodes/ParamRow.tsx` | `frontend/src/features/nodebuilder/useDiagnostics.ts`, `frontend/src/api/nodebuilderValidate.ts`, `nodes/DiagnosticBadge.tsx`, `__tests__/diagnostics.test.tsx`, `__tests__/paramRow.inputType.test.tsx` |

**Contracts: graphs API (1.B).**

```text
GET    /api/graphs                    -> 200 {"graphs": [GraphListItem]}
GET    /api/graphs/{id}               -> 200 GraphEnvelope | 404
POST   /api/graphs                    body {"name", "description"?, "graph"?, "duplicate_of"?} -> 201 GraphEnvelope (rev 1)
PUT    /api/graphs/{id}               body {"rev", "name"?, "description"?, "graph"} -> 200 GraphEnvelope (rev+1) | 409
DELETE /api/graphs/{id}?rev=N         -> 204 | 409
POST   /api/graphs/seed               body {"legacy": <any JSON>} -> 200 {"imported": [id], "skipped": [{"name", "reason"}]}
```

```json
{"id": "g_3f9a1c7e2b40", "rev": 4, "name": "regime_filtered_rsi", "description": "",
 "created_at": "2026-09-29T10:00:00Z", "updated_at": "2026-09-29T11:12:00Z", "graph": { "_version": 2 }}
```

```ts
// frontend/src/api/graphs.ts
export interface GraphEnvelope { id: string; rev: number; name: string; description: string; created_at: string; updated_at: string; graph: Graph }
export interface GraphListItem { id: string; rev: number; name: string; description: string; updated_at: string; node_count: number; groups: string[] }
export interface RevConflict { code: 'rev_conflict'; current_rev: number }
export interface NameTaken { code: 'name_taken' }
export function listGraphs(): Promise<GraphListItem[]>
export function getGraph(id: string): Promise<GraphEnvelope>
export function createGraph(body: { name: string; description?: string; graph?: Graph; duplicate_of?: string }): Promise<GraphEnvelope>
export function saveGraph(id: string, body: { rev: number; name?: string; description?: string; graph: Graph }): Promise<GraphEnvelope>  // throws RevConflict
export function deleteGraph(id: string, rev: number): Promise<void>
export function seedLegacyGraphs(legacy: unknown): Promise<{ imported: string[]; skipped: { name: string; reason: string }[] }>
```

- Ids: `g_` plus 12 lowercase hex characters.
- Names: 1 to 80 characters, unique case-insensitively. A clash returns 409 `{"detail": {"code": "name_taken"}}`. Seeds that clash get the suffix " (imported)".
- 409 on rev: `{"detail": {"code": "rev_conflict", "current_rev": 5}}`.
- Body limit 2 MB. The graph must parse (after migration). Otherwise 400 with diagnostics.
- Seed is idempotent: an entry whose name and content hash match an existing graph is skipped with reason `"duplicate"`.
- `GraphListItem.groups` is `["main"]` until W5.

**Contracts: validate (1.C).**

```text
POST /api/nodebuilder/validate   body {"graph": Graph}
  -> 200 {"ok": bool, "diagnostics": [Diagnostic], "streams": {}}     // "streams" is filled from W2
```

`ok` is false when any diagnostic has severity `error`. Validate never fetches market data.

**Tests to add.**
- Storage: atomic write; 409 on a stale `rev`; two concurrent PUTs serialize (the second gets 409); orphan `.tmp` files in `graphs/` are cleaned at start.
- Seed: object shape, array shape, garbage; running the seed twice imports nothing new.
- Migration: v1 auto-render graphs survive; every parity-fixture graph compiles to identical signals after migration; a bots.json graph snapshot migrates when loaded through `BotManager` (not only `Graph.load`).
- `find_by_path` and `rename_node` run the shared vectors in `vectors/paths.json` from both pytest and vitest.
- Store: undo and redo across add, delete, move and param edits; batch coalescing (a drag is one step); `updateNodeParams` exists by name.
- Persistence UI: draft restore flow; the 409 path shows the conflict dialog and does not overwrite.
- Diagnostics: a badge appears on an unconnected Entry; Run is disabled with errors.
- ParamRow numeric inputs render `type="text"` (critic 35).
- Catalog: the extended consistency test regenerates and diffs.

**Acceptance (scripted).**
- `backend/venv/bin/python -m pytest backend/tests/test_graphs_routes.py backend/tests/nodebuilder/test_storage.py backend/tests/nodebuilder/test_migrate.py backend/tests/nodebuilder/test_botsjson_migration.py backend/tests/nodebuilder/test_validate.py backend/tests/nodebuilder/test_catalog_consistency.py -q` passes.
- `npm --prefix frontend test -- store.history persistence graphBrowser diagnostics paramRow.inputType catalog` passes.
- A route test does: create, PUT twice, GET; the graph comes back byte-identical after JSON normalization.
- A route test posts a graph with an unconnected Entry to `/validate` and gets `ok: false` with `code: "missing_input"` and the Entry's `node_id`.
- `grep -rn "saveCurrentGraph\|loadGraph\|graphHash" frontend/src/features/nodebuilder` finds nothing.
- The parity trio is unchanged.

---

### Wave 2: Columnar kernel and universal stream

**Goal.** Implement D4, D5 and the section 3 stream. Named ports, attribute pickers and signals-of-signals work. The graph language covers every rule-builder condition and indicator. The engine is fast and ready for code.

**Order.**
1. **2.0** (serial, first): record the baseline on today's engine.
2. **2.A** (serial): the kernel, including attribute semantics.
3. {**2.B**, **2.C**, **2.D**} in parallel.
4. The orchestrator regenerates `catalog.generated.ts` once.
5. **2.E** (frontend) builds against the contracts from the start, and merges after step 4.

| Item | What | Owns | New files |
|---|---|---|---|
| 2.0 | Benchmark harness and baseline (critic 8). A synthetic 500,000-row, 30-node graph and a 5-year 1h MSFT-shaped frame. Record cook time and peak RSS on today's per-bar engine. Run on the worker (`bin/worker-probe.sh` first) and record the host. | none | `backend/scripts/bench_graph_cook.py`, `backend/tests/nodebuilder/fixtures/bench_graph_30.json`, output `.run/F435/bench/baseline.json` |
| 2.A | Kernel: `Stream` (points, detail, reserved prims, `written_by`), registry, column evaluator, `required_lookback_bars`. `compile.py`/`evaluator.py` become facades. Delete per-bar closures; fold `NODE_IMPLS`. Bypass is pass-through. **Attribute semantics live here** (critic 18): `attr`, `attr_list` and `write` params; port merge and clash rules; presence and type checks; default reads from the upstream primary write; indicators read their wired `source` (so EMA of RSI works); settings nodes write detail. Existing node types are ported into self-registering modules. | `backend/nodebuilder/compile.py`, `evaluator.py`, `nodes.py`, `models.py` (Wire semantics only), `backend/tests/nodebuilder/test_compile.py`, `test_evaluator.py`, `test_nodes.py` | `backend/nodebuilder/kernel/{__init__,stream,registry,evaluate,schema}.py`, `backend/nodebuilder/trading/{__init__,nodes_indicators,nodes_compare,nodes_logic,nodes_settings,nodes_terminals,nodes_data}.py`, `backend/tests/nodebuilder/test_kernel_stream.py`, `test_legacy_equivalence.py` (temporary, deleted at wave end), `test_handbuilt_graphs.py` |
| 2.B | Migration v2 to v3 (`wire.attr` becomes consumer params in wire order; the Ticker `source` param is dropped). `from_rules` emits v3 graphs, including every condition and indicator 2.C adds. The `/validate` payload gains `streams` (per-node `StreamSchema`). | `backend/nodebuilder/migrate.py`, `backend/nodebuilder/from_rules.py`, `backend/routes/nodebuilder.py` (validate handler only) | `backend/tests/nodebuilder/test_stream_semantics.py`, `test_migrate_v3.py` |
| 2.C | New node types, **new files only** (they self-register): rule-builder coverage parity (critic 1) plus math and data nodes. Full list below. | none shared | `backend/nodebuilder/trading/nodes_math.py`, `nodes_slope.py`, `nodes_indicators_more.py`, `nodes_time.py`, `backend/tests/nodebuilder/test_nodes_math.py`, `test_nodes_slope.py`, `test_nodes_time.py` |
| 2.D | Bot runner on the column program. The whole cook runs inside `self._run_in_executor` (critic 30). Fetch window from `required_lookback_bars` (critic 9). Shared data prep. | `backend/bot_runner.py`, `backend/nodebuilder/run.py` (data-prep block only) | `backend/nodebuilder/prepare.py`, `backend/tests/nodebuilder/test_live_window_parity.py`, `backend/tests/test_bot_runner_graph_executor.py` |
| 2.E `[SPEC S08-S12]` | Ports and stream UI: named input handles from `PortsSpec` (dynamic `in*` for logic and merge), one output handle; `AttrPicker` for `attr`/`attr_list` params (a dropdown of the node's input attributes from `/validate` `streams`, free text allowed); `+@name` write chips with inline rename; derived wire labels with fan-out de-confliction; hover shows the full stream; the time-of-day range widget (critic 13). `onConnect` uses handle ids; `isValidConnection` blocks self-loops, cycles and a second wire into a non-dynamic port. | `frontend/src/features/nodebuilder/nodes/BaseNode.tsx`, `nodes/ParamRow.tsx`, `edges/AttrEdge.tsx`, `Canvas.tsx` (connect logic only), `frontend/src/api/nodebuilder.ts` | `nodes/AttrPicker.tsx`, `nodes/WriteChip.tsx`, `nodes/TimeRangeInput.tsx`, `streamLabels.ts`, `__tests__/ports.test.ts`, `__tests__/streamLabels.test.ts`, `__tests__/attrPicker.test.tsx` |

**2.C node list (type names are fixed here, so 2.B can map them in parallel).**

| Node type | Covers rule condition / indicator | Params |
|---|---|---|
| `rising`, `falling` | `rising`, `falling` | `a`, `out` |
| `rising_over`, `falling_over` | `rising_over`, `falling_over` | `a`, `bars`, `out` |
| `turns_up`, `turns_down` | `turns_up`, `turns_down` | `a`, `out` |
| `turns_up_below`, `turns_down_above` | same names | `a`, `threshold`, `out` |
| `accelerating`, `decelerating` | same names | `a`, `out` |
| `crosses_above`/`crosses_below` with `b` | `crossover_up`/`crossover_down` (legacy aliases), `is_above_signal`/`is_below_signal` via `above`/`below` with `b = @macd_signal` | existing |
| `stochastic` | `stochastic` | as `indicators.py` (`out_k`, `out_d`) |
| `adx` | `adx` | as `indicators.py` |
| `atr_pct` | `atr_pct` | as `indicators.py` |
| `volume` | `volume` | as `indicators.py` |
| `ma` (with `type` select: every type `indicators.compute_ma` accepts) | `ma` variants | `period`, `type`, `source`, `out` |
| `price` | `price` | `field`, `out` |
| `xor` | logic | `terms` |
| `constant` | math | `value`, `out` (point or detail by param `as_detail`) |
| `math` | math | `op` (add, sub, mul, div, min, max, abs, neg), `a`, `b`, `out` |
| `shift` | math | `a`, `bars`, `out` |
| `rolling` | math | `a`, `op` (mean, min, max, std, sum), `window`, `out` |
| `merge` | stream | dynamic `in*`; no params |
| `time_of_day` | data | `range` (`time_range`), `out` (bool) |
| `day_of_week` | data | `days` (multi-select), `out` (bool) |
| `session_bar` | data | `out` (int bar number since the session open, float column) |

Semantics must match `signal_engine.py` exactly for every rule condition, including `negated` handling and the bar-0 guard. Each indicator node calls `indicators.compute_instance`, so values match the chart and the rule engine.

**Contracts.**
- `/validate` gains `"streams": {"<node_id>": StreamSchema}` (section 3.3), keyed by node id, describing each node's **output** stream. The input stream of a node is the union of its upstream outputs; the frontend derives it from `streams` plus wires.
- `CompiledProgram` gains `required_lookback_bars: int` and `stream_schema: int`.
- The backtest request and response do not change in W2.

**Tests to add.**
- Parity trio green with no fixture regeneration.
- `test_legacy_equivalence.py`: the column engine equals the old engine on every `from_rules` fixture (deleted at wave end, after it passes).
- `test_handbuilt_graphs.py` (critic 26): OR, NOT, nested logic, fan-out from one indicator into two comparisons, bypass pass-through, crossover of a derived bool, EMA of RSI, and "MACD crosses its signal".
- The critic-1 sweep `test_rule_coverage.py` now expects **equality for every** indicator × condition × negated combination (no `UnsupportedNodeError` left for rule-builder inputs).
- Name clash: error when read, warning when not read.
- Operand order comes from `a`/`b` params: deleting and re-adding wires never swaps operands.
- `test_live_window_parity.py` (critic 9): for every fixture, a cook over the live fetch window gives the same last value as a cook over the full history.
- `test_bot_runner_graph_executor.py` (critic 30): two bots, one with a deliberately slow cook; the other bot's tick completes on time.
- Benchmark: the 500,000-row, 30-node graph cooks in under 3 s, and peak RSS stays under 3× the input frame. Record the host and number in `.run/F435/bench/w2.json`.

**Acceptance (scripted).**
- `backend/venv/bin/python -m pytest backend/tests/nodebuilder -q` passes, including the three new suites above and the sweep with no expected-failure cases.
- `backend/venv/bin/python backend/scripts/bench_graph_cook.py --check` exits 0 (under 3 s, RSS under 3×).
- `npm --prefix frontend test -- ports streamLabels attrPicker paramRow.inputType` passes.
- `grep -n "PerBarOp\|NODE_IMPLS" -r backend/nodebuilder` finds nothing.
- `grep -n "evaluate_graph(" backend/bot_runner.py` shows the call only inside a function passed to `_run_in_executor`.

---

### Wave 3: Houdini editor ergonomics and the Inspector

**Goal.** It feels like Houdini's network editor. The Inspector (F272) ships.

**Order.**
1. **3.0** (serial, one agent, about half a day): the plugin pre-step.
2. {3.A to 3.H} all in parallel. After 3.0, their file sets are disjoint.

This wave may overlap W2's backend items, but never 2.E.

**3.0 pre-step (critic 19).** One agent makes these changes:
- `nodeTypes.ts` and `edgeTypes.ts` hold the React Flow maps. Items add types with `registerNodeType(name, Component)` and `registerEdgeType(name, Component)` from their own module.
- `rfMapping.ts` holds the graph-to-React-Flow mapping, pulled out of `Canvas.tsx`. W5 extends it for network frames.
- `canvasPlugins.ts` defines the plugin interface below. `Canvas.tsx` calls every registered plugin from its React Flow handlers.
- `commands/index.ts` auto-registers every `commands/*.ts` via `import.meta.glob('./*.ts', { eager: true })`.
- The store is split into slices composed in `store.ts`: `store/graph.ts` (graph plus `commit`), `store/selection.ts`, `store/view.ts` (per-network viewport), `store/annotations.ts` (empty), `store/clipboard.ts` (empty).
- `CanvasChrome.tsx` holds the React Flow `MiniMap` and `Background`. React Flow `Controls` are removed (foundation amendment A2 in `surfaces-w1-w4.md`, accepted by the orchestrator 2026-09-30). Zoom is the mouse wheel, the F and H keys, and the toolbar "Reset view" button.
- `NodeBuilder.tsx` renders named layout slots filled through `slots.ts`: `toolbarLeft`, `toolbarRight`, `rightPanel`, `bottomPanel`, `statusBar`, `overlays`, `dialogs`.

```ts
// frontend/src/features/nodebuilder/canvasPlugins.ts
import type { Node as RFNode, Edge as RFEdge, Connection, Viewport, ReactFlowInstance } from '@xyflow/react'
export interface CanvasCtx {
  rf: ReactFlowInstance
  store: NodeBuilderStoreApi
  pointer(): { x: number; y: number }            // last pointer position in flow coordinates
}
export interface CanvasPlugin {
  id: string
  onNodeDragStart?(e: React.MouseEvent, node: RFNode, nodes: RFNode[], ctx: CanvasCtx): void
  onNodeDrag?(e: React.MouseEvent, node: RFNode, nodes: RFNode[], ctx: CanvasCtx): void
  onNodeDragStop?(e: React.MouseEvent, node: RFNode, nodes: RFNode[], ctx: CanvasCtx): boolean | void  // true = handled, skip default commit
  onReconnect?(oldEdge: RFEdge, conn: Connection, ctx: CanvasCtx): void
  onNodeContextMenu?(e: React.MouseEvent, node: RFNode, ctx: CanvasCtx): void
  onEdgeContextMenu?(e: React.MouseEvent, edge: RFEdge, ctx: CanvasCtx): void
  onPaneContextMenu?(e: React.MouseEvent | MouseEvent, ctx: CanvasCtx): void
  onPointerMove?(flowPos: { x: number; y: number }, ctx: CanvasCtx): void
  onMove?(viewport: Viewport, ctx: CanvasCtx): void
}
export function registerCanvasPlugin(p: CanvasPlugin): void

// frontend/src/features/nodebuilder/commands/index.ts
export interface Command {
  id: string                       // 'flags.toggleBypass'
  label: string                    // shown in menus and the shortcut overlay
  keys?: string[]                  // ['b'], ['mod+c'], ['shift+c']
  when?(s: NodeBuilderState): boolean
  run(ctx: CanvasCtx): void
}
export function registerCommand(c: Command): void
export function listCommands(): Command[]

// frontend/src/features/nodebuilder/slots.ts
export type SlotName = 'toolbarLeft' | 'toolbarRight' | 'rightPanel' | 'bottomPanel' | 'statusBar' | 'overlays' | 'dialogs'
export function registerSlot(name: SlotName, id: string, Component: React.ComponentType, order?: number): void
```

3.0 owns `Canvas.tsx`, `NodeBuilder.tsx` and `store.ts` for its duration. After it, the owners are as below.

| Item | What | Owns | New files |
|---|---|---|---|
| 3.A `[SPEC S14, S15]` | Inspector: resizable right panel. With a selection: name (rename via `rename_node`, shows the new path), type, description, all params, reads and writes, flags, diagnostics. With no selection: legend, flags and keys, as in the mockup. Built to host the code editors in W7. | none shared (registers in `rightPanel`) | `Inspector.tsx`, `InspectorLegend.tsx`, `operations/rename.ts`, `__tests__/inspector.test.tsx` |
| 3.B `[SPEC S16]` | Flags: clickable display (blue) and bypass (amber) dots on each node's right edge; D and B keys. One display node per network. | `nodes/BaseNode.tsx`, `operations.ts` (`setFlag`) | `commands/flags.ts`, `__tests__/flags.test.ts` |
| 3.C | Clipboard: Cmd+C, Cmd+V, Cmd+D, Alt-drag duplicate. Id remap, name uniquing, paste at the cursor, internal wires kept. | `store/clipboard.ts` | `commands/clipboard.ts`, `clipboard.ts`, `plugins/altDragDuplicate.ts`, `__tests__/clipboard.test.ts` |
| 3.D | View: F frames the selection, H and G frame all, Space+drag pan, gentler wheel zoom, per-network viewport memory (fixes the write-only viewport), snap-to-grid toggle. | `Canvas.tsx` (React Flow props), `store/view.ts` | `commands/view.ts`, `__tests__/view.test.ts` |
| 3.E `[SPEC S17, S18]` | Network boxes and sticky notes in `graph.annotations`. RF node types `nbBox` (with NodeResizer; moving a box moves its members) and `nbNote`. | `store/annotations.ts` | `nodes/NetworkBox.tsx`, `nodes/StickyNote.tsx`, `commands/annotations.ts`, `plugins/boxDrag.ts`, `__tests__/annotations.test.ts` |
| 3.F | Tidy layout: elkjs layered top-down layout for the selection or all nodes (L key). Replaces the W0 ×1.8 spacing in "Edit this graph". | `store.ts` (`loadFromAutoRender` only), `frontend/package.json` (add `elkjs`) | `layout.ts`, `commands/layout.ts`, `__tests__/layout.test.ts` |
| 3.G `[SPEC S19, S23]` | Wire ops and menus: reconnect a wire end, drop a node on a wire to splice it (port-aware), context menus for node, wire and pane (items come from `listCommands()`), and the mockup's long-wire middle fade (moved here from 3.H, because 3.G owns the edge file). | `edges/AttrEdge.tsx` | `ContextMenu.tsx`, `commands/wires.ts`, `plugins/wireOps.ts`, `plugins/contextMenus.ts`, `__tests__/wireOps.test.ts` |
| 3.H `[SPEC S20-S22]` | Status bar (zoom %, cursor coordinates, selection, cook state), `?` shortcut overlay (built from `listCommands()`), top hint bar and "Reset view" button, recolour the existing MiniMap in category colours, and **remove `<Controls>`** (amendment A2, accepted 2026-09-30; S22's "Controls fallback" spec is not used). | `CanvasChrome.tsx` | `StatusBar.tsx`, `ShortcutHelp.tsx`, `HintBar.tsx`, `plugins/pointerTracker.ts`, `__tests__/statusBar.test.tsx` |

`operations.ts` belongs to 3.B in this wave. Other items put pure graph operations in new files under `operations/`.

**Tests to add (critic 26).** Clipboard id remap and name uniquing; flags round-trip and backend bypass pass-through; layout is deterministic for the same input; moving a box moves its members; Inspector rename updates the path and the node title; frame and per-network viewport memory; reconnect and splice are each one undo step; the status bar shows zoom and selection; every new op is undoable.

**Acceptance (scripted).**
- `npm --prefix frontend test -- inspector flags clipboard view annotations layout wireOps statusBar` passes.
- A test walks `listCommands()` and asserts these keys are bound: Tab, Space (pan), F, H, G, L, B, D, `?`, mod+C, mod+V, mod+D, mod+Z, mod+shift+Z, mod+Y, Delete, Backspace. Marquee, Shift+click and wheel zoom are asserted as React Flow props in `view.test.ts`.
- Paste then undo leaves the graph identical to before (deep equal).
- `grep -rn "<Controls" frontend/src/features/nodebuilder` finds nothing (A2).
- `bin/verify-batch.sh F435-W3` passes (render probe).

---

### Wave 4: See the data (data sheet, sparklines, auto-cook, real results)

**Goal.** Debug any wire. The graph and the chart are one workspace.

**Order.** 4.A (backend) in parallel with {4.B, 4.C} (frontend, against the mocked contract). 4.D last on the frontend, because it mounts everything. 4.D owns every `NodeBuilder.tsx` and toolbar edit; 4.B and 4.C export components and hooks only (critic 20).

| Item | What | Owns | New files |
|---|---|---|---|
| 4.A | Cook cache with the frame fingerprint key, `/inspect`, `/preview` (D6). The backtest response gains `cook_id`. Column histograms and bool counts for the DataSheet header. | `backend/routes/nodebuilder.py`, `backend/nodebuilder/api_models.py`, `backend/nodebuilder/run.py` | `backend/nodebuilder/cook_cache.py`, `backend/tests/nodebuilder/test_inspect.py`, `test_preview.py`, `test_cook_cache.py`, `backend/tests/nodebuilder/test_backtest_timing.py` |
| 4.B `[SPEC S25]` | DataSheet drawer (Houdini Geometry Spreadsheet): follows the selected wire, else the selected node, else the display-flag node. Virtualized rows; one column per attribute with a "written by" chip; bool columns as coloured cells; a histogram in each numeric column header (critic 13); an "only rows where ..." filter; jump to trade entries; a detail-attribute strip. The time column uses the `toET()` rule for intraday. | none shared (exports `DataSheet`) | `frontend/src/features/nodebuilder/DataSheet.tsx`, `frontend/src/api/nodebuilderInspect.ts`, `__tests__/datasheet.test.tsx` |
| 4.C `[SPEC S26, S27]` | Sparklines on nodes (numeric line, bool strip) and the auto-cook hook (debounced 500 ms `/preview` after a commit; cancels in-flight requests). Exports `AutoCookToggle`. | `nodes/BaseNode.tsx` | `nodes/Sparkline.tsx`, `useAutoCook.ts`, `AutoCookToggle.tsx`, `__tests__/sparkline.test.tsx`, `__tests__/useAutoCook.test.ts` |
| 4.D `[SPEC S28-S30]` | Results integration (D10, D11): `graphResult` state; Results and Chart take the displayed result as a prop; graph/chart split view reusing the one Chart component; the sidebar date range, capital and source drive the window; graph-owned fields are greyed with "Set by graph" and are not sent; rule-only panels show "Not available for graph results"; mount DataSheet, AutoCookToggle and the run handler. Display-flag chart sub-pane is a stretch goal. | `frontend/src/App.tsx`, `NodeBuilder.tsx`, `frontend/src/features/strategy/StrategyBuilder.tsx` (greying only), `frontend/src/features/strategy/Results.tsx` (result prop only), `frontend/src/features/strategy/OptimizerPanel.tsx`, `WalkForwardPanel.tsx`, `SensitivityPanel.tsx` (the "not available" state only) | `frontend/src/features/nodebuilder/graphRun.ts`, `ownership.ts`, `GraphChartSplit.tsx`, `__tests__/graphRun.test.ts`, `__tests__/ownership.test.tsx` |

**Contracts.**

```text
POST /api/nodebuilder/inspect
body {
  "cook_id": "ck_8f2a..." | null,
  "graph": Graph | null, "window": {"ticker","start","end","interval","source"} | null,   // used on a cache miss
  "target": {"node_id": "n_rsi"} | {"wire_id": "w3"},
  "attrs": ["@rsi", "@xb_rsi"] | null,
  "offset": 0, "limit": 500,                         // limit <= 2000
  "around_time": "2024-03-04" | 1709562600 | null,   // centres the page on this bar
  "filter": {"attr": "@xb_rsi", "op": "is_true" | "is_false" | "gt" | "lt" | "not_nan", "value": null} | null
}
-> 200 {
  "cook_id": "ck_8f2a...", "cache": "hit" | "miss", "stream_schema": 1,
  "columns": [{"name": "@time", "dtype": "time", "written_by": null},
              {"name": "@rsi", "dtype": "float", "written_by": "n_rsi"}],
  "detail": [{"name": "@stop_pct", "dtype": "float", "value": 2.5, "written_by": "n_sl"}],
  "prims": [],
  "read_by_consumer": ["@rsi"],                       // wire targets only: what the consumer reads
  "time": ["2024-03-04", ...],                        // "YYYY-MM-DD" daily, unix seconds UTC intraday
  "rows": [[41.2, false], ...],                       // one row per bar, columns in order (time excluded)
  "total": 1256, "offset": 0,
  "stats": {"@rsi": {"min": 12.1, "max": 88.4, "nan_count": 14, "hist": {"edges": [/*21*/], "counts": [/*20*/]}},
            "@xb_rsi": {"true_count": 31}}
}
```

- A wire target returns the source node's whole output stream. A bypassed node returns its input stream.
- An unknown `cook_id` with no `graph`/`window` returns 410 `{"detail": {"code": "cook_expired"}}`.

```text
POST /api/nodebuilder/preview
body {"cook_id": ... | null, "graph": Graph | null, "window": {...} | null, "node_ids": ["n_rsi"] | null, "points": 96}
-> 200 {"cook_id": "ck_...", "nodes": {
  "n_rsi": {"attr": "@rsi", "kind": "line", "min": 12.1, "max": 88.4, "nan_count": 14, "values": [/* 96 numbers or null */]},
  "n_xb":  {"attr": "@xb_rsi", "kind": "bool", "true_pct": 2.5, "values": [/* 96 shares of true, 0..1 */]}
}}
```

- Line values are min/max-decimated to `points` buckets. The shown attribute is the node's primary write.
- The backtest response gains `"cook_id": "ck_..."`.

```ts
// frontend/src/api/nodebuilderInspect.ts
export type InspectTarget = { node_id: string } | { wire_id: string }
export interface InspectRequest {
  cook_id: string | null; graph: Graph | null
  window: { ticker: string; start: string; end: string; interval: string; source: string } | null
  target: InspectTarget; attrs: string[] | null; offset: number; limit: number
  around_time: string | number | null
  filter: { attr: string; op: 'is_true' | 'is_false' | 'gt' | 'lt' | 'not_nan'; value: number | null } | null
}
export interface InspectColumn { name: string; dtype: 'time' | 'float' | 'bool'; written_by: string | null }
export interface InspectResponse {
  cook_id: string; cache: 'hit' | 'miss'; stream_schema: number
  columns: InspectColumn[]
  detail: { name: string; dtype: string; value: number | string | boolean; written_by: string | null }[]
  prims: never[]
  read_by_consumer?: string[]
  time: (string | number)[]; rows: (number | boolean | null)[][]; total: number; offset: number
  stats: Record<string, { min?: number; max?: number; nan_count?: number; hist?: { edges: number[]; counts: number[] }; true_count?: number }>
}
export interface PreviewNode { attr: string; kind: 'line' | 'bool'; min?: number; max?: number; nan_count?: number; true_pct?: number; values: (number | null)[] }
export interface PreviewResponse { cook_id: string; nodes: Record<string, PreviewNode> }
export function inspect(req: InspectRequest): Promise<InspectResponse>
export function preview(req: { cook_id: string | null; graph: Graph | null; window: InspectRequest['window']; node_ids: string[] | null; points: number }): Promise<PreviewResponse>

// frontend/src/features/nodebuilder/graphRun.ts
export interface GraphResultState {
  origin: 'graph'
  graphId: string | null; rev: number | null
  request: GraphBacktestRequest
  response: GraphBacktestResult
  displayedGroup: string            // 'main' until W5
}
export function buildGraphRequest(graph: Graph, sidebar: { start: string; end: string; initial_capital: number; source: string; ticker: string; interval: string }): GraphBacktestRequest   // omits GRAPH_OWNED_FIELDS

// frontend/src/features/nodebuilder/ownership.ts
export const GRAPH_OWNED_FIELDS: readonly string[]   // W4: position_size, stop_loss_pct, slippage_bps, commission_pct; W5 adds direction, trailing_stop, max_bars_held, borrow_rate_annual
```

**Chart constraints (critic 31).** These are part of the S28 spec and are checked in review:
- Move the existing Chart into the split. Do not create a second Chart implementation.
- Use `autoSize` only. No ResizeObserver plus `applyOptions`.
- Keep the teardown guards in Chart.tsx exactly as they are.
- A display-flag sub-pane uses whitespace warmup, an explicit `priceScaleId` and `toET()`.

**Tests to add.**
- Cache hit after a backtest does zero recompute (spy on the evaluator).
- The cache key changes when the fetched frame gains a bar (frame fingerprint).
- Inspect of a bypassed node returns its input stream. Paging. Filter. Memory cap eviction. 410 on an expired cook.
- `/preview` returns 96 points, and `true_pct` for bools.
- Backtest timing stays within 5 % of the W2 benchmark (`test_backtest_timing.py`).
- DataSheet renders intraday unix times as ET wall-clock times (same rule as `toET()`).
- Graph trades render as chart markers (component test with a mocked chart API).
- `graphRun.buildGraphRequest` never includes a field from `GRAPH_OWNED_FIELDS`.
- A graph run never changes `lastRequest` (test App state through the run handler).
- Changing a greyed field does not change the request that `buildGraphRequest` builds.

**Acceptance (scripted).**
- `backend/venv/bin/python -m pytest backend/tests/nodebuilder/test_inspect.py backend/tests/nodebuilder/test_preview.py backend/tests/nodebuilder/test_cook_cache.py backend/tests/nodebuilder/test_backtest_timing.py -q` passes.
- `npm --prefix frontend test -- datasheet sparkline useAutoCook graphRun ownership` passes.
- `grep -n "cook_cache" backend/bot_runner.py` finds nothing.
- The render probe toggles graph/chart view 20 times and resizes the split, with no console error and no requestAnimationFrame loop (`bin/verify-batch.sh F435-W4`).

---

### Wave 5: Lifecycle, networks, Output Groups, reference tickers, regime, bot spawn

**Goal.** A pair-style strategy is one canvas, with per-group and combined results, and spawns two stopped bots. Regime strategies edit and run with the regime intact.

**Order (critic 21, 22).**
1. {**5.0**, **5.A**} in parallel (disjoint).
2. **5.B** (after 5.0 and 5.A).
3. **5.C** (after 5.B).
4. **5.D** (after 5.B; may run in parallel with 5.C, file sets are disjoint).
5. {**5.E**, **5.F**} frontend in parallel from the start, against the contracts below.

| Item | What | Owns | New files |
|---|---|---|---|
| 5.0 | Network core (moved from W6, critic 22): evaluate `Node.parent`; node types `subnet`, `subnet_input`, `subnet_output`; sibling-only wires (`wire_crosses_network`); `flatten()` with `flat_to_source`; composite `outer::inner` ids reserved for asset instances. | `backend/nodebuilder/kernel/evaluate.py` (call `flatten`), `backend/nodebuilder/models.py` (network validation) | `backend/nodebuilder/kernel/flatten.py`, `backend/nodebuilder/trading/nodes_network.py`, `backend/tests/nodebuilder/test_flatten.py` (a nested graph compiles identically to the same graph hand-flattened) |
| 5.A | Terminals and the simulator bridge: `size`, `stop`, `trailing_stop` (all `TrailingStopConfig` fields, including `activate_on_profit`/`activate_pct`), `time_stop` (evaluated inside the simulator; maps to `max_bars_held`), `regime` (with `on_flip`), and the `borrow_rate` settings node (critic 5). Extend `_run_simulation` with optional `size_series`, `stop_series` and `regime_active_series`, sampled at the entry bar, default `None`. **All** `routes/backtest.py` changes in W5 live here. | `backend/routes/backtest.py` (`_run_simulation` signature, `_dir_size`/`_dir_stop`, regime series input), `backend/nodebuilder/trading/nodes_terminals.py`, `backend/nodebuilder/trading/nodes_settings.py` | `backend/nodebuilder/trading/sim_bridge.py`, `backend/tests/nodebuilder/test_terminals.py`, `test_terminal_rule_parity.py` |
| 5.B | Output Groups (D7): `output_group` network node; implicit `main`; `CompiledProgram.groups`; one simulation per group; combined metrics including `exposure_pct` and `gross_deployed_pct` (critic 7); scoped cost constants; the new response shape with legacy fields kept. Remove the derived `simulator_settings` list. | `backend/nodebuilder/kernel/registry.py` (terminal collection), `backend/nodebuilder/api_models.py`, `backend/nodebuilder/run.py`, `backend/routes/nodebuilder.py` | `backend/nodebuilder/trading/nodes_groups.py`, `backend/tests/nodebuilder/test_output_groups.py` |
| 5.C | Reference tickers, HTF and regime (D8): multi-fetch and alignment; `prefix` param; `from_rules` renders regime as a `regime` network; delete `RegimeUnsupportedError`, the compile check and the F274 strip; un-skip the 2 regime parity tests. | `backend/nodebuilder/prepare.py`, `backend/nodebuilder/from_rules.py`, `backend/nodebuilder/evaluator.py` (error class removal), `backend/nodebuilder/kernel/registry.py` (regime check removal, after 5.B), `backend/tests/nodebuilder/test_backtest_parity.py` (un-skip only), `frontend/src/features/nodebuilder/store.ts` (remove the strip only) | `backend/nodebuilder/trading/align.py`, `backend/tests/nodebuilder/test_reference_tickers.py`, `test_regime_readonly_render.py` |
| 5.D | Bot spawn and live groups: `POST /api/graphs/{id}/spawn`; `BotManager.add_bots`; new `BotConfig` fields; `is_bidirectional` replaces every `regime.enabled` check; the runner evaluates its own group (in the executor); reference fetch per tick with `fetch_ohlcv_async` + `asyncio.gather`; library bake-in hook (a no-op until W6); `POST /api/bots/{id}/graph_update`; the `UpdateBotRequest` field-coverage test; the bot summary on `GET /api/bots` gains `graph_name` and `graph_latest_rev` (orchestrator decision 2026-09-30, read from an in-memory head index in storage, never one graph file read per bot). | `backend/bot_manager.py`, `backend/bot_runner.py`, `backend/journal.py` (callers of the regime check only), `backend/routes/graphs.py`, `backend/routes/bots.py`, `backend/nodebuilder/storage.py` (`graph_head()` read helper and its index only) | `backend/tests/test_graph_spawn.py`, `backend/tests/nodebuilder/test_group_live_parity.py`, `backend/tests/test_bot_bidirectional_graph.py`, `backend/tests/test_update_bot_request_coverage.py`, `backend/tests/test_bot_summary_graph_rev.py` |
| 5.E `[SPEC S31-S33]` | One network visual model (critic 22): every network renders **expanded inside a netbox-style frame** (as the mockup's "REGIME" box), with its ports on the frame edge. Output groups use the same frame with a header (name, direction, primary ticker, colour). Per-group result tabs plus "Combined" in Results. `rfMapping.ts` maps `parent` to React Flow `parentId`. Adds `direction`, `trailing_stop`, `max_bars_held`, `borrow_rate_annual` to `GRAPH_OWNED_FIELDS`. | `nodes/OutputNode.tsx`, `rfMapping.ts`, `ownership.ts`, `frontend/src/features/strategy/Results.tsx` (tabs only), `frontend/src/shared/types/strategy.ts` | `nodes/NetworkFrame.tsx`, `nodes/OutputGroupHeader.tsx`, `__tests__/networkFrame.test.tsx`, `__tests__/groupResults.test.tsx` |
| 5.F `[SPEC S34-S36]` | Spawn dialog from the NodeBuilder toolbar: one row per group, capital per leg, broker, data source, always "create stopped". BotCard shows "graph ▸ group @rev" and "Update to latest rev". BotCard learns the latest rev and the graph name from the bot summary fields `graph_latest_rev` and `graph_name` (5.D); it never fetches a graph, per card or otherwise. AddBotBar graph mode gains a group selector. | `frontend/src/features/trading/BotCard.tsx`, `frontend/src/features/trading/AddBotBar.tsx`, `frontend/src/shared/types/trading.ts` (bot summary fields only), `NodeBuilder.tsx` (spawn button slot only) | `frontend/src/features/nodebuilder/SpawnBotsDialog.tsx`, `frontend/src/api/graphSpawn.ts`, `__tests__/spawnDialog.test.tsx` |

**Terminal params (5.A, fixed here for 5.E and 5.F).**

| Terminal | Params |
|---|---|
| `entry`, `exit` | `signal` (attr, bool); `side` (`long \| short`, only in `regime_switch` groups) |
| `size` | `value` (attr, float point or detail; fraction 0.01 to 1.0) or `constant` (number, unit `frac`) |
| `stop` | `value` (attr, percent) or `constant` (number, unit `%`) |
| `trailing_stop` | `pct`, `source` (as `TrailingStopConfig`), `activate_on_profit` (bool), `activate_pct` (number) |
| `time_stop` | `max_bars` (int) |
| `regime` | `signal` (attr, bool), `on_flip` (`hold \| close_only \| close_and_reverse`) |
| `output_group` (network) | `direction` (`long \| short \| regime_switch`), `ticker` (path), `capital_weight` (number, default 1) |

**Contracts.**

Backtest response (legacy fields kept; filled from the group when there is exactly one):

```json
{
  "summary": {}, "trades": [], "equity_curve": [], "baseline_curve": [],
  "cook_id": "ck_...",
  "groups": [
    {"name": "long_leg", "node_id": "n_g1", "path": "/long_leg", "symbol": "AAPL", "interval": "1d",
     "direction": "long", "weight": 1, "capital": 5000.0,
     "summary": {"open_position": null, "exit_connected": true},
     "trades": [], "equity_curve": []}
  ],
  "combined": {
    "summary": {"initial_capital": 10000, "final_value": 11240.5, "total_return_pct": 12.4,
                "max_drawdown_pct": -6.1, "sharpe_ratio": 1.21, "num_trades": 24,
                "exposure_pct": 61.2, "gross_deployed_pct": 48.0},
    "equity_curve": [{"time": "2024-01-02", "value": 10000.0}]
  }
}
```

```ts
// frontend/src/shared/types/strategy.ts (5.E) and frontend/src/api/nodebuilder.ts
export interface GroupResult {
  name: string; node_id: string | null; path: string; symbol: string; interval: string
  direction: 'long' | 'short' | 'regime_switch'; weight: number; capital: number
  summary: BacktestSummary; trades: TradeRecord[]; equity_curve: CurvePoint[]
}
export interface CombinedResult {
  summary: BacktestSummary & { exposure_pct: number; gross_deployed_pct: number }
  equity_curve: CurvePoint[]
}
export interface GraphBacktestResult {
  summary: BacktestSummary; trades: TradeRecord[]; equity_curve: CurvePoint[]; baseline_curve: CurvePoint[]
  cook_id?: string
  groups?: GroupResult[]
  combined?: CombinedResult
}
```

Spawn:

```text
POST /api/graphs/{id}/spawn
body {"rev": 7, "legs": [
  {"group": "long_leg", "allocated_capital": 2500, "broker": "alpaca", "data_source": "alpaca-iex",
   "interval_override": null, "strategy_name": null}
]}
-> 201 {"bots": [{"bot_id": "b_...", "group": "long_leg", "symbol": "AAPL", "direction": "long", "running": false}]}
-> 409 {"detail": {"code": "rev_conflict", "current_rev": 8}}
-> 400 {"detail": {"code": "group_unknown" | "same_symbol_same_direction" | "graph_invalid" | "code_disabled", "message": "...", "diagnostics": []}}
```

- Spawn validates every leg first, then calls `BotManager.add_bots(configs)` once. It is all or nothing.
- Two legs with the same symbol and the same direction, or a leg that collides with an existing bot under the exclusive-symbol guard, are refused up front with `same_symbol_same_direction`.
- `strategy_name` defaults to `"<graph name> ▸ <group>"`.

```ts
// frontend/src/api/graphSpawn.ts
export interface SpawnLeg { group: string; allocated_capital: number; broker: 'alpaca' | 'ibkr'; data_source: 'yahoo' | 'alpaca' | 'alpaca-iex' | 'ibkr'; interval_override: string | null; strategy_name: string | null }
export interface SpawnResult { bots: { bot_id: string; group: string; symbol: string; direction: string; running: false }[] }
export function spawnBots(graphId: string, rev: number, legs: SpawnLeg[]): Promise<SpawnResult>
export function updateBotGraph(botId: string, graphId: string, rev: number): Promise<{ bot_id: string; graph_rev: number }>
```

Graph update:

```text
POST /api/bots/{bot_id}/graph_update   body {"graph_id": "g_...", "rev": 8}
-> 200 {"bot_id": "...", "graph_rev": 8}
-> 409 {"detail": {"code": "in_position"}}            // reuses the existing in-position guard
-> 409 {"detail": {"code": "rev_conflict", "current_rev": 9}}
-> 400 {"detail": {"code": "group_missing" | "symbol_changed" | "direction_changed" | "graph_invalid"}}
```

A new rev that changes the group's symbol or direction is refused. The user spawns a new bot instead.

Bot summary additions (5.D, orchestrator decision 2026-09-30). BotCard reads these; it never fetches the graph.

```text
GET /api/bots -> each bot summary gains
  "graph_name": "pair_aapl_msft" | null,   // current name of graph_id; null when the graph was deleted or the bot has no graph_id
  "graph_latest_rev": 9 | null             // current saved rev of graph_id; null in the same two cases
```

- A bot with `graph_id` set and `graph_latest_rev: null` means the source graph was deleted (S35 "graph deleted" state).
- `storage.py` keeps a `{graph_id: (rev, name)}` head index, filled at start and updated on every write and delete. `graph_head(graph_id)` reads it. Building the summary reads no graph files.

```ts
// frontend/src/shared/types/trading.ts (5.F): the bot summary type gains
graph_name?: string | null
graph_latest_rev?: number | null
```

`BotConfig` additions (5.D):

```python
graph_id: Optional[str] = None
graph_rev: Optional[int] = None
graph_group: Optional[str] = None
graph_direction_mode: Optional[Literal["long", "short", "regime_switch"]] = None

@property
def is_bidirectional(self) -> bool:
    return bool(self.regime and self.regime.enabled) or (
        self.kind == "graph" and self.graph_direction_mode == "regime_switch")
```

Legacy bots without these fields load unchanged.

**Tests to add.**
- Rule snapshot unchanged with `size_series=None` (the parity trio covers it).
- Terminal versus rule parity (critic 26): `trailing_stop` with `activate_pct` versus the rule trailing stop; `time_stop` versus `max_bars_held`; `size`/`stop` versus `position_size`/`stop_loss_pct`.
- A two-group backtest equals the two standalone single-group backtests; combined `exposure_pct` and `gross_deployed_pct` are correct on a hand-computed fixture.
- Group live parity: for a two-group fixture, runner signals match backtest signals bar for bar.
- Regime parity: the 2 formerly skipped tests pass.
- Auto-render of a regime strategy shows the regime children in read-only mode (critic 33).
- No lookahead: a reference bar at time t is never visible before t closes.
- Spawn creates N bots with `running=False` through one `add_bots` call and one save; 409 on a stale `rev`; same symbol and direction refused.
- `is_bidirectional`: the exclusive-symbol guard and P&L plus sizing on a graph `regime_switch` bot.
- Every mutable `BotConfig` field is either in `UpdateBotRequest` or on an explicit denylist (the new `graph_*` fields are on the denylist).
- `graph_update` refuses in position, refuses a symbol change, and sets `graph` and `graph_rev` together.
- `test_bot_summary_graph_rev.py`: the bot summary carries `graph_latest_rev` and `graph_name` equal to the stored envelope; after a PUT the next summary shows the new rev; after a DELETE both are null; building the summary for 10 bots reads no graph file (spy on the file read).
- BotCard (frontend, in `spawnDialog.test.tsx` or its own test): the update button renders from `graph_latest_rev`, and no `GET /api/graphs/{id}` request is made.
- Legacy bots without `graph_id` still load.

**Acceptance (scripted).**
- `backend/venv/bin/python -m pytest backend/tests/nodebuilder backend/tests/test_graph_spawn.py backend/tests/test_bot_bidirectional_graph.py backend/tests/test_update_bot_request_coverage.py backend/tests/test_bot_summary_graph_rev.py -q` passes, with no skipped regime parity tests (`-rs` output shows none).
- A test builds a synthetic AAPL-long / MSFT-short pair graph, backtests it (2 group results plus combined), and spawns 2 stopped bots with the right symbol, direction and capital, with a mocked broker.
- A test edits an existing regime rule strategy as a graph and gets the same backtest as the rule builder.
- `grep -rn "regime.enabled" backend/bot_manager.py backend/bot_runner.py backend/journal.py` finds only the `is_bidirectional` definition.
- `grep -rn "RegimeUnsupportedError" backend frontend/src` finds nothing.
- `npm --prefix frontend test -- networkFrame groupResults spawnDialog` passes.
- `grep -n "getGraph\|/api/graphs/" frontend/src/features/trading/BotCard.tsx` finds nothing (no per-card graph fetch).

---

### Wave 6: Sub-network assets, promotion and dive (T3)

**Goal.** Folders of nodes you can collapse and dive into, reusable assets with promoted params, and a Rules palette. (The network core already landed in 5.0.)

**Order.** {6.A, 6.B} backend in parallel. {6.C, 6.D} frontend in parallel.

| Item | What | Owns | New files |
|---|---|---|---|
| 6.A | Promoted params (`Node.promoted`), substituted in `flatten`; asset instances (`asset_ref`, `locked`) expanded before flatten with composite ids; declared interface `{reads, writes}` checked against the stream schema; `rename_node` now rewrites promoted-param targets. | `backend/nodebuilder/models.py` (`promoted`, `asset_ref`, `locked`), `backend/nodebuilder/kernel/flatten.py`, `backend/nodebuilder/migrate.py` (`rename_node` targets) | `backend/nodebuilder/kernel/assets.py`, `backend/tests/nodebuilder/test_promoted.py`, `test_asset_instances.py` |
| 6.B | Asset library storage and routes; `palette` entries; spawn and bot snapshots bake in definitions (fills the 5.D hook). | `backend/nodebuilder/storage.py`, `backend/routes/graphs.py` (bake-in in spawn) | `backend/routes/graph_library.py`, `backend/tests/test_graph_library.py` |
| 6.C `[SPEC S37, S38]` | Collapse-to-node and dive: a collapsed subnet renders as one node; double-click or I enters, U goes up; breadcrumb `/ > long_leg > regime`; the canvas shows only the current network's children plus boundary nodes; per-network viewport. Registers the Shift+C command and mounts dialogs in slots. | `Canvas.tsx`, `store.ts` (`currentNetworkId`), `NodeBuilder.tsx` (breadcrumb and dialog slots), `rfMapping.ts` | `Breadcrumb.tsx`, `nodes/SubnetNode.tsx`, `nodes/BoundaryNode.tsx`, `commands/network.ts`, `__tests__/dive.test.tsx` |
| 6.D `[SPEC S39-S43]` | Authoring: "Collapse selection into subnet" op with automatic boundary wiring; "Save as asset..."; "Promote to palette"; right-click a param and choose "Promote"; promoted params editable on the subnet node and in the Inspector; Asset Manager (versions, where used); Tab menu asset entries; palette search by attribute (`@volume` matches nodes that read or write it, critic 10). | `nodes/ParamRow.tsx`, `Inspector.tsx`, `TabMenu.tsx`, `search.ts`, `__tests__/search.test.ts` | `operations/collapse.ts`, `AssetManager.tsx`, `SaveAssetDialog.tsx`, `frontend/src/api/graphLibrary.ts`, `__tests__/collapse.test.ts`, `__tests__/promote.test.tsx` |

**Contracts.**

```text
GET    /api/graph_library                         -> 200 {"assets": [AssetListItem]}
GET    /api/graph_library/{name}/{version}        -> 200 AssetFile | 404
POST   /api/graph_library                         body {"name","description","network","promoted","interface"?,"palette"?} -> 201 AssetFile (version = latest + 1)
DELETE /api/graph_library/{name}/{version}        -> 204   (referencing graphs then show "asset_missing"; bots are immune)
```

```json
{
  "name": "regime_filter", "version": 2, "description": "SPY above its 50-day SMA",
  "stream_schema": 1,
  "interface": {"reads": [{"name": "@close", "class": "point", "dtype": "float"}],
                "writes": [{"name": "@regime_on", "class": "point", "dtype": "bool"}]},
  "promoted": [{"name": "lookback", "label": "Lookback", "target": "sma/period", "type": "int", "default": 50}],
  "palette": {"category": "rules", "label": "Regime Filter", "glyph": "R"},
  "network": {"nodes": {}, "wires": []},
  "created_at": "2026-10-20T09:00:00Z"
}
```

```ts
// frontend/src/api/graphLibrary.ts
export interface AttrDecl { name: string; class: 'point' | 'detail'; dtype: string }
export interface PromotedParam { name: string; label: string; target: string; type: ParamType; default: unknown }
export interface AssetFile {
  name: string; version: number; description: string; stream_schema: number
  interface: { reads: AttrDecl[]; writes: AttrDecl[] }
  promoted: PromotedParam[]
  palette: { category: 'rules'; label: string; glyph: string } | null
  network: { nodes: Record<string, GraphNode>; wires: GraphWire[] }
  created_at: string
}
export interface AssetListItem { name: string; versions: number[]; latest: number; palette: AssetFile['palette']; interface: AssetFile['interface']; used_by: { graph_id: string; name: string }[] }
```

- Asset files are immutable per version. Asset names match `^[a-z_][a-z0-9_]{0,63}$`.
- A locked instance is a node of type `subnet` with `asset_ref` and `locked: true`. Its children are not stored in the graph; they come from the library at compile time. An unlocked instance stores its children as a local copy.
- Promoted param values live in the instance's `params` under the promoted `name`. `target` is a path relative to the subnet.

**Tests to add.** Flatten of an asset instance equals the hand-built version; a promoted-param override changes the result; a library version bump does not change existing instances (they pin a version); a bot snapshot is immune to library edits; collapse then undo restores the exact graph; promoted params and paths survive a rename (critic 26); deleting a referenced asset gives an `asset_missing` diagnostic, not a crash; palette search `@volume` finds nodes that read volume.

**Acceptance (scripted).**
- A test saves a "regime_filter" asset once, drops it into 3 graphs with its lookback promoted and set to 3 different values, and backtests all 3 correctly (the vision's T3 exit criterion, in synthetic form).
- `backend/venv/bin/python -m pytest backend/tests/test_graph_library.py backend/tests/nodebuilder/test_promoted.py backend/tests/nodebuilder/test_asset_instances.py -q` passes.
- `npm --prefix frontend test -- dive collapse promote search` passes.

---

### Wave 7: Code at three levels (T5)

**Precondition: met.** John decided the code-nodes design note on 2026-09-30: real Python everywhere, no sandbox. slx is rejected.

**Goal.** Per-parameter expressions, per-node code blocks and Wrangle nodes in real Python, with the `@attr` sugar, `ch()` references and auto-promoted params, Monaco, and edit-time diagnostics. Code failures pause one bot; they never crash the backend or block the event loop.

**Order (critic 24).** 7.A first (pure new files). Then 7.B (it owns the model fields). Then 7.C. 7.D (frontend) runs in parallel from the start against the `parse_code` and `/validate` contracts.

| Item | What | Owns | New files |
|---|---|---|---|
| 7.A | Python runtime core (design note section 4): the `@name` sugar with `tokenize` and its column map; the `ast` scan (parse only) for `ch*()` specs, reference paths, reads, writes and annotation dtypes; `prepare()` (size, sugar, parse, scan, `compile()`, never runs code); `run()` with the curated namespace (`np`, `pd`, `math`, `sl`, `ta` = pandas_ta, `stream`, `ch*`, full builtins); the `stream` proxy (alignment, dtype and declared-write checks); the `sl` helpers, which call the same backend functions as the nodes; exception-to-diagnostic mapping (1-based line, 0-based column, from the `SyntaxError` or the node's traceback frame); the wall-clock limits and the leaked-cook counter; `code_enabled()` for `SL_CODE_NODES`; the `audit_log()` helper. Import it as `nodebuilder.code` only. | none shared | `backend/nodebuilder/code/{__init__,sugar,promote,runtime,sl}.py`, `backend/tests/nodebuilder/code_nodes/{__init__,test_sugar,test_promote,test_runtime,test_sl_parity}.py`, `backend/tests/nodebuilder/vectors/sugar.json` |
| 7.B | Channels and references: `ch`, `chf`, `chi`, `chs`, `chb`, `chv` resolved at cook time from 7.A's static specs; `ch("../node/param")`, `ch("../node/@attr")`, `ch("../name")` (a param of the enclosing network) and absolute paths; the param dependency graph with cycle detection merged into compile order, and its edges returned for `/validate` as `param_deps`; the param-resolution hook in `evaluate.py`; `rename_node` rewrites `ch()` path strings. Model fields `Node.code`, `Node.spare_params`, and `{"expr": ...}` param values. | `backend/nodebuilder/kernel/evaluate.py` (param-resolution hook), `backend/nodebuilder/migrate.py` (`rename_node`), `backend/nodebuilder/models.py` (`code`, `spare_params`, expr values) | `backend/nodebuilder/kernel/params.py`, `backend/tests/nodebuilder/test_ch_refs.py`, `backend/tests/nodebuilder/vectors/ch_refs.json` (shared Py/TS vectors) |
| 7.C | The three levels in compile: (1) `{"expr"}` values, `eval` mode, detail attributes only, scalar of the param's type; (2) `Node.code` runs after the node's main compute, with its outputs and the input stream in scope; (3) the `wrangle` node (inputs `in0..in3` merged; several writes allowed). Every code-bearing node's `lookback_bars` (default 500) enters `required_lookback_bars`. Routes: `POST /api/nodebuilder/parse_code`, `GET /api/nodebuilder/code_capabilities`, and `/validate` gains `param_deps` and `dtype: "any"` for unannotated code writes. Ticker `symbol`/`interval`/`prefix` reject expressions. API routes cook in a worker thread with the 60 s guard. Bots: the cook runs in `_run_in_executor` under `asyncio.wait_for` with the 10 s guard; a timeout or a code exception places no order and pauses the bot with a `pause_reason`; a timed-out cook's late result is dropped by tick id; `SL_CODE_NODES=0` pauses code-bearing bots with `code_disabled`; bots.json code goes through `prepare()` on load and a syntax error pauses only that bot. Audit lines on every code-bearing graph save and every bot start (and spawn, `graph_update`). Spawn and `graph_update` refuse code-bearing graphs when disabled. | `backend/routes/nodebuilder.py`, `backend/routes/graphs.py` (audit call on save and seed, `code_disabled` refusal in spawn only), `backend/routes/bots.py` (`code_disabled` refusal in `graph_update` only), `backend/routes/graph_library.py` (audit call on save only), `backend/bot_manager.py` (load check, pause, audit on start), `backend/bot_runner.py` (guarded cook, pause, stale-result drop) | `backend/nodebuilder/trading/nodes_code.py`, `backend/tests/nodebuilder/test_code_levels.py`, `backend/tests/nodebuilder/test_code_live_parity.py`, `backend/tests/test_code_kill_switch.py`, `backend/tests/test_code_timeout.py`, `backend/tests/test_code_botsjson_load.py`, `backend/tests/test_code_audit.py` |
| 7.D `[SPEC S44-S49]` | Code UI: Monaco, lazy-loaded and bundled locally (`monaco-editor` plus Vite `?worker`, no CDN); Monaco's built-in `python` language, extended with a Monarch rule for the `@attr` sugar and highlighting for `ch*()` and `sl.*` calls, in the FA5 colours; completion offers `available_attrs` after `@`, `ch*` signatures, `sl.*` functions (from `code_capabilities`), and `np`/`pd`; markers from `parse_code` through `toMonacoRange` (1-based line and 0-based column in, Monaco's 1-based column out); an `=` toggle per ParamRow with a one-line `ExprInput` (`type="text"`); a collapsible code drawer on the node and in the Inspector; the Wrangle node body; spare params render as normal ParamRows, with S48's "read by" glyph from `/validate` `param_deps`; the code-disabled state, including the BotCard pause text. | `nodes/ParamRow.tsx`, `Inspector.tsx`, `frontend/package.json`, `frontend/vite.config.ts` (worker config only), `frontend/src/api/nodebuilderValidate.ts` (`param_deps` response type only), `frontend/src/features/trading/BotCard.tsx` (S49 pause text only) | `frontend/src/features/nodebuilder/code/{MonacoEditor,CodeDrawer,ExprInput}.tsx`, `code/pythonLanguage.ts`, `nodes/WrangleNode.tsx`, `frontend/src/api/nodebuilderCode.ts`, `__tests__/exprInput.test.tsx`, `__tests__/chRefs.vectors.test.ts`, `__tests__/pythonLanguage.test.ts`, `__tests__/toMonacoRange.test.ts` |

**Contracts.**

```text
POST /api/nodebuilder/parse_code
body {"code": "...", "context": "expr" | "node_code" | "wrangle",
      "expected": {"type": "int" | "float" | "bool" | "string"} | null,   // for "expr": the param's type
      "graph": Graph | null, "node_id": "n_rsi" | null}                  // for ch() resolution and available attributes
-> 200 {
  "ok": false,
  "params": [{"name": "lookback_bars", "type": "int", "default": 500, "min": 1, "max": 100000, "label": "lookback bars", "options": null},
             {"name": "atr_period", "type": "int", "default": 14, "min": 2, "max": 50, "label": "atr_period", "options": null}],
  "reads":  [{"name": "@close", "class": "point", "dtype": "float"}],
  "writes": [{"name": "@vol_regime", "class": "point", "dtype": "bool"},     // dtype from the annotation `@vol_regime: bool = ...`
             {"name": "@atr_pct", "class": "point", "dtype": "any"}],       // no annotation: known after the first cook
  "result_type": null,             // always null in W7: parse_code never runs code; the resolved value arrives after a cook (S44 "= 21")
  "diagnostics": [Diagnostic]      // line 1-based, col 0-based (Python's convention); end_line, end_col the same
}
```

- `parse_code` runs `prepare()` only (sugar, parse, scan, `compile()`). It never executes the code. Its docstring states the line and column convention.
- For `node_code` and `wrangle`, `params` always starts with `lookback_bars` unless the code declares it.
- The scan cannot tell a per-bar write from a scalar write. So `parse_code` and `/validate` report every code write as `class: "point"`, with the dtype from its annotation (`bool` or `float`) or `"any"`. At cook time a scalar is stored as a detail attribute; a consumer that needs a point attribute then gets `attr_type` from the cook.

```text
GET /api/nodebuilder/code_capabilities
-> 200 {"enabled": true, "language": "python",
        "limits": {"max_source_bytes": 8192, "default_lookback_bars": 500, "cook_timeout_s": {"bot": 10, "backtest": 60}},
        "modules": ["np", "pd", "math", "sl", "ta"],
        "functions": [{"name": "sl.rsi", "signature": "sl.rsi(x, period=14)", "returns": "series_float", "doc": "..."}],
        "leaked_cooks": 0}
```

- `functions` lists the `sl` helpers only. numpy, pandas and pandas_ta are listed by module name in `modules`.
- `leaked_cooks` counts timed-out cooks whose threads are still running (they end by themselves or at a backend restart).

```text
POST /api/nodebuilder/validate   (W7 additions; orchestrator decision 2026-09-30)
-> 200 {..., "param_deps": [
  {"reader_id": "n_long_entry", "reader_param": "threshold", "target_id": "n_spread_z", "target": "threshold"},
  {"reader_id": "n_rsi", "reader_param": null, "target_id": "n_vol", "target": "@atr_pct"}
]}
```

- One edge per literal `ch()` reference. `reader_param` names the param whose expression holds the call, or is null when the call sits in a code block or a Wrangle body. `target` is a param name or an `@attr`.
- 7.B computes the edges in `kernel/params.py`; 7.C returns them. S48's "read by" glyph renders from them.
- `streams` may now carry `"dtype": "any"` for an unannotated code write. This value appears only in `/validate`. A cooked stream always has a real dtype.

```ts
// frontend/src/api/nodebuilderCode.ts
export type CodeContext = 'expr' | 'node_code' | 'wrangle'
export interface SpareParamSpec { name: string; type: 'float' | 'int' | 'string' | 'bool' | 'vector'; default: unknown; min?: number; max?: number; options?: string[] | null; label: string }
export interface ParseCodeResponse {
  ok: boolean
  params: SpareParamSpec[]
  reads: AttrDecl[]; writes: AttrDecl[]      // AttrDecl.dtype may be 'any' for a code write
  result_type: null                          // W7: always null (parse_code never runs code)
  diagnostics: Diagnostic[]                  // line 1-based, col 0-based
}
export interface CodeCapabilities {
  enabled: boolean
  language: 'python'
  limits: { max_source_bytes: number; default_lookback_bars: number; cook_timeout_s: { bot: number; backtest: number } }
  modules: string[]
  functions: { name: string; signature: string; returns: string; doc: string }[]
  leaked_cooks: number
}
export function parseCode(body: { code: string; context: CodeContext; expected: { type: string } | null; graph: Graph | null; node_id: string | null }): Promise<ParseCodeResponse>
export function getCodeCapabilities(): Promise<CodeCapabilities>
// Converts one diagnostic to a Monaco range: startLineNumber = line, startColumn = col + 1,
// endLineNumber = end_line ?? line, endColumn = (end_col ?? col + 1) + 1 (at least startColumn + 1).
export function toMonacoRange(d: Diagnostic): { startLineNumber: number; startColumn: number; endLineNumber: number; endColumn: number }

// frontend/src/api/nodebuilderValidate.ts (W7 addition)
export interface ParamDep { reader_id: string; reader_param: string | null; target_id: string; target: string }
// the validate response gains: param_deps: ParamDep[]   ([] when there are no ch() references)
```

**`ch()` scoping rules (critic 11), tested as shared vectors.**
- A bare `chf("threshold")` reads the calling node's own spare param.
- `ch("../other/param")` reads a sibling's param. `ch("../other/@attr")` reads that node's output attribute.
- From inside a subnet child, `chf("../lookback")` reads the promoted param at the subnet root.
- Absolute paths such as `/shared/spread/@spread` work.
- Paths resolve against the **pre-flatten** hierarchy, so they work through flattened asset instances.
- `ch*` names, paths and keywords must be string literals, so the dependency graph is known before any code runs. Anything else is `ch_dynamic`. At run time a `ch*` call the scan did not see also raises `ch_dynamic`.
- A parameter expression must return a scalar. A series result in a scalar param is `code_type`, with the hint "use a Wrangle for per-bar logic" (divergence V9).

**Tests to add.** The design note, section 5, is the full list. These are required:
- **Sugar rewrite vectors** (`code_nodes/test_sugar.py`, `vectors/sugar.json`): all 28 rows of the design note table, including matmul and decorators left alone, strings and comments untouched, and the column map in both directions.
- **Auto-promotion** (`code_nodes/test_promote.py`): specs from literal `ch*()` calls, in order, with `lookback_bars` first; non-literal names are `ch_dynamic`; conflicting specs are `code_type`; binding `stream` is `code_syntax`; a non-literal write is `attr_dynamic`.
- **`ch()` scoping vectors** (`test_ch_refs.py`, shared with vitest): bare, sibling param, sibling `@attr`, `../name` from a subnet child, absolute, pre-flatten resolution, rename rewrite, `ch_cycle`.
- **Exception to diagnostic** (`code_nodes/test_runtime.py`): a `SyntaxError` maps to Python's line and `offset - 1`; `x = 1 / 0` on line 3 gives `code_runtime` at 3:4; an error inside numpy points at the user's calling line; columns after a rewritten `@attr` map back to the user's text.
- **Timeout to failed cook to bot pause** (`test_code_timeout.py`): a Wrangle that sleeps 2 s under a 0.2 s test guard; no order (mocked broker); the bot pauses with a `pause_reason` starting `code_timeout`; another bot's tick completes on time; `leaked_cooks` rises by 1; the late result is dropped.
- **Backtest/live parity** (`test_code_live_parity.py`): a graph with one code param and one Wrangle gives the same signals live as in the backtest.
- **Kill switch** (`test_code_kill_switch.py`): `SL_CODE_NODES=0` rejects validate, backtest, inspect, preview, spawn and `graph_update` with `code_disabled`; save still works; an existing code-bearing bot pauses with `pause_reason: "code_disabled"` and does not crash-loop.
- **bots.json syntax error on load** (`test_code_botsjson_load.py`): that bot loads paused with `code_syntax`; the other bots and the manager are unaffected.
- **`sl` helper parity** (`code_nodes/test_sl_parity.py`): each helper equals its node's output exactly over the NaN warmup, on a real cached frame and a synthetic frame; `sl.shift(x, -1)` raises.
- Also: `test_code_levels.py` (scalar rules, detail-only reads in expressions, `ticker_param_not_codeable`); `test_code_audit.py` (one `code_audit` line per snippet on save and start, sha256 of the original source); `toMonacoRange.test.ts`; `pythonLanguage.test.ts`; ExprInput renders `type="text"`.

**Acceptance (scripted).**
- A test builds John's vision example in Python: an RSI whose `period` is `7 if chf("../vol/threshold") > 2 else 21`, plus a Wrangle computing ATR percent that writes `@vol_regime` and feeds an AND. The graph backtests, and live parity holds.
- A second test builds the adaptive case as a Wrangle: `@rsi_adaptive = np.where(@atr_pct > chf("th", default=2.0), sl.rsi(@close, 7), sl.rsi(@close, 21))`.
- A snippet with a syntax error returns `code_syntax` at Python's line and `offset - 1`. A snippet that raises returns `code_runtime` at the user's line and column.
- `backend/venv/bin/python -m pytest backend/tests/nodebuilder/code_nodes backend/tests/nodebuilder/test_ch_refs.py backend/tests/nodebuilder/test_code_levels.py backend/tests/nodebuilder/test_code_live_parity.py backend/tests/test_code_kill_switch.py backend/tests/test_code_timeout.py backend/tests/test_code_botsjson_load.py backend/tests/test_code_audit.py -q` passes.
- One execution path: `grep -rnE "\b(exec|eval)\(" backend/nodebuilder backend/bot_runner.py backend/routes` finds calls only in `backend/nodebuilder/code/runtime.py`.
- `grep -rni "slx" backend/nodebuilder frontend/src/features/nodebuilder` finds nothing.
- After `npm --prefix frontend run build`, Monaco is only in a separate chunk: `grep -l "monaco" frontend/dist/assets/index-*.js` finds nothing, and a file matching `frontend/dist/assets/*monaco*` or an `editor.worker` chunk exists.

---

## 7. UI surfaces

Every `[SPEC]` item is built from a design spec written by the UI/UX designer (Fable):
- **Foundation** (tokens, node anatomy, category colours, type scale, focus, motion, empty and error states): `docs/design/nodebuilder/ui-ux-spec.md`.
- **Surfaces S01 to S30:** `docs/design/nodebuilder/surfaces-w1-w4.md`.
- **Surfaces S31 to S49:** `docs/design/nodebuilder/surfaces-w5-w7.md`.

Implementers follow those specs. If a spec is missing when a wave starts, the item waits. If a spec conflicts with a contract in this plan, stop and ask the orchestrator.

| Id | Wave (item) | Surface |
|---|---|---|
| S00 | W1 (all) | Foundation: design tokens, node anatomy, category colours, typography, focus rings, motion, light and dark. |
| S01 | W1 (1.F) | Graph toolbar: name with dirty marker, New, Open, Save, Save As, Rename, Duplicate, Delete, Export, Import, error count, Run state. |
| S02 | W1 (1.F) | Graph Browser dialog: list, search, sort, open, duplicate, delete; wording for "graphs live here, not in the strategy picker". |
| S03 | W1 (1.F) | Draft restore prompt on reload. |
| S04 | W1 (1.F) | Save conflict (409) dialog: reload, save as copy, or overwrite after review. |
| S05 | W1 (1.G) | Diagnostics: node error and warning badges, invalid param field, the list popover behind the toolbar count, Run disabled state. |
| S06 | W1 (1.F) | AddBotBar graph picker fed from the API (empty, loading, error). |
| S07 | W1 (1.F) | Notice banners (regime removed, unsupported nodes, restored draft) in one consistent style. |
| S08 | W2 (2.E) | Node ports: named input handles, dynamic `in*` add/remove, single output handle, port labels. |
| S09 | W2 (2.E) | Attribute picker param row: dropdown of available attributes with dtype and writer, free text, missing-attribute state. |
| S10 | W2 (2.E) | Write chips `+@name` with inline rename and uniqueness feedback. |
| S11 | W2 (2.E) | Wire labels: derived consumer reads, fan-out de-confliction, hover card with the full stream. |
| S12 | W2 (2.E) | Time-of-day range widget and day-of-week picker. |
| S13 | W2 (2.E) | Unsupported or unknown node visual (read-only render of a node compile cannot run). |
| S14 | W3 (3.A) | Inspector with a selection: name and path, type, description, params, reads and writes, flags, diagnostics. |
| S15 | W3 (3.A) | Inspector with no selection: legend, flags, keys (as the mockup). |
| S16 | W3 (3.B) | Display and bypass flag dots and their key feedback. |
| S17 | W3 (3.E) | Network boxes: label, colour, resize, membership feedback while dragging. |
| S18 | W3 (3.E) | Sticky notes. |
| S19 | W3 (3.G) | Context menus for node, wire and pane. |
| S20 | W3 (3.H) | Status bar: zoom, cursor coordinates, selection, cook state. |
| S21 | W3 (3.H) | `?` shortcut overlay. |
| S22 | W3 (3.H) | Top hint bar, Reset view button, recoloured MiniMap; React Flow Controls removed (A2, accepted 2026-09-30). |
| S23 | W3 (3.G) | Wire reconnect and drop-on-wire splice feedback; long-wire middle fade. |
| S24 | W3 (3.0/3.D) | Tab menu at the cursor: search, categories, keyboard flow. |
| S25 | W4 (4.B) | DataSheet drawer: columns, writer chips, bool cells, header histograms, filter, jump to trades, detail strip. |
| S26 | W4 (4.C) | Sparklines on nodes (line and bool strip). |
| S27 | W4 (4.C/4.D) | Auto-cook toggle and cook-state indicator. |
| S28 | W4 (4.D) | Graph/chart split view, including the chart constraints in D10. |
| S29 | W4 (4.D) | "Set by graph" greyed fields in the strategy settings panel. |
| S30 | W4 (4.D) | "Not available for graph results" state in rule-only panels; graph result header. |
| S31 | W5 (5.E) | Network frame: expanded network in a netbox-style frame with ports on the frame edge (one visual model for all networks). |
| S32 | W5 (5.E) | Output Group frame header: name, direction, primary ticker, colour; terminals inside. |
| S33 | W5 (5.E) | Per-group result tabs plus "Combined" (with exposure). |
| S34 | W5 (5.F) | Spawn bots dialog: one row per group, capital, broker, data source, "create stopped". |
| S35 | W5 (5.F) | BotCard graph badge "graph ▸ group @rev" and "Update to latest rev" with its refusals; the latest rev comes from the bot summary (`graph_latest_rev`). |
| S36 | W5 (5.F) | AddBotBar group selector. |
| S37 | W6 (6.C) | Breadcrumb and dive/up navigation. |
| S38 | W6 (6.C) | Collapsed subnet node and boundary nodes. |
| S39 | W6 (6.D) | Collapse selection into subnet (Shift+C) flow. |
| S40 | W6 (6.D) | Promote a param (right-click) and promoted params on the subnet node and in the Inspector. |
| S41 | W6 (6.D) | Save as asset dialog and "Promote to palette". |
| S42 | W6 (6.D) | Asset Manager: versions, where used, delete with warning. |
| S43 | W6 (6.D) | Rules palette category, asset entries in the Tab menu, attribute search. |
| S44 | W7 (7.D) | `=` expression toggle and one-line ExprInput on a param row. |
| S45 | W7 (7.D) | Code drawer on the node and in the Inspector. |
| S46 | W7 (7.D) | Wrangle node body. |
| S47 | W7 (7.D) | Monaco theme, Python language with the `@attr` sugar, `ch*` and `sl.*` highlighting, completion, diagnostics markers and gutter. |
| S48 | W7 (7.D) | Spare params (auto-promoted from `ch*()`) rendered as normal param rows, with the "read by" glyph from `/validate` `param_deps`. |
| S49 | W7 (7.D) | Code-disabled state: graph banner and bot `pause_reason` on BotCard. |

---

## 8. Gates and safety rules

### 8.1 Gate commands (every wave)

Run from any directory. All paths are absolute.

```bash
npm --prefix /Users/jroxenhed/Documents/strategylab/frontend run build
npm --prefix /Users/jroxenhed/Documents/strategylab/frontend test
(cd /Users/jroxenhed/Documents/strategylab/backend && venv/bin/python -m pytest tests -q)
/Users/jroxenhed/Documents/strategylab/bin/verify-batch.sh F435-W<n>     # build + render probe + backend import smoke
```

`verify-batch.sh` needs the backend running on `:8000` for the render probe.

### 8.2 Per-wave extra gates

| Wave | Extra gate |
|---|---|
| W1 | Route round-trip test; migration of bots.json snapshots; catalog drift test. |
| W2 | `bench_graph_cook.py --check`; the rule-coverage sweep has no expected failures; the legacy-equivalence test passed and was then deleted. |
| W3 | The command-key test; render probe. |
| W4 | Backtest timing within 5 % of W2; the split-view toggle probe (20 toggles, no console error, no rAF loop). |
| W5 | No skipped regime parity tests; spawn tests with mocked brokers; `regime.enabled` grep. |
| W6 | The 3-graph asset reuse test. |
| W7 | The robustness tests (timeout pause, bots.json syntax error on load, kill switch); backtest/live parity with code; the single-exec-path grep; the Monaco lazy-chunk check. |

### 8.3 The parity trio rule

`backend/tests/nodebuilder/test_backtest_parity.py`, `test_two_surface_parity.py` and `test_run_simulation_snapshot.py` must be green at the end of every wave. **No snapshot or fixture regeneration** is allowed unless the wave says so. No wave in this plan says so. W5 only un-skips the 2 regime parity tests. If a parity test fails, the change is wrong, not the test.

### 8.4 Money safety in tests

- No test starts a bot. No test places, modifies or cancels an order.
- Brokers are mocked in every bot, spawn and runner test.
- Spawn always creates stopped bots (`running=False`). A test asserts this.
- Tests that touch `bots.json` or the graphs folder use a temporary `STRATEGYLAB_DATA_DIR`.

### 8.5 Review

Severity-graded review per the orchestrator playbook. The contract surfaces in this plan (graph JSON, stream schema, spawn, `graph_update`, `BotConfig` fields, the bot summary fields, the code runtime in `backend/nodebuilder/code/`) always get at least tier B. Changes to the code runtime get a reliability reviewer who checks the D1 guards: no cook on the event loop, the wall-clock guard and its pause path, the stale-result drop, and the bots.json load check. There is no security claim to review: by John's decision, code has full access. Any change to an `sl` helper adds a row to the parity test.

---

## 9. John-run live gates

Agents cannot start bots. These gates are **questions for John**. The orchestrator adds each one to `~/.local/state/strategylab/john-questions.md` when its wave is done, and records the outcome in `JOURNAL.md`. The kill-switch points of the vision (re-evaluate after a tier) are these gates.

- **G1, after W0.** Will you run one graph bot on paper for one full market session? Pass: zero evaluator errors in the log, and the bot's stop matches the graph backtest's stop. This also closes the never-recorded T2 exit criterion ("a graph strategy runs live for at least one trading day").
- **G2, after W5.** Will you spawn the pair graph as two paper bots and run them for one session? Pass: both bots trade their own symbol and direction, and neither has evaluator errors.
- **G3, after W7.** Will you run one paper bot whose graph has a code parameter and a Wrangle, for one session? Pass: zero evaluator or code errors, and the bot's signals match the backtest for that session.

Also ask after W2 and after W5: does the graph now beat the rule builder for the strategies you actually trade? If not, we stop and rethink before the next wave (vision risk 4).

---

## 10. Follow-ups (out of scope for this build)

- **W8 position-state Data nodes** (`bars_in_trade`, `unrealized_pct`, `entry_price`, `equity_drawdown`). Allowed only in sub-graphs feeding Exit, Stop or Size. Compiled to per-bar closures evaluated inside `_run_simulation`. Keep them walled off from the column model (divergence V6).
- **W8 backtest-as-node:** trades as `trade` primitives and metrics as detail attributes. The stream format already reserves both (section 3), so this adds producers only.
- **W8 density toggle** (Atom / Standard / Rich).
- **Candle-pattern selector** widget, once pattern nodes exist.
- **Nodes for dynamic sizing, skip-after-stop and trading hours,** which stay sidebar-owned for now (D11).
- **Regime in walk-forward analysis** and graph strategies in the optimizer, WFA and sensitivity panels.

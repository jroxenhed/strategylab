# Node Builder UI surfaces, waves W5 to W7 (S31 to S49)

Owner: Fable 5.1, lead UI/UX designer for the node builder. Date: 2026-09-30. Task: F435.
Status: build spec. Implementers of 5.E, 5.F, 6.C, 6.D and 7.D build from this file plus the foundation.

Read first: the foundation `docs/design/nodebuilder/ui-ux-spec.md` (tokens, node anatomy, wires, keys, Inspector, global states) and the prototype `docs/design/nodebuilder/prototype/node-editor-v3.html`. This file never repeats a token value; it names the token. Contracts (request and response shapes, param lists, diagnostic codes) come from the plan of record `docs/plans/2026-09-29-node-builder-finish-plan.md`, sections 4, W5, W6 and W7. If a shape here disagrees with the plan, the plan wins; stop and report.

Plain-English summary: this file says how the last three waves look and behave. Wave 5 is groups of trading outputs, reference tickers, per-group results and the dialog that turns a graph into paper-trading bots. Wave 6 is folders of nodes (subnets), diving in and out of them, and saving them as reusable assets. Wave 7 is code: a formula on any parameter, a code block on any node, and a Wrangle node with a small editor.

Each surface is self-contained. It has: purpose, placement, anatomy (sizes and tokens), states, interactions and keys, copy (exact strings), accessibility, must-not rules, and acceptance checks.

---

## Foundation amendments

These change or extend the foundation. They are the only places where this file overrides it.

**FA1. Network nodes have two views, not a header-only collapse.** Foundation 4.9 gives every node a 26px header-only "collapsed" state on `X`. For network nodes (`subnet`, `output_group`, `regime`, asset instances) `X` instead toggles between the **frame** view (expanded in place, S31) and the **card** view (one node, S38). Network nodes never take the header-only state. The state is stored in `Node.meta.view: "frame" | "card"` (default `frame` for `output_group` and `regime`, `card` for a subnet created by Shift+C or placed from the library).

**FA2. Wires into a network are auto-routed through boundary ports.** Foundation 5.4 lists invalid drop targets. Add: a port inside an expanded frame is a **valid** target for a wire from outside the frame. The editor routes it: it adds an input port on the frame edge, a `subnet_input` boundary node, and two wires (outside node to frame port, frame port to inside node). The user sees one continuous wire with a small dot on the frame edge (S31). The same rule applies in the other direction for a subnet's single output. Only a wire between two nodes that have no common route (two different groups, or across two sibling subnets) is invalid, with cursor `not-allowed` and the `wire_crosses_network` message.

**FA3. Terminal bodies may hold up to two rows.** Foundation 4.10 says a terminal body holds one read chip or one param row. The `regime` terminal holds one read chip and one select row (`on_flip`). The `trailing_stop` terminal holds two rows (`pct`, `source`). All other terminals keep one. Terminal width stays 118px.

**FA4. The Ticker node has no `source` row.** The prototype's `aapl` node shows `source yahoo`. Since D11 the data source is owned by the sidebar (backtest) and the spawn dialog (bots), and the v3 migration removes the Ticker `source` param. Ticker rows are `symbol`, `interval`, and for reference tickers `prefix` (S32c). Remove the row from the prototype at the next prototype update.

**FA5. New code tokens.** Foundation 2.5 defines `--nb-code-expr`, `--nb-code-keyword`, `--nb-code-number`, `--nb-code-comment`. Add to `tokens.css`:

| Token | Value | Use |
|---|---|---|
| `--nb-code-func` | `#34d399` (same value as `--nb-cat-indicator`) | `sl.*` helper calls (`sl.rsi`, `sl.zscore`). Matches the prototype's `.fn`. |
| `--nb-code-string` | `#fcd34d` (same value as `--nb-code-expr`) | String constants. |
| `--nb-code-pykw` | `#c3c9d6` (same value as `--nb-text-secondary`) | Python keywords (`if else and or not for def return import True False None`) and decorators. Weight 600. |
| `--nb-code-local` | `#eef1f6` (same value as `--nb-text`) | Local names and operators. |
| `--nb-code-bg-active-line` | `rgba(255,255,255,0.03)` | Monaco current line. |

`@attr` reads and writes and every `ch*` function stay in `--nb-code-keyword`. A written attribute (`@x = ...`, the target of an assignment) is also weight 600. No other color joins the code palette.

**FA6. Toolbar overflow gains one row.** Foundation 3.1 overflow menu (`⋯`): add `Asset Manager…` after `Import JSON`, before `Delete`, separated by a line.

**FA7. Keys.** Add to foundation 6.2: `Shift+X` = toggle frame / card view on the selected network nodes (alias of `X` for network nodes, so a mixed selection of plain nodes and networks behaves predictably: `X` collapses the plain nodes and toggles the networks; `Shift+X` touches only the networks). `Cmd+Shift+A` = open the Asset Manager. Both go through the command registry.

---

## Shared rules for W5 to W7 surfaces

- **Every dialog** (Spawn bots, Save as asset, Asset Manager, Promote param) uses: `--nb-bg-elevated`, radius `--nb-radius-menu`, `--nb-shadow-popover`, 1px `--nb-border-strong`, a 40px title row (sans 14px 600 `--nb-text`, close `✕` 28x28 at the right), body padding 16px, a 48px footer with buttons right-aligned (Cancel as a ghost button, the primary in `--nb-accent-primary-bg` with `--nb-accent-primary` text and border). A dialog is `role="dialog"` with `aria-modal="true"` and `aria-labelledby` pointing at its title. Focus moves to the first field on open and returns to the opener on close. `Esc` closes (Cancel). `Cmd+Enter` runs the primary. A 40% `--nb-bg` scrim sits under it. Dialogs mount in the `NodeBuilder.tsx` dialog slot defined by 6.C.
- **Buttons**: 28px tall, sans 12px 500, radius 4, padding `0 10px`. Ghost: `--nb-text-secondary` on transparent, border 1px `--nb-border`, hover `--nb-bg-hover`. Primary as above. Destructive: `--nb-error` text, border `rgba(248,113,113,0.4)`. Disabled: opacity 0.45, `cursor: not-allowed`, and a `title` that says why.
- **Selects and inputs in dialogs**: 28px tall, `--nb-bg-input`, border 1px `--nb-border`, radius 4, sans 12px (mono 12px for identifiers and numbers), focus ring 1px `--nb-border-focus`. Number inputs are `type="text" inputMode="decimal"` (F278).
- **Errors from the server** show the `detail.message` (or the `detail` string) in a 32px `--nb-error` 12% tint bar inside the dialog above the footer, never the axios text. When `detail.diagnostics` is present, the bar has a `Show diagnostics` link that closes the dialog and opens the Inspector Diagnostics tab.
- **Toasts**: 36px, bottom-center of the canvas column, `--nb-bg-elevated`, sans 12px, auto-hide after 4 s, one action link at most. `role="status"`.
- **Direction pills** (used in S32a, S33, S34, S35, S36): 16px tall, padding `0 5px`, radius 3, sans 600 10px/16px, letter-spacing 0.06em. `LONG` on `rgba(52,211,153,0.16)` in `--nb-ok`. `SHORT` on `rgba(248,113,113,0.16)` in `--nb-error`. `SWITCH` on `--nb-tint-network` in `--nb-cat-network`.
- **Ticker chip** (`AAPL · 1d`): same box, mono 500 10px, `--nb-tint-ticker` background, `--nb-cat-ticker` text, no letter-spacing.

---

# Wave 5

## S31. Network frame (expanded network in place)

**Purpose.** One visual model for every network: a subnet, a regime network and an Output Group all render as a frame with their children inside and their ports on the frame edge. Nobody has to dive to see what a network does.

**Placement.** On the canvas, at `Node.position` of the network node, in the same z-layer as network boxes (under wires, under nodes). Children are React Flow children (`parentId` = the network node id) with positions relative to the frame's top-left.

**Anatomy.**
- Frame: `position: absolute`, border 1px `rgba(241,245,249,0.28)` for Output Groups, or 1px solid at 45% alpha of `--nb-cat-network` for subnets and regime networks; background `rgba(241,245,249,0.025)` (groups) or `rgba(129,140,248,0.04)` (subnets, regime); radius `--nb-radius-frame`. No shadow.
- Size: the bounding box of the children plus 16px padding on every side plus 32px at the top (room for the tab), min 320x120. A user resize (8px corner grips on hover, `nwse-resize` cursors) stores `Node.meta.frame_min: [w, h]`; auto-fit never shrinks below it. Children never leave the frame: dragging a child to the edge grows the frame.
- Tab (26px, top-left inside the frame at `left: 8px; top: 6px`, background `--nb-bg`, radius 4, padding `0 6px 0 4px`, gap 6px): glyph chip 16x16 (`O` in `--nb-cat-output` for groups, `N` in `--nb-cat-network` for subnets, `R` in `--nb-cat-network` for regime), name sans 12px 600 `--nb-text`, then the type-specific pills (S32a for groups; for a subnet: `subnet · 7 nodes` mono 10px `--nb-text-muted`; for regime: `regime` mono 10px), then the diagnostics badge (foundation 4.8) when present, then the flag dots (display, bypass) at the right end of the tab, 6px gap, same rules as foundation 4.6 (an Output Group has no display flag; it has no output).
- Frame ports: input ports sit on the top edge, evenly spaced as in foundation 4.3, 10px circle with a 2px ring in the frame color, label above in mono 9px (`in0`, or the boundary node's name). The single output port (subnets only) sits bottom center, filled in `--nb-cat-network`. An Output Group has input ports only.
- Inner wires: from a frame port straight down to the inside node's input, drawn in the ordinary wire style. Together with the outside wire the user sees one continuous line with a 10px dot on the frame edge.
- The boundary nodes themselves (`subnet_input`, `subnet_output`) are **not drawn as cards** while the network is shown as a frame. They are the frame ports. They appear as half-height cards only when the user dives into the network (S38).

**States.**

| State | Look |
|---|---|
| default | as above |
| hover (border or tab) | border alpha 0.45 (groups) / 0.7 (subnets); the grips appear |
| selected (click the tab or border) | `box-shadow: 0 0 0 1.5px var(--nb-selection)` on the frame; the Inspector shows the network's params (S32a for groups, S40 for promoted params) |
| child selected | the frame is unchanged; the child gets the normal ring |
| drop target (a node dragged over the frame for 150 ms) | border `--nb-selection` 1px solid, background alpha doubled; on release the node is reparented into the frame |
| drag out | a child dragged fully outside the frame for 150 ms shows the parent canvas as the target (frame border returns to default); on release it is reparented to the parent network and its wires are re-routed (FA2) or removed with a toast when no route exists |
| error (`group_invalid`, `missing_terminal`, `boundary_invalid`, `asset_missing`) | error badge in the tab, border `rgba(248,113,113,0.6)` |
| bypassed (subnets only) | the tab shows the amber dot; children keep full opacity; the output stream equals the `in0` stream (pass-through) |
| read-only graph (auto-render of a regime rule strategy, critic 33) | the regime frame renders with all children visible, no grips, no flags, no `=` glyphs; the tab keeps the `VIEW` treatment from foundation 7 |
| empty subnet frame | 320x120, centered text `--nb-text-dim` sans 12px: `Empty network. Drag nodes in, or press Tab inside it.` |

**Interactions and keys.**
- Drag the tab: moves the frame and every child. Drag the border: same. Drag inside empty frame space: marquee inside the frame (does not select the frame).
- Double-click the tab, or `I` / `Enter` with the frame selected: dive (S37).
- `X` or `Shift+X` with the frame selected: card view (S38). Children keep their relative positions for the next expand.
- Tab menu opened while the cursor is inside a frame places the new node inside that network (parent set), at the cursor.
- Marquee from outside that crosses a frame selects children it touches; it never selects the frame itself (Houdini rule: a network is selected by its own tab).
- `Cmd+A` inside a dived network selects that network's children only.
- Right-click the tab: the Group frame menu (foundation 6.4) for groups; for subnets: `Rename F2 · Dive I · Show as card X · ─ · Bypass B · ─ · Save as asset… · Promote parameter ▸ · ─ · Delete network (keeps nothing) ⌫ · Dissolve network (moves children up)`.

**Copy.**
- Empty frame: `Empty network. Drag nodes in, or press Tab inside it.`
- Toast after a drag-out that removed a wire: `Moved rsi out of long_leg. 1 wire had no route and was removed. Undo (⌘Z)`.
- Invalid cross-network wire (cursor tooltip after 400 ms): `Wires connect nodes in the same network. Route through a network port.`

**Accessibility.**
- The frame is a React Flow node with `role="group"` and `aria-label="Output group long_leg, long, AAPL 1d"` (or `Network regime, 4 nodes`). The tab is a `button` inside it (`aria-pressed` for selected). Tab order: tab button, then frame ports, then children in reading order.
- Keyboard-only path: Tab to the frame tab, `Enter` dives, `I` dives, `X` collapses, arrow keys nudge the frame and its children.

**Must not.**
- No `backdrop-filter`, no blur, no shadow on frames (foundation 8.1).
- Do not compute the frame's bounding box on every render. Recompute on node drag end, add, remove and resize end (subscribe to those store events), never in a render pass over all nodes.
- Do not store child positions as absolute coordinates. React Flow `parentId` children use positions relative to the parent; `rfMapping.ts` converts once in each direction.
- Do not render boundary nodes as cards inside a frame (they are the frame ports). Rendering both makes a wire appear to stop at a phantom node.
- Do not let the frame handle `onNodeDrag` for children through React state on pointer move (foundation 8.2). Use React Flow's own drag and re-fit on drag end.

**Acceptance checks.**
- `networkFrame.test.tsx`: an `output_group` with 4 children renders one frame, 4 child cards, N frame ports where N = number of `subnet_input` children, and zero boundary cards.
- Dragging a root node over the frame and releasing sets `parent` to the group id and converts the position to frame-relative coordinates (round trip through `rfMapping.ts` returns the same absolute position ±0.5).
- Connecting a root node's output to an inside node's port creates exactly: one `subnet_input`, one wire outside, one wire inside (store snapshot).
- Connecting two nodes inside two different groups is refused (`isValidConnection` false) and no node is created.
- Screenshot at 100% zoom: the frame tab is 26px tall; the frame border is 1px; the inner wire meets the frame port at the frame's top edge (y within 1px).
- The read-only regime auto-render shows the regime frame with its children (test from W5 "Tests to add").

---

## S32a. Output Group frame header (name, direction, primary ticker, capital weight)

**Purpose.** The header names the group and shows the four things a bot needs: its name, direction, primary ticker and share of capital. It is also where the user edits them.

**Placement.** The S31 tab of an Output Group frame, and the Inspector Parameters section when the group is selected.

**Anatomy (tab, left to right, 6px gaps).**
1. Glyph chip `O` (`--nb-cat-output` background, `--nb-text-on-color` glyph).
2. Name, sans 12px 600 `--nb-text`, inline-editable (double-click or `F2`; validation as foundation 4.2). Max 160px, ellipsis.
3. Direction pill: `LONG`, `SHORT`, or `SWITCH` (for `regime_switch`). Click opens a 3-row select popover (`--nb-bg-elevated`, radius 8, rows 28px) with the same pills and a one-line description each.
4. Primary ticker chip: `AAPL · 1d` in the ticker chip style. Click opens a popover listing every Ticker node in the graph (name, symbol, interval, and `primary of short_leg` when already used by another group) plus `+ New Ticker` at the bottom. Choosing sets `params.ticker` to the node's path.
5. Capital weight: mono 10px `--nb-text-muted`, text `1×` (or `0.5×`, `2×`). Click turns it into a 40px number field; scrub as a number row. Tooltip after 400 ms: `Capital weight 1 of 2 (50 % of initial capital)`.
6. Diagnostics badge, flag dots (bypass only; no display flag).

**Inspector Parameters (group selected).** Rows of 24px: `Name`, `Direction` (select), `Primary ticker` (path picker, showing `/aapl`), `Capital weight` (number, min 0, step 0.25, unit `×`), then a computed read-only row `Capital share` (`50 % · 5 000 of 10 000` from the sidebar capital and the weights of all groups). Under the rows, a `Terminals` list: one 20px line per terminal type in the fixed order `entry, exit, size, stop, trailing_stop, time_stop, regime` with a filled dot when present, a hollow dot when absent, the connected attribute in a read chip, and an `Add` link on absent ones. In `regime_switch` groups, `entry` and `exit` show two lines each (`long`, `short`).

**States.**

| State | Look |
|---|---|
| `regime_switch` direction | pill `SWITCH`; the terminals list shows entry/exit per side; the `regime` terminal line reads `regime · required` |
| missing primary ticker (`ticker_missing`) | ticker chip reads `no ticker ▾` in `--nb-error` on `rgba(248,113,113,0.16)`; error badge |
| the primary Ticker is also primary of another group | allowed; the ticker popover marks it `also primary of short_leg`; validate may warn about same symbol and direction at spawn time, not here |
| missing required terminal (`missing_terminal`) | error badge on the tab; inside the frame a ghost terminal (118x46, dashed 1px `rgba(248,113,113,0.5)`, label `+ exit` mono 11px `--nb-error`, centered) at the slot where it would go in the fixed order. Click adds the terminal. `Add missing terminals` in the context menu adds all. |
| duplicate terminal (`group_duplicate_terminal`) | both terminals get the error badge; the message names the other |
| weight 0 | weight reads `0×` in `--nb-warn`; tooltip `This group gets no capital and will not trade.` |
| only group in the graph (or implicit `main`) | weight shows `1×` and the capital share row reads `100 %`; the implicit group is drawn as a frame named `main` with a muted `implicit` tag in the tab and no editable name until the user clicks `Make explicit` in the Inspector (which writes a real `output_group` node) |

**Interactions and keys.**
- All edits go through `commit` and are undoable.
- Changing direction from `long` to `regime_switch` keeps the existing `entry`/`exit` as `side: long` and shows ghosts for the short side. Changing back drops the short-side terminals after a confirm: `Remove the short-side entry and exit? They are not needed for a long group.` Buttons `Remove` / `Keep as bypassed`.
- Changing the primary ticker re-labels every wire from the old Ticker that fed a terminal in this group; nothing else moves.

**Copy.**
- Direction popover rows: `LONG  Buy on entry, sell on exit`, `SHORT  Sell short on entry, cover on exit`, `SWITCH  Long or short by regime; one entry and exit per side`.
- Ticker popover title: `Primary ticker`; footer link `+ New Ticker`; row suffix `primary of short_leg`.
- Ghost terminal labels: `+ entry`, `+ exit`, `+ entry (long)`, `+ entry (short)`, `+ regime`.
- Tab tooltip on the weight: `Capital weight 1 of 2 (50 % of initial capital)`.

**Accessibility.**
- The pills are buttons with `aria-haspopup="listbox"`; the popovers are `role="listbox"` with `aria-activedescendant`. The weight field has `aria-label="Capital weight"`.
- Keyboard-only: focus the tab button, `Enter` opens the Inspector Parameters on the group (dive is `I`), then Tab through the rows.

**Must not.**
- Do not send `direction` from the sidebar in graph view; it is graph-owned (add it to `GRAPH_OWNED_FIELDS` in 5.E).
- Do not derive the primary ticker from wire order or the first Ticker in the file. It is `params.ticker` only.
- Do not let the weight field accept a negative or a non-number; keep the invalid state red and Run disabled (foundation 4.4).

**Acceptance checks.**
- Setting the direction pill to `SWITCH` writes `params.direction = "regime_switch"` and the terminals list shows 4 entry/exit lines.
- With no `exit`, a ghost card with the text `+ exit` renders inside the frame; clicking it adds an `exit` node with `parent` = the group id.
- Screenshot: tab shows `O long_leg LONG AAPL · 1d 1×` in that order, heights 16px for chip and pills.
- `groupResults.test.tsx` covers the capital share text `50 % · 5 000 of 10 000` for two equal weights and 10 000 capital.

---

## S32b. Terminals: Entry, Exit, Size, Stop, Trailing stop, Time stop, Regime

**Purpose.** Terminals are the last nodes in a group. Each one hands one thing to the simulator. They look alike so the row of them reads like a form.

**Placement.** Inside an Output Group frame (S31), laid out left to right in the fixed order `entry, exit, size, stop, trailing_stop, time_stop, regime`, 12px apart, on one row at frame creation. The user may move them.

**Anatomy.** Card 118px wide (foundation 4.1), white stripe (`--nb-cat-output`), header glyph `O`, name = terminal type (`entry`, `exit`, `size`, `stop`, `trailing`, `time stop`, `regime`; the node `name` stays the type name unless renamed), no type label (name equals type), one input port top center labeled `in`, no output port, no display flag, no bypass flag. Body per terminal:

| Terminal | Body (rows are foundation 4.4 rows; chips are foundation 4.5 chips) |
|---|---|
| `entry`, `exit` | one read chip = `params.signal` (`@go_long`). In a `regime_switch` group, the header name reads `entry · long` / `entry · short` (mono 10px suffix in the type slot) from `params.side`. |
| `size` | one row: label `size`, value `100 %` (from `constant`, shown as a percent of the 0.01 to 1.0 fraction; the field converts). When `params.value` (attr) is set instead, the row shows a read chip `@size_frac ▾` and the constant is hidden. |
| `stop` | one row: label `pct`, value `2.5 %` (from `constant`), or a read chip `@stop_pct ▾` when `params.value` is set. Value 0 or empty means no stop and the row shows `none` in `--nb-text-dim`. |
| `trailing_stop` | two rows (FA3): `pct  2.5 %`, `source  close ▾`. `activate_on_profit` and `activate_pct` live in the Inspector. When `activate_on_profit` is on, the type slot in the header reads `>+1.0 %` mono 10px `--nb-text-muted` (activation threshold at a glance). |
| `time_stop` | one row: `max bars  20`. |
| `regime` | one read chip = `params.signal` (`@spy_uptrend`) and one row `on flip  hold ▾` (select of `hold`, `close_only`, `close_and_reverse`). |

A `size` or `stop` row may be in code mode (S44): the row grows to 36px and shows `= np.clip(@size_scale, 0.1, 1.0)` in `--nb-code-expr`.

**States.**

| State | Look |
|---|---|
| unwired `entry`/`exit`/`regime` (`missing_input`) | chip reads `no input` in `--nb-error` with a dotted underline; error badge |
| `exit` reads an attribute that is never true in the cook (`exit_unconnected` warning) | amber badge; tooltip `exit never fires in this window` |
| unwired `size` or `stop` with a constant | no badge; the type slot reads `const` mono 10px `--nb-text-dim` |
| `size` above 1.0 as a fraction (`size_unit_suspect`) | amber badge; the value cell shows the raw fraction with a `?` suffix |
| `stop` on a `short` group | unchanged look; the Inspector description says `Triggers above entry for a short group.` |
| terminal outside a group in a graph that has groups (`group_terminal_outside`) | error badge; dashed 1px `--nb-error` border at 50%; tooltip `Move this terminal into a group.` Dragging it into a frame fixes it. |
| duplicate (`group_duplicate_terminal`) | error badge on both |
| stale (after a commit, before a cook) | nothing on the terminal; status bar carries it |

**Interactions and keys.**
- Dropping a wire on the card body connects to `in` (single input). A bool attribute is required for `entry`, `exit`, `regime`; the picker only offers bool attrs, free text allowed.
- `size` and `stop` switch between constant and attribute through the value cell: clicking the `▾` opens the attr picker with a first row `Constant…` that returns to the number field.
- Terminals cannot be bypassed, cannot take the display flag, cannot be collapsed (they are already minimal), cannot be copied out of a group (paste at root creates a `group_terminal_outside` error, which is allowed and flagged).
- Delete on a required terminal is allowed; the ghost appears at once (S32a).

**Copy.**
- Read chip placeholder when unwired: `no input`.
- Stop row empty value: `none`.
- Attr picker first row: `Constant…`.
- Inspector descriptions (one line each): `entry: Opens a position on the bar where the signal is true.` `exit: Closes the position on the bar where the signal is true.` `size: Share of the group's capital to use, 1 % to 100 %.` `stop: Fixed stop as a percent from the entry price. Triggers above entry for a short group.` `trailing_stop: Stop that follows the price. Starts at once, or after the position is up by the activation percent.` `time_stop: Closes the position after this many bars.` `regime: Trading is allowed only while the signal is true. On flip says what happens to an open position.`

**Accessibility.**
- Each terminal card is `role="group"` with `aria-label="exit terminal, reads @long_exit"`. The single input port is a `button` with `aria-label="input in"`.
- The `on flip` select is a native `<select>` styled as the row; keyboard arrows change it.

**Must not.**
- Do not show a display flag or bypass flag on a terminal (foundation 4.3, 4.6).
- Do not convert the size percent to a fraction in the store on each keystroke; convert on commit, and keep the store value as the fraction (0.01 to 1.0) so it equals what the backend reads.
- Do not compute "exit never fires" on the client. It comes from `/validate` or the cook diagnostics.
- Do not let the terminal read `params.side` when the group is not `regime_switch`; the field is hidden then.

**Acceptance checks.**
- A rendered `size` terminal with `constant: 1.0` shows `100 %`; typing `50` commits `constant: 0.5`.
- A `regime` terminal renders one chip and one select with 3 options in the order `hold, close_only, close_and_reverse`.
- The `trailing_stop` card shows exactly two rows; with `activate_on_profit: true, activate_pct: 1` the header type slot reads `>+1.0 %`.
- Screenshot: seven terminals in a frame at 100% zoom are each 118px wide with 12px gaps.
- Terminal versus rule parity tests from the plan pass unchanged by any frontend conversion (backend tests; the frontend test asserts the store value is the fraction, not the percent).

---

## S32c. Reference Ticker with prefix

**Purpose.** A second Ticker (MSFT for a pair, SPY for a regime) joins the primary's bar index and writes prefixed attributes so `@close` always means the primary.

**Placement.** The existing `TickerNode` card. A Ticker becomes a reference when no group names it as primary.

**Anatomy.** Card 200px (content-fit within foundation 4.1 limits), cyan stripe, glyph `T`, name = `Node.name` (`msft`), type slot reads `primary` in `--nb-cat-ticker` when the Ticker is the primary of at least one group (tooltip lists them: `primary of long_leg`), otherwise `ref` in `--nb-text-muted`. Rows:
- `symbol  MSFT` (mono, uppercase on display; stored as typed, uppercased on commit).
- `interval  1d ▾` (select).
- `prefix  msft` (reference tickers only; mono; validation `^[a-z_][a-z0-9_]{0,15}$`; default = lowercase symbol, written into params on first render as a reference so the file is explicit).
- Write chips: `+@msft_open +@msft_high +@msft_low +@msft_close +@msft_volume` (5 chips; foundation 4.5 overflow shows `+@msft_open +@msft_high … +2`). The primary shows plain `+@open … +@volume`.
- Alignment tag: when the reference interval is coarser than the primary's (for example `1d` under a `1h` primary), a 16px chip `HTF` in `--nb-tint-data` / `--nb-cat-data` sits after the interval value, tooltip: `Coarser bars are shifted one bar so no future value leaks.`

**States.**

| State | Look |
|---|---|
| primary of a group | type slot `primary`; no `prefix` row; chips unprefixed |
| reference | type slot `ref`; `prefix` row; chips prefixed |
| primary of one group and read by nodes of another | still `primary`; nothing special (its attributes are plain for everyone) |
| prefix clash (two references with the same prefix, `attr_clash` from validate) | both prefix values in `--nb-error`; badge; message names the other node |
| prefix empty or invalid | field red, Run disabled |
| symbol unknown to the provider (cook error from the server) | the banner from foundation 7 names the node; the node gets the error badge with the server message |
| becomes reference when its group is deleted | the `prefix` row appears with the default value and a 4 s toast: `msft is no longer a primary ticker. Its attributes are now @msft_close and so on.` Downstream readers of plain `@close` that came from this Ticker get `attr_missing` until re-pointed. |
| read-only graph | rows are label/value, no select arrows |

**Interactions and keys.**
- `symbol`, `interval` and `prefix` have no `=` glyph (D1; `code_able: false`).
- Renaming the prefix propagates through `rename_attr` to every downstream reader and updates wire labels (same mechanism as write-chip rename in foundation 4.5).
- Double-clicking a write chip on a Ticker does nothing (Ticker writes are fixed by the prefix; the chip tooltip says `Change the prefix to rename these.`).

**Copy.**
- Type slot: `primary`, `ref`.
- Tooltips: `primary of long_leg`, `Reference ticker. Its attributes carry the prefix.`
- HTF tooltip: `Coarser bars are shifted one bar so no future value leaks.`
- Chip tooltip: `Change the prefix to rename these.`

**Accessibility.** The card's `aria-label` reads `Ticker msft, reference, prefix msft` or `Ticker aapl, primary of long_leg`. The prefix input has `aria-describedby` pointing at a hidden hint `letters, digits and underscore`.

**Must not.**
- Do not fetch anything from the node; the frontend never checks a symbol. The server does at cook time.
- Do not decide "primary" from wire topology or file order. Only a group's `params.ticker` path makes a Ticker primary. With no explicit group (implicit `main`), the one Ticker at root that feeds the terminals is primary; if there are two candidates, validate reports `ticker_missing` and both show `ref` with a badge.
- Do not add a `source` row (FA4).

**Acceptance checks.**
- A graph with `aapl` primary of `long_leg` and `msft` unreferenced renders `primary` on aapl and `ref` plus a `prefix msft` row on msft, and msft's chips are `+@msft_*`.
- Changing the prefix to `m` re-labels the wire to a consumer that reads `@msft_close` to `@m_close` (store + edge label snapshot).
- A `1d` reference under a `1h` primary shows the `HTF` chip; the same interval shows none.
- The `prefix` input renders `type="text"` and no `=` glyph is in the DOM for the three Ticker rows.

---

## S33. Per-group results tabs and the Combined tab

**Purpose.** A pair strategy has two legs and one portfolio. The user flips between each leg's numbers and the combined view without leaving Results.

**Placement.** A group strip at the top of the graph result header (S30, W4) in `Results.tsx`, above the existing sub-tabs (Summary, Trades, and so on). The chart pane summary (foundation 3.1) follows the same selection.

**Anatomy.**
- Strip: 32px tall, `--nb-bg-panel`, 1px bottom `--nb-border`, padding `0 12px`, gap 6px, horizontally scrollable with hidden scrollbar when it overflows.
- Tabs are pills 24px tall, radius 12, sans 12px 500, padding `0 10px`, `--nb-text-muted` on transparent; active: `--nb-bg-active`, `--nb-text`, 1px `--nb-border-strong`. Order: `Combined` first (only when 2 or more groups), then one per group in file order. Each group pill: glyph `O` 12x12, name, direction pill (S32a style, 14px tall), ticker chip (`AAPL · 1d`), and the group's return `+18.2 %` in `--nb-ok` or `−4.1 %` in `--nb-error` (mono 11px). The Combined pill: `Σ Combined  +23.9 %`.
- Right end of the strip: `2 groups · 10 000 capital · exposure 61 %` mono 11px `--nb-text-muted` (Combined only).
- Below the strip, the existing sub-tabs render the selected result: a group tab feeds `GroupResult` (`summary`, `trades`, `equity_curve`); Combined feeds `CombinedResult.summary` and `equity_curve`, and a merged trades table with an extra first column `group` (name in a 10px pill).
- Combined Summary adds two stat tiles at the end of the existing tile row: `Exposure  61.2 %` (tooltip `Share of bars with any leg in a position`) and `Gross deployed  48.0 %` (tooltip `Average share of total capital in positions`). Tiles use the app's existing tile component and colors, not new ones.
- Rule-only sub-tabs (Optimizer, Walk-forward, Sensitivity) keep the W4 `Not available for graph results` state on every group tab.

**Chart pane behavior.**
- A group tab: the chart shows that group's primary ticker candles with that group's trade markers, and the group's equity curve in the equity sub-pane.
- Combined: the equity sub-pane shows the combined curve; the candle pane keeps the last chosen group's ticker (default the first group) and shows a 22px hint line at the top of the chart pane: `Candles: long_leg (AAPL). Markers: long_leg. Equity: combined.` in `--nb-text-muted` mono 11px. Group pills inside that hint are clickable and change the candle group without leaving Combined.
- The collapsed chart bar text (foundation 3.1) reads `long_leg · AAPL 1d · 14 trades · +18.2% · Sharpe 1.21 | combined +23.9% · MaxDD −6.1%`.

**States.**

| State | Look |
|---|---|
| one group (or implicit `main`) | no strip; the header reads as W4 S30 |
| two or more groups, first render | Combined active |
| stale (graph changed after the cook) | pills keep their numbers, prefixed by `stale ·` in `--nb-warn` on the active pill only |
| a group with zero trades | return reads `0 trades` in `--nb-text-dim` instead of a percent |
| a group with an open position at the end (`summary.open_position`) | a 6px `--nb-warn` dot after the name; tooltip `Open position at the end of the window` |
| cook failed for the graph | no strip; the W4 error state |
| result from an older rev than the current graph | same as stale |

**Interactions and keys.**
- Click a pill: sets `graphResult.displayedGroup` to the group name or `combined`. Arrow keys move between pills when the strip has focus. `Home` / `End` jump.
- Selecting a group pill also selects the group frame on the canvas (one-way: canvas selection never changes the tab).
- Double-click a group pill: frames that group on the canvas (`F` on it).

**Copy.**
- Pills: `Combined`, group names as in the graph.
- Tiles: `Exposure`, `Gross deployed`.
- Tooltips: `Share of bars with any leg in a position`, `Average share of total capital in positions`, `Open position at the end of the window`.
- Chart hint: `Candles: long_leg (AAPL). Markers: long_leg. Equity: combined.`
- Trades table extra column header: `group`.

**Accessibility.**
- The strip is `role="tablist"` with `aria-label="Result groups"`; pills are `role="tab"` with `aria-selected`; the panel under it is `role="tabpanel"`. Roving tabindex: only the active pill is in the tab order; arrows move.
- Return numbers carry `aria-label="return plus 18.2 percent"` so the sign is read out.

**Must not.**
- Never write a group or combined result into `lastRequest` or `backtestResult` (D10). `displayedGroup` lives in `graphResult` only.
- Do not build a second chart or a second Results component for groups. The strip changes the prop that Results and Chart already take.
- Do not unmount sub-panels on tab switch; keep the `display: 'none'` pattern from Results (F152). The group strip switch may re-render the table, not remount the panel tree.
- Do not compute exposure or gross deployed on the client. They arrive in `combined.summary`.
- Do not call `onTickerChange` to switch the candle symbol (it clears `backtestResult`); set the chart view state directly as W4 does.

**Acceptance checks.**
- `groupResults.test.tsx`: with `groups.length === 2` the strip renders 3 tabs in the order `Combined, long_leg, short_leg`; with 1 group it renders none.
- Clicking `short_leg` sets `displayedGroup === 'short_leg'` and the Summary panel shows that group's `total_return_pct`.
- Combined Summary renders tiles labeled `Exposure` and `Gross deployed` with `61.2 %` and `48.0 %` from the fixture.
- The merged trades table for Combined has a first column `group` and its row count equals the sum of the groups' trades.
- A snapshot of `lastRequest` before and after switching tabs is identical.

---

## S34. Spawn bots dialog

**Purpose.** Turn a saved graph into one stopped paper-trading bot per group, with capital, broker and data source per leg, in one all-or-nothing call.

**Placement.** Opened by the toolbar `Spawn bots…` button (foundation 3.1) or the command `spawnBots`. Width 720px, max height 80% of the viewport, centered in the app window (not the canvas column).

**Anatomy (top to bottom).**
1. Title row: `Spawn bots from pair_aapl_msft` (name in mono), right: `rev 7` mono 11px `--nb-text-muted`, close `✕`.
2. Notice line (sans 12px `--nb-text-secondary`, 32px): `Every bot is created stopped. Start them from the Trading tab when you are ready.`
3. Legs table. Header row 28px caps (`--nb-caps`): `Group · Bot name · Capital · Broker · Data source · Interval`. One row per Output Group, 40px tall, `--nb-border-subtle` separators:
   - Checkbox (12x12) to include the leg. Default on.
   - Group: glyph `O`, name (sans 12px 600), direction pill, ticker chip on a second line (`AAPL · 1d`), total width 180px.
   - Bot name: text input 160px, mono 12px, default `pair_aapl_msft ▸ long_leg` (`strategy_name`; empty sends `null` so the server default applies).
   - Capital: number input 96px, unit suffix from the sidebar currency (plain number, no symbol), default = `initial_capital * weight / sum(weights)` rounded to whole units, `inputMode="decimal"`.
   - Broker: select 96px, options `Alpaca` (`alpaca`), `IBKR` (`ibkr`). Default: the last used broker in this browser (`localStorage['nb.spawn.broker']`) else `alpaca`.
   - Data source: select 112px, options `Yahoo` (`yahoo`), `Alpaca SIP` (`alpaca`), `Alpaca IEX` (`alpaca-iex`), `IBKR` (`ibkr`). Default `alpaca-iex` for Alpaca, `ibkr` for IBKR (changes when the broker changes unless the user touched it).
   - Interval: select 72px, first option `graph (1d)` (sends `null`), then the standard interval list (`interval_override`).
4. Footer left (mono 11px `--nb-text-muted`): `2 bots · 10 000 total capital`. Footer right: `Cancel`, `Create 2 bots` (primary).

**States.**

| State | Look |
|---|---|
| graph has unsaved changes | the toolbar button is enabled but the dialog opens with a 32px `--nb-warn` tint bar: `Save the graph first. Bots pin a saved revision.` with a `Save now` button; the table is disabled (opacity 0.45) until saved |
| graph never saved | same bar, text `Save the graph first.` |
| validation errors in the graph | the bar reads `Fix 2 errors before spawning.` with `Show diagnostics`; primary disabled |
| no Output Group and no implicit main | body reads `This graph has no Output Group, so there is nothing to spawn.`; primary disabled |
| one group only | one row; footer `1 bot · 10 000 total capital`; primary `Create 1 bot` |
| all legs unchecked | primary disabled, `title="Pick at least one group"` |
| capital empty, 0 or not a number | red field; primary disabled; hint under the table `Capital must be a number above 0.` |
| two checked legs with the same symbol and direction | both group cells get an amber left bar and a hint `long_leg and long_leg_2 trade AAPL long. The server refuses this.`; primary stays enabled so the server message shows (the client hint is a courtesy, the server is the guard) |
| submitting | primary shows a 12px spinner and `Creating…`; inputs disabled; Esc ignored |
| 201 | dialog closes; toast `Created 2 stopped bots. Open Trading` (link switches to the Trading tab and scrolls to the first bot card) |
| 409 `rev_conflict` | error bar `The graph was saved elsewhere (rev 8). Reload it, then spawn again.` with `Reload graph` |
| 400 `group_unknown` | error bar with the server message; the named row gets the error left bar |
| 400 `same_symbol_same_direction` | error bar with the server message; matching rows marked |
| 400 `graph_invalid` | error bar `The graph has errors.` + `Show diagnostics` |
| 400 `code_disabled` | error bar `Code nodes are disabled on this server, so this graph cannot run as a bot.` |
| network failure | error bar `Could not reach the server. Retry` |

**Interactions and keys.**
- `Cmd+Enter` submits. `Esc` cancels (except while submitting). Tab moves across a row then down.
- Changing the broker resets the data source default only if the user has not edited that leg's source.
- The capital fields do not re-split when one is edited (no auto-balance). The footer total updates.
- The dialog remembers broker and data source per browser, never capital or names.
- After 201 the legs' settings are not written to the graph.

**Copy.** All strings above. Also: header `Group`, `Bot name`, `Capital`, `Broker`, `Data source`, `Interval`; interval first option `graph (1d)` where `1d` is the group's interval.

**Accessibility.**
- Dialog rules from the shared section. The legs table is a real `<table>` with `<th scope="col">`. Each row's checkbox has `aria-label="Include long_leg"`. The error bar is `role="alert"`.
- Keyboard-only: open with the toolbar button, Tab through the first row, `Cmd+Enter` to submit; the toast link is focusable.

**Must not.**
- Do not send one request per leg. One `spawnBots(graphId, rev, legs)` call with every checked leg (the backend makes it all-or-nothing).
- Do not post `graph` JSON from the client; the server loads the saved revision by `rev`. That is why unsaved changes block the dialog.
- Do not invent a client-side symbol guard beyond the hint. The server owns `same_symbol_same_direction` and the exclusive-symbol guard.
- Do not create bots as running. There is no "start now" checkbox.
- Do not reuse `AddBotBar`'s form for this dialog; it posts a different shape.

**Acceptance checks.**
- `spawnDialog.test.tsx`: a graph with two groups and sidebar capital 10 000 renders two rows with capital `5 000` each and the footer `2 bots · 10 000 total capital`.
- Unchecking one row changes the primary label to `Create 1 bot` and the request body has one leg.
- The request body matches the contract: `{rev, legs: [{group, allocated_capital, broker, data_source, interval_override, strategy_name}]}` with `interval_override: null` when `graph (1d)` is selected and `strategy_name: null` when the name field equals the default.
- A mocked 409 shows the `rev_conflict` text and a `Reload graph` button; a mocked 201 closes the dialog and shows the toast.
- With `dirty === true` the table is disabled and the `Save now` bar renders.
- The capital input renders `type="text" inputMode="decimal"`.

---

## S35. BotCard graph line and "Update to latest rev"

**Purpose.** A graph bot shows which graph, which group and which revision it runs, and offers a one-click update when the graph moved on, with clear refusals.

**Placement.** `frontend/src/features/trading/BotCard.tsx`, directly under the existing line `AAPL · 1d · alpaca-iex · via Alpaca` (the one that carries the `via Graph` badge). Rendered only when `summary.kind === 'graph'`.

**Anatomy.** One 20px line, mono 11px, gap 6px:
1. A 12x12 glyph `O` in `--nb-cat-output` on `--nb-tint-output`, radius 3.
2. `pair_aapl_msft ▸ long_leg` : graph name (link, `--gh-text-primary`, hover underline, opens the graph in the node builder and selects the group frame), `▸`, group name.
3. `@ rev 7` in `--gh-text-faint`.
4. Direction mode tag when `graph_direction_mode === 'regime_switch'`: pill `SWITCH`.
5. Right-aligned: the update affordance (see states). Button 22px tall (BotCard is denser than the node builder), sans 11px, using the card's existing button styles.

The `via Graph` badge stays as it is.

**How the card learns the latest rev (orchestrator decision, 2026-09-30).** The bot summary from `GET /api/bots` carries `graph_name` and `graph_latest_rev` (plan item 5.D). BotCard reads them from its summary. Nothing fetches a graph for the Trading view: not per card, and not per graph id.

**States.**

| State | Right side | Notes |
|---|---|---|
| bot rev equals latest | nothing (or `latest` in `--gh-text-faint` on hover of the line) | |
| latest rev is newer, bot stopped, no position | button `Update to rev 9` | primary tone |
| latest newer, bot running, no position | same button; on click a confirm inline: `Update while running? The bot uses the new rules on its next tick.` `Update` / `Cancel` | |
| latest newer, in position | button disabled, `title="Close the position first"`, plus a faint `rev 9 available` | matches the 409 `in_position` guard |
| updating | button shows spinner + `Updating…` | |
| 200 | line shows `@ rev 9`, toast `long_leg updated to rev 9` | |
| 409 `in_position` | inline error text under the line in `--gh-red-alt`: `Close the position first.` | |
| 409 `rev_conflict` | `The graph changed again (rev 10). Try once more.` and the button relabels | |
| 400 `symbol_changed` | `In rev 9 this group trades a different symbol. Spawn a new bot instead.` with link `Spawn…` (opens the graph and the Spawn dialog) | the update button hides |
| 400 `direction_changed` | `In rev 9 this group changed direction. Spawn a new bot instead.` + `Spawn…` | button hides |
| 400 `group_missing` | `Group long_leg no longer exists in rev 9.` | button hides |
| 400 `graph_invalid` | `Rev 9 has errors. Fix them in the node builder.` link opens the graph | |
| graph deleted (`graph_id` set, `graph_latest_rev` null) | the graph name is plain text, not a link, followed by `(graph deleted)` in `--gh-text-faint`; no button | the bot keeps running on its snapshot |
| `pause_reason` present (any) | the existing pause-reason row shows it (S49 adds the code-disabled text) | |
| legacy graph bot without `graph_id` | line reads `graph snapshot · no source graph` in faint; no button | |

**Interactions and keys.** Click the graph name: navigates to the node builder with that graph open (uses the graph id; if the app has unsaved work in another graph, the normal `Save changes to <name>?` prompt appears first). The update button is a normal button; `Enter` / `Space` activate it.

**Copy.** All strings above. Button labels: `Update to rev 9`, `Updating…`, `Update`, `Cancel`, `Spawn…`.

**Accessibility.** The line is a `<div>` with the graph link as `<a>` (`aria-label="Open graph pair_aapl_msft at group long_leg"`). Errors under the line are `role="alert"`. The disabled update button keeps focusability (`aria-disabled="true"`) so the tooltip is reachable by keyboard.

**Must not.**
- Do not call the generic bot PATCH to update the graph; use `updateBotGraph(botId, graphId, rev)` which hits `POST /api/bots/{id}/graph_update` (D7).
- Do not fetch the graph at all from the Trading view (not per card, per render, per graph id or on an interval). The latest rev and name come from the bot summary.
- Do not let the card try to compute whether symbol or direction changed. It sends the update and renders the server's refusal.
- Do not touch the existing `regime_direction` block or the pause-reason row; add the line between them.

**Acceptance checks.**
- A graph bot summary with `graph_id, graph_name: 'pair_aapl_msft', graph_group: 'long_leg', graph_rev: 7, graph_latest_rev: 9` renders the text `pair_aapl_msft ▸ long_leg @ rev 7` and a button `Update to rev 9`, and no `GET /api/graphs/{id}` request is made.
- With `in_position: true` the button is disabled with `title="Close the position first"`.
- A mocked 400 `symbol_changed` hides the button and renders the sentence containing `Spawn a new bot instead.`
- With `graph_latest_rev: 7` no button renders.
- With `graph_id` set and `graph_latest_rev: null` the line shows `(graph deleted)` and no button.
- A summary with `kind: 'rule'` renders none of this.

---

## S36. AddBotBar group selector

**Purpose.** When a user picks a graph in the AddBotBar's graph mode, a graph with several groups needs a group choice; otherwise the bot would not know which leg to run.

**Placement.** `AddBotBar.tsx`, graph mode, immediately right of the graph `<select>` (the one whose placeholder is `Select graph…`).

**Anatomy.** A `<select>` 160px, same style as the graph select. Options: one per Output Group: `long_leg · LONG · AAPL 1d`. Hidden when the chosen graph has one group or only the implicit `main`. Helper text under the bar when hidden: none. When shown, a 11px hint right of it: `one bot per group` in `--gh-text-faint`.

**Data.** The graph list comes from the API (S06). Choosing a graph fetches `GET /api/graphs/{id}` (once, cached in component state by id) and reads the groups from `graph.nodes` (type `output_group`). The selected group is sent as `graph_group` with `graph_id` and `graph_rev` on the bot POST, and `symbol`/`direction` are filled from the group so the existing fields show the right values read-only (the symbol field reads `from graph` in faint).

**States.**

| State | Look |
|---|---|
| no graph chosen | select hidden |
| loading the graph | select shows one disabled option `loading…` |
| one group | hidden; symbol and direction fields fill from that group |
| several groups | shown; first group preselected |
| graph fetch fails | inline error under the bar with the `detail` text and `Retry` |
| graph has errors (the envelope's saved graph fails `/validate`) | the Create button is disabled with `title="Fix the graph's errors first"`; the AddBotBar does not run validate itself, it reads `diagnostics` returned by the bot POST 400 and shows the first message |

**Interactions.** Changing the group refills symbol and direction. `Esc` on the bar behaves as today.

**Copy.** Option format `long_leg · LONG · AAPL 1d`; hint `one bot per group`; symbol placeholder `from graph`.

**Accessibility.** `aria-label="Output group"` on the select. The read-only symbol field is `readOnly` (not `disabled`) so it stays readable by screen readers.

**Must not.**
- Do not post the graph JSON from localStorage anymore. The bar posts `graph_id`, `graph_rev`, `graph_group` and lets the server load the revision (the same path as spawn). If the backend keeps accepting an inline `graph` for legacy reasons, the bar does not use it.
- Do not read `strategylab-saved-graphs` from localStorage (removed in W1 S06).

**Acceptance checks.**
- With a two-group graph selected, the select renders two options in file order and the POST body carries `graph_group` equal to the selected option.
- With a one-group graph the select is not in the DOM and the symbol field shows the group's symbol.
- The POST body contains no `graph` key.

---

# Wave 6

## S37. Breadcrumb and dive / up navigation

**Purpose.** Moving into a network and back out must feel like Houdini's network editor: `I` or double-click dives, `U` climbs, the breadcrumb always says where you are.

**Placement.** Breadcrumb in the toolbar as foundation 3.1. Dive changes what the canvas shows.

**Anatomy.** As foundation 3.1: first crumb = graph name (root), `›`, one crumb per level; mono 12px; current crumb `--nb-text`, others `--nb-text-muted`; unsaved dot and `rev N` after the crumbs. Each crumb is a button 24px tall with 4px horizontal padding and radius 3; hover `--nb-bg-hover` and underline. A 16px glyph chip before a network crumb (`O` for a group, `N` for a subnet, `R` for regime, a lock glyph 10px after the name for a locked asset instance).

**What the canvas shows at each level.**
- Root: root children. Network children render per their view (frame or card).
- Inside network X: X's children only. Boundary nodes render as half-height cards (S38): `subnet_input` cards along the top, in port order, 24px below the top of the framed area; `subnet_output` at the bottom. The frame border itself is not drawn when dived; instead a 1px inset outline in the network color at 30% alpha around the whole canvas pane (4px inset) says "you are inside", and the status bar selection slot reads `in /long_leg/regime`.
- Per-network viewport: the store keeps `viewports[networkId] = {x, y, zoom}` for the session and in `localStorage['nb.viewports.<graphId>']`. First visit frames all (`H`). Going up restores the parent viewport.

**Transitions.** Dive and up are immediate (no zoom animation; `--nb-motion-base` fade of the node layer at most). Selection is cleared on dive; on up, the network you left becomes the selected node (Houdini behavior) and is framed if outside the viewport.

**States.**

| State | Look |
|---|---|
| at root | first crumb is current; `U` does nothing; `Go up` absent from the pane menu |
| deeper than 4 levels | middle crumbs collapse to `…` (a button that opens a dropdown of the hidden crumbs) |
| network deleted while inside it (undo/redo or a collaborator's reload) | the editor climbs to the nearest existing ancestor and shows a toast `regime no longer exists. Moved up to long_leg.` |
| locked asset instance | the crumb has the lock glyph; inside, nodes are read-only (see S38 locked state) and the canvas outline is dashed |
| read-only graph | crumbs work; nothing else changes |
| width limit reached (40% of toolbar) | leftmost crumbs get the ellipsis first; the current crumb is never truncated below 80px |

**Interactions and keys.**
- `I` or `Enter` with one network node selected: dive. With several selected: dives into the primary one. With a non-network selected: nothing.
- Double-click a frame tab or a subnet card header: dive.
- `U`: up one level. `Shift+U`: to root.
- Click a crumb: go to that level. Right-click a crumb: `Open in place` (dive) and `Frame in parent` (go up to its parent and frame it).
- `Backspace` never navigates (it deletes).
- Breadcrumb crumbs are in the tab order after the toolbar's left group; arrows move between crumbs when one has focus.

**Copy.**
- Status bar slot: `in /long_leg/regime`.
- Toast: `regime no longer exists. Moved up to long_leg.`
- Crumb tooltips: the full path in mono, `/long_leg/regime`.
- Crumb menu: `Open in place`, `Frame in parent`.

**Accessibility.** The breadcrumb is `<nav aria-label="Network path">` with an ordered list; the current crumb has `aria-current="location"`. The canvas pane gets `aria-label="Network /long_leg/regime"` that updates on dive so the change is announced.

**Must not.**
- Do not filter nodes by walking the whole node map on every render. Keep a derived index `childrenByParent` in the store, updated on commit.
- Do not remount the React Flow instance on dive (it drops the viewport and triggers a full re-layout). Swap the nodes and edges arrays and set the viewport.
- Do not keep boundary cards and frame ports visible at the same time (S31 rule).
- Do not animate the viewport on dive (foundation 8.9 whitelist).

**Acceptance checks.**
- `dive.test.tsx`: with the fixture `/long_leg/regime`, after `I` on the regime node the breadcrumb renders 3 crumbs, the last with `aria-current="location"`, and the canvas nodes are exactly regime's children plus its boundary nodes.
- `U` restores the parent's node list and the stored viewport (spy on `setViewport` with the saved values).
- After dive, the previously selected node is not selected; after up, the network node is selected.
- Screenshot inside a network: a 1px inset outline in the network color is visible 4px from the pane edge; the status bar reads `in /long_leg/regime`.

---

## S38. Collapsed subnet node and boundary nodes

**Purpose.** A subnet as one card tells you what goes in, what comes out, and what you can tune, without opening it. Boundary nodes inside make the in and out points explicit.

**Placement.** Canvas. The card replaces the frame when `Node.meta.view === "card"` (FA1). Boundary nodes appear only when dived into the network (S37).

**Subnet card anatomy** (foundation 4.10, made exact).
- Width content-fit, min 200, max 320. Networks stripe (3px `--nb-cat-network`) plus the second 2px stripe at `left: 4px` (`.stripe2` in the prototype).
- Header (26px, padding-left 14px): glyph `N` in `--nb-cat-network`; name; type slot reads `subnet · 7 nodes` mono 10px `--nb-text-muted`, or for an asset instance `regime_filter @ v3` (mono 10px, `--nb-cat-rules` when the asset has a Rules palette entry, else `--nb-text-muted`) followed by a 10px lock glyph when `locked`; badge; collapse chevron (which for network nodes toggles frame view, FA1).
- Ports: input ports = one per `subnet_input` child, in port order, labeled with the boundary node's name (`in0`, or a renamed `signal`); one output when a `subnet_output` exists. Rings in `--nb-cat-network`.
- Body: promoted params as ordinary rows (S40), max 4 on the node, the rest in the Inspector with a `+3 more in Inspector` line (mono 10px `--nb-text-dim`) when there are more. Then interface chips on two lines: reads (grey, from the declared or derived interface) then writes (tinted `--nb-tint-network` / `--nb-cat-network`, or the asset's palette category tint when it has one).
- Sparkline slot: the first write, as any node.

**Boundary node anatomy** (inside a dived network only).
- Half-height card, 26px tall, 160px wide, dashed 1px border in `--nb-cat-network` at 60%, background `--nb-bg-node-bottom`, radius 6, stripe 3px `--nb-cat-network`.
- `subnet_input`: glyph `▽` 10px (mono, `--nb-cat-network`), name (`in0`, editable), type slot `input · 5 attrs` (count of attributes arriving on that port, from `/validate` streams), one **output** port at the bottom center, no input port.
- `subnet_output`: glyph `△`, name `out` (not editable; there is exactly one), type slot `output · 7 attrs`, one **input** port at the top center, no output.
- Hover on the type slot shows the stream tooltip (foundation 5.2) with the attributes.
- Boundary nodes have no flags, no params, no `=` glyphs. They can be moved. Deleting a `subnet_input` deletes the outside wire into that port and re-indexes the remaining inputs (`in0..inN` names are renumbered only when they still have default names).

**States.**

| State | Card | Notes |
|---|---|---|
| default | as above | |
| inline subnet (not an asset) | type slot `subnet · 7 nodes` | |
| locked asset instance | type `regime_filter @ v3 🔒`; the second stripe is dashed; promoted rows editable; interface chips read-only; inside (dived) every node is read-only with the read-only treatment from foundation 7 and a 22px bar under the toolbar: `Locked asset regime_filter v3. Unlock to edit a local copy.` with `Unlock` | |
| unlocked local copy | type `regime_filter v3 · local copy` muted | the Inspector offers `Re-lock to v3` only if the contents equal the asset (the backend answers; the frontend shows the option when `asset_ref` is present and `locked` is false) |
| newer asset version exists | a 6px `--nb-cat-network` dot after the version; Inspector row `v4 available · Update` | |
| `asset_missing` | error badge; type slot `regime_filter @ v3 · missing` in `--nb-error`; the card's body shows the promoted rows greyed and chips as last known; Inspector shows `This asset version was deleted from the library. The graph cannot cook until you replace it or unlock a local copy (not possible: the definition is gone). Remove the node or restore the asset.` | bots are immune (baked in) |
| `asset_interface_mismatch` | error badge on the chip that mismatches (red text, dotted underline) | |
| `promoted_target_missing` | the promoted row's label in `--nb-error` with strikethrough; tooltip names the target path | |
| bypassed | as foundation 4.9; output stream = `in0` | |
| no `subnet_output` | no output port; type slot adds `· no output` in `--nb-warn`; the Inspector Stream section says `This network has no output. Add a Subnet output inside it.` | |
| empty subnet | type slot `subnet · empty`; body shows the `+3 more` line replaced by `Dive in (I) to add nodes` in `--nb-text-dim` | |
| read-only graph | no editing, no flags | |

**Interactions and keys.**
- Double-click header or `I`: dive. `X` / `Shift+X`: frame view.
- Dragging a wire to the card's spare port (dashed, after the last input; visible on hover for subnets) creates a new `subnet_input` inside and wires it. Dragging from the output port when there is no `subnet_output` is not possible (no port is drawn).
- Rename a boundary input inline (double-click its name); the frame port label and the card's port label follow.
- Promoted rows edit like any row, including `=` (S44), except on a locked instance where `=` is allowed too (the expression lives in the instance's `params`, not in the asset).
- Context menu additions on the card: `Unlock (make a local copy)` / `Re-lock to v3`, `Update to v4`, `Open in Asset Manager`.
- Tab menu inside a network: two extra rows under Networks: `Subnet input  Adds an input port to this network` and `Subnet output  Adds the output port (one per network)`; the output row is disabled with `already present` when one exists.

**Copy.**
- Type slots: `subnet · 7 nodes`, `subnet · empty`, `regime_filter @ v3`, `regime_filter v3 · local copy`, `regime_filter @ v3 · missing`, suffix `· no output`.
- Boundary type slots: `input · 5 attrs`, `output · 7 attrs`.
- Locked bar: `Locked asset regime_filter v3. Unlock to edit a local copy.` Button `Unlock`.
- Unlock confirm: `Unlock regime_filter v3? This node keeps a local copy and stops following the library.` `Unlock` / `Cancel`.
- Missing asset Inspector text as in the table.
- Tab menu rows: `Subnet input`, `Adds an input port to this network`, `Subnet output`, `Adds the output port (one per network)`, disabled suffix `already present`.

**Accessibility.**
- Card `aria-label="Subnet momentum_confirm, 6 nodes, 1 input, 1 output"` or `Asset regime_filter version 3, locked`. Boundary cards `aria-label="Network input in0, 5 attributes"`.
- Inline rename fields are labelled `Rename input`.

**Must not.**
- Do not count children by scanning all nodes on each render; use `childrenByParent` from the store (S37).
- Do not store a locked instance's children in the graph; the card and dive read them from the asset file fetched through `graphLibrary.ts` (cached by `name@version`).
- Do not allow editing inside a locked instance through any path (rename, param, wire, flag); the store rejects commits whose target has a locked ancestor and shows the locked bar.
- Do not renumber boundary input names that the user renamed.

**Acceptance checks.**
- A subnet card with 2 `subnet_input` children and 1 `subnet_output` renders 2 input handles labeled with the boundary names and 1 output handle.
- A locked instance renders the lock glyph and `regime_filter @ v3`, and a param commit on a child inside it is rejected by the store (state unchanged, notice emitted).
- Inside a dived subnet, boundary cards are 26px tall (computed style) and a `subnet_output` card has exactly one handle of type `target`.
- With `asset_missing` in diagnostics the type slot contains `missing` and the card has the error border color.
- Screenshot: the double stripe (3px + 2px) is visible on the subnet card; a plain node has one stripe.

---

## S39. Collapse selection into subnet (Shift+C)

**Purpose.** Turn a handful of nodes into one folder in one keystroke, with the wiring taken care of.

**Placement.** Command `collapseIntoSubnet` (`Shift+C`), node context menu `Collapse into subnet ⇧C`, and the multi-selection Inspector bulk actions.

**Preconditions (checked before anything changes).**
- At least one node selected. All selected nodes share the same `parent`.
- The selection contains no terminal, no Output Group frame, no boundary node, no locked-asset descendant. Tickers, subnets (nested), boxes and notes fully inside the selection are allowed (boxes and notes move with the nodes and get the new parent).

**What happens (one undoable commit).**
1. A new node of type `subnet`, name `subnet1` (first free of `subnet1..subnetN` among siblings), `parent` = the selection's parent, `meta.view = "card"`, placed at the selection's bounding-box center.
2. Selected nodes get `parent` = the new subnet id; positions become relative to the subnet's frame origin (the bounding box top-left minus 24px, plus 48px at the top for the boundary row).
3. For each distinct outside source that fed a selected node: one `subnet_input` named `in0..inN` (in the order of the source nodes' x position), one outside wire into the subnet's new port, one inside wire from the boundary to each former consumer port. A source that fed several inside nodes gets one boundary.
4. For inside nodes that fed outside consumers: if exactly one inside node feeds outside, one `subnet_output` wired from it; every former outside consumer now reads from the subnet's output. If two or more inside nodes feed outside, the op inserts a `merge` node (Math & Signal) fed by all of them, then the `subnet_output` after it. Outside consumers keep their `attr` params, so their reads still resolve by name through the merged stream. If the merge creates a clash, `/validate` reports `attr_clash` in the normal way.
5. The new subnet is selected and its inline rename opens with `subnet1` selected, so typing replaces the name at once (Houdini behavior). `Esc` keeps `subnet1`.
6. Wires between selected nodes are untouched. A wire from a selected node to a terminal (outside, since terminals cannot be selected here) is routed as in step 4.

**States and feedback.**

| Case | Feedback |
|---|---|
| success | toast `Collapsed 6 nodes into subnet1 · 2 inputs · 1 output` (add ` (merged from 2 nodes)` when a merge was inserted). Undo (`⌘Z`) restores the exact previous graph. |
| nothing selected | status bar flash `Select nodes to collapse` (2 s, `--nb-warn`) |
| mixed parents | toast `Select nodes in one network to collapse them.` |
| selection contains a terminal or group | toast `Terminals and groups cannot go inside a subnet.` |
| selection is inside a locked asset | toast `This asset is locked. Unlock it first.` |
| selection is the whole content of a network | allowed; the parent network then holds one subnet |

**Interactions and keys.** `Shift+C` in canvas scope. After the op the focus is in the rename field; `Enter` commits and returns focus to the canvas root.

**Copy.** As in the table. Default name `subnet1`. Merge node name `merge_out`. Boundary names `in0`, `in1`, …, `out`.

**Accessibility.** The toast is `role="status"`. The rename field is announced as `Name for the new subnet`.

**Must not.**
- Do not perform the op as several commits (it must be one undo step).
- Do not rewire consumer `attr` params. Reads are by name; only wires change.
- Do not run the op through React Flow's node change handlers; it is a store operation (`operations/collapse.ts`) that produces the next graph and one `commit`.
- Do not drop boxes or notes inside the selection; move them with the nodes (set their `parent`).

**Acceptance checks.**
- `collapse.test.ts`: fixture of 4 root nodes A→B→C→D with B and C selected produces: subnet with children B, C; one `subnet_input` wired A→subnet→B; one `subnet_output` wired C→out and subnet→D; wire B→C unchanged; every consumer `attr` param unchanged.
- Two inside nodes feeding outside produce one `merge_out` node and one `subnet_output`, and the outside consumers' wires point at the subnet.
- Undo after collapse yields a graph deep-equal to the fixture (including positions).
- A selection that includes a terminal leaves the graph unchanged and emits the notice text.
- Compile of the collapsed graph equals compile of the flat fixture (backend `test_flatten.py` covers this; the frontend test asserts the JSON shape the backend expects: `parent` set, boundary types, `to_port` ids).

---

## S40. Promote a parameter, and promoted params on the subnet node and in the Inspector

**Purpose.** Expose one inner setting on the folder so the user tunes it from outside, as a Houdini promoted parameter.

**Placement.** Param row context menu (node and Inspector) inside any network: `Promote to parent…`. Promoted rows show on the subnet card (S38) and in the Inspector when the subnet is selected.

**Promote popover (anchored to the row, 260px, `--nb-bg-elevated`, radius 8).**
- Title 28px: `Promote period to momentum_confirm`.
- `Label` text input (default: the param's label, `Period`).
- `Name` mono input (default: `<node>_<param>` sanitized, e.g. `rsi_period`; validation `^[a-z_][a-z0-9_]{0,63}$`, unique among the subnet's promoted names and its params). Hint under it: `Used in ch("../rsi_period") and in the file.`
- Footer: `Cancel`, `Promote` (primary). `Enter` promotes.
- After promoting: the subnet gains `promoted: [{name, label, target: "rsi/period", type, default: <current value>}]` and `params[name] = <current value>`; the child's param value is replaced by a reference the backend resolves in `flatten` (the frontend stores nothing special on the child; the promoted list on the parent is the source of truth).

**Child row after promotion (inside the network).**
- Gutter shows a 10px `↑` glyph in `--nb-cat-network` (persistent, not hover-only).
- Value cell shows the parent's current value in `--nb-text-secondary`, not editable; click does nothing; hover tooltip `Promoted to ../rsi_period. Edit it on the subnet.` with a link `Go to subnet` (goes up and selects it).
- Context menu on the row: `Unpromote` (copies the current parent value back to the child and removes the promoted entry), `Go to promoted parameter`.
- The `=` glyph is hidden on a promoted child row (the expression belongs on the parent).

**Subnet card rows.** Promoted params render as ordinary rows in `promoted` order, label = promoted `label`, control by `type` (number/int/select/bool/attr/path as foundation 4.4), `=` toggle available (S44), scrub available. On the node: max 4; more go to the Inspector.

**Inspector, subnet selected, Parameters section.**
- A sub-title row `Promoted` (caps) with a count. Each promoted param is a 24px row: a 12px drag grip (`⋮⋮`, `--nb-text-dim`, cursor `grab`) for reordering, the label, the control, the reset-to-default dot, and an overflow `⋯` (28x24) with `Rename label…`, `Rename name…`, `Go to target` (dives and selects the child row), `Unpromote`.
- Under the list: `+ Promote a parameter…` link that opens a tree picker of the network's children and their params (two-level list; picking one opens the popover above prefilled).
- A locked asset instance: same rows, values editable, but no grip, no overflow, and the `+ Promote` link is absent; a muted line `Defined by regime_filter v3` sits under the sub-title.

**States.**

| State | Look |
|---|---|
| target missing (`promoted_target_missing`, the child was deleted or renamed outside the rename path) | the row label in `--nb-error` with strikethrough; tooltip `Target rsi/period no longer exists.`; overflow offers `Remove` and `Retarget…` |
| value out of the child's `min`/`max` (the promoted row inherits them) | red field as foundation 4.4 |
| promoted param used by a `ch()` somewhere and then unpromoted | `/validate` returns `ref_broken` on the reader; the unpromote confirm warns first: `spread_z reads ch("../rsi_period"). Unpromote anyway?` |
| a child param with an expression (`{"expr"}`) is promoted | allowed; the expression moves to the parent row (code mode on the parent), the child shows the promoted state |
| rename of the child node | `rename_node` rewrites `target`; nothing for the UI to do except re-render |
| more than 4 promoted | node shows 4 + `+N more in Inspector` |

**Interactions and keys.**
- Right-click a row → `Promote to parent…`. Keyboard: with the row's control focused, `Shift+F10` opens the row menu (standard), arrows to `Promote to parent…`, `Enter`.
- Reorder by drag in the Inspector; `Alt+Up` / `Alt+Down` with a row focused also reorders.
- Promote is undoable as one commit; unpromote too.

**Copy.**
- Menu: `Promote to parent…`, `Unpromote`, `Go to promoted parameter`, `Go to target`, `Rename label…`, `Rename name…`, `Remove`, `Retarget…`.
- Popover: title `Promote period to momentum_confirm`; fields `Label`, `Name`; hint `Used in ch("../rsi_period") and in the file.`; buttons `Cancel`, `Promote`.
- Tooltips: `Promoted to ../rsi_period. Edit it on the subnet.`; `Target rsi/period no longer exists.`
- Confirm: `spread_z reads ch("../rsi_period"). Unpromote anyway?` `Unpromote` / `Cancel`.
- Inspector: sub-title `Promoted`, link `+ Promote a parameter…`, muted `Defined by regime_filter v3`.

**Accessibility.**
- The popover is `role="dialog"`; focus starts in `Label`. The child row's non-editable value has `aria-readonly="true"` and `aria-describedby` with the tooltip text. The Inspector grip is `aria-roledescription="drag handle"`; keyboard reorder via `Alt+Arrow` is the required path.

**Must not.**
- Do not write the promoted value into the child's `params` as a copy. One value, on the parent. The backend substitutes in `flatten`.
- Do not let a child row that is promoted go into code mode.
- Do not allow promotion across more than one level in one step (a grandchild param is promoted to its parent first, then again; the popover title makes the target network explicit).
- Do not let the Name field accept a name that equals an existing param or promoted name on the subnet (live check, red field).

**Acceptance checks.**
- `promote.test.tsx`: promoting `rsi/period` on subnet `mom` adds `{name: 'rsi_period', label: 'Period', target: 'rsi/period', type: 'int', default: 14}` to `mom.promoted` and `mom.params.rsi_period === 14`; the child row renders `aria-readonly="true"` and the `↑` glyph.
- Editing the promoted row on the card commits `mom.params.rsi_period`, not `rsi.params.period`.
- Unpromote removes the entry and the child row is editable again with the last parent value.
- With 5 promoted params, the card renders 4 rows and the text `+1 more in Inspector`.
- Renaming `rsi` to `rsi_fast` (through `rename_node`) leaves the promoted row working (target string updated; backend vectors), and the UI shows no `promoted_target_missing`.

---

## S41. Save as asset dialog and "Promote to palette"

**Purpose.** Save a subnet to the library as a versioned asset so it can be dropped into other graphs, and optionally give it a place in the Tab menu under Rules.

**Placement.** Node context menu on a subnet (`Save as asset…`), Inspector header overflow on a subnet, and `Promote to palette` (same menu; opens this dialog with the palette section on and the name prefilled from `asset_ref` when present). Dialog width 560px.

**Anatomy (top to bottom).**
1. Title: `Save momentum_confirm as an asset`.
2. `Name` mono input, default = the subnet's name; validation `^[a-z_][a-z0-9_]{0,63}$`. Live line under it: `New asset` in `--nb-text-muted`, or `Saves as version 3 of regime_filter` in `--nb-cat-network` when the name exists in the library, or the red validation message.
3. `Description` text input (one line, sans; max 200 chars; default: the subnet's `meta.note` first line if any).
4. `Interface` (read-only preview, two chip rows): `reads` = attributes the network reads from its inputs; `writes` = its output's writes (derived from the last `/validate` streams; if no validate result yet, the dialog runs one). Each chip has its dtype tag.
5. `Promoted parameters` (read-only list of label · name · type · default; `none` in `--nb-text-dim` when empty; a link `Edit in Inspector` closes the dialog and opens the Inspector Promoted list).
6. Palette section: a checkbox `Show in the Tab menu under Rules`; when on: `Label` (sans input, default title-cased name: `Momentum Confirm`), `Glyph` (mono input, 1 to 2 characters, default `R`). A 32px live preview row in Tab-menu style: glyph chip in `--nb-cat-rules`, label, description, `RULES`.
7. Checkbox `Replace this node with a locked instance of the saved version` (default on). Help text: `Off keeps this node as a local copy that does not follow the library.`
8. Footer: `Cancel`, `Save asset` (primary; label becomes `Save version 3` when the name exists).

**States.**

| State | Look |
|---|---|
| the subnet has no `subnet_output` | warning bar `This network has no output. It can be saved, but it will write nothing.`; save allowed |
| the subnet has diagnostics errors | error bar `Fix the errors inside momentum_confirm before saving it as an asset.` + `Show diagnostics`; primary disabled |
| name invalid or empty | red input, primary disabled |
| library unreachable (GET list fails) | the live line reads `Could not check the library.`; save still attempted |
| saving | primary spinner `Saving…` |
| 201 | dialog closes; toast `Saved regime_filter v3 to the library. Open Asset Manager`; when "Replace" was on, the node now renders as a locked instance `regime_filter @ v3` |
| 400 (name or shape) | error bar with server `detail` |
| the subnet is a locked instance already (`Promote to palette` path) | the Interface and Promoted sections are read-only from the asset; the note under the title says `Saving creates version 4. Existing graphs keep v3.` |

**Interactions and keys.** `Cmd+Enter` saves. Toggling the palette checkbox reveals the two fields with `--nb-motion-base`. The glyph field uppercases nothing (a user may want `Σ`).

**Copy.**
- Title: `Save momentum_confirm as an asset`.
- Lines: `New asset`, `Saves as version 3 of regime_filter`, `Saving creates version 4. Existing graphs keep v3.`
- Fields: `Name`, `Description`, `Interface`, `reads`, `writes`, `Promoted parameters`, `none`, `Edit in Inspector`, `Show in the Tab menu under Rules`, `Label`, `Glyph`, `Replace this node with a locked instance of the saved version`, help `Off keeps this node as a local copy that does not follow the library.`
- Bars: `This network has no output. It can be saved, but it will write nothing.`, `Fix the errors inside momentum_confirm before saving it as an asset.`
- Toast: `Saved regime_filter v3 to the library. Open Asset Manager`.
- Buttons: `Cancel`, `Save asset`, `Save version 3`, `Saving…`.

**Accessibility.** Dialog rules. The interface preview chips are in a `<ul aria-label="Attributes this asset reads">` and `<ul aria-label="Attributes this asset writes">`. The preview row is `aria-hidden` (decorative; the fields carry the values).

**Must not.**
- Do not send `network` with absolute positions; positions stay relative to the subnet (as stored).
- Do not include the instance's current promoted **values** as the asset's `default`s unless the user has not changed them since promotion; use the promoted entry's `default` field as stored, and offer no extra control for it (keep the dialog small).
- Do not mutate the library asset in place; every save is a new version (contract: immutable per version).
- Do not bake `asset_ref` into children; only the subnet node carries `asset_ref` and `locked`.

**Acceptance checks.**
- The POST body matches `{name, description, network: {nodes, wires}, promoted, interface, palette}` with `palette: null` when the checkbox is off and `{category: 'rules', label, glyph}` when on.
- With an existing name the live line contains `Saves as version 3` and the primary reads `Save version 3`.
- After a mocked 201 with "Replace" on, the store's subnet node has `asset_ref: {name, version}` and `locked: true`, and its children are removed from `graph.nodes`.
- With "Replace" off the node is unchanged except that nothing is written (a toast still shows).
- The Glyph field refuses a third character (value stays at 2).

---

## S42. Asset Manager (versions, where used, locked versus local copy)

**Purpose.** One place to see every saved asset, its versions, where it is used, and to insert, delete or update it.

**Placement.** Toolbar overflow `⋯ › Asset Manager…` (FA6), `Cmd+Shift+A`, the Tab menu Library footer link `Manage assets…`, and the toast link after a save. Dialog 840x520, resizable is not required.

**Anatomy.** Two columns.
- Left (280px, `--nb-bg-panel`, 1px right border): a 32px search input (`Search assets or @attributes`), then a virtualized list of assets, rows 44px: glyph chip (palette glyph in `--nb-cat-rules` when a palette entry exists, else `N` in `--nb-cat-network`), name (mono 12px 600), second line `v3 · 2 versions · used in 3 graphs` (sans 11px `--nb-text-muted`). Active row `--nb-bg-active` with a 2px left bar in the chip color. Sorted by name; a `Sort ▾` (name / recently saved / most used) at the right of the search.
- Right (detail):
  - Header 40px: name mono 14px 600, palette badge `RULES` (caps) when present, right: `Insert into graph` (primary) and an overflow `⋯` (`Promote to palette…`, `Export JSON`, `Delete version…`, `Delete all versions…`).
  - Version selector: a row of pills `v1 v2 v3` (latest first is not used; ascending order, latest active by default, active pill `--nb-bg-active`), each with a tooltip of `created_at` in local time.
  - `Description` paragraph (sans 12px `--nb-text-secondary`).
  - `Interface`: two chip rows (reads, writes) with dtype tags.
  - `Promoted parameters`: table with columns `Label · Name · Type · Default` (24px rows, mono for name/type/default).
  - `Used by`: list of graphs (`pair_aapl_msft` link · `2 instances · v3, v2` · `locked` / `local copy` tags). Click a graph opens it and selects the first instance. `Not used in any graph.` when empty. Bots are listed separately when the API exposes it: `Bots keep their own copy; library changes never affect a running bot.` as a fixed footnote (always shown, since it is always true).
  - Preview: a 200px tall read-only mini render of the network (React Flow instance with `nodesDraggable={false}`, `panOnDrag`, `zoomOnScroll`, fit view on open). Optional in the first build; when absent, the space shows `Preview not available` in `--nb-text-dim`. Not required for acceptance.
- Footer: `Close`.

**States.**

| State | Look |
|---|---|
| loading | left list shows 6 skeleton rows (`--nb-bg-hover` bars); right empty |
| empty library | left list shows `No assets yet.` and a line `Select nodes, press Shift+C to make a subnet, then right-click it and choose Save as asset…` (sans 12px `--nb-text-dim`, padded 16px); right empty |
| fetch error | a bar with the server `detail` and `Retry` |
| asset selected, version not the latest | an info line above the description: `v2 · a newer version v3 exists.` |
| delete version with users | confirm dialog: `Delete regime_filter v2? 2 graphs use it: pair_aapl_msft, spy_regime. They will show an "asset missing" error until you replace the node or restore the asset. Bots are not affected.` Buttons `Delete v2` (destructive) / `Cancel`. |
| delete version without users | confirm `Delete regime_filter v2? This cannot be undone.` |
| delete all versions | same confirm with the union of users and `Delete all` |
| after delete | list refreshes; if the current graph used it, the canvas nodes show `asset_missing` (from the next validate) |
| insert while inside a locked instance | `Insert into graph` disabled, `title="You are inside a locked asset"` |
| search by `@attr` | matches assets whose interface reads or writes it; the matching chip is highlighted in the detail (`outline: 1px solid var(--nb-selection)`) |

**Interactions and keys.**
- `Insert into graph`: closes the dialog and places a locked instance of the selected version at the canvas center of the current network (or at the cursor if the dialog was opened from the Tab menu), selected, with its promoted params at defaults. Tab menu insertions behave the same without the dialog.
- Up/Down in the list moves the selection; `Enter` inserts; `Delete` opens the delete confirm.
- Version pills are arrow-navigable.

**Copy.** All strings above. Also: search placeholder `Search assets or @attributes`, sort options `Name`, `Recently saved`, `Most used`, sections `Description`, `Interface`, `Promoted parameters`, `Used by`, footnote `Bots keep their own copy; library changes never affect a running bot.`, empty `Not used in any graph.`

**Accessibility.**
- Dialog rules. The list is `role="listbox"` with `aria-activedescendant`; version pills are `role="radiogroup"`. The used-by graph links are `<a>`. The delete confirm is a nested dialog with focus on `Cancel`.

**Must not.**
- Do not let the manager edit asset content; assets are immutable per version. Editing goes through an unlocked local copy and a new save.
- Do not fetch every asset file on open; the list uses `GET /api/graph_library` (list items carry versions, palette, interface, used_by). Fetch the full file only for the selected version.
- Do not render the preview with a second copy of the node components; reuse the same node types with an `interactive={false}` prop, or skip the preview.
- Do not delete through the generic graph routes.

**Acceptance checks.**
- With a mocked list of 3 assets, the left column renders 3 rows with the text `v3 · 2 versions · used in 3 graphs` from the fixture, and selecting one fetches `GET /api/graph_library/{name}/{latest}` once.
- `Insert into graph` adds a node `{type: 'subnet', asset_ref: {name, version}, locked: true}` to the current network with no children in `graph.nodes`.
- The delete confirm for an asset with users contains the graph names and the phrase `Bots are not affected.`; confirming sends `DELETE /api/graph_library/{name}/{version}`.
- Searching `@regime_on` narrows the list to assets whose interface writes it.
- `Cmd+Shift+A` opens the dialog (command registry test).

---

## S43. Rules palette category, asset entries in the Tab menu, attribute search

**Purpose.** Assets appear where nodes appear, and the palette answers "what writes `@signal`" as the vision asks.

**Placement.** The Tab menu (foundation 6.3). Nothing new is mounted.

**Anatomy and rules.**
- Category list: `Rules` holds built-in rule nodes and every asset whose `palette.category === 'rules'`. `Library` holds every asset (with or without a palette entry). `Networks` holds `Subnet` (empty, card view), `Subnet input`, `Subnet output` (the last two only inside a network).
- Asset row: glyph chip = `palette.glyph` on `--nb-cat-rules` when a palette entry exists, else `N` on `--nb-cat-network`; name = `palette.label` or the asset name; description `asset v3 · <description>`; right column `RULES` or `LIBRARY`.
- Placing an asset from the menu creates a locked instance of the **latest** version at the cursor, wired as foundation 5.4 when the menu was opened from a port drag (`in0` gets the wire; the first promoted `attr` param, if any, is prefilled with the source's primary write).
- Search: the query `@volume` (or `volume` with the `@` omitted) matches every catalog node whose `reads` or `writes` include it, and every asset whose interface does. Result rows show the matching chip after the description (a 16px read or write chip with the matched name; `--nb-selection` outline). Ranking: exact attribute match first, then name, then description (`search.ts`).
- A footer link in the Library category: `Manage assets… ⌘⇧A`.

**States.**

| State | Look |
|---|---|
| library loading | Library and Rules show their built-in rows at once; asset rows append when the list arrives (no spinner; a muted `loading assets…` last row for at most 2 s) |
| library fetch failed | a muted last row `Library unavailable` (click retries) |
| inside a locked instance | the menu opens but every row is disabled with a 22px top note `Locked asset. Unlock to add nodes.` |
| query matches nothing | `No nodes match "foo". Try a type, a description, or @attribute.` |
| `SL_CODE_NODES=0` (S49) | the `Wrangle` row shows a `disabled` tag and is not placeable |

**Copy.** As above; category labels `Rules`, `Library`, `Networks`; rows `Subnet`, `Subnet input`, `Subnet output`; footer `Manage assets…`; empty `No nodes match "foo". Try a type, a description, or @attribute.`

**Accessibility.** As foundation 6.3 (the list is a `listbox`; the search input owns `aria-activedescendant`). The matched chip has `aria-label="matches @volume"`.

**Must not.**
- Do not block the menu on the library request; render built-ins first.
- Do not create an unlocked copy from the menu; instances start locked.
- Do not add the asset rows to `catalog.generated.ts`; they join at runtime.

**Acceptance checks.**
- `search.test.ts`: query `@volume` returns every entry that reads or writes `@volume` and nothing else; query `volume` returns the same plus name/description matches after them.
- With a mocked library of one palette asset, the Rules category count includes it and its row reads `asset v3 · SPY above its 50-day SMA`.
- Choosing the asset row adds a locked instance node at the menu position.
- Inside a network, the Networks category lists `Subnet input`; at root it does not.

---

# Wave 7

## S44. Code-mode `=` toggle and the one-line expression input (ExprInput)

**Purpose.** Any parameter can be a formula instead of a number, the Houdini way: click `=` and type.

**Placement.** Every param row on a node and in the Inspector whose catalog spec has `code_able !== false` and whose type is `number`, `int`, `bool`, `string` or `select` (foundation 4.4). Not on `attr`, `attr_list`, `write`, `path`, `time_range` rows, and never on Ticker `symbol`/`interval`/`prefix`.

**Anatomy.**
- The `=` glyph: mono 10px `--nb-text-dim`, in the 6px gutter left of the label, `opacity: 0` until row hover (CSS), `opacity: 1` and `--nb-code-keyword` while the row is in code mode. Hit area 16x20.
- Code-mode row (node): height 36px; the label stays on line 1 at the left; line 2 holds the ExprInput full-width: a fixed `=` in `--nb-code-keyword` then the field, mono 11px `--nb-code-expr`, background transparent, no border (the row's 2px `--nb-cat-code` gutter tick marks the mode, prototype `.row.code::before`). Long text ellipsizes when not focused; when focused, the field scrolls horizontally.
- Code-mode row (Inspector): 24px label row, then a 28px Monaco single-line editor (S47 theme, `lineNumbers: off`, `folding: off`, `wordWrap: off`, `scrollbar hidden`, `overviewRulerLanes: 0`, `renderLineHighlight: none`), then a 16px status line: `→ int` (result type from `parse_code`, when it sends one; in W7 `parse_code` never runs code and sends `null`, so the line shows a diagnostic or the post-cook `= 21`) in `--nb-text-muted`, or the first diagnostic in `--nb-error` with `line:col`. Until Monaco has loaded, a plain `<input type="text">` with identical font and colors stands in and is swapped in place with the text and caret position kept.
- ExprInput (node and fallback): `<input type="text" inputMode="text" spellCheck={false} autoComplete="off" autoCorrect="off" autoCapitalize="off">` with `data-testid="expr-input"`.
- Right end of the field (both places), 14px: a status dot: none while typing, `--nb-ok` after an ok parse, `--nb-error` after an error, `--nb-warn` when the result type is right but a warning exists.

**Behavior.**
- Enter code mode: click `=`, press `=` while the row's literal input is focused, or `Use expression` in the row menu. The field opens with the literal value as its initial text (`14` becomes `14`; a select value becomes `"sma"`), selected, so typing replaces it.
- Commit: `Enter` or blur commits `{"expr": "<text>"}` through `commit`. Empty text on commit leaves code mode and restores the last literal. `Esc` reverts the text to the last committed expression and blurs; if the row had just entered code mode, `Esc` leaves it.
- Leave code mode: the row menu `Use literal value` (writes the last literal; if none, the param default), or the `=` glyph click (same), with a 400 ms tooltip `Back to a value (keeps the expression in undo)`.
- Validation: 300 ms after the last keystroke, `parseCode({code, context: 'expr', expected: {type}, graph, node_id})` runs; in-flight requests are cancelled on a new keystroke. The result sets the status dot, the Inspector status line, and Monaco markers. Typing never waits on it. Run is disabled while any expression has an error (same rule as invalid fields).
- The value shown after a cook: the Inspector status line appends `= 21` (the resolved scalar) when the cook's detail attributes include it (`/inspect` `detail` for the node; optional, shown only when present).
- Completion (Inspector Monaco only): `@` lists the node's `available_attrs`, **detail** only (an expression may read only detail attrs, per the design note), with dtype; `ch` offers `ch("…")`, `chf("name", default=0.0)`, `chi`, `chb`, `chs`, `chv` snippets; `sl.` lists the `sl` helpers from `code_capabilities.functions` with signature and doc; `np` and `pd` are offered as module names. On a node the ExprInput has no completion.
- Promoted child rows (S40) and locked-asset internals never enter code mode.

**States.**

| State | Look |
|---|---|
| literal | as foundation 4.4; `=` on hover |
| code, valid | yellow expression, gutter tick, green dot; the node's type slot does not change; the status bar does not change |
| code, error | red dot; the field's text keeps its color; a 1px `--nb-error` underline under the field (node) or a Monaco marker (Inspector); the node gets the error badge with the message; Run disabled |
| code, series result on a scalar param (`code_type`) | as error; message from the server includes `use a Wrangle for per-bar logic` |
| code, references a missing node (`ref_broken`) | as error |
| code, cycle (`ch_cycle`) | as error on every node in the cycle |
| code disabled on the server (S49) | `=` glyphs hidden; existing code rows render the expression in `--nb-text-dim` with the error badge `code_disabled`; the field is read-only |
| read-only graph | no `=`; an expression row shows `= 7 if … else 21` as text |
| row is a promoted param on a subnet card | `=` works; the expression is stored in the instance's `params[name]` |
| stale (graph changed since the last parse) | nothing; parse is per row and recent by construction |

**Copy.**
- Row menu: `Use expression =`, `Use literal value`.
- Tooltips: `Expression (=)`, `Back to a value (keeps the expression in undo)`.
- Status line: `→ int`, `→ float`, `→ bool`, `→ string`, `= 21`, and diagnostics as sent (`code_syntax at 1:12: unexpected token`), format `<code> at <line>:<col>: <message>`.

**Accessibility.**
- The `=` glyph is a `button` with `aria-label="Use an expression for period"` / `aria-pressed`. The ExprInput has `aria-label="Expression for period"` and `aria-invalid` when in error, `aria-describedby` pointing at the status line. The Monaco single-line editor exposes `aria-label` through its `ariaLabel` option. Keyboard-only: Tab to the literal input, press `=`, type, `Enter`.

**Must not.**
- Do not render ExprInput as `type="number"` or any input type other than `text` (F278, critic 35). A test asserts it.
- Do not load Monaco for node rows; only the Inspector uses it. Node rows stay plain inputs (foundation 8: no heavy widgets in the graph layer).
- Do not parse on every keystroke without debounce or without cancelling; and never block the commit on the parse result (the store accepts `{"expr"}` even when invalid; validation reports it).
- Do not lose the last literal when entering code mode; keep it in `Node.meta.literal_backup[param]` so `Use literal value` restores it.
- Do not add the `=` glyph to Ticker `symbol`, `interval`, `prefix` (D1) or to `write` rows.

**Acceptance checks.**
- `exprInput.test.tsx`: every rendered `expr-input` has `type="text"`; a number row renders an `=` button; a Ticker `symbol` row renders none.
- Clicking `=` on `period 14`, typing `7 if chf("../vol/threshold") > 2 else 21`, `Enter` commits `params.period = {expr: '7 if chf("../vol/threshold") > 2 else 21'}`; `parseCode` is called once with `context: 'expr'` and `expected: {type: 'int'}` after the debounce (fake timers).
- A mocked `parse_code` with `ok: false` sets `aria-invalid="true"` and shows the diagnostic in the `<code> at <line>:<col>: <message>` format; Run is disabled.
- `Use literal value` after the above restores `params.period = 14`.
- Screenshot: a code-mode row is 36px tall with a 2px purple gutter tick and the expression in `--nb-code-expr`.

---

## S45. Code drawer on a node and in the Inspector (Level 2 code block)

**Purpose.** Any built-in node can carry a short code block that runs after the node's own compute. The node hints that it has one; the Inspector is where it is written.

**Placement.** On the node: a drawer row under the param rows, above the sparkline slot. In the Inspector: the `Code` section (foundation 6.5, item 2).

**Node drawer anatomy.**
- Closed row (20px, present only when `Node.code` is non-empty or the user opened it once this session): a `{}` glyph 10px in `--nb-cat-code`, text `code · 3 lines` mono 10px `--nb-text-muted`, a chevron `▸` at the right. Hover `--nb-bg-hover`.
- Open: the row's chevron turns `▾`; under it a read-only code block (`.codeblk` in the prototype): mono 10px/15px, `--nb-bg-input`, 1px `--nb-border-subtle`, radius 4, padding `4px 6px`, `white-space: pre`, `overflow: hidden`, up to 6 lines; a 7th line reads `… +4 lines` in `--nb-text-dim`. Syntax colors per S47 (a small pure tokenizer in `pythonLanguage.ts` that applies the same rules as the Monaco language, exported as `highlightPython(code): TokenSpan[]`; no Monaco on the node).
- Open state is per node for the session (`Node.meta.code_open` is not saved).
- Clicking the block: selects the node, opens the Inspector if closed, expands its Code section and focuses Monaco at the block's first line.
- Nodes without a code block have no drawer row; the way to add one is the Inspector section or the node menu `Add code block`.

**Inspector Code section anatomy.**
- Section header row 28px: `Code` (caps) with a status tag at the right: `ok · reads @rsi · writes @rsi` mono 10px `--nb-text-muted`, or `1 error` in `--nb-error`, or `off` in `--nb-text-dim` when there is no code. Collapsed by default for built-ins with no code; open when code exists.
- Above the editor, when spare params exist: a caps line `Spare parameters` and the rows (S48).
- Editor: Monaco (S47), height min 96px, grows with content to 320px, then scrolls; a 6px bottom resize handle (`ns-resize`) stores the height in `localStorage['nb.inspector.code.h']`. `lineNumbers: on`, `wordWrap: on`.
- Empty editor placeholder (Monaco decoration, `--nb-text-dim`): `# runs after this node's own compute\n# @rsi = sl.ema(@rsi, chi("smooth", default=3))`.
- Below the editor, the diagnostics list: rows of 20px, severity dot, `line:col`, message, code in mono muted. Click a row moves the cursor there. Max height 96px, scrolls.
- A footer line 16px: `Checked 0.3 s ago · 4 of 8 KB` mono 10px `--nb-text-dim` (source size versus `limits.max_source_bytes`; turns `--nb-warn` above 90%).
- Section overflow `⋯`: `Format (indent)`, `Copy code`, `Clear code…` (confirm: `Remove the code block from rsi? Spare parameters and their values are kept until you save.`), `Open reference` (opens a popover listing `code_capabilities.functions` with signatures and docs; searchable; 320x400).

**Behavior.**
- Edits go to `Node.code` through `commit` on blur, on `Cmd+Enter`, and 800 ms after the last keystroke (so undo groups typing bursts; undo restores the whole burst). `parseCode({code, context: 'node_code', expected: null, graph, node_id})` runs 400 ms after the last keystroke and on commit, cancelling in-flight requests; its `params` become `Node.spare_params` (S48), its `reads`/`writes` update the node's chips, its diagnostics set markers and badges.
- The node's writes chips show the code's writes in `--nb-cat-code` tint after the node's own writes (so an RSI with `@rsi_smooth = …` shows `+@rsi` green and `+@rsi_smooth` purple).
- Bypass on the node also skips the code block (pass-through).
- Cooking: unchanged; the node cooking bar covers both.

**States.**

| State | Look |
|---|---|
| no code | no node row; Inspector section `Code` collapsed with tag `off`; menu `Add code block` |
| code, ok | node row `code · 3 lines`; tag `ok · reads @rsi · writes @rsi_smooth` |
| code, error | node row text in `--nb-error` and the node error badge; tag `1 error`; markers in the editor; Run disabled |
| code, warning only | amber badge; tag `1 warning` |
| code over the size limit (`code_limit`) | footer line red `8.4 of 8 KB`; error as above |
| Monaco loading (first open) | a textarea stand-in with the same theme colors for up to 2 s, then swapped in place; a 10px spinner in the section header |
| Monaco failed to load (chunk error) | the textarea stays as the editor with a muted line `Editor could not load. Plain text editing still works.`; diagnostics still come from the server |
| code disabled on the server (S49) | editor read-only with a 22px bar `Code nodes are disabled on this server.`; the node shows the `code_disabled` badge |
| locked asset internals | editor read-only; the locked bar from S38 |
| read-only graph | the node drawer shows the block; the Inspector editor is read-only |

**Copy.**
- Node row: `code · 3 lines`, `… +4 lines`.
- Tags: `off`, `ok · reads @rsi · writes @rsi_smooth`, `1 error`, `2 errors`, `1 warning`.
- Placeholder: `# runs after this node's own compute` / `# @rsi = sl.ema(@rsi, chi("smooth", default=3))`.
- Footer: `Checked 0.3 s ago · 4 of 8 KB`.
- Menu: `Add code block`, `Format (indent)`, `Copy code`, `Clear code…`, `Open reference`.
- Confirm: `Remove the code block from rsi? Spare parameters and their values are kept until you save.` `Remove` / `Cancel`.
- Failure line: `Editor could not load. Plain text editing still works.`

**Accessibility.**
- The node drawer row is a `button` with `aria-expanded`. The Monaco editor has `ariaLabel="Code block for rsi"`. The diagnostics list is `role="list"`; each row is a `button` (`aria-label="error at line 2 column 8: …"`). Keyboard-only: Inspector section header (`Enter` toggles), Tab into the editor, `Esc` leaves the editor to the section header (Monaco's `Esc` is remapped when the editor has no open widget).

**Must not.**
- Do not mount Monaco on the canvas or per node. The node drawer is static highlighted text from `highlightPython()`.
- Do not use Monaco's `automaticLayout: true` (it polls and adds a ResizeObserver per editor). Call `editor.layout()` on the Inspector resize end and on the drawer resize end.
- Do not load Monaco eagerly. `import('monaco-editor')` on first open; `vite.config.ts` worker setup per 7.D; verify the chunk split with the plan's grep gate.
- Do not commit on every keystroke (undo spam); use the 800 ms burst rule plus blur.
- Do not compute reads/writes on the client; they come from `parse_code`.

**Acceptance checks.**
- A node with `code: "@rsi_smooth = sl.ema(@rsi, chi(\"smooth\", default=3))"` renders a drawer row `code · 1 line` and, when opened, one highlighted line with `@rsi_smooth` in `--nb-code-keyword` and `sl.ema` in `--nb-code-func` (computed style on spans).
- Typing in the Inspector editor calls `parseCode` once with `context: 'node_code'` after 400 ms (fake timers), and a mocked response with `writes: [{name: '@rsi_smooth'}]` adds a write chip to the node.
- A mocked `parse_code` error with `line: 2, col: 8` renders a diagnostics row containing `2:8` and the node's error badge.
- The build gate: `monaco` is absent from `index-*.js` and present in a separate chunk.
- `Clear code…` then confirm sets `Node.code` to `null` and keeps `spare_params` values in `params`.

---

## S46. Wrangle node body

**Purpose.** The Wrangle is the Level 3 node: it merges its inputs and runs a code block that writes new attributes. The node body shows the code, its spare params and what it writes.

**Placement.** Canvas; category Code / Wrangle, glyph `{}`, stripe `--nb-cat-code`. Catalog type `wrangle`, inputs dynamic `in0..in3` (max 4).

**Anatomy.**
- Card min 240, max 320 (foundation limits). Header: glyph `{}` in `--nb-cat-code`, name (`spread_z`), type `wrangle`, badge, chevron.
- Ports: connected inputs plus one spare dashed port (foundation 4.3), labels `in0..in3`, rings in `--nb-cat-code`; one output.
- Body order: code block, spare param rows, chips, sparkline.
  - Code block: the `.codeblk` style (mono 10px/15px, `--nb-bg-input`, 1px `--nb-border-subtle`, radius 4, padding `4px 6px`), always visible (no drawer row), up to 8 lines then `… +N lines`. Highlighted by `highlightPython()`. Click focuses the Inspector editor (as S45). Double-click the block also opens the Inspector.
  - Spare params (S48): normal rows, max 4 on the node.
  - Chips: reads (grey) from `parse_code.reads`; writes tinted `--nb-tint-code` / `--nb-cat-code` from `parse_code.writes`. A Wrangle may write several attributes; all show (overflow rule applies).
  - Sparkline: the first write.
- Default code for a new Wrangle: `# write attributes with @name = expr\n@out = @close`. The default write name is uniqued (`@out_2`).
- Inspector for a Wrangle: the Code section is open by default at 240px (foundation 6.5) and sits **above** Parameters (so the code is the first thing); Parameters holds the spare params; Stream shows reads and writes as usual.

**States.**

| State | Look |
|---|---|
| default, ok | as above |
| error (any `code_*`, `ref_broken`, `ch_cycle`) | the code block gets a 1px `--nb-error` border at 60%; the offending line (from the first diagnostic) gets a `rgba(248,113,113,0.12)` background; error badge; Run disabled |
| warning | amber badge; no block change |
| no writes (code writes nothing) | writes chips replaced by `writes nothing` in `--nb-text-dim`; `/validate` warning shows |
| missing read (`attr_missing`) | the read chip in `--nb-error` with a dotted underline; the `@name` token in the block is underlined in `--nb-error` (from the diagnostic's line/col) |
| bypassed | body at 0.45 opacity; the output is the merged input stream |
| collapsed (`X`) | 26px header; type slot `3 +@` count |
| code disabled (S49) | error badge `code_disabled`; block text `--nb-text-dim`; the Tab menu row for Wrangle is disabled |
| read-only graph | block visible, no editing |
| stale | sparkline dim |

**Interactions and keys.**
- Wire drops on the body connect to the first free `in*` port; the spare port appears after each connection up to 4.
- `Enter` with the node selected and no network to dive into: opens the Inspector Code section (Wrangle only; a Houdini user expects Enter to "open" the node).
- Everything else as any node.

**Copy.**
- Default code lines as above. `… +3 lines`. `writes nothing`.
- Catalog description: `Code that reads the merged input stream and writes new attributes.`

**Accessibility.**
- The code block on the node is `role="button"` with `aria-label="Open code for spread_z, 4 lines"`. The card `aria-label` includes the write list: `Wrangle spread_z, writes @spread and @spread_z`.

**Must not.**
- Do not render the node's code block with Monaco or a `<textarea>`; it is highlighted static text.
- Do not clamp inputs below 4 or allow more than 4 (contract).
- Do not derive the write chips from the code text on the client; use `parse_code.writes` (the client tokenizer is for colors only).
- Do not put the code block below the chips; the block is the first body element (John's mockup).

**Acceptance checks.**
- A new Wrangle from the Tab menu has `code` equal to the default text and one write `@out` (uniqued when taken).
- With a mocked `parse_code` returning `writes: [@spread, @spread_z]`, `reads: [@close, @msft_close]`, the node shows two purple write chips and two grey read chips in that order.
- A mocked diagnostic on line 3 renders a highlighted third line in the block (background color assertion on the line span).
- The card renders `in0`, `in1` connected plus one dashed spare port; with 4 connected there is no spare.
- Screenshot at 100%: the code block is the first element under the header, mono 10px, with a 4px inner padding.

---

## S47. Monaco theme, Python tokens, completion, diagnostics markers and gutter

**Purpose.** One editor look for every code surface: same colors as the node chips, diagnostics from the server with exact positions, and completion that knows the stream and the `sl` helpers.

**Placement.** `frontend/src/features/nodebuilder/code/MonacoEditor.tsx` and `frontend/src/features/nodebuilder/code/pythonLanguage.ts`. Used by S44 (single-line), S45 and S46 (block).

**Language registration (Python with the `@attr` sugar).**
- Code nodes are real Python (design note, decided by John 2026-09-30). The editor uses Monaco's built-in `python` language and extends it.
- Start from the built-in definition (`monaco-editor/esm/vs/basic-languages/python/python.js`, which exports `conf` and `language`). Copy `language`, prepend the rules below to `tokenizer.root`, and register the copy once as the language id `nb-python` with the built-in `conf` (brackets, comments, indentation rules). Models use `nb-python`.
- Prepended Monarch rules, in this priority:
  1. `attr.write` — `@name` followed by optional spaces and `=` (not `==`), an augmented `op=`, or `:` (an annotation such as `@x: bool = …`).
  2. `attr` — any other `@` followed directly by `[a-z_][a-z0-9_]*`. Monarch cannot see the previous token, so a decorator (`@staticmethod`) or a spaceless matmul (`a @b`) also colors as an attribute. This is cosmetic only; the server's sugar rule decides what the code means. `a @ b` (with a space) keeps the built-in coloring.
  3. `ch` — `\b(ch|chf|chi|chb|chs|chv)\b(?=\()`.
  4. `func` — `\bsl\.[a-z_][a-z0-9_]*(?=\()`, the whole `sl.name` as one token. Unknown helper names still color; the server reports them.
- Everything else comes from the built-in Python tokenizer: comments, strings (including f-strings and triple quotes), numbers, keywords, identifiers, delimiters and brackets, and `tag` for decorators it sees first. 7.D checks these class names against the installed `monaco-editor` version and maps any other names in the theme below.
- Line comment `#`. No block comment.

**Theme `nb-code-dark`** (`base: 'vs-dark'`, `inherit: false`):

| Token | Foreground | Style |
|---|---|---|
| `comment` | `--nb-code-comment` `#6b7386` | italic |
| `string` | `--nb-code-string` `#fcd34d` | |
| `attr` | `--nb-code-keyword` `#c084fc` | |
| `attr.write` | `#c084fc` | bold |
| `ch` | `#c084fc` | bold |
| `func` | `--nb-code-func` `#34d399` | |
| `keyword` | `--nb-code-pykw` `#c3c9d6` | bold |
| `tag` (decorators) | `--nb-code-pykw` `#c3c9d6` | bold |
| `number` | `--nb-code-number` `#7dd3fc` | |
| `delimiter` (and unmatched operators) | `--nb-code-local` `#eef1f6` | |
| `identifier` | `#eef1f6` | |

Editor colors: `editor.background` `#0a0c10` (`--nb-bg-input`), `editor.foreground` `#eef1f6`, `editorLineNumber.foreground` `#7a8296`, `editorLineNumber.activeForeground` `#c3c9d6`, `editor.lineHighlightBackground` `rgba(255,255,255,0.03)` (FA5), `editor.selectionBackground` `rgba(56,189,248,0.25)`, `editorCursor.foreground` `#eef1f6`, `editorGutter.background` `#0a0c10`, `editorWidget.background` `#161b25` (`--nb-bg-elevated`), `editorWidget.border` `#2f3848`, `editorSuggestWidget.selectedBackground` `rgba(255,255,255,0.08)`, `editorError.foreground` `#f87171`, `editorWarning.foreground` `#fbbf24`, `editorInfo.foreground` `#98a1b3`, `scrollbarSlider.background` `rgba(255,255,255,0.08)`. Monaco needs literal hex; the file defines these constants once and a test compares them with the CSS tokens by name.

**Editor options (block):** `fontFamily: 'Geist Mono, ui-monospace, SF Mono, Menlo, Consolas, monospace'`, `fontSize: 12`, `lineHeight: 18`, `minimap: {enabled: false}`, `lineNumbers: 'on'`, `lineNumbersMinChars: 3`, `glyphMargin: true`, `folding: false`, `wordWrap: 'on'`, `renderLineHighlight: 'line'`, `scrollBeyondLastLine: false`, `tabSize: 4`, `insertSpaces: true`, `quickSuggestions: {other: true, comments: false, strings: false}`, `suggestOnTriggerCharacters: true`, `bracketPairColorization: {enabled: false}`, `guides: {indentation: false}`, `overviewRulerBorder: false`, `hideCursorInOverviewRuler: true`, `automaticLayout: false`, `contextmenu: false` (the app's menu is used), `fixedOverflowWidgets: true` (so the suggest widget escapes the Inspector's overflow). Single-line (S44) adds the overrides listed there.

**Diagnostics markers.**
- `parse_code.diagnostics[]` map to `monaco.editor.setModelMarkers(model, 'nb-code', markers)`. Severity: `error` → `MarkerSeverity.Error`, `warning` → `Warning`, `info` → `Info`. Message = `<message> (<code>)`.
- Positions (orchestrator decision, 2026-09-30): the backend sends `line` 1-based and `col` 0-based, in characters of the user's text, as Python reports them (`SyntaxError.lineno` and `offset - 1`, traceback `lineno` and `colno`, `ast` `lineno` and `col_offset`); `end_line`/`end_col` the same way. One function `toMonacoRange(d)` in `nodebuilderCode.ts` converts to Monaco's 1-based columns (`startColumn = col + 1`, `endColumn = end_col + 1`; when `end_*` is null, the range runs to the end of the token: `startColumn + 1` at minimum). The `parse_code` docstring states the same convention.
- A diagnostic without a line (`line: null`) becomes a marker on line 1 column 1 with the full first line as range, and it is also listed under the editor (S45).
- Gutter: `glyphMargin` shows a 6px dot per line with a marker, in the severity color (`.nb-code-glyph-error` / `-warning` CSS classes through `deltaDecorations`). Hover on the squiggle shows the Monaco hover with the message and code.
- Markers clear when a new parse returns `ok: true`, never on keystroke (so they do not flicker).

**Completion provider.**
- Trigger characters: `@`, `(`, `"`, `.` (after `sl` it lists the helpers; inside `ch("` it offers `../`).
- `@` → the node's `available_attrs` (its input attributes from the last `/validate` `streams`; point and detail; for S44 detail only), each item: label `@close`, detail `float · point · from aapl`, kind `Variable`. Sorted: attributes read by the current code first, then by writer order.
- `sl.` → the helpers from `code_capabilities.functions`: label `sl.rsi`, detail `sl.rsi(x, period=14) → series_float`, documentation `doc`, insert as a snippet `rsi(${1:x}, ${2:period})` built from the signature; `kind: Function`.
- Identifier prefix → `np` and `pd` as module names (kind `Module`, detail `numpy` / `pandas`), `sl` (detail `StrategyLab helpers`), and the `ch` family. Members of `np` and `pd` are not listed; Monaco's word-based suggestions cover names already in the file.
- `ch` family → snippets: `chf("${1:name}", default=${2:0.0})`, `chi(...)`, `chb(...)`, `chs("${1:name}", default="${2}")`, `chv(...)`, `ch("../${1:node}/${2:param}")`; kind `Snippet`, detail `channel`.
- Inside `ch("` → path items from the graph: sibling node names (`../rsi/`), their params (`../rsi/period`) and attributes (`../rsi/@rsi`), promoted names of the enclosing subnet (`../lookback`), plus absolute roots (`/`). Built from the store's `childrenByParent` and the catalog; no server call.
- Hover provider: hovering a `func` token shows `signature` and `doc`; hovering `@attr` shows `dtype · class · written by <node>`.

**Loading.** `MonacoEditor.tsx` lazy-loads `monaco-editor` and its editor worker through Vite `?worker` (no CDN); registers the language and theme once (module singleton); exposes `value`, `onChange`, `onCommit`, `markers`, `singleLine`, `readOnly`, `ariaLabel`, `height`, and an imperative `layout()`. Fallback: while loading, or on load failure, a `<textarea>` (or `<input>` when `singleLine`) with the theme's font and colors, the same `aria-label`, and the same `onChange`/`onCommit`.

**States.** Loading, loaded, failed (S45 table). Read-only: cursor stays, typing does nothing, background unchanged, a 22px bar above says why (locked, code disabled, read-only graph).

**Accessibility.** Monaco's own accessibility support is on (`accessibilitySupport: 'auto'`); `ariaLabel` per S44/S45/S46; the suggest widget is keyboard-driven by default. The fallback textarea keeps the same label.

**Must not.**
- Do not load Monaco from a CDN, and do not import it at module top level anywhere in the node builder (bundle gate).
- Do not use `automaticLayout: true`.
- Do not register the language or theme per editor instance (leaks providers; completions duplicate). One registration guarded by a module flag; the function list updates through a setter.
- Do not run a client-side parser for diagnostics. Colors are client-side; correctness is the server's.
- Do not set markers from stale responses: tag each parse request with a sequence number and ignore results older than the last sent.

**Acceptance checks.**
- `pythonLanguage.test.ts`: tokenizing `@spread_z = sl.zscore(spread, chi("lookback", 20))  # note` gives these spans these classes: `@spread_z` `attr.write`, `sl.zscore` `func`, `spread` `identifier`, `chi` `ch`, `"lookback"` `string`, `20` `number`, `# note` `comment` (via `monaco.editor.tokenize` in a jsdom-compatible harness, or the exported pure tokenizer used by `highlightPython`). Spans that come only from the built-in tokenizer (the `=`, commas, brackets) are not asserted. A second case: in `z = a @ b` no span has the class `attr`.
- The theme constants equal the `--nb-code-*` token values (test reads `tokens.css` and compares by name).
- `toMonacoRange({line: 2, col: 8, end_line: 2, end_col: 12})` returns `{startLineNumber: 2, startColumn: 9, endLineNumber: 2, endColumn: 13}`; with `end_*: null` it returns `endColumn >= startColumn + 1`.
- The completion provider, given a stream with `@close` and `@spy_close` and code `x = @s`, offers `@spy_close` and not `@close` (prefix filter is Monaco's; the test asserts both items are provided with the right `detail` strings).
- Build gate: `grep -l "monaco" frontend/dist/assets/index-*.js` finds nothing; a `*monaco*` or `editor.worker` chunk exists.
- The language and theme registration function is idempotent (calling twice registers once; spy on `monaco.languages.register`).

---

## S48. Spare parameters (auto-promoted from `ch*()`)

**Purpose.** Writing `chf("threshold", default=2.0)` creates a real parameter on the node, as in Houdini. It looks and edits like any other row.

**Placement.** On the node (after the built-in `onNode` rows and, on a Wrangle, after the code block) and in the Inspector Parameters section under a caps sub-title `Spare parameters` (S45 places the same list above the editor in the Code section; it is one list rendered in one place: the Code section when the section is open, otherwise Parameters. Do not render it twice).

**Anatomy.** Each spare param is a foundation 4.4 row built from `SpareParamSpec`:

| `type` | Row |
|---|---|
| `float` | number row; unit none; slider in the Inspector when `min` and `max` are set (2px track in `--nb-cat-code`) |
| `int` | int row (scrub step 1) |
| `bool` | checkbox row |
| `string` with `options` | select row |
| `string` without options | text row (mono, max 256 chars) |
| `vector` | 2 to 4 number cells in one row, each 40px, mono 11px, labels `x y z w` in `--nb-text-dim` above the cells at 9px; scrub per cell; in the Inspector the row is 36px to fit the labels |

The row label = `spec.label` (defaults to the name). A 10px `{}` glyph in the gutter (persistent, `--nb-cat-code`, replacing the hover-only `=` position when the row is not in code mode) marks it as spare. Tooltip on the label: `Spare parameter from chf("threshold") in this node's code.`

**Behavior.**
- The list is `Node.spare_params` as returned by the last successful `parse_code` for that node (S45/S46 write it through `commit`). Values live in `Node.params[name]`. On first appearance the value is the spec's `default`.
- When the code stops referencing a name, its row disappears at the next successful parse; the stale value stays in `Node.params` (harmless; `/validate` ignores it) so that re-adding the call restores the value. `Clear code…` behaves the same (S45).
- If the type of a name changes (for example `chi("n")` becomes `chs("n")`), the row re-renders with the new control and the value resets to the new default; a 4 s toast: `threshold changed type to string; its value was reset.`
- `min`/`max` clamp on commit and give the red state when typed outside (foundation 4.4).
- Spare params can be promoted (S40) and can take expressions (S44) except that a spare param's own expression may not call `ch*` with its own name (the server reports `ch_cycle`).
- On-node limit: the node shows the first 4 spare params after the built-in `onNode` rows counted together (total on-node rows max 4 + the code block/drawer); the rest go to the Inspector with `+N more in Inspector`.

**States.**

| State | Look |
|---|---|
| appearing (first parse after typing the call) | the row fades in over `--nb-motion-base`; no other animation |
| value set by the user | the reset-to-default dot (foundation 6.5) appears in the Inspector |
| referenced by another node through `ch("../spread_z/threshold")` | a small `←` glyph 10px `--nb-text-dim` at the right of the value (Inspector only), tooltip `Read by long_entry` (from the `/validate` `param_deps` edges whose `target_id` and `target` match this row; one reader name per edge, comma-separated) |
| the code has an error | the last known rows stay (from the last ok parse); nothing is removed on an error |
| locked asset internals | rows read-only (S38) |

**Copy.**
- Sub-title `Spare parameters`.
- Tooltip `Spare parameter from chf("threshold") in this node's code.`
- Toast `threshold changed type to string; its value was reset.`
- Overflow line `+2 more in Inspector`.
- Vector labels `x`, `y`, `z`, `w`.

**Accessibility.** Rows follow foundation 4.4 (labelled inputs, `aria-describedby` for the source tooltip). The vector row groups its cells in a `role="group" aria-label="threshold vector"`.

**Must not.**
- Do not derive spare params on the client from the code text; take `parse_code.params`.
- Do not delete `Node.params[name]` when a row disappears.
- Do not render the spare list in two Inspector sections at once.
- Do not store spare param values anywhere but `Node.params` (the backend reads them there; bots snapshot them).

**Acceptance checks.**
- A mocked `parse_code` returning `params: [{name: 'threshold', type: 'float', default: 2.0, min: 0, max: 10, label: 'threshold'}]` renders one number row labelled `threshold` on the node and in the Inspector, with a slider in the Inspector, and `Node.params.threshold === 2.0` after the commit.
- A second mocked response without that param removes the row and leaves `Node.params.threshold` in place.
- A `vector` spec with a 3-float default renders three cells labelled `x y z`; editing the second cell commits `[a, newB, c]`.
- With 3 built-in `onNode` rows and 3 spare params, the node shows 4 rows and the text `+2 more in Inspector`.
- The Inspector renders the `Spare parameters` sub-title exactly once for a node whose Code section is open.

---

## S49. Code-disabled state: graph banner and bot pause reason

**Purpose.** When the server switch `SL_CODE_NODES=0` is on, the editor says so once, marks what cannot run, and the bot cards explain why a bot paused. Nothing crashes and nothing silently degrades.

**Placement.** Node builder (banner under the toolbar, node badges, Tab menu), Spawn dialog (S34 row), BotCard pause-reason row.

**Detection.** `getCodeCapabilities()` is fetched once when the node builder mounts (and re-fetched on `Retry` of a failed cook). `enabled: false` puts the editor in code-disabled mode. Until the response arrives, code UI behaves as enabled (no flash of a banner).

**Anatomy.**
- Banner: the 32px notice bar style from W1 S07, `--nb-warn` tint at 12% with `--nb-warn` text: `Code nodes are disabled on this server (SL_CODE_NODES=0). Graphs with expressions, code blocks or Wrangles cannot be validated, cooked or spawned.` Right side: `Dismiss` (per session; it returns on reload) and, when the current graph has code, `Show 3 code nodes` (selects them and frames them). Shown only when the graph contains code, or when the user tries to add code (see below).
- Node marks: every node with `{"expr"}` params, `code`, or type `wrangle` gets the error badge with the `code_disabled` diagnostic from `/validate` (the server emits it; the client does not invent it). Code text renders in `--nb-text-dim` (S44, S45, S46).
- Affordances: `=` glyphs hidden; `Add code block` and the Wrangle Tab row disabled with the tag `disabled`; a click on a disabled affordance shows the banner if dismissed.
- Toolbar: the Cook button is disabled with `title="Code nodes are disabled on this server"` when the graph has code; the diagnostics chip counts the `code_disabled` errors like any error.
- Spawn dialog: the `code_disabled` row in S34.
- BotCard: the existing pause-reason row shows the server's `pause_reason` text. When it equals the code-disabled code (`code_disabled`), render `Paused: code nodes are disabled on this server. Turn SL_CODE_NODES back on and restart the bot.` in the row's existing amber style, with a link `Open graph` when `graph_id` is present. Other `pause_reason` values render as they do today.

**States.**

| State | Look |
|---|---|
| enabled | none of the above |
| disabled, graph without code | no banner; `=` hidden; Wrangle row disabled; a first attempt to use code shows the banner |
| disabled, graph with code | banner; badges; Cook disabled |
| capabilities fetch failed | treat as enabled; the first cook or validate that answers `code_disabled` flips the mode and shows the banner |
| re-enabled (capabilities re-fetched after a `Retry` or reload) | banner gone, marks cleared by the next validate |
| bot paused for `code_disabled` | the pause-reason text above; the existing Start button stays (the server refuses to start with a clear `detail`, shown inline as today) |

**Copy.**
- Banner as above; buttons `Dismiss`, `Show 3 code nodes`.
- Cook tooltip: `Code nodes are disabled on this server`.
- Tab row tag: `disabled`.
- BotCard: `Paused: code nodes are disabled on this server. Turn SL_CODE_NODES back on and restart the bot.`, link `Open graph`.

**Accessibility.** The banner is `role="status"` (not `alert`: it is not urgent). Disabled controls keep `aria-disabled="true"` with the reason in `title`, so keyboard users can reach and read them.

**Must not.**
- Do not decide "this graph has code" by a client scan for the banner's `Show N code nodes` count only; use `/validate` diagnostics with `code_disabled` when available, and a store scan (`hasCode(graph)`) only for the immediate hide/show of affordances.
- Do not strip expressions or code from the graph when disabled. The data stays; only running is refused.
- Do not auto-start or auto-resume a bot from the card when the server re-enables code; the user starts it.
- Do not poll `code_capabilities`.

**Acceptance checks.**
- With `getCodeCapabilities` mocked to `enabled: false` and a graph containing one Wrangle, the banner renders with `Show 1 code nodes`, the Cook button is disabled with the tooltip, and no `=` button is in the DOM.
- With the same mock and a graph without code, no banner renders and the Tab menu's Wrangle row has the `disabled` tag and does not place a node on `Enter`.
- A BotCard with `pause_reason: 'code_disabled'` renders the sentence starting `Paused: code nodes are disabled` and an `Open graph` link when `graph_id` is set.
- `hasCode(graph)` returns true for each of: an `{expr}` param, a non-empty `code`, a `wrangle` node; false otherwise.

---

## Mapping to the plan's surface list

| Surface | Plan item | Section |
|---|---|---|
| S31 Network frame | 5.E | S31 |
| S32 Output Group header, terminals inside | 5.E | S32a, S32b, S32c (reference Ticker, 5.C frontend touch) |
| S33 Per-group tabs + Combined | 5.E | S33 |
| S34 Spawn dialog | 5.F | S34 |
| S35 BotCard graph line + update | 5.F | S35 |
| S36 AddBotBar group selector | 5.F | S36 |
| S37 Breadcrumb + dive | 6.C | S37 |
| S38 Subnet card + boundary nodes | 6.C | S38 |
| S39 Collapse into subnet | 6.D (op) + 6.C (command) | S39 |
| S40 Promote param | 6.D | S40 |
| S41 Save as asset / Promote to palette | 6.D | S41 |
| S42 Asset Manager | 6.D | S42 |
| S43 Rules category, asset rows, attribute search | 6.D | S43 |
| S44 `=` toggle + ExprInput | 7.D | S44 |
| S45 Code drawer | 7.D | S45 |
| S46 Wrangle body | 7.D | S46 |
| S47 Monaco theme, Python tokens, completion, markers | 7.D | S47 |
| S48 Spare params | 7.D | S48 |
| S49 Code-disabled state | 7.D (+ BotCard) | S49 |

Former open items, closed by the orchestrator on 2026-09-30:
1. **Closed.** BotCard learns the latest rev from the bot summary: `GET /api/bots` returns `graph_latest_rev` (and `graph_name`) per bot, added by 5.D. BotCard never fetches a graph (S35). S36's AddBotBar still fetches `GET /api/graphs/{id}` once per chosen graph to read its groups.
2. **Closed.** `parse_code` diagnostics use Python's 1-based line and 0-based column. The frontend converts to Monaco's 1-based column in `toMonacoRange` (S47). 7.A states the convention in the `parse_code` docstring.
3. **Closed.** `/api/nodebuilder/validate` exposes param-dependency edges as `param_deps` (7.B computes them, 7.C returns them), so S48's `←` "read by" glyph renders.

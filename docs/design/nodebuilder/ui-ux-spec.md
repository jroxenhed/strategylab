# Node Builder UI/UX Specification (v3)

Owner: Fable 5.1, lead UI/UX designer for the node builder. Date: 2026-09-29. Task: F435.
Status: design foundation. Implementers follow this document and copy values from the prototype at `docs/design/nodebuilder/prototype/node-editor-v3.html`. When the prototype and this text disagree, this text wins; report the difference.

Inputs: vision v1 and v2 (`.run/F435/vision/`), the mockup John liked (`node-editor-mockup.html` / `.png`), the current `tokens.css` and `BaseNode.tsx`, the five audits and 33 live screenshots in `.run/F435/audit/`, and the gap analysis (decisions D1 to D10, waves W0 to W8, critic addendum). Every `[SPEC]` item in the wave plan maps to a section here (map at the end).

Plain-English summary: this document says how the node editor looks and behaves. It fixes colors, sizes, fonts, panels, node parts, wires, keys and states. The prototype is a picture of the finished thing, built from the same numbers.

---

## 1. Design principles

1. **Houdini network editor first.** Data flows top to bottom. Nodes are small cards with one output at the bottom and named inputs on the top. Flags sit in a column on the right edge. Tab adds a node at the cursor. Space+drag pans. Everything Houdini users already know works the same way here. When unsure, do what Houdini does, and record any deliberate difference.
2. **Dense graphs must stay readable.** A 60-node graph at 75% zoom is the normal case. Text never goes below 10px at 100% zoom. Color carries category, not decoration. Contrast is at least WCAG AA (4.5:1) for every text that carries meaning.
3. **The stream-and-attributes model is visible at a glance.** Each node shows what it reads (grey `@chips`) and what it adds (tinted `+@chips`). Each wire is labeled with the attributes its consumer reads. Clicking any wire opens the data behind it. Nobody should need to read a document to learn the model; the graph teaches it.
4. **Calm dark theme, one system with the rest of StrategyLab.** Same near-black canvas (`#0b0e14`), same Geist / Geist Mono fonts, same green "run" accent family. The node builder is a room in the same house, not a different app.
5. **Speed over decoration.** No blur, no per-node shadows on the canvas, no animation longer than 150 ms, no hover effects that need JavaScript. Pan and zoom hold 60 fps with 100 nodes. If a visual costs frames, cut the visual.
6. **Nothing is silent.** Every state that changes a result (bypass, stale, error, unsaved, code-mode param) has a visible mark on the node, in the status bar, or both.

---

## 2. Tokens

All tokens live on `.nodebuilder-root` in `frontend/src/features/nodebuilder/tokens.css`. Existing names are kept; values move to hex so the node builder and the rest of the app (`index.css`, hex) share one way of reading a color. New names are added. Rendered result must match the prototype within 1 hex step.

### 2.1 Surfaces

| Token | Value | Use |
|---|---|---|
| `--nb-bg` | `#0b0e14` | Canvas background. Same as app `--bg-main`. |
| `--nb-bg-grid` | `rgba(255,255,255,0.06)` | Dot grid. 1px dots, 24px pitch, drawn at zoom >= 0.5 only. |
| `--nb-bg-panel` | `#11151d` | Toolbar, Inspector, Data Sheet, status bar, context menus. |
| `--nb-bg-elevated` | `#161b25` | Popovers: Tab menu, dropdowns, tooltips. |
| `--nb-bg-node-top` | `#1a2029` | Node header gradient start. |
| `--nb-bg-node-bottom` | `#141920` | Node body / gradient end. |
| `--nb-bg-node` | `linear-gradient(180deg, var(--nb-bg-node-top) 0, var(--nb-bg-node-bottom) 28px)` | Node card. The gradient stops at the header height so bodies are flat. |
| `--nb-bg-input` | `#0a0c10` | Text inputs, selects, code fields. |
| `--nb-bg-hover` | `rgba(255,255,255,0.04)` | Row hover in menus and the Inspector. |
| `--nb-bg-active` | `rgba(255,255,255,0.08)` | Pressed / active row. |
| `--nb-bg-chip-read` | `#242930` | Grey read chip. |
| `--nb-bg-sheet-row-alt` | `rgba(255,255,255,0.02)` | Data Sheet zebra rows. |

### 2.2 Borders and focus

| Token | Value |
|---|---|
| `--nb-border` | `#232a36` |
| `--nb-border-strong` | `#2f3848` |
| `--nb-border-subtle` | `#1b212b` |
| `--nb-border-focus` | `#38bdf8` |
| `--nb-selection` | `#38bdf8` (same hue as display flag; selection is an outer ring, display is an inner outline, see 4.9) |
| `--nb-selection-soft` | `rgba(56,189,248,0.25)` |
| `--nb-marquee-fill` | `rgba(56,189,248,0.08)` |

### 2.3 Text

| Token | Value | Contrast on node body `#141920` | Rule |
|---|---|---|---|
| `--nb-text` | `#eef1f6` | 16.6 | Values, names. |
| `--nb-text-secondary` | `#c3c9d6` | 11.3 | Param values in muted rows, Inspector body. |
| `--nb-text-muted` | `#98a1b3` | 7.2 | Labels, read chips, wire labels, subtitles. |
| `--nb-text-dim` | `#7a8296` | 4.6 | Placeholders and hints only. Never for a value. |
| `--nb-text-on-color` | `#0b0e14` | >= 6.5 on every category color | Glyph inside the category chip. |

### 2.4 Category colors

One color per category. Used for: the node's left stripe, the glyph chip, the tinted write chips, the input port rings, the Tab menu swatch, the minimap. Text in a category color sits on the node body (>= 6.3:1) or on its own tint (>= 4.68:1). Tint = the category color at 16% over `--nb-bg-node-bottom`.

| Category | Token | Color | Tint (chip bg) | Glyph |
|---|---|---|---|---|
| Tickers | `--nb-cat-ticker` | `#22d3ee` | `#163741` | `T` |
| Data | `--nb-cat-data` | `#60a5fa` | `#202f43` | `D` |
| Indicators | `--nb-cat-indicator` | `#34d399` | `#193733` | `I` |
| Comparisons | `--nb-cat-comparison` | `#fbbf24` | `#393421` | `C` |
| Logic | `--nb-cat-logic` | `#fb923c` | `#392c24` | `L` |
| Math & Signal | `--nb-cat-signal` | `#2dd4bf` | `#183739` | `Σ` |
| Rules | `--nb-cat-rules` | `#f87171` | `#38272d` | `R` |
| Settings | `--nb-cat-settings` | `#a3adc2` | `#2b313a` | `S` |
| Code / Wrangle | `--nb-cat-code` | `#c084fc` | `#302a43` | `{}` |
| Outputs | `--nb-cat-output` | `#f1f5f9` | `#373c43` | `O` |
| Networks | `--nb-cat-network` | `#818cf8` | `#252b43` | `N` |

Networks is new. It covers subnet nodes, Output Group frames use Outputs white, and network boxes use a user tint (default `--nb-cat-network`). Data blue and Networks indigo are 25 degrees apart; they never sit in the same role (Data is a node stripe, Networks is mostly a frame), so this is acceptable. Do not add a twelfth category without re-checking the wheel.

### 2.5 Semantic colors

| Token | Value | Use |
|---|---|---|
| `--nb-flag-display` | `#38bdf8` | Display flag dot, display outline. |
| `--nb-flag-bypass` | `#facc15` | Bypass flag dot, bypass bar. |
| `--nb-flag-off` | `#2f3848` | Unlit flag dot. |
| `--nb-error` | `#f87171` | Diagnostics error, invalid wire, invalid field. |
| `--nb-warn` | `#fbbf24` | Diagnostics warning, stale. |
| `--nb-ok` | `#34d399` | Cook done, saved. |
| `--nb-accent-primary` | `#3fb950` | Run / Cook button, primary actions. Matches app `--gh-green-bright`. |
| `--nb-accent-primary-bg` | `#1a3a2a` | Run button fill. |
| `--nb-code-expr` | `#fcd34d` | Expression text in a code-mode param. |
| `--nb-code-keyword` | `#c084fc` | `@attr` and `ch*()` tokens in code. |
| `--nb-code-number` | `#7dd3fc` | Numbers in code. |
| `--nb-code-comment` | `#6b7386` | Comments in code (decorative, exempt from AA). |
| `--nb-bool-true` | `rgba(52,211,153,0.35)` | Data Sheet true cell fill. |
| `--nb-bool-false` | `transparent` | Data Sheet false cell. |
| `--nb-note-bg` | `#301d12` | Sticky note fill. |
| `--nb-note-border` | `rgba(217,119,6,0.4)` | Sticky note border. |
| `--nb-note-text` | `#fde68a` | Sticky note text (12.9:1). |

### 2.6 Wires

| Token | Value |
|---|---|
| `--nb-wire` | `#4d5666` |
| `--nb-wire-hover` | `#7dd3fc` |
| `--nb-wire-selected` | `#38bdf8` |
| `--nb-wire-invalid` | `#f87171` |
| `--nb-wire-drag` | `#7dd3fc` |
| `--nb-wire-width` | `1.5px` |
| `--nb-wire-width-hot` | `2px` |
| `--nb-wire-label` | `#98a1b3` |
| `--nb-wire-label-halo` | `#0b0e14` (3px paint-order stroke) |

### 2.7 Type

Families are already loaded in `index.css`.

| Token | Value |
|---|---|
| `--nb-font-sans` | `"Geist", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif` |
| `--nb-font-mono` | `"Geist Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace` |
| `--nb-fs-10` | `10px` (chips, wire labels, glyph, port labels; `line-height: 14px`) |
| `--nb-fs-11` | `11px` (param rows, status bar, Inspector labels; `line-height: 16px`) |
| `--nb-fs-12` | `12px` (node name, breadcrumb, Inspector values, menu rows; `line-height: 16px`) |
| `--nb-fs-13` | `13px` (Tab menu search, dialog body; `line-height: 18px`) |
| `--nb-fs-14` | `14px` (panel titles; `line-height: 20px`) |
| `--nb-fw-regular` | `400` |
| `--nb-fw-medium` | `500` |
| `--nb-fw-semibold` | `600` |
| `--nb-caps` | `text-transform: uppercase; letter-spacing: 0.08em; font-size: 10px; font-weight: 600` (section titles, box labels) |

Mono is used for: everything that is an identifier or a value (`@attr`, params, paths, numbers, key caps, coordinates). Sans is used for: names, sentences, buttons, menu labels.

### 2.8 Spacing, radius, elevation, motion

| Token | Value |
|---|---|
| `--nb-space-1` .. `--nb-space-6` | `2px 4px 6px 8px 12px 16px` |
| `--nb-grid` | `24px` (dot pitch and snap grid) |
| `--nb-radius-node` | `6px` |
| `--nb-radius-pill` | `3px` |
| `--nb-radius-card` | `6px` |
| `--nb-radius-menu` | `8px` |
| `--nb-radius-frame` | `8px` (Output Group frame, network box) |
| `--nb-shadow-popover` | `0 12px 32px rgba(0,0,0,0.5), 0 0 0 1px var(--nb-border-strong)` (menus and dialogs only, never nodes) |
| `--nb-shadow-panel` | `none` (panels use borders) |
| `--nb-motion-fast` | `80ms` (hover color, flag dot) |
| `--nb-motion-base` | `150ms` (panel collapse, menu open, selection ring) |
| `--nb-ease` | `cubic-bezier(0.2, 0, 0, 1)` |

`prefers-reduced-motion: reduce` sets both durations to 0 and stops the cooking bar animation (it becomes a static 2px bar).

---

## 3. Workspace layout

### 3.1 Regions

```
+----------------------------------------------------------------------------------+
| App nav (44px, owned by App.tsx)                                                 |
+---------+-------------------------------------------------------+------+---------+
| App     | Toolbar (36px): breadcrumb | graph name • | actions   | Insp | App     |
| left    +-------------------------------------------------------+ ector| right   |
| sidebar | Canvas                                                |      | rail    |
| 224px   |                                                       | 300  | 40px    |
|         |                                                       |  px  |         |
|         +-- chart split handle (22px when collapsed) -----------+      |         |
|         | Chart pane (collapsed by default)                      |      |         |
|         +-------------------------------------------------------+      |         |
|         | Data Sheet drawer (240px, collapsible)                |      |         |
+---------+-------------------------------------------------------+------+---------+
| Status bar (22px)                                                                |
+----------------------------------------------------------------------------------+
```

- **Toolbar** (36px, `--nb-bg-panel`, 1px bottom border). Left: breadcrumb. Center: graph name, unsaved dot, `rev N` in muted mono. Right, in this order: `Auto cook` toggle (switch, 28x16), `Cook` / `Run backtest` button (primary), `Spawn bots…`, `Save` (⌘S), overflow `⋯` (New, Open…, Save as…, Rename, Duplicate, Export JSON, Import JSON, Delete), then two panel toggles (Inspector, Data Sheet) as 28x28 icon buttons with pressed state `--nb-bg-active`. Diagnostics count sits left of `Cook` as a chip: red `● 1` or amber `▲ 2`; click opens the Inspector Diagnostics tab.
- **Breadcrumb** (in the toolbar, mono 12px). The first crumb is the graph name and stands for the root `/`; then `›` and one crumb per network level (`pair_aapl_msft › long_leg › regime`). Current crumb is `--nb-text`, others `--nb-text-muted` and clickable. Next to the crumbs: the unsaved dot and `rev N`. Hover underlines. Right-click a crumb: "Open in place", "Frame in parent". Networks deeper than 4 levels collapse the middle to `…` with a dropdown. Width limit 40% of the toolbar; overflow ellipsis on the leftmost crumbs first.
- **Canvas.** Fills the remaining space. Dot grid `--nb-bg-grid`, 24px pitch, hidden below 50% zoom. Minimap 160x100 bottom-left, 8px inset, nodes drawn as filled rects in category color at 70% alpha, viewport rect 1px `--nb-selection`. Zoom controls are not drawn (wheel, H, F and the status-bar zoom value replace them). React Flow attribution stays in the bottom-right corner in `--nb-text-dim`.
- **Inspector** (right panel). Width 300px default at 1600. Resizable by a 6px handle on its left edge (cursor `col-resize`), min 240, max 520. Collapses to 0 (toolbar toggle or `P`). Persisted per browser in `localStorage['nb.inspector']` (width, open).
- **Data Sheet** (bottom drawer). Height 240px default. Resizable by a 6px top handle, min 120, max 60% of the canvas column. Toggle `S` or the toolbar button. Persisted in `localStorage['nb.sheet']`.
- **Chart pane** (graph/chart split, D10). Sits between the canvas and the Data Sheet. Default collapsed to a 22px handle bar that reads `▸ Chart` on the left and the last cook summary on the right (`AAPL 1d · 6 trades · +25.8% · Sharpe 1.40`). Open height 35% of the canvas column, min 180px. Toggle by clicking the bar or `Shift+V`. When open the existing `Chart` component renders (moved, not copied; `autoSize` only, no external ResizeObserver, per Key Bugs Fixed).
- **Status bar** (22px, mono 11px, `--nb-text-muted` on `--nb-bg-panel`). Left to right with 14px gaps: zoom `85%`, cursor `1173, 277` (graph units), selection (`spread_z` or `3 nodes` or `no selection`), cook state (`idle` / `cooking 340 ms` with a spinner / `stale` in `--nb-warn` / `cooked 12:04:31` in `--nb-ok`), diagnostics (`1 error · 2 warnings`, click opens Inspector). Right: `auto cook on`, `saved 2 min ago` or `unsaved` in `--nb-warn`, `graph 7f3a @ rev 12`.
- **App sidebars in graph view.** The right app sidebar (watchlist + settings) collapses to a 40px icon rail whenever graph view is active; the graph owns direction, size, stop and costs (ownership table D11), so the settings column is not needed. The left app sidebar stays at 224px at app width >= 1440 and collapses to a 48px rail below that. Both restore when leaving graph view.

### 3.2 Behavior by app width

| App width | Left sidebar | Canvas column | Inspector | Data Sheet | Notes |
|---|---|---|---|---|---|
| 1280 | 48px rail | 1280 − 48 − 40 − 280 = 912px | docked, 280px | 200px default | Toolbar hides button text and shows icons only for Save / Spawn; breadcrumb limited to 30%. Minimap 120x76. |
| 1600 | 224px | 1600 − 224 − 40 − 300 = 1036px | docked, 300px | 240px default | Reference layout. |
| 2560 | 224px | 2560 − 224 − 40 − 360 = 1936px | docked, 360px | 280px default; Data Sheet may dock right of the Inspector as a second column when user drags it there (stretch; not required) | Nothing scales up; more graph is visible. |

Below 1100px app width the Inspector becomes an overlay (absolute, right 0, width 280, `--nb-bg-panel` at 96% opacity) and the Data Sheet default is closed. The editor is not designed for phones.

### 3.3 Resize and collapse rules

- Handles are 6px hit areas drawn as 1px `--nb-border` lines; hover shows `--nb-border-strong`; drag shows `--nb-selection`.
- Double-click a handle resets the panel to its default size.
- Collapsing animates width or height over `--nb-motion-base`; the canvas does not re-fit on collapse (viewport stays), it only re-fits on `H`.
- Panel sizes persist per browser, never in the graph file.

---

## 4. Node anatomy

Reference: the `spread_z` Wrangle node and the `below_entry` Comparison node in the prototype.

### 4.1 Card

- Width: content-fit, min 176px, max 320px. Terminals are 118px. Collapsed nodes are 160px.
- Background `--nb-bg-node`, border 1px `--nb-border`, radius `--nb-radius-node`, `overflow: visible` (ports and flags hang outside), no shadow.
- Left category stripe: 3px, full height, category color, radius `6px 0 0 6px`.

### 4.2 Header (26px)

Padding `0 8px 0 11px`. Flex row, 6px gap:
1. **Glyph chip** 16x16, radius 3, background category color, glyph in `--nb-text-on-color`, mono 700 9px, `user-select: none`.
2. **Name** sans 12px 600 `--nb-text`, ellipsis. This is `Node.name` (the path leaf), editable by double-click (inline input, Enter commits, Esc reverts; validation `[a-z_][a-z0-9_]*`, unique among siblings; invalid shows `--nb-error` border).
3. **Type** mono 10px `--nb-text-muted`, right-aligned, e.g. `rsi`, `crosses_below`, `wrangle`. Hidden when the name equals the type.
4. **Diagnostics badge** (4.8), only when present.
5. **Collapse chevron** 12px, appears on hover only, toggles collapsed state (also key `X`).

### 4.3 Ports

- **Inputs** sit on the top edge, centers at `y = 0`. Evenly spaced: for n inputs, port i is at `x = width * (i + 1) / (n + 1)`. 10px circle, `background: var(--nb-bg)`, 2px ring in the node's category color. Dynamic inputs (Logic, Merge, Wrangle) show one spare empty port with a dashed ring after the last connected one.
- **Port name** (mono 9px, `--nb-text-muted`) is drawn above the port, 12px up, with the canvas halo. Shown always when the node has 2 or more inputs, on hover otherwise. Names come from the catalog `inputs` spec: `a`, `b`, `in0..inN`, `source`.
- **Output** is exactly one, bottom center at `y = height`. 10px circle, filled with the category color, 1px `--nb-bg` ring.
- Hover: scale 1.3 over `--nb-motion-fast`, plus `box-shadow: 0 0 6px <category>` on the hovered port only (this is the one allowed glow; it is on a single element).
- Hit area is 18x18 around each port.
- Terminals (Entry, Exit, Size, Stop, Trailing, Time stop, Regime) have one input and no output. Tickers and Settings have no input. A node with no input has nothing on its top edge.

### 4.4 Param rows (20px each)

Body padding `4px 8px 6px 11px`, rows stacked with 0 gap, then chips. Only params marked `onNode: true` in the catalog show on the node (max 4); the rest live in the Inspector. Each row is a 2-column grid: label (mono 11px `--nb-text-muted`, min 56px) and value (right-aligned, mono 11px `--nb-text`).

| Type | Value cell | Edit interaction |
|---|---|---|
| number | `14`, unit suffix in muted (`2.5 %`) | Click to type (`type="text" inputMode="decimal"`, F278). Drag left/right on the label scrubs: 1 unit per 4px, `Shift` x10, `Alt` x0.1. Enter commits, Esc reverts, blur commits. Invalid input: border `--nb-error`, value kept red until fixed; Run is disabled while any field is invalid. |
| int | same, no decimals | Scrub steps of 1. |
| select | `sma ▾` | Click opens a popover list (`--nb-bg-elevated`, radius 8) at the cell; arrow keys and Enter; type-ahead. |
| bool | 12x12 checkbox, `--nb-selection` when on | Click toggles. Label click toggles too. |
| attr picker | chip-shaped value `@close ▾` in the read-chip style | Popover lists `available_attrs` from `/validate`, grouped by the node that wrote them, each with a dtype tag (`f`/`b`). Free text allowed (typed name shows a warning badge until present). |
| path | `../regime/spy_sma` mono, `--nb-text-secondary` | Click opens a path picker (a tree of the current graph); typed paths are validated; broken paths render `--nb-error` with strikethrough. |
| code-mode toggle | see below | `=` glyph. |

**Code mode (per-param expression, W7).** Every row has a 10px `=` glyph in the 6px gutter between the stripe and the label. It is invisible until row hover (opacity 0 to 1, CSS only). Click it, or press `=` while the row's input is focused, to switch the row to code mode:
- The value cell becomes an expression field: mono 11px, text `--nb-code-expr`, prefixed by a fixed `=` in `--nb-code-keyword`, left-aligned, full row width on a second line if longer than the cell (the row grows to 36px).
- The row's gutter shows a persistent 2px tick in `--nb-cat-code`.
- The Inspector shows the same field with Monaco (single line, the nb-python language: Python plus the @attr sugar) and diagnostics under it.
- Switching back keeps the last literal value.
- The Ticker's `symbol`, `interval` and `source` have no `=` glyph (D1 divergence).

### 4.5 Attribute chips

Row below the params (or below the header for nodes without on-node params), `flex-wrap`, 3px gap, padding-top 4px.
- **Read chip** `@close`: 16px tall, mono 10px 500, padding `1px 5px`, radius 3, bg `--nb-bg-chip-read`, text `--nb-text-muted`. Order: as read by the params. A read whose attribute is missing upstream renders text `--nb-error` and a dotted underline; hovering shows "not present on input".
- **Write chip** `+@rsi`: same box, bg = category tint, text = category color. The `+` is part of the text. Double-click to rename inline (same validation as names; the rename propagates to every downstream reader through `rename_attr` and the wire labels update).
- If a node has more than 6 chips, show the first 5 and a `+3` overflow chip that expands on hover into a popover listing all.
- Chips are not ports. Dragging a chip starts nothing. A hint tooltip on first drag attempt says "Wires start from the ports on the top and bottom edges."

### 4.6 Flags

A column outside the right edge: `right: -14px`, top 8px. Two 8px dots, 6px vertical gap:
- **Display** (top): lit `--nb-flag-display`, unlit `--nb-flag-off`. Only nodes with an output have it. One display node per network. Clicking sets it; clicking the lit dot does nothing (Houdini rule). Key `D`.
- **Bypass** (below): lit `--nb-flag-bypass`. Every node except terminals and Tickers. Key `B` toggles on the selection.
- Dots are visible when lit, on node hover, or when the node is selected. Hit area 16x16. Tooltip after 400 ms: `Display (D)` / `Bypass (B)`.

### 4.7 Sparkline slot

Between params and chips, 28px tall, full inner width, shown only when a cook has produced data for this node and the node is expanded. Drawn on a shared `<canvas>` overlay (8.4), not per-node DOM.
- Numeric attribute (the node's first write): 1px polyline in the category color at 85% alpha, min/max decimated to 96 points, a 1px dotted zero or midline in `--nb-border-strong` when the range crosses it. Last value printed at the right end, mono 9px `--nb-text-muted`.
- Bool attribute: a strip of 2px bars, category color where true, empty where false; `% true` printed at the right end.
- Stale: drawn in `--nb-text-dim` instead of the category color.
- Multi-write nodes (MACD, BB): the first write only; the Inspector shows all.

### 4.8 Diagnostics badge

14x14 circle in the header, before the flags. Error: bg `--nb-error`, glyph `!` in `--nb-text-on-color`. Warning: bg `--nb-warn`, glyph `▲` 8px. Count replaces the glyph when > 1. Hover tooltip lists messages (max 5, then "and N more"). Click selects the node and opens the Inspector Diagnostics section. A node with an error also gets `border-color: rgba(248,113,113,0.6)`.

### 4.9 Visual states

States compose. Precedence for the border: error > selected > display > hover > default.

| State | Card | Extra |
|---|---|---|
| default | border `--nb-border` | |
| hover | border `--nb-border-strong`; flags and `=` glyphs and collapse chevron visible | CSS `:hover` only |
| selected | `box-shadow: 0 0 0 1.5px var(--nb-selection)` (outer ring) | Node is raised to the top of the z-order. Primary selection (the last clicked) additionally gets `0 0 10px var(--nb-selection-soft)`. This is the one node-level shadow and it exists on at most a handful of nodes. |
| display | `outline: 1px solid var(--nb-flag-display); outline-offset: -1px` (inner outline) plus lit dot | Selected + display shows both: inner outline and outer ring. |
| bypassed | body content (params, chips, sparkline) `opacity: 0.45`; header keeps full opacity; a second 3px bar in `--nb-flag-bypass` drawn immediately right of the stripe; lit bypass dot; type label reads `bypassed` in `--nb-flag-bypass` | Wires through it stay normal (pass-through). |
| error | border `rgba(248,113,113,0.6)`; red badge | |
| warning | amber badge only | |
| stale | sparkline in `--nb-text-dim`; nothing else on the node | Status bar and Data Sheet header carry the stale message. |
| cooking | 2px bar along the top inner edge, `--nb-accent-primary`, animating `left` 0 to 100% over 900 ms (single keyframe animation, only on the node currently cooking) | Reduced motion: static bar. |
| collapsed | 26px header only, width 160; chips replaced by a count `3 +@` in the type slot; ports stay | Key `X` or the header chevron. |
| dragging | `cursor: grabbing`; no other change | |
| drop target (wire splice) | the hovered wire goes `--nb-wire-hover` 2px; node unchanged | |

### 4.10 Special nodes

**Subnet node** (category Networks, glyph `N`). Card like any node, plus:
- A second 2px stripe in the category color 2px right of the first (double stripe = contains a network).
- Header type reads `subnet · 7 nodes` (or the asset name and version when instanced from the library: `regime_filter @ v3`, with a small lock glyph when locked).
- Body: promoted params as ordinary rows; then interface chips: reads on the first line, writes on the second.
- Double-click the header, or `I`, dives in. `U` goes up. Inside, boundary nodes render as half-height (26px) cards: `subnet_input in0` with only an output port, `subnet_output out` with only an input port, both in the Networks color with a dashed border.

**Output Group frame** (category Outputs). It is a network rendered expanded in place (critic 22): a frame with children inside, never a dive-only node in W5.
- Frame: border 1px `rgba(241,245,249,0.28)`, background `rgba(241,245,249,0.025)`, radius `--nb-radius-frame`, min 320x120, auto-grows to contain its children plus 16px padding, user-resizable.
- Header tab (26px, top-left, inside the frame): glyph chip `O` in Outputs white with dark glyph; name (`long_leg`) sans 12px 600; direction pill 10px caps (`LONG` on `--nb-ok` tint, `SHORT` on `--nb-error` tint, `SWITCH` on `--nb-cat-network` tint); primary ticker chip `AAPL · 1d` in the Tickers tint; capital weight `1×` mono muted. Click the tab to select the group; the Inspector then shows its params.
- Children: the terminals (`entry`, `exit`, `size`, `stop`, optional `trailing`, `time_stop`, `regime`) laid out left to right by default. Terminals: 118px wide, header with white stripe, glyph `O`, name; body holds either one read chip (entry/exit/regime) or one param row (size `100 %`, stop `2.5 %`) that can be in code mode.
- Missing required terminal: the frame gets an error badge in its tab and a ghost outline (dashed 1px `--nb-error` at 50%) where the terminal would go, labeled `+ exit`. Click the ghost to add it.
- Two groups on one canvas are fine; combined results appear as a `Combined` tab in Results.

**Network box** (annotation). Dashed 1px border in the box tint at 45% alpha, fill at 4%, radius 8, default tint `--nb-cat-network`, palette of 8 tints in the context menu (network, ticker, indicator, comparison, logic, rules, code, neutral). Label: caps 10px 600 in the tint, on a 4px-padded `--nb-bg` tab overlapping the top-left corner, editable by double-click. Resizable at corners (8px grips on hover). Moving the box moves the nodes fully inside it. Boxes never affect evaluation; that is what makes them different from an Output Group frame or a subnet. A box that is the auto-render of a rule-strategy regime is labeled `REGIME` and the compiled regime terminal shows the link.

**Sticky note.** Default 200x88, fill `--nb-note-bg`, border 1px `--nb-note-border`, text `--nb-note-text` sans 12px/1.45, padding `8px 10px`, radius 6, no blur, no shadow. Double-click to edit (textarea in place); plain text, no markdown. Resizable at the bottom-right grip. Notes sit under nodes in z-order. `Shift+N` creates one at the cursor.

---

## 5. Wires

- Path: cubic bezier from the output port straight down and into the input port straight up. Control distance `max(40, 0.45 * |dy|)`; when the target is above the source (a back-edge, which validation flags), control distance `120` so the loop is visible.
- Stroke `--nb-wire` `1.5px`, `stroke-linecap: round`, no arrowheads (direction is always downward; ports tell the story).
- Wires are drawn under nodes and above boxes and notes.

### 5.1 States

| State | Stroke | Width | Notes |
|---|---|---|---|
| default | `--nb-wire` | 1.5 | |
| hover (wire, or either endpoint node hovered) | `--nb-wire-hover` | 2 | CSS on the edge class; node hover sets a class on adjacent edges through the store, debounced by React Flow's own hover handling. |
| selected | `--nb-wire-selected` | 2 | Delete removes it. Shift+click adds to the selection. |
| dragging a new wire | `--nb-wire-drag`, dashed `6 4` | 2 | Follows the cursor from the port. |
| invalid target while dragging | `--nb-wire-invalid`, dashed `4 3` | 2 | Cursor `not-allowed`. Targets that are invalid: same node, an output, a port that would make a cycle, a full non-dynamic port. |
| diagnostic (name clash, missing attr) | `--nb-wire-invalid` at 70%, solid | 1.5 | The label turns `--nb-error`. |
| bypassed source | unchanged | | Pass-through keeps the stream. |

### 5.2 Labels

- Text = the attributes the consumer reads through this wire, in the consumer's param order, joined by `, `. More than two: `@a, @b +3`. Nothing read yet (a new node with unset attr params): `stream` in `--nb-text-dim`. The full accumulated stream is on hover (tooltip: `12 attrs: @open @high … +@spread_z`, each chip colored by the writing node's category) and in the Inspector when the wire is selected.
- Style: mono 10px, fill `--nb-wire-label`, `paint-order: stroke`, stroke `--nb-wire-label-halo` 3px, no box. Anchor middle, baseline centered on the path point.
- Position: at `t = 0.5` along the path.
- **Fan-out de-confliction.** For wires that leave the same output port, sort by target x. If every label text is the same, draw one label at `t = 0.5` on the leftmost wire of the group and none on the others; a selected or hovered wire always keeps its own label. The overlap pass below then moves the label if a node covers it. If they differ, place label i of n at `t = 0.38 + 0.24 * i / max(1, n − 1)` and nudge horizontally by `(i − (n − 1) / 2) * 10px`. After placement, run one pass of overlap rejection on label bounding boxes within the viewport: a label that overlaps a label already placed, or a node card, moves along its path by `+0.08 t`, at most twice, then hides (hover still shows it).
- **Fan-in** at Logic nodes with 3 or more inputs: labels at alternating `t = 0.45` and `t = 0.6`.
- Labels hide below 60% zoom. Selected and hovered wires keep their label at any zoom.

### 5.3 Long-wire middle fade

A wire whose straight-line length exceeds 600 graph units gets a `linearGradient` stroke (userSpaceOnUse, from its start point to its end point): stops `0%` full, `30%` 35% alpha, `70%` 35% alpha, `100%` full. Only the middle fades; the ends stay readable at both nodes. Hovered and selected wires drop the fade. The gradient is created once per long wire and updated only when its endpoints move.

### 5.4 Drag to empty space

- Drag from an **output** and release on empty canvas: the Tab menu opens at the release point, filtered to nodes with at least one input. Choosing a node places it with its top-left 20px below the release point, wires the new node's `in0` (or its single input), and, for nodes with an `attr` param, pre-fills it with the source's first write (`@rsi`), which is exactly what the wire label will then show. `Esc` cancels and removes the dashed wire. `Shift+Enter` places without wiring.
- Drag from an **input** and release on empty canvas: same menu, filtered to nodes with an output; the new node sits 20px above; its output wires into the dragged input.
- Release on a node body (not a port): connects to the first free input; if there is none, an invalid flash (`--nb-wire-invalid` for 150 ms) and nothing happens.
- Drop a **dragged node** on a wire: after hovering the wire for 150 ms it goes hot; releasing splices the node in (`in0` from the source, output to the old target) and keeps the old wire's port. `Cmd` held while dropping disables splicing.
- Reconnect: dragging an existing wire's end near an input (within 18px) detaches and re-targets it; releasing on empty canvas deletes it (Houdini behavior) after a 150 ms invalid flash.

---

## 6. Interaction model and keyboard

The canvas root holds focus. Clicking the pane, a node, or a wire focuses `.nodebuilder-root`; keys work from any of them. Keys are ignored while a text input, textarea, select, or Monaco has focus, except `Esc` (blur and cancel) and `Enter` (commit). Shortcuts are dispatched through the command registry (W1 1.E). Mac shows `⌘`; other platforms use `Ctrl` for every `Cmd` below.

### 6.1 Mouse

| Gesture | Action |
|---|---|
| Left-drag on empty canvas | Marquee select (nodes fully or partially inside, configurable; default partially). `Shift` adds, `Alt` removes. |
| Space + left-drag, middle-drag, or two-finger trackpad pan | Pan. Cursor `grab` / `grabbing`. |
| Wheel | Zoom toward the cursor. Factor 1.08 per 100 px of delta (gentle; the current 600 px step from 1.94 to 0.37 is wrong). Range 0.15 to 3.0. `Cmd`+wheel pans vertically, `Shift`+wheel pans horizontally. |
| Click node | Select only it. `Shift+click` toggles it in the selection. `Cmd+click` also toggles (alias). |
| Drag node | Moves the whole selection; snaps to the 24px grid when snap is on (`Cmd` held inverts snap). Dropping over a wire splices (5.4). |
| Alt+drag node | Duplicates the selection and drags the copy (internal wires kept). |
| Double-click node header | Rename inline. Double-click a subnet or group header: dive. |
| Double-click empty canvas | Opens the Tab menu at the cursor (same as `Tab`). |
| Click wire | Select the wire; the Data Sheet follows it. |
| Right-click | Context menu for the node, wire, or pane (6.4). |
| Drag from port | New wire (5.4). |
| Click flag dot | Set display / toggle bypass. |

### 6.2 Keyboard map

| Key | Action | Scope |
|---|---|---|
| `Tab` | Open the Tab menu at the cursor; with a selected node, the new node is placed under it and wired from its output. | canvas |
| `Space` (hold) + drag | Pan | canvas |
| `Shift+click` | Toggle selection | canvas |
| `Cmd+A` | Select all in the current network | canvas |
| `Esc` | Close menu / cancel wire drag / clear selection / blur field, in that order of priority | global |
| `Delete`, `Backspace` | Delete selection (nodes and wires). Single node with one input and one output: rewire through (Houdini "delete and reconnect"); `Shift+Delete` deletes without rewiring. | canvas |
| `B` | Toggle bypass on the selection | canvas |
| `D` | Set the display flag on the primary selected node | canvas |
| `X` | Collapse / expand the selected nodes | canvas |
| `F` | Frame the selection (fit with 80px padding, 150 ms ease) | canvas |
| `H`, `Home` | Frame all nodes in the current network | canvas |
| `L` | Tidy layout of the selection, or of all when nothing is selected (elkjs, top-down) | canvas |
| `I`, `Enter` on a subnet or group | Dive into the selected network | canvas |
| `U` | Go up one network level | canvas |
| `Shift+C` | Collapse the selection into a new subnet (boundary wiring automatic) | canvas |
| `Shift+N` | New sticky note at the cursor | canvas |
| `Shift+B` | New network box around the selection (or an empty 320x200 at the cursor) | canvas |
| `Cmd+C` / `Cmd+X` / `Cmd+V` | Copy / cut / paste at the cursor (ids remapped, names uniqued, internal wires kept) | canvas |
| `Cmd+D` | Duplicate the selection offset by (24, 24) | canvas |
| `Cmd+Z` / `Cmd+Shift+Z` / `Cmd+Y` | Undo / redo (graph history, not the browser's) | global |
| `Cmd+S` | Save | global |
| `Cmd+Enter` | Cook now (backtest) | global |
| `A` | Toggle auto cook | canvas |
| `S` | Toggle the Data Sheet | canvas |
| `P` | Toggle the Inspector (Houdini: P = parameters) | canvas |
| `Shift+V` | Toggle the chart pane | canvas |
| `G` | Toggle grid snap | canvas |
| `F2` | Rename the primary selected node | canvas |
| `=` | While a param input is focused: switch it to code mode | field |
| `?` | Shortcut overlay | global |
| `Arrow keys` | Nudge the selection 1px; `Shift+Arrow` 24px | canvas |
| `1` .. `9` | Jump to bookmark (stretch) | canvas |

`Q` (template flag) and `R` (render flag) are reserved unused, as in Houdini, so a future flag can take them.

### 6.3 Tab menu anatomy

Popover at the cursor (top-left corner at the cursor, clamped inside the canvas with 8px margin; if it would cover the cursor's node, flip to the left). `--nb-bg-elevated`, radius 8, `--nb-shadow-popover`, width 440px, max height 460px.

```
+------------------------------------------------------------+
| [search input, 36px, 13px sans, autofocus, placeholder     |
|  "Search nodes, @attributes, assets…"]                     |
+---------------+--------------------------------------------+
| Recent      5 | ▣ RSI            Relative strength   INDIC |
| Tickers     1 | ▣ Crosses Below  a crosses below b   COMPA |
| Data        3 | ▣ regime_filter  asset v3 · Rules    RULES |
| Indicators 12 | …                                          |
| Comparisons 8 |                                            |
| Logic       4 |                                            |
| Math&Signal 9 |                                            |
| Rules       2 |                                            |
| Settings    6 |                                            |
| Code        1 |                                            |
| Outputs     7 |                                            |
| Networks    2 |                                            |
| Library     4 |                                            |
+---------------+--------------------------------------------+
| ↵ place and wire   ⇧↵ place only   ↑↓ move   esc close    |
+------------------------------------------------------------+
```

- Left column 140px: category list with counts, current category `--nb-bg-active` with a 2px left bar in the category color. Right column: result rows 32px, 16px glyph chip, name sans 12px 600, description sans 11px `--nb-text-muted` (one line, ellipsis), category caps 10px `--nb-text-dim` right-aligned. Active row `--nb-bg-hover`.
- Search matches name, description, category, and `reads`/`writes` (`@volume` lists everything that reads or writes `@volume`). Fuzzy, ranked as in `search.ts`. Empty query shows Recent (last 5 placed) then the selected category.
- Library assets appear under Library and under their `palette.category`; they show `asset` and the version in the description.
- Items with `compileActive: false` never appear.
- Outside click, `Esc`, or losing focus closes it. Text selection is disabled inside it and on the canvas during a wire drag.

### 6.4 Context menus

`--nb-bg-panel`, radius 8, `--nb-shadow-popover`, width 232px, rows 28px, sans 12px, key caps right-aligned in mono 10px `--nb-text-dim`, separators 1px `--nb-border`, destructive rows in `--nb-error`.

- **Node:** Rename `F2` · Display flag `D` · Bypass `B` · Collapse `X` · ─ · Show data `S` · Frame `F` · ─ · Cut / Copy / Paste / Duplicate `⌘D` · ─ · Collapse into subnet `⇧C` · Save as asset… · Promote parameter ▸ (list of params) · ─ · Delete `⌫` · Delete without rewiring `⇧⌫`.
- **Wire:** Show data `S` · Insert node… `Tab` · ─ · Delete `⌫`.
- **Pane:** Add node… `Tab` · Paste `⌘V` · ─ · Sticky note `⇧N` · Network box `⇧B` · ─ · Frame all `H` · Tidy layout `L` · Snap to grid `G` (checkmark) · ─ · Go up `U` (when inside a network).
- **Group frame:** Rename · Direction ▸ (long / short / switch) · Set primary ticker… · Capital weight… · ─ · Add missing terminals · ─ · Delete group.
- **Box:** Rename · Tint ▸ (8 swatches) · Fit to contents · ─ · Delete box (keeps nodes).
- **Param row (Inspector and node):** Set to default · Copy value · ─ · Use expression `=` · Promote to parent… (inside a subnet) · Add slider (numbers) .

### 6.5 Inspector

Width per 3.1. Header 36px: glyph chip, name (inline editable), type, path in mono muted under the name (`/long_leg/spread_z`), and the two flag dots at the right. Sections are collapsible (caps 10px titles, 28px header rows), state remembered per section:
1. **Parameters.** Every param as a 24px row: label left 96px, control right. Numbers get a slider track under the field when the catalog has `min`/`max` (2px track, 10px thumb, category color). Each row has the `=` gutter glyph, a reset-to-default dot when changed (4px `--nb-selection` dot left of the label), and the context menu from 6.4.
2. **Code.** For built-ins: a collapsed "Code block" (Monaco, min 96px, grows to 320px) with the auto-created spare params listed above it. For Wrangle: the editor is open by default, 240px, resizable. Monaco theme: background `--nb-bg-input`, tokens per 2.5, gutter `--nb-text-dim`, markers red/amber matching diagnostics, minimap off, `fontSize: 12`, `lineHeight: 18`, Geist Mono.
3. **Stream.** Two lists: reads (grey chips) and writes (tinted chips, renameable). Below, "Input stream" shows every attribute available at this node's input(s), grouped by writer, with dtype tags. This is the accumulated stream from the vision's T1 wording.
4. **Diagnostics.** Rows with a severity dot, message, and code; click focuses the offending param or wire.
5. **Notes.** A free text field stored in `Node.meta.note`.

With no selection: **Graph** (name, description, groups list with direction and primary ticker), **Legend** (11 categories with swatches), **Flags** (two rows with dots), **Keys** (the 12 most used, then a "Show all (?)" link). With multiple nodes selected: the count, the shared type if any, and bulk actions (bypass all, collapse all, tidy, collapse into subnet).

With a wire selected: source and target with ports, the label attributes, and the accumulated stream.

### 6.6 Data Sheet

Bottom drawer (3.1). Header row 28px: a target chip (`wire spread_z → below_entry` or `node spread_z` or `display: spread_z` in the display color) with a follow-mode select (`follow selection` / `pin`), a filter input (`only rows where @long_entry`), a row count (`1 258 rows`), `jump to trades ◂ ▸`, and a close button. Table: sticky header, time column first (ET wall clock, same rule as `toET()`), one column per attribute in stream order. Header cells: attribute name mono 11px, dtype tag, and a 10px "written by" chip in the writer's category tint. Cells: mono 11px, numbers right-aligned with 4 significant decimals, bools as a filled `--nb-bool-true` cell with `true` text or an empty cell with `·` in `--nb-text-dim`. Zebra rows. Rows are virtualized (28px). The header also holds a 24px tall per-column histogram strip (numeric columns) when the drawer is at least 200px tall. Stale state: an amber 22px bar above the table, `stale — graph changed · Cook (⌘↵)`.

---

## 7. Global states

| State | What the user sees |
|---|---|
| **Empty graph** | Centered on the canvas, `--nb-text-dim` sans 13px, three lines: `Press Tab to add a node`, `Start with a Ticker, then indicators, comparisons, and an Output Group`, `or ⋯ › Open to load a saved graph`. Below them a ghost Ticker card (dashed border, 40% opacity) that is a click target for "Add Ticker". The status bar reads `empty graph`. |
| **Loading a graph** | Toolbar shows the name in `--nb-text-dim` with a 12px spinner; canvas shows nothing (no skeletons); status bar `loading…`. If longer than 2 s, a one-line notice under the toolbar. |
| **Cooking** | Status bar spinner + `cooking… 340 ms` (elapsed updates every 100 ms). The Cook button shows a square stop glyph and cancels on click. The node currently cooking shows the 2px top bar (4.9). Auto cook debounces 500 ms after the last commit. |
| **Cooked** | Status bar `cooked 12:04:31` in `--nb-ok` for 3 s then muted. Sparklines and the Data Sheet refresh. Results publish to the chart pane and Results panel (`origin: 'graph'`, never into `lastRequest`). |
| **Stale** | Any commit after a cook: status bar `stale` in `--nb-warn`; Data Sheet amber bar; chart-pane summary gets the prefix `stale ·`; sparklines dim. Auto cook clears it on its own. |
| **Validation errors** | Node badges; toolbar chip `● 2`; Cook button disabled with tooltip `Fix 2 errors to cook`; status bar `2 errors`. Errors from `/validate` arrive debounced 300 ms after each commit; never block typing. |
| **Server / network failure** | A 32px banner under the toolbar, `--nb-error` tint at 12% with `--nb-error` text: the server `detail` text, the offending node name as a link (selects it), and `Retry`. Never the axios message. |
| **Unsaved changes** | A 6px `--nb-warn` dot after the graph name; status bar `unsaved`; `beforeunload` prompt; New / Open / switching graph asks `Save changes to <name>?` with Save / Discard / Cancel. A draft autosaves to `localStorage` every 5 s; on reload with a newer draft, a banner offers `Restore draft` / `Discard`. |
| **Save conflict (409)** | Banner: `Saved elsewhere at 12:01 (rev 13). Reload theirs · Save as copy`. |
| **Read-only (auto-render of a rule strategy)** | Toolbar shows a `VIEW` pill in `--nb-text-muted` and an `Edit this graph` button; nodes have no `=` glyphs, flags, or editable fields; params show as label/value rows. Editing creates a copy named after the strategy. Banners list dropped or unsupported parts (`Regime moved into a network`, `Unsupported: stochastic rising`). |
| **Cook in a bypassed / no-display network** | If no node has the display flag, the Data Sheet follows the selection; if nothing is selected it shows `Select a node or wire, or set a display flag (D)`. |

---

## 8. Performance rules for implementers

1. **No per-node filters, blur, or `backdrop-filter`.** The only blur in the editor is none. Shadows are allowed on popovers and on the primary selected node only.
2. **Hover is CSS.** Node hover, port hover, flag visibility, `=` glyph visibility, and wire hover all come from `:hover` and class toggles that React Flow already applies. No React state updates on pointer move over nodes.
3. **One canvas for sparklines.** Sparklines are drawn on one absolutely positioned `<canvas>` in the viewport layer, transformed with the flow. Redraw only on cook, on zoom end, and on node move end (not per frame). Budget: 100 sparklines in under 4 ms. Below 50% zoom, skip sparklines.
4. **Node memo and signature.** Node components are `React.memo` with a data signature that excludes `selected`, `dragging`, and viewport. Selection changes touch only the nodes whose selection changed.
5. **Wire labels and fades are cheap.** Labels are SVG `<text>` with `paint-order`, no `<rect>` boxes, no filters. Long-wire gradients exist only for wires over 600 units and are recomputed on drag end, not during drag. Hide labels below 60% zoom.
6. **Data Sheet is virtualized** (row height 28px, overscan 8) and never holds more than 2 000 rows in memory; it pages through `/inspect`.
7. **Cook and validate never block typing.** `/validate` is debounced 300 ms, `/preview` 500 ms, both cancel in-flight requests on a new commit.
8. **Grid and minimap.** The dot grid is a CSS `radial-gradient` background on the pane, hidden below 50% zoom. The minimap draws rects only.
9. **Animation whitelist.** Only these animate: panel collapse (150 ms), Tab menu open (opacity 80 ms), selection ring (80 ms), the cooking bar. Nothing animates continuously while idle.
10. **Frame budget.** Pan and zoom with 100 nodes and 150 wires must stay at p95 <= 20 ms per frame on the dev Mac; the render probe records it. Any regression over 5% is a blocker.
11. **Text stays crisp.** No `transform: scale` on text below 0.5; React Flow's viewport transform is the only transform. No fractional pixel positions on ports (round to 0.5).
12. **Fonts.** Geist and Geist Mono are already loaded; no extra font requests. `font-display: swap` is fine because the metrics are close to the fallbacks.

---

## Mapping to the wave plan `[SPEC]` items

| Item | Section here |
|---|---|
| 1.F Persistence UI (toolbar, Graph Browser, drafts) | 3.1 toolbar, 7 unsaved / conflict / loading |
| 1.G Diagnostics UI | 4.8, 7 validation errors, 6.5 Diagnostics |
| 2.E Ports, attr picker, chips, wire labels | 4.3, 4.4, 4.5, 5.2 |
| 3.A Inspector | 6.5 |
| 3.B Flags | 4.6, 4.9 |
| 3.E Boxes and notes | 4.10 |
| 3.G Wire ops and menus | 5.4, 6.4 |
| 3.H Status bar, `?` overlay, minimap | 3.1, 6.2 |
| 4.B Data Sheet | 6.6 |
| 4.C Sparklines and auto cook | 4.7, 7 cooking / stale, 8.3 |
| 4.D Results and split view | 3.1 chart pane, 7 cooked |
| 5.E Output Group UI | 4.10 |
| 5.F Spawn dialog | 3.1 toolbar (button); dialog body to be specified with W5 |
| 6.C Dive and breadcrumb | 3.1 breadcrumb, 6.2 `I`/`U`, 4.10 subnet |
| 6.D Collapse, promote, Asset Manager | 6.2 `Shift+C`, 6.4 param menu, 6.5 |
| 7.D Code UI | 4.4 code mode, 6.5 Code, 2.5 code tokens |

Not covered here and to be specified when their wave starts: the Graph Browser dialog body, the Spawn dialog body, the Asset Manager, the `?` overlay layout (use the Keys table in 6.2 as its content).

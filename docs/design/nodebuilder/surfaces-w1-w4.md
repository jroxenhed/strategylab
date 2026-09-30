# Node Builder surface specs, waves W1 to W4 (S01 to S30)

Owner: Fable 5.1, lead UI/UX designer for the node builder. Date: 2026-09-30. Task: F435.
Status: build-ready. Each surface below is written so one implementer can build it from its section alone.

Inputs: the foundation `docs/design/nodebuilder/ui-ux-spec.md` (tokens, node anatomy, wires, keys, global states), the prototype `docs/design/nodebuilder/prototype/node-editor-v3.html`, the plan of record `docs/plans/2026-09-29-node-builder-finish-plan.md` (surface list S01 to S30, API contracts in its sections 4 and 6), the mockup John liked (`.run/F435/vision/node-editor-mockup.png`), and the audits in `.run/F435/audit/`.

Plain-English summary: the foundation says what everything looks like. This file says, surface by surface, what to build, how it behaves, what it says, and how to check it. Surfaces S31 to S49 (waves W5 to W7) live in `surfaces-w5-w7.md`.

How to read a surface: **Purpose** (why it exists), **Placement** (where it mounts, which slot, which file), **Anatomy** (parts, sizes, tokens), **States**, **Interactions and keys**, **Copy** (exact strings), **Accessibility**, **Must not** (traps), **Acceptance** (checks a test or a screenshot can confirm). Token names are from the foundation section 2. Every size is in CSS px at 100% zoom. `Cmd` means `⌘` on Mac and `Ctrl` elsewhere.

Rule of precedence: the plan's API contracts win over this file. This file wins over the prototype. The foundation wins over this file except where a "Foundation amendment" below says otherwise.

---

## Foundation amendments

These change or sharpen the foundation. Apply them when the wave that owns the surface starts.

**A1. Hint bar and Reset view exist (mockup parity, critic 14).** The foundation did not place them. They live in the toolbar (S22): the hint text sits in the free middle of the toolbar, right of the graph name, only when the toolbar is at least 1440px wide; `Reset view` is a toolbar button placed first in the right cluster, before `Auto cook`. `Reset view` runs the same command as `H` (frame all). The toolbar order in foundation 3.1 becomes: `Reset view` · `Auto cook` · diagnostics chip · `Run backtest` · `Spawn bots…` · `Save` · `⋯` · Inspector toggle · Data Sheet toggle.

**A2. React Flow `Controls` are removed, not restyled.** The foundation says zoom controls are not drawn. The plan (3.H) says restyle them. This file follows the foundation: remove `<Controls>`; `Reset view`, `H`, `F`, the wheel and the status-bar zoom value replace it. This closes audit bug B10 (white-on-white controls) for good. If the orchestrator decides to keep `Controls`, S22 has a fallback restyle spec; use it and nothing else.

**A3. The toolbar diagnostics chip opens a list popover first.** Foundation 3.1 says the chip opens the Inspector Diagnostics tab. The chip ships in W1 and the Inspector ships in W3, and the Inspector's Diagnostics section is per node, not per graph. So: click the chip to open a graph-wide list popover (S05). A row in that popover selects the node and, from W3 on, opens the Inspector with its Diagnostics section expanded.

**A4. Two kinds of cook.** The foundation's "Cooking" and "Stale" rows treat cook as one thing. There are two: a **preview cook** (`POST /preview`, triggered by auto cook or by selecting a node with no cached data) that refreshes sparklines and the Data Sheet, and a **backtest cook** (`POST /backtest`, the `Run backtest` button, `Cmd+Enter`) that also refreshes trades, equity, the chart and the Results panel. Auto cook only ever runs the preview cook. A preview cook clears `stale` on sparklines and the Data Sheet; the chart-bar summary and the Results header stay `stale ·` until the next backtest cook. The status bar shows the most recent cook of either kind (S20, S27).

**A5. Storage keys.** All per-browser keys start with `nb.`: `nb.inspector`, `nb.sheet`, `nb.split`, `nb.autocook`, `nb.hint`, `nb.seeded`, `nb.draft.<graphId>`, `nb.draft.new`, `nb.recentNodes`. Every read and write is wrapped in try/catch and the page works without storage.

---

## 0. Shared parts

Several surfaces use the same pieces. Build each once (suggested files in parentheses) and reference it.

### 0.1 Buttons (`ui/Button.tsx` or plain classes in `tokens.css`)

| Kind | Size | Style |
|---|---|---|
| default | height 26, padding `0 10px`, radius 6 | border 1px `--nb-border-strong`, bg `--nb-bg-elevated`, text `--nb-text` sans 500 12px; hover bg `#1c222d`; active bg `--nb-bg-active` |
| primary | same | bg `--nb-accent-primary-bg`, border `#2a5a3a`, text `#7ee2a0`; hover border `--nb-accent-primary` |
| danger | same | bg `rgba(248,113,113,0.12)`, border `rgba(248,113,113,0.4)`, text `--nb-error` |
| icon | 28x28, padding 0 | as default; pressed (`aria-pressed=true`) bg `--nb-bg-active`, border `--nb-border-focus` |
| text | height 22, padding `0 4px`, no border | text `--nb-text-secondary`; hover `--nb-text`, underline |

Key caps inside a button: mono 10px `--nb-text-dim`, 6px left of the label's end. Disabled: opacity 0.45, `cursor: not-allowed`, the `title` carries the reason. Focus: `:focus-visible { outline: 2px solid var(--nb-border-focus); outline-offset: 1px }`. Never a focus ring on plain click.

### 0.2 Switch

28x16 track, radius 8. Off: track `--nb-border-strong`. On: track `--nb-accent-primary`. Knob 12px `--nb-bg`, 2px inset, moves over `--nb-motion-fast`. Label to the left, sans 11px `--nb-text-muted`. `role="switch"`, `aria-checked`, Space toggles.

### 0.3 Dialog shell (`ui/Dialog.tsx`)

- Backdrop `rgba(11,14,20,0.6)` over the whole app. Dialog centered, `--nb-bg-elevated`, border 1px `--nb-border-strong`, radius `--nb-radius-menu`, `--nb-shadow-popover`. Widths per surface; max `min(90vw, 960px)`; max height 80vh with a scrolling body.
- Header 44px: title sans 14px 600 `--nb-text`, close `✕` icon button at the right.
- Body padding 16px, sans 13px/18px `--nb-text-secondary`. Inputs: height 28, bg `--nb-bg-input`, border 1px `--nb-border`, radius 6, padding `0 10px`, mono 12px for identifiers, sans 13px for sentences; focus border `--nb-border-focus`; invalid border `--nb-error` with the message under the field in `--nb-error` 11px.
- Footer 44px: buttons right-aligned, 8px gap, Cancel left of the primary. Destructive primary uses the danger kind.
- `role="dialog"`, `aria-modal="true"`, `aria-labelledby` the title. Focus trap. Initial focus: the first input, else the primary button. `Esc` = Cancel. `Enter` = primary when focus is not in a textarea. On close, focus returns to the element that opened it.
- Opens with opacity 0 to 1 over `--nb-motion-fast`. No other animation.

### 0.4 Popover shell (`ui/Popover.tsx`)

`--nb-bg-elevated`, border 1px `--nb-border-strong`, radius `--nb-radius-menu`, `--nb-shadow-popover`, padding 4px. Anchored to an element or to a screen point; clamped inside the canvas column with an 8px margin; flips above when it would leave the bottom. Closes on outside pointer-down, `Esc`, window blur, and canvas zoom or pan. One popover at a time. `role` per use (`listbox`, `menu`, `dialog`).

### 0.5 Banner shell

See S07. Every notice under the toolbar uses it.

### 0.6 Relative time

`just now` (< 10 s), `N s ago` (< 60 s), `N min ago` (< 60 min), `N h ago` (< 24 h), `yesterday`, else `YYYY-MM-DD`. Refresh every 30 s while visible.

### 0.7 Number format for data cells

|v| >= 1000: no decimals, thin-space thousands (`48 213 400`). Otherwise 4 decimals, trailing zeros kept (`41.2000`). `NaN` and `null` print `nan` in `--nb-text-dim`. Negative numbers keep the minus sign; no color coding in data cells (color codes only P&L in Results).

### 0.8 Canvas focus

`.nodebuilder-root` has `tabIndex={0}` and no visible focus ring of its own (its focus is the canvas). Any pointer-down inside the canvas, on a node, a wire, the minimap or the status bar calls `root.focus()`. Every key command is read from the command registry with the scope rules of foundation 6. This fixes audit bug 13 (keys dead after a wire click or toolbar click).

---

# Wave 1 surfaces

## S01. Graph toolbar

**Purpose.** One 36px row that names the graph, shows whether it is saved, and holds every graph-level action. It is the Houdini pane header for this network editor.

**Placement.** The top of the canvas column, under the app nav, full width of the column. Component `GraphToolbar.tsx`, mounted by `NodeBuilder.tsx`. From W3 on, its right cluster is filled through the `toolbarRight` slot with these `order` values: Reset view 10, Auto cook 20, diagnostics chip 30, Run 40, Spawn bots 50, Save 60, overflow 70, Inspector toggle 80, Data Sheet toggle 90. Items that ship later leave a gap; nothing shifts when they arrive.

**Anatomy.** Height 36, `--nb-bg-panel`, border-bottom 1px `--nb-border`, padding `0 10px`, flex row, 8px gap.
1. **Name crumb** (mono 12px `--nb-text`), left. In W1 this is the graph name alone; W6 turns it into the breadcrumb. Click: rename inline (input in place, mono 12, width fits, min 80). Untitled graphs read `untitled` in `--nb-text-dim`.
2. **Unsaved dot** 6px circle `--nb-warn`, 6px right of the name, `title="unsaved changes"`. Hidden when clean.
3. **Rev** mono 10px `--nb-text-muted`: `rev 12`. Hidden for untitled graphs.
4. **VIEW pill** (read-only graphs only): 16px tall, caps 10px 600, `--nb-text-muted` on `--nb-bg-active`, radius 3, `title="Auto-rendered from a rule strategy. Edit this graph to make a copy."`.
5. **Hint text** (S22) in the flexible middle, `--nb-text-dim` sans 11px, only when the toolbar is >= 1440px wide.
6. **Right cluster**, in the order above. Buttons per 0.1:
   - `Reset view` default button, key cap `H` (S22).
   - `Auto cook` switch (S27).
   - Diagnostics chip (S05): height 26, mono 11px, padding `0 8px`, border 1px `--nb-border`, radius 6; `● 1` in `--nb-error` and `▲ 2` in `--nb-warn` with 8px between them. Hidden when both are zero.
   - `▶ Run backtest` primary, key cap `⌘↵`. While running: label `■ Stop` and a 12px spinner left of the glyph.
   - `Spawn bots…` default (W5; renders nothing before W5).
   - `Save` default, key cap `⌘S`.
   - `⋯` icon button, `title="More"`. Opens the overflow menu (context-menu style, foundation 6.4, 232px): `New` · `Open… ⌘O` · `Save as…` · `Rename…` · `Duplicate` · ─ · `Export JSON` · `Import JSON…` · ─ · `Delete…` (danger).
   - Inspector toggle icon `▥`, `title="Inspector (P)"`, pressed when open (W3).
   - Data Sheet toggle icon `▤`, `title="Data sheet (S)"`, pressed when open (W4).

At toolbar width < 1440: `Save` and `Spawn bots…` and `Reset view` show icons only (`⤓`, `⇪`, `⌖`) with the label in `title`; the primary keeps its label; the hint text hides.

**States.**
| State | Toolbar |
|---|---|
| clean, saved | name, `rev N`, no dot; `Save` disabled with `title="No changes to save"` |
| dirty | dot, `Save` enabled; status bar `unsaved` |
| untitled (new graph, never saved) | name `untitled` dim, no rev; `Save` opens Save as |
| saving | `Save` disabled, label `Saving…` with a 12px spinner; other actions stay enabled except New/Open/Delete |
| loading a graph | name in `--nb-text-dim` with a 12px spinner; Run, Save, `⋯` disabled |
| running (backtest cook) | `■ Stop` primary; Save stays enabled |
| errors > 0 | chip shows `● N`; Run disabled, `title="Fix N errors to run"` (singular `Fix 1 error to run`) |
| no nodes | Run disabled, `title="Add a Ticker and an Output to run"` |
| read-only (`readOnly: true`) | VIEW pill; right cluster shows only `Reset view`, `Edit this graph` (primary), Inspector toggle; Save, Run, `⋯`, Auto cook are not rendered |
| server error on save | banner S07 error; toolbar unchanged, still dirty |

**Interactions and keys.**
- `Cmd+S` Save. Untitled: opens the name dialog (below) then creates (`createGraph`), else `saveGraph(id, {rev, graph})`. On success: `dirty=false`, `rev` updates, status bar `saved just now`, draft key removed (S03). On 409 `rev_conflict`: S04. On 409 `name_taken`: name dialog re-opens with the error under the field.
- `Cmd+O` Open: Graph Browser (S02). If dirty first ask **Save changes?** (dialog 360: title `Save changes to <name>?`, body `Your unsaved edits will be lost if you discard them.`, buttons `Discard` (danger, left), `Cancel`, `Save` (primary)).
- `New`: same dirty check; then an empty graph, `graphMeta = {id: null, rev: null, name: 'untitled'}`, history cleared, viewport reset, empty state (foundation 7).
- `Save as…`, `Rename…`, `New` from the browser: the **name dialog** (0.3, width 360): title `Save as` / `Rename graph` / `New graph`; one input labeled `Name`, prefilled with the current name (`Save as` appends ` copy`); helper text `1 to 80 characters. Names are unique.`; errors: empty → `Enter a name.`, > 80 → `Use 80 characters or fewer.`, taken → `A graph named "<name>" already exists.`; primary `Save` / `Rename` / `Create`.
- `Duplicate`: `createGraph({name: '<name> copy', duplicate_of: id})`, then opens the copy. Duplicating a dirty graph first asks to save (the server copies the saved version).
- `Export JSON`: downloads `<name>.graph.json` containing the `GraphEnvelope` as returned by the server plus the current unsaved graph (so what you see is what you export). Untitled graphs export `{name, graph}` only.
- `Import JSON…`: hidden `<input type="file" accept="application/json,.json">`. Accepts a `GraphEnvelope`, a bare `Graph`, or a legacy `{name: Graph}` map (one entry imported per name, each through `createGraph`). Names that clash get ` (imported)`. Errors show as an S07 error banner with the server `detail` and, when present, the diagnostics count: `Import failed: <detail> (3 problems)`. A successful import opens the imported graph and shows an S07 ok banner `Imported "<name>".`
- `Delete…`: confirm dialog (0.3, width 400): title `Delete <name>?`, body `This removes the graph from the server for everyone. Bots already spawned keep their own copy of the graph.`, buttons `Cancel`, `Delete` (danger). On 409: banner `Could not delete: the graph changed on the server. Reload and try again.`
- `▶ Run backtest` / `Cmd+Enter`: the backtest cook (A4). Disabled rules above. Click while running: cancels the in-flight request (AbortController) and returns to idle; the status bar reads `cancelled` in `--nb-text-muted` for 3 s.
- Name crumb click: inline rename; same validation as the dialog; `Enter`, `Cmd+S` and blur commit through the same save path used by `Rename…`; `Esc` reverts.
- The one-time seed (`persistence.ts`): on first mount, if `localStorage['strategylab-saved-graphs']` parses to at least one graph and `nb.seeded` is unset, call `seedLegacyGraphs(raw)` once. On success: rename the legacy key to `strategylab-saved-graphs.migrated` (do not delete), set `nb.seeded = '1'`, and show an S07 info banner: `Imported N saved graphs from this browser to the server. Open one from ⋯ › Open.` with a `Open…` action; append ` · M skipped (duplicates)` for duplicate skips and ` · K could not be read (kept in this browser)` for unreadable ones. If the server answers 413 (the legacy value is larger than the request limit), show a warning banner: `The saved graphs in this browser are too large to import in one request. They are kept in this browser.` The legacy key is renamed only after the server accepted the seed. On any other failure: no banner (retry next mount), console warning only.
- `beforeunload` prompts while dirty.

**Copy.** All strings above, plus tooltips: `Save (⌘S)`, `Run backtest (⌘↵)`, `Stop`, `More`, `Reset view (H)`, `Inspector (P)`, `Data sheet (S)`, `Edit this graph`.

**Accessibility.** The toolbar is `role="toolbar"` with `aria-label="Graph"`. Tab order: name, right cluster left to right. Arrow keys move between toolbar buttons (roving tabindex). The overflow menu is `role="menu"`; `Enter`/`Space` opens it with the first item focused; `Esc` closes and returns focus to `⋯`. The unsaved dot has `aria-label="Unsaved changes"`. The diagnostics chip is a button with `aria-label="1 error, 2 warnings"`.

**Must not.**
- Do not put graph-level actions inside `Canvas.tsx`; the toolbar owns them.
- Do not write graph results into `lastRequest` or `backtestResult` (D10); the run handler writes `graphResult` only (S28).
- Do not `confirm()`/`alert()` for the dirty check; use the dialog shell so tests can find it.
- Do not disable the whole toolbar while saving; only Save, New, Open, Delete.
- Do not debounce or auto-save to the server. Drafts go to `localStorage` only (S03). Server saves are explicit.
- Do not add a second Run button in the canvas or the Results panel.

**Acceptance.**
- `data-testid`: `nb-toolbar`, `nb-graph-name`, `nb-dirty-dot`, `nb-rev`, `nb-btn-run`, `nb-btn-save`, `nb-btn-more`, `nb-diag-chip`, `nb-menu-more`.
- Edit a param: `nb-dirty-dot` appears; `Cmd+S` on a saved graph removes it and increments `nb-rev`.
- `Cmd+S` on an untitled graph opens a dialog with a `Name` input; `Save` creates and the name appears in `nb-graph-name`.
- With one error, `nb-btn-run` is disabled and its `title` is `Fix 1 error to run`.
- Read-only graph: the toolbar has no `nb-btn-save` and has a button labeled `Edit this graph`.
- Screenshot at 1600: toolbar is 36px tall; the right cluster order matches A1.

## S02. Graph Browser dialog

**Purpose.** The only place graphs are listed, opened, duplicated, renamed, exported and deleted. Graphs do not appear in the rule strategy picker (D11); this dialog says so.

**Placement.** Dialog shell (0.3), width 760, height `min(560px, 80vh)`. Opened from `⋯ › Open…`, `Cmd+O`, the empty-state line `or ⋯ › Open to load a saved graph`, and the seed banner action. File `GraphBrowser.tsx`, registered in the `dialogs` slot from W3.

**Anatomy.**
- Header 44: title `Graphs`; right: search input (width 240, placeholder `Search graphs…`, mono 12, autofocus); sort select (`Updated`, `Name`, `Nodes`; default `Updated`, newest first); close `✕`.
- Body: a list, one row per `GraphListItem`, row height 40, padding `0 12px`, columns:
  1. name sans 12px 600 `--nb-text` (highlight matched substring with `--nb-selection` underline);
  2. description sans 11px `--nb-text-muted`, one line, ellipsis (empty description shows nothing);
  3. `12 nodes` mono 11px `--nb-text-muted`, width 72, right-aligned;
  4. groups (W5+): pills 16px caps 10px, `LONG`/`SHORT`/`SWITCH` tints as foundation 4.10; before W5 nothing;
  5. updated: relative time (0.6) mono 11px `--nb-text-muted`, width 96, right-aligned, `title` = full timestamp;
  6. hover actions (visible on row hover or row focus): text buttons `Open`, `Duplicate`, and a `⋯` icon button with a menu `Rename…` · `Export JSON` · ─ · `Delete…`.
  The graph that is open now has a 16px pill `open` (`--nb-selection-soft` bg, `--nb-selection` text) after its name.
- Zebra rows `--nb-bg-sheet-row-alt`; hover `--nb-bg-hover`; selected row `--nb-bg-active` with a 2px left bar `--nb-selection`.
- Footer 44: left, a note sans 11px `--nb-text-muted`: `Graphs live here and in the bot picker. The rule strategy list does not show them.` Right: `Import JSON…` (default), `New graph` (default), `Open` (primary, disabled until a row is selected).

**States.**
| State | Body |
|---|---|
| loading | one row: 12px spinner + `Loading graphs…` in `--nb-text-muted`; footer buttons disabled except Close |
| empty (no graphs) | centered, `--nb-text-dim` 13px: `No graphs yet.` then `Create one with New graph, or import a JSON file.`; `New graph` primary in place of `Open` |
| empty search | `No graphs match "<query>".` |
| error | S07 error banner inside the body: `Could not load graphs: <detail>` with `Retry` |
| deleting | the row's actions replaced by `Deleting…`; on 409 the row stays and a banner reads `Could not delete "<name>": it changed on the server.` |
| row selected | `Open` enabled; `Enter` opens |

**Interactions and keys.**
- Click a row selects it; double-click opens it. `Enter` opens the selected row. `↑`/`↓` move the selection (wraps at the ends); typing while the list is focused moves focus to the search input and keeps the keystroke. `Delete`/`Backspace` on a selected row opens the delete confirm (S01 copy). `Esc` closes the dialog.
- Opening a graph while the current graph is dirty runs the S01 **Save changes?** flow first; `Cancel` returns to the browser with the selection kept.
- Opening the graph that is already open just closes the dialog.
- `Duplicate` creates `<name> copy` (on `name_taken`, `<name> copy 2`, `3`, …) and opens it.
- `Rename…` opens the S01 name dialog; the list refreshes in place.
- `Export JSON` downloads the server envelope (`getGraph` then download).
- `New graph` runs S01 `New` (dirty check included) and closes the browser.
- Search matches name and description, case-insensitive, substring; the list re-filters on each keystroke (no debounce; the list is local). Sort persists in `nb.browser.sort`.
- The list refetches on open and after every create, rename, duplicate or delete.

**Copy.** All strings above. Row `title` on the name: the description, or `No description`.

**Accessibility.** The list is `role="listbox"` with `aria-label="Graphs"`; rows are `role="option"` with `aria-selected`; `aria-activedescendant` on the list follows the selection. The row action buttons are in the tab order only for the selected row (roving). Focus order: search, sort, list, footer buttons, close. The `open` pill has `aria-label="Currently open"`.

**Must not.**
- Do not list graphs from `localStorage`; the API is the only source (`listGraphs()`).
- Do not show graphs in `StrategyBuilder`'s saved-strategy select, and do not add a "graphs" group to it.
- Do not fetch every graph's body to show counts; `GraphListItem.node_count` is enough.
- Do not close the dialog on a failed open; show the error inside it.

**Acceptance.**
- `data-testid`: `nb-graph-browser`, `nb-browser-search`, `nb-browser-row-<id>`, `nb-browser-open`, `nb-browser-new`, `nb-browser-empty`.
- With a mocked list of 3 graphs, typing a name filters to 1 row; `↓` then `Enter` opens it (store `graphMeta.id` changes).
- With an empty list, `nb-browser-empty` renders the `No graphs yet.` text.
- The footer contains the sentence `The rule strategy list does not show them.`
- Deleting a row calls `deleteGraph(id, rev)` only after the confirm dialog's `Delete` button is pressed.

## S03. Draft restore prompt

**Purpose.** Edits survive a crash, a reload or a closed tab, without silent overwrites. The user always chooses.

**Placement.** An S07 info banner under the toolbar, shown after a graph loads when a newer draft exists. Draft logic in `persistence.ts` (`saveDraft`, `readDraft`, `clearDraft`).

**Anatomy of the draft.** `localStorage['nb.draft.<graphId>']` (or `nb.draft.new` for an untitled graph) = `{graphId, rev, name, savedAt (ISO), graph}`. Written at most every 5 s while `dirty` is true (timer starts on the first dirty commit; also written on `visibilitychange` to hidden and on `beforeunload`). Cleared on a successful save, on `Discard`, and when the graph is deleted. Size guard: if the JSON exceeds 1 MB, skip the write and log once.

**When the banner shows.** On load of graph `G` (from the browser, on app start with `nb.lastGraph`, or after New for the untitled key): a draft exists for `G`, and its `graph` differs from the loaded graph (deep equal after JSON normalization). Two variants:
- **Same rev** (`draft.rev === server.rev`): `A newer unsaved draft of <name> from <time> was found in this browser.` Actions: `Restore draft` (primary text button), `Discard`.
- **Server moved on** (`draft.rev < server.rev`): `An unsaved draft of <name> from <time> was found, but the server has a newer version (rev <N>). Restoring it will need Save as copy.` Actions: `Restore draft`, `Discard`.
`<time>` is relative (0.6), `title` = full timestamp.

**States.** The banner is the only UI. While it is visible the graph shown is the **server** version; nothing is restored until the user says so. The banner stays until an action is taken, the graph is closed, or the user edits the loaded graph (an edit implies "keep the server version"; then the banner changes to a one-line `Draft kept · Restore` for 10 s, then the draft is discarded).

**Interactions.**
- `Restore draft`: replaces the graph in the store through one `commit('restore draft')` (undoable), sets `dirty = true`, keeps the draft key until the next save. In the "server moved on" variant, the next `Cmd+S` goes straight to the S04 conflict dialog with `Reload theirs` and `Save as copy` (the user already knows).
- `Discard`: `clearDraft`, banner closes.
- `Cmd+Z` after a restore returns to the server version.

**Copy.** As above, plus the banner's dismiss `✕` `title="Discard draft"`. The status bar shows `draft saved 12:04:31` in `--nb-text-dim` for 2 s after each draft write (replaces the `saved…` segment while dirty).

**Accessibility.** Banner `role="status"`; action buttons are real buttons; focus is not stolen on show.

**Must not.**
- Never restore automatically. Never auto-save a draft to the server.
- Do not store drafts of read-only graphs.
- Do not keep the timer running when clean; stop it on save.
- Do not compare by `rev` alone; compare content, or a saved-then-reloaded graph would prompt forever.

**Acceptance.**
- Edit a graph, wait 5 s (fake timers), reload the store with the same graph id: the banner `nb-banner-draft_found` appears (S07 key rule) with `Restore draft`; clicking it makes the store graph equal the draft and `dirty` true.
- `Discard` removes the key.
- After a successful save the key is gone and no banner shows on reload.
- Content-equal draft: no banner.

## S04. Save conflict (409) dialog

**Purpose.** Two windows saved the same graph. Nobody's work is overwritten without a look.

**Placement.** Dialog shell (0.3), width 480. Opens when `saveGraph` throws `RevConflict`.

**Anatomy.**
- Title `Saved elsewhere`.
- Body paragraph: `<name> was saved from another window at <time> (rev <current_rev>). Your copy is based on rev <my_rev>.` `<time>` from a fresh `getGraph(id).updated_at`, relative with a full `title`.
- A `Compare` disclosure (text button). Open: a compact structural diff, mono 11px, max height 200, scroll: `+ 2 nodes added on the server: sma_spy, spy_uptrend`, `− 1 node removed on the server: vol_ok`, `~ 3 nodes changed: spread_z (params), go_long (wires), long_entry (position)`, `~ name changed: …`. Computed client-side from the server graph and the local graph (`compareGraphs(local, server)` in `persistence.ts`). Empty diff (only positions moved) reads `Only node positions differ.`
- Footer, left to right: `Save as copy` (default), `Reload theirs` (default), `Overwrite` (danger, disabled until `Compare` has been opened once; `title="Open Compare first"`).

**States.** Fetching the server version: body shows a 12px spinner and `Checking the server version…`; buttons disabled. Fetch failed: body `Could not fetch the server version: <detail>`; only `Save as copy` and `Cancel` (✕) available.

**Interactions.**
- `Save as copy`: opens the S01 name dialog prefilled `<name> copy`; creates a new graph with the local content; the editor switches to the copy; the old graph is left as the server has it. Draft key for the old id is cleared.
- `Reload theirs`: replaces the local graph with the server version via one `commit('reload from server')`, sets `rev = current_rev`, `dirty = false`, clears the draft. `Cmd+Z` brings the local version back (and `dirty` true).
- `Overwrite`: `saveGraph(id, {rev: current_rev, graph: local})`. On success normal save. On a second 409 (someone saved again) the dialog re-opens with the new numbers.
- `Esc`/`✕`: cancel; the graph stays dirty; status bar `unsaved · conflict` in `--nb-warn` until resolved; the next `Cmd+S` re-opens the dialog without a new PUT (uses the cached server rev).

**Copy.** As above. Section label above the diff: `What changed on the server`.

**Accessibility.** Dialog rules from 0.3. `Compare` is `aria-expanded`. The diff region is `role="region"` `aria-label="Differences"`.

**Must not.**
- Never retry the PUT with the new rev automatically.
- Do not show a full JSON diff; the structural summary is the whole UI.
- Do not lose the local graph on `Reload theirs` without a history step.

**Acceptance.**
- Mock `saveGraph` to throw `RevConflict{current_rev: 13}` and `getGraph` to return rev 13: the dialog shows `rev 13` and `rev 12`; `Overwrite` is disabled until `Compare` is clicked; then it calls `saveGraph` with `rev: 13`.
- `Reload theirs` sets `dirty=false` and the store graph equals the server graph; `Cmd+Z` restores the local one.
- `Save as copy` calls `createGraph` with the local graph and the name `<name> copy`.

## S05. Diagnostics

**Purpose.** Errors and warnings show where they are (node badge, red field) and how many there are (toolbar chip, status bar), before anyone presses Run. Nothing is silent.

**Placement.** `useDiagnostics()` in `useDiagnostics.ts` calls `POST /validate` 300 ms after each `commit` (cancels an in-flight request; keeps the last result while the next is pending). It exposes `{diagnostics, byNode, errorCount, warningCount, pending}`. Consumers: `DiagnosticBadge.tsx` (in BaseNode's header), `ParamRow.tsx` (invalid field), the toolbar chip (S01), the diagnostics popover (`DiagnosticsPopover.tsx`, opened by the chip and the status bar segment), wires (S11 diagnostic stroke), and from W3 the Inspector Diagnostics section (S14).

**Anatomy.**
- **Node badge** (foundation 4.8): 14x14 circle in the header, after the type label and before the flags. Error: bg `--nb-error`, glyph `!` mono 700 9px `--nb-text-on-color`. Warning: bg `--nb-warn`, glyph `▲` 8px. When a node has both, the error badge shows and the count includes both. Count > 1 replaces the glyph with the number (mono 700 9px). The card border becomes `rgba(248,113,113,0.6)` on error. Hover 400 ms: tooltip listing messages (max 5, then `and N more`), each line prefixed with `●`/`▲` in its color.
- **Invalid field** (ParamRow): when a diagnostic has `param` equal to the row's param, or when local parsing fails (`Number(value)` is NaN for a number/int row), the value cell gets `border: 1px solid var(--nb-error)`, radius 3, text `--nb-error`, `title` = message (local: `Enter a number`). The fix for audit bug 22: set `border` as one shorthand, never `borderColor` with `border`.
- **Toolbar chip**: S01 anatomy. Hidden at 0/0. While `pending` and there has never been a result: shows `…` in `--nb-text-dim`. Later validations do not flicker (old counts stay until the new ones arrive).
- **Popover** (0.4), width 380, max height 360, anchored under the chip (or above the status-bar segment). Header 28: `2 errors · 1 warning` sans 12px 600, close `✕`. Rows 32px grouped by node: group header = node name mono 11px `--nb-text` (or `Graph` for `node_id: null`), then one row per diagnostic: 6px severity dot, message sans 11px `--nb-text-secondary` (max 2 lines), code mono 10px `--nb-text-dim` right (`attr_missing`). Info-severity diagnostics appear here with a grey dot and are not counted in the chip. Footer 24: `Run is disabled until the errors are fixed.` in `--nb-text-dim` when errors > 0.
- **Status bar segment** (S20): `2 errors · 1 warning`; errors in `--nb-error`, warnings in `--nb-warn`; `no problems` in `--nb-text-dim` when clean. Click opens the popover.

**States.**
| State | Look |
|---|---|
| clean | no badges, no chip, status `no problems`, Run enabled |
| errors | badges, red borders, chip `● N`, status red, Run disabled `Fix N errors to run` |
| warnings only | amber badges, chip `▲ N`, Run enabled |
| validate request failed (network) | last result kept; status segment appends ` · validate offline` in `--nb-text-dim`; Run stays as it was; an S07 error banner appears once per failure streak: `Could not reach the server to validate. <detail> · Retry` |
| pending first result | chip `…`; Run enabled (no known errors yet) |
| server 400 with `diagnostics` on Run | the response's diagnostics replace the current set until the next commit; banner S07 error with the `detail` and a link to the `node_id` |

**Interactions.**
- Popover row click: select the node (only it), frame it if off-screen (`F` behavior), and from W3 open the Inspector with Diagnostics expanded and the offending param row flashed (`--nb-selection-soft` background for 800 ms). Wire diagnostics (`port` set, or `dangling_wire`) select the wire instead. Graph-level rows do nothing on click.
- Badge click: same as a popover row click for that node.
- `Esc` closes the popover.

**Copy.** Chip `aria-label`: `1 error, 2 warnings`. Tooltip title on the badge: the messages. Empty popover (opened from the status bar when clean): `No problems in this graph.`

**Accessibility.** Badge is a `button` with `aria-label="1 error: RSI reads @close, but no input provides it."`. Popover `role="dialog"` `aria-label="Diagnostics"`; rows are buttons; arrow keys move between rows. Invalid fields set `aria-invalid="true"` and `aria-describedby` a hidden message element.

**Must not.**
- Never block typing on validation; the request is debounced and off the input path.
- Never call `/validate` for pointer moves, selection changes or viewport changes; only after `commit`.
- Do not clear diagnostics while a request is pending.
- Do not set both `border` and `borderColor` on the same element (React warning, audit 22).
- Do not use `type="number"` for numeric inputs (F278); stay with `type="text" inputMode="decimal"`.

**Acceptance.**
- `data-testid`: `nb-diag-badge-<nodeId>`, `nb-diag-popover`, `nb-diag-row-<index>`, `nb-param-<nodeId>-<param>` with `aria-invalid`.
- A graph with an unconnected Entry: badge on the Entry node, chip `● 1`, Run disabled; the popover row text equals the server message.
- Typing `abc` into a number row sets `aria-invalid="true"` immediately (no request) with `title="Enter a number"`.
- A commit triggers exactly one `/validate` call after 300 ms (fake timers); two commits 100 ms apart trigger one call.
- Screenshot: error badge is red with `!`, warning badge amber with `▲`.

## S06. AddBotBar graph picker

**Purpose.** The bot creation bar lists graphs from the server, not from `localStorage`, with honest empty, loading and error states.

**Placement.** `frontend/src/features/trading/AddBotBar.tsx`, `source === 'graph'` branch. This bar is styled with the app's `--gh-*` tokens, not `--nb-*`; it is part of the Live Trading page.

**Anatomy.** Replaces the saved-graph select:
- Label `Graph` (existing label style).
- `<select>` with one option per `GraphListItem`, sorted by `updated_at` desc, text `<name> · <node_count> nodes` (W5: ` · <groups>`), value = id. Placeholder option `Select a graph…` (disabled, selected by default).
- A `↻` refresh icon button (24x24) right of the select, `title="Refresh graphs"`.
- Under the select, one line 11px `--gh-text-muted`: `rev <rev> · updated <relative time>` for the selected graph.

**States.**
| State | Picker |
|---|---|
| loading | select disabled with one option `Loading graphs…`; `Add bot` disabled |
| empty | select disabled with option `No saved graphs`; helper line `Build one in the Node Editor and save it.`; `Add bot` disabled |
| error | select disabled with option `Could not load graphs`; helper line in `--gh-red`: `<detail> · Retry` (Retry is a text button) |
| loaded, none selected | `Add bot` disabled with `title="Select a graph"` |
| loaded, selected | `Add bot` enabled |

**Interactions.** Fetch `listGraphs()` when `source` becomes `graph` and on `↻`. On `Add bot`: `getGraph(id)` then POST the bot with `kind: 'graph'` and the graph payload the bots API takes today, plus `graph_id` and `graph_rev` when the request model has those fields (W5 adds `group`). If `getGraph` fails: the existing error line reads `Could not load "<name>": <detail>`.

**Copy.** As above. The existing radio stays `Rules | Graph`.

**Accessibility.** The select has `aria-label="Graph"`; the helper line is `aria-live="polite"` so the error announces.

**Must not.**
- Do not read `strategylab-saved-graphs` from `localStorage` any more; delete `parseSavedGraphs` and its tests once the API path is in.
- Do not fetch the list on every render or every keystroke elsewhere in the bar.
- Do not start the bot; spawn creates stopped bots (plan 8.4).

**Acceptance.**
- `data-testid`: `addbot-graph-select`, `addbot-graph-refresh`, `addbot-graph-help`.
- With `listGraphs` mocked to reject: the select is disabled and `addbot-graph-help` contains `Retry`; clicking Retry calls `listGraphs` again.
- With two graphs: two options plus the placeholder; `Add bot` is disabled until one is chosen.
- `grep -n "strategylab-saved-graphs" frontend/src/features/trading/AddBotBar.tsx` finds nothing.

## S07. Notice banners

**Purpose.** One style for every notice under the toolbar: regime removed, unsupported nodes, restored draft, seed import, server failure, cook expired, conflict, saved copy. Same place, same shape, so the eye learns it once.

**Placement.** A stack directly under the toolbar, full width of the canvas column, above the canvas (pushes the canvas down; the canvas does not re-fit). `NoticeBanner.tsx` plus a `notices` store slice (`pushNotice`, `dismissNotice`, `resolveNotice(id)`); from W3 mounted through the `overlays` slot with order 0.

**Anatomy.** Each banner: min height 32, padding `0 12px`, flex row, 10px gap, border-bottom 1px `--nb-border`.
1. Severity dot 8px: info `--nb-selection`, ok `--nb-ok`, warn `--nb-warn`, error `--nb-error`.
2. Message sans 12px, color: info/ok `--nb-text`, warn `--nb-warn`, error `--nb-error`. Inline node links: mono, underlined dotted, same color; click selects and frames the node.
3. Actions: text buttons (0.1), right-aligned before the dismiss, max 3.
4. Dismiss `✕` icon 24x24, `title="Dismiss"`.
Backgrounds: info `rgba(56,189,248,0.10)`, ok `rgba(52,211,153,0.10)`, warn `rgba(251,191,36,0.12)`, error `rgba(248,113,113,0.12)`.
Stack: newest on top, at most 3 visible; a fourth collapses the oldest into a 22px line `+N more notices` (click expands). Banners with the same `key` replace each other (no duplicates).

**States.** ok and info auto-dismiss after 6 s unless hovered or focused; warn and error stay until dismissed or resolved by code (`resolveNotice`). A banner with a `Retry` action shows a 12px spinner in place of the dot while retrying.

**Interactions and keys.** Click `✕` or press `Delete` while a banner's action has focus: dismiss that banner. Click a node link: select and frame the node (the banner stays). Click an action: run it; a banner whose action succeeded resolves itself (`resolveNotice`), one that failed stays and shows the new `detail`. Click `+N more notices`: expand the stack (collapses again after 10 s without pointer contact). `Esc` never touches banners (it belongs to menus, drags and fields). Hover pauses the auto-dismiss timer; leaving restarts it at 6 s.

**Copy (exact, by key).**
| key | severity | text | actions |
|---|---|---|---|
| `regime_removed` | warn | `Regime moved out of this graph: the rule strategy's regime filter is not part of the graph yet. Results may differ.` | `Learn more` (opens a tooltip: `Wave 5 brings regime into the graph.`) |
| `unsupported_nodes` | error | `Unsupported in graphs: <type1>, <type2>. The graph cannot run until these are replaced.` node names as links | none |
| `draft_found` | info | S03 text | `Restore draft`, `Discard` |
| `seed_imported` | info | S01 seed text | `Open…` |
| `import_ok` | ok | `Imported "<name>".` | none |
| `server_error` | error | `<detail>` then ` · ` then the node name link when `node_id` is set | `Retry` (repeats the failed call) |
| `run_error` | error | the Run error `<detail>` then ` · ` then the node name link when `node_id` is set; a separate key so a pushed `server_error` never hides it | `Retry` (runs again) |
| `validate_offline` | error | `Could not reach the server to validate. <detail>` | `Retry` |
| `cook_expired` | warn | `The cached data for this cook expired. Cooking again…` (auto-resolves when the re-cook succeeds) | none |
| `rev_conflict_pending` | warn | `This graph has a save conflict. Save (⌘S) to resolve it.` | `Resolve` (opens S04) |
| `readonly_view` | info | `This graph is a view of the rule strategy "<name>". Edit this graph to make an editable copy.` | `Edit this graph` |
| `saved_copy` | ok | `Saved as "<name>".` | none |
`<detail>` is always the server's `detail` string, never the axios message. When `detail` is missing: `Request failed (<status>)`.

**Accessibility.** The stack is `role="region"` `aria-label="Notices"`. error/warn banners are `role="alert"`; info/ok `role="status"`. Focus is never moved to a banner automatically. Dismiss is a button.

**Must not.**
- No `window.alert`, no toasts floating over the canvas; everything goes through this stack.
- Never show `err.message` from axios; unwrap `response.data.detail` (string or `{code}` object → map code to a sentence: `rev_conflict` → S04, `name_taken` → `That name is taken.`, `cook_expired` → the row above).
- Do not animate height; banners appear and disappear instantly (the canvas re-layout is enough motion).

**Acceptance.**
- `data-testid`: `nb-notices`, `nb-banner-<key>`, `nb-banner-dismiss`.
- Pushing two notices with the same key renders one banner.
- An `ok` notice disappears after 6 s (fake timers) unless hovered.
- A `server_error` notice with `node_id` renders a link whose click selects that node.
- Screenshot: error banner is 32px tall with a red dot and red text on a faint red background.

---

# Wave 2 surfaces

## S08. Node ports

**Purpose.** Every node shows its named inputs on the top edge and its one output on the bottom edge, from the catalog `PortsSpec`. Wires connect port to port, so operand order is fixed by the port, never by wire order.

**Placement.** `BaseNode.tsx` renders React Flow `Handle`s. Input handle ids are `in0`, `in1`, … (matching `GraphWire.to_port`); the output handle id is `out`. `Canvas.tsx` supplies `isValidConnection` and `onConnect` using handle ids. Port helpers in `streamLabels.ts` (`portsOf(nodeType, wires)`).

**Anatomy** (foundation 4.3, restated with the React Flow details):
- Input port: `<Handle type="target" id="in{k}" position={Position.Top}>`, 10x10 circle, `background: var(--nb-bg)`, `border: 2px solid var(--cat)`, `border-radius: 50%`, centered on the top edge (`top: -5px`). Horizontal position `left: calc(100% * (k + 1) / (n + 1))` with `transform: translateX(-50%)`, where `n` is the number of drawn input ports (connected ports plus, for dynamic nodes, one spare). Round to 0.5px.
- Spare port (dynamic nodes only: logic `and`/`or`/`xor`, `merge`, wrangle): the same circle with `border-style: dashed`, opacity 0.6, id `in{n}` where `n` is the next free index. Connecting to it makes it a real port and adds a new spare (up to `PortsSpec.max`; when `max` is reached no spare is drawn). Disconnecting the last connected port of a dynamic node removes the trailing empty ports down to `min`.
- Port label: mono 9px `--nb-text-muted`, centered above the port, `top: -16px`, `text-shadow: 0 0 3px var(--nb-bg), 0 0 3px var(--nb-bg)` (the canvas halo). Always drawn when the node has 2 or more input ports; on node hover otherwise. Text = `PortsSpec.ports[k].label` (`a`, `b`, `source`, `in0`…). Optional ports append nothing; the label is the same.
- Output port: `<Handle type="source" id="out" position={Position.Bottom}>`, 10x10, `background: var(--cat)`, `border: 1px solid var(--nb-bg)`, centered at `left: 50%`, `bottom: -5px`.
- Hit area: an 18x18 transparent pseudo-element around every port (`::before`), so grabbing is easy at 75% zoom.
- Terminals draw input ports only; Tickers and Settings draw the output only. Nothing else is drawn on those edges (fixes audit bug 15).

**States.**
| State | Port |
|---|---|
| default | as above |
| hover | `transform: translateX(-50%) scale(1.3)` over `--nb-motion-fast`, `box-shadow: 0 0 6px var(--cat)` on the hovered port only |
| connected | input port ring becomes a filled disc in `--cat` (the wire visibly "plugs in") |
| required but unconnected, with a `missing_input` diagnostic naming this `port` | ring `--nb-error`; tooltip = the message |
| valid drop target while dragging a wire | ring `--nb-wire-drag`, scale 1.3 |
| invalid drop target while dragging | ring `--nb-wire-invalid`; cursor `not-allowed` |
| full non-dynamic port (already has a wire) | dragging a second wire onto it: invalid state; dropping replaces nothing (see S23 for reconnect) |
| bypassed node | ports unchanged (pass-through keeps the stream) |
| read-only graph | ports drawn, `isConnectable={false}` |

**Interactions and keys.**
- Drag from an output to an input: `onConnect({source, sourceHandle: 'out', target, targetHandle: 'in1'})` → `commit('connect', addWire)`. Drag from an input to an output also works (React Flow supports both directions; store the wire as from/out to to/in).
- `isValidConnection` returns false for: same node; source and target both outputs or both inputs; a target port that already has a wire on a non-dynamic node; a connection that would create a cycle (DFS over the current wires); a target node inside a different network (W5+).
- Drop on empty canvas: S24 (Tab menu filtered).
- Hover a port for 400 ms: tooltip `in1 · @spread_z` (port label and the first attribute the consumer reads through it), or `out · +@rsi +@rsi_slope` for an output. Plain `in1` when nothing flows.
- Pointer events on ports never start a node drag.

**Copy.** Tooltips as above. Diagnostic `missing_input` message comes from the server.

**Accessibility.** Handles get `aria-label="input a"` / `aria-label="output"`. Keyboard wiring is provided by S24 (select a node, press `Tab`, the new node is wired) and by the Inspector's Stream section (W3, a `Connect…` button per unwired required port that opens a node picker). Ports themselves are not tab stops.

**Must not.**
- Do not use React Flow's default handle styles; override `.react-flow__handle` fully (size, border, background, min sizes).
- Do not compute port positions from measured DOM width on every render; percent positions plus `translateX(-50%)` keep layout free.
- Do not infer operands from wire order anywhere in the UI; the port id is the operand.
- Do not draw an output handle on terminals or an input handle on Tickers.
- Do not update React state on port hover; CSS only.

**Acceptance.**
- `data-testid` on handles: `nb-port-<nodeId>-in0`, `nb-port-<nodeId>-out`.
- A `crosses_above` node renders two input handles labeled `a` and `b` and one output handle; an `and` node with two wires renders three input handles, the last dashed.
- `isValidConnection` rejects a connection that closes a cycle and a second wire into `in0` of an `rsi` node.
- Connecting to the spare port of an `and` node adds a wire with `to_port: 'in2'` and a new spare appears.
- Screenshot at 100%: input rings are 10px with a 2px category ring; the output is a filled category disc.

## S09. Attribute picker param row

**Purpose.** Params of type `attr` and `attr_list` pick a named attribute from the node's input stream. The picker shows what is available, who wrote it, and its type, and still lets the user type a name that does not exist yet.

**Placement.** `nodes/AttrPicker.tsx`, used by `ParamRow.tsx` for `type: 'attr' | 'attr_list'` on the node and in the Inspector. Data: the node's input stream, derived in `streamLabels.ts` (`inputStreamOf(nodeId, streams, wires)` = union of upstream `streams[from]` for every wire into the node, in port order; each `AttrInfo` keeps `written_by`).

**Anatomy.**
- **Value cell (closed):** a chip in the read-chip style: 16px tall, mono 10px 500, padding `1px 5px`, radius 3, bg `--nb-bg-chip-read`, text `--nb-text-muted`, text `@close` followed by ` ▾` in `--nb-text-dim`. Right-aligned in the row's value column. `attr_list`: chips inline, 3px gap, wrap allowed (the row grows by 16px per line), plus a trailing `+` chip (dashed border `--nb-border-strong`, no fill).
- **Popover (open):** shell 0.4, width 260, max height 300, anchored to the chip's bottom-left (flips above near the bottom).
  1. Search input, height 28, mono 12, placeholder `@attribute or type a name`, autofocus, prefilled empty (typing filters).
  2. Groups, one per writer, in stream order. Group header 22px: writer chip (the writer node's name, 9px on the writer category tint with category text, radius 3, padding `0 4px`) and, right, `5` count in `--nb-text-dim`. Clicking the header selects the writer node on the canvas without closing the popover.
  3. Rows 26px: `@name` mono 11px `--nb-text`; dtype tag mono 9px `--nb-text-dim` after it (`f` float, `b` bool, `detail` for detail attrs, `prim` never in W1–W7); a `✓` at the right on the current value. Rows whose dtype does not match the param's `dtype` (`float` wanted, `bool` offered, or the reverse) render at opacity 0.5 with `title="This param needs a float"` / `"…a bool"` and are still selectable (the server diagnostic `attr_type` then explains).
  4. Free-text row (when the query is a valid name `^@?[a-z_][a-z0-9_]{0,63}$` and matches no row): `Use @<query>` with `▲ not on the input yet` in `--nb-warn`. An `@` is prepended if missing.
  5. Empty input stream (no upstream wires): body reads `Nothing flows in yet. Wire an input first.` in `--nb-text-dim`, and the free-text row still works.
  Footer 22px: `↑↓ move · ↵ pick · esc close` mono 10px `--nb-text-dim`.

**States.**
| State | Chip |
|---|---|
| unset, optional (`b` of a comparison) | chip text `none ▾` in `--nb-text-dim` |
| unset, required | chip text `pick ▾` in `--nb-warn`, border 1px `rgba(251,191,36,0.5)` |
| set and present | normal |
| set but missing upstream (`attr_missing` names this param, or client-side: not in the input stream) | text `--nb-error`, `text-decoration: underline dotted`; tooltip `@rsi is not present on the input` |
| type mismatch (`attr_type`) | text `--nb-warn`; tooltip = message |
| code mode (W7) | replaced by the expression field; the picker is reachable from the Inspector only |
| read-only graph / disabled | chip without `▾`, no popover |
| open | chip border `--nb-border-focus` |

**Interactions and keys.**
- Click the chip (or `Enter`/`Space` when the row is focused) opens the popover. `↑`/`↓` move; `Enter` picks; `Esc` closes without change; typing filters (substring on the name, prefix on the writer name). Outside click closes. `Tab` moves to the next param row and closes.
- Pick → `commit('set <param>', updateNodeParams(id, {[param]: '@name'}))`. The wire label (S11) updates on the same render. Picking with the mouse keeps the popover for `attr_list` and closes it for `attr`.
- `attr_list`: each chip shows a `✕` on hover (10px, right inside the chip) that removes it; `Backspace` in an empty search removes the last chip; the `+` chip opens the popover. Order = pick order.
- Default reads: when a node is first wired and its `attr` param is unset, `onConnect` pre-fills it with the source's first write (foundation 5.4), so most nodes never open the picker. The picker shows `✓` on that value.

**Copy.** Strings above. Row `title` = `@rsi · float · written by rsi`.

**Accessibility.** The chip is a `button` with `aria-haspopup="listbox"`, `aria-expanded`, `aria-label="source: @close"`. The list is `role="listbox"` with `role="option"` rows and `aria-selected`; group headers are `role="presentation"` with a visible label. The free-text row is an option with `aria-description="not on the input yet"`. Missing state sets `aria-invalid="true"` on the chip.

**Must not.**
- Do not build the option list from the catalog `reads`; use the live `streams` from `/validate` plus wires.
- Do not refuse free text; unknown names are allowed and flagged.
- Do not re-request `/validate` on open; the last response is the source.
- Do not close the popover on canvas re-render; it is portaled to the document body and positioned by screen coordinates.
- Do not let a keydown inside the search bubble to the canvas command registry (stop propagation for all keys except `Esc`).

**Acceptance.**
- `data-testid`: `nb-attr-chip-<nodeId>-<param>`, `nb-attr-popover`, `nb-attr-option-<name>`, `nb-attr-free`.
- With a stream of `@open @high @low @close @volume` from `aapl`, the popover shows one group `aapl` with 5 rows; typing `clo` leaves `@close`; `Enter` commits `source: '@close'`.
- Typing `@foo` shows `Use @foo` with the warning text; picking it stores `'@foo'` and the chip has `aria-invalid="true"`.
- A bool attribute offered to a float param renders at opacity 0.5 with the float tooltip.
- Screenshot: chip is the grey read-chip with a caret.

## S10. Write chips `+@name`

**Purpose.** Every attribute a node adds is visible on the node and can be renamed in place. Renames follow through to every reader.

**Placement.** `nodes/WriteChip.tsx`, rendered in BaseNode's chip row (foundation 4.5) after the read chips, one per `write` param (and per fixed write of nodes without a `write` param, e.g. Tickers). Rename goes through `operations/renameAttr.ts` (`rename_attr(graph, nodeId, oldName, newName)` rewrites this node's `write` param and every downstream `attr`/`attr_list` value equal to `oldName`, stopping at a node that writes the same name again).

**Anatomy.** Chip 16px tall, mono 10px 500, padding `1px 5px`, radius 3, bg = category tint, text = category color, text `+@rsi`. Editing: the chip becomes an inline input of the same size and font: a fixed prefix `+@` (not editable, same color) and an input for the rest (`rsi`), width = text width + 8, min 40, bg `--nb-bg-input`, border 1px `--nb-border-focus`, no radius change. Multi-write nodes (MACD: `+@macd +@macd_signal +@macd_hist`; BB; stochastic `+@k +@d`) show each. More than 6 chips total: foundation 4.5 overflow rule (`+3` chip that opens a popover listing all, where renaming also works).

**States.**
| State | Chip |
|---|---|
| default | as above |
| hover | bg tint at 24% (lighten by one step: add `filter: none`; use a second tint token `--nb-tint-<cat>-hover` computed once in `tokens.css`), cursor `text` |
| editing | inline input; other chips unchanged |
| invalid name while editing (`^[a-z_][a-z0-9_]{0,63}$` fails on the part after `@`) | input border `--nb-error`; tooltip `Use lowercase letters, digits and _; start with a letter or _` |
| clash (`attr_clash` error: the same name already exists on the input stream and is read downstream) | chip text `--nb-error`, tooltip = message (`@rsi is already on the stream (written by rsi_fast). Pick another name.`) |
| shadowed (`attr_shadowed` warning) | chip gets a 1px dotted underline in `--nb-warn`, tooltip = message |
| stale (renamed since the last cook) | no change on the chip; the node sparkline goes dim (S26) |
| read-only | not editable; no hover change |
| bypassed node | chips at body opacity 0.45 (foundation 4.9) |

**Interactions and keys.**
- Double-click a write chip → edit. Select-all on the editable part. `Enter` commits, `Esc` reverts, blur commits (if valid) or reverts (if invalid). `Tab` commits and moves to the next chip.
- Commit → one `commit('rename @old to @new', renameAttr)` step. The wire labels (S11), downstream read chips, the Data Sheet columns and the Inspector update on the same render. `Cmd+Z` reverts all of it at once.
- Renaming to the same name: no commit.
- Renaming to a name that exists upstream: allowed; the chip shows the clash or shadow state after the next `/validate` (300 ms).
- Right-click a chip: the param-row context menu (foundation 6.4) with `Rename`, `Copy name`, `Set to default` (restores the catalog default write name).
- Dragging a chip does nothing (chips are not ports). On the first drag attempt in a session, a 3 s tooltip near the chip: `Wires start from the ports on the top and bottom edges.`

**Copy.** Strings above.

**Accessibility.** Chip is a `button` with `aria-label="writes @rsi. Double-click to rename"`; `F2` while the chip has focus also starts editing. The input has `aria-label="attribute name"` and `aria-invalid` while invalid.

**Must not.**
- Do not rename by string replace across the graph JSON; use `rename_attr` on the model so a downstream node that re-writes the same name stops the propagation.
- Do not commit on every keystroke; one history step per rename.
- Do not let the rename input's keys reach the canvas registry (`B`, `D`, `Delete` must type, not act).
- Do not render the `+` as a separate element the user can delete.

**Acceptance.**
- `data-testid`: `nb-write-chip-<nodeId>-<param>`, `nb-write-chip-input`.
- Double-click `+@rsi` on an RSI node feeding `crosses_below(a=@rsi)`, type `rsi14`, `Enter`: the RSI `out` param is `@rsi14`, the comparison's `a` is `@rsi14`, the wire label reads `@rsi14`, and `past.length` grew by exactly 1; `Cmd+Z` restores both.
- Typing `RSI` (uppercase) sets `aria-invalid="true"` and `Enter` does not commit.
- Screenshot: write chips are tinted in the node's category color; read chips are grey.

## S11. Wire labels and the stream popover

**Purpose.** A wire says what its consumer reads. Hovering says everything that flows. Nobody needs the docs to learn the stream model.

**Placement.** `edges/AttrEdge.tsx` (custom edge: path, label, states), `streamLabels.ts` (label text, fan-out placement, overlap pass), `StreamPopover.tsx` (hover card, portaled). Label text for wire `w` = the consumer's `attr`/`attr_list` param values that are provided through `w` (present in `streams[w.from]`), in the consumer's param order.

**Anatomy.**
- Path, stroke and states: foundation 5 and 5.1 exactly (bezier, `--nb-wire` 1.5px, hover/selected 2px, invalid dashed, diagnostic red at 70%).
- Label: SVG `<text>` at `t = 0.5` (fan-out and fan-in rules of foundation 5.2 restated in `streamLabels.ts` as pure functions with tests): mono 10px, `fill: var(--nb-wire-label)`, `paint-order: stroke`, `stroke: var(--nb-wire-label-halo)`, `stroke-width: 3px`, `text-anchor: middle`, `dominant-baseline: middle`, `pointer-events: all` (so hovering the label counts as hovering the wire). Text forms: `@rsi`; `@a, @b`; `@a, @b +3` (more than two); `stream` in `--nb-text-dim` when the consumer reads nothing yet; selected wire text `--nb-wire-selected`; diagnostic wire text `--nb-error`.
- Fan-out de-confliction, overlap pass and the 60% zoom cutoff are in foundation 5.2; implement them in `placeLabels(edges, nodesRects, zoom)` called from a `useMemo` keyed on (wires, node positions/sizes, zoom band). The zoom band is `zoom >= 0.6`, a boolean, so panning never recomputes.
- **Stream popover** (after 300 ms hover on a wire or label; follows the pointer with a 12px offset; `pointer-events: none`; shell 0.4 but no border animation): width 280, padding 8.
  1. Title row: `stream · 12 attrs` sans 11px 600 `--nb-text`; right, `wire spread_z → below_entry` mono 10px `--nb-text-dim`.
  2. Groups by writer, in stream order: writer name mono 10px `--nb-text-dim`, then chips (read-chip size) colored with the writer's category tint and text; the attributes the consumer reads get a 1px ring `--nb-selection` (`box-shadow: 0 0 0 1px var(--nb-selection)`).
  3. `detail` section (if any): `detail` label, chips `@stop_pct = 2.5` in the writer's tint.
  4. Footer 18px `--nb-text-dim` 10px: `click to select · S shows data` (W4+ text; before W4: `click to select`).
  Max 8 groups; then `+N more writers`.

**States.**
| State | Wire | Label |
|---|---|---|
| default | `--nb-wire` 1.5 | as above |
| hover (wire, label, or either endpoint node hovered) | `--nb-wire-hover` 2 | kept at any zoom; popover after 300 ms |
| selected | `--nb-wire-selected` 2 | `--nb-wire-selected`; kept at any zoom; the Data Sheet follows it (W4) |
| diagnostic on this wire (`attr_missing` whose param reads through it, `dangling_wire`, `attr_clash`) | `--nb-wire-invalid` at 70% | `--nb-error` |
| long (> 600 units) | middle fade (S23) | unchanged |
| zoom < 60% | unchanged | hidden unless hovered or selected |
| dragging a new wire | S23 | none |

**Interactions and keys.**
- Click a wire or its label: select the wire only (`Shift`+click adds). `Delete` removes it (one commit). `Esc` clears.
- Double-click a wire: opens the Tab menu filtered to nodes with an input and an output, and splices the chosen node (S23 `Insert node…`).
- Hover 300 ms: popover. Moving the pointer off the wire hides it at once. The popover never receives pointer events.
- Node hover marks adjacent edges hot through a class toggle in the edge's `data` (`hot: true`) set by the store on `onNodeMouseEnter`/`Leave`; this is the one exception to "no state on hover", limited to 2 renders per hover and to the adjacent edges only.

**Copy.** Strings above. Tooltip on a `stream` placeholder label: `This node does not read anything yet. Pick an attribute on the node.`

**Accessibility.** Each edge `<g>` has `aria-label="wire from spread_z out to below_entry a, carries @spread_z"`. Wires are selectable by keyboard through the Inspector Stream section (W3: `Select wire` buttons per input). The popover is `role="tooltip"`.

**Must not.**
- Do not put labels in `<foreignObject>` or `EdgeLabelRenderer` divs; SVG text with `paint-order` only (performance rule 8.5).
- Do not draw arrowheads.
- Do not recompute label placement per animation frame; only on graph change, node drag end, and the 0.6 zoom-band change.
- Do not show accumulated stream text on the label (V3 divergence); the popover and Inspector show it.
- Do not use filters, `<rect>` boxes or shadows on labels.

**Acceptance.**
- Pure tests in `streamLabels.test.ts`: `labelText(consumer with a=@rsi)` → `@rsi`; three reads → `@a, @b +1`; fan-out of 3 same-text wires yields one visible label on the leftmost; fan-out with different texts yields `t` values `0.38, 0.5, 0.62` and dx `-10, 0, 10`.
- `data-testid`: `nb-edge-<wireId>`, `nb-edge-label-<wireId>`, `nb-stream-popover`.
- Render test: hovering `nb-edge-label-w1` for 300 ms mounts `nb-stream-popover` with the title `stream · N attrs` and one ringed chip per consumer read.
- Screenshot at 80%: labels present with dark halos; at 50% no labels except the selected wire's.

## S12. Time-of-day range and day-of-week widgets

**Purpose.** The `time_of_day` node's `range` param and the `day_of_week` node's `days` param get widgets that read like a trading session, not like a text box.

**Placement.** `nodes/TimeRangeInput.tsx` (type `time_range`) and `nodes/DayPicker.tsx` (type `select` with `multi: true` and the weekday option set; if the catalog spec has no `multi`, `ParamRow` special-cases param name `days` on type `day_of_week`). Both render on the node (`onNode: true`) and in the Inspector.

**Anatomy: time range.**
- Closed value cell: mono 11px `09:35–15:55` (`–` en dash, ET). Unset: `session ▾` in `--nb-text-dim` (the server default is the full session).
- Popover (0.4) width 300, padding 10:
  1. Two inputs `from` and `to`, height 26, mono 12, `type="text" inputMode="numeric"`, placeholder `HH:MM`, 8px gap, label `ET` after them in `--nb-text-dim` (`title="America/New_York wall clock"`).
  2. A session strip 24px tall: track 4px `--nb-border-strong` from 09:30 to 16:00 with tick labels `09:30`, `12:00`, `16:00` mono 9px `--nb-text-dim` under it; the selected range is a 4px bar in the node's category color; two 10px round grips (`--nb-text` fill, 1px `--nb-bg` ring). Pre-market and after-hours are not on the strip; typed times outside 09:30–16:00 are allowed and the strip clamps the drawn bar with a `◂`/`▸` glyph at the edge.
  3. Presets row: text chips `Open 09:30–10:30`, `Midday 10:30–15:00`, `Close 15:00–16:00`, `Full session`.
  4. Footer: `Apply` (primary), `Cancel`.
- Value format sent: `"HH:MM-HH:MM"` (hyphen, 24 h, zero padded), the plan's contract.

**Anatomy: day of week.** Five toggle pills in the value cell (or on their own 20px row when the node is narrower than 200px): `M T W T F`, each 16x16, mono 10px, radius 3; on = category tint bg + category text; off = `--nb-bg-input` bg, `--nb-text-dim` text, border 1px `--nb-border`. Weekends are not offered. Value: an array of `mon..fri` strings in fixed order.

**States.** Time range: invalid time (`25:00`, `9:5`, from >= to) → input border `--nb-error`, `Apply` disabled, helper `Use HH:MM, from before to` under the inputs; dragging a grip: the strip bar updates live and the inputs follow. Day picker: all off → the pills get a `--nb-warn` ring and tooltip `No days selected: the signal is always false`. Read-only: pills and range are not interactive.

**Interactions and keys.** Range: click the cell to open; `Enter` in an input applies; `Esc` cancels; grips drag with 5-minute snapping (`Shift` = 1 minute); `←`/`→` on a focused grip move 5 minutes. Days: click toggles; with a pill focused `Space` toggles and `←`/`→` move focus.

**Copy.** As above.

**Accessibility.** Range popover `role="dialog"` `aria-label="Time of day range"`; grips are `role="slider"` with `aria-valuemin=570` (minutes), `aria-valuemax=960`, `aria-valuenow`, `aria-valuetext="09:35"`. Pills are `role="checkbox"` with `aria-checked` and `aria-label="Monday"`.

**Must not.**
- Never convert to the browser's local time; the value and the display are ET wall clock, matching `toET()` and the backend contract.
- Do not use `<input type="time">` (locale and AM/PM surprises, and no ET).
- Do not commit on each grip move; commit on `Apply` (one history step).

**Acceptance.**
- `data-testid`: `nb-timerange-<nodeId>`, `nb-timerange-from`, `nb-timerange-to`, `nb-timerange-apply`, `nb-day-<mon..fri>`.
- Typing `09:35` and `15:55` then `Apply` stores `"09:35-15:55"`; the cell renders `09:35–15:55`.
- `Apply` is disabled for `16:00` to `09:30`.
- Toggling `M` off stores `['tue','wed','thu','fri']`.

## S13. Unsupported or unknown node

**Purpose.** A node the compiler cannot run (from an auto-rendered rule strategy, an old file, or a type removed from the catalog) is visible, honest and inert. The graph refuses to run until it is replaced; nothing is dropped silently (critic 1).

**Placement.** `BaseNode.tsx` renders this variant when `catalog[node.type]` is missing or has `compile_active: false`, or when a diagnostic with code `unsupported_node` / `unknown_node_type` targets the node.

**Anatomy.** The normal card with these changes: stripe `--nb-text-dim`; glyph chip bg `--nb-bg-chip-read`, glyph `?` in `--nb-text-muted`; name as stored; type label reads the stored type in `--nb-warn` with a `title` of `Not supported yet`; a red error badge (S05) with the diagnostic message; border `1px dashed var(--nb-border-strong)` plus the error border color at 60%; body: every stored param as a plain label/value row (mono 11, value `--nb-text-secondary`, no `=` glyph, no editing); a last row in `--nb-text-dim` 10px: `unsupported · replace this node`; ports drawn from the stored wires (`in0..`, `out`) so wires keep their shape; no chips (nothing is known about its stream); no flags except selection.

**States.** Selected (ring), hover (border-strong) as usual. Read-only graphs show it without the last row. Bypass is not offered (a bypassed unknown node would still be unknown to the compiler).

**Interactions.** Select, move, delete and rewire work. Double-click the header: no rename (the node will be replaced). Context menu (S19) shows `Replace with…` first: opens the Tab menu at the node; choosing a type creates the new node at the same position, moves every wire that fits (`in0..` by index, `out`), and deletes the unsupported node in one commit. The S07 `unsupported_nodes` banner lists these nodes as links.

**Copy.** Badge message from the server, e.g. `Unsupported in graphs: stochastic rising`. Menu item `Replace with…`. Tooltip on the type: `Not supported yet`.

**Accessibility.** `aria-label="unsupported node <name> of type <type>"`; the badge button announces the message.

**Must not.**
- Do not hide or auto-delete unknown nodes.
- Do not render them with React Flow's default white node (audit bug 15); they use this card.
- Do not let Run proceed; the server's `unsupported_node` error and the disabled Run cover it, but the client also treats any unsupported node as an error for the toolbar count even before `/validate` answers.

**Acceptance.**
- `data-testid`: `nb-node-unsupported-<nodeId>`.
- A graph containing `{type: 'stochastic_rising'}` renders the card with the `?` glyph, the type in amber, params as read-only rows and a red badge; Run is disabled.
- `Replace with…` → `rising` creates a `rising` node at the same position, re-attaches the `out` wire, and the history has one new step.

---

# Wave 3 surfaces

## S14. Inspector with a selection

**Purpose.** Houdini's Parameters pane. Everything about the selected node (or wire, or several nodes) in one resizable panel: name and path, every param, code (W7), the stream, diagnostics, notes.

**Placement.** Right of the canvas column, left of the app's 40px rail (foundation 3.1). `Inspector.tsx` registers in the `rightPanel` slot. Width 300 default at 1600 (280 at 1280, 360 at 2560), min 240, max 520, 6px resize handle on its left edge, persisted in `nb.inspector = {width, open, sections: {parameters: true, code: false, stream: true, diagnostics: true, notes: false}}`. Toggle: toolbar `▥` or `P`. Below 1100px app width it overlays (foundation 3.2).

**Anatomy.**
- Panel: `--nb-bg-panel`, border-left 1px `--nb-border`, vertical scroll, sans 12px.
- **Header** 56px, padding `8px 14px`, border-bottom 1px `--nb-border`, flex, 8px gap: glyph chip 20x20 (radius 4, category color, glyph mono 700 11px); a two-line block: line 1 = name sans 14px 600 `--nb-text` (inline editable) and type mono 10px `--nb-text-muted` 6px after; line 2 = path mono 10px `--nb-text-muted` (`/long_leg/spread_z`); right: the two flag dots (8px, 6px gap, S16 behavior, always visible here).
- **Description** (from the catalog `desc`), sans 11px `--nb-text-muted`, padding `6px 14px 0`, one line, `title` holds the full text.
- **Sections**, each with a 28px header row: `▾`/`▸` 9px `--nb-text-dim`, caps 10px 600 title `--nb-text-muted`, and an optional right-side count (`3`, `1 error`). Header hover `--nb-bg-hover`. Body padding `2px 14px 10px`. Border-bottom 1px `--nb-border`.
  1. **Parameters.** One row per catalog param (including the ones not shown on the node), 24px, grid `96px 1fr`, 8px gap. Label mono 11px `--nb-text-muted`, `title` = param name and unit. Control per type: number/int → field 22px (`--nb-bg-input`, border `--nb-border`, radius 4, mono 11 right-aligned, `type="text" inputMode="decimal"`), unit suffix inside the field in `--nb-text-muted`; a slider under the row when the spec has `min` and `max` (2px track `--nb-border-strong`, 10px thumb in the category color, left margin 104px, height 10, 6px bottom margin); select → field with `▾`; bool → 12px checkbox; attr/attr_list → S09; write → S10 chip; path → mono field; time_range → S12. A 4px `--nb-selection` dot 9px left of the label when the value differs from the default (`title="Changed from default 14. Right-click to reset"`). Each row has the `=` gutter glyph (W7) and the param context menu (foundation 6.4). Invalid state as S05.
  2. **Code** (W7; before W7 the section is not rendered).
  3. **Stream.** `reads` label then read chips; `writes` label then write chips (renameable, S10); `input stream · N attrs` label then one 20px row per writer: attribute names mono 11px `--nb-text-secondary` (wrap allowed) and the writer chip right. For each input port a row `in0 ← spread_z out` mono 10px with a `Select wire` text button; an unwired required port shows `in0 · not connected` in `--nb-error` with a `Connect…` text button that opens the Tab menu (S24) in "wire into this port" mode.
  4. **Diagnostics.** Rows 24px: severity dot, message sans 11px, code mono 10px dim; click focuses the param field or selects the wire. Clean: `✓ no issues on this node` in `--nb-ok` 11px. Header count `1 error` in red when present.
  5. **Notes.** A textarea (min 64px, auto-grow to 200, sans 12, `--nb-bg-input`) bound to `node.meta.note` (commit on blur, label `note`). Placeholder `Add a note…` shown as a dashed box in `--nb-text-dim` when empty and not focused.
- **Multiple nodes selected:** header shows `3 nodes` and, if all share a type, the type and glyph; else glyph `≡` in `--nb-bg-chip-read`. Body: a **Bulk** section with buttons `Bypass all (B)`, `Collapse all (X)`, `Tidy (L)`, `Collapse into subnet (⇧C)` (W6), then a **Shared parameters** section (only when all share a type): editing a row writes to every node in one commit; mixed values show `mixed` in `--nb-text-dim`.
- **Wire selected:** header glyph `→` on `--nb-bg-chip-read`, name `spread_z → below_entry`, line 2 `out → a`. Sections: **Wire** (source and target names as links, ports, `reads through this wire: @spread_z` as chips) and **Stream** (the accumulated stream as in the popover S11, static). `Delete wire` danger text button at the bottom.

**States.**
| State | Inspector |
|---|---|
| open, node selected | as above; the first section's first field is not auto-focused |
| open, nothing selected | S15 |
| collapsed | width 0; the toolbar toggle is unpressed; `P` re-opens at the stored width |
| resizing | handle `--nb-selection`; the canvas does not re-fit |
| read-only graph | fields render as label/value text; no flags, no notes textarea (note shown as text) |
| node deleted while shown | falls back to S15 on the same render |
| rename invalid | the name input border `--nb-error`, helper under it `Names use a-z, 0-9 and _ and must be unique among siblings` |
| overlay mode (< 1100px) | absolute right 0, width 280, `--nb-bg-panel` at 96% opacity, closes on `Esc` or outside click |

**Interactions and keys.**
- Click the name (or `F2` with the node selected, or `Enter` on the name) → inline input; `Enter` commits through `rename_node` (`commit('rename node')`), which updates the path line and the node title; `Esc` reverts.
- Section header click or `Enter`/`Space` toggles; the state persists per section.
- Number field: `Enter` commits, `Esc` reverts, blur commits; `↑`/`↓` step by `step` (default 1; `Shift` ×10, `Alt` ×0.1); slider drag commits on release (one step). The node's on-node row and the Inspector row are the same value; editing either updates both.
- Flag dots: as S16.
- Handle: drag to resize; double-click resets to the default width.
- `Esc` inside any field blurs it and returns focus to the canvas root.

**Copy.** Section titles `PARAMETERS`, `CODE`, `STREAM`, `DIAGNOSTICS`, `NOTES`, `BULK`, `SHARED PARAMETERS`, `WIRE`. Strings above.

**Accessibility.** Panel `role="complementary"` `aria-label="Inspector"`. Section headers are `button`s with `aria-expanded` and `aria-controls`. Rows are labeled by their label element (`<label for>`). Sliders `role="slider"` with value text including the unit. Tab order: header name, flags, sections top to bottom; the resize handle is `role="separator"` `aria-orientation="vertical"` with `←`/`→` resizing by 16px. The changed-from-default dot has `aria-label="changed from default"`.

**Must not.**
- Do not `useReactFlow()` inside the Inspector; it reads the store only, so it renders even when the canvas is hidden.
- Do not re-render the whole Inspector on viewport changes; subscribe to `selection` and to the selected node's slice only.
- Do not duplicate ParamRow logic; the Inspector uses the same `ParamRow` component with a `variant="inspector"` prop.
- Do not auto-focus a field when the selection changes (it steals canvas keys).
- Do not store panel sizes in the graph file.

**Acceptance.**
- `data-testid`: `nb-inspector`, `nb-inspector-name`, `nb-inspector-path`, `nb-inspector-section-<id>`, `nb-inspector-param-<param>`, `nb-inspector-handle`.
- Select an RSI node: the header shows `rsi` and `/rsi`; the Parameters section lists `period`, `type`, `source`, `out`; editing `period` to `21` updates the on-node row.
- Rename to `rsi_fast`: the path reads `/rsi_fast`, the node title updates, one history step.
- Rename to `RSI` shows the helper text and does not commit.
- Select two nodes: the header shows `2 nodes` and the Bulk section renders.
- Select a wire: the Wire section shows the ports and the reads.
- `P` toggles the panel; the width survives a remount (`nb.inspector`).

## S15. Inspector with no selection

**Purpose.** An empty Inspector teaches the editor: what the colors mean, what the two dots do, and the keys that matter (the mockup's right panel).

**Placement.** Same panel as S14, rendered when nothing is selected. `InspectorLegend.tsx`.

**Anatomy.**
- Header 56px: title caps 10px 600 `--nb-text-muted` `INSPECTOR`; below it sans 13px `--nb-text-dim` italic `Click a node to inspect.`
- **Graph** section (28px header `GRAPH`): rows 22px, label mono 11 `--nb-text-muted` left, value right: `name` (inline editable, same as the toolbar), `description` (a one-line input, sans 12, placeholder `Add a description…`, commit on blur, saved with the graph), `nodes 14 · wires 16`, `rev 12 · saved 2 min ago`, and from W5 `groups` with a pill per group. Below the rows a `Diagnostics` line: `no problems` or `2 errors · 1 warning` (click opens the S05 popover).
- **Legend** (`LEGEND`): 11 rows 24px (12 with Networks from W5; render only categories that exist in the catalog): 10x10 swatch (radius 2, category color), name sans 12px `--nb-text-secondary` (`Tickers`, `Data`, `Indicators`, `Comparisons`, `Logic`, `Math & Signal`, `Rules`, `Settings`, `Code / Wrangle`, `Outputs`, `Networks`), right: count of nodes of that category in the current network, mono 11px `--nb-text-dim` (hidden when 0). Click a row: selects all nodes of that category (`Shift`+click adds).
- **Flags** (`FLAGS`): two rows 28px: 8px dot lit (`--nb-flag-display` / `--nb-flag-bypass`), name sans 12 (`Display`, `Bypass`), a sentence sans 11 `--nb-text-muted` (`shows this node's data in the Data Sheet and chart`, `skips this node; its input passes through`), and a key cap right (`D`, `B`).
- **Keys** (`KEYS`): 12 rows 24px, label sans 12 left, key cap right (mono 11px on `--nb-bg-input`, border 1px `--nb-border`, radius 3, padding `0 6px`, height 18). Fixed list, in this order: `Add node` `Tab`, `Pan` `Space+drag`, `Zoom` `wheel`, `Marquee` `drag empty`, `Multi-select` `⇧click`, `Frame selection` `F`, `Frame all` `H`, `Bypass` `B`, `Display` `D`, `Undo` `⌘Z`, `Delete` `⌫`, `Data sheet` `S`. Then a text button `Show all shortcuts (?)` that opens S21.
- **Empty graph** variant: the Graph section rows read `nodes 0`; the Keys section is expanded even if it was collapsed before, and the header sentence reads `Press Tab to add your first node.`

**States.** Collapsed sections persist (same `nb.inspector.sections` map, keys `graph`, `legend`, `flags`, `keys`). Read-only graph: name and description are plain text.

**Interactions.** Legend row click selects by category. Flag rows are static (hover shows the sentence in full). Key rows are static. `Show all shortcuts (?)` opens the overlay.

**Copy.** As above. Key caps use the platform modifier (`⌘` or `Ctrl`).

**Accessibility.** Sections as S14. The legend list is `role="list"`; rows are buttons with `aria-label="Select all Indicators (3)"`. Key caps are `<kbd>` elements.

**Must not.**
- Do not build the Keys list from `listCommands()`; it is a curated 12 (the overlay S21 lists everything).
- Do not show "Click a node" when a wire is selected (that is S14's wire view).
- No images or icons beyond swatches and dots.

**Acceptance.**
- `data-testid`: `nb-inspector-empty`, `nb-legend-row-<category>`, `nb-keys-row-<n>`.
- With nothing selected the header text is `Click a node to inspect.`; the Legend has one row per category with a swatch whose background equals the category token; clicking `nb-legend-row-indicator` selects every indicator node.
- The Keys section renders 12 `<kbd>` elements and a `Show all shortcuts (?)` button that opens `nb-shortcuts`.
- Screenshot at 1600 matches the mockup's right panel layout (title, legend, flags, keys).

## S16. Display and bypass flags

**Purpose.** Houdini's two flags. Display picks the node whose data the Data Sheet and chart follow by default. Bypass skips a node and passes its input through.

**Placement.** `BaseNode.tsx` flag column (foundation 4.6), the Inspector header (S14), `commands/flags.ts` (`flags.setDisplay` key `D`, `flags.toggleBypass` key `B`), `operations.ts` `setFlag(graph, nodeId, 'display' | 'bypass', value)`. Stored as `GraphNode.display` / `GraphNode.bypass`.

**Anatomy.** Column outside the right edge: `right: -14px; top: 8px`, two 8px dots, 6px gap, hit area 16x16 each (`::before`). Display dot: lit `--nb-flag-display`, unlit `--nb-flag-off`. Bypass dot below: lit `--nb-flag-bypass`, unlit `--nb-flag-off`. Dot visibility: opacity 0 by default; 1 when lit, on node hover, or when the node is selected (`--nb-motion-fast`). Nodes without an output (terminals) have no display dot; Tickers and terminals have no bypass dot (foundation 4.6). Node-level visuals when set are in foundation 4.9: display = inner outline; bypass = body opacity 0.45, a second 3px amber bar right of the stripe, type label `bypassed` in `--nb-flag-bypass`.

**States.**
| State | Dot |
|---|---|
| off | `--nb-flag-off`, visible on hover or selection only |
| on | lit color, always visible |
| hover on a dot | `transform: scale(1.25)`; tooltip after 400 ms `Display (D)` / `Bypass (B)`; on the lit display dot the tooltip reads `Display (D) · already shown` |
| disabled (read-only graph) | dots not rendered |
| Inspector header | the same two dots, always visible, 8px, same behavior |

**Interactions and keys.**
- Click the display dot: set this node as the display node of its network (`commit('display <name>')`). Any other node in the same network loses the flag in the same commit. Clicking the lit dot does nothing (Houdini rule; there is always a display node once one is set). There is no "no display" state after the first set, except by deleting the node (then the flag moves to nothing and the Data Sheet follows the selection).
- Click the bypass dot: toggle (`commit('bypass <name>')`).
- `D` with a selection: sets display on the primary selected node (the last clicked). With more than one selected the primary wins. `B`: toggles bypass on every selected node that allows it; the new state is the inverse of the primary node's state, applied to all (so a mixed selection becomes uniform) in one commit.
- `D` on a node without an output, or `B` on a Ticker/terminal, or either with no selection: nothing changes; the status bar flashes a message (S20) for 2 s: `Select a node first`, `Terminals have no display flag`, `Tickers cannot be bypassed`, `Terminals cannot be bypassed`.
- Pointer-down on a dot never starts a node drag and never changes the selection.
- Backend contract: bypass is pass-through (W2 kernel); the wire out of a bypassed node carries its input stream, and the Data Sheet for a bypassed node shows that input stream.

**Copy.** Tooltips and flashes above.

**Accessibility.** Each dot is a `button` with `aria-pressed` and `aria-label="Display flag"` / `"Bypass flag"`; they are not in the tab order on the canvas (the Inspector header dots are), so keyboard users set flags through the Inspector or `D`/`B`.

**Must not.**
- Do not toggle display on click of the lit dot (no "unset display" gesture).
- Do not allow two display nodes in one network; `setFlag` clears the old one in the same commit.
- Do not implement bypass in the UI by hiding wires or removing the node from the compile payload; the backend does pass-through.
- Do not use React state for dot visibility on hover; CSS `:hover` and the selected class handle it.

**Acceptance.**
- `data-testid`: `nb-flag-display-<nodeId>`, `nb-flag-bypass-<nodeId>` with `aria-pressed`.
- Clicking display on node B when A has it: B `aria-pressed=true`, A `false`, one history step; clicking B's lit dot again adds no history step.
- `B` with nodes X (bypassed) and Y (not) selected, X primary: both become not bypassed; `B` again: both bypassed.
- A bypassed node's type label reads `bypassed` and its body opacity is 0.45 (computed style).
- `D` on an `entry` terminal changes nothing and the status bar shows `Terminals have no display flag`.

## S17. Network boxes

**Purpose.** Houdini network boxes: a labeled, tinted rectangle that groups nodes visually, moves them together, and never changes evaluation.

**Placement.** React Flow node type `nbBox` (`nodes/NetworkBox.tsx`), stored in `graph.annotations.boxes` (`{id, label, color, rect: [x, y, w, h], members, parent}`). `store/annotations.ts` holds add/update/remove; `plugins/boxDrag.ts` handles member movement and membership updates; `commands/annotations.ts` registers `annotations.newBox` (`Shift+B`), `annotations.fitBox`, `annotations.deleteBox`.

**Anatomy.**
- Rect: `border: 1px dashed <tint at 45%>`, `background: <tint at 4%>`, radius `--nb-radius-frame`, `zIndex: -2` (under wires and nodes; verify the render order in the probe: React Flow edges are z 0; a node with negative `zIndex` renders below them).
- Label tab: caps 10px 600 in the tint, on a `--nb-bg` tab (padding `0 4px`, height 16) overlapping the top-left corner (`top: -8px; left: 10px`). Empty label shows `BOX` in `--nb-text-dim`.
- Tint palette (`color` values stored as strings): `network` (default, `--nb-cat-network`), `ticker`, `indicator`, `comparison`, `logic`, `rules`, `code`, `neutral` (`--nb-text-muted`). The plan's example `"blue"` maps to `network`; unknown values fall back to `network`.
- Resize grips: `NodeResizer` with 8px square handles at the four corners, visible on hover or selection, fill `--nb-bg`, border 1px in the tint; edge lines invisible (`lineStyle` transparent, still draggable). Min size 160x96. Default new size 320x200.
- Members: nodes whose center lies inside the rect at the moment of the last drag stop or resize stop. The `members` array is what moves with the box.

**States.**
| State | Box |
|---|---|
| default | as above |
| hover | border alpha 70%; grips visible |
| selected | `box-shadow: 0 0 0 1.5px var(--nb-selection)`; label tab border `--nb-selection` |
| dragging the box | members move live; cursor `grabbing` |
| a node being dragged has its center inside the box | border becomes solid at 80% alpha (the "will join" signal); leaving returns it to dashed |
| editing the label | label tab becomes an input (caps style kept, `text-transform` applied visually only; the stored label keeps the typed case); width auto |
| auto-render `REGIME` box (read-only graphs) | label `REGIME`, tint `network`, not editable, not deletable |
| overlapping boxes | allowed; a node belongs to the smallest box that contains its center |

**Interactions and keys.**
- `Shift+B`: with a selection, creates a box around the selection's bounding rect plus 24px padding and sets `members` to the selection; without a selection, an empty 320x200 box at the cursor. One commit.
- Drag the box body (anywhere not on a node): moves the box and its members by the same delta; one commit on drop (`commit('move box')`).
- Drag a node: on drop, membership is recomputed for every box the node's center entered or left (`commit` folded into the node move's own commit: one history step).
- Resize: on stop, `rect` updates and membership is recomputed; one commit.
- Double-click the label: edit; `Enter` commits, `Esc` reverts, blur commits; max 40 characters.
- Context menu (S19 `Box`): `Rename` · `Tint ▸` (8 swatches, 14x14 each, current one ringed) · `Fit to contents` (shrinks or grows the rect to the members' bounding rect plus 24px) · ─ · `Delete box (keeps nodes)`.
- `Delete` with a box selected deletes the box only (nodes stay). Selecting a box does not select its members; `Cmd+A` inside a box is not special.
- Marquee selection includes boxes only when the box is fully inside the marquee.
- Copy/paste of a box copies its members with it (W3 clipboard item).

**Copy.** Menu items above. Empty label placeholder `BOX`. Tooltip on the tab: `Network box · double-click to rename`.

**Accessibility.** The box is `role="group"` with `aria-label="Network box <label>, <n> nodes"`. The label input is labeled `Box label`. Keyboard users create boxes with `Shift+B` and rename with `F2` when a box is selected.

**Must not.**
- Do not include boxes in the compile payload as nodes; they live under `annotations` only (the backend ignores them for evaluation).
- Do not set `parentId` on member nodes (React Flow sub-flows clip children and change coordinate spaces); membership is a list plus a drag plugin.
- Do not recompute membership during drag on every move event; only on drag stop and resize stop.
- Do not draw the box above wires; keep `zIndex: -2` (notes are `-1`).
- Do not make the box a drop target for wires.

**Acceptance.**
- `data-testid`: `nb-box-<id>`, `nb-box-label-<id>`, `nb-box-grip-<corner>`.
- `Shift+B` with two selected nodes creates a box whose rect contains both nodes' rects with 24px margin and whose `members` are those two ids.
- Dragging the box by (40, 0) moves both members by (40, 0) in one history step; `Cmd+Z` moves everything back.
- Dropping a third node inside the box adds it to `members`; dragging it out removes it.
- Screenshot: dashed indigo border, faint fill, caps label on a dark tab at the top-left; wires draw over the box.

## S18. Sticky notes

**Purpose.** Houdini sticky notes: a short plain-text note on the canvas that explains intent.

**Placement.** React Flow node type `nbNote` (`nodes/StickyNote.tsx`), stored in `graph.annotations.notes` (`{id, text, rect, color, parent}`). Commands: `annotations.newNote` (`Shift+N`).

**Anatomy.** Default 200x88, min 120x48, max 480x400. Fill `--nb-note-bg`, border 1px `--nb-note-border`, radius 6, padding `8px 10px`, text `--nb-note-text` sans 12px/1.45, `white-space: pre-wrap`, `overflow: hidden` (text beyond the box is clipped; a 12px fade at the bottom hints at more). No shadow, no blur. `zIndex: -1` (above boxes, below wires and nodes). A resize grip at the bottom-right (8px triangle in `--nb-note-border`, visible on hover or selection). Colors (`color` values): `amber` (default, the tokens above), `blue` (`#12243a` / `rgba(56,189,248,0.4)` / `#bae6fd`), `green` (`#12301f` / `rgba(52,211,153,0.4)` / `#bbf7d0`), `grey` (`#1a1f28` / `--nb-border-strong` / `--nb-text-secondary`). Keep all four sets at >= 7:1 contrast.

**States.**
| State | Note |
|---|---|
| default | as above; cursor `default` on text, `grab` on the 8px margin |
| hover | border alpha 70%; grip visible |
| selected | `box-shadow: 0 0 0 1.5px var(--nb-selection)` |
| editing | a textarea fills the box (same font, same colors, no border, caret `--nb-note-text`); the note's border becomes `--nb-border-focus` |
| empty | placeholder `Double-click to write` in `--nb-note-text` at 50% |
| read-only graph | not editable, not movable |

**Interactions and keys.**
- `Shift+N`: a new note at the cursor, immediately in edit mode. Pane context menu `Sticky note`.
- Double-click: edit. `Esc` commits and leaves edit; `Cmd+Enter` also commits; blur commits; plain `Enter` inserts a newline. `Tab` inserts two spaces (keeps focus). Max 2000 characters (the textarea `maxLength`). One commit per edit session (`commit('edit note')`).
- Drag anywhere on the note when not editing: moves it (one commit). Resize by the grip (one commit).
- Context menu (S19): `Edit` · `Color ▸` (4 swatches) · ─ · `Delete`.
- `Delete` with a note selected deletes it. Notes are included in marquee selection when fully inside, and in copy/paste.
- Notes never receive wires and never affect evaluation.

**Copy.** Placeholder above. Menu items above.

**Accessibility.** `role="note"` with `aria-label` = the first 60 characters of the text. The textarea is labeled `Sticky note text`. `F2` on a selected note starts editing.

**Must not.**
- No markdown, no links, no rich text. Plain text only.
- Do not let the textarea's keys reach the canvas registry (`B`, `D`, `Delete`, `Space` must type).
- Do not include notes in the compile payload as nodes.
- Do not add a shadow or blur (performance rule 8.1).

**Acceptance.**
- `data-testid`: `nb-note-<id>`, `nb-note-textarea`.
- `Shift+N` creates a note at the pointer position in edit mode; typing `hello` then `Esc` stores `text: 'hello'` in `annotations.notes` with one history step.
- While editing, pressing `b` inserts `b` and does not toggle bypass on any node.
- Screenshot: amber note with dark-amber text, under a wire that crosses it.

## S19. Context menus (node, wire, pane, box, note, param)

**Purpose.** Right-click gives every action in place, with its key shown, so the keys get learned.

**Placement.** `ContextMenu.tsx` (one component, one instance, portaled to the document body), `plugins/contextMenus.ts` (React Flow `onNodeContextMenu`, `onEdgeContextMenu`, `onPaneContextMenu`), item lists built from `listCommands()` filtered by a `menu` tag on each command (`menu: 'node' | 'wire' | 'pane' | 'box' | 'note' | 'param'`) and by `when(state)`.

**Anatomy.** Shell: `--nb-bg-panel`, border 1px `--nb-border-strong`, radius `--nb-radius-menu`, `--nb-shadow-popover`, width 232, padding 4px 0. Rows 28px, padding `0 12px`, sans 12px `--nb-text`, hover/active `--nb-bg-hover`, key cap right-aligned mono 10px `--nb-text-dim`, submenu arrow `▸` right, separators 1px `--nb-border` with 4px margins, destructive rows text `--nb-error`, disabled rows opacity 0.45 with the reason in `title`. A checkmark column (12px) for toggle items (`Snap to grid ✓`). Submenus open to the right (flip left near the edge), same style; swatch submenus show 14x14 squares in a 4-column grid.

Menus (exact order, keys shown as caps):
- **Node:** `Rename` `F2` · `Display flag` `D` · `Bypass` `B` (checkmark when on) · `Collapse` `X` (or `Expand`) · ─ · `Show data` `S` (W4) · `Frame` `F` · ─ · `Cut` `⌘X` · `Copy` `⌘C` · `Paste` `⌘V` · `Duplicate` `⌘D` · ─ · `Collapse into subnet` `⇧C` (W6) · `Save as asset…` (W6) · `Promote parameter ▸` (W6) · ─ · `Delete` `⌫` · `Delete without rewiring` `⇧⌫`. Unsupported nodes (S13) get `Replace with…` as the first row.
- **Wire:** `Show data` `S` (W4) · `Insert node…` `Tab` · ─ · `Delete` `⌫`.
- **Pane:** `Add node…` `Tab` · `Paste` `⌘V` · ─ · `Sticky note` `⇧N` · `Network box` `⇧B` · ─ · `Frame all` `H` · `Tidy layout` `L` · `Snap to grid` `G` (checkmark) · ─ · `Go up` `U` (W6, only inside a network).
- **Box:** `Rename` · `Tint ▸` · `Fit to contents` · ─ · `Delete box (keeps nodes)`.
- **Note:** `Edit` · `Color ▸` · ─ · `Delete`.
- **Param row (node and Inspector):** `Set to default` · `Copy value` · ─ · `Use expression` `=` (W7) · `Promote to parent…` (W6, inside a subnet) · `Add slider` (numbers with min/max only; adds the slider under the row in the Inspector).

**States.** Open (one at a time), closed. A row is disabled when its command's `when` is false (e.g. `Paste` with an empty clipboard: `title="Nothing to paste"`; `Delete without rewiring` when the node has no both-side wires). Read-only graphs show only `Frame`, `Copy`, `Show data`, `Frame all`, `Snap to grid`.

**Interactions and keys.**
- Right-click a node that is not selected: select only it, then open. Right-click a selected node: keep the selection (bulk actions apply to all). Right-click a wire: select it. Right-click the pane: keep the selection (`Paste` goes to the cursor).
- Opens at the pointer, top-left at the cursor, clamped inside the viewport with 8px margin; flips above the cursor near the bottom.
- `↑`/`↓` move (wrapping); `→` opens a submenu, `←` closes it; `Enter`/`Space` runs; `Esc` closes; typing a letter jumps to the next row starting with it; `Home`/`End`.
- Closes on: run, `Esc`, outside pointer-down (the pointer-down is not passed through to the canvas), wheel, viewport move, window blur, or a second right-click (which opens the new menu).
- Every row runs a registry command, so the key cap is derived from the command's `keys` and can never drift.

**Copy.** As listed. Disabled reasons: `Nothing to paste`, `Select a node first`, `No wires to reconnect`.

**Accessibility.** `role="menu"` with `role="menuitem"` / `menuitemcheckbox` rows, `aria-haspopup` on submenu rows, `aria-disabled` on disabled rows. Focus moves into the menu on open and returns to the canvas root on close. `Shift+F10` and the `ContextMenu` key open the menu for the primary selected node (or the pane when nothing is selected) at the node's center.

**Must not.**
- Do not use `window.oncontextmenu` globally; attach to React Flow's handlers and to the specific elements.
- Do not hard-code key labels in the menu; read `command.keys`.
- Do not let the browser's native context menu appear anywhere inside `.nodebuilder-root` except inside text inputs and textareas.
- Do not render the menu inside the React Flow viewport (it would scale with zoom).

**Acceptance.**
- `data-testid`: `nb-context-menu`, `nb-menu-item-<commandId>`.
- Right-click on a node renders `nb-context-menu` with the Node list in order; the `Bypass` row shows the key cap `B` taken from `listCommands()`.
- With an empty clipboard the `Paste` row has `aria-disabled="true"` and `title="Nothing to paste"`.
- `Esc` closes the menu and focus is on `.nodebuilder-root`.
- Right-click on the pane, then `↓` `↓` `Enter` creates a sticky note.

## S20. Status bar

**Purpose.** A quiet, always-visible line with the numbers a network editor shows: zoom, cursor position, selection, cook state, problems, save state.

**Placement.** The bottom of the node builder, full width of the canvas column plus the Inspector (foundation 3.1 draws it full width under both). `StatusBar.tsx` registers in the `statusBar` slot. Cursor tracking through `plugins/pointerTracker.ts` (`onPointerMove` writes the flow coordinates to a ref and to a DOM text node directly, throttled to one write per animation frame; no React state).

**Anatomy.** Height 22, `--nb-bg-panel`, border-top 1px `--nb-border`, padding `0 12px`, mono 11px `--nb-text-muted`, flex with 14px gaps. Segments left to right:
1. **Zoom** `85%` (rounded integer). Click: sets zoom to 100% around the viewport center. `title="Zoom · click for 100%"`.
2. **Cursor** `1173, 277` (flow units, rounded; `—, —` when the pointer is outside the canvas). Width fixed to 96px so the bar does not jitter.
3. **Selection** `spread_z` (one node), `3 nodes`, `wire spread_z → below_entry`, `2 nodes · 1 wire`, or `no selection` in `--nb-text-dim`.
4. **Cook state** (A4): `idle` dim · `cooking… 340 ms` with a 10px spinner (elapsed updates every 100 ms) · `cooked 12:04:31` in `--nb-ok` for 3 s then muted · `stale` in `--nb-warn` · `cancelled` muted for 3 s · `cook failed` in `--nb-error` (click opens the S07 banner's node). A `preview` cook shows the same words with the suffix ` · preview` in dim.
5. **Diagnostics** `1 error · 2 warnings` (colors S05) or `no problems` dim. Click opens the S05 popover above the segment.
6. **Flash** (transient, replaces nothing; appended after diagnostics): a message set by commands (`Select a node first`), `--nb-text-secondary`, shown for 2 s, one at a time.
Right side (`margin-left: auto`, 14px gaps):
7. `auto cook on` / `auto cook off` (dim when off). Click toggles (same command as `A`).
8. `saved 2 min ago` (relative, 0.6) or `unsaved` in `--nb-warn` or `unsaved · conflict` in `--nb-warn` or `draft saved 12:04:31` dim for 2 s after a draft write. Click on `unsaved` runs Save.
9. `graph 3f9a @ rev 12` (`3f9a` = the last 4 hex characters of the id), or `untitled` dim, or `empty graph` dim when there are no nodes.

**States.** Every segment always renders (fixed positions; empty values show `—`). Read-only graphs show `view` in place of the saved segment. When the node builder is hidden (`display: none` tab switch), the pointer tracker unsubscribes.

**Interactions.** Clicks as listed. Nothing else is interactive. The bar never scrolls or wraps; at < 1100px the cursor and `graph` segments hide.

**Copy.** As listed. Cook state words are lowercase (Houdini style).

**Accessibility.** `role="status"` for the cook state and flash segments (`aria-live="polite"`); the whole bar is `role="contentinfo"` `aria-label="Editor status"`. Clickable segments are `button`s with visible text as their label. The cursor segment is `aria-hidden` (noise for screen readers).

**Must not.**
- Do not set React state on pointer move; write the cursor text through a ref (`el.textContent = …`) inside `requestAnimationFrame`.
- Do not subscribe the whole bar to the store; each segment subscribes to its own slice (`useStore(s => s.selection)` and so on).
- Do not update the zoom text during a pan; only on `onMove` throttled to 100 ms or on `onMoveEnd`.
- Do not show the axios message in `cook failed`; the banner carries the `detail`.

**Acceptance.**
- `data-testid`: `nb-status`, `nb-status-zoom`, `nb-status-cursor`, `nb-status-selection`, `nb-status-cook`, `nb-status-diag`, `nb-status-flash`, `nb-status-saved`, `nb-status-graph`.
- Selecting a node named `rsi` sets `nb-status-selection` to `rsi`; selecting two sets `2 nodes`.
- After `setViewport({zoom: 0.85})` and `onMoveEnd`, `nb-status-zoom` reads `85%`.
- A render-count probe shows the StatusBar component does not re-render on `pointermove` events over the canvas.
- Screenshot: 22px bar, mono text, `cooked` in green, `unsaved` in amber.

## S21. `?` shortcut overlay

**Purpose.** Every key in one place, generated from the command registry, so it is always true.

**Placement.** `ShortcutHelp.tsx` in the `overlays` slot; opened by `?` (registry command `help.shortcuts`, keys `['?', 'shift+/']`), the S15 link, and the pane menu (W3 adds `Shortcuts… ?` at the end of the pane menu).

**Anatomy.** A centered panel (not a dialog shell: no footer buttons), width 720, max height 80vh, `--nb-bg-elevated`, border 1px `--nb-border-strong`, radius 8, `--nb-shadow-popover`, backdrop `rgba(11,14,20,0.5)`. Header 44px: title `Keyboard shortcuts` sans 14px 600; right: a filter input (width 200, placeholder `Filter…`, mono 12, autofocus) and `✕`. Body: three columns (CSS grid, 24px gaps, padding 16), each a list of groups. Groups come from the command id prefix, in this fixed order and with these titles: `graph` → `GRAPH`, `history` → `HISTORY`, `selection` → `SELECTION`, `edit` → `EDIT`, `flags` → `FLAGS`, `wires` → `WIRES`, `view` → `VIEW`, `panels` → `PANELS`, `annotations` → `ANNOTATIONS`, `network` → `NETWORKS` (W6), `code` → `CODE` (W7), `help` → `HELP`. A last static group `MOUSE` lists the gestures from foundation 6.1 (`Marquee` `drag empty`, `Pan` `Space+drag · middle drag`, `Zoom` `wheel`, `Add to selection` `⇧click`, `Duplicate` `⌥drag`, `Splice` `drop node on wire`, `Rename` `double-click header`). Rows 24px: label sans 12px `--nb-text-secondary` left; key caps right (`<kbd>` as S15, several caps joined by ` / ` for alternatives, `+` inside a chord shown as `⌘⇧Z`). Commands with no `keys` are not listed. Footer line inside the body, `--nb-text-dim` 11px: `⌘ is Ctrl on Windows and Linux · press ? or Esc to close`.

**States.** Open, closed. Filter narrows rows by label or key text (case-insensitive); empty groups hide; no match → `No shortcuts match "<query>"`. Commands whose `when()` is false right now render at opacity 0.6 (still listed).

**Interactions.** `?` toggles (also closes). `Esc` closes. Outside click closes. Typing goes to the filter. Focus is trapped while open; on close it returns to the canvas root. The overlay does not pause anything.

**Copy.** As above. Platform modifier symbols: Mac `⌘ ⇧ ⌥ ⌃`; others `Ctrl Shift Alt`.

**Accessibility.** `role="dialog"` `aria-modal="true"` `aria-label="Keyboard shortcuts"`. Groups are `<section>`s with headings. Key caps are `<kbd>`.

**Must not.**
- Do not maintain a hand-written key list; the registry is the source (the static MOUSE group is the only exception).
- Do not open on `?` while a text field is focused (the scope rules of foundation 6 apply).
- Do not animate beyond an 80 ms opacity fade.

**Acceptance.**
- `data-testid`: `nb-shortcuts`, `nb-shortcuts-filter`, `nb-shortcuts-row-<commandId>`.
- Pressing `?` on the canvas mounts `nb-shortcuts`; a row exists for every command that has `keys`, and the row for `flags.toggleBypass` shows `B`.
- Typing `frame` leaves the `view.frameSelection` and `view.frameAll` rows only.
- `Esc` closes it and `.nodebuilder-root` has focus.

## S22. Hint bar, Reset view, minimap (and the Controls fallback)

**Purpose.** The mockup's small helps: a one-line reminder of the basic gestures, a button that brings everything back into view, and a minimap in category colors.

**Placement.** Hint text and `Reset view` in the toolbar (A1) through `toolbarRight` (Reset view, order 10) and a `toolbarCenter` region rendered by `GraphToolbar` itself for the hint (`HintBar.tsx`). Minimap in `CanvasChrome.tsx`.

**Anatomy: hint.** Text sans 11px `--nb-text-dim`, centered in the toolbar's free middle, single line, `white-space: nowrap`, hidden when the toolbar is narrower than 1440px or when the hint would overlap the right cluster (measure once on resize). Text: `Drag node to move · Space+drag to pan · Wheel to zoom · Tab to add`. A `✕` (16x16, dim) at its right dismisses it for this browser (`nb.hint = 'off'`). It also hides while the empty-graph state (foundation 7) is on screen, because that state says the same thing.

**Anatomy: Reset view.** Default button (0.1) `Reset view` with key cap `H`; `title="Frame all nodes (H)"`. Runs `view.frameAll` (fit all nodes of the current network with 80px padding, zoom clamped to [0.15, 1.0], 150 ms ease; reduced motion: instant). At toolbar width < 1440 it becomes an icon button `⌖` with the same `title`. Disabled when the graph has no nodes (`title="Nothing to frame"`).

**Anatomy: minimap.** React Flow `MiniMap`, `position="bottom-left"`, 160x100 (120x76 when the canvas column is under 1000px wide), 8px inset, `style={{background: 'rgba(17,21,29,0.92)', border: '1px solid var(--nb-border)', borderRadius: 4}}`, `nodeColor={n => categoryColor(n.data.cat)}` (boxes: their tint; notes: `--nb-note-border`; unsupported: `--nb-text-dim`), `nodeStrokeWidth={0}`, `nodeBorderRadius={1}`, node opacity 0.7 (CSS on `.react-flow__minimap-node`), `maskColor="rgba(11,14,20,0.6)"`, viewport rect via CSS `.react-flow__minimap-mask { stroke: var(--nb-selection); stroke-width: 1px }`, `pannable` and `zoomable` true. Hidden when the canvas column is under 600px. The React Flow attribution stays bottom-right, restyled: `.react-flow__attribution { background: transparent; color: var(--nb-text-dim); font-size: 10px }`, and it never overlaps the minimap.

**Anatomy: Controls fallback (only if the orchestrator keeps `<Controls>`).** `position="bottom-right"`, 8px inset above the attribution (`bottom: 24px`), buttons 24x24, `--nb-bg-panel` bg, border 1px `--nb-border`, icons (`fill`) `--nb-text-muted`, hover bg `--nb-bg-hover` and icon `--nb-text`, `showInteractive={false}`, `showFitView` runs `view.frameAll`. No white anywhere.

**States.** Hint: visible, dismissed, hidden by width, hidden by empty state. Reset view: enabled, disabled. Minimap: visible, hidden by width; while panning through the minimap the viewport rect follows live.

**Interactions.** Hint `✕` dismisses. `Reset view` click or `H`. Minimap drag pans; wheel over it zooms the main view (React Flow behavior); click jumps the viewport center to that point.

**Copy.** As above.

**Accessibility.** The hint is `aria-hidden` (it repeats what S21 says). `Reset view` is a labeled button. The minimap gets `aria-label="Minimap"` and is not a tab stop (the keyboard path is `H`/`F`).

**Must not.**
- Do not keep the default React Flow Controls styling if the fallback is used (audit B10).
- Do not add zoom buttons anywhere else.
- Do not re-fit the view on panel collapse or graph edits; only `H`, `F`, `Reset view` and first load (`fitView` once when the container has a non-zero size; guard the `display: none` case from audit B7).
- Do not draw more than rects in the minimap (no labels, no ports).

**Acceptance.**
- `data-testid`: `nb-hint`, `nb-hint-dismiss`, `nb-btn-reset-view`.
- At 1600 the hint text is present and reads exactly the sentence above; at 1280 it is absent; after dismiss it is absent on remount (`nb.hint === 'off'`).
- `Reset view` calls the same command as `H` (spy on `view.frameAll.run`).
- Minimap node colors: an indicator node's minimap rect has the fill `#34d399` (computed).
- Screenshot bottom-left: minimap with colored rects and a blue viewport rectangle; bottom-right: no white control box (or, in fallback, dark 24px buttons).

## S23. Wire reconnect, drop-on-wire splice, long-wire fade

**Purpose.** The three wire moves Houdini users expect: pick a wire's end up and move it, drop a node onto a wire to splice it in, and see long wires without visual clutter.

**Placement.** `edges/AttrEdge.tsx` (fade, hot state), `plugins/wireOps.ts` (React Flow `onReconnect`, `onReconnectStart`, `onReconnectEnd`, node drag intersection, splice), `commands/wires.ts` (`wires.delete`, `wires.deleteRewire`, `wires.insertNode`). React Flow props: `edgesReconnectable={editable}`, `reconnectRadius={18}`.

**Anatomy and behavior: reconnect.**
- Hover within 18px of a wire's end near an input port: the port shows the hover state and the cursor is `grab`. Drag: the wire detaches and follows the pointer as a dashed `--nb-wire-drag` line (`6 4`, 2px); its old input port returns to the unconnected look.
- Over a valid input (S08 rules): the target port lights `--nb-wire-drag`; release → `commit('reconnect wire', …)` that updates `to`/`to_port` (and pre-fills the new consumer's unset `attr` param with the source's first write, as in a new connection). The wire id is kept.
- Over an invalid target: the dashed line turns `--nb-wire-invalid` (`4 3`), cursor `not-allowed`; release → the wire snaps back (no commit).
- Release on empty canvas: the line flashes `--nb-wire-invalid` for 150 ms, then the wire is deleted (`commit('delete wire')`) (Houdini behavior, foundation 5.4). `Esc` during the drag cancels and restores the wire.
- Reconnecting the source end (dragging from the output side) works the same way onto another output.

**Anatomy and behavior: splice.**
- While dragging a single node that has at least one free input and an output, the plugin tests, on each `onNodeDrag` (throttled to one test per animation frame), whether the node's center is within 12 flow units of a wire path (use the edge path's `getPointAtLength` sampling cached per edge at drag start, 24 samples per edge, only for edges inside the viewport). Multi-node drags never splice.
- After 150 ms over the same wire it becomes **hot**: `--nb-wire-hover` 2px, and its label shows. The node itself does not change.
- Release while hot and `Cmd` not held: one `commit('splice <name>')` that removes the old wire, adds `source out → node in0` (or the node's first free input), adds `node out → old target port`, and pre-fills the node's unset `attr` param with the source's first write. The old target's `attr` param is untouched (its named read now comes through the new node's pass-through or write).
- `Cmd` held at release: no splice, plain move. Node without a free input or without an output: never hot.
- Release while hot on an invalid case (would create a cycle): the wire flashes `--nb-wire-invalid` 150 ms, plain move.

**Anatomy and behavior: `Insert node…` and delete-with-rewire.**
- Wire context menu `Insert node…` / `Tab` with a wire selected / double-click a wire: the Tab menu opens at the wire's midpoint, filtered to nodes with an input and an output; the chosen node is placed at the midpoint and spliced (same commit as above).
- `Delete` on a single selected node with exactly one input wire and one or more output wires: remove the node and connect its source to every old target port (`commit('delete and rewire')`); the targets' `attr` params are left as they are (a missing attribute then shows as S09 missing). `Shift+Delete` deletes without rewiring. Multi-node deletes never rewire.

**Anatomy: long-wire fade** (foundation 5.3). A wire longer than 600 flow units (straight-line) gets `stroke: url(#nb-fade-<wireId>)` with a `linearGradient` in `userSpaceOnUse` from its start to its end point: stops `0` full, `0.3` alpha 0.35, `0.7` alpha 0.35, `1` full, color = the wire's current color (`--nb-wire` or `--nb-wire-invalid`). Hovered and selected wires use the plain stroke (no fade). The `<defs>` live once in the edge layer (`EdgeDefs` rendered by `CanvasChrome`), keyed by wire id, and update only on drag end, connect, or reconnect, never during drag.

**States.**
| State | Wire |
|---|---|
| reconnect dragging | dashed `--nb-wire-drag` 2px |
| reconnect invalid | dashed `--nb-wire-invalid`, cursor `not-allowed` |
| splice hot | `--nb-wire-hover` 2px, label shown |
| invalid flash | `--nb-wire-invalid` solid 2px for 150 ms |
| long | gradient stroke |
| long + hovered/selected | plain hover/selected stroke |

**Interactions and keys.** `Esc` cancels a reconnect drag. `Cmd` disables splicing. `Delete` / `Shift+Delete` as above. All three operations are exactly one undo step each.

**Copy.** Menu items: `Insert node…`, `Delete`, `Delete without rewiring`. Status-bar flash on an invalid splice: `That would create a loop`.

**Accessibility.** Keyboard splice: select a wire, press `Tab`, pick a node (S24). Keyboard reconnect: the Inspector's Stream section (S14) `Select wire` then `Delete`, and `Connect…` on the new port. Rewire-on-delete is announced in the status bar flash: `Deleted rsi and reconnected 2 wires`.

**Must not.**
- Do not test splice intersections with `document.elementFromPoint` on every move; use the cached path samples.
- Do not recompute gradients per frame; only on drag end.
- Do not keep a dashed wire on the canvas after a cancelled reconnect (audit: ghost wires).
- Do not splice on multi-node drags or when `Cmd` is held.
- Do not change the old target's `attr` params during splice or delete-with-rewire.

**Acceptance.**
- `wireOps.test.ts`: reconnect changes `to_port` and keeps the wire id in one history step; drop on empty deletes; splice of `rsi` onto `ticker → crosses_below` produces two wires with `to_port` `in0` and `a`'s original port, sets `rsi.source = '@close'`, one history step; `Delete` on a node with one input and two outputs produces two wires from the source and one history step; `Shift+Delete` produces none.
- A wire of length 700 renders `stroke="url(#nb-fade-w1)"`; a 300 wire does not; the selected 700 wire uses the plain stroke.
- Render probe: after 20 reconnect-cancel cycles, the edge count equals the wire count (no ghosts).

## S24. Tab menu at the cursor

**Purpose.** Houdini's Tab menu: the one way to add nodes, at the cursor, keyboard first. It also serves wire-drop and splice flows.

**Placement.** `TabMenu.tsx` (existing; restyle to foundation 6.3 and fix placement, focus and idle cost), opened by the `edit.addNode` command (`Tab`), double-click on the pane, the pane menu `Add node…`, wire drag release on empty canvas (S08/S23), `Insert node…`, `Connect…` (S14) and `Replace with…` (S13). Recent list in `nb.recentNodes` (last 5 type names).

**Anatomy.** Foundation 6.3: 440x(max 460) popover at the cursor, search 36px autofocus, 140px category column with counts, result rows 32px, footer hints. Category order: `Recent`, `Tickers`, `Data`, `Indicators`, `Comparisons`, `Logic`, `Math & Signal`, `Rules` (W6), `Settings`, `Code` (W7), `Outputs`, `Networks` (W5), `Library` (W6). Categories with zero visible entries hide. A mode line under the search (20px, `--nb-text-dim` 11px) appears in special modes: `wires from rsi out` (Tab with a selection), `wires into below_entry a` (input drag or Connect…), `splices into rsi → below_entry` (wire modes), `replaces stoch_rising` (S13). Each mode line has a `✕` to drop the mode and just place.

**Placement rules.** Top-left at the cursor; clamp inside the canvas with 8px margin; if the menu would cover the node under the cursor, place it to the right of that node; if that leaves the canvas, to the left. The graph position for the new node is the cursor's flow position **at open time** (stored on open), not the position at pick time. With a selection and no cursor over the canvas (keyboard `Tab` with the pointer elsewhere), the new node goes 40px under the primary selected node, centered.

**States.**
| State | Menu |
|---|---|
| closed | renders `null` (no DOM, no listeners, no filtering work) |
| open, empty query | `Recent` (if any) then the first category with entries; first row active |
| open, query | ranked results across categories (`search.ts`), first row active; category column highlights the active row's category |
| no results | `No nodes match "<query>"` and, from W6, `Search @attributes and assets too` |
| filtered mode (wire drop) | entries that lack the needed port are hidden, not disabled; the mode line names why |
| read-only graph | never opens (`Tab` does nothing; the status bar flashes `Edit this graph to add nodes`) |

**Interactions and keys.**
- `Tab` on the canvas opens; `Tab` inside the menu moves focus between the search and the list (native); `Tab` inside any other text field is the native tab and never opens the menu.
- `↑`/`↓` move; `←`/`→` move between categories when the query is empty; `Enter` places and wires (mode line applies); `Shift+Enter` places without wiring; `Esc` closes (and cancels a wire drop, removing the dashed wire).
- Click a row: same as `Enter`. Click a category: shows it and clears the query.
- Placement commits once (`commit('add <type>')`), including the wire when wiring; the new node becomes the only selection and, for nodes with an `attr` param, gets the source's first write pre-filled.
- Closes on outside pointer-down (the click is not passed to the canvas), `Esc`, loss of window focus, and any canvas pan or zoom. It stays open while the pointer moves.
- Text selection is disabled inside the menu (`user-select: none` except the search input).
- Recent is updated on every placement.

**Copy.** Placeholder `Search nodes, @attributes, assets…` (before W6: `Search nodes…`). Footer `↵ place and wire · ⇧↵ place only · ↑↓ move · esc close`. Mode lines as above.

**Accessibility.** `role="dialog"` `aria-label="Add node"`; the list is `role="listbox"` with `aria-activedescendant`; the category column is `role="tablist"`. Focus returns to the canvas root on close. Rows announce name, description and category.

**Must not.**
- Do not open at the canvas center (audit bug 14).
- Do not keep the component mounted with its listeners while closed (audit bug 17).
- Do not rank or filter on every render; memoize on `(query, category, mode)`.
- Do not include `compile_active: false` entries or Size/Stop stubs.
- Do not leave the dashed drop wire on `Esc`.

**Acceptance.**
- `data-testid`: `nb-tabmenu`, `nb-tabmenu-search`, `nb-tabmenu-row-<type>`, `nb-tabmenu-cat-<category>`, `nb-tabmenu-mode`.
- `Tab` with the pointer at screen (400, 300) opens the menu with its top-left within 8px of that point and the search focused; typing `rsi`, `Enter` adds an `rsi` node whose position equals the flow position of (400, 300).
- With an `rsi` node selected, `Tab`, `Enter` on `crosses_below` adds the node under it, wired from `out` to `in0`, with `a = '@rsi'`.
- Dragging from an output to empty canvas and pressing `Esc` leaves the edge count unchanged.
- When closed, `document.querySelector('[data-testid=nb-tabmenu]')` is `null`.

---

# Wave 4 surfaces

## S25. Data Sheet drawer

**Purpose.** Houdini's Geometry Spreadsheet for streams: every bar, every attribute on the selected wire or node, with who wrote each column, so any signal can be debugged by reading it.

**Placement.** Bottom drawer of the canvas column, under the chart pane (S28), above the status bar. `DataSheet.tsx` exports the component; 4.D mounts it in the `bottomPanel` slot. Data from `POST /inspect` (`api/nodebuilderInspect.ts`). Height 240 default (200 at 1280, 280 at 2560), min 120, max 60% of the column, 6px top handle, persisted in `nb.sheet = {height, open, follow, filter}`. Toggle `S` or the toolbar `▤`.

**Target resolution (follow mode).** In order: the selected wire; else the selected node (primary); else the network's display-flag node; else none. `pin` mode keeps the current target until unpinned or the target is deleted. A wire target asks `/inspect` with `{wire_id}` and shows the source's whole output stream with the consumer's reads highlighted (`read_by_consumer`). A bypassed node shows its input stream (the server does this) with a note in the header.

**Anatomy.**
- **Header row** 28px, `--nb-bg-panel`, border-bottom 1px `--nb-border`, padding `0 10px`, 10px gaps, sans 11px `--nb-text-muted`:
  1. Target chip: mono 11px `--nb-text` on `--nb-bg-elevated`, border 1px `--nb-border`, radius 4, height 20, padding `0 6px`; a leading 8px dot: `--nb-wire-selected` for a wire (`wire spread_z → below_entry`), the node's category color for a node (`node spread_z`), `--nb-flag-display` for the display node (`display spread_z`). Bypassed: suffix ` · input stream (bypassed)`.
  2. Follow select: `follow selection ▾` / `pinned ▾` (20px select styled as the chip).
  3. Filter input: height 20, flex `0 1 260px`, mono 11, bg `--nb-bg-input`, placeholder `only rows where …`. Syntax (parsed client-side into the contract's `filter`): `@x` → `is_true`; `!@x` → `is_false`; `@x > 30` → `gt`; `@x < 30` → `lt`; `@x is set` → `not_nan`. While typing, a small autocomplete (0.4 popover, max 6 rows) offers attribute names and, after a name, the operators. Invalid text: border `--nb-error`, `title="Use @attr, !@attr, @attr > n, @attr < n, or @attr is set"`; the last valid filter stays applied.
  4. Row count mono: `1 258 rows`, or `31 of 1 258 rows` when filtered, or `no rows`.
  5. Jump to trades: `jump to trades ◂ ▸` with two 20x20 icon buttons; a counter `3 / 14` between them once used. Disabled with `title="Run the backtest to get trades"` when there is no graph result.
  6. Detail toggle `detail 3` (text button, only when `detail` is non-empty) that shows or hides the detail strip.
  7. Close `✕` (`title="Close (S)"`) at the far right.
- **Detail strip** (22px, under the header, `--nb-bg-panel`, border-bottom): one chip per detail attribute, `@stop_pct = 2.5` mono 10px in the writer's category tint and color; hover shows `written by stop_loss`.
- **Stale bar** (22px amber, foundation 6.6): `stale — graph changed · Cook (⌘↵)` in `--nb-warn` on `rgba(251,191,36,0.10)`; `Cook` is a text button running the preview cook. Shown when the graph changed since the sheet's `cook_id` was made and auto cook is off (with auto cook on, the sheet refreshes on its own within about a second, so the bar shows only if that fails).
- **Table**: sticky header 28px, virtualized rows 28px (overscan 8), mono 11px, horizontal scroll for many columns. Time column first, sticky left, width 140, left-aligned, `--nb-text-muted`; daily strings as given, intraday unix seconds formatted as ET wall clock `YYYY-MM-DD HH:MM` using the same rule as `toET()` in Chart.tsx (import the helper; do not re-implement). Attribute columns min 96px, right-aligned. Header cell: attribute name mono 11px 500 `--nb-text`, dtype tag `f`/`b` 9px `--nb-text-dim`, a "written by" chip (9px, writer name on the writer's category tint; click selects the writer node), and when the drawer is at least 200px tall a 24px histogram strip under the name (numeric: 20 bars from `stats.hist`, category color at 60% alpha, `nan_count` as a hatched last bar in `--nb-text-dim` when > 0; bool: one bar whose width is `true_count / total` with the text `2.5 % true`). Hovering a histogram bar shows `[41.2, 45.0) · 63 rows` in a tooltip. Columns the consumer reads (wire target) get a 2px top border in `--nb-selection` on the header cell.
  Cells: numbers per 0.7; bools as a filled `--nb-bool-true` cell with `true` in `--nb-text` or an empty cell with `·` in `--nb-text-dim`; zebra rows `--nb-bg-sheet-row-alt`; the hovered row `--nb-bg-hover`; a jumped-to row flashes `rgba(56,189,248,0.10)` for 1.5 s and gets a 2px left bar `--nb-selection` while it is the current trade.
- **Paging**: the sheet keeps at most 2 000 rows in memory. It requests `limit: 500` pages around the visible window (`offset` = first visible row − 250, clamped) and drops pages more than 1 000 rows away from the viewport. `total` drives the scrollbar height.

**States.**
| State | Sheet |
|---|---|
| closed | not rendered; the toolbar toggle unpressed |
| no cook yet (no `cook_id`, auto cook off) | body centered `--nb-text-dim` 12px: `Run the backtest (⌘↵) or turn on auto cook to see data here.` with two text buttons `Run backtest`, `Auto cook on` |
| no target | `Select a node or wire, or set a display flag (D).` |
| loading first page | header shows a 12px spinner after the row count; the previous table stays (dimmed to 0.6) until the new data lands; the first ever load shows `loading rows…` in the body |
| loading another page | rows in the missing page render `…` in `--nb-text-dim` (no layout shift) |
| empty result (filter matches nothing) | `No rows match the filter.` with a `Clear filter` text button |
| stale | amber bar; table stays readable |
| cook expired (410) | the sheet silently re-requests with `graph` and `window`; on failure it shows the error state and the S07 `cook_expired` banner |
| error | one row in the body: `Could not load data: <detail>` with `Retry`; header unchanged |
| target deleted while pinned | falls back to follow mode with a status-bar flash `Pinned target was deleted` |
| resizing | handle `--nb-selection`; the table re-virtualizes on release |

**Interactions and keys.**
- `S` toggles. The 6px handle resizes (double-click resets to the default height).
- Header attribute name click: selects the writer node (same as the chip). Right-click a header: a small menu `Copy column name`, `Filter: only true` / `only false` (bools) or `Filter: is set` (numbers), `Hide column` (session only; a `+N hidden` chip at the end of the header restores).
- Row click: sets the chart crosshair to that bar when the chart pane is open (stretch; uses `setCrosshairPosition` on the existing chart, never a second chart), and updates the status bar flash with `bar 2026-09-04 16:00`.
- `◂` / `▸`: move to the previous/next trade entry time from `graphResult.response.trades` (sorted), request `around_time` for it, scroll it to the vertical center, flash it. `Home`/`End` inside the table go to the first/last row.
- Filter: `Enter` applies, `Esc` clears focus (keeps the filter); the filter persists in `nb.sheet.filter` per target attribute set (drop it when the attribute is no longer in the columns).
- Follow select: `follow selection` / `pin`. Pinning stores the current target.
- Wheel inside the table scrolls the table, never the canvas.
- Keyboard inside the table: `↑`/`↓` move a row focus ring (`--nb-border-focus` 1px inset), `PageUp`/`PageDown`, `Cmd+C` copies the focused row as TSV.

**Copy.** Strings above. Tooltip on the target chip: `Follows the selected wire, then the selected node, then the display node.`

**Accessibility.** The drawer is `role="region"` `aria-label="Data sheet"`. The table is `role="grid"` with `aria-rowcount={total}` and `aria-colcount`; header cells `role="columnheader"` with the written-by chip as a button `aria-label="written by rsi; select node"`; rows `role="row"` with `aria-rowindex`. The histogram strip is `aria-hidden` (its numbers exist in the header tooltip and stats). The filter input has `aria-describedby` a hidden syntax hint. Live region for the row count.

**Must not.**
- Do not render the table without virtualization, and never hold more than 2 000 rows (performance rule 8.6).
- Do not re-implement ET conversion; use the `toET()` rule from Chart.tsx.
- Do not run the backtest to fill the sheet; the sheet reads the cook cache (`cook_id`) and falls back to `/inspect` with `graph` and `window` (D6).
- Do not request `/inspect` on pointer move, viewport move, or every keystroke in the filter; request on target change, page change, filter apply, cook change.
- Do not add a second chart; row-to-chart sync uses the existing chart instance.
- Do not color numbers by sign.

**Acceptance.**
- `data-testid`: `nb-sheet`, `nb-sheet-target`, `nb-sheet-follow`, `nb-sheet-filter`, `nb-sheet-count`, `nb-sheet-jump-prev`, `nb-sheet-jump-next`, `nb-sheet-col-<name>`, `nb-sheet-by-<name>`, `nb-sheet-row-<index>`, `nb-sheet-stale`, `nb-sheet-empty`.
- With a mocked `/inspect` of 1 256 rows: the count reads `1 256 rows`; only about (height / 28 + 16) row elements exist in the DOM; scrolling to row 900 triggers a request with `offset` near 650.
- Intraday `time` value `1709562600` renders as `2024-03-04 09:30` (ET).
- A bool column renders `true` cells with the `--nb-bool-true` background and `·` cells otherwise; its header shows `2.5 % true`.
- Typing `@xb_rsi > 0.5` sends `filter: {attr: '@xb_rsi', op: 'gt', value: 0.5}`; `!@xb_rsi` sends `is_false`.
- With two trades in `graphResult`, `▸` requests `around_time` equal to the first entry time and the flashed row's time equals it.
- Selecting a wire updates the target chip to `wire a → b` and the consumer's read columns get the top border.
- Screenshot at 240px height: header row, histogram strips visible, zebra rows.

## S26. Sparklines on nodes

**Purpose.** Each node shows the shape of what it writes: a tiny line for numbers, a strip for booleans. Sanity at a glance, before opening the sheet.

**Placement.** `nodes/Sparkline.tsx` exports the drawing code and a `SparklineLayer` that owns one `<canvas>` in the React Flow viewport layer (`CanvasChrome` mounts it above edges and below nodes' DOM, transformed with the flow). BaseNode reserves the 28px slot (foundation 4.7) between params and chips and registers its slot rect through a `useSparklineSlot(nodeId)` hook (writes to a ref map, no state). Data from `/preview` (S27) as `PreviewNode` per node id, stored in `store.preview = {cookId, nodes, stale}`.

**Anatomy.** Slot: height 28, full inner width (node width − 19px), shown only when a preview exists for this node, the node is expanded, and zoom >= 0.5.
- Line (`kind: 'line'`): 96 points, min/max-decimated by the server; drawn as a 1px polyline in the category color at 85% alpha, `lineJoin: round`; `null` values break the line (gaps, no interpolation); a 1px dotted (`[1, 2]`) zero or midline in `--nb-border-strong` when `min < 0 < max`; the last non-null value printed at the right end in mono 9px `--nb-text-muted` (format: 2 decimals; >= 1000 → 0 decimals with thin spaces; prices from Tickers 2 decimals), the line stops 30px before the right edge to leave room.
- Bool (`kind: 'bool'`): 96 bars of 2px, height 16 centered, category color at 85% where the bucket share is > 0 (alpha scaled by the share, min 0.35); `<pct> % true` at the right end (one decimal).
- NaN warmup: leading nulls leave the left blank; when `nan_count > 0` a 1px `--nb-text-dim` tick at the top-left marks the warmup length proportionally.
- Stale (`store.preview.stale`): everything in `--nb-text-dim` instead of the category color.
- Multi-write nodes: the primary write only (the server picks it); the Inspector Stream section (S14) shows a 28px sparkline per write in W4 (same drawing code, plain `<canvas>` per row there, because the Inspector holds at most a few).
- Tickers: the `@close` line. Settings nodes and terminals: no sparkline (detail values print as the param row already).

**States.**
| State | Slot |
|---|---|
| no preview yet | slot not reserved (the node is shorter) |
| cooked | drawn |
| stale | dim |
| cooking (preview in flight) | the previous drawing stays; a 2px top bar on the node (foundation 4.9) only for the node currently cooking is not available from `/preview`, so nothing per node; the status bar shows `cooking…` |
| bypassed node | slot hidden (its output is its input; nothing new to show) |
| collapsed node | slot hidden |
| zoom < 0.5 | layer skips drawing entirely |
| error for this node (`nodes[id]` missing while others exist) | slot hidden |

**Interactions.** Hover over the slot for 400 ms: tooltip `@rsi · min 12.1 · max 88.4 · 14 nan` (bool: `@xb_rsi · 2.5 % true`). Click the slot: selects the node and opens the Data Sheet on it (`S` behavior). Nothing else.

**Redraw rules** (foundation 8.3): the layer redraws on preview change, on `onMoveEnd` (zoom or pan end), on node drag end, on node collapse toggle, on panel resize end, and on canvas resize; never per frame. During a drag the layer is hidden (`visibility: hidden`) after 100 ms of movement and shown on drop, so no stale drawings float. Budget: 100 sparklines under 4 ms (measure with `performance.now()` in dev; log once when exceeded).

**Copy.** Tooltip strings above.

**Accessibility.** The canvas layer is `aria-hidden`. Each node exposes the summary as text: BaseNode adds a visually hidden span `sparkline: @rsi from 12.1 to 88.4, last 41.2` when a preview exists.

**Must not.**
- No per-node `<svg>` or `<canvas>` on the canvas (performance rule 8.3). The prototype's inline SVGs are a picture, not the implementation.
- No animation of the line.
- Do not request `/preview` from this component; it only draws what the store holds (S27 owns requests).
- Do not draw when the node builder tab is hidden (`display: none`); check the container size first.
- Do not scale text with zoom below 0.5 (the layer is skipped there anyway).

**Acceptance.**
- `sparkline.test.tsx`: `drawLine(ctx, values, opts)` calls `moveTo`/`lineTo` 95 times for 96 finite values and breaks the path at a `null`; `drawBool` draws N filled rects for N non-zero buckets; the last-value text for `[…, 41.2345]` is `41.23`.
- With a mocked preview for 3 nodes, exactly one `<canvas data-testid="nb-sparkline-layer">` exists and the three nodes each have a 28px slot; nodes without preview have none.
- Setting `preview.stale = true` changes the stroke style to the dim token (spy on `strokeStyle`).
- A drag of 300 ms hides the layer and a drop shows it; `pointermove` without drag does not trigger a redraw (spy on the draw function).
- Screenshot at 100%: thin category-colored lines with a last value; bool strips with a percentage.

## S27. Auto-cook toggle and cook-state indicator

**Purpose.** Houdini cooks on change. Here, auto cook keeps the node data (sparklines and Data Sheet) fresh after every edit, without running the full backtest. The cook state is always visible.

**Placement.** `AutoCookToggle.tsx` (the switch, exported; 4.D mounts it in `toolbarRight` at order 20), `useAutoCook.ts` (the hook: debounced `/preview` after `commit`), cook state in the store (`cook = {kind: 'preview' | 'backtest' | null, status: 'idle' | 'cooking' | 'cooked' | 'stale' | 'failed' | 'cancelled', startedAt, finishedAt, cookId}`), shown in the status bar (S20) and the chart bar (S28). Persisted `nb.autocook` (default `on`). Command `cook.toggleAuto` key `A`; `cook.run` key `Cmd+Enter`; `cook.cancel` `Esc` while cooking with nothing else to cancel.

**Anatomy.** Switch (0.2) with the label `Auto cook` left of it, height 26 aligned with the buttons; `title="Cook node data after each edit (A)"`. When on and a preview is in flight, the knob shows a 1px `--nb-accent-primary` ring pulse is **not** used (no idle animation); instead the status bar spinner carries the motion.

**Behavior of the hook.**
- Trigger: every `commit` that changes the graph (not selection, not viewport, not annotations-only edits like moving a note; box/note text and position changes never cook). Debounce 500 ms after the last commit. Cancel any in-flight preview (AbortController) when a new one starts.
- Precondition: `errorCount === 0` from S05 and the graph has at least one Ticker and one node with an output; otherwise no request, and the cook state becomes `stale` with the status-bar text `stale · fix errors to cook` when errors exist.
- Request: `preview({cook_id: lastCookId, graph, window: sidebarWindow, node_ids: null, points: 96})` where `window` is the sidebar's `{ticker, start, end, interval, source}` (D11).
- Response: `store.preview = {cookId, nodes, stale: false}`; the Data Sheet re-inspects with the new `cook_id` if open; cook state `cooked` at `finishedAt`; sparklines redraw.
- Failure: cook state `failed`; S07 `server_error` banner with `detail` and node link; auto cook stays on (the next commit tries again). Three failures in a row without a success: auto cook turns itself off, banner `Auto cook turned off after 3 failures. Turn it on again from the toolbar.`
- Off: no requests. The Data Sheet's empty state offers `Auto cook on`. Selecting a node when off and no `cook_id` exists shows the sheet's no-cook state; it does not cook.
- The backtest cook (`Run backtest`) also updates `preview` (the backtest response carries `cook_id`; the hook then calls `/preview` with that `cook_id` once, a cache hit, to refresh sparklines).

**States (cook, shown in the status bar and the chart bar).**
| Status | Status bar | Chart bar summary |
|---|---|---|
| idle (never cooked) | `idle` dim | `no result yet · Run backtest (⌘↵)` dim |
| cooking, preview | spinner `cooking… 340 ms · preview` | unchanged |
| cooking, backtest | spinner `cooking… 340 ms` | spinner replaces the first stat |
| cooked, preview | `cooked 12:04:31 · preview` ok for 3 s | unchanged (still `stale ·` if the graph changed since the last backtest) |
| cooked, backtest | `cooked 12:04:31` ok | fresh summary |
| stale | `stale` warn | `stale · ` prefix in warn |
| failed | `cook failed` error | unchanged |
| cancelled | `cancelled` muted for 3 s | unchanged |

**Interactions and keys.** `A` toggles (status-bar flash `auto cook on` / `auto cook off`); the switch toggles; the status-bar segment toggles. `Cmd+Enter` runs the backtest cook regardless of the switch (disabled rules from S01). `Esc` cancels a running backtest cook when no menu, drag or field has priority.

**Copy.** Strings above. Switch `aria-label="Auto cook"`.

**Accessibility.** Switch semantics from 0.2. The cook state segment is `aria-live="polite"` and announces `cooked` and `failed` only (not the elapsed ticks).

**Must not.**
- Do not run `/backtest` from auto cook; only `/preview` (A4).
- Do not cook on selection, viewport, note or box changes.
- Do not `await` the preview inside the commit path; the hook subscribes to the store and schedules.
- Do not leave a request in flight when the node builder unmounts or the tab hides; abort it.
- Do not keep the elapsed timer running when the status bar is not mounted.

**Acceptance.**
- `useAutoCook.test.ts`: two commits 200 ms apart cause one `/preview` call 500 ms after the second (fake timers); a commit while a request is in flight aborts it (spy on `AbortController.abort`); with `errorCount > 0` no call is made and the cook status is `stale`; three rejected calls flip `autoCook` to false and push the banner.
- `data-testid`: `nb-autocook` (`role="switch"`), `nb-status-cook`.
- `A` toggles `aria-checked`; the value survives a remount (`nb.autocook`).
- After a mocked backtest with `cook_id: 'ck_1'`, `/preview` is called once with `cook_id: 'ck_1'`.

## S28. Graph/chart split view and the chart bar

**Purpose.** The graph and its chart are one workspace: the graph above, the price chart with the graph's trades below, resizable, remembered.

**Placement.** `GraphChartSplit.tsx` owns the vertical layout of the canvas column between the toolbar (and banners) and the status bar, using `react-resizable-panels`: `PanelGroup direction="vertical"` with three `Panel`s: `canvas` (min 30%), `chart` (collapsible, default collapsed, open default 35%, min 180px), `sheet` (collapsible, default open at 240px, min 120px, max 60%). `PanelResizeHandle`s are the 6px handles from foundation 3.3. Layout persisted in `nb.split` through the library's `autoSaveId`. The existing `Chart` component is **moved** into the `chart` panel when graph view is active (rendered by `App.tsx` through a prop or portal target), not duplicated.

**Anatomy.**
- **Chart bar** (the collapsed chart panel, 22px, `--nb-bg-panel`, borders top and bottom 1px `--nb-border`, padding `0 10px`, sans 11px `--nb-text-muted`, cursor `pointer`): left `▸ Chart` (`▾ Chart` when open, then the bar is the panel's header); right, mono 11px, the last backtest summary: `<TICKER> <interval> · <n> trades · <+x.x%> · Sharpe <s>` with the return in `--nb-ok` or `--nb-error` by sign, then optional ` · open <+x.x%>` (open position, from `resultsStrip.ts`), ` · Exit not connected` in `--nb-warn` (from `resultsStrip.ts` `EXIT_NOT_CONNECTED`), and the prefix `stale · ` in `--nb-warn` when the graph changed after the run. From W5 the bar shows the displayed group first and `combined` after a `|`. No result: `no result yet · Run backtest (⌘↵)` in `--nb-text-dim`. Tooltip on `stale`: `STALE_TITLE` from `resultsStrip.ts`.
- **Chart panel** (open): the bar stays as its header; below it the `Chart` component fills the panel (`autoSize: true` in lightweight-charts v5; the panel's own size drives it). The chart shows the result's ticker, interval and source (set as the chart's view state directly, never through `onTickerChange`, D10), candles, the graph's trade markers (entry `▲` below the bar in `--nb-ok`, exit `▼` above in `--nb-error`; shorts inverted as the rule chart does), and the app's existing indicator panes as the user has them. A display-flag overlay sub-pane is a stretch goal: if built, it plots the display node's primary numeric write in the node's category color on its own hidden `priceScaleId` (`'nb-display'`), with whitespace entries for warmup bars and `toET()` on timestamps, and a 22px title `display · spread_z` at its top-left.
- **Handles**: 6px, 1px line `--nb-border`, hover `--nb-border-strong`, drag `--nb-selection`, double-click resets the panel to its default size.
- **Results panel** (the app's `Results.tsx`, right side of the app, outside this split): receives `displayedResult` (the graph result while graph view is active) and renders the S30 header line.

**States.**
| State | Split |
|---|---|
| chart collapsed (default) | 22px bar with the summary |
| chart open | bar as header, chart below; the canvas shrinks; the viewport is not re-fitted |
| no result | bar text `no result yet …`; opening the chart shows the chart with the sidebar's ticker and no markers |
| stale | `stale ·` prefix; markers stay (they are the last run) |
| running | a 10px spinner replaces `▸` and the first stat reads `running…` |
| graph view left (Chart tab) | the `Chart` moves back to its normal place; nothing is unmounted twice (see must-not) |
| below 700px column height | the chart's open size clamps to min 180 and the sheet closes first when space runs out |

**Interactions and keys.** Click the bar or `Shift+V` toggles the chart. Drag the handles. `S` toggles the sheet (S25). Switching graphs keeps the layout. Toggling graph view 20 times must not leak chart instances (each mount's cleanup nulls refs before `chart.remove()`).

**Copy.** As above; reuse `STALE_LABEL`, `STALE_TITLE`, `EXIT_NOT_CONNECTED` and the context format `TICKER · interval` from `resultsStrip.ts`; retire the old top results strip in `NodeBuilder.tsx` in favor of this bar (one place for the summary).

**Accessibility.** The chart bar is a `button` with `aria-expanded` and `aria-controls` the chart panel; its summary text is its accessible name. Handles are `role="separator"` with `aria-orientation="horizontal"`, `aria-valuenow` as a percent, and `↑`/`↓` resize by 16px (`react-resizable-panels` provides this). The chart itself is `aria-label="Price chart for <TICKER>"`.

**Must not** (critic 31, Key Bugs Fixed).
- Do not create a second `Chart` implementation or instance; move the existing one.
- Do not add a `ResizeObserver` plus `applyOptions({width, height})` (the F218 60 Hz repaint loop). `autoSize` only.
- Do not touch the teardown guards in `Chart.tsx`: refs nulled before `chart.remove()`, `chartRef.current` read dynamically in `syncWidths`, try/catch around `setVisibleLogicalRange` and `unsubscribe*`.
- Do not write the graph result into `lastRequest` or `backtestResult`; `graphResult` only (D10).
- Do not call `onTickerChange` to show the result's symbol; it clears `backtestResult`.
- Do not animate the panel resize; the library's drag is enough. Collapse and expand animate over `--nb-motion-base` only when `prefers-reduced-motion` is not set.
- Do not re-fit the graph viewport on collapse or expand.
- Do not pass `minBarSpacing` other than the repo's 0.01, and no synthetic wheel events in tests.

**Acceptance.**
- `data-testid`: `nb-split`, `nb-chart-bar`, `nb-chart-panel`, `nb-split-handle-chart`, `nb-split-handle-sheet`.
- Clicking `nb-chart-bar` sets `aria-expanded="true"` and mounts the `Chart` inside `nb-chart-panel`; the chart's series count is one candle series plus the indicator panes the user had.
- With a mocked graph result of 6 trades, `nb-chart-bar` text is `AAPL 1d · 6 trades · +25.8% · Sharpe 1.40`; after a commit it starts with `stale · `.
- A test with a mocked chart API asserts 12 markers for 6 round trips.
- `grep -n "ResizeObserver" frontend/src/features/nodebuilder/GraphChartSplit.tsx frontend/src/features/chart/Chart.tsx` finds nothing new.
- Render probe (`bin/verify-batch.sh F435-W4`): toggling graph view 20 times and dragging the chart handle 10 times produces no console error and no sustained `requestAnimationFrame` loop (frame count over 2 idle seconds < 10).
- Screenshot at 1600 with the chart open: graph above, bar header, chart with markers below, sheet at the bottom, all inside the canvas column.

## S29. "Set by graph" greyed fields

**Purpose.** In graph view the graph owns size, stop and costs (D11). The settings panel shows this instead of pretending those fields still apply.

**Placement.** `frontend/src/features/strategy/StrategyBuilder.tsx` (greying only), driven by `GRAPH_OWNED_FIELDS` from `frontend/src/features/nodebuilder/ownership.ts` and an `graphViewActive` prop. This panel uses the app's `--gh-*` tokens. W4 list: `position_size` (the Position Size mode and value controls), `stop_loss_pct` (`Stop Loss (%)`), `slippage_bps` (`Slippage (bps)` and its source buttons), `commission_pct` (`Commission preset`). W5 adds `direction`, `trailing_stop` (the trailing checkbox and its sub-fields), `max_bars_held`, `borrow_rate_annual`.

**Anatomy.**
- A note at the top of the settings panel (only in graph view), 11px `--gh-text-muted`, padding 8px, border-bottom 1px `--gh-border`: `Graph view: the graph sets size, stop and costs. Date range, capital and data source still come from here.`
- Each graph-owned field group: `opacity: 0.5`, `pointer-events: none` on the controls (the wrapper keeps `title`), the label followed by a 16px pill `GRAPH` (caps 10px 600, `--gh-text-muted` on transparent, border 1px `--gh-border`, radius 3, margin-left 6px) with `title="Set by graph"`. The `title` on the group wrapper is also `Set by graph`.
- Fields that still apply but are not graph-aware (`dynamic sizing`, `skip after stop`, `trading hours`) get a pill `APPLIES TO GRAPH` in the same style, `title="Sent with graph runs too"`.
- Fields the sidebar owns (date range, initial capital, data source) are untouched.

**States.** Graph view on: as above. Graph view off: the panel is exactly as today (no pills, no note). Values are never cleared by greying; leaving graph view restores them as they were.

**Interactions.** None on greyed controls. Clicking the `GRAPH` pill switches the app to graph view's Inspector with the owning settings node selected when one exists (`stop_loss` node for `stop_loss_pct`, and so on); when none exists, a status-bar flash in the node builder: `No stop_loss node in this graph; the engine default applies`. (Optional in W4; the pill may be static first.)

**Copy.** As above.

**Accessibility.** Greyed controls get `disabled` (real attribute, so screen readers announce it) and `aria-describedby` the note's id. The pill is `aria-hidden` when static, a button when interactive.

**Must not.**
- Do not send graph-owned fields in graph runs; `buildGraphRequest` omits them (tested), so a greyed field can never change a result even if the greying fails.
- Do not hide the fields (users need to see what moved); grey them.
- Do not keep a second list of owned fields in `StrategyBuilder.tsx`; import `GRAPH_OWNED_FIELDS`.
- Do not clear or reset the greyed values.

**Acceptance.**
- `ownership.test.tsx`: with `graphViewActive`, the `Stop Loss (%)` input is `disabled`, its group has `title="Set by graph"` and a `GRAPH` pill; without it, no pill and not disabled; changing a greyed value in state does not change the output of `buildGraphRequest`.
- `data-testid`: `sb-graph-note`, `sb-owned-<field>`.
- Screenshot at 1600 in graph view: the four W4 groups at 50% opacity with `GRAPH` pills; date range and capital at full opacity.

## S30. "Not available for graph results" and the graph result header

**Purpose.** The Results panel shows a graph run honestly: which graph, which rev, which window, and which analyses do not apply to it.

**Placement.** `frontend/src/features/strategy/Results.tsx` (takes `displayedResult` with `origin`), `OptimizerPanel.tsx`, `WalkForwardPanel.tsx`, `SensitivityPanel.tsx` (each renders the not-available block when `displayedResult.origin === 'graph'`). App tokens (`--gh-*`).

**Anatomy: graph result header.** A 28px line above the Results summary (inside `Results.tsx`, only when the displayed result is a graph result), 11px, `--gh-text-muted`, border-bottom 1px `--gh-border`: a 16px pill `GRAPH` (`--gh-blue`-tinted: bg `rgba(88,166,255,0.14)`, text `#58a6ff`), then `regime_filtered_rsi @ rev 12 · AAPL 1d · 2025-09-11 → 2026-09-11 · cooked 12:04:31`, then a `stale` badge (amber, same style as the pill) when the graph changed since the run, and a right-aligned text button `Show graph` (switches to graph view when the user is on the Chart tab; hidden when already in graph view). Untitled graphs read `untitled graph`. The existing tab strip stays; tab labels for `Sensitivity`, `Optimizer`, `Walk-Forward` render at 0.6 opacity for graph results (still clickable, so people discover why).

**Anatomy: not-available block.** Replaces the panel body (not the tab), centered, padding 32px: title 13px 600 `--gh-text-primary` `Not available for graph results`; body 12px `--gh-text-muted`, max width 420: `Sensitivity, Optimizer and Walk-Forward work on rule strategies. They re-run a rule request with changed parameters, and a graph has no such request yet.`; a default button `Back to Chart view` (switches the view; the rule result, if any, returns to the panel); below it, 11px `--gh-text-muted`: `Graph parameter sweeps are planned for a later wave.`

**States.** Graph result displayed: header line and, per panel, the block. Rule result displayed: nothing from this surface. No result at all: the existing empty states. Stale graph result: `stale` badge in the header; the summary numbers stay.

**Interactions.** `Show graph` and `Back to Chart view` switch views through the app's existing view toggle (the same handler as `AutoRenderToggle`). Clicking a trade row in the Trades tab, when the node builder's Data Sheet is open, jumps the sheet to that entry (stretch; uses the same `around_time` path as S25's `▸`).

**Copy.** As above.

**Accessibility.** The header line is `role="status"`. The block is a `role="region"` `aria-label="Not available"` with the title as its heading. Dimmed tabs keep full contrast on focus.

**Must not.**
- Do not hide the three tabs; dim them and explain.
- Do not feed a graph result into these panels' request logic; they read `lastRequest`, which a graph run never writes (D10).
- Do not compute the header from `lastRequest`; use `graphResult.request` and `graphResult.graphId/rev`.
- Do not show the header for rule results.

**Acceptance.**
- `graphRun.test.ts`: after a graph run through the run handler, `lastRequest` is unchanged (deep equal) and `graphResult.origin === 'graph'`.
- `data-testid`: `results-graph-header`, `results-not-available`, `results-back-to-chart`.
- With a graph result displayed, the Optimizer tab body contains `Not available for graph results`; with a rule result it does not.
- The header text matches `^GRAPH .+ @ rev \d+ · [A-Z.]+ \w+ · \d{4}-\d{2}-\d{2} → \d{4}-\d{2}-\d{2} · cooked \d{2}:\d{2}:\d{2}$` (ignoring the pill markup).
- Screenshot: header line with the blue `GRAPH` pill above the summary tiles.

---

## Map: plan surface ids to sections here

| Id | Section | Wave item |
|---|---|---|
| S00 | `ui-ux-spec.md` | W1 (all) |
| S01 | Graph toolbar | 1.F |
| S02 | Graph Browser dialog | 1.F |
| S03 | Draft restore prompt | 1.F |
| S04 | Save conflict dialog | 1.F |
| S05 | Diagnostics | 1.G (+ toolbar count in 1.F) |
| S06 | AddBotBar graph picker | 1.F |
| S07 | Notice banners | 1.F (used by all) |
| S08 | Node ports | 2.E |
| S09 | Attribute picker | 2.E |
| S10 | Write chips | 2.E |
| S11 | Wire labels and stream popover | 2.E |
| S12 | Time-of-day and day-of-week widgets | 2.E |
| S13 | Unsupported node | 2.E |
| S14 | Inspector with a selection | 3.A |
| S15 | Inspector with no selection | 3.A |
| S16 | Flags | 3.B |
| S17 | Network boxes | 3.E |
| S18 | Sticky notes | 3.E |
| S19 | Context menus | 3.G |
| S20 | Status bar | 3.H |
| S21 | Shortcut overlay | 3.H |
| S22 | Hint bar, Reset view, minimap | 3.H |
| S23 | Wire reconnect, splice, fade | 3.G |
| S24 | Tab menu at the cursor | 3.0 / 3.D (existing `TabMenu.tsx`) |
| S25 | Data Sheet drawer | 4.B |
| S26 | Sparklines | 4.C |
| S27 | Auto-cook toggle and cook state | 4.C / 4.D |
| S28 | Graph/chart split view | 4.D |
| S29 | Set by graph fields | 4.D |
| S30 | Not available state and graph result header | 4.D |

## Shared `data-testid` and storage key registry

Test ids used above, so no two surfaces collide: prefix `nb-` for node builder surfaces, `sb-` for the strategy settings panel, `results-` for the Results panel, `addbot-` for the bot bar. Storage keys: A5. Command ids referenced: `edit.addNode`, `view.frameAll`, `view.frameSelection`, `flags.setDisplay`, `flags.toggleBypass`, `annotations.newBox`, `annotations.newNote`, `annotations.fitBox`, `annotations.deleteBox`, `wires.delete`, `wires.deleteRewire`, `wires.insertNode`, `cook.run`, `cook.toggleAuto`, `cook.cancel`, `help.shortcuts`, `panels.toggleInspector`, `panels.toggleSheet`, `panels.toggleChart`. Implementers keep these names; the shortcut overlay, the context menus and the tests read them.

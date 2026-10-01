/**
 * The right-click menu's state and rows (item 3.G, spec S19), apart from
 * its React parts (ContextMenu.tsx) so both stay small and the component
 * file only exports components.
 *
 * - `openContextMenu` / `closeContextMenu`: one menu open at a time.
 * - `MENU_LAYOUTS` + `buildMenu`: the rows of each menu, in S19 order, from
 *   the command registry.
 * - `menuCanvas` / `runMenuItem`: run a row against the canvas the menu was
 *   opened on, with the pointer at the menu's spot.
 */

import { useSyncExternalStore, type MouseEvent as ReactMouseEvent } from 'react'
import type { CanvasCtx, TabMenuRequest, XY } from './canvasPlugins'
import { getActiveCanvas, getCommand, isCommandEnabled, listCommands, runCommand, type Command, type CommandMenu } from './commands'
import { isTypingTarget } from './canvasHelpers'
import { formatChord as formatKeyChord } from './shortcutList'
import type { NodeBuilderState } from './store'

// ── Open state ──────────────────────────────────────────────────────────────

export interface ContextMenuRequest {
  kind: CommandMenu
  /** Screen point for the menu's top-left (the pointer). */
  screen: XY
  /** The same point in flow units: where Paste and Add node put things. */
  flow: XY
  /** The canvas the menu belongs to. */
  canvas: CanvasCtx
  /** The param row a 'param' menu was opened on (S19 Param row). */
  target?: ParamTarget
}

/** A node's param, the subject of the param-row menu. */
export interface ParamTarget {
  nodeId: string
  param: string
}

export interface OpenMenu extends ContextMenuRequest {
  seq: number
}

let current: OpenMenu | null = null
let seq = 0
const listeners = new Set<() => void>()

function emit(): void {
  for (const l of [...listeners]) l()
}

/** Open the menu (a second open replaces the first). */
export function openContextMenu(req: ContextMenuRequest): void {
  seq += 1
  current = { ...req, seq }
  emit()
}

/** Close the menu, if open (only that one, when `seqOf` is given). */
export function closeContextMenu(seqOf?: number): void {
  if (!current || (seqOf !== undefined && current.seq !== seqOf)) return
  current = null
  emit()
}

/** The open menu, or null. */
export function getContextMenu(): OpenMenu | null {
  return current
}

function subscribe(l: () => void): () => void {
  listeners.add(l)
  return () => { listeners.delete(l) }
}

/** The open menu (with its sequence number), for the host component. */
export function useOpenMenu(): OpenMenu | null {
  return useSyncExternalStore(subscribe, () => current, () => null)
}

// The param a param-menu row runs against (set while `runMenuItem` runs it).
let runningTarget: ParamTarget | null = null

/**
 * The param the open (or running) param-row menu is about, or null. Param
 * commands (commands/params.ts) read it in `when` and `run`.
 */
export function getMenuTarget(): ParamTarget | null {
  return runningTarget ?? current?.target ?? null
}

/**
 * Right-click on a param row (node card or Inspector, S19 / S14): open the
 * param menu. A text field keeps the browser's own menu (copy, paste).
 */
export function openParamMenu(e: ReactMouseEvent, nodeId: string, param: string): void {
  if (isTypingTarget(e.target)) return
  e.preventDefault()
  // The node's own menu (React Flow's onNodeContextMenu) must not open too.
  e.stopPropagation()
  const canvas = getActiveCanvas()
  if (!canvas) return
  const screen = { x: e.clientX, y: e.clientY }
  let flow = { x: 0, y: 0 }
  try {
    flow = canvas.rf.screenToFlowPosition(screen)
  } catch {
    // No React Flow instance yet: Paste and Add node do not run from this menu.
  }
  openContextMenu({ kind: 'param', screen, flow, canvas, target: { nodeId, param } })
}

// ── Layouts (S19, exact order) ──────────────────────────────────────────────

/** One row of a menu layout. */
export type RowSpec =
  | '-'
  | {
      /** Commands this row can show; the first enabled one wins (else the first registered, disabled). */
      ids?: readonly string[]
      /**
       * Commands whose `menuSlot` is this name also fill the row (EA-11): the
       * place a later wave's command takes without this layout naming an id
       * that does not exist yet. Several commands in one slot give several rows.
       */
      slot?: string
      /** A command tagged for this menu with this key also fills the row. */
      key?: string
      /** Row text instead of the command label. */
      label?: string
      /** Show the row only while its command is enabled. */
      hideWhenDisabled?: boolean
      /** Extra reason to show the row disabled (on top of the command's `when`). */
      disabled?(s: NodeBuilderState): string | null
    }
  | { submenu: string }

/** True when the one selected node has wires on both sides (else `Delete without rewiring` is the same as Delete). */
function noWiresToReconnect(s: NodeBuilderState): string | null {
  const g = s.graph
  if (!g || s.selectedNodeIds.length !== 1) return null
  const id = s.selectedNodeIds[0]
  const both = g.wires.some(w => w.to === id) && g.wires.some(w => w.from === id)
  return both ? null : 'No wires to reconnect'
}

// Rows name only ids that exist today. A later wave's command reaches its S19
// place through its key (`key`: Collapse X, Show data S, Collapse into
// network Shift+C, Up U) or its `menuSlot` (`slot`: Save as asset, Promote,
// Use expression), so nothing here guesses a future id.
export const MENU_LAYOUTS: Record<CommandMenu, readonly RowSpec[]> = {
  node: [
    { ids: ['nodes.replace'], hideWhenDisabled: true },
    { ids: ['edit.rename'], key: 'f2' },
    { ids: ['flags.setDisplay'], key: 'd' },
    { ids: ['flags.toggleBypass'], key: 'b' },
    { key: 'x', slot: 'node.collapse' },
    '-',
    { key: 's', slot: 'data' },
    { ids: ['view.frameSelection'], key: 'f', label: 'Frame' },
    '-',
    { ids: ['clipboard.cut'], key: 'mod+x' },
    { ids: ['clipboard.copy'], key: 'mod+c' },
    { ids: ['clipboard.paste'], key: 'mod+v' },
    { ids: ['clipboard.duplicate'], key: 'mod+d' },
    '-',
    { key: 'shift+c', slot: 'node.network' },
    '-',
    { ids: ['wires.deleteRewire', 'edit.delete'], label: 'Delete' },
    { ids: ['edit.deleteNoRewire'], label: 'Delete without rewiring', disabled: noWiresToReconnect },
  ],
  wire: [
    { key: 's', slot: 'data' },
    { ids: ['wires.insertNode'] },
    '-',
    { ids: ['wires.delete'], label: 'Delete' },
  ],
  pane: [
    { ids: ['edit.addNode'] },
    { ids: ['clipboard.paste'], key: 'mod+v' },
    '-',
    { ids: ['annotations.newNote'], key: 'shift+n' },
    { ids: ['annotations.newBox'], key: 'shift+b' },
    '-',
    { ids: ['view.frameAll'], key: 'h' },
    { ids: ['layout.tidy'], key: 'l' },
    { ids: ['view.toggleSnap'], key: 'g' },
    '-',
    { key: 'u', slot: 'pane.network' },
  ],
  box: [
    { ids: ['annotations.renameBox'] },
    { submenu: 'Tint' },
    { ids: ['annotations.fitBox'] },
    '-',
    { ids: ['annotations.deleteBox'] },
  ],
  note: [
    { ids: ['annotations.editNote'] },
    { submenu: 'Color' },
    '-',
    { ids: ['annotations.deleteNote'] },
  ],
  param: [
    { ids: ['params.reset'] },
    { ids: ['params.copyValue'] },
    '-',
    { slot: 'param' },
  ],
}

// ── Building the rows ───────────────────────────────────────────────────────

/** Extra fields a command may carry for menus (commands/annotations.ts). */
type MenuCommand = Command & { submenu?: string; swatch?: string }

export interface MenuItemRow {
  type: 'item'
  cmd: MenuCommand
  label: string
  /** Key cap text (from `cmd.keys`), or ''. */
  keyCap: string
  disabled: boolean
  /** Why it is disabled (the row's `title`). */
  reason: string | null
  /** null: not a toggle row. */
  checked: boolean | null
  destructive: boolean
  swatch: string | null
}

export interface MenuSubRow {
  type: 'submenu'
  label: string
  items: MenuItemRow[]
  disabled: boolean
}

export type MenuRow = MenuItemRow | MenuSubRow | { type: 'sep' }

function tagged(cmd: Command, kind: CommandMenu): boolean {
  const m = cmd.menu
  if (!m) return false
  return typeof m === 'string' ? m === kind : m.includes(kind)
}

/**
 * A chord as a key cap. The one platform-aware formatter the `?` overlay,
 * the Inspector and this menu share (UX-15): `mod+shift+z` gives `⌘⇧Z` on
 * a Mac and `Ctrl+Shift+Z` elsewhere.
 */
export function formatChord(chord: string, mac?: boolean): string {
  return formatKeyChord(chord, mac)
}

function itemRow(cmd: MenuCommand, s: NodeBuilderState, label?: string, extra?: string | null): MenuItemRow {
  const enabled = isCommandEnabled(cmd, s)
  const reason = !enabled ? (cmd.disabledReason?.(s) ?? null) : extra ?? null
  const disabled = !enabled || !!extra
  const text = label ?? cmd.label
  return {
    type: 'item',
    cmd,
    label: text,
    keyCap: cmd.keys && cmd.keys.length > 0 ? formatChord(cmd.keys[0]) : '',
    disabled,
    reason,
    checked: cmd.checked ? cmd.checked(s) : null,
    destructive: /^delete/i.test(text),
    swatch: cmd.swatch ?? null,
  }
}

/**
 * The rows of a menu, in S19 order: separators between groups (never
 * doubled, never first or last). Pure apart from reading the registry.
 */
export function buildMenu(
  kind: CommandMenu,
  s: NodeBuilderState,
  editable: boolean,
  commands: readonly Command[] = listCommands(),
): MenuRow[] {
  const all = commands as readonly MenuCommand[]
  const byId = (id: string) => {
    for (let i = all.length - 1; i >= 0; i--) if (all[i].id === id) return all[i]
    return null
  }
  const placed = new Set<string>()
  const placedSubmenus = new Set<string>()
  const rows: MenuRow[] = []
  // A read-only graph keeps the commands that say they do not edit (EA-11:
  // a property of the command, not an id list here).
  const keep = (cmd: Command) => editable || !!cmd.readOnlyOk

  const submenu = (name: string): MenuSubRow | null => {
    const items = all.filter(c => tagged(c, kind) && c.submenu === name && keep(c))
    for (const c of items) placed.add(c.id)
    placedSubmenus.add(name)
    if (items.length === 0) return null
    const rowsIn = items.map(c => itemRow(c, s))
    return { type: 'submenu', label: name, items: rowsIn, disabled: rowsIn.every(r => r.disabled) }
  }

  for (const spec of MENU_LAYOUTS[kind]) {
    if (spec === '-') {
      rows.push({ type: 'sep' })
      continue
    }
    if ('submenu' in spec) {
      const row = submenu(spec.submenu)
      if (row) rows.push(row)
      continue
    }
    const candidates = (spec.ids ?? []).map(byId).filter((c): c is MenuCommand => c !== null)
    if (spec.slot) {
      // Every command in the slot gets its own row, in registration order.
      const inSlot = all.filter(c => tagged(c, kind) && c.menuSlot === spec.slot && !candidates.includes(c)
        && (getCommand(c.id) === c || byId(c.id) === c))
      if (candidates.length === 0 && !spec.key) {
        for (const c of inSlot) {
          placed.add(c.id)
          if (keep(c) && !(spec.hideWhenDisabled && !isCommandEnabled(c, s))) rows.push(itemRow(c, s, undefined, spec.disabled?.(s) ?? null))
        }
        continue
      }
      candidates.push(...inSlot)
    }
    if (spec.key) {
      const byKey = all.filter(c => tagged(c, kind) && c.keys?.includes(spec.key!) && !candidates.includes(c))
      candidates.push(...byKey)
    }
    for (const c of candidates) placed.add(c.id)
    const usable = candidates.filter(keep)
    if (usable.length === 0) continue
    const cmd = usable.find(c => isCommandEnabled(c, s)) ?? usable[0]
    if (spec.hideWhenDisabled && !isCommandEnabled(cmd, s)) continue
    rows.push(itemRow(cmd, s, spec.label, spec.disabled?.(s) ?? null))
  }

  // Commands tagged for this menu that no row placed: at the end.
  const rest: MenuRow[] = []
  for (const c of all) {
    if (!tagged(c, kind) || placed.has(c.id) || !keep(c)) continue
    if (c.submenu) {
      if (placedSubmenus.has(c.submenu)) continue
      const row = submenu(c.submenu)
      if (row) rest.push(row)
      continue
    }
    // The newest registration of an id wins (getCommand's rule).
    if (getCommand(c.id) !== c && byId(c.id) !== c) continue
    placed.add(c.id)
    rest.push(itemRow(c, s))
  }
  if (rest.length > 0) rows.push({ type: 'sep' }, ...rest)

  // Tidy the separators.
  const out: MenuRow[] = []
  for (const r of rows) {
    if (r.type === 'sep' && (out.length === 0 || out[out.length - 1].type === 'sep')) continue
    out.push(r)
  }
  while (out.length > 0 && out[out.length - 1].type === 'sep') out.pop()
  return out
}

// ── Running a row ───────────────────────────────────────────────────────────

/**
 * The canvas as a command run from the menu sees it: the pointer is where
 * the menu was opened (Paste goes there), and the Tab menu opens there too.
 */
export function menuCanvas(req: ContextMenuRequest): CanvasCtx {
  const base = req.canvas
  const ctx = Object.create(base) as CanvasCtx
  ctx.pointer = () => ({ ...req.flow })
  ctx.pointerOnCanvas = () => true
  ctx.openTabMenu = (r: TabMenuRequest = {}) => base.openTabMenu({ screen: req.screen, ...r })
  return ctx
}

/** Run a menu row's command against the menu's canvas. Returns true when it ran. */
export function runMenuItem(row: MenuItemRow, req: ContextMenuRequest): boolean {
  if (row.disabled) return false
  const before = runningTarget
  runningTarget = req.target ?? null
  try {
    return runCommand(row.cmd.id, { canvas: menuCanvas(req) })
  } finally {
    runningTarget = before
  }
}

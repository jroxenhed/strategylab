/**
 * The rows of the `?` shortcut overlay (F435 W3 item 3.H, spec S21), built
 * from the command registry. ShortcutHelp.tsx draws them.
 */

import { isCommandEnabled, listCommands, type Command } from './commands'
import { isMacPlatform } from './graphText'

/** Group titles by command id prefix, in display order (S21's fixed list). */
const GROUPS: ReadonlyArray<[string, string]> = [
  ['graph', 'GRAPH'],
  ['history', 'HISTORY'],
  ['selection', 'SELECTION'],
  ['edit', 'EDIT'],
  ['flags', 'FLAGS'],
  ['wires', 'WIRES'],
  ['view', 'VIEW'],
  ['panels', 'PANELS'],
  ['annotations', 'ANNOTATIONS'],
  ['network', 'NETWORKS'],
  ['code', 'CODE'],
  ['help', 'HELP'],
]

/**
 * Id prefixes S21 has no group for, filed under the group they belong to
 * (UX-16): cut/copy/paste and Replace are edits, Tidy is a view action, Run
 * acts on the graph. An unknown prefix still gets its own group at the end.
 */
const GROUP_ALIASES: Readonly<Record<string, string>> = {
  clipboard: 'edit',
  nodes: 'edit',
  layout: 'view',
  cook: 'graph',
}

/** The gestures (foundation 6.1). Not commands, so written here. */
const MOUSE_ROWS: ReadonlyArray<[string, string]> = [
  ['Marquee', 'drag empty'],
  ['Pan', 'Space+drag · middle drag'],
  ['Zoom', 'wheel'],
  ['Add to selection', '⇧click'],
  ['Duplicate', '⌥drag'],
  ['Splice', 'drop node on wire'],
  ['Rename', 'double-click header'],
]

const KEY_NAMES: Record<string, [mac: string, other: string]> = {
  ' ': ['Space', 'Space'],
  space: ['Space', 'Space'],
  tab: ['Tab', 'Tab'],
  enter: ['↩', 'Enter'],
  escape: ['Esc', 'Esc'],
  // The Mac key labelled delete is Backspace; one cap for both on a Mac.
  delete: ['⌫', 'Del'],
  backspace: ['⌫', 'Backspace'],
  home: ['Home', 'Home'],
  end: ['End', 'End'],
  arrowup: ['↑', '↑'],
  arrowdown: ['↓', '↓'],
  arrowleft: ['←', '←'],
  arrowright: ['→', '→'],
  contextmenu: ['Menu', 'Menu'],
}

const MOD_NAMES: Record<string, [mac: string, other: string]> = {
  mod: ['⌘', 'Ctrl'],
  alt: ['⌥', 'Alt'],
  shift: ['⇧', 'Shift'],
  ctrl: ['⌃', 'Ctrl'],
}

/**
 * A chord as a key cap: `mod+shift+z` → `⌘⇧Z` on a Mac, `Ctrl+Shift+Z`
 * elsewhere. The one formatter for every key cap in the builder (the `?`
 * overlay, the Inspector's Keys list and bulk caps, the right-click menu).
 */
export function formatChord(chord: string, mac: boolean = isMacPlatform()): string {
  // A chord on the `+` key itself ends in `++`.
  const parts = chord.endsWith('++') ? [...chord.slice(0, -2).split('+').filter(Boolean), '+'] : chord.split('+')
  let key = parts.pop() ?? ''
  let mods = parts
  // `?` already means Shift held; `shift+?` is the same key press.
  if (key === '?') mods = mods.filter(m => m !== 'shift')
  const named = KEY_NAMES[key]
  key = named ? named[mac ? 0 : 1] : key.length === 1 || /^f\d+$/.test(key) ? key.toUpperCase() : key
  const modCaps = mods.map(m => MOD_NAMES[m]?.[mac ? 0 : 1] ?? m)
  return mac ? [...modCaps, key].join('') : [...modCaps, key].join('+')
}

/** The distinct key caps of a command, in order. */
export function keyCaps(cmd: Command, mac: boolean = isMacPlatform()): string[] {
  const out: string[] = []
  for (const k of cmd.keys ?? []) {
    const cap = formatChord(k, mac)
    if (!out.includes(cap)) out.push(cap)
  }
  return out
}

export interface ShortcutRow {
  id: string
  label: string
  caps: string[]
  enabled: boolean
}

export interface ShortcutGroup {
  key: string
  title: string
  rows: ShortcutRow[]
}

/** The overlay's groups for the registry as it is now, filtered by `query`. */
export function shortcutGroups(query: string, commands: readonly Command[] = listCommands(), mac = isMacPlatform()): ShortcutGroup[] {
  // The newest registration of an id wins (getCommand's rule); keep first-seen order.
  const byId = new Map<string, Command>()
  for (const c of commands) byId.set(c.id, c)
  const q = query.trim().toLowerCase()
  const matches = (label: string, caps: string[]) =>
    !q || label.toLowerCase().includes(q) || caps.some(c => c.toLowerCase().includes(q))

  const groups = new Map<string, ShortcutGroup>()
  for (const cmd of byId.values()) {
    if (!cmd.keys || cmd.keys.length === 0) continue
    const caps = keyCaps(cmd, mac)
    if (!matches(cmd.label, caps)) continue
    const rawPrefix = cmd.id.includes('.') ? cmd.id.slice(0, cmd.id.indexOf('.')) : cmd.id
    const prefix = GROUP_ALIASES[rawPrefix] ?? rawPrefix
    let g = groups.get(prefix)
    if (!g) {
      const title = GROUPS.find(([k]) => k === prefix)?.[1] ?? prefix.toUpperCase()
      g = { key: prefix, title, rows: [] }
      groups.set(prefix, g)
    }
    let enabled = true
    try {
      enabled = isCommandEnabled(cmd)
    } catch {
      enabled = true
    }
    g.rows.push({ id: cmd.id, label: cmd.label, caps, enabled })
  }
  const rank = (k: string) => {
    const i = GROUPS.findIndex(([p]) => p === k)
    return i < 0 ? GROUPS.length : i
  }
  const out = [...groups.values()].sort((a, b) => rank(a.key) - rank(b.key) || a.key.localeCompare(b.key))
  const mouse = MOUSE_ROWS.filter(([label, gesture]) => matches(label, [gesture]))
  if (mouse.length > 0) {
    out.push({
      key: 'mouse',
      title: 'MOUSE',
      rows: mouse.map(([label, gesture]) => ({ id: `mouse.${label}`, label, caps: [gesture], enabled: true })),
    })
  }
  return out
}

/** Split groups into three columns in reading order, about the same number of rows each. */
export function shortcutColumns(groups: ShortcutGroup[]): ShortcutGroup[][] {
  const total = groups.reduce((n, g) => n + g.rows.length + 1, 0)
  const per = Math.ceil(total / 3)
  const cols: ShortcutGroup[][] = [[], [], []]
  let col = 0
  let used = 0
  for (const g of groups) {
    if (used > 0 && used + g.rows.length + 1 > per && col < 2) {
      col++
      used = 0
    }
    cols[col].push(g)
    used += g.rows.length + 1
  }
  return cols
}

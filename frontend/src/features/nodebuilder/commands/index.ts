/**
 * Command registry for the node builder (plan W1 item 1.E, spec 0.8;
 * auto-registry added in W3 pre-step 3.0).
 *
 * Every keyboard shortcut and every menu action is a command with an id, a
 * label, its key chords and a scope. The canvas owns one keydown listener
 * and hands each key press to `dispatchKey`, which finds the matching
 * command and runs it. Menus, the toolbar and the `?` overlay use
 * `listCommands` and `runCommand`.
 *
 * Command modules live in `commands/*.ts` and are loaded automatically. A
 * module exports its commands instead of registering them:
 *
 *   // commands/flags.ts
 *   export const commands: Command[] = [{ id: 'flags.toggleBypass', label: 'Bypass', keys: ['b'], run(ctx) { ... } }]
 *
 * Registering at load time from a module in commands/ would run before this
 * file has finished loading; exporting avoids that. `registerCommand` is for
 * code that registers later (NodeBuilder's Save, tests).
 *
 * Chords are written like `mod+shift+z`: modifiers in the order
 * mod, alt, shift, then the key in lower case. `mod` is Cmd on a Mac and
 * Ctrl elsewhere; either key counts, so Ctrl+Z also undoes on a Mac.
 */

import type { CanvasCtx } from '../canvasPlugins'
import { getActiveCanvas } from '../screen'
import { useNodeBuilderStore, type NodeBuilderState, type NodeBuilderStoreApi } from '../store'

export { getActiveCanvas, setActiveCanvas } from '../screen'

/**
 * Where a command's keys work (foundation 6.2). `canvas`: while the canvas
 * is the active area (Canvas dispatches these). `global`: anywhere in the
 * node builder, in every mode (NodeBuilder's useGlobalKeys dispatches these).
 * Neither fires while typing in a text field unless the command sets
 * `inFields`, so a field keeps its own Cmd+Z.
 */
export type CommandScope = 'canvas' | 'global'

/** The right-click menus a command can appear in (S19). */
export type CommandMenu = 'node' | 'wire' | 'pane' | 'box' | 'note' | 'param'

/** What a command gets when it runs. */
export interface CommandCtx {
  /** The mounted canvas, or null when none is on screen. Canvas-scope keys always come with one. */
  canvas: CanvasCtx | null
  store: NodeBuilderStoreApi
  /** The key press that ran the command, or null (a menu row or a button). */
  event: KeyboardEvent | null
}

export interface Command {
  /** `<group>.<name>`, e.g. 'flags.toggleBypass'. The group titles the `?` overlay section. */
  id: string
  /** Shown in menus and the shortcut overlay. */
  label: string
  /** Key chords, e.g. ['mod+shift+z', 'mod+y']. None: menu or button only. */
  keys?: string[]
  /** Where the keys work. Default 'canvas'. */
  scope?: CommandScope
  /** Right-click menus that list this command (S19). The menu orders its rows itself. */
  menu?: CommandMenu | readonly CommandMenu[]
  /** When false the command is skipped (keys) and shown disabled (menus). */
  when?(s: NodeBuilderState): boolean
  /** Why `when` is false, for a disabled menu row's tooltip ("Nothing to paste"). */
  disabledReason?(s: NodeBuilderState): string | null
  /** For toggle rows: true shows a checkmark (Bypass, Snap to grid). */
  checked?(s: NodeBuilderState): boolean
  /** Older form of `when` with no state argument. Both are checked. */
  enabled?(): boolean
  /**
   * Also runs while a text field has focus (Cmd+S, Cmd+Enter: no field has
   * its own meaning for them). The field is blurred first, so a param row
   * commits the value being typed before the command runs. Leave it unset
   * for keys a field uses itself (Cmd+Z).
   */
  inFields?: boolean
  /**
   * Its keys also work in the read-only strategy view (S19: Copy, Frame,
   * Frame all, Snap to grid). Every other canvas key is ignored there.
   */
  readOnlyOk?: boolean
  /**
   * Which of several commands with the same key gets the first try. Higher
   * first; default 0. Among equal priorities the newest registration goes
   * first (auto-loaded modules register in file-name order). See the
   * shared-chord table above `findCommands`.
   */
  priority?: number
  /**
   * Menu slot for a command no layout row names by id (contextMenuModel.ts
   * MENU_LAYOUTS rows with `slot`). Lets a later wave put its command at its
   * S19 place without the menu naming an id before it exists.
   */
  menuSlot?: string
  /**
   * Runs the command. Return false to say "not handled": an older command
   * with the same key gets a turn, and if none takes it the key keeps its
   * normal browser behavior (e.g. Tab moving focus between buttons).
   */
  run(ctx: CommandCtx): boolean | void
}

/** What the dispatcher needs to know about where the key press happened. */
export interface KeyContext {
  /** The scopes that may act on this key press. */
  scopes: ReadonlySet<CommandScope>
  /** The canvas the key belongs to; default: the active canvas. */
  canvas?: CanvasCtx | null
  /** The key was pressed on a read-only view: only `readOnlyOk` commands run. */
  readOnly?: boolean
}

// Registration order matters: the most recently registered match wins, so
// a canvas can override a built-in for as long as it is mounted.
const registry: Command[] = []

/** Add a command. Returns a function that removes it again. */
export function registerCommand(cmd: Command): () => void {
  loadCommandModules()
  registry.push(cmd)
  return () => {
    const i = registry.indexOf(cmd)
    if (i >= 0) registry.splice(i, 1)
  }
}

/** Add several commands. Returns a function that removes all of them. */
export function registerCommands(cmds: readonly Command[]): () => void {
  const undo = cmds.map(registerCommand)
  return () => { for (const u of undo) u() }
}

/** Every registered command, in registration order (for menus and the `?` overlay). */
export function listCommands(): readonly Command[] {
  loadCommandModules()
  return registry
}

/** The newest registered command with this id, or null. */
export function getCommand(id: string): Command | null {
  loadCommandModules()
  for (let i = registry.length - 1; i >= 0; i--) {
    if (registry[i].id === id) return registry[i]
  }
  return null
}

/** True when the command may run now (`when` and `enabled` both allow it). */
export function isCommandEnabled(cmd: Command, s: NodeBuilderState = useNodeBuilderStore.getState()): boolean {
  if (cmd.enabled && !cmd.enabled()) return false
  if (cmd.when && !cmd.when(s)) return false
  return true
}

/** The chord of a key press, in the same form as `Command.keys`. */
export function chordOf(e: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'altKey' | 'shiftKey'>): string {
  const parts: string[] = []
  if (e.metaKey || e.ctrlKey) parts.push('mod')
  if (e.altKey) parts.push('alt')
  if (e.shiftKey) parts.push('shift')
  parts.push((e.key ?? '').toLowerCase())
  return parts.join('+')
}

/*
 * Shared chords (EA-3). Several commands answer the same key; each returns
 * false when the press is not its case, so the next one gets a turn. Order:
 * `priority` (higher first), then newest registration first.
 *
 *   tab               wires.insertNode (a wire selected), edit.addNode
 *   delete/backspace  wires.deleteRewire, wires.delete, edit.delete
 *   f2                annotations.edit (a box or note, no node), edit.rename
 *
 * A later wave that needs the first try on one of these (a network Delete,
 * Tab into a network) sets `priority: 10` instead of relying on its file
 * name sorting after view.ts / wires.ts.
 */

/** Every enabled command a chord would run in these scopes, in the order they get a try. */
export function findCommands(chord: string, scopes: ReadonlySet<CommandScope>): Command[] {
  loadCommandModules()
  const s = useNodeBuilderStore.getState()
  const out: Command[] = []
  for (let i = registry.length - 1; i >= 0; i--) {
    const cmd = registry[i]
    if (!scopes.has(cmd.scope ?? 'canvas')) continue
    if (!cmd.keys?.includes(chord)) continue
    if (!isCommandEnabled(cmd, s)) continue
    out.push(cmd)
  }
  // Stable sort: equal priorities keep newest-first.
  if (out.some(c => c.priority)) out.sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0))
  return out
}

/** The command a chord would run in the given scopes, or null. */
export function findCommand(chord: string, scopes: ReadonlySet<CommandScope>): Command | null {
  return findCommands(chord, scopes)[0] ?? null
}

// ── The active canvas ───────────────────────────────────────────────────────
// Mounted canvases register in screen.ts (a stack: the last mounted or last
// pressed is active), so a command run from a button or a panel (Reset
// view, the Inspector) can still reach React Flow. getActiveCanvas and
// setActiveCanvas are re-exported above.

/** The context a command runs with. */
export function commandCtx(event: KeyboardEvent | null, canvas: CanvasCtx | null = getActiveCanvas()): CommandCtx {
  return { canvas, store: useNodeBuilderStore, event }
}

/**
 * Run the commands matching this key press in order (newest first) until
 * one handles it. Returns true when one did; the event's default action is
 * then prevented.
 */
export function dispatchKey(e: KeyboardEvent, ctx: KeyContext): boolean {
  if (e.defaultPrevented) return false
  const found = findCommands(chordOf(e), ctx.scopes)
  const cmds = ctx.readOnly ? found.filter(c => c.readOnlyOk) : found
  if (cmds.length === 0) return false
  const run = commandCtx(e, ctx.canvas === undefined ? getActiveCanvas() : ctx.canvas)
  for (const cmd of cmds) {
    if (cmd.run(run) === false) continue
    e.preventDefault()
    return true
  }
  return false
}

/**
 * Run a command by id from a menu row, a button or a panel. Returns true
 * when it ran and handled the action; false when it does not exist, is
 * disabled now, or said "not handled".
 */
export function runCommand(id: string, opts: { event?: KeyboardEvent | null; canvas?: CanvasCtx | null } = {}): boolean {
  const cmd = getCommand(id)
  if (!cmd || !isCommandEnabled(cmd)) return false
  const canvas = opts.canvas === undefined ? getActiveCanvas() : opts.canvas
  return cmd.run(commandCtx(opts.event ?? null, canvas)) !== false
}

// ── Auto-register commands/*.ts ─────────────────────────────────────────────
// Sorted by file name, so the order (and so which of two same-key commands
// wins at equal priority) is the same on every load. useGlobalKeys is a
// hook, not a command module.
//
// The modules' `commands` exports are read lazily, on the first registry
// call, not while this file loads (EA-2). A command module can import this
// file (or a helper that does: viewOps.ts, contextMenuModel.ts) and be the
// first module to load it; reading its export here at load time would see
// it uninitialized (dropped in vitest, a TDZ error in the browser). By the
// first registry call every module has finished loading. The glob commands
// go in front of anything registered earlier, so a command registered at
// runtime (NodeBuilder's Save, a test) still counts as newer.

interface CommandModule {
  commands?: readonly Command[]
}

const modules = import.meta.glob<CommandModule>(
  ['./*.ts', '!./index.ts', '!./useGlobalKeys.ts', '!./*.test.ts'],
  { eager: true },
)
let modulesLoaded = false

function loadCommandModules(): void {
  if (modulesLoaded) return
  modulesLoaded = true
  const fromModules: Command[] = []
  for (const path of Object.keys(modules).sort()) {
    const list = modules[path].commands
    if (list) fromModules.push(...list)
  }
  registry.unshift(...fromModules)
}

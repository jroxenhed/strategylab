/**
 * Command registry for the node builder (plan W1 item 1.E, spec 0.8).
 *
 * Every keyboard shortcut is a command with an id, a label, its key chords
 * and a scope. The canvas owns one keydown listener and hands each key press
 * to `dispatchKey`, which finds the matching command and runs it. Later
 * waves (and other parts of the editor, such as Save) add their commands
 * with `registerCommand`; the `?` overlay (W3) lists them from `listCommands`.
 *
 * Chords are written like `mod+shift+z`: modifiers in the order
 * mod, alt, shift, then the key in lower case. `mod` is Cmd on a Mac and
 * Ctrl elsewhere; either key counts, so Ctrl+Z also undoes on a Mac.
 */

import { historyCommands } from './history'

/**
 * Where a command works (foundation 6.2). `canvas`: while the canvas is the
 * active area (Canvas dispatches these). `global`: anywhere in the node
 * builder, in every mode (NodeBuilder's useGlobalKeys dispatches these).
 * Neither fires while typing in a text field unless the command sets
 * `inFields`, so a field keeps its own Cmd+Z.
 */
export type CommandScope = 'canvas' | 'global'

export interface Command {
  id: string
  label: string
  /** Key chords, e.g. ['mod+shift+z', 'mod+y']. */
  keys: string[]
  scope: CommandScope
  /**
   * Runs the command. Return false to say "not handled": the key then keeps
   * its normal browser behavior (e.g. Tab moving focus between buttons).
   */
  run(event: KeyboardEvent): boolean | void
  /** Optional: when false the command is skipped. */
  enabled?(): boolean
  /**
   * Also runs while a text field has focus (Cmd+S, Cmd+Enter: no field has
   * its own meaning for them). The field is blurred first, so a param row
   * commits the value being typed before the command runs. Leave it unset
   * for keys a field uses itself (Cmd+Z).
   */
  inFields?: boolean
}

/** What the dispatcher needs to know about where the key press happened. */
export interface KeyContext {
  /** The scopes that may act on this key press. */
  scopes: ReadonlySet<CommandScope>
}

// Registration order matters: the most recently registered match wins, so
// a canvas can override a built-in for as long as it is mounted.
const registry: Command[] = []

/** Add a command. Returns a function that removes it again. */
export function registerCommand(cmd: Command): () => void {
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
  return registry
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

/** The command a chord would run in the given scopes, or null. */
export function findCommand(chord: string, scopes: ReadonlySet<CommandScope>): Command | null {
  for (let i = registry.length - 1; i >= 0; i--) {
    const cmd = registry[i]
    if (!scopes.has(cmd.scope)) continue
    if (!cmd.keys.includes(chord)) continue
    if (cmd.enabled && !cmd.enabled()) continue
    return cmd
  }
  return null
}

/**
 * Run the command bound to this key press, if any. Returns true when a
 * command handled it; the event's default action is then prevented.
 */
export function dispatchKey(e: KeyboardEvent, ctx: KeyContext): boolean {
  if (e.defaultPrevented) return false
  const cmd = findCommand(chordOf(e), ctx.scopes)
  if (!cmd) return false
  if (cmd.run(e) === false) return false
  e.preventDefault()
  return true
}

// Built-in commands, registered once when this module loads.
registerCommands(historyCommands)

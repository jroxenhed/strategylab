/**
 * Help commands (F435 W3 item 3.H, spec S21): `?` opens the shortcut
 * overlay (ShortcutHelp.tsx, in the `overlays` slot).
 *
 * The overlay's open state lives here, in a tiny store with no other
 * imports, so the command, the overlay and any button (the Inspector's
 * "Show all shortcuts") share it without an import cycle.
 *
 * `chordOf` reads `?` on a US keyboard as `shift+?`, so both are bound.
 * The scope is `global` (ui-ux-spec 6.2), so `?` also works in the
 * read-only view. Like every key, it does nothing while a text field has
 * focus (S21 "must not"). While the overlay is open it is a dialog and
 * handles `?` itself.
 */

import { useSyncExternalStore } from 'react'
import type { Command } from './index'

let open = false
const listeners = new Set<() => void>()

function emit() {
  for (const l of [...listeners]) l()
}

/** Show or hide the shortcut overlay. */
export function setShortcutHelpOpen(next: boolean): void {
  if (open === next) return
  open = next
  emit()
}

export function isShortcutHelpOpen(): boolean {
  return open
}

function subscribe(l: () => void): () => void {
  listeners.add(l)
  return () => { listeners.delete(l) }
}

/** True while the overlay is open; re-renders the caller when it changes. */
export function useShortcutHelpOpen(): boolean {
  return useSyncExternalStore(subscribe, isShortcutHelpOpen, isShortcutHelpOpen)
}

export const commands: Command[] = [
  {
    id: 'help.shortcuts',
    label: 'Shortcuts…',
    keys: ['?', 'shift+?'],
    scope: 'global',
    menu: 'pane',
    run() {
      setShortcutHelpOpen(!open)
    },
  },
]

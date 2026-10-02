/**
 * useGlobalKeys: the one keydown listener for `global` commands
 * (foundation 6.2: Cmd+S, Cmd+O, Cmd+Enter, Cmd+Z). NodeBuilder mounts it,
 * so the commands work in every mode (edit, the read-only view, nothing
 * loaded), not only while the canvas is editing. Canvas dispatches only its
 * own `canvas` scope.
 *
 * Rules:
 * - The press must belong to the builder (belongsToBuilder): inside it, in
 *   one of its portals, or on the page body after a press inside it.
 * - While a dialog is open nothing runs (it is modal), but Cmd+S and Cmd+O
 *   are still stopped so the browser's own Save and Open dialogs never show.
 * - In a text field only `inFields` commands run, after the field is
 *   blurred so a param row commits its typed value first. Cmd+Z stays with
 *   the field.
 */

import { useEffect, useRef, type RefObject } from 'react'
import { chordOf, commandCtx, findCommands, type CommandScope } from './index'
import { belongsToBuilder, isTypingTarget } from '../canvasHelpers'
import { isDialogOpen } from '../ui/dialogStack'

const GLOBAL_SCOPES: ReadonlySet<CommandScope> = new Set<CommandScope>(['global'])

/** Chords the browser acts on itself (Save Page, Open File). */
export const BROWSER_CHORDS: ReadonlySet<string> = new Set(['mod+s', 'mod+o'])

/** Handle one key press. Returns true when a command ran. */
export function handleGlobalKey(e: KeyboardEvent, root: Element | null, bodyActive: boolean): boolean {
  if (e.defaultPrevented || !root) return false
  const inView = root.getClientRects().length > 0
  if (!belongsToBuilder({ target: e.target, root, inView, bodyActive })) return false
  const chord = chordOf(e)
  const block = () => { if (BROWSER_CHORDS.has(chord)) e.preventDefault() }
  if (isDialogOpen()) {
    block()
    return false
  }
  const typing = isTypingTarget(e.target)
  const cmds = findCommands(chord, GLOBAL_SCOPES).filter(c => !typing || c.inFields)
  if (cmds.length === 0) {
    block()
    return false
  }
  // Commit the field first (ParamRow commits on blur), then run. The newest
  // command runs first; one that returns false hands the key to the next.
  if (typing) (e.target as HTMLElement).blur()
  const ctx = commandCtx(e)
  for (const cmd of cmds) {
    if (cmd.run(ctx) === false) continue
    e.preventDefault()
    return true
  }
  block()
  return false
}

/** Listen for global commands while the builder whose root is `rootRef` is mounted. */
export function useGlobalKeys(rootRef: RefObject<HTMLElement | null>): void {
  // Was the last pointer press inside the builder (or one of its portals)?
  // Decides whether a key on the page body is ours.
  const bodyActive = useRef(true)
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      const root = rootRef.current
      const t = e.target
      bodyActive.current =
        !!root && t instanceof Node && (root.contains(t) || (t instanceof Element && t.closest('.nodebuilder-root') != null))
    }
    const onKey = (e: KeyboardEvent) => {
      handleGlobalKey(e, rootRef.current, bodyActive.current)
    }
    document.addEventListener('pointerdown', onDown, true)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onDown, true)
      document.removeEventListener('keydown', onKey)
    }
  }, [rootRef])
}

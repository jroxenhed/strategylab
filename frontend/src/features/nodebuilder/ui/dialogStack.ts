/**
 * The open node builder dialogs (ui/Dialog.tsx), bottom first; the last one
 * is on top. Only the top dialog handles keys and holds focus. Kept apart
 * from the component so the key dispatcher can ask `isDialogOpen()`.
 */

const stack: string[] = []
// Each dialog's enclosing dialog (React tree), if any.
const parents = new Map<string, string | null>()
// Each open dialog's panel, so a closing one can hand focus to the one below.
const panels = new Map<string, HTMLElement>()

/** True while any node builder dialog is open (it is modal; keys behind it wait). */
export function isDialogOpen(): boolean {
  return stack.length > 0
}

export function isTopDialog(id: string): boolean {
  return stack[stack.length - 1] === id
}

/** The panel of the dialog on top, or null when none is open. */
export function topDialogPanel(): HTMLElement | null {
  return stack.length > 0 ? panels.get(stack[stack.length - 1]) ?? null : null
}

function isInside(id: string, ancestor: string): boolean {
  for (let p = parents.get(id) ?? null; p; p = parents.get(p) ?? null) {
    if (p === ancestor) return true
  }
  return false
}

/**
 * Add a dialog to the stack. A dialog goes below any dialog nested inside
 * it: when both open in the same render, React mounts the inner one first.
 */
export function pushDialog(id: string, parent: string | null, panel: HTMLElement | null): void {
  parents.set(id, parent)
  if (panel) panels.set(id, panel)
  const i = stack.findIndex(other => isInside(other, id))
  if (i < 0) stack.push(id)
  else stack.splice(i, 0, id)
}

export function popDialog(id: string): void {
  const i = stack.lastIndexOf(id)
  if (i >= 0) stack.splice(i, 1)
  parents.delete(id)
  panels.delete(id)
}

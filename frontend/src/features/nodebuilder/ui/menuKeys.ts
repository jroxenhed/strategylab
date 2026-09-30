/**
 * Keyboard for a `role="menu"` list of buttons: Up and Down move between
 * the enabled items (wrapping), Home and End jump. Put it on the element
 * that holds the items. Tab closing the menu is the Popover shell's job.
 */
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'

export function onMenuKeyDown(e: ReactKeyboardEvent<HTMLElement>): void {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return
  e.preventDefault()
  const buttons = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('button:not([disabled])'))
  if (buttons.length === 0) return
  const i = buttons.indexOf(document.activeElement as HTMLButtonElement)
  let next = 0
  if (e.key === 'ArrowDown') next = (i + 1) % buttons.length
  else if (e.key === 'ArrowUp') next = i < 0 ? buttons.length - 1 : (i - 1 + buttons.length) % buttons.length
  else if (e.key === 'End') next = buttons.length - 1
  buttons[next]?.focus()
}

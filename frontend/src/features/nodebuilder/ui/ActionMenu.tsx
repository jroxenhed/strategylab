/**
 * A small `⋯` action menu: a Popover with role="menu" whose rows are
 * buttons. It takes the focus on open, Up/Down/Home/End move between rows
 * (menuKeys.ts), and Esc, Tab or a press outside closes it (the Popover
 * shell); Esc closes only the menu, never a dialog under it. Choosing a row
 * puts the focus back on the `⋯` button first, so a dialog the row opens
 * returns focus there. (UX-06: the Asset Manager and promoted-row menus.)
 */
import type { ReactNode } from 'react'
import { Popover } from './Popover'
import { onMenuKeyDown } from './menuKeys'

export interface ActionMenuItem {
  /** Stable key for the row. */
  id: string
  label: ReactNode
  onSelect: () => void
  disabled?: boolean
  danger?: boolean
  testId?: string
}

export function ActionMenu({
  anchor,
  items,
  onClose,
  ariaLabel,
  width = 180,
  testId,
}: {
  anchor: HTMLElement
  items: readonly ActionMenuItem[]
  onClose: () => void
  ariaLabel: string
  width?: number
  testId?: string
}) {
  return (
    <Popover anchor={anchor} onClose={onClose} role="menu" ariaLabel={ariaLabel} align="end" width={width} autoFocus data-testid={testId}>
      <div onKeyDown={onMenuKeyDown}>
        {items.map(it => (
          <button
            key={it.id}
            type="button"
            role="menuitem"
            className={`nb-menu__item${it.danger ? ' nb-menu__item--danger' : ''}`}
            disabled={it.disabled}
            data-testid={it.testId}
            onClick={() => {
              if (anchor.isConnected) anchor.focus({ preventScroll: true })
              onClose()
              it.onSelect()
            }}
          >
            <span>{it.label}</span>
          </button>
        ))}
      </div>
    </Popover>
  )
}

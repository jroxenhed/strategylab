/**
 * ContextMenu — the right-click menu of the node canvas (item 3.G, spec S19).
 *
 * One menu, one instance, portaled to the document body through the Popover
 * shell (so it never scales with the canvas zoom). It is opened by
 * `openContextMenu` (plugins/contextMenus.ts on a right-click, the
 * `edit.contextMenu` command on Shift+F10) and drawn by `ContextMenuHost`,
 * which sits in the builder's `overlays` slot. The state and the rows live
 * in contextMenuModel.ts.
 *
 * Every row runs a registered command (commands/index.ts), so its label,
 * key cap, disabled state and checkmark come from the command and cannot
 * drift. The order of the rows is fixed here per menu (`MENU_LAYOUTS`).
 * A row names the command ids it can show (the first one that is enabled
 * wins), and optionally a key chord, so a sibling's command with another id
 * but that key and menu tag still lands in the right place. Commands tagged
 * for a menu that no row places go at the end. Rows whose command is not
 * registered (Show data in W4, the W6 network rows) do not show.
 *
 * Commands with the same `submenu` field (annotations' Tint and Color) are
 * grouped under one `Name ▸` row; rows with a `swatch` draw as squares in
 * a 4-column grid.
 *
 * Keys: Up/Down move (wrapping, disabled rows included, as in a menu bar),
 * Home/End, Right opens a submenu, Left closes it, Enter/Space run, a letter
 * jumps to the next row starting with it, Esc closes (Popover). The menu
 * closes when a row runs, on Esc, on an outside press (which does not reach
 * the canvas), on wheel or pan (the canvas closes the open popover), and on
 * window blur. Focus returns to the canvas root.
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent } from 'react'
import { Popover, type PopoverCloseReason } from './ui/Popover'
import {
  buildMenu,
  closeContextMenu,
  getContextMenu,
  runMenuItem,
  useOpenMenu,
  type MenuItemRow,
  type MenuRow,
  type MenuSubRow,
  type OpenMenu,
} from './contextMenuModel'
import { useShallow } from 'zustand/react/shallow'
import { useNodeBuilderStore, type NodeBuilderState } from './store'
import './contextMenu.css'

/**
 * Store fields no menu row depends on: status ticks (a flash, a running
 * cook's timer) and the viewport. A write that changes only these does not
 * rebuild an open menu (IP-9). Everything else counts, so a later command's
 * `when`/`checked` that reads a new field still stays live.
 */
const MENU_IGNORED: ReadonlySet<string> = new Set(['flash', 'cook', 'cooks', 'lastCookKind', 'preview', 'viewport', 'viewports', 'viewportsEpoch'])

/** The store's data fields a menu row may read (functions and MENU_IGNORED left out). */
function menuRelevantState(s: NodeBuilderState): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(s)) {
    if (typeof v === 'function' || MENU_IGNORED.has(k)) continue
    out[k] = v
  }
  return out
}

// ── Outside presses ─────────────────────────────────────────────────────────

/**
 * An outside press closes the menu and does not reach the canvas (S19): the
 * press is stopped before anything else sees it, and so is the click that
 * follows (it comes after the menu is gone, so this outlives it).
 */
function swallowNextClick(): void {
  const onClick = (e: Event) => {
    e.stopPropagation()
    e.preventDefault()
    done()
  }
  const timer = setTimeout(() => done(), 600)
  function done() {
    clearTimeout(timer)
    window.removeEventListener('click', onClick, true)
  }
  window.addEventListener('click', onClick, true)
}

// ── Components ──────────────────────────────────────────────────────────────

/** Draws the open menu. Goes in the `overlays` slot (plugins/contextMenus.ts). */
export function ContextMenuHost() {
  const open = useOpenMenu()
  if (!open) return null
  return <ContextMenuPanel key={open.seq} req={open} />
}

function ContextMenuPanel({ req }: { req: OpenMenu }) {
  // Re-read the rows when the store changes (a checkmark, a selection), but
  // not on a flash or a cook tick (IP-9): a shallow compare of the data
  // fields, so only a real change re-renders the open menu.
  const relevant = useNodeBuilderStore(useShallow(menuRelevantState))
  const editable = req.canvas.editable()
  const rows = useMemo(
    () => buildMenu(req.kind, useNodeBuilderStore.getState(), editable),
    // `relevant` is the trigger; the rows read the full state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [req.kind, relevant, editable],
  )
  const listRef = useRef<HTMLDivElement>(null)
  const [sub, setSub] = useState<number | null>(null)
  // A submenu opened from the keyboard (Right, Enter) takes focus; one
  // opened by hovering does not, so moving the mouse on never drops focus
  // to the page (UX-17).
  const [subByKey, setSubByKey] = useState(false)
  const openSubFrom = (i: number | null, byKey: boolean) => {
    setSub(i)
    setSubByKey(byKey)
  }

  const close = (reason: PopoverCloseReason | 'run') => {
    if (getContextMenu()?.seq !== req.seq) return
    closeContextMenu(req.seq)
    // A new menu (a second right-click) keeps its own focus.
    if (reason !== 'replaced') req.canvas.focus()
  }
  const closeRef = useRef(close)
  useLayoutEffect(() => { closeRef.current = close })

  // Outside press: close, and keep the press from the canvas. A right
  // press goes through, so a second right-click opens the new menu.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const t = e.target
      if (t instanceof Node && listRef.current?.closest('.nb-ctxmenu')?.contains(t)) return
      if (e.button === 2) return
      e.stopPropagation()
      e.preventDefault()
      if (e.type === 'pointerdown') swallowNextClick()
      closeRef.current('outside')
    }
    window.addEventListener('pointerdown', onDown, true)
    window.addEventListener('mousedown', onDown, true)
    return () => {
      window.removeEventListener('pointerdown', onDown, true)
      window.removeEventListener('mousedown', onDown, true)
    }
  }, [])

  // Nothing to show (e.g. a read-only graph's wire menu): close at once.
  useEffect(() => {
    if (rows.length === 0) closeRef.current('signal')
  }, [rows.length])
  if (rows.length === 0) return null

  const activate = (row: MenuRow, index: number) => {
    if (row.type === 'submenu') {
      if (!row.disabled) openSubFrom(index, true)
      return
    }
    if (row.type !== 'item' || row.disabled) return
    // Close first, so a command that opens something (the Tab menu) keeps
    // its focus.
    close('run')
    runMenuItem(row, req)
  }

  return (
    <Popover
      anchor={req.screen}
      onClose={reason => close(reason)}
      role="menu"
      ariaLabel={`${req.kind} menu`}
      autoFocus
      className="nb-ctxmenu"
      data-testid="nb-context-menu"
    >
      <MenuList
        rows={rows}
        listRef={listRef}
        onActivate={activate}
        openSub={sub}
        setOpenSub={openSubFrom}
        renderSub={(row, index) => (
          <SubMenu
            row={row}
            anchor={listRef.current?.querySelector<HTMLElement>(`[data-row="${index}"]`) ?? null}
            takeFocus={subByKey}
            onRun={item => activate(item, -1)}
            onBack={() => {
              openSubFrom(null, false)
              listRef.current?.querySelector<HTMLElement>(`[data-row="${index}"]`)?.focus()
            }}
          />
        )}
      />
    </Popover>
  )
}

interface MenuListProps {
  rows: MenuRow[]
  listRef: React.RefObject<HTMLDivElement | null>
  onActivate(row: MenuRow, index: number): void
  openSub: number | null
  /** `byKey`: opened from the keyboard, so the submenu takes focus. */
  setOpenSub(i: number | null, byKey: boolean): void
  renderSub(row: MenuSubRow, index: number): React.ReactNode
}

/**
 * Move focus between the rows: arrows (wrapping), Home/End, a letter.
 * `attr` is `row` for the main list and `subrow` for a submenu.
 */
function moveFocus(list: HTMLElement, e: ReactKeyboardEvent, attr: 'row' | 'subrow'): boolean {
  const items = Array.from(list.querySelectorAll<HTMLElement>(`[data-${attr}]`))
  if (items.length === 0) return false
  const i = items.indexOf(document.activeElement as HTMLElement)
  let next = -1
  if (e.key === 'ArrowDown') next = (i + 1) % items.length
  else if (e.key === 'ArrowUp') next = i < 0 ? items.length - 1 : (i - 1 + items.length) % items.length
  else if (e.key === 'Home') next = 0
  else if (e.key === 'End') next = items.length - 1
  else if (e.key.length === 1 && /\S/.test(e.key) && !e.metaKey && !e.ctrlKey && !e.altKey) {
    const ch = e.key.toLowerCase()
    for (let k = 1; k <= items.length; k++) {
      const cand = items[(i + k) % items.length]
      if ((cand.dataset.label ?? '').toLowerCase().startsWith(ch)) { next = (i + k) % items.length; break }
    }
    if (next < 0) return false
  } else return false
  items[next]?.focus()
  return true
}

/**
 * When focus is inside an open submenu and the mouse moves to a main row,
 * move focus to that row first: the submenu is about to unmount, and focus
 * on a removed element falls to the page body. Returns true when it moved.
 */
function focusLeftSubmenu(row: HTMLElement): boolean {
  const active = document.activeElement
  if (!(active instanceof Element) || !active.closest('.nb-ctxmenu__sub')) return false
  if (row.closest('.nb-ctxmenu__sub')) return false
  row.focus({ preventScroll: true })
  return true
}

function MenuList({ rows, listRef, onActivate, openSub, setOpenSub, renderSub }: MenuListProps) {
  // Each row's place in focus order (separators have none).
  const rowsByIndex: Array<MenuItemRow | MenuSubRow> = []
  const indexOf: number[] = []
  for (const row of rows) {
    if (row.type === 'sep') { indexOf.push(-1); continue }
    indexOf.push(rowsByIndex.length)
    rowsByIndex.push(row)
  }
  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    // Keys typed in an open submenu are its own.
    if (e.target instanceof Element && e.target.closest('.nb-ctxmenu__sub')) return
    const list = listRef.current
    if (!list) return
    const focused = document.activeElement as HTMLElement | null
    const index = focused?.dataset.row != null ? Number(focused.dataset.row) : -1
    const row = index >= 0 ? rowsByIndex[index] : null
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      if (row) onActivate(row, index)
      return
    }
    if (e.key === 'ArrowRight') {
      e.preventDefault()
      if (row?.type === 'submenu' && !row.disabled) setOpenSub(index, true)
      return
    }
    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      setOpenSub(null, false)
      return
    }
    if (moveFocus(list, e, 'row')) {
      e.preventDefault()
      setOpenSub(null, false)
    }
  }
  return (
    <div ref={listRef} className="nb-ctxmenu__list" onKeyDown={onKeyDown}>
      {rows.map((row, i) => {
        if (row.type === 'sep') return <div key={`sep-${i}`} className="nb-ctxmenu__sep" role="separator" />
        const idx = indexOf[i]
        if (row.type === 'submenu') {
          return (
            <div key={`sub-${row.label}`} className="nb-ctxmenu__subwrap">
              <button
                type="button"
                role="menuitem"
                aria-haspopup="menu"
                aria-expanded={openSub === idx}
                aria-disabled={row.disabled || undefined}
                data-row={idx}
                data-label={row.label}
                data-testid={`nb-menu-sub-${row.label}`}
                className={`nb-ctxmenu__item${row.disabled ? ' nb-ctxmenu__item--disabled' : ''}`}
                onClick={() => onActivate(row, idx)}
                onMouseEnter={e => {
                  if (row.disabled) return
                  setOpenSub(idx, false)
                  // Focus may sit in the submenu that closes: keep it in the menu.
                  focusLeftSubmenu(e.currentTarget)
                }}
              >
                <span className="nb-ctxmenu__check" />
                <span className="nb-ctxmenu__label">{row.label}</span>
                <span className="nb-ctxmenu__arrow" aria-hidden="true">▸</span>
              </button>
              {openSub === idx && renderSub(row, idx)}
            </div>
          )
        }
        return (
          <ItemButton
            key={row.cmd.id}
            row={row}
            index={idx}
            onActivate={() => onActivate(row, idx)}
            onHover={el => {
              setOpenSub(null, false)
              // The submenu that closes may hold focus: give it to this row.
              focusLeftSubmenu(el)
            }}
          />
        )
      })}
    </div>
  )
}

function ItemButton({ row, index, sub, onActivate, onHover }: {
  row: MenuItemRow
  index: number
  /** In a submenu: its rows are counted apart from the main list's. */
  sub?: boolean
  onActivate(): void
  onHover?(el: HTMLButtonElement): void
}) {
  let cls = 'nb-ctxmenu__item'
  if (row.disabled) cls += ' nb-ctxmenu__item--disabled'
  if (row.destructive) cls += ' nb-ctxmenu__item--danger'
  return (
    <button
      type="button"
      role={row.checked === null ? 'menuitem' : 'menuitemcheckbox'}
      aria-checked={row.checked === null ? undefined : row.checked}
      aria-disabled={row.disabled || undefined}
      title={row.disabled ? row.reason ?? undefined : undefined}
      data-row={sub ? undefined : index}
      data-subrow={sub ? index : undefined}
      data-label={row.label}
      data-testid={`nb-menu-item-${row.cmd.id}`}
      className={cls}
      onClick={onActivate}
      onMouseEnter={onHover ? e => onHover(e.currentTarget) : undefined}
    >
      <span className="nb-ctxmenu__check" aria-hidden="true">{row.checked ? '✓' : ''}</span>
      <span className="nb-ctxmenu__label">{row.label}</span>
      {row.keyCap && <kbd className="nb-ctxmenu__key">{row.keyCap}</kbd>}
    </button>
  )
}

/** A submenu: to the right of its row (left near the window edge); swatches as a grid. */
function SubMenu({ row, anchor, takeFocus, onRun, onBack }: {
  row: MenuSubRow
  anchor: HTMLElement | null
  /** Opened from the keyboard: focus its first row. */
  takeFocus: boolean
  onRun(item: MenuItemRow): void
  onBack(): void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<CSSProperties>({ visibility: 'hidden' })
  const swatches = row.items.every(i => i.swatch)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el || !anchor) return
    const a = anchor.getBoundingClientRect()
    const r = el.getBoundingClientRect()
    const margin = 8
    let left = a.right + 2
    if (left + r.width > window.innerWidth - margin) left = Math.max(margin, a.left - 2 - r.width)
    const top = Math.max(margin, Math.min(a.top - 4, window.innerHeight - margin - r.height))
    setPos({ left, top })
  }, [anchor])

  // Focus the first row when it opens from the keyboard. A hover-opened
  // submenu leaves focus where it is (UX-17); the mouse works it directly.
  useEffect(() => {
    if (pos.visibility === 'hidden' || !takeFocus) return
    ref.current?.querySelector<HTMLElement>('[data-subrow]')?.focus({ preventScroll: true })
  }, [pos.visibility, takeFocus])

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const el = ref.current
    if (!el) return
    const focused = document.activeElement as HTMLElement | null
    const i = focused?.dataset.subrow != null ? Number(focused.dataset.subrow) : -1
    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      e.stopPropagation()
      onBack()
      return
    }
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      e.stopPropagation()
      if (i >= 0) onRun(row.items[i])
      return
    }
    if (moveFocus(el, e, 'subrow')) {
      e.preventDefault()
      e.stopPropagation()
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      e.preventDefault()
      e.stopPropagation()
    }
  }

  return (
    <div
      ref={ref}
      role="menu"
      aria-label={row.label}
      className={`nb-ctxmenu__sub${swatches ? ' nb-ctxmenu__sub--swatches' : ''}`}
      style={pos}
      onKeyDown={onKeyDown}
    >
      {row.items.map((item, i) => swatches ? (
        <button
          key={item.cmd.id}
          type="button"
          role="menuitemcheckbox"
          aria-checked={!!item.checked}
          aria-disabled={item.disabled || undefined}
          aria-label={item.label}
          title={item.disabled ? item.reason ?? item.label : item.label}
          data-subrow={i}
          data-label={item.label}
          data-testid={`nb-menu-item-${item.cmd.id}`}
          className={`nb-ctxmenu__swatch${item.checked ? ' nb-ctxmenu__swatch--on' : ''}`}
          style={{ background: item.swatch ?? undefined }}
          onClick={() => onRun(item)}
        />
      ) : (
        <ItemButton key={item.cmd.id} row={item} index={i} sub onActivate={() => onRun(item)} />
      ))}
    </div>
  )
}

export default ContextMenuHost

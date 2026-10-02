/**
 * Output Group header (W5, spec S32a): the tab content of an Output Group
 * frame. Left to right: name, direction pill, primary ticker chip, capital
 * weight. The glyph chip, diagnostics badge and flags are drawn by the
 * frame (NetworkFrame.tsx).
 *
 * - Every edit is one `commit` (undoable): `updateNodeParams` for the
 *   direction, ticker path and weight; `commitRename` for the name.
 * - The primary ticker is `params.ticker` only (a path), never wire order
 *   or file order.
 * - The weight field refuses a negative or a non-number and stays red.
 * - In a read-only graph the header is plain text: no popovers, no fields.
 * - The direction and ticker lists open in a portal popover (ui/Popover),
 *   not inside the frame: the frame node sits under its child cards
 *   (zIndex -1), so a list drawn inside it was covered by them (FE-06).
 * - Switching a group to SWITCH stores `side: long` on its existing entry
 *   and exit (the server already reads a missing side as long), in the
 *   same commit (UX-14).
 */

import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Popover } from '../ui/Popover'
import { withGroupDirection } from '../networkOps'
import type { ParamValue } from '../../../api/nodebuilder'
import { useNodeBuilderStore } from '../store'
import { commitRename } from '../operations/rename'
import { nodePath } from '../paths'
import type { GroupTicker } from '../rfMapping'
import { totalGroupWeight, weightLabel, weightOf, weightTooltip } from '../groupResults'
import { DIRECTION_ROWS, directionLabel, pillClass, tickerChipText } from './networkFormat'

interface Props {
  nodeId: string
  name: string
  params: Record<string, unknown>
  ticker: GroupTicker | null
  editable: boolean
}

type Open = null | 'direction' | 'ticker'

export default function OutputGroupHeader({ nodeId, name, params, ticker, editable }: Props) {
  const direction = params.direction ?? 'long'
  const weight = weightOf(params)
  // A number, so the header re-renders only when the sum changes.
  const totalWeight = useNodeBuilderStore(s => totalGroupWeight(s.graph?.nodes))
  const [open, setOpen] = useState<Open>(null)
  const [editingName, setEditingName] = useState(false)
  const [editingWeight, setEditingWeight] = useState(false)
  const rootRef = useRef<HTMLSpanElement>(null)
  // The pill and chip the popovers open under. State, not refs: the
  // popover reads them while rendering.
  const [dirAnchor, setDirAnchor] = useState<HTMLButtonElement | null>(null)
  const [tickerAnchor, setTickerAnchor] = useState<HTMLButtonElement | null>(null)

  const setParam = (key: string, value: ParamValue) => {
    const s = useNodeBuilderStore.getState()
    if (!s.graph || s.graph.readOnly) return
    s.updateNodeParams(nodeId, { [key]: value })
  }

  return (
    <span ref={rootRef} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, position: 'relative' }}>
      {editingName && editable ? (
        <TextField
          label="Group name"
          initial={name}
          width={120}
          valid={v => /^[a-z_][a-z0-9_]{0,63}$/.test(v)}
          onDone={v => {
            setEditingName(false)
            if (v !== null && v !== name) commitRename(useNodeBuilderStore, nodeId, v)
          }}
        />
      ) : (
        <span
          className="nb-frame__name"
          data-testid={`nb-group-name-${nodeId}`}
          onDoubleClick={editable ? e => { e.stopPropagation(); setEditingName(true) } : undefined}
        >
          {name}
        </span>
      )}

      {editable ? (
        <button
          ref={setDirAnchor}
          type="button"
          className={`${pillClass(direction)} nodrag`}
          aria-haspopup="listbox"
          aria-expanded={open === 'direction'}
          data-testid={`nb-group-direction-${nodeId}`}
          onClick={() => setOpen(open === 'direction' ? null : 'direction')}
        >
          {directionLabel(direction)}
        </button>
      ) : (
        <span className={pillClass(direction)} data-testid={`nb-group-direction-${nodeId}`}>{directionLabel(direction)}</span>
      )}

      {editable ? (
        <button
          ref={setTickerAnchor}
          type="button"
          className={`nb-ticker-chip nodrag${ticker ? '' : ' nb-ticker-chip--missing'}`}
          aria-haspopup="listbox"
          aria-expanded={open === 'ticker'}
          data-testid={`nb-group-ticker-${nodeId}`}
          onClick={() => setOpen(open === 'ticker' ? null : 'ticker')}
        >
          {tickerChipText(ticker)}
        </button>
      ) : (
        <span className={`nb-ticker-chip${ticker ? '' : ' nb-ticker-chip--missing'}`} data-testid={`nb-group-ticker-${nodeId}`}>
          {tickerChipText(ticker)}
        </span>
      )}

      {editingWeight && editable ? (
        <TextField
          label="Capital weight"
          initial={String(weight)}
          width={40}
          className="nb-group-weight-input"
          valid={v => v.trim() !== '' && Number.isFinite(Number(v)) && Number(v) >= 0}
          onDone={v => {
            setEditingWeight(false)
            if (v !== null && Number(v) !== weight) setParam('capital_weight', Number(v))
          }}
        />
      ) : (
        <button
          type="button"
          className={`nb-group-weight nodrag${weight === 0 ? ' nb-group-weight--zero' : ''}`}
          title={weightTooltip(weight, totalWeight || weight)}
          aria-label={`Capital weight ${weightLabel(weight)}`}
          data-testid={`nb-group-weight-${nodeId}`}
          disabled={!editable}
          onClick={() => setEditingWeight(true)}
        >
          {weightLabel(weight)}
        </button>
      )}

      {open === 'direction' && (
        <GroupPopover anchor={dirAnchor} onClose={() => setOpen(null)} testId={`nb-group-direction-pop-${nodeId}`}>
        <Listbox
          label="Direction"
          rows={DIRECTION_ROWS.map(r => ({
            key: r.value,
            content: (
              <>
                <span className={pillClass(r.value)}>{directionLabel(r.value)}</span>
                <span>{r.hint}</span>
              </>
            ),
          }))}
          selected={String(direction)}
          onPick={v => {
            setOpen(null)
            if (v !== direction) setDirection(nodeId, v)
          }}
          onClose={() => setOpen(null)}
        />
        </GroupPopover>
      )}

      {open === 'ticker' && (
        <GroupPopover anchor={tickerAnchor} onClose={() => setOpen(null)} testId={`nb-group-ticker-pop-${nodeId}`}>
        <TickerPopover
          groupId={nodeId}
          current={ticker?.id ?? null}
          onPick={path => {
            setOpen(null)
            setParam('ticker', path)
          }}
          onClose={() => setOpen(null)}
        />
        </GroupPopover>
      )}
    </span>
  )
}

// ---------------------------------------------------------------------------

/** Set a group's direction in ONE commit (see networkOps.withGroupDirection). */
function setDirection(groupId: string, direction: string): void {
  const s = useNodeBuilderStore.getState()
  if (!s.graph || s.graph.readOnly) return
  s.commit('set direction', g => withGroupDirection(g, groupId, direction))
}

/** The portal popover a header list opens in (FE-06): above every card, closes on outside press, Esc, pan and zoom. */
function GroupPopover({ anchor, onClose, testId, children }: {
  anchor: HTMLElement | null
  onClose: () => void
  testId: string
  children: React.ReactNode
}) {
  return (
    <Popover anchor={anchor} onClose={onClose} role="presentation" className="nb-group-popover nodrag nowheel" data-testid={testId}>
      {children}
    </Popover>
  )
}

/** A small inline text field: Enter or blur commits, Esc cancels, invalid stays red. */
function TextField({ label, initial, width, className = 'nb-group-weight-input', valid, onDone }: {
  label: string
  initial: string
  width: number
  className?: string
  valid: (v: string) => boolean
  onDone: (value: string | null) => void
}) {
  const [value, setValue] = useState(initial)
  const ref = useRef<HTMLInputElement>(null)
  const done = useRef(false)
  useEffect(() => { ref.current?.focus(); ref.current?.select() }, [])
  const ok = valid(value)
  const finish = (v: string | null) => {
    if (done.current) return
    done.current = true
    onDone(v)
  }
  return (
    <input
      ref={ref}
      type="text"
      inputMode={label === 'Capital weight' ? 'decimal' : 'text'}
      className={`${className} nodrag`}
      aria-label={label}
      aria-invalid={!ok}
      value={value}
      style={{ width }}
      onChange={e => setValue(e.target.value)}
      onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
        e.stopPropagation()
        if (e.key === 'Enter' && ok) finish(value.trim())
        if (e.key === 'Escape') finish(null)
      }}
      onBlur={() => finish(ok ? value.trim() : null)}
    />
  )
}

/** A popover list (role listbox) with arrow keys, Enter and Esc. */
function Listbox({ label, rows, selected, onPick, onClose, footer }: {
  label: string
  rows: Array<{ key: string; content: React.ReactNode }>
  selected: string | null
  onPick: (key: string) => void
  onClose: () => void
  footer?: React.ReactNode
}) {
  const start = Math.max(0, rows.findIndex(r => r.key === selected))
  const [active, setActive] = useState(start)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    ref.current?.focus()
    // The popover is hidden until it is placed; focus again once it shows.
    const raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame(() => ref.current?.focus({ preventScroll: true })) : null
    return () => { if (raf !== null) cancelAnimationFrame(raf) }
  }, [])
  const optId = (i: number) => `nb-group-pop-${label.replace(/\W/g, '')}-${i}`
  return (
    <div
      ref={ref}
      className="nb-group-pop nodrag nowheel"
      role="listbox"
      aria-label={label}
      tabIndex={-1}
      aria-activedescendant={rows.length ? optId(active) : undefined}
      onKeyDown={e => {
        e.stopPropagation()
        if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(rows.length - 1, a + 1)) }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(0, a - 1)) }
        else if (e.key === 'Enter' && rows[active]) { e.preventDefault(); onPick(rows[active].key) }
        else if (e.key === 'Escape') { e.preventDefault(); onClose() }
      }}
    >
      <div className="nb-group-pop__title">{label}</div>
      {rows.map((r, i) => (
        <div
          key={r.key}
          id={optId(i)}
          role="option"
          aria-selected={r.key === selected}
          className={`nb-group-pop__row${i === active ? ' nb-group-pop__row--active' : ''}`}
          onPointerEnter={() => setActive(i)}
          onClick={() => onPick(r.key)}
        >
          {r.content}
        </div>
      ))}
      {footer}
    </div>
  )
}

/**
 * Every Ticker node in the graph; choosing one sets `params.ticker` to its
 * path. A Ticker already primary of another group says so. The list is read
 * from the store when the popover opens (not on every commit).
 */
function TickerPopover({ groupId, current, onPick, onClose }: {
  groupId: string
  current: string | null
  onPick: (path: string) => void
  onClose: () => void
}) {
  const [rows] = useState(() => {
    const graph = useNodeBuilderStore.getState().graph
    if (!graph) return []
    const primaryOf = new Map<string, string[]>()
    for (const n of Object.values(graph.nodes)) {
      if (n.type !== 'output_group' || n.id === groupId) continue
      const p = n.params?.ticker
      if (typeof p !== 'string') continue
      const t = Object.values(graph.nodes).find(x => x.type === 'ticker' && safePath(graph.nodes, x.id) === p)
      if (t) primaryOf.set(t.id, [...(primaryOf.get(t.id) ?? []), n.name])
    }
    return Object.values(graph.nodes)
      .filter(n => n.type === 'ticker')
      .map(n => ({
        id: n.id,
        path: safePath(graph.nodes, n.id),
        name: n.name,
        symbol: typeof n.params?.symbol === 'string' ? n.params.symbol.toUpperCase() : '',
        interval: typeof n.params?.interval === 'string' ? n.params.interval : '',
        others: primaryOf.get(n.id) ?? [],
      }))
  })
  return (
    <Listbox
      label="Primary ticker"
      selected={current}
      rows={rows.map(r => ({
        key: r.id,
        content: (
          <>
            <span>{r.name}</span>
            <span className="nb-ticker-chip">{r.interval ? `${r.symbol} · ${r.interval}` : r.symbol}</span>
            {r.others.length > 0 && <span className="nb-group-pop__hint">primary of {r.others.join(', ')}</span>}
          </>
        ),
      }))}
      onPick={id => {
        const row = rows.find(r => r.id === id)
        if (row) onPick(row.path)
      }}
      onClose={onClose}
    />
  )
}

function safePath(nodes: Parameters<typeof nodePath>[0]['nodes'], id: string): string {
  try {
    return nodePath({ nodes }, id)
  } catch {
    return id
  }
}

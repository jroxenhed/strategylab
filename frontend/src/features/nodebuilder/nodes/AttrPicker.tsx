/**
 * AttrPicker: the value cell of an `attr` or `attr_list` param (spec S09).
 *
 * Closed, it is a grey read chip (`@close ▾`). Open, a popover lists the
 * attributes on the node's input stream, grouped by the node that wrote
 * them, with a type tag. Free text is allowed: a name that is not on the
 * input yet can still be picked, and is flagged.
 *
 * Data: the node's input stream is the union of the output streams of the
 * nodes wired into it (`inputStreamOf`), from the last `/validate` answer.
 * Opening the popover never asks the server again.
 *
 * Every pick goes through the store's `updateNodeParams`, so it is one
 * undo step and triggers one validate.
 */

import { useMemo, useRef, useState } from 'react'
import type { GraphNode } from '../../../api/nodebuilder'
import type { ParamSpec } from '../catalog'
import { CATS, type CatKey } from '../categories'
import { useNodeBuilderStore } from '../store'
import { useParamDiagnostic, useStreams, useStreamsFresh } from '../useDiagnostics'
import { Popover } from '../ui/Popover'
import {
  ATTR_QUERY_RE,
  attrListValue,
  attrValue,
  catalogEntry,
  inputStreamOf,
  staticInputNames,
  withSigil,
  type InputAttr,
} from '../streamLabels'
import '../stream.css'

// ---------------------------------------------------------------------------
// Pure helpers (tested in attrPicker.test.tsx)
// ---------------------------------------------------------------------------

/** The short type tag after a name: `f` float, `b` bool, `detail` for a scalar. */
export function dtypeTag(a: Pick<InputAttr, 'dtype' | 'detail'>): string {
  if (a.detail) return 'detail'
  if (a.dtype === 'float') return 'f'
  if (a.dtype === 'bool') return 'b'
  return a.dtype
}

/**
 * True when the attribute has the other per-bar type than the param wants
 * (a bool offered to a float param, or the reverse). Such rows are dimmed
 * but can still be picked; the server's `attr_type` then explains.
 */
export function dtypeMismatch(wanted: string | undefined, a: Pick<InputAttr, 'dtype' | 'detail'>): boolean {
  if (wanted !== 'float' && wanted !== 'bool') return false
  if (a.dtype !== 'float' && a.dtype !== 'bool') return false
  return a.dtype !== wanted
}

/** One writer's attributes in the popover. */
export interface AttrGroup {
  /** The writer's node id ('' when the server did not say). */
  writer: string
  attrs: InputAttr[]
}

export interface AttrOptions {
  groups: AttrGroup[]
  /** Every shown attribute, in display order (what the arrow keys walk). */
  flat: InputAttr[]
  /** The name the free-text row offers (with the @), or null for no row. */
  free: string | null
}

/**
 * What the popover shows for a search text: attributes whose name contains
 * the text, or whose writer's name starts with it, grouped by writer in
 * stream order; plus a free-text row when the text is a valid name and no
 * attribute has exactly that name. The free row comes last, so `Enter` on a
 * partial name (`clo`) still picks the first real match (@close), while a
 * short name that only prefixes others (`@rsi` next to `@rsi_2`) can still
 * be typed and used (S09: free text is never refused).
 */
export function attrOptions(
  attrs: readonly InputAttr[],
  query: string,
  writerName: (id: string) => string = id => id,
): AttrOptions {
  const q = query.trim().toLowerCase()
  const groups: AttrGroup[] = []
  const byWriter = new Map<string, AttrGroup>()
  for (const a of attrs) {
    const writer = a.written_by ?? ''
    const hit = q === '' || a.name.toLowerCase().includes(q) || writerName(writer).toLowerCase().startsWith(q)
    if (!hit) continue
    let g = byWriter.get(writer)
    if (!g) {
      g = { writer, attrs: [] }
      byWriter.set(writer, g)
      groups.push(g)
    }
    g.attrs.push(a)
  }
  const flat = groups.flatMap(g => g.attrs)
  let free: string | null = null
  if (ATTR_QUERY_RE.test(q)) {
    const name = withSigil(q)
    if (!attrs.some(a => a.name === name)) free = name
  }
  return { groups, flat, free }
}

/** The new list after picking `name` on an `attr_list`: add it, or take it out when already there. */
export function toggleListName(list: readonly string[], name: string): string[] {
  return list.includes(name) ? list.filter(n => n !== name) : [...list, name]
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface AttrPickerProps {
  nodeId: string
  spec: Pick<ParamSpec, 'name' | 'type'> & Partial<Pick<ParamSpec, 'dtype' | 'optional' | 'label'>>
  value: unknown
  /** Read-only graph: the chip shows the value without a caret or popover. */
  disabled?: boolean
}

const NO_WIRES: never[] = []

export function AttrPicker({ nodeId, spec, value, disabled = false }: AttrPickerProps) {
  const updateNodeParams = useNodeBuilderStore(s => s.updateNodeParams)
  const wires = useNodeBuilderStore(s => s.graph?.wires) ?? NO_WIRES
  const streams = useStreams()
  const input = useMemo(() => inputStreamOf(nodeId, streams, wires), [nodeId, streams, wires])
  const names = useMemo(() => new Set(input.attrs.map(a => a.name)), [input])
  const serverDiag = useParamDiagnostic(nodeId, spec.name)
  // Between a commit and its validate the streams are for the older graph
  // (a rename upstream): the static guess at the input then counts too, so
  // a renamed read does not flash as missing. A string key keeps the store
  // selector stable; it is '' (and cheap) while the streams are fresh.
  const fresh = useStreamsFresh()
  const staticKey = useNodeBuilderStore(s =>
    fresh || !s.graph ? '' : [...staticInputNames(s.graph, nodeId)].join('\n'))
  const staticNames = useMemo(() => new Set(staticKey ? staticKey.split('\n') : []), [staticKey])
  const [open, setOpen] = useState(false)
  const anchorRef = useRef<HTMLButtonElement>(null)

  const isList = spec.type === 'attr_list'
  const list = isList ? attrListValue(value) : []
  const single = isList ? null : attrValue(value)
  const testId = `nb-attr-chip-${nodeId}-${spec.name}`

  // A name is missing when the server says so for this param, or when every
  // upstream stream is known and the name is not on it. With stale streams a
  // name the static guess finds on the input is not missing.
  const serverMissing = serverDiag?.code === 'attr_missing'
  const isMissing = (name: string) => {
    const missing = input.known ? !names.has(name) : serverMissing
    return missing && (fresh || !staticNames.has(name))
  }
  const typeProblem = serverDiag?.code === 'attr_type' ? serverDiag.message : null
  // The same name reaches this node from two writers (the server reports it
  // on the reader): an error, like missing.
  const clash = serverDiag?.code === 'attr_clash' ? serverDiag.message : null

  const pick = (name: string | null, keepOpen: boolean) => {
    if (isList) {
      if (name === null) return
      updateNodeParams(nodeId, { [spec.name]: toggleListName(list, name) })
    } else if (name !== single) {
      updateNodeParams(nodeId, { [spec.name]: name })
    }
    if (!keepOpen) setOpen(false)
  }

  const removeAt = (index: number) => {
    updateNodeParams(nodeId, { [spec.name]: list.filter((_, i) => i !== index) })
  }

  // Keep keys inside the cell: the canvas must not act on them.
  const stopKeys = (e: React.KeyboardEvent) => { if (e.key !== 'Escape') e.stopPropagation() }

  const popover = open && !disabled && (
    <AttrPickerPopover
      anchor={anchorRef.current}
      nodeId={nodeId}
      spec={spec}
      attrs={input.attrs}
      wired={input.wired}
      selected={isList ? list : single ? [single] : []}
      onPick={pick}
      onRemoveLast={isList && list.length > 0 ? () => removeAt(list.length - 1) : undefined}
      onClose={() => setOpen(false)}
    />
  )

  if (isList) {
    return (
      <span className="nb-attr-cell" onPointerDown={e => e.stopPropagation()} onKeyDown={stopKeys}>
        {list.map((name, i) => {
          const missing = isMissing(name)
          const state = missing ? ' nb-chip--missing' : clash ? ' nb-attr-chip--clash' : typeProblem ? ' nb-attr-chip--type' : ''
          return (
            <span
              key={`${name}-${i}`}
              className={`nb-chip nb-attr-chip${state}`}
              data-testid={`${testId}-${i}`}
              aria-invalid={missing || clash ? true : undefined}
              title={missing ? `${name} is not present on the input` : clash ?? typeProblem ?? undefined}
            >
              {name}
              {!disabled && (
                <button
                  type="button"
                  className="nb-attr-chip__remove"
                  aria-label={`remove ${name}`}
                  data-testid={`${testId}-remove-${i}`}
                  onClick={() => removeAt(i)}
                  style={{ background: 'none', border: 0, padding: 0 }}
                >
                  ✕
                </button>
              )}
            </span>
          )
        })}
        {!disabled && (
          <button
            ref={anchorRef}
            type="button"
            className={`nb-chip nb-attr-chip nb-attr-chip--add${open ? ' nb-attr-chip--open' : ''}`}
            data-testid={testId}
            aria-haspopup="listbox"
            aria-expanded={open}
            aria-label={`${spec.name}: add an attribute`}
            onClick={() => setOpen(o => !o)}
          >
            +
          </button>
        )}
        {list.length === 0 && disabled && <span className="nb-chip nb-attr-chip nb-attr-chip--unset">none</span>}
        {popover}
      </span>
    )
  }

  const required = !spec.optional
  const missing = single !== null && isMissing(single)
  let cls = 'nb-chip nb-attr-chip'
  let title: string | undefined
  if (single === null) cls += required ? ' nb-attr-chip--required' : ' nb-attr-chip--unset'
  else if (missing) { cls += ' nb-chip--missing'; title = `${single} is not present on the input` }
  else if (clash) { cls += ' nb-attr-chip--clash'; title = clash }
  else if (typeProblem) { cls += ' nb-attr-chip--type'; title = typeProblem }
  if (open) cls += ' nb-attr-chip--open'
  if (disabled) cls += ' nb-attr-chip--disabled'
  const text = single ?? (required ? 'pick' : 'none')

  return (
    <span className="nb-attr-cell" onPointerDown={e => e.stopPropagation()} onKeyDown={stopKeys}>
      <button
        ref={anchorRef}
        type="button"
        className={cls}
        data-testid={testId}
        disabled={disabled}
        aria-haspopup={disabled ? undefined : 'listbox'}
        aria-expanded={disabled ? undefined : open}
        aria-invalid={missing || clash ? true : undefined}
        aria-label={`${spec.name}: ${single ?? text}`}
        title={title}
        onClick={() => setOpen(o => !o)}
      >
        {text}
        {!disabled && <span className="nb-attr-chip__caret" aria-hidden="true">▾</span>}
      </button>
      {popover}
    </span>
  )
}

// ---------------------------------------------------------------------------
// The open list
// ---------------------------------------------------------------------------

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Move focus from the picker's chip to the next (or, with Shift, previous)
 * focusable control in the same node card; the chip itself when there is
 * none. Exported for tests.
 */
export function focusNextInNode(anchor: HTMLElement | null, backwards = false): void {
  if (!anchor || !anchor.isConnected) return
  const scope = anchor.closest('.react-flow__node') ?? anchor.parentElement
  const all = scope ? Array.from(scope.querySelectorAll<HTMLElement>(FOCUSABLE)) : []
  const at = all.indexOf(anchor)
  const next = at < 0 ? undefined : all[backwards ? at - 1 : at + 1]
  ;(next ?? anchor).focus({ preventScroll: true })
}

interface PopoverProps {
  anchor: HTMLElement | null
  nodeId: string
  spec: AttrPickerProps['spec']
  attrs: InputAttr[]
  wired: boolean
  selected: string[]
  onPick: (name: string | null, keepOpen: boolean) => void
  onRemoveLast?: () => void
  onClose: () => void
}

const NO_NODES: Record<string, GraphNode> = {}

function AttrPickerPopover({ anchor, nodeId, spec, attrs, wired, selected, onPick, onRemoveLast, onClose }: PopoverProps) {
  // Only the open list needs the writers' names, so only it follows node edits.
  const nodes = useNodeBuilderStore(s => s.graph?.nodes) ?? NO_NODES
  const select = useNodeBuilderStore(s => s.select)
  const [query, setQuery] = useState('')
  const isList = spec.type === 'attr_list'
  const writerName = (id: string) => nodes[id]?.name ?? (id || 'input')
  const { groups, flat, free } = attrOptions(attrs, query, writerName)
  // An optional single param can be cleared from the list.
  const showNone = !isList && spec.optional === true && selected.length > 0 && query === ''
  // Open on the current value (the row with the check mark), else the first
  // attribute row; never on `none`, so open + Enter does not clear the value.
  const [active, setActive] = useState(() => {
    const offset = showNone ? 1 : 0
    const k = !isList && selected.length > 0 ? flat.findIndex(a => a.name === selected[0]) : -1
    if (k >= 0) return k + offset
    return flat.length > 0 ? offset : -1
  })
  const items: ({ kind: 'none' } | { kind: 'attr'; attr: InputAttr } | { kind: 'free'; name: string })[] = [
    ...(showNone ? [{ kind: 'none' as const }] : []),
    ...flat.map(attr => ({ kind: 'attr' as const, attr })),
    ...(free ? [{ kind: 'free' as const, name: free }] : []),
  ]
  const activeIndex = items.length === 0 ? -1 : Math.min(active, items.length - 1)

  const choose = (index: number, keepOpen: boolean) => {
    const item = items[index]
    if (!item) return
    if (item.kind === 'none') onPick(null, false)
    else onPick(item.kind === 'attr' ? item.attr.name : item.name, keepOpen)
    if (isList) setQuery('')
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    // Esc is left to the popover shell, which closes without a change.
    if (e.key === 'Escape') return
    e.stopPropagation()
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive(Math.min(items.length - 1, activeIndex + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive(Math.max(0, activeIndex - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (activeIndex >= 0) choose(activeIndex, isList)
    } else if (e.key === 'Backspace' && query === '' && onRemoveLast) {
      e.preventDefault()
      onRemoveLast()
    } else if (e.key === 'Tab') {
      // S09: Tab closes and moves to the next param row. The search box is
      // in a portal at the end of <body>, so the browser's own Tab would
      // leave the canvas: move focus within the node instead.
      e.preventDefault()
      focusNextInNode(anchor, e.shiftKey)
      onClose()
    }
  }

  const optionId = (i: number) => `nb-attr-opt-${nodeId}-${spec.name}-${i}`
  let index = showNone ? 1 : 0

  return (
    <Popover
      anchor={anchor}
      onClose={onClose}
      role="dialog"
      ariaLabel={`Pick an attribute for ${spec.name}`}
      width={260}
      maxHeight={300}
      autoFocus
      className="nb-attr-pop"
      data-testid="nb-attr-popover"
    >
      <input
        type="text"
        className="nb-attr-pop__search"
        placeholder="@attribute or type a name"
        aria-label="Search attributes"
        aria-controls={`nb-attr-list-${nodeId}-${spec.name}`}
        aria-activedescendant={activeIndex >= 0 ? optionId(activeIndex) : undefined}
        spellCheck={false}
        autoComplete="off"
        value={query}
        onChange={e => { setQuery(e.target.value); setActive(0) }}
        onKeyDown={onKeyDown}
        data-testid="nb-attr-search"
      />
      <div className="nb-attr-pop__list" role="listbox" id={`nb-attr-list-${nodeId}-${spec.name}`} aria-multiselectable={isList || undefined}>
        {showNone && (
          <div
            id={optionId(0)}
            role="option"
            aria-selected={false}
            className={`nb-attr-pop__row${activeIndex === 0 ? ' nb-attr-pop__row--active' : ''}`}
            data-testid="nb-attr-none"
            onMouseDown={e => e.preventDefault()}
            onClick={() => choose(0, false)}
          >
            <span style={{ color: 'var(--nb-text-dim)' }}>none</span>
          </div>
        )}
        {!wired && attrs.length === 0 && (
          <div className="nb-attr-pop__empty">Nothing flows in yet. Wire an input first.</div>
        )}
        {groups.map(g => {
          const writer = g.writer ? nodes[g.writer] : undefined
          const cat = (catalogEntry(writer?.type)?.cat ?? 'settings') as CatKey
          const catVars = {
            '--cat': (CATS[cat] ?? CATS.settings).color,
            '--tint': `var(--nb-tint-${cat})`,
          } as React.CSSProperties
          return (
            <div key={g.writer || '_'} role="group" aria-label={writerName(g.writer)}>
              <div
                className="nb-attr-pop__group"
                role="presentation"
                onMouseDown={e => e.preventDefault()}
                onClick={() => { if (writer) select(writer.id) }}
              >
                <span className="nb-attr-pop__writer" style={catVars}>{writerName(g.writer)}</span>
                <span className="nb-attr-pop__count">{g.attrs.length}</span>
              </div>
              {g.attrs.map(a => {
                const i = index++
                const isOn = selected.includes(a.name)
                const dim = dtypeMismatch(spec.dtype, a)
                return (
                  <div
                    key={`${a.name}-${a.written_by ?? ''}`}
                    id={optionId(i)}
                    role="option"
                    aria-selected={isOn}
                    className={`nb-attr-pop__row${i === activeIndex ? ' nb-attr-pop__row--active' : ''}${dim ? ' nb-attr-pop__row--dim' : ''}`}
                    data-testid={`nb-attr-option-${a.name}`}
                    title={dim
                      ? `This param needs a ${spec.dtype}`
                      : `${a.name} · ${a.detail ? 'detail' : a.dtype} · written by ${writerName(g.writer)}`}
                    onMouseDown={e => e.preventDefault()}
                    onMouseEnter={() => setActive(i)}
                    onClick={() => choose(i, isList)}
                  >
                    <span>{a.name}</span>
                    <span className="nb-attr-pop__dtype">{dtypeTag(a)}</span>
                    {isOn && <span className="nb-attr-pop__check" aria-hidden="true">✓</span>}
                  </div>
                )
              })}
            </div>
          )
        })}
        {free && (
          <div
            id={optionId(index)}
            role="option"
            aria-selected={false}
            aria-description="not on the input yet"
            className={`nb-attr-pop__row${index === activeIndex ? ' nb-attr-pop__row--active' : ''}`}
            data-testid="nb-attr-free"
            onMouseDown={e => e.preventDefault()}
            onClick={() => choose(items.length - 1, isList)}
          >
            <span>Use {free}</span>
            <span className="nb-attr-pop__warn">▲ not on the input yet</span>
          </div>
        )}
      </div>
      <div className="nb-attr-pop__footer">↑↓ move · ↵ pick · esc close</div>
    </Popover>
  )
}

export default AttrPicker

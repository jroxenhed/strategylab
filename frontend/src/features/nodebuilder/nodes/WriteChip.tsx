/**
 * WriteChip: `+@rsi`, an attribute a node adds to the stream (spec S10).
 *
 * Tinted in the node's category color (`--cat` / `--tint` come from the
 * node card). Double-click, or F2 while focused, edits the name in place:
 * a fixed `+@` prefix and an input for the rest. Enter or blur commits a
 * valid name, Esc reverts. One rename is one store `commit`
 * (`renameAttr`), so the node's write param and every downstream reader
 * change together and one Cmd+Z undoes all of it.
 *
 * A fixed write (a Ticker's `@close`, `param` null) and a read-only graph
 * show the chip without editing.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useNodeBuilderStore } from '../store'
import { useNodeDiagnostics, useServerDiagnostics } from '../useDiagnostics'
import { renameAttr } from '../renameAttr'
import { ATTR_BODY_RE, writeClashFor } from '../streamLabels'
import '../stream.css'

export const WRITE_NAME_HELP = 'Use lowercase letters, digits and _; start with a letter or _'
const DRAG_HINT = 'Wires start from the ports on the top and bottom edges.'

// The drag hint shows once per page session.
let dragHintShown = false

/** Test hook: show the drag hint again. */
export function resetDragHint(): void {
  dragHintShown = false
}

export interface WriteChipProps {
  nodeId: string
  /** The `write` param that holds the name; null for a fixed write. */
  param: string | null
  /** The full name, with the @. */
  name: string
  editable?: boolean
}

export function WriteChip({ nodeId, param, name, editable = false }: WriteChipProps) {
  const commit = useNodeBuilderStore(s => s.commit)
  const diagnostics = useNodeDiagnostics(nodeId)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [hint, setHint] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  // Set once the edit is finished (Enter, Esc or blur), so a late blur does nothing.
  const revertRef = useRef(false)
  const dragRef = useRef<{ x: number; y: number } | null>(null)

  const canEdit = editable && param !== null
  const body = name.replace(/^@/, '')
  const valid = ATTR_BODY_RE.test(draft)

  // Clash (error) and shadow (warning) for this write param. The server
  // reports a clash on the node that READS the name, so a clash this write
  // takes part in is found from the readers' attr_clash (writeClashFor).
  // The selector returns a string, so it only re-renders when that changes,
  // and does no work while no clash exists anywhere.
  const server = useServerDiagnostics()
  const clashes = useMemo(() => server.filter(d => d.code === 'attr_clash'), [server])
  const derivedClash = useNodeBuilderStore(s =>
    clashes.length === 0 || !s.graph ? null : writeClashFor(s.graph, clashes, nodeId, name)?.message ?? null)
  const own = param === null ? [] : diagnostics.filter(d => d.param === param)
  const clash = own.find(d => d.code === 'attr_clash') ?? (derivedClash ? { message: derivedClash } : null)
  const shadow = own.find(d => d.code === 'attr_shadowed') ?? null

  useEffect(() => {
    if (!editing) return
    const el = inputRef.current
    if (el) {
      el.focus()
      el.select()
    }
  }, [editing])

  useEffect(() => {
    if (!hint) return
    const t = setTimeout(() => setHint(false), 3000)
    return () => clearTimeout(t)
  }, [hint])

  const startEdit = () => {
    if (!canEdit) return
    revertRef.current = false
    setDraft(body)
    setEditing(true)
  }

  const finish = (save: boolean) => {
    // A blur that arrives as the input unmounts must not finish twice.
    revertRef.current = true
    setEditing(false)
    if (!save || param === null || !ATTR_BODY_RE.test(draft) || draft === body) return
    const next = `@${draft}`
    commit(`rename ${name} to ${next}`, g => renameAttr(g, nodeId, param, next))
  }

  const testId = `nb-write-chip-${nodeId}-${param ?? body}`

  if (editing) {
    return (
      <span
        className={`nb-chip-edit nodrag nopan${valid ? '' : ' nb-chip-edit--invalid'}`}
        title={valid ? undefined : WRITE_NAME_HELP}
        onPointerDown={e => e.stopPropagation()}
      >
        <span aria-hidden="true">+@</span>
        <input
          ref={inputRef}
          type="text"
          value={draft}
          size={Math.max(4, draft.length + 1)}
          aria-label="attribute name"
          aria-invalid={valid ? undefined : true}
          data-testid="nb-write-chip-input"
          spellCheck={false}
          autoComplete="off"
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            // The canvas must not act on keys typed here (B, D, Delete...).
            e.stopPropagation()
            if (e.key === 'Enter') {
              e.preventDefault()
              if (valid) finish(true)
            } else if (e.key === 'Escape') {
              e.preventDefault()
              revertRef.current = true
              finish(false)
            }
          }}
          onBlur={() => {
            if (revertRef.current) return
            finish(valid)
          }}
        />
      </span>
    )
  }

  let cls = 'nb-chip nb-chip--write nodrag'
  if (!canEdit) cls += editable ? ' nb-chip--static' : ' nb-chip--readonly'
  if (clash) cls += ' nb-chip--clash'
  else if (shadow) cls += ' nb-chip--shadowed'
  const title = clash?.message ?? shadow?.message ?? (hint ? DRAG_HINT : undefined)

  return (
    <span style={{ position: 'relative', display: 'inline-flex' }}>
      <button
        type="button"
        className={cls}
        data-testid={testId}
        aria-label={canEdit ? `writes ${name}. Double-click to rename` : `writes ${name}`}
        aria-invalid={clash ? true : undefined}
        title={title}
        onDoubleClick={e => { e.stopPropagation(); startEdit() }}
        onKeyDown={e => {
          if (e.key === 'F2') {
            e.preventDefault()
            e.stopPropagation()
            startEdit()
          }
        }}
        // Chips are not ports: a drag on one starts nothing, and the first
        // try in a session explains where wires start.
        onPointerDown={e => { e.stopPropagation(); dragRef.current = { x: e.clientX, y: e.clientY } }}
        onPointerMove={e => {
          const start = dragRef.current
          if (!start || dragHintShown) return
          if (Math.abs(e.clientX - start.x) + Math.abs(e.clientY - start.y) > 6) {
            dragHintShown = true
            dragRef.current = null
            setHint(true)
          }
        }}
        onPointerUp={() => { dragRef.current = null }}
      >
        +{name}
      </button>
      {hint && (
        <span role="status" className="nb-chip-hint" style={{
          position: 'absolute',
          top: 20,
          left: 0,
          zIndex: 5,
          whiteSpace: 'nowrap',
          padding: '2px 6px',
          borderRadius: 4,
          background: 'var(--nb-bg-elevated)',
          border: '1px solid var(--nb-border-strong)',
          fontFamily: 'var(--nb-font-sans)',
          fontSize: 10,
          color: 'var(--nb-text-secondary)',
        }}>
          {DRAG_HINT}
        </span>
      )}
    </span>
  )
}

export default WriteChip

/**
 * ParamRow / ParamRows — inline editor for node params.
 *
 * Rendered as <BaseNode> children when `editable` is true. One labelled input
 * per param. Type inferred from `paramTypes` (catalog override) when provided,
 * otherwise from `typeof value` (number → number input, anything else → text).
 *
 * Commit semantics: blur reads `e.target.value` directly (not React state) so a
 * fast type-then-tab can't lose the value to batching. Enter blurs, ESC reverts.
 * Container has `.nodrag .nopan` + onPointerDown stopPropagation to keep React
 * Flow from grabbing the cursor mid-edit. Select inputs commit immediately
 * onChange (no blur step — there's nothing to type).
 *
 * Store sync (Cmd+Z guard, bug 7): the browser's own undo can rewrite a field
 * behind our back. So the field follows three rules:
 * - While the field is not focused it shows the store value. Input events that
 *   arrive while it is not focused are ignored, and React puts the store value
 *   back on screen.
 * - While it is focused the user's text wins, even if the store changes.
 * - On blur, typed text (including text a native undo put back) is committed.
 *   If nothing was typed, the field re-syncs to the store instead of pushing
 *   its old text over a newer store value.
 * A catalog unit (ParamTypeSpec.unit) shows after the input; a "fraction" also
 * shows its percent.
 * Numeric inputs stay type="text" inputMode="decimal" (F278). Never switch them
 * to type="number".
 */

import { useState, useEffect, useRef } from 'react'
import { useNodeBuilderStore } from '../store'
import type { ParamTypeSpec } from '../catalog'
import { unitLabel } from './paramFormat'

export function ParamRows({
  nodeId,
  params,
  paramTypes,
}: {
  nodeId: string
  params: Record<string, unknown>
  /** Optional per-key type overrides from `NodeCatalogEntry.paramTypes`. */
  paramTypes?: Record<string, ParamTypeSpec>
}) {
  return (
    <div className="nodrag nopan" style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {Object.entries(params).map(([key, value]) => (
        <ParamRow
          key={key}
          nodeId={nodeId}
          paramKey={key}
          value={value}
          typeSpec={paramTypes?.[key]}
        />
      ))}
    </div>
  )
}

const labelStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  fontFamily: 'var(--nb-font-mono)',
  fontSize: 10,
  color: 'var(--nb-text-muted)',
  lineHeight: '14px',
}

// Border is spelled out as longhands (width/style/color) so the invalid state
// can override borderColor alone. Mixing the `border` shorthand with
// `borderColor` makes React warn on re-render (bug 22).
const fieldStyle: React.CSSProperties = {
  flex: 1,
  minWidth: 0,
  background: 'var(--nb-bg-elevated)',
  borderWidth: 1,
  borderStyle: 'solid',
  borderColor: 'var(--nb-border)',
  borderRadius: 'var(--nb-radius-pill)',
  color: 'var(--nb-text)',
  fontFamily: 'var(--nb-font-mono)',
  fontSize: 10,
  padding: '2px 5px',
  outline: 'none',
}

const invalidFieldStyle: React.CSSProperties = {
  ...fieldStyle,
  borderColor: 'var(--nb-cat-rules)',
  boxShadow: '0 0 0 1px var(--nb-cat-rules)',
}

const unitStyle: React.CSSProperties = {
  flexShrink: 0,
  color: 'var(--nb-text-muted)',
}

export function ParamRow({
  nodeId,
  paramKey,
  value,
  typeSpec,
}: {
  nodeId: string
  paramKey: string
  value: unknown
  typeSpec?: ParamTypeSpec
}) {
  const updateNodeParams = useNodeBuilderStore(s => s.updateNodeParams)
  const resolvedType = typeSpec?.type ?? (typeof value === 'number' ? 'number' : 'string')
  const isNumber = resolvedType === 'number'
  const isSelect = resolvedType === 'select'
  const initial = value === null || value === undefined ? '' : String(value)
  const [draft, setDraft] = useState(initial)
  const [invalid, setInvalid] = useState<string | null>(null)
  // Refs, not state: these are read inside event handlers and the sync effect
  // and must never trigger a render on their own.
  const focusedRef = useRef(false)
  // True when the user has typed since the field last matched the store.
  const dirtyRef = useRef(false)

  // Re-sync from the store whenever the store value changes, but never while
  // the user is typing in the field.
  useEffect(() => {
    if (focusedRef.current) return
    setDraft(initial)
    setInvalid(null)
    dirtyRef.current = false
  }, [initial])

  const resync = () => {
    setDraft(initial)
    setInvalid(null)
    dirtyRef.current = false
  }

  const commit = (raw: string) => {
    if (raw === initial) { resync(); return }
    if (isNumber) {
      const trimmed = raw.trim()
      // Empty input → silent revert (F275). Distinct from "abc" which is
      // unparseable: we keep the bad value visible + red so the user can fix it.
      if (trimmed === '') {
        resync()
        return
      }
      const n = Number(trimmed)
      if (Number.isFinite(n)) {
        setInvalid(null)
        // Show the value the store will hold ("20.0" becomes "20") so the
        // field and the store agree even if the number did not change.
        setDraft(String(n))
        dirtyRef.current = false
        updateNodeParams(nodeId, { [paramKey]: n })
      } else {
        setInvalid('Must be a number')
      }
    } else {
      setInvalid(null)
      dirtyRef.current = false
      updateNodeParams(nodeId, { [paramKey]: raw })
    }
  }

  if (isSelect) {
    const options = typeSpec?.options ?? []
    // Include current value as a fallback option if it's not in the list, so a
    // strategy with a legacy/unknown value still displays correctly.
    const allOptions = options.includes(initial as never) ? options : [initial, ...options]
    // A select has no typing to protect, so it always shows the store value.
    return (
      <label style={labelStyle}>
        <span style={{ flexShrink: 0 }}>{paramKey}</span>
        <select
          value={initial}
          onChange={e => { if (e.target.value !== initial) updateNodeParams(nodeId, { [paramKey]: e.target.value }) }}
          onPointerDown={e => e.stopPropagation()}
          style={fieldStyle}
        >
          {allOptions.map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </label>
    )
  }

  const unitText = unitLabel(typeSpec?.unit, initial)

  return (
    <label style={labelStyle}>
      <span style={{ flexShrink: 0 }}>{paramKey}</span>
      <input
        type="text"
        inputMode={isNumber ? 'decimal' : 'text'}
        value={draft}
        title={invalid ?? undefined}
        onFocus={() => { focusedRef.current = true }}
        onChange={e => {
          // A native undo (Cmd+Z) can edit a field that does not have focus.
          // Ignore that: not updating the draft makes React restore the store
          // value on screen.
          if (!focusedRef.current || document.activeElement !== e.currentTarget) return
          dirtyRef.current = true
          setDraft(e.target.value)
          if (invalid) setInvalid(null)
        }}
        onBlur={e => {
          focusedRef.current = false
          if (dirtyRef.current) commit(e.target.value)
          else resync()
        }}
        onKeyDown={e => {
          if (e.key === 'Enter') {
            e.preventDefault();
            (e.target as HTMLInputElement).blur()
          } else if (e.key === 'Escape') {
            // Revert first, then blur: with dirty cleared the blur re-syncs
            // to the store instead of committing the typed text.
            resync();
            (e.target as HTMLInputElement).blur()
          }
        }}
        onPointerDown={e => e.stopPropagation()}
        style={invalid ? invalidFieldStyle : fieldStyle}
      />
      {unitText && <span data-testid="param-unit" style={unitStyle}>{unitText}</span>}
    </label>
  )
}

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
 */

import { useState, useEffect } from 'react'
import { useNodeBuilderStore } from '../store'
import type { ParamTypeSpec } from '../catalog'

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

const fieldStyle: React.CSSProperties = {
  flex: 1,
  minWidth: 0,
  background: 'var(--nb-bg-elevated)',
  border: '1px solid var(--nb-border)',
  borderRadius: 'var(--nb-radius-pill)',
  color: 'var(--nb-text)',
  fontFamily: 'var(--nb-font-mono)',
  fontSize: 10,
  padding: '2px 5px',
  outline: 'none',
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

  useEffect(() => { setDraft(initial); setInvalid(null) }, [initial])

  const commit = (raw: string) => {
    if (raw === initial) { setInvalid(null); return }
    if (isNumber) {
      const trimmed = raw.trim()
      // Empty input → silent revert (F275). Distinct from "abc" which is
      // unparseable: we keep the bad value visible + red so the user can fix it.
      if (trimmed === '') {
        setDraft(initial)
        setInvalid(null)
        return
      }
      const n = Number(trimmed)
      if (Number.isFinite(n)) {
        setInvalid(null)
        updateNodeParams(nodeId, { [paramKey]: n })
      } else {
        setInvalid('Must be a number')
      }
    } else {
      setInvalid(null)
      updateNodeParams(nodeId, { [paramKey]: raw })
    }
  }

  if (isSelect) {
    const options = typeSpec?.options ?? []
    // Include current value as a fallback option if it's not in the list, so a
    // strategy with a legacy/unknown value still displays correctly.
    const allOptions = options.includes(initial as never) ? options : [initial, ...options]
    return (
      <label style={labelStyle}>
        <span style={{ flexShrink: 0 }}>{paramKey}</span>
        <select
          value={draft}
          onChange={e => { setDraft(e.target.value); commit(e.target.value) }}
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

  const inputStyle: React.CSSProperties = invalid
    ? { ...fieldStyle, borderColor: 'var(--nb-cat-rules)', boxShadow: '0 0 0 1px var(--nb-cat-rules)' }
    : fieldStyle

  return (
    <label style={labelStyle}>
      <span style={{ flexShrink: 0 }}>{paramKey}</span>
      <input
        type={isNumber ? 'number' : 'text'}
        value={draft}
        title={invalid ?? undefined}
        onChange={e => { setDraft(e.target.value); if (invalid) setInvalid(null) }}
        onBlur={e => commit(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter') {
            e.preventDefault();
            (e.target as HTMLInputElement).blur()
          } else if (e.key === 'Escape') {
            setDraft(initial);
            setInvalid(null);
            (e.target as HTMLInputElement).blur()
          }
        }}
        onPointerDown={e => e.stopPropagation()}
        style={inputStyle}
      />
    </label>
  )
}

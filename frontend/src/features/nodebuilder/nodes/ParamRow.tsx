/**
 * ParamRow / ParamRows — inline editor for node params.
 *
 * Rendered as <BaseNode> children when `editable` is true. One labelled input
 * per param. Numeric vs text inferred from `typeof value` (typed schema is F271).
 *
 * Commit semantics: blur reads `e.target.value` directly (not React state) so a
 * fast type-then-tab can't lose the value to batching. Enter blurs, ESC reverts.
 * Container has `.nodrag .nopan` + onPointerDown stopPropagation to keep React
 * Flow from grabbing the cursor mid-edit.
 */

import { useState, useEffect } from 'react'
import { useNodeBuilderStore } from '../store'

export function ParamRows({
  nodeId,
  params,
}: {
  nodeId: string
  params: Record<string, unknown>
}) {
  return (
    <div className="nodrag nopan" style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {Object.entries(params).map(([key, value]) => (
        <ParamRow key={key} nodeId={nodeId} paramKey={key} value={value} />
      ))}
    </div>
  )
}

export function ParamRow({
  nodeId,
  paramKey,
  value,
}: {
  nodeId: string
  paramKey: string
  value: unknown
}) {
  const updateNodeParams = useNodeBuilderStore(s => s.updateNodeParams)
  const isNumber = typeof value === 'number'
  const initial = value === null || value === undefined ? '' : String(value)
  const [draft, setDraft] = useState(initial)

  useEffect(() => { setDraft(initial) }, [initial])

  const commit = (raw: string) => {
    if (raw === initial) return
    if (isNumber) {
      const n = Number(raw)
      if (Number.isFinite(n)) {
        updateNodeParams(nodeId, { [paramKey]: n })
      } else {
        setDraft(initial)
      }
    } else {
      updateNodeParams(nodeId, { [paramKey]: raw })
    }
  }

  return (
    <label style={{
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      fontFamily: 'var(--nb-font-mono)',
      fontSize: 10,
      color: 'var(--nb-text-muted)',
      lineHeight: '14px',
    }}>
      <span style={{ flexShrink: 0 }}>{paramKey}</span>
      <input
        type={isNumber ? 'number' : 'text'}
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onBlur={e => commit(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter') {
            e.preventDefault();
            (e.target as HTMLInputElement).blur()
          } else if (e.key === 'Escape') {
            setDraft(initial);
            (e.target as HTMLInputElement).blur()
          }
        }}
        onPointerDown={e => e.stopPropagation()}
        style={{
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
        }}
      />
    </label>
  )
}

/**
 * IndicatorNode — renders indicator nodes (green stripe).
 *
 * Title = display name with params, e.g. "RSI(14)", "MACD(12,26,9)".
 * Reads from catalog; writes as category-tinted pills.
 *
 * Also used as the **generic fallback** for unknown node types
 * (auto_render may emit types not in Core 14, e.g. turns_up, stochastic).
 * In that case the stripe is gray (settings color) and the title is the raw type.
 */

import { useState, useEffect } from 'react'
import type { NodeProps } from '@xyflow/react'
import { BaseNode, type BaseNodeData } from './BaseNode'
import type { CatKey } from '../categories'
import { useNodeBuilderStore } from '../store'

/** Format params as a parenthesized suffix, e.g. "(14)" or "(12,26,9)". */
function formatParams(params: Record<string, unknown>): string {
  const vals = Object.values(params).filter(v => v !== null && v !== undefined && v !== '')
  if (vals.length === 0) return ''
  return `(${vals.join(',')})`
}

/** Convert snake_case indicator name to a friendly display name. */
function friendlyName(name: string): string {
  return name
    .split('_')
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

export default function IndicatorNode({ id, data }: NodeProps) {
  const d = data as unknown as BaseNodeData
  const params = d.params ?? {}
  const catalog = d.catalog
  const backendType = d.backendType ?? ''
  const editable = d.editable === true

  // Determine category — fall back to 'settings' (neutral gray) for unknown types
  const cat = (catalog?.cat ?? 'indicator') as CatKey

  // Build title — when editable, omit param suffix (params shown as inputs below)
  const baseName = catalog ? backendType.toUpperCase() : friendlyName(backendType)
  const paramSuffix = formatParams(params)
  const title = editable
    ? baseName
    : (paramSuffix ? `${baseName}${paramSuffix}` : baseName)

  const reads = catalog?.reads ?? []
  const writes = catalog?.writes ?? []

  return (
    <BaseNode
      cat={cat}
      title={title}
      reads={reads}
      writes={writes}
      display={d.display}
      bypass={d.bypass}
      editable={editable}
    >
      {editable && Object.keys(params).length > 0 && (
        <ParamRows nodeId={id} params={params} />
      )}
    </BaseNode>
  )
}

// ---------------------------------------------------------------------------
// Param rows (inline editor) — rendered as <BaseNode> children when editable.
// One row per param. Numeric inputs commit on blur / Enter; ESC reverts.
// `.nodrag` and `.nopan` keep React Flow from hijacking pointer events.
// ---------------------------------------------------------------------------

function ParamRows({ nodeId, params }: { nodeId: string; params: Record<string, unknown> }) {
  return (
    <div className="nodrag nopan" style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {Object.entries(params).map(([key, value]) => (
        <ParamRow key={key} nodeId={nodeId} paramKey={key} value={value} />
      ))}
    </div>
  )
}

function ParamRow({
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

  // Re-sync when external param value changes (e.g. saved-graph load).
  useEffect(() => { setDraft(initial) }, [initial])

  // Read from `e.target.value` in commit so a fast type-then-blur can't miss
  // (React may not have flushed `draft` state by the time blur fires).
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

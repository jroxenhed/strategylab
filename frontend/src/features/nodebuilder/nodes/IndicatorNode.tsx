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

import type { NodeProps } from '@xyflow/react'
import { BaseNode, type BaseNodeData } from './BaseNode'
import type { CatKey } from '../categories'

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

export default function IndicatorNode({ data }: NodeProps) {
  const d = data as unknown as BaseNodeData
  const params = d.params ?? {}
  const catalog = d.catalog
  const backendType = d.backendType ?? ''

  // Determine category — fall back to 'settings' (neutral gray) for unknown types
  const cat = (catalog?.cat ?? 'indicator') as CatKey

  // Build title from backend type + params
  const baseName = catalog ? backendType.toUpperCase() : friendlyName(backendType)
  const paramSuffix = formatParams(params)
  const title = paramSuffix ? `${baseName}${paramSuffix}` : baseName

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
    />
  )
}

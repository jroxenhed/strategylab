/**
 * ComparisonNode — renders comparison nodes (amber stripe).
 *
 * Title = readable form of the comparator (e.g. "Crosses Below").
 * If params.threshold is set, renders "· 30" chip after title.
 * Reads from catalog; writes @bool.
 */

import type { NodeProps } from '@xyflow/react'
import { BaseNode, type BaseNodeData } from './BaseNode'

/** Convert snake_case condition to title-case readable label. */
function friendlyCondition(name: string): string {
  // crosses_above → Crosses Above, above → Above, etc.
  return name
    .split('_')
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

export default function ComparisonNode({ data }: NodeProps) {
  const d = data as unknown as BaseNodeData
  const params = d.params ?? {}
  const catalog = d.catalog
  const backendType = d.backendType ?? ''

  const conditionLabel = friendlyCondition(backendType)
  const threshold = params.threshold != null ? String(params.threshold) : null

  // Build title: "Crosses Above" optionally with " · 30" chip inline
  const title = threshold ? `${conditionLabel} · ${threshold}` : conditionLabel

  const reads = catalog?.reads ?? ['@close', '@close']
  const writes = catalog?.writes ?? ['@bool']

  return (
    <BaseNode
      cat="comparison"
      title={title}
      reads={reads}
      writes={writes}
      display={d.display}
      bypass={d.bypass}
    />
  )
}

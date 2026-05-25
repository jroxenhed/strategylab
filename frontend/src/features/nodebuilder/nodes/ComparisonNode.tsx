/**
 * ComparisonNode — renders comparison nodes (amber stripe).
 *
 * Title = readable form of the comparator (e.g. "Crosses Below").
 * If params.threshold is set, renders "· 30" chip after title.
 * Reads from catalog; writes @bool.
 */

import type { NodeProps } from '@xyflow/react'
import { BaseNode, type BaseNodeData } from './BaseNode'
import { ParamRows } from './ParamRow'

/** Convert snake_case condition to title-case readable label. */
function friendlyCondition(name: string): string {
  return name
    .split('_')
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

export default function ComparisonNode({ id, data }: NodeProps) {
  const d = data as unknown as BaseNodeData
  const params = d.params ?? {}
  const catalog = d.catalog
  const backendType = d.backendType ?? ''
  const editable = d.editable === true

  const conditionLabel = friendlyCondition(backendType)
  const threshold = params.threshold != null ? String(params.threshold) : null

  // In edit mode, threshold is shown as an input below; keep title clean.
  const title = (!editable && threshold) ? `${conditionLabel} · ${threshold}` : conditionLabel

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
      editable={editable}
    >
      {editable && Object.keys(params).length > 0 && (
        <ParamRows nodeId={id} params={params} paramTypes={catalog?.paramTypes} />
      )}
    </BaseNode>
  )
}

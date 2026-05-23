/**
 * LogicNode — renders logic gate nodes (orange stripe).
 *
 * Title = AND / OR / NOT (uppercase).
 * Reads @bool; writes @bool.
 */

import type { NodeProps } from '@xyflow/react'
import { BaseNode, type BaseNodeData } from './BaseNode'

export default function LogicNode({ data }: NodeProps) {
  const d = data as unknown as BaseNodeData
  const catalog = d.catalog
  const backendType = d.backendType ?? ''

  // Prefer catalog subtitle if available, otherwise uppercase the type
  const title = (catalog?.defaults.subtitle ?? backendType).toUpperCase()

  const reads = catalog?.reads ?? ['@bool']
  const writes = catalog?.writes ?? ['@bool']

  return (
    <BaseNode
      cat="logic"
      title={title}
      reads={reads}
      writes={writes}
      display={d.display}
      bypass={d.bypass}
    />
  )
}

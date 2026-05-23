/**
 * OutputNode — renders output terminal nodes (near-white stripe).
 *
 * Title = Entry / Exit / Size / Stop.
 * Reads @bool (for Entry/Exit) or @scalar (for Size/Stop).
 * No writes (terminal node).
 *
 * Icon chip must be darker to contrast against near-white stripe.
 * The BaseNode already handles this since --nb-bg is very dark.
 */

import type { NodeProps } from '@xyflow/react'
import { BaseNode, type BaseNodeData } from './BaseNode'

/** Human-readable title for output terminals. */
function titleFor(backendType: string): string {
  switch (backendType) {
    case 'entry': return 'Entry'
    case 'exit':  return 'Exit'
    case 'size':  return 'Size'
    case 'stop':  return 'Stop'
    default:      return backendType.charAt(0).toUpperCase() + backendType.slice(1)
  }
}

/** Read attr depends on terminal type. */
function readsFor(backendType: string): readonly string[] {
  switch (backendType) {
    case 'size':
    case 'stop':
      return ['@scalar']
    default:
      return ['@bool']
  }
}

export default function OutputNode({ data }: NodeProps) {
  const d = data as unknown as BaseNodeData
  const backendType = d.backendType ?? ''
  const catalog = d.catalog

  const title = titleFor(backendType)
  const reads = catalog?.reads ?? readsFor(backendType)
  // Output terminals write nothing
  const writes: readonly string[] = []

  return (
    <BaseNode
      cat="output"
      title={title}
      reads={reads}
      writes={writes}
      display={d.display}
      bypass={d.bypass}
      editable={d.editable === true}
    />
  )
}

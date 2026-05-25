/**
 * TickerNode — renders source/ticker nodes (cyan stripe).
 *
 * Title = symbol. Subtitle = "{interval} · {source}".
 * Writes: @open @high @low @close @volume.
 * In edit mode, subtitle is hidden and params render as inline inputs.
 */

import type { NodeProps } from '@xyflow/react'
import { BaseNode, type BaseNodeData } from './BaseNode'
import { ParamRows } from './ParamRow'

export default function TickerNode({ id, data }: NodeProps) {
  const d = data as unknown as BaseNodeData
  const params = d.params ?? {}
  const editable = d.editable === true

  const symbol = typeof params.symbol === 'string' ? params.symbol : (d.backendType ?? 'Ticker')
  const interval = typeof params.interval === 'string' ? params.interval : ''
  const source = typeof params.source === 'string' ? params.source : ''
  const subtitle = editable
    ? undefined
    : (interval && source ? `${interval} · ${source}` : interval || source || undefined)

  const writes = d.catalog?.writes ?? ['@open', '@high', '@low', '@close', '@volume']

  return (
    <BaseNode
      cat="ticker"
      title={symbol.toUpperCase()}
      subtitle={subtitle}
      writes={writes}
      display={d.display}
      bypass={d.bypass}
      editable={editable}
    >
      {editable && Object.keys(params).length > 0 && (
        <ParamRows nodeId={id} params={params} paramTypes={d.catalog?.paramTypes} />
      )}
    </BaseNode>
  )
}

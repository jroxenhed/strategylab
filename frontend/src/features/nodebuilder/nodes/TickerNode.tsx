/**
 * TickerNode — renders source/ticker nodes (cyan stripe).
 *
 * Title = symbol. Subtitle = "{interval}". The data source is not a node
 * param (plan D11: the sidebar and the spawn dialog own it), so a stale
 * `source` left on an older graph is not shown.
 * Writes: @open @high @low @close @volume.
 * In edit mode, subtitle is hidden and params render as inline inputs.
 * A Ticker is a data source, so it has no input handle.
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
  const subtitle = editable ? undefined : interval || undefined

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
      hasInput={false}
    >
      {editable && Object.keys(params).length > 0 && (
        <ParamRows nodeId={id} params={params} paramTypes={d.catalog?.paramTypes} />
      )}
    </BaseNode>
  )
}

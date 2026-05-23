/**
 * TickerNode — renders source/ticker nodes (cyan stripe).
 *
 * Title = symbol. Subtitle = "{interval} · {source}".
 * Writes: @open @high @low @close @volume.
 */

import type { NodeProps } from '@xyflow/react'
import { BaseNode, type BaseNodeData } from './BaseNode'

export default function TickerNode({ data }: NodeProps) {
  const d = data as unknown as BaseNodeData
  const params = d.params ?? {}

  const symbol = typeof params.symbol === 'string' ? params.symbol : (d.backendType ?? 'Ticker')
  const interval = typeof params.interval === 'string' ? params.interval : ''
  const source = typeof params.source === 'string' ? params.source : ''
  const subtitle = interval && source ? `${interval} · ${source}` : interval || source || undefined

  const writes = d.catalog?.writes ?? ['@open', '@high', '@low', '@close', '@volume']

  return (
    <BaseNode
      cat="ticker"
      title={symbol.toUpperCase()}
      subtitle={subtitle}
      writes={writes}
      display={d.display}
      bypass={d.bypass}
    />
  )
}

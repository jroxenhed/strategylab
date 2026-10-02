/**
 * TickerNode — renders source/ticker nodes (cyan stripe).
 *
 * Title = symbol. Subtitle = "{interval}". Rows (edit mode): symbol,
 * interval and prefix (S32c). The data source is not a node param (plan
 * D11, FA4: the sidebar and the spawn dialog own it), so a stale `source`
 * left on an older graph is not shown.
 * Writes: @open @high @low @close @volume, or @<prefix>_open ...
 * @<prefix>_volume when the prefix is set (a reference Ticker, S32c).
 * In edit mode, subtitle is hidden and params render as inline inputs.
 * A Ticker is a data source, so it has no input handle.
 */

import type { NodeProps } from '@xyflow/react'
import type { GraphNode } from '../../../api/nodebuilder'
import { BaseNode, type BaseNodeData } from './BaseNode'
import { ParamRows } from './ParamRow'
import { FROM_SIDEBAR, sidebarTickerValue, useSidebarWindow } from '../sidebarWindow'
import { useNodeBuilderStore } from '../store'
import { graphHasGroups, sidebarOwnsTicker } from '../ownership'
import { prefixedName, tickerPrefixOf, writesOf } from '../streamLabels'
import { isGroupPrimary } from '../graphGroups'
import { tickerRowParams } from './tickerRows'

const PLAIN_WRITES = ['@open', '@high', '@low', '@close', '@volume'] as const

export default function TickerNode({ id, data }: NodeProps) {
  const d = data as unknown as BaseNodeData
  const params = d.params ?? {}
  const editable = d.editable === true

  // D11/D7: in the app the sidebar's symbol and interval drive the implicit
  // group's primary Ticker (no prefix, no Output Group), so the card shows
  // those, not its own params (UX-01). Every other Ticker fetches its own.
  const win = useSidebarWindow()
  const hasGroups = useNodeBuilderStore(s => graphHasGroups(s.graph))
  // A boolean, so the card re-renders only when it flips.
  const isPrimary = useNodeBuilderStore(s => isGroupPrimary(s.graph, id))
  const sidebarOwned = !hasGroups && sidebarOwnsTicker(params, null)
  const sbSymbol = sidebarOwned ? sidebarTickerValue(win, 'ticker', 'symbol') : null
  const sbInterval = sidebarOwned ? sidebarTickerValue(win, 'ticker', 'interval') : null
  const symbol = sbSymbol ?? (typeof params.symbol === 'string' ? params.symbol : (d.backendType ?? 'Ticker'))
  const interval = sbInterval ?? (typeof params.interval === 'string' ? params.interval : '')
  const subtitle = editable ? undefined : (interval ? (sbInterval ? `${interval} · ${FROM_SIDEBAR}` : interval) : undefined)

  const node = { type: d.backendType ?? 'ticker', params: params as GraphNode['params'] }
  const fromCatalog = writesOf(node).map(w => w.name)
  const prefix = tickerPrefixOf(node)
  const writes = fromCatalog.length > 0 ? fromCatalog : PLAIN_WRITES.map(n => prefixedName(n, prefix))

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
      {editable && (
        <ParamRows nodeId={id} params={tickerRowParams(params, isPrimary)} paramTypes={d.catalog?.paramTypes} />
      )}
    </BaseNode>
  )
}

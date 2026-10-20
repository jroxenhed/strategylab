/**
 * TickerNode — renders source/ticker nodes (cyan stripe).
 *
 * Title = symbol. Subtitle = "{interval}". Rows (edit mode): symbol,
 * interval and prefix (S32c), all editable: the graph owns its symbol and
 * interval (F453). The data source is not a node param (plan
 * D11, FA4: the sidebar and the spawn dialog own it), so a stale `source`
 * left on an older graph is not shown.
 * Writes: @open @high @low @close @volume, or @<prefix>_open ...
 * @<prefix>_volume when the prefix is set (a reference Ticker, S32c).
 * In edit mode, subtitle is hidden and params render as inline inputs,
 * except on a primary Ticker with an empty symbol: it shows the sidebar's
 * symbol as its title, "30m · uses sidebar" as its subtitle, and no
 * interval row (runs use the sidebar's interval until a symbol is set).
 * A Ticker is a data source, so it has no input handle.
 */

import type { NodeProps } from '@xyflow/react'
import type { GraphNode } from '../../../api/nodebuilder'
import { BaseNode, type BaseNodeData } from './BaseNode'
import { ParamRows } from './ParamRow'
import { USES_SIDEBAR, useSidebarWindow } from '../sidebarWindow'
import { useNodeBuilderStore } from '../store'
import { implicitPrimaryTicker } from '../graphTicker'
import { prefixedName, tickerPrefixOf, writesOf } from '../streamLabels'
import { isGroupPrimary } from '../graphGroups'
import { tickerRowParams } from './tickerRows'

const PLAIN_WRITES = ['@open', '@high', '@low', '@close', '@volume'] as const

export default function TickerNode({ id, data }: NodeProps) {
  const d = data as unknown as BaseNodeData
  const params = d.params ?? {}
  const editable = d.editable === true

  // F453: the Ticker shows and runs its own symbol and interval. Only the
  // implicit group's primary Ticker with an empty symbol falls back to the
  // sidebar (graphTicker.ts); the card then shows the sidebar values.
  const win = useSidebarWindow()
  // Booleans, so the card re-renders only when they flip.
  const isPrimary = useNodeBuilderStore(s => isGroupPrimary(s.graph, id))
  const isImplicitPrimary = useNodeBuilderStore(s => implicitPrimaryTicker(s.graph)?.id === id)
  const ownSymbol = typeof params.symbol === 'string' ? params.symbol.trim() : ''
  const fallback = ownSymbol === '' && isImplicitPrimary && win != null ? win : null
  const symbol = ownSymbol || fallback?.ticker || (d.backendType ?? 'Ticker')
  const interval = fallback ? fallback.interval : (typeof params.interval === 'string' ? params.interval : '')
  // FL-5 / UX-3: while the card borrows the sidebar's values it says so, in
  // the edit view too, so the title (the sidebar symbol) is not mistaken for
  // the Ticker's own. Otherwise the edit view shows the rows, not a subtitle.
  const subtitle = fallback
    ? `${interval} · ${USES_SIDEBAR}`
    : (editable ? undefined : (interval || undefined))
  // While the symbol is empty, runs use the sidebar's interval, so the
  // Ticker's own interval row is hidden: it would show a value that does not
  // run. It comes back as soon as a symbol is typed (then it is used).
  const rowParams = tickerRowParams(params, isPrimary)
  if (fallback) delete rowParams.interval

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
        <ParamRows nodeId={id} params={rowParams} paramTypes={d.catalog?.paramTypes} />
      )}
    </BaseNode>
  )
}

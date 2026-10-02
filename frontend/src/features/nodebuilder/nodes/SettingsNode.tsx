/**
 * SettingsNode — renders settings nodes (neutral gray stripe).
 *
 * Title = human label (Position Size / Stop Loss / Slippage / Commission /
 * Trailing Stop), plus "(long)" / "(short)" for a per-direction setting.
 * Subtitle = actual param value (1 (100%), 5%, 2 bps, $0.00, 3% trail).
 * No handles: a setting always applies, so compile refuses any wire into or
 * out of it, and the canvas gives the user no port to draw one from.
 */

import type { NodeProps } from '@xyflow/react'
import { BaseNode, type BaseNodeData } from './BaseNode'
import { ParamRows } from './ParamRow'
import { formatPercent } from './paramFormat'

/** Format params into a readable subtitle based on the node type. */
function formatSubtitle(backendType: string, params: Record<string, unknown>): string {
  switch (backendType) {
    case 'position_size': {
      // Show the stored fraction with its percent, e.g. "1 (100%)", so the
      // chip matches what the edit field holds.
      const size = params.size != null ? Number(params.size) : 1.0
      return `${size} (${formatPercent(size)})`
    }
    case 'stop_loss': {
      const pct = params.pct != null ? Number(params.pct) : 5.0
      return `${pct}%`
    }
    case 'slippage': {
      const bps = params.bps != null ? Number(params.bps) : 2.0
      return `${bps} bps`
    }
    case 'commission': {
      const rate = params.per_share_rate != null ? Number(params.per_share_rate) : 0.0
      const min = params.min_per_order != null ? Number(params.min_per_order) : 0.0
      if (rate === 0 && min === 0) return 'free'
      return `$${rate.toFixed(4)}/sh`
    }
    default:
      return ''
  }
}

/** Human-readable title for each settings node type. */
function titleFor(backendType: string): string {
  switch (backendType) {
    case 'position_size': return 'Position Size'
    case 'stop_loss':     return 'Stop Loss'
    case 'slippage':      return 'Slippage'
    case 'commission':    return 'Commission'
    default:              return backendType
  }
}

export default function SettingsNode({ id, data }: NodeProps) {
  const d = data as unknown as BaseNodeData
  const params = d.params ?? {}
  const backendType = d.backendType ?? ''
  const editable = d.editable === true

  // A long/short strategy gives one stop per side; tell them apart.
  const direction = typeof params.direction === 'string' ? params.direction : ''
  const title = direction ? `${titleFor(backendType)} (${direction})` : titleFor(backendType)
  // In edit mode, params show as inputs below; subtitle would duplicate them.
  const subtitle = editable ? undefined : (formatSubtitle(backendType, params) || undefined)
  const writes = d.catalog?.writes ?? ['@setting']

  return (
    <BaseNode
      cat="settings"
      title={title}
      subtitle={subtitle}
      writes={writes}
      display={d.display}
      bypass={d.bypass}
      editable={editable}
      hasInput={false}
      hasOutput={false}
    >
      {editable && Object.keys(params).length > 0 && (
        <ParamRows nodeId={id} params={params} paramTypes={d.catalog?.paramTypes} />
      )}
    </BaseNode>
  )
}

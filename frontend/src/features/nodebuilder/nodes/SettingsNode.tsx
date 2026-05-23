/**
 * SettingsNode — renders settings nodes (neutral gray stripe).
 *
 * Title = human label (Position Size / Stop Loss / Slippage / Commission).
 * Subtitle = actual param value (100%, 5%, 2 bps, $0.00).
 * No read attrs; writes @setting.
 */

import type { NodeProps } from '@xyflow/react'
import { BaseNode, type BaseNodeData } from './BaseNode'

/** Format params into a readable subtitle based on the node type. */
function formatSubtitle(backendType: string, params: Record<string, unknown>): string {
  switch (backendType) {
    case 'position_size': {
      const size = params.size != null ? Number(params.size) : 1.0
      return `${Math.round(size * 100)}%`
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

export default function SettingsNode({ data }: NodeProps) {
  const d = data as unknown as BaseNodeData
  const params = d.params ?? {}
  const backendType = d.backendType ?? ''

  const title = titleFor(backendType)
  const subtitle = formatSubtitle(backendType, params)
  const writes = d.catalog?.writes ?? ['@setting']

  return (
    <BaseNode
      cat="settings"
      title={title}
      subtitle={subtitle || undefined}
      writes={writes}
      display={d.display}
      bypass={d.bypass}
      editable={d.editable === true}
    />
  )
}

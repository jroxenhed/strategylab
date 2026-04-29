import { useMemo } from 'react'
import type { BotSummary } from '../../shared/types'
import { fmtUsd, fmtPnl } from '../../shared/utils/format'
import MiniSparkline from './MiniSparkline'
import { INFO_COLUMN_FLEX, StatCell } from './ui'

interface Props {
  bots: BotSummary[]
  alignedRange?: { from: number; to: number }
}

export default function PortfolioStrip({ bots, alignedRange }: Props) {
  const stats = useMemo(() => {
    const totalPnl = bots.reduce((s, b) => s + b.total_pnl, 0)
    const totalAllocated = bots.reduce((s, b) => s + b.allocated_capital, 0)
    const pnlPct = totalAllocated > 0 ? totalPnl / totalAllocated * 100 : 0
    const runningCount = bots.filter(b => b.status === 'running').length
    const totalCount = bots.length
    const tradedCount = bots.filter(b => b.trades_count > 0).length
    const profitableCount = bots.filter(b => b.total_pnl > 0).length
    return { totalPnl, totalAllocated, pnlPct, runningCount, totalCount, profitableCount, tradedCount }
  }, [bots])

  const equityData = useMemo(() => {
    const triples: { time: string; value: number; botId: string }[] = []
    for (const bot of bots) {
      const snaps = bot.equity_snapshots
      if (!snaps || snaps.length === 0) continue
      for (const snap of snaps) {
        triples.push({ time: snap.time, value: snap.value, botId: bot.bot_id })
      }
    }
    triples.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))

    const latest = new Map<string, number>()
    const raw: { time: string; value: number }[] = []
    for (const t of triples) {
      latest.set(t.botId, t.value)
      let sum = 0
      for (const v of latest.values()) sum += v
      raw.push({ time: t.time, value: sum })
    }

    // Dedup: consecutive entries with same timestamp -> keep last
    const deduped: { time: string; value: number }[] = []
    for (let i = 0; i < raw.length; i++) {
      if (i < raw.length - 1 && raw[i].time === raw[i + 1].time) continue
      deduped.push(raw[i])
    }
    return deduped
  }, [bots])

  const pnlColor = stats.totalPnl >= 0 ? '#26a69a' : '#ef5350'

  return (
    <div style={{
      background: 'linear-gradient(135deg, rgba(88, 166, 255, 0.05), #161b22)',
      border: '1px solid #1e2530', borderRadius: 6,
      padding: '12px 8px', display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      <div style={{ display: 'flex', gap: 12 }}>
        {/* Left column — header + stats grid */}
        <div style={{ flex: INFO_COLUMN_FLEX, display: 'flex', flexDirection: 'column', gap: 8, minWidth: 120 }}>
          <span style={{ color: '#e6edf3', fontWeight: 600 }}>Portfolio</span>
          <div style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '4px 10px',
            fontSize: 12,
          }}>
            <StatCell label="Allocated" value={<span style={{ color: '#aaa' }}>{fmtUsd(stats.totalAllocated)}</span>} />
            <StatCell label="P&L" value={<span style={{ color: pnlColor }}>{fmtPnl(stats.totalPnl)} ({stats.pnlPct.toFixed(1)}%)</span>} />
            <StatCell label="Running" value={<span style={{ color: '#aaa' }}>{stats.runningCount} / {stats.totalCount}</span>} />
            <StatCell label="Profitable" value={<span style={{ color: '#aaa' }}>{stats.profitableCount} / {stats.tradedCount} bots</span>} />
          </div>
        </div>
        {/* Right column — sparkline */}
        <div style={{ flex: 1, minWidth: 120, minHeight: 90 }}>
          <MiniSparkline equityData={equityData} alignedRange={alignedRange} height={90} />
        </div>
      </div>
    </div>
  )
}

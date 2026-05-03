import { useMemo } from 'react'

interface Snapshot {
  time: string
  value: number
}

interface Props {
  snapshots: Snapshot[]
  maxDays?: number
}

function toETDate(isoUtc: string): string {
  try {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: 'America/New_York',
      year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(new Date(isoUtc))
  } catch {
    return isoUtc.slice(0, 10)
  }
}

function computeDailyPnl(snapshots: Snapshot[]): { date: string; pnl: number }[] {
  if (snapshots.length === 0) return []

  // Group by ET date, keeping last value per day
  const byDate = new Map<string, number>()
  for (const s of snapshots) {
    byDate.set(toETDate(s.time), s.value)
  }

  const sorted = [...byDate.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  return sorted.map(([date, value], i) => ({
    date,
    pnl: i === 0 ? value : value - sorted[i - 1][1],
  }))
}

const W = 240
const H = 48
const BAR_GAP = 1
const LABEL_H = 12

export default function DailyPnlChart({ snapshots, maxDays = 30 }: Props) {
  const days = useMemo(() => {
    const all = computeDailyPnl(snapshots)
    return all.slice(-maxDays)
  }, [snapshots, maxDays])

  if (days.length < 2) return null

  const maxAbs = Math.max(...days.map(d => Math.abs(d.pnl)), 0.01)
  const n = days.length
  const barW = Math.max(1, (W - BAR_GAP * (n - 1)) / n)
  const chartH = H - LABEL_H

  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ fontSize: 10, color: '#555', marginBottom: 2 }}>Daily P&L (last {n} days)</div>
      <svg width={W} height={H} style={{ display: 'block', overflow: 'visible' }}>
        {/* zero line */}
        <line
          x1={0} y1={chartH / 2}
          x2={W} y2={chartH / 2}
          stroke="#2a2f37" strokeWidth={1}
        />
        {days.map((d, i) => {
          const x = i * (barW + BAR_GAP)
          const pct = d.pnl / maxAbs
          const barH = Math.max(1, Math.abs(pct) * (chartH / 2 - 1))
          const y = d.pnl >= 0 ? chartH / 2 - barH : chartH / 2
          const color = d.pnl >= 0 ? '#26a69a' : '#ef5350'
          const label = i === 0 || i === n - 1 || (n > 7 && i % Math.ceil(n / 5) === 0)
            ? d.date.slice(5) // MM-DD
            : null
          return (
            <g key={d.date}>
              <rect
                x={x} y={y}
                width={barW} height={barH}
                fill={color} opacity={0.85}
              >
                <title>{d.date}: {d.pnl >= 0 ? '+' : ''}{d.pnl.toFixed(2)}</title>
              </rect>
              {label && (
                <text
                  x={x + barW / 2} y={H}
                  textAnchor="middle"
                  fontSize={9} fill="#555"
                >
                  {label}
                </text>
              )}
            </g>
          )
        })}
      </svg>
    </div>
  )
}

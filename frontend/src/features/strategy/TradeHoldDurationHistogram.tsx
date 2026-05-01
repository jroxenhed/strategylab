import type { Trade } from '../../shared/types'

interface Props {
  trades: Trade[]
  interval: string  // e.g. "5m", "1h", "1d", "1w"
}

interface CompletedTrade {
  duration: number  // hours for intraday, days for daily+
  isWin: boolean
}

function isIntraday(interval: string): boolean {
  return interval.endsWith('m') || interval.endsWith('h')
}

function computeDurations(trades: Trade[], intraday: boolean): CompletedTrade[] {
  const result: CompletedTrade[] = []
  let entry: Trade | null = null

  for (const trade of trades) {
    if (trade.type === 'buy' || trade.type === 'short') {
      entry = trade
    } else if ((trade.type === 'sell' || trade.type === 'cover') && entry !== null) {
      const exitDate = trade.date
      const entryDate = entry.date

      // Guard against null/undefined dates
      if (exitDate == null || entryDate == null) {
        entry = null
        continue
      }

      let duration: number
      if (intraday) {
        // dates are unix timestamps in seconds
        const exitSec = exitDate as number
        const entrySec = entryDate as number
        if (isNaN(exitSec) || isNaN(entrySec)) {
          entry = null
          continue
        }
        duration = (exitSec - entrySec) / 3600  // hours
      } else {
        // dates are "YYYY-MM-DD" strings
        const exitMs = new Date(exitDate as string).getTime()
        const entryMs = new Date(entryDate as string).getTime()
        if (isNaN(exitMs) || isNaN(entryMs)) {
          entry = null
          continue
        }
        duration = (exitMs - entryMs) / 86400000  // days
      }

      const isWin = (trade.pnl ?? 0) >= 0
      result.push({ duration, isWin })
      entry = null
    }
  }

  return result
}

function fmtDuration(v: number, unit: string): string {
  if (v < 10) return `${v.toFixed(1)}${unit}`
  return `${Math.round(v)}${unit}`
}

function median(arr: number[]): number {
  if (arr.length === 0) return 0
  const sorted = [...arr].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid]
}

function avg(arr: number[]): number {
  if (arr.length === 0) return NaN
  return arr.reduce((s, v) => s + v, 0) / arr.length
}

const W = 560
const H = 120
const PAD_LEFT = 8
const PAD_RIGHT = 8
const LABEL_H = 14
const CHART_W = W - PAD_LEFT - PAD_RIGHT
const CHART_H = H - LABEL_H

export default function TradeHoldDurationHistogram({ trades, interval }: Props) {
  const intraday = isIntraday(interval)
  const unit = intraday ? 'hrs' : 'days'

  const completed = computeDurations(trades, intraday)

  if (completed.length < 2) {
    return (
      <div style={{ color: '#484f58', fontSize: 12, padding: '16px 0' }}>
        Not enough completed trades to show hold duration distribution
      </div>
    )
  }

  const durations = completed.map(t => t.duration)

  if (durations.every(d => d === 0)) {
    return (
      <div style={{ color: '#484f58', fontSize: 12, padding: '16px 0' }}>
        All trades entered and exited on the same bar — no duration data
      </div>
    )
  }

  const minDur = Math.min(...durations)
  const maxDur = Math.max(...durations)
  const range = maxDur - minDur || 1

  const bucketCount = Math.min(20, Math.max(5, Math.floor(Math.sqrt(durations.length))))
  const bucketSize = range / bucketCount

  // For each bucket, count wins and losses
  const bucketWins = new Array(bucketCount).fill(0)
  const bucketLosses = new Array(bucketCount).fill(0)

  for (const { duration, isWin } of completed) {
    const idx = Math.min(bucketCount - 1, Math.floor((duration - minDur) / bucketSize))
    if (isWin) bucketWins[idx]++
    else bucketLosses[idx]++
  }

  const bucketTotals = bucketWins.map((w, i) => w + bucketLosses[i])
  const tallest = Math.max(...bucketTotals) || 1
  const barWidth = CHART_W / bucketCount

  const medianAll = median(durations)
  const winDurations = completed.filter(t => t.isWin).map(t => t.duration)
  const lossDurations = completed.filter(t => !t.isWin).map(t => t.duration)
  const avgWin = avg(winDurations)
  const avgLoss = avg(lossDurations)

  return (
    <div>
      <div style={{ marginBottom: 6 }}>
        <span style={{ color: '#e6edf3', fontSize: 13, fontWeight: 600 }}>Hold Duration Distribution</span>
        <span style={{ color: '#8b949e', fontSize: 11, marginLeft: 8 }}>({unit} per trade)</span>
      </div>
      <svg
        width="100%"
        viewBox={`0 0 ${W} ${H}`}
        style={{ display: 'block', overflow: 'visible' }}
        preserveAspectRatio="xMidYMid meet"
      >
        {bucketTotals.map((count, i) => {
          const wins = bucketWins[i]
          const losses = bucketLosses[i]
          const color = wins > losses ? '#26a641' : losses > wins ? '#f85149' : '#8b949e'
          const h = (count / tallest) * (CHART_H - 4)
          return (
            <rect
              key={i}
              x={PAD_LEFT + i * barWidth + 0.5}
              y={CHART_H - h}
              width={Math.max(1, barWidth - 1)}
              height={h}
              fill={color}
              opacity={0.85}
            />
          )
        })}
        {/* x-axis baseline */}
        <line
          x1={PAD_LEFT}
          y1={CHART_H}
          x2={W - PAD_RIGHT}
          y2={CHART_H}
          stroke="#30363d"
          strokeWidth={1}
        />
        {/* x-axis labels */}
        <text x={PAD_LEFT} y={H - 2} fontSize={10} fill="#8b949e" textAnchor="start">
          {fmtDuration(minDur, unit)}
        </text>
        <text x={W - PAD_RIGHT} y={H - 2} fontSize={10} fill="#8b949e" textAnchor="end">
          {fmtDuration(maxDur, unit)}
        </text>
      </svg>
      {/* Summary stats below chart */}
      <div style={{ display: 'flex', gap: 20, marginTop: 6, fontSize: 11, color: '#8b949e', flexWrap: 'wrap' }}>
        <span>
          Median: <span style={{ color: '#e6edf3' }}>{fmtDuration(medianAll, unit)}</span>
        </span>
        {!isNaN(avgWin) && (
          <span>
            Avg win hold: <span style={{ color: '#26a641' }}>{fmtDuration(avgWin, unit)}</span>
          </span>
        )}
        {!isNaN(avgLoss) && (
          <span>
            Avg loss hold: <span style={{ color: '#f85149' }}>{fmtDuration(avgLoss, unit)}</span>
          </span>
        )}
        <span style={{ color: '#484f58' }}>
          {completed.length} completed trade{completed.length !== 1 ? 's' : ''}
        </span>
      </div>
    </div>
  )
}

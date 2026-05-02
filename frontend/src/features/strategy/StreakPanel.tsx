import type { Trade } from '../../shared/types'
import { computeStreakStats } from './streakUtils'

interface Props {
  trades: Trade[]
}

// Mini SVG bar chart: 120px wide × 36px tall, no axis labels
// Buckets by streak length (1..maxStreak), up to 8 buckets; group if more
function MiniStreakChart({ streaks, color }: { streaks: number[]; color: string }) {
  if (streaks.length < 2) return null

  const maxStreak = Math.max(...streaks)
  const numBuckets = Math.max(2, Math.min(8, maxStreak))

  // Build buckets: each bucket covers a range of streak lengths
  const bucketSize = maxStreak / numBuckets
  const counts = new Array(numBuckets).fill(0)
  for (const s of streaks) {
    const idx = Math.min(numBuckets - 1, Math.floor((s - 1) / bucketSize))
    counts[idx]++
  }

  const maxCount = Math.max(...counts) || 1
  const W = 120
  const H = 36
  const barW = W / numBuckets

  return (
    <svg
      width={W}
      height={H}
      style={{ display: 'block' }}
    >
      {counts.map((count, i) => {
        const barH = (count / maxCount) * (H - 2)
        return (
          <rect
            key={i}
            x={i * barW + 0.5}
            y={H - barH - 1}
            width={Math.max(1, barW - 1.5)}
            height={barH}
            fill={color}
            opacity={0.85}
          />
        )
      })}
      <line x1={0} y1={H - 1} x2={W} y2={H - 1} stroke="#30363d" strokeWidth={1} />
    </svg>
  )
}

export default function StreakPanel({ trades }: Props) {
  const sells = trades.filter(t => t.type === 'sell' || t.type === 'cover')
  if (sells.length === 0) return null

  const stats = computeStreakStats(trades)
  const showWinChart = stats.winStreaks.length >= 2
  const showLossChart = stats.lossStreaks.length >= 2
  const showCharts = showWinChart || showLossChart

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      padding: '12px 16px',
      borderTop: '1px solid #21262d',
      gap: 10,
    }}>
      {/* Section label */}
      <div>
        <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>
          Win / Loss Streaks
        </span>
      </div>

      {/* Max streak numbers row */}
      <div style={{ display: 'flex', gap: 32 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ fontSize: 9, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>Max Consec. Wins</span>
          <span style={{ fontSize: 18, fontWeight: 700, color: '#26a641' }}>{stats.maxConsecWins}</span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ fontSize: 9, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>Max Consec. Losses</span>
          <span style={{ fontSize: 18, fontWeight: 700, color: '#f85149' }}>{stats.maxConsecLosses}</span>
        </div>
      </div>

      {/* Avg streak row */}
      <div style={{ display: 'flex', gap: 32, fontSize: 11 }}>
        <span>
          <span style={{ color: '#8b949e' }}>Avg win streak: </span>
          <span style={{ color: '#26a641', fontWeight: 600 }}>
            {stats.avgWinStreak === 0 ? '—' : stats.avgWinStreak.toFixed(1)}
          </span>
        </span>
        <span>
          <span style={{ color: '#8b949e' }}>Avg loss streak: </span>
          <span style={{ color: '#f85149', fontWeight: 600 }}>
            {stats.avgLossStreak === 0 ? '—' : stats.avgLossStreak.toFixed(1)}
          </span>
        </span>
      </div>

      {/* Mini distribution charts */}
      {showCharts && (
        <div style={{ display: 'flex', gap: 24, alignItems: 'flex-end' }}>
          {showWinChart && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={{ fontSize: 9, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                Win streak dist.
              </span>
              <MiniStreakChart streaks={stats.winStreaks} color="#26a641" />
            </div>
          )}
          {showLossChart && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <span style={{ fontSize: 9, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                Loss streak dist.
              </span>
              <MiniStreakChart streaks={stats.lossStreaks} color="#f85149" />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

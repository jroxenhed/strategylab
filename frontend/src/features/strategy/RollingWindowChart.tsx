import { useState, useMemo } from 'react'
import type { Trade } from '../../shared/types'

interface Props {
  trades: Trade[]
}

interface RollingPoint {
  index: number   // exit trade index
  winRate: number // 0-100
  avgPnl: number
  sharpe: number | null
}

function computeRolling(trades: Trade[], window: number): RollingPoint[] {
  const exits = trades.filter(t => (t.type === 'sell' || t.type === 'cover') && t.pnl != null)
  const results: RollingPoint[] = []
  for (let i = window - 1; i < exits.length; i++) {
    const slice = exits.slice(i - window + 1, i + 1)
    const pnls = slice.map(t => t.pnl!)
    const wins = pnls.filter(p => p > 0).length
    const mean = pnls.reduce((a, b) => a + b, 0) / pnls.length
    const variance = pnls.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / pnls.length
    const std = Math.sqrt(variance)
    results.push({
      index: i,
      winRate: (wins / window) * 100,
      avgPnl: mean,
      sharpe: std > 0 ? (mean / std) * Math.sqrt(window) : null,
    })
  }
  return results
}

function MiniChart({
  data, label, color, formatY, refLine,
}: {
  data: { x: number; y: number | null }[]
  label: string
  color: string
  formatY: (v: number) => string
  refLine?: number
}) {
  const W = 560, H = 80
  const pad = { top: 10, right: 12, bottom: 18, left: 52 }
  const plotW = W - pad.left - pad.right
  const plotH = H - pad.top - pad.bottom

  const valid = data.filter(d => d.y != null) as { x: number; y: number }[]
  if (valid.length < 2) return null

  const minY = Math.min(...valid.map(d => d.y), refLine ?? Infinity)
  const maxY = Math.max(...valid.map(d => d.y), refLine ?? -Infinity)
  const rangeY = maxY - minY || 1
  const minX = data[0].x, maxX = data[data.length - 1].x
  const rangeX = maxX - minX || 1

  const xS = (x: number) => pad.left + ((x - minX) / rangeX) * plotW
  const yS = (y: number) => pad.top + (1 - (y - minY) / rangeY) * plotH

  const pathD = valid.map((d, i) => `${i === 0 ? 'M' : 'L'}${xS(d.x).toFixed(1)},${yS(d.y).toFixed(1)}`).join(' ')

  return (
    <div style={{ marginBottom: 4 }}>
      <div style={{ fontSize: 10, color: '#8b949e', marginBottom: 2 }}>{label}</div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: 'block', maxWidth: W }}>
        {/* Y ticks */}
        {[minY, maxY].map((v, i) => (
          <g key={i}>
            <text x={pad.left - 4} y={yS(v) + 3} textAnchor="end" fontSize={8} fill="#8b949e">
              {formatY(v)}
            </text>
          </g>
        ))}
        {/* Ref line */}
        {refLine != null && refLine >= minY && refLine <= maxY && (
          <line x1={pad.left} y1={yS(refLine)} x2={pad.left + plotW} y2={yS(refLine)}
            stroke="#30363d" strokeWidth={1} strokeDasharray="3,2" />
        )}
        <path d={pathD} fill="none" stroke={color} strokeWidth={1.5} />
        {/* Axes */}
        <line x1={pad.left} y1={pad.top} x2={pad.left} y2={pad.top + plotH} stroke="#30363d" />
        <line x1={pad.left} y1={pad.top + plotH} x2={pad.left + plotW} y2={pad.top + plotH} stroke="#30363d" />
      </svg>
    </div>
  )
}

export default function RollingWindowChart({ trades }: Props) {
  const [windowSize, setWindowSize] = useState(20)
  const exits = trades.filter(t => (t.type === 'sell' || t.type === 'cover') && t.pnl != null)

  const rolling = useMemo(() => computeRolling(trades, windowSize), [trades, windowSize])

  if (exits.length < 5) {
    return <div style={{ color: '#8b949e', fontSize: 12, padding: 16 }}>Need at least 5 completed trades</div>
  }

  const winRateData = rolling.map(d => ({ x: d.index, y: d.winRate }))
  const avgPnlData = rolling.map(d => ({ x: d.index, y: d.avgPnl }))
  const sharpeData = rolling.map(d => ({ x: d.index, y: d.sharpe }))

  const fmtPct = (v: number) => `${v.toFixed(0)}%`
  const fmtDollar = (v: number) => {
    if (Math.abs(v) >= 1000) return `$${(v / 1000).toFixed(1)}k`
    return `$${v.toFixed(0)}`
  }
  const fmtSharpe = (v: number) => v.toFixed(2)

  return (
    <div style={{ padding: '16px 0' }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
        <span style={{ fontSize: 11, color: '#8b949e' }}>Window</span>
        {[5, 10, 20, 50].map(w => (
          <button
            key={w}
            onClick={() => setWindowSize(w)}
            disabled={exits.length < w}
            style={{
              padding: '3px 8px',
              fontSize: 11,
              fontWeight: 600,
              color: windowSize === w ? '#58a6ff' : exits.length < w ? '#484f58' : '#8b949e',
              background: windowSize === w ? 'rgba(88,166,255,0.1)' : 'none',
              border: 'none',
              borderRadius: 3,
              cursor: exits.length < w ? 'default' : 'pointer',
            }}
          >
            {w} trades
          </button>
        ))}
        <span style={{ fontSize: 10, color: '#484f58', marginLeft: 8 }}>
          ({rolling.length} windows)
        </span>
      </div>

      {rolling.length < 2 ? (
        <div style={{ color: '#8b949e', fontSize: 12 }}>Not enough trades for selected window size</div>
      ) : (
        <>
          <MiniChart data={winRateData} label="Win Rate" color="#58a6ff" formatY={fmtPct} refLine={50} />
          <MiniChart data={avgPnlData} label="Avg PnL per Trade" color="#26a641" formatY={fmtDollar} refLine={0} />
          <MiniChart data={sharpeData} label="Sharpe Ratio (rolling)" color="#e5c07b" formatY={fmtSharpe} refLine={1} />
        </>
      )}
    </div>
  )
}

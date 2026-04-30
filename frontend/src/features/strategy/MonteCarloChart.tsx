import type { MonteCarloResult } from '../../shared/types'

interface Props {
  mcResult: MonteCarloResult
  initialCapital: number
}

function fmtDollar(v: number): string {
  if (Math.abs(v) >= 1000000) return `$${(v / 1000000).toFixed(2)}M`
  if (Math.abs(v) >= 1000) return `$${(v / 1000).toFixed(1)}k`
  return `$${v.toFixed(0)}`
}

function fmtPct(v: number): string {
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`
}

export default function MonteCarloChart({ mcResult, initialCapital }: Props) {
  const { curves, num_simulations, num_trades, final_value, max_drawdown_pct, ruin_probability } = mcResult

  // SVG dimensions
  const W = 560, H = 200
  const pad = { top: 16, right: 16, bottom: 28, left: 64 }
  const plotW = W - pad.left - pad.right
  const plotH = H - pad.top - pad.bottom

  // Compute value extent from p5 to p95
  const allVals = [...curves.p5, ...curves.p95]
  const minV = Math.min(...allVals)
  const maxV = Math.max(...allVals)
  const range = maxV - minV || 1

  const xScale = (i: number) => pad.left + (i / Math.max(num_trades, 1)) * plotW
  const yScale = (v: number) => pad.top + (1 - (v - minV) / range) * plotH

  // Generate SVG path from array of equity values
  function toPath(vals: number[]): string {
    return vals.map((v, i) => `${i === 0 ? 'M' : 'L'}${xScale(i).toFixed(1)},${yScale(v).toFixed(1)}`).join(' ')
  }

  // Generate SVG area polygon between two curves
  function toArea(upper: number[], lower: number[]): string {
    const fwd = upper.map((v, i) => `${i === 0 ? 'M' : 'L'}${xScale(i).toFixed(1)},${yScale(v).toFixed(1)}`)
    const bwd = [...lower].reverse().map((v, i) => `L${xScale(lower.length - 1 - i).toFixed(1)},${yScale(v).toFixed(1)}`)
    return [...fwd, ...bwd, 'Z'].join(' ')
  }

  // Y-axis tick labels (3 ticks)
  const yTicks = [minV, (minV + maxV) / 2, maxV]

  // Pct return helpers
  const pctOf = (v: number) => ((v - initialCapital) / initialCapital * 100)

  const ruinColor = ruin_probability === 0 ? '#26a641' : ruin_probability < 5 ? '#e5c07b' : '#f85149'

  return (
    <div style={{ padding: '16px 0' }}>
      {/* Stats row */}
      <div style={{ display: 'flex', gap: 32, marginBottom: 12, flexWrap: 'wrap' }}>
        {([
          ['5th pct (worst)', final_value.p5, '#f85149'],
          ['25th pct', final_value.p25, '#e5c07b'],
          ['Median (50th)', final_value.p50, '#8b949e'],
          ['75th pct', final_value.p75, '#26a641'],
          ['95th pct (best)', final_value.p95, '#39d353'],
        ] as [string, number, string][]).map(([label, val, color]) => (
          <div key={label}>
            <div style={{ fontSize: 10, color: '#8b949e', marginBottom: 2 }}>{label}</div>
            <div style={{ fontSize: 14, fontWeight: 700, color }}>
              {fmtDollar(val)}
            </div>
            <div style={{ fontSize: 10, color: '#8b949e' }}>{fmtPct(pctOf(val))}</div>
          </div>
        ))}
        <div style={{ marginLeft: 'auto' }}>
          <div style={{ fontSize: 10, color: '#8b949e', marginBottom: 2 }}>Ruin probability</div>
          <div style={{ fontSize: 20, fontWeight: 700, color: ruinColor }}>{ruin_probability.toFixed(1)}%</div>
          <div style={{ fontSize: 10, color: '#8b949e' }}>{num_simulations.toLocaleString()} simulations</div>
        </div>
      </div>

      {/* SVG percentile band chart */}
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: 'block', maxWidth: W }}>
        {/* Y-axis ticks */}
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={pad.left - 4} y1={yScale(v)} x2={pad.left} y2={yScale(v)} stroke="#30363d" />
            <text x={pad.left - 6} y={yScale(v) + 4} textAnchor="end" fontSize={9} fill="#8b949e">
              {fmtDollar(v)}
            </text>
          </g>
        ))}
        {/* X-axis label */}
        <text x={pad.left + plotW / 2} y={H - 2} textAnchor="middle" fontSize={9} fill="#8b949e">
          Trade #
        </text>

        {/* P5–P95 outer band (very faint) */}
        <path d={toArea(curves.p95, curves.p5)} fill="rgba(88,166,255,0.08)" />
        {/* P25–P75 inner band */}
        <path d={toArea(curves.p75, curves.p25)} fill="rgba(88,166,255,0.18)" />
        {/* P50 median line */}
        <path d={toPath(curves.p50)} fill="none" stroke="#58a6ff" strokeWidth={1.5} />
        {/* Initial capital line */}
        <line
          x1={pad.left} y1={yScale(initialCapital)}
          x2={pad.left + plotW} y2={yScale(initialCapital)}
          stroke="#30363d" strokeWidth={1} strokeDasharray="4,3"
        />

        {/* Axes */}
        <line x1={pad.left} y1={pad.top} x2={pad.left} y2={pad.top + plotH} stroke="#30363d" />
        <line x1={pad.left} y1={pad.top + plotH} x2={pad.left + plotW} y2={pad.top + plotH} stroke="#30363d" />
      </svg>

      {/* Max drawdown percentiles */}
      <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: 11, color: '#8b949e', marginBottom: 6, fontWeight: 600 }}>Max Drawdown Distribution</div>
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
          {([
            ['Best 5%', max_drawdown_pct.p5, '#26a641'],
            ['25th pct', max_drawdown_pct.p25, '#8b949e'],
            ['Median', max_drawdown_pct.p50, '#8b949e'],
            ['75th pct', max_drawdown_pct.p75, '#e5c07b'],
            ['Worst 5%', max_drawdown_pct.p95, '#f85149'],
          ] as [string, number, string][]).map(([label, val, color]) => (
            <div key={label}>
              <span style={{ fontSize: 10, color: '#8b949e', marginRight: 4 }}>{label}</span>
              <span style={{ fontSize: 12, fontWeight: 700, color }}>{val.toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

import type { BacktestResult } from '../../shared/types'

interface Props {
  summary: BacktestResult['summary']
}

interface KellyResult {
  winRate: number
  avgWin: number
  avgLoss: number
  winLossRatio: number
  fullKelly: number
  halfKelly: number
  quarterKelly: number
  hasEdge: boolean
}

function computeKelly(summary: BacktestResult['summary']): KellyResult | null {
  const { win_rate_pct, gain_stats, loss_stats, num_trades } = summary
  if (num_trades < 5) return null
  if (!gain_stats || !loss_stats) return null

  const w = win_rate_pct / 100
  const avgWin = Math.abs(gain_stats.mean ?? 0)
  const avgLoss = Math.abs(loss_stats.mean ?? 1)

  if (avgWin === 0 || avgLoss === 0) return null

  const R = avgWin / avgLoss
  // Kelly formula: f* = W - (1-W)/R
  const fullKelly = w - (1 - w) / R

  return {
    winRate: win_rate_pct,
    avgWin,
    avgLoss,
    winLossRatio: R,
    fullKelly: Math.min(fullKelly, 1),
    halfKelly: Math.min(fullKelly / 2, 1),
    quarterKelly: Math.min(fullKelly / 4, 1),
    hasEdge: fullKelly > 0,
  }
}

function fmtPct(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

const styles = {
  container: {
    padding: '12px 16px 4px',
    borderTop: '1px solid #21262d',
  } as React.CSSProperties,
  title: {
    fontSize: 11,
    fontWeight: 600,
    color: '#8b949e',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.05em',
    marginBottom: 10,
  } as React.CSSProperties,
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, 1fr)',
    gap: '8px 16px',
    marginBottom: 10,
  } as React.CSSProperties,
  stat: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 2,
  } as React.CSSProperties,
  label: {
    fontSize: 10,
    color: '#8b949e',
  } as React.CSSProperties,
  value: {
    fontSize: 13,
    fontWeight: 600,
  } as React.CSSProperties,
  noEdge: {
    fontSize: 12,
    color: '#f85149',
    fontStyle: 'italic' as const,
    marginBottom: 8,
  } as React.CSSProperties,
  hint: {
    fontSize: 10,
    color: '#8b949e',
    marginTop: 4,
    paddingBottom: 8,
  } as React.CSSProperties,
}

export default function KellySizing({ summary }: Props) {
  const k = computeKelly(summary)
  if (!k) return null

  return (
    <div style={styles.container}>
      <div style={styles.title}>Kelly Position Sizing</div>
      {!k.hasEdge ? (
        <>
          <div style={styles.noEdge}>No statistical edge — Kelly suggests 0% position size</div>
          <div style={styles.grid}>
            <div style={styles.stat}>
              <span style={styles.label}>Win Rate</span>
              <span style={{ ...styles.value, color: '#e6edf3' }}>{k.winRate.toFixed(1)}%</span>
            </div>
            <div style={styles.stat}>
              <span style={styles.label}>Win/Loss Ratio</span>
              <span style={{ ...styles.value, color: '#e6edf3' }}>{k.winLossRatio.toFixed(2)}×</span>
            </div>
            <div style={styles.stat}>
              <span style={styles.label}>Full Kelly</span>
              <span style={{ ...styles.value, color: '#f85149' }}>{fmtPct(k.fullKelly)}</span>
            </div>
          </div>
        </>
      ) : (
        <>
          <div style={styles.grid}>
            <div style={styles.stat}>
              <span style={styles.label}>Win Rate</span>
              <span style={{ ...styles.value, color: '#e6edf3' }}>{k.winRate.toFixed(1)}%</span>
            </div>
            <div style={styles.stat}>
              <span style={styles.label}>Win/Loss Ratio</span>
              <span style={{ ...styles.value, color: '#e6edf3' }}>{k.winLossRatio.toFixed(2)}×</span>
            </div>
            <div style={styles.stat}>
              <span style={styles.label}>Avg Win</span>
              <span style={{ ...styles.value, color: '#3fb950' }}>${k.avgWin.toFixed(2)}</span>
            </div>
            <div style={styles.stat}>
              <span style={styles.label}>Full Kelly</span>
              <span style={{ ...styles.value, color: '#d29922' }}>{fmtPct(k.fullKelly)}</span>
            </div>
            <div style={styles.stat}>
              <span style={styles.label}>½ Kelly</span>
              <span style={{ ...styles.value, color: '#3fb950' }}>{fmtPct(k.halfKelly)}</span>
            </div>
            <div style={styles.stat}>
              <span style={styles.label}>¼ Kelly</span>
              <span style={{ ...styles.value, color: '#3fb950' }}>{fmtPct(k.quarterKelly)}</span>
            </div>
          </div>
          <div style={styles.hint}>
            ½ Kelly is typically recommended — full Kelly maximises long-run growth but with extreme drawdowns.
          </div>
        </>
      )}
    </div>
  )
}

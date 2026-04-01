import { useEffect, useRef } from 'react'
import { createChart, LineSeries, ColorType } from 'lightweight-charts'
import type { BacktestResult } from '../types'

interface Props {
  result: BacktestResult
}

export default function Results({ result }: Props) {
  const { summary, trades, equity_curve } = result
  const chartRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!chartRef.current || equity_curve.length === 0) return
    const chart = createChart(chartRef.current, {
      height: 120,
      layout: { background: { type: ColorType.Solid, color: '#0d1117' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#1c2128' }, horzLines: { color: '#1c2128' } },
      timeScale: { borderColor: '#30363d' },
      rightPriceScale: { borderColor: '#30363d' },
    })
    const series = chart.addSeries(LineSeries, {
      color: summary.total_return_pct >= 0 ? '#26a641' : '#f85149',
      lineWidth: 2,
    })
    series.setData(equity_curve.filter(d => d.value !== null).map(d => ({ time: d.time as any, value: d.value as number })))
    chart.timeScale().fitContent()
    const ro = new ResizeObserver(() => { if (chartRef.current) chart.applyOptions({ width: chartRef.current.clientWidth }) })
    ro.observe(chartRef.current)
    return () => { chart.remove(); ro.disconnect() }
  }, [equity_curve])

  const sells = trades.filter(t => t.type === 'sell')

  return (
    <div style={styles.container}>
      {/* Metrics */}
      <div style={styles.metrics}>
        {[
          { label: 'Return', value: `${summary.total_return_pct > 0 ? '+' : ''}${summary.total_return_pct}%`, color: summary.total_return_pct >= 0 ? '#26a641' : '#f85149' },
          { label: 'B&H Return', value: `${summary.buy_hold_return_pct > 0 ? '+' : ''}${summary.buy_hold_return_pct}%`, color: '#8b949e' },
          { label: 'Final Value', value: `$${summary.final_value.toLocaleString()}`, color: '#e6edf3' },
          { label: 'Trades', value: summary.num_trades, color: '#e6edf3' },
          { label: 'Win Rate', value: `${summary.win_rate_pct}%`, color: summary.win_rate_pct >= 50 ? '#26a641' : '#f85149' },
          { label: 'Sharpe', value: summary.sharpe_ratio, color: summary.sharpe_ratio >= 1 ? '#26a641' : '#8b949e' },
          { label: 'Max DD', value: `${summary.max_drawdown_pct}%`, color: '#f85149' },
        ].map(({ label, value, color }) => (
          <div key={label} style={styles.metric}>
            <div style={{ fontSize: 10, color: '#8b949e', marginBottom: 2 }}>{label}</div>
            <div style={{ fontSize: 14, fontWeight: 700, color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Equity curve */}
      <div style={{ flex: '0 0 120px', borderLeft: '1px solid #21262d', padding: '4px 8px' }}>
        <div style={{ fontSize: 10, color: '#8b949e', marginBottom: 2 }}>Equity Curve</div>
        <div ref={chartRef} style={{ width: '100%', height: 100 }} />
      </div>

      {/* Trade list */}
      <div style={styles.tradeList}>
        <div style={{ fontSize: 10, color: '#8b949e', marginBottom: 4 }}>Trades ({sells.length})</div>
        <div style={{ overflowY: 'auto', maxHeight: 100 }}>
          {sells.map((t, i) => (
            <div key={i} style={styles.tradeRow}>
              <span style={{ color: '#8b949e', fontSize: 11, width: 80 }}>{t.date}</span>
              <span style={{ color: (t.pnl ?? 0) >= 0 ? '#26a641' : '#f85149', fontSize: 12, width: 60 }}>
                {(t.pnl ?? 0) >= 0 ? '+' : ''}{t.pnl?.toFixed(2)}
              </span>
              <span style={{ color: '#8b949e', fontSize: 11 }}>{t.pnl_pct?.toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: 'flex', background: '#161b22', borderTop: '1px solid #30363d', height: 140, overflow: 'hidden' },
  metrics: { display: 'flex', alignItems: 'center', gap: 0, padding: '8px 12px', flexShrink: 0 },
  metric: { padding: '0 12px', borderRight: '1px solid #21262d', textAlign: 'center' },
  tradeList: { flex: 1, padding: '8px 12px', borderLeft: '1px solid #21262d', overflowY: 'auto' },
  tradeRow: { display: 'flex', gap: 8, alignItems: 'center', padding: '2px 0', borderBottom: '1px solid #21262d' },
}

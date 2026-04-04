import { useState, useEffect, useRef } from 'react'
import { createChart, LineSeries, ColorType } from 'lightweight-charts'
import type { BacktestResult } from '../../shared/types'

type Tab = 'summary' | 'equity' | 'trades'

interface Props {
  result: BacktestResult
}

export default function Results({ result }: Props) {
  const { summary, trades, equity_curve } = result
  const [activeTab, setActiveTab] = useState<Tab>('summary')
  const chartRef = useRef<HTMLDivElement>(null)
  const sells = trades.filter(t => t.type === 'sell')

  useEffect(() => {
    if (activeTab !== 'equity' || !chartRef.current || equity_curve.length === 0) return
    const chart = createChart(chartRef.current, {
      height: chartRef.current.clientHeight || 185,
      layout: { background: { type: ColorType.Solid, color: '#0d1117' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#1c2128' }, horzLines: { color: '#1c2128' } },
      timeScale: { borderColor: '#30363d' },
      rightPriceScale: { borderColor: '#30363d' },
    })
    const series = chart.addSeries(LineSeries, {
      color: summary.total_return_pct >= 0 ? '#26a641' : '#f85149',
      lineWidth: 2,
    })
    series.setData(
      equity_curve
        .filter(d => d.value !== null)
        .map(d => ({ time: d.time as any, value: d.value as number }))
    )
    chart.timeScale().fitContent()
    const ro = new ResizeObserver(() => {
      if (chartRef.current) chart.applyOptions({ width: chartRef.current.clientWidth })
    })
    ro.observe(chartRef.current)
    return () => { chart.remove(); ro.disconnect() }
  }, [activeTab, equity_curve, summary.total_return_pct])

  return (
    <div style={styles.container}>
      <div style={styles.tabBar}>
        {(['summary', 'equity', 'trades'] as Tab[]).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{ ...styles.tab, ...(activeTab === tab ? styles.tabActive : {}) }}
          >
            {tab === 'summary' ? 'Summary' : tab === 'equity' ? 'Equity Curve' : `Trades (${sells.length})`}
          </button>
        ))}
      </div>

      {activeTab === 'summary' && (
        <div style={styles.metricsGrid}>
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
              <div style={{ fontSize: 16, fontWeight: 700, color }}>{value}</div>
            </div>
          ))}
        </div>
      )}

      {activeTab === 'equity' && (
        <div ref={chartRef} style={{ flex: 1, width: '100%' }} />
      )}

      {activeTab === 'trades' && (
        <div style={styles.tradeList}>
          {sells.length === 0 ? (
            <div style={{ color: '#8b949e', fontSize: 12, padding: 8 }}>No completed trades</div>
          ) : (
            sells.map((t, i) => (
              <div key={i} style={styles.tradeRow}>
                <span style={{ color: '#8b949e', fontSize: 11, width: 80 }}>{t.date}</span>
                <span style={{ color: (t.pnl ?? 0) >= 0 ? '#26a641' : '#f85149', fontSize: 12, width: 60 }}>
                  {(t.pnl ?? 0) >= 0 ? '+' : ''}{t.pnl?.toFixed(2)}
                </span>
                <span style={{ color: '#8b949e', fontSize: 11 }}>{t.pnl_pct?.toFixed(1)}%</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex', flexDirection: 'column',
    background: '#161b22', borderTop: '1px solid #30363d',
    height: 220, flexShrink: 0,
  },
  tabBar: { display: 'flex', borderBottom: '1px solid #30363d', flexShrink: 0 },
  tab: {
    padding: '6px 14px', fontSize: 12, color: '#8b949e',
    background: 'none', border: 'none', borderBottom: '2px solid transparent', cursor: 'pointer',
  },
  tabActive: { color: '#58a6ff', borderBottomColor: '#58a6ff' },
  metricsGrid: { display: 'flex', flexWrap: 'wrap', padding: '12px 16px', gap: 0, alignContent: 'flex-start' },
  metric: { padding: '6px 20px 6px 0', minWidth: 110 },
  tradeList: { flex: 1, overflowY: 'auto', padding: '8px 12px' },
  tradeRow: { display: 'flex', gap: 8, alignItems: 'center', padding: '3px 0', borderBottom: '1px solid #21262d' },
}

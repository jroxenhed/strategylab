import { useState, useEffect, useRef } from 'react'
import { createChart, BaselineSeries, LineSeries, ColorType } from 'lightweight-charts'
import type { IChartApi } from 'lightweight-charts'
import type { BacktestResult, SignalTraceEntry } from '../../shared/types'
import { fmtDateTimeET } from '../../shared/utils/time'
import PnlHistogram from './PnlHistogram'

type Tab = 'summary' | 'equity' | 'trades' | 'trace'

function fmtDate(d: string | number | undefined): string {
  if (typeof d === 'number') return fmtDateTimeET(d)
  return d ?? '—'
}

interface Props {
  result: BacktestResult
  mainChart?: IChartApi | null
}

export default function Results({ result, mainChart }: Props) {
  const { summary, trades, equity_curve, signal_trace } = result
  const [activeTab, setActiveTab] = useState<Tab>('summary')
  const [showBaseline, setShowBaseline] = useState(false)
  const [avgMode, setAvgMode] = useState<'mean' | 'median'>('mean')
  const chartRef = useRef<HTMLDivElement>(null)
  const sells = trades.filter(t => t.type === 'sell' || t.type === 'cover')

  useEffect(() => {
    if (activeTab !== 'equity' || !chartRef.current || equity_curve.length === 0) return
    const chart = createChart(chartRef.current, {
      height: chartRef.current.clientHeight || 185,
      layout: { background: { type: ColorType.Solid, color: '#0d1117' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#1c2128' }, horzLines: { color: '#1c2128' } },
      timeScale: { borderColor: '#30363d' },
      rightPriceScale: { borderColor: '#30363d' },
    })
    const initialCapital = equity_curve.length > 0 && equity_curve[0].value !== null ? equity_curve[0].value : 10000
    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: initialCapital as number },
      topLineColor: '#26a641',
      bottomLineColor: '#f85149',
      topFillColor1: 'rgba(38, 166, 65, 0.1)',
      topFillColor2: 'rgba(38, 166, 65, 0)',
      bottomFillColor1: 'rgba(248, 81, 73, 0)',
      bottomFillColor2: 'rgba(248, 81, 73, 0.1)',
      lineWidth: 2,
    })
    series.setData(
      equity_curve
        .filter(d => d.value !== null)
        .map(d => ({ time: d.time as any, value: d.value as number }))
    )

    if (showBaseline && result.baseline_curve && result.baseline_curve.length > 0) {
      const baselineSeries = chart.addSeries(LineSeries, {
        color: '#8b949e',
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      })
      baselineSeries.setData(
        result.baseline_curve
          .filter(d => d.value !== null)
          .map(d => ({ time: d.time as any, value: d.value as number }))
      )
    }

    // Initial alignment: match main chart's visible range, or fit content as fallback
    if (mainChart) {
      const range = mainChart.timeScale().getVisibleLogicalRange()
      if (range) {
        chart.timeScale().setVisibleLogicalRange(range)
      } else {
        chart.timeScale().fitContent()
      }
    } else {
      chart.timeScale().fitContent()
    }

    // Bidirectional scroll/zoom sync with main chart
    let syncing = false
    const onMainRangeChange = (range: any) => {
      if (syncing || !range) return
      syncing = true
      chart.timeScale().setVisibleLogicalRange(range)
      syncing = false
    }
    const onEquityRangeChange = (range: any) => {
      if (syncing || !range || !mainChart) return
      syncing = true
      mainChart.timeScale().setVisibleLogicalRange(range)
      syncing = false
    }
    // One-way crosshair sync: main → equity
    const onMainCrosshairMove = (param: any) => {
      if (param.time) {
        chart.setCrosshairPosition(NaN, param.time, series)
      } else {
        chart.clearCrosshairPosition()
      }
    }

    if (mainChart) {
      mainChart.timeScale().subscribeVisibleLogicalRangeChange(onMainRangeChange)
      chart.timeScale().subscribeVisibleLogicalRangeChange(onEquityRangeChange)
      mainChart.subscribeCrosshairMove(onMainCrosshairMove)
    }

    const ro = new ResizeObserver(() => {
      if (chartRef.current) chart.applyOptions({ width: chartRef.current.clientWidth, height: chartRef.current.clientHeight })
    })
    ro.observe(chartRef.current)
    return () => {
      if (mainChart) {
        mainChart.timeScale().unsubscribeVisibleLogicalRangeChange(onMainRangeChange)
        mainChart.unsubscribeCrosshairMove(onMainCrosshairMove)
      }
      chart.remove()
      ro.disconnect()
    }
  }, [activeTab, equity_curve, summary.total_return_pct, mainChart, showBaseline, result.baseline_curve])

  return (
    <div style={styles.container}>
      <div style={styles.tabBar}>
        {(['summary', 'equity', 'trades', ...(signal_trace ? ['trace'] : [])] as Tab[]).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{ ...styles.tab, ...(activeTab === tab ? styles.tabActive : {}) }}
          >
            {tab === 'summary' ? 'Summary' : tab === 'equity' ? 'Equity Curve' : tab === 'trades' ? `Trades (${sells.length})` : `Signal Trace (${signal_trace!.length})`}
          </button>
        ))}
      </div>

      {activeTab === 'summary' && (
        <div style={{ display: 'flex', flexDirection: 'column' }}>
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
          {summary.num_trades > 0 && (summary.gain_stats || summary.loss_stats) && (
            <div style={{ display: 'flex', flexDirection: 'column', padding: '12px 16px', borderTop: '1px solid #21262d' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>P&amp;L Distribution</span>
                <div style={{ display: 'flex', gap: 2, background: '#0d1117', border: '1px solid #21262d', borderRadius: 3, padding: 1 }}>
                  {(['mean', 'median'] as const).map(m => (
                    <button
                      key={m}
                      onClick={() => setAvgMode(m)}
                      style={{
                        fontSize: 9, padding: '1px 6px', border: 'none', cursor: 'pointer', borderRadius: 2,
                        background: avgMode === m ? '#1e3a5f' : 'transparent',
                        color: avgMode === m ? '#e6edf3' : '#8b949e',
                      }}
                    >{m}</button>
                  ))}
                </div>
              </div>

              <EvPfHeader
                evPerTrade={summary.ev_per_trade ?? null}
                profitFactor={summary.profit_factor ?? null}
                grossProfit={summary.gross_profit ?? 0}
              />

              {/* Waterfall component added in Task 6 */}

              <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start', marginTop: 12 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 180 }}>
                  <StatRow label="Max gain" value={summary.gain_stats?.max} color="#26a641" />
                  <StatRow label={`Avg gain (${avgMode})`} value={summary.gain_stats?.[avgMode]} color="#26a641" />
                  <StatRow label="Min gain" value={summary.gain_stats?.min} color="#26a641" />
                  <StatRow label="Max loss" value={summary.loss_stats?.min} color="#f85149" />
                  <StatRow label={`Avg loss (${avgMode})`} value={summary.loss_stats?.[avgMode]} color="#f85149" />
                  <StatRow label="Min loss" value={summary.loss_stats?.max} color="#f85149" />
                </div>
                <PnlHistogram values={summary.pnl_distribution ?? []} />
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === 'equity' && (
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', fontSize: 11, color: '#8b949e', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={showBaseline}
              onChange={e => setShowBaseline(e.target.checked)}
            />
            Show buy &amp; hold baseline
          </label>
          <div ref={chartRef} style={{ width: '100%', height: 200, minHeight: 100, maxHeight: 600, resize: 'vertical', overflow: 'hidden' }} />
        </div>
      )}

      {activeTab === 'trades' && (
        <div style={styles.tradeList}>
          {sells.length === 0 ? (
            <div style={{ color: '#8b949e', fontSize: 12, padding: 8 }}>No completed trades</div>
          ) : (<>
            <div style={{ ...styles.tradeRow, borderBottom: '1px solid #30363d', marginBottom: 2 }}>
              <span style={{ ...styles.tradeCell, width: 24, color: '#8b949e', fontSize: 10 }}>#</span>
              <span style={{ ...styles.tradeCell, width: 115, color: '#8b949e', fontSize: 10 }}>Buy</span>
              <span style={{ ...styles.tradeCell, width: 65, color: '#8b949e', fontSize: 10 }}>Buy $</span>
              <span style={{ ...styles.tradeCell, width: 115, color: '#8b949e', fontSize: 10 }}>Sell</span>
              <span style={{ ...styles.tradeCell, width: 65, color: '#8b949e', fontSize: 10 }}>Sell $</span>
              <span style={{ ...styles.tradeCell, width: 45, color: '#8b949e', fontSize: 10 }}>Shares</span>
              <span style={{ ...styles.tradeCell, width: 60, color: '#8b949e', fontSize: 10 }}>P&L</span>
              <span style={{ ...styles.tradeCell, width: 50, color: '#8b949e', fontSize: 10 }}>Return</span>
              <span style={{ ...styles.tradeCell, width: 50, color: '#8b949e', fontSize: 10 }}>Slip</span>
              <span style={{ ...styles.tradeCell, width: 50, color: '#8b949e', fontSize: 10 }}>Comm</span>
              <span style={{ ...styles.tradeCell, width: 40, color: '#8b949e', fontSize: 10 }}>Exit</span>
            </div>
            {sells.map((sell, i) => {
              const buy = trades.filter(t => t.type === 'buy' || t.type === 'short')[i]
              const win = (sell.pnl ?? 0) >= 0
              const color = win ? '#26a641' : '#f85149'
              const totalSlip = (buy?.slippage ?? 0) + (sell.slippage ?? 0)
              const totalComm = (buy?.commission ?? 0) + (sell.commission ?? 0)
              return (
                <div key={i} style={styles.tradeRow}>
                  <span style={{ ...styles.tradeCell, width: 24, color: '#8b949e' }}>{i + 1}</span>
                  <span style={{ ...styles.tradeCell, width: 115, color: '#e5c07b' }}>{fmtDate(buy?.date)}</span>
                  <span style={{ ...styles.tradeCell, width: 65, color: '#e5c07b' }}>${buy?.price.toFixed(2)}</span>
                  <span style={{ ...styles.tradeCell, width: 115, color }}>{fmtDate(sell.date)}</span>
                  <span style={{ ...styles.tradeCell, width: 65, color }}>${sell.price.toFixed(2)}</span>
                  <span style={{ ...styles.tradeCell, width: 45, color: '#8b949e' }}>{sell.shares?.toFixed(1)}</span>
                  <span style={{ ...styles.tradeCell, width: 60, color }}>
                    {win ? '+' : ''}{sell.pnl?.toFixed(2)}
                  </span>
                  <span style={{ ...styles.tradeCell, width: 50, color }}>
                    {win ? '+' : ''}{sell.pnl_pct?.toFixed(2)}%
                  </span>
                  <span style={{ ...styles.tradeCell, width: 50, color: totalSlip > 0 ? '#f0883e' : '#484f58' }}>
                    {totalSlip > 0 ? `$${totalSlip.toFixed(2)}` : '—'}
                  </span>
                  <span style={{ ...styles.tradeCell, width: 50, color: totalComm > 0 ? '#f0883e' : '#484f58' }}>
                    {totalComm > 0 ? `$${totalComm.toFixed(2)}` : '—'}
                  </span>
                  <span style={{ ...styles.tradeCell, width: 40, color: sell.stop_loss ? '#f0883e' : sell.trailing_stop ? '#f0883e' : '#8b949e', fontSize: 10 }}>
                    {sell.stop_loss ? 'SL' : sell.trailing_stop ? 'TSL' : 'Signal'}
                  </span>
                </div>
              )
            })}
          </>)}
        </div>
      )}

      {activeTab === 'trace' && signal_trace && (
        <div style={styles.tradeList}>
          {signal_trace.length === 0 ? (
            <div style={{ color: '#8b949e', fontSize: 12, padding: 8 }}>No signal events recorded</div>
          ) : (<>
            <div style={{ ...styles.tradeRow, borderBottom: '1px solid #30363d', marginBottom: 2 }}>
              <span style={{ ...styles.traceCell, width: 140, color: '#8b949e', fontSize: 10 }}>Date</span>
              <span style={{ ...styles.traceCell, width: 65, color: '#8b949e', fontSize: 10 }}>Price</span>
              <span style={{ ...styles.traceCell, width: 65, color: '#8b949e', fontSize: 10 }}>Position</span>
              <span style={{ ...styles.traceCell, width: 140, color: '#8b949e', fontSize: 10 }}>Action</span>
              <span style={{ ...styles.traceCell, flex: 1, color: '#8b949e', fontSize: 10 }}>Rule Details</span>
            </div>
            {signal_trace.map((entry: SignalTraceEntry, i: number) => {
              const actionColor = entry.action === 'BUY' ? '#26a641'
                : entry.action === 'SELL' ? '#f85149'
                : entry.action === 'STOP_LOSS' ? '#f0883e'
                : entry.action.startsWith('MISSED') ? '#e5c07b'
                : '#8b949e'
              const rules = entry.sell_rules ?? entry.buy_rules ?? []
              return (
                <div key={i} style={styles.tradeRow}>
                  <span style={{ ...styles.traceCell, width: 140, color: '#e6edf3' }}>{fmtDate(entry.date)}</span>
                  <span style={{ ...styles.traceCell, width: 65, color: '#e6edf3' }}>${entry.price.toFixed(2)}</span>
                  <span style={{ ...styles.traceCell, width: 65, color: '#8b949e' }}>{entry.position}</span>
                  <span style={{ ...styles.traceCell, width: 140, color: actionColor, fontWeight: 600 }}>{entry.action}</span>
                  <span style={{ ...styles.traceCell, flex: 1, color: '#8b949e' }}>
                    {rules.map((r, j) => (
                      <span key={j} style={{ marginRight: 10, color: r.muted ? '#484f58' : r.result ? '#26a641' : '#f85149' }}>
                        {r.muted ? '🔇 ' : r.result ? '✓ ' : '✗ '}
                        {r.rule.trim()}
                        {!r.muted && r.v_now != null ? ` [${r.v_prev}→${r.v_now}]` : ''}
                      </span>
                    ))}
                  </span>
                </div>
              )
            })}
          </>)}
        </div>
      )}
    </div>
  )
}

function StatRow({ label, value, color }: { label: string; value: number | null | undefined; color: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
      <span style={{ color: '#8b949e' }}>{label}</span>
      <span style={{ color: value == null ? '#484f58' : color, fontFamily: 'monospace' }}>
        {value == null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}`}
      </span>
    </div>
  )
}

function EvPfHeader({
  evPerTrade,
  profitFactor,
  grossProfit,
}: {
  evPerTrade: number | null
  profitFactor: number | null
  grossProfit: number
}) {
  const evColor = evPerTrade == null ? '#8b949e' : evPerTrade > 0 ? '#26a641' : '#f85149'
  const evText =
    evPerTrade == null
      ? '—'
      : `${evPerTrade >= 0 ? '+' : ''}$${evPerTrade.toFixed(2)} / trade`

  let pfColor: string
  let pfText: string
  if (profitFactor == null) {
    if (grossProfit > 0) {
      pfColor = '#26a641'
      pfText = '∞'
    } else {
      pfColor = '#8b949e'
      pfText = '—'
    }
  } else {
    pfColor = profitFactor > 1 ? '#26a641' : '#f85149'
    pfText = profitFactor.toFixed(2)
  }

  const suffix = <span style={{ fontSize: 10, color: '#8b949e', marginLeft: 4 }}>(mean)</span>

  return (
    <div style={{ display: 'flex', gap: 32, alignItems: 'baseline', marginBottom: 8 }}>
      <div>
        <span style={{ fontSize: 10, color: '#8b949e', marginRight: 6 }}>EV</span>
        <span style={{ fontSize: 16, fontWeight: 700, color: evColor }}>{evText}</span>
        {suffix}
      </div>
      <div>
        <span style={{ fontSize: 10, color: '#8b949e', marginRight: 6 }}>PF</span>
        <span style={{ fontSize: 16, fontWeight: 700, color: pfColor }}>{pfText}</span>
        {suffix}
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex', flexDirection: 'column',
    background: '#161b22', borderTop: '1px solid #30363d',
    flex: 1, minHeight: 0,
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
  tradeRow: { display: 'flex', gap: 4, alignItems: 'center', padding: '3px 0', borderBottom: '1px solid #21262d' },
  tradeCell: { fontSize: 11, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  traceCell: { fontSize: 11, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
}

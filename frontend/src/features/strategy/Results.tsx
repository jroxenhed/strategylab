import { useState, useEffect, useRef } from 'react'
import { createChart, BaselineSeries, LineSeries, HistogramSeries, ColorType } from 'lightweight-charts'
import type { IChartApi } from 'lightweight-charts'
import type { BacktestResult, SignalTraceEntry, StrategyRequest } from '../../shared/types'
import { useMacro } from '../../shared/hooks/useMacro'
import { fmtDateTimeET } from '../../shared/utils/time'
import PnlHistogram from './PnlHistogram'
import MacroEquityChart from './MacroEquityChart'

export type ResultsTab = 'summary' | 'equity' | 'trades' | 'trace'

function fmtDate(d: string | number | undefined): string {
  if (typeof d === 'number') return fmtDateTimeET(d)
  return d ?? '—'
}

interface Props {
  result: BacktestResult
  mainChart?: IChartApi | null
  activeTab: ResultsTab
  onTabChange: (tab: ResultsTab) => void
  bucket: string | null
  onBucketChange: (bucket: string | null) => void
  lastRequest: StrategyRequest | null
}

function autoDefaultBucket(equityLength: number): string {
  if (equityLength < 500) return 'W'
  if (equityLength <= 5000) return 'D'
  if (equityLength <= 50000) return 'W'
  return 'M'
}

export default function Results({ result, mainChart, activeTab, onTabChange, bucket, onBucketChange, lastRequest }: Props) {
  const { summary, trades, equity_curve, signal_trace } = result
  const [showBaseline, setShowBaseline] = useState(false)
  const [logScale, setLogScale] = useState(false)
  const chartRef = useRef<HTMLDivElement>(null)
  const sells = trades.filter(t => t.type === 'sell' || t.type === 'cover')
  const { data: macroData, isLoading: macroLoading } = useMacro(lastRequest, bucket)

  useEffect(() => {
    if (activeTab !== 'equity' || bucket !== null || !chartRef.current || equity_curve.length === 0) return
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

    // Trade density ticks at exact bar positions
    if (sells.length > 0) {
      const maxPnl = Math.max(...sells.map(s => Math.abs(s.pnl ?? 0)), 1)
      const tickSeries = chart.addSeries(HistogramSeries, {
        priceScaleId: 'trade-ticks',
        base: 0,
        lastValueVisible: false,
        priceLineVisible: false,
      })
      chart.priceScale('trade-ticks').applyOptions({
        visible: false,
        scaleMargins: { top: 0.92, bottom: 0 },
      })
      tickSeries.setData(
        sells.map(s => {
          const pnl = s.pnl ?? 0
          const intensity = 0.3 + 0.7 * Math.min(1, Math.abs(pnl) / maxPnl)
          return {
            time: s.date as any,
            value: 1,
            color: pnl >= 0
              ? `rgba(38, 166, 65, ${intensity})`
              : `rgba(248, 81, 73, ${intensity})`,
          }
        })
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
  }, [activeTab, bucket, equity_curve, summary.total_return_pct, mainChart, showBaseline, result.baseline_curve])

  return (
    <div style={styles.container}>
      <div style={{ ...styles.tabBar, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex' }}>
          {(['summary', 'equity', 'trades', ...(signal_trace ? ['trace'] : [])] as ResultsTab[]).map(tab => (
            <button
              key={tab}
              onClick={() => onTabChange(tab)}
              style={{ ...styles.tab, ...(activeTab === tab ? styles.tabActive : {}) }}
            >
              {tab === 'summary' ? 'Summary' : tab === 'equity' ? 'Equity Curve' : tab === 'trades' ? `Trades (${sells.length})` : `Signal Trace (${signal_trace!.length})`}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', marginLeft: 'auto', gap: 2, alignItems: 'center' }}>
          {(['Detail', 'D', 'W', 'M', 'Q', 'Y'] as const).map(b => {
            const isDetail = b === 'Detail'
            const isActive = isDetail ? bucket === null : bucket === b
            const isRecommended = !isDetail && bucket === null && b === autoDefaultBucket(equity_curve.length)
            return (
              <button
                key={b}
                onClick={() => onBucketChange(isDetail ? null : b)}
                style={{
                  padding: '4px 8px',
                  fontSize: 11,
                  fontWeight: 600,
                  color: isActive ? '#58a6ff' : isRecommended ? '#58a6ff' : '#8b949e',
                  background: isActive ? 'rgba(88, 166, 255, 0.1)' : 'none',
                  border: 'none',
                  borderBottom: isRecommended && !isActive ? '2px solid rgba(88, 166, 255, 0.3)' : '2px solid transparent',
                  borderRadius: 3,
                  cursor: 'pointer',
                }}
              >
                {b}
                {!isDetail && macroLoading && bucket === b && ' ...'}
              </button>
            )
          })}
          {activeTab === 'equity' && (
            <>
              <div style={{ width: 1, height: 16, background: '#30363d', margin: '0 6px' }} />
              <button
                onClick={() => setShowBaseline(v => !v)}
                style={{
                  padding: '4px 8px',
                  fontSize: 11,
                  fontWeight: 600,
                  color: showBaseline ? '#58a6ff' : '#8b949e',
                  background: showBaseline ? 'rgba(88, 166, 255, 0.1)' : 'none',
                  border: 'none',
                  borderRadius: 3,
                  cursor: 'pointer',
                }}
              >
                B&amp;H
              </button>
              <button
                onClick={() => setLogScale(v => !v)}
                style={{
                  padding: '4px 8px',
                  fontSize: 11,
                  fontWeight: 600,
                  color: logScale ? '#58a6ff' : '#8b949e',
                  background: logScale ? 'rgba(88, 166, 255, 0.1)' : 'none',
                  border: 'none',
                  borderRadius: 3,
                  cursor: 'pointer',
                }}
              >
                Log
              </button>
            </>
          )}
        </div>
      </div>

      {(activeTab === 'summary' || activeTab === 'equity') && (
        <div style={styles.metricsGrid}>
          {[
            { label: 'Return', value: `${summary.total_return_pct > 0 ? '+' : ''}${summary.total_return_pct}%`, color: summary.total_return_pct >= 0 ? '#26a641' : '#f85149', primary: true },
            { label: 'Final Value', value: `$${summary.final_value.toLocaleString()}`, color: '#e6edf3', primary: true },
            { label: 'B&H Return', value: `${summary.buy_hold_return_pct > 0 ? '+' : ''}${summary.buy_hold_return_pct}%`, color: '#8b949e', primary: false },
            { label: 'Trades', value: summary.num_trades, color: '#e6edf3', primary: false },
            { label: 'Win Rate', value: `${summary.win_rate_pct}%`, color: summary.win_rate_pct >= 50 ? '#26a641' : '#f85149', primary: false },
            { label: 'Sharpe', value: summary.sharpe_ratio, color: summary.sharpe_ratio >= 1 ? '#26a641' : '#8b949e', primary: false },
            { label: 'Max DD', value: `${summary.max_drawdown_pct}%`, color: '#f85149', primary: false },
          ].map(({ label, value, color, primary }) => (
            <div key={label} style={{ ...styles.metric, minWidth: primary ? 140 : 90 }}>
              <div style={{ fontSize: 10, color: '#8b949e', marginBottom: 2 }}>{label}</div>
              <div style={{ fontSize: primary ? 22 : 13, fontWeight: 700, color }}>{value}</div>
            </div>
          ))}
        </div>
      )}

      {activeTab === 'summary' && (
        <div style={{ display: 'flex', flexDirection: 'column', height: 250, minHeight: 100, maxHeight: 600, resize: 'vertical', overflow: 'auto' }}>
          {summary.num_trades > 0 && (summary.gain_stats || summary.loss_stats) && (
            <div style={{ display: 'flex', flexDirection: 'column', padding: '12px 16px', borderTop: '1px solid #21262d' }}>
              <div style={{ marginBottom: 8 }}>
                <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>P&amp;L Distribution</span>
              </div>

              <EvPfHeader
                evPerTrade={summary.ev_per_trade ?? null}
                profitFactor={summary.profit_factor ?? null}
                grossProfit={summary.gross_profit ?? 0}
              />

              <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start', marginTop: 12 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 220 }}>
                  <StatRow label="Biggest win" value={summary.gain_stats?.max} color="#26a641" />
                  <StatRow label="Avg win" value={summary.gain_stats?.mean} color="#26a641" />
                  <StatRow label="Smallest win" value={summary.gain_stats?.min} color="#26a641" />
                  <StatRow label="Biggest loss" value={summary.loss_stats?.min} color="#f85149" />
                  <StatRow label="Avg loss" value={summary.loss_stats?.mean} color="#f85149" />
                  <StatRow label="Smallest loss" value={summary.loss_stats?.max} color="#f85149" />
                </div>
                <div style={{ flex: 1, minWidth: 280 }}>
                  <EvWaterfall
                    winRatePct={summary.win_rate_pct}
                    avgGain={summary.gain_stats?.mean ?? 0}
                    avgLoss={Math.abs(summary.loss_stats?.mean ?? 0)}
                    grossProfit={summary.gross_profit ?? 0}
                    grossLoss={summary.gross_loss ?? 0}
                    numSells={summary.num_trades}
                    evPerTrade={summary.ev_per_trade ?? null}
                  />
                </div>
                <PnlHistogram values={summary.pnl_distribution ?? []} />
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === 'equity' && (
        bucket && macroData ? (
          <MacroEquityChart
            macroCurve={macroData.macro_curve}
            initialCapital={summary.initial_capital}
          />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <div ref={chartRef} style={{ width: '100%', height: 250, minHeight: 100, maxHeight: 600, resize: 'vertical', overflow: 'hidden' }} />
          </div>
        )
      )}

      {(activeTab === 'summary' || activeTab === 'equity') && macroData?.period_stats && bucket && (() => {
        const ps = macroData.period_stats
        const periodName: Record<string, string> = { Daily: 'Day', Weekly: 'Week', Monthly: 'Month', Quarterly: 'Quarter', Yearly: 'Year' }
        const pn = periodName[ps.label] ?? ps.label
        return (
          <div style={{
            display: 'flex', flexWrap: 'wrap', gap: 0, padding: '10px 16px',
            background: '#0d1117', borderTop: '1px solid #21262d', borderBottom: '1px solid #21262d',
            flexShrink: 0,
          }}>
            <div style={{ width: '100%', marginBottom: 6 }}>
              <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                {ps.label} Stats
              </span>
            </div>
            {[
              { label: `Winning ${pn}s`, value: `${ps.winning_pct}%`, color: ps.winning_pct >= 50 ? '#26a641' : '#f85149' },
              { label: 'Avg Return', value: `${ps.avg_return_pct > 0 ? '+' : ''}${ps.avg_return_pct}%`, color: ps.avg_return_pct >= 0 ? '#26a641' : '#f85149' },
              { label: `Best ${pn}`, value: `+${ps.best_return_pct}%`, color: '#26a641' },
              { label: `Worst ${pn}`, value: `${ps.worst_return_pct}%`, color: '#f85149' },
              { label: `Trades/${pn.charAt(0)}`, value: ps.avg_trades.toFixed(1), color: '#e6edf3' },
            ].map(({ label, value, color }) => (
              <div key={label} style={{ padding: '4px 20px 4px 0', minWidth: 100 }}>
                <div style={{ fontSize: 10, color: '#8b949e', marginBottom: 2 }}>{label}</div>
                <div style={{ fontSize: 13, fontWeight: 700, color }}>{value}</div>
              </div>
            ))}
          </div>
        )
      })()}

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

function StatRow({
  label,
  value,
  color,
}: {
  label: string
  value: number | null | undefined
  color: string
}) {
  const fmt = (v: number | null | undefined) =>
    v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}`
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, gap: 8 }}>
      <span style={{ color: '#8b949e' }}>{label}</span>
      <span style={{ fontFamily: 'monospace', color: value == null ? '#484f58' : color }}>
        {fmt(value)}
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

  return (
    <div style={{ display: 'flex', gap: 32, alignItems: 'baseline', marginBottom: 8 }}>
      <div>
        <span style={{ fontSize: 10, color: '#8b949e', marginRight: 6 }}>EV</span>
        <span style={{ fontSize: 16, fontWeight: 700, color: evColor }}>{evText}</span>
      </div>
      <div>
        <span style={{ fontSize: 10, color: '#8b949e', marginRight: 6 }}>PF</span>
        <span style={{ fontSize: 16, fontWeight: 700, color: pfColor }}>{pfText}</span>
      </div>
    </div>
  )
}

function EvWaterfall({
  winRatePct,
  avgGain,
  avgLoss,
  grossProfit,
  grossLoss,
  numSells,
  evPerTrade,
}: {
  winRatePct: number
  avgGain: number
  avgLoss: number
  grossProfit: number
  grossLoss: number
  numSells: number
  evPerTrade: number | null
}) {
  if (numSells <= 0) return null

  const winContribution = grossProfit / numSells
  const lossContribution = grossLoss / numSells
  const netContribution = evPerTrade ?? 0
  const lossRatePct = 100 - winRatePct

  const showWins = grossProfit > 0
  const showLosses = grossLoss > 0
  const maxContribution = Math.max(winContribution, lossContribution, 0.0001)

  const barFor = (value: number, max: number) => ({
    width: `${Math.min(100, (Math.abs(value) / max) * 100)}%`,
    height: 14,
    borderRadius: 3,
  })

  const fmtSigned = (v: number) => `${v >= 0 ? '+' : '-'}$${Math.abs(v).toFixed(2)}`
  const fmtUnsigned = (v: number) => `$${Math.abs(v).toFixed(2)}`
  const netColor = netContribution > 0 ? '#26a641' : netContribution < 0 ? '#f85149' : '#8b949e'

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '55px 1fr auto auto',
        columnGap: 12,
        rowGap: 4,
        alignItems: 'center',
        marginTop: 4,
        marginBottom: 4,
        fontFamily: 'monospace',
        fontSize: 11,
      }}
    >
      {showWins && (
        <>
          <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase' }}>Wins</span>
          <div style={{ ...barFor(winContribution, maxContribution), background: '#26a641' }} />
          <span style={{ color: '#8b949e' }}>
            {winRatePct.toFixed(1)}% × {fmtUnsigned(avgGain)} =
          </span>
          <span style={{ color: '#26a641', textAlign: 'right' }}>{fmtSigned(winContribution)}</span>
        </>
      )}

      {showLosses && (
        <>
          <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase' }}>Losses</span>
          <div style={{ ...barFor(lossContribution, maxContribution), background: '#f85149' }} />
          <span style={{ color: '#8b949e' }}>
            {lossRatePct.toFixed(1)}% × {fmtUnsigned(avgLoss)} =
          </span>
          <span style={{ color: '#f85149', textAlign: 'right' }}>{fmtSigned(-lossContribution)}</span>
        </>
      )}

      <span
        style={{
          fontSize: 10,
          color: '#8b949e',
          textTransform: 'uppercase',
          borderTop: '1px solid #30363d',
          paddingTop: 4,
          marginTop: 2,
        }}
      >
        Net
      </span>
      <div
        style={{
          ...barFor(netContribution, maxContribution),
          background: netColor,
          marginTop: 6,
        }}
      />
      <span style={{ borderTop: '1px solid #30363d', paddingTop: 4, marginTop: 2 }} />
      <span
        style={{
          color: netColor,
          textAlign: 'right',
          borderTop: '1px solid #30363d',
          paddingTop: 4,
          marginTop: 2,
        }}
      >
        {fmtSigned(netContribution)}
      </span>
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
  metricsGrid: { display: 'flex', flexWrap: 'wrap', padding: '12px 16px', gap: 0, alignContent: 'flex-start', flexShrink: 0 },
  metric: { padding: '6px 20px 6px 0', minWidth: 110 },
  tradeList: { flex: 1, overflowY: 'auto', padding: '8px 12px' },
  tradeRow: { display: 'flex', gap: 4, alignItems: 'center', padding: '3px 0', borderBottom: '1px solid #21262d' },
  tradeCell: { fontSize: 11, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  traceCell: { fontSize: 11, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
}

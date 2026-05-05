import { useState, useEffect, useRef } from 'react'
import { createChart, LineSeries, ColorType, LineStyle } from 'lightweight-charts'
import type { SavedStrategy, BacktestResult, DataSource } from '../../shared/types'
import { api } from '../../api/client'
import { loadSavedStrategies } from './savedStrategies'
import { toDisplayTime } from '../../shared/utils/time'

interface ComparisonResult {
  strategy_name: string
  result: BacktestResult
  color: string
}

interface Props {
  ticker: string
  start: string
  end: string
  interval: string
  dataSource: DataSource
  capital?: number
  extendedHours?: boolean
}

const COLORS = ['#2196F3', '#FF9800', '#4CAF50']

const METRICS: Array<{ key: keyof BacktestResult['summary']; label: string; format: (v: any) => string; higherIsBetter: boolean }> = [
  { key: 'total_return_pct', label: 'Return %', format: v => `${v?.toFixed(2)}%`, higherIsBetter: true },
  { key: 'sharpe_ratio', label: 'Sharpe Ratio', format: v => v?.toFixed(3) ?? '—', higherIsBetter: true },
  { key: 'win_rate_pct', label: 'Win Rate %', format: v => `${v?.toFixed(1)}%`, higherIsBetter: true },
  { key: 'num_trades', label: 'Num Trades', format: v => String(v ?? 0), higherIsBetter: false },
  { key: 'max_drawdown_pct', label: 'Max Drawdown %', format: v => `${v?.toFixed(2)}%`, higherIsBetter: false },
  { key: 'profit_factor', label: 'Profit Factor', format: v => v != null ? v.toFixed(2) : '—', higherIsBetter: true },
  { key: 'ev_per_trade', label: 'Expected Value', format: v => v != null ? `$${v.toFixed(2)}` : '—', higherIsBetter: true },
  { key: 'buy_hold_return_pct', label: 'vs B&H %', format: v => `${v?.toFixed(2)}%`, higherIsBetter: true },
]

export default function StrategyComparison({ ticker, start, end, interval, dataSource, capital = 10000, extendedHours }: Props) {
  const [strategies, setStrategies] = useState<SavedStrategy[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [results, setResults] = useState<ComparisonResult[]>([])
  const [normalize, setNormalize] = useState(false)
  const chartRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setStrategies(loadSavedStrategies())
  }, [])

  // Build + teardown chart whenever results or normalize mode changes
  useEffect(() => {
    if (!chartRef.current || results.length === 0) return
    const chart = createChart(chartRef.current, {
      height: chartRef.current.clientHeight || 240,
      layout: { background: { type: ColorType.Solid, color: '#0d1117' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#1c2128' }, horzLines: { color: '#1c2128' } },
      timeScale: { borderColor: '#30363d' },
      rightPriceScale: { borderColor: '#30363d' },
    })

    for (const cr of results) {
      const series = chart.addSeries(LineSeries, {
        color: cr.color,
        lineWidth: 2,
        priceScaleId: 'right',
      })
      const raw = cr.result.equity_curve.filter(d => d.value !== null)
      const base = normalize && raw.length > 0 ? (raw[0].value as number) : 1
      const data = raw.map(d => ({
        time: toDisplayTime(d.time) as any,
        value: normalize ? ((d.value as number) / base - 1) * 100 : (d.value as number),
      }))
      series.setData(data)
    }

    // Add B&H baseline from first result as a dashed reference line
    const baseline = results[0]?.result.baseline_curve
    if (baseline && baseline.length > 0) {
      const bh = chart.addSeries(LineSeries, {
        color: '#8b949e',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceScaleId: 'right',
        title: 'B&H',
      })
      const rawBh = baseline.filter(d => d.value !== null)
      const bhBase = normalize && rawBh.length > 0 ? (rawBh[0].value as number) : 1
      bh.setData(rawBh.map(d => ({
        time: toDisplayTime(d.time) as any,
        value: normalize ? ((d.value as number) / bhBase - 1) * 100 : (d.value as number),
      })))
    }

    chart.timeScale().fitContent()

    return () => {
      try { chart.remove() } catch { /* already removed */ }
    }
  }, [results, normalize])

  function toggleStrategy(name: string) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(name)) {
        next.delete(name)
      } else if (next.size < 3) {
        next.add(name)
      }
      return next
    })
  }

  async function runComparison() {
    if (selected.size === 0) return
    setLoading(true)
    setError('')
    setResults([])
    try {
      const toRun = strategies.filter(s => selected.has(s.name))
      const colorMap = new Map<string, string>()
      toRun.forEach((s, i) => colorMap.set(s.name, COLORS[i] ?? COLORS[0]))

      const promises = toRun.map(async (s, i) => {
        const hasRegime = s.strategyType === 'regime' || (s.regime?.enabled && ((s.longBuyRules?.length ?? 0) > 0 || (s.shortBuyRules?.length ?? 0) > 0))
        const req: Record<string, unknown> = {
          ticker, start, end, interval,
          buy_rules: s.buyRules,
          sell_rules: s.sellRules,
          buy_logic: s.buyLogic,
          sell_logic: s.sellLogic,
          initial_capital: s.capital ?? capital,
          position_size: (s.posSize ?? 100) / 100,
          stop_loss_pct: s.stopLoss !== '' && (s.stopLoss as number) > 0 ? s.stopLoss : undefined,
          max_bars_held: s.maxBarsHeld !== '' && (s.maxBarsHeld as number) > 0 ? s.maxBarsHeld : undefined,
          trailing_stop: s.trailingEnabled ? s.trailingConfig : undefined,
          dynamic_sizing: s.dynamicSizing?.enabled ? s.dynamicSizing : undefined,
          skip_after_stop: s.skipAfterStop?.enabled ? s.skipAfterStop : undefined,
          trading_hours: s.tradingHours?.enabled ? s.tradingHours : undefined,
          slippage_bps: s.slippageBps !== '' ? s.slippageBps : undefined,
          per_share_rate: s.perShareRate ?? 0,
          min_per_order: s.minPerOrder ?? 0,
          borrow_rate_annual: s.direction === 'short' ? (s.borrowRateAnnual ?? 0.5) : 0,
          source: dataSource,
          direction: s.direction,
          extended_hours: extendedHours,
          // regime + dual rule sets (B23)
          regime: s.regime,
          long_buy_rules: hasRegime ? (s.longBuyRules ?? []) : undefined,
          long_sell_rules: hasRegime ? (s.longSellRules ?? []) : undefined,
          long_buy_logic: hasRegime ? (s.longBuyLogic ?? 'AND') : undefined,
          long_sell_logic: hasRegime ? (s.longSellLogic ?? 'AND') : undefined,
          short_buy_rules: hasRegime ? (s.shortBuyRules ?? []) : undefined,
          short_sell_rules: hasRegime ? (s.shortSellRules ?? []) : undefined,
          short_buy_logic: hasRegime ? (s.shortBuyLogic ?? 'AND') : undefined,
          short_sell_logic: hasRegime ? (s.shortSellLogic ?? 'AND') : undefined,
          // B25 per-direction settings
          long_stop_loss_pct: s.longStopLoss !== '' && (s.longStopLoss as number) > 0 ? s.longStopLoss : undefined,
          short_stop_loss_pct: s.shortStopLoss !== '' && (s.shortStopLoss as number) > 0 ? s.shortStopLoss : undefined,
          long_max_bars_held: s.longMaxBarsHeld !== '' && (s.longMaxBarsHeld as number) > 0 ? s.longMaxBarsHeld : undefined,
          short_max_bars_held: s.shortMaxBarsHeld !== '' && (s.shortMaxBarsHeld as number) > 0 ? s.shortMaxBarsHeld : undefined,
          long_position_size: s.longPosSize != null ? s.longPosSize / 100 : undefined,
          short_position_size: s.shortPosSize != null ? s.shortPosSize / 100 : undefined,
          long_trailing_stop: s.longTrailingEnabled ? s.longTrailingConfig : undefined,
          short_trailing_stop: s.shortTrailingEnabled ? s.shortTrailingConfig : undefined,
        }
        const { data } = await api.post('/api/backtest', req)
        return { strategy_name: s.name, result: data as BacktestResult, color: COLORS[i] ?? COLORS[0] }
      })
      const all = await Promise.all(promises)
      setResults(all)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? 'Backtest failed')
    } finally {
      setLoading(false)
    }
  }

  // Determine best/worst per metric across results
  function getHighlight(metricKey: keyof BacktestResult['summary'], idx: number, higherIsBetter: boolean): 'best' | 'worst' | null {
    if (results.length < 2) return null
    const vals = results.map(r => {
      const v = r.result.summary[metricKey]
      return typeof v === 'number' ? v : null
    })
    const valid = vals.filter(v => v !== null) as number[]
    if (valid.length < 2) return null
    const myVal = vals[idx]
    if (myVal === null) return null
    const best = higherIsBetter ? Math.max(...valid) : Math.min(...valid)
    const worst = higherIsBetter ? Math.min(...valid) : Math.max(...valid)
    if (myVal === best && best !== worst) return 'best'
    if (myVal === worst && best !== worst) return 'worst'
    return null
  }

  return (
    <div style={{ padding: 16, color: '#c9d1d9', fontSize: 13 }}>
      {/* Strategy selector */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ color: '#8b949e', marginBottom: 8, fontSize: 12 }}>
          Select up to 3 strategies — will run on {ticker} · {interval} · {start} → {end}
        </div>
        {strategies.length === 0 && (
          <div style={{ color: '#8b949e', fontStyle: 'italic' }}>No saved strategies found.</div>
        )}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {strategies.map(s => {
            const isChecked = selected.has(s.name)
            const colorIdx = Array.from(selected).indexOf(s.name)
            const color = colorIdx >= 0 ? COLORS[colorIdx] : '#58a6ff'
            return (
              <label
                key={s.name}
                style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  padding: '5px 10px', borderRadius: 6,
                  border: `1px solid ${isChecked ? color : '#30363d'}`,
                  background: isChecked ? `${color}18` : '#161b22',
                  cursor: selected.size >= 3 && !isChecked ? 'not-allowed' : 'pointer',
                  opacity: selected.size >= 3 && !isChecked ? 0.5 : 1,
                  transition: 'all 0.15s',
                }}
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => toggleStrategy(s.name)}
                  disabled={selected.size >= 3 && !isChecked}
                  style={{ accentColor: color, cursor: 'inherit' }}
                />
                <span style={{ color: isChecked ? color : '#c9d1d9' }}>{s.name}</span>
              </label>
            )
          })}
        </div>
      </div>

      {/* Run button */}
      <button
        onClick={runComparison}
        disabled={loading || selected.size === 0}
        style={{
          padding: '6px 16px', borderRadius: 6, border: 'none', cursor: loading || selected.size === 0 ? 'not-allowed' : 'pointer',
          background: selected.size === 0 ? '#21262d' : '#238636', color: selected.size === 0 ? '#8b949e' : '#fff',
          fontWeight: 600, fontSize: 13, opacity: loading ? 0.7 : 1, marginBottom: 16,
        }}
      >
        {loading ? 'Running…' : `Run Comparison (${selected.size} selected)`}
      </button>

      {error && (
        <div style={{ color: '#f85149', background: '#2d1517', border: '1px solid #f8514933', borderRadius: 6, padding: '8px 12px', marginBottom: 12 }}>
          {error}
        </div>
      )}

      {/* Loading spinner */}
      {loading && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#8b949e', marginBottom: 12 }}>
          <span style={{ display: 'inline-block', width: 14, height: 14, borderRadius: '50%', border: '2px solid #30363d', borderTopColor: '#58a6ff', animation: 'spin 0.8s linear infinite' }} />
          Running {selected.size} backtest{selected.size > 1 ? 's' : ''} in parallel…
        </div>
      )}

      {results.length > 0 && (
        <>
          {/* Legend */}
          <div style={{ display: 'flex', gap: 16, marginBottom: 8, flexWrap: 'wrap' }}>
            {results.map(r => (
              <div key={r.strategy_name} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 14, height: 3, borderRadius: 2, background: r.color, display: 'inline-block' }} />
                <span style={{ color: r.color, fontWeight: 600 }}>{r.strategy_name}</span>
              </div>
            ))}
            {results[0]?.result.baseline_curve && results[0].result.baseline_curve.length > 0 && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 14, height: 1, background: '#8b949e', display: 'inline-block', borderTop: '1px dashed #8b949e' }} />
                <span style={{ color: '#8b949e' }}>Buy &amp; Hold</span>
              </div>
            )}
          </div>

          {/* Chart toolbar */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 4 }}>
            <button
              onClick={() => setNormalize(n => !n)}
              style={{
                padding: '3px 10px', borderRadius: 4, border: `1px solid ${normalize ? '#58a6ff55' : '#30363d'}`,
                background: normalize ? 'rgba(88,166,255,0.15)' : '#161b22', color: normalize ? '#58a6ff' : '#8b949e',
                fontSize: 11, cursor: 'pointer',
              }}
              title="Show % return from starting value instead of absolute dollar values"
            >
              % Normalized
            </button>
          </div>

          {/* Equity curve overlay */}
          <div ref={chartRef} style={{ height: 240, marginBottom: 16, borderRadius: 6, overflow: 'hidden', border: '1px solid #30363d' }} />

          {/* Metrics table */}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #30363d' }}>
                  <th style={{ ...thStyle, textAlign: 'left', color: '#8b949e' }}>Metric</th>
                  {results.map(r => (
                    <th key={r.strategy_name} style={{ ...thStyle, color: r.color }}>{r.strategy_name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {METRICS.map(m => (
                  <tr key={m.key} style={{ borderBottom: '1px solid #21262d' }}>
                    <td style={{ ...tdStyle, color: '#8b949e' }}>{m.label}</td>
                    {results.map((r, idx) => {
                      const highlight = getHighlight(m.key, idx, m.higherIsBetter)
                      const val = r.result.summary[m.key]
                      return (
                        <td
                          key={r.strategy_name}
                          style={{
                            ...tdStyle,
                            color: highlight === 'best' ? '#3fb950' : highlight === 'worst' ? '#f85149' : '#c9d1d9',
                            fontWeight: highlight ? 600 : 400,
                          }}
                        >
                          {val != null ? m.format(val) : '—'}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}

const thStyle: React.CSSProperties = {
  padding: '6px 10px', fontWeight: 600, textAlign: 'right',
}

const tdStyle: React.CSSProperties = {
  padding: '5px 10px', textAlign: 'right',
}

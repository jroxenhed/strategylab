import { useState, useRef, useEffect } from 'react'
import { createChart, LineSeries, ColorType } from 'lightweight-charts'
import type { IChartApi } from 'lightweight-charts'
import { fetchPerformance, type PerformanceResponse } from '../../api/trading'
import type { Rule } from '../../shared/types'

const SCANNER_STORAGE_KEY = 'strategylab-scanner'

function loadScannerRules() {
  try {
    const raw = localStorage.getItem(SCANNER_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return {
      buyRules: parsed.buyRules as Rule[],
      sellRules: parsed.sellRules as Rule[],
      buyLogic: (parsed.buyLogic ?? 'AND') as 'AND' | 'OR',
      sellLogic: (parsed.sellLogic ?? 'AND') as 'AND' | 'OR',
    }
  } catch { return null }
}

export default function PerformanceComparison() {
  const [symbol, setSymbol] = useState('AAPL')
  const [start, setStart] = useState(() => {
    const d = new Date()
    d.setMonth(d.getMonth() - 1)
    return d.toISOString().slice(0, 10)
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PerformanceResponse | null>(null)

  const handleCompare = async () => {
    const rules = loadScannerRules()
    if (!rules) {
      setError('No strategy rules found — configure rules in the Signal Scanner first')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await fetchPerformance({
        symbol,
        start,
        interval: '15m',
        buy_rules: rules.buyRules,
        sell_rules: rules.sellRules,
        buy_logic: rules.buyLogic,
        sell_logic: rules.sellLogic,
      })
      setResult(res)
    } catch (e: any) {
      setError(e.response?.data?.detail || e.message || 'Failed to fetch performance')
    } finally {
      setLoading(false)
    }
  }

  const fmt = (v: number | null | undefined, suffix = '') =>
    v != null ? `${v > 0 ? '+' : ''}${v.toFixed(2)}${suffix}` : '—'
  const fmtDollar = (v: number | null | undefined) =>
    v != null ? `${v >= 0 ? '+' : ''}$${v.toFixed(2)}` : '—'
  const color = (v: number | null | undefined) =>
    v == null ? '#8b949e' : v > 0 ? '#26a641' : v < 0 ? '#f85149' : '#e6edf3'

  return (
    <div style={styles.section}>
      <div style={styles.header}>
        <span style={styles.title}>Performance: Paper vs Backtest</span>
      </div>
      <div style={styles.controls}>
        <input
          style={styles.input}
          value={symbol}
          onChange={e => setSymbol(e.target.value.toUpperCase())}
          placeholder="Symbol"
        />
        <label style={styles.label}>From</label>
        <input
          type="date"
          style={styles.input}
          value={start}
          onChange={e => setStart(e.target.value)}
        />
        <button onClick={handleCompare} disabled={loading} style={styles.btn}>
          {loading ? 'Loading...' : 'Compare'}
        </button>
        {error && <span style={{ color: '#f85149', fontSize: 12 }}>{error}</span>}
      </div>

      {result && (
        <div style={styles.table}>
          <div style={styles.headRow}>
            <span style={{ ...styles.headCell, width: 140 }}>Metric</span>
            <span style={styles.headCell}>Paper (Actual)</span>
            <span style={styles.headCell}>Backtest (Expected)</span>
          </div>
          <Row
            label="Trades"
            actual={String(result.actual.trade_count)}
            expected={result.backtest ? String(result.backtest.trade_count) : '—'}
          />
          <Row
            label="Completed"
            actual={String(result.actual.completed_trades)}
            expected="—"
          />
          <Row
            label="Total P&L"
            actual={fmtDollar(result.actual.total_pnl)}
            actualColor={color(result.actual.total_pnl)}
            expected={result.backtest ? fmt(result.backtest.total_return_pct, '%') : '—'}
            expectedColor={result.backtest ? color(result.backtest.total_return_pct) : '#8b949e'}
          />
          <Row
            label="Win Rate"
            actual={fmt(result.actual.win_rate_pct, '%')}
            expected={result.backtest ? fmt(result.backtest.win_rate_pct, '%') : '—'}
          />
          <Row
            label="Sharpe Ratio"
            actual="—"
            expected={result.backtest ? String(result.backtest.sharpe_ratio) : '—'}
          />
          <div style={{ padding: '6px 16px', fontSize: 10, color: '#484f58' }}>
            {result.symbol} · {result.period.start} → {result.period.end} · Uses Scanner strategy rules
          </div>
        </div>
      )}

      {result && <EquityChart result={result} />}
    </div>
  )
}

function EquityChart({ result }: { result: PerformanceResponse }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  const backtestCurve = result.backtest?.equity_curve ?? []
  const paperCurve = result.actual.equity_curve ?? []
  const hasData = backtestCurve.length > 0 || paperCurve.length > 0

  useEffect(() => {
    if (!containerRef.current || !hasData) return

    const chart = createChart(containerRef.current, {
      height: 200,
      layout: { background: { type: ColorType.Solid, color: '#0d1117' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#1c2128' }, horzLines: { color: '#1c2128' } },
      timeScale: { borderColor: '#1c2128', timeVisible: false },
      rightPriceScale: { borderColor: '#1c2128' },
      crosshair: { mode: 1 },
    })
    chartRef.current = chart

    if (backtestCurve.length > 0) {
      chart.addSeries(LineSeries, {
        color: '#58a6ff', lineWidth: 2, title: 'Backtest',
        priceScaleId: 'right',
      }).setData(backtestCurve.map(p => ({ time: p.time as any, value: p.value })))
    }

    if (paperCurve.length > 0) {
      chart.addSeries(LineSeries, {
        color: '#26a641', lineWidth: 2, title: 'Paper',
        priceScaleId: 'right',
      }).setData(paperCurve.map(p => ({ time: p.time as any, value: p.value })))
    }

    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth })
    })
    ro.observe(containerRef.current)

    return () => { chart.remove(); ro.disconnect() }
  }, [result])

  if (!hasData) return null

  return (
    <div style={{ padding: '0 16px 8px' }}>
      <div style={{ fontSize: 11, color: '#8b949e', marginBottom: 4 }}>
        Equity Curve
        {backtestCurve.length > 0 && <span style={{ color: '#58a6ff', marginLeft: 12 }}>— Backtest</span>}
        {paperCurve.length > 0 && <span style={{ color: '#26a641', marginLeft: 12 }}>— Paper</span>}
      </div>
      <div ref={containerRef} style={{ width: '100%', borderRadius: 4, overflow: 'hidden' }} />
    </div>
  )
}

function Row({ label, actual, expected, actualColor, expectedColor }: {
  label: string
  actual: string
  expected: string
  actualColor?: string
  expectedColor?: string
}) {
  return (
    <div style={styles.row}>
      <span style={{ ...styles.cell, width: 140, color: '#8b949e' }}>{label}</span>
      <span style={{ ...styles.cell, color: actualColor ?? '#e6edf3' }}>{actual}</span>
      <span style={{ ...styles.cell, color: expectedColor ?? '#e6edf3' }}>{expected}</span>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  section: { background: '#0d1117', borderBottom: '1px solid #30363d' },
  header: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '8px 16px', borderBottom: '1px solid #21262d', flexShrink: 0,
  },
  title: { fontSize: 13, fontWeight: 600, color: '#e6edf3' },
  controls: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '8px 16px',
  },
  label: { fontSize: 11, color: '#8b949e' },
  input: {
    fontSize: 12, padding: '4px 8px', borderRadius: 4,
    background: '#161b22', color: '#e6edf3', border: '1px solid #30363d',
    outline: 'none', width: 100,
  },
  btn: {
    fontSize: 12, padding: '4px 12px', borderRadius: 4,
    background: '#21262d', color: '#e6edf3', border: '1px solid #30363d',
    cursor: 'pointer',
  },
  table: { },
  headRow: {
    display: 'flex', gap: 4, padding: '4px 16px',
    borderBottom: '1px solid #21262d',
  },
  headCell: {
    fontSize: 10, color: '#8b949e', width: 120, flexShrink: 0,
    textTransform: 'uppercase' as const, letterSpacing: '0.03em',
  },
  row: {
    display: 'flex', gap: 4, padding: '5px 16px',
    borderBottom: '1px solid #161b22',
  },
  cell: { fontSize: 12, width: 120, flexShrink: 0 },
}

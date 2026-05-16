import { useState, useMemo, useEffect, useRef } from 'react'
import type { StrategyRequest } from '../../shared/types'
import { api } from '../../api/client'
import { useRequestTimer } from '../../shared/hooks/useRequestTimer'
import { apiErrorDetail } from '../../shared/utils/errors'
import { buildParamOptions, linspace, applyParamPath } from './paramOptions'
import type { ParamOption } from './paramOptions'

interface OptimizerCombo {
  param_values: Record<string, number>
  num_trades: number
  total_return_pct: number
  sharpe_ratio: number
  win_rate_pct: number
  max_drawdown_pct: number
  ev_per_trade: number | null
}

interface OptimizeResponse {
  results: OptimizerCombo[]
  total_combos: number
  completed: number
  skipped: number
  timed_out?: boolean
}

interface ParamRow {
  path: string
  min: string
  max: string
  steps: string
}

type MetricKey = 'total_return_pct' | 'sharpe_ratio' | 'win_rate_pct' | 'max_drawdown_pct'


const METRICS = [
  { value: 'sharpe_ratio', label: 'Sharpe Ratio' },
  { value: 'total_return_pct', label: 'Total Return %' },
  { value: 'win_rate_pct', label: 'Win Rate %' },
]

const NONE_PATH = ''


interface Props {
  lastRequest: StrategyRequest
  onApplyParams?: (updatedReq: StrategyRequest) => void
  onRunBacktest?: () => void
}

export default function OptimizerPanel({ lastRequest, onApplyParams, onRunBacktest }: Props) {
  const paramOptions = useMemo(() => buildParamOptions(lastRequest, 5), [lastRequest])

  const emptyRow = (): ParamRow => ({ path: paramOptions[0]?.path ?? NONE_PATH, min: '', max: '', steps: '5' })

  const [paramRows, setParamRows] = useState<(ParamRow | null)[]>([emptyRow(), null, null])
  const [metric, setMetric] = useState('sharpe_ratio')
  const [topN, setTopN] = useState('10')
  const [loading, setLoading] = useState(false)
  const { elapsed: elapsedSec, final: finalSec } = useRequestTimer(loading)
  const [error, setError] = useState('')
  const [result, setResult] = useState<OptimizeResponse | null>(null)
  const [selectedRowIdx, setSelectedRowIdx] = useState<number | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)

  // Persist input config per (ticker, interval, source). Survives tab switches
  // and page reloads. Result is NOT persisted (could be MB-scale).
  const storageKey = `strategylab-optimizer-config-${lastRequest.ticker}-${lastRequest.interval}-${lastRequest.source ?? 'yahoo'}`

  useEffect(() => {
    let saved: { metric?: string; topN?: string; paramRows?: (ParamRow | null)[] } | null = null
    try {
      const raw = localStorage.getItem(storageKey)
      if (raw) saved = JSON.parse(raw)
    } catch { /* corrupt entry — fall through */ }

    if (saved) {
      if (typeof saved.metric === 'string') setMetric(saved.metric)
      if (typeof saved.topN === 'string') setTopN(saved.topN)
      if (Array.isArray(saved.paramRows)) {
        const validPaths = new Set(paramOptions.map((o) => o.path))
        const restored = saved.paramRows.map((r) =>
          r && validPaths.has(r.path) ? r : null
        )
        while (restored.length < 3) restored.push(null)
        if (restored.filter(Boolean).length === 0) restored[0] = emptyRow()
        setParamRows(restored as (ParamRow | null)[])
      }
    } else {
      setMetric('sharpe_ratio')
      setTopN('10')
      setParamRows([emptyRow(), null, null])
    }
    setResult(null)
    setError('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey])

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify({ metric, topN, paramRows }))
    } catch { /* quota exceeded */ }
  }, [storageKey, metric, topN, paramRows])

  // Abort in-flight request on unmount (stale strategy context).
  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort()
    }
  }, [])

  const activeRows = paramRows.filter((p): p is ParamRow => p !== null && p.path !== NONE_PATH)

  const estimatedCombos = useMemo(() => {
    let total = 1
    for (const p of activeRows) {
      const stepsN = Math.max(1, parseInt(p.steps) || 5)
      total *= stepsN
    }
    return total
  }, [activeRows])

  const setRow = (i: number, update: Partial<ParamRow> | null) => {
    setParamRows(prev => {
      const next = [...prev]
      if (update === null) {
        next[i] = null
      } else {
        next[i] = { ...(prev[i] ?? emptyRow()), ...update }
      }
      return next
    })
  }

  async function runOptimizer() {
    if (activeRows.length === 0 || loading) return
    // Validate param ranges before submitting
    for (const p of activeRows) {
      const opt = paramOptions.find(o => o.path === p.path)
      if (!opt) continue
      const minN = p.min !== '' ? parseFloat(p.min) : opt.defaultMin
      const maxN = p.max !== '' ? parseFloat(p.max) : opt.defaultMax
      const stepsN = p.steps !== '' ? parseInt(p.steps) : NaN
      if (p.min === '' && isNaN(minN)) {
        setError(`"${opt.label}": System default Min is missing — enter a value manually`)
        return
      }
      if (p.max === '' && isNaN(maxN)) {
        setError(`"${opt.label}": System default Max is missing — enter a value manually`)
        return
      }
      if (isNaN(minN)) {
        setError(`"${opt.label}": Min is not a valid number`)
        return
      }
      if (isNaN(maxN)) {
        setError(`"${opt.label}": Max is not a valid number`)
        return
      }
      if (isNaN(stepsN)) {
        setError(`"${opt.label}": Steps is not a valid number`)
        return
      }
      if (stepsN < 2) {
        setError(`"${opt.label}": Steps must be at least 2`)
        return
      }
      if (minN > maxN) {
        setError(`"${opt.label}": Min (${minN}) cannot be greater than Max (${maxN})`)
        return
      }
    }
    const controller = new AbortController()
    abortControllerRef.current = controller

    setLoading(true)
    setError('')
    setResult(null)
    try {
      const requestParams = activeRows.map(p => {
        const opt = paramOptions.find(o => o.path === p.path)
        if (!opt) throw new Error(`Unknown param: ${p.path}`)
        const minN = p.min !== '' ? parseFloat(p.min) : opt.defaultMin
        const maxN = p.max !== '' ? parseFloat(p.max) : opt.defaultMax
        const stepsN = p.steps !== '' ? parseInt(p.steps) : NaN
        let values = linspace(minN, maxN, stepsN)
        if (opt.isInteger) {
          values = [...new Set(values.map(v => Math.round(v)))]
        }
        return { path: p.path, values }
      })
      const { data } = await api.post<OptimizeResponse>('/api/backtest/optimize', {
        base: lastRequest,
        params: requestParams,
        metric,
        top_n: Math.max(1, parseInt(topN) || 10),
      }, { signal: controller.signal })
      setResult(data)
      setSelectedRowIdx(null)
    } catch (e: unknown) {
      // Ignore abort errors from strategy-context change or unmount.
      const err = e as { name?: string; code?: string }
      if (err?.name === 'AbortError' || err?.code === 'ERR_CANCELED') return
      setError(apiErrorDetail(e, 'Optimizer failed'))
    } finally {
      setLoading(false)
      abortControllerRef.current = null
    }
  }

  const topResult = result?.results[0]

  /** Build an updated StrategyRequest with all param values from the given combo row applied. */
  function buildAppliedReq(combo: OptimizerCombo): StrategyRequest {
    let req = lastRequest
    for (const p of activeRows) {
      const val = combo.param_values[p.path]
      if (val != null) req = applyParamPath(req, p.path, val)
    }
    return req
  }

  function handleApply(combo: OptimizerCombo) {
    if (!onApplyParams) return
    onApplyParams(buildAppliedReq(combo))
  }

  function handleApplyAndRun(combo: OptimizerCombo) {
    if (!onApplyParams || !onRunBacktest) return
    onApplyParams(buildAppliedReq(combo))
    onRunBacktest()
  }

  const colColor = (value: number, key: MetricKey) => {
    if (!result || result.results.length < 2) return '#e6edf3'
    const vals = result.results.map(r => r[key]).filter(v => typeof v === 'number')
    const min = Math.min(...vals), max = Math.max(...vals)
    if (max === min) return '#e6edf3'
    const t = (value - min) / (max - min)
    const pct = key === 'max_drawdown_pct' ? 1 - t : t
    if (pct >= 0.7) return '#26a69a'
    if (pct >= 0.4) return '#aaa'
    return '#ef5350'
  }

  return (
    <div style={{ maxWidth: 900 }}>

      {/* ─── Controls ───────────────────────────────────────────────── */}
      <div style={s.section}>
        <div style={s.row}>
          <span style={s.label}>Optimize for</span>
          <select value={metric} onChange={e => setMetric(e.target.value)} style={s.select}>
            {METRICS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
          <span style={{ ...s.label, marginLeft: 16 }}>Show top</span>
          <input
            type="number" min={1} max={50} value={topN}
            onChange={e => setTopN(e.target.value)}
            style={{ ...s.input, width: 50 }}
          />
        </div>
      </div>

      {/* ─── Param rows ─────────────────────────────────────────────── */}
      {[0, 1, 2].map(i => {
        const row = paramRows[i]
        const isActive = row !== null
        const opt = isActive ? paramOptions.find((o: ParamOption) => o.path === row.path) : null
        return (
          <div key={i} style={{ ...s.section, opacity: i > 0 && !paramRows[i - 1] ? 0.4 : 1 }}>
            <div style={s.row}>
              <span style={s.label}>Param {i + 1}</span>
              {isActive ? (
                <>
                  <select
                    value={row.path}
                    onChange={e => setRow(i, { path: e.target.value, min: '', max: '', steps: '5' })}
                    style={{ ...s.select, minWidth: 240 }}
                  >
                    {paramOptions.map((o: ParamOption) => (
                      <option key={o.path} value={o.path}>{o.label}</option>
                    ))}
                  </select>
                  <span style={s.label}>Min</span>
                  <input
                    type="number"
                    placeholder={opt ? String(opt.defaultMin) : ''}
                    value={row.min}
                    onChange={e => setRow(i, { min: e.target.value })}
                    style={s.input}
                  />
                  <span style={s.label}>Max</span>
                  <input
                    type="number"
                    placeholder={opt ? String(opt.defaultMax) : ''}
                    value={row.max}
                    onChange={e => setRow(i, { max: e.target.value })}
                    style={s.input}
                  />
                  <span style={s.label}>Steps</span>
                  <input
                    type="number" min={2} max={10}
                    value={row.steps}
                    onChange={e => setRow(i, { steps: e.target.value })}
                    style={{ ...s.input, width: 50 }}
                  />
                  {i > 0 && (
                    <button onClick={() => setRow(i, null)} style={s.removeBtn}>✕</button>
                  )}
                </>
              ) : (
                <button
                  onClick={() => setRow(i, emptyRow())}
                  disabled={!paramRows[i - 1]}
                  style={s.addBtn}
                >
                  + Add param
                </button>
              )}
            </div>
          </div>
        )
      })}

      {/* ─── Run button ─────────────────────────────────────────────── */}
      <div style={{ ...s.section, ...s.row, gap: 12 }}>
        <button
          onClick={runOptimizer}
          disabled={loading || activeRows.length === 0 || estimatedCombos > 200}
          style={{
            ...s.runBtn,
            opacity: loading || activeRows.length === 0 || estimatedCombos > 200 ? 0.6 : 1,
          }}
        >
          {loading ? `Running ${elapsedSec}s…` : 'Run Optimizer'}
        </button>
        <span style={{ fontSize: 12, color: estimatedCombos > 200 ? '#ef5350' : '#8b949e' }}>
          {estimatedCombos} combination{estimatedCombos !== 1 ? 's' : ''} estimated
          {estimatedCombos > 200 ? ' — reduce steps or params (max 200)' : ''}
        </span>
        {finalSec !== null && !loading && (
          <span style={{ fontSize: 12, color: '#3fb950' }}>
            · Completed in {finalSec}s
          </span>
        )}
      </div>

      {/* ─── Error ──────────────────────────────────────────────────── */}
      {error && (
        <div style={{ color: '#ef5350', fontSize: 12, padding: '6px 0' }}>{error}</div>
      )}

      {/* ─── Results ────────────────────────────────────────────────── */}
      {result && !loading && (
        <div>
          <div style={{ fontSize: 12, color: '#8b949e', marginBottom: 8 }}>
            {result.completed} backtests complete
            {result.skipped > 0 && `, ${result.skipped} skipped`}
            {' · '}ranked by {METRICS.find(m => m.value === metric)?.label}
          </div>

          {result.results.length === 0 ? (
            <div style={{ color: '#484f58', fontSize: 12 }}>No valid results — all combinations failed or were skipped.</div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ borderCollapse: 'collapse', fontSize: 12, width: '100%' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #30363d' }}>
                    <th style={s.th}>#</th>
                    {activeRows.map(p => {
                      const opt = paramOptions.find((o: ParamOption) => o.path === p.path)
                      return <th key={p.path} style={s.th}>{opt?.label ?? p.path}</th>
                    })}
                    <th style={s.th}>Trades</th>
                    <th style={{ ...s.th, color: metric === 'total_return_pct' ? '#58a6ff' : undefined }}>Return %</th>
                    <th style={{ ...s.th, color: metric === 'sharpe_ratio' ? '#58a6ff' : undefined }}>Sharpe</th>
                    <th style={{ ...s.th, color: metric === 'win_rate_pct' ? '#58a6ff' : undefined }}>Win %</th>
                    <th style={s.th}>Max DD %</th>
                    <th style={s.th}>EV/Trade</th>
                    {onApplyParams && <th style={s.th}>Apply</th>}
                  </tr>
                </thead>
                <tbody>
                  {result.results.map((combo, i) => {
                    const isSelected = selectedRowIdx === i
                    return (
                    <tr
                      key={i}
                      onClick={() => setSelectedRowIdx(i === selectedRowIdx ? null : i)}
                      style={{
                        borderBottom: '1px solid #161b22',
                        background: isSelected
                          ? 'rgba(88, 166, 255, 0.12)'
                          : i === 0 ? 'rgba(88, 166, 255, 0.05)' : 'transparent',
                        cursor: onApplyParams ? 'pointer' : undefined,
                      }}
                    >
                      <td style={{ ...s.td, color: '#8b949e' }}>{i + 1}</td>
                      {activeRows.map(p => (
                        <td key={p.path} style={{ ...s.td, color: '#e6edf3', fontFamily: 'monospace' }}>
                          {combo.param_values[p.path]?.toFixed(
                            paramOptions.find((o: ParamOption) => o.path === p.path)?.isInteger ? 0 : 2
                          ) ?? '—'}
                        </td>
                      ))}
                      <td style={s.td}>{combo.num_trades}</td>
                      <td style={{ ...s.td, color: colColor(combo.total_return_pct, 'total_return_pct') }}>
                        {combo.total_return_pct >= 0 ? '+' : ''}{combo.total_return_pct.toFixed(2)}%
                      </td>
                      <td style={{ ...s.td, color: colColor(combo.sharpe_ratio, 'sharpe_ratio') }}>
                        {combo.sharpe_ratio.toFixed(3)}
                      </td>
                      <td style={{ ...s.td, color: colColor(combo.win_rate_pct, 'win_rate_pct') }}>
                        {combo.win_rate_pct.toFixed(1)}%
                      </td>
                      <td style={{ ...s.td, color: colColor(combo.max_drawdown_pct, 'max_drawdown_pct') }}>
                        {combo.max_drawdown_pct.toFixed(2)}%
                      </td>
                      <td style={{ ...s.td, color: combo.ev_per_trade != null && combo.ev_per_trade >= 0 ? '#26a69a' : '#ef5350' }}>
                        {combo.ev_per_trade != null ? `$${combo.ev_per_trade.toFixed(2)}` : '—'}
                      </td>
                      {onApplyParams && (
                        <td style={{ ...s.td, whiteSpace: 'nowrap' }} onClick={e => e.stopPropagation()}>
                          {isSelected && (
                            <span style={{ display: 'inline-flex', gap: 4 }}>
                              <button
                                disabled={loading}
                                onClick={() => handleApply(combo)}
                                style={{ ...s.applyBtn, opacity: loading ? 0.5 : 1 }}
                              >
                                Apply to rules
                              </button>
                              {onRunBacktest && (
                                <button
                                  disabled={loading}
                                  onClick={() => handleApplyAndRun(combo)}
                                  style={{ ...s.applyBtn, opacity: loading ? 0.5 : 1 }}
                                >
                                  Apply &amp; Re-run
                                </button>
                              )}
                            </span>
                          )}
                        </td>
                      )}
                    </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* ─── Timeout warning (D4) ───────────────────────────────── */}
          {result.timed_out && (
            <div style={{ color: '#f0883e', fontSize: 11, padding: '4px 8px' }}>
              Optimizer timed out after 60s — showing partial results ({result.completed} of {result.total_combos} combos)
            </div>
          )}
        </div>
      )}

      {/* ─── Best params highlight + quick-apply buttons ────────────── */}
      {topResult && !loading && (
        <div style={{ marginTop: 12, padding: '8px 12px', background: 'rgba(88,166,255,0.06)', borderRadius: 4, border: '1px solid rgba(88,166,255,0.15)', fontSize: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span>
            <span style={{ color: '#8b949e' }}>Best combo: </span>
            {activeRows.map((p, i) => {
              const opt = paramOptions.find((o: ParamOption) => o.path === p.path)
              const val = topResult.param_values[p.path]
              const formatted = opt?.isInteger ? String(Math.round(val)) : val?.toFixed(2)
              return (
                <span key={p.path}>
                  {i > 0 && <span style={{ color: '#484f58' }}> · </span>}
                  <span style={{ color: '#8b949e' }}>{opt?.label ?? p.path}: </span>
                  <span style={{ color: '#e6edf3', fontWeight: 600 }}>{formatted}</span>
                </span>
              )
            })}
            <span style={{ color: '#484f58' }}> → </span>
            <span style={{ color: '#8b949e' }}>Sharpe: </span>
            <span style={{ color: '#26a69a', fontWeight: 600 }}>{topResult.sharpe_ratio.toFixed(3)}</span>
            <span style={{ color: '#484f58' }}>, </span>
            <span style={{ color: '#8b949e' }}>Return: </span>
            <span style={{ color: topResult.total_return_pct >= 0 ? '#26a69a' : '#ef5350', fontWeight: 600 }}>
              {topResult.total_return_pct >= 0 ? '+' : ''}{topResult.total_return_pct.toFixed(2)}%
            </span>
          </span>
          {onApplyParams && (
            <span style={{ display: 'inline-flex', gap: 6, marginLeft: 4 }}>
              <button
                disabled={loading}
                onClick={() => handleApply(topResult)}
                style={{ ...s.applyBtn, opacity: loading ? 0.5 : 1 }}
              >
                Apply to rules
              </button>
              {onRunBacktest && (
                <button
                  disabled={loading}
                  onClick={() => handleApplyAndRun(topResult)}
                  style={{ ...s.applyBtn, opacity: loading ? 0.5 : 1 }}
                >
                  Apply &amp; Re-run
                </button>
              )}
            </span>
          )}
        </div>
      )}
    </div>
  )
}


const s: Record<string, React.CSSProperties> = {
  section: {
    padding: '8px 0', borderBottom: '1px solid #21262d',
  },
  row: {
    display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
  },
  label: { fontSize: 12, color: '#8b949e', whiteSpace: 'nowrap' },
  select: {
    fontSize: 12, padding: '3px 6px', borderRadius: 4,
    background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d',
    cursor: 'pointer',
  },
  input: {
    fontSize: 12, padding: '3px 6px', borderRadius: 4, width: 70,
    background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d',
  },
  runBtn: {
    fontSize: 12, padding: '5px 14px', borderRadius: 4, cursor: 'pointer',
    background: '#1e3a5f', color: '#e6edf3', border: '1px solid #1f6feb',
    fontWeight: 600,
  },
  addBtn: {
    fontSize: 12, padding: '3px 10px', borderRadius: 4, cursor: 'pointer',
    background: 'transparent', color: '#58a6ff', border: '1px solid #30363d',
  },
  removeBtn: {
    fontSize: 11, padding: '2px 6px', borderRadius: 4, cursor: 'pointer',
    background: 'transparent', color: '#8b949e', border: 'none',
  },
  applyBtn: {
    fontSize: 11, padding: '3px 8px', borderRadius: 4, cursor: 'pointer',
    background: '#1a3a2a', color: '#3fb950', border: '1px solid #238636',
    fontWeight: 600, whiteSpace: 'nowrap' as const,
  },
  th: {
    textAlign: 'left' as const, padding: '4px 8px',
    fontSize: 10, color: '#8b949e', textTransform: 'uppercase' as const,
    letterSpacing: '0.03em', whiteSpace: 'nowrap',
  },
  td: {
    padding: '4px 8px', fontSize: 12, color: '#e6edf3', whiteSpace: 'nowrap',
  },
}

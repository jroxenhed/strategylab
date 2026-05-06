import { useState, useMemo, useEffect } from 'react'
import type { StrategyRequest } from '../../shared/types'
import { api } from '../../api/client'
import { apiErrorDetail } from '../../shared/utils/errors'
import { buildParamOptions, linspace } from './paramOptions'

interface SweepPoint {
  param_value: number
  num_trades: number
  total_return_pct: number
  sharpe_ratio: number
  win_rate_pct: number
  max_drawdown_pct: number
  ev_per_trade: number | null
}

interface SweepResponse {
  results: SweepPoint[]
  requested: number
  completed: number
  skipped: number
}

function colorFor(value: number, min: number, max: number, highIsGood: boolean): string {
  if (max === min) return '#aaa'
  const t = (value - min) / (max - min)
  const pct = highIsGood ? t : 1 - t
  if (pct >= 0.7) return '#26a69a'
  if (pct >= 0.4) return '#aaa'
  return '#ef5350'
}

interface Props {
  lastRequest: StrategyRequest
  sweepInit?: { path: string; centerVal: number } | null
  onSweepConsumed?: () => void
}

export default function SensitivityPanel({ lastRequest, sweepInit, onSweepConsumed }: Props) {
  const paramOptions = useMemo(() => buildParamOptions(lastRequest), [lastRequest])

  const [selectedPath, setSelectedPath] = useState<string>(paramOptions[0]?.path ?? '')
  const selected = paramOptions.find(o => o.path === selectedPath)

  const [minVal, setMinVal] = useState<string>('')
  const [maxVal, setMaxVal] = useState<string>('')
  const [steps, setSteps] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [warning, setWarning] = useState('')
  const [results, setResults] = useState<SweepPoint[] | null>(null)
  const [sweptPath, setSweptPath] = useState<string>('')

  // Reset selectedPath when paramOptions changes and current selection is no longer valid
  useEffect(() => {
    if (paramOptions.length > 0 && !paramOptions.find(o => o.path === selectedPath)) {
      setSelectedPath(paramOptions[0].path)
    }
  }, [paramOptions])

  // Apply sweep init from rule row shortcut
  useEffect(() => {
    if (!sweepInit) return
    const opt = paramOptions.find(o => o.path === sweepInit.path)
    if (!opt) {
      // Path no longer exists (rule deleted) — clear stale init and bail
      onSweepConsumed?.()
      return
    }
    const center = sweepInit.centerVal
    const half = center === 0 ? 1 : Math.abs(center) * 0.5
    setSelectedPath(sweepInit.path)
    setMinVal((center - half).toFixed(2))
    setMaxVal((center + half).toFixed(2))
    setSteps('9')
    setError('')
    onSweepConsumed?.()
  }, [sweepInit])

  // Reset inputs when param selection changes
  function handleParamChange(path: string) {
    setSelectedPath(path)
    setMinVal('')
    setMaxVal('')
    setSteps('')
    setResults(null)
    setError('')
  }

  async function runSweep() {
    if (!selected) return
    const minN = minVal !== '' ? parseFloat(minVal) : selected.defaultMin
    const maxN = maxVal !== '' ? parseFloat(maxVal) : selected.defaultMax
    const stepsN = steps !== '' ? parseInt(steps, 10) : selected.defaultSteps
    if (isNaN(minN) || isNaN(maxN) || isNaN(stepsN) || stepsN < 2 || stepsN > 25) {
      setError('Steps must be 2–25 and min/max must be valid numbers.')
      return
    }
    if (minN >= maxN) {
      setError('Min must be less than max.')
      return
    }
    const rawValues = linspace(minN, maxN, stepsN)
    const values = selected.isInteger ? rawValues.map(v => Math.round(v)) : rawValues
    setLoading(true)
    setError('')
    setWarning('')
    setResults(null)
    try {
      const res = await api.post<SweepResponse>('/api/backtest/sweep', {
        base: lastRequest,
        param_path: selected.path,
        values,
      })
      const data = res.data
      setResults(data.results)
      setSweptPath(selected.path)
      if (data.skipped > 0) {
        setWarning(`${data.completed} of ${data.requested} sweep points completed — ${data.skipped} failed (e.g. invalid parameter value).`)
      }
    } catch (e: any) {
      setError(apiErrorDetail(e, 'Sweep failed.'))
    } finally {
      setLoading(false)
    }
  }

  if (paramOptions.length === 0) {
    return (
      <div style={{ color: '#666', padding: 16, fontSize: 13 }}>
        No sweep-able parameters in this strategy. Add a numeric threshold rule (e.g., RSI below 30) or a stop-loss to enable the sweep.
      </div>
    )
  }

  const returns = results?.map(r => r.total_return_pct) ?? []
  const sharpes = results?.map(r => r.sharpe_ratio) ?? []
  const winRates = results?.map(r => r.win_rate_pct) ?? []
  const dds = results?.map(r => r.max_drawdown_pct) ?? []

  const displayMin = minVal !== '' ? parseFloat(minVal) : selected?.defaultMin ?? 0
  const displayMax = maxVal !== '' ? parseFloat(maxVal) : selected?.defaultMax ?? 10
  const displaySteps = steps !== '' ? parseInt(steps, 10) : selected?.defaultSteps ?? 9

  return (
    <div style={{ padding: '0 0 12px', fontSize: 12 }}>
      <div style={{ color: '#8b949e', marginBottom: 10, fontSize: 12 }}>
        Re-runs the backtest N times varying one parameter. Reveals whether the edge is robust or over-fit to a specific value.
      </div>

      {/* Controls */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'flex-end', marginBottom: 12 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <label style={{ color: '#666', fontSize: 11 }}>Parameter</label>
          <select
            value={selectedPath}
            onChange={e => handleParamChange(e.target.value)}
            style={{ background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d', borderRadius: 3, padding: '3px 6px', fontSize: 12 }}
          >
            {paramOptions.map(o => (
              <option key={o.path} value={o.path}>{o.label} {o.currentValue != null ? `(now: ${o.currentValue})` : ''}</option>
            ))}
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <label style={{ color: '#666', fontSize: 11 }}>Min</label>
          <input
            type="number"
            value={minVal}
            placeholder={String(+(displayMin).toFixed(2))}
            onChange={e => setMinVal(e.target.value)}
            style={{ width: 72, background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d', borderRadius: 3, padding: '3px 6px', fontSize: 12 }}
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <label style={{ color: '#666', fontSize: 11 }}>Max</label>
          <input
            type="number"
            value={maxVal}
            placeholder={String(+(displayMax).toFixed(2))}
            onChange={e => setMaxVal(e.target.value)}
            style={{ width: 72, background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d', borderRadius: 3, padding: '3px 6px', fontSize: 12 }}
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <label style={{ color: '#666', fontSize: 11 }}>Steps</label>
          <input
            type="number"
            min={2}
            max={25}
            value={steps}
            placeholder={String(displaySteps)}
            onChange={e => setSteps(e.target.value)}
            style={{ width: 56, background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d', borderRadius: 3, padding: '3px 6px', fontSize: 12 }}
          />
        </div>

        <button
          onClick={runSweep}
          disabled={loading}
          style={{
            padding: '5px 14px', fontSize: 12, fontWeight: 600, borderRadius: 3, border: 'none', cursor: loading ? 'not-allowed' : 'pointer',
            background: loading ? '#1e2530' : '#1e3a5f', color: loading ? '#555' : '#58a6ff',
          }}
        >
          {loading ? 'Running…' : 'Run Sweep'}
        </button>
      </div>

      {error && (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, padding: '8px 10px', marginBottom: 8, background: 'rgba(239,83,80,0.1)', border: '1px solid rgba(239,83,80,0.3)', borderRadius: 4, color: '#ef5350', fontSize: 12 }}>
          <span style={{ flexShrink: 0, fontWeight: 700 }}>✕</span>
          <span>{error}</span>
        </div>
      )}

      {warning && (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, padding: '8px 10px', marginBottom: 8, background: 'rgba(255,193,7,0.08)', border: '1px solid rgba(255,193,7,0.3)', borderRadius: 4, color: '#d4a017', fontSize: 12 }}>
          <span style={{ flexShrink: 0, fontWeight: 700 }}>⚠</span>
          <span>{warning}</span>
        </div>
      )}

      {/* Sensitivity sparkline — return% vs param value */}
      {results && results.length > 1 && (() => {
        const W = 480, H = 64, PAD = 8
        const xs = results.map(r => r.param_value)
        const ys = results.map(r => r.total_return_pct)
        const xMin = Math.min(...xs), xMax = Math.max(...xs)
        const yMin = Math.min(...ys), yMax = Math.max(...ys)
        const xRange = xMax - xMin || 1
        const yRange = yMax - yMin || 1
        const toX = (v: number) => PAD + (v - xMin) / xRange * (W - 2 * PAD)
        const toY = (v: number) => H - PAD - (v - yMin) / yRange * (H - 2 * PAD)
        const pts = results.map(r => `${toX(r.param_value).toFixed(1)},${toY(r.total_return_pct).toFixed(1)}`).join(' ')
        const zeroY = yMin <= 0 && yMax >= 0 ? toY(0) : null
        return (
          <div style={{ marginBottom: 10, background: 'rgba(255,255,255,0.02)', borderRadius: 3, padding: '4px 0' }}>
            <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: H, display: 'block' }}>
              {zeroY !== null && (
                <line x1={PAD} y1={zeroY} x2={W - PAD} y2={zeroY}
                  stroke="#333" strokeWidth={1} strokeDasharray="3,3" />
              )}
              <polyline points={pts} fill="none" stroke="#58a6ff" strokeWidth={1.5} />
              {results.map((r, i) => (
                <circle key={i}
                  cx={toX(r.param_value)} cy={toY(r.total_return_pct)} r={2.5}
                  fill={r.total_return_pct >= 0 ? '#26a69a' : '#ef5350'}
                />
              ))}
            </svg>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0 8px', color: '#555', fontSize: 10 }}>
              <span>{xMin.toFixed(2)}</span>
              <span style={{ color: '#666' }}>Return% vs {paramOptions.find(o => o.path === sweptPath)?.label ?? sweptPath}</span>
              <span>{xMax.toFixed(2)}</span>
            </div>
          </div>
        )
      })()}

      {/* Results table */}
      {results && results.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
            <thead>
              <tr style={{ color: '#666', borderBottom: '1px solid #1e2530' }}>
                <th style={{ textAlign: 'left', padding: '4px 8px', fontWeight: 500 }}>
                  {paramOptions.find(o => o.path === sweptPath)?.label ?? sweptPath}
                </th>
                <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 500 }}>Trades</th>
                <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 500 }}>Return%</th>
                <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 500 }}>Sharpe</th>
                <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 500 }}>Win%</th>
                <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 500 }}>Max DD%</th>
                <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 500 }}>EV/Trade</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r, i) => (
                <tr
                  key={i}
                  style={{
                    borderBottom: '1px solid #0d1117',
                    background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)',
                  }}
                >
                  <td style={{ padding: '3px 8px', color: '#aaa', fontWeight: 500 }}>{r.param_value.toFixed(3)}</td>
                  <td style={{ padding: '3px 8px', textAlign: 'right', color: '#8b949e' }}>{r.num_trades}</td>
                  <td style={{ padding: '3px 8px', textAlign: 'right', color: colorFor(r.total_return_pct, Math.min(...returns), Math.max(...returns), true) }}>
                    {r.total_return_pct >= 0 ? '+' : ''}{r.total_return_pct.toFixed(1)}%
                  </td>
                  <td style={{ padding: '3px 8px', textAlign: 'right', color: colorFor(r.sharpe_ratio, Math.min(...sharpes), Math.max(...sharpes), true) }}>
                    {r.sharpe_ratio.toFixed(2)}
                  </td>
                  <td style={{ padding: '3px 8px', textAlign: 'right', color: colorFor(r.win_rate_pct, Math.min(...winRates), Math.max(...winRates), true) }}>
                    {r.win_rate_pct.toFixed(1)}%
                  </td>
                  <td style={{ padding: '3px 8px', textAlign: 'right', color: colorFor(r.max_drawdown_pct, Math.min(...dds), Math.max(...dds), true) }}>
                    {r.max_drawdown_pct.toFixed(1)}%
                  </td>
                  <td style={{ padding: '3px 8px', textAlign: 'right', color: r.ev_per_trade == null ? '#555' : r.ev_per_trade >= 0 ? '#26a69a' : '#ef5350' }}>
                    {r.ev_per_trade == null ? '—' : `${r.ev_per_trade >= 0 ? '+' : ''}$${r.ev_per_trade.toFixed(2)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

import { useState, useMemo, useEffect, useRef } from 'react'
import { createChart, LineSeries, ColorType } from 'lightweight-charts'
import type { IChartApi, UTCTimestamp, LineData, Time } from 'lightweight-charts'
import type { StrategyRequest } from '../../shared/types'
import { useRequestTimer } from '../../shared/hooks/useRequestTimer'
import { apiErrorDetail } from '../../shared/utils/errors'
import { api } from '../../api/client'
import { buildParamOptions, linspace } from './paramOptions'
import type { ParamOption } from './paramOptions'
import { parseSseFrame } from './sseParser'

// ---------------------------------------------------------------------------
// Inline types — match backend Pydantic models verbatim
// ---------------------------------------------------------------------------

interface WindowResult {
  window_index: number
  is_start: string
  is_end: string
  oos_start: string
  oos_end: string
  best_params: Record<string, number>
  is_sharpe: number
  is_metrics: Record<string, number>
  oos_metrics: Record<string, number>
  stability_tag: StabilityTag
  is_combo_count: number
  scale_factor: number
}

type StabilityTag = 'stable_plateau' | 'spike' | 'low_trades_is' | 'no_oos_trades' | 'no_is_trades'

interface WalkForwardResponse {
  windows: WindowResult[]
  stitched_equity: Array<{ time: string | number; value: number }>
  wfe: number | null
  param_cv: Record<string, number>
  total_combos: number
  total_oos_trades: number
  low_trades_is_count: number
  low_windows_warn: boolean
  timed_out: boolean
}

// ---------------------------------------------------------------------------
// SSE discriminated union (KT-1)
// ---------------------------------------------------------------------------

type SseEvent =
  | { type: 'started'; total: number }
  | { type: 'progress'; completed: number; total: number }
  | ({ type: 'result' } & WalkForwardResponse)
  | { type: 'error'; detail: string; status: number }

// ---------------------------------------------------------------------------
// Local types
// ---------------------------------------------------------------------------

interface ParamRow {
  path: string
  min: string
  max: string
  steps: string
}

const METRICS = [
  { value: 'sharpe_ratio', label: 'Sharpe Ratio' },
  { value: 'total_return_pct', label: 'Total Return %' },
  { value: 'win_rate_pct', label: 'Win Rate %' },
]

const NONE_PATH = ''

interface Props {
  lastRequest: StrategyRequest
}

// ---------------------------------------------------------------------------
// Fix 1: Interval-aware defaults
// Bars per US-equities trading day at each interval.
// US RTH 9:30–16:00 = 390 minutes.
// ---------------------------------------------------------------------------

const BARS_PER_TRADING_DAY: Record<string, number> = {
  '1m': 390, '2m': 195, '5m': 78, '15m': 26, '30m': 13,
  '60m': 7, '1h': 7, '90m': 5,
  '1d': 1, '1wk': 0.2, '1mo': 1 / 22,
}

// Default IS / OOS bars per interval. Targets ~4 weeks IS / 1 week OOS for intraday
// (4:1 Pardo ratio), 1 year / 3 months for daily, equivalents for weekly/monthly.
// Tight ranges for 1m/2m to fit within Yahoo's 7-day cap.
const DEFAULT_WINDOW_BARS: Record<string, { is: number; oos: number }> = {
  '1m':  { is: 780,  oos: 195 },   // 2 days IS / 0.5 day OOS (yfinance 7-day cap)
  '2m':  { is: 780,  oos: 195 },
  '5m':  { is: 1560, oos: 390 },   // 4 weeks / 1 week
  '15m': { is: 520,  oos: 130 },
  '30m': { is: 260,  oos: 65 },
  '60m': { is: 140,  oos: 35 },
  '1h':  { is: 140,  oos: 35 },
  '90m': { is: 100,  oos: 25 },
  '1d':  { is: 252,  oos: 63 },    // 1 year / 3 months — Pardo standard
  '1wk': { is: 52,   oos: 13 },
  '1mo': { is: 24,   oos: 6 },
}

// Yahoo intraday day-limits (mirror of backend shared.py _INTERVAL_MAX_DAYS).
// Other providers (Alpaca SIP/IEX, IBKR) support years of intraday history — no clamp.
const YAHOO_INTRADAY_MAX_DAYS: Record<string, number> = {
  '1m': 7, '2m': 60, '5m': 60, '15m': 60, '30m': 60,
  '60m': 730, '90m': 60, '1h': 730,
}

// Mirror of backend `routes/walk_forward.py:_WFA_TIMEOUT_SECS`. Raised from 120 → 600
// after F162/F166/F175 (backend ~10× faster + live stream progress lets the user
// see what's happening and abort if needed; the old 120s was a leftover safety net).
const _WFA_TIMEOUT_SECS = 600

// Mirror of backend `_MAX_COMBOS_PER_WINDOW`. Hard ceiling for the IS grid combo count.
const _MAX_COMBOS_PER_WINDOW = 1000
// Soft warning threshold — above this, show amber instead of neutral.
const _COMBO_WARN_THRESHOLD = 500

// ---------------------------------------------------------------------------
// Fix 5: Stability tag short labels
// ---------------------------------------------------------------------------

const STABILITY_LABEL: Record<StabilityTag, string> = {
  stable_plateau: 'Plateau',
  spike:          'Spike',
  low_trades_is:  'Thin IS',
  no_oos_trades:  'No OOS',
  no_is_trades:   'Failed',
}

// ---------------------------------------------------------------------------
// Fix 3: Short param label helper
// ---------------------------------------------------------------------------

function shortParamLabel(path: string): string {
  // "buy_rule_0_params_period" -> "buy0.period"; "stop_loss_pct" -> "stop.pct".
  // Includes the numeric rule-index segment when present so two rules' params don't
  // collide on the same short label (e.g. buy_rule_0_*_period vs buy_rule_1_*_period).
  const parts = path.split('_')
  if (parts.length < 2) return path
  const digitIdx = parts.findIndex((p) => /^\d+$/.test(p))
  const prefix = digitIdx >= 0 ? `${parts[0]}${parts[digitIdx]}` : parts[0]
  return `${prefix}.${parts[parts.length - 1]}`
}

// Lookup for the per-interval IS/OOS defaults. Hoisted to module scope so the
// useEffect dep array is unambiguously complete (only depends on lastRequest).
function defaultBars(interval: string): { is: number; oos: number } {
  return DEFAULT_WINDOW_BARS[interval] ?? { is: 252, oos: 63 }
}

// ---------------------------------------------------------------------------
// Stability tag badge
// ---------------------------------------------------------------------------

function StabilityBadge({ tag }: { tag: StabilityTag }) {
  const config: Record<StabilityTag, { color: string; bg: string }> = {
    stable_plateau: { color: '#0d1117', bg: '#26a69a' },
    spike:          { color: '#0d1117', bg: '#f0883e' },
    low_trades_is:  { color: '#e6edf3', bg: '#484f58' },
    no_oos_trades:  { color: '#fff',    bg: '#ef5350' },
    no_is_trades:   { color: '#e6edf3', bg: '#21262d' },
  }
  const { color, bg } = config[tag]
  return (
    <span style={{
      fontSize: 10, fontWeight: 600, padding: '2px 6px', borderRadius: 3,
      background: bg, color, whiteSpace: 'nowrap',
    }}>
      {STABILITY_LABEL[tag]}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Stitched equity chart — self-contained, never shares the main Chart instance
// ---------------------------------------------------------------------------

function StitchedEquityChart({ data }: { data: Array<{ time: string | number; value: number }> }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!containerRef.current || data.length === 0) return

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth || 700,
      height: 180,
      layout: {
        background: { type: ColorType.Solid, color: '#0d1117' },
        textColor: '#8b949e',
      },
      grid: {
        vertLines: { color: '#21262d' },
        horzLines: { color: '#21262d' },
      },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: true },
    })

    chartRef.current = chart

    const series = chart.addSeries(LineSeries, {
      color: '#58a6ff',
      lineWidth: 1,
      priceScaleId: 'right',
    })
    // lightweight-charts v5: UTCTimestamp is a nominal-typed number; daily strings pass through.
    const typedData: LineData[] = data.map((pt) => ({
      time: (typeof pt.time === 'number' ? (pt.time as UTCTimestamp) : pt.time) as Time,
      value: pt.value,
    }))
    series.setData(typedData)
    chart.timeScale().fitContent()

    return () => {
      // Null the ref BEFORE chart.remove() — avoids teardown race (CLAUDE.md Key Bugs Fixed)
      chartRef.current = null
      try { chart.remove() } catch { /* ignore teardown errors */ }
    }
  }, [data])

  if (data.length === 0) return null

  return (
    <div>
      <div style={{ fontSize: 11, color: '#8b949e', marginBottom: 4 }}>Stitched OOS Equity</div>
      <div ref={containerRef} style={{ width: '100%', height: 180, borderRadius: 4, overflow: 'hidden' }} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function WalkForwardPanel({ lastRequest }: Props) {
  const paramOptions = useMemo(() => buildParamOptions(lastRequest, 5), [lastRequest])

  const emptyRow = (): ParamRow => ({ path: paramOptions[0]?.path ?? NONE_PATH, min: '', max: '', steps: '5' })

  const [isBarStr, setIsBarStr] = useState(() => String(defaultBars(lastRequest.interval).is))
  const [oosBarStr, setOosBarStr] = useState(() => String(defaultBars(lastRequest.interval).oos))
  const [gapBarStr, setGapBarStr] = useState('0')
  const [stepBarStr, setStepBarStr] = useState('')
  const [expandTrain, setExpandTrain] = useState(false)
  const [metric, setMetric] = useState('sharpe_ratio')
  const [paramRows, setParamRows] = useState<(ParamRow | null)[]>(() => [emptyRow(), null, null])
  const [loading, setLoading] = useState(false)
  const { elapsed: elapsedSec, final: finalSec } = useRequestTimer(loading)
  const [error, setError] = useState('')
  const [result, setResult] = useState<WalkForwardResponse | null>(null)
  const [progress, setProgress] = useState<{ completed: number; total: number } | null>(null)
  // KT-2: AbortController ref for cancelling the fetch on unmount or user cancel.
  const abortControllerRef = useRef<AbortController | null>(null)

  // Persist input config per (ticker, interval, source) so the user's WFA
  // setup sticks to a strategy across tab switches AND page reloads.
  const storageKey = `strategylab-wfa-config-${lastRequest.ticker}-${lastRequest.interval}-${lastRequest.source ?? 'yahoo'}`

  // Restore on storage-key change (ticker / interval / source change). Result is
  // NOT persisted (could be MB-scale) — just inputs. Param paths are validated
  // against the current paramOptions so a strategy rule edit doesn't restore
  // a now-invalid path.
  useEffect(() => {
    let saved: {
      isBarStr?: string; oosBarStr?: string; gapBarStr?: string; stepBarStr?: string
      expandTrain?: boolean; metric?: string; paramRows?: (ParamRow | null)[]
    } | null = null
    try {
      const raw = localStorage.getItem(storageKey)
      if (raw) saved = JSON.parse(raw)
    } catch { /* corrupt entry — fall through to defaults */ }

    if (saved) {
      if (typeof saved.isBarStr === 'string') setIsBarStr(saved.isBarStr)
      if (typeof saved.oosBarStr === 'string') setOosBarStr(saved.oosBarStr)
      if (typeof saved.gapBarStr === 'string') setGapBarStr(saved.gapBarStr)
      if (typeof saved.stepBarStr === 'string') setStepBarStr(saved.stepBarStr)
      if (typeof saved.expandTrain === 'boolean') setExpandTrain(saved.expandTrain)
      if (typeof saved.metric === 'string') setMetric(saved.metric)
      if (Array.isArray(saved.paramRows)) {
        // Drop rows whose path no longer exists in the current rule set.
        const validPaths = new Set(paramOptions.map((o) => o.path))
        const restored = saved.paramRows.map((r) =>
          r && validPaths.has(r.path) ? r : null
        )
        // Ensure at least 3 slots so the UI can render the +/− buttons
        while (restored.length < 3) restored.push(null)
        if (restored.filter(Boolean).length === 0) restored[0] = emptyRow()
        setParamRows(restored as (ParamRow | null)[])
      }
    } else {
      // No saved config for this strategy identity → use interval defaults
      const defs = defaultBars(lastRequest.interval)
      setIsBarStr(String(defs.is))
      setOosBarStr(String(defs.oos))
      setGapBarStr('0')
      setStepBarStr('')
      setExpandTrain(false)
      setMetric('sharpe_ratio')
      setParamRows([emptyRow(), null, null])
    }
    setResult(null)
    setError('')
    // paramOptions intentionally not in deps — it changes on every lastRequest
    // ref change which would over-fire. We rely on storageKey identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey])

  // Save config whenever any field changes
  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify({
        isBarStr, oosBarStr, gapBarStr, stepBarStr, expandTrain, metric, paramRows,
      }))
    } catch { /* quota exceeded — silently drop */ }
  }, [storageKey, isBarStr, oosBarStr, gapBarStr, stepBarStr, expandTrain, metric, paramRows])

  // KT-2: Abort in-flight stream on unmount.
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

  // ---------------------------------------------------------------------------
  // Fix 2: Pre-flight cost estimate
  // ---------------------------------------------------------------------------

  const preflightEstimate = useMemo(() => {
    const interval = lastRequest.interval
    const barsPerDay = BARS_PER_TRADING_DAY[interval] ?? 1
    // Yahoo clamps intraday history (7d for 1m, 60d for 5m/15m/30m/90m, 730d for 60m/1h).
    // Alpaca/IBKR have years of intraday history — no clamp.
    const isYahoo = (lastRequest.source ?? 'yahoo') === 'yahoo'
    const maxDays = isYahoo ? (YAHOO_INTRADAY_MAX_DAYS[interval] ?? Infinity) : Infinity

    // Estimate total bars from date range
    const startMs = Date.parse(lastRequest.start)
    const endMs = Date.parse(lastRequest.end)
    if (isNaN(startMs) || isNaN(endMs) || endMs <= startMs) return null

    const calendarDays = (endMs - startMs) / 86400000
    const clampedDays = maxDays < Infinity ? Math.min(calendarDays, maxDays) : calendarDays
    // US-business-day fraction ≈ 252/365
    const estimatedTotalBars = Math.floor(clampedDays * (252 / 365) * barsPerDay)

    const isBars = parseInt(isBarStr) || 0
    const oosBars = parseInt(oosBarStr) || 0
    const gapBars = parseInt(gapBarStr) || 0
    const stepBars = (stepBarStr !== '' ? parseInt(stepBarStr) : 0) || oosBars

    if (isBars <= 0 || oosBars <= 0 || stepBars <= 0) return null

    // Match backend cursor math in walk_forward.py: cursor starts at
    // is_bars + gap_bars + oos_bars - 1, advances by step, yielding +1.
    const usableBars = estimatedTotalBars - isBars - oosBars - gapBars
    const nWindows = usableBars < 0 ? 0 : Math.floor(usableBars / stepBars) + 1
    const nBacktests = nWindows * estimatedCombos + nWindows  // IS grid + OOS calls
    // Per-backtest serial cost. Calibrated against measured WFA runs after
    // F162 (indicator cache) + F169 (pre-sliced df) + F166 (window-level
    // ProcessPool):
    //   NVDA 5m 1y, isBars=3000, 5292 backtests, 8-core parallel → 84s wall
    //     → serial-equivalent 84 * 8 ≈ 672s / 5292 ≈ 127ms/backtest
    //     → per-bar cost ≈ 127ms / 3000 ≈ 4.2e-5 s/bar
    // Floor at 8ms covers OOS backtests and small grids.
    const serialSecsPerBacktest = Math.max(0.008, isBars * 5e-5)
    // F166 spins up a per-request pool sized to min(cpu_count, n_windows).
    // Below _MIN_WINDOWS_FOR_POOL (4) the serial path is used (matches
    // backend wfa_pool.py). Assume 8 cores as the typical case.
    const parallelism = nWindows >= 4 ? 8 : 1
    const fetchOverheadSecs = 1.5
    const tSecs = nBacktests * serialSecsPerBacktest / parallelism + fetchOverheadSecs

    // Frontend mirror of backend step >= oos_bars guard (walk_forward.py).
    // Surface the rejection condition before the user clicks Run.
    const stepInvalid = stepBarStr !== '' && parseInt(stepBarStr) > 0 && parseInt(stepBarStr) < oosBars

    // Sizing hint
    let sizingHint = ''
    if (isBars > 0 && barsPerDay > 0) {
      const tradingDays = isBars / barsPerDay
      if (tradingDays >= 5) {
        const weeks = tradingDays / 5
        if (weeks >= 52) {
          sizingHint = `IS=${isBars} spans ≈${Math.round(weeks / 52)} year${Math.round(weeks / 52) !== 1 ? 's' : ''}`
        } else {
          sizingHint = `IS=${isBars} spans ≈${Math.round(weeks)} week${Math.round(weeks) !== 1 ? 's' : ''}`
        }
      } else {
        sizingHint = `IS=${isBars} spans ≈${tradingDays < 1 ? '<1' : Math.round(tradingDays)} trading day${tradingDays >= 2 ? 's' : ''}`
      }
    }

    const barsLabel = barsPerDay >= 1
      ? `${barsPerDay} bar${barsPerDay !== 1 ? 's' : ''}/trading day`
      : `${Math.round(1 / barsPerDay)} trading days/bar`

    // Time formatting
    let timeStr: string
    if (tSecs < 60) {
      timeStr = `~${Math.ceil(tSecs)}s`
    } else {
      const mins = Math.floor(tSecs / 60)
      const secs = Math.round(tSecs % 60)
      timeStr = `~${mins}m ${secs}s`
    }

    let statusColor = '#8b949e'
    let statusSuffix = ''
    if (stepInvalid) {
      statusColor = '#ef5350'
      statusSuffix = ` — step_bars (${stepBarStr}) must be ≥ oos_bars (${oosBars})`
    } else if (nWindows < 2) {
      statusColor = '#ef5350'
      statusSuffix = ' — increase date range or reduce is_bars'
    } else if (tSecs > _WFA_TIMEOUT_SECS * 2) {
      statusColor = '#f0883e'
      statusSuffix = ' — may time out'
    }

    return {
      nWindows,
      nBacktests,
      estimatedSeconds: tSecs,
      timeStr,
      statusColor,
      statusSuffix,
      intervalLabel: `${interval}: ${barsLabel}`,
      sizingHint,
    }
  }, [lastRequest.interval, lastRequest.source, lastRequest.start, lastRequest.end, isBarStr, oosBarStr, gapBarStr, stepBarStr, estimatedCombos])

  // ---------------------------------------------------------------------------
  // Fix 4: Interpretation callout
  // ---------------------------------------------------------------------------

  const interpretationCallout = useMemo(() => {
    if (!result) return null

    const { wfe, windows, low_trades_is_count, timed_out } = result

    // 1. Most windows below IS trade minimum
    if (low_trades_is_count >= windows.length * 0.5 && windows.length > 0) {
      return {
        border: '#f0883e',
        title: 'IS windows too small to pick meaningful parameters.',
        body: `${low_trades_is_count} of ${windows.length} windows had fewer than min_trades_is IS trades. The optimizer was selecting parameters from coin-flip data. Increase is_bars to span at least 4–6 trading sessions of trade activity, or reduce min_trades_is if you accept thinner statistics.`,
      }
    }

    // 2. Timed out
    if (timed_out) {
      return {
        border: '#f0883e',
        title: 'Walk-forward timed out — showing partial results.',
        body: `Completed ${windows.length} windows in 120s. Reduce IS combos, lengthen oos_bars (fewer windows), or simplify your strategy rules to fit in the budget.`,
      }
    }

    // 3. WFE null
    if (wfe === null) {
      return {
        border: '#484f58',
        title: 'WFE undefined — no OOS trades.',
        body: 'No window produced an OOS trade. Check that your rules generate signals in the OOS date range, or extend oos_bars.',
      }
    }

    // 4. High WFE but every window is a Spike — suspicious combination.
    // Spike means the IS winner sat at an isolated peak with poor grid-neighbors.
    // All-spike + healthy WFE usually means one outlier OOS window is pulling the
    // numerator up; with few windows the math looks healthy but the underlying
    // parameter selection is fragile.
    const spikeCount = windows.filter((w) => w.stability_tag === 'spike').length
    if (wfe >= 0.7 && windows.length > 0 && spikeCount === windows.length) {
      return {
        border: '#f0883e',
        title: 'High WFE but every window is a "Spike".',
        body: `All ${windows.length} IS winners sat at isolated peaks in the parameter grid (no stable plateau). The healthy WFE is likely driven by a single outlier OOS window — try a finer parameter grid (more Steps, wider Min/Max) to see if a plateau emerges, and extend the date range to get 10+ windows across different regimes before trusting this result.`,
      }
    }

    // 5–7. WFE ranges
    if (wfe >= 0.7) {
      return {
        border: '#26a69a',
        title: 'Healthy walk-forward.',
        body: 'OOS performance tracks IS performance well across windows. Strategy parameters generalize beyond the optimization window.',
      }
    }
    if (wfe >= 0.5) {
      return {
        border: '#f0883e',
        title: 'Marginal — passing the practitioner threshold.',
        body: 'Strategy has some edge but expect ~40% performance decay from IS to OOS in live trading. Watch parameter stability (param CV below).',
      }
    }
    return {
      border: '#ef5350',
      title: 'Below practitioner threshold — strategy likely curve-fit.',
      body: 'OOS Sharpe averages less than half of IS Sharpe. The optimizer is finding noise patterns. Try a coarser parameter grid, longer windows, or simpler rules.',
    }
  }, [result])

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

  async function runWalkForward() {
    const isN = parseInt(isBarStr)
    const oosN = parseInt(oosBarStr)
    const gapN = parseInt(gapBarStr) || 0
    const stepN = stepBarStr === '' ? 0 : parseInt(stepBarStr)

    if (!isBarStr || isNaN(isN) || isN <= 0) {
      setError('IS bars must be a positive integer')
      return
    }
    if (!oosBarStr || isNaN(oosN) || oosN <= 0) {
      setError('OOS bars must be a positive integer')
      return
    }
    if (activeRows.length === 0 || loading) return
    if (estimatedCombos > _MAX_COMBOS_PER_WINDOW) return

    // Validate param ranges
    for (const p of activeRows) {
      const opt = paramOptions.find(o => o.path === p.path)
      if (!opt) continue
      const minN = p.min !== '' ? parseFloat(p.min) : opt.defaultMin
      const maxN = p.max !== '' ? parseFloat(p.max) : opt.defaultMax
      const stepsN = p.steps !== '' ? parseInt(p.steps) : NaN
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

    setLoading(true)
    setError('')
    setResult(null)
    setProgress(null)

    // KT-2: Fresh AbortController for this run. Aborted on Cancel or unmount.
    const controller = new AbortController()
    abortControllerRef.current = controller

    try {
      const requestParams = activeRows.map(p => {
        const opt = paramOptions.find(o => o.path === p.path)
        if (!opt) throw new Error(`Unknown param: ${p.path}`)
        const minN = p.min !== '' ? parseFloat(p.min) : opt.defaultMin
        const maxN = p.max !== '' ? parseFloat(p.max) : opt.defaultMax
        const stepsN = Math.max(1, parseInt(p.steps) || 5)
        let values = linspace(minN, maxN, stepsN)
        if (opt.isInteger) {
          values = [...new Set(values.map(v => Math.round(v)))]
        }
        return { path: p.path, values }
      })

      const payload = {
        base: lastRequest,
        params: requestParams,
        is_bars: isN,
        oos_bars: oosN,
        gap_bars: gapN,
        step_bars: stepN,
        expand_train: expandTrain,
        metric,
      }

      // Same baseURL as the axios client (default http://localhost:8000) — the
      // SSE endpoint can't go through axios so we re-use the resolved base.
      const apiBase = (api.defaults.baseURL ?? '').replace(/\/$/, '')
      const resp = await fetch(`${apiBase}/api/backtest/walk_forward/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,  // KT-2: wire abort signal
      })

      if (!resp.ok) {
        const text = await resp.text()
        let parsed: unknown = text
        try { parsed = JSON.parse(text) } catch { /* keep raw text */ }
        setError(apiErrorDetail({ response: { status: resp.status, data: parsed } }, `HTTP ${resp.status}: Walk-forward failed`))
        return
      }

      if (!resp.body) {
        setError('Streaming not supported by this browser')
        return
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''

      // KT-3: Release lock in finally so the body is always unlocked.
      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          // C4/KT-4: Process ALL \n\n-separated events per chunk, not just the first.
          let sepIdx: number
          while ((sepIdx = buf.indexOf('\n\n')) !== -1) {
            const raw = buf.slice(0, sepIdx)
            buf = buf.slice(sepIdx + 2)
            // Each event may have multiple `data:` lines; concatenate them.
            const dataLines = raw.split('\n').filter(l => l.startsWith('data: ')).map(l => l.slice(6))
            if (dataLines.length === 0) continue
            // KT-3: Guard against malformed events from the server.
            const evt = parseSseFrame(dataLines) as SseEvent | null
            if (evt === null) continue
            if (typeof (evt as { type?: unknown }).type !== 'string') {
              console.warn('[WFA] SSE event missing .type, skipping:', evt)
              continue
            }
            if (evt.type === 'started') {
              setProgress({ completed: 0, total: evt.total })
            } else if (evt.type === 'progress') {
              setProgress({ completed: evt.completed, total: evt.total })
            } else if (evt.type === 'result') {
              // KT-5: Snap progress bar to 100% when result lands.
              setProgress(prev => prev ? { completed: prev.total, total: prev.total } : prev)
              // Strip the discriminator before storing.
              const { type: _t, ...resultData } = evt
              setResult(resultData as WalkForwardResponse)
            } else if (evt.type === 'error') {
              setError(evt.detail || 'Walk-forward failed')
            }
          }
        }
        // C4/KT-4: Flush any partial bytes held by the decoder, then process
        // any remaining complete SSE event(s) in the buffer.
        buf += decoder.decode()
        let sepIdx: number
        while ((sepIdx = buf.indexOf('\n\n')) !== -1) {
          const raw = buf.slice(0, sepIdx)
          buf = buf.slice(sepIdx + 2)
          const dataLines = raw.split('\n').filter(l => l.startsWith('data: ')).map(l => l.slice(6))
          if (dataLines.length === 0) continue
          const evt = parseSseFrame(dataLines, 'SSE event in flush') as SseEvent | null
          if (evt === null) continue
          if (typeof (evt as { type?: unknown }).type !== 'string') {
            console.warn('[WFA] SSE event missing .type, skipping:', evt)
            continue
          }
          if (evt.type === 'result') {
            setProgress(prev => prev ? { completed: prev.total, total: prev.total } : prev)
            const { type: _t, ...resultData } = evt
            setResult(resultData as WalkForwardResponse)
          } else if (evt.type === 'error') {
            setError(evt.detail || 'Walk-forward failed')
          }
        }
      } finally {
        // KT-3: Always release the reader lock so the body can be GC'd.
        reader.releaseLock()
      }
    } catch (e) {
      // Ignore abort errors from Cancel / unmount — not a user-visible failure.
      if (e instanceof Error && e.name === 'AbortError') return
      setError(apiErrorDetail(e, 'Walk-forward failed'))
    } finally {
      setLoading(false)
      abortControllerRef.current = null
    }
  }

  function cancelWalkForward() {
    abortControllerRef.current?.abort()
    setLoading(false)
    setProgress(null)
  }

  const wfeBadgeColor = (wfe: number | null) => {
    if (wfe === null) return '#484f58'
    if (wfe >= 0.7) return '#26a69a'
    if (wfe >= 0.5) return '#f0883e'
    return '#ef5350'
  }

  return (
    <div style={{ width: '100%' }}>

      {/* ─── Window configuration ────────────────────────────────── */}
      <div style={s.section}>
        <div style={s.row}>
          <span style={s.label}>IS bars</span>
          <input
            type="number" min={1}
            value={isBarStr}
            onChange={e => setIsBarStr(e.target.value)}
            placeholder="e.g. 252"
            style={s.input}
          />
          <span style={s.label}>OOS bars</span>
          <input
            type="number" min={1}
            value={oosBarStr}
            onChange={e => setOosBarStr(e.target.value)}
            placeholder="e.g. 63"
            style={s.input}
          />
          <span style={s.label}>Gap bars</span>
          <input
            type="number" min={0}
            value={gapBarStr}
            onChange={e => setGapBarStr(e.target.value)}
            style={{ ...s.input, width: 50 }}
          />
          <span style={s.label}>Step bars</span>
          <input
            type="number" min={1}
            value={stepBarStr}
            onChange={e => setStepBarStr(e.target.value)}
            placeholder="= OOS"
            style={{ ...s.input, width: 70 }}
          />
        </div>
        <div style={{ ...s.row, marginTop: 6 }}>
          <label style={{ ...s.label, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={expandTrain}
              onChange={e => setExpandTrain(e.target.checked)}
            />
            Anchored (expanding IS)
          </label>
          <span style={{ ...s.label, marginLeft: 16 }}>Optimize for</span>
          <select value={metric} onChange={e => setMetric(e.target.value)} style={s.select}>
            {METRICS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </div>
      </div>

      {/* ─── Param rows ─────────────────────────────────────────── */}
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

      {/* ─── Pre-flight estimate / live elapsed / final time ────────── */}
      {preflightEstimate && (
        <div style={{ padding: '6px 0', fontSize: 12, color: loading ? '#58a6ff' : (finalSec !== null && !loading ? '#3fb950' : preflightEstimate.statusColor) }}>
          {loading ? (
            <>
              <span>
                Elapsed: {elapsedSec}s / ~{preflightEstimate.timeStr} estimated
                {progress
                  ? ` · ${progress.completed}/${progress.total} windows`
                  : ` · ${preflightEstimate.nBacktests} backtests`}
              </span>
              {/* Progress bar: real window-based when streaming data is available,
                  synthetic clock-based during the brief window before first event.
                  Cap at 99% so the user knows the run isn't done until result lands. */}
              <div
                style={{
                  marginTop: 4,
                  height: 4,
                  background: '#1e2530',
                  borderRadius: 2,
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    width: `${progress
                      ? Math.min(99, (progress.completed / Math.max(1, progress.total)) * 100)
                      : Math.min(99, (elapsedSec / Math.max(1, preflightEstimate.estimatedSeconds)) * 100)
                    }%`,
                    height: '100%',
                    background: '#58a6ff',
                    transition: 'width 250ms linear',
                  }}
                />
              </div>
            </>
          ) : finalSec !== null ? (
            <span>
              Completed in {finalSec}s · {preflightEstimate.nBacktests} backtests
              {preflightEstimate.estimatedSeconds > 0 && (
                <span style={{ marginLeft: 8, color: '#8b949e' }}>
                  ({finalSec < preflightEstimate.estimatedSeconds
                    ? `${Math.round((1 - finalSec / preflightEstimate.estimatedSeconds) * 100)}% under estimate`
                    : `${Math.round((finalSec / preflightEstimate.estimatedSeconds - 1) * 100)}% over estimate`})
                </span>
              )}
            </span>
          ) : (
            <>
              <span>
                Estimated: ~{preflightEstimate.nWindows} windows × {estimatedCombos} combo{estimatedCombos !== 1 ? 's' : ''}{' '}
                = ~{preflightEstimate.nBacktests} backtests ({preflightEstimate.timeStr}){preflightEstimate.statusSuffix}
              </span>
              {preflightEstimate.sizingHint && (
                <span style={{ marginLeft: 12, color: '#8b949e' }}>
                  {preflightEstimate.intervalLabel} — {preflightEstimate.sizingHint}
                </span>
              )}
            </>
          )}
        </div>
      )}

      {/* ─── Run button ─────────────────────────────────────────── */}
      <div style={{ ...s.section, ...s.row, gap: 12 }}>
        <button
          onClick={runWalkForward}
          disabled={loading || activeRows.length === 0 || estimatedCombos > _MAX_COMBOS_PER_WINDOW}
          style={{
            ...s.runBtn,
            opacity: loading || activeRows.length === 0 || estimatedCombos > _MAX_COMBOS_PER_WINDOW ? 0.6 : 1,
          }}
        >
          {loading ? `Running ${elapsedSec}s…` : 'Run Walk-Forward'}
        </button>
        {/* KT-2: Cancel button — visible only while loading; aborts the stream. */}
        {loading && (
          <button onClick={cancelWalkForward} style={s.cancelBtn}>
            Cancel
          </button>
        )}
        <span style={{
          fontSize: 12,
          color: estimatedCombos > _MAX_COMBOS_PER_WINDOW
            ? '#ef5350'
            : estimatedCombos > _COMBO_WARN_THRESHOLD
              ? '#f0883e'
              : '#8b949e',
        }}>
          {estimatedCombos} combination{estimatedCombos !== 1 ? 's' : ''} per IS window
          {estimatedCombos > _MAX_COMBOS_PER_WINDOW
            ? ` — reduce steps or params (max ${_MAX_COMBOS_PER_WINDOW})`
            : estimatedCombos > _COMBO_WARN_THRESHOLD
              ? ' — large grid, check the time estimate'
              : ''}
        </span>
      </div>

      {/* ─── Error ──────────────────────────────────────────────── */}
      {error && (
        <div style={{ color: '#ef5350', fontSize: 12, padding: '6px 0' }}>{error}</div>
      )}

      {/* ─── Results ────────────────────────────────────────────── */}
      {result && !loading && (
        <div style={{ marginTop: 12 }}>

          {/* Summary bar */}
          <div style={{ ...s.row, gap: 16, marginBottom: 12, flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={s.label}>WFE</span>
              <span style={{
                fontSize: 15, fontWeight: 700, padding: '2px 10px', borderRadius: 4,
                background: wfeBadgeColor(result.wfe),
                color: result.wfe !== null && result.wfe >= 0.5 ? '#0d1117' : '#fff',
              }}>
                {result.wfe !== null ? result.wfe.toFixed(3) : 'N/A'}
              </span>
            </div>
            <div style={s.statItem}>
              <span style={s.label}>OOS Trades</span>
              <span style={s.statVal}>{result.total_oos_trades}</span>
            </div>
            <div style={s.statItem}>
              <span style={s.label}>Windows</span>
              <span style={s.statVal}>{result.windows.length}</span>
            </div>
            <div style={s.statItem}>
              <span style={s.label}>IS Combos</span>
              <span style={s.statVal}>{result.total_combos}</span>
            </div>
            {result.low_trades_is_count > 0 && (
              <div style={s.statItem}>
                <span style={{ ...s.label, color: '#f0883e' }}>
                  {result.low_trades_is_count} window{result.low_trades_is_count !== 1 ? 's' : ''} below IS trade minimum
                </span>
              </div>
            )}
          </div>

          {/* Fix 4: Interpretation callout */}
          {interpretationCallout && (
            <div style={{
              background: '#1f2937',
              padding: 12,
              borderRadius: 6,
              fontSize: 13,
              borderLeft: `4px solid ${interpretationCallout.border}`,
              marginBottom: 12,
            }}>
              <div style={{ fontWeight: 600, color: '#e6edf3', marginBottom: 4 }}>
                {interpretationCallout.title}
              </div>
              <div style={{ color: '#8b949e', lineHeight: 1.5 }}>
                {interpretationCallout.body}
              </div>
            </div>
          )}

          {/* Low windows warning */}
          {result.low_windows_warn && (
            <div style={{ color: '#f0883e', fontSize: 11, padding: '4px 8px', marginBottom: 8, background: 'rgba(240,136,62,0.07)', borderRadius: 4 }}>
              Only {result.windows.length} windows — results are statistically thin (≥ 6 recommended)
            </div>
          )}

          {/* Timeout warning */}
          {result.timed_out && (
            <div style={{ color: '#f0883e', fontSize: 11, padding: '4px 8px', marginBottom: 8 }}>
              Walk-forward timed out after 120s — showing partial results ({result.windows.length} window{result.windows.length !== 1 ? 's' : ''} complete)
            </div>
          )}

          {/* Fix 3: Per-window table — horizontally scrollable */}
          <div style={{ overflowX: 'auto', maxWidth: '100%', marginBottom: 16 }}>
            <table style={{ ...s.table, minWidth: 'fit-content' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #30363d' }}>
                  <th style={s.th}>#</th>
                  {/* Fix 5: Verdict column is second */}
                  <th style={s.th}>Verdict</th>
                  <th style={s.th}>IS Period</th>
                  <th style={s.th}>OOS Period</th>
                  <th style={s.th}>Best Params</th>
                  <th style={s.th}>IS Sharpe</th>
                  <th style={s.th}>OOS Sharpe</th>
                  <th style={s.th}>OOS Return %</th>
                  <th style={s.th}>OOS Trades</th>
                </tr>
              </thead>
              <tbody>
                {result.windows.map(w => (
                  <tr key={w.window_index} style={{ borderBottom: '1px solid #161b22' }}>
                    <td style={{ ...s.td, color: '#8b949e' }}>{w.window_index + 1}</td>
                    {/* Fix 5: Verdict badge in second column */}
                    <td style={s.td}><StabilityBadge tag={w.stability_tag} /></td>
                    <td style={s.td}>{w.is_start} – {w.is_end}</td>
                    <td style={s.td}>{w.oos_start} – {w.oos_end}</td>
                    {/* Fix 3: Short param labels with full path on hover */}
                    <td
                      style={{ ...s.td, fontFamily: 'monospace', fontSize: 10 }}
                      title={Object.entries(w.best_params).map(([k, v]) => `${k}=${v}`).join(', ')}
                    >
                      {Object.entries(w.best_params).map(([k, v]) =>
                        `${shortParamLabel(k)}=${v}`
                      ).join(', ') || '—'}
                    </td>
                    <td style={{ ...s.td, color: w.is_sharpe >= 1 ? '#26a69a' : '#e6edf3' }}>
                      {w.is_sharpe.toFixed(3)}
                    </td>
                    <td style={{ ...s.td, color: (w.oos_metrics.sharpe_ratio ?? 0) >= 1 ? '#26a69a' : '#e6edf3' }}>
                      {w.oos_metrics.sharpe_ratio != null ? w.oos_metrics.sharpe_ratio.toFixed(3) : '—'}
                    </td>
                    <td style={{ ...s.td, color: (w.oos_metrics.total_return_pct ?? 0) >= 0 ? '#26a69a' : '#ef5350' }}>
                      {w.oos_metrics.total_return_pct != null
                        ? `${w.oos_metrics.total_return_pct >= 0 ? '+' : ''}${w.oos_metrics.total_return_pct.toFixed(2)}%`
                        : '—'}
                    </td>
                    <td style={s.td}>{w.oos_metrics.num_trades ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Stitched equity chart */}
          {result.stitched_equity.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <StitchedEquityChart data={result.stitched_equity} />
            </div>
          )}

          {/* Param CV table */}
          {Object.keys(result.param_cv).length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, color: '#8b949e', marginBottom: 4 }}>Parameter CV (std / mean across windows)</div>
              <table style={{ borderCollapse: 'collapse', fontSize: 11 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #30363d' }}>
                    <th style={s.th}>Parameter</th>
                    <th style={s.th}>CV</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(result.param_cv).map(([path, cv]) => (
                    <tr key={path} style={{ borderBottom: '1px solid #161b22' }}>
                      <td style={{ ...s.td, fontFamily: 'monospace' }}>{path}</td>
                      <td style={{ ...s.td, color: '#e6edf3' }}>{cv.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Styles — same s.* convention as OptimizerPanel
// ---------------------------------------------------------------------------

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
  cancelBtn: {
    fontSize: 12, padding: '5px 14px', borderRadius: 4, cursor: 'pointer',
    background: 'transparent', color: '#8b949e', border: '1px solid #30363d',
  },
  table: {
    borderCollapse: 'collapse', fontSize: 11, width: '100%',
  },
  th: {
    textAlign: 'left' as const, padding: '4px 8px',
    fontSize: 10, color: '#8b949e', textTransform: 'uppercase' as const,
    letterSpacing: '0.03em', whiteSpace: 'nowrap',
  },
  td: {
    padding: '4px 8px', fontSize: 11, color: '#e6edf3', whiteSpace: 'nowrap',
  },
  statItem: {
    display: 'flex', alignItems: 'center', gap: 4,
  },
  statVal: {
    fontSize: 13, color: '#e6edf3', fontWeight: 600,
  },
}

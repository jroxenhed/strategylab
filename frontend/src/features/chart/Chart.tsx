import { useEffect, useRef, useMemo, useState, useCallback } from 'react'
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  ColorType,
  LineType,
} from 'lightweight-charts'
import type { IChartApi, ISeriesApi, IRange, Time } from 'lightweight-charts'
import type { OHLCVBar, IndicatorInstance, EMAOverlay, Trade, RuleSignal } from '../../shared/types'
import { INDICATOR_DEFS } from '../../shared/types/indicators'
import { Group, Panel, Separator, useDefaultLayout } from 'react-resizable-panels'
import SubPane from './SubPane'
import type { PaneRegistry } from './SubPane'
import { toLineData, aggregateMarkers, snapTimestamp, aggregateBars, aggregateLineSeries } from './chartUtils'
import TradeTooltip from './TradeTooltip'
import { getTimezone, useTimezone } from '../../shared/utils/time'
import { calcVisibleBaseBars, evaluateAutoInterval, INTERVAL_SECS } from '../../shared/utils/intervals'

interface ChartProps {
  data: OHLCVBar[]
  spyData?: OHLCVBar[]
  qqqData?: OHLCVBar[]
  showSpy: boolean
  showQqq: boolean
  indicators: IndicatorInstance[]
  instanceData: Record<string, Record<string, { time: string; value: number | null }[]>>
  instanceLoading?: boolean
  loadingByInstance?: Record<string, boolean>
  instanceError?: boolean
  instanceErrorMessage?: string | null
  onRetryIndicators?: () => void
  trades?: Trade[]
  emaOverlays?: EMAOverlay[]
  ruleSignals?: RuleSignal[]
  regimeSeries?: Array<{ time: string | number; direction: string }>
  viewInterval: string
  backtestInterval: string
  onChartReady?: (chart: IChartApi | null) => void
  /** Master enable for auto-downsampling (user checkbox). Default true; when false
   *  the zoom-span evaluation never fires. Setting null on the render interval is
   *  handled internally; the parent just gates the feature. */
  autoIntervalEnabled?: boolean
  /** Called when the render-layer auto interval changes (null = full resolution).
   *  App.tsx stores this purely for the header badge. */
  onAutoRenderChange?: (iv: string | null) => void
  /** Used to detect ticker/interval/date changes so fitContent fires on symbol switches but not on auto-refresh polls. */
  ticker?: string
  interval?: string
  from?: string
  to?: string
}

declare global {
  interface Window {
    __chartDebug?: {
      lastSetDataPoints: number
      setVisibleLogicalRange?: (from: number, to: number) => void
      getRanges?: () => { logical: unknown; time: unknown } | null
    }
  }
}

const CHART_BG = '#0d1117'
const GRID = '#1c2128'
const TEXT = '#8b949e'
const UP = '#26a641'
const DOWN = '#f85149'

// Distinct from trade green/red; indexed by rule_index mod length
const RULE_SIGNAL_COLORS = ['#58a6ff', '#d2a8ff', '#f0883e', '#56d364', '#e5534b', '#768390', '#f778ba', '#a5d6ff']

const chartOptionsBase = {
  autoSize: true,
  layout: { background: { type: ColorType.Solid, color: CHART_BG }, textColor: TEXT },
  grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
  crosshair: { mode: 1 as const },
  rightPriceScale: { borderColor: GRID },
  leftPriceScale: { visible: false, borderColor: GRID },
}

// lightweight-charts v5 has no localization.timeZone support.
// Shift unix timestamps to the target timezone's wall-clock time by
// reconstructing them as UTC so the chart displays e.g. 9:30 for NYSE open.
// Date strings (daily+) pass through unchanged.
// The target timezone is controlled by the global TzMode toggle in time.ts.
const _fmtCache = new Map<string, Intl.DateTimeFormat>()
function _getFormatter(tzName: string): Intl.DateTimeFormat {
  let f = _fmtCache.get(tzName)
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', {
      timeZone: tzName,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false,
    })
    _fmtCache.set(tzName, f)
  }
  return f
}
const _localTz = Intl.DateTimeFormat().resolvedOptions().timeZone
function toET(time: string | number): any {
  if (typeof time !== 'number') return time
  const tzName = getTimezone() === 'ET' ? 'America/New_York' : _localTz
  const parts = _getFormatter(tzName).formatToParts(new Date(time * 1000))
  const get = (type: string) => parseInt(parts.find(p => p.type === type)?.value ?? '0')
  return Date.UTC(get('year'), get('month') - 1, get('day'), get('hour') % 24, get('minute'), get('second')) / 1000
}

function normalizeTime(t: any): string | number {
  if (typeof t === 'object' && t !== null && 'year' in t)
    return `${t.year}-${String(t.month).padStart(2, '0')}-${String(t.day).padStart(2, '0')}`
  return t
}

function buildMarkers(trades: Trade[], subPane = false) {
  return trades.map(t => {
    const isEntry = t.type === 'buy' || t.type === 'short'
    const isShortEntry = t.type === 'short'
    const isCover = t.type === 'cover'
    if (isEntry) {
      return {
        time: toET(t.date as any) as any,
        position: subPane ? 'inBar' as const : (isShortEntry ? 'aboveBar' as const : 'belowBar' as const),
        color: '#e5c07b',
        shape: subPane ? 'circle' as const : (isShortEntry ? 'arrowDown' as const : 'arrowUp' as const),
        text: isShortEntry ? 'SH' : 'B',
      }
    }
    const win = (t.pnl ?? 0) >= 0
    const color = win ? UP : DOWN
    return {
      time: toET(t.date as any) as any,
      position: subPane ? 'inBar' as const : (isCover ? 'belowBar' as const : 'aboveBar' as const),
      color,
      shape: subPane ? 'circle' as const : (isCover ? 'arrowUp' as const : 'arrowDown' as const),
      text: t.stop_loss ? 'SL' : t.trailing_stop ? 'TSL' : (isCover ? 'COV' : 'S'),
    }
  })
}

export default function Chart({ data, spyData, qqqData, showSpy, showQqq, indicators, instanceData, instanceLoading, loadingByInstance, instanceError, instanceErrorMessage, onRetryIndicators, trades, emaOverlays, ruleSignals, regimeSeries, viewInterval, backtestInterval, onChartReady, autoIntervalEnabled, onAutoRenderChange, ticker, interval, from, to }: ChartProps) {
  const [tzMode] = useTimezone()
  /** Render-layer auto interval: null = render at data resolution (viewInterval).
   *  Set internally by zoom-span evaluation; cleared on checkbox disable. */
  const [autoRenderInterval, setAutoRenderInterval] = useState<string | null>(null)
  /** Effective interval for all aggregation-aware paths: marker snapping, regime snapping,
   *  SNAP tolerance, signal dedup. Auto-render overlays on top of the manual viewInterval. */
  const effectiveInterval = autoRenderInterval ?? viewInterval
  const isAggregated = effectiveInterval !== backtestInterval
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<any> | null>(null)
  const syncWidthsRef = useRef<() => void>(() => {})
  const rangeRestoredRef = useRef(false)
  /** Tracks the last (ticker, interval, from, to) tuple that triggered fitContent.
   *  fitContent fires only when this tuple changes — not on auto-refresh polls. */
  const lastFitParamsRef = useRef<string | null>(null)
  const onChartReadyRef = useRef(onChartReady)
  useEffect(() => { onChartReadyRef.current = onChartReady })

  // Stable refs for auto-downsampling — kept in sync each render so the
  // syncHandler closure (created once at chart-mount) always reads current values
  // without needing to be re-subscribed (which would tear down and recreate the chart).
  /** Stable ref to current viewInterval (manual aggregate or base). */
  const viewIntervalRef = useRef(viewInterval)
  useEffect(() => { viewIntervalRef.current = viewInterval })
  const baseIntervalRef = useRef(backtestInterval)
  useEffect(() => { baseIntervalRef.current = backtestInterval })
  const autoIntervalEnabledRef = useRef(autoIntervalEnabled)
  useEffect(() => { autoIntervalEnabledRef.current = autoIntervalEnabled })
  /** Stable ref to setAutoRenderInterval so syncHandler closure (created once) can call it. */
  const setAutoRenderIntervalRef = useRef(setAutoRenderInterval)
  useEffect(() => { setAutoRenderIntervalRef.current = setAutoRenderInterval })
  /** Stable ref to current autoRenderInterval value for the syncHandler closure. */
  const autoRenderIntervalRef = useRef(autoRenderInterval)
  useEffect(() => { autoRenderIntervalRef.current = autoRenderInterval })
  /** Set by the chart-creation effect: runs the auto-downsample evaluation against
   *  the current visible range. Needed because lw-charts skips no-change range
   *  assignments, so re-enabling the checkbox can't be nudged via a fake event. */
  const autoEvalRef = useRef<(() => void) | null>(null)
  // When the user re-enables auto-downsampling while already zoomed out, run the
  // evaluation immediately instead of waiting for the next pan/zoom.
  // When disabled, clear the render-layer aggregate immediately — capturing the
  // visible time window first so the full-resolution swap keeps the user's view
  // (without this, the coarse logical range reinterprets over ~12x more bars and
  // the view collapses to a small sub-window). Capture ONLY when auto is active:
  // if no data swap follows, nothing consumes autoRangeRef and a stale non-null
  // value would pause the evaluation loop permanently (the in-flight guard).
  useEffect(() => {
    if (autoIntervalEnabled === false) {
      if (autoRenderIntervalRef.current !== null) {
        try {
          const vr = chartRef.current?.timeScale().getVisibleRange()
          if (vr) autoRangeRef.current = vr as IRange<Time>
        } catch { /* chart torn down */ }
      }
      setAutoRenderInterval(null)
      return
    }
    autoEvalRef.current?.()
  }, [autoIntervalEnabled])
  /** Captured getVisibleRange() just before a render-layer interval switch; restored after
   *  new render data lands (takes priority over sessionStorage range restore). */
  const autoRangeRef = useRef<IRange<Time> | null>(null)
  const mainOverlaySeriesRef = useRef<Map<string, ISeriesApi<any>> | null>(null)
  const regimeBgSeriesRef = useRef<ISeriesApi<any> | null>(null)
  const paneRegistryRef = useRef<PaneRegistry>(new Map())

  // SPY/QQQ as real close prices on their own left axis
  const spyLineData = useMemo(() => {
    if (!spyData || spyData.length === 0) return []
    return spyData.map(d => ({ time: toET(d.time as any) as any, value: d.close }))
  }, [spyData, tzMode])

  const qqqLineData = useMemo(() => {
    if (!qqqData || qqqData.length === 0) return []
    return qqqData.map(d => ({ time: toET(d.time as any) as any, value: d.close }))
  }, [qqqData, tzMode])

  /** Render-layer SPY/QQQ: aggregated to match renderCandleData bucket boundaries. */
  const renderSpyLineData = useMemo(() => {
    if (!autoRenderInterval || spyLineData.length === 0) return spyLineData
    const bucketSecs = INTERVAL_SECS[autoRenderInterval]
    if (!bucketSecs) return spyLineData
    return aggregateLineSeries(spyLineData, bucketSecs)
  }, [spyLineData, autoRenderInterval])

  const renderQqqLineData = useMemo(() => {
    if (!autoRenderInterval || qqqLineData.length === 0) return qqqLineData
    const bucketSecs = INTERVAL_SECS[autoRenderInterval]
    if (!bucketSecs) return qqqLineData
    return aggregateLineSeries(qqqLineData, bucketSecs)
  }, [qqqLineData, autoRenderInterval])

  // Memoize toET-shifted series so re-runs triggered by trades/emaOverlays/toggles
  // don't re-transform thousands of bars each time.
  const candleData = useMemo(
    () => data.map(d => ({ ...d, time: toET(d.time as any) as any })),
    [data, tzMode],
  )

  // Main-chart indicator instances (overlays on the candlestick chart)
  const mainInstances = useMemo(
    () => indicators.filter(i => i.enabled && i.pane === 'main'),
    [indicators],
  )
  const mainInstancesKey = useMemo(
    () => JSON.stringify(mainInstances.map(i => ({ id: i.id, type: i.type, params: i.params, color: i.color, htfInterval: i.htfInterval }))),
    [mainInstances],
  )

  // Sub-pane grouping: shared types (RSI) merge into one pane, isolated types (MACD) get their own
  const subPaneGroups = useMemo(() => {
    const subInstances = indicators.filter(i => i.enabled && i.pane === 'sub')
    const groups: { key: string; label: string; instances: IndicatorInstance[] }[] = []
    const seen = new Map<string, number>()

    for (const inst of subInstances) {
      const def = INDICATOR_DEFS[inst.type]
      if ((def.subPaneSharing ?? 'isolated') === 'shared') {
        const existing = seen.get(inst.type)
        if (existing !== undefined) {
          groups[existing].instances.push(inst)
        } else {
          seen.set(inst.type, groups.length)
          groups.push({ key: inst.type, label: inst.type.toUpperCase(), instances: [inst] })
        }
      } else {
        groups.push({
          key: inst.id,
          label: `${inst.type.toUpperCase()}(${Object.values(inst.params).join(',')})`,
          instances: [inst],
        })
      }
    }
    return groups
  }, [indicators])

  /** Render-layer candleData: client-side aggregated when autoRenderInterval is set.
   *  All setData calls, candleTimeIndex, SPY/QQQ, and volume paths consume this. */
  const renderCandleData = useMemo(() => {
    if (!autoRenderInterval) return candleData
    const bucketSecs = INTERVAL_SECS[autoRenderInterval]
    if (!bucketSecs) return candleData
    return aggregateBars(candleData, bucketSecs)
  }, [candleData, autoRenderInterval])

  const candleTimeIndex = useMemo(() => {
    const map = new Map<string | number, number>()
    for (let i = 0; i < renderCandleData.length; i++) {
      map.set(renderCandleData[i].time, i)
    }
    return map
  }, [renderCandleData])

  const mainMarkers = useMemo(
    () => {
      if (!trades || trades.length === 0) return null
      if (isAggregated) return aggregateMarkers(trades, candleTimeIndex, effectiveInterval, backtestInterval, toET)
      return buildMarkers(trades)
    },
    [trades, isAggregated, candleTimeIndex, effectiveInterval, backtestInterval, tzMode],
  )

  const subPaneMarkers = useMemo(
    () => {
      if (!trades || trades.length === 0) return null
      if (isAggregated) return aggregateMarkers(trades, candleTimeIndex, effectiveInterval, backtestInterval, toET, true)
      return buildMarkers(trades, true)
    },
    [trades, isAggregated, candleTimeIndex, effectiveInterval, backtestInterval, tzMode],
  )

  // Rule signal markers — one circle per signal, colored by rule index
  const ruleSignalMarkers = useMemo(() => {
    if (!ruleSignals || ruleSignals.length === 0) return []
    const out: any[] = []
    for (const rs of ruleSignals) {
      const color = RULE_SIGNAL_COLORS[rs.rule_index % RULE_SIGNAL_COLORS.length]
      const position = rs.side === 'buy' ? 'belowBar' : 'aboveBar'
      for (const sig of rs.signals) {
        const time = isAggregated ? snapTimestamp(sig.time, effectiveInterval, toET) : toET(sig.time as any)
        out.push({ time: time as any, position, color, shape: 'circle' as const, size: 0.6 })
      }
    }
    // Dedup by time+position when aggregated (multiple signals per candle)
    if (isAggregated) {
      const seen = new Set<string>()
      const deduped = out.filter(m => {
        const key = `${m.time}:${m.position}:${m.color}`
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
      deduped.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))
      return deduped
    }
    // lightweight-charts requires markers sorted by time ascending
    out.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))
    return out
  }, [ruleSignals, isAggregated, effectiveInterval, tzMode])

  const tradeLookup = useMemo(() => {
    if (!trades || trades.length === 0 || renderCandleData.length === 0) return null
    const SNAP = isAggregated ? 5 : 2
    const byIdx = new Map<number, Trade[]>()
    for (const t of trades) {
      const snapped = snapTimestamp(t.date, effectiveInterval, toET)
      const idx = candleTimeIndex.get(snapped)
      if (idx === undefined) continue
      const arr = byIdx.get(idx)
      if (arr) arr.push(t)
      else byIdx.set(idx, [t])
    }
    const result = new Map<string | number, Trade[]>()
    for (let i = 0; i < renderCandleData.length; i++) {
      for (let d = 0; d <= SNAP; d++) {
        if (d === 0) {
          const t = byIdx.get(i)
          if (t) { result.set(renderCandleData[i].time, t); break }
        } else {
          const left = byIdx.get(i - d)
          const right = byIdx.get(i + d)
          if (left || right) { result.set(renderCandleData[i].time, (left ?? right)!); break }
        }
      }
    }
    return result
  }, [trades, candleTimeIndex, renderCandleData, isAggregated, effectiveInterval, tzMode])

  const [tooltip, setTooltip] = useState<{ x: number; y: number; trades: Trade[] } | null>(null)
  const tradeLookupRef = useRef(tradeLookup)
  useEffect(() => { tradeLookupRef.current = tradeLookup }, [tradeLookup])

  const subPaneCount = subPaneGroups.length

  // ─── Main chart: mount once ─────────────────────────────────────────────
  // All overlays and markers are managed by additive effects below so a new
  // trade or toggle touches only its own series instead of tearing the whole
  // chart down and rebuilding it.
  useEffect(() => {
    if (!containerRef.current) return

    const showMainTimeAxis = subPaneCount === 0
    const chart = createChart(containerRef.current, {
      ...chartOptionsBase,
      // minBarSpacing: lw-charts default 0.5px clamps zoom-out at ~2x chart width in
      // bars (~2.3K on a 1150px pane), which made the 8000-bar auto-downsample
      // threshold unreachable (found in A8 live verification). 0.01 allows deep
      // zoom-out; the auto-interval switch bounds the rendered object count.
      timeScale: { borderColor: GRID, timeVisible: true, visible: showMainTimeAxis, minBarSpacing: 0.01 },
    })
    chartRef.current = chart
    onChartReadyRef.current?.(chart)

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN,
      wickUpColor: UP, wickDownColor: DOWN,
      priceScaleId: 'right',
    })
    candleSeriesRef.current = candleSeries

    function syncWidths() {
      const mainChart = chartRef.current
      if (!mainChart) return
      try {
        let maxRightW = mainChart.priceScale('right').width()
        for (const entry of paneRegistryRef.current.values()) {
          maxRightW = Math.max(maxRightW, entry.chart.priceScale('right').width())
        }
        if (maxRightW > 0) {
          mainChart.applyOptions({ rightPriceScale: { minimumWidth: maxRightW } })
          for (const entry of paneRegistryRef.current.values()) {
            entry.chart.applyOptions({ rightPriceScale: { minimumWidth: maxRightW } })
          }
        }
        const mainLeftW = mainChart.priceScale('left').width()
        if (mainLeftW > 0) {
          for (const entry of paneRegistryRef.current.values()) {
            entry.chart.applyOptions({ leftPriceScale: { minimumWidth: mainLeftW, visible: false } })
          }
        }
      } catch {}
    }
    syncWidthsRef.current = syncWidths

    // Pan/zoom sync + price scale width equalization.
    // syncWidths() forces layout via priceScale().width() + applyOptions, so
    // rAF-coalesce it. sessionStorage is debounced — persisting every frame
    // was the dominant cost during drag.
    let widthsRaf: number | null = null
    let sessionWriteTimer: number | null = null
    let autoIntervalTimer: number | null = null
    const syncHandler = (range: any) => {
      if (!range) return
      for (const entry of paneRegistryRef.current.values()) {
        try { entry.chart.timeScale().setVisibleLogicalRange(range) } catch {}
      }
      if (widthsRaf === null) {
        widthsRaf = requestAnimationFrame(() => {
          widthsRaf = null
          syncWidths()
        })
      }
      if (sessionWriteTimer !== null) window.clearTimeout(sessionWriteTimer)
      sessionWriteTimer = window.setTimeout(() => {
        // R3: no async switch pending anymore — render-layer resample is synchronous.
        // Always safe to persist the logical range.
        sessionStorage.setItem('strategylab-chart-range', JSON.stringify(range))
        sessionWriteTimer = null
      }, 200)

      // Auto-downsampling (render-layer): measure visible base-equivalent bars and
      // call setAutoRenderInterval when the span crosses AUTOS_ON/OFF thresholds.
      // Uses stable refs (viewIntervalRef, baseIntervalRef, autoRenderIntervalRef,
      // setAutoRenderIntervalRef) so this closure never needs to be re-subscribed.
      // D3 fix: compare BASE-equivalent bars via calcVisibleBaseBars() to prevent the
      // re-aggregate loop after a switch (raw bars drop, below OFF threshold).
      if (autoIntervalTimer !== null) window.clearTimeout(autoIntervalTimer)
      autoIntervalTimer = window.setTimeout(() => evaluateZoomSpan(range), 150)
    }

    // The auto-downsample evaluation, callable from two places: the debounced
    // range-change path in syncHandler above, and (via autoEvalRef) the
    // checkbox-re-enable effect — lw-charts skips no-change setVisibleLogicalRange
    // calls, so a synthetic "nudge" event can't reach this; it must be invoked.
    function evaluateZoomSpan(range: { from: number; to: number }) {
      // Master enable (user checkbox). undefined = enabled (default).
      if (autoIntervalEnabledRef.current === false) return
      // Structural guard (FIX-2/RACE-01): skip evaluation while a range-restore is in
      // flight. setVisibleRange fires subscribeVisibleLogicalRangeChange, which would
      // re-enter this evaluator 150ms later. Returning here closes the loop structurally;
      // the hysteresis in evaluateAutoInterval is defence-in-depth only.
      if (autoRangeRef.current !== null) return
      // For auto evaluation: the base is always the DATA interval (viewInterval prop).
      // autoRenderInterval overrides on top — the autoActive flag for the evaluator
      // is whether we currently have a render-layer aggregate active.
      const currentView = viewIntervalRef.current
      const base = baseIntervalRef.current
      const currentAutoRender = autoRenderIntervalRef.current
      // The "effective" view for the evaluator: render interval if active, else viewInterval.
      const evalView = currentAutoRender ?? currentView
      const autoActive = currentAutoRender !== null
      // Compute base-equivalent visible bars (D3) using the shared pure helper.
      const rawSpan = Math.round(range.to - range.from)
      // When render-layer is active, bars on screen = render-aggregated bars;
      // scale back to base-equivalent for the evaluator.
      const visibleBaseBars = calcVisibleBaseBars(rawSpan, evalView, base)

      // Delegate all branching logic to the pure evaluateAutoInterval helper.
      // pendingSince is always null (R3: synchronous, no async race).
      const decision = evaluateAutoInterval({
        visibleBaseBars,
        viewInterval: evalView,
        baseInterval: base,
        autoActive,
        pendingSince: null,
        now: Date.now(),
      })

      if (decision.action === 'none') {
        return
      }

      if (decision.action === 'restore') {
        // User zoomed back in — clear render-layer aggregate.
        autoRangeRef.current = null
        setAutoRenderIntervalRef.current(null)
        return
      }

      // FIX-6/RACE-05: guard against unknown interval targets before committing the
      // coarsen — if the ladder ever outruns INTERVAL_SECS the sub-pane aggregation
      // (which derives bucketSecs from INTERVAL_SECS[autoRenderInterval]) would silently
      // produce mismatched bar counts and desynced crosshair alignment.
      if (!INTERVAL_SECS[decision.target]) return

      // 'coarsen': capture visible time range before the state update so we can
      // restore it after renderCandleData memo recomputes and setData fires (B1/D2).
      try {
        const vr = chart.timeScale().getVisibleRange()
        if (vr) autoRangeRef.current = vr as IRange<Time>
      } catch {}
      setAutoRenderIntervalRef.current(decision.target)
    }
    autoEvalRef.current = () => {
      try {
        const lr = chart.timeScale().getVisibleLogicalRange()
        if (lr) evaluateZoomSpan(lr)
      } catch { /* chart torn down */ }
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(syncHandler)

    // Initial alignment: fire after MACD/RSI effects have had time to mount
    const alignTimer = setTimeout(syncWidths, 100)

    const crosshairHandler = (param: any) => {
      try {
        if (!param.time) {
          for (const entry of paneRegistryRef.current.values()) entry.chart.clearCrosshairPosition()
          setTooltip(null)
          return
        }
        const key = normalizeTime(param.time)
        const tradesOnBar = tradeLookupRef.current?.get(key)
        if (tradesOnBar && param.point) {
          setTooltip({ x: param.point.x, y: param.point.y, trades: tradesOnBar })
        } else {
          setTooltip(null)
        }
        for (const entry of paneRegistryRef.current.values()) {
          try { entry.chart.setCrosshairPosition(NaN, param.time, entry.series) } catch {}
        }
      } catch {}
    }
    chart.subscribeCrosshairMove(crosshairHandler)

    return () => {
      clearTimeout(alignTimer)
      if (widthsRaf !== null) cancelAnimationFrame(widthsRaf)
      if (sessionWriteTimer !== null) window.clearTimeout(sessionWriteTimer)
      if (autoIntervalTimer !== null) window.clearTimeout(autoIntervalTimer)
      autoEvalRef.current = null
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(syncHandler)
      chart.unsubscribeCrosshairMove(crosshairHandler)
      // Null refs before remove() so any late callback (Results' cleanup,
      // sibling pane teardown) takes the null-guard path instead of throwing.
      chartRef.current = null
      candleSeriesRef.current = null
      regimeBgSeriesRef.current = null
      mainMarkersPluginRef.current = null
      tradeLookupRef.current = null
      setTooltip(null)
      rangeRestoredRef.current = false
      autoRangeRef.current = null
      onChartReadyRef.current?.(null)
      chart.remove()
    }
    // subPaneCount triggers re-creation because the Group key changes,
    // remounting the containerRef DOM node. The chart must be recreated
    // on the new node.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subPaneCount])

  // Candle data + range restore / fitContent on symbol/interval/date change.
  // Consumes renderCandleData (client-side aggregated when autoRenderInterval is set)
  // so the chart always displays the render-layer resolution.
  // fitContent fires only when (ticker, interval, from, to) changes — not on
  // auto-refresh polls or render-interval changes.
  useEffect(() => {
    const series = candleSeriesRef.current
    const chart = chartRef.current
    if (!series || !chart || renderCandleData.length === 0) return
    series.setData(renderCandleData)

    // Dev + probe-build assertion surface for auto-downsampling zoom verification (D5/F309).
    // lastSetDataPoints reflects the RENDERED bar count (render-layer resolution).
    // setVisibleLogicalRange lets scripted verification drive zoom without wheel events.
    // VITE_ENABLE_DEBUG_HOOKS=true enables this in render-probe builds (production build
    // with the env flag set); regular production builds (no flag) still exclude the hook.
    if (import.meta.env.DEV || import.meta.env.VITE_ENABLE_DEBUG_HOOKS === 'true') {
      window.__chartDebug = {
        lastSetDataPoints: renderCandleData.length,
        setVisibleLogicalRange: (from: number, to: number) => {
          try { chartRef.current?.timeScale().setVisibleLogicalRange({ from, to }) } catch { /* removed chart */ }
        },
        getRanges: () => {
          try {
            const ts = chartRef.current?.timeScale()
            return { logical: ts?.getVisibleLogicalRange() ?? null, time: ts?.getVisibleRange() ?? null }
          } catch { return null }
        },
      }
    }

    // Render-layer range restore: takes priority over sessionStorage (B1/D2/R3).
    // autoRangeRef is set just before setAutoRenderInterval fires so the visible
    // time window is preserved across the render-resolution switch.
    if (autoRangeRef.current) {
      const savedAutoRange = autoRangeRef.current
      autoRangeRef.current = null
      // Fall back to fitContent if setVisibleRange fails
      // (e.g. saved time-domain range has endpoints not present in coarser series).
      // chartRef read dynamically per Key Bugs Fixed teardown guard pattern.
      try {
        chart.timeScale().setVisibleRange(savedAutoRange)
      } catch {
        try { const c = chartRef.current; if (c) c.timeScale().fitContent() } catch {}
      }
      return
    }

    // Build the tuple key so we can detect actual query-param changes.
    const fitKey = `${ticker ?? ''}|${interval ?? ''}|${from ?? ''}|${to ?? ''}`
    const paramsChanged = lastFitParamsRef.current !== fitKey

    if (!rangeRestoredRef.current) {
      // First load: restore saved range or fit.
      rangeRestoredRef.current = true
      lastFitParamsRef.current = fitKey
      const savedRange = sessionStorage.getItem('strategylab-chart-range')
      if (savedRange) {
        try { chart.timeScale().setVisibleLogicalRange(JSON.parse(savedRange)) }
        catch { chart.timeScale().fitContent() }
      } else {
        chart.timeScale().fitContent()
      }
    } else if (paramsChanged) {
      // Symbol/interval/date switched → fit all panes; also clear auto render interval.
      // FIX-4/RACE-02: write both the ref (live value for the syncHandler closure) and
      // the state (for React rendering) synchronously so the debounced evaluator can't
      // act on the stale previous ticker's auto-render state during the 150ms window.
      autoRenderIntervalRef.current = null
      autoRangeRef.current = null
      setAutoRenderInterval(null)
      // FIX-9/COR-03: fitContent must fire against base-resolution renderCandleData.
      // App.tsx now resets autoRenderInterval eagerly (FIX-5), so Chart's autoRenderInterval
      // prop is null before new data arrives — renderCandleData equals candleData here.
      // The setAutoRenderInterval(null) above is a belt-and-suspenders fallback for any
      // path that bypasses App's eager reset. In both cases the data on this pass is
      // base-resolution, so fitContent fires correctly here.
      lastFitParamsRef.current = fitKey
      try { const c = chartRef.current; if (c) c.timeScale().fitContent() } catch {}
      for (const entry of paneRegistryRef.current.values()) {
        try { const c = entry.chart; if (c) c.timeScale().fitContent() } catch {}
      }
    }
    // else: auto-refresh poll or render-interval change — preserve user zoom.
  }, [renderCandleData, subPaneCount, ticker, interval, from, to])

  // Report render-layer interval changes up to App.tsx for the header badge.
  // Stable ref to onAutoRenderChange so this effect doesn't re-run when the callback
  // identity changes (same pattern as onChartReadyRef).
  const onAutoRenderChangeRef = useRef(onAutoRenderChange)
  useEffect(() => { onAutoRenderChangeRef.current = onAutoRenderChange })
  useEffect(() => {
    onAutoRenderChangeRef.current?.(autoRenderInterval)
  }, [autoRenderInterval])

  // SPY overlay
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !showSpy || renderSpyLineData.length === 0) return
    const spy = chart.addSeries(LineSeries, {
      color: '#f0883e', lineWidth: 1, title: 'SPY',
      priceScaleId: 'spy-scale',
      priceFormat: { type: 'price', precision: 2 },
    })
    spy.setData(renderSpyLineData as any)
    chart.priceScale('spy-scale').applyOptions({ visible: false })
    return () => { try { chart.removeSeries(spy) } catch {} }
  }, [showSpy, renderSpyLineData, subPaneCount])

  // QQQ overlay
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !showQqq || renderQqqLineData.length === 0) return
    const qqq = chart.addSeries(LineSeries, {
      color: '#a371f7', lineWidth: 1, title: 'QQQ',
      priceScaleId: 'qqq-scale',
      priceFormat: { type: 'price', precision: 2 },
    })
    qqq.setData(renderQqqLineData as any)
    chart.priceScale('qqq-scale').applyOptions({ visible: false })
    return () => { try { chart.removeSeries(qqq) } catch {} }
  }, [showQqq, renderQqqLineData, subPaneCount])

  // ─── Main-chart indicator overlays (generic) ─���───────────────────────
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const seriesMap = new Map<string, ISeriesApi<any>>()

    for (const inst of mainInstances) {
      if (inst.type === 'volume') {
        const vol = chart.addSeries(HistogramSeries, {
          priceFormat: { type: 'volume' },
          priceScaleId: 'volume',
        })
        vol.priceScale().applyOptions({ scaleMargins: { top: 0.75, bottom: 0 }, visible: false })
        seriesMap.set(inst.id, vol)
      } else if (inst.type === 'bb') {
        const colors = { upper: '#30363d', middle: '#58a6ff', lower: '#30363d' }
        const htfSuffix = inst.htfInterval ? ` ${inst.htfInterval.toUpperCase()}` : ''
        for (const key of ['upper', 'middle', 'lower'] as const) {
          const s = chart.addSeries(LineSeries, {
            color: colors[key], lineWidth: 1,
            title: `BB ${key.charAt(0).toUpperCase() + key.slice(1)}${htfSuffix}`,
            priceScaleId: 'right',
            ...(inst.htfInterval && inst.htfInterval !== viewInterval ? { lineType: LineType.WithSteps } : {}),
          })
          seriesMap.set(`${inst.id}:${key}`, s)
        }
      } else {
        const paramStr = Object.values(inst.params).join(',')
        const defaultColor = inst.type === 'vwap' ? '#ff9800' : '#f0883e'
        const color = inst.color ?? defaultColor
        const htfSuffix = inst.htfInterval ? ` ${inst.htfInterval.toUpperCase()}` : ''
        const s = chart.addSeries(LineSeries, {
          color, lineWidth: 1,
          title: inst.type === 'vwap' ? `VWAP${htfSuffix}` : `${inst.type.toUpperCase()}(${paramStr})${htfSuffix}`,
          priceScaleId: 'right',
          ...(inst.htfInterval && inst.htfInterval !== viewInterval ? { lineType: LineType.WithSteps } : {}),
        })
        seriesMap.set(inst.id, s)
      }
    }

    mainOverlaySeriesRef.current = seriesMap

    return () => {
      mainOverlaySeriesRef.current = null
      for (const s of seriesMap.values()) { try { chart.removeSeries(s) } catch {} }
    }
  }, [mainInstancesKey, subPaneCount, viewInterval])

  useEffect(() => {
    const seriesMap = mainOverlaySeriesRef.current
    if (!seriesMap) return

    // Render-layer bucket size for indicator line series aggregation.
    const renderBucketSecs = autoRenderInterval ? (INTERVAL_SECS[autoRenderInterval] ?? 0) : 0

    for (const inst of mainInstances) {
      const data = instanceData[inst.id]
      if (!data) continue

      if (inst.type === 'volume') {
        const vol = seriesMap.get(inst.id)
        if (vol) {
          const useCandleColor = inst.params.coloring === 'candle'
          const closeMap = new Map<any, { close: number; prevClose: number }>()
          if (useCandleColor) {
            // Use renderCandleData so color lookup aligns with rendered bars.
            for (let i = 0; i < renderCandleData.length; i++) {
              const bar = renderCandleData[i]
              closeMap.set(bar.time, { close: bar.close, prevClose: i > 0 ? renderCandleData[i - 1].close : bar.open })
            }
          }
          // Aggregate raw volume then color-map against renderCandleData.
          const rawVolData = toLineData(data.volume ?? [], toET)
          const aggVolData = renderBucketSecs > 0 ? aggregateLineSeries(rawVolData, renderBucketSecs) : rawVolData
          vol.setData(aggVolData.map(d => {
            let color = '#26a64166'
            if (useCandleColor) {
              const c = closeMap.get(d.time)
              if (c) color = c.close >= c.prevClose ? '#26a64166' : '#ef535066'
            }
            return d.value !== undefined ? { time: d.time, value: d.value, color } : { time: d.time, color }
          }))
        }
      } else if (inst.type === 'bb') {
        for (const key of ['upper', 'middle', 'lower'] as const) {
          const s = seriesMap.get(`${inst.id}:${key}`)
          if (s && data[key]) {
            const raw = toLineData(data[key], toET)
            s.setData(renderBucketSecs > 0 ? aggregateLineSeries(raw, renderBucketSecs) : raw)
          }
        }
      } else {
        const seriesKey = Object.keys(data)[0]
        if (!seriesKey || !data[seriesKey]) continue
        const raw = toLineData(data[seriesKey], toET)
        seriesMap.get(inst.id)?.setData(renderBucketSecs > 0 ? aggregateLineSeries(raw, renderBucketSecs) : raw)
      }
    }
  }, [instanceData, mainInstancesKey, renderCandleData, autoRenderInterval, tzMode, subPaneCount])

  // EMA rising/falling overlays (per-rule visualization during/after backtest)
  // Uses 2 series per overlay (active + inactive) instead of one per segment
  // to avoid creating hundreds of LineSeries on large datasets.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !emaOverlays || emaOverlays.length === 0) return
    // Only render overlays whose corresponding MA indicator is enabled in the sidebar.
    // overlay.indicator is formatted as "ma_{period}_{type}" (e.g. "ma_200_sma").
    const enabledOverlays = emaOverlays.filter(o => {
      const parts = o.indicator.split('_')
      if (parts.length < 3 || parts[0] !== 'ma') return true // non-MA overlays pass through
      const period = Number(parts[1])
      const type = parts.slice(2).join('_')
      return indicators.some(i => i.type === 'ma' && i.enabled && Number(i.params.period) === period && i.params.type === type)
    })
    const created: ISeriesApi<any>[] = []
    for (const overlay of enabledOverlays) {
      const activeColor = overlay.side === 'buy' ? '#26a641' : '#f85149'
      const inactiveColor = '#484f58'
      const label = `${overlay.indicator.toUpperCase()} ${overlay.condition === 'rising_over' ? '↑' : '↓'}${overlay.lookback}`

      const activePts: Array<{ time: any; value?: number }> = []
      const inactivePts: Array<{ time: any; value?: number }> = []

      for (let i = 0; i < overlay.series.length; i++) {
        const pt = overlay.series[i]
        const t = isAggregated
          ? snapTimestamp(pt.time, effectiveInterval, toET)
          : toET(pt.time as any) as any
        if (pt.value === null) {
          activePts.push({ time: t })
          inactivePts.push({ time: t })
          continue
        }
        const isActive = overlay.active[i]
        if (isActive) {
          activePts.push({ time: t, value: pt.value })
          inactivePts.push({ time: t })
        } else {
          activePts.push({ time: t })
          inactivePts.push({ time: t, value: pt.value })
        }
        // Bridge point: duplicate into the other series at transitions
        // so lines connect across the switch instead of leaving gaps.
        const prev = i > 0 ? overlay.active[i - 1] : isActive
        if (prev !== isActive && i > 0 && overlay.series[i - 1].value !== null) {
          if (isActive) {
            activePts[activePts.length - 1] = { time: t, value: pt.value }
            inactivePts[inactivePts.length - 1] = { time: t, value: pt.value }
          } else {
            activePts[activePts.length - 1] = { time: t, value: pt.value }
            inactivePts[inactivePts.length - 1] = { time: t, value: pt.value }
          }
        }
      }

      const dedup = (pts: Array<{ time: any; value?: number }>) => {
        const map = new Map<any, { time: any; value?: number }>()
        for (const p of pts) map.set(p.time, p)
        return Array.from(map.values())
      }

      const sActive = chart.addSeries(LineSeries, {
        color: activeColor,
        lineWidth: 2,
        title: label,
        priceScaleId: 'right',
        lastValueVisible: false,
        priceLineVisible: false,
      })
      sActive.setData(isAggregated ? dedup(activePts) : activePts)
      created.push(sActive)

      const sInactive = chart.addSeries(LineSeries, {
        color: inactiveColor,
        lineWidth: 1,
        title: '',
        priceScaleId: 'right',
        lastValueVisible: false,
        priceLineVisible: false,
      })
      sInactive.setData(isAggregated ? dedup(inactivePts) : inactivePts)
      created.push(sInactive)
    }
    return () => { for (const s of created) { try { chart.removeSeries(s) } catch {} } }
  }, [emaOverlays, isAggregated, effectiveInterval, tzMode, subPaneCount, indicators])

  // Trade + rule-signal markers — merged into one sorted array and pushed to a
  // single plugin instance. candleData in deps ensures the effect re-runs after
  // series.setData() so the plugin paints correctly post-mount.
  const mainMarkersPluginRef = useRef<any>(null)
  useEffect(() => {
    const series = candleSeriesRef.current
    if (!series) return
    // Merge trade markers and rule-signal markers into one time-sorted array.
    const tradeMs = (mainMarkers ?? []) as any[]
    const merged = [...tradeMs, ...ruleSignalMarkers].sort(
      (a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0)
    )
    if (!mainMarkersPluginRef.current) {
      mainMarkersPluginRef.current = createSeriesMarkers(series, merged as any)
    } else {
      mainMarkersPluginRef.current.setMarkers(merged as any)
    }
  }, [mainMarkers, ruleSignalMarkers, renderCandleData, subPaneCount])

  // Regime background shading — histogram series on hidden scale, green for active long, red for active short
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    if (!regimeSeries || regimeSeries.length === 0) {
      // Clear existing regime series when result is cleared
      if (regimeBgSeriesRef.current) {
        try { chart.removeSeries(regimeBgSeriesRef.current) } catch {}
        regimeBgSeriesRef.current = null
      }
      return
    }
    if (!regimeBgSeriesRef.current) {
      const s = chart.addSeries(HistogramSeries, {
        priceScaleId: 'regime-bg',
        lastValueVisible: false,
        priceLineVisible: false,
      })
      s.priceScale().applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false })
      regimeBgSeriesRef.current = s
    }
    const deduped = new Map<string | number, { time: any; value: number; color: string }>()
    for (const pt of regimeSeries) {
      const color = pt.direction === 'long' ? '#26a64120' : pt.direction === 'short' ? '#f8514920' : undefined
      if (!color) continue
      const t = snapTimestamp(pt.time, effectiveInterval, toET)
      deduped.set(t, { time: t, value: 1, color })
    }
    const bgData = Array.from(deduped.values()).sort((a, b) => {
      if (typeof a.time === 'string' && typeof b.time === 'string') return a.time < b.time ? -1 : a.time > b.time ? 1 : 0
      return (a.time as number) - (b.time as number)
    })
    try { regimeBgSeriesRef.current.setData(bgData as any) } catch {}
  }, [regimeSeries, effectiveInterval, tzMode])

  // Compute default panel sizes based on sub-pane count (matches original ratios)
  const defaultSizes = useMemo(() => {
    if (subPaneCount === 0) return [100]
    if (subPaneCount === 1) return [65, 35]
    if (subPaneCount === 2) return [50, 25, 25]
    // 3+ sub-panes: distribute evenly after giving main ~40%
    const subSize = Math.floor(60 / subPaneCount)
    return [100 - subSize * subPaneCount, ...Array(subPaneCount).fill(subSize)]
  }, [subPaneCount])

  // Double-click to maximize: track which pane index is maximized (null = none)
  const [maximizedPane, setMaximizedPane] = useState<number | null>(null)
  const preMaxLayoutRef = useRef<number[] | null>(null)
  const groupRef = useRef<any>(null)

  // Reset maximized state when sub-pane count changes (indicators toggled)
  useEffect(() => {
    setMaximizedPane(null)
    preMaxLayoutRef.current = null
  }, [subPaneCount])

  // minSize per panel index: main=40% (F225), each sub=8% (~80px on a 1000px column)
  const panelMinSizes = useMemo(() => {
    const mins = [40]
    for (let i = 0; i < subPaneCount; i++) mins.push(8)
    return mins
  }, [subPaneCount])

  const handlePaneDoubleClick = useCallback((paneIndex: number) => {
    const group = groupRef.current
    if (!group) return
    if (maximizedPane === paneIndex) {
      // Restore previous layout
      if (preMaxLayoutRef.current) {
        group.setLayout(preMaxLayoutRef.current)
      }
      preMaxLayoutRef.current = null
      setMaximizedPane(null)
    } else {
      // Save current layout, then maximize this pane.
      // Other panes collapse to their minSize; the target gets the remainder.
      preMaxLayoutRef.current = group.getLayout()
      const othersMin = panelMinSizes.reduce((sum, m, i) => i === paneIndex ? sum : sum + m, 0)
      const layout = panelMinSizes.map((m, i) =>
        i === paneIndex ? 100 - othersMin : m
      )
      group.setLayout(layout)
      setMaximizedPane(paneIndex)
    }
  }, [maximizedPane, subPaneCount, panelMinSizes])

  // After any panel resize, trigger syncWidths for price scale alignment.
  // Debounced: syncWidths adjusts minimumWidth which can re-trigger onLayoutChange,
  // causing an infinite oscillation loop (two widths alternating each frame).
  const layoutRafRef = useRef<number | null>(null)
  const handleLayout = useCallback(() => {
    if (layoutRafRef.current !== null) return
    layoutRafRef.current = requestAnimationFrame(() => {
      layoutRafRef.current = null
      syncWidthsRef.current()
    })
  }, [])

  // Persist pane sizes across page reloads via the v4 useDefaultLayout hook.
  // groupId is keyed by subPaneCount because the Group remounts (key prop) when
  // panel count changes — a new key means a new mount, so a new storage slot is correct.
  const { defaultLayout: savedLayout, onLayoutChanged: saveLayout } = useDefaultLayout({
    id: `chart-pane-sizes-${subPaneCount}`,
    storage: localStorage,
  })

  // The Group needs a key tied to subPaneCount so react-resizable-panels
  // resets its internal layout when panel count changes (defaultSize only
  // applies on mount). This remounts the main chart container, so the
  // main chart effect includes subPaneCount in its deps to recreate
  // the chart on the new DOM node.
  return (
    <div style={{ height: '100%', width: '100%', overflow: 'auto' }}>
      <Group
        key={`chart-panes-${subPaneCount}`}
        {...{ ref: groupRef } as any}
        orientation="vertical"
        defaultLayout={savedLayout}
        onLayoutChange={handleLayout}
        onLayoutChanged={saveLayout}
        style={{ height: '100%' }}
      >
        {/* Main chart panel — always present */}
        <Panel defaultSize={defaultSizes[0]} minSize={40}>
          <div
            style={{ position: 'relative', height: '100%', width: '100%' }}
            onDoubleClick={subPaneCount > 0 ? () => handlePaneDoubleClick(0) : undefined}
          >
            <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
            {tooltip && trades && (
              <TradeTooltip
                x={tooltip.x} y={tooltip.y}
                trades={tooltip.trades}
                allTrades={trades}
                candleTimeIndex={candleTimeIndex}
                toET={toET}
              />
            )}
            {ruleSignals && ruleSignals.length > 0 && (
              <div style={{
                position: 'absolute', top: 8, left: 8,
                background: 'rgba(13,17,23,0.82)',
                border: '1px solid #30363d',
                borderRadius: 4,
                padding: '5px 8px',
                display: 'flex',
                flexDirection: 'column',
                gap: 3,
                pointerEvents: 'none',
                zIndex: 10,
                maxWidth: 200,
              }}>
                {ruleSignals.map(rs => (
                  <div key={rs.side + '-' + rs.rule_index} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <span style={{
                      display: 'inline-block',
                      width: 8, height: 8,
                      borderRadius: '50%',
                      background: RULE_SIGNAL_COLORS[rs.rule_index % RULE_SIGNAL_COLORS.length],
                      flexShrink: 0,
                    }} />
                    <span style={{ color: '#c9d1d9', fontSize: 11, lineHeight: 1.3, wordBreak: 'break-word' }}>
                      {rs.label}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Panel>

        {/* Sub-pane panels with separators */}
        {subPaneGroups.map((group, idx) => (
          <SubPanelEntry
            key={group.key}
            group={group}
            paneIndex={idx + 1}
            isLastPane={idx === subPaneGroups.length - 1}
            defaultSize={defaultSizes[idx + 1]}
            minSize={panelMinSizes[idx + 1]}
            instanceData={instanceData}
            instanceLoading={instanceLoading}
            loadingByInstance={loadingByInstance}
            instanceError={instanceError}
            instanceErrorMessage={instanceErrorMessage}
            onRetryIndicators={onRetryIndicators}
            chartRef={chartRef}
            candleSeriesRef={candleSeriesRef}
            paneRegistryRef={paneRegistryRef}
            syncWidthsRef={syncWidthsRef}
            subPaneMarkers={subPaneMarkers}
            toET={toET}
            tzMode={tzMode}
            bucketSecs={autoRenderInterval ? (INTERVAL_SECS[autoRenderInterval] ?? 0) : undefined}
            onDoubleClick={handlePaneDoubleClick}
          />
        ))}
      </Group>
    </div>
  )
}

// Extracted to avoid inline JSX fragments with Separator+Panel pairs
function SubPanelEntry({
  group, paneIndex, isLastPane, defaultSize, minSize, instanceData, instanceLoading, loadingByInstance, instanceError, instanceErrorMessage, onRetryIndicators, chartRef, candleSeriesRef,
  paneRegistryRef, syncWidthsRef, subPaneMarkers, toET, tzMode, bucketSecs, onDoubleClick,
}: {
  group: { key: string; label: string; instances: IndicatorInstance[] }
  paneIndex: number
  isLastPane: boolean
  defaultSize: number
  minSize: number
  instanceData: Record<string, Record<string, { time: string; value: number | null }[]>>
  instanceLoading?: boolean
  loadingByInstance?: Record<string, boolean>
  instanceError?: boolean
  instanceErrorMessage?: string | null
  onRetryIndicators?: () => void
  chartRef: React.RefObject<IChartApi | null>
  candleSeriesRef: React.RefObject<ISeriesApi<any> | null>
  paneRegistryRef: React.RefObject<PaneRegistry>
  syncWidthsRef: React.RefObject<() => void>
  subPaneMarkers: any[] | null
  toET: (time: string | number) => any
  tzMode?: string
  bucketSecs?: number
  onDoubleClick: (paneIndex: number) => void
}) {
  return (
    <>
      <Separator className="resize-handle-h" />
      <Panel defaultSize={defaultSize} minSize={minSize}>
        <div
          style={{ height: '100%', width: '100%' }}
          onDoubleClick={() => onDoubleClick(paneIndex)}
        >
          <SubPane
            paneKey={group.key}
            instances={group.instances}
            instanceData={instanceData}
            loading={
              loadingByInstance
                ? group.instances.some(i => loadingByInstance[i.id])
                : instanceLoading
            }
            error={instanceError}
            errorMessage={instanceErrorMessage}
            onRetry={onRetryIndicators}
            mainChartRef={chartRef}
            mainSeriesRef={candleSeriesRef}
            paneRegistryRef={paneRegistryRef}
            syncWidthsRef={syncWidthsRef}
            markers={subPaneMarkers ?? undefined}
            toET={toET}
            label={group.label}
            tzMode={tzMode}
            bucketSecs={bucketSecs}
            showTimeAxis={isLastPane}
          />
        </div>
      </Panel>
    </>
  )
}

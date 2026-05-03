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
import type { IChartApi, ISeriesApi } from 'lightweight-charts'
import type { OHLCVBar, IndicatorInstance, EMAOverlay, Trade, RuleSignal } from '../../shared/types'
import { INDICATOR_DEFS } from '../../shared/types/indicators'
import { Group, Panel, Separator } from 'react-resizable-panels'
import SubPane from './SubPane'
import type { PaneRegistry } from './SubPane'
import { toLineData, aggregateMarkers, snapTimestamp } from './chartUtils'
import TradeTooltip from './TradeTooltip'
import { getTimezone, useTimezone } from '../../shared/utils/time'

interface ChartProps {
  data: OHLCVBar[]
  spyData?: OHLCVBar[]
  qqqData?: OHLCVBar[]
  showSpy: boolean
  showQqq: boolean
  indicators: IndicatorInstance[]
  instanceData: Record<string, Record<string, { time: string; value: number | null }[]>>
  trades?: Trade[]
  emaOverlays?: EMAOverlay[]
  ruleSignals?: RuleSignal[]
  regimeSeries?: Array<{ time: string | number; direction: string }>
  viewInterval: string
  backtestInterval: string
  onChartReady?: (chart: IChartApi | null) => void
}

const CHART_BG = '#0d1117'
const GRID = '#1c2128'
const TEXT = '#8b949e'
const UP = '#26a641'
const DOWN = '#f85149'

// Distinct from trade green/red; indexed by rule_index mod length
const RULE_SIGNAL_COLORS = ['#58a6ff', '#d2a8ff', '#f0883e', '#56d364', '#e5534b', '#768390', '#f778ba', '#a5d6ff']

const chartOptions = {
  layout: { background: { type: ColorType.Solid, color: CHART_BG }, textColor: TEXT },
  grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
  crosshair: { mode: 1 as const },
  timeScale: { borderColor: GRID, timeVisible: true },
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

export default function Chart({ data, spyData, qqqData, showSpy, showQqq, indicators, instanceData, trades, emaOverlays, ruleSignals, regimeSeries, viewInterval, backtestInterval, onChartReady }: ChartProps) {
  const [tzMode] = useTimezone()
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<any> | null>(null)
  const syncWidthsRef = useRef<() => void>(() => {})
  const rangeRestoredRef = useRef(false)
  const onChartReadyRef = useRef(onChartReady)
  useEffect(() => { onChartReadyRef.current = onChartReady })
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

  const isAggregated = viewInterval !== backtestInterval

  const candleTimeIndex = useMemo(() => {
    const map = new Map<string | number, number>()
    for (let i = 0; i < candleData.length; i++) {
      map.set(candleData[i].time, i)
    }
    return map
  }, [candleData])

  const mainMarkers = useMemo(
    () => {
      if (!trades || trades.length === 0) return null
      if (isAggregated) return aggregateMarkers(trades, candleTimeIndex, viewInterval, backtestInterval, toET)
      return buildMarkers(trades)
    },
    [trades, isAggregated, candleTimeIndex, viewInterval, backtestInterval, tzMode],
  )

  const subPaneMarkers = useMemo(
    () => {
      if (!trades || trades.length === 0) return null
      if (isAggregated) return aggregateMarkers(trades, candleTimeIndex, viewInterval, backtestInterval, toET, true)
      return buildMarkers(trades, true)
    },
    [trades, isAggregated, candleTimeIndex, viewInterval, backtestInterval, tzMode],
  )

  // Rule signal markers — one circle per signal, colored by rule index
  const ruleSignalMarkers = useMemo(() => {
    if (!ruleSignals || ruleSignals.length === 0) return []
    const out: any[] = []
    for (const rs of ruleSignals) {
      const color = RULE_SIGNAL_COLORS[rs.rule_index % RULE_SIGNAL_COLORS.length]
      const position = rs.side === 'buy' ? 'belowBar' : 'aboveBar'
      for (const sig of rs.signals) {
        out.push({
          time: toET(sig.time as any) as any,
          position,
          color,
          shape: 'circle' as const,
          size: 0.6,
        })
      }
    }
    // lightweight-charts requires markers sorted by time ascending
    out.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0))
    return out
  }, [ruleSignals, tzMode])

  const tradeLookup = useMemo(() => {
    if (!trades || trades.length === 0 || candleData.length === 0) return null
    if (isAggregated) {
      const grouped = new Map<string | number, Trade[]>()
      for (const t of trades) {
        const snapped = snapTimestamp(t.date, viewInterval, toET)
        const existing = grouped.get(snapped)
        if (existing) existing.push(t)
        else grouped.set(snapped, [t])
      }
      return grouped
    }
    const SNAP = 2
    const byIdx = new Map<number, Trade[]>()
    for (const t of trades) {
      const snapped = snapTimestamp(t.date, viewInterval, toET)
      const idx = candleTimeIndex.get(snapped)
      if (idx === undefined) continue
      const arr = byIdx.get(idx)
      if (arr) arr.push(t)
      else byIdx.set(idx, [t])
    }
    const result = new Map<string | number, Trade[]>()
    for (let i = 0; i < candleData.length; i++) {
      for (let d = 0; d <= SNAP; d++) {
        if (d === 0) {
          const t = byIdx.get(i)
          if (t) { result.set(candleData[i].time, t); break }
        } else {
          const left = byIdx.get(i - d)
          const right = byIdx.get(i + d)
          if (left || right) { result.set(candleData[i].time, (left ?? right)!); break }
        }
      }
    }
    return result
  }, [trades, candleTimeIndex, candleData, isAggregated, viewInterval, tzMode])

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

    const chart = createChart(containerRef.current, { ...chartOptions, height: containerRef.current.clientHeight })
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
        sessionStorage.setItem('strategylab-chart-range', JSON.stringify(range))
        sessionWriteTimer = null
      }, 200)
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

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth, height: containerRef.current.clientHeight })
    })
    ro.observe(containerRef.current)

    return () => {
      clearTimeout(alignTimer)
      if (widthsRaf !== null) cancelAnimationFrame(widthsRaf)
      if (sessionWriteTimer !== null) window.clearTimeout(sessionWriteTimer)
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
      onChartReadyRef.current?.(null)
      chart.remove()
      ro.disconnect()
    }
    // subPaneCount triggers re-creation because the Group key changes,
    // remounting the containerRef DOM node. The chart must be recreated
    // on the new node.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subPaneCount])

  // Candle data + one-time range restore
  const prevCandleDataRef = useRef(candleData)
  useEffect(() => {
    const series = candleSeriesRef.current
    const chart = chartRef.current
    if (!series || !chart || candleData.length === 0) return
    series.setData(candleData)
    const dataChanged = prevCandleDataRef.current !== candleData
    prevCandleDataRef.current = candleData
    if (!rangeRestoredRef.current) {
      rangeRestoredRef.current = true
      const savedRange = sessionStorage.getItem('strategylab-chart-range')
      if (savedRange) {
        try { chart.timeScale().setVisibleLogicalRange(JSON.parse(savedRange)) }
        catch { chart.timeScale().fitContent() }
      } else {
        chart.timeScale().fitContent()
      }
    } else if (dataChanged) {
      chart.timeScale().fitContent()
    }
  }, [candleData, subPaneCount])

  // SPY overlay
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !showSpy || spyLineData.length === 0) return
    const spy = chart.addSeries(LineSeries, {
      color: '#f0883e', lineWidth: 1, title: 'SPY',
      priceScaleId: 'spy-scale',
      priceFormat: { type: 'price', precision: 2 },
    })
    spy.setData(spyLineData)
    chart.priceScale('spy-scale').applyOptions({ visible: false })
    return () => { try { chart.removeSeries(spy) } catch {} }
  }, [showSpy, spyLineData, subPaneCount])

  // QQQ overlay
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !showQqq || qqqLineData.length === 0) return
    const qqq = chart.addSeries(LineSeries, {
      color: '#a371f7', lineWidth: 1, title: 'QQQ',
      priceScaleId: 'qqq-scale',
      priceFormat: { type: 'price', precision: 2 },
    })
    qqq.setData(qqqLineData)
    chart.priceScale('qqq-scale').applyOptions({ visible: false })
    return () => { try { chart.removeSeries(qqq) } catch {} }
  }, [showQqq, qqqLineData, subPaneCount])

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
            ...(inst.htfInterval ? { lineType: LineType.WithSteps } : {}),
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
          ...(inst.htfInterval ? { lineType: LineType.WithSteps } : {}),
        })
        seriesMap.set(inst.id, s)
      }
    }

    mainOverlaySeriesRef.current = seriesMap

    return () => {
      mainOverlaySeriesRef.current = null
      for (const s of seriesMap.values()) { try { chart.removeSeries(s) } catch {} }
    }
  }, [mainInstancesKey, subPaneCount])

  useEffect(() => {
    const seriesMap = mainOverlaySeriesRef.current
    if (!seriesMap) return

    for (const inst of mainInstances) {
      const data = instanceData[inst.id]
      if (!data) continue

      if (inst.type === 'volume') {
        const vol = seriesMap.get(inst.id)
        if (vol) {
          const useCandleColor = inst.params.coloring === 'candle'
          const closeMap = new Map<any, { close: number; prevClose: number }>()
          if (useCandleColor) {
            for (let i = 0; i < candleData.length; i++) {
              const bar = candleData[i]
              closeMap.set(bar.time, { close: bar.close, prevClose: i > 0 ? candleData[i - 1].close : bar.open })
            }
          }
          vol.setData((data.volume ?? []).map(d => {
            const t = toET(d.time as any) as any
            let color = '#26a64166'
            if (useCandleColor) {
              const c = closeMap.get(t)
              if (c) color = c.close >= c.prevClose ? '#26a64166' : '#ef535066'
            }
            return { time: t, value: d.value, color }
          }))
        }
      } else if (inst.type === 'bb') {
        for (const key of ['upper', 'middle', 'lower'] as const) {
          const s = seriesMap.get(`${inst.id}:${key}`)
          if (s && data[key]) s.setData(toLineData(data[key], toET))
        }
      } else {
        const seriesKey = Object.keys(data)[0]
        if (!seriesKey || !data[seriesKey]) continue
        seriesMap.get(inst.id)?.setData(toLineData(data[seriesKey], toET))
      }
    }
  }, [instanceData, mainInstancesKey, candleData, tzMode, subPaneCount])

  // EMA rising/falling overlays (per-rule visualization during/after backtest)
  // Uses 2 series per overlay (active + inactive) instead of one per segment
  // to avoid creating hundreds of LineSeries on large datasets.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !emaOverlays || emaOverlays.length === 0 || isAggregated) return
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
        const t = toET(pt.time as any) as any
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

      const sActive = chart.addSeries(LineSeries, {
        color: activeColor,
        lineWidth: 2,
        title: label,
        priceScaleId: 'right',
        lastValueVisible: false,
        priceLineVisible: false,
      })
      sActive.setData(activePts)
      created.push(sActive)

      const sInactive = chart.addSeries(LineSeries, {
        color: inactiveColor,
        lineWidth: 1,
        title: '',
        priceScaleId: 'right',
        lastValueVisible: false,
        priceLineVisible: false,
      })
      sInactive.setData(inactivePts)
      created.push(sInactive)
    }
    return () => { for (const s of created) { try { chart.removeSeries(s) } catch {} } }
  }, [emaOverlays, isAggregated, tzMode, subPaneCount, indicators])

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
  }, [mainMarkers, ruleSignalMarkers, candleData, subPaneCount])

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
    const bgData = regimeSeries.map(pt => {
      const t = toET(pt.time as any) as any
      const color = pt.direction === 'long' ? '#26a64120' : pt.direction === 'short' ? '#f8514920' : undefined
      return { time: t, value: color ? 1 : 0, color }
    }).filter(pt => pt.color !== undefined)
    try { regimeBgSeriesRef.current.setData(bgData as any) } catch {}
  }, [regimeSeries, tzMode])

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

  // minSize per panel index: main=20, each sub=5
  const panelMinSizes = useMemo(() => {
    const mins = [20]
    for (let i = 0; i < subPaneCount; i++) mins.push(5)
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
  // Debounced: syncWidths adjusts minimumWidth which can re-trigger onLayout,
  // causing an infinite oscillation loop (two widths alternating each frame).
  const layoutRafRef = useRef<number | null>(null)
  const handleLayout = useCallback(() => {
    if (layoutRafRef.current !== null) return
    layoutRafRef.current = requestAnimationFrame(() => {
      layoutRafRef.current = null
      syncWidthsRef.current()
    })
  }, [])

  // The Group needs a key tied to subPaneCount so react-resizable-panels
  // resets its internal layout when panel count changes (defaultSize only
  // applies on mount). This remounts the main chart container, so the
  // main chart effect includes subPaneCount in its deps to recreate
  // the chart on the new DOM node.
  return (
    <div style={{ height: '100%', width: '100%', overflow: 'hidden' }}>
      <Group
        key={`chart-panes-${subPaneCount}`}
        {...{ ref: groupRef } as any}
        orientation="vertical"
        autoSaveId={subPaneCount > 0 ? `chart-pane-sizes-${subPaneCount}` : undefined}
        onLayout={handleLayout}
        style={{ height: '100%' }}
      >
        {/* Main chart panel — always present */}
        <Panel defaultSize={defaultSizes[0]} minSize={20}>
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
            defaultSize={defaultSizes[idx + 1]}
            instanceData={instanceData}
            chartRef={chartRef}
            candleSeriesRef={candleSeriesRef}
            paneRegistryRef={paneRegistryRef}
            syncWidthsRef={syncWidthsRef}
            subPaneMarkers={subPaneMarkers}
            toET={toET}
            tzMode={tzMode}
            onDoubleClick={handlePaneDoubleClick}
          />
        ))}
      </Group>
    </div>
  )
}

// Extracted to avoid inline JSX fragments with Separator+Panel pairs
function SubPanelEntry({
  group, paneIndex, defaultSize, instanceData, chartRef, candleSeriesRef,
  paneRegistryRef, syncWidthsRef, subPaneMarkers, toET, tzMode, onDoubleClick,
}: {
  group: { key: string; label: string; instances: IndicatorInstance[] }
  paneIndex: number
  defaultSize: number
  instanceData: Record<string, Record<string, { time: string; value: number | null }[]>>
  chartRef: React.RefObject<IChartApi | null>
  candleSeriesRef: React.RefObject<ISeriesApi<any> | null>
  paneRegistryRef: React.RefObject<PaneRegistry>
  syncWidthsRef: React.RefObject<() => void>
  subPaneMarkers: any[] | null
  toET: (time: string | number) => any
  tzMode?: string
  onDoubleClick: (paneIndex: number) => void
}) {
  return (
    <>
      <Separator className="resize-handle-h" />
      <Panel defaultSize={defaultSize} minSize={5}>
        <div
          style={{ height: '100%', width: '100%' }}
          onDoubleClick={() => onDoubleClick(paneIndex)}
        >
          <SubPane
            paneKey={group.key}
            instances={group.instances}
            instanceData={instanceData}
            mainChartRef={chartRef}
            mainSeriesRef={candleSeriesRef}
            paneRegistryRef={paneRegistryRef}
            syncWidthsRef={syncWidthsRef}
            markers={subPaneMarkers ?? undefined}
            toET={toET}
            label={group.label}
            tzMode={tzMode}
          />
        </div>
      </Panel>
    </>
  )
}

import { useEffect, useRef, useMemo } from 'react'
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  ColorType,
} from 'lightweight-charts'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'
import type { OHLCVBar, IndicatorInstance, EMAOverlay, Trade } from '../../shared/types'
import { INDICATOR_DEFS } from '../../shared/types/indicators'
import SubPane from './SubPane'
import type { PaneRegistry } from './SubPane'
import { toLineData } from './chartUtils'

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
  onChartReady?: (chart: IChartApi | null) => void
}

const CHART_BG = '#0d1117'
const GRID = '#1c2128'
const TEXT = '#8b949e'
const UP = '#26a641'
const DOWN = '#f85149'

const chartOptions = {
  layout: { background: { type: ColorType.Solid, color: CHART_BG }, textColor: TEXT },
  grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
  crosshair: { mode: 1 as const },
  timeScale: { borderColor: GRID, timeVisible: true },
  rightPriceScale: { borderColor: GRID },
  leftPriceScale: { visible: false, borderColor: GRID },
}

// lightweight-charts v5 has no localization.timeZone support.
// Shift unix timestamps to ET wall-clock time by reconstructing them as UTC
// so the chart displays 9:30 instead of 13:30 for NYSE open.
// Date strings (daily+) pass through unchanged.
function toET(time: string | number): any {
  if (typeof time !== 'number') return time
  const d = new Date(time * 1000)
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  }).formatToParts(d)
  const get = (type: string) => parseInt(parts.find(p => p.type === type)?.value ?? '0')
  return Date.UTC(get('year'), get('month') - 1, get('day'), get('hour') % 24, get('minute'), get('second')) / 1000
}

function buildMarkers(trades: Trade[], showPrice = true, subPane = false) {
  return trades.map(t => {
    const isEntry = t.type === 'buy' || t.type === 'short'
    const isShortEntry = t.type === 'short'
    const isCover = t.type === 'cover'
    if (isEntry) {
      const label = isShortEntry ? 'SH' : 'B'
      return {
        time: toET(t.date as any) as any,
        position: subPane ? 'inBar' as const : (isShortEntry ? 'aboveBar' as const : 'belowBar' as const),
        color: '#e5c07b',
        shape: subPane ? 'circle' as const : (isShortEntry ? 'arrowDown' as const : 'arrowUp' as const),
        text: showPrice ? `${label} $${t.price}` : label,
      }
    }
    // Exit: sell or cover
    const win = (t.pnl ?? 0) >= 0
    const color = win ? UP : DOWN
    const pctStr = t.pnl_pct != null ? ` ${t.pnl_pct > 0 ? '+' : ''}${t.pnl_pct}%` : ''
    const label = t.stop_loss ? 'SL' : t.trailing_stop ? 'TSL' : (isCover ? 'COV' : 'S')
    return {
      time: toET(t.date as any) as any,
      position: subPane ? 'inBar' as const : (isCover ? 'belowBar' as const : 'aboveBar' as const),
      color,
      shape: subPane ? 'circle' as const : (isCover ? 'arrowUp' as const : 'arrowDown' as const),
      text: showPrice ? `${label} $${t.price}${pctStr}` : label,
    }
  })
}

export default function Chart({ data, spyData, qqqData, showSpy, showQqq, indicators, instanceData, trades, emaOverlays, onChartReady }: ChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<any> | null>(null)
  const syncWidthsRef = useRef<() => void>(() => {})
  const rangeRestoredRef = useRef(false)
  const onChartReadyRef = useRef(onChartReady)
  useEffect(() => { onChartReadyRef.current = onChartReady })
  const mainOverlaySeriesRef = useRef<Map<string, ISeriesApi<any>> | null>(null)
  const paneRegistryRef = useRef<PaneRegistry>(new Map())

  // SPY/QQQ as real close prices on their own left axis
  const spyLineData = useMemo(() => {
    if (!spyData || spyData.length === 0) return []
    return spyData.map(d => ({ time: toET(d.time as any) as any, value: d.close }))
  }, [spyData])

  const qqqLineData = useMemo(() => {
    if (!qqqData || qqqData.length === 0) return []
    return qqqData.map(d => ({ time: toET(d.time as any) as any, value: d.close }))
  }, [qqqData])

  // Memoize toET-shifted series so re-runs triggered by trades/emaOverlays/toggles
  // don't re-transform thousands of bars each time.
  const candleData = useMemo(
    () => data.map(d => ({ ...d, time: toET(d.time as any) as any })),
    [data],
  )

  // Main-chart indicator instances (overlays on the candlestick chart)
  const mainInstances = useMemo(
    () => indicators.filter(i => i.enabled && i.pane === 'main'),
    [indicators],
  )
  const mainInstancesKey = useMemo(
    () => JSON.stringify(mainInstances.map(i => ({ id: i.id, type: i.type, params: i.params }))),
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

  const mainMarkers = useMemo(
    () => trades && trades.length > 0 ? buildMarkers(trades) : null,
    [trades],
  )

  const subPaneMarkers = useMemo(
    () => trades && trades.length > 0 ? buildMarkers(trades, false, true) : null,
    [trades],
  )

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
          return
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
      mainMarkersPluginRef.current = null
      rangeRestoredRef.current = false
      onChartReadyRef.current?.(null)
      chart.remove()
      ro.disconnect()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Candle data + one-time range restore
  useEffect(() => {
    const series = candleSeriesRef.current
    const chart = chartRef.current
    if (!series || !chart || candleData.length === 0) return
    series.setData(candleData)
    if (!rangeRestoredRef.current) {
      rangeRestoredRef.current = true
      const savedRange = sessionStorage.getItem('strategylab-chart-range')
      if (savedRange) {
        try { chart.timeScale().setVisibleLogicalRange(JSON.parse(savedRange)) }
        catch { chart.timeScale().fitContent() }
      } else {
        chart.timeScale().fitContent()
      }
    }
  }, [candleData])

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
  }, [showSpy, spyLineData])

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
  }, [showQqq, qqqLineData])

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
        for (const key of ['upper', 'middle', 'lower'] as const) {
          const s = chart.addSeries(LineSeries, {
            color: colors[key], lineWidth: 1,
            title: `BB ${key.charAt(0).toUpperCase() + key.slice(1)}`,
            priceScaleId: 'right',
          })
          seriesMap.set(`${inst.id}:${key}`, s)
        }
      } else {
        const paramStr = Object.values(inst.params).join(',')
        const color = inst.color ?? '#f0883e'
        const s = chart.addSeries(LineSeries, {
          color, lineWidth: 1,
          title: `${inst.type.toUpperCase()}(${paramStr})`,
          priceScaleId: 'right',
        })
        seriesMap.set(inst.id, s)
      }
    }

    mainOverlaySeriesRef.current = seriesMap

    return () => {
      mainOverlaySeriesRef.current = null
      for (const s of seriesMap.values()) { try { chart.removeSeries(s) } catch {} }
    }
  }, [mainInstancesKey])

  useEffect(() => {
    const seriesMap = mainOverlaySeriesRef.current
    if (!seriesMap) return

    for (const inst of mainInstances) {
      const data = instanceData[inst.id]
      if (!data) continue

      if (inst.type === 'volume') {
        const vol = seriesMap.get(inst.id)
        if (vol) {
          vol.setData((data.volume ?? []).map(d => ({
            time: toET(d.time as any) as any,
            value: d.value,
            color: '#26a64166',
          })))
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
  }, [instanceData, mainInstancesKey])

  // EMA rising/falling overlays (per-rule visualization during/after backtest)
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !emaOverlays || emaOverlays.length === 0) return
    const created: ISeriesApi<any>[] = []
    for (const overlay of emaOverlays) {
      const activeColor = overlay.side === 'buy' ? '#26a641' : '#f85149'
      const inactiveColor = '#484f58'
      const label = `${overlay.indicator.toUpperCase()} ${overlay.condition === 'rising_over' ? '↑' : '↓'}${overlay.lookback}`

      // Build segments: contiguous runs of active/inactive points
      // Each segment becomes its own line series so colors don't bleed
      type Segment = { active: boolean; pts: Array<{ time: any; value: number }> }
      const segments: Segment[] = []
      let current: Segment | null = null

      for (let i = 0; i < overlay.series.length; i++) {
        const pt = overlay.series[i]
        if (pt.value === null) {
          current = null
          continue
        }
        const isActive = overlay.active[i]
        if (!current || current.active !== isActive) {
          const newSeg: Segment = { active: isActive, pts: [] }
          if (current && current.pts.length > 0) {
            newSeg.pts.push({ ...current.pts[current.pts.length - 1] })
          }
          segments.push(newSeg)
          current = newSeg
        }
        current.pts.push({ time: toET(pt.time as any) as any, value: pt.value })
      }

      let labeled = false
      for (const seg of segments) {
        if (seg.pts.length < 2) continue
        const color = seg.active ? activeColor : inactiveColor
        const title = !labeled ? label : ''
        const s = chart.addSeries(LineSeries, {
          color,
          lineWidth: seg.active ? 2 : 1,
          title,
          priceScaleId: 'right',
          lastValueVisible: false,
          priceLineVisible: false,
        })
        s.setData(seg.pts)
        created.push(s)
        if (title) labeled = true
      }
    }
    return () => { for (const s of created) { try { chart.removeSeries(s) } catch {} } }
  }, [emaOverlays])

  // Trade markers — update via plugin ref so trades arriving post-backtest
  // don't force a series rebuild. candleData is in deps so the effect re-runs
  // after series.setData() — otherwise a plugin attached before the series has
  // its data (or attached to a pre-StrictMode-remount series) paints nothing.
  const mainMarkersPluginRef = useRef<ReturnType<typeof createSeriesMarkers> | null>(null)
  useEffect(() => {
    const series = candleSeriesRef.current
    if (!series) return
    const markers = mainMarkers ?? []
    if (!mainMarkersPluginRef.current) {
      mainMarkersPluginRef.current = createSeriesMarkers(series, markers)
    } else {
      mainMarkersPluginRef.current.setMarkers(markers)
    }
  }, [mainMarkers, candleData])

  const subPaneCount = subPaneGroups.length
  const mainFlex = subPaneCount === 0 ? 1 : subPaneCount === 1 ? 2 : subPaneCount === 2 ? 2 : 1.5

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%', overflow: 'hidden' }}>
      <div ref={containerRef} style={{ flex: mainFlex, minHeight: 200, width: '100%' }} />
      {subPaneGroups.map((group, idx) => (
        <div key={group.key} style={{ flex: 1, minHeight: 120, maxHeight: subPaneCount <= 2 ? '35%' : undefined }}>
          <SubPane
            paneKey={group.key}
            instances={group.instances}
            instanceData={instanceData}
            mainChartRef={chartRef}
            mainSeriesRef={candleSeriesRef}
            paneRegistryRef={paneRegistryRef}
            syncWidthsRef={syncWidthsRef}
            markers={idx === 0 ? (subPaneMarkers ?? undefined) : undefined}
            toET={toET}
            label={group.label}
          />
        </div>
      ))}
    </div>
  )
}

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
import type { OHLCVBar, IndicatorData, IndicatorKey, TimeValue, EMAOverlay, Trade } from '../../shared/types'

interface ChartProps {
  data: OHLCVBar[]
  spyData?: OHLCVBar[]
  qqqData?: OHLCVBar[]
  showSpy: boolean
  showQqq: boolean
  indicatorData: IndicatorData
  activeIndicators: IndicatorKey[]
  trades?: Trade[]
  emaOverlays?: EMAOverlay[]
  onChartReady?: (chart: IChartApi | null) => void
  maShowRaw8?: boolean
  maShowRaw21?: boolean
  maShowSg8?: boolean
  maShowSg21?: boolean
  maCompensateLag?: boolean
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

function toLineData(arr: TimeValue[]) {
  // Use whitespace data (no value field) for nulls so the bar still occupies
  // space in the time scale — keeps logical range aligned across charts
  return arr.map(d => d.value !== null
    ? { time: toET(d.time as any) as any, value: d.value as number }
    : { time: toET(d.time as any) as any }
  )
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

export default function Chart({ data, spyData, qqqData, showSpy, showQqq, indicatorData, activeIndicators, trades, emaOverlays, onChartReady, maShowRaw8 = true, maShowRaw21 = true, maShowSg8 = true, maShowSg21 = true, maCompensateLag = false }: ChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const macdChartRef = useRef<IChartApi | null>(null)
  const rsiChartRef = useRef<IChartApi | null>(null)
  const macdContainerRef = useRef<HTMLDivElement>(null)
  const rsiContainerRef = useRef<HTMLDivElement>(null)
  // Series refs for crosshair sync
  const candleSeriesRef = useRef<ISeriesApi<any> | null>(null)
  const macdSeriesRef = useRef<ISeriesApi<any> | null>(null)
  const rsiSeriesRef = useRef<ISeriesApi<any> | null>(null)
  // Shared helpers the main mount effect installs; other effects trigger
  // width realignment and range-restore without duplicating the logic.
  const syncWidthsRef = useRef<() => void>(() => {})
  const rangeRestoredRef = useRef(false)
  // Hold the latest onChartReady so the mount-once effect doesn't re-fire
  // when the parent re-renders with a new callback identity.
  const onChartReadyRef = useRef(onChartReady)
  useEffect(() => { onChartReadyRef.current = onChartReady })

  const showMacd = activeIndicators.includes('macd')
  const showRsi = activeIndicators.includes('rsi')
  const showVolume = activeIndicators.includes('volume')
  const showEma = activeIndicators.includes('ema')
  const showBb = activeIndicators.includes('bb')
  const showMa = activeIndicators.includes('ma')

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

  const volumeData = useMemo(
    () => data.map(d => ({
      time: toET(d.time as any) as any,
      value: d.volume,
      color: d.close >= d.open ? '#26a64166' : '#f8514966',
    })),
    [data],
  )

  const macdHistData = useMemo(() => {
    if (!indicatorData.macd) return []
    return indicatorData.macd.histogram.map(d => d.value !== null
      ? { time: toET(d.time as any) as any, value: d.value as number, color: d.value >= 0 ? UP : DOWN }
      : { time: toET(d.time as any) as any }
    )
  }, [indicatorData.macd])

  const macdLineData = useMemo(
    () => indicatorData.macd ? toLineData(indicatorData.macd.macd) : [],
    [indicatorData.macd],
  )

  const macdSignalData = useMemo(
    () => indicatorData.macd ? toLineData(indicatorData.macd.signal) : [],
    [indicatorData.macd],
  )

  const rsiData = useMemo(
    () => indicatorData.rsi ? toLineData(indicatorData.rsi) : [],
    [indicatorData.rsi],
  )

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
      // Read the main chart via ref (not closure) so this bails cleanly if it's
      // been removed — MACD/RSI cleanups call syncWidthsRef.current() and can
      // race the main chart's teardown (paneWidgets cleared → throws).
      const mainChart = chartRef.current
      if (!mainChart) return
      try {
        const mainRightW = mainChart.priceScale('right').width()
        const macdRightW = macdChartRef.current?.priceScale('right').width() ?? 0
        const rsiRightW = rsiChartRef.current?.priceScale('right').width() ?? 0
        const maxRightW = Math.max(mainRightW, macdRightW, rsiRightW)
        if (maxRightW > 0) {
          mainChart.applyOptions({ rightPriceScale: { minimumWidth: maxRightW } })
          macdChartRef.current?.applyOptions({ rightPriceScale: { minimumWidth: maxRightW } })
          rsiChartRef.current?.applyOptions({ rightPriceScale: { minimumWidth: maxRightW } })
        }
        const mainLeftW = mainChart.priceScale('left').width()
        if (mainLeftW > 0) {
          macdChartRef.current?.applyOptions({ leftPriceScale: { minimumWidth: mainLeftW, visible: false } })
          rsiChartRef.current?.applyOptions({ leftPriceScale: { minimumWidth: mainLeftW, visible: false } })
        }
      } catch { /* any sibling mid-teardown — skip this realignment */ }
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
      // Guard: refs may point to a chart that's been .remove()'d but not yet
      // nulled (e.g. a pan event firing during sibling teardown on unmount).
      // Calling setVisibleLogicalRange on a removed instance throws from
      // inside lightweight-charts (paneWidgets[0] undefined) and blanks React.
      try { macdChartRef.current?.timeScale().setVisibleLogicalRange(range) } catch {}
      try { rsiChartRef.current?.timeScale().setVisibleLogicalRange(range) } catch {}
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

    // Crosshair sync: main → MACD + RSI.
    // Same stale-ref hazard as syncHandler — wrap in try/catch.
    const crosshairHandler = (param: any) => {
      try {
        if (!param.time) {
          macdChartRef.current?.clearCrosshairPosition()
          rsiChartRef.current?.clearCrosshairPosition()
          return
        }
        if (macdChartRef.current && macdSeriesRef.current)
          macdChartRef.current.setCrosshairPosition(NaN, param.time, macdSeriesRef.current)
        if (rsiChartRef.current && rsiSeriesRef.current)
          rsiChartRef.current.setCrosshairPosition(NaN, param.time, rsiSeriesRef.current)
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
    return () => { chart.removeSeries(spy) }
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
    return () => { chart.removeSeries(qqq) }
  }, [showQqq, qqqLineData])

  // Volume overlay
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !showVolume || volumeData.length === 0) return
    const vol = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    })
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.75, bottom: 0 }, visible: false })
    vol.setData(volumeData)
    return () => { chart.removeSeries(vol) }
  }, [showVolume, volumeData])

  // EMA
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !showEma || !indicatorData.ema) return
    const { ema20, ema50, ema200 } = indicatorData.ema
    const s20 = chart.addSeries(LineSeries, { color: '#f0883e', lineWidth: 1, title: 'EMA20', priceScaleId: 'right' })
    s20.setData(toLineData(ema20))
    const s50 = chart.addSeries(LineSeries, { color: '#a371f7', lineWidth: 1, title: 'EMA50', priceScaleId: 'right' })
    s50.setData(toLineData(ema50))
    const s200 = chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'EMA200', priceScaleId: 'right' })
    s200.setData(toLineData(ema200))
    return () => {
      chart.removeSeries(s20)
      chart.removeSeries(s50)
      chart.removeSeries(s200)
    }
  }, [showEma, indicatorData.ema])

  // Bollinger Bands
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !showBb || !indicatorData.bb) return
    const { upper, middle, lower } = indicatorData.bb
    const su = chart.addSeries(LineSeries, { color: '#30363d', lineWidth: 1, title: 'BB Upper', priceScaleId: 'right' })
    su.setData(toLineData(upper))
    const sm = chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'BB Mid', priceScaleId: 'right' })
    sm.setData(toLineData(middle))
    const sl = chart.addSeries(LineSeries, { color: '#30363d', lineWidth: 1, title: 'BB Lower', priceScaleId: 'right' })
    sl.setData(toLineData(lower))
    return () => {
      chart.removeSeries(su)
      chart.removeSeries(sm)
      chart.removeSeries(sl)
    }
  }, [showBb, indicatorData.bb])

  // MA8 / MA21 + S-G smoothed versions
  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !showMa || !indicatorData.ma) return
    const { ma8, ma21, ma8_sg, ma21_sg, sg8_window, sg21_window } = indicatorData.ma
    const created: ISeriesApi<any>[] = []
    if (maShowRaw8) {
      const s = chart.addSeries(LineSeries, { color: '#e8ab6a', lineWidth: 1, title: 'MA8', priceScaleId: 'right' })
      s.setData(toLineData(ma8))
      created.push(s)
    }
    if (maShowRaw21) {
      const s = chart.addSeries(LineSeries, { color: '#56d4c4', lineWidth: 1, title: 'MA21', priceScaleId: 'right' })
      s.setData(toLineData(ma21))
      created.push(s)
    }

    // Compensate lag: shift S-G values backward by (window-1)/2 bars
    // to reconstruct the centered view. Display-only — backtest stays causal.
    let sg8Display = ma8_sg
    let sg21Display = ma21_sg
    if (maCompensateLag) {
      const shift8 = Math.floor((sg8_window - 1) / 2)
      const shift21 = Math.floor((sg21_window - 1) / 2)
      sg8Display = ma8_sg.map((d, i) => ({
        time: d.time,
        value: i + shift8 < ma8_sg.length ? ma8_sg[i + shift8].value : null,
      }))
      sg21Display = ma21_sg.map((d, i) => ({
        time: d.time,
        value: i + shift21 < ma21_sg.length ? ma21_sg[i + shift21].value : null,
      }))
    }

    if (maShowSg8) {
      const s = chart.addSeries(LineSeries, { color: '#ffffff', lineWidth: 2, title: 'MA8-SG', priceScaleId: 'right', lineStyle: 2 })
      s.setData(toLineData(sg8Display))
      created.push(s)
    }
    if (maShowSg21) {
      const s = chart.addSeries(LineSeries, { color: '#e8ab6a', lineWidth: 2, title: 'MA21-SG', priceScaleId: 'right', lineStyle: 2 })
      s.setData(toLineData(sg21Display))
      created.push(s)
    }

    return () => { for (const s of created) chart.removeSeries(s) }
  }, [showMa, indicatorData.ma, maShowRaw8, maShowRaw21, maShowSg8, maShowSg21, maCompensateLag])

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
    return () => { for (const s of created) chart.removeSeries(s) }
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

  // ─── MACD chart ─────────────────────────────────────────────────────────
  // Kept toggle-driven (create when enabled, destroy when disabled) — same
  // tradeoff as before. Markers moved to their own effect so trades arrival
  // doesn't tear down the MACD chart.
  useEffect(() => {
    if (!macdContainerRef.current || !showMacd || !indicatorData.macd) return

    const chart = createChart(macdContainerRef.current, { ...chartOptions, height: macdContainerRef.current.clientHeight })
    macdChartRef.current = chart

    const histSeries = chart.addSeries(HistogramSeries, {
      color: UP,
      priceFormat: { type: 'price', precision: 4 },
    })
    histSeries.setData(macdHistData)
    macdSeriesRef.current = histSeries

    chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'MACD' }).setData(macdLineData)
    chart.addSeries(LineSeries, { color: '#f0883e', lineWidth: 1, title: 'Signal' }).setData(macdSignalData)

    chart.timeScale().fitContent()

    // Sync to main chart's current visible range
    if (chartRef.current) {
      const mainRange = chartRef.current.timeScale().getVisibleLogicalRange()
      if (mainRange) chart.timeScale().setVisibleLogicalRange(mainRange)
    }

    // Re-align widths now that MACD is in the layout
    syncWidthsRef.current()

    const crosshairHandler = (param: any) => {
      try {
        if (!param.time) {
          chartRef.current?.clearCrosshairPosition()
          rsiChartRef.current?.clearCrosshairPosition()
          return
        }
        if (chartRef.current && candleSeriesRef.current)
          chartRef.current.setCrosshairPosition(NaN, param.time, candleSeriesRef.current)
        if (rsiChartRef.current && rsiSeriesRef.current)
          rsiChartRef.current.setCrosshairPosition(NaN, param.time, rsiSeriesRef.current)
      } catch {}
    }
    chart.subscribeCrosshairMove(crosshairHandler)

    const ro = new ResizeObserver(() => {
      if (macdContainerRef.current) chart.applyOptions({ width: macdContainerRef.current.clientWidth, height: macdContainerRef.current.clientHeight })
    })
    ro.observe(macdContainerRef.current)
    return () => {
      // Null the refs BEFORE remove() so any in-flight main-chart sync event
      // skips this pane via the null guard instead of hitting a destroyed one.
      macdChartRef.current = null
      macdSeriesRef.current = null
      macdMarkersPluginRef.current = null
      chart.unsubscribeCrosshairMove(crosshairHandler)
      chart.remove()
      ro.disconnect()
      syncWidthsRef.current()
    }
  }, [showMacd, indicatorData.macd, macdHistData, macdLineData, macdSignalData])

  // MACD markers
  const macdMarkersPluginRef = useRef<ReturnType<typeof createSeriesMarkers> | null>(null)
  useEffect(() => {
    const series = macdSeriesRef.current
    if (!series) return
    const markers = subPaneMarkers ?? []
    if (!macdMarkersPluginRef.current) {
      macdMarkersPluginRef.current = createSeriesMarkers(series, markers)
    } else {
      macdMarkersPluginRef.current.setMarkers(markers)
    }
  }, [subPaneMarkers, showMacd, indicatorData.macd])

  // ─── RSI chart ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!rsiContainerRef.current || !showRsi || !indicatorData.rsi) return

    const chart = createChart(rsiContainerRef.current, { ...chartOptions, height: rsiContainerRef.current.clientHeight })
    rsiChartRef.current = chart

    const rsiLine = chart.addSeries(LineSeries, { color: '#a371f7', lineWidth: 1, title: 'RSI' })
    rsiLine.setData(rsiData)
    rsiSeriesRef.current = rsiLine

    const len = indicatorData.rsi.length
    if (len > 0) {
      const first = indicatorData.rsi[0].time
      const last = indicatorData.rsi[len - 1].time
      chart.addSeries(LineSeries, { color: '#f85149', lineWidth: 1, lineStyle: 2 }).setData([{ time: toET(first as any) as any, value: 70 }, { time: toET(last as any) as any, value: 70 }])
      chart.addSeries(LineSeries, { color: '#26a641', lineWidth: 1, lineStyle: 2 }).setData([{ time: toET(first as any) as any, value: 30 }, { time: toET(last as any) as any, value: 30 }])
    }

    chart.timeScale().fitContent()

    // Sync to main chart's current visible range
    if (chartRef.current) {
      const mainRange = chartRef.current.timeScale().getVisibleLogicalRange()
      if (mainRange) chart.timeScale().setVisibleLogicalRange(mainRange)
    }

    syncWidthsRef.current()

    const crosshairHandler = (param: any) => {
      try {
        if (!param.time) {
          chartRef.current?.clearCrosshairPosition()
          macdChartRef.current?.clearCrosshairPosition()
          return
        }
        if (chartRef.current && candleSeriesRef.current)
          chartRef.current.setCrosshairPosition(NaN, param.time, candleSeriesRef.current)
        if (macdChartRef.current && macdSeriesRef.current)
          macdChartRef.current.setCrosshairPosition(NaN, param.time, macdSeriesRef.current)
      } catch {}
    }
    chart.subscribeCrosshairMove(crosshairHandler)

    const ro = new ResizeObserver(() => {
      if (rsiContainerRef.current) chart.applyOptions({ width: rsiContainerRef.current.clientWidth, height: rsiContainerRef.current.clientHeight })
    })
    ro.observe(rsiContainerRef.current)
    return () => {
      rsiChartRef.current = null
      rsiSeriesRef.current = null
      rsiMarkersPluginRef.current = null
      chart.unsubscribeCrosshairMove(crosshairHandler)
      chart.remove()
      ro.disconnect()
      syncWidthsRef.current()
    }
  }, [showRsi, indicatorData.rsi, rsiData])

  // RSI markers
  const rsiMarkersPluginRef = useRef<ReturnType<typeof createSeriesMarkers> | null>(null)
  useEffect(() => {
    const series = rsiSeriesRef.current
    if (!series) return
    const markers = subPaneMarkers ?? []
    if (!rsiMarkersPluginRef.current) {
      rsiMarkersPluginRef.current = createSeriesMarkers(series, markers)
    } else {
      rsiMarkersPluginRef.current.setMarkers(markers)
    }
  }, [subPaneMarkers, showRsi, indicatorData.rsi])

  const indicatorPaneCount = (showMacd ? 1 : 0) + (showRsi ? 1 : 0)
  const mainHeightPct = indicatorPaneCount === 0 ? 100 : indicatorPaneCount === 1 ? 65 : 50
  const subHeightPct = indicatorPaneCount === 0 ? 0 : indicatorPaneCount === 1 ? 35 : 25

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%' }}>
      <div ref={containerRef} style={{ height: `${mainHeightPct}%`, width: '100%' }} />
      {showMacd && (
        <div style={{ height: `${subHeightPct}%`, borderTop: '1px solid #1c2128', position: 'relative' }}>
          <span style={{ position: 'absolute', top: 4, left: 8, fontSize: 10, color: '#8b949e', zIndex: 1 }}>MACD</span>
          <div ref={macdContainerRef} style={{ height: '100%', width: '100%' }} />
        </div>
      )}
      {showRsi && (
        <div style={{ height: `${subHeightPct}%`, borderTop: '1px solid #1c2128', position: 'relative' }}>
          <span style={{ position: 'absolute', top: 4, left: 8, fontSize: 10, color: '#8b949e', zIndex: 1 }}>RSI</span>
          <div ref={rsiContainerRef} style={{ height: '100%', width: '100%' }} />
        </div>
      )}
    </div>
  )
}

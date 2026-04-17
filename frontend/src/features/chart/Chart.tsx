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

  const showMacd = activeIndicators.includes('macd')
  const showRsi = activeIndicators.includes('rsi')

  // SPY/QQQ as real close prices on their own left axis
  const spyLineData = useMemo(() => {
    if (!spyData || spyData.length === 0) return []
    return spyData.map(d => ({ time: toET(d.time as any) as any, value: d.close }))
  }, [spyData])

  const qqqLineData = useMemo(() => {
    if (!qqqData || qqqData.length === 0) return []
    return qqqData.map(d => ({ time: toET(d.time as any) as any, value: d.close }))
  }, [qqqData])

  const chartOptions = {
    layout: { background: { type: ColorType.Solid, color: CHART_BG }, textColor: TEXT },
    grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
    crosshair: { mode: 1 },
    timeScale: { borderColor: GRID, timeVisible: true },
    rightPriceScale: { borderColor: GRID },
    leftPriceScale: { visible: false, borderColor: GRID },
  }

  // Main chart
  useEffect(() => {
    if (!containerRef.current || data.length === 0) return

    const chart = createChart(containerRef.current, { ...chartOptions, height: containerRef.current.clientHeight })
    chartRef.current = chart
    onChartReady?.(chart)

    // Candlesticks always on right axis
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN,
      wickUpColor: UP, wickDownColor: DOWN,
      priceScaleId: 'right',
    })
    candleSeries.setData(data.map(d => ({ ...d, time: toET(d.time as any) as any })))
    candleSeriesRef.current = candleSeries

    // SPY/QQQ as real close prices — each on its own left-side scale so
    // different price ranges (e.g. SPY ~$500 vs QQQ ~$420) don't squash together
    if (showSpy && spyLineData.length > 0) {
      const spySeries = chart.addSeries(LineSeries, {
        color: '#f0883e', lineWidth: 1, title: 'SPY',
        priceScaleId: 'spy-scale',
        priceFormat: { type: 'price', precision: 2 },
      })
      spySeries.setData(spyLineData)
      chart.priceScale('spy-scale').applyOptions({ visible: false })
    }
    if (showQqq && qqqLineData.length > 0) {
      const qqqSeries = chart.addSeries(LineSeries, {
        color: '#a371f7', lineWidth: 1, title: 'QQQ',
        priceScaleId: 'qqq-scale',
        priceFormat: { type: 'price', precision: 2 },
      })
      qqqSeries.setData(qqqLineData)
      chart.priceScale('qqq-scale').applyOptions({ visible: false })
    }

    // Volume overlay — semi-transparent bars at bottom 25% of chart
    if (activeIndicators.includes('volume')) {
      const volSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
      })
      volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.75, bottom: 0 }, visible: false })
      volSeries.setData(data.map(d => ({
        time: toET(d.time as any) as any,
        value: d.volume,
        color: d.close >= d.open ? '#26a64166' : '#f8514966',
      })))
    }

    // EMA — explicit priceScaleId: 'right' so they overlay on the candlesticks
    if (activeIndicators.includes('ema') && indicatorData.ema) {
      const { ema20, ema50, ema200 } = indicatorData.ema
      chart.addSeries(LineSeries, { color: '#f0883e', lineWidth: 1, title: 'EMA20', priceScaleId: 'right' }).setData(toLineData(ema20))
      chart.addSeries(LineSeries, { color: '#a371f7', lineWidth: 1, title: 'EMA50', priceScaleId: 'right' }).setData(toLineData(ema50))
      chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'EMA200', priceScaleId: 'right' }).setData(toLineData(ema200))
    }

    // Bollinger Bands — explicit priceScaleId: 'right'
    if (activeIndicators.includes('bb') && indicatorData.bb) {
      const { upper, middle, lower } = indicatorData.bb
      chart.addSeries(LineSeries, { color: '#30363d', lineWidth: 1, title: 'BB Upper', priceScaleId: 'right' }).setData(toLineData(upper))
      chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'BB Mid', priceScaleId: 'right' }).setData(toLineData(middle))
      chart.addSeries(LineSeries, { color: '#30363d', lineWidth: 1, title: 'BB Lower', priceScaleId: 'right' }).setData(toLineData(lower))
    }

    // MA8 / MA21 / S-G smoothed versions
    if (activeIndicators.includes('ma') && indicatorData.ma) {
      const { ma8, ma21, ma8_sg, ma21_sg, sg8_window, sg21_window } = indicatorData.ma
      if (maShowRaw8) chart.addSeries(LineSeries, { color: '#e8ab6a', lineWidth: 1, title: 'MA8', priceScaleId: 'right' }).setData(toLineData(ma8))
      if (maShowRaw21) chart.addSeries(LineSeries, { color: '#56d4c4', lineWidth: 1, title: 'MA21', priceScaleId: 'right' }).setData(toLineData(ma21))

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

      if (maShowSg8) chart.addSeries(LineSeries, { color: '#ffffff', lineWidth: 2, title: 'MA8-SG', priceScaleId: 'right', lineStyle: 2 }).setData(toLineData(sg8Display))
      if (maShowSg21) chart.addSeries(LineSeries, { color: '#e8ab6a', lineWidth: 2, title: 'MA21-SG', priceScaleId: 'right', lineStyle: 2 }).setData(toLineData(sg21Display))
    }

    // Trade markers
    if (trades && trades.length > 0) createSeriesMarkers(candleSeries, buildMarkers(trades))

    // EMA overlays from backtest (rising_over / falling_over conditions)
    // Draw one line with per-point color via separate segments
    if (emaOverlays && emaOverlays.length > 0) {
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
            // Start new segment — duplicate the last point of previous segment
            // as the first point of new segment so they connect
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
          chart.addSeries(LineSeries, {
            color,
            lineWidth: seg.active ? 2 : 1,
            title,
            priceScaleId: 'right',
            lastValueVisible: false,
            priceLineVisible: false,
          }).setData(seg.pts)
          if (title) labeled = true
        }
      }
    }

    // Restore saved scroll/zoom position, or fit all content on first visit
    const savedRange = sessionStorage.getItem('strategylab-chart-range')
    if (savedRange) {
      try {
        chart.timeScale().setVisibleLogicalRange(JSON.parse(savedRange))
      } catch { chart.timeScale().fitContent() }
    } else {
      chart.timeScale().fitContent()
    }

    function syncWidths() {
      const mainRightW = chart.priceScale('right').width()
      const macdRightW = macdChartRef.current?.priceScale('right').width() ?? 0
      const rsiRightW = rsiChartRef.current?.priceScale('right').width() ?? 0
      const maxRightW = Math.max(mainRightW, macdRightW, rsiRightW)
      if (maxRightW > 0) {
        chart.applyOptions({ rightPriceScale: { minimumWidth: maxRightW } })
        macdChartRef.current?.applyOptions({ rightPriceScale: { minimumWidth: maxRightW } })
        rsiChartRef.current?.applyOptions({ rightPriceScale: { minimumWidth: maxRightW } })
      }
      // Mirror the main chart's left axis width onto MACD/RSI (invisible) so
      // all three chart areas start at the same x position
      const mainLeftW = chart.priceScale('left').width()
      if (mainLeftW > 0) {
        macdChartRef.current?.applyOptions({ leftPriceScale: { minimumWidth: mainLeftW, visible: false } })
        rsiChartRef.current?.applyOptions({ leftPriceScale: { minimumWidth: mainLeftW, visible: false } })
      }
    }

    // Pan/zoom sync + price scale width equalization
    // Use logical range (bar-index) so scrolling stays locked across all panes
    const syncHandler = (range: any) => {
      if (!range) return
      syncWidths()
      sessionStorage.setItem('strategylab-chart-range', JSON.stringify(range))
      if (macdChartRef.current) macdChartRef.current.timeScale().setVisibleLogicalRange(range)
      if (rsiChartRef.current) rsiChartRef.current.timeScale().setVisibleLogicalRange(range)
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(syncHandler)

    // Initial alignment: fire after MACD/RSI effects have had time to mount
    const alignTimer = setTimeout(syncWidths, 100)

    // Crosshair sync: main → MACD + RSI
    const crosshairHandler = (param: any) => {
      if (!param.time) {
        macdChartRef.current?.clearCrosshairPosition()
        rsiChartRef.current?.clearCrosshairPosition()
        return
      }
      if (macdChartRef.current && macdSeriesRef.current)
        macdChartRef.current.setCrosshairPosition(NaN, param.time, macdSeriesRef.current)
      if (rsiChartRef.current && rsiSeriesRef.current)
        rsiChartRef.current.setCrosshairPosition(NaN, param.time, rsiSeriesRef.current)
    }
    chart.subscribeCrosshairMove(crosshairHandler)

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth, height: containerRef.current.clientHeight })
    })
    ro.observe(containerRef.current)

    return () => {
      clearTimeout(alignTimer)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(syncHandler)
      chart.unsubscribeCrosshairMove(crosshairHandler)
      onChartReady?.(null)
      chart.remove()
      candleSeriesRef.current = null
      ro.disconnect()
    }
  }, [data, spyLineData, qqqLineData, showSpy, showQqq, activeIndicators, indicatorData, trades, emaOverlays, maShowRaw8, maShowRaw21, maShowSg8, maShowSg21, maCompensateLag])

  // MACD chart
  useEffect(() => {
    if (!macdContainerRef.current || !showMacd || !indicatorData.macd) return

    const chart = createChart(macdContainerRef.current, { ...chartOptions, height: macdContainerRef.current.clientHeight })
    macdChartRef.current = chart

    const { macd, signal, histogram } = indicatorData.macd
    const histSeries = chart.addSeries(HistogramSeries, {
      color: UP,
      priceFormat: { type: 'price', precision: 4 },
    })
    histSeries.setData(histogram.map(d => d.value !== null
      ? { time: toET(d.time as any) as any, value: d.value as number, color: d.value >= 0 ? UP : DOWN }
      : { time: toET(d.time as any) as any }
    ))
    macdSeriesRef.current = histSeries

    chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'MACD' }).setData(toLineData(macd))
    chart.addSeries(LineSeries, { color: '#f0883e', lineWidth: 1, title: 'Signal' }).setData(toLineData(signal))

    if (trades && trades.length > 0) createSeriesMarkers(histSeries, buildMarkers(trades, false, true))

    chart.timeScale().fitContent()

    // Sync to main chart's current visible range
    if (chartRef.current) {
      const mainRange = chartRef.current.timeScale().getVisibleLogicalRange()
      if (mainRange) chart.timeScale().setVisibleLogicalRange(mainRange)
    }

    // Crosshair sync: MACD → main + RSI
    const crosshairHandler = (param: any) => {
      if (!param.time) {
        chartRef.current?.clearCrosshairPosition()
        rsiChartRef.current?.clearCrosshairPosition()
        return
      }
      if (chartRef.current && candleSeriesRef.current)
        chartRef.current.setCrosshairPosition(NaN, param.time, candleSeriesRef.current)
      if (rsiChartRef.current && rsiSeriesRef.current)
        rsiChartRef.current.setCrosshairPosition(NaN, param.time, rsiSeriesRef.current)
    }
    chart.subscribeCrosshairMove(crosshairHandler)

    const ro = new ResizeObserver(() => {
      if (macdContainerRef.current) chart.applyOptions({ width: macdContainerRef.current.clientWidth, height: macdContainerRef.current.clientHeight })
    })
    ro.observe(macdContainerRef.current)
    return () => {
      chart.unsubscribeCrosshairMove(crosshairHandler)
      chart.remove()
      macdChartRef.current = null
      macdSeriesRef.current = null
      ro.disconnect()
    }
  }, [showMacd, indicatorData.macd, trades])

  // RSI chart
  useEffect(() => {
    if (!rsiContainerRef.current || !showRsi || !indicatorData.rsi) return

    const chart = createChart(rsiContainerRef.current, { ...chartOptions, height: rsiContainerRef.current.clientHeight })
    rsiChartRef.current = chart

    const rsiLine = chart.addSeries(LineSeries, { color: '#a371f7', lineWidth: 1, title: 'RSI' })
    rsiLine.setData(toLineData(indicatorData.rsi))
    rsiSeriesRef.current = rsiLine

    const len = indicatorData.rsi.length
    if (len > 0) {
      const first = indicatorData.rsi[0].time
      const last = indicatorData.rsi[len - 1].time
      chart.addSeries(LineSeries, { color: '#f85149', lineWidth: 1, lineStyle: 2 }).setData([{ time: toET(first as any) as any, value: 70 }, { time: toET(last as any) as any, value: 70 }])
      chart.addSeries(LineSeries, { color: '#26a641', lineWidth: 1, lineStyle: 2 }).setData([{ time: toET(first as any) as any, value: 30 }, { time: toET(last as any) as any, value: 30 }])
    }

    if (trades && trades.length > 0) createSeriesMarkers(rsiLine, buildMarkers(trades, false, true))

    chart.timeScale().fitContent()

    // Sync to main chart's current visible range
    if (chartRef.current) {
      const mainRange = chartRef.current.timeScale().getVisibleLogicalRange()
      if (mainRange) chart.timeScale().setVisibleLogicalRange(mainRange)
    }

    // Crosshair sync: RSI → main + MACD
    const crosshairHandler = (param: any) => {
      if (!param.time) {
        chartRef.current?.clearCrosshairPosition()
        macdChartRef.current?.clearCrosshairPosition()
        return
      }
      if (chartRef.current && candleSeriesRef.current)
        chartRef.current.setCrosshairPosition(NaN, param.time, candleSeriesRef.current)
      if (macdChartRef.current && macdSeriesRef.current)
        macdChartRef.current.setCrosshairPosition(NaN, param.time, macdSeriesRef.current)
    }
    chart.subscribeCrosshairMove(crosshairHandler)

    const ro = new ResizeObserver(() => {
      if (rsiContainerRef.current) chart.applyOptions({ width: rsiContainerRef.current.clientWidth, height: rsiContainerRef.current.clientHeight })
    })
    ro.observe(rsiContainerRef.current)
    return () => {
      chart.unsubscribeCrosshairMove(crosshairHandler)
      chart.remove()
      rsiChartRef.current = null
      rsiSeriesRef.current = null
      ro.disconnect()
    }
  }, [showRsi, indicatorData.rsi, trades])

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

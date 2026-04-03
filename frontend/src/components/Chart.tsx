import { useEffect, useRef, useMemo } from 'react'
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  ColorType,
} from 'lightweight-charts'
import type { IChartApi, ISeriesApi, LogicalRange } from 'lightweight-charts'
import type { OHLCVBar, IndicatorData, IndicatorKey, TimeValue } from '../types'

interface ChartProps {
  data: OHLCVBar[]
  spyData?: OHLCVBar[]
  qqqData?: OHLCVBar[]
  showSpy: boolean
  showQqq: boolean
  indicatorData: IndicatorData
  activeIndicators: IndicatorKey[]
  trades?: Array<{ type: 'buy' | 'sell'; date: string; price: number }>
}

const CHART_BG = '#0d1117'
const GRID = '#1c2128'
const TEXT = '#8b949e'
const UP = '#26a641'
const DOWN = '#f85149'

function toLineData(arr: TimeValue[]) {
  return arr.filter(d => d.value !== null).map(d => ({ time: d.time as any, value: d.value as number }))
}

function buildMarkers(trades: Array<{ type: 'buy' | 'sell'; date: string; price: number }>) {
  return trades.map(t => ({
    time: t.date as any,
    position: t.type === 'buy' ? 'belowBar' as const : 'aboveBar' as const,
    color: t.type === 'buy' ? UP : DOWN,
    shape: t.type === 'buy' ? 'arrowUp' as const : 'arrowDown' as const,
    text: t.type === 'buy' ? `B $${t.price}` : `S $${t.price}`,
  }))
}

export default function Chart({ data, spyData, qqqData, showSpy, showQqq, indicatorData, activeIndicators, trades }: ChartProps) {
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

  // % change from start — used for SPY/QQQ comparison on left axis
  const normalizedSpy = useMemo(() => {
    if (!spyData || spyData.length === 0) return []
    const base = spyData[0].close
    return spyData.map(d => ({ time: d.time as any, value: ((d.close - base) / base) * 100 }))
  }, [spyData])

  const normalizedQqq = useMemo(() => {
    if (!qqqData || qqqData.length === 0) return []
    const base = qqqData[0].close
    return qqqData.map(d => ({ time: d.time as any, value: ((d.close - base) / base) * 100 }))
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

    // Candlesticks always on right axis
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN,
      wickUpColor: UP, wickDownColor: DOWN,
      priceScaleId: 'right',
    })
    candleSeries.setData(data.map(d => ({ ...d, time: d.time as any })))
    candleSeriesRef.current = candleSeries

    // SPY/QQQ as % change from start on visible left axis
    // Each starts at 0% so you can directly compare performance
    if (showSpy && normalizedSpy.length > 0) {
      chart.addSeries(LineSeries, {
        color: '#f0883e', lineWidth: 1, title: 'SPY %',
        priceScaleId: 'left',
        priceFormat: { type: 'price', precision: 1 },
      }).setData(normalizedSpy)
    }
    if (showQqq && normalizedQqq.length > 0) {
      chart.addSeries(LineSeries, {
        color: '#a371f7', lineWidth: 1, title: 'QQQ %',
        priceScaleId: 'left',
        priceFormat: { type: 'price', precision: 1 },
      }).setData(normalizedQqq)
    }
    if (showSpy || showQqq) {
      chart.applyOptions({ leftPriceScale: { visible: true, borderColor: GRID } })
    }

    // Volume overlay — semi-transparent bars at bottom 25% of chart
    if (activeIndicators.includes('volume')) {
      const volSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
      })
      volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.75, bottom: 0 }, visible: false })
      volSeries.setData(data.map(d => ({
        time: d.time as any,
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

    // Trade markers
    if (trades && trades.length > 0) createSeriesMarkers(candleSeries, buildMarkers(trades))

    chart.timeScale().fitContent()

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
    const syncHandler = (range: LogicalRange | null) => {
      if (!range) return
      syncWidths()
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
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth })
    })
    ro.observe(containerRef.current)

    return () => {
      clearTimeout(alignTimer)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(syncHandler)
      chart.unsubscribeCrosshairMove(crosshairHandler)
      chart.remove()
      candleSeriesRef.current = null
      ro.disconnect()
    }
  }, [data, normalizedSpy, normalizedQqq, showSpy, showQqq, activeIndicators, indicatorData, trades])

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
    histSeries.setData(histogram.filter(d => d.value !== null).map(d => ({
      time: d.time as any,
      value: d.value as number,
      color: (d.value ?? 0) >= 0 ? UP : DOWN,
    })))
    macdSeriesRef.current = histSeries

    chart.addSeries(LineSeries, { color: '#58a6ff', lineWidth: 1, title: 'MACD' }).setData(toLineData(macd))
    chart.addSeries(LineSeries, { color: '#f0883e', lineWidth: 1, title: 'Signal' }).setData(toLineData(signal))
    chart.timeScale().fitContent()

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
      if (macdContainerRef.current) chart.applyOptions({ width: macdContainerRef.current.clientWidth })
    })
    ro.observe(macdContainerRef.current)
    return () => {
      chart.unsubscribeCrosshairMove(crosshairHandler)
      chart.remove()
      macdChartRef.current = null
      macdSeriesRef.current = null
      ro.disconnect()
    }
  }, [showMacd, indicatorData.macd])

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
      chart.addSeries(LineSeries, { color: '#f85149', lineWidth: 1, lineStyle: 2 }).setData([{ time: first as any, value: 70 }, { time: last as any, value: 70 }])
      chart.addSeries(LineSeries, { color: '#26a641', lineWidth: 1, lineStyle: 2 }).setData([{ time: first as any, value: 30 }, { time: last as any, value: 30 }])
    }
    chart.timeScale().fitContent()

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
      if (rsiContainerRef.current) chart.applyOptions({ width: rsiContainerRef.current.clientWidth })
    })
    ro.observe(rsiContainerRef.current)
    return () => {
      chart.unsubscribeCrosshairMove(crosshairHandler)
      chart.remove()
      rsiChartRef.current = null
      rsiSeriesRef.current = null
      ro.disconnect()
    }
  }, [showRsi, indicatorData.rsi])

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
